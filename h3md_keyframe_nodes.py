"""H3MD Keyframe nodes (2026-11-17, H3MD 0.5.0): keyframes + plan package for the multi-person swap (Remake).

  H3MD Keyframe 1: Prepare  - source video + character pictures + "图1换左二" -> who is who, keyframe moments, where
                              every person to recast stands then.
  H3MD Keyframe 1b: Recast  - 0.5.91: in layers, one person at a time, with Qwen Image 2.1: (1) erase the people
                              to recast -> clean background, (2) make every new character alone in her pose,
                              (3) put them back far to near, face on face.
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
                                    "One line per person in the source, left to right at the start: a name, then what "
                                    "they look like (hair, every garment, shoes), e.g. 'Eva: long wavy blonde hair, light "
                                    "blue silk dress, long light blue gloves'. Recast's prompt enhancer finds every "
                                    "person by this look in every keyframe, wherever they stand. Empty: the enhancer "
                                    "describes the people itself. 原片里从左到右每人一行：名字：外貌（发型、全身衣服、鞋），"
                                    "例如「伊娃：金色长卷发，浅蓝丝绸长裙，浅蓝长手套」。"}),
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
                   "(who is who by the prompt enhancer, one Qwen Image 2.1 edit per keyframe) and H3MD Keyframe 2: "
                   "Assemble.")

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
               "frames": full, "prompt": prompt, "chosen": chosen, "order": order,
               "roster": people_at_start or ""}
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


def _qwen_edit(model, clip, vae, sampler, sigmas, seed, prompt, images):
    """one Qwen Image 2.1 edit (as the user's 'Qwen2.1 image editor' chain): images = [tensor, ...] -> uint8 image on
    image 1's canvas"""
    import nodes
    from comfy_extras.nodes_qwen import TextEncodeQwenImage21
    from comfy_extras.nodes_custom_sampler import SamplerCustom
    enc = TextEncodeQwenImage21.execute(clip=clip, prompt=prompt, negative_prompt="", vae=vae, resolution=0,
                                        images={f"image_{i + 1}": im for i, im in enumerate(images)})
    out = SamplerCustom.execute(model=model, add_noise=True, noise_seed=seed, cfg=1.0, positive=enc.result[0],
                                negative=enc.result[1], sampler=sampler, sigmas=sigmas, latent_image=enc.result[2])
    img = nodes.VAEDecode().decode(vae, out.result[0])[0]
    img = img.reshape(-1, img.shape[-3], img.shape[-2], img.shape[-1])[:1].clamp(0, 1)
    return to_u8(img)


def _resized(u8, w, h):
    """uint8 image -> float tensor [1, h, w, 3]"""
    import numpy as np
    from PIL import Image
    return to_tensor(np.asarray(Image.fromarray(u8).resize((w, h), Image.LANCZOS)))


def _enhance(pe_model, request, image_t, seed, unload):
    """one call of the Qwen Image 2.1 prompt enhancer (ComfyUI-Prompt-Enhancer's node, the user's PE loader settings),
    text only: the answer's prompt text.  unload=False keeps the enhancer loaded for the next keyframe."""
    import nodes
    cls = nodes.NODE_CLASS_MAPPINGS.get("QwenImage21PromptEnhancer")
    if cls is None:
        raise K.KeyframeError(f"{TAG} the Qwen Image 2.1 Prompt Enhancer node is not installed "
                              "(ComfyUI-Prompt-Enhancer). 没装 Qwen Image 2.1 提示词增强节点。")
    out = cls().enhance(**{"Prompt": request, "Task": "Image Edit (edit)", "Seed": int(seed) & 0xFFFFFFFF,
                           "Use Cache": True, "Target Megapixels": 1.0, "Aspect Ratio": "Auto (model decides)",
                           "Prompt Count": 1, "Output Language": "Chinese (zh)", "Prompt Enhancement": True,
                           "pe_model": dict(pe_model, unload=bool(unload)), "Image 1": image_t})
    v = out[0]
    return str(v[0] if isinstance(v, (list, tuple)) else v)


def _places(it):
    """[(person id, place from the left)] of everybody in a keyframe, from one item's own / others boxes"""
    allp = [(it["id"], it["own"])] + list(zip(it.get("others_ids") or [], it.get("others") or []))
    allp.sort(key=lambda qb: (qb[1][0] + qb[1][2]) / 2.0)
    return [(q, r) for r, (q, b) in enumerate(allp, 1)]


class H3MDKeyframeRecast:
    """0.5.91: per keyframe, (1) the Qwen Image 2.1 prompt enhancer tells who is who - by look, against the names and
    looks of people_at_start, wherever they stand now - and how each one stands; it is checked against the tracker's
    places; (2) WE write the edit prompt: the person at place r (name, look) becomes picture N, whole outfit changed
    (user 2026-11-20 17:12: the enhancer, left to write the prompt itself, swapped two pictures once and kept the
    original clothes in 3 of 5 keyframes); (3) one Qwen Image 2.1 edit redraws the whole keyframe.
    The layered method (erase / make alone / put back, 817837a) is gone."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "job": ("H3MD_KEYFRAME_JOB", {"tooltip": "From H3MD Keyframe 1: Prepare"}),
                "pictures": ("IMAGE", {"tooltip": "Prepare's pictures output"}),
                "model": ("MODEL", {"tooltip": "Qwen Image 2.1 (+ the 2-step LoRA etc., as the 'Qwen2.1 image "
                                    "editor' workflow)"}),
                "clip": ("CLIP", {"tooltip": "The Qwen Image 2.1 text encoder (qwen3-vl, type qwen_image)"}),
                "vae": ("VAE", {"tooltip": "qwen_image_2.1_vae"}),
                "sampler": ("SAMPLER", {"tooltip": "euler"}),
                "sigmas": ("SIGMAS", {"tooltip": "ManualSigmas 1.0, 0.5, 0 (2-step LoRA)"}),
                "pe_model": ("QWE_PE_MODEL", {"tooltip": "Qwen Image 2.1 PE Loader (GGUF): it only tells who is "
                                              "who in every keyframe. Thinking on, Plan Tokens 400. "
                                              "增强器只负责认人。"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            }
        }

    INPUT_IS_LIST = True
    RETURN_TYPES = ("IMAGE", "H3MD_KEYFRAME_JOB", "STRING")
    RETURN_NAMES = ("keyframes", "job", "report")
    OUTPUT_IS_LIST = (True, False, False)
    OUTPUT_TOOLTIPS = ("The finished keyframes, one per swap moment (whole frames).",
                       "The job with the keyframes (connect to Assemble).",
                       "Per keyframe: who the enhancer found where, the check against the tracker, the edit prompt, "
                       "times.")
    FUNCTION = "recast"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Multi-person swap keyframes, step 1b (0.5.91): the Qwen Image 2.1 prompt enhancer tells who is "
                   "who in every keyframe (by the names and looks in people_at_start), H3MD writes the swap prompt, "
                   "one Qwen Image 2.1 edit per keyframe. Then H3MD Keyframe 2: Assemble.")

    def recast(self, job, pictures, model, clip, vae, sampler, sigmas, pe_model, seed):
        import time
        import numpy as np
        import comfy.utils
        from PIL import Image
        one = lambda v: v[0] if isinstance(v, list) else v
        job, model, clip, vae, sampler, sigmas, pe_model = (one(job), one(model), one(clip), one(vae), one(sampler),
                                                            one(sigmas), one(pe_model))
        seed = int(one(seed))
        pics = [p[b:b + 1] for p in pictures for b in range(int(p.shape[0]))]
        need = sum(len(m["items"]) for m in job["moments"])
        if len(pics) != need:
            raise K.KeyframeError(f"{TAG} {len(pics)} picture(s) for {need} recast person(s): connect Prepare's "
                                  "pictures. 请接上 Prepare 的 pictures。")
        W, H = job["size"]
        todo = [m for m in job["moments"] if m["items"]]
        pbar = comfy.utils.ProgressBar(max(1, 2 * len(todo)))
        lines, t_all = [], time.time()
        # who is who: names and looks from people_at_start (left to right at the start), else the enhancer
        # describes the people of the first keyframe
        who = {}
        roster = K.parse_roster(job.get("roster", ""))
        order = job.get("order") or []
        if roster:
            for k, nl in enumerate(roster, 1):
                if k <= len(order):
                    who[order[k - 1]] = nl
            lines.append("people (people_at_start): " + "; ".join(f"{n}: {l}" for n, l in roster))
        else:
            t0 = time.time()
            first = todo[0]
            ans = _enhance(pe_model, K.ROSTER_ASK, to_tensor(np.ascontiguousarray(job["frames"][first["at"]])),
                           seed, unload=False)
            roster = K.parse_roster_answer(ans)
            for q, r in _places(first["items"][0]):
                if r <= len(roster):
                    who[q] = roster[r - 1]
            lines.append(f"people (described by the enhancer at {first['at']:.2f} s, {time.time() - t0:.1f} s): " +
                         ("; ".join(f"{n}: {l}" for n, l in roster) if roster else
                          f"NOT UNDERSTOOD - places from the tracker only. Fill people_at_start. Answer: {ans[:300]}"))
        names = [n for n, _ in who.values()]
        # 1 the enhancer: who stands where in every keyframe (kept loaded until the last one)
        found = {}
        for i, m in enumerate(todo):
            t0 = time.time()
            src_t = to_tensor(np.ascontiguousarray(job["frames"][m["at"]]))
            ans = (_enhance(pe_model, K.identify_ask(list(who.values())), src_t, seed, unload=i == len(todo) - 1)
                   if who else "")
            ranks, poses = K.parse_identify(ans, names)
            found[m["at"]] = (ranks, poses, ans, time.time() - t0)
            pbar.update(1)
        # 2 our prompt + one Qwen Image 2.1 edit per keyframe
        done, out_imgs, n = {}, [], 0
        gw, gh = K.fit_size(W, H)
        for m in job["moments"]:
            if not m["items"]:
                continue
            refs = pics[n:n + len(m["items"])]
            n += len(m["items"])
            ranks, poses, ans, t_pe = found[m["at"]]
            people, notes = [], []
            for j, it in enumerate(m["items"]):
                nm, lk = who.get(it["id"], ("", it.get("look") or ""))
                rank, note = K.settle_rank(it["rank"], ranks.get(nm) if nm else None)
                people.append({"rank": rank, "img": j + 2, "name": nm, "look": lk, "desc": it.get("desc", ""),
                               "pose": poses.get(nm, ""), "picture": it["picture"]})
                notes.append(f"  picture {it['picture']} = {nm or 'left ' + str(it['left'])}: place {rank} ({note})")
            if len({p["rank"] for p in people}) < len(people):
                for p, it in zip(people, m["items"]):
                    p["rank"] = it["rank"]
                notes.append("  two people got the same place - the tracker's places used for all")
            prompt = K.recast_prompt(people)
            t0 = time.time()
            src = np.ascontiguousarray(job["frames"][m["at"]])
            out = _qwen_edit(model, clip, vae, sampler, sigmas, seed, prompt, [_resized(src, gw, gh)] + refs)
            out = np.asarray(Image.fromarray(out).resize((W, H), Image.LANCZOS))
            lines.append(f"{m['at']:.2f} s: enhancer {t_pe:.1f} s, edit {time.time() - t0:.1f} s ({gw}x{gh})")
            lines.extend(notes)
            lines.append("  enhancer: " + " ".join(ans.split())[:600])
            lines.append("  prompt: " + prompt)
            done[m["at"]] = out
            out_imgs.append(to_tensor(out))
            pbar.update(1)
        lines.append(f"seed {seed}; total {time.time() - t_all:.1f} s for {len(todo)} keyframe(s)")
        report = "\n".join(lines)
        LOG.info("%s recast report:\n%s", TAG, report)
        job = dict(job, recast_frames=done,
                   recast={"method": "enhancer tells who is who, H3MD writes the prompt, one Qwen Image 2.1 edit",
                           "seed": seed, "report": report})
        return {"ui": {"text": [report]}, "result": (out_imgs, job, report)}


NODE_CLASS_MAPPINGS = {"H3MDKeyframePrepare": H3MDKeyframePrepare, "H3MDKeyframeRecast": H3MDKeyframeRecast,
                       "H3MDKeyframeAssemble": H3MDKeyframeAssemble}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDKeyframePrepare": "H3MD Keyframe 1: Prepare 准备关键帧 0.5.91",
                              "H3MDKeyframeRecast": "H3MD Keyframe 1b: Recast 认人换人 0.5.91",
                              "H3MDKeyframeAssemble": "H3MD Keyframe 2: Assemble 打包计划 0.5.91"}
