"""0.6.6: H3MD Sigma Refiner gives exactly the sigmas of YCNodes' H3SigmaRefiner (the pack is no longer needed)."""
import math
import os
import sys

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import h3md_sigma_refiner as SR  # noqa: E402


def _reference(sigmas, extra_steps, start_at_sigma, end_at_sigma, spacing):
    """The original node's method, written out again from its description (tail re-spaced with extra points)."""
    if extra_steps <= 0:
        return sigmas
    s = sigmas.detach().cpu()
    hits = [i for i, v in enumerate(s) if v <= start_at_sigma]
    if not hits or hits[0] >= len(s) - 1:
        return sigmas
    idx = hits[0]
    a, b = s[idx].item(), max(end_at_sigma, s[-1].item())
    n = len(s) - idx + extra_steps
    pts = []
    for k in range(n):
        t = torch.linspace(0.0, 1.0, steps=n)[k]
        if spacing == "cosine":
            f = (1.0 - torch.cos(t * math.pi)) / 2.0
        elif spacing == "exponential":
            f = (torch.exp(t * 3.0) - 1.0) / (math.exp(3.0) - 1.0)
        else:
            f = t
        pts.append(a + (b - a) * f)
    tail = torch.stack(pts)
    if s[-1].item() == 0.0 and b > 0.0:
        tail = torch.cat([tail, torch.tensor([0.0])])
    return torch.cat([s[:idx], tail]).to(sigmas.dtype)


SCHEDULES = [
    torch.tensor([1.0, 0.9675, 0.8875, 0.75, 0.5328, 0.232, 0.0]),
    torch.tensor([1.0, 0.95, 0.88, 0.79, 0.68, 0.55, 0.4, 0.22, 0.0]),          # an 8-step schedule
    torch.linspace(1.0, 0.0, 5),
]


def test_same_sigmas_as_the_original_node():
    for s in SCHEDULES:
        for extra in (1, 2, 5):
            for start in (0.7, 0.3, 2.0):
                for end in (0.0, 0.05):
                    for sp in SR.SPACINGS:
                        got = SR.refine(s, extra, start, end, sp)
                        want = _reference(s, extra, start, end, sp)
                        assert got.shape == want.shape and torch.allclose(got, want, atol=1e-7), (extra, start, end, sp)


def test_the_example_workflow_setting_adds_one_step():
    """workflows use extra_steps 1, start 0.7, end 0, cosine: 8 steps -> 9 (the node title says 'total 9 NFE')"""
    s = SCHEDULES[1]
    out = SR.H3MDSigmaRefiner().run(s, 1, 0.7, 0.0, "cosine")[0]
    assert len(out) == len(s) + 1 and out[-1] == 0.0
    assert torch.equal(out[:4], s[:4])                      # sigmas above 0.7 are untouched
    assert all(out[i] > out[i + 1] for i in range(len(out) - 1))
    assert out.dtype == s.dtype


def test_unchanged_cases_and_defaults():
    s = SCHEDULES[0]
    assert SR.refine(s, 0, 0.7) is s                        # no extra steps
    assert SR.refine(s, 2, -1.0) is s                       # nothing at or below the start
    spec = SR.H3MDSigmaRefiner.INPUT_TYPES()["required"]
    assert list(spec) == ["sigmas", "extra_steps", "start_at_sigma", "end_at_sigma", "spacing"]   # the pack's order
    assert [spec[k][1]["default"] for k in list(spec)[1:]] == [1, 0.7, 0.0, "cosine"]
    assert SR.NODE_CLASS_MAPPINGS == {"H3MDSigmaRefiner": SR.H3MDSigmaRefiner}
