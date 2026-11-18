"""0.4.8: in window mode the high-res strip planner counts a timeline-aligned guide video at the WINDOW length
(h3md_window.crop_refs crops it per window), not the whole shot."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import torch  # noqa: E402
import h3md_tiled_hires as T  # noqa: E402


def conds(video_t=72, audio=False):
    guide = {"kind": "video", "latent": torch.zeros(1, 24, video_t, 48, 32)}      # 512x768 guide -> 24x16 rows/frame
    if audio:
        guide["audio_latent"] = torch.zeros(1, 64, 400)
    pic = {"kind": "image", "latent": torch.zeros(1, 24, 1, 104, 68)}
    return {"positive": [{"cross_attn": torch.zeros(1, 900, 8), "minimax_refs": [guide, pic]}]}


class WindowGuideRows(unittest.TestCase):
    def test_whole_shot_unchanged(self):
        _t, _k, rows = T.cond_stats(conds())
        self.assertEqual(rows, 72 * 24 * 16 + 52 * 34)

    def test_window_crops_aligned_guide(self):
        _t, _k, rows = T.cond_stats(conds(), full_t=72, window_t=52)
        self.assertEqual(rows, 52 * 24 * 16 + 52 * 34)

    def test_window_crops_guide_soundtrack(self):
        _t, _k, rows = T.cond_stats(conds(audio=True), full_t=72, window_t=52)
        self.assertEqual(rows, 52 * 24 * 16 + 2 * 289 + 52 * 34)   # ceil(400 * 52 / 72) = 289

    def test_other_length_video_kept_whole(self):
        _t, _k, rows = T.cond_stats(conds(video_t=40), full_t=72, window_t=52)
        self.assertEqual(rows, 40 * 24 * 16 + 52 * 34)

    def test_no_window_no_crop(self):
        for ft, wt in ((72, 72), (None, 52), (72, None)):
            _t, _k, rows = T.cond_stats(conds(), full_t=ft, window_t=wt)
            self.assertEqual(rows, 72 * 24 * 16 + 52 * 34)


if __name__ == "__main__":
    unittest.main()
