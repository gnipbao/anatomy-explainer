# 项目文件与运行合约

基础脚本使用 Python 3.10+、Node 22+、FFmpeg/ffprobe、Chrome/Chromium，无 npm 或 pip 依赖。`local` 配音的 VoxCPM2 后端另用 Apple Silicon Mac 和独立 Python 3.12 / MLX 环境，见 `local-voice-engine.md`；macOS `say` 也是可选后端。

## 最小 job.json

参见 `examples/token-bucket/job.json`。字段：

- `schema_version: 1`、`topic`、BCP-47 风格 `language`、`audience`。
- `theme` 默认 `anatomy-official-light`，可选内置黑色 `anatomy-official-dark`；`topic_pack` 默认 `general-science`。一次构建使用一个主题，两套交付需分别构建和渲染。
- `content_ready: true` 表示代理完成研究、脚本和模型；初始化草稿为 false。
- `video: {width,height,fps}`：偶数尺寸 240–3840、整数帧率 1–60；新主题初始化为 1920×1080/30fps，令牌桶示例为 1280×720/24fps。木牛流马双主题验证采用 1920×1080/24fps，不改变初始化默认值。
- `tts: {provider,segment_mode?,voice?,resource_id?,local_voice?,endpoint?,timeout_seconds?}`；provider 为 doubao/local/say/external。新项目优先显式选择与本机偏好，未配置时为 doubao。`local_voice` 是本地注册音色 ID（默认 default），与豆包的 `voice` 分开；`endpoint` 默认 `http://127.0.0.1:3931/v1/audio/speech`，只接受 loopback HTTP 固定路径；本地请求 `timeout_seconds` 默认 600，允许 30–3600。新项目与示例在没有偏好覆盖时为 `segment_mode: "phrases"`，逐短句合成和测长；旧 job 省略此字段仍走场景音频兼容路径。
- `sources: [{id,title,url,supports?}]`；本地资料可用 `local:` 引用并在运行项目保留副本。
- `scenes: [{id,title,narration,caption,source_ids,hold_seconds?,hint?,presentation?}]`。id 唯一小写安全标识，不作为任意路径使用。`narration` 是讲稿，`caption` 是知识栏要点，二者不必重复。`hold_seconds` 为 0–30 秒，长稿应按概念拆场景。
- `presentation: {captions?,reduced_motion?,zoom?,focus?}`：新项目默认 `captions: {mode:"phrase",style:"cinema",placement:"bottom"}`。`mode` 可选 phrase/scene；`style` 可选 cinema/kinetic，省略仍为 cinema。`placement` 可选 auto/bottom/left/right/top，但只控制显式 kinetic 风格；cinema 始终全画幅底部居中。`keep_together` 可选，由最多 64 个非空短语组成，保护术语不被拆行；每项不超过 100 字符且不能含控制字符。`zoom` 为 1–1.12，`focus` 为两个 0–1 数值组成的 `[x,y]`。
- `model` 是当前题材的参数数据，由 `model.mjs` 解释，不能假设所有主题都是令牌桶。

场景可用 `scene.presentation: {caption_placement?,zoom?,focus?}` 覆盖镜头；`caption_placement` 仅用于显式 kinetic 风格。kinetic 下显式全局位置优先于场景位置，auto 才按场景和 cue 选择安全区，输出可能包含内部布局名 bottom-left。cinema 忽略这些换位选项；旧 job 即使写了 placement:auto，也不会让默认影院字幕移动。显式 `mode:"scene"` 保留整场 narration 和旧表现的兼容路径，不把整段讲稿挤进影院两行。

运行 `project.py check` 验证字段、来源引用和现有时轴。TTS 配置还由 `tts.py` 独立验证；机械检查无法证明科学事实正确。

## 初始化配音偏好

`project.py init` 对新主题及 `--example` 都读取可选文件 `~/.config/anatomy-explainer/preferences.json`，合约为 `{"schema_version":1,"tts":{"provider":"local","local_voice":"default","segment_mode":"phrases"}}`。`tts` 只允许这三个非敏感字段；不接受凭据、模型路径、参考音频路径或任意其他 job 字段。默认偏好文件缺失时保持公开模板，显式指定文件缺失或任一偏好文件无效时，初始化在创建目录前失败。

优先级为公开模板 < 本机偏好 < `--tts-provider`；用户本轮指令应通过显式选项或审查后的 job 落实。可用互斥的 `--preferences-file FILE` 指定另一份文件，或 `--ignore-preferences` 跳过偏好。读取只发生在初始化，`check`、`build` 和 `tts.py` 不重读偏好，也不重写已有 job。最终 provider 为 `external` 时，初始化固定采用 `scene` 分段；没有分段偏好时，其他服务保留模板的 `phrases`。

## 知识栏、字幕与主题

横版默认采用左侧知识栏和右侧等轴测主舞台；知识栏显示章节进度、当前标题与 `scene.caption` 要点。旁白字幕是独立的 cinema 图层：全画幅底部居中、白色无衬线常规字重、黑描边与轻阴影、透明背景。字幕按完整短句直接切换，不淡入、不位移、不逐字高亮；同一短句从头到尾位置和字号不变。横屏 1080p 基准为 42px，按成片分辨率等比缩放，不为了容纳长句自动缩小。单行与双行共享底线，最多两行；竖版有独立底部安全区，并避让来源行。

影院字幕的白字黑边不随浅色或黑色主题改变。中文每行最多 16 个全角等效字符，英文每行最多 42 字符；优先保持术语、单词与语义边界完整。规范化只作用于显示文本：中文逗号和句号改为空格，保留必要问号、感叹号及数值中的小数点等。TTS 原稿和原始 cue 文本保持原样。该预设参考 Netflix 简中与通用字幕指南，不表示已满足任意平台的完整交付规范。

`anatomy-official-light` 使用官方浅色基础且无 token 覆盖。`anatomy-official-dark` 使用官方 dark 基础，并在本包主题 JSON 中增强视频对比度：`--anatomy-mid: #96969B`、`--anatomy-lo: #626269`、`--anatomy-faint: #3E3E45`、`--anatomy-text: #BEBEC4`。不能把黑色方案称为完全未改官方色值；`vendor/anatomy` 仍按锁定哈希原样保留。主题只影响视觉，切换后重建与重渲染即可，不必重新合成同一音轨。

## 机制代码

```javascript
import {computeState} from './model.mjs';
export function mount(container, {kit, job}) {
  // 只在这里创建静态 SVG。kit 是固定版本的官方 iso-kit.mjs。
  return {
    frame({scene, progress, time, reducedMotion, setReadout}) {
      // progress 当前场景0..1，time全片秒数；调用顺序可任意。
      // 用纯模型重算部件、路径、受力和读数，并更新已存在的节点。
      // reducedMotion 是运行时传入的偏好，机制自行处理必要的简化。
      setReadout('由同一模型得出的读数');
    }
  };
}
```

额外几何模块必须使用运行项目内的相对导入，例如 `import * as G from './assets/kit/lathe.mjs'`。`project.py build` 已把官方 kit 复制到 assets/kit；不要从运行页面导入 Skill 安装目录或其上级路径，本地资源隔离会拒绝越界。固定入口是 mechanism.mjs 导出 mount，其他几何/状态放 model.mjs；先用真实 kit 做 mount smoke，再截帧，防止导出名错位。

`frame` 必须直接由本次绝对 `time` 或场景 `progress` 重算全部可见状态，顺序播放、反向跳转与随机 seek 应得到相同结果。用部件装配/拆解、正确世界轴转动、路径推进或受力与读数变化表达因果；单纯全图缩放、平移或淡入不足以代替机制运动。每个场景的变化对象、时间窗和解释边界应先写入 script.md。通用运行时提供知识栏、字幕和微镜头，题材专属机制仍需作者实现，初始化不会自动生成可信模型。

`runtime.mjs` 在 `window.__VIDEO_READY__=true` 后提供 `window.renderFrame(seconds)` / `window.seekVideo(seconds)`。帧渲染器逐帧传入时间，禁用 CSS 动画；模型不能依赖墙钟、未设种子的随机或上一帧累计值。`presentation.mjs` 是无 DOM 的确定性表现层；默认通过共享 `subtitles.mjs` 派生影院显示轨。`caption.cue` 与 `caption.source_text` 保留原始 cue 和文本，`caption.display_cue`、`start/end` 与 `start_frame/end_frame` 提供实际显示内容及帧边界，`caption.qc` 提供字幕检查结果。微镜头包裹机制输出而不覆盖部件变换；当前公开状态可从 `window.__PRESENTATION_STATE__` 读取。`index.html` 通过本地 HTTP 预览，ES module 不保证在 file:// 下可用。手动预览：在运行目录启动 `python3 -m http.server 8000 --bind 127.0.0.1`，浏览器打开 `http://127.0.0.1:8000/`，控制台可调用 `seekVideo(10)`。

## TTS 输出与变化检测

`phrases` 模式先按标点分出语义短句，再由 doubao/local/say 逐句真实合成、检查源音频正长度并归一化。中文尽量 12–26 字、上限 32，英文保持单词完整；不能用字数估算音长。每句 WAV 通过 ffprobe 测长，拼为 `audio/<id>.wav` 后再追加 `hold_seconds` 的真实静音，最后拼接 `narration-full.wav`。临时短句音频和文本不发布。仅切换 `tts.provider` 到 local 时保留豆包的音色字段；配置改变仍会改变 `job_fingerprint`，必须重新成功合成才可构建。

`timing.json` 保持原场景合约：`{schema_version:1,provider,language,alignment:'scene',duration,job_fingerprint,scenes:[{id,start,end,duration,audio}]}`。`audio` 为相对路径，场景连续；顶层 `alignment` 仍为 scene，不代表新增的字幕也是场景级。`phrases` 模式另加：

```json
{
  "captions": {
    "alignment": "measured-phrases",
    "cues": [
      {"id":"s01-p001","scene_id":"s01","start":0.0,"end":2.1,"text":"示例短句，时间须来自实际音频。"}
    ]
  }
}
```

上例只示意字段，不是可复用的真实配音数据。cue 使用全片绝对秒、唯一安全 id、所属场景、短文本和正时长，按时间排序、互不重叠且处于所属场景区间内。原始 `timing.captions` 不改写。默认影院显示轨把边界对齐到视频帧，并为相邻字幕留出 2 帧间隔：必要时只收短前条结束帧，不平移后条开头，不推断词级边界。显示区间采用半开规则 `[start,end)`，按全片显示轨查询，不能因原始场景尚未切换而丢失帧对齐后的字幕。若无法在保持正时长的同时留出间隔，会记录 `minimum-gap` 告警，须审查原时轴。`narration.srt` 与烧录字幕共用这份派生显示轨，cue 不覆盖场景末尾 hold；不是逐词、逐字识别或强制对齐。`job_fingerprint` 绑定 language、tts 和每段 id/narration/hold_seconds，旁白改变后 build 拒绝旧时轴。

`external` 只接受每场景一份真实音频并要求 `tts.segment_mode: "scene"`（可省略），不能直接选择 phrases。若需人工字幕，可在审核后提供 `timing.captions.alignment: "manual"` 与真实 cues；TTS 脚本不会从完整录音推断这些边界。没有 cues 时，表现层会按实测场景区间推估短句并明确标记 `estimated-phrases`，需人工核对；它不等于实测短句。缺少实测或人工 cues 时，不能声称已经获得与烧录逐帧相同的影院 SRT。要完整保留旧整场字幕行为，设置 `presentation.captions.mode: "scene"`，这时 SRT 和画面整句覆盖场景与停顿。若只保留旧短句换位与淡入，显式设置 `style:"kinetic"`。

## 构建与输出

`project.py build` 复制固定 kit 与许可，写 data.json、runtime.mjs、presentation.mjs、subtitles.mjs、video.css、index.html。cinema 模式且存在真实或人工 cues 时，通过 Python `subtitle_tools.py` 桥接同一个 JS 字幕引擎，同时刷新 `narration.srt`、`cinematic.srt` 与 `subtitle-qc.json`，不会改写原始 timing。自定义主题传 `--theme-file FILE`。构建最后写 build-manifest.json，绑定 job/timing/data 原始字节哈希；渲染前发现变化就拒绝旧构建。完整渲染另核对输入旁白实测长度与时轴，误差不得超过0.01秒。已有构建产物需显式 `--force`；不覆盖 job/model/mechanism/research。

`render.mjs` 只提供运行项目资源，逐帧截图经 FFmpeg 编码为 H.264/AAC MP4。时长取音频并向上对齐到完整视频帧，尾端补音频静音；不能按估算字数截断旁白。`--frame N --still PATH` 单帧审看；`--out PATH` 指定成片。影院排版的 `line-overflow` 为 error，阻断构建和渲染；应调整讲稿或审核原始 cue 的分段，不能自动压字或伪造时间。时长与阅读速度超出预设的告警保留在 `subtitle-qc.json`，需要审看：每条建议 5/6–7 秒，中文不超过每秒 9 个全角等效字符、英文不超过每秒 20 字符。渲染器还检查可见字幕越界、溢出、标题/来源交叠与标题裁切，在报告记录 alignment、观测到的 cue 数、位置与影院检查数据；这不替代机制语义、图形可读性或完整人工试听。`render-report.json` 的编码与边界数据不是科学审稿或官方几何 audit 证明。
