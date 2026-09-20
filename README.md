# Gemini AI Assistant for Blender

An open-source Blender add-on that integrates Google's Gemini AI directly into the 3D Viewport. Describe what you want in natural language — the add-on generates Blender Python, runs it, and automatically asks the AI to fix its own code if it fails.

## Features

- **Natural Language to Code** — objects, materials, modifiers, animations from a text prompt.
- **Non-blocking** — requests run on a background thread, Blender stays responsive.
- **Auto-correction** — on error, the traceback is sent back to Gemini for one fix attempt.
- **Chat history** — follow-up commands work ("Create a cube" → "Now move it up").
- **Code review** — every generated script is saved to the Text Editor (`gemini_last_code.py`) and printed to the system console.
- **Undo support** — each successful AI action is one undo step (Ctrl+Z).
- **Configurable model** — pick any Gemini model in the add-on preferences.

## Requirements

- Blender 3.2 or newer
- A Gemini API key

## Installation

1. Download `gemini_in_blender.py` from this repository.
2. In Blender: `Edit > Preferences > Add-ons > Install...` and select the file.
3. Enable **Gemini AI Assistant Pro**.
4. Expand the add-on, paste your **API Key**, and optionally change the **Model** (default: `gemini-3.5-flash`).

## Getting an API key

1. Open [Google AI Studio](https://aistudio.google.com/).
2. Click **Get API key** and create a key.
3. Paste it into the add-on preferences.

## Usage

1. In the 3D Viewport press `N` and open the **Gemini AI** tab.
2. Type a command, e.g. *"Create a procedural landscape with 50 random rocks"*.
3. Click **Run**.
4. **Clear History** starts a fresh session.

## ⚠️ Security note

This add-on executes AI-generated Python with full Blender permissions — there is no sandbox. Don't use it on files or machines where you can't afford surprises, and check the generated code in the Text Editor when in doubt.

## License

MIT — see [LICENSE](LICENSE).

---
*Created by **Boris Evtimov***
