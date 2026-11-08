"""H3MD Control Panel (v0.3): the settings people change often, in one node.

Top to bottom: resolution, fps + interpolation, face refine, seed, "which segments", style buttons
(balanced / high motion / motion copy), low-res LoRA list, high-res LoRA list.
0.3.4: every row is a normal LoRA; a style button also sets the model subgraph's high-res 768 turbo strength.

The node applies the two LoRA lists to the low-res and high-res models itself (no rgthree / Crystools nodes) and hands
every other value to the Director, Loop and Save nodes through its outputs.
0.3.9-beta: the style no longer switches a semantic bridge on (its author does not support reference-conditioned use;
the 11-08 A/B showed no visible gain).  The `semantic_bridge` output stays (old workflows keep their links) and is
always off; the example workflow has an H3MD Semantic Bridge node wired to the Loop, bypassed.
The editor (web/js/h3md_control_panel.js) draws the style buttons and the LoRA tables; the lists are stored as JSON in
two hidden STRING widgets, so a workflow saved with the panel loads anywhere.
"""

from __future__ import annotations

import json
import logging

import folder_paths

try:
    from . import h3md_semantic_bridge as SB
    from . import h3md_lora as HL
except ImportError:  # flat import (tests)
    import h3md_semantic_bridge as SB
    import h3md_lora as HL

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Control Panel]"
CATEGORY = "MiniMax H3/H3MD"
STYLES = ["balanced", "high_motion", "motion_copy"]

# Recommended LoRAs (11-02 measured mix, 0.3.4 rework 2026-11-05). key -> file.  Every panel row is a NORMAL LoRA
# (ComfyUI LoraLoaderModelOnly): the one bypass speed LoRA (768 turbo) lives in the model subgraph, one per pass, so a
# panel row can never replace it (ComfyUI keeps only the LAST bypass LoRA of a chain - measured 11-05, runs 0095-0098).
FILES = {
    "combat": "H3_Combat_V2.safetensors",
    "weapon": "H3-Bunny_weapon_combatV1.safetensors",
    "fightflow": "H3_Fight_Flow_V1-Unarmed combat interaction Melee weapon interaction Gun-fu interaction "
                 "一采 0.65 - 二采 0.25.safetensors",
    "motion_repair": "H3 Motion_Repair_V2.safetensors",
    "speed": "H3_speed_slider_1.1.safetensors",
    "cinematic": "Minimax H3 电影感0.1 cinematic texture.safetensors",
    "vbvr": "H3-Licon-VBVR-I2V-1pass1.0-2pass0.3.safetensors",
    "enhancer": "MiniMaxH3_Enhancer_0.5-1.0_3432130_epoch_10.safetensors",
    "realism": "h3-realism-people-r34l1sm-1.0 strong 0.6-0.8 light.safetensors",
    "lms": "minimax_h3_lms_v1.0_r64.safetensors",
}
# 0.3.4: removed rows.  dasiwa was a bypass row (it replaced the model subgraph's speed LoRA), turbo (10ErosMax) was
# stacked on top of it; both are dropped from saved lists when they run (logged), and by the editor.
REMOVED = {"dasiwa", "turbo"}
BYPASS = set()   # no bypass rows any more (kept as a name for old imports)
ORDER = {   # application order (Combat first, where it sat in the old model subgraph)
    "low": ["combat", "weapon", "fightflow", "motion_repair", "speed", "cinematic", "vbvr", "enhancer"],
    "high": ["combat", "realism", "weapon", "fightflow", "motion_repair", "cinematic", "lms", "vbvr"],
}
# style -> pass -> {key: strength}; recommended keys not listed are switched off
PRESETS = {
    "balanced": {
        "low": {"combat": 0.7, "motion_repair": 0.75, "speed": 3.0, "cinematic": 0.5, "vbvr": 1.0, "enhancer": 0.55},
        "high": {"combat": 0.49, "realism": 0.6, "motion_repair": 0.25, "cinematic": 0.2, "lms": 0.2, "vbvr": 0.5},
    },
    "high_motion": {
        "low": {"combat": 0.7, "motion_repair": 0.75, "speed": 6.0, "cinematic": 0.5, "vbvr": 1.0, "fightflow": 0.65,
                "enhancer": 0.5},
        "high": {"combat": 0.49, "realism": 0.7, "motion_repair": 0.25, "cinematic": 0.2, "vbvr": 0.5,
                 "fightflow": 0.25, "lms": 0.2},
    },
    "motion_copy": {
        "low": {"combat": 0.7, "enhancer": 0.5},
        "high": {"combat": 0.49, "realism": 0.5, "lms": 0.2},
    },
}
OFF_STRENGTH = {"low": {"weapon": 0.45, "fightflow": 0.65}, "high": {"weapon": 0.6, "fightflow": 0.25}}
# style -> strength of the model subgraph's HIGH-res 768 turbo (bypass) LoRA.  The editor writes it into that loader
# when a style button is clicked (drama 0.5 / high motion 1.0, like the old full workflow's Drama / High Motion switch).
# The low-res 768 turbo stays 1.0.
TURBO_HIGH = {"balanced": 0.5, "high_motion": 1.0, "motion_copy": 0.5}
TURBO_FILE = "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors"
# style -> semantic bridge (file name fragment, strength), or None.  0.3.9-beta: none for every style (up to 0.3.8:
# balanced MiniMaxH3_SemanticBridge_v1_T8_Compat 0.12, high_motion BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat 0.12).
# A bridge is now a bypassed H3MD Semantic Bridge node in the example workflow: un-bypass it to use one.
BRIDGES = {"balanced": None, "high_motion": None, "motion_copy": None}


def preset_rows(style: str, which: str) -> list:
    """Recommended rows of one pass for a style: [{key, lora, on, strength}] in application order."""
    p = PRESETS[style][which]
    rows = []
    for key in ORDER[which]:
        on = key in p
        s = p.get(key, OFF_STRENGTH[which].get(key, PRESETS["balanced"][which].get(key, 1.0)))
        rows.append({"key": key, "lora": FILES[key], "on": on, "strength": float(s)})
    return rows


def apply_style(rows: list, style: str, which: str) -> list:
    """Style button: recommended rows (with a 'key') get the style's switch + strength; the user's own rows stay."""
    rec = {r["key"]: r for r in preset_rows(style, which)}
    out, seen = [], set()
    for r in rows or []:
        k = r.get("key")
        if k in REMOVED:   # 0.3.4: Dasiwa / 10ErosMax rows are dropped
            continue
        if k in rec:
            out.append(dict(r, on=rec[k]["on"], strength=rec[k]["strength"]))
            seen.add(k)
        else:
            out.append(dict(r))
    for k, r in rec.items():   # recommended rows the list did not have yet
        if k not in seen:
            out.append(r)
    return out


def parse_rows(text) -> list:
    if isinstance(text, list):
        rows = text
    else:
        try:
            rows = json.loads(text or "[]")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{_TAG} LoRA list is not valid JSON: {exc}") from None
    if not isinstance(rows, list):
        raise ValueError(f"{_TAG} LoRA list must be a JSON list")
    out = []
    for r in rows:
        if not isinstance(r, dict) or not str(r.get("lora") or "").strip():
            continue
        try:
            s = float(r.get("strength", 1.0))
        except (TypeError, ValueError):
            s = 1.0
        if r.get("key") in REMOVED:   # 0.3.4: dasiwa / turbo rows of an older workflow
            LOG.info("%s row '%s' (%s) removed in 0.3.4 - the speed LoRA is the 768 turbo in the model subgraph; "
                     "skipped. 0.3.4 已移除这一行（加速改由模型区 768 负责），已跳过。", _TAG, r.get("key"), r["lora"])
            continue
        out.append({"key": r.get("key"), "lora": str(r["lora"]), "on": bool(r.get("on", True)), "strength": s})
    return out


class _LoraCache:
    """Last loaded LoRA files (path -> state dict), so a re-queue does not read them again."""

    def __init__(self):
        self.files = {}

    def get(self, path):
        import comfy.utils
        if path not in self.files:
            self.files[path] = comfy.utils.load_torch_file(path, safe_load=True)
        return self.files[path]

    def keep_only(self, paths):
        for p in list(self.files):
            if p not in paths:
                del self.files[p]


def apply_rows(model, rows: list, cache: _LoraCache, which: str):
    """Apply the active rows in order. Missing files -> one error listing all of them."""
    import comfy.sd
    active = [r for r in rows if r["on"] and r["strength"] != 0]
    missing = [r["lora"] for r in active if folder_paths.get_full_path("loras", r["lora"]) is None]
    if missing:
        raise ValueError(f"{_TAG} {which}-res LoRA file(s) not found in models/loras: {missing}. Download them (see "
                         f"the Model links note) or switch the row off. {which} 清单里这些 LoRA 文件找不到："
                         f"{missing}，请下载到 models/loras，或把这一行关掉。")
    used = []
    for r in active:
        path = folder_paths.get_full_path("loras", r["lora"])
        used.append(path)
        lora = cache.get(path)
        if r.get("key") in BYPASS:
            model, _, _ = HL.apply_lowvram_bypass(model, lora, r["strength"])
        else:
            model, _ = comfy.sd.load_lora_for_models(model, None, lora, r["strength"], 0)
        HL.add_lora_sig(model, ("lora", r["lora"], round(float(r["strength"]), 6)))
    LOG.info("%s %s-res LoRAs: %s", _TAG, which,
             ", ".join(f"{r['lora']} {r['strength']:g}" for r in active) or "none")
    return model, used


def bridge_for(style: str):
    """Semantic bridge chain for the style, or None (no bridge for the style - every style since 0.3.9-beta - or the
    file is not installed: skipped, logged)."""
    spec = BRIDGES.get(style)
    if not spec:
        return None
    frag, strength = spec
    try:
        names = [n for n in SB.scan() if frag in n]
    except Exception as exc:
        LOG.warning("%s semantic bridge scan failed (%s); bridge off", _TAG, exc)
        return None
    if not names:
        LOG.info("%s style %s: semantic bridge '%s' not installed (models/semantic_bridge); bridge off", _TAG, style,
                 frag)
        return None
    try:
        return SB.chain_append(None, SB.make_stage(sorted(names)[0], "manual", strength))
    except Exception as exc:
        LOG.warning("%s semantic bridge %s not usable (%s); bridge off", _TAG, names[0], exc)
        return None


class H3MDControlPanel:
    def __init__(self):
        self.cache = _LoraCache()

    @classmethod
    def INPUT_TYPES(cls):
        lo = json.dumps(preset_rows("balanced", "low"), ensure_ascii=False)
        hi = json.dumps(preset_rows("balanced", "high"), ensure_ascii=False)
        return {
            "required": {
                "low_res_model": ("MODEL", {"tooltip": "Base model for the low-res pass (from the model subgraph)."}),
                "high_res_model": ("MODEL", {"tooltip": "Base model for the high-res pass (from the model subgraph)."}),
                "width": ("INT", {"default": 1088, "min": 64, "max": 4096, "step": 16,
                                  "tooltip": "Video width. 宽"}),
                "height": ("INT", {"default": 1920, "min": 64, "max": 4096, "step": 16,
                                   "tooltip": "Video height. 高"}),
                "interpolate": ("BOOLEAN", {"default": False, "tooltip":
                                "Off: the video is saved at H3's 24 fps. On: RIFE interpolation to fps.\n"
                                "关：按 H3 的 24 帧保存；开：插帧到下面的帧数。"}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 1.0, "tooltip":
                        "Saved frame rate; only with interpolate on (e.g. 30, 48, 60). 插帧开时的目标帧数。"}),
                "face_refine": ("BOOLEAN", {"default": False, "tooltip":
                                "Sharpen small faces in wide shots while saving. 保存时修脸。"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True,
                                 "tooltip": "Fixed or random (control after generate). 种子：固定或随机。"}),
                "segments_to_run": ("STRING", {"default": "", "tooltip":
                                    "Empty: run everything (cached segments reused). 2 / 1-2 / 3, 6-7, 9: generate "
                                    "only these (cached ones again too). The box keeps its value (orange banner) until you clear it.\n"
                                    "空 = 全部跑完；填 2 / 1-2 / 3, 6-7, 9 = 只生成这几段。框里的值会保留（橙色提示），要跑完整片请清空。"}),
                "style": (STYLES, {"default": "balanced", "tooltip":
                          "balanced: drama, walking, dancing · high_motion: fights, gunfights · motion_copy: "
                          "dance / motion transfer. Sets the recommended LoRAs (no semantic bridge: un-bypass the "
                          "Semantic Bridge node in the workflow to use one)."}),
                "lora_low": ("STRING", {"default": lo, "multiline": True, "tooltip": "Low-res LoRA list (JSON)."}),
                "lora_high": ("STRING", {"default": hi, "multiline": True, "tooltip": "High-res LoRA list (JSON)."}),
            },
        }

    RETURN_TYPES = ("MODEL", "MODEL", "INT", "INT", "BOOLEAN", "FLOAT", "BOOLEAN", "INT", "STRING", SB.TYPE)
    RETURN_NAMES = ("low_res_model", "high_res_model", "width", "height", "interpolate", "fps", "face_refine",
                    "seed", "segments_to_run", "semantic_bridge")
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("H3MD Control Panel: resolution, fps / interpolation, face refine, seed, which segments to run, "
                   "style (balanced / high motion / motion copy) and the low-res + high-res LoRA lists in one place.")

    def run(self, low_res_model, high_res_model, width, height, interpolate, fps, face_refine, seed,
            segments_to_run, style, lora_low, lora_high):
        style = style if style in STYLES else "balanced"
        lo, hi = parse_rows(lora_low), parse_rows(lora_high)
        low, used_lo = apply_rows(low_res_model, lo, self.cache, "low")
        high, used_hi = apply_rows(high_res_model, hi, self.cache, "high")
        self.cache.keep_only(set(used_lo) | set(used_hi))
        fps = float(fps) if interpolate else 24.0
        bridge = bridge_for(style)
        LOG.info("%s %dx%d, %s fps%s, face refine %s, seed %d, style %s, segments '%s'", _TAG, width, height,
                 f"{fps:g}", " (interpolated)" if interpolate else "", "on" if face_refine else "off", seed, style,
                 segments_to_run.strip() or "all")
        result = (low, high, int(width), int(height), bool(interpolate), fps, bool(face_refine), int(seed),
                  str(segments_to_run or "").strip(), bridge)
        return {"ui": {"h3md_panel": [{"segments_to_run": str(segments_to_run or "").strip()}]}, "result": result}


def register_routes() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:
        return False

    @routes.get("/h3md/control_panel/presets")
    async def h3md_panel_presets(request):
        try:
            installed = set(folder_paths.get_filename_list("loras"))
        except Exception:
            installed = set()
        return web.json_response({
            "styles": STYLES,
            "presets": {s: {w: preset_rows(s, w) for w in ("low", "high")} for s in STYLES},
            "installed": {k: f in installed for k, f in FILES.items()},
            "removed": sorted(REMOVED),
            "turbo_high": TURBO_HIGH,
            "turbo_file": TURBO_FILE,
            "loras": sorted(installed),
        })

    return True


try:
    register_routes()
except Exception as _exc:  # never blocks node loading
    LOG.warning("%s routes not registered: %s", _TAG, _exc)

NODE_CLASS_MAPPINGS = {"H3MDControlPanel": H3MDControlPanel}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDControlPanel": "H3MD Control Panel 总控台"}
