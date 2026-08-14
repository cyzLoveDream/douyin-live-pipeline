"""Publish clips through the official Douyin creator upload page (Playwright)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from dylive.config import AppConfig
from dylive.exceptions import OperatorRequiredError, PublishError
from dylive.login import (
    CHALLENGE_MARKERS,
    LOGIN_MARKERS,
    UPLOAD_URL,
    _dismiss_modals,
    _export_netscape,
    _is_challenge,
    _is_logged_in,
)
from dylive.state import latest_job, read_json, write_json

log = logging.getLogger("dylive.publish")

VISIBILITY = {
    "public": "公开",
    "friends": "好友可见",
    "private": "仅自己可见",
}

TITLE_HINTS = ("填写作品标题", "添加作品标题", "作品标题")
DESC_HINTS = ("作品简介", "作品描述", "添加作品描述")
PUBLISH_LABELS = ("发布", "立即发布")
DRAFT_LABELS = ("暂存离开", "存草稿", "保存草稿")


def publish_clips(
    cfg: AppConfig,
    clips: list[Path] | None = None,
    *,
    dry_run: bool = False,
    title: str | None = None,
    description: str | None = None,
) -> list[dict]:
    clips = clips or _default_clips(cfg)
    if not clips:
        raise PublishError("没有可发布的成片。先运行 dylive edit")
    texts = _director_publish_texts(cfg)
    note = cfg.activity.publish_note if cfg.activity.enabled else ""
    results = []
    for clip in clips:
        if not clip.is_file():
            raise PublishError(f"找不到成片: {clip}")
        clip_title = texts.get(clip.name) or (title or clip.stem)[:30]
        desc = (description or "") + ((" " + note) if note else "")
        if dry_run:
            log.info("[dry-run] 跳过发布: %s title=%s", clip, clip_title)
            results.append({"clip": str(clip), "status": "dry-run", "title": clip_title})
            continue
        info = upload_one(cfg, clip, title=clip_title, description=desc)
        results.append(info)
    job = latest_job(cfg)
    if job:
        write_json(job / "publish.json", {"results": results})
    return results


def _director_publish_texts(cfg: AppConfig) -> dict[str, str]:
    """读 director.json，返回 {成片文件名: publish_text（标题+必带话题）}。"""
    job = latest_job(cfg)
    if not job or not (job / "director.json").is_file():
        return {}
    data = read_json(job / "director.json")
    texts: dict[str, str] = {}
    for row in data.get("clips") or []:
        if not isinstance(row, dict):
            continue
        idx = int(row.get("index", -1))
        pt = (row.get("publish_text") or "").strip()
        if idx < 0 or not pt:
            continue
        start = int(float(row.get("start", 0)))
        end = int(float(row.get("end", 0)))
        texts[f"{job.name}_{idx + 1:02d}_{start}-{end}.mp4"] = pt
    return texts


def _default_clips(cfg: AppConfig) -> list[Path]:
    job = latest_job(cfg)
    if job and (job / "edit.json").is_file():
        data = read_json(job / "edit.json")
        return [Path(p) for p in data.get("clips") or [] if Path(p).is_file()]
    if cfg.paths.output.exists():
        return sorted(p for p in cfg.paths.output.glob("*.mp4") if p.is_file())
    return []


def upload_one(cfg: AppConfig, clip: Path, *, title: str, description: str = "") -> dict:
    from playwright.sync_api import TimeoutError as PwTimeout
    from playwright.sync_api import sync_playwright

    cfg.paths.ensure()
    profile = cfg.paths.browser_profile
    profile.mkdir(parents=True, exist_ok=True)
    url = cfg.publish.url or UPLOAD_URL
    headed = cfg.publish.headed
    timeout_ms = int(cfg.publish.timeout_seconds * 1000)

    log.info("打开创作者上传页 %s （headed=%s）", url, headed)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not headed,
            viewport={"width": 1400, "height": 900},
            locale="zh-CN",
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except PwTimeout as exc:
            context.close()
            raise PublishError(f"打不开上传页（网络或代理问题）: {url}") from exc

        page.wait_for_timeout(1500)
        _dismiss_modals(page)
        html = page.content()
        if _is_challenge(html):
            _pause_for_operator(page, "验证码 / 2FA / 安全验证")
        if not _is_logged_in(page.url, page.content()) or any(m in page.content() for m in LOGIN_MARKERS):
            _pause_for_operator(page, "扫码登录")
            if not _is_logged_in(page.url, page.content()):
                context.close()
                raise OperatorRequiredError("仍未登录。请先运行 dylive login")

        try:
            _export_netscape(context.cookies(), cfg.paths.cookies)
        except Exception:
            pass

        fill_upload_form(
            page,
            video=clip,
            title=title,
            description=description,
            visibility=cfg.publish.visibility,
            timeout_ms=timeout_ms,
        )
        if cfg.publish.mode == "draft":
            clicked = click_first_button(page, DRAFT_LABELS)
            action = "draft"
        else:
            clicked = click_first_button(page, PUBLISH_LABELS, exact=True)
            action = "publish"
        if not clicked:
            context.close()
            raise OperatorRequiredError(
                f"找不到{'发布' if action == 'publish' else '暂存离开'}按钮。"
                "页面结构可能已改版，请在打开的窗口里手动点一下，然后 Ctrl+C 结束。"
            )
        page.wait_for_timeout(4000)
        result = {"clip": str(clip), "status": action, "title": title, "url": page.url}
        log.info("完成 %s: %s", action, clip.name)
        context.close()
        return result


def fill_upload_form(page, *, video: Path, title: str, description: str, visibility: str, timeout_ms: int) -> None:
    """Drive the official upload form. Selectors prefer visible Chinese copy over CSS classes."""
    file_input = page.locator("input[type='file']").first
    try:
        file_input.wait_for(state="attached", timeout=timeout_ms)
    except Exception as exc:
        raise PublishError(
            "上传页没有 file input。可能未登录，或创作者中心改版。请跑 dylive login 后重试。"
        ) from exc
    file_input.set_input_files(str(video.resolve()))
    log.info("已选择文件 %s，等待转码/跳转 …", video.name)

    deadline = time.time() + timeout_ms / 1000
    ready = False
    while time.time() < deadline:
        _dismiss_modals(page)
        if _looks_like_composer(page):
            ready = True
            break
        time.sleep(0.8)
    if not ready:
        raise PublishError("上传后没有出现标题编辑框（转码超时或页面改版）")

    _fill_title(page, title)
    if description:
        _fill_description(page, description)
    _set_visibility(page, visibility)


def _looks_like_composer(page) -> bool:
    try:
        return bool(
            page.evaluate(
                """(hints) => {
                  const inputs = Array.from(document.querySelectorAll('input,textarea,[contenteditable="true"]'));
                  return inputs.some((el) => {
                    const ph = (el.getAttribute('placeholder') || el.getAttribute('aria-label') || '');
                    return hints.some((h) => ph.includes(h) || (el.textContent || '').includes(h));
                  }) || Array.from(document.querySelectorAll('button')).some(
                    (b) => (b.textContent || '').includes('暂存离开') || (b.textContent || '').trim() === '发布'
                  );
                }""",
                list(TITLE_HINTS),
            )
        )
    except Exception:
        return False


def _fill_title(page, title: str) -> None:
    for hint in TITLE_HINTS:
        loc = page.locator(f"input[placeholder*='{hint}']")
        if loc.count():
            loc.first.fill(title)
            return
    page.evaluate(
        """(title) => {
          const hints = ['填写作品标题', '添加作品标题', '作品标题'];
          const el = Array.from(document.querySelectorAll('input,textarea')).find((n) =>
            hints.some((h) => (n.placeholder || n.getAttribute('aria-label') || '').includes(h))
          );
          if (!el) return false;
          el.focus();
          el.value = title;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return true;
        }""",
        title,
    )


def _fill_description(page, description: str) -> None:
    try:
        editor = page.locator('[contenteditable="true"]').first
        if editor.count():
            editor.click()
            editor.fill(description)
            return
    except Exception:
        pass
    page.evaluate(
        """(text) => {
          const editor = document.querySelector('[contenteditable="true"]');
          if (!editor) return false;
          editor.focus();
          editor.textContent = text;
          editor.dispatchEvent(new Event('input', { bubbles: true }));
          return true;
        }""",
        description,
    )


def _set_visibility(page, visibility: str) -> None:
    label = VISIBILITY.get(visibility, VISIBILITY["public"])
    try:
        loc = page.get_by_text(label, exact=False)
        if loc.count():
            loc.first.click(timeout=2000)
    except Exception:
        log.debug("未能切换可见性到 %s（页面可能无此选项）", label)


def click_first_button(page, labels: tuple[str, ...], *, exact: bool = False) -> bool:
    clicked = page.evaluate(
        """({ labels, exact }) => {
          const buttons = Array.from(document.querySelectorAll('button,[role="button"]'));
          for (const label of labels) {
            const btn = buttons.find((el) => {
              const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
              if (exact) return t === label;
              return t === label || t.includes(label);
            });
            if (btn && !btn.disabled) { btn.click(); return true; }
          }
          return false;
        }""",
        {"labels": list(labels), "exact": exact},
    )
    return bool(clicked)


def _pause_for_operator(page, reason: str) -> None:
    log.warning("需要人工操作: %s。请在打开的浏览器窗口完成，然后回到终端按 Enter。", reason)
    try:
        input(f"\nOPERATOR ACTION REQUIRED ({reason}). 完成后按 Enter 继续 …\n")
    except EOFError:
        # Non-interactive: wait a bit for the operator watching the window.
        log.warning("终端不可交互，等待 90s 以便你在浏览器里完成 %s", reason)
        page.wait_for_timeout(90_000)
    _dismiss_modals(page)
