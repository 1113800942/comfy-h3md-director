"""window-exp (2026-11-07): plan windowMode / windowPrompts, cache keys, per-window prompt pick, guide crop."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402
import h3md_window as WIN  # noqa: E402
import h3md_cache as C  # noqa: E402

LONG = 5 + 17 * 40   # 685 frames: far over one window and over the autosplit budget


def plan(window=None, wps=None, length=LONG):
    seg = {"length": length, "prompt": "Duration: 1 s.\n\nshot", "overlapIn": 0}
    if wps:
        seg["windowPrompts"] = wps
    raw = {"version": 1, "width": 1920, "height": 1088, "globalPrompt": "g", "segments": [seg],
           "media": {"images": [], "videos": [], "audios": []}}
    if window is not None:
        raw["windowMode"] = window
    return json.dumps(raw)


class Normalize(unittest.TestCase):
    def test_defaults_and_snap(self):
        self.assertIsNone(WIN.normalize_window_mode(None))
        self.assertIsNone(WIN.normalize_window_mode(False))
        wm = WIN.normalize_window_mode(True)
        self.assertEqual((wm["length"], wm["overlap"]), (124, 56))
        wm = WIN.normalize_window_mode({"length": 130, "overlap": 40})
        self.assertEqual((wm["length"], wm["overlap"]), (124, 39))

    def test_window_prompts(self):
        wps = WIN.normalize_window_prompts([{"at": 5, "prompt": "b"}, {"at": 0.4, "prompt": "a"},
                                            {"at": 99, "prompt": "late"}], 345, lambda t: "G " + t)
        self.assertEqual([(w["start"], w["end"], w["prompt"]) for w in wps], [(0, 120, "G a"), (120, 345, "G b")])
        with self.assertRaises(ValueError):
            WIN.normalize_window_prompts([{"at": 1}], 345, str)


class PlanParse(unittest.TestCase):
    def test_off_unchanged(self):
        p = D.parse_plan(plan())
        self.assertNotIn("windowMode", p)
        self.assertNotIn("windowPrompts", p["segments"][0])

    def test_window_mode_no_autosplit(self):
        p = D.parse_plan(plan(True, [{"at": 0, "prompt": "a"}, {"at": 10, "prompt": "b"}]))
        self.assertEqual(len(p["segments"]), 1)
        self.assertEqual(p["windowMode"]["overlap"], 56)
        wps = p["segments"][0]["windowPrompts"]
        self.assertEqual([(w["start"], w["end"]) for w in wps], [(0, 240), (240, LONG)])
        self.assertTrue(wps[1]["prompt"].startswith("g"))
        self.assertTrue(any("window mode" in n for n in p["notes"]))

    def test_window_prompts_ignored_when_off(self):
        p = D.parse_plan(plan(None, [{"at": 0, "prompt": "a"}, {"at": 10, "prompt": "b"}], length=345))
        self.assertNotIn("windowPrompts", p["segments"][0])
        self.assertTrue(any("windowPrompts ignored" in n for n in p["notes"]))

    def test_cache_key(self):
        off = C.segment_signature(D.parse_plan(plan(length=345)), 0)
        self.assertNotIn("windowMode", off)
        on = C.segment_signature(D.parse_plan(plan(True, [{"at": 0, "prompt": "a"}, {"at": 5, "prompt": "b"}],
                                                  length=345)), 0)
        self.assertIn("windowMode", on)
        self.assertIn("windowPrompts", on)

    def test_window_prompts_for(self):
        p = D.parse_plan(plan(True, [{"at": 0, "prompt": "a"}, {"at": 10, "prompt": "b"}]))
        self.assertEqual(len(D.window_prompts_for(p, p["segments"][0])), 2)
        short = D.parse_plan(plan(True, [{"at": 0, "prompt": "a"}, {"at": 2, "prompt": "b"}], length=124))
        one = D.window_prompts_for(short, short["segments"][0])
        self.assertEqual(len(one), 1)


if __name__ == "__main__":
    unittest.main()
