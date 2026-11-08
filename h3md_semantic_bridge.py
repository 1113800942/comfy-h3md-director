"""H3MD Semantic Bridge: external, chainable "H3MD Semantic Bridge" node + Loop Sampler hook.

Wiring (like LoRA loaders):
    H3MD Semantic Bridge (A) -> bridge_chain -> H3MD Semantic Bridge (B) -> ... -> H3MD Loop Sampler.semantic_bridge
Not connected = off: Loop Sampler behaviour and segment-cache key are unchanged.

A bridge only changes the POSITIVE text conditioning h [B, T, 5120] of every segment, right after H3MD Segment
Encode and before Segment Continue (where Jack's original wrapper put it).  Each stage of the chain, in order,
takes the previous stage's output (cast back to the conditioning dtype) and does
    pred = net(h / rms(h))                         (per-token RMS normalisation)
    pred = pred * rms(h) / rms(pred)               (per-token magnitude match, eps 1e-8)
    h    = h + alpha * (pred - h)
with its own alpha.  Order matters; it is a serial chain, not an additive LoRA-style merge.

Supported adapter layouts (strict: every tensor key and shape must match the layout, else the node errors):
  * legacy_mlp  - fc1/fc2/fc3 5120-512-512-5120 SiLU student (Original Semantic Bridge v1, BUNNY ActionLogic).
                  Exact port of Jack's own ComfyUI-H3-SemanticBridge/semantic_bridge_node.py (normalisation eps 1e-8).
  * trainer_mlp - net.fc1/fc2/fc3 with metadata arch=mlp (optional residual_skip: x_norm + scale * mlp).
  * trans       - net.in_proj / net.encoder.layers.N.* / net.norm / net.out_proj with metadata arch=trans
                  (T8 comic-combat, wushu v1): in_proj -> + sinusoidal positions -> pre-norm GELU Transformer
                  encoder (ff = 4*hidden, no dropout) -> LayerNorm -> out_proj, optional residual_skip
                  (x_norm + residual_scale * correction).  Whole sequence at once (cross-token).  eps 1e-6.

Provenance: the chaining semantics (ordered serial stages, per-stage alpha, fixed "application_contract" in the
file metadata, duplicate-content rejection, max 8 stages, all stages validated before the first is applied) and
the trans/trainer-MLP inference formulas follow T8 (comfyui-minimax-h3-audio-T8 1.97.0, h3_t8/semantic_bridge*.py,
nodes_semantic_bridge.py "多桥组合") and the t8star/semantic_bridge_T8-comic-combat model card.  T8 is GPL-3.0,
so NO T8 code is copied or imported here: this is an independent implementation written from those formulas and
the weight layout, checked numerically against T8 in the tests.  Strength presets that are author data, not code:
contract alpha from the file metadata (comic-combat 1.0), wushu v1 0.12 (pinned by content SHA-256 in T8),
original/BUNNY six-tensor MLP 0.10.

CPU only (fp32); no VRAM.  No T8 / director / selflift-Avatar file is imported or modified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import struct
from collections import OrderedDict

import torch
from torch import nn
from torch.nn import functional as F

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD SemanticBridge]"
CATEGORY = "MiniMax H3/H3MD"
TYPE = "H3MD_SEMANTIC_BRIDGE"
DIM = 5120
MAX_STAGES = 8
META_KEY = "h3md_semantic_bridge"
# conditioning that already went through a bridge (ours, T8's, Jack's original) is refused, like T8 does
_BRIDGED_KEYS = (META_KEY, "t8_semantic_bridge", "semantic_bridge", "sensenova_h3_distilled")
_WUSHU_V1_SHA = "4b7a459aac43e066b9cd8d3f84f52b7d6c2777e7de05f35a3cb042abab889ebd"  # T8 author preset 0.12
_LEGACY_KEYS = {"fc1.weight", "fc1.bias", "fc2.weight", "fc2.bias", "fc3.weight", "fc3.bias"}
_MAX_BYTES = 256 * 1024 * 1024
_DTYPES = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16}
_NET_CACHE: "OrderedDict[str, nn.Module]" = OrderedDict()   # sha256 -> CPU fp32 module
_SHA_CACHE: dict = {}


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def search_dirs() -> list:
    dirs = []
    try:
        import folder_paths
        base = getattr(folder_paths, "models_dir", None)
        if base:
            dirs.append(os.path.join(base, "semantic_bridge"))
        try:
            dirs.extend(folder_paths.get_folder_paths("semantic_bridge") or [])
        except Exception:
            pass
    except Exception:
        pass
    out, seen = [], set()
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def read_header(path: str) -> dict:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if n <= 0 or n > 16 * 1024 * 1024:
            raise ValueError("not a safetensors file")
        h = json.loads(f.read(n).decode("utf-8"))
    if not isinstance(h, dict):
        raise ValueError("bad safetensors header")
    return h


def file_sha(path: str) -> str:
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def _shape(header, key):
    v = header.get(key)
    return tuple(v["shape"]) if isinstance(v, dict) and "shape" in v else None


def _contract(meta: dict):
    try:
        extra = json.loads(meta.get("extra_json", "{}") or "{}")
    except ValueError:
        raise ValueError("invalid extra_json metadata")
    if not isinstance(extra, dict):
        raise ValueError("invalid extra_json metadata")
    c = extra.get("application_contract")
    if c is None:
        return None
    a = c.get("alpha_min") if isinstance(c, dict) else None
    ok = (isinstance(c, dict) and c.get("schema") == 1 and isinstance(a, (int, float)) and math.isfinite(a)
          and 0 < a <= 1 and c.get("alpha_max") == a and c.get("magnitude_match") == "per_token"
          and c.get("token_span") == "all" and c.get("tail_ratio") == 1 and c.get("chunk_tokens") == 0
          and all(c.get(k) is False for k in ("auto_alpha", "guard", "allow_dim_mismatch")))
    if not ok:
        raise ValueError(f"unsupported application_contract {c}")
    return {"alpha": float(a)}


def _residual(meta):
    skip = str(meta.get("residual_skip", "False")).lower()
    scale = float(meta.get("residual_scale", "0.1"))
    if skip not in ("true", "false") or not math.isfinite(scale) or not 0 < scale <= 1:
        raise ValueError("invalid residual_skip/residual_scale metadata")
    return skip == "true", scale


def inspect(path: str) -> dict:
    """Layout + architecture from the header only (strict).  Raises ValueError for anything else."""
    header = read_header(path)
    meta = header.get("__metadata__", {}) or {}
    keys = set(header) - {"__metadata__"}
    for k in keys:
        if header[k].get("dtype") not in _DTYPES:
            raise ValueError(f"tensor {k} has unsupported dtype {header[k].get('dtype')}")
    if keys == _LEGACY_KEYS:
        hid = (_shape(header, "fc1.weight") or (0,))[0]
        want = {"fc1.weight": (hid, DIM), "fc1.bias": (hid,), "fc2.weight": (hid, hid), "fc2.bias": (hid,),
                "fc3.weight": (DIM, hid), "fc3.bias": (DIM,)}
        if not hid or any(_shape(header, k) != s for k, s in want.items()):
            raise ValueError("six-tensor MLP bridge with wrong shapes")
        return {"kind": "legacy_mlp", "hidden": int(hid), "contract": None, "default_alpha": 0.10}
    if "net.in_proj.weight" in keys:
        if meta.get("arch") != "trans" or int(meta.get("dim", "0")) != DIM:
            raise ValueError("transformer bridge without arch=trans / dim=5120 metadata")
        hid, layers, heads = int(meta.get("hidden", "0")), int(meta.get("layers", "0")), int(meta.get("heads", "0"))
        max_tokens = int(meta.get("max_tokens", "4096"))
        if not (64 <= hid <= 1024 and 1 <= layers <= 4 and 1 <= heads <= 16 and hid % heads == 0
                and 1 <= max_tokens <= 16384):
            raise ValueError(f"unsupported transformer bridge hidden={hid} layers={layers} heads={heads}")
        skip, scale = _residual(meta)
        spec = {"hidden": hid, "layers": layers, "heads": heads, "max_tokens": max_tokens,
                "residual_skip": skip, "residual_scale": scale}
        want = {"net." + k: tuple(v.shape) for k, v in _build_trans(spec).state_dict().items()}
        got = {k: _shape(header, k) for k in keys}
        if got != want:
            raise ValueError("transformer bridge tensor keys/shapes do not match its declared architecture")
        c = _contract(meta)
        return {"kind": "trans", **spec, "contract": c, "default_alpha": c["alpha"] if c else None}
    if "net.fc1.weight" in keys:
        if meta.get("arch") != "mlp" or int(meta.get("dim", "0")) != DIM:
            raise ValueError("trainer MLP bridge without arch=mlp / dim=5120 metadata")
        hid = int(meta.get("hidden", "0"))
        want = {"net.fc1.weight": (hid, DIM), "net.fc1.bias": (hid,), "net.fc2.weight": (hid, hid),
                "net.fc2.bias": (hid,), "net.fc3.weight": (DIM, hid), "net.fc3.bias": (DIM,)}
        if not 1 <= hid <= 4096 or {k: _shape(header, k) for k in keys} != want:
            raise ValueError("trainer MLP bridge tensor keys/shapes do not match its declared width")
        skip, scale = _residual(meta)
        c = _contract(meta)
        return {"kind": "trainer_mlp", "hidden": hid, "residual_skip": skip, "residual_scale": scale,
                "contract": c, "default_alpha": c["alpha"] if c else None}
    raise ValueError("not a Semantic Bridge file (not a LoRA / checkpoint / unknown layout)")


def scan() -> dict:
    """name -> path for every supported adapter under models/semantic_bridge (recursive, like T8)."""
    found = {}
    for d in search_dirs():
        for root, dirs, files in os.walk(d):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for fn in sorted(files):
                if not fn.lower().endswith(".safetensors"):
                    continue
                path = os.path.join(root, fn)
                name = os.path.relpath(path, d).replace("\\", "/")
                if name in found:
                    continue
                try:
                    inspect(path)
                except Exception as exc:
                    LOG.info("%s %s skipped (%s)", _TAG, name, exc)
                    continue
                found[name] = path
    return found


def list_bridge_models() -> list:
    try:
        names = sorted(scan())
    except Exception as exc:
        LOG.warning("%s cannot list adapters: %s", _TAG, exc)
        names = []
    return names or ["(no bridges in models/semantic_bridge)"]


# ---------------------------------------------------------------------------
# networks (independent implementation)
# ---------------------------------------------------------------------------

class _MLP(nn.Module):
    def __init__(self, hidden):
        super().__init__()
        self.fc1, self.fc2, self.fc3 = nn.Linear(DIM, hidden), nn.Linear(hidden, hidden), nn.Linear(hidden, DIM)

    def forward(self, x):
        return self.fc3(F.silu(self.fc2(F.silu(self.fc1(x)))))


def _sinusoid(length, hidden):
    pos = torch.arange(length, dtype=torch.float32)[:, None]
    freq = torch.exp(torch.arange(0, hidden, 2, dtype=torch.float32) * (-math.log(10000.0) / hidden))
    pe = torch.zeros(length, hidden)
    pe[:, 0::2] = torch.sin(pos * freq)
    pe[:, 1::2] = torch.cos(pos * freq)[:, : pe[:, 1::2].shape[1]]
    return pe[None]


class _Trans(nn.Module):
    def __init__(self, spec):
        super().__init__()
        h = spec["hidden"]
        self.residual_skip, self.residual_scale = spec["residual_skip"], spec["residual_scale"]
        self.in_proj = nn.Linear(DIM, h)
        self.register_buffer("pe", _sinusoid(spec["max_tokens"], h), persistent=False)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(h, spec["heads"], h * 4, dropout=0.0, activation="gelu",
                                       batch_first=True, norm_first=True),
            spec["layers"], enable_nested_tensor=False)
        self.norm = nn.LayerNorm(h)
        self.out_proj = nn.Linear(h, DIM)

    def forward(self, x):
        z = self.in_proj(x)
        pe = self.pe
        if z.shape[1] > pe.shape[1]:   # longer than trained: stretch the position table
            pe = F.interpolate(pe.transpose(1, 2), size=z.shape[1], mode="linear",
                               align_corners=False).transpose(1, 2)
        corr = self.out_proj(self.norm(self.encoder(z + pe[:, : z.shape[1]])))
        return x + self.residual_scale * corr if self.residual_skip else corr


def _build_trans(spec):
    with torch.random.fork_rng(devices=[]):   # weights are overwritten; never consume the caller's CPU RNG
        return _Trans(spec)


def _load_tensors(path):
    try:
        from safetensors.torch import load_file
        return load_file(path, device="cpu")
    except ImportError:
        header = read_header(path)
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            f.seek(8 + n)
            blob = f.read()
        out = {}
        for k, v in header.items():
            if k != "__metadata__":
                a, b = v["data_offsets"]
                out[k] = torch.frombuffer(bytearray(blob[a:b]), dtype=_DTYPES[v["dtype"]]).reshape(v["shape"]).clone()
        return out


def load_net(path: str, sha: str, info: dict) -> nn.Module:
    if sha in _NET_CACHE:
        _NET_CACHE.move_to_end(sha)
        return _NET_CACHE[sha]
    sd = _load_tensors(path)
    for k, v in sd.items():
        if not torch.isfinite(v).all():
            raise ValueError(f"{_TAG} non-finite tensor {k} in {path}")
    with torch.random.fork_rng(devices=[]):
        if info["kind"] == "legacy_mlp":
            net, prefix = _MLP(info["hidden"]), ""
        elif info["kind"] == "trainer_mlp":
            net, prefix = _MLP(info["hidden"]), "net."
        else:
            net, prefix = _build_trans(info), "net."
    if any(not k.startswith(prefix) for k in sd):
        raise ValueError(f"{_TAG} unexpected tensor names in {path}")
    net.load_state_dict({k[len(prefix):]: v.float() for k, v in sd.items()}, strict=True)
    net.to(device="cpu", dtype=torch.float32).train(False)   # inference mode
    _NET_CACHE[sha] = net
    while len(_NET_CACHE) > MAX_STAGES:
        _NET_CACHE.popitem(last=False)
    return net


# ---------------------------------------------------------------------------
# math
# ---------------------------------------------------------------------------

def _rms(x, eps):
    return (x.square().mean(dim=-1, keepdim=True) + eps).sqrt()


def predict(stage: dict, net: nn.Module, h: torch.Tensor) -> torch.Tensor:
    """h: [B,T,5120] fp32 CPU -> prediction before magnitude match."""
    kind = stage["kind"]
    if kind == "legacy_mlp":                       # Jack's original: eps 1e-8, per token
        return net(h / _rms(h, 1e-8))
    x = h / _rms(h, 1e-6)
    if kind == "trainer_mlp":
        p = net(x)
        return x + stage["residual_scale"] * p if stage["residual_skip"] else p
    return net(x)                                  # trans: whole sequence, cross-token


def apply_stage(native: torch.Tensor, stage: dict, net: nn.Module) -> torch.Tensor:
    with torch.no_grad(), torch.autocast(device_type="cpu", enabled=False):
        h = native.detach().to(device="cpu", dtype=torch.float32)
        pred = predict(stage, net, h)
        pred = pred * (_rms(h, 1e-8) / _rms(pred, 1e-8))
        out = h + float(stage["alpha"]) * (pred - h)
    if not torch.isfinite(out).all():
        raise ValueError(f"{_TAG} non-finite output from {stage['name']}")
    out = out.to(device=native.device, dtype=native.dtype)
    if not torch.isfinite(out).all():
        raise ValueError(f"{_TAG} {stage['name']} output overflows {native.dtype}")
    return out


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------

class BridgeChain(tuple):
    """Ordered, immutable tuple of stage dicts {name, sha256, kind, alpha, ...} (first applied first)."""

    def identity(self):
        return [{"sha256": s["sha256"], "kind": s["kind"], "alpha": round(float(s["alpha"]), 6)} for s in self]


def stage_from_path(name: str, path: str, alpha: float, mode: str, info: dict = None) -> dict:
    """One validated stage from a bridge file path (strict layout check + load now)."""
    info = inspect(path) if info is None else info
    sha = file_sha(path)
    stage = {"name": name, "path": path, "sha256": sha, "alpha": float(alpha), "mode": mode,
             **{k: v for k, v in info.items() if k != "default_alpha"}}
    load_net(path, sha, stage)   # strict load now -> errors appear on this node, not mid-run
    return stage


def make_stage(name: str, strength_mode: str, strength: float, adapters: dict = None) -> dict:
    adapters = scan() if adapters is None else adapters
    if name not in adapters:
        raise ValueError(f"{_TAG} bridge '{name}' not found or not a supported Semantic Bridge file "
                         f"(available: {sorted(adapters) or 'none'})")
    path = adapters[name]
    info = inspect(path)
    sha = file_sha(path)
    rec = _WUSHU_V1_SHA == sha and 0.12 or info["default_alpha"]
    if strength_mode == "auto":
        if rec is None:
            raise ValueError(f"{_TAG} {name}: no recommended strength in the file (no application_contract and "
                             "not a known model); set strength_mode=manual and use its model card")
        alpha = float(rec)
    elif strength_mode == "manual":
        alpha = float(strength)
        if not math.isfinite(alpha) or not 0 < alpha <= 1:
            raise ValueError(f"{_TAG} {name}: strength must be in (0, 1]")
        if info["contract"] is not None and alpha != info["contract"]["alpha"]:
            raise ValueError(f"{_TAG} {name}: the file's fixed application contract requires strength "
                             f"{info['contract']['alpha']} (got {alpha}); use strength_mode=auto")
    else:
        raise ValueError(f"{_TAG} unknown strength_mode {strength_mode}")
    return stage_from_path(name, path, alpha, strength_mode, info)


# ---------------------------------------------------------------------------
# T8 interop (v0.3.3): the Loop also accepts the output of T8's "H3 Semantic Bridge / 模型与设置", "自动模型参数"
# and "多桥组合" nodes (link type T8_SEMANTIC_BRIDGE).  Only public fields of T8's config objects are read
# (path, alpha, enabled, sha256, magnitude_match, token_scope / bridges); the file is loaded and applied by the
# H3MD code above.  No T8 code is imported.
# ---------------------------------------------------------------------------

T8_TYPE = "T8_SEMANTIC_BRIDGE"


def _t8_configs(obj):
    if obj is None:
        return []
    if hasattr(obj, "bridges"):                           # T8 multi-bridge stack (ordered serial)
        if not getattr(obj, "enabled", True):
            return []
        out = []
        for item in tuple(obj.bridges or ()):
            out.extend(_t8_configs(item))
        return out
    if hasattr(obj, "path") and hasattr(obj, "alpha"):    # T8 single bridge config
        return [obj]
    raise ValueError(f"{_TAG} semantic_bridge input is neither an H3MD bridge chain nor a T8 bridge config "
                     f"({type(obj).__name__})")


def from_t8(obj):
    """T8 bridge config / stack -> H3MD BridgeChain (same stage dicts as the H3MD node builds)."""
    stages = []
    for cfg in _t8_configs(obj):
        alpha = float(getattr(cfg, "alpha", 0) or 0)
        path = str(getattr(cfg, "path", "") or "")
        if not getattr(cfg, "enabled", True) or alpha == 0 or not path:
            continue
        if not math.isfinite(alpha) or not 0 < alpha <= 1:
            raise ValueError(f"{_TAG} T8 bridge {os.path.basename(path)}: alpha must be in (0, 1] (got {alpha})")
        if not os.path.isfile(path):
            raise ValueError(f"{_TAG} T8 bridge file not found: {path}")
        mm, scope = getattr(cfg, "magnitude_match", "per_token"), getattr(cfg, "token_scope", "all_tokens")
        if mm != "per_token" or scope != "all_tokens":
            LOG.warning("%s T8 bridge %s: magnitude_match=%s, token_scope=%s - H3MD applies per_token / all_tokens",
                        _TAG, os.path.basename(path), mm, scope)
        want = str(getattr(cfg, "sha256", "") or "")
        stage = stage_from_path("T8:" + os.path.basename(path), path, alpha, "t8")
        if want and want != stage["sha256"]:
            raise ValueError(f"{_TAG} T8 bridge {os.path.basename(path)} changed since the T8 node ran; re-run it")
        stages = list(chain_append(stages, stage))
    return BridgeChain(stages)


def to_chain(obj):
    """Loop input -> BridgeChain: H3MD chain (tuple of stage dicts) as is, T8 config / stack converted."""
    if not obj:
        return BridgeChain()
    if isinstance(obj, (list, tuple)) and all(isinstance(s, dict) for s in obj):
        return BridgeChain(obj)
    return from_t8(obj)


def chain_append(chain, stage: dict) -> BridgeChain:
    stages = list(chain or ())
    if any(s["sha256"] == stage["sha256"] for s in stages):
        raise ValueError(f"{_TAG} {stage['name']} is already in the chain (same file content); "
                         "do not stack one bridge twice")
    stages.append(stage)
    if len(stages) > MAX_STAGES:
        raise ValueError(f"{_TAG} at most {MAX_STAGES} bridges per chain")
    return BridgeChain(stages)


def apply_chain(conditioning, chain, where=""):
    """Returns (new_conditioning, stats).  Every [tensor[B,T,5120], dict] entry goes through all stages in order.
    Raises on already-bridged input (like T8)."""
    out, blended, skipped, tokens, cos = [], 0, 0, 0, None
    for item in conditioning:
        if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[1], dict) \
                or not torch.is_tensor(item[0]) or item[0].ndim != 3 or item[0].shape[-1] != DIM:
            out.append(item)
            skipped += 1
            continue
        native, meta = item
        hit = [k for k in _BRIDGED_KEYS if k in meta]
        if hit:
            raise ValueError(f"{_TAG} {where}: conditioning already went through a semantic bridge ({hit[0]}); "
                             "use one chain on fresh conditioning")
        if not native.is_floating_point() or not torch.isfinite(native).all():
            raise ValueError(f"{_TAG} {where}: conditioning is not finite floating point")
        cur = native
        for st in chain:
            cur = apply_stage(cur, st, load_net(st["path"], st["sha256"], st))
        a, b = native.detach().float().cpu().flatten(), cur.detach().float().cpu().flatten()
        c = float(F.cosine_similarity(a, b, dim=0))
        cos = c if cos is None else min(cos, c)
        blended += 1
        tokens += int(native.shape[1])
        out.append([cur, {**meta, META_KEY: [{"name": s["name"], "sha256": s["sha256"], "kind": s["kind"],
                                               "alpha": s["alpha"]} for s in chain]}])
    return out, {"blended": blended, "skipped": skipped, "tokens": tokens, "cos": cos}


def _describe(chain):
    return " -> ".join(f"{s['name']} ({s['kind']}, alpha {s['alpha']:g})" for s in chain)


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

class H3MDSemanticBridge:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "bridge": (list_bridge_models(), {"tooltip": "models/semantic_bridge (incl. t8_compat/). Six-tensor MLP "
                           "(Original/BUNNY), trainer MLP and T8 transformer bridges (comic-combat, wushu...)."}),
                "strength_mode": (["auto", "manual"], {"default": "auto",
                                  "tooltip": "auto = the file's fixed contract (comic-combat 1.0) / known preset (wushu v1 "
                                             "0.12) / 0.10 for Original/BUNNY MLP. manual = use strength (a file with a "
                                             "fixed contract must still match it, else error)."}),
                "strength": ("FLOAT", {"default": 0.10, "min": 0.0, "max": 1.0, "step": 0.01,
                             "tooltip": "alpha, used only when strength_mode = manual."}),
            },
            "optional": {
                "bridge_chain": (TYPE, {"tooltip": "Previous H3MD Semantic Bridge node. Chain like LoRA loaders: "
                                 "applied in order (earlier node first), each with its own strength. Max 8."}),
            },
        }

    RETURN_TYPES = (TYPE,)
    RETURN_NAMES = ("semantic_bridge",)
    FUNCTION = "build"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Adds one Semantic Bridge to an ordered chain for the H3MD Loop Sampler (semantic_bridge input). "
                   "Changes only the positive text conditioning of every segment; CPU, no VRAM.")

    @classmethod
    def IS_CHANGED(cls, bridge, strength_mode="auto", strength=0.10, bridge_chain=None):
        try:
            return f"{file_sha(scan()[bridge])}:{strength_mode}:{strength}"
        except Exception:
            return float("nan")

    def build(self, bridge, strength_mode="auto", strength=0.10, bridge_chain=None):
        stage = make_stage(bridge, strength_mode, strength)
        chain = chain_append(bridge_chain, stage)
        LOG.info("%s chain: %s", _TAG, _describe(chain))
        return (chain,)


NODE_CLASS_MAPPINGS = {"H3MDSemanticBridge": H3MDSemanticBridge}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDSemanticBridge": "H3MD Semantic Bridge"}


# ---------------------------------------------------------------------------
# Loop Sampler glue (h3md_loop.py only needs one-line hooks)
# ---------------------------------------------------------------------------

LOOP_INPUTS = {"semantic_bridge": (TYPE + "," + T8_TYPE, {"tooltip":
    "Optional semantic bridge: H3MD Semantic Bridge chain, H3MD Control Panel, or T8's H3 Semantic Bridge "
    "config / multi-bridge node. Not connected = off.\n"
    "可选语义桥：H3MD 语义桥节点、总控台，或 T8 的语义桥「模型与设置 / 多桥组合」节点。不接 = 关。"})}


class BridgeHook:
    def __init__(self, chain):
        self.chain = to_chain(chain)
        self.active = len(self.chain) > 0
        self._last = None
        if not self.active:
            return
        if len(self.chain) > MAX_STAGES:
            raise ValueError(f"{_TAG} at most {MAX_STAGES} bridges per chain")
        seen = set()
        for st in self.chain:   # validate every stage before the first segment (file unchanged, strict load)
            if st["sha256"] in seen:
                raise ValueError(f"{_TAG} {st['name']} is twice in the chain")
            seen.add(st["sha256"])
            if not os.path.isfile(st["path"]) or file_sha(st["path"]) != st["sha256"]:
                raise ValueError(f"{_TAG} {st['name']} changed or disappeared since the bridge node ran; re-run it")
            load_net(st["path"], st["sha256"], st)
        LOG.info("%s ON (%d bridge%s, CPU): %s; applied in order to every segment's positive conditioning "
                 "before Continue", _TAG, len(self.chain), "" if len(self.chain) == 1 else "s", _describe(self.chain))

    def cache_settings(self):
        return self.chain.identity() if self.active else None

    def apply(self, positive, where: str):
        self._last = None
        if not self.active:
            return positive
        new, st = apply_chain(positive, self.chain, where)
        if st["blended"] == 0:
            LOG.warning("%s %s: NOT applied - no [B,T,%d] positive tensor found (%d entr%s passed through)",
                        _TAG, where, DIM, st["skipped"], "y" if st["skipped"] == 1 else "ies")
            self._last = {"applied": False}
            return positive
        LOG.info("%s %s: %d bridge(s) applied to %d tensor(s), %d token(s); cos(native, bridged) %.4f",
                 _TAG, where, len(self.chain), st["blended"], st["tokens"], st["cos"])
        self._last = {"applied": True, "chain": [[s["name"], s["alpha"]] for s in self.chain],
                      "cos": round(st["cos"], 5) if st["cos"] is not None and math.isfinite(st["cos"]) else None}
        return new

    def annotate(self, report: dict):
        if self._last is not None:
            report["semantic_bridge"] = dict(self._last)
