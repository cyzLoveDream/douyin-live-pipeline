"""Watch a Douyin live room via the public webpage (no signed private APIs)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

from dylive.config import AppConfig
from dylive.exceptions import NeedAccessError, NotLiveError
from dylive.httputil import build_client, get_text
from dylive.state import job_dir, write_json
from dylive.urls import LiveTarget, parse_live_url, target_from_redirect

log = logging.getLogger("dylive.watch")

# Douyin room.status == 2 means the broadcast is currently live (public page JSON).
LIVE_STATUS_VALUES = {2, "2", "live", True}

_SCRIPT_ID = re.compile(
    r'<script[^>]+id=["\'](RENDER_DATA|_RENDER_DATA_|RENDER_DATA_SSR)["\'][^>]*>([^<]+)</script>',
    re.I,
)
_ROUTER = re.compile(
    r"window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*;?\s*</script>",
    re.I | re.S,
)
_INIT_PROPS = re.compile(
    r"window\.__INIT_PROPS__\s*=\s*(\{.+?\})\s*;?\s*</script>",
    re.I | re.S,
)
_HLS = re.compile(r"https?://[^\s\"'\\<>]+?\.m3u8[^\s\"'\\<>]*", re.I)
_FLV = re.compile(r"https?://[^\s\"'\\<>]+?\.flv[^\s\"'\\<>]*", re.I)


@dataclass
class StreamUrls:
    hls: list[str] = field(default_factory=list)
    flv: list[str] = field(default_factory=list)

    def best(self) -> str | None:
        return (self.hls or self.flv or [None])[0]


@dataclass
class RoomStatus:
    target: LiveTarget
    is_live: bool
    title: str = ""
    nickname: str = ""
    status_raw: Any = None
    streams: StreamUrls = field(default_factory=StreamUrls)
    page_url: str = ""
    fetched_at: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["target"] = asdict(self.target)
        d["streams"] = asdict(self.streams)
        return d


def resolve_target(cfg: AppConfig, url: str, client: httpx.Client | None = None) -> LiveTarget:
    target = parse_live_url(url)
    if target.web_rid:
        return target
    own = client is None
    client = client or build_client(cfg)
    try:
        log.info("解析分享短链: %s", url)
        final, html, _status = get_text(client, target.canonical_url or url, retries=cfg.http.retries)
        return target_from_redirect(url, final, html)
    finally:
        if own:
            client.close()


def fetch_room(cfg: AppConfig, target: LiveTarget, client: httpx.Client | None = None) -> RoomStatus:
    own = client is None
    client = client or build_client(cfg)
    try:
        url = target.watch_url
        log.info("拉取直播页 %s", url)
        final, html, status = get_text(client, url, retries=cfg.http.retries)
        if status == 404:
            raise NeedAccessError(f"直播间不存在或需要登录才能查看: {url}", status=404)
        if status >= 400:
            raise NeedAccessError(f"直播页 HTTP {status}", status=status)
        return parse_room_html(target, html, page_url=final)
    finally:
        if own:
            client.close()


def parse_room_html(target: LiveTarget, html: str, *, page_url: str = "") -> RoomStatus:
    blobs = list(_extract_json_blobs(html))
    is_live = False
    title = ""
    nickname = ""
    status_raw: Any = None
    streams = StreamUrls()

    for blob in blobs:
        found = _walk_room(blob)
        if found.get("title"):
            title = title or str(found["title"])
        if found.get("nickname"):
            nickname = nickname or str(found["nickname"])
        if found.get("status") is not None:
            status_raw = found["status"]
        if found.get("is_live"):
            is_live = True
        for u in found.get("hls") or []:
            if u not in streams.hls:
                streams.hls.append(u)
        for u in found.get("flv") or []:
            if u not in streams.flv:
                streams.flv.append(u)

    # Fallback: regex over the raw HTML (still the public page, not a private API).
    if not streams.hls:
        streams.hls = _unique(_HLS.findall(html))
    if not streams.flv:
        streams.flv = _unique(_FLV.findall(html))

    if status_raw is None:
        # Heuristic from visible copy; do not invent endpoints.
        if "直播已结束" in html or "主播已下播" in html:
            is_live = False
            status_raw = "ended_text"
        elif streams.best() and ("直播中" in html or "hls_pull_url" in html):
            is_live = True
            status_raw = "stream_present"

    if not blobs and not streams.best() and "live.douyin.com" in (page_url or target.watch_url):
        if len(html) < 500 or "login" in html.lower():
            raise NeedAccessError("直播页没有 RENDER_DATA / 流地址，像是登录墙或空响应")

    note = ""
    if is_live and not streams.best():
        note = "判定在播但页里没有 HLS/FLV，录制可能会失败（需要 cookies 或更新 yt-dlp）"
    return RoomStatus(
        target=target,
        is_live=is_live,
        title=title,
        nickname=nickname,
        status_raw=status_raw,
        streams=streams,
        page_url=page_url or target.watch_url,
        fetched_at=time.time(),
        note=note,
    )


def wait_until_live(
    cfg: AppConfig,
    url: str,
    *,
    once: bool = False,
) -> RoomStatus:
    client = build_client(cfg)
    try:
        target = resolve_target(cfg, url, client)
        log.info("房间 web_rid=%s room_id=%s", target.web_rid, target.room_id)
        deadline = None
        if cfg.watch.timeout_seconds and cfg.watch.timeout_seconds > 0:
            deadline = time.time() + cfg.watch.timeout_seconds
        while True:
            status = fetch_room(cfg, target, client)
            _persist(cfg, status)
            _print_status(status)
            if status.is_live:
                return status
            if once:
                raise NotLiveError(
                    f"未开播: {status.target.key} {status.nickname or ''} {status.title or ''}".strip()
                )
            if deadline and time.time() >= deadline:
                raise NotLiveError(f"等待开播超时 ({cfg.watch.timeout_seconds}s)")
            log.info("未开播，%ss 后重试 …", cfg.watch.poll_interval_seconds)
            time.sleep(cfg.watch.poll_interval_seconds)
    finally:
        client.close()


def _persist(cfg: AppConfig, status: RoomStatus) -> None:
    folder = job_dir(cfg, status.target.key)
    write_json(folder / "watch.json", status.to_dict())
    write_json(folder / "room.json", {"target": asdict(status.target), "title": status.title, "nickname": status.nickname})


def _print_status(status: RoomStatus) -> None:
    state = "LIVE" if status.is_live else "OFFLINE"
    who = status.nickname or "-"
    title = status.title or "-"
    streams = len(status.streams.hls) + len(status.streams.flv)
    log.info("[%s] %s | %s | %s | streams=%s", state, status.target.key, who, title, streams)
    if status.note:
        log.warning("%s", status.note)


def _extract_json_blobs(html: str) -> list[Any]:
    blobs: list[Any] = []
    for rx in (_SCRIPT_ID,):
        for match in rx.finditer(html):
            payload = match.group(2).strip()
            parsed = _try_json(payload) or _try_json(unquote(payload))
            if parsed is not None:
                blobs.append(parsed)
    for rx in (_ROUTER, _INIT_PROPS):
        match = rx.search(html)
        if match:
            parsed = _try_json(match.group(1))
            if parsed is not None:
                blobs.append(parsed)
    return blobs


def _try_json(text: str) -> Any | None:
    text = text.strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _walk_room(node: Any) -> dict[str, Any]:
    found: dict[str, Any] = {"hls": [], "flv": [], "is_live": False}
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if "hls_pull_url" in cur and isinstance(cur["hls_pull_url"], str):
                found["hls"].append(cur["hls_pull_url"])
            if "flv_pull_url" in cur:
                found["flv"].extend(_flatten_urls(cur["flv_pull_url"]))
            if "hls_pull_url_map" in cur:
                found["hls"].extend(_flatten_urls(cur["hls_pull_url_map"]))
            status = cur.get("status_str", cur.get("status"))
            # Common public-page shapes: room.status == 2 (live), room_status == 0 (live)
            if "status_str" in cur or (
                "status" in cur and ("title" in cur or "stream_url" in cur or "owner_user_id" in cur)
            ):
                found["status"] = status
                if status in LIVE_STATUS_VALUES:
                    found["is_live"] = True
            if cur.get("room_status") == 0 and cur.get("user"):
                found["is_live"] = True
                found["status"] = cur.get("room_status")
            if not found.get("title") and isinstance(cur.get("title"), str) and cur.get("title"):
                if "status" in cur or "stream_url" in cur or "owner" in cur:
                    found["title"] = cur["title"]
            nick = cur.get("nickname")
            if isinstance(nick, str) and nick and not found.get("nickname"):
                found["nickname"] = nick
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, str):
            if ".m3u8" in cur:
                found["hls"].append(cur)
            elif ".flv" in cur and cur.startswith("http"):
                found["flv"].append(cur)
    found["hls"] = _unique(found["hls"])
    found["flv"] = _unique(found["flv"])
    return found


def _flatten_urls(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str) and value.startswith("http"):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_flatten_urls(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_flatten_urls(v))
    return out


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        item = item.replace("\\u002F", "/").replace("\\/", "/")
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
