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
