"""H3MD Loop Sampler split: main node "H3MD Loop Sampler" (H3MDLoopSampler2) + "H3MD Loop Options" (H3MDLoopOptions).

Compatibility: the original class id "H3MDLoopSampler" is kept unchanged (all inputs, same positional widget
order) and only re-labelled "H3MD Loop Sampler (legacy)", so every saved workflow loads exactly as before.
The new main node is a NEW class id, so no saved widgets_values can ever be mapped onto it by position.

Both nodes are views of the same computation: H3MDLoopSampler2.run fills every advanced input that is not supplied
through `options` with the legacy node's own widget default and calls H3MDLoopSampler.run.  So
  * options not connected            == legacy node with all advanced widgets at their defaults
  * same values                      == same segment-cache key as the legacy node.
"""

from __future__ import annotations

import logging

try:
    from . import h3md_loop as L
except ImportError:  # flat import (tests)
    import h3md_loop as L

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Loop]"
OPTIONS_TYPE = "H3MD_LOOP_OPTIONS"

# Inputs that stay on the main node (everything else of the legacy node goes to H3MD Loop Options).
# The H3MD Sampler's required widgets (cfg, transition_step, lowres_scale, rho, w_min, w_max, upscaler_model, and any
# future required one) also stay on the main node.
MAIN_INPUTS = ("rerun_segments", "stop_after", "rerun_new_seed",   # H3MD rerun (2026-10-30): top of the node
               "plan", "clip", "vae", "audio_vae", "low_res_model", "sampler", "sigmas", "seed",
               "seed_mode", "decode_output",
               "high_res_model", "highres_tiling",
               "cache_mode", "delete_cache", "resume_from_cache", "semantic_bridge",
               "depth_control",   # H3MD depth (2026-10-26): link input, moved after `options` below
               "segments_to_run")   # v0.3 Control Panel link input, moved to the very end below
_WIDGET_TYPES = ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO")


def is_widget(spec) -> bool:
    return isinstance(spec[0], (list, tuple)) or spec[0] in _WIDGET_TYPES


def spec_default(spec):
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if "default" in opts:
        return opts["default"]
    if isinstance(spec[0], (list, tuple)):
        return spec[0][0] if spec[0] else None
    return {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(spec[0])


def split_spec(legacy: dict) -> tuple:
    """legacy INPUT_TYPES -> (main INPUT_TYPES, options INPUT_TYPES).  Order inside each node follows the legacy order."""
    main = {"required": {}, "optional": {}, "hidden": dict(legacy.get("hidden", {}))}
    opts = {"required": {}, "optional": {}}
    sampler_required = set(L.S.H3MDSampler.INPUT_TYPES()["required"])
    for sect in ("required", "optional"):
        for name, spec in legacy.get(sect, {}).items():
            if name in MAIN_INPUTS or (sect == "required" and name in sampler_required):
                main[sect][name] = spec
            elif is_widget(spec):
                opts["required"][name] = spec
            else:  # an unexpected link input stays on the main node
                main[sect][name] = spec
    # merge_images (last main-node widget, 2026-10-18) was removed again: the Loop never stitches the full video.
    # Workflows saved with it carry one extra trailing widgets_value, which the positional load ignores.
    main["optional"]["options"] = (OPTIONS_TYPE, {"tooltip": "H3MD Loop Options (advanced switches). "
                                                  "Not connected = all defaults."})
    if "depth_control" in main["optional"]:  # H3MD depth: appended LAST (after options) -> old links keep their slots
        main["optional"]["depth_control"] = main["optional"].pop("depth_control")
    if "segments_to_run" in main["optional"]:  # v0.3: appended LAST -> old links keep their slots
        main["optional"]["segments_to_run"] = main["optional"].pop("segments_to_run")
    return main, opts


def option_defaults() -> dict:
    _, opts = split_spec(L.H3MDLoopSampler.INPUT_TYPES())
    return {k: spec_default(v) for k, v in opts["required"].items()}


# Option-dict keys that are not widgets of H3MD Loop Options (added by helper nodes; no widget layout change):
# hires_mode (optional extra key of the options dict).  Absent = the old behaviour.
EXTRA_OPTION_KEYS = ("hires_mode",)
# Removed options (2026-10-30 (d): the drift anchor did not stop the drift): dropped from old option dicts with a warning.
REMOVED_OPTION_KEYS = ("drift_handoff_match", "drift_seam_anchor", "drift_detail")


class H3MDLoopOptions:
    @classmethod
    def INPUT_TYPES(cls):
        return split_spec(L.H3MDLoopSampler.INPUT_TYPES())[1]

    RETURN_TYPES = (OPTIONS_TYPE,)
    RETURN_NAMES = ("options",)
    FUNCTION = "build"
    CATEGORY = L.CATEGORY
    DESCRIPTION = ("Advanced switches for H3MD Loop Sampler: low-res prefix (Alpha/Beta), drift handoff, "
                   "seams, reference windows, tiling, VRAM/cache details.")

    def build(self, **kwargs):
        known = option_defaults()
        unknown = sorted(set(kwargs) - set(known))
        if unknown:
            raise ValueError(f"{_TAG} H3MD Loop Options: unknown inputs {unknown}")
        out = dict(known)
        out.update(kwargs)
        changed = {k: v for k, v in out.items() if v != known[k]}
        LOG.info("%s options: %s", _TAG, changed or "all defaults")
        return (out,)


class H3MDLoopSampler2(L.H3MDLoopSampler):
    @classmethod
    def INPUT_TYPES(cls):
        return split_spec(L.H3MDLoopSampler.INPUT_TYPES())[0]

    DESCRIPTION = ("Runs all segments of an H3MD plan (same computation as the legacy H3MD Loop Sampler). "
                   "Advanced switches: connect H3MD Loop Options; semantic bridges: connect H3MD Semantic Bridge."
                   "\n\n" + L.OUTPUTS_NOTE_EN + "\n\n" + L.OUTPUTS_NOTE_ZH)
    # OUTPUT_TOOLTIPS inherited from H3MDLoopSampler (same outputs)

    def run(self, options=None, **kwargs):
        defaults = option_defaults()
        values = dict(defaults)
        if options is not None:
            if not isinstance(options, dict):
                raise ValueError(f"{_TAG} options must come from H3MD Loop Options")
            options = dict(options)
            extra = {k: options.pop(k) for k in EXTRA_OPTION_KEYS if k in options}  # e.g. hires_mode
            for gone in REMOVED_OPTION_KEYS:
                if options.pop(gone, None):
                    LOG.warning("%s option '%s' was removed (it did not stop the drift); use lowres_anchor", _TAG, gone)
            unknown = sorted(set(options) - set(defaults))
            if unknown:
                raise ValueError(f"{_TAG} options contain unknown keys {unknown} (re-create the H3MD Loop Options node)")
            values.update(options)
        clash = sorted(set(values) & set(kwargs))
        if clash:
            raise ValueError(f"{_TAG} inputs given twice (main node and options): {clash}")
        kwargs.update(values)
        if options is not None:
            kwargs.update(extra)
        return super().run(**kwargs)


NODE_CLASS_MAPPINGS = {"H3MDLoopSampler2": H3MDLoopSampler2, "H3MDLoopOptions": H3MDLoopOptions}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDLoopSampler2": "H3MD Loop Sampler", "H3MDLoopOptions": "H3MD Loop Options"}
