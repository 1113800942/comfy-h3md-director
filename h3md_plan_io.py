"""H3MD plan export / import (v0.2.0).

A saved plan is a plain .zip:

    manifest.json   format, plan_version, name, date, H3MD version, size, shots, frames, seconds, seed,
                    the media list (with sizes, and whether each file is inside the zip) and the models the
                    workflow needs (loader file names + the workflow's own "models" list)
    plan.json       the Director plan exactly as in plan_json (media paths as they were on the exporting machine)
    workflow.json   the whole ComfyUI workflow (all node settings: LoRAs and strengths, seed, Loop options, ...)
    settings.json   a readable summary of every node's widget values (type, title, values)
    thumbnail.jpg   first frame of the newest finished video of this workflow (only when one exists)
    media/...       pictures, songs and (optionally) reference videos  [Export plan + media only]

Never in the zip: finished videos, caches, RefMod files and model weights (listed in the manifest instead).

Import accepts that .zip, or a bare plan .json (what an agent writes with the H3MD prompt skill).  Media from
the zip go to  input/H3MD_plans/<name>/  (an existing folder is never overwritten: <name>_2, _3, ...), and the
plan's file paths are rewritten to point there.  Problems (lengths not 5+17n, wrong overlaps, <Picture N> tags
without a picture, missing files, ...) are listed, never fixed silently.

Everything here is plain Python (zipfile / json / PyAV for the thumbnail) so it is unit-testable without ComfyUI.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import os
import re
import urllib.parse
import zipfile

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Plan]"

FORMAT = "h3md-plan"
PLAN_VERSION = 1
PLANS_SUBDIR = "H3MD_plans"
FIXED = ("manifest.json", "plan.json", "workflow.json", "settings.json", "thumbnail.jpg")
MEDIA_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff",
              ".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v",
              ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")
MAX_FILE_BYTES = 4 * 1024 ** 3        # one member
MAX_TOTAL_BYTES = 8 * 1024 ** 3       # whole plan, unpacked
BIG_VIDEO_BYTES = 200 * 1024 ** 2     # reference videos above this need include_big_videos
SHARED_MAX_BYTES = 1 * 1024 ** 3      # v0.2.1: shared / server ComfyUI (h3md_paths.shared): member and total cap


def _paths():
    try:
        from . import h3md_paths as PATHS
    except ImportError:
        import h3md_paths as PATHS
    return PATHS


def limits() -> tuple:
    """(one member, whole plan) byte limits: lower on a shared ComfyUI, where an upload must not fill the disk."""
    if _paths().shared():
        return SHARED_MAX_BYTES, SHARED_MAX_BYTES
    return MAX_FILE_BYTES, MAX_TOTAL_BYTES
KINDS = ("images", "videos", "audios")
FPS = 24
TOKEN_RE = re.compile(r"<(Picture|Video|Audio) (\d+)>")
MODEL_EXTS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft", ".onnx")


# ------------------------------------------------------------------ helpers
def h3md_version() -> str:
    try:
        txt = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pyproject.toml"), encoding="utf-8").read()
        m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def safe_name(name: str, fallback: str = "plan") -> str:
    s = re.sub(r"[^\w\-. ]+", "_", str(name or ""), flags=re.UNICODE).strip(" ._")
    return (s or fallback)[:60]


def _load_plan(plan_json) -> dict:
    plan = plan_json if isinstance(plan_json, dict) else json.loads(plan_json or "{}")
    if not isinstance(plan, dict):
        raise ValueError(f"{_TAG} the plan must be a JSON object")
    return plan


def plan_layout(plan: dict):
    """(shots, frames, seconds) as the Director runs them: each shot after the first adds length - overlapIn."""
    segs = [s for s in plan.get("segments") or [] if isinstance(s, dict)]
    frames = 0
    for i, s in enumerate(segs):
        ln = int(s.get("length") or 0)
        frames += ln if i == 0 else max(0, ln - int(s.get("overlapIn") or 0))
    return len(segs), frames, round(frames / float(plan.get("fps") or FPS), 2)


def media_items(plan: dict):
    """[(kind, id, file)] for pictures / videos / audios (RefMods are listed separately)."""
    out = []
    for k in KINDS:
        for it in (plan.get("media") or {}).get(k) or []:
            if isinstance(it, dict) and it.get("file"):
                out.append((k, str(it.get("id") or ""), str(it["file"])))
    return out


def refmod_files(plan: dict):
    return [str(it.get("file")) for it in (plan.get("media") or {}).get("refmods") or []
            if isinstance(it, dict) and it.get("file")]


def resolve_in(base: str, file: str):
    """Relative paths must stay inside base (the input folder); absolute paths only for media files inside
    ComfyUI's input / output / temp folders or the H3MD config folders (v0.3.7: an export never packs other files)."""
    if not file:
        return None
    if os.path.isabs(file):
        if not file.lower().endswith(MEDIA_EXTS) or not _paths().may_read(file):
            return None
        return file if os.path.isfile(file) else None
    cand = _paths().under(base, file)
    return cand if cand and os.path.isfile(cand) else None


def _graph_nodes(workflow):
    """All nodes, including nodes inside subgraph definitions."""
    if not isinstance(workflow, dict):
        return []
    nodes = list(workflow.get("nodes") or [])
    for sg in ((workflow.get("definitions") or {}).get("subgraphs") or []):
        nodes += list(sg.get("nodes") or [])
    return [n for n in nodes if isinstance(n, dict)]


def workflow_models(workflow) -> list:
    """Model files the workflow names: the workflow's own 'models' lists plus every loader value that looks like a file."""
    seen, out = set(), []

    def add(name, directory=None, url=None):
        key = (str(name), directory)
        if key in seen:
            return
        seen.add(key)
        d = {"name": str(name)}
        if directory:
            d["directory"] = directory
        if url:
            d["url"] = url
        out.append(d)

    for n in _graph_nodes(workflow):
        if n.get("mode") in (2, 4):          # muted / bypassed: not needed to run
            continue
        for m in (n.get("properties") or {}).get("models") or []:
            if isinstance(m, dict) and m.get("name"):
                add(m["name"], m.get("directory"), m.get("url"))
        vals = n.get("widgets_values")
        vals = vals.values() if isinstance(vals, dict) else (vals or [])
        for v in vals:
            if isinstance(v, str) and v.lower().endswith(MODEL_EXTS) and len(v) < 300:
                add(v)
            elif isinstance(v, dict) and v.get("on", True) and isinstance(v.get("lora"), str) \
                    and v.get("lora").lower().endswith(MODEL_EXTS):
                add(v["lora"], "loras")   # rgthree Power Lora Loader rows
    for m in (workflow or {}).get("models") or []:
        if isinstance(m, dict) and m.get("name"):
            add(m["name"], m.get("directory"), m.get("url"))
    return out


def workflow_seed(workflow):
    """Seed of the H3MD Loop node (widget order: rerun_segments, stop_after, rerun_new_seed, seed, ...)."""
    for n in _graph_nodes(workflow):
        if str(n.get("type", "")).startswith("H3MDLoopSampler"):
            v = n.get("widgets_values")
            if isinstance(v, list) and len(v) > 3 and isinstance(v[3], (int, float)):
                return int(v[3])
    return None


def workflow_settings(workflow) -> list:
    out = []
    for n in _graph_nodes(workflow):
        if n.get("type") in ("Note", "MarkdownNote") or n.get("mode") == 4:
            continue
        vals = n.get("widgets_values")
        if not vals:
            continue
        if n.get("type", "").startswith("H3MDDirector") and isinstance(vals, list) and vals and isinstance(vals[0], str):
            vals = ["<plan.json>"] + list(vals[1:])
        out.append({"id": n.get("id"), "type": n.get("type"), "title": n.get("title"), "widgets_values": vals})
    return out


def newest_video(output_dir: str, prefixes) -> str | None:
    """Newest finished video whose name starts with one of the workflow's save prefixes."""
    best = None
    for p in prefixes or []:
        p = str(p or "").replace("\\", "/").strip("/")
        if not p:
            continue
        sub, stem = os.path.split(p)
        d = os.path.abspath(os.path.join(output_dir, sub))
        if os.path.commonpath((os.path.abspath(output_dir), d)) != os.path.abspath(output_dir) or not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.startswith(stem) and f.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                fp = os.path.join(d, f)
                t = os.path.getmtime(fp)
                if best is None or t > best[0]:
                    best = (t, fp)
    return best[1] if best else None


def thumbnail_jpeg(video_path: str, width: int = 360) -> bytes | None:
    try:
        import av
        from PIL import Image
        with av.open(video_path) as c:
            for fr in c.decode(c.streams.video[0]):
                im = fr.to_image()
                break
            else:
                return None
        h = max(2, round(im.height * width / im.width))
        buf = io.BytesIO()
        im.convert("RGB").resize((width, h), Image.LANCZOS).save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception as exc:
        LOG.info("%s no thumbnail (%s)", _TAG, exc)
        return None


# ------------------------------------------------------------------ checks (shared by import and the agent .json)
def check_plan(plan: dict, media_present=None) -> list:
    """Human-readable problems; nothing is changed.  media_present: callable(file) -> bool, or None to skip."""
    probs = []
    segs = plan.get("segments")
    if not isinstance(segs, list) or not segs:
        return ["the plan has no shots (segments)"]
    media = plan.get("media") or {}
    ids = {k: {str(it.get("id")) for it in media.get(k) or [] if isinstance(it, dict)} for k in KINDS}
    for i, s in enumerate(segs, 1):
        if not isinstance(s, dict):
            probs.append(f"shot {i}: not an object")
            continue
        ln = s.get("length")
        if not isinstance(ln, int) or ln < 5 or (ln - 5) % 17:
            probs.append(f"shot {i}: length {ln} is not 5 + 17n frames (e.g. 294, 345, 362, 396)")
        ov = int(s.get("overlapIn") or 0)
        if i == 1 and ov:
            probs.append(f"shot 1: overlapIn must be 0 (got {ov})")
        if i > 1 and ov not in (0, 22, 39):
            probs.append(f"shot {i}: overlapIn {ov} - use 39 (continue the previous shot), 22, or 0 (hard cut)")
        n = {"Picture": len(s.get("images") or []), "Video": len(s.get("videos") or []),
             "Audio": len(s.get("audios") or [])}
        # a reference video's own soundtrack takes an <Audio> number too: the editor/backend validator handles
        # that exactly; here only clear overshoots are reported
        n["Audio"] += sum(1 for v in s.get("videos") or [] if isinstance(v, dict) and v.get("useAudio", True))
        for kind, num in TOKEN_RE.findall(str(s.get("prompt") or "")):
            if int(num) > n[kind]:
                probs.append(f"shot {i}: prompt uses <{kind} {num}> but the shot has {n[kind]} {kind.lower()}(s)")
        for k in KINDS:
            for ref in s.get(k) or []:
                rid = (ref.get("id") or ref.get("ref")) if isinstance(ref, dict) else ref
                if rid is not None and str(rid) not in ids[k]:
                    probs.append(f"shot {i}: {k[:-1]} '{rid}' is not in media.{k}")
    if media_present is not None:
        for kind, mid, f in media_items(plan):
            if not media_present(f):
                probs.append(f"missing file: {f} ({kind[:-1]} '{mid}') - upload it, then pick it in the library")
    return probs


# ------------------------------------------------------------------ export
def export_plan(plan_json, workflow, input_dir: str, with_media: bool = True, include_big_videos: bool = False,
                name: str = "", thumbnail_video: str | None = None) -> tuple[bytes, dict]:
    """-> (zip bytes, manifest).  Media files that cannot be found are listed in the manifest, not fatal."""
    plan = _load_plan(plan_json)
    shots, frames, seconds = plan_layout(plan)
    name = safe_name(name or plan.get("name") or "H3MD plan")
    entries, used = [], set()
    for kind, mid, f in media_items(plan):
        path = resolve_in(input_dir, f)
        e = {"kind": kind, "id": mid, "file": f, "bytes": os.path.getsize(path) if path else None, "included": False}
        if path and with_media and (kind != "videos" or include_big_videos or e["bytes"] <= BIG_VIDEO_BYTES):
            base = os.path.basename(path)
            stem, ext = os.path.splitext(base)
            k = 2
            while base.lower() in used:
                base = f"{stem}_{k}{ext}"
                k += 1
            used.add(base.lower())
            e["included"], e["zip"] = True, "media/" + base
            e["_path"] = path
        elif path is None:
            e["missing"] = True
        elif with_media:
            e["skipped"] = "reference video over 200 MB (tick 'include big videos' to add it)"
        entries.append(e)
    thumb = thumbnail_jpeg(thumbnail_video) if thumbnail_video else None
    manifest = {
        "format": FORMAT, "plan_version": PLAN_VERSION, "name": name,
        "created": _dt.datetime.now().isoformat(timespec="seconds"), "h3md_version": h3md_version(),
        "width": plan.get("width"), "height": plan.get("height"), "fps": plan.get("fps") or FPS,
        "shots": shots, "frames": frames, "seconds": seconds,
        "seed": workflow_seed(workflow), "with_media": bool(with_media), "thumbnail": bool(thumb),
        "media": [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries],
        "refmods": refmod_files(plan), "models": workflow_models(workflow),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("plan.json", json.dumps(plan, ensure_ascii=False, indent=2))
        if workflow:
            z.writestr("workflow.json", json.dumps(workflow, ensure_ascii=False))
            z.writestr("settings.json", json.dumps(workflow_settings(workflow), ensure_ascii=False, indent=1))
        if thumb:
            z.writestr("thumbnail.jpg", thumb)
        for e in entries:
            if e.get("included"):
                comp = zipfile.ZIP_STORED if e["file"].lower().endswith((".mp4", ".mov", ".webm", ".mkv", ".mp3",
                                                                          ".m4a", ".jpg", ".jpeg", ".png", ".webp")) \
                    else zipfile.ZIP_DEFLATED
                z.write(e["_path"], e["zip"], compress_type=comp)
    return buf.getvalue(), manifest


def export_size(plan_json, input_dir: str, include_big_videos: bool = False) -> dict:
    """Bytes the media would add (for the 'size before export' line and the big-video question)."""
    plan = _load_plan(plan_json)
    total, big = 0, []
    for kind, mid, f in media_items(plan):
        p = resolve_in(input_dir, f)
        if not p:
            continue
        b = os.path.getsize(p)
        if kind == "videos" and b > BIG_VIDEO_BYTES:
            big.append({"file": f, "bytes": b})
            if not include_big_videos:
                continue
        total += b
    return {"media_bytes": total, "big_videos": big}


# ------------------------------------------------------------------ import
def _member_ok(name: str) -> bool:
    if name in FIXED:
        return True
    if not name.startswith("media/") or name.endswith("/"):
        return False
    rest = name[len("media/"):]
    return ("/" not in rest and "\\" not in rest and rest not in ("", ".", "..") and not rest.startswith(".")
            and rest.lower().endswith(MEDIA_EXTS))


def _unique_dir(root: str, name: str) -> str:
    d, k = os.path.join(root, name), 2
    while os.path.exists(d):
        d = os.path.join(root, f"{name}_{k}")
        k += 1
    return d


def read_plan_file(data: bytes, filename: str = "") -> dict:
    """Look inside a .zip / .json without writing anything: plan, manifest, thumbnail, workflow, problems."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            bad = [n for n in names if not _member_ok(n)]
            if "plan.json" not in names:
                raise ValueError(f"{_TAG} not an H3MD plan zip (no plan.json)")
            plan = _load_plan(z.read("plan.json").decode("utf-8"))
            manifest = json.loads(z.read("manifest.json").decode("utf-8")) if "manifest.json" in names else {}
            workflow = json.loads(z.read("workflow.json").decode("utf-8")) if "workflow.json" in names else None
            thumb = z.read("thumbnail.jpg") if "thumbnail.jpg" in names else None
            media = [n for n in names if n.startswith("media/") and _member_ok(n)]
        return {"kind": "zip", "plan": plan, "manifest": manifest, "workflow": workflow, "thumbnail": thumb,
                "media_members": media, "ignored": bad}
    text = data.decode("utf-8-sig")
    obj = json.loads(text)
    if isinstance(obj, dict) and isinstance(obj.get("plan"), (dict, str)) and "segments" not in obj:
        obj = _load_plan(obj["plan"])          # {"plan": {...}} wrapper
    plan = _load_plan(obj)
    return {"kind": "json", "plan": plan, "manifest": {}, "workflow": None, "thumbnail": None,
            "media_members": [], "ignored": []}


def import_plan(data: bytes, input_dir: str, filename: str = "") -> dict:
    """Unpack media to input/H3MD_plans/<name>/ and return the rewritten plan + workflow + problems."""
    info = read_plan_file(data, filename)
    plan, manifest = info["plan"], info["manifest"]
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    name = safe_name(manifest.get("name") or plan.get("name") or stem or "plan")
    folder_rel = None
    if info["media_members"]:
        root = os.path.join(os.path.abspath(input_dir), PLANS_SUBDIR)
        os.makedirs(root, exist_ok=True)
        dest = _unique_dir(root, name)
        folder_rel = f"{PLANS_SUBDIR}/{os.path.basename(dest)}"
        max_file, max_total = limits()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            total = 0
            for zi in z.infolist():
                if zi.filename in info["media_members"]:
                    if zi.file_size > max_file:
                        raise ValueError(f"{_TAG} {zi.filename} is too large ({zi.file_size} bytes)")
                    total += zi.file_size
            if total > max_total:
                raise ValueError(f"{_TAG} the plan's media are too large ({total} bytes)")
            os.makedirs(dest)
            written = 0
            for m in info["media_members"]:
                out = os.path.join(dest, os.path.basename(m))
                size = 0
                with z.open(m) as src, open(out, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        size += len(chunk)
                        written += len(chunk)
                        if size > max_file or written > max_total:   # sizes in the zip header can lie (zip bomb)
                            raise ValueError(f"{_TAG} {m} unpacks larger than its header says; import stopped")
                        dst.write(chunk)
        # rewrite plan paths: manifest maps original file -> media/<name>; without a manifest match by base name
        by_file = {e.get("file"): e.get("zip") for e in manifest.get("media") or [] if e.get("included")}
        have = {os.path.basename(m).lower(): os.path.basename(m) for m in info["media_members"]}
        for k in KINDS:
            for it in (plan.get("media") or {}).get(k) or []:
                if not isinstance(it, dict) or not it.get("file"):
                    continue
                z = by_file.get(it["file"])
                base = os.path.basename(z) if z else have.get(os.path.basename(str(it["file"]).replace("\\", "/")).lower())
                if base:
                    it["file"] = f"{folder_rel}/{base}"
    problems = check_plan(plan, media_present=lambda f: resolve_in(input_dir, f) is not None)
    for m in info["ignored"]:
        problems.append(f"ignored unexpected file in the zip: {m}")
    if manifest.get("plan_version", PLAN_VERSION) > PLAN_VERSION:
        problems.append(f"this plan was saved by a newer H3MD ({manifest.get('h3md_version')}); "
                        f"update H3MD if something is missing")
    return {"ok": True, "kind": info["kind"], "name": name, "folder": folder_rel,
            "plan_json": json.dumps(plan, ensure_ascii=False), "workflow": info["workflow"],
            "manifest": manifest, "thumbnail": info["thumbnail"], "problems": problems}


# ------------------------------------------------------------------ routes
def register() -> bool:
    try:
        import asyncio
        import base64
        from aiohttp import web
        from server import PromptServer
        import folder_paths
        routes = PromptServer.instance.routes
    except Exception as exc:
        LOG.debug("%s routes not registered: %s", _TAG, exc)
        return False

    def _in():
        return os.path.abspath(folder_paths.get_input_directory())

    @routes.post("/h3md/plan/export_size")
    async def h3md_plan_export_size(request):
        body = await request.json()
        try:
            out = await asyncio.get_running_loop().run_in_executor(
                None, export_size, body.get("plan_json", ""), _in(), bool(body.get("include_big_videos")))
            return web.json_response(out)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.post("/h3md/plan/export")
    async def h3md_plan_export(request):
        body = await request.json()

        def job():
            vid = newest_video(folder_paths.get_output_directory(), body.get("prefixes") or [])
            return export_plan(body.get("plan_json", ""), body.get("workflow"), _in(),
                               with_media=bool(body.get("with_media", True)),
                               include_big_videos=bool(body.get("include_big_videos")),
                               name=body.get("name", ""), thumbnail_video=vid)
        try:
            data, manifest = await asyncio.get_running_loop().run_in_executor(None, job)
        except Exception as exc:
            LOG.warning("%s export failed: %s", _TAG, exc)
            return web.json_response({"error": str(exc)}, status=400)
        fname = manifest["name"] + (" (plan + media)" if manifest["with_media"] else " (plan)") + ".zip"
        return web.Response(body=data, content_type="application/zip", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(fname)})

    @routes.post("/h3md/plan/import")
    async def h3md_plan_import(request):
        try:
            reader = await request.multipart()
            field = await reader.next()
            while field is not None and field.name != "file":
                field = await reader.next()
            if field is None:
                return web.json_response({"ok": False, "error": "no file"}, status=400)
            filename = field.filename or ""
            data = bytearray()
            while True:
                chunk = await field.read_chunk(1 << 20)
                if not chunk:
                    break
                data += chunk
                if len(data) > limits()[1]:
                    return web.json_response({"ok": False, "error": "file too large"}, status=400)
            out = await asyncio.get_running_loop().run_in_executor(None, import_plan, bytes(data), _in(), filename)
        except Exception as exc:
            LOG.warning("%s import failed: %s", _TAG, exc)
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        if out.get("thumbnail"):
            out["thumbnail"] = "data:image/jpeg;base64," + base64.b64encode(out["thumbnail"]).decode()
        return web.json_response(out)

    return True
