"""H3MD's built-in RIFE frame interpolation (0.6.3) - no other node pack needed.

  network   IFNet_HDv3.py, from hzwer's official RIFE v4.26 model archive (MIT, see LICENSE_RIFE.txt)
  weights   rife_v4.26.safetensors: flownet.pkl of the same archive (MIT) converted to safetensors - the same
            tensors, no pickle file in the package - shipped in this folder
  the rest  (loader, batch inference, warp on the tensor's own device) is H3MD's own code

Until 0.6.2 H3MD borrowed the RIFE model of the ComfyUI-VFI pack at run time; a missing pack only showed when the
video was being saved, after all the sampling.  The weights are the same tensors (that pack downloads the same
flownet.pkl), so the frames are the same (checked bit-identical on GPU fp16).
"""

from __future__ import annotations

import hashlib
import logging
import os

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD RIFE]"
HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "flownet.pkl"            # the name in the save nodes' model_name list (kept: saved workflows hold it)
MODEL_VERSION = "RIFE v4.26"
WEIGHTS_FILE = "rife_v4.26.safetensors"
WEIGHTS_SHA256 = "aadde4f1fdbe1d5b974928960fe272e2fd02524ee06511dc452a6f06a9fd5588"
WEIGHTS_BYTES = 24576716
SOURCE_SHA256 = "45c7f74156704769dc9f85cfcaf8552e1e926f9399dcfa3a553dee88fac6f53f"   # the official flownet.pkl
_CACHE = {}


def weights_path() -> str:
    return os.path.join(HERE, WEIGHTS_FILE)


def model_names():
    return [MODEL_NAME]


def problem():
    """None when interpolation can run, otherwise one sentence saying what is wrong (shown before a run starts)."""
    p = weights_path()
    if not os.path.isfile(p):
        return (f"the RIFE weights file is missing: {p} . It ships with H3MD; reinstall / update "
                f"ComfyUI-H3MD-Director, or turn interpolate off. 插帧权重文件丢失，请重新安装 H3MD，或关闭插帧。")
    size = os.path.getsize(p)
    if size != WEIGHTS_BYTES:
        return (f"the RIFE weights file is incomplete ({size} of {WEIGHTS_BYTES} bytes): {p} . Reinstall / update "
                f"ComfyUI-H3MD-Director, or turn interpolate off. 插帧权重文件不完整，请重新安装 H3MD，或关闭插帧。")
    return None


def file_sha256(path=None) -> str:
    h = hashlib.sha256()
    with open(path or weights_path(), "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class _Model:
    """What the save code calls: .flownet (the IFNet) and .inference_batch()."""

    def __init__(self, flownet):
        self.flownet = flownet

    def inference_batch(self, batch_img0, batch_img1, timesteps, scale=1.0):
        """[n,3,H,W] + [n,3,H,W] + n time values in (0,1) -> [n,3,H,W]; one pair at a time, like one-pass RIFE."""
        pairs = torch.cat((batch_img0, batch_img1), 1)
        scales = [16 / scale, 8 / scale, 4 / scale, 2 / scale, 1 / scale]
        t = torch.as_tensor(timesteps, device=pairs.device, dtype=pairs.dtype).reshape(-1)
        out = []
        for i in range(pairs.shape[0]):
            _flow, _mask, merged = self.flownet(pairs[i:i + 1], t[i], scales)
            out.append(merged[-1])
            del _flow, _mask, merged
        return torch.cat(out, 0)


class Interpolator:
    def __init__(self, device, use_fp16):
        from .IFNet_HDv3 import IFNet
        why = problem()
        if why:
            raise RuntimeError(f"{_TAG} {why}")
        self.device = torch.device(device)
        self.use_fp16 = bool(use_fp16) and self.device.type == "cuda"
        from safetensors.torch import load_file      # a ComfyUI requirement, always there
        state = load_file(weights_path(), device="cpu")
        net = IFNet()
        missing, unexpected = net.load_state_dict(state, strict=False)
        if missing:
            raise RuntimeError(f"{_TAG} {WEIGHTS_FILE} does not match the built-in network "
                               f"({len(missing)} missing keys, e.g. {missing[:3]})")
        if unexpected:
            LOG.debug("%s %d unused keys in %s (training-only parts)", _TAG, len(unexpected), WEIGHTS_FILE)
        net.train(False).requires_grad_(False).to(self.device)
        if self.use_fp16:
            net.half()
        self.model = _Model(net)


def default_device():
    """ComfyUI's own device choice (follows --cpu etc.); CUDA / CPU when ComfyUI is not there."""
    try:
        import comfy.model_management as mm
        return torch.device(mm.get_torch_device())
    except Exception:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load(use_fp16=True, device=None):
    """The interpolator for this device / precision (kept in memory: the network is ~25 MB)."""
    if device is None:
        device = default_device()
    device = torch.device(device)
    key = (str(device), bool(use_fp16) and device.type == "cuda")
    it = _CACHE.get(key)
    if it is None:
        it = Interpolator(device, use_fp16)
        _CACHE[key] = it
        LOG.info("%s built-in %s loaded on %s%s", _TAG, MODEL_VERSION, device, " (fp16)" if it.use_fp16 else "")
    else:   # the CPU fallback in h3md_stream_save may have moved it; put it back
        want = torch.float16 if it.use_fp16 else torch.float32
        it.model.flownet.to(device=it.device, dtype=want)
    return it


def unload():
    _CACHE.clear()
    try:
        from .warplayer import clear_grids
        clear_grids()
    except Exception:
        pass
