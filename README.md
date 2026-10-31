# H3MD Director · H3MD 导演台 (ComfyUI-H3MD-Director)

Long, multi-shot MiniMax H3 video in ComfyUI: plan shots in a visual Director, generate them segment by segment
with a two-pass sampler (low-res → latent lift → full-res, bundled), keep every finished segment on disk, and write
the final video chunk by chunk with optional RIFE frame interpolation — without holding the whole clip in RAM.

在 ComfyUI 里用 MiniMax H3 做长视频、多镜头视频：在可视化的导演台里排镜头，用自带的两段式采样器逐段生成（先低清、
潜空间放大、再高清），每段做完就存到硬盘缓存，最后一段一段读出来写成视频（可选 RIFE 插帧），整段视频不会一次放进内存。

Copyright (C) 2026 YJ · License: AGPL-3.0-or-later + 署名条款（见 [License / 许可证](#license--许可证)）

> H3MD does **not** include any MiniMax H3 weights. The MiniMax H3 model has its own license — read [NOTICE.md](NOTICE.md)
> before you download or use the model.
> H3MD **不包含**任何 MiniMax H3 权重。H3 模型有它自己的许可证，下载或使用模型前请先看 [NOTICE.md](NOTICE.md)。

Contents / 目录: [中文](#中文) · [English](#english) · [Low VRAM / 小显存](#low-vram--小显存) ·
[Models / 模型清单](#models-manifest--模型清单) · [RIFE](#rife-model-manual-download--rife-模型手动下载) ·
[Caveats / 注意事项](#caveats--注意事项) · [License / 许可证](#license--许可证) ·
[Technical reference](#technical-reference-english)

---

## 中文

### 这个节点包做什么
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

### 安装
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

### 快速开始
在 ComfyUI 的 **模板 / Templates** 里找到 `ComfyUI-H3MD-Director` → `H3MD alpha example`（文件在 `example_workflows/`）。
模板不带素材：请自己上传图片/视频、写提示词。缺的模型会在“缺少模型”弹窗里列出，可以直接下载。

```
H3MD Director --plan--> H3MD Loop Sampler (+ H3MD Loop Options --options-->)
H3MD Loop Sampler --long video--> H3MD Save Video Interp
```

第一次建议只跑第一段（`stop_after = 1`），确认画面和速度后改回 0 跑全片（第 1 段从缓存取）。

### 只重跑有问题的段（`rerun_segments`）
片子跑完后如果某几段有问题（跳变、穿帮），不用整片重做：
1. 保持 `resume_from_cache` 打开，其他设置不变；可以改参考图、提示词（**总长和参考图像素尺寸保持不变**，否则自动拆段会变）。
2. 在 Loop 节点最上面的 `rerun_segments` 填要重跑的段，从 1 数，和日志里的“segment 8/16”一致：如 `3`、`4-6`、`8-10, 13-14`。
3. 每一块的开头接上一段的结尾；结尾的 39 帧锁成下一段的旧开头（高清、低清、音频都锁），所以下一段及以后一帧不动。
   选了最后一段，或下一段是硬切时，不锁结尾。连续的几段请一起选（如 8-10），被锁的那一头必须是好的。
4. 其余段直接从缓存取，输出仍是完整视频。被重跑段的旧文件挪到缓存文件夹里的 `_replaced_<时间>` 留底。
5. `rerun_new_seed`（默认开）：重跑的段用新的随机种子，提示词和参考图没改也能抽到不同结果。
开跑前日志会列出每段是“cache”还是“RERUN（head/tail）”；缺缓存时直接报错并列出缺哪几段。

### 长视频越来越浓、越来越锐、人越来越老？
这是 H3 一段接一段续写的共性问题（各家续写节点都有），只能压住、不能根除。实测 16 段：饱和度 0.32→0.46，锐度明显上升。
颜色漂移在**低清阶段**就产生了（模型续写自己的结果），表现为 24 个 latent 通道的均值和幅度慢慢偏移。
* 单一场景的长视频（动作复刻、长镜头）：`H3MD Loop Options` 最后一项 `lowres_anchor` 设 1。每个续接段低清跑完、放大前，
  新画面的通道统计拉回本镜头第一段，低清接续数据也一起校正，偏差不会传到下一段。需要 `continue_lowres_prefix = native_lowres_carry`。
  场景本该变色的叙事视频不要开（硬切后会以新镜头的第一段为准）。
* 其他建议：每段都放参考照片锚定身份和颜色；参考照片不要太浓太锐；提示词少写打光形容词（glowing、radiant 等）；
  特别长的片子在自然切点用参考照片重新起。

### 动作复刻（depth）的规则
* 视频勾了 depth，就**只驱动深度 ControlNet，不当 `<Video N>` 参考**（自动，没有开关）。导演台里它也不占 `<Video N>` 编号。
  原因：黑底剪影当参考时，模型会把黑底或灰色影棚照搬进画面。勾了原声的深度视频例外，仍是 `<Video N>`（日志警告）。
* 勾了 depth 却没接 `H3MD Depth Control`，开跑前直接报错。
* **参考照片不要用纯色/灰色影棚背景**（实测：关掉 `<Video>` 参考后，灰底照片仍会把画面拉灰）。用真实场景照片，或把人物抠出来放在模糊的目标场景上；
  全身照最好不露地板，地板在提示词里只写一种（纯色无纹理最稳）。

### 主要功能怎么用
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
* 位置：`cache_dir` 为空时自动选（环境变量 `H3MD_CACHE_DEFAULT` → 已有的 `H3MD_cache` → 剩余空间最大的本地盘）。
* Loop 自己不会在出片前删缓存。要删就在 `H3MD Save Video Interp` 打开 `delete_cache_after_save`（视频完整写好后才删）。
  1088x1920、243 帧一段大约 3 GB（fp16）。

**`H3MD Save Video Interp`**
* H3 固定 24 fps。开插帧时从 24 插到 `target_fps`；不插帧又把 `target_fps` 设成非 24，声音会对不上（日志会警告）。
* `format`：`video/h264-mp4`（默认）、`video/h265-mp4`、`image/gif`、`image/webp`、`image/png-sequence`（后三种没有声音）。
* chunk 大小按空闲显存/内存自动决定；显存不够会自动减半重试，最后退到 CPU。

---

## English

### What it does
* **Multi-segment continuation**: `H3MD Loop Sampler` runs every shot/segment of the Director plan. Continued shots
  start from the tail of the previous segment's final latent (`overlapIn` 22 or 39 frames, drift: lightly re-noised
  while sampling, restored exactly at the end); hard cuts (`overlapIn` 0) are generated independently. Shots over the
  token budget are auto-split into sub-segments.
* **Two-pass sampling, bundled**: every segment runs through `H3MD Sampler`, built on the bundled Luna-H3-Sampler
  engine (GPL-3.0): the first steps at low resolution, a learned H3 latent upscaler lifts the video, the rest at full
  resolution. Audio is one trajectory across both passes.
* **Automatic high-res tiling**: with `highres_tiling` (default on) each segment measures the free VRAM and runs the
  full frame when it fits, otherwise the fewest strips that fit. On a CUDA out-of-memory error the high-res pass is
  retried with more strips (the low-res result is kept) and the machine remembers it for next time.
* **Disk cache + resume**: with `cache_mode = disk` (default) each segment is decoded and written to disk right away,
  so RAM holds one segment. With `resume_from_cache` on, re-queueing skips finished segments and reuses the base
  seed of the cached run.
* **Streamed save**: `long video` → `H3MD Save Video Interp` reads, (optionally) RIFE-interpolates and encodes the
  frames chunk by chunk, with bounded RAM.
* **Director console**: `H3MD Director` has a built-in visual editor (asset bin, timeline, shot tabs, @-mentions,
  thumbnails on picture tags).
* **Motion transfer**: `H3MD Depth Control` turns a reference video into depth / SAM3 person silhouettes for the
  Fun ControlNet and `<Video N>`.

### Install
1. Put the folder at `custom_nodes/ComfyUI-H3MD-Director`
   (`git clone https://github.com/1113800942/comfy-h3md-director custom_nodes/ComfyUI-H3MD-Director`, or copy it).
2. Install the node packs below, then restart ComfyUI.
3. No extra `pip install`: H3MD only uses torch, numpy, PyAV, Pillow and einops, which ship with ComfyUI.
4. Recommended: a learned latent upscaler in `models/latent_upscale_models/`, e.g.
   `minimax_h3_latent_upscaler_3d_fp16.safetensors` or a fine-tune such as
   `h3_upscaler_sharpness_2000steps_v0.1_fp32.safetensors`. Without one, set `upscaler_model = none` (nearest, softer).

Dependencies:
* **ComfyUI** with the core MiniMax H3 nodes (`comfy_extras.nodes_minimax_h3`), 0.43 or newer recommended.
* **selflift-Avatar is no longer needed**: the two-pass engine is bundled (`h3md_luna/`).
* **ComfyUI-ClipProj** (<https://github.com/nicolab28/ComfyUI-ClipProj>) — clip projection model used by the example.
* **ComfyUI-VFI** (<https://github.com/GACLove/ComfyUI-VFI>) — only for RIFE interpolation in `H3MD Save Video Interp`.
* The example workflow also uses ComfyUI-KJNodes, rgthree-comfy, MiniMax H3 Audio T8, CRT-Nodes and
  ComfyUI-YCNodes-MiniMax-H3; ComfyUI lists any missing pack when the workflow opens.

### Quick start
ComfyUI **Templates** → `ComfyUI-H3MD-Director` → `H3MD alpha example` (file in `example_workflows/`). The template
ships without media: upload your own images/videos and write your prompts. Missing models are listed in the
missing-models popup with download buttons.

```
H3MD Director --plan--> H3MD Loop Sampler (+ H3MD Loop Options --options-->)
H3MD Loop Sampler --long video--> H3MD Save Video Interp
```

Run only the first segment first (`stop_after = 1`) to check the look and the speed, then set it back to 0 (segment 1
comes from the cache).

### Re-render only the bad segments (`rerun_segments`)
When a few segments of a finished video are bad (jumps, glitches), do not re-render the whole video:
1. Keep `resume_from_cache` on and the other settings unchanged; reference pictures and prompts may change (**keep the
   total length and the reference pictures' pixel size**, otherwise the automatic split changes).
2. In `rerun_segments` (top of the Loop node) list the segments, 1-based as in the log ("segment 8/16"): `3`, `4-6`,
   `8-10, 13-14`.
3. Each block starts from the end of the segment before it; its last 39 frames are locked to the start of the cached
   segment after it (high-res, low-res and audio), so that segment and everything after it stay untouched. No tail lock
   for the last segment or before a hard cut. Select neighbouring bad segments together (e.g. 8-10); the locked end
   must be a good one.
4. All other segments come from the cache and the output is still the whole video. Old files of re-rendered segments
   are moved to `_replaced_<time>` inside the cache folder.
5. `rerun_new_seed` (default on): re-rendered segments get a new random seed.
The log lists every segment as "cache" or "RERUN (head/tail)" first; a missing cached segment stops the run with a list.

### Long video getting more saturated / sharper / older-looking?
This is common to every chained H3 continuation; it can be held down, not removed. Measured over 16 segments:
saturation 0.32 -> 0.46 and rising sharpness. The colour drift is born in the **low-res pass** (the model continuing its
own result) as a slow shift of the 24 latent channels' mean and spread.
* One continuous scene (motion copy, a long take): set `lowres_anchor` (last widget of `H3MD Loop Options`) to 1. After
  each continued segment's low-res pass, before the upscale, the new frames' channel statistics are pulled back to the
  shot's first segment, and the low-res carry is corrected too, so the error does not reach the next segment. Needs
  `continue_lowres_prefix = native_lowres_carry`. Not for a story whose colours should change (a hard cut starts a new
  reference).
* Also: give every segment the reference photos; avoid over-saturated / over-sharp reference photos; avoid lighting
  adjectives (glowing, radiant ...) in prompts; restart very long pieces from the reference photos at natural cuts.

### Motion copy (depth) rules
* A video with depth ticked **drives only the depth ControlNet and is not a `<Video N>` reference** (automatic, no
  switch); the Director gives it no `<Video N>` number. Reason: a black-background silhouette as `<Video N>` was copied
  into the picture (black / grey studio). A depth video with its soundtrack on stays a `<Video N>` (warning).
* Depth ticked without `H3MD Depth Control` connected: error before the run.
* **No plain / grey studio backgrounds in reference photos** (measured: with no `<Video>` reference a grey-backdrop
  photo still pulled the video grey). Use real-location photos or cut the person onto a blurred target location; keep
  the floor out of full-body photos and describe one plain floor in the prompt.

### Using the main features
**`H3MD Director`**
* `plan_json` is the single source of truth: the editor reads it and writes every edit back. Editor / JSON toggle top right.
* Asset bin: upload or pick files from `input/`; drag an item onto a shot to assign it.
* Shot lengths are 5+17n frames at 24 fps; a shot with a reference video follows the clip. Width and height snap to
  multiples of 64, so the low-res pass is exactly half size.
* Type `@` or click a tag chip to insert a media tag (`<Picture N>` / `<Video N>` / `<Audio N>`), in backend order.
* **Check what each `<Picture N>` shows before writing its role** (the tag chips show a thumbnail): the person, the
  outfit and the location must each point to the picture that really contains it. Calling an empty scene picture
  "the person" makes the video jump to another background or outfit midway. See `skills/` (EN / 中文).

**`H3MD Loop Sampler` + `H3MD Loop Options`**
* Main node: models, sampler, sigmas, seed, cfg, `transition_step` (low-res steps), `lowres_scale`, `rho` / `w_min` /
  `w_max`, `upscaler_model`, `seed_mode` (default increment: segment k uses seed + k), `highres_tiling`,
  `rerun_segments` / `stop_after` / `rerun_new_seed` (top of the node), `cache_mode`, `resume_from_cache`.
* Advanced switches live in `H3MD Loop Options`, linked to `options`; not linked = defaults. The node shows the common
  options; tick "advanced" to see all.
* Outputs: `latent`, `images` (64x64 placeholder), `audio`, `frame_count`, `summary` (per segment: seed, tiling plan,
  step times), `long video`.
* **Connect `long video` to `H3MD Save Video Interp`** — the only save path designed for bounded VRAM/RAM.

**Disk cache**
* Folders: `resume_<hash>` (stable with `resume_from_cache` on) or `run_<time>_<pid>`.
* Location: with `cache_dir` empty it is chosen automatically (env `H3MD_CACHE_DEFAULT` → an existing `H3MD_cache` →
  the local drive with the most free space).
* The Loop never deletes its cache before the video is saved; use `delete_cache_after_save` in
  `H3MD Save Video Interp`. A 243-frame 1088x1920 segment takes about 3 GB (fp16).

**`H3MD Save Video Interp`**
* H3 is fixed at 24 fps. With `interpolate` on, RIFE goes from 24 to `target_fps`; with it off and `target_fps` ≠ 24
  the clip plays faster/slower and the audio no longer matches (logged).
* `format`: `video/h264-mp4` (default), `video/h265-mp4`, `image/gif`, `image/webp`, `image/png-sequence` (the last
  three have no audio).
* Chunk size follows free VRAM/RAM; on CUDA OOM the chunk is halved and retried, finally on the CPU.

---

## Low VRAM / 小显存

**中文**
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

**English**
* **High-res pass**: keep `highres_tiling` on. It picks the strip count from the free VRAM (up to 12) and runs the full
  frame whenever it fits, so it costs nothing on large cards. On CUDA OOM it retries with more strips, then with
  feed-forward chunking, and only then stops with a readable error.
* **Low-res pass**: not tiled. On OOM the error suggests `ff_chunk_rows = 8192` in H3MD Loop Options (feed-forward in
  row chunks: same result, slightly slower).
* **Reference media do not shrink with more strips**: reference images / videos, audio and text are whole in every
  strip. Measured at 1088x1920 with 2 pictures + 1 reference video: ~37k fixed rows per strip. On **cards under 16 GB
  use a lower resolution** (e.g. 768x1344 or smaller), shorter segments and fewer / smaller reference media.
* **Model weights** are left to ComfyUI's memory management (DynamicVRAM, `--lowvram`, `--reserve-vram N`);
  H3MD LowVRAM LoRA Loader loads LoRAs in token chunks.
* `unload_idle_models` (default on) unloads text encoder / VAEs between the passes and between segments;
  `vram_release = deep` also clears caches between segments.
* The estimate is calibrated on an RTX 5090 32 GB; after the first OOM on your machine it is recorded in
  `user/h3md/tiling_calibration.json` and later runs plan more strips automatically.

## Models manifest / 模型清单

**中文**：示例工作流顶层有一个 `models` 数组（ComfyUI 工作流规范，<https://docs.comfy.org/specs/workflow_json>），每条有
`name`、`url`、`directory`（必填）和 `hash` / `hash_type`（可选）。ComfyUI 前端打开工作流时，如果 `models/<directory>/`
里没有这个文件，就在“缺少模型”弹窗里显示下载按钮。限制：只接受 Hugging Face / Civitai 地址；扩展名只能是
`.safetensors` / `.sft` / `.ckpt` / `.pth` / `.pt`；`directory` 必须是已注册的模型文件夹；**`name` 必须和节点里选的文件名
完全一致**，否则那一条不会显示。

**English**: the example workflow has a top-level `models` array (ComfyUI workflow spec,
<https://docs.comfy.org/specs/workflow_json>): `name`, `url`, `directory` (required), `hash` / `hash_type` (optional).
When a listed file is missing from `models/<directory>/`, the frontend's missing-models popup offers a download button.
Limits: Hugging Face / Civitai URLs only; extensions `.safetensors` / `.sft` / `.ckpt` / `.pth` / `.pt` only;
`directory` must be a registered models folder; **`name` must match the node's widget value exactly**, otherwise that
entry is not shown.

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

Each model has its own license; check its model page before use. / 每个模型有自己的许可证，使用前请看它的模型页。
The `ClipProjApply` entry only shows when ComfyUI-ClipProj is installed. / `ClipProjApply` 那条只有装了 ComfyUI-ClipProj 才显示。

## RIFE model (manual download) / RIFE 模型（手动下载）

* 中文：“缺少模型”弹窗只能下载到 `models/` 下已注册的文件夹，而且不允许 `.pkl`，所以 RIFE 的 `flownet.pkl` 不在
  `models` 清单里，需要手动下载：节点包 ComfyUI-VFI（<https://github.com/GACLove/ComfyUI-VFI>）；模型
  <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip>，解压后把 `flownet.pkl` 放到
  `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl`（相对 ComfyUI 根目录）。ComfyUI-VFI 第一次运行时通常也会自己下载。
* English: the missing-models popup only downloads into registered `models/` folders and does not allow `.pkl`, so
  RIFE `flownet.pkl` is not in the manifest. Download it manually: node pack ComfyUI-VFI
  (<https://github.com/GACLove/ComfyUI-VFI>); model <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip>
  — extract `flownet.pkl` to `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl` (relative to the ComfyUI root).
  ComfyUI-VFI normally downloads it itself on first use.

## Caveats / 注意事项

**中文**
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

**English**
* For large / long videos the `latent` / externally decoded images / `audio` paths do **not** guarantee against
  VRAM/RAM blowups; a one-pass external decode can also differ slightly at the seams. Only `long video` →
  `H3MD Save Video Interp` is designed for bounded VRAM/RAM.
* The `images` output is a 64x64 placeholder (except with `cache_mode = ram`).
* Old workflows: removed options (`merge_images`; Save Video Interp's `preset` / `cache_folder` / `pingpong` / `fps`
  output, etc.) are re-aligned by name and dropped on load, nothing else shifts; `H3MD Loop Sampler (legacy)` is kept
  so old saves load.
* `video/h265-mp4` and 10-bit (`yuv420p10le`) are **untested**.
* The `resume_from_cache` key does not cover models, LoRAs or the upscaler; after changing them turn resume off or
  clear the cache folder.
* This version changed the sampling engine: older caches are not resumed, and an old workflow's `H3MD Loop Options`
  node should be deleted and re-added (its options changed).
* **Text encoder**: 32B (`qwen3vl_32b_minimax_h3_int8_convrot`) follows prompts best. Short on VRAM? Use 4B + ClipProj:
  it saves 10+ GB with no loss in image quality, only slightly weaker prompt understanding (details can differ a little
  from 32B). Fine for generating from scratch. **Keep one encoder for a whole piece**; do not switch when re-rendering
  segments with `rerun_segments`.
* **Fast-motion ghosting**: a fast arm swing can briefly show ghost hands (4 frames are packed into 1 latent step, plus
  few sampling steps). Re-render that segment with a new seed via `rerun_segments`.
* A motion-copy plan is aligned to the reference video (rounded up to 5+17n); trim the frames past the reference video
  after export.

## License / 许可证

**English**: ComfyUI-H3MD is Copyright (C) 2026 YJ and licensed under the GNU Affero General Public License v3.0 or
later (SPDX `AGPL-3.0-or-later`) with one additional term: keep a visible attribution "Based on H3MD Director (ComfyUI-H3MD-Director) by
YJ" when you distribute it or offer it as an online service. Commercial use is allowed; a modified version offered over
a network must publish its source. Full text in [LICENSE](LICENSE), details in [NOTICE.md](NOTICE.md); the bundled
Luna-H3-Sampler code in `h3md_luna/` is GPL-3.0 ([LICENSES/](LICENSES/)).

**中文**：ComfyUI-H3MD 版权所有 (C) 2026 YJ，按 GNU Affero 通用公共许可证第 3 版或更新版本（SPDX `AGPL-3.0-or-later`）发布，
附加一条：分发或作为在线服务提供时，必须保留明显的署名“基于 H3MD 导演台 / H3MD Director（ComfyUI-H3MD-Director），作者 YJ”。允许商用；修改后通过网络
提供给别人用，必须公开修改版源码。全文见 [LICENSE](LICENSE)，说明见 [NOTICE.md](NOTICE.md)；`h3md_luna/` 里自带的
Luna-H3-Sampler 代码是 GPL-3.0（[LICENSES/](LICENSES/)）。

---

## Technical reference (English)

Detailed behaviour of every node and option (kept from the development notes).

Multi-segment MiniMax H3 pipeline nodes (category `MiniMax H3/H3MD`):

| Class | Display name | Purpose |
|---|---|---|
| H3MDDirector | H3MD Director | plan_json -> validated `H3MD_PLAN` (5+17n lengths, overlaps, ref windows, label order); built-in visual editor, see below |
| H3MDSegmentPick | H3MD Segment Pick | raw media of one segment (native-res ref video, single <=15 s window) |
| H3MDSegmentEncode | H3MD Segment Encode | calls ComfyUI core `MiniMaxH3ReferenceToVideo.execute` (official label order) |
| H3MDSegmentContinue | H3MD Segment Continue | final-latent-only handoff, nested noise_mask, H3MD drift (strength 0 = prefix kept unchanged), locked audio, VRAM release between segments |
| H3MDSegmentJoin | H3MD Segment Join | trims overlap frames/audio of the new segment and appends (optional crossfade) |
| H3MDSampler | H3MD Sampler (two-pass) | bundled Luna-H3-Sampler two-pass engine + drift, low-res head, VRAM release between the passes, high-res tiling, per-step log; outputs only the final latent |
| H3MDLoopSampler2 | H3MD Loop Sampler | main loop node: runs ALL segments (Encode -> Continue -> H3MD Sampler -> VRAM release) and joins them; advanced switches via `options` |
| H3MDLoopOptions | H3MD Loop Options | advanced Loop switches (Alpha/Beta prefix, handoff, seams, ref windows, tiling, cache details) -> `options` |
| H3MDLoopSampler | H3MD Loop Sampler (legacy) | the original single node, inputs unchanged, kept so saved workflows load exactly; same computation |
| H3MDSemanticBridge | H3MD Semantic Bridge | chainable Semantic Bridge (six-tensor MLP / trainer MLP / T8 transformer) -> Loop Sampler `semantic_bridge` |
| H3MDLowVRAMLoraLoader | H3MD LowVRAM LoRA Loader | token-sliced bypass LoRA loader (same behaviour as H3LowVRAMBypassLoraLoader) |

### Director editor (Phase 2 UI)
`H3MD Director` has a built-in visual editor (`web/js/h3md_director_ui.js`, logic in `web/js/h3md_plan.js`).
The `plan_json` widget is still the single source of truth: the editor reads it, and every edit writes
formatted JSON back into it. Workflows save as `widgets_values = [plan_json, width, height, continuity_prompt]` (older 3-value workflows
load with continuity_prompt = True),
and the editor widget itself is not serialized and not sent to the backend. Opening a workflow does not
rewrite `plan_json`; the text only changes when you edit something.

* **Editor / JSON toggle** (top right): `JSON` shows the original plain text box (fallback, hand editing).
  `Editor` re-reads the text. Invalid JSON keeps the text untouched and stays in JSON mode with the parse error.
  The mode is stored in the node property `h3md_ui_mode`.
* **Global**: plan width/height (the node's width/height widgets override them when > 0), globalPrompt,
  globalMode (fallback | prepend). fps is fixed at 24.
* **Media bins** (Images / Videos / Audios):
  * `Upload…` (or drop files from the OS onto a bin) stores the file through ComfyUI's `/upload/image` endpoint
    (type `input`, any file type), which puts it in the ComfyUI input folder.
  * The `file in input/` box lists existing input-folder files (type-ahead). `Add` or Enter adds the typed file.
  * Image thumbnails and video first frames come from `/view`. Video duration, size and audio track, and audio
    duration, come from `/h3md/probe`. For a new video, `duration` and `hasAudio` are filled in automatically.
  * The id field renames the item and updates all segment refs and `locked.source`. `✕` removes it from the
    plan and from the segments that use it; the file stays on disk. `♪` toggles `hasAudio`.
  * Drag a bin item onto a segment card to assign it.
* **Timeline bar**: every segment drawn to scale, with overlaps hatched. Click a block to jump to its card.
* **Shot tabs**: shots are a row of tabs (`Shot N`, its seconds and frames; `▶` = length from its video,
  `⇢` continuation, `✂` cut) plus `+`. One settings panel below edits the selected tab: move ◂/▸,
  duplicate ⧉, insert after +, delete ✕. Dropping a bin item on a tab assigns it to that shot. The saved
  plan format is unchanged (a plain `segments` list).
  * **Shot length follows its first reference video** (`<Video 1>`): the clip's duration in seconds from
    `trimStart` to its end (video-stream frames / its own fps, so 30 fps clips are fine; the Director
    re-probes the file), times 24, rounded UP to the next 5+17n: `5 + 17*ceil((ceil(sec*24) - 5) / 17)`.
    Example: a 3.3 s clip = 79.2 f -> 90 f (3.75 s); 3.88 s -> 107 f; 15.14 s -> 379 f. The length field
    is then read-only. Longer than `maxSegmentLength` -> auto-split as before (`ref_window_mode=advance`
    moves the <=15 s window through the clip). A shot without a video keeps its own length (rounded up).
  * **Continued shots (overlapIn > 0) get overlap + content**: the first `overlapIn` frames of a continued
    shot are the previous shot's tail and are trimmed at the join, so a video-driven continued shot is
    generated as `aligned_up(overlapIn + clip frames)` and its **new content is never shorter than the clip**
    (the timeline / total count only the new part once). If that is over the max length (396, or the shot's
    own `maxSegmentLength` with auto-split), the overlap is first shrunk through 5+17k values (39 -> 22, never
    below 22); still over -> auto-split splits the shot (keeping the requested overlap) **only if that is worth a
    split** (see "no sliver splits" below); with auto-split off
    the shot stays over 396 at overlap 22 and the editor warns ("turn on auto-split or trim the reference
    video"). The editor stores a shrunk overlap in `overlapIn` and the user's choice in `overlapRequest`
    (picking/dragging an overlap clears it). The reference window still starts at `trimStart`, i.e. it lines
    up with the first NEW frame (frame `overlapIn` of the shot). Hard cuts and shots without a video ref
    (manual length, which already includes the overlap) are unchanged. Example, six 15 s clips continued with
    overlap 39: shot 1 = 362 f; shots 2-6 = 396 f at overlap 22 (39 + 360 = 413 > 396) = 374 f / 15.58 s new
    each; total 2232 f = 93.0 s. Tabs show `+15.58s new · 396f`; the card shows generated and new length.
    Old saved plans load; their continued video shots are re-lengthened (a note says so) and re-render.
  * **No sliver splits** (auto-split on, video-driven shots, 2026-10-17e): a shot that does not fit the max even
    at overlap 22 (0 for a first shot / hard cut) is auto-split only when the clip runs at least **48 frames
    (2 s)** past what one capped shot covers (max - 22 new frames), and every split piece then carries at least
    48 new frames. Otherwise the shot is **capped at the max** (396 at overlap 22, or 396 for a cut) and the last
    frames of the clip are dropped, with a note ("capped at 396 frames (overlap 22), no auto-split: the clip is
    only 10 frames over ... dropped"); the editor shows it on the length line. Why 2 s: it is the shortest
    reference window H3 accepts (MIN_REF_SECONDS), and a split costs a whole extra generation pass plus a seam,
    which is not worth less motion than that. Examples (max 396): 16.07 s clip continued -> 396 f / 22, 15.58 s new,
    0.48 s dropped; 17.5 s -> capped (46 f over); 17.6 s -> split 2 x at 39 (48.4 f over); 20 s -> split.
    Manual lengths, auto-split off and per-shot max 0 are unchanged.
  * `length` in frames rounds up to 5+17n. `sec` converts seconds, rounded up to 5+17n.
  * `overlapIn` choices are the same fixed steps for **every** continued shot: **0 (hard cut), 22 or 39 frames**
    (only steps shorter than both lengths are offered). Timeline drag snaps to the nearest step. When 39 + the
    clip would exceed the max length the overlap is shrunk to 22 (shown as `39f → 22f (fits 396f)`; the choice
    stays 39). Older plans with other 5+17k overlaps (5, 56, 107 ...) still load and run as saved, with a
    warning; choosing a step replaces them. The Director accepts the same set (others: WARNING note).
    A click on a timeline block (or 1-2 px pointer jitter) never edits the plan, and dropping a shot where it
    already is keeps its choice (before 2026-10-17d a click on a shot whose 39 had been shrunk to 22 stored an
    explicit 22). The editor notes when a chosen 22 does not fit either (the shot is split anyway) and when a
    clip runs longer than its <=15 s window (the shot follows the whole clip).
  * `startFrame` is computed (previous start + length − overlapIn). It is only written back if the plan
    already had the key.
  * Images and audios are chips; the selected order is the label number (◂ ▸ reorders).
  * Each video has trimStart/dur (clamped to 2–15 s and to the source length) and a soundtrack checkbox.
  * audioMode is reference | locked | none. locked adds a source (video:id / audio:id) and an offset.
* **Label preview**: the exact `<Picture N>` / `<Audio N>` / `<Video N>` tags the backend will produce:
  images first, then each video's soundtrack `<Audio>` right before its `<Video>` (only if audioMode ≠ none,
  useSoundtrack and hasAudio), then standalone audios. Click a tag to insert it at the prompt cursor.
* **Validation**: the same checks as `parse_plan`, with the same message text and order, plus editor hints:
  * missing files
  * hasAudio/duration that disagree with the file (one-click `fix`)
  * a ref window longer than the segment
  * more refs than `H3MD Segment Pick` outputs
  * prompt tags that do not exist in the segment
  * refs not mentioned in the prompt (info only)

`h3md_plan.js` ports `parse_plan` / `compute_labels`. If you change the backend rules, change both files.
`tests/js/parity.py` checks them against each other.

#### Editor helper routes (read-only, `h3md_routes.py`)
* `GET /h3md/files?kind=image|video|audio`: media files under the input folder (recursive, relative paths).
  If this route is missing, the editor falls back to the LoadImage/LoadVideo/LoadAudio file lists.
* `GET /h3md/probe?file=<plan file>`: `exists`, `duration`, `hasAudio`, `width`, `height`, `fps`. Relative
  paths must stay inside the input folder.

### Shots, automatic sub-segments and the timeline (phase 3)
* `plan_json["segments"]` entries are **shots**. Shot-to-shot seams follow the overlap rule:
  `overlapIn > 0` = **continuation** (the new shot starts with the tail of the previous shot's FINAL
  two-stage latent, copied overlap is trimmed on join); `overlapIn == 0` = **hard cut** (generated
  independently, no previous latent, no trimming, joined end to end). No switch needed.
* Optional plan field `"maxSegmentLength"` (5+17n, e.g. 396) turns on **auto-split**: a shot longer than it is
  expanded by the Director into near-equal continuation sub-segments (all 5+17n, each <= max) joined by
  `"subOverlap"` frames (default 39), covering the shot length exactly (`1433f -> 396+396+379+379`).
  Per shot: `"maxSegmentLength": 0` never splits that shot; `"subPrompts": ["", "part 2 text"]` overrides the
  prompt of individual parts (empty = shot prompt). Sub-segments inherit the shot refs; locked audio offsets
  advance with each part. Without `maxSegmentLength` a plan parses exactly as before (tested against the
  phase 2 parser on 6000+ plans).
* Director `continuity_prompt` (default on): every continued segment (shot seam or auto part) gets a short
  continuity sentence prepended (`continuity_note()`); cuts and the first segment are untouched.
* Editor timeline: shots are draggable blocks on a ruler (24 fps). Drag a block onto the previous one =
  continuation (hatched overlap, label `continue Xf`, snapped to 5+17k); drag it to just touch = `cut`; a gap
  snaps back to touching. Drag the right edge = length (5+17n). Dashed lines/hatches inside a block = the
  automatic sub-segments. `+ Shot` appends a 10 s hard cut. `Split shot…` (manual option) replaces a shot by
  separate shots using the same split math, e.g. "60 s, max 396, overlap 39" -> 4 shots.

### Two-node layout (`h3md_loop_split.py`): H3MD Loop Sampler + H3MD Loop Options
* **H3MD Loop Sampler** (`H3MDLoopSampler2`): rerun_segments, stop_after, rerun_new_seed (top), plan, clip, vae, audio_vae, low_res_model, sampler, sigmas, seed, the
  sampler widgets (cfg, transition_step, lowres_scale, rho, w_min, w_max, upscaler_model), seed_mode, decode_output;
  optional high_res_model, highres_tiling, cache_mode, delete_cache,
  resume_from_cache, `semantic_bridge` (link), `options` (link, `H3MD_LOOP_OPTIONS`), `depth_control` (link).
* **H3MD Loop Options** (`H3MDLoopOptions`): ref_image_size, continue_audio, drift_strength, drift_lock_tokens,
  audio_crossfade_ms, release_vram_between_segments, tiling_axis, tiling_overlap, ff_chunk_rows, release_upscaler,
  unload_idle_models, cache_dtype, cache_dir, unload_models_before_merge, continue_lowres_prefix,
  merge_overlap_source, seam options, seam_decode_context, vram_release, slow_step_guard (+ any future non-main widget).
* `options` not connected = every advanced input at its widget default; the run (frames, latent, summary, cache
  key) is identical to the legacy node with the same values.
* **Legacy**: class id `H3MDLoopSampler`, labelled "H3MD Loop Sampler (legacy)". Since 2026-10-30 (start_index /
  end_index / previous_latent removed, rerun inputs added at the top) workflows saved before that date must be
  re-created or migrated (`widgets_values` are positional).
  Example: `example_workflows/H3MD alpha example.json`.

### H3MD Loop Sampler (`h3md_loop.py`)
Runs every segment of the plan (or segments 1..`stop_after`; `rerun_segments` re-renders only the listed ones with a
head + tail lock and takes the rest from the cache): Encode (official encoder, native-res
refs) -> Continue for continuation seams and locked audio (handoff = previous FINAL latent `samples` only, H3MD drift)
-> H3MD Sampler -> VRAM release between segments. Then joins everything (`latent`, and with `decode_output` the
frames / audio through the disk cache). `summary` is JSON with per-segment seam / trim / seed / tiling plan / step
times. `seed_mode` increment (default, seed + segment index) | same; with `resume_from_cache` the base seed stored in
the resume folder (`run.json`) is reused. Interrupt is checked between segments and decodes.

#### Loop outputs: what to connect / Loop 输出怎么接 (2026-10-19)

**中文**
* `latent`（拼好的音视频 latent，接缝处的重叠已去掉）可以接到外部节点做放大或自定义处理，例如
  `Separate AV Latent` → Hyper-VAE 或 VAE 解码器 → VHS Video Combine 或其他节点。
* `audio`（整段拼好的音频，和 H3MD Save Video Interp 写进文件的是同一段）也可以接到外部节点自定义处理。
* `images` 只是一张 64x64 占位图（`merge_images` 已删除，Loop 不再把整段视频拼进内存）。要拿到画面帧，
  请在外部解码 `latent`。（`cache_mode=ram` 例外：`images` 仍是内存里的整段视频。）
* **警告**：大/长视频走 `latent` / 解码出来的图片 / `audio` 这些路径，**不保证**显存/内存不爆——在外部把整段
  `latent` 解码，会把整段视频放进显存/内存。外部一次解码整段 latent，接缝处也可能和 `long video` 出的片子略有不同
  （`long video` 用的是逐段解码 + 接缝处理后的帧）。
* 只有 **`long video` → H3MD Save Video Interp** 是按有限显存/内存设计的（从磁盘缓存一段一段流式处理）。

**English**
* `latent` (the joined audio-video latent, seam overlaps removed) can be connected externally for upscaling or
  custom processing, e.g. `Separate AV Latent` -> Hyper-VAE or a VAE decoder -> VHS Video Combine or other nodes.
* `audio` (the joined audio of the whole clip - the same audio H3MD Save Video Interp writes) can be connected
  externally for custom processing.
* `images` is only a 64x64 placeholder (`merge_images` was removed; the Loop no longer joins the clip in RAM).
  To get frames, decode `latent` externally. (`cache_mode=ram` is the exception: `images` is still the whole clip
  in RAM.)
* **Warning:** for large / long videos the latent / decoded-image / audio paths do **not** guarantee against
  VRAM/RAM blowups - decoding the full latent externally holds the whole clip in memory. A one-pass external decode
  of the joined latent can also differ slightly at the seams from the `long video` result (which uses the
  per-segment decoded frames with the seam handling).
* Only **`long video` -> H3MD Save Video Interp** is designed to stay within bounded VRAM/RAM (streamed chunk by
  chunk from the disk cache).
* The same text is in the Loop nodes' DESCRIPTION and output tooltips (`OUTPUT_TOOLTIPS`, hover an output dot), and
  as a Note node in `example_workflows/H3MD alpha example.json`.

**RIFE 模型（手动下载） / RIFE model (manual download)**
* 中文：ComfyUI 的“缺少模型”下载弹窗只能下载到 `models/` 下已注册的文件夹，而且不允许 `.pkl` 文件，所以 RIFE 的
  `flownet.pkl` 不放进工作流的 `models` 清单，需要手动下载：节点包 ComfyUI-VFI
  (<https://github.com/GACLove/ComfyUI-VFI>)；模型 <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip>，
  解压后把 `flownet.pkl` 放到 `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl`（相对 ComfyUI 根目录）。
  ComfyUI-VFI 第一次运行时通常也会自己下载这个 zip。
* English: the frontend's missing-models popup only downloads into registered `models/` folder keys and only
  `.safetensors/.sft/.ckpt/.pth/.pt` files, so RIFE `flownet.pkl` is not in the workflow `models` manifest. Download
  it manually: node pack ComfyUI-VFI (<https://github.com/GACLove/ComfyUI-VFI>); model
  <https://huggingface.co/hzwer/RIFE/resolve/main/RIFEv4.26_0921.zip> - extract `flownet.pkl` to
  `custom_nodes/ComfyUI-VFI/rife/train_log/flownet.pkl` (relative to the ComfyUI root). ComfyUI-VFI normally
  downloads this zip itself on first use.

#### Per-segment disk cache (`h3md_cache.py`, default `cache_mode = disk`)

With `decode_output` on, each segment is decoded **right after it is sampled** and written to
`<cache root>/run_<time>_<pid>_<n>/`. The cache root is `cache_dir` when set; when empty it is picked automatically
(no hard-coded drive): (1) the `H3MD_CACHE_DEFAULT` environment variable if set; (2) an existing `<drive>/H3MD_cache`
that already holds `resume_*` folders (newest wins), so `resume_from_cache` finds its folder after a ComfyUI restart;
(3) otherwise `<drive>/H3MD_cache` on the local drive with the most free space (Windows: fixed drives only, removable /
network / optical skipped; Linux/macOS: home and real local mounts such as `/mnt/*`, `/media/*`, `/Volumes/*`), created on
demand. The chosen path and its free GB are logged. If nothing is writable it falls back to `<ComfyUI temp>/h3md_cache`
with a warning (temp may be moved or deleted on restart, so resume may not find it there). Files written:
`seg_###_frames.npy` (fp16, or uint8 with `cache_dtype`, written chunk-wise via a memmap),
`seg_###_audio.pt`, `seg_###_latent.pt` (the final two-stage latent), and `seg_###.json` (written last; it marks
the segment as complete and logs the sizes). The decoded tensors are freed (del + gc + soft_empty_cache) before the next
segment, so RAM holds one decoded segment at a time. Between segments the base model stays loaded (VRAM policy unchanged).

After the last segment, `unload_models_before_merge` (default on) calls `unload_all_models()` + `soft_empty_cache()`
(sampling is done, base UNet included). Since 2026-10-18 (late) the frames are **not** read back into an IMAGE any more:
`images` is a 64x64 placeholder and the `long video` output hands the cache + merge plan to H3MD Save Video Interp,
which reads them memory-mapped chunk by chunk (see below). The merge rules are unchanged: continuations drop their
overlap frames, cuts drop nothing, and the first segment in range is never trimmed (as in RAM mode).
Audio is joined with `join_audio()` (byte-identical to the audio part of `join_segments`, crossfade included) and is
still output on `audio`.

* `delete_cache`: hidden and ignored since 2026-10-18 (the Loop never deletes its cache). The run folder is removed
  only by H3MD Save Video Interp `delete_cache_after_save`, after the video file is complete. On any error or
  interrupt the cache is **kept** and its path is logged.
* `resume_from_cache` (default off, opt-in) uses a stable folder `resume_<settings hash>` and skips every segment whose
  key matches. The key covers the segment (prompt, refs, windows, length/overlap, media file size+mtime), its seed, the
  scalar sampler/continue settings, sigmas, and, for continuations, the key of the previous segment. Models, LoRAs and
  the upscaler are **not** part of the key, so after changing them turn resume off or clear the folder. To resume after
  a crash, the earlier run must have had `resume_from_cache` on (crash = cache kept). Delete the folder by hand when done,
  or let H3MD Save Video Interp with `delete_cache_after_save` on remove it.
  * **Per-segment keys**: the folder name and the keys of first / hard-cut segments ignore the options that only
    change continued segments (audio/drift, `continue_lowres_prefix`,
    `merge_overlap_source`/`ref_window_mode`/`seam_tone_*`), so A/B tests of those options resume the unchanged first
    segment. Continued segments still use the full settings + the previous segment's key. A hard cut with locked
    audio uses the full settings too. Beta
    (`native_lowres_carry`) re-renders a cached cut segment that was written without its low-res carry. Caches
    written by older versions use the old folder name and are not resumed.
* Disk use per 396-frame segment at 1472x1088: about 3.8 GB fp16 or 1.9 GB uint8. `cache_mode = ram` restores the old
  behaviour (decode + join in RAM after all segments).

#### Continued segments: low-res overlap prefix (`continue_lowres_prefix`, default `native_lowres_carry`)

**Default since 2026-10-18: `native_lowres_carry`** (best in the T01 speckle tests; `pixel_reencode` = previous
default, still available).  Saved workflows keep the value stored in their widgets - switch old nodes by hand.

Without a low-res head the engine builds the low-resolution pass's kept prefix by resizing the full-res final latent in latent
space; for H3 (16x VAE) that is not a valid low-res latent, decodes washed-out/ghosted, and the low-res stage
continued that look for the whole new segment.  With `pixel_reencode` the loop decodes the overlap frames of
the previous FINAL latent (disk mode reuses the already-decoded frames), downscales them in pixel space and
VAE-encodes them; only the low-res stage's kept anchor is replaced, the high-res anchor and the output prefix
stay the exact previous latent.  Log: `[H3MD Loop] ... low-res overlap prefix re-encoded ...` and
`[H3MD Sampler] low-res pass: kept prefix ...`.  `latent_resize` = the engine's own resize (A/B).

**`native_lowres_carry` (default; was BETA).**  The loop also captures each segment's low-res pass endpoint (the engine's `low_res_latent`,
VAE-native space, native low-res grid).  A continued segment pins its low-res overlap prefix from the tail
of that carry (no resize; tail must sit on the 5-token grid, 22 f = 7 tokens) and its high-res prefix from the
previous final latent as before.  Stored in the disk cache as `seg_###_lowres.pt` (~18 MB per 396 f) and reused by
resume; the mode is part of the cache key.  No carry / grid mismatch -> warning and
fallback to `pixel_reencode`.  Log: `[H3MD Loop] segment N: low-res overlap prefix = native low-res carry (1, 24, 7, h, w)`.

#### Seams and reference windows (`h3md_seam.py`, `h3md_refwindow.py`; last five Loop Sampler inputs)
* **`merge_overlap_source`** (default `next`; `prev` = old behaviour).  At a continuation join both segments contain
  the overlap frames.  The H3 video VAE decodes in 7-token windows (5-token stride) and blends each window with the
  next, so a clip's last ~5 frames are decoded without following context (xmarre, CONTINUUM_DECODE_CONTEXT.md -
  idea only).  `next` keeps the NEW segment's decoded copy (it has the following frames as context) and drops the
  previous segment's last `overlap` frames instead of the new segment's first.  Frame count, audio and the joined
  latent are identical in both modes; hard cuts are joined end to end as before (a cut segment that is followed by
  a continuation gives up its tail instead).  RAM and disk-cache merges behave the same; only the merge changes, so
  it is not part of the cache key.
* **`ref_window_mode`** (default `static` = old behaviour).  `advance`: for automatic sub-segments of a long shot,
  each sub-segment's reference video window starts at `trimStart + (sub.startFrame - shot.startFrame - shot.overlapIn)/24`
  (>= 0), computed from the shot's first new frame and snapped to the 24 fps grid; the video's soundtrack follows the same window, standalone
  reference audios start at the same offset (Director hook `audioStarts`).  Still one window per reference, <= 15 s,
  loaded at native resolution (never resized to the canvas).  If the source is too short for the advanced window
  (or its duration cannot be read) that reference stays static with a warning.  Log:
  `[H3MD RefWindow] segment 2 (shot 1.2/2): video 'v1' window 0.000+15.000s -> 4.958+15.000s ...`; the summary
  carries `ref_window` per sub-segment.
* **`seam_tone_match`** `off` (default) | `luma` | `color`, `seam_tone_strength` (0-1, default 1),
  `seam_tone_global` (0-1, default 0).  After decode, at continuation joins only: the two copies of the overlap are
  compared content-aligned over the <= 12 frames nearest the visible seam on a heavily blurred 16x16 grid (per-cell
  median -> robust to motion).  `strength x delta`, clamped to +-0.04 per channel, is added to the new segment at the
  seam and eases out (raised cosine) over 18 frames, to 0 or to an optional whole-segment luma offset
  (`global x delta_luma`, clamped to +-0.015).  Pixels stay in [0,1]; latents and audio are untouched.  Disk mode
  writes the corrected frames (the low-res prefix still uses the uncorrected decode) and resume re-reads the cached
  tail.  Log: `[H3MD Seam] segment N: seam_tone_match luma window 12f ... measured dRGB [...] dY ... -> offset ...;
  residual ...`; the summary carries `tone` per corrected segment.
  * The overlap prefix is restored exactly (drift), so the overlap copies are the same latent and that comparison is always ~0.
    There the visible step is where the kept frames end and the generated frames begin (frame `overlapIn` of the new
    segment): the last 6 kept frames are compared with the first 6 generated frames of the same decode (same grid,
    per-cell median), minus the change the kept window's own trend predicts (bounded), and the offset is applied to
    the generated frames only (full at the first generated frame, easing out over 18 frames to the optional global
    offset). Log: `seam_tone_match luma (static_lock boundary, frame 22, window 6f) ...`.
* Defaults leave the cache key unchanged, so caches written before these options still resume.

#### Semantic Bridge (`h3md_semantic_bridge.py`; node `H3MD Semantic Bridge` + Loop Sampler input `semantic_bridge`)
External, chainable like LoRA loaders:
`H3MD Semantic Bridge (A) -> bridge_chain -> H3MD Semantic Bridge (B) -> ... -> H3MD Loop Sampler.semantic_bridge`.
Not connected = off: behaviour and segment-cache key unchanged.
* Node inputs: **`bridge`** (any supported file in `models/semantic_bridge`, recursive, incl. `t8_compat/`),
  **`strength_mode`** `auto` (default) | `manual`, **`strength`** (alpha, default 0.10, used in manual),
  optional **`bridge_chain`** (`H3MD_SEMANTIC_BRIDGE`). Output: the ordered chain (max 8; the same file content
  twice is refused).
* `auto` strength: the file's fixed `application_contract` (T8 comic-combat: 1.0), the T8 author preset pinned to
  wushu v1's SHA-256 (0.12), 0.10 for six-tensor MLP bridges; a transformer bridge with neither needs `manual`.
  `manual` on a file with a fixed contract must equal the contract, else the node errors.
* What it does: right after Segment Encode, before Continue, on EVERY segment (first, continuation, hard cut),
  the positive text conditioning `h [B,T,5120]` goes through the chain in order (earlier node first). Each stage:
  `pred = net(h/rms(h))`, rescaled per token to `rms(h)`, `h = h + alpha*(pred-h)`, cast back to the conditioning
  dtype; the next stage gets that output. Serial, order matters, not an additive LoRA merge. Negative, latents,
  audio untouched. Conditioning already carrying a bridge tag is refused.
* Layouts (strict key/shape check; anything else is not listed / errors):
  `legacy_mlp` six-tensor fc1/fc2/fc3 5120-512-512-5120 (Original v1, BUNNY; the original formula, eps 1e-8),
  `trainer_mlp` (net.fc*, arch=mlp, optional residual), `trans` (net.in_proj + pre-norm GELU Transformer encoder
  + LayerNorm + out_proj, sinusoidal positions, optional residual; whole sequence = cross-token; T8 comic-combat,
  wushu v1).
* Parity: `trans` single and chained = bit-identical to T8 1.97.0 (`apply_bridge` / `BridgeStack`) with the real
  comic-combat and wushu files; six-tensor MLP = bit-identical to the original six-tensor package, and differs from T8's
  implementation only by bf16 rounding (T8 uses eps 1e-6 and 256-token chunks; mean |diff| ~5e-8).
* CPU fp32 only, no VRAM: about 10-60 ms per stage per segment (200-1200 tokens). All stages are loaded and
  checked on the node; the Loop re-checks the files' SHA-256 before the first segment and stops if one changed.
* Log: `[H3MD SemanticBridge] chain: a (trans, alpha 1) -> b (legacy_mlp, alpha 0.1)` on the node; on the Loop
  `[H3MD SemanticBridge] ON (2 bridges, CPU): ...` once and per segment
  `[H3MD SemanticBridge] segment N/M: 2 bridge(s) applied to 1 tensor(s), T token(s); cos(native, bridged) 0.97..`;
  summary `semantic_bridge` per segment; `... NOT applied ...` = no 5120-dim tensor found.

### H3MD drift (own implementation)
Per sampler step, for overlap-prefix tokens that the static mask keeps (mask 0):
`mask = ramp_weight(k) * sigma_next / sigma_current`, ramp_weight = drift_strength * (n-k)/n for
k < n = prefix_tokens - drift_lock_tokens, 0 otherwise. The `drift_lock_tokens` tokens nearest the seam stay
0; audio masks stay static; generated tokens are never touched. At the last step sigma_next = 0, so the
prefix ends fully kept (and the samplers restore it exactly).

### High-res tiling (`h3md_tiled_hires.py`) and feed-forward chunking (`h3md_ffchunk.py`)
With `highres_tiling` on (default) the high-res pass of every segment runs through an OUTER_SAMPLE wrapper on the
high-res model. `plan_tiles` measures the workspace (free + reclaimable VRAM - weights - ComfyUI's reserve) and picks
the fewest strips (1..12, along `tiling_axis`, overlap `tiling_overlap` latent px) whose calibrated estimate fits;
1 strip = the plain full-frame pass, untouched. N > 1 strips = the **joint** method: one full-frame sampling run, every
model evaluation split into the strips (DIFFUSION_MODEL wrapper) and the video predictions feather-blended over the
overlaps before the sampler step; the continuation mask stays the sampler's own full-frame denoise mask; keyframe
latents are cropped, refs / text / audio stay whole, RoPE positions are the full frame's; the audio part of the mask
is set to 0, so audio stays at the clean audio handed over from the low-res pass. ComfyUI reserves memory for the
largest strip (PREPARE_SAMPLING wrapper). Log tag `[H3MD Tiling]`.

Out-of-memory ladder: CUDA OOM in the high-res pass -> free memory -> re-plan with more strips (the low-res result is
kept) -> ... -> the most strips -> + feed-forward chunking (`OOM_RETRY_ROWS` 8192) -> `HighResOutOfMemory` with a
readable message. Every run is recorded in `<user dir>/h3md/tiling_calibration.json` (per GPU name and DynamicVRAM
on/off); a recorded OOM caps that machine's limit at 0.65 x its ratio.

Calibration (`MEASURED_RUNS`, RTX 5090 32 GB, DynamicVRAM, sol_attn): need = 0.55 x ComfyUI's memory_required formula;
accepted when need / workspace <= the largest ratio that really ran (+0.5%) and < 65% of the smallest that failed;
strips with more rows than any verified run get 0.9 x that limit. Without DynamicVRAM: raw formula within 110%.

`ff_chunk_rows` (H3MD Sampler / Loop Options, default 0 = off): the H3 DiT feed-forward runs on that many sequence rows
at a time (same result, lower peak memory), both passes. Technique credit for the strip layout:
nkxx188/ComfyUI-MiniMaxH3-Easy (MIT), notice in the module header.

### H3MD Save Video Interp (`h3md_stream_save.py`, class `H3MDSaveVideoInterp`, 2026-10-18)

**中文（简要）** 长片子不再整片放进内存：从 H3MD 的分段缓存一段一段读帧，（可选）用 ComfyUI-VFI 的 RIFE 模型插帧，
插完立刻写进 mp4（H.264 + AAC），再释放这一小段。
接法：`H3MD Loop Sampler` 的输出 `long video`（原名 `segments`，同一个位置）接到 `H3MD Save Video Interp`；
这一个节点代替原来的 RIFE Frame Interpolation + Create Video + Save Video。
**2026-10-18 晚起 Loop 不再把整段视频拼进内存**：`merge_images` 开关已删除，`images` 输出只是一张 64x64 的占位图；
视频只从 `long video` 出。旧工作流照常打开（存过 `merge_images` 的、没存过的都不错位），但 `images` 接着 RIFE /
Save Video 的话出来的不是视频，运行时日志会警告一次，请改接 `long video` → H3MD Save Video Interp。
（`cache_mode=ram` 例外：仍在内存里拼好从 `images` 出，`long video` 为空。）`crf` 新建节点默认 19（和 Video Combine 一样）。
* 第一个开关 `interpolate`：关 = 不插帧；开 = 用 RIFE 插帧。插帧选项（`scale`、`model_name`、`batch_size`、
  `use_fp16`）在关的时候变灰。`source_fps` 只是一行只读显示“24 (fixed)”（H3MD 永远是 24 fps，不是输入）。
* `target_fps`（默认 24）永远可改，**就是写出文件的帧率**。注意：`interpolate` 关、`target_fps` 又不是 24 时，
  帧不变只是播放快/慢了，**声音会对不上**（日志警告，写明快/慢多少倍）。
* `format`：`video/h264-mp4`（默认）、`video/h265-mp4`、`image/gif`、`image/webp`、`image/png-sequence`。
  不适用的选项会变灰：`crf`/`pix_fmt`（yuv420p / yuv420p10le）只给 h264/h265；`loop_count`（0 = 无限循环）只给
  gif/webp；`trim_to_audio`（片长截到音频长度）只给 mp4（只有 mp4 有声音）；`save_output` 关时 `output_folder` 变灰。
* 最上面两行是 `filename_prefix`、`output_folder`（文件名和保存位置放在一起），下面才是 `interpolate` 等选项；
  `delete_cache_after_save` 和缓存路径行仍在最下面（播放器上方）。
* `save_metadata`：把 prompt/workflow 写进文件（mp4 标签；png 序列第一帧；gif/webp 另存同名 `.png`）。
* `save_output` 关 = 写到 ComfyUI temp 文件夹（节点里照样能预览）。
* 节点最下面有自己的预览播放器（固定高度，播放/暂停、拖动进度、静音/音量，黑边等比显示）。
* 输出：`video_path`（STRING）、`frame_count`（INT）、`video`（VIDEO，可接 ComfyUI 核心的 Get Video Components /
  Save Video；webp 和 png 序列时为空）。原来的 `fps` 输出已删除，旧工作流里接在 `fps` 上的线加载时会被断开。
* 每次处理多少帧（chunk）全自动：每个 chunk 之前查一次空闲显存，最多用 90%
  （按分辨率、scale、fp16、batch 保守估算 RIFE 显存），最少 8 帧，另有内存上限。
  显存不够（CUDA OOM）时：清缓存，chunk 减半重试（最少 8）→ 8 帧还不够：让 ComfyUI 卸载模型、清显存，
  `batch_size` 改 1 再试 → 还不够：这一段改用 CPU 插帧（很慢，日志里写明）。成功后之后的 chunk 不再变大。
* `delete_cache_after_save` 默认关（保留缓存）；打开时只在视频文件完整写好之后才删缓存。
  它下面一行只显示缓存文件夹的绝对路径（没有标签，太长截断，鼠标悬停看全路径和状态 kept / deleted after save）。
* 已删除：`preset`（用编码器默认）、`source_fps` 输入、`cache_folder` + `folder_overlap_source` +
  `folder_audio_crossfade_ms`（不再支持不接 Loop 直接从文件夹出片）；`long video` 现在必须接。
  `pingpong`（v2 里加过，同日又删除）。以前所有布局存的旧工作流（2026-10-18 原版、带 pingpong 的 17 值 v2、
  去掉 pingpong 的 16 值 v3）照常打开、按名字对齐，删掉的选项值丢弃；2026-10-18 原版里“插帧关”的存档仍按 24 fps 出片。
* 失败/中断：删掉未完成的 `.part` 文件（png 序列删掉未完成的文件夹），缓存保留。Loop 自己永远不在出片前删缓存。

**English.** Saves the H3MD Loop result straight from the per-segment disk cache, chunk by chunk:
read merged source frames (memory-mapped, exactly the frames/spans `SegmentCache.merge` would put into the IMAGE
output, incl. `merge_overlap_source` and trims), optionally interpolate them with ComfyUI-VFI's RIFE model, encode
them into the open mp4 and free them. Peak RAM ~ one chunk (+ x264 look-ahead), not the whole clip.

Wiring (replaces `RIFE Frame Interpolation` + `Create Video` + `Save Video`):

```
H3MD Loop Sampler --long video--> H3MD Save Video Interp
```

* **The Loop never concatenates the clip in RAM** (since 2026-10-18 late; both Loop nodes, `cache_mode=disk`):
  `images` (slot 1, IMAGE) is a 64x64 placeholder, `audio` is still the joined audio, `frame_count` the real count.
  The 6th output **`long video`** (slot 5, type `H3MD_SEGMENTS`; was named `segments` - same slot and type, so saved
  links still connect; `None` without a disk cache) carries the cache folder + merge plan.
  When `images` is linked to anything in the queued prompt, the Loop logs one warning per run:
  `'images' no longer carries the video (64x64 placeholder): connect 'long video' to H3MD Save Video Interp`.
  `cache_mode=ram` keeps the old in-RAM join (`images` = clip, `long video` = None, logged).
* `merge_images` (a main-node widget for a few hours on 2026-10-18) was **removed**. It was the last widget, so a
  workflow saved with it has one extra trailing `widgets_values` entry that the positional load ignores; workflows
  saved before it existed have the current length. Nothing else shifts (hidden `delete_cache` keeps its slot). A
  stale unlinked `merge_images` input slot from such a workflow is dropped on load, and an API prompt that still sends
  `merge_images` is accepted and ignored (logged). `web/js/h3md_long_video*.js` also relabels the saved output name
  `segments` -> `long video` on load.
* The save node's input id stays **`segments`** (saved links and API-format prompts address inputs by this name;
  renaming it would break them); only its label shows `long video` (`web/js/h3md_long_video*.js`).
* Inputs, in order (2026-10-19 layout): required `segments` (label `long video`), then the widgets
  `filename_prefix`, `output_folder` (the two top rows), `interpolate` (default off), `target_fps` (default 24,
  1-240), `scale`, `model_name`, `batch_size`, `use_fp16`, `format`, `pix_fmt`, `crf` (default 19, 0-51),
  `loop_count` (0-100), `trim_to_audio`, `save_metadata` (on), `save_output` (on), `delete_cache_after_save`. UI-only (not inputs, not in `widgets_values`):
  `source_fps` display (read-only "24 (fixed)", right after `interpolate`), the unlabelled cache path line and the
  preview player (last). Standard one-widget-per-row layout; the player is a normal DOM widget below the options.
* **`target_fps` is always the frame rate of the written file** and is always editable. `interpolate` on: RIFE
  from 24 fps (H3MD's fixed rate) to `target_fps`. `interpolate` off: the frames are written unchanged at
  `target_fps`; with anything but 24 the clip plays faster/slower and **the audio no longer matches** - a warning
  says so (`interpolate is OFF but target_fps ... audio will NOT match ... x faster/slower`). (Could be labelled
  `frame_rate` like VHS; kept as `target_fps` so saved values/links stay valid.)
* Interpolation: `scale` 0.25-4 (flow scale, output size unchanged), `model_name` (ComfyUI-VFI's list),
  `batch_size` 1-32, `use_fp16`; greyed out while `interpolate` is off (`widget.disabled`; values kept and saved).
  The model is loaded through ComfyUI-VFI's own
  `RIFEInterpolation._get_or_load_model` (same files, auto download, model cache). Output frame count and the
  (frame, frame, timestep) of every output frame equal a one-pass RIFE run (`int(duration * target_fps)`, the
  wrapper's own `_calculate_target_frame_positions`); each chunk carries its last source frame into the next.
  `target_fps` 24 = no interpolation (like RIFE).
* `format` (all streamed chunk by chunk; options that do not apply are greyed out):

  | format | codec / container | audio | `crf`, `pix_fmt` | `loop_count` | `trim_to_audio` | metadata |
  |---|---|---|---|---|---|---|
  | `video/h264-mp4` (default) | libx264 mp4 | AAC | yes | - | yes | mp4 tags `prompt`/`workflow` |
  | `video/h265-mp4` | libx265 mp4 (`hvc1`) | AAC | yes | - | yes | mp4 tags |
  | `image/gif` | palette per frame | none | - | yes (0 = forever) | - | sidecar `<file>.png` |
  | `image/webp` | libwebp_anim, quality 90 | none | - | yes (0 = forever) | - | sidecar `<file>.png` |
  | `image/png-sequence` | folder `frame_000001.png ...` | none | - | - | - | in the first PNG |

  `pix_fmt`: `yuv420p` (default, plays everywhere) or `yuv420p10le` (10-bit, encoded from 16-bit RGB). GIF delays
  are 1/100 s steps (24 fps plays at 25). `loop_count` is not applied to mp4 (it would need the whole clip).
* `trim_to_audio` (mp4 only): the video is cut to the audio length (nothing happens without audio or when the
  video is already shorter).
* `save_metadata` (on): prompt + workflow as above; off, or ComfyUI `--disable-metadata`, = none.
* `save_output` off: written to ComfyUI's temp folder (`output_folder` greyed out), still previewed in the node.
* **Preview player** (own code, `web/js/h3md_save_video_interp.js`): HTML5 `<video controls>` with play/pause,
  seek bar, mute/volume, no autoplay, fixed height 270 px, letterboxed (`object-fit: contain`); gif/webp show the
  animation, png-sequence its first frame. Fed by the node's own `h3md_video` UI message (not `images`, so the node
  size stays fixed; the core images preview / queue thumbnail is not used).
* Outputs: `video_path` (STRING; a folder for png-sequence), `frame_count` (INT), `video` (VIDEO, lazy
  `comfy_api.latest.InputImpl.VideoFromFile` on the written file - connects to core Get Video Components / Save
  Video etc.; `None` for webp and png-sequence). The old `fps` (FLOAT) output was removed: links from it in saved
  workflows are dropped on load (logged in the browser console). (VHS Video Combine outputs `VHS_FILENAMES`
  instead.)
* **Removed** (2026-10-19): `preset` (encoder default), the `source_fps` input, `cache_folder`,
  `folder_overlap_source`, `folder_audio_crossfade_ms` (saving a kept cache folder without the Loop is gone),
  `pingpong` (briefly in the v2 layout); `long video` is required. Workflows saved with any earlier layout load
  aligned by name (`widgets_values_named` when its keys are in an older order or hold removed keys, else the
  positional layout: V1 2026-10-18 = 15 values, V2 = 17 with `pingpong`, V3 = 16 starting with `interpolate`;
  the current one starts with `filename_prefix`) - removed values are dropped, a V1 save with `interpolate` off
  is set to `target_fps` 24 (what it wrote); stale unlinked input slots are removed. API prompts
  that still send the removed keys are accepted and ignored (logged).
* **Automatic chunk size** (no `chunk_frames` widget): before every chunk the free VRAM is read
  (`comfy.model_management.get_free_memory`, else `torch.cuda.mem_get_info`). Chunk = (90 % of free - RIFE working
  set) / per-source-frame bytes, with a conservative estimate from padded resolution, `scale`, fp16 and `batch_size`;
  clamped to 8..240 source frames and to 25 % of free RAM (source + interpolated frames). Without interpolation only
  the RAM cap applies. Chunk size and reason are logged; the report lists `chunk_sizes` and `oom_events`.
* **CUDA OOM ladder**, per chunk: empty cache, halve the chunk (>= 8), retry -> still OOM at 8: ComfyUI memory
  management (`unload_all_models` + `soft_empty_cache`), `batch_size` 1, retry -> still OOM: this chunk on the CPU
  (float32; ComfyUI-VFI's module-level `device` is switched in memory for the call and restored, no file changed;
  slow, may differ slightly from GPU fp16), logged as a warning. After an OOM the chunk never grows back above the
  size that fitted. Non-OOM errors are raised unchanged.
* mp4 encoding like ComfyUI's SaveVideo / VHS: `crf` default **19** for new nodes (saved workflows keep their
  value), BT.709/sRGB tags, AAC at the source sample rate, `<filename_prefix>_00001_.<ext>` in the output folder
  (`output_folder` empty = ComfyUI output). Written as `<file>.part` (png-sequence: `<folder>.part`), renamed when
  complete; removed on failure.
* `delete_cache_after_save` (default off): cache folder removed only after a complete save, and only if it looks
  like an H3MD run folder (`resume_*` / `run_*` holding only `seg_*` files).
* Cache path line (right below `delete_cache_after_save`, widget `cache_folder_path`): **no label**, only the
  absolute cache folder (truncated when long, full path + `kept` / `deleted after save` / `kept (save failed)` on
  hover); read-only, not an input, not in `widgets_values`; filled at start (`h3md.save_video_interp` message) and
  at the end (`onExecuted`).
* Frontend files: `web/js/h3md_save_video_interp_ui.js` (pure helpers, node-tested) +
  `web/js/h3md_save_video_interp.js` (extension); `web/js/h3md_hide_widgets*.js` (Loop `delete_cache`);
  `web/js/h3md_long_video.js` (pure, node-tested) + `web/js/h3md_long_video_ext.js` (`long video` labels).

`delete_cache` (Loop): **hidden in the UI** since 2026-10-18 (`web/js/h3md_hide_widgets*.js` sets `widget.hidden`;
the input, its widget slot and `widgets_values` position are unchanged, so old workflows load as before) and **ignored
by the backend**: the Loop never deletes its cache before the video is saved, whatever the saved value is (logged).
Cache deletion = H3MD Save Video Interp `delete_cache_after_save`, after the file is complete. Without the save node,
kept caches must be removed by hand (`<drive>/H3MD_cache/run_*` / `resume_*`).
Loop 的 `delete_cache` 已在界面隐藏（输入和位置都保留，旧工作流照常加载），后端不再在出片前删缓存；删缓存只由
H3MD Save Video Interp 的 `delete_cache_after_save` 在视频完整写好之后做。

### VRAM policy
Between the two passes (H3MD Sampler, inside the lift) the depth ControlNet patch is released, idle models that are
not the diffusion models are unloaded (`unload_idle_models`) and the learned upscaler is dropped from RAM after use
(`release_upscaler`). Between segments: `vram_release` deep (idle models, ComfyUI buffers, CUDA cache, full unload
when needed) or basic. `slow_step_guard` watches the per-step log (`[H3MD timing] low_res|high_res step i/n`) and
stops the run (cache kept) when a step takes >= 4x the usual time (VRAM exhausted).

### Licensing / provenance
See [NOTICE.md](NOTICE.md) (third-party sources, what was adapted vs. used as an idea only, runtime dependencies) and [LICENSE](LICENSE) (AGPL-3.0-or-later, with an attribution term).

### Requirements
ComfyUI with core MiniMax H3 nodes (`comfy_extras.nodes_minimax_h3`), PyAV, torch, numpy, Pillow, einops (all in a
standard ComfyUI install). The two-pass engine is bundled in `h3md_luna/` (Luna-H3-Sampler, GPL-3.0).
