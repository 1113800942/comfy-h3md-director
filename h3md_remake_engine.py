"""H3MD multi-person swap engine (experimental): Remake swap 6-step on the low-res pass only.
The 6-step sigma list matches the local example workflow
``H3MD MultiSwap (exp).json`` ManualSigmas
``1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0``.
That list has not been remotely re-verified against an official upstream source in this change.

Plan-level switch (default off): "swapEngine": "remake".  Optional "remakeMode": "swap" (default) or "edit".
When off, this module is not used: old workflows, cache keys, and low/high schedules stay as they were.

When on, for every segment (Director still does its own segmented encode / sample / cache / save relay;
this does not switch to official Remake Prefix/stitch):

pass 1 (low-res only)
  - user must explicitly connect a Remake-prepared MODEL to H3MD Loop's remake_model.  The two
    viggle_remake_{swap,edit}_comfyui.safetensors files (even when they sit under diffusion_models)
    are LoRA adapters (lora_A / lora_B / alpha), not a merged Remake base; do not load them with a
    UNET / diffusion-model loader.  Load an fl2va base, apply the matching LoRA with bypass
    (Load LoRA Bypass Model Only), then connect that MODEL.  The original low/high base and Turbo
    are never a silent fallback.  Missing or same-object input -> clear error, no download.
  - conditioning built by Viggle Remake's RemakeConditioning (Apache-2.0 pack, called through the
    registry; H3MD does not bundle or edit that pack): source clip as <Video 1>, plan keyframes as
    <Picture N> pinned at segment-local frames.  Pins are conditioning constraints, not a hard
    replacement of final pixels.
  - Remake swap 6-step from the local MultiSwap example (7 sigma values, 6 forwards), KeyframeUntil releases keyframes once
    sigma < 0.9 (keeps ordinary minimax_refs).
  - low-res canvas short edge 480 or 736.

pass 2 (high-res): existing H3MD schedule, model, and logic are left unchanged.  Remake KeyframeUntil
  and the Remake model stay on a low-res clone; keyframes are tagged h3md_low_only and dropped by the
  two-pass engine.  After the low pass ends, the 6-step wrapper is not applied to high.

Swap 6-step values match the local example workflow ``H3MD MultiSwap (exp).json`` ManualSigmas
(1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0).  This is not a remote official re-verification.
The previously spliced 0.315789 high-res step is not part of that 6-step and is not mixed into either pass.
Edit 6-step values were not found in local exp docs/code; remakeMode=edit raises instead of inventing.
"""

import logging

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Swap]"

KEY = "swapEngine"
MODE_KEY = "remakeMode"
REMAKE = "remake"
ENGINES = (REMAKE,)
MODES = ("swap", "edit")
# Remake swap 6-step (6 forwards). Source: local example workflow
# ``H3MD MultiSwap (exp).json`` ManualSigmas widgets_values
# ['1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0'].
# Not remotely re-verified. RemakeKeyframeUntil: "0.9 = the last two of six forwards".
# Identifier kept for compatibility; cache key official6-low is unchanged.
OFFICIAL_SWAP_SIGMAS = (1.0, 0.990476, 0.965243, 0.923077, 0.8, 0.631579, 0.0)
# Alias of the low-res 6-step schedule (no longer a low+high splice).
SIGMAS = OFFICIAL_SWAP_SIGMAS
# Deprecated: the 2026-11-14 split that leaked Remake steps into high-res. Not used when Remake is on.
TRANSITION = None
KEYFRAME_UNTIL = 0.9
MAX_REFS = 4                 # RemakeConditioning: ref_1 .. ref_4
SHORT_EDGES = (480, 736)     # the canvases Remake was trained on
MIN_FRAMES, MAX_FRAMES = 90, 345
LOW_ONLY_TAG = "h3md_low_only"   # = h3md_director.KEYFRAME_TAG: the two-pass engine drops these keyframes in pass 2
TURBO_HINTS = ("turbo_4step", "fl2v_turbo", "768p_comfyui_bf16")
REMAKE_FILE_HINTS = ("viggle_remake",)
CACHE_SCHEDULE = "official6-low"


def engine(plan) -> str | None:
    value = (plan or {}).get(KEY)
    return value if value in ENGINES else None


def remake_mode(plan) -> str | None:
    """'swap' when the remake engine is on (default), 'edit' when requested, else None."""
    if not engine(plan):
        return None
    value = (plan or {}).get(MODE_KEY)
    return value if value in MODES else "swap"


def normalize(raw: dict, notes: list):
    """parse_plan helper: the plan's swapEngine or None (unknown values are dropped with a note)."""
    value = raw.get(KEY)
    if value in (None, "", "off", "none"):
        return None
    if value not in ENGINES:
        notes.append(f"swapEngine {value!r} unknown (known: {', '.join(ENGINES)}) - ignored")
        return None
    return value


def normalize_mode(raw: dict, notes: list):
    """parse_plan helper: remakeMode when swapEngine is remake. Default swap is omitted (old keys)."""
    if normalize(raw, []) is None:
        return None
    value = raw.get(MODE_KEY)
    if value in (None, "", "swap"):
        return None
    if value == "edit":
        return "edit"
    notes.append(f"remakeMode {value!r} unknown (known: swap, edit) - ignored, using swap")
    return None


def low_sigmas(plan=None) -> torch.Tensor:
    """Local-example Remake swap 6-step for the low-res pass only. Does not invent an edit schedule."""
    mode = remake_mode(plan) if plan is not None else "swap"
    if mode == "edit":
        raise ValueError(
            f"{_TAG} remakeMode 'edit' has no official 6-step sigma list in the local experimental docs/code "
            "(only the swap schedule is recorded here). Refusing to invent edit values. "
            "本地没有核对到 official edit 六步 sigma，不能编造；请用 remakeMode=swap 或补上本地文档后再开 edit。"
        )
    return torch.tensor(OFFICIAL_SWAP_SIGMAS, dtype=torch.float32)


def sigmas() -> torch.Tensor:
    """Local-example Remake swap 6-step (low-res only). Prefer low_sigmas(plan)."""
    return low_sigmas({"swapEngine": REMAKE})


def high_sigmas_from(sigmas, transition_step: int = 6) -> torch.Tensor:
    """The existing H3MD high-res arm, split from the workflow schedule. Remake does not replace it."""
    try:
        from . import h3md_sampler as S
    except ImportError:
        import h3md_sampler as S
    _low, high = S.split_sigmas(sigmas, transition_step)
    return high


def _node(name: str):
    import nodes as comfy_nodes
    cls = comfy_nodes.NODE_CLASS_MAPPINGS.get(name)
    if cls is None:
        raise RuntimeError(f"{_TAG} swapEngine 'remake' needs the ComfyUI-Viggle-Remake nodes ({name} not found). "
                           "多人换人需要安装 ComfyUI-Viggle-Remake 节点包。")
    return cls


def keyframe_until(model, until: float = KEYFRAME_UNTIL):
    """The low-res model with Remake's keyframe release (keyframes condition only forwards with sigma >= until)."""
    return _node("RemakeKeyframeUntil")().run(model, float(until))[0]


def classify_remake_weight(path) -> str:
    """Read only the safetensors JSON header (not the weight body). 'lora', 'full_or_mixed', or 'unreadable'."""
    import struct, json
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n < 2 or n > 32_000_000:
                return "unreadable"
            meta = json.loads(f.read(n).decode("utf-8"))
    except Exception:
        return "unreadable"
    names = [k for k in meta.keys() if k != "__metadata__"]
    if not names:
        return "unreadable"
    lora = sum(1 for n in names if "lora_a" in n.lower() or "lora_b" in n.lower() or "lora_up" in n.lower() or "lora_down" in n.lower())
    if lora and lora + sum(1 for n in names if n.lower().endswith("alpha") or ".alpha" in n.lower()) == len(names):
        return "lora"
    if lora:
        return "full_or_mixed"
    return "full_or_mixed"


def scan_remake_files() -> list:
    """Filenames under diffusion_models / loras / unet that look like Remake weights. Never downloads.
    Each entry is annotated with a header-only type so a LoRA sitting in diffusion_models is not
    treated as a merged base."""
    found = []
    try:
        import folder_paths
    except Exception:
        return found
    for folder in ("diffusion_models", "unet", "checkpoints", "loras"):
        try:
            names = folder_paths.get_filename_list(folder) or []
        except Exception:
            continue
        for name in names:
            low = str(name).lower()
            if not any(h in low for h in REMAKE_FILE_HINTS):
                continue
            path = None
            try:
                path = folder_paths.get_full_path(folder, name)
            except Exception:
                path = None
            kind = classify_remake_weight(path) if path else "unreadable"
            found.append(f"{folder}/{name} [{kind}]")
    return found


def _lora_sig_text(model) -> str:
    sig = ((getattr(model, "model_options", None) or {}).get("h3md_lora_sig")) if model is not None else None
    return " ".join(str(x) for x in sig) if sig else ""


def model_has_turbo(model) -> bool:
    text = _lora_sig_text(model).lower()
    return any(h in text for h in TURBO_HINTS)


def require_remake_model(remake_model, low_res_model, high_res_model):
    """The isolated Remake base for the low-res pass, or a clear error. Never falls back to the original base."""
    found = scan_remake_files()
    found_txt = ", ".join(found) if found else "none found in models/"
    if remake_model is None:
        raise ValueError(
            f"{_TAG} swapEngine 'remake' is on but remake_model is not connected. "
            "Turn on the 'Remake model' node (UNETLoader, viggle_remake_swap_pruned_int8_convrot.safetensors in "
            "models/diffusion_models) wired to H3MD Loop's remake_model: in the example workflows the Control Panel "
            "style 多人互换 switches it on, or open H3MD Director Remake.json. "
            "选「多人互换」风格会自动打开工作流里的「Remake model 换人模型」节点（或用 H3MD Director Remake.json）。 "
            "Other route: load an fl2va base, then apply the "
            "swap or edit Remake file with a bypass LoRA loader (Load LoRA Bypass, Model Only). "
            "viggle_remake_*_comfyui.safetensors are LoRA adapters (lora_A/B + alpha), even if they appear under "
            "diffusion_models — do not load them with a UNET/diffusion-model loader, and do not treat a random "
            "MODEL socket as a verified Remake base. The original/Turbo low_res_model is never a silent fallback. "
            f"No download. Files that look like Remake: {found_txt}. "
            "换人模式已开，但未接入 Remake 模型：请先加载 fl2va，再用 bypass LoRA 套上 swap/edit 文件，把得到的 MODEL "
            "接到 Loop 的 remake_model。这两个 viggle_remake_* 即使位于 diffusion_models 也是 LoRA，不能当 UNET 底模加载。"
            "任意 MODEL 接入不等于已验证专用底模；不要 Turbo、不要回落原底座。不会自动下载。"
        )
    if remake_model is low_res_model or remake_model is high_res_model:
        raise ValueError(
            f"{_TAG} remake_model is the same object as the original low-res or high-res base. "
            "Disconnect the original/Turbo base and load a Remake-prepared model on remake_model. "
            "Remake 模型不能与原低清/高清底座是同一对象，不能沿用 Turbo。"
        )
    if model_has_turbo(remake_model):
        raise ValueError(
            f"{_TAG} remake_model still carries a Turbo LoRA signature ({TURBO_HINTS}). "
            "Remake must not inherit the Turbo / original-base stack. Load fl2va + Remake LoRA bypass only. "
            "检测到 remake_model 仍带 Turbo LoRA，不能用作魔改底模。"
        )
    return remake_model


def low_short_edge(width: int, height: int, lowres_scale: float = 0.5) -> int:
    """Short edge in pixels of the two-pass engine's low-res grid (same rounding as luna_two_pass)."""
    h = max(2, round(int(height) // 16 * lowres_scale / 2) * 2) * 16
    w = max(2, round(int(width) // 16 * lowres_scale / 2) * 2) * 16
    return min(h, w)


def check(plan: dict, seg: dict, lowres_scale: float = 0.5) -> int:
    se = low_short_edge(plan["width"], plan["height"], lowres_scale)
    if se not in SHORT_EDGES:
        raise ValueError(f"{_TAG} the low-res canvas has a short edge of {se} px; Remake was trained on 480 and 736. "
                         "Use an output of 1664x960 (or 960x1664) / 2560x1472 at lowres_scale 0.5. "
                         f"换人模式要求低清短边 480 或 736 像素：成片用 1664x960 或 2560x1472。")
    n = int(seg["length"])
    if not MIN_FRAMES <= n <= MAX_FRAMES:
        raise ValueError(f"{_TAG} segment {seg['index'] + 1}: {n} frames; Remake renders {MIN_FRAMES}-{MAX_FRAMES} "
                         "frames per segment (split the shot). 换人模式每段 90-345 帧。")
    return se


def _pins_for_segment(keyframes: list, n: int, seg_index: int) -> list:
    """Segment-local pins in [0, n). Drops duplicates; errors on out-of-range (no silent cross-segment drift)."""
    valid, seen = [], set()
    for kf in keyframes:
        f = int(kf["frame"])
        if f < 0 or f >= n:
            raise ValueError(
                f"{_TAG} segment {seg_index + 1}: keyframe '{kf.get('image')}' at frame {f} is outside this "
                f"segment (0-{n - 1}). Pins must be re-anchored to the segment; a global index would drift. "
                f"关键帧 {f} 超出本段 0-{n - 1}，不能用跨段全局帧号。"
            )
        if f in seen:
            continue
        seen.add(f)
        valid.append(kf)
    if len(valid) > MAX_REFS:
        LOG.warning("%s segment %d: %d keyframes, Remake takes %d - the first %d are used", _TAG, seg_index + 1,
                    len(valid), MAX_REFS, MAX_REFS)
        valid = valid[:MAX_REFS]
    return valid


def encode(plan: dict, seg: dict, picked: dict, prompt: str, clip, vae, audio_vae, keyframes: list, load_keyframe,
           lowres_scale: float = 0.5):
    """-> (positive, latent).  positive: RemakeConditioning on the low-res canvas, keyframes tagged low-res only.
    latent: H3MD's own empty full-size target (video zeros + the audio stream of Remake's target, the locked song is
    put in by H3MD's continuation step as for every other plan)."""
    se = check(plan, seg, lowres_scale)
    if not picked["videos"]:
        raise ValueError(f"{_TAG} segment {seg['index'] + 1}: no source video (<Video 1>) - the swap needs one")
    n = int(seg["length"])
    keyframes = _pins_for_segment(list(keyframes or ()), n, int(seg["index"]))
    pinned = True
    if not keyframes and seg.get("keyframesNear"):
        # 2026-11-15 review: an autosplit part with no keyframe of its own uses the shot's nearest keyframe as a
        # plain reference (<Picture 1>, not pinned to a frame) instead of stopping the whole run
        keyframes, pinned = list(seg["keyframesNear"])[:MAX_REFS], False
        LOG.warning("%s segment %d: no keyframe inside this part - the nearest one ('%s') is used as an un-pinned "
                    "reference", _TAG, seg["index"] + 1, keyframes[0]["image"])
    if not keyframes:
        raise ValueError(f"{_TAG} segment {seg['index'] + 1}: no keyframes - give at least one picture of the source "
                         "with everybody already replaced (MultiSwap makes them)")
    src = picked["videos"][0][..., :3]
    if src.shape[0] < n:   # held last frame, as H3MD does for a guide a few frames short of its shot
        src = torch.cat([src, src[-1:].expand(n - src.shape[0], *src.shape[1:])], 0)
    refs = [load_keyframe(plan["media"]["images"][kf["image"]]["file"])[:1, ..., :3] for kf in keyframes]
    pins = ", ".join(str(int(kf["frame"])) for kf in keyframes) if pinned else ""
    kw = {f"ref_{i + 1}": r for i, r in enumerate(refs[1:], start=1)}
    positive, rlat = _node("RemakeConditioning")().run(clip, vae, audio_vae, src[:n], prompt, refs[0], pins, n, se,
                                                       audio=None, **kw)
    tagged = []
    for c in positive:
        d = dict(c[1])
        if d.get("minimax_keyframes"):
            d["minimax_keyframes"] = [dict(k, **{LOW_ONLY_TAG: True}) for k in d["minimax_keyframes"]]
        tagged.append([c[0], d])
    video_low, audio = rlat["samples"].unbind()
    video = torch.zeros(1, video_low.shape[1], video_low.shape[2], int(plan["height"]) // 16, int(plan["width"]) // 16)
    import comfy.nested_tensor
    latent = {"samples": comfy.nested_tensor.NestedTensor((video, torch.zeros_like(audio)))}
    LOG.info("%s segment %d: Remake conditioning, %d frames, low-res canvas short edge %d, %d keyframe(s) pinned at "
             "frames %s (low-res pass only, released below sigma %.2f; pins condition, they do not hard-replace "
             "final pixels)", _TAG, seg["index"] + 1, n, se, len(refs), pins, KEYFRAME_UNTIL)
    return tagged, latent
