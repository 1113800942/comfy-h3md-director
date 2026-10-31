// H3MD - pure helpers (no ComfyUI imports) for h3md_long_video_ext.js; also used by the node tests.
// 2026-10-18 (late): the Loop's 6th output 'segments' is now called 'long video' (same slot 5, same type
// H3MD_SEGMENTS, so saved links still connect), and the Loop's merge_images widget was removed.
//  * A workflow saved earlier stores the old output name in its JSON; relabelLoopOutput() shows 'long video'.
//  * H3MD Save Video Interp keeps its input id 'segments' (API prompts / saved links use it); only the label changes.
//  * staleInputSlots() finds unlinked widget-input slots of removed widgets (Loop: merge_images; Save Video Interp:
//    source_fps, preset, cache_folder, folder_*) that a saved workflow may still list; the extension removes them.
// Nothing here changes widgets_values or node size.

export const LONG_VIDEO = "long video";
export const SEGMENTS_TYPE = "H3MD_SEGMENTS";
export const LOOP_NODES = ["H3MDLoopSampler", "H3MDLoopSampler2"];
export const SAVE_NODE = "H3MDSaveVideoInterp";
export const SAVE_INPUT = "segments";
export const REMOVED_WIDGETS = ["merge_images"];   // Loop
// Save Video Interp widgets removed on 2026-10-19 (values re-mapped by h3md_save_video_interp_ui.js)
export const SAVE_REMOVED_WIDGETS = ["source_fps", "preset", "cache_folder", "folder_overlap_source",
  "folder_audio_crossfade_ms", "pingpong"];

// Loop: output of type H3MD_SEGMENTS -> name + label 'long video'. Returns the number of slots changed.
export function relabelLoopOutput(node) {
  let n = 0;
  for (const o of node?.outputs ?? []) {
    if (!o || o.type !== SEGMENTS_TYPE) continue;
    if (o.name !== LONG_VIDEO || o.label !== LONG_VIDEO) { o.name = LONG_VIDEO; o.label = LONG_VIDEO; n++; }
  }
  return n;
}

// Save Video Interp: input id stays 'segments', label 'long video'.
export function relabelSaveInput(node) {
  let n = 0;
  for (const i of node?.inputs ?? []) {
    if (!i || i.name !== SAVE_INPUT) continue;
    if (i.label !== LONG_VIDEO) { i.label = LONG_VIDEO; n++; }
  }
  return n;
}

// Loop: indices (descending) of input slots for removed widgets that are not linked and have no widget any more.
export function staleInputSlots(node, removed = REMOVED_WIDGETS) {
  const widgets = new Set((node?.widgets ?? []).map((w) => w?.name));
  const out = [];
  (node?.inputs ?? []).forEach((i, k) => {
    if (i && removed.includes(i.name) && !widgets.has(i.name) && i.link == null) out.push(k);
  });
  return out.reverse();
}

// Save Video Interp outputs: slot 2 was 'fps' (FLOAT) until 2026-10-19, now 'video' (VIDEO). A saved workflow keeps
// the old slot; its fps links cannot carry a VIDEO, so they are dropped. Returns the dropped link ids.
export const SAVE_OUTPUTS = [["video_path", "STRING"], ["frame_count", "INT"], ["video", "VIDEO"]];
const isOldFps = (node, info = null) => {
  const o = node?.outputs?.[2];
  const saved = info?.outputs?.[2];
  return !!o && !!((o.name === "fps" || o.type === "FLOAT") || (saved && (saved.name === "fps" || saved.type === "FLOAT")));
};

// Also catches a slot the frontend already renamed from the node def: any link on it that is not a VIDEO link
// (the old fps FLOAT links) is stale.
export function fixSaveOutputs(node, info = null) {
  const o = node?.outputs?.[2];
  if (!o) return [];
  const linkOf = (id) => node.graph?.links?.get?.(id) ?? node.graph?.links?.[id];
  const oldSlot = isOldFps(node, info);
  const ids = [...(o.links ?? [])];
  const stale = oldSlot ? ids : ids.filter((id) => { const l = linkOf(id); return !!l && l.type != null && l.type !== "VIDEO"; });
  if (oldSlot) { o.name = "video"; o.type = "VIDEO"; o.label = "video"; }
  if (!stale.length) return [];
  if (stale.length === ids.length && node.disconnectOutput) node.disconnectOutput(2);
  else if (node.graph?.removeLink) for (const id of stale) node.graph.removeLink(id);
  else o.links = ids.filter((id) => !stale.includes(id));
  return stale;
}
fixSaveOutputs.isOld = isOldFps;
