"""H3MD feed-forward row chunking (own code, 2026-10-29) - lower peak VRAM for one model evaluation.

The H3 DiT feed-forward (comfy.ldm.minimax.model.MLP: fc1 -> swiglu -> fc2) materialises a [rows, 2 x ffn] tensor
for the whole packed sequence at once; on long / large segments that is one of the largest single allocations of a
step.  With chunking the MLP runs on `rows` sequence rows at a time and writes into one output tensor.  Every row is
computed exactly as before (the MLP has no interaction between rows), only the peak memory changes.

Used by the H3MD Sampler (input ff_chunk_rows, both passes) and by the high-res tiling's out-of-memory retry (as the
last step after the most strips).  It replaces the selflift-Avatar highres_token_chunk option, which never took
effect in H3MD (that code was removed together with selflift-Avatar).
"""

from __future__ import annotations

import contextlib
import logging
import threading

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD FF chunk]"
WRAPPER_KEY = "h3md_ff_chunk"
OOM_RETRY_ROWS = 8192   # rows used by the tiling OOM retry
_LOCK = threading.RLock()
_ACTIVE = []            # stack of row limits (nested wrappers: the smallest wins)


def _mlp_class():
    import comfy.ldm.minimax.model as MM
    return MM.MLP


def chunked_forward(orig, rows: int):
    def forward(self, x):
        n = int(x.shape[-2]) if x.ndim >= 2 else 0
        if rows <= 0 or n <= rows:
            return orig(self, x)
        first = orig(self, x.narrow(-2, 0, rows))
        out = torch.empty(tuple(x.shape[:-2]) + (n, first.shape[-1]), dtype=first.dtype, device=first.device)
        out.narrow(-2, 0, rows).copy_(first)
        del first
        for i in range(rows, n, rows):
            k = min(rows, n - i)
            out.narrow(-2, i, k).copy_(orig(self, x.narrow(-2, i, k)))
        return out
    forward.h3md_chunked = True
    return forward


@contextlib.contextmanager
def chunked_mlp(rows: int):
    """While active, every H3 MLP forward runs on at most `rows` sequence rows at a time."""
    rows = int(rows or 0)
    if rows <= 0:
        yield
        return
    cls = _mlp_class()
    with _LOCK:
        if not _ACTIVE:
            _ACTIVE.append(cls.forward)          # bottom of the stack = the original forward
        orig = _ACTIVE[0]
        _ACTIVE.append(rows)
        cls.forward = chunked_forward(orig, min(r for r in _ACTIVE[1:]))
    try:
        yield
    finally:
        with _LOCK:
            _ACTIVE.remove(rows)
            if len(_ACTIVE) == 1:
                cls.forward = _ACTIVE.pop()
            else:
                cls.forward = chunked_forward(_ACTIVE[0], min(r for r in _ACTIVE[1:]))


def with_ff_chunk(model, rows: int):
    """Clone of `model` whose diffusion-model calls run with chunked_mlp(rows); `model` itself when rows <= 0."""
    rows = int(rows or 0)
    if model is None or rows <= 0:
        return model
    import comfy.patcher_extension
    m = model.clone()

    def diffusion(executor, *a, **k):
        with chunked_mlp(rows):
            return executor(*a, **k)

    m.add_wrapper_with_key(comfy.patcher_extension.WrappersMP.DIFFUSION_MODEL, WRAPPER_KEY, diffusion)
    return m
