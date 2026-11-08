"""H3 MultiSeg DualRes (H3MD) - ComfyUI custom node package.

Each module is imported in its own guard: a failure only drops that module's nodes and logs a warning.
Plain relative imports only (no dynamic imports), so security scanners see exactly which files are loaded.
"""

import logging

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def _m_h3md_director():
    from . import h3md_director as m
    return m

def _m_h3md_lora():
    from . import h3md_lora as m
    return m

def _m_h3md_sampler():
    from . import h3md_sampler as m
    return m

def _m_h3md_loop():
    from . import h3md_loop as m
    return m

def _m_h3md_loop_split():
    from . import h3md_loop_split as m
    return m

def _m_h3md_semantic_bridge():
    from . import h3md_semantic_bridge as m
    return m

def _m_h3md_stream_save():
    from . import h3md_stream_save as m
    return m

def _m_h3md_face_refine_save():
    from . import h3md_face_refine_save as m
    return m

def _m_h3md_video_preview():
    from . import h3md_video_preview as m
    return m

def _m_h3md_depth_control():
    from . import h3md_depth_control as m
    return m

def _m_h3md_control_panel():
    from . import h3md_control_panel as m
    return m

def _m_h3md_window():
    from . import h3md_window as m
    return m

def _m_h3md_director_refmod():
    from . import h3md_director_refmod as m
    return m


# h3md_director_refmod last: its nodes build on the ones above.
for _name, _load in (
        ("h3md_director", _m_h3md_director),
        ("h3md_lora", _m_h3md_lora),
        ("h3md_sampler", _m_h3md_sampler),
        ("h3md_loop", _m_h3md_loop),
        ("h3md_loop_split", _m_h3md_loop_split),
        ("h3md_semantic_bridge", _m_h3md_semantic_bridge),
        ("h3md_stream_save", _m_h3md_stream_save),
        ("h3md_face_refine_save", _m_h3md_face_refine_save),
        ("h3md_video_preview", _m_h3md_video_preview),
        ("h3md_depth_control", _m_h3md_depth_control),
        ("h3md_control_panel", _m_h3md_control_panel),
        ("h3md_window", _m_h3md_window),
        ("h3md_director_refmod", _m_h3md_director_refmod),
):
    try:
        _mod = _load()
        NODE_CLASS_MAPPINGS.update(_mod.NODE_CLASS_MAPPINGS)
        NODE_DISPLAY_NAME_MAPPINGS.update(_mod.NODE_DISPLAY_NAME_MAPPINGS)
    except Exception as _exc:
        logging.warning("[H3MD] %s not loaded: %s", _name, _exc)

try:  # Phase 2 editor helpers (read-only GET routes); never blocks node loading
    from . import h3md_routes as _routes
    _routes.register()
except Exception as _exc:
    logging.warning("[H3MD] editor routes not loaded: %s", _exc)

try:  # v0.2.0 plan export / import routes; never blocks node loading
    from . import h3md_plan_io as _plan_io
    _plan_io.register()
except Exception as _exc:
    logging.warning("[H3MD] plan export/import routes not loaded: %s", _exc)

def _read_version():
    """Version from pyproject.toml (the one source of truth), shown on the Director node so the installed build is
    visible at a glance (2026-11-04)."""
    import os
    import re
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


H3MD_VERSION = _read_version()
for _k in ("H3MDDirectorRefMod", "H3MDDirector"):   # display name only: node ids, widgets and caches unchanged
    if _k in NODE_DISPLAY_NAME_MAPPINGS:
        NODE_DISPLAY_NAME_MAPPINGS[_k] = f"{NODE_DISPLAY_NAME_MAPPINGS[_k]} v{H3MD_VERSION}"
logging.info("[H3MD] H3MD Director v%s loaded", H3MD_VERSION)

# Frontend: H3MD Director plan editor (web/js). plan_json stays the serialized widget.
WEB_DIRECTORY = "./web/js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
