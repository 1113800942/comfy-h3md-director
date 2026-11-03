"""H3MD drift correct: pull every continued segment of a finished video back to the first segment of its shot
(saturation + sharpness).

Usage (ComfyUI's Python: PyAV, numpy, torch):

    python tools/drift_correct.py my_video.mp4 --segments 294,362,362,362 --overlap 39
    python tools/drift_correct.py my_video.mp4 --every 13.5 --strength 0.7 --no-sharpen

Shot by shot: every hard cut (overlapIn 0) starts a new shot whose first segment is the new reference, so a film
with several scenes can use it - each continued segment is pulled back to the first segment of ITS shot.  Shots with
fewer than --min-shot segments (default 3) are left alone.  Inside one shot the look must stay the same: a shot whose
light or content is meant to change (night falls, someone walks in) must not be corrected.

    python tools/drift_correct.py film.mp4 --plan "my workflow.json"        (or a plan .json / exported plan .zip)
    python tools/drift_correct.py film.mp4 --segments 294,362,362,362:0,362,362

Scene changes inside a shot are recognised and left alone: a continued segment whose brightness, contrast, colour or
layout differs clearly from its reference (day -> night, a new room, someone walks in), or whose saturation /
sharpness change is too large to be drift, is NOT corrected and becomes the new reference (h3md_drift_correct.py).
Every decision is written to <video>_corrected.json and printed.

Each drifting segment's mean saturation and sharpness are measured exactly like tools/drift_report.py, then:
  * saturation: colour is scaled around each pixel's own brightness and the segment's average colour cast (a single
    gain per segment), so the overall tint does not move,
  * sharpness: a light unsharp mask on brightness only (sigma 1.2 px, amount capped at 0.8),
solved per segment so the segment matches its shot's first segment (x strength).  The change eases in over 2 s (raised cosine) after
each continued seam, so nothing jumps (a hard cut keeps its cut).  Brightness, contrast and hue are not touched.  The original audio is copied unchanged.
Writes <video>_corrected.mp4 (H.264 CRF 16) and <video>_corrected.json (the gain / amount per segment).

Measured on the 3-minute single take (14 segments, lowres_anchor = 1):
saturation +8.4 % -> -0.2 %, sharpness -18.0 % -> +1.3 % (worst segment vs segment 1).
"""
import argparse
import json
import os
import sys

import av
import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import drift_report as DR  # noqa: E402
import h3md_drift_correct as DC  # noqa: E402


def parse_segments(text, overlap):
    """'294,362,362:0,362' -> [(294, 0), (362, 39), (362, 0), (362, 39)]  (':0' = hard cut, ':22' = other overlap)."""
    out = []
    for i, tok in enumerate(t.strip() for t in text.split(",") if t.strip()):
        ln, _, ov = tok.partition(":")
        out.append((int(ln), 0 if i == 0 else (int(ov) if ov else overlap)))
    return out


def plan_segments(path):
    """(length, overlapIn) per segment from an H3MD plan .json, a workflow .json or an exported plan .zip."""
    import zipfile
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            obj = json.loads(z.read("plan.json").decode("utf-8"))
    else:
        with open(path, encoding="utf-8-sig") as f:
            obj = json.load(f)
    if isinstance(obj, dict) and "nodes" in obj:          # a ComfyUI workflow: take the Director's plan_json
        nodes = list(obj.get("nodes") or [])
        for sg in (obj.get("definitions") or {}).get("subgraphs") or []:
            nodes += sg.get("nodes") or []
        plans = [n["widgets_values"][0] for n in nodes if str(n.get("type", "")).startswith("H3MDDirector")
                 and isinstance(n.get("widgets_values"), list) and n["widgets_values"]]
        if not plans:
            raise SystemExit("no H3MD Director node in this workflow")
        obj = json.loads(plans[0]) if isinstance(plans[0], str) else plans[0]
    if isinstance(obj, dict) and isinstance(obj.get("plan"), (dict, str)) and "segments" not in obj:
        obj = obj["plan"] if isinstance(obj["plan"], dict) else json.loads(obj["plan"])
    segs = [s for s in obj.get("segments") or [] if isinstance(s, dict)]
    return [(int(s["length"]), 0 if i == 0 else int(s.get("overlapIn") or 0)) for i, s in enumerate(segs)]


def layout(n, fps, segs=None, every=None):
    """-> blocks [(start, end)] of visible frames and cut flags (True = first segment / hard cut)."""
    if segs:
        scale = fps / 24.0                     # H3MD renders 24 fps; an interpolated 48 fps file has 2x the frames
        b, cut = [0], []
        for i, (ln, ov) in enumerate(segs):
            b.append(b[-1] + (ln if i == 0 else ln - ov))
            cut.append(i == 0 or ov == 0)
        b = [int(round(x * scale)) for x in b]
        b[-1] = n if abs(b[-1] - n) <= 2 * scale + 1 else min(b[-1], n)
        blocks = [(b[i], b[i + 1]) for i in range(len(segs)) if b[i + 1] > b[i]]
        return blocks, cut[:len(blocks)]
    blocks = DR.bounds(n, fps, None, 0, every)
    return blocks, [i == 0 for i in range(len(blocks))]


class SequentialReader:
    """read(a, b) for increasing, contiguous ranges of a video file (decoded once, front to back)."""

    def __init__(self, path):
        self.c = av.open(path)
        self.it = self.c.decode(video=0)
        self.pos = 0

    def read(self, a, b):
        while self.pos < a:
            next(self.it)
            self.pos += 1
        out = []
        while self.pos < b:
            out.append(torch.from_numpy(next(self.it).to_ndarray(format="rgb24")))
            self.pos += 1
        return torch.stack(out).float() / 255.0

    def close(self):
        self.c.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--segments", help="segment lengths in frames, e.g. 294,362,362; LEN:0 = this segment starts with a "
                                       "hard cut (a new shot, measured against its own first segment)")
    ap.add_argument("--plan", help="read the segments from an H3MD plan .json, a workflow .json or an exported plan .zip")
    ap.add_argument("--overlap", type=int, default=39)
    ap.add_argument("--every", type=float, help="cut into blocks of N seconds instead (one shot)")
    ap.add_argument("--strength", type=float, default=1.0, help="0..1 (default 1 = match the reference)")
    ap.add_argument("--no-sharpen", action="store_true", help="saturation only")
    ap.add_argument("--min-shot", type=int, default=3, help="shots with fewer segments are left untouched (default 3)")
    ap.add_argument("--out", help="output file (default <video>_corrected.mp4)")
    a = ap.parse_args()
    out_path = a.out or os.path.splitext(a.video)[0] + "_corrected.mp4"

    with av.open(a.video) as c:
        vs = c.streams.video[0]
        fps = float(vs.average_rate or 24)
        n = vs.frames or 0
    if not n:
        with av.open(a.video) as c:
            n = sum(1 for _ in c.decode(video=0))
    segs = plan_segments(a.plan) if a.plan else (parse_segments(a.segments, a.overlap) if a.segments else None)
    blocks, cut = layout(n, fps, segs, a.every)

    rd = SequentialReader(a.video)
    try:
        plan = DC.build(rd.read, blocks, cut, fps, a.strength, not a.no_sharpen, a.min_shot)
    finally:
        rd.close()
    for row in plan.report():
        extra = f" -> colour gain {row['gain']:.3f}, sharpen {row['sharpen']:.3f}" if row["role"] == "drift" else ""
        why = f" ({row['why']})" if row.get("why") else ""
        print(f"segment {row['segment']} (shot {row['shot']}): {row['role']}{why}{extra}")

    with av.open(a.video) as c, av.open(out_path, "w") as o:
        vin = c.streams.video[0]
        vout = o.add_stream("libx264", rate=vin.average_rate or 24)
        vout.width, vout.height, vout.pix_fmt = vin.codec_context.width, vin.codec_context.height, "yuv420p"
        vout.options = {"crf": "16", "preset": "medium"}
        ain = c.streams.audio[0] if c.streams.audio else None
        aout = o.add_stream_from_template(ain) if ain is not None else None
        streams = [vin] + ([ain] if ain is not None else [])
        k = 0
        for pkt in c.demux(*streams):
            if pkt.stream is ain:
                if pkt.dts is not None:
                    pkt.stream = aout
                    o.mux(pkt)
                continue
            for fr in pkt.decode():
                t = torch.from_numpy(fr.to_ndarray(format="rgb24")).float()[None] / 255.0
                img = (plan.correct(t, k)[0] * 255).round().clamp(0, 255).to(torch.uint8).numpy()
                for p in vout.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                    o.mux(p)
                k += 1
        for p in vout.encode():
            o.mux(p)
    with open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"video": os.path.basename(a.video), "strength": a.strength, "sharpen": not a.no_sharpen,
                   "min_shot": a.min_shot, "shots": int(sum(cut)), "segments": plan.report()}, f, indent=1)
    print("written:", out_path)


if __name__ == "__main__":
    main()
