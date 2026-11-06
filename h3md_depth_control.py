"""H3MD Depth Control (2026-10-26) - optional depth ControlNet for the H3MD Loop low-res stage.

Node "H3MD Depth Control" (H3MDDepthControl) only builds a SETTINGS dict (type H3MD_DEPTH): file names, numbers and
the VAE reference passed through (the VAE is not loaded or copied here; the Loop uses it only to encode the control
video inside core's Fun ControlNet patch).  No model, patch or tensor is ever held by this node.

Connect it to the optional input `depth_control` (appended LAST) of H3MD Loop Sampler / H3MD Loop Sampler + RefMod /
H3MD Loop Sampler (legacy).  Per plan video (Director editor): `depth` (default false).  The per-video `ref` flag was
removed (2026-10-26 audioui): every video is a <Video N>; a legacy ref=false is ignored by parse_plan.
Depth-only route (2026-10-26 depthonly, the tutorial's idea), decided PER SEGMENT:
  depth on           : the depth video drives ONLY the ControlNet (2026-10-30 ctlonly); the video is NOT a <Video N>
                       reference at all (the Director gives it no <Video N> label) - unless it also supplies the
                       soundtrack <Audio>, then the depth frames stay its <Video N> (warning).  The raw video only
                       supplies the window (duration / frame count).
  depth off          : unchanged (raw video reference as before); no depth model, no patch, old cache key.
  Not connected      : error before the run (a depth video has no <Video N> label, so it cannot fall back).
  Audio (soundtrack <Audio> / locked track) is independent of the picture route: it follows only the video's
  useSoundtrack and the shot's lock, depth on or off (2026-10-26 audioui).
At most one depth video per segment (the first one wins; the editor is single-choice).
A depth-on segment never changes another segment's Combined inputs, model patches or cache key: the override lives on
a per-segment plan copy, the patch on a per-segment clone of the low-res model (released after its low-res stage), and
the depth settings enter only that segment's cache key (segment_cache_settings).

Lazy loading (the Loop, per segment):
  * a segment without a depth video never loads the depth model or the int8 ControlNet patch;
  * depth segment, BEFORE encode: decode the raw window frames (no crop) -> comfyui_controlnet_aux
    DepthAnythingV2Preprocessor (looked up at runtime in nodes.NODE_CLASS_MAPPINGS) -> result kept on CPU ->
    the depth model is dropped (aux deletes it after the call) + gc + soft_empty_cache;
  * then (after encode / continue) the int8 patch is loaded (core ModelPatchLoader) and registered on a CLONE of the
    low-res model; right after the low-res stage: patch.cleanup() + unload + soft_empty_cache; never kept between
    segments.  The high-res stage never gets control (guard: assert_no_fun_control).

Frame fit (2026-10-26 depthfit: identical to the reference-video path, no letterbox, no crop):
  The H3 reference path (core nodes_minimax_h3.MiniMaxH3ReferenceToVideo.execute) maps a reference video edge to edge:
  _resize(video_frames, cw, ch, "disabled") = comfy.utils.common_upscale(..., "lanczos", crop="disabled"), i.e. a plain
  stretch of the FULL frame (nothing cropped, no bars).  Every depth copy now uses that same function and mode:
  (a) depth inference: aux itself resizes keeping the aspect (short side = resolution), pads to a multiple of 64 and
      removes the pad again (resize_image_with_pad / remove_pad), so its output is the full source frame.
  (b) ControlNet copy: ref_fit_frames() = core _resize(depth, canvas_w, canvas_h, "disabled") straight to the low-res
      canvas (latent x 16): the full depth frame stretched edge to edge exactly like the reference video, so control
      pixel (x, y) and reference pixel (x, y) cover the same normalised source position; frame count = core's
      ((T-2)//5)*17+5.  The tensor already has core's exact target size, so core _fit_frames ("bilinear", "center")
      neither resizes nor crops anything.
  (c) H3 reference copy (depth on): the depth frames are resized back to the source video's native frame size
      and go through the normal reference path (apply_ref_video_height, then core _resize "disabled").
SAM3 mask (2026-10-26 sam3): a depth video's per-video sam3Prompt (e.g. "person, TV") is used ONLY by SAM3 - it
never enters the H3 / model prompt.  SAM3 runs only when sam3_enabled is on AND that depth video has a non-empty
sam3Prompt (else: no SAM3 model, full depth, byte-identical to before).  Core's native SAM3.1 (checkpoint
sam3.1_multiplex_fp16.safetensors, = CheckpointLoaderSimple + CLIPTextEncode + SAM3_VideoTrack + SAM3_TrackToMask with
all objects) tracks the comma-separated prompts on the SAME raw window frames (same oneshot origin) the depth is
computed from; detection_threshold = sam3_threshold; the union of all tracked objects is kept and every other depth
pixel is set to black (0) BEFORE the depth feeds the ControlNet and the <Video N> reference.  Overlap control frames
taken from the previous control tail stay as they are (already masked by the previous segment).  SAM3 is loaded
lazily per such segment and unloaded right after (+ soft_empty_cache), before the depth model is loaded.
Cache: only a SAM3-masked segment gets an extra key part (sam3_mask); the prompt is in the segment signature.
"""

from __future__ import annotations

import gc
import logging
import math

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Depth]"
CATEGORY = "MiniMax H3/H3MD"
DEPTH_TYPE = "H3MD_DEPTH"
DEFAULT_CONTROL = "minimax_h3_fun_controlnet_union_pruned_int8_convrot.safetensors"
DEPTH_MODELS = ("depth_anything_v2_vitl.pth", "depth_anything_v2_vitb.pth", "depth_anything_v2_vits.pth",
                "depth_anything_v2_vitg.pth")   # Depth Anything V2 only (comfyui_controlnet_aux names)
DEFAULT_DEPTH = "depth_anything_v2_vitl.pth"
AUX_NODE = "DepthAnythingV2Preprocessor"
BLOCK_PATCH = "MiniMaxH3FunControlBlockPatch"
FPS = 24
FIT_MODE = "ref_stretch"   # cache-key tag of the control frame fit (depth plans only; letterbox caches never reused)
ROUTE = "controlnet_only"  # cache-key tag (depth-on segments only), 2026-10-30 ctlonly: depth drives ONLY the ControlNet
                           # (was "depth_only": depth = ControlNet + <Video N>; a silhouette on black as <Video N> made
                           # stretches of the output black / studio grey)
SAM3_CKPT = "sam3.1_multiplex_fp16.safetensors"   # core SAM3.1 (models/checkpoints), text-prompted video tracking
SAM3_MASK = "sam3.1_videotrack_union_black"       # cache-key tag (SAM3-masked depth segments only)
# 2026-11-04 pose mode (branch pose-exp): the same node / Loop path can feed a DWPose-style skeleton video to the Fun
# ControlNet instead of depth.  control_kind "depth" (default) keeps every depth cache key byte-identical.
CONTROL_KINDS = ("depth", "pose")
POSE_ENGINES = ("sdpose", "dwpose")
SDPOSE_CKPT = "sdpose_wholebody_fp16.safetensors"       # core SDPose (models/checkpoints)
SDPOSE_BATCH = 16
POSE_STICK, POSE_FACE_POINT, POSE_SCORE = 4, 2, 0.3     # SDPoseDrawKeypoints settings of the user's pose workflow
DWPOSE_NODE = "DWPreprocessor"                          # comfyui_controlnet_aux
DWPOSE_DET = "yolox_l.onnx"
DWPOSE_EST = "dw-ll_ucoco_384_bs5.torchscript.pt"       # the TorchScript file already on disk (no download)


# ---------------------------------------------------------------------------
# plan helpers (pure python)
# ---------------------------------------------------------------------------

def ref_on(ref: dict) -> bool:
    return ref.get("ref", True) is not False


def segment_depth_ref(seg: dict):
    """The segment's depth video ref (first with depth true) or None."""
    for ref in seg.get("videos") or []:
        if isinstance(ref, dict) and ref.get("depth") is True:
            return ref
    return None


def sam3_prompt(ref) -> str:
    """The depth video's SAM3 text (SAM3 only - never part of the H3 prompt)."""
    return str((ref or {}).get("sam3Prompt") or "").strip()


def sam3_active(dc, ref) -> bool:
    """SAM3 runs only when the depth video carries a non-empty sam3Prompt.  2026-10-27 sam3box: the Director keeps
    sam3Prompt only when the editor's per-video SAM3 box is ticked, so that box (+ text) is the switch; the node's
    sam3_enabled widget is hidden and ignored (old workflows saved it as False)."""
    return bool(dc) and bool(sam3_prompt(ref))


def plan_uses_depth(plan: dict, indices=None) -> bool:
    segs = plan.get("segments") or []
    idx = range(len(segs)) if indices is None else indices
    return any(segment_depth_ref(segs[k]) is not None for k in idx)


def control_frame_count(latent_frames: int) -> int:
    """Pixel frames core's Fun patch encodes for T latent frames (nodes_minimax_h3.prepare_control_latent)."""
    return max((int(latent_frames) - 2) // 5, 0) * 17 + 5


def control_alignment(seg: dict, rw_info, ref_id: str, continued=None) -> dict:
    """How control frame i maps to the depth window (spec point 5).  H3MD oneshot (2026-10-26): the depth window
    starts at output frame 0 (D.video_window_origin = start - overlapIn/24), so window[i] = source time
    start + (i - overlapIn)/24 = the time of output frame i (same as <Video N>, soundtrack and locked audio).
      aligned : control[i] = window[i] (first segment, hard cut, advanced refwindow sub-segment, overlapIn without a
                previous latent = frames kept)
      continue: control[0:ov] = previous control tail (else window[0:ov]); control[ov:] = window[ov:]
      off     : refwindow fallback for this video -> depth disabled for the segment"""
    if rw_info:
        for v in rw_info.get("videos") or []:
            if str(v.get("id")) == str(ref_id) and v.get("mode") == "fallback":
                return {"mode": "off", "overlap": 0, "why": "refwindow fallback (window could not advance)"}
        return {"mode": "aligned", "overlap": 0, "why": "refwindow advanced window"}
    ov = int(seg.get("overlapIn") or 0)
    if ov > 0 and continued is not False:   # continued=False: no previous latent (start_index) -> like a cut
        return {"mode": "continue", "overlap": ov, "why": f"continuation, overlap {ov}"}
    return {"mode": "aligned", "overlap": 0, "why": "first segment / hard cut"}


def align_indices(mode: str, count: int, overlap: int, n_window: int, has_prev: bool) -> list:
    """Source of every control frame: ("win", j) or ("prev", j); the window is padded with its last frame.
    H3MD oneshot (2026-10-26): the window starts at output frame 0, so control[i] = window[i] in every mode; only a
    continuation with a previous control tail takes frames 0..overlap-1 from that tail."""
    last = max(0, int(n_window) - 1)
    out = []
    for i in range(int(count)):
        if mode == "continue" and i < overlap and has_prev:
            out.append(("prev", i))
        else:
            out.append(("win", min(max(i, 0), last)))
    return out


def window_frames_needed(mode: str, length: int, overlap: int, ref_is_on: bool) -> int:
    """Raw window frames to decode: the full segment length when the depth video is also the reference
    (same call as pick_segment_media), else only what the control needs."""
    if ref_is_on or mode != "continue":
        return int(length)
    return max(5, int(length) - int(overlap))


def control_kind(dc) -> str:
    k = str((dc or {}).get("control_kind", "depth"))
    return k if k in CONTROL_KINDS else "depth"


def pose_options(dc: dict) -> dict:
    eng = str(dc.get("pose_engine", "sdpose"))
    return {"engine": eng if eng in POSE_ENGINES else "sdpose", "hands": bool(dc.get("pose_hands", True)),
            "face": bool(dc.get("pose_face", False)), "feet": bool(dc.get("pose_feet", False)),
            "resolution": int(dc.get("depth_resolution", 512))}


def pose_cache_part(dc: dict) -> dict:
    o = pose_options(dc)
    if o["engine"] == "sdpose":   # SDPose stretches to its own 768x1024; resolution does not change it
        o.pop("resolution")
        o.update(ckpt=SDPOSE_CKPT, stick=POSE_STICK, face_point=POSE_FACE_POINT, score=POSE_SCORE)
    else:
        o.update(det=DWPOSE_DET, est=DWPOSE_EST)
    return o


def depth_settings(dc: dict) -> dict:
    """Cache-key part (weights are not hashed; the VAE reference is not part of the key).  Depth: unchanged."""
    if control_kind(dc) == "pose":
        return {"strength": round(float(dc["strength"]), 6), "start": round(float(dc["start_percent"]), 6),
                "end": round(float(dc["end_percent"]), 6), "control": str(dc["control_model"]),
                "kind": "pose", "pose": pose_cache_part(dc), "fit": FIT_MODE}
    return {"strength": round(float(dc["strength"]), 6), "start": round(float(dc["start_percent"]), 6),
            "end": round(float(dc["end_percent"]), 6), "control": str(dc["control_model"]),
            "depth_model": str(dc["depth_model"]), "resolution": int(dc["depth_resolution"]),
            "sam3": {"enabled": bool(dc["sam3_enabled"]), "threshold": round(float(dc["sam3_threshold"]), 6)},
            "fit": FIT_MODE}


def has_fun_control(model) -> bool:
    """True if a ModelPatcher carries core's Fun ControlNet block patch (patches_replace['dit'])."""
    opts = getattr(model, "model_options", None) or {}
    dit = ((opts.get("transformer_options") or {}).get("patches_replace") or {}).get("dit") or {}
    for p in dit.values():
        seen = 0
        while p is not None and seen < 64:
            if type(p).__name__ == BLOCK_PATCH:
                return True
            p, seen = getattr(p, "previous", None), seen + 1
    return False


def assert_no_fun_control(model, where: str = "high-res model"):
    if model is not None and has_fun_control(model):
        raise RuntimeError(f"{_TAG} the {where} carries a MiniMax H3 Fun ControlNet patch ({BLOCK_PATCH}); "
                           "the high-res stage must never get control (connect an unpatched high_res_model)")


def without_video(plan: dict, k: int, ref_id: str):
    """2026-10-30 ctlonly: shallow plan copy whose segment k has NO <Video N> for ref_id (ControlNet only).
    Labels are recomputed (segments with RefMods keep theirs).  The input plan is not modified."""
    try:
        from . import h3md_director as D
    except ImportError:  # flat import (tests)
        import h3md_director as D
    segs = list(plan["segments"])
    seg = dict(segs[k])
    seg["videos"] = [ref for ref in (seg.get("videos") or []) if str(ref.get("id")) != str(ref_id)]
    if not seg.get("refmods") and "labels" in seg:
        seg["labels"] = D.compute_labels(seg, plan["media"])
    segs[k] = seg
    out = dict(plan)
    out["segments"] = segs
    return out


def with_video_override(plan: dict, k: int, ref_id: str, frames):
    """Shallow plan copy whose segment k decodes `frames` (the depth video) instead of video ref_id.
    Depth-only route: the depth video is that video's <Video N> reference.  A legacy ref-off entry (only possible
    in a plan not built by parse_plan, which drops the flag) is turned into a reference in THIS copy only, keeping
    its useSoundtrack (audio is independent of the picture route, 2026-10-26 audioui), and the segment's labels are
    recomputed (segments with RefMods keep their director labels).  The input plan is not modified."""
    segs = list(plan["segments"])
    seg = dict(segs[k])
    seg["_videoOverrides"] = dict(seg.get("_videoOverrides") or {}, **{str(ref_id): frames})
    videos = list(seg.get("videos") or [])
    promoted = False
    for i, ref in enumerate(videos):
        if isinstance(ref, dict) and str(ref.get("id")) == str(ref_id) and ref.get("ref", True) is False:
            new = {key: val for key, val in ref.items() if key != "ref"}   # audioui: useSoundtrack kept as is
            videos[i] = new
            promoted = True
            break
    if promoted:
        try:
            from . import h3md_director as D
        except ImportError:  # flat import (tests)
            import h3md_director as D
        n_ref = sum(1 for ref in videos if ref.get("ref", True) is not False)
        if n_ref > D.MAX_VIDEOS:
            raise ValueError(f"{_TAG} segment {k + 1}: depth video '{ref_id}' needs a <Video N> slot but the segment "
                             f"already has {n_ref - 1} reference videos (max {D.MAX_VIDEOS})")
        seg["videos"] = videos
        if not seg.get("refmods") and "labels" in seg:
            seg["labels"] = D.compute_labels(seg, plan["media"])
        elif seg.get("refmods"):
            LOG.warning("%s segment %d: legacy ref-off depth video '%s' gets a <Video> slot but the segment has RefMods; "
                        "its label list is not renumbered (log / labels output only)", _TAG, k + 1, ref_id)
    segs[k] = seg
    out = dict(plan)
    out["segments"] = segs
    return out


# ---------------------------------------------------------------------------
# torch helpers
# ---------------------------------------------------------------------------

def resize_frames(frames, w: int, h: int, chunk: int = 32):
    """IMAGE [N,H,W,C] -> [N,h,w,C] bilinear (no crop), CPU float32."""
    import torch
    import torch.nn.functional as F
    if int(frames.shape[2]) == int(w) and int(frames.shape[1]) == int(h):
        return frames.to("cpu", torch.float32)
    out = torch.empty((frames.shape[0], int(h), int(w), frames.shape[3]), dtype=torch.float32)
    for a in range(0, frames.shape[0], chunk):
        x = frames[a:a + chunk].to(torch.float32).movedim(-1, 1)
        out[a:a + chunk] = F.interpolate(x, size=(int(h), int(w)), mode="bilinear", align_corners=False).movedim(1, -1).clamp_(0, 1)
    return out


def ref_fit_frames(frames, dst_w: int, dst_h: int):
    """IMAGE [N,H,W,C] -> [N,dst_h,dst_w,3] with the reference-video path's own fit: core
    nodes_minimax_h3._resize(frames, w, h, "disabled") = common_upscale lanczos, crop disabled (full frame stretched
    edge to edge, no bars, no crop).  CPU float32."""
    import torch
    from comfy_extras import nodes_minimax_h3 as core
    out = core._resize(frames.to("cpu", torch.float32), int(dst_w), int(dst_h), "disabled")
    return out.to("cpu", torch.float32).clamp_(0, 1).contiguous()


def run_depth(frames, depth_model: str, resolution: int):
    """comfyui_controlnet_aux Depth Anything V2 on raw frames -> CPU IMAGE (source aspect).  The aux node loads
    the model for this call only and deletes it afterwards; here: drop every reference + gc + soft_empty_cache."""
    import nodes  # ComfyUI's global node registry (aux registers its classes there)
    cls = getattr(nodes, "NODE_CLASS_MAPPINGS", {}).get(AUX_NODE)
    if cls is None:
        raise RuntimeError(f"{_TAG} '{AUX_NODE}' not found: comfyui_controlnet_aux is not installed or failed to load "
                           "(H3MD Depth Control needs it for Depth Anything V2)")
    node = cls()
    try:
        out = node.execute(frames, ckpt_name=depth_model, resolution=int(resolution))[0]
        out = out.to("cpu").float()
    finally:
        del node
        gc.collect()
        try:
            from . import h3md_vram
        except ImportError:
            import h3md_vram
        h3md_vram.soft_empty_cache("(depth model released)")
    return out


def _release_comfy_model(patcher, reason: str):
    """Unload a core ModelPatcher (and its clones) from ComfyUI's loaded list, then drop it."""
    try:
        import comfy.model_management as mm
        inner = getattr(patcher, "model", None)
        for lm in list(getattr(mm, "current_loaded_models", [])):
            m = getattr(lm, "model", None)
            if m is not None and (m is patcher or getattr(m, "model", None) is inner):
                try:
                    lm.model_unload()
                except Exception:
                    pass
                try:
                    mm.current_loaded_models.remove(lm)
                except ValueError:
                    pass
    except Exception as e:   # never fail a run on cleanup
        LOG.warning("%s pose model release: %s", _TAG, e)
    gc.collect()
    try:
        from . import h3md_vram
    except ImportError:
        import h3md_vram
    h3md_vram.soft_empty_cache(reason)


def _sdpose(frames, opts):
    import nodes
    from comfy_extras import nodes_sdpose as SP
    model, vae = None, None
    try:
        model, _clip, vae = nodes.CheckpointLoaderSimple().load_checkpoint(SDPOSE_CKPT)[:3]
        kps = SP.SDPoseKeypointExtractor.execute(model=model, vae=vae, image=frames, batch_size=SDPOSE_BATCH)[0]
    finally:
        if model is not None:
            _release_comfy_model(model, "(SDPose model released)")
        del model, vae
        gc.collect()
    out = SP.SDPoseDrawKeypoints.execute(keypoints=kps, draw_body=True, draw_hands=opts["hands"],
                                         draw_face=opts["face"], draw_feet=opts["feet"], stick_width=POSE_STICK,
                                         face_point_size=POSE_FACE_POINT, score_threshold=POSE_SCORE,
                                         draw_head=True)[0]
    return out


def _dwpose(frames, opts):
    import nodes
    cls = getattr(nodes, "NODE_CLASS_MAPPINGS", {}).get(DWPOSE_NODE)
    if cls is None:
        raise RuntimeError(f"{_TAG} '{DWPOSE_NODE}' not found: comfyui_controlnet_aux is not installed or failed to "
                           "load (pose_engine dwpose needs it; or choose pose_engine sdpose)")
    node = cls()
    try:
        r = node.estimate_pose(frames, detect_hand="enable" if opts["hands"] else "disable", detect_body="enable",
                               detect_face="enable" if opts["face"] else "disable", resolution=opts["resolution"],
                               bbox_detector=DWPOSE_DET, pose_estimator=DWPOSE_EST)
        return r["result"][0] if isinstance(r, dict) else r[0]
    finally:
        del node
        gc.collect()
        try:
            from . import h3md_vram
        except ImportError:
            import h3md_vram
        h3md_vram.soft_empty_cache("(DWPose model released)")


def run_pose(frames, opts: dict):
    """Skeleton video (black background, OpenPose colours) from raw frames -> CPU IMAGE at the source size."""
    if opts["engine"] == "sdpose":
        try:
            from comfy_extras import nodes_sdpose  # noqa: F401
        except ImportError as e:
            raise RuntimeError(f"{_TAG} core SDPose nodes not available ({e}): update ComfyUI or choose pose_engine "
                               "dwpose") from e
        out = _sdpose(frames, opts)
    else:
        out = _dwpose(frames, opts)
    return out.to("cpu").float()[..., :3].contiguous()


def load_control_patch(name: str):
    import comfy_extras.nodes_model_patch as MP
    return MP.ModelPatchLoader().load_model_patch(name)[0]


def apply_control(model, model_patch, vae, control, strength: float, start: float, end: float):
    """Same as core MiniMaxH3FunControlNetApply.execute with control_video only -> (patched clone, patch)."""
    from comfy_extras import nodes_minimax_h3 as core
    ms = model.get_model_object("model_sampling")
    patch = core.MiniMaxH3FunControlPatch(model_patch, vae, control[..., :3].movedim(-1, 1), None, None, float(strength),
                                          float(ms.percent_to_sigma(start)), float(ms.percent_to_sigma(end)))
    patched = model.clone()
    patch.register(patched)
    return patched, patch


# ---------------------------------------------------------------------------
# Loop helper
# ---------------------------------------------------------------------------

class ControlHandle:
    """One segment's loaded int8 patch; release() is idempotent (low-res end hook + Loop finally)."""

    def __init__(self, patch, model_patch, release_fn=None):
        self.patch, self.model_patch, self.released = patch, model_patch, False
        self._release_fn = release_fn

    def release(self, reason: str = "(after low-res stage)"):
        if self.released:
            return
        self.released = True
        try:
            if self.patch is not None:
                self.patch.cleanup()
        finally:
            mp, self.patch, self.model_patch = self.model_patch, None, None
            if self._release_fn is not None:
                self._release_fn(mp, reason)
            del mp
            gc.collect()


def _default_release(model_patch, reason):
    try:
        from . import h3md_vram
    except ImportError:
        import h3md_vram
    if model_patch is not None:
        h3md_vram.unload_patchers([model_patch], f"(depth ControlNet patch released {reason})")
    else:
        h3md_vram.soft_empty_cache(reason)


def run_sam3(frames, prompt: str, threshold: float):
    """Core SAM3.1 video tracking of `prompt` (comma-separated, core's syntax) on raw frames [N, H, W, C] (CPU).
    -> bool masks [N, Hm, Wm] on CPU (union of all tracked objects, SAM3's mask resolution).  The SAM3 model and its
    text encoder are loaded here and unloaded before returning (+ soft_empty_cache)."""
    import folder_paths
    import comfy.sd
    import comfy.model_management as mm
    try:
        from . import h3md_vram
    except ImportError:
        import h3md_vram
    path = folder_paths.get_full_path("checkpoints", SAM3_CKPT)
    if not path:
        raise FileNotFoundError(f"{_TAG} SAM3 checkpoint '{SAM3_CKPT}' not found in models/checkpoints "
                                "(needed because a depth video has a SAM3 prompt and sam3_enabled is on)")
    from comfy_extras.nodes_sam3 import _extract_text_prompts
    from comfy.ldm.sam3.tracker import unpack_masks
    out = comfy.sd.load_checkpoint_guess_config(path, output_vae=False, output_clip=True,
                                                embedding_directory=folder_paths.get_folder_paths("embeddings"))
    model, clip = out[0], out[1]
    del out
    patchers = [model] + ([clip.patcher] if clip is not None and getattr(clip, "patcher", None) is not None else [])
    try:
        return _sam3_track(model, clip, frames, prompt, threshold, mm, _extract_text_prompts, unpack_masks)
    finally:   # always unload SAM3 (model + text encoder) before the depth model is loaded
        h3md_vram.unload_patchers(patchers, "(SAM3 unloaded)")
        del model, clip, patchers
        gc.collect()
        h3md_vram.soft_empty_cache("(SAM3 freed)")


def _sam3_track(model, clip, frames, prompt, threshold, mm, extract_text_prompts, unpack_masks):
    import torch
    with torch.inference_mode():
        cond = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))   # = CLIPTextEncode (SAM3's own text encoder)
        mm.load_model_gpu(model)
        device = mm.get_torch_device()
        dtype = model.model.get_dtype()
        text_prompts = [(e, m) for e, m, _ in extract_text_prompts(cond, device, dtype)]
        res = model.model.diffusion_model.forward_video(
            images=frames[..., :3].movedim(-1, 1), initial_masks=None, pbar=None, text_prompts=text_prompts,
            new_det_thresh=float(threshold), max_objects=0, detect_interval=1, target_device=device,
            target_dtype=dtype)
        packed = res.get("packed_masks")
        n = int(frames.shape[0])
        if packed is None:
            LOG.warning("%s SAM3 found nothing for '%s' (threshold %.3g): the whole depth is black", _TAG, prompt,
                        float(threshold))
            return torch.zeros((n, 1, 1), dtype=torch.bool)
        union = packed[:, 0].clone()   # = SAM3_TrackToMask with all objects
        for i in range(1, packed.shape[1]):
            union |= packed[:, i]
        masks = unpack_masks(union).cpu()
        LOG.info("%s SAM3 '%s': %d object(s) tracked over %d frames (threshold %.3g)", _TAG, prompt,
                 int(packed.shape[1]), n, float(threshold))
        return masks


SAM3_FILL, SAM3_GROW, SAM3_BLUR = 20, 2, 2   # 2026-10-28 holefill: tight silhouette. fill = largest ENCLOSED hole
#   (px of the source video, as a diameter) that is filled; open gaps (between the legs, arm and body) are never
#   bridged - a wider silhouette than the body left a translucent ghost on the floor.
SAM3_OUTPUTS = ("silhouette", "masked_depth")   # 2026-10-28 silhouette: what feeds the ControlNet AND <Video N>


def _dilate(m, r: int):
    import torch.nn.functional as F
    return F.max_pool2d(m, 2 * r + 1, stride=1, padding=r) if r > 0 else m


def _gauss(m, r: int):
    """Separable gaussian blur, radius r (sigma r/2), on [B,1,h,w]."""
    import torch
    import torch.nn.functional as F
    if r <= 0:
        return m
    x = torch.arange(-r, r + 1, dtype=m.dtype, device=m.device)
    k = torch.exp(-0.5 * (x / max(r / 2.0, 1e-3)) ** 2)
    k = k / k.sum()
    m = F.conv2d(F.pad(m, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1))
    return F.conv2d(F.pad(m, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1))


def _fill_holes(m, fill: int):
    """Fill only ENCLOSED holes (background regions not connected to the frame border) up to ~fill px across.
    Open gaps between limbs / legs stay background (unlike a closing, which bridges them)."""
    import numpy as np
    import torch
    try:
        from scipy import ndimage
    except Exception:
        LOG.warning("%s scipy not available: SAM3 hole fill skipped", _TAG)
        return m
    a = (m[:, 0] > 0.5).cpu().numpy()
    max_area = float(np.pi * (fill / 2.0) ** 2) if fill > 0 else 0.0
    out = a.copy()
    for i in range(a.shape[0]):
        holes = ndimage.binary_fill_holes(a[i]) & ~a[i]
        if not holes.any():
            continue
        lab, n = ndimage.label(holes)
        sizes = ndimage.sum(holes, lab, range(1, n + 1))
        keep = np.isin(lab, [j + 1 for j, sz in enumerate(sizes) if sz <= max_area])
        out[i] |= keep
    return torch.from_numpy(out).to(m.device, m.dtype).unsqueeze(1)


def refine_mask(m, fill: int = 0, grow: int = 0, blur: int = 0):
    """m float [B,1,h,w] in 0..1 -> fill enclosed holes (<= ~fill px), grow (dilate), feather (gaussian); px of m."""
    if fill > 0:
        m = _fill_holes(m, fill)
    m = _dilate(m, grow)
    return _gauss(m, blur).clamp(0.0, 1.0)


def apply_sam3_mask(depth, masks, chunk: int = 32, fill: int = 0, grow: int = 0, blur: int = 0, src_h: int = 0,
                    output: str = "masked_depth"):
    """Black (0) every depth pixel outside the SAM3 mask.  depth [N, h, w, C] float, masks bool [N, Hm, Wm].
    2026-10-28 samrefine: the mask is first cleaned like the author's workflow - fill gaps, grow, gaussian feather
    (fill / grow / blur in px of the SOURCE video, src_h = its height; scaled to the depth size) - so no background
    depth leaks through holes or ragged edges (that leak is what turns the background into the grey source room)."""
    import torch
    import torch.nn.functional as F
    n, h, w = int(depth.shape[0]), int(depth.shape[1]), int(depth.shape[2])
    if int(masks.shape[0]) != n:
        raise RuntimeError(f"{_TAG} SAM3 mask frames {int(masks.shape[0])} != depth frames {n}")
    sc = (h / float(src_h)) if src_h else 1.0
    f, g, b = (int(round(max(0, v) * sc)) for v in (fill, grow, blur))
    out = depth.clone()
    for a in range(0, n, chunk):
        m = masks[a:a + chunk].unsqueeze(1).float()
        m = (F.interpolate(m, size=(h, w), mode="bilinear", align_corners=False) > 0.5).float()
        m = refine_mask(m, f, g, b)[:, 0]
        if output == "silhouette":   # author's route: refined mask -> white figure on black, no depth / no texture
            out[a:a + chunk] = m.unsqueeze(-1).to(out.dtype).expand(-1, -1, -1, int(out.shape[-1]))
        else:
            out[a:a + chunk] = out[a:a + chunk] * m.unsqueeze(-1).to(out.dtype)
    return out


class LoopDepth:
    """Per-run state of the depth feature inside the H3MD Loop.  Inactive (dc None / no depth segment) = no-op."""

    def __init__(self, dc, plan: dict, indices=None, loaders=None):
        if dc is not None and not (isinstance(dc, dict) and dc.get("type") == DEPTH_TYPE):
            raise ValueError(f"{_TAG} depth_control must come from H3MD Depth Control")
        self.dc = dc
        self.uses = bool(dc is not None and plan_uses_depth(plan))
        self.prev_tail = None   # last frames of the previous segment's control (CPU)
        ld = loaders or {}
        self._run_depth = ld.get("run_depth", run_depth)
        self._run_pose = ld.get("run_pose", run_pose)
        self.kind = control_kind(dc)
        self._load_patch = ld.get("load_patch", load_control_patch)
        self._apply = ld.get("apply", apply_control)
        self._release = ld.get("release", _default_release)
        self._fit = ld.get("fit", ref_fit_frames)
        self._load_video = ld.get("load_video")
        self._run_sam3 = ld.get("run_sam3", run_sam3)
        self.loads = {"depth": 0, "patch": 0}
        self.last_report = None
        if dc is None and plan_uses_depth(plan):
            LOG.warning("%s the plan has depth videos but no H3MD Depth Control is connected: depth ignored "
                        "(raw video reference as before)", _TAG)
        if self.uses and any(sam3_prompt(r) for s in plan.get("segments", []) for r in (s.get("videos") or [])
                             if isinstance(r, dict) and r.get("depth")):
            LOG.info("%s SAM3 (threshold %.3g): depth videos with SAM3 ticked + a prompt are masked (outside = black); "
                     "the others keep the full depth (SAM3 not loaded for them)", _TAG, float(dc["sam3_threshold"]))

    def cache_settings(self):
        return depth_settings(self.dc) if self.uses else None

    def segment_cache_settings(self, plan: dict, k: int):
        """Per-segment cache-key part: only for a segment that has a depth video while depth control is connected
        (None otherwise -> that segment keeps exactly its old key)."""
        ref = segment_depth_ref(plan["segments"][k]) if self.uses else None
        if ref is None:
            return None
        out = dict(depth_settings(self.dc), route=ROUTE)
        if self.kind == "depth" and sam3_active(self.dc, ref):   # SAM3-masked segment only (its prompt is in the segment signature)
            out["sam3_mask"] = SAM3_MASK
            r = tuple(int(self.dc.get(k, 0)) for k in ("sam3_fill", "sam3_grow", "sam3_blur"))
            if any(r):
                out["sam3_refine"] = list(r)
            so = str(self.dc.get("sam3_output", "masked_depth"))
            if so != "masked_depth":
                out["sam3_output"] = so
        return out

    def prepare(self, enc_plan: dict, k: int, rw_info, where: str, continued=None):
        """Before encode.  Returns None (no control for this segment) or a state dict; runs depth + releases it."""
        if not self.uses:
            return None
        seg = enc_plan["segments"][k]
        ref = segment_depth_ref(seg)
        if ref is None:
            self.prev_tail = None
            LOG.info("%s %s: route = REFERENCE (depth off for this segment): videos encoded as before, no depth "
                     "model, no ControlNet", _TAG, where)
            return None
        al = control_alignment(seg, rw_info, ref["id"], continued)
        if al["mode"] == "off":
            LOG.warning("%s %s: depth disabled for this segment (%s); video '%s' used %s", _TAG, where, al["why"],
                        ref["id"], "as a plain reference" if ref_on(ref) else "not at all")
            self.prev_tail = None
            return None
        try:
            from . import h3md_director as D
        except ImportError:  # flat import (tests)
            import h3md_director as D
        return self._prepare(enc_plan, k, seg, ref, al, where, D)

    def _prepare(self, enc_plan, k, seg, ref, al, where, D):
        info = enc_plan["media"]["videos"][ref["id"]]
        src_dur = D._num(info.get("duration"))
        origin = D.video_window_origin(ref, seg)   # H3MD oneshot: same origin as pick_segment_media (output frame 0)
        dur = seg["length"] / FPS if src_dur is None else max(0.0, src_dur - origin)
        need = window_frames_needed(al["mode"], seg["length"], al["overlap"], True)   # depth = <Video N>: full window
        load_video = self._load_video or D.load_video_window
        frames = load_video(info["file"], origin, dur, max_frames=need)   # raw window, no crop
        native = (int(frames.shape[2]), int(frames.shape[1]))
        masks, sam3 = None, None
        if self.kind == "pose":
            opts = pose_options(self.dc)
            self.loads["pose"] = self.loads.get("pose", 0) + 1
            if sam3_active(self.dc, ref):
                LOG.info("%s %s: pose mode: SAM3 prompt ignored (person selection comes in a later version)", _TAG,
                         where)
            depth = self._run_pose(frames, opts)
            del frames
            gc.collect()
            LOG.info("%s %s: pose skeleton of '%s' %d frames %dx%d (%s, hands %s, face %s, feet %s), %s", _TAG, where,
                     ref["id"], depth.shape[0], native[0], native[1], opts["engine"], opts["hands"], opts["face"],
                     opts["feet"], al["why"])
            return self._finish_prepare(enc_plan, seg, ref, al, where, D, depth, native, None)
        if sam3_active(self.dc, ref):   # SAM3 on the SAME raw window frames, unloaded before the depth model loads
            sam3 = {"prompt": sam3_prompt(ref), "threshold": float(self.dc["sam3_threshold"])}
            self.loads["sam3"] = self.loads.get("sam3", 0) + 1
            masks = self._run_sam3(frames, sam3["prompt"], sam3["threshold"])
        self.loads["depth"] += 1
        depth = self._run_depth(frames, self.dc["depth_model"], self.dc["depth_resolution"])
        del frames
        gc.collect()
        if masks is not None:
            mf, mg, mb = (int(self.dc.get(k, d)) for k, d in (("sam3_fill", 0), ("sam3_grow", 0), ("sam3_blur", 0)))
            so = str(self.dc.get("sam3_output", "masked_depth"))
            depth = apply_sam3_mask(depth, masks, fill=mf, grow=mg, blur=mb, src_h=native[1], output=so)
            sam3["refine"] = (mf, mg, mb, so)
            del masks
            gc.collect()
            LOG.info("%s %s: SAM3 mask '%s' (threshold %.3g, fill/grow/blur %s px) applied to the depth of '%s': "
                     "outside = black", _TAG, where, sam3["prompt"], sam3["threshold"], sam3["refine"], ref["id"])
        LOG.info("%s %s: depth of '%s' %d frames %dx%d -> %s (%s), %s", _TAG, where, ref["id"], depth.shape[0],
                 native[0], native[1], f"{depth.shape[2]}x{depth.shape[1]}", self.dc["depth_model"], al["why"])
        return self._finish_prepare(enc_plan, seg, ref, al, where, D, depth, native, sam3)

    def _finish_prepare(self, enc_plan, seg, ref, al, where, D, depth, native, sam3):
        # depth-only route: the depth frames ALWAYS replace the raw video as its <Video N> (ref flag on or off), at
        # the raw window's native size, so the reference path fits them exactly like it fitted the raw video
        # (apply_ref_video_height + core _resize "disabled") = the same full-frame stretch as the ControlNet copy
        drop = D.depth_control_only(seg, ref, enc_plan["media"])   # 2026-10-30 ctlonly: automatic, no switch
        if not drop:
            LOG.warning("%s %s: depth video '%s' also supplies the soundtrack <Audio>, so it stays a <Video N> reference "
                        "(turn its soundtrack off to use the depth for the ControlNet only)", _TAG, where, ref["id"])
        if drop:
            override = None
            LOG.info("%s %s: route = CONTROLNET ONLY: depth of '%s' drives the ControlNet; NO <Video> reference for it",
                     _TAG, where, ref["id"])
        else:
            override = resize_frames(depth, native[0], native[1])
            LOG.info("%s %s: route = DEPTH ONLY: depth of '%s' -> ControlNet + <Video> reference (%d frames %dx%d); raw "
                     "video NOT encoded (window / duration only)%s", _TAG, where, ref["id"], override.shape[0], native[0],
                     native[1], "" if ref_on(ref) else ", ref off -> slot added for the depth video")
        st = {"ref_id": ref["id"], "ref_on": ref_on(ref), "kind": self.kind, "depth": depth, "override": override, "drop_video": drop,
              "mode": al["mode"], "overlap": al["overlap"], "native": native}
        if sam3 is not None:
            st["sam3"] = sam3
        return st

    def build_control(self, st: dict, latent_frames: int, low_hw, spatial: int = 16):
        """Control video [N, h*16, w*16, 3] (CPU) for the low-res canvas, fitted like the reference video
        (full frame stretched edge to edge, no letterbox, no crop)."""
        import torch
        n = control_frame_count(latent_frames)
        cw, ch = int(low_hw[1]) * spatial, int(low_hw[0]) * spatial
        ov = st["overlap"]
        has_prev = st["mode"] == "continue" and self.prev_tail is not None and self.prev_tail.shape[0] >= ov \
            and tuple(self.prev_tail.shape[1:3]) == (ch, cw)
        idx = align_indices(st["mode"], n, ov, st["depth"].shape[0], has_prev)
        used = sorted({j for s, j in idx if s == "win"})
        lb = self._fit(st["depth"][used], cw, ch)
        if tuple(lb.shape[1:3]) != (ch, cw):
            raise RuntimeError(f"{_TAG} control fit returned {tuple(lb.shape[1:3])}, expected {(ch, cw)}")
        pos = {j: p for p, j in enumerate(used)}
        out = torch.empty((n, ch, cw, 3), dtype=torch.float32)
        prev = self.prev_tail[-ov:] if has_prev else None
        for i, (s, j) in enumerate(idx):
            out[i] = prev[j] if s == "prev" else lb[pos[j], ..., :3]
        st["geometry"] = {"canvas": [cw, ch], "content": [cw, ch], "offset": [0, 0], "fit": FIT_MODE,
                          "source": [int(st["depth"].shape[2]), int(st["depth"].shape[1])],
                          "frames": n, "prev_tail": bool(has_prev)}
        return out

    def attach(self, model, st: dict, latent: dict, low_hw, where: str):
        """After encode/continue: build the control, load the int8 patch, patch a CLONE of the low-res model.
        -> (low-res model to sample with, ControlHandle or None)."""
        dc = self.dc
        samples = latent["samples"]
        v = samples.unbind()[0] if getattr(samples, "is_nested", False) else samples
        vae = dc["vae"]
        spatial = int(vae.spacial_compression_encode()) if hasattr(vae, "spacial_compression_encode") else 16
        control = self.build_control(st, int(v.shape[2]), low_hw, spatial)
        st["control_tail"] = control
        st["depth"] = None   # the depth window is no longer needed (CPU RAM)
        LOG.info("%s %s: control %s from depth %s (fit = reference path: core _resize lanczos, crop disabled, full "
                 "frame edge to edge, no letterbox)%s", _TAG, where, st["geometry"]["canvas"], st["geometry"]["source"],
                 ", overlap from the previous control" if st["geometry"]["prev_tail"] else "")
        if float(dc["strength"]) == 0.0:
            LOG.info("%s %s: strength 0 -> no ControlNet patch loaded", _TAG, where)
            return model, None
        self.loads["patch"] += 1
        model_patch = self._load_patch(dc["control_model"])
        try:
            patched, patch = self._apply(model, model_patch, vae, control, dc["strength"], dc["start_percent"],
                                         dc["end_percent"])
        except BaseException:
            self._release(model_patch, "(apply failed)")
            raise
        LOG.info("%s %s: ControlNet '%s' strength %.3g, %.3g-%.3g on the low-res stage only", _TAG, where,
                 dc["control_model"], float(dc["strength"]), float(dc["start_percent"]), float(dc["end_percent"]))
        return patched, ControlHandle(patch, model_patch, self._release)

    def finish(self, st, handle, next_overlap: int):
        """After the segment (finally): release the patch if still loaded; keep only the next overlap's tail."""
        if handle is not None:
            handle.release("(segment end)")
        self.last_report = None if not st else {"video": st.get("ref_id"), "ref": st.get("ref_on"),
                                                "route": ROUTE, "kind": self.kind,
                                                "video_input": "none" if st.get("drop_video") else "depth",
                                                "align": st.get("mode"), "overlap": st.get("overlap"),
                                                "control": st.get("geometry"), "patched": handle is not None}
        if st and st.get("sam3"):
            self.last_report["sam3"] = dict(st["sam3"])
        ctl = st.get("control_tail") if st else None
        self.prev_tail = ctl[-next_overlap:].clone() if (ctl is not None and next_overlap > 0
                                                          and ctl.shape[0] >= next_overlap) else None
        if st:
            st.clear()
        gc.collect()

    def reset_tail(self):
        self.prev_tail = None
        self.last_report = None


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

def _patch_names():
    try:
        import folder_paths
        names = list(folder_paths.get_filename_list("model_patches"))
    except Exception:
        names = []
    return names or [DEFAULT_CONTROL]


class H3MDDepthControl:
    @classmethod
    def INPUT_TYPES(cls):
        names = _patch_names()
        return {"required": {
            "control_model": (names, {"default": DEFAULT_CONTROL if DEFAULT_CONTROL in names else names[0],
                              "tooltip": "MiniMax H3 Fun ControlNet file in models/model_patches (int8). Loaded by the "
                                         "Loop only for segments with a depth video, released after their low-res stage."}),
            "vae": ("VAE", {"tooltip": "Video VAE used by core's Fun ControlNet to encode the control video "
                                       "(passed through as a reference, nothing is loaded here)."}),
            "strength": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 10.0, "step": 0.01}),
            "start_percent": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.001}),
            "end_percent": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 1.0, "step": 0.001}),
            "depth_model": (list(DEPTH_MODELS), {"default": DEFAULT_DEPTH,
                            "tooltip": "Depth Anything V2 checkpoint (comfyui_controlnet_aux). Loaded per depth "
                                       "segment, unloaded right after."}),
            "depth_resolution": ("INT", {"default": 512, "min": 64, "max": 2048, "step": 64,
                                 "tooltip": "Depth inference short side (aux keeps the aspect, no crop)."}),
            "sam3_enabled": ("BOOLEAN", {"default": False,
                             "tooltip": "SAM3 mask on depth videos that have a SAM3 prompt (Director editor): only the "
                                        "prompted objects keep their depth, the rest is black. Empty prompt = full "
                                        "depth. The SAM3 prompt never enters the H3 prompt."}),
            "sam3_threshold": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.01,
                               "tooltip": "SAM3 detection score threshold (core SAM3_VideoTrack detection_threshold)."}),
        }, "optional": {
            "sam3_fill": ("INT", {"default": SAM3_FILL, "min": 0, "max": 256, "step": 1,
                          "tooltip": "SAM3 mask: fill enclosed holes up to this size (px of the source video). Open gaps "
                                     "between legs / arm and body are never filled."}),
            "sam3_grow": ("INT", {"default": SAM3_GROW, "min": 0, "max": 256, "step": 1,
                          "tooltip": "SAM3 mask: grow outward (px of the source video). Keep small: a silhouette wider "
                                     "than the body leaves a translucent ghost (e.g. on the floor). Default 2."}),
            "sam3_blur": ("INT", {"default": SAM3_BLUR, "min": 0, "max": 256, "step": 1,
                          "tooltip": "SAM3 mask: gaussian feather radius (px of the source video). Default 2."}),
            "sam3_output": (list(SAM3_OUTPUTS), {"default": "silhouette",
                            "tooltip": "With SAM3: silhouette = the cleaned mask as a white figure on black (author's "
                                       "workflow) feeds the ControlNet AND <Video N> - only shape and motion, no depth "
                                       "shading, so the reference video carries no picture at all. masked_depth = the "
                                       "depth inside the mask (grey figure)."}),
        }}

    RETURN_TYPES = (DEPTH_TYPE,)
    RETURN_NAMES = ("depth_control",)
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Settings for the H3MD Loop's depth ControlNet (Depth Anything V2 + MiniMax H3 Fun ControlNet int8) "
                   "on the low-res stage. Holds no model: the Loop loads the depth model and the patch only for "
                   "segments with a depth video. Tick 'depth' on a video in the Director editor.")

    def build(self, control_model, vae, strength=0.7, start_percent=0.0, end_percent=0.6,
              depth_model=DEFAULT_DEPTH, depth_resolution=512, sam3_enabled=False, sam3_threshold=0.3,
              sam3_fill=SAM3_FILL, sam3_grow=SAM3_GROW, sam3_blur=SAM3_BLUR, sam3_output="silhouette",
              control_kind="depth", pose_engine="sdpose", pose_hands=True, pose_face=False, pose_feet=False):
        if float(start_percent) > float(end_percent):
            raise ValueError(f"{_TAG} start_percent {start_percent} > end_percent {end_percent}")
        if depth_model not in DEPTH_MODELS:
            raise ValueError(f"{_TAG} depth_model must be one of {DEPTH_MODELS} (Depth Anything V2)")
        return ({"type": DEPTH_TYPE, "version": 1, "control_model": str(control_model), "vae": vae,
                 "strength": float(strength), "start_percent": float(start_percent), "end_percent": float(end_percent),
                 "depth_model": str(depth_model), "depth_resolution": int(depth_resolution),
                 "sam3_enabled": bool(sam3_enabled), "sam3_threshold": float(sam3_threshold),
                 "sam3_fill": int(sam3_fill), "sam3_grow": int(sam3_grow), "sam3_blur": int(sam3_blur),
                 "sam3_output": str(sam3_output) if sam3_output in SAM3_OUTPUTS else "silhouette",
                 "control_kind": control_kind if control_kind in CONTROL_KINDS else "depth",
                 "pose_engine": pose_engine if pose_engine in POSE_ENGINES else "sdpose",
                 "pose_hands": bool(pose_hands), "pose_face": bool(pose_face), "pose_feet": bool(pose_feet)},)


DEFAULT_CONTROL_V2 = "minimax_h3_fun_controlnet_union_2.0_pruned_int8_convrot.safetensors"


class H3MDMotionControl(H3MDDepthControl):
    """2026-11-04 (0.3.4 pose-exp): a NEW node next to H3MD Depth Control (which stays exactly as in 0.3.3).
    Same output type, so it plugs into the Loop's depth_control input; adds control_kind depth / pose."""

    @classmethod
    def INPUT_TYPES(cls):
        spec = H3MDDepthControl.INPUT_TYPES()
        names = _patch_names()
        cm = spec["required"]["control_model"]
        spec["required"]["control_model"] = (cm[0], dict(cm[1], default=DEFAULT_CONTROL_V2 if DEFAULT_CONTROL_V2 in names
                                                          else cm[1]["default"]))
        req = {"control_kind": (list(CONTROL_KINDS), {"default": "pose",
                                "tooltip": "pose = skeleton (body motion only, any character / outfit; Fun ControlNet "
                                           "2.0 recommended). depth = Depth Anything V2, same as H3MD Depth Control."}),
               "pose_engine": (list(POSE_ENGINES), {"default": "sdpose",
                               "tooltip": "pose only. sdpose = ComfyUI core SDPose wholebody (models/checkpoints/"
                                          "sdpose_wholebody_fp16.safetensors). dwpose = comfyui_controlnet_aux DWPose "
                                          "(yolox_l.onnx + dw-ll_ucoco_384_bs5.torchscript.pt; uses depth_resolution)."}),
               "pose_hands": ("BOOLEAN", {"default": True, "tooltip": "pose only: draw hands / fingers."}),
               "pose_face": ("BOOLEAN", {"default": False,
                             "tooltip": "pose only: draw face points (off: the prompt / reference decides the face)."}),
               "pose_feet": ("BOOLEAN", {"default": False, "tooltip": "pose only (sdpose): draw feet points."})}
        r = spec["required"]
        spec["required"] = {k: r[k] for k in ("control_model", "vae", "strength", "start_percent", "end_percent")}
        spec["required"].update(req)
        spec["required"].update({k: v for k, v in r.items() if k not in spec["required"]})
        return spec

    RETURN_NAMES = ("motion_control",)
    DESCRIPTION = ("H3MD Loop motion control: pose skeleton (SDPose / DWPose) or depth (Depth Anything V2) + MiniMax H3 "
                   "Fun ControlNet on the low-res stage. Connect to the Loop's depth_control input. Tick 'depth' on a "
                   "video in the Director editor to use it as the motion source.")


NODE_CLASS_MAPPINGS = {"H3MDDepthControl": H3MDDepthControl, "H3MDMotionControl": H3MDMotionControl}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDDepthControl": "H3MD Depth Control",
                              "H3MDMotionControl": "H3MD Motion Control (pose / depth)"}
