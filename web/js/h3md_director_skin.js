// H3MD - Director editor skin + language (2026-10-27).
// A display layer on top of h3md_director_refmod_ui.js: it never touches the plan, widgets_values or the backend.
//  * Skin: a modern card style for every .h3md-root (larger type, one accent colour + status colours, rounded cards,
//    help paragraphs folded behind an "ⓘ" button, empty media boxes collapsed, quiet "Clear" buttons that need a
//    second click). Media colours are kept (image green, video blue, audio amber) and RefMod keeps its own purple.
//  * UI scale 90-140 % (A- / A+ in the toolbar), and a switch back to the classic look. Both are per-browser
//    conveniences (localStorage).
//  * Language: follows ComfyUI's setting Comfy.Locale (zh* -> Chinese, anything else -> English). The editor's mixed
//    "English 中文" labels are rewritten in place to one language; prompts, <Picture/Video/Audio/RefMod N> labels,
//    file names and parameter names are never translated.
import { app } from "../../scripts/app.js";

const LS_SKIN = "h3md.director.skin";      // "modern" | "classic"
const LS_SCALE = "h3md.director.scale";    // 0.9 .. 1.4
const ROOT = ".h3md-root";

const ls = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
};

// ------------------------------------------------------------------ language
function locale() {
  let l = "";
  try { l = app.extensionManager?.setting?.get?.("Comfy.Locale") || app.ui?.settings?.getSettingValue?.("Comfy.Locale") || ""; } catch { /* ignore */ }
  if (!l) l = navigator.language || "en";
  return /^zh/i.test(l) ? "zh" : "en";
}

// exact texts: original -> [zh, en]
const EXACT = {
  "⚠ 提示词里有按时间写的镜头，自动拆段后每段都从 0 秒套用整段提示词，后面几段的时间会错位：建议点“拆分分镜…”按上面的切点手动拆开，各写各的提示词": ["⚠ 提示词里有按时间写的镜头，自动拆段后每段都从 0 秒套用整段提示词，后面几段的时间会错位：建议点“拆分分镜…”按上面的切点手动拆开，各写各的提示词", "⚠ the prompt is time-coded: every auto-split part gets the whole prompt from 0 s, so later parts' timings drift; use “Split shot…” at the cuts above and write one prompt per part"],
  "Media 素材库": ["素材库", "Media"],
  "音频 Audios": ["音频", "Audios"], "视频 Videos": ["视频", "Videos"], "图片 Images": ["图片", "Images"],
  "Upload…": ["上传…", "Upload…"], "Add": ["添加", "Add"],
  "Clear 清空": ["清空", "Clear"], "Clear 清空分镜": ["清空分镜", "Clear shots"],
  "empty · Upload… or drop files here": ["空 · 点“上传”或把文件拖到这里", "empty · Upload… or drop files here"],
  "Ref video height 参考视频分辨率": ["参考视频分辨率", "Ref video height"],
  "native 原始": ["原始", "native"], "auto 自动": ["自动", "auto"],
  "native = 不改 · 480/576/720 = 短边 · auto = 按段估算 token 自动降档":
    ["原始 = 不改 · 480/576/720 = 短边 · 自动 = 按段估算 token 自动降档", "native = unchanged · 480/576/720 = short edge · auto = steps down per segment by estimated tokens"],
  "Timeline 时间轴": ["时间轴", "Timeline"], "Split shot…": ["拆分分镜…", "Split shot…"], "+ Shot": ["+ 分镜", "+ Shot"],
  "drag a shot onto the previous one = continuation (hatched overlap) · drag it to just touch = hard cut · drag the right edge = length":
    ["把分镜拖到前一个上 = 续接（斜线为重叠）· 拖到刚好相接 = 硬切 · 拖右边缘 = 长度", "drag a shot onto the previous one = continuation (hatched overlap) · drag it to just touch = hard cut · drag the right edge = length"],
  "Shots 分镜": ["分镜", "Shots"],
  "length": ["长度", "length"], "images": ["图片", "images"], "videos": ["视频", "videos"], "audios": ["音频", "audios"],
  "refmods": ["RefMod", "RefMods"], "start": ["起点", "start"], "depth": ["深度", "depth"],
  "video audio": ["视频原声", "video audio"], "soundtrack": ["原声参考", "soundtrack"], "lock": ["锁定", "lock"],
  "empty – double-click an audio in the library, or drag it here": ["空 – 在素材库双击音频，或拖到这里", "empty – double-click an audio in the library, or drag it here"],
  "empty – double-click a RefMod in the library, or drag it here": ["空 – 在素材库双击 RefMod，或拖到这里", "empty – double-click a RefMod in the library, or drag it here"],
  "references 引用:": ["引用：", "references:"],
  "Global 全局提示词": ["全局提示词", "Global prompt"],
  "applies to every shot · prepended to each shot prompt (trigger words, style … or leave empty)":
    ["作用于每个分镜 · 加在每段提示词前面（触发词、风格……或留空）", "applies to every shot · prepended to each shot prompt (trigger words, style … or leave empty)"],
  "New plan 新建计划": ["新建计划", "New plan"], "RefMod 参考模型": ["RefMod 参考模型", "RefMod"],
  "full report: plan_info output": ["完整报告：plan_info 输出", "full report: plan_info output"],
  // titles / placeholders
  "Upload to ComfyUI input folder": ["上传到 ComfyUI 的 input 文件夹", "Upload to ComfyUI input folder"],
  "Upload to ComfyUI input folder (.safetensors = RefMod, to input/refmods)": ["上传到 ComfyUI 的 input 文件夹（.safetensors = RefMod，放进 input/refmods）", "Upload to ComfyUI input folder (.safetensors = RefMod, to input/refmods)"],
  "file in input/ …": ["input 里的文件 …", "file in input/ …"], "file in input/ or RefMod name …": ["input 里的文件或 RefMod 名 …", "file in input/ or RefMod name …"],
  "Add the chosen input-folder file": ["添加选中的 input 文件", "Add the chosen input-folder file"],
  "Refresh file list": ["刷新文件列表", "Refresh file list"],
  "rename id": ["重命名 id", "rename id"], "remove from plan (file stays on disk)": ["从计划中移除（文件保留）", "remove from plan (file stays on disk)"],
  "Manual: replace the selected shot by separate shots (each gets its own prompt/refs card)": ["手动：把选中的分镜拆成几个独立分镜（各自有提示词和素材）", "Manual: replace the selected shot by separate shots (each gets its own prompt/refs card)"],
  "Append a hard-cut shot": ["在末尾加一个硬切分镜", "Append a hard-cut shot"],
  "clear the shots area: all shots → one empty shot (media library is kept)": ["清空分镜区：所有分镜 → 一个空分镜（素材库保留）", "clear the shots area: all shots → one empty shot (media library is kept)"],
  "add a shot after the selected one": ["在选中分镜后加一个分镜", "add a shot after the selected one"],
  "switch to the previous shot (does not reorder)": ["切到上一个分镜（不改顺序）", "switch to the previous shot (does not reorder)"],
  "switch to the next shot (does not reorder)": ["切到下一个分镜（不改顺序）", "switch to the next shot (does not reorder)"],
  "duplicate": ["复制分镜", "duplicate"], "insert a new shot after this one": ["在后面插入新分镜", "insert a new shot after this one"],
  "delete shot": ["删除分镜", "delete shot"], "remove": ["移除", "remove"], "drag to reorder": ["拖动排序", "drag to reorder"],
  "order = label number · drag to reorder": ["顺序 = 标签编号 · 拖动排序", "order = label number · drag to reorder"],
  "H3 uses 24 fps (fixed, not editable)": ["H3 固定 24 fps（不可改）", "H3 uses 24 fps (fixed, not editable)"],
  "SAM3 prompt, e.g. person, TV": ["SAM3 提示词，如 person, TV", "SAM3 prompt, e.g. person, TV"],
};
// patterns: [regex, zh replacer, en replacer]  ($n = groups)
const RULES = [
  [/^(\d+) seg · (\d+)f \(([\d.]+s)\)$/, "$1 段 · $2 帧（$3）", "$1 seg · $2f ($3)"],
  [/^refmod reference fps\s+(\d+)$/, "RefMod 参考帧率 $1", "RefMod reference fps $1"],
  [/^预算 (\S+) tok\/段 · (\S+) → (\d+) 段$/, "预算 $1 tok/段 · $2 → $3 段", "budget $1 tok/seg · $2 → $3 seg"],
  [/^音频 (\d+) · 视频 (\d+) · 图片 (\d+)$/, "音频 $1 · 视频 $2 · 图片 $3", "audio $1 · video $2 · image $3"],
  [/^double-click = add to Shot (\d+) · drag = reorder \/ drop on a shot$/, "双击 = 加到分镜 $1 · 拖动 = 排序 / 拖到分镜上", "double-click = add to Shot $1 · drag = reorder / drop on a shot"],
  [/^(\d+) · click a tab to edit it · drag tabs to reorder$/, "$1 个 · 点标签编辑 · 拖动标签排序", "$1 · click a tab to edit it · drag tabs to reorder"],
  [/^(\d+) selected – drag or double-click to add all$/, "已选 $1 个 – 拖动或双击全部加入", "$1 selected – drag or double-click to add all"],
  [/^(\d+) shot\(s\) · (\d+)f \(([\d.]+s)\)$/, "$1 个分镜 · $2 帧（$3）", "$1 shot(s) · $2f ($3)"],
  [/^continue (\d+)f$/, "续接 $1 帧", "continue $1f"],
  [/^Shot (\d+)$/, "分镜 $1", "Shot $1"],
  [/^⚡(\d+) 段$/, "⚡$1 段", "⚡$1 parts"],
  [/^clip ([\d.]+s)$/, "片长 $1", "clip $1"],
  [/^from start ([\d.]+s)$/, "从 $1 开始", "from start $1"],
  [/^frames (\d+)–(\d+) · (.+)$/, "帧 $1–$2 · $3", "frames $1–$2 · $3"],
  [/^= ([\d.]+s) · auto: (.+)$/, "= $1 · 自动：$2", "= $1 · auto: $2"],
  [/^预计 ([\d,]+) token \/ 预算 ([\d,]+)（(\d+%)），一段跑完$/, "预计 $1 token / 预算 $2（$3），一段跑完", "est. $1 tokens / budget $2 ($3): one segment"],
  [/^预计 ([\d,]+) token，超出预算 ([\d,]+)（(\d+%)）→ 运行时自动分成 (\d+) 段：(.+?) 帧（重叠 (\d+) 帧，每段最多 ([\d,]+) token；切点在本分镜 (.+?)，已按参考视频动作避开大动作，运行时完全一致）$/,
    "预计 $1 token，超出预算 $2（$3）→ 运行时自动分成 $4 段：$5 帧（重叠 $6 帧，每段最多 $7 token；切点在本分镜 $8，已按参考视频动作避开大动作，运行时完全一致）",
    "est. $1 tokens, over the budget $2 ($3) → auto-split at run time into $4 parts: $5 f (overlap $6 f, ≤ $7 tokens each; cuts at $8 of this shot, placed at calm reference motion, same as the run)"],
  [/^预计 ([\d,]+) token，超出预算 ([\d,]+)（(\d+%)），但无法拆分（单段最长只能 (\d+) 帧）：请降低分辨率或减少参考素材$/,
    "预计 $1 token，超出预算 $2（$3），但无法拆分（单段最长只能 $4 帧）：请降低分辨率或减少参考素材",
    "est. $1 tokens, over the budget $2 ($3) and cannot be split (max $4 f per part): lower the resolution or use fewer references"],
];
// timeline bar / tab sub-lines: word swaps inside a longer text
const WORDS_ZH = [[/^Shot (\d+) · /, "分镜 $1 · "], [/ new\b/g, " 新"], [/▶vid/g, "▶视频"]];
const LONG_NOTE_EN = "Shot length is what you set (5+17n frames). At run time the Director estimates each shot against the VRAM budget: a shot over budget is split automatically into equal continuation parts (overlap 39 frames); reference videos and locked audio follow, no manual Split needed. The line under each shot tab shows the estimate: green = one segment, amber = will be split into N parts, red = over budget and cannot be split. Budget = GPU VRAM − ComfyUI's launch reserve (--reserve-vram / --vram-headroom), minus a 15% margin. With reference video height = auto, depth videos always use the lowest tier and other reference videos stay sharp, using more parts if needed (plan_json \"autoSplitPrefer\": \"fewer\" prefers fewer parts; \"autoSplit\": false turns it off).";
const PROMPT_NOTE_EN = "Write prompts in the standard six-section H3 format (subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music); times start at 0 s of this shot. <Picture/Video/Audio N> numbers follow the order of the media in this shot (images, then videos with a video's soundtrack before it, then audios). Dropping media on a shot only attaches it; insert labels with the buttons under the prompt or by typing @. RefMod: write <RefMod j>; at run time it becomes the shot's real <Picture k>/<Video k>/<Audio k>. Drop an image/video RefMod onto an image/video to take that position; otherwise it follows the shot's own images/videos.";

const ORIG = new WeakMap();   // text node / element attr -> original text
function tr(text, lang) {
  const t = text.trim();
  if (!t) return text;
  const ex = EXACT[t];
  if (ex) return text.replace(t, ex[lang === "zh" ? 0 : 1]);
  for (const [re, zh, en] of RULES) if (re.test(t)) return text.replace(t, t.replace(re, lang === "zh" ? zh : en));
  if (lang === "zh" && /^Shot \d+ · |\bnew\b|▶vid/.test(t)) { let s = t; for (const [re, r] of WORDS_ZH) s = s.replace(re, r); return text.replace(t, s); }
  if (lang === "en" && t.startsWith("分镜长度按你设定的来")) return LONG_NOTE_EN;
  if (lang === "en" && t.startsWith("提示词按标准 H3 六段式写")) return PROMPT_NOTE_EN;
  return text;
}
function translateTree(root, lang) {
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (!p || p.closest("textarea,.h3md-pf,.h3md-label,.h3md-tile .nm,.h3md-meta")) continue;
    let o = ORIG.get(n);
    if (o === undefined || (n.nodeValue !== o.cur)) { o = { src: n.nodeValue, cur: null }; }
    const out = tr(o.src, lang);
    o.cur = out; ORIG.set(n, o);
    if (n.nodeValue !== out) n.nodeValue = out;
  }
  for (const el of [root, ...root.querySelectorAll("[title],[placeholder]")]) {
    for (const a of ["title", "placeholder"]) {
      const v = el.getAttribute(a);
      if (!v) continue;
      const key = "_h3o_" + a;
      if (el[key] === undefined || el[key].cur !== v) el[key] = { src: v, cur: null };
      const out = tr(el[key].src, lang);
      el[key].cur = out;
      if (v !== out) el.setAttribute(a, out);
    }
  }
}

// ------------------------------------------------------------------ skin css
const CSS = `
.h3md-root[data-h3sk]{--s:1;--acc:#9cc9ff;--acc2:#3f6fa8;--mint:#9be7c4;--bg:rgba(33,47,57,.5);--card:rgba(52,72,86,.26);--card2:rgba(64,86,102,.22);--line:rgba(190,220,235,.18);
  --txt:#ffffff;--mut:#d3dde7;--ok:#7fe0b0;--warn:#f2b84b;--bad:#ff6b6b;--img:#4f9a5c;--vid:#3f78c8;--aud:#c48a35;--rm:#a35bb0;
  background:var(--bg)!important;border:1px solid rgba(150,190,215,.35)!important;border-radius:14px!important;padding:calc(10px*var(--s))!important;
  font:calc(13.5px*var(--s))/1.45 "Inter","Segoe UI","Microsoft YaHei UI","PingFang SC",system-ui,sans-serif!important;color:var(--txt)!important;text-shadow:0 1px 2px rgba(0,0,0,.7);
  scrollbar-color:#5a6372 transparent}
[data-h3sk] .h3md-small{font-size:calc(12px*var(--s))!important;opacity:1!important;color:var(--mut)}
[data-h3sk] .h3md-bar{background:linear-gradient(180deg,rgba(40,58,70,.62),rgba(30,44,54,.62));border:1px solid var(--line);border-radius:10px;padding:calc(7px*var(--s)) calc(10px*var(--s));gap:calc(8px*var(--s));margin-bottom:calc(10px*var(--s))!important}
[data-h3sk] .h3md-title{font-size:calc(15px*var(--s));font-weight:700;letter-spacing:.2px;background:linear-gradient(90deg,#9be7c4,#9cc9ff);-webkit-background-clip:text;background-clip:text;color:transparent}
[data-h3sk] .h3md-bar>.h3md-small{background:rgba(255,255,255,.05);border:1px solid var(--line);border-radius:999px;padding:1px calc(9px*var(--s));color:#e3e9f1}
[data-h3sk] [data-as-budget]{background:rgba(155,231,196,.12)!important;border-color:rgba(155,231,196,.4)!important;color:#c9f3df!important}
[data-h3sk] .h3md-stat{font-size:calc(12px*var(--s));line-height:calc(20px*var(--s));padding:0 calc(9px*var(--s));border-radius:999px;background:rgba(76,195,138,.1);border-color:rgba(76,195,138,.35)}
[data-h3sk] .h3md-sec,[data-h3sk] .h3md-sub,[data-h3sk] .h3md-seg,[data-h3sk] .h3md-dlg{background:var(--card)!important;border:1px solid var(--line)!important;border-radius:12px!important;padding:calc(9px*var(--s))!important;margin-bottom:calc(10px*var(--s))!important;box-shadow:0 1px 0 rgba(255,255,255,.03) inset,0 4px 14px rgba(0,0,0,.18)}
[data-h3sk] .h3md-seg{border-left:1px solid var(--line)!important}
[data-h3sk] .h3md-seg.tabbed{border-top-left-radius:0!important;margin-top:0!important}
[data-h3sk] .h3md-sec>h4,[data-h3sk] .h3md-sub>h4,[data-h3sk] .h3md-seg>h4{font-size:calc(14.5px*var(--s))!important;font-weight:650;height:calc(28px*var(--s))!important;gap:calc(8px*var(--s))!important;margin-bottom:calc(6px*var(--s))!important}
[data-h3sk] .h3md-sec>h4::before,[data-h3sk] .h3md-sub>h4::before{content:"";width:4px;height:60%;border-radius:2px;background:linear-gradient(180deg,var(--mint),var(--acc));flex:none}
[data-h3sk] .h3md-box{background:var(--card2)!important;border:1px solid var(--line)!important;border-radius:10px!important}
[data-h3sk] .h3md-boxhead{padding:calc(5px*var(--s)) calc(8px*var(--s));border-bottom:1px solid var(--line);font-weight:600}
[data-h3sk] .h3md-cnt{font-size:calc(11px*var(--s));line-height:calc(17px*var(--s));min-width:calc(20px*var(--s));border-radius:999px;color:#fff}
[data-h3sk] .h3md-box.images .h3md-cnt{background:var(--img)}[data-h3sk] .h3md-box.videos .h3md-cnt{background:var(--vid)}[data-h3sk] .h3md-box.audios .h3md-cnt{background:var(--aud)}
[data-h3sk] .h3md-grid:has(>.h3md-empty:only-child){height:auto!important;min-height:0!important;resize:none!important;grid-auto-rows:auto!important}
[data-h3sk] .h3md-empty{padding:calc(6px*var(--s));font-size:calc(12px*var(--s));border-style:dashed;border-color:#5a6372;border-radius:8px;opacity:.85}
[data-h3sk] .h3md-tile{border-radius:8px;border-color:rgba(150,190,215,.3);background:rgba(20,30,38,.45);transition:transform .12s,border-color .12s,box-shadow .12s}
[data-h3sk] .h3md-tile:hover{border-color:var(--acc);box-shadow:0 4px 14px rgba(0,0,0,.3);transform:translateY(-1px)}
[data-h3sk] .h3md-tile .nm{font-size:calc(11px*var(--s))}
[data-h3sk] .h3md-tile .tk{font-size:calc(10px*var(--s))}
[data-h3sk] input,[data-h3sk] select,[data-h3sk] textarea{background:rgba(12,19,25,.7)!important;border:1px solid rgba(150,190,215,.3)!important;border-radius:8px!important;color:var(--txt)!important;padding:calc(3px*var(--s)) calc(7px*var(--s))!important}
[data-h3sk] input:focus,[data-h3sk] select:focus,[data-h3sk] textarea:focus{outline:none;border-color:var(--acc)!important;box-shadow:0 0 0 3px rgba(155,231,196,.2)}
[data-h3sk] button{background:rgba(70,98,118,.35)!important;border:1px solid rgba(160,200,225,.35)!important;border-radius:8px!important;color:var(--txt)!important;padding:calc(2px*var(--s)) calc(10px*var(--s))!important;transition:background .12s,border-color .12s}
[data-h3sk] button:hover{filter:none!important;background:rgba(90,125,150,.55)!important;border-color:var(--mint)!important}
[data-h3sk] button.on{background:var(--acc2)!important;border-color:var(--acc)!important}
[data-h3sk] button.h3sk-danger{background:transparent!important;border-color:transparent!important;color:var(--mut)!important}
[data-h3sk] button.h3sk-danger:hover{color:var(--bad)!important;border-color:rgba(255,107,107,.45)!important;background:rgba(255,107,107,.08)!important}
[data-h3sk] button.h3sk-armed{color:#fff!important;background:#c94b4b!important;border-color:#ff8a8a!important}
[data-h3sk] .h3md-tl2{height:calc(96px*var(--s));background:rgba(12,19,25,.4)!important;border:1px solid var(--line);border-radius:10px!important}
[data-h3sk] .h3md-tl2 .tick{font-size:calc(10px*var(--s));color:var(--mut);border-left-color:rgba(255,255,255,.08)}
[data-h3sk] .h3md-tl2 .sb{height:calc(26px*var(--s))!important;border-radius:7px!important;font-size:calc(11px*var(--s))!important;font-weight:600;box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 2px 6px rgba(0,0,0,.35)!important}
[data-h3sk] .h3md-tl2 .sb.sel{box-shadow:0 0 0 2px #fff,0 0 12px rgba(106,165,255,.6)!important}
[data-h3sk] .h3md-tl2 .seam{font-size:calc(10.5px*var(--s));border-radius:999px;padding:0 calc(6px*var(--s))}
[data-h3sk] .h3md-tabs{height:calc(52px*var(--s))!important;gap:calc(5px*var(--s));border-bottom:1px solid var(--line)!important}
[data-h3sk] .h3md-tab{height:calc(49px*var(--s))!important;border-radius:10px 10px 0 0!important;border-color:var(--line)!important;background:rgba(52,72,86,.3)!important;padding:calc(4px*var(--s)) calc(10px*var(--s))!important;font-size:calc(12.5px*var(--s))!important}
[data-h3sk] .h3md-tab .tl{font-size:calc(10.5px*var(--s))!important;color:var(--mut);opacity:1}
[data-h3sk] .h3md-tab .as{font-size:calc(10.5px*var(--s))!important;font-weight:600}
[data-h3sk] .h3md-tab.sel{background:linear-gradient(180deg,rgba(156,201,255,.26),rgba(156,201,255,.08))!important;border-color:rgba(156,201,255,.6)!important;box-shadow:inset 0 2px 0 var(--mint)}
[data-h3sk] .h3md-as-ok{color:var(--ok)}[data-h3sk] .h3md-as-split{color:var(--warn)}[data-h3sk] .h3md-as-over{color:var(--bad)}
[data-h3sk] .h3md-chip{font-size:calc(12px*var(--s));border-radius:999px;padding:calc(1px*var(--s)) calc(9px*var(--s));border-color:rgba(150,190,215,.3);background:rgba(52,72,86,.4)}
[data-h3sk] .h3md-label{font-size:calc(12px*var(--s));border-radius:6px;padding:calc(1px*var(--s)) calc(6px*var(--s))}
[data-h3sk] .h3md-label.image{background:var(--img)}[data-h3sk] .h3md-label.video{background:var(--vid)}[data-h3sk] .h3md-label.audio,[data-h3sk] .h3md-label.video_soundtrack{background:var(--aud)}
[data-h3sk] .h3md-label.refmod{background:var(--rm)!important;box-shadow:0 0 0 1px #d48be0 inset}
[data-h3sk] .h3md-labrow{height:calc(30px*var(--s))!important}
[data-h3sk] .h3md-subline{height:calc(26px*var(--s))!important}
[data-h3sk] .h3md-row.one{height:calc(30px*var(--s))!important}
[data-h3sk] .h3md-one{height:calc(19px*var(--s))!important;line-height:calc(19px*var(--s))!important}
[data-h3sk] .h3md-ref{background:rgba(20,30,38,.32);border-radius:8px;padding:calc(3px*var(--s)) calc(6px*var(--s))}
[data-h3sk] .h3md-pf,[data-h3sk] .h3md-pf *,[data-h3sk] input,[data-h3sk] select,[data-h3sk] textarea,[data-h3sk] .h3md-tl2 .so,[data-h3sk] .h3md-tl2 .ov{text-shadow:none!important}
[data-h3sk] .h3md-pf-back,[data-h3sk] .h3md-pf textarea{font-size:calc(12.5px*var(--s))!important;line-height:1.55!important}
[data-h3sk] .h3md-pf textarea{padding:2px 4px!important}
[data-h3sk] .h3md-pf textarea{background:transparent!important}   /* the highlight layer sits UNDER the textarea: an opaque textarea hides it */   /* must equal .h3md-pf-back (refmod_ui.js:404), else the tag highlights drift */
[data-h3sk] .h3md-pf-back mark{border-radius:4px!important;background:rgba(255,255,255,.28)!important;box-shadow:0 0 0 1.5px rgba(255,255,255,.7)!important}
[data-h3sk] .h3md-pf-back mark.image{background:rgba(70,165,60,.88)!important;box-shadow:0 0 0 1.5px #9be27f!important}
[data-h3sk] .h3md-pf-back mark.video{background:rgba(45,115,220,.92)!important;box-shadow:0 0 0 1.5px #9cc4ff!important}
[data-h3sk] .h3md-pf-back mark.audio{background:rgba(205,125,30,.92)!important;box-shadow:0 0 0 1.5px #ffc778!important}
[data-h3sk] .h3md-pf-back mark.refmod{background:rgba(170,65,190,.92)!important;box-shadow:0 0 0 1.5px #ee9ef9!important}
[data-h3sk] .h3md-pf-back mark.bad{background:rgba(210,55,55,.92)!important;box-shadow:0 0 0 1.5px #ff9a9a!important}
[data-h3sk] .h3md-pf{border-radius:10px;background:rgba(10,16,21,.72)!important}
[data-h3sk] .h3md-note{display:none;background:rgba(20,30,38,.5);border:1px solid var(--line);border-left:3px solid var(--mint);border-radius:8px;padding:calc(8px*var(--s)) calc(10px*var(--s));color:#dfe5ee!important;font-size:calc(12.5px*var(--s))!important;line-height:1.6!important}
[data-h3sk][data-h3help] .h3md-note{display:block}
[data-h3sk] .h3md-tlhint{font-size:calc(11.5px*var(--s))!important;color:var(--mut)}
[data-h3sk] .h3md-pop,[data-h3sk] .h3md-at{border-radius:10px;border-color:rgba(150,190,215,.35);background:rgba(28,40,49,.96);box-shadow:0 12px 30px rgba(0,0,0,.45)}
.h3sk-tools{display:inline-flex;gap:4px;align-items:center;margin-left:auto}
.h3sk-tools button{padding:0 calc(8px*var(--s,1))!important;line-height:calc(20px*var(--s,1))}
.h3sk-tools .pct{font-size:11px;color:#c9d1dc;min-width:34px;text-align:center}
`;
const TL_COLORS = ["#4f8fc9", "#47a593", "#5b86d6", "#4b9fb0", "#5f95d8", "#52a88c"];

function injectCss() {
  if (document.getElementById("h3md-skin-css")) return;
  const st = document.createElement("style");
  st.id = "h3md-skin-css";
  st.textContent = CSS;
  document.head.append(st);
}

function T(zh, en) { return locale() === "zh" ? zh : en; }

// ------------------------------------------------------------------ per-root work
function markDanger(b) {
  const t = b.title || "", d = b.getAttribute("data-clear");
  if (d || /^remove ALL /.test(t) || /^clear the shots area/.test(t) || (b._h3o_title && /^remove ALL |^clear the shots area/.test(b._h3o_title.src))) b.classList.add("h3sk-danger");
}
// skinperf (2026-10-27): a partial re-render (timeline redraw while dragging, status / budget pill, pick info, armed Clear)
// is decorated only inside the re-filled container, at once (before the next paint) - the whole editor is not re-walked.
function decoratePart(root, scope) {
  if (scope.nodeType !== 1 || !scope.isConnected || !root.contains(scope)) return;
  if (scope.querySelector(".h3md-note")) { schedule(root); return; }      // help toggle: keep the full pass
  if (root.hasAttribute("data-h3sk")) {
    if (scope.matches("button")) markDanger(scope);
    for (const b of scope.querySelectorAll("button")) markDanger(b);
    if (scope.querySelector(".sb") || scope.closest(".h3md-tl2"))
      root.querySelectorAll(".h3md-tl2 .sb").forEach((el, i) => {
        const c = TL_COLORS[i % TL_COLORS.length];
        el.style.background = `linear-gradient(180deg, ${c}, ${shade(c, -18)})`;
      });
  }
  translateTree(scope, locale());
}
function decorate(root) {
  const modern = ls.get(LS_SKIN, "modern") !== "classic";
  if (modern) root.setAttribute("data-h3sk", ""); else root.removeAttribute("data-h3sk");
  if (modern) root.style.setProperty("--s", String(scale()));
  else root.style.removeProperty("--s");
  // toolbar tools (re-added after every editor re-render)
  const bar = root.querySelector(".h3md-bar");
  if (bar && !bar.querySelector(".h3sk-tools")) bar.append(tools(root));
  else if (bar) refreshTools(bar.querySelector(".h3sk-tools"), root);
  if (modern) {
    for (const b of root.querySelectorAll("button")) markDanger(b);
    root.querySelectorAll(".h3md-tl2 .sb").forEach((el, i) => {
      const c = TL_COLORS[i % TL_COLORS.length];
      el.style.background = `linear-gradient(180deg, ${c}, ${shade(c, -18)})`;
    });
    // help toggle in front of the first note of a shots section
    for (const note of root.querySelectorAll(".h3md-note")) {
      const prev = note.previousElementSibling;
      if (prev && prev.classList?.contains("h3sk-helpbtn")) continue;
      const first = !(prev && prev.classList?.contains("h3md-note"));
      if (!first) continue;
      const b = document.createElement("button");
      b.className = "h3sk-helpbtn";
      b.style.cssText = "margin:0 0 6px 0;font-size:calc(12px*var(--s))";
      b.textContent = root.hasAttribute("data-h3help") ? T("ⓘ 收起说明", "ⓘ Hide help") : T("ⓘ 使用说明", "ⓘ Help");
      b.onclick = (e) => { e.stopPropagation(); root.toggleAttribute("data-h3help"); decorate(root); };
      note.before(b);
    }
    for (const b of root.querySelectorAll(".h3sk-helpbtn")) b.textContent = root.hasAttribute("data-h3help") ? T("ⓘ 收起说明", "ⓘ Hide help") : T("ⓘ 使用说明", "ⓘ Help");
  } else {
    root.querySelectorAll(".h3sk-helpbtn").forEach((b) => b.remove());
  }
  translateTree(root, locale());
}
function shade(hex, pct) {
  const n = parseInt(hex.slice(1), 16), f = (c) => Math.max(0, Math.min(255, Math.round(c * (1 + pct / 100))));
  return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
}
function scale() { const v = parseFloat(ls.get(LS_SCALE, "1.1")); return isFinite(v) ? Math.min(1.4, Math.max(0.9, v)) : 1.1; }
function tools(root) {
  const box = document.createElement("span");
  box.className = "h3sk-tools";
  const mk = (txt, title, fn) => { const b = document.createElement("button"); b.textContent = txt; b.title = title; b.onclick = (e) => { e.stopPropagation(); fn(); decorateAll(); }; return b; };
  box.append(
    mk("A−", "", () => ls.set(LS_SCALE, String(Math.round((scale() - 0.1) * 10) / 10))),
    Object.assign(document.createElement("span"), { className: "pct" }),
    mk("A+", "", () => ls.set(LS_SCALE, String(Math.round((scale() + 0.1) * 10) / 10))),
    mk("", "", () => ls.set(LS_SKIN, ls.get(LS_SKIN, "modern") === "classic" ? "modern" : "classic")));
  box.dataset.h3sk = "1";
  refreshTools(box, root);
  return box;
}
function refreshTools(box, root) {
  if (!box) return;
  const modern = ls.get(LS_SKIN, "modern") !== "classic";
  const [minus, pct, plus, skin] = box.children;
  minus.title = T("缩小界面", "smaller UI"); plus.title = T("放大界面", "larger UI");
  minus.style.display = plus.style.display = pct.style.display = modern ? "" : "none";
  pct.textContent = `${Math.round(scale() * 100)}%`;
  skin.textContent = modern ? T("经典界面", "Classic") : T("新界面", "Modern");
  skin.title = modern ? T("切回原来的界面样式", "switch back to the classic look") : T("切换到新界面样式", "switch to the modern look");
}

// two-click confirm for the quiet "Clear" buttons (no browser dialog)
function armClear(e) {
  const b = e.target.closest?.("button.h3sk-danger");
  if (!b || !b.closest("[data-h3sk]")) return;
  if (b.classList.contains("h3sk-armed")) { b.classList.remove("h3sk-armed"); return; }   // second click: let it through
  e.stopImmediatePropagation(); e.preventDefault();
  b.classList.add("h3sk-armed");
  const old = b.textContent;
  b.textContent = T("再点一次确认", "click again to confirm");
  setTimeout(() => { if (b.isConnected && b.classList.contains("h3sk-armed")) { b.classList.remove("h3sk-armed"); b.textContent = old; } }, 3000);
}

// ------------------------------------------------------------------ wiring
const seen = new WeakSet();
let pending = new Set(), raf = 0;
function schedule(root) {
  pending.add(root);
  if (raf) return;
  raf = setTimeout(() => { raf = 0; const rs = [...pending]; pending = new Set(); for (const r of rs) if (r.isConnected) { try { decorate(r); } catch (err) { console.error("[H3MD skin]", err); } } }, 30);
}
function watch(root) {
  if (seen.has(root)) return;
  seen.add(root);
  root.addEventListener("click", armClear, true);
  new MutationObserver((muts) => {
    // ignore our own text rewrites (characterData) and our own tools / help nodes – only structural re-renders matter
    const scopes = new Set();
    for (const m of muts) {
      if (m.type !== "childList" || ![...m.addedNodes].some((n) => !(n.nodeType === 1 && n.classList?.contains("h3sk-helpbtn")) && !(n.nodeType === 1 && n.dataset?.h3sk))) continue;
      if (m.target === root || pending.has(root)) { schedule(root); return; }   // full editor render(): unchanged path
      scopes.add(m.target);
    }
    for (const s of scopes) {
      if ([...scopes].some((o) => o !== s && o.contains(s))) continue;
      try { decoratePart(root, s); } catch (err) { console.error("[H3MD skin]", err); }
    }
  }).observe(root, { childList: true, subtree: true });
  schedule(root);
}
function decorateAll() { document.querySelectorAll(ROOT).forEach((r) => { watch(r); decorate(r); }); }

app.registerExtension({
  name: "H3MD.DirectorSkin",
  setup() {
    injectCss();
    // new editors (node added / workflow tab switched): a cheap 1 s poll instead of a document-wide observer
    setInterval(() => { for (const r of document.querySelectorAll(ROOT)) if (!seen.has(r)) watch(r); }, 1000);
    decorateAll();
    // follow a change of Comfy.Locale without a reload
    let last = locale();
    setInterval(() => { const l = locale(); if (l !== last) { last = l; decorateAll(); } }, 1500);
  },
});
