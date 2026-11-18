"""2026-11-15: Remake engine — official 6-step on low only, high-res schedule untouched, remake_model required."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import torch  # noqa: E402
import h3md_director as D  # noqa: E402
import h3md_cache as C  # noqa: E402
import h3md_sampler as S  # noqa: E402
import h3md_remake_engine as SWAP  # noqa: E402
from h3md_luna import luna_two_pass as L  # noqa: E402

IMAGES = [{"id": "k1", "file": "k1.png"}, {"id": "k2", "file": "k2.png"}]
VIDEOS = [{"id": "src", "file": "src.mp4", "duration": 10.125, "hasAudio": True}]


def raw_plan(swap="remake", window=None, w=1664, h=960, kfs=None, mode=None):
    raw = {"version": 1, "width": w, "height": h, "globalPrompt": "",
           "media": {"images": IMAGES, "videos": VIDEOS, "audios": []},
           "segments": [{"length": 243, "overlapIn": 0, "prompt": "swap prompt", "images": [],
                         "videos": [{"id": "src", "trimStart": 0, "useSoundtrack": False}], "audios": [],
                         "audioMode": "locked", "locked": {"source": "video:src", "offset": 0},
                         "keyframes": kfs if kfs is not None else [{"image": "k1", "at": 0}, {"image": "k2", "at": 5}]}]}
    if swap is not None:
        raw["swapEngine"] = swap
    if mode is not None:
        raw["remakeMode"] = mode
    if window is not None:
        raw["windowMode"] = window
    return raw


class Plan(unittest.TestCase):
    def test_passthrough(self):
        p = D.parse_plan(json.dumps(raw_plan()))
        self.assertEqual(p["swapEngine"], "remake")
        self.assertEqual(SWAP.engine(p), "remake")
        self.assertEqual(SWAP.remake_mode(p), "swap")
        self.assertNotIn("remakeMode", p)  # default swap is omitted so off/default keys stay lean

    def test_off_and_unknown(self):
        self.assertNotIn("swapEngine", D.parse_plan(json.dumps(raw_plan(swap=None))))
        p = D.parse_plan(json.dumps(raw_plan(swap="viggle9")))
        self.assertNotIn("swapEngine", p)
        self.assertIn("swapEngine 'viggle9' unknown", "\n".join(p["notes"]))
        self.assertIsNone(SWAP.engine({}))
        self.assertIsNone(SWAP.engine({"swapEngine": "off"}))

    def test_window_mode_off(self):
        p = D.parse_plan(json.dumps(raw_plan(window={"length": 243, "overlap": 22})))
        self.assertNotIn("windowMode", p)
        self.assertIn("window mode off", "\n".join(p["notes"]))

    def test_cache_key(self):
        a = C.segment_signature(D.parse_plan(json.dumps(raw_plan())), 0)
        b = C.segment_signature(D.parse_plan(json.dumps(raw_plan(swap=None))), 0)
        self.assertEqual(a.get("swapEngine"), "remake")
        self.assertEqual(a.get("remakeSchedule"), "official6-low")
        self.assertNotIn("swapEngine", b)   # plans without the swap engine keep their old keys
        self.assertNotIn("remakeSchedule", b)
        self.assertNotIn("remakeMode", b)

    def test_edit_mode_recorded_not_invented(self):
        p = D.parse_plan(json.dumps(raw_plan(mode="edit")))
        self.assertEqual(p.get("remakeMode"), "edit")
        with self.assertRaises(ValueError) as ctx:
            SWAP.low_sigmas(p)
        self.assertIn("edit", str(ctx.exception).lower())


class Canvas(unittest.TestCase):
    def test_short_edge(self):
        self.assertEqual(SWAP.low_short_edge(1664, 960), 480)
        self.assertEqual(SWAP.low_short_edge(960, 1664), 480)
        self.assertEqual(SWAP.low_short_edge(2560, 1472), 736)
        self.assertEqual(SWAP.low_short_edge(1920, 1088), 544)

    def test_check(self):
        p = D.parse_plan(json.dumps(raw_plan()))
        self.assertEqual(SWAP.check(p, p["segments"][0]), 480)
        bad = dict(p, width=1920, height=1088)
        with self.assertRaises(ValueError):
            SWAP.check(bad, bad["segments"][0])

    def test_size_forced_for_swap(self):
        # 0.5.5: a Control Panel size Remake can't run is replaced (plan size if valid, else 1664x960 by orientation)
        p = D.parse_plan(json.dumps(raw_plan()), width=1920, height=1088)
        self.assertEqual((p["width"], p["height"]), (1664, 960))
        p = D.parse_plan(json.dumps(raw_plan(w=1088, h=1920)), width=1088, height=1920)
        self.assertEqual((p["width"], p["height"]), (960, 1664))
        p = D.parse_plan(json.dumps(raw_plan(w=960, h=1664)), width=1920, height=1088)
        self.assertEqual((p["width"], p["height"]), (960, 1664))
        p = D.parse_plan(json.dumps(raw_plan()), width=2560, height=1472)
        self.assertEqual((p["width"], p["height"]), (2560, 1472))
        self.assertEqual(SWAP.check(p, p["segments"][0]), 736)
        p = D.parse_plan(json.dumps(raw_plan(swap=None, w=1920, h=1088)))
        self.assertEqual((p["width"], p["height"]), (1920, 1088))


class Schedule(unittest.TestCase):
    def test_official_six_step_low_only(self):
        low = SWAP.low_sigmas({"swapEngine": "remake"})
        self.assertEqual([round(float(x), 6) for x in low],
                         [1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0])
        # 6 forwards; keyframes stay on the first four (sigma >= 0.9)
        self.assertEqual(len(low) - 1, 6)
        self.assertEqual(sum(float(s) >= SWAP.KEYFRAME_UNTIL for s in low[:-1]), 4)

    def test_high_res_uses_original_split(self):
        original = torch.tensor([1.0, 0.9, 0.8, 0.6, 0.3, 0.0])
        high = SWAP.high_sigmas_from(original, 2)
        _low, expected = S.split_sigmas(original, 2)
        self.assertEqual([round(float(x), 6) for x in high], [round(float(x), 6) for x in expected])
        self.assertEqual([round(float(x), 6) for x in high], [0.8, 0.6, 0.3, 0.0])
        # official Remake terminal 0.315789 splice must not appear on the high arm
        self.assertNotIn(0.315789, [round(float(x), 6) for x in high])
        self.assertNotIn(0.631579, [round(float(x), 6) for x in high])

    def test_audio_handover_zero_sigma_is_gated(self):
        # default path still forbids dividing by sigma 0; remake complete-low uses a separate branch
        x0 = torch.ones(2)
        state = torch.ones(2)
        _x0, eps0 = L.audio_handover(state, x0, 0.0, 0.8, 1.0, 1.0)
        self.assertFalse(torch.isfinite(eps0).all())  # default formula is undefined at sigma 0
        x0_vae, eps = L.audio_handover(state * 0.2, x0, 0.2, 0.8, 1.0, 1.0)
        self.assertEqual(float(x0_vae[0]), 1.0)
        self.assertTrue(torch.isfinite(eps).all())


class Isolation(unittest.TestCase):
    def test_require_remake_model(self):
        with self.assertRaises(ValueError) as ctx:
            SWAP.require_remake_model(None, object(), object())
        msg = str(ctx.exception)
        self.assertIn("remake_model", msg)
        self.assertIn("回落", msg)
        low = object()
        high = object()
        with self.assertRaises(ValueError):
            SWAP.require_remake_model(low, low, high)
        with self.assertRaises(ValueError):
            SWAP.require_remake_model(high, low, high)
        ok = object()
        self.assertIs(SWAP.require_remake_model(ok, low, high), ok)

    def test_reject_turbo_signature(self):
        class M:
            model_options = {"h3md_lora_sig": [("lora", "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors", 1.0)]}
        with self.assertRaises(ValueError) as ctx:
            SWAP.require_remake_model(M(), object(), object())
        self.assertIn("Turbo", str(ctx.exception))


class FakeRemake:
    calls = []

    def run(self, clip, vae, audio_vae, source, prompt, ref_1, pins, frames, short_edge, audio=None, **refs):
        FakeRemake.calls.append({"source": tuple(source.shape), "pins": pins, "frames": frames, "se": short_edge,
                                 "refs": 1 + len(refs), "prompt": prompt})
        h, w = (480, 832)
        kf = [{"resolved_frame_index": int(p), "latent": torch.zeros(1, 24, 1, h // 16, w // 16)}
              for p in pins.split(", ")]
        cond = [[torch.zeros(1, 4, 8), {"minimax_refs": [{"kind": "video"}], "minimax_keyframes": kf}]]
        import comfy.nested_tensor
        video = torch.zeros(1, 24, D.video_tokens(frames), h // 16, w // 16)
        za = torch.zeros(1, 32, 2, D.audio_tokens(frames))
        return cond, {"samples": comfy.nested_tensor.NestedTensor((video, za))}


class Encode(unittest.TestCase):
    def setUp(self):
        import nodes
        self._old = dict(nodes.NODE_CLASS_MAPPINGS)
        nodes.NODE_CLASS_MAPPINGS["RemakeConditioning"] = FakeRemake
        FakeRemake.calls = []

    def tearDown(self):
        import nodes
        nodes.NODE_CLASS_MAPPINGS.clear()
        nodes.NODE_CLASS_MAPPINGS.update(self._old)

    def test_encode(self):
        p = D.parse_plan(json.dumps(raw_plan()))
        seg = p["segments"][0]
        picked = {"videos": [torch.rand(230, 488, 896, 3)], "images": [], "locked_audio": None}
        pos, lat = SWAP.encode(p, seg, picked, "swap prompt", None, None, None, D.segment_keyframes(seg),
                               lambda f: torch.rand(5, 480, 832, 3))
        call = FakeRemake.calls[0]
        self.assertEqual(call["source"][0], 243)            # held last frame up to the segment length
        self.assertEqual((call["pins"], call["frames"], call["se"], call["refs"]), ("0, 120", 243, 480, 2))
        self.assertTrue(all(k[SWAP.LOW_ONLY_TAG] for k in pos[0][1]["minimax_keyframes"]))
        self.assertIn("minimax_refs", pos[0][1])  # ordinary refs are kept; KeyframeUntil drops only keyframes
        video, audio = lat["samples"].unbind()
        self.assertEqual(tuple(video.shape), (1, 24, D.video_tokens(243), 960 // 16, 1664 // 16))
        # pass 2 drops them, pass 1 keeps them on the low grid (same 832x480 canvas: unchanged)
        self.assertNotIn("minimax_keyframes", L._without_low_only_keyframes(pos)[0][1])
        self.assertEqual(len(L._keyframes_at(pos, 30, 52)[0][1]["minimax_keyframes"]), 2)

    def test_pin_out_of_segment_rejected(self):
        p = D.parse_plan(json.dumps(raw_plan()))
        picked = {"videos": [torch.rand(243, 480, 832, 3)], "images": [], "locked_audio": None}
        with self.assertRaises(ValueError) as ctx:
            SWAP.encode(p, p["segments"][0], picked, "x", None, None, None,
                        [{"image": "k1", "frame": 243}], lambda f: torch.rand(1, 480, 832, 3))
        self.assertIn("243", str(ctx.exception))

    def test_needs_keyframes_and_node(self):
        p = D.parse_plan(json.dumps(raw_plan(kfs=[])))
        picked = {"videos": [torch.rand(243, 480, 832, 3)], "images": [], "locked_audio": None}
        with self.assertRaises(ValueError):
            SWAP.encode(p, p["segments"][0], picked, "x", None, None, None, [], lambda f: None)
        import nodes
        nodes.NODE_CLASS_MAPPINGS.pop("RemakeConditioning")
        p = D.parse_plan(json.dumps(raw_plan()))
        with self.assertRaises(RuntimeError):
            SWAP.encode(p, p["segments"][0], picked, "x", None, None, None, D.segment_keyframes(p["segments"][0]),
                        lambda f: torch.rand(1, 480, 832, 3))


REMAKE = HERE.parent.parent / "ComfyUI-Viggle-Remake" / "nodes.py"


@unittest.skipUnless(REMAKE.exists(), "ComfyUI-Viggle-Remake not next to H3MD")
class RealKeyframeUntil(unittest.TestCase):
    def test_wrapper(self):
        spec = importlib.util.spec_from_file_location("viggle_remake_nodes", REMAKE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import nodes
        old = dict(nodes.NODE_CLASS_MAPPINGS)
        try:
            nodes.NODE_CLASS_MAPPINGS["RemakeKeyframeUntil"] = mod.RemakeKeyframeUntil
            m = SWAP.keyframe_until(h3_harness.tiny_h3_model())
            import comfy.patcher_extension
            found = m.get_wrappers(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, "viggle_remake_keyframe_until")
            self.assertTrue(found)
        finally:
            nodes.NODE_CLASS_MAPPINGS.clear()
            nodes.NODE_CLASS_MAPPINGS.update(old)




class WeightType(unittest.TestCase):
    def test_diffusion_models_files_are_lora_adapters(self):
        root = Path(r"E:\\ComfyUI_models\\diffusion_models")
        for name in ("viggle_remake_swap_comfyui.safetensors", "viggle_remake_edit_comfyui.safetensors"):
            path = root / name
            if not path.exists():
                self.skipTest("weight not present: " + str(path))
            self.assertEqual(SWAP.classify_remake_weight(path), "lora")

    def test_missing_model_message_warns_not_unet(self):
        with self.assertRaises(ValueError) as ctx:
            SWAP.require_remake_model(None, object(), object())
        msg = str(ctx.exception)
        self.assertIn("LoRA", msg)
        self.assertIn("UNET", msg)
        self.assertIn("bypass", msg.lower())


class WrapperIsolation(unittest.TestCase):
    def test_keyframe_until_drops_keys_keeps_refs_and_does_not_leak(self):
        if not REMAKE.exists():
            self.skipTest("ComfyUI-Viggle-Remake not next to H3MD")
        spec = importlib.util.spec_from_file_location("viggle_remake_nodes_iso", REMAKE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import nodes
        import comfy.patcher_extension
        old = dict(nodes.NODE_CLASS_MAPPINGS)
        try:
            nodes.NODE_CLASS_MAPPINGS["RemakeKeyframeUntil"] = mod.RemakeKeyframeUntil
            base = h3_harness.tiny_h3_model()
            low = SWAP.keyframe_until(base)
            key = "viggle_remake_keyframe_until"
            self.assertTrue(low.get_wrappers(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, key))
            self.assertFalse(base.get_wrappers(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, key))
            wrappers = low.get_wrappers(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, key)
            release = wrappers[0]
            seen = []

            def executor(x, timestep, context, transformer_options, minimax_payload=None, **kwargs):
                seen.append(minimax_payload)
                return x

            refs = [{"latent": torch.ones(1, 2), "kind": "image"}]
            payload = {"keyframes": [{"resolved_frame_index": 0}], "layout": "packed", "refs": refs}
            release(executor, torch.zeros(1), torch.tensor([950.0]), None, {}, minimax_payload=payload)
            release(executor, torch.zeros(1), torch.tensor([800.0]), None, {}, minimax_payload=payload)
            keep, drop = seen
            self.assertIsNotNone(keep.get("keyframes"))
            self.assertEqual(keep.get("layout"), "packed")
            self.assertIsNone(drop.get("keyframes"))
            self.assertIsNone(drop.get("layout"))
            self.assertEqual(len(drop.get("cond_video_latents") or []), 1)
            self.assertTrue(torch.equal(drop["cond_video_latents"][0], refs[0]["latent"]))
        finally:
            nodes.NODE_CLASS_MAPPINGS.clear()
            nodes.NODE_CLASS_MAPPINGS.update(old)

    def test_remake_complete_renoises_finite(self):
        x0 = torch.ones(2, 4)
        _x0, eps0 = L.audio_handover(x0, x0, 0.0, 0.8, 1.0, 1.0)
        self.assertFalse(torch.isfinite(eps0).all())
        import comfy.sample
        noise = comfy.sample.prepare_noise(x0, 123)
        self.assertTrue(torch.isfinite(noise).all())
        self.assertEqual(tuple(noise.shape), tuple(x0.shape))


if __name__ == "__main__":
    unittest.main()
