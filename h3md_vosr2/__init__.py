"""H3MD's built-in VOSR 2.0 face restore (0.6.6) - no other node pack and no pip package needed.

  code      color.py, inference.py, loader.py, tiled_vae.py and models/ are ComfyUI-VOSR2 0.4.2 by ylchen333
            (https://github.com/ylchen333/ComfyUI-VOSR2, commit 6a21810); Apache License 2.0, see
            LICENSE_VOSR2.txt.  Unchanged except loader.py: three eval-mode calls are written ".train(False)" (the
            same thing; the Comfy Registry scanner flags the other spelling).  It is the ComfyUI port of VOSR 2.0 (https://github.com/cswry/VOSR, Apache-2.0).
            Its two nodes (VOSR 2.0 Model Loader / Upscale) are NOT registered by H3MD, so that pack can still be
            installed next to H3MD without a clash.
  models    NOT in the package (about 7 GB): the VOSR 2.0 bundle - LightningDiT + its Qwen-Image 2D VAE + DINOv2-L -
            is fetched by loader.py from https://huggingface.co/CSWRY/VOSR into ComfyUI/models/vosr2/VOSR2 the first
            time face refine runs, and never again once the files are there.  The same folder and layout as the
            ComfyUI-VOSR2 pack, so files downloaded for that pack are found.

H3MD's save node loads the bundle itself when face refine is on and nothing is wired to vosr2_model - until 0.6.5
the example workflows carried the pack's loader node, a red node for everyone who had not installed the pack.
"""

from __future__ import annotations

import logging

from . import loader as LD
from .inference import run_vosr2

LOG = logging.getLogger("H3MD")
_TAG = "[H3MD VOSR2]"
SOURCE_VERSION = "0.4.2"
MODEL = LD.KNOWN_MODEL
_CACHE = {}

MANUAL_HELP = (
    "Put these files from https://huggingface.co/CSWRY/VOSR into ComfyUI/models/vosr2/VOSR2/ : "
    "VOSR2/args.json -> args.json ; VOSR2/checkpoints/ema_model.safetensors -> checkpoints/ema_model.safetensors ; "
    "Qwen-Image-vae-2d/config.json + diffusion_pytorch_model.safetensors -> Qwen-Image-vae-2d/ ; "
    "torch_cache/checkpoints/dinov2_vitl14_pretrain.pth is converted to dinov2_vitl14.safetensors on first use "
    "(needs the download to work once, or copy dinov2_vitl14.safetensors from a machine that has it). "
    "手动放置：从 huggingface.co/CSWRY/VOSR 下载上面这几个文件，放到 ComfyUI/models/vosr2/VOSR2/ 里对应的位置。")


def bundle_dir():
    return LD._VOSR2_ROOT / MODEL


def missing_files():
    """Names of the bundle parts that are not on disk yet (empty = nothing will be downloaded)."""
    b = bundle_dir()
    out = []
    if LD._find_dit_weight(b) is None or not (b / "args.json").is_file():
        out.append("VOSR2 DiT (args.json + checkpoints/ema_model.safetensors)")
    if not all((b / LD._VAE_SUBDIR / n).is_file() for n in LD._VAE_FILES):
        out.append("Qwen-Image 2D VAE (Qwen-Image-vae-2d/)")
    if not (b / LD._VISION_FILENAME).is_file():
        out.append("DINOv2-L encoder (dinov2_vitl14.safetensors)")
    return out


def load(dtype: str = "default"):
    """The VOSR 2.0 bundle (kept for the next run).  Missing files are downloaded first; a failed download or load
    raises - the save node then writes the video without face refine and says why."""
    if dtype in _CACHE:
        return _CACHE[dtype]
    gone = missing_files()
    if gone:
        LOG.warning("%s first use: downloading the VOSR 2.0 model files (about 7 GB in all) from huggingface.co/%s "
                    "into %s - missing: %s. 第一次修脸：正在下载 VOSR 2.0 模型（共约 7 GB）。",
                    _TAG, LD.HF_REPO_ID, bundle_dir(), "; ".join(gone))
    model = LD.load_vosr2(MODEL, dtype)
    _CACHE.clear()
    _CACHE[dtype] = model
    return model


def is_builtin(model) -> bool:
    return isinstance(model, LD.VOSR2Model)


class Upscale:
    """The call H3MD makes on the pack's 'VOSR 2.0 Upscale' node, on the built-in code."""

    @staticmethod
    def execute(model, image, upscale, seed, color_alignment, tile_size, tile_overlap, vae_tile_size,
                vae_tile_overlap):
        return run_vosr2(model, image, upscale, seed, color_alignment, tile_size, tile_overlap, vae_tile_size,
                         vae_tile_overlap)
