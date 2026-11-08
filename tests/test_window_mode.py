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
        self.assertEqual((wm["highLength"], wm["highOverlap"]), (124, 39))
        wm = WIN.normalize_window_mode({"length": 294, "overlap": 56, "highLength": 180})
        self.assertEqual((wm["length"], wm["highLength"], wm["highOverlap"]), (294, 175, 56))

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

    def test_window_mode_never_splits_with_budget(self):
        p = D.parse_plan(plan(True), budget=50000)
        self.assertEqual(len(p["segments"]), 1)
        self.assertEqual(p["segments"][0]["length"], LONG)
        p_off = D.parse_plan(plan(), budget=50000)
        self.assertGreater(len(p_off["segments"]), 1)

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



TIMED = ("summary: a woman dances.\ndetailed_description: A woman on a stage. [2s] she raises both arms. "
         "[20s] she spins once. [35s] she claps. [50s] she bows.\noverall_soundscape: music.")


def timed_plan(window=True, prompt=TIMED, length=1445):
    raw = json.loads(plan(window, length=length))
    raw["segments"][0]["prompt"] = prompt
    return json.dumps(raw)


class TimedPrompts(unittest.TestCase):
    def test_spans_tile_and_hold_centres(self):
        for total, ln, ov in ((1445, 243, 56), (721, 243, 56), (685, 124, 56), (1445, 175, 22)):
            wins, spans = WIN.window_spans(total, ln, ov)
            self.assertEqual(spans[0][0], 0)
            self.assertEqual(spans[-1][1], total)
            for a, b in zip(spans, spans[1:]):
                self.assertEqual(a[1], b[0])
            for (a, b), (lo, hi) in zip(wins, spans):
                self.assertTrue(lo <= (a + b) // 2 < hi or (a + b) // 2 == hi == total)

    def test_each_action_once(self):
        wps = WIN.auto_window_prompts(TIMED, 1445, 243, 56)
        self.assertEqual(len(wps), 8)
        self.assertEqual(sum(w["events"] for w in wps), 4)
        for act in ("raises both arms", "spins once", "claps", "bows"):
            self.assertEqual(sum(act in w["prompt"] for w in wps), 1, act)
        for w in wps:
            self.assertIn("summary: a woman dances.", w["prompt"])
            self.assertIn("overall_soundscape: music.", w["prompt"])
            self.assertIn("A woman on a stage.", w["prompt"])
            self.assertNotIn("[", w["prompt"])
            if not w["events"]:
                self.assertIn(WIN.NO_NEW_ACTION, w["prompt"])

    def test_no_marks_or_one_window(self):
        self.assertIsNone(WIN.auto_window_prompts("detailed_description: a woman sings.", 1445, 243, 56))
        self.assertIsNone(WIN.auto_window_prompts("[2s] no section", 1445, 243, 56))
        self.assertIsNone(WIN.auto_window_prompts(TIMED, 243, 243, 56))

    def test_plan_auto(self):
        p = D.parse_plan(timed_plan())
        seg = p["segments"][0]
        self.assertTrue(seg.get("windowPromptsAuto"))
        self.assertEqual(len(seg["windowPrompts"]), len(WIN.window_spans(1445, 124, 56)[0]))

    def test_plan_off_or_manual_untouched(self):
        p = D.parse_plan(timed_plan(window=None))
        self.assertNotIn("windowPrompts", p["segments"][0])
        raw = json.loads(timed_plan())
        raw["segments"][0]["windowPrompts"] = [{"at": 0, "prompt": "a"}, {"at": 30, "prompt": "b"}]
        seg = D.parse_plan(json.dumps(raw))["segments"][0]
        self.assertEqual(len(seg["windowPrompts"]), 2)
        self.assertNotIn("windowPromptsAuto", seg)


class Preview(unittest.TestCase):
    def test_flag_and_cache_key(self):
        self.assertFalse(WIN.normalize_window_mode(True)["preview"])
        self.assertTrue(WIN.normalize_window_mode({"preview": 1})["preview"])
        a = D.parse_plan(timed_plan({"length": 243}))
        b = D.parse_plan(timed_plan({"length": 243, "preview": True}))
        self.assertEqual(C.segment_signature(a, 0), C.segment_signature(b, 0))

    def test_auto_prompt_duration_is_window(self):
        raw = json.loads(timed_plan({"length": 243}))
        raw["segments"][0]["prompt"] = "Duration: 60 s.\n" + TIMED
        seg = D.parse_plan(json.dumps(raw))["segments"][0]
        for wp in seg["windowPrompts"]:
            self.assertIn("Duration: 10.12 s (243 frames).", wp["prompt"])


CUTP = ("summary: two shots.\ndetailed_description: Photoreal footage, one woman. [0s cut] Medium shot of her in a meadow, "
        "swaying. [3s] she raises one hand. [7s cut] Close-up of her face by a lake at sunset. [10s] she smiles."
        "\noverall_soundscape: wind.")


class HardCuts(unittest.TestCase):
    def test_schedule(self):
        self.assertEqual(WIN.core_windows(345, 124, 56), WIN.core_windows(345, 124, 56, []))
        for total, ln, ov, cuts in ((345, 124, 56, [168]), (719, 243, 56, [300]), (1445, 243, 56, [300, 900]),
                                    (719, 175, 22, [300]), (345, 124, 56, [20, 330])):
            n = WIN.video_latent_t(total)
            cl, co = WIN.video_latent_t(ln), min(WIN.video_latent_t(ov), WIN.video_latent_t(ln) - 5)
            lats = WIN.cut_latents(cuts, total)
            wins = WIN.core_windows(total, ln, ov, cuts)
            self.assertEqual(wins[0][0], 0)
            self.assertEqual(wins[-1][1], n - 1)
            for a, b in wins:
                self.assertEqual(a % 5, 0)
                self.assertFalse(any(a < c <= b for c in lats), (wins, lats))   # no window straddles a cut
                self.assertLessEqual(b - a + 1, cl + 4)
            for (a, b), (c, d) in zip(wins, wins[1:]):
                if c in lats:
                    self.assertEqual(c, b + 1)
                else:
                    self.assertGreaterEqual(b - c + 1, co)
            ws, spans = WIN.window_spans(total, ln, ov, cuts)
            for (a, b), (lo, hi) in zip(ws, spans):
                self.assertTrue(lo <= (a + b) // 2 < hi)

    def test_prompts_follow_shots(self):
        self.assertEqual(WIN.prompt_cuts(CUTP), [168])
        wps = WIN.auto_window_prompts(CUTP, 345, 124, 56)
        _, spans = WIN.window_spans(345, 124, 56, [168])
        self.assertEqual([(w["start"], w["end"]) for w in wps], spans)
        for w in wps:
            first = w["end"] <= 170
            self.assertEqual("meadow" in w["prompt"], first)
            self.assertEqual("lake at sunset" in w["prompt"], not first)
            self.assertIn("Photoreal footage", w["prompt"])
        self.assertEqual(sum("raises one hand" in w["prompt"] for w in wps), 1)
        self.assertEqual(sum("she smiles" in w["prompt"] for w in wps), 1)

    def test_plan_cuts(self):
        p = D.parse_plan(timed_plan(True, CUTP, 345))
        seg = p["segments"][0]
        self.assertEqual(seg["windowCuts"], [170])
        sig = C.segment_signature(p, 0)
        self.assertEqual(sig["windowCuts"], [170])


REFP = ("subject_definitions:\n<Subject 1> is the woman shown in <Picture 1> and <Picture 2>.\n<Picture 3> is the mountain.\n\n"
        "summary:\nreference generation. <Subject 1> performs to the music. Her look comes from <Picture 1>.\n\n"
        "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved.\n<Picture 3> (appears in [Shot 2]): kept.\n\n"
        "detailed_description:\nPhotoreal footage. [0s cut] <Subject 1> stands in a meadow. [7s cut] An empty valley like "
        "<Picture 3>, no people.\n\noverall_soundscape:\nwind.")


class RefsPerWindow(unittest.TestCase):
    def test_window_refs(self):
        kept, t = WIN.window_refs(REFP.replace("[0s cut] <Subject 1> stands in a meadow. [7s cut] ", ""), 3)
        self.assertEqual(kept, [2])
        self.assertIn("<Picture 1> is the mountain", t)
        self.assertNotIn("<Subject 1>", t)
        self.assertIn("like <Picture 1>", t)
        kept, t = WIN.window_refs(REFP, 3)
        self.assertEqual(kept, [0, 1, 2])
        self.assertEqual(t, REFP)

    def test_plan(self):
        raw = json.loads(timed_plan(True, REFP, 345))
        raw["media"]["images"] = [{"id": "a", "file": "a.png"}, {"id": "b", "file": "b.png"}, {"id": "c", "file": "c.png"}]
        raw["segments"][0]["images"] = ["a", "b", "c"]
        seg = D.parse_plan(json.dumps(raw))["segments"][0]
        imgs = [w.get("images") for w in seg["windowPrompts"]]
        self.assertEqual(imgs[0], [0, 1])
        self.assertEqual(imgs[-1], [2])
        raw["windowMode"] = {"refs": False}
        seg = D.parse_plan(json.dumps(raw))["segments"][0]
        self.assertTrue(all("images" not in w for w in seg["windowPrompts"]))


class VideoPerWindow(unittest.TestCase):
    def _plan(self, length=719, vpw="auto"):
        p = D.parse_plan(plan({"length": 243, "highLength": 175, "highOverlap": 22, "videoPerWindow": vpw},
                              length=length))
        return p, p["segments"][0]

    def test_switch(self):
        import torch
        p, seg = self._plan()
        long_v = {"videos": [torch.zeros(719, 4, 4, 3)]}
        short_v = {"videos": [torch.zeros(300, 4, 4, 3)]}
        self.assertTrue(D.video_per_window(p, seg, long_v))
        self.assertFalse(D.video_per_window(p, seg, short_v))
        self.assertFalse(D.video_per_window(p, seg, {"videos": []}))
        p2, seg2 = self._plan(vpw=True)
        self.assertTrue(D.video_per_window(p2, seg2, short_v))
        self.assertFalse(D.video_per_window(D.parse_plan(plan(length=719)), seg, long_v))

    def test_encode_slices(self):
        import torch
        p, seg = self._plan()
        frames = torch.arange(719).float().view(719, 1, 1, 1).expand(719, 4, 4, 3)
        snd = {"waveform": torch.zeros(1, 2, 719 * 2000), "sample_rate": 48000}
        picked = {"videos": [frames], "video_audios": [snd], "images": []}
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})

        pos, lat, text = D.encode_video_windows(FakeCore, p, seg, picked, {}, None, "Duration: 30 s.\nx", None,
                                                None, None, "match")
        ranges = WIN.all_window_ranges(719, p["windowMode"])
        self.assertEqual(len(calls), len(ranges))
        self.assertEqual([c[1][WIN.RANGE_KEY] for c in pos], ranges)
        for (a, b), kw in zip(ranges, calls):
            v = kw["ref_videos"]["ref_video_0"]
            self.assertEqual(int(v[0, 0, 0, 0]), a)                 # the slice starts at the window start
            self.assertEqual(v.shape[0], b - a)
            self.assertEqual(kw["ref_video_audios"]["ref_video_audio_0"]["waveform"].shape[-1], round((b - a) / 24 * 48000))
            self.assertIn(f"({b - a} frames)", kw["prompt"])

    def test_pick_by_range(self):
        class W:
            index_list = list(range(15, 90))
            total_frames = 212
        cw = [{WIN.SPAN_KEY: (0, 719), WIN.RANGE_KEY: (0, 243)}, {WIN.SPAN_KEY: (0, 719), WIN.RANGE_KEY: (51, 306)}]
        starts = WIN.latent_pixel_starts(212)
        orig = WIN.window_pixel_span
        try:
            WIN.window_pixel_span = lambda w: (starts[w.index_list[0]], starts[w.index_list[-1] + 1])
            self.assertEqual((starts[15], starts[90]), (51, 306))
            self.assertEqual(WIN.pick_span_cond(cw, W), 1)
        finally:
            WIN.window_pixel_span = orig


def multi_plan(window=True, segs=((345, 0), (345, 39), (243, 0), (345, 39))):
    raw = {"version": 1, "width": 1920, "height": 1088, "globalPrompt": "",
           "media": {"images": [{"id": "a", "file": "a.png"}, {"id": "b", "file": "b.png"}], "videos": [], "audios": []},
           "segments": [{"length": ln, "overlapIn": ov, "images": ["a"] if i % 2 == 0 else ["b"],
                         "prompt": f"Duration: 1 s.\n\nshot {i + 1} <Picture 1>"} for i, (ln, ov) in enumerate(segs)]}
    if window is not None:
        raw["windowMode"] = window
    return json.dumps(raw)


class Groups(unittest.TestCase):
    def test_one_timeline(self):
        p = D.parse_plan(multi_plan({"groupLength": 1445}))
        self.assertEqual(len(p["segments"]), 1)
        g = p["segments"][0]
        self.assertEqual(g["length"], 345 + 306 + 243 + 306 - 5)   # back on the 17n+5 grid, nearest way
        self.assertEqual(g["length"] % 17, 5)
        gps = g["groupParts"]
        self.assertEqual([(gp["start"], gp["end"]) for gp in gps], [(0, 345), (345, 651), (651, 894), (894, 1195)])
        self.assertEqual([gp["cut"] for gp in gps], [False, False, True, False])
        self.assertEqual(g["windowCuts"], [646])      # 651 snapped to the 17-frame grid
        self.assertEqual(g["images"], ["a", "b"])
        self.assertEqual([gp["images"] for gp in gps], [[0], [1], [0], [1]])
        for gp in gps:
            self.assertNotIn("Continuity", gp["prompt"])
        self.assertIn("Duration: 12.75 s (306 frames).", gps[1]["prompt"])
        self.assertEqual(p["total_frames"], 1195)
        self.assertIn("groupParts", C.segment_signature(p, 0))

    def test_split_at_last_cut(self):
        p = D.parse_plan(multi_plan({"groupLength": 1000}))
        segs = p["segments"]
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["length"], 651)          # shots 1-2, then the group ends at the hard cut of shot 3
        self.assertEqual(segs[1]["overlapIn"], 0)
        self.assertEqual(segs[1]["startFrame"], 651)

    def test_no_cut_note(self):
        p = D.parse_plan(multi_plan({"groupLength": 700}, segs=((345, 0), (345, 39), (345, 39))))
        self.assertTrue(any("put a hard cut" in n for n in p["notes"]))
        self.assertEqual(p["segments"][1]["overlapIn"], 39)   # fallback: the old continuation

    def test_off(self):
        p = D.parse_plan(multi_plan(None))
        self.assertEqual(len(p["segments"]), 4)
        p = D.parse_plan(multi_plan({"groups": False}))
        self.assertEqual(len(p["segments"]), 4)

if __name__ == "__main__":
    unittest.main()


class LoopProgress(unittest.TestCase):
    """The Loop builds the progress store from the real SegmentCache (11-07 bug: SegmentCache has no .path)."""

    def test_progress_store_from_segment_cache(self):
        import tempfile
        import h3md_loop as L
        with tempfile.TemporaryDirectory() as d:
            cache = C.SegmentCache(d, "abc" * 6, True)
            plan_on = D.parse_plan(plan(True, length=345))
            st = L.progress_store(cache, 0, "k", plan_on)
            self.assertIsNotNone(st)
            self.assertTrue(st.dir.endswith("seg_000_progress"))
            self.assertIsNone(L.progress_store(cache, 0, "k", D.parse_plan(plan(length=345))))
            self.assertIsNone(L.progress_store(None, 0, "k", plan_on))


class TilingWindow(unittest.TestCase):
    def test_window_shape(self):
        import h3md_tiled_hires as T

        class H:
            context_length = 52

        class P:
            model_options = {"context_handler": H()}

        class P0:
            model_options = {}
        self.assertEqual(T.window_shape(P(), (1, 24, 422, 104, 68), 2400), ((1, 24, 52, 104, 68), 296))
        self.assertEqual(T.window_shape(P0(), (1, 24, 422, 104, 68), 2400), ((1, 24, 422, 104, 68), 2400))


class AutoGroup(unittest.TestCase):
    def test_ram(self):
        self.assertEqual(WIN.auto_group_length(1088, 1664, 64)[0], 1450)
        self.assertEqual(WIN.auto_group_length(2560, 1408, 64)[0], 1450)
        self.assertEqual(WIN.auto_group_length(1088, 1664, 32)[0], 719)

    def test_plan_note(self):
        p = D.parse_plan(multi_plan())
        self.assertTrue(any("groups up to" in n for n in p["notes"]))
        p = D.parse_plan(multi_plan({"groupLength": 1445}))
        self.assertFalse(any("groups up to" in n for n in p["notes"]))


def _fake_decode(vae, lat):
    """Fake H3 video VAE: [1,C,5m+2,h,w] -> 17m+5 frames, each frame = the value of the latent it comes from."""
    import torch
    t = int(lat.shape[2])
    vals = lat[0, 0, :, 0, 0]
    out = []
    for k in range(t):
        out += [float(vals[k])] * (1 if k % 5 == 0 else 4)
    return torch.tensor(out).view(-1, 1, 1, 1).expand(-1, 2, 2, 3).clone()


class ChunkedDecode(unittest.TestCase):
    def test_same_as_full(self):
        import torch
        import h3md_loop as L
        for n_clips in (15, 29, 44, 84):
            T = 5 * n_clips + 2
            v = torch.arange(T).float().view(1, 1, T, 1, 1).expand(1, 2, T, 1, 1).clone()
            full = _fake_decode(None, v)
            cf = L.ChunkedFrames(None, v, chunk_clips=14, ctx=2, decode=_fake_decode)
            self.assertEqual(cf.shape[0], full.shape[0])
            got = torch.cat([cf[i:i + 16] for i in range(0, cf.shape[0], 16)], 0)
            self.assertTrue(torch.equal(got, full), n_clips)
            self.assertTrue(torch.equal(cf[-39:], full[-39:]))

    def test_switch(self):
        import torch
        import h3md_loop as L
        self.assertTrue(L.chunked_decode_ok(torch.zeros(1, 1, 5 * 20 + 2, 1, 1), {"length": 243}))
        self.assertFalse(L.chunked_decode_ok(torch.zeros(1, 1, 5 * 10 + 2, 1, 1), {"length": 243}))
        self.assertFalse(L.chunked_decode_ok(torch.zeros(1, 1, 5 * 20 + 2, 1, 1), None))


class ChunkedCacheWrite(unittest.TestCase):
    def test_write(self):
        import tempfile, torch
        import numpy as np
        import comfy.nested_tensor as NT
        import h3md_loop as L
        T = 5 * 30 + 2
        v = torch.arange(T).float().view(1, 1, T, 1, 1).expand(1, 2, T, 1, 1).clone()
        full = _fake_decode(None, v)
        cf = L.ChunkedFrames(None, v, chunk_clips=14, ctx=2, decode=_fake_decode)
        with tempfile.TemporaryDirectory() as d:
            cache = C.SegmentCache(d, "abc" * 6, True)
            lat = {"samples": NT.NestedTensor((v, torch.zeros(1, 2, 3)))}
            cache.write(0, "k", lat, cf, None, {"trim": 0})
            fr = np.asarray(cache.frames(0))
            self.assertTrue(np.allclose(fr, full.numpy().astype(np.float16)))


class GroupWindowPrompts(unittest.TestCase):
    def test_window_across_continuation_gets_both_shots(self):
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22, "groupLength": 1450},
                                    segs=((294, 0), (345, 39), (243, 0))))
        for i, s in enumerate(raw["segments"]):
            s["prompt"] = (f"Duration: 1 s.\n\nsummary:\nshot {i + 1}.\n\ndetailed_description:\nACTION{i + 1} here."
                           "\n\noverall_soundscape:\nquiet.")
            s["images"] = ["a"]
        p = D.parse_plan(json.dumps(raw))
        seg = p["segments"][0]
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})
        orig = D.pick_media_for
        try:
            D.pick_media_for = lambda plan, s, size="match", extra=0: {"images": [], "videos": [], "video_audios": [],
                                                                         "audios": [], "locked_audio": None}
            pos, lat, text = D.encode_group_windows(FakeCore, p, seg, None, None, None, None, "match")
        finally:
            D.pick_media_for = orig
        ranges = [c[1][WIN.RANGE_KEY] for c in pos]
        cut = seg["windowCuts"][0]
        for (a, b), kw in zip(ranges, calls):
            pr = kw["prompt"]
            if a < 294 < b:            # straddles the continuation 1 -> 2: both actions, in order
                self.assertIn("ACTION1", pr)
                self.assertIn("the shot continues: ACTION2", pr)
            if a >= cut:               # after the hard cut: shot 3 only
                self.assertIn("ACTION3", pr)
                self.assertNotIn("ACTION2", pr)
            self.assertFalse(a < cut < b)
