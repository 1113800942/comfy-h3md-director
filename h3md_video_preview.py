"""H3MD Video Preview (H3MDVideoPreview) - shows a file saved by an H3MD node in its own, freely resizable node.

Input 'preview' (H3MD_VIDEO_PREVIEW): the 'preview' output of H3MD Save Video Interp, H3MD Save Video Face Refine
And Interp or H3MD Seam Face Refine - a dict for ComfyUI's /view (filename, subfolder, type, format, frame_rate,
first_frame) or None (nothing to show).  The player is web/js/h3md_video_preview.js (HTML5 <video>, letterboxed,
fills the node: drag the node's corner to resize it).
"""

from __future__ import annotations

try:
    from . import h3md_stream_save as S
except ImportError:  # flat import (tests)
    import h3md_stream_save as S

NODE_ID = "H3MDVideoPreview"
_KEYS = ("filename", "subfolder", "type", "format", "frame_rate", "first_frame")


class H3MDVideoPreview:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"preview": (S.PREVIEW_TYPE, {"tooltip":
                "'preview' output of H3MD Save Video Interp / Save Video Face Refine And Interp / Seam Face Refine."})}}

    RETURN_TYPES = ()
    OUTPUT_NODE = True
    FUNCTION = "show"
    CATEGORY = S.CATEGORY
    DESCRIPTION = "Plays the video an H3MD node saved, in a player that fills this node (move / resize freely)."

    def show(self, preview=None):
        entry = {k: preview.get(k) for k in _KEYS} if isinstance(preview, dict) and preview.get("filename") else None
        return {"ui": {"h3md_video": [entry] if entry else []}}


NODE_CLASS_MAPPINGS = {NODE_ID: H3MDVideoPreview}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "H3MD Video Preview"}
