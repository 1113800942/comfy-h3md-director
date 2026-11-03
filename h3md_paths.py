"""Where H3MD may read and write files (v0.2.1, after the RunningHub review of 2026-11-02).

Two situations:
* local (ComfyUI listens on 127.0.0.1 / localhost / ::1 only - the default): one person on their own machine.
  Behaviour is unchanged: absolute media paths, an absolute output_folder and the automatic cache drive all work.
* shared (ComfyUI started with --listen 0.0.0.0, a LAN address, or a cloud platform): other people can send
  workflows. Then H3MD only reads media inside ComfyUI's input / output / temp folders, only writes videos inside
  the output folder, and keeps its cache inside ComfyUI's temp folder - so a workflow cannot probe, read or write
  arbitrary files on the machine.

Override with the environment variable H3MD_PATHS=local or H3MD_PATHS=shared. Extra folders that may be read in
shared mode: H3MD_MEDIA_DIRS (separated by os.pathsep, e.g. "D:\\media;E:\\songs" on Windows).
"""

from __future__ import annotations

import logging
import os

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD paths]"
_LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _listen_addresses() -> list:
    try:
        from comfy.cli_args import args
        listen = getattr(args, "listen", "127.0.0.1") or "127.0.0.1"
    except Exception:
        listen = "127.0.0.1"
    return [a.strip() for a in str(listen).split(",") if a.strip()]


def shared() -> bool:
    """True when other people may be able to send workflows to this ComfyUI (see module doc)."""
    mode = os.environ.get("H3MD_PATHS", "").strip().lower()
    if mode in ("local", "shared"):
        return mode == "shared"
    return any(a not in _LOOPBACK for a in _listen_addresses())


def _comfy_dirs() -> list:
    out = []
    try:
        import folder_paths
        for get in (folder_paths.get_input_directory, folder_paths.get_output_directory,
                    folder_paths.get_temp_directory):
            try:
                out.append(os.path.abspath(get()))
            except Exception:
                pass
    except Exception:
        pass
    return out


def read_roots() -> list:
    extra = [os.path.abspath(p) for p in os.environ.get("H3MD_MEDIA_DIRS", "").split(os.pathsep) if p.strip()]
    return _comfy_dirs() + extra


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


def may_read(path: str) -> bool:
    """Local: anything. Shared: only inside ComfyUI's input / output / temp folders (+ H3MD_MEDIA_DIRS)."""
    return (not shared()) or inside(path, read_roots())


def check_read(path: str) -> str:
    if not may_read(path):
        raise PermissionError(f"H3MD: '{path}' is outside ComfyUI's input/output folders; this ComfyUI is shared "
                              "(--listen), so H3MD only reads media there (set H3MD_MEDIA_DIRS to allow more).")
    return path


def output_root(custom: str, base: str) -> str:
    """Folder a video is saved into. Local: custom (absolute) or base. Shared: always inside base (the output
    folder); a custom absolute folder outside it is refused with a warning, a relative one becomes a subfolder."""
    custom = (custom or "").strip().strip('"')
    if not custom:
        return base
    if not shared():
        return os.path.abspath(custom)
    cand = os.path.abspath(custom if os.path.isabs(custom) else os.path.join(base, custom))
    if inside(cand, [base]):
        return cand
    LOG.warning("%s output_folder '%s' is outside the output folder on a shared ComfyUI; saving to %s instead",
                _TAG, custom, base)
    return base
