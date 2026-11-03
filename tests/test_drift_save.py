"""v0.2.0 drift_correct in the save nodes: plan from the segment cache (trims -> hard cuts), corrected read(), report."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import av_latent  # noqa: E402,F401  (CPU ComfyUI first)

import numpy as np  # noqa: E402
import torch  # noqa: E402

import h3md_cache as C  # noqa: E402
import h3md_drift_correct as DRIFT  # noqa: E402
import h3md_stream_save as S  # noqa: E402

N, H, W = 60, 96, 128
OV = 12


def picture(seed, sat, light=1.0):
    rng = np.random.default_rng(seed)
    base = np.kron(rng.integers(40, 215, size=(H // 8, W // 8, 3)).astype(np.float32), np.ones((8, 8, 1), np.float32))
    y = base.mean(-1, keepdims=True)
    img = np.clip((y + (base - y) * sat) * light, 0, 255) / 255.0
    return torch.from_numpy(img.astype(np.float32))[None].repeat(N, 1, 1, 1)


def make_cache(d, segs):
    """segs: [(seed, sat, light, trim)] -> H3MD_SEGMENTS info."""
    c = C.SegmentCache(d, "drift" * 8, True, dtype="uint8")
    lat = av_latent(t=2, h=4, w=4, seed=1)
    audio = {"waveform": torch.zeros(1, 2, 48000 * 3), "sample_rate": 48000}
    for k, (seed, sat, light, trim) in enumerate(segs):
        c.write(k, f"key{k}", lat, picture(seed, sat, light), audio, {"trim": trim, "report": {}})
    return S.segments_info(c.dir, list(range(len(segs))), [t for *_, t in segs], "next")


def seg_sat(src, j):
    blocks, _ = src.segment_blocks()
    s, e = blocks[j]
    fr = src.read(s + (e - s) // 2, e)                   # after the ease
    return float(np.mean([DRIFT.measure_frame(fr[i])["saturation"] for i in range(fr.shape[0])]))


class DriftSaveTests(unittest.TestCase):
    def test_inputs_appended_last(self):
        spec = S.H3MDSaveVideoInterp.INPUT_TYPES()
        self.assertEqual(list(spec["optional"]), ["drift_correct", "drift_strength"])
        self.assertEqual(spec["optional"]["drift_correct"][1]["default"], "off")

    def test_plan_from_cache_and_corrected_read(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, [(1, 1.0, 1.0, 0), (1, 1.06, 1.0, OV), (1, 1.12, 1.0, OV),   # shot 1: drifting
                                  (2, 1.5, 1.0, 0)])                                          # hard cut: shot 2
            src = S.SegmentSource(info)
            raw = [seg_sat(src, j) for j in range(4)]
            rows = S.attach_drift(src, "auto", 1.0)
            self.assertEqual([r["role"] for r in rows], ["ref", "drift", "drift", "ref"])
            fixed = [seg_sat(src, j) for j in range(4)]
            self.assertGreater(raw[2] / raw[0] - 1, 0.05)
            self.assertLess(abs(fixed[2] / fixed[0] - 1), 0.015)
            self.assertAlmostEqual(fixed[3], raw[3], delta=0.05)          # new shot: untouched
            src.close()

    def test_off_is_untouched_and_save_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, [(1, 1.0, 1.0, 0), (1, 1.06, 1.0, OV), (1, 1.12, 1.0, OV)])
            src = S.SegmentSource(info)
            self.assertIsNone(S.attach_drift(src, "off"))
            self.assertIsNone(src.drift)
            src.close()
            out = os.path.join(d, "clip.mp4")
            rep = S.stream_save(info, out, drift_correct="auto")
            self.assertTrue(os.path.isfile(out))
            with open(rep["drift_report"], encoding="utf-8") as f:
                j = json.load(f)
            self.assertEqual([r["role"] for r in j["segments"]], ["ref", "drift", "drift"])
            self.assertEqual(rep["frames"], 3 * N - 2 * OV)


if __name__ == "__main__":
    unittest.main()
