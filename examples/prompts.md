# 首次使用与回归提示

以下是输入示例及验收条件，示例本身不代表完成了访谈、合成或渲染。路径均相对 Skill 根目录；输入、凭据和产物保留在用户指定的工作目录。

## 中文默认：grill-me 审题、豆包与官方主题

> 用 anatomy-explainer 做一条中文科普视频，主题是“令牌桶为什么既限速又允许突发”。结合 grill-me 先把受众和核心问题审清楚，默认采用官方主题与豆包语音。你先查事实，每轮把已经具备前置条件的问题一起问我，给出推荐答案；我确认简报后再制作。

应先定位精确名称的 `grill-me` 入口及其 `grilling` 实现；找不到时明确使用本 Skill 的审题 fallback。官方主题与豆包已有用户约束，不重复询问。受众、时长等未定项应标为推荐或待答，不伪造用户确认。锁定后使用 `themes/anatomy-official-light.json` 等符合用户约束的视觉配置。

## v0.2：实测短句、动态字幕与部件动作

> 做一条“声音怎样穿过空气”的横屏中文科普视频。使用默认浅色主题和豆包 TTS，按语义短句分别合成并实测时长；左侧显示知识点，右侧用振动物体、空气粒子和接收端解释因果。字幕随短句在安全区切换位置。画面要呈现部件的阶段变化，最后检查倒放和随机跳转能否恢复同一状态。

新 job 应含 `tts.segment_mode: "phrases"` 和 `presentation.captions: {"mode":"phrase","placement":"auto"}`。短句分别合成后，`timing.captions.alignment` 为 `measured-phrases`，SRT 与 cues 使用同一组实测边界；场景末尾 hold 不延长字幕。不得用字数比例冒充音频对齐，也不声称逐字同步。横屏预览应检查左知识栏、右机制舞台和每条字幕的遮挡；机制 `frame` 根据本次时间/进度恢复部件状态，仅有镜头缩放或整组淡入不满足部件阶段变化的要求。

## 同内容浅色与黑色分别导出

> 使用相同讲稿、机制、配音和时轴，给我浅色与黑色两版视频。浅色用 anatomy-official-light；黑色用 anatomy-official-dark，接受其官方 dark base 上的视频对比度增强。分别审看字幕、木构细节和线条，再导出并保留两份渲染报告。

一次 `init` 仍只选择一个主题，默认 light；双版本需两份运行项目分别设置 `job.theme`、`build` 和 `render`。主题变化可以复用真实音频和 `timing.json`，无需再次请求 TTS。黑色主题的 mid / lo / faint / text 有本包 token 覆盖，不能说两版均保持全部官方色值；vendor 几何与 CSS 未改。必须重新渲染并验证对应色值的输出，旧深色样片不构成增强黑色已完成的证据。

## 英语：显式选择本地 say voice

> Plan an English explainer about token buckets with anatomy-explainer and grill-me. I have already chosen the official light theme and the macOS say provider with voice Samantha. Check whether this exact voice is available yourself. Interview me about the unresolved audience, central question and scope in dependency-aware rounds, each with recommended answers. Start production after I confirm the brief.

不重问已指定的语言、主题、provider 和 voice。代理核对 `Samantha` 可用性，不能要求用户代查。新项目的 `say` 也可使用 `segment_mode: "phrases"`，保留英文单词并实测每句长度。缺少该 voice 或 `say` 时报告事实，把确实影响制作的替代选择放入后续前沿；不能无提示换成其他语音或付费服务。

## 内容主题与视觉插件分别扩展

> 读取 topic-packs/general-science.json，把它复制为我指定工作目录里的“天文科普”内容主题插件，受众固定为高中生，增加尺度与观测证据的核对问题。用它审题“月亮为什么总以同一面朝向地球”。视觉固定官方浅色主题。用 grill-me 帮我决定核心问题与覆盖范围，锁定简报后再开始制作。

内容主题改变研究问题、解释结构与适用边界；视觉插件改变允许的颜色 token。二者都不能改写已验证事实、绕过研究或执行插件代码。代理读取并核对内容主题数据后再审题；未知字段、损坏 JSON 和依赖问题应具体报告，不静默忽略。可用 `themes/custom-example.json` 演示后续视觉扩展。

## 缺少依赖：事实不让用户代查

> 我想做“令牌桶”的中文解说，官方主题和豆包已确定。先用 grill-me 审清楚内容。请你查 ffmpeg、浏览器渲染器和语音配置是否可用；若缺依赖，仍推进不依赖它们的审题与资料整理，只把必须由我选择的替代方案问我。不打印、写进文档或打包凭据。

应派代理查事实，只阻塞依赖未决事实的决策分支。同一轮不问互相依赖的问题。完成简报前不生成假定为正式版本的旁白与付费音频；缺依赖时保留资料和未决项，不能声称已有成片。环境修复后继续原简报，不丢弃已确定的条件。

## 误解检查：事实校正先于审题选择

> 审查这句想法：“令牌桶最多4枚，所以每秒能通过4个；6个一起到达时4个通过，另外2个自动排队，1秒后桶又变满。”本例每秒补1枚，每请求耗1枚，缺令牌就拒绝，无队列。先查明事实错误，再用 grill-me 帮我决定视频重点讲容量与速率，还是讲不同限流器的对比。

应直接纠正三处事实冲突：容量不等于持续速率、拒绝不等于排队、1秒只能补1枚。这些不是让用户投票决定的选项。随后对仍未定的内容范围给出推荐并等待回答。[learning-checks.json](learning-checks.json) 的可选教育材料与场景 `predict`、`mechanism`、`transfer` 对齐：初始4同时6通过4拒2；空桶等2秒后同时3，通过2拒1。

## 不把示例假设当用户确认

> 参考 examples/brief.json 的结构为“咖啡为什么提神”建立简报。不要继承令牌桶的受众、时长或核心问题，也不要把示例状态改成 locked。先查资料，再按 grill-me 规则提问。

应保留 `example_assumption` 与真实 `user_answer` 的区别。只有用户实际确认了当前简报的共同理解，才进入 `locked`；复制示例、自动采用推荐值或用户沉默都不构成确认。预测题可另存为 `learning-checks.json`，不把它称为 grill-me 原生 JSON 接口。

## 历史器物：确定用途与不确定结构

> 测试制作一条“木牛流马”的中文介绍视频，保留官方浅色主题，使用已配置的豆包TTS。核对古籍来源，明确区分史料和结构示意。

应核对《三国志》与裴注所引制法，把231/234写成使用记载而不是发明日期。原文的人力描述不等于无需人力；不把单轮车或其他复原形态说成唯一历史原貌。自定义几何从运行项目 assets/kit 导入，mechanism.mjs 必须导出 mount；按场景展示装配、支撑分层、人力输入与轮转等部件变化，并用绝对时间重算，不能只推近镜头。逐场景审看短句字幕与“解释性示意”标记，只有真实云合成与MP4导出成功后才报告通过。
