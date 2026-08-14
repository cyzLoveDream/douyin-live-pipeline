"""Parse Douyin live URLs and share links into a web_rid / room identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

LIVE_HOSTS = {"live.douyin.com", "www.live.douyin.com"}
SHARE_HOSTS = {"v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"}
WEBCAST_HOSTS = {"webcast.amemv.com", "webcast.douyin.com"}

# live.douyin.com/<web_rid>  (numeric or short handle)
_LIVE_PATH = re.compile(r"^/([A-Za-z0-9._\-]+)(?:/)?$")
_WEB_RID_QUERY = re.compile(r"(?:webRid|web_rid|web_room_id)=([A-Za-z0-9._\-]+)", re.I)
_ROOM_ID_QUERY = re.compile(r"(?:room_id|roomId|room_id_str)=(\d+)", re.I)
# Share pages sometimes embed "https://live.douyin.com/123"
_EMBEDDED_LIVE = re.compile(
    r"https?://(?:www\.)?live\.douyin\.com/([A-Za-z0-9._\-]+)", re.I
)


@dataclass(frozen=True)
class LiveTarget:
    """Identity of a Douyin live room as far as the public web page goes."""

    original_url: str
    canonical_url: str
    web_rid: str | None
    room_id: str | None = None
    share_code: str | None = None

    @property
    def key(self) -> str:
        return self.web_rid or self.room_id or "unknown"

    @property
    def watch_url(self) -> str:
        if self.web_rid:
            return f"https://live.douyin.com/{self.web_rid}"
        return self.canonical_url or self.original_url


def looks_like_live_url(url: str) -> bool:
    try:
        host = urlparse(_ensure_scheme(url)).hostname or ""
    except ValueError:
        return False
    host = host.lower()
    return host in LIVE_HOSTS or host in SHARE_HOSTS or host in WEBCAST_HOSTS or "douyin.com" in host


def _ensure_scheme(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("空链接")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def parse_live_url(url: str) -> LiveTarget:
    """Parse a live / share URL without network I/O.

    Share short-links may not contain a web_rid until redirects are followed;
    call `resolve_share` in `dylive.watch` for those.
    """
    original = url.strip()
    full = _ensure_scheme(original)
    parsed = urlparse(full)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parsed.query or ""
    combined = f"{path}?{query}"

    web_rid = None
    room_id = None
    share_code = None

    m = _WEB_RID_QUERY.search(combined)
    if m:
        web_rid = m.group(1)
    m = _ROOM_ID_QUERY.search(combined)
    if m:
        room_id = m.group(1)

    if host in LIVE_HOSTS:
        pm = _LIVE_PATH.match(path)
        if pm and pm.group(1) not in {"", "favicon.ico"}:
            web_rid = web_rid or pm.group(1)
        canonical = f"https://live.douyin.com/{web_rid}" if web_rid else full.split("?")[0]
        return LiveTarget(original, canonical, web_rid, room_id)

    if host in SHARE_HOSTS or host.endswith(".douyin.com") and "/share" in path:
        parts = [p for p in path.split("/") if p]
        if parts:
            share_code = parts[-1]
        embedded = _EMBEDDED_LIVE.search(full)
        if embedded:
            web_rid = web_rid or embedded.group(1)
        return LiveTarget(original, full, web_rid, room_id, share_code=share_code)

    if host in WEBCAST_HOSTS:
        # /webcast/reflow/<room_id>
        parts = [p for p in path.split("/") if p]
        if parts:
            maybe = parts[-1]
            if maybe.isdigit():
                room_id = room_id or maybe
        return LiveTarget(original, full, web_rid, room_id)

    embedded = _EMBEDDED_LIVE.search(full)
    if embedded:
        web_rid = embedded.group(1)
        return LiveTarget(
            original, f"https://live.douyin.com/{web_rid}", web_rid, room_id, share_code
        )

    qs = parse_qs(query)
    for key in ("webRid", "web_rid", "roomId", "room_id"):
        if qs.get(key):
            val = qs[key][0]
            if key.lower().startswith("web"):
                web_rid = web_rid or val
            else:
                room_id = room_id or val
    if web_rid:
        return LiveTarget(original, f"https://live.douyin.com/{web_rid}", web_rid, room_id)

    raise ValueError(
        f"无法从链接解析直播间: {url!r}。支持 live.douyin.com/<id> 或 v.douyin.com 分享短链。"
    )


def target_from_redirect(original: str, final_url: str, html: str = "") -> LiveTarget:
    """Build a LiveTarget after following a share-link redirect."""
    try:
        parsed = parse_live_url(final_url)
    except ValueError:
        parsed = LiveTarget(original, final_url, None, None)

    web_rid = parsed.web_rid
    room_id = parsed.room_id
    if html:
        if not web_rid:
            m = _EMBEDDED_LIVE.search(html) or _WEB_RID_QUERY.search(html)
            if m:
                web_rid = m.group(1)
        if not room_id:
            m = _ROOM_ID_QUERY.search(html)
            if m:
                room_id = m.group(1)
    if not web_rid and not room_id:
        # last chance: original share parse
        try:
            orig = parse_live_url(original)
            web_rid = orig.web_rid
            room_id = orig.room_id
        except ValueError:
            pass
    if not web_rid and not room_id:
        raise ValueError(f"短链跳转后仍无法得到房间 id: {final_url}")
    canonical = f"https://live.douyin.com/{web_rid}" if web_rid else final_url
    return LiveTarget(original, canonical, web_rid, room_id)
