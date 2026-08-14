# douyin-live-pipeline

把一条抖音直播链接变成竖屏高能切片，并可从官方创作者中心上传。换机器 `git clone` 后按本文安装即可跑。

包名 **dylive**，命令行入口也是 `dylive`。

---

## 你必须知道的限制

1. **网络**：运行环境必须能打开 `live.douyin.com` 和 `creator.douyin.com`。在海外通常需要 `HTTP_PROXY` / `HTTPS_PROXY`（或系统代理）。本工具**不会**绕过验证码、签名（`a_bogus`）或反爬。
2. **版权 / 平台条款**：转载、二次分发**别人的直播**可能侵犯著作权并违反抖音用户协议。默认请只处理**你自己的直播间**。若确实要标注来源，打开 `edit.source_caption`（默认开），字幕形如 `来源 live.douyin.com/<房间id>`。
3. **登录**：发布和很多直播页都需要登录。第一次在能弹出窗口的机器上跑 `dylive login`，用抖音 App 扫码。浏览器资料存在 `data/browser-profile/`（已 gitignore），cookies 可导出为 `cookies.txt`（也已 gitignore）。**不要把密码写进仓库，也不要去偷操作系统里的浏览器 cookie。**
4. **yt-dlp 现状（2026）**：官方 extractor `DouyinIE` 只认 `https://www.douyin.com/video/<id>`，**没有** Douyin 直播 extractor（yt-dlp#7231 仍未落地）。流水线会：
   - 先让 yt-dlp 碰一下直播 URL（失败是预期的）；
   - 再解析**公开直播页 HTML** 里的 `RENDER_DATA` / `hls_pull_url` / `flv_pull_url`；
   - 把得到的 HLS/FLV 交给 yt-dlp 或 ffmpeg 分段录像。
   若页面被风控成验证码墙，工具会明确报错：需要 cookies / 大陆网络 / 代理。

---

## 依赖

| 类型 | 内容 |
| --- | --- |
| Python | 3.11+ |
| 系统 | **ffmpeg** + ffprobe（`apt install ffmpeg` / `brew install ffmpeg`） |
| Python 包 | `yt-dlp` `playwright` `pyyaml` `typer` `httpx`（见 `pyproject.toml` 钉死版本） |
| 可选 | `faster-whisper`（字幕；模型缺失时自动跳过） |
| 字体 | 标题卡 / 来源字幕需要中文字体，Debian 上 `fonts-noto-cjk` 即可 |

```bash
git clone https://github.com/cyzLoveDream/douyin-live-pipeline.git
cd douyin-live-pipeline
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium        # 登录 / 发布用

# 系统依赖（择一）
sudo apt install -y ffmpeg fonts-noto-cjk     # Debian/Ubuntu
brew install ffmpeg                           # macOS
```

复制配置：

```bash
cp config.example.yaml config.yaml
cp .env.example .env          # 可选，代理和 cookies 路径
```

---

## 命令

```bash
dylive --help
dylive -c config.yaml run "https://live.douyin.com/<web_rid>" --dry-run
```

分阶段（给人或给其他 agent 逐步跑）：

```bash
# 1. 解析房间、轮询直到 LIVE
dylive watch "https://live.douyin.com/745964462470"
dylive watch "https://v.douyin.com/xxxx/" --once     # 只查一次

# 2. 开播后分段录像到 recordings/<id>/
dylive record "https://live.douyin.com/745964462470" --max-seconds 120

# 3. 高能检测（音频能量尖峰 + 切镜；可选本地弹幕 JSON）
dylive detect
dylive detect recordings/745964462470

# 4. 二次剪辑 → output/clips/ （9:16、响度、标题卡、来源字幕）
dylive edit
dylive edit --title "今晚高能" --room-id 745964462470

# 5. 创作者中心上传。默认草稿；--dry-run 完全不打开浏览器
dylive publish --dry-run
dylive publish output/clips/foo.mp4 --title "切片标题"

# 扫码登录（会弹出 Chromium；登录态写进 data/browser-profile/）
dylive login
```

全流程：

```bash
dylive run "https://live.douyin.com/<你的房间>" --dry-run --max-seconds 180
```

`--dry-run` **只跳过发布**，前面的录像和剪辑仍会跑。

---

## 登录与 cookies

```bash
dylive login
```

1. 弹出 Chromium，打开 `https://creator.douyin.com/`。
2. 用抖音 App 扫码。若出现验证码 / 短信 / 2FA，在窗口里自己完成。
3. 成功后把 cookie 写成 Netscape 格式的 `cookies.txt`，供 yt-dlp 和直播页请求使用。

也可以用浏览器扩展（如 Get cookies.txt LOCALLY）从已经能打开抖音的浏览器导出，放到项目根目录 `cookies.txt`。

出现验证码时流水线会**停住并提示操作员**，不会尝试自动破解。

---

## 目录

```
recordings/<room>/     # 分段 ts/mp4 + manifest.json
output/clips/          # 成片
data/jobs/<room>/      # watch.json / highlights.json / edit.json
data/browser-profile/  # Playwright 持久化登录（gitignore）
cookies.txt            # gitignore
```

---

## 高能检测

- **音频**：把音轨解成 PCM，按窗口算 RMS，超过分位阈值的区间视为尖峰。
- **切镜**：`ffmpeg` `select='gt(scene,…)'`。
- **弹幕/礼物**：不爬私有接口。若你自己已有 JSON（`[{"t": 12.3, "type": "gift"}]`），在配置里填 `detect.chat_events_file`。
- 相邻窗口合并，长度默认夹在 **12–45 秒**（`config.yaml` 可改）。

剪辑：补前后 padding → 竖屏 9:16（`crop` 或 `blur` 填充）→ `loudnorm` → 可选标题卡 → 来源字幕 → 可选 whisper 字幕。

发布：Playwright 打开官方页
[https://creator.douyin.com/creator-micro/content/upload](https://creator.douyin.com/creator-micro/content/upload)
（2026 年创作者中心上传地址）。默认 `publish.mode: draft`（点「暂存离开」），改成 `publish` 才会点「发布」。

---

## 测试

```bash
pytest
```

测试用 ffmpeg 现场生成很小的 wav/mp4，不往 git 提交媒体二进制。

---

## 配置要点

见 `config.example.yaml`。常用：

- `record.segment_seconds` / `resume_gap_seconds`：分段与短断线续录
- `detect.min_clip_seconds` / `max_clip_seconds`
- `edit.fill`: `blur` 或 `crop`
- `edit.source_caption`: 来源字幕
- `publish.mode`: `draft` | `publish`
- `publish.headed`: 发布时是否显示浏览器（验证码时必须能看到）

环境变量：`DYLIVE_CONFIG`、`DYLIVE_COOKIES`、`HTTP_PROXY`、`HTTPS_PROXY`。

---

## 故障

| 现象 | 处理 |
| --- | --- |
| `无法访问抖音` / 验证码墙 | `dylive login` 或放入新鲜 `cookies.txt`；确认代理能打开直播页 |
| yt-dlp `Unsupported URL` / `Fresh cookies` | 预期。工具会改走页面解析；仍失败就按上一行 |
| 上传页没有 file input | 未登录或页面改版 → `dylive login`；不行就 headed 模式下手动点 |
| whisper 没字幕 | 没装 `faster-whisper` 或模型没下完，会跳过，不影响出片 |

许可证：MIT。
