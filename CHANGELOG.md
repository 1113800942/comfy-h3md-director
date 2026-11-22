# Changelog / 更新记录

Dates are development dates (2026). / 日期为开发日期（2026 年）。

## [0.6.0] - 2026-11-22

### Added / 新增
- Multi-person swap keyframes, new flow: **one workflow, two groups, all by buttons** (`example_workflows/H3MD_Keyframe_MultiSwap_0.6.0.json`).
  1. **Keyframe Export**: Prepare + the new **H3MD Keyframe Export** write `output/H3MD_keyframes/<name>_keyframe_export_<date>_<no>.zip` (keyframes, character pictures, who replaces whom, a README for the AI) and preview its keyframes.
  2. An AI (Claude, ChatGPT, Gemini ...) reads the keyframes with the new skill **`skills/h3md-keyframe`** and answers with `<name>_keyframe_import_<date>_<no>.h3kf` (or a `.json`): one MiniMax H3 Image prompt per keyframe.
  3. **Keyframe Import**: the new **H3MD Keyframe Import** (Import package button; group 1 can stay bypassed) -> **H3MD Keyframe Recast** (MiniMax H3 Image, int8, 4 MP, the tuned 6-step sigmas, ~25 s a keyframe; shows original | new, numbered) -> the new **H3MD Keyframe Redo** (one button per keyframe: only the chosen ones are made again) -> **H3MD Keyframe Assemble** (`output/H3MD_plans/<name>_multiswap_plan_<date>_<no>.zip`).
  Packages get a date and a number and are never overwritten. Reading the keyframes is the slow and decisive part (H3 Image draws what the text says), so it is done once by the AI; the skill says what to read and get right for every person (orientation, each hand, legs as a still pose, head, expression, place, size, overlaps, crop; image left/right) and how to check before answering.
  多人换人关键帧新流程:**一个工作流两组,全用按钮**。第 1 组导出关键帧包,AI 按新 skill `skills/h3md-keyframe` 读图写 H3 Image 提示词回包(.h3kf / .json),第 2 组按钮导入、出图(每张约 25 秒)、原图 | 新图对比、单张重做按钮、打计划包。包名带日期和序号,不覆盖。
- Example: `example_plans/H3MD_multiswap_3dancers_0.6.0_plan.zip` - the three-dancer swap (28.5 s, three performers, two place changes, a blackout): the source clip, the 14 recast keyframes (2 of them spares) and the prompt - import it in the Director and run.
  示例:三人舞蹈换人(28.5 秒,三个人、两次换位、一次熄灯)的计划包:原片、14 张换好的关键帧(其中 2 张备用)和提示词,导演台导入就能跑。
- README: what the multi-person swap can and cannot do - works well with a fixed or steady camera; many people crossing close to the lens, faces against the lens and people walking in and out are not perfect (a person can get the wrong picture); music videos with edited-in morph effects (clothes changing between two frames, a face morphing into another person) are not supported.
  说明里写清多人换人的上限:固定机位、镜头稳的效果好;人多贴脸交叉换位、频繁进出不完美,可能换错;后期加了变形特效(一帧换衣服、脸渐变换人)的 MV 不支持。

### Changed / 变更
- Multi-person swap seams: the Director cuts a long swap shot **on its keyframes** - every part's new frames begin with a keyframe (within 0.7 s) on any GPU, instead of equal parts that could start up to 3 s before the next keyframe (the source's gloves came back right after a seam). Keyframe Prepare adds **spare keyframes** (marked `"backup": true` in the plan, "(spare)" on the sheets) only where two keyframes are further apart than a 16 GB GPU's part can reach; a bigger GPU ignores them, a smaller one takes them exactly where its cuts need them. A part start that still has no keyframe is named in the run notes with its time. Remake parts are capped at 345 frames (a 48 GB GPU planned 379 and would have stopped).
  多人换人接缝：导演台按关键帧切段，任何显卡上每一段的新画面开头都有关键帧（0.7 秒内），不再平均切（之前接缝后最多 3 秒没有关键帧，原片的手套漏回来）。Prepare 只在两张关键帧隔得比 16G 显卡一段还长的地方加备用关键帧（计划里 `"backup": true`，对比图标 "(spare)"）；大显卡不用，小显卡只在它的切点需要的地方用。仍然缺关键帧的段开头会在运行说明里写出时间。Remake 每段最多 345 帧（48G 显卡原来会切出 379 帧而报错）。
- Keyframe Prepare, cast panel: **one picture can take several numbers**. When the tracker splits one person into two
  numbers (after a cut, a turn, a crossing), drag a second line from the same picture to the other number: both become
  one person for the keyframes and the plan ("图1换2号" + "图1换4号"). Numbers without a line stay themselves (passers-by);
  a number still goes to one picture only. The user's lines are final.
  演员表一张图可以连多个号：追踪把同一个人切成两个号（剪辑点后、转身、交错）时，从同一张图再拖一根线，两个号合成一个人。
  没连线的号保持原样（路人）；一个号仍然只能连一张图。用户连的线说了算。
- Keyframe Prepare: a frame where the people are **too dark to read** (a blackout, a strobe) is never a keyframe - the
  nearest lit frame is taken. Dark = the lit parts of the people (90th percentile of the brightness in their boxes)
  below 40 % of the clip's own usual light, so a clip that is dark all through loses nothing. (A three-dancer clip had
  a keyframe on the 0.2 s blackout at 16.83 s; now 17.17 s.)
  人身上太暗看不清的帧（熄灯、频闪）不再当关键帧，取前后最近的亮帧。和这条片子自己的正常亮度比（低于 4 成算暗），
  整片都偏暗的视频不受影响。
- Keyframe Prepare: *descriptions* is optional. Left empty, the AI that writes the keyframe prompts describes every character from its picture, asks the user what the picture does not show (a half-body picture: trousers and shoes) and returns one sentence per character as "characters" in its answer; H3MD Keyframe Import puts them into the Director's video prompt (kept next to the export package for later redo answers). request.json lists the characters once; skill and package README updated.
  描述框可以不填：写关键帧提示词的 AI 按角色图自己描述，图上看不到的（半身照的裤子、鞋）先问用户；它在回包里给每个角色一句描述（characters），Import 填进导演台的视频提示词。
- Keyframe Prepare: **who is who, redone** (K.identify, after Zhang et al., "Tracking Persons-of-Interest via Unsupervised Representation Adaptation"). Every tracker number is cut at the shot cuts and where the person's look changes, and the pieces are grouped into people by face, hair colour and clothes colour - each weighted by how well it tells THIS clip's people apart, learned from the clip itself (two halves of one track = one person, two tracks on screen together = two people). Pieces seen together are never one person; when two people fit about equally well nothing is guessed; the number of people is the number on screen together. An AI-generated three-woman dance whose faces ArcFace could not tell apart had the numbers jump between two women at the cuts (one woman shown twice on the cast panel, the other half 'kept'); now each is one number for the whole clip, and five men in the same white clothes are still told apart by their faces. A second box the tracker put on the same person is merged. Replaces the 0.5.9 re-identification and the face clusters below.
  演员表"一个人出现两次"：追踪器在切镜头时把号交给了另一个人。现在每个号在镜头切换和长相变化处切开，再按脸、发色、衣服颜色归人，各项的权重由这段视频自己算（同一段前后两半是同一人，同时出现的是不同的人），同时出现的绝不归成一个人，分不清就不猜。同一个人身上的重复框也会合并。
- Keyframe Prepare: identity by face - the faces of every sampled frame are grouped into people, and a track whose face turns into another person's (the tracker passed the number on at a crossing) is cut there; five men in the same white clothes keep one number each. The cast sheet shows each person bright and framed, the others dimmed.
- Keyframe Prepare: a cast panel on the node after a run - drag a line from a picture to the person it replaces (or click people in order); running again reuses the tracking, so a new choice takes seconds.
- Multi-person swap keyframes for any GPU: the Director splits a shot by the token budget of the GPU it runs on, so Prepare gives every replaced person on screen a keyframe at least every 3 s (no more thinning to 4), each plan keyframe lists the replaced people it shows, and every Remake part picks the 4 keyframes that show everybody of that part (h3md_remake_engine.pick_keyframes; old plans: 4 spread over the part). The Remake report names people a part cannot show.
- Keyframe Export: request.json lists every person of a keyframe left to right with the box in % of the frame, the edges that cut them and their role, so the AI can check left/right and front/back by numbers; the h3md-keyframe skill and the package README have the new rules (same character sentence in every keyframe, people mostly outside the frame or almost hidden, clear tilts and leans).
- Keyframe Prepare: who gets which picture no longer depends on places in one frame. Everyone seen clearly for at least 1 s is numbered in order of first appearance (people who appear together: left to right); with *mapping* left empty, picture 1 goes to person #1, picture 2 to #2 and so on, a person keeps his picture wherever he moves, and people after the last picture stay themselves. *People* is now a cast sheet (each person large, the seconds he is seen, the picture he gets or "kept"); "图1换3号" / "picture 1 -> 3" picks by its numbers. Clips cut from the middle, with people walking in later, work the same way.
- Keyframe Prepare: people are told apart by their faces too (InsightFace ArcFace, models/insightface antelopev2 / buffalo_l, as ReActor / PuLID use; optional): when both tracks were seen with a face, the face decides who comes back after a cut, else hair / clothes colour as before.
- H3MD Keyframe Recast no longer reads the keyframes itself (the local prompt enhancer took 55-83 s a keyframe and sometimes answered empty). Its result is cut to the video's aspect before scaling (no stretch).
  Recast 不再自己看图写提示词(本地反推每张 55-83 秒,偶尔回空);出图先按视频比例裁齐再缩放,不拉伸。
- Control Panel style 多人互换 switches on only the LMS LoRA (high-res 0.2, nothing on low-res) instead of keeping the previous style's LoRAs; a multi-swap plan's global prompt is the LMS trigger words.
  总控台「多人互换」只开 LMS(高清 0.2,低清不开),不再沿用上一个风格的 LoRA;多人换人计划的全局提示词是 LMS 触发词。
- Keyframe node names are English (H3MD Keyframe Prepare / Export / Import / Recast / Redo / Assemble + version). The two older keyframe workflows and the flat prompt-skill files `skills/h3md_director_prompt_skill-cn/en.md` are removed.
  关键帧节点名改为英文;旧的关键帧工作流和旧的单文件 skill 删除。

### Fixed / 修复
- Keyframe Prepare: a person whose pieces were grouped together was dropped as a whole when one small piece shared a few
  frames with someone else (a double box at a crossing) - the centre woman of a dance stayed unswapped from 3.0 to 8.7 s.
  The pieces of a blocked group now join one by one.
  认人：一组片段里只要有一小段和别人重叠几帧（交错时的双框），整组都被丢掉——中间那位 3.0 到 8.7 秒没换。现在被挡的组逐段并入。
- Keyframe Prepare: a person who walked in after the part's keyframes were picked got another character's look in that part (the run used 7.3 s parts, the keyframes were thinned for 10.8 s). A picture written twice in 'mapping' takes the last line; pictures connected but missing from an old 'mapping' text are reported.
- Keyframe Prepare: a new person walking in (a face unlike everyone missing) no longer warns "a person came back who could be any of ..."; a person showing only as a narrow strip at the frame edge is not recast at that keyframe (a 26 px strip got a character picture).
- Loop: a trial (试镜) or `segments_to_run` took segments it did not run from the cache even when they had been made for **another plan** (the cache folder is shared by every plan with the same settings; only the frame count was checked). The old take was spliced into the video and used as the tail lock of the re-made segment. Every cached segment now records what it was made from; a segment is kept from the cache only when it matches this plan. Caches written before 0.6.0 are made again once when repairing.
  Loop:试镜 / 只跑某几段时,没跑的段会从缓存拿,哪怕是**别的计划**做的(缓存文件夹按设置共用,只查了帧数):旧视频被接进成片,还被当成重做那段的锁尾。现在每段缓存记下它是用哪份计划内容做的,对得上才沿用。0.6.0 以前的缓存修补时会重做一次。
- Director Import plan keeps the global prompt already in the Director when the imported plan has none (it was wiped).
  导演台导入计划时,计划里没有全局提示词就保留导演台原有的(以前会被清空)。
- Director: importing a portrait plan while the Control Panel was set to landscape (or the other way) ran at the wrong size. Import plan now sets the Control Panel's width / height to the plan's, and a multi-swap plan run at a Remake size of the other orientation follows the plan (1664x960 -> 960x1664, 2560x1472 -> 1472x2560).
  导演台:横屏的总控台导入竖屏计划(或反过来)会按错的尺寸跑。现在导入计划时总控台宽高自动改成计划的;多人换人计划遇到另一方向的 Remake 尺寸也跟计划走。
- Keyframe package: request.json lists per keyframe who stays (`keeps_people_from_left`, any positions, different per keyframe), and the keyframe skill says to describe every kept person too - with only "stays as in Picture 1", H3 Image redrew two kept men as copies of the new characters.
  关键帧包:request.json 每张写明哪些人保留(`keeps_people_from_left`,任意位置、每张不同),skill 要求保留的人也要描述——只写“保持原样”时 H3 Image 把两个保留的人画成了新角色的复制品。
- Keyframe Prepare: every replaced person gets at least one large, clear keyframe (at least 0.4 of the frame height, not at a side edge, not mostly overlapped). Without one Remake had no face for that person and drew someone else in the close-ups (a hangar clip: picture 3 was small in one keyframe and only a sliver in the other). The frame where they are largest is added, never dropped by the 4-per-part limit; a person never seen large is named in the warnings.
  关键帧 Prepare:每个要换的人至少有一张大而清楚的关键帧(至少 0.4 画面高、不在左右边、没被挡住大半)。没有的话 Remake 没有他的脸,近景会画成别人(机库片段:图3 一张很小、一张只露一条)。补上他最大最清楚的那一帧,“每段 4 张”不会删它;整段都没有就在警告里写明。
- Registry scan test reads file names as UTF-8 (Chinese workflow names).
- Director (RefMod): in the new ComfyUI frontend (Nodes 2.0) the plan editor inside the node shrank to a narrow strip after clicking the node. The editor now always keeps the node's width. Fix by Civitai user zhendao - thank you!
  导演台（RefMod）：新版界面（Nodes 2.0）点一下导演台节点，里面的计划编辑器会缩成很窄的一条。现在编辑器始终跟节点同宽。修复来自 Civitai 用户 zhendao，感谢！
- Keyframe workflow (multi-person swap): Prepare finds a person again after the tracker lost her - a blackout, a shot cut, a spin or leaving the frame and coming back. The tracker gives such a person a new id, so the picture mapped to her was "followed only up to N s" and later keyframes left her unswapped (Remake then drew the original back; 11-19: a group dance with a blackout position change, the third dancer was lost at 7.8 s). A new track is joined to a lost one by elimination (it is the only person missing) or by look (hair colour first, then clothes). Two tracks seen together are never joined; when it is not clear (two lost people who look alike) nothing is guessed and the info names the moment. The log says "person found again at ... s".
- Keyframe workflow: keyframes avoid frames the edit model cannot redraw at the right size. Skipped: a chosen person filling the whole frame height, or cut off by the bottom AND a side edge (only part of her in the frame: the model drew a whole, smaller person and the cut-off original stayed beside her - 11-19 run 38, 2.33 s). A keyframe with people cut off by the bottom edge moves, inside the same shot, to a frame where they are whole or clearly farther away - never across a cut or a place change, never making a gap longer than max_gap_s. People still cut off by the bottom get "keep the same size" in the edit prompt, and every edit prompt says to keep a back or side view (6.83 s had been redrawn facing the camera). The 4-keyframes-per-part limit now follows the Director's real split instead of a fixed 14.4 s window (a needed keyframe at 8.33 s had been dropped).
  关键帧流程：关键帧避开修图模型画不对大小的帧。不放：要换的人从上到下占满画面，或者同时被下边和左右边截断（人只有一部分在画面里：修图会画成一个完整的小人，旁边还留着原来那个人的一截——11-19 第 38 次，2.33 秒）。人被下边截断的关键帧，在同一个镜头里挪到人完整、或明显更远的那一帧；不跨剪辑点、不跨换位、不让间隔超过 max_gap_s。仍被下边截断的人，修图提示词加「保持一样大」；所有修图提示词都加「原来背对或侧身，新的人也背对或侧身」（6.83 秒曾被画成正面）。每段最多 4 张的限制改按导演台实际的分段来算，不再用固定的 14.4 秒（之前误删了 8.33 秒那张）。
  关键帧流程（多人换人）：跟踪器在黑场、切镜头、转圈、出画再入画后把人跟丢时，Prepare 会把她认回来。以前跟丢的人会被当成新人，图片对应的那个人「只跟到第 N 秒」，后面的关键帧漏换她，Remake 又把原来的人画了回来（11-19 团舞黑场换位，第三位在 7.8 秒跟丢）。认人的办法：排除法（只少了她一个），或看长相（先比发色，再比衣服颜色）。同时出现过的两个人绝不合并；分不清（两个长得像的人同时跟丢）就不猜，在信息里写明是哪一刻。日志会写「person found again at … s」。

## [0.5.9] - 2026-11-20

### Fixed / 修复
- Face refine: a face detector put in `models/ultralytics/bbox` did not show in the `detector` list unless ComfyUI-Impact-Subpack was installed (reported by several users). H3MD now registers `models/ultralytics/bbox` (and `segm`) itself, for ComfyUI's models folder and every other models folder ComfyUI knows (extra_model_paths, the Desktop app's shared folder). A registration made by the Subpack is kept as it is.
  修脸：人脸检测模型放进 `models/ultralytics/bbox` 后，不装 ComfyUI-Impact-Subpack 就在 `detector` 下拉框里看不到（多位用户反馈）。现在 H3MD 自己登记这个文件夹（以及 `segm`），ComfyUI 自己的 models 和它认识的其它 models 文件夹（extra_model_paths、桌面版共享文件夹）都算；已装 Subpack 时不改动它的登记。
- Face refine: when ComfyUI-H3-FaceRefine is not loaded (not installed, ComfyUI too old, requirements missing), `detector` and `cut_detection` only offered `none` and a saved workflow turned red. They now offer the detector files H3MD finds and both cut modes, and the tooltip says what is missing and where to look (`IMPORT FAILED` in the startup log).
  修脸：ComfyUI-H3-FaceRefine 没加载时（没装、ComfyUI 太旧、依赖没装），`detector` 和 `cut_detection` 只剩 `none`，保存过的工作流会变红。现在会列出 H3MD 找到的检测模型和两种切换检测方式，提示里写明缺什么、去启动日志里搜 `IMPORT FAILED`。
- Face refine no longer stops the run when a part is missing (the pack not loaded, `detector = none`, the detector file not found): the video is saved without face refine and the node's text says why, like "nothing to refine" already did.
  修脸缺东西时（插件没加载、`detector = none`、找不到模型文件）不再中断：视频照常保存，只是不修脸，节点输出文字写明原因。

## [0.5.8] - 2026-11-19

### Fixed / 修复
- Keyframe workflow (multi-person swap): Prepare now finds the source's shot cuts and makes sure every shot has a keyframe (the skill's rule 'one keyframe per shot'). Before, keyframes were only spaced by max_gap_s; after a cut with no keyframe Remake could not tell who is who in the new shot and drew one person twice (cosplay MV, cut at 14.3 s). The cuts are listed in Prepare's info and the log.
  关键帧流程（多人换人）：Prepare 会找出原片的剪辑点，保证每个镜头都有一张关键帧。以前只按间隔挑，剪辑后的新镜头没有关键帧时，Remake 认不出谁是谁，会把一个人画成两个（cos 群舞片 14.3 秒剪辑处）。

## [0.5.7] - 2026-11-19

### Added / 新增
- Keyframe workflow (multi-person swap): new node **H3MD Keyframe 1b: Recast** (sequential). It runs the image edit itself, one person after another; each crop is cut from the keyframe where the people before her are already recast, so the neighbours in a crop are the new characters, never the originals. Before, all crops were cut from the source frame at once, and bits of a bigger original (wig, ears, tail, wide skirt, apron, socks) stayed around the new person. Sliders: `mask_grow` (default 0.25: how far each recast person's own area reaches past her box; 0.25-0.35 for cosplay-sized originals) and `protect_others` (default -0.05, only for the other people being recast; people who are not recast always keep their source pixels). It replaces the Text Encode / Sampler / VAE Decode chain between Prepare and Assemble; Assemble takes its job. Tested on a three-person cosplay dance: clean keyframes.
  关键帧流程（多人换人）：新节点 **H3MD Keyframe 1b: Recast 串行换人**。在节点里调用修图模型，一个人一个人换：后一个人从已经换好前面人的帧上裁，裁剪图里的旁人都是新角色，不会再是原角色。以前所有裁剪图一次从原帧裁出，原角色比新角色轮廓大时（假发、耳朵、尾巴、蓬裙、围裙、袜子），会残留在新人物周围。滑块：`mask_grow`（遮罩扩大，默认 0.25，cos 服 0.25-0.35）和 `protect_others`（只管要换的人，默认 -0.05；不换的人始终保留原图）。它替代 Prepare 和 Assemble 中间的 Text Encode / Sampler / VAE Decode；三人 cos 群舞实测关键帧干净。

### Changed / 修改
- The example workflow `Qwen Image 2.1 Keyframe.json` now uses Recast; the old parallel chain is no longer shipped (Assemble still accepts it, and has `mask_grow` / `protect_others` too, default as before).
  示例工作流 `Qwen Image 2.1 Keyframe.json` 改用 Recast；旧的并行链路不再随包（Assemble 仍兼容，也带两个滑块，默认值和以前一样）。
- The edit prompt also asks to remove every piece of the original sticking out around the new person and fill it with the background.
  修图提示词加了一句：原角色伸出来的部分全部去掉，补成背景。
- The keyframe nodes show the version in their names, like the Director. / 关键帧节点名字里显示版本号，和导演台一样。
- Tip: write shoes and legwear in each description line and what she does NOT wear (e.g. "bare legs and black stiletto heels; no socks, no boots, no apron"), or the editor copies the original's.
  提示：每行描述写上鞋和腿（光腿、袜子），再写明没有什么（如 "no socks, no boots, no apron"），否则修图会照抄原角色的鞋袜。

## [0.5.6] - 2026-11-18

### Added / 新增
- Keyframe workflow (multi-person swap): when `start_s` / `end_s` on H3MD Keyframe 1: Prepare cut a part of the source, the plan now records where that part sits in the original (`sourceTrim`: start and end in seconds). H3MD RH (RunningHub, 0.5.5-rh1) uses it to cut the same part when the whole original is uploaded there. Nothing else changes; plans without it work as before.
  关键帧工作流（多人换人）：Prepare 用 `start_s` / `end_s` 截了原片的一段时，计划里会记下这一段在原片里的起止秒数（`sourceTrim`）。RunningHub 版 H3MD RH（0.5.5-rh1）在那边上传整条原片时，靠它自动截出同一段。其他都不变；没有这一项的旧计划照常用。

## [0.5.5] - 2026-11-18

### Changed / 改动
- Keyframe workflow (multi-person swap): drag the source video into a Load Video node instead of typing its path in the input folder; `start_s` / `end_s` on H3MD Keyframe 1: Prepare recast only that part (cut with its sound; it becomes the plan's source video). The typed path still works for older workflows.
  关键帧工作流（多人换人）：原片直接拖进「加载视频」节点，不用再放进 input 文件夹填路径；Prepare 上的 `start_s` / `end_s` 只换其中一段（连同原声截出来，作为计划包的原片）。旧工作流填路径照样能用。
- Keyframe example: the prompt enhancer is removed. Prepare already writes the edit prompt for each crop; the enhancer only rewrote it and took most of the time (about 11 of 14 minutes for a 26 s clip). Its prompts now go straight into Text Encode.
  关键帧示例：去掉提示词增强器。Prepare 已经给每张裁图写好修图提示词，增强器只是再改写一遍，却占了大部分时间（26 秒原片 14 分钟里约 11 分钟）。现在 Prepare 的提示词直接进文字编码。
- Keyframe example: the `descriptions` box now holds correct lines for the three example pictures (it had wrong placeholder text). Without the enhancer the edit model follows the text: one simple line is enough when it matches the picture, but a line that contradicts it (gender, race, clothes) gives the wrong person. Tested: 26 s source, keyframes in 2 min 33 s, all three people right.
  关键帧示例：descriptions 换成和三张示例角色图对得上的描述（原来是乱写的占位）。不开增强器时修图照文字画：一句简单的、和图对得上就够；和图相反（性别、人种、衣服）就会换错人。实测：26 秒原片，关键帧 2 分 33 秒，三个人都换对。
- Director: a multi-person swap plan (swapEngine remake) now runs at a size Remake was trained on, whatever the Control Panel says: if the Control Panel size is not 1664x960 / 960x1664 / 2560x1472, the plan's size is used when it is one of them, otherwise 1664x960 (960x1664 for portrait). The run log and report say so. Before, a Control Panel at 1920x1088 stopped the run with an error.
  导演台：多人互换计划（swapEngine remake）自动用 Remake 训练过的尺寸：总控台尺寸不是 1664x960 / 960x1664 / 2560x1472 时，计划里的尺寸合规就用计划的，否则横屏 1664x960、竖屏 960x1664；日志和报告里写明。以前总控台是 1920x1088 会直接报错停下。

## [0.5.4] - 2026-11-17

### Fixed / 修复
- Face refine: when the hair runs off the bottom of the face crop (long hair in a wide shot), the redrawn head was pasted with a hard straight edge at the crop border, a visible box line on the chest. The paste mask now fades to zero towards every border of the crop, so the redrawn part blends into the original frame.
  修脸：远景长发超出脸部裁图下边时，贴回会在裁图边框处留下一条直边（胸前一道方框线）。现在贴回遮罩在裁图四周逐渐淡到 0，重画部分和原片自然过渡。

## [0.5.3] - 2026-11-17

### Changed / 改动
- Face refine now redraws the head with H3 inside a SAM3 head mask before VOSR2 (replaces the restore-only refine; small faces in wide shots come out clear and like the reference). The save node gets four inputs: `redraw_model`, `redraw_clip`, `redraw_vae`, `redraw_audio_vae` (the sampler's H3 model with the 768 turbo LoRA, text encoder, video VAE, audio VAE); the examples have them wired. The clip's own sound is locked, so lips keep their timing. Defaults: mask `head`, denoise 0.8, 6 steps, empty prompt, face close-up from the identity photo (or `redraw_face_image`). Up to 15 s is redrawn in one pass; longer clips are chained like the Loop (each part starts from the previous part's redrawn overlap, locked); results are cached in `face_mask/` and `face_layer/`. Needs `sam3.1_multiplex_fp16.safetensors` in `checkpoints/`. A redraw never runs across a hard cut. If the four inputs are not connected (an older workflow), the face picture or the SAM3 model is missing, the face is only restored as before and the report says why; a part that runs out of memory is only restored too.
  修脸改为：先用 SAM3 找头部遮罩，H3 只在遮罩里重画，再 VOSR2，按遮罩贴回（替换原来只修复不重画的修脸；远景小脸能修清楚、像参考图）。保存节点新增四个输入（H3 模型 + 768 加速 LoRA、文字编码器、视频 VAE、音频 VAE），示例已接好；原声锁定，口型不乱。默认：遮罩 head、强度 0.8、6 步、提示词空；15 秒以内一次画完，更长的像 Loop 一样接着画。需要 SAM3.1 模型（放 `checkpoints/`）。不会跨硬切重画；没接那四个输入（旧工作流）、缺脸部图片或 SAM3 模型时，照旧只做修复，报告里写明原因；某一段爆显存也只做修复，不中断保存。
- Example workflows: one speed LoRA only (LightX2V 768 turbo v1.2); DMAD, curveproj-compatible and 10ErosMax speed LoRAs removed. 示例工作流只留一个 768 加速 LoRA，DMAD 等其他加速 LoRA 全部删除。

## [0.5.2] - 2026-11-17
* **Fix / 修复: dull, damaged sound with high-res tiling (issue #5).** When the high-res pass has to run in 2 or more
  strips (long or large shots, e.g. 1080p over ~10 s), the strips cannot share the audio, so the audio was kept at the
  low-res pass's hand-over estimate - a mid-schedule guess, which sounds muffled / smeared. Generated audio (dialogue,
  sound effects, a generated song) is now finished after the strips with the same recipe the 1-strip high-res pass
  uses (same model, schedule, cfg, audio start and noise), with the final video scaled to the low grid and held, so
  the extra run is cheap (about a quarter of the frame, a few low-res steps). Locked audio (a song you give, audio
  reuse) is never touched and costs nothing extra; 1-strip runs are unchanged. The log says "audio finished on the low
  grid". Found by issue #5 (and the out-of-memory in #3 comes from tiling being off).
  修复：开高清分块且画面切成 2 条以上时（长镜头、大画面，比如 1080p 超过约 10 秒），声音沿用低清阶段半路的估计，
  听起来发闷、发糊。现在切条后用和整帧高清同样的做法，在低清尺寸上把生成的声音补完（画面用成片缩小后固定不动，额外开销小）；
  锁定的原声不受影响、不多花时间；不切条时完全不变。

## [0.5.1] - 2026-11-17
* **Diagnostic report: every setting, named, compared with the example workflow / 诊断报告：全部参数带名字，并和示例工作流对照.**
  Before, the report held each node's values as an unnamed list and left out bypassed nodes, so "is tiling on?" could
  not be read from it. Now: every node (inside subgraphs too) with its state (on / bypassed / muted, or off because its
  subgraph is off) and its settings by name (`settings.md` for people, `settings.json` for tools); a **Key switches**
  table at the top of report.md (seamless long video, style, size, interpolation, face refine, LoRAs and strengths,
  high-res tiling and its axis / overlap, low-res scale and steps, upscaler, cache and cache type, VRAM release,
  Remake model, motion control, semantic bridge, redraw); and **Differences from the example workflow**: the shipped
  example closest to yours is picked automatically, then nodes switched on / off, missing or added, and every changed
  value are listed (seed, plan text and media files are listed apart as per-run values). New checks: high-res tiling
  off (warning under 40 GB VRAM), a multi-person swap plan with the Remake model off or missing, H3MD / model nodes
  switched off against the example. Same privacy rules as 0.5.0.
  报告里列出每个节点的开关状态（开 / 旁路 / 静音）和带名字的全部参数；report.md 顶部加「关键开关」表；自动找最像的随包示例工作流，
  列出开关不同、缺少、多加的节点和每个改过的参数。新增检查：高清分块关着、多人换人计划但 Remake 模型没开。隐私规则不变。

## [0.5.0] - 2026-11-15
* **New / 新功能: multi-person swap with Viggle Remake** (plan `"swapEngine": "remake"`). Replace several people of a
  real video with your own characters; motion, camera, lip movement, timing and the soundtrack stay. The low-res pass
  runs Viggle Remake's swap model (its RemakeConditioning, the official 6 sigmas, keyframes held while sigma >= 0.9);
  the high-res pass is H3MD's usual one. The keyframes (0.4.9 `segments[].keyframes`) are painted frames of the result;
  every autosplit part takes up to 4 of its own, and a part without one borrows the nearest keyframe as an un-pinned
  reference instead of stopping the run. Needs ComfyUI-Viggle-Remake and
  `viggle_remake_swap_pruned_int8_convrot.safetensors` (Hugging Face `Viggle/Viggle-Remake`, install them yourself)
  wired to the Loop's `remake_model`; output 1664x960 / 960x1664 (or 2560x1472); 90-345 frames per part; window mode
  is turned off. Control Panel: 4th style **多人换人**; Director: **Remake 换人** switch with a keyframe list. The simple / full / Remake example workflows all carry a 'Remake model' loader wired to the Loop's
  `remake_model`; the 多人换人 style switches it on, every other style bypasses it (never loaded otherwise). New
  example workflow `H3MD Director Remake.json`. The keyframes are painted by your own agent or image editor:
  `skills/h3md-prompt` section 12d says how. Plans without `swapEngine` run and cache exactly as before.
  多人换人:导演台低清这一遍改用 Viggle Remake 换人模型,高清照旧;关键帧(画好的结果帧)由你自己的智能体或修图工具画,
  打成计划包导入;需要自装 ComfyUI-Viggle-Remake 节点和 Remake int8 模型,接到 Loop 的 `remake_model`。
* **New / 新增: keyframe workflow for the multi-person swap** - nodes **H3MD Keyframe 1: Prepare** and **H3MD
  Keyframe 2: Assemble**, example `Qwen Image 2.1 Keyframe.json`. One local run: core SAM3.1 tracks the people,
  numbers them left to right at the start ("图1换左二" / "picture 1 -> left 2"), picks keyframe moments at most ~5 s
  apart where the people stand apart, crops each recast person (skill 12d route A), lets any image-edit model redraw
  the crops (the example: Qwen Image 2.1 Edit + 2-step LoRA + official prompt enhancer), colour-matches and pastes them
  back (the people around keep their source pixels), writes the Remake plan (prompt per skill 12d step 6, length not
  past the source) and saves a plan .zip to `output/H3MD_plans/` for the Director's Import plan (local or RunningHub).
  Measured on our 26 s five-person demo (3 people recast).
  多人换人关键帧工作流:本地一次运行认人编号、挑关键帧、裁人、修图(修图模型自选)、对色贴回、写计划、打计划包。
* **New / 新增: diagnostic report** - Director button **Report 诊断报告**: one .zip for a bug report with the
  environment (GPU, VRAM and the run's peak, ComfyUI / torch / CUDA / H3MD versions, a fixed list of related node
  packs, models found or not), the node settings, a plan check (format, the Director's notes, prompt-skill rules:
  5+17n, window handoffs and windows without a timed action, Remake sizes / parts / keyframes, VRAM budget) and the
  last runs' H3MD log and errors, ranked as environment / settings / plan / skill / run problems. Privacy by
  whitelist: no user or computer names, folders, environment variables or media; paths cut to file names; prompts
  optional. Saved to `output/H3MD_reports/`, never sent. The GitHub bug form asks for it first.
  诊断报告：导演台一键生成，附在 issue 里；只收诊断必需的信息，不含个人信息和素材。
* **Keyframes now have a Director UI** (inside the Remake module) and are kept per part by autosplit.
  关键帧在导演台里可以直接编辑。

## [0.4.9] — 2026-11-13
* **New / 新增: keyframes - pin a picture at a moment of a shot** (plan field `segments[].keyframes`:
  `[{"image": "<media image id>", "at": <seconds from the shot's frame 0>}]`; no Director UI yet). The picture is a
  painted frame of the wanted result (e.g. a frame of the guide with each person replaced), placed with core
  `MiniMaxH3AddGuide` (needs an fl2va or fl2va/ref2va hybrid model). It steers only the low-res pass: the composition
  and who stands where are fixed early, the high-res pass only refines. Made for multi-person swaps, where people who
  cross each other could swap identities (11-13 test: three people, the second side-by-side swap came out with the
  wrong people). Works in segment mode, autosplit (each part keeps its own keyframes), window mode and multi-shot
  timelines. Plans without keyframes behave and cache exactly as before. Idea from Viggle Remake's pinned frames.
  新增关键帧：计划里给镜头加 `keyframes`（图片 id + 第几秒），把一张画好的帧钉在那一刻（需要 fl2va 或混合模型）；只在低清
  阶段起作用，定住构图和"谁在哪"，高清阶段只细化。用于多人换人（交错换位时不再对错人）。暂时没有导演台界面。

## [0.4.8] — 2026-11-13
* **Fix / 修复: `refVideoHeight: auto` now also limits the size of the reference media itself.** Before, auto only
  checked the shot's token total, so a large guide video (e.g. a 1088x1664 H3MD render used as `<Video 1>`) stayed at
  the top tier when the total fit; in the high-res pass every strip carries all reference rows, so the strips could
  not shrink (11-13: 12 strips, OOM warning, ~5.5 min per window step). Auto now also keeps the reference media
  (videos + pictures + RefMods) under 45,000 tokens (scaled with the GPU budget like the total) and steps the video
  down until both fit; the log shows both numbers and why it stepped down. 480p guides (up to ~15 s) are unchanged;
  `native` / `480` / `576` / `720` are unchanged. 参考视频自动缩小（auto）新增第二条限制：参考素材本身（视频 + 图片 +
  RefMod）不超过 45,000 token（按显存比例缩放），超了就继续降档；日志写明两项数值和降档原因。480p 原片（约 15 秒以内）和手动指定高度不受影响。
* **Fix / 修复: window mode high-res strips counted the whole guide video.** Window mode crops a timeline-aligned
  guide (and its soundtrack) to each window, but the strip planner counted all of its frames, so every strip looked
  bigger than it is (11-13: 72 latent frames counted, 52 used -> 12 strips). It now counts the window's share.
  无接缝（窗口）模式：高清分块估算原来按整段原片算参考视频，实际每个窗口只带自己那一段；现在按窗口长度算，分块数不再虚高。

## [0.4.7] — 2026-11-13
* **Face refine save node: optional H3 Face Stitch Plus and Face Flow Lock** (ComfyUI-H3-FaceRefine-Plus by AnniCodex,
  MIT; install it yourself, it is not bundled). Two new optional inputs at the end of
  `H3MD Save Video Face Refine And Interp`: `stitch_mode` (`standard` = as before / `plus` = H3 Face Stitch Plus:
  antialiased paste, light and colour from the original frame, detail from the refined face; `colour_match` 0 = no
  tone transfer) and `flow_lock` (H3 Face Flow Lock after every pasted chunk, per frame, so it fits the chunked save).
  Old workflows open unchanged; if the Plus pack (or opencv-python) is missing the node falls back / skips and says so
  in the report. 修脸保存节点新增两个可选输入：`stitch_mode`（standard / plus）和 `flow_lock`；需要自己装
  ComfyUI-H3-FaceRefine-Plus；没装就退回原样并在报告里说明；旧工作流不受影响。
* **`example_plans/h3md_silver_warrior_1min_v3.zip`**: a 1-minute fight demo plan (9 shots) to import in the Director.
  新增 1 分钟打斗示范计划。

## [0.4.6] — 2026-11-13
* **Fix / 修复: motion copy no longer fails when the guide video is a few frames shorter than the shot.** A shot that
  follows its video is rounded up to 5+17n; without `motion_transfer:` in the prompt it was not rounded down, so the
  last window needed more guide frames than the video had (TARA: "Native temporal guide and output must have identical
  temporal grids", after minutes of sampling). Now a guide that ends up to 16 frames before its shot keeps its last
  frame to the shot length - segment mode and window mode - and the log says so (`[H3MD RefVideo] ... last frame is
  held`). 动作复刻：原片比镜头短不超过 16 帧时，用最后一帧补齐（分段 / 窗口模式都一样），不再跑到最后一个窗口才报 TARA 的
  "identical temporal grids"，日志会写明补了几帧。
* **Control Panel style "motion copy" adds `motion_transfer:` for you.** When the plan has the trigger neither in the
  global nor in a shot prompt, the plan sent to the server gets it at the start of the global prompt (done when the
  workflow is queued; the workflow and the editor keep your text). 总控台选「动作复刻」，计划里没写 `motion_transfer:`
  时，排队时自动加在全局提示词最前面（工作流和编辑器里的文字不变）。
* **High motion style = one weapon LoRA per pass.** Low-res `H3-Bunny_weapon_combatV1` 0.85, high-res 0.5, every other
  row off (the rows stay in the list; the high-res 768 turbo strength stays 1.0). Measured by a 1-minute fight (11-12).
  高动态风格：低清、高清各只开一个 weapon（低 0.85 / 高 0.5），其余行关闭但保留在清单里；高清 768 加速强度仍是 1.0。

## [0.4.5] — 2026-11-09
* **试镜 / Trial** (the Control Panel box that was "which segments"): with **Seamless long video** on, a number now
  means a part of the timeline, shown as buttons under the box — `试镜 2 · 镜头 5–9 · 0:58–1:52` (shots and seconds).
  Click a button to render only that part (shift-click adds more); any part can be tried, not just the first. A part
  after a hard cut runs on its own and is the same take the full run makes (the full run reuses it); a part that
  continues a long take (no hard cut inside ~60 s) also uses / renders the part before it. When every other part is
  cached, a tried part is a new take inside the finished video. Without Seamless long video the box works as before
  (segments). The internal input name `segments_to_run` is unchanged, so old workflows open as they are.
  控制台的"跑哪几段"改名**试镜**。开了无接缝长视频时，框下面一排按钮列出每一段（哪几个镜头、几秒到几秒），点一下就只跑那段，
  任何一段都能单独试，不只第一段；硬切后面的段单独跑，结果和整片里的一样，出整片时直接复用；接着长镜头的段会先用 / 先补跑上一段。
  没开无接缝时照旧按分段算。旧工作流照常打开。
* The Director's preview (`/h3md/autosplit`) and the parsed plan carry a `trial` list (parts → shots, seconds).

## [0.4.4] — 2026-11-09
* **Fix / 修复: `delete_cache_after_save` now really deletes the run cache.** The save nodes refused every run folder
  ("holds non-H3MD files") because of the run's own `run.json` (its base seed), so the cache was always kept. Folders
  with any other foreign file are still never deleted. 保存后删缓存现在真的会删：以前每个运行文件夹里都有自己的
  `run.json`（记录种子），保存节点把它当成外来文件，一律不删。有其他外来文件的文件夹仍然不删。
* **Prompt skill: motion copy written as the guide really is / 提示词技能：动作复刻照原片的样子写** (`skills/h3md-prompt`,
  English and Chinese). Camera as in the guide (fixed → locked-off, moving → tracking shot); keep the guide's
  surroundings (steadiest, first choice for a moving camera) or name a new setting; cuts in the guide written as
  `[Ns cut]` at the guide's times; "dance" only for dances. Measured 11-09: a dance-only prompt on a tracking-shot
  guide made the second window jump back to the scene words; the matching prompt joined cleanly.
  镜头照原片（固定 → locked-off，在动 → tracking shot）；景物用原片的（最稳，镜头在动时首选）或写明新场景；原片的剪辑按原片时间写
  `[Ns cut]`；原片是跳舞才写 dance。11-09 实测：跟拍的原片配"原地跳舞"的提示词，第 2 个窗口跳回提示词里的场景；照原片写就接得上。

## [0.4.3] — 2026-11-08
* **Lighter Control Panel / 总控台更轻**: each LoRA dropdown now holds only its own LoRA until it is opened; the full
  `models/loras` list is filled in then. Before, every row carried the whole list (with ~450 LoRAs, 16-18 rows made
  7,000-8,000 hidden entries - most of the page), so every click in the panel rebuilt them (~76 ms). Nothing changes in
  how the panel is used. 每个 LoRA 下拉框在点开之前只放这一行的 LoRA，点开时才填完整列表。以前每一行都带着整个列表
  （450 个左右的 LoRA × 16-18 行 = 7000-8000 个隐藏选项，占了整页的大半），总控台每点一下都要重建（约 76 毫秒）。用法不变。

## [0.4.2] — 2026-11-08
* **Window mode is no longer marked beta / 窗口模式不再标"测试版"**: 0.4.0 is the finished version. README headings and the
  manual node's name (`H3MD Context Windows (window mode)`, class name unchanged - saved workflows keep working) drop
  "beta" / "exp". 0.4.0 是定版：README 标题和手动节点的显示名去掉 "beta" / "exp"（类名不变，保存的工作流照常能用）。

## [0.4.1] — 2026-11-08
* **Clear message on ComfyUI older than 0.44 / ComfyUI 低于 0.44 时给清楚的提示**: with "Seamless long video" ticked, the
  Loop now stops before anything runs with "needs ComfyUI 0.44 or newer" (this ComfyUI's version and what to do)
  instead of a bare `cannot import name 'MiniMaxH3ContextWindowsNode'`. 勾了"无接缝长视频"时，Loop 在开始前就说明需要
  ComfyUI 0.44 或更新（附当前版本号和怎么办），不再是看不懂的导入错误。
* **Registry / Manager description / 注册表和 Manager 里的一句话简介**: now mentions seamless long takes (CCW) and local
  redraw. 改成写无接缝长镜头（CCW）和局部重绘。

## [0.4.0] — 2026-11-08 — CCW: Chained Context Windows (seamless long video, includes 0.3.8)
**In short / 简要：**
* **CCW = Chained Context Windows (接续窗口)**: the name of this release. ComfyUI's core Context Windows render a long
  video as overlapping windows and average the overlaps; CCW runs the windows in time order and each window takes
  over the overlap from the one before it - no averaging, so no double images. With it, continued shots are rendered
  on one timeline without seams (below). 本版名字。ComfyUI 核心的 Context Windows 把长视频分成互相重叠的窗口，
  重叠部分两边平均；CCW 按时间顺序跑窗口，重叠部分接手前一个窗口的结果，不平均，所以没有重影。续接的镜头因此在一条
  时间线上生成，没有接缝（见下）。
* **接续窗口 (default on, `"chain": false` turns it off)**: in the low-res pass the windows run in time order and a
  window's overlap with the windows before it takes their result of the same step (input and output) instead of being
  averaged - averaging two different pictures was the ghosting / double layers of the 2:20 test video. Same amount of
  computation; default low-res overlap 22 (56 with chain off). Timed actions go to the window that really draws those
  frames. Tested 11-08: a 27 s piece of the 2:20 video and two turns (spin, back view to facing each other) - no double
  images, same speed per window. 低清阶段按时间顺序跑窗口，重叠区直接用前一个窗口这一步的结果，不再两边平均（平均两份
  不同的画面就是 2 分 20 秒测试片里的重影）；计算量不变；低清默认重叠 22（关掉时 56）；带时间的动作交给真正画那几帧的
  窗口。11-08 实测：2 分 20 秒片子里的 27 秒和两次转身（转圈、背影转面对面）都没有重影，每个窗口速度不变。
* **Seamless long video (window mode, beta; needs ComfyUI 0.44+)**: one switch on the Director. Continued shots are rendered together on
  one timeline (no continuation seams); hard cuts stay cuts; long plans are grouped only at hard cuts. 导演台一个开关：
  续接的镜头在同一条时间线上一起生成（没有续接接缝）；硬切照常；长计划只在硬切处分组。

* **Semantic bridge: wired but bypassed / 语义桥：接着但旁路**: the style buttons no longer switch a semantic bridge on
  (up to 0.3.8: balanced v1 T8_Compat 0.12, high motion BUNNY ActionLogic 0.12). The simple workflow has an H3MD Semantic
  Bridge node wired to the Loop, bypassed: select it and press Ctrl+B to use it. Reason: the bridge's author does not
  support reference-conditioned use (worse singing / lip-sync), and an 11-08 A/B with and without it looked the same.
  The Control Panel's `semantic_bridge` output stays (old workflows keep their links) and is always off; old caches made
  with balanced / high motion are not reused. 风格按钮不再自动开语义桥（0.3.8 以前：均衡 v1 T8_Compat 0.12、高动态
  BUNNY ActionLogic 0.12）；清爽版里加了一个接好的语义桥节点，默认旁路，选中按 Ctrl+B 打开。原因：原作者不支持带参考
  的用法（唱歌口型变差），11-08 开桥 / 关桥对比画面一样。总控台的 `semantic_bridge` 口保留（旧工作流不断线），永远是关；
  以前用均衡 / 高动态跑的缓存不会再复用。

* **Defaults match the Director switch / 默认值和导演台一致**: a plan with just `"windowMode": true` (or `{}`) now
  means low-res 243 / 22 chained, high-res 175 / 22 - what the Director's switch writes (it was a 124-frame window).
  The manual node H3MD Context Windows gets a `chain` switch (default on) and defaults 243 / 22. Docs: the "big
  changes dissolve" limit is gone from README / CHANGELOG (接续窗口 fixed it), technical reference has a window-mode
  section, the speed note is re-measured (about +25 %: low-res +11 %, high-res +34 % per frame).
  计划里只写 `"windowMode": true`（或 `{}`）时，现在就是低清 243 / 22 接续、高清 175 / 22，和导演台开关写的一样（以前是
  124 帧窗口）；手动节点 H3MD Context Windows 加了 `chain` 开关（默认开），默认 243 / 22。文档：README / CHANGELOG 去掉
  "大变化会叠化"的限制（接续窗口已解决），技术参考加了窗口模式一节，速度说明重新实测（约慢 25%：低清每帧约 11%，高清约 34%）。

* **New node: H3MD Redraw / Preview / 新节点：局部重绘 / 低清预览**: between the Director and the Loop (bypassed in
  both example workflows; Ctrl+B to use). `preview` = low-res pass only; `redraw` = seconds of a finished run to render
  again with a new seed, e.g. `20-24` or `1:12-1:15` (whole-video time). Kept off the Director's editor so the old
  interface stays as it is. Fixed with it: clearing `redraw` (or bypassing the node) now keeps the redrawn take; before,
  the next run rendered the original take over it. A redraw may also use a changed prompt, added / replaced pictures
  and reference media (only the redrawn seconds use them); size, length, hard-cut positions and the locked song can not
  change (a moved cut is refused). 重画前可以改提示词、加或换参考图和参考音视频，只有重画的那几秒用新的；分辨率、长度、
  硬切位置、锁定的歌不能改（移动了硬切会报错）。 The first full-size test (11-08, 2:20 video, seconds 20-24) showed
  double exposures at both edges and a washed-out picture inside: the soft 3-latent edge and the downscaled final
  latent used as low-res context are gone (hard edges, the group's own low-res result as context). 接在导演台和 Loop 之间（两个示例工作流里都接好、默认旁路，Ctrl+B 打开）。
  `preview` = 只跑低清；`redraw` = 已跑完的片子里要换种子重画的秒数，如 `20-24`、`1:12-1:15`（整条片子的时间）。不放进
  导演台编辑器，原来的界面不变。同时修好：清空 `redraw`（或旁路节点）后保留重画的那一版；以前下一次运行会用原来的那一版把它盖掉。
  第一次整片实测（11-08，2 分 20 秒片子的第 20–24 秒）两个边缘有双重曝光、中间发白发虚：去掉了 3 个 latent 的渐变边，
  低清阶段周围不再用缩小的最终 latent，改用这一组自己的低清结果。
* **Interpolation at hard cuts / 插帧遇到硬切**: with interpolation on (48 / 60 fps), the frame between two shots is no
  longer a RIFE blend of both shots (one melted frame at every hard cut, in every version so far); it repeats the
  nearest original frame. Same frame count, timing and audio; also in H3MD Face Refine Save. 开插帧时，两个镜头之间的那
  一帧不再由 RIFE 把两个镜头混在一起（以前每个硬切都有一帧糊帧，所有版本都有），改用最近的原帧；帧数、时长、声音不变；修脸保存节点同样。
* **8-bit frame cache in the example workflows / 示例工作流默认 8 位缓存**: Loop Options `cache_dtype = uint8` in both
  example workflows (the node's own default stays fp16, so old workflows keep their caches). Half the disk: about 9 GB
  per minute of 1920x1088 video instead of 18 GB (the 2:20 test: about 21 GB instead of 41 GB). Compared on that video:
  the saved video differs by at most 1/255 per value and 10 s encoded from either cache is equally close to its input
  (40.7 dB) - no visible difference. Keep fp16 for 10-bit output and for a video already started with fp16 (the cache
  format is part of the resume settings). 两个示例工作流的 Loop Options 改成 `cache_dtype = uint8`（节点自己的默认值仍是
  fp16，旧工作流的缓存照常能用）。缓存减半：1920x1088 每分钟约 9 GB（原来 18 GB；2 分 20 秒的测试片约 21 GB，原来 41 GB）。
  在这条片子上对比：存出来的视频像素值最多差 1/255，两种缓存各编 10 秒，和原画面一样接近（40.7 dB），看不出区别。存 10 位
  视频、或接着做一条用 fp16 开始的视频时，用 fp16（缓存格式算在续跑设置里）。

* **Docs / 文档**: README step by step for seamless long video (preview → full → redraw → rerun → resume), what the
  cache keeps and how big it is (about 18 GB per minute at 1920×1088 in fp16; a 2:20 run left 41 GB; every redraw keeps
  the previous take of its group). README 加无接缝长视频的操作步骤（预览 → 完整版 → 局部重绘 → 重跑 → 续跑）和缓存说明
  （1920×1088 fp16 约每分钟 18 GB；2 分 20 秒的片子 41 GB；每次局部重绘保留这一组的旧版本）。

**Details / 详细：**
* Core MiniMax H3 context windows (ComfyUI 0.44+) with H3MD additions: low-res windows 243 / overlap 22 (chained),
  high-res 175 / 22. 核心上下文窗口 + H3MD 扩展：低清 243 / 重叠 22（接续窗口）、高清 175 / 22。
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
* Local redraw: `windowMode.redraw [[start_s, end_s]]` re-renders only those seconds of a finished run (new seed, hard
  edges joined by the model like a continuation, the low-res pass keeps the group's own low-res result around them,
  only the windows touching them are computed); the rest stays frame-identical and the previous version is kept.
  局部重绘：只重画已完成视频的指定秒数（换种子；边缘不做渐变，由模型像续接一样接上；低清阶段周围用这一组自己的低清结果；
  只算碰到的窗口），其他部分一帧不变，旧版本保留。
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
* Autosplit places its cuts at quiet moments of the reference video again (0.3.5 bug: the motion profile never reached
  it); only over-budget shots with a reference video split differently. Window mode never splits, so it is unaffected.
  自动拆段重新按参考视频动作安静的地方切（0.3.5 的 bug：动作曲线没传进去）；只影响超预算、带参考视频的镜头。窗口模式不拆段，不受影响。
* A window that covers a continuation adds only the next shot's new sentences ("with no cut, the same shot continues",
  no second `[Shot 1]`, no repeated style lines). Found on a 2:20 two-person test video, where a continued shot came out
  as a cut; the rest came from the prompts (now in the skill: time new actions, write place and outfit in full in every
  shot). With averaged windows (`"chain": false`) a big change inside one take (turning around, back view to front
  view) can dissolve where two windows overlap; 接续窗口 (default) removes this. 跨续接的窗口只加下一个镜头的新句子（不再重复 `[Shot 1]` 和风格句）。
  2 分 20 秒两人测试片里续接镜头变成了硬切，其余原因在提示词（skill 已加：新动作写时间、每个镜头写全场景和服装）。
  窗口平均（`"chain": false`）时，同一条连续镜头里的大变化（转身、背影变正面）在两个窗口重叠处可能叠化；默认的接续窗口没有这个问题。
* Off = the old behaviour (same cache keys), except that motion copy no longer uses depth / pose control. 关闭时和原来
  一样（缓存键不变），唯一的变化：动作复刻不再用深度图 / 骨骼图控制。
## [0.3.8] — 2026-11-08
* Same code as 0.3.7. The 0.3.7 package was flagged by the registry scan only because CHANGELOG.md quoted one of the
  spellings the scan looks for; the text is reworded, and the self-check test now reads the documentation files too.
  代码和 0.3.7 完全一样。0.3.7 被注册表标记，只是因为 CHANGELOG 里原样引用了扫描会查的写法；已改写，自查测试现在
  连说明文档一起查。

## [0.3.7] — 2026-11-08
**In short / 简要：**
* Passes the Comfy Registry checks again (every version since 0.2.0 was "Flagged", so ComfyUI-Manager only offered
  0.1.2). 重新符合 Comfy 注册表的检查（0.2.0 起每个版本都被 Flagged，ComfyUI-Manager 只能装到 0.1.2）。
* H3MD reads and writes files only in allowed folders, on every ComfyUI: media from ComfyUI's input / output / temp
  folders, videos into the output folder, the cache into a `<drive>/H3MD_cache` folder or ComfyUI's temp folder.
  Other folders: list them in `<ComfyUI user folder>/h3md/config.json` (`{"folders": ["D:/media"]}`).
  本机和服务器一样，H3MD 只在允许的文件夹里读写：素材从 input / output / temp 读，视频存进 output，缓存放在
  `<盘>/H3MD_cache` 或 temp。别的文件夹写进 `<ComfyUI 用户文件夹>/h3md/config.json`。
* The settings `H3MD_PATHS`, `H3MD_MEDIA_DIRS` and `H3MD_SHARED_RESUME` (read from the system before) are now keys of
  that file: `"mode"`, `"folders"`, `"shared_resume"`. 原来的环境变量改成这个文件里的 `"mode"`、`"folders"`、
  `"shared_resume"`。

**Details / 详细：**
* What the registry flagged (seen with `include_status_reason=true`): settings read from the system
  (`h3md_paths.py`, `h3md_cache.py`), a bound-function call in `web/js/h3md_control_panel.js` (matched by a network
  rule), and in 0.2.x a dynamic import; the policy review of 0.3.0 / 0.3.5 found "arbitrary-file-read, path-traversal". 注册表标的是：
  从系统读设置、控制台 JS 里绑定函数的写法（被网络规则误判）、0.2.x 的动态 import；0.3.0 / 0.3.5 的策略复审结论是
  "任意读文件、路径穿越"。
* Fixed: a relative media path with `..` could leave the input folder (Director, probe, plan export); the probe
  route and plan export could read any media file on a local ComfyUI. Now both are limited as above. 修复：带 `..` 的
  相对路径能跳出 input 文件夹；本机时探测接口和计划导出能读硬盘上任何媒体文件。现在都按上面的规则限制。
* `output_folder`: a relative name is a subfolder of the output folder; an absolute folder must be inside it or listed
  in the config file, otherwise the video is saved into the output folder (a warning says so). `cache_dir`: a folder
  outside the allowed places falls back to the automatic cache folder (warning). `output_folder` 填相对名字 = output
  的子文件夹；绝对路径必须在 output 里或配置文件里列出，否则存到 output（有提示）。`cache_dir` 不在允许的位置时改用
  自动缓存文件夹（有提示）。
* Control Panel: the run check wraps `app.queuePrompt` with an arrow function instead of a bound function. 控制台的
  运行前检查改用箭头函数。
* New test `tests/test_registry_scan.py` checks the shipped files for the flagged patterns. 新增注册表模式自查测试。

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
* Registry scan: no dynamic-import, eval or RegExp-exec spellings left in the shipped files.

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
* `__init__.py`, `h3md_director.py`, `h3md_depth_control.py`: plain imports instead of dynamic imports.
  改用普通 import。
* `h3md_director_refmod.py`: own argument mapping instead of `inspect.Signature.bind` (flagged as a network bind).
  不再用 `.bind`。
* `h3md_cache.py`: **removed** the `H3MD_CACHE_DEFAULT` system setting; set `cache_dir` on the Loop instead.
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
