"""Flask 服务模块：应用工厂模式，便于测试与多种方式部署。

接口:
  GET  /            页面
  GET  /api/health  存活探针（不触发知识库加载，轻量）
  POST /api/ingest  上传文件并摄入知识库（multipart，可多文件）
  POST /api/query   问答 {question}
  GET  /api/stats   知识库状态
  GET  /api/config  API 配置状态（仅本机）
  PUT  /api/config  保存 API 配置（仅本机）
"""
from dataclasses import replace
import logging
import os
import sqlite3
import time
from urllib.parse import urlparse

from flask import Flask, g, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .auth import (can_manage_config, init_auth, load_user_api_config,
                   save_user_api_config, valid_csrf)
from .config import Config
from .pipeline import RAGPipeline

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_ROOT = os.environ.get("PM_DATA_ROOT", os.path.join(ROOT, "data"))
UPLOAD_DIR = os.path.join(DATA_ROOT, "docs")
ENV_PATH = os.environ.get("PM_ENV_PATH", os.path.join(ROOT, ".env"))
AUTH_DB_PATH = os.path.join(DATA_ROOT, "users.db")
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SUPPORTED_EXTENSIONS = (".pdf", ".txt", ".md")

API_ENV_KEYS = {
    "base_url": "PM_BASE_URL",
    "chat_model": "PM_CHAT_MODEL",
    "embed_base_url": "PM_EMBED_BASE_URL",
    "embed_api_model": "PM_EMBED_API_MODEL",
}


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _update_env_file(path: str, updates: dict) -> None:
    """只更新指定 API 配置项，保留 .env 中的其他设置与注释。"""
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as env_file:
            lines = env_file.readlines()

    output = []
    handled = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key not in updates:
            output.append(line)
            continue
        if key not in handled and updates[key]:
            output.append(f"{key}={updates[key]}\n")
        handled.add(key)

    if output and not output[-1].endswith(("\n", "\r")):
        output[-1] += "\n"
    for key, value in updates.items():
        if key not in handled and value:
            output.append(f"{key}={value}\n")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as env_file:
        env_file.writelines(output)
    os.replace(temp_path, path)


def create_app(config: Config = None, upload_dir: str = None,
               env_path: str = None, auth_db_path: str = None,
               auth_required: bool = None) -> Flask:
    """应用工厂：所有状态挂到 app 上，避免模块级全局单例。"""
    upload_dir = upload_dir or UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    app = Flask(__name__,
                template_folder=os.path.join(ROOT, "templates"))
    if os.environ.get("PM_TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["UPLOAD_FOLDER"] = upload_dir
    app.config["PM_CONFIG"] = config
    app.config["PM_ENV_PATH"] = env_path or ENV_PATH
    app.extensions["rag_pipeline"] = None  # 懒加载：首请求才初始化
    app.extensions["rag_pipelines"] = {}
    app.extensions["pm_user_configs"] = {}
    app.extensions["pm_boot"] = time.time()
    if auth_required is None:
        auth_required = os.environ.get("PM_AUTH_REQUIRED", "1") != "0"
    init_auth(app, auth_db_path or AUTH_DB_PATH, auth_required)

    def current_user_key():
        return g.user["id"] if g.user else 0

    def get_current_config() -> Config:
        user_key = current_user_key()
        cached = app.extensions["pm_user_configs"].get(user_key)
        if cached is not None:
            return cached

        base = app.config["PM_CONFIG"] or Config()
        if user_key:
            values = load_user_api_config(
                user_key,
                {field: getattr(base, field) for field in API_ENV_KEYS} | {
                    "api_key": base.api_key,
                    "embed_api_key": base.embed_api_key,
                },
            )
            base = replace(base, **values)
        app.extensions["pm_user_configs"][user_key] = base
        return base

    def get_pipeline() -> RAGPipeline:
        user_key = current_user_key()
        if user_key not in app.extensions["rag_pipelines"]:
            logger.info("初始化 RAG Pipeline（首请求触发）")
            pipeline = RAGPipeline(get_current_config())
            app.extensions["rag_pipelines"][user_key] = pipeline
            app.extensions["rag_pipeline"] = pipeline
        return app.extensions["rag_pipelines"][user_key]

    # ---------------- 页面 ----------------
    @app.route("/")
    def index():
        return render_template("index.html")

    # ---------------- 探针 ----------------
    @app.route("/api/health")
    def health():
        """存活探针：常驻轻量，不加载知识库。"""
        return jsonify({"ok": True, "uptime": round(time.time() - app.extensions["pm_boot"], 1)})

    # ---------------- 业务接口 ----------------
    @app.route("/api/ingest", methods=["POST"])
    def ingest():
        uploads = [f for f in request.files.getlist("files") if f and f.filename]
        if not uploads:
            return jsonify({"ok": False, "error": "请选择要导入的文件"}), 400
        unsupported = [
            f.filename for f in uploads
            if os.path.splitext(f.filename)[1].lower() not in SUPPORTED_EXTENSIONS
        ]
        if unsupported:
            return jsonify({
                "ok": False,
                "error": "仅支持 PDF、TXT、MD 文件",
                "files": unsupported,
            }), 400

        pipe = get_pipeline()
        saved = []
        for f in uploads:
            dest = os.path.join(app.config["UPLOAD_FOLDER"],
                                os.path.basename(f.filename))
            f.save(dest)
            saved.append(dest)
        stats = pipe.ingest(saved or None)
        return jsonify({"ok": True, "stats": stats})

    @app.route("/api/query", methods=["POST"])
    def query():
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"ok": False, "error": "问题不能为空"}), 400
        try:
            return jsonify({"ok": True, **get_pipeline().query(question)})
        except Exception:
            logger.exception("问答处理失败")
            return jsonify({"ok": False, "error": "服务内部错误，请稍后重试"}), 500

    @app.route("/api/config", methods=["GET", "PUT"])
    def api_config():
        """管理员配置 API；密钥只接收写入，永不返回明文。"""
        if not can_manage_config():
            return jsonify({"ok": False, "error": "仅管理员可以修改 API 配置"}), 403
        if request.method == "PUT" and not valid_csrf():
            return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403

        current = get_current_config()
        if request.method == "GET":
            return jsonify({
                "ok": True,
                "api_key_configured": bool(current.api_key),
                "base_url": current.base_url,
                "chat_model": current.chat_model,
                "embed_api_key_configured": bool(current.embed_api_key),
                "embed_base_url": current.embed_base_url,
                "embed_api_model": current.embed_api_model,
            })

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "配置必须是 JSON 对象"}), 400

        values = {}
        for field in API_ENV_KEYS:
            value = data.get(field, getattr(current, field))
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                return jsonify({"ok": False, "error": f"{field} 格式无效"}), 400
            values[field] = value.strip()

        if not _is_http_url(values["base_url"]):
            return jsonify({"ok": False, "error": "生成 API 地址必须是 HTTP(S) URL"}), 400
        if values["embed_base_url"] and not _is_http_url(values["embed_base_url"]):
            return jsonify({"ok": False, "error": "嵌入 API 地址必须是 HTTP(S) URL"}), 400
        if not values["chat_model"] or not values["embed_api_model"]:
            return jsonify({"ok": False, "error": "模型名称不能为空"}), 400

        api_key = current.api_key
        embed_api_key = current.embed_api_key
        env_updates = {
            env_key: values[field]
            for field, env_key in API_ENV_KEYS.items()
            if field in data
        }

        if data.get("clear_api_key") is True:
            api_key = ""
            env_updates["PM_API_KEY"] = ""
        elif "api_key" in data:
            if not isinstance(data["api_key"], str) or any(
                    char in data["api_key"] for char in "\r\n"):
                return jsonify({"ok": False, "error": "api_key 格式无效"}), 400
            if data["api_key"].strip():
                api_key = data["api_key"].strip()
                env_updates["PM_API_KEY"] = api_key

        if data.get("clear_embed_api_key") is True:
            embed_api_key = ""
            env_updates["PM_EMBED_API_KEY"] = ""
        elif "embed_api_key" in data:
            if not isinstance(data["embed_api_key"], str) or any(
                    char in data["embed_api_key"] for char in "\r\n"):
                return jsonify({"ok": False, "error": "embed_api_key 格式无效"}), 400
            if data["embed_api_key"].strip():
                embed_api_key = data["embed_api_key"].strip()
                env_updates["PM_EMBED_API_KEY"] = embed_api_key

        if embed_api_key and not values["embed_base_url"]:
            return jsonify({"ok": False, "error": "配置嵌入密钥时必须填写嵌入 API 地址"}), 400

        updated = replace(current, api_key=api_key, embed_api_key=embed_api_key,
                          **values)
        user_key = current_user_key()
        try:
            if user_key:
                save_user_api_config(
                    user_key,
                    {field: getattr(updated, field) for field in API_ENV_KEYS} | {
                        "api_key": updated.api_key,
                        "embed_api_key": updated.embed_api_key,
                    },
                )
            else:
                _update_env_file(app.config["PM_ENV_PATH"], env_updates)
        except (OSError, sqlite3.Error):
            logger.exception("API 配置写入失败")
            return jsonify({"ok": False, "error": "配置写入失败"}), 500

        app.extensions["pm_user_configs"][user_key] = updated
        app.extensions["rag_pipelines"].pop(user_key, None)
        app.extensions["rag_pipeline"] = None
        return jsonify({
            "ok": True,
            "message": "配置已保存并生效",
            "api_key_configured": bool(updated.api_key),
            "embed_api_key_configured": bool(updated.embed_api_key),
        })

    @app.route("/api/stats")
    def stats():
        pipe = get_pipeline()
        return jsonify({
            "ok": True,
            "chunks": len(pipe.store),
            "sources": pipe.store.sources,
            "embed_mode": pipe.embedder.mode,
            "llm_model": pipe.config.chat_model if pipe.config.api_key else "extractive",
            "llm_configured": bool(pipe.config.api_key),
            "retrieval_mode": pipe.config.retrieval_mode,
            "top_k": pipe.config.top_k,
            "supported_extensions": list(SUPPORTED_EXTENSIONS),
            "max_upload_mb": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
            "uptime": round(time.time() - app.extensions["pm_boot"], 1),
        })

    # ---------------- 统一错误处理 ----------------
    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"ok": False, "error": "接口不存在"}), 404

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({"ok": False, "error": "上传文件超过 64MB 限制"}), 413

    @app.errorhandler(500)
    def server_error(_):
        return jsonify({"ok": False, "error": "服务内部错误"}), 500

    return app
