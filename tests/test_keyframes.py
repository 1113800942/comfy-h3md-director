"""0.4.9: keyframes - plan parsing, autosplit / window-group mapping, encode tagging, high-res pass drop, cache key."""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402,F401
import torch  # noqa: E402
import h3md_director as D  # noqa: E402
import h3md_cache as C  # noqa: E402
from h3md_luna import luna_two_pass as L  # noqa: E402

IMAGES = [{"id": "a", "file": "a.png"}, {"id": "b", "file": "b.png"}]


def plan(segs, window=None):
    raw = {"version": 1, "width": 1408, "height": 768, "globalPrompt": "", "segments": segs,
           "media": {"images": IMAGES, "videos": [], "audios": []}}
    if window is not None:
        raw["windowMode"] = window
    return json.dumps(raw)


def shot(length=243, kfs=None, overlap=0):
    s = {"length": length, "prompt": "Duration: 1 s.\n\nshot", "overlapIn": overlap}
    if kfs is not None:
        s["keyframes"] = kfs
    return s


class Parse(unittest.TestCase):
    def test_frames_sorted_and_checked(self):
        p = D.parse_plan(plan([shot(243, [{"image": "b", "at": 9.0}, {"image": "a", "at": 5.0},
                                          {"image": "zz", "at": 2.0}, {"image": "a", "at": 30.0},
                                          {"image": "a"}])]))
        self.assertEqual(p["segments"][0]["keyframes"], [{"image": "a", "frame": 120}, {"image": "b", "frame": 216}])
        notes = "\n".join(p["notes"])
        self.assertIn("'zz' is not in media.images", notes)
        self.assertIn("outside the shot", notes)
        self.assertIn("needs 'image' and 'at'", notes)

    def test_not_a_reference(self):
        p = D.parse_plan(plan([shot(243, [{"image": "a", "at": 5.0}])]))
        self.assertEqual(p["segments"][0]["images"], [])
        self.assertFalse(any(lab["tag"].startswith("<Picture") for lab in p["segments"][0]["labels"]))

    def test_overlap_skipped(self):
        p = D.parse_plan(plan([shot(243), shot(243, [{"image": "a", "at": 0.5}, {"image": "b", "at": 4.0}], 39)]))
        self.assertEqual(p["segments"][1]["keyframes"], [{"image": "b", "frame": 96}])
        self.assertIn("inside the carried overlap", "\n".join(p["notes"]))

    def test_same_frame_later_wins(self):
        p = D.parse_plan(plan([shot(243, [{"image": "a", "at": 5.0}, {"image": "b", "at": 5.0}])]))
        self.assertEqual(p["segments"][0]["keyframes"], [{"image": "b", "frame": 120}])

    def test_no_keyframes_no_key(self):
        p = D.parse_plan(plan([shot(243)]))
        self.assertNotIn("keyframes", p["segments"][0])


class Autosplit(unittest.TestCase):
    def test_each_part_keeps_its_own(self):
        long = 5 + 17 * 40   # 685 frames, split with a small budget
        p = D.parse_plan(plan([shot(long, [{"image": "a", "at": 2.0}, {"image": "b", "at": 25.0}])]), budget=50000)
        segs = p["segments"]
        self.assertGreater(len(segs), 1)
        found = []
        for s in segs:
            for k in s.get("keyframes") or ():
                self.assertGreaterEqual(k["frame"], int(s["overlapIn"]))
                self.assertLess(k["frame"], int(s["length"]))
                found.append((k["image"], int(s["startFrame"]) + k["frame"]))
        self.assertEqual(sorted(found), [("a", 48), ("b", 600)])


class Group(unittest.TestCase):
    def test_parts_mapped_to_timeline(self):
        p = D.parse_plan(plan([shot(243, [{"image": "a", "at": 2.0}]),
                               shot(243, [{"image": "b", "at": 3.0}], 22)], window=True))
        segs = p["segments"]
        self.assertEqual(len(segs), 1)
        g = segs[0]
        self.assertTrue(g.get("groupParts"))
        self.assertNotIn("keyframes", g)
        origin2 = g["groupParts"][1]["origin"]
        self.assertEqual(D.segment_keyframes(g), [{"image": "a", "frame": 48}, {"image": "b", "frame": origin2 + 72}])


class FakeGuide:
    calls = []

    @classmethod
    def execute(cls, positive, latent, frame_idx, vae=None, image=None, **kw):
        if frame_idx > 200:
            raise ValueError("frame_idx outside")
        cls.calls.append(frame_idx)
        out = []
        for c, d in positive:
            d = dict(d)
            d["minimax_keyframes"] = list(d.get("minimax_keyframes") or []) + [{"resolved_frame_index": frame_idx}]
            out.append([c, d])
        return (out,)


class FakeCore:
    MiniMaxH3AddGuide = FakeGuide


class Encode(unittest.TestCase):
    def test_tagged_and_bad_frame_skipped(self):
        p = D.parse_plan(plan([shot(243, [{"image": "a", "at": 5.0}, {"image": "b", "at": 9.0}])]))
        seg = p["segments"][0]
        orig = D.load_image
        D.load_image = lambda f: torch.zeros(1, 8, 8, 3)
        try:
            pos = D.add_keyframes(FakeCore, [[torch.zeros(1), {}], [torch.zeros(1), {"x": 1}]],
                                  {"samples": None}, p, seg, vae=object())
        finally:
            D.load_image = orig
        self.assertEqual(len(pos), 2)
        for c in pos:
            kfs = c[1]["minimax_keyframes"]
            self.assertEqual([k["resolved_frame_index"] for k in kfs], [120])   # 216 > 200 -> skipped
            self.assertTrue(all(k[D.KEYFRAME_TAG] for k in kfs))
        self.assertEqual(pos[1][1]["x"], 1)

    def test_no_keyframes_untouched(self):
        p = D.parse_plan(plan([shot(243)]))
        pos = [[torch.zeros(1), {}]]
        self.assertIs(D.add_keyframes(FakeCore, pos, {"samples": None}, p, p["segments"][0], None), pos)


class HighResDrop(unittest.TestCase):
    def test_only_tagged_dropped(self):
        cond = [[torch.zeros(1), {"minimax_keyframes": [{"h3md_low_only": True, "i": 1}, {"i": 2}]}],
                [torch.zeros(1), {"minimax_keyframes": [{"h3md_low_only": True}]}],
                [torch.zeros(1), {"y": 3}]]
        out = L._without_low_only_keyframes(cond)
        self.assertEqual(out[0][1]["minimax_keyframes"], [{"i": 2}])
        self.assertNotIn("minimax_keyframes", out[1][1])
        self.assertEqual(out[2][1], {"y": 3})
        self.assertEqual(len(cond[1][1]["minimax_keyframes"]), 1)   # input untouched


class CacheKey(unittest.TestCase):
    def test_key_only_with_keyframes(self):
        plain = C.segment_signature(D.parse_plan(plan([shot(243)])), 0)
        self.assertNotIn("keyframes", plain)
        kf = C.segment_signature(D.parse_plan(plan([shot(243, [{"image": "a", "at": 5.0}])])), 0)
        self.assertEqual(kf["keyframes"], [["a", 120]])
        self.assertTrue(any(f[0] == "images" and f[1] == "a" for f in kf["files"]))


if __name__ == "__main__":
    unittest.main()
