"""v0.2.0 plan export / import: round trip, safety, agent .json, problem list."""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import h3md_plan_io as P  # noqa: E402

PLAN = {
    "version": 1, "fps": 24, "width": 704, "height": 1280, "globalPrompt": "",
    "media": {"images": [{"id": "eve", "file": "eve_home_ref.png"}],
              "videos": [],
              "audios": [{"id": "song", "file": "music/above.mp3"}], "refmods": []},
    "segments": [
        {"length": 294, "overlapIn": 0, "prompt": "<Picture 1> sits. <Audio 1> plays.", "images": ["eve"],
         "audios": ["song"]},
        {"length": 362, "overlapIn": 39, "prompt": "She keeps singing to <Audio 1>.", "audios": ["song"]},
    ],
}
WF = {"nodes": [
    {"id": 1, "type": "H3MDDirectorRefMod", "widgets_values": [json.dumps(PLAN), 0, 0, True, 24]},
    {"id": 2, "type": "H3MDLoopSamplerRefMod", "widgets_values": ["", 0, False, 27675700028608, "randomize"]},
    {"id": 3, "type": "UNETLoader", "widgets_values": ["minimax_h3_bf16.safetensors", "default"]},
    {"id": 4, "type": "Power Lora Loader (rgthree)",
     "widgets_values": [{}, {"on": True, "lora": "realism_people.safetensors", "strength": 0.5}]},
], "models": [{"name": "h3_vae.safetensors", "directory": "vae", "url": "https://example.invalid/vae"}]}


def _input():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "eve_home_ref.png"), "wb") as f:
        f.write(b"\x89PNG fake")
    os.makedirs(os.path.join(d, "music"))
    with open(os.path.join(d, "music", "above.mp3"), "wb") as f:
        f.write(b"ID3 fake")
    return d


def test_layout():
    assert P.plan_layout(PLAN) == (2, 294 + 323, round(617 / 24, 2))


def test_export_with_media_roundtrip():
    src = _input()
    data, man = P.export_plan(json.dumps(PLAN), WF, src, with_media=True, name="Eve MV")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert {"manifest.json", "plan.json", "workflow.json", "settings.json",
            "media/eve_home_ref.png", "media/above.mp3"} <= names
    assert man["seed"] == 27675700028608 and man["shots"] == 2 and man["name"] == "Eve MV"
    models = {m["name"] for m in man["models"]}
    assert {"minimax_h3_bf16.safetensors", "realism_people.safetensors", "h3_vae.safetensors"} <= models
    settings = json.loads(z.read("settings.json"))
    assert settings[0]["widgets_values"][0] == "<plan.json>"

    dst = tempfile.mkdtemp()
    out = P.import_plan(data, dst, "Eve MV (plan + media).zip")
    assert out["folder"] == "H3MD_plans/Eve MV"
    plan = json.loads(out["plan_json"])
    assert plan["media"]["images"][0]["file"] == "H3MD_plans/Eve MV/eve_home_ref.png"
    assert plan["media"]["audios"][0]["file"] == "H3MD_plans/Eve MV/above.mp3"
    assert out["problems"] == [], out["problems"]
    assert out["workflow"]["nodes"][1]["widgets_values"][3] == 27675700028608
    # second import never overwrites
    out2 = P.import_plan(data, dst, "x.zip")
    assert out2["folder"] == "H3MD_plans/Eve MV_2"


def test_export_without_media_lists_missing_on_import():
    src = _input()
    data, man = P.export_plan(json.dumps(PLAN), WF, src, with_media=False)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert not [n for n in names if n.startswith("media/")]
    out = P.import_plan(data, tempfile.mkdtemp(), "p.zip")
    assert out["folder"] is None
    assert any("missing file: eve_home_ref.png" in p for p in out["problems"])


def test_missing_source_file_is_listed_not_fatal():
    src = tempfile.mkdtemp()
    data, man = P.export_plan(PLAN, None, src, with_media=True)
    assert all(m.get("missing") for m in man["media"])
    assert "workflow.json" not in zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_unsafe_members_are_ignored():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("plan.json", json.dumps(PLAN))
        z.writestr("../evil.png", b"x")
        z.writestr("media/../../evil2.png", b"x")
        z.writestr("media/run.py", b"x")
        z.writestr("media/ok.png", b"x")
    dst = tempfile.mkdtemp()
    out = P.import_plan(buf.getvalue(), dst, "bad.zip")
    written = []
    for root, _, files in os.walk(dst):
        written += [os.path.relpath(os.path.join(root, f), dst) for f in files]
    assert written == [os.path.join("H3MD_plans", "bad", "ok.png")], written
    assert sum("ignored unexpected file" in p for p in out["problems"]) == 3


def test_agent_json_and_problems():
    bad = json.loads(json.dumps(PLAN))
    bad["segments"][1]["length"] = 360            # not 5+17n
    bad["segments"][1]["overlapIn"] = 30
    bad["segments"][0]["prompt"] += " <Picture 2>"
    out = P.import_plan(json.dumps({"plan": bad}).encode(), tempfile.mkdtemp(), "agent.json")
    pr = "\n".join(out["problems"])
    assert out["kind"] == "json"
    assert "shot 2: length 360" in pr and "overlapIn 30" in pr and "<Picture 2>" in pr
    assert "missing file: eve_home_ref.png" in pr


def test_big_video_skipped_unless_asked():
    src = _input()
    p = json.loads(json.dumps(PLAN))
    p["media"]["videos"] = [{"id": "ref", "file": "ref.mp4"}]
    with open(os.path.join(src, "ref.mp4"), "wb") as f:
        f.seek(P.BIG_VIDEO_BYTES + 10)
        f.write(b"\0")
    data, man = P.export_plan(p, WF, src, with_media=True)
    v = [m for m in man["media"] if m["kind"] == "videos"][0]
    assert not v["included"] and "200 MB" in v["skipped"]
    assert P.export_size(p, src)["big_videos"][0]["file"] == "ref.mp4"



def test_models_skip_bypassed_and_off_rows():
    wf = {"nodes": [
        {"id": 1, "type": "UNETLoader", "mode": 4, "widgets_values": ["bypassed.safetensors"]},
        {"id": 2, "type": "Power Lora Loader (rgthree)", "widgets_values": [
            {"on": False, "lora": "off.safetensors"}, {"on": True, "lora": "on.safetensors"}]}]}
    names = {m["name"] for m in P.workflow_models(wf)}
    assert names == {"on.safetensors"}, names


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"):
            f()
            print("ok", k)
