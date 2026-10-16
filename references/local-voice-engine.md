# 独立本地配音引擎

`anatomy-voice-engine` 是与 Skill 分开运行的本机 HTTP 服务。当前后端为 `voxcpm2_mlx`：Apple Silicon Mac、原生 arm64 Python 3.12、`mlx-audio==0.5.9`、`mlx==0.31.1`、`soundfile==0.13.1`，使用社区转换的 VoxCPM2 8-bit 权重。Windows、NVIDIA 和其他模型后端尚未实现。基础视频流程仍使用原来的 Python 环境；MLX 依赖只装入独立虚拟环境。

豆包仍是新项目默认值。服务启动且模型就绪后，只把已有 `job.json` 的 `tts.provider` 改为 `"local"`，继续使用相同的 TTS、字幕、构建与渲染命令。原有 `tts.voice`、`tts.resource_id` 可保留，切回 `"doubao"` 时继续使用；本地音色另用 `tts.local_voice`，默认 `"default"`。

## 准备环境与模型

在 Skill 包目录运行以下命令。第一条只预览路径；第二条会创建独立运行目录、安装依赖并下载四个已固定哈希的小型 JSON 文件。需要已安装 `uv`，它可能按需准备 Python 3.12；不会下载主模型权重，也不会启动服务或修改其他声音引擎。

```bash
python3 scripts/setup_voice_engine.py --install-deps --prepare-metadata --dry-run
python3 scripts/setup_voice_engine.py --install-deps --prepare-metadata
```

默认目录：

| 用途 | 位置 |
| --- | --- |
| 独立程序 | `~/.local/share/anatomy-voice-engine/app/` |
| MLX 虚拟环境 | `~/.local/share/anatomy-voice-engine/.venv/` |
| 私有配置 | `~/.config/anatomy-voice-engine/config.json` |
| 模型 | `~/Models/AnatomyVoice/VoxCPM2-8bit/` |

模型来自 [mlx-community/VoxCPM2-8bit](https://huggingface.co/mlx-community/VoxCPM2-8bit)，固定 revision 为 `d52725898a0675703f7f9ddc5a4d1a3cdbb99032`。手动下载 [model.safetensors（3,221,772,935 字节，约 3.22 GB）](https://huggingface.co/mlx-community/VoxCPM2-8bit/resolve/d52725898a0675703f7f9ddc5a4d1a3cdbb99032/model.safetensors?download=true)，保持文件名，放到：

```text
~/Models/AnatomyVoice/VoxCPM2-8bit/model.safetensors
```

同目录应有 `config.json`、`tokenizer.json`、`tokenizer_config.json`、`special_tokens_map.json`；使用 `--prepare-metadata` 后这四个文件已准备好。无需下载 `audiovae.pth`。模型不在 Skill 包内；权重大小不等于运行内存需求，本项目未确定 8 GB / 16 GB 设备的最低条件或全机型耗时。

下载完成后在包目录校验全部五个文件的大小和 SHA-256，再检查独立环境：

```bash
python3 scripts/check_voice_model.py
python3 "$HOME/.local/share/anatomy-voice-engine/app/voice_engine.py" doctor
```

模型校验成功只说明文件匹配固定清单；`doctor` 检查文件、依赖和 API，不加载权重完成合成，`synthesis_passed` 仍为 false。缺权重时应显示 `model_missing`，不要创建空文件绕过检查。程序不自动下载、购买服务或退回豆包。

已有配置会保留，更新程序前会把旧 `app/` 备份到独立运行目录的 `backups/`。可用 `--home`、`--config`、`--model-dir` 指定路径；已有配置不会随这些选项被重写，改路径时应核对其中的 `python_path` 和 `model_path`。配置模板见包根目录 `voice-engine.config.example.json`。

## 启动并替换豆包

在一个终端运行服务，按 Ctrl-C 停止；没有自动注册开机启动：

```bash
python3 "$HOME/.local/share/anatomy-voice-engine/app/voice_engine.py" serve
```

服务默认监听 `127.0.0.1:3931`。`GET /health` 返回 `ready` 状态；服务进程正在运行不等于模型就绪。权重缺失时服务仍可启动，合成返回 503。下载完成后健康检查会重新核对变化的模型文件；修改配置则需要重启服务。

只改已有 job 的 `tts.provider` 即可切换，下面是可选的完整本地字段片段，不是完整 job：

```json
{
  "tts": {
    "provider": "local",
    "segment_mode": "phrases",
    "local_voice": "default",
    "endpoint": "http://127.0.0.1:3931/v1/audio/speech",
    "timeout_seconds": 1800
  }
}
```

`endpoint` 省略时使用上述地址，仅接受 loopback HTTP 的 `/v1/audio/speech` 路径，禁止重定向与代理转发。适配器默认超时 600 秒，可设 30–3600 秒；独立服务的 `timeout_seconds` 默认 1800 秒，可设 1–7200 秒。若单段需要更久，应缩短语义段落并检查机器资源。并发合成返回 409，不自动排队或重试。

继续使用原流程；重做已有音轨时，核对目标后加 `--force`：

```bash
python3 scripts/tts.py --project /path/to/project --dry-run
python3 scripts/tts.py --project /path/to/project
python3 scripts/project.py build --project /path/to/project
```

`phrases` 仍逐短句合成并实测边界，保持旧电影式字幕合约；分段会重置模型上下文，每次请求也会启动独立 worker 并重新加载模型，不能承诺低延迟或连续段落的韵律。`scene` 每场景合成完整段落，只得到场景边界；不能将推估短句称为实测字幕。切换后必须试听漏读、重复、发音、尾音和句间停连，再确认成片。

## 独立生成完整旁白

引擎也可以脱离视频项目使用。先准备 UTF-8 讲稿文件和已经存在的输出目录：

```bash
python3 "$HOME/.local/share/anatomy-voice-engine/app/voice_engine.py" synthesize \
  --text-file /path/to/narration.txt --out /path/to/narration.wav \
  --voice default --language zh-CN
```

输出真实 PCM WAV 和同名 `.voice.json`，记录音频与讲稿哈希、生成参数、参考音频哈希、实测时长及模型文件指纹。模型运行指纹按相对文件名、大小、修改时间计算；固定权重的完整 SHA-256 由前面的模型校验器负责。默认不覆盖已有音频或元数据；`--force` 才替换这对产物。

单请求最多 12,000 字符，超长段落不保证可用。建议按完整语义段落先试听，完整讲稿并不保证自然度更好。该路径没有转写或字幕对齐，元数据明确为 `alignment_pending`、`listening: not_reviewed`、`transcript: not_reviewed`；不能直接作为有精确字幕时轴的成片交付。复用到视频时，按场景导入 `external`，或另行获得并审核真实 cues。

## 音色与生成参数

编辑私有配置的 `voice` 设置默认音色，或在 `voices` 注册其他 ID；job 的 `tts.local_voice` 只引用 ID，HTTP 请求不能传入任意文件路径或改变后端参数。

| 字段 | 含义 |
| --- | --- |
| `instruct` | 文字描述音色与表达，属于模型条件，不是已验证的质量承诺 |
| `reference_audio` | 现有本地参考音频，用于音色条件；仅使用本人或已获授权的参考 |
| `prompt_audio` + `prompt_text` | 连续语境参考与其准确转写，必须成对提供，可与 `reference_audio` 合用 |
| `language` | 默认语言标签；讲稿需直接写成目标语言，当前 worker 不把它映射成未实现的强制语种参数 |

参考音频、讲稿和本地绝对路径留在私有配置及运行目录，不放入公开 Skill。引擎不会自动降噪、人声分离或翻译，也不把服务名称当作模型或音色名称。当前实现不接受 `speed`、`temperature`、`cfg_scale` 或流式合成开关。

`generation` 支持 `seed`（0–4294967295）、`cfg_value`（大于 0 且不超过 10）、`inference_timesteps`（1–100）、`max_tokens`（1–6500）；默认分别为 42、2.0、10、2000。参数只从本机配置读取，改动后重新合成并试听。长文达到 token 上限或资源不足时，不能把提前结束的声音当作完整旁白。

## HTTP 合约与后端扩展

| 接口 | 返回 |
| --- | --- |
| `GET /health` | 就绪状态、后端、模型与安全诊断 |
| `GET /engines/tts` | 当前引擎和已注册音色 ID |
| `POST /v1/audio/speech` | 实际 `audio/wav`；请求字段为 `model`、`input`、`voice`、`response_format`、`language` |

`model` 为 `voxcpm2` 或 `default`，`response_format` 只接受 `wav`。成功响应包含实测时长、采样率和 `X-Subtitle-Alignment: pending`。这是一组明确实现的本地接口，不代表兼容所有 OpenAI、VoiceStudio 或同名软件 API；没有 `/generate` 或 `/transcribe`。只允许本机非浏览器客户端访问，无 CORS 和远程绑定。

扩展模型时，在 `voice_engine.py` 的显式 `BACKENDS` 注册经审阅的后端，实现 `doctor(config)` 就绪诊断与 `generate(config, request, output)` 真实 WAV 输出，保留请求校验、隔离执行、失败清理和输出检查。安装任意 Python 插件或在配置中写一个模型名不会自动集成新模型。新增后端需验证空/坏/半截音频、超时、缺模型、合成失败及对齐边界；不能静默降级。

## 验证范围

服务、适配器、安装准备与 worker 的离线回归使用明确标注的测试音频和模拟模型，验证真实 HTTP、WAV、时长与失败处理；它们不证明 VoxCPM2 的听感。当前交付已准备独立依赖环境与四份模型元数据，主权重由用户下载；真实模型加载、旁白完整性、多语言发音和听感需在权重就位后另测。不得把上述接口测试写成真实 VoxCPM2 合成成功。
