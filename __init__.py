"""H3 MultiSeg DualRes (H3MD) - ComfyUI custom node package.

Each module is imported in its own guard: a failure only drops that module's nodes and logs a warning.
"""

import importlib
import logging

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

for _name in ("h3md_director", "h3md_lora", "h3md_sampler", "h3md_loop", "h3md_loop_split",
              "h3md_semantic_bridge", "h3md_stream_save", "h3md_face_refine_save", "h3md_video_preview",
              "h3md_depth_control"):
    try:
        _mod = importlib.import_module("." + _name, __name__)
        NODE_CLASS_MAPPINGS.update(_mod.NODE_CLASS_MAPPINGS)
        NODE_DISPLAY_NAME_MAPPINGS.update(_mod.NODE_DISPLAY_NAME_MAPPINGS)
    except Exception as _exc:
        logging.warning("[H3MD] %s not loaded: %s", _name, _exc)

try:  # H3MD Director + RefMod (new nodes in h3md_director_refmod.py; the nodes above are unchanged)
    _mod = importlib.import_module(".h3md_director_refmod", __name__)
    NODE_CLASS_MAPPINGS.update(_mod.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_mod.NODE_DISPLAY_NAME_MAPPINGS)
except Exception as _exc:
    logging.warning("[H3MD] h3md_director_refmod not loaded: %s", _exc)

try:  # Phase 2 editor helpers (read-only GET routes); never blocks node loading
    from . import h3md_routes as _routes
    _routes.register()
except Exception as _exc:
    logging.warning("[H3MD] editor routes not loaded: %s", _exc)

# Frontend: H3MD Director plan editor (web/js). plan_json stays the serialized widget.
WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
