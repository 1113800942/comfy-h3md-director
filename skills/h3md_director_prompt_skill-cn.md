# H3MD Director 分镜提示词规则

H3MD Director 的每个分镜都是一次独立的 H3 生成。所以每个分镜的提示词，就是一份完整的 H3 提示词，外加下面这些 H3MD 自己的规则。

## 1. 先选格式（沿用 H3 skill）
- 分镜里有参考素材（图片、视频、音频、RefMod）时，用 **full-reference 六段式**，按 `minimax-h3-prompt-skill` 的 `references/ref-en.txt` 写，顺序是：`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。
- 完全没有参考素材时，用 base 模式（T2VA / I2VA 等），按 `references/base-en.txt` 写。
- 正文用英文。对白和歌词放在 `<d>[语言] ...</d>` 里，保留原文。
- `summary` 开头要加任务类型前缀，比如 `[reference generation]`。`retention_analysis` 用固定标记：fully_preserved、partially_preserved、attribute_transfer、weak_reference；音频用 fully_copy、partially_copy、reference、weak_reference。
- `subject_definitions` 里只写描述，不写时间、不写镜头。

## 2. 时间轴：每个分镜从 0 秒算
- `[Shot 1]` 不写时间。后面的镜头写成 `[Shot N] At MM:SS.mmm, ...`。
- 所有时间都按**本分镜自己的时间**算，不按原片时间。最后一个镜头的时间要小于分镜时长。
- 分镜长度是 24fps 下的 **5+17n 帧**，比如 362 帧约 15.1 秒、396 帧约 16.5 秒。长度跟着第一个参考视频走时，会向上取整。
- 没有长度上限，也不再自动分段。但**每个参考视频最多只取 15 秒**，所以一个分镜建议不超过约 15 秒；超过 15 秒的部分没有动作参考。

## 3. 参考标签的编号
- `<Picture N>`、`<Video N>`、`<Audio N>` 按素材**在本分镜里的顺序**自动编号：先图片，再视频（勾了原声的视频，它的音轨 `<Audio>` 排在这个视频前面），最后是单独的音频。
- 把素材拖进分镜只是挂上素材，标签要用提示词下方的标签按钮插入，或者输入 @ 插入。
- **写每张 `<Picture N>` 的作用之前，先看清它在本分镜里到底是什么图**（提示词下方的标签按钮会显示每张图片的缩略图）。复用或通用的提示词写着“`<Picture 1>` 是人物”，而本分镜第一张图其实是空场景时，人物和服装就没有来源，视频可能中途跳到别的背景或服装。人物、服装、场景要分别对应到真正包含它的那张图，并写明场景图是整段的拍摄地点。
- 一张图片如果只用来定义角色、场景或服装，就写进 `<Subject N>` 的定义里，比如 “`<Subject 1>` is the man in `<Picture 1>` ...”，不单独占一行。
- 一个参考视频只提供动作、运镜、剪辑节奏和场景时，就写成 `<Video N>` 的结构来源。人物替换用 `attribute_transfer`，意思是把原片里那个人的动作和位置换到 `<Subject N>` 身上。
- **勾了 depth（动作复刻）的视频没有 `<Video N>` 标签**：它只驱动深度 ControlNet，不送进模型当参考，所以提示词里不要写它，也不要写 attribute_transfer。动作写成"一镜到底、全身入镜、镜头平稳跟随"即可；场景、地板、灯光全靠文字和图片写清楚，并加一句"背景每一帧都清楚可见，不变黑、不变灰、不变成素色"。（勾了原声的深度视频例外，仍是 `<Video N>`。）
- **参考照片不要用纯色或灰色影棚背景**：灰底照片会把画面拉成灰色影棚。用带真实场景的照片，或把人物抠出来放在模糊的目标场景上。全身照最好不露地板，地板材质只在文字里写一种（纯色、无纹理的最稳，比如纯色大理石），避免木地板拼法前后不一致。

## 4. RefMod 的写法
- 提示词里写 **`<RefMod j>`**，也可以写 `RefMod j` 或 `参考RefMod 1`，大小写都行。运行时会自动换成它实际的 `<Picture k>`、`<Video k>` 或 `<Audio k>`，存在方案里的原文不会改。
- 只认 “RefMod” 这个词。写成 `REF model 1`、`Ref 1`、`<Video 2>` 这类，都不会被替换。
- j 是这个 RefMod 在**本分镜 RefMod 行里的顺序**，左右拖动可以调整，不是素材栏图标上的 “Ref N”。
- 把图片或视频类 RefMod 拖到某张图片或某个视频上，它就排在那个位置，后面的编号顺延。没设位置时，排在本分镜自己的图片或视频之后，比如分镜里已经有 1 个视频，那 RefMod 1 就是 `<Video 2>`。
- 本分镜没用到的 RefMod 运行时会被忽略，不会出错。
- 例子：`<Subject 1> is the man in the black suit from <RefMod 1>, an identity reference. His facial identity, hairstyle, and clothing are preserved throughout.`

## 5. 切分镜和参考视频的起点
- 用 **Split shot…** 切出来的段，每段的参考视频都**从自己那段的起点开始**读（内部字段 trimStart）。所以提示词里写 “the entire clip of `<Video 1>`” 就行，不要写 “from 00:19.6”。
- custom 模式会把每段长度向上取整到 5+17n 帧，而且长度包含重叠帧，所以实际切点会比填的秒数稍微往后。要严格对齐原片的场景切点，最稳的做法是先把参考视频剪成独立文件，再分别拉进各个分镜。
- 手动把整个原片拉进多个分镜时，每段都会从原片 0 秒开始播。

## 6. 分镜之间的接法
- `overlapIn = 0` 是**硬切**：这一段独立生成，拼接时首尾直接相连。
- `overlapIn = 22` 或 `39` 是**续接**：新的一段拿上一段最终画面的最后 22 或 39 帧当开头接着生成。系统会自动在提示词前面加一句 “Continuity: ...”，所以自己不用写 “接着上一段”。
- 原片在这里换了场景或背景，就用硬切。同一个连续镜头要求动作接得上，就用续接。
- 画面没有交叉淡化，crossfade 只作用于音频。

## 7. 输出前自查
- [ ] 用的是六段式，或者 base 格式，段落顺序和字段名都正确。
- [ ] 时间从 0 秒算，最后一个镜头在分镜时长以内，分镜不超过约 15 秒。
- [ ] 标签编号和本分镜里的素材顺序一致；RefMod 写成 `<RefMod j>`。
- [ ] 没有引用原片的时间码，也没有描述别的分镜里的场景。
- [ ] 硬切还是续接，跟原片的切换方式一致。
- [ ] 每个镜头都写了构图、人物位置、环境光线、动作、运镜和声音。
