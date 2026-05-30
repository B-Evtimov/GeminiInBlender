bl_info = {
    "name": "Gemini AI Assistant Pro",
    "author": "Boris Evtimov",
    "version": (2, 1),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Gemini AI",
    "description": "Autonomous AI Agent with Non-Blocking Chat Interface and Auto-Correction",
    "category": "Object",
}

import bpy
import urllib.request
import json
import traceback
import threading
import queue

# Глобална опашка за комуникация между worker нишката и главната нишка
_result_queue = queue.Queue()
# Пазим състоянието на текущата заявка, за да не пускаме няколко наведнъж
_request_active = False

# Колко завъртания (turns) от историята да изпращаме максимум
MAX_HISTORY_TURNS = 6
# Колко пъти да опитаме да поправим грешен код (1 = първоначален опит + 1 поправка)
MAX_RETRIES = 1


# --- PREFERENCES ---
class GeminiPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    api_key: bpy.props.StringProperty(name="API Key", subtype='PASSWORD')

    def draw(self, context):
        self.layout.prop(self, "api_key")


# --- DATA STRUCTURE ---
class GEMINI_HistoryItem(bpy.types.PropertyGroup):
    user_input: bpy.props.StringProperty()
    ai_response: bpy.props.StringProperty()
    status: bpy.props.StringProperty(default="OK")  # OK / FAIL
    info: bpy.props.StringProperty(default="")       # кратко съобщение за статуса


# --- LOGIC ---
def get_scene_context():
    return {
        "active_object": bpy.context.active_object.name if bpy.context.active_object else "None",
        "selected_objects": [obj.name for obj in bpy.context.selected_objects],
        "all_objects": [obj.name for obj in bpy.data.objects][:15],
        "render_engine": bpy.context.scene.render.engine,
    }


def call_gemini_api(api_key, scene_data, history_snapshot, custom_prompt, error_log=None):
    """
    ВАЖНО: тази функция върви на worker нишка.
    Тук НЯМА достъп до bpy.data / bpy.ops — затова получава scene_data и
    history_snapshot като готови (обикновени) Python структури, а не през context.
    """
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-1.5-flash:generateContent?key={api_key}"
    )

    system_instr = (
        "You are a Blender Python Expert. Output ONLY valid Python code. No markdown. "
        f"Context: {json.dumps(scene_data)}. "
    )
    if error_log:
        system_instr += f"\nFIX ERROR: {error_log}"

    messages = []
    for entry in history_snapshot:
        messages.append({"role": "user", "parts": [{"text": entry["user_input"]}]})
        messages.append({"role": "model", "parts": [{"text": entry["ai_response"]}]})

    messages.append({"role": "user", "parts": [{"text": system_instr + "\nTask: " + custom_prompt}]})

    data = {"contents": messages}
    body = json.dumps(data).encode('utf-8')
    headers = {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        return res_data['candidates'][0]['content']['parts'][0]['text']


def worker_thread(api_key, scene_data, history_snapshot, user_prompt):
    """
    Целият мрежов трафик става тук, на отделна нишка.
    Резултатът (само текст/грешка) се връща през опашката към главната нишка.
    bpy НЕ се пипа от тук.
    """
    try:
        raw_response = call_gemini_api(api_key, scene_data, history_snapshot, user_prompt)
        _result_queue.put({"ok": True, "code": raw_response, "prompt": user_prompt})
    except Exception as e:
        _result_queue.put({"ok": False, "error": str(e), "prompt": user_prompt})


def execute_ai_code(context, clean_code, user_prompt):
    """
    Изпълнява генерирания код на ГЛАВНАТА нишка (извикано от таймера).
    Връща (success: bool, error_log: str|None).
    """
    safe_globals = {
        "bpy": bpy,
        "context": context,
        "math": __import__("math"),
        "bmesh": __import__("bmesh"),
        "__builtins__": {
            "range": range, "len": len, "print": print, "list": list, "dict": dict,
        },
    }
    try:
        exec(clean_code, safe_globals)
        return True, None
    except Exception:
        return False, traceback.format_exc()


def add_history_item(context, user_prompt, code, status, info):
    item = context.scene.gemini_history.add()
    item.user_input = user_prompt
    item.ai_response = code
    item.status = status
    item.info = info


def poll_queue():
    """
    Таймер на главната нишка. Проверява опашката за резултати от worker-а.
    Тук е безопасно да пипаме bpy.
    Връща интервал (секунди) за следващо извикване, или None за спиране.
    """
    global _request_active

    try:
        result = _result_queue.get_nowait()
    except queue.Empty:
        return 0.2  # още няма резултат, пробвай пак след 0.2s

    context = bpy.context
    api_key = context.preferences.addons[__name__].preferences.api_key
    user_prompt = result["prompt"]

    if not result["ok"]:
        add_history_item(context, user_prompt, "", "FAIL", f"API Error: {result['error']}")
        _force_redraw()
        _request_active = False
        return None

    # Имаме код от AI — изпълняваме на главната нишка, с retry при грешка
    clean_code = result["code"].replace("```python", "").replace("```", "").strip()
    success, error_log = execute_ai_code(context, clean_code, user_prompt)

    if success:
        add_history_item(context, user_prompt, clean_code, "OK", "Task completed")
        context.scene.gemini_user_prompt = ""
        _force_redraw()
        _request_active = False
        return None

    # Грешка → пробваме retry, като пращаме traceback-а обратно (отново на нишка)
    retries_done = context.scene.gemini_retry_count
    if retries_done < MAX_RETRIES:
        context.scene.gemini_retry_count += 1
        scene_data = get_scene_context()
        history_snapshot = _snapshot_history(context)
        # Подаваме грешния код в prompt-а, за да го поправи
        fix_prompt = user_prompt + f"\n[Previous attempt failed, fix this code:]\n{clean_code}"
        t = threading.Thread(
            target=_worker_with_error,
            args=(api_key, scene_data, history_snapshot, fix_prompt, error_log),
            daemon=True,
        )
        t.start()
        return 0.2  # продължаваме да слушаме за новия резултат

    # Изчерпани опити
    add_history_item(context, user_prompt, clean_code, "FAIL", "AI could not fix the code")
    context.scene.gemini_retry_count = 0
    _force_redraw()
    _request_active = False
    return None


def _worker_with_error(api_key, scene_data, history_snapshot, user_prompt, error_log):
    try:
        raw_response = call_gemini_api(api_key, scene_data, history_snapshot, user_prompt, error_log)
        _result_queue.put({"ok": True, "code": raw_response, "prompt": user_prompt})
    except Exception as e:
        _result_queue.put({"ok": False, "error": str(e), "prompt": user_prompt})


def _snapshot_history(context):
    """Прави обикновено Python копие на историята (без bpy типове) за worker нишката."""
    snap = []
    items = list(context.scene.gemini_history)[-MAX_HISTORY_TURNS:]
    for entry in items:
        snap.append({"user_input": entry.user_input, "ai_response": entry.ai_response})
    return snap


def _force_redraw():
    """Опреснява всички VIEW_3D панели, за да се види новата история."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# --- OPERATORS ---
class OBJECT_OT_GeminiExecute(bpy.types.Operator):
    bl_idname = "object.gemini_execute"
    bl_label = "Send to AI"

    def execute(self, context):
        global _request_active

        if _request_active:
            self.report({'WARNING'}, "AI is already working, please wait...")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__name__].preferences
        if not prefs.api_key:
            self.report({'ERROR'}, "Set API Key in Preferences!")
            return {'CANCELLED'}

        user_prompt = context.scene.gemini_user_prompt
        if not user_prompt:
            return {'CANCELLED'}

        # Подготвяме данните на главната нишка (тук bpy е достъпен), после ги подаваме на worker-а
        scene_data = get_scene_context()
        history_snapshot = _snapshot_history(context)

        context.scene.gemini_retry_count = 0
        _request_active = True

        t = threading.Thread(
            target=worker_thread,
            args=(prefs.api_key, scene_data, history_snapshot, user_prompt),
            daemon=True,
        )
        t.start()

        # Стартираме таймера, който ще слуша за резултата
        if not bpy.app.timers.is_registered(poll_queue):
            bpy.app.timers.register(poll_queue)

        self.report({'INFO'}, "Request sent to AI (working in background)...")
        return {'FINISHED'}


class OBJECT_OT_GeminiClearHistory(bpy.types.Operator):
    bl_idname = "object.gemini_clear_history"
    bl_label = "Clear Chat"

    def execute(self, context):
        context.scene.gemini_history.clear()
        return {'FINISHED'}


# --- UI PANEL ---
class VIEW3D_PT_GeminiPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Gemini AI'
    bl_label = "Gemini AI Agent"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Input Area
        col = layout.column(align=True)
        col.label(text="Ask AI:")
        col.prop(scene, "gemini_user_prompt", text="")

        row = col.row()
        row.enabled = not _request_active
        row.operator("object.gemini_execute", text="Run", icon='PLAY')

        if _request_active:
            layout.label(text="AI is working...", icon='SORTTIME')

        # History Area (The Chat Window)
        if len(scene.gemini_history) > 0:
            layout.separator()
            layout.label(text="Chat History:")

            main_box = layout.box()
            for entry in scene.gemini_history:
                icon = 'CHECKMARK' if entry.status == "OK" else 'ERROR'
                col_h = main_box.column(align=True)
                col_h.label(text=f"Me: {entry.user_input}", icon='USER')
                col_h.label(text=entry.info, icon=icon)

            layout.operator("object.gemini_clear_history", icon='TRASH', text="Clear History")


# --- REGISTRATION ---
classes = (
    GEMINI_HistoryItem,
    GeminiPreferences,
    OBJECT_OT_GeminiExecute,
    OBJECT_OT_GeminiClearHistory,
    VIEW3D_PT_GeminiPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gemini_user_prompt = bpy.props.StringProperty(name="")
    bpy.types.Scene.gemini_history = bpy.props.CollectionProperty(type=GEMINI_HistoryItem)
    bpy.types.Scene.gemini_retry_count = bpy.props.IntProperty(default=0)


def unregister():
    # Спираме таймера, ако още работи
    if bpy.app.timers.is_registered(poll_queue):
        bpy.app.timers.unregister(poll_queue)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gemini_user_prompt
    del bpy.types.Scene.gemini_history
    del bpy.types.Scene.gemini_retry_count


if __name__ == "__main__":
    register()