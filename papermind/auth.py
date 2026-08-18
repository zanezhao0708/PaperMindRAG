"""本地账户与第三方 OAuth 登录。"""
from datetime import timedelta
from ipaddress import ip_address
import os
import re
import secrets
import sqlite3
import time

from authlib.integrations.flask_client import OAuth
from flask import (Blueprint, current_app, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash


auth_bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
API_CONFIG_FIELDS = (
    "api_key",
    "base_url",
    "chat_model",
    "embed_api_key",
    "embed_base_url",
    "embed_api_model",
)
OAUTH_HANDOFF_TTL = 10 * 60


def _connect():
    conn = sqlite3.connect(current_app.config["PM_AUTH_DB_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _user_dict(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "avatar_url": row["avatar_url"] or "",
    }


def _load_user(user_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, avatar_url FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _user_dict(row)


def ensure_csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_csrf():
    supplied = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    return bool(supplied and expected and secrets.compare_digest(supplied, expected))


def _is_loopback(remote_addr):
    try:
        addr = ip_address((remote_addr or "").split("%", 1)[0])
    except ValueError:
        return False
    return addr.is_loopback or bool(
        addr.version == 6 and addr.ipv4_mapped and addr.ipv4_mapped.is_loopback
    )


def _account_switcher_enabled():
    return (
        os.environ.get("PM_ACCOUNT_SWITCHER") == "1"
        and _is_loopback(request.remote_addr)
    )


def _local_accounts():
    if not _account_switcher_enabled():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, email, display_name, avatar_url, password_hash IS NOT NULL AS has_password
               FROM users ORDER BY id"""
        ).fetchall()
    return [
        {
            **_user_dict(row),
            "has_password": bool(row["has_password"]),
        }
        for row in rows
    ]


def load_user_api_config(user_id, defaults):
    """Load a user's local API config, migrating the legacy config once."""
    with _connect() as conn:
        row = conn.execute(
            """SELECT api_key, base_url, chat_model, embed_api_key,
                      embed_base_url, embed_api_model
               FROM user_api_configs WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        if row:
            return {field: row[field] for field in API_CONFIG_FIELDS}

        first_user = conn.execute("SELECT MIN(id) AS id FROM users").fetchone()
        values = {
            field: str(defaults.get(field) or "")
            for field in API_CONFIG_FIELDS
        }
        if not first_user or user_id != first_user["id"]:
            values["api_key"] = ""
            values["embed_api_key"] = ""
        conn.execute(
            """INSERT INTO user_api_configs
               (user_id, api_key, base_url, chat_model, embed_api_key,
                embed_base_url, embed_api_model)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, *(values[field] for field in API_CONFIG_FIELDS)),
        )
    return values


def save_user_api_config(user_id, values):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO user_api_configs
               (user_id, api_key, base_url, chat_model, embed_api_key,
                embed_base_url, embed_api_model, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 api_key = excluded.api_key,
                 base_url = excluded.base_url,
                 chat_model = excluded.chat_model,
                 embed_api_key = excluded.embed_api_key,
                 embed_base_url = excluded.embed_base_url,
                 embed_api_model = excluded.embed_api_model,
                 updated_at = CURRENT_TIMESTAMP""",
            (user_id, *(str(values.get(field) or "") for field in API_CONFIG_FIELDS)),
        )


def _load_user_preferences(user_id):
    with _connect() as conn:
        row = conn.execute(
            "SELECT theme FROM user_preferences WHERE user_id = ?", (user_id,)
        ).fetchone()
    return {"theme": row["theme"] if row else ""}


def _save_user_preferences(user_id, theme):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO user_preferences (user_id, theme, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                 theme = excluded.theme,
                 updated_at = CURRENT_TIMESTAMP""",
            (user_id, theme),
        )


def can_manage_config():
    """本机用户可配置；公网部署仅允许指定管理员邮箱。"""
    if _is_loopback(request.remote_addr):
        return True
    admin_email = os.environ.get("PM_ADMIN_EMAIL", "").strip().lower()
    return bool(g.user and admin_email and g.user["email"].lower() == admin_email)


def _sign_in(user_id):
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    ensure_csrf_token()


def _provider_status():
    return current_app.extensions["pm_oauth_providers"]


def _oauth_handoffs():
    handoffs = current_app.extensions["pm_oauth_handoffs"]
    now = time.monotonic()
    expired = [
        token for token, item in handoffs.items()
        if now - item["created_at"] > OAUTH_HANDOFF_TTL
    ]
    for token in expired:
        handoffs.pop(token, None)
    return handoffs


def _finish_oauth_handoff(token, user_id=None, error=""):
    item = _oauth_handoffs().get(token)
    if not item:
        return False
    item["user_id"] = user_id
    item["error"] = error
    return True


def _oauth_profile(provider, remote, token):
    if provider == "github":
        response = remote.get("user", token=token)
        response.raise_for_status()
        profile = response.json()
        email = profile.get("email")
        if not email:
            response = remote.get("user/emails", token=token)
            response.raise_for_status()
            emails = response.json()
            verified = [item for item in emails if item.get("verified")]
            preferred = next((item for item in verified if item.get("primary")), None)
            email = (preferred or (verified[0] if verified else {})).get("email")
        return {
            "provider_id": str(profile.get("id") or ""),
            "email": email,
            "display_name": profile.get("name") or profile.get("login"),
            "avatar_url": profile.get("avatar_url") or "",
        }

    profile = token.get("userinfo") or {}
    if provider == "google" and profile.get("email_verified") is not True:
        raise ValueError("Google 账户邮箱尚未验证")
    email = profile.get("email") or profile.get("preferred_username")
    return {
        "provider_id": str(profile.get("oid") or profile.get("sub") or ""),
        "email": email,
        "display_name": profile.get("name") or email,
        "avatar_url": profile.get("picture") or "",
    }


def _oauth_user(provider, profile):
    provider_id = profile["provider_id"]
    email = (profile.get("email") or "").strip().lower()
    if not provider_id or not EMAIL_RE.fullmatch(email):
        raise ValueError("第三方账户没有提供可用邮箱")

    with _connect() as conn:
        row = conn.execute(
            """SELECT users.id, users.email, users.display_name, users.avatar_url
               FROM user_identities
               JOIN users ON users.id = user_identities.user_id
               WHERE provider = ? AND provider_user_id = ?""",
            (provider, provider_id),
        ).fetchone()
        if row:
            return _user_dict(row)

        existing = conn.execute(
            "SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ).fetchone()
        if existing:
            raise ValueError("该邮箱已注册，请先使用邮箱密码登录")

        cursor = conn.execute(
            """INSERT INTO users (email, password_hash, display_name, avatar_url)
               VALUES (?, NULL, ?, ?)""",
            (email, (profile.get("display_name") or email).strip()[:80],
             profile.get("avatar_url") or ""),
        )
        user_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO user_identities (provider, provider_user_id, user_id)
               VALUES (?, ?, ?)""",
            (provider, provider_id, user_id),
        )
    return _load_user(user_id)


@auth_bp.route("/login")
def login_page():
    if g.user and current_app.config["PM_AUTH_REQUIRED"]:
        return redirect(url_for("index"))
    accounts = _local_accounts()
    selected_id = request.args.get("account", type=int)
    selected_account = next(
        (account for account in accounts if account["id"] == selected_id), None
    )
    return render_template(
        "login.html",
        csrf_token=ensure_csrf_token(),
        providers=_provider_status(),
        oauth_error=request.args.get("error", ""),
        local_accounts=accounts,
        selected_account=selected_account,
        initial_mode="register" if request.args.get("mode") == "register" else "login",
    )


@auth_bp.route("/auth/register", methods=["POST"])
def register():
    if not valid_csrf():
        return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    display_name = str(data.get("display_name") or "").strip()
    if not EMAIL_RE.fullmatch(email):
        return jsonify({"ok": False, "error": "请输入有效邮箱地址"}), 400
    if not 8 <= len(password) <= 128:
        return jsonify({"ok": False, "error": "密码长度需要为 8–128 个字符"}), 400
    display_name = (display_name or email.split("@", 1)[0])[:80]

    try:
        with _connect() as conn:
            cursor = conn.execute(
                """INSERT INTO users (email, password_hash, display_name)
                   VALUES (?, ?, ?)""",
                (email, generate_password_hash(password), display_name),
            )
            user_id = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "该邮箱已注册"}), 409

    _sign_in(user_id)
    return jsonify({"ok": True, "user": _load_user(user_id)})


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    if not valid_csrf():
        return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password = str(data.get("password") or "")
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, email, password_hash, display_name, avatar_url
               FROM users WHERE email = ? COLLATE NOCASE""",
            (email,),
        ).fetchone()
    if not row or not row["password_hash"] or not check_password_hash(
            row["password_hash"], password):
        return jsonify({"ok": False, "error": "邮箱或密码错误"}), 401
    _sign_in(row["id"])
    return jsonify({"ok": True, "user": _user_dict(row)})


@auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    if not valid_csrf():
        return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/auth/me")
def me():
    if not g.user:
        return jsonify({"ok": False, "error": "尚未登录"}), 401
    body = {
        "ok": True,
        "user": g.user,
        "csrf_token": ensure_csrf_token(),
        "can_configure": can_manage_config(),
        "preferences": _load_user_preferences(g.user["id"]),
    }
    if _account_switcher_enabled():
        body["accounts"] = _local_accounts()
    return jsonify(body)


@auth_bp.route("/auth/preferences", methods=["PUT"])
def preferences():
    if not g.user:
        return jsonify({"ok": False, "error": "请先登录"}), 401
    if not valid_csrf():
        return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403
    data = request.get_json(silent=True) or {}
    theme = str(data.get("theme") or "")
    if theme not in ("light", "dark"):
        return jsonify({"ok": False, "error": "主题设置无效"}), 400
    _save_user_preferences(g.user["id"], theme)
    return jsonify({"ok": True, "preferences": {"theme": theme}})


@auth_bp.route("/auth/oauth/<provider>")
def oauth_start(provider):
    if provider not in _provider_status():
        return redirect(url_for("auth.login_page", error="不支持该登录方式"))
    if not _provider_status()[provider]:
        return redirect(url_for("auth.login_page", error=f"{provider.title()} 登录尚未配置"))
    handoff = request.args.get("handoff", "")
    if handoff:
        if not _account_switcher_enabled() or handoff not in _oauth_handoffs():
            return redirect(url_for("auth.login_page", error="桌面登录请求已过期"))
        session["oauth_handoff"] = handoff
    remote = current_app.extensions["pm_oauth"].create_client(provider)
    callback = url_for("auth.oauth_callback", provider=provider, _external=True)
    return remote.authorize_redirect(callback)


@auth_bp.route("/auth/oauth/desktop/<provider>", methods=["POST"])
def oauth_desktop_start(provider):
    if not _account_switcher_enabled():
        return jsonify({"ok": False, "error": "仅桌面版支持系统浏览器登录"}), 404
    if not valid_csrf():
        return jsonify({"ok": False, "error": "页面已过期，请刷新后重试"}), 403
    if provider not in _provider_status() or not _provider_status()[provider]:
        return jsonify({"ok": False, "error": "登录方式未配置"}), 400

    token = secrets.token_urlsafe(32)
    _oauth_handoffs()[token] = {
        "created_at": time.monotonic(),
        "user_id": None,
        "error": "",
    }
    return jsonify({
        "ok": True,
        "token": token,
        "url": url_for("auth.oauth_start", provider=provider,
                       handoff=token, _external=True),
    })


@auth_bp.route("/auth/oauth/desktop/status/<token>")
def oauth_desktop_status(token):
    if not _account_switcher_enabled():
        return jsonify({"ok": False, "error": "仅桌面版支持系统浏览器登录"}), 404
    item = _oauth_handoffs().get(token)
    if not item:
        return jsonify({"ok": False, "error": "登录请求已过期，请重试"}), 410
    if item["error"]:
        _oauth_handoffs().pop(token, None)
        return jsonify({"ok": False, "error": item["error"]}), 400
    if item["user_id"] is None:
        return jsonify({"ok": True, "pending": True}), 202

    user_id = item["user_id"]
    _oauth_handoffs().pop(token, None)
    _sign_in(user_id)
    return jsonify({"ok": True, "pending": False, "user": _load_user(user_id)})


@auth_bp.route("/auth/oauth/<provider>/callback")
def oauth_callback(provider):
    if provider not in _provider_status() or not _provider_status()[provider]:
        return redirect(url_for("auth.login_page", error="登录方式未配置"))
    handoff = session.get("oauth_handoff", "")
    try:
        remote = current_app.extensions["pm_oauth"].create_client(provider)
        token = remote.authorize_access_token()
        user = _oauth_user(provider, _oauth_profile(provider, remote, token))
        if handoff and _finish_oauth_handoff(handoff, user_id=user["id"]):
            session.clear()
            return render_template("oauth_complete.html", ok=True, error="")
        _sign_in(user["id"])
        return redirect(url_for("index"))
    except Exception as error:
        current_app.logger.warning("%s OAuth 登录失败: %s", provider, error)
        message = str(error) if isinstance(error, ValueError) else "第三方登录失败，请重试"
        if handoff and _finish_oauth_handoff(handoff, error=message):
            session.clear()
            return render_template("oauth_complete.html", ok=False, error=message), 400
        return redirect(url_for("auth.login_page", error=message))


def _session_secret(db_path):
    configured = os.environ.get("PM_SECRET_KEY", "")
    if configured:
        if len(configured) < 32:
            raise ValueError("PM_SECRET_KEY 至少需要 32 个字符")
        return configured
    secret_path = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".session_secret")
    os.makedirs(os.path.dirname(secret_path), exist_ok=True)
    try:
        with open(secret_path, encoding="utf-8") as secret_file:
            saved = secret_file.read().strip()
        if saved:
            return saved
    except FileNotFoundError:
        pass
    secret = secrets.token_urlsafe(48)
    try:
        with open(secret_path, "x", encoding="utf-8") as secret_file:
            secret_file.write(secret)
    except FileExistsError:
        with open(secret_path, encoding="utf-8") as secret_file:
            return secret_file.read().strip()
    return secret


def _init_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT,
                display_name TEXT NOT NULL,
                avatar_url TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_identities (
                provider TEXT NOT NULL,
                provider_user_id TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY (provider, provider_user_id)
            );
            CREATE TABLE IF NOT EXISTS user_api_configs (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                api_key TEXT NOT NULL DEFAULT '',
                base_url TEXT NOT NULL DEFAULT '',
                chat_model TEXT NOT NULL DEFAULT '',
                embed_api_key TEXT NOT NULL DEFAULT '',
                embed_base_url TEXT NOT NULL DEFAULT '',
                embed_api_model TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                theme TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def init_auth(app, db_path, required=True):
    """初始化认证数据库、会话和 OAuth 客户端。"""
    app.config.update(
        SECRET_KEY=_session_secret(db_path),
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("PM_COOKIE_SECURE") == "1",
        PM_AUTH_DB_PATH=db_path,
        PM_AUTH_REQUIRED=required,
    )
    _init_db(db_path)

    oauth = OAuth(app)
    provider_env = {
        "github": ("PM_GITHUB_CLIENT_ID", "PM_GITHUB_CLIENT_SECRET"),
        "google": ("PM_GOOGLE_CLIENT_ID", "PM_GOOGLE_CLIENT_SECRET"),
        "microsoft": ("PM_MICROSOFT_CLIENT_ID", "PM_MICROSOFT_CLIENT_SECRET"),
    }
    providers = {
        name: bool(os.environ.get(client_id) and os.environ.get(client_secret))
        for name, (client_id, client_secret) in provider_env.items()
    }
    app.extensions["pm_oauth_providers"] = providers
    app.extensions["pm_oauth_handoffs"] = {}

    oauth.register(
        "github",
        client_id=os.environ.get("PM_GITHUB_CLIENT_ID"),
        client_secret=os.environ.get("PM_GITHUB_CLIENT_SECRET"),
        access_token_url="https://github.com/login/oauth/access_token",
        authorize_url="https://github.com/login/oauth/authorize",
        api_base_url="https://api.github.com/",
        client_kwargs={
            "scope": "read:user user:email",
            "code_challenge_method": "S256",
        },
    )
    oauth.register(
        "google",
        client_id=os.environ.get("PM_GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("PM_GOOGLE_CLIENT_SECRET"),
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
        },
    )
    tenant = os.environ.get("PM_MICROSOFT_TENANT", "common")
    oauth.register(
        "microsoft",
        client_id=os.environ.get("PM_MICROSOFT_CLIENT_ID"),
        client_secret=os.environ.get("PM_MICROSOFT_CLIENT_SECRET"),
        server_metadata_url=(
            f"https://login.microsoftonline.com/{tenant}/v2.0/"
            ".well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid profile email User.Read",
            "code_challenge_method": "S256",
        },
    )
    app.extensions["pm_oauth"] = oauth
    app.register_blueprint(auth_bp)

    @app.before_request
    def load_authenticated_user():
        g.user = _load_user(session.get("user_id")) if session.get("user_id") else None
        if session.get("user_id") and not g.user:
            session.clear()

    @app.before_request
    def require_authentication():
        if not app.config["PM_AUTH_REQUIRED"] or g.user:
            return None
        endpoint = request.endpoint or ""
        if endpoint == "health" or endpoint == "static" or endpoint.startswith("auth."):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "请先登录"}), 401
        return redirect(url_for("auth.login_page"))
