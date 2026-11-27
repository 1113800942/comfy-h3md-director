// H3MD - "H3MD Live Preview" window (H3MDLivePreviewWindow, h3md_live_preview.py).
// While an H3MD Loop samples, the backend decodes the model's current guess of the whole segment and sends it as a
// short looping clip (animated WebP, real-time speed) in the websocket event "h3md.live_preview"; every window node
// in the workflow (outer graph or any subgraph) plays the latest one. No wires, not part of the run. The clip fills
// the node (drag the corner to resize). Nothing is serialised.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "H3MDLivePreviewWindow";
const EVENT = "h3md.live_preview";
const VIEW = "h3md_live_view";
const TEXT_IDLE = "waiting for a run - the H3MD Loop's live preview plays here\n等待运行：H3MD Loop 的实时预览会在这里播放";

let last = null;   // latest { video, label } (a window added during a run shows it at once)

function ensureView(node) {
  if (node.__h3mdLive) return node.__h3mdLive;
  if (!node.addDOMWidget) return null;
  const box = document.createElement("div");
  Object.assign(box.style, { position: "relative", width: "100%", height: "100%", background: "#000",
                             display: "flex", alignItems: "center", justifyContent: "center", overflow: "hidden",
                             borderRadius: "4px", boxSizing: "border-box" });
  const img = document.createElement("img");
  Object.assign(img.style, { width: "100%", height: "100%", objectFit: "contain", display: "none" });
  const cap = document.createElement("div");
  Object.assign(cap.style, { position: "absolute", left: "6px", top: "4px", color: "#eee", font: "11px sans-serif",
                             textShadow: "0 0 3px #000", pointerEvents: "none", display: "none" });
  const empty = document.createElement("div");
  empty.textContent = TEXT_IDLE;
  Object.assign(empty.style, { color: "#888", font: "12px sans-serif", padding: "0 10px", textAlign: "center",
                               whiteSpace: "pre-line" });
  box.append(img, cap, empty);
  const w = node.addDOMWidget(VIEW, "h3md_live_view", box, { serialize: false, hideOnZoom: false,
                                                             getMinHeight: () => 80 });
  w.serialize = false;
  node.__h3mdLive = { img, cap, empty };
  if (last) show(node, last);
  return node.__h3mdLive;
}

function show(node, data) {
  const v = ensureView(node);
  if (!v) return;
  v.img.src = data.video;
  v.img.style.display = "block";
  v.cap.textContent = (data.label || "") + "  ·  preview, not final quality / 预览，非最终画质";
  v.cap.style.display = "block";
  v.empty.style.display = "none";
  node.setDirtyCanvas?.(true, true);
}

function graphs() {
  const out = new Set();
  const add = (g) => {
    if (!g || out.has(g)) return;
    out.add(g);
    for (const n of g._nodes ?? g.nodes ?? []) if (n?.subgraph) add(n.subgraph);
  };
  add(app.graph);
  for (const sg of app.graph?.subgraphs?.values?.() ?? []) add(sg);
  add(app.canvas?.graph);
  return out;
}

function windows() {
  const found = [];
  for (const g of graphs()) for (const n of g._nodes ?? g.nodes ?? []) if (n?.type === NODE || n?.comfyClass === NODE) found.push(n);
  return found;
}

app.registerExtension({
  name: "H3MD.LivePreview",
  setup() {
    api.addEventListener(EVENT, ({ detail }) => {
      if (!detail?.video) return;
      last = { video: detail.video, label: detail.label };
      for (const n of windows()) show(n, last);
    });
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      ensureView(this);
      if (!this.__h3mdSized) { this.setSize?.([420, 600]); this.__h3mdSized = true; }
      return r;
    };
  },
});
