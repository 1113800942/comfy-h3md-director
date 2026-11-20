"""0.5.8: shot cuts and one keyframe per shot."""
import h3md_keyframe as K


def _rows(n, fps=24.0, cut_at=None):
    times = [i / fps for i in range(n)]
    hd = [0.03] * n
    pd = [0.07] * n
    hd[0] = pd[0] = 0.0
    if cut_at is not None:
        i = int(round(cut_at * fps))
        hd[i], pd[i] = 0.14, 0.20          # values measured at the real cut at 17.25 s
    return times, hd, pd


def test_cut_found():
    t, hd, pd = _rows(240, cut_at=5.0)
    assert K.cuts_from(t, hd, pd) == [5.0]


def test_fast_dance_is_not_a_cut():
    t, hd, pd = _rows(240)
    hd[100], pd[100] = 0.06, 0.13          # a fast move: pixel jump but colours stay
    assert K.cuts_from(t, hd, pd) == []


def _frames(dur=16.5, fps=6.0):
    fr = []
    for i in range(int(dur * fps)):
        t = round(i / fps, 4)
        fr.append({"at": t, "who": [[1, [0.1, 0.2, 0.3, 0.9]], [2, [0.4, 0.2, 0.6, 0.9]], [3, [0.7, 0.2, 0.9, 0.9]]]})
    return fr


def test_shot_without_keyframe_gets_one():
    fr = _frames()
    idx = {f["at"]: i for i, f in enumerate(fr)}
    moments = [(idx[0.0], "swap"), (idx[4.3333], "swap"), (idx[9.1667], "swap"), (idx[12.6667], "swap")]
    out = K.ensure_per_shot(fr, moments, {1, 2, 3}, [6.625, 14.25], 16.5)
    assert len(out) == 5
    added = [fr[i]["at"] for i, _ in out if (i, "swap") not in moments]
    assert len(added) == 1 and 14.25 < added[0] < 16.5


def test_no_cuts_changes_nothing():
    fr = _frames()
    moments = [(0, "swap"), (30, "swap")]
    assert K.ensure_per_shot(fr, moments, {1, 2, 3}, [], 16.5) == moments
