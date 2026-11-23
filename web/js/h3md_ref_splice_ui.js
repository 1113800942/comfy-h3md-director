// H3MD - UI for "H3MD Ref Splice" (H3MDRefSplice, h3md_ref_splice.py): shows the run's report (timeline, shots,
// plan path) in a read-only box on the node after it runs. Nothing is serialised.
import { app } from "../../scripts/app.js";

const NODE = "H3MDRefSplice";

function reportBox(node) {
  if (node.__h3mdReport) return node.__h3mdReport;
  if (!node.addDOMWidget) return null;
  const ta = document.createElement("textarea");
  ta.readOnly = true;
  ta.placeholder = "run the node: the timeline, the shots and the plan path appear here";
  Object.assign(ta.style, { width: "100%", height: "100%", minHeight: "90px", fontFamily: "monospace", fontSize: "11px",
                            resize: "none", background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)" });
  node.addDOMWidget("h3md_splice_report", "report", ta, { serialize: false, getValue: () => "", setValue: () => {} });
  node.__h3mdReport = ta;
  return ta;
}

app.registerExtension({
  name: "H3MD.RefSplice",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      try { reportBox(this); } catch (e) { console.error("[H3MD] ref splice UI", e); }
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      try {
        const t = Array.isArray(message?.text) ? message.text[0] : message?.text;
        const box = reportBox(this);
        if (box && t) box.value = String(t);
      } catch (e) { console.error("[H3MD] ref splice UI", e); }
      return r;
    };
  },
});
