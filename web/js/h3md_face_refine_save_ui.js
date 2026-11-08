// H3MD - UI for "H3MD Save Video Face Refine And Interp" (H3MDSaveVideoFaceRefineInterp).
// Same look as H3MD Save Video Interp, for this node type only (the existing extensions are keyed by node type and
// are not changed):
//  (1) input 'segments' keeps its id, label 'long video' (like h3md_long_video_ext.js does for Save Video Interp);
//  (2) the read-only cache-folder line (ui.h3md_cache_dir /
//      h3md_cache_state + the live 'h3md.save_video_interp' events, filtered by this node's id), the read-only
//      "source_fps 24 (fixed)" line and the same grey-out of RIFE / format options as Save Video Interp;
//  (3) face_refine off greys out every face-refine widget (tracking, stitch, vosr2_seed), live on toggle.
// No old-layout remap (new node, no old workflows). Display widgets are not serialised (widgets_values unchanged).
// Pure helpers are imported (unchanged) from h3md_save_video_interp_ui.js and h3md_long_video.js.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { syncInterpWidgets, ensureFpsDisplay, ensureCacheDisplay, setCacheDisplay } from "./h3md_save_video_interp_ui.js";
import { relabelSaveInput } from "./h3md_long_video.js";

export const FR_NODE = "H3MDSaveVideoFaceRefineInterp";
const EVENT = "h3md.save_video_interp";            // sent by h3md_stream_save.notify_ui (node id as a string)
const isMine = (node) => node?.comfyClass === FR_NODE || node?.type === FR_NODE;

// face-refine widgets (h3md_face_refine_save.py: TRACK_REQUIRED/OPTIONAL, STITCH_REQUIRED/OPTIONAL, vosr2_seed).
// VOSR2 scale is fixed at 1 (no widget); `scale` is RIFE's and stays with the interpolate grey-out.
export const FACE_WIDGETS = [
  "detector", "confidence", "crop_factor", "canvas_width", "canvas_height", "canvas_mode", "smooth_window",
  "size_smooth_window", "smooth_method", "size_mode",
  "identity_track", "identity_threshold", "select", "fallback_detector", "fallback_head_frac", "select_index",
  "identity_model", "cut_detection", "cut_threshold", "absent_shots", "X", "Y", "frame_index",
  "paste_region", "mask_dilation", "feather", "colour_match", "blend", "undetected_frames", "feather_scales_with_crop",
  "vosr2_seed", "refine_model"];
export function syncFaceWidgets(node) {
  // face_refine fed by a link (e.g. from the Director / an outer subgraph input): its value is only known at run time,
  // so the face widgets stay editable (11-07 user report: everything greyed out whatever the outer value was)
  const linked = (node?.inputs ?? []).some((i) => i.name === "face_refine" && i.link != null);
  const on = linked || node?.widgets?.find((w) => w.name === "face_refine")?.value !== false;
  for (const w of node?.widgets ?? []) if (FACE_WIDGETS.includes(w.name)) w.disabled = !on;
  return on;
}

// no built-in player any more (2026-10-23): 'preview' output -> H3MD Video Preview (h3md_video_preview.js)

const first = (v) => (Array.isArray(v) ? v[0] : v);

app.registerExtension({
  name: "H3MD.SaveVideoFaceRefineInterp",
  setup() {
    api.addEventListener(EVENT, (ev) => {
      try {
        const d = ev.detail || {};
        const node = app.graph?.getNodeById?.(Number(d.node)) ?? app.graph?.getNodeById?.(d.node);
        if (isMine(node)) {
          setCacheDisplay(node, d.cache_dir, d.state);
          node.setDirtyCanvas?.(true, true);
        }
      } catch (e) { console.error("[H3MD] face refine save event failed", e); }
    });
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== FR_NODE) return;
    const refresh = (node) => {
      try {
        relabelSaveInput(node);
        syncInterpWidgets(node);
        syncFaceWidgets(node);
        ensureFpsDisplay(node);
        ensureCacheDisplay(node);
        node.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] face refine save UI failed", e); }
    };
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      const node = this;
      for (const name of ["interpolate", "format", "save_output", "face_refine"]) {
        const sw = this.widgets?.find((w) => w.name === name);
        if (!sw) continue;
        const cb = sw.callback;
        sw.callback = function () {
          const out = cb?.apply(this, arguments);
          refresh(node);
          return out;
        };
      }
      refresh(this);
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      refresh(this);
      return r;
    };
    const onConn = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
      const r = onConn?.apply(this, arguments);
      refresh(this);   // face_refine linked / unlinked -> face widgets editable or follow the toggle
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      try {
        setCacheDisplay(this, first(message?.h3md_cache_dir), first(message?.h3md_cache_state));
        this.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] face refine save onExecuted failed", e); }
      return r;
    };
  },
});
