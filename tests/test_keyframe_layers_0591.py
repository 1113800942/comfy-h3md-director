"""0.5.91: keyframes in layers, one person at a time (Qwen Image 2.1): erase -> clean background, make every new
character alone in her pose, put them back far to near, face on face."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402


def test_frame_rank():
    assert K.frame_rank([160, 20, 240, 190], [[20, 20, 100, 190], [300, 20, 380, 190]]) == (2, 3)
    assert K.frame_rank([0, 0, 5, 5], []) == (1, 1)


def test_fit_size():
    w, h = K.fit_size(400, 900)
    assert w % 32 == 0 and h % 32 == 0 and 0.85e6 <= w * h <= 1.15e6 and abs(w / h - 400 / 900) < 0.03
    w, h = K.fit_size(1664, 960, mp=0.46)
    assert abs(w / h - 1664 / 960) < 0.01 and 0.39e6 <= w * h <= 0.53e6


def test_grow_box_and_mask():
    assert K.grow_box([100, 100, 200, 300], 0.5, 0.25, 1000, 1000) == (50, 50, 250, 350)
    assert K.grow_box([0, 0, 100, 100], 0.5, 0.5, 120, 120) == (0, 0, 120, 120)          # clipped
    m = np.zeros((20, 30), bool)
    m[10, 15] = True
    g = K.grow_mask(m, 3)
    assert g.sum() == 49 and K.mask_box(g) == [12, 7, 19, 14] and K.mask_box(np.zeros((3, 3), bool)) is None


def test_depth_order():
    boxes = [[0, 0, 10, 300], [20, 0, 30, 500], [40, 0, 50, 200]]
    assert K.far_to_near(boxes) == [2, 0, 1]
    assert K.in_front([5, 0, 25, 500], [0, 0, 10, 300]) and not K.in_front([0, 0, 10, 300], [5, 0, 25, 500])
    assert not K.in_front([100, 0, 110, 500], [0, 0, 10, 300])                            # not overlapping


def test_match_people_by_overlap():
    objs = {0: [0.6, 0.1, 0.8, 0.9], 1: [0.1, 0.1, 0.3, 0.9], 2: [0.35, 0.1, 0.55, 0.9]}
    people = [[0.12, 0.1, 0.3, 0.9], [0.62, 0.15, 0.8, 0.9], [0.9, 0.1, 1.0, 0.9]]
    assert K.match_people(objs, people) == [1, 0, None]
    assert K.match_people({0: [0.1, 0.1, 0.3, 0.9]}, [[0.1, 0.1, 0.3, 0.9], [0.11, 0.1, 0.3, 0.9]]).count(0) == 1


def test_pick_head():
    person = [100, 50, 200, 450]
    heads = [[130, 55, 170, 100], [210, 60, 250, 105], [140, 300, 160, 320]]   # hers, a neighbour's, a knee
    assert K.pick_head(person, heads) == [130, 55, 170, 100]
    assert K.pick_head(person, [[210, 60, 250, 105]]) is None


def test_place_character():
    s, dx, dy, how = K.place_character([150, 40, 250, 400], [0, 0, 1000, 1200], [190, 40, 210, 60],
                                       [450, 100, 550, 200])
    assert how == "head" and abs(s - 0.2) < 1e-9
    assert abs(500 * s + dx - 200) < 1e-6 and abs(100 * s + dy - 40) < 1e-6
    s, dx, dy, how = K.place_character([150, 40, 250, 400], [100, 0, 300, 720])
    assert how == "figure" and abs(s - 0.5) < 1e-9 and abs(200 * s + dx - 200) < 1e-6 and abs(dy - 40) < 1e-6


def test_prompts():
    p = K.person_prompt("a man in a beige trench coat, dark grey trousers.")
    assert "图2" in p and "图1正中间那个人" in p and "beige trench coat" in p and "纯白色背景" in p
    assert "红框" in K.ERASE_PROMPT and "一切" in K.ERASE_PROMPT
    for w in ("手套", "袖", "丝袜", "裙"):                       # user rule: removal in one general phrase
        assert w not in K.ERASE_PROMPT
