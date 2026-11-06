"""H3MD LowVRAM LoRA Loader (H3MDLowVRAMLoraLoader) - same code/behaviour as our
H3LowVRAMBypassLoraLoader, renamed for the H3 MultiSeg DualRes package.

Drop-in for ComfyUI's "Load LoRA (Bypass, Model Only)" (LoraLoaderBypassModelOnly,
comfy/sd.py load_bypass_lora_for_models) with the same inputs and the same math:

    out = g(base_forward(x) + h(x))      h = up(down(x)) * strength * alpha / rank

Differences (memory only):
* for plain LoRA adapters on Linear layers, h is computed in sequential token
  slices and added in place into base_out[slice]; no full-size lora-out,
  out*scale or base_out+h temporaries exist. Peak extra VRAM ~ one slice;
* other adapter types (LoHa/LoKr/OFT/GLoRA, conv layers, autograd) fall back to
  ComfyUI's exact unsliced g(base + h) path;
* stack_mode: "replace_like_comfy" reproduces ComfyUI's behaviour (each bypass
  loader overwrites the model's "bypass_lora" injection, so only the last bypass
  LoRA in a chain is active); "stack" keeps every upstream bypass adapter active,
  including ones from ComfyUI's own bypass loader, which are absorbed so only one
  hook ever wraps a module.

Non-adapter patches (diff / diff_b / set ...) go through add_patches exactly as
comfy/sd.py does. Uses the same injection key ("bypass_lora") so ComfyUI treats it
the same way (clone, is_clone checks, inject/eject on load/unload).
"""

from __future__ import annotations

import logging

import torch

import comfy.lora
import comfy.lora_convert
import comfy.model_management
import comfy.utils
import comfy.weight_adapter
import folder_paths
from comfy.patcher_extension import PatcherInjection
from comfy.weight_adapter.bypass import BypassForwardHook, BypassInjectionManager
from comfy.weight_adapter.base import WeightAdapterBase
from comfy.weight_adapter.lora import LoRAAdapter

log = logging.getLogger(__name__)

INJECTION_KEY = "bypass_lora"          # same key as comfy/sd.py so semantics match
STACK_MODES = ["replace_like_comfy", "stack"]
DEFAULT_CHUNK_TOKENS = 8192


class H3MDBypassInjection(PatcherInjection):
    """PatcherInjection that remembers its (module_key, adapter, strength) entries."""

    def __init__(self, entries, chunk_tokens):
        self.h3_entries = list(entries)
        self.h3_chunk_tokens = int(chunk_tokens)
        self._live = {}                 # id(model) -> list of _ModuleHook
        super().__init__(inject=self._inject, eject=self._eject)

    def _inject(self, model_patcher):
        model = model_patcher.model
        if id(model) in self._live:
            return
        grouped = {}
        order = []
        for key, adapter, strength in self.h3_entries:
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append((adapter, strength))
        mgr = BypassInjectionManager()   # reuse its module lookup
        hooks = []
        for key in order:
            module = mgr._get_module_by_key(model, key)
            if module is None or not hasattr(module, "weight"):
                log.warning("[H3MD LowVRAM LoRA] module not found / no weight: %s", key)
                continue
            hook = _ModuleHook(module, grouped[key], self.h3_chunk_tokens)
            hook.inject()
            hooks.append(hook)
        self._live[id(model)] = hooks

    def _eject(self, model_patcher):
        hooks = self._live.pop(id(model_patcher.model), [])
        for hook in reversed(hooks):
            hook.eject()


class _ModuleHook:
    def __init__(self, module, items, chunk_tokens):
        self.module = module
        self.chunk_tokens = max(1, int(chunk_tokens))
        self.items = []
        for adapter, strength in items:
            # comfy's hook ctor sets is_conv / kernel info / multiplier on the adapter
            BypassForwardHook(module, adapter, multiplier=strength)
            self.items.append((adapter, float(strength)))
        self.injected = False

    def inject(self):
        if self.injected:
            return
        module = self.module
        device = comfy.model_management.get_torch_device()
        dtype = getattr(getattr(module, "weight", None), "dtype", None)
        if dtype is not None and dtype not in (torch.float32, torch.float16, torch.bfloat16):
            dtype = None
        for adapter, strength in self.items:     # same placement as comfy BypassForwardHook.inject
            BypassForwardHook(module, adapter, multiplier=strength)._move_adapter_weights_to_device(device, dtype)
        self._had_instance_forward = "forward" in module.__dict__
        self._instance_forward = module.__dict__.get("forward")
        self.original_forward = module.forward
        self.original_force_forward = getattr(module, "comfy_force_forward", False)
        module.comfy_force_forward = True        # comfy bypass does this too (disables fused int8 act path)
        module.forward = self._forward
        self.injected = True

    def eject(self):
        if not self.injected:
            return
        module = self.module
        if self._had_instance_forward:
            module.forward = self._instance_forward
        else:
            module.__dict__.pop("forward", None)
        module.comfy_force_forward = self.original_force_forward
        self.original_forward = None
        self.injected = False

    # ---- forward ----
    def _forward(self, x, *args, **kwargs):
        out = self.original_forward(x, *args, **kwargs)
        for adapter, strength in self.items:
            adapter.multiplier = strength
            if self._sliceable(adapter, x, out):
                self._add_lora_sliced(adapter, x, out)
            else:
                out = self._comfy_exact(adapter, x, out, args, kwargs)
        return out

    def _comfy_exact(self, adapter, x, out, args, kwargs):
        # identical to BypassForwardHook._bypass_forward for an already computed base_out
        adapter_bypass = getattr(adapter, "bypass_forward", None)
        if adapter_bypass is not None:
            t = type(adapter)
            if not (t.bypass_forward is WeightAdapterBase.bypass_forward
                    or t.bypass_forward is comfy.weight_adapter.WeightAdapterTrainBase.bypass_forward):
                return adapter_bypass(lambda *_a, **_k: out, x, *args, **kwargs)
        return adapter.g(out + adapter.h(x, out))

    @staticmethod
    def _sliceable(adapter, x, out):
        if type(adapter) is not LoRAAdapter:
            return False
        if getattr(adapter, "is_conv", False) or adapter.weights[3] is not None:
            return False
        if type(x) is not torch.Tensor or type(out) is not torch.Tensor:
            return False
        if x.dim() < 2 or out.dim() != x.dim() or out.shape[:-1] != x.shape[:-1]:
            return False
        if out.dtype != x.dtype or out.requires_grad or (torch.is_grad_enabled() and x.requires_grad):
            return False
        return True

    def _add_lora_sliced(self, adapter, x, out):
        # Same ops / dtypes as LoRAAdapter.h + (base_out + h), element for element:
        #   up, down -> x.dtype; hidden = linear(x, down); o = linear(hidden, up)
        #   o = o * scale (python float); out = out + o
        v = adapter.weights
        up = v[0].to(dtype=x.dtype)
        down = v[1].to(dtype=x.dtype)
        alpha = v[2]
        rank = down.shape[0]
        scale = (alpha / rank) if alpha is not None else 1.0
        scale = scale * getattr(adapter, "multiplier", 1.0)
        n = x.shape[-2]
        rows_per = n
        lead = 1
        for d in x.shape[:-2]:
            lead *= int(d)
        rows_per = max(1, self.chunk_tokens // max(1, lead))
        F = torch.nn.functional
        for start in range(0, n, rows_per):
            length = min(rows_per, n - start)
            xs = x.narrow(-2, start, length)
            o = F.linear(F.linear(xs, down), up)
            o.mul_(scale)
            out.narrow(-2, start, length).add_(o)
            del o, xs


def _extract_entries(model_patcher):
    """Upstream bypass adapters (ours or ComfyUI's) as [(module_key, adapter, strength)]."""
    entries = []
    for inj in model_patcher.get_injections(INJECTION_KEY) or []:
        # duck-typed: also absorbs injections made by the older H3LowVRAMBypassLoraLoader
        if isinstance(getattr(inj, "h3_entries", None), list):
            entries.extend(inj.h3_entries)
            continue
        mgr = None
        for cell in getattr(getattr(inj, "inject", None), "__closure__", None) or ():
            try:
                c = cell.cell_contents
            except ValueError:
                continue
            if isinstance(c, BypassInjectionManager):
                mgr = c
                break
        if mgr is None:
            return None
        for key, (adapter, strength) in mgr.adapters.items():
            entries.append((key, adapter, strength))
    return entries


def apply_lowvram_bypass(model, lora, strength_model, stack_mode="replace_like_comfy",
                         chunk_tokens=DEFAULT_CHUNK_TOKENS):
    """comfy.sd.load_bypass_lora_for_models (model only) with the sliced hook."""
    key_map = comfy.lora.model_lora_keys_unet(model.model, {})
    lora = comfy.lora_convert.convert_lora(lora)
    loaded = comfy.lora.load_lora(lora, key_map)

    bypass_patches, regular_patches = {}, {}
    for key, patch_data in loaded.items():
        if isinstance(patch_data, comfy.weight_adapter.WeightAdapterBase):
            bypass_patches[key] = patch_data
        else:
            regular_patches[key] = patch_data

    new = model.clone()
    if regular_patches:
        new.add_patches(regular_patches, strength_model)

    model_sd_keys = set(new.model.state_dict().keys())
    mine = []
    for key, adapter in bypass_patches.items():
        if key in model_sd_keys:
            mk = key[:-7] if key.endswith(".weight") else key
            mine.append((mk, adapter, strength_model))
        else:
            log.warning("[H3MD LowVRAM LoRA] Adapter key not in model state_dict: %s", key)

    upstream = []
    if stack_mode == "stack":
        upstream = _extract_entries(new)
        if upstream is None:
            log.warning("[H3MD LowVRAM LoRA] upstream 'bypass_lora' injection is of an unknown kind; "
                        "replacing it (ComfyUI semantics) instead of stacking")
            upstream = []
    entries = upstream + mine
    if mine or (stack_mode == "stack" and entries):
        new.set_injections(INJECTION_KEY, [H3MDBypassInjection(entries, chunk_tokens)])
    n_up = len({e[0] for e in upstream})
    log.info("[H3MD LowVRAM LoRA] %d adapters (+%d upstream kept, mode=%s), %d regular patches, chunk_tokens=%d",
             len(mine), n_up, stack_mode, len(regular_patches), int(chunk_tokens))
    return new, len(mine), len(regular_patches)


class H3MDLowVRAMLoraLoader:
    def __init__(self):
        self.loaded_lora = None

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "model": ("MODEL",),
                "lora_name": (folder_paths.get_filename_list("loras"),),
                "strength_model": ("FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01}),
            },
            "optional": {
                "stack_mode": (STACK_MODES, {
                    "default": "replace_like_comfy",
                    "tooltip": "replace_like_comfy (default): identical to ComfyUI's bypass loader - every bypass "
                               "loader REPLACES the previous bypass LoRAs, so in a chain only the LAST bypass "
                               "LoRA is active (normal/merged LoRAs are unaffected). Swapping nodes keeps your "
                               "current output.\nstack: keep all upstream bypass LoRAs active too (also absorbs "
                               "ones from ComfyUI's own bypass loader). This changes the result if your chain "
                               "had several bypass loaders."}),
                "chunk_tokens": ("INT", {
                    "default": DEFAULT_CHUNK_TOKENS, "min": 256, "max": 1 << 20, "step": 256, "advanced": True,
                    "tooltip": "Tokens per LoRA slice. Peak extra VRAM per layer ~ chunk_tokens x layer width x "
                               "2 bytes (8192 x 21504 qkv in bf16 = 0.34 GiB). Lower = less VRAM, slightly slower."}),
            },
        }

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "load_lora_model_only"
    CATEGORY = "MiniMax H3/H3MD"
    DESCRIPTION = ("Same math and inputs as 'Load LoRA (Bypass, Model Only)', but the LoRA term is computed in "
                   "token slices and added in place, so no full-size temporaries are allocated.")

    def load_lora_model_only(self, model, lora_name, strength_model, stack_mode="replace_like_comfy",
                             chunk_tokens=DEFAULT_CHUNK_TOKENS):
        if strength_model == 0:            # comfy LoraLoaderBypass returns the model untouched
            return (model,)
        lora_path = folder_paths.get_full_path_or_raise("loras", lora_name)
        lora = None
        if self.loaded_lora is not None:
            if self.loaded_lora[0] == lora_path:
                lora = self.loaded_lora[1]
            else:
                self.loaded_lora = None
        if lora is None:
            lora = comfy.utils.load_torch_file(lora_path, safe_load=True)
            self.loaded_lora = (lora_path, lora)
        new, _, _ = apply_lowvram_bypass(model, lora, strength_model, stack_mode, chunk_tokens)
        add_lora_sig(new, ("bypass", str(lora_name), round(float(strength_model), 6), str(stack_mode)))
        return (new,)


LORA_SIG = "h3md_lora_sig"   # 0.3.4: model_options entry the H3MD Loop puts into each segment's cache key


def add_lora_sig(model, item):
    """Record a LoRA on a (cloned) ModelPatcher so the Loop's cache key changes with it (new list, never mutated)."""
    try:
        opts = model.model_options
        opts[LORA_SIG] = list(opts.get(LORA_SIG) or []) + [list(item)]
    except Exception:
        pass


NODE_CLASS_MAPPINGS = {"H3MDLowVRAMLoraLoader": H3MDLowVRAMLoraLoader}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDLowVRAMLoraLoader": "H3MD LowVRAM LoRA Loader"}
