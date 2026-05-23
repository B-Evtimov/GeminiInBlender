bl_info = {
    "name": "Gemini AI Assistant",
    "author": "Boris Evtimov",
    "version": (1, 1),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > Gemini AI",
    "description": "AI-powered Blender automation with chat history",
    "category": "Object",
}

import bpy
import urllib.request
import json

class GeminiPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key: bpy.props.StringProperty(
        name="API Key",
        subtype='PASSWORD',
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key")

def call_gemini_api(api_key, context):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    history_data = []
    for entry in context.scene.gemini_history:
        history_data.append({"role": "user", "parts": [{"text": entry.user_input}]})
        history_data.append({"role": "model", "parts": [{"text": entry.ai_response}]})
    
    current_prompt = (
        "You are a Blender Python expert. Output ONLY valid Python code using 'bpy'. "
        "No markdown, no explanations. Target the current scene. Task: " + context.scene.gemini_user_prompt
    )
    
    history_data.append({"role": "user", "parts": [{"text": current_prompt}]})
    
    data = {"contents": history_data}
    body = json.dumps(data).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            return res_data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"print('Error: {str(e)}')"

class GEMINI_HistoryItem(bpy.types.PropertyGroup):
    user_input: bpy.props.StringProperty()
    ai_response: bpy.props.StringProperty()

class OBJECT_OT_GeminiExecute(bpy.types.Operator):
    bl_idname = "object.gemini_execute"
    bl_label = "Execute AI Command"
    
    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        if not prefs.api_key:
            self.report({'ERROR'}, "API Key missing in Preferences")
            return {'CANCELLED'}
        
        prompt = context.scene.gemini_user_prompt
        raw_response = call_gemini_api(prefs.api_key, context)
        clean_code = raw_response.replace("```python", "").replace("```", "").strip()
        
        try:
            exec(clean_code)
            item = context.scene.gemini_history.add()
            item.user_input = prompt
            item.ai_response = clean_code
            context.scene.gemini_user_prompt = ""
            self.report({'INFO'}, "Success")
        except Exception as e:
            self.report({'ERROR'}, f"Python Error: {e}")
            
        return {'FINISHED'}

class OBJECT_OT_GeminiClearHistory(bpy.types.Operator):
    bl_idname = "object.gemini_clear_history"
    bl_label = "Clear History"
    
    def execute(self, context):
        context.scene.gemini_history.clear()
        return {'FINISHED'}

class VIEW3D_PT_GeminiPanel(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Gemini AI'
    bl_label = "Gemini Assistant"

    def draw(self, layout):
        scene = bpy.context.scene
        col = layout.column(align=True)
        
        col.label(text="Command:")
        col.prop(scene, "gemini_user_prompt", text="")
        col.separator()
        col.operator("object.gemini_execute", text="Run Command", icon='PLAY')
        
        if len(scene.gemini_history) > 0:
            layout.separator()
            layout.label(text="History:")
            box = layout.box()
            for entry in scene.gemini_history:
                box.label(text=entry.user_input, icon='CONSOLE')
            layout.operator("object.gemini_clear_history", icon='TRASH')

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
    bpy.types.Scene.gemini_user_prompt = bpy.props.StringProperty(name="Prompt")
    bpy.types.Scene.gemini_history = bpy.props.CollectionProperty(type=GEMINI_HistoryItem)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gemini_user_prompt
    del bpy.types.Scene.gemini_history

if __name__ == "__main__":
    register()