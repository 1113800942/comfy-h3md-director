# H3MD 分镜提示词（中文版）

English: [SKILL.md](SKILL.md)。装成 skill 时用的是英文版 SKILL.md，这份中文版供阅读参考。

## 适用范围
- **只在用 H3MD 节点时用本 skill**：H3MD Director（或 Director + RefMod）、H3MD Loop Sampler，提示词要填进 Director 的各个 shot。
- **用普通 H3**（官方 API、MiniMax Hub、ComfyUI 自带 H3 节点、单段生成，或没提 H3MD）时，不用本 skill，改用 MiniMax 官方的 [`h3-prompt-writing`](https://github.com/MiniMax-AI/MiniMax-H3) skill（`npx skills add https://github.com/MiniMax-AI/MiniMax-H3 --skill h3-prompt-writing`）。不要把下面 H3MD 专有的规则（Continuity 句自动加、自动拆段、RefMod、depth/SAM3、标签自动编号、globalPrompt）套到普通 H3 上。
- 不确定用哪个时，先问一句：“这次是用 H3MD 还是普通 H3？”

H3MD Director 里每个 shot 都是一次独立的 H3 生成（太长的 shot 会被自动拆成几段续接，见第 2 节）。所以每个 shot 的提示词都是一份完整、合规的官方 H3 提示词（格式同 `h3-prompt-writing`），再加上 H3MD 自己的时间轴、标签、接缝规则。

## 0. 开始前先确认
- 总时长、shot 数量由场景决定，不平均切。
- 每个 shot 挂了哪些素材：图片（最多 9）、视频（最多 3）、音频（最多 3）、RefMod；哪个视频开了 depth、哪个是 locked 音频。
- **先看每张参考照片**：背景是不是纯色 / 灰色影棚、表情是什么样、有没有地板。这些都会被模型照搬（见第 9 节）。
- 每个接缝是硬切（overlapIn 0）还是续接（22 或 39 帧）。
- 能读到 `h3-prompt-writing` skill 时，用它的完整格式细则。下面是要点。
- **不要给自己找事**：场景和道具越简单越稳。不写镜子、玻璃反射、木地板、地砖、条纹、网格、水面倒影这类每段都可能长得不一样的纹理；地面只写一种纯色、无纹理的材质（如 seamless polished cream marble in one solid color）。

## 1. 选格式（官方规范）
### 有任何参考素材 → full-reference 六段式，顺序固定
```
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```
- `subject_definitions`：每个要追踪的内容一行。只写定义和特征，不写时间、不写镜头。图片只用来定义人物、场景、服装时，写进 `<Subject N>` 里（“`<Subject 1>` is the woman in `<Picture 1>` …”），不单独占一行。`<Picture N>` 单独成行只用于首帧、关键帧、尾帧、构图锚点。`<Video N>` 只表示整段结构来源（动作、运镜、剪辑节奏、编辑），视频里被复用的人或物仍然用 `<Subject N>`。说话人对应的音色参考写成 “`<Audio 1>` is the voice-timbre reference for `<Subject 1>` (S1).”
- `summary`：一段短英文，开头写任务类型前缀，可用 ` + ` 组合：`keyframe completion`、`reference generation`、`video editing`、`video continuation`、`audio reuse`、`audio reference`。不引入新标签。视频只提供动作或运镜时属于 `reference generation`，不是 `video continuation`。
- `retention_analysis`：每个标签一行。画面类标记：`fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`；音频标记：`fully_copy`、`partially_copy`、`reference`、`weak_reference`。格式：`<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - ...`。这里不写 (Sx)。
- `detailed_description`：先用一两句英文定风格，再写 `[Shot 1] ...`。每个镜头写清构图、人物外观和位置、环境和光线、动作和状态变化、运镜（类型、幅度、速度）、当前声音，以及参考内容在哪里生效。生成任务一般 350–500 个英文词。
- `overall_soundscape`：全片环境声和物理声。`non_diegetic_music`：只有观众听得到的配乐，写乐器、速度、强弱变化；没有就写 `N/A`。对白和歌词不在这两段重复。

### 完全没有参考素材 → base 模式（T2VA / I2VA / FL2VA / L2VA）
- 字段：`integrated_multimodal_description`、`overall_soundscape`、`non_diegetic_music`。
- I2VA 第一行固定写：`For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.`，空一行再写字段。FL2VA、L2VA 用 `h3-prompt-writing` 里的对齐句，秒数保留两位小数。

### 通用写法
- 正文英文。对白、歌词、画面里的文字保留原语言，写成 `<d>[Mandarin] ...</d>`。听不清写 `[unclear]`。
- 说话人用全片稳定的 (S1)、(S2)…，按实际发声顺序分配；参考主体说话写 `<Subject N> (S1)`。
- 用具体的视觉和声音细节，不用 cinematic、beautiful 这类空词。
- 少写打光形容词（glowing、radiant、dramatic light、shimmering）和“光线越来越亮”之类的变化：长视频会被一段段放大。光线写成固定状态（“the lighting stays the same from start to end”）。

## 2. 时间轴和长度
- 长度是 24fps 下的 **5+17n 帧**（Director 会对齐）：124 f≈5.17 s、226 f≈9.42 s、294 f≈12.25 s、362 f≈15.08 s、396 f=16.5 s、719 f≈29.96 s。按场景定长度，取最近的 5+17n。
- `[Shot 1]` 不写时间；之后写 `[Shot N] At MM:SS.mmm, ...`，时间都是本 shot 自己的时间，不写原片时间码。最后一个镜头的时间要小于 shot 长度。
- 续接 shot 的长度包含 overlap，成片会裁掉开头 overlap，所以**新内容 = 长度 − overlapIn**（22 帧≈0.92 s，39 帧≈1.63 s）。规划总时长时按新内容累加。
- 有参考视频时，shot 长度跟随这个视频（从 trimStart 到结尾，向上取整到 5+17n，续接时再加上 overlap）。**分镜总长不要超过参考视频的帧数**：多出来的帧没有动作可跟，会出怪画面（取不超过它的最大 5+17n）。
- 镜头要少，不要碎切。

### 自动拆段（autosplit，默认开）
- 一个 shot 超过显存 token 预算时，Director 自动把它拆成几段等长的续接（每段 5+17n，段间 39 帧重叠），拆点尽量放在参考视频动作安静的地方。不需要手动拆。
- **拆出的每一段都拿到整个 shot 的提示词**（续接段前面自动加 Continuity 句）。所以：
  - 有参考视频（动作复刻）的长 shot：动作由视频逐段给出，提示词写成“一镜到底、持续状态”即可，16 段也稳。
  - 没有参考视频的长 shot：不要写按时间先后发生的事件（“先走到海边，再坐下，最后回头”）或 `[Shot 2] At 00:20` 这类镜头切换，否则后面的段会把开头重演一遍。写成**持续、可循环的状态**（“she walks slowly along the water's edge, the camera tracks beside her at walking pace”）。需要剧情先后时，手动分成几个 shot，各写各的提示词。
  - 一个 shot 只写一个连续镜头（`[Shot 1]`），不再加子镜头。

### 窗口模式（0.4.0 CCW：导演台的“无接缝长视频”开关）
- 计划照常写：多个镜头、续接镜头（`overlapIn` 22/39）、硬切（`overlapIn` 0）。打开开关后（计划里是
  `"windowMode": {"length": 243, "overlap": 22, "highLength": 175, "highOverlap": 22}`），续接的镜头会被接成一条时间线、
  一次采样出来，没有接缝；硬切保持干净的硬切。一组最长约 60 秒（内存小于 40 GB 时 30 秒），在放得下的最后一个硬切处分组；
  一镜到底超过这个长度时，系统会提示在哪里加一个硬切。（计划里只写 `"windowMode": true` 也是这组数值。）
- 低清窗口是接续窗口：后一个窗口接着前一个窗口画，不再两边平均，所以连续镜头里的大变化（转圈、背影转正面、跑步时背景一直在换）
  在窗口重叠处不会再叠化（11-08 实测）。大的变化仍然要写时间，这样只有一个窗口演它。
- 窗口模式下自动关闭：自动拆段、深度图 / 骨骼图控制（视频改作普通参考视频；选动作复刻时也关闭）、RefMod（人物一致性靠参考照片）。
- 长镜头里的时间（推荐）：每个动作、每句歌词写上从本镜头开头算起的时间，用平常的写法
  `At 00:07.9 she sings: <d>[English] ...</d>.`，或者 `[8s] she raises one hand, then lowers it.`。系统把每一条只分给一个
  窗口（约 8–10 秒），并告诉那个窗口在第几秒，所以不会演两遍、唱两遍；没有动作的窗口自动写“延续，不加新动作”。
  只有时间后面的第一句话属于它，其余文字整个镜头共用。两个动作隔约 8 秒。不写时间时每个窗口拿到整个镜头的文字，只能写持续的状态。
- 续接镜头：开头写 "The same ... continues without a cut"，先写上一个镜头结束时的状态，每个新动作、每次镜头变化都写时间
  （`[3s] she gradually slows down ...`），并写成慢慢变化。实测（11-08）：不写时间的 "she slows down, stops at the railing and
  turns toward the sea, the camera settling into a medium shot" 变成了一个硬切，后一个窗口又演了一遍（重影）。
- 场景和光线：同一条连续镜头里的每个镜头（包括续接镜头）都要用同样的文字写全。每个窗口只知道自己镜头的文字：
  只写 "the same boardwalk" 的续接镜头生成了另一座栈桥（旧木头、串灯），约一秒内叠化过去（11-08 实测）。
- summary 里带 “then / after / finally” 的句子会自动从窗口提示词里去掉；最好 summary 本来就只写不变的东西。
- 参考照片自动跟着窗口走：一个窗口只带它自己文字里提到的照片。
- 对白和歌词一律写在 `<d>[English] ...</d>` 里（实测：不加会说得听不懂）。
- 参考照片里没有的服装：在每个镜头的人物定义里用**同样的句子**写全（款式、领口、袖子、长度、面料、颜色，并写 "opaque, not see-through"）。硬切两边除了文字和照片什么都不共享（11-08 实测：只写 "a flowing light-blue summer dress"，切点前是细肩带、腰部半透明蕾丝，切点后变成泡泡袖）。服装要求严格就加一张服装图。
- `Duration:` 行由系统改写（每个窗口写自己的时长）。
- `"preview": true` 只跑低清（看动作和构图，之后正式跑会直接用它）；`"redraw": [[4, 8]]` 只重画已完成视频的第 4–8 秒（换种子），其他部分一帧不变。
  工作流里用 H3MD Redraw / Preview（局部重绘 / 低清预览）节点设置这两项（在导演台和 Loop 之间，Ctrl+B 取消旁路）：`preview` 开关；`redraw` 写整条视频的秒数（`20-24`、`1:12-1:15`）。只有几秒不好时用局部重绘，不用重跑整组；满意了清空 `redraw` 就保留这一版。重画前可以先改这个镜头的提示词、加一张参考图（比如从衣服画对的镜头里截一张，写明"只参照裙子，不参照姿势和背景"），只有重画的那几秒用改过的内容；重画不能改分辨率、长度、硬切位置、锁定的歌。
- 只靠文字的衣服，每过一个硬切都会被重新"设计"（扣子、肩带、面料会变）：每个镜头都把衣服写全，写明它没有什么（"no buttons, no zipper"），别把另一个人的衣服词（如 "button-up shirt"）放在描写她的句子旁边。
- 开插帧（48 / 60 fps）时硬切处是干净的（两个镜头之间不会混出一帧）。
- 一个镜头里写硬切（`[7s cut] [Shot 2] ...`）也可以，但分成一个 `overlapIn` 0 的新镜头更简单。

## 3. 接缝：硬切还是续接
- `overlapIn = 0` 硬切：独立生成、首尾直接拼。场景或背景换了就用硬切。
- `overlapIn = 22 / 39` 续接：新 shot 以上一个 shot 最终 latent 的最后 22/39 帧开头。同一个连续镜头、动作要接上时用续接。
- 系统在续接 shot 前自动加一句 `Continuity: the first X seconds are carried over unchanged ...`（总是开）。所以不要写 “continuing from the previous shot” 这类话，也不要用 `<Video N>` 或 `video continuation` 去指上一段，因为上一段不是参考素材。
- 但手动续接的 shot，`[Shot 1]` 要用平实英文写出**上一段结尾的具体状态**：人物位置、姿势、朝向、服装、光线、机位和正在进行的动作，开头 overlap 那几秒保持这个状态，新动作从 overlap 之后开始。
- 画面没有交叉淡化，crossfade 只作用于音频。

## 4. 参考标签编号（按本 shot 的素材顺序自动编号）
- 顺序：先所有图片 `<Picture 1..>`；再每个视频——开了原声的视频，它的原声 `<Audio j>` 排在这个 `<Video k>` 前面；最后是单独的音频。
- `<Video N>` 和 `<Audio N>` 各自编号，编号不同也可能来自同一个视频。
- **开了 depth 的视频没有 `<Video N>` 编号**（见第 6 节），后面的视频编号顺延。
- 素材拖进 shot 只是挂上，标签用提示词下方的标签按钮或输入 @ 插入，保证编号和后端一致。
- **先核对每张 `<Picture N>` 在本 shot 里实际是什么图，再写它的作用**（标签按钮上有缩略图；问不清就问用户）。人物、服装、场景要分别对应到真正包含它的那张图，不要套“`<Picture 1>` 是人物、`<Picture 2>` 是脸”这类固定模板。有场景图时，写明它是整段的拍摄地点、从第一帧到最后一帧不变。
- 人物替换：参考视频提供动作和位置，人换成 `<Subject N>`，用 `attribute_transfer`。多人换人(Remake 引擎)见第 12d 节。
- **动作复刻（TARA）：原片拍成什么样，提示词就写成什么样**（0.4.4，11-09 实测）。提示词要和原片对得上；照"原地跳舞"写的模板（"固定机位、在某场景跳舞、场景不变"）用在镜头会动的原片上，第 2 个窗口就跳回了提示词里的场景。
  - 镜头：照原片。原片固定机位 → `A locked-off shot matching the framing of <Video 1>.`；原片在动（跟拍、摇镜头、跟着跑步的人）→ `One continuous tracking shot that moves exactly like the camera of <Video 1>.`
  - 景物：用原片的，或者写明新场景。
    用原片的（最稳，镜头在动时首选）：`The surroundings are the ones in <Video 1> and pass by exactly as in <Video 1>, in the same order and at the same moments, as one continuous route; nothing in the background jumps, resets or repeats.`，retention 写 `<Video 1>: attribute_transfer - its motion, timing, camera movement and surroundings are kept; only the person is replaced by <Subject 1>.`
    换新场景：写明是什么地方、从头到尾是同一个地方。固定机位没问题；镜头在动、又开了无接缝时，两个窗口交接处可能小跳一下（实测），所以镜头在动时优先用原片的景物。
  - 原片里有剪辑：按原片的时间逐个写 `[9.1s cut] ...`（写清切过去是什么画面），无接缝模式会在那里断开窗口。只有没有剪辑的原片才写"一个连续镜头"。
  - 动作：`<Subject 1> moves exactly like the person in <Video 1>: every step, arm swing, head turn and expression in order and at matching times.`；原片是跳舞才写 "dance"、"on the beat"。

## 5. RefMod（用 H3MD Director + RefMod 时）
- 写 `<RefMod j>`（`RefMod j`、`参考RefMod 1` 也行，不分大小写）。运行时自动替换成它实际的 `<Picture k>` / `<Video k>` / `<Audio k>`。只认 “RefMod” 这个词，写成 `Ref 1` 或直接写 `<Video 2>` 都不会替换。
- j 是它在本 shot RefMod 行里的顺序（可左右拖），不是素材栏上的 “Ref N”。
- 没指定位置时，RefMod 排在本 shot 自己的图片或视频之后；拖到某个图片或视频上就占那个位置，后面顺延。
- 没用到的 RefMod 会被忽略。例：`<Subject 1> is the woman in the navy satin gown from <RefMod 1>, an identity reference. Her facial identity, hairstyle, and gown are preserved throughout.`

## 6. 动作复刻：Depth 和 SAM3
- 一个 shot 最多一个 depth 视频。**勾了 depth 的视频只驱动深度 ControlNet（只作用于低清阶段），不再作为 `<Video N>` 参考送进模型，也不占 `<Video N>` 编号**（自动，没有开关）。原因：黑底白人形的 SAM3 剪影当参考时，模型会把黑底或灰色影棚照搬进画面（实测）。例外：这个视频同时勾了原声当 `<Audio>` 时，仍保留 `<Video N>`（日志警告）。
- 所以提示词里**不要提 `<Video N>`、不要写 attribute_transfer**。改成描述这个镜头本身：`One continuous take: the dancer's whole body stays in view from head to heels and a smooth camera follows her; no cuts, zooms or reframing are added.`
- 地点、地面、光线都要写全，因为背景没有任何参考。加一句：`The <location> stays fully visible around her in every frame, and the background never turns black, grey or plain.`
- 动作由 ControlNet 给，提示词只写动作的性质（`dances the samba with relaxed, rhythmic energy`），不逐拍描述。
- SAM3 提示词（如 `person`）只填在视频的 sam3Prompt 里，**不写进 H3 提示词**。
- Loop 上勾了 depth 却没接 `H3MD Depth Control` 会在开跑前报错。

## 7. 音频
- 音频模式：reference（作为 `<Audio N>` 参考）、locked（硬拷贝成这段的音轨）、none（不传）。每个 shot 最多锁一个。
- 锁了某个**音频文件**：它仍然有 `<Audio N>` 标签，用 `fully_copy` 或 `partially_copy`，并在对应镜头写出它在哪里响起。同一首歌跨好几个硬切 shot 时，每个 shot 的 offset = 这个 shot 在成片里的起始秒数（自动拆段会自己顺延，不用管）。
- 锁了某个**视频的原声**：这个原声不再生成 `<Audio>` 标签（锁和原声参考互斥）。提示词里用普通文字描述声音，内容要和锁定音轨一致（唱的歌词照抄进 `<d>`，口型对得上）。
- 不知道歌词、或不想对口型时，写明人物不唱：`she does not sing; her lips stay softly closed`。不要编歌词。
- 只参考音色时，不把原音频里的台词搬进来。

## 8. 全局提示词
- `globalPrompt` 会加在**每个** shot 提示词前面（续接 shot 还会再在最前面加 Continuity 句）。它会落在 `subject_definitions:` 上面，所以建议留空，或只写 LoRA 触发词、一句不带标签的风格说明。风格写进每个 shot 的 `detailed_description` 开头更稳。

## 9. 参考照片（实测会被照搬的东西）
- **背景**：纯色、灰色影棚背景的照片会把视频拉进灰色影棚（实测，和深度视频无关也会发生）。用有真实场景的照片；没有的话，把人物抠出来，放到目标场景的模糊版背景上（保持原照片像素尺寸）。
- **地板**：全身照尽量不露地板，地面只在文字里写一种。
- **表情**：照片里是露齿大笑，视频里就会一直大笑、笑容僵住。要么换表情柔和的照片，要么在提示词里明确“身份照照片、表情不照照片”：`Her face keeps the exact facial identity of <Picture 2> from every angle, but with a soft, relaxed expression instead of the photo's wide smile`，并在人物定义里写 `a gentle, relaxed expression`、动作里写 `only a soft closed-lip smile now and then; she never laughs or grins widely`。
- **颜色**：参考照片偏浓、偏锐，长视频会越往后越浓。照片宁可淡一点。
- 重跑某几段时要换参考图：新图保持和原图一样的像素尺寸，分镜总长不变，否则自动拆段会变、缓存对不上。

## 10. 真人长视频的稳定性
- 少转身、少旋转，动作幅度小而慢，跟着音乐或演唱走；转身多了一分钟左右身份就会漂。
- 放在有景的地方，不要空棚；场景简单、明亮、一种地面。
- 每个 shot 都挂她的照片（图片或 RefMod）定义 `<Subject 1>`，retention 写 `fully_preserved`；每个 shot 都重新完整描述主体外观，不依赖上一段。
- 一镜到底的单场景长视频（动作复刻、长镜头）：Loop Options 的 `lowres_anchor` 设 1（需要 `continue_lowres_prefix = native_lowres_carry`），每段低清结果拉回本镜头第一段的颜色统计，颜色不会一段段变深、人不会越来越显老。场景本该变色的叙事片不要开；硬切后自动以新镜头第一段为准。
- 1080p 直出的中近景（脸约占画面高度 1/5）已经够清楚，不用修脸。远景小脸修脸也救不回来，靠景别解决（见 10b）。

## 10b. 唱歌、景别和机位（160 秒 MV 实测）
- **只在中景和近景里唱。** 远景里脸太小看不清嘴，H3 会自己插一个正面唱歌的特写：要么硬切，要么半透明叠化（一张大脸浮在画面上）。写了"一镜到底、不切"也拦不住。远景写 `She does not sing in this shot; her lips stay softly closed`，歌声当画外音。
- **脸的大小决定清晰度。** 1920×1088 横屏的膝盖以上中景，脸只有约 110 像素，会糊、有斑点，修脸也救不回来。腰部以上或更近，或者用 1088×1920 竖屏，脸有 200 像素以上就清楚。脸会很小的镜头，改成背影、远处侧影或细节（头发、手、裙摆）。
- **要看清脸的镜头，按"脸占画面高度约 1/5"来设计（11-13 实测）。** 同一个镜头、同一个种子、同样的参考图，只把景别从 Medium-wide 改成 Medium close-up：脸从约 100 像素（不到画面高度 1/10）变成约 260 像素（约 1/5），不开修脸就清楚，出片时间一样。小脸上试过的后期办法都不够：标准修脸、Plus 拼接、放大重画、换修复模型（VOSR2 / SeedVR2），只是从"很糊"到"勉强能看"，眼睛重影、发丝发虚还在，还多花时间。所以：
  - 唱歌、说话、表情戏：中近景或更近，写明构图，例如 `Medium close-up locked-off shot, framed from the chest up: the head and shoulders of <Subject 1> fill the frame and her face takes up about one fifth of the frame height.`
  - 远景只用在不需要看脸的地方（交代环境、背影、剪影），时间短，夹在中近景之间，或者用推拉把远景接到中近景。
  - 整条 MV 以中近景为主；做计划时先排景别，再写内容。
- **参考图配一张脸部大特写 + 一张带服装的半身照，并把每张图的作用写对。** 例如 `<Subject 1> is the woman shown in <Picture 2> and <Picture 3>. <Picture 2> is her identity and outfit reference. <Picture 3> is a close-up portrait of the face of <Subject 1>.` 不要把人物写成在场景图里（原 Water 计划就写错了）。只换更好的参考图、景别不变，清晰度只提高约 17%，不解决问题；参考图越多越慢（15 秒一段约 9.5 分钟 → 14–15 分钟），够用就行。
- **脸部特写照片只给中景和近景。** 挂在远景里，模型可能把这张照片当成一个镜头照搬（构图和背景都搬）。
- **带时间的歌词，一个镜头只能是一段。** 写 `At 00:07.3 she sings: <d>[English] ...</d>`，用本镜头自己的时间；唱歌的镜头控制在约 500 帧（一段）以内，或者拆成几个镜头各写提示词。自动拆段会让每一段都从 0 秒套用歌词时间。
- **近处有大片地面时，机位固定。** 镜头在大片草地上移动，草地会像波浪一样起伏。写 `The camera is locked off on a tripod and does not move at all; the ground stays perfectly still and only she, her gown and her hair move.` 运镜留给天空、云和远景。
- **动作不要写 slowly。** slowly、very slowly、unhurried、slow waves 会变成慢动作。写 `at a natural, relaxed pace in real time`，再加一句 `All motion plays at natural real-time speed, not slow motion.` 舒缓交给音乐和构图。
- **参考图的横竖要和成片一致。** 横的场景图放进竖屏，会变成一条带黑边的横条。竖屏要另做一张竖版（上面补天空、下面补云海）。
- **图库水印的"鬼影"。** 大片雪坡上偶尔会出现淡黄色、带方块纹理的水印斑，和 seed 有关：换 seed 重跑那一段。

## 10c. 纯音乐段、"不要什么"、参考图（3 分钟一镜到底实测）
- **前奏、间奏、尾奏单独成一段，这一段的提示词里完全不提唱歌。** 只在唱歌段里写"0–12.7 秒不唱、嘴闭着"压不住：模型看到"她是歌手 + 有音乐"就会跟着音乐动嘴。拆成单独一段后，前奏 12 秒、尾奏 14 秒嘴全程闭着。纯音乐段这样写：
  - `<Audio 1> is an instrumental passage of the song's music, with no singing voice; <Subject 1> only listens to it and does not sing.`
  - summary：`... listening silently to <Audio 1> ...`；retention：`it is instrumental here, so her lips stay closed.`
  - non_diegetic_music 写乐器和 `no singing voice`；overall_soundscape 不写她的歌声。
  - 动作给一个具体的事：闭嘴微笑、慢慢呼吸、闭眼再睁开、看远处。
- **唱歌段第一句前还有几秒纯音乐**：写 `From 00:00.0 to 00:02.1 <Audio 1> is purely instrumental, with no singing voice at all, so she is only listening ...`，配合上面的拆段用。
- **"不要什么"不起作用。** H3 是去掉 CFG 蒸馏过的，负面提示词无效；正面提示词里写 `no shine, no oil, no gloss` 也一样。干脆不写这件事（见下面"有参考图时"那条）。
- **参考图不要做局部修图**（去油、局部调色）。修过的图把黄边和背景橙色偏色带进了整条视频，原图没有。参考图有问题就重新出图。
- **画面出现黄斑 / 偏色：先查参考图，再怀疑种子。**
- **种子：对比测试才固定种子（只改一个变量），正式出片用随机种子。**
- **有参考图时，不写人物长相、灯光、色彩，交给图片。** 同一张参考图、同一个种子实测：提示词里写了脸、"素颜……额头鼻子脸颊看得见毛孔"、"true-to-life skin texture"、"music-video footage"、"光落在她脸上和手上"，结果满脸像出汗、手像抹了油，而参考图本身是哑光的；同一段改成 `<Subject 1> is the woman shown in <Picture 1>.`、地点写"`<Picture 1>` 里的房间"、不写任何光线色彩风格词，出来和参考图一样哑光。只写图里看不出来的：机位、景别、动作、时间、声音。写皮肤、质感，模型就会去"刻画"皮肤，油光就是这么来的。（缎面裙子会反光，那是面料本身。）
- **油光也和 LoRA 组合有关。** 同一张图、同一个种子，换成 FL2V turbo 4 步 v1.2 768p、Enhancer 0.55（低清）、realism-people 0.6（高清）、电影感两边都开，油光去掉大半；再配合上面的极简提示词，剩下的也没了。长片开跑前先用第 1 段试 LoRA 组合。

## 11. 跑之前的工作习惯
- 先只跑第 1 段（Loop 的 `stop_after = 1`），满意后改回 0，第 1 段从缓存取。
- 某几段坏了不用整片重跑：`rerun_segments` 填 `8-10, 13-14`（从 1 数），开头接上一段、结尾锁到下一段旧开头，其余从缓存取（`resume_from_cache` 要开）。

## 12. 输出格式
每个 shot 单独一块，按顺序输出：
1. 块上方一行标注（**不粘进提示词框**）：`Shot N — 长度 f / 秒 s · overlapIn X（新内容 Y s）· 硬切/续接 · 素材与标签对应：<Picture 1>=… · 锁定音频 offset …`
2. 下面是可直接粘贴的完整提示词（六段式或 base 格式）。
最后给一行合计：shot 数、生成总帧数、成片新内容总时长。

## 12b. 可导入的计划文件（H3MD v0.2.0 起）
提示词之后，再把整个计划写成**一个** JSON 代码块，用户存成 `<名字>.json`，在导演台点 **Import plan…** 一键导入，不用一段段复制粘贴。格式见仓库 `docs/plan_format.md`：

```json
{
  "version": 1, "fps": 24, "width": 704, "height": 1280, "globalPrompt": "",
  "media": {"images": [{"id": "eve", "file": "eve.png"}], "videos": [],
            "audios": [{"id": "song", "file": "song.mp3"}]},
  "segments": [
    {"length": 294, "overlapIn": 0, "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 0.0}], "prompt": "Duration: 12.25 s (294 frames).\n\nsubject_definitions: ..."},
    {"length": 362, "overlapIn": 39, "images": ["eve"],
     "audios": [{"id": "song", "mode": "locked", "offset": 10.625}], "prompt": "Duration: 15.08 s (362 frames).\n\n..."}
  ]
}
```
- `file` 是用户在 ComfyUI `input/` 文件夹里（或将要上传）的文件名；`id` 是分镜里用的短名字。
- `length` 是 5+17n；`overlapIn` 第一段和硬切写 0，续接写 39。
- 锁定的歌：`offset` = 上一段 offset +（上一段长度 − 本段 overlapIn）/ 24。
- `prompt` 是这一段完整的提示词（换行写成 `\n`），和要粘贴的内容一字不差。
- 必须是合法 JSON（双引号、不加注释、末尾不留逗号）。导演台发现问题（长度、重叠、标签没有对应素材、缺文件）会逐条列出来，不会替你改。

## 12c. 带图片和歌的计划 zip（能写文件时优先用这个）
单个 `.json` 只导入计划本身：图片、歌还得用户一个个上传，文件名还要对得上。只要你能生成文件（用户给了图片 / 歌，或者是你做的），就交**一个 `.zip`**：导演台 **Import plan…** 会把素材解压到 `input/H3MD_plans/<名字>/`，并自动把计划里的路径指过去，一键可用。

```
<名字>.zip
├─ manifest.json   {"format": "h3md-plan", "plan_version": 1, "name": "<名字>", "width": W, "height": H, "fps": 24,
│                   "with_media": true,
│                   "media": [{"kind": "images", "id": "eve", "file": "eve.png", "included": true, "zip": "media/eve.png"},
│                             {"kind": "audios", "id": "song", "file": "song.mp3", "included": true, "zip": "media/song.mp3"}]}
├─ plan.json       12b 的计划（id 一致；"file" 写纯文件名，如 "eve.png"）
└─ media/          eve.png、song.mp3……（平铺，不建子文件夹）
```
- `plan.json` 里每张图、每个视频、每首歌在 manifest 的 `media` 里各写一条；manifest 的 `file` = `plan.json` 的 `file`；`zip` = `media/` + 它在 zip 里的文件名。文件名不重复、不建子文件夹、不以点开头。
- 素材格式只认：png jpg jpeg webp bmp gif tif · mp4 mov webm mkv avi m4v · wav mp3 flac ogg m4a aac opus。zip 里别的文件会被忽略，并列为问题。
- **不要**放 `workflow.json` / `settings.json`：这样导入时是「Plan only」，只换计划，用户自己工作流里的 LoRA、强度、种子都不动。带工作流的包只由 H3MD 的 **Export plan** 导出。
- 打包前自查：长度 5+17n、重叠对、每个 `<Picture N>` / `<Audio N>` 都有对应素材、每个素材文件确实在 `media/` 里。导入后导演台会把问题逐条列出来。
- Python（只用标准库）：
  ```python
  import json, os, zipfile
  def make_plan_zip(plan, media_paths, out_zip, name):      # media_paths: {"eve.png": "/path/to/eve.png", ...}
      kinds = {"images": plan["media"].get("images", []), "videos": plan["media"].get("videos", []),
               "audios": plan["media"].get("audios", [])}
      man = {"format": "h3md-plan", "plan_version": 1, "name": name, "width": plan["width"],
             "height": plan["height"], "fps": plan.get("fps", 24), "with_media": True, "media": []}
      with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
          for kind, items in kinds.items():
              for it in items:
                  base = os.path.basename(it["file"])
                  z.write(media_paths[base], "media/" + base)
                  man["media"].append({"kind": kind, "id": it["id"], "file": it["file"],
                                       "included": True, "zip": "media/" + base})
          z.writestr("plan.json", json.dumps(plan, ensure_ascii=False, indent=2))
          z.writestr("manifest.json", json.dumps(man, ensure_ascii=False, indent=2))
  ```
- 没法生成文件（只能聊天）？就给 12b 的 `.json` 代码块，并列出用户要按原文件名上传到 `input/` 的文件。
- 完整包的样例（H3MD 自己导出、带工作流）：huggingface.co/yj0942/H3MD-Director 的 `plans/H3MD_demo_eve_mv.zip`。

## 12d. 多人换人(Remake):智能体画关键帧,导演台出视频

把真人视频里的几个人换成用户的角色时用这一节(H3MD 0.5.0 起,`swapEngine: "remake"`)。视频由导演台生成;**你负责
关键帧、提示词,最后交计划包(或图片清单)**。Remake 会照着关键帧拍:关键帧里人挪了位、变了大小、换错了人,视频就跟着错。
所以每张关键帧都要过第 5 步的自查,不合格就重画,不能凑合交。不要用 TARA、depth 或 H3 遮罩重画来做这件事。

**本地用户用关键帧工作流(0.5.91)**:`example_workflows/H3MD_Keyframe_MultiSwap_0.5.91.json`,一个工作流两组:
1. **Keyframe Export**:H3MD Keyframe Prepare(编号、找剪辑点、挑时刻,每个镜头至少一张关键帧)+ H3MD Keyframe Export,
   写出 `<名字>_keyframe_export_<日期>_<序号>.zip`。
2. 智能体按 **H3MD 关键帧 skill(`skills/h3md-keyframe`)** 读这个包,回一个 `.h3kf`(或 `.json`),每张关键帧一段
   MiniMax H3 Image 提示词。
3. **Keyframe Import**:Import(按钮导入)→ Recast(H3 Image,每张约 25 秒)→ Compare(原图 | 新图)。哪张不对用 Redo
   节点单张重做。Assemble 写出 `<名字>_multiswap_plan_<日期>_<序号>.zip`,交给导演台。

用户每个角色写一行从头到脚的描述:人种、年龄、长相、发型、每件衣服,**还有鞋,以及参考图上看不到的部分**(半身图要写
裤子和鞋)。如果是你来写这些关键帧提示词,照 `skills/h3md-keyframe` 做。下面两条路线留给智能体自己画关键帧。

**两条路线**:按你自己的能力选一条,其余步骤两条线共用。
- **A 线:你能运行代码**(Claude Code、Codex、Cursor 之类,或接着本地 ComfyUI):裁图 → 修图 → 贴回 → 对色。构图在原理上
  不会变,远景小人、人多的画面都能做。**能走 A 线就走 A 线。**
- **B 线:你只能聊天出图**:推荐用 **ChatGPT**(它的出图模型就是 Remake 原作者用的 gpt-image)。实测 Grok 会把整张图重画、换错人、角色也不像,不能用。整张图交给修图模型改。修图模型整张重画时常常会把人放大、挪位、
  多画或漏画一个人,所以 B 线对选帧和自查要求更严(见第 2、4、5 步)。原片的帧由用户截好给你(或用户先请 A 线智能体截)。

**真人素材**:在线修图服务(Grok、GPT-image、豆包等)通常拒绝修改真人照片,名人几乎一定被拒。素材要用你有权使用的视频;真人素材只能走 A 线 + 本地修图模型。不要用这个功能冒充真实的人。

**修图模型**:本地用 MiniMax H3 Image(关键帧工作流)或其他修图模型(A 线);只能聊天出图就用 ChatGPT(B 线)。要求是:能同时看两张以上
的图(原帧 + 角色图),输出尺寸可以指定或保持原图大小。

### 1. 认人:按出场先后编号,编号一直不变(0.5.91)
- 按**出场先后**编号:第一个清楚出现(至少 1 秒)的人是 1 号,下一个是 2 号……同时出场的从左往右。一闪而过的、只在画面边上
  露一小条的不编号。默认图1 换 1 号、图2 换 2 号……用户也可以写"图1换3号"。
- 这个编号就是这个人在整条视频里的身份:后面换了位置、出画再回来都不变;图用完以后出场的人保持原样。
- 给每个人记一句外貌(帽子、发型、衣服颜色和款式),后面每一帧都靠它认人。两个人长得很像、或分不清谁是谁时,停下来问用户。
- 角色图:每个角色一张清楚的全身或半身图;记下角色的外貌(发型、上衣、裤子、鞋、袜子 / 光腿),原角色穿了而新角色没有的写明
  "no socks, no boots"。

### 2. 选关键帧的时间点
- 先看完整条视频,列出剪辑点(场景检测 + 自己看),以及每个镜头里各编号的人在哪、谁进场出场、转身、换位置。
- **每个被换的人至少要有一张大而清楚的关键帧**（中景，脸清楚，正面或侧前方）。只在后排、被挡住或很小的人，模型拿不到他的脸，成片会往原演员那边漂（实测：David 只有小图时，唱歌的镜头里变成了欧洲人的脸）。
- **每个镜头一张**;人物换位、进场、背身转正面时**加一张**;相邻两张**不超过约 6 秒**。
- 自动分段后每段(最多 345 帧,约 14 秒)最多用自己的 **4 张**,所以一段 14 秒里 2–3 张就够。
- 挑**要换的人看得清**的一帧:不糊、不被画面边缘切掉一大半、不被别人挡住大半。
- **近景、特写不画**:画面里只看得到这个人大腿以上、而且他占了画面的大半时,裁出来几乎是整张图,修图模型会把人缩小或挪位(全身入画的人再高都没关系)。改在特写之前的**中景**画一张
  (人约占画面高度 1/4 到 1/2):实测中景关键帧能把身份带到同一段后面的特写。
- **B 线额外规则**:要换的人在画面里太小(不到画面高度约 1/5)时,整张修图很容易多画、漏画或放大人。这种远景要么不画、
  由后面的中景关键帧带过去,要么请用户换 A 线的智能体来画这一张。
- 每张记下:原片时间(秒)、帧号(= 时间 × 24)、这一帧里每个人的编号和位置、要换谁。

### 3. 取原帧,并裁成成片的比例
- 用**原片那一帧本身**(不要用缩略图、截屏)。
- 先把这一帧裁成成片的比例,再缩放到成片尺寸。成片尺寸:竖屏 **960×1664**,横屏 **1664×960**(或 2560×1472)。
  做法是**居中裁切**(和 Remake 处理原片的方法一样),例如 1080×1920 的竖屏原片:上下各裁 24 像素,得到 1080×1872,
  再缩放到 960×1664。关键帧比例不对的话,Remake 会把第一张拉伸变形。
- B 线:用户把裁好的帧给你;你交回来的图必须是同样的尺寸。

### 4. 画关键帧
**A 线(能运行代码):一次只换一个人,只改他那一块。**
1. 框出要换的人(全身框),向外扩:左右各加框宽的 45%,上加框高的 12%,下加 8%;最窄 1:2;超出画面的部分截掉。
2. 把这块裁下来,放大到约 1.6 百万像素(长宽取 32 的倍数),和角色图一起交给修图模型,提示词见下面的模板。
3. 修好的图缩回原来这块的大小;**对色**:取这块里人物框以外的一圈(背景部分),算修图前后每个颜色通道的均值和标准差,把修好
   的图整体调到和原图一致;再贴回原位,四周(碰到画面边缘的那一边除外)羽化约 8% 的宽度。
4. 一帧里要换几个人,就按这个顺序一个一个做;每做完一个,下一个从贴好的图上裁(不要全部从原帧裁,否则旁边的原角色会被贴回去)。
5. 块以外的像素和原帧一模一样,构图不可能变。

**B 线(只能聊天出图):整张图修,一张一张来。**
1. 每次只发一张原帧 + 需要的角色图,要求输出和原帧**同样尺寸**。
2. 一帧里要换的人多、或者第一次出图有人被改错时,改成**一次只换一个人**:换好一个,拿这张结果再换下一个。
3. 每张出图后马上按第 5 步自查;不合格的重新生成(可以换种子或把提示词里的位置描述写得更具体),不要拿去用。

**给修图模型的提示词模板**(中英文都可以;图1 是原帧或裁下的那块,图2 起是角色图):
> 以图1为底图,保持图1的构图、机位、背景、光线、颜色和清晰度,以及所有人的位置、大小、姿势、动作、朝向和遮挡关系完全不变。
> 只把图1中【位置 + 外貌,例如:从左数第二个、戴白色棒球帽穿白色运动服的男子】换成图2中的【角色外貌,例如:女子(金色长卷发,
> 白色罗纹背心,黑色长牛仔裤,白色运动鞋)】,站姿和动作沿用原来那个人,被挡住的部分仍然被挡住。其他人和背景完全不变。
> 不要美化,不要调色,不加滤镜,是和图1一样的写实照片。

A 线裁图时把第一句换成"图1是一张照片的局部,把图1中间的【……】换成……",其余一样。

### 5. 自查(每张都要做,两条线一样)
把关键帧和原帧并排,或叠在一起来回切换着看:
- [ ] 尺寸和成片一致(960×1664 或 1664×960)。
- [ ] 人数一样:没有多画一个人,也没有少一个人。
- [ ] 每个人的位置和大小都和原帧一样(头顶、脚底、左右边缘对得上);背景、地面、天空、建筑没有挪动。
- [ ] 衣服完全是角色的：原演员的外套、夹克、帽子一件都不能留（实测：关键帧里留着原演员的夹克，成片也一直穿着）。
- [ ] 换对了人:左几换成了哪个角色,和用户的要求一致;角色的脸、发型、衣服和角色图一致。
- [ ] 不换的人完全没变:脸、帽子、眼镜、衣服都和原帧一样(常见错误:把角色的帽子或衣服画到旁边的人身上)。
- [ ] 手和脸完整,没有多手指、没有拼接痕迹;颜色和亮度和原帧一致(没有一块明显偏亮或偏色)。
有一项不合格就重画这一张(A 线只重画出错的那一块)。

### 6. 写提示词(整条视频一份,六段式,Remake 写法)
Remake 里 `<Picture N>` 是本段第 N 张关键帧,不是角色图,所以**不要把 Subject 绑到某个 Picture 编号上**。按外貌和所替换的演员
定义每个 Subject,并说明这些图是本次成片的重画帧:
- `subject_definitions`:`<Video 1> is the source video with 5 performers: it supplies the motion, gestures, head and
  lip movement, poses, positions, place changes, timing, the camera, the surroundings, the lighting and the
  soundtrack.` / `The <Picture> images are redrawn frames of this same run: each shows the people at that moment with
  their final look, in the positions and poses they hold then; together they fix who is who as the performers change
  places, leave and come back, and in close-ups.` / `<Subject 1> is a woman with long wavy blonde hair in a white
  ribbed tank top, black jeans and white sneakers; this subject replaces performer #2, who first appears at 0.0 s on the left of the frame.`(每个角色一行)。**每个 Subject 都要写人种、大概年龄和长相**（脸型、眼睛、肤色、有没有胡子），不能只写发型和衣服，例如 `<Subject 3> is an East Asian man in his late twenties with a clean-shaven face, dark almond-shaped eyes and short black hair ...`（实测：只写"黑色短发、黑 T 恤"时，东亚角色被画成了欧洲人）
- `summary`:`The target video is an edited version of <Video 1>. [video editing + reference generation + audio reuse]
  Recast 3 of the 5 performers with a fixed mapping the whole time, numbered in order of first appearance: ...; the
  other performers stay themselves, with their own faces, hair and outfits.`
- `retention_analysis`:`<Video 1>: partially_preserved - motion, posture, lip and jaw movement, timing, camera, framing,
  scene, lighting and colour grade are retained; the face, hair and outfit of the recast performers are not carried
  over.`;每个 Subject 一行 `fully_preserved - ..., identical in every moment, including close-ups and group shots.`;
  `Source audio: fully_copy - the original soundtrack and its synchronisation are kept.`
- `detailed_description`:与 `<Video 1>` 一致的一个连续镜头(原片有剪辑就按原片时间写 `[Ns cut]`),开头从左到右谁是谁,
  每个被换的人动作和原演员完全一样,每一帧都保持自己的脸、发型和衣服;手有五根手指。
- `overall_soundscape`:`Keep the source soundtrack; do not invent dialogue.`;`non_diegetic_music`:`<Video 1>` 的音乐不变。

### 7. 交付
- **A 线:打计划包 zip**(格式见 12c),原片和关键帧都放进 `media/`:
  `"swapEngine": "remake"`、`"autoSplit": true`、成片尺寸;一个 segment:
  `{"length": 5+17n(不超过原片结尾), "overlapIn": 0, "videos": [{"id": "src", "trimStart": 开始秒数,
  "useSoundtrack": false, "depth": false}], "audioMode": "locked", "locked": {"source": "video:src", "offset": 0},
  "keyframes": [{"image": "kf1", "at": 秒}, ...], "prompt": "..."}`。
  关键帧图只列在 `media.images`,不放进 segment 的 `images`;`at` 是从本段开头算的秒数(= 原片时间 − trimStart);
  角色图不用放(Remake 不读)。
- **B 线:交一份清单**:每张关键帧的文件名、原片时间(秒)、换了谁,再加第 6 步的提示词。用户在导演台打开「Remake 换人」,
  把图片加进关键帧列表、填时间,提示词贴进镜头里;或者把清单交给 A 线智能体打包。
- 请用户在示例工作流 `H3MD Director Remake.json` 里先只跑第一段低清,看过再跑全片。

## 13. 输出前自查
- [ ] 确实是 H3MD 任务（不是就改用 `h3-prompt-writing`）。
- [ ] 格式和段落顺序对；字段名一字不差。
- [ ] 时间从 0 秒算，最后一个镜头在 shot 长度以内；续接 shot 的新动作在 overlap 之后开始。
- [ ] 长度都是 5+17n；总时长按“长度 − overlap”累加；动作复刻的分镜总长不超过参考视频。
- [ ] 会被自动拆段的长 shot：写的是持续状态，没有按时间先后的事件和子镜头。
- [ ] 照片里没有的服装写全了，每个镜头逐字相同（面料不透明）。
- [ ] 动作复刻：镜头、景物、剪辑都和原片对得上（固定 / 跟拍；用原片景物或写明新场景；原片的剪辑按原片时间写 `[Ns cut]`）。
- [ ] 窗口模式：长镜头里的动作、歌词写了从镜头开头算的时间（`At 00:07.9 ...` 或 `[8s] ...`），隔约 8 秒；对白 / 歌词在 `<d>[English] ...</d>` 里；summary 只写不变的东西。

- [ ] 标签编号和本 shot 素材顺序一致；depth 视频没有 `<Video N>`；RefMod 写成 `<RefMod j>`；没有未定义的标签。
- [ ] 每张 `<Picture N>` 写的作用（人物 / 服装 / 场景）和它实际的图片内容一致；照片没有纯色 / 灰色背景，或已换成场景模糊背景。
- [ ] 表情写明了（不照搬照片的大笑）；没有打光形容词堆砌；光线写成固定不变。
- [ ] 场景简单：没有镜子、木地板、地砖、条纹；地面只有一种纯色材质；写了“背景不变黑、不变灰”。
- [ ] 每个镜头都写了构图、人物位置、环境光线、动作、运镜和声音；对白歌词在 `<d>` 里，保留原语言；不知道歌词就写不唱。
- [ ] 只在中景、近景唱歌；远景写明不唱；脸部特写照片只给中景和近景。
- [ ] 要看清脸的镜头都是中近景或更近（脸约占画面高度 1/5，1080p 约 200 像素以上），并写明了构图；远景短、不靠它看脸；没有指望修脸来救小脸。
- [ ] 近处有大片地面时机位固定；没有 slowly 类词；参考图横竖和成片一致。
- [ ] 有参考图时：没有写长相、皮肤、妆容、灯光、色彩、风格，只写机位、动作、时间和声音。
- [ ] 前奏 / 间奏 / 尾奏是单独的段，提示词里没有任何"唱"的意思；没有"no X"这类负面写法；参考图是原图，没有做过局部修图。
- [ ] 用户要导入时：给了一个合法的计划 JSON，长度、重叠、offset 和提示词与上面各段一致。
- [ ] 计划 zip：`manifest.json` + `plan.json` + 平铺的 `media/`，计划用到的图和歌都在里面，没有 `workflow.json`。
