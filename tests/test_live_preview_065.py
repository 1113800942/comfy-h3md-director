"""0.6.5 live preview: display only, never in a cache key, never stops a render."""
import os
import sys

import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import h3md_live_preview as LP  # noqa: E402


def test_pick_spreads_and_keeps_ends():
    assert LP.pick(142) [0] == 0 and LP.pick(142)[-1] == 141 and len(LP.pick(142)) == LP.N_FRAMES
    assert LP.pick(3) == [0, 1, 2]


def test_wanted_follows_mode_and_step():
    LP.configure("low-res", 3)
    assert not LP.wanted("low_res", 2) and LP.wanted("low_res", 3) and not LP.wanted("high_res", 1)
    LP.configure("low-res + high-res", 1)
    assert LP.wanted("low_res", 1) and LP.wanted("high_res", 1)
    LP.configure("off", 1)
    assert not LP.wanted("low_res", 6)
    LP.configure("nonsense", 0)          # unknown mode -> default, step clamped to 1
    assert LP._cfg["mode"] == LP.DEFAULT_MODE and LP._cfg["from_step"] == 1


def test_video_stream_only_takes_h3_video():
    assert LP.video_stream(None) is None
    assert LP.video_stream(torch.zeros(1, 16, 3, 4, 4)) is None
    v = torch.zeros(1, 24, 3, 4, 4)
    assert LP.video_stream(v) is v


def test_show_never_raises_and_turns_itself_off(monkeypatch):
    LP.configure("low-res", 1)

    def boom(*a, **k):
        raise RuntimeError("decoder broke")
    monkeypatch.setattr(LP, "decode_frames", boom)
    LP.show("low_res", 1, 6, torch.zeros(1, 24, 5, 4, 4))   # must not raise
    assert LP._state["failed"] and not LP.wanted("low_res", 6)
    LP.configure("low-res", 1)                               # next run: on again
    assert LP.wanted("low_res", 1)


def test_strip_image_layout():
    portrait = [torch.zeros(64, 32, 3, dtype=torch.uint8)] * 8
    img = LP.strip_image(portrait, "x")
    assert img.size[0] == 8 * 32 and img.size[1] == 64 + 18      # portrait: one row
    landscape = [torch.zeros(32, 64, 3, dtype=torch.uint8)] * 8
    assert LP.strip_image(landscape).size == (4 * 64, 2 * 32)     # landscape: 2 x 4


def test_bundled_decoder_and_license_present():
    assert os.path.isfile(LP.DECODER_FILE)
    lic = os.path.join(os.path.dirname(LP.DECODER_FILE), "LICENSE_TAEHV.txt")
    assert "MIT License" in open(lic, encoding="utf-8").read()


def test_loop_inputs_appended_last_and_popped_before_cache_key():
    import inspect
    import h3md_loop as L
    src = inspect.getsource(L.H3MDLoopSampler.run)
    assert src.index('pop("live_preview"') < src.index("settings = {")   # never reaches the settings hash
    import h3md_loop as L
    opt = L.H3MDLoopSampler.INPUT_TYPES()["optional"]
    keys = list(opt)
    assert keys[-2:] == ["live_preview", "live_preview_from_step"]
    assert opt["live_preview"][1]["default"] == "low-res"
    import h3md_loop_split as LS
    assert "live_preview" in LS.option_defaults() and "live_preview_from_step" in LS.option_defaults()


def test_packed_av_latent_inside_the_sampler():
    """0.6.5 test 01:10: inside the sampler ComfyUI packs video + audio into one flat [B, 1, N] tensor."""
    import comfy.utils

    class Base:
        pass

    class Wrap:
        def __init__(self, inner):
            self.inner_model = inner

    video = torch.randn(1, 24, 7, 4, 3)
    audio = torch.randn(1, 32, 2, 9)
    packed, shapes = comfy.utils.pack_latents([video, audio])
    base = Base()
    base.latent_shapes = shapes
    model_k = Wrap(Wrap(base))                       # KSamplerX0Inpaint -> CFGGuider -> BaseModel
    got = LP.latent_shapes_of(model_k)
    assert got == [tuple(video.shape), tuple(audio.shape)]
    assert torch.equal(LP.video_stream(packed, got), video)
    assert LP.video_stream(packed, None) is None     # no shapes -> not guessed


def test_no_video_is_reported_not_silent(caplog):
    LP.configure("low-res", 1)
    with caplog.at_level("WARNING", logger="H3MD"):
        LP.show("low_res", 1, 6, torch.zeros(1, 1, 50))
    assert LP._state["failed"] and "no H3 video latent" in caplog.text


def test_window_node_needs_no_wires_and_is_not_run():
    n = LP.H3MDLivePreviewWindow
    assert n.INPUT_TYPES() == {"required": {}} and n.RETURN_TYPES == ()
    assert not getattr(n, "OUTPUT_NODE", False)                 # never executed, never blocks a queue
    assert "H3MDLivePreviewWindow" in LP.NODE_CLASS_MAPPINGS
    js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "js", "h3md_live_preview.js")
    src = open(js, encoding="utf-8").read()
    assert LP.EVENT in src and "H3MDLivePreviewWindow" in src


def test_send_without_server_does_not_raise():
    from PIL import Image
    LP._send(1, 6, Image.new("RGB", (8, 8)))
