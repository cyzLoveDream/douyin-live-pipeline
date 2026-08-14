from pathlib import Path

import pytest

from tests.conftest import make_landscape_video

HTML = """<!doctype html>
<meta charset="utf-8">
<title>upload mock</title>
<input type="file" accept="video/*">
<input placeholder="填写作品标题" id="title">
<div contenteditable="true" placeholder="作品简介" id="desc"></div>
<label><input type="radio" name="vis">公开</label>
<button type="button" id="pub">发布</button>
<button type="button" id="draft">暂存离开</button>
<pre id="log"></pre>
<script>
document.querySelector('input[type=file]').addEventListener('change', (e) => {
  document.getElementById('log').textContent = 'file:' + (e.target.files[0] && e.target.files[0].name);
});
document.getElementById('pub').onclick = () => { document.getElementById('log').textContent += '|published'; };
document.getElementById('draft').onclick = () => { document.getElementById('log').textContent += '|drafted'; };
</script>
"""


@pytest.fixture(scope="module")
def playwright_chromium():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"chromium not installed: {exc}")
        yield browser
        browser.close()


def test_fill_and_draft(playwright_chromium, tmp_path: Path):
    from dylive.publish import click_first_button, fill_upload_form

    html = tmp_path / "upload.html"
    html.write_text(HTML, encoding="utf-8")
    video = make_landscape_video(tmp_path / "clip.mp4", seconds=1.0)
    page = playwright_chromium.new_page()
    page.goto(html.as_uri())
    fill_upload_form(
        page,
        video=video,
        title="单元测试标题",
        description="desc",
        visibility="public",
        timeout_ms=10_000,
    )
    assert page.locator("#title").input_value() == "单元测试标题"
    assert click_first_button(page, ("暂存离开",))
    assert "drafted" in page.locator("#log").inner_text()
    page.close()
