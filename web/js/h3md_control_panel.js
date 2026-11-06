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
  const out = [], seen = new Set(), removed = new Set(P.removed || ["dasiwa", "turbo"]);
  for (const r of rows) {
    if (r.key && removed.has(r.key)) continue;   // 0.3.4: Dasiwa / 10ErosMax rows removed (speed LoRA = model-area 768)
    if (r.key && rec[r.key]) { out.push({ ...r, on: rec[r.key].on, strength: rec[r.key].strength }); seen.add(r.key); }
    else out.push({ ...r });
  }
  for (const [k, r] of Object.entries(rec)) if (!seen.has(k)) out.push({ ...r });
  return out;
}

// 0.3.4: the style buttons also set the HIGH-res 768 turbo (bypass) loader in the model subgraph
// (drama 0.5 / high motion 1.0 / motion copy 0.5, from /h3md/control_panel/presets).  That loader is found by its
// property h3md_role = "turbo768_high" (set in the example workflow), else by a title with "768" and 高清 / high.
function allGraphs() {
  const out = [], seen = new Set();
  const add = (g) => { if (g && !seen.has(g)) { seen.add(g); out.push(g); } };
  add(app.graph);
  for (const g of app.graph?.subgraphs?.values?.() || []) add(g);
  for (const g of app.graph?._subgraphs?.values?.() || []) add(g);
  return out;
}
export function turboHighLoaders() {
  const hits = [];
  for (const g of allGraphs()) for (const n of g._nodes || g.nodes || []) {
    if (n.type !== "H3MDLowVRAMLoraLoader" || n.mode === 4 || n.mode === 2) continue;
    const role = n.properties?.h3md_role, t = String(n.title || "");
    if (role === "turbo768_high" || (!role && /768/.test(t) && /高清|high/i.test(t))) hits.push(n);
  }
  return hits;
}
// 0.3.4: the "motion copy" style switches the simple workflow's third-party TARA nodes on (TARA LoRA + BFS TARA
// alignment, found by property h3md_role = tara_lora / tara_temporal) and bypasses "Sage low-res" (h3md_role sage_low,
// Sage stops TARA); every other style switches them back (TARA off, Sage low-res follows Sage high-res).  Without
// ComfyUI-BFSNodes or the TARA LoRA nothing is switched on (a missing node would stop the run) - a note says why.
const TARA_NODE = "BFSMiniMaxH3NativeTemporal";
const TARA_LORA = "minimax_h3_motion_transfer_tara_v1.0_ref2va.safetensors";
function roleNodes(role, titleRe) {
  const hits = [];
  for (const g of allGraphs()) for (const n of g._nodes || g.nodes || []) {
    const r = n.properties?.h3md_role;
    if (r === role || (!r && titleRe && titleRe.test(String(n.title || "")))) hits.push(n);
  }
  return hits;
}
function setMode(n, mode) { if (n.mode !== mode) { n.mode = mode; n.setDirtyCanvas?.(true, true); n.graph?.setDirtyCanvas?.(true, true); } }
export function taraSwitch(style, P) {
  const lora = roleNodes("tara_lora"), temporal = roleNodes("tara_temporal");
  const sageLow = roleNodes("sage_low", /Sage.*(低清|low)/i), sageHigh = roleNodes("sage_high", /Sage.*(高清|high)/i);
  if (!lora.length && !temporal.length) return "";   // workflow without the TARA nodes: nothing to do
  const LG = globalThis.LiteGraph || window.LiteGraph;
  const hasNode = !!LG?.registered_node_types?.[TARA_NODE];
  const hasLora = !(P?.loras?.length) || P.loras.includes(TARA_LORA);
  const want = style === "motion_copy";
  if (want && (!hasNode || !hasLora)) {
    for (const n of [...lora, ...temporal]) setMode(n, 4);
    return !hasNode ? "TARA 动作复刻需要第三方 ComfyUI-BFSNodes（Manager → 安装缺失节点），没装：TARA 节点保持关闭 · TARA needs ComfyUI-BFSNodes (not installed): TARA stays off"
      : "TARA LoRA 未下载（minimax_h3_motion_transfer_tara_v1.0_ref2va）：TARA 节点保持关闭 · TARA LoRA missing: TARA stays off";
  }
  for (const n of [...lora, ...temporal]) setMode(n, want ? 0 : 4);
  const highMode = sageHigh.length ? sageHigh[0].mode : 0;
  for (const n of sageLow) setMode(n, want ? 4 : highMode);
  return want ? "动作复刻：TARA 已打开，Sage 低清已旁路（原片当 <Video 1>、不勾 depth，提示词开头写 motion_transfer:） · TARA on, Sage low-res bypassed" : "";
}

function setTurboHigh(v) {
  if (typeof v !== "number") return;
  for (const n of turboHighLoaders()) {
    const w = n.widgets?.find((x) => x.name === "strength_model");
    if (w) { w.value = v; w.callback?.(v); n.setDirtyCanvas?.(true, true); }
  }
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
    const m = part.match(/^(\d+)(?:-(\d+))?$/);
    if (!m) return null;
    let lo = Number(m[1]), hi = Number(m[2] ?? m[1]);
    if (lo > hi) [lo, hi] = [hi, lo];
    if (lo < 1) return null;
    for (let k = lo; k <= hi; k++) out.add(k);
  }
  return [...out].sort((a, b) => a - b);
}

// segments the Loop will run = the Director's auto-split result (numbered as in the log), or null if unknown yet
function totalSegments() {
  for (const n of app.graph?._nodes || []) {
    const a = n.__h3md?.as;
    if (a && a.ok && Number.isFinite(Number(a.segments))) return Number(a.segments);
  }
  return null;
}

function hintText(text, total = totalSegments()) {
  const s = parseSegments(text);
  const all = total ? `共 ${total} 段 · ${total} segment(s)` : "";
  if (s === null) return { bad: true, t: "写法有误 / cannot read it — 例 e.g.  2   1-2   3, 6-7, 9" };
  if (total && s.length && s[s.length - 1] > total)
    return { bad: true, t: `超出范围：本片只有 ${total} 段（段号按导演台自动拆分后的顺序，和日志一致）· out of range: ${total} segment(s)` };
  if (!s.length) return { bad: false, t: `全部跑完：有缓存的段直接用${all ? "（" + all + "）" : ""} · Run all (cached segments are reused)` };
  return { bad: false, set: true, t: `⚠ 已设定：只跑第 ${s.join("、")} 段${all ? "（" + all + "）" : ""}，有缓存的也重新生成。要跑完整片请清空此框 · Set: only segment(s) ${s.join(", ")} (cached ones again too). Clear the box to run everything` };
}

// a wrong "which segments" box blocks the queue (instead of failing after the models are loaded)
export function panelProblems() {
  const out = [];
  for (const n of app.graph?._nodes || []) {
    if (n.type !== NODE || n.mode === 2 || n.mode === 4) continue;
    const h = hintText(W(n, "segments_to_run")?.value);
    if (h.bad) out.push(h.t);
  }
  return out;
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
.h3mdcp .hint{font-size:11px;color:#9ab;padding:0 4px;display:flex;align-items:center;gap:6px}.h3mdcp .hint.bad{color:#fff;background:#8a2a2a;border-radius:4px;padding:4px 6px}
.h3mdcp .hint.set{color:#1a1a1a;background:#f0a020;border-radius:4px;padding:4px 6px;font-weight:bold}
.h3mdcp .hint button{margin-left:auto;font-size:11px;padding:1px 6px;border:1px solid #333;border-radius:4px;background:#fff3;color:inherit;cursor:pointer}
.h3mdcp .styles{display:flex;gap:4px}.h3mdcp .styles button{flex:1;padding:5px 2px;border:1px solid #555;border-radius:5px;background:#2a2a2a;color:#ccc;cursor:pointer;font-size:11px}
.h3mdcp .styles button.on{background:#2f5d8a;border-color:#4a8bd0;color:#fff}
.h3mdcp .taranote{margin:4px 0 2px;padding:4px 6px;border-radius:5px;background:rgba(80,120,60,.25);border:1px solid #6a8f50;color:#dfe9d6;font-size:11px;line-height:1.4}
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
      serialize: false, hideOnZoom: false, getValue: () => undefined, setValue: () => {},
      getMinHeight: () => this.minH || 360, getMaxHeight: () => this.minH || 360,
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
    setTurboHigh(P.turbo_high?.[style]);
    this.taraNote = taraSwitch(style, P);
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

  // hint under the "which segments" box: grey = run all; orange banner + Clear = only some segments are set (kept
  // after the run on purpose, so the same segment can be generated again); red = wrong / out of range
  hintEl(h) {
    const cls = `hint${h.bad ? " bad" : h.set ? " set" : ""}`;
    const box = el("div", { class: cls }, el("span", {}, "↑ " + h.t));
    if (h.set || h.bad) box.append(el("button", { title: "clear the box: run everything (cached segments reused)",
      onclick: () => { const w = W(this.node, "segments_to_run"); if (w) { w.value = ""; w.callback?.(""); } this.render(); } }, "清空 Clear"));
    return box;
  }

  // cheap refresh of the hint line only (the Director's auto-split count arrives later / changes)
  updateHint() {
    const old = this.root.querySelector(".hint");
    if (!old) return;
    const h = hintText(W(this.node, "segments_to_run")?.value);
    if (old.firstChild?.textContent !== "↑ " + h.t) old.replaceWith(this.hintEl(h));
  }

  render() {
    injectCss();
    const h = hintText(W(this.node, "segments_to_run")?.value);
    const style = W(this.node, "style")?.value || "balanced";
    this.root.replaceChildren(
      this.hintEl(h),
      el("div", { class: "styles" }, ...["balanced", "high_motion", "motion_copy"].map((s) =>
        el("button", { class: s === style ? "on" : "", title: STYLE_TIP[s], onclick: () => this.setStyle(s) }, STYLE_LABEL[s]))),
      ...(this.taraNote ? [el("div", { class: "taranote" }, this.taraNote)] : []),
      this.table("low"), this.table("high"));
    this.fit();
  }

  // the node grows / shrinks with the LoRA tables (+ / ✕ rows), keeping its width
  fit() {
    requestAnimationFrame(() => {
      // content height (not scrollHeight: the root is stretched to the widget area, so it never shrinks)
      const last = this.root.lastElementChild, first = this.root.firstElementChild;
      const need = last && first ? Math.ceil(last.offsetTop + last.offsetHeight - first.offsetTop) + 14 : 360;
      if (!need || need === this.minH) return;
      this.minH = need;
      const size = this.node.computeSize?.();
      if (size && this.node.setSize) this.node.setSize([this.node.size[0], size[1]]);
      this.node.setDirtyCanvas?.(true, true);
    });
  }
}

app.registerExtension({
  name: "H3MD.ControlPanel",
  setup() {
    setInterval(() => { for (const n of app.graph?._nodes || []) if (n.type === NODE) n.__h3mdPanel?.updateHint(); }, 1000);
    const queue = app.queuePrompt?.bind(app);
    if (queue) {
      app.queuePrompt = async function (...args) {
        const bad = panelProblems();
        if (bad.length) {
          const msg = "H3MD Control Panel · 跑哪几段: " + bad[0];
          try { app.extensionManager?.toast?.add?.({ severity: "error", summary: "H3MD", detail: msg, life: 6000 }); }
          catch { /* older frontend */ }
          console.warn("[H3MD]", msg);
          return false;
        }
        return queue(...args);
      };
    }
    // (v0.3: the box is NOT cleared after a run - the same segment can be generated again; it shows an orange banner)
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      this.__h3mdPanel = new Panel(this);
      if (!this.size || this.size[0] < 360) this.setSize?.([400, this.size?.[1] || 600]);
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
