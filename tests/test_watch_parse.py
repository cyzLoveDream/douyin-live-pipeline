from dylive.urls import parse_live_url
from dylive.watch import parse_room_html


LIVE_HTML = """
<html><body>
<script id="RENDER_DATA">%7B%22state%22%3A%7B%22roomStore%22%3A%7B%22roomInfo%22%3A%7B%22room%22%3A%7B%22title%22%3A%22hello-live%22%2C%22status%22%3A2%2C%22status_str%22%3A%222%22%2C%22stream_url%22%3A%7B%22hls_pull_url%22%3A%22https%3A%2F%2Fpull.example.com%2Flive.m3u8%22%2C%22flv_pull_url%22%3A%7B%22FULL_HD1%22%3A%22https%3A%2F%2Fpull.example.com%2Flive.flv%22%7D%7D%7D%2C%22anchor%22%3A%7B%22nickname%22%3A%22tester%22%7D%7D%7D%7D%7D</script>
</body></html>
"""

OFFLINE_HTML = """
<html><body>
<script id="RENDER_DATA">{"state":{"roomStore":{"roomInfo":{"room":{"title":"bye","status":4,"status_str":"4"}}}}}</script>
</body></html>
"""


def test_parse_live_render_data():
    target = parse_live_url("https://live.douyin.com/111")
    status = parse_room_html(target, LIVE_HTML)
    assert status.is_live
    assert status.title == "hello-live"
    assert status.nickname == "tester"
    assert status.streams.hls
    assert "live.m3u8" in status.streams.hls[0]
    assert status.streams.flv


def test_parse_offline():
    target = parse_live_url("https://live.douyin.com/111")
    status = parse_room_html(target, OFFLINE_HTML)
    assert not status.is_live
    assert status.title == "bye"
