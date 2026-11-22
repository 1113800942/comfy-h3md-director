"""0.5.91 (user 16:11): who is who - K.identify (tracklets, cues learned from the clip, constrained grouping).
Carries over every case of the 0.5.9 re-identification and the 0.5.91 face split, plus H3MD_00154."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K

W, H = 160, 90
COL = {"blonde": (230, 200, 120), "red": (150, 30, 30), "black": (20, 20, 20), "white": (235, 235, 235),
       "lightblue": (150, 190, 240), "royal": (30, 50, 200), "navy": (20, 30, 90)}
L, C, R = (0.1, 0.2, 0.25, 0.95), (0.42, 0.2, 0.57, 0.95), (0.75, 0.2, 0.9, 0.95)
A, B, D = ("blonde", "lightblue"), ("red", "royal"), ("black", "navy")
U = ("white", "white")


def draw(people):
    img = np.full((H, W, 3), 40, np.uint8)
    for pid, (b, hair, body) in people.items():
        x0, y0, x1, y1 = int(b[0] * W), int(b[1] * H), int(b[2] * W), int(b[3] * H)
        img[y0:y1, x0:x1] = COL[body]
        img[y0:y0 + max(2, (y1 - y0) // 5), x0:x1] = COL[hair]
    return img


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def emb(person, rng, noise=0.25):
    base = np.zeros(32, np.float32)
    base[person] = 1.0
    return unit(base + noise * rng.standard_normal(32).astype(np.float32) / np.sqrt(32))


def run(rows, looks=True, faces=None, cuts=()):
    """rows: [{track id: (box, (hair, body)[, face person])}]"""
    per = [{pid: v[0] for pid, v in r.items()} for r in rows]
    lk = None
    if looks:
        imgs = np.stack([draw({pid: (v[0],) + v[1] for pid, v in r.items()}) for r in rows])
        lk = K.look_samples(imgs, per)
    if faces is None:
        rng = np.random.default_rng(0)
        faces = {(i, pid): (emb(v[2], rng), 80) for i, r in enumerate(rows) for pid, v in r.items() if len(v) > 2}
    return K.identify(per, [i / 6 for i in range(len(rows))], cuts, faces, lk)


def who(out, i, box):
    return next(q for q, b in out[i].items() if tuple(b) == tuple(box))


def test_blackout_position_change_joined():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 10 + [{0: (L, A), 1: (C, B)}] * 2 \
         + [{0: (R, A), 1: (L, B), 7: (C, D)}] * 10       # D comes back in the centre with a new tracker id
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, R) == who(out, -1, C)                # D is one person
    assert len({q for fr in out for q in fr}) == 3 and not unsure


def test_two_lost_told_apart_by_hair():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 8 + [{0: (L, A)}] * 2 \
         + [{0: (L, A), 5: (C, D), 6: (R, B)}] * 8         # B and D both lost, come back swapped
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, R) == who(out, -1, C) and who(out, 0, C) == who(out, -1, R)


def test_two_lost_look_alike_not_guessed():
    same = ("black", "navy")
    rows = [{0: (L, A), 1: (C, same), 2: (R, same)}] * 8 + [{0: (L, A)}] * 2 \
         + [{0: (L, A), 5: (C, same), 6: (R, same)}] * 8
    out, joins, unsure, _ = run(rows)
    start = {who(out, 0, C), who(out, 0, R)}
    assert not ({who(out, -1, C), who(out, -1, R)} & start)   # nothing guessed
    assert unsure


def test_new_extra_person_not_joined():
    rows = [{0: (L, A), 1: (C, B)}] * 8 + [{0: (L, A), 1: (C, B), 9: (R, D)}] * 8
    out, joins, unsure, _ = run(rows)
    assert len({q for fr in out for q in fr}) == 3 and not unsure


def test_seen_together_never_joined():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 6 + [{0: (L, A), 1: (C, B), 2: (R, D), 4: (C, D)}] * 14 \
         + [{0: (L, A), 1: (C, B), 4: (R, D)}] * 6
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, R) != who(out, -1, R)


def test_elimination_without_looks():
    rows = [{0: (L, A), 1: (R, D)}] * 6 + [{0: (L, A)}] * 2 + [{0: (L, A), 3: (C, D)}] * 6
    out, joins, unsure, _ = run(rows, looks=False, faces={})
    assert who(out, 0, R) == who(out, -1, C)
    assert joins and joins[0]["why"] == "elimination"


def test_ghost_overlap_allowed():
    rows = [{0: (L, A), 1: (R, D)}] * 6 + [{0: (L, A), 1: (R, D), 3: (C, D)}] * 2 + [{0: (L, A), 3: (C, D)}] * 6
    out, joins, unsure, _ = run(rows)
    p = who(out, 0, R)
    assert p == who(out, -1, C)
    assert tuple(out[7][p]) == C                            # the newer track's box wins where both were seen


def test_handoff_double_box_joined():
    rows = [{0: (L, A), 1: (C, B), 2: (R, D)}] * 10 + [{0: (L, A), 1: (C, B), 2: (R, D), 3: (R, D)}] * 6 \
         + [{0: (L, A), 1: (C, B), 3: (R, D)}] * 10      # the old track ends, the new one goes on
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, R) == who(out, -1, R)
    assert all(len(fr) == 3 for fr in out)                  # one box per person


def test_handoff_different_people_side_by_side_not_joined():
    rows = [{0: (L, A), 2: (R, D)}] * 10 + [{0: (L, A), 2: (R, D), 3: (C, B)}] * 6 + [{0: (L, A), 3: (C, B)}] * 10
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, R) != who(out, -1, C)


def test_handoff_uniform_group_not_joined():
    E_, Rx = (0.0, 0.2, 0.08, 0.95), (0.93, 0.2, 1.0, 0.95)
    rows = [{0: (L, U), 1: (C, U), 2: (Rx, U)}] * 10 + [{0: (L, U), 1: (C, U), 2: (Rx, U), 3: (E_, U)}] * 6 \
         + [{0: (L, U), 1: (C, U), 3: (E_, U)}] * 10
    out, joins, unsure, _ = run(rows)
    assert who(out, 0, Rx) != who(out, -1, E_)


def test_number_passed_on_at_a_crossing_follows_the_faces():
    """_004: at a crossing the tracker swapped two men's numbers (same white clothes): the people follow the faces;
    one stray face does not cut"""
    rows = []
    for i in range(30):
        sw = 10 <= i < 20
        rows.append({3: (L, U, 1 if sw else 0), 4: (R, U, 0 if sw else 1)})
    rng = np.random.default_rng(1)
    faces = {(i, pid): (emb(v[2], rng), 80) for i, r in enumerate(rows) for pid, v in r.items()}
    faces[(25, 4)] = (emb(2, rng), 80)                      # a stray face, once
    out, joins, unsure, notes = run(rows, faces=faces)
    howie = who(out, 0, L)
    assert who(out, 15, R) == howie and who(out, 29, L) == howie and who(out, 25, L) == howie
    assert len({q for fr in out for q in fr}) == 2


def test_odd_angle_on_one_track_is_no_new_person():
    """_012: the lead singer turning away - faces unlike anyone for 10 frames on his own track: still him"""
    rows = [{4: (C, U, 9 if 15 <= i < 25 else 4), 1: (R, U, 1)} for i in range(40)]
    out, joins, unsure, _ = run(rows)
    lead = who(out, 0, C)
    assert all(who(out, i, C) == lead for i in range(40))


def test_same_box_twice_is_one_detection():
    rows = [{4: (C, U, 4), 9: (C, U, 4)} for _ in range(10)]
    out, joins, unsure, _ = run(rows)
    assert all(len(fr) == 1 for fr in out)


def test_back_turned_piece_fills_a_gap_of_his_track():
    rng = np.random.default_rng(5)
    nick, other = emb(4, rng), emb(1, rng)
    odd = unit(0.8 * nick + 0.6 * emb(9, rng))
    rows, faces = [], {}
    for i in range(60):
        r = {1: (R, U)}
        if 20 <= i < 35:
            r[7] = (C, U)
            faces[(i, 7)] = (odd, 80)
        else:
            r[4] = (C, U)
            faces[(i, 4)] = (nick, 80)
        faces[(i, 1)] = (other, 80)
        rows.append(r)
    out, joins, unsure, _ = run(rows, faces=faces)
    assert who(out, 0, C) == who(out, 25, C) == who(out, 59, C)


def test_new_person_walking_in_is_not_a_warning():
    rows = []
    for i in range(20):
        r = {}
        if i < 10:
            r[0], r[1] = (L, U, 0), (C, U, 1)
        if i >= 5:
            r[2] = ((0.75, 0.2, 0.95, 0.95), U, 2)
        rows.append(r)
    out, joins, unsure, _ = run(rows)
    assert not unsure and len({q for fr in out for q in fr}) == 3


def test_ids_jump_at_a_cut_faces_alike_clothes_differ():
    """H3MD_00154: after a cut the tracker gave the woman on the right the left woman's number; their faces barely
    differ (AI-generated video), their dresses do - the people follow the dresses"""
    rng = np.random.default_rng(7)
    rows, faces = [], {}
    for i in range(40):
        cut = i >= 20
        r = {0: (L, B if cut else A), 1: (R, A if cut else B), 2: (C, D)}
        rows.append(r)
        for pid in r:
            faces[(i, pid)] = (emb(0, rng, noise=1.2), 80)  # everybody's face about the same
    out, joins, unsure, notes = run(rows, faces=faces, cuts=[20 / 6])
    assert who(out, 0, L) == who(out, -1, R) and who(out, 0, R) == who(out, -1, L)
    assert len({q for fr in out for q in fr}) == 3


def test_prepare_uses_identify_with_its_own_variable():
    """11-19 bug: the track colours overwrote `looks` (people_at_start); 0.5.91: identify replaces the old re-id"""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "h3md_keyframe_nodes.py"),
               encoding="utf-8").read()
    assert "det_looks = K.look_samples(" in src and "K.identify(" in src
    assert "\n            looks = " not in src
    for old in ("reid_merge", "split_by_face", "face_clusters", "add_faces"):
        assert f"K.{old}(" not in src
