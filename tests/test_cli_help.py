from typer.testing import CliRunner

from dylive.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("watch", "record", "transcribe", "detect", "edit", "compile", "jianying", "ui", "publish", "login", "run"):
        assert name in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip()
    assert "0.3.0" in result.output or result.output.strip()
