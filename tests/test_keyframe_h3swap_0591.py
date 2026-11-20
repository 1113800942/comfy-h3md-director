"""0.5.91: keyframes by MiniMax H3 Image character swap on the whole frame - the automatic checks."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402

W, H = 400, 200
A, B, C = [20, 20, 100, 190], [160, 20, 240, 190], [300, 20, 380, 190]


def scene(cols):
    img = np.full((H, W, 3), 128, np.uint8)
    for box, col in zip((A, B, C), cols):
        img[box[1]:box[3], box[0]:box[2]] = col
    return img


RED, GREEN, BLUE, YELLOW = (200, 40, 40), (40, 200, 40), (40, 40, 200), (230, 210, 30)


def test_frame_rank():
    assert K.frame_rank(B, [A, C]) == (2, 3)
    assert K.frame_rank(A, []) == (1, 1)


def test_check_swap_right_person():
    before, after = scene([RED, GREEN, BLUE]), scene([RED, YELLOW, BLUE])
    ok, t, sims = K.check_swap(before, after, B, [A, C])
    assert ok and t < K.SWAP_CHANGED and min(sims) > 0.95


def test_check_swap_wrong_person_is_caught():
    before, after = scene([RED, GREEN, BLUE]), scene([YELLOW, GREEN, BLUE])   # asked for B, A was changed
    ok, t, sims = K.check_swap(before, after, B, [A, C])
    assert not ok and t > K.SWAP_CHANGED


def test_check_swap_nothing_changed_is_caught():
    before = scene([RED, GREEN, BLUE])
    assert not K.check_swap(before, before.copy(), B, [A, C])[0]


def test_check_layout():
    n = lambda b: [b[0] / W, b[1] / H, b[2] / W, b[3] / H]
    src = [n(A), n(B), n(C)]
    assert K.check_layout(src, src) == []
    small = n([170, 100, 230, 190])                       # shrunk to about half
    probs = K.check_layout(src, [n(A), small, n(C)])
    assert probs and "left 2" in probs[0]
    assert "2 people found" in K.check_layout(src, [n(A), n(C)])[0]
