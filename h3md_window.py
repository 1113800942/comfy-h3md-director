"""H3MD window mode (experimental branch window-exp, 2026-11-07).

Window mode renders one whole shot in a single sampling run with the core MiniMax H3 context windows: every
denoising step goes over all windows and the overlaps are fused at every step, so there is no continuation seam.
H3MD adds three things on top of the core MiniMaxH3ContextWindows patch:

1. Guide video cropped per window.  Core passes reference videos whole to every window.  A timeline-aligned
   reference video (latent length == the target's, i.e. the motion-copy guide with useDuration = shot length) is
   cropped to the window's latent range (plus its soundtrack), so TARA matches window time t with guide time t.
   11-06/07 tests: without it every window re-copied the first seconds (repeated poses) and ran 2.5x slower.
2. The packed layout is rebuilt for the window before the model function runs (attention patches that wrap the
   model function, e.g. BFS / TARA temporal alignment, read payload["layout"] first).
3. Window prompts.  A segment may carry several prompts with start times (plan segment "windowPrompts").  The
   encoder tags each conditioning with its frame span (key SPAN_KEY); each window uses the prompt whose span holds
   the window's centre, instead of the core's even split by count.  Without it every window re-plays the whole
   action list of the one prompt (11-07 test: the hair-brush gesture came back every window).

Plan keys (Director passes them through; old plans are unchanged):
  "windowMode": true | {"length": 124, "overlap": 56, "freenoise": true, "cropGuide": true}
  segment "windowPrompts": [{"at": 0.0, "prompt": "..."}, {"at": 5.0, "prompt": "..."}]   (seconds from shot start)
Window mode turns autosplit off (one sampling run per shot); hard cuts and continuation segments stay as before.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Window]"
_KEY = "h3md_window"
SPAN_KEY = "h3md_window_span"          # conditioning dict key: (start_frame, end_frame) in shot pixel frames
DEFAULTS = {"length": 124, "overlap": 56, "freenoise": True, "cropGuide": True}
FPS = 24


def _round_17(frames: int, lo: int) -> int:
    """Nearest 17n+5 >= lo."""
    n = max(0, round((int(frames) - 5) / 17))
    return max(lo, 5 + 17 * n)


def normalize_window_mode(value):
    """Plan 'windowMode' -> dict with DEFAULTS filled in, or None (off).  Lengths snap to 17n+5."""
    if value in (None, False, 0, "", "off"):
        return None
    out = dict(DEFAULTS)
    if isinstance(value, dict):
        for k in DEFAULTS:
            if k in value and value[k] is not None:
                out[k] = value[k]
    out["length"] = _round_17(out["length"], 22)
    out["overlap"] = min(_round_17(out["overlap"], 5), out["length"] - 17)
    out["freenoise"] = bool(out["freenoise"])
    out["cropGuide"] = bool(out["cropGuide"])
    return out


def normalize_window_prompts(value, length: int, resolve):
    """Segment 'windowPrompts' -> sorted [{"start": frame, "end": frame, "prompt": str}] or None.
    resolve(prompt) applies the plan's global prompt.  Entries outside the shot are dropped."""
    if not value:
        return None
    if not isinstance(value, list):
        raise ValueError("H3MD Director: windowPrompts must be a list of {at, prompt}")
    items = []
    for it in value:
        if not isinstance(it, dict) or not str(it.get("prompt", "")).strip():
            raise ValueError("H3MD Director: each windowPrompts entry needs 'at' (seconds) and 'prompt'")
        start = max(0, int(round(float(it.get("at", 0.0)) * FPS)))
        if start < length:
            items.append({"start": start, "prompt": resolve(str(it["prompt"]))})
    if not items:
        return None
    items.sort(key=lambda x: x["start"])
    items[0]["start"] = 0
    for a, b in zip(items, items[1:] + [None]):
        a["end"] = b["start"] if b else int(length)
    return items


# ------------------------------------------------------------------------------------------- per-window payload
def crop_refs(refs, window, total_t, audio_window, total_audio_t, stats):
    """refs with timeline-aligned videos cropped to the window (new list; inputs untouched)."""
    first, last = window.index_list[0], window.index_list[-1]
    out = []
    for blk in refs:
        kind = blk.get("kind")
        lat = blk.get("latent")
        if kind in ("video", "video_audio") and lat is not None and lat.shape[2] == total_t:
            nb = dict(blk)
            nb["latent"] = lat[:, :, first:last + 1]
            nb["latent_t"] = nb["latent"].shape[2]
            aud = blk.get("audio_latent")
            if aud is not None and audio_window is not None and aud.shape[-1] == total_audio_t:
                a0, a1 = audio_window.index_list[0], audio_window.index_list[-1] + 1
                nb["audio_latent"] = aud[..., a0:a1]
                nb["ref_audio_t"] = a1 - a0
            elif aud is not None:
                stats["audio_kept"] += 1
            stats["cropped"] += 1
            out.append(nb)
        else:
            if kind in ("video", "video_audio") and lat is not None:
                stats["skipped"].append(int(lat.shape[2]))
            out.append(blk)
    return out


def make_resize_callback(handler, crop_guide: bool, seen: set):
    import comfy.ldm.minimax.model as mm

    def resize(cond_key, cond_value, window, x_in, device, new_cond_item):
        if cond_key != "minimax_payload" or not crop_guide:
            return None
        payload = cond_value.cond
        refs = payload.get("refs")
        if not refs:
            return None
        model = handler._model
        if payload.get("keyframes"):
            new_payload = model._window_payload(payload, window)       # core: crop keyframes / locked audio
        else:
            new_payload = {k: v for k, v in payload.items() if k != "layout"}
        total_t = window.total_frames
        audio_window = window.modality_windows.get(1) if window.modality_windows else None
        total_audio_t = audio_window.total_frames if audio_window is not None else None
        stats = {"cropped": 0, "skipped": [], "audio_kept": 0}
        new_payload["refs"] = crop_refs(refs, window, total_t, audio_window, total_audio_t, stats)
        new_payload.update(model._cond_latents(new_payload.get("keyframes"), new_payload["refs"]))
        old = payload.get("layout")
        new_payload.pop("layout", None)
        if old is not None and audio_window is not None:
            text_len, _, lh, lw, _ = old.signature
            new_payload["layout"] = mm.PackedLayout(text_len, len(window.index_list), lh, lw,
                                                    len(audio_window.index_list),
                                                    keyframes=new_payload.get("keyframes"), refs=new_payload["refs"])
        sig = (window.index_list[0], window.index_list[-1], total_t)
        if sig not in seen:
            seen.add(sig)
            LOG.info("%s window latents %d-%d of %d: guide videos cropped %d, other videos kept %s%s", _TAG,
                     sig[0], sig[1], total_t, stats["cropped"], stats["skipped"] or "none",
                     f", {stats['audio_kept']} soundtrack(s) kept whole" if stats["audio_kept"] else "")
        return cond_value._copy_with(new_payload)
    return resize


# ------------------------------------------------------------------------------------------- window prompts
def window_pixel_span(window):
    """(first pixel frame, end pixel frame) of a window on the shot timeline."""
    from comfy.model_base import MiniMaxH3
    starts = MiniMaxH3._latent_pixel_starts(window.total_frames)
    return starts[window.index_list[0]], starts[window.index_list[-1] + 1]


def pick_span_cond(cond_in, window):
    """Index of the conditioning whose SPAN_KEY span holds the window centre (nearest span otherwise), or None
    when the conds carry no spans."""
    spans = [c.get(SPAN_KEY) for c in cond_in]
    if len(cond_in) < 2 or any(s is None for s in spans):
        return None
    a, b = window_pixel_span(window)
    mid = (a + b) / 2.0
    best, dist = 0, None
    for i, (s, e) in enumerate(spans):
        d = 0.0 if s <= mid < e else min(abs(mid - s), abs(mid - e))
        if dist is None or d < dist:
            best, dist = i, d
    return best


def make_overlap_monitor(handler):
    """Ghost check: how much neighbouring windows disagree on their shared latent frames, per step.  A window pair
    that still disagrees late in sampling is fused as a double exposure (11-07: two faces in one frame).  Logged
    per step; handler._h3md_overlap keeps [(sigma, [(pair, value), ...]), ...] for reports."""
    import torch
    state = {"sigma": None, "store": {}, "pairs": {}, "win": {}}
    handler._h3md_overlap = []

    def flush():
        if state["sigma"] is not None and state["pairs"]:
            vals = sorted((k, sum(v) / len(v)) for k, v in state["pairs"].items())
            handler._h3md_overlap.append((state["sigma"], vals))
            LOG.info("%s overlap disagreement at sigma %.3f: %s", _TAG, state["sigma"],
                     ", ".join(f"w{a + 1}-w{b + 1} {v:.3f}" for (a, b), v in vals))

    def combine(h, x_in, sub_conds_out, sub_conds, window, window_idx, total_windows, timestep, *rest):
        if not getattr(window, "modality_windows", None) or not sub_conds_out:
            return                                            # audio modality / nothing to compare
        sigma = float(timestep.flatten()[0])
        if sigma != state["sigma"]:
            flush()
            state.update(sigma=sigma, store={}, pairs={})
        out = sub_conds_out[0]
        for pos, idx in enumerate(window.index_list):
            cur = out.narrow(window.dim, pos, 1)
            prev = state["store"].get(idx)
            if prev is not None:
                pw, pt = prev
                d = (cur - pt).abs().mean() / ((cur.abs().mean() + pt.abs().mean()) / 2 + 1e-6)
                state["pairs"].setdefault((pw, window_idx), []).append(float(d))
            state["store"][idx] = (window_idx, cur.detach())
        if window_idx + 1 == total_windows:
            flush()
            state.update(sigma=None, store={}, pairs={})

    return combine


def install(handler, crop_guide: bool):
    """Turn a core IndexListContextHandler into the H3MD one (window prompts + guide crop)."""
    import comfy.context_windows as cw
    import comfy.patcher_extension as pe

    base = handler.__class__

    class H3MDWindowHandler(base):
        def get_resized_cond(self, cond_in, x_in, window, device=None):
            if cond_in is not None:
                k = pick_span_cond(cond_in, window)
                if k is not None:
                    key = (window.index_list[0], window.index_list[-1], k)
                    if key not in self._h3md_seen:
                        self._h3md_seen.add(key)
                        s, e = cond_in[k][SPAN_KEY]
                        LOG.info("%s window latents %d-%d: prompt %d of %d (frames %d-%d)", _TAG, key[0], key[1],
                                 k + 1, len(cond_in), s, e)
                    cond_in = [cond_in[k]]
            return super().get_resized_cond(cond_in, x_in, window, device)

    handler.__class__ = H3MDWindowHandler
    handler._h3md_seen = set()
    pe.add_callback_with_key(cw.IndexListCallbacks.COMBINE_CONTEXT_WINDOW_RESULTS, _KEY, make_overlap_monitor(handler),
                             handler.callbacks)
    if crop_guide:
        pe.add_callback_with_key(cw.IndexListCallbacks.RESIZE_COND_ITEM, _KEY,
                                 make_resize_callback(handler, True, set()), handler.callbacks)
        if not pe.get_all_callbacks(cw.IndexListCallbacks.RESIZE_COND_ITEM, handler.callbacks):
            raise RuntimeError(f"{_TAG} resize callback not registered")
    return handler


def apply(model, length=124, overlap=56, fuse_method="pyramid", freenoise=True, crop_guide=True):
    """Core MiniMax H3 context windows + H3MD additions on a clone of `model`."""
    from comfy_extras.nodes_context_windows import MiniMaxH3ContextWindowsNode
    res = MiniMaxH3ContextWindowsNode.execute(model, int(length), int(overlap), fuse_method, bool(freenoise), False)
    patched = res.result[0] if hasattr(res, "result") else res[0]
    install(patched.model_options["context_handler"], bool(crop_guide))
    return patched


def wrap_models(window_mode: dict, low, high):
    wm = normalize_window_mode(window_mode)
    if wm is None:
        return low, high
    LOG.info("%s window mode: windows %d / overlap %d frames, freenoise %s, crop guide %s", _TAG, wm["length"],
             wm["overlap"], wm["freenoise"], wm["cropGuide"])
    low = apply(low, wm["length"], wm["overlap"], "pyramid", wm["freenoise"], wm["cropGuide"])
    if high is not None:
        high = apply(high, wm["length"], wm["overlap"], "pyramid", wm["freenoise"], wm["cropGuide"])
    return low, high


class H3MDContextWindows:
    """Manual node: the same patch on one MODEL (for hand-built graphs; the Loop applies it itself in window mode)."""

    @classmethod
    def INPUT_TYPES(cls):
        import comfy.context_windows as cw
        return {"required": {
            "model": ("MODEL",),
            "context_length": ("INT", {"default": 124, "min": 22, "max": 4096, "step": 17,
                                       "tooltip": "Window length in real frames (17n+5)."}),
            "context_overlap": ("INT", {"default": 56, "min": 5, "max": 4096, "step": 17,
                                        "tooltip": "Overlap in real frames (17n+5). 56 removed the 11-07 ghosting case."}),
            "fuse_method": (cw.ContextFuseMethods.LIST_STATIC, {"default": cw.ContextFuseMethods.PYRAMID}),
            "freenoise": ("BOOLEAN", {"default": True}),
            "crop_guide_video": ("BOOLEAN", {"default": True,
                                             "tooltip": "Crop the timeline-aligned guide video (motion copy) to each window."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "run"
    CATEGORY = "H3MD/experimental"
    EXPERIMENTAL = True

    def run(self, model, context_length, context_overlap, fuse_method, freenoise, crop_guide_video):
        LOG.info("%s manual node: windows %d / overlap %d, %s, freenoise %s, crop guide %s", _TAG, context_length,
                 context_overlap, fuse_method, freenoise, crop_guide_video)
        return (apply(model, context_length, context_overlap, fuse_method, freenoise, crop_guide_video),)


NODE_CLASS_MAPPINGS = {"H3MDContextWindows": H3MDContextWindows}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDContextWindows": "H3MD Context Windows (window mode, exp)"}
