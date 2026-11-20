"""0.5.7: the recast person's own area grows past her box (mask_grow) and the people around keep only their
body core (protect_others), so bits of a bigger original (wig, tail, wide skirt) do not stay."""
import numpy as np

import h3md_keyframe as K

W, H = 1000, 600
CROP = [200, 0, 800, 600]
OWN = [380, 50, 560, 550]
NEIGHBOUR = [565, 50, 745, 550]
STRAND = 578 - CROP[0]          # a wig strand of the original, 18 px past her box, inside the neighbour's box


def test_defaults():
    assert K.OWN_GROW == 0.0 and K.PROTECT_PAD == 0.06


def test_old_settings_kept_the_strand():
    a = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.0, protect=0.06)   # = 0.5.6
    assert a[300, STRAND] < 0.2


def test_mask_grow_covers_the_strand():
    a = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.3, protect=-0.05)
    assert a[300, STRAND] > 0.95


def _skip_default_already_helps():
    old = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.0, protect=0.06)
    new = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR])
    assert new[300, STRAND] > old[300, STRAND] + 0.3


def test_neighbour_core_still_protected():
    a = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.3, protect=-0.05)
    assert a[300, 680 - CROP[0]] < 0.05


def test_bigger_grow_covers_more():
    small = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.1, protect=-0.05)
    big = K.paste_alpha(CROP, W, H, OWN, [NEIGHBOUR], own_grow=0.35, protect=-0.05)
    assert big.sum() > small.sum()


def test_edit_prompt_removes_leftovers():
    assert "sticking out" in K.edit_prompt("a woman", "en")
    assert "补成背景" in K.edit_prompt("a woman", "zh")
