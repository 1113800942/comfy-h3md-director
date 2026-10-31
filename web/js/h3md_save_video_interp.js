// H3MD - UI for "H3MD Save Video Interp" (helpers in h3md_save_video_interp_ui.js).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { NODE, syncInterpWidgets, ensureFpsDisplay, ensureCacheDisplay, setCacheDisplay,
         remapSaveValues } from "./h3md_save_video_interp_ui.js";

// No built-in player any more (2026-10-23): the saved file is the node's 'preview' output -> H3MD Video Preview
// (h3md_video_preview.js), a separate node that can be moved / resized freely.

const first = (v) => (Array.isArray(v) ? v[0] : v);
const serialisable = (node) => (node.widgets || []).filter((w) => w && w.serialize !== false && w.options?.serialize !== false);

app.registerExtension({
  name: "H3MD.SaveVideoInterp",
  setup() {
    // pushed by the backend when the save starts / fails (cache folder known before the long write)
    api.addEventListener("h3md.save_video_interp", (ev) => {
      try {
        const d = ev.detail || {};
        const node = app.graph?.getNodeById?.(Number(d.node)) ?? app.graph?.getNodeById?.(d.node);
        if (node?.comfyClass === NODE || node?.type === NODE) {
          setCacheDisplay(node, d.cache_dir, d.state);
          node.setDirtyCanvas?.(true, true);
        }
      } catch (e) { console.error("[H3MD] save video interp event failed", e); }
    });
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const refresh = (node) => {
      try {
        syncInterpWidgets(node);
        ensureFpsDisplay(node);
        ensureCacheDisplay(node);
        node.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] save video interp UI failed", e); }
    };
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      const node = this;
      try {
        this.__h3mdDefaults = Object.fromEntries(serialisable(this).map((w) => [w.name, w.value]));
      } catch (e) { console.error("[H3MD] save video interp defaults failed", e); }
      for (const name of ["interpolate", "format", "save_output"]) {
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
    nodeType.prototype.onConfigure = function (info) {
      const r = onConfigure?.apply(this, arguments);
      try {
        const ws = serialisable(this);
        const vals = remapSaveValues(ws.map((w) => w.name), info?.widgets_values, this.__h3mdDefaults || {},
                                     info?.widgets_values_named);
        if (vals) {
          ws.forEach((w, i) => { if (vals[i] !== undefined) w.value = vals[i]; });
          console.info(`[H3MD] H3MDSaveVideoInterp #${this.id}: old workflow layout re-mapped by name ` +
                       "(source_fps, preset, cache_folder, folder_*, pingpong were removed)");
        }
      } catch (e) { console.error("[H3MD] save video interp remap failed", e); }
      refresh(this);
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      try {
        setCacheDisplay(this, first(message?.h3md_cache_dir), first(message?.h3md_cache_state));
        this.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] save video interp onExecuted failed", e); }
      return r;
    };
  },
});
