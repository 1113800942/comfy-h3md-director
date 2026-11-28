"""H3MD's own VRAM / speed patch nodes for the MiniMax H3 model (0.6.6), so the example workflows open without red
nodes when ComfyUI-KJNodes is not installed.

  H3MD Chunk FeedForward   the same method as KJNodes' 'MiniMax H3 Chunk FeedForward' (Kijai,
                           https://github.com/kijai/ComfyUI-KJNodes, GPL-3.0): the feed-forward of every block runs
                           over the tokens in chunks, which lowers the VRAM peak.  The rows are independent, so the
                           result is the same as without the patch.  Needs nothing installed.
  H3MD Sage Attention      SageAttention on the H3 self-attention (faster, less VRAM).  SageAttention is a compiled
                           package that cannot be shipped inside a node pack, so this node USES it when it is there -
                           through KJNodes' 'MiniMax H3 Mem Eff Sage Attention Patch', which follows that package's
                           versions - and hands the model on unchanged when it is not: no red node, no error, the
                           run only needs more VRAM and time.
"""

from __future__ import annotations

import logging
import sys
import types

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Speed]"
CATEGORY = "MiniMax H3/H3MD"
KJ_SAGE_NODE = "MiniMaxH3MemoryEfficientSageAttentionPatch"
_said = set()


def _say_once(key, level, msg, *args):
    if key in _said:
        return
    _said.add(key)
    LOG.log(level, msg, *args)


# ---------------------------------------------------------------------------------------------- chunk feed-forward
def chunked_mlp_forward(mlp, x, chunks: int, seq_threshold: int):
    """The block's SwiGLU feed-forward over the packed tokens (S, hidden), `chunks` slices at a time."""
    import comfy.ops
    if x.shape[0] > seq_threshold and chunks > 1:
        out = torch.empty_like(x)
        at = 0
        for part in torch.chunk(x, chunks, dim=0):
            out[at:at + part.shape[0]] = comfy.ops.linear_input_act(mlp.fc2, mlp.fc1(part), "swiglu")
            at += part.shape[0]
        return out
    return comfy.ops.linear_input_act(mlp.fc2, mlp.fc1(x), "swiglu")


def _chunk_patch(mlp, chunks: int, seq_threshold: int):
    def forward(self, x, *args, **kwargs):
        return chunked_mlp_forward(self, x, chunks, seq_threshold)
    return types.MethodType(forward, mlp)


def apply_chunk_feedforward(model, chunks: int, seq_threshold: int):
    if int(chunks) <= 1:
        return model
    m = model.clone()
    dm = m.get_model_object("diffusion_model")
    blocks = getattr(dm, "blocks", None)
    if not blocks or not hasattr(blocks[0], "mlp") or not hasattr(blocks[0].mlp, "fc1"):
        LOG.warning("%s Chunk FeedForward: the model does not look like MiniMax H3 (no blocks[*].mlp.fc1 / fc2); "
                    "model unchanged", _TAG)
        return model
    for idx, block in enumerate(blocks):
        m.add_object_patch(f"diffusion_model.blocks.{idx}.mlp.forward",
                           _chunk_patch(block.mlp, int(chunks), int(seq_threshold)))
    return m


class H3MDChunkFeedForward:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "chunks": ("INT", {"default": 2, "min": 1, "max": 64, "step": 1, "tooltip":
                       "Slices the feed-forward runs in. More = lower VRAM peak, slightly slower; 1 = off. "
                       "前馈分几块算：越多显存峰值越低、略慢；1 = 不分。"}),
            "seq_threshold": ("INT", {"default": 4096, "min": 256, "max": 262144, "step": 256, "tooltip":
                              "Only chunk above this many tokens. 超过这么多 token 才分块。"}),
        }}

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Lower VRAM peak for MiniMax H3: the feed-forward of every block runs over the tokens in chunks. "
                   "The result is the same as without it. Built into H3MD (method: KJNodes' MiniMax H3 Chunk "
                   "FeedForward, GPL-3.0). 降低显存峰值，结果不变；已内置，不用装 KJNodes。")

    def run(self, model, chunks, seq_threshold):
        return (apply_chunk_feedforward(model, chunks, seq_threshold),)


# ---------------------------------------------------------------------------------------------- sage attention
def _kj_sage_class():
    import nodes as comfy_nodes
    return comfy_nodes.NODE_CLASS_MAPPINGS.get(KJ_SAGE_NODE)


def _unwrap_model(out):
    for attr in ("result", "args"):
        v = getattr(out, attr, None)
        if isinstance(v, (tuple, list)) and v:
            return v[0]
    if isinstance(out, (tuple, list)) and out:
        return out[0]
    return out


def sage_status():
    """(usable, why_not): can SageAttention be applied on this install?"""
    try:
        cls = _kj_sage_class()
    except Exception as exc:
        return False, f"node registry not readable ({exc})"
    if cls is None:
        return False, ("ComfyUI-KJNodes is not installed (its MiniMax H3 SageAttention patch is what drives the "
                       "sageattention package)")
    return True, None


KJNODES_URL = "https://github.com/kijai/ComfyUI-KJNodes"
SAGE_WINDOWS_URL = "https://github.com/woct0rdho/SageAttention/releases"
SAGE_SOURCE_URL = "https://github.com/thu-ml/SageAttention"


def _sage2_importable() -> bool:
    """SageAttention 2.x is installed (the 1.0.6 on PyPI has no CUDA kernels and is not enough)."""
    try:
        from sageattention.core import get_cuda_arch_versions  # noqa: F401
        return True
    except Exception:
        return False


def install_steps(info: dict) -> dict:
    """What to install on THIS machine to get SageAttention, in Chinese and English ('' when nothing is missing)."""
    py, tv, cv = info.get("python") or "python", info.get("torch") or "?", info.get("cuda") or "?"
    cu = "cu" + cv.replace(".", "") if cv and cv != "?" else "cuXXX"
    zh, en = [], []
    if not info.get("kjnodes"):
        zh.append(f"装 ComfyUI-KJNodes：在 ComfyUI Manager 里搜 KJNodes 安装（{KJNODES_URL}）。")
        en.append(f"Install ComfyUI-KJNodes with ComfyUI Manager ({KJNODES_URL}).")
    if not info.get("sageattention"):
        if info.get("windows"):
            zh.append(f"装 sageattention 2.x（不要直接 pip install sageattention，那样装到的是旧的 1.0.6，用不了）："
                      f"先运行  \"{py}\" -m pip install triton-windows  ；再到 {SAGE_WINDOWS_URL} 下载文件名里带 {cu} 、"
                      f"并且标的 torch 版本不高于你的 {tv} 的那个 .whl，运行  \"{py}\" -m pip install 下载的文件.whl 。")
            en.append(f"Install sageattention 2.x (plain 'pip install sageattention' gives the old 1.0.6, which does not "
                      f"work here): run  \"{py}\" -m pip install triton-windows  ; then download from {SAGE_WINDOWS_URL} "
                      f"the .whl whose name has {cu} and a torch version not above your {tv}, and run  "
                      f"\"{py}\" -m pip install <that file>.whl .")
        else:
            zh.append(f"装 sageattention 2.x（不要直接 pip install sageattention，那是旧的 1.0.6）：按 {SAGE_SOURCE_URL} 的"
                      f"说明从源码安装，或装和你的 PyTorch {tv} / CUDA {cv} 对应的预编译包；用这个 Python 装：{py}")
            en.append(f"Install sageattention 2.x (not the 1.0.6 on PyPI): build it from {SAGE_SOURCE_URL}, or use a "
                      f"prebuilt wheel for PyTorch {tv} / CUDA {cv}; install it with this Python: {py}")
    if zh:
        zh.append("装完重启 ComfyUI。不装也能出片，只是更慢、更吃显存。")
        en.append("Restart ComfyUI afterwards. Without it the run still works, only slower and with more VRAM.")
    number = lambda rows: " ".join(f"{i}) {r}" for i, r in enumerate(rows, 1))  # noqa: E731
    return {"zh": number(zh), "en": number(en)}


def sage_info() -> dict:
    """What the editor shows about SageAttention: is it used on this install, and if not, what is missing and how to
    install it here.  'active' follows the very checks KJNodes' patch node makes before it runs."""
    info = {"kjnodes": False, "sageattention": False, "h3_kernel": True, "active": False,
            "windows": sys.platform.startswith("win"), "python": sys.executable or "python",
            "torch": str(torch.__version__).split("+")[0], "cuda": getattr(torch.version, "cuda", None) or ""}
    try:
        cls = _kj_sage_class()
    except Exception:
        cls = None
    info["kjnodes"] = cls is not None
    kj_mod = sys.modules.get(getattr(cls, "__module__", "")) if cls is not None else None
    if kj_mod is not None and hasattr(kj_mod, "_cuda_archs"):
        info["sageattention"] = getattr(kj_mod, "_cuda_archs", None) is not None
        info["h3_kernel"] = getattr(kj_mod, "_ck", True) is not None
    else:
        info["sageattention"] = _sage2_importable()
    info["active"] = bool(info["kjnodes"] and info["sageattention"] and info["h3_kernel"])
    missing = [n for n, ok in (("ComfyUI-KJNodes", info["kjnodes"]), ("sageattention 2.x", info["sageattention"]))
               if not ok]
    info["missing"] = missing
    steps = install_steps(info)
    info["how_zh"], info["how_en"] = steps["zh"], steps["en"]
    if info["active"]:
        info["status_zh"], info["status_en"] = "已加速（SageAttention）", "SageAttention on"
    else:
        what = "、".join(missing) or "ComfyUI 版本不支持"
        info["status_zh"] = f"未加速：没装 {what}（照常能跑，更慢、更吃显存）"
        info["status_en"] = f"SageAttention off: {', '.join(missing) or 'not supported by this ComfyUI'} not installed"
    return info


def register_routes() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return False

    @routes.get("/h3md/speed/sage_status")
    async def h3md_sage_status(request):
        return web.json_response(sage_info())

    return True


try:
    register_routes()
except Exception as _exc:  # never blocks node loading
    LOG.warning("%s routes not registered: %s", _TAG, _exc)


def apply_sage(model):
    """The model with SageAttention on its H3 self-attention, or the model unchanged when that is not possible here
    (KJNodes or the sageattention package missing or too old) - never an error."""
    ok, why = sage_status()
    if not ok:
        _say_once("no-kj", logging.INFO, "%s Sage Attention off: %s. The run goes on with ComfyUI's own attention "
                  "(more VRAM, slower). To use it: install ComfyUI-KJNodes and the sageattention package. "
                  "没装 KJNodes / sageattention：不加速，照常运行（更吃显存、更慢）。", _TAG, why)
        return model
    try:
        patched = _unwrap_model(_kj_sage_class().execute(model))
        if patched is None:
            raise RuntimeError("the patch returned nothing")
        _say_once("on", logging.INFO, "%s Sage Attention on (KJNodes MiniMax H3 patch + sageattention)", _TAG)
        return patched
    except Exception as exc:
        _say_once("kj-fail:" + type(exc).__name__, logging.WARNING, "%s Sage Attention off: %s. The run goes on with "
                  "ComfyUI's own attention (more VRAM, slower). Most often the sageattention package is missing or too "
                  "old. sageattention 没装或版本太旧：不加速，照常运行。", _TAG, exc)
        return model


class H3MDSageAttention:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"model": ("MODEL",)}}

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("SageAttention for MiniMax H3 (faster, less VRAM) when it is available: needs ComfyUI-KJNodes and "
                   "the sageattention package. Without them this node does nothing - no error, the run just needs "
                   "more VRAM and time. 有 KJNodes + sageattention 就加速省显存；没有也不报错，照常运行。")

    def run(self, model):
        return (apply_sage(model),)


NODE_CLASS_MAPPINGS = {"H3MDChunkFeedForward": H3MDChunkFeedForward, "H3MDSageAttention": H3MDSageAttention}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDChunkFeedForward": "H3MD Chunk FeedForward 省显存",
                              "H3MDSageAttention": "H3MD Sage Attention 加速（有就用）"}
