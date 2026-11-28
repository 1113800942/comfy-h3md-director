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
    assert hi["realism"]["strength"] == 0.6 and hi["lms"]["on"]
    # 0.6.6: the paid speed slider and the cinematic LoRA are no longer recommended rows
    assert "speed" not in CP.FILES and "cinematic" not in CP.FILES and "speed" not in lo and "cinematic" not in hi
    assert not lo["fightflow"]["on"] and not lo["weapon"]["on"]
    # 0.4.6: high motion = one weapon LoRA per pass, nothing else on (YJ's 1-minute fight, 11-12)
    for which, strength in (("low", 0.85), ("high", 0.5)):
        on = {r["key"]: r["strength"] for r in CP.preset_rows("high_motion", which) if r["on"]}
        assert on == {"weapon": strength}
    off = {r["key"] for r in CP.preset_rows("high_motion", "low") if not r["on"]}
    assert {"combat", "fightflow", "motion_repair", "vbvr", "enhancer"} <= off
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
    assert not [r for r in out if r.get("key") == "vbvr"][0]["on"]


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
    # 0.6.6: the LoRAs are recommendations - a switched-on row whose file is missing is skipped, the run goes on
    skipped = []
    out, used = CP.apply_rows(model, [{"key": None, "lora": "nope.safetensors", "on": True, "strength": 1},
                                      {"key": "vbvr", "lora": CP.FILES["vbvr"], "on": True, "strength": 1}],
                              CP._LoraCache(), "low", skipped)
    assert out is model and used == [] and skipped == ["nope.safetensors", CP.FILES["vbvr"]]


def test_066_official_names_and_renamed_copy_found_by_sha256(tmp_path, monkeypatch):
    import hashlib
    # official file names only: no strengths / trigger words / Chinese in a recommended file name
    for key, spec in CP.RECOMMENDED.items():
        assert spec["file"].endswith(".safetensors") and " " not in spec["file"] and spec["file"].isascii()
        assert len(spec["sha256"]) == 64 and spec["bytes"] > 0 and spec["url"].startswith("https://")
    data = b"renamed lora bytes"
    (tmp_path / "my renamed 0.6-0.8.safetensors").write_bytes(data)
    (tmp_path / "same size other.safetensors").write_bytes(b"x" * len(data))
    files = {p.name: str(p) for p in tmp_path.iterdir()}
    monkeypatch.setattr(CP.folder_paths, "get_full_path", lambda kind, name: files.get(name))
    monkeypatch.setattr(CP.folder_paths, "get_filename_list", lambda kind: sorted(files))
    monkeypatch.setitem(CP.RECOMMENDED, "vbvr", dict(CP.RECOMMENDED["vbvr"], bytes=len(data),
                                                     sha256=hashlib.sha256(data).hexdigest()))
    official = CP.FILES["vbvr"]
    assert CP.find_lora(official) == "my renamed 0.6-0.8.safetensors"      # same bytes under another name
    assert CP.find_lora(CP.FILES["combat"]) is None                        # not there at all
    assert CP.find_lora("someone elses.safetensors") is None               # not a recommended file: name only
    # a recommended row saved under an older renamed name is looked up by its key's official file
    assert CP.find_row({"key": "vbvr", "lora": "H3-Licon-VBVR-I2V-1pass1.0-2pass0.3.safetensors"}) \
        == "my renamed 0.6-0.8.safetensors"
    files[official] = str(tmp_path / "same size other.safetensors")        # the official name wins when present
    assert CP.find_lora(official) == official


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


def _example(name):
    path = os.path.join(os.path.dirname(HERE), "example_workflows", name)
    wf = json.load(open(path, encoding="utf-8"))
    subs = (wf.get("definitions") or {}).get("subgraphs") or []
    return wf, subs


def test_066_simple_and_remake_workflows_need_no_other_node_pack_to_open():
    """0.6.6: a new user opens the default workflows without red nodes - no rgthree / YCNodes / KJNodes / VOSR2 node
    is enabled in them; the bypassed Redraw and Semantic Bridge helper nodes are gone (the Loop keeps its input)."""
    gone = {"Label (rgthree)", "H3SigmaRefiner", "MiniMaxChunkFeedForward", "VOSR2ModelLoader",
            "MiniMaxH3MemoryEfficientSageAttentionPatch", "H3MDRedraw", "H3MDRedrawPreview", "H3MDSemanticBridge"}
    core_or_ours = ("H3MD", "MarkdownNote", "Note", "UNETLoader", "VAELoader", "CLIPLoader", "KSamplerSelect",
                    "BasicScheduler", "LoraLoaderModelOnly")
    for name in ("H3MD Director simple.json", "H3MD Director Remake.json"):
        wf, subs = _example(name)
        sub_ids = {s["id"] for s in subs}
        for g in [wf] + subs:
            for n in g["nodes"]:
                assert n["type"] not in gone, (name, n["type"])
                if n.get("mode", 0) == 0 and n["type"] not in sub_ids:        # every ENABLED node is ours or core
                    assert n["type"].startswith(core_or_ours), (name, n["type"])
        types = [n["type"] for g in [wf] + subs for n in g["nodes"]]
        assert types.count("H3MDSageAttention") == 2 and types.count("H3MDChunkFeedForward") == 2, name
        assert types.count("H3MDSigmaRefiner") == 1, name
        loop = [n for s in subs for n in s["nodes"] if n["type"].startswith("H3MDLoopSampler")][0]
        assert [i for i in loop["inputs"] if i["name"] == "semantic_bridge"]      # the input stays for users' own bridge
        roles = {(n.get("properties") or {}).get("h3md_role") for s in subs for n in s["nodes"]}
        assert {"sage_low", "sage_high", "turbo768_low", "turbo768_high"} <= roles, name   # the panel's switches find them


def test_066_redraw_runs_from_the_control_panel_to_the_loop():
    for name in ("H3MD Director simple.json", "H3MD Director Remake.json"):
        wf, subs = _example(name)
        nodes = {n["id"]: n for n in wf["nodes"]}
        panel = [n for n in wf["nodes"] if n["type"] == "H3MDControlPanel"][0]
        out = [o for o in panel["outputs"] if o["name"] == "redraw"]
        assert len(out) == 1 and panel["outputs"][-1]["name"] == "redraw" and len(out[0]["links"]) == 1
        link = [l for l in wf["links"] if l[0] == out[0]["links"][0]][0]
        inst = nodes[link[3]]
        assert inst["inputs"][link[4]]["name"] == "redraw" and inst["inputs"][link[4]]["link"] == link[0]
        sg = [s for s in subs if s["id"] == inst["type"]][0]
        sg_in = [i for i in sg["inputs"] if i["name"] == "redraw"][0]
        inner = [l for l in sg["links"] if l["id"] == sg_in["linkIds"][0]][0]
        assert inner["origin_id"] == -10 and inner["origin_slot"] == sg["inputs"].index(sg_in)
        loop = [n for n in sg["nodes"] if n["id"] == inner["target_id"]][0]
        assert loop["type"].startswith("H3MDLoopSampler") and loop["inputs"][inner["target_slot"]]["name"] == "redraw"
        # the plan goes from the Director straight to the sampler (no Redraw node in between any more)
        plan_in = [i for i in inst["inputs"] if i["name"] == "plan"][0]
        src = [l for l in wf["links"] if l[0] == plan_in["link"]][0]
        assert nodes[src[1]]["type"].startswith("H3MDDirector") and src[5] == "H3MD_PLAN"
        assert not [o for o in panel["outputs"] if o["name"] == "semantic_bridge"][0]["links"]


def test_066_workflow_lora_lists_use_official_names():
    for name in ("H3MD Director simple.json", "H3MD Director Remake.json"):
        wf, _subs = _example(name)
        panel = [n for n in wf["nodes"] if n["type"] == "H3MDControlPanel"][0]
        vals = panel["widgets_values"]
        assert vals[-1] == ""                                                    # redraw: empty
        for text in (vals[-3], vals[-2]):
            rows = json.loads(text)
            assert rows and not [r for r in rows if r.get("key") in ("speed", "cinematic")]
            for r in rows:
                if r.get("key") in CP.FILES:
                    assert r["lora"] == CP.FILES[r["key"]], (name, r)
        note = [n for n in wf["nodes"] if n["type"] == "MarkdownNote" and "Model links" in (n.get("title") or "")][0]
        text = note["widgets_values"][0]
        for f in CP.FILES.values():
            assert f in text, (name, f)
        assert "speed_slider" not in text and "CSWRY/VOSR" in text and "sageattention" in text


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
    assert CP.applies_lora_presets("remake")       # 0.5.91 (user 23:57): multi-person swap = LMS only
    assert [r["key"] for r in CP.preset_rows("remake", "low") if r["on"]] == []
    assert [(r["key"], r["strength"]) for r in CP.preset_rows("remake", "high") if r["on"]] == [("lms", 0.2)]
    # remake preset adds no remake LoRA row
    for which in ("low", "high"):
        keys = [r["key"] for r in CP.preset_rows("remake", which)]
        assert "remake" not in keys
        assert all(r["lora"] != "viggle_remake_swap_comfyui.safetensors" for r in CP.preset_rows("remake", which))
    high_motion_low = CP.apply_style(CP.preset_rows("high_motion", "low"), "high_motion", "low")
    after = CP.apply_style(high_motion_low, "remake", "low")       # from high motion: weapon switched off
    assert not any(r["on"] for r in after)
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


def test_066_redraw_row_output_and_loop_input():
    """0.6.6: redraw by seconds is a Control Panel row.  Declared last + optional (old workflows / API prompts load),
    handed on as the LAST output; wrong writing stops the run at the panel; the Loop takes it as its last input."""
    spec = CP.H3MDControlPanel.INPUT_TYPES()
    assert list(spec["optional"]) == ["redraw"] and "redraw" not in spec["required"]
    assert CP.H3MDControlPanel.RETURN_NAMES[-1] == "redraw" and CP.H3MDControlPanel.RETURN_NAMES[9] == "semantic_bridge"
    node = CP.H3MDControlPanel()
    m1, m2 = object(), object()
    res = node.run(m1, m2, 704, 1280, False, 24.0, False, 7, "", "balanced", "[]", "[]")["result"]
    assert len(res) == 11 and res[10] == ""                      # not given (older workflow): empty
    res = node.run(m1, m2, 704, 1280, False, 24.0, False, 7, "", "balanced", "[]", "[]", " 4-8, 1:12-1:15 ")["result"]
    assert res[10] == "4-8, 1:12-1:15"
    try:
        node.run(m1, m2, 704, 1280, False, 24.0, False, 7, "", "balanced", "[]", "[]", "eight")
        raise AssertionError("wrong redraw writing accepted")
    except ValueError as exc:
        assert "redraw" in str(exc)
    import h3md_loop as L
    import h3md_loop_split as LS
    assert list(L.H3MDLoopSampler.INPUT_TYPES()["optional"])[-1] == "redraw"
    main = LS.H3MDLoopSampler2.INPUT_TYPES()["optional"]
    assert list(main)[-2:] == ["segments_to_run", "redraw"] and "redraw" not in LS.option_defaults()
    import inspect
    src = inspect.getsource(L.H3MDLoopSampler.run)
    assert src.index('pop("redraw"') < src.index("WIN.wrap_models")      # applied to the plan before anything runs


def test_066_redraw_ignored_without_window_mode():
    import h3md_window as WIN
    plan = {"segments": [{"startFrame": 0, "length": 243}], "windowMode": {"length": 243, "overlap": 22}}
    out = WIN.redraw_plan(plan, "4-8")
    assert out["windowMode"]["redraw"] == [[4.0, 8.0]] and "redraw" not in plan["windowMode"]
    try:                                                         # the Redraw NODE still says why (it was added on purpose)
        WIN.redraw_plan({"segments": plan["segments"]}, "4-8")
        raise AssertionError("redraw without window mode accepted by the node")
    except ValueError:
        pass
