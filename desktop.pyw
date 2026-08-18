"""PaperMind Windows desktop entry point."""

from multiprocessing import freeze_support

from papermind.desktop import run_desktop


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(run_desktop())

