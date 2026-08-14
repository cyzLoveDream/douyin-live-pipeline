from pathlib import Path

from dylive.httputil import load_netscape_cookies


def test_netscape_parser(tmp_path: Path):
    p = tmp_path / "cookies.txt"
    p.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t1999999999\tttwid\tabc\n"
        "#HttpOnly_.douyin.com\tTRUE\t/\tTRUE\t1999999999\tsessionid\txyz\n",
        encoding="utf-8",
    )
    cookies = load_netscape_cookies(p)
    jar = {c.name: c.value for c in cookies.jar}
    assert jar["ttwid"] == "abc"
    assert jar["sessionid"] == "xyz"
