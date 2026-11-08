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
# overlap monitor: two windows that agree -> 0, disagree -> > 0
mon = WIN.make_overlap_monitor(h)
wa = cw.IndexListContextWindow(list(range(0, 10)), dim=2, total_frames=20); wa.modality_windows = {1: None}
wb = cw.IndexListContextWindow(list(range(6, 20)), dim=2, total_frames=20); wb.modality_windows = {1: None}
t = torch.tensor([0.5])
mon(h, None, [torch.ones(1, 2, 10, 2, 2)], None, wa, 0, 2, t)
mon(h, None, [torch.cat([torch.ones(1, 2, 4, 2, 2), torch.zeros(1, 2, 10, 2, 2)], 2)], None, wb, 1, 2, t)
sig, vals = h._h3md_overlap[-1]
assert abs(sig - 0.5) < 1e-6 and vals[0][0] == (0, 1) and abs(vals[0][1] - 0.0) < 1e-6, vals
mon(h, None, [torch.ones(1, 2, 10, 2, 2)], None, wa, 0, 2, t)
mon(h, None, [-torch.ones(1, 2, 14, 2, 2)], None, wb, 1, 2, t)
assert h._h3md_overlap[-1][1][0][1] > 1.5, h._h3md_overlap[-1]
print("overlap monitor OK", h._h3md_overlap[-2:])

# 3a: our pure-python window schedule == core (frames -> latents -> static_standard -> pixel span)
from types import SimpleNamespace  # noqa: E402
for total_f, ln, ov in ((1445, 243, 56), (721, 243, 56), (685, 124, 56), (1445, 175, 22)):
    T = WIN.video_latent_t(total_f)
    cl = WIN.video_latent_t(ln)
    h = SimpleNamespace(context_length=cl, context_overlap=min(WIN.video_latent_t(ov), cl - 5))
    core = [(w[0], w[-1]) for w in cw.create_windows_static_standard(T, h, {})]
    assert core == WIN.core_windows(total_f, ln, ov), (total_f, ln, ov)
    assert WIN.latent_pixel_starts(T) == list(MiniMaxH3._latent_pixel_starts(T))
    wins, spans = WIN.window_spans(total_f, ln, ov)
    for (a, b), (lo, hi) in zip(wins, spans):
        c = (a + b) // 2
        assert lo <= c < hi or c == hi == total_f
print("3a window schedule matches core")

# 3b: the installed handler schedules around hard cuts (real IndexListContextHandler)
h = cw.IndexListContextHandler(cw.get_matching_context_schedule(cw.ContextSchedules.STATIC_STANDARD),
                               cw.get_matching_fuse_method("pyramid"), context_length=37, context_overlap=17, dim=2)
WIN.install(h, False)
x = torch.zeros(1, 1, WIN.video_latent_t(345), 1, 1)
core_w = [(w.index_list[0], w.index_list[-1]) for w in h.get_context_windows(None, x, {})]
assert core_w == WIN.core_windows(345, 124, 56), core_w
h._h3md_cuts = [170]
cut_w = [(w.index_list[0], w.index_list[-1]) for w in h.get_context_windows(None, x, {})]
assert cut_w == WIN.core_windows(345, 124, 56, [170]), cut_w
print("3b handler windows OK", core_w, cut_w)

# 11-08 review: a clip no longer than one window but with per-window conditionings is still windowed
h = cw.IndexListContextHandler(cw.get_matching_context_schedule(cw.ContextSchedules.STATIC_STANDARD),
                               cw.get_matching_fuse_method("pyramid"), context_length=72, context_overlap=17, dim=2)
WIN.install(h, False)
h._build_window_state = lambda x_in, conds, model: SimpleNamespace(latents=[x_in])
x = torch.zeros(1, 1, WIN.video_latent_t(141), 1, 1)
two = [[{WIN.RANGE_KEY: (0, 68), WIN.CUT_KEY: (68,)}, {WIN.RANGE_KEY: (68, 141), WIN.CUT_KEY: (68,)}]]
assert h.should_use_context(None, two, x, None, {}), "short clip with two window conditionings must be windowed"
assert not h.should_use_context(None, [[{"plain": 1}]], x, None, {}), "a plain short clip stays unwindowed"
h._h3md_cuts = [68]
short_w = [(w.index_list[0], w.index_list[-1]) for w in h.get_context_windows(None, x, {})]
assert short_w == WIN.core_windows(141, 243, 56, [68]), short_w
print("short clip windows OK", short_w)
