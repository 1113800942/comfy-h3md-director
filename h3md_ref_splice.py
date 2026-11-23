"""H3MD Ref Splice (0.6.2) - cut several moves out of ONE reference video, put them in any order (repeat / swap
allowed), join them with short transitions and write one spliced reference video plus a ready Director plan.

Why: a GitHub user asked for `<Video 1, time=a to b>` inside a prompt.  The Director keeps one continuous window per
reference video, so instead of changing the Director this node edits the video BEFORE it: the result is an ordinary
reference video + an ordinary plan (Import plan), and the Director runs it unchanged.

Rules measured on 2026-11-22 (checklist "参考视频剪辑插件", runs 1-6, one fight clip, low-res, same seed):
  * a transition placeholder of two still frames makes the model stand still: the placeholder is HOLD (4) frames of
    the last frame, a cross-dissolve, HOLD frames of the next clip's first frame;
  * an empty transition (no text) gives a natural link; ~1 s is enough;
  * one simple move written for a transition (kneel, crouch, fall and get up) is done when it has ~1.5 s; time left
    over becomes a pause, too little time makes the next move start early (0.3-0.5 s early is normal);
  * a long transition (> 2 s, e.g. a dialogue beat) is really another shot: it becomes its own text-only shot,
    continued with 39 frames on both sides, and the reference video holds a 39-frame dissolve before the next move so
    the carried frames land on the transition, never on the move.
Only this node uses these rules; nothing else in H3MD changes.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
import re

import numpy as np

try:
    from . import h3md_director as D
except ImportError:  # flat import (tests)
    import h3md_director as D

LOG = logging.getLogger("H3MD")
TAG = "[H3MD RefSplice]"
CATEGORY = "MiniMax H3/H3MD"
FPS = D.FPS
HOLD = 4                    # still frames kept at each end of a transition placeholder
SHORT_GAP_MAX = 2.0         # seconds; a longer transition becomes its own text-only shot
CARRY = 39                  # frames carried across the shots a long transition creates (an OVERLAP_STEPS value)
END_PAD = 17                # still frames after the last clip: a shot is rounded UP to 5+17n (<= 16 frames more)
AUDIO_RATE = 48000
PLANS_SUBDIR = "H3MD_plans"
MIN_CLIP = 5 / FPS


class SpliceError(ValueError):
    pass


def _version() -> str:
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


VERSION = _version()

# ---------------------------------------------------------------------------------------------------- script
DEFAULT_SCRIPT = """# H3MD Ref Splice - one line per item, times in seconds of the source video
subject: <Subject 1> is the fighter in the black sleeveless top and navy wide-leg trousers.
subject: <Subject 2> is the bearded heavyweight in black armour.
style: A realistic live-action tournament fight, real-time speed, sharp impacts, no slow motion.
scene: A side-on medium-wide shot of a wooden fight floor in a packed indoor arena, warm spotlights.
clip 4.0-6.0: they trade kicks
gap 1.5: <Subject 1> drops onto one knee with one hand on the floor, then stands back up
clip 0.0-2.0: a low kick, a front kick and a spinning back kick
gap 1
clip 8.0-9.75: a flying flip kick knocks <Subject 2> onto his back
sound: A loud arena crowd, thuds of kicks on armour, a heavy crash as he hits the floor.
music: N/A
"""

_KEYS = ("picture", "subject", "style", "scene", "summary", "sound", "music")
_TIME = r"(\d+(?::\d+(?:\.\d+)?)?(?:\.\d+)?)"
_CLIP_RE = re.compile(rf"^clip\s+{_TIME}\s*(?:-|–|~|to)\s*{_TIME}\s*s?\s*(?::\s*(.*))?$", re.I)
_GAP_RE = re.compile(r"^(?:gap|transition)\s+(\d+(?:\.\d+)?)\s*s?\s*(?::\s*(.*))?$", re.I)


def parse_time(s: str) -> float:
    """'12.5' / '1:05.5' -> seconds"""
    if ":" in s:
        m, sec = s.split(":", 1)
        return int(m) * 60 + float(sec)
    return float(s)


def parse_script(text: str) -> dict:
    out = {k: [] if k in ("picture", "subject") else "" for k in _KEYS}
    items = []
    for n, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _CLIP_RE.match(line)
        if m:
            a, b = parse_time(m.group(1)), parse_time(m.group(2))
            if b - a < MIN_CLIP:
                raise SpliceError(f"{TAG} line {n}: clip {a:g}-{b:g} is shorter than 5 frames")
            items.append({"kind": "clip", "a": a, "b": b, "text": (m.group(3) or "").strip(), "line": n})
            continue
        m = _GAP_RE.match(line)
        if m:
            sec = float(m.group(1))
            if sec <= 0:
                raise SpliceError(f"{TAG} line {n}: a gap needs a length in seconds above 0")
            items.append({"kind": "gap", "sec": sec, "text": (m.group(2) or "").strip(), "line": n})
            continue
        key, sep, val = line.partition(":")
        key = key.strip().lower()
        if sep and key in _KEYS:
            val = val.strip()
            if key in ("picture", "subject"):
                out[key].append(val)
            else:
                out[key] = (out[key] + " " + val).strip()
            continue
        raise SpliceError(f"{TAG} line {n} not understood: '{line}'. Use clip a-b: ..., gap s: ..., or one of "
                          f"{', '.join(k + ':' for k in _KEYS)}")
    if not any(i["kind"] == "clip" for i in items):
        raise SpliceError(f"{TAG} the script has no clip line (clip 4.0-6.0: ...)")
    if items[0]["kind"] != "clip" or items[-1]["kind"] != "clip":
        raise SpliceError(f"{TAG} the script must start and end with a clip (a gap joins two clips)")
    for x, y in zip(items, items[1:]):
        if x["kind"] == "gap" and y["kind"] == "gap":
            raise SpliceError(f"{TAG} line {y['line']}: two gaps in a row - write them as one gap")
    out["items"] = items
    return out


# -------------------------------------------------------------------------------------------------- timeline
def _clip_frames(it) -> list:
    """source times (s) of a clip at 24 fps"""
    n = max(1, int(round((it["b"] - it["a"]) * FPS)))
    return [it["a"] + k / FPS for k in range(n)]


def _placeholder(n: int, a_time: float, b_time: float) -> list:
    """n frames: HOLD of a, a cross-dissolve a->b, HOLD of b (all dissolve when n is short)"""
    hold = HOLD if n >= 2 * HOLD + 2 else 0
    mid = n - 2 * hold
    ops = [("src", a_time)] * hold
    ops += [("mix", a_time, b_time, (k + 1) / (mid + 1)) for k in range(mid)]
    ops += [("src", b_time)] * hold
    return ops


def build_timeline(script: dict, src_dur=None) -> dict:
    """-> {"ops": frame ops of the spliced video, "audio": audio ops, "shots": [...]}.
    ops: ("src", t) = source frame at t s; ("mix", ta, tb, w) = (1-w)*frame(ta) + w*frame(tb).
    audio ops: ("src", a, b) source seconds / ("sil", seconds)."""
    items = script["items"]
    for it in items:
        if it["kind"] == "clip" and src_dur is not None and it["b"] > src_dur + 0.05:
            raise SpliceError(f"{TAG} line {it['line']}: clip ends at {it['b']:g} s but the video is only "
                              f"{src_dur:.2f} s long")
    ops, audio, shots = [], [], []

    def new_video_shot(ov):
        return {"kind": "video", "overlap": ov, "ref_start": len(ops), "frames": 0, "events": []}

    shot = new_video_shot(0)
    for k, it in enumerate(items):
        if it["kind"] == "clip":
            fr = _clip_frames(it)
            t0 = shot["frames"]
            ops.extend(("src", t) for t in fr)
            audio.append(("src", it["a"], it["a"] + len(fr) / FPS))
            shot["frames"] += len(fr)
            shot["events"].append({"kind": "clip", "f0": t0, "f1": shot["frames"], "text": it["text"],
                                   "src": (it["a"], it["b"])})
            continue
        prev, nxt = items[k - 1], items[k + 1]
        a_time = _clip_frames(prev)[-1]
        b_time = nxt["a"]
        if it["sec"] <= SHORT_GAP_MAX + 1e-9:
            n = max(1, int(round(it["sec"] * FPS)))
            t0 = shot["frames"]
            ops.extend(_placeholder(n, a_time, b_time))
            audio.append(("sil", n / FPS))
            shot["frames"] += n
            shot["events"].append({"kind": "gap", "f0": t0, "f1": shot["frames"], "text": it["text"]})
            continue
        # long transition: close this shot, add a text-only shot, the next clip starts a new shot carried by CARRY
        shots.append(shot)
        n = max(1, int(round(it["sec"] * FPS)))
        shots.append({"kind": "text", "overlap": CARRY, "frames": n, "text": it["text"],
                      "events": [{"kind": "gap", "f0": 0, "f1": n, "text": it["text"]}]})
        ops.extend(_placeholder(CARRY, a_time, b_time))   # the next shot's carried frames read this dissolve
        audio.append(("sil", CARRY / FPS))
        shot = new_video_shot(CARRY)
    shots.append(shot)
    last = ops[-1]
    ops.extend([("src", last[1] if last[0] == "src" else last[2])] * END_PAD)
    audio.append(("sil", END_PAD / FPS))
    for s in shots:
        s["length"] = D.aligned_up(s["overlap"] + s["frames"])
    return {"ops": ops, "audio": audio, "shots": shots}


# --------------------------------------------------------------------------------------------------- prompts
_SUBJ_RE = re.compile(r"<Subject\s*(\d+)>")


def _subject_tags(script) -> list:
    seen = []
    for line in script["subject"]:
        for m in _SUBJ_RE.finditer(line):
            t = f"<Subject {m.group(1)}>"
            if t not in seen:
                seen.append(t)
    return seen


def _a(sec: float) -> str:
    """article before a spoken number: an 8.7 / an 11 / an 18, a 9.4"""
    n = f"{sec:.1f}"
    return "an" if n.startswith("8") or n.split(".")[0] in ("11", "18") else "a"


def _s(frames) -> str:
    return f"{frames / FPS:.2f}"


def _opening(shot, prev_text: str) -> str:
    if not shot["overlap"]:
        return ""
    what = prev_text.rstrip(". ") if prev_text else "the end of the previous moment"
    return (f"For the first {_s(shot['overlap'])} seconds it holds the end of the previous moment ({what}), "
            f"then the new action starts.")


def shot_prompt(script: dict, shot: dict, n_pics: int, has_audio: bool, prev_text: str = "") -> str:
    ov = shot["overlap"]
    total = shot["length"] / FPS
    style = script["style"] or "A realistic live-action scene, real-time speed, no slow motion."
    scene = script["scene"]
    subj_tags = _subject_tags(script)
    events = []
    for e in shot["events"]:
        t0, t1 = _s(ov + e["f0"]), _s(ov + e["f1"])
        if e["kind"] == "clip" and shot["kind"] == "video":
            txt = e["text"].rstrip(". ") if e["text"] else "the people move exactly as in <Video 1>"
            events.append(f"From {t0} to {t1} seconds, as in <Video 1>: {txt}.")
        elif e["text"]:
            events.append(f"From {t0} to {t1} seconds, {e['text'].rstrip('. ')}.")
    body = " ".join(x for x in [scene, _opening(shot, prev_text)] + events if x)
    sound = script["sound"] or "The sound of the scene, matching the action on screen."
    music = script["music"] or "N/A"

    if shot["kind"] == "text" and n_pics == 0:   # no reference at all: base (T2VA) format
        plain = lambda s: _SUBJ_RE.sub(lambda m: f"person {m.group(1)}", s)
        people = " ".join(plain(x) for x in script["subject"])
        return (f"integrated_multimodal_description:\n{style} {plain(people)} {plain(body)}\n\n"
                f"overall_soundscape:\n{plain(sound)}\n\nnon_diegetic_music:\n{music}")

    defs = list(script["subject"])
    kinds = ["reference generation"]
    if shot["kind"] == "video":
        defs.append("<Video 1> is the motion and camera reference: its moves, timing and camera framing.")
        if has_audio:
            defs.append("<Audio 1> is the sound of <Video 1>, used only as a loose reference for the sound.")
            kinds.append("audio reference")
    summary = script["summary"] or (
        f"The target video presents {_a(total)} {total:.1f}-second continuous shot"
        + (" following the moves of <Video 1>." if shot["kind"] == "video" else "."))
    ret = [f"{t} (appears in [Shot 1]): fully_preserved - the look and outfit stay the same from the first frame "
           f"to the last." for t in subj_tags]
    if shot["kind"] == "video":
        wins = [f"{_s(ov + e['f0'])} to {_s(ov + e['f1'])}" for e in shot["events"] if e["kind"] == "clip"]
        gaps = any(e["kind"] == "gap" for e in shot["events"])
        ret.append("<Video 1> (appears in [Shot 1]): partially_preserved - its moves and camera framing are followed "
                   f"from {', '.join(wins)} seconds"
                   + ("; between those moments it only shows a slow dissolve from one pose to the next." if gaps
                      else "."))
        if has_audio:
            ret.append("<Audio 1> (appears in [Shot 1]): reference - the impacts and atmosphere only.")
    return (f"subject_definitions:\n" + "\n".join(defs) + "\n\n"
            f"summary:\n[{' + '.join(kinds)}] {summary}\n\n"
            f"retention_analysis:\n" + "\n".join(ret) + "\n\n"
            f"detailed_description:\n{style}\n[Shot 1] {body}\n\n"
            f"overall_soundscape:\n{sound}\n\nnon_diegetic_music:\n{music}")


# ------------------------------------------------------------------------------------------------------ plan
def canvas_for(w: int, h: int):
    return (1664, 960) if w >= h else (960, 1664)


def build_plan(script: dict, tl: dict, name: str, video_file: str, has_audio: bool, pictures: list,
               canvas=(1664, 960)) -> dict:
    pic_ids = [f"pic{k + 1}" for k in range(len(pictures))]
    total = len(tl["ops"]) / FPS
    plan = {"version": 1, "name": name, "fps": FPS, "width": canvas[0], "height": canvas[1],
            "globalPrompt": "", "globalMode": "prepend",
            "media": {"images": [{"id": i, "file": f} for i, f in zip(pic_ids, pictures)],
                      "videos": [{"id": "ref", "file": video_file, "duration": round(total, 6),
                                  "hasAudio": bool(has_audio)}],
                      "audios": [], "refmods": []},
            "segments": []}
    prev_text = ""
    for s in tl["shots"]:
        seg = {"length": s["length"], "overlapIn": s["overlap"], "images": list(pic_ids), "audios": [],
               "prompt": shot_prompt(script, s, len(pictures), has_audio, prev_text)}
        if s["kind"] == "video":
            seg["videos"] = [{"id": "ref", "trimStart": round(s["ref_start"] / FPS, 6),
                              "useDuration": round(s["frames"] / FPS, 6), "useSoundtrack": bool(has_audio)}]
        else:
            seg["videos"] = []
        plan["segments"].append(seg)
        prev_text = s["events"][-1]["text"] if s["events"] else ""
    return plan


def check_plan(plan: dict) -> list:
    """the Director's own parser - raises on anything it would refuse; returns its notes"""
    p = D.parse_plan(json.dumps(plan))
    return list(p.get("notes") or [])


def report(script, tl, plan, notes) -> str:
    lines = [f"H3MD Ref Splice {VERSION}: spliced video {len(tl['ops'])} frames = {len(tl['ops']) / FPS:.2f} s, "
             f"{len(tl['shots'])} shot(s)"]
    for k, s in enumerate(tl["shots"], 1):
        kind = "video" if s["kind"] == "video" else "text only"
        lines.append(f"  Shot {k} ({kind}): {s['length']} f = {s['length'] / FPS:.2f} s, overlapIn {s['overlap']}")
        for e in s["events"]:
            what = ("clip " + "-".join(f"{x:g}" for x in e["src"])) if e["kind"] == "clip" else "transition"
            lines.append(f"    {_s(s['overlap'] + e['f0'])}-{_s(s['overlap'] + e['f1'])} s  {what}"
                         + (f": {e['text']}" if e["text"] else ""))
        if s["kind"] == "video" and s["frames"] / FPS > D.MAX_REF_SECONDS:
            lines.append(f"    note: {s['frames'] / FPS:.1f} s of reference - the Director splits it by VRAM")
    for e in script["items"]:
        if e["kind"] == "gap" and e["sec"] <= SHORT_GAP_MAX and e["text"] and e["sec"] < 1.2:
            lines.append(f"  hint: line {e['line']}: {e['sec']:g} s is short for a written move (about 1.5 s per move)")
    lines += [f"  director: {n}" for n in notes]
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------- media I/O
def probe(path: str) -> dict:
    import av
    with av.open(path) as c:
        vs = c.streams.video[0]
        fps = float(vs.average_rate or vs.guessed_rate or 24)
        dur = float(vs.duration * vs.time_base) if vs.duration else (float(c.duration) / 1e6 if c.duration else 0.0)
        return {"fps": fps, "duration": dur, "width": vs.codec_context.width, "height": vs.codec_context.height,
                "has_audio": len(c.streams.audio) > 0}


def read_frames(path: str, times) -> dict:
    """{time: HxWx3 uint8} for the source frames nearest to the wanted times (one decode pass)"""
    import av
    want = sorted(set(times))
    got = {}
    with av.open(path) as c:
        vs = c.streams.video[0]
        tb = float(vs.time_base)
        start = float(vs.start_time * vs.time_base) if vs.start_time is not None else 0.0
        frames = []
        for fr in c.decode(vs):
            t = (fr.pts * tb - start) if fr.pts is not None else len(frames) / float(vs.average_rate or 24)
            frames.append((t, fr))
            # keep only frames that some wanted time may still pick (small memory: drop the far past)
            while len(frames) > 2 and want and frames[1][0] <= want[0] - 1.0:
                frames.pop(0)
            while want and frames[-1][0] >= want[0] and len(frames) >= 1:
                w = want.pop(0)
                best = min(frames, key=lambda x: abs(x[0] - w))
                got[w] = best[1].to_ndarray(format="rgb24")
        for w in want:   # past the last frame: the last one
            got[w] = frames[-1][1].to_ndarray(format="rgb24")
    return got


def read_audio(path: str):
    """(2, n) float32 at 48 kHz, or None"""
    import av
    with av.open(path) as c:
        if not c.streams.audio:
            return None
        rs = av.AudioResampler(format="fltp", layout="stereo", rate=AUDIO_RATE)
        parts = []
        for fr in c.decode(c.streams.audio[0]):
            for o in rs.resample(fr):
                parts.append(o.to_ndarray())
        for o in rs.resample(None):
            parts.append(o.to_ndarray())
    return np.concatenate(parts, axis=1).astype(np.float32) if parts else None


def splice_audio(src, ops):
    out = []
    for op in ops:
        if op[0] == "src":
            a, b = int(round(op[1] * AUDIO_RATE)), int(round(op[2] * AUDIO_RATE))
            seg = src[:, a:b] if src is not None else np.zeros((2, 0), np.float32)
            if seg.shape[1] < b - a:
                seg = np.concatenate([seg, np.zeros((2, b - a - seg.shape[1]), np.float32)], axis=1)
            out.append(seg)
        else:
            out.append(np.zeros((2, int(round(op[1] * AUDIO_RATE))), np.float32))
    return np.concatenate(out, axis=1)


def write_video(path: str, frames_by_time: dict, ops, audio=None):
    import av
    first = frames_by_time[ops[0][1]]
    h, w = first.shape[:2]
    h2, w2 = h - h % 2, w - w % 2
    with av.open(path, "w") as c:
        vs = c.add_stream("libx264", rate=FPS)
        vs.width, vs.height, vs.pix_fmt = w2, h2, "yuv420p"
        vs.options = {"crf": "16", "preset": "veryfast"}
        ast = None
        if audio is not None:
            ast = c.add_stream("aac", rate=AUDIO_RATE)
            ast.layout = "stereo"
        for op in ops:
            if op[0] == "src":
                img = frames_by_time[op[1]]
            else:
                a, b = frames_by_time[op[1]].astype(np.float32), frames_by_time[op[2]].astype(np.float32)
                img = np.clip(a * (1.0 - op[3]) + b * op[3] + 0.5, 0, 255).astype(np.uint8)
            vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(img[:h2, :w2]), format="rgb24")
            for p in vs.encode(vf):
                c.mux(p)
        for p in vs.encode():
            c.mux(p)
        if ast is not None:
            n = audio.shape[1]
            step = 1024
            for i in range(0, n, step):
                chunk = np.ascontiguousarray(audio[:, i:i + step])
                af = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                af.sample_rate = AUDIO_RATE
                af.pts = i
                for p in ast.encode(af):
                    c.mux(p)
            for p in ast.encode():
                c.mux(p)



def safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return n or "ref_splice"


def out_target(prefix: str, output_dir: str):
    """like ComfyUI's save nodes: a prefix inside the output folder ('H3MD_plans/ref_splice'), or an absolute path.
    -> (folder, name)"""
    p = (prefix or "").strip().strip('"').replace("\\", "/") or "H3MD_plans/ref_splice"
    if os.path.isabs(p) or re.match(r"^[A-Za-z]:/", p):
        folder, name = os.path.dirname(p), os.path.basename(p)
    else:
        sub, name = os.path.dirname(p), os.path.basename(p)
        folder = os.path.abspath(os.path.join(output_dir, sub))
        if not folder.startswith(os.path.abspath(output_dir)):
            raise SpliceError(f"{TAG} filename_prefix must stay inside the output folder (or be an absolute path)")
    return folder, safe_name(name)


def stamp(folder: str, name: str, today=None) -> str:
    """'<name>_<YYYYMMDD>_<NNN>' - NNN = next free number of the day, so nothing is ever overwritten"""
    day = (today or datetime.date.today()).strftime("%Y%m%d")
    pre = f"{name}_{day}_"
    used = [0]
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            m = re.match(re.escape(pre) + r"(\d{3})(?!\d)", f)
            if m:
                used.append(int(m.group(1)))
    return f"{pre}{max(used) + 1:03d}"


# ---------------------------------------------------------------------------------------- people (cast)
def subject_lines(cast: dict, links: list) -> list:
    """who is who in the prompt: linked people -> '<Subject k> is the person in <Picture j>' + where in the video;
    the others keep their look from the video.  links: [(picture, person number)] (several numbers per picture =
    one person the tracker split)."""
    people = cast.get("people") or []
    by_num = {k: a for k, a in enumerate(people, 1)}
    pics = sorted({p for p, _ in links})
    tag_of = {p: j for j, p in enumerate(pics, 1)}       # <Picture j> = j-th picture in the plan
    lines, used, s = [], set(), 0
    for p in pics:
        nums = sorted(k for q, k in links if q == p)
        used.update(nums)
        a = by_num.get(nums[0], {})
        s += 1
        where = f"the person first seen on the {a.get('side', 'screen')} at {a.get('first', 0):.1f} s" if a else \
            "a person"
        lines.append(f"<Subject {s}> is the person in <Picture {tag_of[p]}>: face, hair, body and outfit exactly as in "
                     f"<Picture {tag_of[p]}>. In <Video 1> <Subject {s}> takes the place of {where}.")
    for k, a in by_num.items():
        if k in used:
            continue
        s += 1
        lines.append(f"<Subject {s}> is {('the person first seen on the ' + a.get('side', 'screen'))} at "
                     f"{a.get('first', 0):.1f} s in <Video 1>, with the same look as in <Video 1>.")
    return lines


def people_cast(path: str, info: dict):
    """the keyframe Prepare tracking (SAM3 people + cuts + faces + re-identification), shared cache with it.
    -> (frames, people, small)"""
    try:
        from . import h3md_keyframe_nodes as KN, h3md_keyframe as K
    except ImportError:
        import h3md_keyframe_nodes as KN
        import h3md_keyframe as K
    size = K.target_size(info["width"], info["height"])
    dur = K.plan_length(int(info["duration"] * K.FPS + 1e-6)) / K.FPS
    tkey = (os.path.abspath(path), os.path.getsize(path), tuple(size), 0.4, 8, round(dur, 3))
    hit = KN._TRACK_CACHE.get(tkey)
    K.FRAME_ASPECT = size[0] / float(size[1])
    if hit is not None:
        times, small, per_frame, reid_joins, reid_unsure, face_boxes, cuts = hit
        LOG.info("%s people: tracking reused", TAG)
    else:
        times, small = KN.sample_frames(path, dur, size)
        per_frame = K.link_windows(KN.track_people(small, threshold=0.4, max_people=8))[:len(times)]
        try:
            cuts = KN.detect_cuts(path)
        except Exception:
            cuts = []
        faces, face_boxes = {}, {}
        try:
            obs = KN.face_observations(path, times, per_frame, size, boxes_out=face_boxes)
            faces = {(i, pid): (emb, w) for i, pid, emb, w in obs}
        except Exception as exc:
            LOG.info("%s faces not read (%s)", TAG, exc)
        try:
            per_frame, reid_joins, reid_unsure, _ = K.identify(per_frame, times, cuts, faces,
                                                               K.look_samples(small, per_frame))
        except Exception as exc:
            reid_joins, reid_unsure = [], []
            LOG.info("%s identification skipped (%s)", TAG, exc)
        KN._TRACK_CACHE.clear()
        KN._TRACK_CACHE[tkey] = (times, small, per_frame, reid_joins, reid_unsure, face_boxes, cuts)
    frames = K.report(per_frame, times)
    frames, _ = K.merge_twins(frames)
    frames, _ = K.clean_frames(frames, face_boxes)
    return frames, K.cast(frames), small


def cast_ui(frames, people, small, links, pics: dict) -> dict:
    """the drag-a-line panel of the keyframe Prepare node (same message, same widget 'mapping')"""
    try:
        from . import h3md_keyframe_nodes as KN
    except ImportError:
        import h3md_keyframe_nodes as KN
    picset = sorted(pics)
    ui = {"pictures": picset, "people": [
        {"person": n, "first": round(a["first"], 1), "last": round(a["last"], 1),
         "picture": next((p for p, k in links if k == n), None)} for n, a in enumerate(people, 1)]}
    try:
        _, crops = KN.cast_sheet(small, frames, people, [(p, k, people[k - 1]["id"]) for p, k in links
                                                          if 0 < k <= len(people)])
        ui["tiles"] = [KN.thumb_b64(c) for c in crops]
        ui["thumbs"] = {str(p): KN.thumb_b64(KN.tensor_pil(pics[p])) for p in picset}
    except Exception as exc:
        LOG.warning("%s cast panel pictures failed: %s", TAG, exc)
    return ui


KEEP_PEOPLE = ("<Subject 1> is the people seen in <Video 1>: the same faces, hair, bodies and outfits as in "
               "<Video 1>, nobody is replaced.",)


# ------------------------------------------------------------------------------------------------ draft
def draft_script(path: str, duration: float, min_len=1.5, max_clips=8, cuts=None) -> str:
    """first run with an empty edit: a template to change on the tracks.  Split points, best first: the video's own
    camera cuts, then the longest quiet stretches (0.54 s average of the frame difference - a pause between moves;
    a brief quiet frame, e.g. the top of a jump, does not win), never in the first second after a camera cut (a
    jump filmed from a new angle).  Original order, a 1 s transition between clips."""
    try:
        from . import h3md_autosplit as AS
    except ImportError:
        import h3md_autosplit as AS
    if cuts is None:
        try:
            try:
                from . import h3md_keyframe_nodes as KN
            except ImportError:
                import h3md_keyframe_nodes as KN
            cuts = KN.detect_cuts(path)
        except Exception:
            cuts = []
    end = math.floor(duration * FPS) / FPS
    splits = []
    for c in sorted(float(x) for x in cuts or ()):
        if min_len <= c <= end - min_len and all(abs(c - s) >= min_len for s in splits):
            splits.append(snap_t(c))
    prof = AS.motion_profile(path, 0.0, duration) or []
    n = len(prof)
    if n > 20 and len(splits) < max_clips - 1:
        m = np.convolve(np.asarray([v or 0.0 for v in prof], np.float32), np.ones(13) / 13.0, mode="same")
        med = float(np.median(m))
        for i in sorted(range(7, n - 7), key=lambda i: m[i]):
            if m[i] > med or len(splits) >= max_clips - 1:
                break
            tt = i / FPS
            if tt < min_len or end - tt < min_len or any(abs(tt - s) < min_len for s in splits):
                continue
            if any(0 <= tt - c < 1.0 for c in cuts or ()):
                continue
            splits.append(snap_t(tt))
    edges = [0.0] + sorted(splits) + [end]
    lines = ["# draft: cut at camera cuts / pauses; fix it on the tracks (drag the edge between two moves, split, merge)"]
    for k, (a, b) in enumerate(zip(edges, edges[1:])):
        if k:
            lines.append("gap 1")
        lines.append(f"clip {a:.2f}-{b:.2f}")
    return "\n".join(lines) + "\n"


def snap_t(t: float) -> float:
    return round(round(float(t) * FPS) / FPS, 4)


def edit_to_text(script: dict) -> str:
    out = [f"{k}: {v}" for k in ("picture", "subject") for v in script[k]]
    out += [f"{k}: {script[k]}" for k in ("style", "scene", "summary") if script[k]]
    for it in script["items"]:
        if it["kind"] == "clip":
            out.append(f"clip {it['a']:g}-{it['b']:g}" + (f": {it['text']}" if it["text"] else ""))
        else:
            out.append(f"gap {it['sec']:g}" + (f": {it['text']}" if it["text"] else ""))
    out += [f"{k}: {script[k]}" for k in ("sound", "music") if script[k]]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------------------------------------ export
def save_picture(t, path: str):
    try:
        from . import h3md_keyframe_nodes as KN
    except ImportError:
        import h3md_keyframe_nodes as KN
    KN.tensor_pil(t).save(path, "PNG")


def export(src_path: str, edit_text: str, folder: str, name: str, pictures: dict = None, subjects=None) -> dict:
    """splice + plan zip (manifest + plan + media) + a preview copy of the spliced video next to it.
    pictures: {picture number: IMAGE tensor or file path} (only the linked ones); subjects: prompt lines or None."""
    import zipfile
    script = parse_script(edit_text)
    if not script["subject"]:
        script["subject"] = list(subjects) if subjects else list(KEEP_PEOPLE)
    info = probe(src_path)
    tl = build_timeline(script, info["duration"])
    os.makedirs(folder, exist_ok=True)
    stem = stamp(folder, name)
    vid_name = f"{stem}.mp4"
    pic_files = [f"{stem}_picture{p}.png" for p in sorted(pictures or {})]
    plan = build_plan(script, tl, stem, vid_name, info["has_audio"], pic_files,
                      canvas_for(info["width"], info["height"]))
    notes = check_plan(plan)
    times = [op[1] for op in tl["ops"] if op[0] == "src"] + [t for op in tl["ops"] if op[0] == "mix"
                                                             for t in (op[1], op[2])]
    frames = read_frames(src_path, times)
    audio = splice_audio(read_audio(src_path), tl["audio"]) if info["has_audio"] else None
    preview = os.path.join(folder, f"{stem}_preview.mp4")
    write_video(preview, frames, tl["ops"], audio)
    zip_path = os.path.join(folder, f"{stem}.zip")
    manifest = {"format": "h3md-plan", "plan_version": 1, "name": stem, "width": plan["width"],
                "height": plan["height"], "fps": FPS, "with_media": True,
                "media": [{"kind": "videos", "id": "ref", "file": vid_name, "included": True,
                           "zip": f"media/{vid_name}"}]
                + [{"kind": "images", "id": i["id"], "file": i["file"], "included": True, "zip": f"media/{i['file']}"}
                   for i in plan["media"]["images"]]}
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("plan.json", json.dumps(plan, ensure_ascii=False, indent=2))
        z.write(preview, f"media/{vid_name}", compress_type=zipfile.ZIP_STORED)
        for p, f in zip(sorted(pictures or {}), pic_files):
            src = pictures[p]
            if isinstance(src, str):
                z.write(src, f"media/{f}")
            else:
                tmp = os.path.join(folder, f"_{f}")
                save_picture(src, tmp)
                z.write(tmp, f"media/{f}")
                os.remove(tmp)
    rep = report(script, tl, plan, notes) + (f"\n  package: {zip_path}\n  drag it into the H3MD Director (or Import "
                                             f"plan) and run the low-res pass first")
    LOG.info("%s %s", TAG, rep)
    return {"zip": zip_path, "preview": preview, "report": rep, "plan": plan, "stem": stem}


# ------------------------------------------------------------------------------------------------------ node
_EDIT_TIP = ("The edit, kept in step with the timeline above (you rarely type here). One line per item: "
             "'clip 4.0-6.0: what happens', 'gap 1.5: one move' (gap with no words = natural link; over 2 s = its own "
             "text-only shot). Empty + Run = a draft cut where the motion pauses. Optional lines: style: / scene: / "
             "sound: / music: / subject:. 剪辑内容，和上面的时间轴同步；空着运行 = 自动出草稿。")


class H3MDRefSplice:
    @classmethod
    def INPUT_TYPES(cls):
        pics = {f"picture_{i}": ("IMAGE", {"tooltip": f"Character picture {i}: drag a line from it to the person it "
                                                      f"replaces on the cast panel (default: picture 1 = first "
                                                      f"person to appear). 角色图{i}。"}) for i in range(1, 6)}
        return {"required": {
            "video": ("VIDEO", {"tooltip": "The reference video: connect a Load Video node and drag the file in. "
                                           "参考视频：接「加载视频」，把文件拖进去。"}),
            "edit": ("STRING", {"multiline": True, "default": "", "tooltip": _EDIT_TIP}),
            "mapping": ("STRING", {"multiline": True, "default": "", "tooltip":
                        "Who becomes whom, written by the cast panel (picture 1 -> person 3). Empty = automatic: "
                        "picture 1 = first person to appear. 由人物面板写入；空 = 自动。"}),
            "filename_prefix": ("STRING", {"default": "H3MD_plans/ref_splice", "tooltip":
                                "Where the plan package goes, like ComfyUI's save nodes: inside the output folder "
                                "(default output/H3MD_plans/ref_splice_<date>_<NNN>.zip), or an absolute path. "
                                "计划包存放位置：默认在 output 里。"}),
        }, "optional": pics}

    RETURN_TYPES = ("STRING", "STRING", "VIDEO")
    RETURN_NAMES = ("package", "report", "video")
    OUTPUT_TOOLTIPS = ("Path of the plan package (.zip) for the H3MD Director.", "What was made.",
                       "The spliced video itself (transitions = dissolves): connect ComfyUI's Save Video to keep the "
                       "edit as a finished file without H3. 拼好的视频：接 Save Video 直接存成成品。")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("Cut moves out of one reference video, put them in any order with short transitions, choose who "
                   "your character pictures replace, preview, and get a plan package for the H3MD Director. "
                   "从一条参考视频里剪出动作、自由排序加过渡、指定角色图换谁，预览后得到导演台计划包。")

    def run(self, video, edit, mapping, filename_prefix, **pics):
        import folder_paths
        src = video.get_stream_source() if hasattr(video, "get_stream_source") else None
        if not (isinstance(src, str) and os.path.isfile(src)):
            src = os.path.join(folder_paths.get_temp_directory(), "h3md_ref_splice_source.mp4")
            video.save_to(src)
        info = probe(src)
        pics = {int(k.split("_")[1]): v for k, v in pics.items() if k.startswith("picture_") and v is not None}
        ui = {}
        subjects, links_used = None, []
        if pics:
            try:
                from . import h3md_keyframe as K
            except ImportError:
                import h3md_keyframe as K
            frames, people, small = people_cast(src, info)
            mp = K.parse_mapping(mapping)
            if not mp:
                mp = K.auto_cast(people, sorted(pics))
            mp = {p: (v if isinstance(v, (list, tuple)) else [v]) for p, v in mp.items() if p in pics}
            links_used = [(p, k) for p, ks in mp.items() for k in ks if 0 < k <= len(people)]
            subjects = subject_lines({"people": people}, links_used)
            ui["h3md_cast"] = [json.dumps(cast_ui(frames, people, small, links_used, pics))]
        splice = {"duration": info["duration"]}
        if not (edit or "").strip() or not parse_script_items_present(edit):
            # first run: a draft (cut where the motion pauses), exported at once like every later run
            edit = draft_script(src, info["duration"])
            splice["draft"] = edit
        folder, name = out_target(filename_prefix, folder_paths.get_output_directory())
        used_pics = {p: pics[p] for p in sorted({p for p, _ in links_used})}
        res = export(src, edit, folder, name, used_pics, subjects)
        if splice.get("draft"):
            res["report"] = ("Draft (the video cut where the motion pauses) - change it on the tracks and Run again.\n"
                             + res["report"])
        out_dir = os.path.abspath(folder_paths.get_output_directory())
        if os.path.abspath(res["preview"]).startswith(out_dir):
            rel = os.path.relpath(os.path.dirname(res["preview"]), out_dir).replace("\\", "/")
            splice["preview"] = {"filename": os.path.basename(res["preview"]), "subfolder": "" if rel == "." else rel,
                                 "type": "output"}
        splice["package"] = res["zip"]
        ui["h3md_splice"] = [json.dumps(splice)]
        ui["text"] = [res["report"]]
        from comfy_api.latest import InputImpl
        return {"ui": ui, "result": (res["zip"], res["report"], InputImpl.VideoFromFile(res["preview"]))}


def parse_script_items_present(text: str) -> bool:
    return any(_CLIP_RE.match(x.strip()) for x in (text or "").splitlines())


NODE_CLASS_MAPPINGS = {"H3MDRefSplice": H3MDRefSplice}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDRefSplice": f"H3MD Ref Splice {VERSION}"}
