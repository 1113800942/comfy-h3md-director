"""v0.3 H3MD Control Panel: presets, style buttons keep user rows, LoRA list parsing, missing files, outputs."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import h3md_control_panel as CP  # noqa: E402


def test_presets_match_measured_mix():
    lo = {r["key"]: r for r in CP.preset_rows("balanced", "low")}
    hi = {r["key"]: r for r in CP.preset_rows("balanced", "high")}
    assert lo["enhancer"]["on"] and lo["enhancer"]["strength"] == 0.55
    assert hi["realism"]["strength"] == 0.6 and hi["cinematic"]["on"] and hi["dasiwa"]["strength"] == 0.5
    assert not lo["fightflow"]["on"] and not lo["weapon"]["on"]
    hm = {r["key"]: r for r in CP.preset_rows("high_motion", "high")}
    assert hm["dasiwa"]["strength"] == 1.0 and hm["fightflow"]["on"]          # old 武戏 switch
    mc = {r["key"]: r for r in CP.preset_rows("motion_copy", "low") if r["on"]}
    assert set(mc) == {"dasiwa", "turbo", "enhancer"}


def test_style_button_keeps_user_rows():
    rows = CP.preset_rows("balanced", "low") + [{"key": None, "lora": "my_anime.safetensors", "on": True,
                                                  "strength": 0.8}]
    out = CP.apply_style(rows, "motion_copy", "low")
    mine = [r for r in out if r["lora"] == "my_anime.safetensors"][0]
    assert mine["on"] and mine["strength"] == 0.8
    assert not [r for r in out if r.get("key") == "cinematic"][0]["on"]


def test_parse_rows():
    assert CP.parse_rows("") == []
    rows = CP.parse_rows(json.dumps([{"lora": "a.safetensors", "strength": "0.3"}, {"lora": ""}, 5]))
    assert rows == [{"key": None, "lora": "a.safetensors", "on": True, "strength": 0.3}]
    try:
        CP.parse_rows("{bad")
        raise AssertionError("bad json accepted")
    except ValueError:
        pass


def test_missing_lora_listed_and_off_rows_skipped():
    model = object()
    out, used = CP.apply_rows(model, [{"key": None, "lora": "nope.safetensors", "on": False, "strength": 1}],
                              CP._LoraCache(), "low")
    assert out is model and used == []
    try:
        CP.apply_rows(model, [{"key": None, "lora": "nope.safetensors", "on": True, "strength": 1}],
                      CP._LoraCache(), "low")
        raise AssertionError("missing file accepted")
    except ValueError as exc:
        assert "nope.safetensors" in str(exc)


def test_outputs_and_fps_lock():
    node = CP.H3MDControlPanel()
    m1, m2 = object(), object()
    res = node.run(m1, m2, 704, 1280, False, 60.0, True, 7, " 2 ", "motion_copy", "[]", "[]")["result"]
    assert res[0] is m1 and res[1] is m2
    assert res[2:9] == (704, 1280, False, 24.0, True, 7, "2")     # interpolate off: fps 24
    assert res[9] is None                                         # motion copy: no semantic bridge
    res = node.run(m1, m2, 704, 1280, True, 60.0, False, 7, "", "balanced", "[]", "[]")["result"]
    assert res[5] == 60.0


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
