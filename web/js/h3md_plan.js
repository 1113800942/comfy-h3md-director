// H3 MultiSeg DualRes (H3MD) - plan model helpers for the Director editor.
// Pure functions, no DOM. Mirrors h3md_director.py parse_plan / compute_labels so the
// editor shows exactly what the backend will produce. Keep both files in sync.

export const FPS = 24;
export const MAX_IMAGES = 9;
export const MAX_VIDEOS = 3;
export const MAX_AUDIOS = 3;
export const PICK_LIMITS = { images: 4, videos: 2, audios: 2 };
const EPS = 1e-3;
const P = "H3MD Director: ";
export const KINDS = ["images", "videos", "audios"];
export const KIND_SINGULAR = { images: "image", videos: "video", audios: "audio" };

// ---------------------------------------------------------------- python-compatible helpers
export function pyRound(x) {
  const f = Math.floor(x);
  const d = x - f;
  if (Math.abs(d - 0.5) < 1e-12) return f % 2 === 0 ? f : f + 1;
  return Math.round(x);
}
export function pyBool(v) {
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === "object") return Object.keys(v).length > 0;
  return !!v;
}
function pyStr(v) {
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  return String(v);
}
export function num(v, d = null) {
  if (v === null || v === undefined) return d;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "string" && v.trim() === "") return d;
  const f = Number(v);
  return Number.isFinite(f) ? f : d;
}
function toInt(v) {
  const f = num(v, NaN);
  if (!Number.isFinite(f)) throw new Error(`invalid literal for int(): '${v}'`);
  return Math.trunc(f);
}
const f2 = (x) => Number(x).toFixed(2);

// ---------------------------------------------------------------- H3 geometry
export const isAlignedLength = (n) => Number.isInteger(n) && n >= 5 && (n - 5) % 17 === 0;
export const nearestAligned = (n) => 5 + 17 * Math.max(0, Math.trunc(pyRound((Number(n) - 5) / 17)));
export const validOverlap = (n) => n === 0 || isAlignedLength(n);
// User-set shot length -> NEAREST 5+17n (ties go up, minimum 5), no maximum (mirrors h3md_director.snap_length).
export const snapShotLength = (frames) => 5 + 17 * Math.max(0, Math.floor((Number(frames) - 5) / 17 + 0.5));
// Smallest 5+17n >= frames (core nodes_minimax_h3.align_frame_count rounds up the same way).
export const alignedUp = (frames) => { const f = Math.max(5, Math.ceil(Number(frames) - 1e-6)); return 5 + 17 * Math.floor((f - 5 + 16) / 17); };
export const videoTokens = (n) => (n <= 5 ? 2 : Math.floor((n - 5) / 17) * 5 + 2);
export const snap32 = (v) => Math.max(32, Math.trunc(pyRound(Math.trunc(v) / 32)) * 32);
export const SIZE_ALIGN = 64;   // video width/height (h3md_director.SIZE_ALIGN)
export const snapSize = (v) => Math.max(SIZE_ALIGN, Math.trunc(pyRound(Math.trunc(v) / SIZE_ALIGN)) * SIZE_ALIGN);
export const framesToSeconds = (n) => Number(n) / FPS;

// No maximum shot length and no automatic splitting (2026-10-20): every shot runs as ONE segment of the
// length the user set. 396 is only the default "max length" of the manual "Split shot…" dialog.
export const DEFAULT_MAX_SEGMENT = 362;
export const DEFAULT_SUB_OVERLAP = 39;
export const DEFAULT_SHOT_OVERLAP = 39;

// Recommended split (2026-10-22): a shot longer than 15 s (360 f) and than maxLength becomes FULL maxLength segments
// followed by the leftover tail as its own last segment (not merged, not redistributed); consecutive segments share
// `overlap` frames (included in each length) and together cover `length` exactly. Otherwise [length] (not split).
export function splitLengths(length, maxLength, overlap) {
  if (!maxLength || length <= Math.max(maxLength, 15 * FPS)) return [length];
  const k = 1 + Math.floor((length - maxLength) / (maxLength - overlap));   // full segments that fit
  const lengths = Array(k).fill(maxLength);
  const covered = k * maxLength - (k - 1) * overlap;
  if (covered < length) lengths.push(length - covered + overlap);   // the leftover tail (5+17n, > overlap)
  return lengths;
}
// Mirrors h3md_director.continuity_note (Python "{:.1f}" formatting).
export function pyFixed1(x) {
  // Python "{:.1f}": exact halves round to even
  const t = x * 10, f = Math.floor(t);
  if (Math.abs(t - f - 0.5) < 1e-9) return ((f % 2 === 0 ? f : f + 1) / 10).toFixed(1);
  return (Math.round(t) / 10).toFixed(1);
}
export function continuityNote(overlapFrames) {
  return `Continuity: the first ${pyFixed1(overlapFrames / FPS)} seconds are carried over unchanged from the end of ` +
    "the previous part. Continue seamlessly from that moment, keeping the same people, " +
    "appearance, positions, motion, lighting and camera framing, then follow the description below.";
}
// Automatic splitting was removed (2026-10-20): a legacy plan/shot maxSegmentLength (and subOverlap, subPrompts)
// still loads but is ignored, so this is always false and every shot is exactly one segment.
export function autoSplitOn(plan) {
  return false;
}
// Effective max length of shot i (0 = not split). Tolerant: returns 0 for invalid values.
export function shotMax(plan, i) {
  if (!autoSplitOn(plan)) return 0;
  const s = (plan.segments || [])[i] || {};
  const v = s.maxSegmentLength !== undefined && s.maxSegmentLength !== null ? s.maxSegmentLength : plan.maxSegmentLength;
  const ms = Math.trunc(num(v, 0) || 0);
  return isAlignedLength(ms) ? ms : 0;
}
export function subOverlapOf(plan) {
  const v = plan && plan.subOverlap !== undefined ? plan.subOverlap : DEFAULT_SUB_OVERLAP;
  return Math.trunc(num(v || 0, 0));
}
// Sub-segment lengths of shot i for display (tolerant; [length] when splitting is off/invalid).
export function shotSubLengths(plan, i) {
  const len = segmentLength((plan.segments || [])[i]) ?? 0;
  const ms = shotMax(plan, i), ov = subOverlapOf(plan);
  if (!ms || !isAlignedLength(len) || !isAlignedLength(ov) || ov >= ms) return [len];
  return splitLengths(len, ms, ov);
}

// Global is ALWAYS prepended to every segment prompt (mirrors h3md_director.resolve_prompt).
// globalMode is a legacy argument and ignored; an empty side simply drops out.
export function resolvePrompt(globalPrompt, globalMode, segPrompt) {
  const g = (globalPrompt || "").trim();
  const s = (segPrompt || "").trim();
  if (g && s) return g + "\n\n" + s;
  return s || g;
}

// ---------------------------------------------------------------- media maps
export function mediaMaps(plan) {
  const out = { images: {}, videos: {}, audios: {} };
  const m = (plan && plan.media) || {};
  for (const k of KINDS) {
    for (const it of m[k] || []) {
      if (it && typeof it === "object" && it.id && it.file && !(it.id in out[k])) {
        const e = { ...it };
        if (k === "videos") {
          e.duration = num(it.duration);
          e.hasAudio = pyBool(it.hasAudio === undefined ? true : it.hasAudio);
        }
        out[k][it.id] = e;
      }
    }
  }
  return out;
}

export function videoRef(v) {
  return typeof v === "string" ? { id: v } : (v || {});
}

// H3MD depth (2026-10-26): per-video toggles, mirror h3md_director.parse_plan.
// depth (default false): the video drives the depth ControlNet (H3MD Depth Control on the Loop); at most one per shot.
// H3MD audioui (2026-10-26): the `ref` toggle was removed - every video is an H3 reference (<Video N>, MAX_VIDEOS
// slot); a legacy ref=false is ignored (as parse_plan) and dropped on the next edit of that video's flags.  The
// soundtrack <Audio> follows only useSoundtrack / the shot's audio mode, never depth.
export function refOn(r) {
  return true;
}
export function legacyRefOff(r) {
  const v = videoRef(r);
  return v.ref !== undefined && !pyBool(v.ref);
}
export function depthOn(r) {
  return pyBool(videoRef(r).depth);
}
// Index of the shot's depth video (first with depth on, as the backend) or -1.
// 2026-10-27 sam3box: SAM3 box state (a legacy plan without the flag: on when it has a prompt)
export function sam3On(r) {
  const v = videoRef(r);
  return v.sam3 === undefined ? !!String(v.sam3Prompt || "").trim() : pyBool(v.sam3);
}
export function depthIndex(s) {
  return ((s && s.videos) || []).findIndex((r) => depthOn(r));
}
// Set flags on video idx of shot s; only NON-default values are written (old plans stay byte-identical).
// depth on clears depth (and its sam3Prompt) on every other video of the shot (single choice).
export function setVideoFlags(s, idx, flags) {
  s.videos = (s.videos || []).map(videoRef).map((v) => ({ ...v }));
  const v = s.videos[idx];
  if (!v) return s;
  delete v.ref;   // H3MD audioui: legacy ref flag dropped (ignored by the backend anyway)
  if ("depth" in flags) {
    if (flags.depth) {
      v.depth = true;
      s.videos.forEach((o, j) => { if (j !== idx) { delete o.depth; delete o.sam3; delete o.sam3Prompt; } });
    } else { delete v.depth; delete v.sam3; delete v.sam3Prompt; }
  }
  // 2026-10-27 sam3box: SAM3 box (depth videos only). ticked -> sam3 true (+ "person" when the text is empty);
  // unticked -> sam3 false, the text is kept for later but ignored (parse_plan drops it: no SAM3, nothing loaded)
  if ("sam3" in flags && v.depth === true) {
    if (flags.sam3) { v.sam3 = true; if (!String(v.sam3Prompt || "").trim()) v.sam3Prompt = "person"; }
    else v.sam3 = false;
  }
  if ("sam3Prompt" in flags) {
    const t = String(flags.sam3Prompt || "").trim();
    if (t && v.depth === true) v.sam3Prompt = t; else delete v.sam3Prompt;
  }
  return s;
}

// Reference start offset (set by Split) and clip duration, exactly as parse_plan reads them.
export function effectiveTrim(ref, info) {
  const srcDur = info ? num(info.duration) : null;
  const trimStart = Math.max(0.0, num(ref.trimStart, 0.0));
  return { trimStart, srcDur };
}

// Shot length that follows the shot's FIRST reference video: the used video duration (ref.useDuration, seconds,
// editable in the shot settings; default = the whole clip from its internal start offset to the clip end), at
// 24 fps, rounded UP to 5+17n; null when the shot has no video ref or the duration is unknown (its stored length
// is used). Mirrors h3md_director.video_shot_length.
export function videoShotLength(s, mediaVideos) {
  const c = videoContentFrames(s, mediaVideos);
  return c === null ? null : alignedUp(c);
}
// True content length (unrounded 24 fps frames) of a shot that follows its first reference video, or null.
// Mirrors h3md_director.video_content_frames.
export function videoContentFrames(s, mediaVideos) {
  for (const r of (s && s.videos) || []) {
    const v = videoRef(r);
    const info = mediaVideos[pyStr(v.id)];
    if (!info) continue;
    const d = num(info.duration);
    if (d === null) return null;
    const rem = d - Math.max(0, num(v.trimStart, 0));
    if (!(rem > 0)) return null;
    const u = num(v.useDuration, null);
    return (u !== null && u > 0 ? Math.min(u, rem) : rem) * FPS;
  }
  return null;
}
// Editable used video duration of shot i (seconds of its first reference video that the shot covers as new
// content; trimStart is only an internal start offset, set by "Split shot", not shown in the UI).
// -> { seconds, max, ref } or null when the shot does not follow a video.
export function shotUsedSeconds(plan, i) {
  const s = (plan.segments || [])[i];
  const mv = mediaMaps(plan).videos;
  const idx = ((s && s.videos) || []).findIndex((r) => mv[pyStr(videoRef(r).id)]);
  if (idx < 0) return null;
  const v = videoRef(s.videos[idx]);
  const d = num(mv[pyStr(v.id)].duration);
  if (d === null) return null;
  const max = d - Math.max(0, num(v.trimStart, 0));
  if (!(max > 0)) return null;
  const u = num(v.useDuration, null);
  return { seconds: u !== null && u > 0 ? Math.min(u, max) : max, max, idx };
}
// Set the used video duration of shot i (seconds, clamped to 5 frames .. the rest of the clip); the shot length
// follows it (applyVideoLengths). The whole rest of the clip removes the key (default).
export function setShotUsedSeconds(plan, i, seconds) {
  const u = shotUsedSeconds(plan, i);
  const sec = Number(seconds);
  if (!u || !Number.isFinite(sec)) return false;
  const s = plan.segments[i];
  s.videos = s.videos.map((r) => (typeof r === "string" ? { id: r } : r));
  const ref = s.videos[u.idx];
  const v = Math.max(5 / FPS, sec);
  if (v >= u.max - 1e-9) delete ref.useDuration;
  else ref.useDuration = v;
  applyVideoLengths(plan);
  syncStartFrames(plan);
  return true;
}
// Video-driven shot: a typed frame count (nearest 5+17n) sets the used duration to the new content it holds.
export function setShotUsedFrames(plan, i, frames) {
  const s = plan.segments[i];
  const f = snapShotLength(Number(frames));
  const req = requestedOverlap(s, i);
  const ov = i > 0 && req > 0 && validOverlap(req) ? req : 0;
  if (!Number.isFinite(f) || f - ov <= 0) return false;
  return setShotUsedSeconds(plan, i, (f - ov) / FPS);
}
export const MIN_AUTO_OVERLAP = 22;
// The overlap choices of EVERY continued shot (2026-10-17c): 0 = hard cut, 22 or 39 frames. Old plans with other
// 5+17k values (5, 56, 107 ...) still load and run as saved, with a warning; any edit snaps to these steps.
export const OVERLAP_STEPS = [0, 22, 39];
export const isStandardOverlap = (n) => OVERLAP_STEPS.includes(Math.trunc(Number(n)));
// Default overlap for a new continuation of a shot of `length` frames (largest step shorter than it).
export const defaultOverlap = (length) => [...OVERLAP_STEPS].reverse().find((k) => k < (Number(length) || 396)) || 0;
export function overlapCandidates(requested) {
  const out = [Math.trunc(requested)];
  for (let o = Math.trunc(requested) - 17; o >= MIN_AUTO_OVERLAP; o -= 17) out.push(o);
  return out;
}
// Continued shot that follows its reference video: length = alignedUp(overlap + content) (the first overlap
// frames are the previous shot's tail). Over maxLength: overlap 39 -> 22 (never below 22); still over ->
// "split" (auto-split on) or "too_long" (auto-split off, smallest overlap, warned). Mirrors continued_video_length.
export function continuedVideoLength(content, requested, maxLength, canSplit) {
  const cands = overlapCandidates(requested);
  for (const o of cands) {
    const L = alignedUp(o + content);
    if (!maxLength || L <= maxLength) return { length: L, overlap: o, status: o === cands[0] ? "ok" : "reduced" };
  }
  if (canSplit) return { length: alignedUp(cands[0] + content), overlap: cands[0], status: "split" };
  return { length: alignedUp(cands[cands.length - 1] + content), overlap: cands[cands.length - 1], status: "too_long" };
}
// No sliver splits (2026-10-17e), mirrors h3md_director.auto_video_length: a shot that does not fit the max even
// at the smallest overlap is only auto-split when that buys >= MIN_SPLIT_FRAMES (2 s, the shortest H3 reference)
// of clip beyond one capped shot and every piece carries >= that much new content; otherwise it is capped at the
// max (overlap 22 / 0) and the clip tail is dropped.
export const MIN_SPLIT_FRAMES = 48;
export function autoVideoLength(content, requested, maxLength, canSplit, subOverlap = DEFAULT_SUB_OVERLAP) {
  const req = Math.trunc(requested);
  const cands = req > 0 ? overlapCandidates(req) : [0];
  for (const o of cands) {
    const L = alignedUp(o + content);
    if (!maxLength || L <= maxLength) return { length: L, overlap: o, status: o === cands[0] ? "ok" : "reduced", dropped: 0 };
  }
  if (!canSplit) {
    if (req <= 0) return { length: alignedUp(content), overlap: 0, status: "ok", dropped: 0 };
    return { length: alignedUp(cands[cands.length - 1] + content), overlap: cands[cands.length - 1], status: "too_long", dropped: 0 };
  }
  const capOv = cands[cands.length - 1];
  const tail = content - (maxLength - capOv);
  const L = alignedUp(cands[0] + content);
  let worth = tail >= MIN_SPLIT_FRAMES;
  if (worth && isAlignedLength(subOverlap) && subOverlap > 0 && subOverlap < maxLength) {
    const pieces = splitLengths(L, maxLength, subOverlap);
    const news = [pieces[0] - cands[0], ...pieces.slice(1).map((x) => x - subOverlap)];
    worth = Math.min(...news) >= MIN_SPLIT_FRAMES;
  }
  if (worth) return { length: L, overlap: cands[0], status: "split", dropped: 0 };
  return { length: Math.trunc(maxLength), overlap: capOv, status: "capped", dropped: tail };
}
// The user's overlap for shot i (overlapRequest is kept when the stored overlapIn was shrunk to fit).
export function requestedOverlap(s, i) {
  const ov = Math.trunc(num((s && s.overlapIn) || 0, 0));
  if (i <= 0 || ov <= 0) return ov;
  return s.overlapRequest !== undefined && s.overlapRequest !== null ? Math.trunc(num(s.overlapRequest, ov)) : ov;
}
// Effective {length, overlap, content, status} of a video-driven shot (null when its stored length is used).
export function autoShotGeometry(plan, i, mediaVideos = null) {
  const segs = plan.segments || [];
  const s = segs[i];
  const mv = mediaVideos || mediaMaps(plan).videos;
  const content = videoContentFrames(s, mv);
  if (content === null) return null;
  const req = requestedOverlap(s, i);
  const cont = i > 0 && req > 0 && validOverlap(req);
  if (cont) {
    // no length cap (2026-10-20): the requested overlap is always kept, the new content follows it
    return { length: alignedUp(req + content), overlap: req, content, requested: req, status: "ok", max: 0, dropped: 0 };
  }
  return { length: alignedUp(content), overlap: Math.trunc(num((s && s.overlapIn) || 0, 0)), content, requested: req, status: "ok", max: 0, dropped: 0 };
}
export const shotLengthIsAuto = (plan, i) => videoShotLength((plan.segments || [])[i], mediaMaps(plan).videos) !== null;
// New-content frames of shot i (length minus the frames shared with the previous shot).
export function shotNewFrames(plan, i) {
  const s = (plan.segments || [])[i];
  const len = segmentLength(s) ?? 0;
  return i > 0 ? len - Math.trunc(num((s && s.overlapIn) || 0, 0)) : len;
}
// Editor: write every video-driven shot length (and a shrunk overlap) into the plan, then keep seams valid.
export function applyVideoLengths(plan) {
  const mv = mediaMaps(plan).videos;
  let changed = 0;
  (plan.segments || []).forEach((s, i) => {
    // the old per-reference window (trimDuration, "dur") no longer exists: drop it from older plans
    if (Array.isArray(s.videos)) s.videos.forEach((r) => {
      if (r && typeof r === "object" && "trimDuration" in r) { delete r.trimDuration; changed += 1; }
    });
    const g = s && typeof s === "object" ? autoShotGeometry(plan, i, mv) : null;
    if (g === null) return;
    if (segmentLength(s) !== g.length) {
      s.length = g.length; changed += 1;
      if ("endFrame" in s) delete s.endFrame;
    }
    if (i > 0 && g.requested > 0) {
      const cur = Math.trunc(num(s.overlapIn || 0, 0));
      if (cur !== g.overlap) { s.overlapIn = g.overlap; changed += 1; }
      if (g.overlap !== g.requested) s.overlapRequest = g.requested;
      else if ("overlapRequest" in s) { delete s.overlapRequest; changed += 1; }
    }
  });
  if (changed) clampSeams(plan);
  return changed;
}

// ---------------------------------------------------------------- per-file audio mode
// A shot "audios" entry is an id (= reference) or {id, mode: reference|locked|none, offset}.
// reference + locked files are passed as <Audio> refs (locked too, as shot-level locked mode always did),
// none files are not passed, at most one file is locked.  Shot-level audioMode "none" still drops all audio,
// a shot-level locked.source still works while no file is locked.  Mirrors resolve_shot_audio() in Python.
export const AUDIO_FILE_MODES = ["reference", "locked", "none"];
export function audioEntry(x) {
  if (x && typeof x === "object") return { id: pyStr(x.id), mode: x.mode ? String(x.mode) : "reference", offset: Math.max(0, num(x.offset, 0) ?? 0) };
  return { id: String(x), mode: "reference", offset: 0 };
}
export const isPerFileAudio = (s) => Array.isArray(s && s.audios) && s.audios.some((x) => x && typeof x === "object");
// -> { audioMode, audios: [ids passed as refs], locked: {kind,id,offset}|null, entries, errors: [msg] }
export function shotAudio(s, media, n = 0) {
  const errors = [];
  const raw = s && s.audioMode ? String(s.audioMode) : "reference";
  const entries = ((s && s.audios) || []).map(audioEntry);
  for (const e of entries) if (!AUDIO_FILE_MODES.includes(e.mode)) errors.push(`segment ${n} audio '${e.id}' mode must be reference|locked|none`);
  for (const e of entries) if (media && media.audios && !(e.id in media.audios)) errors.push(`segment ${n} references unknown audio '${e.id}'`);
  const fileLocked = entries.filter((e) => e.mode === "locked");
  if (fileLocked.length > 1) errors.push(`segment ${n} has ${fileLocked.length} locked audios (max 1)`);
  const audios = entries.filter((e) => e.mode !== "none").map((e) => e.id);
  if (audios.length > MAX_AUDIOS) errors.push(`segment ${n} has ${audios.length} audios (max ${MAX_AUDIOS})`);
  if (raw === "none") return { audioMode: "none", audios, locked: null, entries, errors };
  if (fileLocked.length) return { audioMode: "locked", audios, locked: { kind: "audio", id: fileLocked[0].id, offset: fileLocked[0].offset }, entries, errors };
  if (raw === "locked") {
    const lk = (s && s.locked) || {};
    const source = lk.source ? String(lk.source) : "";
    if (!source) return { audioMode: "reference", audios, locked: null, entries, errors };   // leftover lock (mirrors parse_plan)
    const c = source.indexOf(":");
    const kind = c < 0 ? source : source.slice(0, c);
    const refId = c < 0 ? "" : source.slice(c + 1);
    const ok = media && ((kind === "video" && refId in media.videos) || (kind === "audio" && refId in media.audios));
    if (!ok) { errors.push(`segment ${n} audioMode 'locked' needs locked.source 'video:<id>' or 'audio:<id>' (got '${source}')`); return { audioMode: "locked", audios, locked: null, entries, errors }; }
    let offset = Math.max(0, num(lk.offset, 0.0));
    // H3MD lockrule (2026-10-26): a video lock's start follows the video's start by rule (mirrors parse_plan)
    const lref = kind === "video" ? ((s && s.videos) || []).map(videoRef).find((v) => pyStr(v.id) === refId) : null;
    if (lref) offset = videoLockOffset(effectiveTrim(lref, null).trimStart, (s && s.overlapIn) || 0);
    return { audioMode: "locked", audios, locked: { kind, id: refId, offset }, entries, errors };
  }
  return { audioMode: raw, audios, locked: null, entries, errors };
}
// H3MD lockrule (2026-10-26): locked start of a video lock from the video's start (seconds) and the shot's overlapIn:
// start - overlapIn/24 (overlapIn 0 -> start).  H3MD syncfix (2026-10-26): no max(0) clamp; a negative value means
// leading silence (inside the overlap the join drops), so frame k always hears start + (k - overlapIn)/24.
// Mirrors h3md_director.video_lock_offset.
export function videoLockOffset(start, overlapIn) {
  const t = Math.max(0, num(start, 0));
  const ov = Math.trunc(num(overlapIn, 0));
  return ov > 0 ? t - ov / FPS : t;
}
// H3MD lockexcl (2026-10-26): id of the video whose own sound is the shot's locked track (hard-copied into the
// segment audio), else null.  That video's soundtrack is never also an <Audio> reference (lock and soundtrack are
// mutually exclusive; mirrors h3md_director.locked_video_id).  Accepts raw plan shots, backend segments
// ({locked: {kind, id}}) and an explicit seg.lockedVideo.
export function videoLockId(s) {
  if (!s) return null;
  if (s.lockedVideo !== undefined) return s.lockedVideo;
  if (String(s.audioMode || "") !== "locked" || !s.locked) return null;
  if (s.locked.kind !== undefined) return s.locked.kind === "video" ? pyStr(s.locked.id) : null;
  if (((s.audios || [])).some((x) => x && typeof x === "object" && x.mode === "locked")) return null;   // a locked file wins
  const src = String(s.locked.source || "");
  return src.startsWith("video:") ? src.slice(6) : null;
}
// H3MD lockexcl (2026-10-26): per-video audio of video idx in shot i; flags {soundtrack: bool} and/or {lock: bool}.
// lock and soundtrack are mutually exclusive (ticking one unticks the other); neither = the video's sound is not used.
// lock = shot-level locked.source "video:<id>" with offset = the video's start (trimStart).
export function setVideoAudio(plan, i, idx, flags) {
  let s = plan.segments[i];
  if (!s) return s;
  // ticking either box on an older shot-level "none" shot first converts it to per-file modes (same result)
  if ((flags.lock || flags.soundtrack) && !isPerFileAudio(s) && s.audioMode === "none") { toPerFileAudio(plan, i); s = plan.segments[i]; }
  s.videos = (s.videos || []).map(videoRef).map((v) => ({ ...v }));
  const v = s.videos[idx];
  if (!v) return s;
  const id = pyStr(v.id);
  const t = effectiveTrim(v, null).trimStart;
  v.trimStart = t;
  const wasLocked = videoLockId(s) === id;
  const unlock = () => { delete s.audioMode; delete s.locked; };
  if ("lock" in flags) {
    if (flags.lock) {
      if (isPerFileAudio(s)) for (const o of s.audios) if (o && typeof o === "object" && o.mode === "locked") { o.mode = "reference"; delete o.offset; }
      s.audioMode = "locked"; s.locked = { source: `video:${id}`, offset: videoLockOffset(t, s.overlapIn) };   // H3MD lockrule
      v.useSoundtrack = false;
    } else if (wasLocked) unlock();
  }
  if ("soundtrack" in flags) {
    v.useSoundtrack = !!flags.soundtrack;
    if (flags.soundtrack && videoLockId(s) === id) unlock();
  }
  return s;
}
// H3MD lockexcl/lockrule: recompute a video lock's offset from the locked video's start by rule (videoLockOffset;
// call after editing a video's trimStart or the shot's overlapIn).
export function syncVideoLockOffset(s) {
  const id = videoLockId(s);
  if (!id || !s.locked || s.locked.kind !== undefined) return;
  const r = (s.videos || []).map(videoRef).find((v) => pyStr(v.id) === id);
  if (r) s.locked = { ...s.locked, offset: videoLockOffset(effectiveTrim(r, null).trimStart, s.overlapIn) };
}

// Official order: images, then per video (<Audio> soundtrack right before its <Video>), then standalone audio.
// seg fields may be raw plan values; unknown ids are labelled with missing=true.
export function computeLabels(seg, media) {
  const labels = [];
  let pic = 0, vid = 0, aud = 0;
  const audioMode = String(seg.audioMode || "reference");
  const audioAllowed = audioMode !== "none";
  const refAudios = ((seg.audios || []).map(audioEntry)).filter((e) => e.mode !== "none").map((e) => e.id);
  const lockedVid = audioAllowed ? videoLockId(seg) : null;   // H3MD lockexcl: the locked video's sound gets no <Audio N>
  for (const raw of seg.images || []) {
    const id = String(raw);
    const info = media.images[id];
    pic += 1;
    labels.push({ tag: `<Picture ${pic}>`, kind: "image", id, file: info ? info.file : null, missing: !info });
  }
  for (const r of seg.videos || []) {
    const ref = videoRef(r);
    // H3MD audioui (2026-10-26): every video is a <Video N>; its soundtrack depends only on useSoundtrack / audioMode
    const id = pyStr(ref.id);
    const info = media.videos[id];
    const useSt = pyBool(ref.useSoundtrack === undefined ? true : ref.useSoundtrack);
    const hasAudio = info ? info.hasAudio !== false : true;
    const soundtrack = audioAllowed && useSt && id !== lockedVid && hasAudio;
    if (soundtrack) {
      aud += 1;
      labels.push({ tag: `<Audio ${aud}>`, kind: "video_soundtrack", id, file: info ? info.file : null, missing: !info });
    }
    // H3MD ctlonly (2026-10-30): a depth video drives only the ControlNet - no <Video N> (mirror compute_labels)
    if (depthOn(r) && !soundtrack) continue;
    vid += 1;
    const t = effectiveTrim(ref, info);
    const end = t.trimStart + (segmentLength(seg) ?? 0) / FPS;   // full reference video from trimStart, capped by the segment length
    labels.push({ tag: `<Video ${vid}>`, kind: "video", id, file: info ? info.file : null, missing: !info,
                  window: [t.trimStart, t.srcDur === null ? end : Math.min(t.srcDur, end)] });
  }
  if (audioAllowed) {
    for (const id of refAudios) {
      const info = media.audios[id];
      aud += 1;
      labels.push({ tag: `<Audio ${aud}>`, kind: "audio", id, file: info ? info.file : null, missing: !info });
    }
  }
  return labels;
}

// Segment frame layout (startFrame from previous lengths minus overlap), tolerant of bad values.
export function segmentLength(s) {
  if (!s || typeof s !== "object") return null;
  let length = s.length;
  if ((length === undefined || length === null) && s.endFrame != null && s.startFrame != null) {
    length = num(s.endFrame, 0) - num(s.startFrame, 0);
  }
  if (length === undefined || length === null) return null;
  const f = num(length, NaN);
  return Number.isFinite(f) ? Math.trunc(f) : null;
}
export function layout(plan) {
  const segs = (plan && plan.segments) || [];
  const out = [];
  let prevStart = 0, prevLen = 0;
  segs.forEach((s, i) => {
    const length = segmentLength(s) ?? 0;
    const overlap = i === 0 ? 0 : Math.trunc(num((s && s.overlapIn) || 0, 0));
    const start = i === 0 ? 0 : prevStart + prevLen - overlap;
    out.push({ index: i, startFrame: start, length, overlapIn: overlap, endFrame: start + length });
    prevStart = start; prevLen = length;
  });
  return out;
}

// ---------------------------------------------------------------- validation
// Returns {errors:[{seg, msg}], warnings:[{seg, msg, fix?}], segments:[{startFrame,length,labels,prompt}], width, height, totalFrames}
// errors[] order follows parse_plan, so errors[0].msg equals the exception the backend raises
// (for plans whose failure is decidable without reading media files).
// probes: optional {fileName: {exists, hasAudio, duration}} from /h3md/probe.
export function validatePlan(raw, opts = {}) {
  const errors = [], warnings = [];
  const err = (seg, msg) => errors.push({ seg, msg: P + msg });
  const warn = (seg, msg, fix) => warnings.push({ seg, msg, fix });
  const res = { errors, warnings, segments: [], width: 0, height: 0, totalFrames: 0, notes: [] };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) { err(null, "plan_json must be a JSON object"); return res; }
  try {
    const version = raw.version === undefined ? 1 : toInt(raw.version);
    if (version !== 1) err(null, `unsupported plan version ${version} (expected 1)`);
  } catch (e) { err(null, `version: ${e.message}`); }
  try {
    const fps = raw.fps === undefined ? FPS : toInt(raw.fps);
    if (fps !== FPS) err(null, "MiniMax H3 plans must use fps 24");
  } catch (e) { err(null, `fps: ${e.message}`); }

  const nodeW = Math.trunc(num(opts.width, 0) || 0), nodeH = Math.trunc(num(opts.height, 0) || 0);
  let w = nodeW > 0 ? nodeW : Math.trunc(num(raw.width, 0) || 0);
  let h = nodeH > 0 ? nodeH : Math.trunc(num(raw.height, 0) || 0);
  if (w <= 0 || h <= 0) err(null, "width/height missing (set them in plan_json or on the node)");
  else if (w % SIZE_ALIGN || h % SIZE_ALIGN) {   // same rule as h3md_director.SIZE_ALIGN (exact half-size low-res pass)
    const sw = snapSize(w), sh = snapSize(h);
    res.notes.push(`size ${w}x${h} snapped to ${sw}x${sh} (multiple of ${SIZE_ALIGN}: exact half-size low-res pass)`);
    warn(null, `size ${w}x${h} will be snapped to ${sw}x${sh} (multiple of ${SIZE_ALIGN})`);
    w = sw; h = sh;
  }
  res.width = w; res.height = h;

  const mediaRaw = raw.media || {};
  const media = { images: {}, videos: {}, audios: {} };
  for (const kind of KINDS) {
    for (const item of mediaRaw[kind] || []) {
      if (!item || typeof item !== "object" || Array.isArray(item) || !pyBool(item.id) || !pyBool(item.file)) {
        err(null, `every media.${kind} entry needs 'id' and 'file'`); continue;
      }
      if (item.id in media[kind]) { err(null, `duplicate media id '${item.id}' in ${kind}`); continue; }
      const e = { ...item };
      if (kind === "videos") {
        e.duration = num(item.duration);
        e.hasAudio = pyBool(item.hasAudio === undefined ? true : item.hasAudio);
      }
      media[kind][item.id] = e;
    }
  }
  const globalPrompt = raw.globalPrompt ? String(raw.globalPrompt) : "";
  const globalMode = "prepend";   // legacy plan key, ignored (global is always prepended)

  // 2026-10-20: no maximum shot length, no automatic splitting. Legacy split keys (plan/shot maxSegmentLength,
  // subOverlap, shot subPrompts) are accepted with any value and ignored (mirrors parse_plan).
  const autoSplit = false, subOverlap = DEFAULT_SUB_OVERLAP, planMax = 0;
  const legacy = ["maxSegmentLength", "subOverlap"].filter((k) => raw[k] !== undefined && raw[k] !== null);
  res.autoSplit = autoSplit; res.subOverlap = subOverlap; res.maxSegmentLength = planMax;

  const segsRaw = raw.segments || [];
  if (!segsRaw.length) err(null, "plan has no segments");
  const segments = res.segments;
  let lengthsMoved = false; // a stored length was replaced by its video length: stored startFrames are stale
  segsRaw.forEach((s, i) => {
    const n = i + 1;
    if (!s || typeof s !== "object" || Array.isArray(s)) { err(i, `segment ${n} must be an object`); segments.push(null); return; }
    let length = segmentLength(s);
    const shotMaxLen = 0;
    if (s.maxSegmentLength !== undefined && s.maxSegmentLength !== null && !legacy.includes("maxSegmentLength")) legacy.push("maxSegmentLength");
    if (Array.isArray(s.subPrompts) && s.subPrompts.some((x) => String(x).trim())) res.notes.push(`segment ${n} subPrompts ignored (no automatic splitting): the shot prompt is used`);
    let overlap = Math.trunc(num(s.overlapIn || 0, 0));
    const requested = requestedOverlap(s, i);
    const content = videoContentFrames(s, media.videos);
    if (i > 0 && validOverlap(requested) && !isStandardOverlap(requested)) {
      res.notes.push(`WARNING segment ${n} overlapIn ${requested} is not one of 0/22/39 (older plan); kept as saved`);
      warn(i, `overlap ${requested}f is not one of the standard steps 0 / 22 / 39 (older plan): it still runs as saved; pick 0, 22 or 39 to change it`);
    }
    let autoLen = null, autoStatus = null;
    if (content !== null) {
      const cont = i > 0 && requested > 0 && validOverlap(requested);
      if (cont) {
        // no length cap: the requested overlap is always kept, the new content follows the overlap
        autoLen = alignedUp(requested + content); autoStatus = "ok";
        if (i > 0 && requested !== overlap) lengthsMoved = true;
        overlap = requested;
      } else autoLen = alignedUp(content);
      if (length !== null && length !== autoLen) {
        lengthsMoved = true;
        const what = i > 0 && overlap > 0 ? `overlap ${overlap} + ${f2(content / FPS)}s new content` : "its first reference video duration";
        res.notes.push(`segment ${n} length ${length} -> ${autoLen} (follows ${what}, rounded up to 5+17n)`);
      }
      length = autoLen;
    }
    if (length === null) { err(i, `segment ${n} needs 'length' (frames, 5+17n)`); length = 0; }
    else if (!isAlignedLength(length)) {
      if (!Number.isFinite(Number(length))) err(i, `segment ${n} length ${length} is not a number`);
      else {
        const snapped = snapShotLength(length);
        res.notes.push(`segment ${n} length ${length} -> ${snapped} (snapped to the nearest H3 5+17n frame count)`);
        length = snapped; lengthsMoved = true;
      }
    }
    let start = 0;
    const prev = [...segments].reverse().find((x) => x) || null;
    if (i === 0) {
      if (overlap) err(i, "segment 1 cannot have overlapIn (no previous segment)");
    } else if (prev) {
      if (!validOverlap(overlap)) {
        err(i, `segment ${n} overlapIn ${overlap} must be 0 or 5+17k (nearest: ${nearestAligned(overlap)}); 1-frame overlap is not supported`);
      } else if (overlap >= Math.min(prev.length, length)) {
        err(i, `segment ${n} overlapIn ${overlap} must be shorter than both segment lengths (${prev.length}, ${length})`);
      }
      start = prev.startFrame + prev.length - overlap;
    }
    if (s.startFrame !== undefined && s.startFrame !== null && Math.trunc(num(s.startFrame, NaN)) !== start && !lengthsMoved) {
      err(i, `segment ${n} startFrame ${s.startFrame} disagrees with previous end - overlapIn = ${start}`);
    }
    const subPrompts = null;   // legacy key, ignored (no automatic splitting)

    const audioMode = s.audioMode ? String(s.audioMode) : "reference";
    if (!["reference", "locked", "none"].includes(audioMode)) err(i, `segment ${n} audioMode must be reference|locked|none`);

    const images = (s.images || []).map(String);
    for (const id of images) if (!(id in media.images)) err(i, `segment ${n} references unknown image '${id}'`);
    if (images.length > MAX_IMAGES) err(i, `segment ${n} has ${images.length} images (max ${MAX_IMAGES})`);

    let nVideos = 0;
    for (const r of s.videos || []) {
      const v = videoRef(r);
      const vid = pyStr(v.id);
      nVideos += 1;   // H3MD audioui: every video takes a MAX_VIDEOS slot (ref flag removed)
      if (!(vid in media.videos)) { err(i, `segment ${n} references unknown video '${vid}'`); continue; }
      if (legacyRefOff(v)) warn(i, `video '${vid}' has legacy ref=false; ignored (ref option removed): it is used as a <Video N> reference again (remove the video from the shot to drop it)`);
    }
    if (nVideos > MAX_VIDEOS) err(i, `segment ${n} has ${nVideos} videos (max ${MAX_VIDEOS})`);

    const sa = shotAudio(s, media, n);
    for (const m of sa.errors) err(i, m);
    if (sa.locked && sa.locked.kind === "video" && media.videos[sa.locked.id] && media.videos[sa.locked.id].hasAudio === false) warn(i, `locked source video '${sa.locked.id}' is marked hasAudio=false`);
    const prompt = resolvePrompt(globalPrompt, globalMode, s.prompt || "");
    if (!prompt) err(i, `segment ${n} has no prompt (segment prompt and globalPrompt empty)`);
    const lockedVideo = sa.locked && sa.locked.kind === "video" ? sa.locked.id : null;
    if (lockedVideo) {   // H3MD lockexcl: mirror parse_plan's notes for a video lock in this shot
      const lref = (s.videos || []).map(videoRef).find((v) => pyStr(v.id) === lockedVideo);
      if (lref && pyBool(lref.useSoundtrack === undefined ? true : lref.useSoundtrack))
        res.notes.push(`segment ${n}: video '${lockedVideo}' is locked; its soundtrack is not also passed as an <Audio> reference (lock and soundtrack are exclusive)`);
    }
    const labels = computeLabels({ ...s, audioMode: sa.audioMode, audios: sa.audios, lockedVideo }, media);

    // editor-only hints (the backend does not check these at plan time)
    const counts = { images: images.length, videos: nVideos, audios: labels.filter((l) => l.kind === "audio").length };
    for (const k of KINDS) {
      if (counts[k] > PICK_LIMITS[k]) warn(i, `${counts[k]} ${k}: H3MD Segment Pick only outputs ${PICK_LIMITS[k]}; use H3MD Segment Encode (takes all)`);
    }
    const have = new Set(labels.map((l) => l.tag));
    const used = new Set((prompt.match(/<(Picture|Video|Audio) \d+>/g) || []));
    for (const tag of used) if (!have.has(tag)) warn(i, `prompt mentions ${tag} but this segment has no such reference`);
    for (const l of labels) if (!l.missing && !used.has(l.tag)) warnings.push({ seg: i, level: "info", msg: `${l.tag} (${l.id}) is not mentioned in the prompt` });
    let locked = null;
    if (sa.locked) locked = { offset: sa.locked.offset };
    segments.push({ index: i, startFrame: start, length, overlapIn: overlap, newFrames: length - overlap,
                    contentFrames: content, autoStatus, audioMode: sa.audioMode, prompt, labels,
                    maxLen: shotMaxLen, subPrompts: Array.isArray(subPrompts) ? subPrompts : [], locked });
  });

  if (legacy.length) res.notes.push(`${legacy.join("/")} ignored (older plan): shots are never split, each runs at its own length`);
  // ---- runnable segments: one per shot (mirrors parse_plan; only for an otherwise valid plan)
  res.expanded = [];
  if (!errors.length && segments.length) {
    let prevLastLen = null;
    for (const sh of segments) {
      const n = sh.index + 1;
      const lengths = autoSplit ? splitLengths(sh.length, sh.maxLen, subOverlap) : [sh.length];
      if (autoSplit && sh.index > 0 && sh.overlapIn > 0 && sh.overlapIn >= Math.min(prevLastLen, lengths[0])) {
        err(sh.index, `segment ${n} overlapIn ${sh.overlapIn} must be shorter than the auto sub-segments it joins (${prevLastLen}, ${lengths[0]}); lower overlapIn or raise maxSegmentLength`);
        break;
      }
      sh.subs = [];
      let st = sh.startFrame;
      lengths.forEach((len, j) => {
        if (j) st = st + lengths[j - 1] - subOverlap;
        const ov = j === 0 ? sh.overlapIn : subOverlap;
        const override = j < sh.subPrompts.length ? sh.subPrompts[j] : "";
        let prompt = (override || "").trim() ? resolvePrompt(globalPrompt, globalMode, override) : sh.prompt;
        if (opts.continuity && ov > 0) prompt = continuityNote(ov) + "\n\n" + prompt;
        const e = { index: res.expanded.length, startFrame: st, length: len, overlapIn: ov, newFrames: len - ov, prompt, labels: sh.labels };
        if (autoSplit) Object.assign(e, { shot: sh.index, sub: j, subCount: lengths.length,
          lockedOffset: sh.locked ? sh.locked.offset + (st - sh.startFrame) / FPS : null });
        sh.subs.push(e.index);
        res.expanded.push(e);
      });
      prevLastLen = lengths[lengths.length - 1];
    }
    if (errors.length) res.expanded = [];
  }

  // file checks from probes (backend fails at run time for these)
  const probes = opts.probes || {};
  for (const kind of KINDS) {
    for (const id of Object.keys(media[kind])) {
      const it = media[kind][id];
      const pr = probes[it.file];
      if (!pr) continue;
      if (pr.exists === false) { warnings.push({ seg: null, level: "error", msg: `${KIND_SINGULAR[kind]} '${id}': file not found in input folder: ${it.file}` }); continue; }
      if (kind === "videos") {
        if (pr.hasAudio === false && it.hasAudio !== false) {
          warn(null, `video '${id}' has no audio track but hasAudio is true (labels would shift / encode fails)`, { kind, id, set: { hasAudio: false } });
        } else if (pr.hasAudio === true && it.hasAudio === false) {
          warn(null, `video '${id}' has an audio track but hasAudio is false`, { kind, id, set: { hasAudio: true } });
        }
        if (pr.duration != null && (it.duration == null || Math.abs(pr.duration - it.duration) > 0.05)) {
          warn(null, `video '${id}' duration in plan ${it.duration == null ? "unset" : f2(it.duration) + "s"}, file is ${f2(pr.duration)}s`, { kind, id, set: { duration: pr.duration } });
        }
      }
    }
  }
  const valid = segments.filter((x) => x);
  if (valid.length) { const l = valid[valid.length - 1]; res.totalFrames = l.startFrame + l.length; }
  if (autoSplit) {
    for (const sh of valid) {
      const subs = splitLengths(sh.length, sh.maxLen, subOverlap);
      if (isAlignedLength(sh.length) && subs.length > 1) warnings.push({ seg: sh.index, level: "info", msg: `auto-split into ${subs.length} sub-segments: ${subs.join(" + ")} frames (overlap ${subOverlap}f each)` });
    }
  }
  return res;
}

// ---------------------------------------------------------------- edit operations (mutate plan in place)
export function newPlan() {
  return { version: 1, fps: 24, width: 1472, height: 1088, globalPrompt: "", globalMode: "prepend",
           media: { images: [], videos: [], audios: [] },
           segments: [{ length: 396, overlapIn: 0, prompt: "", images: [], videos: [], audios: [], audioMode: "reference" }] };
}
export function ensureShape(plan) {
  plan.media = plan.media && typeof plan.media === "object" ? plan.media : {};
  for (const k of KINDS) if (!Array.isArray(plan.media[k])) plan.media[k] = [];
  if (!Array.isArray(plan.segments)) plan.segments = [];
  return plan;
}
export function uniqueId(plan, kind, base) {
  const ids = new Set((plan.media[kind] || []).map((x) => x && x.id));
  const clean = String(base || KIND_SINGULAR[kind]).replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 24) || KIND_SINGULAR[kind];
  if (!ids.has(clean)) return clean;
  for (let k = 2; ; k++) if (!ids.has(`${clean}_${k}`)) return `${clean}_${k}`;
}
export function addMedia(plan, kind, file, extra = {}) {
  ensureShape(plan);
  const base = String(file).split("/").pop();
  const item = { id: uniqueId(plan, kind, base), file, ...extra };
  plan.media[kind].push(item);
  return item;
}
const lockedPrefix = { videos: "video:", audios: "audio:" };
export function renameMedia(plan, kind, oldId, newId) {
  newId = String(newId || "").trim();
  if (!newId || newId === oldId) return false;
  if ((plan.media[kind] || []).some((x) => x.id === newId)) throw new Error(`id '${newId}' already exists`);
  const item = plan.media[kind].find((x) => x.id === oldId);
  if (!item) return false;
  item.id = newId;
  for (const s of plan.segments || []) {
    if (!s || typeof s !== "object") continue;
    if (kind === "videos") {
      s.videos = (s.videos || []).map((v) => (typeof v === "string" ? (v === oldId ? newId : v) : (v.id === oldId ? { ...v, id: newId } : v)));
    } else if (kind === "audios" && Array.isArray(s.audios)) {
      s.audios = s.audios.map((x) => (x && typeof x === "object" ? (String(x.id) === oldId ? { ...x, id: newId } : x) : (String(x) === oldId ? newId : x)));
    } else if (Array.isArray(s[kind])) {
      s[kind] = s[kind].map((x) => (String(x) === oldId ? newId : x));
    }
    if (lockedPrefix[kind] && s.locked && s.locked.source === lockedPrefix[kind] + oldId) s.locked.source = lockedPrefix[kind] + newId;
  }
  return true;
}
export function mediaUsage(plan, kind, id) {
  const out = [];
  (plan.segments || []).forEach((s, i) => {
    if (!s) return;
    const ids = kind === "videos" ? (s.videos || []).map((v) => videoRef(v).id) : kind === "audios" ? (s.audios || []).map((x) => audioEntry(x).id) : (s[kind] || []).map(String);
    if (ids.includes(id) || (lockedPrefix[kind] && s.locked && s.locked.source === lockedPrefix[kind] + id)) out.push(i);
  });
  return out;
}
export function removeMedia(plan, kind, id) {
  plan.media[kind] = (plan.media[kind] || []).filter((x) => x.id !== id);
  for (const s of plan.segments || []) {
    if (!s) continue;
    if (kind === "videos") s.videos = (s.videos || []).filter((v) => videoRef(v).id !== id);
    else if (kind === "audios" && Array.isArray(s.audios)) s.audios = s.audios.filter((x) => audioEntry(x).id !== id);
    else if (Array.isArray(s[kind])) s[kind] = s[kind].filter((x) => String(x) !== id);
    // a lock on the removed file is dropped (back to reference); an empty source used to fail at queue time
    if (lockedPrefix[kind] && s.locked && s.locked.source === lockedPrefix[kind] + id) { delete s.audioMode; delete s.locked; }
  }
}
// H3MD noref (2026-10-29): reference-only shot state never outlives its references.  A lock whose source file is
// gone is dropped; a shot with no images, videos, audios or RefMods loses every reference setting (audio lock) and
// is back to a plain text shot.  Prompt, length, overlap and an explicit audioMode "none" are the user's input
// and stay.  Called on every commit and when a plan is loaded.
export function clearOrphanRefState(plan) {
  if (!plan || !Array.isArray(plan.segments)) return 0;
  const media = plan.media || {};
  const has = (kind, id) => (media[kind] || []).some((x) => x && x.id === id);
  let changed = 0;
  for (const s of plan.segments) {
    if (!s || typeof s !== "object") continue;
    if (s.locked) {
      const src = String(s.locked.source || "");
      const c = src.indexOf(":");
      const kind = c < 0 ? "" : src.slice(0, c), id = c < 0 ? "" : src.slice(c + 1);
      const ok = (kind === "video" && has("videos", id)) || (kind === "audio" && has("audios", id));
      if (!ok) { delete s.locked; if (s.audioMode === "locked") delete s.audioMode; changed++; }
    }
    const n = (k) => (Array.isArray(s[k]) ? s[k].length : 0);
    if (!n("images") && !n("videos") && !n("audios") && !n("refmods")) {
      if (s.locked || s.audioMode === "locked") { delete s.locked; if (s.audioMode === "locked") delete s.audioMode; changed++; }
    }
  }
  // the face-refine ID Ref goes with the last shot that uses its picture
  if (plan.idRef && !plan.segments.some((s) => s && (s.images || []).map(String).includes(String(plan.idRef)))) {
    delete plan.idRef; changed++;
  }
  return changed;
}
export function syncStartFrames(plan) {
  // startFrame is optional; keep it consistent only where the plan already carries it.
  const lay = layout(plan);
  (plan.segments || []).forEach((s, i) => {
    if (s && typeof s === "object" && s.startFrame !== undefined && s.startFrame !== null) s.startFrame = lay[i].startFrame;
    if (s && s.endFrame !== undefined && s.length !== undefined) delete s.endFrame;
  });
  if (plan.segments && plan.segments[0] && plan.segments[0].overlapIn) plan.segments[0].overlapIn = 0;
}
export function addSegment(plan, after = null) {
  ensureShape(plan);
  const segs = plan.segments;
  const ref = after === null ? segs[segs.length - 1] : segs[after];
  const length = ref ? (segmentLength(ref) || 396) : 396;
  const seg = { length, overlapIn: segs.length ? defaultOverlap(length) : 0, prompt: "",
                images: [], videos: [], audios: [], audioMode: "reference" };
  if (!segs.length) seg.overlapIn = 0;
  const at = after === null ? segs.length : after + 1;
  segs.splice(at, 0, seg);
  syncStartFrames(plan);
  return at;
}
export function duplicateSegment(plan, i) {
  const copy = JSON.parse(JSON.stringify(plan.segments[i]));
  if (copy.startFrame !== undefined) delete copy.startFrame;
  if (!copy.overlapIn) copy.overlapIn = defaultOverlap(segmentLength(copy) || 396);
  plan.segments.splice(i + 1, 0, copy);
  syncStartFrames(plan);
  return i + 1;
}
export function deleteSegment(plan, i) {
  plan.segments.splice(i, 1);
  syncStartFrames(plan);
}
export function moveSegment(plan, i, d) {
  const j = i + d;
  if (j < 0 || j >= plan.segments.length) return i;
  const [s] = plan.segments.splice(i, 1);
  plan.segments.splice(j, 0, s);
  // a segment moved to the front cannot keep an overlap; the old first one gets a default overlap
  if (plan.segments[1] && !plan.segments[1].overlapIn && (i === 0 || j === 0)) {
    plan.segments[1].overlapIn = defaultOverlap(segmentLength(plan.segments[1]) || 396);
  }
  syncStartFrames(plan);
  return j;
}
// 2026-10-28 idref: plan.idRef = the media image id used as the face-refine identity reference (one per plan).
// Valid only while some shot still uses that image.
export function idRefOf(plan) {
  const id = plan && plan.idRef;
  if (!id) return "";
  return (plan.segments || []).some((s) => s && (s.images || []).map(String).includes(String(id))) ? String(id) : "";
}
export function setIdRef(plan, id, on) {
  if (on) plan.idRef = String(id);
  else if (String(plan.idRef) === String(id)) delete plan.idRef;
}
export function toggleRef(plan, i, kind, id, on) {
  const s = plan.segments[i];
  if (kind === "videos") {
    s.videos = (s.videos || []).map(videoRef);
    const has = s.videos.some((v) => v.id === id);
    if (on && !has) {
      const info = (plan.media.videos || []).find((x) => x.id === id) || {};
      s.videos.push({ id, trimStart: 0, useSoundtrack: info.hasAudio !== false });
    }
    if (!on && has) s.videos = s.videos.filter((v) => v.id !== id);
  } else if (kind === "audios" && isPerFileAudio(s)) {
    const has = s.audios.some((x) => audioEntry(x).id === id);
    if (on && !has) s.audios.push({ id, mode: "reference" });
    if (!on && has) s.audios = s.audios.filter((x) => audioEntry(x).id !== id);
  } else {
    s[kind] = (s[kind] || []).map(String);
    const has = s[kind].includes(id);
    if (on && !has) s[kind].push(id);
    if (!on && has) s[kind] = s[kind].filter((x) => x !== id);
  }
}
// Convert shot i to per-file audio entries with identical behaviour (called before the first per-file edit):
// legacy none -> every file none + video soundtracks off (none also dropped soundtracks);
// legacy locked audio:X with X in the list -> X locked (offset kept); a video lock, or a locked file
// that is not in the list, stays a shot-level lock (still supported).
export function toPerFileAudio(plan, i) {
  const s = plan.segments[i];
  if (!s || isPerFileAudio(s)) return;
  const mode = s.audioMode ? String(s.audioMode) : "reference";
  const entries = (s.audios || []).map((x) => ({ id: String(x), mode: mode === "none" ? "none" : "reference" }));
  if (mode === "none") {
    s.videos = (s.videos || []).map(videoRef).map((v) => ({ ...v, useSoundtrack: false }));
    delete s.audioMode; delete s.locked;
  } else if (mode === "locked" && s.locked) {
    const src = String(s.locked.source || "");
    const e = src.startsWith("audio:") ? entries.find((x) => x.id === src.slice(6)) : null;
    if (e) { e.mode = "locked"; e.offset = Math.max(0, num(s.locked.offset, 0)); delete s.audioMode; delete s.locked; }
  }
  s.audios = entries;
}
export function setAudioFileMode(plan, i, id, mode) {
  toPerFileAudio(plan, i);
  const s = plan.segments[i];
  const e = (s.audios || []).find((x) => audioEntry(x).id === id);
  if (!e || !AUDIO_FILE_MODES.includes(mode)) return;
  if (mode === "locked") {
    for (const o of s.audios) if (o !== e && o.mode === "locked") { o.mode = "reference"; delete o.offset; }
    if (s.audioMode === "locked") { delete s.audioMode; delete s.locked; }   // one locked track per shot
    if (e.offset === undefined) e.offset = 0;
  } else delete e.offset;
  e.mode = mode;
}
export function setAudioFileOffset(plan, i, id, offset) {
  const s = plan.segments[i];
  const e = (s.audios || []).find((x) => x && typeof x === "object" && x.id === id);
  if (e) e.offset = Math.max(0, Number(offset) || 0);
}
export function moveRef(plan, i, kind, idx, d) {
  const arr = plan.segments[i][kind];
  const j = idx + d;
  if (!arr || j < 0 || j >= arr.length) return;
  const [x] = arr.splice(idx, 1);
  arr.splice(j, 0, x);
}
export function parsePlanText(text) {
  const t = (text || "").trim();
  if (!t) return { plan: null, error: null, empty: true };
  try {
    const p = JSON.parse(t);
    if (!p || typeof p !== "object" || Array.isArray(p)) return { plan: null, error: "plan_json must be a JSON object" };
    return { plan: p, error: null };
  } catch (e) {
    return { plan: null, error: "plan_json is not valid JSON (" + e.message + ")" };
  }
}
export function serializePlan(plan) {
  return JSON.stringify(plan, null, 2);
}

// ---------------------------------------------------------------- timeline (shots) drag model
// Semantics: a shot that overlaps the previous one continues it (overlapIn = overlap frames, taken from
// the previous shot's final latent); a shot that exactly touches the previous one is a hard cut
// (overlapIn 0). Gaps cannot exist: a shot dragged right of the previous end snaps back to touching.
export const snapLength = (frames) => nearestAligned(Math.max(5, Number(frames) || 5));

// Largest allowed overlap (exclusive) for shot i given current lengths (and auto-split sub lengths).
export function overlapLimit(plan, i) {
  if (i <= 0) return 0;
  const segs = plan.segments || [];
  const prevLen = segmentLength(segs[i - 1]) ?? 0, len = segmentLength(segs[i]) ?? 0;
  let lim = Math.min(prevLen, len);
  if (autoSplitOn(plan)) {
    const ps = shotSubLengths(plan, i - 1), cs = shotSubLengths(plan, i);
    lim = Math.min(lim, ps[ps.length - 1], cs[0]);
  }
  return Math.max(0, lim);
}
// Nearest of {0, 5, 22, 39, ...} below `limit` (exclusive). Values under 2.5 frames snap to 0 (cut).
export function snapOverlap(frames, limit) {
  const f = Math.max(0, Number(frames) || 0);
  let best = 0, bestD = f;
  for (let k = 5; k < limit; k += 17) {
    const d = Math.abs(f - k);
    if (d < bestD) { best = k; bestD = d; }
  }
  return best;
}
// The fixed choices {0, 22, 39} that fit shot i (an overlap must stay shorter than both lengths it joins).
export function overlapSteps(plan, i) {
  if (i <= 0) return [0];
  const lim = overlapLimit(plan, i);
  return OVERLAP_STEPS.filter((k) => k === 0 || k < lim);
}
// Nearest of OVERLAP_STEPS below `limit` (exclusive); ties go to the smaller step. Used by drag and clamping.
export function snapOverlapStep(frames, limit) {
  const f = Math.max(0, Number(frames) || 0);
  let best = 0, bestD = f;
  for (const k of OVERLAP_STEPS) {
    if (k === 0 || k >= limit) continue;
    const d = Math.abs(f - k);
    if (d < bestD) { best = k; bestD = d; }
  }
  return best;
}
export function seamOf(plan, i) {
  if (i <= 0) return { kind: "start", frames: 0 };
  const ov = Math.trunc(num((plan.segments[i] || {}).overlapIn || 0, 0));
  return ov > 0 ? { kind: "continue", frames: ov } : { kind: "cut", frames: 0 };
}
export function seamLabel(plan, i) {
  const s = seamOf(plan, i);
  return s.kind === "continue" ? `continue ${s.frames}f` : s.kind === "cut" ? "cut" : "";
}
// Drag shot i so it starts at `targetStart` (frames). Later shots move with it. Returns the new overlapIn.
export function dragShotTo(plan, i, targetStart) {
  if (i <= 0) return 0;
  const lay = layout(plan);
  const prevEnd = lay[i - 1].endFrame;
  const ov = snapOverlapStep(prevEnd - targetStart, overlapLimit(plan, i));
  const cur = Math.trunc(num(plan.segments[i].overlapIn || 0, 0));
  // unchanged position (a click, pointer jitter, or a drag that came back) keeps the user's choice: without this a
  // click on a shot whose 39 was shrunk to 22 stored an explicit 22 and lost the 39 (2026-10-17d)
  if (ov === cur) return cur;
  plan.segments[i].overlapIn = ov;
  delete plan.segments[i].overlapRequest;   // a manual overlap replaces a shrunk one
  applyVideoLengths(plan);
  syncStartFrames(plan);
  return Math.trunc(num(plan.segments[i].overlapIn || 0, 0));
}
// Drag the right edge of shot i to `targetEnd` (frames). Keeps both neighbouring seams valid.
export function resizeShotTo(plan, i, targetEnd) {
  const lay = layout(plan);
  const s = plan.segments[i];
  if (shotLengthIsAuto(plan, i)) return segmentLength(s);  // length follows the shot's reference video
  const minLen = Math.max(5, (i > 0 ? Math.trunc(num(s.overlapIn || 0, 0)) : 0) + 1,
                          i + 1 < plan.segments.length ? Math.trunc(num(plan.segments[i + 1].overlapIn || 0, 0)) + 1 : 0);
  let len = snapShotLength(Math.max(5, targetEnd - lay[i].startFrame));
  while (len < minLen) len += 17;
  s.length = len;
  if ("endFrame" in s) delete s.endFrame;
  clampSeams(plan);
  return len;
}
// After a length / auto-split change: lower overlaps that became too long (never creates a gap).
export function clampSeams(plan) {
  (plan.segments || []).forEach((s, i) => {
    if (i === 0 || !s) return;
    const ov = Math.trunc(num(s.overlapIn || 0, 0));
    const lim = overlapLimit(plan, i);
    if (ov >= lim || !validOverlap(ov)) { s.overlapIn = snapOverlapStep(Math.min(ov, lim - 1), lim); delete s.overlapRequest; }
  });
  syncStartFrames(plan);
}
export function setAutoSplit(plan, on, maxLength = DEFAULT_MAX_SEGMENT, subOverlap = DEFAULT_SUB_OVERLAP) {
  if (on) {
    plan.maxSegmentLength = snapLength(maxLength);
    if (subOverlap !== DEFAULT_SUB_OVERLAP || plan.subOverlap !== undefined) plan.subOverlap = subOverlap;
  } else {
    delete plan.maxSegmentLength;
    delete plan.subOverlap;
  }
  clampSeams(plan);
}
// Manual option: replace shot i by N separate shots (continuations), each at most maxLength frames
// (recommended split: full maxLength segments + the leftover tail). Works on every shot, also one that follows a reference video.
// Each copy keeps the refs/prompt (prompt editable afterwards); returns the number of new shots.
export function splitShot(plan, i, maxLength = DEFAULT_MAX_SEGMENT, overlap = DEFAULT_SUB_OVERLAP) {
  const len = splitSourceLength(plan, i);
  const ms = snapLength(maxLength);
  if (!isAlignedLength(overlap) || overlap >= ms) throw new Error(`overlap ${overlap} must be 5+17k and shorter than ${ms}`);
  return applySplit(plan, i, splitLengths(len, ms, overlap), overlap);
}
// Custom split: the user lists segment durations in seconds ("10, 15"), each -> rounded UP to 5+17n frames (the whole
// generated length, overlap included, like the recommended split). Consecutive segments share `overlap` frames;
// what is left of the shot becomes one more segment. Throws with a readable message when the list does not fit.
export function parseSecondsList(text) {
  const parts = String(text || "").split(/[,，;；\s]+/).filter((x) => x !== "");
  if (!parts.length) throw new Error("enter segment durations in seconds, e.g. 10, 15");
  return parts.map((x) => {
    const v = Number(x);
    if (!Number.isFinite(v) || v <= 0) throw new Error(`'${x}' is not a positive number of seconds`);
    return v;
  });
}
export function customSplitLengths(length, secondsList, overlap, overlapIn = 0) {
  if (!isAlignedLength(overlap)) throw new Error(`overlap ${overlap} must be 5+17k`);
  const lengths = secondsList.map((sec) => alignedUp(sec * FPS));   // first 5+17n >= the input, never down
  lengths.forEach((l, j) => {
    const need = j === 0 ? overlapIn : overlap;
    if (l <= need) throw new Error(`segment ${j + 1}: ${l}f must be longer than its ${need}f overlap`);
  });
  const covered = lengths.reduce((a, x) => a + x, 0) - (lengths.length - 1) * overlap;
  if (covered > length) throw new Error(`the segments cover ${covered}f (${f2(covered / FPS)}s), more than the shot's ${length}f (${f2(length / FPS)}s)`);
  if (covered < length) lengths.push(length - covered + overlap);   // the rest of the shot (5+17n, > overlap)
  if (lengths.length < 2) throw new Error("the segments cover the whole shot in one piece: nothing to split");
  return lengths;
}
export function splitShotCustom(plan, i, secondsList, overlap = DEFAULT_SUB_OVERLAP) {
  const len = splitSourceLength(plan, i);
  return applySplit(plan, i, customSplitLengths(len, secondsList, overlap, splitOverlapIn(plan, i)), overlap);
}
// Current length of shot i as it runs (a video-driven shot: from its used video duration).
function splitSourceLength(plan, i) {
  const g = autoShotGeometry(plan, i);
  const len = g ? g.length : segmentLength(plan.segments[i]);
  if (!isAlignedLength(len)) throw new Error(`shot ${i + 1} length ${len} is not on the 5+17n grid`);
  return len;
}
// Frames shot i shares with the previous shot as it runs (a video-driven shot: its requested overlap).
function splitOverlapIn(plan, i) {
  if (i === 0) return 0;
  const g = autoShotGeometry(plan, i);
  return g ? g.overlap : Math.trunc(num(plan.segments[i].overlapIn || 0, 0));
}
// Replace shot i by copies of `lengths` frames joined by `overlap`. Copy j starts `off` frames into the shot:
// a locked audio offset advances by off; every video ref's start offset advances by the frames from the shot's
// content origin (frame overlapIn of the shot) to the copy's first NEW frame, and the copy uses exactly the video
// it covers as new content (its used duration), so its length follows the video to the same 5+17n value.
function applySplit(plan, i, lengths, overlap) {
  const s = plan.segments[i];
  const mv = mediaMaps(plan).videos;
  const g = autoShotGeometry(plan, i, mv);
  const ovIn = splitOverlapIn(plan, i);
  const subPrompts = Array.isArray(s.subPrompts) ? s.subPrompts : [];
  const copies = lengths.map((l, j) => {
    const c = JSON.parse(JSON.stringify(s));
    delete c.startFrame; delete c.endFrame; delete c.subPrompts;
    c.length = l;
    c.overlapIn = j === 0 ? ovIn : overlap;
    if (j > 0) delete c.overlapRequest;
    if (j < subPrompts.length && String(subPrompts[j] || "").trim()) c.prompt = subPrompts[j];
    const off = lengths.slice(0, j).reduce((a, x) => a + x - overlap, 0);
    const lockedFile = Array.isArray(c.audios) ? c.audios.find((x) => x && typeof x === "object" && x.mode === "locked") : null;
    if (lockedFile) lockedFile.offset = Math.round((num(lockedFile.offset, 0) + off / FPS) * 1000) / 1000;
    else if (c.audioMode === "locked" && c.locked) {
      c.locked = { ...c.locked, offset: Math.round((num(c.locked.offset, 0) + off / FPS) * 1000) / 1000 };
    }
    if (g && Array.isArray(c.videos)) {
      const startIn = off + c.overlapIn - ovIn;            // content frames before this copy's first new frame
      const rest = g.content - startIn;
      if (!(rest > 0)) throw new Error(`segment ${j + 1} starts after the end of the shot's video (${f2(g.content / FPS)}s)`);
      const used = Math.min(l - c.overlapIn, rest) / FPS;
      c.videos = c.videos.map((r) => {
        const v = typeof r === "string" ? { id: r } : r;
        v.trimStart = Math.max(0, num(v.trimStart, 0)) + startIn / FPS;
        const info = mv[pyStr(v.id)];
        const d = info ? num(info.duration) : null;
        v.useDuration = d === null ? used : Math.min(used, Math.max(0, d - v.trimStart));
        return v;
      });
    }
    syncVideoLockOffset(c);   // H3MD lockrule (2026-10-26): video lock offset = rule(start, overlapIn) of this part
    return c;
  });
  plan.segments.splice(i, 1, ...copies);
  syncStartFrames(plan);
  return copies.length;
}
// Split dialog model (pure; the plan is not modified): opts = { mode: "recommended"|"custom", seconds (total,
// "" = keep), max, ov, custom (text) } -> { target, lengths, error }.
export function splitPreview(plan, i, opts) {
  const p = JSON.parse(JSON.stringify(plan));
  try {
    applySplitTotal(p, i, opts.seconds);
    const target = splitSourceLength(p, i);
    let lengths;
    if (opts.mode === "custom") {
      lengths = customSplitLengths(target, parseSecondsList(opts.custom), Number(opts.ov), splitOverlapIn(p, i));
    } else if (opts.parts !== undefined) {
      // H3MD splitauto (2026-10-27): recommended = the auto-split parts from /h3md/autosplit (the yellow line), as they are
      if (!Array.isArray(opts.parts)) throw new Error(opts.partsNote || "waiting for the auto-split estimate (the yellow line under the shot tab)…");
      lengths = opts.parts.map(Number);
      const ov = Number(opts.ov);
      const covered = lengths.reduce((a, x) => a + x, 0) - (lengths.length - 1) * ov;
      if (!lengths.every(isAlignedLength) || covered !== target) throw new Error(`auto-split parts ${lengths.join(" + ")} (overlap ${ov}f) do not cover this shot's ${target}f: wait for the auto-split estimate to refresh`);
    } else {
      const ms = snapLength(opts.max);
      if (!isAlignedLength(Number(opts.ov)) || Number(opts.ov) >= ms) throw new Error(`overlap ${opts.ov} must be 5+17k and shorter than ${ms}`);
      lengths = splitLengths(target, ms, Number(opts.ov));
    }
    applySplit(p, i, lengths, Number(opts.ov));   // dry run on the copy: catches video-window errors too
    return { target, lengths, error: null };
  } catch (e) {
    let target = null;
    try { target = splitSourceLength(p, i); } catch { /* keep null */ }
    return { target, lengths: null, error: e.message };
  }
}
// The dialog's optional "total seconds": a plain shot gets that length (nearest 5+17n), a video-driven shot that
// used video duration.
function applySplitTotal(plan, i, seconds) {
  if (seconds === "" || seconds === null || seconds === undefined) return;
  const sec = Number(seconds);
  if (!Number.isFinite(sec) || sec <= 0) throw new Error(`total seconds '${seconds}' is not a positive number`);
  if (shotUsedSeconds(plan, i)) setShotUsedSeconds(plan, i, sec);
  else { plan.segments[i].length = snapLength(sec * FPS); clampSeams(plan); }
}
export function splitShotDialog(plan, i, opts) {
  const pre = splitPreview(plan, i, opts);
  if (pre.error) throw new Error(pre.error);
  applySplitTotal(plan, i, opts.seconds);
  return applySplit(plan, i, pre.lengths, Number(opts.ov));
}
// A new shot of `seconds` (nearest 5+17n) appended after shot `after` (default last).
export function addShotSeconds(plan, seconds, after = null, overlap = 0) {
  const at = addSegment(plan, after);
  plan.segments[at].length = snapShotLength(seconds * FPS);
  plan.segments[at].overlapIn = overlap;
  clampSeams(plan);
  return at;
}
// Ruler tick step (seconds) for a timeline of `totalFrames` drawn over `px` pixels.
export function rulerStep(totalFrames, px) {
  const secs = totalFrames / FPS;
  for (const st of [0.5, 1, 2, 5, 10, 15, 30, 60, 120]) if ((st / secs) * px >= 48) return st;
  return 300;
}

// One-click clears (no confirmation): all shots → one empty default shot; a media kind → empty library
// (and its references removed from every shot).
export function clearSegments(plan) {
  ensureShape(plan);
  plan.segments = [{ length: 396, overlapIn: 0, prompt: "", images: [], videos: [], audios: [], audioMode: "reference" }];
  syncStartFrames(plan);
}
export function clearMedia(plan, kind) {
  ensureShape(plan);
  for (const it of (plan.media[kind] || []).slice()) if (it) removeMedia(plan, kind, it.id);
  plan.media[kind] = [];
}
