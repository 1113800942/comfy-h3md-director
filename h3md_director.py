"""H3 MultiSeg DualRes (H3MD) - multi-segment director backend nodes.

Nodes:
  H3MDDirector         plan_json STRING -> H3MD_PLAN (validated, labels resolved)
  H3MDSegmentPick      plan + index -> raw media (native resolution, single <=15 s ref window)
  H3MDSegmentEncode    plan + index -> core MiniMaxH3ReferenceToVideo.execute (official label order)
  H3MDSegmentContinue  final-latent-only handoff: overlap token copy + nested noise_mask,
                       optional locked audio, H3MD drift (prefix re-noised during sampling, restored exactly at the end)
                       (own dynamic overlap re-noise mask, see H3MDDriftMask)
  H3MDSegmentJoin      trim overlap frames/audio from the new segment head and append

plan_json v1 (paths are relative to ComfyUI's input directory, or absolute):
{
  "version": 1, "fps": 24, "width": 1472, "height": 1088,
  "globalPrompt": "", "globalMode": "prepend",   # globalMode is legacy and ignored: global is always prepended
  "media": {
    "images": [{"id": "img1", "file": "029.png"}],
    "videos": [{"id": "vid1", "file": "clip.mp4", "duration": 15.0, "hasAudio": true}],
    "audios": [{"id": "aud1", "file": "voice.wav"}]
  },
  "segments": [{
    "length": 396, "overlapIn": 0, "prompt": "...",
    "images": ["img1"],
    "videos": [{"id": "vid1", "trimStart": 0, "useSoundtrack": true}],
    "audios": [],
    "audioMode": "reference" | "locked" | "none",
    "locked": {"source": "video:vid1" | "audio:aud1", "offset": 0.0}
    # a video lock (source "video:<id>", the video in this shot) excludes its soundtrack <Audio> reference (lock and
    # soundtrack are exclusive, 2026-10-26 lockexcl); its "offset" is derived by rule (lockrule 2026-10-26, see
    # video_lock_offset): overlapIn > 0 -> start - overlapIn/24, NO clamp (syncfix 2026-10-26: a negative part is
    # padded with leading silence, load_locked_audio_window), overlapIn == 0 -> start (saved value ignored)
    # per-file audio mode (editor >= 2026-10-18): an "audios" entry may be an object instead of an id:
    #   "audios": ["aud1", {"id": "aud2", "mode": "reference" | "locked" | "none", "offset": 0.0}]
    # reference/locked files are passed as <Audio> references (a locked file too, like shot-level
    # locked mode always did); none files are not passed; at most one file per shot is locked
    # (= locked audio track, offset in seconds).  A plain id means "reference".  Shot-level
    # audioMode "none" still drops all audio (soundtracks too); shot-level locked (video or audio
    # source) still works when no file is locked.  See resolve_shot_audio().
  }]
}
Shot length (2026-10-20): every entry of "segments" runs as exactly ONE segment of the length the user
  set (5+17n frames, no upper limit, no automatic splitting).  Recommended: about 15 s per shot.
  Legacy keys from older plans are accepted and ignored: plan/shot "maxSegmentLength", plan "subOverlap",
  shot "subPrompts" (a note is added when an old plan still carries them).
  Seams keep the overlapIn rule: overlapIn > 0 = continuation from the previous shot (its last overlapIn
  frames carried over, continuity sentence prepended), overlapIn == 0 = hard cut (independent generation).
Label order follows the official MiniMax H3 encoder: images -> for each video
(<Audio j> soundtrack right before its <Video k>) -> standalone audios.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys

import torch

try:
    from . import h3md_vram
    from . import h3md_autosplit as AS   # H3MD autosplit (2026-10-27)
    from . import h3md_paths as PATHS    # v0.2.1: shared-ComfyUI path limits
except ImportError:  # flat import (tests)
    import h3md_vram
    import h3md_autosplit as AS
    import h3md_paths as PATHS

LOG = logging.getLogger("H3MD")

FPS = 24
AUDIO_LATENT_FPS = 40
MAX_REF_SECONDS = 15.0
MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
PLAN_TYPE = "H3MD_PLAN"
CATEGORY = "MiniMax H3/H3MD"
_EPS = 1e-3


# ----------------------------------------------------------------------------
# H3 geometry helpers
# ----------------------------------------------------------------------------

def is_aligned_length(frames: int) -> bool:
    return isinstance(frames, int) and frames >= 5 and (frames - 5) % 17 == 0


def nearest_aligned(frames: float) -> int:
    return 5 + 17 * max(0, int(round((float(frames) - 5) / 17.0)))


def snap_length(frames: float) -> int:
    """User-set shot length -> NEAREST H3 frame count 5+17n (ties go up, minimum 5), like the TD Timeline
    Director's GEN windows (no maximum).  5 s = 120 f -> 124, 15 s = 360 f -> 362, 16.5 s = 396 f -> 396."""
    return 5 + 17 * max(0, int(math.floor((float(frames) - 5) / 17.0 + 0.5)))


def aligned_up(frames: float) -> int:
    """Smallest H3 frame count (5+17n) >= frames - core comfy_extras/nodes_minimax_h3.align_frame_count
    rounds the same way (while n % 17 != 5: n += 1)."""
    f = max(5, int(math.ceil(float(frames) - 1e-3)))   # 1e-3: seconds x 24 float noise (12.041667 s -> 289.000008 f)
    return 5 + 17 * ((f - 5 + 16) // 17)


def aligned_down(frames: float) -> int:
    """Largest H3 frame count (5+17n) <= frames (minimum 5)."""
    f = int(math.floor(float(frames) + 1e-3))
    return 5 + 17 * max(0, (f - 5) // 17)


MOTION_TRIGGER = "motion_transfer:"   # TARA motion copy trigger (third-party LoRA); its prompt must start with it


def is_motion_copy(global_prompt: str, prompt: str) -> bool:
    return MOTION_TRIGGER in f"{global_prompt or ''}\n{prompt or ''}"


def fit_video_length(overlap: int, content: float, avail, motion: bool = False) -> int:
    """Length of a shot that follows its reference video: rounded UP to 5+17n (unchanged).
    v0.3.5 motion copy (TARA, prompt with 'motion_transfer:'): rounded DOWN when rounding up would run past the
    end of the clip (new frames > avail = clip frames from trimStart to its end) - TARA needs a source frame for
    every generated frame ('identical temporal grids' error otherwise).  Loses at most 16 frames (0.7 s) at the
    clip end; nothing for the user to set."""
    up = aligned_up(overlap + content)
    if motion and avail is not None and up - overlap > float(avail) + 1e-3:
        down = aligned_down(overlap + content)
        if down - overlap >= 1:
            return down
    return up


DURATION_LINE = re.compile(r"^[ \t]*Duration:[^\n]*$", re.M)


def fix_duration_line(prompt: str, length: int, overlap: int) -> str:
    """v0.3.5: the prompt's first 'Duration: ...' line is rewritten to the segment's real length (after the
    video-following length and the auto-split), so nobody has to count frames.  No such line: unchanged."""
    if not prompt or "Duration:" not in prompt:
        return prompt
    tail = f"; the first {overlap} frames continue the previous shot" if overlap else ""
    line = f"Duration: {length / FPS:.2f} s ({length} frames{tail})."
    return DURATION_LINE.sub(lambda m: line, prompt, count=1)


def video_shot_length(segment: dict, media_videos: dict):
    """Shot length that follows the shot's FIRST reference video: its used duration in seconds (useDuration,
    editable in the editor; default = the clip from its internal start offset trimStart to its end;
    media.videos[].duration = video-stream frames / fps, refreshed from the file by the
    Director node; any source fps), converted to 24 fps and rounded UP to 5+17n; the shot itself is not capped or split.
    None when the shot has no video ref or the clip duration is unknown: its stored 'length' is used."""
    content = video_content_frames(segment, media_videos)
    return None if content is None else aligned_up(content)


def video_available_frames(segment: dict, media_videos: dict):
    """Frames (24 fps, float) of the shot's first reference video from trimStart to the clip end, or None."""
    for v in segment.get("videos") or []:
        v = {"id": v} if isinstance(v, str) else (v or {})
        info = media_videos.get(str(v.get("id")))
        if info is None:
            continue
        src_dur = _num(info.get("duration"))
        if src_dur is None:
            return None
        rem = src_dur - max(0.0, _num(v.get("trimStart"), 0.0))
        return rem * FPS if rem > 0 else None
    return None


def video_content_frames(segment: dict, media_videos: dict):
    """True content length (24 fps frames, unrounded float) of a shot that follows its first reference video:
    min(useDuration, clip duration from trimStart to the clip end) x 24 (no useDuration: to the clip end).
    None = the shot's stored length is used.  Mirrors h3md_plan.js videoContentFrames."""
    for v in segment.get("videos") or []:
        v = {"id": v} if isinstance(v, str) else (v or {})
        info = media_videos.get(str(v.get("id")))
        if info is None:
            continue
        src_dur = _num(info.get("duration"))
        if src_dur is None:
            return None
        rem = src_dur - max(0.0, _num(v.get("trimStart"), 0.0))
        if not rem > 0:
            return None
        use = _num(v.get("useDuration"), None)
        return (min(use, rem) if use is not None and use > 0 else rem) * FPS
    return None


# The overlap steps the editor offers for EVERY continued shot (2026-10-17c).  Other 5+17k values (older plans:
# 5, 56, 107 ...) are still accepted and run as saved, with a WARNING note.
OVERLAP_STEPS = (0, 22, 39)


def video_tokens(frames: int) -> int:
    frames = int(frames)
    return 2 if frames <= 5 else ((frames - 5) // 17) * 5 + 2


def audio_tokens(frames: int) -> int:
    return int(round(int(frames) * AUDIO_LATENT_FPS / FPS))


def valid_overlap(frames: int) -> bool:
    return frames == 0 or is_aligned_length(frames)


CONTINUITY_TEMPLATE = ("Continuity: the first {seconds:.1f} seconds are carried over unchanged from the end of "
                       "the previous part. Continue seamlessly from that moment, keeping the same people, "
                       "appearance, positions, motion, lighting and camera framing, then follow the description below.")


def continuity_note(overlap_frames: int) -> str:
    return CONTINUITY_TEMPLATE.format(seconds=int(overlap_frames) / FPS)


# ----------------------------------------------------------------------------
# Plan parsing / validation
# ----------------------------------------------------------------------------

def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _snap32(value: int) -> int:
    return max(32, int(round(int(value) / 32.0)) * 32)


# Video size alignment (2026-10-29, idea from Melatonin-CZ/ComfyUI-H3TwoStage, MIT; own code): the two-pass sampler
# runs its low-res pass on round(latent * lowres_scale / 2) * 2.  With the default lowres_scale 0.5 that grid is an
# exact half only when the latent side (pixels / 16) is a multiple of 4, i.e. the pixel side is a multiple of 64; any
# other size makes the low-res grid a slightly different shape and the lift stretches it (coloured edges).
SIZE_ALIGN = 64


def _snap_size(value: int) -> int:
    return max(SIZE_ALIGN, int(round(int(value) / float(SIZE_ALIGN))) * SIZE_ALIGN)


def resolve_prompt(global_prompt: str, global_mode: str = "prepend", segment_prompt: str = "") -> str:
    """Global is always prepended to every segment prompt (blank line between).  global_mode is kept
    only for call compatibility and ignored (old plans may still say "fallback").  An empty global
    gives the segment prompt alone; an empty segment prompt gives the global alone."""
    g = (global_prompt or "").strip()
    s = (segment_prompt or "").strip()
    if g and s:
        return g + "\n\n" + s
    return s or g


def video_lock_offset(start: float, overlap_in: int) -> float:
    """H3MD lockrule (2026-10-26) + syncfix (2026-10-26): locked start (seconds into the video) of a video lock,
    recomputed from the video's start (trimStart) every time: start - overlapIn/24 (overlapIn 0 -> start).
    syncfix: NO max(0) clamp - output frame k always hears source time start + (k - overlapIn)/24, the same moment
    its picture shows; a negative value means leading silence (pick_segment_media, load_locked_audio_window), which
    lies inside the overlap that the join drops.  Mirrors web/js/h3md_plan.js videoLockOffset."""
    start = max(0.0, float(start))
    return start - int(overlap_in) / FPS if int(overlap_in) > 0 else start


def video_window_origin(ref: dict, segment: dict) -> float:
    """H3MD oneshot (2026-10-26): source time (seconds) at OUTPUT FRAME 0 of a video's picture window (<Video N>
    reference, depth window) and of its soundtrack <Audio>: the same origin as the video lock (video_lock_offset),
    start - overlapIn/24, so output frame k always maps to source time start + (k - overlapIn)/24 on every path.
    overlapIn 0 (hard cut / first segment) -> start, i.e. exactly the old window.  A reference whose trimStart is
    already a frame-0 time (refwindow-advanced sub-segment window, flag "_frame0") is used as is (no double shift).
    May be negative: pictures before source time 0 = the first source frame (load_video_window), soundtrack = silence
    (load_locked_audio_window)."""
    if ref.get("_frame0"):
        return float(ref["trimStart"])
    return video_lock_offset(ref["trimStart"], int(segment.get("overlapIn") or 0))


def locked_video_id(segment: dict):
    """H3MD lockexcl (2026-10-26): id of the video whose own sound is the shot's locked audio track, else None.
    A locked video's sound is hard-copied into the segment audio (locked_audio) and is never also passed as a
    soundtrack <Audio> reference (lock and soundtrack are mutually exclusive)."""
    lk = segment.get("locked")
    if segment.get("audioMode") == "locked" and isinstance(lk, dict) and lk.get("kind") == "video":
        return lk.get("id")
    return None


def depth_control_only(segment: dict, ref: dict, media: dict) -> bool:
    """H3MD ctlonly (2026-10-30): a depth video is NOT a <Video N> reference (only the depth ControlNet), unless it
    also supplies the segment's soundtrack <Audio> (then it stays a reference; the Loop warns)."""
    if not (isinstance(ref, dict) and ref.get("depth") is True):
        return False
    info = media["videos"].get(ref["id"], {})
    soundtrack = (segment.get("audioMode", "reference") != "none" and ref.get("useSoundtrack", True)
                  and ref["id"] != locked_video_id(segment) and info.get("hasAudio", True))
    return not soundtrack


def compute_labels(segment: dict, media: dict) -> list:
    """Official order: images, then per video (soundtrack <Audio> before <Video>), then standalone audio."""
    labels = []
    pic = vid = aud = 0
    audio_allowed = segment["audioMode"] != "none"
    locked_vid = locked_video_id(segment)   # H3MD lockexcl: the locked video's sound gets no <Audio N>
    for image_id in segment["images"]:
        pic += 1
        labels.append({"tag": f"<Picture {pic}>", "kind": "image", "id": image_id,
                       "file": media["images"][image_id]["file"]})
    for ref in segment["videos"]:
        # H3MD audioui (2026-10-26): its soundtrack <Audio> depends only on useSoundtrack / audioMode.
        # H3MD ctlonly (2026-10-30): a depth video drives only the ControlNet and gets NO <Video N>.
        info = media["videos"][ref["id"]]
        soundtrack = bool(audio_allowed and ref["useSoundtrack"] and ref["id"] != locked_vid and info.get("hasAudio", True))
        if soundtrack:
            aud += 1
            labels.append({"tag": f"<Audio {aud}>", "kind": "video_soundtrack", "id": ref["id"],
                           "file": info["file"]})
        if ref.get("depth") is True and not soundtrack:
            continue
        vid += 1
        src_dur = _num(info.get("duration"))
        end = ref["trimStart"] + segment["length"] / FPS   # full reference video from trimStart, capped by the segment length
        labels.append({"tag": f"<Video {vid}>", "kind": "video", "id": ref["id"], "file": info["file"],
                       "window": [ref["trimStart"], end if src_dur is None else min(src_dur, end)]})
    if audio_allowed:
        for audio_id in segment["audios"]:
            aud += 1
            labels.append({"tag": f"<Audio {aud}>", "kind": "audio", "id": audio_id,
                           "file": media["audios"][audio_id]["file"]})
    return labels


AUDIO_FILE_MODES = ("reference", "locked", "none")


def resolve_shot_audio(s: dict, n: int, media: dict, audio_mode: str):
    """Shot audio -> (audioMode, reference audio ids, locked) with the same meaning as before.

    Legacy shots (only plain ids) are returned exactly as the old parser did.  Per-file entries
    ({"id", "mode", "offset"}) give each file its own mode; the shot's effective audioMode is
    "locked" when a file (or a legacy locked.source) is locked, otherwise "reference"."""
    entries = []
    for x in s.get("audios") or []:
        if isinstance(x, dict):
            mode = str(x.get("mode") or "reference")
            if mode not in AUDIO_FILE_MODES:
                raise ValueError(f"H3MD Director: segment {n} audio '{x.get('id')}' mode must be reference|locked|none")
            entries.append({"id": str(x.get("id")), "mode": mode, "offset": max(0.0, _num(x.get("offset"), 0.0))})
        else:
            entries.append({"id": str(x), "mode": "reference", "offset": 0.0})
    for e in entries:
        if e["id"] not in media["audios"]:
            raise ValueError(f"H3MD Director: segment {n} references unknown audio '{e['id']}'")
    file_locked = [e for e in entries if e["mode"] == "locked"]
    if len(file_locked) > 1:
        raise ValueError(f"H3MD Director: segment {n} has {len(file_locked)} locked audios (max 1)")
    audios = [e["id"] for e in entries if e["mode"] != "none"]
    if len(audios) > MAX_AUDIOS:
        raise ValueError(f"H3MD Director: segment {n} has {len(audios)} audios (max {MAX_AUDIOS})")
    if audio_mode == "none":                       # legacy shot-level switch: no audio at all
        return "none", audios, None
    if file_locked:
        e = file_locked[0]
        return "locked", audios, {"kind": "audio", "id": e["id"], "offset": e["offset"]}
    if audio_mode == "locked":
        locked_raw = s.get("locked") or {}
        source = str(locked_raw.get("source") or "")
        kind, _, ref_id = source.partition(":")
        if not ((kind == "video" and ref_id in media["videos"]) or (kind == "audio" and ref_id in media["audios"])):
            # H3MD noref (2026-10-29): a lock whose file is gone (or a shot without any reference) is stale state,
            # not an instruction - the editor drops it too (clearOrphanRefState); run the shot as reference audio
            if kind not in ("", "video", "audio"):
                raise ValueError(
                    f"H3MD Director: segment {n} audioMode 'locked' needs locked.source "
                    f"'video:<id>' or 'audio:<id>' (got '{source}')")
            LOG.warning("H3MD Director: segment %s audio lock '%s' points to no loaded video/audio (removed?); "
                        "lock ignored, the shot uses audioMode 'reference'", n, source)
            return "reference", audios, None
        return "locked", audios, {"kind": kind, "id": ref_id, "offset": max(0.0, _num(locked_raw.get("offset"), 0.0))}
    return audio_mode, audios, None


def make_motion(raw):
    """H3MD autosplit (2026-10-27): motion(seg, content) for parse_plan - the reference motion of a shot's first video
    over its new content, used to place the auto-split cut points.  Shared by the Director run and the editor preview
    so the parts the editor shows (and the Split dialog's "recommended") are exactly the parts the run uses."""
    media_files = {str(v.get("id")): v.get("file") for v in ((raw.get("media") or {}).get("videos") or [])
                   if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def motion(seg, content):
        if not seg.get("videos"):
            return None
        ref = seg["videos"][0]
        f = media_files.get(ref["id"])
        return AS.motion_profile(resolve_media_path(f), float(ref["trimStart"]), content / FPS) if f else None
    return motion


def parse_plan(plan_json, width: int = 0, height: int = 0, continuity: bool = False, motion=None, sizes=None,
               extra_tokens=None, budget=None, report=None) -> dict:
    """motion / sizes / extra_tokens / budget: H3MD autosplit inputs (see h3md_autosplit.apply); all optional -
    without them over-budget shots are still split, using fallback media sizes and evenly spaced cuts."""
    if isinstance(plan_json, dict):
        raw = plan_json
    else:
        text = (plan_json or "").strip()
        if not text:
            raise ValueError("H3MD Director: plan_json is empty")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"H3MD Director: plan_json is not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise ValueError("H3MD Director: plan_json must be a JSON object")
    version = int(raw.get("version", 1))
    if version != 1:
        raise ValueError(f"H3MD Director: unsupported plan version {version} (expected 1)")
    fps = int(raw.get("fps", FPS))
    if fps != FPS:
        raise ValueError("H3MD Director: MiniMax H3 plans must use fps 24")

    notes = []
    w = int(width) if int(width or 0) > 0 else int(raw.get("width") or 0)
    h = int(height) if int(height or 0) > 0 else int(raw.get("height") or 0)
    pw, ph = int(raw.get("width") or 0), int(raw.get("height") or 0)
    if int(width or 0) > 0 and int(height or 0) > 0 and pw > 0 and ph > 0 and (pw, ph) != (w, h):
        # 0.3.4: the node's width / height (usually wired from the Control Panel) win over the plan's size
        notes.append(f"size {w}x{h} from the node / Control Panel (the plan says {pw}x{ph}; the Control Panel wins)")
        LOG.info("[H3MD Director] size %dx%d from the node / Control Panel; the plan's %dx%d is ignored. 尺寸以总控台为准"
                 "（%dx%d），计划里的 %dx%d 不用。", w, h, pw, ph, w, h, pw, ph)
    if w <= 0 or h <= 0:
        raise ValueError("H3MD Director: width/height missing (set them in plan_json or on the node)")
    if w % SIZE_ALIGN or h % SIZE_ALIGN:
        sw, sh = _snap_size(w), _snap_size(h)
        notes.append(f"size {w}x{h} snapped to {sw}x{sh} (multiple of {SIZE_ALIGN}: exact half-size low-res pass)")
        LOG.warning("[H3MD Director] size %dx%d snapped to %dx%d (multiple of %d, so the low-res pass is exactly half "
                    "size)", w, h, sw, sh, SIZE_ALIGN)
        w, h = sw, sh

    media_raw = raw.get("media") or {}
    media = {"images": {}, "videos": {}, "audios": {}}
    for kind in ("images", "videos", "audios"):
        for item in media_raw.get(kind) or []:
            if not isinstance(item, dict) or not item.get("id") or not item.get("file"):
                raise ValueError(f"H3MD Director: every media.{kind} entry needs 'id' and 'file'")
            if item["id"] in media[kind]:
                raise ValueError(f"H3MD Director: duplicate media id '{item['id']}' in {kind}")
            entry = dict(item)
            if kind == "videos":
                entry["duration"] = _num(item.get("duration"))
                entry["hasAudio"] = bool(item.get("hasAudio", True))
            media[kind][item["id"]] = entry

    # 2026-10-28 idref: plan-level identity reference = one media image (face refine follows this person)
    id_ref = None
    if raw.get("idRef"):
        rid = str(raw.get("idRef"))
        used = any(rid in [str(x) for x in ((s or {}).get("images") or [])] for s in (raw.get("segments") or [])
                   if isinstance(s, dict))
        if rid in media["images"] and used:
            id_ref = {"id": rid, "file": media["images"][rid]["file"]}
        elif rid in media["images"]:
            notes.append(f"idRef '{rid}' is used by no shot - ignored")
        else:
            notes.append(f"idRef '{rid}' is not a media image - ignored")

    global_prompt = str(raw.get("globalPrompt") or "")
    # legacy key: any value (or none) is accepted and ignored; global is always prepended
    global_mode = "prepend"

    # 2026-10-20: no maximum shot length and no automatic splitting.  Legacy split keys (plan/shot
    # maxSegmentLength, subOverlap, shot subPrompts) are accepted with any value and ignored.
    legacy = [k for k in ("maxSegmentLength", "subOverlap") if raw.get(k) is not None]

    segs_raw = raw.get("segments") or []
    if not segs_raw:
        raise ValueError("H3MD Director: plan has no segments")
    segments = []
    lengths_moved = False  # a stored length was replaced by its video length: stored startFrames are stale
    for i, s in enumerate(segs_raw):
        n = i + 1
        if not isinstance(s, dict):
            raise ValueError(f"H3MD Director: segment {n} must be an object")
        length = s.get("length")
        if length is None and s.get("endFrame") is not None and s.get("startFrame") is not None:
            length = int(s["endFrame"]) - int(s["startFrame"])
        if s.get("maxSegmentLength") is not None and "maxSegmentLength" not in legacy:
            legacy.append("maxSegmentLength")
        if any(str(x).strip() for x in (s.get("subPrompts") if isinstance(s.get("subPrompts"), list) else [])):
            notes.append(f"segment {n} subPrompts ignored (no automatic splitting): the shot prompt is used")
        overlap = int(s.get("overlapIn", 0) or 0)
        requested = s.get("overlapRequest")          # editor: the user's overlap when the stored one was shrunk
        requested = int(requested) if requested is not None and i > 0 and overlap > 0 else overlap
        content = video_content_frames(s, media["videos"])
        avail = video_available_frames(s, media["videos"])
        motion = is_motion_copy(global_prompt, s.get("prompt", ""))
        if i > 0 and valid_overlap(requested) and requested not in OVERLAP_STEPS:
            notes.append(f"WARNING segment {n} overlapIn {requested} is not one of 0/22/39 (older plan); kept as saved")
        if content is not None:
            cont = i > 0 and requested > 0 and valid_overlap(requested)
            if cont:
                # no length cap: the requested overlap is always kept, the new content follows the overlap
                overlap = requested
                auto_len = fit_video_length(overlap, content, avail, motion)
                if i > 0 and overlap != int(s.get("overlapIn", 0) or 0):
                    lengths_moved = True
            else:
                auto_len = fit_video_length(0, content, avail, motion)
            if length is not None and int(length) != auto_len:
                lengths_moved = True
                what = (f"overlap {overlap} + {content / FPS:.2f}s new content" if i > 0 and overlap > 0
                        else "its first reference video duration")
                notes.append(f"segment {n} length {int(length)} -> {auto_len} (follows {what}, "
                             f"{'motion copy: rounded down to 5+17n so it does not run past the clip end' if auto_len < (overlap if i > 0 else 0) + content - 1e-3 else 'rounded up to 5+17n'})")
            length = auto_len
        if length is None:
            raise ValueError(f"H3MD Director: segment {n} needs 'length' (frames, 5+17n)")
        try:
            length_f = float(length)
        except (TypeError, ValueError):
            raise ValueError(f"H3MD Director: segment {n} length {length!r} is not a number")
        if not (length_f == int(length_f) and is_aligned_length(int(length_f))):
            snapped = snap_length(length_f)
            notes.append(f"segment {n} length {length} -> {snapped} (snapped to the nearest H3 5+17n frame count)")
            length_f = snapped
            lengths_moved = True
        length = int(length_f)
        if i == 0:
            if overlap:
                raise ValueError("H3MD Director: segment 1 cannot have overlapIn (no previous segment)")
            start = 0
        else:
            prev = segments[-1]
            if not valid_overlap(overlap):
                raise ValueError(
                    f"H3MD Director: segment {n} overlapIn {overlap} must be 0 or 5+17k "
                    f"(nearest: {nearest_aligned(overlap)}); 1-frame overlap is not supported")
            if overlap >= min(prev["length"], length):
                raise ValueError(
                    f"H3MD Director: segment {n} overlapIn {overlap} must be shorter than both "
                    f"segment lengths ({prev['length']}, {length})")
            start = prev["startFrame"] + prev["length"] - overlap
        if s.get("startFrame") is not None and int(s["startFrame"]) != start and not lengths_moved:
            raise ValueError(
                f"H3MD Director: segment {n} startFrame {s['startFrame']} disagrees with "
                f"previous end - overlapIn = {start}")

        audio_mode = str(s.get("audioMode") or "reference")
        if audio_mode not in ("reference", "locked", "none"):
            raise ValueError(f"H3MD Director: segment {n} audioMode must be reference|locked|none")

        images = [str(x) for x in (s.get("images") or [])]
        for image_id in images:
            if image_id not in media["images"]:
                raise ValueError(f"H3MD Director: segment {n} references unknown image '{image_id}'")
        if len(images) > MAX_IMAGES:
            raise ValueError(f"H3MD Director: segment {n} has {len(images)} images (max {MAX_IMAGES})")

        videos = []
        depth_taken = False
        for v in s.get("videos") or []:
            if isinstance(v, str):
                v = {"id": v}
            vid = str(v.get("id"))
            if vid not in media["videos"]:
                raise ValueError(f"H3MD Director: segment {n} references unknown video '{vid}'")
            trim_start = max(0.0, _num(v.get("trimStart"), 0.0))
            ref = {"id": vid, "trimStart": trim_start, "useSoundtrack": bool(v.get("useSoundtrack", True))}
            # H3MD depth (2026-10-26): depth (default false) kept ONLY when non-default, so old plans and depth-off
            # plans produce identical segments (and cache keys).  H3MD audioui (2026-10-26): the per-video `ref` flag
            # was removed; a legacy ref=false is IGNORED (the video is a normal reference again) and noted
            if not bool(v.get("ref", True)):
                notes.append(f"WARNING segment {n}: video '{vid}' has legacy ref=false; ignored (ref option removed): "
                             f"it is used as a <Video N> reference again (remove the video from the shot to drop it)")
            if bool(v.get("depth", False)):
                if depth_taken:
                    notes.append(f"WARNING segment {n}: more than one depth video; only the first is used ('{vid}' ignored)")
                else:
                    depth_taken = True
                    ref["depth"] = True
                    sam3 = str(v.get("sam3Prompt") or "").strip()
                    if sam3 and v.get("sam3", True) is not False:   # 2026-10-27 sam3box: unticked = no SAM3 (text kept)
                        ref["sam3Prompt"] = sam3
            videos.append(ref)
        n_ref_videos = len(videos)   # H3MD audioui: every video takes a MAX_VIDEOS slot (ref flag removed)
        if n_ref_videos > MAX_VIDEOS:
            raise ValueError(f"H3MD Director: segment {n} has {n_ref_videos} videos (max {MAX_VIDEOS})")

        audio_mode, audios, locked = resolve_shot_audio(s, n, media, audio_mode)
        # H3MD lockexcl (2026-10-26): a video lock and that video's soundtrack are mutually exclusive, and the
        # locked track starts at the video's start (trimStart) of this shot.  Only shots with a video lock change.
        if audio_mode == "locked" and locked and locked["kind"] == "video":
            lref = next((r for r in videos if r["id"] == locked["id"]), None)
            if lref is not None:
                if lref["useSoundtrack"]:
                    notes.append(f"segment {n}: video '{locked['id']}' is locked; its soundtrack is not also passed "
                                 f"as an <Audio> reference (lock and soundtrack are exclusive)")
                for r in videos:
                    if r["id"] == locked["id"]:
                        r["useSoundtrack"] = False
                # H3MD lockrule (2026-10-26): the locked start follows the video's start by rule (stale saved
                # offsets are replaced): start - overlapIn/24 (syncfix: may be negative = leading silence)
                locked["offset"] = video_lock_offset(lref["trimStart"], overlap)

        seg = {
            "index": i, "startFrame": start, "length": length, "overlapIn": overlap,
            "shotIndex": i,   # H3MD refmodfix (2026-10-27): raw shot index; autosplit renumbers "index", RefMod uses this
            "_content": None if content is None else round(content, 3),
            "_raw_prompt": s.get("prompt", ""),
            "prompt": resolve_prompt(global_prompt, global_mode, s.get("prompt", "")),
            "images": images, "videos": videos, "audios": audios,
            "audioMode": audio_mode, "locked": locked,
        }
        if not seg["prompt"]:
            raise ValueError(f"H3MD Director: segment {n} has no prompt (segment prompt and globalPrompt empty)")
        seg["labels"] = compute_labels(seg, media)
        n_soundtracks = sum(1 for lab in seg["labels"] if lab["kind"] == "video_soundtrack")
        if n_soundtracks > MAX_VIDEOS:
            raise ValueError(f"H3MD Director: segment {n} has too many soundtracks")
        segments.append(seg)

    if legacy:
        notes.append(f"{'/'.join(legacy)} ignored (older plan): manual split keys are not used (H3MD autosplit decides)")
    # H3MD autosplit (2026-10-27): over-budget shots -> equal continuation parts (plan "autoSplit": false = off)
    rvh_mode = normalize_ref_video_height(raw.get("refVideoHeight")) or "native"
    budget_info = None
    if budget is None and raw.get("autoSplit", True) is not False:
        b = budget_info = AS.token_budget(raw, REF_VIDEO_AUTO_TOKEN_LIMIT)
        budget = b["tokens"]
        notes.append(f"token budget {budget:,} per segment (available {b['avail_gib']} GiB"
                     + (f" after ComfyUI's reserve {b['reserve_gib']} GiB" if b["reserve_gib"] is not None else "")
                     + f", safety {b['safety']})")
    segments = AS.apply(sys.modules[__name__], segments, media, raw, w, h, rvh_mode, notes, motion=motion,
                        extra_tokens=extra_tokens, budget=budget, sizes=sizes, report=report)
    for seg in segments:
        for key in ("_raw_prompt", "_content"):
            seg.pop(key, None)
        seg["prompt"] = fix_duration_line(seg["prompt"], int(seg["length"]), int(seg["overlapIn"]))
        if continuity and seg["overlapIn"] > 0:
            seg["prompt"] = continuity_note(seg["overlapIn"]) + "\n\n" + seg["prompt"]
    ref_video_height = normalize_ref_video_height(raw.get("refVideoHeight"))
    if ref_video_height is None:
        notes.append(f"refVideoHeight {raw.get('refVideoHeight')!r} unknown -> native")
        ref_video_height = "native"
    last = segments[-1]
    out = {
        "version": 1, "fps": FPS, "width": w, "height": h,
        "refVideoHeight": ref_video_height,
        "globalPrompt": global_prompt, "globalMode": global_mode,
        "media": media, "segments": segments,
        "idRef": id_ref,   # 2026-10-28 idref: {"id", "file"} or None
        "total_frames": last["startFrame"] + last["length"],
        "tokenBudget": budget,   # H3MD autosplit: also the refVideoHeight=auto limit (None = autoSplit off)
        "tokenBudgetInfo": budget_info,
        "notes": notes,
    }
    return out


def plan_summary(plan: dict) -> str:
    shots = f"{len(plan['shots'])} shot(s) -> " if plan.get("shots") is not None else ""
    lines = [f"H3MD plan v1: {plan['width']}x{plan['height']} @24fps, {shots}{len(plan['segments'])} segment(s), "
             f"{plan['total_frames']} frames ({plan['total_frames'] / FPS:.2f}s)"]
    for note in plan.get("notes", []):
        lines.append("note: " + note)
    for seg in plan["segments"]:
        where = f" shot {seg['shot'] + 1}.{seg['sub'] + 1}/{seg['subCount']}" if "shot" in seg else ""
        if "autoPart" in seg:   # H3MD autosplit
            where = f" shot {seg['shotIndex'] + 1} part {seg['autoPart'] + 1}/{seg['autoParts']}"
        seam = "" if seg["index"] == 0 else (f" continue {seg['overlapIn']}f" if seg["overlapIn"] else " cut")
        lines.append(
            f"[{seg['index']}]{where}{seam} frames {seg['startFrame']}-{seg['startFrame'] + seg['length']} "
            f"(len {seg['length']}, overlapIn {seg['overlapIn']}), audio={seg['audioMode']}: "
            + " ".join(lab["tag"] for lab in seg["labels"]))
    return "\n".join(lines)


PICK_LIMITS = {"images": 4, "videos": 2, "audios": 2}


def _video_has_audio(file: str):
    """True/False = the file has/has no audio stream, "missing" = not found, None = unreadable."""
    try:
        path = resolve_media_path(file)
    except FileNotFoundError:
        return "missing"
    try:
        import av
        with av.open(path) as c:
            return bool(c.streams.audio)
    except Exception:
        return None


def build_plan_info(plan: dict, probe_audio=None, exists=None) -> str:
    """Human-readable plan check (moved out of the Director editor): status line, warnings, hints.
    Only called for plans that parse_plan accepted; blocking errors still raise in parse_plan.
    probe_audio(file) -> True|False|"missing"|None and exists(file) -> bool are injectable (tests)."""
    import re
    if probe_audio is None:
        probe_audio = _video_has_audio
    if exists is None:
        def exists(file):
            try:
                resolve_media_path(file)
                return True
            except FileNotFoundError:
                return False
    segs = plan["segments"]
    shots = plan.get("shots")
    warn, info = [], []
    media = plan["media"]
    for kind in ("images", "videos", "audios"):
        for mid, item in media[kind].items():
            if kind == "videos":
                ha = probe_audio(item["file"])
                if ha == "missing":
                    warn.append(f"video '{mid}': file not found in input folder: {item['file']}")
                elif ha is False and item.get("hasAudio", True):
                    warn.append(f"video '{mid}' has no audio track but hasAudio is true (labels would shift / encode fails)")
                elif ha is True and not item.get("hasAudio", True):
                    warn.append(f"video '{mid}' has an audio track but hasAudio is false")
            elif not exists(item["file"]):
                warn.append(f"{kind[:-1]} '{mid}': file not found in input folder: {item['file']}")
    for t in plan.get("notes") or []:
        (warn if str(t).startswith("WARNING") else info).append(str(t).replace("WARNING ", "", 1))
    reps = [(k, segs[sh["segments"][0]], sh) for k, sh in enumerate(shots)] if shots else [(k, sg, None) for k, sg in enumerate(segs)]
    for k, seg, sh in reps:
        n = k + 1
        labels = seg["labels"]
        have = {lab["tag"] for lab in labels}
        used = set(re.findall(r"<(?:Picture|Video|Audio) \d+>", seg["prompt"]))
        for tag in sorted(used - have):
            warn.append(f"shot {n}: prompt mentions {tag} but this shot has no such reference")
        for lab in labels:
            if lab["tag"] not in used:
                info.append(f"shot {n}: {lab['tag']} ({lab['id']}) is not mentioned in the prompt")
        counts = {"images": len(seg["images"]), "videos": len(seg["videos"]),
                  "audios": sum(1 for lab in labels if lab["kind"] == "audio")}
        for kind, lim in PICK_LIMITS.items():
            if counts[kind] > lim:
                info.append(f"shot {n}: {counts[kind]} {kind}: H3MD Segment Pick only outputs {lim}; use H3MD Segment Encode (takes all)")
        lk = seg.get("locked")
        if lk and lk["kind"] == "video" and not media["videos"][lk["id"]].get("hasAudio", True):
            warn.append(f"shot {n}: locked source video '{lk['id']}' is marked hasAudio=false")
        if sh and len(sh["segments"]) > 1:
            subs = [segs[j]["length"] for j in sh["segments"]]
            info.append(f"shot {n}: auto-split into {len(subs)} sub-segments: {' + '.join(map(str, subs))} frames")
    total = plan["total_frames"]
    head = (f"OK plan valid · {plan['width']}x{plan['height']} · {len(segs)} segment(s)"
            + (f" from {len(shots)} shot(s)" if shots else "") + f" · total {total} frames ({total / FPS:.2f}s)"
            + (f" · {len(warn)} warning(s)" if warn else ""))
    lines = [head]
    if warn:
        lines += [f"WARNINGS ({len(warn)}):"] + [f"- {w}" for w in warn]
    if info:
        lines += [f"INFO ({len(info)}):"] + [f"- {t}" for t in info]
    return "\n".join(lines)


def get_segment(plan: dict, index: int) -> dict:
    if not isinstance(plan, dict) or plan.get("version") != 1 or "segments" not in plan:
        raise ValueError("H3MD: 'plan' must come from an H3MD Director node")
    index = int(index)
    if index < 0 or index >= len(plan["segments"]):
        raise ValueError(f"H3MD: segment_index {index} out of range (plan has {len(plan['segments'])} segments, 0-based)")
    return plan["segments"][index]


# ----------------------------------------------------------------------------
# Media loading (PyAV / PIL, native resolution)
# ----------------------------------------------------------------------------

def _input_dir() -> str:
    try:
        import folder_paths
        return folder_paths.get_input_directory()
    except Exception:
        return os.getcwd()


def resolve_media_path(file: str) -> str:
    if os.path.isabs(file) and os.path.isfile(file):
        return PATHS.check_read(file)   # shared ComfyUI: only inside input/output/temp (h3md_paths)
    base = os.path.abspath(_input_dir())
    candidate = os.path.abspath(os.path.join(base, file))
    if os.path.isfile(candidate):
        return candidate
    raise FileNotFoundError(f"H3MD: media file not found: '{file}' (looked in {base})")


def load_image(file: str) -> torch.Tensor:
    from PIL import Image, ImageOps
    import numpy as np
    with Image.open(resolve_media_path(file)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        arr = np.asarray(im, dtype=np.float32) / 255.0
    return torch.from_numpy(arr.copy())[None]


def _frame_time(frame, stream, fallback):
    if frame.pts is not None and stream.time_base is not None:
        return float(frame.pts * stream.time_base)
    return fallback


def video_stream_seconds(path: str):
    """Duration of the VIDEO stream in seconds = frame count / its average fps (any source rate, e.g. 30 fps);
    falls back to the stream duration, then the container duration.  None if unreadable / no video stream.
    (Container durations can be longer than the picture, e.g. a 3.80 s / 114-frame 30 fps clip in a 3.88 s mp4.)"""
    try:
        import av
        with av.open(path) as c:
            if not c.streams.video:
                return None
            v = c.streams.video[0]
            rate = float(v.average_rate) if v.average_rate else 0.0
            if v.frames and rate > 0:
                return round(int(v.frames) / rate, 3)
            if v.duration and v.time_base:
                return round(float(v.duration * v.time_base), 3)
            return round(float(c.duration) / 1_000_000.0, 3) if c.duration else None
    except Exception:
        return None


def refresh_video_durations(raw: dict, probe=None) -> list:
    """In place: media.videos[].duration := the real video-stream duration of the file (frames / fps).
    Returns notes.  Files that cannot be found / read keep the plan's value.
    `probe(file) -> seconds|None` is injectable (tests)."""
    notes = []
    if probe is None:
        def probe(file):
            try:
                return video_stream_seconds(resolve_media_path(file))
            except Exception:
                return None
    real = {}
    for item in ((raw.get("media") or {}).get("videos") or []):
        if not isinstance(item, dict) or not item.get("id") or not item.get("file"):
            continue
        sec = probe(item["file"])
        if sec is None:
            continue
        old = _num(item.get("duration"))
        if old is None or abs(old - sec) > 0.01:
            notes.append(f"video '{item['id']}' duration {old if old is None else round(old, 3)} -> {sec}s "
                         f"(video stream frames / fps)")
            item["duration"] = sec
        real[str(item["id"])] = sec
    return notes


def load_video_window(file: str, start: float, duration: float, max_frames: int | None = None) -> torch.Tensor:
    """Decode [start, start+duration) at 24 fps, native resolution -> IMAGE [N,H,W,3]."""
    import av
    import numpy as np
    path = resolve_media_path(file)
    count = max(1, int(round(duration * FPS)))
    if max_frames:
        count = min(count, int(max_frames))
    targets = [start + k / FPS for k in range(count)]
    out = []
    with av.open(path) as container:
        if not container.streams.video:
            raise ValueError(f"H3MD: '{file}' has no video stream")
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        seek_to = max(0.0, start - 1.0)
        if seek_to > 0 and stream.time_base:
            container.seek(int(seek_to / stream.time_base), stream=stream, backward=True)
        rate = float(stream.average_rate or FPS)
        prev = None
        ti = 0
        for idx, frame in enumerate(container.decode(stream)):
            t = _frame_time(frame, stream, seek_to + idx / rate)
            while ti < len(targets) and t >= targets[ti]:
                if prev is not None and abs(prev[0] - targets[ti]) <= abs(t - targets[ti]):
                    out.append(prev[1])
                else:
                    out.append(frame.to_ndarray(format="rgb24"))
                ti += 1
            if ti >= len(targets):
                break
            prev = (t, frame.to_ndarray(format="rgb24"))
        while ti < len(targets) and prev is not None:  # stream ended early: hold last frame only within 1 frame
            if targets[ti] - prev[0] > 1.5 / FPS:
                break
            out.append(prev[1])
            ti += 1
    if len(out) < 5:
        raise ValueError(f"H3MD: decoded only {len(out)} frames from '{file}' window {start:.2f}+{duration:.2f}s")
    arr = np.stack(out).astype(np.float32) / 255.0
    return torch.from_numpy(arr)


def load_audio_window(file: str, start: float, duration: float | None) -> dict | None:
    """Decode an audio window -> ComfyUI AUDIO {'waveform':[1,C,L], 'sample_rate'}; None if no audio stream."""
    import av
    import numpy as np
    path = resolve_media_path(file)
    with av.open(path) as container:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        sr = int(stream.rate or stream.codec_context.sample_rate or 48000)
        channels = int(getattr(stream.codec_context, "channels", 0) or 2)
        layout = "mono" if channels == 1 else "stereo"
        resampler = av.AudioResampler(format="fltp", layout=layout, rate=sr)
        seek_to = max(0.0, start - 1.0)
        if seek_to > 0 and stream.time_base:
            container.seek(int(seek_to / stream.time_base), stream=stream, backward=True)
        chunks = []
        first_t = None
        end_t = None if duration is None else start + duration + 0.5
        for frame in container.decode(stream):
            t = _frame_time(frame, stream, None)
            for rf in resampler.resample(frame):
                if first_t is None:
                    first_t = t if t is not None else 0.0
                chunks.append(rf.to_ndarray())
            if end_t is not None and t is not None and t > end_t:
                break
        try:
            for rf in resampler.resample(None):
                chunks.append(rf.to_ndarray())
        except Exception:
            pass
    if not chunks:
        return None
    data = np.concatenate(chunks, axis=1).astype(np.float32)
    offset = max(0, int(round((start - (first_t or 0.0)) * sr)))
    end = data.shape[1] if duration is None else offset + int(round(duration * sr))
    data = data[:, offset:end]
    if duration is not None and data.shape[1] < int(round(duration * sr)):
        pad = int(round(duration * sr)) - data.shape[1]
        data = np.pad(data, ((0, 0), (0, pad)))
    return {"waveform": torch.from_numpy(np.ascontiguousarray(data))[None], "sample_rate": sr}


def load_locked_audio_window(file: str, offset: float, duration: float) -> dict | None:
    """H3MD syncfix (2026-10-26): locked audio window [offset, offset+duration) on the source timeline.
    offset >= 0: exactly load_audio_window (unchanged).  offset < 0: the part before source time 0 is silence
    (-offset seconds of zeros), then the source from 0; total length round(duration * sr) samples, so audio
    sample s of the segment is always source time offset + s/sr."""
    offset = float(offset)
    if offset >= 0.0:
        return load_audio_window(file, offset, duration)
    lead = -offset
    body = load_audio_window(file, 0.0, max(0.0, duration - lead))
    if body is None:
        return None
    sr = int(body["sample_rate"])
    wave = body["waveform"]
    total = int(round(duration * sr))
    pad = min(int(round(lead * sr)), total)
    wave = torch.cat([wave.new_zeros(*wave.shape[:-1], pad), wave], dim=-1)
    return {"waveform": fit_audio_samples(wave, total), "sample_rate": sr}


def fit_audio_samples(wave: torch.Tensor, total: int) -> torch.Tensor:
    """Cut or zero-pad the TAIL of a [..., L] waveform to exactly `total` samples (the head = time 0 is kept)."""
    total = max(0, int(total))
    if wave.shape[-1] >= total:
        return wave[..., :total].contiguous()
    return torch.cat([wave, wave.new_zeros(*wave.shape[:-1], total - wave.shape[-1])], dim=-1)


def fit_audio_to_frames(audio: dict | None, frames: int) -> dict | None:
    """H3MD syncfix (2026-10-26): a decoded segment's audio made exactly as long as its picture
    (round(frames / 24 * sr) samples, tail cut or zero-padded).  The H3 audio latent has round(frames*40/24)
    steps of 800 samples (core temporal_shape), up to 1/3 step (8.3 ms) longer or shorter than the picture per
    segment; joined end to end that difference accumulated into audio-vs-picture drift."""
    if audio is None or audio.get("waveform") is None:
        return audio
    sr = int(audio["sample_rate"])
    total = int(round(int(frames) / FPS * sr))
    wave = audio["waveform"]
    if wave.shape[-1] == total:
        return audio
    return {**audio, "waveform": fit_audio_samples(wave, total)}


# ----------------------------------------------------------------------------
# Reference-video resolution (plan field "refVideoHeight", 2026-10-24)
# ----------------------------------------------------------------------------
# "native" (default, also for old plans): frames go to core exactly as before.
# "480" / "576" / "720": short edge = value, aspect kept, even dimensions, never upscaled.
# "auto": per segment, at encode time, the reference video steps down whole tiers (below) while the
#         estimated segment token total is above REF_VIDEO_AUTO_TOKEN_LIMIT; at the lowest tier it only warns.
# Frames are resized in memory before core; the media files are never touched.
REF_VIDEO_HEIGHTS = ("native", "480", "576", "720", "auto")
# (short edge max, long edge max) per tier, tried top-down; each is an aspect-kept fit, never upscaled.
REF_VIDEO_TIERS = ((768, 1344), (640, 1120), (512, 896), (480, 840))
# EMPIRICAL safety line for the estimated segment total (canvas + ref videos + ref images + RefMods, DiT tokens),
# calibrated on 2026-10-24 from a handful of real runs on Jack's PC (960x1696 canvas = 1590 tokens/latent frame):
#   OK    13-seg run, 362f shots, 854x480 ref (core 864x480):   170130 + 43335 + <=1590 image  = ~215055 (3-5 tiles, ~0.81 of limit)
#   OK    run ca6fa8d4 seg1, 192f, 1444x720 ref (core 1440x704): 90630 + 56430 + ~1590 image   = ~148650 (2 tiles)
#   SLOW  run ca6fa8d4 seg2, 277f:                             130380 + 81180 + 4608 RefMod  =  216168 (12 tiles, 110% warning, 2173 s)
#   OOM   run ca6fa8d4 seg3, 328f:                             154230 + 91080 + 4608 RefMod  =  249918 (OOM in the low-res stage)
# Middle-leaning-conservative pick between the largest clean total (~215055) and the first bad one (216168).
# The gap is <1 %, so this total is only a rough proxy (per-tile fixed rows matter more); not a measured limit.
REF_VIDEO_AUTO_TOKEN_LIMIT = 215000


def normalize_ref_video_height(value):
    """Plan value -> one of REF_VIDEO_HEIGHTS, or None if it is not a valid value.  Missing/empty -> "native"."""
    if value is None:
        return "native"
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if float(value) != int(value):
            return None
        value = str(int(value))
    text = str(value).strip().lower()
    if not text:
        return "native"
    if text.endswith("p") and text[:-1].isdigit():
        text = text[:-1]
    return text if text in REF_VIDEO_HEIGHTS else None


def _even_down(x: float) -> int:
    return max(2, int(math.floor(float(x) + 1e-6)) // 2 * 2)


def ref_video_fit(w: int, h: int, short_max: int, long_max: int = 0):
    """(w, h) -> aspect-kept fit inside short <= short_max (and long <= long_max if > 0), even dims, never upscaled.
    Returns (w, h) unchanged when it already fits."""
    w, h = int(w), int(h)
    s = float(short_max) / min(w, h)
    if long_max:
        s = min(s, float(long_max) / max(w, h))
    if s >= 1.0:
        return w, h
    return _even_down(w * s), _even_down(h * s)


def core_ref_video_size(w: int, h: int):
    """Size core MiniMaxH3ReferenceToVideo resizes a w x h reference video to (mirrors nodes_minimax_h3:
    adapt_canvas 768 short edge / 768*1344 area cap, or the native size on the 32 grid when that is smaller)."""
    ratio = w / h
    nom_w, nom_h = (768 * ratio, 768) if ratio >= 1.0 else (768, 768 / ratio)
    if nom_w * nom_h > 768 * 1344:
        s = math.sqrt(768 * 1344 / (nom_w * nom_h))
        nom_w, nom_h = nom_w * s, nom_h * s
    cw, ch = max(32, round(nom_w / 32) * 32), max(32, round(nom_h / 32) * 32)
    if w * h < cw * ch:
        cw, ch = max(32, round(w / 32) * 32), max(32, round(h / 32) * 32)
    return cw, ch


def _latent_t(n: int) -> int:
    return 2 if n <= 5 else ((n - 5) // 17) * 5 + 2


def canvas_tokens(width: int, height: int, length: int) -> int:
    return _latent_t(aligned_up(length)) * (int(height) // 32) * (int(width) // 32)


def ref_video_tokens(w: int, h: int, frames: int, length: int) -> int:
    """DiT tokens of one reference video as core builds it (resize, cut to the canvas length, trim to 17k+5)."""
    cw, ch = core_ref_video_size(w, h)
    n = min(int(frames), aligned_up(length))
    if n < 5:
        return 0
    n = 5 + 17 * ((n - 5) // 17)
    return _latent_t(n) * (ch // 32) * (cw // 32)


def ref_image_tokens(w: int, h: int, width: int, height: int, ref_image_size: str = "match") -> int:
    if ref_image_size == "match":
        scale = min(1.0, math.sqrt((width * height) / (w * h)))
    else:
        scale = min(1.0, 2048 / min(w, h))
    tw = max(32, round(w * scale / 32) * 32)
    th = max(32, round(h * scale / 32) * 32)
    return (th // 32) * (tw // 32)


def estimate_segment_tokens(width, height, length, videos, images, ref_image_size="match", extra_tokens=0) -> dict:
    """videos: [(w, h, frames)], images: [(w, h)], extra_tokens: RefMod tokens.  -> breakdown dict + "total"."""
    out = {"canvas": canvas_tokens(width, height, length),
           "videos": sum(ref_video_tokens(w, h, n, length) for w, h, n in videos),
           "images": sum(ref_image_tokens(w, h, width, height, ref_image_size) for w, h in images),
           "refmods": int(extra_tokens or 0)}
    out["total"] = out["canvas"] + out["videos"] + out["images"] + out["refmods"]
    return out


def plan_ref_video_sizes(mode, width, height, length, videos, images, ref_image_size="match", extra_tokens=0,
                         limit=REF_VIDEO_AUTO_TOKEN_LIMIT, depth=None) -> dict:
    """-> {"sizes": [(w, h)] target per video, "tier": index or None, "est": breakdown, "over": bool}.
    depth (auto only, H3MD autosplit 2026-10-27): per-video flags; a depth video (grey depth map, no detail to keep)
    is always fitted to the LOWEST tier, the tier steps only apply to the other (detail) videos."""
    mode = normalize_ref_video_height(mode) or "native"
    native = [(w, h) for w, h, _n in videos]
    if mode == "native" or not videos:
        return {"sizes": native, "tier": None, "est": None, "over": False, "mode": mode}
    if mode != "auto":
        sizes = [ref_video_fit(w, h, int(mode)) for w, h in native]
        est = estimate_segment_tokens(width, height, length, [(sw, sh, n) for (sw, sh), (_w, _h, n) in
                                                              zip(sizes, videos)], images, ref_image_size,
                                      extra_tokens)
        return {"sizes": sizes, "tier": None, "est": est, "over": False, "mode": mode}
    flags = list(depth) if depth else [False] * len(native)
    low = REF_VIDEO_TIERS[-1]
    for tier, (smax, lmax) in enumerate(REF_VIDEO_TIERS):
        sizes = [ref_video_fit(w, h, *(low if d else (smax, lmax))) for (w, h), d in zip(native, flags)]
        est = estimate_segment_tokens(width, height, length, [(sw, sh, n) for (sw, sh), (_w, _h, n) in
                                                              zip(sizes, videos)], images, ref_image_size,
                                      extra_tokens)
        if est["total"] <= limit:
            return {"sizes": sizes, "tier": tier, "est": est, "over": False, "mode": mode}
    return {"sizes": sizes, "tier": len(REF_VIDEO_TIERS) - 1, "est": est, "over": True, "mode": mode}


def resize_video_frames(frames: torch.Tensor, w: int, h: int, chunk: int = 16) -> torch.Tensor:
    """[N,H,W,3] float -> [N,h,w,3] with area interpolation (downscale only), in chunks to bound memory."""
    if frames.shape[1] == h and frames.shape[2] == w:
        return frames
    import torch.nn.functional as F
    parts = []
    for i in range(0, frames.shape[0], chunk):
        x = frames[i:i + chunk].movedim(-1, 1)
        parts.append(F.interpolate(x, size=(int(h), int(w)), mode="area").movedim(1, -1).clamp_(0.0, 1.0))
    return torch.cat(parts, 0).contiguous()


def apply_ref_video_height(plan: dict, seg: dict, result: dict, ref_image_size="match", extra_tokens=0) -> None:
    """Resize result["videos"] in place according to plan["refVideoHeight"] and log what was used."""
    mode = normalize_ref_video_height(plan.get("refVideoHeight")) or "native"
    if mode == "native" or not result["videos"]:
        return
    videos = [(int(v.shape[2]), int(v.shape[1]), int(v.shape[0])) for v in result["videos"]]
    images = [(int(im.shape[2]), int(im.shape[1])) for im in result["images"]]
    limit = int(plan.get("tokenBudget") or REF_VIDEO_AUTO_TOKEN_LIMIT)   # H3MD autosplit: same budget as the split
    overrides = seg.get("_videoOverrides") or {}   # depth route active for these videos (depth frames)
    depth = [str(r.get("id")) in overrides for r in seg["videos"]] if len(seg["videos"]) == len(videos) else None
    pick = plan_ref_video_sizes(mode, plan["width"], plan["height"], seg["length"], videos, images,
                                ref_image_size, extra_tokens, limit, depth)
    changes = []
    for i, ((w, h, _n), (tw, th)) in enumerate(zip(videos, pick["sizes"])):
        if (tw, th) != (w, h):
            result["videos"][i] = resize_video_frames(result["videos"][i], tw, th)
        cw, ch = core_ref_video_size(tw, th)
        changes.append(f"video {i + 1}: {w}x{h} -> {tw}x{th} (core {cw}x{ch})")
    est = pick["est"]
    parts = (f"est. tokens {est['total']:,} (canvas {est['canvas']:,} + videos {est['videos']:,} + "
             f"images {est['images']:,} + RefMods {est['refmods']:,})")
    if mode == "auto":
        smax, lmax = REF_VIDEO_TIERS[pick["tier"]]
        head = (f"[H3MD RefVideo] segment {seg['index'] + 1}: refVideoHeight=auto -> tier{pick['tier'] + 1} "
                f"{smax}x{lmax}{' (depth videos: lowest tier)' if depth and any(depth) else ''} | {parts} vs limit {limit:,}")
        if pick["over"]:
            LOG.warning("%s | still over the limit at the lowest tier - reference kept | %s", head, "; ".join(changes))
            return
    else:
        head = f"[H3MD RefVideo] segment {seg['index'] + 1}: refVideoHeight={mode} | {parts}"
    LOG.info("%s | %s", head, "; ".join(changes))


def pick_segment_media(plan: dict, index: int, ref_image_size: str = "match", extra_tokens: int = 0) -> dict:
    seg = get_segment(plan, index)
    media = plan["media"]
    result = {"segment": seg, "images": [], "videos": [], "video_audios": [], "audios": [], "locked_audio": None}
    for image_id in seg["images"]:
        result["images"].append(load_image(media["images"][image_id]["file"]))
    audio_allowed = seg["audioMode"] != "none"
    overrides = seg.get("_videoOverrides") or {}   # H3MD depth: depth frames replace a depth-on video's picture
    locked_vid = locked_video_id(seg)   # H3MD lockexcl: the locked video's sound is only the locked track
    for ref in seg["videos"]:
        # H3MD audioui (2026-10-26): no ref flag any more; picture line (raw or depth frames) and soundtrack line
        # (useSoundtrack, independent of depth) are decided separately below
        info = media["videos"][ref["id"]]
        # H3MD oneshot (2026-10-26): window origin = output frame 0 = start - overlapIn/24 (video_window_origin; hard
        # cut -> start, unchanged); full reference video from there, capped by the segment length.  A negative origin
        # repeats the first source frame (load_video_window holds frame 0 for targets before it).
        origin = video_window_origin(ref, seg)
        src_dur = _num(info.get("duration"))
        ref_dur = seg["length"] / FPS if src_dur is None else max(0.0, src_dur - origin)
        frames = overrides.get(ref["id"])
        if frames is None:
            frames = load_video_window(info["file"], origin, ref_dur, max_frames=seg["length"])
        soundtrack = None
        if audio_allowed and ref["useSoundtrack"] and ref["id"] != locked_vid and info.get("hasAudio", True):
            # H3MD oneshot: the soundtrack <Audio> starts at the same origin as its <Video N> (origin >= 0: exactly
            # load_audio_window; negative: leading silence)
            soundtrack = load_locked_audio_window(info["file"], origin, frames.shape[0] / FPS)
            if soundtrack is None:
                raise ValueError(
                    f"H3MD: video '{ref['id']}' has no audio track; set useSoundtrack=false or hasAudio=false "
                    f"(label numbering depends on it)")
        result["videos"].append(frames)
        result["video_audios"].append(soundtrack)
    apply_ref_video_height(plan, seg, result, ref_image_size, extra_tokens)   # refVideoHeight (native = no-op)
    if audio_allowed:
        for audio_id in seg["audios"]:
            # --- H3MD refwindow hook: start offset set by ref_window_mode=advance (h3md_refwindow), else 0 ---
            a_start = float((seg.get("audioStarts") or {}).get(audio_id, 0.0))
            audio = load_audio_window(media["audios"][audio_id]["file"], a_start, None)
            if audio is None:
                raise ValueError(f"H3MD: audio '{audio_id}' has no audio stream")
            result["audios"].append(audio)
    if seg["audioMode"] == "locked":
        lk = seg["locked"]
        file = media["videos" if lk["kind"] == "video" else "audios"][lk["id"]]["file"]
        # H3MD syncfix: offset may be negative (video lock with start < overlapIn/24) -> leading silence
        result["locked_audio"] = load_locked_audio_window(file, lk["offset"], seg["length"] / FPS)  # H3MD seamtrim: offset as planned (overlap back-fill kept)
        if result["locked_audio"] is None:
            raise ValueError(f"H3MD: locked audio source '{lk['kind']}:{lk['id']}' has no audio stream")
    return result


# ----------------------------------------------------------------------------
# Core encoder call
# ----------------------------------------------------------------------------

def _core_h3():
    from comfy_extras import nodes_minimax_h3
    return nodes_minimax_h3


def _unwrap_node_output(out):
    for attr in ("args", "result"):
        value = getattr(out, attr, None)
        if value is not None:
            return tuple(value)
    if isinstance(out, dict) and "result" in out:
        return tuple(out["result"])
    return tuple(out)


def build_core_kwargs(picked: dict) -> dict:
    ref_images = {f"ref_image_{i}": img for i, img in enumerate(picked["images"])}
    ref_videos = {f"ref_video_{k}": frames for k, frames in enumerate(picked["videos"])}
    ref_video_audios = {f"ref_video_audio_{k}": a for k, a in enumerate(picked["video_audios"]) if a is not None}
    ref_audios = {f"ref_audio_{j}": a for j, a in enumerate(picked["audios"])}
    return {"ref_images": ref_images or None, "ref_videos": ref_videos or None,
            "ref_video_audios": ref_video_audios or None, "ref_audios": ref_audios or None}


def zero_out_conditioning(conditioning):
    try:
        import nodes as comfy_nodes
        return comfy_nodes.ConditioningZeroOut().zero_out(conditioning)[0]
    except Exception:
        out = []
        for t in conditioning:
            d = t[1].copy()
            if d.get("pooled_output") is not None:
                d["pooled_output"] = torch.zeros_like(d["pooled_output"])
            out.append([torch.zeros_like(t[0]), d])
        return out


# ----------------------------------------------------------------------------
# Continue / Join core logic
# ----------------------------------------------------------------------------

def _streams(latent: dict, name: str):
    samples = latent.get("samples") if isinstance(latent, dict) else None
    tensors = getattr(samples, "tensors", None)
    if not getattr(samples, "is_nested", False) or tensors is None or len(tensors) != 2:
        raise ValueError(f"H3MD Continue: {name} must be a nested MiniMax H3 AV latent (video, audio)")
    video, audio = tensors
    if video.ndim != 5 or video.shape[1] != 24 or audio.ndim != 4:
        raise ValueError(f"H3MD Continue: {name} has unexpected shapes video={tuple(video.shape)} audio={tuple(audio.shape)}")
    return video, audio


def _nested(tensors):
    import comfy.nested_tensor
    return comfy.nested_tensor.NestedTensor(tuple(tensors))


def encode_locked_audio(audio_vae, audio: dict, target_audio_t: int, like: torch.Tensor) -> torch.Tensor:
    core = _core_h3()
    z, _ = core._encode_ref_audio(audio_vae, audio)
    z = z.to(device=like.device, dtype=like.dtype)
    if z.ndim != like.ndim or tuple(z.shape[1:-1]) != tuple(like.shape[1:-1]):
        raise ValueError(f"H3MD Continue: locked audio latent {tuple(z.shape)} incompatible with target {tuple(like.shape)}")
    if z.shape[-1] < target_audio_t:
        z = torch.nn.functional.pad(z, (0, target_audio_t - z.shape[-1]))
    return z[..., :target_audio_t]


def continue_latent(target_latent: dict, previous_latent: dict | None, overlap_frames: int,
                    continue_audio: bool = True, locked_audio_latent: torch.Tensor | None = None,
                    next_latent: dict | None = None, tail_overlap: int = 0):
    """Return (latent_dict, info). latent_dict contains only 'samples' (+ 'noise_mask' when anything is kept).

    Only previous_latent['samples'] / next_latent['samples'] are read - every other key is ignored.
    H3MD rerun (2026-10-30) tail lock: with next_latent + tail_overlap, the LAST video tokens (and audio) are
    pinned to the head of the (already rendered) next segment, mask 0, so the next segment needs no re-render.
    """
    video, audio = _streams(target_latent, "latent")
    video = video.clone()
    audio = audio.clone()
    video_mask = torch.ones((1, 1, video.shape[2], 1, 1), dtype=torch.float32, device=video.device)
    audio_mask = torch.ones((1, 1, 1, audio.shape[-1]), dtype=torch.float32, device=audio.device)
    info = {"overlap_frames": 0, "video_tokens": 0, "audio_tokens": 0, "audio_locked": False}

    overlap = int(overlap_frames or 0)
    if previous_latent is not None and overlap > 0:
        if not valid_overlap(overlap):
            raise ValueError(f"H3MD Continue: overlap_frames {overlap} must be 0 or 5+17k")
        prev_only = {"samples": previous_latent.get("samples") if isinstance(previous_latent, dict) else None}
        prev_video, prev_audio = _streams(prev_only, "previous_latent")
        if tuple(prev_video.shape[3:]) != tuple(video.shape[3:]):
            raise ValueError(
                f"H3MD Continue: previous latent spatial size {tuple(prev_video.shape[3:])} != target "
                f"{tuple(video.shape[3:])}; use the same width/height for every segment and hand over the "
                f"FINAL full-resolution latent")
        vt = video_tokens(overlap)
        if vt >= video.shape[2] or vt > prev_video.shape[2]:
            raise ValueError(f"H3MD Continue: overlap {overlap} frames ({vt} tokens) does not fit the latents")
        tail = prev_video[:1, :, -vt:].to(device=video.device, dtype=video.dtype)
        video[:, :, :vt] = tail.expand(video.shape[0], -1, -1, -1, -1)
        video_mask[:, :, :vt] = 0.0
        info.update(overlap_frames=overlap, video_tokens=vt)
        if continue_audio and locked_audio_latent is None:
            at = min(audio_tokens(overlap), prev_audio.shape[-1], audio.shape[-1])
            if at > 0:
                atail = prev_audio[:1, ..., -at:].to(device=audio.device, dtype=audio.dtype)
                audio[..., :at] = atail.expand(audio.shape[0], *([-1] * (audio.ndim - 1)))
                audio_mask[..., :at] = 0.0
                info["audio_tokens"] = at
    elif previous_latent is not None and overlap <= 0:
        LOG.info("[H3MD Continue] previous_latent given but overlap is 0: starting a fresh segment")

    tail = int(tail_overlap or 0)
    info["tail_frames"] = 0
    info["tail_tokens"] = 0
    info["tail_audio_tokens"] = 0
    if next_latent is not None and tail > 0:  # H3MD rerun: tail lock
        if not valid_overlap(tail):
            raise ValueError(f"H3MD Continue: tail_overlap {tail} must be 0 or 5+17k")
        next_only = {"samples": next_latent.get("samples") if isinstance(next_latent, dict) else None}
        next_video, next_audio = _streams(next_only, "next_latent")
        if tuple(next_video.shape[3:]) != tuple(video.shape[3:]) or next_video.shape[1] != video.shape[1]:
            raise ValueError(
                f"H3MD Continue: next latent size {tuple(next_video.shape[1:])} does not match this segment "
                f"{tuple(video.shape[1:])}")
        tt = video_tokens(tail)
        head_t = info["video_tokens"]
        if tt > next_video.shape[2] or head_t + tt >= video.shape[2]:
            raise ValueError(f"H3MD Continue: tail lock of {tail} frames ({tt} tokens) does not fit a segment of "
                             f"{video.shape[2]} tokens with a {head_t}-token head")
        head = next_video[:1, :, :tt].to(device=video.device, dtype=video.dtype)
        video[:, :, -tt:] = head.expand(video.shape[0], -1, -1, -1, -1)
        video_mask[:, :, -tt:] = 0.0
        info.update(tail_frames=tail, tail_tokens=tt)
        if locked_audio_latent is None:
            at = min(audio_tokens(tail), next_audio.shape[-1], audio.shape[-1])
            if at > 0:
                ahead = next_audio[:1, ..., :at].to(device=audio.device, dtype=audio.dtype)
                audio[..., -at:] = ahead.expand(audio.shape[0], *([-1] * (audio.ndim - 1)))
                audio_mask[..., -at:] = 0.0
                info["tail_audio_tokens"] = at

    if locked_audio_latent is not None:
        z = locked_audio_latent.to(device=audio.device, dtype=audio.dtype)
        if z.ndim == 4 and z.shape[0] != audio.shape[0]:
            z = z[:1].expand(audio.shape[0], -1, -1, -1)
        audio = z[..., :audio.shape[-1]].clone()
        audio_mask.zero_()
        info["audio_locked"] = True

    out = {"samples": _nested((video, audio))}
    if bool((video_mask < 1).any()) or bool((audio_mask < 1).any()):
        out["noise_mask"] = _nested((video_mask, audio_mask))
    return out, info


# ----------------------------------------------------------------------------
# H3MD drift: dynamic overlap re-noise mask (own implementation)
# ----------------------------------------------------------------------------
# Idea: the copied overlap prefix is not frozen for the whole schedule.  At each
# sampler step the kept (mask==0) prefix tokens get a fractional "generate"
# weight  m = ramp_weight(k) * sigma_next / sigma_current, so early, noisy steps
# may harmonise the prefix with the new prompt/refs while late steps converge to
# fully kept (sigma_next -> 0 gives m -> 0).  ramp_weight is largest for the
# token farthest from the seam and falls linearly to 0; the last `lock_tokens`
# prefix tokens (nearest the seam, the frames that border the new content) are
# always locked at 0.  Audio masks are left static.  The function only lowers
# the protection of positions that the static mask keeps; it never touches
# positions the static mask generates.

def drift_token_weights(prefix_tokens: int, lock_tokens: int, strength: float, total_tokens: int) -> torch.Tensor:
    w = torch.zeros(int(total_tokens), dtype=torch.float32)
    n = max(0, min(int(prefix_tokens), int(total_tokens)) - max(0, int(lock_tokens)))
    s = float(min(max(strength, 0.0), 1.0))
    for k in range(n):
        w[k] = s * (n - k) / n
    return w


def sigma_step_ratio(sigma, sigmas) -> float:
    """sigma_next / sigma_current for the step whose sigma matches `sigma` (0 at the last step)."""
    cur = float(torch.as_tensor(sigma).flatten()[0])
    if cur <= 0.0:
        return 0.0
    if sigmas is None or len(sigmas) == 0:
        return 1.0
    sig = torch.as_tensor(sigmas, dtype=torch.float64).flatten()
    idx = int(torch.argmin((sig - cur).abs()))
    nxt = float(sig[idx + 1]) if idx + 1 < sig.numel() else 0.0
    return float(min(max(nxt / cur, 0.0), 1.0))


class H3MDDriftMask:
    """denoise_mask_function: sigma-scheduled re-noise of the kept overlap prefix.

    Works with ComfyUI's packed AV mask ([B,1,N]: video [B,C,T,H,W] flattened then
    audio) and with a plain 5-D video mask.  Only plain numbers are stored so the
    object survives ModelPatcher.clone() deep copies.
    """

    def __init__(self, prefix_tokens, lock_tokens, strength, video_tokens, audio_tokens,
                 video_channels=24, audio_elems_per_token=64):
        self.prefix_tokens = int(prefix_tokens)
        self.lock_tokens = int(lock_tokens)
        self.strength = float(strength)
        self.video_tokens = int(video_tokens)
        self.audio_tokens = int(audio_tokens)
        self.video_channels = int(video_channels)
        self.audio_elems_per_token = int(audio_elems_per_token)
        self._warned = False

    def _warn(self, msg):
        if not self._warned:
            LOG.warning("[H3MD drift] %s; static mask used", msg)
            self._warned = True

    def time_index(self, mask: torch.Tensor):
        """Per-element video time index (same shape as mask, -1 for non-video), or None."""
        T = self.video_tokens
        if mask.ndim == 5:
            if mask.shape[2] != T:
                return None
            t = torch.arange(T, device=mask.device).view(1, 1, T, 1, 1)
            return t.expand_as(mask)
        if mask.ndim == 3:
            n = mask.shape[-1]
            video_len = n - self.audio_tokens * self.audio_elems_per_token
            per_c = self.video_channels * T
            if video_len <= 0 or video_len % per_c:
                return None
            hw = video_len // per_c
            t_video = (torch.arange(video_len, device=mask.device) // hw) % T
            t_all = torch.full((n,), -1, dtype=torch.long, device=mask.device)
            t_all[:video_len] = t_video
            return t_all.view(1, 1, n).expand_as(mask)
        return None

    def __call__(self, sigma, denoise_mask, extra_options=None):
        if denoise_mask is None or self.prefix_tokens <= self.lock_tokens:
            return denoise_mask
        ratio = sigma_step_ratio(sigma, (extra_options or {}).get("sigmas"))
        if ratio <= 0.0 or self.strength <= 0.0:
            return denoise_mask
        t = self.time_index(denoise_mask)
        if t is None:
            self._warn(f"unexpected mask layout {tuple(denoise_mask.shape)}")
            return denoise_mask
        w = drift_token_weights(self.prefix_tokens, self.lock_tokens, self.strength, self.video_tokens)
        w = w.to(device=denoise_mask.device, dtype=denoise_mask.dtype)
        valid = t >= 0
        drift = torch.where(valid, w[t.clamp(min=0)], torch.zeros((), device=w.device, dtype=w.dtype)) * ratio
        kept = denoise_mask <= 0
        return torch.where(kept & (drift > 0), drift, denoise_mask)


def install_drift(model, video_tokens_total, audio_tokens_total, prefix_tokens, lock_tokens, strength):
    fn = H3MDDriftMask(prefix_tokens, lock_tokens, strength, video_tokens_total, audio_tokens_total)
    m = model.clone()
    if hasattr(m, "set_model_denoise_mask_function"):
        m.set_model_denoise_mask_function(fn)
    else:
        m.model_options["denoise_mask_function"] = fn
    m.model_options["h3md_drift"] = fn  # lets the H3MD Sampler put it on a separate high-res model too
    return m, fn


def _match_audio(audio: dict, sample_rate: int, channels: int) -> torch.Tensor:
    wav = audio["waveform"]
    if int(audio["sample_rate"]) != int(sample_rate):
        try:
            import comfy.audio
            wav = comfy.audio.resample(wav, int(audio["sample_rate"]), int(sample_rate))
        except Exception:
            import torchaudio
            wav = torchaudio.functional.resample(wav, int(audio["sample_rate"]), int(sample_rate))
    if wav.shape[1] != channels:
        if wav.shape[1] == 1:
            wav = wav.expand(-1, channels, -1)
        elif channels == 1:
            wav = wav.mean(dim=1, keepdim=True)
        else:
            raise ValueError(f"H3MD Join: cannot match {wav.shape[1]} audio channels to {channels}")
    return wav


def join_segments(images: torch.Tensor, audio: dict | None, overlap_frames: int,
                  merged_images: torch.Tensor | None = None, merged_audio: dict | None = None,
                  crossfade_ms: int = 0):
    overlap = max(0, int(overlap_frames or 0))
    if merged_images is None:
        return images, audio, int(images.shape[0])
    if tuple(images.shape[1:]) != tuple(merged_images.shape[1:]):
        raise ValueError(f"H3MD Join: frame size {tuple(images.shape[1:])} != merged {tuple(merged_images.shape[1:])}")
    if overlap >= images.shape[0]:
        raise ValueError(f"H3MD Join: overlap {overlap} >= new segment frames {images.shape[0]}")
    new_frames = images[overlap:].to(device=merged_images.device, dtype=merged_images.dtype)
    out_images = torch.cat([merged_images, new_frames], dim=0)
    out_audio = join_audio(audio, overlap, merged_audio, crossfade_ms)
    return out_images, out_audio, int(out_images.shape[0])


def join_audio(audio: dict | None, overlap: int, merged_audio: dict | None, crossfade_ms: int = 0):
    """Audio half of join_segments: drop `overlap` frames of audio from the new segment head and append."""
    overlap = max(0, int(overlap or 0))
    out_audio = merged_audio
    if audio is not None and merged_audio is not None:
        sr = int(merged_audio["sample_rate"])
        base = merged_audio["waveform"]
        new = _match_audio(audio, sr, base.shape[1]).to(device=base.device, dtype=base.dtype)
        trim = int(round(overlap / FPS * sr))
        trim = min(trim, new.shape[-1])
        cf = min(int(round(max(0, int(crossfade_ms)) / 1000.0 * sr)), trim, base.shape[-1])
        if cf > 0:
            ramp = torch.linspace(0.0, 1.0, cf, device=base.device, dtype=base.dtype)
            blended = base[..., -cf:] * (1 - ramp) + new[..., trim - cf:trim] * ramp
            wave = torch.cat([base[..., :-cf], blended, new[..., trim:]], dim=-1)
        else:
            wave = torch.cat([base, new[..., trim:]], dim=-1)
        out_audio = {"waveform": wave, "sample_rate": sr}
    elif audio is not None and merged_audio is None:
        LOG.warning("[H3MD Join] merged_audio missing: output audio covers only the new segment")
        out_audio = audio
    return out_audio


# ----------------------------------------------------------------------------
# ComfyUI nodes
# ----------------------------------------------------------------------------

class H3MDDirector:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "plan_json": ("STRING", {"multiline": True, "default": "",
                                         "tooltip": "H3MD plan v1 JSON (see module docstring). UI comes in Phase 2."}),
            },
            "optional": {
                "width": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 64,
                                  "tooltip": "0 = use plan width (snapped to a multiple of 64)"}),
                "height": ("INT", {"default": 0, "min": 0, "max": 8192, "step": 64,
                                   "tooltip": "0 = use plan height"}),
                "continuity_prompt": ("BOOLEAN", {"default": True,
                                      "tooltip": "Prepend a short continuity sentence to every continued segment "
                                                 "(overlapIn > 0)."}),
            },
        }

    RETURN_TYPES = (PLAN_TYPE, "INT", "INT", "STRING", "STRING")
    RETURN_NAMES = ("plan", "segment_count", "total_frames", "summary", "plan_info")   # plan_info appended: old links keep their slots
    FUNCTION = "build"
    CATEGORY = CATEGORY

    def build(self, plan_json, width=0, height=0, continuity_prompt=True, extra_tokens=None):
        raw, extra, sizes = plan_json, [], None
        try:
            loaded = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
            if isinstance(loaded, dict):
                raw = json.loads(json.dumps(loaded))
                extra = refresh_video_durations(raw)
                sizes = AS.probe_media_sizes(raw, resolve_media_path)   # H3MD autosplit: reference pixel sizes
        except (json.JSONDecodeError, TypeError):
            pass  # parse_plan reports it
        motion = make_motion(raw)   # H3MD autosplit: same motion source as the editor preview (/h3md/autosplit)
        plan = parse_plan(raw, width, height, True, motion=motion, sizes=sizes,
                          extra_tokens=extra_tokens)   # continuity_prompt is hidden in the UI and always on (2026-10-24); widget kept so old widgets_values stay aligned
        plan["notes"] = extra + plan.get("notes", [])
        summary = plan_summary(plan)
        LOG.info("[H3MD Director] %s", summary.replace("\n", " | "))
        try:
            info = build_plan_info(plan)
        except Exception as exc:   # a check must never stop a valid plan from running
            info = f"OK plan valid (plan_info check failed: {exc})"
        return (plan, len(plan["segments"]), plan["total_frames"], summary, info)


class H3MDSegmentPick:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"plan": (PLAN_TYPE,),
                             "segment_index": ("INT", {"default": 0, "min": 0, "max": 63})}}

    RETURN_TYPES = ("STRING", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE", "AUDIO", "AUDIO",
                    "AUDIO", "AUDIO", "AUDIO", "INT", "INT", "INT", "INT", "STRING")
    RETURN_NAMES = ("prompt", "ref_image_1", "ref_image_2", "ref_image_3", "ref_image_4",
                    "ref_video_1", "ref_video_2", "ref_video_audio_1", "ref_video_audio_2",
                    "ref_audio_1", "ref_audio_2", "locked_audio",
                    "width", "height", "length", "overlap", "labels_json")
    FUNCTION = "pick"
    CATEGORY = CATEGORY

    def pick(self, plan, segment_index):
        picked = pick_segment_media(plan, segment_index)
        seg = picked["segment"]
        if len(picked["images"]) > 4 or len(picked["videos"]) > 2 or len(picked["audios"]) > 2:
            LOG.warning("[H3MD Pick] segment %d has more refs than Pick outputs; use H3MD Segment Encode for all",
                        seg["index"])

        def slot(items, i):
            return items[i] if i < len(items) else None

        return (seg["prompt"],
                slot(picked["images"], 0), slot(picked["images"], 1), slot(picked["images"], 2), slot(picked["images"], 3),
                slot(picked["videos"], 0), slot(picked["videos"], 1),
                slot(picked["video_audios"], 0), slot(picked["video_audios"], 1),
                slot(picked["audios"], 0), slot(picked["audios"], 1),
                picked["locked_audio"],
                plan["width"], plan["height"], seg["length"], seg["overlapIn"],
                json.dumps(seg["labels"], ensure_ascii=False))


class H3MDSegmentEncode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "plan": (PLAN_TYPE,),
                "segment_index": ("INT", {"default": 0, "min": 0, "max": 63}),
                "clip": ("CLIP",),
                "vae": ("VAE",),
                "audio_vae": ("VAE",),
                "ref_image_size": (["match", "max"], {"default": "match"}),
            },
            "optional": {
                "prompt_override": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "CONDITIONING", "LATENT", "STRING", "STRING", "INT", "INT", "INT", "INT", "AUDIO")
    RETURN_NAMES = ("positive", "negative", "latent", "prompt", "labels_json", "length", "overlap",
                    "width", "height", "locked_audio")
    FUNCTION = "encode"
    CATEGORY = CATEGORY

    def encode(self, plan, segment_index, clip, vae, audio_vae, ref_image_size="match", prompt_override=""):
        picked = pick_segment_media(plan, segment_index, ref_image_size)
        seg = picked["segment"]
        prompt = (prompt_override or "").strip() or seg["prompt"]
        core = _core_h3()
        kwargs = build_core_kwargs(picked)
        out = core.MiniMaxH3ReferenceToVideo.execute(
            clip=clip, prompt=prompt, width=plan["width"], height=plan["height"], length=seg["length"],
            ref_image_size=ref_image_size, vae=vae, audio_vae=audio_vae, **kwargs)
        positive, latent = _unwrap_node_output(out)[:2]
        latent = {"samples": latent["samples"]}
        negative = zero_out_conditioning(positive)
        labels = json.dumps(seg["labels"], ensure_ascii=False)
        LOG.info("[H3MD Encode] segment %d: %dx%d len %d overlapIn %d refs %s video windows %s",
                 seg["index"], plan["width"], plan["height"], seg["length"], seg["overlapIn"],
                 " ".join(lab["tag"] for lab in seg["labels"]),
                 [(tuple(v.shape[1:3]), v.shape[0]) for v in picked["videos"]])
        return (positive, negative, latent, prompt, labels, seg["length"], seg["overlapIn"],
                plan["width"], plan["height"], picked["locked_audio"])


class H3MDSegmentContinue:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING",),
                "latent": ("LATENT", {"tooltip": "Target AV latent from H3MD Segment Encode"}),
                "model": ("MODEL",),
                "overlap_frames": ("INT", {"default": 0, "min": 0, "max": 3600, "forceInput": True}),
                "continue_audio": ("BOOLEAN", {"default": True}),
                "drift_strength": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 1.0, "step": 0.05,
                                   "tooltip": "Drift: peak re-noise weight for the overlap prefix token farthest from the seam "
                                              "(the prefix is restored exactly at the end). 0 = prefix kept unchanged "
                                              "during sampling too (the former static_lock)."}),
                "drift_lock_tokens": ("INT", {"default": 2, "min": 0, "max": 64,
                                      "tooltip": "Prefix tokens nearest the seam that stay locked (1 token = 3.4 frames)."}),
                "release_vram": ("BOOLEAN", {"default": True,
                                 "tooltip": "Between segments: unload idle models (text encoder, VAE, upscaler) and "
                                            "soft_empty_cache. The base UNet is kept loaded."}),
            },
            "optional": {
                "previous_latent": ("LATENT", {"tooltip": "FINAL sampled latent of the previous segment. Only "
                                               "'samples' is read; all other keys are dropped."}),
                "locked_audio": ("AUDIO",),
                "audio_vae": ("VAE",),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "LATENT", "MODEL", "INT")
    RETURN_NAMES = ("positive", "latent", "model", "actual_overlap")
    FUNCTION = "prepare"
    CATEGORY = CATEGORY

    def prepare(self, positive, latent, model, overlap_frames, continue_audio=True,
                drift_strength=0.6, drift_lock_tokens=2, release_vram=True,
                previous_latent=None, locked_audio=None, audio_vae=None, next_latent=None, tail_overlap=0):
        locked_z = None
        if locked_audio is not None:
            if audio_vae is None:
                raise ValueError("H3MD Continue: locked_audio needs audio_vae")
            _, target_audio = _streams(latent, "latent")
            locked_z = encode_locked_audio(audio_vae, locked_audio, target_audio.shape[-1], target_audio)
        out, info = continue_latent(latent, previous_latent, overlap_frames, continue_audio, locked_z,
                                    next_latent=next_latent, tail_overlap=tail_overlap)  # H3MD rerun: tail lock
        out_model = model
        if info["video_tokens"] > 0:  # H3MD drift (the only continuation mode since 2026-10-29)
            video, audio = _streams(out, "latent")
            out_model, _ = install_drift(model, video.shape[2], audio.shape[-1], info["video_tokens"],
                                         drift_lock_tokens, drift_strength)
            info["drift"] = {"strength": float(drift_strength), "lock_tokens": int(drift_lock_tokens)}
        if release_vram and previous_latent is not None:
            h3md_vram.unload_idle_models([model], "(between segments)")
        LOG.info("[H3MD Continue] %s (drift)", info)
        return (positive, out, out_model, info["overlap_frames"])


class H3MDSegmentJoin:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "overlap_frames": ("INT", {"default": 0, "min": 0, "max": 3600, "forceInput": True}),
                "audio_crossfade_ms": ("INT", {"default": 0, "min": 0, "max": 2000}),
            },
            "optional": {
                "audio": ("AUDIO",),
                "merged_images": ("IMAGE",),
                "merged_audio": ("AUDIO",),
            },
        }

    RETURN_TYPES = ("IMAGE", "AUDIO", "INT")
    RETURN_NAMES = ("merged_images", "merged_audio", "frame_count")
    FUNCTION = "join"
    CATEGORY = CATEGORY

    def join(self, images, overlap_frames, audio_crossfade_ms=0, audio=None, merged_images=None, merged_audio=None):
        return join_segments(images, audio, overlap_frames, merged_images, merged_audio, audio_crossfade_ms)


# plain "H3MDDirector" node unregistered 2026-10-24 (only H3MD Director + RefMod is offered); the class stays,
# H3MDDirectorRefMod subclasses it.
NODE_CLASS_MAPPINGS = {
    "H3MDSegmentPick": H3MDSegmentPick,
    "H3MDSegmentEncode": H3MDSegmentEncode,
    "H3MDSegmentContinue": H3MDSegmentContinue,
    "H3MDSegmentJoin": H3MDSegmentJoin,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3MDSegmentPick": "H3MD Segment Pick",
    "H3MDSegmentEncode": "H3MD Segment Encode",
    "H3MDSegmentContinue": "H3MD Segment Continue",
    "H3MDSegmentJoin": "H3MD Segment Join",
}
