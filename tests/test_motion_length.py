"""v0.3.5: motion copy (TARA, 'motion_transfer:') shots that follow their video are rounded DOWN at the clip end;
other shots keep rounding up; the 'Duration:' line follows the real segment length."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402

PROMPT = "Duration: 229.71 s (5513 frames).\n\nsubject_definitions:\n<Subject 1> is ..."


def plan(global_prompt, dur=229.8, extra=None):
    seg = {"length": 345, "overlapIn": 0, "images": [], "videos": [{"id": "v", "trimStart": 0}],
           "audios": [], "prompt": PROMPT}
    seg.update(extra or {})
    return json.dumps({"version": 1, "fps": 24, "width": 1280, "height": 704, "globalPrompt": global_prompt,
                       "media": {"images": [], "videos": [{"id": "v", "file": "v.mp4", "duration": dur}],
                                 "audios": []},
                       "autoSplit": False, "segments": [seg]})


class MotionLength(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(D.aligned_up(5515.2), 5530)
        self.assertEqual(D.aligned_down(5515.2), 5513)
        self.assertEqual(D.aligned_up(12.041667 * 24 + 39), 328)      # float noise no longer rounds up a step
        self.assertEqual(D.fit_video_length(0, 5515.2, 5515.2, motion=True), 5513)
        self.assertEqual(D.fit_video_length(0, 5515.2, 5515.2, motion=False), 5530)
        self.assertEqual(D.fit_video_length(0, 300.0, 5515.2, motion=True), 311)   # inside the clip: up

    def test_motion_copy_rounds_down(self):
        out = D.parse_plan(plan("motion_transfer:"), 1280, 704)
        self.assertEqual(out["segments"][0]["length"], 5513)
        self.assertIn("Duration: 229.71 s (5513 frames).", out["segments"][0]["prompt"])

    def test_normal_rounds_up(self):
        out = D.parse_plan(plan(""), 1280, 704)
        self.assertEqual(out["segments"][0]["length"], 5530)
        self.assertIn("Duration: 230.42 s (5530 frames).", out["segments"][0]["prompt"])

    def test_duration_line(self):
        self.assertEqual(D.fix_duration_line("no line here", 345, 0), "no line here")
        p = D.fix_duration_line("motion_transfer:\n\nDuration: 1 s (1 frames).\nbody", 311, 39)
        self.assertIn("Duration: 12.96 s (311 frames; the first 39 frames continue the previous shot).", p)


if __name__ == "__main__":
    unittest.main()
