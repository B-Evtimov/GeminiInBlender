bl_info = {
    "name": "Gemini AI Assistant Pro",
    "author": "Boris Evtimov",
    "version": (2, 2),
    "blender": (3, 2, 0),
    "location": "View3D > Sidebar > Gemini AI",
    "description": "AI agent that turns natural language into Blender Python, non-blocking, with auto-correction",
    "category": "Object",
}

import bpy
import urllib.request
import urllib.error
import json
import re
import traceback
import threading
import queue

_result_queue = queue.Queue()
_request_active = False
_retries_done = 0

MAX_HISTORY_TURNS = 6
MAX_RETRIES = 1
CODE_TEXT_NAME = "gemini_last_code.py"

SYSTEM_PROMPT = (
    "You are a Blender Python (bpy) expert. Reply with ONLY valid Python code, no markdown, "
    "no explanations. The code runs inside Blender via exec(); `bpy`, `bmesh`, `math`, "
    "`mathutils` and `context` are already available, imports are allowed. "
    "Prefer bpy.data API over bpy.ops where possible."
)


# --- PREFERENCES ---
class GeminiPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    api_key: bpy.props.StringProperty(name="API Key", subtype='PASSWORD')
    model: bpy.props.StringProperty(
        name="Model", default="gemini-3.5-flash",
        description="Gemini model id (see ai.google.dev/gemini-api/docs/models)",
    )

    def draw(self, context):
        self.layout.prop(self, "api_key")
        self.layout.prop(self, "model")


# --- DATA ---
class GEMINI_HistoryItem(bpy.types.PropertyGroup):
    user_input: bpy.props.StringProperty()
    ai_response: bpy.props.StringProperty()
    status: bpy.props.StringProperty(default="OK")
    info: bpy.props.StringProperty(default="")


# --- LOGIC (main thread) ---
def get_scene_context():
    ctx = bpy.context
    objs = []
    for o in list(bpy.data.objects)[:30]:
        objs.append({
            "name": o.name, "type": o.type,
            "location": [round(v, 3) for v in o.location],
        })
    return {
        "mode": ctx.mode,
        "active_object": ctx.active_object.name if ctx.active_object else None,
        "selected_objects": [o.name for o in ctx.selected_objects],
        "objects": objs,
        "object_count": len(bpy.data.objects),
        "frame": ctx.scene.frame_current,
        "render_engine": ctx.scene.render.engine,
        "blender_version": bpy.app.version_string,
    }


def _snapshot_history():
    items = list(bpy.context.scene.gemini_history)[-MAX_HISTORY_TURNS:]
    # само успешните ходове — провалените само объркват модела
    return [{"user_input": e.user_input, "ai_response": e.ai_response}
            for e in items if e.status == "OK" and e.ai_response]


# --- NETWORK (worker thread, NO bpy here) ---
def call_gemini_api(api_key, model, scene_data, history, prompt, error_log=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    contents = []
    for h in history:
        contents.append({"role": "user", "parts": [{"text": h["user_input"]}]})
        contents.append({"role": "model", "parts": [{"text": h["ai_response"]}]})

    task = f"Scene context: {json.dumps(scene_data)}\nTask: {prompt}"
    if error_log:
        task += f"\n\nYour previous code failed with this error, fix it:\n{error_log}"
    contents.append({"role": "user", "parts": [{"text": task}]})

    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,   # ключът в хедър, не в URL
    })

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None

    cands = data.get("candidates") or []
    if not cands:
        reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise RuntimeError(f"Empty response ({reason})")
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise RuntimeError(f"Empty response ({cands[0].get('finishReason', '?')})")
    return text


def worker(api_key, model, scene_data, history, orig_prompt, send_prompt, error_log=None):
    try:
        code = call_gemini_api(api_key, model, scene_data, history, send_prompt, error_log)
        _result_queue.put({"ok": True, "code": code, "prompt": orig_prompt})
    except Exception as e:
        _result_queue.put({"ok": False, "error": str(e), "prompt": orig_prompt})


def _start_worker(orig_prompt, send_prompt, error_log=None):
    prefs = bpy.context.preferences.addons[__name__].preferences
    threading.Thread(
        target=worker,
        args=(prefs.api_key, prefs.model, get_scene_context(), _snapshot_history(),
              orig_prompt, send_prompt, error_log),
        daemon=True,
    ).start()


# --- EXECUTION (main thread) ---
def clean_code(raw):
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", raw, re.S)
    return (m.group(1) if m else raw).strip()


def _view3d_override():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                return {"window": window, "area": area, "region": region}
    return {}


def execute_ai_code(code):
    # Бележка: това НЕ е sandbox — bpy сам по себе си дава пълен достъп.
    # Затова даваме нормални builtins (иначе `import bpy` в AI кода гърми).
    import math, bmesh, mathutils
    g = {"__name__": "__gemini__", "bpy": bpy, "bmesh": bmesh,
         "math": math, "mathutils": mathutils, "context": bpy.context}
    try:
        with bpy.context.temp_override(**_view3d_override()):
            exec(compile(code, "<gemini>", "exec"), g)
        return True, None
    except Exception:
        return False, traceback.format_exc(limit=3)


def _save_code_for_review(prompt, code):
    txt = bpy.data.texts.get(CODE_TEXT_NAME) or bpy.data.texts.new(CODE_TEXT_NAME)
    txt.from_string(f"# Prompt: {prompt}\n{code}\n")
    print(f"\n--- Gemini code for: {prompt} ---\n{code}\n--- end ---")


def add_history_item(prompt, code, status, info):
    item = bpy.context.scene.gemini_history.add()
    item.user_input, item.ai_response, item.status, item.info = prompt, code, status, info


def _force_redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _finish():
    global _request_active, _retries_done
    _request_active = False
    _retries_done = 0
    _force_redraw()
    return None


def poll_queue():
    global _retries_done
    try:
        result = _result_queue.get_nowait()
    except queue.Empty:
        return 0.2

    try:
        prompt = result["prompt"]
        if not result["ok"]:
            add_history_item(prompt, "", "FAIL", f"API: {result['error']}"[:200])
            return _finish()

        code = clean_code(result["code"])
        _save_code_for_review(prompt, code)
        ok, err = execute_ai_code(code)

        if ok:
            bpy.ops.ed.undo_push(message=f"Gemini: {prompt[:40]}")
            add_history_item(prompt, code, "OK", "Task completed")
            bpy.context.scene.gemini_user_prompt = ""
            return _finish()

        print(err)
        if _retries_done < MAX_RETRIES:
            _retries_done += 1
            fix = f"{prompt}\n\nFailed code:\n{code}"
            _start_worker(prompt, fix, err)
            return 0.2

        add_history_item(prompt, code, "FAIL", err.strip().splitlines()[-1][:200])
        return _finish()
    except Exception:
        traceback.print_exc()
        return _finish()   # никога не оставяй UI-а заключен


# --- OPERATORS ---
class OBJECT_OT_GeminiExecute(bpy.types.Operator):
    bl_idname = "object.gemini_execute"
    bl_label = "Send to AI"

    def execute(self, context):
        global _request_active, _retries_done
        if _request_active:
            self.report({'WARNING'}, "AI is already working, please wait...")
            return {'CANCELLED'}
        prefs = context.preferences.addons[__name__].preferences
        if not prefs.api_key:
            self.report({'ERROR'}, "Set API Key in Preferences!")
            return {'CANCELLED'}
        prompt = context.scene.gemini_user_prompt.strip()
        if not prompt:
            return {'CANCELLED'}

        _request_active = True
        _retries_done = 0
        _start_worker(prompt, prompt)
        if not bpy.app.timers.is_registered(poll_queue):
            bpy.app.timers.register(poll_queue)
        self.report({'INFO'}, "Request sent (working in background)...")
        return {'FINISHED'}


class OBJECT_OT_GeminiClearHistory(bpy.types.Operator):
    bl_idname = "object.gemini_clear_history"
    bl_label = "Clear Chat"

    def execute(self, context):
        context.scene.gemini_history.clear()
        return {'FINISHED'}


# --- UI ---
class VIEW3D_PT_GeminiPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Gemini AI'
    bl_label = "Gemini AI Agent"

    def draw(self, context):
        layout, scene = self.layout, context.scene
        col = layout.column(align=True)
        col.label(text="Ask AI:")
        col.prop(scene, "gemini_user_prompt", text="")
        row = col.row()
        row.enabled = not _request_active
        row.operator("object.gemini_execute", text="Run", icon='PLAY')
        if _request_active:
            layout.label(text="AI is working...", icon='SORTTIME')

        if len(scene.gemini_history):
            layout.separator()
            layout.label(text="Chat History:")
            box = layout.box()
            for e in scene.gemini_history:
                c = box.column(align=True)
                c.label(text=f"Me: {e.user_input}", icon='USER')
                c.label(text=e.info, icon='CHECKMARK' if e.status == "OK" else 'ERROR')
            layout.label(text=f"Last code: Text Editor > {CODE_TEXT_NAME}", icon='TEXT')
            layout.operator("object.gemini_clear_history", icon='TRASH', text="Clear History")


classes = (GEMINI_HistoryItem, GeminiPreferences, OBJECT_OT_GeminiExecute,
           OBJECT_OT_GeminiClearHistory, VIEW3D_PT_GeminiPanel)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gemini_user_prompt = bpy.props.StringProperty(name="")
    bpy.types.Scene.gemini_history = bpy.props.CollectionProperty(type=GEMINI_HistoryItem)


def unregister():
    if bpy.app.timers.is_registered(poll_queue):
        bpy.app.timers.unregister(poll_queue)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gemini_user_prompt
    del bpy.types.Scene.gemini_history


if __name__ == "__main__":
    register()
