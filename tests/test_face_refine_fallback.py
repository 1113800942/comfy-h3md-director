"""2026-10-29: face refine with nothing to refine saves the video unrefined instead of failing."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: F401,E402  (CPU ComfyUI first)
import h3md_face_refine_save as F  # noqa: E402


class FallbackTests(unittest.TestCase):
    def test_nothing_to_refine_saves_plain(self):
        node = F.H3MDSaveVideoFaceRefineInterp()
        plain = {"ui": {"x": 1}, "result": ("out.mp4", 243, None, None)}

        def boom(*a, **k):
            raise F.NothingToRefine("Every shot is marked as not containing the subject")

        with patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom), \
                patch.object(F.S.H3MDSaveVideoInterp, "save", lambda self, **k: plain):
            res = node.save(segments={"cache_dir": "x"}, face_refine=True)
        self.assertEqual(res["result"][0], "out.mp4")
        self.assertIn("SKIPPED", res["result"][3])

    def test_other_errors_still_raise(self):
        node = F.H3MDSaveVideoFaceRefineInterp()

        def boom(*a, **k):
            raise ValueError("bad format")

        with patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", boom):
            with self.assertRaises(ValueError):
                node.save(segments={"cache_dir": "x"}, face_refine=True)


if __name__ == "__main__":
    unittest.main()
