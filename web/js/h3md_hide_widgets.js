// H3MD - pure helper (no ComfyUI imports) for h3md_hide_widgets_ext.js; also used by the node tests.
// 2026-10-18: the Loop's `delete_cache` widget is hidden, NOT removed: it stays in node.widgets and keeps its
// serialize flag, so widgets_values keeps the same length/positions and old workflows load unchanged. The backend
// ignores it (the Loop never deletes its cache before the video is saved; H3MD Save Video Interp does).

export const HIDDEN_WIDGETS = {
  H3MDLoopSampler: ["delete_cache"],   // legacy node
  H3MDLoopSampler2: ["delete_cache"],  // "H3MD Loop Sampler" main node
  H3MDDepthControl: ["sam3_enabled"],   // 2026-10-27 sam3box: ignored by the backend; the per-video SAM3 box in the Director decides
  H3MDMotionControl: ["sam3_enabled"],  // 2026-11-04 pose-exp: same as H3MD Depth Control
  // H3MDLoopOptions: handled by h3md_options_view.js since 2026-10-27 (basic / advanced view, recommended values)
};

// hides the named widgets of `node` in place; returns the names that were hidden
export function hideWidgets(node, names) {
  const done = [];
  for (const w of node?.widgets ?? []) {
    if (!w || !names.includes(w.name)) continue;
    w.hidden = true;  // the ComfyUI frontend's own visibility flag (core extensions hide widgets the same way)
    done.push(w.name);
  }
  return done;
}
