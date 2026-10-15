# 项目文件与运行合约

包内脚本使用 Python 3.10+、Node 22+、FFmpeg/ffprobe、Chrome/Chromium。无 npm 或 pip 依赖；macOS `say` 为可选本地配音，不是跨平台必备。

## 最小 job.json

参见 `examples/token-bucket/job.json`。字段：

- `schema_version: 1`、`topic`、BCP-47 风格 `language`、`audience`。
- `theme` 默认 `anatomy-official-light`，可选内置黑色 `anatomy-official-dark`；`topic_pack` 默认 `general-science`。一次构建使用一个主题，两套交付需分别构建和渲染。
- `content_ready: true` 表示代理完成研究、脚本和模型；初始化草稿为 false。
- `video: {width,height,fps}`：偶数尺寸 240–3840、整数帧率 1–60；新主题初始化为 1920×1080/30fps，令牌桶示例为 1280×720/24fps。木牛流马双主题验证采用 1920×1080/24fps，不改变初始化默认值。
- `tts: {provider,segment_mode?,voice?,resource_id?}`；provider 为 doubao/say/external。新项目与示例默认 `segment_mode: "phrases"`，逐短句合成和测长；旧 job 省略此字段仍走场景音频兼容路径。
- `sources: [{id,title,url,supports?}]`；本地资料可用 `local:` 引用并在运行项目保留副本。
- `scenes: [{id,title,narration,caption,source_ids,hold_seconds?,hint?,presentation?}]`。id 唯一小写安全标识，不作为任意路径使用。`narration` 是讲稿，`caption` 是知识栏要点，二者不必重复。`hold_seconds` 为 0–30 秒，长稿应按概念拆场景。
- `presentation: {captions?,reduced_motion?,zoom?,focus?}`：新项目默认 `captions: {mode:"phrase",placement:"auto"}`。`mode` 可选 phrase/scene，显式 scene 才把整场 narration 作为一条字幕；`placement` 可选 auto/bottom/left/right/top。`zoom` 为 1–1.12，`focus` 为两个 0–1 数值组成的 `[x,y]`。
- `model` 是当前题材的参数数据，由 `model.mjs` 解释，不能假设所有主题都是令牌桶。

场景可用 `scene.presentation: {caption_placement?,zoom?,focus?}` 覆盖镜头和字幕位置；显式的全局字幕位置优先于场景位置，auto 才按场景和 cue 选择安全区。auto 输出可能包含内部布局名 bottom-left，输入枚举仍以上述五项为准。字幕位置在短句之间切换，同一句阅读期间保持不动。

运行 `project.py check` 验证字段、来源引用和现有时轴。TTS 配置还由 `tts.py` 独立验证；机械检查无法证明科学事实正确。

## 知识栏、字幕与主题

横版默认采用左侧知识栏和右侧等轴测主舞台；知识栏显示章节进度、当前标题与 `scene.caption` 要点。旁白字幕是独立图层，按当前 cue 显示、换行和入退场，不把整段讲稿常驻在固定底栏。竖版使用上方知识栏与下方主舞台；不应沿用横版尺寸假设。

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

`runtime.mjs` 在 `window.__VIDEO_READY__=true` 后提供 `window.renderFrame(seconds)` / `window.seekVideo(seconds)`。帧渲染器逐帧传入时间，禁用 CSS 动画；模型不能依赖墙钟、未设种子的随机或上一帧累计值。`presentation.mjs` 是无 DOM 的纯函数表现层，保留 supplied cue 的文字与时间，微镜头包裹机制输出而不覆盖部件变换；当前公开状态可从 `window.__PRESENTATION_STATE__` 读取。`index.html` 通过本地 HTTP 预览，ES module 不保证在 file:// 下可用。手动预览：在运行目录启动 `python3 -m http.server 8000 --bind 127.0.0.1`，浏览器打开 `http://127.0.0.1:8000/`，控制台可调用 `seekVideo(10)`。

## TTS 输出与变化检测

`phrases` 模式先按标点分出语义短句，再由 doubao/say 逐句真实合成、检查源音频正长度并归一化。中文尽量 12–26 字、上限 32，英文保持单词完整；不能用字数估算音长。每句 WAV 通过 ffprobe 测长，拼为 `audio/<id>.wav` 后再追加 `hold_seconds` 的真实静音，最后拼接 `narration-full.wav`。临时短句音频和文本不发布。

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

上例只示意字段，不是可复用的真实配音数据。cue 使用全片绝对秒、唯一安全 id、所属场景、短文本和正时长，按时间排序、互不重叠且处于所属场景区间内。`narration.srt` 逐短句输出，cue 不覆盖场景末尾 hold；不是逐词、逐字识别或强制对齐。`job_fingerprint` 绑定 language、tts 和每段 id/narration/hold_seconds，旁白改变后 build 拒绝旧时轴。

`external` 只接受每场景一份真实音频并要求 `tts.segment_mode: "scene"`（可省略），不能直接选择 phrases。若需人工字幕，可在审核后提供 `timing.captions.alignment: "manual"` 与真实 cues；TTS 脚本不会从完整录音推断这些边界。没有 cues 时，表现层会按实测场景区间推估短句并明确标记 `estimated-phrases`，需人工核对；它不等于实测短句。要完整保留旧字幕行为，还需设置 `presentation.captions.mode: "scene"`，这时 SRT 和画面整句覆盖场景与停顿。

## 构建与输出

`project.py build` 复制固定 kit 与许可，写 data.json、runtime.mjs、presentation.mjs、video.css、index.html。自定义主题传 `--theme-file FILE`。构建最后写 build-manifest.json，绑定 job/timing/data 原始字节哈希；渲染前发现变化就拒绝旧构建。完整渲染另核对输入旁白实测长度与时轴，误差不得超过0.01秒。已有构建产物需显式 `--force`；不覆盖 job/model/mechanism/research。

`render.mjs` 只提供运行项目资源，逐帧截图经 FFmpeg 编码为 H.264/AAC MP4。时长取音频并向上对齐到完整视频帧，尾端补音频静音；不能按估算字数截断旁白。`--frame N --still PATH` 单帧审看；`--out PATH` 指定成片。渲染器检查可见字幕越界、溢出、标题/来源交叠与标题裁切，在报告记录 alignment、观测到的 cue 数与位置；这不替代机制语义、图形可读性或完整人工试听。`render-report.json` 的编码与边界数据不是科学审稿或官方几何 audit 证明。
