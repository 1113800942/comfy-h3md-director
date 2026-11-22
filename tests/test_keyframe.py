import os
import numpy as np
import pytest

import h3md_keyframe as K


def test_parse_mapping_zh_en():
    assert K.parse_mapping("图1换左二，图2换左四") == {1: [2], 2: [4]}
    assert K.parse_mapping("picture 1 -> left 2; pic 3 replaces left #5") == {1: [2], 3: [5]}
    assert K.parse_mapping("图2换左第3个\n图1换左一") == {1: [1], 2: [3]}


def test_parse_mapping_cast_numbers():
    """0.5.91: numbers are cast numbers (order of first appearance); empty = automatic"""
    assert K.parse_mapping("") == {} and K.parse_mapping("  \n") == {}
    assert K.parse_mapping("图1换3号，图2换1号") == {1: [3], 2: [1]}
    assert K.parse_mapping("picture 1 -> #3") == {1: [3]}


def test_parse_mapping_one_picture_several_people():
    """0.5.91 (user 22:11): the tracker cut one person into #2 and #4 - both lines go to picture 1"""
    assert K.parse_mapping("图1换2号\n图1换4号\n图2换3号") == {1: [2, 4], 2: [3]}
    assert K.parse_mapping("picture 3 -> 3\n图3换3号") == {3: [3]}


def test_merge_cast_makes_one_person():
    """_040: the centre woman was #2 (0-2.8 s, 8.8 s on) and #4 (3.0-9.3 s, a double box at 8.8-9.3 s); the user
    links both to picture 1 - one id everywhere, the double box dropped, the kept list loses #4"""
    fr = []
    for i in range(60):
        t = i / 6.0
        who = [[11, [0.1, 0.2, 0.3, 0.95]], [13, [0.7, 0.2, 0.9, 0.95]]]
        if t < 2.8 or t >= 8.8:
            who.append([12, [0.4, 0.2, 0.6, 0.95]])
        if 3.0 <= t < 9.3:
            who.append([14, [0.41, 0.25, 0.6, 0.9]])
        fr.append({"at": t, "who": who})
    people = K.cast(fr)
    nums = {a["id"]: n for n, a in enumerate(people, 1)}
    links = K.resolve({1: [nums[12], nums[14]], 2: [nums[11]], 3: [nums[13]]}, people)
    chosen, gone, notes = K.merge_cast(fr, links)
    assert [c[2] for c in chosen] == [12, 11, 13] and gone == {14} and notes
    for f in fr:
        ids = [w[0] for w in f["who"]]
        assert 14 not in ids and ids.count(12) <= 1                 # double box at 8.8-9.3 s: one box left
        cx = [w[1][0] + w[1][2] for w in f["who"]]
        assert cx == sorted(cx)                                      # still left to right
    # she is boxed everywhere except the real 2.8-3.0 s gap of the tracker
    assert all(any(w[0] == 12 for w in f["who"]) for f in fr if f["at"] < 2.8 or f["at"] >= 3.0)
    # the 8.8-9.3 s overlap keeps the picture's own (first) number's box
    assert all(next(w[1] for w in f["who"] if w[0] == 12)[1] == 0.2 for f in fr if 8.8 <= f["at"] < 9.3)


@pytest.mark.parametrize("bad", ["换人", "图1换左二，图2换左二"])
def test_parse_mapping_errors(bad):
    with pytest.raises(K.KeyframeError):
        K.parse_mapping(bad)


def test_descriptions():
    d = K.parse_descriptions("图1：短发男\n\n2. 中长卷发女", [1, 2])
    assert d == {1: "短发男", 2: "中长卷发女"}
    # 0.5.91 (user 16:53): optional - the AI describes the rest from the pictures
    assert K.parse_descriptions("only one", [1, 2]) == {1: "only one", 2: ""}
    assert K.parse_descriptions("", [1, 3]) == {1: "", 3: ""}
    assert K.parse_descriptions("picture 3: a woman in a red dress", [1, 3]) == {1: "", 3: "a woman in a red dress"}


def test_characters_from_the_answer_fill_the_remake_prompt():
    people = [{"first": 0.0, "side": "left"}, {"first": 0.0, "side": "right"}]
    pr = K.swap_prompt(120, [(1, 1, 11), (2, 2, 12)], {1: "a man in a red jacket", 2: ""}, 2, [],
                       {11: None, 12: None}, people=people)
    assert "a man in a red jacket" in pr and (K.DESC_SLOT % 2) in pr and (K.DESC_SLOT % 1) not in pr
    ans = '{"package": "x", "characters": {"2": "a woman with long black hair, navy dress, black heels."}, ' \
          '"prompts": {"1": "p"}}'
    ch = K.parse_characters(ans)
    assert ch == {2: "a woman with long black hair, navy dress, black heels."}
    out, left = K.fill_characters(pr, ch)
    assert not left and "navy dress, black heels;" in out and "{{" not in out
    assert K.fill_characters(pr, {})[1] == [2]
    assert K.parse_characters("#1: a prompt") == {} and K.parse_prompts(ans, 1) == {1: "p"}


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


def test_cast_and_resolve():
    people = K.cast(_frames())
    assert [a["id"] for a in people] == [11, 12, 13]
    assert K.resolve({1: [2]}, people) == [(1, 2, 12)]
    with pytest.raises(K.KeyframeError):
        K.resolve({1: [4]}, people)


def test_cast_order_of_appearance():
    """0.5.91 (user 01:50): a clip cut from the middle - two people at the start, more walk in later, places
    change.  Numbers follow the first appearance; a passer-by (< 1 s) and an edge sliver get no number."""
    out = []
    for i in range(60):
        t = i / 6.0
        who = [[1, [0.55, 0.1, 0.7, 0.95]], [2, [0.1, 0.1, 0.25, 0.95]]]
        if t >= 3:
            who.append([3, [0.75, 0.2, 0.9, 0.95]])
        if t >= 5:
            who[0], who[1] = [1, [0.1, 0.1, 0.25, 0.95]], [2, [0.55, 0.1, 0.7, 0.95]]   # swap places
            who.append([4, [0.35, 0.2, 0.5, 0.95]])
        if 2 <= t < 2.5:
            who.append([7, [0.3, 0.2, 0.4, 0.9]])                                     # passer-by
        who.append([9, [0.97, 0.2, 1.0, 0.9]])                                        # strip at the edge
        out.append({"at": t, "who": sorted(who, key=lambda w: (w[1][0] + w[1][2]) / 2)})
    people = K.cast(out)
    assert [a["id"] for a in people] == [2, 1, 3, 4]
    assert people[0]["side"] == "left" and people[2]["first"] == 3.0
    mp = K.auto_cast(people, [1, 2, 3])
    assert K.resolve(mp, people) == [(1, 1, 2), (2, 2, 1), (3, 3, 3)]
    with pytest.raises(K.KeyframeError):
        K.auto_cast(people, [1, 2, 3, 4, 5])
    assert K.sliver([0.97, 0.2, 1.0, 0.9]) and not K.sliver([0.0, 0.2, 0.2, 0.9])


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
    assert len(m) == 5 and all(b - a <= 2 * 5.0 + 1e-6 for a, b in zip(ts, ts[1:]))   # 0.6.1: one per 5 s slot (gaps over a part: spares)
    assert 25.0 - ts[-1] <= 5.0
    assert not any(4.2 < t < 4.8 for t in ts)    # never while the two cross
    assert all(k == "swap" for _, k in m)


def test_place_changes():
    ch = K.place_changes(_frames(), [11, 12, 13])
    assert len(ch) == 1 and ch[0][2] == [(11, 12)]
    assert 4.0 <= ch[0][0] <= ch[0][1] <= 5.2


def test_cover_crop():
    assert K.cover_crop(1920, 1080, 1664, 960)[2:] == (1872, 1080)


def test_plan():
    assert K.plan_length(634) == 634 - ((634 - 5) % 17)
    assert K.plan_length(634) <= 634 and (K.plan_length(634) - 5) % 17 == 0
    with pytest.raises(K.KeyframeError):
        K.plan_length(60)
    chosen = [(1, 2, 12)]
    pr = K.swap_prompt(617, chosen, {1: "a man with short black hair, red jacket"}, 3,
                       [(4.0, 5.0, [(11, 12)])], {11: "the man in grey", 12: None, 13: "the woman"})
    assert "<Subject 1>" in pr and "performer #2" in pr and "change places" in pr
    assert "at the start" not in pr and "order of first appearance" in pr
    people = [{"first": 0.0, "side": "left"}, {"first": 3.0, "side": "right"}, {"first": 3.0, "side": "centre"}]
    pr2 = K.swap_prompt(617, chosen, {1: "a man"}, 3, [], {11: "x", 12: None, 13: "y"}, people=people)
    assert "performer #2, who first appears at 3.0 s on the right of the frame" in pr2
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
    assert "performer #2, the woman in red." in pr and "the man in a beanie and the girl" in pr


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


def test_dark_frames_are_no_keyframes():
    """0.5.91 (user 23:15, _046 #9): the stage blacks out at 16.8-17.0 s - the keyframe moves to the nearest lit frame;
    a clip that is dark all through loses nothing (the clip's own light is the yardstick)"""
    fr = []
    for i in range(60):
        t = i / 6.0
        fr.append({"at": t, "who": [[1, [0.2, 0.2, 0.35, 0.95]], [2, [0.6, 0.2, 0.75, 0.95]]]})
    light = {i: (0.1 if 4.4 < f["at"] < 5.6 else 0.7) for i, f in enumerate(fr)}
    notes = K.mark_dark(fr, light)
    assert notes and all(bool(f.get("dark")) == (4.4 < f["at"] < 5.6) for f in fr)
    assert K.score(fr, 27, {1, 2}) is None and K.score(fr, 20, {1, 2}) is not None
    mom = K.pick_moments(fr, {1, 2}, 10.0, max_gap=5.0)
    assert mom and not any(fr[i].get("dark") for i, _ in mom)
    dim = [{"at": f["at"], "who": f["who"]} for f in fr]
    assert K.mark_dark(dim, {i: 0.12 for i in range(len(dim))}) == [] and not any(f.get("dark") for f in dim)
    img = np.zeros((40, 80, 3), np.uint8)
    img[10:30, 10:20] = 200
    assert abs(K.people_light(img, [[0.1, 0.2, 0.3, 0.8]]) - 200 / 255) < 0.02
    assert K.people_light(img, [[0.6, 0.2, 0.9, 0.8]]) == 0.0


def test_one_keyframe_per_part():
    """0.6.1 (user 01:33): one keyframe per Director part - a 38 s clip with 12 s parts gets ceil(38 / 12) = 4 slot
    keyframes, the first near the start; with 5 s slots 8.  The 3 s per-person cover (ensure_cover) is gone."""
    fr = []
    for i in range(int(38 * 6)):
        t = i / 6.0
        fr.append({"at": t, "who": [[1, [0.1, 0.2, 0.3, 0.95]], [2, [0.5, 0.2, 0.7, 0.95]], [3, [0.75, 0.2, 0.9, 0.95]]]})
    for gap, n in ((12.0, 4), (5.0, 8)):
        m = K.pick_moments(fr, {1, 2, 3}, 38.0, max_gap=gap)
        ts = [fr[i]["at"] for i, _ in m]
        assert len(m) == n and ts[0] < 0.5 and all(b - a <= 2 * 38.0 / n + 0.2 for a, b in zip(ts, ts[1:]))
    assert not hasattr(K, "ensure_cover")


def test_slot_without_clear_frame_avoids_double_boxes():
    """0.6.1: a slot with no frame score() accepts takes a frame that is not a double box when there is one"""
    fr = []
    for i in range(60):
        t = i / 6.0
        fr.append({"at": t, "who": [[1, [0.1, 0.2, 0.3, 0.95]], [2, [0.5, 0.2, 0.7, 0.95]]], "bad": t < 4.0 or t > 4.5})
    for f in fr:
        if not f["bad"]:
            f["dark"] = True                     # the only non-double-box frames are dark: then any usable one
    m = K.pick_moments(fr, {1, 2}, 10.0, max_gap=12.0)
    assert len(m) == 1
    fr[30]["dark"] = False; fr[30]["bad"] = False; fr[30]["at"] = 5.0
    fr[29]["bad"] = True
    m = K.pick_moments([dict(f, bad=True) if i != 30 else f for i, f in enumerate(fr)], {1, 2}, 10.0, max_gap=12.0)
    assert m and m[0][0] == 30
