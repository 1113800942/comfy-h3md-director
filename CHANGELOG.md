# Changelog / 更新记录

Dates are development dates (2026). / 日期为开发日期（2026 年）。

## [0.1.2] — 2026-11-02
No node code changes: docs, examples and tools since 0.1.1. / 节点代码没有改动：0.1.1 之后补充的文档、示例和工具。

* `example_workflows/H3MD Director (lite).json`: the same H3MD features with the fewest dependencies (ComfyUI + H3MD +
  ComfyUI-KJNodes); LoRAs on core `LoraLoaderModelOnly`, no face refine / RIFE / extra refine step.
  精简版示例工作流：H3MD 功能不变，只依赖 ComfyUI + H3MD + ComfyUI-KJNodes；LoRA 改用自带加载器，不含修脸、补帧、多一步精修。
* `tools/drift_report.py`: measures a long video segment by segment (brightness, contrast, saturation, sharpness, colour
  shift vs segment 1) and writes a table + chart; runs on ComfyUI's Python (PyAV + numpy). 逐段劣化测量脚本：对任意视频
  输出亮度、对比度、饱和度、锐度、色差（都和第 1 段比）的表和图。
* README: dependency table (required / recommended / optional, what each pack does, what happens without it) and a
  measured "Hardware and render time" section (RTX 5090: time per segment, peak RAM, disk cache, model sizes), both
  folded. README 加依赖表（必需 / 推荐 / 可选、各自用途、不装会怎样）和实测的"硬件要求和用时"（都折叠）。
* `example_workflows/H3MD hard cut example.json`: minimal 2-shot hard-cut workflow (shot 2 starts fresh from the last frame of shot 1, no latent continuation); steps in the Note node. 硬切示例工作流（2 个分镜，第 2 镜用第 1 镜最后一帧重新生成，不续接 latent），步骤写在 Note 节点里。
* Prompt skill: section 10b, singing / framing / camera rules measured on the "Above the Clouds" music video; README music-video samples. 提示词 skill 加 10b（唱歌、景别、机位实测规则）；README 加 MV 样片。
* `skills/h3md-prompt/`: H3MD prompt skill (English SKILL.md + Chinese copy), linked from the README install steps.
  H3MD 提示词 skill（英文 + 中文），README 安装步骤里加了说明。

## [0.1.1] — 2026-10-31
### Comfy Registry scan clean-up / 注册表安全扫描整改
The registry scan flagged v0.1.0 with four info-level findings; all were harmless, but the code now avoids the patterns.
注册表扫描对 0.1.0 标了 4 条 info 级提示，都是误报；这一版把相关写法换掉，功能不变。
* `__init__.py`, `h3md_director.py`, `h3md_depth_control.py`: plain imports instead of `importlib.import_module`.
  改用普通 import。
* `h3md_director_refmod.py`: own argument mapping instead of `inspect.Signature.bind` (flagged as a network bind).
  不再用 `.bind`。
* `h3md_cache.py`: **removed** the `H3MD_CACHE_DEFAULT` environment variable; set `cache_dir` on the Loop instead.
  删除环境变量 `H3MD_CACHE_DEFAULT`，需要固定位置请填 Loop 的 `cache_dir`。
* Registry icon; README lists comfy-cli and ComfyUI-Manager install. 加注册表图标；README 加 comfy-cli / Manager 安装方式。

## [0.1.0] — 2026-10-31
### 2026-10-31 — Release prep / 发布准备
* Example workflow `example_workflows/H3MD Director.json` (the 133 s motion-copy sample H3MD_00072) with notes:
  model links, custom-node links, usage and how H3MD works. README: text-encoder choice (32B vs 4B + ClipProj),
  fast-motion ghosting, motion-copy length trim. / 动作复刻示例工作流（含模型、节点链接、注意事项和技术思路说明）。
* README split into English (`README.md`, default) and Chinese (`README.zh-CN.md`); highlights and "How H3MD works" with
  thanks. / README 拆成英文版（默认）和中文版，加上亮点和技术思路（含致谢）。

### 2026-10-30 (d) — Low-res anchor replaces the drift anchor / 低清锚定取代漂移锚定
* **Removed / 删除**: `drift_handoff_match`, `drift_seam_anchor`, `drift_detail` (2026-10-30 (c)), `h3md_drift_anchor.py`,
  the `handoff` hook in `h3md_luna/luna_two_pass.py`, the aex-drift MIT notice. Reason: tests showed no effect - they
  graded new frames to the held frames of the previous segment, which had already drifted. Old option values are
  ignored with a warning. 漂移锚定实测无效（拿已漂的上一段当标准），删除。
* **Added / 新增** (H3MD Loop Options, appended last; 0 = off, old cache keys): `lowres_anchor`. After each continued
  segment's low-res pass, before the lift, the new latent steps' per-channel mean / std are pulled to the first segment
  of the shot; the low-res carry is corrected too. Reason: cache analysis of a 16-segment take - the colour drift is
  born in the low-res pass (saturation 0.39 -> 0.58); matching channel statistics to segment 1 brings it back to 0.40.
  Seg report field `lowres_anchor`. 单场景长视频：低清阶段把新画面通道统计拉回本镜头第一段。

### 2026-10-30 (b) — Depth video drives only the ControlNet / 深度视频只驱动 ControlNet
* **Changed / 修改**: a video with `depth` ticked is no longer a `<Video N>` reference: it drives only the depth
  ControlNet, automatically (no switch). The Director gives it no `<Video N>` label (Python and editor). A depth video
  that also supplies the soundtrack `<Audio>` stays a reference (warning). Cache route tag `controlnet_only`.
  Reason (tests 00064 / 00065 / 00066): a white-on-black SAM3 silhouette as `<Video N>` made stretches of the output
  black or studio grey; grey-backdrop reference photos cause grey on their own. 勾了 depth 的视频自动只驱动 ControlNet。
* **Changed / 修改**: depth ticked without `H3MD Depth Control` connected → error before the run (was: silently used as
  a plain reference).
* Docs / skills: motion-copy prompts have no `<Video N>`; reference photos must not use plain / grey studio
  backgrounds; describe one plain floor.

### 2026-10-30 — Re-render selected segments / 重跑选定段落
* **Added / 新增**: `rerun_segments` (e.g. `8-10, 13-14`, 1-based) at the top of the Loop nodes: only those segments
  are re-rendered; each block's head continues the segment before it and its last overlap frames (high-res latent,
  low-res pass, audio) are locked to the head of the cached segment after it, so nothing else is re-rendered and the
  output is the whole video. Unselected segments are taken from the cache even when references or prompts changed,
  and get the new chain key. Old files go to `_replaced_<time>`. `rerun_new_seed` (default on). `stop_after`
  (1-based, 0 = all). 只重跑选中的段：开头接上一段、结尾锁到下一段旧开头，其余从缓存取，输出完整视频。
* **Removed / 删除**: `start_index`, `end_index`, `previous_latent` on the Loop nodes (the old range could not
  continue from the cache and output only the range). Saved workflows: migrate the Loop node's widgets.
* Cache keys and resume folders are unchanged, so existing caches can be repaired.
* Tests: `tests/test_rerun.py`.

### 2026-10-29 — H3MD Director (two-pass engine bundled)
* **Name / 名称**: H3MD Director · H3MD 导演台, repository `ComfyUI-H3MD-Director`. License AGPL-3.0-or-later with an
  attribution term (was GPL-3.0-or-later). 许可证改为 AGPL-3.0 + 署名条款。
* **Engine / 引擎**: `H3MD Sampler` on the bundled Luna-H3-Sampler engine (`h3md_luna/`, GPL-3.0) replaces the runtime
  wrapper around selflift-Avatar; selflift-Avatar is no longer needed. 自带两段式引擎，不再依赖 selflift-Avatar。
* **Removed / 删除**: continue mode `static_lock` (drift only; strength 0 = prefix unchanged), `continued_tiling*`,
  `tiling_mode`, `tiling_tiles`, sequential tiling and fade, `highres_tiling_mode`, `highres_token_chunk`,
  `highres_weight_cache_gb`. Old Loop Options nodes must be re-added.
* **Changed / 修改**: `highres_tiling` default on (fewest strips, full frame when it fits); `seed_mode` default
  increment, resume reuses the stored base seed; width / height snap to 64; per-step timing + VRAM log, read by the
  slow-step guard; seam tone measured at the kept/new boundary; the upscaler list shows every model in
  `latent_upscale_models`; picture tag chips show thumbnails.
* **Added / 新增**: high-res out-of-memory retry (more strips → feed-forward chunking → readable error) with a
  per-machine calibration file; `ff_chunk_rows`; readable low-res OOM message; README low-VRAM section.
* Tests: `tests/` (CPU, real ComfyUI H3 code with a tiny model); GPU: first segment 1088x1920 243 f in ~11 min on an
  RTX 5090, ~16 min with 16 GB reserved (12 strips).

### 2026-10-27
* **Autosplit** (`h3md_autosplit.py`): every shot is estimated with the Director's token formula (canvas + reference
  videos + images + RefMods); a shot over the budget is replaced by the fewest equal continuation parts (overlap 39,
  5+17n, cut points nudged to quiet frames of its reference video). Budget = GPU VRAM − ComfyUI's own reserve
  (`--reserve-vram` / default + `--vram-headroom`) − optional plan `autoSplitReserveGB`, scaled from the 215k
  calibration, × plan `autoSplitSafety` (0.85). `refVideoHeight=auto` picks its tier against the same budget. Depth videos always use the lowest tier; detail (non-depth) videos are
  planned at the top tier (more parts instead of a blurrier reference; plan `autoSplitPrefer: "fewer"` = fewest parts).
  Plan `"autoSplit": false` = off. Editor: per-shot badge (green / ⚡N parts / ✗), card line and budget pill,
  served by `POST /h3md/autosplit` (the Python parse itself, no JS port).
  自动分段：按 token 预算自动把超限分镜拆成等长续接段，编辑器显示估算与段数。
* **Memory guard** (`h3md_vram.py`, Loop Options `vram_release` = deep, `slow_step_guard` = stop): between segments
  unload idle models, reset ComfyUI's cast/staging buffers, empty the CUDA cache, log memory before/after; > 2 GB idle
  PyTorch cache left -> unload all models. A selflift step >= 4x the usual time (>= 120 s) stops the run (cache kept).
  显存守护：段间深度释放 + 日志；某步突然变慢 4 倍以上自动停止（缓存保留，重启后续跑）。

* Director console: drag-performance fix (static thumbnails, video plays on hover, no thumbnail redraw while panning) — in progress.
  导演台：拖画布卡顿修复（缩略图静态、悬停才播放视频、拖动时不重绘）——进行中。

## 0.1.0 — first public release (待确认 date) / 首次公开发布
Release docs / 发布文件: bilingual README, AGPL-3.0-or-later LICENSE, NOTICE.md, pyproject.toml, `example_workflows/H3MD alpha example.json`.

### 2026-10-19
* `H3MD Loop Sampler`: output slot 5 renamed `segments` → **`long video`** (same type `H3MD_SEGMENTS`, saved links keep working).
* `H3MD Save Video Interp`: `filename_prefix` + `output_folder` moved to the top; `fps` output replaced by a `video` (VIDEO)
  output; own preview player; read-only source fps (24); removed `preset`, `source_fps` input, `cache_folder`,
  `folder_overlap_source`, `folder_audio_crossfade_ms`, `pingpong`. Old layouts load aligned by name.
* Output notes in node descriptions / output tooltips; models manifest in the example workflow.

### 2026-10-18
* New **`H3MD Save Video Interp`**: streams the Loop's disk cache chunk by chunk, optional RIFE (ComfyUI-VFI), h264/h265 mp4,
  gif, webp, png sequence; `crf` default 19; automatic chunk size and CUDA-OOM ladder.
* Loop no longer joins the clip in RAM: `merge_images` removed, `images` = 64x64 placeholder (except `cache_mode = ram`).
* Loop `delete_cache` hidden and ignored; cache deletion only via `delete_cache_after_save` after a complete save.
* `continue_lowres_prefix` default → `native_lowres_carry`.

### 2026-10-17
* Director: no-sliver-split rule for video-driven shots.
* Phase 3: shots, automatic sub-segments (`maxSegmentLength`, `subOverlap`), draggable timeline, `continuity_prompt`.
* Continued tiling round 2 (`continued_tiling_method` joint/sequential, `continued_tiling_tiles`); removed the two DC-match
  options (old workflows re-mapped by `web/js/h3md_options_compat.js`); measured strip-count calibration.
* Seams and reference windows: `merge_overlap_source`, `ref_window_mode`, `seam_tone_*`.
* `H3MD Semantic Bridge` (chainable).

### 2026-10-16
* Phase 1–2: `H3MD Director` plan + visual editor, Segment Pick / Encode / Continue / Join, `SelfLift H3MD Sampler`,
  `H3MD Loop Sampler` (+ legacy single node), per-segment disk cache with resume, `H3MD LowVRAM LoRA Loader`.

### Dates not recorded / 日期未记录
* Director: shot tabs, @-mentions in prompts, `plan_info` output.
