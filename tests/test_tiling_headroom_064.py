"""0.6.4: the window-mode headroom strip (one strip more than the estimate needs) stays on cards smaller than the
32 GB card the safety table was measured on, and is dropped on a 30 GiB+ card only while the plan keeps a comfortable
distance from the measured-safe limit."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_tiled_hires as T  # noqa: E402

GIB = 2 ** 30


def plan(ratio=0.61, limit=0.83, fits=True, dynamic=True, tiles=2):
    return {"tiles": tiles, "fit_ratio": ratio, "calib_limit": limit, "fits": fits, "dynamic_vram": dynamic}


class HeadroomStrip(unittest.TestCase):
    def test_5090_comfortable_plan_gets_no_extra_strip(self):
        # 11-26 log: RTX 5090 (31.8 GiB), 2 strips at 0.61 of the workspace, limit 0.83
        self.assertIsNone(T.headroom_strip_reason(plan(), 31.8 * GIB))

    def test_48gb_card_too(self):
        self.assertIsNone(T.headroom_strip_reason(plan(), 48 * GIB))

    def test_smaller_cards_keep_the_extra_strip(self):
        for gib in (8, 12, 16, 24, 29.9):
            self.assertIn("card", T.headroom_strip_reason(plan(), gib * GIB), gib)

    def test_unknown_card_size_counts_as_small(self):
        self.assertIsNotNone(T.headroom_strip_reason(plan(), 0.0))
        self.assertIsNotNone(T.headroom_strip_reason(plan(), None))

    def test_big_card_close_to_the_limit_keeps_it(self):
        # comfort line = 0.90 x 0.83 = 0.747
        self.assertIsNone(T.headroom_strip_reason(plan(ratio=0.74), 32 * GIB))
        self.assertIn("comfort", T.headroom_strip_reason(plan(ratio=0.76), 32 * GIB))
        self.assertIn("comfort", T.headroom_strip_reason(plan(ratio=0.83), 32 * GIB))

    def test_plan_that_does_not_fit_keeps_it(self):
        self.assertIsNotNone(T.headroom_strip_reason(plan(ratio=0.5, fits=False), 32 * GIB))
        self.assertIsNotNone(T.headroom_strip_reason(plan(ratio=float("inf"), fits=False), 32 * GIB))
        self.assertIsNotNone(T.headroom_strip_reason(plan(limit=0.0), 32 * GIB))

    def test_without_dynamic_vram_keeps_it(self):
        self.assertIn("DynamicVRAM", T.headroom_strip_reason(plan(dynamic=False), 32 * GIB))

    def test_constants_are_conservative(self):
        self.assertGreaterEqual(T.HEADROOM_CARD_GIB, 30.0)   # a 24 GB card must never lose the strip
        self.assertLessEqual(T.HEADROOM_COMFORT, 0.90)

    def test_run_uses_the_rule(self):
        src = (HERE.parent / "h3md_tiled_hires.py").read_text(encoding="utf-8")
        body = src[src.index("def _run("):src.index("def _run_plan(")]
        self.assertIn("headroom_strip_reason(plan, total_vram_bytes(patcher))", body)
        self.assertIn('min_tiles=plan["tiles"] + 1', body)

    def test_total_vram_bytes_never_raises(self):
        self.assertEqual(T.total_vram_bytes(object()), 0.0)


if __name__ == "__main__":
    unittest.main()
