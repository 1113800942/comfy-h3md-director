// H3MD - plan editor for the "H3MD Director + RefMod" node (H3MDDirectorRefMod, h3md_director_refmod.py).
// FORK of h3md_director_ui.js (md5 6DC2FDDF..., 2026-10-20) - that file and the H3MD Director node are untouched.
// Added: media kind "refmods" (saved RefMod .safetensors; data kept separate, shown inside the images / videos / audios
// library boxes of its kind, no own library box; in a shot card they have their own RefMod row: drag to reorder, or
// onto a picture / video to place it at that position = `at`), per-shot RefMod refs,
// <RefMod j> prompt labels (the backend rewrites them to the core label, e.g. <Video 2>).
// The node's plan_json STRING widget stays the single source of truth (it is what the workflow
// saves and what the backend parses). This editor only reads/writes that JSON.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import * as M from "./h3md_plan.js";
import { PERF_CSS, videoThumb, attachHoverPlay, installMoveWatch, watchNode, unwatchNode } from "./h3md_director_perf.js";
import { hideWidgets } from "./h3md_hide_widgets.js";

const NODE_CLASS = "H3MDDirectorRefMod";
// v0.3 simple Director: rarely used buttons are hidden (code kept). Settings → H3MD → "Show advanced Director buttons".
const ADVANCED_SETTING = "H3MD.Director.AdvancedButtons";
const advanced = () => { try { return !!app.extensionManager?.setting?.get?.(ADVANCED_SETTING); } catch { return false; } };
const LOOP_CLASS = "H3MDLoopSamplerRefMod";
// UI-state keys of removed features (raw plan_json view, Segment Prompt Auto Split): dropped on load so old
// workflows that saved them still open in the normal editor
const OBSOLETE_PROPS = ["h3md_ui_mode", "h3md_autosplit"];
// static writing note (no skill loading / no parsing behind it)
const PROMPT_NOTE = "提示词按标准 H3 六段式写：subject_definitions、summary、retention_analysis、detailed_description、overall_soundscape、non_diegetic_music。时间从本段 0 秒起算。subject_definitions 里只写描述。<Picture/Video/Audio N> 编号按素材在本分镜的顺序自动生成（先图片，再视频，视频音轨排在它前面，最后音频）；把素材拖到分镜只会挂上素材，标签要点提示词下方的标签按钮或输入 @ 插入。RefMod：拖进分镜后显示在分镜卡自己的 RefMod 行里，左右拖动调整顺序（同时改 <RefMod j> 编号）；提示词里写 <RefMod 1>（或「参考 RefMod 1」），运行时自动换成它实际的 <Picture k>/<Video k>/<Audio k> 编号。把图片/视频类 RefMod 拖到某张图片/某个视频上，它就排在那个位置（编号和顺序跟着位置走，后面的普通图片/视频编号顺延）；没设位置时排在本分镜自己的图片/视频之后（例如已有 1 个视频时 RefMod 1 = <Video 2>）；音频 RefMod 仍排在音频之后。";
const PROP_SIZES = "h3md_ui_sizes"; // { "bin-images": px, "prompt": px, ... } user-dragged heights (UI only, saved with the workflow)
const KIND_LETTER = { audios: "A", videos: "V", images: "P", refmods: "R" };
const KIND_LABEL = { images: "图片 Images", videos: "视频 Videos", audios: "音频 Audios", refmods: "RefMod 参考模型" };
const ACCEPT = { images: "image/*,.safetensors", videos: "video/*,.mkv,.safetensors", audios: "audio/*", refmods: ".safetensors" };
const BIN_ORDER = ["audios", "videos", "images"]; // library boxes, top to bottom (RefMods are listed inside these)
// reference-video resolution (plan.refVideoHeight, read by h3md_director.py; absent = native = old behaviour)
const REF_VIDEO_HEIGHTS = ["native", "480", "576", "720", "auto"];
const REF_VIDEO_HEIGHT_LABEL = { native: "native 原始", 480: "480", 576: "576", 720: "720", auto: "auto 自动" };
const BIN_ROWS = { audios: 1.5, videos: 1.5, images: 2.5, refmods: 1.5 };  // visible rows per box (rest scrolls)
const TILE_H = 74, TILE_GAP = 4, GRID_PAD = 4;              // px; 9 tiles per row (CSS grid)
const TOKEN_RE = /<(Picture|Video|Audio|RefMod) (\d+)>/g;
const TAG_KIND = { Picture: "image", Video: "video", Audio: "audio", RefMod: "refmod" };
const ALL_KINDS = [...M.KINDS, "refmods"];
const RM_LABEL = { image: "Picture", video: "Video", audio: "Audio" };
const escHTML = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const probeCache = new Map(); // file -> Promise<probe>
const fileListCache = {}; // kind -> Promise<string[]>

// ------------------------------------------------------------------ small DOM helper
function h(tag, attrs, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = !!v;
    else e.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
  return el;
}
const fmtS = (s) => (s == null || !Number.isFinite(Number(s)) ? "?" : Number(s).toFixed(2) + "s");
const fmtF = (f) => `${f}f (${(f / M.FPS).toFixed(2)}s)`;

function splitFile(file) {
  const f = String(file || "").replace(/\\/g, "/");
  const i = f.lastIndexOf("/");
  return i < 0 ? { subfolder: "", filename: f } : { subfolder: f.slice(0, i), filename: f.slice(i + 1) };
}
function tailName(file, max = 13) {
  const n = splitFile(file).filename;
  return n.length > max ? "…" + n.slice(n.length - (max - 1)) : n;
}
function isAbs(file) { return /^([A-Za-z]:[\\/]|\/)/.test(String(file || "")); }
function viewURL(file) {
  if (!file || isAbs(file)) return null;
  const { subfolder, filename } = splitFile(file);
  return api.apiURL(`/view?filename=${encodeURIComponent(filename)}&subfolder=${encodeURIComponent(subfolder)}&type=input`);
}

// ------------------------------------------------------------------ server helpers
function probe(file) {
  if (!probeCache.has(file)) {
    probeCache.set(file, api.fetchApi(`/h3md/probe?file=${encodeURIComponent(file)}`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return probeCache.get(file);
}
async function listInputFiles(kind) {
  if (!fileListCache[kind]) {
    fileListCache[kind] = (async () => {
      try {
        const r = await api.fetchApi(`/h3md/files?kind=${M.KIND_SINGULAR[kind]}`);
        if (r.ok) return (await r.json()).files || [];
      } catch { /* fall back below */ }
      // fallback: the core loader nodes' file lists
      const node = { images: ["LoadImage", "image"], videos: ["LoadVideo", "file"], audios: ["LoadAudio", "audio"] }[kind];
      try {
        const r = await api.fetchApi(`/object_info/${node[0]}`);
        const j = await r.json();
        const spec = j[node[0]].input.required[node[1]];
        return (Array.isArray(spec[0]) ? spec[0] : (spec[1] && spec[1].options) || []).map(String);
      } catch { return []; }
    })();
  }
  return fileListCache[kind];
}
async function uploadFile(file) {
  const body = new FormData();
  body.append("image", file, file.name);
  body.append("type", "input");
  body.append("subfolder", "");
  const r = await api.fetchApi("/upload/image", { method: "POST", body });
  if (r.status !== 200) throw new Error(`upload failed: ${r.status} ${r.statusText}`);
  const j = await r.json();
  return j.subfolder ? `${j.subfolder}/${j.name}` : j.name;
}

// ------------------------------------------------------------------ RefMod helpers (plan: media.refmods, segment.refmods)
const rmInfoCache = new Map(); // file -> Promise<info>
let rmListCache = null;
function refmodInfo(file) {
  if (!rmInfoCache.has(file)) {
    rmInfoCache.set(file, api.fetchApi(`/h3md/refmod_info?file=${encodeURIComponent(file)}`)
      .then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return rmInfoCache.get(file);
}
async function listRefMods() {
  if (!rmListCache) {
    rmListCache = (async () => {
      try { const r = await api.fetchApi("/h3md/refmods"); if (r.ok) return (await r.json()).refmods || []; } catch { /* ignore */ }
      return [];
    })();
  }
  return rmListCache;
}
async function uploadRefMod(file) {
  const body = new FormData();
  body.append("image", file, file.name);
  body.append("type", "input");
  body.append("subfolder", "refmods");
  const r = await api.fetchApi("/upload/image", { method: "POST", body });
  if (r.status !== 200) throw new Error(`upload failed: ${r.status} ${r.statusText}`);
  const j = await r.json();
  return j.subfolder ? `${j.subfolder}/${j.name}` : j.name;
}
const rmEntry = (x) => (x && typeof x === "object" ? { ...x, id: String(x.id) } : { id: String(x) });
// receiver of a shot RefMod (= backend refmod_slot): "image" / "video" when dropped on the shot's images / videos box,
// otherwise the RefMod's own kind; audio RefMods always stay audio
const rmSlot = (e, kind) => (kind && kind !== "audio" && (e.slot === "image" || e.slot === "video") ? e.slot : kind);
const RM = {
  ensure(plan) { if (plan && plan.media && !Array.isArray(plan.media.refmods)) plan.media.refmods = []; return plan; },
  usage(plan, id) {
    const out = [];
    (plan.segments || []).forEach((s, i) => { if (s && (s.refmods || []).some((x) => rmEntry(x).id === id)) out.push(i); });
    return out;
  },
  toggle(plan, i, id, on, slot = null) {
    const s = plan.segments[i];
    s.refmods = (s.refmods || []).map(rmEntry);
    const has = s.refmods.some((x) => x.id === id);
    if (on && !has) s.refmods.push(slot ? { id, slot } : { id });
    if (on && has && slot) for (const x of s.refmods) if (x.id === id && x.slot !== slot) { x.slot = slot; delete x.at; }   // dropped on the other receiver: moves there (end of it)
    if (!on && has) s.refmods = s.refmods.filter((x) => x.id !== id);
    if (!s.refmods.length) delete s.refmods;
  },
  remove(plan, id) {
    plan.media.refmods = (plan.media.refmods || []).filter((x) => x.id !== id);
    for (const s of plan.segments || []) {
      if (!s || !Array.isArray(s.refmods)) continue;
      s.refmods = s.refmods.filter((x) => rmEntry(x).id !== id);
      if (!s.refmods.length) delete s.refmods;
    }
  },
  rename(plan, oldId, newId) {
    newId = String(newId || "").trim();
    if (!newId || newId === oldId) return false;
    if ((plan.media.refmods || []).some((x) => x.id === newId)) throw new Error(`id '${newId}' already exists`);
    const item = (plan.media.refmods || []).find((x) => x.id === oldId);
    if (!item) return false;
    item.id = newId;
    for (const s of plan.segments || []) if (s && Array.isArray(s.refmods)) s.refmods = s.refmods.map((x) => { const e = rmEntry(x); if (e.id === oldId) e.id = newId; return e; });
    return true;
  },
  // same numbering as the backend (h3md_director_refmod.parse_refmods): a picture / video RefMod with `at` = p
  // (0..own count-1) runs right before the shot's own picture / video p+1 (later own ones are renumbered); without
  // `at` (or at >= own count) after the shot's own refs; audio RefMods after the shot's audios.
  // -> { rm: RefMod labels, ownNum: { image: [new number of own picture p], video: [...] } }
  layout(plan, s, infos) {
    const own = M.computeLabels(s, M.mediaMaps(plan));
    const nOwn = { image: own.filter((l) => l.kind === "image").length, video: own.filter((l) => l.kind === "video").length };
    let aud = own.filter((l) => l.kind === "audio" || l.kind === "video_soundtrack").length;
    const items = plan.media.refmods || [];
    const rm = (s.refmods || []).map(rmEntry).map((e, k) => {
      const it = items.find((x) => x.id === e.id);
      const info = it ? infos[it.file] : null;
      const kind = (info && info.kind) || (it && it.kind) || null;
      const slot = rmSlot(e, kind);
      const a = Number(e.at);
      const at = (slot === "image" || slot === "video") && e.at != null && Number.isInteger(a) && a >= 0 && a < nOwn[slot] ? a : null;
      return { tag: `<RefMod ${k + 1}>`, kind: "refmod", id: e.id, file: it ? it.file : null, missing: !it || (info && info.exists === false),
               core: null, rmKind: kind, slot, at, tokens: info ? info.tokens : (it && it.tokens),
               note: "" };
    });
    const ownNum = { image: [], video: [] };
    for (const slot of ["image", "video"]) {
      let num = 0;
      for (let p = 0; p < nOwn[slot]; p++) {
        for (const l of rm) if (l.slot === slot && l.at === p) l.core = `<${RM_LABEL[slot]} ${++num}>`;
        ownNum[slot].push(++num);
      }
      for (const l of rm) if (l.slot === slot && l.at === null) l.core = `<${RM_LABEL[slot]} ${++num}>`;
    }
    for (const l of rm) if (l.slot === "audio") l.core = `<Audio ${++aud}>`;
    for (const l of rm) l.note = l.core ? `→ ${l.core}` : "";
    return { rm, ownNum };
  },
  labels(plan, s, infos) { return RM.layout(plan, s, infos).rm; },
  // the shot's own labels with their pictures / videos renumbered around positioned RefMods (unchanged otherwise)
  retagOwn(labels, ownNum) {
    const c = { image: 0, video: 0 };
    return labels.map((l) => {
      if (!(l.kind in c)) return l;
      const k = ownNum[l.kind][c[l.kind]++];
      const tag = k ? `<${RM_LABEL[l.kind]} ${k}>` : l.tag;
      return tag === l.tag ? l : { ...l, tag };
    });
  },
};
const SHOTREF = "application/x-h3md-shotref";   // drag-to-reorder inside a shot card (not a library drop)

// 2026-10-27: time-coded prompt (later shots with a time, e.g. "[Shot 2] At 00:08.000" / "At 8.5s")
const TIMECODED = /\[Shot\s*[2-9]\d*\][^\n]*\bAt\b|\bAt\s+\d{1,2}:\d{2}|\bAt\s+\d+(?:\.\d+)?\s*s(?:ec(?:onds?)?)?\b/i;
const AS_TC_WARN = "⚠ 自动拆段后每一段都从 0 秒套用整段提示词：没有参考视频的分镜，后面几段会重演开头（画面跳回、声音重来），按时间写的镜头也会错位。建议点“拆分分镜…”按上面的切点手动拆开、各写各的提示词，或把分镜缩短";
// H3MD autosplit: shot-local times (s) where each auto-split part's new content starts (part 1 starts at 0)
function cutTimes(x) {
  const out = []; let f = 0;
  x.parts.forEach((p, j) => { if (j) out.push((f / 24).toFixed(2) + "s"); f += j ? p - x.overlap : p; });
  return out.join("、");
}
// ------------------------------------------------------------------ styles
function injectRefModStyles() {
  if (document.getElementById("h3md-refmod-css")) return;
  document.head.append(h("style", { id: "h3md-refmod-css" }, `
.h3md-label.refmod{background:#6a2f6f}
.h3md-pf-back mark.refmod{background:rgba(150,60,160,.6);box-shadow:0 0 0 1px #c070d0}
.h3md-tile.rmt .tk{background:#6a2f6f;color:#f3d9f8}
.h3md-tile .tv.rm{display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(160deg,#3e1f44,#1f1022);color:#e6c8ee;font-size:10px;line-height:1.25;text-align:center;padding:12px 2px}
.h3md-tile .tv.rm .big{font-size:13px;font-weight:600}.h3md-tile .tv.rm .hot{color:#ff8080}
.h3md-tile .tv.rm .sl,.h3md-rmicon .sl{font-size:9px;opacity:.9;white-space:nowrap}.h3md-tile .tv.rm .big.ref{font-size:15px;font-weight:700}
.h3md-rmicon{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;min-width:64px;padding:2px 6px;border-radius:4px;background:linear-gradient(160deg,#3e1f44,#1f1022);color:#e6c8ee;line-height:1.15;text-align:center;vertical-align:middle}
.h3md-rmicon .big{font-size:13px;font-weight:700}.h3md-rmicon .fn{font-size:9px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.h3md-rmref input.n{width:58px}
.h3md-rmrow{display:flex;flex-wrap:wrap;gap:0 6px;align-items:center}
.h3md-sdrag{cursor:grab}.h3md-sdrag.dragging{opacity:.4}.h3md-seg>h4[draggable=true]{cursor:grab}
.h3md-chip.ins-b,.h3md-rmref.ins-b{box-shadow:inset 3px 0 0 #6fb0ff}.h3md-chip.ins-a,.h3md-rmref.ins-a{box-shadow:inset -3px 0 0 #6fb0ff}
.h3md-ref.v.ins-b{box-shadow:inset 0 3px 0 #6fb0ff}.h3md-ref.v.ins-a{box-shadow:inset 0 -3px 0 #6fb0ff}
.h3md-stgt-end.drop{outline:1px dashed #c070d0;outline-offset:-1px}
.h3md-tab .as{display:block;font-size:9px;line-height:1.1}
.h3md-as-ok{color:#7cc77c}.h3md-as-split{color:#e8b84a}.h3md-as-over{color:#ff7070}
.h3md-as-line{margin:2px 0 4px 0;white-space:normal}
.h3md-as-tc{color:#ffb35c;font-weight:600}
.h3md-tabs{height:46px}.h3md-tab{height:43px}`));
}
function injectStyles() {
  if (document.getElementById("h3md-editor-css")) return;
  const css = `
.h3md-root{box-sizing:border-box;width:100%;height:100%;overflow:auto;font:12px/1.35 system-ui,sans-serif;color:var(--fg-color,#ddd);
  background:#2b2c2e;border:1px solid var(--border-color,#444);border-radius:6px;padding:6px;}
.h3md-root *{box-sizing:border-box}
.h3md-root input,.h3md-root select,.h3md-root textarea{background:var(--comfy-input-bg,#111);color:var(--input-text,#ddd);border:1px solid var(--border-color,#555);border-radius:4px;font:inherit;padding:2px 4px}
.h3md-root textarea{width:100%;resize:vertical;font-family:ui-monospace,Consolas,monospace;font-size:11px}
.h3md-root button{background:var(--comfy-input-bg,#333);color:var(--fg-color,#ddd);border:1px solid var(--border-color,#555);border-radius:4px;padding:1px 6px;cursor:pointer;font:inherit}
.h3md-root button:hover{filter:brightness(1.3)}
.h3md-root button.on{background:#2d5a8a;border-color:#4a8ad0}
.h3md-bar{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px}
.h3md-bar .sp{flex:1}
.h3md-title{font-weight:600}
.h3md-sec{border:1px solid rgba(150,178,215,.22);border-radius:5px;padding:5px;margin-bottom:6px;background:rgba(150,178,215,.06)}
.h3md-sec>h4{margin:0 0 4px 0;font-size:12px;display:flex;gap:6px;align-items:center}
.h3md-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:2px 0}
.h3md-bins{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.h3md-bin{border:1px dashed var(--border-color,#555);border-radius:5px;padding:4px;min-height:60px}
.h3md-bin.drop{border-color:#4a8ad0;background:rgba(74,138,208,.12)}
.h3md-item{display:flex;gap:5px;align-items:center;border:1px solid var(--border-color,#444);border-radius:4px;padding:3px;margin:3px 0;cursor:grab;background:rgba(255,255,255,.03)}
.h3md-thumb{width:56px;height:42px;object-fit:cover;border-radius:3px;background:#000;flex:none}
.h3md-thumb.ph{display:flex;align-items:center;justify-content:center;font-size:18px;color:#888}
.h3md-meta{min-width:0;flex:1}
.h3md-meta .f{opacity:.7;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.h3md-meta input.id{width:100%;font-weight:600}
.h3md-bad{color:#ff7a7a}.h3md-warn{color:#f0c060}.h3md-info{color:#9ab}.h3md-ok{color:#7c7}
.h3md-tl{position:relative;height:30px;background:rgba(0,0,0,.35);border-radius:4px;overflow:hidden;margin:4px 0}
.h3md-tl .b{position:absolute;top:3px;bottom:3px;border-radius:3px;font-size:10px;color:#fff;padding:1px 3px;overflow:hidden;white-space:nowrap;cursor:pointer;opacity:.85}
.h3md-tl .ov{position:absolute;top:0;bottom:0;background:repeating-linear-gradient(45deg,rgba(255,255,255,.45) 0 3px,transparent 3px 6px)}
.h3md-seg{border:1px solid var(--border-color,#555);border-left:4px solid #4a8ad0;border-radius:5px;padding:5px;margin:6px 0}
.h3md-seg.drop{background:rgba(74,138,208,.12)}
.h3md-seg h4{margin:0 0 4px 0;display:flex;gap:6px;align-items:center;font-size:12px}
.h3md-tabs{display:flex;gap:3px;overflow-x:auto;white-space:nowrap;padding:2px 0 0 0;border-bottom:1px solid var(--border-color,#555);scrollbar-width:thin}
.h3md-tab{flex:0 0 auto;border:1px solid var(--border-color,#555);border-bottom:none;border-radius:5px 5px 0 0;padding:3px 7px;font-size:11px;cursor:pointer;background:rgba(255,255,255,.04);line-height:1.25}
.h3md-tab .tl{display:block;font-size:9px;opacity:.75}
.h3md-tab.sel{background:rgba(74,138,208,.30);border-color:#4a8ad0;font-weight:600}
.h3md-tab.err{box-shadow:inset 0 -2px 0 #d05050}
.h3md-tab.drop{background:rgba(74,138,208,.5)}
.h3md-tab.add{font-weight:700;padding:3px 9px}
.h3md-seg.tabbed{margin-top:0;border-top-left-radius:0}
.h3md-chip{display:inline-flex;gap:3px;align-items:center;border:1px solid var(--border-color,#555);border-radius:10px;padding:0 6px;margin:2px;font-size:11px;cursor:pointer;user-select:none}
.h3md-chip.sel{background:#2d5a8a;border-color:#4a8ad0}
.h3md-chip.idref{box-shadow:0 0 0 2px #e0b040}.h3md-idref{display:inline-flex;align-items:center;gap:1px;margin-left:3px;font-size:10px;color:#f0d080;cursor:pointer}.h3md-idref input{margin:0;width:11px;height:11px}
.h3md-label{display:inline-block;border-radius:3px;padding:0 4px;margin:2px;font-family:ui-monospace,Consolas,monospace;font-size:11px;cursor:pointer;border:1px solid transparent}
.h3md-label:hover{border-color:#fff}
.h3md-chipthumb{height:28px;width:auto;max-width:48px;object-fit:cover;vertical-align:middle;margin-right:4px;border-radius:2px}
.h3md-label.image{background:#3b5f2f}.h3md-label.video{background:#2d4f7a}.h3md-label.video_soundtrack,.h3md-label.audio{background:#6a4a1f}
.h3md-label.missing{background:#7a2d2d}
.h3md-ref{display:flex;gap:4px;align-items:center;flex-wrap:wrap;padding:2px 3px;margin:2px 0;border-radius:3px;background:rgba(255,255,255,.04)}
.h3md-ref input[type=number]{width:62px}
.h3md-msgs div{margin:1px 0}
.h3md-small{font-size:11px;opacity:.75}
.h3md-root input.n{width:70px}
.h3md-tl2{position:relative;height:96px;background:rgba(0,0,0,.35);border-radius:4px;margin:4px 0;user-select:none;touch-action:none;overflow:hidden}
.h3md-tl2 .tick{position:absolute;top:0;height:14px;border-left:1px solid rgba(255,255,255,.25);font-size:9px;padding-left:2px;opacity:.75;white-space:nowrap}
.h3md-tl2 .sb{position:absolute;height:26px;border-radius:4px;font-size:10px;color:#fff;padding:2px 4px;overflow:hidden;white-space:nowrap;cursor:grab;box-shadow:inset 0 0 0 1px rgba(255,255,255,.25)}
.h3md-tl2 .sb.first{cursor:default}
.h3md-tl2 .sb.sel{box-shadow:inset 0 0 0 2px #fff}
.h3md-tl2 .sb.drag{opacity:.8;cursor:grabbing}
.h3md-tl2 .rh{position:absolute;right:0;top:0;bottom:0;width:7px;cursor:ew-resize;background:rgba(255,255,255,.35)}
.h3md-tl2 .sm{position:absolute;top:0;bottom:0;width:0;border-left:1px dashed rgba(255,255,255,.85);pointer-events:none}
.h3md-tl2 .so{position:absolute;top:0;bottom:0;background:repeating-linear-gradient(-45deg,rgba(255,255,255,.28) 0 2px,transparent 2px 5px);pointer-events:none}
.h3md-tl2 .ov{position:absolute;top:16px;height:60px;background:repeating-linear-gradient(45deg,rgba(255,220,120,.55) 0 3px,rgba(0,0,0,.15) 3px 7px);border-left:1px solid #fc6;border-right:1px solid #fc6;pointer-events:none}
.h3md-tl2 .cut{position:absolute;top:16px;height:60px;width:0;border-left:2px solid #ff6a6a;pointer-events:none}
.h3md-tl2 .seam{position:absolute;bottom:2px;font-size:10px;transform:translateX(-50%);white-space:nowrap;padding:0 3px;border-radius:3px;pointer-events:none}
.h3md-tl2 .seam.c{background:#7a5a10;color:#fff}.h3md-tl2 .seam.x{background:#7a2d2d;color:#fff}
.h3md-dlg{border:1px solid #4a8ad0;border-radius:5px;padding:5px;margin:4px 0;background:rgba(74,138,208,.08)}
.h3md-lib{display:flex;flex-direction:column;gap:6px}
.h3md-box{border:1px solid var(--border-color,#444);border-radius:5px;background:rgba(0,0,0,.18)}
.h3md-box.drop{border-color:#4a8ad0;background:rgba(74,138,208,.12)}
.h3md-boxhead{display:flex;gap:5px;align-items:center;padding:3px 5px;border-bottom:1px solid rgba(255,255,255,.08)}
.h3md-boxhead input{min-width:80px;flex:0 1 220px}
.h3md-cnt{display:inline-block;min-width:18px;text-align:center;border-radius:8px;padding:0 5px;font-size:10px;background:rgba(255,255,255,.12)}
.h3md-box.images .h3md-cnt{background:#3b5f2f}.h3md-box.videos .h3md-cnt{background:#2d4f7a}.h3md-box.audios .h3md-cnt{background:#6a4a1f}
.h3md-grid{display:grid;grid-template-columns:repeat(9,minmax(0,1fr));grid-auto-rows:${TILE_H}px;gap:${TILE_GAP}px;padding:${GRID_PAD}px;overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;align-content:start}
.h3md-empty{grid-column:1/-1;display:flex;align-items:center;justify-content:center;opacity:.5;font-size:11px;border:1px dashed var(--border-color,#555);border-radius:4px}
.h3md-tile{position:relative;display:flex;flex-direction:column;border:1px solid var(--border-color,#444);border-radius:4px;background:rgba(255,255,255,.04);overflow:hidden;cursor:grab;user-select:none;min-width:0}
.h3md-tile:hover{border-color:#8ab4e0}
.h3md-tile.cur{box-shadow:inset 0 0 0 1px #4a8ad0}
.h3md-tile.missing{border-color:#d05050}
.h3md-tile.dragging{opacity:.35}
.h3md-tile.ins-b{box-shadow:inset 3px 0 0 #6fb0ff}.h3md-tile.ins-a{box-shadow:inset -3px 0 0 #6fb0ff}
.h3md-tile .tv{flex:1;min-height:0;width:100%;object-fit:cover;background:#000;display:block;pointer-events:none}
.h3md-tile .tv.ph{display:flex;align-items:center;justify-content:center;color:#777;font-size:18px}
.h3md-tile .tv.aud{display:flex;align-items:center;justify-content:center;background:linear-gradient(160deg,#4a3418,#2a1e0f);padding:13px 3px 14px}
.h3md-tile .tv.aud .note{display:none}
.h3md-tile .tv.aud .fn{font-size:9px;line-height:1.2;color:#f3dcb5;text-align:center;word-break:break-all;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;opacity:.85}
.h3md-tile .nm{flex:none;height:15px;line-height:15px;font-size:10px;padding:0 3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-align:center}
.h3md-tile .bdg{position:absolute;font-size:9px;line-height:12px;padding:0 3px;border-radius:3px;background:rgba(0,0,0,.72);color:#fff;pointer-events:none;max-width:calc(100% - 4px);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.h3md-tile .bdg.use{top:2px;left:2px;background:rgba(45,90,138,.92)}
.h3md-tile .bdg.dur{bottom:17px;right:2px}
.h3md-tile .bdg.snd{bottom:17px;left:2px;pointer-events:auto;cursor:pointer;color:#f0c070}
.h3md-tile .bdg.snd.off{color:#888;text-decoration:line-through}
.h3md-tile .play{position:absolute;bottom:17px;left:2px;padding:0 4px;font-size:9px;line-height:12px}
.h3md-tile .acts{position:absolute;top:2px;right:2px;display:none;gap:2px}
.h3md-tile:hover .acts{display:flex}
.h3md-tile .acts button{padding:0 3px;font-size:10px;line-height:13px;background:rgba(0,0,0,.75)}
.h3md-tile:hover .bdg.use{display:none}
.h3md-ph{font-size:11px;opacity:.5;font-style:italic;border:1px dashed var(--border-color,#555);border-radius:4px;padding:1px 8px}
.h3md-tab[draggable=true]{cursor:grab}
.h3md-tab.ins-b{box-shadow:inset 3px 0 0 #6fb0ff}.h3md-tab.ins-a{box-shadow:inset -3px 0 0 #6fb0ff}
.h3md-tab.dragging{opacity:.4}
.h3md-label.ro{cursor:text;user-select:text}.h3md-label.ro:hover{border-color:transparent}
.h3md-root .h3md-seg,.h3md-root .h3md-dlg{background:rgba(150,178,215,.06);border-color:rgba(150,178,215,.22)}
.h3md-root .h3md-seg{border-left:4px solid #4a8ad0}
.h3md-box{border-color:rgba(150,178,215,.22)!important;background:rgba(150,178,215,.06)!important}
.h3md-box.drop{border-color:#4a8ad0!important;background:rgba(74,138,208,.16)!important}
.h3md-grid{position:relative;resize:vertical;min-height:${TILE_H + 2 * GRID_PAD}px}
.h3md-tile .tk{flex:none;height:13px;line-height:13px;font-size:9px;font-weight:700;padding:0 3px;color:#cfe0f5;background:rgba(150,178,215,.16);white-space:nowrap;overflow:hidden}
.h3md-box.audios .h3md-tile .tk{color:#f3d6a4}.h3md-box.images .h3md-tile .tk{color:#bfe3ae}
.h3md-tile .mid{position:relative;flex:1;min-height:0;display:flex}
.h3md-tile .mid .tv{flex:1}
.h3md-tile .mid.aud{background:rgba(160,105,30,.12)}
.h3md-tile .nm{text-align:left;direction:ltr}
.h3md-tile .bdg.use{top:auto;bottom:2px;left:2px}
.h3md-tile .bdg.dur{bottom:2px;right:2px}
.h3md-tile .bdg.snd{bottom:2px;left:auto;right:auto;top:2px;left:2px}
.h3md-tile .play{bottom:auto;top:2px;left:2px}
.h3md-tile:hover .bdg.use{display:block}
.h3md-tile .acts{top:14px}
.h3md-tile.picked{border-color:#ffd25a;box-shadow:0 0 0 1px #ffd25a}
.h3md-marq{position:absolute;border:1px dashed #ffd25a;background:rgba(255,210,90,.12);pointer-events:none;z-index:5}
.h3md-ro{font-weight:600;padding:0 2px}
.h3md-modes{display:inline-flex;gap:8px;align-items:center;padding:0 6px;border-right:1px solid rgba(150,178,215,.25);margin-right:2px}
.h3md-modes label{display:inline-flex;gap:2px;align-items:center;cursor:pointer}
.h3md-label.ins{cursor:pointer}
.h3md-root{position:relative}
.h3md-row.one{height:26px;align-items:center}
.h3md-seg>h4{height:22px}
.h3md-sec>h4{height:24px;align-items:center}
.h3md-tabs{height:38px;overflow-y:hidden}
.h3md-tab{height:35px}
.h3md-bar,.h3md-sec>h4,.h3md-seg h4,.h3md-row.one{flex-wrap:nowrap;white-space:nowrap;overflow:hidden}
.h3md-sec>h4 .h3md-small,.h3md-row.one .ell{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;flex:1 1 0}
.h3md-one{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;height:16px;line-height:16px}
.h3md-stat{display:inline-flex;gap:4px;align-items:center;cursor:pointer;border-radius:9px;padding:0 7px;font-size:11px;line-height:17px;border:1px solid rgba(150,178,215,.3);user-select:none}
.h3md-stat .ok{color:#7c7}.h3md-stat .bad{color:#ff7a7a}.h3md-stat .wn{color:#f0c060}
.h3md-pop{position:absolute;z-index:30;background:var(--comfy-menu-bg,#232323);border:1px solid #4a8ad0;border-radius:6px;box-shadow:0 6px 18px rgba(0,0,0,.6);padding:6px;overflow:auto}
.h3md-pop .ln{margin:2px 0;white-space:normal}
.h3md-pop .hd{font-weight:700;margin:4px 0 2px}
.h3md-sec.tl,.h3md-seg{position:relative}
.h3md-refs{height:180px;min-height:44px;overflow-y:auto;overflow-x:hidden;resize:vertical;border:1px solid rgba(150,178,215,.16);border-radius:4px;padding:2px 4px;margin:3px 0;scrollbar-width:thin}
.h3md-labrow{display:flex;flex-wrap:nowrap;align-items:center;gap:2px;height:26px;overflow-x:auto;overflow-y:hidden;white-space:nowrap;scrollbar-width:thin}
.h3md-labrow>span[data-seg-labels]{display:inline-flex;flex-wrap:nowrap}
.h3md-subline{display:flex;gap:6px;align-items:center;height:22px;white-space:nowrap;overflow:hidden;margin-top:3px}
.h3md-subline .ell{overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 0}
.h3md-aref .h3md-modes{border-right:none}
.h3md-tl2+.h3md-small,.h3md-tlhint{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.h3md-pf{position:relative;background:var(--comfy-input-bg,#111);border-radius:4px}
.h3md-pf-back,.h3md-pf textarea{font-family:ui-monospace,Consolas,monospace;font-size:11px;line-height:1.4;padding:2px 4px;border:1px solid transparent;white-space:pre-wrap;overflow-wrap:break-word;word-break:normal;letter-spacing:normal;tab-size:4}
.h3md-pf-back{position:absolute;top:0;left:0;overflow:hidden;color:transparent;pointer-events:none;border-radius:4px}
.h3md-root .h3md-pf textarea{position:relative;display:block;background:transparent;border-color:var(--border-color,#555);margin:0}
.h3md-pf-back mark{color:transparent;border-radius:3px;background:rgba(255,255,255,.15);box-shadow:0 0 0 1px rgba(255,255,255,.25)}
.h3md-pf-back mark.image{background:rgba(80,140,60,.55);box-shadow:0 0 0 1px #6fae55}
.h3md-pf-back mark.video{background:rgba(50,100,170,.6);box-shadow:0 0 0 1px #5d93d6}
.h3md-pf-back mark.audio{background:rgba(160,105,30,.6);box-shadow:0 0 0 1px #d09040}
.h3md-pf-back mark.bad{background:rgba(170,50,50,.6);box-shadow:0 0 0 1px #e06060}
.h3md-at{position:absolute;z-index:20;min-width:220px;max-width:360px;max-height:180px;overflow-y:auto;background:var(--comfy-menu-bg,#232323);border:1px solid #4a8ad0;border-radius:5px;box-shadow:0 4px 14px rgba(0,0,0,.55);padding:2px 0}
.h3md-at .it{display:flex;gap:6px;align-items:center;padding:2px 6px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.h3md-at .it.sel,.h3md-at .it:hover{background:rgba(74,138,208,.35)}
.h3md-at .it .h3md-label{margin:0;cursor:pointer}
.h3md-shotsec>.h3md-sub{margin:6px 0 0 0}
.h3md-sub{border:1px solid rgba(150,178,215,.22);border-radius:5px;padding:5px;background:rgba(150,178,215,.06)}
.h3md-sub>h4{margin:0 0 4px 0;font-size:12px;display:flex;gap:6px;align-items:center;height:24px;flex-wrap:nowrap;white-space:nowrap;overflow:hidden}
.h3md-sub>h4 .h3md-small{overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 0}
.h3md-note{white-space:normal;line-height:1.35;margin:0 0 4px 0;color:#b8c4d6}
.h3md-sub.gl{border-left:4px solid #3fa07a;background:rgba(63,160,122,.08)}
.h3md-sub.gl>h4{color:#9fdcc0}
.h3md-root button.clr{color:#f0a0a0}`;
  document.head.append(h("style", { id: "h3md-editor-css" }, css + PERF_CSS));
}

function mb(b) { b = Number(b || 0); return b >= 1024 ** 3 ? `${(b / 1024 ** 3).toFixed(1)} GB` : b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`; }
function injectPlanIOStyles() {
  if (document.getElementById("h3md-planio-css")) return;
  document.head.append(h("style", { id: "h3md-planio-css" },
    ".h3md-planio{display:flex;gap:6px;align-items:center;flex-wrap:nowrap;white-space:nowrap;overflow:hidden;margin-bottom:4px;padding-bottom:4px;border-bottom:1px solid rgba(150,178,215,.18)}" +
    ".h3md-planio .sp{flex:1}.h3md-planio button:disabled{opacity:.45;cursor:not-allowed}.h3md-planio-msg{overflow:hidden;text-overflow:ellipsis;min-width:0}" +
    ".h3md-importdlg .ln{white-space:normal}.h3md-importdlg button:disabled{opacity:.45;cursor:not-allowed}"));
}

// ------------------------------------------------------------------ editor
class DirectorEditor {
  constructor(node) {
    this.node = node;
    this.planW = node.widgets.find((w) => w.name === "plan_json");
    this.widthW = node.widgets.find((w) => w.name === "width");
    this.heightW = node.widgets.find((w) => w.name === "height");
    this.contW = node.widgets.find((w) => w.name === "continuity_prompt");
    if (this.contW) this.contW.hidden = true;   // 2026-10-24: hidden, always on (backend ignores its value); kept so widgets_values stay aligned
    this.selShot = 0;
    this.splitDlg = false;
    this.plan = null;
    this.parseError = null;
    this.lastText = null;
    this.probes = {};
    this.collapsed = { global: false, bins: false };
    this.focusPrompt = null;
    this.pick = { kind: null, ids: new Set(), anchor: null };   // library multi-selection (UI only)
    this.fpsW = node.widgets.find((w) => w.name === "refmod_reference_fps");
    this.lockFps();
    this.rmInfos = {};   // refmod file -> /h3md/refmod_info
    this.as = null;      // H3MD autosplit preview (/h3md/autosplit) for asKey
    this.asKey = null;
    this.asTimer = null;
    injectStyles();
    injectRefModStyles();
    // live-update the read-only width/height text when the node widgets change (wraps, never replaces, the callback)
    for (const w of [this.widthW, this.heightW]) {
      if (!w) continue;
      const prev = w.callback;
      w.callback = function () { const r = prev?.apply(this, arguments); try { node.__h3md?.updateRes(); } catch { /* ignore */ } return r; };
    }
    this.root = h("div", { class: "h3md-root" });
    // keep wheel scrolling inside the editor instead of zooming the canvas
    this.root.addEventListener("wheel", (e) => {
      const t = e.target.closest("textarea, .h3md-grid, .h3md-at") || this.root;
      if (t.scrollHeight > t.clientHeight) e.stopPropagation();
    }, { passive: true });
    this.root.addEventListener("pointerenter", () => { if (this.planW && this.planW.value !== this.lastText) this.reload(); else this.updateRes(); });
    this.widget = node.addDOMWidget("h3md_editor", "h3md_editor", this.root, {
      serialize: false, hideOnZoom: false, margin: 4,
      getValue: () => undefined, setValue: () => {},
      getMinHeight: () => 320,
    });
    this.widget.serialize = false; // not part of widgets_values: plan_json/width/height keep their slots
    this.reload();
  }

  // ---- user-resizable heights (media boxes, prompts): stored in node.properties[PROP_SIZES]
  sizeOf(key) {
    const v = this.node.properties && this.node.properties[PROP_SIZES] && this.node.properties[PROP_SIZES][key];
    return Number.isFinite(Number(v)) && Number(v) > 0 ? Number(v) : null;
  }
  setSizeOf(key, px) {
    this.node.properties = this.node.properties || {};
    const m = { ...(this.node.properties[PROP_SIZES] || {}) };
    m[key] = Math.round(px);
    this.node.properties[PROP_SIZES] = m;
    this.node.setDirtyCanvas?.(true, true);
    try { app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.(); } catch { /* optional */ }
  }
  // remember a height only when the user drags the native resize corner (pointerdown → pointerup with a changed height)
  trackResize(el, key) {
    el.addEventListener("pointerdown", (e) => {
      const r = el.getBoundingClientRect();
      if (e.clientX < r.right - 18 || e.clientY < r.bottom - 18) return;   // only the bottom-right corner
      const h0 = el.offsetHeight;
      const up = () => {
        window.removeEventListener("pointerup", up, true);
        if (Math.abs(el.offsetHeight - h0) >= 2) this.setSizeOf(key, el.offsetHeight);
      };
      window.addEventListener("pointerup", up, true);
    });
  }
  resText() {
    const p = this.plan || {};
    const ow = Number(this.widthW?.value || 0), oh = Number(this.heightW?.value || 0);
    return { w: ow > 0 ? ow : p.width, h: oh > 0 ? oh : p.height };
  }
  updateRes() {
    const r = this.resText();
    const a = this.root.querySelector("[data-res-w]"), b = this.root.querySelector("[data-res-h]");
    if (a) a.textContent = String(r.w ?? "?");
    if (b) b.textContent = String(r.h ?? "?");
  }

  applyVisibility() {
    if (this.planW) this.planW.hidden = true;   // plan_json is edited only through the visual editor
    const sz = this.node.computeSize ? this.node.computeSize() : null;
    if (sz) this.node.setSize([Math.max(this.node.size[0], sz[0]), Math.max(this.node.size[1], sz[1])]);
    this.node.setDirtyCanvas?.(true, true);
  }

  reload() {
    const text = this.planW ? String(this.planW.value ?? "") : "";
    this.lastText = text;
    const r = M.parsePlanText(text);
    this.parseError = r.error;
    this.plan = r.plan ? RM.ensure(M.ensureShape(r.plan)) : (r.empty ? null : null);
    if (this.plan) M.applyVideoLengths(this.plan);  // shown as the Director will run it (saved on the next edit)
    const orphan = this.plan ? M.clearOrphanRefState(this.plan) : 0;   // H3MD noref: stale reference state from an older save
    this.node.properties = this.node.properties || {};
    for (const k of OBSOLETE_PROPS) delete this.node.properties[k];
    if (this.plan) this.probeAll();
    if (orphan) this.commit();   // write the cleaned plan back so the queued plan matches what is shown
    this.render();
    this.applyVisibility();
  }

  commit(full = true) {
    if (!this.plan) return;
    RM.ensure(this.plan);
    this.plan.globalMode = "prepend";   // legacy key kept for older H3MD copies; global is always prepended
    M.clearOrphanRefState(this.plan);  // no references left -> no reference state (H3MD noref)
    M.applyVideoLengths(this.plan);  // shots with a reference video follow its window (rounded up to 5+17n)
    M.syncStartFrames(this.plan);
    const text = M.serializePlan(this.plan);
    this.lastText = text;
    if (this.planW) {
      this.planW.value = text;
      this.planW.callback?.(text);
    }
    this.node.setDirtyCanvas?.(true, true);
    try { app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.(); } catch { /* optional */ }
    if (full) this.render(); else this.renderStatus();
  }

  async probeAll() {
    const files = new Set();
    for (const k of M.KINDS) for (const it of this.plan.media[k] || []) if (it && it.file) files.add(it.file);
    let changed = false;
    await Promise.all([...files].map(async (f) => {
      const p = await probe(f);
      if (p && this.probes[f] !== p) { this.probes[f] = p; changed = true; }
    }));
    await Promise.all((this.plan.media.refmods || []).map(async (it) => {
      if (!it || !it.file) return;
      const p = await refmodInfo(it.file);
      if (p && this.rmInfos[it.file] !== p) { this.rmInfos[it.file] = p; changed = true; }
    }));
    if (changed) this.render();
  }

  // refmod_reference_fps: fixed at H3's 24 fps (the backend ignores any saved value); the real widget is hidden and the editor toolbar
  // shows it as read-only text 'refmod reference fps  24' (2026-10-24); the widget is
  // kept so widgets_values stay aligned; re-applied after configure (an old workflow may load another value into it)
  lockFps() {
    const w = this.fpsW;
    if (!w) return;
    w.value = 24;
    w.disabled = true;
    w.hidden = true;   // 2026-10-24: real widget not rendered (canvas + Vue nodes); read-only text in renderToolbar() instead
    if (w.options) { w.options.min = 24; w.options.max = 24; }
    if (!w.__h3mdFpsLocked) {
      w.__h3mdFpsLocked = true;
      const prev = w.callback;
      w.callback = function () { w.value = 24; return prev?.apply(this, arguments); };
    }
  }
  rmLabels(i) {
    const s = this.plan && this.plan.segments[i];
    return s && typeof s === "object" ? RM.labels(this.plan, s, this.rmInfos) : [];
  }
  // the shot's own labels (base: the validator's, else computeLabels) renumbered like the backend around positioned RefMods
  ownLabels(i, base = null) {
    const s = this.plan && this.plan.segments[i];
    if (!s || typeof s !== "object") return base || [];
    return RM.retagOwn(base || M.computeLabels(s, M.mediaMaps(this.plan)), RM.layout(this.plan, s, this.rmInfos).ownNum);
  }

  validation() {
    const v = M.validatePlan(this.plan, { width: this.widthW?.value, height: this.heightW?.value, probes: this.probes,
                                          continuity: true });
    // RefMod checks (the backend raises / notes the same)
    const ids = new Set();
    for (const it of this.plan.media.refmods || []) {
      if (!it || !it.id || !it.file) { v.errors.push({ seg: null, msg: "H3MD Director + RefMod: every media.refmods entry needs 'id' and 'file'" }); continue; }
      if (ids.has(it.id)) v.errors.push({ seg: null, msg: `H3MD Director + RefMod: duplicate media id '${it.id}' in refmods` });
      ids.add(it.id);
      const info = this.rmInfos[it.file];
      if (info && info.exists === false) v.warnings.push({ seg: null, level: "error", msg: `RefMod '${it.id}': ${info.error || "not found"}` });
    }
    (this.plan.segments || []).forEach((s, i) => {
      if (!s || typeof s !== "object") return;
      const labs = this.rmLabels(i);
      const cores = new Set(labs.map((l) => l.core).filter(Boolean));
      // the shot prompt may also use the resolved core label directly (e.g. <Video 2>)
      for (let k = v.warnings.length - 1; k >= 0; k--) {
        const w = v.warnings[k], m = String(w.msg).match(/^prompt mentions (<(?:Picture|Video|Audio) \d+>) but/);
        if (w.seg === i && m && cores.has(m[1])) v.warnings.splice(k, 1);
      }
      for (const l of labs) {
        if (!ids.has(l.id)) v.errors.push({ seg: i, msg: `H3MD Director + RefMod: segment ${i + 1} references unknown RefMod '${l.id}'` });
      }
      const prompt = M.resolvePrompt(this.plan.globalPrompt, "prepend", s.prompt || "");
      const used = new Set([...prompt.matchAll(/<\s*RefMod\s*(\d+)\s*>|(?<![A-Za-z0-9_])RefMod\s*(\d+)(?![0-9])/gi)].map((m) => Number(m[1] || m[2])));
      for (const j of used) if (j < 1 || j > labs.length) v.warnings.push({ seg: i, msg: `prompt mentions RefMod ${j} but this shot has ${labs.length} RefMod(s)` });
      labs.forEach((l, k) => { if (!used.has(k + 1) && !(l.core && prompt.includes(l.core))) v.warnings.push({ seg: i, level: "info", msg: `${l.tag} (${l.id}) is not mentioned in the prompt` }); });
      if (labs.some((l) => l.at !== null && l.core)) {
        // positioned RefMods renumber the shot's own pictures / videos: redo the tag checks with the new numbers
        const base = (v.segments && v.segments[i] && v.segments[i].labels) || M.computeLabels(s, M.mediaMaps(this.plan));
        const own = this.ownLabels(i, base);
        const stale = new Set(base.filter((l) => (l.kind === "image" || l.kind === "video")).map((l) => `${l.tag} (${l.id}) is not mentioned in the prompt`));
        for (let k = v.warnings.length - 1; k >= 0; k--) {
          const w = v.warnings[k];
          if (w.seg === i && (/^prompt mentions <(?:Picture|Video|Audio) \d+> but this segment/.test(String(w.msg)) || (w.level === "info" && stale.has(String(w.msg))))) v.warnings.splice(k, 1);
        }
        const have = new Set([...own.map((l) => l.tag), ...cores]);
        const usedTags = new Set(prompt.match(/<(?:Picture|Video|Audio) \d+>/g) || []);
        for (const tag of usedTags) if (!have.has(tag)) v.warnings.push({ seg: i, msg: `prompt mentions ${tag} but this segment has no such reference` });
        for (const l of own) if ((l.kind === "image" || l.kind === "video") && !l.missing && !usedTags.has(l.tag)) v.warnings.push({ seg: i, level: "info", msg: `${l.tag} (${l.id}) is not mentioned in the prompt` });
      }
    });
    return v;
  }

  // ---------------------------------------------------------------- rendering
  render() {
    const scroll = this.root.scrollTop;
    const active = document.activeElement && this.root.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    this.root.replaceChildren();
    this.root.append(this.renderPlanIO(), this.renderToolbar());
    if (this.importDlg) this.root.append(this.renderImportDlg());
    if (!this.plan) {
      // broken or empty plan_json: a read-only error line + New plan (no raw JSON editor)
      this.root.append(h("div", { class: "h3md-sec" },
        h("div", { "data-plan-error": this.parseError ? "1" : "0" }, this.parseError ? h("span", { class: "h3md-bad" }, `plan_json is invalid: ${String(this.parseError).replace(/^plan_json is not valid JSON\s*/, "").replace(/^\((.*)\)$/, "$1")}`) : "plan_json is empty."),
        h("div", { class: "h3md-row" }, h("button", { onclick: () => { this.plan = M.newPlan(); this.commit(); } }, "New plan 新建计划"),
          h("span", { class: "h3md-small" }, "or click Import plan… to start from a saved plan 或者点 Import plan… 从保存的计划开始"))));
      return;
    }
    this.val = this.validation();
    // the global prompt lives inside the Shots section, directly under the shot card
    this.root.append(this.renderBins(), this.renderRefVideoHeight(), this.renderTimeline(), this.renderSegments());
    if (this.statOpen) this.root.append(this.renderStatPop());
    this.renderStatus();
    this.applyAutoSplitUI();
    this.scheduleAutoSplit();
    this.root.scrollTop = scroll;
    if (active) {
      const e = this.root.querySelector(`[data-key="${CSS.escape(active)}"]`);
      if (e) e.focus({ preventScroll: true });
    }
  }

  renderToolbar() {
    const lay = this.plan ? M.layout(this.plan) : [];
    const total = lay.length ? lay[lay.length - 1].endFrame : 0;
    return h("div", { class: "h3md-bar" },
      h("span", { class: "h3md-title" }, "H3MD Plan"),
      this.plan ? h("span", { class: "h3md-small" }, `${lay.length} seg · ${fmtF(total)}`) : null,
      this.plan ? h("span", { class: "h3md-stat", "data-stat": "1", onclick: () => { this.statOpen = !this.statOpen; this.render(); } }) : null,
      // refmod_reference_fps as read-only text (the real widget is hidden and locked at 24 by lockFps)
      h("span", { class: "h3md-small", "data-refmod-fps": "1", style: { whiteSpace: "pre" }, title: "H3 uses 24 fps (fixed, not editable)" }, "refmod reference fps  24"),
      this.plan ? h("span", { class: "h3md-small", "data-as-budget": "1" }) : null,
      h("span", { class: "sp" }));
  }

  // ---------------------------------------------------------------- v0.2.0 plan export / import (row above the plan header)
  renderPlanIO() {
    injectPlanIOStyles();
    const can = !!(this.plan && (this.plan.segments || []).length);
    const tip = can ? "" : "Nothing to export yet";
    const st = this.planIO || (this.planIO = { busy: false, msg: "", size: null, big: [], withBig: false });
    const file = h("input", { type: "file", accept: ".zip,.json,application/zip,application/json", style: { display: "none" },
      onchange: (e) => { const f = e.target.files && e.target.files[0]; e.target.value = ""; if (f) this.importPlanFile(f); } });
    const bigBox = st.big.length ? h("label", { class: "h3md-small", title: st.big.map((b) => `${b.file} · ${mb(b.bytes)}`).join("\n") },
      h("input", { type: "checkbox", checked: st.withBig, onchange: (e) => { st.withBig = e.target.checked; this.refreshExportSize(); } }),
      ` incl. big ref video${st.big.length > 1 ? "s" : ""}`) : null;
    if (can && st.size === null && !st.sizing) this.refreshExportSize();
    return h("div", { class: "h3md-planio", "data-planio": "1" },
      file,
      h("button", { disabled: st.busy, title: "Open a saved H3MD plan (.zip) or a plan .json written by an agent with the H3MD prompt skill",
        onclick: () => file.click() }, "Import plan…"),
      h("span", { class: "h3md-small h3md-planio-msg", "data-planio-msg": "1" }, st.msg || ""),
      h("span", { class: "sp" }),
      can && st.size !== null ? h("span", { class: "h3md-small", title: "pictures + songs" + (st.withBig ? " + big reference videos" : "") }, `media ${mb(st.size)}`) : null,
      bigBox,
      h("button", { disabled: !can || st.busy, title: tip || "Plan, all node settings and the pictures / songs it uses, in one .zip (for yourself)",
        onclick: () => this.exportPlan(true) }, "Export plan + media"),
      h("button", { disabled: !can || st.busy, title: tip || "Plan and all node settings, no pictures / songs (to share)",
        onclick: () => this.exportPlan(false) }, "Export plan (no media)"));
  }
  setPlanIOMsg(msg, bad = false) {
    const st = this.planIO || (this.planIO = { busy: false, msg: "", size: null, big: [], withBig: false });
    st.msg = msg;
    const el = this.root.querySelector("[data-planio-msg]");
    if (el) { el.textContent = msg; el.classList.toggle("h3md-bad", !!bad); }
  }
  async refreshExportSize() {
    const st = this.planIO;
    if (!this.plan || !st) return;
    st.sizing = true;
    try {
      const r = await api.fetchApi("/h3md/plan/export_size", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_json: M.serializePlan(this.plan), include_big_videos: st.withBig }) });
      const j = await r.json();
      if (r.ok) { st.size = j.media_bytes || 0; st.big = j.big_videos || []; }
    } catch { /* route missing (old backend): leave the size empty */ }
    st.sizing = false;
    const row = this.root.querySelector("[data-planio]");
    if (row) row.replaceWith(this.renderPlanIO());
  }
  savePrefixes() {
    // filename prefixes of every save node (also inside subgraphs) -> the backend picks the newest finished video for the thumbnail
    const out = new Set();
    const walk = (graph, depth) => {
      for (const n of (graph && (graph._nodes || graph.nodes)) || []) {
        for (const w of n.widgets || []) if (/^(filename_?)?prefix$/i.test(String(w.name || "")) && typeof w.value === "string" && w.value) out.add(w.value);
        if (n.subgraph && depth < 4) walk(n.subgraph, depth + 1);
      }
    };
    try { walk(app.graph, 0); } catch { /* ignore */ }
    return [...out];
  }
  async exportPlan(withMedia) {
    const st = this.planIO;
    if (!this.plan || st.busy) return;
    st.busy = true; this.setPlanIOMsg("exporting…");
    try {
      const wf = (await app.graphToPrompt()).workflow;
      const wfName = String(app.extensionManager?.workflow?.activeWorkflow?.filename || app.extensionManager?.workflow?.activeWorkflow?.path || "H3MD plan")
        .split("/").pop().replace(/\.json$/i, "");
      const r = await api.fetchApi("/h3md/plan/export", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_json: M.serializePlan(this.plan), workflow: wf, with_media: withMedia,
          include_big_videos: !!st.withBig, name: wfName, prefixes: this.savePrefixes() }) });
      if (!r.ok) { const j = await r.json().catch(() => ({})); throw new Error(j.error || `HTTP ${r.status}`); }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = String(cd).match(/filename\*=UTF-8''([^;]+)/);
      const name = m ? decodeURIComponent(m[1]) : `${wfName}.zip`;
      const a = h("a", { href: URL.createObjectURL(blob), download: name });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
      this.setPlanIOMsg(`saved ${name} · ${mb(blob.size)}`);
    } catch (e) {
      this.setPlanIOMsg(`export failed: ${e.message || e}`, true);
    } finally {
      st.busy = false;
    }
  }
  async importPlanFile(f) {
    const st = this.planIO;
    st.busy = true; this.setPlanIOMsg(`reading ${f.name}…`);
    try {
      const fd = new FormData();
      fd.append("file", f, f.name);
      const r = await api.fetchApi("/h3md/plan/import", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
      this.importDlg = j;
      this.setPlanIOMsg("");
    } catch (e) {
      this.setPlanIOMsg(`import failed: ${e.message || e}`, true);
    } finally {
      st.busy = false;
      this.render();
    }
  }
  renderImportDlg() {
    const j = this.importDlg, man = j.manifest || {};
    let plan = null;
    try { plan = JSON.parse(j.plan_json); } catch { /* shown as a problem below */ }
    const lay = plan ? M.layout(M.ensureShape(JSON.parse(j.plan_json))) : [];
    const frames = lay.length ? lay[lay.length - 1].endFrame : 0;
    const close = () => { this.importDlg = null; this.render(); };
    const usePlan = () => {
      this.plan = RM.ensure(M.ensureShape(JSON.parse(j.plan_json)));
      this.importDlg = null;
      this.commit();
      this.probeAll();
      this.setPlanIOMsg(`imported plan '${j.name}'`);
    };
    const useAll = async () => {
      const wf = JSON.parse(JSON.stringify(j.workflow));
      const nodes = [...(wf.nodes || []), ...((wf.definitions && wf.definitions.subgraphs) || []).flatMap((s) => s.nodes || [])];
      for (const n of nodes) if (String(n.type || "").startsWith("H3MDDirector") && Array.isArray(n.widgets_values)) n.widgets_values[0] = j.plan_json;
      this.importDlg = null;
      this.render();
      // ComfyUI's own load: missing node packs show red, missing models open the usual download dialog
      await app.loadGraphData(wf, true, true, `${j.name}.json`);
    };
    const probs = j.problems || [];
    return h("div", { class: "h3md-pop h3md-importdlg", "data-import-dlg": "1", style: { top: "34px", left: "6px", right: "6px", maxHeight: "420px" } },
      h("div", { class: "h3md-row one" }, h("b", {}, "Import H3MD plan"), h("span", { style: { flex: 1 } }), h("button", { onclick: close }, "✕")),
      h("div", { class: "h3md-row", style: { alignItems: "flex-start", gap: "10px" } },
        j.thumbnail ? h("img", { src: j.thumbnail, style: { maxWidth: "140px", maxHeight: "160px", borderRadius: "4px" } }) : null,
        h("div", { class: "h3md-small", style: { lineHeight: "1.5" } },
          h("div", {}, h("b", {}, j.name), j.kind === "json" ? " · plan .json" : ` · H3MD ${man.h3md_version || "?"}`),
          h("div", {}, `${lay.length} shot(s) · ${fmtF(frames)}${plan && plan.width ? ` · ${plan.width}×${plan.height}` : ""}${man.seed != null ? ` · seed ${man.seed}` : ""}`),
          j.folder ? h("div", {}, `media → input/${j.folder}/`) : h("div", {}, man.with_media === false ? "no media in this plan: upload your own pictures / songs" : ""),
          man.models && man.models.length ? h("div", { title: man.models.map((m) => m.name).join("\n") }, `${man.models.length} model file(s) named in the workflow`) : null)),
      probs.length ? h("div", { class: "hd h3md-bad" }, `${probs.length} problem(s) – nothing was changed, fix them after importing`) : h("div", { class: "h3md-ok h3md-small" }, "✓ no problems found"),
      probs.map((p) => h("div", { class: "ln h3md-bad" }, "✗ " + p)),
      h("div", { class: "h3md-row", style: { marginTop: "6px" } },
        h("button", { disabled: !plan, title: "Replace this Director's plan; keep this workflow's other settings", onclick: usePlan }, "Plan only"),
        h("button", { disabled: !plan || !j.workflow, title: j.workflow ? "Open the saved workflow (all node settings) with this plan in a new tab" : "this file has no workflow (plan .json)",
          onclick: useAll }, "Plan + all settings"),
        h("span", { style: { flex: 1 } }),
        h("button", { onclick: close }, "Cancel")));
  }

  // plan check messages: the full report is the Director's plan_info output; the editor only shows a small
  // status pill (✓ / ✗ n / ⚠ n) whose popover lists the messages (with the cheap 'fix' actions)
  statusLists() {
    const v = this.val || this.validation();
    const tag = (m) => (m.seg === null || m.seg === undefined ? "" : `Shot ${m.seg + 1}: `) + String(m.msg).replace(/^H3MD Director: /, "");
    return {
      errors: [...v.errors, ...v.warnings.filter((w) => w.level === "error")].map((m) => ({ text: tag(m) })),
      warns: v.warnings.filter((w) => w.level !== "error" && w.level !== "info").map((m) => ({ text: tag(m), fix: m.fix })),
      infos: v.warnings.filter((w) => w.level === "info").map((m) => ({ text: tag(m) })),
    };
  }
  renderStatus() {
    if (!this.plan) return;
    this.val = this.validation();
    const L = this.statusLists();
    const el = this.root.querySelector("[data-stat]");
    if (el) {
      fill(el, L.errors.length ? h("span", { class: "bad" }, `✗ ${L.errors.length}`) : h("span", { class: "ok" }, "✓"),
        L.warns.length ? h("span", { class: "wn" }, `⚠ ${L.warns.length}`) : null,
        L.infos.length ? h("span", { class: "h3md-small" }, `· ${L.infos.length}`) : null);
      el.title = [L.errors.length ? "✗ the Director node will refuse this plan" : "✓ plan valid",
        ...L.errors.map((x) => "✗ " + x.text), ...L.warns.map((x) => "⚠ " + x.text), ...L.infos.slice(0, 12).map((x) => "· " + x.text),
        "(click for details · full report: Director output plan_info)"].join("\n");
    }
    const pop = this.root.querySelector("[data-stat-pop]");
    if (pop) pop.replaceWith(this.renderStatPop());
    const v = this.val;
    for (const t of this.root.querySelectorAll("[data-tab]")) t.classList.toggle("err", v.errors.some((e) => e.seg === Number(t.dataset.tab)));
    for (const box of this.root.querySelectorAll("[data-seg-labels]")) fill(box, this.labelChips(Number(box.dataset.segLabels)));
    this.scheduleAutoSplit();
  }
  renderStatPop() {
    const L = this.statusLists();
    const bar = this.root.querySelector(".h3md-bar");
    const top = bar ? bar.offsetTop + bar.offsetHeight + 2 : 30;
    return h("div", { class: "h3md-pop", "data-stat-pop": "1", style: { top: `${top}px`, left: "6px", right: "6px", maxHeight: "320px" } },
      h("div", { class: "h3md-row one" },
        L.errors.length ? h("b", { class: "h3md-bad" }, `✗ ${L.errors.length} problem(s) – the Director node will refuse this plan`)
          : h("b", { class: "h3md-ok" }, `✓ plan valid · ${this.val.width}x${this.val.height} · total ${fmtF(this.val.totalFrames)}`),
        h("span", { style: { flex: 1 } }), h("span", { class: "h3md-small" }, "full report: plan_info output"),
        h("button", { onclick: () => { this.statOpen = false; this.render(); } }, "✕")),
      L.errors.map((x) => h("div", { class: "ln h3md-bad" }, "✗ " + x.text)),
      L.warns.map((x) => h("div", { class: "ln h3md-warn" }, "⚠ " + x.text, x.fix ? h("button", { style: { marginLeft: "6px" }, onclick: () => this.applyFix(x.fix) }, "fix") : null)),
      L.infos.length ? h("div", { class: "hd h3md-small" }, "hints") : null,
      L.infos.map((x) => h("div", { class: "ln h3md-info" }, "· " + x.text)));
  }

  applyFix(fix) {
    const it = (this.plan.media[fix.kind] || []).find((x) => x.id === fix.id);
    if (it) Object.assign(it, fix.set);
    this.commit();
  }

  // Global prompt: shown under the shot card (same width), but it is ONE text for all shots – switching
  // tabs never changes it.  Always prepended to every shot prompt (globalMode is gone).
  renderGlobal() {
    const p = this.plan;
    const sec = h("div", { class: "h3md-sub gl", "data-global": "1" });
    sec.append(h("h4", {}, h("button", { onclick: () => { this.collapsed.global = !this.collapsed.global; this.render(); } }, this.collapsed.global ? "▸" : "▾"),
      "Global 全局提示词", h("span", { class: "h3md-small" }, "applies to every shot · prepended to each shot prompt (trigger words, style … or leave empty)")));
    if (this.collapsed.global) return sec;
    sec.append(this.promptField({ rows: 3, key: "g-prompt", sizeKey: "g-prompt", value: p.globalPrompt || "", strict: false,
      placeholder: "globalPrompt – prepended to every shot prompt (empty = shot prompt only) – type @ to insert a reference label",
      labels: () => this.globalLabels(),
      onText: (t) => { p.globalPrompt = t; this.commit(false); } }));
    return sec;
  }

  // Guard: the clear buttons must never change the global prompt (only the user's own
  // typing in the Global box does).  Runs fn and puts plan.globalPrompt back exactly as it was.
  keepGlobal(fn) {
    const had = Object.prototype.hasOwnProperty.call(this.plan, "globalPrompt"), g = this.plan.globalPrompt;
    try { return fn(); } finally { if (had) this.plan.globalPrompt = g; else delete this.plan.globalPrompt; }
  }

  // ---------------------------------------------------------------- bins
  renderBins() {
    const sec = h("div", { class: "h3md-sec" });
    const cnt = (k) => (this.plan.media[k] || []).length;
    const segN = this.plan.segments.length;
    sec.append(h("h4", {}, h("button", { onclick: () => { this.collapsed.bins = !this.collapsed.bins; this.render(); } }, this.collapsed.bins ? "▸" : "▾"),
      "Media 素材库", h("span", { class: "h3md-small" }, `音频 ${cnt("audios")} · 视频 ${cnt("videos")} · 图片 ${cnt("images")}`),
      h("span", { style: { flex: 1 } }),
      this.collapsed.bins ? null : h("span", { class: "h3md-small" },
        segN ? `double-click = add to Shot ${Math.min(this.selShot, segN - 1) + 1} · drag = reorder / drop on a shot` : "")));
    if (this.collapsed.bins) return sec;
    const lib = h("div", { class: "h3md-lib" });
    for (const kind of BIN_ORDER) lib.append(this.renderBin(kind));
    sec.append(lib);
    return sec;
  }

  // reference-video resolution row (between the media library's 图片 box and the shots); native removes the key
  renderRefVideoHeight() {
    const p = this.plan;
    if (!advanced()) {   // v0.3: fixed auto, row not shown (auto follows the VRAM estimate)
      if (p.refVideoHeight == null) { p.refVideoHeight = "auto"; queueMicrotask(() => this.commit()); }
      return null;
    }
    const raw = String(p.refVideoHeight ?? "native").trim().toLowerCase();
    const cur = REF_VIDEO_HEIGHTS.includes(raw) ? raw : "native";
    const sel = h("select", { "data-key": "ref-video-height",
      title: "reference video resolution sent to H3 (frames are resized in memory, files untouched, never upscaled)\n" +
        "native: as is · 480/576/720: short edge = value · auto: per segment at run time, steps down 768x1344 → 640x1120 → 512x896 → 480x840 while the estimated token total is too high",
      onchange: (e) => { const v = e.target.value; if (v === "native") delete p.refVideoHeight; else p.refVideoHeight = v; this.commit(); } },
      REF_VIDEO_HEIGHTS.map((v) => h("option", { value: v }, REF_VIDEO_HEIGHT_LABEL[v])));
    sel.value = cur;
    return h("div", { class: "h3md-sec", "data-ref-video-height": "1" },
      h("div", { class: "h3md-row" }, h("b", {}, "Ref video height 参考视频分辨率"), sel,
        h("span", { class: "h3md-small" }, "native = 不改 · 480/576/720 = 短边 · auto = 按段估算 token 自动降档")));
  }

  renderBin(kind) {
    const items = this.plan.media[kind] || [];
    const box = h("div", { class: `h3md-box ${kind}`, "data-bin": kind });
    const fileInput = h("input", { type: "file", accept: ACCEPT[kind], multiple: true, style: { display: "none" },
      onchange: async (e) => { await this.addUploads(kind, [...e.target.files]); e.target.value = ""; } });
    const listId = `h3md-dl-${this.node.id}-${kind}`;
    const dl = h("datalist", { id: listId });
    const isRM = kind === "refmods";
    // images / videos boxes also pick / upload RefMods (.safetensors or a models/refmods name -> RefMod path);
    // saved RefMods are listed in the box of their kind (image / video / audio; unknown kind -> images)
    const takesRM = kind === "images" || kind === "videos";
    const rmItems = (this.plan.media.refmods || []).filter((it) => it && this.rmBin(it) === kind);
    const pickInput = h("input", { list: listId, placeholder: isRM ? "RefMod name (models/refmods) …" : takesRM ? "file in input/ or RefMod name …" : "file in input/ …", "data-key": `pick-${kind}`,
      onfocus: async () => {
        if (isRM) { const mods = await listRefMods(); dl.replaceChildren(...mods.map((m) => h("option", { value: m.file }, `${m.kind}`))); return; }
        const files = await listInputFiles(kind);
        const mods = takesRM ? await listRefMods() : [];
        dl.replaceChildren(...files.map((f) => h("option", { value: f })), ...mods.map((m) => h("option", { value: m.file }, `RefMod ${m.kind}`)));
      },
      onkeydown: (e) => { if (e.key === "Enter") addPicked(); } });
    const isRefModName = async (f) => /\.safetensors$/i.test(f) || (await listRefMods()).some((m) => m.file === f);
    const addPicked = async () => { const f = pickInput.value.trim(); if (f) await this.addExisting(takesRM && await isRefModName(f) ? "refmods" : kind, f); };
    const npick = this.pick.kind === kind ? this.pick.ids.size : 0;
    box.append(h("div", { class: "h3md-boxhead" },
      h("b", {}, KIND_LABEL[kind]), h("span", { class: "h3md-cnt" }, items.length),
      h("span", { class: "h3md-small", "data-pickinfo": kind }, npick > 1 ? `${npick} selected – drag or double-click to add all` : ""),
      h("span", { style: { flex: 1 } }),
      h("button", { title: isRM ? "Upload RefMod .safetensors to ComfyUI input/refmods" : takesRM ? "Upload to ComfyUI input folder (.safetensors = RefMod, to input/refmods) – or drop files here" : "Upload to ComfyUI input folder – or drop files here", onclick: () => fileInput.click() }, advanced() ? "Upload…" : "＋"),
      ...(advanced() ? [pickInput, dl,
      h("button", { title: "Add the chosen input-folder file", onclick: addPicked }, "Add"),
      h("button", { title: "Refresh file list", onclick: () => { if (isRM) { rmListCache = null; rmInfoCache.clear(); this.rmInfos = {}; this.probeAll(); } else { delete fileListCache[kind]; if (takesRM) rmListCache = null; } pickInput.focus(); } }, "↻"),
      h("button", { class: "clr", "data-clear": kind, title: `remove ALL ${kind} from the plan (and from every shot) – files stay on disk`, disabled: items.length ? null : true,
        onclick: () => { this.keepGlobal(() => { if (isRM) for (const it of items.slice()) RM.remove(this.plan, it.id); else M.clearMedia(this.plan, kind); }); if (this.pick.kind === kind) this.pick = { kind: null, ids: new Set(), anchor: null }; this.commit(); } }, "Clear 清空")] : []),
      fileInput));
    const defH = Math.round(BIN_ROWS[kind] * (TILE_H + TILE_GAP) + GRID_PAD);
    const grid = h("div", { class: "h3md-grid", "data-grid": kind, style: { height: `${this.sizeOf("bin-" + kind) || defH}px` } });
    this.trackResize(grid, "bin-" + kind);
    if (!items.length && !rmItems.length) grid.append(h("div", { class: "h3md-empty" }, isRM ? "empty · type/pick a RefMod name and Add, Upload… a .safetensors, or drop it here" : takesRM ? "empty · drop files here or click ＋ (.safetensors = RefMod)" : "empty · drop files here or click ＋"));
    items.forEach((item, idx) => grid.append(this.renderTile(kind, item, idx)));
    rmItems.forEach((item) => grid.append(this.renderTile("refmods", item, this.plan.media.refmods.indexOf(item))));
    // reorder: drop on the empty area after the last tile = move to the end
    grid.addEventListener("dragover", (e) => { if (this.libDrag && this.libDrag.kind === kind && e.target === grid) e.preventDefault(); });
    grid.addEventListener("drop", (e) => {
      if (!this.libDrag || this.libDrag.kind !== kind || e.target !== grid) return;
      e.preventDefault(); e.stopPropagation();
      this.moveMedia(kind, this.libDrag.ids, null, true);
    });
    this.marquee(grid, kind);
    box.append(grid);
    // drop files from the OS straight into the box
    box.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); box.classList.add("drop"); } });
    box.addEventListener("dragleave", () => box.classList.remove("drop"));
    box.addEventListener("drop", (e) => {
      if (!e.dataTransfer?.files?.length) return;
      e.preventDefault(); e.stopPropagation(); box.classList.remove("drop");
      this.addUploads(kind, [...e.dataTransfer.files]);
    });
    return box;
  }

  rmBin(it) {
    const k = (this.rmInfos[it.file] && this.rmInfos[it.file].kind) || it.kind;
    return k === "video" ? "videos" : k === "audio" ? "audios" : "images";
  }

  // rubber-band selection: press on the empty grid area and drag (ctrl/shift adds to the selection)
  marquee(grid, kind) {
    grid.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target !== grid) return;
      const gr = grid.getBoundingClientRect();
      if (e.clientX > gr.right - 18 && e.clientY > gr.bottom - 18) return;           // resize corner
      if (e.clientX > grid.clientWidth + gr.left) return;                             // scrollbar
      e.preventDefault(); e.stopPropagation();
      const add = e.ctrlKey || e.metaKey || e.shiftKey;
      const base = add && this.pick.kind === kind ? new Set(this.pick.ids) : new Set();
      const x0 = e.clientX, y0 = e.clientY + 0, st0 = grid.scrollTop;
      const box = h("div", { class: "h3md-marq" });
      let moved = false;
      const tiles = [...grid.querySelectorAll(".h3md-tile")].filter((t) => t.dataset.media.startsWith(kind + ":"));   // RefMod tiles: click-select
      const onMove = (ev) => {
        if (!moved && Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) < 4) return;
        if (!moved) { moved = true; grid.append(box); }
        const g = grid.getBoundingClientRect();
        const ax = Math.min(x0, ev.clientX), bx = Math.max(x0, ev.clientX);
        const ay = Math.min(y0 - (grid.scrollTop - st0), ev.clientY), by = Math.max(y0 - (grid.scrollTop - st0), ev.clientY);
        Object.assign(box.style, { left: `${ax - g.left}px`, top: `${ay - g.top + grid.scrollTop}px`, width: `${bx - ax}px`, height: `${by - ay}px` });
        const ids = new Set(base);
        for (const t of tiles) {
          const r = t.getBoundingClientRect();
          const hit = r.right > ax && r.left < bx && r.bottom > ay && r.top < by;
          const id = t.dataset.media.slice(kind.length + 1);
          t.classList.toggle("picked", hit || base.has(id));
          if (hit) ids.add(id);
        }
        this.pick = { kind, ids, anchor: this.pick.anchor };
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove, true);
        window.removeEventListener("pointerup", onUp, true);
        box.remove();
        if (!moved) this.pick = { kind: null, ids: new Set(), anchor: null };       // plain click on empty area clears
        this.updatePickDOM();
      };
      window.addEventListener("pointermove", onMove, true);
      window.addEventListener("pointerup", onUp, true);
    });
  }

  clickTile(e, kind, id) {
    const items = (this.plan.media[kind] || []).map((x) => x.id);
    const same = this.pick.kind === kind;
    let ids;
    if (e.shiftKey && same && this.pick.anchor != null) {
      const a = items.indexOf(this.pick.anchor), b = items.indexOf(id);
      ids = new Set(e.ctrlKey || e.metaKey ? this.pick.ids : []);
      for (let k = Math.min(a, b); k <= Math.max(a, b); k++) if (k >= 0) ids.add(items[k]);
      this.pick = { kind, ids, anchor: this.pick.anchor };
    } else if (e.ctrlKey || e.metaKey) {
      ids = new Set(same ? this.pick.ids : []);
      if (ids.has(id)) ids.delete(id); else ids.add(id);
      this.pick = { kind, ids, anchor: id };
    } else {
      this.pick = { kind, ids: new Set([id]), anchor: id };
    }
    this.updatePickDOM();
  }

  // selection changes only touch classes (no re-render), so a following double-click still lands on the same tile
  updatePickDOM() {
    for (const t of this.root.querySelectorAll(".h3md-tile")) {
      const [k, ...rest] = t.dataset.media.split(":");
      t.classList.toggle("picked", this.pick.kind === k && this.pick.ids.has(rest.join(":")));
    }
    for (const el of this.root.querySelectorAll("[data-pickinfo]")) {
      const n = this.pick.kind === el.dataset.pickinfo ? this.pick.ids.size : 0;
      el.textContent = n > 1 ? `${n} selected – drag or double-click to add all` : "";
    }
  }

  renderTile(kind, item, idx) {
    const isRM = kind === "refmods";
    const pr = isRM ? this.rmInfos[item.file] : this.probes[item.file];
    const url = isRM ? null : viewURL(item.file);
    const usage = isRM ? RM.usage(this.plan, item.id) : M.mediaUsage(this.plan, kind, item.id);
    const missing = pr && pr.exists === false;
    const isPicked = () => this.pick.kind === kind && this.pick.ids.has(item.id);
    const picked = isPicked();
    const group = () => (isPicked() && this.pick.ids.size > 1 ? this.orderedPick(kind) : [item.id]);
    let vis = null;
    if (kind === "images") vis = url ? h("img", { class: "tv", src: url, loading: "lazy", draggable: "false" }) : h("div", { class: "tv ph" }, "?");
    else if (kind === "videos") {
      // static poster (same frame as before); a live <video> only while hovered (h3md_director_perf.js)
      if (url) vis = videoThumb(url);
      else vis = h("div", { class: "tv ph" }, "▶");
    } else if (isRM) {
      // no thumbnail: RefMod-coloured icon = slot label in the selected shot ("—" if not used there);
      // the filename is the tile's name line below; no library-order number
      const lab = this.rmLabels(this.selShot).find((l) => l.id === item.id);
      vis = h("div", { class: "tv rm" }, h("span", { class: "sl" }, lab && lab.core ? lab.core.slice(1, -1) : "—"));
    }
    const dur = kind === "videos" ? (item.duration ?? pr?.duration) : kind === "audios" ? pr?.duration : null;
    const fname = splitFile(item.file).filename;
    const token = `${KIND_LETTER[kind]}${idx + 1}`;
    const tip = [isRM ? fname : `${token} · ${fname}`, item.file !== fname ? item.file : null, `id: ${item.id}`,
      missing ? "⚠ missing file" : null,
      pr && pr.width ? `${pr.width}x${pr.height}` : null,
      dur != null ? fmtS(dur) : null,
      kind === "videos" ? (item.hasAudio === false ? "no audio" : "audio ✓") : null,
      isRM && pr ? (pr.exists === false ? `⚠ ${pr.error || "not found"}` : `RefMod ${pr.kind}${pr.concept_type ? " · " + pr.concept_type : ""}`) : null,
      usage.length ? `used in ${usage.map((i) => "#" + (i + 1)).join(", ")}` : "unused",
      "double-click: add to the open shot · drag: reorder, or drop on a shot · ctrl/shift-click or drag a box on the empty area: multi-select"].filter(Boolean).join("\n");
    const rename = (e) => {
      e.stopPropagation();
      const v = prompt(`Rename id '${item.id}' (used by the shots; the file is not renamed):`, item.id);
      if (v === null) return;
      try { if ((isRM ? RM.rename(this.plan, item.id, v) : M.renameMedia(this.plan, kind, item.id, v))) this.commit(); } catch (err) { alert(err.message); }
    };
    const remove = (e) => {
      e.stopPropagation();
      if (isRM) RM.remove(this.plan, item.id); else M.removeMedia(this.plan, kind, item.id); this.pick.ids.delete(item.id); this.commit();
    };
    const tile = h("div", { class: `h3md-tile${isRM ? " rmt" : ""}${usage.includes(this.selShot) ? " cur" : ""}${missing ? " missing" : ""}${picked ? " picked" : ""}`, draggable: "true", title: tip, "data-media": `${kind}:${item.id}` },
      isRM ? null : h("div", { class: "tk" }, token),   // RefMods: no library-order token
      h("div", { class: `mid${kind === "audios" ? " aud" : ""}` },
        vis,
        usage.length ? h("span", { class: "bdg use" }, usage.map((i) => "#" + (i + 1)).join(" ")) : null,
        dur != null ? h("span", { class: "bdg dur" }, fmtS(dur)) : null,
        kind === "videos" ? h("span", { class: `bdg snd${item.hasAudio === false ? " off" : ""}`, title: "hasAudio: the clip has a soundtrack (click to toggle; changes label numbering)",
          onclick: (e) => { e.stopPropagation(); item.hasAudio = item.hasAudio === false; this.commit(); } }, "♪") : null,
        kind === "audios" && url ? h("button", { class: "play", title: "play / stop", onclick: (e) => { e.stopPropagation(); this.togglePlay(e.currentTarget, url); } }, "▶") : null),
      h("div", { class: "nm" }, tailName(item.file)),
      h("div", { class: "acts" },
        h("button", { title: "rename id", onclick: rename }, "✎"),
        h("button", { title: "remove from plan (file stays on disk)", onclick: remove }, "✕")));
    if (kind === "videos" && url && vis) attachHoverPlay(tile, vis.parentElement, url);
    tile.addEventListener("click", (e) => {
      if (e.target.closest("button, .snd") || e.detail > 1) return;
      clearTimeout(this.collapseT);
      // plain click on a tile inside a multi-selection: wait, it may be the first half of a double-click
      if (!e.ctrlKey && !e.metaKey && !e.shiftKey && isPicked() && this.pick.ids.size > 1) {
        const ev = { ctrlKey: false, metaKey: false, shiftKey: false };
        this.collapseT = setTimeout(() => this.clickTile(ev, kind, item.id), 300);
        return;
      }
      this.clickTile(e, kind, item.id);
    });
    tile.addEventListener("dblclick", (e) => {
      if (e.target.closest("button, .snd")) return;
      e.preventDefault(); e.stopPropagation();
      clearTimeout(this.collapseT);
      this.addToShot(kind, group());
    });
    tile.addEventListener("dragstart", (e) => {
      clearTimeout(this.collapseT);
      const ids = group();
      e.dataTransfer.setData("application/x-h3md-ref", JSON.stringify({ kind, id: ids[0], ids }));
      e.dataTransfer.setData("text/plain", ids.join(","));
      e.dataTransfer.effectAllowed = "copyMove";
      this.libDrag = { kind, id: item.id, ids };
      requestAnimationFrame(() => { for (const t of this.root.querySelectorAll(".h3md-tile")) if (ids.some((x) => t.dataset.media === `${kind}:${x}`)) t.classList.add("dragging"); });
    });
    tile.addEventListener("dragend", () => {
      this.libDrag = null;
      for (const t of this.root.querySelectorAll(".h3md-tile.dragging, .h3md-tile.ins-a, .h3md-tile.ins-b")) t.classList.remove("dragging", "ins-a", "ins-b");
    });
    tile.addEventListener("dragover", (e) => {
      if (!this.libDrag || this.libDrag.kind !== kind || this.libDrag.ids.includes(item.id)) return;
      e.preventDefault(); e.stopPropagation();
      e.dataTransfer.dropEffect = "move";
      const r = tile.getBoundingClientRect();
      const after = e.clientX > r.left + r.width / 2;
      tile.classList.toggle("ins-a", after); tile.classList.toggle("ins-b", !after);
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("ins-a", "ins-b"));
    tile.addEventListener("drop", (e) => {
      if (!this.libDrag || this.libDrag.kind !== kind) return;
      e.preventDefault(); e.stopPropagation();
      const after = tile.classList.contains("ins-a");
      tile.classList.remove("ins-a", "ins-b");
      if (!this.libDrag.ids.includes(item.id)) this.moveMedia(kind, this.libDrag.ids, item.id, after);
    });
    return tile;
  }

  orderedPick(kind) {
    return (this.plan.media[kind] || []).map((x) => x.id).filter((id) => this.pick.ids.has(id));
  }

  // library order = order of plan.media[kind] (the backend looks media up by id, so order is display-only)
  moveMedia(kind, ids, targetId, after) {
    const arr = this.plan.media[kind] || [];
    const set = new Set(Array.isArray(ids) ? ids : [ids]);
    const moving = arr.filter((x) => x && set.has(x.id));
    if (!moving.length) return;
    const rest = arr.filter((x) => !(x && set.has(x.id)));
    let to = targetId == null ? rest.length : rest.findIndex((t) => t && t.id === targetId);
    if (to < 0) to = rest.length;
    else if (targetId != null && after) to += 1;
    rest.splice(to, 0, ...moving);
    this.plan.media[kind] = rest;
    this.libDrag = null;
    this.commit();
  }

  addToShot(kind, ids, shot = null, slot = null) {
    const n = this.plan.segments.length;
    if (!n) return;
    const i = Math.max(0, Math.min(n - 1, shot == null ? this.selShot : shot));
    if (!this.plan.segments[i] || typeof this.plan.segments[i] !== "object") return;
    for (const id of Array.isArray(ids) ? ids : [ids]) { if (kind === "refmods") RM.toggle(this.plan, i, id, true, slot); else M.toggleRef(this.plan, i, kind, id, true); }
    this.commit();
  }
  dropRefs(i, d) {
    let o; try { o = JSON.parse(d); } catch { return; }
    if (!o || !ALL_KINDS.includes(o.kind)) return;
    this.addToShot(o.kind, Array.isArray(o.ids) && o.ids.length ? o.ids : [o.id], i);
  }
  // images / videos receiver of a shot card: a RefMod dropped here runs as a picture / video (slot; numbered after the
  // shot's own pictures / videos unless placed with `at` from the RefMod row); any other media bubbles up to the card's
  // drop (unchanged)
  refModReceiver(el, i, slot) {
    el.addEventListener("drop", (e) => {
      let o; try { o = JSON.parse(e.dataTransfer?.getData("application/x-h3md-ref") || ""); } catch { return; }
      if (!o || o.kind !== "refmods") return;
      e.preventDefault(); e.stopPropagation();
      el.closest(".h3md-seg")?.classList.remove("drop");
      this.addToShot("refmods", Array.isArray(o.ids) && o.ids.length ? o.ids : [o.id], i, slot);
    });
    return el;
  }

  // drag-to-reorder inside a shot card: own MIME type SHOTREF (the card's library drop, the RefMod receivers, the
  // tabs and the library tiles ignore it); src = { i, row: "images" | "videos" | "audios" | "refmods", idx, slot }
  shotRefSource(handle, src) {
    handle.setAttribute("draggable", "true");
    handle.classList.add("h3md-sdrag");
    handle.addEventListener("dragstart", (e) => {
      e.stopPropagation();
      this.shotRefDrag = src;
      e.dataTransfer.setData(SHOTREF, JSON.stringify(src));
      e.dataTransfer.effectAllowed = "move";
      requestAnimationFrame(() => handle.classList.add("dragging"));
    });
    handle.addEventListener("dragend", () => { this.shotRefDrag = null; this.clearShotRefMarks(); });
    return handle;
  }
  clearShotRefMarks() {
    for (const t of this.root.querySelectorAll(".h3md-sdrag.dragging, .h3md-stgt.ins-a, .h3md-stgt.ins-b, .h3md-stgt-end.drop")) t.classList.remove("dragging", "ins-a", "ins-b", "drop");
  }
  // one entry as drop target: insert before / after it (left / right half, or upper / lower half when vertical)
  shotRefTarget(el, accept, drop, vertical = false) {
    el.classList.add("h3md-stgt");
    if (vertical) el.classList.add("v");
    const ok = (e) => this.shotRefDrag && e.dataTransfer?.types?.includes(SHOTREF) && accept(this.shotRefDrag);
    el.addEventListener("dragover", (e) => {
      if (!ok(e)) return;
      e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "move";
      const r = el.getBoundingClientRect();
      const after = vertical ? e.clientY > r.top + r.height / 2 : e.clientX > r.left + r.width / 2;
      el.classList.toggle("ins-a", after); el.classList.toggle("ins-b", !after);
    });
    el.addEventListener("dragleave", () => el.classList.remove("ins-a", "ins-b"));
    el.addEventListener("drop", (e) => {
      if (!ok(e)) return;
      e.preventDefault(); e.stopPropagation();
      const d = this.shotRefDrag, after = el.classList.contains("ins-a");
      this.shotRefDrag = null; this.clearShotRefMarks();
      drop(d, after);
    });
    return el;
  }
  // images row / videos box itself: a RefMod of that slot dropped there (not on an entry) = after all own refs
  shotRefEnd(el, i, slot) {
    el.classList.add("h3md-stgt-end");
    const ok = (e) => { const d = this.shotRefDrag; return d && d.i === i && d.row === "refmods" && d.slot === slot && e.dataTransfer?.types?.includes(SHOTREF); };
    el.addEventListener("dragover", (e) => { if (!ok(e)) return; e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "move"; el.classList.add("drop"); });
    el.addEventListener("dragleave", (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove("drop"); });
    el.addEventListener("drop", (e) => {
      if (!ok(e)) return;
      e.preventDefault(); e.stopPropagation();
      const d = this.shotRefDrag;
      this.shotRefDrag = null; this.clearShotRefMarks();
      this.setRefModAt(i, d.idx, null, 0);
    });
    return el;
  }
  // move entry `from` of s[kind] to insertion index `to` (counted before removal)
  moveShotRef(i, kind, from, to) {
    const s = this.plan.segments[i];
    if (kind === "videos") s.videos = (s.videos || []).map(M.videoRef);
    if (kind === "refmods") s.refmods = (s.refmods || []).map(rmEntry);
    const arr = s[kind];
    if (!Array.isArray(arr) || from < 0 || from >= arr.length) return;
    if (from < to) to -= 1;
    if (to === from) return;
    const [x] = arr.splice(from, 1);
    arr.splice(Math.max(0, Math.min(arr.length, to)), 0, x);
    this.commit();
  }
  // RefMod (index in s.refmods) dropped among its slot's own pictures / videos: at = insert position; null / >= own
  // count = after all own refs (no `at`, the old behaviour)
  setRefModAt(i, idx, at, nOwn) {
    const s = this.plan.segments[i];
    s.refmods = (s.refmods || []).map(rmEntry);
    const e = s.refmods[idx];
    if (!e) return;
    if (at == null || at >= nOwn) delete e.at; else e.at = at;
    this.commit();
  }

  // drag a shot tab (or the card title) onto another tab = reorder (one M.moveSegment step at a time, so overlaps/start frames follow)
  moveShot(from, to) {
    const p = this.plan;
    let k = from;
    while (k < to) k = M.moveSegment(p, k, 1);
    while (k > to) k = M.moveSegment(p, k, -1);
    M.clampSeams(p);
    this.selShot = k;
    this.commit();
  }

  // ---------------------------------------------------------------- prompt field with @-mention + token highlight
  shotLabels(i) {
    const s = this.plan && this.plan.segments[i];
    if (!s || typeof s !== "object") return [];
    return [...this.ownLabels(i), ...this.rmLabels(i)];
  }
  globalLabels() {
    // the global prompt is used by every shot, so a token means "the Nth picture of whichever shot runs it"
    const by = new Map();
    (this.plan.segments || []).forEach((s, i) => {
      for (const l of this.shotLabels(i)) {
        const g = by.get(l.tag) || { tag: l.tag, kind: l.kind, ids: [], per: [] };
        if (!g.ids.includes(l.id)) g.ids.push(l.id);
        g.per.push(`#${i + 1}:${l.id}`);
        by.set(l.tag, g);
      }
    });
    const order = { image: 0, video_soundtrack: 2, audio: 2, video: 1, refmod: 3 };
    return [...by.values()].sort((a, b) => (order[a.kind] - order[b.kind]) || a.tag.localeCompare(b.tag, undefined, { numeric: true }))
      .map((g) => ({ tag: g.tag, kind: g.kind, id: g.ids.slice(0, 3).join(" / ") + (g.ids.length > 3 ? ` +${g.ids.length - 3}` : ""), note: g.per.join(" ") }));
  }

  promptField({ rows, key, sizeKey, value, placeholder, labels, onText, onfocus, strict }) {
    const wrap = h("div", { class: "h3md-pf" });
    const back = h("div", { class: "h3md-pf-back", "aria-hidden": "true" });
    const ta = h("textarea", { rows, "data-key": key, value: value || "", placeholder, spellcheck: "false" });
    if (sizeKey) {
      const px = this.sizeOf(sizeKey);
      if (px) ta.style.height = `${px}px`;
      this.trackResize(ta, sizeKey);
    }
    const pop = h("div", { class: "h3md-at", style: { display: "none" } });
    wrap.append(back, ta, pop);
    const markKind = (tagWord) => TAG_KIND[tagWord] || "image";
    const sync = () => {
      back.style.width = ta.offsetWidth + "px";
      back.style.height = ta.offsetHeight + "px";
      back.style.paddingRight = (4 + Math.max(0, ta.offsetWidth - ta.clientWidth - 2)) + "px";
      back.scrollTop = ta.scrollTop;
    };
    const paint = () => {
      const t = ta.value;
      const ok = strict ? new Set(labels().map((l) => l.tag)) : null;
      let html = "", last = 0;
      for (const m of t.matchAll(TOKEN_RE)) {
        html += escHTML(t.slice(last, m.index));
        html += `<mark class="${ok && !ok.has(m[0]) ? "bad" : markKind(m[1])}">${escHTML(m[0])}</mark>`;
        last = m.index + m[0].length;
      }
      back.innerHTML = html + escHTML(t.slice(last)) + "\n";
      sync();
    };
    const changed = () => { onText(ta.value); paint(); };
    let st = null;
    const close = () => { st = null; pop.style.display = "none"; };
    const caretXY = (pos) => {
      const m = h("div", { class: "h3md-pf-back", style: { visibility: "hidden", width: back.style.width, paddingRight: back.style.paddingRight, height: "auto" } });
      m.innerHTML = escHTML(ta.value.slice(0, pos)) + '<span>\u200b</span>';
      wrap.append(m);
      const sp = m.lastChild;
      const xy = { x: sp.offsetLeft, y: sp.offsetTop + sp.offsetHeight - ta.scrollTop };
      m.remove();
      return xy;
    };
    const pick = (j) => {
      const l = st && st.items[j];
      if (!l) return;
      ta.setRangeText(l.tag, st.start, st.end, "end");
      close(); changed(); ta.focus();
    };
    const draw = () => {
      fill(pop, st.items.length ? st.items.map((l, j) => h("div", { class: `it${j === st.sel ? " sel" : ""}`, title: l.note || l.file || l.id,
        onmousedown: (e) => { e.preventDefault(); pick(j); } },
        h("span", { class: `h3md-label ${l.kind}${l.missing ? " missing" : ""}` }, l.tag), h("b", {}, l.id),
        l.note ? h("span", { class: "h3md-small" }, l.note) : null))
        : h("div", { class: "h3md-small", style: { padding: "3px 6px" } }, strict ? "no media in this shot yet (double-click a library item)" : "no media used by any shot yet"));
      pop.querySelector(".it.sel")?.scrollIntoView?.({ block: "nearest" });
    };
    const update = () => {
      const c = ta.selectionStart;
      if (c !== ta.selectionEnd || document.activeElement !== ta) return close();
      const m = String(ta.value.slice(0, c)).match(/@([^\s@<>]*)$/);
      if (!m) return close();
      const q = m[1].toLowerCase();
      const items = labels().filter((l) => !q || l.id.toLowerCase().includes(q) || l.tag.toLowerCase().includes(q) || String(l.file || "").toLowerCase().includes(q));
      st = { start: c - m[0].length, end: c, items, sel: Math.min(st ? st.sel : 0, Math.max(0, items.length - 1)) };
      draw();
      const xy = caretXY(st.start);
      pop.style.display = "block";
      pop.style.left = Math.max(0, Math.min(xy.x, ta.offsetWidth - 230)) + "px";
      pop.style.top = Math.min(xy.y + 2, ta.offsetHeight) + "px";
    };
    ta.addEventListener("input", () => { changed(); update(); });
    ta.addEventListener("scroll", sync);
    ta.addEventListener("blur", () => setTimeout(close, 120));
    ta.addEventListener("click", update);
    ta.addEventListener("keyup", (e) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) update(); });
    if (onfocus) ta.addEventListener("focus", onfocus);
    ta.addEventListener("keydown", (e) => {
      if (st) {
        const n = st.items.length;
        if (n && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); e.stopPropagation(); st.sel = (st.sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n; draw(); return; }
        if (n && (e.key === "Enter" || e.key === "Tab")) { e.preventDefault(); e.stopPropagation(); pick(st.sel); return; }
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
      }
      // Backspace / Delete next to (or inside) a label token removes the whole token
      if ((e.key === "Backspace" || e.key === "Delete") && ta.selectionStart === ta.selectionEnd && !e.ctrlKey && !e.altKey && !e.metaKey) {
        const c = ta.selectionStart;
        for (const m of ta.value.matchAll(TOKEN_RE)) {
          const a = m.index, b = a + m[0].length;
          if (e.key === "Backspace" ? (c > a && c <= b) : (c >= a && c < b)) {
            e.preventDefault(); e.stopPropagation();
            ta.setRangeText("", a, b, "start");
            changed();
            return;
          }
        }
      }
    });
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(sync).observe(ta);
    paint();
    requestAnimationFrame(sync);
    return wrap;
  }

  togglePlay(btn, url) {
    if (this.audioEl && !this.audioEl.paused) { this.audioEl.pause(); this.audioBtn && (this.audioBtn.textContent = "▶"); if (this.audioBtn === btn) return; }
    this.audioEl = new Audio(url); this.audioBtn = btn; btn.textContent = "■";
    this.audioEl.onended = () => { btn.textContent = "▶"; };
    this.audioEl.play().catch(() => { btn.textContent = "▶"; });
  }

  async addExisting(kind, file) {
    if (kind === "refmods") {
      RM.ensure(this.plan);
      const info = await refmodInfo(file);
      if (info) this.rmInfos[file] = info;
      if (!info || info.exists === false) { if (!confirm(`RefMod '${file}' could not be read${info && info.error ? `:\n${info.error}` : ""}\nAdd anyway?`)) return; }
      const extra = info && info.exists !== false ? { kind: info.kind, tokens: info.tokens } : {};
      M.addMedia(this.plan, kind, file, extra);
      this.commit();
      return;
    }
    const pr = await probe(file);
    if (pr) this.probes[file] = pr;
    if (pr && pr.exists === false && !confirm(`'${file}' was not found in the input folder. Add anyway?`)) return;
    const extra = {};
    if (kind === "videos") {
      if (pr && pr.duration != null) extra.duration = pr.duration;
      extra.hasAudio = pr && pr.hasAudio !== undefined ? !!pr.hasAudio : true;
    }
    M.addMedia(this.plan, kind, file, extra);
    this.commit();
  }

  async addUploads(kind, files) {
    if (kind === "images" || kind === "videos") {   // .safetensors dropped / uploaded here = RefMod
      const rm = files.filter((f) => /\.safetensors$/i.test(f.name));
      if (rm.length) { await this.addUploads("refmods", rm); files = files.filter((f) => !rm.includes(f)); }
    }
    for (const f of files) {
      try {
        if (kind === "refmods" && !/\.safetensors$/i.test(f.name)) throw new Error(`${f.name}: a RefMod must be a .safetensors file`);
        const name = kind === "refmods" ? await uploadRefMod(f) : await uploadFile(f);
        probeCache.delete(name);
        rmInfoCache.delete(name); rmListCache = null;
        delete fileListCache[kind];
        await this.addExisting(kind, name);
      } catch (err) {
        alert(`H3MD: ${err.message}`);
      }
    }
  }

  // ---------------------------------------------------------------- timeline + segments
  renderTimeline() {
    const p = this.plan;
    const sec = h("div", { class: "h3md-sec" });
    sec.append(h("h4", {}, "Timeline 时间轴",
      h("span", { class: "h3md-small", "data-tl-total": "1" }, ""),
      h("span", { style: { flex: 1 } }),
      h("button", { title: "Manual: replace the selected shot by separate shots (each gets its own prompt/refs card)", onclick: () => { this.splitDlg = !this.splitDlg; this.render(); } }, "Split shot…"),
      advanced() ? h("button", { title: "Append a hard-cut shot", onclick: () => { const at = M.addShotSeconds(p, 10, null, 0); this.selShot = at; this.commit(); } }, "+ Shot") : null));
    sec.classList.add("tl");
    if (this.splitDlg) sec.append(this.renderSplitDialog());   // in normal flow: under the title bar, above the track
    this.tlBox = h("div", {});
    const hint = "drag a shot onto the previous one = continuation (hatched overlap) · drag it to just touch = hard cut · drag the right edge = length";
    sec.append(this.tlBox, h("div", { class: "h3md-small h3md-tlhint", title: hint }, hint));
    this.drawTimeline();
    return sec;
  }

  drawTimeline() {
    const p = this.plan;
    const lay = M.layout(p);
    const total = Math.max(1, lay.length ? Math.max(...lay.map((l) => l.endFrame)) : 1);
    const span = this.dragSpan || Math.max(total * 1.12, total + 48);
    const pct = (f) => `${(f / span) * 100}%`;
    const colors = ["#3a6ea5", "#5a8a3a", "#9a5a2a", "#7a4a9a", "#2a8a8a", "#9a3a5a"];
    const bar = h("div", { class: "h3md-tl2", "data-tl": "1" });
    const step = M.rulerStep(span, (this.tlBox && this.tlBox.clientWidth) || 900);
    for (let t = 0; t * M.FPS <= span; t += step) bar.append(h("div", { class: "tick", style: { left: pct(t * M.FPS) } }, `${t}s`));
    const sov = M.subOverlapOf(p);
    lay.forEach((l, i) => {
      const subs = M.shotSubLengths(p, i);
      const block = h("div", { class: `sb${i === 0 ? " first" : ""}${i === this.selShot ? " sel" : ""}${this.drag && this.drag.i === i ? " drag" : ""}`, "data-shot": i,
        style: { left: pct(l.startFrame), width: pct(l.length), top: i % 2 ? "48px" : "18px", background: colors[i % colors.length] },
        title: `Shot ${i + 1}: frames ${l.startFrame}-${l.endFrame} (${fmtS(l.startFrame / M.FPS)}–${fmtS(l.endFrame / M.FPS)}), ${fmtF(l.length)}` +
          (subs.length > 1 ? `\nauto sub-segments: ${subs.join(" + ")} (overlap ${sov}f)` : "") },
        `Shot ${i + 1} · ${fmtS(l.length / M.FPS)}${i > 0 && l.overlapIn ? ` (${fmtS((l.length - l.overlapIn) / M.FPS)} new)` : ""}${M.shotLengthIsAuto(p, i) ? " · ▶vid" : ""}${subs.length > 1 ? ` · ×${subs.length}` : ""}`);
      if (M.shotLengthIsAuto(p, i)) block.title += `\nlength follows the duration of <Video 1> of this shot${l.overlapIn ? " plus the overlap" : ""} (rounded up to 5+17n): ${l.length} f`;
      if (i > 0 && l.overlapIn) block.title += `\nnew content ${l.length - l.overlapIn} f = ${fmtS((l.length - l.overlapIn) / M.FPS)} (after the ${l.overlapIn} f overlap)`;
      if (subs.length > 1) {
        let off = 0;
        subs.forEach((sl, j) => {
          if (j) {
            off += subs[j - 1] - sov;
            block.append(h("div", { class: "so", style: { left: `${(off / l.length) * 100}%`, width: `${(sov / l.length) * 100}%` } }));
            block.append(h("div", { class: "sm", "data-sub": `${i}.${j}`, style: { left: `${(off / l.length) * 100}%` } }));
          }
        });
      }
      const rh = h("div", { class: "rh", title: M.shotLengthIsAuto(p, i) ? "length follows this shot's reference video (edit its used length in the shot settings: length / sec)" : "drag: shot length (nearest 5+17n frames)" });
      block.append(rh);
      block.addEventListener("pointerdown", (e) => this.startDrag(e, i, e.target === rh ? "resize" : "move", bar, span));
      bar.append(block);
      if (i > 0) {
        const seam = M.seamOf(p, i);
        if (seam.kind === "continue") {
          bar.append(h("div", { class: "ov", style: { left: pct(l.startFrame), width: pct(seam.frames) } }));
          bar.append(h("div", { class: "seam c", "data-seam": i, style: { left: pct(l.startFrame + seam.frames / 2) } }, `continue ${seam.frames}f`));
        } else {
          bar.append(h("div", { class: "cut", style: { left: pct(l.startFrame) } }));
          bar.append(h("div", { class: "seam x", "data-seam": i, style: { left: pct(l.startFrame) } }, "cut"));
        }
      }
    });
    fill(this.tlBox, bar);
    const tot = this.root.querySelector("[data-tl-total]");
    if (tot) tot.textContent = `${lay.length} shot(s) · ${fmtF(total)}`;
  }

  startDrag(e, i, kind, bar, span) {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    this.selShot = i;
    if (kind === "move" && i === 0) { this.drawTimeline(); this.focusCard(i); return; }
    const lay = M.layout(this.plan);
    const pxPerFrame = bar.getBoundingClientRect().width / span;
    this.dragSpan = span;
    this.drag = { i, kind, x0: e.clientX, start0: lay[i].startFrame, end0: lay[i].endFrame, moved: false };
    const target = e.currentTarget;
    try { target.setPointerCapture(e.pointerId); } catch { /* synthetic events */ }
    const onMove = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const df = (ev.clientX - this.drag.x0) / pxPerFrame;
      if (Math.abs(ev.clientX - this.drag.x0) > 2) this.drag.moved = true;
      if (!this.drag.moved) return;     // a click (or 1-2 px jitter) never edits the plan
      if (this.drag.kind === "move") M.dragShotTo(this.plan, i, this.drag.start0 + df);
      else M.resizeShotTo(this.plan, i, this.drag.end0 + df);
      this.drawTimeline();
    };
    const onUp = (ev) => {
      ev.stopPropagation();
      window.removeEventListener("pointermove", onMove, true);
      window.removeEventListener("pointerup", onUp, true);
      const moved = this.drag && this.drag.moved;
      this.drag = null; this.dragSpan = null;
      if (moved) this.commit(); else { this.drawTimeline(); this.focusCard(i); }
    };
    window.addEventListener("pointermove", onMove, true);
    window.addEventListener("pointerup", onUp, true);
    this.drawTimeline();
  }

  focusCard(i) {
    // timeline click selects that shot's tab (one shared settings panel)
    if (!this.root.querySelector(`[data-seg-card="${i}"]`)) { this.selShot = i; this.render(); }
    this.root.querySelector(`[data-seg-card="${i}"]`)?.scrollIntoView({ block: "nearest" });
  }

  // H3MD splitauto (2026-10-27): the yellow auto-split result of shot i (this.as from /h3md/autosplit, the same data the
  // shot tab shows) -> { parts, overlap } or { note } (no split to recommend); null = not ready for the current plan yet.
  autoSplitParts(i) {
    const a = this.as;
    const sz = this.runSize();
    if (!a || this.asKey !== `${sz.w}x${sz.h}|${this.lastText || ""}`) return null;
    if (!a.ok) return { note: `auto-split estimate unavailable: ${a.error || "?"}` };
    if (!a.budget) return { note: "plan_json has \"autoSplit\": false: no recommended split (use custom)" };
    const x = (a.shots || []).find((s) => s && s.shot === i);
    if (!x || !Array.isArray(x.parts)) return null;
    if (x.status !== "split" || x.parts.length < 2) return { note: "the auto-split runs this shot as one part: no recommended split (use custom)" };
    return { parts: x.parts.map(Number), overlap: Number(x.overlap) };
  }

  renderSplitDialog() {
    const p = this.plan;
    const i = Math.min(this.selShot, p.segments.length - 1);
    const len = M.segmentLength(p.segments[i]) ?? 0;
    const st = this.splitState || (this.splitState = { mode: "recommended", seconds: "", max: M.DEFAULT_MAX_SEGMENT, ov: M.DEFAULT_SUB_OVERLAP, custom: "", error: "" });
    // H3MD splitauto (2026-10-27): "recommended" = exactly the yellow auto-split parts (no 362 / fixed fallback); until
    // that estimate is ready nothing is recommended (the dialog re-renders when it arrives). max length / overlap show
    // the auto-split's values read-only. Custom mode is unchanged.
    const custom = st.mode === "custom";
    const asp = custom ? null : this.autoSplitParts(i);
    const asOk = !!(asp && asp.parts);
    const maxEff = custom ? st.max : (asOk ? Math.max(...asp.parts) : "");
    const ovEff = custom || !asOk ? st.ov : asp.overlap;
    const opts = { mode: st.mode, seconds: st.seconds, max: maxEff, ov: ovEff, custom: st.custom,
      parts: custom ? undefined : (asOk ? asp.parts : null), partsNote: asp && asp.note ? asp.note : "" };
    const pre = M.splitPreview(p, i, opts);
    const err = st.error || pre.error || "";
    const go = () => {
      try {
        const n = M.splitShotDialog(p, i, opts);
        this.splitDlg = false; this.splitState = null;
        this.selShot = i;
        this.commit();
        console.info(`[H3MD] shot ${i + 1} split into ${n} shots`);
      } catch (e2) { st.error = e2.message; this.render(); }
    };
    const set = (patch) => { Object.assign(st, patch, { error: "" }); this.render(); };
    const ovOpts = []; for (let k = 5; k < M.snapLength(asOk ? maxEff : st.max); k += 17) ovOpts.push(k);
    return h("div", { class: "h3md-dlg", "data-split-dlg": "1" },
      h("div", { class: "h3md-row" }, h("b", {}, `Split shot ${i + 1} into separate shots`)),
      h("div", { class: "h3md-row" },
        h("label", {}, h("input", { type: "radio", name: "h3md-split-mode", "data-key": "split-mode-rec", checked: custom ? null : "checked", onchange: () => set({ mode: "recommended" }) }), "recommended"),
        h("label", {}, h("input", { type: "radio", name: "h3md-split-mode", "data-key": "split-mode-custom", checked: custom ? "checked" : null, onchange: () => set({ mode: "custom" }) }), "custom")),
      h("div", { class: "h3md-row" },
        "total seconds", h("input", { type: "number", class: "n", min: 1, step: 0.5, value: st.seconds, placeholder: ((pre.target ?? len) / M.FPS).toFixed(2), "data-key": "split-sec",
          onchange: (e) => set({ seconds: e.target.value }) }),
        custom ? null : ["max length", h("input", { type: "number", class: "n", min: 22, step: 17, value: maxEff, disabled: true, "data-key": "split-max", title: "follows the auto-split (the yellow line under the shot tab)",
          onchange: (e) => set({ max: M.snapLength(Number(e.target.value)) }) }), "f  "],
        "overlap", h("select", { "data-key": "split-ov", disabled: custom ? null : true, onchange: (e) => set({ ov: Number(e.target.value) }) },
          ovOpts.map((k) => h("option", { value: k, selected: k === ovEff ? "selected" : null }, `${k}f`)))),
      custom ? h("div", { class: "h3md-row" },
        "segments (s)", h("input", { type: "text", value: st.custom, placeholder: "10, 15", "data-key": "split-custom", style: { width: "160px" },
          title: "seconds per segment, comma separated; each -> rounded up to 5+17n frames (overlap included); the rest of the shot becomes one more segment",
          onchange: (e) => set({ custom: e.target.value }) })) : null,
      h("div", { class: "h3md-row" },
        h("span", { "data-split-preview": "1" }, `${pre.target != null ? fmtF(pre.target) : "?"} → ${pre.lengths ? pre.lengths.join(" + ") + ` (overlap ${ovEff}f)` : "?"}`),
        h("button", { "data-key": "split-go", onclick: go, disabled: err ? true : null }, "Split"), h("button", { onclick: () => { this.splitDlg = false; this.render(); } }, "Cancel")),
      err ? h("div", { class: "h3md-row h3md-bad", "data-split-error": "1" }, err) : null);
  }

  // Shots as a horizontal tab row + ONE settings panel for the selected tab (stored plan format unchanged).
  selectShot(i) {
    const n = (this.plan && this.plan.segments.length) || 1;
    this.selShot = Math.max(0, Math.min(n - 1, i));
    this.render();
  }

  renderSegments() {
    const p = this.plan;
    const n = p.segments.length;
    if (this.selShot >= n) this.selShot = n - 1;
    if (this.selShot < 0) this.selShot = 0;
    const lay = M.layout(p);
    const v = this.val || this.validation();
    const tabs = h("div", { class: "h3md-tabs", "data-tabs": "1" });
    p.segments.forEach((s, i) => {
      const len = M.segmentLength(s) ?? 0;
      const auto = M.shotLengthIsAuto(p, i);
      const seam = M.seamLabel(p, i);
      const bad = v.errors.some((e) => e.seg === i);
      const nf = M.shotNewFrames(p, i);
      const tab = h("div", { class: `h3md-tab${i === this.selShot ? " sel" : ""}${bad ? " err" : ""}`, "data-tab": i, draggable: n > 1 ? "true" : null,
        title: `Shot ${i + 1}: generated ${fmtF(len)} = ${fmtS(len / M.FPS)}${nf !== len ? ` · new content ${fmtF(nf)} = ${fmtS(nf / M.FPS)} (after the ${len - nf}f overlap)` : ""}${auto ? " (length from its reference video, rounded up to 5+17n)" : ""}${seam ? ` · ${seam}` : ""}`,
        onclick: () => this.selectShot(i) },
        `Shot ${i + 1}`, h("span", { class: "tl" }, `${nf !== len ? `+${fmtS(nf / M.FPS)} new · ` : `${fmtS(len / M.FPS)} · `}${len}f${auto ? " ▶" : ""}${seam === "cut" ? " ✂" : seam ? " ⇢" : ""}`),
        h("span", { class: "as", "data-as-tab": i }));
      tab.addEventListener("dragstart", (e) => {
        this.tabDrag = i;
        e.dataTransfer.setData("application/x-h3md-shot", String(i));
        e.dataTransfer.effectAllowed = "move";
        requestAnimationFrame(() => tab.classList.add("dragging"));
      });
      tab.addEventListener("dragend", () => { this.tabDrag = null; for (const t of tabs.children) t.classList.remove("dragging", "ins-a", "ins-b"); });
      tab.addEventListener("dragover", (e) => {
        if (this.tabDrag != null) {
          if (this.tabDrag === i) return;
          e.preventDefault(); e.dataTransfer.dropEffect = "move";
          const r = tab.getBoundingClientRect();
          const after = e.clientX > r.left + r.width / 2;
          tab.classList.toggle("ins-a", after); tab.classList.toggle("ins-b", !after);
          return;
        }
        if (e.dataTransfer?.types?.includes("application/x-h3md-ref")) { e.preventDefault(); tab.classList.add("drop"); }
      });
      tab.addEventListener("dragleave", () => tab.classList.remove("drop", "ins-a", "ins-b"));
      tab.addEventListener("drop", (e) => {
        if (this.tabDrag != null) {
          e.preventDefault(); e.stopPropagation();
          const from = this.tabDrag, after = tab.classList.contains("ins-a");
          tab.classList.remove("ins-a", "ins-b");
          this.tabDrag = null;
          let to = after ? i + 1 : i;
          if (from < to) to -= 1;
          if (to !== from) this.moveShot(from, to);
          return;
        }
        const d = e.dataTransfer?.getData("application/x-h3md-ref");
        tab.classList.remove("drop");
        if (!d) return;
        e.preventDefault(); e.stopPropagation();
        this.selShot = i; this.dropRefs(i, d);
      });
      tabs.append(tab);
    });
    tabs.append(h("div", { class: "h3md-tab add", title: "add a shot after the selected one", "data-tab-add": "1",
      onclick: () => { this.selShot = M.addSegment(p, this.selShot); this.commit(); } }, "+"));
    const wrap = h("div", { class: "h3md-sec h3md-shotsec" });
    wrap.append(h("h4", {}, "Shots 分镜", h("span", { class: "h3md-small" }, `${n} · click a tab to edit it · drag tabs to reorder`),
      h("button", { class: "clr", "data-clear": "segments", title: "clear the shots area: all shots → one empty shot (media library is kept)",
        onclick: () => { this.keepGlobal(() => M.clearSegments(p)); this.selShot = 0; this.subOpen = {}; this.commit(); } }, "Clear 清空分镜")),
      h("div", { class: "h3md-small h3md-note", "data-prompt-note": "1" }, PROMPT_NOTE),
      h("div", { class: "h3md-small h3md-note", "data-len-note": "1" }, "分镜长度按你设定的来（17n+5 帧）。运行时 Director 会按显存预算估算每个分镜：超出预算的分镜会自动拆成几段等长的续接段（重叠 39 帧），参考视频、锁定音轨跟着走，不用手动 Split。没有参考视频和 RefMod 的分镜按低清阶段估算（高清阶段会自动分块），一般不会被拆。每个分镜标签下方显示估算结果：绿色 = 一段跑完，黄色 = 将自动分成 N 段，红色 = 超预算但无法拆分。预算 = 显卡显存 − ComfyUI 启动保留（--reserve-vram / --vram-headroom），再留 15% 余量。参考视频分辨率选 auto 时：开了深度图的参考视频固定用最低档，其他参考视频保持高清，必要时多分几段（plan_json 里写 \"autoSplitPrefer\": \"fewer\" 则改为优先少分段）。plan_json 里写 \"autoSplit\": false 可关闭。"), tabs);
    const card = this.renderSegment(p.segments[this.selShot], this.selShot, lay);
    card.classList.add("tabbed");
    wrap.append(card, this.renderGlobal());
    requestAnimationFrame(() => tabs.querySelector(".h3md-tab.sel")?.scrollIntoView?.({ block: "nearest", inline: "nearest" }));
    return wrap;
  }

  // "= 16.5s generated · new 15.58s (374f) after the 22f overlap"
  // ---- H3MD autosplit preview: the Director's own estimate, fetched from /h3md/autosplit (debounced)
  // width/height as the Director will run: its own widget, else the node linked into it (a widget named
  // width/height on that node, or a JSON widget carrying width/height, e.g. an aspect-ratio picker), else the plan
  runSize() {
    const out = { w: Number(this.widthW?.value || 0), h: Number(this.heightW?.value || 0) };
    const g = this.node.graph || app.graph;
    for (const key of ["w", "h"]) {
      if (out[key] > 0) continue;
      const name = key === "w" ? "width" : "height";
      const inp = (this.node.inputs || []).find((x) => x && x.name === name);
      const link = inp && inp.link != null ? (g?.links?.[inp.link] || g?.links?.get?.(inp.link) || g?.getLink?.(inp.link)) : null;
      const src = link ? g?.getNodeById?.(link.origin_id) : null;
      if (!src) continue;
      const ws = src.widgets || [];
      const direct = ws.find((w) => w && w.name === name && Number(w.value) > 0);
      if (direct) { out[key] = Number(direct.value); continue; }
      for (const w of ws) {
        if (typeof w?.value !== "string" || !w.value.trim().startsWith("{")) continue;
        try { const j = JSON.parse(w.value); if (Number(j[name]) > 0) { out[key] = Number(j[name]); break; } } catch { /* not JSON */ }
      }
    }
    return out;
  }
  scheduleAutoSplit() {
    if (!this.plan) return;
    const sz = this.runSize();
    const key = `${sz.w}x${sz.h}|${this.lastText || ""}`;
    if (key === this.asKey && this.as) { this.applyAutoSplitUI(); return; }
    clearTimeout(this.asTimer);
    this.asTimer = setTimeout(async () => {
      try {
        const r = await api.fetchApi("/h3md/autosplit", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan_json: this.lastText || "", width: sz.w, height: sz.h }) });
        const j = await r.json();
        this.as = j; this.asKey = key;
      } catch (e) {
        this.as = { ok: false, error: String(e) }; this.asKey = key;
      }
      this.applyAutoSplitUI();
      if (this.splitDlg) this.render();   // H3MD splitauto: an open Split dialog fills its recommendation from the new estimate
    }, 600);
  }
  applyAutoSplitUI() {
    const a = this.as;
    const pill = this.root.querySelector("[data-as-budget]");
    if (pill) {
      if (!a) { pill.textContent = ""; pill.title = ""; }
      else if (!a.ok) { pill.textContent = "budget ?"; pill.title = `autosplit preview unavailable: ${a.error || "?"}`; }
      else if (!a.budget) { pill.textContent = "autoSplit off"; pill.title = "plan_json has \"autoSplit\": false"; }
      else {
        const b = a.budgetInfo || {};
        pill.textContent = `预算 ${(a.budget / 1000).toFixed(0)}k tok/段 · ${a.width}x${a.height} → ${a.segments} 段`;
        pill.title = `token budget per generated segment: ${a.budget.toLocaleString()}\n` +
          `available VRAM ${b.avail_gib ?? "?"} GiB${b.reserve_gib != null ? ` (after ComfyUI's reserve ${b.reserve_gib} GiB: --reserve-vram / --vram-headroom)` : ""}, safety ${b.safety ?? "?"}\n` +
          `the Director runs ${a.segments} segment(s), ${a.totalFrames} frames (${(a.totalFrames / M.FPS).toFixed(2)}s)`;
      }
    }
    const byShot = new Map(((a && a.ok && a.shots) || []).map((x) => [x.shot, x]));
    const txt = (x) => {
      const pct = Math.round(100 * x.tokens / x.budget);
      if (x.status === "split") return { cls: "h3md-as-split", tab: `⚡${x.parts.length} 段`, line:
        `预计 ${x.tokens.toLocaleString()} token，超出预算 ${x.budget.toLocaleString()}（${pct}%）→ 运行时自动分成 ${x.parts.length} 段：${x.parts.join(" + ")} 帧（重叠 ${x.overlap} 帧，每段最多 ${Math.max(...x.partTokens).toLocaleString()} token；切点在本分镜 ${cutTimes(x)}，已按参考视频动作避开大动作，运行时完全一致）` };
      if (x.status === "over") return { cls: "h3md-as-over", tab: `✗ ${pct}%`, line:
        `预计 ${x.tokens.toLocaleString()} token，超出预算 ${x.budget.toLocaleString()}（${pct}%），但无法拆分（单段最长只能 ${x.maxPart || 0} 帧）：请降低分辨率或减少参考素材` };
      return { cls: "h3md-as-ok", tab: `${pct}%`, line: `预计 ${x.tokens.toLocaleString()} token / 预算 ${x.budget.toLocaleString()}（${pct}%），一段跑完` };
    };
    for (const el of this.root.querySelectorAll("[data-as-tab], [data-as-card]")) {
      const i = Number(el.dataset.asTab ?? el.dataset.asCard);
      const x = byShot.get(i);
      el.classList.remove("h3md-as-ok", "h3md-as-split", "h3md-as-over");
      if (!x) { el.textContent = ""; el.title = ""; continue; }
      const t = txt(x);
      el.classList.add(t.cls);
      el.textContent = el.dataset.asTab != null ? t.tab : t.line;
      el.title = t.line;
      // 2026-10-27: a split shot whose prompt is time-coded ([Shot 2] At 00:08 …) - every auto part gets the whole
      // prompt from 0 s, so later parts' timings would be off: suggest a manual Split with one prompt per part
      if (x.status === "split" && (x.light || TIMECODED.test(String(this.plan?.segments?.[i]?.prompt || "")))) {
        const w = h("span", { class: "h3md-as-tc", title: AS_TC_WARN }, el.dataset.asTab != null ? " ⚠" : AS_TC_WARN);
        if (el.dataset.asTab == null) el.append(h("br"));
        el.append(w);
        el.title = t.line + "\n" + AS_TC_WARN;
      }
    }
  }

  lengthInfo(p, i, len, autoLen) {
    const nf = M.shotNewFrames(p, i);
    let t = `= ${fmtS(len / M.FPS)}${nf !== len ? " generated" : ""}`;
    if (nf !== len) t += ` · new ${fmtS(nf / M.FPS)} (${nf}f) after the ${len - nf}f overlap`;
    if (autoLen) {
      const g = M.autoShotGeometry(p, i);
      t += ` · auto: ${g && i > 0 && g.overlap > 0 ? `overlap + ` : ""}<Video 1> ${fmtS(g ? g.content / M.FPS : 0)}`;
    }
    return t;
  }

  renderSegment(s, i, lay) {
    const p = this.plan;
    const l = lay[i];
    const n = p.segments.length;
    if (!s || typeof s !== "object") {
      return h("div", { class: "h3md-seg" }, h("span", { class: "h3md-bad" }, `segment ${i + 1} is not an object`),
        h("button", { onclick: () => { M.deleteSegment(p, i); this.commit(); } }, "delete"));
    }
    const card = h("div", { class: "h3md-seg", "data-seg-card": i });
    card.addEventListener("dragover", (e) => { if (e.dataTransfer?.types?.includes("application/x-h3md-ref")) { e.preventDefault(); card.classList.add("drop"); } });
    card.addEventListener("dragleave", () => card.classList.remove("drop"));
    card.addEventListener("drop", (e) => {
      const d = e.dataTransfer?.getData("application/x-h3md-ref");
      card.classList.remove("drop");
      if (!d) return;
      e.preventDefault(); e.stopPropagation();
      this.dropRefs(i, d);
    });
    const prev = i > 0 ? lay[i - 1] : null;
    const len = M.segmentLength(s) ?? 0;
    // header
    const subs = M.shotSubLengths(p, i);
    const seamTxt = M.seamLabel(p, i);
    const head = h("h4", { draggable: n > 1 ? "true" : null, title: n > 1 ? "drag onto a shot tab to reorder (like dragging the tab)" : null },
      `Shot ${i + 1}`,
      seamTxt ? h("span", { class: seamTxt === "cut" ? "h3md-bad" : "h3md-warn", title: "set by the overlap on the timeline" }, seamTxt) : null,
      h("span", { class: "h3md-small" }, `frames ${l.startFrame}–${l.endFrame} · ${fmtS(l.startFrame / M.FPS)}–${fmtS(l.endFrame / M.FPS)}`),
      h("span", { style: { flex: 1 } }),
      h("button", { title: "switch to the previous shot (does not reorder)", disabled: i === 0 ? true : null, onclick: () => this.selectShot(i - 1) }, "◂"),
      h("button", { title: "switch to the next shot (does not reorder)", disabled: i === n - 1 ? true : null, onclick: () => this.selectShot(i + 1) }, "▸"),
      h("button", { title: "duplicate", onclick: () => { this.selShot = M.duplicateSegment(p, i); this.commit(); } }, "⧉"),
      h("button", { title: "insert a new shot after this one", onclick: () => { this.selShot = M.addSegment(p, i); this.commit(); } }, "+"),
      h("button", { title: "delete shot", disabled: n === 1 ? true : null, onclick: () => { M.deleteSegment(p, i); this.selShot = Math.max(0, Math.min(i, p.segments.length - 1)); this.commit(); } }, "✕"));
    // card title = drag handle with the tab's payload (only the selected card is rendered: drop it on a tab)
    head.addEventListener("dragstart", (e) => {
      if (n < 2) return;
      this.tabDrag = i;
      e.dataTransfer.setData("application/x-h3md-shot", String(i));
      e.dataTransfer.effectAllowed = "move";
    });
    head.addEventListener("dragend", () => { this.tabDrag = null; for (const t of this.root.querySelectorAll(".h3md-tab.dragging, .h3md-tab.ins-a, .h3md-tab.ins-b")) t.classList.remove("dragging", "ins-a", "ins-b"); });
    card.append(head);
    // length / overlap / audio mode
    // every continued shot offers the same fixed steps 0 / 22 / 39; the select shows the user's choice
    // (overlapRequest when shrink-to-fit lowered the stored overlapIn), an old non-standard value is kept listed
    const ovOptions = M.overlapSteps(p, i);
    const curOv = Math.trunc(M.num(s.overlapIn || 0, 0));
    const reqOv = M.requestedOverlap(s, i);
    const legacy = !ovOptions.includes(reqOv);
    if (legacy) ovOptions.push(reqOv);
    const ovLabel = (k) => (k === 0 ? "0 (cut)" : `${k}f · ${(k / M.FPS).toFixed(2)}s · ${M.videoTokens(k)} tok`)
      + (k === reqOv && legacy ? " (old value)" : "");
    const ovNote = reqOv !== curOv ? `→ ${curOv}f` : "";
    const autoLen = M.shotLengthIsAuto(p, i);
    const lenTitle = autoLen ? "auto: follows the used length of this shot's first reference video (sec; default the whole clip), rounded up to 5+17n; read-only" : "frames, 5+17n (snapped to the nearest on change)";
    const used = autoLen ? M.shotUsedSeconds(p, i) : null;
    card.append(h("div", { class: "h3md-row one" },
      "length", autoLen
        ? h("span", { class: "h3md-small", title: lenTitle }, fmtF(len))
        : h("input", { type: "number", class: "n", min: 5, step: 17, value: len, "data-key": `len-${i}`, title: lenTitle,
          onchange: (e) => { const v = Number(e.target.value); s.length = M.isAlignedLength(v) ? v : M.snapShotLength(v); if ("endFrame" in s) delete s.endFrame; M.clampSeams(p); this.commit(); } }),
      autoLen ? null : "sec", autoLen
        ? null
        : h("input", { type: "number", class: "n", min: 0.2, step: 0.5, value: (len / M.FPS).toFixed(2), "data-key": `sec-${i}`, title: "seconds → nearest 5+17n frames",
          onchange: (e) => { s.length = M.snapShotLength(Number(e.target.value) * M.FPS); if ("endFrame" in s) delete s.endFrame; M.clampSeams(p); this.commit(); } }),
      i > 0 ? ["overlapIn", h("select", { "data-key": `ov-${i}`, title: "frames copied from the previous shot's FINAL latent: 0 (hard cut), 22 or 39 — the same steps for every shot",
        onchange: (e) => { s.overlapIn = Number(e.target.value); delete s.overlapRequest; M.applyVideoLengths(p); this.commit(); } },
        ovOptions.map((k) => h("option", { value: k, selected: k === reqOv ? "selected" : null }, ovLabel(k)))),
        ovNote ? h("span", { class: "h3md-small" }, ovNote) : null] : null,
      (() => { const t = this.lengthInfo(p, i, len, autoLen); return h("span", { class: "h3md-small ell", "data-len-info": i, title: t }, t); })()));
    card.append(h("div", { class: "h3md-small h3md-as-line", "data-as-card": i }));
    // reference assignment: one fixed-height box (scrolls inside, user-resizable, height kept in node properties)
    const refs = h("div", { class: "h3md-refs", "data-refs": i }, this.renderAssign(s, i, "images"), this.renderVideoRefs(s, i), this.renderAudioRefs(s, i), this.renderRefModRefs(s, i));
    const rpx = this.sizeOf("shot-refs");
    if (rpx) refs.style.height = `${rpx}px`;
    this.trackResize(refs, "shot-refs");
    card.append(refs);
    // labels + prompt
    const ta = this.promptField({ rows: 6, key: `prompt-${i}`, sizeKey: "prompt", value: s.prompt || "", strict: true,
      placeholder: (p.globalPrompt ? "shot prompt (the global prompt below is prepended) " : "shot prompt ") + "– type @ to insert a reference label",
      labels: () => this.shotLabels(i),
      onText: (t) => { s.prompt = t; this.commit(false); },
      onfocus: () => { this.focusPrompt = i; } });
    card.append(
      h("div", { class: "h3md-labrow" }, h("span", { class: "h3md-small" }, "references 引用:"), h("span", { "data-seg-labels": i })),
      ta);
    const contOn = true;
    card.append(...this.renderSubs(s, i, subs, contOn && (curOv > 0 || subs.length > 1)));
    return card;
  }

  renderSubs(s, i, subs, contNote) {
    const p = this.plan;
    const open = (this.subOpen || (this.subOpen = {}))[i] && subs.length > 1;
    const sov = M.subOverlapOf(p);
    const note = contNote ? "continuity sentence is prepended to every continued part (Director: continuity_prompt)" : "";
    const line = h("div", { class: "h3md-subline", "data-subs": i },
      subs.length > 1 ? [h("button", { onclick: () => { this.subOpen[i] = !open; this.render(); } }, open ? "▾" : "▸"),
        h("b", {}, `auto: ${subs.length} sub-segments`)] : null,
      (() => { const t = [subs.length > 1 ? `${subs.join(" + ")} frames, overlap ${sov}f · part prompts ▸` : "", note].filter(Boolean).join(" · ");
        return h("span", { class: "h3md-small ell", title: t }, t); })());
    if (!open) return [line];
    const box = h("div", { class: "h3md-pop", "data-subs-pop": i, style: { left: "6px", right: "6px", bottom: "30px", maxHeight: "280px" } },
      h("div", { class: "h3md-row one" }, h("b", {}, `Shot ${i + 1}: part prompts`), h("span", { style: { flex: 1 } }),
        h("button", { onclick: () => { this.subOpen[i] = false; this.render(); } }, "✕")));
    const prompts = Array.isArray(s.subPrompts) ? s.subPrompts : [];
    subs.forEach((sl, j) => {
      box.append(h("div", { class: "h3md-small" }, `part ${j + 1}/${subs.length} · ${fmtF(sl)}${j ? ` · continues ${sov}f` : ""}`),
        this.promptField({ rows: 2, key: `subp-${i}-${j}`, sizeKey: "subprompt", value: prompts[j] || "", placeholder: "(empty: uses the shot prompt) – @ inserts a label", strict: true,
          labels: () => this.shotLabels(i),
          onText: (txt) => {
            const arr = Array.isArray(s.subPrompts) ? s.subPrompts.slice() : [];
            while (arr.length <= j) arr.push("");
            arr[j] = txt;
            while (arr.length && !arr[arr.length - 1]) arr.pop();
            if (arr.length) s.subPrompts = arr; else delete s.subPrompts;
            this.commit(false);
          } }));
    });
    return [line, box];
  }

  labelChips(i) {
    const seg = this.val && this.val.segments[i];
    const s = this.plan.segments[i];
    const labels = [...this.ownLabels(i, seg ? seg.labels : M.computeLabels(s || {}, M.mediaMaps(this.plan))), ...this.rmLabels(i)];
    if (!labels.length) return [h("span", { class: "h3md-ph" }, "no references yet")];
    return labels.map((lab) => h("span", {
      class: `h3md-label ins ${lab.kind}${lab.missing ? " missing" : ""}`,
      title: `click to insert ${lab.tag} · ${lab.kind} · ${lab.file || lab.id}${lab.window ? ` · window ${fmtS(lab.window[0])}–${fmtS(lab.window[1])}` : ""}`
        + (lab.kind === "video_soundtrack" ? " (soundtrack of the video)" : "")
        + (lab.kind === "refmod" ? (lab.core ? ` · runs as ${lab.core} (${lab.at !== null ? "by its position among" : "after"} this shot's own references)` : " · kind unknown: stays plain text 'RefMod N'") : ""),
      onmousedown: (e) => e.preventDefault(),
      onclick: () => this.insertLabel(i, lab.tag) },
    lab.kind === "image" && viewURL(lab.file) ? h("img", { class: "h3md-chipthumb", src: viewURL(lab.file), alt: "", draggable: "false" }) : null,
    lab.tag, lab.kind === "refmod" && lab.core ? h("span", { class: "h3md-small" }, ` →${lab.core}`) : null, h("span", { class: "h3md-small" }, ` ${lab.file ? tailName(lab.file, 18) : lab.id}`)));
  }

  insertLabel(i, tag) {
    const ta = this.root.querySelector(`[data-key="prompt-${i}"]`);
    const s = this.plan.segments[i];
    if (!ta) return;
    if (!s) return;
    const a = this.focusPrompt === i ? (ta.selectionStart ?? ta.value.length) : ta.value.length;
    const b = this.focusPrompt === i ? (ta.selectionEnd ?? a) : a;
    ta.focus();
    ta.setRangeText(tag, a, b, "end");
    ta.dispatchEvent(new Event("input"));   // promptField: saves s.prompt + repaints the token highlight
  }

  renderAssign(s, i, kind) {
    const sel = (s[kind] || []).map(String);
    const max = kind === "images" ? M.MAX_IMAGES : M.MAX_AUDIOS;
    const row = h("div", { class: "h3md-row" }, h("span", { style: { minWidth: "52px" } }, kind === "images" ? "images" : "audios"));
    const items = this.plan.media[kind] || [];
    const num = kind === "images" ? RM.layout(this.plan, s, this.rmInfos).ownNum.image : [];
    if (!sel.length) row.append(h("span", { class: "h3md-ph" }, `empty – double-click ${kind === "images" ? "an image" : "an audio"} in the library, or drag it here`));
    // selected in order; drag a chip to reorder (a picture RefMod dragged here from the RefMod row takes that position)
    const idRef = kind === "images" ? M.idRefOf(this.plan) : "";   // 2026-10-28 idref: one per plan
    sel.forEach((id, idx) => row.append(this.shotRefTarget(this.shotRefSource(h("span", { class: `h3md-chip sel${idRef === id ? " idref" : ""}`, title: "order = label number · drag to reorder" },
      `${num[idx] || idx + 1}. ${id}`,
      kind === "images" && (!idRef || idRef === id) ? h("label", { class: "h3md-idref", title: "ID Ref: this picture is the identity reference for face refine (one per plan; untick to choose another). It stays a normal <Picture N> for the prompt",
        onmousedown: (e) => e.stopPropagation(), onclick: (e) => e.stopPropagation() },
        h("input", { type: "checkbox", checked: idRef === id, draggable: false, "data-key": `idref-${i}-${idx}`,
          onchange: (e) => { M.setIdRef(this.plan, id, e.target.checked); this.commit(); } }), "ID") : null,
      kind === "audios" ? h("span", { class: "h3md-small" }, (() => { const it = items.find((x) => x.id === id); const pr = it && this.probes[it.file]; return pr && pr.duration != null ? fmtS(pr.duration) : ""; })()) : null,
      h("span", { title: "remove", onclick: () => { M.toggleRef(this.plan, i, kind, id, false); this.commit(); } }, "✕")), { i, row: kind, idx }),
      (d) => d.i === i && (d.row === kind || (kind === "images" && d.row === "refmods" && d.slot === "image")),
      (d, after) => (d.row === kind ? this.moveShotRef(i, kind, d.idx, idx + (after ? 1 : 0)) : this.setRefModAt(i, d.idx, idx + (after ? 1 : 0), sel.length)))));
    if (kind === "audios" && (s.audioMode || "reference") === "none" && sel.length) row.append(h("span", { class: "h3md-warn" }, "audioMode none: audios ignored"));
    if (sel.length > max) row.append(h("span", { class: "h3md-bad" }, `max ${max}`));
    return kind === "images" ? this.refModReceiver(this.shotRefEnd(row, i, "image"), i, "image") : row;
  }

  // one row per assigned audio file with its own mode: reference / locked / none (exactly one ticked);
  // at most one locked file per shot; the locked file shows its offset.  Old shot-level modes are shown as
  // they behave and converted (behaviour-preserving, M.toPerFileAudio) on the first per-file edit.
  renderAudioRefs(s, i) {
    const p = this.plan;
    const box = h("div", {});
    const items = p.media.audios || [];
    const sa = M.shotAudio(s, M.mediaMaps(p), i + 1);
    const perFile = M.isPerFileAudio(s);
    const legacyNone = !perFile && (s.audioMode || "reference") === "none";
    const head = h("div", { class: "h3md-row" }, h("span", { style: { minWidth: "52px" } }, "audios"));
    if (!sa.entries.length) head.append(h("span", { class: "h3md-ph" }, "empty – double-click an audio in the library, or drag it here"));
    if (legacyNone) head.append(h("span", { class: "h3md-warn", title: "older plan: shot-level audioMode none drops ALL audio of this shot, video soundtracks included" }, "old plan: all audio off (incl. soundtracks)"),
      h("button", { title: "convert to per-file modes (files → none, video soundtracks → off): same result", onclick: () => { M.toPerFileAudio(p, i); this.commit(); } }, "convert"));
    box.append(head);
    // H3MD audioui (2026-10-26): one entry per video of the shot = that video's own audio, with its soundtrack / lock
    // boxes (moved here from the video row).  Audio only follows these two boxes, independent of depth.
    // H3MD lockexcl (2026-10-26): soundtrack and lock are mutually exclusive (ticking one unticks the other, the boxes
    // show the real state): soundtrack = the clip's sound from its start as a soft <Audio> reference; lock = the clip's
    // sound from its start is hard-copied into the segment audio (shot-level locked.source "video:<id>", offset =
    // the video's start, no own offset field, never also an <Audio> reference); neither = the clip's sound is not used.
    const vrefs = (s.videos || []).map(M.videoRef);
    const vitems = p.media.videos || [];
    const lockedVid = M.videoLockId(s);
    vrefs.forEach((r, idx) => {
      const info = vitems.find((x) => x.id === r.id);
      const t = M.effectiveTrim(r, info);
      const noAudio = !!(info && info.hasAudio === false);
      const vLocked = !legacyNone && lockedVid === r.id && !!sa.locked && sa.locked.kind === "video";
      const stOn = !legacyNone && !vLocked && M.pyBool(r.useSoundtrack === undefined ? true : r.useSoundtrack);
      const setAudio = (f) => { M.setVideoAudio(p, i, idx, f); this.commit(); };
      box.append(h("div", { class: "h3md-ref h3md-aref h3md-vaudio", "data-vaudio": r.id },
        h("span", { class: "h3md-chip", title: `sound of video ${r.id}${info ? ` (${info.file})` : ""}` }, `♪ ${r.id}`),
        h("span", { class: "h3md-small" }, noAudio ? "video audio · clip has no audio" : "video audio"),
        h("label", { title: noAudio ? "clip has no audio" : "soundtrack: the clip's sound from its start as a soft <Audio> reference (unticks lock)" },
          h("input", { type: "checkbox", checked: stOn, disabled: noAudio && !stOn ? true : null,
            "data-key": `vst-${i}-${idx}`,
            onchange: (e) => setAudio({ soundtrack: e.target.checked }) }), "soundtrack"),
        h("label", { title: noAudio ? "clip has no audio" : "lock: the clip's sound from its start is copied unchanged into this shot's audio (one per shot; not an <Audio> reference; unticks soundtrack)" },
          h("input", { type: "checkbox", checked: vLocked, disabled: noAudio && !vLocked ? true : null, "data-key": `vlk-${i}-${idx}`,
            onchange: (e) => setAudio({ lock: e.target.checked }) }), "lock"),
        vLocked ? h("span", { class: "h3md-small", title: "the locked track starts at the video's start (edit start in the videos row)" }, `from start ${fmtS(t.trimStart)}`) : null));
    });
    // a shot-level lock that is not one of the listed files nor one of this shot's videos (shown above) stays supported
    if (sa.locked && !(sa.locked.kind === "audio" && sa.entries.some((e) => e.id === sa.locked.id))
        && !(sa.locked.kind === "video" && vrefs.some((r) => r.id === sa.locked.id))) {
      box.append(h("div", { class: "h3md-ref h3md-aref" },
        h("span", { class: "h3md-chip sel", title: "locked audio track set at shot level (older plan / video soundtrack)" }, `locked: ${sa.locked.kind}:${sa.locked.id}`),
        "offset", h("input", { type: "number", class: "n", min: 0, step: 0.1, value: sa.locked.offset, "data-key": `lko-${i}`,
          onchange: (e) => { s.locked.offset = Math.max(0, Number(e.target.value) || 0); this.commit(); } }), "s",
        h("button", { title: "unlock (back to reference)", onclick: () => { delete s.audioMode; delete s.locked; this.commit(); } }, "✕")));
    }
    const modeOf = (e) => {
      if (legacyNone) return "none";
      if (perFile) return e.mode;
      return sa.locked && sa.locked.kind === "audio" && sa.locked.id === e.id ? "locked" : "reference";
    };
    sa.entries.forEach((e, idx) => {
      const it = items.find((x) => x.id === e.id);
      const pr = it && this.probes[it.file];
      const mode = modeOf(e);
      box.append(this.shotRefTarget(h("div", { class: "h3md-ref h3md-aref", "data-aref": e.id },
        this.shotRefSource(h("span", { class: "h3md-chip sel", title: `${it ? it.file : "unknown audio"} · drag to reorder` }, `${idx + 1}. ${e.id}`,
          pr && pr.duration != null ? h("span", { class: "h3md-small" }, " " + fmtS(pr.duration)) : null), { i, row: "audios", idx }),
        h("span", { class: "h3md-modes", title: "reference = passed as an <Audio> reference · locked = the shot's locked audio track (also passed as reference; one per shot) · none = not used" },
          M.AUDIO_FILE_MODES.map((m) => h("label", {},
            h("input", { type: "checkbox", checked: mode === m, "data-key": `am-${i}-${e.id}-${m}`,
              onchange: () => { if (m !== mode) M.setAudioFileMode(p, i, e.id, m); this.commit(); } }), m))),
        mode === "locked" ? ["offset", h("input", { type: "number", class: "n", min: 0, step: 0.1, value: perFile ? e.offset : M.num(s.locked && s.locked.offset, 0), "data-key": `ao-${i}-${e.id}`,
          title: "seconds into this file where the shot starts",
          onchange: (ev) => { M.toPerFileAudio(p, i); M.setAudioFileOffset(p, i, e.id, ev.target.value); this.commit(); } }), "s"] : null,
        h("button", { title: "remove from this shot", onclick: () => { M.toggleRef(p, i, "audios", e.id, false); this.commit(); } }, "✕")),
        (d) => d.i === i && d.row === "audios", (d, after) => this.moveShotRef(i, "audios", d.idx, idx + (after ? 1 : 0)), true));
    });
    if (sa.audios.length > M.MAX_AUDIOS) box.append(h("span", { class: "h3md-bad" }, `max ${M.MAX_AUDIOS} referenced audios`));
    return box;
  }

  renderVideoRefs(s, i) {
    const box = this.refModReceiver(this.shotRefEnd(h("div", {}), i, "video"), i, "video");
    const refs = (s.videos || []).map(M.videoRef);
    const head = h("div", { class: "h3md-row" }, h("span", { style: { minWidth: "52px" } }, "videos"));
    const items = this.plan.media.videos || [];
    const num = RM.layout(this.plan, s, this.rmInfos).ownNum.video;
    if (!refs.length) head.append(h("span", { class: "h3md-ph" }, "empty – double-click a video in the library, or drag it here"));
    box.append(head);
    // H3MD audioui (2026-10-26): no ref box any more - every video is a <Video N> (numbered by position); its
    // soundtrack / lock boxes live in the audios area (renderAudioRefs), independent of depth
    const nRef = refs.length;
    refs.forEach((r, idx) => {
      const info = items.find((x) => x.id === r.id);
      const dOn = M.depthOn(r) && M.depthIndex(s) === idx, pos = idx;
      const flags = (f) => { M.setVideoFlags(s, idx, f); this.commit(); };
      const t = M.effectiveTrim(r, info);
      box.append(this.shotRefTarget(h("div", { class: "h3md-ref" },
        this.shotRefSource(h("span", { class: "h3md-chip sel", title: "drag to reorder" }, `${num[pos] || pos + 1}. ${r.id}`), { i, row: "videos", idx }),
        h("span", { class: "h3md-small" }, `clip ${fmtS(t.srcDur)}`),
        // start = the ref's trimStart (s into the clip; Split shot sets it per segment, editable here, 2026-10-25);
        // a used duration past the new clip end is dropped (= rest of clip); a locked soundtrack follows the start
        h("label", { class: "h3md-small", title: "start offset into the clip (s); Split shot sets it per segment" }, "start ",
          h("input", { type: "number", min: 0, step: 0.1, value: +t.trimStart.toFixed(3), "data-key": `ts-${i}-${idx}`, style: { width: "64px" },
            onchange: (e) => {
              let v = Number(e.target.value);
              if (!Number.isFinite(v) || v < 0) v = 0;
              if (t.srcDur !== null) v = Math.min(v, Math.max(0, t.srcDur - 5 / 24));
              v = Math.round(v * 1000) / 1000;
              s.videos = (s.videos || []).map(M.videoRef);
              const ref = s.videos[idx];
              ref.trimStart = v;
              const u = Number(ref.useDuration);
              if (t.srcDur !== null && Number.isFinite(u) && u >= t.srcDur - v - 1e-9) delete ref.useDuration;
              M.syncVideoLockOffset(s);   // H3MD lockexcl: a video lock's offset follows the video's start
              this.commit();
            } })),
        // H3MD depth (2026-10-26): depth = drives H3MD Depth Control (one per shot); audioui: the ref box was removed
        h("label", { title: "off: the clip's picture is the <Video N> reference as before · on: depth route - its depth video drives the ControlNet AND replaces the picture as <Video N> (raw picture not encoded; needs H3MD Depth Control on the Loop; one clip per shot). Sound is set in the audios area" },
          h("input", { type: "checkbox", checked: dOn, "data-key": `vd-${i}-${idx}`,
            onchange: (e) => flags({ depth: e.target.checked }) }), "depth"),
        // 2026-10-27 sam3box: SAM3 box after depth; the prompt field shows only when it is ticked. SAM3 runs only
        // with box ticked AND text (else full depth, SAM3 not loaded)
        dOn ? h("label", { title: "SAM3 mask: only the objects named in the box keep their depth, the rest is black (use when the reference background must NOT shape the scene). Unticked or empty = full depth, SAM3 is not loaded" },
          h("input", { type: "checkbox", checked: M.sam3On(r), "data-key": `vsb-${i}-${idx}`,
            onchange: (e) => flags({ sam3: e.target.checked }) }), "SAM3") : null,
        dOn && M.sam3On(r) ? h("input", { type: "text", value: r.sam3Prompt || "", placeholder: "SAM3 prompt, e.g. person, TV", "data-key": `vs-${i}-${idx}`,
          title: "SAM3 prompt (SAM3 mask only, never added to the H3 prompt), e.g. person, TV", style: { width: "140px" },
          onchange: (e) => flags({ sam3Prompt: e.target.value }) }) : null,
        h("button", { title: "remove", onclick: () => { M.toggleRef(this.plan, i, "videos", r.id, false); this.commit(); } }, "✕")),
        (d) => d.i === i && (d.row === "videos" || (d.row === "refmods" && d.slot === "video")),
        (d, after) => (d.row === "videos" ? this.moveShotRef(i, "videos", d.idx, idx + (after ? 1 : 0)) : this.setRefModAt(i, d.idx, pos + (after ? 1 : 0), nRef)), true));
    });
    return box;
  }

  // RefMods of this shot in their own row, in segment.refmods order (= <RefMod j>): drag an icon left / right to
  // reorder (renumbers j); drag it onto a picture (picture slot) / video (video slot) entry to run it at that position
  // (sets `at`; dropped on the images row / videos box itself = after the shot's own refs).
  renderRefModRefs(s, i) {
    const box = h("div", {});
    const labs = this.rmLabels(i);
    const head = h("div", { class: "h3md-row" }, h("span", { style: { minWidth: "52px" } }, "refmods"));
    if (!labs.length) head.append(h("span", { class: "h3md-ph" }, "empty – double-click a RefMod in the library, or drag it here"));
    const row = h("div", { class: "h3md-rmrow" });
    box.append(head, row);
    labs.forEach((l, idx) => {
      row.append(this.shotRefTarget(h("div", { class: "h3md-ref h3md-rmref", "data-rmref": l.id },
        // RefMod-coloured icon (drag handle): this shot's slot label (top; "—" = unknown kind), filename
        // (bottom); the prompt tag <RefMod j> (j = order in this row) follows
        this.shotRefSource(h("span", { class: "h3md-rmicon", title: `${l.file || "unknown RefMod"}\nid: ${l.id}\ndrag left / right to reorder`
          + (l.slot === "image" || l.slot === "video" ? `; drag onto a ${l.slot === "image" ? "picture" : "video"} above to run it at that position` : "") },
          h("span", { class: "sl" }, l.core ? l.core.slice(1, -1) : "—"),
          h("span", { class: "fn" }, l.file ? tailName(l.file, 18) : l.id)), { i, row: "refmods", idx, slot: l.slot }),
        h("span", { class: "h3md-label refmod ro", title: l.core ? `prompt label; runs as ${l.core}` : "prompt label" }, l.tag),
        l.slot && l.slot !== l.rmKind ? h("span", { class: "h3md-small" }, ` (as ${l.slot === "image" ? "picture" : "video"})`) : null,
        h("button", { title: "remove from this shot", onclick: () => { RM.toggle(this.plan, i, l.id, false); this.commit(); } }, "✕")),
        (d) => d.i === i && d.row === "refmods", (d, after) => this.moveShotRef(i, "refmods", d.idx, idx + (after ? 1 : 0))));
    });
    return box;
  }
}

// H3MD Loop Sampler + RefMod: hide delete_cache like h3md_hide_widgets_ext.js does for H3MDLoopSampler2 (value kept)
function hideLoopWidgets(nodeType) {
  const apply = (node) => {
    try { if (hideWidgets(node, ["delete_cache"]).length) node.setSize?.([node.size[0], node.computeSize()[1]]); }
    catch (e) { console.error("[H3MD] hide widgets failed", e); }
  };
  const onCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function () { const r = onCreated?.apply(this, arguments); apply(this); return r; };
  const onConfigure = nodeType.prototype.onConfigure;
  nodeType.prototype.onConfigure = function () { const r = onConfigure?.apply(this, arguments); apply(this); return r; };
}

app.registerExtension({
  name: "H3MD.DirectorEditorRefMod",
  settings: [{ id: ADVANCED_SETTING, category: ["H3MD", "Director", "Advanced buttons"],
    name: "Show advanced Director buttons (add file by name / refresh / clear media, reference video height, + Shot)",
    tooltip: "Off (default): simple Director – media rows show only ＋ (or drop files), reference video height is auto. Reopen the workflow after changing.",
    type: "boolean", defaultValue: false }],
  setup() {
    try { installMoveWatch(app); } catch (e) { console.error("[H3MD] editor move watch failed", e); }
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === LOOP_CLASS) { hideLoopWidgets(nodeType); return; }
    if (nodeData?.name !== NODE_CLASS) return;
    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      try {
        this.__h3md = new DirectorEditor(this);
        watchNode(this);
        installMoveWatch(app);   // no-op once installed (covers a canvas created after setup)
        if (this.size[0] < 760 || this.size[1] < 720) this.setSize([Math.max(this.size[0], 760), Math.max(this.size[1], 720)]);
      } catch (e) { console.error("[H3MD] editor init failed", e); }
      return r;
    };
    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      try {
        this.__h3md?.reload();
        this.__h3md?.lockFps();   // after widgets_values were applied
        // older workflows saved the node at plain-textbox size; give the editor room (size only)
        if (this.__h3md && this.size[0] < 640) this.setSize([760, Math.max(this.size[1], 720)]);
      } catch (e) { console.error("[H3MD] editor reload failed", e); }
      return r;
    };
    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      try { this.__h3md?.audioEl?.pause(); } catch { /* ignore */ }
      try { unwatchNode(this); for (const t of this.__h3md?.root?.querySelectorAll?.(".h3md-tile") ?? []) t.__h3mdReleaseHover?.(); } catch { /* ignore */ }
      return onRemoved?.apply(this, arguments);
    };
  },
});
