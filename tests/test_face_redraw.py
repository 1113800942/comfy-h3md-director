"""0.5.3 face redraw by mask: units, latent grouping, mask cleanup and latent mapping, stores, node calls, save wiring."""
import os
import sys
from unittest import mock

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_face_redraw as RD  # noqa: E402
import h3md_face_refine_save as F  # noqa: E402


def test_units_and_crossfade():
    assert RD.valid_length(124) == 124 and RD.valid_length(130) == 124 and RD.valid_length(4) == 0
    sp = RD.unit_spans(362, 124)
    assert sp[0] == (0, 124) and sp[-1] == (238, 362) and all((b - a) == 124 for a, b in sp)
    assert all(sp[k + 1][0] <= sp[k][1] - 17 for k in range(len(sp) - 1))
    assert RD.unit_spans(100, 124) == [(0, 90)] + [(10, 100)] and RD.unit_spans(362) == [(0, 362)]
    assert RD.span_weight((0, 124), 0, 362) == 1.0 and RD.span_weight((107, 231), 107, 362) < 0.1


def test_latent_groups_match_h3_vae():
    g = RD.latent_groups(124)
    assert len(g) == 37 and g[:3] == [(0, 1), (1, 5), (5, 9)] and g[5] == (17, 18) and g[-1] == (120, 124)
    assert len(RD.latent_groups(362)) == 107 and len(RD.latent_groups(22)) == 7
    assert sum(e - s for s, e in RD.latent_groups(362)) == 362
    assert len(RD.even_groups(10, 3)) == 3


def _blob(T=22, H=128, W=128):
    m = torch.zeros(T, H, W)
    m[:, 40:90, 40:90] = 1.0          # the head, every frame
    m[3, 5:8, 5:8] = 1.0              # a speck
    m[10:12, 100:125, 100:125] = 1.0  # a 2-frame flicker
    return m


def test_clean_masks_drops_specks_and_flicker():
    c = RD.clean_masks(_blob(), shrink=4, min_frames=4, edge_grow=2)
    assert float(c[:, 60, 60].min()) == 1.0
    assert float(c[3, 5:8, 5:8].max()) == 0.0 and float(c[10:12, 100:125, 100:125].max()) == 0.0
    assert float(RD.clean_masks(torch.zeros(5, 32, 32)).max()) == 0.0


def test_to_latent_mask_shape_grow_tokens():
    m = torch.zeros(22, 128, 128)
    m[8:9, 64:66, 64:66] = 1.0
    lm = RD.to_latent_mask(m, 7, grow_px=0, grow_frames=1)
    assert lm.shape == (7, 8, 8)
    on = (lm > 0).nonzero().tolist()
    # frames 7..9 -> latents 2 (5-8) and 3 (9-12); pixel 64 -> cell 4, token 2x2 -> cells 4..5
    assert {t for t, _, _ in on} == {2, 3} and {y for _, y, _ in on} == {4, 5} and {x for _, _, x in on} == {4, 5}
    grown = RD.to_latent_mask(m, 7, grow_px=36, grow_frames=0)
    assert int((grown[2] > 0).sum()) > int((lm[2] > 0).sum())
    odd = RD.to_latent_mask(m, 5, grow_px=0, grow_frames=0)   # unexpected latent count: even split
    assert odd.shape[0] == 5


def test_reference_size_and_audio():
    h, w = RD.reference_size(512, 512)
    assert h % 32 == 0 and w % 32 == 0 and abs(h * w - 2e6) < 1.5e5
    a = {"waveform": torch.ones(1, 2, 48000), "sample_rate": 48000}
    s = RD.slice_audio(a, 12, 36, 24.0)
    assert s["waveform"].shape[-1] == 48000
    tail = RD.slice_audio(a, 20, 44, 24.0)          # runs past the end: padded with silence
    assert tail["waveform"].shape[-1] == 48000 and float(tail["waveform"][..., -10:].abs().max()) == 0.0
    assert RD.slice_audio(None, 0, 10) is None


def test_store_roundtrip_and_key(tmp_path):
    st = RD.UnitStore(str(tmp_path / "m"), 362, "k1")
    st.put((0, 124), torch.full((124, 8, 8), 1.0))
    st.put((107, 231), torch.zeros((124, 8, 8)))
    assert float(st.frame(0).mean()) == 1.0 and st.frame(300) is None
    mid = float(st.frame(115).mean())
    assert 0.0 < mid < 1.0
    st.close()
    again = RD.UnitStore(str(tmp_path / "m"), 362, "k1")
    assert again.has((0, 124)) and again.has((107, 231))
    other = RD.UnitStore(str(tmp_path / "m"), 362, "k2")
    assert not other.spans


def test_pick_object_prefers_centre():
    a = torch.zeros(3, 64, 64)
    a[:, 0:10, 0:10] = 1
    b = torch.zeros(3, 64, 64)
    b[:, 28:36, 28:36] = 1
    assert RD.pick_object([a, b]) == 1


class _Node:
    calls = []

    def __init__(self, name, fn):
        self.name, self.fn = name, fn

    def execute(self, **kw):
        _Node.calls.append((self.name, kw))
        return self.fn(**kw)


def _registry():
    T, H, W = 22, 64, 64

    def node(name, fn):
        n = _Node(name, fn)
        return type(name, (), {"define_schema": classmethod(lambda c: None),
                               "execute": classmethod(lambda c, **kw: n.execute(**kw))})
    return {
        "DifferentialDiffusion": node("DD", lambda model, strength: ("dd-model",)),
        "MiniMaxH3ReferenceToVideo": node("R2V", lambda **kw: ("cond", {"samples": None})),
        "VAEEncode": node("VAEEncode", lambda pixels, vae: ({"samples": torch.zeros(1, 4, 7, H // 16, W // 16)},)),
        "SetLatentNoiseMask": node("SLNM", lambda samples, mask: (dict(samples, noise_mask=mask),)),
        "VAEEncodeAudio": node("VAEEncodeAudio", lambda audio, vae: ({"samples": torch.zeros(1, 8, 30)},)),
        "SolidMask": node("SolidMask", lambda value, width, height: (torch.full((1, height, width), value),)),
        "LTXVConcatAVLatent": node("Concat", lambda video_latent, audio_latent: ({"samples": video_latent["samples"],
                                                                                  "v": video_latent,
                                                                                  "a": audio_latent},)),
        "BasicScheduler": node("Sched", lambda model, scheduler, steps, denoise: (torch.linspace(1, 0, steps + 1),)),
        "BasicGuider": node("Guider", lambda model, conditioning: ("guider",)),
        "KSamplerSelect": node("KSS", lambda sampler_name: ("sampler",)),
        "RandomNoise": node("Noise", lambda noise_seed: ("noise",)),
        "SamplerCustomAdvanced": node("SCA", lambda **kw: (kw["latent_image"],)),
        "VAEDecode": node("VAEDecode", lambda samples, vae: (torch.full((1, T, H, W, 3), 0.25),)),
    }


def test_redraw_recipe_calls():
    _Node.calls = []
    p = RD.H3MaskRedraw(_registry(), "model", "clip", "vae", "avae", torch.rand(1, 300, 200, 3))
    assert p.ref.shape[1] % 32 == 0 and p.ref.shape[2] % 32 == 0
    m = torch.zeros(22, 64, 64)
    m[:, 20:40, 20:40] = 1
    out = p.run(torch.rand(22, 64, 64, 3), m, RD.silence(22), unit_index=3)
    assert out.shape == (22, 64, 64, 3) and float(out.mean()) == 0.25
    by = {}
    for name, kw in _Node.calls:
        by.setdefault(name, []).append(kw)
    assert by["Sched"][0] == {"model": "model", "scheduler": "beta", "steps": 4, "denoise": 0.8}
    assert by["Guider"][0]["model"] == "dd-model" and by["KSS"][0]["sampler_name"] == "euler"
    assert by["Noise"][0]["noise_seed"] == 45
    assert by["R2V"][0]["prompt"] == "" and "ref_image_0" in by["R2V"][0]["ref_images"]
    assert by["SolidMask"][0]["value"] == 0.0
    video_mask, audio_mask = by["SLNM"][0]["mask"], by["SLNM"][1]["mask"]
    assert video_mask.shape == (7, 4, 4) and float(video_mask.max()) == 1.0 and float(audio_mask.max()) == 0.0
    try:
        RD.H3MaskRedraw(_registry(), "model", None, "vae", "avae", torch.rand(1, 8, 8, 3))
        raise AssertionError("missing clip accepted")
    except ValueError as exc:
        assert "redraw_clip" in str(exc)


def test_build_runs_sam3_first_then_h3(tmp_path):
    order = []

    class Tracker:
        seconds = 0.0

        def track(self, crops):
            order.append(("sam3", crops.shape[0]))
            m = torch.zeros(crops.shape[:3])
            m[:, 16:48, 16:48] = 1
            return m

        def close(self):
            order.append(("close",))

    class Painter:
        seconds = 0.0

        def run(self, crops, mask, audio, k, lock_frames=0):
            order.append(("h3", crops.shape[0], audio["waveform"].shape[-1] > 0))
            return crops * 0 + 0.5

    total = 40
    transform = {"boxes": [[0, 0, 100, 100]] * total, "crop_factor": 2.5}
    masks = RD.UnitStore(str(tmp_path / "m"), total, "a")
    layer = RD.UnitStore(str(tmp_path / "l"), total, "b")
    read = lambda a, b: torch.rand(b - a, 32, 32, 3)   # noqa: E731
    crop = lambda f, i: torch.rand(1, 64, 64, 3)       # noqa: E731
    rep = RD.build(read, crop, transform, [True] * total, Tracker(), Painter(), masks, layer, audio=None,
                   unit=22)
    n = len(RD.unit_spans(total, 22))
    assert [o[0] for o in order] == ["sam3"] * n + ["close"] + ["h3"] * n
    assert rep["redrawn"] == n and rep["frames_without_head"] == 0
    order.clear()
    rep = RD.build(read, crop, transform, [True] * total, Tracker(), Painter(), masks, layer, unit=22)
    assert rep["reused"] == n and [o[0] for o in order] == ["close"]   # all cached: nothing redrawn
    assert abs(float(layer.frame(10).mean()) - 0.5) < 0.01 and float(masks.frame(10).max()) == 1.0


def test_save_node_inputs_and_canvas():
    spec = F.H3MDSaveVideoFaceRefineInterp.INPUT_TYPES()
    opt = list(spec["optional"])
    assert opt[-len(F.REDRAW_INPUTS):] == list(F.REDRAW_INPUTS)
    assert "face_redraw" not in spec["optional"]            # replaces the restore-only refine, no switch
    assert spec["optional"]["redraw_denoise"][1]["default"] == 0.8
    kw, note = F.redraw_canvas({"canvas_mode": "manual", "canvas_width": 512, "canvas_height": 512})
    assert (kw["canvas_width"], kw["canvas_height"]) == (768, 768) and "768x768" in note
    kw, note = F.redraw_canvas({"canvas_mode": "manual", "canvas_width": 1024, "canvas_height": 1024})
    assert note is None and kw["canvas_width"] == 1024
    kw, note = F.redraw_canvas({"canvas_mode": "auto_capped_768", "canvas_width": 768, "canvas_height": 768})
    assert kw["canvas_mode"] == "manual" and note


def test_refiner_pastes_redrawn_frames_through_masks(tmp_path):
    class Stitch:
        calls = []

        def run(self, base_images, refined_crops, transform, **kw):
            Stitch.calls.append((transform["source"], "masks" in kw, float(refined_crops.mean())))
            return (base_images,)

    class Up:
        @classmethod
        def execute(cls, model, image, *a):
            return (image,)

    class VOSR2Model:
        pass

    class FR:
        @staticmethod
        def _affine_crop(frame, box, cw, ch):
            return torch.zeros(1, ch, cw, 3)

    total = 30
    layer = RD.UnitStore(str(tmp_path / "l"), total, "x")
    masks = RD.UnitStore(str(tmp_path / "m"), total, "y")
    layer.put((0, 22), torch.full((22, 64, 64, 3), 0.5))
    masks.put((0, 22), torch.ones((22, 64, 64)))
    transform = {"canvas": [64, 64], "boxes": [[0, 0, 10, 10]] * total, "absent": [False] * total,
                 "weights": [1.0] * total, "detected": [True] * total}
    reg = {"VOSR2Upscale": Up, "H3FaceStitch": Stitch}
    VOSR2Model.__name__ = "VOSR2Model"
    with mock.patch.object(F, "_registry", lambda: reg):
        r = F.FaceRefiner(FR(), transform, VOSR2Model(), None, 1, {"undetected_frames": "fade_out", "blend": 1.0},
                          layer=layer, masks=masks)
        r.refine(torch.zeros(10, 16, 16, 3), 18)
    plain, drawn = Stitch.calls
    assert plain == ([4, 5, 6, 7, 8, 9], False, 0.0)          # frames 22..27: no redraw -> standard paste
    assert drawn[:2] == ([0, 1, 2, 3], True) and abs(drawn[2] - 0.5) < 0.01   # frames 18..21: redrawn, via the mask


def test_lock_frames_zero_the_carried_latents():
    m = torch.ones(22, 64, 64)
    lm = RD.to_latent_mask(m, 7, grow_px=0, grow_frames=0, lock_frames=17)
    assert float(lm[:5].max()) == 0.0 and float(lm[5:].min()) == 1.0   # frames 0..16 = latents 0..4


def test_chained_units_start_from_the_previous_redraw(tmp_path):
    seen = []

    class Tracker:
        seconds = 0.0

        def track(self, crops):
            return torch.ones(crops.shape[:3])

        def close(self):
            pass

    class Painter:
        seconds = 0.0

        def run(self, crops, mask, audio, k, lock_frames=0):
            seen.append((k, lock_frames, float(crops[:max(lock_frames, 1)].mean())))
            return crops * 0 + 0.2 * (k + 1)

    total = 40
    transform = {"boxes": [[0, 0, 100, 100]] * total, "crop_factor": 2.5}
    masks = RD.UnitStore(str(tmp_path / "m"), total, "a")
    layer = RD.UnitStore(str(tmp_path / "l"), total, "b")
    RD.build(lambda a, b: torch.ones(b - a, 8, 8, 3), lambda f, i: torch.ones(1, 16, 16, 3), transform,
             [True] * total, Tracker(), Painter(), masks, layer, unit=22)
    assert seen[0][1] == 0 and all(s[1] >= 17 for s in seen[1:])
    assert all(abs(s[2] - 0.2 * s[0]) < 0.01 for s in seen[1:])        # overlap = the previous unit's redraw
    assert RD.UNIT_FRAMES == 362 and RD.STEPS == 4
