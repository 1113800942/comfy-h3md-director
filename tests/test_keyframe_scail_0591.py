"""0.5.91: keyframes by SCAIL-2 character replacement (ComfyUI's official template), driven by a short piece of
the source around every keyframe, everybody at once, people bound to their characters by SCAIL-2's mask colours."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_keyframe as K  # noqa: E402


def test_frame_rank():
    assert K.frame_rank([160, 20, 240, 190], [[20, 20, 100, 190], [300, 20, 380, 190]]) == (2, 3)
    assert K.frame_rank([0, 0, 5, 5], []) == (1, 1)


def test_scail_size():
    w, h = K.scail_size(1664, 960)
    assert (w, h) == (896, 512)                                  # the official template's size
    w, h = K.scail_size(960, 1664)
    assert h > w


def test_window_times_centred_and_inside():
    t, k = K.window_times(8.33, 17.0)
    assert len(t) == K.SCAIL_LEN and abs(t[k] - 8.33) < 1 / 24 and k == K.SCAIL_LEN // 2
    assert all(b > a for a, b in zip(t, t[1:]))
    t, k = K.window_times(0.0, 17.0)
    assert t[0] == 0.0 and k == 0 and len(set(t)) == K.SCAIL_LEN
    t, k = K.window_times(16.95, 17.0)
    assert t[-1] <= 17.0 and abs(t[k] - 16.95) < 1 / 24 and k > K.SCAIL_LEN // 2


def test_match_people_by_overlap():
    objs = {0: [0.6, 0.1, 0.8, 0.9], 1: [0.1, 0.1, 0.3, 0.9], 2: [0.35, 0.1, 0.55, 0.9]}
    people = [[0.12, 0.1, 0.3, 0.9], [0.62, 0.15, 0.8, 0.9], [0.9, 0.1, 1.0, 0.9]]
    assert K.match_people(objs, people) == [1, 0, None]          # the third is not there
    assert K.match_people({}, people) == [None, None, None]
    # one tracked person is never given to two people
    assert K.match_people({0: [0.1, 0.1, 0.3, 0.9]}, [[0.1, 0.1, 0.3, 0.9], [0.11, 0.1, 0.3, 0.9]]).count(0) == 1


def test_pick_head():
    person = [100, 50, 200, 450]
    heads = [[130, 55, 170, 100], [210, 60, 250, 105], [140, 300, 160, 320]]   # hers, a neighbour's, a knee
    assert K.pick_head(person, heads) == [130, 55, 170, 100]
    assert K.pick_head(person, [[210, 60, 250, 105]]) is None and K.pick_head(person, []) is None


def test_place_character_head_on_head():
    # half-body photo, head 100 px tall at (450..550, 100..200); the target head 20 px at (190..210, 40..60)
    s, dx, dy, how = K.place_character([150, 40, 250, 400], [0, 0, 1000, 1200], [190, 40, 210, 60],
                                       [450, 100, 550, 200])
    assert how == "head" and abs(s - 0.2) < 1e-9
    assert abs(500 * s + dx - 200) < 1e-6 and abs(100 * s + dy - 40) < 1e-6   # head middle and top land on hers
    s, dx, dy, how = K.place_character([150, 40, 250, 400], [100, 0, 300, 720])
    assert how == "figure" and abs(s - 0.5) < 1e-9 and abs(200 * s + dx - 200) < 1e-6 and abs(dy - 40) < 1e-6


def test_scail_prompt_names_people_by_place():
    p = K.scail_prompt([{"rank": 3, "desc": "a man in a beige coat."}, {"rank": 1, "desc": "a blonde woman"}])
    assert p.index("first person from the left is a blonde woman") < p.index("third person from the left is a man")
    assert "same scene" in p and "coat." in p and ".." not in p
    for w in ("glove", "sleeve", "stocking", "hand"):          # user rule after runs 40/41: no item list
        assert w not in p.lower()
