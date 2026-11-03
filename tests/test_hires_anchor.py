"""H3MD hi-res anchor (v0.2.0): reference from the shot's first segment, new rows pulled back, kept rows untouched."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import av_latent  # noqa: E402  (CPU ComfyUI first)

import torch  # noqa: E402

import h3md_director as D  # noqa: E402
import h3md_loop as L  # noqa: E402
import h3md_loop_split as LS  # noqa: E402

SEG = 107   # 32 tokens
OV = 39     # 12 tokens


class HiresAnchorTests(unittest.TestCase):
    def test_option_is_last_and_off_by_default(self):
        names = list(LS.H3MDLoopOptions.INPUT_TYPES()["required"])
        self.assertEqual(names[-1], "hires_anchor")
        self.assertEqual(LS.option_defaults()["hires_anchor"], 0.0)

    def test_reference_then_correction(self):
        t = D.video_tokens(SEG)
        first = av_latent(t=t, h=8, w=8, seed=1)
        out, ref, info = L.hires_anchor_step(first, None, False, 0, 1.0, "seg 1")
        self.assertIs(out, first)
        self.assertIsNone(info)
        self.assertIsNotNone(ref)

        nxt = av_latent(t=t, h=8, w=8, seed=2)
        v, a = nxt["samples"].unbind()
        v = v * 1.3 + 0.2      # a drifted segment: wider spread, shifted mean
        nxt = {"samples": D._nested((v, a))}
        out, ref2, info = L.hires_anchor_step(nxt, ref, True, OV, 1.0, "seg 2")
        self.assertIs(ref2, ref)
        v2, a2 = out["samples"].unbind()
        held = D.video_tokens(OV)
        self.assertTrue(torch.equal(v2[:, :, :held], v[:, :, :held]))          # kept overlap untouched
        self.assertTrue(torch.equal(a2, a))                                      # audio untouched
        m_ref = torch.tensor(ref["mean"])
        m_new = v2[:, :, held:].float().mean(dim=(0, 2, 3, 4))
        self.assertLess(float((m_new - m_ref).abs().max()), 1e-3)
        self.assertEqual(info["new_steps"], t - held)

        # half strength lands in between
        out_h, _, _ = L.hires_anchor_step(nxt, ref, True, OV, 0.5, "seg 2")
        vh = out_h["samples"].unbind()[0][:, :, held:].float().mean(dim=(0, 2, 3, 4))
        m_raw = v[:, :, held:].float().mean(dim=(0, 2, 3, 4))
        self.assertTrue(bool(((vh - (m_raw + m_ref) / 2).abs() < 0.05).all()))

    def test_tail_lock_rows_kept(self):
        t = D.video_tokens(SEG)
        _, ref, _ = L.hires_anchor_step(av_latent(t=t, h=8, w=8, seed=1), None, False, 0, 1.0)
        v, a = av_latent(t=t, h=8, w=8, seed=3)["samples"].unbind()
        v = v * 2.0
        out, _, info = L.hires_anchor_step({"samples": D._nested((v, a))}, ref, True, OV, 1.0, "rerun", tail_frames=OV)
        v2 = out["samples"].unbind()[0]
        k = D.video_tokens(OV)
        self.assertTrue(torch.equal(v2[:, :, -k:], v[:, :, -k:]))
        self.assertEqual(info["new_steps"], t - 2 * k)

    def test_continued_without_reference_is_noop(self):
        t = D.video_tokens(SEG)
        x = av_latent(t=t, h=8, w=8, seed=4)
        out, ref, info = L.hires_anchor_step(x, None, True, OV, 1.0)
        self.assertIs(out, x)
        self.assertIsNone(ref)
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
