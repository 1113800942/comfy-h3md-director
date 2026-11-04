"""v0.3.3: face refine also uses the plain ComfyUI-SeedVR2_VideoUpscaler (numz) nodes."""
import os
import sys
from unittest import mock

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_face_refine_save as F  # noqa: E402


class FakeNumz:
    calls = []

    @classmethod
    def execute(cls, image, dit, vae, seed, resolution=1080, max_resolution=0, batch_size=5, **kw):
        cls.calls.append((resolution, max_resolution, seed, image.shape[0], kw.get("offload_device")))
        return (image * 0 + 0.25,)


class FakeStitch:
    pass


def test_plain_seedvr2_path():
    reg = {"SeedVR2VideoUpscaler": FakeNumz, "H3FaceStitch": FakeStitch}
    transform = {"canvas": [768, 512], "absent": [False, False]}
    with mock.patch.object(F, "_registry", lambda: reg), mock.patch.object(F, "paste_weights", lambda *a: [1.0, 1.0]):
        r = F.FaceRefiner(None, transform, None, None, 9, {}, {"model": "seedvr2_3b.safetensors"}, {"model": "vae"})
        assert r.numz is FakeNumz and r.vosr2 is None
        out = r._upscale(torch.zeros(4, 512, 768, 3))
    assert out.shape == (4, 512, 768, 3) and float(out.mean()) == 0.25
    assert FakeNumz.calls[-1] == (512, 0, 9, 4, "cpu")   # shortest edge = crop -> scale 1


def test_detection():
    assert F.is_numz_seedvr2({"model": "x"}) and not F.is_numz_seedvr2(object())
    spec = F.H3MDSaveVideoFaceRefineInterp.INPUT_TYPES()["optional"]
    assert spec["seedvr2_model"][0] == "MODEL,SEEDVR2_DIT" and spec["seedvr2_vae"][0] == "VAE,SEEDVR2_VAE"


if __name__ == "__main__":
    test_plain_seedvr2_path()
    test_detection()
    print("ok")
