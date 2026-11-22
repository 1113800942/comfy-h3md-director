"""0.5.91 (user 01:31): five men in the same white clothes - faces decide who is who after a cut."""
import numpy as np
import h3md_keyframe as K


def test_assign_face_to_the_head_of_its_box():
    boxes = {1: [0.1, 0.2, 0.3, 0.9], 2: [0.25, 0.1, 0.45, 0.95]}
    assert K.assign_face((0.15, 0.22, 0.2, 0.28), boxes) == 1        # head of person 1
    assert K.assign_face((0.3, 0.12, 0.36, 0.2), boxes) == 2         # head of person 2 (overlapping boxes)
    assert K.assign_face((0.15, 0.7, 0.2, 0.76), boxes) is None       # a face at the feet: nobody's head


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


