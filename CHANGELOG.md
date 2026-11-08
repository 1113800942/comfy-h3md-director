# Changelog / 更新记录

Dates are development dates (2026). / 日期为开发日期（2026 年）。

## [0.3.7-beta] — 2026-11-07
**In short / 简要：**
* **Seamless long video (window mode, beta)**: one switch on the Director. Continued shots are rendered together on
  one timeline (no continuation seams); hard cuts stay cuts; long plans are grouped only at hard cuts. 导演台一个开关：
  续接的镜头在同一条时间线上一起生成（没有续接接缝）；硬切照常；长计划只在硬切处分组。

**Details / 详细：**
* Core MiniMax H3 context windows (ComfyUI 0.44+) with H3MD additions: low-res windows 243 / overlap 56, high-res
  175 / 22 (measured: no double faces, no window seams). 核心上下文窗口 + H3MD 扩展：低清 243 / 重叠 56、高清 175 / 22（实测无重影、无窗口接缝）。
* Each shot's prompt, pictures, reference videos (sliced per window, so guides longer than ~15 s work) and audio follow
  its own time range. 每个镜头的提示词、参考图、参考视频（按窗口切开，超过约 15 秒也能用）、音频只管自己的时间段。
* Timed actions `[Ns] ...` and in-shot hard cuts `[Ns cut] ...` in `detailed_description`; each action is played once;
  pictures are left out of windows whose shot does not show the person; summary sentences about the order of events
  are left out per window. 提示词里的时间动作和镜头内硬切；每个动作只演一次；镜头里没有人物的窗口不带人物照片；
  summary 里讲先后的句子按窗口自动去掉。
* Progress cache: an interrupted run resumes at the last sampling step; the low-res result is kept, and
  `windowMode.preview` renders the low-res pass only (about half the time), reused by the full run. 进度缓存：中断后从
  最后一步接着跑；低清结果保留；低清预览约省一半时间，之后的完整版直接复用。
* Lyrics and lines timed the usual way (`At 00:07.9 she sings: ...`) count as time marks, also inside continued
  shots on one timeline: each line goes to exactly one window, timed inside it. 用平常写法标时间的歌词 / 台词
  （`At 00:07.9 she sings: ...`）也算时间标记，接成一条时间线的续接镜头里也一样：每句只给一个窗口，并写明在窗口里的第几秒。
* Local redraw: `windowMode.redraw [[start_s, end_s]]` re-renders only those seconds of a finished run (new seed, soft
  edges, only the windows touching them are computed); the rest stays frame-identical and the previous version is kept.
  局部重绘：只重画已完成视频的指定秒数（换种子、边缘柔和过渡、只算碰到的窗口），其他部分一帧不变，旧版本保留。
* Depth / pose control is switched off in window mode and in motion copy (the video stays a normal reference video);
  RefMods are switched off in window mode. 窗口模式和动作复刻下自动关闭深度图 / 骨骼图控制（视频改作普通参考视频）；
  窗口模式下自动关闭 RefMod。
* Memory: chunked decode (~10 s chunks written straight to the cache) and per-window high-res strip planning; 2K
  (2560×1408) runs on 64 GB RAM. 内存：分块解码（约 10 秒一块直接写进缓存）、高清条带按窗口估算；2K 在 64 GB 内存上可跑。
* Speed: a locked song that is also `<Audio N>` gives each window only its own stretch (a 160 s song made the low-res
  steps ~38% slower); changing only high-res settings reuses the low-res pass. 提速：锁定的歌每个窗口只带自己那一段
  （160 秒的歌曾让低清每步慢约 38%）；只改高清设置时复用低清结果。
* Fixes from an independent review: short groups are still windowed (prompts no longer averaged, cuts kept); a rerun with
  a new seed redoes the low-res pass; a group keeps one sound track (a new group starts where the lock changes); a failed
  redraw / rerun restores the previous version; a guide video split into continued shots stays on the timeline clock.
  独立审查修复：短组照样按窗口生成（提示词不再被平均、硬切保留）；换种子重跑会重跑低清；一组只有一条音轨（锁定变了就另起一组）；
  重画 / 重跑失败时旧版本放回；拆成续接镜头的引导视频按时间线对齐。
* Off = the old behaviour (same cache keys), except that motion copy no longer uses depth / pose control. 关闭时和原来
  一样（缓存键不变），唯一的变化：动作复刻不再用深度图 / 骨骼图控制。

## [0.3.6] — 2026-11-07
**In short / 简要：**
* Face refine save node: the face-refine options (detector … feather) are no longer greyed out when `face_refine` is
  fed by a link (as in the example workflows). 修脸保存节点：`face_refine` 由连线输入时（示例工作流就是这样），
  detector 到 feather 这些修脸选项不再被灰掉、可以正常修改。
* The registry entry no longer declares "NVIDIA CUDA only", so ComfyUI-Manager stops showing an
  "unsupported accelerator" warning. 注册表信息去掉"只支持 NVIDIA CUDA"，ComfyUI-Manager 不再提示"加速器不受支持"。

**Details / 详细：**
* The grey-out only looked at the node's own `face_refine` toggle; a linked input leaves that toggle at a stale value,
  so the options stayed greyed out whatever the link carried. Running was not affected (defaults were used). Now a
  linked `face_refine` keeps the options editable, and linking / unlinking refreshes them. 之前只看节点自己的
  `face_refine` 开关；连线输入时开关的值是旧的，所以不管外面传 true 还是 false 都灰掉（运行不受影响，用默认值）。
  现在连线时选项保持可改，连线 / 断开时会重新判断。

## [0.3.5] — 2026-11-05
**In short / 简要：**
* Motion copy (TARA) no longer fails at the end of a video: the shot length that follows the source video is now
  rounded **down** in motion copy, so it never runs past the clip. 动作复刻（TARA）跑到视频结尾不再报错：跟随原片的镜头
  长度在动作复刻时改为**向下取整**，不会超出原片。
* The `Duration:` line of a prompt is rewritten to the real segment length automatically. 提示词第一行的
  `Duration:` 自动改成实际段长。

**Details / 详细：**
* A shot that follows its reference video gets its length from the video. H3 needs 5+17n frames, so the length is
  rounded:
  * **motion copy** (the prompt or global prompt contains `motion_transfer:`, i.e. the TARA LoRA): rounded **down**
    when rounding up would run past the end of the clip. TARA needs a source frame for every generated frame
    (BFS error "Native temporal guide and output must have identical temporal grids"). At most 16 frames (0.7 s) of
    the clip end are left out.
  * everything else (reference video, depth / pose control, music videos): rounded **up** as before.
  跟随参考视频的镜头，长度取视频长度，再对齐到 5+17n：**动作复刻**（提示词或全局提示词里有 `motion_transfer:`，
  即用 TARA LoRA）在向上取整会超出原片结尾时**向下取整**（TARA 要求每一帧都有原片对应，否则 BFS 报 identical
  temporal grids），最多少原片结尾 16 帧（0.7 秒）；**其他功能**（参考视频、深度 / 骨骼控制、MV 等）照旧**向上取整**。
* Float noise in video durations (e.g. 12.041667 s × 24 = 289.000008) no longer rounds a length up a whole 17-frame
  step. 视频时长的小数误差不再把段长多进一档（17 帧）。
* `Duration: ...` (first such line of a prompt) is rewritten per segment after auto-split, e.g.
  `Duration: 12.96 s (311 frames; the first 39 frames continue the previous shot).` Prompts without it are unchanged.
  提示词里的 `Duration:` 行按每个小段（自动分段之后）的实际长度改写；没有这一行的提示词不变。

## [0.3.4] — 2026-11-05
**In short / 简要：**
* Fixed one or two cosmetic UI bugs (no effect on use). 修复了一两个界面美化上的 bug，不影响使用。
* Added skeleton (pose) support: new **H3MD Motion Control** node (pose / depth). 添加了对骨骼图的支持（新节点 H3MD Motion Control）。
* Motion copy in the workflow: the simple workflow now includes the **TARA LoRA** and the **BFS Nodes** TARA node
  (third-party, bypassed by default; install them yourself). 工作流里视频复刻方面添加了 TARA LoRA 和 BFS Nodes 的使用
  （第三方，默认旁路，需要自己安装）。

**Details / 详细：**
* **H3MD Motion Control** (new node next to H3MD Depth Control, which is unchanged): `control_kind` pose / depth.
  新节点 Motion Control（pose / depth），旧的 Depth Control 不变。
* **LoRA area rework** (measured: ComfyUI keeps only the LAST bypass LoRA of a chain, so the model area's DMAD and
  shared 768 were always replaced by the panel's Dasiwa row):
  * model subgraph: one 768 turbo (bypass) per pass — low 1.0, high follows the style (drama 0.5 / high motion 1.0 /
    motion copy 0.5); DMAD and the shared 768 are bypassed (removed in a later version); Combat V2 moved to the panel.
  * Control Panel: every row is a normal LoRA; **Dasiwa and 10ErosMax rows removed** (dropped from older saved lists,
    logged); Combat V2 row (0.7 / 0.49) in every style.
  模型区每条线只留一个 768 旁路加速（低清 1.0、高清跟风格联动），总控台只有普通 LoRA，删掉 Dasiwa 和 10ErosMax，
  Combat V2 移到总控台。
  * simple workflow: Sage split into **Sage low-res** and **Sage high-res** (both on). Bypass the low-res one when you add a
    third-party TARA node (Sage stops TARA). 清爽版 Sage 拆成低清、高清两个（都开）；自己接 TARA 时把低清那个旁路。
  * simple workflow: two **third-party TARA** nodes (Motion Transfer TARA LoRA + BFS TARA alignment on the low-res line),
    bypassed by default, with a note; the TARA LoRA is in the missing-models list. Needs ComfyUI-BFSNodes when enabled
    (opening the workflow without it shows the node as missing; bypassed, the workflow still runs).
    清爽版放好两个第三方 TARA 节点（默认旁路，附说明），要用时打开并旁路「Sage 低清」；需要自己装 ComfyUI-BFSNodes。
  * Control Panel: the **motion copy** button switches those TARA nodes on and bypasses "Sage low-res"; balanced / high
    motion switch back (TARA off, Sage low-res like Sage high-res). Without BFSNodes or the TARA LoRA, TARA stays off
    (a note says why). 点「动作复刻」自动打开 TARA、旁路 Sage 低清；点「均衡」「高动态」自动恢复；没装就不打开并提示。
* Cache: LoRAs loaded by **H3MD LowVRAM LoRA Loader** and the **Control Panel** are now part of each segment's cache
  key, so changing one re-renders instead of resuming the old result (core LoRA loaders are not tracked). Caches written
  by 0.3.3 with panel LoRAs are re-rendered once. 换 LoRA 后缓存会重算（之前会直接拿旧结果）；0.3.3 的缓存会重跑一次。
* Loop: `rerun_segments` running past the last segment is cut there (e.g. `1-2` on a one-segment plan) instead of
  failing. 重跑段落超出段数时自动截掉。
* Director: logs when the Control Panel's size overrides the plan's size. 尺寸以总控台为准时日志提示。
* Director editor: the `<Picture N>` / `<Video N>` highlights stay on their words at every A− / A+ UI scale.
  导演台放大缩小后提示词标签颜色不再错位。
* The Director node shows the version in its title (e.g. "H3MD Director + RefMod v0.3.4").

## [0.3.3] — 2026-11-04
* Loop `semantic_bridge` also takes **T8's semantic bridge** ("H3 Semantic Bridge / 模型与设置", auto config, multi-bridge
  stack). H3MD reads the bridge file and strength from it and applies them with its own code (no T8 code imported);
  disabled or strength 0 = off; a stack is applied in order; same file + strength = same cache as our own bridge node.
  Loop 的语义桥输入也能接 T8 的语义桥节点（单桥、多桥组合）。
* Face refine also takes the plain **ComfyUI-SeedVR2_VideoUpscaler** (numz): connect `SeedVR2 (Down)Load DiT Model`
  and `(Down)Load VAE Model` to `seedvr2_model` / `seedvr2_vae`, `refine_model = SeedVR2`. TE-Speed SeedVR2 still works;
  both inputs must come from the same pack. 修脸也能接普通版 SeedVR2（numz），TE-Speed 加速版照常可用。
* Loop `segments_to_run` is a box you can type in (e.g. `1`, `2, 5, 7-9`); connecting the Control Panel still works.
  Loop 上的"跑哪几段"可以直接填，也照样能接总控台。
* Old workflows: unchanged. 旧工作流不受影响。

## [0.3.2] — 2026-11-03
* Removed `H3MD hard cut example.json`: with the Eve music video every example's shots are hard cuts already; for a
  short test type `1-2` in the Control Panel's "which segments" box. Examples now: `H3MD Director simple.json`
  (start here) and `H3MD Director.json` (full, flat). 删掉硬切示例：现在只留清爽版和完整版。
* Eve demo plan pack: exported from 0.3.1 with "which segments" = `1` (first segment only; Clear = whole video) and a
  thumbnail. 演示包默认只跑第一段，带缩略图。

## [0.3.1] — 2026-11-03
* Control Panel: removed the size drop-down (it only filled in width / height, which are typed directly; the size
  reference note on the canvas stays). 总控台去掉"宽高快选"下拉，宽高直接填，画布上的尺寸参考表保留。
* Removed the `H3MD Director lite.json` and `H3MD alpha example.json` examples: the simple workflow
  (`H3MD Director simple.json`) replaces them. Examples now: simple, full, hard cut. 删掉精简版和 alpha 示例，由清爽版代替。

## [0.3.0] — 2026-11-03 (清爽版)
### H3MD Control Panel + simple workflow / 总控台 + 清爽版工作流
* New node **H3MD Control Panel**: size, fps / interpolation (24 locked when off),
  face refine, seed, which segments to run, style buttons (Balanced / High motion / Motion copy: recommended LoRAs,
  semantic bridge, Dasiwa high-res strength), low-res and high-res LoRA lists (on / strength / remove / add / reset;
  the node grows with the lists). The node loads the LoRAs itself (no rgthree / Crystools).
  新节点"总控台"：常改的设置集中在一处，风格按钮一键切推荐 LoRA、语义桥；自己加载 LoRA。
* "Which segments" box (`segments_to_run`, new last input of the Loop): empty = run everything (cached segments
  reused); `2` / `1-2` / `3, 6-7, 9` = generate only these (cached ones again too; missing earlier segments as well;
  a repair inside a finished video keeps the whole video). The value stays after the run (orange banner + Clear);
  the hint shows the total segment count; a wrong or out-of-range value blocks the queue. Old workflows: unchanged.
  "跑哪几段"一个框代替 rerun_segments + stop_after；跑完保留（橙色提示 + 清空按钮）；写错拦住运行。
* `example_workflows/H3MD Director simple.json`: Control Panel + Director + preview, models / sampler / save in
  subgraphs, 4B encoder + ClipProj note inside. Examples now use our own Eve music video "Above the Clouds".
  Example files without brackets (`H3MD Director simple.json` instead of `H3MD Director (simple).json`).
* Simple Director: media rows show only ＋ (or drop files), reference video height is auto, "+ Shot" hidden; all
  kept in code – Settings → H3MD → "Show advanced Director buttons" brings them back.
  导演台简化：只隐藏，设置里可恢复。
* Face refine also takes the original ComfyUI-VOSR2 ("VOSR 2.0 Model Loader"); the simple workflow uses it.
  修脸支持原作者 ComfyUI-VOSR2。
* Registry scan: no `__import__` / `.eval()` / RegExp `.exec` spellings left in the shipped files.

## [0.2.2] — 2026-11-02
### More shared / server hardening / 共享和服务器继续加固
* Plan export: a plan can no longer pull arbitrary files into the zip - absolute paths only for media files, and on a
  shared ComfyUI only inside ComfyUI's folders. 导出计划不能再把任意文件打进 zip。
* Plan import: on a shared ComfyUI uploads and unpacked media are capped at 1 GB (8 GB locally); unpacking counts the
  real bytes, so a zip whose header lies (zip bomb) is stopped. 共享模式导入上限 1 GB；解压按实际字节计数，防压缩炸弹。
* Cache: on a shared ComfyUI `resume_from_cache` no longer reuses the `resume_<settings>` folder (another person with the
  same settings could get your segments); each run gets its own folder. `H3MD_SHARED_RESUME=1` allows it again.
  共享模式下不再复用同设置的续跑缓存，避免拿到别人的分段。

## [0.2.1] — 2026-11-02
### Safer on shared / server ComfyUI / 共享和服务器上更安全
* New `h3md_paths.py`. When ComfyUI listens beyond localhost (`--listen`, LAN, cloud platform) H3MD only reads media
  inside ComfyUI's input / output / temp folders (`/h3md/probe` and the Director), only saves videos inside the output
  folder (`output_folder` outside it falls back to the output folder, a relative one becomes a subfolder) and keeps the
  automatic cache in ComfyUI's temp folder (no scan of other drives; an explicit `cache_dir` outside ComfyUI's folders
  is ignored). Local use (default `127.0.0.1`) is unchanged. Overrides: `H3MD_PATHS=local|shared`, `H3MD_MEDIA_DIRS`.
  `/h3md/probe` answers for media files only, in every mode.
  ComfyUI 对外开放（--listen、局域网、云平台）时：只读 input/output/temp 里的素材，视频只存 output 里，缓存放 ComfyUI temp；
  本机使用不变。probe 只回答媒体文件。
* RefMod: the list and the lookup no longer depend on the RefMod plugin's private helpers (`_list_mod_names`,
  `_find_mod_path`): if they are renamed or fail, H3MD scans `models/refmods` itself.
  RefMod 列表和查找不再依赖另一个插件的私有函数，拿不到时自己扫描目录。
* A latent that cannot be moved to the CPU is now logged instead of silently kept on the GPU.
* README: demo pack (import and run) and the shared-ComfyUI rules. Tests: `test_paths.py`, `test_refmod_fallback.py`.

## [0.2.0] — 2026-11-02
### Example workflows / 示例工作流
* Full, lite and hard-cut examples: LoRA mix set to the one measured against oily skin — FL2V turbo 4-step **v1.2** 768p
  (was v1.3, matching the README model table); low-res: turbo 0.5, Motion Repair 0.75, speed slider 3, cinematic 0.5,
  VBVR 1, Enhancer 0.55; high-res: turbo 0.7, realism-people 0.6, Motion Repair 0.25, cinematic 0.2, lms 0.2, VBVR 0.5
  (lite: Enhancer 0.55, realism-people 0.6). High-res LoRA note: regular row now "lms-0.2, realism people-0.6".
  示例工作流换成今天实测去油光的 LoRA 组合（FL2V turbo 改回 v1.2 768p，Enhancer 0.55，realism-people 0.6，电影感两边都开），
  高清 LoRA 注释 regular 一行同步修改。

### Save and share a plan / 保存和分享计划
* Director: a new row above the plan header — **Import plan…** (left), **Export plan + media** / **Export plan (no media)**
  (right); shown on an empty Director too (exports greyed: "Nothing to export yet").
  导演台计划栏上方新增一行：左边 Import plan…，右边两个导出按钮；空白导演台也显示（导出变灰）。
* Export: one plain `.zip` — `manifest.json` (size, shots, seconds, seed, media list, models the active loaders name),
  `plan.json`, `workflow.json` (all node settings), `settings.json` (readable), `thumbnail.jpg` (first frame of the
  newest finished video), and with media: pictures, songs (reference videos over 200 MB only when ticked). Never:
  finished videos, caches, models, RefMods. 导出普通 zip：清单、计划、整张工作流、可读参数、缩略图，带素材时加照片和歌；
  不放成片、缓存、模型、RefMod。
* Import: `.zip` or a bare plan `.json` (e.g. written by an agent with the prompt skill). Media to
  `input/H3MD_plans/<name>/` (never overwritten), paths rewritten. **Plan only** / **Plan + all settings** (opens the
  saved workflow through ComfyUI's own loader: missing nodes red, missing models → download dialog). Problems are listed,
  not fixed silently. Zip members outside `media/` + the fixed files are ignored (no `..` paths).
  导入 zip 或智能体写的计划 json；素材放 input/H3MD_plans/<名字>/；两种导入方式；问题逐条列出不自动改；只解规定的文件。
* `docs/plan_format.md`; prompt skill section 12b (write an importable plan `.json`) and 10c (instrumental segments,
  "no X" does nothing, don't retouch reference photos, fixed seeds only for A/B).
  新增格式说明；提示词 skill 加 12b（输出可导入的计划 json）和 10c（3 分钟一镜到底的经验）。

### Long-video quality / 长视频画质
* `hires_anchor` (last widget of H3MD Loop Options, default 0 = off): one continuous scene only, with `lowres_anchor`.
  The high-res result of each segment pins the next segment's high-res start, so it can drift too; after each
  continued segment the NEW high-res latent steps (not the kept overlap, not a rerun tail lock) get their 24 channels'
  mean / spread pulled back to the shot's first segment, before decode and before the next segment continues.
  Measured (same seed as the 3-minute take, segments 1–5): channel statistics locked exactly; colour shift 0.29 -> 0.13,
  sharpness −10.6 % -> −7.6 %, saturation unchanged (+3.7 % -> +3.6 %) — a small gain, so it stays off by default.
  高清锚定（Loop Options 最后一个，默认关）：续接段新生成的高清 latent 在解码前拉回本镜头第一段。实测前 5 段：
  色差 0.29 → 0.13，锐度 −10.6% → −7.6%，饱和度不变；效果小，默认关。
* **`drift_correct` (off / auto) + `drift_strength`** on H3MD Save Video Interp and the Face Refine save node (appended
  last, default off): while the video is written, every segment is measured (brightness, contrast, colour, saturation,
  sharpness). A hard cut, or a clear brightness / contrast / colour change inside a shot, starts a new reference. Per
  reference, saturation and sharpness are corrected only when they follow a clear, steady trend (fitted line through
  the reference, trend >= 3 %, residual <= max(1.5 %, 35 % of the trend)); the measured drift, smoothed over
  neighbouring segments, is removed (colour gain around each pixel's brightness and the segment's tint - plain
  brightness-centred gain for strongly tinted pictures - and a light luma-only unsharp mask, easing over 2 s). Sharpness
  is never lowered. Decisions in `<video>_drift.json`; the cache is not changed. Measured: 3-minute take saturation
  +8.4 % -> <= +1.5 %, sharpness −18 % -> <= +3.1 %; 11-shot hard-cut music video untouched; 16-segment dance: 2–6 %
  colour pull only; synthetic day -> night kept as a new scene. `h3md_drift_correct.py`; `tools/drift_correct.py` does
  the same on finished videos (`--segments 294,362:0,...` or `--plan`).
  保存节点新增 drift_correct（默认关）：硬切或镜头内亮度、对比度、色调明显变化都换新参照；同一参照下只有饱和度、锐度呈稳定
  趋势才校正，随内容跳动的不动；锐度只补不减；每段判断写进 _drift.json；缓存不变。
* Why both: the latent statistics were already locked (low-res and high-res) and the picture still got +3.6 %
  saturation over 5 segments, so the rest of the drift is not a per-channel shift; it is fixed in pixel space.
  为什么两个都做：低清、高清 latent 的通道统计都已锁住，画面饱和度仍涨 3.6%，剩下的漂移不是通道整体偏移，只能在像素层面校正。

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
