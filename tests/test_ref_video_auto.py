"""0.4.8: refVideoHeight=auto also limits the reference media itself (rows fixed in every high-res strip)."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402

PICS = [(1248, 1664), (1248, 1664)]


class RefVideoAuto(unittest.TestCase):
    def test_large_guide_steps_down(self):
        # 11-13 swapA: 1088x1664 guide, 243-frame shot at 1088x1664 - total fit, references did not
        pick = D.plan_ref_video_sizes("auto", 1088, 1664, 243, [(1088, 1664, 345)], PICS)
        self.assertFalse(pick["over"])
        self.assertGreaterEqual(pick["tier"], 2, pick)
        self.assertLessEqual(D.ref_fixed_tokens(pick["est"]), pick["fixed_limit"])
        self.assertLessEqual(pick["est"]["total"], D.REF_VIDEO_AUTO_TOKEN_LIMIT)
        w, h = pick["sizes"][0]
        self.assertLess(w, 768)

    def test_old_rule_would_have_kept_tier1(self):
        # documents the bug: tier1 passes the total but not the reference limit
        smax, lmax = D.REF_VIDEO_TIERS[0]
        size = D.ref_video_fit(1088, 1664, smax, lmax)
        est = D.estimate_segment_tokens(1088, 1664, 243, [size + (345,)], PICS)
        self.assertLessEqual(est["total"], D.REF_VIDEO_AUTO_TOKEN_LIMIT)
        self.assertGreater(D.ref_fixed_tokens(est), D.REF_VIDEO_AUTO_FIXED_LIMIT)

    def test_480p_guide_unchanged(self):
        for length in (243, 362):
            pick = D.plan_ref_video_sizes("auto", 1088, 1632, length, [(480, 720, 1800)], PICS)
            # a 480p guide fits every tier unchanged; only the (old) total rule may still warn on long shots
            self.assertEqual(pick["sizes"], [(480, 720)])
            self.assertNotIn("references", pick["reasons"], pick)

    def test_fixed_heights_and_native_unchanged(self):
        vids = [(1088, 1664, 345)]
        self.assertEqual(D.plan_ref_video_sizes("native", 1088, 1664, 243, vids, PICS)["sizes"], [(1088, 1664)])
        p = D.plan_ref_video_sizes("720", 1088, 1664, 243, vids, PICS)
        self.assertEqual(p["sizes"], [D.ref_video_fit(1088, 1664, 720)])
        self.assertNotIn("fixed_limit", p)

    def test_limit_scales_with_budget(self):
        self.assertEqual(D.ref_fixed_limit(D.REF_VIDEO_AUTO_TOKEN_LIMIT), D.REF_VIDEO_AUTO_FIXED_LIMIT)
        self.assertLess(D.ref_fixed_limit(150000), D.REF_VIDEO_AUTO_FIXED_LIMIT)
        small = D.plan_ref_video_sizes("auto", 1088, 1664, 243, [(1088, 1664, 345)], PICS, limit=150000)
        big = D.plan_ref_video_sizes("auto", 1088, 1664, 243, [(1088, 1664, 345)], PICS)
        self.assertGreaterEqual(small["tier"], big["tier"])


if __name__ == "__main__":
    unittest.main()
