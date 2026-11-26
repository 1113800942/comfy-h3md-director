"""Backward warp for RIFE's IFNet (H3MD's own version of Practical-RIFE's model/warplayer.py, MIT, (c) 2021 hzwer).

Same maths as upstream (bilinear grid_sample, border padding, align_corners=True).  Difference: the sampling grid is
built on the flow's own device instead of a module-level `device`, so the same network can run on the GPU or - in
H3MD's out-of-memory fallback - on the CPU without switching a global.
"""

import torch
import torch.nn.functional as F

_GRIDS = {}
_MAX_GRIDS = 8


def warp(tenInput, tenFlow):
    key = (str(tenFlow.device), tuple(tenFlow.shape))
    grid = _GRIDS.get(key)
    if grid is None:
        n, _c, h, w = tenFlow.shape
        xs = torch.linspace(-1.0, 1.0, w, device=tenFlow.device).view(1, 1, 1, w).expand(n, -1, h, -1)
        ys = torch.linspace(-1.0, 1.0, h, device=tenFlow.device).view(1, 1, h, 1).expand(n, -1, -1, w)
        grid = torch.cat([xs, ys], 1)
        if len(_GRIDS) >= _MAX_GRIDS:
            _GRIDS.clear()
        _GRIDS[key] = grid
    flow = torch.cat([tenFlow[:, 0:1] / ((tenInput.shape[3] - 1.0) / 2.0),
                      tenFlow[:, 1:2] / ((tenInput.shape[2] - 1.0) / 2.0)], 1)
    g = (grid + flow).permute(0, 2, 3, 1)
    if g.dtype != tenInput.dtype and not torch.is_autocast_enabled():
        g = g.to(tenInput.dtype)
    return F.grid_sample(input=tenInput, grid=g, mode="bilinear", padding_mode="border", align_corners=True)


def clear_grids():
    _GRIDS.clear()
