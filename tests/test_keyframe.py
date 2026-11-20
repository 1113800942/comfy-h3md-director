import os
import numpy as np
import pytest

import h3md_keyframe as K


def test_parse_mapping_zh_en():
    assert K.parse_mapping("图1换左二，图2换左四") == {1: 2, 2: 4}
    assert K.parse_mapping("picture 1 -> left 2; pic 3 replaces left #5") == {1: 2, 3: 5}
    assert K.parse_mapping("图2换左第3个\n图1换左一") == {1: 1, 2: 3}


@pytest.mark.parametrize("bad", ["", "换人", "图1换左二，图2换左二", "图1换左二，图1换左三"])
def test_parse_mapping_errors(bad):
    with pytest.raises(K.KeyframeError):
        K.parse_mapping(bad)


def test_descriptions():
    d = K.parse_descriptions("图1：短发男\n\n2. 中长卷发女", [1, 2])
    assert d == {1: "短发男", 2: "中长卷发女"}
    with pytest.raises(K.KeyframeError):
        K.parse_descriptions("only one", [1, 2])


def _frames(n=60, fps=6.0):
    """three people standing apart; 1 and 2 (ids 11, 12) change places between 4 and 5 s"""
    out = []
    for i in range(n):
        t = i / fps
        a = 0.1 if t < 4 else (0.1 + 0.3 * min(1, (t - 4))) if t < 5 else 0.4
        b = 0.4 if t < 4 else (0.4 - 0.3 * min(1, (t - 4))) if t < 5 else 0.1
        who = [[11, [a, 0.2, a + 0.15, 0.95]], [12, [b, 0.2, b + 0.15, 0.95]], [13, [0.7, 0.2, 0.85, 0.95]]]
        out.append({"at": t, "who": sorted(who, key=lambda w: w[1][0])})
    return out


def test_number_and_resolve():
    f, order = K.number_people(_frames())
    assert order == [11, 12, 13]
    assert K.resolve({1: 2}, order) == [(1, 2, 12)]
    with pytest.raises(K.KeyframeError):
        K.resolve({1: 4}, order)


def test_link_windows():
    w1 = [{0: [0.1, 0.1, 0.3, 0.9], 1: [0.6, 0.1, 0.8, 0.9]}] * 3
    w2 = [{5: [0.61, 0.1, 0.8, 0.9], 7: [0.1, 0.1, 0.3, 0.9]}, {5: [0.62, 0.1, 0.8, 0.9], 7: [0.1, 0.1, 0.3, 0.9],
                                                                9: [0.4, 0.1, 0.5, 0.9]}]
    out = K.link_windows([w1, w2])
    assert len(out) == 4                       # shared frame counted once
    assert set(out[-1]) == {0, 1, 2}           # 7 -> 0, 5 -> 1, 9 is new
    assert out[-1][1][0] == 0.62


def test_pick_moments_gap_and_crossing():
    fr = _frames(n=150)                         # 25 s
    m = K.pick_moments(fr, {11, 12}, duration=25.0, max_gap=5.0)
    ts = [fr[i]["at"] for i, _ in m]
    assert ts[0] <= 5.0
    assert all(b - a <= 5.0 + 1e-6 for a, b in zip(ts, ts[1:]))
    assert 25.0 - ts[-1] <= 5.0
    assert not any(4.2 < t < 4.8 for t in ts)    # never while the two cross
    assert all(k == "swap" for _, k in m)


def test_place_changes():
    ch = K.place_changes(_frames(), [11, 12, 13])
    assert len(ch) == 1 and ch[0][2] == [(11, 12)]
    assert 4.0 <= ch[0][0] <= ch[0][1] <= 5.2


def test_swap_size():
    w, h = K.swap_size(1664, 960)
    assert w % 32 == 0 and h % 32 == 0 and 3.6e6 < w * h < 4.4e6 and abs(w / h - 1664 / 960) < 0.02
    assert K.cover_crop(1920, 1080, 1664, 960)[2:] == (1872, 1080)


def test_plan():
    assert K.plan_length(634) == 634 - ((634 - 5) % 17)
    assert K.plan_length(634) <= 634 and (K.plan_length(634) - 5) % 17 == 0
    with pytest.raises(K.KeyframeError):
        K.plan_length(60)
    chosen = [(1, 2, 12)]
    pr = K.swap_prompt(617, chosen, {1: "a man with short black hair, red jacket"}, 3,
                       [(4.0, 5.0, [(11, 12)])], {11: "the man in grey", 12: None, 13: "the woman"})
    assert "<Subject 1>" in pr and "second performer" in pr and "change places" in pr
    p = K.build_plan("H3MD_plans/x/src.mp4", 26.4, True, (1664, 960), 617, [("kf1", "H3MD_plans/x/kf1.png", 1.0)],
                     pr, "x")
    s = p["segments"][0]
    assert p["swapEngine"] == "remake" and p["autoSplit"] and s["length"] == 617
    assert s["videos"][0]["useDuration"] == round(617 / 24, 4)
    assert s["keyframes"] == [{"image": "kf1", "at": 1.0}] and s["locked"]["source"] == "video:src"


def test_people_looks():
    assert K.parse_people("左1：the man in a beanie\n\n2. the woman in red") == ["the man in a beanie", "the woman in red"]
    pr = K.swap_prompt(617, [(1, 2, 12)], {1: "a man"}, 3, [], {11: "the man in a beanie", 12: None, 13: "the girl"},
                       {1: "the man in a beanie", 2: "the woman in red", 3: "the girl"})
    assert "left at the start, the woman in red." in pr and "the man in a beanie and the girl" in pr
    assert "second from the left (the woman in red)" in K.swap_instruction(
        [{"rank": 2, "ref": 2, "desc": "a man", "look": "the woman in red"}])


def test_place_changes_far_swap():
    """2 and 5 of a row of five swap (the ones in between stay): one pair, not every inversion"""
    fr = []
    for i in range(60):
        t = i / 6.0
        xs = [0.05, 0.25, 0.4, 0.6, 0.8] if t < 4 else [0.05, 0.8, 0.4, 0.6, 0.25]
        fr.append({"at": t, "who": sorted([[pid, [x, 0.2, x + 0.1, 0.9]] for pid, x in zip([1, 2, 3, 4, 5], xs)],
                                          key=lambda w: w[1][0])})
    ch = K.place_changes(fr, [1, 2, 3, 4, 5])
    assert [c[2] for c in ch] == [[(2, 5)]]


def test_source_clip_drag_in_and_trim(tmp_path):
    import h3md_keyframe_nodes as KN
    src = tmp_path / "dance.mp4"
    src.write_bytes(b"x")
    calls = []

    class Vid:
        def __init__(self, path=None):
            self.path = path

        def get_stream_source(self):
            return self.path

        def get_duration(self):
            return 26.4

        def as_trimmed(self, start, dur):
            calls.append(("trim", start, dur))
            return Vid(None)

        def save_to(self, dst, crf=None):
            calls.append(("save", os.path.basename(dst), crf))
            open(dst, "wb").write(b"y")

    out = str(tmp_path / "out")
    assert KN.source_clip(Vid(str(src)), None, 0, 0, out) == str(src)           # dragged in, whole video: as is
    p = KN.source_clip(Vid(str(src)), None, 3, 13, out)
    assert calls == [("trim", 3.0, 10.0), ("save", "dance_3-13s.mp4", 16)] and os.path.isfile(p)
    calls.clear()
    KN.source_clip(Vid(str(src)), None, 5, 0, out)                              # 0 = to the end
    assert calls[0] == ("trim", 5.0, 0.0)
    for bad in ((Vid(str(src)), None, 10, 4), (None, None, 0, 0)):
        try:
            KN.source_clip(*bad, out)
            raise AssertionError("accepted")
        except K.KeyframeError:
            pass


def test_prepare_inputs_keep_old_widget_order():
    import h3md_keyframe_nodes as KN
    spec = KN.H3MDKeyframePrepare.INPUT_TYPES()
    assert list(spec["required"])[0] == "video"
    assert list(spec["optional"])[-3:] == ["source_video", "start_s", "end_s"]
