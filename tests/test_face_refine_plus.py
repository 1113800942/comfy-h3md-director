"""0.4.7: face refine save can paste with H3 Face Stitch Plus and run H3 Face Flow Lock (ComfyUI-H3-FaceRefine-Plus)."""
import os
import sys
from unittest import mock

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_face_refine_save as F  # noqa: E402

STITCH_KW = {"paste_region": "face_only", "mask_dilation": 16, "feather": 6, "colour_match": 1.0, "blend": 0.9,
             "undetected_frames": "fade_out", "feather_scales_with_crop": False}


class FakeStitch:
    calls = []

    def run(self, **kw):
        FakeStitch.calls.append(kw)
        return (kw["base_images"] + 0.25,)


class FakePlus:
    calls = []

    def run(self, **kw):
        FakePlus.calls.append(kw)
        return (kw["base_images"] + 0.5,)


class FakeFlow:
    calls = []

    def run(self, **kw):
        FakeFlow.calls.append(kw)
        return (kw["refined_images"] + 0.125, "report")


class VOSR2Model:   # looks like the original VOSR2 loader's output, so the refiner needs no TE-Speed node
    def denoise_one_step(self, *a):
        pass


class FakeUpscale:
    pass


class FakeFR:
    @staticmethod
    def _affine_crop(img, box, cw, ch):
        return torch.zeros(1, ch, cw, 3)


def _refiner(reg, **kw):
    reg = dict(reg, VOSR2Upscale=FakeUpscale)
    transform = {"canvas": [8, 8], "absent": [False] * 3, "boxes": [[0, 0, 4, 4]] * 3}
    with mock.patch.object(F, "_registry", lambda: reg), mock.patch.object(F, "paste_weights", lambda *a: [1.0] * 3), \
            mock.patch.object(F, "slice_transform", lambda t, idx, start, n: {"idx": list(idx)}):
        r = F.FaceRefiner(FakeFR, transform, VOSR2Model(), None, 1, STITCH_KW, **kw)
        r._upscale = lambda crops: crops + 1.0
        out = r.refine(torch.zeros(3, 16, 16, 3), 0)
    return r, out


def test_plus_kw_mapping():
    kw = F.plus_stitch_kw(STITCH_KW)
    assert kw == {"paste_region": "face_only", "mask_dilation": 16, "feather": 6, "blend": 0.9,
                  "undetected_frames": "fade_out", "tone_mode": "freq_sep", "lowpass_face_frac": 0.15,
                  "detail_gain": 1.0}
    assert F.plus_stitch_kw(dict(STITCH_KW, colour_match=0.0))["tone_mode"] == "none"


def test_default_path_unchanged():
    FakeStitch.calls.clear()
    r, out = _refiner({"H3FaceStitch": FakeStitch, "H3FaceStitchPlus": FakePlus, "H3FaceFlowLock": FakeFlow})
    assert r.plus is False and r.flow is None
    assert len(FakeStitch.calls) == 1 and "colour_match" in FakeStitch.calls[0]
    assert float(out.mean()) == 0.25 and r.t_flow == 0.0


def test_plus_and_flow_lock_run_after_the_paste():
    FakePlus.calls.clear()
    FakeFlow.calls.clear()
    FakeStitch.calls.clear()
    r, out = _refiner({"H3FaceStitch": FakeStitch, "H3FaceStitchPlus": FakePlus, "H3FaceFlowLock": FakeFlow},
                      plus=True, flow_lock=True)
    assert not FakeStitch.calls and len(FakePlus.calls) == 1
    assert "colour_match" not in FakePlus.calls[0] and FakePlus.calls[0]["tone_mode"] == "freq_sep"
    f = FakeFlow.calls[0]
    assert float(f["base_images"].mean()) == 0.0 and float(f["refined_images"].mean()) == 0.5   # base, stitch result
    assert f["base_offset"] == 0 and abs(f["sigma_frac"] - 1 / 150) < 1e-9 and f["threshold"] == 4.0
    assert float(out.mean()) == 0.625                                                          # flow lock's output


def test_missing_pack_falls_back_with_notes():
    with mock.patch.object(F, "_registry", lambda: {"H3FaceStitch": FakeStitch}):
        assert F.resolve_plus("standard", False) == (False, False, [])
        use_plus, use_flow, notes = F.resolve_plus("plus", True)
    assert (use_plus, use_flow) == (False, False) and len(notes) == 2
    with mock.patch.object(F, "_registry", lambda: {"H3FaceStitchPlus": FakePlus, "H3FaceFlowLock": FakeFlow}):
        assert F.resolve_plus("plus", False) == (True, False, [])
        try:
            F.resolve_plus("fancy", False)
            raise AssertionError("bad stitch_mode accepted")
        except ValueError:
            pass


def test_new_inputs_are_optional_and_last():
    opt = list(F.H3MDSaveVideoFaceRefineInterp.INPUT_TYPES()["optional"])
    assert opt[-2:] == ["stitch_mode", "flow_lock"]


def test_save_accepts_new_inputs_and_hands_them_over():
    node = F.H3MDSaveVideoFaceRefineInterp()
    seen = {}

    def fake(self, *a):
        seen["args"] = a
        return {"ui": {}, "result": ("p", 1, None, "r", None)}

    with mock.patch.object(F.H3MDSaveVideoFaceRefineInterp, "_save_refined", fake):
        node.save(segments={"cache_dir": "x"}, stitch_mode="plus", flow_lock=True)
        assert seen["args"][-2:] == ("plus", True)
        node.save(segments={"cache_dir": "x"})
        assert seen["args"][-2:] == ("standard", False)   # an old workflow


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
