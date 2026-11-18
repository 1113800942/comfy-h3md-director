"""H3MD 0.5.3 face redraw by mask (2026-11-17).

A small face in a wide shot has too few pixels for any restore model: VOSR2 at scale 1 cannot add what was never
drawn.  Here the tracked face crop (enlarged to a 768x768 canvas) is redrawn by H3 itself, but only inside a mask of
the head, the way the published 'minimax_h3_r2v_perfect_face_refine' workflow does it:

  1. SAM3 (ComfyUI core) tracks "head" on the crops -> per-frame head masks;
  2. the masks are cleaned (specks and flickers dropped, edge grown) and reduced to H3's latent grid (16x space,
     2x2 tokens, 1+4+4+4+4 frames per 17-frame clip), grown first so the redraw reaches a little past the head;
  3. the crops are VAE-encoded, that latent mask is the noise mask, DifferentialDiffusion softens its edge;
  4. the clip's own sound is encoded and locked (noise mask 0), so the lips keep the timing of the audio;
  5. conditioning = one face close-up (the reference photo cropped to its face, 2 MP), prompt empty;
  6. euler, beta schedule, 6 steps (our 768 turbo LoRA; tested at 4 and 8), denoise 0.8 (0.5 lighter, up to 1.0);
  7. the save node then runs its restore model (VOSR2) and pastes the crops back through the RAW SAM3 head mask.

Tested 11-17 (L15b, Water wide shot, face ~90 px): face and hair strands clear and close to the reference, calm
expression, lips on the original audio.

Long videos: units of up to 362 frames (15 s, one pass as tested); a longer clip is chained like the Loop: the
next unit starts from the previous unit's redrawn overlap (17+ frames, locked) instead of two separate redraws being
cross-faded (11-17 test: cross-fading 5 s units left a double face where the eyes differed).  SAM3 masks
(<cache>/face_mask/) and redrawn crops (<cache>/face_layer/) are kept per unit, so a failed save does not redraw.
SAM3 runs over every unit first and is unloaded before H3 is used.

All models are ComfyUI core nodes called through the registry; nothing third-party is bundled.  The mask cleanup and
the latent-space mapping are H3MD's own code.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import time

import numpy as np
import torch
import torch.nn.functional as F

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD FaceRedraw]"

UNIT_FRAMES = 362            # 15 s at 24 fps (5 + 17k): one pass, as tested (L15b); longer clips chain units
OVERLAP_FRAMES = 17          # cross-fade between neighbouring units
MASK_DIR, LAYER_DIR = "face_mask", "face_layer"

MASK_TEXT = "head"           # 11-17: "face" left the hair blurry; "head" redraws hair and face together
DENOISE = 0.8                # 0.5 = the published baseline; 0.8 tested best on a 90 px face
STEPS, SCHEDULER, SAMPLER = 6, "beta", "euler"   # YJ 11-17: 6 (our 768 turbo LoRA is a 4-step LoRA; the reference used an 8-step one)
SEED = 42

SAM3_CKPT = "sam3.1_multiplex_fp16.safetensors"
SAM3_THRESHOLD = 0.5
SAM3_MAX_OBJECTS = 4         # the object nearest the canvas centre (= the tracked face) is kept

CLEAN_THRESHOLD = 0.3        # mask cleanup: what counts as head
CLEAN_SHRINK = 12            # blobs thinner than ~2x this (px) are specks
CLEAN_MIN_FRAMES = 4         # blobs present in fewer frames are flicker
CLEAN_EDGE_GROW = 16         # kept blobs are grown by this (px) so the soft edge survives

LATENT_GROW_PX = 36          # the redraw reaches this far past the head (pixels, before the 16x reduction)
LATENT_GROW_FRAMES = 1       # and one frame before / after
VAE_SPATIAL, VAE_CLIP, TOKEN = 16, 17, 2   # H3 video VAE: 16x space, 17-frame clips (1+4+4+4+4), 2x2 latent tokens

CANVAS = 768                 # crop canvas for the redraw (multiple of 32)
REF_CROP_FACTOR = 2.2        # the reference photo is cropped to its face box x this (head and hair)
REF_MEGAPIXELS, REF_MULTIPLE = 2.0, 32


# ---------------------------------------------------------------------------
# units
# ---------------------------------------------------------------------------

def valid_length(n: int) -> int:
    """Largest 5 + 17k that fits in n frames (0 when n < 5)."""
    n = int(n)
    return 0 if n < 5 else n - ((n - 5) % 17)


def unit_spans(total: int, unit: int = UNIT_FRAMES, overlap: int = OVERLAP_FRAMES):
    """[(a, b)] covering [0, total): every span 5 + 17k frames, neighbours overlap by `overlap` frames, the last
    span ends at the last frame (it may overlap more).  Fewer than 5 frames -> []."""
    total = int(total)
    length = valid_length(min(int(unit), total))
    if length == 0:
        return []
    step = max(1, length - max(0, int(overlap)))
    spans, a = [], 0
    while a + length < total:
        spans.append((a, a + length))
        a += step
    last = (total - length, total)
    if not spans or spans[-1] != last:
        spans.append(last)
    return spans


def span_weight(span, i: int, total: int, ramp: int = OVERLAP_FRAMES) -> float:
    """Cross-fade weight of frame i in span: ramps up after a start that is not the clip start, down before an end
    that is not the clip end."""
    a, b = span
    if not a <= i < b:
        return 0.0
    r = float(max(1, ramp) + 1)
    w = 1.0
    if a > 0:
        w = min(w, (i - a + 1) / r)
    if b < total:
        w = min(w, (b - i) / r)
    return max(w, 0.0)


class UnitStore:
    """Per-unit uint8 arrays on disk (<dir>/unit_<a>_<b>.npy, first axis = frames), cross-faded by frame().
    A different key (other settings or track) empties the folder."""

    def __init__(self, folder: str, total: int, key: str, overlap: int = OVERLAP_FRAMES):
        self.dir, self.total, self.key, self.overlap = folder, int(total), key, int(overlap)
        self.spans, self._mm = [], {}
        meta = os.path.join(folder, "store.json")
        old = None
        try:
            with open(meta, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            pass
        if old is None or old.get("key") != key:
            if os.path.isdir(folder):
                shutil.rmtree(folder, ignore_errors=True)
            os.makedirs(folder, exist_ok=True)
            with open(meta, "w", encoding="utf-8") as f:
                json.dump({"key": key, "total": self.total}, f)
        else:
            for name in sorted(os.listdir(folder)):
                if name.startswith("unit_") and name.endswith(".npy"):
                    a, b = (int(v) for v in name[5:-4].split("_"))
                    self.spans.append((a, b))
            self.spans.sort()

    def _path(self, span):
        return os.path.join(self.dir, f"unit_{span[0]}_{span[1]}.npy")

    def has(self, span) -> bool:
        return (int(span[0]), int(span[1])) in self.spans

    def put(self, span, x: torch.Tensor):
        """x float in [0, 1], first axis = the span's frames."""
        span = (int(span[0]), int(span[1]))
        arr = (x.clamp(0, 1) * 255.0 + 0.5).to(torch.uint8).cpu().numpy()
        if arr.shape[0] != span[1] - span[0]:
            raise ValueError(f"{_TAG} unit {span}: {arr.shape[0]} frames for {span[1] - span[0]}")
        part = self._path(span) + ".part"
        with open(part, "wb") as f:
            np.save(f, arr)
        os.replace(part, self._path(span))
        self._mm.pop(span, None)
        if span not in self.spans:
            self.spans.append(span)
            self.spans.sort()

    def get(self, span) -> torch.Tensor:
        span = (int(span[0]), int(span[1]))
        return torch.from_numpy(np.array(self._unit(span))).to(torch.float32) / 255.0

    def _unit(self, span):
        if span not in self._mm:
            self._mm[span] = np.load(self._path(span), mmap_mode="r")
        return self._mm[span]

    def frame(self, i: int):
        """Cross-faded float32 frame i, or None when no stored unit holds it."""
        acc, wsum = None, 0.0
        for span in self.spans:
            w = span_weight(span, i, self.total, self.overlap)
            if w <= 0.0:
                continue
            x = torch.from_numpy(np.array(self._unit(span)[i - span[0]])).to(torch.float32) / 255.0
            acc = x * w if acc is None else acc + x * w
            wsum += w
        return None if acc is None else acc / wsum

    def close(self):
        self._mm = {}


def store_key(transform, total: int, settings: dict) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(settings, sort_keys=True, default=str).encode())
    h.update(str(int(total)).encode())
    h.update(json.dumps([[round(float(v), 2) for v in b] for b in transform.get("boxes", [])]).encode())
    h.update(json.dumps(list(transform.get("canvas", []))).encode())
    return h.hexdigest()[:24]


# ---------------------------------------------------------------------------
# masks (H3MD's own code)
# ---------------------------------------------------------------------------

def latent_groups(frames: int, clip: int = VAE_CLIP):
    """Pixel-frame ranges [(s, e)] that make each H3 latent frame.  The H3 VAE encodes 17-frame clips: the first
    frame of a clip alone, then four frames per latent; the last clip is padded with its last frame and the VAE
    drops 3 latents, so a 5 + 17k frame unit gives 5k + 2 latents."""
    frames = int(frames)
    out = []
    for c0 in range(0, frames, clip):
        out.append((c0, c0 + 1))
        for s in range(c0 + 1, min(c0 + clip, frames), 4):
            out.append((s, min(s + 4, c0 + clip, frames)))
    return out


def even_groups(frames: int, latents: int):
    """Fallback when the latent count is not the expected one: frames split evenly over the latents."""
    edges = [round(k * frames / latents) for k in range(latents + 1)]
    return [(edges[k], max(edges[k + 1], edges[k] + 1)) for k in range(latents)]


def _grow(x: torch.Tensor, px: int) -> torch.Tensor:
    """Grey dilation of [T, H, W] by about px pixels in every direction (alternating 3x3 square and cross steps,
    which is close to a disc)."""
    if px <= 0:
        return x
    y = x.unsqueeze(1)
    for k in range(int(px)):
        if k % 2:
            y = torch.maximum(F.max_pool2d(y, (3, 1), 1, (1, 0)), F.max_pool2d(y, (1, 3), 1, (0, 1)))
        else:
            y = F.max_pool2d(y, 3, 1, 1)
    return y.squeeze(1)


def _erode_bool(x: torch.Tensor, px: int) -> torch.Tensor:
    """Binary erosion of bool [T, H, W] by px pixels; outside the frame counts as inside (a head cut by the canvas
    edge is not eaten from that side)."""
    y = (~x).float()
    y = F.pad(y.unsqueeze(1), (px, px, px, px), value=0.0)
    y = _grow(y.squeeze(1), px)[:, px:-px or None, px:-px or None] if px > 0 else y.squeeze(1)
    return y < 0.5


def clean_masks(m: torch.Tensor, threshold: float = CLEAN_THRESHOLD, shrink: int = CLEAN_SHRINK,
                min_frames: int = CLEAN_MIN_FRAMES, edge_grow: int = CLEAN_EDGE_GROW) -> torch.Tensor:
    """Soft masks [T, H, W] -> the same masks with specks and short flickers removed.
    A blob (connected in space and time) is kept when it has a core that survives shrinking by `shrink` px and it
    lasts at least `min_frames` frames; the kept blobs are grown by `edge_grow` px and gate the original values."""
    from scipy import ndimage
    m = m.to(torch.float32).cpu()
    on = m > float(threshold)
    if not bool(on.any()):
        return torch.zeros_like(m)
    core = _erode_bool(on, int(shrink)) if shrink > 0 else on
    labels, count = ndimage.label(on.numpy(), structure=np.ones((3, 3, 3), dtype=bool))
    keep = np.zeros(count + 1, dtype=bool)
    keep[np.unique(labels[core.numpy()])] = True
    keep[0] = False
    if min_frames > 1:
        for lab, sl in enumerate(ndimage.find_objects(labels), start=1):
            if sl is not None and sl[0].stop - sl[0].start < int(min_frames):
                keep[lab] = False
    gate = torch.from_numpy(keep[labels]).to(torch.float32)
    gate = _grow(gate, int(edge_grow))
    return m * gate


def to_latent_mask(m: torch.Tensor, latents: int, grow_px: int = LATENT_GROW_PX,
                   grow_frames: int = LATENT_GROW_FRAMES, spatial: int = VAE_SPATIAL, token: int = TOKEN,
                   lock_frames: int = 0):
    """Pixel masks [T, H, W] -> H3 latent noise mask [latents, H // spatial, W // spatial]: grown in space and time,
    max over every 16x16 cell, then over every 2x2 token, then over the frames of each latent."""
    x = _grow(m.to(torch.float32), int(grow_px))
    if grow_frames > 0:
        k = 2 * int(grow_frames) + 1
        x = F.max_pool1d(x.permute(1, 2, 0).reshape(-1, 1, x.shape[0]), k, 1, int(grow_frames)) \
            .reshape(x.shape[1], x.shape[2], x.shape[0]).permute(2, 0, 1)
    T, H, W = x.shape
    lh, lw = max(1, H // spatial), max(1, W // spatial)
    x = F.adaptive_max_pool2d(x.unsqueeze(1), (lh, lw))
    if token > 1:
        t = F.max_pool2d(x, token, token, ceil_mode=True)
        x = t.repeat_interleave(token, 2).repeat_interleave(token, 3)[..., :lh, :lw]
    x = x.squeeze(1)
    groups = latent_groups(T)
    if len(groups) != int(latents):
        LOG.warning("%s %d frames gave %d latents (expected %d): mask frames split evenly", _TAG, T, latents,
                    len(groups))
        groups = even_groups(T, int(latents))
    out = torch.stack([x[s:min(e, T)].amax(0) for s, e in groups])
    if lock_frames > 0:   # latents made only of carried-over frames stay as they are (like the Loop's overlap)
        for j, (s, e) in enumerate(groups):
            if e <= lock_frames:
                out[j] = 0.0
    return out


# ---------------------------------------------------------------------------
# node calls, reference, audio
# ---------------------------------------------------------------------------

def _unwrap(res):
    """Outputs of a node call as a tuple (old-style nodes return a tuple, new-style a NodeOutput / dict)."""
    if isinstance(res, dict):
        res = res.get("result", res)
    if hasattr(res, "result") and not isinstance(res, (tuple, list, torch.Tensor)):
        res = res.result
    return tuple(res) if isinstance(res, (tuple, list)) else (res,)


def call_node(reg, name: str, **kw):
    cls = reg.get(name)
    if cls is None:
        raise RuntimeError(f"{_TAG} face refine needs ComfyUI's node '{name}', which is not loaded "
                           "(update ComfyUI)")
    if hasattr(cls, "define_schema"):
        return _unwrap(cls.execute(**kw))
    return _unwrap(getattr(cls(), cls.FUNCTION)(**kw))


def crop_face_reference(fr, image, detector: str, confidence: float, factor: float = REF_CROP_FACTOR):
    """The reference photo cropped to a square around its largest face (x factor), or the photo as it is."""
    if image is None:
        return None, "no reference photo"
    img = image[:1]
    try:
        bgr = fr._to_bgr_u8(img[0])
        res = fr._load_detector(detector).predict(bgr, conf=float(confidence), verbose=False)[0]
        if not len(res.boxes):
            return img, "reference: no face found in the photo, used as it is"
        x0, y0, x1, y1 = max(res.boxes.xyxy.tolist(), key=lambda q: (q[2] - q[0]) * (q[3] - q[1]))
        H, W = int(img.shape[1]), int(img.shape[2])
        side = min(float(max(x1 - x0, y1 - y0)) * float(factor), float(min(W, H)))
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        a = int(round(min(max(cx - side / 2.0, 0), W - side)))
        b = int(round(min(max(cy - side / 2.0, 0), H - side)))
        s = int(side)
        return img[:, b:b + s, a:a + s, :3].contiguous(), f"reference: photo cropped to its face ({s}x{s} px)"
    except Exception as exc:   # the crop is a nicety; never lose a save over it
        return img, f"reference: photo used as it is ({exc})"


def reference_size(h: int, w: int, megapixels: float = REF_MEGAPIXELS, multiple: int = REF_MULTIPLE):
    """(h, w) scaled to about `megapixels` and snapped to a multiple of `multiple`."""
    s = math.sqrt(float(megapixels) * 1e6 / max(1.0, float(h) * float(w)))
    return (max(multiple, int(round(h * s / multiple)) * multiple),
            max(multiple, int(round(w * s / multiple)) * multiple))


def prepare_reference(img: torch.Tensor) -> torch.Tensor:
    h, w = reference_size(int(img.shape[1]), int(img.shape[2]))
    x = img[..., :3].movedim(-1, 1).to(torch.float32)
    mode = "area" if h * w < img.shape[1] * img.shape[2] else "bicubic"
    kw = {} if mode == "area" else {"align_corners": False}
    x = F.interpolate(x, size=(h, w), mode=mode, **kw).clamp(0, 1)
    return x.movedim(1, -1).contiguous()


def slice_audio(audio, a: int, b: int, fps: float = 24.0):
    """The part of a ComfyUI AUDIO dict under frames [a, b), padded with silence to the full length; None when
    there is no audio."""
    if not isinstance(audio, dict) or audio.get("waveform") is None:
        return None
    sr = int(audio["sample_rate"])
    wave = audio["waveform"]
    s0, n = int(round(a / fps * sr)), int(round((b - a) / fps * sr))
    part = wave[..., s0:s0 + n]
    if part.shape[-1] < n:
        part = F.pad(part, (0, n - part.shape[-1]))
    return {"waveform": part.contiguous(), "sample_rate": sr}


def silence(frames: int, fps: float = 24.0, sr: int = 44100):
    return {"waveform": torch.zeros((1, 2, int(round(frames / fps * sr)))), "sample_rate": sr}


# ---------------------------------------------------------------------------
# SAM3 and H3
# ---------------------------------------------------------------------------

def centre_weight(h: int, w: int) -> torch.Tensor:
    """Weight that peaks at the canvas centre (where the tracked face is)."""
    yy = torch.linspace(-1, 1, h).view(-1, 1)
    xx = torch.linspace(-1, 1, w).view(1, -1)
    return torch.exp(-(xx ** 2 + yy ** 2) / (2 * 0.3 ** 2))


def pick_object(masks) -> int:
    """Index of the object mask [T, H, W] that covers the canvas centre most."""
    best, best_v = 0, -1.0
    for k, m in enumerate(masks):
        v = float((m.to(torch.float32).cpu() * centre_weight(m.shape[-2], m.shape[-1])).sum())
        if v > best_v:
            best, best_v = k, v
    return best


class SAM3Tracker:
    """SAM3 (core) text tracking on crops; load() once, track() per unit, close() to unload."""

    def __init__(self, reg, text: str = MASK_TEXT, threshold: float = SAM3_THRESHOLD):
        self.reg, self.text, self.threshold = reg, (text or MASK_TEXT).strip(), float(threshold)
        self.model = self.clip = self.cond = None
        self.seconds = 0.0

    def load(self):
        if self.model is None:
            self.model, self.clip = call_node(self.reg, "CheckpointLoaderSimple", ckpt_name=SAM3_CKPT)[:2]
            self.cond = call_node(self.reg, "CLIPTextEncode", clip=self.clip, text=self.text)[0]

    def track(self, crops: torch.Tensor) -> torch.Tensor:
        """crops [T, H, W, 3] -> head mask [T, H, W] float (zeros where nothing was found)."""
        t0 = time.time()
        self.load()
        T, H, W = int(crops.shape[0]), int(crops.shape[1]), int(crops.shape[2])
        track = call_node(self.reg, "SAM3_VideoTrack", images=crops, model=self.model, conditioning=self.cond,
                          detection_threshold=self.threshold, max_objects=SAM3_MAX_OBJECTS, detect_interval=1)[0]
        packed = track.get("packed_masks") if isinstance(track, dict) else None
        n_obj = 0 if packed is None else int(packed.shape[1])
        if n_obj == 0:
            out = torch.zeros((T, H, W))
        else:
            per = [call_node(self.reg, "SAM3_TrackToMask", track_data=track, object_indices=str(k))[0]
                   for k in range(n_obj)]
            out = per[pick_object(per)].to("cpu", torch.float32)
            if tuple(out.shape[-2:]) != (H, W):
                out = F.interpolate(out.unsqueeze(1), size=(H, W), mode="bilinear", align_corners=False).squeeze(1)
        self.seconds += time.time() - t0
        return out.clamp(0, 1)

    def close(self):
        if self.model is None:
            return
        try:
            try:
                from . import h3md_vram
            except ImportError:
                import h3md_vram
            patchers = [self.model] + ([self.clip.patcher] if getattr(self.clip, "patcher", None) else [])
            h3md_vram.unload_patchers(patchers, "(SAM3 unloaded)")
            h3md_vram.soft_empty_cache("(SAM3 freed)")
        except Exception as exc:
            LOG.warning("%s SAM3 unload: %s", _TAG, exc)
        self.model = self.clip = self.cond = None


class H3MaskRedraw:
    """Redraw one unit of crops with H3 inside a mask (img2img), the clip's own audio locked."""

    def __init__(self, reg, model, clip, vae, audio_vae, ref_image, prompt: str = "", denoise: float = DENOISE,
                 steps: int = STEPS, seed: int = SEED):
        missing = [n for n, v in (("redraw_model", model), ("redraw_clip", clip), ("redraw_vae", vae),
                                  ("redraw_audio_vae", audio_vae)) if v is None]
        if missing:
            raise ValueError(f"{_TAG} face refine needs {', '.join(missing)} connected to the face refine save node (the same H3 model / text "
                             "encoder / video VAE / audio VAE as the sampler)")
        if ref_image is None:
            raise ValueError(f"{_TAG} face refine needs a face picture: connect redraw_face_image, or an "
                             "identity_reference / Director ID Ref photo to crop the face from")
        self.reg, self.model, self.clip, self.vae, self.audio_vae = reg, model, clip, vae, audio_vae
        self.ref = prepare_reference(ref_image[:1])
        self.prompt = (prompt or "").strip()
        self.denoise, self.steps, self.seed = float(denoise), int(steps), int(seed)
        self._dd = None
        self.seconds, self.units, self.empty_frames = 0.0, 0, 0

    def run(self, crops: torch.Tensor, mask: torch.Tensor, audio, unit_index: int = 0,
            lock_frames: int = 0) -> torch.Tensor:
        """crops float [T, H, W, 3] (T = 5 + 17k, H, W multiples of 32), cleaned mask [T, H, W], audio dict ->
        redrawn crops [T, H, W, 3]."""
        t0 = time.time()
        T, H, W = int(crops.shape[0]), int(crops.shape[1]), int(crops.shape[2])
        if valid_length(T) != T or H % 32 or W % 32:
            raise ValueError(f"{_TAG} a unit must be 5 + 17k frames on a canvas that is a multiple of 32 "
                             f"(got {T} frames, {W}x{H})")
        if self._dd is None:
            self._dd = call_node(self.reg, "DifferentialDiffusion", model=self.model, strength=1.0)[0]
        cond = call_node(self.reg, "MiniMaxH3ReferenceToVideo", clip=self.clip, vae=self.vae,
                         audio_vae=self.audio_vae, ref_images={"ref_image_0": self.ref}, prompt=self.prompt,
                         width=W, height=H, length=T, ref_image_size="match")[0]
        video = call_node(self.reg, "VAEEncode", pixels=crops[..., :3], vae=self.vae)[0]
        lat_t = int(video["samples"].shape[2])
        lmask = to_latent_mask(mask, lat_t, lock_frames=lock_frames)
        video = call_node(self.reg, "SetLatentNoiseMask", samples=video, mask=lmask)[0]
        alat = call_node(self.reg, "VAEEncodeAudio", audio=audio, vae=self.audio_vae)[0]
        lock = call_node(self.reg, "SolidMask", value=0.0, width=32, height=32)[0]
        alat = call_node(self.reg, "SetLatentNoiseMask", samples=alat, mask=lock)[0]
        av = call_node(self.reg, "LTXVConcatAVLatent", video_latent=video, audio_latent=alat)[0]
        sigmas = call_node(self.reg, "BasicScheduler", model=self.model, scheduler=SCHEDULER, steps=self.steps,
                           denoise=self.denoise)[0]
        guider = call_node(self.reg, "BasicGuider", model=self._dd, conditioning=cond)[0]
        sampler = call_node(self.reg, "KSamplerSelect", sampler_name=SAMPLER)[0]
        noise = call_node(self.reg, "RandomNoise", noise_seed=self.seed + int(unit_index))[0]
        out = call_node(self.reg, "SamplerCustomAdvanced", noise=noise, guider=guider, sampler=sampler,
                        sigmas=sigmas, latent_image=av)[0]
        samples = out["samples"]
        if getattr(samples, "is_nested", False):
            samples = samples.unbind()[0]
        img = call_node(self.reg, "VAEDecode", samples={"samples": samples}, vae=self.vae)[0]
        if img.ndim == 5:
            img = img.reshape(-1, *img.shape[-3:])
        img = img[..., :3].to("cpu", torch.float32)
        if img.shape[0] < T:
            raise RuntimeError(f"{_TAG} H3 returned {img.shape[0]} frames for a {T} frame unit")
        img = img[:T]
        if tuple(img.shape[1:3]) != (H, W):
            img = F.interpolate(img.movedim(-1, 1), size=(H, W), mode="bilinear",
                                align_corners=False).movedim(1, -1)
        self.seconds += time.time() - t0
        self.units += 1
        return img.clamp(0, 1)


def face_height(transform, i: int) -> float:
    """Height of the face in source pixels at frame i (crop box / crop_factor)."""
    return float(transform["boxes"][i][3]) / float(transform.get("crop_factor", 2.5) or 2.5)


def shot_spans(total: int, segments=None, unit: int = UNIT_FRAMES, overlap: int = OVERLAP_FRAMES):
    """unit_spans inside every shot (the track's segments between hard cuts): a redraw never runs across a cut.
    Shots shorter than 5 frames get no unit (their frames are only restored)."""
    out = []
    for a, b in (segments or [(0, total)]):
        a, b = max(0, int(a)), min(int(total), int(b))
        out += [(a + s, a + e) for s, e in unit_spans(b - a, unit, overlap)]
    return out


def _is_oom(exc) -> bool:
    return isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())) or "out of memory" in str(exc).lower()


def build(read, crop, transform, todo, tracker, painter, masks: UnitStore, layer: UnitStore, audio=None,
          fps: float = 24.0, unit: int = UNIT_FRAMES, skip_above_px: float = 0.0, check=None, progress=None) -> dict:
    """Pass A: SAM3 head masks for every unit to redraw (then SAM3 is unloaded).  Pass B: H3 redraws each unit.
    read(a, b) -> source frames [b - a, H, W, C]; crop(frame [1, H, W, C], i) -> canvas crop [1, ch, cw, 3]."""
    spans = shot_spans(layer.total, transform.get("segments"), unit, layer.overlap)
    plan, skipped_big, skipped_empty = [], 0, 0
    for (a, b) in spans:
        idx = [i for i in range(a, b) if todo[i]]
        if not idx:
            skipped_empty += 1
        elif skip_above_px > 0 and float(np.median([face_height(transform, i) for i in idx])) > skip_above_px:
            skipped_big += 1
        else:
            plan.append((a, b))

    def crops_of(a, b):
        base = read(a, b)
        out = torch.cat([crop(base[i - a:i - a + 1], i).to(torch.float32) for i in range(a, b)], 0)
        del base
        return out[..., :3]

    steps = 2 * max(1, len(plan))
    done_steps = 0
    try:
        for (a, b) in plan:
            if check is not None:
                check()
            if not masks.has((a, b)):
                masks.put((a, b), tracker.track(crops_of(a, b)))
            done_steps += 1
            if progress is not None:
                progress(done_steps, steps)
    finally:
        tracker.close()
    redrawn = reused = no_head = oom = 0
    for k, (a, b) in enumerate(plan):
        if check is not None:
            check()
        if layer.has((a, b)):
            reused += 1
        else:
            raw = masks.get((a, b))
            no_head += int((raw.flatten(1).amax(1) <= CLEAN_THRESHOLD).sum())
            clean = clean_masks(raw)
            crops, lock = crops_of(a, b), 0
            prev = [sp for sp in layer.spans if sp[0] < a < sp[1]]
            if prev:   # chain like the Loop: the overlap starts from the previous unit's redraw and stays locked
                pa, pb = max(prev, key=lambda sp: sp[1])
                lock = min(pb, b) - a
                crops[:lock] = layer.get((pa, pb))[a - pa:a - pa + lock, ..., :3]
            try:
                layer.put((a, b), painter.run(crops, clean, slice_audio(audio, a, b, fps) or silence(b - a, fps), k,
                                              lock_frames=lock))
                redrawn += 1
            except Exception as exc:   # out of memory: this unit is only restored, the save goes on
                if not _is_oom(exc):
                    raise
                oom += 1
                LOG.warning("%s unit %s ran out of memory: not redrawn (set redraw_unit lower)", _TAG, (a, b))
                del crops
                try:
                    import comfy.model_management as mm
                    mm.soft_empty_cache()
                except Exception:
                    pass
        done_steps += 1
        if progress is not None:
            progress(done_steps, steps)
    rep = {"units": len(spans), "redrawn": redrawn, "reused": reused, "skipped_no_face": skipped_empty,
           "skipped_large_face": skipped_big, "frames_without_head": no_head, "out_of_memory": oom,
           "sam3_s": round(tracker.seconds, 1), "h3_s": round(painter.seconds, 1)}
    LOG.info("%s %s", _TAG, rep)
    return rep
