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

### 窗口模式（0.3.9 测试版：导演台的“无接缝长视频”开关）
- 计划照常写：多个镜头、续接镜头（`overlapIn` 22/39）、硬切（`overlapIn` 0）。打开开关后（计划里是
  `"windowMode": {"length": 243, "overlap": 56, "highLength": 175, "highOverlap": 22}`），续接的镜头会被接成一条时间线、
  一次采样出来，没有接缝；硬切保持干净的硬切。一组最长约 60 秒（内存小于 40 GB 时 30 秒），在放得下的最后一个硬切处分组；
  一镜到底超过这个长度时，系统会提示在哪里加一个硬切。
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
- 人物替换：参考视频提供动作和位置，人换成 `<Subject N>`，用 `attribute_transfer`。

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
- 1080p 直出的中景、半身脸已经够清楚，一般不用修脸；远景小脸再考虑。

## 10b. 唱歌、景别和机位（160 秒 MV 实测）
- **只在中景和近景里唱。** 远景里脸太小看不清嘴，H3 会自己插一个正面唱歌的特写：要么硬切，要么半透明叠化（一张大脸浮在画面上）。写了"一镜到底、不切"也拦不住。远景写 `She does not sing in this shot; her lips stay softly closed`，歌声当画外音。
- **脸的大小决定清晰度。** 1920×1088 横屏的膝盖以上中景，脸只有约 110 像素，会糊、有斑点，修脸也救不回来。腰部以上或更近，或者用 1088×1920 竖屏，脸有 200 像素以上就清楚。脸会很小的镜头，改成背影、远处侧影或细节（头发、手、裙摆）。
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

## 13. 输出前自查
- [ ] 确实是 H3MD 任务（不是就改用 `h3-prompt-writing`）。
- [ ] 格式和段落顺序对；字段名一字不差。
- [ ] 时间从 0 秒算，最后一个镜头在 shot 长度以内；续接 shot 的新动作在 overlap 之后开始。
- [ ] 长度都是 5+17n；总时长按“长度 − overlap”累加；动作复刻的分镜总长不超过参考视频。
- [ ] 会被自动拆段的长 shot：写的是持续状态，没有按时间先后的事件和子镜头。
- [ ] 照片里没有的服装写全了，每个镜头逐字相同（面料不透明）。
- [ ] 窗口模式：长镜头里的动作、歌词写了从镜头开头算的时间（`At 00:07.9 ...` 或 `[8s] ...`），隔约 8 秒；对白 / 歌词在 `<d>[English] ...</d>` 里；summary 只写不变的东西。

- [ ] 标签编号和本 shot 素材顺序一致；depth 视频没有 `<Video N>`；RefMod 写成 `<RefMod j>`；没有未定义的标签。
- [ ] 每张 `<Picture N>` 写的作用（人物 / 服装 / 场景）和它实际的图片内容一致；照片没有纯色 / 灰色背景，或已换成场景模糊背景。
- [ ] 表情写明了（不照搬照片的大笑）；没有打光形容词堆砌；光线写成固定不变。
- [ ] 场景简单：没有镜子、木地板、地砖、条纹；地面只有一种纯色材质；写了“背景不变黑、不变灰”。
- [ ] 每个镜头都写了构图、人物位置、环境光线、动作、运镜和声音；对白歌词在 `<d>` 里，保留原语言；不知道歌词就写不唱。
- [ ] 只在中景、近景唱歌；远景写明不唱；脸部特写照片只给中景和近景。
- [ ] 近处有大片地面时机位固定；没有 slowly 类词；参考图横竖和成片一致。
- [ ] 有参考图时：没有写长相、皮肤、妆容、灯光、色彩、风格，只写机位、动作、时间和声音。
- [ ] 前奏 / 间奏 / 尾奏是单独的段，提示词里没有任何"唱"的意思；没有"no X"这类负面写法；参考图是原图，没有做过局部修图。
- [ ] 用户要导入时：给了一个合法的计划 JSON，长度、重叠、offset 和提示词与上面各段一致。
