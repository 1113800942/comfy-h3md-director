"""Diagnostic report: privacy scrub, plan checks (skill rules), report format."""
import json
import zipfile
import io

import pytest

import h3md_report as R


def test_scrub_paths_and_ids():
    s = R.scrub(r'File "D:\Comfy-Desktop\ComfyUI (1)\custom_nodes\pack\a.py", line 3; /home/alice/models/x.safetensors '
                r'E:\H3MD_cache\run 1\out.mp4 https://huggingface.co/Comfy-Org/Qwen me@x.org 10.0.0.7 1664x960 3:4')
    assert "Comfy-Desktop" not in s and "alice" not in s and "H3MD_cache" not in s
    assert '".../a.py"' in s and ".../x.safetensors" in s and ".../out.mp4" in s
    assert "https://huggingface.co/Comfy-Org/Qwen" in s and "<email>" in s and "<ip>" in s and "1664x960" in s


def test_scrub_obj_prompts():
    o = R.scrub_obj({"prompt": "x" * 400, "file": r"C:\a\b\c.png", "n": 3}, keep_text=False)
    assert o["prompt"] == "<text, 400 chars>" and o["file"] == ".../c.png" and o["n"] == 3


def _plan(**kw):
    p = {"version": 1, "fps": 24, "width": 1664, "height": 960, "media": {"images": [], "videos": [], "audios": []},
         "segments": [{"length": 345, "overlapIn": 0, "images": [], "videos": [], "audios": [],
                       "prompt": "detailed_description:\nA dancer on a stage."}]}
    p.update(kw)
    return p


def test_plan_length_error():
    p = _plan()
    p["segments"][0]["length"] = 300
    F, _ = R.plan_findings(p)
    assert any(f["sev"] == "error" and "5 + 17n" in f["text"] for f in F)


def test_window_mode_rules():
    p = _plan(windowMode={"length": 175, "overlap": 39})
    p["segments"][0]["length"] = 600
    F, s = R.plan_findings(p)
    assert s["windowMode"]
    assert any(f["cat"] == "skill" and "no timed actions" in f["text"] for f in F)
    p["segments"][0]["prompt"] = "detailed_description:\nA dancer on a stage. [1s] She waves."
    F, _ = R.plan_findings(p)
    assert any(f["cat"] == "skill" and "has no timed action" in f["text"] for f in F)


def test_remake_rules():
    p = _plan(swapEngine="remake", autoSplit=True, width=1280, height=720)
    F, _ = R.plan_findings(p)
    assert any(f["cat"] == "settings" and "1664x960" in f["text"] for f in F)


def test_markdown_and_zip():
    r = {"created": "now", "h3md": "0.5.0", "prompts_included": False,
         "environment": {"h3md": "0.5.0", "gpu": "RTX", "vram_total_gb": 16.0,
                         "node_packs": [{"pack": "VideoHelperSuite", "installed": True, "version": "1.0"}]},
         "models": [{"name": "a.safetensors", "found": False}], "plan_summary": {"shots": 1},
         "findings": [{"sev": "error", "cat": "env", "where": "models", "text": "model file not found: a.safetensors"}],
         "recent_runs": [{"status": "error", "error": {"node_type": "X", "message": "boom", "traceback": "tb"}}],
         "log": ["12:00:00 I [H3MD] hi"], "settings": [], "plan": {}}
    md = R.to_markdown(r)
    assert "❌" in md and "NOT FOUND" in md and "boom" in md and "RTX" in md
    z = zipfile.ZipFile(io.BytesIO(R.write_zip(r)))
    assert sorted(z.namelist()) == ["plan.json", "report.json", "report.md", "settings.json"]
    assert "plan" not in json.loads(z.read("report.json"))


def test_ring_buffer():
    import logging
    ring = R._Ring()
    log = logging.getLogger("H3MD")
    log.addHandler(ring)
    old = log.level
    log.setLevel(logging.INFO)
    try:
        log.info("[H3MD] step 3 of 6")
        logging.getLogger("other").warning("not kept")
    finally:
        log.removeHandler(ring)
        log.setLevel(old)
    assert [l[3] for l in ring.lines] == ["[H3MD] step 3 of 6"]
