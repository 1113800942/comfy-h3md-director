"""H3MD Keyframe - pure logic for the multi-person swap keyframes (2026-11-17, H3MD 0.5.0).

The nodes in h3md_keyframe_nodes.py wrap this file:
  1 Prepare: who is who (numbered left to right at the start), which moments become keyframes, where every person to
    recast stands at every moment.
  1b Recast (0.5.91): the user's H3 Image AIO chain - one plain sentence (who at which place becomes which
    picture), the official H3 Image prompt enhancer writes the prompt, one H3 Image edit redraws the keyframe.
  2 Assemble: the keyframes are saved and the Remake plan is written.

Method = skill 12d, line A, measured in demo5 (2026-11-16: our own 26 s five-person clip, 4 keyframes, Remake swapped
3 people correctly the whole time).  Numbering rule and mapping text from the exp MultiSwap pack (11-14/15).
No ComfyUI imports here (unit-tested on its own)."""
import math
import re

FPS = 24
REMAKE_SIZES = {"landscape": (1664, 960), "portrait": (960, 1664)}
MIN_FRAMES = 90           # Remake: 90-345 frames per autosplit part
MIN_H = 0.06              # a person shorter than this (fraction of the frame height) does not count as seen
MAX_TOP = 0.85            # ... nor one whose box starts below this (only a head peeking in at the bottom)
LINK_IOU = 0.3            # same person in two tracking windows: boxes on the shared frame overlap at least this
LOOK_S = 3.0              # the numbering moment is searched in the first seconds
CLEAR_IOU = 0.2
COVERED = 0.3
SPACING = 0.7            # keyframes are picked in (prev + 0.7 * max_gap, prev + max_gap]: as few as the gap allows
EDIT_MP = 4.0             # 0.5.91: H3 Image canvas (megapixels), as the user's AIO v1.01 (4.0, multiple 32)

CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
_PAT = re.compile(r"(?:图片|圖片|图|圖|picture|pic|image|img)\s*(\d+)[^\d\n,，;；。]*?(?:左|left)\s*(?:第|no\.?|#)?\s*"
                  r"([0-9]+|[一二两三四五六七八九十]+)", re.I)
TAG = "[H3MD Keyframe]"


class KeyframeError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------- mapping
def cn_number(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in CN:
        return CN[s]
    if len(s) == 2 and s[0] == "十" and s[1] in CN:
        return 10 + CN[s[1]]
    if len(s) == 2 and s[1] == "十" and s[0] in CN:
        return CN[s[0]] * 10
    raise KeyframeError(f"{TAG} '{s}' is not a number")


def parse_mapping(text: str) -> dict:
    """'图1换左二，图2换左四' / 'picture 1 -> left 2' -> {1: 2, 2: 4} (picture -> left number)."""
    out = {}
    for m in _PAT.finditer(text or ""):
        p, k = int(m.group(1)), cn_number(m.group(2))
        if p < 1 or k < 1:
            raise KeyframeError(f"{TAG} '{m.group(0)}': pictures and people are counted from 1")
        if p in out and out[p] != k:
            raise KeyframeError(f"{TAG} picture {p} is given twice (left {out[p]} and left {k}). 图{p}写了两次。")
        out[p] = k
    if not out:
        raise KeyframeError(f"{TAG} write which picture replaces which person, e.g. 图1换左二，图2换左四 or "
                            "'picture 1 -> left 2' (people are numbered left to right at the start). "
                            "请写清楚：图1换左几。")
    ks = list(out.values())
    dup = sorted({k for k in ks if ks.count(k) > 1})
    if dup:
        raise KeyframeError(f"{TAG} left {dup} is given to two pictures. 同一个人对了两张图：左{dup}。")
    return dict(sorted(out.items()))


def parse_descriptions(text: str, pictures) -> dict:
    """one line per picture (empty lines skipped) -> {picture: description}; every mapped picture needs one."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    lines = [re.sub(r"^(?:图|picture|pic)?\s*\d+\s*[:：.、)]\s*", "", l, flags=re.I) for l in lines]
    need = sorted(pictures)
    if len(lines) < max(need):
        raise KeyframeError(f"{TAG} write one description line per picture (race, age, face, hair, every garment); "
                            f"{len(lines)} line(s) for pictures up to {max(need)}. 每张角色图写一行外貌描述。")
    return {p: lines[p - 1] for p in need}


# ---------------------------------------------------------------------------------------------------- boxes
def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def covered(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def gap(b, c):
    """distance between two boxes (share of the frame width; < 0 = they overlap)"""
    return max(c[0] - b[2], b[0] - c[2], (c[1] - b[3]) * 9 / 16.0, (b[1] - c[3]) * 9 / 16.0)


def link_windows(windows):
    """Tracking runs in windows; consecutive windows share one frame.  windows: [ [ {local id: box} per frame ] ].
    -> one list of {global id: box} per frame (shared frames counted once).  A local id is the same person as the
    global id whose box on the shared frame overlaps it most (IoU >= LINK_IOU); otherwise a new person."""
    out, nxt = [], 0
    prev_map = {}
    for w, frames in enumerate(windows):
        mapping = {}
        if w > 0 and frames and out:
            shared_prev = out[-1]            # = the previous window's last frame (global ids)
            shared_new = frames[0]
            pairs = sorted(((iou(b, shared_prev[g]), lid, g) for lid, b in shared_new.items() for g in shared_prev),
                           reverse=True)
            used_l, used_g = set(), set()
            for s, lid, g in pairs:
                if s < LINK_IOU or lid in used_l or g in used_g:
                    continue
                mapping[lid] = g
                used_l.add(lid)
                used_g.add(g)
        for fr in frames:
            for lid in fr:
                if lid not in mapping:
                    mapping[lid] = nxt
                    nxt += 1
        start = 1 if (w > 0 and out) else 0
        for fr in frames[start:]:
            out.append({mapping[lid]: b for lid, b in fr.items()})
        prev_map = mapping
    return out


# 0.5.9: re-identify a person the tracker lost (blackout, shot cut, spin, leaving and coming back).  SAM3 links a person
# only while it can follow the box; after a cut or a blackout the same person comes back with a NEW id, and the old id
# (the one a picture is mapped to) just ends - later keyframes then miss that person (11-19: a dance with a blackout
# position change, the third woman was "lost at 7.8 s" and stayed unswapped).  A new id is joined to a lost one by
# (1) elimination - it is the only lost person it could be - or (2) look: hair (top of the box) and clothes (middle)
# colour histograms.  Two ids seen together for more than REID_OVERLAP frames are never the same person.  When it is
# not clear, nothing is joined and a warning says who could not be told apart.
REID_OVERLAP = 2          # sampled frames two ids may share and still be one person (a ghost box lingering in the dark)
REID_HANDOFF = 12        # sampled frames (2 s at 6 fps) an old and a new track of ONE person may run side by side when
                          # SAM3 hands over (11-19 log: old track 0 ended 0.8 s = 6 frames after new track 3 began) -
                          # allowed only if their boxes overlap there (a double box on one person) or they look alike
REID_HANDOFF_IOU = 0.3
REID_EDGE = 0.03          # a box this close to the left / right frame edge = walking out or in
REID_MIN_FRAMES = 2       # an id seen in fewer sampled frames is noise: never joined, never a candidate
REID_SIM_ELIM = 0.25      # only one lost person it could be: join unless the look is clearly different
REID_SIM_MIN = 0.55       # several lost people: the best look must reach this ...
REID_MARGIN = 0.08        # ... and beat the second best by this
REID_HAIR_W = 0.6         # look similarity = hair * this + clothes * (1 - this): hair colour tells people apart better
                          # than clothes (navy vs royal-blue dresses; the same uniform)


def _hsv_hist(px):
    """px: [n, 3] uint8 RGB -> normalised histogram: 12 hues x 3 saturations x 3 values for coloured pixels plus 6
    grey levels for pixels with little saturation (black, white, grey hair or clothes)."""
    import numpy as np
    if px.size == 0:
        return None
    a = px.astype(np.float32) / 255.0
    mx, mn = a.max(1), a.min(1)
    d = mx - mn
    sat = np.where(mx > 1e-6, d / np.maximum(mx, 1e-6), 0.0)
    r, g, b = a[:, 0], a[:, 1], a[:, 2]
    dd = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) / 6.0
    grey = (sat < 0.18) | (mx < 0.12)
    hist = np.zeros(12 * 3 * 3 + 6, np.float64)
    c = ~grey
    if c.any():
        hb = np.minimum((h[c] * 12).astype(int), 11)
        sb = np.minimum(((sat[c] - 0.18) / 0.82 * 3).astype(int), 2)
        vb = np.minimum((mx[c] * 3).astype(int), 2)
        np.add.at(hist, hb * 9 + sb * 3 + vb, 1)
    if grey.any():
        np.add.at(hist, 108 + np.minimum((mx[grey] * 6).astype(int), 5), 1)
    return hist / hist.sum()


def appearance(frames_u8, per_frame):
    """mean hair / clothes histograms of every id over the sampled frames where it stands clear (not overlapping
    another box much).  frames_u8: [N, h, w, 3]; per_frame: [{id: box (0-1)}].  -> {id: (hair, clothes)}"""
    import numpy as np
    acc = {}
    for img, fr in zip(frames_u8, per_frame):
        # grey-world white balance per frame: coloured stage light (pink / violet / cyan washes) tints everyone the
        # same way and would make one dancer look like another from frame to frame
        f = img.astype(np.float32)
        m = f.reshape(-1, 3).mean(0)
        gain = np.clip(m.mean() / np.maximum(m, 1.0), 1 / 3.0, 3.0)   # capped: a near-black frame is not blown up
        img = np.clip(f * gain, 0, 255).astype(np.uint8)
        hh, ww = img.shape[:2]
        items = list(fr.items())
        for pid, b in items:
            if (b[3] - b[1]) < MIN_H:
                continue
            if any(iou(b, c) > CLEAR_IOU or covered(b, c) > COVERED for q, c in items if q != pid):
                continue
            x0, x1 = int(b[0] * ww), int(b[2] * ww)
            y0, y1 = int(b[1] * hh), int(b[3] * hh)
            bw, bh = x1 - x0, y1 - y0
            if bw < 4 or bh < 8:
                continue
            hair = img[y0:y0 + max(2, int(bh * 0.15)), x0 + int(bw * 0.2):x1 - int(bw * 0.2)].reshape(-1, 3)
            body = img[y0 + int(bh * 0.25):y0 + int(bh * 0.70), x0 + int(bw * 0.25):x1 - int(bw * 0.25)].reshape(-1, 3)
            hh_, bb_ = _hsv_hist(hair), _hsv_hist(body)
            if hh_ is None or bb_ is None:
                continue
            a = acc.setdefault(pid, [np.zeros_like(hh_), np.zeros_like(bb_), 0])
            a[0] += hh_
            a[1] += bb_
            a[2] += 1
    return {pid: (a[0] / a[2], a[1] / a[2]) for pid, a in acc.items() if a[2]}


def look_similarity(a, b, hair_w=REID_HAIR_W):
    """histogram intersection of two appearance() entries, 0-1"""
    import numpy as np
    if a is None or b is None:
        return 0.0
    return float(hair_w * np.minimum(a[0], b[0]).sum() + (1 - hair_w) * np.minimum(a[1], b[1]).sum())


def reid_merge(per_frame, looks=None, times=None, keep=None):
    """join ids the tracker split.  per_frame: [{id: box}]; looks: appearance() or None (elimination only).
    keep: ids that must stay roots (the people numbered at the start).  -> (new per_frame, joins, unsure) where
    joins = [{"new": id, "into": id, "at": s, "why": "elimination"|"look", "look": sim}] and
    unsure = [{"new": id, "at": s, "candidates": [[id, sim], ...]}]."""
    times = times or list(range(len(per_frame)))
    looks = looks or {}
    seen = {}
    for i, fr in enumerate(per_frame):
        for pid in fr:
            seen.setdefault(pid, []).append(i)
    real = {pid for pid, fs in seen.items() if len(fs) >= REID_MIN_FRAMES}
    root = {pid: pid for pid in seen}

    def find(x):
        while root[x] != x:
            x = root[x]
        return x

    frames_of = {pid: set(fs) for pid, fs in seen.items()}
    first = {pid: fs[0] for pid, fs in seen.items()}
    keep = set(keep or ())
    start_ids = set(per_frame[0]) if per_frame else set()
    joins, unsure = [], []
    notes = []
    def box_of(pid_root, i, latest=True):
        bs = [bx for q, bx in per_frame[i].items() if find(q) == pid_root]
        return bs[-1] if bs else None

    def at_edge(bx):
        return bx is not None and (bx[0] <= REID_EDGE or bx[2] >= 1 - REID_EDGE)

    for new in sorted(real - start_ids, key=lambda q: first[q]):
        if new in keep:
            continue
        f0 = first[new]
        cands, why_not = [], []
        for old in sorted({find(q) for q in real if first[q] < f0}):
            if old == find(new):
                continue
            span = frames_of[old]
            shared = sorted(span & frames_of[new])
            if max(span) >= max(frames_of[new]):        # the old one is still there after the new one ends
                why_not.append(f"{old}: still there after it")
                continue
            ov = 0.0
            if len(shared) > REID_OVERLAP or max(span) > f0 + REID_OVERLAP:
                if len(shared) > REID_HANDOFF or max(span) > f0 + REID_HANDOFF:
                    why_not.append(f"{old}: seen together {len(shared)} frames")
                    continue
                ious = []
                for i in shared:
                    ob = [bx for q, bx in per_frame[i].items() if find(q) == old]
                    nb = per_frame[i].get(new)
                    if ob and nb is not None:
                        ious.append(max(iou(bx, nb) for bx in ob))
                ov = sum(ious) / len(ious) if ious else 0.0
            # walked out of the frame / walked in: then it can be a different person - do not decide by elimination
            edge = at_edge(box_of(old, max(span))) or at_edge(per_frame[f0].get(new))
            cands.append((old, ov, edge, len(shared)))
        notes.append(f"track {new} from {times[f0]:.1f} s: candidates "
                     f"{[(c[0], 'overlap %d IoU %.2f' % (c[3], c[1]), 'edge' if c[2] else 'inside') for c in cands]}; "
                     f"ruled out {why_not}")
        if not cands:
            continue
        nl = looks.get(new) if looks else None
        scored = []
        for old, ov, edge, sh in cands:
            lk = look_similarity(looks.get(old), nl) if looks else 0.0
            scored.append((lk, old, ov, edge))
        scored.sort(reverse=True)
        lk, best, ov, edge = scored[0]
        others = {find(q) for q in per_frame[f0] if find(q) not in {c[0] for c in cands} | {find(new)}}
        rival = max([look_similarity(looks.get(o), nl) for o in others] or [0.0]) if looks else 0.0
        why = None
        if len(scored) == 1:
            if ov >= REID_HANDOFF_IOU:
                why = "double box"                       # the two tracks sat on one person during the hand-over
            elif not edge and (nl is None or (lk >= REID_SIM_ELIM and lk >= rival - REID_MARGIN)):
                why = "elimination"                      # the only person missing, nobody walked out or in, and
                                                         # the look does not point to someone else
            elif lk >= REID_SIM_MIN and lk - rival >= REID_MARGIN:
                why = "look"
        else:
            if lk >= REID_SIM_MIN and lk - scored[1][0] >= REID_MARGIN and lk - rival >= REID_MARGIN:
                why = "look"
        if not why:
            unsure.append({"new": new, "at": times[f0], "candidates": [[o, round(s_, 3)] for s_, o, _, _ in scored]})
            notes.append(f"track {new}: not joined (look {lk:.2f}, best other on screen {rival:.2f}, "
                         f"{'edge' if edge else 'inside'})")
            continue
        root[new] = best
        frames_of[best] = frames_of[best] | frames_of[new]
        joins.append({"new": new, "into": best, "at": times[f0], "why": why, "look": round(lk, 3)})
    out = []
    for i, fr in enumerate(per_frame):
        row = {}
        for pid, b in sorted(fr.items(), key=lambda kv: first[kv[0]]):
            row[find(pid)] = b                           # where both were seen, the newer track's box wins
        out.append(row)
    reid_merge.notes = notes
    return out, joins, unsure


def report(per_frame, times):
    """{id: box} per sampled frame + times -> [{"at": t, "who": [[id, box], ...] left to right}]"""
    return [{"at": round(t, 4), "who": sorted([[i, list(map(float, b))] for i, b in fr.items()],
                                              key=lambda w: (w[1][0] + w[1][2]) / 2)}
            for fr, t in zip(per_frame, times)]


def visible(frame, relaxed=False):
    out = [(w[0], w[1]) for w in frame.get("who") or ()
           if relaxed or ((w[1][3] - w[1][1]) >= MIN_H and w[1][1] <= MAX_TOP)]
    return sorted(out, key=lambda w: (w[1][0] + w[1][2]) / 2.0)


def number_people(frames, look_s=LOOK_S):
    """-> (frame used, [ids left to right]): the first frame in the first `look_s` seconds that shows the most people,
    none overlapping much (else the one where the most hidden person is hidden least)."""
    frames = [f for f in frames if visible(f)]
    if not frames:
        raise KeyframeError(f"{TAG} nobody was found in the video. 视频里没找到人。")
    t0 = frames[0]["at"]
    look = [f for f in frames if f["at"] - t0 <= look_s] or frames[:1]
    best = max(len(visible(f)) for f in look)

    def worst(f):
        bx = [b for _, b in visible(f)]
        return max([max(iou(bx[i], bx[j]) / CLEAR_IOU, covered(bx[i], bx[j]) / COVERED)
                    for i in range(len(bx)) for j in range(i + 1, len(bx))] or [0.0])

    cand = [f for f in look if len(visible(f)) == best]
    clear = [f for f in cand if worst(f) <= 1.0]
    pick = clear[0] if clear else min(cand, key=worst)
    return pick, [pid for pid, _ in visible(pick)]


def resolve(mapping: dict, order: list) -> list:
    """{picture: left number} + ids left to right -> [(picture, left number, person id)]."""
    out = []
    for p, k in mapping.items():
        if k > len(order):
            raise KeyframeError(f"{TAG} only {len(order)} people are seen at the start, left {k} does not exist. "
                                f"开头只看到 {len(order)} 个人，没有左{k}。")
        out.append((p, k, order[k - 1]))
    return out


# ---------------------------------------------------------------------------------------------------- moments
def _motion(frames, i, pid):
    """how far this person's box centre moves between the neighbouring sampled frames (share of the frame width)"""
    def centre(f):
        b = next((w[1] for w in f.get("who") or () if w[0] == pid), None)
        return None if b is None else ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    c = centre(frames[i])
    d = [abs(c[0] - o[0]) + abs(c[1] - o[1]) for j in (i - 1, i + 1) if 0 <= j < len(frames)
         for o in [centre(frames[j])] if o is not None] if c else []
    return max(d) if d else 0.0


CLOSE_EDGE = 0.03   # 0.5.9: a chosen person whose box runs from the top edge to the bottom edge (head and legs cut
                    # off) is an extreme close-up: no keyframe there.  Waist-up / thigh-up shots stay usable - the edit
                    # prompt then says she is cut off by the bottom edge (tested on the real 11-19 tracks: box shape
                    # and height cannot tell a waist-up close-up from a thigh-up mid shot of dancers with arms out)
FRAME_ASPECT = 16 / 9   # frame width / height; Prepare sets it (kept for callers)


def closeup(frame, chosen_ids):
    """a frame the edit model cannot redraw at the right size: a chosen person (1) fills the whole frame height (cut
    off at the top and the bottom), or (2) is cut off by the bottom AND a side edge - only part of her is in the
    frame.  The crop is then clipped at the frame edge and the edit model draws a whole, smaller person inside it,
    with the original's cut-off part left beside her (2026-11-19 run 38, 2.33 s: all three shrunk to full body and a
    ghost of the original at the left edge; 0.33 s, same framing but nobody at a side edge, kept the size)."""
    for pid, b in visible(frame):
        if pid not in chosen_ids:
            continue
        if b[1] <= CLOSE_EDGE and b[3] >= 1 - CLOSE_EDGE:
            return True
        if b[3] >= 1 - CLOSE_EDGE and (b[0] <= CLOSE_EDGE or b[2] >= 1 - CLOSE_EDGE):
            return True
    return False


def cut_at_bottom(box):
    """the person is cut off by the bottom edge (waist-up / thigh-up framing)"""
    return box[3] >= 1 - CLOSE_EDGE


def score(frames, i, chosen_ids):
    """(chosen people seen, smallest gap of a chosen person to anybody else (capped), -motion, mean height) or None
    (nobody chosen seen, or a close-up)"""
    vis = visible(frames[i])
    ch = [w for w in vis if w[0] in chosen_ids]
    if not ch or closeup(frames[i], chosen_ids):
        return None
    g = 0.05
    for pid, b in ch:
        for q, c in vis:
            if q != pid:
                g = min(g, gap(b, c))
    mot = max(_motion(frames, i, pid) for pid, _ in ch)
    return (len(ch), round(g, 2), -round(mot, 2), sum(b[3] - b[1] for _, b in ch) / len(ch))


def pick_moments(frames, chosen_ids, duration, max_gap=5.0):
    """Keyframe moments one after another, never more than max_gap apart (Remake: every autosplit part needs one).
    Each next one is picked in (prev + 0.7 max_gap, prev + max_gap]: most chosen people seen, farthest from everybody
    else (people crossing are skipped), least motion, biggest.  -> [(index into frames, kind)]; kind "swap" (chosen
    people seen) or "plain" (nobody chosen in the frame: the source frame itself is the keyframe)."""
    last = duration - 2.0 / FPS
    idx = [i for i, f in enumerate(frames) if 0.0 <= f["at"] <= last]
    out, lo, hi = [], 0.0, min(max_gap / 2.0, last)   # the first one near the start (who is who)
    while idx and lo <= last:
        cand = [i for i in idx if lo <= frames[i]["at"] <= hi]
        if not cand:
            cand = [min(idx, key=lambda i: abs(frames[i]["at"] - hi))]
        best, bs = None, None
        for i in (reversed(cand) if out else cand):  # ties: first one early, then the later moment (fewer in all)
            s = score(frames, i, chosen_ids)
            if s is not None and (bs is None or s > bs):
                best, bs = i, s
        if best is None:
            present = [i for i in cand if any(w[0] in chosen_ids for w in frames[i].get("who") or ())]
            usable = [i for i in present if not closeup(frames[i], chosen_ids)]
            if present and not usable:
                # only close-ups here: no keyframe, look further on (the next mid shot carries the swap)
                lo, hi = hi, min(hi + max_gap, last)
                if lo >= last:
                    break
                continue
            if usable:
                best = max(usable, key=lambda i: max(w[1][3] - w[1][1] for w in frames[i]["who"]
                                                     if w[0] in chosen_ids))
                out.append((best, "swap"))
            else:
                best = cand[len(cand) // 2]
                out.append((best, "plain"))
        else:
            out.append((best, "swap"))
        t = frames[best]["at"]
        if t + max_gap >= last:
            break
        lo, hi = t + SPACING * max_gap, t + max_gap
    return out



# ---------------------------------------------------------------------------------------------------- cuts (0.5.8)
CUT_HIST, CUT_PIX, CUT_HIST_RATIO, CUT_PIX_RATIO, CUT_MIN_SHOT = 0.10, 0.12, 3.0, 2.5, 0.5
SHOT_SETTLE, SHOT_LOOK = 0.3, 2.5


def cuts_from(times, hist_d, pix_d):
    """shot cuts from per-frame distances to the previous frame: colour-histogram distance (1 - intersection) and mean
    pixel difference (0-1).  A cut is a jump in both, well above the level of the frames around it (a fast dance or a
    camera move stays below).  Tested 2026-11-19 on 8 clips: the 3 cuts of the cosplay MV found, no false cut in 5
    single-shot dances."""
    import numpy as np
    hd, pd = np.asarray(hist_d, np.float32), np.asarray(pix_d, np.float32)
    cuts = []
    for i in range(1, len(times)):
        lo, hi = max(1, i - 12), min(len(times), i + 13)
        if hi - lo <= 2:
            continue
        lh = float(np.median(np.r_[hd[lo:i], hd[i + 1:hi]]))
        lp = float(np.median(np.r_[pd[lo:i], pd[i + 1:hi]]))
        if (hd[i] > CUT_HIST and pd[i] > CUT_PIX and hd[i] > CUT_HIST_RATIO * max(lh, 0.02)
                and pd[i] > CUT_PIX_RATIO * max(lp, 0.01)):
            if not cuts or times[i] - cuts[-1] >= CUT_MIN_SHOT:
                cuts.append(round(float(times[i]), 3))
    return cuts


def ensure_per_shot(frames, moments, chosen_ids, cuts, duration, settle=SHOT_SETTLE, look=SHOT_LOOK):
    """skill 12d rule 'one keyframe per shot': a shot without a keyframe gets one - the best scored frame (most chosen
    people seen, standing apart, little motion) in the first `look` s after the cut (skipping `settle` s), else
    anywhere in the shot.  Without a keyframe after a cut Remake cannot tell who is who in the new shot (2026-11-19:
    a person drawn twice after the cut at 14.3 s).  -> moments sorted by time."""
    bounds = [0.0] + sorted(c for c in cuts if 0.0 < c < duration) + [duration]
    out = list(moments)
    have = [frames[i]["at"] for i, _ in out]
    for s0, s1 in zip(bounds, bounds[1:]):
        if s1 - s0 < 0.4 or any(s0 <= t < s1 for t in have):
            continue
        cand, scored = [], []
        for lo, hi in ((s0 + settle, min(s1, s0 + settle + look)), (s0 + settle, s1)):
            cand = [i for i, f in enumerate(frames) if lo <= f["at"] < hi - 1.0 / FPS]
            scored = [(s, i) for i in cand for s in [score(frames, i, chosen_ids)] if s is not None]
            if scored:
                break
        if scored:
            out.append((max(scored)[1], "swap"))
        elif cand and not any(any(w[0] in chosen_ids for w in frames[i].get("who") or ()) for i in cand):
            out.append((cand[len(cand) // 2], "plain"))
        # else: the chosen people are there but only in close-ups - no keyframe (an edited close-up does more harm)
    return sorted(out, key=lambda m: frames[m[0]]["at"])


PART_FRAMES = 345          # Remake renders at most this per autosplit part ...
PART_MAX_KF = 4            # ... and takes only the first 4 keyframes of a part (the rest are dropped - h3md_report)


FULL_SHRINK = 0.85        # prefer_full: a frame of the same shot where the people are this much smaller (camera farther)


def prefer_full(frames, moments, chosen_ids, cuts, duration, max_gap=5.0):
    """0.5.9: a keyframe where a chosen person is cut off by the bottom edge (waist-up, chest-up) moves to a frame of
    the SAME shot where every chosen person seen is whole (feet in the frame), else where they are clearly smaller
    (camera farther, more of the body in the frame) - if the shot has one and the move keeps
    every gap <= max_gap (or no worse than before) and nobody changes places in between (the keyframe still pins
    the same left-to-right order).  The edit model tends to redraw a cut-off person as a smaller
    full-body one (run 38, 2.33 s: the chest-up woman in the middle shrank although she was at no side edge; the
    full-body keyframes 6.83/11.17/16.17 kept their size).  -> (moments sorted by time, notes)"""
    out = sorted(moments, key=lambda m: frames[m[0]]["at"])
    notes = []
    bounds = [0.0] + sorted(c for c in cuts if 0.0 < c < duration) + [duration]
    shot = lambda t_: max(k for k, b0 in enumerate(bounds) if b0 <= t_)

    def cut(i):
        return any(cut_at_bottom(b) for pid, b in visible(frames[i]) if pid in chosen_ids)

    def seen(i):
        return sum(1 for pid, _ in visible(frames[i]) if pid in chosen_ids)

    def mh(i):
        hs = [b[3] - b[1] for pid, b in visible(frames[i]) if pid in chosen_ids]
        return sum(hs) / max(1, len(hs))

    def worst_gap(ts):
        pts = [0.0] + sorted(ts) + [duration]
        return max(b - a for a, b in zip(pts, pts[1:]))

    last = duration - 2.0 / FPS
    try:
        changes = place_changes(frames, sorted(chosen_ids))
    except Exception:
        changes = []
    for k, (i, kind) in enumerate(list(out)):
        if kind != "swap" or not cut(i):
            continue
        t, sh = frames[i]["at"], shot(frames[i]["at"])
        ts = [frames[j]["at"] for j, _ in out]
        limit = max(max_gap, worst_gap(ts))
        others = ts[:k] + ts[k + 1:]
        cand = [j for j, f in enumerate(frames)
                if f["at"] <= last and shot(f["at"]) == sh and j != i and seen(j) >= seen(i)
                and (not cut(j) or mh(j) <= FULL_SHRINK * mh(i))
                and not any(min(t, f["at"]) <= c1 and c0 <= max(t, f["at"]) for c0, c1, _ in changes)
                and score(frames, j, chosen_ids) is not None and f["at"] not in others
                and worst_gap(others + [f["at"]]) <= limit]
        if not cand:
            continue
        best = max(cand, key=lambda j: (not cut(j), score(frames, j, chosen_ids), -abs(frames[j]["at"] - t)))
        out[k] = (best, kind)
        notes.append(f"keyframe {t:.2f} s -> {frames[best]['at']:.2f} s (same shot, people "
                     + ("whole instead of cut off by the bottom edge)" if not cut(best) else
                        f"farther away: {mh(best):.2f} instead of {mh(i):.2f} of the frame height)"))
    return sorted(out, key=lambda m: frames[m[0]]["at"]), notes


def part_span(duration, part_frames=PART_FRAMES):
    """seconds of new frames the biggest autosplit part can carry: the Director splits a shot evenly (h3md_autosplit
    .plan_parts) and may move a cut point by one 17-frame unit, so this + 17 frames.  A smaller VRAM budget only makes
    the parts smaller.  Falls back to part_frames.  (Run 38: a fixed 14.4 s window dropped the keyframe at 8.33 s
    although the real parts were 0-8.7 s with 3 and 8.7-16.5 s with 2.)"""
    try:
        from . import h3md_autosplit as A
    except ImportError:
        try:
            import h3md_autosplit as A
        except ImportError:
            return part_frames / FPS
    n = int(round(duration * FPS))
    try:
        parts = A.plan_parts(n, 0, int(part_frames))
    except Exception:
        parts = None
    if not parts:
        return part_frames / FPS
    return min(part_frames, max(parts) + 17 * getattr(A, "NUDGE_UNITS", 1)) / FPS


def finish_moments(frames, moments, chosen_ids, duration, max_gap=5.0, part_frames=PART_FRAMES,
                   part_max=PART_MAX_KF, cuts=()):
    """0.5.9 cross-check of the moment rules (picks every <= max_gap s, one per shot, none on close-ups):
    (1) no keyframe at all (only close-ups found) -> the best frame anyway, close-ups allowed - a part without any
        keyframe is an error;
    (2) more than part_max keyframes inside any part_frames stretch -> thin out: drop the one whose removal leaves
        the smallest gap, never the only keyframe of a shot window - else Remake would silently drop the LAST ones
        of that part (often the ones right after a cut, the most important).
    -> (moments sorted by time, notes)"""
    notes = []
    out = sorted(moments, key=lambda m: frames[m[0]]["at"])
    if not out:
        cand = [i for i, f in enumerate(frames) if any(w[0] in chosen_ids for w in f.get("who") or ())]
        if cand:
            best = max(cand, key=lambda i: (len([w for w in frames[i]["who"] if w[0] in chosen_ids]),
                                             -_motion(frames, i, frames[i]["who"][0][0])))
            out = [(best, "swap")]
            notes.append(f"only close-ups found: one keyframe at {frames[best]['at']:.2f} s anyway")
    span = part_span(duration, part_frames)
    changed = True
    while changed:
        changed = False
        ts = [frames[i]["at"] for i, _ in out]
        for a in range(len(ts)):
            win = [j for j in range(a, len(ts)) if ts[j] - ts[a] < span]
            if len(win) <= part_max:
                continue
            def gap_without(j):
                rest = ts[:j] + ts[j + 1:]
                pts = [0.0] + rest + [duration]
                return max(b - a_ for a_, b in zip(pts, pts[1:]))
            bounds = [0.0] + sorted(c for c in cuts if 0 < c < duration) + [duration]
            shot = lambda t_: max(k for k, b0 in enumerate(bounds) if b0 <= t_)
            alone = {j for j in win if sum(1 for t_ in ts if shot(t_) == shot(ts[j])) == 1}
            pool = [j for j in win if j not in alone] or win      # the only keyframe of a shot stays if possible
            drop = min(pool, key=lambda j: (gap_without(j), -abs(ts[j] - ts[j - 1]) if j else 0))
            notes.append(f"{len(win)} keyframes within {span:.1f} s: dropped the one at {ts[drop]:.2f} s "
                         f"(Remake keeps {part_max} per part)")
            out.pop(drop)
            changed = True
            break
    return out, notes


def place_changes(frames, ids, min_hold=0.5):
    """[(t0, t1, [(a, b), ...])]: moments where the left-to-right order of `ids` changes, with the pairs that swapped.
    Only frames where all of them are seen count; an order has to hold min_hold seconds to count."""
    rows = []
    for f in frames:
        vis = [pid for pid, _ in visible(f, relaxed=True) if pid in ids]
        if len(vis) == len(ids):
            rows.append((f["at"], tuple(vis)))
    stable, out = [], []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] == rows[i][1]:
            j += 1
        if rows[j][0] - rows[i][0] >= min_hold or not stable:
            if stable and stable[-1][2] != rows[i][1]:
                stable.append((rows[i][0], rows[j][0], rows[i][1]))
            elif not stable:
                stable.append((rows[i][0], rows[j][0], rows[i][1]))
        i = j + 1
    for (a0, a1, oa), (b0, b1, ob) in zip(stable, stable[1:]):
        # who took whose place: a and b "change places" when each now stands where the other stood (people in
        # between only shift is impossible here - a row keeps its slots; anything else is "moves to X's place")
        pairs, done = [], set()
        for k, x in enumerate(oa):
            if x in done or ob[k] == x:
                continue
            y = ob[k]                                  # y now stands in x's old slot
            if ob[oa.index(y)] == x:
                pairs.append((x, y))
                done |= {x, y}
            else:
                pairs.append((y, x, "moves"))
                done.add(y)
        out.append((round(a1, 2), round(b0, 2), pairs))
    return out


# ---------------------------------------------------------------------------------------------------- crops
def target_size(src_w, src_h):
    return REMAKE_SIZES["landscape" if src_w >= src_h else "portrait"]


def cover_crop(src_w, src_h, tw, th):
    """centre crop (x0, y0, w, h) of the source with the target's aspect (same as Remake does with the source)"""
    s = max(tw / src_w, th / src_h)
    w, h = tw / s, th / s
    return int(round((src_w - w) / 2)), int(round((src_h - h) / 2)), int(round(w)), int(round(h))


def fit_size(w, h, mp=EDIT_MP, grid=32):
    """a canvas of about mp megapixels with the aspect of w x h, multiples of grid"""
    asp, area = w / float(max(1, h)), mp * 1e6
    best = None
    for gh in range(grid, 4096 + grid, grid):
        for gw in {max(grid, math.floor(gh * asp / grid) * grid), max(grid, math.ceil(gh * asp / grid) * grid)}:
            if abs(gw * gh - area) <= 0.15 * area:
                err = abs(gw / float(gh) - asp)
                if best is None or err < best[0] - 1e-9:
                    best = (err, gw, gh)
    if best is None:
        sc = math.sqrt(area / max(1, w * h))
        return max(grid, round(w * sc / grid) * grid), max(grid, round(h * sc / grid) * grid)
    return best[1], best[2]


def frame_rank(own, others):
    """(place from the left, people in the frame) of a person in THIS keyframe, by box centre.  The mapping number
    (left k at the start) is the person's ID; where she stands at a later keyframe can differ."""
    cx = (own[0] + own[2]) / 2.0
    return 1 + sum(1 for b in others if (b[0] + b[2]) / 2.0 < cx), 1 + len(others)


def parse_people(text: str) -> list:
    """optional: one line per person at the start, left to right (what they look like now) -> [str]"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return [re.sub(r"^(?:左|left|L)?\s*\d+\s*[:：.、)]\s*", "", l, flags=re.I).rstrip("。.") for l in lines]


# ----------------------------------------------- 0.5.91 Recast: the user's H3 Image AIO v1.01 chain (18:31)
# One plain sentence (the user's own wording, 18:28) -> the official MiniMax H3 Image prompt enhancer writes the
# prompt -> one H3 Image edit of the whole keyframe.  Test 18:27 (user, 0 s keyframe): all three swapped right, in
# place, pose kept, whole outfit, no extra people.  Our own long prompt template (17:32/17:47: people added instead
# of replaced) and the Qwen Image 2.1 path are gone.
SWAP_KEEP = ("严格保持图1中的对应人物的朝向,姿势,动作,表情,口型,人物在图片里的位置和所占比例的大小,还有前后遮挡关系不变. "
             "严格保持{pics}里的人的五官外貌,发型发色,服装等特征不变,")


def h3_size(w, h, mp=EDIT_MP, grid=32):
    """the edit canvas as the AIO workflow's Resolution Selector makes it: the keyframe aspect at mp megapixels, each
    side rounded to a multiple of grid"""
    asp = w / float(max(1, h))
    return (max(grid, int(round(math.sqrt(mp * 1e6 * asp) / grid)) * grid),
            max(grid, int(round(math.sqrt(mp * 1e6 / asp) / grid)) * grid))


def swap_sentence(pairs, n_people) -> str:
    """the plain request for the enhancer: pairs [(place from the left in this keyframe, picture number)], n_people
    in the keyframe.  'replace the 1st from the left with picture 3, the 2nd with picture 4, ...' + the user's keep
    clause; others not recast are kept."""
    pairs = sorted(pairs)
    head = "把图1里" + "，".join(f"从左数第{r}个人替换成图{i}里的人" for r, i in pairs) + "，"
    if len(pairs) < n_people:
        head += "图1里其他的人保持不变，"
    pics = "、".join(f"图{i}" for i in sorted(i for _, i in pairs))
    return head + SWAP_KEEP.format(pics=pics)


def pe_caption(text):
    """the enhancer's expanded_caption (its answer is one JSON object, after an optional thinking part) or None"""
    import json
    t = str(text or "")
    if "</think>" in t:
        t = t.rsplit("</think>", 1)[1]
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        cap = json.loads(t[i:j + 1]).get("expanded_caption")
    except Exception:
        m = re.search(r'"expanded_caption"\s*:\s*"((?:[^"\\]|\\.)*)"', t[i:j + 1], re.S)
        cap = json.loads('"' + m.group(1) + '"') if m else None
    cap = (cap or "").strip()
    return cap or None


def plan_length(n_frames: int) -> int:
    """largest 5 + 17n not past the source's end (the Director would round UP and run past it)"""
    if n_frames < MIN_FRAMES:
        raise KeyframeError(f"{TAG} the video has {n_frames} frames; Remake needs at least {MIN_FRAMES} "
                            f"({MIN_FRAMES / FPS:.2f} s). 视频太短。")
    return 5 + 17 * ((n_frames - 5) // 17)


def ordinal(k: int) -> str:
    return ORDINAL[k - 1] if 1 <= k <= len(ORDINAL) else f"number {k}"


def swap_prompt(length, chosen, descs, n_people, changes, names, looks=None):
    """skill 12d step 6 (Remake wording).  chosen [(picture, left, id)], descs {picture: text}, names {id: label} in
    left-to-right order, looks {left number: what that person looks like at the start} (optional)."""
    looks = looks or {}
    sub = {pid: f"<Subject {n}>" for n, (_, _, pid) in enumerate(chosen, 1)}

    def who(pid):
        return sub.get(pid) or names.get(pid) or "another performer"

    lines = [f"Duration: {length / FPS:.2f} s ({length} frames).", "", "subject_definitions:",
             f"<Video 1> is the source video with {n_people} performer{'s' if n_people != 1 else ''}: it supplies the "
             "motion, gestures, head and lip movement, poses, positions, place changes, timing, the camera, the depth "
             "of field, the surroundings, the lighting and the soundtrack.",
             "The <Picture> images are redrawn frames of this same run: each shows the people at that moment with "
             "their final look, in the positions and poses they hold then; together they fix who is who as the "
             "performers change places."]
    for n, (p, k, pid) in enumerate(chosen, 1):
        orig = f", {looks[k]}" if looks.get(k) else ""
        lines.append(f"<Subject {n}> is {descs[p].rstrip('.')}; this subject replaces the {ordinal(k)} performer "
                     f"counted from the left at the start{orig}.")
    lines.append("In every frame each Subject has the face, hair and clothes described here, never those of the "
                 "performer they replace.")
    rest = n_people - len(chosen)
    kept = [looks[k] for k in range(1, n_people + 1) if looks.get(k) and k not in {c[1] for c in chosen}]
    kept_s = (", " + " and ".join(kept) + "," if kept and len(kept) == rest else "")
    keep = (f" The other {rest} performer{'s' if rest != 1 else ''}{kept_s} stay themselves, with their own faces, "
            "hair and outfits from <Video 1> in every frame.") if rest > 0 else ""
    mp = ", ".join(f"the {ordinal(k)} becomes <Subject {n}>" for n, (_, k, _) in enumerate(chosen, 1))
    lines += ["", "summary:",
              f"The target video is an edited version of <Video 1>. [video editing + reference generation + audio "
              f"reuse] Recast {len(chosen)} of the {n_people} performers with a fixed mapping the whole time, counted "
              f"from left to right at the start: {mp}.{keep} Every recast person keeps the same replacement through "
              "place changes, in the same position, blocking and occlusion order as their performer in every frame; "
              "the motion, timing, performance, singing and lip and jaw movement stay as in <Video 1>.",
              "", "retention_analysis:",
              "<Video 1>: partially_preserved - motion, posture, lip and jaw movement, timing, camera, framing, scene, "
              "lighting and colour grade are retained" + (", and the other performers keep their own look" if rest
                                                           else "") +
              "; the face, hair and outfit of the recast performers are not carried over."]
    for n, (p, _, _) in enumerate(chosen, 1):
        lines.append(f"<Subject {n}>: fully_preserved - {descs[p].rstrip('.')}, identical in every moment.")
    lines.append("Source audio: fully_copy - the original soundtrack and its synchronisation are kept.")
    order = ", ".join(who(pid) for pid in names)
    def move(pr):
        return (f"{who(pr[0])} moves into the place of {who(pr[1])}" if len(pr) > 2
                else f"{who(pr[0])} and {who(pr[1])} change places")
    moves = "".join(f" Around {t0:.1f} to {t1:.1f} seconds " + "; ".join(move(pr) for pr in pairs) + "."
                    for t0, t1, pairs in changes if pairs)
    lines += ["", "detailed_description:",
              "A natural, clean look. [Shot 1] One continuous take matching the framing and camera of <Video 1>. "
              "The setting is the one in <Video 1>, unchanged. Each recast person moves exactly like their performer "
              f"in <Video 1>, with the same lip movement. At the start, from left to right: {order}.{moves} Each "
              "recast subject keeps their own face, hair and clothes the whole time, as in the redrawn frames. Their "
              "hands have five natural fingers each.",
              "", "overall_soundscape:", "Keep the source soundtrack; do not invent dialogue.",
              "", "non_diegetic_music:", "The music of <Video 1> plays unchanged as the soundtrack."]
    return "\n".join(lines)


def build_plan(video_file, duration_s, has_audio, size, length, keyframes, prompt, name):
    """keyframes [(media id, file, seconds)] -> the H3MD plan dict (swapEngine remake, one shot, autosplit)."""
    w, h = size
    return {"version": 1, "fps": FPS, "width": w, "height": h, "name": name, "globalPrompt": "",
            "globalMode": "prepend", "refVideoHeight": "auto", "autoSplit": True, "swapEngine": "remake",
            "media": {"images": [{"id": i, "file": f} for i, f, _ in keyframes],
                      "videos": [{"id": "src", "file": video_file, "duration": round(float(duration_s), 3),
                                  "hasAudio": bool(has_audio)}],
                      "audios": [], "refmods": []},
            "segments": [{"length": int(length), "overlapIn": 0, "images": [],
                          "videos": [{"id": "src", "trimStart": 0, "useSoundtrack": False, "depth": False,
                                      "useDuration": round(length / FPS, 4)}],
                          "audios": [], "audioMode": "locked" if has_audio else "none",
                          **({"locked": {"source": "video:src", "offset": 0}} if has_audio else {}),
                          "keyframes": [{"image": i, "at": round(float(t), 4)} for i, _, t in keyframes],
                          "prompt": prompt}]}
