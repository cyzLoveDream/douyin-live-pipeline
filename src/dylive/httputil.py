"""HTTP client: cookies.txt, retries, proxy from env, browser-like headers."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from dylive.config import AppConfig
from dylive.exceptions import NeedAccessError

log = logging.getLogger("dylive.http")

BLOCK_MARKERS = (
    "验证码",
    "请完成验证",
    "captcha",
    "安全验证",
    "滑块",
    "access denied",
    "unusual traffic",
)


def load_netscape_cookies(path: Path) -> httpx.Cookies:
    cookies = httpx.Cookies()
    if not path.is_file():
        return cookies
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_") :]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        domain, _flag, _path, _secure, _expiry, name, value = parts[:7]
        domain = domain.lstrip(".")
        try:
            cookies.set(name, value, domain=domain, path=_path or "/")
        except Exception:
            cookies.set(name, value)
        _ = http_only  # Netscape flag; httpx does not expose HttpOnly
    return cookies


def build_client(cfg: AppConfig) -> httpx.Client:
    cookies = load_netscape_cookies(cfg.paths.cookies)
    timeout = httpx.Timeout(cfg.http.timeout_seconds, connect=min(15.0, cfg.http.timeout_seconds))
    headers = {
        "User-Agent": cfg.http.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://live.douyin.com/",
    }
    # httpx reads HTTP(S)_PROXY from the environment by default (trust_env=True).
    return httpx.Client(
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        follow_redirects=True,
        trust_env=True,
    )


def get_text(client: httpx.Client, url: str, *, retries: int = 3, referer: str | None = None) -> tuple[str, str, int]:
    """GET url, return (final_url, text, status). Raises NeedAccessError on blocks."""
    last_exc: Exception | None = None
    headers = {}
    if referer:
        headers["Referer"] = referer
    for attempt in range(1, retries + 1):
        try:
            resp = client.get(url, headers=headers)
            final = str(resp.url)
            status = resp.status_code
            text = resp.text or ""
            if status in {401, 403, 407}:
                raise NeedAccessError(f"{url} 被拒绝", status=status)
            if status == 404:
                return final, text, status
            if status >= 500:
                raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)
            lowered = text[:8000].lower()
            if status == 200 and any(m.lower() in lowered for m in BLOCK_MARKERS):
                # A captcha wall still sometimes returns 200.
                if "RENDER_DATA" not in text and "_ROUTER_DATA" not in text and "hls_pull_url" not in text:
                    raise NeedAccessError("页面像是验证码/风控墙，没有直播数据", status=status)
            return final, text, status
        except NeedAccessError:
            raise
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_exc = exc
            log.warning("GET %s 失败 (%s/%s): %s", url, attempt, retries, exc)
            time.sleep(min(2 ** attempt, 8))
    raise NeedAccessError(f"请求失败: {last_exc}")
