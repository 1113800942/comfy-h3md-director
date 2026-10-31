"""H3MD rerun (2026-10-30): rerun_segments / stop_after, tail lock, forced cache reuse, old-file backup.

CPU, tiny real H3 model (h3_harness).  Run from the repo root:  python -m pytest -q tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from h3_harness import FakeVAE, av_latent, empty_cond, sampler, tiny_h3_model  # noqa: E402  (CPU first)

import torch  # noqa: E402

import h3md_director as D  # noqa: E402
import h3md_sampler as S  # noqa: E402
import h3md_loop as L  # noqa: E402
import h3md_loop_split as LS  # noqa: E402
import h3md_lowres_anchor as LA  # noqa: E402

SIG = torch.tensor([1.0, 0.95, 0.9, 0.8, 0.6, 0.3, 0.0])   # 6 steps: 4 low + 2 high
SEG_LEN = 107            # 32 video tokens
OV = 39                  # 12 video tokens


def streams(latent):
    return latent["samples"].unbind()


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(L.parse_segments("", 16), [])
        self.assertEqual(L.parse_segments("3", 16), [2])
        self.assertEqual(L.parse_segments("4-6", 16), [3, 4, 5])
        self.assertEqual(L.parse_segments("8-10, 13-14, 16", 16), [7, 8, 9, 12, 13, 15])
        self.assertEqual(L.parse_segments("8～10，13、16", 16), [7, 8, 9, 12, 15])
        self.assertEqual(L.parse_segments("6-4", 16), [3, 4, 5])
        with self.assertRaises(ValueError):
            L.parse_segments("17", 16)
        with self.assertRaises(ValueError):
            L.parse_segments("0", 16)
        with self.assertRaises(ValueError):
            L.parse_segments("a-b", 16)

    def test_stop_after(self):
        plan = {"segments": [{}] * 5}
        self.assertEqual(L.segment_range(plan, 0), [0, 1, 2, 3, 4])
        self.assertEqual(L.segment_range(plan, 1), [0])
        with self.assertRaises(ValueError):
            L.segment_range(plan, 6)

    def test_inputs_on_top_and_removed(self):
        spec = LS.H3MDLoopSampler2.INPUT_TYPES()
        names = list(spec["required"]) + list(spec["optional"])
        self.assertEqual(names[:3], ["rerun_segments", "stop_after", "rerun_new_seed"])
        for gone in ("start_index", "end_index", "previous_latent"):
            self.assertNotIn(gone, names)
        self.assertNotIn("rerun_segments", LS.option_defaults())


class TailLockTests(unittest.TestCase):
    def setUp(self):
        self.model = tiny_h3_model()
        self.vae = FakeVAE()

    def test_continue_latent_tail(self):
        t = D.video_tokens(SEG_LEN)
        latent = av_latent(t=t, h=8, w=8, seed=1)
        prev = av_latent(t=t, h=8, w=8, seed=2)
        nxt = av_latent(t=t, h=8, w=8, seed=3)
        out, info = D.continue_latent(latent, prev, OV, True, None, next_latent=nxt, tail_overlap=OV)
        v, a = streams(out)
        vm, am = out["noise_mask"].unbind()
        tt = D.video_tokens(OV)
        self.assertEqual(info["tail_tokens"], tt)
        self.assertTrue(torch.equal(v[:, :, -tt:], streams(nxt)[0][:, :, :tt]))
        self.assertTrue(torch.equal(v[:, :, :tt], streams(prev)[0][:, :, -tt:]))
        self.assertTrue(bool((vm[:, :, -tt:] == 0).all()) and bool((vm[:, :, tt:-tt] == 1).all()))
        at = info["tail_audio_tokens"]
        self.assertGreater(at, 0)
        self.assertTrue(torch.equal(a[..., -at:], streams(nxt)[1][..., :at]))
        self.assertTrue(bool((am[..., -at:] == 0).all()))

    def test_tail_only_and_too_long(self):
        t = D.video_tokens(SEG_LEN)
        latent = av_latent(t=t, h=8, w=8, seed=1)
        nxt = av_latent(t=t, h=8, w=8, seed=3)
        out, info = D.continue_latent(latent, None, 0, True, None, next_latent=nxt, tail_overlap=OV)
        self.assertEqual(info["video_tokens"], 0)
        self.assertEqual(info["tail_tokens"], D.video_tokens(OV))
        with self.assertRaises(ValueError):
            D.continue_latent(av_latent(t=12, h=8, w=8), None, 0, True, None, next_latent=nxt, tail_overlap=OV)

    def test_sampler_keeps_tail_exactly_and_uses_low_tail(self):
        t = D.video_tokens(SEG_LEN)
        tt = D.video_tokens(OV)
        latent = av_latent(t=t, h=8, w=8, seed=1)
        prev = av_latent(t=t, h=8, w=8, seed=2)
        nxt = av_latent(t=t, h=8, w=8, seed=3)
        lat, info = D.continue_latent(latent, prev, OV, True, None, next_latent=nxt, tail_overlap=OV)
        model, _ = D.install_drift(self.model, t, streams(lat)[1].shape[-1], info["video_tokens"], 2, 0.6)
        low_tail = torch.full((1, 24, tt, 4, 4), 0.125)
        self.assertIsNone(S.check_low_res_tail(low_tail, lat, 0.5))
        self.assertIsNotNone(S.check_low_res_tail(low_tail, av_latent(t=t, h=8, w=8), 0.5))   # no mask
        node = S.H3MDSampler()
        out = node.sample(model, empty_cond(), empty_cond(), self.vae, lat, sampler("euler"), SIG, seed=7, cfg=1.0,
                          transition_step=4, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0, upscaler_model="none",
                          highres_tiling=False, h3md_capture_lowres=True, h3md_low_res_suffix=low_tail)[0]
        v, _ = streams(out)
        self.assertTrue(torch.equal(v[:, :, -tt:], streams(nxt)[0][:, :, :tt].to(v)))
        self.assertTrue(torch.equal(v[:, :, :tt], streams(prev)[0][:, :, -tt:].to(v)))
        self.assertTrue(torch.equal(node.last_lowres[:, :, -tt:], low_tail))
        self.assertFalse(torch.equal(v[:, :, tt:-tt], streams(lat)[0][:, :, tt:-tt].to(v)))   # middle generated

    def test_sampler_lowres_anchor_keeps_locked_rows(self):
        t = D.video_tokens(SEG_LEN)
        tt = D.video_tokens(OV)
        latent = av_latent(t=t, h=8, w=8, seed=1)
        prev = av_latent(t=t, h=8, w=8, seed=2)
        lat, info = D.continue_latent(latent, prev, OV, True, None)
        model, _ = D.install_drift(self.model, t, streams(lat)[1].shape[-1], info["video_tokens"], 2, 0.6)
        head = torch.full((1, 24, tt, 4, 4), 0.25)
        kw = dict(transition_step=4, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0, upscaler_model="none",
                  highres_tiling=False, h3md_capture_lowres=True, h3md_low_res_prefix=head)
        held = LA.held_rows(lat["noise_mask"].unbind()[0], t)
        plain = S.H3MDSampler()
        plain.sample(model, empty_cond(), empty_cond(), self.vae, lat, sampler("euler"), SIG, seed=7, cfg=1.0, **kw)
        self.assertIsNone(plain.last_anchor)
        base = LA.stats(plain.last_lowres, ~held)
        ref = {"mean": [m + 0.2 for m in base["mean"]], "std": [x * 1.3 for x in base["std"]]}
        node = S.H3MDSampler()
        out = node.sample(model, empty_cond(), empty_cond(), self.vae, lat, sampler("euler"), SIG, seed=7, cfg=1.0,
                          h3md_lowres_anchor={"strength": 1.0, "ref": ref}, **kw)[0]
        v, _ = streams(out)
        self.assertTrue(torch.equal(v[:, :, :2], streams(prev)[0][:, :, -tt:][:, :, :2].to(v)))  # locked rows exact
        self.assertIsNotNone(node.last_anchor)
        low = node.last_lowres
        self.assertTrue(torch.equal(low[:, :, :2], head[:, :, :2]))       # locked carry rows untouched
        got = LA.stats(low, ~held)                                        # carry corrected to the reference
        self.assertTrue(torch.allclose(torch.tensor(got["mean"]), torch.tensor(ref["mean"]), atol=1e-3))
        self.assertTrue(torch.allclose(torch.tensor(got["std"]), torch.tensor(ref["std"]), atol=1e-3))
        self.assertFalse(torch.allclose(plain.last_lowres, low))


class AnchorMathTests(unittest.TestCase):
    def test_transform_matches_reference_and_skips_held(self):
        g = torch.Generator().manual_seed(0)
        x = torch.randn((1, 24, 10, 4, 4), generator=g) * 1.7 + 0.4
        held = torch.zeros(10, dtype=torch.bool)
        held[:3] = True
        ref = {"mean": [0.0] * 24, "std": [1.0] * 24}
        xf = LA.transform(x, held, ref, 1.0)
        y = LA.apply(x, xf)
        self.assertTrue(torch.equal(y[:, :, :3], x[:, :, :3]))
        st = LA.stats(y, ~held)
        self.assertTrue(torch.allclose(torch.tensor(st["mean"]), torch.zeros(24), atol=1e-4))
        self.assertTrue(torch.allclose(torch.tensor(st["std"]), torch.ones(24), atol=1e-4))
        half = LA.apply(x, LA.transform(x, held, ref, 0.5))
        self.assertTrue(torch.allclose(half, (x + y) / 2, atol=1e-5))
        self.assertIsNone(LA.transform(x, held, ref, 0.0))
        self.assertIsNone(LA.transform(x, torch.ones(10, dtype=torch.bool), ref, 1.0))


# ---------------------------------------------------------------------------
# Loop end to end with a fake encoder / sampler (real cache, real continuation)
# ---------------------------------------------------------------------------

class FakeEncoder:
    INPUT_TYPES = D.H3MDSegmentEncode.INPUT_TYPES
    def encode(self, plan, k, clip, vae, audio_vae, ref_image_size="match"):
        seg = plan["segments"][k]
        lat = av_latent(t=D.video_tokens(seg["length"]), h=8, w=8, seed=100 + k)
        return (empty_cond(), empty_cond(), {"samples": lat["samples"]}, "p", "[]", seg["length"], seg["overlapIn"],
                128, 128, None)


class FakeSampler:
    calls = []
    INPUT_TYPES = S.H3MDSampler.INPUT_TYPES

    def __init__(self):
        self.last_lowres = None
        self.last_tiling = None
        self.last_steps = []
        self.last_anchor = None

    def sample(self, low_res_model=None, latent_image=None, seed=0, h3md_low_res_prefix=None,
               h3md_low_res_suffix=None, h3md_lowres_anchor=None, **kw):
        v, a = streams(latent_image)
        nm = latent_image.get("noise_mask")
        vm, am = nm.unbind() if nm is not None else (torch.ones_like(v[:, :1, :, :1, :1]), torch.ones_like(a))
        g = torch.Generator().manual_seed(int(seed) % (2 ** 31))
        v2 = torch.where(vm == 0, v, torch.randn(v.shape, generator=g))
        a2 = torch.where(am == 0, a, torch.randn(a.shape, generator=g))
        low = torch.randn((1, 24, v.shape[2], 4, 4), generator=g)
        if h3md_low_res_prefix is not None:
            low[:, :, :h3md_low_res_prefix.shape[2]] = h3md_low_res_prefix
        if h3md_low_res_suffix is not None:
            low[:, :, -h3md_low_res_suffix.shape[2]:] = h3md_low_res_suffix
        self.last_lowres = low
        FakeSampler.calls.append({"seed": seed, "latent": latent_image, "suffix": h3md_low_res_suffix,
                                  "prefix": h3md_low_res_prefix, "anchor": h3md_lowres_anchor})
        self.last_anchor = {"strength": h3md_lowres_anchor["strength"]} if h3md_lowres_anchor else None
        return ({"samples": D._nested((v2, a2))},)


def fake_audio(audio_vae, a):
    return {"waveform": torch.zeros((1, 2, 48000 * 5)), "sample_rate": 48000}


def make_plan(n=4):
    segs = []
    for k in range(n):
        segs.append({"index": k, "length": SEG_LEN, "overlapIn": 0 if k == 0 else OV, "startFrame": k * (SEG_LEN - OV),
                     "prompt": f"segment {k}", "images": [], "videos": [], "audios": [], "labels": []})
    return {"width": 128, "height": 128, "fps": 24, "segments": segs, "media": {"images": {}, "videos": {}, "audios": {}}}


class LoopRerunTests(unittest.TestCase):
    def setUp(self):
        self.model = tiny_h3_model()
        self.tmp = tempfile.TemporaryDirectory()
        FakeSampler.calls = []
        self.patches = [patch.object(L.D, "H3MDSegmentEncode", FakeEncoder),
                        patch.object(L.S, "H3MDSampler", FakeSampler),
                        patch.object(L, "decode_audio", fake_audio),
                        patch.object(L, "unload_all_models", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_loop(self, plan, drift=None, **kw):
        opts = dict(LS.option_defaults(), cache_dir=self.tmp.name, release_vram_between_segments=False)
        opts.update(drift or {})
        args = dict(plan=plan, clip=None, vae=FakeVAE(), audio_vae=None, low_res_model=self.model,
                    sampler=sampler("euler"), sigmas=SIG, seed=5, cfg=1.0, transition_step=4, lowres_scale=0.5,
                    rho=0.0, w_min=0.5, w_max=1.0, upscaler_model="none", seed_mode="increment", decode_output=True,
                    high_res_model=None, highres_tiling=False, cache_mode="disk", delete_cache=False,
                    resume_from_cache=True, rerun_segments="", stop_after=0, rerun_new_seed=True)
        args.update(kw)
        node = LS.H3MDLoopSampler2()
        out = node.run(options=opts, **args)
        return node, out

    def cache_dir(self):
        dirs = [d for d in os.listdir(self.tmp.name) if d.startswith("resume_")]
        self.assertEqual(len(dirs), 1)
        return os.path.join(self.tmp.name, dirs[0])

    def latent(self, k):
        d = torch.load(os.path.join(self.cache_dir(), f"seg_{k:03d}_latent.pt"), weights_only=True)
        return d["video"], d["audio"]

    def test_rerun_middle_block_with_tail_lock(self):
        plan = make_plan(4)
        self.run_loop(plan)
        self.assertEqual(len(FakeSampler.calls), 4)
        old = {k: self.latent(k) for k in range(4)}
        old_low3 = torch.load(os.path.join(self.cache_dir(), "seg_003_lowres.pt"), weights_only=True)["video"]

        FakeSampler.calls = []
        plan2 = make_plan(4)
        plan2["segments"][1]["prompt"] = "changed refs/prompt"   # keys of 1..3 change; 0 and 3 must still be reused
        node, out = self.run_loop(plan2, rerun_segments="2-3")
        self.assertEqual(len(FakeSampler.calls), 2)                # only segments 2 and 3 (1-based)
        tt = D.video_tokens(OV)
        c1, c2 = FakeSampler.calls
        self.assertIsNone(c1["suffix"])                            # next one is re-rendered too: no tail lock
        self.assertIsNotNone(c2["suffix"])
        self.assertTrue(torch.equal(c2["suffix"], old_low3[:1, :, :tt]))
        new1, new2 = self.latent(1), self.latent(2)
        self.assertTrue(torch.equal(new1[0][:, :, :tt], old[0][0][:, :, -tt:]))    # head from cached segment 1
        self.assertTrue(torch.equal(new2[0][:, :, :tt], new1[0][:, :, -tt:]))      # head from new segment 2
        self.assertTrue(torch.equal(new2[0][:, :, -tt:], old[3][0][:, :, :tt]))    # tail locked to old segment 4
        self.assertFalse(torch.equal(new2[0], old[2][0]))
        self.assertTrue(torch.equal(self.latent(3)[0], old[3][0]))                # segment 4 untouched
        self.assertTrue(torch.equal(self.latent(0)[0], old[0][0]))
        arch = [d for d in os.listdir(self.cache_dir()) if d.startswith("_replaced_")]
        self.assertEqual(len(arch), 1)
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(self.cache_dir(), arch[0])) if f.endswith(".json")),
                         ["seg_001.json", "seg_002.json"])
        self.assertIn('"rerun": true', out[4])
        self.assertIn("tail_lock", out[4])
        self.assertEqual(out[3], 4 * SEG_LEN - 3 * OV)             # full video

        FakeSampler.calls = []                                      # a normal resume afterwards: everything cached
        self.run_loop(plan2)
        self.assertEqual(len(FakeSampler.calls), 0)

    def test_lowres_anchor_option(self):
        plan = make_plan(3)
        self.run_loop(plan)
        self.assertTrue(all(c["anchor"] is None for c in FakeSampler.calls))
        low0 = torch.load(os.path.join(self.cache_dir(), "seg_000_lowres.pt"), weights_only=True)["video"]
        FakeSampler.calls = []
        _, out = self.run_loop(plan, drift={"lowres_anchor": 1.0, "drift_handoff_match": 0.9})  # old name: ignored
        self.assertEqual(len(FakeSampler.calls), 2)              # segment 1 from the cache; 2-3 re-render (new key)
        ref = LA.stats(low0)
        for c in FakeSampler.calls:
            self.assertEqual(c["anchor"]["strength"], 1.0)
            self.assertEqual(c["anchor"]["ref"], ref)            # reference = the shot's first segment (cached)
        self.assertIn("lowres_anchor", out[4])
        FakeSampler.calls = []                                   # option off again: old keys, segment 1 still cached
        self.run_loop(plan)
        self.assertEqual(len(FakeSampler.calls), 2)
        self.assertTrue(all(c["anchor"] is None for c in FakeSampler.calls))

    def test_options_removed(self):
        for gone in ("drift_handoff_match", "drift_seam_anchor", "drift_detail"):
            self.assertNotIn(gone, LS.option_defaults())
        self.assertEqual(LS.option_defaults()["lowres_anchor"], 0.0)

    def test_stop_after_then_full(self):
        plan = make_plan(3)
        _, out = self.run_loop(plan, stop_after=1)
        self.assertEqual(len(FakeSampler.calls), 1)
        self.assertEqual(out[3], SEG_LEN)
        FakeSampler.calls = []
        self.run_loop(plan)
        self.assertEqual(len(FakeSampler.calls), 2)                # segment 1 from the cache

    def test_rerun_needs_cache_and_resume(self):
        plan = make_plan(3)
        with self.assertRaises(ValueError):
            self.run_loop(plan, rerun_segments="2")                 # nothing cached
        self.assertEqual(len(FakeSampler.calls), 0)
        with self.assertRaises(ValueError):
            self.run_loop(plan, rerun_segments="2", resume_from_cache=False)

    def test_rerun_last_and_first(self):
        plan = make_plan(3)
        self.run_loop(plan)
        old1 = self.latent(1)
        FakeSampler.calls = []
        self.run_loop(plan, rerun_segments="1, 3", rerun_new_seed=False)
        self.assertEqual(len(FakeSampler.calls), 2)
        tt = D.video_tokens(OV)
        first, last = FakeSampler.calls
        self.assertIsNotNone(first["suffix"])                       # segment 1: tail locked to segment 2
        self.assertIsNone(last["suffix"])                           # last segment: head only
        self.assertTrue(torch.equal(self.latent(0)[0][:, :, -tt:], old1[0][:, :, :tt]))
        self.assertEqual(first["seed"], 5)                          # rerun_new_seed off: original seed
        self.assertTrue(torch.equal(self.latent(1)[0], old1[0]))


if __name__ == "__main__":
    unittest.main()


class ControlNetOnlyTests(unittest.TestCase):
    """2026-10-30 ctlonly: a depth video drives only the ControlNet - automatic, no switch, no <Video N> label."""

    MEDIA = {"images": {"p1": {"file": "a.png"}}, "videos": {"v": {"file": "v.mp4", "duration": 10}},
             "audios": {"m": {"file": "m.mp3"}}}

    def seg(self, depth=True, soundtrack=False):
        v = {"id": "v", "trimStart": 0, "useSoundtrack": soundtrack}
        if depth:
            v["depth"] = True
        return {"images": ["p1"], "videos": [v], "audios": ["m"], "audioMode": "reference", "length": 107}

    def test_no_switch_and_route_key(self):
        import h3md_depth_control as DC
        spec = DC.H3MDDepthControl.INPUT_TYPES()
        self.assertNotIn("video_reference", {**spec["required"], **spec["optional"]})
        self.assertEqual(DC.ROUTE, "controlnet_only")

    def test_labels(self):
        tags = lambda s: [l["tag"] for l in D.compute_labels(s, self.MEDIA)]
        self.assertEqual(tags(self.seg(depth=True)), ["<Picture 1>", "<Audio 1>"])            # no <Video>
        self.assertEqual(tags(self.seg(depth=False)), ["<Picture 1>", "<Video 1>", "<Audio 1>"])
        self.assertEqual(tags(self.seg(depth=True, soundtrack=True)),                        # soundtrack: kept
                         ["<Picture 1>", "<Audio 1>", "<Video 1>", "<Audio 2>"])
        self.assertTrue(D.depth_control_only(self.seg(), self.seg()["videos"][0], self.MEDIA))
        s = self.seg(soundtrack=True)
        self.assertFalse(D.depth_control_only(s, s["videos"][0], self.MEDIA))

    def test_without_video(self):
        import h3md_depth_control as DC
        seg = self.seg(depth=False)
        seg["labels"] = D.compute_labels(seg, self.MEDIA)
        plan = {"segments": [seg], "media": self.MEDIA}
        out = DC.without_video(plan, 0, "v")
        self.assertEqual([l["tag"] for l in out["segments"][0]["labels"]], ["<Picture 1>", "<Audio 1>"])
        self.assertEqual(len(plan["segments"][0]["videos"]), 1)               # input untouched

    def test_loop_errors_without_depth_control(self):
        plan = make_plan(2)
        plan["segments"][1]["videos"] = [{"id": "v", "trimStart": 0, "useSoundtrack": False, "depth": True}]
        node = LS.H3MDLoopSampler2()
        with self.assertRaises(ValueError) as cm:
            node.run(options=dict(LS.option_defaults()), plan=plan, clip=None, vae=FakeVAE(), audio_vae=None,
                     low_res_model=None, sampler=None, sigmas=SIG, seed=1, cfg=1.0, transition_step=4,
                     lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0, upscaler_model="none", decode_output=True,
                     cache_mode="disk", resume_from_cache=False)
        self.assertIn("Depth Control", str(cm.exception))
