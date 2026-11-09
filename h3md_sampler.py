"""H3MD Sampler (H3MDSampler) - two-pass MiniMax H3 sampling for H3MD on the bundled Luna-H3-Sampler engine.

Engine: h3md_luna/luna_two_pass.two_pass_sample (Luna-H3-Sampler, GPL-3.0, vendored unchanged).  One schedule
(`sigmas`) is split at `transition_step`: pass 1 runs sigmas[0..t] on a grid of lowres_scale, the clean video
estimate is lifted to the full grid (learned H3 latent upscaler, optionally blended with the pixel route by
rho / w_min / w_max) and re-noised, pass 2 runs sigmas[t..end] at full size.  Audio is one trajectory through both
passes.  Mask-0 regions of latent_image (kept continuation prefix, locked audio) come out exactly as they went in.

What H3MD adds around the engine (own code, no monkeypatching of the engine):
  * H3MD drift: the drift denoise_mask_function travels on the model clones (both passes);
  * low_res_head: the kept prefix of the low-res pass comes from the previous segment's own low-res pass
    (native carry) or a pixel-space re-encode, instead of a latent-space resize of the full-res prefix;
  * between the passes (hook in the lift): the depth ControlNet patch is released, idle models are unloaded,
    the learned upscaler is dropped from RAM after use;
  * high-res tiling (h3md_tiled_hires.py): the high-res pass picks the fewest strips that fit the free VRAM;
  * per-step timing / VRAM log ("[H3MD timing] ..."), read by the Loop's slow-step guard;
  * output: ONLY {'samples': final latent}; the low-res endpoint is kept in self.last_lowres for the next segment.
"""

from __future__ import annotations

import functools
import logging
import time

import torch

try:
    from . import h3md_vram
    from . import h3md_tiled_hires as TILED
    from . import h3md_ffchunk as FF
    from . import h3md_lowres_anchor as LA
    from .h3md_luna import luna_two_pass as LUNA
    from .h3md_luna import luna_lift
    from .h3md_luna import luna_upscaler
except ImportError:  # flat import (tests)
    import h3md_vram
    import h3md_tiled_hires as TILED
    import h3md_ffchunk as FF
    import h3md_lowres_anchor as LA
    from h3md_luna import luna_two_pass as LUNA
    from h3md_luna import luna_lift
    from h3md_luna import luna_upscaler

try:
    from .h3md_luna import LUNA_COMMIT
except ImportError:
    from h3md_luna import LUNA_COMMIT

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Sampler]"
ENGINE = "luna-" + LUNA_COMMIT[:8]   # part of the Loop's cache settings: a new engine never reuses old segments
CATEGORY = "MiniMax H3/H3MD"
PREFERRED_UPSCALER = ("minimax_h3_latent_upscaler_3d_fp16", "minimax_h3_latent_upscaler_3d_bf16")


# ---------------------------------------------------------------------------
# latent / mask helpers
# ---------------------------------------------------------------------------

def _unbind(x):
    if getattr(x, "is_nested", False):
        return list(x.unbind())
    return [x]


def sanitize_latent(latent: dict) -> dict:
    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError(f"{_TAG} latent_image must be a LATENT dict with 'samples'")
    clean = {"samples": latent["samples"]}
    if latent.get("noise_mask") is not None:
        clean["noise_mask"] = latent["noise_mask"]
    dropped = sorted(k for k in latent if k not in clean)
    if dropped:
        LOG.info("%s ignoring input latent keys %s", _TAG, dropped)
    return clean


def low_res_hw(target_hw, lowres_scale: float):
    """The engine's low-res latent grid (luna_two_pass: max(2, round(H * s / 2) * 2))."""
    H, W = int(target_hw[0]), int(target_hw[1])
    s = float(lowres_scale)
    return max(2, round(H * s / 2) * 2), max(2, round(W * s / 2) * 2)


def exact_grid_note(latent: dict, lowres_scale: float):
    """None when the low-res grid is exactly lowres_scale x the full grid, else a warning text."""
    video = _unbind(latent["samples"])[0]
    H, W = int(video.shape[-2]), int(video.shape[-1])
    h, w = low_res_hw((H, W), lowres_scale)
    s = float(lowres_scale)
    if abs(h - H * s) < 1e-6 and abs(w - W * s) < 1e-6:
        return None
    return (f"low-res grid {w}x{h} latents is not exactly {s:g} x {W}x{H} (pixel size {W * 16}x{H * 16}); the lift "
            f"stretches it slightly - use sizes that are multiples of {int(round(32 / s))} px")


def find_drift(*models):
    for m in models:
        fn = getattr(m, "model_options", {}).get("h3md_drift") if m is not None else None
        if fn is not None:
            return fn
    return None


def with_drift(model, drift):
    """model with the H3MD drift denoise_mask_function (a clone when it has to be added)."""
    if model is None or drift is None or model.model_options.get("denoise_mask_function") is drift:
        return model
    m = model.clone()
    if hasattr(m, "set_model_denoise_mask_function"):
        m.set_model_denoise_mask_function(drift)
    else:
        m.model_options["denoise_mask_function"] = drift
    m.model_options["h3md_drift"] = drift
    return m


def split_sigmas(sigmas, transition_step: int):
    s = torch.as_tensor(sigmas).flatten().float().cpu()
    t = int(transition_step)
    if s.numel() < 3 or not 1 <= t < s.numel() - 1:
        raise ValueError(f"{_TAG} transition_step {t} must leave at least one step for each pass "
                         f"({s.numel() - 1} steps in sigmas)")
    return s[:t + 1].clone(), s[t:].clone()


def check_low_res_head(prefix, latent: dict, lowres_scale: float):
    """None when `prefix` ([B,24,vt,h,w] on the low grid) can pin the low-res pass's kept prefix, else the reason."""
    if not isinstance(prefix, torch.Tensor) or prefix.ndim != 5:
        return f"unexpected prefix {type(prefix).__name__}"
    video = _unbind(latent["samples"])[0]
    h, w = low_res_hw(video.shape[-2:], lowres_scale)
    if tuple(prefix.shape[-2:]) != (h, w) or prefix.shape[1] != video.shape[1]:
        return f"prefix {tuple(prefix.shape)} does not match the low grid (C={video.shape[1]}, {h}x{w})"
    vt = int(prefix.shape[2])
    if not 0 < vt < video.shape[2]:
        return f"prefix has {vt} tokens, the segment {video.shape[2]}"
    nm = latent.get("noise_mask")
    vm = _unbind(nm)[0] if nm is not None else None
    if vm is None or vm.ndim != 5 or vm.shape[2] != video.shape[2] or bool((vm[:, :, :vt] != 0).any()):
        return "the latent does not keep (mask 0) all prefix tokens"
    return None


def check_low_res_base(base, latent: dict, lowres_scale: float):
    """Local redraw (0.4.0): None when `base` ([B,24,T,h,w], the whole segment on the low grid) can supply the
    kept rows of the low-res pass, else the reason."""
    if not isinstance(base, torch.Tensor) or base.ndim != 5:
        return f"unexpected base {type(base).__name__}"
    video = _unbind(latent["samples"])[0]
    h, w = low_res_hw(video.shape[-2:], lowres_scale)
    if tuple(base.shape) != (video.shape[0], video.shape[1], video.shape[2], h, w):
        return f"base {tuple(base.shape)} does not match the low grid {(video.shape[0], video.shape[1], video.shape[2], h, w)}"
    if latent.get("noise_mask") is None:
        return "the latent has no noise mask"
    return None


def check_low_res_tail(suffix, latent: dict, lowres_scale: float):
    """H3MD rerun (2026-10-30): None when `suffix` ([B,24,vt,h,w] on the low grid) can pin the low-res pass's kept
    tail (tail lock to the next segment), else the reason."""
    if not isinstance(suffix, torch.Tensor) or suffix.ndim != 5:
        return f"unexpected suffix {type(suffix).__name__}"
    video = _unbind(latent["samples"])[0]
    h, w = low_res_hw(video.shape[-2:], lowres_scale)
    if tuple(suffix.shape[-2:]) != (h, w) or suffix.shape[1] != video.shape[1]:
        return f"suffix {tuple(suffix.shape)} does not match the low grid (C={video.shape[1]}, {h}x{w})"
    vt = int(suffix.shape[2])
    if not 0 < vt < video.shape[2]:
        return f"suffix has {vt} tokens, the segment {video.shape[2]}"
    nm = latent.get("noise_mask")
    vm = _unbind(nm)[0] if nm is not None else None
    if vm is None or vm.ndim != 5 or vm.shape[2] != video.shape[2] or bool((vm[:, :, -vt:] != 0).any()):
        return "the latent does not keep (mask 0) all tail tokens"
    return None


# ---------------------------------------------------------------------------
# per-step timing (read by h3md_vram.SlowStepWatch)
# ---------------------------------------------------------------------------

def _vram_gib():
    try:
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / 2**30, torch.cuda.max_memory_allocated() / 2**30
    except Exception:
        pass
    return None, None


def _euler_next(d, sigmas):
    """State after Euler step d['i'] (k-diffusion sample_euler, s_churn 0): x + (x - denoised) / sigma * dt."""
    x, den = d.get("x"), d.get("denoised")
    if x is None or den is None:
        return None
    j = int(d["i"])
    if j + 1 >= int(sigmas.shape[-1]):
        return None
    sigma = d.get("sigma_hat", sigmas[j])
    dt = sigmas[j + 1] - sigma
    from comfy.k_diffusion.sampling import to_d
    return x + to_d(x, sigma, den) * dt


def timed_sampler(sampler, stage: str, total: int, log: list | None = None, progress=None):
    """KSAMPLER that logs '[H3MD timing] <stage> step i/n <sec>s ...' per step (step 1 includes model loading and is
    tagged 'setup').  Samplers that are not KSAMPLERs are returned unchanged."""
    import comfy.samplers
    if not isinstance(sampler, comfy.samplers.KSAMPLER):
        return sampler
    inner = sampler.sampler_function
    is_euler = getattr(inner, "__name__", "") == "sample_euler" and not sampler.extra_options.get("s_churn", 0)

    @functools.wraps(inner)
    def fn(model, x, sigmas, *a, callback=None, **kw):
        state = {"t": time.perf_counter(), "n": 0}
        offset = 0
        n_sig = int(sigmas.shape[-1])
        if progress is not None:   # window-exp progress cache: restart this pass at the last saved step
            got = progress.resume(stage, n_sig)
            if got is not None:
                offset, saved = got
                x = saved.to(device=x.device, dtype=x.dtype)
                sigmas = sigmas[offset:]
        try:
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass

        def cb(d):
            now = time.perf_counter()
            state["n"] += 1
            i = int(d.get("i", state["n"] - 1)) + offset
            if progress is not None and d.get("x") is not None:
                try:
                    # The callback fires after step i's model call with x = the state BEFORE step i.  For plain
                    # Euler (H3MD default) the state AFTER step i is known now: save that as step i+1, so a stop
                    # during step i+1 resumes there instead of one step earlier.  Other samplers: save x_i.
                    nxt = _euler_next(d, sigmas) if is_euler else None
                    if nxt is not None and i + 1 < n_sig - 1:
                        progress.save_step(stage, i + 1, nxt, n_sig)
                    elif nxt is None:
                        progress.save_step(stage, i, d["x"], n_sig)
                except Exception as exc:  # a full disk must not kill the run
                    LOG.warning("[H3MD Progress] could not save %s step %d (%s)", stage, i + 1, exc)
            sec = now - state["t"]
            state["t"] = now
            alloc, peak = _vram_gib()
            sigma = d.get("sigma")
            sigma = float(sigma.flatten()[0]) if isinstance(sigma, torch.Tensor) else float(sigma or 0.0)
            mem = f" vram {alloc:.1f}/{peak:.1f} GiB" if alloc is not None else ""
            LOG.info("[H3MD timing] %s step %d/%d %s%.2fs sigma %.4f%s", stage, i + 1, total,
                     "(setup) " if i == 0 else "", sec, sigma, mem)
            if log is not None:
                log.append({"stage": stage, "step": i + 1, "sec": round(sec, 3), "sigma": round(sigma, 5),
                            "vram_gib": None if alloc is None else round(alloc, 2),
                            "peak_gib": None if peak is None else round(peak, 2)})
            if callback is not None:
                return callback(dict(d, i=i) if offset else d)

        return inner(model, x, sigmas, *a, callback=cb, **kw)

    return comfy.samplers.KSAMPLER(fn, sampler.extra_options.copy(), sampler.inpaint_options.copy())


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

UPSCALER_EXT = (".safetensors", ".sft", ".pth", ".pt")


def upscaler_choices():
    """Every checkpoint in models/latent_upscale_models (fine-tunes of the H3 3D upscaler such as
    h3_upscaler_sharpness_* have other file names) + the engine's own list (also scans upscale_models)."""
    try:
        names = set(luna_upscaler.list_upscaler_models())
    except Exception:
        names = {"none"}
    try:
        import folder_paths
        for n in folder_paths.get_filename_list("latent_upscale_models"):
            if n.lower().endswith(UPSCALER_EXT):
                names.add(n.replace("\\", "/"))
    except Exception:
        pass
    names = ["none"] + sorted(n for n in names if n != "none")
    default = next((n for p in PREFERRED_UPSCALER for n in names if n.rsplit("/", 1)[-1].startswith(p)),
                   next((n for n in names if n != "none"), "none"))
    return names, default


class SamplerOutOfMemory(RuntimeError):
    pass


class H3MDSampler:
    @classmethod
    def INPUT_TYPES(cls):
        names, default = upscaler_choices()
        return {
            "required": {
                "low_res_model": ("MODEL", {"tooltip": "Model for the low-res pass (and the high-res pass when "
                                                       "high_res_model is not connected)."}),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE", {"tooltip": "Video VAE (pixel route of the lift when rho > 0)."}),
                "latent_image": ("LATENT", {"tooltip": "Target-size H3 audio+video latent; noise_mask 0 = keep."}),
                "sampler": ("SAMPLER",),
                "sigmas": ("SIGMAS", {"tooltip": "One schedule for both passes; split at transition_step."}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 100.0, "step": 0.1, "round": 0.01}),
                "transition_step": ("INT", {"default": 6, "min": 1, "max": 10000,
                                    "tooltip": "Steps of the schedule run at low resolution; the rest run at full "
                                               "resolution."}),
                "lowres_scale": ("FLOAT", {"default": 0.5, "min": 0.25, "max": 1.0, "step": 0.05,
                                 "tooltip": "Size of the low-res pass (0.5 = half width and height)."}),
                "rho": ("FLOAT", {"default": 0.0, "min": 0.0, "max": 1.0, "step": 0.05,
                        "tooltip": "Share of the most inconsistent positions corrected toward the pixel-VAE route. "
                                   "0 = learned upscaler only (no VAE round trip)."}),
                "w_min": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.0, "step": 0.05}),
                "w_max": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
                "upscaler_model": (names, {"default": default,
                                   "tooltip": "Learned H3 3D latent upscaler for the lift (models/latent_upscale_models "
                                              "or upscale_models). none = nearest."}),
            },
            "optional": {
                "high_res_model": ("MODEL", {"tooltip": "Model for the high-res pass (default: low_res_model)."}),
                "highres_tiling": ("BOOLEAN", {"default": True,
                                   "tooltip": "High-res pass: measure the free VRAM and use the fewest strips that fit "
                                              "(full frame when it fits - no cost on large cards). Off = always full "
                                              "frame."}),
                **TILED.TILING_INPUTS,
                "ff_chunk_rows": ("INT", {"default": 0, "min": 0, "max": 262144, "step": 1024,
                                  "tooltip": "Low VRAM: run the DiT feed-forward on this many sequence rows at a time "
                                             "(both passes). Same result, lower peak memory, a little slower. 0 = off "
                                             "(the high-res tiling still switches it on by itself after an "
                                             "out-of-memory retry at the most strips)."}),
                "release_upscaler": ("BOOLEAN", {"default": True,
                                     "tooltip": "Drop the learned upscaler from RAM right after the lift."}),
                "unload_idle_models": ("BOOLEAN", {"default": True,
                                       "tooltip": "Between the passes: unload text encoder / VAEs / other idle models "
                                                  "+ empty the cache. The diffusion models stay loaded."}),
            },
        }

    RETURN_TYPES = ("LATENT",)
    RETURN_NAMES = ("latent",)
    FUNCTION = "sample"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Two-pass MiniMax H3 sampling (low-res pass, latent lift, full-res pass) on the bundled "
                   "Luna-H3-Sampler engine, with H3MD drift, low-res head, VRAM release between the passes and "
                   "high-res tiling. Outputs only the final latent.")

    last_lowres = None   # low-res pass endpoint (CPU, VAE space, low grid) of the last call, when captured
    last_tiling = None   # tiling stats of the last call
    last_steps = None    # per-step timing log of the last call
    last_anchor = None   # low-res anchor report of the last call (None = not applied)

    def sample(self, low_res_model, positive, negative, vae, latent_image, sampler, sigmas, seed, cfg,
               transition_step=6, lowres_scale=0.5, rho=0.0, w_min=0.5, w_max=1.0, upscaler_model="none",
               high_res_model=None, highres_tiling=True, tiling_axis="auto", tiling_overlap=8, ff_chunk_rows=0,
               release_upscaler=True, unload_idle_models=True,
               h3md_low_res_prefix=None, h3md_low_res_prefix_source="pixel_reencode", h3md_capture_lowres=False,
               h3md_on_lowres_end=None, h3md_low_res_suffix=None, h3md_lowres_anchor=None, h3md_progress=None, h3md_low_only=False,
               h3md_low_res_base=None, unique_id=None, **unknown):
        """Internal keyword inputs (set by the H3MD Loop):
        h3md_low_res_prefix: [1,24,vt,h,w] low-grid latent of the kept overlap prefix (engine low_res_head);
        h3md_capture_lowres: keep the low-res pass endpoint in self.last_lowres (next segment's native carry);
        h3md_on_lowres_end: callable(reason) run between the passes (depth ControlNet release);
        h3md_low_res_suffix: [1,24,vt,h,w] low-grid latent of the kept tail (rerun tail lock to the next segment);
        h3md_lowres_anchor: {"strength", "ref": {"mean", "std"}} - pull the new low-res rows back to the shot's first
        segment before the lift (also applied to the captured carry);
        h3md_low_res_base: [1,24,T,h,w] low-grid latent for EVERY kept row (local redraw: the segment's own low-res
        result)."""
        if unknown:
            LOG.warning("%s ignoring unknown inputs %s (removed options?)", _TAG, sorted(unknown))
        self.last_lowres, self.last_tiling, self.last_steps, self.last_anchor = None, None, [], None
        latent_in = sanitize_latent(latent_image)
        sig_low, sig_high = split_sigmas(sigmas, transition_step)
        grid_note = exact_grid_note(latent_in, lowres_scale)
        if grid_note:
            LOG.warning("%s %s", _TAG, grid_note)
        drift = find_drift(low_res_model, high_res_model)
        low = with_drift(low_res_model, drift)
        high = with_drift(high_res_model if high_res_model is not None else low_res_model, drift)
        if ff_chunk_rows:
            low, high = FF.with_ff_chunk(low, ff_chunk_rows), FF.with_ff_chunk(high, ff_chunk_rows)
        if highres_tiling:
            self.last_tiling = {}
            high = TILED.tiled_high_model(high, {"axis": tiling_axis, "overlap": tiling_overlap}, self.last_tiling)
        head = None
        if h3md_low_res_prefix is not None:
            why = check_low_res_head(h3md_low_res_prefix, latent_in, lowres_scale)
            if why is None:
                head = {"samples": h3md_low_res_prefix}
                LOG.info("%s low-res pass: kept prefix %s = %s", _TAG, tuple(h3md_low_res_prefix.shape),
                         "the previous segment's own low-res pass (native carry)"
                         if h3md_low_res_prefix_source == "native_lowres_carry"
                         else "pixel-space re-encode of the previous frames")
            else:
                LOG.warning("%s low-res prefix not used (%s); the engine resizes the full-res prefix", _TAG, why)
        tail = None
        if h3md_low_res_suffix is not None:  # H3MD rerun: tail lock
            why = check_low_res_tail(h3md_low_res_suffix, latent_in, lowres_scale)
            if why is None:
                tail = {"samples": h3md_low_res_suffix}
                LOG.info("%s low-res pass: kept tail %s = the next segment's own low-res head (tail lock)", _TAG,
                         tuple(h3md_low_res_suffix.shape))
            else:
                LOG.warning("%s low-res tail not used (%s); the engine resizes the full-res tail", _TAG, why)
        base = None
        if h3md_low_res_base is not None:  # H3MD local redraw: kept rows = the segment's own low-res result
            why = check_low_res_base(h3md_low_res_base, latent_in, lowres_scale)
            if why is None:
                base = {"samples": h3md_low_res_base}
                LOG.info("%s low-res pass: kept rows = the segment's own low-res result %s (local redraw)", _TAG,
                         tuple(h3md_low_res_base.shape))
            else:
                LOG.warning("%s low-res base not used (%s); the kept rows are the downscaled final latent", _TAG, why)
        lifter = None
        if upscaler_model and upscaler_model != "none":
            lifter = lambda z, hw: luna_upscaler.learned_latent_lift(z, hw, upscaler_model)  # noqa: E731

        passes = {"low_done": False, "anchor": None}

        def lift(z0_low, vae_, out_hw, rho_, w_min_, w_max_, latent_format, latent_lifter=None, mask=None):
            passes["low_done"] = True
            if h3md_lowres_anchor:  # H3MD low-res anchor (2026-10-30): new rows back to the shot's first segment
                xf = LA.transform(z0_low, LA.held_rows(mask, int(z0_low.shape[2])),
                                  h3md_lowres_anchor.get("ref"), float(h3md_lowres_anchor.get("strength", 0.0)))
                if xf is not None:
                    z0_low = LA.apply(z0_low, xf)
                    passes["anchor"] = xf
                    LOG.info("%s low-res anchor: %d new latent steps pulled to the shot's first segment (strength "
                             "%.2f, channel mean shift %.3f std, spread ratio %.2f-%.2f)", _TAG,
                             xf["report"]["new_steps"], xf["report"]["strength"], xf["report"]["mean_shift"],
                             *xf["report"]["std_ratio"])
            self._between_passes(h3md_on_lowres_end, unload_idle_models, [low, high])
            try:
                return luna_lift.lift_video(z0_low, vae_, out_hw, rho_, w_min_, w_max_, latent_format,
                                            latent_lifter=latent_lifter, mask=mask)
            finally:
                if release_upscaler and lifter is not None and \
                        luna_upscaler.MODEL_CACHE.pop(upscaler_model, None) is not None:
                    LOG.info("%s upscaler '%s' released from RAM", _TAG, upscaler_model)

        n_low, n_high = sig_low.numel() - 1, sig_high.numel() - 1
        LOG.info("%s seed %d, %d low-res + %d high-res steps (scale %.2f), cfg %.2f, upscaler %s, tiling %s%s",
                 _TAG, int(seed), n_low, n_high, float(lowres_scale), float(cfg), upscaler_model,
                 f"on ({tiling_axis}, overlap {tiling_overlap})" if highres_tiling else "off",
                 ", drift" if drift is not None else "")
        h3md_vram.log_memory("before low-res pass")
        try:
            latent, low_latent = LUNA.two_pass_sample(
                low, positive, negative, vae, latent_in,
                timed_sampler(sampler, "low_res", n_low, self.last_steps, h3md_progress),
                timed_sampler(sampler, "high_res", n_high, self.last_steps, h3md_progress),
                "simple", n_low + n_high, n_low, int(seed), float(cfg), float(cfg), float(lowres_scale),
                float(rho), float(w_min), float(w_max), latent_lifter=lifter, high_res_model=high,
                low_res_head=head, sigmas_low=sig_low, sigmas_high=sig_high, lift=lift, low_res_tail=tail,
                pass1_store=h3md_progress, low_only=bool(h3md_low_only), low_res_base=base)
        except BaseException as exc:
            if not TILED.is_oom(exc) or isinstance(exc, TILED.HighResOutOfMemory):
                raise
            if not passes["low_done"]:
                raise SamplerOutOfMemory(
                    f"{_TAG} out of GPU memory in the LOW-res pass. Set ff_chunk_rows (e.g. 8192) in H3MD Loop "
                    "Options, lower the resolution or the segment length, use fewer / smaller reference media, or "
                    "start ComfyUI with --lowvram / --reserve-vram. 低清阶段显存不足：可在 H3MD Loop Options 里把 "
                    "ff_chunk_rows 设为 8192，或降低分辨率、缩短段长、减少参考素材，或用 --lowvram / --reserve-vram "
                    "启动 ComfyUI。") from exc
            if not highres_tiling:
                raise SamplerOutOfMemory(
                    f"{_TAG} out of GPU memory in the high-res pass with highres_tiling OFF. Turn highres_tiling on "
                    "(it uses strips only when the full frame does not fit). 高清阶段显存不足且 highres_tiling 关闭："
                    "请打开 highres_tiling（放得下时它不会分块）。") from exc
            raise
        h3md_vram.log_memory("after high-res pass")
        if passes["anchor"] is not None:
            self.last_anchor = passes["anchor"]["report"]
        if h3md_capture_lowres:
            self.last_lowres = _unbind(low_latent["samples"])[0].detach().to("cpu", torch.float32).clone()
            if passes["anchor"] is not None:  # H3MD low-res anchor: the carry is the corrected picture
                self.last_lowres = LA.apply(self.last_lowres, passes["anchor"])
            LOG.info("%s captured low-res endpoint %s", _TAG, tuple(self.last_lowres.shape))
        return ({"samples": latent["samples"]},)

    @staticmethod
    def _between_passes(on_lowres_end, unload_idle, keep):
        if on_lowres_end is not None:
            try:  # H3MD depth: ControlNet patch cleanup + unload before the high-res pass
                on_lowres_end("(after low-res pass)")
            except Exception as exc:
                LOG.warning("%s depth ControlNet release after the low-res pass failed: %s", _TAG, exc)
        if unload_idle:
            h3md_vram.unload_idle_models(keep, "(low-res -> high-res)")
        else:
            h3md_vram.soft_empty_cache("(low-res -> high-res)")


NODE_CLASS_MAPPINGS = {"H3MDSampler": H3MDSampler}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDSampler": "H3MD Sampler (two-pass)"}
