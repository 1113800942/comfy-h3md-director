// H3MD Sage Attention node (0.6.6): shows on the node whether SageAttention is used on this install and, when it is
// not, a button with the install steps for this machine.  The node itself never errors without it.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "H3MDSageAttention";
let cache = null;
export async function sageStatus() {
  if (cache) return cache;
  try { cache = await (await api.fetchApi("/h3md/speed/sage_status")).json(); }
  catch (e) { console.warn("[H3MD] sage status", e); cache = { active: null }; }
  return cache;
}
export function sageHelp(s) {
  const text = [s.status_zh, s.how_zh, s.status_en, s.how_en].filter(Boolean).join("\n");
  console.info("[H3MD] SageAttention:\n" + text);
  try { app.extensionManager?.toast?.add?.({ severity: s.active ? "success" : "info", summary: "H3MD · SageAttention", detail: text, life: 60000 }); }
  catch { /* older frontend: the text is in the browser console */ }
}

app.registerExtension({
  name: "H3MD.SpeedPatches",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      sageStatus().then((s) => {
        this.__h3mdSage = s;
        if (s && s.active === false && !this.widgets?.some((w) => w.name === "h3md_sage_help")) {
          const w = this.addWidget("button", "怎么装 · How to install", null, () => sageHelp(s));
          w.name = "h3md_sage_help";
          w.serialize = false;
        }
        const size = this.computeSize?.();
        if (size && this.setSize) this.setSize([Math.max(this.size?.[0] || 0, 300), size[1] + 20]);
        this.setDirtyCanvas?.(true, true);
      });
      return r;
    };
    const draw = nodeType.prototype.onDrawForeground;
    nodeType.prototype.onDrawForeground = function (ctx) {
      draw?.apply(this, arguments);
      const s = this.__h3mdSage;
      if (!s || s.active == null || this.flags?.collapsed) return;
      ctx.save();
      ctx.font = "11px sans-serif";
      ctx.fillStyle = s.active ? "#7fc99a" : "#d9b36a";
      const text = s.active ? "✓ " + s.status_zh : "⚠ " + s.status_zh;
      ctx.fillText(text, 10, this.size[1] - 8, this.size[0] - 20);
      ctx.restore();
    };
  },
});
