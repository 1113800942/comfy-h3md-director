"""0.6.3: RIFE frame interpolation is built in (h3md_rife/) - no ComfyUI-VFI, and a problem shows before the run."""
import os
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import h3_harness  # noqa: E402,F401  (CPU ComfyUI first)

import torch  # noqa: E402

import h3md_control_panel as CP  # noqa: E402
import h3md_rife as RIFE  # noqa: E402
import h3md_stream_save as S  # noqa: E402


class Weights(unittest.TestCase):
    def test_file_ships_and_is_the_official_one(self):
        self.assertIsNone(RIFE.problem())
        self.assertEqual(os.path.getsize(RIFE.weights_path()), RIFE.WEIGHTS_BYTES)
        self.assertEqual(RIFE.file_sha256(), RIFE.WEIGHTS_SHA256)

    def test_license_ships(self):
        text = (ROOT / "h3md_rife" / "LICENSE_RIFE.txt").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2021 hzwer", text)


class Inference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RIFE.unload()
        cls.it = S.load_interpolator("flownet.pkl", False)

    def test_wrapper_shape(self):
        it = self.it
        self.assertFalse(it.use_fp16)                      # CPU: never half precision
        self.assertEqual(it.device.type, "cpu")
        self.assertTrue(callable(it.model.inference_batch))
        self.assertIs(S.load_interpolator("flownet.pkl", False), it)   # cached

    def test_every_network_weight_came_from_the_file(self):
        from safetensors.torch import load_file
        have = set(load_file(RIFE.weights_path()))
        need = set(self.it.model.flownet.state_dict())
        self.assertFalse(need - have)

    def test_same_frame_twice_stays_that_frame(self):
        ramp = torch.linspace(0.0, 1.0, 128)
        a = torch.stack([ramp.view(1, 128).expand(128, 128), ramp.view(128, 1).expand(128, 128),
                         torch.full((128, 128), 0.5)]).unsqueeze(0)      # a smooth picture, no motion
        with torch.inference_mode():
            out = self.it.model.inference_batch(a, a, [0.5], scale=1.0)
        self.assertEqual(tuple(out.shape), (1, 3, 128, 128))
        self.assertLess(float((out - a).abs().mean()), 0.01)

    def test_moving_square_lands_in_between(self):
        a = torch.zeros(1, 3, 128, 128)
        b = torch.zeros(1, 3, 128, 128)
        a[:, :, 48:80, 24:56] = 1.0          # centre x = 40
        b[:, :, 48:80, 40:72] = 1.0          # centre x = 56
        with torch.inference_mode():
            out = self.it.model.inference_batch(a, b, [0.5], scale=1.0)
        self.assertTrue(torch.isfinite(out).all())
        col = out[0].mean(0).sum(0)          # brightness per column
        centre = float((col * torch.arange(128)).sum() / col.sum())
        self.assertGreater(centre, 44.0)
        self.assertLess(centre, 52.0)        # about 48 = half way, not a double image at 40 and 56 only

    def test_batch_equals_one_by_one(self):
        g = torch.Generator().manual_seed(5)
        a, b = torch.rand(2, 3, 128, 128, generator=g), torch.rand(2, 3, 128, 128, generator=g)
        with torch.inference_mode():
            both = self.it.model.inference_batch(a, b, [0.25, 0.75], scale=1.0)
            one = self.it.model.inference_batch(a[1:], b[1:], [0.75], scale=1.0)
        self.assertTrue(torch.allclose(both[1:], one, atol=1e-6))

    def test_save_chunk_path_runs_with_the_real_model(self):
        g = torch.Generator().manual_seed(7)
        frames = torch.rand(3, 40, 56, 3, generator=g)       # padded to 128 inside
        pos = S.interpolator_positions(self.it, 24.0, 48.0, 3)
        self.assertEqual(pos, S.target_positions(24.0, 48.0, 3))
        outs = S.interpolate_chunk(self.it, frames, pos, 1.0, 2)
        self.assertEqual(len(outs), 6)
        self.assertTrue(torch.equal(outs[0], frames[0]) and torch.equal(outs[2], frames[1]))
        self.assertEqual(tuple(outs[1].shape), (40, 56, 3))
        self.assertTrue(torch.isfinite(outs[1]).all())

    def test_cpu_fallback_context_restores_the_model(self):
        net = self.it.model.flownet
        with S.rife_on_cpu(self.it) as (dev, dt):
            self.assertEqual(dev.type, "cpu")
            self.assertEqual(next(net.parameters()).dtype, torch.float32)
        self.assertEqual(next(net.parameters()).device.type, self.it.device.type)


class Preflight(unittest.TestCase):
    def setUp(self):
        self._real = RIFE.weights_path

    def tearDown(self):
        RIFE.weights_path = self._real

    def test_all_good(self):
        self.assertIs(S.validate_interpolate(True), True)
        self.assertIs(S.validate_interpolate(False), True)
        self.assertIs(S.validate_interpolate(None), True)      # linked input
        self.assertIs(S.validate_interpolate([None]), True)
        CP.preflight(True, False)
        CP.preflight(False, False)

    def test_missing_weights_stop_the_run_at_the_start(self):
        RIFE.weights_path = lambda: os.path.join(str(HERE), "no_such_dir", "rife.safetensors")
        self.assertIn("missing", RIFE.problem())
        msg = S.validate_interpolate(True)
        self.assertIsInstance(msg, str)
        self.assertIn("interpolate is on", msg)
        self.assertIs(S.validate_interpolate(False), True)     # off: nothing to complain about
        with self.assertRaises(RuntimeError):
            CP.preflight(True, False)
        CP.preflight(False, False)

    def test_save_nodes_validate_before_the_run(self):
        import h3md_face_refine_save as FR
        for cls in (S.H3MDSaveVideoInterp, FR.H3MDSaveVideoFaceRefineInterp):
            self.assertIs(cls.VALIDATE_INPUTS(interpolate=True), True)
            self.assertIs(cls.VALIDATE_INPUTS(interpolate=None), True)
            self.assertIn("interpolate", cls.INPUT_TYPES()["required"])
        RIFE.weights_path = lambda: os.path.join(str(HERE), "no_such_dir", "rife.safetensors")
        for cls in (S.H3MDSaveVideoInterp, FR.H3MDSaveVideoFaceRefineInterp):
            self.assertIsInstance(cls.VALIDATE_INPUTS(interpolate=True), str)

    def test_old_model_name_still_loads(self):
        self.assertEqual(S.rife_model_names(), ["flownet.pkl"])
        self.assertIsNotNone(S.load_interpolator("rife49.pth", False))


class NoOtherPack(unittest.TestCase):
    def test_no_pickle_file_ships(self):
        self.assertEqual([p.name for p in (ROOT / "h3md_rife").glob("*.pkl")], [])
        self.assertTrue(RIFE.weights_path().endswith(".safetensors"))

    def test_no_runtime_use_of_comfyui_vfi(self):
        for py in sorted(ROOT.glob("*.py")) + sorted((ROOT / "h3md_rife").glob("*.py")):
            src = py.read_text(encoding="utf-8")
            self.assertNotIn("RIFEInterpolation", src, py.name)
            self.assertIsNone(re.search(r"_get_or_load_model", src), py.name)


if __name__ == "__main__":
    unittest.main()
