"""Typed failures with operator-facing messages."""


class DyliveError(Exception):
    """Base error for the pipeline."""


class ConfigError(DyliveError):
    """Invalid or missing configuration."""


class NeedAccessError(DyliveError):
    """Douyin blocked the request; cookies / China network / proxy needed."""

    DEFAULT = (
        "无法访问抖音。本工具不会绕过验证码、签名或反爬。请检查：\n"
        "  1. cookies.txt（Netscape 格式，含 ttwid / s_v_web_id，用浏览器导出）\n"
        "  2. 运行环境能打开 live.douyin.com（通常需要中国大陆网络）\n"
        "  3. HTTP_PROXY / HTTPS_PROXY 指向可用代理\n"
        "然后执行: dylive login"
    )

    def __init__(self, detail: str = "", *, status: int | None = None):
        extra = f"\n详情: {detail}" if detail else ""
        if status is not None:
            extra += f" (HTTP {status})"
        super().__init__(self.DEFAULT + extra)
        self.detail = detail
        self.status = status


class NotLiveError(DyliveError):
    """Room is currently offline."""


class OperatorRequiredError(DyliveError):
    """QR / captcha / 2FA — a human must complete it in the browser."""


class MediaError(DyliveError):
    """ffmpeg / yt-dlp / probe failure."""


class PublishError(DyliveError):
    """Creator-center upload page could not complete."""


class DependencyError(DyliveError):
    """Optional extra is not installed (e.g. pyJianYingDraft)."""

