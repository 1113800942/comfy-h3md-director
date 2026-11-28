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
