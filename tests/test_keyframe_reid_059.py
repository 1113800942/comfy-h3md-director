"""0.5.9: re-identification of people the tracker split (blackout / cut / turn)."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K

W, H = 160, 90
COL = {"blonde": (230, 200, 120), "red": (150, 30, 30), "black": (20, 20, 20),
       "lightblue": (150, 190, 240), "royal": (30, 50, 200), "navy": (20, 30, 90)}


def draw(people):
    """people: {id: (box, hair, body)} -> uint8 frame"""
    img = np.full((H, W, 3), 40, np.uint8)
    for pid, (b, hair, body) in people.items():
        x0, y0, x1, y1 = int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H)
        img[y0:y1, x0:x1] = COL[body]
        img[y0:y0 + max(2, (y1 - y0) // 5), x0:x1] = COL[hair]
    return img


L, C, R = (0.1, 0.2, 0.25, 0.95), (0.42, 0.2, 0.57, 0.95), (0.75, 0.2, 0.9, 0.95)
A, B, D = ("blonde", "lightblue"), ("red", "royal"), ("black", "navy")


def clip(rows):
    per = [{pid: v[0] for pid, v in r.items()} for r in rows]
    imgs = np.stack([draw({pid: (v[0],) + v[1] for pid, v in r.items()}) for r in rows])
    return per, imgs


def run(rows, looks=True):
    per, imgs = clip(rows)
    lk = K.appearance(imgs, per) if looks else None
    return K.reid_merge(per, lk, [i / 6 for i in range(len(per))])


def test_blackout_position_change_joined_by_elimination():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 10 + [{0: (L, A), 1: (C, B)}] * 2 \
         + [{0: (R, A), 1: (L, B), 7: (C, D)}] * 10       # D comes back in the centre with a new id
    out, joins, unsure = run(rows)
    assert [j["into"] for j in joins] == [2] and joins[0]["new"] == 7
    assert all(set(r) <= {0, 1, 2} for r in out) and 2 in out[-1]
    assert not unsure


def test_two_lost_told_apart_by_hair():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 8 + [{0: (L, A)}] * 2 \
         + [{0: (L, A), 5: (C, D), 6: (R, B)}] * 8         # B and D both lost, come back swapped
    out, joins, unsure = run(rows)
    got = {j["new"]: j["into"] for j in joins}
    assert got == {5: 2, 6: 1}, joins
    assert not unsure


def test_two_lost_look_alike_not_guessed():
    same = ("black", "navy")
    rows = [{0: (L, A), 1: (C, same), 2: (R, same)}] * 8 + [{0: (L, A)}] * 2 \
         + [{0: (L, A), 5: (C, same), 6: (R, same)}] * 8
    out, joins, unsure = run(rows)
    assert not joins and {u["new"] for u in unsure} == {5, 6}


def test_new_extra_person_not_joined():
    rows = [{0: (L, A), 1: (C, B)}] * 8 + [{0: (L, A), 1: (C, B), 9: (R, D)}] * 8
    out, joins, unsure = run(rows)
    assert not joins and not unsure and 9 in out[-1]


def test_seen_together_never_joined():
    # longer than a hand-over (REID_HANDOFF frames) side by side: two people, even if they look alike
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 6 + [{0: (L, A), 1: (C, B), 2: (R, D), 4: (C, D)}] * 14 \
         + [{0: (L, A), 1: (C, B), 4: (R, D)}] * 6
    out, joins, unsure = run(rows)
    assert not joins


def test_elimination_without_looks():
    rows = [{0: (L, A), 1: (R, D)}] * 6 + [{0: (L, A)}] * 2 + [{0: (L, A), 3: (C, D)}] * 6
    out, joins, unsure = run(rows, looks=False)
    assert joins and joins[0]["into"] == 1 and joins[0]["why"] == "elimination"


def test_ghost_overlap_allowed():
    rows = [{0: (L, A), 1: (R, D)}] * 6 + [{0: (L, A), 1: (R, D), 3: (C, D)}] * 2 + [{0: (L, A), 3: (C, D)}] * 6
    out, joins, unsure = run(rows)
    assert joins and joins[0]["into"] == 1
    assert out[7][1] == C                                   # newer track's box wins where both were seen


def test_prepare_keeps_people_at_start_separate():
    """11-19 bug: the track appearance overwrote `looks` (people_at_start), so the edit prompts got colour
    histograms as 'the original person' and the log said 'people_at_start has 5 line(s)'."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "h3md_keyframe_nodes.py"),
               encoding="utf-8").read()
    assert "track_looks = K.add_faces(K.appearance(" in src     # 0.5.91: + faces, still its own variable
    assert "\n            looks = K.appearance(" not in src


def test_handoff_double_box_joined():
    """11-19 real log: old track ran 6 frames beside the new one (a double box on the same dancer), then ended."""
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 10 + [{0: (L, A), 1: (C, B), 2: (R, D), 3: (R, D)}] * 6 \
         + [{0: (L, A), 1: (C, B), 3: (C, D)}] * 10
    out, joins, unsure = run(rows)
    assert joins and joins[0]["new"] == 3 and joins[0]["into"] == 2


def test_handoff_different_people_side_by_side_not_joined():
    rows = [{0: (L, A), 2: (R, D)}] * 10 + [{0: (L, A), 2: (R, D), 3: (C, B)}] * 6 + [{0: (L, A), 3: (C, B)}] * 10
    out, joins, unsure = run(rows)
    assert not joins


def test_handoff_uniform_group_not_joined():
    """everyone in the same outfit: a new dancer walking in while another leaves is not taken for her"""
    U = ("black", "navy")
    E_ = (0.0, 0.2, 0.08, 0.95)                             # walks in at the left edge while the right one walks out
    Rx = (0.93, 0.2, 1.0, 0.95)
    rows = [{0: (L, U), 1: (C, U), 2: (Rx, U)}] * 10 + [{0: (L, U), 1: (C, U), 2: (Rx, U), 3: (E_, U)}] * 6 \
         + [{0: (L, U), 1: (C, U), 3: (E_, U)}] * 10
    out, joins, unsure = run(rows)
    assert not joins



def _f(at, boxes):
    return {"at": at, "who": [[i, list(b)] for i, b in boxes.items()]}


def test_only_extreme_closeups_skipped():
    head_cut = {0: (0.05, 0.0, 0.35, 1.0), 1: (0.35, 0.0, 0.65, 1.0), 2: (0.65, 0.0, 0.95, 1.0)}
    waist_up = {0: (0.05, 0.15, 0.35, 1.0), 1: (0.35, 0.1, 0.65, 1.0), 2: (0.65, 0.15, 0.95, 1.0)}
    wide = {0: (0.1, 0.2, 0.25, 0.9), 1: (0.42, 0.2, 0.57, 0.9), 2: (0.75, 0.2, 0.9, 0.9)}
    ids = {0, 1, 2}
    assert K.closeup(_f(0, head_cut), ids)
    assert not K.closeup(_f(0, waist_up), ids) and not K.closeup(_f(0, wide), ids)
    frames = [_f(i / 6, head_cut if i / 6 < 3.0 else wide) for i in range(6 * 12)]
    m = K.ensure_per_shot(frames, K.pick_moments(frames, ids, 12.0, 5.0), ids, [3.0], 12.0)
    assert min(frames[i]["at"] for i, _ in m) >= 3.0


def test_cut_off_prompt():
    assert K.cut_at_bottom((0.1, 0.2, 0.3, 1.0)) and not K.cut_at_bottom((0.1, 0.2, 0.3, 0.9))


def test_finish_moments_caps_four_per_part_and_never_empty():
    wide = {0: (0.1, 0.2, 0.25, 0.9), 1: (0.42, 0.2, 0.57, 0.9)}
    frames = [_f(i / 6, wide) for i in range(6 * 16)]
    ms = [(i, "swap") for i in (6, 18, 30, 42, 54, 66)]           # 6 keyframes in 10 s
    out, notes = K.finish_moments(frames, ms, {0, 1}, 16.0)
    ts = [frames[i]["at"] for i, _ in out]
    import h3md_autosplit as A
    parts = A.plan_parts(16 * 24, 0, 345)
    for nudge in (-17, 0, 17):                                     # the Director may move the cut point one unit
        b = parts[0] + nudge
        assert len([t for t in ts if t * 24 < b]) <= 4 and len([t for t in ts if t * 24 >= b]) <= 4
    assert notes
    close = {0: (0.05, 0.0, 0.35, 1.0)}
    frames2 = [_f(i / 6, close) for i in range(30)]
    out2, notes2 = K.finish_moments(frames2, [], {0}, 5.0)
    assert len(out2) == 1 and notes2



def test_finish_moments_keeps_only_keyframe_of_a_shot():
    wide = {0: (0.1, 0.2, 0.25, 0.9), 1: (0.42, 0.2, 0.57, 0.9)}
    frames = [_f(i / 6, wide) for i in range(6 * 16)]
    ms = [(i, "swap") for i in (6, 12, 18, 24, 66)]             # 1, 2, 3, 4 s in shot A, 11 s in shot B (cut at 10 s)
    out, _ = K.finish_moments(frames, ms + [(30, "swap")], {0, 1}, 16.0, cuts=[4.5, 10.0])
    ts = [frames[i]["at"] for i, _ in out]
    assert 5.0 in ts                                             # the only keyframe of shot 4.5-10 s stays
    assert 11.0 in ts


def test_part_span_follows_the_director_split():
    # run 38: 396 frames are split 209 + 187, not into one 345-frame part
    assert abs(K.part_span(396 / 24) - (209 + 17) / 24) < 1e-6
    assert K.part_span(10.0) >= 10.0                               # one part: no thinning needed below 4


def test_partial_person_at_side_and_bottom_is_skipped():
    # run 38, 2.33 s: cut off at the left edge and the bottom -> shrunk + ghost of the original
    assert K.closeup(_f(0, {0: (0.0, 0.19, 0.16, 1.0), 1: (0.23, 0.3, 0.69, 1.0)}), {0, 1})
    # run 38, 0.33 s: waist-up, nobody at a side edge -> kept the size -> usable
    assert not K.closeup(_f(0, {0: (0.12, 0.19, 0.39, 1.0), 1: (0.35, 0.19, 0.66, 1.0)}), {0, 1})
    # full body at the side edge but feet inside the frame -> usable
    assert not K.closeup(_f(0, {0: (0.0, 0.3, 0.12, 0.9)}), {0})


def _shot_frames():
    close = {0: (0.1, 0.1, 0.3, 1.0), 1: (0.5, 0.1, 0.7, 1.0)}      # chest-up, cut by the bottom
    far = {0: (0.2, 0.3, 0.3, 1.0), 1: (0.5, 0.3, 0.6, 1.0)}        # farther, feet on the edge
    whole = {0: (0.2, 0.3, 0.3, 0.9), 1: (0.5, 0.3, 0.6, 0.9)}      # whole people
    return close, far, whole


def test_prefer_full_moves_within_the_shot():
    close, far, whole = _shot_frames()
    frames = [_f(i / 6, close if i < 30 else far) for i in range(6 * 8)]
    out, notes = K.prefer_full(frames, [(6, "swap")], {0, 1}, [], 8.0, 5.0)
    assert frames[out[0][0]]["at"] >= 5.0 and notes                 # moved to the farther frames
    frames = [_f(i / 6, close if i < 30 else whole) for i in range(6 * 8)]
    out, _ = K.prefer_full(frames, [(6, "swap")], {0, 1}, [], 8.0, 5.0)
    assert K.cut_at_bottom(frames[out[0][0]]["who"][0][1]) is False


def test_prefer_full_never_leaves_the_shot_or_breaks_the_gap():
    close, far, whole = _shot_frames()
    frames = [_f(i / 6, close if i < 30 else whole) for i in range(6 * 16)]
    out, notes = K.prefer_full(frames, [(6, "swap")], {0, 1}, [4.0], 16.0, 5.0)
    assert out == [(6, "swap")] and not notes                       # the whole frames are in the next shot
    frames = [_f(i / 6, close if i < 6 * 10 else whole) for i in range(6 * 16)]
    out, _ = K.prefer_full(frames, [(6, "swap"), (90, "swap")], {0, 1}, [], 16.0, 5.0)
    ts = [frames[i]["at"] for i, _ in out]
    pts = [0.0] + ts + [16.0]
    assert max(b - a for a, b in zip(pts, pts[1:])) <= 15.0         # never worse than before (1 -> 15 s was 14 s)


def test_prefer_full_does_not_cross_a_place_change():
    close, far, whole = _shot_frames()
    swapped = {0: whole[1], 1: whole[0]}
    frames = [_f(i / 6, close if i < 24 else swapped) for i in range(6 * 8)]
    out, notes = K.prefer_full(frames, [(6, "swap")], {0, 1}, [], 8.0, 5.0)
    assert out == [(6, "swap")] and not notes
