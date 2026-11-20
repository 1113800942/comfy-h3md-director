"""0.5.91: keyframes by MiniMax H3 Image character swap, everybody in one edit, redrawn only inside the people's
SAM3 masks (grown a little, soft edge), canvas = the keyframe's size."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402


def test_frame_rank():
    assert K.frame_rank([160, 20, 240, 190], [[20, 20, 100, 190], [300, 20, 380, 190]]) == (2, 3)
    assert K.frame_rank([0, 0, 5, 5], []) == (1, 1)


def test_canvas_size():
    assert K.canvas_size(1664, 960) == (1664, 960) and K.canvas_size(1000, 570) == (992, 576)


def test_instruction_names_everybody_by_place_now():
    t = K.swap_instruction([{"rank": 3, "ref": 3, "desc": "B", "cut_bottom": True},
                            {"rank": 1, "ref": 2, "desc": "A", "look": "the man in grey"}])
    assert t.index("first from the left (the man in grey)") < t.index("third from the left")
    assert "<Picture 2> (A)" in t and "<Picture 3> (B)" in t and t.count("bottom edge") == 1
    assert "same pose, position, scale and gaze" in t and "camera framing" in t
    for w in ("glove", "sleeve", "stocking", "hand"):          # user rule after runs 40/41: no item list
        assert w not in t.lower()


def test_grow_and_pack():
    m = np.zeros((20, 30), bool)
    m[10, 15] = True
    g = K.grow_mask(m, 3)
    assert g.sum() == 49 and g[7, 12] and g[13, 18] and not g[6, 15] and not g[10, 19]
    assert (K.unpack_mask(K.pack_mask(g)) == g).all()
    e = np.zeros((5, 5), bool)
    e[0, 0] = True
    assert K.grow_mask(e, 2).sum() == 9                          # clipped at the border


def test_mask_for_matches_box():
    a, b = np.zeros((10, 10), bool), np.zeros((10, 10), bool)
    a[:, :4], b[:, 6:] = True, True
    masks = [([0.0, 0.0, 0.4, 1.0], K.pack_mask(a)), ([0.6, 0.0, 1.0, 1.0], K.pack_mask(b))]
    assert (K.unpack_mask(K.mask_for([0.62, 0.0, 1.0, 1.0], masks)) == b).all()
    assert K.mask_for([0.42, 0.0, 0.58, 1.0], masks) is None and K.mask_for([0, 0, 1, 1], None) is None


def test_swap_mask_covers_people_only():
    small = np.zeros((48, 80), bool)
    small[10:40, 10:20] = True                                   # tracker mask at tracking size
    it1 = {"own": [10 * 20.8, 10 * 20.0, 20 * 20.8, 40 * 20.0], "mask": K.pack_mask(small)}
    it2 = {"own": [1200.0, 200.0, 1400.0, 900.0], "mask": None}  # no tracker mask: her box
    m = K.swap_mask([it1, it2], 1664, 960)
    assert m.shape == (960, 1664) and m.dtype == np.float32 and m.max() == 1.0
    assert m[500, 300] == 1.0 and m[500, 1300] == 1.0           # inside both people
    assert m[175, 300] > 0.5                                     # grown above her head (room for a taller one)
    assert m[500, 800] == 0.0 and m[50, 50] == 0.0               # the floor between them and the corner stay
    assert K.swap_mask([], 64, 32).max() == 0.0
