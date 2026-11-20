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
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 6 + [{0: (L, A), 1: (C, B), 2: (R, D), 4: (C, D)}] * 4 \
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
    assert "track_looks = K.appearance(" in src
    assert "\n            looks = K.appearance(" not in src
