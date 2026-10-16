# 旁白适配层

`scripts/tts.py` 使用 Python 3.10+ 标准库；正式运行还需要 `ffmpeg`、`ffprobe`。默认影院字幕通过 `subtitle_tools.py` 桥接共享 `subtitles.mjs`，还需要 Node 22+，无需 npm 或 pip 依赖。项目中的 `job.json` 是唯一内容输入。先完成讲稿和场景审核，再设置 `content_ready: true`。以下命令中的项目路径可以是相对路径，脚本会解析为绝对路径。

```bash
python3 scripts/tts.py --project /path/to/project --dry-run
python3 scripts/tts.py --project /path/to/project
python3 scripts/tts.py --project /path/to/project --force
```

`--dry-run` 只验证结构并输出公开配置、字符数和拟合成短句，不读取凭据、不访问网络、不调用合成、不创建音频、不估算时长。它不证明账户、音色、语言或外部音频可用。已有音频输出时默认拒绝覆盖；`--force` 替换整套 `audio/`、完整旁白、字幕和时间轴，不能把私人文件存进生成用的 `audio/`。

## 本机默认与单次覆盖

`project.py init`（含 `--example`）先使用公开模板，再应用可选的 `~/.config/anatomy-explainer/preferences.json`，最后由 `--tts-provider` 覆盖服务。没有偏好文件时保留豆包短句模式；偏好文件只允许 `schema_version: 1` 与 `tts` 中的 `provider`、`local_voice`、`segment_mode`，不存凭据、接口地址或参考音频路径。格式有误时应修复偏好，不静默忽略。

```json
{"schema_version": 1, "tts": {"provider": "local", "local_voice": "default", "segment_mode": "phrases"}}
```

`init --preferences-file FILE` 指定另一份偏好，`--ignore-preferences` 跳过读取，两者互斥。显式指定的文件必须存在；默认文件缺失才回落到公开模板。最终服务为 `external` 时，初始化固定采用 `segment_mode: "scene"`。本机偏好只参与初始化；已有 job 的检查、合成和构建仍使用 job 中的配置，不自动迁移。用户本轮明确选择优先；参考音色由本地引擎的私有配置管理，见 [本地配音引擎](local-voice-engine.md)。

## 豆包 Seed TTS 2.0（未配置偏好时）

```json
{
  "schema_version": 1,
  "topic": "声音如何传播",
  "content_ready": true,
  "language": "zh-CN",
  "tts": {"provider": "doubao", "segment_mode": "phrases"},
  "presentation": {"captions": {"mode": "phrase", "style": "cinema", "placement": "bottom"}},
  "scenes": [
    {"id": "s01", "title": "振动", "narration": "声音来自振动。", "hold_seconds": 0.5}
  ]
}
```

默认资源 `seed-tts-2.0`、默认音色 `zh_female_vv_uranus_bigtts`。用 `tts.voice` 和 `tts.resource_id` 显式覆盖；资源必须匹配所选音色。脚本不会从文字自动推断或翻译语言。`language` 接受 `zh-CN`、`en`、`ja-JP` 等语言标签的基础语法；音色的具体语种能力仍须核对控制台。

在脚本外安全配置环境变量，切勿把值写进 `job.json`、日志或公开文件：

- 新版控制台：`DOUBAO_API_KEY`，发送为 `X-Api-Key`。
- 旧版控制台：`DOUBAO_APP_ID` + `DOUBAO_ACCESS_KEY`，分别发送为 `X-Api-App-Id`、`X-Api-Access-Key`。旧环境的 `DOUBAO_TTS_TOKEN` / `VOLCENGINE_ACCESS_TOKEN` 仅作为旧版 Access Key 的兼容变量，仍需 App ID。

中文可用默认音色。**非中文必须显式提供已核对语种支持的 `tts.voice`**；填写语言标签本身不代表所选音色支持该语言，也不保证发音质量。

实现使用固定 HTTPS 地址 `https://openspeech.bytedance.com/api/v3/tts/unidirectional`，按 NDJSON 读取 `code: 0` 的 base64 音频，只在收到 `code: 20000000` 且已有音频时接受结果。请求 MP3 / 24 kHz 后统一转换为 48 kHz 立体声 PCM WAV。连接失败、业务错误、无完成帧或无音频均失败，不将半截音频当成完成产物。禁止跟随重定向，以免转发鉴权头；不打印请求头、凭据值、远端错误正文或原始命令错误输出。`phrases` 模式每个短句单独请求，`scene` 模式每个场景单独请求；失败不自动重试，避免重复计费。

核对日期：2026-10-15。接口依据：[火山引擎单向流式合成 HTTP](https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-text-to-speech-http?lang=zh)、[HTTP Chunked V3](https://docs.volcengine.com/docs/DoubaoVoice/HTTPChunkedSSEUnidirectionalStreaming-V3?lang=zh)、[旧版鉴权](https://docs.volcengine.com/docs/DoubaoVoice/old-version-console-authentication-reference-example?lang=zh)。没有真实云端合成证据时，不能声称云端音色或账户已验证。

## 独立本地引擎：local

VoxCPM2 本地服务的安装、模型下载、音色配置与独立 CLI 见 [本地配音引擎](local-voice-engine.md)。当前后端需要 Apple Silicon Mac、专用 Python 3.12 / MLX 环境和手动下载的 `mlx-community/VoxCPM2-8bit`；这些依赖不装入基础视频环境，主权重不随包分发或自动下载。

服务就绪后，只把 `tts.provider` 改为 `"local"`。保留原 `tts.voice` / `tts.resource_id` 方便切回豆包；本地使用另一个字段 `tts.local_voice`（默认 `"default"`），对应本机服务注册的音色 ID。可选配置：

```json
{
  "provider": "local",
  "segment_mode": "phrases",
  "local_voice": "default",
  "endpoint": "http://127.0.0.1:3931/v1/audio/speech",
  "timeout_seconds": 1800
}
```

`endpoint` 省略时使用上述地址，仅接受 loopback HTTP 的固定路径；请求不走代理、不跟随重定向。`timeout_seconds` 省略时为 600，允许 30–3600 秒。适配器提交 `model: "voxcpm2"`、场景或短句原文、音色 ID、`response_format: "wav"` 和目标语言，核对响应长度及实际 PCM WAV 后才归一化、测长和提交整套产物。连接失败、503 缺模型、409 正忙、超时、静音或坏音频均明确失败，不自动重试或转用豆包；失败时保留上一套完整输出。

`phrases` 与 `scene` 使用原有时间线合约，`phrases` 仍逐短句合成并实测，因而可直接替换原服务。每次请求启动隔离 worker 并重新加载模型；短句之间的韵律和速度需要试听，不因换模型就宣称改善。`scene` 只测出场景边界；独立整篇 CLI 没有转写与字幕对齐，只能标记 `alignment_pending`，不能按字数生成假精确 cues。`language` 是标签且不负责翻译，当前 MLX worker 不将它映射成未实现的强制语言控制；多语言发音须以实际讲稿试听验证。

## 本地多语言基础：macOS say

设置 `"tts": {"provider": "say", "segment_mode": "phrases"}`。脚本从系统已安装音色中选择与 `language` 匹配的音色，可用 `tts.voice` 精确指定安装名称。先运行 `say -v '?'` 查看可用音色。系统未安装对应语言音色则报错；不静默退回其他语言。此适配层提供基础本地合成，音色质量和可用语种由本机决定。

macOS 的受限沙箱可能让 `say` 返回退出码 0、却生成 0 帧 AIFF。适配层会在追加停顿前检查源音频实测时长，遇到空音频明确报错，不会把停顿静音当成成功旁白。此时需要在获准访问系统语音服务的本地环境执行合成，或把真实已生成音频交给 `external`；脚本自身不会提升权限。2026-10-15 本机复现了沙箱内 0 帧、同一文本在获准环境生成 2.316735 秒音频的差异。

## 其他引擎：external

设置 `"tts": {"provider": "external"}`，把其他语言引擎或人工录制的真实音频放到 `PROJECT/audio-input/<scene-id>.wav`（也支持 `.mp3`、`.m4a`）。每个场景恰好一个输入文件。脚本不会替你调用其他云服务，也不会验证外部语音与文字是否一致；正式使用前需要人工试听。输入必须解析到项目目录内部。

外部场景音频没有逐短句实测边界，因此 `external` 只支持 `tts.segment_mode: "scene"`（可省略）。若旧配置来自短句模板，必须显式改为 `scene`；请求 `external` + `phrases` 会在合成前清楚报错，不会用字数比例伪造短句字幕。人工对齐的 cues 应交给独立审核的手工字幕流程，本脚本不推断它们。

## 按真实音频对齐短句

`tts.segment_mode: "phrases"` 适用于 `doubao`、`local` 和 `say`。先按标点分出语义短句，中文在同一句内尽量合并为 12–26 字、最多 32 字；短的完整句子可以独立成段。英文按空格和标点切分，保持单词完整。超长单词可超过字符上限。分段只决定合成文本，绝不决定时间长度。

每个短句实际合成后，先检查源音频正时长，再转换 WAV 并由 `ffprobe` 实测。按这些短句音频的真实长度顺序拼成场景，随后追加 `hold_seconds` 对应的真实静音，最后拼接所有场景。原始短句 cue 保留该短句实测音频的开始和结束秒数；影院字幕另按视频帧派生显示边界，并在相邻条目间保留 2 帧间隔，场景末尾停顿没有残留字幕。分开合成可能改变连续讲述的语调和停连，正式使用前应试听。

未设置 `tts.segment_mode` 时继续使用旧版 `scene` 路径，保持既有 job 兼容；也可显式设置 `"scene"`。两种模式均为真实语音实测边界，不是逐词、逐字或强制对齐。

## 输出和时间精度

- `audio/<scene-id>.wav`：逐场景音频；`hold_seconds` 是追加到该段末尾的真实静音。
- `narration-full.wav`：按场景顺序拼接的完整旁白。
- `narration.srt`：`phrases` + 默认 `cinema` 模式使用共享引擎输出规范显示文本、最多两行及帧对齐边界；显式 `kinetic` 使用原短句文本与实测秒数。`scene` 模式维持每个场景一条字幕，覆盖该场景及末尾停顿。
- `timing.json`：保留 `schema_version: 1`、`provider`、`language`、顶层 `alignment: "scene"`、完整实测 `duration`、`job_fingerprint`，以及 `scenes` 中的 `id/start/end/duration/audio`。`phrases` 模式另加 `captions: {"alignment": "measured-phrases", "cues": [...]}`；每个 cue 含 `id`（如 `s01-p001`）、`scene_id`、绝对秒 `start/end` 和 `text`。cue 按实测短句音频长度对齐，时间不含场景末尾静音。旧场景模式不新增 `captions`。

默认影院字幕与语音分段属于两层：TTS 的 narration 与 `timing.captions` 是原始证据，显示轨规范化和排版不改写它们。中文显示文本中的逗号、句号替换为空格；小数等必要标点保持，语音合成仍读取原稿。显示边界四舍五入到视频帧；为留 2 帧间隔，仅在必要时收短前条结束帧，不平移后条开头，也不凭文字比例伪造词级时间。无法保留正时长又满足间隔时记录 `minimum-gap` 告警，须审核原时轴。读取方式为半开区间 `[start,end)`，烧录按全片显示轨直接切换，无淡入、位移或字号变化。

`project.py build` 在 cinema 模式且有实测或人工 cues 时使用同一引擎刷新 `narration.srt` 和 `cinematic.srt`，并生成 `subtitle-qc.json`；两份 SRT 与烧录共享派生内容和帧边界。中文每行最多 16 个全角等效字符、英文每行最多 42 字符，最多两行，固定字号不自动压缩。可在 `presentation.captions.keep_together` 中提供最多 64 个短术语（每项不超过 100 字符，无控制字符）；保护词过长仍应报告溢出，不能隐藏问题。`line-overflow` error 阻断 build/render；时长建议 5/6–7 秒及中文每秒 9 字／英文每秒 20 字的阅读速度限值产生告警，需要结合旁白审看。这里参考 [Netflix 简中字幕指南](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215986007-Chinese-Simplified-Timed-Text-Style-Guide)；影院预设的字体、安全边距和外观由本项目定义，不自称全平台标准合规。

没有明确 cue 的旧场景音频，表现层只能给出标为 `estimated-phrases` 的推估短句；不能把它报告为实测字幕，或声称旧场景 SRT 与该推估画面已经逐帧一致。显式 `presentation.captions.mode:"scene"` 保留整场字幕兼容；显式 `style:"kinetic"` 才启用旧短句的换位和淡入效果。

每个短句、场景和完整音频都通过 `ffprobe` 测量真实时长，禁止使用字数估算代替。`job_fingerprint` 覆盖语言、TTS 配置、场景 ID、讲稿和停顿，用于检测修改讲稿后误用旧配音。只有整套音频、字幕和时间轴成功后才提交输出，`timing.json` 最后写入；普通失败清理本次暂存，不留下貌似成功的半成品。短句 MP3/WAV 和临时文本仅在暂存目录存在，成功也只发布场景 WAV、完整 WAV、SRT 和 timing，不发布原始响应或短句中间文件。失败的 `--force` 合成保留上一套完整输出，因此修改内容后仍须重新成功合成才可使用。多进程同时写同一项目不受支持；执行期间不要手动编辑输出目录。

离线验证：

```bash
python3 -m unittest discover -s tests -p 'test_tts.py' -v
python3 -m unittest discover -s tests -p 'test_segments.py' -v
```

测试包括流完成/业务失败/缺音频、危险 ID 和语言检查、dry-run 不执行、分句标点和英文单词边界、真实 ffmpeg 时长与末尾静音、覆盖保护、失败清理，以及用明确标注的测试波形验证逐短句时轴。本地适配器另覆盖 loopback 限制、保留豆包音色、缺模型和不完整音频；独立服务、环境准备与 worker 检查见对应 `test_voice_engine.py`、`test_voice_setup.py`、`test_local_voice_worker.py`。测试不请求豆包或下载模型，不把测试波形和模拟模型冒充真实配音成功。
