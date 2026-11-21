"""0.5.91 (user 01:31): five men in the same white clothes - faces decide who is who after a cut."""
import numpy as np
import h3md_keyframe as K


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def test_face_decides_over_same_clothes():
    white = np.zeros(114)
    white[110] = 1.0                                  # everybody: the same white grey-level histogram
    a, b, c = unit([1, 0.1, 0]), unit([0.95, 0.2, 0.05]), unit([0, 1, 0])
    same = K.look_similarity((white, white, a), (white, white, b))
    other = K.look_similarity((white, white, a), (white, white, c))
    assert same > 0.9 and other == 0.0
    assert K.look_similarity((white, white, None), (white, white, None)) > 0.99   # colours alone: no difference


def test_add_faces_and_missing_colour():
    looks = K.add_faces({1: (np.ones(3), np.ones(3))}, {1: unit([1, 0]), 2: unit([0, 1])})
    assert looks[1][2] is not None and looks[2][0] is None
    assert K.look_similarity(looks[2], (np.ones(3), np.ones(3), None)) == 0.0


def test_assign_face_to_the_head_of_its_box():
    boxes = {1: [0.1, 0.2, 0.3, 0.9], 2: [0.25, 0.1, 0.45, 0.95]}
    assert K.assign_face((0.15, 0.22, 0.2, 0.28), boxes) == 1        # head of person 1
    assert K.assign_face((0.3, 0.12, 0.36, 0.2), boxes) == 2         # head of person 2 (overlapping boxes)
    assert K.assign_face((0.15, 0.7, 0.2, 0.76), boxes) is None       # a face at the feet: nobody's head


def test_new_person_walking_in_is_not_a_warning():
    """2026-11-21 _004: three new men walked in at 3 s; their faces matched nobody, yet each was reported as 'a person
    came back who could be any of tracks [1, 0]'.  A face unlike every candidate = a new person: no warning."""
    import numpy as np
    e = np.eye(4, dtype=np.float32)
    per_frame = []
    for i in range(20):
        fr = {}
        if i < 10:
            fr[0] = [0.1, 0.1, 0.3, 0.9]
            fr[1] = [0.4, 0.1, 0.6, 0.9]
        if i >= 5:
            fr[2] = [0.85, 0.1, 1.0, 0.9]
        per_frame.append(fr)
    white = np.ones(3) / 3
    looks = {0: (white, white, e[0]), 1: (white, white, e[1]), 2: (white, white, e[2])}
    out, joins, unsure = K.reid_merge(per_frame, looks, list(range(20)))
    assert joins == [] and unsure == []
    # without faces it stays unsure, as before
    looks2 = {q: (white, white, None) for q in looks}
    _, joins2, unsure2 = K.reid_merge(per_frame, looks2, list(range(20)))
    assert joins2 or unsure2


def _emb(person, rng, noise=0.25):
    import numpy as np
    base = np.zeros(32, np.float32)
    base[person] = 1.0
    v = base + noise * rng.standard_normal(32).astype(np.float32) / np.sqrt(32)
    return v / np.linalg.norm(v)


def test_face_clusters_people():
    import numpy as np
    rng = np.random.default_rng(0)
    embs = [_emb(p, rng) for p in (0, 1, 2) for _ in range(10)] + [_emb(7, rng)]     # one stray face
    labels, cents = K.face_clusters(embs)
    assert len(cents) == 3
    assert len({labels[i] for i in range(10)}) == 1 and labels[0] != labels[10] != labels[20]
    assert labels[-1] == -1


def test_split_by_face_number_passed_on_at_a_crossing():
    """2026-11-21 _004: at a crossing the tracker swapped the numbers of two men (track 3: Howie, Nick, Howie; track 4:
    Nick, Howie, Nick).  After the split each man is one id the whole time; a single stray face does not cut."""
    import numpy as np
    rng = np.random.default_rng(1)
    L, R = [0.1, 0.1, 0.3, 0.9], [0.5, 0.1, 0.7, 0.9]
    per_frame = []
    obs = []
    for i in range(30):
        swapped = 10 <= i < 20
        per_frame.append({3: L, 4: R})
        obs.append((i, 3, _emb(1 if swapped else 0, rng)))      # 0 = Howie, 1 = Nick
        obs.append((i, 4, _emb(0 if swapped else 1, rng)))
    obs.append((25, 4, _emb(2, rng)))                           # stray face, a third man, once
    labels, cents = K.face_clusters([o[2] for o in obs])
    new, person_of, notes = K.split_by_face(per_frame, obs, labels)
    howie = [q for q, v in new[0].items() if v == L][0]
    nick = [q for q, v in new[0].items() if v == R][0]
    assert howie != nick and howie >= K.FACE_ID and nick >= K.FACE_ID
    assert new[15][nick] == L and new[15][howie] == R            # the numbers follow the faces, not the track
    assert new[29][nick] == R and new[25][nick] == R             # the stray face did not cut track 4
    assert all(set(fr) == {howie, nick} for fr in new)
    assert len(notes) == 2


def test_odd_angle_cluster_on_one_track_is_no_new_person():
    """_012 (24.2-26.5 s): the lead singer turning away gave faces that formed a 6th 'person' seen on his track only -
    no cut there, he stays himself"""
    import numpy as np
    rng = np.random.default_rng(3)
    per_frame = [{4: [0.3, 0.1, 0.6, 0.95], 1: [0.7, 0.1, 0.9, 0.95]} for _ in range(40)]
    obs = []
    for i in range(40):
        obs.append((i, 4, _emb(5 if 15 <= i < 25 else 4, rng)))     # 5 = odd angle, only on track 4
        obs.append((i, 1, _emb(1, rng)))
    labels, cents = K.face_clusters([o[2] for o in obs])
    new, person_of, notes = K.split_by_face(per_frame, obs, labels)
    lead = [q for q, v in new[0].items() if v == [0.3, 0.1, 0.6, 0.95]][0]
    assert all(fr.get(lead) == [0.3, 0.1, 0.6, 0.95] for fr in new) and not notes


def test_same_box_twice_is_one_detection():
    """_012 (8.5 s): one person twice with the same box after the split - one copy, no phantom 'kept' person"""
    import numpy as np
    rng = np.random.default_rng(4)
    B = [0.3, 0.1, 0.6, 0.95]
    per_frame = [{4: B, 9: list(B)} for _ in range(10)]
    obs = [(i, 4, _emb(4, rng)) for i in range(10)] + [(i, 9, _emb(4, rng)) for i in range(10)]
    labels, cents = K.face_clusters([o[2] for o in obs])
    new, person_of, notes = K.split_by_face(per_frame, obs, labels)
    assert all(len(fr) == 1 for fr in new)


def test_clean_frames_wide_and_hidden_boxes():
    fr = []
    for i in range(20):
        who = [[1, [0.30, 0.10, 0.50, 0.95]], [2, [0.60, 0.20, 0.75, 0.90]]]
        if i == 10:
            who[1] = [2, [0.20, 0.20, 0.80, 0.90]]                 # spans two people
        if i == 12:
            who.append([3, [0.35, 0.30, 0.42, 0.50]])               # hidden behind 1
        fr.append({"at": i / 6.0, "who": who})
    out, notes = K.clean_frames(fr)
    assert out[10].get("bad") == [2] and K.score(out, 10, {1, 2}) is None
    assert all(w[0] != 3 for w in out[12]["who"])
