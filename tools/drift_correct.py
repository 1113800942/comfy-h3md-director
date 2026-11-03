"""H3MD drift correct: pull every segment of a finished long video back to segment 1 (saturation + sharpness).

Usage (ComfyUI's Python: PyAV, numpy, torch):

    python tools/drift_correct.py my_video.mp4 --segments 294,362,362,362 --overlap 39
    python tools/drift_correct.py my_video.mp4 --every 13.5 --strength 0.7 --no-sharpen

For ONE continuous scene (a long take, motion copy) - a story whose look should change must not use it.
Each segment's mean saturation and sharpness are measured exactly like tools/drift_report.py (360 px wide), then:
  * saturation: colour is scaled around each pixel's own brightness and the segment's average colour cast (a single
    gain per segment), so the overall tint does not move,
  * sharpness: a light unsharp mask on brightness only (sigma 1.2 px, amount capped at 0.8),
solved per segment so the segment matches segment 1 (x strength).  The change eases in over 2 s (raised cosine) after
each seam, so nothing jumps.  Brightness, contrast and hue are not touched.  The original audio is copied unchanged.
Writes <video>_corrected.mp4 (H.264 CRF 16) and <video>_corrected.json (the gain / amount per segment).

Measured on the 3-minute single take (14 segments, lowres_anchor = 1):
saturation +8.4 % -> +0.1 %, sharpness -18.0 % -> +1.5 % (worst segment vs segment 1).
"""
import argparse
import json
import math
import os
import sys

import av
import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drift_report as DR  # noqa: E402

EASE_S = 2.0
SIGMA = 1.2
MAX_AMOUNT = 0.8
SAMPLES = 6


def stats(rgb):
    t = torch.from_numpy(rgb).permute(2, 0, 1)[None].float()
    h = max(2, round(rgb.shape[0] * 360 / rgb.shape[1] / 2) * 2)
    s = F.interpolate(t, size=(h, 360), mode="area")[0].permute(1, 2, 0).clamp(0, 255).round().byte().numpy()
    L, _, _ = DR._lab_like(s)
    return DR._saturation(s).mean(), DR._sharpness(L)


def _kernel():
    r = int(math.ceil(3 * SIGMA))
    x = torch.arange(-r, r + 1, dtype=torch.float32)
    k = torch.exp(-x * x / (2 * SIGMA * SIGMA))
    return k / k.sum()


KERNEL = _kernel()


def chroma_mean(frames):
    """Mean colour offset (RGB minus brightness) of a few frames: the cast that the gain must not change."""
    t = torch.from_numpy(np.stack(frames)).float()
    y = (t * torch.tensor([0.299, 0.587, 0.114])).sum(-1, keepdim=True)
    return (t - y).mean(dim=(0, 1, 2))


def correct(rgb, gain, amount, cast=None):
    t = torch.from_numpy(rgb).float()
    y = (t * torch.tensor([0.299, 0.587, 0.114])).sum(-1, keepdim=True)
    c = cast if cast is not None else torch.zeros(3)
    out = y + (t - y - c) * gain + c
    if amount > 0:
        r = (len(KERNEL) - 1) // 2
        yy = y.permute(2, 0, 1)[None]
        bl = F.conv2d(F.pad(yy, (r, r, 0, 0), mode="replicate"), KERNEL.view(1, 1, 1, -1))
        bl = F.conv2d(F.pad(bl, (0, 0, r, r), mode="replicate"), KERNEL.view(1, 1, -1, 1))
        out = out + (yy - bl)[0].permute(1, 2, 0) * amount
    return out.clamp(0, 255).round().byte().numpy()


def bisect(fn, target, lo, hi, increasing, n=12):
    for _ in range(n):
        mid = (lo + hi) / 2
        if (fn(mid) > target) == increasing:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--segments", help="segment lengths in frames, e.g. 294,362,362")
    ap.add_argument("--overlap", type=int, default=39)
    ap.add_argument("--every", type=float, help="cut into blocks of N seconds instead")
    ap.add_argument("--strength", type=float, default=1.0, help="0..1 (default 1 = match segment 1)")
    ap.add_argument("--no-sharpen", action="store_true", help="saturation only")
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
    segs = [int(x) for x in a.segments.split(",")] if a.segments else None
    blocks = DR.bounds(n, fps, segs, a.overlap, a.every)
    seg_of = np.zeros(n, np.int32)
    for i, (s, e) in enumerate(blocks):
        seg_of[s:e] = i

    raw = [[] for _ in blocks]
    samples = [[] for _ in blocks]
    with av.open(a.video) as c:
        for k, fr in enumerate(c.decode(video=0)):
            if k >= n:
                break
            i = seg_of[k]
            rgb = fr.to_ndarray(format="rgb24")
            raw[i].append(stats(rgb))
            s, e = blocks[i]
            if (k - s) % max(1, (e - s) // SAMPLES) == 0 and len(samples[i]) < SAMPLES:
                samples[i].append(rgb)
    ref = np.array(raw[0]).mean(0)
    params = [(1.0, 0.0)]
    casts = [chroma_mean(samples[0])]
    rows = []
    for i in range(1, len(blocks)):
        m = np.array(raw[i]).mean(0)
        smp = samples[i]
        cast = chroma_mean(smp)
        casts.append(cast)

        def sat(g, amt=0.0):
            return np.mean([stats(correct(f, g, amt, cast))[0] for f in smp])

        def sharp(amt, g):
            return np.mean([stats(correct(f, g, amt, cast))[1] for f in smp])
        s0 = sat(1.0)
        tgt = (m[0] + (ref[0] - m[0]) * a.strength) * s0 / m[0]
        g = bisect(sat, tgt, 0.7, 1.3, True)
        amt = 0.0
        if not a.no_sharpen and m[1] < ref[1]:
            k0 = sharp(0.0, g)
            tk = (m[1] + (ref[1] - m[1]) * a.strength) * k0 / m[1]
            amt = bisect(lambda x: sharp(x, g), tk, 0.0, MAX_AMOUNT, True, n=10)
        params.append((g, amt))
        rows.append({"segment": i + 1, "saturation_pct": round(100 * (m[0] / ref[0] - 1), 1),
                     "sharpness_pct": round(100 * (m[1] / ref[1] - 1), 1), "gain": round(g, 4), "amount": round(amt, 4)})
        print(f"segment {i + 1}: saturation {rows[-1]['saturation_pct']:+.1f} %, sharpness {rows[-1]['sharpness_pct']:+.1f} %"
              f" -> colour gain {g:.3f}, sharpen {amt:.3f}")

    ease = max(1, int(round(EASE_S * fps)))

    def factor(k):
        i = seg_of[min(k, n - 1)]
        g, amt = params[i]
        c = casts[i]
        if i > 0:
            p = k - blocks[i][0]
            e = max(1, min(ease, (blocks[i][1] - blocks[i][0]) // 3))   # short blocks: ease over their first third
            if p < e:
                w = 0.5 - 0.5 * math.cos(math.pi * p / e)
                g0, a0 = params[i - 1]
                g, amt, c = g0 + (g - g0) * w, a0 + (amt - a0) * w, casts[i - 1] + (c - casts[i - 1]) * w
        return g, amt, c

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
                g, amt, c = factor(k)
                img = correct(fr.to_ndarray(format="rgb24"), g, amt, c)
                nf = av.VideoFrame.from_ndarray(img, format="rgb24")
                for p in vout.encode(nf):
                    o.mux(p)
                k += 1
        for p in vout.encode():
            o.mux(p)
    with open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump({"video": os.path.basename(a.video), "strength": a.strength, "sharpen": not a.no_sharpen,
                   "segments": rows}, f, indent=1)
    print("written:", out_path)


if __name__ == "__main__":
    main()
