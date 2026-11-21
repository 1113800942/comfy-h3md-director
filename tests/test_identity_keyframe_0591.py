"""0.5.91 (user 01:08): every replaced person gets one large, clear keyframe; it is never thinned out."""
import h3md_keyframe as K


def frames_with(heights_b, n=60, fps=6.0):
    """person 1 large everywhere; person 2 small (0.2) except a few frames with height heights_b[t]"""
    out = []
    for i in range(n):
        t = round(i / fps, 4)
        hb = heights_b.get(i, 0.2)
        out.append({"at": t, "who": [[1, [0.10, 0.10, 0.30, 0.90]], [2, [0.60, 0.95 - hb, 0.75, 0.95]]]})
    return out


def test_adds_the_frame_where_he_is_large():
    fr = frames_with({40: 0.5, 41: 0.6, 42: 0.55})
    chosen = [(1, 1, 1), (2, 2, 2)]
    m, keep, notes, warns = K.ensure_identity(fr, [(0, "swap")], chosen, 10.0)
    assert [fr[i]["at"] for i, _ in m] == [0.0, fr[41]["at"]] and keep == {fr[41]["at"]}
    assert "picture 2" in notes[0] and not warns


def test_nothing_added_when_large_already():
    fr = frames_with({0: 0.6})
    m, keep, notes, warns = K.ensure_identity(fr, [(0, "swap")], [(1, 1, 1), (2, 2, 2)], 10.0)
    assert m == [(0, "swap")] and not keep and not notes


def test_sliver_at_the_edge_does_not_count_and_never_large_warns():
    fr = frames_with({})
    for f in fr[30:]:
        f["who"][1][1] = [0.97, 0.10, 1.0, 0.95]                 # only a sliver at the right edge: not clear
    m, keep, notes, warns = K.ensure_identity(fr, [(0, "swap")], [(2, 2, 2)], 10.0)
    assert fr[m[-1][0]]["at"] != fr[35]["at"]
    assert warns and "picture 2" in warns[0]


def test_finish_keeps_the_identity_keyframe():
    fr = [{"at": round(i / 6.0, 4), "who": [[1, [0.1, 0.1, 0.3, 0.9]]]} for i in range(120)]
    moments = [(i, "swap") for i in (0, 12, 24, 36, 48)]          # 5 keyframes in 8 s: one must go
    out, notes = K.finish_moments(fr, moments, {1}, 20.0, keep={fr[24]["at"]}, part_frames=345)
    assert fr[24]["at"] in [fr[i]["at"] for i, _ in out] and len(out) == 4


def test_every_part_shows_every_replaced_person():
    """2026-11-21 _009 trial: part 1 (0-7.3 s) had keyframes at 1.0, 3.17, 7.0 s, none with the man who walked in at
    3.3 s - Remake gave him another character's look.  ensure_part_cover adds a keyframe with him in that part."""
    fr = []
    for i in range(120):                      # 20 s at 6 fps
        t = i / 6.0
        who = [[1, [0.1, 0.1, 0.3, 0.95]]]
        if 3.3 <= t < 12:
            who.append([2, [0.5, 0.1, 0.7, 0.95]])
        fr.append({"at": round(t, 4), "who": who})
    ids = {1, 2}
    idx = {round(f["at"], 2): i for i, f in enumerate(fr)}
    moments = [(idx[1.0], "swap"), (idx[3.0], "swap"), (idx[7.0], "swap"), (idx[14.83], "swap")]
    bounds = [(0.0, 7.29), (7.29, 12.25), (12.25, 19.33)]
    out, notes, warns = K.ensure_part_cover(fr, moments, ids, 19.33, bounds=bounds)
    for a, b in bounds:
        shown = set().union(*[K.kf_people(fr, i, ids) for i, _ in out if a <= fr[i]["at"] < b] or [set()])
        need = {pid for pid in ids if any(a <= f["at"] < b and pid in K.kf_people(fr, j, ids) for j, f in enumerate(fr))}
        assert need <= shown, (a, b, need, shown)
        assert sum(1 for i, _ in out if a <= fr[i]["at"] < b) <= K.PART_MAX_KF
    assert notes and not warns
