"""H3MD window mode (window-exp, 2026-11-07; 0.4.0: 接续窗口 default).

Window mode renders one whole shot in a single sampling run with the core MiniMax H3 context windows: every
denoising step goes over all windows, so there is no continuation seam.  Low-res pass (0.4.0): 接续窗口 - the
windows run in time order and a window's overlap with the windows before it is pinned to their result of the same
step (no averaging, so no double images; overlap 22).  High-res pass: the core's averaged windows (175 / 22; it only
refines the upscaled low-res result, 11-08 tests showed no double images there).
H3MD adds these things on top of the core MiniMaxH3ContextWindows patch:

1. Guide video cropped per window.  Core passes reference videos whole to every window.  A timeline-aligned
   reference video (latent length == the target's, i.e. the motion-copy guide with useDuration = shot length) is
   cropped to the window's latent range (plus its soundtrack), so TARA matches window time t with guide time t.
   11-06/07 tests: without it every window re-copied the first seconds (repeated poses) and ran 2.5x slower.
2. The packed layout is rebuilt for the window before the model function runs (attention patches that wrap the
   model function, e.g. BFS / TARA temporal alignment, read payload["layout"] first).
3. Window prompts.  A segment may carry several prompts with start times (plan segment "windowPrompts").  The
   encoder tags each conditioning with its frame span (key SPAN_KEY); each window uses the prompt whose span holds
   the window's centre (chained low-res windows: the window's last frame, 0.4.0), instead of the core's even
   split by count.  Without it every window re-plays the whole
   action list of the one prompt (11-07 test: the hair-brush gesture came back every window).

Plan keys (Director passes them through; old plans are unchanged):
  "windowMode": true | {"length": 243, "overlap": 22, "highLength": 175, "highOverlap": 22, "chain": true,
                        "freenoise": true, "cropGuide": true}
  true / {} = exactly what the Director's switch writes: low 243 / 22 chained, high 175 / 22.  "chain": false = the
  core's averaging, low overlap 56.  A "length" / "overlap" given without highLength / highOverlap is used for both
  passes (as before; earlier builds used a 124-frame window for `true`).
  segment "windowPrompts": [{"at": 0.0, "prompt": "..."}, {"at": 5.0, "prompt": "..."}]   (seconds from shot start)
Window mode turns autosplit off (one sampling run per shot); hard cuts and continuation segments stay as before.
"""
from __future__ import annotations

import logging
import re

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Window]"
_KEY = "h3md_window"
SPAN_KEY = "h3md_window_span"          # conditioning dict key: (start_frame, end_frame) in shot pixel frames
DEFAULTS = {"videoPerWindow": "auto", "length": 243, "overlap": None, "highLength": None, "highOverlap": None, "freenoise": True,
            "cropGuide": True, "preview": False,
            "refs": True, "groups": True, "groupLength": 1445,
            "redraw": None,
            # 11-08 接续窗口 (sequential windows, low-res pass only): each step runs the windows in time order and a
            # window's overlap with the previous one is pinned to that window's result of the same step instead of
            # being averaged (averaging two different pictures = the ghosting of the 2:20 test video).  chainPin is
            # the noise level the pinned rows run at, as a fraction of the step's sigma (0 = kept like a
            # continuation prefix).  Default ON since the 11-08 tests (27 s and the two turns: no double images,
            # same speed); "chain": false = the core's averaging again.
            "chain": True, "chainPin": 0.0}
OVERLAP_CHAIN = 22         # default low-res overlap with 接续窗口 (enough context once the overlap is pinned, 11-08 tests)
OVERLAP_AVERAGED = 56      # default low-res overlap without it (56 removed the 11-07 ghosting case of averaged windows)
HIGH_LENGTH = 175          # default high-res window (11-07: 243 filled a 32 GB card at 1088x1664; 175 is 1/3 faster)
HIGH_OVERLAP = 22          # default high-res overlap (the high-res pass refines the low-res result; 22 showed no seams)
MAX_TRAINED = 362          # H3 was trained on ~124-362 frame clips; longer windows are outside that range
FPS = 24


def _round_17(frames: int, lo: int) -> int:
    """Nearest 17n+5 >= lo."""
    n = max(0, round((int(frames) - 5) / 17))
    return max(lo, 5 + 17 * n)


def auto_group_length(width: int, height: int, total_ram_gb: float = None):
    """window-exp (11-07): longest group (frames, 17n+5) for this machine.  Since the chunked decode (a group is decoded
    ~10 s at a time straight into the disk cache) the decode no longer grows with the group; what is left in RAM is the
    models moved off the GPU (~18 GB) plus the system.  Measured: 64 GB machines run 60 s groups (sampling 30-40 GB).
    Below ~40 GB RAM (not measured yet) the groups stay at ~30 s to keep some headroom."""
    if total_ram_gb is None:
        try:
            import psutil
            total_ram_gb = psutil.virtual_memory().total / 1e9
        except Exception:
            total_ram_gb = 64.0
    frames = 1450 if total_ram_gb >= 40 else 719
    return frames, f"{total_ram_gb:.0f} GB RAM (decode in ~10 s chunks)"


def normalize_window_mode(value):
    """Plan 'windowMode' -> dict with DEFAULTS filled in, or None (off).  Lengths snap to 17n+5."""
    if value in (None, False, 0, "", "off"):
        return None
    out = dict(DEFAULTS)
    if isinstance(value, dict):
        for k in DEFAULTS:
            if k in value and value[k] is not None:
                out[k] = value[k]
    out["chain"] = bool(out["chain"])
    if out["overlap"] is None:
        out["overlap"] = OVERLAP_CHAIN if out["chain"] else OVERLAP_AVERAGED
    out["length"] = _round_17(out["length"], 22)
    out["overlap"] = min(_round_17(out["overlap"], 5), out["length"] - 17)
    # 11-07: the high-res pass may need a smaller window than low-res (243 filled a 32 GB card at 1088x1664).
    # 0.4.0: not given -> 175 / 22 (what the Director writes), unless the plan set the low-res value itself
    given = value if isinstance(value, dict) else {}
    hl = out["highLength"] or (out["length"] if given.get("length") is not None else HIGH_LENGTH)
    out["highLength"] = _round_17(hl, 22)
    ho = out["highOverlap"] or (out["overlap"] if given.get("overlap") is not None else HIGH_OVERLAP)
    out["highOverlap"] = min(_round_17(ho, 5), out["highLength"] - 17)
    for k in ("length", "highLength"):
        if out[k] > MAX_TRAINED:
            LOG.warning("%s window %s %d frames is longer than H3's trained range (<= %d)", _TAG, k, out[k], MAX_TRAINED)
    out["freenoise"] = bool(out["freenoise"])
    out["cropGuide"] = bool(out["cropGuide"])
    out["preview"] = bool(out["preview"])
    out["refs"] = bool(out["refs"])
    out["groups"] = bool(out["groups"])            # 3e: multi-shot plans -> one timeline per group
    out["chainPin"] = min(1.0, max(0.0, float(out["chainPin"] or 0.0)))
    out["groupLength"] = _round_17(out["groupLength"], 22)
    v = out["videoPerWindow"]   # 3d: "auto" = when a reference video is longer than H3's ~15 s limit
    out["videoPerWindow"] = "auto" if v in ("auto", None) else bool(v)          # 3c: each auto window prompt gets only the pictures it names   # low-res preview: stop after the low-res pass
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


# ------------------------------------------------------------------------------------------- 3a: timed prompts
FRAME_PER_TOKEN = (1, 4, 4, 4, 4)
TIME_MARK = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*s(\s+cut)?\s*\]", re.I)
# 11-08: the skill's own lyric / line timing 'At 00:12.7 she sings: ...' (minutes:seconds WITH a decimal, so a clock
# time in the story such as 'at 10:30' is never taken) counts as a '[12.7s]' mark in window mode
CLOCK_MARK = re.compile(r"\bAt (\d{1,2}):(\d{2}\.\d{1,3})(?=[\s,:;])\s*,?\s*")


def clock_marks(prompt: str) -> str:
    """'At 00:12.7 she sings: ...' -> '[12.7s] she sings: ...' (the time is from the shot's own start)."""
    return CLOCK_MARK.sub(lambda m: f"[{int(m.group(1)) * 60 + float(m.group(2)):g}s] ", prompt or "")
# 11-08 review: a prompt without reference media (T2VA) carries its description in integrated_multimodal_description
BODY_SECTIONS = ("detailed_description", "integrated_multimodal_description")
SECTION = re.compile(r"(?m)^\s*(subject_definitions|summary|retention_analysis|detailed_description|"
                     r"integrated_multimodal_description|"
                     r"overall_soundscape|non_diegetic_music|dialogue|sound_effects)\s*:")
NO_NEW_ACTION = ("No new action starts in this part; whatever is moving keeps moving the same way as the moment "
                 "before.")   # 11-07: no "subject" wording - an empty landscape window must not invite a person
CUT_KEY = "h3md_window_cuts"
RANGE_KEY = "h3md_window_range"   # 3d: conditioning made for exactly this window (pixel frames [a, b))
MAX_REF_VIDEO = 362                # H3 reference videos: ~15 s


def video_latent_t(frames: int) -> int:
    """Latent length of a 17n+5-frame clip (5n+2), as the core windows node computes it."""
    return ((int(frames) - 5) // 17) * 5 + 2


def latent_pixel_starts(latent_t: int):
    starts = [0]
    for k in range(int(latent_t)):
        starts.append(starts[-1] + FRAME_PER_TOKEN[k % 5])
    return starts


def _core_latent_windows(n: int, cl: int, co: int):
    if n <= cl:
        return [(0, n - 1)]
    out, delta = [], cl - co
    for start in range(0, n, delta):
        if start + cl >= n:
            s0 = n - cl
            out.append((s0, s0 + cl - 1))
            break
        out.append((start, start + cl - 1))
    return out


def cut_latents(cut_frames, total_frames: int):
    """Hard cuts (pixel frames) -> latent indices on the 5-latent chunk grid (pixel 17k), inside the shot, sorted."""
    n = video_latent_t(total_frames)
    out = set()
    for f in cut_frames or ():
        c = 5 * int(round(float(f) / 17.0))
        if 0 < c < n:
            out.add(c)
    return sorted(out)


def _block_windows(b0: int, b1: int, cl: int, co: int):
    """Windows covering latents [b0, b1): starts on the 5-latent grid (each window opens on a 1-frame latent, as in a
    real clip), the first starts at b0, the last ends at b1, evenly spaced, overlap >= co."""
    L = b1 - b0
    if L <= cl:
        return [(b0, b1 - 1)]
    n = 2
    while True:
        last = b0 + ((L - cl) // 5) * 5            # last start on the grid, its window ends at b1 (length cl..cl+4)
        starts = [b0 + 5 * round((last - b0) / 5 * i / (n - 1)) for i in range(n)]
        ok = all(starts[i] + cl - 1 - starts[i + 1] + 1 >= co for i in range(n - 1))
        if ok or n > 64:
            break
        n += 1
    wins = [(st, st + cl - 1) for st in starts[:-1]] + [(starts[-1], b1 - 1)]
    return wins


def core_windows(total_frames: int, length: int, overlap: int, cuts=()):
    """Latent (first, last) windows.  No cuts: exactly the core MiniMax H3 static schedule (comfy.context_windows.
    create_windows_static_standard after MiniMaxH3ContextWindowsNode's frame -> latent).  With hard cuts (pixel
    frames, 3b): every cut is a window boundary with no overlap (nothing is fused across a cut) and each part between
    cuts gets its own evenly spaced windows."""
    n = video_latent_t(total_frames)
    cl = video_latent_t(length)
    co = min(video_latent_t(overlap), cl - 5)
    return latent_schedule(n, cl, co, cut_latents(cuts, total_frames))


def latent_schedule(n: int, cl: int, co: int, cut_lats=()):
    """Windows over n latents (window cl, overlap co, both in latents); cut_lats = hard cuts on the 5-latent grid."""
    if not cut_lats:
        return _core_latent_windows(n, cl, co)
    out, edges = [], [0] + list(cut_lats) + [n]
    for b0, b1 in zip(edges, edges[1:]):
        out += _block_windows(b0, b1, cl, co)
    return out


def window_spans(total_frames: int, length: int, overlap: int, cuts=(), chain: bool = False):
    """Pixel-frame ranges [a, b) of the windows and each window's own span: from the middle of its overlap with the
    previous window to the middle of its overlap with the next (at a hard cut: the cut).  The spans tile the shot, and
    each window's centre lies in its own span, so per-window prompts tagged with these spans are picked once.
    chain (接续窗口, 11-08): a window generates only what the windows before it did not (the overlap is pinned to
    their result), so its own span runs from the previous window's END to its own end (the first window, and the
    first one after a cut, from its start); a timed action then goes to the window that really draws those frames."""
    n = video_latent_t(total_frames)
    px = latent_pixel_starts(n)
    wins = [(px[a], px[b + 1]) for a, b in core_windows(total_frames, length, overlap, cuts)]
    spans = []
    for i, (a, b) in enumerate(wins):
        if chain:
            lo = 0 if i == 0 else max(a, wins[i - 1][1])
            hi = int(total_frames) if i == len(wins) - 1 else b
        else:
            lo = 0 if i == 0 else (a + wins[i - 1][1]) // 2
            hi = int(total_frames) if i == len(wins) - 1 else (wins[i + 1][0] + b) // 2
        spans.append((lo, hi))
    return wins, spans


# ------------------------------------------------------------------------------------------- local redraw
REDRAW_RAMP = 0   # latents of soft mask on each side of a redrawn range.  0.4.0: 0 (hard edges).  The 11-08
                  # 2:20 test with 3 showed double exposures in the ramp: a fractional noise mask mixes the new take
                  # and the kept one at every step.  With hard edges the model sees the kept frames on both sides as
                  # known frames (like a continuation prefix / rerun tail lock) and joins the new seconds to them.


def redraw_ranges(window_mode, seg_start: int, seg_len: int, fps: int = FPS):
    """windowMode.redraw = [[start_s, end_s], ...] on the OUTPUT timeline -> [(f0, f1)] frames inside this segment
    (clipped), or [] when none touches it."""
    out = []
    for r in (window_mode or {}).get("redraw") or []:
        try:
            a, b = float(r[0]), float(r[1])
        except Exception:
            continue
        f0, f1 = max(0, int(round(a * fps)) - seg_start), min(seg_len, int(round(b * fps)) - seg_start)
        if f1 > f0:
            out.append((f0, f1))
    return out


def redraw_masks(latent_t: int, audio_t: int, ranges, ramp: int = REDRAW_RAMP):
    """(video mask [T] float, audio mask [A] float, (first, end) latents to compute) for redrawing frame `ranges`:
    1 inside, 0 kept, a linear ramp of `ramp` latents on each side."""
    import torch
    px = latent_pixel_starts(latent_t)
    total = px[-1]
    vm = torch.zeros(latent_t)
    for f0, f1 in ranges:
        l0 = max(k for k in range(latent_t) if px[k] <= f0)
        l1 = min([k for k in range(latent_t + 1) if px[k] >= f1] or [latent_t])
        vm[l0:l1] = 1.0
        for j in range(1, ramp + 1):
            w = 1.0 - j / (ramp + 1)
            if l0 - j >= 0:
                vm[l0 - j] = max(float(vm[l0 - j]), w)
            if l1 - 1 + j < latent_t:
                vm[l1 - 1 + j] = max(float(vm[l1 - 1 + j]), w)
    am = torch.zeros(audio_t)
    for k in range(latent_t):
        if vm[k] > 0:
            a0, a1 = round(px[k] * audio_t / total), round(px[k + 1] * audio_t / total)
            am[a0:a1] = torch.maximum(am[a0:a1], torch.full((max(0, a1 - a0),), float(vm[k])))
    nz = torch.nonzero(vm > 0).flatten()
    active = (int(nz[0]), int(nz[-1]) + 1) if nz.numel() else None
    return vm, am, active


def set_active(models, active):
    """Tell the window handlers of `models` which latents a local redraw computes (None = all)."""
    for m in models:
        h = (getattr(m, "model_options", None) or {}).get("context_handler")
        if h is not None:
            h._h3md_active = active


def all_window_ranges(total_frames: int, window_mode: dict, cuts=()):
    """3d: pixel ranges of every window the low-res and the high-res pass will use (sorted, unique)."""
    out = set()
    for ln, ov in ((window_mode["length"], window_mode["overlap"]),
                   (window_mode["highLength"], window_mode["highOverlap"])):
        out.update(window_spans(total_frames, ln, ov, cuts)[0])
    return sorted(out)


SEQ_WORDS = re.compile(r"\b(then|after(?:wards)?|later|finally|first|next|before|followed by|eventually)\b", re.I)


def _first_sentence(text: str):
    """(first sentence, rest) of a text."""
    parts = _SENT_SPLIT.split(text.strip(), maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_timed_prompt(prompt: str):
    """Parse the time marks of detailed_description, written anywhere in it:
      '[12s] <one sentence>'      an action at 12 s (only the sentence right after the mark; anything after it belongs
                                  to the current shot, or to the shared description before any shot)
      '[8s cut] <text>'           a hard cut at 8 s; all text up to the next mark describes that shot
    Returns (before, common, shots, acts, after) or None without marks: before/after = text before / after the
    detailed_description body, common = shared description, shots = [(sec, text)], acts = [(sec, text)] (time order)."""
    prompt = clock_marks(prompt)
    if not prompt or not TIME_MARK.search(prompt):
        return None
    heads = list(SECTION.finditer(prompt))
    det = next((m for m in heads if m.group(1) in BODY_SECTIONS), None)
    if det is None:
        return None
    nxt = next((m.start() for m in heads if m.start() > det.end()), len(prompt))
    body = prompt[det.end():nxt]
    marks = list(TIME_MARK.finditer(body))
    if not marks:
        return None
    common = [body[:marks[0].start()].strip()]
    shots, acts, cur = [], [], None
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        txt = body[m.end():end].strip()
        sec = float(m.group(1))
        if m.group(2):
            cur = [sec, txt]
            shots.append(cur)
            continue
        first, rest = _first_sentence(txt)
        if first:
            acts.append((sec, first))
        if rest:
            if cur is not None:
                cur[1] = (cur[1] + " " + rest).strip()
            else:
                common.append(rest)
    shots = sorted((s, t) for s, t in shots)
    acts.sort(key=lambda e: e[0])
    return prompt[:det.end()], " ".join(x for x in common if x), shots, acts, prompt[nxt:]


def strip_sequence(text: str):
    """Window mode: drop summary sentences that tell an order of events ('... then ...'): every window gets the
    summary, and such a sentence makes the windows after a cut replay the earlier part.  (text, removed sentences)"""
    secs = _sections(text)
    removed, out = [], []
    for n, t in secs:
        if n == "summary":
            head, _, rest = t.partition(":")
            keep = []
            for x in _SENT_SPLIT.split(rest.strip()):
                (removed if SEQ_WORDS.search(x) else keep).append(x)
            t = head + ":\n" + " ".join(k for k in keep if k) + "\n\n"
        out.append(t)
    return ("".join(out), removed) if removed else (text, [])


def prompt_cuts(prompt: str, fps: int = FPS):
    """Hard cuts written as '[8s cut] ...' -> pixel frames (a '[0s cut]' opens the first shot, not a cut)."""
    parts = split_timed_prompt(prompt)
    if parts is None:
        return []
    return sorted({int(round(sec * fps)) for sec, _ in parts[2] if sec > 0})


def auto_window_prompts(prompt: str, total_frames: int, length: int, overlap: int, fps: int = FPS, cuts=None,
                        chain: bool = False):
    """3a/3b: one timed prompt -> per-window prompts [{"start", "end", "prompt", "events"}] (pixel frames), each action
    given to exactly ONE window (the one whose own span holds its time).  A '[Ns cut] text' mark starts a new shot:
    its text describes every window after the cut (until the next cut), and windows never straddle it.  None when the
    prompt has no time marks or the shot fits one window."""
    parts = split_timed_prompt(prompt)
    if parts is None:
        return None
    before, common, shots, acts, after = parts
    if cuts is None:
        cuts = prompt_cuts(prompt, fps)
    wins, spans = window_spans(total_frames, length, overlap, cuts, chain)
    if len(wins) < 2:
        return None
    out = []
    for (a, b), (lo, hi) in zip(wins, spans):
        r = resolve_window(parts, a, b, lo, hi, 0, fps)
        out.append({"start": lo, "end": hi, "prompt": r["prompt"], "events": r["events"], "own": r["own"],
                    "removed": r["removed"]})
    return out


def resolve_window(parts, a, b, lo, hi, origin=0, fps: int = FPS):
    """One window's prompt from a parsed timed prompt (split_timed_prompt): the shot text in force at the window
    centre, the actions whose time falls in the window's OWN span [lo, hi) (each action in exactly one window) with
    their time inside the window, else NO_NEW_ACTION.  a, b, lo, hi are frames on the timeline; the marks count from
    `origin` (the shot's own frame 0 on that timeline: 0 for a single shot, the shot start inside a group)."""
    before, common, shots, acts, after = parts
    before, removed = strip_sequence(before)
    mid = (a + b) / 2.0
    shot = [t for sec, t in shots if origin + sec * fps <= mid]
    mine = [(sec, t) for sec, t in acts if lo <= origin + sec * fps < hi]
    # the time inside this window, so H3 places the action near the right second (11-07: about one window off)
    action = " ".join(f"At about {max(0.0, (origin + sec * fps - a) / fps):.0f} s into this part, "
                      f"{t[0].lower() + t[1:]}" for sec, t in mine if t) if mine else NO_NEW_ACTION
    own = " ".join(x for x in (shot[-1] if shot else "", " ".join(t for _, t in mine)) if x)
    body = " ".join(x for x in (common, shot[-1] if shot else "", action) if x)
    return {"prompt": f"{before}\n{body}\n\n{after.lstrip()}" if after else f"{before}\n{body}",
            "events": len(mine), "own": own if shots else None, "removed": removed}


def timed_window_prompt(prompt: str, a: int, b: int, lo: int, hi: int, origin: int = 0, fps: int = FPS):
    """3e (11-08): a group part's prompt for one window: its time marks resolved for this window (resolve_window),
    or the prompt unchanged when it has no marks.  (prompt, events or None)"""
    parts = split_timed_prompt(prompt)
    if parts is None:
        return prompt, None
    r = resolve_window(parts, a, b, lo, hi, origin, fps)
    return r["prompt"], r["events"]


def window_span_map(total_frames: int, window_mode: dict, cuts=()):
    """{(a, b): (lo, hi)} for the windows of both passes: each window's own span (the first pass wins a tie).  The
    low-res windows use the 接续窗口 spans when chain is on; the high-res pass is never chained."""
    out = {}
    for ln, ov, ch in ((window_mode["length"], window_mode["overlap"], bool(window_mode.get("chain"))),
                       (window_mode["highLength"], window_mode["highOverlap"], False)):
        wins, spans = window_spans(total_frames, ln, ov, cuts, ch)
        for w, s in zip(wins, spans):
            out.setdefault(w, s)
    return out


# ------------------------------------------------------------------------------------------- 3c: refs per window
PIC_TAG = re.compile(r"<Picture (\d+)>")
PERSON_WORDS = re.compile(r"\b(she|he|they|her|his|their|him|them)\b", re.I)   # not "people": "no people"
SUBJ_TAG = re.compile(r"<Subject (\d+)>")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _sections(prompt: str):
    """[(name or None, text)] in order; None = text before the first section header."""
    heads = list(SECTION.finditer(prompt))
    out = [(None, prompt[:heads[0].start()] if heads else prompt)]
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(prompt)
        out.append((m.group(1), prompt[m.start():end]))
    return out


def window_refs(prompt: str, n_images: int, own: str = None):
    """3c: the reference pictures one window prompt really uses, and the prompt rewritten for them.
    Used = pictures named in detailed_description / dialogue / sound sections, plus the pictures that define a subject
    named there (subject_definitions line '<Subject k> ... <Picture j> ...').  Returns (kept, prompt): kept = 0-based
    image indices in order (pictures renumbered 1..len(kept)); definition / retention lines and summary sentences that
    name only dropped subjects / pictures are removed.  (list(range(n)), prompt) when nothing would change."""
    secs = _sections(prompt)
    body = " ".join(t for n, t in secs if n not in (None, "subject_definitions", "summary", "retention_analysis"))
    if own is not None:   # a shot of its own (hard cuts): only that shot's text decides, not the shared description
        body = own + " " + " ".join(t for n, t in secs if n in ("dialogue",))
    subj_pics = {}
    for n, t in secs:
        if n == "subject_definitions":
            for line in t.splitlines():
                m = re.match(r"\s*<Subject (\d+)>", line)
                if m:
                    subj_pics.setdefault(int(m.group(1)), set()).update(int(x) for x in PIC_TAG.findall(line))
    subjects = {int(x) for x in SUBJ_TAG.findall(body)}
    pics = {int(x) for x in PIC_TAG.findall(body)}
    for k in subjects:
        pics |= subj_pics.get(k, set())
    kept = [i for i in range(n_images) if i + 1 in pics]
    if not kept and PERSON_WORDS.search(body):
        # 11-08: the window's text talks about people ("they keep dancing") without naming <Subject N>: keep every
        # picture rather than lose the identities (only a text with no person at all drops them)
        kept = list(range(n_images))
    if len(kept) == n_images:
        return list(range(n_images)), prompt
    renum = {old + 1: new + 1 for new, old in enumerate(kept)}

    def unused(text):
        return any(int(x) not in renum for x in PIC_TAG.findall(text)) or \
            any(int(x) not in subjects for x in SUBJ_TAG.findall(text))

    out = []
    for n, t in secs:
        if n in ("subject_definitions", "retention_analysis"):
            lines = t.splitlines(keepends=True)
            t = lines[0] + "".join(l for l in lines[1:] if not (PIC_TAG.search(l) or SUBJ_TAG.search(l)) or not unused(l))
        elif n in ("summary",) + BODY_SECTIONS:
            head, _, rest = t.partition(":")
            sents = [x for x in _SENT.split(rest.strip()) if x and not unused(x)]
            t = head + ":\n" + " ".join(sents) + "\n\n"
        out.append(t)
    text = PIC_TAG.sub(lambda m: f"<Picture {renum.get(int(m.group(1)), int(m.group(1)))}>", "".join(out))
    return kept, text


def _detail_body(prompt: str) -> str:
    for n, t in _sections(prompt):
        if n in BODY_SECTIONS:
            return t.partition(":")[2].strip()
    return ""


SHOT_TAG = re.compile(r"\[Shot \d+\]\s*", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _continuation(base_body: str, body: str) -> str:
    """11-08 (2:20 MV: a continued shot came out as a CUT): the later shot's text without its '[Shot N]' tag and
    without the sentences the first shot already says (style preamble, identity / singing lines).  A second
    '[Shot 1]' after a repeated style preamble reads as a new shot to H3."""
    seen = {s.strip() for s in _SENTENCE.split(SHOT_TAG.sub("", base_body)) if s.strip()}
    return " ".join(s.strip() for s in _SENTENCE.split(SHOT_TAG.sub("", body)) if s.strip() and s.strip() not in seen)


def combine_part_prompts(parts):
    """3e fix (11-07 ghost at a continuation): a window that covers the end of one continued shot and the start of the
    next gets ONE prompt telling both, in order: the first shot's full prompt, then for each later shot
    'At about N s into this part, with no cut, the same shot continues: <its new sentences>'.  parts = [(seconds into
    the window where the shot starts, prompt)], in time order.  The neighbouring windows then describe the same thing
    in their overlap (before: each window took one shot's prompt, so the overlap mixed two different actions ->
    double image)."""
    base = parts[0][1]
    base_body = _detail_body(base)
    extra = " ".join(f"At about {max(0.0, sec):.0f} s into this part, with no cut, the same shot continues: {txt}"
                     for sec, p in parts[1:] if (txt := _continuation(base_body, _detail_body(p))))
    if not extra:
        return base
    out = []
    for n, t in _sections(base):
        if n in BODY_SECTIONS:
            t = t.rstrip() + " " + extra + "\n\n"
        out.append(t)
    return "".join(out)


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


def pick_span_cond(cond_in, window, at_end: bool = False):
    """Index of the conditioning whose SPAN_KEY span holds the window centre (nearest span otherwise), or None
    when the conds carry no spans.  3d: conds tagged with RANGE_KEY are matched to the window's exact pixel range.
    at_end (接续窗口): use the window's last frame instead of its centre - a chained window's own span is the part it
    generates (previous window's end .. its end), which need not hold its centre (e.g. a short last window)."""
    ranges = [c.get(RANGE_KEY) for c in cond_in]
    if len(cond_in) >= 2 and all(r is not None for r in ranges):
        a, b = window_pixel_span(window)
        for i, r in enumerate(ranges):
            if tuple(r) == (a, b):
                return i
        LOG.warning("%s no conditioning made for window frames %d-%d; using the nearest", _TAG, a, b)
    spans = [c.get(SPAN_KEY) for c in cond_in]
    if len(cond_in) < 2 or any(s is None for s in spans):
        return None
    a, b = window_pixel_span(window)
    mid = (b - 1) if at_end else (a + b) / 2.0
    best, dist = 0, None
    for i, (s, e) in enumerate(spans):
        d = 0.0 if s <= mid < e else min(abs(mid - s), abs(mid - e))
        if dist is None or d < dist:
            best, dist = i, d
    return best


def chain_pin_cond(cond, x, window, rows, m, dim=2):
    """接续窗口: the cond of one window with the video denoise mask lowered to m on the pinned rows (positions in the
    window).  H3 runs a row with mask m at sigma = m * sigma (m = 0: a kept row, like a continuation prefix)."""
    import torch
    import comfy.conds
    mc = cond.get("model_conds") if isinstance(cond, dict) else None
    if not isinstance(mc, dict) or not rows:
        return cond
    anchor = getattr(window, "causal_anchor_index", None)
    lead = 1 if anchor is not None and anchor >= 0 else 0
    tw = len(window.index_list) + lead
    old = mc.get("denoise_mask")
    if old is not None and isinstance(getattr(old, "cond", None), torch.Tensor) and old.cond.ndim > dim \
            and old.cond.shape[dim] == tw:
        mask = old.cond.clone().float()
    else:
        mask = torch.ones((1, 1, tw) + tuple(x.shape[-2:]), dtype=torch.float32, device=x.device)
    sl = tuple([slice(None)] * dim + [[r + lead for r in rows]])
    mask[sl] = torch.clamp(mask[sl], max=float(m))
    mc = dict(mc)
    mc["denoise_mask"] = comfy.conds.CONDRegular(mask)
    cond = dict(cond)
    cond["model_conds"] = mc
    return cond


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
        def should_use_context(self, model, conds, x_in, timestep, model_options):
            if super().should_use_context(model, conds, x_in, timestep, model_options):
                return True
            # 11-08 review: a shot / group no longer than one window that still carries per-window conditionings
            # (several shots, a hard cut, window prompts): windowed anyway, so each conditioning keeps its own range
            # and a cut stays a cut (without windows the sampler would average all of them over the whole clip)
            tagged = sum(1 for clist in conds or () for c in clist or ()
                         if isinstance(c, dict) and (c.get(RANGE_KEY) or c.get(SPAN_KEY) or c.get(CUT_KEY)))
            return tagged >= 2

        def get_resized_cond(self, cond_in, x_in, window, device=None):
            if cond_in is not None:
                k = pick_span_cond(cond_in, window, bool(getattr(self, "_h3md_chain", False)))
                if k is not None:
                    key = (window.index_list[0], window.index_list[-1], k)
                    if key not in self._h3md_seen:
                        self._h3md_seen.add(key)
                        s, e = cond_in[k][SPAN_KEY]
                        LOG.info("%s window latents %d-%d: prompt %d of %d (frames %d-%d)", _TAG, key[0], key[1],
                                 k + 1, len(cond_in), s, e)
                    cond_in = [cond_in[k]]
            out = super().get_resized_cond(cond_in, x_in, window, device)
            pin = getattr(self, "_h3md_pin_now", None)
            if out and pin is not None and tuple(window.index_list) == pin[0]:
                out = [chain_pin_cond(c, x_in, window, pin[1], pin[2], self.dim) for c in out]
            return out

        def execute(self, calc_cond_batch, model, conds, x_in, timestep, model_options):
            cuts = set()
            for clist in conds or ():
                for c in clist or ():
                    cuts.update((c or {}).get(CUT_KEY) or ())
            self._h3md_cuts = sorted(cuts)
            if getattr(self, "_h3md_chain", False):
                out = self._h3md_chain_execute(calc_cond_batch, model, conds, x_in, timestep, model_options)
            else:
                out = super().execute(calc_cond_batch, model, conds, x_in, timestep, model_options)
            if getattr(self, "_h3md_active", None) is not None:
                # local redraw: latents no window covered come back 0/0 = NaN; they are masked (kept) anyway
                import torch
                out = [torch.nan_to_num(o, nan=0.0) if isinstance(o, torch.Tensor) else o for o in out]
            return out

        def _h3md_chain_execute(self, calc_cond_batch, model, conds, x_in, timestep, model_options):
            """接续窗口 (11-08): the core execute, but windows run in time order and the video rows a window shares
            with the windows before it are pinned to their result of THIS step (input: moved along that result's
            path to m * sigma; output: that result) - each video latent gets exactly one version per step, nothing
            is averaged.  Other modalities (audio) are fused as before."""
            import torch
            import comfy.utils
            self._model = model
            self.set_step(timestep, model_options)
            ws = self._build_window_state(x_in, conds, model)
            nmod = len(ws.latents)
            x0 = ws.latents[0]
            work = x0.clone()                                  # this step's input with the pinned rows written in
            ws.latents = [work] + list(ws.latents[1:])
            d = self.dim
            windows = sorted(self.get_context_windows(model, x0, model_options), key=lambda w: w.index_list[0])
            total = len(windows)
            video = [torch.zeros_like(x0) for _ in conds]
            done = [False] * x0.size(d)
            accum = [[torch.zeros_like(lat) for _ in conds] for lat in ws.latents]
            counts = [[torch.zeros(cw.get_shape_for_dim(lat, md), device=lat.device) for _ in conds]
                      for lat, md in zip(ws.latents, ws.modality_dims)]
            biases = [[[0.0] * lat.shape[md] for _ in conds] for lat, md in zip(ws.latents, ws.modality_dims)]
            for cb in pe.get_all_callbacks(cw.IndexListCallbacks.EXECUTE_START, self.callbacks):
                cb(self, model, x_in, conds, timestep, model_options)
            s = float(timestep.flatten()[0])
            m = float(getattr(self, "_h3md_pin", 0.0))
            plan = []
            try:
                for wi, window in enumerate(windows):
                    rows = [p for p, i in enumerate(window.index_list) if done[i]]
                    idx = [window.index_list[p] for p in rows]
                    if idx:
                        sl = tuple([slice(None)] * d + [idx])
                        prev = video[0][sl]
                        eps = (x0[sl] - (1.0 - s) * prev) / max(s, 1e-6)
                        work[sl] = ((1.0 - m * s) * prev + (m * s) * eps).to(work.dtype)
                        self._h3md_pin_now = (tuple(window.index_list), rows, m)
                        plan.append((wi, idx[0], idx[-1]))
                    else:
                        self._h3md_pin_now = None
                    results = self.evaluate_context_windows(calc_cond_batch, model, x_in, conds, timestep,
                                                            [(wi, window)], model_options, window_state=ws,
                                                            total_windows=total)
                    new = [p for p, i in enumerate(window.index_list) if not done[i]]
                    for res in results:
                        for ci in range(len(conds)):
                            out = res.sub_conds_out[ci][0]
                            if new:
                                dst = tuple([slice(None)] * d + [[window.index_list[p] for p in new]])
                                src = tuple([slice(None)] * d + [new])
                                video[ci][dst] = out[src].to(video[ci].dtype)
                        for mi in range(1, nmod):
                            mod_out = [res.sub_conds_out[ci][mi] for ci in range(len(conds))]
                            self.combine_context_window_results(
                                ws.latents[mi], mod_out, res.sub_conds, res.window.get_window_for_modality(mi),
                                res.window_idx, total, timestep, accum[mi], counts[mi], biases[mi])
                    for i in window.index_list:
                        done[i] = True
                key = ("chain", tuple(tuple(w.index_list[::max(1, len(w.index_list) - 1)]) for w in windows), m)
                if plan and key not in self._h3md_seen:
                    self._h3md_seen.add(key)
                    LOG.info("%s 接续窗口 (chain): %s keep the previous window's result (pin %.2f), no averaging", _TAG,
                             ", ".join(f"w{wi + 1} latents {a}-{b}" for wi, a, b in plan), m)
                result_out = []
                for ci in range(len(conds)):
                    fin = [video[ci]]
                    for mi in range(1, nmod):
                        a = accum[mi][ci]
                        if self.fuse_method.name != cw.ContextFuseMethods.RELATIVE:
                            a = a / counts[mi][ci]
                        fin.append(a)
                    for mi in range(nmod):
                        if ws.guide_latents[mi] is not None:
                            fin[mi] = torch.cat([fin[mi], ws.guide_latents[mi]], dim=ws.modality_dims[mi])
                    if ws.is_multimodal and len(fin) > 1:
                        result_out.append(comfy.utils.pack_latents(fin)[0])
                    else:
                        result_out.append(fin[0])
                return result_out
            finally:
                self._h3md_pin_now = None
                for cb in pe.get_all_callbacks(cw.IndexListCallbacks.EXECUTE_CLEANUP, self.callbacks):
                    cb(self, model, x_in, conds, timestep, model_options)

        def get_context_windows(self, model, x_in, model_options):
            cuts = getattr(self, "_h3md_cuts", None)
            active = getattr(self, "_h3md_active", None)
            if not cuts and active is None:
                return super().get_context_windows(model, x_in, model_options)
            n = x_in.size(self.dim)
            lats = sorted({5 * int(round(f / 17.0)) for f in cuts or () if 0 < 5 * int(round(f / 17.0)) < n})
            wins = latent_schedule(n, self.context_length, self.context_overlap, lats)
            if active is not None:   # local redraw: only the windows that touch the redrawn latents
                a0, a1 = active
                wins = [w for w in wins if w[0] < a1 and w[1] >= a0] or wins
            key = (n, tuple(lats), self.context_length, active)
            if key not in self._h3md_seen:
                self._h3md_seen.add(key)
                LOG.info("%s %swindows %s%s", _TAG, f"hard cuts at latents {lats}: " if lats else "", wins,
                         f" (local redraw of latents {active[0]}-{active[1] - 1}: other windows skipped)"
                         if active is not None else " (no fusion across a cut)")
            return [cw.IndexListContextWindow(list(range(a, b + 1)), dim=self.dim, total_frames=n,
                                              context_overlap=self.context_overlap) for a, b in wins]

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


MIN_COMFYUI = "0.44"   # first ComfyUI with the core MiniMax H3 context windows (MiniMaxH3ContextWindowsNode)


def comfyui_version() -> str:
    try:
        import comfyui_version as v
        return str(v.__version__)
    except Exception:
        return "unknown"


def core_windows_node():
    """0.4.1: the core MiniMax H3 context-window node, or a clear message on a ComfyUI that does not have it (before:
    a bare ImportError).  The Loop wraps its models first thing, so this stops the run before anything is encoded."""
    try:
        from comfy_extras.nodes_context_windows import MiniMaxH3ContextWindowsNode
    except ImportError as e:
        ver = comfyui_version()
        raise RuntimeError(
            f"{_TAG} Seamless long video (window mode, CCW) needs ComfyUI {MIN_COMFYUI} or newer: this ComfyUI ({ver}) "
            f"has no core MiniMax H3 context windows. Update ComfyUI, or untick 'Seamless long video' on the Director. "
            f"/ 无接缝长视频（窗口模式，CCW）需要 ComfyUI {MIN_COMFYUI} 或更新：当前 ComfyUI（{ver}）没有核心的 MiniMax H3 "
            f"Context Windows。请更新 ComfyUI，或在导演台取消勾选「无接缝长视频」。") from e
    return MiniMaxH3ContextWindowsNode


def apply(model, length=124, overlap=56, fuse_method="pyramid", freenoise=True, crop_guide=True, chain=False,
          chain_pin=0.0):
    """Core MiniMax H3 context windows + H3MD additions on a clone of `model` (chain: 接续窗口, 11-08)."""
    MiniMaxH3ContextWindowsNode = core_windows_node()
    res = MiniMaxH3ContextWindowsNode.execute(model, int(length), int(overlap), fuse_method, bool(freenoise), False)
    patched = res.result[0] if hasattr(res, "result") else res[0]
    handler = install(patched.model_options["context_handler"], bool(crop_guide))
    handler._h3md_chain = bool(chain)
    handler._h3md_pin = float(chain_pin)
    return patched


def wrap_models(window_mode: dict, low, high):
    wm = normalize_window_mode(window_mode)
    if wm is None:
        return low, high
    LOG.info("%s window mode: low-res windows %d / overlap %d%s, high-res windows %d / overlap %d frames, freenoise %s, "
             "crop guide %s", _TAG, wm["length"], wm["overlap"],
             f" (接续窗口 chain, pin {wm['chainPin']:.2f})" if wm["chain"] else "", wm["highLength"], wm["highOverlap"],
             wm["freenoise"], wm["cropGuide"])
    low = apply(low, wm["length"], wm["overlap"], "pyramid", wm["freenoise"], wm["cropGuide"], wm["chain"],
                wm["chainPin"])
    if high is not None:
        high = apply(high, wm["highLength"], wm["highOverlap"], "pyramid", wm["freenoise"], wm["cropGuide"])
    return low, high


class H3MDContextWindows:
    """Manual node: the same patch on one MODEL (for hand-built graphs; the Loop applies it itself in window mode).
    0.4.0: defaults = the Loop's low-res pass (243 / 22, 接续窗口 on); `chain` is the last input, so graphs saved
    before keep their values and get chain on."""

    @classmethod
    def INPUT_TYPES(cls):
        import comfy.context_windows as cw
        return {"required": {
            "model": ("MODEL",),
            "context_length": ("INT", {"default": 243, "min": 22, "max": 4096, "step": 17,
                                       "tooltip": "Window length in real frames (17n+5). Loop window mode: low-res 243, "
                                                  "high-res 175."}),
            "context_overlap": ("INT", {"default": 22, "min": 5, "max": 4096, "step": 17,
                                        "tooltip": "Overlap in real frames (17n+5). 22 with chain on; 56 with chain off "
                                                   "(56 removed the 11-07 ghosting case of averaged windows)."}),
            "fuse_method": (cw.ContextFuseMethods.LIST_STATIC, {"default": cw.ContextFuseMethods.PYRAMID}),
            "freenoise": ("BOOLEAN", {"default": True}),
            "crop_guide_video": ("BOOLEAN", {"default": True,
                                             "tooltip": "Crop the timeline-aligned guide video (motion copy) to each window."}),
            "chain": ("BOOLEAN", {"default": True, "tooltip":
                      "接续窗口: windows run in time order and a window's overlap takes the previous window's result "
                      "(no averaging, no double images). For the low-res model; the Loop's window mode leaves the "
                      "high-res model averaged. Off = the core's averaging."}),
        }}

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "run"
    CATEGORY = "H3MD/experimental"
    EXPERIMENTAL = True

    def run(self, model, context_length, context_overlap, fuse_method, freenoise, crop_guide_video, chain=True):
        LOG.info("%s manual node: windows %d / overlap %d, %s, freenoise %s, crop guide %s, chain %s", _TAG,
                 context_length, context_overlap, fuse_method, freenoise, crop_guide_video, chain)
        return (apply(model, context_length, context_overlap, fuse_method, freenoise, crop_guide_video, bool(chain)),)


_TIME = r"(\d+(?:\.\d+)?|\d+:\d{1,2}(?:\.\d+)?)"
_RANGE = re.compile(_TIME + r"\s*(?:-|–|—|~|～|to|到|至)\s*" + _TIME, re.IGNORECASE)


def _seconds(t: str) -> float:
    """'12.5' -> 12.5; '1:05' -> 65.0; '1:05.5' -> 65.5"""
    if ":" in t:
        m, s = t.split(":", 1)
        return int(m) * 60 + float(s)
    return float(t)


def parse_redraw(text: str, total_s: float | None = None):
    """'4-8, 1:12-1:15' -> [[4.0, 8.0], [72.0, 75.0]] (seconds on the whole video's timeline, sorted, merged).
    Separators between ranges: comma, semicolon, new line (Chinese ones too).  Raises ValueError with a message
    the user can act on."""
    text = str(text or "").strip()
    if not text:
        return []
    out = []
    for part in re.split(r"[,，;；\n]+", text):
        part = part.strip()
        if not part:
            continue
        m = _RANGE.fullmatch(part)
        if not m:
            raise ValueError(f"{_TAG} redraw: '{part}' is not a time range - write it like 4-8 or 1:12-1:15 (seconds). "
                             f"局部重绘：'{part}' 看不懂，请写成 4-8 或 1:12-1:15（秒）。")
        a, b = _seconds(m.group(1)), _seconds(m.group(2))
        if b <= a:
            raise ValueError(f"{_TAG} redraw: '{part}' ends before it starts. 局部重绘：'{part}' 结束时间要晚于开始时间。")
        if total_s is not None and a >= total_s:
            raise ValueError(f"{_TAG} redraw: '{part}' starts after the video ends ({total_s:.2f} s). "
                             f"局部重绘：'{part}' 超出视频长度（{total_s:.2f} 秒）。")
        out.append([a, min(b, total_s) if total_s is not None else b])
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in merged]


def plan_seconds(plan) -> float | None:
    segs = (plan or {}).get("segments") or []
    try:
        return max(int(s["startFrame"]) + int(s["length"]) for s in segs) / float(FPS)
    except Exception:
        return None


class H3MDRedrawPreview:
    """0.4.0: low-res preview and local redraw as their own node (YJ 11-08: not on the Director's editor, so the
    old interface stays as it is).  Put it between the Director's 'plan' and the Loop's 'plan'.  Bypassed or both
    switches off = the plan goes through unchanged."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "plan": ("H3MD_PLAN",),
            "preview": ("BOOLEAN", {"default": False, "tooltip":
                        "Low-res pass only (about half the time). Happy with it? Switch this off and run again: the "
                        "full version reuses the low-res pass from the cache.\n"
                        "只跑低清（约省一半时间）。满意后关掉再跑一次，完整版直接用缓存里的低清结果。"}),
            "redraw": ("STRING", {"default": "", "multiline": False, "tooltip":
                       "Seconds of a FINISHED run to render again with a new seed, e.g. 4-8 or 4-8, 1:12-1:15 "
                       "(time on the whole video). Everything else stays exactly as it was; the previous version is "
                       "kept in the cache folder. Each run with this filled in tries a new seed; empty it (or bypass "
                       "the node) to keep the version you like.\n"
                       "已经跑完的片子里要重画的秒数（整条片子的时间），如 4-8 或 4-8, 1:12-1:15。其他部分一帧不变，"
                       "旧版本留在缓存文件夹里。填着就每跑一次换一个种子重画；满意了清空（或旁路这个节点）就保留当前版本。"}),
        }}

    RETURN_TYPES = ("H3MD_PLAN",)
    RETURN_NAMES = ("plan",)
    FUNCTION = "run"
    CATEGORY = "MiniMax H3/H3MD"
    DESCRIPTION = ("Low-res preview and local redraw for seamless long video (window mode). Between the Director and "
                   "the Loop. 无接缝长视频的低清预览和局部重绘，接在导演台和 Loop 之间。")

    def run(self, plan, preview=False, redraw=""):
        import copy
        ranges = parse_redraw(redraw, plan_seconds(plan))
        if not preview and not ranges:
            return (plan,)
        if not isinstance(plan, dict) or not plan.get("windowMode"):
            raise ValueError(f"{_TAG} low-res preview and local redraw work with seamless long video only: tick "
                             f"'无接缝长视频' in the Director (or bypass this node). 低清预览和局部重绘只能用在无接缝长视频："
                             f"在导演台勾上「无接缝长视频」（或旁路这个节点）。")
        if preview and ranges:
            raise ValueError(f"{_TAG} preview and redraw together: redraw works on a finished full run - switch preview "
                             f"off first. 低清预览和局部重绘不能同时开：局部重绘要在跑完的完整版上做，先关掉低清预览。")
        out = copy.deepcopy(plan)
        wm = dict(out["windowMode"]) if isinstance(out["windowMode"], dict) else \
            dict(normalize_window_mode(out["windowMode"]) or {})
        wm["preview"] = bool(preview)
        wm["redraw"] = ranges or None   # None = the normalized 'no redraw'
        out["windowMode"] = wm
        LOG.info("%s Redraw / Preview node: %s", _TAG,
                 f"local redraw of seconds {ranges}" if ranges else "low-res preview only")
        return (out,)


NODE_CLASS_MAPPINGS = {"H3MDContextWindows": H3MDContextWindows, "H3MDRedrawPreview": H3MDRedrawPreview}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDContextWindows": "H3MD Context Windows (window mode)",
                              "H3MDRedrawPreview": "H3MD Redraw / Preview 局部重绘 / 低清预览"}
