"""v0.3.3: the Loop's semantic_bridge input also accepts T8's bridge config / multi-bridge stack.

T8 objects are imitated with plain dataclasses carrying the same public fields (no T8 code is imported).
The converted chain must give exactly the result of the H3MD bridge node for the same file and strength.
"""
import dataclasses
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import h3_harness  # noqa: E402,F401
import torch  # noqa: E402
from safetensors.torch import save_file  # noqa: E402

import h3md_semantic_bridge as SB  # noqa: E402


@dataclasses.dataclass(frozen=True)
class FakeT8Config:            # same public fields as T8's BridgeConfig
    path: str
    sha256: str = ""
    alpha: float = 0.10
    magnitude_match: str = "per_token"
    token_scope: str = "all_tokens"
    device: str = "auto"
    chunk_tokens: int = 256
    enabled: bool = True


@dataclasses.dataclass(frozen=True)
class FakeT8Stack:             # same public fields as T8's BridgeStack
    bridges: tuple
    enabled: bool = True


def _bridge_file(folder, name, seed, hid=32):
    g = torch.Generator().manual_seed(seed)
    t = {"fc1.weight": torch.randn(hid, SB.DIM, generator=g) * 0.02, "fc1.bias": torch.zeros(hid),
         "fc2.weight": torch.randn(hid, hid, generator=g) * 0.1, "fc2.bias": torch.zeros(hid),
         "fc3.weight": torch.randn(SB.DIM, hid, generator=g) * 0.1, "fc3.bias": torch.zeros(SB.DIM)}
    p = os.path.join(folder, name)
    save_file(t, p)
    return p


def _cond(seed=0):
    g = torch.Generator().manual_seed(seed)
    return [[torch.randn(1, 7, SB.DIM, generator=g), {"pooled_output": None}]]


def test_single_t8_config_equals_h3md_stage():
    with tempfile.TemporaryDirectory() as d:
        p = _bridge_file(d, "a.safetensors", 1)
        ours = SB.BridgeChain([SB.stage_from_path("a", p, 0.12, "manual")])
        t8 = SB.to_chain(FakeT8Config(p, SB.file_sha(p), 0.12))
        assert len(t8) == 1 and t8[0]["alpha"] == 0.12 and t8[0]["sha256"] == ours[0]["sha256"]
        a, _ = SB.apply_chain(_cond(), ours, "t")
        b, _ = SB.apply_chain(_cond(), t8, "t")
        assert torch.equal(a[0][0], b[0][0])
        assert t8.identity() == ours.identity()          # same cache key as the H3MD node


def test_stack_keeps_order_and_skips_disabled():
    with tempfile.TemporaryDirectory() as d:
        p1, p2, p3 = (_bridge_file(d, f"{n}.safetensors", s) for n, s in (("a", 1), ("b", 2), ("c", 3)))
        stack = FakeT8Stack((FakeT8Config(p2, alpha=0.1), FakeT8Config(p3, enabled=False),
                             FakeT8Config(p1, alpha=0.2)))
        ch = SB.to_chain(stack)
        assert [s["path"] for s in ch] == [p2, p1] and [s["alpha"] for s in ch] == [0.1, 0.2]
        hook = SB.BridgeHook(stack)
        assert hook.active and len(hook.chain) == 2
        assert not SB.BridgeHook(FakeT8Stack((FakeT8Config(p1),), enabled=False)).active
        assert not SB.BridgeHook(FakeT8Config(p1, alpha=0.0)).active


def test_errors_and_h3md_chain_unchanged():
    with tempfile.TemporaryDirectory() as d:
        p = _bridge_file(d, "a.safetensors", 1)
        for bad in (FakeT8Config(p, sha256="0" * 64), FakeT8Config(os.path.join(d, "missing.safetensors")),
                    FakeT8Stack((FakeT8Config(p), FakeT8Config(p))), object()):
            try:
                SB.to_chain(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"accepted {bad}")
        ours = SB.BridgeChain([SB.stage_from_path("a", p, 0.1, "manual")])
        assert SB.to_chain(ours) == ours and not SB.BridgeHook(None).active
        assert SB.LOOP_INPUTS["semantic_bridge"][0] == "H3MD_SEMANTIC_BRIDGE,T8_SEMANTIC_BRIDGE"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
