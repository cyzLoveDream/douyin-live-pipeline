# douyin-live-pipeline

把一条抖音直播链接变成竖屏高能切片：转写 → 多信号高能检测 → 词级字幕 + 抖音风特效 → 本地客户端预览 → 可选剪映草稿 / 发布。换机器 `git clone` 后按本文安装即可跑。

包名 **dylive**，命令行入口也是 `dylive`。当前版本 **0.5.0**。

这是 **直播切片流水线**，不是影视解说整片工具。口播转写、高能窗、成片字幕和特效是一等公民，不是可选项。

---

## 你必须知道的限制

1. **网络**：运行环境必须能打开 `live.douyin.com` 和 `creator.douyin.com`。在海外通常需要 `HTTP_PROXY` / `HTTPS_PROXY`。本工具**不会**绕过验证码、签名（`a_bogus`）或反爬。
2. **版权 / 平台条款**：转载、二次分发**别人的直播**可能侵犯著作权并违反抖音用户协议。默认请只处理**你自己的直播间**。`edit.source_caption` 默认开，字幕形如 `来源 live.douyin.com/<房间id>`。
3. **登录**：发布和很多直播页都需要登录。第一次跑 `dylive login`，用抖音 App 扫码。
4. **yt-dlp 现状（2026）**：官方 extractor 不管 Douyin 直播。流水线解析公开直播页 HTML 里的 `RENDER_DATA` / `hls_pull_url` / `flv_pull_url`，再交给 yt-dlp 或 ffmpeg。
5. **CPU whisper**：默认 `small` + `int8`。长录像会慢；可在配置里改 `transcribe.model`（`tiny`/`base` 更快更糙）。测试**不会**下载模型。
6. **字体**：烧录中文字幕需要 Noto Sans CJK / 思源黑体 / 文泉驿 / PingFang。缺字体时会明确提示 `sudo apt install fonts-noto-cjk`。
7. **剪映没有官方 API**。本项目用 pyJianYingDraft 写新草稿给剪映专业版打开。不解密剪映 7+ 旧草稿，也不做 Windows 界面连点导出。

---

## 依赖

| 类型 | 内容 |
| --- | --- |
| Python | 3.11+ |
| 系统 | **ffmpeg** + ffprobe + 中文字体（`fonts-noto-cjk`） |
| Python 包 | `yt-dlp` `playwright` `pyyaml` `typer` `httpx` `numpy` **`faster-whisper`** `fastapi` `uvicorn`（硬依赖） |

```bash
git clone https://github.com/cyzLoveDream/douyin-live-pipeline.git
cd douyin-live-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# 可选：pip install -e ".[jianying]"   # pyJianYingDraft，写剪映草稿
playwright install chromium

sudo apt install -y ffmpeg fonts-noto-cjk     # Debian/Ubuntu
# brew install ffmpeg                         # macOS（自带 PingFang）
```

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

---

## 本地客户端

dylive ui 启动本地工作室。打开 http://127.0.0.1:8787 查看成片。run 结束会打印该地址。

## 全自动（无人值守 + DeepSeek 导演）

`dylive auto "<直播间链接>"` 一条命令跑完整个流水线，无需人工操作：

```bash
dylive auto "https://live.douyin.com/<web_rid>"
```

看播 → 录制 → 转写 → 高能检测 → **DeepSeek 导演决策** → 剪辑 → 发布，全程自动。你只需事后看 `output/clips/` 里的成片。

**DeepSeek 导演决策**（`dylive director`）：把每条高能片段的客观信号（能量/频谱/口播/切镜/关键词/弹幕 + 转写原文）喂给大模型，让它判断最优剪辑方案：

| 决策项 | 说明 |
| --- | --- |
| style | 从 douyin_hot / party / clean / cinematic / vlog 中选 |
| effects | punch / shake / glitch / jumpcut / keyword_pop / progress / vignette / grain 开关 |
| 文案 | 标题、开场钩子、话题标签、发布文案 |
| reason | 一句话解释为什么这样剪（可查看 AI 剪辑思路） |

决策结果写 `data/jobs/<room>/director.json`。无 LLM key 时降级为启发式规则，绝不失败。

**大模型配置**（`.env`，OpenAI 兼容，默认 DeepSeek）：

```bash
DYLIVE_LLM_API_KEY=sk-xxx            # DeepSeek
# DYLIVE_LLM_BASE_URL=https://api.deepseek.com
# DYLIVE_LLM_MODEL=deepseek-v4-pro    # 推理型，判断最优特效
# DYLIVE_LLM_FAST_MODEL=deepseek-v4-flash
```

**可选多模态**（豆包 Vision，抽帧选封面）：配置 `DYLIVE_VISION_API_KEY` 后自动启用；不配则跳过。

```bash
# DYLIVE_VISION_API_KEY=xxx
# DYLIVE_VISION_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
# DYLIVE_VISION_MODEL=doubao-seed-2-1-pro-260628
```

> **豆包 Vision 开通**（正确模型名是 `doubao-seed-2-1-pro-260628`，旧名 `doubao-1.5-vision-pro` 已下线）：
> 1. 火山方舟控制台 https://console.volcengine.com/ark → 左侧「开通管理」（需先实名认证）
> 2. 「视觉大模型」分类 → 找到 **Doubao-Seed-2.1** → 点「开通」→ 勾选协议确认
> 3. 开通后即可调用；更快更便宜的备选：`doubao-seed-2-1-turbo-260628`
>
> 常见报错：`InvalidEndpointOrModel.NotFound` = 模型名写错；`ModelNotOpen` = 模型名对但没开通，去控制台开通即可。

**发布**：首次 `dylive login` 扫码一次，之后用持久化 cookie 全自动上传，无需再人工。

## 命令

```bash
dylive --help
dylive auto "https://live.douyin.com/<web_rid>"      # 全自动：看播→剪辑→发布（DeepSeek 导演）
dylive -c config.yaml run "https://live.douyin.com/<web_rid>" --dry-run
dylive ui          # 打开 http://127.0.0.1:8787 查看成片
```

分阶段：

```bash
dylive watch "https://live.douyin.com/745964462470"
dylive record "https://live.douyin.com/745964462470" --max-seconds 120

# 转写是必做阶段（词级时间戳）。检测和烧字幕都依赖它。
dylive transcribe
dylive transcribe recordings/745964462470

# 多信号高能检测（缺 transcript.json 会先 transcribe）
dylive detect

# DeepSeek 导演决策：判断每条高能片段的最优特效/风格/文案
dylive director

# 二次剪辑：9:16 + 特效预设 + 强制烧录词级字幕；写出 timeline.json 和剪映旁路目录
dylive edit --title "今晚高能" --room-id 745964462470

# 把成片 xfade 合成竖屏合集
dylive compile

dylive jianying              # pyJianYingDraft 草稿，需 pip install -e ".[jianying]"

dylive publish --dry-run
dylive login
```

`--dry-run` **只跳过发布**。

全流程：`watch → record → transcribe → detect → create(二次创作) → director(DeepSeek 导演决策) → edit → compile → publish`。

---

## 高能检测（不是只看 RMS）

每一窗打分写进 `data/jobs/<room>/highlights.json` 的 `why`：

| 信号 | 做法 |
| --- | --- |
| energy | 200–250ms hop 的 RMS **z-score** |
| flux | 帧间频谱通量（numpy FFT），抓「突然变热闹」 |
| speech | 能量 VAD；死空气接近 0（关键词/弹幕仍计分） |
| scene | ffmpeg `select=gt(scene,…)` |
| keywords | 口播命中配置里的中文高能/带货词（卧槽/绝了/买它/秒杀/家人们…） |
| chat | 仅当本地已有 JSON 时加权。不爬私有 webcast API |

加权全在 `config.yaml` 的 `detect.weights`。窗口吸附到 whisper 词/句边界（±0.15–0.4s），合并近窗，夹在 **8–45 秒**，取 `max_clips`。

有 API key（`OPENAI_API_KEY` 或 `DYLIVE_LLM_API_KEY`，可选 `DYLIVE_LLM_BASE_URL`）时，会给每段写抖音标题 / 3 个话题 / hook；**没 key 就用口播首句启发式，流水线不会失败**。

---

## 二次创作（不是简单切片）

`dylive create` 在检测之后、剪辑之前运行，产出 `data/jobs/<room>/create.json`：

| 能力 | 说明 |
| --- | --- |
| 文案改写 | LLM 把口播改写为≤18字抖音文案；无 key 用口播首句 |
| 开场钩子 | 花字钩子（用原声，不配音） |
| 结尾 CTA | 「关注主播」引导花字（config 可自定义） |
| 解说稿 + 配音 | edge-tts 合成旁白混入成片（可选 `.[voice]`，缺库自动跳过） |
| 剪口播 | 去掉「嗯/那个/就是」等语气词，字幕更干净 |
| 多版本 | `create.versions: [douyin_hot, cinematic]` 一个切片切多个风格 |

无 LLM key 时全部降级为启发式，绝不失败。

## 字幕（每条成片必须有）

- 用词级时间戳生成 **ASS**（不是整段一锅 SRT），再 `ffmpeg subtitles=` 烧进画面。
- 同时在 mp4 旁边写 `.ass`，剪映导出里还有 `.srt`。
- 样式 `edit.caption_style`（默认 `douyin`）：
  - `hormozi`：画面中央超大字，当前词弹出、其余变淡
  - `douyin`：下部 2–3 词，当前词黄白高亮 + 描边
  - `standard`：底栏衬底框
- 没有词级字幕会报错，请先 `dylive transcribe`。成片没字幕视为 bug。

默认预设还会在字幕后面加一条 **底部约 16% 高的半透明遮罩**（口播底栏），避免字叠在花衬衫上。

---

## 特效预设（ffmpeg，不是 trim+scale）

`edit.style`：

| 预设 | 观感 |
| --- | --- |
| **`douyin_hot`（默认）** | 9:16 模糊填充、loudnorm、douyin/hormozi 字幕、口播首句 hook、能量峰 **punch**、淡入、质感对比、暗角、轻度颗粒、底栏遮罩、进度条、来源字幕 |
| `clean` | 裁切 9:16、standard 字幕、loudnorm、来源字幕，不 punch |
| `party` | douyin_hot + 峰值微抖 + glitch/RGB分裂 + 静音略加速 + 关键词弹出 |
| `cinematic` | 裁切 9:16、standard 字幕、电影感对比 + 暗角 + 颗粒、淡入，不 punch |
| `vlog` | 9:16 模糊填充、douyin 字幕、饱和度 + 颗粒 + 进度条、口播 hook + punch |

效果库在 `src/dylive/effects.py`：zoom_in/out, pan, punch_zoom, shake, flash, fade, caption_mask, progress_bar, saturation, vignette, grain, glitch, rgb_split, contrast, freeze, speed_ramp, mirror。图编译失败会降级，但默认预设仍应产出「看起来剪过」的文件。

可选本地 BGM：`edit.bgm` 指向 `assets/bgm/` 里你自己的循环乐，口播用 `sidechaincompress` duck；文件不存在就跳过。

---

## 多轨道时间轴

每次 job 写 `data/jobs/<room>/timeline.json`。成片**不改写原始录像**，视频轨只用 `src` + `in` / `out` 指向原片，最后编译成 ffmpeg。轨类型：`video` / `caption` / `effect` / `overlay` / `audio`。

`dylive compile` 把已导出的竖屏成片用 xfade（edit.xfade：fade/fadeblack/wipeleft/slideleft/circlecrop，默认 fadeblack 0.25s）合成 `output/clips/<room>_pack.mp4`。

---

## 剪映草稿（没有官方 API）

剪映 / CapCut **没有官方开放 API**。本项目用 pyJianYingDraft（pip install -e ".[jianying]"）写新草稿，给「剪映专业版」打开。不解密剪映 7+ 旧草稿，也不做界面连点自动导出。

    dylive jianying [room]

写出 output/jianying/<room>/draft/（draft_content.json 等）并复制媒体。用剪映专业版打开该草稿目录（设置里把草稿位置指过来，或把文件夹拷进剪映草稿目录）。Windows 可直接用；macOS/Linux 生成后仍需在剪映里打开。缺库时会提示 pip install 'dylive[jianying]'。

旁路目录仍有 clip_*.mp4 + srt，可直接拖进剪映。

### 特效对照（ffmpeg 预览 vs 剪映草稿）

fade=叠化；fadeblack=闪黑；wipeleft=向左擦除；slideleft=左移；circlecrop=圆形遮罩；punch_zoom=斜切（入场）；shake=抖动；flash=闪白；glitch=故障；rgb_split=色差故障；grain=胶片；vignette=暗角；contrast=质感；saturation=原生肤。


---

## 目录

```
recordings/<room>/          # 分段录像（不改写）
output/clips/               # 成片 + <room>_pack.mp4
output/jianying/<room>/     # 旁路 mp4/srt
output/jianying/<room>/draft/  # pyJianYingDraft 草稿
src/dylive/web/             # 本地客户端 SPA
data/jobs/<room>/           # watch/record/transcript/highlights/timeline/edit.json
assets/bgm/                 # 可选本地配乐（不要提交大 mp3）
```

---

## 测试

```bash
pytest
```

用 ffmpeg 生成很小的 wav/mp4。转写测试注入 FakeTranscriber / fixture JSON，**不下载 whisper 模型**。UI 用 TestClient。剪映草稿单测用假 writer。

---

## 参考（能力，不是搬代码）

流水线仍是「直播高能切片」，但成片观感参考了这三家的**能力**：

- [linyqh/NarratoAI](https://github.com/linyqh/NarratoAI) — 口播/高光 + 成片字幕底栏遮罩 + 本地 BGM 混音；导出给剪映用旁路目录，而不是 vendoring 他们的草稿生成器。
- [VienLi/CcClip](https://github.com/VienLi/CcClip) — 多轨道时间轴（视频/音频/字幕/特效），不切割源文件，只用 in/out 指向原片，最后编译 ffmpeg。
- [LumingMelody/Ai-movie-clip](https://github.com/LumingMelody/Ai-movie-clip) — zoom in/out、pan、easing、抖音风模板、转场；自然语言标题可选 LLM。不引入 Coze 数字人或 DashScope 强依赖。

另外：Hormozi / 冲击字幕、词级时间戳切点吸附、多信号高能（能量+口播关键词+切镜，而不是只看 RMS）是这类短视频工作流的常见做法。

---

## 故障

| 现象 | 处理 |
| --- | --- |
| `无法访问抖音` / 验证码墙 | `dylive login` 或放入新鲜 `cookies.txt`；确认代理能打开直播页 |
| 转写很慢 | CPU 上 `small` 模型就是慢；改 `transcribe.model: base` 或更小 |
| 找不到中文字体 | `sudo apt install fonts-noto-cjk` |
| 成片报「没有词级字幕」 | 先 `dylive transcribe` |
| BGM 没混上 | 检查 `edit.bgm` 路径；缺文件会跳过而不是崩 |
| 没 LLM 标题 | 没 key 时用口播首句，属预期 |
| 导出剪映草稿提示缺库 | pip install 'dylive[jianying]' |
| 剪映 7+ 打不开旧草稿 | 预期：只写新草稿，不解密 |
| 想让剪映自动导出成片 | 不支持（剪映 7+ 无自动导出） |

许可证：MIT。
