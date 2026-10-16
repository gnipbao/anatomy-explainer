# Anatomy 科普视频 Skill

给一个主题，先用 **grill-me / grilling** 分轮审题，锁定受众和核心解释，再交付 **Anatomy 等轴测机制画面 + 旁白 + 电影式字幕 + MP4**。默认中文、官方浅色主题、豆包 Seed TTS 2.0；另有官方 dark base 加视频对比度增强的黑色主题，支持自定义视觉主题、科普题材插件、本地多语言语音和其他服务音频导入。

这是在 [wheresryan22/anatomy](https://github.com/wheresryan22/anatomy) 之上新建的视频工作流包。官方几何 kit 原样保留，固定版本与文件哈希见 `upstream.lock.json`。不是 Anatomy 官方的视频产品。

## 第一次使用

把 `SKILL.md` 作为 Skill 交给代理，例如：

> 使用 anatomy-explainer。主题是“空气净化器怎么过滤颗粒”。结合 grill-me 确定受众与解释范围，中文，60秒，保留 Anatomy 官方浅色风格，豆包配音。其他制作细节你推荐，我确认后开始。

已明确参数可直接提供，也可以说“其余采用默认，你决定”，减少审题轮次。grill-me 的问题针对用户的制作选择，科学事实和环境检查由代理完成。集成精确名称是 **grill-me**。

未安装 grill-me 时，Skill 有独立审题合约；装有外部版本则读取确认后调用，不依赖虚构的 JSON 导入接口。

## 环境与安装

Python 3.10+、Node 22+、Chrome/Chromium、FFmpeg 和 ffprobe；基础流程不需要 npm/pip 包。macOS `say` 是可选本地配音后端，Windows/Linux 可使用豆包或导入真实音频。工具未装时不自动修改机器环境。

克隆仓库后验证并安装：

```bash
git clone https://github.com/gnipbao/anatomy-explainer.git
cd anatomy-explainer
python3 scripts/verify.py
python3 scripts/install.py --target "$HOME/.agents/skills/anatomy-explainer" --dry-run
python3 scripts/install.py --target "$HOME/.agents/skills/anatomy-explainer"
```

安装器只接受不存在的目标，拒绝覆盖。`~/.agents/skills/` 是上游明确支持的 Codex Skill 位置；也可显式选择自己的 Skill 目录。安装后如未发现，重新打开会话。当前交付不代表已经全局安装。

## 配音配置

默认 `job.tts.provider = "doubao"`。在运行环境安全配置 `DOUBAO_API_KEY`；旧控制台使用 `DOUBAO_APP_ID` + `DOUBAO_ACCESS_KEY`。不要把值发给代理或写进项目文件。默认音色 `zh_female_vv_uranus_bigtts` 与资源 `seed-tts-2.0`；其他音色/资源在 job 中显式配置并核对控制台支持。

新建项目和令牌桶样例默认 `tts.segment_mode: "phrases"`：先拆语义短句，每句分别合成、归一化并实测音频长度，再合并为场景音轨。`timing.json` 保存 `captions.alignment: "measured-phrases"` 和每句的实测边界，电影式 SRT 每句一条，按帧对齐并为连续句保留两帧间隔；场景末尾的 `hold_seconds` 不延长字幕。它不是逐词或逐字对齐。旧 job 未写 `segment_mode` 时仍走 `scene` 路径；不要把按字数估算的显示时间称为实测。

`language` 不是翻译器。最终讲稿、字幕和问题应先写成目标语言；非中文豆包必须显式给出支持该语言的 voice。本地 `say` 从已安装语音中按语言匹配，可指定 voice。外部服务把每场景的真实音频放入 `audio-input/`，并使用 `tts.segment_mode: "scene"`；`external` 不支持自动实测短句边界。完整说明见 `references/tts.md`。

## 跑通完整样例

以下在包目录执行。默认豆包会发起真实合成请求，按服务计费；先 dry-run 只检查结构。

```bash
python3 scripts/project.py init --project /tmp/anatomy-demo --example token-bucket
python3 scripts/project.py check --project /tmp/anatomy-demo
python3 scripts/tts.py --project /tmp/anatomy-demo --dry-run
python3 scripts/tts.py --project /tmp/anatomy-demo
python3 scripts/project.py build --project /tmp/anatomy-demo
node scripts/render.mjs --project /tmp/anatomy-demo --frame 0 --still /tmp/anatomy-demo/preview.png
node scripts/render.mjs --project /tmp/anatomy-demo
```

无云凭据时，可主动将示例 job 的 tts 改为 `{"provider":"say","segment_mode":"phrases"}`，以本机语言音色验证。沙箱可能阻断 macOS 语音服务或 Chrome；只为相应本地验证申请必要权限，不放宽全局设置。空语音会失败，不能将静音充当成功配音。

样例仅演示令牌桶机制，参数为容量4、每秒补1、每请求耗1；依据 RFC 3290，使用按请求拒绝的教学简化。新主题执行 `project.py init --topic "主题"` 后是草稿，需要代理研究并编写专属 model/mechanism，不是自动套壳。

`brief.json` 示例未伪造真人确认。实际生产先完成审题，把真实用户决定或明确委托写入 brief，再把知识与脚本审核后的 job.content_ready 设 true。

## 主题插件

一次 `init` 选择一个主题，默认 `anatomy-official-light`；新项目可用 `--theme anatomy-official-dark` 选黑色。浅色主题不覆盖官方颜色；黑色主题使用官方 `dark` base，并覆盖四个颜色 token 提高视频对比度，详见 `references/plugins.md`。官方几何 kit 与 vendor CSS 原样保留，增强发生在主题数据层。

需要同片双主题时，准备两份内容、机制、实测音频和时轴相同的运行项目，分别设置 `job.theme`，各自执行 `build`、预览和 `render`。主题变化不需要重新配音。脚本不会一次初始化或自动双导出；两份输出和渲染报告应分别保存。已审阅且明确用于本轮重建的目标可使用 `--force`。

新增视觉主题参考 `themes/custom-example.json`，构建传 `--theme-file FILE`。新增题材模板参考 `topic-packs/general-science.json`，check/build 传 `--topic-pack-file FILE`。插件都是经过校验的数据，不能植入脚本或凭据。题材模板约束研究问题与讲解顺序，事实仍需独立来源。

## 画面布局与机制动作

横屏采用左侧知识栏、右侧机制主舞台：知识栏显示章节、标题与场景知识点，短句字幕独立显示。默认 `presentation.captions: {"mode":"phrase","style":"cinema","placement":"bottom"}`：全画幅底部居中，白色无衬线字配黑描边，透明背景；最多两行、固定字号、共同底线，直接切换。中文字幕按16全角等效字符/行处理，中文逗号与句号仅在显示中改为空格。字幕不跟随模型移动，也不做淡入、飞入或缩放。更多规则和来源见 `references/subtitles.md`。旧换位字幕仅在显式 `style:"kinetic"` 时使用；竖屏仍需审看安全区。

机制由 `mount` 返回的 `frame({scene, progress, time, setReadout})` 根据本次绝对时间和场景进度重算。部件位移、轮转、传力或因果状态变化应由机制代码实现，镜头轻推和知识栏入场只负责呈现。运行时不会替自定义机制生成动作；验收应包括阶段差异、倒放和随机 seek 后的状态复现。

## 产物与验证范围

运行项目保存 brief、研究、讲稿、场景参数、机制代码、逐场景/完整 WAV、SRT、实测时轴、HTML 预览、H.264/AAC MP4 与渲染报告。短句模式的原始时间保留在 timing.json；电影式 SRT 与烧录字幕共同使用帧对齐后的显示时间、断行和文案，旧场景模式仍为每场景一条；两者都不是字级识别。帧按照绝对时间重算，真实音频长度驱动场景。

v0.2 已有同内容双主题样片的验证记录：1920×1080、24 fps、1551 帧、64.625 秒、17 条实测短句。增强对比度后的黑色版本也已完成关键帧审看与完整重渲染。每次主题调整仍需用对应的新截图、MP4 和渲染报告复核。运行视频和用户参考资料不随源码发布。

构建输出 `cinematic.srt`、同内容的 `narration.srt` 与 `subtitle-qc.json`；两行放不下会阻断，显示时长和阅读速度超出预设会记录告警，不自动压小字号或杜撰新切点。

本包检查包含上游哈希、项目与主题合约、模型算术、豆包流解析、音频归一化和渲染边界。真实验证与未验证项见 `references/sources.md`。测试通过不证明科学结论或观众学习效果，也不代表云端账户已配置。

本包采用 [MIT](LICENSE)，第三方归属见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；官方上游许可在 `vendor/anatomy/LICENSE`。Skill 不自动发布、推送、画廊投稿或全局安装。
