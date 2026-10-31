"""Stage 1 (2026-10-29): Luna engine, drift-only continuation, high-res tiling, seed resume.

Runs on CPU with ComfyUI's real MiniMax H3 code and a tiny one-block model (h3_harness).
COMFYUI_ROOT must point at a ComfyUI checkout.  Run from the repo root:  python -m pytest -q tests
"""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import FakeVAE, av_latent, empty_cond, sampler, tiny_h3_model  # noqa: E402  (CPU first)

import torch  # noqa: E402

import comfy.nested_tensor  # noqa: E402

import h3md_sampler as S  # noqa: E402
import h3md_tiled_hires as TILED  # noqa: E402
import h3md_vram  # noqa: E402
import h3md_director as D  # noqa: E402
import h3md_cache as C  # noqa: E402


def streams(latent):
    return latent["samples"].unbind()


def sig(n):
    return torch.linspace(1.0, 0.0, n + 1)[:-1].tolist() + [0.0]


SIG = torch.tensor([1.0, 0.95, 0.9, 0.8, 0.6, 0.3, 0.0])   # 6 steps: 4 low + 2 high


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.model = tiny_h3_model()
        self.vae = FakeVAE()

    def run_sampler(self, latent, model=None, **kw):
        node = S.H3MDSampler()
        kw.setdefault("highres_tiling", False)
        out = node.sample(model or self.model, empty_cond(), empty_cond(), self.vae, latent, sampler("euler"), SIG,
                          seed=7, cfg=1.0, transition_step=4, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0,
                          upscaler_model="none", **kw)
        return node, out[0]

    def test_input_types_static(self):
        spec = S.H3MDSampler.INPUT_TYPES()
        self.assertEqual(list(spec["required"])[:9], ["low_res_model", "positive", "negative", "vae", "latent_image",
                                                     "sampler", "sigmas", "seed", "cfg"])
        for gone in ("highres_tiling_mode", "highres_token_chunk", "highres_weight_cache_gb", "tiling_mode",
                     "tiling_tiles"):
            self.assertNotIn(gone, spec["optional"])
        self.assertTrue(spec["optional"]["highres_tiling"][1]["default"])
        self.assertIn("tiling_axis", spec["optional"])
        self.assertIn("tiling_overlap", spec["optional"])

    def test_split_sigmas(self):
        lo, hi = S.split_sigmas(SIG, 4)
        self.assertEqual(lo.tolist(), SIG[:5].tolist())
        self.assertEqual(hi.tolist(), SIG[4:].tolist())
        with self.assertRaises(ValueError):
            S.split_sigmas(SIG, 6)

    def test_plain_run_only_samples_and_steps_logged(self):
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        try:
            node, out = self.run_sampler(av_latent(t=7, h=8, w=8), h3md_capture_lowres=True)
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertEqual(list(out), ["samples"])
        v, a = streams(out)
        self.assertEqual(tuple(v.shape), (1, 24, 7, 8, 8))
        self.assertTrue(torch.isfinite(v).all() and torch.isfinite(a).all())
        self.assertEqual(tuple(node.last_lowres.shape), (1, 24, 7, 4, 4))
        steps = [r for r in records if r.startswith("[H3MD timing]")]
        self.assertEqual(len(steps), 6)
        self.assertEqual(len(node.last_steps), 6)
        m = h3md_vram._STEP_RE.search(steps[0])
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "low_res")
        self.assertTrue(m.group(4))   # step 1 = setup
        self.assertEqual(h3md_vram._STEP_RE.search(steps[-1]).group(1), "high_res")

    def continuation(self, strength=0.6, t=7, vt=2, prev_seed=3):
        latent = av_latent(t=t, h=8, w=8, seed=1)
        prev = av_latent(t=t, h=8, w=8, seed=prev_seed)
        out, info = D.continue_latent(latent, prev, 5, True, None)
        model, fn = D.install_drift(self.model, t, streams(out)[1].shape[-1], info["video_tokens"], 1, strength)
        return out, model, info

    def test_drift_continuation_restores_prefix_exactly(self):
        latent, model, info = self.continuation()
        vt = info["video_tokens"]
        self.assertGreater(vt, 0)
        _, out = self.run_sampler(latent, model=model)
        self.assertTrue(torch.equal(streams(out)[0][:, :, :vt], streams(latent)[0][:, :, :vt].to(streams(out)[0])))

    def test_low_res_head_used(self):
        latent, model, info = self.continuation()
        vt = info["video_tokens"]
        head = torch.full((1, 24, vt, 4, 4), 0.25)
        self.assertIsNone(S.check_low_res_head(head, latent, 0.5))
        seen = {}
        real = S.LUNA.two_pass_sample

        def spy(*a, **k):
            seen["head"] = k.get("low_res_head")
            return real(*a, **k)

        with patch.object(S.LUNA, "two_pass_sample", spy):
            node, _ = self.run_sampler(latent, model=model, h3md_low_res_prefix=head, h3md_capture_lowres=True,
                                       h3md_low_res_prefix_source="native_lowres_carry")
        self.assertIs(seen["head"]["samples"], head)
        self.assertTrue(torch.equal(node.last_lowres[:, :, :vt], head))   # kept prefix of the low-res endpoint

    def test_bad_head_skipped(self):
        latent, model, _ = self.continuation()
        self.assertIsNotNone(S.check_low_res_head(torch.zeros(1, 24, 2, 3, 3), latent, 0.5))
        self.assertIsNotNone(S.check_low_res_head(torch.zeros(1, 24, 2, 4, 4), av_latent(t=7, h=8, w=8), 0.5))

    def test_drift_moves_to_separate_high_model(self):
        latent, model, _ = self.continuation()
        high = tiny_h3_model(seed=5)
        seen = {}
        real = S.LUNA.two_pass_sample

        def spy(*a, **k):
            seen["high"] = k["high_res_model"]
            seen["low"] = a[0]
            return real(*a, **k)

        with patch.object(S.LUNA, "two_pass_sample", spy):
            self.run_sampler(latent, model=model, high_res_model=high)
        drift = model.model_options["h3md_drift"]
        self.assertIs(seen["high"].model_options.get("denoise_mask_function"), drift)
        self.assertIs(seen["low"].model_options.get("denoise_mask_function"), drift)
        self.assertNotIn("denoise_mask_function", high.model_options)   # input model untouched

    def test_between_passes_hook_and_unknown_inputs(self):
        calls = []
        _, out = self.run_sampler(av_latent(t=7, h=8, w=8), h3md_on_lowres_end=lambda r: calls.append(r),
                                  highres_tiling_mode="global_stream")
        self.assertEqual(len(calls), 1)

    def test_tiling_one_strip_is_identical(self):
        latent = av_latent(t=7, h=8, w=8)
        _, plain = self.run_sampler(latent, highres_tiling=False)
        with patch.object(TILED, "available_workspace", lambda p: 1e15):
            node, tiled = self.run_sampler(latent, highres_tiling=True)
        self.assertEqual(node.last_tiling["plan"]["tiles"], 1)
        for x, y in zip(streams(plain), streams(tiled)):
            self.assertTrue(torch.equal(x, y))

    def test_tiling_joint_strips_freeze_audio(self):
        latent = av_latent(t=7, h=8, w=24, seed=1)
        captured = {}
        real_run = TILED._run

        def spy(executor, state, req, stats, noise, latent_image, smp, sigmas, denoise_mask, *a, **k):
            captured["in_audio"] = latent_image.clone()
            captured["shapes"] = k.get("latent_shapes") or a[-1]
            return real_run(executor, state, req, stats, noise, latent_image, smp, sigmas, denoise_mask, *a, **k)

        with patch.object(TILED, "available_workspace", lambda p: 1.0), patch.object(TILED, "_run", spy):
            node, out = self.run_sampler(latent, highres_tiling=True, tiling_overlap=2)
        plan = node.last_tiling["plan"]
        self.assertGreater(plan["tiles"], 1)
        self.assertGreater(node.last_tiling["evals"], 0)
        v, a = streams(out)
        self.assertEqual(tuple(v.shape), (1, 24, 7, 8, 24))
        self.assertTrue(torch.isfinite(v).all() and torch.isfinite(a).all())
        # audio frozen: the output audio is the clean audio handed into the high-res pass
        import comfy.utils
        shapes = captured["shapes"]
        audio_in = comfy.utils.unpack_latents(captured["in_audio"], shapes)[1]
        self.assertTrue(torch.allclose(a.float(), audio_in.float(), atol=1e-4))

    def test_frozen_audio_mask(self):
        shapes = [(1, 24, 2, 4, 4), (1, 32, 2, 5)]
        m = TILED.frozen_audio_mask(None, shapes)
        nv = 24 * 2 * 4 * 4
        self.assertTrue(bool((m[..., :nv] == 1).all()) and bool((m[..., nv:] == 0).all()))
        dm = torch.cat([torch.zeros(1, 1, nv), torch.ones(1, 1, 32 * 2 * 5)], -1)
        m = TILED.frozen_audio_mask(dm, shapes)
        self.assertTrue(bool((m == 0).all()))


class ContinueTests(unittest.TestCase):
    def test_no_mode_input(self):
        spec = D.H3MDSegmentContinue.INPUT_TYPES()["required"]
        self.assertNotIn("mode", spec)
        self.assertIn("drift_strength", spec)


class CacheSeedTests(unittest.TestCase):
    def test_run_seed_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            c = C.SegmentCache(d, "abc" * 10, True)
            self.assertIsNone(c.run_seed())
            c.save_run_seed(123456789012)
            c2 = C.SegmentCache(d, "abc" * 10, True)
            self.assertEqual(c2.dir, c.dir)
            self.assertEqual(c2.run_seed(), 123456789012)

    def test_cache_version(self):
        self.assertTrue(C.H3MD_CACHE_VERSION.startswith("luna"))


class LoopSpecTests(unittest.TestCase):
    def test_loop_inputs(self):
        import h3md_loop as L
        import h3md_loop_split as LS
        spec = L.H3MDLoopSampler.INPUT_TYPES()
        allin = {**spec["required"], **spec["optional"]}
        for gone in ("continue_mode", "continued_tiling", "continued_tiling_overlap", "continued_tiling_fade",
                     "continued_tiling_method", "continued_tiling_tiles", "tiling_mode", "tiling_tiles",
                     "highres_tiling_mode", "highres_token_chunk", "highres_weight_cache_gb"):
            self.assertNotIn(gone, allin)
        self.assertEqual(spec["required"]["seed_mode"][1]["default"], "increment")
        main, opts = LS.split_spec(spec)
        self.assertIn("highres_tiling", main["optional"])
        self.assertIn("upscaler_model", main["required"])
        self.assertIn("tiling_overlap", opts["required"])
        self.assertIn("drift_strength", opts["required"])
        d = LS.option_defaults()
        self.assertEqual(d["tiling_overlap"], 8)

    def test_step_summary(self):
        import h3md_loop as L
        rows = [{"stage": "low_res", "step": i, "sec": s, "peak_gib": None} for i, s in enumerate((9, 1, 2, 1), 1)]
        out = L.step_summary(rows)
        self.assertEqual(out["low_res"]["steps"], 4)
        self.assertEqual(out["low_res"]["median_s"], 1)


if __name__ == "__main__":
    unittest.main()
