"""2026-10-29: face refine with nothing to refine saves the video unrefined instead of failing.
0.5.9: the same when a face-refine part is missing (no detector, detector file missing).
0.6.6: the FaceRefine code and the default detector are built into H3MD; a missing restore model also saves plain."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: F401,E402  (CPU ComfyUI first)
import h3md_face_refine_save as F  # noqa: E402

PLAIN = {"ui": {"x": 1}, "result": ("out.mp4", 243, None, None)}


class FallbackTests(unittest.TestCase):
    def test_nothing_to_refine_saves_plain(self):
        node = F.H3MDSaveVideoFaceRefineInterp()

        def boom(*a, **k):
            raise F.NothingToRefine("Every shot is marked as not containing the subject")

        with patch.object(F, "refine_blocker", lambda *a: None), \
                patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom), \
                patch.object(F.S.H3MDSaveVideoInterp, "save", lambda self, **k: PLAIN):
            res = node.save(segments={"cache_dir": "x"}, face_refine=True)
        self.assertEqual(res["result"][0], "out.mp4")
        self.assertIn("SKIPPED", res["result"][3])

    def test_other_errors_still_raise(self):
        node = F.H3MDSaveVideoFaceRefineInterp()

        def boom(*a, **k):
            raise ValueError("bad format")

        with patch.object(F, "refine_blocker", lambda *a: None), \
                patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom):
            with self.assertRaises(ValueError):
                node.save(segments={"cache_dir": "x"}, face_refine=True)

    def test_restore_model_missing_saves_plain(self):
        """0.6.6: tracking + detector are built in; without a VOSR2 model wired the video is saved without refine,
        the reason in the text (before: a ValueError when the video was being saved, after all the sampling)"""
        node = F.H3MDSaveVideoFaceRefineInterp()

        def never(*a, **k):
            raise AssertionError("refine must not run")

        with patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", never), \
                patch.object(F.S.H3MDSaveVideoInterp, "save", lambda self, **k: PLAIN):
            res = node.save(segments={"cache_dir": "x"}, face_refine=True)
        self.assertIn("SKIPPED", res["result"][3])
        self.assertIn("VOSR2", res["result"][3])

    def test_blocker_reasons(self):
        B = F.FRP.BUILTIN
        self.assertEqual(B, "face_yolov8n_v2.pt")
        self.assertIn("none", F.refine_blocker("none"))
        self.assertIsNone(F.refine_blocker(B))                       # built in: no file, no pack, no pip package
        self.assertIsNone(F.refine_blocker(B, "VOSR2", object()))
        self.assertIn("VOSR2", F.refine_blocker(B, "VOSR2", None))
        self.assertIn("seedvr2", F.refine_blocker(B, "SeedVR2", None, object(), None))
        self.assertIsNone(F.refine_blocker(B, "SeedVR2", None, object(), object()))
        with patch.object(F.MP, "find_detector", lambda n: None):
            self.assertIn("models/ultralytics/bbox", F.refine_blocker("face_yolov8m.pt"))
        with patch.object(F.MP, "find_detector", lambda n: "C:/m/face_yolov8m.pt"):
            with patch.object(F.FRP, "ultralytics_problem", lambda: None):
                self.assertIsNone(F.refine_blocker("face_yolov8m.pt"))
            with patch.object(F.FRP, "ultralytics_problem", lambda: "the ultralytics package is not installed"):
                self.assertIn("ultralytics", F.refine_blocker("face_yolov8m.pt"))

    def test_built_in_module_and_widget_lists(self):
        """0.6.6: the FaceRefine code is H3MD's own copy - nothing behind ComfyUI's node registry is needed"""
        with patch.object(F, "_registry", lambda: {}), \
                patch.object(F.MP, "detector_files", lambda: ["face_yolov8n.pt", F.FRP.BUILTIN]):
            fr = F._fr_module()
            track, stitch = F._fr_specs()
        self.assertFalse([n for n in F._FR_NEEDED if not hasattr(fr, n)])
        self.assertIs(F._stitch_class(), fr.H3FaceStitch)
        det = track["detector"]
        self.assertEqual(det[0], [F.FRP.BUILTIN, "face_yolov8n.pt", "none"])     # built-in first, once; none stays valid
        self.assertEqual(det[1]["default"], F.FRP.BUILTIN)
        self.assertIn("auto (pyscenedetect)", track["cut_detection"][0])
        self.assertEqual(track["select"][1]["default"], "largest_face")
        self.assertIn("paste_region", stitch)
        # the pack's node classes are not registered by H3MD (the pack can be installed next to it)
        import h3md_facerefine as FRP
        self.assertFalse(hasattr(FRP, "NODE_CLASS_MAPPINGS"))

    def test_built_in_detector_finds_nothing_in_a_blank_frame_and_keeps_the_interface(self):
        import numpy as np
        det = F._fr_module()._load_detector(F.FRP.BUILTIN)
        res = det.predict(np.full((360, 640, 3), 90, dtype=np.uint8), conf=0.35, verbose=False)[0]
        self.assertEqual(len(res.boxes), 0)
        self.assertEqual(res.boxes.xyxy.tolist(), [])
        self.assertIsNotNone(getattr(res.boxes, "conf", None))
        self.assertIsNotNone(getattr(res.boxes, "cls", None))
        self.assertIs(F._fr_module()._load_detector(F.FRP.BUILTIN), det)        # loaded once
        g = F.FRP.DET.letterbox_geometry(1080, 1920)                            # r, new w, new h, left, top
        self.assertEqual(g[1:], (640, 360, 0, 140))


if __name__ == "__main__":
    unittest.main()
