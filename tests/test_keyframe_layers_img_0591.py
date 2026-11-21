"""0.5.91 layers: the hole fill before the erase, and the cut-out of a generated character."""
import numpy as np
import pytest

pytest.importorskip("folder_paths")
pytest.importorskip("scipy")
import h3md_keyframe_nodes as N  # noqa: E402


def test_fill_hole_takes_the_surroundings():
    img = np.zeros((120, 160, 3), np.uint8)
    img[:, :80] = (200, 0, 0)
    img[:, 80:] = (0, 0, 200)
    img[40:80, 60:100] = (0, 255, 0)                            # the person
    hole = np.zeros((120, 160), bool)
    hole[35:85, 55:105] = True
    out = N._fill_hole(img, hole)
    assert out[60, 62][1] < 40 and out[60, 62][0] > 120          # left of the hole: red from the left
    assert out[60, 98][1] < 40 and out[60, 98][2] > 120          # right: blue
    assert (out[~hole] == img[~hole]).all()                      # nothing outside changes


def test_main_piece_drops_specks_and_rim():
    m = np.zeros((60, 60), bool)
    m[10:50, 10:40] = True
    m[2:4, 55:57] = True                                          # a speck
    p = N._main_piece(m, erode=2)
    assert not p[3, 56] and p[30, 25] and not p[10, 25] and p[12, 25]
    a = N._soft(p, 2.0, inside=True)
    assert a.shape == (60, 60, 1) and a[30, 5, 0] == 0.0 and a[30, 25, 0] == 1.0
