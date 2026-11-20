"""2026-10-29: face refine with nothing to refine saves the video unrefined instead of failing.
0.5.9: the same when a face-refine part is missing (FaceRefine not loaded, no detector, detector file missing)."""
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

        with patch.object(F, "refine_blocker", lambda d: None), \
                patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom), \
                patch.object(F.S.H3MDSaveVideoInterp, "save", lambda self, **k: PLAIN):
            res = node.save(segments={"cache_dir": "x"}, face_refine=True)
        self.assertEqual(res["result"][0], "out.mp4")
        self.assertIn("SKIPPED", res["result"][3])

    def test_other_errors_still_raise(self):
        node = F.H3MDSaveVideoFaceRefineInterp()

        def boom(*a, **k):
            raise ValueError("bad format")

        with patch.object(F, "refine_blocker", lambda d: None), \
                patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom):
            with self.assertRaises(ValueError):
                node.save(segments={"cache_dir": "x"}, face_refine=True)

    def test_facerefine_missing_saves_plain(self):
        """0.5.9 (Bilibili): ComfyUI-H3-FaceRefine not loaded -> saved without refine, the reason in the text"""
        node = F.H3MDSaveVideoFaceRefineInterp()

        def never(*a, **k):
            raise AssertionError("refine must not run")

        with patch.object(F, "_registry", lambda: {}), \
                patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", never), \
                patch.object(F.S.H3MDSaveVideoInterp, "save", lambda self, **k: PLAIN):
            res = node.save(segments={"cache_dir": "x"}, face_refine=True)
        self.assertIn("SKIPPED", res["result"][3])
        self.assertIn("H3-FaceRefine", res["result"][3])

    def test_blocker_reasons(self):
        fake = type("M", (), {n: None for n in F._FR_NEEDED})
        cls = type("H3FaceTrackCrop", (), {"__module__": "fake_fr"})
        with patch.dict(sys.modules, {"fake_fr": fake}), \
                patch.object(F, "_registry", lambda: {"H3FaceTrackCrop": cls}):
            self.assertIn("none", F.refine_blocker("none"))
            with patch.object(F.MP, "find_detector", lambda n: None):
                self.assertIn("models/ultralytics/bbox", F.refine_blocker("face_yolov8n.pt"))
            with patch.object(F.MP, "find_detector", lambda n: "C:/m/face_yolov8n.pt"):
                self.assertIsNone(F.refine_blocker("face_yolov8n.pt"))

    def test_fallback_lists_keep_choices(self):
        """FaceRefine missing: detector files and both cut modes stay selectable (a saved workflow is not red)"""
        with patch.object(F, "_registry", lambda: {}), \
                patch.object(F.MP, "detector_files", lambda: ["face_yolov8n.pt"]):
            track, _ = F._fr_specs()
        self.assertIn("face_yolov8n.pt", track["detector"][0])
        self.assertIn("auto (pyscenedetect)", track["cut_detection"][0])


if __name__ == "__main__":
    unittest.main()
