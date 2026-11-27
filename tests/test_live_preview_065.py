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
