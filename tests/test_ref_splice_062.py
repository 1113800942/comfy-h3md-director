"""0.6.2 H3MD Ref Splice: script parsing, timeline (short dissolve transitions, long transitions -> text shots carried
by 39 frames), prompts with the right times, the Director accepts the plan, and an end-to-end splice of a small
synthetic video (frames, dissolve, audio, files)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import h3_harness  # noqa: E402,F401
import h3md_director as D  # noqa: E402
import h3md_ref_splice as S  # noqa: E402

BASIC = """subject: <Subject 1> is the fighter in black.
subject: <Subject 2> is the man in armour.
style: Realistic fight.
scene: A wide shot of an arena.
clip 4.0-6.0: they trade kicks
gap 1.5: <Subject 1> drops onto one knee, then stands up
clip 0-2: a low kick
gap 1
clip 8-9.75: a flip kick
"""


class Parse(unittest.TestCase):
    def test_items_and_keys(self):
        s = S.parse_script(BASIC)
        self.assertEqual([i["kind"] for i in s["items"]], ["clip", "gap", "clip", "gap", "clip"])
        self.assertEqual(len(s["subject"]), 2)
        self.assertEqual(s["items"][1]["sec"], 1.5)
        self.assertEqual(s["items"][3]["text"], "")
        self.assertEqual(S.parse_time("1:05.5"), 65.5)

    def test_errors(self):
        for bad in ("gap 1\nclip 0-2", "clip 0-2\ngap 1", "clip 0-2\ngap 1\ngap 1\nclip 3-4", "hello", "subject: x",
                    "clip 2-2.1"):
            with self.assertRaises(S.SpliceError, msg=bad):
                S.parse_script(bad)


class Timeline(unittest.TestCase):
    def test_short_transitions_stay_in_one_shot(self):
        tl = S.build_timeline(S.parse_script(BASIC), 10.0)
        self.assertEqual(len(tl["shots"]), 1)
        s = tl["shots"][0]
        self.assertEqual(s["frames"], 48 + 36 + 48 + 24 + 42)
        self.assertEqual(s["length"], 209)
        self.assertEqual(len(tl["ops"]), s["frames"] + S.END_PAD)   # rounding up never runs past the video
        gap = tl["ops"][48:84]
        self.assertTrue(all(o[0] == "src" and abs(o[1] - (6.0 - 1 / 24)) < 1e-9 for o in gap[:4]))  # HOLD
        self.assertTrue(all(o[0] == "mix" for o in gap[4:-4]))       # then a dissolve
        self.assertEqual(gap[-4:], [("src", 0.0)] * 4)              # HOLD of the next first frame
        ws = [o[3] for o in gap[4:-4]]
        self.assertTrue(all(a < b for a, b in zip(ws, ws[1:])) and 0 < ws[0] and ws[-1] < 1)

    def test_long_transition_becomes_a_text_shot(self):
        sc = BASIC.replace("gap 1.5:", "gap 4:")
        tl = S.build_timeline(S.parse_script(sc), 10.0)
        kinds = [s["kind"] for s in tl["shots"]]
        self.assertEqual(kinds, ["video", "text", "video"])
        a, t, b = tl["shots"]
        self.assertEqual((a["overlap"], t["overlap"], b["overlap"]), (0, 39, 39))
        self.assertEqual(a["frames"], 48)
        self.assertEqual(t["frames"], 96)
        self.assertEqual(t["length"], D.aligned_up(39 + 96))
        # the next video shot starts after a 39-frame dissolve: its carried frames read the transition
        self.assertEqual(b["ref_start"], 48 + 39)
        self.assertTrue(all(o[0] in ("src", "mix") for o in tl["ops"][48:87]))
        self.assertEqual(tl["ops"][b["ref_start"]], ("src", 0.0))

    def test_clip_past_the_end(self):
        with self.assertRaises(S.SpliceError):
            S.build_timeline(S.parse_script("clip 0-12"), 10.0)


class Prompts(unittest.TestCase):
    def test_times_and_tags(self):
        sc = S.parse_script(BASIC)
        tl = S.build_timeline(sc, 10.0)
        plan = S.build_plan(sc, tl, "t", "H3MD_plans/t/t.mp4", True, [])
        p = plan["segments"][0]["prompt"]
        self.assertIn("From 0.00 to 2.00 seconds, as in <Video 1>: they trade kicks.", p)
        self.assertIn("From 2.00 to 3.50 seconds, <Subject 1> drops onto one knee, then stands up.", p)
        self.assertIn("From 3.50 to 5.50 seconds, as in <Video 1>: a low kick.", p)
        self.assertNotIn("From 5.50 to 6.50", p)                    # an empty transition gets no words
        self.assertIn("From 6.50 to 8.25 seconds, as in <Video 1>: a flip kick.", p)
        self.assertIn("<Audio 1> is the sound of <Video 1>", p)
        for w in ("natural", "do not stop", "no pause"):
            self.assertNotIn(w, p.lower())
        for k in ("subject_definitions:", "summary:", "retention_analysis:", "detailed_description:",
                  "overall_soundscape:", "non_diegetic_music:"):
            self.assertIn(k, p)
        v = plan["segments"][0]["videos"][0]
        self.assertEqual((v["trimStart"], v["useDuration"], v["useSoundtrack"]), (0.0, round(198 / 24, 6), True))
        self.assertEqual(plan["globalPrompt"], "")                  # import keeps the workflow's trigger words

    def test_text_shot_has_no_video_tags(self):
        sc = S.parse_script(BASIC.replace("gap 1.5:", "gap 4:"))
        tl = S.build_timeline(sc, 10.0)
        plan = S.build_plan(sc, tl, "t", "H3MD_plans/t/t.mp4", True, ["pics/hero.png"])
        txt = plan["segments"][1]
        self.assertEqual(txt["videos"], [])
        self.assertNotIn("<Video", txt["prompt"])
        self.assertNotIn("<Audio", txt["prompt"])
        self.assertIn("From 1.62 to 5.62 seconds, <Subject 1> drops onto one knee", txt["prompt"])
        third = plan["segments"][2]
        self.assertEqual(third["overlapIn"], 39)
        self.assertIn("For the first 1.62 seconds it holds the end of the previous moment", third["prompt"])
        self.assertIn("From 1.62 to 3.62 seconds, as in <Video 1>: a low kick.", third["prompt"])
        self.assertEqual(third["videos"][0]["trimStart"], round(87 / 24, 6))

    def test_text_shot_without_pictures_uses_base_format(self):
        sc = S.parse_script(BASIC.replace("gap 1.5:", "gap 4:"))
        tl = S.build_timeline(sc, 10.0)
        plan = S.build_plan(sc, tl, "t", "H3MD_plans/t/t.mp4", True, [])
        p = plan["segments"][1]["prompt"]
        self.assertTrue(p.startswith("integrated_multimodal_description:"))
        self.assertNotIn("<Subject", p)


class DirectorAccepts(unittest.TestCase):
    def test_parse_plan(self):
        for sc_text in (BASIC, BASIC.replace("gap 1.5:", "gap 4:")):
            sc = S.parse_script(sc_text)
            tl = S.build_timeline(sc, 10.0)
            plan = S.build_plan(sc, tl, "t", "H3MD_plans/t/t.mp4", True, [])
            S.check_plan(plan)
            p = D.parse_plan(json.dumps(plan))
            for seg, shot in zip(p["segments"], tl["shots"]):
                self.assertEqual(seg["length"], shot["length"])     # the Director computes the same lengths
            for seg, shot in zip(p["segments"], tl["shots"]):
                if shot["kind"] == "video":
                    self.assertAlmostEqual(D.video_window_origin(seg["videos"][0], seg) * 24,
                                           shot["ref_start"] - shot["overlap"], places=3)


def _synthetic_video(path, seconds=10, fps=30, w=64, h=48):
    """frame k is a flat grey of level k (so a frame's time can be read back), with a 440 Hz tone"""
    import av
    with av.open(path, "w") as c:
        vs = c.add_stream("libx264", rate=fps)
        vs.width, vs.height, vs.pix_fmt = w, h, "yuv420p"
        vs.options = {"crf": "0", "preset": "veryfast"}
        a = c.add_stream("aac", rate=48000)
        a.layout = "stereo"
        for k in range(seconds * fps):
            img = np.full((h, w, 3), min(255, k), np.uint8)
            for p in vs.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(p)
        for p in vs.encode():
            c.mux(p)
        t = np.arange(seconds * 48000) / 48000.0
        tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        st = np.stack([tone, tone])
        for i in range(0, st.shape[1], 1024):
            fr = av.AudioFrame.from_ndarray(np.ascontiguousarray(st[:, i:i + 1024]), format="fltp", layout="stereo")
            fr.sample_rate, fr.pts = 48000, i
            for p in a.encode(fr):
                c.mux(p)
        for p in a.encode():
            c.mux(p)


class EndToEnd(unittest.TestCase):
    def test_splice_files(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.mp4")
            _synthetic_video(src, seconds=10, fps=30, w=64, h=48)
            inp = os.path.join(d, "input")
            os.makedirs(inp)
            res = S.run_splice(src, BASIC, "my splice", inp)
            self.assertTrue(res["plan_path"].endswith(".json") and os.path.isfile(res["video_path"]))
            stem = os.path.basename(os.path.dirname(res["plan_path"]))
            self.assertTrue(stem.startswith("my_splice_") and stem.endswith("_001"))
            info = S.probe(res["video_path"])
            self.assertAlmostEqual(info["duration"] * 24, 198 + S.END_PAD, delta=1.5)
            self.assertTrue(info["has_audio"])
            import av
            with av.open(res["video_path"]) as c:
                lv = [float(f.to_ndarray(format="rgb24").mean()) for f in c.decode(c.streams.video[0])]
            self.assertAlmostEqual(lv[0], 120, delta=3)            # clip 4.0 s at 30 fps = level 120
            self.assertAlmostEqual(lv[47], 30 * (6 - 1 / 24), delta=3)
            self.assertAlmostEqual(lv[84], 0, delta=3)             # clip 0-2 starts after the 1.5 s transition
            mid = lv[66]
            self.assertTrue(10 < mid < 170)                        # inside the dissolve: between both ends
            # a second run never overwrites the first
            res2 = S.run_splice(src, BASIC, "my splice", inp)
            self.assertTrue(os.path.dirname(res2["plan_path"]).endswith("_002"))
            with open(res["plan_path"], encoding="utf-8") as f:
                plan = json.load(f)
            self.assertEqual(plan["media"]["videos"][0]["file"], f"H3MD_plans/{stem}/{stem}.mp4")


if __name__ == "__main__":
    unittest.main()
