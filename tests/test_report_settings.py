"""Report 0.5.1: every setting named, node states, comparison with the shipped example workflow, key switches."""
import copy
import io
import json
import os
import zipfile

import h3md_report as R
import h3md_report_settings as RS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LOOP_SPEC = {"required": {"rerun_segments": ("STRING", {}), "stop_after": ("INT", {}), "rerun_new_seed": ("BOOLEAN", {}),
                          "plan": ("H3MD_PLAN",), "seed": ("INT", {"control_after_generate": True}),
                          "cfg": ("FLOAT", {}), "upscaler_model": (["none", "a"], {}),
                          "high_res_model": ("MODEL",), "highres_tiling": ("BOOLEAN", {})},
             "optional": {"segments_to_run": ("STRING", {"forceInput": False})}}


def test_widget_names_order_and_extras():
    assert RS.widget_names(LOOP_SPEC) == ["rerun_segments", "stop_after", "rerun_new_seed", "seed",
                                          "control_after_generate", "cfg", "upscaler_model", "highres_tiling",
                                          "segments_to_run"]
    img = {"required": {"image": (["a.png"], {"image_upload": True})}}
    assert RS.widget_names(img) == ["image", "upload"]
    legacy_seed = {"required": {"noise_seed": ("INT", {}), "x": ("IMAGE",), "s": ("STRING", {"forceInput": True})}}
    assert RS.widget_names(legacy_seed) == ["noise_seed", "control_after_generate"]


def _wf(tiling=True, loop_mode=0, sub_mode=0, extra=False):
    loop = {"id": 227, "type": "Loop", "mode": loop_mode,
            "widgets_values": ["", 0, True, 5, "fixed", 1.0, "a", tiling]}      # older save: no segments_to_run
    nodes = [{"id": 1, "type": "Director", "mode": 0, "widgets_values": ["{plan}", 0, 0]},
             {"id": 339, "type": "SG", "mode": sub_mode, "widgets_values": [5]},
             {"id": 9, "type": "Note", "mode": 0, "widgets_values": ["hi"]}]
    if extra:
        nodes.append({"id": 400, "type": "Mystery", "mode": 0, "widgets_values": [1, 2]})
    return {"nodes": nodes,
            "definitions": {"subgraphs": [{"id": "SG", "name": "Sampler", "nodes": [loop]}]}}


LOOK = {"Loop": RS.widget_names(LOOP_SPEC), "Director": ["plan_json", "width", "height"]}.get


def test_named_nodes_states_and_names():
    rows = RS.named_nodes(_wf(sub_mode=4), LOOK)
    by = {r["id"]: r for r in rows}
    assert 9 not in by                                        # notes are skipped
    assert by[339]["type"] == "subgraph" and by[339]["state"] == "bypassed"
    loop = by[227]
    assert loop["where"] == "Sampler" and loop["state"].startswith("off (subgraph bypassed")
    assert loop["params"]["highres_tiling"] is True and loop["params"]["control_after_generate"] == "fixed"
    assert "segments_to_run" not in loop["params"]            # shorter old save: named by prefix
    rows = RS.named_nodes(_wf(extra=True), LOOK)
    assert [r for r in rows if r["id"] == 400][0]["params"] == {"#1": 1, "#2": 2}


def test_compare_with_example():
    ex = RS.named_nodes(_wf(), LOOK)
    user = RS.named_nodes(_wf(tiling=False, loop_mode=4, extra=True), LOOK)
    user[0]["params"]["plan_json"] = "{other plan}"
    d = RS.compare(user, ex)
    assert [(c["param"], c["example"], c["now"]) for c in d["changed"]] == [("highres_tiling", True, False)]
    assert d["switched"][0]["now"] == "bypassed" and d["switched"][0]["example"] == "on"
    assert d["added"][0]["node"].startswith("Mystery") and not d["missing"]
    assert any(c["param"] == "plan_json" for c in d["run"])
    assert RS._same(0.2999999999999998, 0.3) and not RS._same(True, 1.5)


def test_findings_and_key_switches():
    user = RS.named_nodes(_wf(tiling=False), LOOK)
    for r in user:
        if r["type"] == "Loop":
            r["type"] = "H3MDLoopSamplerRefMod"
    F = RS.settings_findings(user, None, {"swapEngine": "remake"}, 24.0)
    assert any(f["sev"] == "warn" and "tiling is off" in f["text"] for f in F)
    assert any(f["sev"] == "error" and "Remake model loader is missing" in f["text"] for f in F)
    keys = dict(RS.key_switches(user, {"windowMode": {"length": 175}}))
    assert keys["High-res tiling 高清分块"] is False and keys["Seamless long video 无接缝长视频 (plan)"] is True
    assert keys["Semantic bridge 语义桥"].startswith("not in workflow")


def test_shipped_examples_compare_clean_and_flag_changes():
    """The simple example against itself: no differences; tiling switched off is the one change found."""
    look = RS.spec_lookup()
    path = os.path.join(ROOT, "example_workflows", "H3MD Director simple.json")
    w = json.load(open(path, encoding="utf-8"))
    examples = RS.load_examples(lookup=look)
    assert set(examples) >= {"H3MD Director simple.json", "H3MD Director.json", "H3MD Director Remake.json"}
    rows = RS.named_nodes(w, look)
    assert RS.best_example(rows, examples) in ("H3MD Director simple.json", "H3MD Director Remake.json")
    d = RS.compare(rows, examples["H3MD Director simple.json"])
    assert not (d["switched"] or d["changed"] or d["missing"] or d["added"])
    u = copy.deepcopy(w)
    for sg in u["definitions"]["subgraphs"]:
        for n in sg["nodes"]:
            if n["type"] == "H3MDLoopSamplerRefMod":
                n["widgets_values"][14] = False                   # highres_tiling
    d = RS.compare(RS.named_nodes(u, look), examples["H3MD Director simple.json"])
    names = {c["param"] for c in d["changed"]}
    assert names == {"highres_tiling"} or (names == {"#15"} and look("H3MDLoopSamplerRefMod") is None)


def _h3md_lookup():
    import importlib
    import h3_harness  # noqa: F401  (ComfyUI on CPU)
    m = {}
    for name in ("h3md_control_panel", "h3md_loop", "h3md_loop_split", "h3md_director_refmod", "h3md_face_refine_save",
                 "h3md_depth_control", "h3md_lora", "h3md_semantic_bridge", "h3md_window"):
        try:
            m.update(importlib.import_module(name).NODE_CLASS_MAPPINGS)
        except Exception:
            pass
    return lambda t: RS.widget_names(m[t].INPUT_TYPES()) if t in m else None


def test_report_zip_has_settings_md(monkeypatch):
    monkeypatch.setattr(RS, "spec_lookup", _h3md_lookup)
    w = json.load(open(os.path.join(ROOT, "example_workflows", "H3MD Director simple.json"), encoding="utf-8"))
    r = R.build({"version": 1, "segments": []}, w, include_prompts=False)
    assert r["example_workflow"] in ("H3MD Director simple.json", "H3MD Director Remake.json")
    keys = dict(r["key_switches"])
    assert keys["High-res tiling 高清分块"] is True and keys["Style 风格"] == "balanced"
    md = R.to_markdown(r)
    assert "Key switches 关键开关" in md and "Differences from the example" in md
    z = zipfile.ZipFile(io.BytesIO(R.write_zip(r)))
    assert "settings.md" in z.namelist()
    s = z.read("settings.md").decode("utf-8")
    assert "<plan.json>" in s or "plan_json" not in s
    assert "combat" in json.dumps(r["key_switches"], ensure_ascii=False)     # LoRA rows survive prompts-off scrub
