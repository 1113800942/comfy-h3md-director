"""H3MD high-res tiling (ContTile) for the H3MD two-pass sampler (h3md_sampler.py, Luna engine).

What it does: the high-res pass of every segment (first, hard cut, continued) runs through an OUTER_SAMPLE wrapper on
the high-res model clone.  plan_tiles() measures the free VRAM and picks the FEWEST strips whose (calibrated) memory
estimate fits - 1 strip = the plain full-frame pass, untouched.  With N > 1 strips the "joint" method runs ONE
full-frame sampling run, but every model evaluation is split into the strips (DIFFUSION_MODEL wrapper _joint_forward)
and the video predictions are blended with linear feathers over the overlaps before the sampler step, so every step
of every strip starts from the same full-frame state (neighbours included):
  * the continuation mask (kept prefix, drift, locked audio) is the sampler's own full-frame denoise mask, unchanged;
  * keyframe conditioning latents are cropped to the strip; references / text / audio stay whole; RoPE positions of
    the strip rows are the FULL frame's (core H3 normalises positions by sqrt(h*w));
  * audio is not predicted per strip: with N > 1 strips the audio part of the denoise mask is set to 0, so the
    audio stays at the clean audio Luna handed over from the low-res pass (its x0); with 1 strip Luna finishes the
    audio in the high-res pass as usual;
  * a PREPARE_SAMPLING wrapper makes ComfyUI reserve memory for the largest strip, not for the full frame.

Attribution: tile_spans follows the strip layout technique of nkxx188/ComfyUI-MiniMaxH3-Easy (nodes.py:
_segment_tile_axis), adapted here:

    MIT License - Copyright (c) 2026 nkxx188
    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
    associated documentation files (the "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
    following conditions: The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
    LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO
    EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
    IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE
    USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from __future__ import annotations

import inspect
import logging
import math
import time

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Tiling]"
WRAPPER_KEY = "h3md_hires_tiling"
GLOBAL_POSITIONS = True  # tile rows use the full frame's RoPE positions (False = standalone tile positions)
ROW_ELEMENTS = 96        # latent elements per packed H3 row (24 ch x 2 x 2 patch); used for memory estimates
AUTO_MAX_TILES = 12      # most strips the planner (and the out-of-memory retry) will use
FIT_MARGIN = 1.10        # without DynamicVRAM: an estimate up to 10% over the workspace still counts as fitting

# ---------------------------------------------------------------------------
# measured calibration (2026-10-17, Jack's RTX 5090 32 GB, ComfyUI DynamicVRAM (comfy-aimdo) on, sol_attn +
# chunked FF, 1472x1088 -> latent 68x92).  rows = packed rows of the largest strip; formula = ComfyUI
# memory_required (batch 1) for those rows; available = available_workspace() at plan time.  Only the high-res
# stage of a continued segment (or selflift's own tiled stage, same model call) is listed.
# ---------------------------------------------------------------------------
MEASURED_RUNS = (
    # name, frames, strips, rows, formula MiB, available MiB, ok
    ("short R6 full frame",         107, 1,  64365, 14088, 25181, True),
    ("short R3 joint 4W",           107, 4,  30637,  6706, 24235, True),
    ("Jack 396f selflift 8 (seg1)", 396, 8,  98440, 21547, 19722, True),
    ("Jack 396f selflift 8 (seg2)", 396, 8,  98440, 21547, 17716, True),
    ("Jack 396f seq 6 (alpha)",     396, 6,  85782, 18776, 19722, True),
    ("Jack 362f seq 7 (beta)",      362, 7,  78290, 17136, 17718, True),
    ("L1 362f joint 6",             362, 6,  81928, 17932, 19368, True),
    ("L3 362f joint 4W",            362, 4,  96480, 21118, 20172, True),
    ("L4 362f joint 2W",            362, 2, 132860, 29080, 19366, True),   # nvidia-smi peak 25802/32579 MiB
    # selflift untiled full frame, continued seg 3 (08:35): sol_attn asked 6.27 GiB on top of 18.98 GiB allocated
    # with 3.83 GiB free -> OOM.  available not logged there: the larger value of that run (19722) is used, which
    # makes the failure ratio smaller = the guard stricter.
    ("Jack 396f full frame OOM",    396, 1, 225012, 49251, 19722, False),
)
# measured need / formula: the OOM run needed >= (18.98 + 6.27) GiB torch memory for 225012 rows = 0.525 of the
# formula (everything allocated counted as activations -> an upper bound); rounded up.
CALIB_SCALE = 0.55
TABLE_TOLERANCE = 0.005  # never looser than the largest verified ratio (0.5% = rounding only)
FAIL_GUARD = 0.65        # ... and must stay below 65% of the smallest ratio that failed
EXTRAPOLATION_FACTOR = 0.90  # strips with more rows than any verified run: limit x0.9 (one tier safer)

TILING_AXES = ("auto", "width", "height")
TILING_INPUTS = {   # H3MD Sampler / Loop inputs (highres_tiling itself is defined by the sampler)
    "tiling_axis": (list(TILING_AXES), {"default": "auto",
                    "tooltip": "High-res tiling: split direction. auto = the longer side; width = left/right strips; "
                               "height = top/bottom strips. Audio and time are never split."}),
    "tiling_overlap": ("INT", {"default": 8, "min": 2, "max": 32, "step": 2,
                       "tooltip": "High-res tiling: overlap between neighbouring strips in latent pixels (x16 image "
                                  "pixels); the strip predictions are feather-blended over it."}),
}


def make_request(axis: str = "auto", overlap: int = 8) -> dict:
    axis = str(axis or "auto")
    if axis not in TILING_AXES:
        raise ValueError(f"{_TAG} tiling_axis must be one of {TILING_AXES}, got {axis!r}")
    overlap = int(overlap)
    if overlap < 0:
        raise ValueError(f"{_TAG} tiling_overlap must be >= 0")
    return {"axis": axis, "overlap": overlap}


def tile_spans(length: int, tiles: int, overlap: int):
    """Strips covering [0, length): list of (start, end, overlap_with_previous).

    Starts are even (aligned to the H3 2x2 patch grid), neighbours overlap by >= `overlap` (rounded up to
    even), every strip adds at least 2 new positions.  The count is reduced when the axis is too short."""
    L = int(length)
    n = max(1, int(tiles))
    ov = max(0, int(overlap))
    ov += ov % 2
    while n > 1:
        size = -(-(L + (n - 1) * ov) // n)
        size += size % 2
        room = (L - size) - (L - size) % 2  # even last start
        if size < L and size - ov >= 2 and room >= 2 * (n - 1):  # distinct even starts, new columns per strip
            break
        n -= 1
    if n <= 1:
        return [(0, L, 0)]
    last_start = L - size
    last_start -= last_start % 2
    starts = []
    for i in range(n):
        s = round(i * last_start / (n - 1))
        starts.append(s - s % 2)
    spans, prev_end = [], 0
    for i, s in enumerate(starts):
        e = L if i == n - 1 else min(L, s + size)
        spans.append((s, e, max(0, prev_end - s) if i else 0))
        prev_end = max(prev_end, e)
    for i in range(1, n):  # coverage / ordering invariants
        assert spans[i][0] <= spans[i - 1][1] and spans[i][1] > spans[i - 1][1], spans
    assert spans[0][0] == 0 and spans[-1][1] == L, spans
    return spans


def select_axis(video_shape, choice: str = "auto") -> int:
    if choice == "width":
        return 4
    if choice == "height":
        return 3
    return 3 if video_shape[3] > video_shape[4] else 4


def available_workspace(model_patcher) -> float:
    import comfy.model_management as mm
    dev = model_patcher.load_device
    free = float(mm.get_free_memory(dev))
    reclaim, seen = 0.0, set()
    for lm in mm.loaded_models():
        if lm is None:
            continue
        p = lm if callable(getattr(lm, "loaded_size", None)) else getattr(lm, "model", None)
        if p is None or getattr(p, "load_device", None) != dev or id(getattr(p, "model", p)) in seen:
            continue
        seen.add(id(getattr(p, "model", p)))
        try:
            reclaim += float(p.loaded_size())
        except Exception:
            pass
    pool = min(float(mm.get_total_memory(dev)), free + reclaim)
    weights = min(float(model_patcher.model_size()), pool * float(getattr(mm, "MIN_WEIGHT_MEMORY_RATIO", 0.4)))
    work = max(0.0, pool - weights - float(mm.minimum_inference_memory()))
    LOG.info("%s capacity: free=%.0f MiB reclaimable_weights=%.0f MiB weight_allowance=%.0f MiB workspace=%.0f MiB",
             _TAG, free / 2**20, reclaim / 2**20, weights / 2**20, work / 2**20)
    return work


def packed_rows(video_shape, audio_t: int, text_len: int, keyframe_tokens: int, ref_rows: int) -> int:
    """Rows of the packed H3 sequence for a (tile) video shape [B,C,T,h,w]."""
    hp, wp = (video_shape[3] + 1) // 2, (video_shape[4] + 1) // 2
    return int(text_len + ref_rows + 2 * audio_t + (video_shape[2] + keyframe_tokens) * hp * wp)


def cond_stats(conds):
    """(text_len, keyframe video tokens, ref rows) - max over cond dicts."""
    text_len = kf_tokens = ref_rows = 0
    groups = conds.values() if isinstance(conds, dict) else []
    for g in groups:
        for d in g or []:
            ca = d.get("cross_attn")
            if isinstance(ca, torch.Tensor):
                text_len = max(text_len, int(ca.shape[-2]))
            kt = sum(int(kf["latent"].shape[2]) for kf in (d.get("minimax_keyframes") or [])
                     if isinstance(kf.get("latent"), torch.Tensor))
            kf_tokens = max(kf_tokens, kt)
            rr = 0
            for r in d.get("minimax_refs") or []:
                lat = r.get("latent")
                if isinstance(lat, torch.Tensor) and lat.ndim >= 4:
                    t = lat.shape[2] if lat.ndim == 5 else 1
                    rr += int(t * ((lat.shape[-2] + 1) // 2) * ((lat.shape[-1] + 1) // 2))
                al = r.get("audio_latent")
                if isinstance(al, torch.Tensor):
                    rr += 2 * int(al.shape[-1])
            ref_rows = max(ref_rows, rr)
    return text_len, kf_tokens, ref_rows


def dynamic_vram_enabled() -> bool:
    try:
        import comfy.memory_management as cmm
        return bool(getattr(cmm, "aimdo_enabled", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# per-machine calibration (2026-10-29): every high-res run is recorded (estimate / available, ok or out of memory)
# in <ComfyUI user dir>/h3md/tiling_calibration.json, per GPU name and DynamicVRAM on/off.  A local out-of-memory
# run tightens the limit on that machine (FAIL_GUARD x its ratio), so a card / attention backend that needs more
# memory than the RTX 5090 table says learns it after one retry and plans more strips from then on.
# ---------------------------------------------------------------------------
LOCAL_FILE = "tiling_calibration.json"
LOCAL_MAX_RECORDS = 300
LOCAL_PATH = None   # tests may set a path


def local_path():
    if LOCAL_PATH:
        return LOCAL_PATH
    try:
        import os
        import folder_paths
        return os.path.join(folder_paths.get_user_directory(), "h3md", LOCAL_FILE)
    except Exception:
        return None


def device_name(device=None) -> str:
    try:
        if torch.cuda.is_available() and (device is None or getattr(device, "type", "cuda") == "cuda"):
            return torch.cuda.get_device_name(device)
    except Exception:
        pass
    return str(device or "cpu")


def local_records(device: str | None = None, dynamic: bool | None = None) -> list:
    import json
    p = local_path()
    try:
        with open(p, encoding="utf-8") as f:
            recs = json.load(f).get("runs", [])
    except Exception:
        return []
    return [r for r in recs if (device is None or r.get("device") == device)
            and (dynamic is None or bool(r.get("dynamic")) == bool(dynamic))]


def record_run(plan: dict, ok: bool, device: str):
    """Append one run (ratio = formula estimate / available workspace, unscaled) to the local calibration file."""
    import json
    import os
    p = local_path()
    if not p or not plan or not plan.get("available_mib"):
        return
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"runs": []}
        data.setdefault("runs", []).append({
            "device": device, "dynamic": bool(plan.get("dynamic_vram")), "ok": bool(ok),
            "tiles": int(plan.get("tiles", 1)), "rows": int(plan.get("rows_tile", 0)),
            "ratio": round(float(plan["est_tile_mib"]) / float(plan["available_mib"]), 4),
            "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        data["runs"] = data["runs"][-LOCAL_MAX_RECORDS:]
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, p)
    except Exception as exc:
        LOG.warning("%s could not write the local calibration file %s: %s", _TAG, p, exc)


def calibration(dynamic: bool | None = None, device: str | None = None) -> dict:
    """{'scale', 'limit', 'dynamic', 'max_ok', 'min_fail'}: a strip count is accepted when
    scale * formula <= limit * available.  With DynamicVRAM the limit comes from MEASURED_RUNS (largest verified
    scaled ratio + TABLE_TOLERANCE, capped by FAIL_GUARD x the smallest failed one) - i.e. never looser than what
    has really run; anything above it is rounded to the next (safer) strip count.  Without DynamicVRAM (not
    measured) the old rule: raw formula within FIT_MARGIN."""
    dynamic = dynamic_vram_enabled() if dynamic is None else bool(dynamic)
    ok = [CALIB_SCALE * r[4] / r[5] for r in MEASURED_RUNS if r[6]]
    bad = [CALIB_SCALE * r[4] / r[5] for r in MEASURED_RUNS if not r[6]]
    max_ok, min_fail = max(ok), (min(bad) if bad else float("inf"))
    max_rows = max(r[3] for r in MEASURED_RUNS if r[6])
    local_fail = [float(r["ratio"]) for r in local_records(device, dynamic) if not r.get("ok") and r.get("ratio")]
    if not dynamic:
        limit = FIT_MARGIN
        if local_fail:   # raw ratios (scale 1.0)
            limit = min(limit, FAIL_GUARD * min(local_fail))
        return {"scale": 1.0, "limit": limit, "dynamic": False, "max_ok": max_ok, "min_fail": min_fail,
                "max_rows": None, "local_fail": min(local_fail) if local_fail else None}
    limit = min(max_ok * (1 + TABLE_TOLERANCE), FAIL_GUARD * min_fail)
    if local_fail:
        limit = min(limit, FAIL_GUARD * CALIB_SCALE * min(local_fail))
    return {"scale": CALIB_SCALE, "limit": limit, "dynamic": True, "max_ok": max_ok, "min_fail": min_fail,
            "max_rows": max_rows, "local_fail": min(local_fail) if local_fail else None}


def limit_for(cal: dict, rows: int) -> float:
    """Measured-safe ratio for a strip of `rows` rows: rows beyond every verified run -> EXTRAPOLATION_FACTOR."""
    if cal.get("max_rows") and rows > cal["max_rows"] * (1 + TABLE_TOLERANCE):
        return cal["limit"] * EXTRAPOLATION_FACTOR
    return cal["limit"]


def nearest_measured(rows: int, ratio: float):
    """Closest MEASURED_RUNS entry (relative rows + scaled ratio distance), for the log."""
    def d(r):
        return abs(math.log(max(1, rows) / r[3])) + abs(ratio - CALIB_SCALE * r[4] / r[5])
    return min(MEASURED_RUNS, key=d)


def estimate_bytes(model_patcher, rows: int):
    """(minimum, preferred) ComfyUI memory_required for a packed sequence of `rows` rows."""
    elems = int(rows) * ROW_ELEMENTS
    mm_ = model_patcher.model
    return float(mm_.memory_required([1, 1, elems])), float(mm_.memory_required([2, 1, elems]))


def plan_tiles(model_patcher, video_shape, audio_t: int, conds, req: dict, available=None, dynamic=None,
               min_tiles: int = 1, device: str | None = None):
    """Fewest strips (>= min_tiles) whose calibrated estimate fits the free VRAM.  min_tiles > 1 is used by the
    out-of-memory retry; device selects this machine's local calibration records."""
    axis = select_axis(video_shape, req.get("axis", "auto"))
    length = int(video_shape[axis])
    text_len, kf_tokens, ref_rows = cond_stats(conds)
    cal = calibration(dynamic, device)

    def tile_rows(spans):
        big = max(e - s for s, e, _ in spans)
        shp = list(video_shape)
        shp[axis] = big
        return packed_rows(shp, audio_t, text_len, kf_tokens, ref_rows), tuple(shp)

    full_rows, _ = tile_rows([(0, length, 0)])
    full_min, _ = estimate_bytes(model_patcher, full_rows)
    if available is None:
        available = available_workspace(model_patcher)
    candidates = list(range(max(1, int(min_tiles)), AUTO_MAX_TILES + 1)) or [AUTO_MAX_TILES]
    def strict_for(rows):
        return limit_for(cal, rows) if cal["dynamic"] else 1.0

    tried = []
    for n in candidates:
        spans = tile_spans(length, n, req.get("overlap", 8))
        if tried and len(spans) == len(tried[-1][0]):
            continue  # axis too short for more strips
        rows, shp = tile_rows(spans)
        need_min, need_pref = estimate_bytes(model_patcher, rows)
        tried.append((spans, rows, shp, need_min, need_pref))
        if cal["scale"] * need_min <= available * strict_for(rows):
            break
    # auto: fewest strips whose calibrated estimate is inside the measured-safe limit; else the most strips (warned)
    # (without DynamicVRAM, old rule: smallest count that fits strictly, else the smallest within FIT_MARGIN)
    pick = next((t for t in tried if cal["scale"] * t[3] <= available * strict_for(t[1])), None) \
        or next((t for t in tried if cal["scale"] * t[3] <= available * limit_for(cal, t[1])), None) or tried[-1]
    spans, rows, shp, need_min, need_pref = pick
    need_cal = cal["scale"] * need_min
    lim = limit_for(cal, rows)
    ratio = need_cal / available if available > 0 else float("inf")
    near = nearest_measured(rows, ratio)
    plan = {"axis": axis, "axis_name": "H" if axis == 3 else "W", "spans": spans, "tiles": len(spans),
            "length": length, "max_tiles": len(tile_spans(length, AUTO_MAX_TILES, req.get("overlap", 8))),
            "largest_tile": shp, "rows_full": full_rows, "rows_tile": rows,
            "est_full_mib": full_min / 2**20, "est_tile_mib": need_min / 2**20, "est_tile_pref_mib": need_pref / 2**20,
            "est_tile_cal_mib": need_cal / 2**20, "calib_scale": cal["scale"], "calib_limit": lim,
            "dynamic_vram": cal["dynamic"], "nearest_measured": near[0],
            "available_mib": available / 2**20, "fits": need_cal <= available * lim,
            "fit_ratio": ratio, "fits_strict": need_cal <= available * strict_for(rows),
            "overlap": int(req.get("overlap", 8)), "min_tiles": int(min_tiles), "local_fail": cal.get("local_fail")}
    LOG.info("%s plan: min_tiles=%d axis=%s tiles=%d spans=%s overlap=%d largest_tile=%s rows %d/%d; "
             "formula tile=%.0f MiB (cfg batch %.0f) vs full frame=%.0f MiB; calibrated tile=%.0f MiB (x%.2f, "
             "DynamicVRAM %s) = %.2f of available %.0f MiB, safe limit %.2f (measured table) fits=%s; nearest measured "
             "run: %s", _TAG, plan["min_tiles"], plan["axis_name"], plan["tiles"],
             [(s, e) for s, e, _ in spans], plan["overlap"], shp, rows, full_rows,
             plan["est_tile_mib"], plan["est_tile_pref_mib"], plan["est_full_mib"], plan["est_tile_cal_mib"],
             cal["scale"], "on" if cal["dynamic"] else "off", ratio, plan["available_mib"], lim,
             plan["fits"], near[0])
    if not plan["fits"]:
        LOG.warning("%s tile estimate is %.0f%% of the available workspace (> the measured-safe %.0f%%); OOM is "
                    "likely even at %d strips (reduce resolution, segment length or reference media). "
                    "Rows per tile: %d, of which %d are fixed (text/refs/audio) and do not shrink with more tiles",
                    _TAG, 100 * ratio, 100 * lim, len(spans), rows, rows - shp[2] * ((shp[3] + 1) // 2) * ((shp[4] + 1) // 2))
    elif not plan["fits_strict"]:
        LOG.warning("%s tile estimate is %.1f%% of the available workspace: within the %.0f%% margin of this "
                    "pessimistic estimate, proceeding", _TAG, 100 * ratio, 100 * lim)
    return plan


def _make_layout(signature, payload):
    """PackedLayout as core's extra_conds builds it.  keyframes / refs are ALWAYS passed: other custom nodes
    wrap PackedLayout.__init__ as (self, text_len, ..., audio_t, *args, **kwargs) (e.g. T8 sol_attn's Morton
    observer), so a signature filter would silently drop them and the layout would have no reference /
    keyframe rows (core then fails in _embed_and_pack: cond rows vs ~img_update).  Only the optional
    frame_count is passed conditionally: when named, or when a **kwargs wrapper may forward it (retried
    without it if the constructor underneath rejects it)."""
    from comfy.ldm.minimax.model import PackedLayout
    kwargs = {"keyframes": payload.get("keyframes"), "refs": payload.get("refs")}
    params = inspect.signature(PackedLayout).parameters
    fc = payload.get("frame_count")
    if "frame_count" in params:
        return PackedLayout(*signature, frame_count=fc, **kwargs)
    if fc is not None and any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        try:
            return PackedLayout(*signature, frame_count=fc, **kwargs)
        except TypeError as exc:
            if "frame_count" not in str(exc):
                raise
    return PackedLayout(*signature, **kwargs)


def _cond_rows(payload) -> int:
    """Visual condition rows core packs from payload['cond_video_latents'] (2x2 patches, padded)."""
    n = 0
    for z in payload.get("cond_video_latents") or []:
        n += int(z.shape[2]) * ((int(z.shape[3]) + 1) // 2) * ((int(z.shape[4]) + 1) // 2)
    return n


def check_tile_layout(layout, payload):
    """Fail early and clearly if the tile layout does not match the payload core will pack against it."""
    base = payload.get("layout")
    if base is not None and getattr(base, "signature", None) == layout.signature:
        if [(b - a, k) for a, b, k in base.segments] != [(b - a, k) for a, b, k in layout.segments]:
            raise RuntimeError(f"{_TAG} tile layout segments {[(b - a, k) for a, b, k in layout.segments]} differ "
                               f"from the sampler's own layout {[(b - a, k) for a, b, k in base.segments]}")
    if payload.get("cond_video_latents"):
        want = _cond_rows(payload)
        have = int((~layout.img_update).sum())
        if want != have:
            raise RuntimeError(f"{_TAG} tile layout has {have} visual condition rows but the payload carries "
                               f"{want} (keyframes/refs not in the layout?)")
    return layout


def tile_layout_with_full_positions(payload, tile_sig, full_hw_padded, axis, start, end):
    """New PackedLayout for the tile whose position ids are the full frame's (cropped for video/cond rows)."""
    text_len, T, th, tw, audio_t = tile_sig
    Hp, Wp = full_hw_padded
    full = _make_layout((text_len, T, Hp, Wp, audio_t), payload)
    tile = _make_layout(tile_sig, payload)
    if len(full.segments) != len(tile.segments):
        raise RuntimeError(f"{_TAG} tile/full layouts differ in segment count")
    n_patch = (end - start + 1) // 2
    for (fa, fb, kind), (ta, tb, tkind) in zip(full.segments, tile.segments):
        if kind != tkind:
            raise RuntimeError(f"{_TAG} tile/full layout segment mismatch {kind} != {tkind}")
        pos = full.position_ids[fa:fb]
        if kind in ("cond", "video"):
            pos = pos.reshape(-1, Hp // 2, Wp // 2, 3).narrow(axis - 2, start // 2, n_patch).reshape(-1, 3)
        if pos.shape[0] != tb - ta:
            raise RuntimeError(f"{_TAG} tile layout segment {kind} has {tb - ta} rows, full crop {pos.shape[0]}")
        tile.position_ids[ta:tb].copy_(pos)
    return tile


def joint_windows(spans):
    """Per-strip 1-D blend windows for the joint method: linear ramps over each overlap ((k + 0.5) / ov, so two
    neighbours' weights sum to 1 inside the overlap), 1 elsewhere."""
    out = []
    for i, (s, e, ov) in enumerate(spans):
        w = torch.ones(e - s, dtype=torch.float32)
        if i and ov:
            o = min(ov, e - s)
            w[:o] *= (torch.arange(o, dtype=torch.float32) + 0.5) / o
        if i + 1 < len(spans):
            o = min(spans[i + 1][2], e - s)
            if o:
                w[e - s - o:] *= 1.0 - (torch.arange(o, dtype=torch.float32) + 0.5) / o
        out.append(w)
    return out


def tile_payload(payload, axis: int, start: int, end: int, full_hw):
    """minimax_payload for one strip: keyframe latents cropped (core packs them on the target grid), the visual
    condition list rebuilt in core's order (keyframes, then refs), refs / text / audio untouched."""
    out = dict(payload)
    kfs = payload.get("keyframes")
    if kfs:
        H, W = int(full_hw[0]), int(full_hw[1])
        new = []
        for kf in kfs:
            kf = dict(kf)
            lat = kf.get("latent")
            if isinstance(lat, torch.Tensor):
                if lat.ndim != 5 or tuple(lat.shape[-2:]) != (H, W):
                    raise ValueError(f"{_TAG} keyframe latent {tuple(lat.shape)} does not share the target grid "
                                     f"{H}x{W}; cannot crop it to a strip")
                kf["latent"] = lat.narrow(axis, start, end - start).contiguous()
            new.append(kf)
        out["keyframes"] = new
        out["cond_video_latents"] = ([kf["latent"] for kf in new if kf.get("latent") is not None]
                                     + [r["latent"] for r in (payload.get("refs") or []) if "latent" in r])
    return out


def _joint_forward(state, executor, x, timestep, context, transformer_options, minimax_payload=None, **kwargs):
    """DIFFUSION_MODEL wrapper (joint method): run the model on every strip of the CURRENT full-frame state and
    blend the *video* predictions.  Each strip gets: its crop of the video, the full audio (conditioning only),
    its crop of the per-token video denoise mask, the full audio mask, cropped keyframes and the full frame's
    RoPE positions.  Video predictions are blended with joint_windows.  Audio is NOT predicted per strip and
    NOT averaged: strip audio outputs are discarded and the input audio latent is returned unchanged (the
    outer sampler freezes audio via mask_a=0 + low-res x0 anchor; see _run_joint)."""
    jt = state.joint
    video, audio = x[0], x[1]
    H, W = jt["hw"]
    if tuple(video.shape[-2:]) != (H, W) or not isinstance(minimax_payload, dict):
        return executor(x, timestep, context, transformer_options, minimax_payload=minimax_payload, **kwargs)
    if kwargs.get("control") is not None:
        raise ValueError(f"{_TAG} the joint method does not support ControlNet")
    axis, spans, wins = jt["axis"], jt["spans"], jt["windows"]
    dm = kwargs.get("denoise_mask")
    acc_v = torch.zeros(video.shape, dtype=torch.float32, device="cpu")
    acc_w = torch.zeros(video.shape[axis], dtype=torch.float32)
    full_pad = ((H + 1) // 2 * 2, (W + 1) // 2 * 2)
    for (s, e, _), w in zip(spans, wins):
        n = e - s
        tile = video.narrow(axis, s, n).contiguous()
        tkw = dict(kwargs)
        if isinstance(dm, torch.Tensor) and dm.ndim == 5 and tuple(dm.shape[-2:]) == (H, W):
            tkw["denoise_mask"] = dm.narrow(axis, s, n).contiguous()
        elif isinstance(dm, torch.Tensor) and dm.ndim == 5 and dm.shape[axis] != 1:
            raise ValueError(f"{_TAG} video denoise_mask {tuple(dm.shape)} is not on the target grid {H}x{W}")
        key = ("joint", s, e, int(context.shape[1]), tuple(tile.shape), int(audio.shape[-1]),
               id(minimax_payload.get("keyframes")), id(minimax_payload.get("refs")))
        cached = state.cache.get(key)
        if cached is None:
            pay = tile_payload(minimax_payload, axis, s, e, (H, W))
            sig = (int(context.shape[1]), int(tile.shape[2]), (tile.shape[3] + 1) // 2 * 2,
                   (tile.shape[4] + 1) // 2 * 2, int(audio.shape[-1]))
            if GLOBAL_POSITIONS:
                lay = tile_layout_with_full_positions(pay, sig, full_pad, axis, s, e)
            else:
                lay = _make_layout(sig, pay)
            check_tile_layout(lay, pay)
            pay["layout"] = lay
            cached = pay
            state.cache[key] = pay
        pv, pa = executor([tile, audio], timestep, context, transformer_options.copy(), minimax_payload=cached, **tkw)
        view = [1] * video.ndim
        view[axis] = n
        acc_v.narrow(axis, s, n).addcmul_(pv.float().cpu(), w.view(view))
        acc_w[s:e] += w
        del tile, pv, pa  # strip audio prediction discarded on purpose
    state.evals += 1
    view = [1] * video.ndim
    view[axis] = video.shape[axis]
    acc_v.div_(acc_w.clamp(min=1e-6).view(view))
    # Audio: pass through the input latent unchanged (not a strip average).
    return [acc_v.to(device=video.device, dtype=video.dtype), audio]


def _prepare_wrapper(state, executor, model, noise_shape, conds, model_options=None, force_full_load=False,
                     force_offload=False, **kw):
    """PREPARE_SAMPLING wrapper: while the joint method runs, ComfyUI reserves memory for the largest strip (the
    same packed size the sequential method hands to the sampler per strip), not for the full frame."""
    if state.joint is not None and state.budget is not None and not force_offload:
        noise_shape = (noise_shape[0], 1, int(state.budget))
    return executor(model, noise_shape, conds, model_options=model_options, force_full_load=force_full_load,
                    force_offload=force_offload, **kw)


def _pack(tensors):
    import comfy.utils
    return comfy.utils.pack_latents(list(tensors))


def _unpack(packed, shapes):
    import comfy.utils
    return comfy.utils.unpack_latents(packed, shapes)


# ---------------------------------------------------------------------------
# the high-res model wrapper
# ---------------------------------------------------------------------------

class _JointState:
    def __init__(self):
        self.cache = {}
        self.joint = None   # {"axis", "spans", "windows", "hw"} while the joint method samples the full frame
        self.budget = None  # packed elements of the largest strip: memory reservation for the joint method
        self.evals = 0


def frozen_audio_mask(denoise_mask, shapes):
    """Packed AV denoise mask with the audio part set to 0 (kept) and the video part unchanged (all 1 when the
    sampler had no mask).  shapes = [video shape, audio shape]."""
    if denoise_mask is None:
        mv = torch.ones((shapes[0][0], 1, math.prod(shapes[0][1:])), dtype=torch.float32)
    else:
        mv = denoise_mask[:, :, :math.prod(shapes[0][1:])].float().cpu()
    ma = torch.zeros((shapes[1][0], 1, math.prod(shapes[1][1:])), dtype=torch.float32)
    if mv.shape[0] != ma.shape[0]:
        ma = ma.expand(mv.shape[0], -1, -1)
    return torch.cat([mv, ma], dim=-1)


def tiled_high_model(base, req: dict, stats: dict | None = None):
    """Clone of the high-res model whose sampling runs through plan_tiles + the joint method (see module docstring).
    stats (optional dict) receives the plan of the last run under 'plan'."""
    import comfy.patcher_extension
    try:  # H3MD depth guard: the high-res pass must never carry the Fun ControlNet patch
        from . import h3md_depth_control as _DC
    except ImportError:
        import h3md_depth_control as _DC
    _DC.assert_no_fun_control(base, "high-res model (tiling)")
    req = make_request(req.get("axis", "auto"), req.get("overlap", 8))
    stats = stats if stats is not None else {}
    state = _JointState()
    patched = base.clone()

    def outer(executor, noise, latent_image, sampler, sigmas, denoise_mask=None, callback=None,
              disable_pbar=False, seed=None, latent_shapes=None):
        return _run(executor, state, req, stats, noise, latent_image, sampler, sigmas, denoise_mask, callback,
                    disable_pbar, seed, latent_shapes)

    def diffusion(executor, *a, **k):
        if state.joint is not None:
            return _joint_forward(state, executor, *a, **k)
        return executor(*a, **k)

    patched.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.OUTER_SAMPLE, WRAPPER_KEY, outer)
    patched.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, WRAPPER_KEY, diffusion)
    patched.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.PREPARE_SAMPLING, WRAPPER_KEY,
                                 lambda ex, *a, **k: _prepare_wrapper(state, ex, *a, **k))
    return patched


def is_oom(exc) -> bool:
    if isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())) or type(exc).__name__ == "OutOfMemoryError":
        return True
    try:
        import comfy.model_management as mm
        if isinstance(exc, mm.OOM_EXCEPTION):
            return True
    except Exception:
        pass
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


class HighResOutOfMemory(RuntimeError):
    pass


def _free_after_oom():
    import gc
    gc.collect()
    try:
        import comfy.model_management as mm
        mm.soft_empty_cache(True)
    except Exception:
        pass
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _run(executor, state, req, stats, noise, latent_image, sampler, sigmas, denoise_mask, callback, disable_pbar,
         seed, latent_shapes):
    """Plan, run, and on CUDA out-of-memory retry the high-res pass with more strips (the low-res result is kept):
    fewest strips -> more strips ... -> AUTO_MAX_TILES -> + feed-forward chunking -> readable error."""
    try:
        from . import h3md_ffchunk as FF
    except ImportError:
        import h3md_ffchunk as FF
    guider = executor.class_obj
    patcher = guider.model_patcher
    device = device_name(patcher.load_device)
    min_tiles, ff_rows, attempt = 1, 0, 0
    stats["retries"] = []
    while True:
        attempt += 1
        plan = None
        try:
            plan = plan_tiles(patcher, [tuple(x) for x in latent_shapes][0], tuple(latent_shapes[1])[-1],
                              guider.conds, req, min_tiles=min_tiles, device=device)
            stats["plan"] = plan
            with FF.chunked_mlp(ff_rows):
                out = _run_plan(executor, state, plan, stats, noise, latent_image, sampler, sigmas, denoise_mask,
                                callback, disable_pbar, seed, latent_shapes)
            record_run(plan, True, device)
            if ff_rows:
                stats["ff_chunk_rows"] = ff_rows
            return out
        except BaseException as exc:
            if not is_oom(exc):
                raise
            if plan is not None:
                record_run(plan, False, device)
            tiles = plan["tiles"] if plan is not None else min_tiles
            _free_after_oom()
            if plan is not None and tiles < plan["max_tiles"]:
                min_tiles = max(tiles + 1, min(AUTO_MAX_TILES, tiles * 2))
                step = f"retry with >= {min_tiles} strips"
            elif not ff_rows:
                min_tiles, ff_rows = max(min_tiles, tiles), FF.OOM_RETRY_ROWS
                step = f"retry at {tiles} strips + feed-forward chunking ({ff_rows} rows)"
            else:
                raise HighResOutOfMemory(
                    f"{_TAG} out of GPU memory in the high-res pass even with {tiles} strips and feed-forward chunking. "
                    "Lower the resolution or the segment length, use fewer / smaller reference images or videos, or "
                    "start ComfyUI with --lowvram / --reserve-vram. "
                    f"高清阶段显存不足：已切到 {tiles} 条并启用前馈分块仍放不下。请降低分辨率或缩短段长、减少参考素材，"
                    "或用 --lowvram / --reserve-vram 启动 ComfyUI。") from exc
            stats["retries"].append({"attempt": attempt, "tiles": tiles, "next": step})
            LOG.warning("%s CUDA out of memory in the high-res pass at %d strip(s) (attempt %d): %s. Recorded in the "
                        "local calibration; the low-res result is kept.", _TAG, tiles, attempt, step)


def _run_plan(executor, state, plan, stats, noise, latent_image, sampler, sigmas, denoise_mask, callback,
              disable_pbar, seed, latent_shapes):
    guider = executor.class_obj
    patcher = guider.model_patcher
    shapes = [tuple(s) for s in (latent_shapes or [])]
    if len(shapes) != 2 or len(shapes[0]) != 5:
        raise ValueError(f"{_TAG} needs an H3 audio+video latent, got shapes {shapes}")
    if plan["tiles"] == 1:
        LOG.info("%s 1 strip: full-frame high-res pass (audio finished by the high-res pass)", _TAG)
        stats.update(tiles_run=1, audio="high-res")
        return executor(noise, latent_image, sampler, sigmas, denoise_mask, callback, disable_pbar, seed,
                        latent_shapes=latent_shapes)
    axis, spans = plan["axis"], plan["spans"]
    H, W = shapes[0][3], shapes[0][4]
    tshape = list(shapes[0])
    tshape[axis] = max(e - s for s, e, _ in spans)
    mask = frozen_audio_mask(denoise_mask, shapes).to(device=patcher.load_device)
    state.cache = {}
    state.evals = 0
    state.joint = {"axis": axis, "spans": spans, "windows": joint_windows(spans), "hw": (H, W)}
    state.budget = math.prod(tshape[1:]) + math.prod(shapes[1][1:])
    LOG.info("%s joint method: %d strips %s[%s] blended per model evaluation (overlap %d, linear feather); "
             "audio kept at the low-res pass's clean audio; memory reserved for the largest strip %s", _TAG,
             len(spans), plan["axis_name"], ", ".join(f"{s}:{e}" for s, e, _ in spans), plan["overlap"],
             tuple(tshape))
    tic = time.perf_counter()
    try:
        out = executor(noise, latent_image, sampler, sigmas, mask, callback, disable_pbar, seed,
                       latent_shapes=latent_shapes)
    finally:
        evals = state.evals
        state.joint = None
        state.budget = None
        state.cache = {}
    LOG.info("%s joint method done in %.1fs: %d model evaluations x %d strips", _TAG, time.perf_counter() - tic,
             evals, len(spans))
    stats.update(tiles_run=len(spans), evals=evals, audio="low-res x0 (frozen)")
    return out
