"""H3MD diagnostic report (2026-11-17, H3MD 0.5.0): one click in the Director -> a .zip a user attaches to an issue.

report.md  - environment, settings, plan check (format + the Director's own notes + the prompt-skill rules), recent
             runs (H3MD log lines, errors, timings, VRAM peak) and a summary ranked by severity
report.json, plan.json, settings.json - the same data for tools / agents

Privacy (YJ 11-17): a fixed whitelist only.  No disk scans, no folder listings, no environment variables, no user /
computer names, IPs or accounts; node packs only from a fixed H3MD-related list; models only "found / not found" by
file name; every path in logs, settings and the plan is cut to its file name; no pictures, videos or audio; prompts
only when the user leaves them in.  The report is written to the user's own output folder and downloaded - never sent
anywhere."""
from __future__ import annotations

import collections
import datetime as _dt
import io
import json
import logging
import math
import os
import platform
import re
import time
import zipfile

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Report]"
REPORT_SUBDIR = "H3MD_reports"
MAX_LINES = 4000
SEV = {"error": 0, "warn": 1, "info": 2}
CATEGORIES = {"env": "environment 环境", "settings": "settings 设置", "plan": "plan 计划",
              "skill": "prompt skill rule 不符合 skill", "run": "run error 运行出错"}

# H3MD-related node packs: (label, marker node classes) - nothing else is listed
PACKS = [
    ("ComfyUI core MiniMax H3", ("MiniMaxH3AddGuide",)),
    ("ComfyUI core MiniMax H3 context windows", ()),     # checked by import (window mode / CCW)
    ("ComfyUI-Viggle-Remake (multi-person swap)", ("RemakeConditioning",)),
    ("MiniMax H3 RefMods", ("MiniMaxH3RefModsLoader",)),
    ("ComfyUI-H3-FaceRefine", ("H3FaceTrackCrop",)),
    ("ComfyUI-H3-FaceRefine-Plus", ("H3FaceStitchPlus",)),
    ("RIFE frame interpolation", ("RIFEInterpolation",)),
    ("Depth Anything V2 (controlnet_aux)", ("DepthAnythingV2Preprocessor",)),
    ("VideoHelperSuite", ("VHS_VideoCombine",)),
    ("KJNodes", ("PathchSageAttentionKJ",)),
    ("Qwen Image 2.1 prompt enhancer", ("QwenImage21PromptEnhancer",)),
]
SAM3_CKPT = "sam3.1_multiplex_fp16.safetensors"


# --------------------------------------------------------------------------------------------- log buffer
class _Ring(logging.Handler):
    """One handler on the root logger: H3MD records (any level) + ERROR records of any other logger (ComfyUI's
    tracebacks), the last MAX_LINES kept in memory only."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = collections.deque(maxlen=MAX_LINES)

    def emit(self, record):
        try:
            if record.name != "H3MD" and record.levelno < logging.ERROR:
                return
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            self.lines.append((record.created, record.levelname, record.name, msg))
        except Exception:
            pass


_RING = None


def attach():
    """Called once when the routes are registered (ComfyUI start)."""
    global _RING
    if _RING is None:
        _RING = _Ring()
        logging.getLogger().addHandler(_RING)
    return _RING


# --------------------------------------------------------------------------------------------- privacy
_QUOTED_PATH = re.compile(r"([\"'])((?:[A-Za-z]:|\\\\|/)[^\"'\n]*?)\1")
_WIN_PATH = re.compile(r"(?<![A-Za-z])(?:[A-Za-z]:|\\\\[^\\\s]+)[\\/][^\s\"'<>|*?,;]*")
_BACKSLASH_TOKEN = re.compile(r"[^\s\"'<>|]*\\[^\s\"'<>|]*")
_NIX_PATH = re.compile(r"(?<![\w./:])/(?:[^\s\"'<>|:,;/]+/)+[^\s\"'<>|:,;]*")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _tail_s(p):
    p = p.rstrip("\\/.")
    base = re.split(r"[\\/]", p)[-1]
    return ".../" + base if base else "..."


def _tail(m):
    return _tail_s(m.group(0))


def scrub(text):
    """paths -> '.../file name' (so no user folder names), e-mail and IP addresses removed"""
    if text is None:
        return text
    s = str(text)
    s = _QUOTED_PATH.sub(lambda m: m.group(1) + _tail_s(m.group(2)) + m.group(1), s)   # quoted: may hold spaces
    s = _WIN_PATH.sub(_tail, s)
    s = _BACKSLASH_TOKEN.sub(_tail, s)      # what is left of an unquoted Windows path with spaces
    s = _NIX_PATH.sub(_tail, s)
    s = _EMAIL.sub("<email>", s)
    s = _IPV4.sub("<ip>", s)
    return s


def scrub_obj(obj, keep_text=True):
    """recursively scrub strings; keep_text False: long free text (prompts) -> '<text, N chars>'"""
    if isinstance(obj, str):
        if not keep_text and len(obj) > 160 and not obj.lstrip().startswith(("[{", "{")):   # JSON (LoRA rows) stays
            return f"<text, {len(obj)} chars>"
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_obj(v, keep_text) for v in obj]
    if isinstance(obj, dict):
        return {k: scrub_obj(v, keep_text) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------------------------- environment
def _frontend_version():
    try:
        import comfyui_frontend_package as f
        return str(getattr(f, "__version__", None) or "?")
    except Exception:
        return "?"


def _pack_version(cls):
    """version from the pyproject.toml next to the node's module (path itself is not reported)"""
    try:
        import sys
        mod = sys.modules.get(getattr(cls, "__module__", ""))
        d = os.path.dirname(os.path.abspath(getattr(mod, "__file__", "") or ""))
        if not d or "comfy_extras" in d.replace("\\", "/").split("/"):
            return "core"
        for _ in range(3):
            p = os.path.join(d, "pyproject.toml")
            if os.path.isfile(p):
                m = re.search(r'^version\s*=\s*"([^"]+)"', open(p, encoding="utf-8").read(), re.M)
                return m.group(1) if m else "?"
            d = os.path.dirname(d)
    except Exception:
        pass
    return "?"


def environment() -> dict:
    env = {"h3md": _h3md_version(), "python": platform.python_version(),
           "os": f"{platform.system()} {platform.release()}"}
    try:
        import comfyui_version
        env["comfyui"] = str(comfyui_version.__version__)
    except Exception:
        env["comfyui"] = "?"
    env["frontend"] = _frontend_version()
    try:
        import torch
        env["torch"] = torch.__version__
        env["cuda"] = torch.version.cuda
        if torch.cuda.is_available():
            i = torch.cuda.current_device()
            p = torch.cuda.get_device_properties(i)
            env["gpu"] = p.name
            env["vram_total_gb"] = round(p.total_memory / 1024 ** 3, 1)
            free, _ = torch.cuda.mem_get_info(i)
            env["vram_free_now_gb"] = round(free / 1024 ** 3, 1)
            env["vram_peak_reserved_gb"] = round(torch.cuda.max_memory_reserved(i) / 1024 ** 3, 1)
            env["vram_peak_allocated_gb"] = round(torch.cuda.max_memory_allocated(i) / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        env["ram_total_gb"] = round(vm.total / 1024 ** 3, 1)
        env["ram_free_now_gb"] = round(vm.available / 1024 ** 3, 1)
    except Exception:
        pass
    try:
        import comfy.model_management as mm
        env["vram_mode"] = getattr(getattr(mm, "vram_state", None), "name", "?")
        env["comfy_reserved_gb"] = round(float(mm.extra_reserved_memory()) / 1024 ** 3, 2)
    except Exception:
        pass
    try:
        from comfy.cli_args import args
        flags = {}
        for k in ("lowvram", "novram", "highvram", "gpu_only", "reserve_vram", "vram_headroom", "use_sage_attention",
                  "use_flash_attention", "use_pytorch_cross_attention", "disable_smart_memory", "cache_classic",
                  "cache_lru", "cache_none", "fast"):
            v = getattr(args, k, None)
            if v:
                flags[k] = v if isinstance(v, (bool, int, float)) else str(v)
        env["comfy_flags"] = flags
    except Exception:
        pass
    env["node_packs"] = node_packs()
    return env


def node_packs() -> list:
    try:
        import nodes
        mp = nodes.NODE_CLASS_MAPPINGS
    except Exception:
        mp = {}
    out = []
    for label, markers in PACKS:
        cls = next((mp[m] for m in markers if m in mp), None)
        if not markers:
            try:
                from comfy_extras.nodes_context_windows import MiniMaxH3ContextWindowsNode as cls
            except Exception:
                cls = None
        out.append({"pack": label, "installed": cls is not None, "version": _pack_version(cls) if cls else None})
    return out


def _h3md_version():
    try:
        from . import h3md_plan_io as P
    except ImportError:
        import h3md_plan_io as P
    return P.h3md_version()


def check_models(models: list) -> list:
    """file names the workflow uses -> found / not found (no paths)"""
    try:
        import folder_paths
        folders = list(folder_paths.folder_names_and_paths.keys())
    except Exception:
        return [{"name": m.get("name"), "found": None} for m in models]
    out = []
    for m in models:
        name = str(m.get("name") or "")
        if not name:
            continue
        cand = [m["directory"]] if m.get("directory") in folders else folders
        hit = None
        for f in cand:
            try:
                if folder_paths.get_full_path(f, name):
                    hit = f
                    break
            except Exception:
                continue
        out.append({"name": os.path.basename(name), "found": hit is not None, "folder": hit})
    return out


# --------------------------------------------------------------------------------------------- plan check
def _D():
    try:
        from . import h3md_director as D
    except ImportError:
        import h3md_director as D
    return D


def _W():
    try:
        from . import h3md_window as W
    except ImportError:
        import h3md_window as W
    return W


def _RS():
    try:
        from . import h3md_report_settings as m
    except ImportError:
        import h3md_report_settings as m
    return m


def _P():
    try:
        from . import h3md_plan_io as P
    except ImportError:
        import h3md_plan_io as P
    return P


def plan_findings(plan: dict, width: int = 0, height: int = 0, env: dict | None = None) -> tuple[list, dict]:
    """-> ([{sev, cat, where, text}], summary).  Format, the Director's own parse (notes, errors), and the skill
    rules that can be checked from the plan alone."""
    F = []

    def add(sev, cat, where, text):
        F.append({"sev": sev, "cat": cat, "where": where, "text": scrub(text)})

    P, D, W = _P(), _D(), _W()
    for p in P.check_plan(plan):
        add("error" if "not 5 + 17n" in p or "no shots" in p else "warn", "plan", "plan", p)
    segs_raw = [s for s in plan.get("segments") or [] if isinstance(s, dict)]
    summary = {"shots": len(segs_raw), "width": plan.get("width"), "height": plan.get("height"),
               "swapEngine": plan.get("swapEngine"), "windowMode": bool(plan.get("windowMode")),
               "autoSplit": bool(plan.get("autoSplit"))}
    parsed = None
    try:
        raw = json.loads(json.dumps(plan))
        try:
            D.refresh_video_durations(raw)
        except Exception:
            pass
        parsed = D.parse_plan(raw, int(width or 0), int(height or 0), True, motion=None)
    except Exception as exc:
        add("error", "plan", "Director", f"the Director cannot read this plan: {exc}")
    if parsed is not None:
        summary.update({"parts_after_autosplit": len(parsed["segments"]), "total_frames": parsed["total_frames"],
                        "seconds": round(parsed["total_frames"] / D.FPS, 2), "width": parsed["width"],
                        "height": parsed["height"], "token_budget": parsed.get("tokenBudget")})
        for n in parsed.get("notes") or []:
            add("info", "plan", "Director note", n)
        _skill_rules(parsed, plan, add, D, W, env or {})
    return F, summary


def _skill_rules(parsed, raw, add, D, W, env):
    segs = parsed["segments"]
    fps = D.FPS
    media_v = {str(v.get("id")): v for v in (raw.get("media") or {}).get("videos") or [] if isinstance(v, dict)}
    # shot longer than its locked / reference video (the Director would run past the source)
    for i, s in enumerate(raw.get("segments") or [], 1):
        for v in s.get("videos") or []:
            mv = media_v.get(str(v.get("id")))
            dur = mv and mv.get("duration")
            if dur and v.get("useDuration"):
                if float(v["useDuration"]) > float(dur) - float(v.get("trimStart") or 0) + 0.05:
                    add("warn", "skill", f"shot {i}", f"uses {v['useDuration']} s of video '{v.get('id')}' but only "
                        f"{float(dur) - float(v.get('trimStart') or 0):.2f} s are there: the end has no source motion")
    # Remake (multi-person swap)
    if raw.get("swapEngine") == "remake":
        w, h = parsed["width"], parsed["height"]
        if (w, h) not in ((1664, 960), (960, 1664), (2560, 1472), (1472, 2560)):
            add("error", "settings", "Remake", f"output {w}x{h}: Remake needs 1664x960 / 960x1664 (or 2560x1472)")
        for k, s in enumerate(segs, 1):
            n = int(s["length"])
            if not 90 <= n <= 345:
                add("error", "plan", f"part {k}", f"{n} frames: Remake renders 90-345 frames per part (turn on autoSplit)")
            kf = s.get("keyframes") or []
            if not kf and not s.get("keyframesNear"):
                add("error", "skill", f"part {k}", "no keyframe in this part (skill 12d: one at most ~5-6 s apart)")
            elif not kf:
                add("warn", "skill", f"part {k}", "no keyframe of its own: the nearest one is borrowed un-pinned")
            if len(kf) > 4:
                add("warn", "skill", f"part {k}", f"{len(kf)} keyframes: Remake takes the first 4, the rest are dropped")
    # window mode: every window needs a timed action, none right at a window handoff (skill section 2)
    wm = parsed.get("windowMode")
    if wm:
        for k, s in enumerate(segs, 1):
            prompt = s.get("prompt") or ""
            total = int(s["length"])
            try:
                parts = W.split_timed_prompt(prompt)
                cuts = W.prompt_cuts(prompt)
                wins, spans = W.window_spans(total, int(wm["length"]), int(wm["overlap"]), cuts,
                                             bool(wm.get("chain")))
            except Exception as exc:
                add("info", "plan", f"shot {k}", f"window check skipped ({exc})")
                continue
            if len(wins) <= 1:
                continue
            acts = [t for t, _ in (parts[3] if parts else [])]
            if not acts:
                add("warn", "skill", f"shot {k}", f"window mode, {len(wins)} windows, but no timed actions "
                    "('[12s] ...' / 'At 00:12.0 ...'): each window improvises (skill section 2)")
                continue
            for j, (lo, hi) in enumerate(spans, 1):
                if not any(lo / fps <= t < hi / fps for t in acts):
                    add("warn", "skill", f"shot {k} window {j}", f"{lo / fps:.1f}-{hi / fps:.1f} s has no timed action: "
                        "that window improvises (give it one, e.g. a hold)")
            hand = [spans[j][0] / fps for j in range(1, len(spans))]
            for t in acts:
                near = [x for x in hand if abs(t - x) < 1.0]
                if near:
                    add("warn", "skill", f"shot {k}", f"timed action at {t:.1f} s is within 1 s of a window handoff "
                        f"({near[0]:.1f} s): big moves there can jump or ghost")
            for t in acts:
                if t > total / fps + 0.05:
                    add("warn", "skill", f"shot {k}", f"timed action at {t:.1f} s is after the shot's end "
                        f"({total / fps:.1f} s)")
    # VRAM: shots over the token budget the run will use (autosplit off -> they run as they are)
    budget = parsed.get("tokenBudget")
    vram = env.get("vram_total_gb")
    if not raw.get("autoSplit") and vram:
        for k, s in enumerate(segs, 1):
            try:
                tok = D.canvas_tokens(parsed["width"], parsed["height"], int(s["length"]))
            except Exception:
                continue
            if budget and tok > budget:
                add("warn", "settings", f"shot {k}", f"{tok} canvas tokens over the budget {budget}: may run out of "
                    "VRAM - turn on autoSplit or shorten the shot")


# --------------------------------------------------------------------------------------------- runs
def recent_runs(n: int = 3) -> list:
    """last runs from ComfyUI's history: status, time, the error (node, message, traceback) - scrubbed"""
    try:
        from server import PromptServer
        hist = PromptServer.instance.prompt_queue.get_history(max_items=n)
    except Exception:
        return []
    out = []
    for pid, h in list(hist.items())[-n:]:
        st = h.get("status") or {}
        r = {"status": st.get("status_str"), "completed": st.get("completed")}
        t0 = t1 = None
        for m in st.get("messages") or []:
            kind, data = m[0], m[1] if len(m) > 1 else {}
            ts = (data or {}).get("timestamp")
            if kind == "execution_start":
                t0 = ts
            elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                t1 = ts
            if kind == "execution_error":
                r["error"] = {"node_type": data.get("node_type"), "node_id": data.get("node_id"),
                              "message": scrub(data.get("exception_message")),
                              "type": str(data.get("exception_type") or "").split(".")[-1],
                              "traceback": scrub("".join(data.get("traceback") or []))[-6000:]}
            if kind == "execution_interrupted":
                r["interrupted"] = True
        if t0 and t1:
            r["seconds"] = round((t1 - t0) / 1000.0, 1)
        out.append(r)
    return out


def log_lines(limit: int = 600) -> list:
    if _RING is None:
        return []
    rows = list(_RING.lines)[-limit:]
    return [f"{_dt.datetime.fromtimestamp(t).strftime('%H:%M:%S')} {lvl[0]} {scrub(msg)}" for t, lvl, name, msg in rows]


def run_findings(runs, lines) -> list:
    F = []
    for r in runs[-1:]:
        e = r.get("error")
        if e:
            msg = e.get("message") or ""
            oom = "out of memory" in msg.lower() or "OutOfMemory" in str(e.get("type"))
            F.append({"sev": "error", "cat": "env" if oom else "run", "where": f"last run, node {e.get('node_type')}",
                      "text": ("out of GPU memory: " if oom else "") + msg[:600]})
    for ln in lines[-400:]:
        if " W " in ln[:12] and ("[H3MD" in ln) and any(k in ln for k in ("OOM", "VRAM", "not found", "older than",
                                                                           "missing")):
            F.append({"sev": "warn", "cat": "env", "where": "log", "text": ln[9:][:400]})
    return F[-30:]


# --------------------------------------------------------------------------------------------- report
def build(plan_json, workflow=None, include_prompts: bool = True, width: int = 0, height: int = 0) -> dict:
    P = _P()
    plan = plan_json if isinstance(plan_json, dict) else json.loads(plan_json or "{}")
    env = environment()
    models = check_models(P.workflow_models(workflow) if workflow else [])
    if any(s.get("depth") for seg in plan.get("segments") or [] for s in seg.get("videos") or []
           if isinstance(s, dict)) or plan.get("swapEngine"):
        models += check_models([{"name": SAM3_CKPT, "directory": "checkpoints"}]) if not any(
            m["name"] == SAM3_CKPT for m in models) else []
    settings, example, diff, keys, sf = _settings_part(workflow, plan, include_prompts, env)
    pf, summary = plan_findings(plan, width, height, env)
    runs = recent_runs()
    lines = log_lines()
    F = pf + sf + run_findings(runs, lines)
    for m in models:
        if m.get("found") is False:
            F.append({"sev": "warn", "cat": "env", "where": "models", "text": f"model file not found: {m['name']} "
                      "(needed only if that node runs)"})
    for p in env.get("node_packs") or []:
        if p["pack"].startswith("ComfyUI core MiniMax H3") and not p["installed"]:
            F.append({"sev": "error", "cat": "env", "where": "ComfyUI", "text": f"{p['pack']} missing: update ComfyUI"})
        if p["pack"].startswith("ComfyUI-Viggle-Remake") and not p["installed"] and plan.get("swapEngine") == "remake":
            F.append({"sev": "error", "cat": "env", "where": "Remake", "text": "the plan uses the Remake swap but "
                      "ComfyUI-Viggle-Remake is not installed"})
    F.sort(key=lambda f: (SEV.get(f["sev"], 9), f["cat"]))
    plan_out = scrub_obj(plan, keep_text=include_prompts)
    return {"created": _dt.datetime.now().isoformat(timespec="seconds"), "h3md": env.get("h3md"),
            "prompts_included": bool(include_prompts), "environment": env, "models": models, "plan_summary": summary,
            "findings": F, "recent_runs": runs, "log": lines, "settings": settings, "plan": plan_out,
            "example_workflow": example, "settings_diff": diff, "key_switches": keys}


def _settings_part(workflow, plan, include_prompts, env):
    """every node, named + state, compared with the closest shipped example workflow (0.5.1)"""
    RS = _RS()
    if not workflow:
        return [], None, None, [], []
    look = RS.spec_lookup()
    clean = lambda rows: scrub_obj(rows, keep_text=include_prompts)  # noqa: E731

    def strip_plan(rows):
        for r in rows:
            if str(r.get("type", "")).startswith("H3MDDirector") and "plan_json" in r["params"]:
                r["params"]["plan_json"] = "<plan.json>"
        return rows
    rows = clean(strip_plan(RS.named_nodes(workflow, look)))
    examples = {k: clean(strip_plan(v)) for k, v in RS.load_examples(lookup=look).items()}
    example = RS.best_example(rows, examples)
    diff = RS.compare(rows, examples[example]) if example else None
    keys = [[k, v] for k, v in RS.key_switches(rows, plan)]
    sf = RS.settings_findings(rows, diff, plan, env.get("vram_total_gb"))
    return rows, example, diff, keys, sf


def to_markdown(r: dict) -> str:
    e = r["environment"]
    L = [f"# H3MD diagnostic report / 诊断报告", "",
         f"Created {r['created']} · H3MD {e.get('h3md')} · prompts {'included' if r['prompts_included'] else 'left out'}",
         "", "> Privacy: no user or computer names, no folders, no pictures / videos / audio; paths are cut to file "
             "names. 不含用户名、电脑名、文件夹和任何图片视频音频；路径只留文件名。", ""]
    L += ["## 1. Summary 结论", ""]
    if not r["findings"]:
        L += ["No problems found. 没有发现问题。", ""]
    else:
        L += ["| | category 类别 | where 位置 | finding 问题 |", "|---|---|---|---|"]
        icon = {"error": "❌", "warn": "⚠️", "info": "ℹ️"}
        for f in r["findings"]:
            L.append(f"| {icon.get(f['sev'], '')} | {CATEGORIES.get(f['cat'], f['cat'])} | {f['where']} | "
                     f"{str(f['text']).replace('|', '/').replace(chr(10), ' ')} |")
        L.append("")
    L += _settings_md(r)
    L += ["## 2. Environment 环境", ""]
    for k in ("comfyui", "frontend", "python", "torch", "cuda", "os", "gpu", "vram_total_gb", "vram_free_now_gb",
              "vram_peak_reserved_gb", "vram_peak_allocated_gb", "ram_total_gb", "ram_free_now_gb", "vram_mode",
              "comfy_reserved_gb", "comfy_flags"):
        if k in e:
            L.append(f"- **{k}**: {e[k]}")
    L += ["", "| node pack 节点包 | installed 已装 | version 版本 |", "|---|---|---|"]
    for p in e.get("node_packs") or []:
        L.append(f"| {p['pack']} | {'yes' if p['installed'] else 'no'} | {p['version'] or ''} |")
    L += ["", "Models the workflow uses 工作流用到的模型:", ""]
    for m in r["models"]:
        L.append(f"- {m['name']}: {'found (' + str(m.get('folder')) + ')' if m.get('found') else 'NOT FOUND' if m.get('found') is False else '?'}")
    s = r["plan_summary"]
    L += ["", "## 3. Plan 计划", "", "- " + ", ".join(f"{k}: {v}" for k, v in s.items()), "",
          "Full plan: plan.json · every node and setting 全部节点和参数: settings.md / settings.json", ""]
    L += ["## 4. Recent runs 最近的运行", ""]
    for i, run in enumerate(r["recent_runs"], 1):
        L.append(f"- run {i}: {run.get('status')} {str(run.get('seconds', '?')) + ' s' if run.get('seconds') else ''}"
                 f"{' (interrupted)' if run.get('interrupted') else ''}")
        if run.get("error"):
            er = run["error"]
            L += [f"  - error in **{er.get('node_type')}** (node {er.get('node_id')}): {er.get('type')}: {er.get('message')}",
                  "", "```text", er.get("traceback") or "", "```"]
    L += ["", "## 5. Log 日志 (H3MD lines + errors)", "", "```text"] + r["log"][-600:] + ["```", ""]
    return "\n".join(L)


def _cell(v, n=120):
    t = str(v).replace("|", "/").replace("\n", " ")
    return t if len(t) <= n else t[:n] + " …"


def _settings_md(r: dict) -> list:
    L = []
    if r.get("key_switches"):
        L += ["## 1a. Key switches 关键开关", "", "| switch 开关 | value 值 |", "|---|---|"]
        L += [f"| {k} | {_cell(v, 200)} |" for k, v in r["key_switches"]]
        L.append("")
    d = r.get("settings_diff")
    if d is not None:
        L += [f"## 1b. Differences from the example workflow 和示例工作流不同的地方", "",
              f"Compared with 对照: `{r.get('example_workflow')}` (the shipped example closest to this workflow 随包示例里最像的一个)", ""]
        if not any(d.get(k) for k in ("switched", "missing", "added", "changed")):
            L += ["Same as the example (apart from per-run values) 和示例一样（每次运行会变的值除外）", ""]
        if d.get("switched"):
            L += ["**Nodes switched on / off 开关不同的节点**", "", "| node 节点 | example 示例 | here 这里 |", "|---|---|---|"]
            L += [f"| {_cell(x['node'])} | {x['example']} | **{x['now']}** |" for x in d["switched"]] + [""]
        if d.get("changed"):
            L += ["**Changed settings 改过的参数**", "", "| node 节点 | parameter 参数 | example 示例 | here 这里 |",
                  "|---|---|---|---|"]
            L += [f"| {_cell(x['node'])} | {x['param']} | {_cell(x['example'])} | **{_cell(x['now'])}** |"
                  for x in d["changed"]] + [""]
        if d.get("missing"):
            L += ["**In the example, not here 示例里有、这里没有**: " + "; ".join(_cell(x["node"]) for x in d["missing"]), ""]
        if d.get("added"):
            L += ["**Added here 这里多加的**: " + "; ".join(f"{_cell(x['node'])} ({x['state']})" for x in d["added"]), ""]
        if d.get("run"):
            L += ["Per-run values 本次运行的值 (normal 正常): " + "; ".join(
                f"{x['param']} = {_cell(x['now'], 60)}" for x in d["run"][:40]), ""]
    return L


def write_zip(r: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.md", to_markdown(r))
        z.writestr("report.json", json.dumps({k: v for k, v in r.items() if k not in ("plan", "settings")},
                                             ensure_ascii=False, indent=1))
        z.writestr("plan.json", json.dumps(r["plan"], ensure_ascii=False, indent=2))
        z.writestr("settings.json", json.dumps(r["settings"], ensure_ascii=False, indent=1))
        if r.get("settings"):
            z.writestr("settings.md", _RS().settings_markdown(r["settings"], r.get("settings_diff"),
                                                              r.get("example_workflow")))
    return buf.getvalue()


def save(data: bytes) -> str:
    import folder_paths
    d = os.path.join(folder_paths.get_output_directory(), REPORT_SUBDIR)
    os.makedirs(d, exist_ok=True)
    stem = "H3MD_report_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name, k = stem + ".zip", 2
    while os.path.exists(os.path.join(d, name)):
        name, k = f"{stem}_{k}.zip", k + 1
    with open(os.path.join(d, name), "wb") as f:
        f.write(data)
    return name


# --------------------------------------------------------------------------------------------- route
def register() -> bool:
    attach()
    try:
        import asyncio
        import urllib.parse
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception as exc:
        LOG.debug("%s route not registered: %s", _TAG, exc)
        return False

    @routes.post("/h3md/report")
    async def h3md_report(request):
        body = await request.json()

        def job():
            r = build(body.get("plan_json") or "{}", body.get("workflow"), bool(body.get("include_prompts", True)),
                      int(body.get("width") or 0), int(body.get("height") or 0))
            data = write_zip(r)
            return data, save(data), r
        try:
            data, name, r = await asyncio.get_running_loop().run_in_executor(None, job)
        except Exception as exc:
            LOG.warning("%s failed: %s", _TAG, exc)
            return web.json_response({"error": str(exc)}, status=400)
        counts = {k: sum(1 for f in r["findings"] if f["sev"] == k) for k in SEV}
        return web.Response(body=data, content_type="application/zip", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name),
            "X-H3MD-Findings": json.dumps(counts), "X-H3MD-Saved": f"output/{REPORT_SUBDIR}/{name}"})

    return True
