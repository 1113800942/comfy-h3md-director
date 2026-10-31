// H3MD - "H3MD Loop Options" basic / advanced view (2026-10-27).
// Basic view (default): only the options that are real choices are shown; every other option is HIDDEN (not
// removed: widgets_values keep their positions, old workflows load unchanged) and set to the tested recommended
// value, so a value saved in an old workflow (e.g. continue_lowres_prefix = pixel_reencode) can no longer hide in
// the background. "Advanced 高级选项" (node property h3md_advanced, saved with the workflow) shows everything and
// leaves the values as you set them; switching back to basic restores the recommended values.
import { app } from "../../scripts/app.js";

const NODE = "H3MDLoopOptions";
const PROP = "h3md_advanced";
export const BASIC = ["drift_strength", "seam_tone_match", "cache_dir", "cache_dtype",
  "slow_step_guard", "audio_crossfade_ms", "ff_chunk_rows"];
// tested / calibrated values (2026-10-27 tests on the RTX 5090; tiling calibration 2026-10-17)
export const RECOMMENDED = {
  continue_lowres_prefix: "native_lowres_carry", merge_overlap_source: "next", seam_decode_context: 2,
  continue_audio: true, release_vram_between_segments: true, release_upscaler: true, unload_idle_models: true,
  unload_models_before_merge: true, vram_release: "deep",
  drift_lock_tokens: 2, seam_tone_strength: 1, seam_tone_global: 1, ref_image_size: "match",
  tiling_axis: "auto", tiling_overlap: 8,
};
const TOGGLE = "高级选项 advanced";

function applyView(node) {
  const adv = !!(node.properties && node.properties[PROP]);
  for (const w of node.widgets || []) {
    if (!w || w.name === TOGGLE || BASIC.includes(w.name)) continue;
    w.hidden = !adv;
    if (!adv && Object.prototype.hasOwnProperty.call(RECOMMENDED, w.name)) {
      const v = RECOMMENDED[w.name];
      if (!Array.isArray(w.options?.values) || w.options.values.includes(v)) w.value = v;
    }
  }
  const t = (node.widgets || []).find((w) => w && w.name === TOGGLE);
  if (t) t.value = adv;
  try { node.setSize?.([node.size[0], node.computeSize()[1]]); } catch { /* ignore */ }
  node.setDirtyCanvas?.(true, true);
}

function ensureToggle(node) {
  if ((node.widgets || []).some((w) => w && w.name === TOGGLE)) return;
  const w = node.addWidget("toggle", TOGGLE, !!(node.properties && node.properties[PROP]), (v) => {
    node.properties = node.properties || {};
    node.properties[PROP] = !!v;
    applyView(node);
  }, { on: "显示全部 show all", off: "推荐值 recommended", serialize: false });
  w.serialize = false;   // UI only: never part of widgets_values or the prompt
}

app.registerExtension({
  name: "H3MD.LoopOptionsView",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      try { this.properties = this.properties || {}; if (!(PROP in this.properties)) this.properties[PROP] = false;
            ensureToggle(this); applyView(this); } catch (e) { console.error("[H3MD] options view init failed", e); }
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      const run = () => { try { ensureToggle(this); applyView(this); } catch (e) { console.error("[H3MD] options view failed", e); } };
      run();
      setTimeout(run, 0);   // after any other extension's onConfigure (e.g. the old-layout remap)
      return r;
    };
  },
});
