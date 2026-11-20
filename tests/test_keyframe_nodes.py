"""Smoke test of the Keyframe nodes Prepare and Assemble on a synthetic clip (SAM3 replaced by the known boxes)."""
import json
import os
import sys
import zipfile

import numpy as np
import pytest

av = pytest.importorskip("av")
pytest.importorskip("folder_paths")
torch = pytest.importorskip("torch")

import h3md_keyframe_nodes as N  # noqa: E402

W, H, FPS, SEC = 832, 480, 24, 12
COLS = [(200, 40, 40), (40, 200, 40), (40, 40, 200)]


def boxes_at(t):
    """three people; 0 and 1 change places between 5 and 6 s (normalised boxes, in id order)"""
    s = min(1.0, max(0.0, t - 5.0))
    xs = [0.15 + 0.3 * s, 0.45 - 0.3 * s, 0.75]
    return [[x - 0.06, 0.15, x + 0.06, 0.95] for x in xs]


@pytest.fixture()
def clip(tmp_path, monkeypatch):
    import folder_paths
    inp, out = tmp_path / "input", tmp_path / "output"
    inp.mkdir()
    out.mkdir()
    monkeypatch.setattr(folder_paths, "get_input_directory", lambda: str(inp))
    monkeypatch.setattr(folder_paths, "get_output_directory", lambda: str(out))
    p = inp / "clips" / "src.mp4"
    p.parent.mkdir()
    with av.open(str(p), "w") as c:
        s = c.add_stream("libx264", rate=FPS)
        s.width, s.height, s.pix_fmt = W, H, "yuv420p"
        for i in range(FPS * SEC):
            img = np.full((H, W, 3), 128, np.uint8)
            for b, col in zip(boxes_at(i / FPS), COLS):
                img[int(b[1] * H):int(b[3] * H), int(b[0] * W):int(b[2] * W)] = col
            for pk in s.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(pk)
        for pk in s.encode():
            c.mux(pk)

    def fake_track(frames, **kw):
        n = frames.shape[0]
        return [[{o: b for o, b in enumerate(boxes_at(i / N.SAMPLE_FPS))} for i in range(n)]]
    monkeypatch.setattr(N, "track_people", fake_track)
    return inp, out


def test_prepare_assemble(clip):
    inp, out = clip
    pic = torch.rand(1, 1200, 1000, 3)
    pics, job, sheet, info = N.H3MDKeyframePrepare().prepare(
        "clips/src.mp4", "图1换左二", "a man with short red hair, yellow coat", "", 5.0, 0.4, 8,
        picture_1=pic)["result"]
    i = json.loads(info)
    assert i["people_at_start"] == 3 and i["mapping"] == [{"picture": 1, "left": 2}]
    assert len(pics) == i["recasts"] == sum(len(m["items"]) for m in job["moments"]) >= 2
    assert job["size"] == [1664, 960] and job["length"] <= SEC * FPS
    assert not any(5.0 < m["at"] < 6.0 for m in job["moments"])
    assert "change places" in job["prompt"]
    for m in job["moments"]:                                  # 0.5.91: where she stands NOW (they change places)
        assert len(m["boxes"]) == 3
        for it in m["items"]:
            assert it["rank"] == (2 if m["at"] < 5.0 else 1) and it["n"] == 3 and it["desc"].startswith("a man")
    done = {}                                                 # stand-in for Recast: paint the person white
    for m in job["moments"]:
        f = job["frames"][m["at"]].copy()
        for it in m["items"]:
            x0, y0, x1, y1 = [int(v) for v in it["own"]]
            f[y0:y1, x0:x1] = 255
        done[m["at"]] = f
    cm = np.zeros((20, 40, 3), np.uint8)
    job2 = dict(job, recast_frames=done, recast_colours={job["moments"][0]["at"]: cm},
                recast={"flags": {str(job["moments"][0]["at"]): "swap"}, "report": "r"})
    sheet2, plan_s, zp = N.H3MDKeyframeAssemble().assemble(job2, "t1", True)["result"]
    plan = json.loads(plan_s)
    seg = plan["segments"][0]
    assert plan["swapEngine"] == "remake" and seg["videos"][0]["useDuration"] == round(seg["length"] / 24, 4)
    assert plan["media"]["videos"][0]["file"] == "clips/src.mp4"
    kf = plan["media"]["images"][0]["file"]
    assert os.path.isfile(inp / kf) and os.path.isfile(inp / os.path.dirname(kf) / "recast_report.txt")
    assert len([f for f in os.listdir(inp / os.path.dirname(kf)) if f.startswith("colours_")]) == 1
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
    assert "plan.json" in names and any(n.endswith("src.mp4") for n in names)
    assert len([n for n in names if n.endswith(".png")]) == len(plan["media"]["images"])
    with pytest.raises(N.K.KeyframeError):                    # Assemble needs Recast's job
        N.H3MDKeyframeAssemble().assemble(job, "t2", False)
