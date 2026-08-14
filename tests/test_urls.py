import pytest

from dylive.urls import parse_live_url, target_from_redirect


def test_live_numeric_room():
    t = parse_live_url("https://live.douyin.com/745964462470")
    assert t.web_rid == "745964462470"
    assert t.watch_url == "https://live.douyin.com/745964462470"


def test_live_with_query():
    t = parse_live_url("https://live.douyin.com/12345?foo=bar")
    assert t.web_rid == "12345"


def test_webrid_query():
    t = parse_live_url("https://www.douyin.com/follow?webRid=998877")
    assert t.web_rid == "998877"


def test_share_code_without_follow():
    t = parse_live_url("https://v.douyin.com/iQFeBnt/")
    assert t.share_code == "iQFeBnt"
    assert t.web_rid is None


def test_scheme_optional():
    t = parse_live_url("live.douyin.com/abc_def")
    assert t.web_rid == "abc_def"


def test_webcast_reflow():
    t = parse_live_url("https://webcast.amemv.com/webcast/reflow/7123456789")
    assert t.room_id == "7123456789"


def test_redirect_html_extracts_webrid():
    html = 'redirect to https://live.douyin.com/555666 and room_id=999'
    t = target_from_redirect("https://v.douyin.com/xxx/", "https://www.douyin.com/share/live", html)
    assert t.web_rid == "555666"
    assert t.room_id == "999"


def test_bad_url():
    with pytest.raises(ValueError):
        parse_live_url("https://example.com/nope")
