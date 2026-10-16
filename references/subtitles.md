# 电影式字幕

默认 `presentation.captions = {"mode":"phrase","style":"cinema","placement":"bottom"}`。对白字幕承担连续阅读；知识栏、部件标注与模型动画承担解释。不要把“字幕随讲解变化”理解成字幕位置或字号不断变化。

## 外观与断行

- 全画幅底部居中，单行和双行保持共同底线。横屏预设42px/1080p、底部5%留白，随分辨率等比缩放；竖屏另留安全区。
- 白色无衬线常规字重，黑色描边与轻阴影，透明背景。浅色和黑色主题使用相同字幕样式，不做卡片、色块、边线、飞入、淡入、缩放、游走或逐字高亮。
- 中文每行最多16全角等效字符，最多两行；英文预设42字符/行，其他语种目前也沿用基础预设，正式交付须另核对对应语言规则。优先单行；需要双行时在语义处分行，避免把开括号留行尾、闭标点留行首，避免顶行只有一两字。
- `captions.keep_together` 可列出人名、专名、数字单位等不应拆开的词组。它是断行保护，不能代替人工语言审稿。放不下时报告错误，不能偷偷压小字号或删字。
- 简体中文显示中逗号、句号改成一个空格，必要问号、感叹号保留。小数、域名及数字分组不按句号处理。原始讲稿、TTS输入与实测cue文案不变；显示文案另存。

## 时间与一致性

`timing.captions.cues` 是源数据，保留原始音频边界。共享 `scripts/subtitles.mjs` 将它们转换为显示事件：起止四舍五入到视频帧，必要时只略缩前一条尾部来保留两帧间隔，不移动下一条起点、不凭字数制造逐词时间。派生事件保留 `source`、`source_text`、`source_start/end`，显示时间为 `start/end` 与 `start_frame/end_frame`。字幕事件从全片时轴查询，不能因画面场景边界的半帧差异漏掉字幕。

TTS在成功发布前用同一引擎生成 `narration.srt`；构建时重新写入相同的 `narration.srt`、`cinematic.srt` 和 `subtitle-qc.json`。SRT仅支持毫秒精度，起止都从整数帧向下取整到毫秒，误差小于1毫秒，确保在每个视频采样帧上与烧录的半开区间一致。浏览器烧录同样调用该引擎；不能维护两套独立的断行/时间算法。

本预设检查每条5/6–7秒、连续至少两帧间隔、中文成人9等效字/秒、英文20字符/秒。计入空格和标点，中文ASCII半宽按0.5计，人工换行不额外计。当前速度阈值针对成人；儿童项目需更保守的人工审稿。时长/速度或无法留间隔会给出告警；零帧、两行放不下等错误会阻断构建与导出。修复应回到讲稿或经审核的真实cue切点，不能通过拉长字幕盖过下一句来假装合规。

旧录音没有cue时，预览可使用明确标识的 `estimated-phrases`；正式字幕须人工核对。显式 `style:"kinetic"` 保留旧换位动效，显式 `mode:"scene"` 保留旧整场景行为；它们不是本片的电影式交付默认。

## 验收与依据

逐场景检查实际画面；同一条字幕全程位置、字号、不透明度一致，单/双行共同底线；每帧的显示cue与SRT事件一致，暂停区无残留，乱序seek可复现。渲染器检查最多两行、固定字号、底部居中、白字描边、无位移淡入以及越界/溢出/标题与来源遮挡。通过这些检查不代表完整人工试听、翻译审稿或平台认证。

中文行长、行数、白字、标点和阅读速度参考 [Netflix简体中文规范](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007-Chinese-Simplified-Timed-Text-Style-Guide)；时长与两帧间隔参考 [通用要求](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215758617-Timed-Text-Style-Guide-General-Requirements)及[时间指南](https://partnerhelp.netflixstudios.com/hc/en-us/articles/360051554394-Timed-Text-Style-Guide-Subtitle-Timing-Guidelines)，计数参考[官方说明](https://partnerhelp.netflixstudios.com/hc/en-us/articles/115001352212-How-is-reading-speed-measured-Do-punctuation-and-spaces-count)。这里选择适合科普旁白的电影式预设；42px、描边粗细、安全边距和禁动效是本项目设计参数，不声称全球统一标准或完整Netflix交付合规。
