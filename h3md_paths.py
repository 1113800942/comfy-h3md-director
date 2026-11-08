"""Where H3MD may read and write files (v0.3.7, after the Comfy Registry review of 2026-11-08; first version 0.2.1).

On every ComfyUI (one person on their own machine or a shared / cloud server) the same rule holds, so a workflow or a
request to H3MD's routes cannot read or write arbitrary files on the machine:
* reading (pictures, videos and songs in a plan, the editor's probe, plan export): only inside ComfyUI's input,
  output and temp folders and the folders listed in the H3MD config file. A relative path is looked up inside the
  input folder and may not leave it ("..").
* writing: saved videos go into the output folder (output_folder = a subfolder of it) or a listed folder; the segment
  cache goes into a <drive>/H3MD_cache folder (own machine), ComfyUI's temp / output folder or a listed folder
  (see h3md_cache.allowed_cache_root).
* shared ComfyUI (started with --listen 0.0.0.0, a LAN address, or on a cloud platform): the cache always lives in
  ComfyUI's temp folder, plan uploads are capped, and resuming across runs is off unless the config allows it.

Config file (optional): <ComfyUI user folder>/h3md/config.json, for example
    {"mode": "auto", "folders": ["D:/media", "E:/songs"], "shared_resume": false}
  mode          "auto" (default: shared when ComfyUI listens beyond this machine), "local" or "shared"
  folders       extra folders H3MD may read media from and save videos / caches into
  shared_resume true = a shared ComfyUI may still reuse cached segments across runs
"""

from __future__ import annotations

import json
import logging
import os

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD paths]"
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
CONFIG_DIR = "h3md"
CONFIG_NAME = "config.json"
_CFG = {"path": None, "mtime": None, "data": {}}


# ------------------------------------------------------------------ config file
def config_path() -> str | None:
    """<ComfyUI user folder>/h3md/config.json (outside the per-user 'default' folder that /userdata can write)."""
    try:
        import folder_paths
        return os.path.join(folder_paths.get_user_directory(), CONFIG_DIR, CONFIG_NAME)
    except Exception:
        return None


def config() -> dict:
    """The parsed config file ({} when there is none); re-read when the file changes."""
    p = config_path()
    if not p:
        return {}
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {}
    if _CFG["path"] == p and _CFG["mtime"] == mtime:
        return _CFG["data"]
    try:
        with open(p, encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("the file must hold one JSON object")
    except Exception as exc:
        LOG.warning("%s %s could not be read (%s); using the defaults", _TAG, p, exc)
        data = {}
    _CFG.update(path=p, mtime=mtime, data=data)
    return data


def folders() -> list:
    """Extra folders from the config file (absolute paths that exist)."""
    raw = config().get("folders") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for f in raw:
        if isinstance(f, str) and f.strip():
            p = os.path.abspath(os.path.expanduser(f.strip()))
            if os.path.isdir(p):
                out.append(p)
            else:
                LOG.warning("%s config folder '%s' does not exist; ignored", _TAG, f)
    return out


def shared_resume() -> bool:
    return config().get("shared_resume") is True


# ------------------------------------------------------------------ mode
def _listen_addresses() -> list:
    try:
        from comfy.cli_args import args
        listen = getattr(args, "listen", "127.0.0.1") or "127.0.0.1"
    except Exception:
        listen = "127.0.0.1"
    return [a.strip() for a in str(listen).split(",") if a.strip()]


def shared() -> bool:
    """True when other people may be able to send workflows to this ComfyUI (see module doc)."""
    mode = str(config().get("mode") or "auto").strip().lower()
    if mode in ("local", "shared"):
        return mode == "shared"
    return any(a not in _LOOPBACK for a in _listen_addresses())


# ------------------------------------------------------------------ allowed folders
def _comfy_dirs(with_input: bool) -> list:
    out = []
    try:
        import folder_paths
        getters = [folder_paths.get_output_directory, folder_paths.get_temp_directory]
        if with_input:
            getters.insert(0, folder_paths.get_input_directory)
        for get in getters:
            try:
                out.append(os.path.abspath(get()))
            except Exception:
                pass
    except Exception:
        pass
    return out


def read_roots() -> list:
    """ComfyUI's input / output / temp folders + the config folders."""
    return _comfy_dirs(True) + folders()


def write_roots() -> list:
    """ComfyUI's output / temp folders + the config folders."""
    return _comfy_dirs(False) + folders()


def inside(path: str, roots) -> bool:
    p = os.path.realpath(os.path.abspath(path))
    for r in roots:
        r = os.path.realpath(r)
        try:
            if os.path.commonpath((p, r)) == r:
                return True
        except ValueError:                       # other drive (Windows)
            continue
    return False


def under(base: str, rel: str) -> str | None:
    """base/rel when it stays inside base (no '..' or link out of it), else None."""
    base = os.path.abspath(base)
    cand = os.path.abspath(os.path.join(base, rel))
    return cand if inside(cand, [base]) else None


def may_read(path: str) -> bool:
    """Only inside ComfyUI's input / output / temp folders and the config folders (every ComfyUI)."""
    return inside(path, read_roots())


def check_read(path: str) -> str:
    if not may_read(path):
        raise PermissionError(f"H3MD: '{path}' is outside ComfyUI's input / output / temp folders. Put the file into "
                              f"the input folder, or add its folder to \"folders\" in {config_path() or CONFIG_NAME}.")
    return path


def may_write(path: str) -> bool:
    return inside(path, write_roots())


def output_root(custom: str, base: str) -> str:
    """Folder a video is saved into: base (the output folder) when custom is empty; a relative custom folder becomes
    a subfolder of base; an absolute one must be inside base or a config folder, otherwise base is used (warning)."""
    custom = (custom or "").strip().strip('"')
    if not custom:
        return base
    if not os.path.isabs(custom):
        cand = under(base, custom)
    else:
        cand = os.path.abspath(custom)
        cand = cand if inside(cand, [base] + folders()) else None
    if cand:
        return cand
    LOG.warning("%s output_folder '%s' is outside the output folder; saving to %s instead (add the folder to "
                "\"folders\" in %s to allow it)", _TAG, custom, base, config_path() or CONFIG_NAME)
    return base
