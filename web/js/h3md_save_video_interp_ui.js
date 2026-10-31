// H3MD - pure helpers (no ComfyUI imports) for h3md_save_video_interp.js; also used by the node tests.
// H3MD Save Video Interp:
//  (1) greys out what does not apply: the RIFE options while `interpolate` is off; crf / pix_fmt unless the format is
//      h264/h265-mp4; loop_count unless gif / webp. target_fps is always enabled (it is the file's frame rate).
//  (2) two read-only (edits undone), NOT serialised lines: `source_fps` = 24 (H3 renders a fixed 24 fps) right after
//      `interpolate`, and the cache folder right after `delete_cache_after_save` - no label, only the absolute path
//      (the canvas truncates a long one; the full path + state are the hover tooltip).
//  (3) remapSaveValues(): workflows saved with the first layout (2026-10-18: source_fps, preset, cache_folder,
//      folder_overlap_source, folder_audio_crossfade_ms) and with the short-lived v2 layout (2026-10-19, 17 values
//      incl. pingpong), v3 (16, filename_prefix after save_output) are re-mapped BY NAME onto the current widgets
//      (filename_prefix + output_folder first); removed values are dropped.
// Nothing here changes what the node does; display widgets have serialize = false (widgets_values unchanged).

export const NODE = "H3MDSaveVideoInterp";
export const INTERP_WIDGETS = ["scale", "model_name", "batch_size", "use_fp16"];
export const VIDEO_FORMATS = ["video/h264-mp4", "video/h265-mp4"];
export const LOOP_FORMATS = ["image/gif", "image/webp"];
export const VIDEO_ONLY = ["crf", "pix_fmt"];
export const LOOP_ONLY = ["loop_count"];
export const AUDIO_ONLY = ["trim_to_audio"];       // h264/h265-mp4 (formats with an audio track)
export const OUTPUT_ONLY = ["output_folder"];      // greyed while save_output is off (temp folder)
export const PLAYER = "h3md_player";
export const PLAYER_HEIGHT = 270;                  // fixed: the node never grows / shrinks with the clip
export const DISPLAY = "cache_folder_path";
export const AFTER = "delete_cache_after_save";
export const FPS_DISPLAY = "h3md_source_fps";   // own id: never collides with the removed source_fps widget
export const FPS_LABEL = "source_fps";
export const FPS_TEXT = "24 (fixed)";
export const SOURCE_FPS = 24;

// first layout (widgets_values order) of 2026-10-18
export const LAYOUT_V1 = ["interpolate", "source_fps", "target_fps", "scale", "model_name", "batch_size", "use_fp16",
  "filename_prefix", "output_folder", "crf", "preset", "delete_cache_after_save", "cache_folder",
  "folder_overlap_source", "folder_audio_crossfade_ms"];
// v2 (2026-10-19, before pingpong was removed): the serialised widget order, 17 values
export const LAYOUT_V2 = ["interpolate", "target_fps", "scale", "model_name", "batch_size", "use_fp16", "format",
                          "pix_fmt", "crf", "loop_count", "pingpong", "trim_to_audio", "save_metadata", "save_output",
                          "filename_prefix", "output_folder", "delete_cache_after_save"];
export const LAYOUT_V3 = LAYOUT_V2.filter((n) => n !== "pingpong");
// current serialised order: filename_prefix + output_folder first (delete_cache_after_save stays last)
export const LAYOUT = ["filename_prefix", "output_folder",
                       ...LAYOUT_V3.filter((n) => n !== "filename_prefix" && n !== "output_folder")];
const PRESETS = ["auto", "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];

const val = (node, name) => node?.widgets?.find((w) => w.name === name)?.value;

export function syncInterpWidgets(node) {
  const on = !!val(node, "interpolate");
  const fmt = val(node, "format");
  const video = fmt === undefined || VIDEO_FORMATS.includes(fmt);
  const loop = LOOP_FORMATS.includes(fmt);
  for (const w of node?.widgets ?? []) {
    if (INTERP_WIDGETS.includes(w.name)) w.disabled = !on;
    else if (VIDEO_ONLY.includes(w.name)) w.disabled = !video;
    else if (LOOP_ONLY.includes(w.name)) w.disabled = !loop;
    else if (AUDIO_ONLY.includes(w.name)) w.disabled = !video;
    else if (OUTPUT_ONLY.includes(w.name)) w.disabled = val(node, "save_output") === false;
    else if (w.name === "target_fps") w.disabled = false;
  }
  return on;
}

function readOnly(node, name, value, tooltip, after) {
  let d = node.widgets?.find((w) => w.name === name);
  if (!d) {
    d = node.addWidget("text", name, value, function () { this.value = this.h3mdShown ?? value; }, { serialize: false });
    d.serialize = false;
    d.h3mdShown = value;
    d.tooltip = tooltip;
    const ws = node.widgets, i = ws.indexOf(d), at = ws.findIndex((w) => w.name === after);
    if (at >= 0 && i >= 0 && i !== at + 1) { ws.splice(i, 1); ws.splice(at + 1, 0, d); }
  }
  return d;
}

// read-only "source_fps 24 (fixed)" right after `interpolate`
export function ensureFpsDisplay(node) {
  const d = readOnly(node, FPS_DISPLAY, FPS_TEXT,
    "Read-only: H3 renders a fixed 24 fps; RIFE interpolates 24 -> target_fps.", "interpolate");
  d.label = FPS_LABEL;
  return d;
}

// Read-only, unlabelled cache path. NOT `disabled`: the 1.55 frontend draws a disabled widget without its value.
export const NO_LABEL = "\u200b";
export function ensureCacheDisplay(node) {
  const d = readOnly(node, DISPLAY, "", "H3MD cache folder of this run (set when the node runs).", AFTER);
  // no label: only the path is drawn. litegraph uses `label || name`, so "" would fall back to the name;
  // a zero-width space is truthy but draws (and measures) nothing; the instance getter covers other builds
  d.label = NO_LABEL;
  try { Object.defineProperty(d, "displayName", { get: () => "", configurable: true }); } catch (e) { /* frozen */ }
  return d;
}

export function setCacheDisplay(node, dir, state) {
  if (!dir) return null;
  const d = ensureCacheDisplay(node);
  d.value = d.h3mdShown = String(dir);
  d.tooltip = state ? `${dir}\n[${state}]` : String(dir);
  return d.value;
}

// -> values in the current widget order, or null when the saved node already has the current layout.
// `named` = the frontend's widgets_values_named (1.55 saves it, in the saved widget order; preferred), else the
// positional widgets_values. Layouts: V1 (2026-10-18, 15), V2 (17, pingpong), V3 (16, filename_prefix after
// save_output), current (16, filename_prefix + output_folder first).
const V1_KEYS = ["source_fps", "preset", "cache_folder", "folder_overlap_source", "folder_audio_crossfade_ms"];
const REMOVED_KEYS = [...V1_KEYS, "pingpong"];
const isFormat = (v) => typeof v === "string" && /^(video|image)\//.test(v);
function namedIsOld(names, named) {
  const keys = Object.keys(named);
  if (REMOVED_KEYS.some((k) => k in named)) return true;
  const saved = keys.filter((k) => names.includes(k)), cur = names.filter((k) => k in named);
  return saved.length > 1 && saved.join("\u0000") !== cur.join("\u0000");   // same widgets, other order
}
export function remapSaveValues(names, values, defaults = {}, named = null) {
  if (!Array.isArray(names)) return null;
  let old = null, v1 = false;
  const pos = (layout) => Object.fromEntries(layout.map((n, i) => [n, values[i]]));
  if (named && typeof named === "object" && !Array.isArray(named) && namedIsOld(names, named)) {
    old = { ...named };
    v1 = V1_KEYS.some((k) => k in named);
  } else if (named && typeof named === "object" && Object.keys(named).length) {
    return null;                                    // named in the current order: nothing to do
  } else if (!Array.isArray(values) || typeof values[0] !== "boolean" || typeof values[1] !== "number") {
    return null;                                    // current layout starts with filename_prefix (a string)
  } else if (values.length === LAYOUT_V1.length && PRESETS.includes(values[10])) {
    old = pos(LAYOUT_V1); v1 = true;
  } else if (values.length === LAYOUT_V2.length && isFormat(values[6]) && typeof values[10] === "boolean"
             && typeof values[11] === "boolean") {
    old = pos(LAYOUT_V2);
  } else if (values.length === LAYOUT_V3.length && isFormat(values[6]) && typeof values[10] === "boolean"
             && typeof values[13] === "string") {
    old = pos(LAYOUT_V3);
  }
  if (!old) return null;
  // the first layout always wrote 24 fps with interpolation off (target_fps was ignored): keep that behaviour.
  // V2 / V3 already meant target_fps = the file's frame rate: kept as saved.
  if (v1 && !old.interpolate) old.target_fps = SOURCE_FPS;
  return names.map((n) => (n in old && !REMOVED_KEYS.includes(n) ? old[n] : defaults[n]));
}

// widget-input slots a saved workflow may still list for widgets that no longer exist
export const REMOVED = ["source_fps", "preset", "cache_folder", "folder_overlap_source", "folder_audio_crossfade_ms"];

// preview entry (ui.h3md_video[0]) -> {kind: 'video' | 'image' | 'none', url}
export function previewSource(entry, now = 0) {
  if (!entry || !entry.filename) return { kind: "none", url: "" };
  const fmt = entry.format || "video/h264-mp4";
  let filename = entry.filename, subfolder = entry.subfolder || "";
  if (fmt === "image/png-sequence") {
    if (!entry.first_frame) return { kind: "none", url: "" };
    subfolder = subfolder ? `${subfolder}/${filename}` : filename;
    filename = entry.first_frame;
  }
  const q = new URLSearchParams({ filename, subfolder, type: entry.type || "output" });
  if (now) q.set("t", String(now));               // cache-buster: a re-run may reuse nothing, but be safe
  return { kind: fmt.startsWith("video/") ? "video" : "image", url: `/view?${q.toString()}` };
}
