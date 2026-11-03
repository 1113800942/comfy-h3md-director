// H3MD Control Panel (v0.3): style buttons, low/high LoRA tables, "which segments" hint + auto-clear.
// The LoRA lists live in two hidden STRING widgets (lora_low / lora_high, JSON); the style in a hidden combo.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "H3MDControlPanel";
const STYLE_LABEL = { balanced: "均衡 Balanced", high_motion: "高动态 High motion", motion_copy: "动作复刻 Motion copy" };
const STYLE_TIP = {
  balanced: "drama, walking, running, dancing (default) · 文戏、跑步、跳舞",
  high_motion: "fights, gunfights, charges · 打斗、枪战",
  motion_copy: "dance / motion transfer from a reference video · 动作复刻",
};
let presetCache = null;

async function presets() {
  if (presetCache) return presetCache;
  try { presetCache = await (await api.fetchApi("/h3md/control_panel/presets")).json(); }
  catch (e) { console.error("[H3MD] control panel presets", e); presetCache = { presets: {}, loras: [], installed: {} }; }
  return presetCache;
}

const W = (node, name) => node.widgets?.find((w) => w.name === name);
const rowsOf = (node, which) => { try { const r = JSON.parse(W(node, `lora_${which}`)?.value || "[]"); return Array.isArray(r) ? r : []; } catch { return []; } };
const setRows = (node, which, rows) => { const w = W(node, `lora_${which}`); if (w) { w.value = JSON.stringify(rows); w.callback?.(w.value); } node.setDirtyCanvas?.(true, true); };

// same rule as h3md_control_panel.apply_style: recommended rows follow the style, the user's own rows stay
function applyStyle(rows, style, which, P) {
  const rec = {};
  for (const r of P.presets?.[style]?.[which] || []) rec[r.key] = r;
  const out = [], seen = new Set();
  for (const r of rows) {
    if (r.key && rec[r.key]) { out.push({ ...r, on: rec[r.key].on, strength: rec[r.key].strength }); seen.add(r.key); }
    else out.push({ ...r });
  }
  for (const [k, r] of Object.entries(rec)) if (!seen.has(k)) out.push({ ...r });
  return out;
}

// "3, 6-7, 9" -> [3,6,7,9] (1-based) or null when the writing is wrong; "" -> []
export function parseSegments(text) {
  let raw = String(text || "").trim();
  if (!raw) return [];
  for (const [a, b] of [["，", ","], ["、", ","], ["；", ","], [";", ","], ["～", "-"], ["~", "-"], ["–", "-"], ["—", "-"], ["到", "-"]]) raw = raw.split(a).join(b);
  const out = new Set();
  for (let part of raw.replace(/\s+/g, ",").split(",")) {
    part = part.trim();
    if (!part) continue;
    const m = /^(\d+)(?:-(\d+))?$/.exec(part);
    if (!m) return null;
    let lo = Number(m[1]), hi = Number(m[2] ?? m[1]);
    if (lo > hi) [lo, hi] = [hi, lo];
    if (lo < 1) return null;
    for (let k = lo; k <= hi; k++) out.add(k);
  }
  return [...out].sort((a, b) => a - b);
}

function hintText(text) {
  const s = parseSegments(text);
  if (s === null) return { bad: true, t: "写法有误 / cannot read it — 例 e.g.  2   1-2   3, 6-7, 9" };
  if (!s.length) return { bad: false, t: "全部跑完：有缓存的段直接用 · Run all (cached segments are reused)" };
  return { bad: false, t: `这次只生成第 ${s.join("、")} 段（有缓存的也重新生成），跑完自动清空 · Only segment(s) ${s.join(", ")} this time; cleared after the run` };
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "style") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k in e && typeof v !== "string") e[k] = v;
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return e;
}

const CSS = `
.h3mdcp{font:12px sans-serif;color:#ddd;display:flex;flex-direction:column;gap:6px;padding:2px 2px 6px}
.h3mdcp .hint{font-size:11px;color:#9ab;padding:0 4px}.h3mdcp .hint.bad{color:#f77}
.h3mdcp .styles{display:flex;gap:4px}.h3mdcp .styles button{flex:1;padding:5px 2px;border:1px solid #555;border-radius:5px;background:#2a2a2a;color:#ccc;cursor:pointer;font-size:11px}
.h3mdcp .styles button.on{background:#2f5d8a;border-color:#4a8bd0;color:#fff}
.h3mdcp .box{border:1px solid #444;border-radius:6px;padding:4px}.h3mdcp .head{display:flex;align-items:center;gap:6px;margin-bottom:3px}
.h3mdcp .head b{flex:1}.h3mdcp .head button,.h3mdcp .add{font-size:11px;padding:2px 6px;border:1px solid #555;border-radius:4px;background:#2a2a2a;color:#ccc;cursor:pointer}
.h3mdcp .row{display:flex;align-items:center;gap:4px;margin:2px 0}.h3mdcp .row select{flex:1;min-width:0;background:#1e1e1e;color:#ddd;border:1px solid #444;border-radius:3px;font-size:11px}
.h3mdcp .row input[type=number]{width:52px;background:#1e1e1e;color:#ddd;border:1px solid #444;border-radius:3px;font-size:11px}
.h3mdcp .row.off select,.h3mdcp .row.off input[type=number]{opacity:.45}.h3mdcp .row .x{cursor:pointer;color:#999;padding:0 3px}
.h3mdcp .row.missing select{border-color:#c44;color:#f99}.h3mdcp .rec{font-size:10px;color:#7a9;width:10px}`;
function injectCss() { if (document.getElementById("h3mdcp-css")) return; document.head.append(el("style", { id: "h3mdcp-css" }, CSS)); }

class Panel {
  constructor(node) {
    this.node = node;
    this.root = el("div", { class: "h3mdcp" });
    for (const ev of ["pointerdown", "wheel"]) this.root.addEventListener(ev, (e) => e.stopPropagation(), { passive: true });
    this.widget = node.addDOMWidget("h3md_panel_ui", "h3md_panel_ui", this.root, {
      serialize: false, hideOnZoom: false, getValue: () => undefined, setValue: () => {}, getMinHeight: () => 360,
    });
    this.widget.serialize = false;
    for (const n of ["style", "lora_low", "lora_high"]) { const w = W(node, n); if (w) w.hidden = true; }
    const seg = W(node, "segments_to_run");
    if (seg) { const cb = seg.callback; seg.callback = (...a) => { const r = cb?.apply(seg, a); this.render(); return r; }; }
    const interp = W(node, "interpolate");
    if (interp) { const cb = interp.callback; interp.callback = (...a) => { const r = cb?.apply(interp, a); this.syncFps(); return r; }; }
    this.syncFps();
    presets().then(() => this.render());
    this.render();
  }

  syncFps() {
    const on = !!W(this.node, "interpolate")?.value, fps = W(this.node, "fps");
    if (fps) { fps.disabled = !on; if (!on) fps.value = 24; }
    this.node.setDirtyCanvas?.(true, true);
  }

  setStyle(style) {
    const P = presetCache || {};
    const sw = W(this.node, "style");
    if (sw) { sw.value = style; sw.callback?.(style); }
    for (const which of ["low", "high"]) setRows(this.node, which, applyStyle(rowsOf(this.node, which), style, which, P));
    this.render();
  }

  table(which) {
    const P = presetCache || { loras: [] };
    const rows = rowsOf(this.node, which);
    const installed = new Set(P.loras || []);
    const style = W(this.node, "style")?.value || "balanced";
    const box = el("div", { class: "box" },
      el("div", { class: "head" }, el("b", {}, which === "low" ? "低清 LoRA · Low-res pass" : "高清 LoRA · High-res pass"),
        el("button", { title: "back to the recommended values of the selected style (your own rows stay)",
          onclick: () => { setRows(this.node, which, applyStyle(rows, style, which, P)); this.render(); } }, "恢复推荐 Reset")));
    rows.forEach((r, i) => {
      const missing = P.loras?.length && !installed.has(r.lora);
      const upd = (patch) => { const rs = rowsOf(this.node, which); rs[i] = { ...rs[i], ...patch }; setRows(this.node, which, rs); this.render(); };
      const sel = el("select", { title: r.lora + (missing ? "\n(not found in models/loras)" : ""), onchange: (e) => upd({ lora: e.target.value, key: null }) });
      const names = [...new Set([r.lora, ...(P.loras || [])])];
      for (const n of names) sel.append(el("option", { value: n }, n.replace(/\.safetensors$/i, "")));
      sel.value = r.lora;
      box.append(el("div", { class: `row${r.on ? "" : " off"}${missing ? " missing" : ""}` },
        el("span", { class: "rec", title: r.key ? "recommended (follows the style buttons)" : "your own LoRA" }, r.key ? "★" : ""),
        el("input", { type: "checkbox", checked: !!r.on, onchange: (e) => upd({ on: e.target.checked }) }),
        sel,
        el("input", { type: "number", step: "0.05", value: String(r.strength), onchange: (e) => upd({ strength: Number(e.target.value) || 0 }) }),
        el("span", { class: "x", title: "remove this row", onclick: () => { const rs = rowsOf(this.node, which); rs.splice(i, 1); setRows(this.node, which, rs); this.render(); } }, "✕")));
    });
    box.append(el("button", { class: "add", onclick: () => {
      const rs = rowsOf(this.node, which);
      const first = (P.loras || []).find((n) => !rs.some((r) => r.lora === n)) || (P.loras || [])[0] || "";
      if (!first) return;
      rs.push({ key: null, lora: first, on: true, strength: 1.0 });
      setRows(this.node, which, rs); this.render();
    } }, "＋ 添加 LoRA · Add LoRA"));
    return box;
  }

  render() {
    injectCss();
    const h = hintText(W(this.node, "segments_to_run")?.value);
    const style = W(this.node, "style")?.value || "balanced";
    this.root.replaceChildren(
      el("div", { class: `hint${h.bad ? " bad" : ""}` }, "↑ " + h.t),
      el("div", { class: "styles" }, ...["balanced", "high_motion", "motion_copy"].map((s) =>
        el("button", { class: s === style ? "on" : "", title: STYLE_TIP[s], onclick: () => this.setStyle(s) }, STYLE_LABEL[s]))),
      this.table("low"), this.table("high"));
  }
}

app.registerExtension({
  name: "H3MD.ControlPanel",
  setup() {
    // "which segments" is cleared after a FINISHED run of a prompt that contained the panel
    const pending = new Map();   // prompt_id -> [node ids]
    api.addEventListener("executed", (ev) => {
      const d = ev.detail || {};
      if (!d.output?.h3md_panel) return;
      const ids = pending.get(d.prompt_id) || [];
      ids.push(d.node);
      pending.set(d.prompt_id, ids);
    });
    api.addEventListener("execution_success", (ev) => {
      const id = ev.detail?.prompt_id;
      for (const nid of pending.get(id) || []) {
        const node = app.graph?.getNodeById?.(Number(nid)) ?? app.graph?.getNodeById?.(nid);
        const w = node && W(node, "segments_to_run");
        if (w && w.value) { w.value = ""; node.__h3mdPanel?.render(); node.setDirtyCanvas?.(true, true); }
      }
      pending.delete(id);
    });
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      this.__h3mdPanel = new Panel(this);
      if (!this.size || this.size[0] < 360) this.setSize?.([380, Math.max(this.size?.[1] || 0, 720)]);
      return r;
    };
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = configure?.apply(this, arguments);
      queueMicrotask(() => { this.__h3mdPanel?.syncFps(); this.__h3mdPanel?.render(); });
      return r;
    };
  },
});
