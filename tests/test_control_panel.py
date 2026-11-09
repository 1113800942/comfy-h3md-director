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
    assert hi["realism"]["strength"] == 0.6 and hi["cinematic"]["on"]
    assert not lo["fightflow"]["on"] and not lo["weapon"]["on"]
    hm = {r["key"]: r for r in CP.preset_rows("high_motion", "high")}
    assert hm["fightflow"]["on"]
    mc = {r["key"]: r for r in CP.preset_rows("motion_copy", "low") if r["on"]}
    assert set(mc) == {"combat", "enhancer"}
    mch = {r["key"]: r["strength"] for r in CP.preset_rows("motion_copy", "high") if r["on"]}
    assert mch == {"combat": 0.49, "realism": 0.5, "lms": 0.2}


def test_034_no_bypass_rows_turbo_high_and_combat():
    """0.3.4: no bypass rows; Dasiwa / 10ErosMax removed (old saved rows dropped); Combat V2 in every style;
    the high-res 768 turbo strength follows the style (drama 0.5 / high motion 1.0 / motion copy 0.5)."""
    assert CP.BYPASS == set() and "dasiwa" not in CP.FILES and "turbo" not in CP.FILES
    assert CP.TURBO_HIGH == {"balanced": 0.5, "high_motion": 1.0, "motion_copy": 0.5}
    for st in CP.STYLES:
        lo = {r["key"]: r for r in CP.preset_rows(st, "low")}
        hi = {r["key"]: r for r in CP.preset_rows(st, "high")}
        assert lo["combat"]["on"] and lo["combat"]["strength"] == 0.7
        assert hi["combat"]["on"] and hi["combat"]["strength"] == 0.49
    old = [{"key": "dasiwa", "lora": "d.safetensors", "on": True, "strength": 0.9},
           {"key": "turbo", "lora": "t.safetensors", "on": True, "strength": 0.5},
           {"key": None, "lora": "mine.safetensors", "on": True, "strength": 0.3}]
    assert [r["lora"] for r in CP.parse_rows(old)] == ["mine.safetensors"]
    out = CP.apply_style(old, "balanced", "low")
    assert not [r for r in out if r.get("key") in ("dasiwa", "turbo")]
    assert [r for r in out if r["lora"] == "mine.safetensors"]


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


def test_styles_switch_no_semantic_bridge():
    """0.4.0: no style turns a semantic bridge on (the output stays, always off)."""
    node = CP.H3MDControlPanel()
    for style in CP.STYLES:
        assert CP.BRIDGES[style] is None and CP.bridge_for(style) is None
        res = node.run(object(), object(), 704, 1280, False, 24.0, False, 1, "", style, "[]", "[]")["result"]
        assert res[9] is None, style


def test_simple_workflow_bridge_wired_and_bypassed():
    """The simple example workflow: a bypassed H3MD Semantic Bridge feeds the sampler's semantic_bridge input; the
    Control Panel's semantic_bridge output is not connected."""
    path = os.path.join(os.path.dirname(HERE), "example_workflows", "H3MD Director simple.json")
    wf = json.load(open(path, encoding="utf-8"))
    nodes = {n["id"]: n for n in wf["nodes"]}
    bridges = [n for n in wf["nodes"] if n["type"] == "H3MDSemanticBridge"]
    assert len(bridges) == 1 and bridges[0]["mode"] == 4                  # 4 = bypassed
    out = bridges[0]["outputs"][0]["links"]
    assert len(out) == 1
    link = [l for l in wf["links"] if l[0] == out[0]][0]
    target = nodes[link[3]]
    assert target["inputs"][link[4]]["name"] == "semantic_bridge"
    panel = [n for n in wf["nodes"] if n["type"] == "H3MDControlPanel"][0]
    assert not [o for o in panel["outputs"] if o["name"] == "semantic_bridge"][0]["links"]



def test_lora_dropdowns_filled_on_open():
    """0.4.3: each LoRA dropdown starts with only its own row's LoRA; the full models/loras list (hundreds of files x
    16-18 rows = thousands of <option>s, ~76 ms per panel re-render) is filled in when that dropdown is opened."""
    js = open(os.path.join(os.path.dirname(HERE), "web", "js", "h3md_control_panel.js"), encoding="utf-8").read()
    table = js[js.index("  table(which) {"):js.index("  hintEl(h) {")]
    assert "for (const n of names) sel.append" not in table           # no longer every LoRA up front
    assert "sel.append(loraOption(r.lora));" in table                   # only the current one
    fill = table[table.index("const fill = () => {"):]
    assert "if (sel.dataset.full) return;" in fill                       # filled once per dropdown
    assert "P.loras" in fill and "sel.value = r.lora;" in fill          # full list, current value kept
    assert 'sel.addEventListener("pointerdown", fill);' in table and 'sel.addEventListener("focus", fill);' in table
    assert "onchange: (e) => upd({ lora: e.target.value, key: null })" in table   # choosing still updates the row

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
