"""H3MD low-res anchor (2026-10-30): stop a single-scene long video from drifting segment by segment.

Measured on a 16-segment motion-copy take (cache of H3MD_00068): the colour drift is born in the LOW-RES pass - the
model continuing its own low-res result (native low-res carry) - not in the upscaler or the high-res pass (that one adds a
constant offset per segment, it does not compound).  Preview saturation of the low-res result 0.39 -> 0.58 over 16
segments, the same rise as the finished video.  The drift is a slow per-channel shift of the 24 latent channels' mean
and spread; pulling segment 16's channels back to segment 1's mean / std brings saturation from 0.58 back to 0.40.

So: after each continued segment's low-res pass, before the lift, the NEW latent steps (not the locked head / tail) get
their per-channel mean and std moved toward the reference statistics of the first segment of the shot.  The same
transform is applied to the captured low-res carry, so the next segment continues from the corrected picture and the
error cannot pile up.  `strength` 0..1 blends between the raw result (0) and the full correction (1).

For one continuous scene only (motion copy, a long take).  A story whose colours should change (a new place, night)
must not use it - a hard cut starts a new reference anyway.
"""

from __future__ import annotations

import logging

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Anchor]"


def held_rows(mask_video, T: int) -> torch.Tensor:
    """Bool [T]: latent steps kept exactly (mask 0 everywhere in that step: head prefix, rerun tail lock)."""
    if mask_video is None:
        return torch.zeros(T, dtype=torch.bool)
    m = mask_video
    while m.ndim > 3:
        m = m[0]
    if m.ndim == 3 and m.shape[0] == T:
        return (m.amax(dim=(1, 2)) <= 0).cpu()
    return torch.zeros(T, dtype=torch.bool)


def stats(video: torch.Tensor, rows=None) -> dict:
    """Per-channel mean / std of a [B,C,T,H,W] latent (only `rows` of T when given). CPU float lists."""
    v = video.float()
    if rows is not None:
        v = v[:, :, rows.to(v.device)]
    return {"mean": v.mean(dim=(0, 2, 3, 4)).cpu().tolist(), "std": v.std(dim=(0, 2, 3, 4)).cpu().tolist()}


def transform(video: torch.Tensor, held: torch.Tensor, ref: dict, strength: float):
    """Plan the correction for the new rows of `video` -> (gain [C], shift [C], new-rows bool [T]) or None."""
    if strength <= 0 or not ref:
        return None
    new = ~held.cpu()
    if not bool(new.any()):
        return None
    cur = stats(video, new)
    m, s = torch.tensor(cur["mean"]), torch.tensor(cur["std"])
    m0, s0 = torch.tensor(ref["mean"]), torch.tensor(ref["std"])
    if m.numel() != m0.numel():
        LOG.warning("%s reference has %d channels, latent %d - not applied", _TAG, m0.numel(), m.numel())
        return None
    gain = torch.where(s > 1e-4, s0 / s.clamp_min(1e-6), torch.ones_like(s)).clamp(0.5, 2.0)
    # x' = x + k * ((x - m) * gain + m0 - x) = x * (1 + k(gain - 1)) + k(m0 - m*gain)
    k = float(min(max(strength, 0.0), 1.0))
    return {"gain": 1 + k * (gain - 1), "shift": k * (m0 - m * gain), "rows": new,
            "report": {"strength": round(k, 3), "new_steps": int(new.sum()),
                       "mean_shift": round(float(((m - m0).abs() / s0.clamp_min(1e-6)).mean()), 4),
                       "std_ratio": [round(float((s / s0.clamp_min(1e-6)).min()), 3),
                                     round(float((s / s0.clamp_min(1e-6)).max()), 3)]}}


def apply(video: torch.Tensor, xf) -> torch.Tensor:
    """Apply a planned correction to the same rows of `video` (any device / dtype; returns a new tensor)."""
    if xf is None:
        return video
    rows = xf["rows"].to(video.device)
    if rows.numel() != video.shape[2]:
        LOG.warning("%s carry has %d latent steps, correction planned for %d - not applied", _TAG, video.shape[2],
                    rows.numel())
        return video
    g = xf["gain"].to(video.device, torch.float32).view(1, -1, 1, 1, 1)
    b = xf["shift"].to(video.device, torch.float32).view(1, -1, 1, 1, 1)
    out = video.clone()
    out[:, :, rows] = (video[:, :, rows].float() * g + b).to(video.dtype)
    return out
