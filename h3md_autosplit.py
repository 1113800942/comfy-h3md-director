"""H3MD autosplit (2026-10-27) - budget-driven automatic splitting of long shots (own code).

Why: a shot's DiT token total (canvas + reference videos + images + RefMods) decides whether it fits the GPU.
Manual splits at a fixed 362 f were far over the line at 1088x1920 (264k tokens vs the ~215k empirical limit),
and a run then slowed down ~25x when VRAM ran out.  Here every shot is estimated with the Director's own token
formula; a shot over the budget is replaced by the fewest equal continuation parts that fit, with cut points nudged
to quiet moments of its reference video.  No user action needed; "autoSplit": false in plan_json turns it off.

Reference videos with refVideoHeight=auto: a depth video always uses the lowest tier (a grey depth map has no detail
to lose); a detail video is planned at the top tier, so the shot gets more parts instead of a blurrier reference
(plan "autoSplitPrefer": "fewer" plans it at the lowest tier = fewest parts).  The Loop's per-segment tier choice uses
the same rule and the same budget.

Budget (tokens) = REF_VIDEO_AUTO_TOKEN_LIMIT x (available VRAM / calibration VRAM) x safety
  available VRAM   = GPU total - ComfyUI's own reserve (extra_reserved_memory, i.e. default or --reserve-vram,
                     + --vram-headroom) - optional plan "autoSplitReserveGB" (extra room for other apps)
  calibration VRAM = the RTX 5090 runs the 215k line was measured on (31.84 GiB - 0.68 - 2.0 headroom)
  safety           = plan "autoSplitSafety" (default 0.85; the measured good/bad gap was < 1 %)

Parts (all lengths 5+17n): part 1 keeps the shot's overlapIn, later parts continue with AUTO_OVERLAP (39) frames.
New content per part is a multiple of 17 frames (part 1 also carries the shot's 5-frame remainder), so the parts
tile the shot exactly: the shot's frame range, the following shots and their startFrames are unchanged.
Each part's video refs start at the source time of its first NEW frame (trimStart), so the existing one-origin
rule (video_window_origin = trimStart - overlapIn/24) maps every part frame to the same source time as the unsplit
shot; a video lock follows by rule, a locked audio file and standalone reference audios advance by the part's
offset.  Parts carry shotIndex / autoPart / autoParts (not "shot": the old refwindow advance stays inactive).
"""

from __future__ import annotations

import copy
import logging
import math
import os

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD AutoSplit]"

AUTO_OVERLAP = 39                   # frames shared by consecutive parts (an OVERLAP_STEPS value)
DEFAULT_SAFETY = 0.85
GIB = 1024 ** 3
CALIB_AVAIL_GIB = 31.84 - 0.68 - 2.0   # RTX 5090, Windows default reserve, --vram-headroom 2 (the calibration runs)
MIN_PART_CONTENT = 48               # never create a part with less than 2 s of new content
NUDGE_UNITS = 1                     # cut points may move +-1 x 17 frames (0.71 s) towards a quieter moment
FALLBACK_VIDEO_SIZE = (720, 1280)   # unknown reference size: a typical vertical 720p clip


def token_budget(raw: dict, limit_tokens: int, avail_bytes: int | None = None) -> dict:
    """-> {"tokens", "avail_gib", "safety", "reserve_gib"}.  avail_bytes None = measure the GPU now."""
    safety = raw.get("autoSplitSafety", DEFAULT_SAFETY)
    try:
        safety = min(1.0, max(0.5, float(safety)))
    except (TypeError, ValueError):
        safety = DEFAULT_SAFETY
    try:
        extra = max(0.0, float(raw.get("autoSplitReserveGB") or 0.0))
    except (TypeError, ValueError):
        extra = 0.0
    reserve_gib = None
    if avail_bytes is None:
        try:
            from . import h3md_vram as V
        except ImportError:
            import h3md_vram as V
        total = V.device_total_bytes()
        if total > 0:
            reserve = V.comfy_reserved_bytes()
            reserve_gib = reserve / GIB
            avail_bytes = total - reserve
    avail_gib = (avail_bytes / GIB if avail_bytes else CALIB_AVAIL_GIB) - extra
    tokens = int(limit_tokens * max(0.1, avail_gib) / CALIB_AVAIL_GIB * safety)
    return {"tokens": tokens, "avail_gib": round(avail_gib, 2), "safety": safety,
            "reserve_gib": None if reserve_gib is None else round(reserve_gib + extra, 2)}


# ---------------------------------------------------------------------------- media sizes / motion (Director IO)

def probe_media_sizes(raw: dict, resolve) -> dict:
    """{("videos"|"images", id): (w, h)} pixel sizes of the plan's media (PyAV / PIL); unreadable files are left out.
    Kept OUT of the media entries on purpose: those enter the segment cache keys."""
    sizes = {}
    media = raw.get("media") or {}
    for item in media.get("videos") or []:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        try:
            import av
            with av.open(resolve(item["file"])) as c:
                v = c.streams.video[0]
                sizes[("videos", str(item.get("id")))] = (int(v.codec_context.width), int(v.codec_context.height))
        except Exception:
            pass
    for item in media.get("images") or []:
        if not isinstance(item, dict) or not item.get("file"):
            continue
        try:
            from PIL import Image
            with Image.open(resolve(item["file"])) as im:
                sizes[("images", str(item.get("id")))] = (int(im.size[0]), int(im.size[1]))
        except Exception:
            pass
    return sizes


_MOTION_CACHE = {}


def motion_profile(path: str, start: float, seconds: float, fps: int = 24):
    """Mean absolute frame difference per 24 fps output frame over [start, start+seconds) of a video (small grey
    frames).  index f = change between source time start+(f-1)/24 and start+f/24.  None if unreadable."""
    try:
        st = os.stat(path)
        key = (path, st.st_mtime_ns, round(start, 3), round(seconds, 3))
    except OSError:
        return None
    if key in _MOTION_CACHE:
        return _MOTION_CACHE[key]
    try:
        import av
        import numpy as np
        n = int(math.ceil(seconds * fps)) + 1
        grid = [None] * n
        with av.open(path) as c:
            vs = c.streams.video[0]
            vs.thread_type = "AUTO"
            if start > 0:
                c.seek(int(max(0.0, start - 1.0) / vs.time_base), stream=vs, backward=True)
            for frame in c.decode(vs):
                if frame.pts is None:
                    continue
                t = float(frame.pts * vs.time_base)
                if t < start - 0.5 / fps:
                    continue
                i = int(round((t - start) * fps))
                if i >= n:
                    break
                if 0 <= i < n and grid[i] is None:
                    grid[i] = frame.reformat(width=64, height=64, format="gray").to_ndarray().astype(np.float32)
        last, prof = None, []
        for g in grid:
            if g is not None and last is not None:
                prof.append(float(np.abs(g - last).mean()))
            else:
                prof.append(prof[-1] if prof else 0.0)
            if g is not None:
                last = g
        _MOTION_CACHE[key] = prof
        return prof
    except Exception as exc:
        LOG.warning("%s motion profile of '%s' unavailable (%s): cuts stay evenly spaced", _TAG, path, exc)
        return None


# ---------------------------------------------------------------------------- pure split logic (no IO)

def part_tokens(D, seg: dict, sizes: dict, width: int, height: int, mode: str, length: int, extra: int = 0,
                prefer: str = "auto") -> int:
    videos = [tuple(sizes.get(("videos", ref["id"]), FALLBACK_VIDEO_SIZE)) + (int(length),)
              for ref in seg.get("videos") or []]
    images = [tuple(sizes.get(("images", i), (width, height))) for i in seg.get("images") or []]
    if mode == "auto":
        # Same rule as the Loop's auto tier (plan_ref_video_sizes with depth flags): a depth video always runs at the
        # lowest tier; a detail (non-depth) video is planned at the TOP tier ("detail", default) so it keeps its
        # resolution and the shot gets more parts instead - or at the lowest tier with prefer "fewer" (fewest parts;
        # the Loop still steps the tier up when a part has room).
        top, low = D.REF_VIDEO_TIERS[0], D.REF_VIDEO_TIERS[-1]
        flags = [bool(ref.get("depth")) for ref in seg.get("videos") or []]
        detail = top if prefer != "fewer" else low
        videos = [D.ref_video_fit(w, h, *(low if d else detail)) + (n,) for (w, h, n), d in zip(videos, flags)]
        mode = "native"
    pick = D.plan_ref_video_sizes(mode, width, height, length, videos, images, "match", extra)
    est = pick["est"] or D.estimate_segment_tokens(width, height, length, videos, images, "match", extra)
    if is_light(seg, extra):
        # H3MD notile-split (2026-10-29): without reference videos / RefMods the full-size canvas never sits in VRAM
        # at once any more - pass 1 runs it at low res (lowres_scale 0.5 = 1/4 of the tokens) and pass 2 runs in
        # high-res strips (h3md_tiled_hires, up to 12).  The 215k line was measured on shots WITH reference videos
        # (they OOMed in the low-res stage), so those keep the full-canvas estimate below.
        return int(est["total"] - est["canvas"] + math.ceil(est["canvas"] * LIGHT_CANVAS_SHARE))
    return int(est["total"])


LIGHT_CANVAS_SHARE = 0.25   # low-res pass at lowres_scale 0.5 (the largest single canvas the run holds)


def is_light(seg: dict, extra: int = 0) -> bool:
    """A shot with no reference video and no RefMod (text only, or pictures only)."""
    return not (seg.get("videos") or []) and not int(extra or 0)


def max_length(D, seg, sizes, width, height, mode, budget, extra=0, cap=2000, prefer="auto"):
    """Largest 5+17n length whose estimate fits the budget (0 if not even 22 frames fit)."""
    best = 0
    for n in range(1, (cap - 5) // 17 + 1):
        length = 5 + 17 * n
        if part_tokens(D, seg, sizes, width, height, mode, length, extra, prefer) <= budget:
            best = length
        else:
            break
    return best


def plan_parts(content: int, ov0: int, lmax: int, motion=None):
    """Content frames of each part (list) or None when no useful split exists.
    content = shot length - ov0; part 1 = ov0 + c1 frames, part j>1 = AUTO_OVERLAP + cj frames."""
    r0 = content % 17                       # 5 for a first shot / cut, 0 for a continued shot
    cap1_u = (lmax - ov0 - r0) // 17        # 17-frame units part 1 may carry
    capn_u = (lmax - AUTO_OVERLAP) // 17
    min_u = int(math.ceil(MIN_PART_CONTENT / 17))
    if capn_u < min_u or cap1_u < min_u:
        return None
    units = (content - r0) // 17
    n = 1
    while cap1_u + (n - 1) * capn_u < units:
        n += 1
    if n == 1:
        return [content]
    base, extra = divmod(units, n)
    us = [base + (1 if j < extra else 0) for j in range(n)]
    if us[0] > cap1_u:                      # first part has a smaller cap (bigger overlapIn): move units right
        for j in range(1, n):
            give = min(us[0] - cap1_u, capn_u - us[j])
            us[0] -= give
            us[j] += give
    if motion:
        def frame_score(f):                 # motion around content frame f (window +-3 frames)
            lo, hi = max(0, f - 3), min(len(motion), f + 4)
            return sum(motion[lo:hi]) / max(1, hi - lo) if hi > lo else 0.0
        for j in range(n - 1):              # boundary between part j and j+1
            best_d, best_s = 0, None
            for d in range(-NUDGE_UNITS, NUDGE_UNITS + 1):
                a, b = us[j] + d, us[j + 1] - d
                cap_a = cap1_u if j == 0 else capn_u
                if a < min_u or b < min_u or a > cap_a or b > capn_u:
                    continue
                f = r0 + 17 * (sum(us[:j]) + a)
                s = frame_score(f)
                if best_s is None or s < best_s - 1e-9:
                    best_d, best_s = d, s
            us[j] += best_d
            us[j + 1] -= best_d
    if any(u < min_u for u in us):
        return None
    return [r0 + 17 * us[0]] + [17 * u for u in us[1:]]


def split_segment(D, seg: dict, media: dict, contents: list) -> list:
    """seg (a parsed shot) -> its parts.  contents = new frames per part (plan_parts)."""
    s0, ov0, fps = int(seg["startFrame"]), int(seg["overlapIn"]), D.FPS
    parts, off, start = [], 0, s0
    for j, c in enumerate(contents):
        ov = ov0 if j == 0 else AUTO_OVERLAP
        p = copy.deepcopy(seg)
        p["length"] = ov + c
        p["overlapIn"] = ov
        p["startFrame"] = start
        for ref in p["videos"]:              # first NEW frame of the part = shot content frame `off`
            ref["trimStart"] = round(float(ref["trimStart"]) + off / fps, 6)
        lk = p.get("locked")
        if p.get("audioMode") == "locked" and isinstance(lk, dict):
            if lk.get("kind") == "video":
                vref = next((r for r in p["videos"] if r["id"] == lk["id"]), None)
                if vref is not None:
                    lk["offset"] = D.video_lock_offset(vref["trimStart"], ov)
            else:                            # locked audio file: frame 0 of the part = shot frame (start - s0)
                lk["offset"] = round(float(lk.get("offset") or 0.0) + (start - s0) / fps, 6)
        if p.get("audios"):
            base = dict(p.get("audioStarts") or {})
            p["audioStarts"] = {a: round(float(base.get(a, 0.0)) + (start - s0) / fps, 6) for a in p["audios"]}
        p["shotIndex"] = seg["index"]
        p["autoPart"], p["autoParts"] = j, len(contents)
        p["labels"] = D.compute_labels(p, media)
        parts.append(p)
        start = start + p["length"] - (AUTO_OVERLAP if j + 1 < len(contents) else 0)
        off += c
    return parts


def apply(D, segments: list, media: dict, raw: dict, width: int, height: int, mode: str, notes: list,
          motion=None, extra_tokens=None, budget=None, sizes=None, report=None) -> list:
    """Expand every over-budget shot.  motion(seg) -> per-frame motion of its first video (content timeline) or None.
    extra_tokens: per raw shot (RefMods).  budget: tokens (None = token_budget(raw)).  Returns the new segment list
    (re-indexed); notes get one line per split shot."""
    if raw.get("autoSplit", True) is False:
        return segments
    if budget is None:
        b = token_budget(raw, D.REF_VIDEO_AUTO_TOKEN_LIMIT)
        budget = b["tokens"]
        notes.append(f"auto-split budget {budget:,} tokens (avail {b['avail_gib']} GiB after "
                     f"{'ComfyUI reserve ' + str(b['reserve_gib']) + ' GiB' if b['reserve_gib'] is not None else 'calibration'}"
                     f", safety {b['safety']})")
    prefer = str(raw.get("autoSplitPrefer") or "auto").lower()
    if prefer not in ("auto", "detail", "fewer"):
        prefer = "auto"
    out = []
    for seg in segments:
        extra = int((extra_tokens or {}).get(seg["index"], 0) or 0)
        est = part_tokens(D, seg, sizes or {}, width, height, mode, seg["length"], extra, prefer)
        entry = {"shot": seg["index"], "tokens": est, "budget": budget, "length": int(seg["length"]),
                 "parts": [int(seg["length"])], "status": "ok", "light": is_light(seg, extra)}
        if report is not None:
            report.append(entry)
        if est <= budget:
            out.append(seg)
            continue
        lmax = max_length(D, seg, sizes or {}, width, height, mode, budget, extra, prefer=prefer)
        content = int(seg["length"]) - int(seg["overlapIn"])
        prof = None
        if motion is not None:
            try:
                prof = motion(seg, content)
            except Exception:
                prof = None
        contents = plan_parts(content, int(seg["overlapIn"]), lmax, prof) if lmax else None
        n = seg["index"] + 1
        if not contents or len(contents) == 1:
            entry.update(status="over", maxPart=lmax)
            notes.append(f"WARNING segment {n}: estimated {est:,} tokens > budget {budget:,}, but it cannot be split "
                         f"usefully (max part {lmax} f); lower the resolution or the reference count")
            out.append(seg)
            continue
        if is_light(seg, extra):
            notes.append(f"WARNING segment {n}: a shot without a reference video is split automatically - every part "
                         f"gets the whole prompt, so later parts may replay its opening (cut / sound restart). "
                         f"Better: split it by hand into shots with their own prompts, or shorten it")
        parts = split_segment(D, seg, media, contents)
        entry.update(status="split", parts=[int(p["length"]) for p in parts], overlap=AUTO_OVERLAP, maxPart=lmax,
                     partTokens=[part_tokens(D, p, sizes or {}, width, height, mode, p["length"], extra, prefer) for p in parts])
        cuts = []
        acc = 0
        for c in contents[:-1]:
            acc += c
            cuts.append(f"{acc / D.FPS:.2f}s")
        notes.append(f"segment {n}: auto-split, estimated {est:,} tokens > budget {budget:,} -> {len(parts)} parts "
                     f"{' + '.join(str(p['length']) for p in parts)} f (overlap {AUTO_OVERLAP}, each <= "
                     f"{max(part_tokens(D, p, sizes or {}, width, height, mode, p['length'], extra, prefer) for p in parts):,} tokens; "
                     f"cuts at {', '.join(cuts)} of the shot's new content"
                     f"{', cut points checked against the reference motion' if prof else ''})")
        out.extend(parts)
    for i, seg in enumerate(out):
        seg["index"] = i
    return out


def preview(plan_json, width: int = 0, height: int = 0) -> dict:
    """Editor route /h3md/autosplit: the Director's own parse (same budget, sizes, RefMod tokens, and since 2026-10-27
    the same reference-motion cut placement), so the parts shown are exactly the parts the run uses.  -> {"ok", "budget", "budgetInfo", "width",
    "height", "segments", "totalFrames", "shots": [{shot, tokens, budget, length, parts, status, ...}]}"""
    import json
    try:
        from . import h3md_director as D
    except ImportError:
        import h3md_director as D
    try:
        raw = json.loads(plan_json) if isinstance(plan_json, str) else json.loads(json.dumps(plan_json))
        D.refresh_video_durations(raw)
        sizes = probe_media_sizes(raw, D.resolve_media_path)
        try:
            try:
                from . import h3md_director_refmod as R
            except ImportError:
                import h3md_director_refmod as R
            extra = R.refmod_tokens_by_shot(raw)
        except Exception:
            extra = {}
        report = []
        plan = D.parse_plan(raw, int(width or 0), int(height or 0), True, motion=D.make_motion(raw), sizes=sizes,
                            extra_tokens=extra, report=report)
        return {"ok": True, "budget": plan.get("tokenBudget"), "budgetInfo": plan.get("tokenBudgetInfo"),
                "width": plan["width"], "height": plan["height"], "segments": len(plan["segments"]),
                "totalFrames": plan["total_frames"], "shots": report}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
