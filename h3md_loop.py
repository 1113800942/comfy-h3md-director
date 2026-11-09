"""H3MD Loop Sampler (H3MDLoopSampler) - runs every segment of an H3MD plan in one node.

Per segment (plan["segments"] = runnable segments, i.e. shots already expanded into automatic
sub-segments by the Director):
  1. H3MD Segment Encode (official MiniMax H3 encoder, native-resolution refs, one <=15 s window)
  2. continuation (overlapIn > 0): H3MD Segment Continue with previous = the previous segment's
     FINAL two-pass latent ('samples' only);
     hard cut (overlapIn == 0): no previous latent at all (locked audio is still applied)
  3. H3MD Sampler (two-pass on the bundled Luna engine, VRAM release between the passes, high-res tiling)
  4. between segments: unload idle models (text encoder / VAEs / upscaler leftovers) and
     soft_empty_cache; clones of the shared base UNet are never unloaded
Decoded output (default cache_mode=disk): each segment is decoded right after it is sampled and written
to a per-segment disk cache (h3md_cache.py), so RAM only ever holds one decoded segment.  Before the
final merge every model is unloaded (sampling is done).  Since 2026-10-18 (late) the frames are NOT read back
into the IMAGE output any more: 'images' is a 64x64 placeholder and the 6th output 'long video' (cache folder +
merge plan) goes to H3MD Save Video Interp, which writes the video chunk by chunk.  cache_mode=ram keeps the
original behaviour (decode + join in RAM after all segments, 'images' = the clip, 'long video' = None).
Then all segments are joined: continuation seams drop the copied overlap from the new segment,
cut seams are joined end to end.  Outputs the joined AV latent and (optionally) decoded frames +
audio joined exactly like H3MD Segment Join.

All the actual work is delegated to the existing H3MD nodes/functions, so a loop run is the same
computation as the manual Encode -> Continue -> Sampler -> Join chain.
"""

from __future__ import annotations

import json
import logging
import os

import torch

import gc

BETA = "native_lowres_carry"

try:
    from . import h3md_director as D
    from . import h3md_sampler as S
    from . import h3md_vram
    from . import h3md_cache as C
    from . import h3md_seam as SEAM          # H3MD seam/refwindow features
    from . import h3md_refwindow as RW
    from . import h3md_semantic_bridge as SB  # H3MD semantic-bridge hook
    from . import h3md_stream_save as SS          # H3MD stream save ('long video' output)
    from . import h3md_depth_control as DC        # H3MD depth ControlNet (optional input depth_control)
    from . import h3md_lowres_anchor as LA        # H3MD low-res anchor (2026-10-30)
    from . import h3md_window as WIN              # window-exp branch (2026-11-07)
    from . import h3md_progress as PROG           # window-exp: per-step progress cache
except ImportError:  # flat import (tests)
    import h3md_director as D
    import h3md_sampler as S
    import h3md_vram
    import h3md_cache as C
    import h3md_seam as SEAM
    import h3md_refwindow as RW
    import h3md_semantic_bridge as SB
    import h3md_stream_save as SS
    import h3md_depth_control as DC
    import h3md_lowres_anchor as LA
    import h3md_window as WIN
    import h3md_progress as PROG

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Loop]"
CATEGORY = "MiniMax H3/H3MD"
_SAMPLER_COMPUTED = ("positive", "negative", "latent_image")


# ---------------------------------------------------------------------------
# hooks (monkeypatched in tests)
# ---------------------------------------------------------------------------

def decode_video(vae, video: torch.Tensor) -> torch.Tensor:
    """Same as core VAEDecode on the video stream."""
    images = vae.decode(video)
    if images.ndim == 5:
        images = images.reshape(-1, images.shape[-3], images.shape[-2], images.shape[-1])
    return images


def decode_audio(audio_vae, audio: torch.Tensor) -> dict:
    """Same as core VAEDecodeAudio on the audio stream."""
    from comfy_extras.nodes_audio import vae_decode_audio
    return vae_decode_audio(audio_vae, {"samples": audio})


# --- H3MD-Exp: seam decode context (idea from pxl-pshr/ComfyUI-MiniMaxH3-Loop's wrapped-context decode) ---
# A continued segment is decoded on its own, so its frame 0 (the visible seam with merge_overlap_source=next)
# is where the video VAE starts cold.  With seam_decode_context = c > 0, c whole 17-frame clips (5 latents each)
# of the PREVIOUS final latent, taken right before the overlap, are prepended for the decode only; the 17*c
# extra frames are dropped again.  Frame count, latents, audio and hard cuts are unchanged.  0 = off (old).
SEAM_DECODE_CONTEXT_MAX = 2
CLIP_TOKENS = 5          # latents per 17-frame clip (1,4,4,4,4)
CLIP_FRAMES = 17


def decode_video_seam_context(vae, video: torch.Tensor, prev: dict | None, trim: int, clips: int,
                              where: str = "") -> torch.Tensor:
    """decode_video(vae, video), but with `clips` clips of the previous final latent as leading VAE context
    when this segment continues (trim > 0).  Falls back to the plain decode on anything unexpected."""
    clips, trim = int(clips or 0), int(trim or 0)
    if clips <= 0 or trim <= 0 or prev is None:
        return decode_video(vae, video)
    try:
        pv, _ = D._streams({"samples": prev["samples"]}, "previous_latent")
    except Exception as exc:
        LOG.warning("%s %s: seam_decode_context skipped (previous latent unreadable: %s)", _TAG, where, exc)
        return decode_video(vae, video)
    n_new, start = int(video.shape[2]), int(pv.shape[2]) - D.video_tokens(trim)
    if (video.ndim != 5 or pv.ndim != 5 or video.shape[0] != 1 or start < 0 or start % CLIP_TOKENS
            or (n_new - 2) % CLIP_TOKENS or pv.shape[1] != video.shape[1] or tuple(pv.shape[3:]) != tuple(video.shape[3:])):
        LOG.warning("%s %s: seam_decode_context skipped (previous %s / segment %s / overlap %df not clip-aligned)",
                    _TAG, where, tuple(pv.shape), tuple(video.shape), trim)
        return decode_video(vae, video)
    c = min(clips, start // CLIP_TOKENS)
    if c <= 0:
        return decode_video(vae, video)
    ctx = pv[:1, :, start - c * CLIP_TOKENS:start].to(device=video.device, dtype=video.dtype)
    imgs = decode_video(vae, torch.cat([ctx, video], dim=2))
    drop = CLIP_FRAMES * c
    expected = (n_new - 2) // CLIP_TOKENS * CLIP_FRAMES + 5
    if imgs.shape[0] != drop + expected:
        LOG.warning("%s %s: seam_decode_context decoded %d frames, expected %d + %d context; plain decode used",
                    _TAG, where, imgs.shape[0], expected, drop)
        del imgs
        return decode_video(vae, video)
    LOG.info("%s %s: seam_decode_context %d clip(s) (%d frames) of the previous latent decoded before the seam, "
             "dropped again", _TAG, where, c, drop)
    return imgs[drop:]


# --- window-exp (11-07): chunked decode of a long window-mode group ---
# A group (up to ~60 s) decoded in one piece needed ~3x its frames in RAM (68 GB for 60 s at 1088x1664).  Here the
# latent is decoded a few clips at a time, straight into the disk cache: each chunk starts CLIP-aligned with
# DECODE_CTX_CLIPS clips of the latent right before it as VAE warm-up (dropped again, as seam_decode_context does) and
# ends 2 latents into the next clip (a valid 5n+2 stream), whose 5 frames are dropped again.  RAM ~ one chunk.
DECODE_CHUNK_CLIPS = 14   # ~238 frames (~10 s) per chunk
DECODE_CTX_CLIPS = 2


class ChunkedFrames:
    """[N,H,W,3] frames of a [1,C,5n+2,h,w] video latent, decoded chunk by chunk on access (slices only).  Behaves
    like the decoded tensor for h3md_cache.SegmentCache.write and for tail slices."""

    def __init__(self, vae, video: torch.Tensor, chunk_clips: int = DECODE_CHUNK_CLIPS, ctx: int = DECODE_CTX_CLIPS,
                 decode=None, reuse=None):
        if video.ndim != 5 or video.shape[0] != 1 or (int(video.shape[2]) - 2) % CLIP_TOKENS:
            raise ValueError(f"not a 5n+2 video latent: {tuple(video.shape)}")
        self.vae, self.video, self.ctx = vae, video, int(ctx)
        self.decode = decode or decode_video
        self.n_clips = (int(video.shape[2]) - 2) // CLIP_TOKENS
        self.chunk = max(1, int(chunk_clips))
        self.n = self.n_clips * CLIP_FRAMES + 5
        self._cur = None    # (first frame, frames)
        self._hw = None
        # local redraw: (old frames .npy of the same latent, (first, end) changed latents): a chunk whose latents
        # (warm-up included) are all unchanged is copied from the old frames instead of decoded (identical result)
        self._reuse, self._old = reuse, None
        self.reused = self.decoded = 0

    def _old_frames(self, j0: int, want: int):
        if self._old is None:
            import numpy as np
            try:
                self._old = np.load(self._reuse[0], mmap_mode="r")
                if self._old.ndim != 4 or self._old.shape[0] != self.n:
                    self._old = False
            except Exception:
                self._old = False
        if self._old is False:
            return None
        import numpy as np
        part = np.asarray(self._old[CLIP_FRAMES * j0:CLIP_FRAMES * j0 + want])
        fr = torch.from_numpy(part.astype(np.float32))
        return fr / 255.0 if part.dtype == np.uint8 else fr

    def _decode_chunk(self, j0: int):
        j1 = min(self.n_clips, j0 + self.chunk)
        c = min(self.ctx, j0)
        last = j1 == self.n_clips
        if self._reuse is not None:
            l0, l1 = self._reuse[1]
            if l1 <= CLIP_TOKENS * (j0 - c) or l0 >= CLIP_TOKENS * j1 + 2:
                old = self._old_frames(j0, CLIP_FRAMES * (j1 - j0) + (5 if last else 0))
                if old is not None:
                    self.reused += 1
                    self._cur = (CLIP_FRAMES * j0, old)
                    return self._cur
        self.decoded += 1
        lat = self.video[:, :, CLIP_TOKENS * (j0 - c):CLIP_TOKENS * j1 + 2]
        imgs = self.decode(self.vae, lat)
        want = CLIP_FRAMES * (j1 - j0) + (5 if last else 0)
        if imgs.shape[0] != CLIP_FRAMES * (j1 - j0 + c) + 5:
            raise RuntimeError(f"chunk decode gave {imgs.shape[0]} frames, expected {CLIP_FRAMES * (j1 - j0 + c) + 5}")
        out = imgs[CLIP_FRAMES * c:CLIP_FRAMES * c + want].to("cpu")
        del imgs
        self._cur = (CLIP_FRAMES * j0, out)
        return self._cur

    def _frames(self, a: int, b: int):
        parts = []
        while a < b:
            if self._cur is None or not (self._cur[0] <= a < self._cur[0] + self._cur[1].shape[0]):
                self._decode_chunk(min(a // CLIP_FRAMES, self.n_clips - 1) // self.chunk * self.chunk)
            f0, fr = self._cur
            e = min(b, f0 + fr.shape[0])
            parts.append(fr[a - f0:e - f0])
            a = e
        return parts[0] if len(parts) == 1 else torch.cat(parts, 0)

    @property
    def shape(self):
        if self._hw is None:
            self._hw = tuple(self._frames(0, 1).shape[1:])
        return (self.n,) + self._hw

    def __getitem__(self, idx):
        if not isinstance(idx, slice):
            raise TypeError("ChunkedFrames supports slices only")
        a, b, step = idx.indices(self.n)
        if step != 1:
            raise TypeError("ChunkedFrames supports step-1 slices only")
        return self._frames(a, b) if b > a else self._frames(0, 1)[:0]


def chunked_decode_ok(video: torch.Tensor, window_mode) -> bool:
    """Chunked decode for window-mode segments longer than one chunk."""
    return bool(window_mode) and video.ndim == 5 and (int(video.shape[2]) - 2) % CLIP_TOKENS == 0 and \
        (int(video.shape[2]) - 2) // CLIP_TOKENS > DECODE_CHUNK_CLIPS


def encode_video(vae, frames: torch.Tensor) -> torch.Tensor:
    """Same as core VAEEncode on [N,H,W,3] frames -> [1,24,T,h,w] (H3 video VAE)."""
    return vae.encode(frames[..., :3])


def check_interrupt():
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


def unload_all_models(reason=""):
    """Sampling finished: free everything (base UNet included) before the merge / RIFE."""
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache()
        LOG.info("%s unloaded all models + soft_empty_cache %s", _TAG, reason)
    except Exception as exc:
        LOG.warning("%s unload_all_models failed: %s", _TAG, exc)
    gc.collect()


def progress_bar(total, node_id=None):
    try:
        import comfy.utils
        return comfy.utils.ProgressBar(total, node_id=node_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def segment_range(plan: dict, stop_after: int = 0):
    """H3MD rerun (2026-10-30): segments 1..stop_after (1-based, 0 = all) -> 0-based indices."""
    n = len(plan["segments"])
    stop = int(stop_after or 0)
    if stop < 0 or stop > n:
        raise ValueError(f"{_TAG} stop_after {stop} invalid: the plan has {n} segment(s) (0 = all). "
                         f"stop_after {stop} 无效：本分镜共 {n} 段（0 = 全部）。")
    return list(range(stop if stop > 0 else n))


def model_lora_sig(low, high) -> dict:
    """0.3.4: LoRAs recorded on the models by H3MD LowVRAM LoRA Loader / H3MD Control Panel (model_options
    'h3md_lora_sig').  Changing one of them now changes the segment cache keys (before, a resume reused the old
    segments).  Core loaders are not recorded.  Empty -> {} (keys exactly as before)."""
    out = {}
    for name, m in (("low", low), ("high", high)):
        sig = ((getattr(m, "model_options", None) or {}).get("h3md_lora_sig")) if m is not None else None
        if sig:
            out[name] = sig
    return out


def parse_segments(text: str, n: int) -> list:
    """'8-10, 13-14, 16' (1-based, as in the log and the Director) -> sorted 0-based indices.  '' -> []."""
    raw = str(text or "").strip()
    if not raw:
        return []
    for a, b in (("，", ","), ("、", ","), ("；", ","), (";", ","), ("～", "-"), ("~", "-"), ("–", "-"), ("—", "-"),
                 ("到", "-")):
        raw = raw.replace(a, b)
    out = set()
    for part in raw.replace(" ", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
            else:
                lo = hi = int(part)
        except ValueError:
            raise ValueError(f"{_TAG} rerun_segments: cannot read '{part}' (write e.g. 3 or 4-6 or 8-10, 13-14). "
                             f"重跑段落写法有误：'{part}'（示例：3、4-6、8-10, 13-14）。") from None
        if lo > hi:
            lo, hi = hi, lo
        if lo < 1 or lo > n:
            raise ValueError(f"{_TAG} rerun_segments: {part} is outside segments 1..{n}. "
                             f"重跑段落 {part} 超出范围（本分镜共 {n} 段，从 1 数）。")
        if hi > n:   # 0.3.4: a range running past the end is cut there (e.g. 1-2 on a 1-segment plan)
            LOG.warning("%s rerun_segments: %s runs past segment %d (the plan has %d); using %d-%d. 重跑段落 %s 超出"
                        "段数（共 %d 段），已按 %d-%d 处理。", _TAG, part, n, n, lo, n, part, n, lo, n)
            hi = n
        out.update(range(lo - 1, hi))
    return sorted(out)


def cache_usable(cache, k: int, segs: list, beta: bool) -> bool:
    """Segment k (0-based) can be taken from the cache as it is (same check as the rerun pre-check)."""
    man = cache.manifest(k) if cache is not None else None
    if man is None or int(man.get("frames", -1)) != int(segs[k]["length"]):
        return False
    return not (beta and not man.get("lowres"))


def plan_segments_to_run(text: str, segs: list, cache, beta: bool):
    """v0.3 'segments_to_run' (H3MD Control Panel): one box instead of rerun_segments + stop_after.
    Empty -> None (normal run).  '2' / '1-2' / '3, 6-7, 9' -> (stop_after, rerun list, log line):
    run up to the highest one; the listed segments are generated again even if cached; earlier segments missing
    from the cache are generated too (a continuation needs the segment before it)."""
    sel = parse_segments(text, len(segs))
    if not sel:
        return None
    stop = max(sel) + 1
    forced = [k for k in sel if cache_usable(cache, k, segs, beta)]
    missing = [k for k in range(stop) if k not in sel and not cache_usable(cache, k, segs, beta)]
    later_cached = all(cache_usable(cache, k, segs, beta) for k in range(stop, len(segs)))
    if forced and later_cached:
        stop = len(segs)   # a repair inside a finished video: keep the whole video (later segments from the cache)
    rerun = sorted(set(sel) | set(missing)) if forced else []
    one = lambda ks: ", ".join(str(k + 1) for k in ks) or "-"   # noqa: E731
    line = (f"segments_to_run '{text}': run up to segment {stop}; listed: {one(sel)} (cached, generated again: "
            f"{one(forced)}); missing earlier segments generated too: {one(missing)}; the rest from the cache")
    return stop, rerun, line


def describe_rerun(selected: list, indices: list, segs: list, tail_from: dict) -> str:
    """One line per segment for the log: cache / rerun (head from / tail locked to)."""
    lines = []
    for k in indices:
        if k not in selected:
            lines.append(f"  {k + 1}: cache")
            continue
        head = (f"head <- {k}" if segs[k]["overlapIn"] > 0 and k > 0 else "no head (first / cut)")
        tail = f"tail -> {tail_from[k] + 1}" if k in tail_from else "no tail lock"
        lines.append(f"  {k + 1}: RERUN ({head}, {tail})")
    return "\n".join(lines)


def join_latents(latents: list, trims: list) -> dict:
    """Concatenate final AV latents. trims[k] = overlap frames dropped from the head of latents[k]
    (0 for the first segment and for hard cuts)."""
    videos, audios = [], []
    for lat, trim in zip(latents, trims):
        v, a = D._streams(lat, "segment latent")
        vt = D.video_tokens(trim) if trim > 0 else 0
        at = min(D.audio_tokens(trim), a.shape[-1]) if trim > 0 else 0
        videos.append(v[:, :, vt:])
        audios.append(a[..., at:])
    dev = videos[0].device
    video = torch.cat([v.to(dev) for v in videos], dim=2)
    audio = torch.cat([a.to(audios[0].device) for a in audios], dim=-1)
    return {"samples": D._nested((video, audio))}


low_res_hw = S.low_res_hw   # the engine's low-res latent grid


def low_res_prefix(vae, prev: dict, overlap: int, target_latent: dict, lowres_scale: float,
                   tail_frames: torch.Tensor | None = None, where: str = ""):
    """Proper low-resolution latent of the continuation overlap, for the sampler's low-res pass.

    Without it the engine builds its low-res kept prefix by trilinear-resizing the FULL-res final latent in latent
    space.  A 2x latent-space downsample of an H3 latent (16x VAE, 24 ch) is not a latent of the downscaled
    video: decoded it is washed-out / ghosted, and because the kept tokens are fed to H3 as clean (t=0)
    context the low-res stage continues that look for the whole new segment.  Here the overlap frames
    (decoded from the previous FINAL latent - nothing else is handed over) are downscaled in pixel space and
    re-encoded with the video VAE.  Returns [1,24,vt,h,w] or None (scale 1 / mismatch -> engine default)."""
    v_target, _ = D._streams(target_latent, "latent")
    H, W = v_target.shape[-2:]
    h, w = low_res_hw((H, W), lowres_scale)
    if (h, w) == (H, W):
        return None
    vt = D.video_tokens(overlap)
    if tail_frames is not None and tail_frames.shape[0] >= overlap:
        frames = tail_frames[-overlap:]
    else:
        pv, _ = D._streams({"samples": prev["samples"]}, "previous_latent")
        frames = decode_video(vae, pv[:1, :, -vt:])
        if frames.shape[0] != overlap:
            LOG.warning("%s %s: decoded %d overlap frames, expected %d", _TAG, where, frames.shape[0], overlap)
            frames = frames[-overlap:]
    ry, rx = frames.shape[1] / H, frames.shape[2] / W
    ph, pw = int(round(h * ry)), int(round(w * rx))
    x = frames[..., :3].movedim(-1, 1).float()
    x = torch.nn.functional.interpolate(x, size=(ph, pw), mode="bilinear", align_corners=False, antialias=True)
    small = x.movedim(1, -1).clamp(0.0, 1.0)
    z = encode_video(vae, small)
    if z.ndim != 5 or z.shape[1] != v_target.shape[1] or z.shape[2] != vt or tuple(z.shape[-2:]) != (h, w):
        LOG.warning("%s %s: re-encoded low-res prefix %s != expected (1,%d,%d,%d,%d); using the engine's latent resize",
                    _TAG, where, tuple(z.shape), v_target.shape[1], vt, h, w)
        return None
    LOG.info("%s %s: low-res overlap prefix re-encoded from %d frames at %dx%d -> latent %s",
             _TAG, where, frames.shape[0], pw, ph, tuple(z.shape))
    return z.to(device="cpu", dtype=v_target.dtype)


def hires_anchor_step(final: dict, ref, continued: bool, overlap: int, strength: float, where: str = "",
                      tail_frames: int = 0):
    """H3MD hi-res anchor (v0.2.0).  A shot's first segment sets the reference (per-channel mean / std of its final
    high-res video latent); every continued segment's NEW latent steps (not the kept overlap prefix) are pulled toward
    it with LA.transform / LA.apply - the same correction lowres_anchor makes on the low-res result.  Because the
    corrected final is what the next segment continues from, the error cannot pile up.  -> (final, ref, report|None)"""
    try:
        v, a = D._streams(final, "final latent")
    except ValueError as exc:
        LOG.warning("%s %s: hires_anchor skipped (%s)", _TAG, where, exc)
        return final, ref, None
    if not continued or ref is None:
        if continued:
            LOG.warning("%s %s: hires_anchor has no reference (the shot's first segment was not seen); not applied",
                        _TAG, where)
            return final, ref, None
        return final, LA.stats(v), None
    T = int(v.shape[2])
    held = torch.zeros(T, dtype=torch.bool)
    held[:min(T, D.video_tokens(int(overlap)))] = True
    if tail_frames:  # H3MD rerun tail lock: the kept tail must stay exactly the next segment's head
        held[max(0, T - D.video_tokens(int(tail_frames))):] = True
    xf = LA.transform(v, held, ref, strength)
    if xf is None:
        return final, ref, None
    v2 = LA.apply(v, xf)
    LOG.info("%s %s: hi-res anchor: %d new latent steps pulled to the shot's first segment (strength %.2f, channel "
             "mean shift %.3f std, spread ratio %.2f-%.2f)", _TAG, where, xf["report"]["new_steps"],
             xf["report"]["strength"], xf["report"]["mean_shift"], *xf["report"]["std_ratio"])
    out = dict(final)
    out["samples"] = D._nested((v2, a))
    return out, ref, xf["report"]


def native_carry_prefix(prev_lowres, overlap: int, target_latent: dict, lowres_scale: float, where: str = ""):
    """Beta: overlap prefix for the low-res stage = tail of the previous segment's own low-res endpoint.

    prev_lowres is the previous segment's low-res pass endpoint (VAE-native space, like latent['samples']), on its
    native low-res grid - nothing is resized.  Returns [1,24,vt,h,w] or (None, reason) -> caller falls back to Alpha."""
    v_target, _ = D._streams(target_latent, "latent")
    H, W = v_target.shape[-2:]
    h, w = low_res_hw((H, W), lowres_scale)
    vt = D.video_tokens(overlap)
    if prev_lowres is None:
        return None, "no low-res carry from the previous segment"
    if not isinstance(prev_lowres, torch.Tensor) or prev_lowres.ndim != 5:
        return None, f"low-res carry has unexpected type/shape {getattr(prev_lowres, 'shape', type(prev_lowres))}"
    T = int(prev_lowres.shape[2])
    if prev_lowres.shape[1] != v_target.shape[1] or tuple(prev_lowres.shape[-2:]) != (h, w):
        return None, f"low-res carry {tuple(prev_lowres.shape)} != this segment's low-res grid (C={v_target.shape[1]}, {h}x{w})"
    if vt > T or vt >= v_target.shape[2]:
        return None, f"overlap {overlap}f ({vt} tokens) does not fit the carry ({T} tokens)"
    if (T - vt) % 5 != 0:  # FRAME_PER_TOKEN (1,4,4,4,4): the tail must start on a 1-frame token like token 0
        return None, f"carry tail of {vt} tokens is not aligned to the 5-token grid (carry T={T})"
    z = prev_lowres[:1, :, T - vt:].to(device="cpu", dtype=v_target.dtype).clone()
    LOG.info("%s %s: low-res overlap prefix = native low-res carry %s (tokens %d..%d of %d)",
             _TAG, where, tuple(z.shape), T - vt, T - 1, T)
    return z, None


# 'images' placeholder (disk cache): the clip is never concatenated in RAM; 'long video' -> H3MD Save Video Interp.
# merge_images (a main-node widget for a few hours on 2026-10-18) was removed: the Loop no longer stitches the
# full video. It was the LAST widget, so workflows saved with it load unchanged (the extra trailing value is ignored);
# run() still accepts and ignores a merge_images value (API-format prompts).
LONG_VIDEO = "long video"            # display name of output 5 (was 'segments'; same slot / type H3MD_SEGMENTS)
IMAGES_SLOT, LONG_VIDEO_SLOT = 1, 5
IMAGES_PLACEHOLDER_WARNING = ("'images' no longer carries the video (64x64 placeholder): connect 'long video' to "
                              "H3MD Save Video Interp")

# Notes on the outputs (DESCRIPTION, output tooltips, README, notes/H3MD_outputs_note.*) - documentation only.
OUTPUTS_NOTE_EN = (
    "Outputs: only 'long video' -> H3MD Save Video Interp is designed to stay within bounded VRAM/RAM (it streams "
    "chunk by chunk from the disk cache). 'latent' (the joined audio-video latent) and 'audio' (the joined audio) "
    "can also be connected to other nodes for upscaling / custom processing, e.g. Separate AV Latent -> Hyper-VAE "
    "or a VAE decoder -> VHS Video Combine. 'images' is only a 64x64 placeholder (merge_images was removed): to get "
    "frames, decode 'latent' externally. WARNING: for large / long videos the latent / decoded-image / audio paths "
    "do NOT guarantee against VRAM/RAM blowups - decoding the full latent externally holds the whole clip in memory.")
OUTPUTS_NOTE_ZH = (
    "输出说明：只有 'long video' → H3MD Save Video Interp 是按有限显存/内存设计的（从磁盘缓存一段一段流式处理）。"
    "'latent'（拼好的音视频 latent）和 'audio'（拼好的音频）也可以接到别的节点做放大或自定义处理，例如 "
    "Separate AV Latent → Hyper-VAE 或 VAE 解码器 → VHS Video Combine。'images' 只是 64x64 占位图"
    "（merge_images 已删除）：要拿到画面帧，请在外部解码 'latent'。警告：大/长视频走 latent / 解码图片 / audio "
    "这些路径不保证显存/内存不爆——在外部解码整段 latent 会把整段视频放进内存。")
OUTPUT_TOOLTIPS = (
    "Joined audio-video latent of all segments (seam overlaps removed). Can be connected externally (e.g. Separate "
    "AV Latent -> Hyper-VAE / VAE Decode -> VHS Video Combine) for upscaling or custom processing. NOT bounded: "
    "decoding the whole latent holds the whole clip in VRAM/RAM on long videos. Bounded path: 'long video' -> "
    "H3MD Save Video Interp.\n拼好的音视频 latent，可接外部节点（如 Hyper-VAE / VAE 解码 → VHS Video Combine）放大或"
    "自定义处理；长视频整段解码可能爆显存/内存。",
    "Placeholder only (64x64; merge_images was removed) - NOT the video. For frames, decode 'latent' externally, or "
    "save with 'long video' -> H3MD Save Video Interp. (cache_mode=ram: still the whole clip in RAM.)\n"
    "只是占位图，不是视频；要画面帧请在外部解码 'latent'，或用 'long video' → H3MD Save Video Interp 出片。",
    "Joined audio of the whole clip (the same audio H3MD Save Video Interp writes). Can be connected externally for "
    "custom processing; not a bounded-memory path on large / long videos.\n"
    "整段拼好的音频，可接外部节点自定义处理；大/长视频不保证不爆内存。",
    "Number of video frames of the joined clip (24 fps).",
    "JSON report of every segment.",
    "Cache folder + merge plan for H3MD Save Video Interp - the only output designed to stay within bounded "
    "VRAM/RAM (streamed chunk by chunk from the disk cache). None with cache_mode=ram.\n"
    "接 H3MD Save Video Interp：唯一按有限显存/内存设计的出片路径。",
)



def images_used_downstream(prompt, unique_id) -> list:
    """Node ids in the queued prompt (API format) that take this Loop's 'images' output (slot 1)."""
    if not isinstance(prompt, dict) or unique_id is None:
        return []
    me, users = str(unique_id), []
    for nid, node in prompt.items():
        ins = node.get("inputs", {}) if isinstance(node, dict) else {}
        for v in ins.values():
            if isinstance(v, (list, tuple)) and len(v) == 2 and str(v[0]) == me and v[1] == IMAGES_SLOT:
                users.append(str(nid))
                break
    return users


def step_summary(steps) -> dict | None:
    """Per-pass summary of the sampler's per-step log: steps, total / median step seconds, peak VRAM (GiB)."""
    if not steps:
        return None
    out = {}
    for stage in ("low_res", "high_res"):
        rows = [r for r in steps if r.get("stage") == stage]
        if not rows:
            continue
        secs = sorted(r["sec"] for r in rows[1:]) or [rows[0]["sec"]]   # step 1 includes model loading
        peaks = [r["peak_gib"] for r in rows if r.get("peak_gib") is not None]
        out[stage] = {"steps": len(rows), "total_s": round(sum(r["sec"] for r in rows), 1),
                      "median_s": round(secs[len(secs) // 2], 2), "peak_gib": max(peaks) if peaks else None}
    return out


def _to_cpu(latent: dict) -> dict:
    s = latent["samples"]
    try:
        s = s.to("cpu")
    except Exception as exc:                                       # keep going on the device, but say so
        LOG.warning("H3MD could not move a latent to CPU (%s); keeping it on %s", exc, getattr(s, "device", "?"))
    return {"samples": s}


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

def check_redraw_base(cache, k, man: dict, seg: dict, where=""):
    """Local redraw needs this segment finished in the cache with the same length and hard cuts (0.4.0: the
    prompt, pictures and reference media may differ - only the redrawn seconds use the new ones).  The frame size is
    checked by redraw_latent."""
    if not man.get("key") or not cache.has(k, man["key"]):
        raise ValueError(f"{_TAG} {where}: local redraw needs this segment finished in the cache (run it once without "
                         f"'redraw', with resume_from_cache on); cache {cache.dir}. 局部重绘需要先把这一段完整跑过一次"
                         f"（打开 resume_from_cache），缓存里没有。")
    if int(man.get("frames", -1)) != int(seg["length"]):
        raise ValueError(f"{_TAG} {where}: local redraw: the segment is {seg['length']} frames now, the cached one "
                         f"{man.get('frames')} - redraw can not change lengths; run the segment again instead. "
                         f"局部重绘不能改长度（现在 {seg['length']} 帧，缓存里 {man.get('frames')} 帧），请整段重跑。")
    now = [int(c) for c in seg.get("windowCuts") or []]
    if "cuts" in man and [int(c) for c in man.get("cuts") or []] != now:
        raise ValueError(f"{_TAG} {where}: local redraw: the hard cuts moved (cached {man.get('cuts')}, now {now}) - "
                         f"redraw can not move cuts; run the segment again instead. 局部重绘不能移动硬切位置"
                         f"（缓存里 {man.get('cuts')}，现在 {now}），请整段重跑。")


def redraw_latent(cache, k, latent: dict, ranges, where=""):
    """window-exp local redraw: the segment's cached final latent with a soft noise mask over `ranges` (frames inside
    the segment; 1 = redraw, 0 = keep).  A locked song (audio mask already 0 in `latent`) stays locked.  Returns
    (latent, active latent range, fresh seed)."""
    import random
    old_v, old_a = D._streams(cache.load_latent(k), "cached latent")
    cur_v, cur_a = D._streams({"samples": latent["samples"]}, "latent")
    if tuple(old_v.shape[2:]) != tuple(cur_v.shape[2:]) or old_a.shape[-1] != cur_a.shape[-1]:
        raise ValueError(f"{_TAG} {where}: the cached segment {tuple(old_v.shape)} does not match the plan "
                         f"{tuple(cur_v.shape)} - local redraw needs the same length and size")
    vm, am, active = WIN.redraw_masks(int(old_v.shape[2]), int(old_a.shape[-1]), ranges)
    nm = latent.get("noise_mask")
    a_keep = None
    if nm is not None:   # e.g. locked audio from the Continue step
        _vm0, am0 = nm.unbind() if hasattr(nm, "unbind") else nm
        a_keep = am0
    video = old_v.to(cur_v)
    audio = old_a.to(cur_a)
    video_mask = vm.view(1, 1, -1, 1, 1).to(device=cur_v.device, dtype=torch.float32)
    audio_mask = am.view(1, 1, 1, -1).to(device=cur_a.device, dtype=torch.float32)
    if a_keep is not None:
        audio = torch.where(a_keep.to(audio.device) == 0, cur_a, audio)
        audio_mask = torch.minimum(audio_mask, a_keep.to(audio_mask))
    seed = random.SystemRandom().randrange(1, 2 ** 50)
    LOG.info("%s %s: LOCAL REDRAW of frames %s (latents %d-%d, ramp %d), new seed %d; the rest of the cached segment "
             "is kept", _TAG, where, ranges, active[0], active[1] - 1, WIN.REDRAW_RAMP, seed)
    return {"samples": D._nested((video, audio)), "noise_mask": D._nested((video_mask, audio_mask))}, active, seed


def low_pass_key(settings_hash, plan, k, key_seed, seg_seed, continued, prev_key, dkey, lora_sig):
    """11-08: key of what the LOW-RES pass depends on: the segment key without the high-res windows and without the
    high-res LoRAs (so changing only those keeps the low-res result, pass1.pt).  Window-mode plans only."""
    wm = plan.get("windowMode") if isinstance(plan, dict) else None
    if not wm:
        return None
    plan_low = dict(plan, windowMode={x: v for x, v in wm.items() if x not in ("highLength", "highOverlap")})
    low = C.segment_key(settings_hash, plan_low, k, key_seed, continued, prev_key)
    if dkey is not None:
        low = C._sha({"key": low, "depth_control": dkey})
    if (lora_sig or {}).get("low"):
        low = C._sha({"key": low, "loras": {"low": lora_sig["low"]}})
    if seg_seed != key_seed:
        low = C._sha({"key": low, "seed": seg_seed})
    return low


def progress_store(cache, k, key, plan, where="", low_key=None):
    """window-exp: the segment's progress cache (low-res result + per-step state) in the segment cache folder, or None
    (no disk cache, no key, or not a window-mode plan)."""
    if cache is None or not key or not isinstance(plan, dict) or not plan.get("windowMode"):
        return None
    try:
        return PROG.ProgressStore(os.path.join(cache.dir, f"seg_{int(k):03d}_progress"), key, low_key=low_key)
    except Exception as exc:
        LOG.warning("%s %s: progress cache unavailable (%s)", _TAG, where, exc)
        return None


class H3MDLoopSampler:
    @classmethod
    def INPUT_TYPES(cls):
        sampler_spec = S.H3MDSampler.INPUT_TYPES()
        s_req = {k: v for k, v in sampler_spec["required"].items() if k not in _SAMPLER_COMPUTED}
        s_opt = dict(sampler_spec.get("optional", {}))
        enc = D.H3MDSegmentEncode.INPUT_TYPES()["required"]
        cont = D.H3MDSegmentContinue.INPUT_TYPES()["required"]
        req = {
            # H3MD rerun (2026-10-30): at the top of the node; replace start_index / end_index / previous_latent
            "rerun_segments": ("STRING", {"default": "", "tooltip":
                "Re-render only these segments (1-based, as in the log): e.g. 3  or  4-6  or  8-10, 13-14. Empty = "
                "normal run. Needs resume_from_cache on and every other segment in the cache. Each selected block "
                "starts from the end of the segment before it and its last overlap frames are locked to the start of "
                "the cached segment after it, so nothing else is re-rendered. Old files go to _replaced_<time>.\n"
                "只重跑这些段（从 1 数，和日志一致）：如 3、4-6、8-10, 13-14。留空 = 正常跑。需要打开 "
                "resume_from_cache，其余段都要在缓存里。每块开头接上一段结尾，结尾锁到下一段的旧开头，其余段不重跑；"
                "旧文件挪到 _replaced_<时间> 留底。"}),
            "stop_after": ("INT", {"default": 0, "min": 0, "max": 255, "tooltip":
                "Run segments 1..N only (0 = all). Short test: 1 = only the first segment; set it back to 0 later "
                "and the cached segments are reused.\n只跑到第 N 段为止（0 = 全部）。短测试填 1 = 只跑第 1 段；"
                "之后改回 0，已跑的段从缓存取。"}),
            "rerun_new_seed": ("BOOLEAN", {"default": True, "tooltip":
                "Re-rendered segments get a new random seed (logged and stored in the cache), so a rerun differs "
                "even with the same prompt and refs. Off: the original seed.\n重跑的段用新的随机种子（记进日志和"
                "缓存），提示词和参考图不变也能抽到不同结果。关：用原种子。"}),
            "plan": (D.PLAN_TYPE,),
            "clip": enc["clip"],
            "vae": s_req.pop("vae", enc["vae"]),
            "audio_vae": enc["audio_vae"],
        }
        req.update(s_req)  # low_res_model, sampler, sigmas, seed, cfg, ... upscaler_model
        req.update({
            "ref_image_size": enc["ref_image_size"],
            "continue_audio": cont["continue_audio"],
            "drift_strength": cont["drift_strength"],
            "drift_lock_tokens": cont["drift_lock_tokens"],
            "seed_mode": (["increment", "same"], {"default": "increment",
                          "tooltip": "increment (default): segment k uses seed + k, so equal-length segments do not get "
                                     "identical noise; same: every segment uses the seed. With resume_from_cache on, "
                                     "the seed of the run being resumed is reused."}),
            "decode_output": ("BOOLEAN", {"default": True,
                              "tooltip": "Decode every segment (video + audio VAE) after sampling and join the frames "
                                         "like H3MD Segment Join. Off: only the joined latent is produced."}),
            "audio_crossfade_ms": ("INT", {"default": 0, "min": 0, "max": 2000}),
            "release_vram_between_segments": ("BOOLEAN", {"default": True,
                                              "tooltip": "Unload idle models (text encoder, VAEs, upscaler leftovers) + "
                                                         "soft_empty_cache between segments. The shared base UNet stays."}),
        })
        opt = dict(s_opt)  # high_res_model, highres_tiling, tiling_axis, tiling_overlap, release_upscaler, unload_idle_models
        opt.update({
            "cache_mode": (["disk", "ram"], {"default": "disk",
                           "tooltip": "disk: decode each segment right after sampling and write it to a disk cache "
                                      "(RAM holds one segment); merge at the end. ram: original in-RAM decode/join."}),
            "cache_dtype": (["fp16", "uint8"], {"default": "fp16",
                            "tooltip": "Cached frame format. fp16 ~= lossless (2 B/value); uint8 = half the disk, "
                                       "8-bit like the saved video."}),
            "cache_dir": ("STRING", {"default": "", "tooltip": "Empty = auto: <drive>/H3MD_cache on the local fixed drive with the most free space (an existing H3MD_cache holding resume_* runs is reused first, so resume survives restarts). Falls back to ComfyUI temp/h3md_cache with a warning if nothing is writable. A set folder must be inside a <drive>/H3MD_cache folder, ComfyUI's output / temp folder or a folder listed under \"folders\" in <ComfyUI user folder>/h3md/config.json (v0.3.7); otherwise the automatic folder is used."}),
            "delete_cache": ("BOOLEAN", {"default": True,  # hidden in the UI (web/js/h3md_hide_widgets.js); slot kept
                             "tooltip": "Hidden / no effect since 2026-10-18: the Loop never deletes its cache before the "
                                        "video is saved. Use H3MD Save Video Interp -> delete_cache_after_save."}),
            "resume_from_cache": ("BOOLEAN", {"default": False,
                                  "tooltip": "Reuse cached segments with identical plan segment, refs, seed and settings "
                                             "(models/LoRAs are NOT checked). The base seed of the run being resumed is "
                                             "reused, so a randomized seed widget does not break the resume."}),
            "unload_models_before_merge": ("BOOLEAN", {"default": True,
                                           "tooltip": "After the last segment: unload all models (incl. the base UNet) "
                                                      "+ empty cache before the merge and downstream RIFE/Save."}),
            "continue_lowres_prefix": (["pixel_reencode", "latent_resize", BETA], {"default": BETA,
                                       "tooltip": "Continued segments, low-res pass: pixel_reencode (Alpha) = the overlap "
                                                  "frames are downscaled in pixel space and VAE-encoded (fixes the hazy/ghosted "
                                                  "continuation); latent_resize = the engine's latent-space resize; "
                                                  "native_lowres_carry (default) = the previous segment's own low-res stage latent tail "
                                                  "(unresized) pins the low-res prefix, the high-res final latent pins the high-res "
                                                  "prefix; falls back to pixel_reencode when no matching carry exists."}),
        })
        opt.update(SEAM.post_input_types())  # H3MD seam/refwindow hook: appended LAST (old workflows load)
        opt.update(SB.LOOP_INPUTS)  # H3MD semantic-bridge hook: optional chain link, appended LAST (not connected = off)
        opt["seam_decode_context"] = ("INT", {"default": 0, "min": 0, "max": SEAM_DECODE_CONTEXT_MAX,  # H3MD-Exp, LAST
                                      "tooltip": "Continued segments: decode with this many 17-frame clips of the previous "
                                                 "segment's final latent in front (VAE context), then drop them, so the new "
                                                 "segment does not start the VAE cold at the seam. 0 = off (old behaviour)."})
        opt["depth_control"] = (DC.DEPTH_TYPE, {"tooltip": "H3MD Depth Control (optional, appended LAST): depth "  # H3MD depth
                                "ControlNet on the low-res stage for plan videos with 'depth' ticked. Not connected = off."})
        # H3MD memguard (2026-10-27), appended LAST; VRAM-only: never part of a cache key
        opt["vram_release"] = (["deep", "basic"], {"default": "deep", "tooltip":
            "Between segments. deep: unload idle models, reset ComfyUI's cast/staging buffers, empty the CUDA cache and "
            "log memory before/after; if PyTorch still keeps > 2 GB idle or the GPU is nearly full, unload ALL models "
            "(the base UNet reloads, ~0.5-1 min). basic: the old idle-model unload only."})
        opt["slow_step_guard"] = (["stop", "warn", "off"], {"default": "stop", "tooltip":
            "Watches the sampler's step times. A step >= 4x the usual time of that stage (and >= 120 s) means VRAM ran out "
            "(weights streamed every step). stop: interrupt the run (disk cache kept; restart ComfyUI and queue again "
            "with resume_from_cache on). warn: log only. off: no watch."})
        # H3MD low-res anchor (2026-10-30), appended LAST; 0 = off (old keys)
        opt["lowres_anchor"] = ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip":
            "ONE continuous scene only (motion copy, a long take). After each continued segment's low-res pass, pull the "
            "new frames' 24 latent channels (mean and spread) back to the first segment of the shot, before the upscale; "
            "the low-res carry is corrected too, so colour / contrast drift cannot pile up segment after segment. "
            "0 = off, 1 = full. Needs continue_lowres_prefix = native_lowres_carry. Do not use for a story whose colours "
            "should change.\n只用于单一场景的长视频（动作复刻、长镜头）：每个续接段低清跑完、放大前，把新画面 24 个通道"
            "的均值和幅度拉回本镜头第一段，低清接续数据也一起校正，颜色不会一段段越漂越深。0 = 关，1 = 全量。需要 "
            "continue_lowres_prefix = native_lowres_carry。场景本该变色的叙事视频不要开。"})
        # H3MD high-res anchor (v0.2.0), appended LAST; 0 = off (old keys)
        opt["hires_anchor"] = ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip":
            "ONE continuous scene only, together with lowres_anchor. The high-res result of each segment pins the next "
            "segment's high-res start, so a small colour / detail shift there also piles up (measured on a 3-minute take: "
            "saturation +8 %, sharpness -15 % with lowres_anchor alone). After each continued segment, the new high-res "
            "latent frames (not the kept overlap) get their 24 channels' mean and spread pulled back to the first segment "
            "of the shot, before decode and before the next segment continues from it. 0 = off, 1 = full.\n"
            "只用于单一场景的长视频，和 lowres_anchor 一起用：每个续接段的高清结果会钉住下一段的高清开头，细小的颜色、"
            "清晰度变化也会一段段累积（3 分钟长镜头实测：只开 lowres_anchor 时饱和度 +8%、锐度 -15%）。开了以后，每个续接段"
            "新生成的高清 latent（不含重叠部分）在解码前、下一段接续前，把 24 个通道的均值和幅度拉回本镜头第一段。0 = 关，1 = 全量。"})
        # v0.3 H3MD Control Panel, appended LAST: one box for "which segments" (empty / not connected = old widgets)
        opt["segments_to_run"] = ("STRING", {"default": "", "tooltip":
            "Type here or connect H3MD Control Panel. Empty: run everything (cached segments reused). '2' / '1-2' / '3, 6-7, 9': "
            "generate only these this time (cached ones again too); missing earlier segments are generated as well. "
            "Overrides rerun_segments / stop_after when not empty.\n直接填或接总控台。空 = 全部跑完（有缓存的直接用）；"
            "填 2 / 1-2 / 3, 6-7, 9 = 这次只生成这几段（有缓存的也重新生成），前面缺的段一起生成。"})
        # hidden 'prompt' (no widget / no slot): only to warn when 'images' (now a placeholder) is used downstream
        return {"required": req, "optional": opt, "hidden": {"unique_id": "UNIQUE_ID", "prompt": "PROMPT"}}

    RETURN_TYPES = ("LATENT", "IMAGE", "AUDIO", "INT", "STRING", SS.SEGMENTS_TYPE)  # 'long video' appended LAST
    RETURN_NAMES = ("latent", "images", "audio", "frame_count", "summary", LONG_VIDEO)  # slot 5 was 'segments'
    FUNCTION = "run"
    CATEGORY = CATEGORY
    OUTPUT_TOOLTIPS = OUTPUT_TOOLTIPS   # one per output (latent, images, audio, frame_count, summary, long video)
    DESCRIPTION = ("Runs all segments of an H3MD plan: Encode -> Continue (final latent only; skipped for hard cuts) "
                   "-> H3MD Sampler -> VRAM release, then joins them.\n\n" + OUTPUTS_NOTE_EN + "\n\n"
                   + OUTPUTS_NOTE_ZH)

    def run(self, plan, clip, vae, audio_vae, low_res_model, sampler, sigmas, seed,
            ref_image_size="match", continue_audio=True, drift_strength=0.6,
            drift_lock_tokens=2, seed_mode="increment", decode_output=True, audio_crossfade_ms=0,
            release_vram_between_segments=True, high_res_model=None, highres_tiling=True,
            rerun_segments="", stop_after=0, rerun_new_seed=True, cache_mode="disk", cache_dtype="fp16",
            cache_dir="", delete_cache=True, resume_from_cache=False, unload_models_before_merge=True,
            continue_lowres_prefix=BETA, merge_images=None, unique_id=None, prompt=None, **sampler_kwargs):
        for gone in ("start_index", "end_index", "previous_latent"):  # removed 2026-10-30 (API-format prompts)
            if sampler_kwargs.pop(gone, None) is not None:
                LOG.warning("%s '%s' was removed: use rerun_segments / stop_after", _TAG, gone)
        if isinstance(plan, dict) and plan.get("windowMode"):   # window-exp: core context windows + H3MD additions
            low_res_model, high_res_model = WIN.wrap_models(plan["windowMode"], low_res_model, high_res_model)
        run_text = str(sampler_kwargs.pop("segments_to_run", "") or "").strip()   # v0.3 Control Panel box
        if run_text:
            parse_segments(run_text, len(plan["segments"]))   # wrong writing -> error before anything runs
            rerun_segments, stop_after = "", max(parse_segments(run_text, len(plan["segments"]))) + 1
        indices = segment_range(plan, stop_after)
        segs = plan["segments"]
        selected = parse_segments(rerun_segments, len(segs))
        selected = [k for k in selected if k in indices]
        if str(rerun_segments or "").strip() and not selected:
            raise ValueError(f"{_TAG} rerun_segments '{rerun_segments}' selects nothing within stop_after. "
                             f"重跑段落 '{rerun_segments}' 不在本次运行范围内（stop_after）。")
        if selected and not (decode_output and cache_mode == "disk" and resume_from_cache):
            raise ValueError(f"{_TAG} rerun_segments needs decode_output on, cache_mode=disk and resume_from_cache "
                             f"on (the other segments come from the cache). 重跑段落需要 decode_output 开、"
                             f"cache_mode=disk、resume_from_cache 开（其余段从缓存取）。")
        self._post = SEAM.post_options(sampler_kwargs)  # H3MD seam/refwindow hook (never reaches the sampler)
        self._seam = SEAM.SeamPost(self._post, plan)
        self._seam.boundary = True   # H3MD seam hook: the prefix is restored exactly (drift) -> measure at the boundary
        self._sb = SB.BridgeHook(sampler_kwargs.pop("semantic_bridge", None))  # H3MD semantic-bridge hook (pops its input)
        sampler_kwargs.pop("hires_mode", None)  # removed hires_mode option (old option dicts): never reaches the sampler
        self._ctx_clips = max(0, min(SEAM_DECODE_CONTEXT_MAX, int(sampler_kwargs.pop("seam_decode_context", 0) or 0)))  # H3MD-Exp
        _dc = sampler_kwargs.pop("depth_control", None)
        if _dc is None and DC.plan_uses_depth(plan, indices):   # 2026-10-30 ctlonly: no silent fallback
            raise ValueError(f"{_TAG} a video in the plan has 'depth' ticked but no H3MD Depth Control is connected to "
                             f"'depth_control'. Connect it, or untick depth. 分镜里有视频勾了 depth，但 Loop 的 "
                             f"depth_control 没接 H3MD Depth Control：请接上，或取消勾选 depth。")
        self._depth = DC.LoopDepth(_dc, plan)  # H3MD depth (never reaches the sampler)
        self._vram_release = str(sampler_kwargs.pop("vram_release", "deep") or "deep")      # H3MD memguard (not in keys)
        guard = str(sampler_kwargs.pop("slow_step_guard", "stop") or "stop")                 # H3MD memguard (not in keys)
        self._anchor = max(0.0, min(1.0, float(sampler_kwargs.pop("lowres_anchor", 0.0) or 0.0)))  # H3MD low-res anchor
        self._hires_anchor = max(0.0, min(1.0, float(sampler_kwargs.pop("hires_anchor", 0.0) or 0.0)))  # H3MD hi-res anchor
        for gone in ("drift_handoff_match", "drift_seam_anchor", "drift_detail"):  # removed 2026-10-30 (ineffective)
            if sampler_kwargs.pop(gone, None):
                LOG.warning("%s '%s' was removed (it did not stop the drift): use lowres_anchor", _TAG, gone)
        if self._anchor > 0 and continue_lowres_prefix != BETA:
            LOG.warning("%s lowres_anchor needs continue_lowres_prefix=%s (the reference is the low-res result); off",
                        _TAG, BETA)
            self._anchor = 0.0
        self._watch = h3md_vram.SlowStepWatch(guard).install() if guard in ("stop", "warn") else None
        encoder, continuer, sampler_node = D.H3MDSegmentEncode(), D.H3MDSegmentContinue(), S.H3MDSampler()
        pbar = progress_bar(len(indices), unique_id)
        finals, trims, report = [], [], []
        prev = None
        use_cache = bool(decode_output) and cache_mode == "disk"
        settings = {"sampler": {k: v for k, v in sampler_kwargs.items() if isinstance(v, (int, float, str, bool))
                                and k not in C.VRAM_ONLY_SAMPLER_KWARGS},  # VRAM-only switches: not in the key
                    "seed_mode": seed_mode, "ref_image_size": ref_image_size, "continue": ["drift", continue_audio,
                    drift_strength, drift_lock_tokens], "tiling": bool(highres_tiling), "dtype": cache_dtype,
                    "sigmas": [round(float(x), 6) for x in torch.as_tensor(sigmas).flatten()] if sigmas is not None else None,
                    "sampler_name": getattr(sampler, "sampler_name", str(type(sampler).__name__)),
                    "prev_latent": False,   # kept (previous_latent input removed 2026-10-30): same cache keys
                    "lowres_prefix": continue_lowres_prefix,
                    "size": [int(plan.get("width") or 0), int(plan.get("height") or 0)],
                    "engine": S.ENGINE}
        if self._anchor > 0:  # H3MD low-res anchor: continued-segment key only (0 = old keys unchanged)
            settings["continue"] = list(settings["continue"]) + [{"lowres_anchor": self._anchor}]
        if self._hires_anchor > 0:  # H3MD hi-res anchor: continued-segment key only (0 = old keys unchanged)
            settings["continue"] = list(settings["continue"]) + [{"hires_anchor": self._hires_anchor}]
        if self._ctx_clips:  # H3MD-Exp seam_decode_context: continued-segment key only (0 = old keys unchanged)
            settings["continue"] = list(settings["continue"]) + [{"seam_decode_context": self._ctx_clips}]
        if SEAM.cache_settings(self._post) is not None:  # H3MD seam/refwindow hook
            settings["post"] = SEAM.cache_settings(self._post)
        if self._sb.cache_settings() is not None:  # H3MD semantic-bridge hook
            settings["semantic_bridge"] = self._sb.cache_settings()
        # H3MD depth (2026-10-26 depthonly): NOT in the run-wide settings any more - the depth settings enter only the
        # key of a depth-on segment (see _run), so depth-off segments of a mixed plan keep their old keys
        base_hash, full_hash = C.split_settings(settings)   # per-segment keys: cut/first segments ignore cont-only options
        settings_hash = {"base": base_hash, "full": full_hash}
        cache = C.SegmentCache(cache_dir.strip() or None, base_hash, bool(resume_from_cache), cache_dtype) if use_cache else None
        # H3MD stream save hook: the disk-cache path never concatenates the clip ('images' = placeholder)
        if merge_images is not None:
            LOG.info("%s merge_images=%s ignored (removed): the Loop no longer stitches the full video; connect "
                     "'%s' to H3MD Save Video Interp", _TAG, merge_images, LONG_VIDEO)
        if cache is None and decode_output:
            LOG.warning("%s cache_mode=ram: 'images' carries the joined clip in RAM as before, '%s' = None "
                        "(H3MD Save Video Interp needs cache_mode=disk)", _TAG, LONG_VIDEO)
        elif cache is not None:
            users = images_used_downstream(prompt, unique_id)
            if users:  # once per run
                LOG.warning("%s %s (node(s) %s use 'images').", _TAG, IMAGES_PLACEHOLDER_WARNING, ", ".join(users))
        if run_text:   # v0.3 segments_to_run: decide rerun vs. normal resume from what is really in the cache
            planned = plan_segments_to_run(run_text, segs, cache if resume_from_cache else None,
                                           continue_lowres_prefix == BETA)
            _stop, selected, line = planned
            LOG.info("%s %s", _TAG, line)
            if _stop != len(indices):   # whole video kept (repair) or shorter test run
                indices = list(range(_stop))
                pbar = progress_bar(len(indices), unique_id)
            if selected and not (decode_output and cache_mode == "disk" and resume_from_cache):
                selected = []   # cannot re-render from the cache: plain run of segments 1..stop
        if cache is not None:  # base seed of a resumed run (the seed widget is usually randomized every queue)
            stored = cache.run_seed() if resume_from_cache else None
            if stored is not None and stored != int(seed):
                LOG.info("%s resume: using the base seed %d of the cached run (seed input %d ignored; turn "
                         "resume_from_cache off to start a new run)", _TAG, stored, int(seed))
                seed = stored
            elif stored is None:
                cache.save_run_seed(int(seed))
        self._segments_info = None
        prev_key = None
        try:
            out = self._run(plan, indices, segs, encoder, continuer, sampler_node, pbar, finals, trims, report, prev,
                             cache, prev_key, settings_hash, clip, vae, audio_vae, low_res_model, sampler, sigmas, seed,
                             ref_image_size, continue_audio, drift_strength, drift_lock_tokens, seed_mode,
                             decode_output, audio_crossfade_ms, release_vram_between_segments, high_res_model,
                             highres_tiling, unique_id, sampler_kwargs, bool(resume_from_cache), bool(delete_cache),
                             bool(unload_models_before_merge), continue_lowres_prefix,
                             rerun=selected, rerun_new_seed=bool(rerun_new_seed))
            return tuple(out) + (self._segments_info,)  # H3MD stream save hook: 'long video' (None without disk cache)
        except BaseException:
            if cache is not None:
                LOG.warning("%s run failed or was interrupted; cache KEPT at %s (resume_from_cache can reuse it)",
                            _TAG, cache.dir)
            raise
        finally:
            if getattr(self, "_watch", None) is not None:  # H3MD memguard
                self._watch.remove()
                self._watch = None

    def _run(self, plan, indices, segs, encoder, continuer, sampler_node, pbar, finals, trims, report, prev,
             cache, prev_key, settings_hash, clip, vae, audio_vae, low_res_model, sampler, sigmas, seed,
             ref_image_size, continue_audio, drift_strength, drift_lock_tokens, seed_mode,
             decode_output, audio_crossfade_ms, release_vram_between_segments, high_res_model,
             highres_tiling, unique_id, sampler_kwargs, resume, delete_cache, unload_before_merge,
             lowres_prefix_mode=BETA, rerun=None, rerun_new_seed=True):
        prev_tail = None  # decoded frames of the previous segment's tail (disk-cache mode), for the low-res prefix
        prev_low = None   # Beta: previous segment's low-res stage endpoint (native low-res grid, CPU)
        ctx_clips = int(getattr(self, "_ctx_clips", 0) or 0)  # H3MD-Exp seam_decode_context
        lora_sig = model_lora_sig(low_res_model, high_res_model)   # 0.3.4: {} = old keys unchanged
        ctx_prevs = []    # H3MD-Exp: previous final latent per entry of `finals` (None = no decode context)
        depth = getattr(self, "_depth", None) or DC.LoopDepth(None, {"segments": []})  # H3MD depth (inactive = no-op)
        beta = lowres_prefix_mode == BETA
        if beta:
            LOG.info("%s continue_lowres_prefix=%s (BETA): low-res carry is captured per segment", _TAG, BETA)
        rerun = sorted(set(rerun or []))
        anchor_strength = float(getattr(self, "_anchor", 0.0) or 0.0) if beta else 0.0  # H3MD low-res anchor
        anchor_ref = None   # per-channel stats of the current shot's first segment (low-res result)
        hires_strength = float(getattr(self, "_hires_anchor", 0.0) or 0.0)  # H3MD hi-res anchor
        hires_ref = None    # per-channel stats of the current shot's first segment (high-res final latent)
        tail_from = {}
        archive = None
        if rerun:  # H3MD rerun (2026-10-30): check the cache before anything is rendered
            missing = []
            for k in indices:
                man = cache.manifest(k) if cache is not None else None
                if k in rerun:
                    continue
                if man is None:
                    missing.append(f"{k + 1} (not cached)")
                elif int(man.get("frames", -1)) != int(segs[k]["length"]):
                    missing.append(f"{k + 1} (cached {man.get('frames')} frames, plan {segs[k]['length']})")
                elif beta and not man.get("lowres"):
                    missing.append(f"{k + 1} (no low-res carry)")
            if missing:
                raise ValueError(f"{_TAG} rerun_segments: these segments are not usable from the cache "
                                 f"{cache.dir if cache is not None else ''}: {', '.join(missing)}. The plan's split or "
                                 f"length may have changed (keep the same total length and reference sizes), or the "
                                 f"resume folder differs (same settings needed). 重跑段落：以下段无法从缓存取用："
                                 f"{', '.join(missing)}。可能分镜拆段/长度变了（总长和参考图尺寸要保持不变），或设置"
                                 f"不同导致缓存文件夹不同。")
            for k in rerun:
                nk = k + 1
                if nk in indices and nk not in rerun and segs[nk]["overlapIn"] > 0:
                    tail_from[k] = nk
            archive = cache.archive_dir()
            LOG.info("%s RERUN plan (cache %s, old files of re-rendered segments -> %s):\n%s", _TAG, cache.dir,
                     archive, describe_rerun(rerun, indices, segs, tail_from))
        LOG.info("%s %d segment(s) %s, continuation=drift seed_mode=%s base seed %d", _TAG, len(indices),
                 [f"{i}:{'cont ' + str(segs[i]['overlapIn']) + 'f' if segs[i]['overlapIn'] else 'cut/first'}" for i in indices],
                 seed_mode, int(seed))
        for step, k in enumerate(indices):
            check_interrupt()
            seg = segs[k]
            where = f"segment {k + 1}/{len(segs)}" + (f" (shot {seg['shot'] + 1}.{seg['sub'] + 1}/{seg['subCount']})" if "shot" in seg else "")
            if "autoPart" in seg:  # H3MD autosplit (2026-10-27)
                where += f" (shot {seg['shotIndex'] + 1} part {seg['autoPart'] + 1}/{seg['autoParts']})"
            seg_seed = int(seed) + (k if seed_mode == "increment" else 0)
            key_seed = seg_seed   # the cache key keeps the run's seed, so a later normal resume reuses a rerun
            if rerun and k in rerun and rerun_new_seed:  # H3MD rerun: fresh seed for a re-rendered segment
                import random
                seg_seed = random.SystemRandom().randrange(1, 2 ** 50)
            will_continue = seg["overlapIn"] > 0 and prev is not None
            preview = bool(isinstance(plan, dict) and (plan.get("windowMode") or {}).get("preview"))
            uses_cont = C.segment_uses_cont_settings(seg, will_continue, highres_tiling)
            key = C.segment_key(settings_hash["full" if uses_cont else "base"], plan, k, key_seed, will_continue,
                                prev_key) if cache is not None else None
            dkey = depth.segment_cache_settings(plan, k)  # H3MD depth: per-segment, None for a depth-off segment
            if key is not None and dkey is not None:
                key = C._sha({"key": key, "depth_control": dkey})
            if key is not None and lora_sig:   # 0.3.4: LoRAs recorded by H3MD loaders / the Control Panel
                key = C._sha({"key": key, "loras": lora_sig})
            # window-exp low-res preview: its own segment-cache key (never mistaken for a finished segment); the
            # progress cache keeps the full run's key, so the full run reuses the preview's low-res pass (pass1.pt)
            pkey = key
            if key is not None and seg_seed != key_seed:
                # 11-08 review: a rerun with a new seed must not reuse the old take's low-res pass (pass1.pt)
                pkey = C._sha({"key": key, "seed": seg_seed})
            low_key = low_pass_key(settings_hash["full" if uses_cont else "base"], plan, k, key_seed, seg_seed,
                                   will_continue, prev_key, dkey, lora_sig) if key is not None else None
            if key is not None and preview:
                key = C._sha({"key": key, "preview": True})
            # window-exp local redraw: windowMode.redraw [[start_s, end_s], ...] re-renders only those seconds of the
            # cached segment.  0.4.0: the prompt, the pictures and the reference media may have changed since the
            # segment was rendered (only the redrawn seconds use the new ones); length, size and hard cuts may not.
            redraw = WIN.redraw_ranges(plan.get("windowMode") if isinstance(plan, dict) else None,
                                       int(seg["startFrame"]), int(seg["length"]))
            base_key = key
            if redraw and cache is not None and key is not None:
                man = cache.manifest(k) or {}
                check_redraw_base(cache, k, man, seg, where)
                if base_key not in (man.get("key"), man.get("base_key")):
                    LOG.info("%s %s: local redraw: the prompt / pictures / reference media of this segment changed since "
                             "it was rendered - the redrawn seconds %s use the new ones, the rest keeps the cached take. "
                             "局部重绘：这一段的提示词 / 参考素材改过，只有重画的秒数用新的，其余保持原样。", _TAG, where,
                             redraw)
                key = C._sha({"key": base_key, "redraw": redraw})
            elif redraw:
                raise ValueError(f"{_TAG} {where}: local redraw needs the disk cache (cache_mode=disk)")
            if not redraw and cache is not None and key is not None:
                # a segment redrawn earlier keeps its latest version: also once 'redraw' is cleared or the Redraw /
                # Preview node is bypassed (0.4.0 fix: before, clearing 'redraw' re-rendered the original take
                # over the redrawn one)
                man = cache.manifest(k) or {}
                if man.get("base_key") == key and man.get("key"):
                    key = man["key"]
            if rerun:  # H3MD rerun: not selected = cached (validated above), selected = re-rendered
                hit = k not in rerun
                if hit and not cache.has(k, key) and not preview:
                    # new chain key, so a later normal resume reuses it too (not with the preview key: that would
                    # relabel a finished full-quality segment as a preview and the next full run would redo it)
                    cache.adopt(k, key)
            else:
                hit = cache is not None and (resume or bool((plan.get("windowMode") or {}).get("redraw"))) \
                    and not redraw and cache.has(k, key)
            if hit and not rerun and beta and step + 1 < len(indices) and segs[indices[step + 1]]["overlapIn"] > 0 \
                    and not (cache.manifest(k) or {}).get("lowres"):
                LOG.info("%s %s: cached segment has no BETA low-res carry (written in another mode); re-rendering",
                         _TAG, where)
                hit = False
            if hit:
                man = cache.manifest(k)
                final = cache.load_latent(k)
                finals.append(final)
                ctx_prevs.append(None)  # resumed: frames come from the cache (disk mode only)
                trims.append(int(man["trim"]))
                report.append(dict(man["report"], resumed=True))
                prev, prev_key, prev_tail = final, key, None
                depth.reset_tail()  # H3MD depth: a resumed segment leaves no control tail
                prev_low = cache.load_lowres(k) if beta else None
                if anchor_strength > 0 and not (seg["overlapIn"] > 0 and step > 0):  # H3MD low-res anchor: shot start
                    anchor_ref = LA.stats(prev_low) if isinstance(prev_low, torch.Tensor) else None
                if hires_strength > 0 and not (seg["overlapIn"] > 0 and step > 0):  # H3MD hi-res anchor: shot start
                    hires_ref = LA.stats(D._streams(final, "cached latent")[0])
                if self._seam.active:  # H3MD seam hook
                    self._seam.remember(cache.frames(k))
                LOG.info("%s %s: resumed from cache (%d frames)%s", _TAG, where, man["frames"],
                         "" if not beta else f", low-res carry {'restored' if prev_low is not None else 'missing'}")
                if pbar is not None:
                    pbar.update_absolute(step + 1, len(indices))
                continue
            tail_lat, tail_low, tail_ov = None, None, 0
            if rerun and k in rerun:
                if k in tail_from:
                    nk = tail_from[k]
                    tail_ov = int(segs[nk]["overlapIn"])
                    tail_lat = cache.load_latent(nk)
                    nlow = cache.load_lowres(nk) if beta else None
                    tvt = D.video_tokens(tail_ov)
                    if isinstance(nlow, torch.Tensor) and nlow.ndim == 5 and nlow.shape[2] >= tvt:
                        tail_low = nlow[:1, :, :tvt].clone()
                    LOG.info("%s %s: RERUN, tail locked to the first %d frames of segment %d (low-res tail %s)",
                             _TAG, where, tail_ov, nk + 1, "native" if tail_low is not None else "engine resize")
            if getattr(self, "_watch", None) is not None:  # H3MD memguard: step-time baseline per segment shape
                self._watch.shape = (int(seg["length"]), int(plan.get("width") or 0), int(plan.get("height") or 0))
            h3md_vram.log_memory(f"before {where}")  # H3MD memguard
            enc_plan, rw_info = RW.window_plan(plan, k)  # H3MD refwindow hook
            dseg = depth.prepare(enc_plan, k, rw_info, where, will_continue)  # H3MD depth: depth model loaded + released here only
            if dseg is not None and dseg["override"] is not None:  # depth on: depth replaces the raw video as <Video N>
                enc_plan = DC.with_video_override(enc_plan, k, dseg["ref_id"], dseg["override"])
                dseg["override"] = None
            elif dseg is not None and dseg.get("drop_video"):  # 2026-10-30 ctlonly: ControlNet only, no <Video N>
                enc_plan = DC.without_video(enc_plan, k, dseg["ref_id"])
            elif dseg is None and depth.uses:   # depth disabled for this segment: still no <Video N> (no label)
                _ref = DC.segment_depth_ref(enc_plan["segments"][k])
                if _ref is not None and D.depth_control_only(enc_plan["segments"][k], _ref, enc_plan["media"]):
                    enc_plan = DC.without_video(enc_plan, k, _ref["id"])
                    LOG.warning("%s %s: depth video '%s' not used for this segment (no control, and a depth video "
                                "has no <Video N>)", _TAG, where, _ref["id"])
            positive, negative, latent, _p, _l, length, overlap, _w, _h, locked = encoder.encode(
                enc_plan, k, clip, vae, audio_vae, ref_image_size)
            del enc_plan
            positive = self._sb.apply(positive, where)  # H3MD semantic-bridge hook (no-op when off)
            continued = overlap > 0 and prev is not None
            if overlap > 0 and prev is None:
                LOG.warning("%s %s has overlapIn %d but no previous final latent (start_index without previous_latent): "
                            "generated as a fresh segment, joined without trimming", _TAG, where, overlap)
            model = low_res_model
            if continued or locked is not None or tail_lat is not None:
                prev_c = prev
                positive, latent, model, actual = continuer.prepare(
                    positive, latent, low_res_model, overlap if continued else 0, continue_audio=continue_audio,
                    drift_strength=drift_strength, drift_lock_tokens=drift_lock_tokens, release_vram=False,
                    previous_latent=prev_c if continued else None, locked_audio=locked, audio_vae=audio_vae,
                    next_latent=tail_lat, tail_overlap=tail_ov)  # H3MD rerun: tail lock (None = off)
            tiling = bool(highres_tiling)
            LOG.info("%s %s: len %d, %s, seed %d, tiling %s", _TAG, where, length,
                     f"continue {overlap}f (drift)" if continued else ("first" if step == 0 and overlap == 0 and k == 0 else "hard cut"),
                     seg_seed, tiling)
            low_prefix = None
            use_alpha = continued and lowres_prefix_mode == "pixel_reencode"
            if continued and beta:
                low_prefix, why = native_carry_prefix(prev_low, overlap, latent, sampler_kwargs.get("lowres_scale", 0.5),
                                                      where)
                if low_prefix is None:
                    LOG.warning("%s %s: BETA native low-res carry unavailable (%s); falling back to pixel_reencode",
                                _TAG, where, why)
                    use_alpha = True
            if use_alpha:
                try:
                    low_prefix = low_res_prefix(vae, prev, overlap, latent, sampler_kwargs.get("lowres_scale", 0.5),
                                                prev_tail, where)
                except Exception as exc:
                    if type(exc).__name__ == "InterruptProcessingException":
                        raise
                    LOG.warning("%s %s: low-res prefix re-encode failed (%s); using the engine's latent resize",
                                _TAG, where, exc)
                    low_prefix = None
            extra = {"h3md_low_res_prefix": low_prefix} if low_prefix is not None else {}
            if low_prefix is not None and continued and beta and not use_alpha:
                extra["h3md_low_res_prefix_source"] = BETA   # log wording only
            if beta:
                extra["h3md_capture_lowres"] = True
            if tail_low is not None:  # H3MD rerun: tail lock on the low grid
                extra["h3md_low_res_suffix"] = tail_low
            if continued and anchor_strength > 0:  # H3MD low-res anchor
                if anchor_ref is not None:
                    extra["h3md_lowres_anchor"] = {"strength": anchor_strength, "ref": anchor_ref}
                else:
                    LOG.warning("%s %s: lowres_anchor has no reference (the shot's first segment has no low-res "
                                "result in the cache); not applied", _TAG, where)
            low_model, high_model, dctl = model, high_res_model, None
            progress = None if redraw else progress_store(cache, k, pkey, plan, where, low_key)
            if progress is not None:
                extra["h3md_progress"] = progress
            if redraw:   # local redraw: the cached segment is the starting point, only the ranges are re-rendered
                latent, active, seg_seed = redraw_latent(cache, k, latent, redraw, where)
                WIN.set_active([low_res_model, high_res_model], active)
                old_low = cache.load_lowres(k)   # 0.4.0: the kept rows of the low-res pass = its own result
                if isinstance(old_low, torch.Tensor):
                    extra["h3md_low_res_base"] = old_low
                else:
                    LOG.warning("%s %s: local redraw: no low-res result of this segment in the cache (written without "
                                "the Beta low-res carry); the low-res pass uses the downscaled final picture around "
                                "the redrawn seconds", _TAG, where)
            if preview:
                extra["h3md_low_only"] = True
            nxt_ov = segs[indices[step + 1]]["overlapIn"] if step + 1 < len(indices) else 0
            try:
                if dseg is not None:  # H3MD depth: int8 patch loaded now, on a CLONE of the low-res model only
                    high_model = high_res_model if high_res_model is not None else model  # unpatched (= old None path)
                    low_model, dctl = depth.attach(model, dseg, latent,
                                                   low_res_hw(S._unbind(latent["samples"])[0].shape[-2:],
                                                              sampler_kwargs.get("lowres_scale", 0.5)), where)
                    DC.assert_no_fun_control(high_model, "high-res model")
                    if dctl is not None:
                        extra["h3md_on_lowres_end"] = dctl.release
                res = sampler_node.sample(low_res_model=low_model, positive=positive, negative=negative, vae=vae,
                                          latent_image=latent, sampler=sampler, sigmas=sigmas, seed=seg_seed,
                                          high_res_model=high_model, highres_tiling=tiling, unique_id=unique_id,
                                          **extra, **sampler_kwargs)
            finally:
                if redraw:
                    WIN.set_active([low_res_model, high_res_model], None)
                if dseg is not None:
                    depth.finish(dseg, dctl, nxt_ov)
                else:
                    depth.reset_tail()
                del low_model, dctl
            out = res["result"][0] if isinstance(res, dict) else res[0]
            final = _to_cpu(out)  # only 'samples' ever leaves the sampler; this is the handoff
            if progress is not None:
                progress.finish()
            if hires_strength > 0:  # H3MD hi-res anchor (v0.2.0)
                final, hires_ref, hinfo = hires_anchor_step(final, hires_ref, continued, overlap, hires_strength, where,
                                                          tail_ov if tail_lat is not None else 0)
            else:
                hinfo = None
            finals.append(final)
            ctx_prevs.append(prev if (continued and ctx_clips > 0) else None)  # H3MD-Exp
            trims.append(overlap if continued else 0)
            report.append({"segment": k, "shot": seg.get("shot", k), "sub": seg.get("sub", 0), "length": length,
                           "seam": ("continue" if continued else ("start" if step == 0 else "cut")),
                           "overlapIn": overlap, "trim": trims[-1], "seed": seg_seed, "tiling": tiling})
            if getattr(sampler_node, "last_anchor", None) is not None:  # H3MD low-res anchor
                report[-1]["lowres_anchor"] = sampler_node.last_anchor
            if hinfo is not None:  # H3MD hi-res anchor
                report[-1]["hires_anchor"] = hinfo
            if rerun and k in rerun:  # H3MD rerun
                report[-1]["rerun"] = True
                if tail_lat is not None:
                    report[-1]["tail_lock"] = {"segment": tail_from[k] + 1, "frames": tail_ov,
                                               "lowres": tail_low is not None}
            del tail_lat, tail_low
            if dseg is not None and depth.last_report is not None:  # H3MD depth (no key when depth is not used)
                report[-1]["depth"] = depth.last_report
            self._sb.annotate(report[-1])  # H3MD semantic-bridge hook
            if rw_info is not None:  # H3MD refwindow hook
                report[-1]["ref_window"] = rw_info
            if tiling:  # high-res tiling plan actually used
                plan_ct = (getattr(sampler_node, "last_tiling", None) or {}).get("plan") or {}
                report[-1]["tiling_plan"] = {"tiles": plan_ct.get("tiles"), "axis": plan_ct.get("axis_name"),
                                             "spans": [list(x[:2]) for x in plan_ct.get("spans", [])]}
            report[-1]["steps"] = step_summary(getattr(sampler_node, "last_steps", None))
            prev, prev_key, prev_tail = final, key, None
            prev_low = getattr(sampler_node, "last_lowres", None) if beta else None
            if redraw and isinstance(prev_low, torch.Tensor) and cache is not None:
                # local redraw: the low-res pass started from the DOWNSCALED final picture; outside the redrawn
                # seconds keep the segment's own native low-res result (the continuation carry / anchor source)
                try:
                    old_low = cache.load_lowres(k)
                    if isinstance(old_low, torch.Tensor) and old_low.shape == prev_low.shape:
                        vm, _am, _act = WIN.redraw_masks(int(prev_low.shape[2]), 1, redraw)
                        keep = (vm == 0).view(1, 1, -1, 1, 1)
                        prev_low = torch.where(keep, old_low.to(prev_low), prev_low)
                except Exception as exc:
                    LOG.warning("%s %s: local redraw: old low-res result not kept (%s)", _TAG, where, exc)
            if anchor_strength > 0 and not continued:  # H3MD low-res anchor: a shot's first segment is the reference
                anchor_ref = LA.stats(prev_low) if isinstance(prev_low, torch.Tensor) else None
            if beta and prev_low is not None:
                report[-1]["lowres_carry"] = list(prev_low.shape)
            if cache is not None:  # decode now, write to disk, free RAM
                v, a = final["samples"].unbind()
                redraw_arc = None
                if chunked_decode_ok(v, isinstance(plan, dict) and plan.get("windowMode")) and not self._seam.active:
                    reuse = None
                    if redraw:   # local redraw: unchanged chunks are copied from the previous version's frames
                        try:
                            old_v = D._streams(cache.load_latent(k), "cached latent")[0]
                            if tuple(old_v.shape) == tuple(v.shape):
                                ch = torch.nonzero((v.float() - old_v.float()).abs().flatten(3)
                                                   .amax(dim=3).amax(dim=(0, 1)) > 0).flatten()
                                changed = (int(ch[0]), int(ch[-1]) + 1) if ch.numel() else (0, 0)
                                redraw_arc = cache.archive_dir()
                                cache.archive(k, redraw_arc)
                                reuse = (os.path.join(redraw_arc, os.path.basename(cache._p(k, "_frames.npy"))),
                                         changed)
                            del old_v
                        except Exception as exc:
                            LOG.warning("%s %s: local redraw: decoding everything (%s)", _TAG, where, exc)
                    imgs = ChunkedFrames(vae, v, reuse=reuse)   # window-exp: decoded chunk by chunk while written
                    LOG.info("%s %s: chunked decode (%d frames, %d-clip chunks)%s", _TAG, where, imgs.n,
                             DECODE_CHUNK_CLIPS, f", unchanged latents outside {reuse[1]} copied from the previous "
                             f"version" if reuse else "")
                else:
                    imgs = decode_video_seam_context(vae, v, ctx_prevs[-1], trims[-1], ctx_clips, where)  # H3MD-Exp
                ctx_prevs[-1] = None
                nxt = segs[indices[step + 1]]["overlapIn"] if step + 1 < len(indices) else 0
                if nxt > 0 and lowres_prefix_mode in ("pixel_reencode", BETA) and imgs.shape[0] >= nxt:
                    prev_tail = imgs[-nxt:].clone()
                aud = D.fit_audio_to_frames(decode_audio(audio_vae, a), imgs.shape[0])  # H3MD syncfix: audio = picture length
                report[-1]["decoded_frames"] = int(imgs.shape[0])
                imgs = self._seam.tone(imgs, trims[-1], where, report[-1])  # H3MD seam hook (no-op when off)
                moved_to = redraw_arc
                if rerun and k in rerun:  # H3MD rerun: old files -> _replaced_<time> only once the new ones exist
                    cache.archive(k, archive)
                    moved_to = archive
                elif redraw and redraw_arc is None:   # local redraw: the previous version is kept in the archive
                    moved_to = cache.archive_dir()
                    cache.archive(k, moved_to)
                try:
                    cache.write(k, key, final, imgs, aud,
                                dict({"trim": trims[-1], "report": report[-1],
                                      "cuts": [int(c) for c in seg.get("windowCuts") or []]},   # redraw check
                                     **({"base_key": base_key, "redraw": redraw} if redraw else {})),
                                lowres=prev_low if beta else None)
                except BaseException:
                    # 11-08 review: with chunked decode the frames are decoded WHILE they are written; a failure or a
                    # cancel there must not leave the segment missing - the previous version goes back in place
                    if moved_to is not None:
                        if hasattr(imgs, "_old"):
                            imgs._old = None   # release the memmap of the archived frames (Windows can't move it)
                        gc.collect()
                        try:
                            cache.restore(k, moved_to)
                        except Exception as exc:
                            LOG.warning("%s %s: previous version not restored from %s (%s)", _TAG, where, moved_to,
                                        exc)
                    raise
                if getattr(imgs, "reused", 0):
                    LOG.info("%s %s: local redraw: %d chunk(s) decoded, %d copied unchanged", _TAG, where,
                             imgs.decoded, imgs.reused)
                del imgs, aud, v, a
                gc.collect()
                h3md_vram.soft_empty_cache(f"(after caching {where})")
            if pbar is not None:
                pbar.update_absolute(step + 1, len(indices))
            if release_vram_between_segments and step < len(indices) - 1:
                if getattr(self, "_vram_release", "deep") == "deep":  # H3MD memguard
                    rel = h3md_vram.deep_release([low_res_model, high_res_model], f"(after {where})")
                    report[-1]["memory"] = {"after": {k2: (round(v, 2) if isinstance(v, float) else v)
                                                      for k2, v in rel["after"].items()}, "full_unload": rel["full"]}
                else:
                    h3md_vram.unload_idle_models([low_res_model, high_res_model], f"(after {where})")
        check_interrupt()
        joined = join_latents(finals, trims)

        images, audio = None, None
        stream_frames = None  # H3MD stream save hook
        if decode_output and cache is not None:
            if unload_before_merge:
                unload_all_models("(before merge)")
            check_interrupt()
            # --- H3MD stream save hook: 'long video' describes the on-disk merge for H3MD Save Video Interp ---
            self._segments_info = SS.segments_info(cache.dir, indices, trims, self._post["merge_overlap_source"],
                                                   audio_crossfade_ms,
                                                   "per_segment",
                                                   # 0.4.0: hard cuts inside a segment (window-mode groups) so
                                                   # the save node never interpolates across them
                                                   [segs[k].get("windowCuts") or [] for k in indices])
            # never concatenated in RAM: audio only (small); the frames stay on disk for H3MD Save Video Interp
            _idr = plan.get("idRef") if isinstance(plan, dict) else None   # 2026-10-28 idref
            if _idr and _idr.get("file"):
                try:
                    self._segments_info["id_ref_file"] = D.resolve_media_path(_idr["file"])
                except Exception as exc:
                    LOG.warning("%s idRef image not found (%s) - face refine falls back to its own selection", _TAG, exc)
            audio, _total = SS.merged_audio(self._segments_info)
            images = torch.zeros((1, 64, 64, 3))
            stream_frames = int(_total)
            # delete_cache (hidden in the UI since 2026-10-18, input + widget slot kept for old workflows):
            # the Loop never deletes its cache - H3MD Save Video Interp's delete_cache_after_save does that after
            # the file is complete.
            LOG.info("%s %d frames stay in the cache %s; connect '%s' to H3MD Save Video Interp ('images' is a "
                     "placeholder). Cache is NOT deleted here%s", _TAG, stream_frames, cache.dir, LONG_VIDEO,
                     " (delete_cache=on is ignored)" if delete_cache else "")
            # --- end H3MD stream save hook ---
        elif decode_output:
            merged_images, merged_audio = None, None
            for final, trim, rep, ctx_prev in zip(finals, trims, report, ctx_prevs):
                check_interrupt()
                v, a = final["samples"].unbind()
                imgs = decode_video_seam_context(vae, v, ctx_prev, trim, ctx_clips,
                                                 f"segment {rep['segment'] + 1}")  # H3MD-Exp
                aud = D.fit_audio_to_frames(decode_audio(audio_vae, a), imgs.shape[0])  # H3MD syncfix: audio = picture length
                imgs = self._seam.tone(imgs, trim, f"segment {rep['segment'] + 1}", rep)  # H3MD seam hook
                merged_images, merged_audio, count = SEAM.join_frames(imgs, aud, trim, merged_images, merged_audio,
                                                                      audio_crossfade_ms,
                                                                      self._post["merge_overlap_source"])
                rep["decoded_frames"] = int(imgs.shape[0])
            images, audio = merged_images, merged_audio
        else:
            images = torch.zeros((1, 64, 64, 3))
        if decode_output and cache is not None and stream_frames is not None:  # H3MD stream save hook
            frame_count = stream_frames
        else:
            frame_count = int(images.shape[0]) if decode_output else sum(r["length"] - r["trim"] for r in report)
        summary = json.dumps({"segments": report, "frame_count": frame_count,
                              "seconds": round(frame_count / D.FPS, 3)}, ensure_ascii=False)
        LOG.info("%s done: %s", _TAG, summary)
        return (joined, images, audio, frame_count, summary)


NODE_CLASS_MAPPINGS = {"H3MDLoopSampler": H3MDLoopSampler}
# class id kept for saved workflows (positional widgets_values); new layout = h3md_loop_split.py
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDLoopSampler": "H3MD Loop Sampler (legacy)"}
