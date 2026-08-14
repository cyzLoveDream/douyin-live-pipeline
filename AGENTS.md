# AGENTS.md — 给其他 agent 的运行说明书

本仓库是可移植的抖音**直播切片**流水线（不是影视解说整片）。包名 `dylive`。不要去逆向 `a_bogus`、不要破解验证码、不要读取用户操作系统里的浏览器 cookie 或密码。

## 输入

- 直播 URL：`https://live.douyin.com/<web_rid>` 或 `https://v.douyin.com/<code>/`。
- 配置：`config.yaml`（从 `config.example.yaml` 复制）。`--config` 或 `DYLIVE_CONFIG`。
- 网络：必须能打开 Douyin。海外设 `HTTPS_PROXY`。
- 转写：`faster-whisper` 是硬依赖。测试必须注入 FakeTranscriber 或 fixture `transcript.json`，禁止下载模型。

## 阶段命令（按顺序）

```text
dylive watch  <url>
dylive record <url>
dylive transcribe [path|room]   # 必做。data/jobs/<id>/transcript.json（segments + words）
dylive detect [path|room]       # 缺转写会先 transcribe；写出 highlights.json（含 why）
dylive edit   [path|room]       # 强制烧字幕；写出 timeline.json + 成片 + 剪映旁路
dylive compile [room]           # xfade 合成 <room>_pack.mp4
dylive publish [--dry-run]
dylive run <url> --dry-run      # 以上全部（仍跳过发布）
```

状态文件：

- `data/jobs/<id>/watch.json` `record.json` `transcript.json` `highlights.json` `timeline.json` `edit.json`
- `recordings/<id>/manifest.json`（原始分段，成片只引用 in/out，不改写）

## 字幕样式

`edit.caption_style`：`hormozi` | `douyin`（默认）| `standard`。调用 `dylive.captions.build_ass` / `write_ass`。缺中文字体要提示安装 `fonts-noto-cjk`。

## 特效预设

`edit.style`：`douyin_hot`（默认）| `clean` | `party`。库在 `src/dylive/effects.py`：`zoom_in`/`zoom_out`/`pan`/`punch_zoom`/`shake`/`flash`/`fade`/`caption_mask`，带 ease-in-out。时间轴特效是参数，不是改源文件。

## 时间轴

`timeline.json` 的 tracks：`video` / `caption` / `effect` / `overlay` / `audio`。video clip = `{src, in, out, effects[]}`。`dylive.timeline.build_clip_timeline` 给其他 agent 用。

## 何时必须找人

1. 登录过期 → `dylive login`
2. 页面出现扫码/验证码/短信 → 停下让人完成
3. NeedAccessError → cookies / 大陆网络 / 代理
4. 创作者中心 DOM 对不上 → headed 模式留下窗口

不要：签名算法、滑块、协议破解；提交 `.env` / `cookies.txt` / `data/browser-profile/`；默认搬运别人的直播。

## 实现约束

- 直播状态只从公开 HTML 解析。不要新增需 `a_bogus` 的 webcast enter。
- 上传只走官方创作者中心页。
- 测试用 ffmpeg 小夹具；禁止提交大媒体；禁止在测试里加载 whisper 模型。`pytest` 要绿。
- 不要 vendoring NarratoAI 的剪映草稿生成器；剪映导出只给 clips + srt + IMPORT.md。
- 不要引入 Coze / DashScope 强依赖。LLM 文案无 key 时降级为启发式。
