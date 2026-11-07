"""window-exp core check against a real ComfyUI checkout (not part of the light test suite).
Usage: python tests/window_core_check.py <ComfyUI dir>"""
import sys
root = sys.argv[1]
sys.argv = [sys.argv[0], "--cpu"]
sys.path.insert(0, root)
import comfy.options; comfy.options.enable_args_parsing()  # noqa: E401,E702
import torch  # noqa: E402
import comfy.context_windows as cw  # noqa: E402
import comfy.conds  # noqa: E402
import comfy.ldm.minimax.model as mm  # noqa: E402
from comfy.model_base import MiniMaxH3  # noqa: E402
sys.path.insert(0, __file__.rsplit("tests", 1)[0])
import h3md_window as WIN  # noqa: E402

total, A = 87, 345
vw = cw.IndexListContextWindow(list(range(25, 62)), dim=2, total_frames=total)
aw = cw.IndexListContextWindow(list(range(100, 250)), dim=2, total_frames=A)
vw.modality_windows = {1: aw}


class FakeModel:
    _window_payload = MiniMaxH3._window_payload
    _latent_pixel_starts = staticmethod(MiniMaxH3._latent_pixel_starts)
    _cond_latents = staticmethod(MiniMaxH3._cond_latents)


refs = [{"kind": "image", "latent_h": 8, "latent_w": 8, "latent": torch.zeros(1, 4, 1, 8, 8)},
        {"kind": "video", "latent_t": total, "ref_audio_t": 0, "latent_h": 6, "latent_w": 6,
         "latent": torch.arange(total).float().view(1, 1, total, 1, 1).expand(1, 4, total, 6, 6).clone()}]
layout = mm.PackedLayout(20, total, 4, 4, A, refs=refs)
payload = {"refs": refs, "layout": layout, "keyframes": [{"resolved_frame_index": 0, "audio_latent": torch.zeros(1, 8, A)}]}

h = cw.IndexListContextHandler(cw.get_matching_context_schedule(cw.ContextSchedules.STATIC_STANDARD),
                               cw.get_matching_fuse_method(cw.ContextFuseMethods.PYRAMID), 37, 12, dim=2)
h._model = FakeModel()
WIN.install(h, True)
x = torch.zeros(1, 4, total, 6, 6)
a, b = WIN.window_pixel_span(vw)
conds = [{"model_conds": {"minimax_payload": comfy.conds.CONDConstant(payload)}, WIN.SPAN_KEY: (0, 120), "tag": "A"},
         {"model_conds": {"minimax_payload": comfy.conds.CONDConstant(payload)}, WIN.SPAN_KEY: (120, 345), "tag": "B"}]
res = h.get_resized_cond(conds, x, vw, device="cpu")
pl = res[0]["model_conds"]["minimax_payload"].cond
assert len(res) == 1 and res[0]["tag"] == "B", (a, b, res[0]["tag"])
assert pl["refs"][1]["latent"].shape[2] == 37 and pl["refs"][1]["latent"][0, 0, 0, 0, 0].item() == 25
assert pl["layout"].signature == (20, 37, 4, 4, 150), pl["layout"].signature
assert refs[1]["latent"].shape[2] == total
w0 = cw.IndexListContextWindow(list(range(0, 37)), dim=2, total_frames=total)
w0.modality_windows = {1: cw.IndexListContextWindow(list(range(0, 150)), dim=2, total_frames=A)}
assert h.get_resized_cond(conds, x, w0, device="cpu")[0]["tag"] == "A"
one = h.get_resized_cond([conds[0]], x, vw, device="cpu")
assert one[0]["tag"] == "A"
print("window core check OK: window span", (a, b), "-> prompt B; guide cropped to 37; layout rebuilt; window 0 -> A")
