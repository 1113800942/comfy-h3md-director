# Third-party notices

## MiniMax H3 latent upscaler

The network classes (`AttnBlock3D`, `ResBlockEmb3D`, `TemporalConv`, `LatentResizer3D`),
normalization helpers and H3 latent statistics in `luna_upscaler.py` are adapted from
[LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler](https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler),
[`nodes/minimax_h3_latent_upscaler_3d.py`](https://raw.githubusercontent.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler/main/nodes/minimax_h3_latent_upscaler_3d.py),
retrieved 2026-10-28. [Upstream licence](https://raw.githubusercontent.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler/main/LICENSE).

Adaptations: preserve temporal length in scale-only calls; ComfyUI folder discovery,
safe CPU checkpoint loading and architecture inference; cache on CPU; exact target size,
original dtype/device restoration, and unconditional offload/cleanup after inference.
The 2D source is not used. Model weights are not bundled or relicensed by this notice.
The upstream LICENSE has a literal `/n/n` between paragraphs; it is rendered below as a blank line.

MIT License

Copyright (c) 2026 LBH-123-AI

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
