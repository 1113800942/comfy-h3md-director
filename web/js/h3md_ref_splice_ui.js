// H3MD 0.6.2 - "H3MD Ref Splice" (H3MDRefSplice, h3md_ref_splice.py): a small video editor on the node.
//  * player: the source video (from the connected Load Video node, before any run) or, after a run, the spliced
//    preview ("Source" / "Result");
//  * source track ("where in the source"): every move is a block over the source time - ONLY its left / right edge
//    can be dragged (longer / shorter), the player jumps to that frame; the block never moves (0.6.2 v3: moving it
//    there as well as on the edit track mixed the two up);
//  * edit track: the clips and transitions in their order - drag a block left / right to reorder; click one to edit
//    it below (times, words, duplicate, delete, add a transition after it);
//  * "Play edit": plays the source in the edit's order right away (no run), stopping for each transition;
//  * the 'edit' text box is the same edit as text (kept in step both ways; an AI can write it);
//  * after a run: the cast panel of the keyframe Prepare node (drag a line from a picture to a person) and the report.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { buildCast } from "./h3md_keyframe_ui.js";

const NODE = "H3MDRefSplice";
const FPS = 24;
const COLORS = ["#e8743b", "#19a979", "#ed4a7b", "#945ecf", "#13a4b4", "#525df4", "#bf399e", "#6c8893", "#ee6868", "#2f6497"];
const first = (v) => (Array.isArray(v) ? v[0] : v);
const wget = (node, name) => node?.widgets?.find((w) => w.name === name);
const snap = (t) => Math.round(t * FPS) / FPS;
const fmt = (t) => String(Math.round(t * 100) / 100);
const TIME = String.raw`(\d+(?::\d+(?:\.\d+)?)?(?:\.\d+)?)`;
const CLIP_RE = new RegExp(String.raw`^clip\s+${TIME}\s*(?:-|–|~|to)\s*${TIME}\s*s?\s*(?::\s*(.*))?$`, "i");
const GAP_RE = /^(?:gap|transition)\s+(\d+(?:\.\d+)?)\s*s?\s*(?::\s*(.*))?$/i;
const ptime = (s) => (s.includes(":") ? Number(s.split(":")[0]) * 60 + Number(s.split(":")[1]) : Number(s));

export function parseEdit(text) {
  const meta = [], items = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const l = raw.trim();
    if (!l) continue;
    let m = l.match(CLIP_RE);
    if (m) { items.push({ kind: "clip", a: ptime(m[1]), b: ptime(m[2]), text: (m[3] || "").trim() }); continue; }
    m = l.match(GAP_RE);
    if (m) { items.push({ kind: "gap", sec: Number(m[1]), text: (m[2] || "").trim() }); continue; }
    if (/^#\s*draft/i.test(l)) continue;
    meta.push(l);
  }
  return { meta, items };
}

export function editText(model) {
  const out = [...model.meta];
  for (const it of model.items) {
    if (it.kind === "clip") out.push(`clip ${fmt(it.a)}-${fmt(it.b)}` + (it.text ? `: ${it.text}` : ""));
    else out.push(`gap ${fmt(it.sec)}` + (it.text ? `: ${it.text}` : ""));
  }
  return out.join("\n") + (out.length ? "\n" : "");
}

// the clip number (colour) of each clip item: same source range = same colour
function clipKeys(items) {
  const keys = [], seen = [];
  for (const it of items) {
    if (it.kind !== "clip") { keys.push(null); continue; }
    let k = seen.findIndex((s) => Math.abs(s.a - it.a) < 1e-3 && Math.abs(s.b - it.b) < 1e-3);
    if (k < 0) { seen.push({ a: it.a, b: it.b }); k = seen.length - 1; }
    keys.push(k);
  }
  return keys;
}

function sourceUrl(node) {
  const inp = node.inputs?.find((i) => i.name === "video");
  if (inp?.link == null) return null;
  const g = app.graph;
  const link = g.getLink?.(inp.link) ?? g.links?.get?.(inp.link) ?? g.links?.[inp.link];
  const src = link && g.getNodeById(link.origin_id);
  const w = src?.widgets?.find((x) => typeof x.value === "string" && /\.(mp4|mov|webm|mkv|avi|m4v|gif)$/i.test(x.value));
  if (!w) return null;
  const v = w.value.replace(/\\/g, "/");
  const i = v.lastIndexOf("/");
  const sub = i >= 0 ? v.slice(0, i) : "", name = i >= 0 ? v.slice(i + 1) : v;
  return api.apiURL(`/view?filename=${encodeURIComponent(name)}&type=input&subfolder=${encodeURIComponent(sub)}`);
}

function el(tag, style = {}, text) {
  const e = document.createElement(tag);
  Object.assign(e.style, style);
  if (text != null) e.textContent = text;
  return e;
}

function button(label, onclick, title) {
  const b = el("button", { background: "#333", color: "#ddd", border: "1px solid #555", borderRadius: "4px",
    padding: "2px 8px", cursor: "pointer", fontSize: "12px" }, label);
  b.onclick = (e) => { e.stopPropagation(); onclick(); };
  if (title) b.title = title;
  return b;
}

function zoomOf(box) { return box.getBoundingClientRect().width / (box.offsetWidth || 1) || 1; }

function setupEditor(node) {
  if (node.__rs) return node.__rs;
  const editW = wget(node, "edit");
  const S = node.__rs = { model: parseEdit(editW?.value), sel: -1, dur: 0, mode: "source", resultUrl: null, srcUrl: null };
  const root = el("div", { display: "flex", flexDirection: "column", gap: "6px", fontFamily: "sans-serif", fontSize: "12px",
    color: "#ddd", background: "#161616", padding: "6px", borderRadius: "6px", height: "100%", boxSizing: "border-box" });
  const bar = el("div", { display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap" });
  const video = el("video", { width: "100%", height: "220px", background: "#000", objectFit: "contain", borderRadius: "4px" });
  video.controls = true; video.preload = "auto"; video.muted = false;
  const timeLab = el("span", { color: "#aaa", minWidth: "90px" }, "0.00 s");
  const help = el("div", { color: "#999", fontSize: "11px" },
    "Upper track: only the edges move (where a move starts / ends). Lower track: the order - drag, duplicate, add " +
    "transitions. A transition with no words = the model links the two poses (≈1 s); one move (kneel...) ≈1.5 s; " +
    "over 2 s = its own shot. Run = preview + plan package + the video output (connect Save Video to keep it). " +
    "上轨只能拖边缘（起止）；下轨管顺序、复制、过渡。过渡不写字 = 自然衔接（约 1 秒）；一个动作约 1.5 秒；超过 2 秒自动成单独镜头。" +
    "运行 = 预览 + 计划包 + 视频输出（接 Save Video 存成品）。");
  const trackA = el("div", { position: "relative", height: "48px", background: "#222", borderRadius: "4px", overflow: "hidden", cursor: "pointer" });
  const trackB = el("div", { position: "relative", height: "44px", background: "#222", borderRadius: "4px", overflowX: "auto",
    display: "flex", gap: "3px", padding: "3px", boxSizing: "border-box" });
  const insp = el("div", { display: "flex", gap: "6px", alignItems: "center", flexWrap: "wrap", minHeight: "28px" });
  const srcBtn = button("Source", () => { stopPlay(); show("source"); }, "the original video");
  const playBtn = button("▶ Play edit", () => (S.playing ? stopPlay() : playEdit()),
    "play the source in the edit's order now, without running (transitions: a pause) / 不运行，按剪辑顺序直接预览");
  const resBtn = button("Result", () => { stopPlay(); show("result"); }, "the spliced preview of the last run");
  bar.append(srcBtn, resBtn, timeLab,
    playBtn,
    button("+ Clip at playhead", () => addClip(), "a 2 s clip from the current frame, added at the end"),
    button("+ Transition after", () => addGap(), "a 1 s transition after the selected block"),
    button("Run", () => app.queuePrompt?.(0, 1), "preview + plan package (first run with no clips = a draft)"));
  const title = (s) => el("div", { color: "#bbb", fontSize: "11px", fontWeight: "bold" }, s);
  root.append(bar, video, title("原片位置 · where each move is in the source — drag a block's EDGES to make it longer / shorter"),
    trackA, title("播放顺序 · the order they play in — drag blocks to reorder, click one to change it"), trackB, insp, help);
  const w = node.addDOMWidget("h3md_splice_editor", "editor", root, { serialize: false, hideOnZoom: false,
    getMinHeight: () => 520, getMaxHeight: () => 520 });
  w.__h3mdSplice = true;

  function show(mode) {
    S.mode = mode;
    const url = mode === "result" ? S.resultUrl : (S.srcUrl = sourceUrl(node));
    if (url && video.dataset.url !== url) { video.dataset.url = url; video.src = url; }
    srcBtn.style.borderColor = mode === "source" ? "#ff8c00" : "#555";
    resBtn.style.borderColor = mode === "result" ? "#ff8c00" : "#555";
    resBtn.disabled = !S.resultUrl;
    render();
  }
  video.addEventListener("loadedmetadata", () => { if (S.mode === "source" && isFinite(video.duration)) { S.dur = video.duration; render(); } });
  video.addEventListener("timeupdate", () => { timeLab.textContent = `${video.currentTime.toFixed(2)} s`; drawHead(); });

  function write() {
    if (!editW) return;
    S.writing = true;
    editW.value = editText(S.model);
    editW.callback?.(editW.value);
    S.writing = false;
    node.setDirtyCanvas?.(true, true);
  }
  if (editW) {
    const cb = editW.callback;
    editW.callback = function (v) { const r = cb?.apply(this, arguments); if (!S.writing) { S.model = parseEdit(v); render(); } return r; };
  }

  function addClip() {
    const t = snap(video.currentTime || 0), d = S.dur || video.duration || 0;
    const b = d ? Math.min(d, t + 2) : t + 2;
    if (S.model.items.length) S.model.items.push({ kind: "gap", sec: 1, text: "" });
    S.model.items.push({ kind: "clip", a: t, b: snap(b), text: "" });
    S.sel = S.model.items.length - 1; write(); render();
  }
  function addGap() {
    const i = S.sel >= 0 ? S.sel : S.model.items.length - 1;
    if (i < 0) return;
    S.model.items.splice(i + 1, 0, { kind: "gap", sec: 1, text: "" });
    S.sel = i + 1; write(); render();
  }

  // play the edit straight from the source (no run): clips in order, a pause for each transition
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  function stopPlay() { S.playing = false; video.pause(); playBtn.textContent = "▶ Play edit"; }
  async function playEdit() {
    const items = S.model.items;
    if (!items.length) return;
    show("source");
    S.playing = true; playBtn.textContent = "■ Stop";
    const keys = clipKeys(items);
    for (let i = 0; i < items.length && S.playing; i++) {
      const it = items[i];
      S.sel = i; render();
      if (it.kind === "gap") { video.pause(); timeLab.textContent = `transition ${fmt(it.sec)} s`; await sleep(it.sec * 1000); continue; }
      video.currentTime = it.a;
      await new Promise((res) => { const f = () => { video.removeEventListener("seeked", f); res(); }; video.addEventListener("seeked", f); setTimeout(f, 400); });
      try { await video.play(); } catch { /* autoplay blocked */ }
      while (S.playing && video.currentTime < it.b - 0.02 && !video.paused) await sleep(15);
      video.pause();
      timeLab.textContent = `move ${keys[i] + 1}`;
    }
    stopPlay();
  }

  let head = null;
  function drawHead() {
    if (S.mode !== "source" || !S.dur) { if (head) head.style.display = "none"; return; }
    if (!head || head.parentNode !== trackA) { head = el("div", { position: "absolute", top: 0, bottom: 0, width: "2px", background: "#fff", pointerEvents: "none" }); trackA.append(head); }
    head.style.display = "block";
    head.style.left = `${(video.currentTime / S.dur) * 100}%`;
  }

  trackA.onpointerdown = (e) => {     // click on the empty track = seek
    if (e.target !== trackA || !S.dur) return;
    e.stopPropagation();
    const r = trackA.getBoundingClientRect();
    video.currentTime = Math.max(0, Math.min(S.dur, ((e.clientX - r.left) / r.width) * S.dur));
  };

  function render() {
    const items = S.model.items, keys = clipKeys(items), dur = S.dur;
    // source track
    trackA.innerHTML = ""; head = null;
    if (!dur) trackA.append(el("div", { color: "#888", padding: "14px 8px" }, "connect a Load Video node with the source video (then the source track appears)"));
    items.forEach((it, i) => {
      if (it.kind !== "clip" || !dur) return;
      const row = (i / 2) % 2 ? 1 : 0;
      const b = el("div", { position: "absolute", top: row ? "25px" : "3px", height: "20px", left: `${(it.a / dur) * 100}%`,
        width: `${Math.max(0.5, ((it.b - it.a) / dur) * 100)}%`, background: COLORS[keys[i] % COLORS.length], opacity: S.sel === i ? 1 : 0.8,
        border: S.sel === i ? "2px solid #fff" : "1px solid #000", boxSizing: "border-box", borderRadius: "3px", cursor: "pointer",
        fontSize: "11px", color: "#000", overflow: "hidden", whiteSpace: "nowrap", padding: "0 9px", touchAction: "none" },
        `Move ${keys[i] + 1} · ${fmt(it.a)}-${fmt(it.b)}s`);
      b.title = `move ${keys[i] + 1}: ${fmt(it.a)}-${fmt(it.b)} s of the source - drag the left / right edge`;
      b.onpointerdown = (e) => { e.stopPropagation(); S.sel = i; if (S.mode === "source") video.currentTime = it.a; render(); };
      for (const side of ["a", "b"]) {
        const h = el("div", { position: "absolute", top: 0, bottom: 0, width: "8px", [side === "a" ? "left" : "right"]: 0,
          background: "rgba(255,255,255,0.55)", cursor: "ew-resize", touchAction: "none" });
        h.title = side === "a" ? "drag: where this move starts" : "drag: where this move ends";
        h.onpointerdown = (e) => dragA(e, i, b, h, side);
        b.append(h);
      }
      trackA.append(b);
    });
    drawHead();
    // edit track: #k = the k-th clip in play order
    const order = []; let n = 0;
    items.forEach((it, i) => { if (it.kind === "clip") order[i] = ++n; });
    trackB.innerHTML = "";
    if (!items.length) trackB.append(el("div", { color: "#888", padding: "10px 6px" }, "empty: Run once for a draft, or + Clip at playhead"));
    items.forEach((it, i) => {
      const secs = it.kind === "clip" ? it.b - it.a : it.sec;
      const blk = el("div", { flex: "0 0 auto", width: `${Math.max(34, secs * 46)}px`, height: "100%", borderRadius: "3px",
        background: it.kind === "clip" ? COLORS[keys[i] % COLORS.length] : "repeating-linear-gradient(45deg,#444,#444 6px,#555 6px,#555 12px)",
        border: S.sel === i ? "2px solid #fff" : "1px solid #000", boxSizing: "border-box", color: it.kind === "clip" ? "#000" : "#ddd",
        fontSize: "11px", padding: "2px 4px", overflow: "hidden", whiteSpace: "nowrap", cursor: "grab", touchAction: "none" },
        it.kind === "clip" ? `#${order[i]} · Move ${keys[i] + 1} · ${fmt(secs)}s` : `↔ ${fmt(secs)}s${it.text ? " ✎" : ""}`);
      blk.title = it.kind === "clip" ? `clip ${fmt(it.a)}-${fmt(it.b)} s${it.text ? ": " + it.text : ""}` : `transition ${fmt(it.sec)} s${it.text ? ": " + it.text : " (no words = natural link)"}`;
      blk.onpointerdown = (e) => dragB(e, i, blk);
      trackB.append(blk);
    });
    inspector();
    resBtn.disabled = !S.resultUrl;
  }

  function dragA(e, i, b, h, mode) {
    e.preventDefault(); e.stopPropagation();
    const it = S.model.items[i], r = trackA.getBoundingClientRect();
    const a0 = it.a, b0 = it.b, x0 = e.clientX, dur = S.dur;
    S.sel = i;
    const same = S.model.items.filter((x) => x.kind === "clip" && Math.abs(x.a - a0) < 1e-3 && Math.abs(x.b - b0) < 1e-3);
    b = h;   // the handle captures the pointer; the block is its parent
    const blk = h.parentNode;
    try { b.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    const move = (ev) => {
      ev.stopPropagation();
      const dt = ((ev.clientX - x0) / r.width) * dur;
      const min = 5 / FPS;
      if (mode === "a") { const v = snap(Math.max(0, Math.min(b0 - min, a0 + dt))); for (const x of same) x.a = v; video.currentTime = v; }
      else { const v = snap(Math.min(dur, Math.max(a0 + min, b0 + dt))); for (const x of same) x.b = v; video.currentTime = Math.max(0, v - 1 / FPS); }
      blk.style.left = `${(it.a / dur) * 100}%`; blk.style.width = `${Math.max(0.5, ((it.b - it.a) / dur) * 100)}%`;
      timeLab.textContent = `${fmt(it.a)}-${fmt(it.b)} s`;
    };
    const up = (ev) => {
      ev.stopPropagation();
      b.removeEventListener("pointermove", move); b.removeEventListener("pointerup", up); b.removeEventListener("pointercancel", up);
      try { b.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
      write(); render();
    };
    b.addEventListener("pointermove", move); b.addEventListener("pointerup", up); b.addEventListener("pointercancel", up);
  }

  function dragB(e, i, blk) {
    e.preventDefault(); e.stopPropagation();
    const x0 = e.clientX; let moved = false;
    try { blk.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    const move = (ev) => { ev.stopPropagation(); if (Math.abs(ev.clientX - x0) > 6) { moved = true; blk.style.opacity = "0.5"; blk.style.transform = `translateX(${(ev.clientX - x0) / zoomOf(trackB)}px)`; } };
    const up = (ev) => {
      ev.stopPropagation();
      blk.removeEventListener("pointermove", move); blk.removeEventListener("pointerup", up); blk.removeEventListener("pointercancel", up);
      try { blk.releasePointerCapture(ev.pointerId); } catch { /* ignore */ }
      if (!moved) {
        S.sel = i;
        const it = S.model.items[i];
        if (it.kind === "clip" && S.mode === "source") video.currentTime = it.a;
        render(); return;
      }
      const kids = [...trackB.children].filter((c) => c !== blk);
      let to = kids.findIndex((c) => { const r = c.getBoundingClientRect(); return ev.clientX < r.left + r.width / 2; });
      if (to < 0) to = kids.length;
      const [it] = S.model.items.splice(i, 1);
      S.model.items.splice(to, 0, it);
      S.sel = to; write(); render();
    };
    blk.addEventListener("pointermove", move); blk.addEventListener("pointerup", up); blk.addEventListener("pointercancel", up);
  }

  function num(label, value, step, set) {
    const inp = el("input", { width: "64px", background: "#222", color: "#ddd", border: "1px solid #555" });
    inp.type = "number"; inp.step = step; inp.value = fmt(value);
    inp.onchange = () => { const v = Number(inp.value); if (isFinite(v)) { set(v); write(); render(); } };
    inp.onpointerdown = (e) => e.stopPropagation();
    return [el("span", {}, label), inp];
  }
  function txt(value, placeholder, set) {
    const inp = el("input", { flex: "1 1 240px", minWidth: "160px", background: "#222", color: "#ddd", border: "1px solid #555" });
    inp.value = value || ""; inp.placeholder = placeholder;
    inp.onchange = () => { set(inp.value.trim()); write(); render(); };
    inp.onpointerdown = (e) => e.stopPropagation();
    inp.onkeydown = (e) => e.stopPropagation();
    return inp;
  }
  function inspector() {
    insp.innerHTML = "";
    const i = S.sel, it = S.model.items[i];
    if (!it) { insp.append(el("span", { color: "#888" }, "click a block on the edit track to change it")); return; }
    const del = button("Delete", () => {
      S.model.items.splice(i, 1);
      // never two transitions in a row, never a transition at either end
      const its = S.model.items;
      for (let j = its.length - 1; j >= 0; j--) if (its[j].kind === "gap" && (j === 0 || j === its.length - 1 || its[j - 1].kind === "gap")) its.splice(j, 1);
      S.sel = -1; write(); render();
    });
    if (it.kind === "clip") {
      const d = S.dur || 1e9;
      insp.append(el("b", {}, `Clip`), ...num("from", it.a, 0.05, (v) => { it.a = snap(Math.max(0, Math.min(v, it.b - 5 / FPS))); }),
        ...num("to", it.b, 0.05, (v) => { it.b = snap(Math.min(d, Math.max(v, it.a + 5 / FPS))); }),
        txt(it.text, "what happens (optional, short)", (v) => { it.text = v; }),
        button("Duplicate", () => { S.model.items.splice(i + 1, 0, { kind: "gap", sec: 1, text: "" }, { ...it }); S.sel = i + 2; write(); render(); }, "use this move again (after a 1 s transition)"),
        button("+ Transition after", () => addGap()), del);
    } else {
      insp.append(el("b", {}, "Transition"), ...num("seconds", it.sec, 0.5, (v) => { it.sec = Math.max(1 / FPS, v); }),
        txt(it.text, "empty = natural link · or ONE move, e.g. he drops onto one knee, then stands up (about 1.5 s)", (v) => { it.text = v; }), del);
    }
  }

  node.__rsShow = show;
  node.__rsRender = () => { S.model = parseEdit(wget(node, "edit")?.value); render(); };
  setTimeout(() => show("source"), 0);
  return S;
}

function reportBox(node) {
  if (node.__h3mdReport) return node.__h3mdReport;
  if (!node.addDOMWidget) return null;
  const ta = document.createElement("textarea");
  ta.readOnly = true;
  ta.placeholder = "run the node: the shots and the package path appear here";
  Object.assign(ta.style, { width: "100%", height: "100%", minHeight: "70px", fontFamily: "monospace", fontSize: "11px",
    resize: "none", background: "var(--comfy-input-bg, #222)", color: "var(--input-text, #ddd)" });
  node.addDOMWidget("h3md_splice_report", "report", ta, { serialize: false, getValue: () => "", setValue: () => {},
    getMinHeight: () => 90, getMaxHeight: () => 120 });
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
      try {
        setupEditor(this); reportBox(this);
        const sz = this.computeSize?.();
        this.setSize?.([Math.max(760, sz?.[0] ?? 0), Math.max(this.size?.[1] ?? 0, sz?.[1] ?? 0)]);
      } catch (e) { console.error("[H3MD] ref splice UI", e); }
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      try { setTimeout(() => { this.__rsRender?.(); this.__rsShow?.("source"); }, 50); } catch (e) { console.error("[H3MD] ref splice UI", e); }
      return r;
    };
    const onConn = nodeType.prototype.onConnectionsChange;
    nodeType.prototype.onConnectionsChange = function () {
      const r = onConn?.apply(this, arguments);
      try { setTimeout(() => this.__rsShow?.("source"), 50); } catch { /* ignore */ }
      return r;
    };
    const onExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      const r = onExecuted?.apply(this, arguments);
      try {
        const t = first(message?.text);
        const box = reportBox(this);
        if (box && t) box.value = String(t);
        const sp = first(message?.h3md_splice);
        const info = sp ? (typeof sp === "string" ? JSON.parse(sp) : sp) : null;
        const S = setupEditor(this);
        if (info?.duration) S.dur = S.dur || info.duration;
        const editW = wget(this, "edit");
        if (info?.draft && editW && !parseEdit(editW.value).items.length) {
          editW.value = info.draft; editW.callback?.(editW.value);
          S.model = parseEdit(info.draft);
        }
        if (info?.preview) {
          const p = info.preview;
          S.resultUrl = api.apiURL(`/view?filename=${encodeURIComponent(p.filename)}&type=${p.type}&subfolder=${encodeURIComponent(p.subfolder || "")}&t=${Date.now()}`);
          this.__rsShow?.("result");
        } else this.__rsShow?.(S.mode);
        const c = first(message?.h3md_cast);
        if (c) { this.__h3mdCast = typeof c === "string" ? JSON.parse(c) : c; buildCast(this); }
      } catch (e) { console.error("[H3MD] ref splice UI", e); }
      return r;
    };
  },
});
