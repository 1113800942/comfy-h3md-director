"""2026-10-29 notile-split: shots without reference video / RefMod are budgeted by their low-res canvas (high-res
runs in strips); shots with a reference video keep the calibrated full-canvas estimate."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402
import h3md_autosplit as AS  # noqa: E402

BUDGET = 182576   # the user's 5090 budget from the 2026-10-29 log


def seg(length, videos=None, images=None):
    return {"index": 0, "length": length, "overlapIn": 0, "startFrame": 0, "videos": videos or [],
            "images": images or [], "audios": [], "prompt": "x", "audioMode": "reference", "locked": None}


class LightShots(unittest.TestCase):
    def test_text_shot_1080p_not_split(self):
        for length in (362, 396):
            notes = []
            out = AS.apply(D, [seg(length)], {"images": {}, "videos": {}, "audios": {}}, {}, 1920, 1088, "native",
                           notes, budget=BUDGET)
            self.assertEqual(len(out), 1, notes)
        full = D.canvas_tokens(1920, 1088, 362)
        self.assertEqual(full, 218280)                      # what split the shot before
        self.assertLess(AS.part_tokens(D, seg(362), {}, 1920, 1088, "native", 362), BUDGET // 3)

    def test_very_long_text_shot_still_split_with_warning(self):
        notes = []
        out = AS.apply(D, [seg(5 + 17 * 200)], {"images": {}, "videos": {}, "audios": {}}, {}, 1920, 1088, "native",
                       notes, budget=BUDGET)
        self.assertGreater(len(out), 1)
        self.assertTrue(any("replay its opening" in n for n in notes))

    def test_video_shot_keeps_full_estimate(self):
        s = seg(362, videos=[{"id": "v", "trimStart": 0.0}])
        sizes = {("videos", "v"): (1280, 720)}
        t = AS.part_tokens(D, s, sizes, 1920, 1088, "native", 362)
        self.assertGreaterEqual(t, 218280)


if __name__ == "__main__":
    unittest.main()
