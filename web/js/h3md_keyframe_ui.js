// H3MD 0.5.91 (user 22:47): multi-person swap keyframes by clicks.
//  * H3MD Keyframe Import: an "Import package" button - pick the AI's .h3kf / .json, it is uploaded to
//    input/H3MD_keyframes/imports/ and the 'package' box is set (no copy and paste).
//  * H3MD Keyframe Redo: one button per keyframe (#1 ... #N, N from the last Export / Import / Recast run), chosen =
//    made again, the others are kept; "All" clears the choice; "Run" queues the workflow.
// The 'redo' / 'count' widgets stay (hidden), so widgets_values keep their places.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const IMPORT = "H3MDKeyframeImport";
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
