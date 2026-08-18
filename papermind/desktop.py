"""Windows desktop launcher for the local PaperMind web application."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import threading
from urllib.parse import urlparse
import webbrowser


APP_NAME = "PaperMind"


@dataclass(frozen=True)
class DesktopPaths:
    root: Path
    data: Path
    documents: Path
    index: Path
    cache: Path
    webview: Path
    logs: Path
    env_file: Path


def configure_desktop_environment(root: Path = None) -> DesktopPaths:
    """Create persistent desktop directories and configure the web app paths."""
    if root is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = Path(local_app_data) / APP_NAME if local_app_data else Path.home() / APP_NAME

    root = Path(root).resolve()
    paths = DesktopPaths(
        root=root,
        data=root / "data",
        documents=root / "data" / "docs",
        index=root / "data" / "index",
        cache=root / "cache",
        webview=root / "webview",
        logs=root / "logs",
        env_file=root / "settings.env",
    )
    for directory in (
        paths.data,
        paths.documents,
        paths.index,
        paths.cache,
        paths.webview,
        paths.logs,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    os.environ["PM_DATA_ROOT"] = str(paths.data)
    os.environ["PM_DATA_DIR"] = str(paths.documents)
    os.environ["PM_INDEX_DIR"] = str(paths.index)
    os.environ["PM_ENV_PATH"] = str(paths.env_file)
    os.environ["PM_COOKIE_SECURE"] = "0"
    os.environ["PM_ACCOUNT_SWITCHER"] = "1"
    os.environ.setdefault("PM_AUTH_REQUIRED", "1")
    os.environ.setdefault("XDG_CACHE_HOME", str(paths.cache))
    return paths


class LocalServer:
    """Run a WSGI app on an ephemeral loopback port in a background thread."""

    def __init__(self, app, port: int = 0):
        from werkzeug.serving import make_server

        self._server = make_server("127.0.0.1", port, app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="papermind-local-server",
            daemon=True,
        )

    @property
    def url(self) -> str:
        return f"http://localhost:{self._server.server_port}/"

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


class DesktopApi:
    """Small WebView bridge used only to open local OAuth starts externally."""

    def __init__(self, server_url: str):
        parsed = urlparse(server_url)
        self._port = parsed.port

    def open_external(self, url: str) -> bool:
        parsed = urlparse(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port != self._port
            or not parsed.path.startswith("/auth/oauth/")
        ):
            return False
        return bool(webbrowser.open(url))


def _show_startup_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
    except Exception:
        logging.getLogger(__name__).exception("无法显示启动错误对话框")


def run_desktop() -> int:
    paths = configure_desktop_environment()
    logging.basicConfig(
        filename=paths.logs / "desktop.log",
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        encoding="utf-8",
    )
    logger = logging.getLogger(__name__)
    server = None

    try:
        # Environment paths must be set before these modules read their defaults.
        import webview

        from papermind.server import create_app

        desktop_port = int(os.environ.get("PM_DESKTOP_PORT", "5000"))
        server = LocalServer(create_app(), port=desktop_port)
        server.start()
        logger.info("PaperMind desktop server started at %s", server.url)

        webview.create_window(
            "PaperMind · 论文知识工作台",
            server.url,
            js_api=DesktopApi(server.url),
            width=1280,
            height=820,
            min_size=(960, 640),
            background_color="#0d1117",
        )
        webview.start(
            gui="edgechromium",
            private_mode=False,
            storage_path=str(paths.webview),
        )
        return 0
    except Exception as exc:
        logger.exception("PaperMind desktop startup failed")
        _show_startup_error(
            "PaperMind 启动失败。\n\n"
            f"{exc}\n\n"
            f"详细日志：{paths.logs / 'desktop.log'}"
        )
        return 1
    finally:
        if server is not None:
            server.stop()
            logger.info("PaperMind desktop server stopped")
