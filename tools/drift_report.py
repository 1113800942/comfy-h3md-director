"""H3MD drift report: measure how much a long video drifts from its first segment.

Usage (run with ComfyUI's Python, which already has PyAV and numpy):

    python tools/drift_report.py my_video.mp4
    python tools/drift_report.py my_video.mp4 --segments 294,362,362,362 --overlap 39
    python tools/drift_report.py my_video.mp4 --every 13.5

For every segment it prints brightness, contrast, saturation and sharpness as a % change versus segment 1, plus the
colour shift (distance in the a/b colour plane, CIELAB-like; below about 2 is hard to see). It writes
<video>_drift.md (table) and, if matplotlib is installed, <video>_drift.png (chart with a ±10 % band).

Segments: pass the H3MD segment lengths in frames (as in the Director plan) with --overlap (39 by default), or
--every N to cut the video into N-second blocks. With neither, the video is cut into 10 equal blocks.
Frames are scaled to 360 px wide before measuring, so the numbers compare videos of different sizes fairly.
"""
import argparse
import os

import av
import numpy as np


def _lab_like(rgb):
    """Rough CIELAB (D65) from 8-bit sRGB, enough for drift comparisons."""
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return L, a, b


def _saturation(rgb):
    c = rgb.astype(np.float32)
    mx, mn = c.max(-1), c.min(-1)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0) * 100


def _sharpness(gray):
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(lap.var())


def measure(path, width=360):
    rows = []
    with av.open(path) as box:
        stream = box.streams.video[0]
        fps = float(stream.average_rate or 24)
        for frame in box.decode(stream):
            h = max(2, round(frame.height * width / frame.width / 2) * 2)
            rgb = frame.to_ndarray(format="rgb24", width=width, height=h)
            L, a, b = _lab_like(rgb)
            rows.append((L.mean(), L.std(), _saturation(rgb).mean(), _sharpness(L), a.mean(), b.mean()))
    return np.array(rows, np.float64), fps


def bounds(n, fps, segments=None, overlap=39, every=None):
    if segments:
        b = [0]
        for i, length in enumerate(segments):
            b.append(b[-1] + (length if i == 0 else length - overlap))
        b[-1] = min(b[-1], n)
    elif every:
        step = max(1, round(every * fps))
        b = list(range(0, n, step)) + [n]
    else:
        b = [round(i * n / 10) for i in range(11)]
    return [(b[i], b[i + 1]) for i in range(len(b) - 1) if b[i + 1] > b[i]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--segments", help="segment lengths in frames, e.g. 294,362,362")
    ap.add_argument("--overlap", type=int, default=39)
    ap.add_argument("--every", type=float, help="cut into blocks of N seconds instead")
    args = ap.parse_args()

    data, fps = measure(args.video)
    segs = [int(x) for x in args.segments.split(",")] if args.segments else None
    blocks = bounds(len(data), fps, segs, args.overlap, args.every)
    means = [data[s:e].mean(0) for s, e in blocks]
    base = means[0]
    names = ["Brightness", "Contrast", "Saturation", "Sharpness"]
    lines = ["| Segment | Time | " + " | ".join(n + " %" for n in names) + " | Colour shift |",
             "|---:|---|" + "---:|" * (len(names) + 1)]
    pct = []
    for k, ((s, e), m) in enumerate(zip(blocks, means), 1):
        p = [100 * (m[i] / base[i] - 1) for i in range(4)]
        pct.append(p)
        shift = float(np.hypot(m[4] - base[4], m[5] - base[5]))
        lines.append(f"| {k} | {s / fps:.1f}–{e / fps:.1f} s | " + " | ".join(f"{v:+.1f}" for v in p) + f" | {shift:.2f} |")
    pct = np.array(pct)
    summary = "Largest change vs segment 1: " + ", ".join(
        f"{n.lower()} {pct[np.abs(pct[:, i]).argmax(), i]:+.1f} %" for i, n in enumerate(names))
    text = "\n".join(lines) + "\n\n" + summary + "\n"
    print(text)
    stem = os.path.splitext(args.video)[0]
    with open(stem + "_drift.md", "w", encoding="utf-8") as f:
        f.write(f"Drift report for `{os.path.basename(args.video)}`\n\n" + text)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib not installed: no chart)")
        return
    x = np.arange(1, len(pct) + 1)
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=150)
    for i, n in enumerate(names):
        ax.plot(x, pct[:, i], marker="o", ms=3, lw=1.6, label=n)
    ax.axhspan(-10, 10, color="#888", alpha=0.08, lw=0)
    ax.axhline(0, color="#666", lw=0.8)
    ax.set_xticks(x)
    ax.set_xlabel("Segment")
    ax.set_ylabel("Change vs segment 1 (%)")
    ax.legend(frameon=False, ncol=4, fontsize=8, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(stem + "_drift.png")
    print("chart:", stem + "_drift.png")


if __name__ == "__main__":
    main()
