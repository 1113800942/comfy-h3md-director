"""0.6.6: H3MD's own Chunk FeedForward (exact) and Sage Attention (used when available, never an error)."""
import os
import sys

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import comfy.ops  # noqa: E402
import h3md_speed_patches as SP  # noqa: E402


class _Mlp(torch.nn.Module):
    def __init__(self, hidden=16, inner=24):
        super().__init__()
        ops = comfy.ops.disable_weight_init
        self.fc1 = ops.Linear(hidden, inner * 2)
        self.fc2 = ops.Linear(inner, hidden)
        torch.manual_seed(3)
        for p in self.parameters():
            torch.nn.init.normal_(p, std=0.2)


def test_chunked_feedforward_gives_the_same_result():
    mlp, x = _Mlp(), torch.randn(37, 16)
    whole = SP.chunked_mlp_forward(mlp, x, 1, 8)
    for chunks in (2, 3, 5, 64):
        got = SP.chunked_mlp_forward(mlp, x, chunks, 8)
        assert got.shape == whole.shape and torch.allclose(got, whole, atol=1e-6), chunks
    assert torch.equal(SP.chunked_mlp_forward(mlp, x, 4, 4096), whole)      # under the threshold: not chunked


class _Patcher:
    def __init__(self, dm):
        self.dm, self.patches = dm, {}

    def clone(self):
        c = _Patcher(self.dm)
        c.patches = dict(self.patches)
        return c

    def get_model_object(self, name):
        assert name == "diffusion_model"
        return self.dm

    def add_object_patch(self, key, obj):
        self.patches[key] = obj


def test_chunk_node_patches_every_block_and_leaves_the_input_model_alone():
    dm = torch.nn.Module()
    dm.blocks = torch.nn.ModuleList()
    for _ in range(3):
        b = torch.nn.Module()
        b.mlp = _Mlp()
        dm.blocks.append(b)
    model = _Patcher(dm)
    out = SP.H3MDChunkFeedForward().run(model, 3, 8)[0]
    assert out is not model and not model.patches
    assert sorted(out.patches) == [f"diffusion_model.blocks.{i}.mlp.forward" for i in range(3)]
    x = torch.randn(20, 16)
    assert torch.allclose(out.patches["diffusion_model.blocks.1.mlp.forward"](x),
                          SP.chunked_mlp_forward(dm.blocks[1].mlp, x, 1, 8), atol=1e-6)
    assert SP.H3MDChunkFeedForward().run(model, 1, 8)[0] is model            # chunks 1 = off
    other = _Patcher(torch.nn.Module())                                         # not an H3 model: unchanged
    assert SP.H3MDChunkFeedForward().run(other, 3, 8)[0] is other
    spec = SP.H3MDChunkFeedForward.INPUT_TYPES()["required"]
    assert list(spec) == ["model", "chunks", "seq_threshold"]                   # KJNodes' order: values carry over
    assert spec["chunks"][1]["default"] == 2 and spec["seq_threshold"][1]["default"] == 4096


def test_sage_attention_is_used_when_there_and_skipped_without_error(monkeypatch):
    model, patched = object(), object()
    monkeypatch.setattr(SP, "_kj_sage_class", lambda: None)                     # KJNodes not installed
    assert SP.H3MDSageAttention().run(model)[0] is model

    class Out:
        def __init__(self, v):
            self.result = (v,)

    class Kj:
        @classmethod
        def execute(cls, m):
            assert m is model
            return Out(patched)

    monkeypatch.setattr(SP, "_kj_sage_class", lambda: Kj)
    assert SP.H3MDSageAttention().run(model)[0] is patched

    class KjNoSage:
        @classmethod
        def execute(cls, m):
            raise RuntimeError("sageattention is not new enough version")

    monkeypatch.setattr(SP, "_kj_sage_class", lambda: KjNoSage)                 # the package missing / too old
    assert SP.H3MDSageAttention().run(model)[0] is model
    assert set(SP.NODE_CLASS_MAPPINGS) == {"H3MDChunkFeedForward", "H3MDSageAttention"}
