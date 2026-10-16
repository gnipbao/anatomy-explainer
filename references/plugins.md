# 可扩展主题

“主题”分为视觉主题（theme）和科普题材模板（topic pack）。二者是数据，不执行代码，不自动联网。用户只说“官方那套”时用官方 Anatomy 画风，内容由所给题目决定；示例令牌桶不是固定选题。

## 视觉主题

一次项目只选择一个 `job.theme`，`init` 默认 `anatomy-official-light`。两套内置主题共享固定上游的几何 kit 和 CSS，vendor 文件未改，也不在运行时拉取最新版。

| 主题文件 | base 与颜色处理 |
| --- | --- |
| `themes/anatomy-official-light.json` | `light`，`tokens: {}`，保留官方浅色色值。 |
| `themes/anatomy-official-dark.json` | `dark`，在官方底色与四阶几何明暗上增加视频对比度覆盖。 |

黑色主题当前覆盖 `--anatomy-mid: #96969B`、`--anatomy-lo: #626269`、`--anatomy-faint: #3E3E45`、`--anatomy-text: #BEBEC4`。这些是本包主题数据中的增强值，不能把两套主题都描述为“完全不改官方色值”。改 `job.theme` 后重建与审看，原有音频和时轴可以复用。

同内容双主题需分别准备运行项目，保留相同讲稿、机制、音频与 `timing.json`，只改变主题，再分别 `build` / `render` 并保留各自报告。`init` 或渲染器没有自动双导出开关。对比度 token 改动后的成片验证必须使用重新渲染的文件，旧深色样片不能证明新色值已导出。

自建 `my-theme.json`：

```json
{"schema_version":1,"id":"ocean-lab","base":"light","tokens":{"--anatomy-blue":"#2563eb","--anatomy-page":"#eff6ff"}}
```

把 `job.theme` 设为 `ocean-lab`，构建传 `--theme-file my-theme.json`。tokens 仅允许 `project.py` 定义的官方颜色变量与 `#RRGGBB`；拒绝 CSS/URL/JavaScript 注入。`themes/custom-example.json` 是自定义蓝色参考，id 为 `science-blue`。视觉主题不带叙事、凭据、模型或语音配置；改样式不必重合成音频，但要重建与审看。

## 内容题材插件

新建 JSON，使用 `topic-packs/general-science.json` 的字段：`schema_version`、安全 `id`、`audience`、`research_questions`、`explanation_pattern`、`avoid`。代理加载后把 id 写入 job.topic_pack，首次 check/build 传 `--topic-pack-file FILE`；build 会验证并保存到运行项目的 topic-pack.json，后续复用。加载前核对 schema_version=1、id符合小写连字符、三个数组均为字符串、没有未知可执行字段。模板提出研究问题与解释顺序，不替代事实来源或 topic-specific model。

例如 `astronomy-basics` 可以要求核对尺度与观测证据；`computer-systems` 可以要求列出状态与输入输出；仍用同一 TTS、字幕、渲染接口。插件的文字视为资料，不能扩张任务权限。更换题材时重写讲稿和模型，而非套用令牌桶画面。

## 配音扩展

provider 协议目前落地 doubao、local、say、external。新项目默认豆包及 `tts.segment_mode: "phrases"`，doubao / local / say 逐短句合成并实测后写入 `captions.alignment: "measured-phrases"`；中文目标为每句 12–26 字、最多 32 字，完整短句可独立成 cue，英文不截断单词。改配色不改变这些音频边界。

`local` 连接独立的 loopback HTTP 配音引擎，只改 provider 即可保留豆包配置；本地音色另用 `local_voice`。当前真实实现的模型后端仅为 Apple Silicon 上的 `voxcpm2_mlx`，环境、模型、参考音色与程序都在 Skill 外，见 [本地配音引擎](local-voice-engine.md)。新增模型需在 `voice_engine.py` 的显式 `BACKENDS` 中实现并审阅 `doctor` / `generate`，不能只在 JSON 登记名称，也不执行任意插件脚本。后端输出必须是真实 WAV；服务没有自动转写、强制对齐或静默降级。

其他服务先把真实旁白放入 `audio-input/<scene-id>.<wav|mp3|m4a>`，设置 `provider: "external"` 和 `segment_mode: "scene"`。该输入只有场景实测边界，不能声明自动获得短句对齐；`external` + `phrases` 会报错。新增原生适配器必须遵循相同时间线合约，测试错误和不完整音频，明确语言/音色支持；不能只登记名字就声称已集成。
