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
    # 0.4.6: high motion = one weapon LoRA per pass, nothing else on (YJ's 1-minute fight, 11-12)
    for which, strength in (("low", 0.85), ("high", 0.5)):
        on = {r["key"]: r["strength"] for r in CP.preset_rows("high_motion", which) if r["on"]}
        assert on == {"weapon": strength}
    off = {r["key"] for r in CP.preset_rows("high_motion", "low") if not r["on"]}
    assert {"combat", "fightflow", "motion_repair", "speed", "cinematic", "vbvr", "enhancer"} <= off
    # the weapon row switched off keeps the tested strength, so turning it on again starts from it
    bal = {r["key"]: r for r in CP.preset_rows("balanced", "low")}
    assert not bal["weapon"]["on"] and bal["weapon"]["strength"] == 0.85
    mc = {r["key"]: r for r in CP.preset_rows("motion_copy", "low") if r["on"]}
    assert set(mc) == {"combat", "enhancer"}
    mch = {r["key"]: r["strength"] for r in CP.preset_rows("motion_copy", "high") if r["on"]}
    assert mch == {"combat": 0.49, "realism": 0.5, "lms": 0.2}


def test_034_no_bypass_rows_turbo_high_and_combat():
    """0.3.4: no bypass rows; Dasiwa / 10ErosMax removed (old saved rows dropped); Combat V2 in every style;
    the high-res 768 turbo strength follows the style (drama 0.5 / high motion 1.0 / motion copy 0.5)."""
    assert CP.BYPASS == set() and "dasiwa" not in CP.FILES and "turbo" not in CP.FILES
    assert CP.TURBO_HIGH == {"balanced": 0.5, "high_motion": 1.0, "motion_copy": 0.5}
    for st in ("balanced", "motion_copy"):   # 0.4.6: high motion has the weapon LoRA only
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


def test_motion_trigger_added_at_queue_time():
    """0.4.6: style = motion copy and no `motion_transfer:` anywhere in the plan -> the outgoing plan_json gets it at the
    start of the global prompt; a plan that has it, or another style, is left alone.  The pure functions of the
    panel script run in node (skipped without node)."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        return
    src = open(os.path.join(os.path.dirname(HERE), "web", "js", "h3md_control_panel.js"), encoding="utf-8").read()
    body = src[src.index("export const MOTION_TRIGGER"):src.index("function setTurboHigh")].replace("export ", "")
    code = ('const NODE = "H3MDControlPanel";\n' + body + """
const plan = (g, p) => JSON.stringify({ globalPrompt: g, segments: [{ prompt: p }] });
const out = (style, text) => ({ "1": { class_type: "H3MDControlPanel", inputs: { style } },
                                "2": { class_type: "H3MDDirectorRefMod", inputs: { plan_json: text } } });
const r = {};
let o = out("motion_copy", plan("r64, r34l1sm", "dance"));
r.n1 = injectMotionTrigger(o); r.g1 = JSON.parse(o["2"].inputs.plan_json).globalPrompt;
o = out("motion_copy", plan("", "dance")); injectMotionTrigger(o); r.g2 = JSON.parse(o["2"].inputs.plan_json).globalPrompt;
o = out("motion_copy", plan("", "motion_transfer:\\ndance")); r.n3 = injectMotionTrigger(o);
o = out("motion_copy", plan("motion_transfer:", "dance")); r.n4 = injectMotionTrigger(o);
o = out("balanced", plan("", "dance")); r.n5 = injectMotionTrigger(o);
o = out("motion_copy", "not json"); r.n6 = injectMotionTrigger(o); r.t6 = o["2"].inputs.plan_json;
console.log(JSON.stringify(r));
""")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(code)
    res = json.loads(subprocess.run([node, f.name], capture_output=True, text=True, check=True).stdout)
    os.unlink(f.name)
    assert res["n1"] == 1 and res["g1"] == "motion_transfer:\nr64, r34l1sm"
    assert res["g2"] == "motion_transfer:"
    assert res["n3"] == 0 and res["n4"] == 0 and res["n5"] == 0       # already there / other style: untouched
    assert res["n6"] == 0 and res["t6"] == "not json"                  # broken plan: left for the Director to report




def test_remake_style_panel_button():
    """总控台 fourth style: internal value remake, visible label exactly 多人互换; default stays balanced.
    Entering remake does not rewrite LoRA presets; swapEngine follows the style; keyframes stay."""
    assert CP.STYLES[-1] == "remake" and "remake" in CP.STYLES
    assert CP.REMAKE_STYLE == "remake"
    assert CP.STYLE_LABELS["remake"] == "多人互换"
    assert list(CP.STYLE_LABELS.values()).count("多人互换") == 1
    types = CP.H3MDControlPanel.INPUT_TYPES()
    assert types["required"]["style"][1]["default"] == "balanced"
    assert types["required"]["style"][0] == CP.STYLES
    assert CP.applies_lora_presets("balanced") and CP.applies_lora_presets("high_motion") and CP.applies_lora_presets("motion_copy")
    assert CP.applies_lora_presets("remake") is False
    # remake preset exists for API/Reset but adds no remake LoRA row
    for which in ("low", "high"):
        keys = [r["key"] for r in CP.preset_rows("remake", which)]
        assert "remake" not in keys
        assert all(r["lora"] != "viggle_remake_swap_comfyui.safetensors" for r in CP.preset_rows("remake", which))
    high_motion_low = CP.apply_style(CP.preset_rows("high_motion", "low"), "high_motion", "low")
    # applying remake presets would change strengths, but the UI must not do that when entering remake
    assert CP.applies_lora_presets("remake") is False
    # plan switch
    plan = {"windowMode": {"length": 243}, "segments": [{"length": 243, "keyframes": [{"image": "kf1", "at": 0}]}]}
    CP.apply_remake_for_style(plan, "remake")
    assert plan["swapEngine"] == "remake" and "windowMode" not in plan
    assert plan["segments"][0]["keyframes"] == [{"image": "kf1", "at": 0}]
    CP.apply_remake_for_style(plan, "balanced")
    assert "swapEngine" not in plan
    assert plan["segments"][0]["keyframes"] == [{"image": "kf1", "at": 0}]
    CP.apply_remake_for_style(plan, "high_motion")
    assert "swapEngine" not in plan
    # node run with remake: passthrough models, no bridge, interpol off -> 24 fps (old behaviour)
    node = CP.H3MDControlPanel()
    m1, m2 = object(), object()
    res = node.run(m1, m2, 1408, 768, False, 60.0, False, 1, "", "remake", "[]", "[]")["result"]
    assert res[0] is m1 and res[1] is m2
    assert res[2:9] == (1408, 768, False, 24.0, False, 1, "")
    assert res[9] is None
    # unknown style still falls back (does not become remake)
    res = node.run(m1, m2, 704, 1280, False, 24.0, False, 1, "", "not_a_style", "[]", "[]")["result"]
    assert res[0] is m1 and res[9] is None


def test_inject_remake_engine_at_queue_time():
    """Panel style remake writes swapEngine onto the outgoing Director plan; other styles strip it
    and keep keyframes; no panel leaves the plan alone.  Pure functions run in node (skipped without node)."""
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node")
    if not node:
        return
    src = open(os.path.join(os.path.dirname(HERE), "web", "js", "h3md_control_panel.js"), encoding="utf-8").read()
    start = src.index("export function applyRemakeForStyle")
    end = src.index("function directorNodes")
    body = src[start:end].replace("export ", "")
    code = ('const NODE = "H3MDControlPanel";\nconst REMAKE_STYLE = "remake";\n' + body + """
const plan = (extra) => JSON.stringify(Object.assign({ segments: [{ length: 243, keyframes: [{ image: "kf1", at: 0 }] }] }, extra || {}));
const out = (style, text) => {
  const o = { "2": { class_type: "H3MDDirectorRefMod", inputs: { plan_json: text } } };
  if (style !== null) o["1"] = { class_type: "H3MDControlPanel", inputs: { style } };
  return o;
};
const r = {};
let o = out("remake", plan({}));
r.n1 = injectRemakeEngine(o); r.p1 = JSON.parse(o["2"].inputs.plan_json);
o = out("remake", plan({ swapEngine: "remake" })); r.n2 = injectRemakeEngine(o);
o = out("balanced", plan({ swapEngine: "remake", windowMode: { length: 243 } }));
r.n3 = injectRemakeEngine(o); r.p3 = JSON.parse(o["2"].inputs.plan_json);
o = out("high_motion", plan({ swapEngine: "remake" }));
r.n4 = injectRemakeEngine(o); r.p4 = JSON.parse(o["2"].inputs.plan_json);
o = out("motion_copy", plan({ swapEngine: "remake" }));
r.n5 = injectRemakeEngine(o); r.p5 = JSON.parse(o["2"].inputs.plan_json);
o = out(null, plan({ swapEngine: "remake" })); r.n6 = injectRemakeEngine(o); r.p6 = JSON.parse(o["2"].inputs.plan_json);
o = out("remake", plan({ windowMode: { length: 243 } })); r.n7 = injectRemakeEngine(o); r.p7 = JSON.parse(o["2"].inputs.plan_json);
o = out("remake", "not json"); r.n8 = injectRemakeEngine(o); r.t8 = o["2"].inputs.plan_json;
o = out("balanced", plan({})); r.n9 = injectRemakeEngine(o);
console.log(JSON.stringify(r));
""")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False, encoding="utf-8") as f:
        f.write(code)
    res = json.loads(subprocess.run([node, f.name], capture_output=True, text=True, check=True).stdout)
    os.unlink(f.name)
    assert res["n1"] == 1 and res["p1"]["swapEngine"] == "remake"
    assert res["p1"]["segments"][0]["keyframes"] == [{"image": "kf1", "at": 0}]
    assert res["n2"] == 0   # already remake, no windowMode: unchanged
    assert res["n3"] == 1 and "swapEngine" not in res["p3"]
    assert res["p3"]["segments"][0]["keyframes"] == [{"image": "kf1", "at": 0}]
    assert "windowMode" in res["p3"]   # leaving remake does not invent windowMode deletion beyond swapEngine
    # wait: applyRemakeForStyle only deletes swapEngine when leaving; windowMode in the incoming plan stays if we
    # started with both. p3 had both swapEngine and windowMode; leaving remake only pops swapEngine, windowMode stays.
    assert res["n4"] == 1 and "swapEngine" not in res["p4"]
    assert res["n5"] == 1 and "swapEngine" not in res["p5"]
    assert res["n6"] == 0 and res["p6"]["swapEngine"] == "remake"   # no panel: plan left alone
    assert res["n7"] == 1 and res["p7"]["swapEngine"] == "remake" and "windowMode" not in res["p7"]
    assert res["n8"] == 0 and res["t8"] == "not json"
    assert res["n9"] == 0   # balanced + no swapEngine: untouched


def test_panel_js_button_label_is_exactly_duorenhuhuan():
    js = open(os.path.join(os.path.dirname(HERE), "web", "js", "h3md_control_panel.js"), encoding="utf-8").read()
    assert 'remake: "多人互换"' in js
    assert "STYLE_ORDER.map" in js and "STYLE_LABEL[s]" in js
    # the visible control-panel button must be these four characters, not 「Remake 换人」
    assert "Remake 换人" not in js.split("const STYLE_LABEL")[1].split("const STYLE_TIP")[0]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
