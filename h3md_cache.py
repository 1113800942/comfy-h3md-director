"""H3MD per-segment disk cache (own code).

After each segment the loop decodes it and writes:
  seg_###_frames.npy   decoded frames [N,H,W,3], fp16 (default) or uint8, written chunk-wise via a memmap
  seg_###_audio.pt     {'waveform': [1,C,L] float32, 'sample_rate'}
  seg_###_latent.pt    final two-stage latent streams (video, audio) - for the joined latent and resume
  seg_###_lowres.pt    (Beta native_lowres_carry only) low-res stage endpoint [1,24,T,h,w], for resume
  seg_###.json         manifest, written LAST (a segment counts as cached only when it exists)
The final merge reads the frames back memory-mapped, chunk by chunk, into one preallocated IMAGE tensor
(continuation seams drop `trim` head frames, cuts drop none) and joins the audio with join_audio().
"""

from __future__ import annotations

import gc
import hashlib
import json
import logging
import os
import shutil
import time

import numpy as np
import torch

try:
    from . import h3md_director as D
except ImportError:
    import h3md_director as D

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Cache]"
CHUNK = 16  # frames per conversion chunk (bounds the extra RAM of a write / read)


FOLDER = "H3MD_cache"  # stable folder name placed on the chosen drive / mount
_REAL_FS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "jfs", "reiserfs", "ntfs", "ntfs3", "fuseblk",
            "exfat", "vfat", "apfs", "hfs"}
_SKIP_MOUNTS = ("/boot", "/proc", "/sys", "/dev", "/run", "/snap", "/var/lib/docker", "/tmp")


def temp_root() -> str:
    """Old default: ComfyUI's temp directory (another custom node may move it per start), /h3md_cache."""
    try:
        import folder_paths
        return os.path.join(folder_paths.get_temp_directory(), "h3md_cache")
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), "h3md_cache")


def _windows_fixed_drives() -> list:
    import ctypes
    k32 = ctypes.windll.kernel32
    mask, out = k32.GetLogicalDrives(), []
    for n in range(26):
        if mask & (1 << n):
            root = chr(65 + n) + ":\\"
            if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:   # DRIVE_FIXED (skips removable/network/optical/ram)
                out.append(root)
    return out


def _posix_mounts() -> list:
    out = [os.path.expanduser("~")]
    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                dev, mnt, fs = (line.split() + ["", "", ""])[:3]
                mnt = mnt.replace("\\040", " ")
                if fs in _REAL_FS and dev.startswith("/dev/") and mnt != "/" \
                        and not any(mnt == p or mnt.startswith(p + "/") for p in _SKIP_MOUNTS):
                    out.append(mnt)
    except OSError:
        pass
    if os.path.isdir("/Volumes"):                                  # macOS: external / data volumes
        for v in sorted(os.listdir("/Volumes")):
            p = os.path.join("/Volumes", v)
            if os.path.ismount(p) and not os.path.islink(p):
                out.append(p)
    return out


def _candidates() -> list:
    """Base folders that may hold FOLDER: local fixed drives (Windows) or home + real local mounts (Linux/macOS)."""
    try:
        bases = _windows_fixed_drives() if os.name == "nt" else _posix_mounts()
    except Exception as exc:
        LOG.warning("%s could not enumerate local drives (%s)", _TAG, exc)
        bases = []
    seen, out = set(), []
    for b in bases:
        key = os.path.normcase(os.path.abspath(b))
        if key not in seen:
            seen.add(key)
            out.append(b)
    return out


def _free(path: str):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _latest_resume(folder: str):
    try:
        times = [e.stat().st_mtime for e in os.scandir(folder) if e.is_dir() and e.name.startswith("resume_")]
    except OSError:
        return None
    return max(times) if times else None


def _usable(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        return os.access(folder, os.W_OK)
    except OSError:
        return False


def default_root() -> str:
    """Root used when cache_dir is empty (no hard-coded drive, works for any user):
    1. sticky: an existing <base>/H3MD_cache that holds resume_* runs (newest one wins), so resume survives restarts;
    2. otherwise <base>/H3MD_cache on the local fixed drive / mount with the most free space (created on demand);
    3. nothing writable: the old temp location, with a warning.
    Shared ComfyUI (--listen, h3md_paths.shared): always ComfyUI's temp folder - no scanning of other drives."""
    if D.PATHS.shared():
        root = temp_root()
        LOG.info("%s shared ComfyUI: cache root %s (ComfyUI temp folder)", _TAG, root)
        return root
    bases = _candidates()
    sticky = []
    for b in bases:
        t = _latest_resume(os.path.join(b, FOLDER))
        if t is not None:
            sticky.append((t, b))
    ranked = [b for _, b in sorted(sticky, key=lambda x: -x[0])]
    free = [(f, b) for b in bases for f in [_free(b)] if f is not None]
    ranked += [b for _, b in sorted(free, key=lambda x: -x[0]) if b not in ranked]   # stable for equal free
    for b in ranked:
        folder = os.path.join(b, FOLDER)
        if _usable(folder):
            f = _free(b)
            why = "existing resume cache" if b in {y for _, y in sticky} else "most free space"
            LOG.info("%s cache root %s (%s, %.1f GB free)", _TAG, folder, why, (f or 0) / 1024 ** 3)
            return folder
    fallback = temp_root()
    LOG.warning("%s no writable local drive for %s; falling back to %s (temp: may be deleted/moved on restart, "
                "so resume_from_cache may not find it)", _TAG, FOLDER, fallback)
    return fallback


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


def _file_sig(file: str):
    try:
        p = D.resolve_media_path(file)
        st = os.stat(p)
        return [file, st.st_size, int(st.st_mtime)]
    except Exception:
        return [file, None, None]


def _used_files(seg: dict, media: dict) -> list:
    used = [("images", i) for i in seg["images"]] + [("videos", v["id"]) for v in seg["videos"]]
    used += [("audios", a) for a in seg["audios"]]
    if seg.get("audioMode") == "locked" and seg.get("locked"):
        lk = seg["locked"]
        used.append(("videos" if lk["kind"] == "video" else "audios", lk["id"]))
    files = []
    for kind, ref in used:
        info = media.get(kind, {}).get(ref, {})
        files.append([kind, ref, info, _file_sig(info.get("file", ""))])
    return files


def segment_signature(plan: dict, k: int) -> dict:
    """Everything of segment k that changes its output (prompt, refs, windows, geometry, media files + stat)."""
    seg = plan["segments"][k]
    media = plan["media"]
    files = _used_files(seg, media)
    keep = ("startFrame", "length", "overlapIn", "prompt", "images", "videos", "audios", "audioMode", "locked")
    sig = {"seg": {key: seg.get(key) for key in keep}, "files": files,
           "size": [plan.get("width"), plan.get("height")]}
    # window-exp: only window-mode plans get these keys, so every other plan keeps its old cache keys
    if plan.get("windowMode"):
        sig["windowMode"] = {k: v for k, v in plan["windowMode"].items() if k not in ("preview", "redraw")}
        if seg.get("windowPrompts"):
            sig["windowPrompts"] = seg["windowPrompts"]
        if seg.get("windowCuts"):
            sig["windowCuts"] = seg["windowCuts"]
        if seg.get("groupParts"):   # 3e: every shot of the group, with its own media files
            sig["groupParts"] = [{"start": gp["start"], "end": gp["end"], "origin": gp["origin"], "prompt": gp["prompt"],
                                  "seg": {k: gp["seg"].get(k) for k in keep},
                                  "files": _used_files(gp["seg"], media)}   # 11-08: pictures + sound too
                                 for gp in seg["groupParts"]]
    # refVideoHeight: only non-native values enter the key, so native (and old plans) keep their old keys
    rvh = D.normalize_ref_video_height(plan.get("refVideoHeight")) or "native"
    if rvh != "native" and seg["videos"]:
        sig["refVideoHeight"] = rvh
        if rvh == "auto":
            sig["refVideoAuto"] = [D.REF_VIDEO_AUTO_TOKEN_LIMIT, [list(t) for t in D.REF_VIDEO_TIERS]]
            if plan.get("tokenBudget"):   # H3MD autosplit: the auto tier limit is the plan's token budget
                sig["refVideoAuto"][0] = int(plan["tokenBudget"])
            if any(v.get("depth") for v in seg["videos"]):   # depth videos fixed at the lowest tier (2026-10-27)
                sig["refVideoAutoDepth"] = "lowest"
    return sig


# Settings that only change CONTINUED segments (or a hard cut with locked audio): a cut/first segment's cache key
# and the run folder ignore them, so changing e.g. continue_lowres_prefix / seam_tone_match still resumes the
# unchanged first segment.
# The tiling inputs (highres_tiling, tiling_axis, tiling_overlap) are sampler settings and part of the BASE key:
# with highres_tiling on, EVERY segment (first / cut / continued) runs its high-res pass through the tiling wrapper.
CONT_ONLY_SETTINGS = ("continue", "lowres_prefix", "post")

# Sampler kwargs that only free VRAM (unload models / the latent upscaler between stages) and never change the
# sampled result: kept out of the cache key so toggling them does not re-render cached segments.
VRAM_ONLY_SAMPLER_KWARGS = ("release_upscaler", "unload_idle_models", "ff_chunk_rows")

# Cache format/logic version (2026-10-26 final): part of BOTH settings hashes (split_settings), so every segment key,
# every chained continuation key and the resume_* folder name change; caches written before the syncfix are never
# reused (they are not deleted - a new resume folder is used).  Bump when segment output logic changes.
H3MD_CACHE_VERSION = "luna-20261029"   # Luna two-pass engine (oneshot-20261026: one origin for picture/audio)


def split_settings(settings: dict):
    """-> (base_hash, full_hash).  base = settings minus CONT_ONLY_SETTINGS (run folder + cut-segment keys);
    full = everything (continued-segment keys, as before)."""
    settings = {**settings, "_cacheVersion": H3MD_CACHE_VERSION}   # H3MD final 2026-10-26: cache version marker
    base = {k: v for k, v in settings.items() if k not in CONT_ONLY_SETTINGS}
    return _sha({"base": base}), _sha(settings)


def segment_uses_cont_settings(seg: dict, continued: bool, tiling: bool) -> bool:
    return bool(continued) or (bool(tiling) and seg.get("audioMode") == "locked")


def allowed_cache_root(root: str) -> bool:
    """v0.3.7: where a cache_dir may point. Own machine: inside a <drive>/H3MD_cache folder, ComfyUI's output / temp
    folder or a folder listed in the H3MD config file. Shared ComfyUI: ComfyUI's output / temp folder or a config
    folder only (h3md_paths)."""
    roots = D.PATHS.write_roots()
    if not D.PATHS.shared():
        roots = roots + [os.path.join(b, FOLDER) for b in _candidates()]
    return D.PATHS.inside(root, roots)


def segment_key(settings_hash: str, plan: dict, k: int, seed: int, continued: bool, prev_key: str | None) -> str:
    """Chain key: a continuation also depends on the previous segment's key (its tail is our prefix)."""
    return _sha({"settings": settings_hash, "segment": segment_signature(plan, k), "seed": int(seed),
                 "prev": prev_key if continued else "cut"})


class SegmentCache:
    def __init__(self, root: str | None, settings_hash: str, resume: bool, dtype: str = "fp16"):
        if root and not allowed_cache_root(root):
            LOG.warning("%s cache_dir '%s' is not inside a <drive>/%s folder, ComfyUI's output / temp folder or a folder "
                        "listed in %s; using the default", _TAG, root, FOLDER, D.PATHS.config_path() or "h3md/config.json")
            root = None
        root = os.path.abspath(root or default_root())
        if resume and D.PATHS.shared() and not D.PATHS.shared_resume():
            # resume_<settings hash> is shared by every run with the same settings: on a shared ComfyUI another
            # person's segments could be picked up. Fresh run folder instead ("shared_resume": true allows it).
            LOG.warning("%s shared ComfyUI: resume_from_cache off for this run (\"shared_resume\": true in %s allows it)",
                        _TAG, D.PATHS.config_path() or "h3md/config.json")
            resume = False
        if resume:
            name = "resume_" + settings_hash[:16]
        else:
            name = "run_" + time.strftime("%Y%m%d_%H%M%S") + f"_{os.getpid()}_{int(time.time() * 1000) % 100000:05d}"
        self.dir = os.path.join(root, name)
        self.dtype = dtype if dtype in ("fp16", "uint8") else "fp16"
        os.makedirs(self.dir, exist_ok=True)
        LOG.info("%s directory %s (frames %s)", _TAG, self.dir, self.dtype)

    def run_seed(self):
        """Base seed stored by the run that created this (resume) folder, or None."""
        try:
            with open(os.path.join(self.dir, "run.json"), encoding="utf-8") as f:
                return int(json.load(f)["seed"])
        except Exception:
            return None

    def save_run_seed(self, seed: int):
        tmp = os.path.join(self.dir, "run.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"seed": int(seed), "written": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        os.replace(tmp, os.path.join(self.dir, "run.json"))

    def _p(self, k, suffix):
        return os.path.join(self.dir, f"seg_{k:03d}{suffix}")

    def manifest(self, k):
        try:
            with open(self._p(k, ".json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def has(self, k, key) -> bool:
        m = self.manifest(k)
        return bool(m and m.get("key") == key and m.get("dtype") == self.dtype
                    and all(os.path.isfile(self._p(k, s)) for s in ("_frames.npy", "_audio.pt", "_latent.pt")))

    def write(self, k, key, latent: dict, images: torch.Tensor, audio: dict | None, info: dict,
              lowres: torch.Tensor | None = None) -> dict:
        t0 = time.time()
        n, h, w, c = (int(x) for x in images.shape)
        np_dtype = np.float16 if self.dtype == "fp16" else np.uint8
        frames_path = self._p(k, "_frames.npy")
        mm = np.lib.format.open_memmap(frames_path, mode="w+", dtype=np_dtype, shape=(n, h, w, c))
        for i in range(0, n, CHUNK):
            part = images[i:i + CHUNK].detach().to("cpu", torch.float32)
            if self.dtype == "uint8":
                part = (part.clamp(0.0, 1.0) * 255.0).round().to(torch.uint8)
            else:
                part = part.to(torch.float16)
            mm[i:i + part.shape[0]] = part.numpy()
            del part
        mm.flush()
        del mm
        v, a = latent["samples"].unbind()
        torch.save({"video": v.detach().cpu(), "audio": a.detach().cpu()}, self._p(k, "_latent.pt"))
        if audio is not None:
            torch.save({"waveform": audio["waveform"].detach().cpu(), "sample_rate": int(audio["sample_rate"])},
                       self._p(k, "_audio.pt"))
        else:
            torch.save(None, self._p(k, "_audio.pt"))
        sizes = {s: os.path.getsize(self._p(k, s)) for s in ("_frames.npy", "_audio.pt", "_latent.pt")}
        if lowres is not None:
            torch.save({"video": lowres.detach().cpu()}, self._p(k, "_lowres.pt"))
            sizes["_lowres.pt"] = os.path.getsize(self._p(k, "_lowres.pt"))
        man = dict(info, key=key, index=k, frames=n, height=h, width=w, channels=c, dtype=self.dtype, bytes=sizes,
                   lowres=lowres is not None)
        tmp = self._p(k, ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._p(k, ".json"))
        LOG.info("%s segment %d cached: %d frames %dx%d %s, frames %.2f GB, audio %.1f MB, latent %.1f MB (%.1fs) -> %s",
                 _TAG, k + 1, n, w, h, self.dtype, sizes["_frames.npy"] / 1e9, sizes["_audio.pt"] / 1e6,
                 sizes["_latent.pt"] / 1e6, time.time() - t0, self.dir)
        return man

    # --- H3MD rerun (2026-10-30) ---
    SEG_FILES = (".json", "_frames.npy", "_audio.pt", "_latent.pt", "_lowres.pt")

    def archive_dir(self) -> str:
        """Folder for the old files of re-rendered segments (created on first use)."""
        return os.path.join(self.dir, "_replaced_" + time.strftime("%Y%m%d_%H%M%S"))

    def archive(self, k, folder: str) -> list:
        """Move segment k's files into `folder` (kept, never deleted).  Returns the moved file names."""
        moved = []
        for suf in self.SEG_FILES:
            src = self._p(k, suf)
            if os.path.isfile(src):
                os.makedirs(folder, exist_ok=True)
                os.replace(src, os.path.join(folder, os.path.basename(src)))
                moved.append(os.path.basename(src))
        if moved:
            LOG.info("%s segment %d: old files moved to %s", _TAG, k + 1, folder)
        return moved

    def restore(self, k, folder: str) -> list:
        """Undo archive(): segment k's files in `folder` go back in place (a half-written new version of the same
        files is removed first).  Returns the restored file names."""
        back = []
        for suf in self.SEG_FILES:
            dst = self._p(k, suf)
            src = os.path.join(folder, os.path.basename(dst))
            if os.path.isfile(src):
                if os.path.isfile(dst):
                    os.remove(dst)
                os.replace(src, dst)
                back.append(os.path.basename(dst))
        if back:
            LOG.info("%s segment %d: previous version restored from %s", _TAG, k + 1, folder)
        return back

    def adopt(self, k, key: str):
        """Rerun mode: a kept segment gets the new chain key (its predecessor or the refs changed), so a later
        normal resume_from_cache run reuses it instead of re-rendering everything after a repaired segment."""
        m = self.manifest(k)
        if not m:
            return
        old = m.get("key")
        m["key"] = key
        m.setdefault("adopted_keys", []).append(old)
        tmp = self._p(k, ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._p(k, ".json"))

    def load_latent(self, k) -> dict:
        d = torch.load(self._p(k, "_latent.pt"), map_location="cpu", weights_only=True)
        return {"samples": D._nested((d["video"], d["audio"]))}

    def load_lowres(self, k):
        """Beta carry of segment k, or None (not stored / missing file)."""
        m = self.manifest(k)
        p = self._p(k, "_lowres.pt")
        if not (m and m.get("lowres")) or not os.path.isfile(p):
            return None
        return torch.load(p, map_location="cpu", weights_only=True)["video"]

    def load_audio(self, k):
        return torch.load(self._p(k, "_audio.pt"), map_location="cpu", weights_only=True)

    def frames(self, k):
        return np.load(self._p(k, "_frames.npy"), mmap_mode="r")

    def merge(self, indices, trims, crossfade_ms=0, overlap_source="prev"):
        """Read back + join. Returns (images float32 [T,H,W,3], audio, per-segment frame counts).
        overlap_source 'prev' drops `trim` head frames of a continuation (old behaviour); 'next' keeps them and
        drops the previous segment's last `trim` frames instead (h3md_seam.keep_spans).  Audio is unchanged."""
        mans = [self.manifest(k) for k in indices]
        trims = [0] + [int(t) for t in trims[1:]]  # like join_segments: the first segment in range is never trimmed
        if any(m is None for m in mans):
            raise RuntimeError(f"{_TAG} incomplete cache in {self.dir}")
        h, w, c = mans[0]["height"], mans[0]["width"], mans[0]["channels"]
        for m, trim in zip(mans, trims):
            if (m["height"], m["width"], m["channels"]) != (h, w, c):
                raise ValueError(f"{_TAG} segment {m['index'] + 1} frame size differs")
            if trim >= m["frames"] and trim > 0:
                raise ValueError(f"H3MD Join: overlap {trim} >= new segment frames {m['frames']}")
        # --- H3MD seam hook (merge_overlap_source): frame span kept from each segment ---
        spans = [(t, m["frames"]) for m, t in zip(mans, trims)]
        if overlap_source == "next":
            spans = [(0, m["frames"] - (trims[j + 1] if j + 1 < len(mans) else 0)) for j, m in enumerate(mans)]
            if any(e <= s for s, e in spans):
                raise ValueError("H3MD Join: an overlap leaves no frames of the previous segment (merge_overlap_source=next)")
        total = sum(e - s for s, e in spans)
        LOG.info("%s merging %d segment(s) -> %d frames %dx%d (IMAGE float32 %.2f GB)", _TAG, len(indices), total,
                 w, h, total * h * w * c * 4 / 1e9)
        out = torch.empty((total, h, w, c), dtype=torch.float32)
        pos = 0
        merged_audio = None
        counts = []
        for k, m, trim, (s0, e0) in zip(indices, mans, trims, spans):
            mm = self.frames(k)
            for i in range(s0, e0, CHUNK):
                part = torch.from_numpy(np.array(mm[i:min(i + CHUNK, e0)]))
                part = part.to(torch.float32)
                if m["dtype"] == "uint8":
                    part = part / 255.0
                out[pos:pos + part.shape[0]] = part
                pos += part.shape[0]
                del part
            del mm
            audio = self.load_audio(k)
            merged_audio = audio if merged_audio is None else D.join_audio(audio, trim, merged_audio, crossfade_ms)
            counts.append(m["frames"])
        assert pos == total
        gc.collect()
        return out, merged_audio, counts

    def delete(self):
        gc.collect()  # Windows: memmap handles must be closed before the files can be removed
        shutil.rmtree(self.dir, ignore_errors=True)
        if os.path.exists(self.dir):
            LOG.warning("%s could not fully delete %s (file still in use?) - remove it by hand", _TAG, self.dir)
        else:
            LOG.info("%s deleted %s", _TAG, self.dir)
