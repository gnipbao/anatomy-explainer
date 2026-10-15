# 旁白适配层

`scripts/tts.py` 使用 Python 3.9+ 标准库；正式运行还需要 `ffmpeg`、`ffprobe`。项目中的 `job.json` 是唯一内容输入。先完成讲稿和场景审核，再设置 `content_ready: true`。以下命令中的项目路径可以是相对路径，脚本会解析为绝对路径。

```bash
python3 scripts/tts.py --project /path/to/project --dry-run
python3 scripts/tts.py --project /path/to/project
python3 scripts/tts.py --project /path/to/project --force
```

`--dry-run` 只验证结构并输出公开配置、字符数和拟合成短句，不读取凭据、不访问网络、不调用合成、不创建音频、不估算时长。它不证明账户、音色、语言或外部音频可用。已有音频输出时默认拒绝覆盖；`--force` 替换整套 `audio/`、完整旁白、字幕和时间轴，不能把私人文件存进生成用的 `audio/`。

## 默认：豆包 Seed TTS 2.0

```json
{
  "schema_version": 1,
  "topic": "声音如何传播",
  "content_ready": true,
  "language": "zh-CN",
  "tts": {"provider": "doubao", "segment_mode": "phrases"},
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

## 本地多语言基础：macOS say

设置 `"tts": {"provider": "say", "segment_mode": "phrases"}`。脚本从系统已安装音色中选择与 `language` 匹配的音色，可用 `tts.voice` 精确指定安装名称。先运行 `say -v '?'` 查看可用音色。系统未安装对应语言音色则报错；不静默退回其他语言。此适配层提供基础本地合成，音色质量和可用语种由本机决定。

macOS 的受限沙箱可能让 `say` 返回退出码 0、却生成 0 帧 AIFF。适配层会在追加停顿前检查源音频实测时长，遇到空音频明确报错，不会把停顿静音当成成功旁白。此时需要在获准访问系统语音服务的本地环境执行合成，或把真实已生成音频交给 `external`；脚本自身不会提升权限。2026-10-15 本机复现了沙箱内 0 帧、同一文本在获准环境生成 2.316735 秒音频的差异。

## 其他引擎：external

设置 `"tts": {"provider": "external"}`，把其他语言引擎或人工录制的真实音频放到 `PROJECT/audio-input/<scene-id>.wav`（也支持 `.mp3`、`.m4a`）。每个场景恰好一个输入文件。脚本不会替你调用其他云服务，也不会验证外部语音与文字是否一致；正式使用前需要人工试听。输入必须解析到项目目录内部。

外部场景音频没有逐短句实测边界，因此 `external` 只支持 `tts.segment_mode: "scene"`（可省略）。若旧配置来自短句模板，必须显式改为 `scene`；请求 `external` + `phrases` 会在合成前清楚报错，不会用字数比例伪造短句字幕。人工对齐的 cues 应交给独立审核的手工字幕流程，本脚本不推断它们。

## 按真实音频对齐短句

`tts.segment_mode: "phrases"` 适用于 `doubao` 和 `say`。先按标点分出语义短句，中文在同一句内尽量合并为 12–26 字、最多 32 字；短的完整句子可以独立成段。英文按空格和标点切分，保持单词完整。超长单词可超过字符上限。分段只决定合成文本，绝不决定时间长度。

每个短句实际合成后，先检查源音频正时长，再转换 WAV 并由 `ffprobe` 实测。按这些短句音频的真实长度顺序拼成场景，随后追加 `hold_seconds` 对应的真实静音，最后拼接所有场景。短句字幕在该短句音频结束时消失，场景末尾停顿没有残留字幕。分开合成可能改变连续讲述的语调和停连，正式使用前应试听。

未设置 `tts.segment_mode` 时继续使用旧版 `scene` 路径，保持既有 job 兼容；也可显式设置 `"scene"`。两种模式均为真实语音实测边界，不是逐词、逐字或强制对齐。

## 输出和时间精度

- `audio/<scene-id>.wav`：逐场景音频；`hold_seconds` 是追加到该段末尾的真实静音。
- `narration-full.wav`：按场景顺序拼接的完整旁白。
- `narration.srt`：`phrases` 模式每个实测短句一条字幕；`scene` 模式维持每个场景一条字幕，覆盖该场景及末尾停顿。
- `timing.json`：保留 `schema_version: 1`、`provider`、`language`、顶层 `alignment: "scene"`、完整实测 `duration`、`job_fingerprint`，以及 `scenes` 中的 `id/start/end/duration/audio`。`phrases` 模式另加 `captions: {"alignment": "measured-phrases", "cues": [...]}`；每个 cue 含 `id`（如 `s01-p001`）、`scene_id`、绝对秒 `start/end` 和 `text`。cue 按实测短句音频长度对齐，时间不含场景末尾静音。旧场景模式不新增 `captions`。

每个短句、场景和完整音频都通过 `ffprobe` 测量真实时长，禁止使用字数估算代替。`job_fingerprint` 覆盖语言、TTS 配置、场景 ID、讲稿和停顿，用于检测修改讲稿后误用旧配音。只有整套音频、字幕和时间轴成功后才提交输出，`timing.json` 最后写入；普通失败清理本次暂存，不留下貌似成功的半成品。短句 MP3/WAV 和临时文本仅在暂存目录存在，成功也只发布场景 WAV、完整 WAV、SRT 和 timing，不发布原始响应或短句中间文件。失败的 `--force` 合成保留上一套完整输出，因此修改内容后仍须重新成功合成才可使用。多进程同时写同一项目不受支持；执行期间不要手动编辑输出目录。

离线验证：

```bash
python3 -m unittest discover -s tests -p 'test_tts.py' -v
python3 -m unittest discover -s tests -p 'test_segments.py' -v
```

测试包括流完成/业务失败/缺音频、危险 ID 和语言检查、dry-run 不执行、分句标点和英文单词边界、真实 ffmpeg 时长与末尾静音、覆盖保护、失败清理，以及用明确标注的测试波形验证逐短句时轴。测试不请求豆包，也不把测试波形冒充真实配音或云端成功。
