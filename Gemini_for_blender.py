bl_info = {
    "name": "Gemini AI Assistant Pro",
    "author": "Boris Evtimov",
    "version": (2, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Gemini AI",
    "description": "Autonomous AI Agent with Scene Awareness and Auto-Correction",
    "category": "Object",
}

import bpy
import urllib.request
import json
import traceback

class GeminiPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__
    api_key: bpy.props.StringProperty(name="API Key", subtype='PASSWORD')

    def draw(self, context):
        self.layout.prop(self, "api_key")

def get_scene_context():
    return {
        "active_object": bpy.context.active_object.name if bpy.context.active_object else "None",
        "selected_objects": [obj.name for obj in bpy.context.selected_objects],
        "all_objects": [obj.name for obj in bpy.data.objects][:20], 
        "unit_system": bpy.context.scene.unit_settings.system,
        "render_engine": bpy.context.scene.render.engine
    }

def call_gemini_api(api_key, context, custom_prompt, error_log=None):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    scene_data = get_scene_context()
    system_instr = (
        "You are a Blender Python Expert. Output ONLY valid Python code. No markdown. "
        f"Scene Context: {json.dumps(scene_data)}. "
    )
    
    if error_log:
        system_instr += f"SYSTEM ALERT: Previous code failed with error: {error_log}. Fix the code."

    history = []
    for entry in context.scene.gemini_history:
        history.append({"role": "user", "parts": [{"text": entry.user_input}]})
        history.append({"role": "model", "parts": [{"text": entry.ai_response}]})
    
    history.append({"role": "user", "parts": [{"text": system_instr + "\nTask: " + custom_prompt}]})
    
    data = {"contents": history}
    body = json.dumps(data).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            return res_data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"print('API Error: {str(e)}')"

class GEMINI_HistoryItem(bpy.types.PropertyGroup):
    user_input: bpy.props.StringProperty()
    ai_response: bpy.props.StringProperty()

class OBJECT_OT_GeminiExecute(bpy.types.Operator):
    bl_idname = "object.gemini_execute"
    bl_label = "Run AI Agent"
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        if not prefs.api_key:
            self.report({'ERROR'}, "API Key missing")
            return {'CANCELLED'}
        
        user_prompt = context.scene.gemini_user_prompt
        error_to_send = None
        max_retries = 2

        for i in range(max_retries + 1):
            raw_response = call_gemini_api(prefs.api_key, context, user_prompt, error_to_send)
            clean_code = raw_response.replace("```python", "").replace("```", "").strip()
            
            # --- SAFE EXECUTION SANDBOX ---
            safe_globals = {
                "bpy": bpy,
                "context": context,
                "math": __import__("math"),
                "bmesh": __import__("bmesh"),
                "__builtins__": {
                    "range": range, "len": len, "print": print, 
                    "list": list, "dict": dict, "int": int, "float": float
                }
            }

            try:
                exec(clean_code, safe_globals)
                
                item = context.scene.gemini_history.add()
                item.user_input = user_prompt
                item.ai_response = clean_code
                context.scene.gemini_user_prompt = ""
                self.report({'INFO'}, f"Success (Attempts: {i+1})")
                break 
            except Exception:
                error_to_send = traceback.format_exc()
                print(f"Attempt {i+1} failed. Retrying...")
                if i == max_retries:
                    self.report({'ERROR'}, "AI failed to fix the code after retries.")
            
        return {'FINISHED'}

class OBJECT_OT_GeminiClearHistory(bpy.types.Operator):
    bl_idname = "object.gemini_clear_history"
    bl_label = "Clear Session"
    def execute(self, context):
        context.scene.gemini_history.clear()
        return {'FINISHED'}

class VIEW3D_PT_GeminiPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Gemini AI'
    bl_label = "Gemini Agent Pro"

    def draw(self, layout):
        scene = bpy.context.scene
        layout.prop(scene, "gemini_user_prompt", text="")
        layout.operator("object.gemini_execute", text="Execute Task", icon='FORWARD')
        
        if len(scene.gemini_history) > 0:
            layout.separator()
            box = layout.box()
            for entry in scene.gemini_history:
                box.label(text=entry.user_input, icon='TEXT')
            layout.operator("object.gemini_clear_history", icon='TRASH')

def register():
    bpy.utils.register_class(GEMINI_HistoryItem)
    bpy.utils.register_class(GeminiPreferences)
    bpy.utils.register_class(OBJECT_OT_GeminiExecute)
    bpy.utils.register_class(OBJECT_OT_GeminiClearHistory)
    bpy.utils.register_class(VIEW3D_PT_GeminiPanel)
    bpy.types.Scene.gemini_user_prompt = bpy.props.StringProperty(name="Command")
    bpy.types.Scene.gemini_history = bpy.props.CollectionProperty(type=GEMINI_HistoryItem)

def unregister():
    bpy.utils.unregister_class(GEMINI_HistoryItem)
    bpy.utils.unregister_class(GeminiPreferences)
    bpy.utils.unregister_class(OBJECT_OT_GeminiExecute)
    bpy.utils.unregister_class(OBJECT_OT_GeminiClearHistory)
    bpy.utils.unregister_class(VIEW3D_PT_GeminiPanel)
    del bpy.types.Scene.gemini_user_prompt
    del bpy.types.Scene.gemini_history

if __name__ == "__main__":
    register()
