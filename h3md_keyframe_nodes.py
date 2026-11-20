"""H3MD Keyframe nodes (2026-11-17, H3MD 0.5.0): keyframes + plan package for the multi-person swap (Remake).

  H3MD Keyframe 1: Prepare  - source video + character pictures + "图1换左二" -> who is who, keyframe moments, where
                              every person to recast stands then.
  H3MD Keyframe 1b: Recast  - 0.5.91: SCAIL-2 character replacement (as ComfyUI's official template) on a short
                              piece of the source around every keyframe; the keyframe is its middle frame.
  H3MD Keyframe 2: Assemble - keyframes saved -> H3MD plan + plan zip for the Director's "Import plan"
                              (H3MD Director Remake.json).

Pure logic in h3md_keyframe.py.  People are tracked with ComfyUI's core SAM3.1 (models/checkpoints/
sam3.1_multiplex_fp16.safetensors, the same file the depth / pose control uses)."""
from __future__ import annotations

import gc
import json
import logging
import math
import os

try:
    from . import h3md_keyframe as K
except ImportError:  # flat import (tests)
    import h3md_keyframe as K

LOG = logging.getLogger("H3MD")
CATEGORY = "MiniMax H3/H3MD"
SAM3_CKPT = "sam3.1_multiplex_fp16.safetensors"
SAMPLE_FPS = 6.0          # tracking / moment search rate
TRACK_SHORT = 480         # tracking frames: short side in pixels
WINDOW_S = 5.0            # SAM3 tracking window (consecutive windows share one frame)
PICTURE_MP = 4.0          # character pictures are scaled down to at most this
KF_SUBDIR = "H3MD_keyframes"
PLANS_SUBDIR = "H3MD_plans"
TAG = K.TAG


def _vram():
    try:
        from . import h3md_vram
    except ImportError:
        import h3md_vram
    return h3md_vram


def _plan_io():
    try:
        from . import h3md_plan_io
    except ImportError:
        import h3md_plan_io
    return h3md_plan_io


# ------------------------------------------------------------------------------------------------- video
def resolve_video(video: str) -> str:
    import folder_paths
    v = (video or "").strip().strip('"')
    if not v:
        raise K.KeyframeError(f"{TAG} write the source video's path inside ComfyUI's input folder "
                              "(e.g. my_clips/dance.mp4). 填原片在 input 文件夹里的路径。")
    p = v if os.path.isabs(v) else os.path.join(folder_paths.get_input_directory(), v)
    if not os.path.isfile(p):
        raise K.KeyframeError(f"{TAG} video not found: {p}. 找不到原片。")
    return p


SOURCES_SUBDIR = "_sources"   # input/H3MD_keyframes/_sources: the dragged-in / trimmed source clips


def source_clip(video=None, path=None, start_s=0.0, end_s=0.0, out_dir=None, stem=None):
    """0.5.5: the source the plan uses.  video = a core VIDEO (Load Video node, drag a file in) or None; path = the
    old typed path (used when no video is connected).  start_s / end_s (0 = to the end) cut the part to recast.
    Returns a file path: the original when nothing has to change, else the part (with its sound) written to
    out_dir (input/H3MD_keyframes/_sources)."""
    start_s, end_s = max(0.0, float(start_s or 0.0)), max(0.0, float(end_s or 0.0))
    if end_s and end_s <= start_s:
        raise K.KeyframeError(f"{TAG} end_s ({end_s:g}) must be after start_s ({start_s:g}), or 0 for the end of "
                              "the video. 结束秒要大于起始秒（0 = 到片尾）。")
    src = None
    if video is not None:
        s = video.get_stream_source() if hasattr(video, "get_stream_source") else None
        src = s if isinstance(s, str) and os.path.isfile(s) else None
    elif path:
        src = path
    else:
        raise K.KeyframeError(f"{TAG} connect a Load Video node (drag the source video into it). "
                              "接一个「加载视频」节点，把原片拖进去。")
    trim = start_s > 0 or end_s > 0
    if src is not None and not trim:
        return src
    if video is None:
        from comfy_api.latest import InputImpl
        video = InputImpl.VideoFromFile(src)
    if trim:
        if not hasattr(video, "as_trimmed"):
            raise K.KeyframeError(f"{TAG} cutting a part (start_s / end_s) needs a newer ComfyUI (Trim Video); "
                                  "update ComfyUI. 截取片段需要更新 ComfyUI。")
        part = video.as_trimmed(start_s, (end_s - start_s) if end_s else 0.0)
        if part is None:
            raise K.KeyframeError(f"{TAG} could not cut {start_s:g}-{end_s or 'end'} s from the video "
                                  f"(it is {video.get_duration():.2f} s long). 截取失败，检查起止秒。")
        video = part
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0] if src else (stem or "source")
    tag = f"_{start_s:g}-{end_s:g}s" if trim else ""
    dst = os.path.join(out_dir, f"{base}{tag}.mp4")
    try:
        video.save_to(dst, crf=16)
    except TypeError:            # an older ComfyUI without the crf argument
        video.save_to(dst)
    LOG.info("%s source part %s -> %s", TAG, f"{start_s:g}-{end_s or 'end'} s", dst)
    return dst


def plan_path(video: str, path: str) -> str:
    """the source as the plan names it: relative to input with forward slashes (absolute stays absolute)"""
    import folder_paths
    inp = os.path.abspath(folder_paths.get_input_directory())
    ap = os.path.abspath(path)
    if ap.lower().startswith(inp.lower() + os.sep):
        return os.path.relpath(ap, inp).replace("\\", "/")
    return ap


def probe(path):
    import av
    with av.open(path) as c:
        vs = c.streams.video[0]
        fps = float(vs.average_rate or vs.guessed_rate or 24)
        dur = float(vs.duration * vs.time_base) if vs.duration else (float(c.duration) / 1e6 if c.duration else 0.0)
        n = int(vs.frames or 0) or int(round(dur * fps))
        if not dur:
            dur = n / fps
        return {"fps": fps, "duration": dur, "frames": n, "width": vs.codec_context.width,
                "height": vs.codec_context.height, "audio": len(c.streams.audio) > 0}


def _fit(img, tw, th):
    """PIL image -> centre crop to tw:th and resize to (tw, th)"""
    from PIL import Image
    x0, y0, w, h = K.cover_crop(img.width, img.height, tw, th)
    return img.crop((x0, y0, x0 + w, y0 + h)).resize((tw, th), Image.LANCZOS)


def read_frames(path, times, size):
    """frames (uint8 [h, w, 3], centre-cropped + resized to size) nearest to each time, decoded in one pass"""
    import av
    import numpy as np
    want = sorted(set(times))
    out, best = {}, {}
    with av.open(path) as c:
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        k = 0
        for fr in c.decode(vs):
            if fr.pts is None:
                continue
            t = float(fr.pts * vs.time_base)
            while k < len(want) and want[k] < t - 0.5 / 24:
                k += 1
            if k >= len(want):
                break
            for j in (k - 1, k, k + 1):
                if 0 <= j < len(want):
                    d = abs(want[j] - t)
                    if d < best.get(want[j], 1e9):
                        best[want[j]] = d
                        out[want[j]] = fr.to_image()
            if want and t > want[-1] + 0.5:
                break
    res = {}
    for t in want:
        if t not in out:
            raise K.KeyframeError(f"{TAG} could not read the frame at {t:.2f} s")
        res[t] = np.asarray(_fit(out[t], *size).convert("RGB"))
    return res


def sample_frames(path, duration, size, fps=SAMPLE_FPS):
    """tracking frames: every 1/fps s, small (short side TRACK_SHORT).  -> (times, uint8 [N, h, w, 3])"""
    import numpy as np
    tw, th = size
    s = TRACK_SHORT / min(tw, th)
    small = (int(round(tw * s / 16) * 16), int(round(th * s / 16) * 16))
    n = int(math.floor(duration * fps - 1e-6)) + 1
    times = [round(i / fps, 4) for i in range(n) if i / fps <= duration - 1.0 / 24]
    fr = read_frames(path, times, small)
    return times, np.stack([fr[t] for t in times])



def detect_cuts(path, w=96, h=54):
    """0.5.8: the source's shot cuts (seconds), every decoded frame compared with the one before (PyAV)."""
    import av
    import numpy as np
    times, hd, pd, prev = [], [], [], None
    with av.open(path) as c:
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        for fr in c.decode(vs):
            if fr.pts is None:
                continue
            a = fr.reformat(width=w, height=h, format="rgb24").to_ndarray()
            q = (a // 64).reshape(-1, 3).astype(np.int64)
            hist = np.bincount(q[:, 0] * 16 + q[:, 1] * 4 + q[:, 2], minlength=64).astype(np.float32)
            hist /= max(1.0, float(hist.sum()))
            gray = a.mean(axis=2).astype(np.float32)
            times.append(float(fr.pts * vs.time_base))
            if prev is None:
                hd.append(0.0)
                pd.append(0.0)
            else:
                hd.append(1.0 - float(np.minimum(hist, prev[0]).sum()))
                pd.append(float(np.abs(gray - prev[1]).mean()) / 255.0)
            prev = (hist, gray)
    return K.cuts_from(times, hd, pd)


# ------------------------------------------------------------------------------------------------- SAM3
def load_sam3():
    """ComfyUI's core SAM3.1 (model, its text encoder)"""
    import folder_paths
    import comfy.sd
    path = folder_paths.get_full_path("checkpoints", SAM3_CKPT)
    if not path:
        raise FileNotFoundError(f"{TAG} SAM3 checkpoint '{SAM3_CKPT}' not found in models/checkpoints (ComfyUI's core "
                                "SAM3.1, used to find and follow the people). 缺 SAM3.1 模型。")
    out = comfy.sd.load_checkpoint_guess_config(path, output_vae=False, output_clip=True,
                                                embedding_directory=folder_paths.get_folder_paths("embeddings"))
    return out[0], out[1]


def track_people(frames_u8, prompt="person", threshold=0.4, max_people=8, fps=SAMPLE_FPS, window_s=WINDOW_S):
    """Core SAM3.1 video tracking in windows (consecutive windows share one frame) -> [{local id: box}] per window
    per frame (boxes normalised).  Model + text encoder are unloaded afterwards."""
    import numpy as np
    import torch
    import comfy.model_management as mm
    from comfy_extras.nodes_sam3 import _extract_text_prompts
    from comfy.ldm.sam3.tracker import unpack_masks
    model, clip = load_sam3()
    patchers = [model] + ([clip.patcher] if clip is not None and getattr(clip, "patcher", None) is not None else [])
    n = int(frames_u8.shape[0])
    win = max(3, int(round(window_s * fps)))
    windows = []
    try:
        frames = torch.from_numpy(frames_u8).float() / 255.0
        with torch.inference_mode():
            cond = clip.encode_from_tokens_scheduled(clip.tokenize(prompt))
            mm.load_model_gpu(model)
            dev, dt = mm.get_torch_device(), model.model.get_dtype()
            tp = [(e, m) for e, m, _ in _extract_text_prompts(cond, dev, dt)]
            a = 0
            while a < n - 1 or (a == 0 and n == 1):
                b = min(n, a + win)
                res = model.model.diffusion_model.forward_video(
                    images=frames[a:b, ..., :3].movedim(-1, 1), initial_masks=None, pbar=None, text_prompts=tp,
                    new_det_thresh=float(threshold), max_objects=int(max_people), detect_interval=1,
                    target_device=dev, target_dtype=dt)
                packed = res.get("packed_masks")
                rows = [dict() for _ in range(b - a)]
                if packed is not None:
                    for t in range(min(b - a, packed.shape[0])):
                        m = unpack_masks(packed[t:t + 1])[0].cpu().numpy().astype(bool)
                        H, W = m.shape[-2:]
                        for o in range(m.shape[0]):
                            if m[o].mean() < 0.0008:
                                continue
                            ys, xs = np.nonzero(m[o])
                            rows[t][o] = [xs.min() / W, ys.min() / H, (xs.max() + 1) / W, (ys.max() + 1) / H]
                windows.append(rows)
                LOG.info("%s SAM3 window %.1f-%.1f s: %d people", TAG, a / fps, (b - 1) / fps,
                         len({o for r in rows for o in r}))
                if b >= n:
                    break
                a = b - 1
        return windows
    finally:
        _vram().unload_patchers(patchers, "(SAM3 unloaded)")
        del model, clip, patchers
        gc.collect()
        _vram().soft_empty_cache("(SAM3 freed)")


# ------------------------------------------------------------------------------------------------- images
def to_tensor(arr_u8):
    import numpy as np
    import torch
    return torch.from_numpy(np.ascontiguousarray(arr_u8).astype(np.float32) / 255.0)[None]


def to_u8(t):
    import numpy as np
    a = t.detach().cpu().float().numpy()
    if a.ndim == 4:
        a = a[0]
    return (np.clip(a[..., :3], 0, 1) * 255 + 0.5).astype(np.uint8)


def _font(size):
    from PIL import ImageFont
    for f in ("arial.ttf", "DejaVuSans.ttf", "msyh.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def people_sheet(frame_u8, who, chosen, t):
    """numbering frame with every person's box: L1, L2 ... (left to right); recast people in orange with 'pic N'"""
    from PIL import Image, ImageDraw
    img = Image.fromarray(frame_u8).convert("RGB")
    W, H = img.size
    d = ImageDraw.Draw(img)
    f = _font(max(18, H // 28))
    pic_of = {pid: p for p, _, pid in chosen}
    for k, (pid, b) in enumerate(who, 1):
        col = (255, 140, 0) if pid in pic_of else (80, 200, 255)
        box = [b[0] * W, b[1] * H, b[2] * W, b[3] * H]
        d.rectangle(box, outline=col, width=max(3, W // 400))
        label = f"L{k}" + (f" <- pic {pic_of[pid]}" if pid in pic_of else "")
        tb = d.textbbox((box[0] + 6, box[1] + 6), label, font=f)
        d.rectangle([tb[0] - 4, tb[1] - 3, tb[2] + 4, tb[3] + 3], fill=(0, 0, 0))
        d.text((box[0] + 6, box[1] + 6), label, fill=col, font=f)
    d.text((10, H - max(18, H // 28) - 12), f"{t:.2f} s  numbered left to right", fill=(255, 255, 255),
           font=f, stroke_width=2, stroke_fill=(0, 0, 0))
    import numpy as np
    return np.asarray(img)


def contact_sheet(images, labels, cols=2, width=1664):
    import numpy as np
    from PIL import Image, ImageDraw
    if not images:
        return np.zeros((64, 64, 3), np.uint8)
    h0, w0 = images[0].shape[:2]
    cw = width // cols
    ch = int(round(h0 * cw / w0))
    rows = int(math.ceil(len(images) / cols))
    sheet = Image.new("RGB", (cw * cols, ch * rows), (20, 20, 20))
    f = _font(max(16, ch // 18))
    for i, (im, lab) in enumerate(zip(images, labels)):
        tile = Image.fromarray(im).resize((cw, ch), Image.LANCZOS)
        d = ImageDraw.Draw(tile)
        d.text((8, 6), lab, fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))
        sheet.paste(tile, ((i % cols) * cw, (i // cols) * ch))
    return np.asarray(sheet)


def fit_picture(t, mp=PICTURE_MP):
    """character picture -> one image, at most about `mp` megapixels (multiples of 16)"""
    import comfy.utils
    img = t[:1]
    h, w = int(img.shape[1]), int(img.shape[2])
    if w * h <= mp * 1e6:
        return img[..., :3]
    s = math.sqrt(mp * 1e6 / (w * h))
    nw, nh = max(16, int(w * s) // 16 * 16), max(16, int(h * s) // 16 * 16)
    return comfy.utils.common_upscale(img[..., :3].movedim(-1, 1), nw, nh, "lanczos", "disabled").movedim(1, -1)


def _unique_dir(base, name):
    d, k = os.path.join(base, name), 2
    while os.path.exists(d):
        d = os.path.join(base, f"{name}_{k}")
        k += 1
    return d


# ------------------------------------------------------------------------------------------------- nodes
class H3MDKeyframePrepare:
    """Who is who, which moments, and a crop + edit prompt per recast person per moment."""

    @classmethod
    def INPUT_TYPES(cls):
        pics = {f"picture_{i}": ("IMAGE", {"tooltip": f"Character picture {i} (图{i}): the look this person gets."})
                for i in range(1, 6)}
        return {
            "required": {
                "video": ("STRING", {"default": "", "tooltip":
                                     "Only when no Load Video node is connected to source_video: the source video's "
                                     "path inside ComfyUI's input folder (older workflows). 没接加载视频时才用：原片在 "
                                     "input 里的路径。"}),
                "mapping": ("STRING", {"multiline": True, "default": "图1换左二\n图2换左四", "tooltip":
                                       "Which picture replaces which person; people are numbered left to right in the "
                                       "first clear frame (see people_sheet). '图1换左二' = picture 1 replaces the "
                                       "second from the left; English: 'picture 1 -> left 2'."}),
                "descriptions": ("STRING", {"multiline": True, "default": "", "tooltip":
                                            "One line per picture, in picture order: race, age, face, hair, every "
                                            "garment (e.g. 'a Black man in his twenties, short black hair, red bomber "
                                            "jacket, white T-shirt, black jeans'). Used in the edit prompt and the "
                                            "Remake prompt; English recommended. 每张图一行外貌描述。"}),
                "people_at_start": ("STRING", {"multiline": True, "default": "", "tooltip":
                                    "Optional: one line per person in the source, left to right at the start, what "
                                    "they look like now (e.g. 'the woman with wavy dark-brown hair in a red crop top'). "
                                    "Helps the edit model pick the right person and the Remake prompt keep the others. "
                                    "可选：原片里从左到右每人一行，现在的样子。"}),
                "max_gap_s": ("FLOAT", {"default": 5.0, "min": 2.0, "max": 8.0, "step": 0.5, "tooltip":
                                        "Largest distance between two keyframes (Remake splits long videos into "
                                        "parts; each part needs a keyframe)."}),
                "threshold": ("FLOAT", {"default": 0.4, "min": 0.05, "max": 0.95, "step": 0.05,
                                        "tooltip": "SAM3 detection threshold for 'person'"}),
                "max_people": ("INT", {"default": 8, "min": 1, "max": 20}),
            },
            # 0.5.5 (appended last; old workflows keep the typed path): drag the video into a Load Video node
            "optional": dict(pics, source_video=("VIDEO", {"tooltip":
                             "The source video: connect a Load Video node and drag the file into it. When it is "
                             "connected the 'video' path box is ignored. 原片：接「加载视频」节点，把视频拖进去。"}),
                             start_s=("FLOAT", {"default": 0.0, "min": 0.0, "max": 36000.0, "step": 0.1,
                                                "tooltip": "Recast only from this second on (0 = from the start). "
                                                           "只换这一秒之后的部分。"}),
                             end_s=("FLOAT", {"default": 0.0, "min": 0.0, "max": 36000.0, "step": 0.1,
                                              "tooltip": "...up to this second (0 = to the end). The part is cut with "
                                                         "its sound and becomes the plan's source. 到这一秒（0 = 片尾）。"})),
        }

    RETURN_TYPES = ("IMAGE", "H3MD_KEYFRAME_JOB", "IMAGE", "STRING")
    RETURN_NAMES = ("pictures", "job", "people_sheet", "info")
    OUTPUT_IS_LIST = (True, False, False, False)
    OUTPUT_TOOLTIPS = ("The character picture of every recast person at every keyframe, in order (connect to Recast).",
                       "Everything Recast and Assemble need (connect it there).",
                       "First clear frame with every person numbered left to right: check the numbers match "
                       "your mapping.",
                       "Moments, people, warnings (JSON).")
    FUNCTION = "prepare"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Multi-person swap keyframes, step 1. Finds and numbers the people in the source (left to right "
                   "at the start), picks keyframe moments at most max_gap_s apart (people standing apart, little "
                   "motion) and notes where every person to recast stands then. Then H3MD Keyframe 1b: Recast "
                   "(MiniMax H3 Image character swap on the whole keyframe) and H3MD Keyframe 2: Assemble.")

    def prepare(self, video, mapping, descriptions, people_at_start, max_gap_s, threshold, max_people,
                source_video=None, start_s=0.0, end_s=0.0, **pics):
        import comfy.utils
        mp = K.parse_mapping(mapping)
        for p in mp:
            if pics.get(f"picture_{p}") is None:
                raise K.KeyframeError(f"{TAG} picture {p} is used in the mapping but picture_{p} is not connected. "
                                      f"图{p} 没接。")
        descs = K.parse_descriptions(descriptions, mp.keys())
        looks = {k: s for k, s in enumerate(K.parse_people(people_at_start), 1)}
        import folder_paths
        typed = resolve_video(video) if source_video is None else None
        path = source_clip(source_video, typed, start_s, end_s,
                           os.path.join(folder_paths.get_input_directory(), KF_SUBDIR, SOURCES_SUBDIR))
        info = probe(path)
        size = K.target_size(info["width"], info["height"])
        n24 = int(math.floor(info["duration"] * K.FPS + 1e-6))
        length = K.plan_length(n24)
        dur = length / K.FPS
        LOG.info("%s %s: %dx%d %.2f s -> %dx%d, %d frames", TAG, os.path.basename(path), info["width"],
                 info["height"], info["duration"], size[0], size[1], length)
        times, small = sample_frames(path, dur, size)
        K.FRAME_ASPECT = size[0] / float(size[1])
        windows = track_people(small, threshold=threshold, max_people=max_people)
        per_frame = K.link_windows(windows)[:len(times)]
        # 0.5.9: join people the tracker split (blackout, cut, spin, out and back in) - elimination, then hair /
        # clothes colour.  Before, the person a picture is mapped to was "lost" and later keyframes missed her.
        try:
            track_looks = K.appearance(small, per_frame)
            per_frame, reid_joins, reid_unsure = K.reid_merge(per_frame, track_looks, times)
        except Exception as exc:
            reid_joins, reid_unsure = [], []
            LOG.warning("%s re-identification failed (%s): tracks used as they are", TAG, exc)
        try:   # 0.5.9 diagnostics: every track's time span, and why a new track was or was not joined
            spans = {}
            for t_, fr in zip(times, per_frame):
                for q in fr:
                    a = spans.setdefault(q, [t_, t_, 0])
                    a[1], a[2] = t_, a[2] + 1
            LOG.info("%s tracks after re-identification: %s", TAG,
                     "; ".join(f"{q}: {a[0]:.1f}-{a[1]:.1f} s ({a[2]} frames)" for q, a in sorted(spans.items())))
            for n_ in getattr(K.reid_merge, "notes", []):
                LOG.info("%s re-id %s", TAG, n_)
        except Exception:
            pass
        for j in reid_joins:
            LOG.info("%s person found again at %.1f s (track %s = track %s, %s, look %.2f)", TAG, j["at"], j["new"],
                     j["into"], j["why"], j["look"])
        frames = K.report(per_frame, times)
        nf, order = K.number_people(frames)
        chosen = K.resolve(mp, order)
        ids = {pid for _, _, pid in chosen}
        moments = K.pick_moments(frames, ids, dur, max_gap_s)
        try:   # 0.5.8: one keyframe in every shot
            cuts = detect_cuts(path)
        except Exception as exc:
            cuts = []
            LOG.warning("%s cut detection failed (%s): keyframes by max_gap_s only", TAG, exc)
        n_before = len(moments)
        moments = K.ensure_per_shot(frames, moments, ids, cuts, dur)
        LOG.info("%s shot cuts at %s s: %d keyframe(s) added so every shot has one", TAG, cuts,
                 len(moments) - n_before)
        moments, full_notes = K.prefer_full(frames, moments, ids, cuts, dur, max_gap_s)
        moments, fin_notes = K.finish_moments(frames, moments, ids, dur, max_gap_s, cuts=cuts)
        fin_notes = full_notes + fin_notes
        for n_ in fin_notes:
            LOG.info("%s %s", TAG, n_)
        LOG.info("%s keyframes at %s s (close-ups skipped)", TAG, [round(frames[i]["at"], 2) for i, _ in moments])
        try:   # 0.5.9: tracking data next to the sources, so the moment rules can be checked again without a run
            import json as _json
            dbg = os.path.join(folder_paths.get_input_directory(), KF_SUBDIR, SOURCES_SUBDIR,
                               os.path.splitext(os.path.basename(path))[0] + "_tracks.json")
            with open(dbg, "w", encoding="utf-8") as fh:
                _json.dump({"frames": frames, "cuts": cuts, "chosen": sorted(ids), "duration": dur,
                            "aspect": K.FRAME_ASPECT, "max_gap": max_gap_s}, fh)
        except Exception:
            pass
        changes = K.place_changes(frames, order)
        warnings = []
        for u in reid_unsure:
            warnings.append(f"at {u['at']:.1f} s a person came back (after a cut, a blackout or a turn) who could be "
                            f"any of tracks {[c[0] for c in u['candidates']]} (look {[c[1] for c in u['candidates']]}): "
                            "not joined. Move start_s / end_s past this moment if someone is then missed. "
                            "这里有人重新出现但认不出是谁，没有接上。")
        seen_last = {pid: max([f["at"] for f in frames if any(w[0] == pid for w in f["who"])] or [0.0])
                     for pid in ids}
        for p, k, pid in chosen:
            if seen_last[pid] < dur - max_gap_s:
                warnings.append(f"picture {p} (left {k}) is followed only up to {seen_last[pid]:.1f} s: after that "
                                "the tracker lost them (left the frame or hidden?). Later keyframes miss this person.")
        sheet = people_sheet(small[frames.index(nf)], K.visible(nf), chosen, nf["at"])
        full = read_frames(path, [frames[i]["at"] for i, _ in moments], size)
        W, H = size
        pictures, job_moments = [], []
        pic_t = {p: fit_picture(pics[f"picture_{p}"]) for p in mp}
        pbar = comfy.utils.ProgressBar(len(moments))
        for i, kind in moments:
            f = frames[i]
            t = f["at"]
            vis = K.visible(f)
            allb = K.visible(f, relaxed=True)
            items = []
            for p, k, pid in chosen:
                own = next((b for q, b in vis if q == pid), None)
                if own is None:
                    continue
                ob = [own[0] * W, own[1] * H, own[2] * W, own[3] * H]
                others = [[b[0] * W, b[1] * H, b[2] * W, b[3] * H] for q, b in allb if q != pid]
                rank, n_here = K.frame_rank(ob, others)
                pictures.append(pic_t[p])
                # 0.5.91: where she stands in THIS keyframe + what she looked like at the start (the prompt names
                # her by both), and her own / the others' boxes for the automatic checks
                items.append({"picture": p, "left": k, "id": pid, "own": ob, "others": others,
                              "others_ids": [q for q, b in allb if q != pid], "rank": rank, "n": n_here,
                              "desc": descs[p], "look": looks.get(k, ""), "cut_bottom": bool(K.cut_at_bottom(own))})
            job_moments.append({"at": t, "kind": "swap" if items else "plain", "items": items,
                                "boxes": [list(b) for q, b in allb]})
            pbar.update(1)
        names = {pid: (None if pid in ids else (looks.get(n) or
                                                f"the unchanged performer who starts {K.ordinal(n)} from the left"))
                 for n, pid in enumerate(order, 1)}
        if looks and len(looks) != len(order):
            warnings.append(f"people_at_start has {len(looks)} line(s) but {len(order)} people were found at the start; "
                            "check the people sheet.")
        prompt = K.swap_prompt(length, chosen, descs, len(order), changes, names, looks)
        job = {"video": plan_path(video, path), "video_abs": path, "duration": info["duration"],
               "has_audio": info["audio"], "size": list(size), "length": length, "moments": job_moments,
               "frames": full, "prompt": prompt, "chosen": chosen, "order": order}
        if float(start_s or 0.0) > 0 or float(end_s or 0.0) > 0:
            # 0.5.6: where the cut part sits in the original, so H3MD RH can cut the same part from a whole original
            st = round(max(0.0, float(start_s or 0.0)), 3)
            job["source_trim"] = {"start": st, "end": round(st + float(info["duration"]), 3)}
        text = {"video": job["video"], "size": list(size), "frames": length, "seconds": round(dur, 2),
                "people_at_start": len(order), "numbered_at": nf["at"],
                "mapping": [{"picture": p, "left": k} for p, k, _ in chosen],
                "keyframes": [{"at": m["at"], "recast": [it["picture"] for it in m["items"]]} for m in job_moments],
                "place_changes": [{"from": a, "to": b} for a, b, pr in changes if pr],
                "cuts": cuts,
                "recasts": len(pictures), "warnings": warnings, "reid": reid_joins}
        info_s = json.dumps(text, ensure_ascii=False, indent=1)
        for w in warnings:
            LOG.warning("%s %s", TAG, w)
        LOG.info("%s %d keyframes, %d people to recast", TAG, len(job_moments), len(pictures))
        if not pictures:
            raise K.KeyframeError(f"{TAG} none of the chosen people was found at any keyframe. 一个要换的人都没找到。")
        return {"ui": {"text": [info_s]},
                "result": (pictures, job, to_tensor(sheet), info_s)}


class H3MDKeyframeAssemble:
    """Recast keyframes -> saved keyframes -> H3MD plan + plan zip."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "job": ("H3MD_KEYFRAME_JOB", {"tooltip": "From H3MD Keyframe 1b: Recast (the job with the keyframes)"}),
                "name": ("STRING", {"default": "multiswap", "tooltip": "Plan name: keyframes go to input/"
                                    "H3MD_keyframes/<name>/, the zip to output/H3MD_plans/<name>.zip"}),
                "save_zip": ("BOOLEAN", {"default": True, "tooltip": "Also write the plan zip (source video + "
                                         "keyframes + plan) for the Director's Import plan / RunningHub."}),
            }
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("keyframes_sheet", "plan_json", "zip_path")
    OUTPUT_TOOLTIPS = ("All keyframes with their times: check them before running the Director.",
                       "The H3MD plan (paste it into the Director's plan box, or import the zip).",
                       "Where the plan zip was written.")
    OUTPUT_NODE = True
    FUNCTION = "assemble"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Multi-person swap keyframes, step 2. Saves Recast's keyframes and writes the Remake plan + plan "
                   "zip. Then: Director workflow 'H3MD Director Remake.json' -> Import plan.")

    def assemble(self, job, name, save_zip):
        import folder_paths
        from PIL import Image
        if not job.get("recast_frames"):
            raise K.KeyframeError(f"{TAG} no keyframes in the job: connect H3MD Keyframe 1b: Recast's job output. "
                                  "请接 Recast 的 job 输出。")
        name = _plan_io().safe_name(name or "multiswap", "multiswap")
        W, H = job["size"]
        inp = folder_paths.get_input_directory()
        kdir = _unique_dir(os.path.join(inp, KF_SUBDIR), name)
        os.makedirs(kdir, exist_ok=True)
        rel = os.path.relpath(kdir, inp).replace("\\", "/")
        kfs, imgs, labels = [], [], []
        for mi, m in enumerate(job["moments"]):
            out = job["recast_frames"].get(m["at"], job["frames"][m["at"]])
            fr = int(round(m["at"] * K.FPS))
            fn = f"kf_{fr:04d}.png"
            Image.fromarray(out).save(os.path.join(kdir, fn))
            kfs.append((f"kf{mi + 1}", f"{rel}/{fn}", m["at"]))
            imgs.append(out)
            label = f"{m['at']:.2f} s  " + ("pic " + ", ".join(str(i["picture"]) for i in m["items"])
                                             if m["items"] else "source frame")
            labels.append(label)
        plan = K.build_plan(job["video"], job["duration"], job["has_audio"], (W, H), job["length"], kfs,
                            job["prompt"], name)
        if job.get("source_trim"):
            plan["sourceTrim"] = dict(job["source_trim"])
        plan_s = json.dumps(plan, ensure_ascii=False, indent=2)
        with open(os.path.join(kdir, "plan.json"), "w", encoding="utf-8") as fh:
            fh.write(plan_s)
        for at, cm in (job.get("recast_colours") or {}).items():   # 0.5.91: who got which SCAIL-2 colour
            Image.fromarray(cm).save(os.path.join(kdir, f"colours_{int(round(at * K.FPS)):04d}.png"))
        report = (job.get("recast") or {}).get("report")
        if report:
            with open(os.path.join(kdir, "recast_report.txt"), "w", encoding="utf-8") as fh:
                fh.write(report)
        zip_path = ""
        if save_zip:
            data, man = _plan_io().export_plan(plan, None, inp, with_media=True, include_big_videos=True, name=name)
            missing = [e["file"] for e in man["media"] if not e.get("included")]
            if missing:
                LOG.warning("%s not packed: %s", TAG, missing)
            odir = os.path.join(folder_paths.get_output_directory(), PLANS_SUBDIR)
            os.makedirs(odir, exist_ok=True)
            zip_path, k = os.path.join(odir, f"{name}.zip"), 2
            while os.path.exists(zip_path):
                zip_path = os.path.join(odir, f"{name}_{k}.zip")
                k += 1
            with open(zip_path, "wb") as fh:
                fh.write(data)
            LOG.info("%s plan zip: %s (%.1f MB)", TAG, zip_path, len(data) / 1e6)
        sheet = contact_sheet(imgs, labels)
        msg = f"{len(kfs)} keyframes -> input/{rel}/" + (f"\nplan zip: {zip_path}" if zip_path else "")
        return {"ui": {"text": [msg]}, "result": (to_tensor(sheet), plan_s, zip_path)}


def _boxes_at(track, t):
    """{object index: normalised box} of a SAM3 track at frame t"""
    import numpy as np
    from comfy.ldm.sam3.tracker import unpack_masks
    packed = track.get("packed_masks")
    if packed is None or packed.shape[1] == 0:
        return {}, None
    m = unpack_masks(packed[t:t + 1])[0].cpu().numpy().astype(bool)
    H, W = m.shape[-2:]
    out = {}
    for o in range(m.shape[0]):
        if m[o].mean() < 0.0005:
            continue
        ys, xs = np.nonzero(m[o])
        out[o] = [xs.min() / W, ys.min() / H, (xs.max() + 1) / W, (ys.max() + 1) / H]
    return out, m


def _detect(sam, image_t, cond, threshold):
    """SAM3 single-image detection -> [(box xyxy px, bool mask)] (ComfyUI's SAM3_Detect, one mask per object)"""
    from comfy_extras.nodes_sam3 import SAM3_Detect
    masks, boxes = SAM3_Detect.execute(model=sam, image=image_t, conditioning=cond, threshold=threshold,
                                       refine_iterations=2, individual_masks=True).result
    bx = boxes[0] if boxes else []
    out = []
    for i, d in enumerate(bx):
        m = masks[i].cpu().numpy() > 0.5 if i < masks.shape[0] else None
        out.append(([d["x"], d["y"], d["x"] + d["width"], d["y"] + d["height"]], m))
    return out


def cut_character(sam, picture, cond_person, cond_head, threshold):
    """the character in a picture: (rgb uint8, figure mask, figure box, head box or None)"""
    import numpy as np
    rgb = to_u8(picture)
    found = _detect(sam, picture[..., :3], cond_person, threshold)
    if found:
        box, m = max(found, key=lambda bm: (bm[0][2] - bm[0][0]) * (bm[0][3] - bm[0][1]))
        if m is None or not m.any():
            m = np.zeros(rgb.shape[:2], bool)
            m[int(box[1]):int(box[3]), int(box[0]):int(box[2])] = True
    else:                                       # nothing found: the whole picture is the character
        h, w = rgb.shape[:2]
        box, m = [0.0, 0.0, float(w), float(h)], np.ones((h, w), bool)
    head = K.pick_head(box, [b for b, _ in _detect(sam, picture[..., :3], cond_head, threshold)])
    return rgb, m, box, head


def aligned_reference(chars, targets, w, h):
    """0.5.91: SCAIL-2's reference picture, aligned with the keyframe: every character pasted where the person she
    replaces stands, head on head at the same size; the nearer person (lower box bottom) last.  chars / targets:
    lists in colour order; targets = (figure box px, head box px or None).  -> (rgb uint8 [h, w, 3], colour mask
    float [h, w, 3] on black, notes)"""
    import numpy as np
    from PIL import Image
    from comfy_extras.nodes_scail import DEFAULT_PALETTE
    rgb = np.zeros((h, w, 3), np.uint8)
    cm = np.zeros((h, w, 3), np.float32)
    notes = []
    order = sorted(range(len(chars)), key=lambda k: targets[k][0][3])
    for k in order:
        c_rgb, c_m, c_box, c_head = chars[k]
        t_box, t_head = targets[k]
        sc, dx, dy, how = K.place_character(t_box, c_box, t_head, c_head)
        notes.append(how)
        aff = (1.0 / sc, 0.0, -dx / sc, 0.0, 1.0 / sc, -dy / sc)
        im = np.asarray(Image.fromarray(c_rgb).transform((w, h), Image.AFFINE, aff, resample=Image.BILINEAR))
        mk = np.asarray(Image.fromarray(c_m.astype(np.uint8) * 255).transform((w, h), Image.AFFINE, aff,
                                                                              resample=Image.BILINEAR)) > 127
        rgb[mk] = im[mk]
        cm[mk] = DEFAULT_PALETTE[k % len(DEFAULT_PALETTE)]
    return rgb, cm, [notes[order.index(k)] for k in range(len(chars))]


class H3MDKeyframeRecast:
    """0.5.91: SCAIL-2 character replacement, wired as ComfyUI's official template 'Character Replacement (SCAIL-2
    Int8)': the SCAIL-2 model + DPO LoRA + lightx2v distill LoRA, shift 5, 6 steps, CFG 1, replacement mode.  For
    every keyframe it is driven by SCAIL_LEN source frames around it; SAM3 tracks them and the recast people are
    painted in SCAIL-2's trained colours (picture order); the reference picture is aligned with the keyframe: each
    character cut out of her picture and put where the person she replaces stands, head on head at the same size,
    in the same colour (test 15:35: characters side by side were drawn into wide shots as extra giant people and
    swapped faces when the people stood in another order).  The keyframe is the generated frame at the keyframe's place.  Everybody not recast and the
    background come from the source.  H3 Image (tests 2026-11-20 13:58 / 14:24 / 14:42: reposed, rescaled or
    reframed the people even with masks) is gone."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "job": ("H3MD_KEYFRAME_JOB", {"tooltip": "From H3MD Keyframe 1: Prepare"}),
                "pictures": ("IMAGE", {"tooltip": "Prepare's pictures output"}),
                "model": ("MODEL", {"tooltip": "wan2.1_14B_SCAIL_2 + wan2.1_SCAIL_2_DPO_lora (1.0) + lightx2v I2V "
                                    "distill LoRA (0.8) + ModelSamplingSD3 shift 5 (the official template)"}),
                "clip": ("CLIP", {"tooltip": "umt5_xxl (type wan)"}),
                "clip_vision": ("CLIP_VISION", {"tooltip": "clip_vision_h"}),
                "vae": ("VAE", {"tooltip": "Wan 2.1 VAE"}),
                "sampler": ("SAMPLER", {"tooltip": "euler"}),
                "sigmas": ("SIGMAS", {"tooltip": "BasicScheduler simple, 6 steps (the official template)"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            }
        }

    INPUT_IS_LIST = True
    RETURN_TYPES = ("IMAGE", "H3MD_KEYFRAME_JOB", "STRING", "IMAGE")
    RETURN_NAMES = ("keyframes", "job", "report", "masks")
    OUTPUT_IS_LIST = (True, False, False, True)
    OUTPUT_TOOLTIPS = ("The finished keyframes, one per swap moment (whole frames).",
                       "The job with the keyframes (connect to Assemble).",
                       "Prompt, who was found and time of every keyframe.",
                       "Per keyframe: the coloured people mask and the reference picture with its colours.")
    FUNCTION = "recast"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Multi-person swap keyframes, step 1b (0.5.91): SCAIL-2 character replacement on a short piece "
                   "of the source around every keyframe, all people at once. Then H3MD Keyframe 2: Assemble.")

    THRESHOLD = 0.5          # SAM3 as the user's SCAIL-2 workflow: detection threshold 0.5, detect every 2nd frame
    DETECT_INTERVAL = 2

    def recast(self, job, pictures, model, clip, clip_vision, vae, sampler, sigmas, seed):
        import time
        import numpy as np
        import torch
        import comfy.utils
        import nodes
        from PIL import Image
        from comfy_extras.nodes_sam3 import SAM3_VideoTrack
        from comfy_extras.nodes_scail import WanSCAILToVideo, _subset_track_data, _render_colored_masks
        from comfy_extras.nodes_custom_sampler import SamplerCustom
        one = lambda v: v[0] if isinstance(v, list) else v
        job, model, clip, clip_vision, vae, sampler, sigmas = (one(job), one(model), one(clip), one(clip_vision),
                                                               one(vae), one(sampler), one(sigmas))
        seed = int(one(seed))
        pics = [p[b:b + 1] for p in pictures for b in range(int(p.shape[0]))]
        need = sum(len(m["items"]) for m in job["moments"])
        if len(pics) != need:
            raise K.KeyframeError(f"{TAG} {len(pics)} picture(s) for {need} recast person(s): connect Prepare's "
                                  "pictures. 请接上 Prepare 的 pictures。")
        W, H = job["size"]
        gw, gh = K.scail_size(W, H)
        steps = max(1, int(len(sigmas)) - 1)
        todo = [m for m in job["moments"] if m["items"]]
        for m in todo:
            if len(m["items"]) > K.SCAIL_COLOURS:
                raise K.KeyframeError(f"{TAG} {len(m['items'])} people to recast at {m['at']:.2f} s: SCAIL-2 tells "
                                      f"at most {K.SCAIL_COLOURS} apart. 一帧最多换 {K.SCAIL_COLOURS} 个人。")
        pbar = comfy.utils.ProgressBar(2 * max(1, len(todo)))
        lines, prep, n = [], [], 0
        t_all = time.time()
        # 1) SAM3 (as the template: SAM3 video track on the driving frames and on the reference picture)
        sam, sclip = load_sam3()
        patchers = [sam] + ([sclip.patcher] if getattr(sclip, "patcher", None) is not None else [])
        try:
            cond = nodes.CLIPTextEncode().encode(sclip, "person")[0]
            cond_head = nodes.CLIPTextEncode().encode(sclip, "head")[0]
            cut_cache = {}
            for m in job["moments"]:
                if not m["items"]:
                    continue
                refs = pics[n:n + len(m["items"])]
                n += len(m["items"])
                times, ki = K.window_times(m["at"], job["duration"])
                fr = read_frames(job["video_abs"], times, (gw, gh))
                drive = torch.from_numpy(np.stack([fr[t] for t in times]).astype(np.float32) / 255.0)
                track = SAM3_VideoTrack.execute(images=drive, model=sam, conditioning=cond,
                                                detection_threshold=self.THRESHOLD, max_objects=8,
                                                detect_interval=self.DETECT_INTERVAL).result[0]
                objs, _ = _boxes_at(track, ki)
                people = [[it["own"][0] / W, it["own"][1] / H, it["own"][2] / W, it["own"][3] / H]
                          for it in m["items"]]
                got = K.match_people(objs, people)
                keep = [j for j in range(len(refs)) if got[j] is not None]
                notes = [f"picture {m['items'][j]['picture']} (left {m['items'][j]['left']}): not found in the "
                         "piece around the keyframe - left as in the source" for j in range(len(refs)) if j not in keep]
                if keep:
                    heads = [b_ for b_, _ in _detect(sam, drive[ki:ki + 1], cond_head, self.THRESHOLD)]
                    chars, targets = [], []
                    for j in keep:
                        p_ = m["items"][j]["picture"]
                        if p_ not in cut_cache:
                            cut_cache[p_] = cut_character(sam, refs[j], cond, cond_head, self.THRESHOLD)
                        chars.append(cut_cache[p_])
                        ob = objs[got[j]]
                        tb = [ob[0] * gw, ob[1] * gh, ob[2] * gw, ob[3] * gh]
                        targets.append((tb, K.pick_head(tb, heads)))
                    ref_rgb, ref_cm, how = aligned_reference(chars, targets, gw, gh)
                    notes += [f"picture {m['items'][j]['picture']}: placed {how[k]} on {how[k]}"
                              + ("" if how[k] == "head" else " (no head found - whole figure on the person's box)")
                              for k, j in enumerate(keep)]
                    pose_mask = _render_colored_masks(_subset_track_data(track, [got[j] for j in keep]), "white")
                    ref_mask = torch.from_numpy(ref_cm)[None]
                    ref_img = torch.from_numpy(ref_rgb.astype(np.float32) / 255.0)[None]
                    prompt = K.scail_prompt([{"rank": m["items"][j]["rank"], "desc": m["items"][j].get("desc", "")}
                                             for j in keep])
                else:
                    pose_mask = ref_mask = ref_img = prompt = None
                prep.append(dict(m=m, drive=drive, ki=ki, times=times, pose_mask=pose_mask, ref_mask=ref_mask,
                                 ref_img=ref_img, prompt=prompt, notes=notes, found=len(objs)))
                pbar.update(1)
        finally:
            _vram().unload_patchers(patchers, "(SAM3 unloaded)")
            del sam, sclip, patchers
            gc.collect()
            _vram().soft_empty_cache("(SAM3 freed)")
        # 2) SCAIL-2
        done, out_imgs, out_masks, colours = {}, [], [], {}
        neg0 = nodes.CLIPTextEncode().encode(clip, "")[0]
        for p in prep:
            m = p["m"]
            lines.append(f"{m['at']:.2f} s ({gw}x{gh}, {len(p['times'])} frames {p['times'][0]:.2f}-"
                         f"{p['times'][-1]:.2f} s, keyframe = frame {p['ki'] + 1}, {steps} steps, "
                         f"{len(m['items'])} to recast, {p['found']} people tracked):")
            for t_ in p["notes"]:
                lines.append("  " + t_)
            if p["prompt"] is None:
                out = job["frames"][m["at"]]
                lines.append("  nobody recast: the source frame is kept")
            else:
                t1 = time.time()
                lines.append("  prompt: " + p["prompt"])
                pos = nodes.CLIPTextEncode().encode(clip, p["prompt"])[0]
                cvo = nodes.CLIPVisionEncode().encode(clip_vision, p["ref_img"], "none")[0]
                res = WanSCAILToVideo.execute(positive=pos, negative=neg0, vae=vae, width=gw, height=gh,
                                              length=len(p["times"]), batch_size=1, pose_strength=1.0, pose_start=0.0,
                                              pose_end=1.0, video_frame_offset=0, previous_frame_count=5,
                                              replacement_mode=True, reference_image=p["ref_img"],
                                              clip_vision_output=cvo, pose_video=p["drive"],
                                              pose_video_mask=p["pose_mask"], reference_image_mask=p["ref_mask"])
                ppos, pneg, lat = res.result[0], res.result[1], res.result[2]
                smp = SamplerCustom.execute(model=model, add_noise=True, noise_seed=seed, cfg=1.0, positive=ppos,
                                            negative=pneg, sampler=sampler, sigmas=sigmas, latent_image=lat)
                vid = nodes.VAEDecode().decode(vae, smp.result[0])[0]
                vid = vid.reshape(-1, vid.shape[-3], vid.shape[-2], vid.shape[-1]).clamp(0, 1)
                out = to_u8(vid[min(p["ki"], vid.shape[0] - 1):][:1])
                if out.shape[1] != W or out.shape[0] != H:
                    out = np.asarray(Image.fromarray(out).resize((W, H), Image.LANCZOS))
                lines.append(f"  SCAIL-2 {time.time() - t1:.1f} s, seed {seed}")
                pm = to_u8(p["pose_mask"][p["ki"]:p["ki"] + 1].clamp(0, 1))
                rm = to_u8(p["ref_mask"][:1].clamp(0, 1))
                ri = to_u8(p["ref_img"][:1])
                cmap = np.concatenate([pm, (ri * 0.5 + rm * 0.5).astype(np.uint8)], axis=1)
                colours[m["at"]] = cmap
                out_masks.append(to_tensor(cmap))
            done[m["at"]] = out
            out_imgs.append(to_tensor(out))
            pbar.update(1)
        lines.append(f"total {time.time() - t_all:.1f} s for {len(prep)} keyframe(s)")
        report = "\n".join(lines)
        LOG.info("%s recast report:\n%s", TAG, report)
        job = dict(job, recast_frames=done, recast_colours=colours,
                   recast={"method": "SCAIL-2 character replacement", "seed": seed,
                                                    "steps": steps, "report": report})
        return {"ui": {"text": [report]}, "result": (out_imgs, job, report, out_masks)}


NODE_CLASS_MAPPINGS = {"H3MDKeyframePrepare": H3MDKeyframePrepare, "H3MDKeyframeRecast": H3MDKeyframeRecast,
                       "H3MDKeyframeAssemble": H3MDKeyframeAssemble}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDKeyframePrepare": "H3MD Keyframe 1: Prepare 准备关键帧 0.5.91",
                              "H3MDKeyframeRecast": "H3MD Keyframe 1b: Recast SCAIL-2 换人 0.5.91",
                              "H3MDKeyframeAssemble": "H3MD Keyframe 2: Assemble 打包计划 0.5.91"}
