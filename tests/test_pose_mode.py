"""H3MD pose mode (2026-11-04, branch pose-exp): control_kind depth / pose on the same node and Loop path.

CPU only, no ComfyUI.  Run from the repo root:  python -m pytest -q tests/test_pose_mode.py
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import torch  # noqa: E402

import h3md_director as D  # noqa: E402
import h3md_depth_control as DC  # noqa: E402

OLD_DEPTH_KEY = {"strength": 0.7, "start": 0.0, "end": 0.6, "control": DC.DEFAULT_CONTROL,
                 "depth_model": DC.DEFAULT_DEPTH, "resolution": 512,
                 "sam3": {"enabled": False, "threshold": 0.3}, "fit": DC.FIT_MODE}


def node_dc(**kw):
    args = dict(control_model=DC.DEFAULT_CONTROL, vae=None)
    args.update(kw)
    return DC.H3MDDepthControl().build(**args)[0]


class TestKeys(unittest.TestCase):
    def test_depth_key_unchanged(self):
        self.assertEqual(DC.depth_settings(node_dc()), OLD_DEPTH_KEY)
        # an old saved dict without control_kind = depth, same key
        old = {k: v for k, v in node_dc().items() if not k.startswith(("control_kind", "pose_"))}
        self.assertEqual(DC.depth_settings(old), OLD_DEPTH_KEY)
        # pose options do not touch the depth key
        self.assertEqual(DC.depth_settings(node_dc(pose_engine="dwpose", pose_face=True)), OLD_DEPTH_KEY)

    def test_pose_key_differs(self):
        a = DC.depth_settings(node_dc(control_kind="pose"))
        self.assertEqual(a["kind"], "pose")
        self.assertNotIn("depth_model", a)
        self.assertNotEqual(a, OLD_DEPTH_KEY)
        b = DC.depth_settings(node_dc(control_kind="pose", pose_engine="dwpose"))
        c = DC.depth_settings(node_dc(control_kind="pose", pose_hands=False))
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        # sdpose ignores depth_resolution, dwpose uses it
        self.assertEqual(a, DC.depth_settings(node_dc(control_kind="pose", depth_resolution=1024)))
        self.assertNotEqual(b, DC.depth_settings(node_dc(control_kind="pose", pose_engine="dwpose",
                                                         depth_resolution=1024)))

    def test_bad_values_fall_back(self):
        d = node_dc(control_kind="nope", pose_engine="nope")
        self.assertEqual(d["control_kind"], "depth")
        self.assertEqual(d["pose_engine"], "sdpose")

    def test_old_node_unchanged(self):
        """H3MD Depth Control keeps exactly the 0.3.3 inputs and name (old workflows / caches untouched)."""
        spec = DC.H3MDDepthControl.INPUT_TYPES()
        allk = {**spec["required"], **spec["optional"]}
        for k in ("control_kind", "pose_engine", "pose_hands", "pose_face", "pose_feet"):
            self.assertNotIn(k, allk)
        self.assertEqual(list(spec["required"]), ["control_model", "vae", "strength", "start_percent", "end_percent",
                                                  "depth_model", "depth_resolution", "sam3_enabled", "sam3_threshold"])
        self.assertEqual(list(spec["optional"]), ["sam3_fill", "sam3_grow", "sam3_blur", "sam3_output"])
        self.assertEqual(DC.NODE_DISPLAY_NAME_MAPPINGS["H3MDDepthControl"], "H3MD Depth Control")

    def test_motion_node(self):
        spec = DC.H3MDMotionControl.INPUT_TYPES()
        self.assertEqual(spec["required"]["control_kind"][1]["default"], "pose")
        for k in ("pose_engine", "pose_hands", "pose_face", "pose_feet", "depth_model", "sam3_enabled"):
            self.assertIn(k, spec["required"])
        self.assertEqual(DC.H3MDMotionControl.RETURN_TYPES, (DC.DEPTH_TYPE,))
        self.assertIn("H3MDMotionControl", DC.NODE_CLASS_MAPPINGS)
        d = DC.H3MDMotionControl().build(control_model="x", vae=None, control_kind="pose")[0]
        self.assertEqual(DC.control_kind(d), "pose")
        d = DC.H3MDMotionControl().build(control_model=DC.DEFAULT_CONTROL, vae=None, control_kind="depth")[0]
        self.assertEqual(DC.depth_settings(d), OLD_DEPTH_KEY)


class TestPrepare(unittest.TestCase):
    MEDIA = {"images": {"p1": {"file": "a.png"}}, "videos": {"v": {"file": "v.mp4", "duration": 10}},
             "audios": {"m": {"file": "m.mp3"}}}

    def plan(self, sam3=False):
        v = {"id": "v", "trimStart": 0, "useSoundtrack": False, "depth": True}
        if sam3:
            v["sam3Prompt"] = "woman"
            v["sam3"] = True
        seg = {"images": ["p1"], "videos": [v], "audios": ["m"], "audioMode": "reference", "length": 107}
        return {"media": self.MEDIA, "segments": [seg]}

    def loop(self, dc, plan):
        calls = {"depth": 0, "pose": [], "sam3": 0}

        def run_depth(frames, m, r):
            calls["depth"] += 1
            return torch.full_like(frames, 0.5)

        def run_pose(frames, opts):
            calls["pose"].append(dict(opts))
            return torch.zeros_like(frames)

        def run_sam3(*a):
            calls["sam3"] += 1
            return torch.ones(a[0].shape[:3])

        load = lambda f, o, d, max_frames: torch.rand(max_frames, 32, 24, 3)
        ld = DC.LoopDepth(dc, plan, loaders={"run_depth": run_depth, "run_pose": run_pose, "run_sam3": run_sam3,
                                              "load_video": load})
        return ld, calls

    def run_prepare(self, ld, plan):
        seg = plan["segments"][0]
        ref = DC.segment_depth_ref(seg)
        al = {"mode": "fresh", "overlap": 0, "why": "test"}
        return ld._prepare(plan, 0, seg, ref, al, "seg 1", D)

    def test_depth_path(self):
        p = self.plan()
        ld, calls = self.loop(node_dc(), p)
        st = self.run_prepare(ld, p)
        self.assertEqual((calls["depth"], len(calls["pose"])), (1, 0))
        self.assertEqual(st["kind"], "depth")
        self.assertTrue(st["drop_video"])

    def test_pose_path(self):
        p = self.plan(sam3=True)
        dc = node_dc(control_kind="pose", sam3_enabled=True, pose_face=True)
        ld, calls = self.loop(dc, p)
        st = self.run_prepare(ld, p)
        self.assertEqual((calls["depth"], calls["sam3"]), (0, 0))   # no depth model, no SAM3 in pose mode
        self.assertEqual(calls["pose"][0]["engine"], "sdpose")
        self.assertTrue(calls["pose"][0]["face"])
        self.assertEqual(st["kind"], "pose")
        self.assertEqual(st["depth"].shape[1:3], (32, 24))
        seg_key = ld.segment_cache_settings(p, 0)
        self.assertEqual(seg_key["kind"], "pose")
        self.assertNotIn("sam3_mask", seg_key)

    def test_missing_dwpose_node_message(self):
        import types
        fake = types.ModuleType("nodes")
        fake.NODE_CLASS_MAPPINGS = {}
        old = sys.modules.get("nodes")
        sys.modules["nodes"] = fake
        try:
            with self.assertRaises(RuntimeError) as cm:
                DC.run_pose(torch.zeros(1, 8, 8, 3), DC.pose_options({"pose_engine": "dwpose"}))
            self.assertIn("DWPreprocessor", str(cm.exception))
        finally:
            if old is None:
                sys.modules.pop("nodes", None)
            else:
                sys.modules["nodes"] = old


class TestRerunClip(unittest.TestCase):
    def test_range_past_end_is_cut(self):
        import h3_harness  # noqa: F401
        import h3md_loop as L
        self.assertEqual(L.parse_segments("1-2", 1), [0])
        self.assertEqual(L.parse_segments("2-9", 3), [1, 2])
        with self.assertRaises(ValueError):
            L.parse_segments("3", 1)


class TestLoraSig(unittest.TestCase):
    def test_sig_recorded_and_read(self):
        import h3_harness  # noqa: F401
        import h3md_loop as L
        import h3md_lora as HL

        class M:
            def __init__(self):
                self.model_options = {}
        a, b = M(), M()
        self.assertEqual(L.model_lora_sig(a, b), {})            # nothing recorded -> old keys
        self.assertEqual(L.model_lora_sig(object(), None), {})
        HL.add_lora_sig(a, ("bypass", "x.safetensors", 1.0, "replace_like_comfy"))
        first = a.model_options[HL.LORA_SIG]
        HL.add_lora_sig(a, ("lora", "y.safetensors", 0.5))
        self.assertEqual(len(first), 1)                           # earlier list not mutated (clones stay apart)
        self.assertEqual(L.model_lora_sig(a, b), {"low": [["bypass", "x.safetensors", 1.0, "replace_like_comfy"],
                                                          ["lora", "y.safetensors", 0.5]]})
        HL.add_lora_sig(object(), ("lora", "z", 1))              # objects without model_options: ignored


if __name__ == "__main__":
    unittest.main()
