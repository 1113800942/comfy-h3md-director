"""v0.3: face refine uses the original ComfyUI-VOSR2 'VOSR 2.0 Upscale' node when its model is connected."""
import os
import sys
from unittest import mock

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_face_refine_save as F  # noqa: E402


class VOSR2Model:   # same class name as the original loader's output
    def denoise_one_step(self, *a):
        pass


class FakeUpscale:
    calls = []

    @classmethod
    def execute(cls, model, image, upscale, seed, color, tile, tile_ov, vtile, vtile_ov):
        cls.calls.append((upscale, seed, color, tile, image.shape[0]))
        return (image * 0 + 0.5,)


class FakeStitch:
    pass


def test_original_vosr2_path():
    reg = {"VOSR2Upscale": FakeUpscale, "H3FaceStitch": FakeStitch}
    transform = {"canvas": [768, 768], "absent": [False, False]}
    with mock.patch.object(F, "_registry", lambda: reg), mock.patch.object(F, "paste_weights", lambda *a: [1.0, 1.0]):
        r = F.FaceRefiner(None, transform, VOSR2Model(), None, 7, {})
        assert r.vosr2 is None and r.orig is FakeUpscale
        out = r._upscale(torch.zeros(3, 768, 768, 3))
    assert out.shape == (3, 768, 768, 3) and float(out.mean()) == 0.5
    assert FakeUpscale.calls == [(1, 7, "wavelet", 512, 3)]   # scale 1, own tiling above 512 px
    assert F.is_original_vosr2(VOSR2Model()) and not F.is_original_vosr2(object())


if __name__ == "__main__":
    test_original_vosr2_path()
    print("ok")
