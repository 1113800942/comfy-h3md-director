// H3MD 0.5.91 (user 22:47): multi-person swap keyframes by clicks.
//  * H3MD Keyframe Import: an "Import package" button - pick the AI's .h3kf / .json, it is uploaded to
//    input/H3MD_keyframes/imports/ and the 'package' box is set (no copy and paste).
//  * H3MD Keyframe Redo: one button per keyframe (#1 ... #N, N from the last Export / Import / Recast run), chosen =
//    made again, the others are kept; "All" clears the choice; "Run" queues the workflow.
// The 'redo' / 'count' widgets stay (hidden), so widgets_values keep their places.
//  * H3MD Keyframe Prepare (02:18-02:23): after a run, a cast panel - the pictures on top, every person of the cast
//    below.  Drag a line from a picture to a person = that picture replaces him (or click a person: the next free
//    picture); click a line or a picked person to free it.  "Automatic" clears the choice (picture 1 = first to
//    appear ...); "Run" queues the workflow - the tracking is reused, so it takes seconds.  The choice is written
//    into 'mapping' as 图1换4号 ...
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const IMPORT = "H3MDKeyframeImport";
const PREPARE = "H3MDKeyframePrepare";
const REDO = "H3MDKeyframeRedo";
const COUNTING = ["H3MDKeyframeExport", "H3MDKeyframeImport", "H3MDKeyframeRecast"];
const first = (v) => (Array.isArray(v) ? v[0] : v);
const wget = (node, name) => node?.widgets?.find((w) => w.name === name);

export function parseRedo(text) {
  return [...new Set(String(text || "").match(/\d+/g)?.map(Number) || [])].filter((k) => k > 0).sort((a, b) => a - b);
}

async function uploadPackage(file) {
  const body = new FormData();
  body.append("image", file, file.name);
  body.append("type", "input");
  body.append("subfolder", "H3MD_keyframes/imports");
  const r = await api.fetchApi("/upload/image", { method: "POST", body });
  if (r.status !== 200) throw new Error(`upload failed: ${r.status} ${r.statusText}`);
  const j = await r.json();
  return j.subfolder ? `${j.subfolder}/${j.name}` : j.name;
}

function setupImport(node) {
  if (node.__h3mdImport) return;
  node.__h3mdImport = true;
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".h3kf,.json,.zip";
  input.style.display = "none";
  document.body.append(input);
  input.onchange = async () => {
    const f = input.files && input.files[0];
    input.value = "";
    if (!f) return;
    try {
      const name = await uploadPackage(f);
      const w = wget(node, "package");
      if (w) { w.value = name; w.callback?.(name); }
      node.setDirtyCanvas?.(true, true);
    } catch (e) { alertLater(`H3MD: ${e.message || e}`); }
  };
  node.addWidget("button", "Import package (.h3kf / .json)", null, () => input.click(), { serialize: false });
  const onRemoved = node.onRemoved;
  node.onRemoved = function () { input.remove(); return onRemoved?.apply(this, arguments); };
}

function alertLater(msg) { console.error(msg); try { app.extensionManager?.toast?.add?.({ severity: "error", summary: msg, life: 6000 }); } catch { /* ignore */ } }

function buildRedo(node) {
  const redoW = wget(node, "redo");
  const countW = wget(node, "count");
  if (!redoW || !countW) return;
  redoW.hidden = true;
  countW.hidden = true;
  node.widgets = node.widgets.filter((w) => !w.__h3mdRedo);
  const n = Math.max(0, Number(countW.value) || 0);
  const sel = new Set(parseRedo(redoW.value).filter((k) => k <= n));
  const add = (label, cb) => { const w = node.addWidget("button", label, null, cb, { serialize: false }); w.__h3mdRedo = true; return w; };
  const set = (s) => { redoW.value = [...s].sort((a, b) => a - b).join(","); buildRedo(node); };
  if (!n) add("Run Export or Import once: the keyframe buttons appear", () => {});
  for (let k = 1; k <= n; k++) {
    add(sel.has(k) ? `#${k}  REDO` : `#${k}  keep`, () => { const s = new Set(sel); s.has(k) ? s.delete(k) : s.add(k); set(s); });
  }
  if (n) {
    add(sel.size ? `Make all again (clear ${sel.size} chosen)` : "None chosen = make all", () => set(new Set()));
    add(sel.size ? `Run: redo ${[...sel].sort((a, b) => a - b).join(", ")}` : "Run: make all", () => app.queuePrompt?.(0, 1));
  }
  const sz = node.computeSize?.();
  if (sz) node.setSize?.([Math.max(node.size?.[0] ?? 0, sz[0]), sz[1]]);
  node.setDirtyCanvas?.(true, true);
}

export function parseCastMapping(text) {
  const out = {};
  for (const m of String(text || "").matchAll(/(?:图|圖|picture|pic|image|img)\s*(\d+)[^\d\n,，;；。]*?(\d+)/gi)) {
    out[Number(m[1])] = Number(m[2]);
  }
  return out;
}

export function castMappingText(map) {
  return Object.keys(map).map(Number).sort((a, b) => a - b).map((p) => `图${p}换${map[p]}号`).join("\n");
}

function buildCast(node) {
  const cast = node.__h3mdCast;
  const mapW = wget(node, "mapping");
  if (!cast || !mapW) return;
  const pics = (cast.pictures || []).slice().sort((a, b) => a - b);
  const people = cast.people || [];
  let map = parseCastMapping(mapW.value);            // picture -> person
  const auto = !Object.keys(map).length;
  if (auto) for (const q of people) if (q.picture) map[q.picture] = q.person;
  const write = (m) => { mapW.value = castMappingText(m); mapW.callback?.(mapW.value); buildCast(node); };

  // one DOM panel: pictures on top, the cast below, a line from every picture to the person it replaces
  let w = node.widgets?.find((x) => x.__h3mdCastPanel);
  if (!w) {
    const el = document.createElement("div");
    w = node.addDOMWidget("cast_panel", "h3md_cast", el, { serialize: false, hideOnZoom: false,
      getMinHeight: () => 470, getMaxHeight: () => 470 });
    w.__h3mdCastPanel = true;
    w.__el = el;
  }
  const el = w.__el;
  el.innerHTML = "";
  Object.assign(el.style, { position: "relative", height: "470px", background: "#141414", borderRadius: "6px",
    overflow: "hidden", userSelect: "none", fontFamily: "sans-serif", color: "#ddd", fontSize: "12px" });
  const head = document.createElement("div");
  head.textContent = (auto ? "Automatic: picture 1 = first to appear. " : "Your cast. ")
    + "Drag a line from a picture to a person (or click a person: next free picture). Click a line or a picked person to free it.";
  Object.assign(head.style, { padding: "4px 8px", color: "#aaa" });
  el.append(head);
  const row = (top) => { const r = document.createElement("div");
    Object.assign(r.style, { position: "absolute", left: "8px", right: "8px", top: top + "px", display: "flex", gap: "8px",
      overflowX: "auto" }); el.append(r); return r; };
  const picRow = row(44), castRow = row(250);
  const card = (src, label, sub, col) => {
    const c = document.createElement("div");
    Object.assign(c.style, { flex: "0 0 auto", border: `3px solid ${col}`, borderRadius: "4px", background: "#222",
      cursor: "pointer", textAlign: "center" });
    const im = document.createElement("img");
    im.src = src || ""; im.draggable = false;
    Object.assign(im.style, { height: "130px", display: "block", margin: "0 auto" });
    const t = document.createElement("div"); t.textContent = label; t.style.padding = "2px 4px";
    const s = document.createElement("div"); s.textContent = sub; s.style.color = col; s.style.padding = "0 4px 3px";
    c.append(im, t, s);
    return c;
  };
  const picCard = {}, personCard = {};
  const personOf = {}; for (const [p, k] of Object.entries(map)) personOf[k] = Number(p);
  for (const p of pics) {
    const c = card(cast.thumbs?.[p], `picture ${p}`, map[p] ? `-> #${map[p]}` : "not used", map[p] ? "#ff8c00" : "#888");
    c.title = cast.desc?.[p] || "";
    picRow.append(c); picCard[p] = c;
  }
  people.forEach((q, i) => {
    const p = personOf[q.person];
    const c = card(cast.tiles?.[i], `#${q.person}  ${q.first}-${q.last} s`, p ? `<- picture ${p}` : "kept", p ? "#ff8c00" : "#50c8ff");
    castRow.append(c); personCard[q.person] = c;
    c.onclick = () => {
      const m = auto ? {} : { ...map };
      if (!auto && p) { delete m[p]; return write(m); }
      const used = new Set(Object.keys(m).map(Number));
      const free = pics.find((x) => !used.has(x));
      if (free === undefined) { alertLater(`H3MD: all ${pics.length} pictures are used - free one first`); return; }
      m[free] = q.person; write(m);
    };
  });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  Object.assign(svg.style, { position: "absolute", left: 0, top: 0, width: "100%", height: "100%", pointerEvents: "none" });
  el.append(svg);
  const centre = (c, bottom) => { const a = c.getBoundingClientRect(), b = el.getBoundingClientRect();
    const k = b.width / el.offsetWidth || 1;                           // canvas zoom
    return [(a.left + a.width / 2 - b.left) / k, ((bottom ? a.bottom : a.top) - b.top) / k]; };
  const line = (x1, y1, x2, y2, col, click) => {
    const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
    Object.assign(l, {}); l.setAttribute("x1", x1); l.setAttribute("y1", y1); l.setAttribute("x2", x2); l.setAttribute("y2", y2);
    l.setAttribute("stroke", col); l.setAttribute("stroke-width", "5"); l.setAttribute("stroke-linecap", "round");
    if (click) { l.style.pointerEvents = "stroke"; l.style.cursor = "pointer"; l.onclick = click; }
    svg.append(l); return l;
  };
  const drawLines = () => {
    svg.innerHTML = "";
    for (const [p, k] of Object.entries(map)) {
      if (!picCard[p] || !personCard[k]) continue;
      const [x1, y1] = centre(picCard[p], true), [x2, y2] = centre(personCard[k], false);
      line(x1, y1, x2, y2, "#ff8c00", () => { const m = auto ? { ...map } : { ...map }; delete m[p]; write(m); });
    }
  };
  requestAnimationFrame(drawLines);
  picRow.onscroll = castRow.onscroll = drawLines;
  // drag a line from a picture
  // 02:49: the canvas took the pointer away from window listeners (the line never landed) - the picture card
  // captures the pointer itself, so move / up always come back to it
  for (const p of pics) {
    const pc = picCard[p];
    pc.style.touchAction = "none";
    pc.onpointerdown = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      try { pc.setPointerCapture(ev.pointerId); } catch { /* ignore */ }
      const [x1, y1] = centre(pc, true);
      const k = el.getBoundingClientRect().width / el.offsetWidth || 1;
      const tmp = line(x1, y1, x1, y1, "#ffd27f");
      const move = (e) => { e.stopPropagation(); const b = el.getBoundingClientRect();
        tmp.setAttribute("x2", (e.clientX - b.left) / k); tmp.setAttribute("y2", (e.clientY - b.top) / k); };
      const up = (e) => {
        e.stopPropagation();
        pc.removeEventListener("pointermove", move); pc.removeEventListener("pointerup", up);
        pc.removeEventListener("pointercancel", up);
        try { pc.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
        tmp.remove();
        const hit = Object.entries(personCard).find(([, c]) => { const a = c.getBoundingClientRect();
          return e.clientX >= a.left && e.clientX <= a.right && e.clientY >= a.top && e.clientY <= a.bottom; });
        if (!hit) return;
        const m = { ...map };
        for (const [pp, kk] of Object.entries(m)) if (kk === Number(hit[0])) delete m[pp];   // one picture per person
        m[p] = Number(hit[0]); write(m);
      };
      pc.addEventListener("pointermove", move); pc.addEventListener("pointerup", up);
      pc.addEventListener("pointercancel", up);
    };
  }
  // buttons under the panel
  node.widgets = node.widgets.filter((x) => !x.__h3mdCastBtn);
  const add = (label, cb) => { const b = node.addWidget("button", label, null, cb, { serialize: false }); b.__h3mdCastBtn = true; };
  add("Automatic again (picture 1 = first to appear ...)", () => { mapW.value = ""; mapW.callback?.(""); buildCast(node); });
  add("Run with this cast (tracking is reused: seconds)", () => app.queuePrompt?.(0, 1));
  const sz = node.computeSize?.();
  if (sz) node.setSize?.([Math.max(node.size?.[0] ?? 0, sz[0], 640), Math.max(node.size?.[1] ?? 0, sz[1])]);
  node.setDirtyCanvas?.(true, true);
}

function setCount(n) {
  for (const node of app.graph?._nodes ?? []) {
    if ((node.comfyClass || node.type) !== REDO) continue;
    const w = wget(node, "count");
    if (w && Number(w.value) !== n) { w.value = n; buildRedo(node); }
  }
}

app.registerExtension({
  name: "H3MD.KeyframeUI",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const id = nodeData?.name;
    if (id === IMPORT || id === REDO) {
      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = onCreated?.apply(this, arguments);
        try { id === IMPORT ? setupImport(this) : buildRedo(this); } catch (e) { console.error("[H3MD] keyframe UI", e); }
        return r;
      };
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function () {
        const r = onConfigure?.apply(this, arguments);
        try { if (id === REDO) buildRedo(this); } catch (e) { console.error("[H3MD] keyframe UI", e); }
        return r;
      };
    }
    if (id === PREPARE) {
      const onExecuted = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        try {
          const c = first(message?.h3md_cast);
          if (c) { this.__h3mdCast = typeof c === "string" ? JSON.parse(c) : c; buildCast(this); }
        } catch (e) { console.error("[H3MD] cast UI", e); }
        return r;
      };
    }
    if (COUNTING.includes(id)) {
      const onExecuted = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message) {
        const r = onExecuted?.apply(this, arguments);
        try { const n = Number(first(message?.h3md_kf_count)); if (n > 0) setCount(n); } catch (e) { console.error("[H3MD] keyframe UI", e); }
        return r;
      };
    }
  },
});
