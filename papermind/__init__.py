"""PaperMind: 面向计算机视觉论文的轻量级 RAG 问答系统。"""

__version__ = "1.0.0"

__all__ = ["Config"]


def __getattr__(name):
    if name == "Config":
        from .config import Config

        return Config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
