"""Metadata Statistics extension - entry point.

Registers a new tab in the WebUI. All real logic lives in the ms_lib package
next to this scripts/ folder.
"""

import os
import sys
import traceback

_EXT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _EXT_DIR not in sys.path:
    sys.path.insert(0, _EXT_DIR)

from modules import script_callbacks  # noqa: E402

try:
    from ms_lib import ui as ms_ui
except Exception:
    traceback.print_exc()
    ms_ui = None


def on_ui_tabs():
    if ms_ui is None:
        import gradio as gr
        with gr.Blocks(analytics_enabled=False) as block:
            gr.Markdown(
                "**Metadata Statistics failed to load.** "
                "See the console for the traceback."
            )
        return [(block, "Metadata Statistics", "metadata_statistics")]
    block = ms_ui.build_tab()
    return [(block, "Metadata Statistics", "metadata_statistics")]


script_callbacks.on_ui_tabs(on_ui_tabs)
