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
