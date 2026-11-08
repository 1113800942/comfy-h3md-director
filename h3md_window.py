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
  "windowMode": true | {"length": 124, "overlap": 56, "highLength": 175, "highOverlap": 56, "freenoise": true,
                        "cropGuide": true}     (high* default to the low-res values)
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
DEFAULTS = {"videoPerWindow": "auto", "length": 124, "overlap": 56, "highLength": None, "highOverlap": None, "freenoise": True,
            "cropGuide": True, "preview": False,
            "refs": True}
MAX_TRAINED = 362          # H3 was trained on ~124-362 frame clips; longer windows are outside that range
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
    # 11-07: the high-res pass may need a smaller window than low-res (243 filled a 32 GB card at 1088x1664)
    out["highLength"] = _round_17(out["highLength"] or out["length"], 22)
    out["highOverlap"] = min(_round_17(out["highOverlap"] or out["overlap"], 5), out["highLength"] - 17)
    for k in ("length", "highLength"):
        if out[k] > MAX_TRAINED:
            LOG.warning("%s window %s %d frames is longer than H3's trained range (<= %d)", _TAG, k, out[k], MAX_TRAINED)
    out["freenoise"] = bool(out["freenoise"])
    out["cropGuide"] = bool(out["cropGuide"])
    out["preview"] = bool(out["preview"])
    out["refs"] = bool(out["refs"])
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
SECTION = re.compile(r"(?m)^\s*(subject_definitions|summary|retention_analysis|detailed_description|"
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


def window_spans(total_frames: int, length: int, overlap: int, cuts=()):
    """Pixel-frame ranges [a, b) of the windows and each window's own span: from the middle of its overlap with the
    previous window to the middle of its overlap with the next (at a hard cut: the cut).  The spans tile the shot, and
    each window's centre lies in its own span, so per-window prompts tagged with these spans are picked once."""
    n = video_latent_t(total_frames)
    px = latent_pixel_starts(n)
    wins = [(px[a], px[b + 1]) for a, b in core_windows(total_frames, length, overlap, cuts)]
    spans = []
    for i, (a, b) in enumerate(wins):
        lo = 0 if i == 0 else (a + wins[i - 1][1]) // 2
        hi = int(total_frames) if i == len(wins) - 1 else (wins[i + 1][0] + b) // 2
        spans.append((lo, hi))
    return wins, spans


def all_window_ranges(total_frames: int, window_mode: dict, cuts=()):
    """3d: pixel ranges of every window the low-res and the high-res pass will use (sorted, unique)."""
    out = set()
    for ln, ov in ((window_mode["length"], window_mode["overlap"]),
                   (window_mode["highLength"], window_mode["highOverlap"])):
        out.update(window_spans(total_frames, ln, ov, cuts)[0])
    return sorted(out)


def split_timed_prompt(prompt: str):
    """(before, common, events, after) when detailed_description has time marks like '[12s] ...' / '[8s cut] ...',
    else None.  before/after = the text before / after the detailed_description body; common = its text before the
    first mark; events = [(seconds, text, is_cut)] in time order."""
    if not prompt or not TIME_MARK.search(prompt):
        return None
    heads = list(SECTION.finditer(prompt))
    det = next((m for m in heads if m.group(1) == "detailed_description"), None)
    if det is None:
        return None
    nxt = next((m.start() for m in heads if m.start() > det.end()), len(prompt))
    body = prompt[det.end():nxt]
    marks = list(TIME_MARK.finditer(body))
    if not marks:
        return None
    common = body[:marks[0].start()].strip()
    events = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        txt = body[m.end():end].strip()
        if txt or m.group(2):
            events.append((float(m.group(1)), txt, bool(m.group(2))))
    events.sort(key=lambda e: e[0])
    return prompt[:det.end()], common, events, prompt[nxt:]


def prompt_cuts(prompt: str, fps: int = FPS):
    """Hard cuts written as '[8s cut] ...' -> pixel frames (a '[0s cut]' opens the first shot, not a cut)."""
    parts = split_timed_prompt(prompt)
    if parts is None:
        return []
    return sorted({int(round(sec * fps)) for sec, _, cut in parts[2] if cut and sec > 0})


def auto_window_prompts(prompt: str, total_frames: int, length: int, overlap: int, fps: int = FPS, cuts=None):
    """3a/3b: one timed prompt -> per-window prompts [{"start", "end", "prompt", "events"}] (pixel frames), each action
    given to exactly ONE window (the one whose own span holds its time).  A '[Ns cut] text' mark starts a new shot:
    its text describes every window after the cut (until the next cut), and windows never straddle it.  None when the
    prompt has no time marks or the shot fits one window."""
    parts = split_timed_prompt(prompt)
    if parts is None:
        return None
    before, common, events, after = parts
    if cuts is None:
        cuts = prompt_cuts(prompt, fps)
    wins, spans = window_spans(total_frames, length, overlap, cuts)
    if len(wins) < 2:
        return None
    shots = [(sec * fps, t) for (sec, t, cut) in events if cut]
    acts = [(sec * fps, t) for (sec, t, cut) in events if not cut]
    out = []
    for (a, b), (lo, hi) in zip(wins, spans):
        mid = (a + b) / 2.0
        shot = [t for (f, t) in shots if f <= mid]
        mine = [t for (f, t) in acts if lo <= f < hi]
        action = " Then ".join(x.rstrip(".") + "." for x in mine) if mine else NO_NEW_ACTION
        body = " ".join(x for x in (common, shot[-1] if shot else "", action) if x)
        out.append({"start": lo, "end": hi, "prompt": f"{before}\n{body}\n\n{after.lstrip()}" if after else
                    f"{before}\n{body}", "events": len(mine)})
    return out


# ------------------------------------------------------------------------------------------- 3c: refs per window
PIC_TAG = re.compile(r"<Picture (\d+)>")
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


def window_refs(prompt: str, n_images: int):
    """3c: the reference pictures one window prompt really uses, and the prompt rewritten for them.
    Used = pictures named in detailed_description / dialogue / sound sections, plus the pictures that define a subject
    named there (subject_definitions line '<Subject k> ... <Picture j> ...').  Returns (kept, prompt): kept = 0-based
    image indices in order (pictures renumbered 1..len(kept)); definition / retention lines and summary sentences that
    name only dropped subjects / pictures are removed.  (list(range(n)), prompt) when nothing would change."""
    secs = _sections(prompt)
    body = " ".join(t for n, t in secs if n not in (None, "subject_definitions", "summary", "retention_analysis"))
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
        elif n == "summary":
            head, _, rest = t.partition(":")
            sents = [x for x in _SENT.split(rest.strip()) if x and not unused(x)]
            t = head + ":\n" + " ".join(sents) + "\n\n"
        out.append(t)
    text = PIC_TAG.sub(lambda m: f"<Picture {renum.get(int(m.group(1)), int(m.group(1)))}>", "".join(out))
    return kept, text


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
    when the conds carry no spans.  3d: conds tagged with RANGE_KEY are matched to the window's exact pixel range."""
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

        def execute(self, calc_cond_batch, model, conds, x_in, timestep, model_options):
            cuts = set()
            for clist in conds or ():
                for c in clist or ():
                    cuts.update((c or {}).get(CUT_KEY) or ())
            self._h3md_cuts = sorted(cuts)
            return super().execute(calc_cond_batch, model, conds, x_in, timestep, model_options)

        def get_context_windows(self, model, x_in, model_options):
            cuts = getattr(self, "_h3md_cuts", None)
            if not cuts:
                return super().get_context_windows(model, x_in, model_options)
            n = x_in.size(self.dim)
            lats = sorted({5 * int(round(f / 17.0)) for f in cuts if 0 < 5 * int(round(f / 17.0)) < n})
            wins = latent_schedule(n, self.context_length, self.context_overlap, lats)
            key = (n, tuple(lats), self.context_length)
            if key not in self._h3md_seen:
                self._h3md_seen.add(key)
                LOG.info("%s hard cuts at latents %s: %d windows %s (no fusion across a cut)", _TAG, lats, len(wins),
                         wins)
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
    LOG.info("%s window mode: low-res windows %d / overlap %d, high-res windows %d / overlap %d frames, freenoise %s, "
             "crop guide %s", _TAG, wm["length"], wm["overlap"], wm["highLength"], wm["highOverlap"], wm["freenoise"],
             wm["cropGuide"])
    low = apply(low, wm["length"], wm["overlap"], "pyramid", wm["freenoise"], wm["cropGuide"])
    if high is not None:
        high = apply(high, wm["highLength"], wm["highOverlap"], "pyramid", wm["freenoise"], wm["cropGuide"])
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
