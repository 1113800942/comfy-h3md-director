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


def test_finish_no_longer_thins():
    """0.5.91 (03:31): thinning to 4 per estimated part is gone - the Director picks 4 per real part"""
    fr = [{"at": round(i / 6.0, 4), "who": [[1, [0.1, 0.1, 0.3, 0.9]]]} for i in range(120)]
    moments = [(i, "swap") for i in (0, 12, 24, 36, 48)]
    out, notes = K.finish_moments(fr, moments, {1}, 20.0, keep={fr[24]["at"]})
    assert len(out) == 5


def _clip():
    """20 s at 6 fps: man 1 the whole time, man 2 walks in at 3.3 s and out at 12 s"""
    fr = []
    for i in range(120):
        t = i / 6.0
        who = [[1, [0.1, 0.1, 0.3, 0.95]]]
        if 3.3 <= t < 12:
            who.append([2, [0.5, 0.1, 0.7, 0.95]])
        fr.append({"at": round(t, 4), "who": who})
    return fr


