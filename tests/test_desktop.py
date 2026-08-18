import os
from pathlib import Path
import subprocess
import sys
import time

from flask import Flask
import requests

from papermind.desktop import DesktopApi, LocalServer, configure_desktop_environment


def test_desktop_import_keeps_config_lazy():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
import papermind.desktop
assert "papermind.config" not in sys.modules
from papermind import Config
assert Config.__name__ == "Config"
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_configure_desktop_environment_uses_persistent_paths(tmp_path, monkeypatch):
    for key in (
        "PM_DATA_ROOT",
        "PM_DATA_DIR",
        "PM_INDEX_DIR",
        "PM_ENV_PATH",
        "PM_COOKIE_SECURE",
        "PM_AUTH_REQUIRED",
        "PM_ACCOUNT_SWITCHER",
        "XDG_CACHE_HOME",
    ):
        monkeypatch.delenv(key, raising=False)

    paths = configure_desktop_environment(tmp_path / "PaperMind")

    assert paths.documents.is_dir()
    assert paths.index.is_dir()
    assert paths.webview.is_dir()
    assert os.environ["PM_DATA_ROOT"] == str(paths.data)
    assert os.environ["PM_DATA_DIR"] == str(paths.documents)
    assert os.environ["PM_INDEX_DIR"] == str(paths.index)
    assert os.environ["PM_ENV_PATH"] == str(paths.env_file)
    assert os.environ["PM_COOKIE_SECURE"] == "0"
    assert os.environ["PM_AUTH_REQUIRED"] == "1"
    assert os.environ["PM_ACCOUNT_SWITCHER"] == "1"


def test_local_server_uses_loopback_and_stops():
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {"ok": True}

    server = LocalServer(app)
    assert server.url.startswith("http://localhost:")

    server.start()
    try:
        response = requests.get(f"{server.url}health", timeout=3)
        assert response.json() == {"ok": True}
    finally:
        server.stop()

    for _ in range(20):
        if not server.running:
            break
        time.sleep(0.01)
    assert not server.running


def test_desktop_api_only_opens_local_oauth_urls(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "papermind.desktop.webbrowser.open",
        lambda url: opened.append(url) or True,
    )
    api = DesktopApi("http://localhost:5000/")

    allowed = "http://localhost:5000/auth/oauth/google?handoff=test"
    assert api.open_external(allowed) is True
    assert api.open_external("https://accounts.google.com/") is False
    assert api.open_external("http://localhost:5001/auth/oauth/google") is False
    assert opened == [allowed]
