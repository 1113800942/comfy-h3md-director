// H3MD - canvas-drag performance helpers for the H3MD Director editor (h3md_director_ui.js). Written for H3MD.
// 2026-10-19: the editor's media library used to keep one live <video> per clip (9+ video layers inside a large DOM
// widget). While the canvas is panned the frontend moves the DOM widget every frame (left/top), so the whole editor and
// every video layer were re-painted / re-composited per frame -> GPU-bound stutter. Now:
//  (1) video tiles show a static poster: the same frame as before (t = 0.05 s, object-fit: cover), captured ONCE per
//      file with a detached <video> -> <canvas> -> blob URL (max 2 captures at a time; cached for the page session).
//      A live <video> exists only while the pointer hovers a tile (muted, looped preview) and is released on leave.
//      If a poster cannot be captured, the tile falls back to the old <video preload=metadata> thumbnail.
//  (2) while the canvas view (offset / zoom) or a Director node moves, <html> gets the class `h3md-canvas-moving`:
//      the editor root becomes its own compositor layer (will-change: transform), so moving it is a cheap layer
//      translate instead of a repaint; hover previews pause. Removed 150 ms after the last movement, so text is
//      re-rastered crisp at the current zoom.
// Nothing here changes the plan, widgets_values or node sizes.

export const MOVING_CLASS = "h3md-canvas-moving";
export const SETTLE_MS = 150;
export const POSTER_T = 0.05;          // same frame the old <video src="...#t=0.05"> thumbnail showed
export const POSTER_MAX = 360;         // px, longest side of the cached poster (tiles are ~120 x 45 css px)
const MAX_CAPTURES = 2;
const CAPTURE_TIMEOUT_MS = 10000;

// ------------------------------------------------------------------ posters
const posterCache = new Map();         // url -> Promise<string | null> (blob URL)
const queue = [];
let running = 0;

function pump() {
  while (running < MAX_CAPTURES && queue.length) {
    const job = queue.shift();
    running++;
    job().finally(() => { running--; pump(); });
  }
}

export function posterSize(w, h, max = POSTER_MAX) {
  if (!(w > 0) || !(h > 0)) return null;
  const k = Math.min(1, max / Math.max(w, h));
  return [Math.max(1, Math.round(w * k)), Math.max(1, Math.round(h * k))];
}

function capture(url) {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    let done = false, timer = 0;
    const finish = (result) => {
      if (done) return;
      done = true; clearTimeout(timer);
      try { v.removeAttribute("src"); v.load(); } catch { /* ignore */ }   // release the decoder at once
      resolve(result);
    };
    const draw = () => {
      try {
        const sz = posterSize(v.videoWidth, v.videoHeight);
        if (!sz) return finish(null);
        const c = document.createElement("canvas");
        c.width = sz[0]; c.height = sz[1];
        c.getContext("2d").drawImage(v, 0, 0, sz[0], sz[1]);
        c.toBlob((b) => finish(b ? URL.createObjectURL(b) : null), "image/jpeg", 0.9);
      } catch { finish(null); }
    };
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous";
    v.addEventListener("seeked", draw, { once: true });
    v.addEventListener("loadeddata", () => {
      const t = Math.min(POSTER_T, Math.max(0, (v.duration || 0) - 0.001));
      if (Math.abs(v.currentTime - t) < 1e-3) draw(); else v.currentTime = t;
    }, { once: true });
    v.addEventListener("error", () => finish(null), { once: true });
    timer = setTimeout(() => finish(null), CAPTURE_TIMEOUT_MS);
    v.src = url;
  });
}

// -> Promise<blob URL | null>; one capture per url per page session
export function posterURL(url) {
  if (!url) return Promise.resolve(null);
  if (!posterCache.has(url)) {
    posterCache.set(url, new Promise((resolve) => { queue.push(() => capture(url).then(resolve)); pump(); }));
  }
  return posterCache.get(url);
}

// a video tile's visual: <img class="tv"> showing the cached poster (falls back to the old <video> thumbnail)
export function videoThumb(url) {
  const img = document.createElement("img");
  img.className = "tv vposter";
  img.draggable = false;
  img.decoding = "async";
  img.alt = "";
  posterURL(url).then((src) => {
    if (src) { img.src = src; return; }
    const v = document.createElement("video");   // fallback = previous behaviour
    v.className = "tv"; v.muted = true; v.preload = "metadata"; v.playsInline = true; v.draggable = false;
    v.src = url + "#t=0.05";
    img.replaceWith(v);
  });
  return img;
}

// live muted preview only while the pointer is on the tile; released on leave
export function attachHoverPlay(tile, holder, url) {
  if (!tile || !holder || !url) return;
  let v = null;
  const release = () => {
    if (!v) return;
    try { v.pause(); v.removeAttribute("src"); v.load(); } catch { /* ignore */ }
    v.remove(); v = null;
  };
  tile.addEventListener("pointerenter", (e) => {
    if (v || (e.buttons & 1) || isMoving()) return;   // not while dragging the canvas / a tile
    v = document.createElement("video");
    v.className = "tv hov"; v.muted = true; v.loop = true; v.playsInline = true; v.preload = "auto";
    v.draggable = false;
    v.src = url + "#t=0.05";
    holder.append(v);
    v.play?.().catch(() => { /* autoplay refused: keeps the first frame */ });
  });
  tile.addEventListener("pointerleave", release);
  tile.addEventListener("dragstart", release);
  tile.__h3mdReleaseHover = release;
}

// ------------------------------------------------------------------ canvas-moving flag
let moving = false, settleT = 0, last = null;
const watched = new Set();             // Director nodes (their pos / size are part of the signature)
export const isMoving = () => moving;
export function watchNode(node) { if (node) watched.add(node); }
export function unwatchNode(node) { watched.delete(node); }

export function viewSignature(canvas, nodes = watched) {
  const ds = canvas?.ds;
  const s = [ds?.scale ?? 1, ds?.offset?.[0] ?? 0, ds?.offset?.[1] ?? 0];
  for (const n of nodes) s.push(n.pos?.[0] ?? 0, n.pos?.[1] ?? 0, n.size?.[0] ?? 0, n.size?.[1] ?? 0, n.flags?.collapsed ? 1 : 0);
  return s;
}
const same = (a, b) => !!a && !!b && a.length === b.length && a.every((x, i) => x === b[i]);

function setMoving(on) {
  if (moving === on) return;
  moving = on;
  document.documentElement.classList.toggle(MOVING_CLASS, on);
  if (on) for (const v of document.querySelectorAll(".h3md-root video.hov")) { try { v.pause(); } catch { /* ignore */ } }
  else for (const v of document.querySelectorAll(".h3md-root video.hov")) v.play?.().catch(() => {});
}

// called once per canvas frame (cheap: a few number compares)
export function frameCheck(canvas) {
  if (!watched.size) { last = null; return; }
  const sig = viewSignature(canvas);
  if (last && !same(sig, last)) {
    setMoving(true);
    clearTimeout(settleT);
    settleT = setTimeout(() => setMoving(false), SETTLE_MS);
  }
  last = sig;
}

let installed = null;
export function installMoveWatch(app) {
  const c = app?.canvas;
  if (!c || installed === c) return;
  installed = c;
  const prev = c.onDrawForeground;
  c.onDrawForeground = function () {
    const r = prev?.apply(this, arguments);
    try { frameCheck(c); } catch { /* never break drawing */ }
    return r;
  };
}

export const PERF_CSS = `
.${MOVING_CLASS} .h3md-root{will-change:transform}
.h3md-root{contain:layout paint}
.h3md-tile .mid .tv.hov{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:transparent;pointer-events:none}
.${MOVING_CLASS} .h3md-root .h3md-tile .tv.hov{visibility:hidden}
`;
