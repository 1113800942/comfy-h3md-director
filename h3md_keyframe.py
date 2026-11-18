"""H3MD Keyframe - pure logic for the multi-person swap keyframes (2026-11-17, H3MD 0.5.0).

The two nodes in h3md_keyframe_nodes.py wrap this file:
  1 Prepare: who is who (numbered left to right at the start), which moments become keyframes, and for every person to
    recast at every moment a crop + an edit prompt for ANY image-edit model (Qwen Image 2.1, Flux, ...).
  2 Assemble: the edited crops are colour-matched and pasted back, and the Remake plan is written.

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
PAD_X, PAD_TOP, PAD_BOTTOM = 0.45, 0.12, 0.08   # skill 12d step 4: crop = person box + 45 % left/right, 12 % up, 8 % down
GEN_MP = 1.6              # edit size (megapixels), multiples of 32
SPACING = 0.7            # keyframes are picked in (prev + 0.7 * max_gap, prev + max_gap]: as few as the gap allows
FEATHER = 0.08            # paste feather, share of the crop width
PROTECT_PAD = 0.06        # other people's boxes grown by this (share of their size) are kept from the source

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


def score(frames, i, chosen_ids):
    """(chosen people seen, smallest gap of a chosen person to anybody else (capped), -motion, mean height) or None"""
    vis = visible(frames[i])
    ch = [w for w in vis if w[0] in chosen_ids]
    if not ch:
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
            if present:
                best = max(present, key=lambda i: max(w[1][3] - w[1][1] for w in frames[i]["who"]
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


def crop_box(b, W, H):
    """person box in pixels -> crop box in pixels (skill 12d step 4): +45 % left/right, +12 % up, +8 % down,
    at least 1:2 wide, clipped to the frame"""
    x0, y0, x1, y1 = b
    w, h = x1 - x0, y1 - y0
    cx0, cx1 = x0 - PAD_X * w, x1 + PAD_X * w
    cy0, cy1 = y0 - PAD_TOP * h, y1 + PAD_BOTTOM * h
    cw = cx1 - cx0
    if (cy1 - cy0) > 2 * cw:
        pad = ((cy1 - cy0) / 2 - cw) / 2
        cx0, cx1 = cx0 - pad, cx1 + pad
    return [int(max(0, math.floor(cx0))), int(max(0, math.floor(cy0))),
            int(min(W, math.ceil(cx1))), int(min(H, math.ceil(cy1)))]


def gen_size(w, h, mp=GEN_MP, grid=32):
    s = math.sqrt(mp * 1e6 / max(1, w * h))
    return max(grid, round(w * s / grid) * grid), max(grid, round(h * s / grid) * grid)


def parse_people(text: str) -> list:
    """optional: one line per person at the start, left to right (what they look like now) -> [str]"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    return [re.sub(r"^(?:左|left|L)?\s*\d+\s*[:：.、)]\s*", "", l, flags=re.I).rstrip("。.") for l in lines]


def edit_prompt(description: str, lang: str = "zh", original: str = "") -> str:
    """skill 12d step 4 template (line A: the crop is a part of a photo, the person to replace is in the middle).
    original: what the person to replace looks like (optional; helps in crowded crops)."""
    if lang == "en":
        who = f"the person in the middle of image 1 ({original})" if original else "the person in the middle of image 1"
        return (f"Image 1 is a part of a photo. Replace {who} with the person from "
                f"image 2: {description}. The clothes become entirely the clothes of the person in image 2; nothing "
                "of the original person's clothes stays. Keep the original pose, action, body direction, position, "
                "size and any covered parts exactly the same; everybody else, their body parts and the background "
                "in image 1 stay completely unchanged, with the same composition as image 1. No beautifying, no "
                "colour grading, no filter: a realistic photo exactly like image 1.")
    who = f"图1正中间的那个人（{original}）" if original else "图1正中间的那个人"
    return (f"图1是一张照片的局部，把{who}换成图2中的人：{description}。衣服完全换成图2里那个人的衣服，"
            "原来那个人的衣服一件都不留。保持原来的姿势、动作、身体朝向、在图中的位置、大小和被遮挡的部分不变；"
            "图1里其他人、其他人的身体部分和背景完全不变，画面构图和图1一样。不要美化，不要调色，不加滤镜，"
            "是和图1一样的写实照片。")


# ---------------------------------------------------------------------------------------------------- paste
def colour_match(edit, orig, inner):
    """per-channel mean/std of the ring outside the person box (inner, crop pixels) matched to the source"""
    import numpy as np
    m = np.ones(orig.shape[:2], bool)
    x0, y0, x1, y1 = [int(v) for v in inner]
    m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = False
    if m.sum() < 64:
        return edit.astype(np.float32)
    e, o = edit.astype(np.float32), orig.astype(np.float32)
    for c in range(3):
        em, es = e[..., c][m].mean(), e[..., c][m].std() + 1e-6
        om, osd = o[..., c][m].mean(), o[..., c][m].std() + 1e-6
        e[..., c] = (e[..., c] - em) * (osd / es) + om
    return np.clip(e, 0, 255)


def paste_alpha(crop, W, H, own, others):
    """alpha [h, w] for pasting an edited crop: linear feather on the sides inside the frame, and 0 (feathered) over
    every OTHER person's box (grown by PROTECT_PAD) except where it overlaps this person's own box - so the people
    around keep their source pixels and an earlier pasted recast stays."""
    import numpy as np
    x0, y0, x1, y1 = crop
    w, h = x1 - x0, y1 - y0
    a = np.ones((h, w), np.float32)
    f = max(2, int(FEATHER * w))
    r = np.linspace(0, 1, f + 2, dtype=np.float32)[1:-1]
    if x0 > 0:
        a[:, :f] *= r[None]
    if x1 < W:
        a[:, w - f:] *= r[::-1][None]
    if y0 > 0:
        a[:f, :] *= r[:, None]
    if y1 < H:
        a[h - f:, :] *= r[::-1][:, None]
    keep = np.zeros((h, w), np.float32)
    for b in others:
        bw, bh = b[2] - b[0], b[3] - b[1]
        px, py = PROTECT_PAD * bw, PROTECT_PAD * bh
        bx0, by0 = int(max(x0, b[0] - px)) - x0, int(max(y0, b[1] - py)) - y0
        bx1, by1 = int(min(x1, b[2] + px)) - x0, int(min(y1, b[3] + py)) - y0
        if bx1 > bx0 and by1 > by0:
            keep[by0:by1, bx0:bx1] = 1.0
    if keep.any():
        ox0, oy0 = int(max(x0, own[0])) - x0, int(max(y0, own[1])) - y0
        ox1, oy1 = int(min(x1, own[2])) - x0, int(min(y1, own[3])) - y0
        if ox1 > ox0 and oy1 > oy0:
            keep[oy0:oy1, ox0:ox1] = 0.0
        k = max(1, f // 2)                      # soften the protected edges a little
        from numpy.lib.stride_tricks import sliding_window_view as swv
        pad = np.pad(keep, k, mode="edge")
        keep = swv(pad, (2 * k + 1, 2 * k + 1)).mean(axis=(-1, -2)).astype(np.float32)
        a *= (1.0 - keep)
    return a


def paste(full, patch, crop, alpha):
    import numpy as np
    x0, y0, x1, y1 = crop
    reg = full[y0:y1, x0:x1].astype(np.float32)
    full[y0:y1, x0:x1] = np.clip(patch * alpha[..., None] + reg * (1 - alpha[..., None]), 0, 255).astype(np.uint8)
    return full


# ---------------------------------------------------------------------------------------------------- plan
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
