// H3MD - UI for "H3MD Video Preview" (H3MDVideoPreview, h3md_video_preview.py).
// Plays the file behind a 'preview' output (H3MD Save Video Interp / Save Video Face Refine And Interp / Seam Face
// Refine) in a DOM widget that FILLS the node: only a minimum height, no fixed / maximum height, so dragging the
// node's corner resizes the player; the clip is letterboxed (object-fit: contain). Native HTML5 <video controls>
// (no autoplay); gif / webp / png-sequence show an <img> (first frame for a sequence). Nothing is serialised.
import { app } from "../../scripts/app.js";
import { previewSource } from "./h3md_save_video_interp_ui.js";

export const PREVIEW_NODE = "H3MDVideoPreview";
export const VIEW = "h3md_video_view";
export const MIN_HEIGHT = 80;
export const DEFAULT_SIZE = [480, 360];
export const TEXT_IDLE = "connect a 'preview' output and run";
export const TEXT_NONE = "nothing to preview (mode off / overlap 0, or file outside ComfyUI's output folder)";

export function ensureView(node) {
  if (node.__h3mdView) return node.__h3mdView;
  if (!node.addDOMWidget) return null;
  const box = document.createElement("div");
  Object.assign(box.style, { width: "100%", height: "100%", background: "#000", display: "flex",
                             alignItems: "center", justifyContent: "center", overflow: "hidden", borderRadius: "4px",
                             boxSizing: "border-box" });
  const video = document.createElement("video");
  video.controls = true; video.preload = "metadata"; video.playsInline = true; video.loop = false;
  Object.assign(video.style, { width: "100%", height: "100%", objectFit: "contain", display: "none", background: "#000" });
  const img = document.createElement("img");
  Object.assign(img.style, { width: "100%", height: "100%", objectFit: "contain", display: "none" });
  const empty = document.createElement("div");
  empty.textContent = TEXT_IDLE;
  Object.assign(empty.style, { color: "#777", font: "12px sans-serif", padding: "0 8px", textAlign: "center" });
  box.append(video, img, empty);
  for (const el of [box, video, img]) {   // keep wheel / pointer on the player (no canvas zoom/drag while seeking)
    el.addEventListener("pointerdown", (e) => e.stopPropagation());
    el.addEventListener("wheel", (e) => e.stopPropagation(), { passive: true });
  }
  // only getMinHeight: the frontend gives the widget all remaining node height (follows the node size)
  const w = node.addDOMWidget(VIEW, "h3md_video_view", box, {
    serialize: false, hideOnZoom: false, getMinHeight: () => MIN_HEIGHT,
  });
  w.serialize = false;
  node.__h3mdView = { w, box, video, img, empty };
  return node.__h3mdView;
}

export function showPreview(node, entry) {
  const p = ensureView(node);
  if (!p) return;
  const src = previewSource(entry, Date.now());
  p.video.pause?.();
  p.video.style.display = src.kind === "video" ? "block" : "none";
  p.img.style.display = src.kind === "image" ? "block" : "none";
  p.empty.style.display = src.kind === "none" ? "block" : "none";
  if (src.kind === "video") { p.img.removeAttribute("src"); p.video.src = src.url; p.video.load?.(); }
  else if (src.kind === "image") { p.video.removeAttribute("src"); p.video.load?.(); p.img.src = src.url; }
  else {
    p.video.removeAttribute("src"); p.video.load?.(); p.img.removeAttribute("src");
    p.empty.textContent = TEXT_NONE;
  }
  node.__h3mdPreview = entry ?? null;
}

const first = (v) => (Array.isArray(v) ? v[0] : v);

app.registerExtension({
  name: "H3MD.VideoPreview",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== PREVIEW_NODE) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      try {
        ensureView(this);
        // a fresh node starts at a usable size (a loaded workflow restores its own size afterwards)
        if (this.size && (this.size[0] < DEFAULT_SIZE[0] || this.size[1] < DEFAULT_SIZE[1]))
          this.setSize?.([Math.max(this.size[0], DEFAULT_SIZE[0]), Math.max(this.size[1], DEFAULT_SIZE[1])]);
      } catch (e) { console.error("[H3MD] video preview UI failed", e); }
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      try {
        if (message && "h3md_video" in message) showPreview(this, first(message.h3md_video));
        this.setDirtyCanvas?.(true, true);
      } catch (e) { console.error("[H3MD] video preview onExecuted failed", e); }
      return r;
    };
  },
});
