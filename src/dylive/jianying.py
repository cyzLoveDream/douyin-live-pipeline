"""剪映-friendly sidecar export. Not a CapCut/Jianying draft replica.

Drops rendered clips + captions.ass/srt + timeline.json + IMPORT.md so a human
(or another agent) can import into 剪映. We do not vendor third-party draft builders.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from dylive.captions import write_ass, write_srt
from dylive.config import AppConfig
from dylive.timeline import Timeline, caption_words
from dylive.transcribe import Word

IMPORT_MD = """# 导入剪映

这个目录是 **成片旁路导出**，不是剪映工程文件（`.draft` / CapCut draft）。

## 怎么用

1. 打开剪映 → 新建草稿 → 竖屏 9:16。
2. 把 `clip_*.mp4` 拖进时间轴（已经是 9:16、烧过字幕的成片；若要重新排版，用未烧字幕的口播轨）。
3. 若只想用字幕：导入 `captions.srt`（或 `captions.ass`）作为字幕轨。
4. `timeline.json` 记录了每段在 **原始录像** 上的 in/out，以及特效名（punch_zoom / fade / caption_mask 等），方便对照，不是剪映滤镜预设。

## 不会做的事

- 不生成剪映草稿二进制 / 不调用未公开接口。
- 不读取你电脑上的剪映工程目录。

字幕字体建议：Noto Sans CJK / 思源黑体。Debian：`sudo apt install fonts-noto-cjk`。
"""


def export_jianying(
    cfg: AppConfig,
    room_id: str,
    clips: list[Path],
    *,
    words: list[Word],
    timeline: Timeline | None,
    caption_style: str = "douyin",
) -> Path:
    dest = Path("output") / "jianying" / room_id
    # honour cfg output parent
    dest = cfg.paths.output.parent / "jianying" / room_id
    dest.mkdir(parents=True, exist_ok=True)
    for i, clip in enumerate(clips, start=1):
        if clip.is_file():
            shutil.copy2(clip, dest / f"clip_{i:02d}{clip.suffix}")
        ass = clip.with_suffix(".ass")
        if ass.is_file():
            shutil.copy2(ass, dest / f"clip_{i:02d}.ass")
    if words:
        write_srt(dest / "captions.srt", words)
        write_ass(
            dest / "captions.ass",
            words,
            style=caption_style,
            width=cfg.edit.width,
            height=cfg.edit.height,
        )
    elif timeline:
        tw = caption_words(timeline)
        if tw:
            write_srt(dest / "captions.srt", tw)
            write_ass(
                dest / "captions.ass",
                tw,
                style=caption_style,
                width=cfg.edit.width,
                height=cfg.edit.height,
            )
    if timeline:
        (dest / "timeline.json").write_text(
            __import__("json").dumps(timeline.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (dest / "IMPORT.md").write_text(IMPORT_MD, encoding="utf-8")
    return dest
