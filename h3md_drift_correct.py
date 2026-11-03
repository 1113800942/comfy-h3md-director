"""H3MD drift correct (v0.2.0): pull slow segment-to-segment drift (saturation up, sharpness down) back, after the run.

Used by the save nodes (option drift_correct = auto) and by tools/drift_correct.py (finished videos).
Nothing here touches the sampling: it only changes the frames while they are written, so it can be switched off and
the video saved again from the cache.

Decision (a "fingerprint" per segment = mean brightness, contrast, colour (a/b), saturation, sharpness and a small grey
layout thumbnail, measured like tools/drift_report.py on a 360 px wide copy):
  1. GROUPS.  The first segment and every segment after a HARD CUT start a new group (= reference).  Inside a shot a
     continued segment whose brightness, contrast or colour clearly differs from the group's reference (day -> night,
     indoors -> outdoors) is a SCENE change: it starts a new group.  Drift does not do that (3-minute take: brightness
     within 0.7 %, contrast 1 %, colour shift < 1 over 14 segments).
  2. TREND.  Inside a group of >= `min_shot` segments, saturation and sharpness (log ratio vs the reference) are fitted
     with a straight line through the reference: drift creeps a little every segment, so it is a clean trend
     (3-minute take: +0.65 % saturation per segment, residual < 1 %).  Moving content (a dancer, someone walking in)
     makes the values jump around instead.  A metric is corrected only when its trend is clear (>= 2 % by the end of
     the group) and the segments follow it (residual <= max(1.5 %, 35 % of the trend)); otherwise it is left alone.
  3. CORRECTION.  Only the drift is removed: the measured values smoothed over neighbouring segments (so a one-segment
     jump from the content is not "fixed", and drift that levels off is not over-corrected).  Colour: one gain per segment (scaled around each pixel's own brightness and the segment's
     average colour cast, so tint and brightness stay).  Sharpness: a light unsharp mask on brightness only (capped).
     Both are solved on a few sample frames.  Across a continued seam the change eases over 2 s (raised cosine); a hard
     cut keeps its cut.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import torch
import torch.nn.functional as F

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Drift]"

MODES = ["off", "auto"]
MEASURE_W = 360
SAMPLES = 6
EASE_S = 2.0
SIGMA = 1.2
MAX_AMOUNT = 0.8
GAIN_RANGE = (0.8, 1.2)
THUMB = (16, 9)          # layout thumbnail (w, h) - transposed for portrait
# scene-change thresholds (relative to the group's reference): lighting changes, not content
LIMITS = {"brightness": 0.05, "contrast": 0.10, "colour": 2.5}
TREND_MIN = 0.03          # a trend smaller than 3 % by the end of the group is not worth correcting
TREND_FIT = (0.015, 0.35)  # residual allowed: max(1.5 %, 35 % of the trend)
MAX_CORR = {"saturation": 0.15, "sharpness": 0.35}   # never remove more than this
_LUMA = torch.tensor([0.299, 0.587, 0.114])


# --------------------------------------------------------------------------- measuring (same as drift_report)
def _lab_like(rgb):
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return 116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])


def _saturation(rgb):
    c = rgb.astype(np.float32)
    mx, mn = c.max(-1), c.min(-1)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0) * 100


def _sharpness(gray):
    lap = (-4 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(lap.var())


def to_u8(frame) -> np.ndarray:
    """[H,W,3] float 0..1 tensor / uint8 numpy -> uint8 numpy."""
    if isinstance(frame, np.ndarray) and frame.dtype == np.uint8:
        return frame
    t = frame if isinstance(frame, torch.Tensor) else torch.from_numpy(np.asarray(frame))
    return (t[..., :3].float().clamp(0, 1) * 255).round().to(torch.uint8).cpu().numpy()


def measure_frame(frame) -> dict:
    """One frame -> brightness, contrast, a, b, saturation, sharpness + grey layout thumbnail."""
    rgb = to_u8(frame)
    t = torch.from_numpy(rgb).permute(2, 0, 1)[None].float()
    h = max(2, round(rgb.shape[0] * MEASURE_W / rgb.shape[1] / 2) * 2)
    s = F.interpolate(t, size=(h, MEASURE_W), mode="area")[0].permute(1, 2, 0).clamp(0, 255).round().byte().numpy()
    L, a, b = _lab_like(s)
    tw, th = THUMB if rgb.shape[1] >= rgb.shape[0] else THUMB[::-1]
    thumb = F.adaptive_avg_pool2d(torch.from_numpy(L)[None, None], (th, tw))[0, 0].numpy()
    return {"brightness": float(L.mean()), "contrast": float(L.std()), "a": float(a.mean()), "b": float(b.mean()),
            "saturation": float(_saturation(s).mean()), "sharpness": _sharpness(L), "thumb": thumb}


def fingerprint(per_frame: list) -> dict:
    keys = ("brightness", "contrast", "a", "b", "saturation", "sharpness")
    fp = {k: float(np.mean([m[k] for m in per_frame])) for k in keys}
    fp["thumb"] = np.mean([m["thumb"] for m in per_frame], axis=0)
    return fp


def _corr(x, y):
    x, y = x - x.mean(), y - y.mean()
    d = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum()) / d if d > 1e-9 else 1.0


def compare(fp: dict, ref: dict) -> dict:
    """Relative differences of a segment vs its reference."""
    return {"brightness": fp["brightness"] / max(ref["brightness"], 1e-6) - 1,
            "contrast": fp["contrast"] / max(ref["contrast"], 1e-6) - 1,
            "colour": math.hypot(fp["a"] - ref["a"], fp["b"] - ref["b"]),
            "layout": _corr(fp["thumb"], ref["thumb"]),
            "saturation": fp["saturation"] / max(ref["saturation"], 1e-6) - 1,
            "sharpness": fp["sharpness"] / max(ref["sharpness"], 1e-6) - 1}


def scene_reasons(d: dict, limits=LIMITS) -> list:
    r = []
    if abs(d["brightness"]) > limits["brightness"]:
        r.append(f"brightness {d['brightness'] * 100:+.1f} %")
    if abs(d["contrast"]) > limits["contrast"]:
        r.append(f"contrast {d['contrast'] * 100:+.1f} %")
    if d["colour"] > limits["colour"]:
        r.append(f"colour shift {d['colour']:.2f}")
    return r


def fit_trend(values):
    """values = log ratios vs the reference for segments 1..m of a group (the reference itself is 0 at x = 0).
    -> (slope per segment, residual std, trend at the end, clear?)"""
    y = np.asarray(values, np.float64)
    x = np.arange(1, len(y) + 1, dtype=np.float64)
    b = float((x * y).sum() / (x * x).sum())
    resid = float(np.sqrt(np.mean((y - b * x) ** 2)))
    end = abs(b * len(y))
    ok = end >= TREND_MIN and resid <= max(TREND_FIT[0], TREND_FIT[1] * end)
    return b, resid, end, ok


def smooth(ys):
    """3-point moving average (2 points at the ends): keeps the shape of the drift, drops one-segment jumps."""
    y = np.asarray(ys, np.float64)
    if len(y) < 3:
        return y
    out = np.empty_like(y)
    out[0] = (y[0] + y[1]) / 2
    out[-1] = (y[-2] + y[-1]) / 2
    out[1:-1] = (y[:-2] + y[1:-1] + y[2:]) / 3
    return out


def decide(fps: list, cut: list, min_shot: int = 3, limits=LIMITS) -> list:
    """fps: fingerprint per segment; cut[i] True = segment i starts a new shot (first segment / hard cut).
    -> per segment {"role": ref|scene|drift|steady|short, "ref", "shot", "diff", "trend": {metric: {...}},
                    "target": {metric: log ratio to remove}, "reasons"}"""
    shot, shots = -1, []
    for i, c in enumerate(cut):
        shot += 1 if (i == 0 or c) else 0
        shots.append(shot)
    # 1. groups: hard cuts and lighting changes start a new reference
    out, ref = [], 0
    for i, fp in enumerate(fps):
        if i == 0 or cut[i]:
            ref = i
            out.append({"role": "ref", "ref": i, "shot": shots[i], "reasons": ["hard cut" if i else "first segment"]})
            continue
        d = compare(fp, fps[ref])
        why = scene_reasons(d, limits)
        if why:
            ref = i
            out.append({"role": "scene", "ref": i, "shot": shots[i], "diff": {k: round(v, 4) for k, v in d.items()},
                        "reasons": why})
        else:
            out.append({"role": "member", "ref": ref, "shot": shots[i], "diff": {k: round(v, 4) for k, v in d.items()},
                        "reasons": []})
    # 2. trend per group and metric
    groups = {}
    for i, o in enumerate(out):
        groups.setdefault(o["ref"], []).append(i)
    for r, idx in groups.items():
        members = [i for i in idx if i != r]
        if len(idx) < min_shot:
            for i in members:
                out[i]["role"] = "short"
                out[i]["reasons"] = [f"{len(idx)} segment(s) with this reference < {min_shot}"]
            continue
        trends, targets = {}, {i: {} for i in members}
        for m in ("saturation", "sharpness"):
            ys = [math.log(max(fps[i][m], 1e-6) / max(fps[r][m], 1e-6)) for i in members]
            b, resid, end, ok = fit_trend(ys)
            if m == "sharpness" and b > 0:
                ok = False            # never blur: only a loss of sharpness is corrected
            trends[m] = {"per_segment_pct": round(b * 100, 2), "residual_pct": round(resid * 100, 2),
                         "end_pct": round(end * 100, 1), "corrected": bool(ok)}
            if ok:
                # follow the measured drift, smoothed over neighbours (drift can level off; a straight line would
                # over-correct the last segments), never past the fitted line's sign
                sm = smooth(ys)
                for k, i in enumerate(members):
                    v = sm[k] if sm[k] * b > 0 else 0.0
                    targets[i][m] = float(np.clip(v, -MAX_CORR[m], MAX_CORR[m]))
        for i in members:
            out[i]["trend"] = trends
            out[i]["target"] = targets[i]
            out[i]["role"] = "drift" if targets[i] else "steady"
            if not targets[i]:
                out[i]["reasons"] = ["no clear drift trend (values move with the content)"]
    return out


# --------------------------------------------------------------------------- correcting
def _kernel():
    r = int(math.ceil(3 * SIGMA))
    x = torch.arange(-r, r + 1, dtype=torch.float32)
    k = torch.exp(-x * x / (2 * SIGMA * SIGMA))
    return k / k.sum()


_K = _kernel()


def chroma_mean(frames) -> torch.Tensor:
    t = torch.stack([torch.from_numpy(to_u8(f)).float() / 255.0 for f in frames])
    y = (t * _LUMA).sum(-1, keepdim=True)
    return (t - y).mean(dim=(0, 1, 2))


def apply(frames: torch.Tensor, gain, amount, cast) -> torch.Tensor:
    """frames float [N,H,W,C] 0..1 (C >= 3); gain / amount / cast: scalars or per-frame tensors [N] / [N,3]."""
    x = frames[..., :3].float()
    N = x.shape[0]
    g = torch.as_tensor(gain, dtype=torch.float32).reshape(-1)
    amt = torch.as_tensor(amount, dtype=torch.float32).reshape(-1)
    c = torch.as_tensor(cast, dtype=torch.float32).reshape(-1, 3)
    g = (g if g.numel() == N else g.expand(N)).view(N, 1, 1, 1).to(x.device)
    amt = (amt if amt.numel() == N else amt.expand(N)).view(N, 1, 1, 1).to(x.device)
    c = (c if c.shape[0] == N else c.expand(N, 3)).view(N, 1, 1, 3).to(x.device)
    y = (x * _LUMA.to(x.device)).sum(-1, keepdim=True)
    out = y + (x - y - c) * g + c
    if bool((amt > 0).any()):
        r = (len(_K) - 1) // 2
        k = _K.to(x.device)
        yy = y.permute(0, 3, 1, 2)
        bl = F.conv2d(F.pad(yy, (r, r, 0, 0), mode="replicate"), k.view(1, 1, 1, -1))
        bl = F.conv2d(F.pad(bl, (0, 0, r, r), mode="replicate"), k.view(1, 1, -1, 1))
        out = out + (yy - bl).permute(0, 2, 3, 1) * amt
    out = out.clamp(0, 1)
    if frames.shape[-1] > 3:
        out = torch.cat([out, frames[..., 3:].float()], -1)
    return out.to(frames.dtype) if frames.is_floating_point() else out


def _bisect(fn, target, lo, hi, n=12):
    for _ in range(n):
        mid = (lo + hi) / 2
        if fn(mid) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def solve(samples: list, target: dict, strength: float = 1.0, sharpen: bool = True):
    """target = {metric: log ratio of the drift trend at this segment}.  -> (gain, amount, cast) that remove it."""
    cast = chroma_mean(samples)
    ts = [torch.from_numpy(to_u8(f)).float()[None] / 255.0 for f in samples]

    def meas(g, a):
        ms = [measure_frame(apply(t, g, a, cast)[0]) for t in ts]
        return float(np.mean([m["saturation"] for m in ms])), float(np.mean([m["sharpness"] for m in ms]))
    s0, k0 = meas(1.0, 0.0)
    g, a = 1.0, 0.0
    if "saturation" in target:
        want = s0 * math.exp(-target["saturation"] * strength)
        g = _bisect(lambda x: meas(x, 0.0)[0], want, *GAIN_RANGE)
        if abs(g - 1) > 2.5 * abs(target["saturation"] * strength) + 0.02:
            # a strongly tinted picture (most of its saturation is the overall tint): keeping the tint would need a
            # big gain on the remaining colours - scale around brightness only instead (a gentle, even change)
            cast = torch.zeros(3)
            g = _bisect(lambda x: meas(x, 0.0)[0], want, *GAIN_RANGE)
    if sharpen and target.get("sharpness", 0.0) < 0:
        k1 = meas(g, 0.0)[1]
        a = _bisect(lambda x: meas(g, x)[1], k1 * math.exp(-target["sharpness"] * strength), 0.0, MAX_AMOUNT, n=10)
    return g, a, cast


class Plan:
    """Per-segment corrections + the per-frame schedule (eased inside a shot)."""

    def __init__(self, blocks, cut, decisions, params, fps):
        self.blocks, self.cut, self.decisions, self.params = blocks, cut, decisions, params
        self.ease = max(1, int(round(EASE_S * fps)))
        self.seg_of = np.zeros(blocks[-1][1] if blocks else 0, np.int32)
        for i, (s, e) in enumerate(blocks):
            self.seg_of[s:e] = i

    @property
    def active(self) -> bool:
        return any(abs(p[0] - 1) > 1e-4 or p[1] > 1e-4 for p in self.params)

    def at(self, k: int):
        i = int(self.seg_of[min(max(k, 0), len(self.seg_of) - 1)])
        g, a, c = self.params[i]
        if i > 0 and not self.cut[i]:                     # continued seam: ease from the previous segment's values
            p = k - self.blocks[i][0]
            e = max(1, min(self.ease, (self.blocks[i][1] - self.blocks[i][0]) // 3))
            if p < e:
                w = 0.5 - 0.5 * math.cos(math.pi * p / e)
                g0, a0, c0 = self.params[i - 1]
                g, a, c = g0 + (g - g0) * w, a0 + (a - a0) * w, c0 + (c - c0) * w
        return g, a, c

    def correct(self, frames: torch.Tensor, start: int) -> torch.Tensor:
        """Correct merged frames [start, start + N)."""
        if not self.active or frames.shape[0] == 0:
            return frames
        vals = [self.at(start + j) for j in range(frames.shape[0])]
        g = torch.tensor([v[0] for v in vals])
        a = torch.tensor([v[1] for v in vals])
        c = torch.stack([torch.as_tensor(v[2], dtype=torch.float32) for v in vals])
        return apply(frames, g, a, c)

    def report(self) -> list:
        rows = []
        for i, (d, (g, a, _)) in enumerate(zip(self.decisions, self.params)):
            row = {"segment": i + 1, "shot": d["shot"] + 1, "role": d["role"], "reference": d["ref"] + 1,
                   "gain": round(float(g), 4), "sharpen": round(float(a), 4)}
            if "diff" in d:
                row["vs_reference"] = {"brightness_pct": round(d["diff"]["brightness"] * 100, 1),
                                       "contrast_pct": round(d["diff"]["contrast"] * 100, 1),
                                       "colour_shift": round(d["diff"]["colour"], 2),
                                       "layout": round(d["diff"]["layout"], 3),
                                       "saturation_pct": round(d["diff"]["saturation"] * 100, 1),
                                       "sharpness_pct": round(d["diff"]["sharpness"] * 100, 1)}
            if d.get("target"):
                row["removed_trend_pct"] = {k: round(v * 100, 1) for k, v in d["target"].items()}
            if d.get("trend"):
                row["group_trend"] = d["trend"]
            if d["reasons"]:
                row["why"] = "; ".join(d["reasons"])
            rows.append(row)
        return rows


def build(read_segment, blocks, cut, fps=24.0, strength=1.0, sharpen=True, min_shot=3, chunk=16, check=None):
    """Measure every segment and plan the corrections.
    read_segment(a, b) -> float [N,H,W,C] 0..1 merged frames; blocks = [(start, end)] per segment; cut[i] = hard cut.
    -> Plan"""
    fps_list, samples = [], []
    for s, e in blocks:
        per, smp = [], []
        step = max(1, (e - s) // SAMPLES)
        for a in range(s, e, chunk):
            if check is not None:
                check()
            fr = read_segment(a, min(e, a + chunk))
            for j in range(fr.shape[0]):
                per.append(measure_frame(fr[j]))
                if (a + j - s) % step == 0 and len(smp) < SAMPLES:
                    smp.append(to_u8(fr[j]))
            del fr
        fps_list.append(fingerprint(per))
        samples.append(smp)
    dec = decide(fps_list, cut, min_shot)
    params = []
    for i, d in enumerate(dec):
        cast = chroma_mean(samples[i])
        if d["role"] == "drift":
            g, a, cast = solve(samples[i], d["target"], strength, sharpen)
            params.append((g, a, cast))
        else:
            params.append((1.0, 0.0, cast))
    plan = Plan(blocks, cut, dec, params, fps)
    for row in plan.report():
        LOG.info("%s segment %d (shot %d): %s%s", _TAG, row["segment"], row["shot"], row["role"],
                 f" -> colour gain {row['gain']:.3f}, sharpen {row['sharpen']:.3f}" if row["role"] == "drift"
                 else (f" ({row['why']})" if row.get("why") else ""))
    return plan
