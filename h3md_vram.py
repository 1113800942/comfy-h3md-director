"""H3MD VRAM helpers (own code).

Policy (Jack, 2026-10-16): the low-res and high-res stages share ONE base UNet
(different LoRA clones of the same weights), so the base model is never
unloaded here - that would only force a reload.  We only:
  * unload idle models that are NOT clones of the diffusion model(s) in use
    (text encoder, video/audio VAE, latent upscaler, other leftovers),
  * soft_empty_cache().
"""

from __future__ import annotations

import logging

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD VRAM]"


def same_base(a, b) -> bool:
    """True if two ModelPatchers are clones of the same base weights."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    ua, ub = getattr(a, "clone_base_uuid", None), getattr(b, "clone_base_uuid", None)
    if ua is not None and ub is not None:
        return ua == ub
    try:
        return bool(a.is_clone(b))
    except Exception:
        return False


def soft_empty_cache(reason: str = "") -> None:
    try:
        import comfy.model_management as mm
        mm.soft_empty_cache()
        LOG.info("%s soft_empty_cache %s", _TAG, reason)
    except Exception as exc:  # never break sampling because of housekeeping
        LOG.warning("%s soft_empty_cache failed: %s", _TAG, exc)


def unload_idle_models(keep_models, reason: str = "") -> int:
    """Unload every loaded model except clones of `keep_models` (the shared base UNet).

    Returns the number of LoadedModel entries released.  Safe no-op on failure.
    """
    try:
        import comfy.model_management as mm
    except Exception:
        return 0
    keep_models = [m for m in (keep_models or []) if m is not None]
    try:
        loaded = list(mm.current_loaded_models)
        keep_loaded = [lm for lm in loaded
                       if getattr(lm, "model", None) is not None and any(same_base(lm.model, k) for k in keep_models)]
        before = len(loaded)
        mm.free_memory(1e30, mm.get_torch_device(), keep_loaded=keep_loaded)
        released = max(0, before - len(mm.current_loaded_models))
        mm.soft_empty_cache()
        LOG.info("%s unloaded %d idle model(s), kept %d clone(s) of the base UNet %s",
                 _TAG, released, len(keep_loaded), reason)
        return released
    except Exception as exc:
        LOG.warning("%s unload_idle_models failed (%s): %s", _TAG, reason, exc)
        return 0


def unload_patchers(patchers, reason: str = "") -> int:
    """Unload specific patchers (e.g. the latent upscaler) and their clones, then empty cache."""
    n = 0
    try:
        import comfy.model_management as mm
    except Exception:
        return 0
    for p in patchers or []:
        try:
            mm.unload_model_and_clones(p)
            n += 1
        except Exception as exc:
            LOG.warning("%s unload failed: %s", _TAG, exc)
    soft_empty_cache(reason)
    return n


# ----------------------------------------------------------------------------
# H3MD memguard (2026-10-27): budget, snapshots, deep release between segments
# ----------------------------------------------------------------------------
# Why: a long Loop run is ONE prompt, so ComfyUI's per-prompt housekeeping (cast buffers, aimdo staging buffers)
# never runs between segments.  Run 2026-10-27 (1088x1920, 5 x 362 f): segment 3's low-res steps went from ~47 s
# to ~1500 s; the device showed 33.7 / 34.2 GB used while PyTorch held 18.2 GB reserved of which 15.6 GB idle, so
# the dynamically loaded weights no longer fitted and were streamed every step.  deep_release() runs the full
# housekeeping between segments, logs before/after, and escalates to a full unload when idle cache stays high.

GB = 1024 ** 3


def _cuda():
    try:
        import torch
        return torch if torch.cuda.is_available() else None
    except Exception:
        return None


def comfy_reserved_bytes() -> int:
    """VRAM ComfyUI itself keeps free for the OS / other apps: extra_reserved_memory() (default ~0.7 GB on Windows,
    or --reserve-vram) + --vram-headroom (DynamicVRAM headroom).  H3MD budgets inside the rest."""
    total = 0
    try:
        import comfy.model_management as mm
        total += int(mm.extra_reserved_memory())
    except Exception:
        total += int(0.7 * GB)
    try:
        from comfy.cli_args import args
        total += int(float(getattr(args, "vram_headroom", 0) or 0) * GB)
    except Exception:
        pass
    return total


def device_total_bytes() -> int:
    torch = _cuda()
    if torch is None:
        return 0
    try:
        return int(torch.cuda.get_device_properties(torch.cuda.current_device()).total_memory)
    except Exception:
        return 0


def memory_snapshot() -> dict:
    """GB numbers: device total/free, torch reserved/allocated/idle (= reserved - allocated), loaded models, RAM %."""
    out = {}
    torch = _cuda()
    if torch is not None:
        try:
            free, total = torch.cuda.mem_get_info()
            out["dev_total"], out["dev_free"] = total / GB, free / GB
            reserved, allocated = torch.cuda.memory_reserved(), torch.cuda.memory_allocated()
            out["torch_reserved"], out["torch_allocated"] = reserved / GB, allocated / GB
            out["torch_idle"] = max(0, reserved - allocated) / GB
            out["backend"] = torch.cuda.get_allocator_backend()
        except Exception as exc:
            out["error"] = str(exc)
    try:
        import comfy.model_management as mm
        out["models"] = len(mm.current_loaded_models)
        out["models_gb"] = sum(float(lm.model_loaded_memory()) for lm in mm.current_loaded_models
                               if getattr(lm, "model", None) is not None) / GB
    except Exception:
        pass
    try:
        import psutil
        out["ram_pct"] = float(psutil.virtual_memory().percent)
    except Exception:
        pass
    return out


def format_snapshot(s: dict) -> str:
    if not s or "dev_total" not in s:
        return "(no CUDA stats)"
    parts = [f"device free {s['dev_free']:.1f}/{s['dev_total']:.1f} GB",
             f"torch reserved {s['torch_reserved']:.1f} GB (in use {s['torch_allocated']:.1f}, idle {s['torch_idle']:.1f})"]
    if "models_gb" in s:
        parts.append(f"models {s['models']} ({s['models_gb']:.1f} GB on GPU)")
    if "ram_pct" in s:
        parts.append(f"RAM {s['ram_pct']:.0f}%")
    return ", ".join(parts)


def log_memory(where: str, level=logging.INFO) -> dict:
    s = memory_snapshot()
    LOG.log(level, "%s memory %s: %s", _TAG, where, format_snapshot(s))
    return s


def _reset_comfy_buffers() -> bool:
    """ComfyUI's per-prompt housekeeping (cast / aimdo staging buffers, cross-step state); only safe while no model
    is sampling, i.e. between segments.  Missing in older ComfyUI -> skipped."""
    try:
        import comfy.model_management as mm
        fn = getattr(mm, "reset_cast_buffers", None)
        if fn is None:
            return False
        fn()
        return True
    except Exception as exc:
        LOG.warning("%s reset_cast_buffers skipped: %s", _TAG, exc)
        return False


def deep_release(keep_models, reason: str = "", idle_limit_gb: float = 2.0, allow_full: bool = True) -> dict:
    """Between segments: unload idle models -> reset ComfyUI cast buffers -> gc -> empty the CUDA cache, log
    before/after.  If PyTorch still keeps more than idle_limit_gb idle (cache it could not hand back), escalate
    (allow_full): unload ALL models incl. the base UNet (it reloads on the next segment, about 0.5-1 min) and empty
    the cache again.  Returns {"before", "after", "full"}."""
    import gc
    before = memory_snapshot()
    unload_idle_models(keep_models, reason)
    _reset_comfy_buffers()
    gc.collect()
    soft_empty_cache(reason)
    after = memory_snapshot()
    full = False
    # Only the IDLE PyTorch cache decides (DynamicVRAM deliberately fills the GPU with resident weights up to its
    # headroom, so a low "device free" right after a segment is normal and is not a reason to unload).
    if allow_full and after.get("dev_total") and after.get("torch_idle", 0) > idle_limit_gb:
        try:
            import comfy.model_management as mm
            mm.unload_all_models()
            _reset_comfy_buffers()
            gc.collect()
            mm.soft_empty_cache()
            full = True
        except Exception as exc:
            LOG.warning("%s full unload failed: %s", _TAG, exc)
        after2 = memory_snapshot()
        LOG.warning("%s deep release %s: PyTorch still kept %.1f GB idle after the normal release -> unloaded ALL "
                    "models (base UNet reloads on the next segment): %s", _TAG, reason,
                    after.get("torch_idle", 0), format_snapshot(after2))
        after = after2
    LOG.info("%s deep release %s: before [%s] -> after [%s]", _TAG, reason, format_snapshot(before),
             format_snapshot(after))
    return {"before": before, "after": after, "full": full}


# ----------------------------------------------------------------------------
# H3MD memguard: slow-step watchdog (reads the H3MD Sampler's per-step log lines, h3md_sampler.timed_sampler)
# ----------------------------------------------------------------------------
import re as _re

_STEP_RE = _re.compile(r"\[H3MD timing\] (low_res|high_res) step (\d+)/(\d+) (\(setup\) )?([0-9.]+)s")


class SlowStepWatch(logging.Handler):
    """Compares every sampler step time with the median of the earlier steps of the same stage and segment shape.
    A step >= `factor` x that median (and >= `min_seconds`) logs a WARNING; action "stop" also requests ComfyUI's
    interrupt, so the run stops at the next check and keeps its disk cache (resume_from_cache continues it after a
    ComfyUI restart)."""

    def __init__(self, action: str = "warn", factor: float = 4.0, min_seconds: float = 120.0):
        super().__init__(logging.INFO)
        self.action, self.factor, self.min_seconds = action, float(factor), float(min_seconds)
        self.shape = None            # set by the Loop per segment (e.g. its length and size)
        self.history = {}            # (shape, stage) -> [seconds of non-setup steps]
        self.tripped = False

    def emit(self, record):
        try:
            m = _STEP_RE.search(record.getMessage())
        except Exception:
            return
        if not m or self.tripped:
            return
        stage, setup, sec = m.group(1), bool(m.group(4)), float(m.group(5))
        if setup:
            return
        hist = self.history.setdefault((self.shape, stage), [])
        if len(hist) >= 2:
            med = sorted(hist)[len(hist) // 2]
            if sec >= self.min_seconds and sec >= self.factor * med:
                self.tripped = True
                LOG.warning("%s SLOW STEP: %s step took %.0f s, %.1fx the usual %.0f s for this segment size - "
                            "VRAM is probably exhausted (weights streamed from RAM / shared GPU memory). %s",
                            _TAG, stage, sec, sec / med, med,
                            "Stopping the run (cache kept): restart ComfyUI and queue again with "
                            "resume_from_cache on." if self.action == "stop" else
                            "Consider stopping, restarting ComfyUI and resuming from the cache.")
                if self.action == "stop":
                    try:
                        import comfy.model_management as mm
                        mm.interrupt_current_processing(True)
                    except Exception as exc:
                        LOG.warning("%s could not request the interrupt: %s", _TAG, exc)
                return
        hist.append(sec)

    def install(self):
        logging.getLogger().addHandler(self)
        return self

    def remove(self):
        try:
            logging.getLogger().removeHandler(self)
        except Exception:
            pass
