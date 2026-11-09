"""H3MD Save Video Interp (H3MDSaveVideoInterp) - own code.

Writes the final long video straight from the H3MD per-segment disk cache (h3md_cache.py) into one video file,
chunk by chunk, so RAM never holds the whole clip (the in-RAM path needs ~19 MB/frame float32 at 1472x1088, i.e.
~60 GB for 3126 frames plus a second, larger copy inside RIFE).

  segments  <- H3MD Loop Sampler output 'long video' (cache folder + merge plan); the only source
               (input id stays 'segments' so saved links / API prompts keep working; the UI label is 'long video')
  frames    -> exactly the frames H3MD Loop's disk merge (SegmentCache.merge) would put into its IMAGE output
               (same trim / merge_overlap_source spans, same fp16/uint8 -> float32 conversion); the Loop itself
               no longer builds that IMAGE ('images' is a placeholder since 2026-10-18)
  audio     -> joined exactly like the merge (h3md_director.join_audio, incl. audio_crossfade_ms)
  optional frame interpolation through ComfyUI-VFI's RIFE model, loaded at runtime with ComfyUI-VFI's OWN loader
               (RIFEInterpolation._get_or_load_model: same model files, same auto download, same model cache).
               No ComfyUI-VFI code is copied (that repository ships no license file).  The target-frame timing
               uses the wrapper's own _calculate_target_frame_positions when available, so a chunked run maps every
               output frame to the same (source frame, source frame, timestep) as a one-pass RIFE run.
  encoding  -> PyAV (ComfyUI's own dependency): mp4 / H.264 yuv420p, BT.709 tags, AAC audio - the settings
               ComfyUI's SaveVideo uses (crf default 19 here, like Jack's Video Combine) - written to <name>.mp4.part and renamed on success.
  cache     -> deleted only when delete_cache_after_save is on AND the file was fully written, closed and renamed.
"""

from __future__ import annotations

import contextlib
import gc
import json
import logging
import math
import os
import re
import shutil
import time
from fractions import Fraction

import numpy as np
import torch

try:
    from . import h3md_director as D
    from . import h3md_drift_correct as DRIFT   # v0.2.0 drift_correct (save-time correction)
except ImportError:  # flat import (tests)
    import h3md_director as D
    import h3md_drift_correct as DRIFT

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD StreamSave]"
CATEGORY = "MiniMax H3/H3MD"
SEGMENTS_TYPE = "H3MD_SEGMENTS"
# saved-file preview (dict for /view: filename, subfolder, type, format, frame_rate, first_frame) or None;
# shown by H3MD Video Preview (h3md_video_preview.py). The save nodes no longer have a built-in player.
PREVIEW_TYPE = "H3MD_VIDEO_PREVIEW"
INFO_VERSION = 1
# output formats (VHS Video Combine style names); every one is written frame by frame (no full-clip RAM)
FORMATS = ("video/h264-mp4", "video/h265-mp4", "image/gif", "image/webp", "image/png-sequence")
VIDEO_FORMATS = ("video/h264-mp4", "video/h265-mp4")     # crf + pix_fmt + AAC audio + embedded metadata
LOOP_FORMATS = ("image/gif", "image/webp")               # loop_count
PIX_FMTS = ("yuv420p", "yuv420p10le")                    # like VHS h264/h265-mp4
EXTENSIONS = {"video/h264-mp4": "mp4", "video/h265-mp4": "mp4", "image/gif": "gif", "image/webp": "webp",
              "image/png-sequence": ""}
SOURCE_FPS = 24.0                                        # H3 renders a fixed 24 fps (source_fps is display only)
WEBP_QUALITY = 90
# removed widgets (2026-10-19): source_fps (fixed 24, display only) and preset (encoder default); an API-format
# prompt that still sends them is accepted and ignored
REMOVED_INPUTS = ("source_fps", "preset", "cache_folder", "folder_overlap_source", "folder_audio_crossfade_ms",
                  "pingpong")   # pingpong: v2 layout of 2026-10-19 only, removed again the same day
_RUN_DIR = re.compile(r"^(resume|run)_[0-9A-Za-z_]+$")
_SEG_JSON = re.compile(r"^seg_(\d{3,})\.json$")
_RUN_FILES = ("run.json", "run.json.tmp")   # the run's base seed (h3md_cache.SegmentCache.save_run_seed)


# ---------------------------------------------------------------------------
# segment plan (what the Loop hands over)
# ---------------------------------------------------------------------------

def segments_info(cache_dir: str, indices, trims, overlap_source: str = "next", audio_crossfade_ms: int = 0,
                  hires_mode: str = "per_segment", cuts=None) -> dict:
    """The H3MD_SEGMENTS value: everything needed to rebuild the merge from disk (no tensors).
    cuts (0.4.0): per segment, the hard cuts INSIDE it (frames from the segment's own first frame, e.g. a
    window-mode group's windowCuts); hard cuts between segments come from trims (0 = cut).  Older values have no
    'cuts' key: only the cuts between segments are known then."""
    out = {"version": INFO_VERSION, "cache_dir": os.path.abspath(cache_dir), "indices": [int(k) for k in indices],
           "trims": [int(t) for t in trims], "overlap_source": str(overlap_source),
           "audio_crossfade_ms": int(audio_crossfade_ms), "fps": D.FPS, "hires_mode": str(hires_mode)}
    if cuts is not None:
        out["cuts"] = [sorted({int(c) for c in (cs or ()) if int(c) > 0}) for cs in cuts]
    return out


def keep_spans(manifests, trims, overlap_source: str):
    """Frame span (start, end) each segment contributes - identical to SegmentCache.merge."""
    trims = [0] + [int(t) for t in trims[1:]]   # the first segment in range is never trimmed
    for m, t in zip(manifests, trims):
        if t >= m["frames"] and t > 0:
            raise ValueError(f"H3MD Join: overlap {t} >= new segment frames {m['frames']}")
    spans = [(t, m["frames"]) for m, t in zip(manifests, trims)]
    if overlap_source == "next":
        spans = [(0, m["frames"] - (trims[j + 1] if j + 1 < len(manifests) else 0)) for j, m in enumerate(manifests)]
        if any(e <= s for s, e in spans):
            raise ValueError("H3MD Join: an overlap leaves no frames of the previous segment (merge_overlap_source=next)")
    return spans, trims


class SegmentSource:
    """Random access to the merged frame sequence, read memory-mapped from the cache (one range at a time)."""

    def __init__(self, info: dict):
        if not isinstance(info, dict) or info.get("version") != INFO_VERSION:
            raise ValueError(f"{_TAG} 'long video' must come from H3MD Loop Sampler (output 'long video')")
        self.dir = info["cache_dir"]
        self.indices = list(info["indices"])
        self.crossfade_ms = int(info.get("audio_crossfade_ms", 0))
        self.fps = float(info.get("fps", D.FPS))
        if not os.path.isdir(self.dir):
            raise FileNotFoundError(f"{_TAG} cache folder {self.dir} does not exist (deleted?)")
        mans = []
        for k in self.indices:
            m = self._manifest(k)
            if m is None:
                raise FileNotFoundError(f"{_TAG} segment {k + 1}: {self._p(k, '.json')} missing - cache incomplete")
            if not os.path.isfile(self._p(k, "_frames.npy")):
                raise FileNotFoundError(f"{_TAG} segment {k + 1}: frames file missing")
            mans.append(m)
        h, w, c = mans[0]["height"], mans[0]["width"], mans[0]["channels"]
        for m in mans:
            if (m["height"], m["width"], m["channels"]) != (h, w, c):
                raise ValueError(f"{_TAG} segment {m['index'] + 1} frame size differs")
        self.h, self.w, self.c = int(h), int(w), int(c)
        self.manifests = mans
        self.spans, self.trims = keep_spans(mans, info["trims"], info.get("overlap_source", "next"))
        self.starts = []
        pos = 0
        for s, e in self.spans:
            self.starts.append(pos)
            pos += e - s
        self.total = pos
        self._mm = {}
        self.drift = None   # v0.2.0: h3md_drift_correct.Plan applied in read() (None = frames unchanged)
        inner = info.get("cuts")
        self.inner_cuts = [list(c or ()) for c in inner] if isinstance(inner, list) and len(inner) == len(mans) \
            else [[] for _ in mans]

    def _p(self, k, suffix):
        return os.path.join(self.dir, f"seg_{k:03d}{suffix}")

    def _manifest(self, k):
        try:
            with open(self._p(k, ".json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _frames(self, j):
        if j not in self._mm:
            self._mm = {j: np.load(self._p(self.indices[j], "_frames.npy"), mmap_mode="r")}  # one open map at a time
        return self._mm[j]

    def read(self, a: int, b: int) -> torch.Tensor:
        """Merged frames [a, b) as float32 [b-a, H, W, C] (same conversion as SegmentCache.merge)."""
        a, b = max(0, int(a)), min(self.total, int(b))
        out = torch.empty((max(0, b - a), self.h, self.w, self.c), dtype=torch.float32)
        pos = a
        for j, (s0, e0) in enumerate(self.spans):
            g0, n = self.starts[j], e0 - s0
            if pos >= b:
                break
            if pos >= g0 + n:
                continue
            lo = s0 + (pos - g0)
            hi = s0 + min(n, b - g0)
            part = torch.from_numpy(np.array(self._frames(j)[lo:hi])).to(torch.float32)
            if self.manifests[j]["dtype"] == "uint8":
                part = part / 255.0
            out[pos - a:pos - a + part.shape[0]] = part
            pos += part.shape[0]
            del part
        assert pos == b, (pos, b)
        if self.drift is not None:
            out = self.drift.correct(out, a)
        return out

    def segment_blocks(self):
        """[(start, end)] of every segment in the merged clip and cut flags (True = first segment / hard cut)."""
        blocks = [(g0, g0 + (e - s)) for g0, (s, e) in zip(self.starts, self.spans)]
        cut = [j == 0 or int(t) == 0 for j, t in enumerate(self.trims)]
        return blocks, cut

    def hard_cuts(self):
        """Merged-clip frame numbers where a new shot starts (0.4.0): every segment after the first that is not a
        continuation (trim 0), plus the cuts inside a segment (info 'cuts', e.g. a window-mode group) that fall in
        the frames this segment contributes.  Frame c = first frame of the new shot, so frames c-1 and c must never
        be blended by the interpolation."""
        out = set()
        for j, ((s, e), g0) in enumerate(zip(self.spans, self.starts)):
            if j > 0 and int(self.trims[j]) == 0:
                out.add(g0)
            for c in self.inner_cuts[j]:
                if s <= int(c) < e:
                    out.add(g0 + int(c) - s)
        return sorted(f for f in out if 0 < f < self.total)

    def audio(self):
        """Joined audio, like SegmentCache.merge (audio is small: ~0.4 MB/s stereo float32)."""
        merged = None
        for k, trim in zip(self.indices, self.trims):
            a = torch.load(self._p(k, "_audio.pt"), map_location="cpu", weights_only=True)
            merged = a if merged is None else D.join_audio(a, trim, merged, self.crossfade_ms)
        return merged

    def close(self):
        self._mm = {}
        gc.collect()  # Windows: memmap handles must be closed before the files can be removed


def merged_audio(info: dict):
    src = SegmentSource(info)
    try:
        return src.audio(), src.total
    finally:
        src.close()


# ---------------------------------------------------------------------------
# frame-rate conversion timing (one-pass equivalent)
# ---------------------------------------------------------------------------

def target_positions(source_fps: float, target_fps: float, total_source_frames: int):
    """[(src1, src2, factor)] per output frame, for the WHOLE clip: output frame t sits at time t/target_fps.
    Fallback only - at run time the RIFE wrapper's own _calculate_target_frame_positions is used when present."""
    n = int(total_source_frames)
    total = int(n / source_fps * target_fps)
    out = []
    for t in range(total):
        p = t / target_fps * source_fps
        i1 = int(p)
        i2 = min(i1 + 1, n - 1)
        out.append((i1, i2, 0.0 if i1 == i2 else p - i1))
    return out


def skip_cuts(positions, cuts):
    """0.4.0: never interpolate across a hard cut.  positions [(src1, src2, factor)]; cuts = frames where a new
    shot starts.  An output frame between frames c-1 and c (a blend of two different shots) becomes a plain copy of the
    nearer source frame (the earlier one at exactly half way); timing, frame count and audio stay the same.
    -> (new positions, number of output frames changed)."""
    cs = sorted({int(c) for c in cuts or ()})
    if not cs:
        return list(positions), 0
    import bisect
    out, changed = [], 0
    for i1, i2, f in positions:
        if f and i1 != i2:
            k = bisect.bisect_right(cs, int(i1))       # first cut after i1
            if k < len(cs) and cs[k] <= int(i2):       # i1 < cut <= i2: the two frames belong to different shots
                src = i1 if f <= 0.5 else i2
                out.append((src, src, 0.0))
                changed += 1
                continue
        out.append((i1, i2, f))
    return out, changed


def log_cut_skips(cuts, changed, tag=_TAG):
    if cuts:
        LOG.info("%s %d hard cut(s) at source frames %s: %d interpolated frame(s) across them use the nearest source "
                 "frame (no blend of two shots)", tag, len(cuts), cuts, changed)


def next_chunk(positions, j: int, n_source: int, chunk_frames: int):
    """Next chunk starting at output frame j: the outputs whose first source frame lies in
    [positions[j][0], positions[j][0] + chunk_frames) -> (src_lo, src_hi_exclusive, j, j_end).
    Source frames needed: <= chunk_frames + 1 (the carried boundary frame)."""
    chunk = max(1, int(chunk_frames))
    a = positions[j][0]
    b = min(a + chunk, int(n_source))
    k = j
    while k < len(positions) and positions[k][0] < b:
        k += 1
    lo = min(p[0] for p in positions[j:k])
    hi = max(max(p[0], p[1]) for p in positions[j:k]) + 1
    return lo, hi, j, k


def chunk_plan(positions, n_source: int, chunk_frames: int):
    """Whole plan with a fixed chunk size (tests / reference): [(src_lo, src_hi, out_lo, out_hi)], in order."""
    plan, j = [], 0
    while j < len(positions):
        plan.append(next_chunk(positions, j, n_source, chunk_frames))
        j = plan[-1][3]
    return plan


# ---------------------------------------------------------------------------
# RIFE via ComfyUI-VFI (runtime import; hooks are monkeypatched in tests)
# ---------------------------------------------------------------------------

def _vfi_class():
    try:
        import nodes as comfy_nodes  # ComfyUI's global node registry
        return comfy_nodes.NODE_CLASS_MAPPINGS.get("RIFEInterpolation")
    except Exception:
        return None


def vfi_model_names():
    cls = _vfi_class()
    try:
        spec = cls.INPUT_TYPES()["optional"]["model_name"]
        if isinstance(spec[0], (list, tuple)) and spec[0]:
            return list(spec[0])
    except Exception:
        pass
    return ["flownet.pkl"]


def load_interpolator(model_name: str, use_fp16: bool):
    """ComfyUI-VFI's RIFEWrapper, through its own loader + model cache."""
    cls = _vfi_class()
    if cls is None:
        raise RuntimeError(f"{_TAG} interpolate=on needs ComfyUI-VFI (node 'RIFE Frame Interpolation') installed")
    return cls()._get_or_load_model(model_name, use_fp16=use_fp16)


def interpolator_positions(wrapper, source_fps, target_fps, n):
    fn = getattr(wrapper, "_calculate_target_frame_positions", None)
    if callable(fn):
        return list(fn(source_fps, target_fps, n))
    LOG.warning("%s RIFE wrapper has no _calculate_target_frame_positions; using H3MD's equivalent", _TAG)
    return target_positions(source_fps, target_fps, n)


def autocast_ctx(use_fp16: bool):
    if use_fp16 and torch.cuda.is_available():
        return torch.amp.autocast("cuda")
    return contextlib.nullcontext()


# ---------------------------------------------------------------------------
# automatic chunk size (free VRAM / RAM; hooks monkeypatched in tests)
# ---------------------------------------------------------------------------

MIN_CHUNK = 8            # source frames
MAX_CHUNK = 240          # source frames (10 s at 24 fps): keeps progress / interrupt responsive
VRAM_USE = 0.90          # of the free VRAM at the start of each chunk
RAM_USE = 0.25           # of the available RAM (sanity cap)
PAIR_ACT_PER_PX = 256    # conservative RIFE (IFNet HDv3) activation bytes-per-element factor per padded pixel ...
PAIR_ACT_PER_PX_S2 = 256  # ... plus this x scale^2 (the last IFBlock runs at `scale` x the padded size)


def free_vram(device):
    """Free VRAM in bytes (ComfyUI's view incl. torch's reserved-but-free memory), None without CUDA."""
    if device is None or getattr(device, "type", str(device)) != "cuda":
        return None
    try:
        import comfy.model_management as mm
        return int(mm.get_free_memory(device))
    except Exception:
        pass
    try:
        return int(torch.cuda.mem_get_info(device)[0])
    except Exception:
        return None


def free_ram():
    try:
        import comfy.model_management as mm
        return int(mm.get_free_memory(torch.device("cpu")))
    except Exception:
        pass
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:
        return 16 * 1024 ** 3


def padded_hw(height: int, width: int, scale: float):
    tmp = max(128, int(128 / scale))
    return ((height - 1) // tmp + 1) * tmp, ((width - 1) // tmp + 1) * tmp


def rife_vram_estimate(height, width, scale, fp16, batch_size):
    """-> (bytes per chunk source frame on the GPU, fixed working set of one batch).  Conservative on purpose."""
    eb = 2 if fp16 else 4
    ph, pw = padded_hw(height, width, scale)
    per_frame = 3 * height * width * eb
    px = ph * pw
    batch = int(batch_size) * 3 * 3 * px * eb                       # I0, I1 and the stacked results
    act = px * (PAIR_ACT_PER_PX + PAIR_ACT_PER_PX_S2 * max(1.0, float(scale)) ** 2) * 4   # fp32 worst case
    return per_frame, int(batch + act)


def ram_chunk_cap(height, width, out_per_src: float):
    per = 3 * height * width * 4 * (1.0 + max(0.0, out_per_src))   # float32 source chunk + its output frames
    return max(MIN_CHUNK, int(free_ram() * RAM_USE // max(1, per)))


def auto_chunk(height, width, device, scale, fp16, batch_size, out_per_src, interp=True, learned_cap=None):
    """Source frames for the next chunk: <= 90 % of the free VRAM (RIFE), RAM cap, [MIN_CHUNK, MAX_CHUNK]."""
    n = min(MAX_CHUNK, ram_chunk_cap(height, width, out_per_src))
    why = "ram"
    if interp:
        free = free_vram(device)
        if free is not None:
            per_frame, work = rife_vram_estimate(height, width, scale, fp16, batch_size)
            v = int((free * VRAM_USE - work) // per_frame)
            if v < n:
                n, why = v, f"vram (free {free / 1e9:.2f} GB)"
    if learned_cap is not None and learned_cap < n:
        n, why = learned_cap, "after OOM"
    return max(MIN_CHUNK, int(n)), why


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


def empty_cache():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def free_everything():
    """Last resort: ComfyUI's own memory management (unload every model it manages, empty its cache)."""
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache(True)
    except Exception as exc:
        LOG.warning("%s ComfyUI memory management unavailable (%s)", _TAG, exc)
    empty_cache()


@contextlib.contextmanager
def rife_on_cpu(wrapper):
    """Temporarily run the RIFE wrapper on the CPU in float32 (VFI's warp() reads a module-level `device`)."""
    import sys
    net = getattr(getattr(wrapper, "model", None), "flownet", None)
    saved = []
    p0 = next(net.parameters(), None) if net is not None and hasattr(net, "parameters") else None
    old_dev, old_dtype = (p0.device, p0.dtype) if p0 is not None else (None, None)
    mods = set()
    if net is not None:
        m = sys.modules.get(type(net).__module__)
        if m is not None:
            mods.add(m)
            w = getattr(m, "warp", None)
            if w is not None and sys.modules.get(getattr(w, "__module__", "")) is not None:
                mods.add(sys.modules[w.__module__])
    try:
        for m in mods:
            if hasattr(m, "device"):
                saved.append((m, m.device))
                m.device = torch.device("cpu")
        if net is not None:
            net.to(device="cpu", dtype=torch.float32)
        empty_cache()
        yield torch.device("cpu"), torch.float32
    finally:
        if net is not None and old_dev is not None:
            net.to(device=old_dev, dtype=old_dtype)
        for m, d in saved:
            m.device = d


def interpolate_chunk(wrapper, frames: torch.Tensor, jobs, scale: float, batch_size: int, device=None, dtype=None):
    """frames [n,H,W,3] float32 (CPU); jobs [(i1, i2, factor)] local indices -> list of [H,W,3] float32 CPU frames.
    Direct copies for factor 0 / i1 == i2; the rest in batches of batch_size through wrapper.model.inference_batch
    (padding to a multiple of max(128, int(128/scale))). The chunk's source frames go to the GPU once."""
    height, width = int(frames.shape[1]), int(frames.shape[2])
    ph, pw = padded_hw(height, width, scale)
    padding = (0, pw - width, 0, ph - height)
    device = device if device is not None else getattr(wrapper, "device", torch.device("cpu"))
    if dtype is None:
        dtype = torch.float16 if getattr(wrapper, "use_fp16", False) else torch.float32
    out = [None] * len(jobs)
    work = []
    for o, (i1, i2, f) in enumerate(jobs):
        if f == 0.0 or i1 == i2:
            out[o] = frames[i1]
        else:
            work.append((o, i1, i2, f))
    if not work:
        return out
    need = sorted({x[1] for x in work} | {x[2] for x in work})
    with torch.inference_mode():
        gpu = {i: frames[i].to(device=device, dtype=dtype) for i in need}   # same conversion as VFI, once per chunk
        try:
            for b0 in range(0, len(work), batch_size):
                batch = work[b0:b0 + batch_size]
                I0 = torch.empty((len(batch), 3, ph, pw), dtype=dtype, device=device)
                I1 = torch.empty((len(batch), 3, ph, pw), dtype=dtype, device=device)
                steps = []
                for n, (_o, i1, i2, f) in enumerate(batch):
                    I0[n] = torch.nn.functional.pad(gpu[i1].permute(2, 0, 1).unsqueeze(0), padding)[0]
                    I1[n] = torch.nn.functional.pad(gpu[i2].permute(2, 0, 1).unsqueeze(0), padding)[0]
                    steps.append(f)
                res = wrapper.model.inference_batch(I0, I1, steps, scale=scale)
                for n, (o, *_r) in enumerate(batch):
                    out[o] = res[n, :, :height, :width].permute(1, 2, 0).cpu().to(torch.float32)
                del I0, I1, res
        finally:
            del gpu
            if getattr(device, "type", str(device)) == "cuda":
                torch.cuda.empty_cache()
    return out


def interpolate_with_fallback(wrapper, src, positions, j, n_src, chunk, scale, batch_size, state):
    """One chunk with the OOM ladder: halve the chunk (>= MIN_CHUNK) -> free ComfyUI models + batch 1 -> CPU.
    Returns (lo, hi, j0, j1, outs, chunk actually used).  state: {'cap': learned chunk cap, 'events': [...]}"""
    stage = 0      # 0 normal, 1 freed + batch 1, 2 cpu
    oomed = False
    while True:
        lo, hi, j0, j1 = next_chunk(positions, j, n_src, chunk)
        frames = src.read(lo, hi)
        jobs = [(p[0] - lo, p[1] - lo, p[2]) for p in positions[j0:j1]]
        try:
            if stage < 2:
                outs = interpolate_chunk(wrapper, frames, jobs, scale, batch_size if stage == 0 else 1)
            else:
                with rife_on_cpu(wrapper) as (dev, dt):
                    with torch.autocast("cuda", enabled=False) if torch.cuda.is_available() else contextlib.nullcontext():
                        outs = interpolate_chunk(wrapper, frames, jobs, scale, 1, dev, dt)
            if oomed:  # never grow back beyond what fitted after an OOM
                state["cap"] = chunk if stage == 0 else MIN_CHUNK
            return lo, hi, j0, j1, outs, chunk
        except BaseException as exc:
            if not is_oom(exc):
                raise
            oomed = True
            del frames, jobs
            empty_cache()
            if stage == 0 and chunk > MIN_CHUNK:
                new = max(MIN_CHUNK, chunk // 2)
                state["events"].append(("halve", chunk, new))
                LOG.warning("%s CUDA out of memory with a %d-frame chunk; retrying with %d", _TAG, chunk, new)
                chunk = new
            elif stage == 0:
                stage = 1
                state["events"].append(("free_models_batch1", chunk))
                LOG.warning("%s CUDA out of memory at the minimum chunk (%d): unloading ComfyUI models, "
                            "soft_empty_cache, batch_size 1, retry", _TAG, chunk)
                free_everything()
            elif stage == 1:
                stage = 2
                state["events"].append(("cpu", chunk))
                LOG.warning("%s still out of memory: interpolating this chunk on the CPU (float32, slow; "
                            "frames may differ slightly from GPU fp16)", _TAG)
            else:
                raise


# ---------------------------------------------------------------------------
# streaming writer (PyAV)
# ---------------------------------------------------------------------------

def _color_tagger():
    """ComfyUI's own sRGB/BT.709 tagging when available (same as SaveVideo), else a minimal equivalent."""
    try:
        from comfy_api.latest._input_impl.video_types import set_video_color_properties, BT709_NCL
        return (lambda t: set_video_color_properties(t, "sRGB")), BT709_NCL
    except Exception:
        pass
    try:
        from av.video.reformatter import ColorPrimaries, ColorRange, ColorTrc

        def tag(t):
            t.color_primaries = ColorPrimaries.BT709
            t.color_trc = ColorTrc.IEC61966_2_1
            t.colorspace = 1
            t.color_range = ColorRange.MPEG
        return tag, 1
    except Exception:
        return None, None


def _tag_json(value):
    return json.dumps(value)   # like ComfyUI SaveVideo / VHS: every value JSON-encoded


class StreamWriter:
    """mp4 / H.264 or H.265 (yuv420p or yuv420p10le) + AAC, frames appended chunk by chunk; audio is interleaved up to
    the written video time.  Metadata = mp4 tags 'prompt' / 'workflow' / ... (use_metadata_tags), like ComfyUI's
    SaveVideo and VHS Video Combine (save_metadata)."""

    def __init__(self, path, width, height, fps, audio=None, crf=19, pix_fmt="yuv420p", metadata=None, codec="h264"):
        import av
        self.av = av
        self.path = path
        self.rate = Fraction(round(float(fps) * 1000), 1000)
        self.pix_fmt = pix_fmt if pix_fmt in PIX_FMTS else "yuv420p"
        self.out = av.open(path, mode="w", format="mp4", options={"movflags": "use_metadata_tags+faststart"})
        try:
            for key, value in (metadata or {}).items():
                self.out.metadata[key] = _tag_json(value)
            if codec == "h265":
                self.v = self.out.add_stream("libx265", rate=self.rate)
                self.v.codec_tag = "hvc1"                       # like VHS h265-mp4 (-vtag hvc1, plays on Apple)
                opts = {"crf": str(int(crf)), "x265-params": "log-level=error"}
            else:
                self.v = self.out.add_stream("h264", rate=self.rate)
                opts = {"crf": str(int(crf))}
            self.v.width, self.v.height, self.v.pix_fmt = int(width), int(height), self.pix_fmt
            self.v.options = opts                              # no preset: the encoder's default (medium)
            self.tag, self.dst_cs = _color_tagger()
            if self.tag is not None:
                try:
                    self.tag(self.v.codec_context)
                except Exception as exc:
                    LOG.warning("%s color tags not set on the stream (%s)", _TAG, exc)
                    self.tag = None
            self.a = None
            self.wave, self.sr, self.a_done = None, 1, 0
            if audio is not None and audio.get("waveform") is not None:
                self.sr = int(audio["sample_rate"])
                wave = audio["waveform"][0].float().cpu().contiguous()
                self.layout = {1: "mono", 2: "stereo", 6: "5.1"}.get(int(wave.shape[0]), "stereo")
                if self.layout == "stereo" and wave.shape[0] != 2:
                    wave = wave[:2] if wave.shape[0] > 2 else wave.expand(2, -1)
                self.wave = wave
                self.a = self.out.add_stream("aac", rate=self.sr, layout=self.layout)
            self.frames = 0
        except BaseException:
            self.out.close()
            raise

    def _audio_upto(self, n_video_frames, final=False):
        if self.a is None:
            return
        limit = min(self.wave.shape[-1], math.ceil(self.sr / self.rate * n_video_frames))
        if limit <= self.a_done:
            return
        part = self.wave[:, self.a_done:limit].contiguous().numpy()
        fr = self.av.AudioFrame.from_ndarray(part, format="fltp", layout=self.layout)
        fr.sample_rate = self.sr
        fr.pts = self.a_done
        fr.time_base = Fraction(1, self.sr)
        for pkt in self.a.encode(fr):
            self.out.mux(pkt)
        self.a_done = limit

    def write(self, frames):
        """frames: iterable of [H,W,3] float tensors in [0,1] (SaveVideo's 8-bit conversion: *255, clamp, truncate)."""
        for f in frames:
            if self.pix_fmt == "yuv420p10le":   # 16-bit RGB in, so the 10-bit file keeps more than 8 bits
                img = (f.float() * 65535).clamp(0, 65535).round().to(torch.int32).cpu().numpy().astype(np.uint16)
                vf = self.av.VideoFrame.from_ndarray(img, format="rgb48le")
            else:
                img = (f * 255).clamp(0, 255).byte().cpu().numpy()
                vf = self.av.VideoFrame.from_ndarray(img, format="rgb24")
            vf = vf.reformat(format=self.pix_fmt, dst_colorspace=self.dst_cs) if self.dst_cs is not None \
                else vf.reformat(format=self.pix_fmt)
            if self.tag is not None:
                try:
                    self.tag(vf)
                except Exception:
                    pass
            vf.pts = self.frames
            vf.time_base = 1 / self.rate
            for pkt in self.v.encode(vf):
                self.out.mux(pkt)
            self.frames += 1
        self._audio_upto(self.frames)

    def finish(self):
        for pkt in self.v.encode(None):
            self.out.mux(pkt)
        if self.a is not None:
            self._audio_upto(self.frames, final=True)
            for pkt in self.a.encode(None):
                self.out.mux(pkt)
        self.out.close()

    def abort(self):
        try:
            self.out.close()
        except Exception:
            pass


def _to_rgb24(f):
    return (f * 255).clamp(0, 255).byte().cpu().numpy()


class AnimWriter:
    """Animated GIF (per-frame palette: palettegen stats_mode=single + paletteuse new=1) or animated WebP
    (libwebp_anim, quality WEBP_QUALITY), frame by frame through PyAV.  No audio.  loop_count: 0 = loop forever,
    n = play n more times (VHS / Pillow meaning).  GIF stores delays in 1/100 s (24 fps plays as 25 fps)."""

    def __init__(self, path, width, height, fps, fmt, loop_count=0):
        import av
        self.av, self.path, self.fmt = av, path, fmt
        self.rate = Fraction(round(float(fps) * 1000), 1000)
        self.a, self.a_done, self.sr, self.frames = None, 0, 1, 0
        loop = str(max(0, int(loop_count)))
        if fmt == "image/gif":
            self.out = av.open(path, mode="w", format="gif", options={"loop": loop})
            try:
                self.v = self.out.add_stream("gif", rate=self.rate)
                self.v.width, self.v.height, self.v.pix_fmt = int(width), int(height), "pal8"
                g = av.filter.Graph()
                self.src = g.add_buffer(width=int(width), height=int(height), format="rgb24", time_base=1 / self.rate)
                sp, pg = g.add("split"), g.add("palettegen", "stats_mode=single:max_colors=256:reserve_transparent=0")
                pu, self.sink = g.add("paletteuse", "new=1:dither=sierra2_4a"), g.add("buffersink")
                self.src.link_to(sp); sp.link_to(pg, 0, 0); sp.link_to(pu, 1, 0); pg.link_to(pu, 0, 1)
                pu.link_to(self.sink)
                g.configure()
                self.graph = g
            except BaseException:
                self.out.close()
                raise
        else:
            self.out = av.open(path, mode="w", format="webp", options={"loop": loop})
            try:
                self.v = self.out.add_stream("libwebp_anim", rate=self.rate)
                self.v.width, self.v.height, self.v.pix_fmt = int(width), int(height), "yuv420p"
                self.v.options = {"quality": str(WEBP_QUALITY), "lossless": "0"}
                self.graph = None
            except BaseException:
                self.out.close()
                raise
        self.pushed = 0

    def _encode(self, vf):
        vf.pts = self.frames
        vf.time_base = 1 / self.rate
        for pkt in self.v.encode(vf):
            self.out.mux(pkt)
        self.frames += 1

    def _drain(self):
        while True:
            try:
                f = self.sink.pull()
            except (self.av.BlockingIOError, self.av.EOFError):
                return
            self._encode(f)

    def write(self, frames):
        for f in frames:
            vf = self.av.VideoFrame.from_ndarray(_to_rgb24(f), format="rgb24")
            if self.graph is not None:
                vf.pts, vf.time_base = self.pushed, 1 / self.rate
                self.src.push(vf)
                self.pushed += 1
                self._drain()
            else:
                self._encode(vf.reformat(format="yuv420p"))

    def finish(self):
        if self.graph is not None:
            self.src.push(None)
            self._drain()
        for pkt in self.v.encode(None):
            self.out.mux(pkt)
        self.out.close()

    def abort(self):
        try:
            self.out.close()
        except Exception:
            pass


class PngSeqWriter:
    """Numbered 8-bit PNGs (frame_000001.png ...) in the folder `path`; prompt/workflow as PNG text chunks of the
    first frame (save_metadata, like ComfyUI's SaveImage).  No audio."""

    def __init__(self, path, width, height, fps, metadata=None):
        os.makedirs(path, exist_ok=True)
        self.path, self.meta, self.frames = path, metadata, 0
        self.a, self.a_done, self.sr = None, 0, 1

    def write(self, frames):
        from PIL import Image
        for f in frames:
            info = None
            if self.frames == 0 and self.meta:
                from PIL.PngImagePlugin import PngInfo
                info = PngInfo()
                for k, v in self.meta.items():
                    info.add_text(k, _tag_json(v))
            Image.fromarray(_to_rgb24(f)).save(os.path.join(self.path, f"frame_{self.frames + 1:06d}.png"),
                                               pnginfo=info, compress_level=4)
            self.frames += 1

    def finish(self):
        pass

    def abort(self):
        pass


def open_writer(fmt, path, width, height, fps, audio=None, crf=19, pix_fmt="yuv420p", metadata=None, loop_count=0):
    if fmt not in FORMATS:
        raise ValueError(f"{_TAG} unknown format {fmt!r} (one of {', '.join(FORMATS)})")
    if fmt in VIDEO_FORMATS:
        return StreamWriter(path, width, height, fps, audio, crf=crf, pix_fmt=pix_fmt, metadata=metadata,
                            codec="h265" if fmt == "video/h265-mp4" else "h264")
    if audio is not None:
        LOG.info("%s %s has no audio track: audio is not written", _TAG, fmt)
    if fmt == "image/png-sequence":
        return PngSeqWriter(path, width, height, fps, metadata)
    return AnimWriter(path, width, height, fps, fmt, loop_count)


def metadata_png(path, first_frame, metadata):
    """gif / webp + save_metadata: <file>.png = first frame with the prompt/workflow text chunks (as VHS does)."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    for k, v in metadata.items():
        info.add_text(k, _tag_json(v))
    Image.fromarray(_to_rgb24(first_frame)).save(path, pnginfo=info, compress_level=4)


def _held_bytes(tensors):
    """Bytes of the distinct CPU storages behind `tensors` (views of the chunk are not counted twice)."""
    seen, total = set(), 0
    for t in tensors:
        st = t.untyped_storage()
        if st.data_ptr() not in seen:
            seen.add(st.data_ptr())
            total += st.nbytes()
    return total


def probe(path):
    """(video frames, video seconds, audio seconds or None) of a written file, read back with PyAV."""
    import av
    with av.open(path) as c:
        v = c.streams.video[0]
        n = v.frames or sum(1 for _ in c.demux(v) if _.size)
        vd = float(v.duration * v.time_base) if v.duration else None
        ad = None
        if c.streams.audio:
            a = c.streams.audio[0]
            ad = float(a.duration * a.time_base) if a.duration else None
    return int(n), vd, ad


# ---------------------------------------------------------------------------
# hooks (monkeypatched in tests)
# ---------------------------------------------------------------------------

def check_interrupt():
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def progress_bar(total, node_id=None):
    try:
        import comfy.utils
        return comfy.utils.ProgressBar(total, node_id=node_id)
    except Exception:
        return None


def output_directory():
    import folder_paths
    return folder_paths.get_output_directory()


def temp_directory():
    import folder_paths
    return folder_paths.get_temp_directory()


def save_path(filename_prefix, output_folder, width, height, ext="mp4", temp=False):
    """-> (full path, filename, subfolder for /view or None when outside ComfyUI's folder, 'output' | 'temp').
    temp (save_output off): ComfyUI's temp folder, output_folder ignored."""
    import folder_paths
    base = temp_directory() if temp else output_directory()
    custom = "" if temp else (output_folder or "").strip().strip('"')
    root = D.PATHS.output_root(custom, base)   # v0.2.1: shared ComfyUI -> always inside the output folder
    os.makedirs(root, exist_ok=True)
    full, name, counter, subfolder, _ = folder_paths.get_save_image_path(filename_prefix, root, width, height)
    file = f"{name}_{counter:05}_.{ext}" if ext else f"{name}_{counter:05}_"   # '' = a folder (png sequence)
    preview = None
    try:
        rel = os.path.relpath(os.path.join(full, file), base)
        if not rel.startswith(".."):
            preview = os.path.dirname(rel).replace("\\", "/")
    except ValueError:  # other drive than the output folder (Windows)
        preview = None
    return os.path.join(full, file), file, preview, "temp" if temp else "output"


def delete_cache_dir(path):
    """Delete a H3MD run cache folder - only one that looks like one (resume_*/run_* holding seg_###.json)."""
    path = os.path.abspath(path)
    names = os.listdir(path) if os.path.isdir(path) else []
    if not _RUN_DIR.match(os.path.basename(path)) or not any(_SEG_JSON.match(n) for n in names):
        LOG.warning("%s %s does not look like a H3MD run cache; NOT deleted", _TAG, path)
        return False
    if any(not n.startswith("seg_") and n not in _RUN_FILES for n in names):   # 11-09: run.json is ours too
        LOG.warning("%s %s holds non-H3MD files; NOT deleted", _TAG, path)
        return False
    gc.collect()
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        LOG.warning("%s could not fully delete %s (file still in use?) - remove it by hand", _TAG, path)
        return False
    LOG.info("%s deleted cache %s", _TAG, path)
    return True


# ---------------------------------------------------------------------------
# core (no ComfyUI dependency except the hooks above)
# ---------------------------------------------------------------------------

DRIFT_INPUTS = {
    "drift_correct": (DRIFT.MODES, {"default": "off", "tooltip":
                      "v0.2.0. auto: while saving, pull slow drift (saturation creeping up, picture getting softer) "
                      "back segment by segment. A hard cut starts a new reference; inside a shot a segment whose "
                      "brightness, colour or layout clearly changed (night falls, a new room, someone walks in) is "
                      "left alone and becomes the new reference. Shots of 1-2 segments are not touched. The cache is "
                      "not changed: switch off and save again to compare. Decisions: <video>_drift.json.\n"
                      "auto：保存时把一段段累积的漂移（饱和度变高、画面变软）拉回来。硬切换新参照；同一镜头里亮度、"
                      "色调或构图明显变了的段（天黑、换地方、有人进来）不调，并成为新参照；1-2 段的短镜头不动。"
                      "缓存不变：关掉再存一次就能对比。每段的判断写在 <视频>_drift.json。"}),
    "drift_strength": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip":
                       "drift_correct = auto: 1 = match the reference, 0.5 = half way. 校正力度。"}),
}


def attach_drift(src, mode="off", strength=1.0):
    """Measure the clip and set src.drift (save-time correction).  -> per-segment report rows or None."""
    if mode != "auto" or float(strength) <= 0:
        return None
    t0 = time.time()
    blocks, cut = src.segment_blocks()
    src.drift = None
    plan = DRIFT.build(src.read, blocks, cut, src.fps, float(strength), True, check=check_interrupt)
    src.drift = plan if plan.active else None
    rows = plan.report()
    LOG.info("%s drift_correct auto: %d segment(s) measured in %.1fs, %d corrected, %d scene change(s)", _TAG,
             len(blocks), time.time() - t0, sum(r["role"] == "drift" for r in rows),
             sum(r["role"] == "scene" for r in rows))
    return rows


def write_drift_report(path, rows, strength):
    if rows is None:
        return None
    rp = os.path.splitext(path)[0] + "_drift.json"
    try:
        with open(rp, "w", encoding="utf-8") as f:
            json.dump({"video": os.path.basename(path), "strength": float(strength), "segments": rows}, f, indent=1,
                      ensure_ascii=False)
        return rp
    except OSError as exc:
        LOG.warning("%s drift report not written (%s)", _TAG, exc)
        return None


def stream_save(info, path, interpolate=False, target_fps=SOURCE_FPS, scale=1.0, model_name="flownet.pkl",
                batch_size=8, use_fp16=True, crf=19, metadata=None, interpolator=None, pbar=None, chunk_frames=None,
                fmt="video/h264-mp4", pix_fmt="yuv420p", loop_count=0, trim_to_audio=False, drift_correct="off",
                drift_strength=1.0):
    """Write the merged clip of `info` to `path` (atomic: path + '.part' renamed at the end). Returns a report.
    The source is always H3MD's own frame rate (24 fps).  The file is always written at target_fps:
    interpolate on: RIFE 24 -> target_fps.  Off: the frames unchanged, played at target_fps (!= 24 plays faster /
    slower than real time and the audio no longer matches - logged as a warning).
    Chunk size is automatic (free VRAM / RAM before each chunk); chunk_frames (tests only) pins it."""
    t0 = time.time()
    src = SegmentSource(info)
    drift_rows = attach_drift(src, drift_correct, drift_strength)   # v0.2.0 (None when off)
    n = src.total
    source_fps = float(src.fps)
    out_fps = float(target_fps)
    if out_fps <= 0:
        raise ValueError(f"{_TAG} target_fps must be > 0")
    do_interp = bool(interpolate) and abs(source_fps - out_fps) >= 0.01  # VFI: equal fps = passthrough
    if not bool(interpolate) and abs(source_fps - out_fps) >= 0.01:
        LOG.warning("%s interpolate is OFF but target_fps = %.3f: the %d frames (%.3f fps) are saved at %.3f fps, so "
                    "the video plays %.2fx %s than real time and the audio will NOT match. Set target_fps = %g, or "
                    "turn interpolate on.", _TAG, out_fps, n, source_fps, out_fps, out_fps / source_fps,
                    "faster" if out_fps > source_fps else "slower", source_fps)
    cuts, cut_copies = src.hard_cuts(), 0
    if do_interp:
        wrapper = interpolator if interpolator is not None else load_interpolator(model_name, bool(use_fp16))
        positions, cut_copies = skip_cuts(interpolator_positions(wrapper, source_fps, out_fps, n), cuts)
        log_cut_skips(cuts, cut_copies)
        device = getattr(wrapper, "device", torch.device("cpu"))
        fp16 = bool(getattr(wrapper, "use_fp16", False))
    else:
        wrapper, device, fp16 = None, None, False
        positions = [(i, i, 0.0) for i in range(n)]
    audio = src.audio()
    if trim_to_audio and fmt in VIDEO_FORMATS:   # like VHS: the video ends with the audio (formats with audio only)
        if audio is not None and audio.get("waveform") is not None:
            a_sec = audio["waveform"].shape[-1] / float(audio["sample_rate"])
            limit = max(1, int(math.floor(a_sec * out_fps + 1e-6)))
            if limit < len(positions):
                LOG.info("%s trim_to_audio: %d -> %d frames (audio %.3f s)", _TAG, len(positions), limit, a_sec)
                positions = positions[:limit]
        else:
            LOG.info("%s trim_to_audio: no audio, nothing trimmed", _TAG)
    forward = len(positions)
    if forward <= 0:
        raise ValueError(f"{_TAG} nothing to write ({n} source frames)")
    total_out = forward
    ratio = forward / max(1, n)
    part_path = path + ".part"
    LOG.info("%s %d segment(s), %d frames %dx%d -> %s (%s): %d frames @ %.3f fps%s; chunk size automatic",
             _TAG, len(src.indices), n, src.w, src.h, path, fmt, total_out, out_fps,
             f" (RIFE {source_fps:g}->{out_fps:g}, scale {scale}, batch {batch_size})" if do_interp else "")
    if fmt == "image/png-sequence" and os.path.isdir(part_path):
        shutil.rmtree(part_path, ignore_errors=True)
    if fmt in VIDEO_FORMATS:
        writer = StreamWriter(part_path, src.w, src.h, out_fps, audio, crf=crf, pix_fmt=pix_fmt, metadata=metadata,
                              codec="h265" if fmt == "video/h265-mp4" else "h264")
    else:
        writer = open_writer(fmt, part_path, src.w, src.h, out_fps, audio, crf, pix_fmt, metadata, loop_count)
    meta_png = metadata and fmt in LOOP_FORMATS
    peak_chunk, chunks, sizes = 0, 0, []
    state = {"cap": None, "events": []}

    def produce(j, size):
        if do_interp:
            _lo, _hi, _j0, j1, outs, size = interpolate_with_fallback(wrapper, src, positions, j, n, size,
                                                                     float(scale), int(batch_size), state)
            return j1, outs, None, size
        _lo, _hi, _j0, j1 = next_chunk(positions, j, n, size)
        frames = src.read(_lo, _hi)
        return j1, list(frames), frames, size

    try:
        with autocast_ctx(bool(use_fp16) and do_interp):
            j = 0
            while j < forward:
                check_interrupt()
                if chunk_frames:
                    size, why = max(1, int(chunk_frames)), "pinned"
                else:
                    size, why = auto_chunk(src.h, src.w, device, float(scale), fp16, int(batch_size), ratio,
                                           do_interp, state["cap"])
                j1, outs, frames, size = produce(j, size)
                if chunks == 0 or size != sizes[-1]:
                    LOG.info("%s chunk %d: %d source frames (%s)", _TAG, chunks + 1, size, why)
                sizes.append(size)
                peak_chunk = max(peak_chunk, _held_bytes(outs if frames is None else [frames] + outs))
                if meta_png and chunks == 0 and outs:
                    metadata_png(os.path.splitext(path)[0] + ".png", outs[0], metadata)
                writer.write(outs)
                del frames, outs
                chunks += 1
                j = j1
                if pbar is not None:
                    pbar.update_absolute(j1, total_out)
        writer.finish()
    except BaseException:
        writer.abort()
        src.close()
        try:
            if os.path.isdir(part_path):
                shutil.rmtree(part_path, ignore_errors=True)
            else:
                os.remove(part_path)
        except OSError:
            pass
        if meta_png:
            try:
                os.remove(os.path.splitext(path)[0] + ".png")
            except OSError:
                pass
        LOG.warning("%s failed or interrupted; partial file removed, cache KEPT at %s", _TAG, src.dir)
        raise
    src.close()
    if writer.frames != total_out:
        raise RuntimeError(f"{_TAG} wrote {writer.frames} frames, expected {total_out}; cache kept")
    os.replace(part_path, path)
    report = {"file": path, "frames": total_out, "fps": out_fps, "source_frames": n, "width": src.w, "height": src.h,
              "seconds": round(total_out / out_fps, 3), "interpolated": do_interp, "chunks": chunks, "format": fmt,
              "real_time": do_interp or abs(source_fps - out_fps) < 0.01,
              "chunk_sizes": sorted(set(sizes)), "oom_events": state["events"],
              "audio_seconds": round(writer.a_done / writer.sr, 3) if writer.a is not None else None,
              "peak_chunk_mb": round(peak_chunk / 1e6, 1), "cache_dir": src.dir, "elapsed_s": round(time.time() - t0, 1),
              "hard_cuts": cuts, "cut_frames_copied": cut_copies}
    if drift_rows is not None:
        report["drift_report"] = write_drift_report(path, drift_rows, drift_strength)
    LOG.info("%s saved %s", _TAG, report)
    return report


VIDEO_OUTPUT_FORMATS = ("video/h264-mp4", "video/h265-mp4", "image/gif")


def video_output(path, fmt):
    """'video' output: ComfyUI's VideoFromFile on the saved file (lazy - it opens the file only when a downstream
    node reads it). None for formats ComfyUI cannot read back as one video file (webp, png-sequence) or without
    comfy_api."""
    if fmt not in VIDEO_OUTPUT_FORMATS:
        LOG.info("%s 'video' output is empty for %s (use video_path)", _TAG, fmt)
        return None
    try:
        from comfy_api.latest import InputImpl
        return InputImpl.VideoFromFile(path)
    except Exception as exc:
        LOG.warning("%s 'video' output unavailable (%s); use video_path", _TAG, exc)
        return None


def preview_entry(file, subfolder, kind, fmt, fps):
    """'preview' output value for H3MD Video Preview; None when the file is outside ComfyUI's output/temp folder."""
    if subfolder is None:
        return None
    return {"filename": file, "subfolder": subfolder, "type": kind, "format": fmt, "frame_rate": float(fps),
            "first_frame": "frame_000001.png" if fmt == "image/png-sequence" else None}


def notify_ui(node_id, payload):
    """Push the cache folder to the node while it runs (web/js/h3md_save_video_interp.js); best effort."""
    try:
        from server import PromptServer
        PromptServer.instance.send_sync("h3md.save_video_interp", dict(payload, node=str(node_id)))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

class H3MDSaveVideoInterp:
    INTERP_WIDGETS = ("scale", "model_name", "batch_size", "use_fp16")  # greyed out when interpolate is off
    # widget layout (widgets_values order) of the first version (2026-10-18), re-mapped by name on load by
    # web/js/h3md_save_video_interp_ui.js -> remapSaveValues()
    LAYOUT_V1 = ("interpolate", "source_fps", "target_fps", "scale", "model_name", "batch_size", "use_fp16",
                 "filename_prefix", "output_folder", "crf", "preset", "delete_cache_after_save", "cache_folder",
                 "folder_overlap_source", "folder_audio_crossfade_ms")
    # later short-lived layouts (2026-10-19), also re-mapped by name in the frontend: v2 (17 values, with pingpong)
    # and v3 (16 values, pingpong removed, filename_prefix still after save_output). Now filename_prefix, output_folder are first.
    LAYOUT_V2 = ("interpolate", "target_fps", "scale", "model_name", "batch_size", "use_fp16", "format", "pix_fmt",
                 "crf", "loop_count", "pingpong", "trim_to_audio", "save_metadata", "save_output", "filename_prefix",
                 "output_folder", "delete_cache_after_save")
    LAYOUT_V3 = tuple(n for n in LAYOUT_V2 if n != "pingpong")
    # cache_folder / folder_overlap_source / folder_audio_crossfade_ms (manual cache re-save) were removed on
    # 2026-10-19: 'long video' from the Loop is the only source

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                # input id 'segments' kept (saved links / API prompts); shown as 'long video' (web/js label).
                # Required since 2026-10-19 (the manual cache_folder mode was removed).
                "segments": (SEGMENTS_TYPE, {"tooltip": "long video: H3MD Loop Sampler output 'long video'."}),
                "filename_prefix": ("STRING", {"default": "video/H3MD", "tooltip":
                                    "Like SaveVideo: <prefix>_00001_.mp4 (subfolders allowed, %date% vars)."}),
                "output_folder": ("STRING", {"default": "", "tooltip":
                                  "Empty = ComfyUI's output folder. A relative name = a subfolder of it. An absolute folder only if it is inside the output folder or listed under \"folders\" in <ComfyUI user folder>/h3md/config.json (v0.3.7)."}),
                "interpolate": ("BOOLEAN", {"default": False, "tooltip":
                                "On: RIFE frame interpolation (ComfyUI-VFI's model) 24 fps -> target_fps. Off: the "
                                "H3MD frames are written unchanged (the RIFE options below are greyed out)."}),
                # source_fps: read-only display "24" in the UI (H3 renders a fixed 24 fps), not an input
                "target_fps": ("FLOAT", {"default": SOURCE_FPS, "min": 1.0, "max": 240.0, "step": 0.1,
                               "display": "number", "tooltip":
                               "Frame rate of the saved file. interpolate on: RIFE target (e.g. 30, 48, 60). "
                               "interpolate off: the file is still written at this rate - anything but 24 plays "
                               "faster/slower than real time and the audio no longer matches (warned in the log)."}),
                # --- ComfyUI-VFI 'RIFE Frame Interpolation' options (same names / ranges / steps) ---
                "scale": ("FLOAT", {"default": 1.0, "min": 0.25, "max": 4.0, "step": 0.25, "display": "number",
                          "tooltip": "RIFE flow processing scale. Lower values process faster but may reduce quality "
                                     "(output size is unchanged)."}),
                "model_name": (vfi_model_names(), {"default": "flownet.pkl",
                               "tooltip": "RIFE model (ComfyUI-VFI's list / locations / auto download)."}),
                "batch_size": ("INT", {"default": 8, "min": 1, "max": 32, "step": 1, "display": "number",
                               "tooltip": "Frames interpolated in parallel (VRAM)."}),
                "use_fp16": ("BOOLEAN", {"default": True, "tooltip": "Half precision RIFE (CUDA only)."}),
                # --- output (VHS Video Combine style) ---
                "format": (list(FORMATS), {"default": "video/h264-mp4", "tooltip":
                           "h264/h265-mp4: video + AAC audio. gif / webp: animated image, no audio. png-sequence: a "
                           "folder of numbered PNGs, no audio. Every format is written chunk by chunk."}),
                "pix_fmt": (list(PIX_FMTS), {"default": "yuv420p", "tooltip":
                            "h264/h265-mp4 only. yuv420p = plays everywhere; yuv420p10le = 10 bit (less banding, "
                            "not every player)."}),
                "crf": ("INT", {"default": 19, "min": 0, "max": 51, "step": 1,
                        "tooltip": "h264/h265-mp4 only. Quality (default 19 like Video Combine; lower = better/larger)."}),
                "loop_count": ("INT", {"default": 0, "min": 0, "max": 100, "step": 1, "tooltip":
                               "gif / webp only: 0 = loop forever, n = play n more times (like Video Combine)."}),
                "trim_to_audio": ("BOOLEAN", {"default": False, "tooltip":
                                  "h264/h265-mp4 only: end the video with the audio (like Video Combine)."}),
                "save_metadata": ("BOOLEAN", {"default": True, "tooltip":
                                  "Embed the prompt + workflow (like Video Combine): mp4 tags, first PNG of a sequence; "
                                  "gif / webp: an extra <file>.png first frame carries them."}),
                "save_output": ("BOOLEAN", {"default": True, "tooltip":
                                "On: save into the output folder (or output_folder). Off: ComfyUI's temp folder "
                                "(cleared on restart) - still previewable below."}),
                "delete_cache_after_save": ("BOOLEAN", {"default": False, "tooltip":
                                            "Delete the H3MD segment cache after the video file was fully written. "
                                            "Off (default): keep it, e.g. to re-render single segments with "
                                            "resume_from_cache or to save again. The folder is shown below."}),
            },
            "optional": dict(DRIFT_INPUTS),   # v0.2.0, appended LAST (old workflows load with drift_correct off)
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    # 2026-10-19: slot 2 was 'fps' (FLOAT); now 'video' = ComfyUI's VIDEO type backed by the saved file
    # (VideoFromFile: lazy, no frames in RAM) for core Get Video Components / Save Video / ...
    # mp4 and gif only; webp / png-sequence -> None (logged).  Saved links from the old fps slot are dropped
    # on load (web/js/h3md_long_video.js).
    # 2026-10-23: 'preview' appended LAST (H3MD Video Preview); the built-in player was removed
    RETURN_TYPES = ("STRING", "INT", "VIDEO", PREVIEW_TYPE)
    RETURN_NAMES = ("video_path", "frame_count", "video", "preview")
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Saves the H3MD Loop result straight from its segment cache (mp4 H.264/H.265 + AAC, gif, webp or "
                   "a PNG sequence), chunk by chunk (automatic size from free VRAM / RAM) - RAM never holds the whole "
                   "clip. Optional RIFE interpolation (ComfyUI-VFI's model and options). Replaces RIFE Frame "
                   "Interpolation + Video Combine / Create Video + Save Video after H3MD Loop.")

    def save(self, interpolate, target_fps, scale, model_name, batch_size, use_fp16, filename_prefix, output_folder,
             crf, delete_cache_after_save, format="video/h264-mp4", pix_fmt="yuv420p", loop_count=0,
             save_metadata=True, trim_to_audio=False, save_output=True, segments=None, prompt=None, extra_pnginfo=None, unique_id=None,
             drift_correct="off", drift_strength=1.0, **removed):
        fmt = format
        if removed:
            unknown = sorted(set(removed) - set(REMOVED_INPUTS))
            if unknown:
                raise TypeError(f"{_TAG} unexpected inputs {unknown}")
            LOG.info("%s ignored removed input(s) %s (source_fps is fixed 24; preset = encoder default; "
                     "cache_folder mode removed; pingpong removed)",
                     _TAG, sorted(removed))
        if fmt not in FORMATS:
            raise ValueError(f"{_TAG} unknown format {fmt!r} (one of {', '.join(FORMATS)})")
        if not isinstance(segments, dict):
            raise ValueError(f"{_TAG} connect H3MD Loop Sampler 'long video' (needs cache_mode=disk, decode_output on)")
        info = segments
        cache_dir = os.path.abspath(info["cache_dir"])
        notify_ui(unique_id, {"cache_dir": cache_dir, "state": "writing"})
        if info.get("hires_mode") == "final_upscale":
            LOG.warning("%s the loop ran hires_mode=final_upscale: its cache holds the LOW-RES preview frames", _TAG)
        src = SegmentSource(info)
        w, h, n_est = src.w, src.h, src.total
        src.close()
        path, file, subfolder, kind = save_path(filename_prefix, output_folder, w, h, EXTENSIONS[fmt],
                                                temp=not save_output)
        meta = None
        try:
            from comfy.cli_args import args
            disabled = bool(getattr(args, "disable_metadata", False))
        except Exception:
            disabled = False
        if not disabled and save_metadata:
            meta = dict(extra_pnginfo or {})
            if prompt is not None:
                meta["prompt"] = prompt
            meta = meta or None
        pbar = progress_bar(max(1, n_est), unique_id)
        try:
            rep = stream_save(info, path, interpolate, target_fps, scale, model_name, batch_size, use_fp16, crf, meta,
                              pbar=pbar, fmt=fmt, pix_fmt=pix_fmt, loop_count=loop_count,
                              trim_to_audio=trim_to_audio, drift_correct=drift_correct, drift_strength=drift_strength)
        except BaseException:
            notify_ui(unique_id, {"cache_dir": cache_dir, "state": "kept (save failed)"})
            raise
        try:
            import comfy.model_management as mm
            mm.soft_empty_cache()
        except Exception:
            pass
        state = "kept"
        written = (os.path.isdir(path) and any(f.endswith(".png") for f in os.listdir(path))) if fmt == "image/png-sequence" \
            else (os.path.isfile(path) and os.path.getsize(path) > 0)
        if delete_cache_after_save and written:
            state = "deleted after save" if delete_cache_dir(cache_dir) else "kept (delete refused, see log)"
        else:
            LOG.info("%s cache kept at %s", _TAG, cache_dir)
        ui = {"h3md_cache_dir": [cache_dir], "h3md_cache_state": [state]}
        # no built-in player any more: the saved file goes to the 'preview' output (H3MD Video Preview)
        preview = preview_entry(file, subfolder, kind, fmt, rep["fps"])
        return {"ui": ui, "result": (path, int(rep["frames"]), video_output(path, fmt), preview)}


# Only one id: nothing with the earlier working name H3MDStreamSaveVideo was ever deployed or saved.
NODE_CLASS_MAPPINGS = {"H3MDSaveVideoInterp": H3MDSaveVideoInterp}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDSaveVideoInterp": "H3MD Save Video Interp"}
