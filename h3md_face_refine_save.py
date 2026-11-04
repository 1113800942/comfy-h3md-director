"""H3MD Save Video Face Refine And Interp (H3MDSaveVideoFaceRefineInterp).

H3MD Save Video Interp (h3md_stream_save.py, reused unchanged) plus a chunked face refine:

  pass 1  stream the merged clip from the H3MD segment cache, detect faces (ultralytics YOLO, the FaceRefine
          pack's own loader) and, optionally, cuts (PySceneDetect AdaptiveDetector fed frame by frame).
          Only boxes are kept - never the frames.
  solve   whole-clip subject tracking exactly like H3 Face Track + Crop (ComfyUI-H3-FaceRefine 1.1.2):
          subject selection / identity anchor (single frames read lazily from the cache), within-reach rule,
          lost frames, per-shot interpolation + smoothing, paste weights, crop boxes.  Shots marked absent keep
          their original pixels (nothing is dropped, so no 17k+5 padding).
  pass 2  per chunk: crop -> TE-Speed VOSR2 Image (scale 1, one fixed seed for every chunk) -> H3 Face Stitch Back
          (with the chunk's slice of the transform) -> the Save Video Interp RIFE chunk / OOM path -> encoder.
          A frame shared by two chunks (RIFE boundary frame, OOM retry) is refined once and reused.

face_refine off: exactly H3MD Save Video Interp (h3md_stream_save.stream_save).

Runtime dependencies (looked up in ComfyUI's node registry when the node runs, nothing is bundled):
  ComfyUI-H3-FaceRefine >= 1.1.2 (H3FaceTrackCrop's module helpers + H3FaceStitch),
  TE-Speed-VOSR2 (TESpeedVOSR2Image), ComfyUI-VFI (RIFE, only with interpolate on).

-----------------------------------------------------------------------------------------------------------------
Third-party notice.  solve_track() and faces_from_pick() below are ported (adapted to lazy, chunked frame access)
from H3FaceTrackCrop.run() in ComfyUI-H3-FaceRefine 1.1.2 (https://github.com/Carasibana/ComfyUI-H3-FaceRefine,
nodes.py, commit d8521d1), which is under the MIT License:

    MIT License

    Copyright (c) 2026 Carasibana

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
-----------------------------------------------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
import math
import os
import shutil
import sys
import time

import numpy as np
import torch

try:
    from . import h3md_stream_save as S
except ImportError:  # flat import (tests)
    import h3md_stream_save as S

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD FaceRefineSave]"
NODE_ID = "H3MDSaveVideoFaceRefineInterp"

# identity_clip_vision (CLIP_VISION, for identity_model=clip_vision) and face_pick (H3FACEPICK, from
# H3 Load Video + Face Select) are fully handled by save() / solve_track() but NOT shown on the node:
# H3 Load Video + Face Select loads the whole clip into RAM, which a long H3MD video cannot afford.
# Set this to True (and restart ComfyUI) to add both as optional inputs.
EXPOSE_ADVANCED_IDENTITY_INPUTS = False

VOSR2_SCALE = 1                # VOSR2 restores at the crop's own size (the stitch resamples it back)
REFINE_MIN_WEIGHT = 1.0 / 1024  # paste opacity below this changes no 8-bit pixel: the frame is not refined
DETECT_READ = 16               # frames read per cache access in pass 1

# FaceRefine module attributes solve_track() needs (1.1.2 layout)
_FR_NEEDED = ("_load_detector", "_detector_list", "_make_cut_detector", "_segments", "_to_bgr_u8", "_resolve_select",
              "_rank_boxes", "_make_embedder", "_shot_anchors", "_track_continuity", "_anchor_from_track",
              "_build_clip_anchor", "_shots_without_subject", "_holds_crowd", "_track_back", "_typical_height",
              "_within_reach", "_continuity_cost", "_iou", "_interp_gaps_seg", "_smooth_seg", "_affine_crop",
              "_frame_ranges", "_SELECT_MODES", "_IDENT_MODES", "_CUT_MODES")


# ---------------------------------------------------------------------------
# runtime lookups (monkeypatched in tests)
# ---------------------------------------------------------------------------

def _registry():
    import nodes as comfy_nodes  # ComfyUI's global node registry
    return comfy_nodes.NODE_CLASS_MAPPINGS


def _fr_module():
    """The loaded ComfyUI-H3-FaceRefine nodes module (helpers are called from it, not copied)."""
    cls = _registry().get("H3FaceTrackCrop")
    if cls is None:
        raise RuntimeError(f"{_TAG} face_refine needs ComfyUI-H3-FaceRefine (H3 Face Track + Crop) installed")
    mod = sys.modules.get(cls.__module__)
    missing = [n for n in _FR_NEEDED if mod is None or not hasattr(mod, n)]
    if missing:
        raise RuntimeError(f"{_TAG} the loaded ComfyUI-H3-FaceRefine is older than 1.1.2 (missing {missing[:4]}"
                           f"{' ...' if len(missing) > 4 else ''}); update it and restart ComfyUI")
    return mod


def _stitch_class():
    cls = _registry().get("H3FaceStitch")
    if cls is None:
        raise RuntimeError(f"{_TAG} face_refine needs ComfyUI-H3-FaceRefine (H3 Face Stitch Back) installed")
    return cls


def _vosr2_class():
    cls = _registry().get("TESpeedVOSR2Image")
    if cls is None:
        raise RuntimeError(f"{_TAG} face_refine needs TE-Speed-VOSR2 (TE-Speed VOSR2 Image) installed")
    return cls


def _vosr2_original():
    """v0.3: the original ComfyUI-VOSR2 (github.com/ylchen333/ComfyUI-VOSR2) 'VOSR 2.0 Upscale' node, or None."""
    return _registry().get("VOSR2Upscale")


def is_original_vosr2(model) -> bool:
    """A model from the original 'VOSR 2.0 Model Loader' (VOSR2_MODEL), not the TE-Speed one."""
    return type(model).__name__ == "VOSR2Model" or hasattr(model, "denoise_one_step")


def _seedvr2_numz():
    """v0.3.3: the common ComfyUI-SeedVR2_VideoUpscaler (numz) 'SeedVR2 Video Upscaler' node, or None."""
    return _registry().get("SeedVR2VideoUpscaler")


def is_numz_seedvr2(model) -> bool:
    """A DiT from numz's 'SeedVR2 (Down)Load DiT Model' (SEEDVR2_DIT is a config dict), not a TE-Speed MODEL."""
    return isinstance(model, dict)


def _seedvr2_class():
    cls = _registry().get("TESpeedSeedVR2Integrated")
    if cls is None:
        raise RuntimeError(f"{_TAG} face_refine with seedvr2_model needs TE-Speed-SeedVR2 installed")
    return cls


def _seedvr2_defaults(cls):
    """SeedVR2 parameters at the defaults its own node declares (read at run time, nothing hardcoded here)."""
    spec = cls.INPUT_TYPES()
    out = {}
    for sect in ("required", "optional"):
        for k, v in (spec.get(sect) or {}).items():
            if k in ("model", "vae", "images", "max_long_edge", "seed"):
                continue
            opts = v[1] if len(v) > 1 and isinstance(v[1], dict) else {}
            if "default" in opts:
                out[k] = opts["default"]
            elif sect == "required" and isinstance(v[0], (list, tuple)) and v[0]:
                out[k] = v[0][0]
    return out


class _Interrupt:
    """Stand-in for comfy.model_management inside the ported code (only the interrupt check is used)."""

    @staticmethod
    def throw_exception_if_processing_interrupted():
        S.check_interrupt()


class LazyFrames:
    """Read-only IMAGE-like view of the merged clip: frames[i] -> [H,W,3], frames[a:b] -> [b-a,H,W,3] float32,
    read from the segment cache on access (the FaceRefine helpers only ever take single frames)."""

    def __init__(self, src):
        self.src = src
        self.shape = (int(src.total), int(src.h), int(src.w), int(src.c))
        self.dtype = torch.float32

    def __len__(self):
        return self.shape[0]

    def __getitem__(self, key):
        if isinstance(key, slice):
            a, b, step = key.indices(self.shape[0])
            if step != 1:
                raise IndexError("LazyFrames: step slices are not supported")
            return self.src.read(a, max(a, b))
        if isinstance(key, (int, np.integer)):
            i = int(key)
            if i < 0:
                i += self.shape[0]
            if not 0 <= i < self.shape[0]:
                raise IndexError(i)
            return self.src.read(i, i + 1)[0]
        raise IndexError(f"LazyFrames: unsupported index {key!r}")


# ---------------------------------------------------------------------------
# pass 1: boxes (+ cuts) for the whole clip, streamed
# ---------------------------------------------------------------------------

def detect_pass(fr, frames, detector, confidence, cut_detection="none", cut_threshold=3.0, pbar=None,
                get_model=None):
    """Like the detection loop of H3FaceTrackCrop.run(), reading DETECT_READ frames at a time.
    -> (all_boxes, all_confs, cuts, cut_note)"""
    B = len(frames)
    model = get_model() if get_model is not None else fr._load_detector(detector)
    all_boxes, all_confs, cuts = [], [], []
    cut_det = cut_tc = None
    cut_note = ""
    if cut_detection != "none":
        try:
            cut_det, cut_tc = fr._make_cut_detector(cut_threshold)
        except Exception as exc:
            cut_note = f"cut detection unavailable, treating the video as one shot: {exc}"
            print(f"[H3FaceRefine] {cut_note}")
    for a in range(0, B, DETECT_READ):
        S.check_interrupt()
        block = frames[a:min(B, a + DETECT_READ)]
        for k in range(block.shape[0]):
            i = a + k
            bgr = fr._to_bgr_u8(block[k])
            if cut_det is not None:
                for _t in cut_det.process_frame(cut_tc(i, fps=24.0), bgr):
                    cuts.append(int(_t.get_frames()))
            res = model.predict(bgr, conf=confidence, verbose=False)[0]
            if len(res.boxes):
                bx = [[float(v) for v in q] for q in res.boxes.xyxy.tolist()]
                cf = getattr(res.boxes, "conf", None)
                all_boxes.append(bx)
                all_confs.append([float(c) for c in cf.tolist()] if cf is not None else [1.0] * len(bx))
            else:
                all_boxes.append([])
                all_confs.append([])
        del block
        if pbar is not None:
            pbar.update_absolute(min(B, a + DETECT_READ), B)
    return all_boxes, all_confs, cuts, cut_note


class NothingToRefine(ValueError):
    """The track found no frame to refine (no face, or every shot marked absent). save() then writes the video
    unrefined instead of failing, so a finished long run is never lost."""


def faces_from_pick(face_pick, B, W, H):
    """The face_pick branch of H3FaceTrackCrop.run() -> (all_boxes, all_confs, segs_given, forced, absent, note)."""
    if int(face_pick.get("frames", -1)) != B:
        raise ValueError(f"face_pick describes {face_pick.get('frames')} frames but the long video has {B}. "
                         f"The pick has to describe exactly this video.")
    _src = face_pick.get("src_size") or (0, 0)
    if int(_src[0] or 0) and (int(_src[0]) != W or int(_src[1]) != H):
        raise ValueError(f"face_pick describes {_src[0]}x{_src[1]} frames but the long video is {W}x{H}.")
    all_boxes = [[[float(v) for v in q] for q in fr] for fr in face_pick["boxes"]]
    all_confs = [[float(c) for c in fr] for fr in face_pick["confs"]]
    segs_given = [(int(x), int(y)) for x, y in (face_pick.get("segments") or [])]
    forced, absent_frames = {}, set()
    for pk in face_pick.get("picks") or []:
        if pk.get("absent"):
            a2, b2 = int(pk["segment"][0]), int(pk["segment"][1])
            absent_frames.update(range(max(0, a2), min(B, b2)))
        elif int(pk.get("frame", -1)) >= 0:
            _f, _b = int(pk["frame"]), int(pk.get("box", -1))
            if 0 <= _f < B and 0 <= _b < len(all_boxes[_f]):
                forced[_f] = _b
    note = (f"boxes from face_pick (detector {face_pick.get('detector')}, "
            f"confidence {face_pick.get('confidence')}), {len(forced)} shot(s) with a chosen subject")
    return all_boxes, all_confs, segs_given, forced, absent_frames, note


# ---------------------------------------------------------------------------
# solve: H3FaceTrackCrop.run() tracking logic on the whole clip (ported, see the notice above)
# ---------------------------------------------------------------------------

def solve_track(fr, images, all_boxes, all_confs, cuts, detector, confidence, crop_factor, canvas_width,
                canvas_height, canvas_mode, smooth_window, size_smooth_window, smooth_method, size_mode,
                select="largest_face", fallback_detector="none", fallback_head_frac=0.5, identity_reference=None,
                identity_threshold=0.28, identity_track=True, select_index=0, identity_model="insightface",
                identity_clip_vision=None, cut_detection="none", cut_threshold=3.0, absent_shots="off",
                X=0, Y=0, frame_index=0, face_pick=None, segs_given=None, forced=None, absent_frames=None,
                cut_note="", pick_note="", get_model=None):
    """images: LazyFrames (or an IMAGE tensor in tests); all_boxes / all_confs / cuts from detect_pass() or
    faces_from_pick().  -> dict(transform=<H3FACEXFORM for EVERY source frame>, report=str, ...).
    Differences to H3FaceTrackCrop.run(): no crops / preview are built, and absent frames are not dropped
    (transform covers all frames; 'absent' marks the ones pass 2 leaves untouched)."""
    _model_box = []

    def _get_model():
        if not _model_box:
            _model_box.append(get_model() if get_model is not None else fr._load_detector(detector))
        return _model_box[0]

    B, H, W, _ = images.shape
    cx = np.zeros(B); cy = np.zeros(B); sz = np.zeros(B); fw = np.zeros(B)
    valid = np.zeros(B, dtype=bool)
    via_body = np.zeros(B, dtype=bool)
    _mm = _Interrupt
    forced = dict(forced or {})
    absent_frames = set(absent_frames or ())

    segs = segs_given if segs_given else fr._segments(B, cuts)

    max_faces = max((len(b) for b in all_boxes), default=0)
    if max_faces == 0:
        raise ValueError(
            "No face detected in any frame. Lower `confidence`, or this video has no "
            "usable face and should be skipped."
        )

    # ---- which face is the subject ---------------------------------------------
    # select ranks the boxes within a frame; select_index takes one out
    # of that ranking. The pick happens ONCE, on the first frame that actually
    # contains the requested index, and continuity carries the subject from there: a
    # rank is a per-frame property, so re-ranking every frame would hop between people
    # the moment two of them cross or change size.
    requested_index = int(select_index)
    select_index = max(0, min(requested_index, max_faces - 1))
    first_face = next(i for i in range(B) if all_boxes[i])
    select = fr._resolve_select(select)
    if select == "closest_to_xy":
        # The frame the user named is where the measurement belongs. If it holds no
        # face, the search moves forward from there rather than back to the start,
        # so the lock stays as close to the named moment as the detections allow.
        _want = min(max(0, int(frame_index)), B - 1)
        index_lock = next((i for i in range(_want, B)
                           if len(all_boxes[i]) > select_index),
                          next(i for i in range(B)
                               if len(all_boxes[i]) > select_index))
    else:
        index_lock = next(i for i in range(B) if len(all_boxes[i]) > select_index)
    selection_default = (select == "largest_face" and select_index == 0)
    # Named in the report so a surprising pick is traceable to what was asked for,
    # including the frame the measurement actually landed on.
    _sel_desc = (f"select={select} nearest X={int(X)} Y={int(Y)} measured on frame "
                 f"{index_lock} (asked for {int(frame_index)}) index={select_index}"
                 if select == "closest_to_xy"
                 else f"select={select} index={select_index}")
    # A named subject outranks the automatic "the biggest face is the subject"
    # guess, so the identity anchor is built from THAT pick instead. A face_pick
    # names one per shot, and the tracker's own select widgets are unused while it
    # is connected, so they cannot be what decides this.
    selection_leads = identity_reference is None and (
        bool(forced) or not selection_default)

    # ---- identity anchor -------------------------------------------------------
    # Without one, "largest" has no idea WHO it is following: it takes the biggest box
    # each frame, so in a crowd it hops subject whenever the framing changes. Observed
    # in testing: a single clip switched subject 4 times.
    ref_emb, embedder = None, None
    ref_failed = False
    pick_warn = ""
    ident_note, ident_scores = "", []
    ident_threshold = float(identity_threshold)
    n_ident, n_cont, n_conflict = 0, 0, 0
    # max_faces, not frame 0: a clip that opens on one face - or on none, which a
    # wide shot often does - and fills up later still needs an anchor built.
    multi = max_faces > 1

    if identity_track and (multi or identity_reference is not None):
        try:
            embedder = fr._make_embedder(identity_model, identity_clip_vision)
            if ident_threshold <= 0.0:
                ident_threshold = float(embedder.default_threshold)
            if identity_reference is not None:
                # _get_model() would load the detector file just to hand it to a
                # backend that ignores it - and with a face_pick wired there is no
                # other reason to load it at all, so a stale detector value would
                # fail a run that never needed it.
                _det = (_get_model()
                        if getattr(embedder, "needs_detector", True) else None)
                ref_emb = embedder.embed_reference(identity_reference[:1],
                                                   _det, confidence)
                if ref_emb is not None:
                    print("[H3FaceRefine] identity anchor from the supplied reference")
                else:
                    # Only THIS backend can say whether the reference is usable -
                    # insightface reads it with SCRFD, the crop backends with the
                    # tracker's detector - so a second opinion from anything else
                    # would be answering a different question. Say which one failed,
                    # because the fix is usually to try another backend.
                    ref_failed = True
            if ref_emb is None and selection_leads:
                # Anchor on the SELECTED subject, followed by continuity. Falling back
                # to the clip anchor here would re-anchor on the dominant face, i.e.
                # exactly the person select_index was used to avoid, so a failure here
                # leaves ref_emb None and the track runs on continuity alone.
                if forced:
                    _keep, _odd = fr._shot_anchors(embedder, images, all_boxes, segs,
                                                forced, B, ident_threshold)
                    if _keep:
                        ref_emb = (embedder.merge(_keep) if len(_keep) > 1
                                   else _keep[0])
                        used = len(_keep)
                    if _odd:
                        pick_warn = (
                            f"\n!! shot(s) {_odd} hold a different face from the rest, "
                            f"so they were left out of the identity anchor.")
                else:
                    seed = fr._rank_boxes(all_boxes[index_lock], all_confs[index_lock],
                                       W, H, select, X, Y)[select_index]
                    _tr = fr._track_continuity(all_boxes, index_lock, seed)
                    _sb = all_boxes[index_lock][seed]
                    ref_emb, used = fr._anchor_from_track(
                        embedder, images, all_boxes, _tr,
                        min_face=min(32.0, 0.9 * (_sb[3] - _sb[1])))
                print(f"[H3FaceRefine] identity anchor built from the selected subject "
                      f"({used} unambiguous frames)" if ref_emb is not None else
                      "[H3FaceRefine] no clean frames to anchor the selected subject - "
                      "tracking by continuity alone")
            elif ref_emb is None:
                if ref_failed:
                    print("[H3FaceRefine] !! no face found in identity_reference "
                          f"by {embedder.name}")
                ref_emb, used = fr._build_clip_anchor(embedder, images, all_boxes)
                if ref_emb is not None:
                    print(f"[H3FaceRefine] identity anchor built from the video itself "
                          f"({used} unambiguous frames)")
        except Exception as exc:
            # Identity is an assist, not a requirement - a missing backend must not
            # kill a run that continuity can still track. Surfaced in the report as
            # well as stdout, because a silent downgrade is the thing that wastes an
            # evening on a crowd scene.
            embedder, ref_emb = None, None
            ident_note = f"{identity_model} unavailable, tracking by continuity: {exc}"
            print(f"[H3FaceRefine] identity matching unavailable ({exc})")

    # Waiting for a frame that contains select_index only makes sense when the RANKING
    # is what picks the subject. A usable identity anchor overrules the ranking at
    # lock-on, so deferring there would discard leading frames identity resolves
    # perfectly - frames where the subject is often alone, i.e. the most reliable ones
    # in the clip.
    # Shots the reference person is not in. Marked absent, so those frames keep
    # their original pixels and drop out of the batch entirely.
    absent_note = ""
    if absent_shots == "by_identity" and ref_emb is not None and embedder is not None:
        try:
            _bad, _scores = fr._shots_without_subject(
                embedder, images, all_boxes, segs, ref_emb, ident_threshold)
            for k in _bad:
                absent_frames.update(range(segs[k][0], segs[k][1]))
            absent_note = (
                ("no subject in shot(s) " + ", ".join(
                    f"{k + 1} (best {_scores.get(k, -1):.3f})" for k in _bad))
                if _bad else "every shot contains the subject")
        except Exception as exc:
            absent_note = f"absent-shot detection failed: {exc}"
            print(f"[H3FaceRefine] {absent_note}")
    elif absent_shots == "by_identity":
        absent_note = "absent_shots=by_identity needs a usable identity anchor; ignored"

    ranking_picks = selection_leads or ref_emb is None
    lock_frame = index_lock if ranking_picks else first_face

    last = None   # (cx, cy, size) of the subject on the previous resolved frame
    last_i = -1   # the frame `last` was resolved on
    heights = []  # the subject's face heights on frames resolved since the last cut
    lost = []     # frames holding faces, none of them within reach of the subject
    crowd = [False] * B
    for _a, _b in segs:
        _c = fr._holds_crowd(all_boxes, _a, _b)
        for _j in range(max(0, _a), min(B, _b)):
            crowd[_j] = _c
    seg_starts = {int(x) for x, _ in segs}

    # Where each frame's shot locks on: per shot with a face_pick, otherwise the
    # single clip-wide lock as before.
    if forced:
        lock_of = [0] * B
        # Frames before a shot's lock, resolved backwards from it. Only where the
        # subject was named, so this never guesses on a rule-based lock.
        backfill = {}
        for _pk in (face_pick.get("picks") or []):
            _a, _b = int(_pk["segment"][0]), int(_pk["segment"][1])
            _lf = int(_pk.get("frame", -1))
            for _i in range(max(0, _a), min(B, _b)):
                lock_of[_i] = _lf if _lf >= 0 else _a
            if _lf > _a and not _pk.get("absent") and all_boxes[_lf]:
                _bi = int(_pk.get("box", -1))
                if 0 <= _bi < len(all_boxes[_lf]):
                    backfill.update(fr._track_back(all_boxes, _lf, _bi, max(0, _a)))
    else:
        lock_of, backfill = None, {}
        if select == "closest_to_xy" and all_boxes[index_lock]:
            # A frame the user named is as much a naming of the subject as a
            # face_pick is, so the frames before it are that same person walking
            # backwards - the one rule-based lock where guessing backwards is not
            # a guess. Without this, moving frame_index later would quietly drop
            # everything before it out of the composite.
            _rk = fr._rank_boxes(all_boxes[index_lock], all_confs[index_lock],
                              W, H, select, X, Y)
            if _rk:
                _bi = _rk[min(select_index, len(_rk) - 1)]
                _a = max((a for a, b in segs if a <= index_lock < b), default=0)
                backfill = fr._track_back(all_boxes, index_lock, _bi, _a)

    for i in range(B):
        _mm.throw_exception_if_processing_interrupted()
        if i in seg_starts:
            # A cut ends continuity - "nearest box to where the subject was" means
            # nothing once the camera has changed shot.
            last = None
            heights = []
        if i in absent_frames:
            continue
        boxes = all_boxes[i]
        if not boxes:
            continue
        if (last is None and i not in backfill
                and i < (lock_of[i] if lock_of is not None else lock_frame)):
            # Too few faces here to honour the selection. Leaving these frames
            # unresolved hands them to the same interpolation that covers detection
            # dropouts, which beats locking onto whoever happens to be alone.
            continue

        b = None
        if i in forced and 0 <= forced[i] < len(boxes):
            # Chosen upstream for this shot; take it verbatim.
            b = boxes[forced[i]]
            n_cont += 1
        elif i in backfill and 0 <= backfill[i] < len(boxes):
            # Before this shot's lock, resolved by continuity run backwards from it.
            b = boxes[backfill[i]]
            n_cont += 1
        elif last is None and len(boxes) == 1:
            b = boxes[0]
            n_cont += 1
        elif last is None:
            # first resolved frame: identity if we have it, else the ranking rule
            if ref_emb is not None and not selection_leads:
                cands = embedder.embed(images[i:i + 1], boxes)
                k, score = embedder.best_match(cands, ref_emb)
                # Deliberately unthresholded, unlike the mid-shot path below. A
                # small, poorly detailed face embeds weakly, and that face is the
                # subject this node exists to fix; there is also no continuity yet
                # to fall back on, so the best available match beats handing the
                # choice to the ranking rule. Choose the face manually when it is
                # the wrong one.
                if k is not None:
                    ident_scores.append(score)
                    b = cands[k][0]
                    n_ident += 1
            if b is None:
                # Clamped: when identity was in charge the lock frame is only
                # guaranteed to hold a face, not to hold select_index of them, and
                # insightface can come back empty on a frame the face detector liked.
                ranked = fr._rank_boxes(boxes, all_confs[i], W, H, select, X, Y)
                b = boxes[ranked[min(select_index, len(ranked) - 1)]]
                n_cont += 1
        else:
            # Only faces within reach of where the subject was can continue it.
            if crowd[i]:
                _h = fr._typical_height(heights)
                reach = [q for q in boxes if fr._within_reach(q, last, i - last_i, _h)]
            else:
                reach = boxes
            if not reach:
                # The subject's face is not detected here, or is too far from where it
                # was to be the same person. The frame stays unresolved and fades out
                # of the composite; the subject is picked up again when a face
                # reappears within reach of where it was lost.
                lost.append(i)
                continue
            elif len(reach) == 1:
                b = reach[0]
                n_cont += 1
            else:
                # Continuity first: the nearest box to where the subject was, penalised
                # for size change. Cheap and correct while people stay separated.
                ranked = sorted(reach, key=lambda q: fr._continuity_cost(q, last))
                best, second = ranked[0], ranked[1]
                c0, c1 = fr._continuity_cost(best, last), fr._continuity_cost(second, last)

                # AMBIGUOUS when two candidates are similarly plausible, or their boxes
                # overlap - exactly when continuity alone picks the wrong person. Only
                # then is the embedding worth computing.
                conflict = (c1 < c0 * 2.0) or (fr._iou(best, second) > 0.2)

                if conflict and ref_emb is not None:
                    n_conflict += 1
                    near = ([q for q in reach if fr._continuity_cost(q, last) < c0 * 3.0]
                            or reach)
                    cands = [c for c in embedder.embed(images[i:i + 1], near)
                             if any(fr._iou(c[0], q) > 0.3 for q in near)]
                    k, score = embedder.best_match(cands, ref_emb)
                    if k is not None:
                        ident_scores.append(score)
                    if k is not None and score >= ident_threshold:
                        b = cands[k][0]
                        n_ident += 1
                if b is None:
                    # No conflict, or the embedding was not confident enough. Embeddings
                    # degrade on profiles and occlusion - precisely where the subject is
                    # hardest to hold - so continuity is the safer default there.
                    b = best
                    n_cont += 1

        last = ((b[0]+b[2])/2.0, (b[1]+b[3])/2.0, b[3]-b[1])
        last_i = i
        heights.append(last[2])
        cx[i] = (b[0] + b[2]) / 2.0
        cy[i] = (b[1] + b[3]) / 2.0
        sz[i] = b[3] - b[1]          # face HEIGHT: more stable than width as the head turns
        fw[i] = b[2] - b[0]          # face WIDTH: needed for the FaceDetailer-style paste mask
        valid[i] = True

    found = int(valid.sum())
    if found == 0:
        if absent_frames and len(absent_frames) >= B:
            raise NothingToRefine(
                "Every shot is marked as not containing the subject, so there is "
                "nothing to refine (" + (absent_note or "Pick faces marks every shot absent") + "). "
                "Check the identity reference / Director ID Ref, set absent_shots off, or unmark a shot in Pick faces."
            )
        raise NothingToRefine(
            "No face detected in any frame. Lower `confidence`, or this video has no "
            "usable face and should be skipped."
        )

    # Body fallback for frames the face detector missed. Interpolated size feeds the
    # head-position estimate, so size comes from frames where a face WAS measured while
    # position comes from the body actually visible in this frame.
    #
    # Keyed on "no face was detected", NOT on "no subject was resolved": the frames the
    # lock deferral holds back DID have faces, and the body model would re-centre them
    # on the largest body in shot - usually the very person select_index was used to
    # avoid - then mark them via_body, which hides them from both the interpolation and
    # the dropout warning while smoothing that error into the frames after the lock.
    no_face = np.array([not b for b in all_boxes], dtype=bool)
    sz_seed = fr._interp_gaps_seg(sz, valid, segs)
    cx_seed = fr._interp_gaps_seg(cx, valid, segs)
    cy_seed = fr._interp_gaps_seg(cy, valid, segs)
    if fallback_detector != "none" and no_face.any():
        try:
            bmodel = fr._load_detector(fallback_detector)
            for i in np.nonzero(no_face)[0]:
                res = bmodel.predict(fr._to_bgr_u8(images[i]), conf=confidence, verbose=False)[0]
                if not len(res.boxes):
                    continue
                bb = res.boxes.xyxy.tolist()
                cls = (res.boxes.cls.tolist() if getattr(res.boxes, "cls", None) is not None
                       else [0] * len(bb))
                people = [q for q, cc in zip(bb, cls) if int(cc) == 0] or bb
                # The body whose head lands nearest where the subject is expected. In
                # a group the largest body is often someone else.
                _hd = fallback_head_frac * max(sz_seed[i], 8.0)
                p = min(people, key=lambda q: ((q[0] + q[2]) / 2.0 - cx_seed[i]) ** 2
                        + (q[1] + _hd - cy_seed[i]) ** 2)
                cx[i] = (p[0] + p[2]) / 2.0
                cy[i] = p[1] + fallback_head_frac * max(sz_seed[i], 8.0)
                sz[i] = sz_seed[i]
                via_body[i] = True
        except Exception as exc:  # never let the fallback kill the run
            print(f"[H3FaceRefine] body fallback '{fallback_detector}' failed: {exc}")

    known = valid | via_body
    raw_cx = fr._interp_gaps_seg(cx, known, segs)
    raw_cy = fr._interp_gaps_seg(cy, known, segs)
    raw_sz = fr._interp_gaps_seg(sz, valid, segs)  # size ALWAYS from real face measurements
    raw_fw = fr._interp_gaps_seg(fw, valid, segs)
    sm_fw = fr._smooth_seg(raw_fw, size_smooth_window, smooth_method, segs)
    cx = fr._smooth_seg(raw_cx, smooth_window, smooth_method, segs)
    cy = fr._smooth_seg(raw_cy, smooth_window, smooth_method, segs)
    sz = fr._smooth_seg(raw_sz, size_smooth_window, smooth_method, segs)
    if size_mode == "max_of_clip":
        # Clip-wide on purpose, cuts or not: H3 generates one width/height for the
        # batch, and a single IMAGE batch cannot carry a canvas per shot.
        sz[:] = sz.max()

    # Shots the subject is not in. The section stays in the batch - the clip is one
    # H3 generation and cutting a hole in the middle would splice two unrelated
    # shots together - but there is no face to follow, so the crop is a centred box
    # of the frame rather than a position interpolated from other shots. Set after
    # smoothing so it lands exactly centred; the segment is its own smoothing
    # window, so neighbouring shots are not dragged toward the centre.
    absent = np.zeros(B, dtype=bool)
    if absent_frames:
        absent[np.array(sorted(absent_frames), dtype=int)] = True
        live = (~absent) & valid
        _sz = float(np.median(sz[live])) if live.any() else float(min(W, H)) / 3.0
        _fw = float(np.median(sm_fw[live])) if live.any() else _sz
        cx[absent] = W / 2.0
        cy[absent] = H / 2.0
        sz[absent] = _sz
        sm_fw[absent] = _fw

    # frame-to-frame movement, before vs after: the number that corresponds to
    # visible jitter. Residual is what still moves after smoothing.
    def _jit(a):
        return float(np.abs(np.diff(a)).mean()) if len(a) > 1 else 0.0

    jit_before = (_jit(raw_cx) + _jit(raw_cy)) / 2.0
    jit_after = (_jit(cx) + _jit(cy)) / 2.0
    sz_before, sz_after = _jit(raw_sz), _jit(sz)

    # Size the canvas from the clip itself so no frame is ever downscaled. The largest
    # crop is (largest smoothed face height) * crop_factor, clamped to the frame; matching
    # the canvas to it keeps magnification >= 1.0x everywhere.
    if canvas_mode != "manual":
        need = float(min(sz.max() * crop_factor, H))
        snapped = int(np.ceil(need / 32.0) * 32)
        if canvas_mode == "auto_capped_768":
            snapped = min(snapped, 768)
        # Floor at 512. The crop is bounded by the source frame, so on a small
        # face in a low-resolution clip the canvas would otherwise track the crop
        # down to a couple of hundred px and H3 would be handed the same small
        # face it renders badly in the first place.
        snapped = max(512, min(snapped, 1344))
        if snapped != canvas_height:
            print(f"[H3FaceRefine] canvas_mode={canvas_mode}: "
                  f"{canvas_width}x{canvas_height} -> {snapped}x{snapped} "
                  f"(largest crop {need:.0f}px)")
        canvas_width = canvas_height = snapped

    # ---- crop boxes for every frame (H3FaceTrackCrop.run box maths; no keep/drop, no crops) ----------------
    aspect = canvas_width / float(canvas_height)
    boxes = []
    for i in range(B):
        bh = sz[i] * crop_factor
        bw = bh * aspect
        if bw > W:
            bw, bh = float(W), float(W) / aspect
        if bh > H:
            bh, bw = float(H), float(H) * aspect
        x = min(max(cx[i] - bw / 2.0, 0.0), max(0.0, W - bw))
        y = min(max(cy[i] - bh / 2.0, 0.0), max(0.0, H - bh))
        boxes.append((float(x), float(y), float(bw), float(bh)))

    weights = fr._smooth_seg(valid.astype(np.float64), max(9, smooth_window // 2), "gaussian", segs)
    weights = np.clip(weights, 0.0, 1.0)

    runs, cur = [], 0
    for v in known:
        if v:
            if cur:
                runs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        runs.append(cur)
    longest_gap = max(runs) if runs else 0

    transform = {
        "boxes": boxes,
        "canvas": (int(canvas_width), int(canvas_height)),
        "src_size": (int(W), int(H)),
        "frames": int(B),
        "source": list(range(B)),
        "source_frames": int(B),
        "segments": [(int(a), int(b)) for a, b in segs],
        "absent": [bool(v) for v in absent],
        "weights": [float(v) for v in weights],
        "detected": [bool(v) for v in valid],
        "face_rect": [
            (
                float(canvas_width) * 0.5 - 0.5 * float(sm_fw[i]) / max(b[2], 1e-6) * canvas_width,
                float(canvas_height) * 0.5 - 0.5 * float(sz[i]) / max(b[3], 1e-6) * canvas_height,
                float(sm_fw[i]) / max(b[2], 1e-6) * canvas_width,
                float(sz[i]) / max(b[3], 1e-6) * canvas_height,
            )
            for i, b in enumerate(boxes)
        ],
        "crop_factor": float(crop_factor),
    }

    # ---- report (H3FaceTrackCrop wording where it applies) ----------------------------------------------------
    live = [i for i in range(B) if not absent[i]] or list(range(B))
    mags = [canvas_height / float(boxes[i][3]) for i in live]
    shortest_shot = min((b - a for a, b in segs), default=B)
    lines = []
    if forced:
        _lk = sorted(forced)
        lockdesc = (f"locked on per shot at {', '.join(str(v) for v in _lk[:6])}{' ...' if len(_lk) > 6 else ''}"
                    f" of {B}")
    else:
        lockdesc = f"locked on at frame {lock_frame} of {B}"
    prelock = 0 if forced else sum(1 for i in range(lock_frame) if all_boxes[i])
    lines.append(f"subject: {'from face_pick, one per shot' if forced else _sel_desc}"
                 f"{'' if (ranking_picks or forced) else ' (ignored - identity_reference decides)'}, {lockdesc}"
                 f"{f', {prelock} earlier frames interpolated' if prelock else ''}")
    lines.append(f"faces: max {max_faces} in one frame")
    if pick_note:
        lines.append(f"pick: {pick_note}")
    if cut_note:
        lines.append(f"cuts: {cut_note}")
    elif cut_detection != "none" or len(segs) > 1:
        _cutsrc = "carried on face_pick" if segs_given else f"at threshold {cut_threshold:.1f}"
        lines.append(f"cuts: {len(segs) - 1} found {_cutsrc} -> {len(segs)} shot(s), shortest {shortest_shot} "
                     f"frames; smoothing and interpolation run per shot"
                     + (f"; cut frames {', '.join(str(a) for a, _b in segs[1:])}" if len(segs) > 1 else ""))
    if absent_note:
        lines.append(f"absent: {absent_note}")
    n_abs = int(absent.sum())
    lines.append(f"absent frames: {n_abs}"
                 + (f" ({fr._frame_ranges(np.nonzero(absent)[0].tolist())}) - original pixels kept" if n_abs else ""))
    lines.append(f"tracking: {n_cont} by continuity, {n_conflict} ambiguous ({n_ident} resolved by face identity)")
    lines.append(f"lost: {len(lost)} frame(s) held faces but none within reach of the subject"
                 + (f" ({fr._frame_ranges(lost)}) - left unresolved" if lost else ""))
    if ident_note:
        lines.append(f"identity: {ident_note}")
    elif embedder is None:
        lines.append("identity: not used (single face, or identity_track off)")
    else:
        if ident_scores:
            scores = (f"  scores n={len(ident_scores)} min={min(ident_scores):.3f} "
                      f"mean={sum(ident_scores) / len(ident_scores):.3f} max={max(ident_scores):.3f}")
        elif ref_emb is None:
            scores = "  (no anchor could be built - tracked by continuity alone)"
        else:
            scores = "  (anchor built, never consulted - continuity was unambiguous)"
        lines.append(f"identity: {embedder.name}  threshold={ident_threshold:.3f}"
                     f"{' (model default)' if identity_threshold <= 0.0 else ''}"
                     f"{'  [' + embedder.note + ']' if getattr(embedder, 'note', '') else ''}{scores}")
    lines.append(f"frames={B}  face={found} ({found / B * 100:.0f}%)  body-fallback={int(via_body.sum())}  "
                 f"interpolated={B - int(known.sum())}")
    lines.append(f"face height  min={sz.min():.0f}px  mean={sz.mean():.0f}px  max={sz.max():.0f}px")
    lines.append(f"magnification into {canvas_width}x{canvas_height}: min={min(mags):.2f}x  "
                 f"mean={sum(mags) / len(mags):.2f}x  max={max(mags):.2f}x")
    lines.append(f"jitter ({smooth_method}) centre {jit_before:.2f} -> {jit_after:.2f} px/frame   "
                 f"size {sz_before:.2f} -> {sz_after:.2f} px/frame")
    lines.append(f"dropout runs: {len(runs)}  longest={longest_gap} frames ({longest_gap / 24.0:.1f}s at 24fps)")
    warns = []
    if identity_reference is not None and not identity_track:
        warns.append("identity_reference is connected but identity_track is off, so it was not read")
    if ref_failed:
        warns.append(f"{embedder.name} found no face in identity_reference; the anchor was built from the "
                     f"video's dominant face instead")
    if pick_warn:
        warns.append(pick_warn.strip().lstrip("!").strip())
    if requested_index != select_index and not forced:
        warns.append(f"select_index={requested_index} is out of range (max {max_faces} face(s)); used {select_index}")
    if len(segs) > 1 and shortest_shot < max(int(smooth_window), int(size_smooth_window)):
        warns.append(f"shortest shot is {shortest_shot} frames, under the smoothing window")
    n_down = sum(1 for m in mags if m < 1.0)
    if n_down:
        warns.append(f"{n_down}/{len(mags)} frames have magnification < 1.0x (crop downscaled into the canvas)")
    report = "\n".join(lines + [f"!! {w}" for w in warns])
    return {"transform": transform, "report": report, "lost": list(lost), "absent": absent, "valid": valid,
            "segments": segs, "canvas": (int(canvas_width), int(canvas_height))}


# ---------------------------------------------------------------------------
# pass 2: refine chunk by chunk
# ---------------------------------------------------------------------------

def paste_weights(transform, undetected_frames, blend):
    """Per-frame opacity H3FaceStitch will use (blend x its weight for undetected_frames)."""
    n = len(transform["boxes"])
    if undetected_frames == "composite_anyway":
        w = [1.0] * n
    elif undetected_frames == "skip":
        w = [1.0 if d else 0.0 for d in transform["detected"]]
    else:
        w = list(transform["weights"])
    return [float(blend) * float(v) for v in w]


def slice_transform(transform, idx, base_start, base_len):
    """The transform for one H3FaceStitch call: crops of source frames `idx` (absolute, ascending), pasted into a
    base batch that starts at source frame base_start and holds base_len frames."""
    sub = dict(transform)
    for key in ("boxes", "weights", "detected", "face_rect", "absent"):
        if transform.get(key) is not None:
            sub[key] = [transform[key][i] for i in idx]
    sub["source"] = [int(i) - int(base_start) for i in idx]
    sub["frames"] = len(idx)
    sub["source_frames"] = int(base_len)
    sub["segments"] = [(0, len(idx))]
    return sub


def _unwrap_image(res):
    if isinstance(res, dict):
        res = res.get("result", res)
    if hasattr(res, "result") and not isinstance(res, (tuple, list, torch.Tensor)):
        res = res.result
    if isinstance(res, (tuple, list)):
        res = res[0]
    if not isinstance(res, torch.Tensor):
        raise RuntimeError(f"{_TAG} TE-Speed VOSR2 Image returned {type(res).__name__}, expected an IMAGE")
    return res


class FaceRefiner:
    """crop -> VOSR2 -> stitch for any range of source frames, driven by the whole-clip transform."""

    def __init__(self, fr, transform, vosr2_model, vosr2_settings, seed, stitch_kw, seedvr2_model=None,
                 seedvr2_vae=None):
        self.fr = fr
        self.t = transform
        self.cw, self.ch = (int(v) for v in transform["canvas"])
        self.model = vosr2_model
        self.settings = vosr2_settings
        self.seed = int(seed)
        self.stitch_kw = dict(stitch_kw)
        w = paste_weights(transform, self.stitch_kw.get("undetected_frames", "fade_out"),
                          self.stitch_kw.get("blend", 1.0))
        absent = transform.get("absent") or [False] * len(w)
        self.todo = [(not absent[i]) and w[i] > REFINE_MIN_WEIGHT for i in range(len(w))]
        self.seedvr2_vae = seedvr2_vae
        self.numz = None
        if seedvr2_model is not None and is_numz_seedvr2(seedvr2_model):   # v0.3.3: plain SeedVR2 (numz)
            self.model = seedvr2_model
            self.numz = _seedvr2_numz()
            if self.numz is None:
                raise RuntimeError(f"{_TAG} the SeedVR2 model comes from ComfyUI-SeedVR2_VideoUpscaler, but its "
                                   "'SeedVR2 Video Upscaler' node is not loaded")
            self.vosr2 = None
        elif seedvr2_model is not None:
            self.model = seedvr2_model
            sv_cls = _seedvr2_class()
            sv = sv_cls()
            self.sv_fn = getattr(sv, sv_cls.FUNCTION)
            self.sv_kw = _seedvr2_defaults(sv_cls)
            self.sv_kw["attention_backend"] = "comfy_auto"  # TE-Speed sage path asserts on fp32 with no fallback
            self.vosr2 = None
        elif is_original_vosr2(vosr2_model):   # v0.3: original ComfyUI-VOSR2 node (no TE-Speed build needed)
            orig = _vosr2_original()
            if orig is None:
                raise RuntimeError(f"{_TAG} the VOSR2 model comes from ComfyUI-VOSR2, but its 'VOSR 2.0 Upscale' node "
                                   "is not loaded")
            self.vosr2 = None
            self.orig = orig
        else:
            self.vosr2 = _vosr2_class()()
        self.stitch = _stitch_class()()
        self.vbatch = None           # learned VOSR2 sub-batch after an OOM
        self.count = {}              # source frame -> times refined (must stay 1)
        self.events = []
        self.t_vosr2 = self.t_stitch = 0.0

    def _upscale(self, crops):
        outs, k = [], 0
        size = self.vbatch or crops.shape[0]
        extra = {"settings": self.settings} if self.settings is not None else {}
        while k < crops.shape[0]:
            S.check_interrupt()
            part = crops[k:k + size]
            try:
                if getattr(self, "orig", None) is not None:
                    # original node: scale 1 (the crop size), its own tiling only when a crop is above the 512 px it
                    # was trained at; wavelet colour alignment as the TE-Speed default
                    tile = 512 if max(self.cw, self.ch) > 512 else 0
                    r = _unwrap_image(self.orig.execute(self.model, part, 1, self.seed, "wavelet", tile, 32, 0, 32))
                elif self.numz is not None:
                    # plain SeedVR2: shortest edge = the crop's (scale 1), its own defaults otherwise
                    r = _unwrap_image(self.numz.execute(image=part, dit=self.model, vae=self.seedvr2_vae,
                                                        seed=self.seed, resolution=min(self.cw, self.ch),
                                                        max_resolution=0, batch_size=5, uniform_batch_size=False,
                                                        temporal_overlap=0, prepend_frames=0,
                                                        color_correction="wavelet", input_noise_scale=0.0,
                                                        latent_noise_scale=0.0, offload_device="cpu",
                                                        enable_debug=False))
                elif self.vosr2 is None:
                    r = _unwrap_image(self.sv_fn(model=self.model, vae=self.seedvr2_vae, images=part,
                                                 max_long_edge=max(self.cw, self.ch), seed=self.seed, **self.sv_kw))
                else:
                    r = _unwrap_image(self.vosr2.upscale(model=self.model, images=part, scale=VOSR2_SCALE,
                                                         seed=self.seed, **extra))
            except BaseException as exc:
                if not S.is_oom(exc) or size <= 1:
                    raise
                del part
                S.empty_cache()
                size = max(1, size // 2)
                self.vbatch = size
                self.events.append(("vosr2_batch", size))
                LOG.warning("%s VOSR2 out of memory; retrying with %d crops per call", _TAG, size)
                continue
            r = r[..., :3].to("cpu", torch.float32)
            if tuple(r.shape[1:3]) != (self.ch, self.cw):
                r = torch.nn.functional.interpolate(r.movedim(-1, 1), size=(self.ch, self.cw), mode="bilinear",
                                                    align_corners=False).movedim(1, -1)
            outs.append(r)
            k += part.shape[0]
        return outs[0] if len(outs) == 1 else torch.cat(outs, 0)

    def refine(self, base, start):
        """base: source frames [start, start + n) float32 -> the same frames with the refined face pasted."""
        n = int(base.shape[0])
        idx = [i for i in range(start, start + n) if self.todo[i]]
        if not idx:
            return base
        crops = torch.cat([self.fr._affine_crop(base[i - start:i - start + 1], self.t["boxes"][i], self.cw, self.ch)
                           .to(torch.float32) for i in idx], 0)
        t0 = time.time()
        refined = self._upscale(crops)
        del crops
        t1 = time.time()
        out = self.stitch.run(base_images=base, refined_crops=refined, transform=slice_transform(self.t, idx, start, n),
                              **self.stitch_kw)[0]
        self.t_vosr2 += t1 - t0
        self.t_stitch += time.time() - t1
        for i in idx:
            self.count[i] = self.count.get(i, 0) + 1
        return out


class RefinedSource:
    """SegmentSource stand-in for the Save Video Interp chunk path: read(a, b) returns refined frames.
    Frames already refined (the RIFE boundary frame, an OOM retry of the same chunk) come from a cache that
    retain() trims after every written chunk, so no frame is refined twice."""

    def __init__(self, src, refiner):
        self.src = src
        self.refiner = refiner
        self.h, self.w, self.c, self.total = src.h, src.w, src.c, src.total
        self.fps, self.indices, self.dir = src.fps, src.indices, src.dir
        self.cache = {}

    def read(self, a, b):
        a, b = max(0, int(a)), min(self.total, int(b))
        out = torch.empty((max(0, b - a), self.h, self.w, self.c), dtype=torch.float32)
        i = a
        while i < b:
            if i in self.cache:
                out[i - a] = self.cache[i]
                i += 1
                continue
            j = i
            while j < b and j not in self.cache:
                j += 1
            base = self.src.read(i, j)
            out[i - a:j - a] = self.refiner.refine(base, i)
            del base
            i = j
        self.cache.update({k: out[k - a] for k in range(a, b)})   # views, until retain()
        return out

    def retain(self, keep_from):
        """Keep (as small copies) only the cached frames >= keep_from; drops the views of finished chunks."""
        self.cache = {k: v.clone() for k, v in self.cache.items() if k >= keep_from}

    def audio(self):
        return self.src.audio()

    def close(self):
        self.cache = {}
        self.src.close()


def refined_stream_save(info, path, refiner, interpolate=False, target_fps=S.SOURCE_FPS, scale=1.0,
                        model_name="flownet.pkl", batch_size=8, use_fp16=True, crf=19, metadata=None,
                        interpolator=None, pbar=None, chunk_frames=None, fmt="video/h264-mp4", pix_fmt="yuv420p",
                        loop_count=0, trim_to_audio=False, src=None, drift_correct="off", drift_strength=1.0):
    """h3md_stream_save.stream_save with RefinedSource as the frame source (same timing, writers, RIFE chunk / OOM
    ladder, atomic .part rename).  Chunk size: auto_chunk with the refine buffers added to the RAM budget."""
    t0 = time.time()
    base_src = src if src is not None else S.SegmentSource(info)
    drift_rows = S.attach_drift(base_src, drift_correct, drift_strength)   # v0.2.0: corrected before the face refine
    src = RefinedSource(base_src, refiner)
    n = src.total
    source_fps = float(src.fps)
    out_fps = float(target_fps)
    if out_fps <= 0:
        raise ValueError(f"{_TAG} target_fps must be > 0")
    do_interp = bool(interpolate) and abs(source_fps - out_fps) >= 0.01
    if not bool(interpolate) and abs(source_fps - out_fps) >= 0.01:
        LOG.warning("%s interpolate is OFF but target_fps = %.3f: the video plays %.2fx %s than real time and the "
                    "audio will NOT match", _TAG, out_fps, out_fps / source_fps,
                    "faster" if out_fps > source_fps else "slower")
    if do_interp:
        wrapper = interpolator if interpolator is not None else S.load_interpolator(model_name, bool(use_fp16))
        positions = S.interpolator_positions(wrapper, source_fps, out_fps, n)
        device = getattr(wrapper, "device", torch.device("cpu"))
        fp16 = bool(getattr(wrapper, "use_fp16", False))
    else:
        wrapper, device, fp16 = None, None, False
        positions = [(i, i, 0.0) for i in range(n)]
    audio = src.audio()
    if trim_to_audio and fmt in S.VIDEO_FORMATS:
        if audio is not None and audio.get("waveform") is not None:
            a_sec = audio["waveform"].shape[-1] / float(audio["sample_rate"])
            limit = max(1, int(math.floor(a_sec * out_fps + 1e-6)))
            if limit < len(positions):
                positions = positions[:limit]
    forward = len(positions)
    if forward <= 0:
        raise ValueError(f"{_TAG} nothing to write ({n} source frames)")
    total_out = forward
    ratio = forward / max(1, n)
    cw, ch = refiner.cw, refiner.ch
    # RAM per chunk source frame: the refined chunk + its base read + the stitched copy (3 frames) or the chunk +
    # the RIFE outputs, plus the crops and the VOSR2 results at canvas size
    out_per_src = max(ratio, 2.0) + 2.0 * (cw * ch) / float(max(1, src.w * src.h))
    part_path = path + ".part"
    LOG.info("%s %d frames %dx%d -> %s (%s): %d frames @ %.3f fps, face refine on %d frame(s) at %dx%d%s", _TAG, n,
             src.w, src.h, path, fmt, total_out, out_fps, sum(refiner.todo), cw, ch,
             f" (RIFE {source_fps:g}->{out_fps:g}, scale {scale}, batch {batch_size})" if do_interp else "")
    if fmt == "image/png-sequence" and os.path.isdir(part_path):
        shutil.rmtree(part_path, ignore_errors=True)
    if fmt in S.VIDEO_FORMATS:
        writer = S.StreamWriter(part_path, src.w, src.h, out_fps, audio, crf=crf, pix_fmt=pix_fmt, metadata=metadata,
                                codec="h265" if fmt == "video/h265-mp4" else "h264")
    else:
        writer = S.open_writer(fmt, part_path, src.w, src.h, out_fps, audio, crf, pix_fmt, metadata, loop_count)
    meta_png = metadata and fmt in S.LOOP_FORMATS
    peak_chunk, chunks, sizes = 0, 0, []
    state = {"cap": None, "events": []}

    def produce(j, size):
        if do_interp:
            _lo, _hi, _j0, j1, outs, size = S.interpolate_with_fallback(wrapper, src, positions, j, n, size,
                                                                       float(scale), int(batch_size), state)
            return j1, outs, None, size
        _lo, _hi, _j0, j1 = S.next_chunk(positions, j, n, size)
        frames = src.read(_lo, _hi)
        return j1, list(frames), frames, size

    try:
        with S.autocast_ctx(bool(use_fp16) and do_interp):
            j = 0
            while j < forward:
                S.check_interrupt()
                if chunk_frames:
                    size, why = max(1, int(chunk_frames)), "pinned"
                else:
                    size, why = S.auto_chunk(src.h, src.w, device, float(scale), fp16, int(batch_size), out_per_src,
                                             do_interp, state["cap"])
                j1, outs, frames, size = produce(j, size)
                if chunks == 0 or size != sizes[-1]:
                    LOG.info("%s chunk %d: %d source frames (%s)", _TAG, chunks + 1, size, why)
                sizes.append(size)
                peak_chunk = max(peak_chunk, S._held_bytes(outs if frames is None else [frames] + outs))
                if meta_png and chunks == 0 and outs:
                    S.metadata_png(os.path.splitext(path)[0] + ".png", outs[0], metadata)
                writer.write(outs)
                del frames, outs
                src.retain(positions[j1][0] if j1 < forward else n)
                chunks += 1
                j = j1
                if pbar is not None:
                    pbar.update_absolute(j1, total_out)
        writer.finish()
    except BaseException:
        writer.abort()
        src.close()
        try:
            if os.path.isdir(part_path):
                shutil.rmtree(part_path, ignore_errors=True)
            else:
                os.remove(part_path)
        except OSError:
            pass
        if meta_png:
            try:
                os.remove(os.path.splitext(path)[0] + ".png")
            except OSError:
                pass
        LOG.warning("%s failed or interrupted; partial file removed, cache KEPT at %s", _TAG, src.dir)
        raise
    src.close()
    if writer.frames != total_out:
        raise RuntimeError(f"{_TAG} wrote {writer.frames} frames, expected {total_out}; cache kept")
    os.replace(part_path, path)
    twice = sorted(i for i, c in refiner.count.items() if c > 1)
    report = {"file": path, "frames": total_out, "fps": out_fps, "source_frames": n, "width": src.w, "height": src.h,
              "seconds": round(total_out / out_fps, 3), "interpolated": do_interp, "chunks": chunks, "format": fmt,
              "real_time": do_interp or abs(source_fps - out_fps) < 0.01,
              "chunk_sizes": sorted(set(sizes)), "oom_events": state["events"] + refiner.events,
              "refined_frames": len(refiner.count), "refined_twice": len(twice),
              "vosr2_s": round(refiner.t_vosr2, 1), "stitch_s": round(refiner.t_stitch, 1),
              "audio_seconds": round(writer.a_done / writer.sr, 3) if writer.a is not None else None,
              "peak_chunk_mb": round(peak_chunk / 1e6, 1), "cache_dir": src.dir, "elapsed_s": round(time.time() - t0, 1)}
    if drift_rows is not None:
        report["drift_report"] = S.write_drift_report(path, drift_rows, drift_strength)
    LOG.info("%s saved %s", _TAG, report)
    return report


# ---------------------------------------------------------------------------
# node
# ---------------------------------------------------------------------------

TRACK_REQUIRED = ("detector", "confidence", "crop_factor", "canvas_width", "canvas_height", "canvas_mode",
                  "smooth_window", "size_smooth_window", "smooth_method", "size_mode")
TRACK_OPTIONAL = ("identity_track", "identity_threshold", "select", "fallback_detector", "fallback_head_frac",
                  "select_index", "identity_model", "cut_detection", "cut_threshold", "absent_shots", "X", "Y",
                  "frame_index")
STITCH_REQUIRED = ("paste_region", "mask_dilation", "feather", "colour_match", "blend", "undetected_frames")
STITCH_OPTIONAL = ("feather_scales_with_crop",)
CANVAS_DEFAULT = 512

# used only when ComfyUI-H3-FaceRefine is not loaded (the node then shows, but face_refine needs it)
_FALLBACK_TRACK = {
    "detector": (["none"], {}),
    "confidence": ("FLOAT", {"default": 0.35, "min": 0.05, "max": 0.95, "step": 0.05}),
    "crop_factor": ("FLOAT", {"default": 2.5, "min": 1.2, "max": 8.0, "step": 0.1}),
    "canvas_width": ("INT", {"default": CANVAS_DEFAULT, "min": 128, "max": 1344, "step": 32}),
    "canvas_height": ("INT", {"default": CANVAS_DEFAULT, "min": 128, "max": 1344, "step": 32}),
    "canvas_mode": (["manual", "auto_no_downscale", "auto_capped_768"], {"default": "manual"}),
    "smooth_window": ("INT", {"default": 21, "min": 1, "max": 201, "step": 2}),
    "size_smooth_window": ("INT", {"default": 51, "min": 1, "max": 201, "step": 2}),
    "smooth_method": (["gaussian", "savgol", "moving_average"], {"default": "gaussian"}),
    "size_mode": (["max_of_clip", "per_frame"], {"default": "per_frame"}),
    "identity_track": ("BOOLEAN", {"default": True}),
    "identity_threshold": ("FLOAT", {"default": 0.28, "min": 0.0, "max": 1.0, "step": 0.01}),
    "select": (["largest_face"], {"default": "largest_face"}),
    "fallback_detector": (["none"], {"default": "none"}),
    "fallback_head_frac": ("FLOAT", {"default": 0.5, "min": 0.0, "max": 1.5, "step": 0.05}),
    "select_index": ("INT", {"default": 0, "min": 0, "max": 63}),
    "identity_model": (["insightface"], {"default": "insightface"}),
    "cut_detection": (["none"], {"default": "none"}),
    "cut_threshold": ("FLOAT", {"default": 3.0, "min": 0.5, "max": 20.0, "step": 0.1}),
    "absent_shots": (["off", "by_identity"], {"default": "off"}),
    "X": ("INT", {"default": 0, "min": 0, "max": 16384}),
    "Y": ("INT", {"default": 0, "min": 0, "max": 16384}),
    "frame_index": ("INT", {"default": 0, "min": 0, "max": 999999}),
}
_FALLBACK_STITCH = {
    "paste_region": (["face_only", "face_ellipse", "full_crop"], {"default": "face_only"}),
    "mask_dilation": ("INT", {"default": 16, "min": 0, "max": 256, "step": 2}),
    "feather": ("INT", {"default": 6, "min": 0, "max": 256, "step": 2}),
    "colour_match": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
    "blend": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.05}),
    "undetected_frames": (["fade_out", "skip", "composite_anyway"], {"default": "fade_out"}),
    "feather_scales_with_crop": ("BOOLEAN", {"default": False}),
}


def _copy_spec(spec, **override):
    if len(spec) > 1 and isinstance(spec[1], dict):
        opts = dict(spec[1])
        opts.update(override)
        return (spec[0], opts) + tuple(spec[2:])
    return (spec[0], dict(override)) if override else tuple(spec)


def _fr_specs():
    """Widget specs (with tooltips) of the loaded H3 Face Track + Crop / H3 Face Stitch Back, else the fallback."""
    try:
        reg = _registry()
        tc = reg["H3FaceTrackCrop"].INPUT_TYPES()
        st = reg["H3FaceStitch"].INPUT_TYPES()
        track = dict(tc.get("required", {}), **tc.get("optional", {}))
        stitch = dict(st.get("required", {}), **st.get("optional", {}))
        out_t = {k: track.get(k, _FALLBACK_TRACK[k]) for k in TRACK_REQUIRED + TRACK_OPTIONAL}
        out_s = {k: stitch.get(k, _FALLBACK_STITCH[k]) for k in STITCH_REQUIRED + STITCH_OPTIONAL}
    except Exception:
        out_t, out_s = dict(_FALLBACK_TRACK), dict(_FALLBACK_STITCH)
    for k in ("canvas_width", "canvas_height"):
        out_t[k] = _copy_spec(out_t[k], default=CANVAS_DEFAULT)
    return out_t, out_s


def _spec_default(spec):
    if len(spec) > 1 and isinstance(spec[1], dict) and "default" in spec[1]:
        return spec[1]["default"]
    if isinstance(spec[0], (list, tuple)) and spec[0]:
        return spec[0][0]
    return None


def _save_inputs():
    req = dict(S.H3MDSaveVideoInterp.INPUT_TYPES()["required"])
    req.pop("segments", None)
    return req


class H3MDSaveVideoFaceRefineInterp:
    @classmethod
    def INPUT_TYPES(cls):
        track, stitch = _fr_specs()
        req = {
            "segments": (S.SEGMENTS_TYPE, {"tooltip": "long video: H3MD Loop Sampler output 'long video'."}),
        }
        req.update(_save_inputs())
        req["face_refine"] = ("BOOLEAN", {"default": True, "tooltip":
                              "On: track the face over the whole video, restore it with VOSR2 and paste it back, "
                              "chunk by chunk, before interpolation. Off: exactly H3MD Save Video Interp."})
        req.update({k: track[k] for k in TRACK_REQUIRED})
        req.update({k: stitch[k] for k in STITCH_REQUIRED})
        req["vosr2_seed"] = ("INT", {"default": 42, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "tooltip":
                             "VOSR2 / SeedVR2 seed, the same for every chunk (scale is fixed at 1)."})
        opt = {
            "vosr2_model": ("TE_SPEED_VOSR2_MODEL,VOSR2_MODEL", {"lazy": True, "tooltip": "VOSR2 model, used when "
                            "refine_model = VOSR2: 'VOSR 2.0 Model Loader' of the original ComfyUI-VOSR2 (recommended) "
                            "or the TE-Speed VOSR2 loader."}),
            "vosr2_settings": ("TE_SPEED_VOSR2_SETTINGS", {"lazy": True, "tooltip": "Optional TE-Speed VOSR2 settings."}),
            "seedvr2_model": ("MODEL,SEEDVR2_DIT", {"lazy": True, "tooltip": "SeedVR2 model, used when refine_model "
                              "= SeedVR2: 'SeedVR2 (Down)Load DiT Model' of ComfyUI-SeedVR2_VideoUpscaler, or the "
                              "TE-Speed SeedVR2 Loader 'model'. Other parameters run at that node's own defaults."}),
            "seedvr2_vae": ("VAE,SEEDVR2_VAE", {"lazy": True, "tooltip": "SeedVR2 VAE: 'SeedVR2 (Down)Load VAE Model' "
                            "or the TE-Speed SeedVR2 Loader 'vae' (from the same pack as seedvr2_model)."}),
            "identity_reference": ("IMAGE", {"tooltip": "Optional: a picture of the person to follow "
                                                        "(H3 Face Track + Crop identity_reference)."}),
        }
        opt.update({k: track[k] for k in TRACK_OPTIONAL})
        opt.update({k: stitch[k] for k in STITCH_OPTIONAL})
        if EXPOSE_ADVANCED_IDENTITY_INPUTS:
            opt["identity_clip_vision"] = ("CLIP_VISION", {})
            opt["face_pick"] = ("H3FACEPICK", {})
        opt["refine_model"] = (["VOSR2", "SeedVR2"], {"default": "VOSR2", "tooltip":
                               "Face restore path: VOSR2 uses vosr2_model (+ vosr2_settings); SeedVR2 uses "
                               "seedvr2_model + seedvr2_vae. The other inputs are ignored."})
        opt.update(S.DRIFT_INPUTS)   # v0.2.0, appended LAST
        return {"required": req, "optional": opt,
                "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("STRING", "INT", "VIDEO", "STRING", S.PREVIEW_TYPE)   # 'preview' appended LAST (2026-10-23)
    RETURN_NAMES = ("video_path", "frame_count", "video", "report", "preview")
    OUTPUT_NODE = True
    FUNCTION = "save"
    CATEGORY = S.CATEGORY
    DESCRIPTION = ("H3MD Save Video Interp with face refinement: pass 1 tracks the face over the whole long video "
                   "(H3 Face Track + Crop logic), pass 2 crops, restores with TE-Speed VOSR2 and pastes back "
                   "(H3 Face Stitch Back) chunk by chunk, then RIFE-interpolates and encodes like Save Video Interp.")

    def check_lazy_status(self, face_refine=True, refine_model="VOSR2", **kw):
        """Only the selected refine_model path's loader runs (the other stays unevaluated / unloaded)."""
        if not face_refine:
            return []
        want = ("seedvr2_model", "seedvr2_vae") if refine_model == "SeedVR2" else ("vosr2_model", "vosr2_settings")
        return [k for k in want if k in kw]   # only connected inputs (ComfyUI raises for an unconnected one)

    def save(self, segments=None, vosr2_model=None, face_refine=True, vosr2_seed=42, vosr2_settings=None,
             identity_reference=None, identity_clip_vision=None, face_pick=None, prompt=None, extra_pnginfo=None,
             unique_id=None, seedvr2_model=None, seedvr2_vae=None, refine_model="VOSR2", **kw):
        save_keys = set(_save_inputs()) | set(S.REMOVED_INPUTS) | set(S.DRIFT_INPUTS)
        save_kw = {k: v for k, v in kw.items() if k in save_keys}
        track, stitch = _fr_specs()
        track_kw = {k: kw.get(k, _spec_default(track[k])) for k in TRACK_REQUIRED + TRACK_OPTIONAL}
        stitch_kw = {k: kw.get(k, _spec_default(stitch[k])) for k in STITCH_REQUIRED + STITCH_OPTIONAL}
        unknown = sorted(set(kw) - save_keys - set(track_kw) - set(stitch_kw))
        if unknown:
            raise TypeError(f"{_TAG} unexpected inputs {unknown}")
        if not face_refine:
            res = S.H3MDSaveVideoInterp().save(segments=segments, prompt=prompt, extra_pnginfo=extra_pnginfo,
                                               unique_id=unique_id, **save_kw)
            path, frames, video, preview = res["result"]
            return {"ui": res["ui"], "result": (path, frames, video, "face_refine off: saved like H3MD Save Video "
                                                                     f"Interp ({frames} frames) -> {path}", preview)}
        if identity_reference is None and isinstance(segments, dict) and segments.get("id_ref_file"):
            # 2026-10-28 idref: the Director's ID Ref picture (a wired identity_reference still wins)
            try:
                try:
                    from . import h3md_director as _D
                except ImportError:
                    import h3md_director as _D
                identity_reference = _D.load_image(segments["id_ref_file"])
                LOG.info("%s identity_reference = Director ID Ref %s", _TAG, os.path.basename(segments["id_ref_file"]))
            except Exception as exc:
                LOG.warning("%s Director ID Ref could not be loaded (%s) - using the node's own selection", _TAG, exc)
        try:
            return self._save_refined(segments, vosr2_model, int(vosr2_seed), vosr2_settings, identity_reference,
                                      identity_clip_vision, face_pick, prompt, extra_pnginfo, unique_id, save_kw,
                                      track_kw, stitch_kw, seedvr2_model, seedvr2_vae, refine_model)
        except NothingToRefine as exc:
            LOG.warning("%s face refine skipped: %s Saving the video WITHOUT face refine.", _TAG, exc)
            res = S.H3MDSaveVideoInterp().save(segments=segments, prompt=prompt, extra_pnginfo=extra_pnginfo,
                                               unique_id=unique_id, **save_kw)
            path, frames, video, preview = res["result"]
            return {"ui": res["ui"], "result": (path, frames, video, f"face refine SKIPPED ({exc}); saved without "
                                                                     f"refine ({frames} frames) -> {path}", preview)}

    def _save_refined(self, segments, vosr2_model, seed, vosr2_settings, identity_reference, identity_clip_vision,
                      face_pick, prompt, extra_pnginfo, unique_id, save_kw, track_kw, stitch_kw, seedvr2_model=None,
                      seedvr2_vae=None, refine_model="VOSR2"):
        g = lambda k, dflt: save_kw.get(k, dflt)  # noqa: E731
        fmt = g("format", "video/h264-mp4")
        removed = sorted(k for k in save_kw if k in S.REMOVED_INPUTS)
        if removed:
            LOG.info("%s ignored removed input(s) %s", _TAG, removed)
        if fmt not in S.FORMATS:
            raise ValueError(f"{_TAG} unknown format {fmt!r} (one of {', '.join(S.FORMATS)})")
        if not isinstance(segments, dict):
            raise ValueError(f"{_TAG} connect H3MD Loop Sampler 'long video' (needs cache_mode=disk, decode_output on)")
        if refine_model not in ("VOSR2", "SeedVR2"):
            raise ValueError(f"{_TAG} unknown refine_model {refine_model!r} (VOSR2 or SeedVR2)")
        if refine_model == "SeedVR2":
            if seedvr2_model is None or seedvr2_vae is None:
                raise ValueError(f"{_TAG} refine_model=SeedVR2 needs seedvr2_model + seedvr2_vae "
                                 f"(SeedVR2 Load DiT / VAE Model, or TE-Speed SeedVR2 Loader 'model' and 'vae')")
            if is_numz_seedvr2(seedvr2_model) != isinstance(seedvr2_vae, dict):
                raise ValueError(f"{_TAG} seedvr2_model and seedvr2_vae must come from the same SeedVR2 pack")
            vosr2_model = vosr2_settings = None
        else:
            if vosr2_model is None:
                raise ValueError(f"{_TAG} refine_model=VOSR2 needs vosr2_model ('VOSR 2.0 Model Loader' of ComfyUI-VOSR2)")
            seedvr2_model = seedvr2_vae = None
        fr = _fr_module()
        _stitch_class()
        if seedvr2_model is not None and is_numz_seedvr2(seedvr2_model):
            if _seedvr2_numz() is None:
                raise RuntimeError(f"{_TAG} 'SeedVR2 Video Upscaler' (ComfyUI-SeedVR2_VideoUpscaler) is not loaded")
        elif seedvr2_model is not None:
            _seedvr2_class()
        elif not is_original_vosr2(vosr2_model):
            _vosr2_class()
        info = segments
        cache_dir = os.path.abspath(info["cache_dir"])
        S.notify_ui(unique_id, {"cache_dir": cache_dir, "state": "writing"})
        if info.get("hires_mode") == "final_upscale":
            LOG.warning("%s the loop ran hires_mode=final_upscale: its cache holds the LOW-RES preview frames", _TAG)

        # ---- pass 1: detect + solve on the whole clip --------------------------------------------------------
        t0 = time.time()
        src1 = S.SegmentSource(info)
        try:
            frames = LazyFrames(src1)
            B, H, W, _c = frames.shape
            model_box = []

            def get_model():
                if not model_box:
                    model_box.append(fr._load_detector(track_kw["detector"]))
                return model_box[0]

            if face_pick is not None:
                all_boxes, all_confs, segs_given, forced, absent_frames, pick_note = faces_from_pick(face_pick, B, W, H)
                cuts, cut_note = [], ""
            else:
                pbar1 = S.progress_bar(max(1, B), unique_id)
                all_boxes, all_confs, cuts, cut_note = detect_pass(fr, frames, track_kw["detector"],
                                                                   track_kw["confidence"], track_kw["cut_detection"],
                                                                   track_kw["cut_threshold"], pbar1, get_model)
                segs_given, forced, absent_frames, pick_note = None, None, None, ""
            t1 = time.time()
            solved = solve_track(fr, frames, all_boxes, all_confs, cuts, identity_reference=identity_reference,
                                 identity_clip_vision=identity_clip_vision, face_pick=face_pick,
                                 segs_given=segs_given, forced=forced, absent_frames=absent_frames,
                                 cut_note=cut_note, pick_note=pick_note, get_model=get_model, **track_kw)
        finally:
            src1.close()
        t2 = time.time()
        transform = solved["transform"]
        LOG.info("%s track solved in %.1fs (detect %.1fs):\n%s", _TAG, t2 - t0, t1 - t0, solved["report"])

        # ---- pass 2: refine + interpolate + encode, chunk by chunk ---------------------------------------------
        refiner = FaceRefiner(fr, transform, vosr2_model, vosr2_settings, seed, stitch_kw, seedvr2_model, seedvr2_vae)
        path, file, subfolder, kind = S.save_path(g("filename_prefix", "video/H3MD"), g("output_folder", ""), W, H,
                                                  S.EXTENSIONS[fmt], temp=not g("save_output", True))
        meta = None
        try:
            from comfy.cli_args import args
            disabled = bool(getattr(args, "disable_metadata", False))
        except Exception:
            disabled = False
        if not disabled and g("save_metadata", True):
            meta = dict(extra_pnginfo or {})
            if prompt is not None:
                meta["prompt"] = prompt
            meta = meta or None
        pbar = S.progress_bar(max(1, B), unique_id)
        try:
            rep = refined_stream_save(info, path, refiner, g("interpolate", False), g("target_fps", S.SOURCE_FPS),
                                      g("scale", 1.0), g("model_name", "flownet.pkl"), g("batch_size", 8),
                                      g("use_fp16", True), g("crf", 19), meta, pbar=pbar, fmt=fmt,
                                      pix_fmt=g("pix_fmt", "yuv420p"), loop_count=g("loop_count", 0),
                                      trim_to_audio=g("trim_to_audio", False),
                                      drift_correct=g("drift_correct", "off"), drift_strength=g("drift_strength", 1.0))
        except BaseException:
            S.notify_ui(unique_id, {"cache_dir": cache_dir, "state": "kept (save failed)"})
            raise
        try:
            import comfy.model_management as mm
            mm.soft_empty_cache()
        except Exception:
            pass
        state = "kept"
        written = (os.path.isdir(path) and any(f.endswith(".png") for f in os.listdir(path))) \
            if fmt == "image/png-sequence" else (os.path.isfile(path) and os.path.getsize(path) > 0)
        if g("delete_cache_after_save", False) and written:
            state = "deleted after save" if S.delete_cache_dir(cache_dir) else "kept (delete refused, see log)"
        else:
            LOG.info("%s cache kept at %s", _TAG, cache_dir)
        report = "\n".join([
            f"saved {rep['frames']} frames @ {rep['fps']:g} fps ({rep['seconds']}s) -> {path}",
            f"face refine: {rep['refined_frames']}/{B} frames refined at {refiner.cw}x{refiner.ch} "
            f"(VOSR2 {rep['vosr2_s']}s, stitch {rep['stitch_s']}s), {rep['chunks']} chunk(s) "
            f"{rep['chunk_sizes']}, track solve {t2 - t0:.1f}s, total {time.time() - t0:.1f}s"
            + (f", refined twice: {rep['refined_twice']}" if rep["refined_twice"] else ""),
            f"interpolated: {rep['interpolated']}  oom events: {rep['oom_events'] or 'none'}  cache: {state}",
            "--- track ---",
            solved["report"],
        ])
        ui = {"h3md_cache_dir": [cache_dir], "h3md_cache_state": [state]}
        preview = S.preview_entry(file, subfolder, kind, fmt, rep["fps"])   # no built-in player any more
        return {"ui": ui, "result": (path, int(rep["frames"]), S.video_output(path, fmt), report, preview)}


NODE_CLASS_MAPPINGS = {NODE_ID: H3MDSaveVideoFaceRefineInterp}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "H3MD Save Video Face Refine And Interp"}
