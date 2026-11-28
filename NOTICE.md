# NOTICE — H3MD Director / H3MD 导演台 (ComfyUI-H3MD-Director)

Copyright (C) 2026 YJ

SPDX-License-Identifier: AGPL-3.0-or-later (with an attribution term, see section 1); h3md_luna/: GPL-3.0

---

## 1. License of H3MD / H3MD 的许可证

**English.** ComfyUI-H3MD is free software: you can redistribute it and/or modify it under the terms of the GNU
**Affero** General Public License as published by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version, **with the additional attribution term below** (AGPL-3.0 section 7(b)). It is
distributed WITHOUT ANY WARRANTY. The full license text is in [LICENSE](LICENSE)
(<https://www.gnu.org/licenses/agpl-3.0.txt>).

Plain-language summary (not a substitute for the license text):
* Anyone may use, study, modify and share H3MD, including commercially (for example selling videos made with it, or
  running it on a paid GPU platform such as RunningHub).
* If you **distribute** H3MD or a modified version / derivative work, it must be licensed under AGPL-3.0 (or later),
  with the complete corresponding source code, the copyright and license notices kept and your changes marked.
  Closed-source redistribution is not allowed.
* If you **modify** H3MD and let people use it **over a network** (a website, an online service, an app backend), you
  must offer those users the complete source code of your modified version (AGPL-3.0 section 13).
* There is no warranty.

**Additional term (AGPL-3.0 section 7(b)) — attribution.** Anyone who distributes H3MD or a work based on it, or
offers it (modified or not) to others as an online service, must keep a reasonable, visible attribution:
"Based on H3MD Director (ComfyUI-H3MD-Director) by YJ" with a link to the H3MD repository
(<https://github.com/1113800942/comfy-h3md-director>), shown in the user interface or
documentation where the feature is offered (a node, a workflow page, a product or feature description). It may not
be presented as the provider's own work.

**中文（仅供参考）。** ComfyUI-H3MD 是自由软件，按自由软件基金会发布的 GNU **Affero** 通用公共许可证第 3 版，或（由你选择）
任何更新的版本发布，**并附加下面的署名条款**（AGPL-3.0 第 7(b) 条），**不提供任何担保**。全文见 [LICENSE](LICENSE)。

通俗说明：
* 任何人都可以使用、研究、修改和分享 H3MD，也可以商用（例如出售用它做的视频，或在 RunningHub 这类付费算力平台上运行）。
* 如果你**分发** H3MD 或它的修改版 / 衍生作品，必须同样按 AGPL-3.0（或更新版本）发布，提供完整源码，保留版权和许可证声明，
  并注明改动。**不允许闭源再分发。**
* 如果你**修改了** H3MD 并通过**网络**提供给别人使用（网站、在线服务、应用后台），必须向这些用户提供你修改版的完整源码
  （AGPL-3.0 第 13 条）。
* 不提供任何担保。

**附加条款（AGPL-3.0 第 7(b) 条）——署名。** 分发 H3MD 或基于它的作品，或把它（无论是否修改）作为在线服务提供给他人，
都必须在提供该功能的界面或说明中（节点、工作流页面、产品或功能介绍）保留明显、合理的署名：
“基于 H3MD 导演台 / H3MD Director（ComfyUI-H3MD-Director），作者 YJ”并附 H3MD 仓库链接（<https://github.com/1113800942/comfy-h3md-director>）；不得把它说成是提供方自己的作品。

> 以上中文说明仅供参考，具有法律效力的是 [LICENSE](LICENSE) 中的英文 AGPL-3.0 原文和上面的英文附加条款。
> The Chinese text is for reference only; the English AGPL-3.0 text in [LICENSE](LICENSE) and the English additional
> term above are the legally binding version.

**Bundled GPL-3.0 code.** The folder `h3md_luna/` contains Luna-H3-Sampler code under GPL-3.0
([LICENSES/GPL-3.0.txt](LICENSES/GPL-3.0.txt)); GPL-3.0 section 13 and AGPL-3.0 section 13 allow combining it with
H3MD's AGPL code. Those files stay under GPL-3.0. / `h3md_luna/` 里是 GPL-3.0 的 Luna-H3-Sampler 代码，按两种许可证各自的
第 13 条可以与 H3MD 的 AGPL 代码组合使用，这些文件本身仍是 GPL-3.0。

---

## 2. MiniMax H3 model / MiniMax H3 模型

**English.** H3MD is built for and runs on the MiniMax H3 video model through ComfyUI's core MiniMax H3 nodes.
**H3MD does not include, bundle or redistribute any MiniMax H3 weights, code or documentation.** The MiniMax H3 model
is licensed separately under the **MiniMax H3 Community License Agreement** (release/license date August 2, 2026):
<https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE> (Q&A:
<https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/QA-about-License.md>). Using H3MD with H3 means you must
comply with that license yourself. Key points of that license (read the full text):
* The grant applies **only inside the "Applicable Territory" = worldwide excluding the European Union, the United
  Kingdom, the Republic of Korea and the United States of America.** Use in those regions is not authorized by the
  agreement; MiniMax says users there may apply for a separate license (<https://platform.minimax.io/h3-license>).
* Commercial products/services with more than USD 20 million yearly revenue need prior written authorization; commercial
  products must display "MiniMax H3" in their UI.
* H3 Works and their Outputs may not be used to improve any other AI model.
* Use must follow the Acceptable Use Policy (Exhibit A), e.g. no harm to minors, no impersonation without consent,
  machine-generated content published in public must be disclosed as such, no military use.
* Whoever redistributes H3 Works must ship the H3 agreement and a NOTICE file containing:
  "MiniMax H3 is licensed under the MiniMax H3 Community License Agreement, Copyright © 2026 MiniMax. All Rights Reserved."
  (H3MD itself ships no H3 Works; the sentence is quoted here for information.)

**中文。** H3MD 是为 MiniMax H3 视频模型做的，通过 ComfyUI 核心的 MiniMax H3 节点运行。**H3MD 不包含、不捆绑、不再分发
任何 MiniMax H3 的权重、代码或文档。** H3 模型另按 **MiniMax H3 Community License Agreement**（2026-08-02）授权，链接见
上。用 H3MD 跑 H3，你需要自己遵守那份许可证。要点（以原文为准）：
* 授权**只在“适用地区”有效：全世界，但不包括欧盟、英国、韩国和美国。** 在这些地区使用不在授权范围内，MiniMax 表示
  可以另外申请许可。
* 年收入超过 2000 万美元的商业产品/服务需要事先书面授权；商业产品界面上要显示 “MiniMax H3”。
* 不得用 H3 或它的输出去改进其他 AI 模型。
* 必须遵守附件 A 的可接受使用政策（例如不得伤害未成年人、不得未经同意冒充他人、公开发布的生成内容要标明是 AI 生成、
  不得用于军事等）。

---

## 2b. RIFE frame interpolation (included since 0.6.3) / RIFE 补帧（0.6.3 起随包）

`h3md_rife/IFNet_HDv3.py` (network definition, one import line adapted) and `h3md_rife/rife_v4.26.safetensors`
(weights, RIFE v4.26: the official `flownet.pkl` stored as safetensors, tensors unchanged) come from the official model archive `RIFEv4.26_0921.zip` published by hzwer
(<https://huggingface.co/hzwer/RIFE>, <https://github.com/hzwer/Practical-RIFE>). **MIT License, Copyright (c) 2021
hzwer** - full text in `h3md_rife/LICENSE_RIFE.txt`. The loader, batch inference and warp around it are H3MD's own
code. No code of the ComfyUI-VFI pack is used (until 0.6.2 its model was called at run time; that pack has no
license file).

`h3md_rife/IFNet_HDv3.py`（网络）和 `h3md_rife/rife_v4.26.safetensors`（权重，RIFE v4.26，官方 flownet.pkl 转存为 safetensors，数值不变）取自 hzwer 官方发布的模型包，
MIT 许可，全文见 `h3md_rife/LICENSE_RIFE.txt`。外面的加载、批量推理和 warp 是 H3MD 自己写的。

---

## 2c. taeh3 tiny decoder for the live preview (included since 0.6.5) / taeh3 小解码器（实时预览用，0.6.5 起内置）

`h3md_preview/taeh3.safetensors` is the tiny MiniMax H3 decoder taeh3 by Ollin Boer Bohan, unchanged, from
<https://github.com/madebyollin/taehv> (`safetensors/taeh3.safetensors`). **MIT License, Copyright (c) 2025 Ollin
Boer Bohan** - full text in `h3md_preview/LICENSE_TAEHV.txt`. It is only used to show the live preview while a
segment samples; the network class is ComfyUI's own (`comfy.taesd.taehv`), the preview code is H3MD's own.

`h3md_preview/taeh3.safetensors` 是 Ollin Boer Bohan 的 MiniMax H3 小解码器 taeh3（原文件未改动），采用 MIT 许可，全文见
`h3md_preview/LICENSE_TAEHV.txt`。只用于采样时的实时预览；网络结构用的是 ComfyUI 自带的类，预览代码是 H3MD 自己写的。

## 2d. Face refine: tracking code and face detector (included since 0.6.6) / 修脸：跟脸代码和人脸检测模型（0.6.6 起内置）

`h3md_facerefine/fr_nodes.py` is `nodes.py` of **ComfyUI-H3-FaceRefine 1.1.2** by Carasibana
(<https://github.com/Carasibana/ComfyUI-H3-FaceRefine>, commit d8521d1), unchanged. **MIT License, Copyright (c) 2026
Carasibana** - full text in `h3md_facerefine/LICENSE_FACEREFINE.txt`. H3MD calls its face tracking helpers and its
H3 Face Stitch Back class from the save node; its node classes are not registered by H3MD.

`h3md_facerefine/face_yolov8n_v2.torchscript` is the face detector `face_yolov8n_v2.pt` from Bingsu/adetailer
(<https://huggingface.co/Bingsu/adetailer>, a YOLOv8n model trained for faces; the model card gives Apache-2.0,
earlier AGPL-3.0), exported once to TorchScript with ultralytics (imgsz 640, fp32) so that it runs with PyTorch
alone. The code around it (`h3md_facerefine/detector.py`: letterbox, NMS, boxes back on the frame) is H3MD's own.

`h3md_facerefine/fr_nodes.py` 是 Carasibana 的 ComfyUI-H3-FaceRefine 1.1.2 的 `nodes.py`（原样未改），MIT 许可，全文见
`h3md_facerefine/LICENSE_FACEREFINE.txt`；H3MD 只在保存节点里调用它的跟脸函数和贴回类，不注册它的节点。
`h3md_facerefine/face_yolov8n_v2.torchscript` 是 Bingsu/adetailer 的人脸检测模型 `face_yolov8n_v2.pt`（模型页标注
Apache-2.0，早期为 AGPL-3.0）导出成 TorchScript 的版本，只用 PyTorch 就能运行；周围的代码是 H3MD 自己写的。

## 2e. VOSR 2.0 face restore code (included since 0.6.6) / VOSR 2.0 修脸代码（0.6.6 起内置）

`h3md_vosr2/` (`color.py`, `inference.py`, `loader.py`, `tiled_vae.py`, `models/`) is **ComfyUI-VOSR2 0.4.2** by
ylchen333 (<https://github.com/ylchen333/ComfyUI-VOSR2>, commit 6a21810; changed files: `loader.py`, three eval-mode calls written as `.train(False)`, same effect) - the ComfyUI port of VOSR 2.0
(<https://github.com/cswry/VOSR>). **Apache License 2.0** - full text in `h3md_vosr2/LICENSE_VOSR2.txt`. Its nodes
are not registered by H3MD; H3MD's save node calls its loader and its one-step upscale.

The VOSR 2.0 **model files are not part of H3MD**: about 7 GB, downloaded from <https://huggingface.co/CSWRY/VOSR>
into `ComfyUI/models/vosr2/VOSR2/` the first time face refine runs. That repository gives Apache-2.0 for the VOSR
project's own checkpoints; the Qwen-Image 2D VAE and the DINOv2 encoder in it keep their upstream terms - see the
model page before use.

`h3md_vosr2/` 是 ylchen333 的 ComfyUI-VOSR2 0.4.2（只把 loader.py 里三处切换推理模式的调用写成等效的 .train(False)，Apache-2.0，全文见 `h3md_vosr2/LICENSE_VOSR2.txt`），
H3MD 不注册它的节点，只在保存节点里调用。VOSR 2.0 的**模型文件不在 H3MD 包里**（约 7 GB），第一次修脸时从
huggingface.co/CSWRY/VOSR 下载到 `ComfyUI/models/vosr2/VOSR2/`；模型各部分的许可见该模型页。

## 2f. Sigma refiner (included since 0.6.6) / 末段加步节点（0.6.6 起内置）

`h3md_sigma_refiner.py` (node H3MD Sigma Refiner) is a port of `H3SigmaRefiner` from **ComfyUI-YCNodes-MiniMax-H3**
by yc (<https://github.com/yichengup/ComfyUI-YCNodes-MiniMax-H3>, **MIT License, Copyright (c) 2026 yc**): the same
inputs and the same sigmas out, so the example workflows no longer need that pack.

`h3md_sigma_refiner.py`（H3MD Sigma Refiner 节点）移植自 yc 的 ComfyUI-YCNodes-MiniMax-H3 里的 `H3SigmaRefiner`
（MIT 许可），输入和算出的 sigma 完全相同，示例工作流不再需要装那个包。

## 2g. Chunked feed-forward patch (included since 0.6.6) / 前馈分块补丁（0.6.6 起内置）

`h3md_speed_patches.py` (node H3MD Chunk FeedForward) uses the method of `MiniMaxChunkFeedForward` from
**ComfyUI-KJNodes** by Kijai (<https://github.com/kijai/ComfyUI-KJNodes>, **GPL-3.0**): the feed-forward of every
MiniMax H3 block runs over the tokens in chunks. The node H3MD Sage Attention in the same file contains no KJNodes
code: it calls KJNodes' own SageAttention patch node when that pack is installed and does nothing otherwise.

`h3md_speed_patches.py` 里的 H3MD Chunk FeedForward 节点用的是 Kijai 的 ComfyUI-KJNodes（GPL-3.0）里
`MiniMaxChunkFeedForward` 的做法。同一文件里的 H3MD Sage Attention 不含 KJNodes 的代码：装了 KJNodes 就调用它的
SageAttention 补丁节点，没装就什么都不做。

## 3. Runtime dependencies (not included) / 运行时依赖（不包含在本包里）

| Project | Status (checked 2026-10-19) | How H3MD uses it |
|---|---|---|
| ComfyUI — <https://github.com/Comfy-Org/ComfyUI> | GPL-3.0 | Host application; core MiniMax H3 nodes are called. |

这些依赖项目的许可证由其作者决定，H3MD 的许可证不改变它们。H3MD 不再使用 selflift-Avatar（2026-10-29 起换成自带的 Luna 引擎）。

---

## 4. Third-party code and ideas / 第三方代码与思路

Code adapted under a permissive license (notices kept; MIT and GPL-3.0 code can be combined with AGPL-3.0):

| Source | License | Used in H3MD |
|---|---|---|
| Luna-H3-Sampler — <https://github.com/lunaaispace-eng/Luna-H3-Sampler> (commit c5c67294, 2026-10-29) | GPL-3.0 | Two-pass sampling engine, SelfLift lift and learned H3 latent upscaler, vendored in `h3md_luna/` (called by `h3md_sampler.py`). Modified by H3MD on 2026-10-30 in `luna_two_pass.py`: optional `low_res_tail` (rerun tail lock). |
| LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler — <https://github.com/LBH-123-AI/Comfyui_Minimax_h3_latent_Upscaler> | MIT, Copyright (c) 2026 LBH-123-AI | Upscaler network, adapted inside Luna's `luna_upscaler.py` (see `h3md_luna/THIRD_PARTY_NOTICES.md`). |
| nkxx188/ComfyUI-MiniMaxH3-Easy — <https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy> | MIT, Copyright (c) 2026 nkxx188 | Strip layout technique adapted in `h3md_tiled_hires.py` (notice in the module header). |

Ideas only — no code copied (GPL sources were read for ideas only):

| Source | License | Idea |
|---|---|---|
| xmarre/MiniMax-H3-Flow-Aligned-Regenerate — <https://github.com/xmarre/MiniMax-H3-Flow-Aligned-Regenerate> | Apache-2.0 | Seam decode context (`h3md_seam.py`, `merge_overlap_source`). |
| elledartois/comfyui_dtv_h3timeline (DTV H3 reference-window node) — <https://github.com/elledartois/comfyui_dtv_h3timeline> | MIT | Advancing reference windows (`h3md_refwindow.py`). |
| Clerveu/ComfyUI-H3-Rolling — <https://github.com/Clerveu/ComfyUI-H3-Rolling> | MIT | Advancing reference windows (`h3md_refwindow.py`). |
| JGRFW/comfyui-AICG3D — <https://github.com/JGRFW/comfyui-AICG3D> | GPL-3.0 | Bounded, eased seam tone correction (`h3md_seam.py`, independent implementation). |
| T8mars/comfyui-minimax-h3-audio-T8 — <https://github.com/T8mars/comfyui-minimax-h3-audio-T8> | GPL-3.0-or-later | Transformer / trainer-MLP semantic-bridge formulas and chaining rules (`h3md_semantic_bridge.py`, independent implementation; T8 is only imported by tests for parity checks). |
| H3 Relay | GPL-3.0 (per earlier review; not re-checked) | Read for ideas only; no code used. |

The six-tensor MLP semantic-bridge path in `h3md_semantic_bridge.py` is ported from the author's own earlier
ComfyUI-H3-SemanticBridge package.

---

## 5. Models / 模型

The models listed in the example workflow's `models` manifest are downloaded by the user from their own pages and are
**not** part of H3MD; each has its own license. / 示例工作流 `models` 清单里的模型由用户从各自页面下载，**不属于** H3MD，
各有各的许可证。

