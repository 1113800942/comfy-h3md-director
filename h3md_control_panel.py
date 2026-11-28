"""H3MD Control Panel (v0.3): the settings people change often, in one node.

Top to bottom: resolution, fps + interpolation, face refine, seed, "which segments", style buttons
(balanced / high motion / motion copy / remake 多人互换), low-res LoRA list, high-res LoRA list.
0.3.4: every row is a normal LoRA; a style button also sets the model subgraph's high-res 768 turbo strength.

The node applies the two LoRA lists to the low-res and high-res models itself (no rgthree / Crystools nodes) and hands
every other value to the Director, Loop and Save nodes through its outputs.
0.4.0: the style no longer switches a semantic bridge on (its author does not support reference-conditioned use;
the 11-08 A/B showed no visible gain).  The `semantic_bridge` output stays (old workflows keep their links) and is
always off; the example workflow has an H3MD Semantic Bridge node wired to the Loop, bypassed.
The editor (web/js/h3md_control_panel.js) draws the style buttons and the LoRA tables; the lists are stored as JSON in
two hidden STRING widgets, so a workflow saved with the panel loads anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

import folder_paths

try:
    from . import h3md_semantic_bridge as SB
    from . import h3md_lora as HL
    from . import h3md_window as WIN
except ImportError:  # flat import (tests)
    import h3md_semantic_bridge as SB
    import h3md_lora as HL
    import h3md_window as WIN

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Control Panel]"
CATEGORY = "MiniMax H3/H3MD"
STYLES = ["balanced", "high_motion", "motion_copy", "remake"]
REMAKE_STYLE = "remake"  # visible control-panel label is 「多人互换」; value matches plan.swapEngine
STYLE_LABELS = {"balanced": "均衡 Balanced", "high_motion": "高动态 High motion",
                "motion_copy": "动作复刻 Motion copy", "remake": "多人互换"}

# Recommended LoRAs (11-02 measured mix, 0.3.4 rework 2026-11-05). key -> file.  Every panel row is a NORMAL LoRA
# (ComfyUI LoraLoaderModelOnly): the one bypass speed LoRA (768 turbo) lives in the model subgraph, one per pass, so a
# panel row can never replace it (ComfyUI keeps only the LAST bypass LoRA of a chain - measured 11-05, runs 0095-0098).
# 0.6.6: these rows are RECOMMENDED, never required - a file that is not there is skipped (see find_row / apply_rows).
# Each LoRA is listed under its author's ORIGINAL file name (users could not find the renamed ones), with its size and
# sha256 so a copy the user renamed is still recognised (all eight checked against the model pages, 2026-11-27).
RECOMMENDED = {
    "combat": {"file": "H3_Combat_V2.safetensors", "bytes": 155110280,
               "sha256": "5b3edb09e9d6029439badf9bc9db4ba4a355ae679b0810888de82c4793c0bac8",
               "url": "https://huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2"},
    "weapon": {"file": "Bunny_weapon_combatV1.safetensors", "bytes": 155110272,
               "sha256": "d49de2902f3ea5e6889d9489c031ed64694de93d6a77c111d164f83c0dc359df",
               "url": "https://huggingface.co/JOKER141/MiniMax-H3-Weapon-Combat-LoRA"},
    "fightflow": {"file": "H3_Fight_Flow_V1.safetensors", "bytes": 155110296,
                  "sha256": "579e8eca67b6d70f4db94b2db67cd938751d576ee3d72aba875dae3d911f7398",
                  "url": "https://huggingface.co/JOKER141/H3-Fight-Flow-Fight-Interaction-LoRA"},
    "motion_repair": {"file": "Motion_Repair_V2.safetensors", "bytes": 155110272,
                      "sha256": "edfa0e2858caeba7be9108883f61da8525d3c58af998c94ac7a449d9603d7574",
                      "url": "https://civitai.com/models/2890788"},
    "vbvr": {"file": "H3-Licon-VBVR-I2V.safetensors", "bytes": 149238584,
             "sha256": "19ba9d18c06eaecebd11c1ca1d27a0277f414d43370124e3cfcb33a113e9f886",
             "url": "https://huggingface.co/LiconStudio/Minimax-H3-VBVR"},
    "enhancer": {"file": "MiniMaxH3_Enhancer_minimaxh3_3432130_epoch_10.safetensors", "bytes": 310170072,
                 "sha256": "7e6b1dac779962f1aa795701ef2a199a300cc90840e347d1ee6d3a165754e002",
                 "url": "https://civitai.com/models/3025511"},
    "realism": {"file": "h3-realism-people-t2v-i2v-r2v.safetensors", "bytes": 131229656,
                "sha256": "acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e",
                "url": "https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA"},
    "lms": {"file": "minimax_h3_lms_v1.0_r64.safetensors", "bytes": 1239664536,
            "sha256": "16f3195bc6bffa431c0598dc2031e520ba058b4fcbc9f104db9e3fdfdeacf60c",
            "url": "https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI/tree/main/loras"},
}
FILES = {k: v["file"] for k, v in RECOMMENDED.items()}
_KEY_OF_FILE = {v["file"]: k for k, v in RECOMMENDED.items()}
_HASHES = {}   # (path, size, mtime_ns) -> sha256, for this ComfyUI session


def _sha256(path: str) -> str:
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime_ns)
    if key not in _HASHES:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        _HASHES[key] = h.hexdigest()
    return _HASHES[key]


def find_lora(name: str):
    """The name in models/loras that holds this LoRA, or None.  The name itself when that file is there; for a
    recommended LoRA's official name also a renamed copy: same size in bytes, then the same sha256 (only files of
    exactly that size are hashed, once per session)."""
    if folder_paths.get_full_path("loras", name) is not None:
        return name
    spec = RECOMMENDED.get(_KEY_OF_FILE.get(name))
    if spec is None:
        return None
    try:
        names = folder_paths.get_filename_list("loras")
    except Exception:
        return None
    for n in names:
        try:
            p = folder_paths.get_full_path("loras", n)
            if p and os.path.getsize(p) == spec["bytes"] and _sha256(p) == spec["sha256"]:
                return n
        except OSError:
            continue
    return None


def find_row(row: dict):
    """find_lora for a panel row.  A recommended row (it has a key) saved under an older, renamed file name is also
    looked up under the official name of its key."""
    n = find_lora(row["lora"])
    if n is None and row.get("key") in RECOMMENDED and RECOMMENDED[row["key"]]["file"] != row["lora"]:
        n = find_lora(RECOMMENDED[row["key"]]["file"])
    return n
# 0.3.4: removed rows.  dasiwa was a bypass row (it replaced the model subgraph's speed LoRA), turbo (10ErosMax) was
# stacked on top of it; both are dropped from saved lists when they run (logged), and by the editor.
REMOVED = {"dasiwa", "turbo"}
BYPASS = set()   # no bypass rows any more (kept as a name for old imports)
BYPASS_STACK = {"remake"}   # exp multiswap: bypass rows stacked on top of the model subgraph's bypass speed LoRA
ORDER = {   # application order (Combat first, where it sat in the old model subgraph)
    "low": ["combat", "weapon", "fightflow", "motion_repair", "vbvr", "enhancer"],
    "high": ["combat", "realism", "weapon", "fightflow", "motion_repair", "lms", "vbvr"],
}
# style -> pass -> {key: strength}; recommended keys not listed are switched off
PRESETS = {
    "balanced": {
        "low": {"combat": 0.7, "motion_repair": 0.75, "vbvr": 1.0, "enhancer": 0.55},
        "high": {"combat": 0.49, "realism": 0.6, "motion_repair": 0.25, "lms": 0.2, "vbvr": 0.5},
    },
    # 0.4.6: one weapon LoRA per pass is enough (YJ's 1-minute fight, 11-12: low 0.85 / high 0.5, nothing else on)
    "high_motion": {
        "low": {"weapon": 0.85},
        "high": {"weapon": 0.5},
    },
    "motion_copy": {
        "low": {"combat": 0.7, "enhancer": 0.5},
        "high": {"combat": 0.49, "realism": 0.5, "lms": 0.2},
    },
    # 0.5.91 (user 2026-11-20 23:57): multi-person swap switches on the LMS LoRA only - before, the rows the
    # previous style had switched on were carried over
    "remake": {
        "low": {},
        "high": {"lms": 0.2},
    },
}
OFF_STRENGTH = {"low": {"weapon": 0.85, "fightflow": 0.65}, "high": {"weapon": 0.5, "fightflow": 0.25}}
# style -> strength of the model subgraph's HIGH-res 768 turbo (bypass) LoRA.  The editor writes it into that loader
# when a style button is clicked (drama 0.5 / high motion 1.0, like the old full workflow's Drama / High Motion switch).
# The low-res 768 turbo stays 1.0.
TURBO_HIGH = {"balanced": 0.5, "high_motion": 1.0, "motion_copy": 0.5}
TURBO_FILE = "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors"
# style -> semantic bridge (file name fragment, strength), or None.  0.4.0: none for every style (up to 0.3.8:
# balanced MiniMaxH3_SemanticBridge_v1_T8_Compat 0.12, high_motion BUNNY_H3_ActionLogic_Bridge_V1_T8_Compat 0.12).
# A bridge is now a bypassed H3MD Semantic Bridge node in the example workflow: un-bypass it to use one.
BRIDGES = {"balanced": None, "high_motion": None, "motion_copy": None, "remake": None}


def applies_lora_presets(style: str) -> bool:
    """Every style button sets its recommended LoRA rows (0.5.91: multi-person swap too - LMS only)."""
    return style in STYLES


def apply_remake_for_style(plan: dict, style: str) -> dict:
    """Drive plan.swapEngine from the control-panel style. Keyframes are never stripped.
    remake -> swapEngine 'remake' and windowMode dropped; any other style -> delete swapEngine.
    """
    if not isinstance(plan, dict):
        return plan
    if style == REMAKE_STYLE:
        plan["swapEngine"] = "remake"
        plan.pop("windowMode", None)
    else:
        plan.pop("swapEngine", None)
    return plan


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


def apply_rows(model, rows: list, cache: _LoraCache, which: str, skipped: list = None):
    """Apply the active rows in order.  0.6.6: a row whose file is not in models/loras is SKIPPED (the LoRAs are
    recommendations, the run goes on without it) - logged once with where to get it, and appended to `skipped`."""
    import comfy.sd
    active, gone = [], []
    for r in rows:
        if not (r["on"] and r["strength"] != 0):
            continue
        n = find_row(r)
        if n is None:
            gone.append(r)
        else:
            active.append(dict(r, file=n))
    if gone:
        names = [r["lora"] for r in gone]
        urls = sorted({RECOMMENDED[r["key"]]["url"] for r in gone if r.get("key") in RECOMMENDED})
        LOG.warning("%s %s-res: %d optional LoRA(s) not in models/loras, skipped - the run continues without them: %s%s"
                    " 这几个可选 LoRA 没下载，已跳过，照常出片。", _TAG, which, len(gone), names,
                    f" (download: {', '.join(urls)})" if urls else "")
        if skipped is not None:
            skipped.extend(n for n in names if n not in skipped)
    used = []
    for r in active:
        path = folder_paths.get_full_path("loras", r["file"])
        used.append(path)
        lora = cache.get(path)
        if r.get("key") in BYPASS:
            model, _, _ = HL.apply_lowvram_bypass(model, lora, r["strength"])
        elif r.get("key") in BYPASS_STACK:
            # Remake LoRA must not be stacked on the original/Turbo base; connect remake_model on the Loop.
            raise ValueError(
                f"{_TAG} Remake LoRA '{r['lora']}' cannot be stacked onto the original/Turbo {which}-res base. "
                "Connect a Remake-prepared model (fl2va + Remake LoRA, bypass, no Turbo) to H3MD Loop's remake_model. "
                "换人 LoRA 不能叠在原底座/Turbo 上：请把专用 Remake 模型接到 Loop 的 remake_model。"
            )
        else:
            model, _ = comfy.sd.load_lora_for_models(model, None, lora, r["strength"], 0)
        HL.add_lora_sig(model, ("lora", r["lora"], round(float(r["strength"]), 6)))
    LOG.info("%s %s-res LoRAs: %s", _TAG, which,
             ", ".join(f"{r['lora']} {r['strength']:g}" for r in active) or "none")
    return model, used


def bridge_for(style: str):
    """Semantic bridge chain for the style, or None (no bridge for the style - every style since 0.4.0 - or the
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


def preflight(interpolate, face_refine):
    """0.6.3: the Control Panel runs first, so problems with the save-time options show before the sampling starts.
    interpolate on + RIFE not usable -> error now.  face_refine needs no check here since 0.6.6 (built in; the save
    node writes the video without face refine when its restore model is missing)."""
    if interpolate:
        try:
            try:
                from . import h3md_rife as RIFE
            except ImportError:
                import h3md_rife as RIFE
            why = RIFE.problem()
        except Exception as exc:
            why = f"built-in RIFE not usable ({exc})"
        if why:
            raise RuntimeError(f"{_TAG} interpolate is on but {why}")
    # 0.6.6: face refine's tracking code and face detector are built in; what it still needs (a VOSR2 / SeedVR2
    # restore model wired to the save node) is checked there, and a missing one saves without face refine


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
                                "Off (default): the video is saved at H3's 24 fps. On: RIFE interpolation to fps "
                                "(built in, nothing to install).\n"
                                "关（默认）：按 H3 的 24 帧保存；开：插帧到下面的帧数（已内置，不用另装插件）。"}),
                "fps": ("FLOAT", {"default": 24.0, "min": 1.0, "max": 240.0, "step": 1.0, "tooltip":
                        "Saved frame rate; only with interpolate on (e.g. 30, 48, 60). 插帧开时的目标帧数。"}),
                "face_refine": ("BOOLEAN", {"default": False, "tooltip":
                                "Off (default). On: sharpen small faces in wide shots while saving. Finding and following the "
                                "face is built in (nothing to install); the sharpening itself uses a VOSR2 model from the "
                                "ComfyUI-VOSR2 pack - without it the video is saved without face refine.\n"
                                "关（默认）；开：保存时修脸。找脸、跟脸已内置，不用另装；修脸本身用 ComfyUI-VOSR2 的模型，"
                                "没装就不修脸、照常保存。"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True,
                                 "tooltip": "Fixed or random (control after generate). 种子：固定或随机。"}),
                "segments_to_run": ("STRING", {"default": "", "tooltip":
                                    "试镜 / Trial. Empty: render the whole video (cached parts reused). 2 / 1-2 / 3, 6-7: render only "
                                    "these parts - with Seamless long video a part is one of the buttons under the box "
                                    "(shots + seconds), otherwise a segment. The box keeps its value (orange banner) until you clear it.\n"
                                    "试镜：空 = 出整片（有缓存的直接用）；填 2 / 1-2 / 3, 6-7 = 只跑这几段。开了无接缝长视频时，"
                                    "段 = 框下面那排按钮（写着哪几个镜头、几秒到几秒），点一下就填好；没开时 = 分段。框里的值会保留（橙色提示），出整片请清空。"}),
                "style": (STYLES, {"default": "balanced", "tooltip":
                          "balanced: drama, walking, dancing · high_motion: fights, gunfights · motion_copy: "
                          "dance / motion transfer · remake (button 多人互换): low-res Remake swap path, requires remake_model on the Loop (not auto-wired). Sets the recommended LoRAs except remake, which leaves the current LoRA lists in place (no semantic bridge: un-bypass the "
                          "Semantic Bridge node in the workflow to use one)."}),
                "lora_low": ("STRING", {"default": lo, "multiline": True, "tooltip": "Low-res LoRA list (JSON)."}),
                "lora_high": ("STRING", {"default": hi, "multiline": True, "tooltip": "High-res LoRA list (JSON)."}),
            },
            # 0.6.6: declared LAST and optional, so workflows and API prompts saved before it still load; the editor
            # draws it one row above the trial box
            "optional": {
                "redraw": ("STRING", {"default": "", "tooltip":
                           "重画 / Redraw. " + WIN.REDRAW_TIP + "\nOnly with the Director's Seamless long video switch on "
                           "(greyed out otherwise). 只在导演台开了「无接缝长视频」时可用，没开时是灰的。"}),
            },
        }

    RETURN_TYPES = ("MODEL", "MODEL", "INT", "INT", "BOOLEAN", "FLOAT", "BOOLEAN", "INT", "STRING", SB.TYPE, "STRING")
    RETURN_NAMES = ("low_res_model", "high_res_model", "width", "height", "interpolate", "fps", "face_refine",
                    "seed", "segments_to_run", "semantic_bridge", "redraw")   # 0.6.6: redraw appended LAST
    FUNCTION = "run"
    CATEGORY = CATEGORY
    DESCRIPTION = ("H3MD Control Panel: resolution, fps / interpolation, face refine, seed, 重画 redraw (seconds to render again), 试镜 trial (which parts to run), "
                   "style (balanced / high motion / motion copy / remake 多人互换) and the low-res + high-res LoRA lists in one place.")

    def run(self, low_res_model, high_res_model, width, height, interpolate, fps, face_refine, seed,
            segments_to_run, style, lora_low, lora_high, redraw=""):
        style = style if style in STYLES else "balanced"
        redraw = str(redraw or "").strip()
        WIN.parse_redraw(redraw)                 # 0.6.6: wrong writing -> error now, before any sampling
        preflight(interpolate, face_refine)      # 0.6.3: before any sampling, not when the video is saved
        lo, hi = parse_rows(lora_low), parse_rows(lora_high)
        skipped = []
        low, used_lo = apply_rows(low_res_model, lo, self.cache, "low", skipped)
        high, used_hi = apply_rows(high_res_model, hi, self.cache, "high", skipped)
        self.cache.keep_only(set(used_lo) | set(used_hi))
        fps = float(fps) if interpolate else 24.0
        bridge = bridge_for(style)
        LOG.info("%s %dx%d, %s fps%s, face refine %s, seed %d, style %s, segments '%s'", _TAG, width, height,
                 f"{fps:g}", " (interpolated)" if interpolate else "", "on" if face_refine else "off", seed, style,
                 segments_to_run.strip() or "all")
        result = (low, high, int(width), int(height), bool(interpolate), fps, bool(face_refine), int(seed),
                  str(segments_to_run or "").strip(), bridge, redraw)
        return {"ui": {"h3md_panel": [{"segments_to_run": str(segments_to_run or "").strip(), "skipped": skipped}]},
                "result": result}


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
            # 0.6.6: per recommended LoRA - the file found for it in models/loras (official name, or a renamed copy
            # recognised by size + sha256), or null: the editor shows such a row switched off, "not downloaded"
            "found": (found := {k: find_lora(f) for k, f in FILES.items()}),
            "installed": {k: v is not None for k, v in found.items()},
            "info": {k: {"file": v["file"], "url": v["url"]} for k, v in RECOMMENDED.items()},
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
