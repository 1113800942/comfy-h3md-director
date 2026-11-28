"""H3MD Sigma Refiner (0.6.6): extra denoising steps at the low-noise end of a sigma schedule.

The same computation as 'H3 Sigma Refiner' (H3SigmaRefiner) of ComfyUI-YCNodes-MiniMax-H3 by yc
(https://github.com/yichengup/ComfyUI-YCNodes-MiniMax-H3, MIT License, Copyright (c) 2026 yc), ported so the
example workflows no longer need that pack: same inputs, same defaults, the same sigmas out.

From the first sigma at or below start_at_sigma on, the tail of the schedule is laid out again with extra_steps
more points between that sigma and the end (cosine / linear / exponential spacing).  The H3MD example workflows
use it for one extra refine step after the 8-step schedule ("+1, total 9 NFE").
"""

from __future__ import annotations

import math

import torch

SPACINGS = ["cosine", "linear", "exponential"]
EXP_ALPHA = 3.0


def refine(sigmas: torch.Tensor, extra_steps: int, start_at_sigma: float, end_at_sigma: float = 0.0,
           spacing: str = "cosine") -> torch.Tensor:
    if int(extra_steps) <= 0:
        return sigmas
    s = sigmas.detach().cpu()
    idx = -1
    for i, v in enumerate(s):
        if v <= start_at_sigma:
            idx = i
            break
    if idx == -1 or idx >= len(s) - 1:     # nothing at or below the start, or only the final point: unchanged
        return sigmas
    head = s[:idx]
    a = s[idx].item()
    b = max(float(end_at_sigma), s[-1].item())
    t = torch.linspace(0.0, 1.0, steps=(len(s) - idx) + int(extra_steps))
    if spacing == "cosine":
        factor = (1.0 - torch.cos(t * math.pi)) / 2.0
    elif spacing == "exponential":
        factor = (torch.exp(t * EXP_ALPHA) - 1.0) / (math.exp(EXP_ALPHA) - 1.0)
    else:
        factor = t
    tail = a + (b - a) * factor
    if s[-1].item() == 0.0 and b > 0.0:    # the schedule ended at 0 and the refined tail stops above it: end at 0
        tail = torch.cat([tail, torch.tensor([0.0])])
    return torch.cat([head, tail]).to(device=sigmas.device, dtype=sigmas.dtype)


class H3MDSigmaRefiner:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "sigmas": ("SIGMAS", {"tooltip": "The sigma schedule to refine. 要加细的 sigma 序列。"}),
            "extra_steps": ("INT", {"default": 1, "min": 0, "max": 15, "step": 1, "tooltip":
                            "Steps added at the low-noise end (0 = unchanged). 在低噪声段多加几步（0 = 不改）。"}),
            "start_at_sigma": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 20.0, "step": 0.01, "tooltip":
                               "Refine from the first sigma at or below this. 从第一个不大于这个值的 sigma 开始加细。"}),
            "end_at_sigma": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 5.0, "step": 0.01, "tooltip":
                             "Where the refined part ends (usually 0). 加细到哪个 sigma 为止（一般 0）。"}),
            "spacing": (SPACINGS, {"default": "cosine", "tooltip":
                        "How the refined steps are spaced; cosine puts more of them near the end. 步子的分布方式。"}),
        }}

    RETURN_TYPES = ("SIGMAS",)
    RETURN_NAMES = ("sigmas",)
    FUNCTION = "run"
    CATEGORY = "MiniMax H3/H3MD"
    DESCRIPTION = ("Extra denoising steps at the low-noise end of a sigma schedule (more fine detail). The same "
                   "computation as H3 Sigma Refiner of ComfyUI-YCNodes-MiniMax-H3 (yc, MIT), built into H3MD. "
                   "在采样末段（低噪声段）多加几步，细节更足；算法同 YCNodes 的 H3 Sigma Refiner，已内置。")

    def run(self, sigmas, extra_steps, start_at_sigma, end_at_sigma, spacing):
        return (refine(sigmas, extra_steps, start_at_sigma, end_at_sigma, spacing),)


NODE_CLASS_MAPPINGS = {"H3MDSigmaRefiner": H3MDSigmaRefiner}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDSigmaRefiner": "H3MD Sigma Refiner 末段加步"}
