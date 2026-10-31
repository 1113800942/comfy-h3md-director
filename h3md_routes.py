"""H3MD editor helper routes (read-only): list input-folder media and probe one file.

GET /h3md/files?kind=image|video|audio   -> {"files": ["a.png", "sub/b.mp4", ...]}
GET /h3md/probe?file=<plan file>         -> {"exists", "kind", "duration", "hasAudio", "hasVideo",
                                             "width", "height", "fps"}
POST /h3md/autosplit {plan_json, width, height} -> per-shot token estimate + automatic parts (h3md_autosplit.preview)
Paths are relative to ComfyUI's input directory (the same rule the H3MD Director uses).
Nothing is written; the editor uploads through ComfyUI's own /upload/image endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import os

LOG = logging.getLogger("H3MD")

EXTS = {
    "image": (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"),
    "video": (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif"),
    "audio": (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"),
}
MAX_FILES = 5000


def _input_dir() -> str:
    import folder_paths
    return os.path.abspath(folder_paths.get_input_directory())


def list_input_files(kind: str, base: str | None = None) -> list:
    exts = EXTS.get(kind)
    if exts is None:
        raise ValueError("kind must be image, video or audio")
    base = base or _input_dir()
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in files:
            if name.lower().endswith(exts) and not name.startswith("."):
                rel = os.path.relpath(os.path.join(root, name), base).replace(os.sep, "/")
                out.append(rel)
                if len(out) >= MAX_FILES:
                    return sorted(out)
    return sorted(out, key=str.lower)


def _resolve(file: str, base: str) -> str | None:
    """Same lookup as h3md_director.resolve_media_path, but relative paths must stay inside input/."""
    if not file:
        return None
    if os.path.isabs(file):
        return file if os.path.isfile(file) else None
    cand = os.path.abspath(os.path.join(base, file))
    if os.path.commonpath((base, cand)) != base:
        return None
    return cand if os.path.isfile(cand) else None


def probe_file(file: str, base: str | None = None) -> dict:
    base = base or _input_dir()
    path = _resolve(file, base)
    info = {"file": file, "exists": path is not None}
    if path is None:
        return info
    low = path.lower()
    if low.endswith(EXTS["image"]) and not low.endswith(".gif"):
        info["kind"] = "image"
        try:
            from PIL import Image
            with Image.open(path) as im:
                info["width"], info["height"] = im.size
        except Exception as exc:
            info["error"] = str(exc)
        return info
    try:
        import av
        with av.open(path) as c:
            v = c.streams.video[0] if c.streams.video else None
            a = c.streams.audio[0] if c.streams.audio else None
            dur = None
            if c.duration:
                dur = float(c.duration) / 1_000_000.0
            for s in (v, a):
                if dur is None and s is not None and s.duration and s.time_base:
                    dur = float(s.duration * s.time_base)
            info.update(kind="video" if v is not None else "audio", hasVideo=v is not None,
                        hasAudio=a is not None, duration=None if dur is None else round(dur, 3))
            if v is not None:
                info["width"] = int(v.codec_context.width or 0)
                info["height"] = int(v.codec_context.height or 0)
                info["fps"] = float(v.average_rate) if v.average_rate else None
                rate = info["fps"] or 0.0
                vsec = (int(v.frames) / rate) if (v.frames and rate > 0) else \
                    (float(v.duration * v.time_base) if (v.duration and v.time_base) else None)
                if vsec is not None:  # shot lengths follow the PICTURE duration (frames / fps), not the container
                    info["containerDuration"] = info.get("duration")
                    info["duration"] = round(vsec, 3)
                    info["frames"] = int(v.frames or 0)
    except Exception as exc:
        info["error"] = str(exc)
    return info


def register() -> bool:
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception as exc:  # no server (tests, CLI)
        LOG.debug("[H3MD] routes not registered: %s", exc)
        return False

    @routes.get("/h3md/files")
    async def h3md_files(request):
        try:
            files = await asyncio.get_running_loop().run_in_executor(
                None, list_input_files, request.query.get("kind", "image"))
            return web.json_response({"files": files})
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/h3md/probe")
    async def h3md_probe(request):
        info = await asyncio.get_running_loop().run_in_executor(None, probe_file, request.query.get("file", ""))
        return web.json_response(info)

    @routes.post("/h3md/autosplit")
    async def h3md_autosplit(request):
        """H3MD autosplit preview for the editor: per-shot token estimate and the parts the Director will run."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "body must be JSON"}, status=400)
        try:
            from . import h3md_autosplit as AS
        except ImportError:
            import h3md_autosplit as AS
        out = await asyncio.get_running_loop().run_in_executor(
            None, AS.preview, body.get("plan_json", ""), int(body.get("width") or 0), int(body.get("height") or 0))
        return web.json_response(out)

    return True
