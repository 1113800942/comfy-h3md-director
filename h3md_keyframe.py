"""H3MD Keyframe - pure logic for the multi-person swap keyframes (2026-11-17, H3MD 0.5.0).

The nodes in h3md_keyframe_nodes.py wrap this file:
  1 Prepare: who is who (numbered in order of first appearance), which moments become keyframes, where every person to
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
CAST_MIN_S = 1.0          # 0.5.91 (user 01:52): a person counts (gets a number, can take a picture) when seen clearly
                          # this long - a passer-by or a sliver at the edge does not use up a picture
CAST_TOGETHER_S = 1.0     # 0.5.91: people first seen within this many seconds of each other appear "together"
SLIVER_W = 0.08           # 0.5.91: a person cut off by a side edge and narrower than this share of the width is not
                          # recast at that keyframe (2026-11-21 _004: picture 3 was put on a 26 px strip)
CLEAR_IOU = 0.2
COVERED = 0.3
SPACING = 0.7            # keyframes are picked in (prev + 0.7 * max_gap, prev + max_gap]: as few as the gap allows
EDIT_MP = 4.0             # 0.5.91: H3 Image canvas (megapixels), as the user's AIO v1.01 (4.0, multiple 32)

CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
# 0.5.91: '图1换3号' / 'picture 1 -> 3' / 'pic 1 -> #3': the person's number on the cast sheet (order of appearance)
_PAT = re.compile(r"(?:图片|圖片|图|圖|picture|pic|image|img)\s*(\d+)[^\d\n,，;；。一二两三四五六七八九十]*?"
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
    """optional override of the automatic cast: '图1换3号，图2换1号' / 'picture 1 -> 3' -> {1: 3, 2: 1} (picture ->
    person number on the cast sheet = order of first appearance).  Empty -> {} = automatic."""
    out = {}
    if not (text or "").strip():
        return out
    for m in _PAT.finditer(text or ""):
        p, k = int(m.group(1)), cn_number(m.group(2))
        if p < 1 or k < 1:
            raise KeyframeError(f"{TAG} '{m.group(0)}': pictures and people are counted from 1")
        out[p] = k                          # 0.5.91 (02:49): a picture written twice - the last line wins
                                            # (old text + a new choice below it must not stop the run)
    if not out:
        raise KeyframeError(f"{TAG} mapping: leave it empty (automatic: picture 1 = the first person to appear, "
                            "picture 2 = the next ...) or write e.g. 图1换3号 / 'picture 1 -> 3' with the numbers of "
                            "the cast sheet. 留空 = 自动；或写 图1换3号（演员表上的编号）。")
    ks = list(out.values())
    dup = sorted({k for k in ks if ks.count(k) > 1})
    if dup:
        raise KeyframeError(f"{TAG} person {dup} is given to two pictures. 同一个人对了两张图：{dup} 号。")
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


FACE_LO, FACE_HI = 0.15, 0.45   # 0.5.91: ArcFace cosine -> look 0-1 (different people ~0.0-0.15, one person 0.45+)


def face_similarity(fa, fb):
    """cosine of two L2-normalised face embeddings mapped to 0-1 (FACE_LO -> 0, FACE_HI -> 1)"""
    import numpy as np
    c = float(np.dot(fa, fb))
    return max(0.0, min(1.0, (c - FACE_LO) / (FACE_HI - FACE_LO)))


def look_similarity(a, b, hair_w=REID_HAIR_W):
    """0-1.  a, b: (hair hist, clothes hist[, face embedding]) - 0.5.91: when both have a face, the face decides (five
    men in the same white clothes: colour cannot tell them apart); else histogram intersection of hair and clothes."""
    import numpy as np
    if a is None or b is None:
        return 0.0
    fa = a[2] if len(a) > 2 else None
    fb = b[2] if len(b) > 2 else None
    if fa is not None and fb is not None:
        return face_similarity(fa, fb)
    if a[0] is None or b[0] is None or a[1] is None or b[1] is None:
        return 0.0
    return float(hair_w * np.minimum(a[0], b[0]).sum() + (1 - hair_w) * np.minimum(a[1], b[1]).sum())


def add_faces(looks, faces):
    """appearance() + {id: face embedding} -> {id: (hair, clothes, face)} (ids with only a face get None histograms)"""
    out = {pid: (lk[0], lk[1], None) for pid, lk in (looks or {}).items()}
    for pid, f in (faces or {}).items():
        h = out.get(pid, (None, None, None))
        out[pid] = (h[0], h[1], f)
    return out


def assign_face(face_box, boxes):
    """face box (x0, y0, x1, y1, 0-1) -> the person id whose box holds it in its upper part, or None.
    boxes: {id: box}.  Several: the one whose head height fits best."""
    cx, cy = (face_box[0] + face_box[2]) / 2, (face_box[1] + face_box[3]) / 2
    cand = []
    for pid, b in boxes.items():
        h = b[3] - b[1]
        if b[0] <= cx <= b[2] and b[1] - 0.02 <= cy <= b[1] + 0.4 * h:
            cand.append((abs(cy - (b[1] + 0.1 * h)) / max(h, 1e-6), pid))
    return min(cand)[1] if cand else None


FACE_SAME = 0.35          # 0.5.91: ArcFace cosine at which two faces are one person (same person ~0.5-0.8,
                          # different ~0.0-0.2)
FACE_MIN_COUNT = 3        # a face cluster needs this many faces to be a person (else: a stray detection)
FACE_KEEP_RUN = 2         # a track changes its face only after this many faces in a row of the other person


def face_clusters(embs, same=FACE_SAME, min_count=FACE_MIN_COUNT, rounds=3):
    """0.5.91 (2026-11-21 _004: five men in white, the tracker passed numbers between them when they crossed): group
    L2-normalised face embeddings into people.  Centroid clustering: each face joins the nearest person if the
    cosine reaches `same`, else starts a new one; centroids closer than `same` merge; then every face is assigned
    again.  -> (labels per face, -1 = none; centroids)"""
    import numpy as np
    if not len(embs):
        return [], []
    X = np.asarray(embs, np.float32)
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-6)
    cents = []
    for x in X:
        if cents:
            sims = np.asarray(cents) @ x
            j = int(np.argmax(sims))
            if sims[j] >= same:
                c = cents[j] + x
                cents[j] = c / max(float(np.linalg.norm(c)), 1e-6)
                continue
        cents.append(x.copy())
    for _ in range(rounds):
        C = np.asarray(cents)
        lab = np.argmax(X @ C.T, 1)
        cents = []
        for j in range(len(C)):
            m = X[lab == j]
            if len(m):
                c = m.mean(0)
                cents.append(c / max(float(np.linalg.norm(c)), 1e-6))
        merged = []
        for c in cents:                                   # people closer than `same` are one
            for k, d in enumerate(merged):
                if float(c @ d) >= same:
                    e = c + d
                    merged[k] = e / max(float(np.linalg.norm(e)), 1e-6)
                    break
            else:
                merged.append(c)
        cents = merged
    C = np.asarray(cents)
    S = X @ C.T
    lab = np.argmax(S, 1)
    counts = np.bincount(lab, minlength=len(C))
    out = [int(l) if S[i, l] >= same and counts[l] >= min_count else -1 for i, l in enumerate(lab)]
    keep = sorted({l for l in out if l >= 0})
    remap = {l: n for n, l in enumerate(keep)}
    return [remap.get(l, -1) for l in out], [C[l] for l in keep]


def split_by_face(per_frame, obs, labels, keep_run=FACE_KEEP_RUN):
    """0.5.91: a track is one person only while his face stays the same.  obs [(frame index, id, embedding)],
    labels from face_clusters.  Where a track's face changes to another person (for keep_run faces in a row) the
    track is cut half-way between the two faces; every piece with a face is renamed after that person (FACE_ID +
    person), so pieces of one person anywhere in the clip are one id; pieces without a face keep their id.  Two
    boxes of one person in one frame: the second keeps a separate id.  -> (new per_frame, {new id: person}, notes)"""
    by_id = {}
    for (i, pid, _), lab in zip(obs, labels):
        if lab >= 0:
            by_id.setdefault(pid, []).append((i, lab))
    # 0.5.91 (_012, 24.2-26.5 s): the lead singer turning away gave a 6th "person" whose faces all came from his own
    # track - a cluster seen on one track only, on a track that is mostly someone else, is that someone at an odd
    # angle: no cut
    # (_014: a few stray faces of that 'person' on other tracks kept the cut: count them instead - a person is
    # established only when a fair share of his faces are on OTHER tracks)
    total = {}
    for pid, seq in by_id.items():
        for _, lab in seq:
            total.setdefault(lab, {}).setdefault(pid, 0)
            total[lab][pid] += 1
    for pid, seq in by_id.items():
        counts = {}
        for _, lab in seq:
            counts[lab] = counts.get(lab, 0) + 1
        main = max(counts, key=counts.get)

        def elsewhere(lab):
            n_all = sum(total[lab].values())
            n_out = n_all - total[lab].get(pid, 0)
            return n_out >= max(FACE_MIN_COUNT, 0.3 * n_all)
        by_id[pid] = [(i, main if (lab != main and not elsewhere(lab)) else lab) for i, lab in seq]
    pieces = {}                                           # id -> [(from frame, to frame, person)]
    notes = []
    for pid, seq in by_id.items():
        seq.sort()
        runs = []                                         # [person, first i, last i, faces]
        for i, lab in seq:
            if runs and runs[-1][0] == lab:
                runs[-1][2], runs[-1][3] = i, runs[-1][3] + 1
            else:
                runs.append([lab, i, i, 1])
        clean = []                                        # a short run between two runs is a stray face
        for r in runs:
            if clean and r[3] < keep_run and r is not runs[-1]:
                continue
            if clean and clean[-1][0] == r[0]:
                clean[-1][2], clean[-1][3] = r[2], clean[-1][3] + r[3]
            else:
                clean.append(list(r))
        if len(clean) > 1 and clean[-1][3] < keep_run:    # a stray face at the very end
            clean[-2][2] = clean[-1][2]
            clean.pop()
        span = []
        for n, r in enumerate(clean):
            a = -10 ** 9 if n == 0 else (clean[n - 1][2] + r[1] + 1) // 2
            b = 10 ** 9 if n == len(clean) - 1 else (r[2] + clean[n + 1][1] + 1) // 2 - 1
            span.append((a, b, r[0]))
        pieces[pid] = span
        if len(span) > 1:
            notes.append(f"track {pid} changes person at frame(s) {[s_[0] for s_ in span[1:]]}: "
                         f"persons {[s_[2] for s_ in span]}")
    out, person_of = [], {}
    for i, fr in enumerate(per_frame):
        row = {}
        for pid, b in fr.items():
            nid = pid
            for a, z, lab in pieces.get(pid, ()):
                if a <= i <= z:
                    nid = FACE_ID + lab
                    break
            if nid in row:                                # one person twice in a frame
                if iou(row[nid], b) >= DUP_IOU:           # 0.5.91 (_012, 8.5 s): the same box twice - one copy
                    continue
                nid = SPLIT_ID + pid                      # else keep them apart
            row[nid] = b
            if nid >= FACE_ID and nid < SPLIT_ID:
                person_of[nid] = nid - FACE_ID
        out.append(row)
    return out, person_of, notes


FACE_ID, SPLIT_ID = 1000, 5000   # ids given by split_by_face (tracker ids are small numbers)
DUP_IOU = 0.7                    # two boxes of one person overlapping this much are one detection
WIDE_BOX = 1.6                   # 0.5.91: a box this much wider than the person's usual box spans two people
HIDDEN_COVER, HIDDEN_AREA = 0.8, 0.35   # a box this much inside a bigger one (and this small) = a hidden person


def clean_frames(frames):
    """0.5.91 (_012 keyframes 4-6, 8, 15): mark what the tracker got wrong, before any keyframe is chosen.
    - a box much wider than that person's usual box (WIDE_BOX x the median width) spans two people (a hidden man and
      the one in front): the frame gets "bad": [id] and is never a keyframe (score() is None);
    - a small box almost entirely inside another person's bigger box is someone hidden behind him: he does not count
      as seen in that frame (dropped from "who").
    -> the same frames, changed in place, and notes"""
    import statistics
    widths = {}
    for f in frames:
        for pid, b in f.get("who") or ():
            widths.setdefault(pid, []).append(b[2] - b[0])
    med = {pid: statistics.median(ws) for pid, ws in widths.items() if len(ws) >= 3}
    n_wide = n_hidden = 0
    for f in frames:
        who = f.get("who") or []
        keep = []
        for pid, b in who:
            area = (b[2] - b[0]) * (b[3] - b[1])
            hidden = any(covered(b, c) >= HIDDEN_COVER and area < HIDDEN_AREA * (c[2] - c[0]) * (c[3] - c[1])
                         for q, c in who if q != pid)
            if hidden:
                n_hidden += 1
                continue
            keep.append([pid, b])
        f["who"] = keep
        bad = [pid for pid, b in keep if pid in med and (b[2] - b[0]) > WIDE_BOX * med[pid]
               and any(iou(b, c) > CLEAR_IOU for q, c in keep if q != pid)]
        if bad:
            f["bad"] = bad
            n_wide += 1
    return frames, [f"{n_wide} frame(s) with a box spanning two people (never keyframes), {n_hidden} hidden "
                    "person box(es) not counted as seen"]


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
            gap = False
            if max(span) >= max(frames_of[new]):        # the old one is still there after the new one ends ...
                during = [i for i in span if f0 <= i <= max(frames_of[new])]
                if len(during) > REID_OVERLAP:          # ... and was there meanwhile: someone else
                    why_not.append(f"{old}: still there after it")
                    continue
                # 0.5.91 (_013, 24.2-26.5 s): the new track fills a gap of the old one (he turned his back, the
                # tracker gave his back a new number, then his face came back to the old one): a candidate
                gap = True
            ov = 0.0
            if not gap and (len(shared) > REID_OVERLAP or max(span) > f0 + REID_OVERLAP):
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
        def has_face(lk_):
            return lk_ is not None and len(lk_) > 2 and lk_[2] is not None
        if not why and has_face(nl) and all(has_face(looks.get(o)) and s_ <= 0.0 for s_, o, _, _ in scored):
            # 0.5.91 (_004, 3.0 s): every candidate has a face and none is like the new one - a new person walking
            # in, not someone lost: no warning
            notes.append(f"track {new}: a new person (the face matches none of {[s[1] for s in scored]})")
            continue
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


def sliver(b, width=SLIVER_W):
    """0.5.91: only a strip of the person is in the frame (cut off by a side edge and narrow): not recast there"""
    return (b[0] <= 0.01 or b[2] >= 0.99) and (b[2] - b[0]) < width


def side_of(x):
    return "left" if x < 0.36 else "right" if x > 0.64 else "centre"


def cast(frames, min_s=CAST_MIN_S):
    """0.5.91 (user 01:50/01:52, replaces 'left k in the first clear frame'): everyone in the clip seen clearly for at
    least min_s, numbered in order of first appearance (same moment: left to right).  A clip cut from the middle,
    people walking in later, places changing - the number belongs to the person, not to a place.
    -> [{"id", "first", "last", "side" (where first seen), "seen" s, "presence", "best" (frame index: largest, not
    at an edge)}]"""
    if not frames:
        raise KeyframeError(f"{TAG} nobody was found in the video. 视频里没找到人。")
    step = (frames[-1]["at"] - frames[0]["at"]) / (len(frames) - 1) if len(frames) > 1 else 1.0
    acc = {}
    for i, f in enumerate(frames):
        for pid, b in visible(f):
            if sliver(b):
                continue
            h = b[3] - b[1]
            a = acc.get(pid)
            if a is None:
                a = acc[pid] = {"id": pid, "first": f["at"], "x": (b[0] + b[2]) / 2, "last": f["at"], "n": 0,
                                "presence": 0.0, "best": i, "_k": (-1, 0.0)}
            a["last"], a["n"], a["presence"] = f["at"], a["n"] + 1, a["presence"] + h * step
            alone = all(iou(b, c) <= CLEAR_IOU and covered(b, c) <= COVERED and covered(c, b) <= COVERED
                        for q, c in visible(f, relaxed=True) if q != pid)
            k = (int(b[0] > 0.01 and b[2] < 0.99), int(alone), round(h, 2))
            if k > a["_k"]:
                a["_k"], a["best"] = k, i
    people = [a for a in acc.values() if a["n"] * step >= min_s - 1e-6]
    if not people:
        raise KeyframeError(f"{TAG} nobody is seen clearly for {min_s:.0f} s in the video. 视频里没有清楚出现的人。")
    # people who show up within CAST_TOGETHER_S of each other count as appearing together: left to right (a viewer
    # sees one opening shot with two men, not "the one detected 0.7 s earlier")
    people.sort(key=lambda a: a["first"])
    groups = []
    for a in people:
        if groups and a["first"] - groups[-1][0]["first"] <= CAST_TOGETHER_S:
            groups[-1].append(a)
        else:
            groups.append([a])
    people = [a for g in groups for a in sorted(g, key=lambda a: a["x"])]
    for a in people:
        a["seen"] = round(a["n"] * step, 2)
        a["side"] = side_of(a.pop("x"))
        a.pop("_k")
    return people


def auto_cast(people, pictures):
    """0.5.91 (user 01:52): picture 1 -> the first person to appear, picture 2 -> the next ...; a person, once given
    a picture, keeps it (the id follows him through the clip); people after the last picture stay themselves.
    -> {picture: person number}"""
    pics = sorted(pictures)
    if len(pics) > len(people):
        raise KeyframeError(f"{TAG} {len(pics)} pictures but only {len(people)} people are seen clearly in the clip. "
                            f"接了 {len(pics)} 张角色图，片子里只清楚出现 {len(people)} 个人。")
    return {p: n for n, p in enumerate(pics, 1)}


def resolve(mapping: dict, people: list) -> list:
    """{picture: person number} + cast(...) -> [(picture, person number, person id)]."""
    out = []
    for p, k in mapping.items():
        if k > len(people):
            raise KeyframeError(f"{TAG} only {len(people)} people are on the cast sheet, #{k} does not exist. "
                                f"演员表里只有 {len(people)} 个人，没有 {k} 号。")
        out.append((p, k, people[k - 1]["id"]))
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
    if frames[i].get("bad"):              # 0.5.91: a box spanning two people (clean_frames)
        return None
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


ID_MIN_H = 0.4           # 0.5.91: a person this tall (share of the frame height) or more is "large" - a mid shot
                         # (hangar clip: at 0.25 the face was ~50 px and Remake drew someone else in the close-ups)
ID_MAX_OVERLAP = 0.5     # ... and "clear" when no other box covers more than this of the smaller one
ID_MIN_APART = 0.5       # an added keyframe keeps this many seconds from the others


def clear_height(frame, pid):
    """height of person pid in this frame when he is seen whole in width (not at a side edge) and not mostly
    overlapped by someone else, else 0"""
    vis = visible(frame)
    b = next((bb for q, bb in vis if q == pid), None)
    if b is None or b[0] <= CLOSE_EDGE or b[2] >= 1 - CLOSE_EDGE:
        return 0.0
    if any(covered(b, c) > ID_MAX_OVERLAP for q, c in vis if q != pid):
        return 0.0
    return b[3] - b[1]


def ensure_identity(frames, moments, chosen, duration, min_h=ID_MIN_H, apart=ID_MIN_APART):
    """0.5.91 (user 01:08): skill 12d 'every replaced person needs at least one large, clear keyframe'.  For every
    chosen (picture, left, id) whose best keyframe shows him smaller than min_h (or not clear), add the frame where he
    is largest and clear - no close-up, at least `apart` s from the other keyframes; best: tallest, then most chosen
    people seen, then least motion.  Without it Remake had no face for him and drew someone else in the close-ups
    (hangar clip: picture 3 only small in keyframe 1 and a sliver in keyframe 2).
    -> (moments sorted by time, set of the added times (never thinned out), notes, warnings)"""
    out = sorted(moments, key=lambda m: frames[m[0]]["at"])
    ids = {pid for _, _, pid in chosen}
    last = duration - 2.0 / FPS
    added, notes, warns = set(), [], []
    for p, k, pid in chosen:
        have = max([clear_height(frames[i], pid) for i, kind in out if kind == "swap"] or [0.0])
        if have >= min_h:
            continue
        ts = [frames[i]["at"] for i, _ in out]
        cand = [i for i, f in enumerate(frames)
                if f["at"] <= last and clear_height(f, pid) > have and score(frames, i, ids) is not None
                and all(abs(f["at"] - t) >= apart for t in ts)]
        if not cand:
            warns.append(f"picture {p} (person #{k}) is never seen large and clear (best {have:.2f} of the frame height): "
                         "the video may not look like this character in close-ups. 图{0} 在原片里一直没有大而清楚的"
                         "镜头，近景可能不像。".format(p))
            continue
        best = max(cand, key=lambda i: (round(clear_height(frames[i], pid), 2), score(frames, i, ids)))
        out.append((best, "swap"))
        added.add(frames[best]["at"])
        h = clear_height(frames[best], pid)
        notes.append(f"picture {p} (person #{k}): keyframe added at {frames[best]['at']:.2f} s where he/she is large and "
                     f"clear ({h:.2f} of the frame height; the others showed {have:.2f})")
        if h < min_h:
            warns.append(f"picture {p} (person #{k}) is at most {h:.2f} of the frame height in the clip: close-ups may not "
                         "look like this character. 图{0} 在原片里最大也只有 {1:.2f} 画面高，近景可能不像。".format(p, h))
    return sorted(out, key=lambda m: frames[m[0]]["at"]), added, notes, warns


def finish_moments(frames, moments, chosen_ids, duration, max_gap=5.0, cuts=(), keep=()):
    """0.5.9 cross-check of the moment rules (picks every <= max_gap s, one per shot, none on close-ups):
    (1) no keyframe at all (only close-ups found) -> the best frame anyway, close-ups allowed - a part without any
        keyframe is an error;
    (0.5.91: the old step (2), thinning to 4 keyframes per estimated part, is gone - the parts depend on the GPU;
    the Director picks 4 per real part, h3md_remake_engine.pick_keyframes.)
    -> (moments sorted by time, notes)"""
    notes = []
    out = sorted(moments, key=lambda m: frames[m[0]]["at"])
    # 0.5.91 (_013: keyframes at 10.0 and 10.17 s): a keyframe closer than NEAR_S to another that shows the same
    # replaced people or fewer is a near copy - dropped (never a kept one)
    j = 0
    while j < len(out):
        i = out[j][0]
        twin = [m for m in out if m is not out[j] and abs(frames[m[0]]["at"] - frames[i]["at"]) < NEAR_S
                and kf_people(frames, i, chosen_ids) <= kf_people(frames, m[0], chosen_ids)]
        if twin and frames[i]["at"] not in set(keep):
            notes.append(f"keyframe at {frames[i]['at']:.2f} s dropped: a near copy of the one at "
                         f"{frames[twin[0][0]]['at']:.2f} s")
            out.pop(j)
            continue
        j += 1
    if not out:
        cand = [i for i, f in enumerate(frames) if any(w[0] in chosen_ids for w in f.get("who") or ())]
        if cand:
            best = max(cand, key=lambda i: (len([w for w in frames[i]["who"] if w[0] in chosen_ids]),
                                             -_motion(frames, i, frames[i]["who"][0][0])))
            out = [(best, "swap")]
            notes.append(f"only close-ups found: one keyframe at {frames[best]['at']:.2f} s anyway")
    return out, notes


def kf_people(frames, i, chosen_ids):
    """the chosen people a keyframe really shows (seen, not only a strip at the edge)"""
    return {pid for pid, b in visible(frames[i]) if pid in chosen_ids and not sliver(b)}


COVER_S = 3.0             # 0.5.91 (user 03:31): every replaced person on screen has a keyframe at least every this
                          # many seconds - the Director's parts depend on the GPU it runs on (token budget), and each
                          # part takes the look of a person only from its own keyframes
NEAR_S = 0.5              # 0.5.91: two keyframes closer than this showing the same people are one too many
COVER_APART = 0.75        # ... and an added keyframe is at least this far from the others (no near copies)
COVER_MIN_S = 0.5         # ... when he is seen for at least this long in that stretch (a short
                          # appearance in a part still gets his look from a keyframe)


def ensure_cover(frames, moments, chosen_ids, duration, every=COVER_S, min_s=COVER_MIN_S):
    """0.5.91: for every replaced person, every stretch of `every` seconds in which he is seen clearly for min_s or
    more holds a keyframe that shows him (a part of any length >= every then has one).  Greedy from the start: the
    first such stretch without one gets the frame in its first half where he is seen best (most replaced people,
    then tallest, no close-up), which covers up to `every` s further.  The Director picks 4 per part
    (h3md_remake_engine.pick_keyframes).  -> (moments sorted by time, notes, warnings)"""
    out = sorted(moments, key=lambda m: frames[m[0]]["at"])
    notes, warns = [], []
    step = (frames[-1]["at"] - frames[0]["at"]) / (len(frames) - 1) if len(frames) > 1 else 1.0
    last = duration - 2.0 / FPS
    half = every / 2.0
    for pid in sorted(chosen_ids):
        seen = [(f["at"], i) for i, f in enumerate(frames) if f["at"] <= last and pid in kf_people(frames, i, chosen_ids)]
        j = 0
        while j < len(seen):
            t = seen[j][0]
            if sum(1 for u, _ in seen if t <= u < t + every) * step < min_s - 1e-6:
                j += 1
                continue
            cov = [frames[i]["at"] for i, _ in out if pid in kf_people(frames, i, chosen_ids)
                   and abs(frames[i]["at"] - t) <= half]
            if cov:
                t_next = max(cov) + half
            else:
                cand = [i for u, i in seen if t <= u <= t + half and score(frames, i, chosen_ids) is not None
                        and all(abs(frames[i]["at"] - frames[m[0]]["at"]) >= COVER_APART for m in out)]
                if not cand:
                    warns.append(f"a replaced person seen around {t:.1f} s has no frame clear enough for a keyframe "
                                 f"there: Remake may give him another character's look. {t:.1f} 秒附近有一个要换的人"
                                 "没有合适的关键帧，可能换错。")
                    t_next = t + half
                else:
                    best = max(cand, key=lambda i: (len(kf_people(frames, i, chosen_ids)),
                                                    round(clear_height(frames[i], pid), 2)))
                    out.append((best, "swap"))
                    out.sort(key=lambda m: frames[m[0]]["at"])
                    notes.append(f"keyframe added at {frames[best]['at']:.2f} s: a replaced person seen around "
                                 f"{t:.1f} s had none within {half:.1f} s")
                    t_next = frames[best]["at"] + half
            while j < len(seen) and seen[j][0] <= t_next:
                j += 1
    return sorted(out, key=lambda m: frames[m[0]]["at"]), notes, warns


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
    (person #k, order of first appearance) is the person's ID; where she stands at a keyframe can differ."""
    cx = (own[0] + own[2]) / 2.0
    return 1 + sum(1 for b in others if (b[0] + b[2]) / 2.0 < cx), 1 + len(others)


def parse_people(text: str) -> list:
    """optional: one line per person of the cast sheet, #1 #2 ... (what they look like now) -> [str]"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return [re.sub(r"^(?:左|left|L)?\s*\d+\s*[:：.、)]\s*", "", l, flags=re.I).rstrip("。.") for l in lines]


# ----------------------------------------------- 0.5.91 Recast: the user's H3 Image AIO v1.01 chain (18:31)
# One plain sentence (the user's own wording, 18:28) -> the official MiniMax H3 Image prompt enhancer writes the
# prompt -> one H3 Image edit of the whole keyframe.  Test 18:27 (user, 0 s keyframe): all three swapped right, in
# place, pose kept, whole outfit, no extra people.  Our own long prompt template (17:32/17:47: people added instead
# of replaced) and the Qwen Image 2.1 path are gone.
SWAP_KEEP = ("严格保持图1中的对应人物的朝向,姿势,动作,手掌姿势,表情,口型,人物在图片里的位置和所占比例的大小,还有前后遮挡关系不变. "
             "严格保持{pics}里的人的五官外貌,发型发色,服装等特征不变,")


def h3_size(w, h, mp=EDIT_MP, grid=32):
    """the edit canvas as the AIO workflow's Resolution Selector makes it: the keyframe aspect at mp megapixels, each
    side rounded to a multiple of grid"""
    asp = w / float(max(1, h))
    return (max(grid, int(round(math.sqrt(mp * 1e6 * asp) / grid)) * grid),
            max(grid, int(round(math.sqrt(mp * 1e6 / asp) / grid)) * grid))


_CN_COUNT = "零一二三四五六七八九十"


def swap_sentence(pairs, n_people) -> str:
    """the user's plain request (21:32 wording) for the enhancer: pairs [(place from the left in this keyframe,
    picture number)], n_people in the keyframe.  Everybody recast: '把图1里从左到右的三个人，替换成图3、图4、图2里的人' -
    the pictures in the order the people stand now; only some: '从左数第2个人替换成图2里的人' + the others kept."""
    pairs = sorted(pairs)
    pics = "、".join(f"图{i}" for i in sorted(i for _, i in pairs))
    if [r for r, _ in pairs] == list(range(1, n_people + 1)):
        cnt = _CN_COUNT[n_people] if n_people < len(_CN_COUNT) else str(n_people)
        head = (f"把图1里从左到右的{cnt}个人，替换成" + "、".join(f"图{i}" for _, i in pairs) + "里的人，")
    else:
        head = ("把图1里" + "，".join(f"从左数第{r}个人替换成图{i}里的人" for r, i in pairs) + "，图1里其他的人保持不变，")
    return head + SWAP_KEEP.format(pics=pics)


def parse_redo(text, n):
    """'2' / '2,4' / '2 4' / '第2张' -> the keyframe numbers (1..n) to make again; empty -> None (all of them)"""
    nums = [int(x) for x in re.findall(r"\d+", str(text or ""))]
    sel = sorted({k for k in nums if 1 <= k <= n})
    if nums and not sel:
        raise KeyframeError(f"{TAG} redo: keyframes are numbered 1 to {n}. 重做的编号是 1 到 {n}。")
    return sel or None


def parse_prompts(text, n):
    """the AI's answer -> {keyframe number: prompt}: a JSON object {"1": "...", ...} (or {"prompts": {...}}), or
    lines '#1: ...' / '1: ...' (a prompt may run over several lines until the next number)"""
    import json
    t = str(text or "").strip()
    if not t:
        return {}
    out = {}
    try:
        d = json.loads(t)
        if isinstance(d, dict) and isinstance(d.get("prompts"), (dict, list)):
            d = d["prompts"]
        if isinstance(d, list):
            d = {str(i + 1): v for i, v in enumerate(d)}
        for k, v in d.items():
            m = re.search(r"\d+", str(k))
            if m and isinstance(v, str) and v.strip():
                out[int(m.group())] = v.strip()
    except ValueError:
        cur = None
        for line in t.splitlines():
            m = re.match(r"^\s*#?\s*(\d+)\s*[:：.)]\s*(.*)$", line)
            if m:
                cur = int(m.group(1))
                out[cur] = m.group(2).strip()
            elif cur is not None and line.strip():
                out[cur] = (out[cur] + " " + line.strip()).strip()
    bad = [k for k in out if not 1 <= k <= n]
    if bad:
        raise KeyframeError(f"{TAG} prompts: keyframes are numbered 1 to {n}, got {bad}. 提示词编号应为 1 到 {n}。")
    return {k: v for k, v in out.items() if v}


AI_README = """# H3MD keyframe package - write the H3 Image prompts

You get this package from the H3MD Keyframe Export node. Write one MiniMax H3 Image edit prompt per keyframe, so
that the people in each keyframe are replaced by the character pictures and nothing else changes.

## 1. Check the package first
Read request.json. The package must contain: request.json, keyframe_1.png ... keyframe_N.png (one per entry in
"keyframes"), every character_picture_*.png named in "pictures", and per keyframe a "sentence" and for every picture
"replaces_person_from_left" and a "description". "people_in_frame" must match the people you see in the keyframe.
If something is missing or does not match (a file, a description, the number of people, a person who is not
there), stop and tell the user exactly what is missing and how to fix it (run Export again, write the description,
...). Do not guess.

## 2. What the edit model gets
For keyframe k: <Picture 1> = keyframe_k.png (the original video frame), <Picture 2>, <Picture 3>, ... = the
character pictures listed for that keyframe in "pictures", in that order. People are counted from the left as they
stand in THIS keyframe (after a place change the order differs from the start - use replaces_person_from_left).
The picture numbers change from keyframe to keyframe - take them from that keyframe's "pictures".
"people_from_left" gives every person's box in % of the frame (left, top, right, bottom), the edges that cut them
and their role ("picture N" or "stays"): confirm every left/right, front/back and "peeks out on which side" by these
numbers before you write it.
H3 Image draws what the text says: the prompt decides the result. Read the keyframe carefully.

## 3. Write each prompt (English, the structure of the official H3 Image prompt enhancer)
  a. Define every character picture as a Subject and describe the person positively and completely, head to toe,
     from the picture and its "description": face, hair, every garment, shoes - and what they do NOT have (bare
     hands, no gloves). Never describe or mention the clothes of the people in <Picture 1>; never write what to
     remove. Use the same sentence for a character in every keyframe.
  b. Define <Picture 1> as a Subject that is partially preserved: background, floor, lighting, camera angle,
     framing and crop, the places, spacing and sizes of the people, who is in front, the overlaps.
  c. Body parts the frame cuts off are not mentioned (feet outside the picture: no shoes for that person) - else
     the model pulls the camera back. Say where the frame cuts each person. A person mostly outside the frame or
     almost hidden behind someone: write only the part that shows and that the rest is outside / hidden by whom,
     else the model draws all of him.
  d. For every replaced person, which Subject takes which place, and every one of these, read from <Picture 1>:
     - body orientation and how far it is turned; head turn and tilt (towards which side of the image), up/down;
       a clear tilt or lean must be written plainly, else it comes out straight;
     - EACH hand separately: where (above the head, beside the head, shoulder, chest, waist, thigh, behind the
       back, on someone), shape (fist, open, fingers spread or together, pointing, palms together, on the hip,
       hidden), which way the palm faces, the arm (straight or bent, up, forward, sideways);
     - legs: how much the knees bend, squat depth, which leg carries the weight, how far apart the feet are, where
       the toes point. Describe the still pose you see; never write a motion you guess from one frame (walking,
       stepping, jumping);
     - expression, eyes and mouth (open, shouting, closed smile, pout);
     - place and size in the frame; who is in front of / behind whom, which part of whom is hidden, who leans
       towards whom, standing apart or pressed together.
     Left and right always mean the sides of the image ("the hand on the left side of the image"), never the
     person's own left/right.
  e. People who are NOT replaced ("keeps_people_from_left" in request.json - any positions, different per keyframe,
     never assume they are the last ones): say how many people the frame holds and which positions stay, describe every
     kept person positively and completely as they look in <Picture 1> (face, hair, every garment, shoes, place, pose)
     and that they stay themselves - else H3 Image redraws them as copies of the new characters.
Write general rules for what to look at; describe only what this keyframe shows.

## 4. Check before you answer
Put each prompt next to its keyframe and check every person, item by item (orientation, both hands, legs, head,
expression, mouth, front/back, overlap, crop). The result must not differ visibly from the keyframe: a keyframe that
starts wrong (one fist instead of two, the wrong turn, the wrong overlap) cannot be pulled back later. If you are not
sure about something, say so instead of guessing.

## 5. Answer (the user imports it with one button - no copy and paste)
Best: send back this package as the file  {answer_file}  : the same zip, every file unchanged, with one file
added at the top: prompts.json = {{"package": "{package}", "prompts": {{"1": "prompt for keyframe 1", "2": "..."}}}}.
If you cannot make files: give the user the text of that prompts.json and tell them to save it as
{answer_json}  next to the export package.
Only redoing some keyframes: put only those numbers in "prompts"; the user picks the same numbers on the Redo node.
The folder _h3md/ is for the nodes - leave it as it is.
"""


def stamp_name(folder, name, kind, ext, today=None):
    """0.5.91 (user 22:52): '<name>_<kind>_<YYYYMMDD>_<NNN><ext>' in `folder`, NNN = next free number of that day
    for that name and kind (any extension), so a package is never overwritten and the export / import / plan of one
    run share date and number"""
    import datetime
    import os
    day = today or datetime.date.today().strftime("%Y%m%d")
    pre = f"{name}_{kind}_{day}_"
    used = []
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            m = re.match(re.escape(pre) + r"(\d{3})(?!\d)", f)
            if m:
                used.append(int(m.group(1)))
    return os.path.join(folder, f"{pre}{max(used, default=0) + 1:03d}{ext}")


def crop_to_aspect(w, h, tw, th):
    """the centred box (x0, y0, x1, y1) of a w x h picture with the aspect of tw x th (H3 Image's canvas is rounded
    to 32 px: crop the few extra pixels, then scale, instead of stretching)"""
    if w * th > tw * h:                      # too wide
        nw = int(round(h * tw / th))
        x0 = (w - nw) // 2
        return (x0, 0, x0 + nw, h)
    nh = int(round(w * th / tw))
    y0 = (h - nh) // 2
    return (0, y0, w, y0 + nh)


# 0.5.91 (user 23:57): a multi-swap plan's global prompt = the trigger words of the LMS LoRA (the only LoRA the
# multi-person swap style switches on; Civitai "LMS - A Little More Sharpness", trainedWords)
LMS_TRIGGER = ("Enhance this video with sharp, crisp details while preserving a natural photorealistic "
               "appearance.")


def plan_length(n_frames: int) -> int:
    """largest 5 + 17n not past the source's end (the Director would round UP and run past it)"""
    if n_frames < MIN_FRAMES:
        raise KeyframeError(f"{TAG} the video has {n_frames} frames; Remake needs at least {MIN_FRAMES} "
                            f"({MIN_FRAMES / FPS:.2f} s). 视频太短。")
    return 5 + 17 * ((n_frames - 5) // 17)


def ordinal(k: int) -> str:
    return ORDINAL[k - 1] if 1 <= k <= len(ORDINAL) else f"number {k}"


def swap_prompt(length, chosen, descs, n_people, changes, names, looks=None, people=None):
    """skill 12d step 6 (Remake wording).  chosen [(picture, person number, id)], descs {picture: text}, names
    {id: label} in cast order, looks {person number: what that person looks like} (optional), people = cast(...)
    (0.5.91: people are named by when and where they first appear, not by a place at the start)."""
    looks = looks or {}
    people = people or []

    def first_seen(k):
        if 1 <= k <= len(people):
            a = people[k - 1]
            return f"performer #{k}, who first appears at {a['first']:.1f} s on the {a['side']} of the frame"
        return f"performer #{k}"
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
        lines.append(f"<Subject {n}> is {descs[p].rstrip('.')}; this subject replaces {first_seen(k)}{orig}.")
    lines.append("In every frame each Subject has the face, hair and clothes described here, never those of the "
                 "performer they replace.")
    rest = n_people - len(chosen)
    kept = [looks[k] for k in range(1, n_people + 1) if looks.get(k) and k not in {c[1] for c in chosen}]
    kept_s = (", " + " and ".join(kept) + "," if kept and len(kept) == rest else "")
    keep = (f" The other {rest} performer{'s' if rest != 1 else ''}{kept_s} stay themselves, with their own faces, "
            "hair and outfits from <Video 1> in every frame.") if rest > 0 else ""
    mp = ", ".join(f"#{k} becomes <Subject {n}>" for n, (_, k, _) in enumerate(chosen, 1))
    lines += ["", "summary:",
              f"The target video is an edited version of <Video 1>. [video editing + reference generation + audio "
              f"reuse] Recast {len(chosen)} of the {n_people} performers with a fixed mapping the whole time, numbered "
              f"in order of first appearance: {mp}.{keep} Every recast person keeps the same replacement through "
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
              f"in <Video 1>, with the same lip movement. The performers, in order of first appearance: {order}.{moves} Each "
              "recast subject keeps their own face, hair and clothes the whole time, as in the redrawn frames. Their "
              "hands have five natural fingers each.",
              "", "overall_soundscape:", "Keep the source soundtrack; do not invent dialogue.",
              "", "non_diegetic_music:", "The music of <Video 1> plays unchanged as the soundtrack."]
    return "\n".join(lines)


def build_plan(video_file, duration_s, has_audio, size, length, keyframes, prompt, name):
    """keyframes [(media id, file, seconds[, replaced people shown])] -> the H3MD plan dict (swapEngine remake, one
    shot, autosplit)."""
    w, h = size
    keyframes = list(keyframes)
    return {"version": 1, "fps": FPS, "width": w, "height": h, "name": name, "globalPrompt": LMS_TRIGGER,
            "globalMode": "prepend", "refVideoHeight": "auto", "autoSplit": True, "swapEngine": "remake",
            "media": {"images": [{"id": kf[0], "file": kf[1]} for kf in keyframes],
                      "videos": [{"id": "src", "file": video_file, "duration": round(float(duration_s), 3),
                                  "hasAudio": bool(has_audio)}],
                      "audios": [], "refmods": []},
            "segments": [{"length": int(length), "overlapIn": 0, "images": [],
                          "videos": [{"id": "src", "trimStart": 0, "useSoundtrack": False, "depth": False,
                                      "useDuration": round(length / FPS, 4)}],
                          "audios": [], "audioMode": "locked" if has_audio else "none",
                          **({"locked": {"source": "video:src", "offset": 0}} if has_audio else {}),
                          "keyframes": [dict({"image": kf[0], "at": round(float(kf[2]), 4)},
                                             **({"people": list(kf[3])} if len(kf) > 3 else {}))
                                        for kf in keyframes],
                          "prompt": prompt}]}
