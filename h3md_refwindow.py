"""H3MD reference windows for automatic sub-segments (own code).

Reference windows always advance (the old 'static' ref_window_mode option was removed 2026-10-23):
  each sub-segment's reference VIDEO window is moved to the time that sub-segment covers in the
            shot: trimStart' = trimStart + (sub.startFrame - shot.startFrame) / 24, computed from the shot
            origin on the 24 fps grid every time (rounded against global time, so no per-sub rounding drift).
            The video's soundtrack (<Audio j> of a video) follows the same window automatically.  Standalone
            reference audios (seg["audios"]) are started at the same offset when the file is long enough.
            Locked audio already advances (Director).
  Limits kept: one window per reference, <= 15 s (MAX_REF_SECONDS), loaded at native resolution by the
  Director (never resized / upscaled to the canvas - the grey-output lesson).  If the source is too short for
  the advanced window (or its duration cannot be read) that reference keeps the shot's window and a warning is logged.
  Idea from the DTV H3 reference-window node by elledartois (MIT) and ComfyUI-H3-Rolling encode(s, Lk) (MIT):
  slice the reference to the window of the current part in global shot time.  No code copied.
"""

from __future__ import annotations

import logging

try:
    from . import h3md_director as D
except ImportError:  # flat import (tests)
    import h3md_director as D

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD RefWindow]"
_SLACK = 0.05  # seconds of tolerance at the source end (container durations are approximate)


def probe_duration(file: str):
    """Media duration in seconds (container, else longest stream), None if unknown.  Hook (tests)."""
    try:
        import av
        with av.open(D.resolve_media_path(file)) as c:
            if c.duration:
                return float(c.duration) / 1_000_000.0
            best = None
            for s in c.streams:
                if s.duration and s.time_base:
                    d = float(s.duration * s.time_base)
                    best = d if best is None else max(best, d)
            return best
    except Exception as exc:
        LOG.warning("%s cannot read duration of '%s': %s", _TAG, file, exc)
        return None


def grid_time(seconds: float) -> float:
    """Snap to the 24 fps grid."""
    return round(float(seconds) * D.FPS) / D.FPS


def shot_offset_frames(plan: dict, seg: dict) -> int:
    """Frames from the shot's content origin to this sub-segment start (0 for non-split segments).
    The content origin is the first NEW frame: a continued shot's first overlapIn frames are the previous shot's
    tail, so its reference window (trimStart) lines up with frame overlapIn of the shot (2026-10-17)."""
    if "shot" not in seg or int(seg.get("subCount", 1)) <= 1 or not plan.get("shots"):
        return 0
    shot = plan["shots"][int(seg["shot"])]
    return max(0, int(seg["startFrame"]) - int(shot["startFrame"]) - int(shot.get("overlapIn", 0) or 0))


def window_plan(plan: dict, k: int):
    """Plan view for segment k with advanced reference windows (shallow copy; the input plan is not modified).
    Returns (plan_view, info).  non-split / first sub-segment -> (plan, None)."""
    seg = plan["segments"][k]
    off = shot_offset_frames(plan, seg)
    if off <= 0 or (not seg.get("videos") and not seg.get("audios")):
        return plan, None
    where = f"segment {k + 1} (shot {seg['shot'] + 1}.{seg['sub'] + 1}/{seg['subCount']})"
    off_s = off / D.FPS
    need = min(seg["length"] / D.FPS, D.MAX_REF_SECONDS)
    media = plan["media"]
    videos, info = [], {"offset_frames": off, "videos": [], "audios": []}
    for ref in seg.get("videos", []):
        src = media["videos"][ref["id"]]
        dur = src.get("duration")
        if dur is None:
            dur = probe_duration(src["file"])
        start = grid_time(ref["trimStart"] + off_s)              # from the shot origin, on the 24 fps grid
        avail = None if dur is None else float(dur) - start
        if avail is None or avail + _SLACK < need:
            LOG.warning("%s %s: video '%s' too short for the advanced window (needs %.2fs from %.3fs, source %s) "
                        "-> shot window from %.3fs", _TAG, where, ref["id"], need, start,
                        "unknown" if dur is None else f"{float(dur):.2f}s", ref["trimStart"])
            videos.append(dict(ref))
            info["videos"].append({"id": ref["id"], "mode": "fallback", "start": ref["trimStart"]})
            continue
        new = dict(ref, trimStart=start, _frame0=True)   # H3MD oneshot: already the sub-segment's frame-0 time (no -overlapIn/24)
        videos.append(new)
        info["videos"].append({"id": ref["id"], "mode": "advance", "start": round(start, 4),
                               "duration": round(min(avail, seg["length"] / D.FPS), 4)})
        LOG.info("%s %s: video '%s' window start %.3fs -> %.3fs (shot offset %d frames = %.3fs, native res)",
                 _TAG, where, ref["id"], ref["trimStart"], start, off, off_s)
    starts = {}
    if seg.get("audioMode") != "none":
        for aid in seg.get("audios", []):
            dur = probe_duration(media["audios"][aid]["file"])
            start = grid_time(off_s)
            if dur is None or float(dur) - start + _SLACK < need:
                LOG.warning("%s %s: audio '%s' too short for the advanced window (needs %.2fs from %.3fs, source %s) "
                            "-> whole file", _TAG, where, aid, need, start,
                            "unknown" if dur is None else f"{float(dur):.2f}s")
                info["audios"].append({"id": aid, "mode": "fallback", "start": 0.0})
                continue
            starts[aid] = start
            info["audios"].append({"id": aid, "mode": "advance", "start": round(start, 4)})
            LOG.info("%s %s: audio '%s' starts at %.3fs (was 0.000s)", _TAG, where, aid, start)
    new_seg = dict(seg, videos=videos)
    if starts:
        new_seg["audioStarts"] = starts      # read by Director.pick_segment_media (H3MD refwindow hook)
    segments = list(plan["segments"])
    segments[k] = new_seg
    return dict(plan, segments=segments), info
