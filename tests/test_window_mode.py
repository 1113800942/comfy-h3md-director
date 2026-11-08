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

    def test_reuse_unchanged_chunks(self):
        import tempfile, os
        import numpy as np
        import torch
        import h3md_loop as L
        T = 5 * 44 + 2
        v = torch.arange(T).float().view(1, 1, T, 1, 1).expand(1, 2, T, 1, 1).clone()
        full = _fake_decode(None, v)
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "old.npy")
            np.save(old, (full + 1000.0).numpy().astype(np.float16))   # marks copied frames
            calls = []

            def dec(vae, lat):
                calls.append(int(lat.shape[2]))
                return _fake_decode(vae, lat)
            cf = L.ChunkedFrames(None, v, chunk_clips=14, ctx=2, decode=dec, reuse=(old, (80, 135)))
            got = torch.cat([cf[i:i + 16] for i in range(0, cf.shape[0], 16)], 0)
            # chunk 0 (clips 0-13, latents 0-71) and chunk 3 (clips 42-43, latents 200-221 + ctx 190-) are copied;
            # chunks 1 (latents 60-141) and 2 (latents 130-211) touch the change and are decoded
            self.assertEqual((cf.reused, cf.decoded), (2, 2))
            self.assertTrue(torch.equal(got[:238], full[:238] + 1000.0))
            self.assertTrue(torch.equal(got[238:714], full[238:714]))
            self.assertTrue(torch.equal(got[714:], full[714:] + 1000.0))
            del cf

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
                self.assertIn("with no cut, the same shot continues: ACTION2", pr)
            if a >= cut:               # after the hard cut: shot 3 only
                self.assertIn("ACTION3", pr)
                self.assertNotIn("ACTION2", pr)
            self.assertFalse(a < cut < b)


class TimedGroupParts(unittest.TestCase):
    """11-08: lyric / line timing inside group parts and the skill's 'At 00:12.7' format."""

    def test_clock_marks(self):
        self.assertEqual(WIN.clock_marks("At 00:12.7 she sings: <d>x</d>."), "[12.7s] she sings: <d>x</d>.")
        self.assertEqual(WIN.clock_marks("At 01:05.5, he waves."), "[65.5s] he waves.")
        self.assertEqual(WIN.clock_marks("They meet at 10:30 in the morning."), "They meet at 10:30 in the morning.")
        wps = WIN.auto_window_prompts(TIMED.replace("[2s]", "At 00:02.0").replace("[20s]", "At 00:20.0"), 1445, 243, 56)
        self.assertEqual(sum(w["events"] for w in wps), 4)
        self.assertEqual(sum("spins once" in w["prompt"] for w in wps), 1)

    def test_lines_go_to_one_window_each(self):
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22, "groupLength": 1450},
                                    segs=((362, 0), (396, 39))))
        lines = {0: ("LINEA", "LINEB", "LINEC"), 1: ("LINED", "LINEE", "LINEF")}
        for i, s in enumerate(raw["segments"]):
            a, b, c = lines[i]
            s["prompt"] = ("Duration: 1 s.\n\nsummary:\nshe sings.\n\ndetailed_description:\nShe stands on a roof. "
                           f"At 00:02.0 she sings: <d>[English] {a}</d>. At 00:07.5 she sings: <d>[English] {b}</d>. "
                           f"At 00:12.0 she sings: <d>[English] {c}</d>.\n\noverall_soundscape:\nsong.")
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
        wm = p["windowMode"]
        for ln, ov in ((wm["length"], wm["overlap"]), (wm["highLength"], wm["highOverlap"])):
            wins = WIN.window_spans(seg["length"], ln, ov, tuple(seg.get("windowCuts") or ()))[0]
            prompts = [calls[ranges.index(w)]["prompt"] for w in wins]
            for word in lines[0] + lines[1]:      # every line once per pass, never the raw mark
                self.assertEqual(sum(word in pr for pr in prompts), 1, (ln, word))
            for pr in prompts:
                self.assertNotIn("At 00:", pr)
                self.assertNotIn("s]", pr)
        # line D is 2 s into shot 2, which starts at frame 362 - 39 = 323 on the timeline: ~15.5 s
        win = next(w for w in WIN.window_spans(seg["length"], wm["length"], wm["overlap"])[0]
                   if "LINED" in calls[ranges.index(w)]["prompt"])
        self.assertLessEqual(win[0], 323 + 48)
        self.assertGreater(win[1], 323 + 48)


class GroupGuideVideo(unittest.TestCase):
    """11-08: a motion-copy guide split into continued shots stays on the timeline's clock in every window."""

    def test_guide_slices_follow_the_source_clock(self):
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 1450}, segs=((362, 0), (396, 39))))
        raw["media"]["videos"] = [{"id": "g", "file": "g.mp4", "duration": 60.0, "hasAudio": False}]
        starts, durs = (0.0, 362 / 24), (362 / 24, (396 - 39) / 24)
        for i, s in enumerate(raw["segments"]):
            s["videos"] = [{"id": "g", "trimStart": starts[i], "useSoundtrack": False, "useDuration": durs[i]}]
        p = D.parse_plan(json.dumps(raw))
        seg = p["segments"][0]
        self.assertEqual(seg["length"], 719)
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})

        def media(plan, s, size="match", extra=0):
            f0 = round(D.video_window_origin(s["videos"][0], s) * 24)
            fr = torch.arange(f0, f0 + s["length"]).float().view(-1, 1, 1, 1).expand(-1, 2, 2, 3).clone()
            return {"images": [], "videos": [fr], "video_audios": [None], "audios": [], "locked_audio": None}
        orig = D.pick_media_for
        try:
            D.pick_media_for = media
            pos, lat, text = D.encode_group_windows(FakeCore, p, seg, None, None, None, None, "match")
        finally:
            D.pick_media_for = orig
        for c, kw in zip(pos, calls):
            a, b = c[1][WIN.RANGE_KEY]
            got = kw["ref_videos"]["ref_video_0"][:, 0, 0, 0].tolist()
            self.assertEqual(got, [float(x) for x in range(a, b)], (a, b))


class GroupGuideHold(unittest.TestCase):
    def test_hard_cut_before_shot_start_holds_first_frame(self):
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 1450}, segs=((294, 0), (345, 0))))
        raw["media"]["videos"] = [{"id": "g", "file": "g.mp4", "duration": 60.0, "hasAudio": False}]
        raw["segments"][1]["videos"] = [{"id": "g", "trimStart": 10.0, "useSoundtrack": False,
                                         "useDuration": 345 / 24}]
        p = D.parse_plan(json.dumps(raw))
        seg = p["segments"][0]
        cut = seg["windowCuts"][0]
        origin = seg["groupParts"][1]["origin"]
        self.assertLess(cut, origin)        # 294 snaps to 289
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})

        def media(plan, s, size="match", extra=0):
            if not s["videos"]:
                return {"images": [], "videos": [], "video_audios": [], "audios": [], "locked_audio": None}
            fr = torch.arange(1000, 1000 + s["length"]).float().view(-1, 1, 1, 1).expand(-1, 2, 2, 3).clone()
            return {"images": [], "videos": [fr], "video_audios": [None], "audios": [], "locked_audio": None}
        orig = D.pick_media_for
        try:
            D.pick_media_for = media
            pos, lat, text = D.encode_group_windows(FakeCore, p, seg, None, None, None, None, "match")
        finally:
            D.pick_media_for = orig
        n = 0
        for c, kw in zip(pos, calls):
            a, b = c[1][WIN.RANGE_KEY]
            if a < cut:
                self.assertIsNone(kw["ref_videos"])
                continue
            got = kw["ref_videos"]["ref_video_0"][:, 0, 0, 0].tolist()
            want = [1000.0 + max(0, x - origin) for x in range(a, min(b, origin + 345))]
            self.assertEqual(got, want, (a, b))
            n += 1
        self.assertGreater(n, 0)


class GroupGuideTrailHold(unittest.TestCase):
    def test_guide_then_shot_without_it(self):
        """A guide shot continued by a shot without the guide: every window of the guide shot gets a guide of the
        window's own length (native-temporal nodes require it), last frame held."""
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 1450}, segs=((362, 0), (396, 39))))
        raw["media"]["videos"] = [{"id": "g", "file": "g.mp4", "duration": 60.0, "hasAudio": False}]
        raw["segments"][0]["videos"] = [{"id": "g", "trimStart": 0.0, "useSoundtrack": False,
                                         "useDuration": 362 / 24}]
        p = D.parse_plan(json.dumps(raw))
        seg = p["segments"][0]
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})

        def media(plan, s, size="match", extra=0):
            vids = [torch.arange(0, s["length"]).float().view(-1, 1, 1, 1).expand(-1, 2, 2, 3).clone()
                    for _ in s["videos"]]
            return {"images": [], "videos": vids, "video_audios": [None] * len(vids), "audios": [],
                    "locked_audio": None}
        orig = D.pick_media_for
        try:
            D.pick_media_for = media
            pos, lat, text = D.encode_group_windows(FakeCore, p, seg, None, None, None, None, "match")
        finally:
            D.pick_media_for = orig
        n = 0
        for c, kw in zip(pos, calls):
            a, b = c[1][WIN.RANGE_KEY]
            if kw["ref_videos"]:
                got = kw["ref_videos"]["ref_video_0"][:, 0, 0, 0].tolist()
                self.assertEqual(got, [float(min(x, 361)) for x in range(a, b)], (a, b))
                n += 1
        self.assertGreater(n, 2)


class ReviewFixes(unittest.TestCase):
    """11-08 independent review: group sound track, T2VA section, short shot with a cut, cache restore."""

    def _group(self, audios):
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 1450}, segs=((124, 0), (124, 0), (124, 0))))
        raw["media"]["audios"] = [{"id": "A", "file": "a.mp3", "duration": 100.0},
                                  {"id": "B", "file": "b.mp3", "duration": 100.0}]
        for s, au in zip(raw["segments"], audios):
            if au:
                s["audios"] = [{"id": au[0], "mode": "locked", "offset": au[1]}]
                s["audioMode"] = "locked"
        return D.parse_plan(json.dumps(raw))

    def test_group_splits_where_the_sound_track_changes(self):
        same = self._group([("A", 0.0), ("A", 124 / 24), ("A", 248 / 24)])
        self.assertEqual(len(same["segments"]), 1)
        mixed = self._group([None, ("B", 0.0), ("A", 10.0)])
        self.assertEqual(len(mixed["segments"]), 3)
        self.assertTrue(any("own sound track" in n for n in mixed["notes"]))
        jump = self._group([("A", 0.0), ("A", 50.0), ("A", 50.0 + 124 / 24)])
        self.assertEqual(len(jump["segments"]), 2)
        self.assertEqual(jump["segments"][1]["locked"]["offset"], 50.0)

    def test_t2va_section_marks(self):
        pr = ("summary:\na walk.\n\nintegrated_multimodal_description:\nA woman on a beach. [2s] she waves. "
              "[20s] she sits down.\n\noverall_soundscape:\nwaves.")
        wps = WIN.auto_window_prompts(pr, 719, 243, 56)
        self.assertEqual(sum(w["events"] for w in wps), 2)
        self.assertEqual(sum("sits down" in w["prompt"] for w in wps), 1)
        self.assertIn("the same shot continues: B", WIN.combine_part_prompts(
            [(0, "integrated_multimodal_description:\nA\n\n"), (3, "integrated_multimodal_description:\nB\n\n")]))

    def test_short_shot_with_cut_keeps_both_prompts(self):
        pr = ("summary:\ntwo.\n\ndetailed_description:\nPhotoreal. [0s cut] SHOT_ONE in a meadow. "
              "[4s cut] SHOT_TWO by a lake.\n\noverall_soundscape:\nwind.")
        p = D.parse_plan(timed_plan(prompt=pr, length=192, window={"length": 243, "overlap": 56,
                                                                    "highLength": 175, "highOverlap": 22}))
        seg = p["segments"][0]
        wps = D.window_prompts_for(p, seg)
        self.assertEqual(len(wps), 2)
        self.assertTrue(any("SHOT_TWO" in w["prompt"] for w in wps))

    def test_cache_restore(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            cache = C.SegmentCache(d, "abc" * 6, True)
            for suf in (".json", "_latent.pt"):
                open(cache._p(0, suf), "w").write("old" + suf)
            arc = cache.archive_dir()
            cache.archive(0, arc)
            open(cache._p(0, "_latent.pt"), "w").write("half-written")
            cache.restore(0, arc)
            self.assertEqual(open(cache._p(0, "_latent.pt")).read(), "old_latent.pt")
            self.assertEqual(open(cache._p(0, ".json")).read(), "old.json")


class SongPerWindow(unittest.TestCase):
    """11-08 speed: a locked song that is also <Audio N> gives each window only its own stretch."""

    def _song(self, n_sec=60.0, sr=100):
        import torch
        return {"waveform": torch.arange(int(n_sec * sr)).float().view(1, 1, -1), "sample_rate": sr}

    def test_single_shot_windows_hear_their_stretch(self):
        import torch
        raw = json.loads(plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22}, length=719))
        raw["media"]["audios"] = [{"id": "s", "file": "s.mp3", "duration": 60.0}]
        raw["segments"][0]["audios"] = [{"id": "s", "mode": "locked", "offset": 10.0}]
        raw["segments"][0]["audioMode"] = "locked"
        p = D.parse_plan(json.dumps(raw))
        seg = p["segments"][0]
        song = self._song()
        picked = {"images": [], "videos": [], "video_audios": [], "audios": [song], "locked_audio": None,
                  "segment": seg}
        self.assertTrue(D.song_per_window(p, seg, picked))
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})
        pos, lat, text = D.encode_video_windows(FakeCore, p, seg, picked, D.build_core_kwargs(picked), None,
                                                seg["prompt"], None, None, None, "match")
        for c, kw in zip(pos, calls):
            a, b = c[1][WIN.RANGE_KEY]
            w = kw["ref_audios"]["ref_audio_0"]["waveform"][0, 0]
            self.assertEqual(int(w.shape[0]), round((b - a) / 24 * 100))
            self.assertEqual(float(w[0]), float(round((10.0 + a / 24) * 100)))

    def test_song_end_and_start_keep_numbering(self):
        seg = {"audios": ["s"], "audioMode": "locked", "locked": {"kind": "audio", "id": "s", "offset": -2.0}}
        out = D.window_ref_audios(seg, {"audios": [self._song(60.0)]}, 0, 240)
        w = out["ref_audio_0"]["waveform"][0, 0]
        self.assertEqual(int(w.shape[0]), 1000)
        self.assertEqual(float(w[:200].abs().sum()), 0.0)          # 2 s of silence before the song starts
        seg["locked"]["offset"] = 30.0                              # song (5 s) already over: silence, still there
        out = D.window_ref_audios(seg, {"audios": [self._song(5.0)]}, 0, 240)
        self.assertIn("ref_audio_0", out)
        self.assertEqual(float(out["ref_audio_0"]["waveform"].abs().sum()), 0.0)

    def test_other_reference_audio_stays_whole(self):
        seg = {"audios": ["v", "s"], "audioMode": "locked", "locked": {"kind": "audio", "id": "s", "offset": 0.0}}
        voice, song = self._song(3.0), self._song(60.0)
        out = D.window_ref_audios(seg, {"audios": [voice, song]}, 240, 480)
        self.assertIs(out["ref_audio_0"], voice)
        self.assertEqual(int(out["ref_audio_1"]["waveform"].shape[-1]), 1000)


class LowPassKept(unittest.TestCase):
    """11-08: changing only high-res settings keeps the low-res pass (pass1.pt)."""

    def test_store(self):
        import tempfile, os, torch
        import h3md_progress as PROG
        with tempfile.TemporaryDirectory() as d:
            s = PROG.ProgressStore(d, "A", low_key="L")
            s.save_pass1(torch.ones(2), torch.zeros(2))
            s.save_step("high_res", 1, torch.ones(2), 4)
            s = PROG.ProgressStore(d, "B", low_key="L")          # high-res settings changed
            self.assertIsNotNone(s.load_pass1())
            self.assertEqual(s._steps("high_res"), [])
            s = PROG.ProgressStore(d, "C", low_key="M")          # low-res settings changed
            self.assertIsNone(s.load_pass1())
            s = PROG.ProgressStore(d, "D")                        # old callers: no low key -> wipe as before
            self.assertIsNone(s.load_pass1())

    def test_low_key_ignores_high_res_only(self):
        import h3md_loop as L
        base = {"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22}
        p1 = D.parse_plan(plan(dict(base), length=719))
        p2 = D.parse_plan(plan(dict(base, highLength=124, highOverlap=39), length=719))
        p3 = D.parse_plan(plan(dict(base, overlap=39), length=719))
        k = lambda p, lo: L.low_pass_key("h", p, 0, 1, 1, False, None, None, lo)
        self.assertEqual(k(p1, {"low": ["a"], "high": ["x"]}), k(p2, {"low": ["a"], "high": ["y"]}))
        self.assertNotEqual(k(p1, {"low": ["a"]}), k(p3, {"low": ["a"]}))
        self.assertNotEqual(k(p1, {"low": ["a"]}), k(p1, {"low": ["b"]}))
        self.assertIsNone(L.low_pass_key("h", D.parse_plan(plan(length=345)), 0, 1, 1, False, None, None, {}))


class AutosplitMotion(unittest.TestCase):
    """11-08: the reference-motion profile reaches autosplit (0.3.5 bug: a bool overwrote it); window mode never splits,
    so it never asks for it."""

    def _raw(self, window=None):
        raw = json.loads(plan(window, length=LONG))
        raw["media"]["videos"] = [{"id": "v", "file": "v.mp4", "duration": 60.0, "hasAudio": False}]
        raw["segments"][0]["videos"] = [{"id": "v", "trimStart": 0.0, "useSoundtrack": False,
                                         "useDuration": LONG / 24}]
        return json.dumps(raw)

    def test_profile_used_in_segment_mode_only(self):
        calls = []

        def motion(seg, content):
            calls.append(content)
            return [1.0] * content
        p = D.parse_plan(self._raw(), budget=300000, motion=motion)
        self.assertGreater(len(p["segments"]), 1)
        self.assertTrue(calls, "autosplit must ask for the reference motion")
        calls.clear()
        p = D.parse_plan(self._raw({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22}),
                         budget=300000, motion=motion)
        self.assertEqual(len(p["segments"]), 1)
        self.assertEqual(calls, [])


class RefsPronouns(unittest.TestCase):
    def test_people_without_tags_keep_pictures(self):
        pr = ("subject_definitions:\n<Subject 1> is the woman in <Picture 1>.\n<Subject 2> is the man in <Picture 2>."
              "\n\nsummary:\nthey dance.\n\ndetailed_description:\nThe same rooftop: they keep swaying slowly.\n")
        kept, _ = WIN.window_refs(pr, 2)
        self.assertEqual(kept, [0, 1])
        empty = pr.replace("they keep swaying slowly.", "Empty snow mountains, no people, the wind blows.")
        kept, _ = WIN.window_refs(empty, 2)
        self.assertEqual(kept, [])

    def test_group_song_follows_the_group_clock(self):
        import torch
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 700}, segs=((294, 0), (294, 0), (345, 0), (294, 0))))
        raw["media"]["audios"] = [{"id": "s", "file": "s.mp3", "duration": 100.0}]
        st = 0
        for s in raw["segments"]:
            s["audios"] = [{"id": "s", "mode": "locked", "offset": st / 24}]
            s["audioMode"] = "locked"
            st += s["length"]
        p = D.parse_plan(json.dumps(raw))
        g = p["segments"][-1]
        self.assertIn("groupParts", g)
        self.assertNotAlmostEqual(g["locked"]["offset"], g["groupParts"][0]["seg"]["locked"]["offset"])   # drifted
        calls = []

        class FakeCore:
            class MiniMaxH3ReferenceToVideo:
                @staticmethod
                def execute(**kw):
                    calls.append(kw)
                    return ([[torch.zeros(1), {}]], {"samples": torch.zeros(1)})
        song = {"waveform": torch.arange(100 * 100).float().view(1, 1, -1), "sample_rate": 100}
        orig = D.pick_media_for
        try:
            D.pick_media_for = lambda plan, s, size="match", extra=0: {"images": [], "videos": [], "video_audios": [],
                                                                         "audios": [song], "locked_audio": None}
            pos, lat, text = D.encode_group_windows(FakeCore, p, g, None, None, None, None, "match")
        finally:
            D.pick_media_for = orig
        for c, kw in zip(pos, calls):
            a, b = c[1][WIN.RANGE_KEY]
            first = float(kw["ref_audios"]["ref_audio_0"]["waveform"][0, 0, 0])
            self.assertEqual(first, float(round((g["locked"]["offset"] + a / 24) * 100)), (a, b))


class ChainWindows(unittest.TestCase):
    """11-08 接续窗口: windows run in time order; a window's overlap with the earlier windows is pinned to their
    result of the same step (input and output), never averaged; the old fusion stays when chain is off."""

    class FakeModel:
        class latent_format:
            temporal_downscale_ratio = 4

        def resize_cond_for_context_window(self, *a, **k):
            return None

    def handler(self, chain=True, pin=0.0, length=12, overlap=4):
        import comfy.context_windows as cw
        h = cw.IndexListContextHandler(cw.get_matching_context_schedule(cw.ContextSchedules.STATIC_STANDARD),
                                       cw.get_matching_fuse_method(cw.ContextFuseMethods.PYRAMID), length, overlap,
                                       dim=2, causal_window_fix=False)
        WIN.install(h, False)
        h._h3md_chain, h._h3md_pin = chain, pin
        return h

    def run_step(self, h, x, sigma=1.0, conds=None):
        import torch
        calls = []

        def calc(model, sub_conds, sub_x, sub_t, model_options):
            w = model_options["transformer_options"]["context_window"]
            dm = ((sub_conds[0] or [{}])[0]).get("model_conds", {}).get("denoise_mask")
            calls.append((w.index_list[0], w.index_list[-1], sub_x.clone(), None if dm is None else dm.cond.clone()))
            return [torch.full_like(sub_x, float(w.index_list[0] + 1)) for _ in sub_conds]

        mo = {"transformer_options": {"sample_sigmas": torch.tensor([1.0, 0.5, 0.0])}}
        out = h.execute(calc, self.FakeModel(), conds or [[{"model_conds": {}}]], x, torch.tensor([sigma]), mo)
        return out[0], calls

    def test_order_and_no_averaging(self):
        import torch
        x = torch.randn(1, 2, 30, 4, 4)
        out, calls = self.run_step(self.handler(), x)
        starts = [c[0] for c in calls]
        self.assertEqual(starts, sorted(starts))
        self.assertGreater(len(calls), 2)
        first = {}
        for a, b, _, _ in calls:
            for i in range(a, b + 1):
                first.setdefault(i, a + 1)
        for i in range(30):                                   # every latent = the FIRST window that holds it
            self.assertTrue(torch.all(out[:, :, i] == first[i]), i)
        for k, (a, b, sub_x, dm) in enumerate(calls):
            pinned = [i for i in range(a, b + 1) if first[i] != a + 1]
            if k == 0:
                self.assertIsNone(dm)
                continue
            self.assertTrue(pinned)
            for i in pinned:                                 # input of a pinned row = the earlier result (pin 0)
                self.assertTrue(torch.all(sub_x[:, :, i - a] == first[i]))
                self.assertTrue(torch.all(dm[:, :, i - a] == 0.0))
            for i in range(a, b + 1):
                if i not in pinned:
                    self.assertTrue(torch.all(dm[:, :, i - a] == 1.0))
                    self.assertTrue(torch.equal(sub_x[:, :, i - a], x[:, :, i]))

    def test_old_fusion_averages(self):
        import torch
        x = torch.randn(1, 2, 30, 4, 4)
        out, calls = self.run_step(self.handler(chain=False), x)
        a1, b1 = calls[1][0], calls[0][1]                     # overlap of window 1 and 2
        mid = (a1 + b1) // 2
        self.assertFalse(torch.all(out[:, :, mid] == 1.0))   # fused, not window 1's value

    def test_pin_level(self):
        import torch
        x = torch.randn(1, 2, 30, 4, 4)
        s, m = 0.5, 0.5
        out, calls = self.run_step(self.handler(pin=m), x, sigma=s)
        a, b, sub_x, dm = calls[1]
        i = a                                                 # first pinned row, earlier result = 1.0
        eps = (x[:, :, i] - (1 - s) * 1.0) / s
        self.assertTrue(torch.allclose(sub_x[:, :, 0], (1 - m * s) * 1.0 + m * s * eps, atol=1e-6))
        self.assertTrue(torch.all(dm[:, :, 0] == m))

    def test_single_window_unchanged(self):
        import torch
        x = torch.randn(1, 2, 10, 4, 4)
        out, calls = self.run_step(self.handler(), x)
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0][3])
        self.assertTrue(torch.all(out == 1.0))

    def test_hard_cut_not_pinned(self):
        import torch
        x = torch.randn(1, 2, 30, 4, 4)
        conds = [[{"model_conds": {}, WIN.CUT_KEY: [51]}]]   # pixel frame 51 -> latent 15
        out, calls = self.run_step(self.handler(), x, conds=conds)
        after = [c for c in calls if c[0] >= 15]
        self.assertIsNone(after[0][3])                        # the first window after the cut: nothing pinned
        self.assertTrue(all(c[3] is None or not torch.any(c[3] == 0) or c[0] >= 15 or c[1] < 15 for c in calls))
        self.assertTrue(torch.all(out[:, :, 15] == 16.0))
        self.assertTrue(all(c[1] < 15 or c[0] >= 15 for c in calls))

    def test_option(self):
        wm = WIN.normalize_window_mode({"chain": True, "chainPin": 3, "overlap": 22})
        self.assertTrue(wm["chain"])
        self.assertEqual(wm["chainPin"], 1.0)
        self.assertEqual(wm["overlap"], 22)
        self.assertFalse(WIN.normalize_window_mode({})["chain"])


class ChainRealModel(unittest.TestCase):
    """接续窗口 on the tiny real H3 model (audio + video packed, locked audio mask): runs every step, finite output."""

    def test_two_pass_sampler(self):
        import os
        import tempfile
        import torch
        from h3_harness import FakeVAE, av_latent, empty_cond, sampler, tiny_h3_model
        import h3md_sampler as S
        import h3md_tiled_hires as TILED
        TILED.LOCAL_PATH = os.path.join(tempfile.mkdtemp(), "cal.json")
        try:
            m = WIN.apply(tiny_h3_model(), 39, 22, "pyramid", False, False, True, 0.0)
            h = m.model_options["context_handler"]
            calls = []
            orig = type(h)._h3md_chain_execute

            def spy(self, *a, **k):
                calls.append(1)
                return orig(self, *a, **k)

            type(h)._h3md_chain_execute = spy
            a_t = av_latent(t=32, h=4, w=4)["samples"].unbind()[1].shape[-1]
            lat = av_latent(t=32, h=4, w=4, audio_mask=torch.zeros(1, 1, 2, a_t))
            out = S.H3MDSampler().sample(m, empty_cond(), empty_cond(), FakeVAE(), lat, sampler("euler"),
                                         torch.tensor([1.0, 0.95, 0.9, 0.8, 0.6, 0.3, 0.0]), seed=7, cfg=1.0,
                                         transition_step=4, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0,
                                         upscaler_model="none")
            v, a = out[0]["samples"].unbind()
            self.assertEqual(len(calls), 6)
            self.assertEqual(tuple(v.shape), (1, 24, 32, 4, 4))
            self.assertTrue(bool(torch.isfinite(v).all()) and bool(torch.isfinite(a).all()))
        finally:
            TILED.LOCAL_PATH = None


class ContinuationNoCut(unittest.TestCase):
    """11-08 2:20 MV: the S2 -> S3 continuation came out as a cut - the window prompt repeated S3's style preamble
    and its own '[Shot 1]'.  The later shot now adds only its new sentences, without a shot tag, as 'with no cut'."""

    def test_combined_text(self):
        style = "Photorealistic footage, no captions. All motion plays at natural real-time speed."
        a = (f"summary:\nx.\n\ndetailed_description:\n{style} [Shot 1] One tracking shot: she walks toward the "
             "camera. Her face keeps the exact identity of <Picture 1>. She is the only person.\n\n")
        b = (f"summary:\ny.\n\ndetailed_description:\n{style} [Shot 1] The same tracking shot continues: she stops "
             "at the railing. Her face keeps the exact identity of <Picture 1>. She is the only person.\n\n")
        out = WIN.combine_part_prompts([(0, a), (8, b)])
        body = WIN._detail_body(out)
        self.assertEqual(body.count("[Shot"), 1)
        self.assertEqual(body.count("Photorealistic footage"), 1)
        self.assertEqual(body.count("She is the only person."), 1)
        self.assertIn("At about 8 s into this part, with no cut, the same shot continues: The same tracking shot "
                      "continues: she stops at the railing.", body)


class WindowPlanExportImport(unittest.TestCase):
    """11-08 (YJ): plan export / import keeps window mode, and the imported plan renders the same groups."""

    def test_roundtrip(self):
        import tempfile, os
        import h3md_plan_io as P
        src = tempfile.mkdtemp()
        os.makedirs(os.path.join(src, "h3md_chars"))
        for f in ("h3md_chars/Eva.png", "h3md_chars/Peter.png", "song.mp3"):
            open(os.path.join(src, f), "wb").write(b"fake")
        raw = json.loads(multi_plan({"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22,
                                     "groupLength": 1450, "redraw": [[4, 8]]}, segs=((294, 0), (362, 39), (243, 0))))
        raw["media"] = {"images": [{"id": "eva", "file": "h3md_chars/Eva.png"},
                                   {"id": "peter", "file": "h3md_chars/Peter.png"}],
                        "videos": [], "audios": [{"id": "song", "file": "song.mp3", "duration": 100.0}], "refmods": []}
        st = 0
        for i, s in enumerate(raw["segments"]):
            st = st - s["overlapIn"] if i else 0
            s["images"] = ["eva"] if i < 2 else ["eva", "peter"]
            s["audios"] = [{"id": "song", "mode": "locked", "offset": st / 24}]
            s["audioMode"] = "locked"
            s["prompt"] = ("summary:\nx.\n\ndetailed_description:\n<Subject 1> walks. At 00:02.0 she sings: "
                           "<d>[English] la</d>.\n")
            st += s["length"]
        data, man = P.export_plan(json.dumps(raw), {"nodes": []}, src, with_media=True, name="Eva Peter")
        dst = tempfile.mkdtemp()
        out = P.import_plan(data, dst, "Eva Peter.zip")
        self.assertEqual(out["problems"], [])
        back = json.loads(out["plan_json"])
        self.assertEqual(back["windowMode"], raw["windowMode"])
        self.assertEqual(back["segments"][1]["audios"], raw["segments"][1]["audios"])
        a, b = D.parse_plan(json.dumps(raw)), D.parse_plan(json.dumps(back))
        self.assertEqual([(s["startFrame"], s["length"], s.get("windowCuts")) for s in a["segments"]],
                         [(s["startFrame"], s["length"], s.get("windowCuts")) for s in b["segments"]])
        self.assertEqual(len(b["segments"][0]["groupParts"]), 3)


class DepthOff(unittest.TestCase):
    def _raw(self, window, prompt="shot"):
        raw = json.loads(plan(window, length=345))
        raw["media"]["videos"] = [{"id": "v", "file": "v.mp4", "duration": 20.0, "hasAudio": False}]
        raw["segments"][0]["videos"] = [{"id": "v", "depth": True, "useSoundtrack": False}]
        raw["segments"][0]["prompt"] = prompt
        return json.dumps(raw)

    def test_window_mode_turns_depth_off(self):
        seg = D.parse_plan(self._raw(True))["segments"][0]
        self.assertFalse(seg["videos"][0].get("depth"))
        seg = D.parse_plan(self._raw(None))["segments"][0]
        self.assertTrue(seg["videos"][0].get("depth"))

    def test_motion_copy_turns_depth_off(self):
        seg = D.parse_plan(self._raw(None, "motion_transfer: shot"))["segments"][0]
        self.assertFalse(seg["videos"][0].get("depth"))


class RefModOff(unittest.TestCase):
    def test_window_mode_drops_refmods(self):
        import h3md_director_refmod as R
        raw = json.loads(plan(True, length=345))
        raw["media"]["refmods"] = [{"id": "eve", "file": "character/eve"}]
        raw["segments"][0]["refmods"] = ["eve"]
        p = D.parse_plan(json.dumps(raw))
        notes = R.parse_refmods(raw, p, {})
        self.assertTrue(any("RefMods off" in n for n in notes))
        self.assertFalse(p["segments"][0].get("refmods"))
        self.assertEqual(p["media"]["refmods"], {})


class LocalRedraw(unittest.TestCase):
    def test_masks(self):
        vm, am, active = WIN.redraw_masks(102, 600, [(170, 255)])
        self.assertEqual(float(vm[60]), 1.0)
        self.assertEqual(float(vm[0]), 0.0)
        self.assertEqual(float(vm[101]), 0.0)
        self.assertTrue(0 < float(vm[active[0]]) < 1)        # soft ramp at the edge
        self.assertGreater(float(am.sum()), 0)
        self.assertEqual(WIN.redraw_ranges({"redraw": [[7, 10.5]]}, 0, 345), [(168, 252)])
        self.assertEqual(WIN.redraw_ranges({"redraw": [[7, 10.5]]}, 300, 345), [])
        self.assertEqual(WIN.redraw_ranges(None, 0, 345), [])

    def test_redraw_latent(self):
        import torch
        import tempfile
        import comfy.nested_tensor as NT
        import h3md_loop as L
        v = torch.randn(1, 24, 102, 4, 4)
        a = torch.randn(1, 8, 1, 600)
        with tempfile.TemporaryDirectory() as d:
            cache = C.SegmentCache(d, "abc" * 6, True)
            torch.save({"video": v, "audio": a}, cache._p(0, "_latent.pt"))
            cur = {"samples": NT.NestedTensor((torch.zeros_like(v), torch.zeros_like(a)))}
            lat, active, seed = L.redraw_latent(cache, 0, cur, [(170, 255)])
            vv, aa = lat["samples"].unbind()
            self.assertTrue(torch.equal(vv, v))
            vm, am = lat["noise_mask"].unbind()
            self.assertEqual(tuple(vm.shape), (1, 1, 102, 1, 1))
            self.assertEqual(float(vm[0, 0, 0, 0, 0]), 0.0)
            self.assertEqual(float(vm[0, 0, 60, 0, 0]), 1.0)
            self.assertIsNotNone(active)

    def test_signature_ignores_redraw(self):
        a = D.parse_plan(plan({"length": 243}, length=719))
        b = D.parse_plan(plan({"length": 243, "redraw": [[3, 6]]}, length=719))
        self.assertEqual(C.segment_signature(a, 0), C.segment_signature(b, 0))
