"""0.4.0: H3MD Redraw / Preview node (low-res preview + local redraw outside the Director's editor)."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import h3_harness  # noqa: E402,F401  (CPU ComfyUI first)

import h3md_window as WIN  # noqa: E402

ROOT = HERE.parent


def plan(window=True, length=3364):
    p = {"width": 1920, "height": 1088, "fps": 24,
         "segments": [{"index": 0, "startFrame": 0, "length": 1280, "overlapIn": 0},
                      {"index": 1, "startFrame": 1280, "length": length - 1280, "overlapIn": 0}]}
    if window:
        p["windowMode"] = WIN.normalize_window_mode(True)
    return p


class ParseTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(WIN.parse_redraw(""), [])
        self.assertEqual(WIN.parse_redraw("4-8"), [[4.0, 8.0]])
        self.assertEqual(WIN.parse_redraw("4 – 8，1:12~1:15.5"), [[4.0, 8.0], [72.0, 75.5]])
        self.assertEqual(WIN.parse_redraw("20到24; 2.5 to 3"), [[2.5, 3.0], [20.0, 24.0]])
        self.assertEqual(WIN.parse_redraw("4-8, 6-10"), [[4.0, 10.0]])                 # overlapping ranges merge

    def test_clipped_to_the_video(self):
        self.assertEqual(WIN.parse_redraw("138-150", 140.167), [[138.0, 140.167]])

    def test_errors(self):
        for bad in ("abc", "8-4", "4", "4-8-9"):
            with self.assertRaises(ValueError, msg=bad):
                WIN.parse_redraw(bad)
        with self.assertRaises(ValueError):
            WIN.parse_redraw("150-160", 140.0)


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.node = WIN.H3MDRedrawPreview()

    def test_off_passes_the_plan_through(self):
        p = plan(window=False)
        self.assertIs(self.node.run(p, False, "")[0], p)

    def test_redraw_sets_window_mode_and_copies(self):
        p = plan()
        out = self.node.run(p, False, "20-24")[0]
        self.assertEqual(out["windowMode"]["redraw"], [[20.0, 24.0]])
        self.assertFalse(out["windowMode"]["preview"])
        self.assertIsNone(p["windowMode"]["redraw"])                     # the Director's plan is not changed
        self.assertEqual(out["windowMode"]["length"], p["windowMode"]["length"])
        self.assertEqual(WIN.redraw_ranges(out["windowMode"], 0, 1280), [(480, 576)])
        self.assertEqual(WIN.redraw_ranges(out["windowMode"], 1280, 2084), [])

    def test_preview(self):
        p = plan()
        p["windowMode"]["redraw"] = [[1, 2]]                               # a hand-written redraw is dropped
        out = self.node.run(p, True, "")[0]
        self.assertTrue(out["windowMode"]["preview"])
        self.assertIsNone(out["windowMode"]["redraw"])

    def test_needs_window_mode(self):
        with self.assertRaises(ValueError):
            self.node.run(plan(window=False), False, "4-8")
        with self.assertRaises(ValueError):
            self.node.run(plan(window=False), True, "")

    def test_not_both(self):
        with self.assertRaises(ValueError):
            self.node.run(plan(), True, "4-8")

    def test_registered(self):
        self.assertIn("H3MDRedrawPreview", WIN.NODE_CLASS_MAPPINGS)
        spec = WIN.H3MDRedrawPreview.INPUT_TYPES()["required"]
        self.assertEqual(list(spec), ["plan", "preview", "redraw"])
        self.assertEqual(WIN.H3MDRedrawPreview.RETURN_TYPES, ("H3MD_PLAN",))


class WorkflowTests(unittest.TestCase):
    """Both example workflows: the node sits between the Director and the Loop, bypassed (mode 4)."""

    def check(self, name, loop_id, loop_slot):
        w = json.loads((ROOT / "example_workflows" / name).read_text(encoding="utf-8"))
        nodes = {n["id"]: n for n in w["nodes"]}
        rp = [n for n in w["nodes"] if n["type"] == "H3MDRedrawPreview"]
        self.assertEqual(len(rp), 1)
        rp = rp[0]
        self.assertEqual(rp["mode"], 4)
        self.assertEqual(rp["widgets_values"], [False, ""])
        links = {l[0]: l for l in w["links"]}
        lin = links[rp["inputs"][0]["link"]]
        self.assertEqual(nodes[lin[1]]["type"], "H3MDDirectorRefMod")
        self.assertEqual(lin[5], "H3MD_PLAN")
        (lout,) = rp["outputs"][0]["links"]
        self.assertEqual(links[lout][3:6], [loop_id, loop_slot, "H3MD_PLAN"])
        self.assertEqual(nodes[loop_id]["inputs"][loop_slot]["link"], lout)
        self.assertLessEqual(max(n["id"] for n in w["nodes"]), w["last_node_id"])
        self.assertLessEqual(max(links), w["last_link_id"])

    def test_simple(self):
        self.check("H3MD Director simple.json", 339, 2)

    def test_full(self):
        self.check("H3MD Director.json", 227, 0)


class LowResBaseTests(unittest.TestCase):
    """0.4.0: a local redraw's low-res pass keeps the segment's own low-res result on every kept row."""

    def test_write_low_base(self):
        import torch
        from h3md_luna import luna_two_pass as LTP
        low = torch.zeros(1, 4, 6, 2, 2)
        base = torch.arange(1 * 4 * 6 * 2 * 2, dtype=torch.float32).view(1, 4, 6, 2, 2)
        mask = torch.tensor([0, 0, 1, 1, 0, 0], dtype=torch.float32).view(1, 1, 6, 1, 1)
        out = LTP._write_low_base(low, mask, {"samples": base})
        self.assertTrue(torch.equal(out[:, :, :2], base[:, :, :2]))
        self.assertTrue(torch.equal(out[:, :, 4:], base[:, :, 4:]))
        self.assertTrue(torch.equal(out[:, :, 2:4], low[:, :, 2:4]))   # redrawn rows untouched (noise comes later)
        with self.assertRaises(ValueError):
            LTP._write_low_base(low, mask, {"samples": base[:, :, :5]})
        with self.assertRaises(ValueError):
            LTP._write_low_base(low, None, {"samples": base})

    def test_check_low_res_base(self):
        import torch
        import comfy.nested_tensor as NT
        import h3md_sampler as S
        v = torch.zeros(1, 24, 6, 8, 8)
        a = torch.zeros(1, 8, 1, 40)
        lat = {"samples": NT.NestedTensor((v, a)),
               "noise_mask": NT.NestedTensor((torch.zeros(1, 1, 6, 1, 1), torch.zeros(1, 1, 1, 40)))}
        self.assertIsNone(S.check_low_res_base(torch.zeros(1, 24, 6, 4, 4), lat, 0.5))
        self.assertIsNotNone(S.check_low_res_base(torch.zeros(1, 24, 5, 4, 4), lat, 0.5))
        self.assertIsNotNone(S.check_low_res_base(torch.zeros(1, 24, 6, 8, 8), lat, 0.5))
        self.assertIsNotNone(S.check_low_res_base(torch.zeros(1, 24, 6, 4, 4), {"samples": lat["samples"]}, 0.5))


if __name__ == "__main__":
    unittest.main()
