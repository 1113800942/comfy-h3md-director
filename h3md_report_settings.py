"""H3MD diagnostic report - every setting of the workflow, named, and compared with the shipped example workflow
(2026-11-17, H3MD 0.5.1; YJ: "all parameters in a list, on and off, so we can trace a problem").

- named_nodes(workflow): every node (subgraphs included) with where it sits, its state (on / bypassed / muted, or off
  because its subgraph is switched off) and its widget values by name. Names come from the node's own INPUT_TYPES,
  in the order ComfyUI's frontend builds the widgets (a seed's control_after_generate, an upload button); when the
  count does not match, the values stay positional (#1, #2 ...).
- best_example / compare: the example workflow (example_workflows/) closest to the user's, then nodes switched off or
  on, missing or added, and every changed value. Values that change every run (seed, plan text, media files) are
  listed apart.
- key_switches: the handful of settings that decide speed, VRAM and look, in one table.
Pure logic; the report module adds privacy scrubbing."""
from __future__ import annotations

import json
import math
import os

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
UPLOAD_FLAGS = ("image_upload", "video_upload", "audio_upload", "animated_image_upload", "upload")
SKIP_TYPES = {"Note", "MarkdownNote", "Label (rgthree)", "Reroute", "PrimitiveNode", "Fast Groups Bypasser (rgthree)",
              "Fast Groups Muter (rgthree)", "FancyTimerNode"}
STATES = {0: "on", 2: "muted", 4: "bypassed"}
# values expected to differ every run - shown, never flagged
RUN_KEYS = {"seed", "noise_seed", "control_after_generate", "plan_json", "image", "audio", "video", "upload", "file",
            "filename_prefix", "segments_to_run", "rerun_segments", "stop_after", "people", "mapping",
            "descriptions", "people_at_start", "vosr2_seed", "text", "prompt", "name"}
RUN_TYPES = {"LoadImage", "LoadAudio", "VHS_LoadVideo", "LoadVideo", "PreviewAny", "PreviewImage"}
EXAMPLE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example_workflows")


# --------------------------------------------------------------------------------------------- names
def widget_names(input_types: dict) -> list:
    """Widget names in the order the frontend creates (and serialises) them."""
    out = []
    for part in ("required", "optional"):
        for name, spec in ((input_types or {}).get(part) or {}).items():
            if not isinstance(spec, (list, tuple)) or not spec:
                continue
            t = spec[0]
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            kind = "COMBO" if isinstance(t, (list, tuple)) else str(t)
            if kind not in WIDGET_TYPES or opts.get("forceInput"):
                continue
            out.append(name)
            ctl = opts.get("control_after_generate")
            if ctl or (ctl is None and kind == "INT" and name in ("seed", "noise_seed")):
                out.append("control_after_generate")
            if kind == "COMBO" and any(opts.get(f) for f in UPLOAD_FLAGS):
                out.append("upload")
    return out


def spec_lookup():
    """type -> widget names, from the running ComfyUI (None outside ComfyUI)."""
    try:
        import nodes
        mapping = nodes.NODE_CLASS_MAPPINGS
    except Exception:
        return lambda t: None
    cache = {}

    def look(t):
        if t not in cache:
            cls = mapping.get(t)
            try:
                cache[t] = widget_names(cls.INPUT_TYPES()) if cls is not None else None
            except Exception:
                cache[t] = None
        return cache[t]
    return look


def _params(node, names):
    vals = node.get("widgets_values")
    if isinstance(vals, dict):
        return {str(k): v for k, v in vals.items() if not str(k).startswith("_")}, True
    vals = list(vals or [])
    if names is not None and 0 < len(vals) <= len(names):
        # the frontend fills widgets in order; a workflow saved before a widget was added simply has fewer values
        return dict(zip(names, vals)), True
    return {f"#{i + 1}": v for i, v in enumerate(vals)}, not vals


# --------------------------------------------------------------------------------------------- walk
def named_nodes(workflow, lookup=None) -> list:
    """Every node with where / state / params; subgraph contents follow their instance's state."""
    if not isinstance(workflow, dict):
        return []
    lookup = lookup or spec_lookup()
    defs = {d.get("id"): d for d in ((workflow.get("definitions") or {}).get("subgraphs") or []) if isinstance(d, dict)}
    out, seen = [], set()

    def visit(nodes_, where, parent_off, depth):
        for n in nodes_ or []:
            if not isinstance(n, dict):
                continue
            t = str(n.get("type") or "")
            mode = n.get("mode") or 0
            state = STATES.get(mode, f"mode {mode}")
            eff = "off (subgraph " + parent_off + ")" if parent_off and state == "on" else state
            sub = defs.get(t)
            title = n.get("title") or (sub.get("name") if sub else None)
            if t not in SKIP_TYPES:
                if sub:   # promoted widgets: named only when they match the subgraph's widget inputs one to one
                    names = [str(i.get("name")) for i in sub.get("inputs") or [] if i.get("type") in WIDGET_TYPES]
                    vals = n.get("widgets_values")
                    names = names if isinstance(vals, list) and len(vals) == len(names) else None
                else:
                    names = lookup(t)
                params, named = _params(n, names)
                row = {"id": n.get("id"), "type": "subgraph" if sub else t, "title": title, "where": " > ".join(where),
                       "state": eff, "params": params}
                if not named:
                    row["positional"] = True
                role = (n.get("properties") or {}).get("h3md_role")
                if role:
                    row["role"] = role
                out.append(row)
            if sub is not None and depth < 8 and (t, tuple(where)) not in seen:
                seen.add((t, tuple(where)))
                off = parent_off or (state if state != "on" else None)
                visit(sub.get("nodes"), where + [str(sub.get("name") or t[:8])], off, depth + 1)

    visit(workflow.get("nodes"), [], None, 0)
    return out


def is_on(row) -> bool:
    return row.get("state") == "on"


# --------------------------------------------------------------------------------------------- examples
def load_examples(folder=EXAMPLE_DIR, lookup=None) -> dict:
    out = {}
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".json"))
    except OSError:
        return out
    for f in files:
        try:
            with open(os.path.join(folder, f), encoding="utf-8") as fh:
                out[f] = named_nodes(json.load(fh), lookup)
        except Exception:
            continue
    return out


def _key(row):
    return (row.get("type"), row.get("where"))


def best_example(user: list, examples: dict):
    """The example sharing the most (type, place) nodes with the user's workflow; ties: more id matches."""
    best, score = None, (0, 0)
    ukeys = [_key(r) for r in user]
    uids = {(r.get("id"), r.get("type")) for r in user}
    for name, rows in examples.items():
        pool = list(ukeys)
        hits = 0
        for r in rows:
            if _key(r) in pool:
                pool.remove(_key(r))
                hits += 1
        ids = sum(1 for r in rows if (r.get("id"), r.get("type")) in uids)
        if (hits, ids) > score:
            best, score = name, (hits, ids)
    return best


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6)
    if isinstance(a, str) and isinstance(b, str) and a.startswith("[") and b.startswith("["):
        try:
            return json.loads(a) == json.loads(b)
        except ValueError:
            pass
    return a == b


def _label(row) -> str:
    t = row.get("title") or row.get("type")
    w = row.get("where")
    return f"{t} (#{row.get('id')}{', ' + w if w else ''})"


def compare(user: list, example: list) -> dict:
    """Differences between the user's workflow and the example: switched, missing, added, changed, run values."""
    res = {"switched": [], "missing": [], "added": [], "changed": [], "run": []}
    left = list(user)

    def take(r):
        for cand in left:      # same id + type + place first, then the first of the same type + place
            if (cand.get("id"), _key(cand)) == (r.get("id"), _key(r)):
                left.remove(cand)
                return cand
        for cand in left:
            if _key(cand) == _key(r):
                left.remove(cand)
                return cand
        return None

    for r in example:
        u = take(r)
        if u is None:
            res["missing"].append({"node": _label(r), "example_state": r["state"]})
            continue
        if is_on(r) != is_on(u):
            res["switched"].append({"node": _label(u), "example": r["state"], "now": u["state"],
                                    "type": u.get("type"), "role": u.get("role")})
        if r.get("type") in RUN_TYPES:
            continue
        for k, v in u.get("params", {}).items():
            if k not in r.get("params", {}):
                continue
            ev = r["params"][k]
            if _same(ev, v):
                continue
            row = {"node": _label(u), "param": k, "example": ev, "now": v}
            (res["run"] if k in RUN_KEYS else res["changed"]).append(row)
    for u in left:
        res["added"].append({"node": _label(u), "state": u["state"]})
    return res


# --------------------------------------------------------------------------------------------- key switches
def _first(rows, pred):
    on = [r for r in rows if pred(r) and is_on(r)]
    return (on or [r for r in rows if pred(r)] or [None])[0]


def _loras(value) -> str:
    try:
        rows = json.loads(value) if isinstance(value, str) else value
        on = [f"{r.get('key') or r.get('lora')} {r.get('strength')}" for r in rows if r.get("on")]
        return ", ".join(on) or "none"
    except Exception:
        return "?"


def key_switches(rows: list, plan: dict | None = None) -> list:
    """(label, value) rows; 'not in workflow' / the node's state when a node is missing or off."""
    plan = plan or {}
    out = []

    def node_state(label, pred):
        r = _first(rows, pred)
        out.append((label, "not in workflow 工作流里没有" if r is None else r["state"]))
        return r

    def param(label, pred, key, fmt=None):
        r = _first(rows, pred)
        if r is None:
            out.append((label, "not in workflow 工作流里没有"))
            return
        v = r["params"].get(key, "?")
        text = fmt(v) if fmt else v
        out.append((label, text if is_on(r) else f"{text} (node {r['state']})"))

    by_type = lambda *ts: (lambda r: r.get("type") in ts)  # noqa: E731
    loop = by_type("H3MDLoopSamplerRefMod", "H3MDLoopSampler", "H3MDLoopSampler2")
    out.append(("Seamless long video 无接缝长视频 (plan)", bool(plan.get("windowMode"))))
    out.append(("Multi-person swap 多人换人 (plan)", plan.get("swapEngine") or "off"))
    param("Style 风格", by_type("H3MDControlPanel"), "style")
    cp = _first(rows, by_type("H3MDControlPanel"))
    if cp is not None:
        p = cp["params"]
        out.append(("Size 尺寸 (Control Panel)", f"{p.get('width')}x{p.get('height')}"))
        out.append(("Interpolation 补帧", f"{p.get('interpolate')} -> {p.get('fps')} fps"))
        out.append(("Face refine 修脸", p.get("face_refine")))
        out.append(("LoRAs low-res 低清 LoRA", _loras(p.get("lora_low"))))
        out.append(("LoRAs high-res 高清 LoRA", _loras(p.get("lora_high"))))
    param("High-res tiling 高清分块", loop, "highres_tiling")
    param("Low-res scale 低清比例", loop, "lowres_scale")
    param("Transition step 低清步数", loop, "transition_step")
    param("Upscaler 放大模型", loop, "upscaler_model")
    param("Cache 缓存", loop, "cache_mode")
    opts = by_type("H3MDLoopOptions")
    for key, label in (("tiling_axis", "Tiling axis 分块方向"), ("tiling_overlap", "Tiling overlap 分块重叠"),
                       ("ref_image_size", "Reference size 参考图尺寸"), ("cache_dtype", "Cache type 缓存类型"),
                       ("vram_release", "VRAM release 显存释放"),
                       ("release_vram_between_segments", "Release VRAM between segments 段间释放显存"),
                       ("ff_chunk_rows", "FF chunk rows 前馈分块"), ("lowres_anchor", "Low-res anchor 低清锚定")):
        param(label, opts, key)
    node_state("Remake model 换人模型", lambda r: r.get("role") == "remake_model")
    node_state("Motion control 动作控制", by_type("H3MDMotionControl", "H3MDDepthControl"))
    node_state("Semantic bridge 语义桥", lambda r: "SemanticBridge" in str(r.get("type")))
    node_state("Redraw / Preview 局部重绘", by_type("H3MDRedrawPreview"))
    node_state("Save / face refine node 保存节点", by_type("H3MDSaveVideoFaceRefineInterp"))
    return out


def settings_findings(rows: list, diff: dict | None, plan: dict | None, vram_gb) -> list:
    """Rules on the settings themselves (dicts like the report's findings)."""
    F = []
    plan = plan or {}
    loop = _first(rows, lambda r: r.get("type") in ("H3MDLoopSamplerRefMod", "H3MDLoopSampler", "H3MDLoopSampler2"))
    if loop is not None and loop["params"].get("highres_tiling") is False:
        sev = "warn" if not vram_gb or vram_gb < 40 else "info"
        F.append({"sev": sev, "cat": "settings", "where": _label(loop),
                  "text": "high-res tiling is off: the high-res pass renders the whole frame at once and can run out of "
                          "VRAM on long or large shots (the example workflows keep it on) 高清分块关着，长镜头 / 大画面容易爆显存"})
    if plan.get("swapEngine") == "remake":
        rm = _first(rows, lambda r: r.get("role") == "remake_model")
        if rm is None or not is_on(rm):
            F.append({"sev": "error", "cat": "settings", "where": "Remake model",
                      "text": "the plan is a multi-person swap but the Remake model loader is "
                              + ("missing" if rm is None else rm["state"]) + " (choose the 多人互换 style)"})
    for s in (diff or {}).get("switched", []):
        if s.get("role") == "remake_model":
            continue          # follows the style; covered above
        if str(s.get("type", "")).startswith("H3MD") or s.get("type") in ("UNETLoader", "VAELoader", "CLIPLoader"):
            F.append({"sev": "info", "cat": "settings", "where": s["node"],
                      "text": f"{s['example']} in the example workflow, {s['now']} here 示例工作流里是 {s['example']}，这里是 {s['now']}"})
    return F


def settings_markdown(rows: list, diff: dict | None, example_name) -> str:
    """settings.md: every node and every parameter, the example's value next to changed ones."""
    changed = {(c["node"], c["param"]): c["example"] for c in (diff or {}).get("changed", []) + (diff or {}).get("run", [])}
    L = ["# H3MD workflow settings 工作流全部设置", "",
         f"Compared with 对照: `{example_name or 'no example matched 没有匹配的示例'}` · "
         "state 状态: on 开 / bypassed 旁路 / muted 静音 / off (subgraph …) 所在子图关着 · "
         "**bold** = differs from the example 和示例不同", ""]
    for r in rows:
        head = f"## {_label(r)} - {r['type']} - **{r['state']}**"
        L += [head, ""]
        if not r["params"]:
            L += ["(no settings 无参数)", ""]
            continue
        if r.get("positional"):
            L.append("(names not known here 参数名未知: node not installed or different version)")
        L += ["| parameter 参数 | value 值 | example 示例 |", "|---|---|---|"]
        for k, v in r["params"].items():
            key = (_label(r), k)
            val = str(v).replace("|", "/").replace("\n", " ")
            if len(val) > 300:
                val = val[:300] + " …"
            if key in changed:
                L.append(f"| {k} | **{val}** | {str(changed[key]).replace('|', '/')[:300]} |")
            else:
                L.append(f"| {k} | {val} | |")
        L.append("")
    return "\n".join(L)
