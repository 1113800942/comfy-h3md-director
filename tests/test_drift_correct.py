"""tools/drift_correct.py on a synthetic 3-block video whose saturation and sharpness drift (no ComfyUI needed)."""
import os
import subprocess
import sys
import json
import tempfile

import av
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import drift_report as DR  # noqa: E402


def _video(path, w=192, h=128, fps=24):
    rng = np.random.default_rng(0)
    base = rng.integers(40, 215, size=(h // 8, w // 8, 3)).astype(np.float32)
    base = np.kron(base, np.ones((8, 8, 1), np.float32))            # blocky picture with edges
    with av.open(path, "w") as o:
        s = o.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        s.options = {"crf": "10"}
        for blk, (sat, blur) in enumerate([(1.0, 0.0), (1.06, 0.1), (1.12, 0.2)]):   # like a real take: +-10 %
            y = base.mean(-1, keepdims=True)
            img = y + (base - y) * sat
            box = (img + np.roll(img, 1, 0) + np.roll(img, -1, 0) + np.roll(img, 1, 1) + np.roll(img, -1, 1)) / 5
            img = img * (1 - blur) + box * blur                       # softer each block
            frame = np.clip(img, 0, 255).astype(np.uint8)
            for _ in range(2 * fps):
                for p in s.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                    o.mux(p)
        for p in s.encode():
            o.mux(p)


def _block_stats(path):
    data, fps = DR.measure(path)
    blocks = DR.bounds(len(data), fps, every=2.0)
    m = [data[s:e].mean(0) for s, e in blocks]
    return [(100 * (x[2] / m[0][2] - 1), 100 * (x[3] / m[0][3] - 1)) for x in m]


def test_drift_correct_pulls_blocks_back():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "drift.mp4")
    _video(src)
    before = _block_stats(src)
    assert before[2][0] > 5 and before[2][1] < -5, before
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "drift_correct.py"), src, "--every", "2"], check=True,
                   stdout=subprocess.DEVNULL)
    after = _block_stats(os.path.join(d, "drift_corrected.mp4"))
    # last block: saturation and sharpness much closer to block 1 than before
    assert abs(after[2][0]) < abs(before[2][0]) / 3, (before, after)
    assert abs(after[2][1]) < abs(before[2][1]) / 2, (before, after)
    assert os.path.isfile(os.path.join(d, "drift_corrected.json"))


def _two_shots(path, w=192, h=128, fps=24, block=48):
    """shot A (3 segments, drifting) | hard cut | shot B: other picture, much more colourful (3 segments, drifting)."""
    rng = np.random.default_rng(1)
    pics = [np.kron(rng.integers(40, 215, size=(h // 8, w // 8, 3)).astype(np.float32), np.ones((8, 8, 1), np.float32))
            for _ in range(2)]
    with av.open(path, "w") as o:
        s = o.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        s.options = {"crf": "10"}
        for shot, base_sat in enumerate([1.0, 1.6]):
            base = pics[shot]
            for sat in (base_sat, base_sat * 1.06, base_sat * 1.12):
                y = base.mean(-1, keepdims=True)
                frame = np.clip(y + (base - y) * sat, 0, 255).astype(np.uint8)
                for _ in range(block):
                    for p in s.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                        o.mux(p)
        for p in s.encode():
            o.mux(p)


def test_hard_cut_starts_a_new_reference():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "two.mp4")
    _two_shots(src)
    # segments of 48 visible frames: continued ones are 56 long with an 8-frame overlap; '48:0' = hard cut
    segs = "48,56,56,48:0,56,56"
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "drift_correct.py"), src, "--segments", segs,
                    "--overlap", "8", "--no-sharpen"], check=True, stdout=subprocess.DEVNULL)
    data_b, _ = DR.measure(src)
    data_a, _ = DR.measure(os.path.join(d, "two_corrected.mp4"))
    blk = [(i * 48, (i + 1) * 48) for i in range(6)]
    before = [data_b[s:e, 2].mean() for s, e in blk]
    after = [data_a[s:e, 2].mean() for s, e in blk]
    # shot B's first segment is a new reference: untouched (not pulled to shot A)
    assert abs(after[3] / before[3] - 1) < 0.01, (before, after)
    # inside each shot the drift is pulled back to that shot's first segment
    for first, last in ((0, 2), (3, 5)):
        assert abs(before[last] / before[first] - 1) > 0.05
        assert abs(after[last] / after[first] - 1) < abs(before[last] / before[first] - 1) / 3, (before, after)
    rep = json.load(open(os.path.join(d, "two_corrected.json")))
    assert rep["shots"] == 2 and {r["shot"] for r in rep["segments"]} == {1, 2}


def _day_to_night(path, w=192, h=128, fps=24, block=48):
    """One continued shot: 3 drifting day segments, then it gets dark (brightness -35 %), 3 more drifting segments."""
    rng = np.random.default_rng(2)
    base = np.kron(rng.integers(40, 215, size=(h // 8, w // 8, 3)).astype(np.float32), np.ones((8, 8, 1), np.float32))
    with av.open(path, "w") as o:
        s = o.add_stream("libx264", rate=fps)
        s.width, s.height, s.pix_fmt = w, h, "yuv420p"
        s.options = {"crf": "10"}
        for light, sat in ((1.0, 1.0), (1.0, 1.05), (1.0, 1.10), (0.65, 1.10), (0.65, 1.155), (0.65, 1.21)):
            y = base.mean(-1, keepdims=True)
            img = (y + (base - y) * sat) * light
            frame = np.clip(img, 0, 255).astype(np.uint8)
            for _ in range(block):
                for p in s.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                    o.mux(p)
        for p in s.encode():
            o.mux(p)


def test_scene_change_inside_a_shot_is_not_pulled_back():
    d = tempfile.mkdtemp()
    src = os.path.join(d, "night.mp4")
    _day_to_night(src)
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "drift_correct.py"), src, "--segments",
                    "48,56,56,56,56,56", "--overlap", "8", "--no-sharpen"], check=True, stdout=subprocess.DEVNULL)
    rep = json.load(open(os.path.join(d, "night_corrected.json")))
    roles = [r["role"] for r in rep["segments"]]
    assert roles == ["ref", "drift", "drift", "scene", "drift", "drift"], rep["segments"]
    assert "brightness" in rep["segments"][3]["why"]
    data_b, _ = DR.measure(src)
    data_a, _ = DR.measure(os.path.join(d, "night_corrected.mp4"))
    blk = [(i * 48 + 30, (i + 1) * 48) for i in range(6)]   # skip the ease (here: first third)
    bri_b = [data_b[s:e, 0].mean() for s, e in blk]
    bri_a = [data_a[s:e, 0].mean() for s, e in blk]
    # night stays night (relative to the untouched first segment: the re-encode itself shifts levels a little)
    assert abs((bri_a[3] / bri_a[0]) / (bri_b[3] / bri_b[0]) - 1) < 0.02
    sat_a = [data_a[s:e, 2].mean() for s, e in blk]
    sat_b = [data_b[s:e, 2].mean() for s, e in blk]
    assert abs(sat_a[5] / sat_a[3] - 1) < abs(sat_b[5] / sat_b[3] - 1) / 3   # pulled to the NEW reference


def test_content_noise_is_not_corrected():
    """Values that jump around (moving content) are not a drift trend: nothing is corrected."""
    import h3md_drift_correct as DC
    fp = lambda s, k: {"brightness": 50.0, "contrast": 20.0, "a": 0.0, "b": 0.0, "saturation": s, "sharpness": k,  # noqa: E731
                       "thumb": np.ones((9, 16))}
    noisy = [fp(40, 100), fp(42.8, 100), fp(40.4, 98), fp(41.6, 101), fp(43.6, 99), fp(42.4, 100)]
    dec = DC.decide(noisy, [True] + [False] * 5)
    assert [d["role"] for d in dec] == ["ref"] + ["steady"] * 5, dec
    trend = [fp(40 * (1 + 0.007 * i), 100 * (1 - 0.015 * i)) for i in range(14)]
    dec = DC.decide(trend, [True] + [False] * 13)
    assert all(d["role"] == "drift" for d in dec[1:])
    assert dec[13]["target"]["saturation"] > 0.08 and dec[13]["target"]["sharpness"] < -0.15


if __name__ == "__main__":
    test_drift_correct_pulls_blocks_back()
    test_hard_cut_starts_a_new_reference()
    test_scene_change_inside_a_shot_is_not_pulled_back()
    test_content_noise_is_not_corrected()
    print("ok")
