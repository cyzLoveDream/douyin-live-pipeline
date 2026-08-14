"""Word-level ASS captions: hormozi / douyin / standard.

Other agents can call build_ass() / write_ass() without going through the CLI.
"""

from __future__ import annotations

from pathlib import Path

from dylive.exceptions import MediaError
from dylive.media import font_family_name, require_cjk_font
from dylive.transcribe import Word

# ASS highlight yellow (AABBGGRR) — Douyin/TikTok punch colour.
HIGHLIGHT = "0000E5FF"
WHITE = "00FFFFFF"
DIM = "00AAAAAA"


def ass_time(seconds: float) -> str:
    t = max(0.0, float(seconds))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return (
        (text or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", r"\N")
    )


def _is_cjk(ch: str) -> bool:
    return any(
        "\u4e00" <= c <= "\u9fff" or "\u3400" <= c <= "\u4dbf" or "\uf900" <= c <= "\ufaff"
        for c in ch
    )


def join_words(parts: list[str]) -> str:
    if not parts:
        return ""
    out = parts[0]
    for p in parts[1:]:
        if _is_cjk(out[-1:]) and _is_cjk(p[:1]):
            out += p
        else:
            out += " " + p
    return out


def slice_words(words: list[Word], start: float, end: float, *, origin: float) -> list[Word]:
    """Clip-relative copies of words overlapping [start, end]."""
    out: list[Word] = []
    for w in words:
        if w.end <= start + 0.01 or w.start >= end - 0.01:
            continue
        out.append(
            Word(
                start=max(0.0, w.start - origin),
                end=max(0.04, w.end - origin),
                word=w.word,
                prob=w.prob,
            )
        )
    return out


def first_sentence(words: list[Word], *, max_chars: int = 14) -> str:
    buf: list[str] = []
    n = 0
    for w in words:
        token = (w.word or "").strip()
        if not token:
            continue
        buf.append(token)
        n += len(token)
        if n >= max_chars or any(c in token for c in "。！？!?，,"):
            break
    return join_words(buf)


def build_ass(
    words: list[Word],
    *,
    style: str = "douyin",
    width: int = 1080,
    height: int = 1920,
    font_name: str = "Noto Sans CJK SC",
) -> str:
    """Return an ASS document with Dialogue lines for each spoken word/group."""
    if style not in {"hormozi", "douyin", "standard"}:
        style = "douyin"
    header = _header(width, height, font_name)
    if not words:
        return header
    if style == "hormozi":
        events = _hormozi_events(words)
    elif style == "standard":
        events = _standard_events(words)
    else:
        events = _douyin_events(words)
    return header + "".join(events)


def write_ass(
    path: Path,
    words: list[Word],
    *,
    style: str = "douyin",
    width: int = 1080,
    height: int = 1920,
    font_path: str | None = None,
) -> Path:
    font_path = font_path or require_cjk_font()
    family = font_family_name(font_path)
    text = build_ass(words, style=style, width=width, height=height, font_name=family)
    if "Dialogue:" not in text:
        raise MediaError("生成的 ASS 没有 Dialogue 行（没有可烧录的词）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def ffmpeg_subtitles_filter(ass_path: Path, font_path: str | None = None) -> str:
    """subtitles= filter argument, with fontsdir so libass finds CJK fonts."""
    font_path = font_path or require_cjk_font()
    fontsdir = str(Path(font_path).resolve().parent)
    ass = _escape_filter_path(ass_path.resolve())
    fonts = _escape_filter_path(fontsdir)
    return f"subtitles='{ass}':fontsdir='{fonts}'"


def _escape_filter_path(path: Path | str) -> str:
    s = str(path)
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")


def _header(width: int, height: int, font_name: str) -> str:
    return (
        "[Script Info]\n"
        "; dylive word-level captions\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Hormozi,{font_name},92,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,8,0,5,40,40,0,1\n"
        f"Style: Douyin,{font_name},68,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,6,0,2,48,48,260,1\n"
        f"Style: Standard,{font_name},46,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "0,0,0,0,100,100,0,0,3,8,0,2,40,40,90,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue(start: float, end: float, style: str, text: str) -> str:
    if end <= start:
        end = start + 0.04
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}\n"


def _hormozi_events(words: list[Word]) -> list[str]:
    """Huge centered current-word pop; neighbours stay dim behind it."""
    events: list[str] = []
    for i, w in enumerate(words):
        token = ass_escape(w.word.strip())
        if not token:
            continue
        prev_t = ass_escape(words[i - 1].word.strip()) if i else ""
        next_t = ass_escape(words[i + 1].word.strip()) if i + 1 < len(words) else ""
        # Current word slams the center; dim neighbours sit smaller underneath.
        body = (
            r"{\an5\bord12\shad0\c&H"
            + HIGHLIGHT
            + r"&\fscx128\fscy128\b1}"
            + token
        )
        if prev_t or next_t:
            dim = r"{\an5\fs42\bord4\c&H" + DIM + r"\fscx90\fscy90\b0}"
            extras = []
            if prev_t:
                extras.append(prev_t)
            extras.append("")  # spacer for the pop word which is on its own layer
            if next_t:
                extras.append(next_t)
            # Keep it simple: just the slamming word. Neighbours as a second line look messy
            # on vertical video; the pop is the Hormozi look.
        events.append(_dialogue(w.start, w.end, "Hormozi", body))
    return events


def _douyin_events(words: list[Word]) -> list[str]:
    """Lower-center, 2–3 words visible, current word yellow + outline."""
    events: list[str] = []
    n = len(words)
    for i, w in enumerate(words):
        if not (w.word or "").strip():
            continue
        # Keep current word in a 2–3 word window.
        start_i = max(0, min(i - 1, n - 3)) if n >= 3 else 0
        group = words[start_i : start_i + 3] if n >= 2 else [w]
        parts: list[str] = []
        for gw in group:
            token = ass_escape(gw.word.strip())
            if not token:
                continue
            if gw is w:
                parts.append(r"{\c&H" + HIGHLIGHT + r"\fs78\bord7\b1}" + token + r"{\c&H" + WHITE + r"\fs64\bord6\b0}")
            else:
                parts.append(r"{\c&H" + WHITE + r"\fs64\bord6\b0}" + token)
        if not parts:
            continue
        # CJK: no spaces between tagged runs; glue with empty ASS reset.
        text = r"{\an2}" + "".join(parts)
        events.append(_dialogue(w.start, w.end, "Douyin", text))
    return events


def _standard_events(words: list[Word]) -> list[str]:
    """Bottom bar with opaque box, ~2s / 18-char chunks."""
    events: list[str] = []
    for chunk in _chunk_words(words, max_dur=2.2, max_chars=18):
        text = join_words([ass_escape(w.word.strip()) for w in chunk if w.word.strip()])
        if not text:
            continue
        events.append(_dialogue(chunk[0].start, chunk[-1].end, "Standard", r"{\an2}" + text))
    return events


def _chunk_words(words: list[Word], *, max_dur: float, max_chars: int) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    chars = 0
    for w in words:
        token = (w.word or "").strip()
        if not token:
            continue
        if not cur:
            cur = [w]
            chars = len(token)
            continue
        dur = w.end - cur[0].start
        if dur > max_dur or chars + len(token) > max_chars:
            chunks.append(cur)
            cur = [w]
            chars = len(token)
        else:
            cur.append(w)
            chars += len(token)
    if cur:
        chunks.append(cur)
    return chunks


def srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, words: list[Word]) -> Path:
    """Grouped SRT for 剪映 / other NLEs. ASS remains the burned-in track."""
    chunks = _chunk_words(words, max_dur=2.2, max_chars=18)
    lines: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        text = join_words([w.word.strip() for w in chunk if w.word.strip()])
        if not text:
            continue
        lines.append(f"{i}\n{srt_time(chunk[0].start)} --> {srt_time(chunk[-1].end)}\n{text}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
