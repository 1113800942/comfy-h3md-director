"""Smoke test of the two Keyframe nodes on a synthetic clip (SAM3 replaced by the known boxes)."""
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
    crops, pics, prompts, job, sheet, info = N.H3MDKeyframePrepare().prepare(
        "clips/src.mp4", "图1换左二", "a man with short red hair, yellow coat", "", 5.0, "en", 0.4, 8,
        picture_1=pic)["result"]
    i = json.loads(info)
    assert i["people_at_start"] == 3 and i["mapping"] == [{"picture": 1, "left": 2}]
    assert len(crops) == len(pics) == len(prompts) == i["edits"] >= 2
    assert all(c.shape[1] % 32 == 0 and c.shape[2] % 32 == 0 for c in crops)
    assert pics[0].shape[1] * pics[0].shape[2] <= 1.0e6
    assert job["size"] == [1664, 960] and job["length"] <= SEC * FPS
    assert not any(5.0 < m["at"] < 6.0 for m in job["moments"])
    assert "change places" in job["prompt"]
    edited, k = [], 0                                       # "edit" = paint the person white, keep the rest
    for m in job["moments"]:
        for it in m["items"]:
            c = crops[k].clone()
            k += 1
            x0, y0, x1, y1 = it["crop"]
            sx, sy = c.shape[2] / (x1 - x0), c.shape[1] / (y1 - y0)
            a, b, e, f = it["inner"]
            c[:, int(b * sy):int(f * sy), int(a * sx):int(e * sx)] = 1.0
            edited.append(c)
    sheet2, plan_s, zp = N.H3MDKeyframeAssemble().assemble([job], edited, ["t1"], [True])["result"]
    plan = json.loads(plan_s)
    seg = plan["segments"][0]
    assert plan["swapEngine"] == "remake" and seg["videos"][0]["useDuration"] == round(seg["length"] / 24, 4)
    assert plan["media"]["videos"][0]["file"] == "clips/src.mp4"
    kf = plan["media"]["images"][0]["file"]
    assert os.path.isfile(inp / kf)
    from PIL import Image
    a = np.asarray(Image.open(inp / kf))
    m = job["moments"][0]
    own = m["items"][0]["own"]
    cx, cy = int((own[0] + own[2]) / 2), int((own[1] + own[3]) / 2)
    assert a[cy, cx].min() > 150                             # recast person painted
    other = [b for b in m["items"][0]["others"] if b[0] > own[2]][0]
    ox, oy = int((other[0] + other[2]) / 2), int((other[1] + other[3]) / 2)
    assert abs(int(a[oy, ox, 2]) - 200) < 40                 # the blue neighbour keeps its pixels
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
    assert "plan.json" in names and any(n.endswith("src.mp4") for n in names)
    assert len([n for n in names if n.endswith(".png")]) == len(plan["media"]["images"])
