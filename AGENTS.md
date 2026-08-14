# AGENTS.md — 给其他 agent 的运行说明书

本仓库是可移植的抖音直播切片流水线。包名 `dylive`。不要去逆向 `a_bogus`、不要破解验证码、不要读取用户操作系统里的浏览器 cookie 或密码。

## 输入

- 直播 URL：`https://live.douyin.com/<web_rid>` 或 `https://v.douyin.com/<code>/` 分享短链。
- 配置：仓库根目录 `config.yaml`（从 `config.example.yaml` 复制）。可用 `--config` 或 `DYLIVE_CONFIG`。
- 网络：必须能打开 Douyin。海外设 `HTTPS_PROXY`。
- 登录：发布前、以及直播页被墙时，需要人扫码。

## 阶段命令（按顺序）

在仓库根、已 `pip install -e .` 且 `PATH` 里有 `ffmpeg` 的环境执行：

```text
dylive watch  <url>          # 解析 web_rid，轮询直到 LIVE（--once 只查一次）
dylive record <url>          # 写入 recordings/<id>/ 分段文件；断流会按 resume_gap 续录
dylive detect [path|room]    # 读录像，写 data/jobs/<id>/highlights.json
dylive edit   [path|room]    # 读 highlights.json，写 output/clips/*.mp4
dylive publish [--dry-run]   # Playwright 打开创作者上传页；dry-run 则跳过
dylive run <url> --dry-run   # 以上全部（仍跳过发布）
```

状态文件（阶段之间靠这些衔接，不要删）：

- `data/jobs/<id>/watch.json` `record.json` `highlights.json` `edit.json`
- `recordings/<id>/manifest.json`

默认 `publish.mode` 是 **draft**（暂存离开），不是公开发布。

## 何时必须找人（operator）

停下、打印原因、不要重试破解：

1. **第一次使用 / 登录过期** → 跑 `dylive login`（headed Chromium）。人用抖音 App 扫码。资料目录：`data/browser-profile/`。
2. **页面出现「扫码登录」「请完成验证」「短信验证」「安全验证」** → 暂停并告诉人在窗口里完成，然后按 Enter（非 TTY 则等 90s）。
3. **NeedAccessError**（验证码墙、403、没有 RENDER_DATA）→ 告诉人：准备 `cookies.txt`（Netscape），或换到能打开 Douyin 的网络 / 代理。
4. **创作者中心 DOM 对不上**（没有 `input[type=file]` 或找不到「发布」）→ headed 模式留下窗口，让人手工点。

不要：

- 实现签名算法、滑块、协议破解；
- 把 `.env`、`cookies.txt`、`data/browser-profile/` 提交到 git；
- 默认去搬运别人的直播（版权 / ToS）。来源字幕配置项是 `edit.source_caption`。

## 实现约束（改代码时）

- 直播状态和流地址只从**公开直播页 HTML** 解析（`RENDER_DATA`、`hls_pull_url` 等）。不要新增未公开、需 `a_bogus` 的 webcast enter 调用。
- yt-dlp 的 `DouyinIE` 目前不管 live.douyin.com；录制路径是「页面流 URL → yt-dlp/ffmpeg」。
- 上传只走官方页 `https://creator.douyin.com/creator-micro/content/upload`，用可见中文文案定位控件，不要依赖哈希 CSS 类。
- 测试必须用 ffmpeg 生成小夹具，禁止提交大媒体文件。`pytest` 要绿。

## 安装备忘

```bash
pip install -e ".[dev]"
playwright install chromium
# apt install ffmpeg fonts-noto-cjk   或  brew install ffmpeg
```
