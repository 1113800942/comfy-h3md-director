"""H3MD live preview (0.6.5): watch a segment while it samples.

After each sampler step (from a chosen step of the low-res pass on) the model's current guess of the FINISHED
segment (x0 / "denoised", not the noisy state) is decoded with the bundled tiny decoder taeh3 and shown as a strip of
frames spread over the segment on the running node (ComfyUI's own sampler-preview channel).  Nothing waits: the run
goes on into the high-res pass; ComfyUI's Cancel stops it (the disk cache keeps every finished step).

Display only: never changes the sampled result, the seed or any cache key.  Any error turns the preview off for the
rest of the run (logged once) instead of stopping the render.

Decoder: taeh3 by Ollin Boer Bohan (https://github.com/madebyollin/taehv), MIT License - h3md_preview/LICENSE_TAEHV.txt.
Loaded through ComfyUI's own TAEHV class (comfy.taesd.taehv, ComfyUI >= 0.36 knows the H3 variant).
"""

from __future__ import annotations

import logging
import math
import os
import time

import torch

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD Live Preview]"

DECODER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "h3md_preview", "taeh3.safetensors")
MODES = ("low-res", "low-res + high-res", "off")
DEFAULT_MODE = "low-res"
DEFAULT_FROM_STEP = 3
N_FRAMES = 8          # frames in the strip, spread over the segment
MAX_SIDE = 1536       # longest side of the strip image sent to the browser

_cfg = {"mode": DEFAULT_MODE, "from_step": DEFAULT_FROM_STEP, "seg": ""}
_state = {"model": None, "failed": False, "ms": [], "last": None}


def set_segment(k: int, n: int):
    """Label of the segment being sampled (1-based)."""
    _cfg["seg"] = f"segment {k}/{n}  " if n > 1 else ""


def configure(mode=DEFAULT_MODE, from_step=DEFAULT_FROM_STEP):
    """Called by the Loop at the start of a run (inputs live_preview / live_preview_from_step)."""
    mode = mode if mode in MODES else DEFAULT_MODE
    _cfg.update(mode=mode, from_step=max(1, int(from_step or 1)))
    _state["failed"] = False
    _state["ms"] = []
    _state["last"] = None
    _cfg["seg"] = ""
    LOG.info("%s %s%s", _TAG, mode, f" (low-res from step {_cfg['from_step']})" if mode != "off" else "")


def wanted(stage: str, step: int) -> bool:
    """step: 1-based step of that stage."""
    m = _cfg["mode"]
    if m == "off" or _state["failed"]:
        return False
    if stage == "low_res":
        return step >= _cfg["from_step"]
    if stage == "high_res":
        return m == "low-res + high-res"
    return False


def _decoder(device):
    m = _state["model"]
    if m is None:
        import comfy.sd
        import comfy.utils
        if not os.path.isfile(DECODER_FILE):
            raise FileNotFoundError(f"bundled decoder missing: {DECODER_FILE}")
        vae = comfy.sd.VAE(sd=comfy.utils.load_torch_file(DECODER_FILE))
        m = vae.first_stage_model.train(False)   # inference mode (not the word the registry scanner flags)
        if getattr(m, "latent_channels", None) != 24:
            raise ValueError("h3md_preview/taeh3.safetensors is not an H3 (24-channel) tiny decoder")
        m.show_progress_bar = False
        _state["model"] = m
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    return m.to(device=device, dtype=dtype), dtype


def latent_shapes_of(model):
    """ComfyUI packs an AV latent (video + audio) into one flat [B, 1, N] tensor while sampling and stores the
    stream shapes on the BaseModel (CFGGuider.inner_sample).  model = the k-diffusion model the sampler gets
    (KSamplerX0Inpaint -> CFGGuider -> BaseModel)."""
    m = model
    for _ in range(4):
        shapes = getattr(m, "latent_shapes", None)
        if shapes:
            return [tuple(s) for s in shapes]
        m = getattr(m, "inner_model", None)
        if m is None:
            break
    return None


def video_stream(denoised, shapes=None):
    """The video latent [B, 24, T, H, W] of the sampler's x0: nested (video, audio), packed flat (inside the
    sampler; needs the stream shapes) or a plain video latent."""
    if denoised is None:
        return None
    if getattr(denoised, "is_nested", False):
        parts = denoised.unbind() if hasattr(denoised, "unbind") else denoised.tensors
        denoised = parts[0]
    elif isinstance(denoised, torch.Tensor) and denoised.ndim == 3 and shapes:
        vs = shapes[0]
        cut = math.prod(vs[1:])
        if denoised.shape[-1] < cut:
            return None
        denoised = denoised[:, :, :cut].reshape([denoised.shape[0]] + list(vs[1:]))
    if not isinstance(denoised, torch.Tensor) or denoised.ndim != 5 or denoised.shape[1] != 24:
        return None
    return denoised


def pick(t: int, n: int = N_FRAMES):
    """n latent-frame indices spread evenly over 0..t-1 (first and last included)."""
    if t <= n:
        return list(range(t))
    return sorted({round(k * (t - 1) / (n - 1)) for k in range(n)})


PX_BATCH = 4 * 832 * 480   # output pixels decoded at once (~0.5 GB peak); 4 low-res frames, 1 high-res frame


@torch.inference_mode()
def decode_frames(video, idx, max_side=None):
    """-> list of HxWx3 uint8 CPU tensors, one still frame per latent frame index (a few at a time: small peak
    memory).  max_side: shrink the frames to this longest side (the clip in the window does not need full size)."""
    import torch.nn.functional as F
    device = video.device
    model, dtype = _decoder(device)
    out_h, out_w = int(video.shape[-2]) * 16, int(video.shape[-1]) * 16
    batch = max(1, PX_BATCH // (out_h * out_w))
    out = []
    try:
        for s in range(0, len(idx), batch):
            ii = list(idx[s:s + batch])
            z = video[0][:, ii].movedim(1, 0).unsqueeze(2).to(dtype)   # [n, 24, 1, h, w]: n single still frames
            img = model.decode(z)                        # 5-D in 0..1: [n, 3, 1, H, W] or [n, 1, 3, H, W]
            img = img[:, :, 0] if img.shape[1] == 3 else img[:, 0]     # [n, 3, H, W]
            if max_side and max(img.shape[-2:]) > max_side:
                img = F.interpolate(img.float(), scale_factor=max_side / max(img.shape[-2:]), mode="bilinear",
                                    antialias=True)
            img = (img.clamp(0, 1) * 255).to(torch.uint8).movedim(1, -1).cpu()
            out.extend(img.unbind(0))
    finally:
        model.to("cpu")
    return out


CLIP_SIDE = 480        # longest side of the clip in the window


def pixel_frames(t_latent: int) -> int:
    """H3 latent frames -> video frames (5n+2 latent <-> 17n+5 frames at 24 fps)."""
    return max(1, (t_latent - 2) // 5 * 17 + 5) if t_latent > 1 else 1


def frame_ms(t_latent: int, n: int) -> int:
    """Display time of each clip frame so the clip lasts as long as the segment (24 fps)."""
    return max(20, int(round(1000.0 * pixel_frames(t_latent) / 24.0 / max(1, n))))


def clip_webp(frames, t_latent: int) -> bytes:
    """Animated WebP of all latent frames, played in real time (each latent frame ~ 3.4 video frames)."""
    import io
    from PIL import Image
    ims = [Image.fromarray(f.numpy()) for f in frames]
    ms = frame_ms(t_latent, len(ims))
    buf = io.BytesIO()
    ims[0].save(buf, "WEBP", save_all=True, append_images=ims[1:], duration=ms, loop=0, quality=60, method=0)
    return buf.getvalue()


def strip_image(frames, label: str = ""):
    from PIL import Image, ImageDraw
    h, w = frames[0].shape[:2]
    cols = len(frames) if h > w else min(4, len(frames))
    rows = math.ceil(len(frames) / cols)
    scale = min(1.0, MAX_SIDE / max(cols * w, rows * h))
    tw, th = max(1, int(w * scale)), max(1, int(h * scale))
    top = 18 if label else 0
    sheet = Image.new("RGB", (cols * tw, rows * th + top), (16, 16, 16))
    for k, f in enumerate(frames):
        im = Image.fromarray(f.numpy()).resize((tw, th), Image.BILINEAR)
        sheet.paste(im, ((k % cols) * tw, top + (k // cols) * th))
    if label:
        ImageDraw.Draw(sheet).text((6, 3), label, fill=(235, 235, 235))
    return sheet


EVENT = "h3md.live_preview"   # websocket event for the H3MD Live Preview window node (web/js/h3md_live_preview.js)


def _send_node(step, total, strip):
    """The running node's own sampler preview (the Loop, also inside a subgraph): the strip of 8 frames."""
    import comfy.utils
    hook = comfy.utils.PROGRESS_BAR_HOOK
    if hook is not None:
        hook(step, total, ("JPEG", strip, MAX_SIDE), node_id=None)


def _send_window(clip: bytes, label: str):
    """Every H3MD Live Preview window node, wherever it sits (outer graph, subgraph): the playing clip."""
    try:
        import base64
        from server import PromptServer
        PromptServer.instance.send_sync(EVENT, {
            "video": "data:image/webp;base64," + base64.b64encode(clip).decode("ascii"), "label": label})
    except Exception as exc:   # no server (tests) / no client: the node preview is enough
        LOG.debug("%s window event not sent: %s", _TAG, exc)


class H3MDLivePreviewWindow:
    """A window that shows the Loop's live preview.  Place it anywhere (outer graph or a subgraph); no wires.
    It is not part of the run (no inputs, no outputs): the picture arrives over the websocket."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "MiniMax H3/H3MD"
    DESCRIPTION = ("Shows the H3MD Loop's live preview while a run samples: drop it anywhere (also outside the subgraph "
                   "that holds the Loop), no wires needed. Settings: live_preview / live_preview_from_step in H3MD Loop "
                   "Options.\n跑的时候显示 H3MD Loop 的实时预览：放在画布任何地方都行（Loop 在子图里也行），不用连线。"
                   "设置在 H3MD Loop Options 的 live_preview / live_preview_from_step。")

    def noop(self):
        return ()


NODE_CLASS_MAPPINGS = {"H3MDLivePreviewWindow": H3MDLivePreviewWindow}
NODE_DISPLAY_NAME_MAPPINGS = {"H3MDLivePreviewWindow": "H3MD Live Preview 实时预览"}


def show(stage: str, step: int, total: int, denoised, shapes=None):
    """Sampler callback hook (H3MD timed_sampler), called after ComfyUI's own step preview.  Never raises.
    shapes: latent_shapes_of(model) when the sampler works on a packed AV latent.
    Decodes every latent frame of the model's current x0: a playing clip for the window node(s), and 8 of those
    frames as a strip for the running node."""
    if _cfg["mode"] == "off" or _state["failed"]:
        return
    try:
        if not wanted(stage, step):
            # keep the last strip on the node (ComfyUI's own preview would replace it with a colour-noise image);
            # the window keeps its last clip by itself
            if _state["last"] is not None:
                _send_node(step, total, _state["last"])
            return
        video = video_stream(denoised, shapes)
        if video is None:   # never silent (0.6.5 test 01:10: a packed latent was skipped without a word)
            _state["failed"] = True
            LOG.warning("%s turned off for this run: no H3 video latent in the sampler's x0 (%s %s, shapes %s)",
                        _TAG, type(denoised).__name__, tuple(getattr(denoised, "shape", ())), shapes)
            return
        t0 = time.perf_counter()
        t_lat = int(video.shape[2])
        frames = decode_frames(video, list(range(t_lat)), CLIP_SIDE)
        t1 = time.perf_counter()
        name = "low-res" if stage == "low_res" else "high-res"
        label = f"{_cfg['seg']}{name} step {step}/{total}"
        strip = strip_image([frames[i] for i in pick(t_lat)], label + "  (preview, not final quality)")
        clip = clip_webp(frames, t_lat)
        t2 = time.perf_counter()
        _state["ms"].append((t2 - t0) * 1000.0)
        _state["last"] = strip
        _send_node(step, total, strip)
        _send_window(clip, label)
        LOG.info("%s %s step %d/%d: %d frames decoded in %.0f ms, clip %.0f ms (%d KB)", _TAG, name, step, total,
                 len(frames), (t1 - t0) * 1000.0, (t2 - t1) * 1000.0, len(clip) // 1024)
    except Exception as exc:  # display only: never stop the render
        _state["failed"] = True
        LOG.warning("%s turned off for this run: %s", _TAG, exc)
