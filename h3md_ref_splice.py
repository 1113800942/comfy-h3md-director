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


def out_folder(input_dir: str, name: str, today=None) -> str:
    day = (today or datetime.date.today()).strftime("%Y%m%d")
    base = os.path.join(input_dir, PLANS_SUBDIR)
    for k in range(1, 1000):
        d = os.path.join(base, f"{name}_{day}_{k:03d}")
        if not os.path.exists(d):
            return d
    raise SpliceError(f"{TAG} too many runs named {name} today")


def safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return n or "ref_splice"


def run_splice(src_path: str, script_text: str, name: str, input_dir: str) -> dict:
    """the whole job; returns {"plan_path", "video_path", "report", "plan"}"""
    script = parse_script(script_text)
    info = probe(src_path)
    pictures = []
    for p in script["picture"]:
        f = p.strip().strip('"').replace("\\", "/")
        if not os.path.isfile(f if os.path.isabs(f) else os.path.join(input_dir, f)):
            raise SpliceError(f"{TAG} picture not found in ComfyUI's input folder: {f}")
        pictures.append(f)
    tl = build_timeline(script, info["duration"])
    folder = out_folder(input_dir, safe_name(name))
    stem = os.path.basename(folder)
    rel_video = f"{PLANS_SUBDIR}/{stem}/{stem}.mp4"
    plan = build_plan(script, tl, stem, rel_video, info["has_audio"], pictures,
                      canvas_for(info["width"], info["height"]))
    notes = check_plan(plan)       # before writing anything: refuse a plan the Director would refuse
    times = [op[1] for op in tl["ops"] if op[0] == "src"] + [t for op in tl["ops"] if op[0] == "mix"
                                                             for t in (op[1], op[2])]
    frames = read_frames(src_path, times)
    audio = splice_audio(read_audio(src_path), tl["audio"]) if info["has_audio"] else None
    os.makedirs(folder, exist_ok=False)
    video_path = os.path.join(folder, f"{stem}.mp4")
    write_video(video_path, frames, tl["ops"], audio)
    plan_path = os.path.join(folder, f"{stem}.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    rep = report(script, tl, plan, notes) + f"\n  plan: {plan_path}\n  import it in the Director (Import plan)"
    LOG.info("%s %s", TAG, rep)
    return {"plan_path": plan_path, "video_path": video_path, "report": rep, "plan": plan}


# ------------------------------------------------------------------------------------------------------ node
class H3MDRefSplice:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "video": ("VIDEO", {"tooltip": "the source reference video (Load Video node: drag the file in)"}),
            "script": ("STRING", {"multiline": True, "default": DEFAULT_SCRIPT, "tooltip":
                       "one line per item: clip a-b: what happens / gap seconds: optional one move / subject: / "
                       "picture: / style: / scene: / sound: / music:. Ask your AI to write it (H3MD skill, Ref "
                       "Splice part)."}),
            "name": ("STRING", {"default": "ref_splice", "tooltip": "English name of the plan folder"}),
        }}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("plan_path", "report")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("Cut moves out of one reference video in any order, join them with short transitions and write "
                   "the spliced video + a Director plan (Import plan). The Director itself is unchanged.")

    def run(self, video, script, name):
        import folder_paths
        src = video.get_stream_source() if hasattr(video, "get_stream_source") else None
        if not (isinstance(src, str) and os.path.isfile(src)):
            tmp = os.path.join(folder_paths.get_temp_directory(), "h3md_ref_splice_source.mp4")
            video.save_to(tmp)
            src = tmp
        res = run_splice(src, script, name, folder_paths.get_input_directory())
        return {"ui": {"text": [res["report"]]}, "result": (res["plan_path"], res["report"])}


NODE_CLASS_MAPPINGS = {"H3MDRefSplice": H3MDRefSplice}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDRefSplice": f"H3MD Ref Splice {VERSION}"}
