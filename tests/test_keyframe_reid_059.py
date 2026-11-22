"""0.5.9: re-identification of people the tracker split (blackout / cut / turn)."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K

W, H = 160, 90
COL = {"blonde": (230, 200, 120), "red": (150, 30, 30), "black": (20, 20, 20),
       "lightblue": (150, 190, 240), "royal": (30, 50, 200), "navy": (20, 30, 90)}


L, C, R = (0.1, 0.2, 0.25, 0.95), (0.42, 0.2, 0.57, 0.95), (0.75, 0.2, 0.9, 0.95)
A, B, D = ("blonde", "lightblue"), ("red", "royal"), ("black", "navy")


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


def test_finish_moments_never_empty():
    """(0.5.91: the cap of 4 per part moved to the Director, h3md_remake_engine.pick_keyframes)"""
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
