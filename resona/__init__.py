import os
import secrets
from pathlib import Path

from flask import Flask, g, redirect, request, session, url_for, render_template
from flask_session import Session
from cachelib import SimpleCache
from redis import Redis
from dotenv import load_dotenv

from .db import close_db, init_app as init_db_app


load_dotenv()


def _session_cookie_secure():
    """Default the session cookie to Secure unless the deployment is plainly local.

    An explicit SESSION_COOKIE_SECURE always wins. When it is absent the cookie
    must not silently fall back to cleartext on a public HTTPS deployment, so we
    derive it from PUBLIC_BASE_URL and only relax it for http:// origins.
    """
    configured = os.getenv("SESSION_COOKIE_SECURE")
    if configured is not None and configured.strip():
        return configured.strip() == "1"
    public_base_url = os.getenv("PUBLIC_BASE_URL", "").strip().lower()
    if public_base_url.startswith("http://"):
        return False
    return True


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.getenv("SECRET_KEY", secrets.token_hex(32)),
        DATABASE=str(Path(app.instance_path) / "resona.sqlite3"),
        STORAGE_ROOT=str(Path(app.instance_path) / "storage"),
        USER_QUOTA_BYTES=1_073_741_824,
        CLOSEAI_BASE_URL=os.getenv("CLOSEAI_BASE_URL", "https://api.openai-proxy.org"),
        CLOSEAI_API_KEY=os.getenv("CLOSEAI_API_KEY", ""),
        CLOSEAI_MODEL=os.getenv("CLOSEAI_MODEL", "gpt-5.6-sol"),
        CLOSEAI_READ_TIMEOUT_SECONDS=int(os.getenv("CLOSEAI_READ_TIMEOUT_SECONDS", "300")),
        CLOSEAI_PREFER_ENV=os.getenv("CLOSEAI_PREFER_ENV", "0") == "1",
        RESEND_API_KEY=os.getenv("RESEND_API_KEY", ""),
        RESEND_FROM_EMAIL=os.getenv("RESEND_FROM_EMAIL", "").strip().lower(),
        RESEND_FROM_NAME=os.getenv("RESEND_FROM_NAME", "Resona").strip(),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
        EXABYTE_OIDC_ISSUER=os.getenv("EXABYTE_OIDC_ISSUER", "https://accounts.exabyte.org.cn").strip().rstrip("/"),
        EXABYTE_OIDC_CALLBACK_URL=os.getenv("EXABYTE_OIDC_CALLBACK_URL", "").strip(),
        EXABYTE_OIDC_SCOPES=os.getenv("EXABYTE_OIDC_SCOPES", "openid profile email").strip(),
        EXABYTE_AVATAR_ROOT=str(Path(app.instance_path) / "exabyte_avatars"),
        REDIS_URL=os.getenv("REDIS_URL", "").strip(),
        AGENT_MAX_STEPS=int(os.getenv("AGENT_MAX_STEPS", "80")),
        CAPTCHA_CHALLENGE_COUNT=int(os.getenv("CAPTCHA_CHALLENGE_COUNT", "50")),
        CAPTCHA_CHALLENGE_DIFFICULTY=int(os.getenv("CAPTCHA_CHALLENGE_DIFFICULTY", "4")),
        AGENT_TRACE=False,
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin").strip().lower(),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", ""),
        ADMIN_EMAIL=os.getenv("ADMIN_EMAIL", "").strip().lower(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_session_cookie_secure(),
        SESSION_COOKIE_NAME="resona_session",
        SESSION_PERMANENT=False,
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,
    )
    if os.getenv("DATABASE_PATH"):
        app.config["DATABASE"] = os.getenv("DATABASE_PATH")
    if os.getenv("RESONA_STORAGE_ROOT"):
        app.config["STORAGE_ROOT"] = os.getenv("RESONA_STORAGE_ROOT")
    if os.getenv("RESONA_USER_QUOTA_BYTES"):
        app.config["USER_QUOTA_BYTES"] = int(os.getenv("RESONA_USER_QUOTA_BYTES"))
    if test_config:
        app.config.update(test_config)

    if app.config.get("REDIS_URL"):
        app.config["SESSION_TYPE"] = "redis"
        app.config["SESSION_REDIS"] = Redis.from_url(app.config["REDIS_URL"])
    else:
        app.config["SESSION_TYPE"] = "cachelib"
        app.config["SESSION_CACHELIB"] = SimpleCache(default_timeout=12 * 60 * 60)
    Session(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["STORAGE_ROOT"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["EXABYTE_AVATAR_ROOT"]).mkdir(parents=True, exist_ok=True)
    init_db_app(app)

    from .auth import auth_bp
    from .admin import admin_bp
    from .player import player_bp
    from .agent import agent_bp
    from .storage import storage_bp
    from .account import account_bp
    from .captcha import captcha_bp, init_captcha
    from .exabyte_oidc import exabyte_bp, init_exabyte_oidc
    from .exabyte_webhook import webhook_bp
    from .exabyte_maintenance import init_app as init_exabyte_maintenance
    from .legal import legal_bp

    init_captcha(app)
    init_exabyte_oidc(app)
    init_exabyte_maintenance(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(player_bp)
    app.register_blueprint(agent_bp)
    app.register_blueprint(storage_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(captcha_bp)
    app.register_blueprint(exabyte_bp)
    app.register_blueprint(webhook_bp)
    app.register_blueprint(legal_bp)

    @app.get("/")
    def home():
        return redirect(url_for("player.index" if g.get("user") else "auth.login"))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/lite/privacy")
    def resonaliteprivacy():
        return render_template("lite/privacy.html")

    @app.route("/lite")
    def resonaliteprivacy():
        return render_template("lite/index.html")

    @app.before_request
    def load_user():
        from .db import get_db
        from .exabyte_oidc import exabyte_access_allowed

        user_id = session.get("user_id")
        g.user = get_db().execute(
            "SELECT id, username, email, display_name, email_verified_at, password_login_enabled, is_admin, is_demo, demo_enabled, session_version, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone() if user_id else None
        if g.user and (
            session.get("session_version", 0) != g.user["session_version"]
            or (g.user["is_demo"] and not g.user["demo_enabled"])
            or not exabyte_access_allowed(g.user["id"])
        ):
            session.clear()
            g.user = None

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-src 'self'; "
            "worker-src 'self' blob:; font-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'self'; object-src 'none'",
        )
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=(), interest-cohort=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.is_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.context_processor
    def inject_csrf():
        from .exabyte_oidc import exabyte_is_configured
        from .user_controls import get_user_controls

        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return {"csrf_token": token, "user_controls": get_user_controls(), "exabyte_configured": exabyte_is_configured()}

    app.teardown_appcontext(close_db)
    return app
