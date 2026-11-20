"""0.5.9: model folders H3MD's face refine needs, registered by H3MD itself.

ComfyUI-H3-FaceRefine lists its face detectors from the 'ultralytics_bbox' / 'ultralytics' model folders, which
only ComfyUI-Impact-Subpack registers.  Without the Subpack a detector put in models/ultralytics/bbox never shows
in the list (Bilibili reports, 2026-11-20).  H3MD registers the same folders the same way (only what is missing),
for ComfyUI's own models folder and every other models root ComfyUI knows (extra_model_paths, the Desktop app's
shared folder), so the Subpack is no longer needed for the list.
"""
import logging
import os

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD]"
ULTRA_KEYS = {"ultralytics_bbox": ("ultralytics", "bbox"), "ultralytics_segm": ("ultralytics", "segm")}


def _model_roots(fp):
    """ComfyUI's models folder plus the models root of every registered checkpoints folder."""
    roots = [getattr(fp, "models_dir", None)]
    try:
        for p in fp.folder_names_and_paths.get("checkpoints", ([], set()))[0]:
            roots.append(os.path.dirname(os.path.normpath(p)))
    except Exception:
        pass
    out, seen = [], set()
    for r in roots:
        if r and os.path.normcase(os.path.abspath(r)) not in seen:
            seen.add(os.path.normcase(os.path.abspath(r)))
            out.append(r)
    return out


def register_ultralytics(fp=None):
    """Add models/ultralytics/bbox and .../segm under every models root to the 'ultralytics_bbox' /
    'ultralytics_segm' folder keys (created with ComfyUI's model extensions when missing). Returns what was added."""
    if fp is None:
        import folder_paths as fp
    exts = set(getattr(fp, "supported_pt_extensions", {".pt", ".pth", ".safetensors"})) | {".pt"}
    added = []
    for key, sub in ULTRA_KEYS.items():
        entry = fp.folder_names_and_paths.get(key)
        if entry is None:
            entry = ([], exts)
            fp.folder_names_and_paths[key] = entry
        paths = entry[0]
        have = {os.path.normcase(os.path.abspath(p)) for p in paths}
        for root in _model_roots(fp):
            p = os.path.join(root, *sub)
            if os.path.normcase(os.path.abspath(p)) not in have:
                paths.append(p)
                have.add(os.path.normcase(os.path.abspath(p)))
                added.append((key, p))
    try:   # ComfyUI caches file lists per folder key
        for key in ULTRA_KEYS:
            getattr(fp, "filename_list_cache", {}).pop(key, None)
    except Exception:
        pass
    if added:
        LOG.info("%s registered face-detector folders: %s", _TAG, "; ".join(f"{k} -> {p}" for k, p in added))
    return added


def find_detector(name, fp=None):
    """Full path of a face detector file, looked up the way ComfyUI-H3-FaceRefine does; None when missing."""
    if not name or name == "none":
        return None
    if fp is None:
        import folder_paths as fp
    for key in ("ultralytics_bbox", "ultralytics", "ultralytics_segm"):
        try:
            p = fp.get_full_path(key, name)
        except Exception:
            p = None
        if p:
            return p
    for root in _model_roots(fp):
        for sub in (("ultralytics", "bbox"), ("ultralytics",), ("ultralytics", "segm")):
            cand = os.path.join(root, *sub, name)
            if os.path.exists(cand):
                return cand
    return None


def detector_files(fp=None):
    """Detector file names in the registered folders (for the node list when ComfyUI-H3-FaceRefine is missing)."""
    if fp is None:
        import folder_paths as fp
    names = []
    for key in ("ultralytics_bbox", "ultralytics"):
        try:
            names.extend(fp.get_filename_list(key))
        except Exception:
            pass
    return list(dict.fromkeys(names))
