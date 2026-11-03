"""tools/drift_correct.py on a synthetic 3-block video whose saturation and sharpness drift (no ComfyUI needed)."""
import os
import subprocess
import sys
import tempfile

import av
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
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


if __name__ == "__main__":
    test_drift_correct_pulls_blocks_back()
    print("ok")
