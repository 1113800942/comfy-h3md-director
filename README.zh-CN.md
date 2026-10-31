# H3MD Director · H3MD 导演台

[English](README.md) | **中文**

在 ComfyUI 里用 MiniMax H3 做长视频、多镜头视频——**一次生成 2 分钟以上的 1080p**。在可视化导演台里排镜头，逐段生成，
每段做完就存进硬盘，哪段坏了只修哪段。

> H3MD **不包含**任何 MiniMax H3 权重；模型有它自己的许可证（见 [NOTICE.md](NOTICE.md)）。

## 样片

| 2K 单段直出（2560×1408，15 秒） | 1080p 长视频（1920×1088，2 分 29 秒，节选） |
|:---:|:---:|
| <img src="docs/media/coast_2k_one_segment.gif" width="420"> | <img src="docs/media/coast_1080p_long.gif" width="420"> |
| **多镜头叙事**（1080p，2 分 19 秒，节选） | **动作复刻**：深度图 → 成片（1088×1920，2 分 13 秒，节选） |
| <img src="docs/media/palace_1080p_story.gif" width="420"> | <img src="docs/media/motion_copy_depth_vs_result.gif" width="420"> |

## 亮点
- **一次生成长视频**：2–3 分钟、1088×1920，自动拆成无缝续接的段。
- **坏了只修那几段**：`rerun_segments` 只重跑有问题的段，两头锁住前后段。
- **动作复刻**：参考视频只提供动作（深度图 + SAM3 + Fun ControlNet），长相来自你的照片。
- **长视频不漂色**：`lowres_anchor` 让单场景长镜头从头到尾颜色和人脸都稳定。
- **按你的显卡来**：按显存自动拆段、高清条带分块、硬盘缓存和断点续跑；4B + ClipProj 编码器能省十几 GB。

## 安装

> [!IMPORTANT]
> **安装：在终端里运行下面两行命令，然后重启 ComfyUI：**
>
> ```bash
> cd ComfyUI/custom_nodes
> git clone https://github.com/1113800942/comfy-h3md-director ComfyUI-H3MD-Director
> ```
>
> 也可以用 [comfy-cli](https://github.com/Comfy-Org/comfy-cli)：`comfy node install comfyui-h3md-director`，
> 或者 ComfyUI-Manager → **Custom Nodes Manager** → 搜 **H3MD Director**。

1. 不需要 `pip install`。
2. ComfyUI 需要支持 MiniMax H3 的版本（建议 0.43 或更新）。
3. 打开示例工作流：ComfyUI **模板 / Templates** → `ComfyUI-H3MD-Director` → **H3MD Director**，或者把
   [`example_workflows/H3MD Director.json`](example_workflows/H3MD%20Director.json) 拖进 ComfyUI。
4. 缺节点包：ComfyUI-Manager → **Install Missing Custom Nodes**。缺模型：看工作流里的 **模型下载** note，每个文件都有下载链接和存放文件夹。

## 快速开始
1. 在导演台里放好照片、参考视频和音乐，每个镜头写好提示词。
2. Loop 节点上 `stop_after = 1`，先只跑第 1 段，看画面和速度。
3. 改回 `0`，`resume_from_cache` 打开：第 1 段从缓存取，其余段接着生成。
4. 某段坏了？在 `rerun_segments` 填段号（如 `8-10`）再跑一次，只重跑这几段。

```
H3MD Director --plan--> H3MD Loop Sampler（+ H3MD Loop Options）--long video--> H3MD Save Video Interp
```

## 更多内容（点开查看）

<details>
<summary><b>这个节点包做什么</b></summary>

* **多段续接**：`H3MD Loop Sampler` 按导演台的计划把所有镜头/分段跑完。续接的镜头用上一段最终 latent 的尾巴做重叠
  前缀（`overlapIn` 22 或 39 帧，drift 方式：采样中轻微重噪、最后精确还原），硬切（`overlapIn` 0）则独立生成。超过
  token 预算的镜头会自动拆成子段。
* **两段式采样（自带，不用另装）**：每段都走 `H3MD Sampler`，内置 Luna-H3-Sampler 引擎（GPL-3.0）：先低清跑前几步，
  用学习型 H3 潜空间放大器放大，再高清跑完。音频在两遍里是同一条轨迹。
* **高清自动分块**：`highres_tiling`（默认开）每段先测空闲显存，放得下就整帧跑，放不下就切成最少的条带；万一爆显存，
  会自动多切几条重跑高清阶段（低清结果保留），并记住这台机器的情况。
* **硬盘缓存 + 断点续跑**：`cache_mode = disk`（默认）时每段采样完立刻解码写进硬盘，内存里一次只有一段；打开
  `resume_from_cache` 后重新排队会跳过已完成的段，seed 自动沿用上次那次运行的。
* **流式出片**：`long video` 输出 → `H3MD Save Video Interp`，从缓存一段一段读帧、（可选）RIFE 插帧、编码写文件。
* **导演台**：`H3MD Director` 自带可视化编辑器（素材库、时间线、镜头标签页、@ 引用素材，图片标签带缩略图）。
* **动作复刻**：`H3MD Depth Control` 把参考视频转成深度图 / SAM3 人物剪影，走 Fun ControlNet + `<Video N>`。

</details>

<details>
<summary><b>安装细节和依赖</b></summary>

1. 把整个文件夹放到 `custom_nodes/ComfyUI-H3MD-Director`
   （`git clone https://github.com/1113800942/comfy-h3md-director custom_nodes/ComfyUI-H3MD-Director`，或直接复制）。
2. 安装依赖的节点包（见下），然后重启 ComfyUI。
3. 不需要额外 `pip install`：H3MD 只用到 ComfyUI 自带的 torch、numpy、PyAV、Pillow、einops。
4. 下载学习型潜空间放大器（推荐）放到 `models/latent_upscale_models/`：`minimax_h3_latent_upscaler_3d_fp16.safetensors`
   或它的微调版（如 `h3_upscaler_sharpness_2000steps_v0.1_fp32.safetensors`）。不放也能跑（`upscaler_model = none`，最近邻放大，效果差一些）。

依赖：
* **ComfyUI 本体**，需要带核心 MiniMax H3 节点（`comfy_extras.nodes_minimax_h3`），建议 0.43 或更新。
* **不再需要 selflift-Avatar**：两段式采样引擎已经自带（`h3md_luna/`）。
* **ComfyUI-ClipProj**（<https://github.com/nicolab28/ComfyUI-ClipProj>）——示例工作流里 clip projection 模型要用。
* **ComfyUI-VFI**（<https://github.com/GACLove/ComfyUI-VFI>）——只有 `H3MD Save Video Interp` 打开 `interpolate` 时才需要。
* 示例工作流另外还用到：ComfyUI-KJNodes、rgthree-comfy、MiniMax H3 Audio T8、CRT-Nodes、ComfyUI-YCNodes-MiniMax-H3。
  缺哪个，ComfyUI 打开工作流时会提示。

</details>

<details>
<summary><b>只重跑有问题的段（rerun_segments）</b></summary>

片子跑完后如果某几段有问题（跳变、穿帮），不用整片重做：
1. 保持 `resume_from_cache` 打开，其他设置不变；可以改参考图、提示词（**总长和参考图像素尺寸保持不变**，否则自动拆段会变）。
2. 在 Loop 节点最上面的 `rerun_segments` 填要重跑的段，从 1 数，和日志里的“segment 8/16”一致：如 `3`、`4-6`、`8-10, 13-14`。
3. 每一块的开头接上一段的结尾；结尾的 39 帧锁成下一段的旧开头（高清、低清、音频都锁），所以下一段及以后一帧不动。
   选了最后一段，或下一段是硬切时，不锁结尾。连续的几段请一起选（如 8-10），被锁的那一头必须是好的。
4. 其余段直接从缓存取，输出仍是完整视频。被重跑段的旧文件挪到缓存文件夹里的 `_replaced_<时间>` 留底。
5. `rerun_new_seed`（默认开）：重跑的段用新的随机种子，提示词和参考图没改也能抽到不同结果。
开跑前日志会列出每段是“cache”还是“RERUN（head/tail）”；缺缓存时直接报错并列出缺哪几段。

</details>

<details>
<summary><b>长视频漂移和 lowres_anchor</b></summary>

这是 H3 一段接一段续写的共性问题（各家续写节点都有），只能压住、不能根除。实测 16 段：饱和度 0.32→0.46，锐度明显上升。
颜色漂移在**低清阶段**就产生了（模型续写自己的结果），表现为 24 个 latent 通道的均值和幅度慢慢偏移。
* 单一场景的长视频（动作复刻、长镜头）：`H3MD Loop Options` 最后一项 `lowres_anchor` 设 1。每个续接段低清跑完、放大前，
  新画面的通道统计拉回本镜头第一段，低清接续数据也一起校正，偏差不会传到下一段。需要 `continue_lowres_prefix = native_lowres_carry`。
  场景本该变色的叙事视频不要开（硬切后会以新镜头的第一段为准）。
* 其他建议：每段都放参考照片锚定身份和颜色；参考照片不要太浓太锐；提示词少写打光形容词（glowing、radiant 等）；
  特别长的片子在自然切点用参考照片重新起。

</details>

<details>
<summary><b>动作复刻（depth）的规则</b></summary>

* 视频勾了 depth，就**只驱动深度 ControlNet，不当 `<Video N>` 参考**（自动，没有开关）。导演台里它也不占 `<Video N>` 编号。
  原因：黑底剪影当参考时，模型会把黑底或灰色影棚照搬进画面。勾了原声的深度视频例外，仍是 `<Video N>`（日志警告）。
* 勾了 depth 却没接 `H3MD Depth Control`，开跑前直接报错。
* **参考照片不要用纯色/灰色影棚背景**（实测：关掉 `<Video>` 参考后，灰底照片仍会把画面拉灰）。用真实场景照片，或把人物抠出来放在模糊的目标场景上；
  全身照最好不露地板，地板在提示词里只写一种（纯色无纹理最稳）。

</details>

<details>
<summary><b>主要功能怎么用</b></summary>

**导演台 `H3MD Director`**
* `plan_json` 是唯一的数据源，编辑器读它、改完写回它；右上角可在 Editor / JSON 之间切换。
* 素材库（图片 / 视频 / 音频）：上传、或从 `input/` 文件夹选文件，拖到镜头上即可分配。
* 镜头长度是 5+17n 帧（24 fps）。有参考视频的镜头，长度跟着视频走。宽高会对齐到 64 的倍数（低清阶段正好是一半）。
* 提示词里输入 `@` 或点标签按钮插入素材标签（`<Picture N>` / `<Video N>` / `<Audio N>`），编号顺序就是后端实际用的顺序。
* **写提示词前先看清每张 `<Picture N>` 是什么**（标签按钮上有缩略图）：人物、服装、场景要分别对应到真正包含它的那张图。
  把空场景图写成“人物”会让画面中途跳到别的背景或服装。`skills/` 里有中英文写法说明。

**`H3MD Loop Sampler` + `H3MD Loop Options`**
* 主节点：模型、采样器、sigmas、seed、cfg、`transition_step`（低清跑几步）、`lowres_scale`、`rho` / `w_min` / `w_max`、
  `upscaler_model`、`seed_mode`（默认 increment：第 k 段用 seed + k）、`highres_tiling`、最上面的 `rerun_segments` / `stop_after` / `rerun_new_seed`、
  `cache_mode`、`resume_from_cache`。
* 高级开关在 `H3MD Loop Options` 里，接到 `options`；不接 = 全部默认值。节点默认只显示常用项，勾“高级选项”显示全部。
* 输出：`latent`、`images`（只是 64x64 占位图）、`audio`、`frame_count`、`summary`（每段的 seed、分块、每步耗时）、`long video`。
* **`long video` 要接 `H3MD Save Video Interp`**——这是唯一按有限显存/内存设计的出片路径。

**硬盘缓存**
* 缓存文件夹：`resume_<hash>`（打开 `resume_from_cache` 时固定，能断点续跑）或 `run_<时间>_<pid>`。
* 位置：`cache_dir` 为空时自动选（已有的 `H3MD_cache` → 剩余空间最大的本地盘）。
* Loop 自己不会在出片前删缓存。要删就在 `H3MD Save Video Interp` 打开 `delete_cache_after_save`（视频完整写好后才删）。
  1088x1920、243 帧一段大约 3 GB（fp16）。

**`H3MD Save Video Interp`**
* H3 固定 24 fps。开插帧时从 24 插到 `target_fps`；不插帧又把 `target_fps` 设成非 24，声音会对不上（日志会警告）。
* `format`：`video/h264-mp4`（默认）、`video/h265-mp4`、`image/gif`、`image/webp`、`image/png-sequence`（后三种没有声音）。
* chunk 大小按空闲显存/内存自动决定；显存不够会自动减半重试，最后退到 CPU。

</details>

<details>
<summary><b>小显存</b></summary>

* **高清阶段**：`highres_tiling` 保持打开。它按空闲显存自动决定切几条（最多 12 条），放得下就整帧，不会白白变慢。
  爆显存时会自动多切几条重跑；切到最多还不行，就再打开前馈分块重跑一次；仍然不行才报错，并提示怎么调。
* **低清阶段**：没有分块。爆显存时会提示在 H3MD Loop Options 里把 `ff_chunk_rows` 设成 8192（前馈按行分块，结果不变，稍慢）。
* **参考素材占的显存不会随切条变小**：参考图、参考视频、音频和文字在每一条里都是整份的。实测 1088x1920、带 2 张图 +
  1 段参考视频，每条里约 3.7 万行是这些固定内容。所以 **16 GB 以下的卡请用较低分辨率**（如 768x1344 或更小）、
  较短的段（`length` 小一些）、更少/更小的参考素材。
* **模型权重**：交给 ComfyUI 自己的显存管理（DynamicVRAM、`--lowvram`、`--reserve-vram N`）。H3MD 的 LowVRAM LoRA Loader
  可以按 token 分块加载 LoRA。
* `unload_idle_models`（默认开）在两遍之间、每段之间卸载文本编码器 / VAE 等闲置模型；`vram_release = deep` 段间还会清理缓存。
* 显存估算按 RTX 5090 32 GB 实测校准；你的机器第一次爆显存后会被记进 `user/h3md/tiling_calibration.json`，之后自动多切几条。

</details>

<details>
<summary><b>模型清单</b></summary>

示例工作流顶层有一个 `models` 数组（ComfyUI 工作流规范，<https://docs.comfy.org/specs/workflow_json>），每条有
`name`、`url`、`directory`（必填）和 `hash` / `hash_type`（可选）。ComfyUI 前端打开工作流时，如果 `models/<directory>/`
里没有这个文件，就在“缺少模型”弹窗里显示下载按钮。限制：只接受 Hugging Face / Civitai 地址；扩展名只能是
`.safetensors` / `.sft` / `.ckpt` / `.pth` / `.pt`；`directory` 必须是已注册的模型文件夹；**`name` 必须和节点里选的文件名
完全一致**，否则那一条不会显示。

| name | directory | source |
|---|---|---|
| `minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors` | `diffusion_models` | huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models |
| `Huihui-Qwen3-VL-4B-Instruct-abliterated-int8_convrot.safetensors` | `text_encoders` | huggingface.co/ahmed22xa/Huihui-Qwen3-VL-4B-Instruct-abliterated-comfy |
| `minimax_h3_video_vae_fp16.safetensors` | `vae` | huggingface.co/Comfy-Org/MiniMax-H3 |
| `minimax_h3_audio_vae_fp32.safetensors` | `vae` | huggingface.co/Comfy-Org/MiniMax-H3 |
| `mmh3-4b-ClipProj-v3.1-mlp.safetensors` | `clip_projections` | huggingface.co/NicoLab28/ClipProj-MiniMax-H3 |
| `h3_upscaler_sharpness_2000steps_v0.1_fp32.safetensors` | `latent_upscale_models` | huggingface.co/Alissonerdx/Minimax-H3-ComfyUI |
| `H3_Combat_V2.safetensors` | `loras` | huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2 |
| `minimax_h3_DMAD_4step_full_lora_avg_rank_39_bf16.safetensors` | `loras` | huggingface.co/Kijai/MiniMax-H3-experimental |
| `minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors` | `loras` | huggingface.co/lightx2v/Minimax-h3-Turbo |
| `minimax_h3_lms_v1.0_r64_r.safetensors` | `loras` | huggingface.co/RunningHubAI/rh-minimax-h3-lms-v1.0-r64-lora |

每个模型有自己的许可证，使用前请看它的模型页。`ClipProjApply` 那条只有装了 ComfyUI-ClipProj 才显示。

</details>

<details>
<summary><b>RIFE 模型（手动下载）</b></summary>

“缺少模型”弹窗只能下载到 `models/` 下已注册的文件夹，而且不允许 `.pkl`，所以 RIFE 的 `flownet.pkl` 不在 `models` 清单里，需要手动下载：节点包 ComfyUI-VFI（<https://github.com/GACLove/ComfyUI-VFI>）；模型 <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip>，解压后把 `flownet.pkl` 放到 `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl`（相对 ComfyUI 根目录）。ComfyUI-VFI 第一次运行时通常也会自己下载。

</details>

<details>
<summary><b>注意事项和已知问题</b></summary>

* 走 `latent` / 外部解码出来的图片 / `audio` 这些路径处理大/长视频，**不保证**显存/内存不爆；外部一次解码整段 latent，
  接缝处也可能和 `long video` 出的片子略有不同。只有 `long video` → `H3MD Save Video Interp` 是按有限显存/内存设计的。
* `images` 输出只是 64x64 占位图（`cache_mode = ram` 例外）。
* 旧工作流：已删除的选项（`merge_images`、Save Video Interp 的 `preset` / `cache_folder` / `pingpong` / `fps` 输出等）
  加载时按名字对齐并丢弃，其他参数不错位；旧的 `H3MD Loop Sampler (legacy)` 节点保留，旧存档照常打开。
* `video/h265-mp4` 和 10-bit（`yuv420p10le`）**还没测过**。
* `resume_from_cache` 的缓存键不包括模型、LoRA 和放大器；换了这些请关掉 resume 或清掉缓存文件夹。
* 这一版换了采样引擎，旧版留下的缓存不会被续跑使用；旧工作流里的 `H3MD Loop Options` 节点请删掉重新放一个（选项变了）。
* **文本编码器怎么选**：32B（`qwen3vl_32b_minimax_h3_int8_convrot`）对提示词理解最准；显存不够就用 4B + ClipProj，能省十几 GB，
  画质不受影响，只是理解稍弱、画面细节和 32B 略有不同。从零生成时完全可用。**同一部片子从头到尾用同一个编码器**，不要在
  `rerun_segments` 重跑时中途换。
* **快动作残影**：快速甩手、挥手时偶尔有一瞬间的残影（多只手）。原因是时间上 4 帧压成 1 帧，再加上少步数；可以用
  `rerun_segments` 换种子重跑那一段。
* 动作复刻的分镜总长会自动对齐到参考视频（5+17n 向上取整），导出后把超出参考视频的最后几帧剪掉。

</details>

<details>
<summary><b>H3MD 用到的技术和思路，以及致谢</b></summary>

MiniMax H3 单次最多十几秒。要做两三分钟的长视频，难点不在“能不能接上”，而在接了十几段之后还**不跳、不漂、不爆显存、坏了能修**。下面是我们踩过的坑和解法，欢迎拿去改进。

### 1. 用 latent 接续，不走像素
- **问题**：常见做法是把上一段解码成视频再编码回去当参考，每接一次就糊一点、偏色一点。
- **做法**：直接拿上一段**最终 latent 的最后 39 帧**当下一段的开头（noise mask = 0 锁住），采样时只给开头几帧轻微重加噪，最后再精确还原。合并时重叠部分取后一段的。

### 2. 两段式采样 + 低清原生接续
- **做法**：先在一半分辨率跑 6 步，用学习型潜空间放大器放大，再在全分辨率跑 3 步（引擎来自 Luna-H3-Sampler）。
- **低清原生接续**：下一段的低清开头直接用上一段**自己的低清结果**，不从高清缩回去，两条链各自连贯。

### 3. 按显存自动拆段、自动分块
- 每个镜头先按 token 预算估算（画面 + 参考视频 + 图片），超了就自动拆成等长的续接段，拆点尽量放在参考视频动作安静的地方。
- 高清阶段先测空闲显存，放得下就整帧跑，放不下就切成最少的条带；万一爆显存就多切几条重跑，并记住这台机器的情况。
- 慢步保护：一步突然慢 4 倍以上，说明显存耗尽、权重在来回搬，自动停下，缓存保留。

### 4. 硬盘缓存、断点续跑、只重跑坏的段
- 每段采样完立刻解码写硬盘，内存里一次只有一段；重开时跳过已完成的段。
- **重跑选定段**：选中的段开头接上一段，**结尾也锁成下一段的旧开头**（高清 latent、低清结果、音频三样都锁），所以换进去的新段两头都无缝，其他段一帧不动。不用为一处跳变重跑 5 小时。

### 5. 动作复刻：深度只驱动 ControlNet
- 参考视频 → Depth Anything V2 深度图 → SAM3 只保留人物 → Fun ControlNet，**只作用在低清阶段**。
- **实测教训**：把黑底白人形的剪影同时当 `<Video N>` 参考喂进去，模型会把黑底、灰色影棚照搬进画面。现在深度视频只驱动 ControlNet，不当参考。

### 6. 参考照片会被“照搬”的东西（实测）
- 灰色 / 纯色影棚背景 → 视频跑进灰色影棚。解法：把人物抠出来，放到目标场景的模糊背景上。
- 照片里露齿大笑 → 视频里一直笑到僵。解法：提示词写明“身份照照片，表情不照照片”。
- 木地板、地砖、镜子这类纹理 → 每段长得不一样。解法：只写一种纯色地面。

### 7. 长视频漂移：在低清阶段按通道拉回（lowres_anchor）
- **问题**：一段接一段续写，颜色越来越深、越来越锐、人越来越显老。这是各家 H3 续写方案都有的共性问题。
- **定位**：我们把 16 段缓存里的低清结果和最终结果分开测，发现颜色漂移**在低清阶段就产生了**（饱和度 0.39 → 0.58），高清阶段只加一个固定偏移，不累积。漂移的形状很简单：24 个 latent 通道的均值和幅度慢慢偏移。
- **做法**：每段低清跑完、放大前，把新画面的通道均值和幅度拉回**本镜头第一段**的统计，低清接续数据也一起校正，误差就传不到下一段。
- **结果**：133 秒整片饱和度一直在 0.33–0.36，不开时是 0.34 → 0.48；锐度不再上涨，人不再变老。
- **走过的弯路**：一开始拿“上一段的保留帧”当标准去校正，没效果，因为那几帧本身已经漂了，等于拿漂过的对齐漂过的。**锚点必须选一个没漂的源头。**

### 8. 编码器：32B 和 4B + ClipProj
- 4B + ClipProj 能省十几 GB 显存，画质不受影响，只是对提示词的理解稍弱。从零生成完全可用；同一部片子不要中途换编码器。

### 9. 方法上的经验
- **先查社区，再做测试**：一天只能跑几次测试，别人踩过的坑先看。
- **用数字说话**：每段测饱和度、拉普拉斯锐度，再截脸对比，不靠“感觉好像”。
- **离线分析缓存**：缓存里存着每段的低清和最终 latent，不占显卡就能定位问题出在哪一步。

### 致谢 👍
H3MD 站在很多人的肩膀上。下面这些作者的代码、模型或公开分享的经验给了我们直接的思路，在这里给他们点个赞：

**引擎、模型与节点**
- 👍 [MiniMax](https://huggingface.co/MiniMaxAI) 开源 H3，[Comfy-Org](https://huggingface.co/Comfy-Org/MiniMax-H3) 做了 ComfyUI 原生支持
- 👍 [Luna-H3-Sampler](https://github.com/lunaaispace-eng/Luna-H3-Sampler)：两段式采样引擎（H3MD 内置，GPL-3.0）
- 👍 [LBH-123-AI](https://huggingface.co/LBH-123-AI/Minimax_h3_latent_Upscaler)：学习型潜空间放大器（MIT）
- 👍 [Alissonerdx](https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI)：sharpness 放大器和 lms LoRA
- 👍 [nkxx188/ComfyUI-MiniMaxH3-Easy](https://github.com/nkxx188/ComfyUI-MiniMaxH3-Easy)：高清条带分块的思路（MIT）
- 👍 [Kijai](https://github.com/kijai/ComfyUI-KJNodes)：SageAttention / FeedForward 省显存补丁，DMAD 转换
- 👍 [NicoLab28/ClipProj](https://github.com/nicolab28/ComfyUI-ClipProj)：4B 编码器投影，让小显存也能跑
- 👍 [alibaba-pai Fun ControlNet](https://huggingface.co/alibaba-pai/MiniMax-H3-Fun-Controlnet-Union)、[Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2)、[SAM3](https://huggingface.co/Comfy-Org/sam3.1)：动作复刻的三块基石
- 👍 [smhfacct](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models)（fl2va × ref2va 混合模型）、[t8star](https://huggingface.co/t8star)（curveproj 兼容 LoRA）、[lightx2v](https://huggingface.co/lightx2v/Minimax-h3-Turbo)（Turbo LoRA）、[DMAD](https://huggingface.co/ZhengmingYu/DMAD)、[fal](https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA)（realism people）、[JOKER141](https://huggingface.co/JOKER141/MiniMax-H3-Combat-Base-V2)（Combat LoRA）

**长视频漂移：公开分享经验的作者**
- 👍 [tritant/ComfyUI_MiniMax_H3_Extender](https://github.com/tritant/ComfyUI_MiniMax_H3_Extender)：公开记录“每接一段对比度就上升”，并指出参考图会把颜色带进来
- 👍 [NikoDemon80/ComfyUI-H3-Motion-Context](https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context)：latent 接续代替像素往返，以及“最后一步少加锐度”的思路
- 👍 [mark9009/ComfyUI-H3-LongTake](https://github.com/mark9009/ComfyUI-H3-LongTake)：关键帧锚定和接缝颜色匹配的实测数据
- 👍 [EchoHeadache/comfyui-aex-drift](https://github.com/EchoHeadache/comfyui-aex-drift)：交接匹配、接缝锚定的思路。我们试过之后改成了锚定第一段，但正是它让我们想清楚“锚点要选没漂的源头”
- 👍 [roycho87/clown_context_suite](https://github.com/roycho87/clown_context_suite)：周期性干净锚点的想法
- 👍 dev.to 上 [10 段链式实测](https://dev.to/nefayran/chaining-image-to-video-clips-from-the-last-frame-what-10-links-did-to-a-face-minimax-h3-46e0) 的作者：把“脸会变老、锐度过冲”量化出来

### 特别鸣谢 👍
[t8star] [JOKER141]-Bunny [TE MAN] 等多位大佬长期为社区提供的公益教学, 他们制作了很多杰出的作品和分享了很多思路和经验, 给他们点赞!

谢谢大家把踩过的坑公开出来，H3MD 也会继续把我们的发现分享回社区。

</details>

每个节点和选项的完整说明（英文）：[docs/technical-reference.md](docs/technical-reference.md)。

## 许可证
ComfyUI-H3MD 版权所有 (C) 2026 YJ，按 GNU Affero 通用公共许可证第 3 版或更新版本（SPDX `AGPL-3.0-or-later`）发布，
附加一条：分发或作为在线服务提供时，必须保留明显的署名“基于 H3MD 导演台 / H3MD Director（ComfyUI-H3MD-Director），作者 YJ”。允许商用；修改后通过网络
提供给别人用，必须公开修改版源码。全文见 [LICENSE](LICENSE)，说明见 [NOTICE.md](NOTICE.md)；`h3md_luna/` 里自带的
Luna-H3-Sampler 代码是 GPL-3.0（[LICENSES/](LICENSES/)）。
