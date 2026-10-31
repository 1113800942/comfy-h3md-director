"""Stage 2 (2026-10-29): size alignment, feed-forward chunking, out-of-memory retry, local calibration, readable
out-of-memory errors.  CPU, tiny real H3 model (h3_harness)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import FakeVAE, av_latent, empty_cond, sampler, tiny_h3_model  # noqa: E402

import torch  # noqa: E402

import h3md_sampler as S  # noqa: E402
import h3md_tiled_hires as TILED  # noqa: E402
import h3md_ffchunk as FF  # noqa: E402
import h3md_director as D  # noqa: E402

SIG = torch.tensor([1.0, 0.95, 0.9, 0.8, 0.6, 0.3, 0.0])


class OOM(torch.cuda.OutOfMemoryError if hasattr(torch.cuda, "OutOfMemoryError") else RuntimeError):
    pass


def streams(latent):
    return latent["samples"].unbind()


class Base(unittest.TestCase):
    def setUp(self):
        self.model = tiny_h3_model()
        self.vae = FakeVAE()
        self.tmp = tempfile.TemporaryDirectory()
        TILED.LOCAL_PATH = os.path.join(self.tmp.name, "cal.json")

    def tearDown(self):
        TILED.LOCAL_PATH = None
        self.tmp.cleanup()

    def run_sampler(self, latent, **kw):
        node = S.H3MDSampler()
        out = node.sample(self.model, empty_cond(), empty_cond(), self.vae, latent, sampler("euler"), SIG, seed=7,
                          cfg=1.0, transition_step=4, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0,
                          upscaler_model="none", **kw)
        return node, out[0]


class SizeTests(unittest.TestCase):
    def test_snap_64(self):
        self.assertEqual(D._snap_size(1472), 1472)
        self.assertEqual(D._snap_size(1088), 1088)
        self.assertEqual(D._snap_size(720), 704)
        self.assertEqual(D._snap_size(1080), 1088)

    def test_grid_note(self):
        self.assertIsNone(S.exact_grid_note(av_latent(t=2, h=68, w=92), 0.5))   # 1088 x 1472
        self.assertIsNotNone(S.exact_grid_note(av_latent(t=2, h=45, w=80), 0.5))  # 720p


class FFChunkTests(Base):
    def test_identical_and_restored(self):
        cls = FF._mlp_class()
        orig = cls.forward
        latent = av_latent(t=7, h=8, w=8)
        _, a = self.run_sampler(latent, highres_tiling=False)
        _, b = self.run_sampler(latent, highres_tiling=False, ff_chunk_rows=7)
        self.assertIs(cls.forward, orig)
        for x, y in zip(streams(a), streams(b)):
            self.assertTrue(torch.allclose(x, y, atol=1e-5), float((x - y).abs().max()))

    def test_chunked_forward_math(self):
        lin1 = torch.nn.Linear(6, 12, bias=False)

        class M:
            pass

        def orig(self, x):
            return lin1(x)[..., :6] * 2

        x = torch.randn(3, 50, 6)
        y = FF.chunked_forward(orig, 8)(M(), x)
        self.assertTrue(torch.allclose(y, orig(None, x), atol=1e-6))


class RetryTests(Base):
    def test_oom_retry_more_strips_then_ok(self):
        calls = []
        real = TILED._run_plan

        def flaky(executor, state, plan, *a, **k):
            calls.append(plan["tiles"])
            if len(calls) == 1:
                raise OOM("CUDA out of memory (simulated)")
            return real(executor, state, plan, *a, **k)

        with patch.object(TILED, "available_workspace", lambda p: 1e15), patch.object(TILED, "_run_plan", flaky):
            node, out = self.run_sampler(av_latent(t=7, h=8, w=24), highres_tiling=True, tiling_overlap=2)
        self.assertEqual(calls[0], 1)
        self.assertGreater(calls[1], 1)
        self.assertEqual(len(node.last_tiling["retries"]), 1)
        self.assertTrue(torch.isfinite(streams(out)[0]).all())
        recs = TILED.local_records()
        self.assertEqual([r["ok"] for r in recs], [False, True])

    def test_local_failure_tightens_limit(self):
        before = TILED.calibration(True, "gpu-x")["limit"]
        TILED.record_run({"available_mib": 1000.0, "est_tile_mib": 600.0, "tiles": 1, "rows_tile": 10,
                          "dynamic_vram": True}, False, "gpu-x")
        after = TILED.calibration(True, "gpu-x")["limit"]
        self.assertLess(after, before)
        self.assertAlmostEqual(after, min(before, TILED.FAIL_GUARD * TILED.CALIB_SCALE * 0.6), places=6)
        self.assertEqual(TILED.calibration(True, "other-gpu")["limit"], before)

    def test_ladder_ends_with_readable_error(self):
        def always(*a, **k):
            raise OOM("CUDA out of memory (simulated)")

        with patch.object(TILED, "available_workspace", lambda p: 1e15), patch.object(TILED, "_run_plan", always):
            with self.assertRaises(TILED.HighResOutOfMemory) as cm:
                self.run_sampler(av_latent(t=7, h=8, w=24), highres_tiling=True, tiling_overlap=2)
        self.assertIn("高清阶段显存不足", str(cm.exception))

    def test_low_res_oom_message(self):
        real = S.LUNA._sample

        def boom(model, *a, **k):
            raise OOM("CUDA out of memory (simulated)")

        with patch.object(S.LUNA, "_sample", boom):
            with self.assertRaises(S.SamplerOutOfMemory) as cm:
                self.run_sampler(av_latent(t=7, h=8, w=8), highres_tiling=False)
        self.assertIn("LOW-res", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
