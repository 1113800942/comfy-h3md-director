"""GitHub #15: Keyframe Export could not read the frame at 0.00 s when the first frame's pts is above 0."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import h3md_keyframe_nodes as KN  # noqa: E402


def late_start_video(path, first_pts=2, n=60, fps=30):
    """frames start at pts 2 (0.066 s at 30 fps), like a phone recording with an edit list"""
    import av
    from fractions import Fraction
    with av.open(path, "w") as c:
        vs = c.add_stream("libx264", rate=fps)
        vs.width, vs.height, vs.pix_fmt = 64, 48, "yuv420p"
        vs.time_base = Fraction(1, fps)
        for k in range(n):
            fr = av.VideoFrame.from_ndarray(np.full((48, 64, 3), k * 3, np.uint8), format="rgb24")
            fr.pts = first_pts + k
            fr.time_base = Fraction(1, fps)
            for p in vs.encode(fr):
                c.mux(p)
        for p in vs.encode():
            c.mux(p)


class LateFirstFrame(unittest.TestCase):
    def test_frame_at_zero(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "late.mp4")
            late_start_video(src)
            got = KN.read_frames(src, [0.0], (64, 48))
            self.assertIn(0.0, got)
            got = KN.read_frames(src, [0.0, 1.0], (64, 48))
            self.assertEqual(sorted(got), [0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
