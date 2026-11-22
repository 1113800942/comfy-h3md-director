"""0.5.91 seams (user 2026-11-21 18:12 / 19:03): the Director cuts a long swap shot ON keyframes so that every part's
new frames start pinned (plan 017: the gloves came back at 22.2-24.5 s, 2.3 s after a seam with no keyframe), takes
a spare ("backup") keyframe only where its parts are too short to reach a main one, and Keyframe Prepare adds spares
for gaps a 16 GB GPU cannot bridge."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_autosplit as AS  # noqa: E402
import h3md_director as D  # noqa: E402
import h3md_keyframe as K  # noqa: E402

KF017 = [0, 80, 116, 180, 200, 260, 296, 400, 452, 512, 588, 608, 648]   # plan 017 keyframes (frames), 685 f shot


def starts(contents):
    out, acc = [], 0
    for c in contents[:-1]:
        acc += c
        out.append(acc)
    return out


def test_5090_every_part_starts_on_a_keyframe():
    contents, uncovered = AS.plan_parts_kf(685, 0, 226, [(f, False) for f in KF017], AS.REMAKE_MIN)
    assert sum(contents) == 685 and not uncovered
    for b in starts(contents):
        assert any(0 <= f - b < AS.SEAM_WINDOW for f in KF017), (b, contents)
    assert all(AS.REMAKE_MIN <= c + (0 if j == 0 else AS.AUTO_OVERLAP) <= 226 for j, c in enumerate(contents))


def test_the_017_seam_is_gone():
    """22.17 s (frame 532) began 2.3 s before the next keyframe (588); no part may start in 512 < s <= 588 - 17."""
    contents, _ = AS.plan_parts_kf(685, 0, 226, [(f, False) for f in KF017], AS.REMAKE_MIN)
    assert not any(512 < b <= 588 - AS.SEAM_WINDOW for b in starts(contents))


def test_16gb_needs_a_spare_and_takes_it():
    main = [(f, False) for f in KF017]
    _, uncovered = AS.plan_parts_kf(685, 0, 124, main, AS.REMAKE_MIN)
    assert uncovered                                     # 296 -> 400 is longer than a 16 GB part
    contents, uncovered = AS.plan_parts_kf(685, 0, 124, main + [(348, True)], AS.REMAKE_MIN)
    assert not uncovered
    assert any(0 <= 348 - b < AS.SEAM_WINDOW for b in starts(contents))


def test_big_gpu_ignores_spares_and_fewest_parts_win():
    main = [(f, False) for f in KF017]
    a = AS.plan_parts_kf(685, 0, 345, main, AS.REMAKE_MIN)
    b = AS.plan_parts_kf(685, 0, 345, main + [(348, True), (560, True)], AS.REMAKE_MIN)
    assert a == b


def test_fits_whole():
    assert AS.plan_parts_kf(200, 0, 226, [(0, False)], AS.REMAKE_MIN) == ([200], [])


def _seg(kfs, length=685):
    return {"index": 0, "length": length, "overlapIn": 0, "startFrame": 0, "videos": [], "images": [], "audios": [],
            "prompt": "x", "audioMode": "none", "locked": None, "keyframes": kfs}


def test_split_uses_only_the_spares_it_needs(monkeypatch):
    kfs = [{"image": f"k{f}", "frame": f} for f in KF017] + [{"image": "s348", "frame": 348, "backup": True},
                                                            {"image": "s620", "frame": 620, "backup": True}]
    contents, _ = AS.plan_parts_kf(685, 0, 124, [(k["frame"], bool(k.get("backup"))) for k in kfs], AS.REMAKE_MIN)
    monkeypatch.setattr(D, "compute_labels", lambda p, m: [])
    parts = AS.split_segment(D, _seg(kfs), {}, contents)
    used = [k["image"] for p in parts for k in p.get("keyframes") or () if k.get("backup")]
    assert used == ["s348"]
    for p in parts[1:]:
        first = min(k["frame"] for k in p["keyframes"])
        assert AS.AUTO_OVERLAP <= first < AS.AUTO_OVERLAP + AS.SEAM_WINDOW


def test_unsplit_shot_drops_spares():
    seg = AS.drop_spares(_seg([{"image": "a", "frame": 0}, {"image": "b", "frame": 50, "backup": True}], 200))
    assert [k["image"] for k in seg["keyframes"]] == ["a"]


def test_remake_never_gets_a_part_over_345(monkeypatch):
    monkeypatch.setattr(D, "compute_labels", lambda p, m: [])
    s = _seg([{"image": f"k{f}", "frame": f} for f in KF017])
    s["videos"] = [{"id": "src", "trimStart": 0.0}]
    notes = []
    out = AS.apply(D, [s], {"images": {}, "videos": {}, "audios": {}}, {"swapEngine": "remake"}, 1664, 960,
                   "native", notes, budget=10 ** 9, sizes={("videos", "src"): (1920, 1080)})
    assert len(out) > 1 and all(int(p["length"]) <= AS.REMAKE_MAX for p in out), notes



def _frames(n_s=12.0):
    fr = []
    for i in range(int(n_s * 6) + 1):
        fr.append({"at": i / 6.0, "who": [[1, [0.40, 0.30, 0.55, 0.75]], [2, [0.60, 0.30, 0.75, 0.75]]]})
    return fr


