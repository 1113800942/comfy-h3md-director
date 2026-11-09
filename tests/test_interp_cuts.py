"""0.3.9-beta: frame interpolation never blends two shots at a hard cut (save node + face-refine save)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import av_latent  # noqa: E402  (CPU ComfyUI first)

import torch  # noqa: E402

import h3md_cache as C  # noqa: E402
import h3md_stream_save as S  # noqa: E402

H, W = 32, 48
COLORS = {"red": (1.0, 0.0, 0.0), "green": (0.0, 1.0, 0.0), "blue": (0.0, 0.0, 1.0)}


def solid(names):
    """[N,H,W,3] frames, one colour name per frame."""
    return torch.stack([torch.tensor(COLORS[n]).view(1, 1, 3).expand(H, W, 3) for n in names])


def make_cache(d, segs, cuts=None, overlap_source="next"):
    """segs: [(colour names per frame, trim)] -> H3MD_SEGMENTS info (uint8 cache: colours stay exact)."""
    c = C.SegmentCache(d, "cuts" * 16, True, dtype="uint8")
    lat = av_latent(t=2, h=4, w=4, seed=1)
    audio = {"waveform": torch.zeros(1, 2, 48000 * 4), "sample_rate": 48000}
    for k, (names, trim) in enumerate(segs):
        c.write(k, f"key{k}", lat, solid(names), audio, {"trim": trim, "report": {}})
    return S.segments_info(c.dir, list(range(len(segs))), [t for _, t in segs], overlap_source, cuts=cuts)


class LinearModel:
    """RIFE stand-in: a plain cross-fade, so a blended frame is easy to spot."""

    def inference_batch(self, I0, I1, steps, scale=1.0):
        t = torch.tensor(steps, dtype=I0.dtype).view(-1, 1, 1, 1)
        return I0 * (1 - t) + I1 * t


class FakeWrapper:
    device = torch.device("cpu")
    use_fp16 = False

    def __init__(self):
        self.model = LinearModel()


class RecordingWriter:
    """Stands in for the PyAV writer: keeps the frames, creates the .part file on finish."""
    last = None

    def __init__(self, path, width, height, fps, audio=None, **kw):
        self.path, self.got, self.frames, self.a, self.a_done, self.sr = path, [], 0, None, 0, 48000
        RecordingWriter.last = self

    def write(self, frames):
        for f in frames:
            self.got.append(f.clone())
        self.frames += len(frames)

    def finish(self):
        open(self.path, "wb").close()

    def abort(self):
        pass


def is_blend(frame):
    """True when the frame is not one of the pure colours (a cross-fade of two shots)."""
    px = frame[0, 0]
    return not any(torch.allclose(px, torch.tensor(c), atol=0.02) for c in COLORS.values())


class SkipCutsTests(unittest.TestCase):
    def test_48fps_half_way_takes_the_earlier_frame(self):
        pos = S.target_positions(24, 48, 10)
        new, changed = S.skip_cuts(pos, [5])
        self.assertEqual(changed, 1)
        self.assertEqual(len(new), len(pos))
        k = pos.index((4, 5, 0.5))
        self.assertEqual(new[k], (4, 4, 0.0))
        self.assertEqual([p for i, p in enumerate(new) if i != k], [p for i, p in enumerate(pos) if i != k])

    def test_60fps_nearest_source_frame(self):
        pos = S.target_positions(24, 60, 12)
        new, changed = S.skip_cuts(pos, [6])
        crossing = [(i, p) for i, p in enumerate(pos) if p[2] and p[0] < 6 <= p[1]]
        self.assertEqual(changed, len(crossing))
        self.assertGreater(changed, 1)
        for i, (i1, i2, f) in crossing:
            self.assertEqual(new[i], (i1, i1, 0.0) if f <= 0.5 else (i2, i2, 0.0))
        firsts = [p[0] for p in new]
        self.assertEqual(firsts, sorted(firsts))          # chunking needs the first source frame in order

    def test_30fps_and_no_cuts(self):
        pos = S.target_positions(24, 30, 20)
        self.assertEqual(S.skip_cuts(pos, []), (pos, 0))
        new, changed = S.skip_cuts(pos, [10, 15])
        self.assertEqual(changed, sum(1 for p in pos if p[2] and (p[0] < 10 <= p[1] or p[0] < 15 <= p[1])))
        self.assertTrue(all(not (p[2] and (p[0] < 10 <= p[1] or p[0] < 15 <= p[1])) for p in new))


class HardCutTests(unittest.TestCase):
    SEGS = [(["red"] * 30 + ["green"] * 30, 0),         # cut inside segment 1 at frame 30
            (["green"] * 60, 12),                       # continuation (trim 12)
            (["blue"] * 40 + ["red"] * 20, 0)]          # hard cut before segment 3, cut inside at 40

    def test_next_and_prev(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, self.SEGS, cuts=[[30], [5], [40]])
            src = S.SegmentSource(info)
            # next: spans (0,48) (0,60) (0,60) -> starts 0, 48, 108; segment 2's cut at 5 is a real frame
            self.assertEqual(src.hard_cuts(), [30, 53, 108, 148])
            src.close()
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, self.SEGS, cuts=[[30], [5], [40]], overlap_source="prev")
            src = S.SegmentSource(info)
            # prev: spans (0,60) (12,60) (0,60) -> starts 0, 60, 108; segment 2's cut at 5 is trimmed away
            self.assertEqual(src.hard_cuts(), [30, 108, 148])
            src.close()

    def test_old_info_without_cuts(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, self.SEGS)
            self.assertNotIn("cuts", info)
            src = S.SegmentSource(info)
            self.assertEqual(src.hard_cuts(), [108])   # only the cut between segments is known
            src.close()


class SaveTests(unittest.TestCase):
    def setUp(self):
        self._w = S.StreamWriter
        S.StreamWriter = RecordingWriter

    def tearDown(self):
        S.StreamWriter = self._w

    def save(self, info, d, fps, chunk=None):
        out = os.path.join(d, "clip.mp4")
        rep = S.stream_save(info, out, interpolate=True, target_fps=fps, interpolator=FakeWrapper(),
                            chunk_frames=chunk)
        return rep, RecordingWriter.last.got

    def test_48fps_no_blend_at_cuts(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, HardCutTests.SEGS, cuts=[[30], [], [40]])
            rep, frames = self.save(info, d, 48.0)
            n = 48 + 60 + 60
            self.assertEqual(rep["source_frames"], n)
            self.assertEqual(len(frames), 2 * n)
            self.assertEqual(rep["hard_cuts"], [30, 108, 148])
            self.assertEqual(rep["cut_frames_copied"], 3)
            self.assertFalse(any(is_blend(f) for f in frames))
            # each cut: the in-between frame repeats the last frame of the old shot
            for c, old in ((30, "red"), (108, "green"), (148, "blue")):
                self.assertTrue(torch.allclose(frames[2 * c - 1][0, 0], torch.tensor(COLORS[old])))
                self.assertFalse(torch.allclose(frames[2 * c][0, 0], torch.tensor(COLORS[old])))

    def test_chunk_boundary_at_a_cut(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, HardCutTests.SEGS, cuts=[[30], [], [40]])
            _rep, a = self.save(info, d, 60.0, chunk=30)    # a chunk ends right at the first cut
            _rep, b = self.save(info, d, 60.0, chunk=8)
            self.assertEqual(len(a), len(b))
            self.assertFalse(any(is_blend(f) for f in a))
            self.assertTrue(all(torch.equal(x, y) for x, y in zip(a, b)))

    def test_without_cut_info_the_old_behaviour_stays_inside_segments(self):
        with tempfile.TemporaryDirectory() as d:
            info = make_cache(d, HardCutTests.SEGS)            # an old 'long video' value: no inner cuts known
            rep, frames = self.save(info, d, 48.0)
            self.assertEqual(rep["hard_cuts"], [108])
            self.assertEqual(sum(is_blend(f) for f in frames), 2)   # the two inner cuts still blend (unknown)


if __name__ == "__main__":
    unittest.main()
