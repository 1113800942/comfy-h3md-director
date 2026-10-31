"""H3MD seam post-processing (own code): which decoded copy of a continuation overlap is kept, and an optional
bounded low-frequency tone match at continuation joins.

merge_overlap_source
  A continuation segment starts with `trim` overlap frames that are the same moments as the previous segment's
  last `trim` frames (same latent tokens under static_lock).  ComfyUI's H3 video VAE decodes in 7-token windows
  with a 5-token stride and blends each window with the previous one; the LAST frames of a clip are emitted
  without a following window, so the previous segment's decoded tail lacks future context.  (Idea from xmarre,
  MiniMax-H3-Flow-Aligned-Regenerate docs/CONTINUUM_DECODE_CONTEXT.md, Apache-2.0 - idea only, no code used.)
    'prev' = old behaviour: keep the previous segment's copy (drop the new segment's first `trim` frames).
    'next' = keep the NEXT segment's copy (decoded with the following frames as context) and drop the previous
             segment's last `trim` frames.  Frame count, audio and the joined latent are identical either way.
  Hard cuts (trim 0) are joined end to end in both modes.

seam_tone_match
  At continuation joins only, after decode: both copies of the overlap show the same moments, so the previous
  segment's (already corrected) overlap frames and the new segment's overlap frames are compared content-aligned,
  over the <= TONE_WINDOW frames nearest the visible seam, on a heavily blurred 16x16 low-frequency grid.  The
  per-cell median difference (robust to motion) is the measured delta (luma: one value; color: per RGB channel).
  strength * delta, clamped to +-MAX_SEAM_OFFSET, is added to the new segment at the visible seam and eases out
  (raised cosine) over TONE_EASE frames to an optional whole-segment luma offset (global * delta_luma, clamped to
  +-MAX_GLOBAL_OFFSET; default 0).  Measured and residual deltas are logged.  (Approach written from scratch;
  AICG3D's GPL _SequenceSeamMatcher was only read for the idea of bounded, eased seam correction.)
"""

from __future__ import annotations

import logging
import math

import torch

try:
    from . import h3md_director as D
except ImportError:  # flat import (tests)
    import h3md_director as D

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Seam]"

OVERLAP_SOURCES = ["next", "prev"]
TONE_MODES = ["off", "luma", "color"]
TONE_WINDOW = 12          # frames measured on each side (capped by the overlap)
TONE_EASE = 18            # frames over which the seam offset eases out
GRID = 16                 # low-frequency grid (cells per side)
MAX_SEAM_OFFSET = 0.04    # max |offset| per channel at the seam (~10/255)
MAX_GLOBAL_OFFSET = 0.015  # max |whole-segment luma offset| (~4/255)
MIN_DELTA = 0.002         # below this the seam is left untouched
BOUNDARY_WINDOW = 6       # static_lock: frames measured on each side of the kept -> generated boundary
_LUMA = (0.2126, 0.7152, 0.0722)
_CHUNK = 16


# ---------------------------------------------------------------------------
# merge_overlap_source
# ---------------------------------------------------------------------------

def keep_spans(frame_counts, trims, source: str = "prev"):
    """[(start, end)] of the decoded frames each segment contributes to the merged video.
    trims[k] = overlap of segment k with segment k-1 (trims[0] ignored: first segment in range is never trimmed)."""
    counts = [int(c) for c in frame_counts]
    starts, ends = [0] * len(counts), list(counts)
    for k in range(1, len(counts)):
        t = int(trims[k] or 0)
        if t <= 0:
            continue
        if t >= counts[k]:
            raise ValueError(f"H3MD Join: overlap {t} >= new segment frames {counts[k]}")
        if source == "next":
            ends[k - 1] -= t
            if ends[k - 1] <= starts[k - 1]:
                raise ValueError(f"H3MD Join: overlap {t} leaves no frames of segment {k} (merge_overlap_source=next)")
        else:
            starts[k] = t
    return list(zip(starts, ends))


def visible_start(trim: int, source: str) -> int:
    """Index (in the new segment) of its first frame that is visible in the merged video."""
    return 0 if source == "next" else max(0, int(trim or 0))


def join_frames(images, audio, trim, merged_images=None, merged_audio=None, crossfade_ms=0, source="prev"):
    """RAM-mode join.  'prev' == D.join_segments (bit-identical); 'next' drops the merged tail instead of the new
    head.  Audio is always joined by D.join_audio (unchanged)."""
    trim = max(0, int(trim or 0))
    if source != "next" or merged_images is None or trim == 0:
        return D.join_segments(images, audio, trim, merged_images, merged_audio, crossfade_ms)
    if tuple(images.shape[1:]) != tuple(merged_images.shape[1:]):
        raise ValueError(f"H3MD Join: frame size {tuple(images.shape[1:])} != merged {tuple(merged_images.shape[1:])}")
    if trim >= images.shape[0]:
        raise ValueError(f"H3MD Join: overlap {trim} >= new segment frames {images.shape[0]}")
    if trim >= merged_images.shape[0]:
        raise ValueError(f"H3MD Join: overlap {trim} >= merged frames {merged_images.shape[0]}")
    new = images.to(device=merged_images.device, dtype=merged_images.dtype)
    out = torch.cat([merged_images[:-trim], new], dim=0)
    out_audio = D.join_audio(audio, trim, merged_audio, crossfade_ms)
    return out, out_audio, int(out.shape[0])


# ---------------------------------------------------------------------------
# seam_tone_match
# ---------------------------------------------------------------------------

def lowfreq(frames) -> torch.Tensor:
    """[N,H,W,C] (torch float/uint8 or numpy) -> heavily blurred [N,3,g,g] float32 grid (area downsample + 3x3 box)."""
    if not isinstance(frames, torch.Tensor):  # numpy / memmap (disk cache)
        import numpy as np
        frames = torch.from_numpy(np.array(frames))
    x = frames
    if x.dtype == torch.uint8:
        x = x.float() / 255.0
    x = x[..., :3].detach().to("cpu", torch.float32).movedim(-1, 1)
    gh, gw = min(GRID, x.shape[-2]), min(GRID, x.shape[-1])
    out = []
    for i in range(0, x.shape[0], _CHUNK):
        g = torch.nn.functional.adaptive_avg_pool2d(x[i:i + _CHUNK], (gh, gw))
        g = torch.nn.functional.pad(g, (1, 1, 1, 1), mode="replicate")
        out.append(torch.nn.functional.avg_pool2d(g, 3, stride=1))
    return torch.cat(out, 0) if out else torch.zeros((0, 3, gh, gw))


def _luma(rgb: torch.Tensor) -> torch.Tensor:
    """[...,3,h,w] -> [...,h,w]"""
    w = torch.tensor(_LUMA, dtype=rgb.dtype).view(3, 1, 1)
    return (rgb * w).sum(dim=-3)


def measure(ref_grid: torch.Tensor, new_grid: torch.Tensor, mode: str) -> torch.Tensor:
    """Robust low-frequency delta (ref - new) -> [3] (luma mode: the same value in all three)."""
    d = ref_grid.mean(0) - new_grid.mean(0)            # [3,g,g], frame-averaged per cell
    if mode == "color":
        return d.flatten(1).median(dim=1).values
    y = _luma(d).flatten().median()
    return y.repeat(3)


def tail_grid(images, keep: int):
    """Low-frequency grids of the last `keep` frames (what the next continuation is compared with)."""
    keep = int(keep)
    if keep <= 0 or images is None or images.shape[0] == 0:
        return None
    return lowfreq(images[-keep:])


def apply_tone_match(images: torch.Tensor, prev_grid: torch.Tensor | None, trim: int, source: str, mode: str,
                     strength: float = 1.0, global_strength: float = 0.0, where: str = ""):
    """Bounded, eased low-frequency offset on the new continuation segment `images` (in place when possible).
    prev_grid = lowfreq of the previous segment's last frames (>= trim, as they appear in the output).
    Returns (images, info|None)."""
    trim = int(trim or 0)
    if mode not in ("luma", "color") or trim <= 0:
        return images, None
    if prev_grid is None or prev_grid.shape[0] < trim:
        LOG.info("%s %s: seam_tone_match skipped (previous segment frames not available)", _TAG, where)
        return images, None
    n = min(TONE_WINDOW, trim)
    if source == "next":   # visible seam = start of the overlap
        ref, new_sl = prev_grid[prev_grid.shape[0] - trim:prev_grid.shape[0] - trim + n], slice(0, n)
    else:                  # visible seam = end of the overlap
        ref, new_sl = prev_grid[-n:], slice(trim - n, trim)
    delta = measure(ref, lowfreq(images[new_sl]), mode)
    dy = float(_luma(delta.view(3, 1, 1)).flatten()[0])
    s = max(0.0, float(strength))
    seam_off = (delta * s).clamp(-MAX_SEAM_OFFSET, MAX_SEAM_OFFSET)
    g = max(-MAX_GLOBAL_OFFSET, min(MAX_GLOBAL_OFFSET, float(global_strength) * dy))
    glob = torch.full((3,), g)
    info = {"mode": mode, "window": n, "measured": [round(float(x), 4) for x in delta],
            "measured_luma": round(dy, 4), "seam_offset": [round(float(x), 4) for x in seam_off],
            "global_offset": round(g, 4), "ease": TONE_EASE}
    if float(delta.abs().max()) < MIN_DELTA and abs(g) < 1e-6:
        info["applied"] = False
        LOG.info("%s %s: seam_tone_match %s window %df measured dRGB %s (dY %+.4f) < %.3f: unchanged",
                 _TAG, where, mode, n, info["measured"], dy, MIN_DELTA)
        return images, info
    start = visible_start(trim, source)
    N = images.shape[0]
    idx = torch.arange(N, dtype=torch.float32) - start
    w = torch.where(idx < 0, torch.ones(()), 0.5 * (1.0 + torch.cos(math.pi * idx.clamp(min=0) / TONE_EASE)))
    w = torch.where(idx >= TONE_EASE, torch.zeros(()), w)
    offs = glob.view(1, 3) + (seam_off - glob).view(1, 3) * w.view(N, 1)     # [N,3]
    if not images.is_floating_point():
        images = images.float()
    for i in range(0, N, _CHUNK):
        o = offs[i:i + _CHUNK].to(device=images.device, dtype=images.dtype).view(-1, 1, 1, 3)
        try:
            images[i:i + _CHUNK, ..., :3].add_(o).clamp_(0.0, 1.0)
        except RuntimeError:  # e.g. a tensor that cannot be modified in place: fall back to a copy
            images = images.clone()
            images[i:i + _CHUNK, ..., :3].add_(o).clamp_(0.0, 1.0)
    residual = measure(ref, lowfreq(images[new_sl]), mode)
    info.update(applied=True, residual=[round(float(x), 4) for x in residual],
                residual_luma=round(float(_luma(residual.view(3, 1, 1)).flatten()[0]), 4))
    LOG.info("%s %s: seam_tone_match %s window %df (%s copy seam at frame %d): measured dRGB %s dY %+.4f -> offset %s "
             "easing over %df to global %+.4f; residual dRGB %s dY %+.4f", _TAG, where, mode, n, source, start,
             info["measured"], dy, info["seam_offset"], TONE_EASE, g, info["residual"], info["residual_luma"])
    return images, info


def apply_boundary_tone(images: torch.Tensor, trim: int, mode: str, strength: float = 1.0,
                        global_strength: float = 0.0, where: str = ""):
    """static_lock continuation: the overlap copies are the same latent, so comparing them measures ~0 (the old
    measurement).  The visible step is where the KEPT frames end and the GENERATED frames begin (frame `trim` of
    the new segment).  Measured on the same decode: the last BOUNDARY_WINDOW kept frames vs the first
    BOUNDARY_WINDOW generated frames (low-frequency grid, per-cell median), minus the content change the kept
    window's own trend predicts across the gap (bounded), so slow lighting / motion is not "corrected".
    The offset (strength * delta, clamped) is applied to the generated frames only, at full strength on frame
    `trim`, easing out (raised cosine) over TONE_EASE frames to the optional global offset; kept frames untouched."""
    trim = int(trim or 0)
    N = int(images.shape[0])
    n = min(BOUNDARY_WINDOW, trim, N - trim)
    if mode not in ("luma", "color") or trim <= 0 or n < 2:
        return images, None
    kept = lowfreq(images[trim - n:trim])
    new = lowfreq(images[trim:trim + n])
    raw = measure(kept, new, mode)                                  # kept - new
    h = n // 2
    trend = measure(kept[n - h:], kept[:h], mode) / float(n - h)    # per-frame change inside the kept window
    predicted = (trend * float(n)).clamp(-MAX_SEAM_OFFSET, MAX_SEAM_OFFSET)   # kept centre -> new centre
    delta = raw + predicted
    dy = float(_luma(delta.view(3, 1, 1)).flatten()[0])
    s = max(0.0, float(strength))
    seam_off = (delta * s).clamp(-MAX_SEAM_OFFSET, MAX_SEAM_OFFSET)
    g = max(-MAX_GLOBAL_OFFSET, min(MAX_GLOBAL_OFFSET, float(global_strength) * dy))
    glob = torch.full((3,), g)
    info = {"mode": mode, "measure": "boundary", "window": n, "measured_raw": [round(float(x), 4) for x in raw],
            "trend_comp": [round(float(x), 4) for x in predicted], "measured": [round(float(x), 4) for x in delta],
            "measured_luma": round(dy, 4), "seam_offset": [round(float(x), 4) for x in seam_off],
            "global_offset": round(g, 4), "ease": TONE_EASE, "boundary_frame": trim}
    if float(delta.abs().max()) < MIN_DELTA and abs(g) < 1e-6:
        info["applied"] = False
        LOG.info("%s %s: seam_tone_match %s (static_lock boundary, frame %d, window %df) measured dRGB %s "
                 "(raw %s, trend %s, dY %+.4f) < %.3f: unchanged", _TAG, where, mode, trim, n, info["measured"],
                 info["measured_raw"], info["trend_comp"], dy, MIN_DELTA)
        return images, info
    idx = torch.arange(N, dtype=torch.float32) - trim
    w = 0.5 * (1.0 + torch.cos(math.pi * idx.clamp(min=0) / TONE_EASE))
    w = torch.where(idx >= TONE_EASE, torch.zeros(()), w)
    offs = glob.view(1, 3) + (seam_off - glob).view(1, 3) * w.view(N, 1)     # [N,3]
    offs[:trim] = 0.0                                                          # kept frames untouched
    if not images.is_floating_point():
        images = images.float()
    for i in range(trim, N, _CHUNK):
        o = offs[i:i + _CHUNK].to(device=images.device, dtype=images.dtype).view(-1, 1, 1, 3)
        try:
            images[i:i + _CHUNK, ..., :3].add_(o).clamp_(0.0, 1.0)
        except RuntimeError:
            images = images.clone()
            images[i:i + _CHUNK, ..., :3].add_(o).clamp_(0.0, 1.0)
    residual = measure(kept, lowfreq(images[trim:trim + n]), mode) + predicted
    info.update(applied=True, residual=[round(float(x), 4) for x in residual],
                residual_luma=round(float(_luma(residual.view(3, 1, 1)).flatten()[0]), 4))
    LOG.info("%s %s: seam_tone_match %s (static_lock boundary, frame %d, window %df): measured dRGB %s (raw %s, "
             "trend %s) dY %+.4f -> offset %s on generated frames, easing over %df to global %+.4f; residual dRGB %s "
             "dY %+.4f", _TAG, where, mode, trim, n, info["measured"], info["measured_raw"], info["trend_comp"], dy,
             info["seam_offset"], TONE_EASE, g, info["residual"], info["residual_luma"])
    return images, info


# ---------------------------------------------------------------------------
# Loop Sampler glue (options + per-run state), so h3md_loop.py only needs one-line hooks
# ---------------------------------------------------------------------------

POST_DEFAULTS = {"merge_overlap_source": "next", "seam_tone_match": "off",
                 "seam_tone_strength": 1.0, "seam_tone_global": 0.0}


def post_input_types() -> dict:
    """Optional Loop Sampler inputs, appended at the END of the node inputs (old workflows load with defaults)."""
    return {
        "merge_overlap_source": (OVERLAP_SOURCES, {"default": "next",
                                 "tooltip": "Continuation joins: which decoded copy of the overlap frames is kept. next = the "
                                            "new segment's copy (decoded WITH following frames as VAE context; the previous "
                                            "segment's last frames are decoded without it); prev = old behaviour. Hard cuts, "
                                            "frame count, audio and the joined latent are unchanged."}),
        "seam_tone_match": (TONE_MODES, {"default": "off",
                            "tooltip": "Continuation joins, after decode: measure the low-frequency brightness (luma) or "
                                       "colour (color) difference of the two overlap copies near the seam and add a small, "
                                       "clamped offset to the new segment that eases out over ~18 frames. off = unchanged."}),
        "seam_tone_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05,
                               "tooltip": f"Fraction of the measured seam delta corrected (clamped to +-{MAX_SEAM_OFFSET})."}),
        "seam_tone_global": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                             "tooltip": "Optional whole-segment luma offset = this * measured luma delta (clamped to "
                                        f"+-{MAX_GLOBAL_OFFSET}); the seam offset eases to it instead of to 0."}),
    }


def post_options(kwargs: dict) -> dict:
    """Pop + validate our options from the Loop Sampler's **kwargs (so they never reach the sampler)."""
    opts = {k: kwargs.pop(k, d) for k, d in POST_DEFAULTS.items()}
    if opts["merge_overlap_source"] not in OVERLAP_SOURCES:
        raise ValueError(f"{_TAG} merge_overlap_source must be one of {OVERLAP_SOURCES}")
    kwargs.pop("ref_window_mode", None)  # removed option (old prompts): reference windows always advance
    if opts["seam_tone_match"] not in TONE_MODES:
        raise ValueError(f"{_TAG} seam_tone_match must be one of {TONE_MODES}")
    opts["seam_tone_strength"] = max(0.0, min(1.0, float(opts["seam_tone_strength"])))
    opts["seam_tone_global"] = max(0.0, min(1.0, float(opts["seam_tone_global"])))
    LOG.info("%s merge_overlap_source=%s seam_tone_match=%s (strength %.2f, global %.2f)", _TAG,
             opts["merge_overlap_source"], opts["seam_tone_match"],
             opts["seam_tone_strength"], opts["seam_tone_global"])
    return opts


def cache_settings(opts: dict):
    """Part of the segment-cache settings hash that changes cached frames/refs (None when all defaults, so caches
    written before these options existed still resume).  merge_overlap_source only affects the merge -> excluded."""
    tone = opts["seam_tone_match"] != "off"
    return {"ref_window": "advance",
            "tone": [opts["seam_tone_match"], opts["seam_tone_strength"], opts["seam_tone_global"]] if tone else "off"}


class SeamPost:
    """Per-run seam_tone_match state: low-frequency grid of the previous segment's tail (as it appears in output)."""

    def __init__(self, opts: dict, plan: dict):
        self.opts = opts
        self.mode = opts["seam_tone_match"]
        self.source = opts["merge_overlap_source"]
        self.keep = max([int(s.get("overlapIn", 0) or 0) for s in plan["segments"]] + [0])
        self.prev_grid = None
        self.boundary = False   # set by the Loop (True: the overlap prefix is restored exactly -> measure at the boundary)

    @property
    def active(self) -> bool:
        return self.mode != "off" and self.keep > 0

    def tone(self, images, trim, where="", report: dict | None = None):
        """Correct `images` (new segment) at a continuation join, then remember its tail.  Hard cut: tail only."""
        if not self.active:
            return images
        if int(trim or 0) > 0 and self.boundary:
            images, info = apply_boundary_tone(images, trim, self.mode, self.opts["seam_tone_strength"],
                                               self.opts["seam_tone_global"], where)
            if info is not None and report is not None:
                report["tone"] = info
        elif int(trim or 0) > 0:
            images, info = apply_tone_match(images, self.prev_grid, trim, self.source, self.mode,
                                            self.opts["seam_tone_strength"], self.opts["seam_tone_global"], where)
            if info is not None and report is not None:
                report["tone"] = info
        self.prev_grid = tail_grid(images, self.keep)
        return images

    def remember(self, frames):
        """Resumed segment (cached frames, already corrected): only remember its tail."""
        if self.active:
            self.prev_grid = tail_grid(frames, self.keep)
