"""0.6.4: weight patches put on the models by core loaders / other packs (LoRA nodes, Load LakonLab Adapter) are part
of the segment cache key.  Before, resume_from_cache returned the old segment after such an adapter was swapped
(11-26: H3MD_00247 was the cached H3MD_00246).  A model without weight patches keeps its old keys."""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import h3_harness  # noqa: E402
import torch  # noqa: E402
import h3md_cache as C  # noqa: E402
import h3md_loop as L  # noqa: E402


class Fake:
    def __init__(self, patches=None, lora_sig=None):
        self.patches = patches or {}
        self.model_options = {"h3md_lora_sig": lora_sig} if lora_sig else {}


def lora(seed=0, strength=1.0):
    g = torch.Generator().manual_seed(seed)
    up, down = torch.randn(8, 4, generator=g), torch.randn(4, 8, generator=g)
    return [(strength, ("lora", (up, down, 4.0, None, None, None)), 1.0, None, None)]


class PatchSignature(unittest.TestCase):
    def test_no_patches_no_signature(self):
        self.assertIsNone(C.patch_signature(Fake()))
        self.assertIsNone(C.patch_signature(object()))
        self.assertIsNone(C.patch_signature(None))

    def test_same_patches_same_signature(self):
        a = C.patch_signature(Fake({"diffusion_model.blocks.0.attn.qkv_proj.weight": lora(1)}))
        b = C.patch_signature(Fake({"diffusion_model.blocks.0.attn.qkv_proj.weight": lora(1)}))
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)

    def test_other_weights_strength_or_key_change_it(self):
        base = C.patch_signature(Fake({"k.weight": lora(1)}))
        self.assertNotEqual(base, C.patch_signature(Fake({"k.weight": lora(2)})))            # another adapter file
        self.assertNotEqual(base, C.patch_signature(Fake({"k.weight": lora(1, 0.8)})))       # another strength
        self.assertNotEqual(base, C.patch_signature(Fake({"j.weight": lora(1)})))            # another layer
        self.assertNotEqual(base, C.patch_signature(Fake({"k.weight": lora(1) + lora(3)})))  # one more LoRA stacked

    def test_set_patch_of_an_adapter(self):
        # Load LakonLab Adapter replaces whole tensors: ("set", (tensor,))
        w1, w2 = torch.full((16, 16), 0.5), torch.full((16, 16), 0.25)
        a = C.patch_signature(Fake({"k.weight": [(1.0, ("set", (w1,)), 1.0, None, None)]}))
        b = C.patch_signature(Fake({"k.weight": [(1.0, ("set", (w2,)), 1.0, None, None)]}))
        self.assertNotEqual(a, b)

    def test_large_tensor_is_sampled_not_read_whole(self):
        big = torch.zeros(4096, 4096)
        self.assertIsNotNone(C.patch_signature(Fake({"k.weight": [(1.0, ("set", (big,)), 1.0, None, None)]})))
        self.assertLessEqual(C.PATCH_SAMPLES, 256)

    def test_unreadable_patch_value_does_not_raise(self):
        class Odd:
            name = "odd"
            weights = (torch.ones(2, 2), object())
        self.assertIsNotNone(C.patch_signature(Fake({"k.weight": [(1.0, Odd(), 1.0, None, len)]})))

    def test_real_model_patcher(self):
        m = h3_harness.tiny_h3_model()
        self.assertIsNone(C.patch_signature(m))
        key = next(k for k in m.model.state_dict() if k.startswith("diffusion_model.") and k.endswith(".weight"))
        w = m.model.state_dict()[key]
        a, b = m.clone(), m.clone()
        a.add_patches({key: ("set", (torch.zeros_like(w),))}, 1.0)
        b.add_patches({key: ("set", (torch.ones_like(w),))}, 1.0)
        self.assertIsNotNone(C.patch_signature(a))
        self.assertNotEqual(C.patch_signature(a), C.patch_signature(b))
        self.assertIsNone(C.patch_signature(m))   # the clones did not touch the original


class LoopKeys(unittest.TestCase):
    def test_models_without_weight_patches_keep_the_old_signature(self):
        self.assertEqual(L.model_lora_sig(Fake(), Fake()), {})
        sig = [["bypass", "a.safetensors", 1.0, "replace_like_comfy"]]
        self.assertEqual(L.model_lora_sig(Fake(lora_sig=sig), Fake()), {"low": sig})

    def test_weight_patches_enter_the_signature(self):
        low, high = Fake({"k.weight": lora(1)}), Fake({"k.weight": lora(2)})
        out = L.model_lora_sig(low, high)
        self.assertEqual(set(out), {"low_patches", "high_patches"})
        self.assertNotEqual(out["low_patches"], out["high_patches"])
        self.assertNotEqual(L.made_with_sig({"full": "s"}, out), L.made_with_sig({"full": "s"}, {}))
        self.assertNotEqual(L.made_with_sig({"full": "s"}, out),
                            L.made_with_sig({"full": "s"}, L.model_lora_sig(high, high)))

    def test_remake_model_patches(self):
        out = L.model_lora_sig(Fake(), Fake(), Fake({"k.weight": lora(5)}))
        self.assertEqual(set(out), {"remake_patches"})

    def test_low_pass_key(self):
        old = C.segment_key
        C.segment_key = lambda *a, **k: "segkey"
        try:
            plan = {"windowMode": {"length": 72}}
            args = ("settings", plan, 0, 1, 1, False, None, None)
            sig = [["bypass", "a.safetensors", 1.0, "replace_like_comfy"]]
            self.assertEqual(L.low_pass_key(*args, {}), "segkey")
            # only H3MD-loader LoRAs: exactly the 0.6.3 key
            self.assertEqual(L.low_pass_key(*args, {"low": sig}), C._sha({"key": "segkey", "loras": {"low": sig}}))
            # the high-res model's patches do not touch the low-res pass key
            self.assertEqual(L.low_pass_key(*args, {"low": sig, "high_patches": "x"}),
                             L.low_pass_key(*args, {"low": sig}))
            # the low-res model's patches do
            self.assertNotEqual(L.low_pass_key(*args, {"low": sig, "low_patches": "x"}),
                                L.low_pass_key(*args, {"low": sig}))
            self.assertNotEqual(L.low_pass_key(*args, {"low_patches": "x"}), L.low_pass_key(*args, {"low_patches": "y"}))
        finally:
            C.segment_key = old


if __name__ == "__main__":
    unittest.main()
