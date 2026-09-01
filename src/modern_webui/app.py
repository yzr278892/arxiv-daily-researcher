"""ASGI management application for ArXiv Daily Researcher.

All writes go through portable configuration helpers, the SQLite ledger, and
the Worker-owned trigger queue.  The browser service stays presentation-only.
"""

from __future__ import annotations

import json
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any

from starlette import status
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _PROJECT_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from modern_webui import backend  # noqa: E402
from modern_webui.auth import (  # noqa: E402
    Account,
    _clear_attempts,
    _configured,
    _record_failed_attempt,
    _remaining_retry_seconds,
    account_is_owner,
    account_session_marker,
    find_account,
    hash_password,
    read_auth_config,
    serialize_accounts,
    session_secret,
    validate_password,
    validate_username,
    verify_password_hash,
)
from modern_webui.i18n import client_catalogue  # noqa: E402
from utils.config_io import read_env, write_env  # noqa: E402


STATIC_DIR = Path(__file__).resolve().parent / "static"
_MAX_JSON_BYTES = 1_000_000
_ASSET_VERSION_TOKEN = "__ASSET_VERSION__"


class VersionedStaticFiles(StaticFiles):
    """Cache fingerprinted assets aggressively without pinning direct links."""

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == status.HTTP_200_OK:
            # The SPA document injects a file fingerprint into every CSS/JS
            # URL.  A direct asset URL remains revalidatable for operators and
            # tests, while a fingerprinted URL avoids repeat transfers over a
            # LAN or Tailscale connection.
            query = scope.get("query_string", b"")
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable"
                if b"v=" in query
                else "no-cache"
            )
        return response


def _frontend_asset_version() -> str:
    """Return a cheap content-change token for the two SPA entry assets."""

    parts: list[str] = []
    for name in ("app.css", "app.js"):
        try:
            stat = (STATIC_DIR / name).stat()
            parts.append(f"{stat.st_mtime_ns:x}-{stat.st_size:x}")
        except OSError:
            # A development edit can briefly race with a request.  The HTML
            # still loads normally and the next refresh gets the new token.
            parts.append("missing")
    return ".".join(parts)


async def _blocking_call(function: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run legacy synchronous storage/network helpers outside Uvicorn's loop.

    The UI deliberately shares the existing backend helpers.  Those helpers
    perform SQLite access, report-directory walks, file writes and
    occasional network connection tests synchronously.  Calling them directly
    from an ``async`` endpoint blocks every other request served by this
    process, which made the lightweight UI feel less responsive than expected.
    """

    if kwargs:
        function = partial(function, *args, **kwargs)
        return await run_in_threadpool(function)
    return await run_in_threadpool(function, *args)


def _auth_config():
    return read_auth_config(read_env())


def _session_secret() -> str:
    return session_secret(_auth_config())


def _modern_session_authenticated(request: Request, config: Any | None = None) -> bool:
    """Check a browser session against an optionally preloaded auth config."""
    if config is None:
        config = _auth_config()
    if not config.enabled:
        return True
    if not _configured(config):
        return False
    username = request.session.get("username")
    last_activity = request.session.get("last_activity")
    if not isinstance(last_activity, (int, float)):
        request.session.clear()
        return False
    if time.time() - last_activity > config.session_timeout_minutes * 60:
        request.session.clear()
        return False
    account = find_account(config, username)
    if account is None:
        request.session.clear()
        return False
    marker = request.session.get("account_marker")
    if not isinstance(marker, str) or marker != account_session_marker(account):
        request.session.clear()
        return False
    request.session["last_activity"] = time.time()
    return True


def _require_session(request: Request) -> None:
    config = _auth_config()
    if not config.enabled:
        return
    if not _configured(config):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="管理员账户尚未初始化。",
        )
    if not _modern_session_authenticated(request, config):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")


def _actor(request: Request) -> str:
    config = _auth_config()
    if not config.enabled:
        return "local"
    username = request.session.get("username")
    if not isinstance(username, str) or find_account(config, username) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录。")
    return username


def _require_owner(request: Request) -> tuple[str, Any]:
    actor = _actor(request)
    config = _auth_config()
    if not account_is_owner(config, actor):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有账户所有者可以管理账户。")
    return actor, config


async def _payload(request: Request, *, limit: int = _MAX_JSON_BYTES) -> dict[str, Any]:
    header = request.headers.get("content-length")
    if header:
        try:
            if int(header) > limit:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="请求内容过大。")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求长度无效。") from None
    try:
        value = await request.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求必须是 JSON 对象。") from None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求必须是 JSON 对象。")
    return value


async def _optional_payload(request: Request) -> dict[str, Any]:
    """Keep legacy POST endpoints usable when they intentionally send no body."""

    if request.headers.get("content-length") in {None, "0"}:
        return {}
    return await _payload(request)


def _safe_error(exc: Exception) -> HTTPException:
    if isinstance(exc, backend.ModernWebUIError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="请求未完成，请查看系统日志。")


async def health(_request: Request) -> Response:
    return Response("ok", media_type="text/plain")


async def translations_get(_request: Request) -> JSONResponse:
    """Expose the shared, non-sensitive UI wording before sign-in."""

    return JSONResponse({"items": client_catalogue()})


async def auth_status(request: Request) -> JSONResponse:
    config = _auth_config()
    configured = _configured(config) if config.enabled else True
    username = request.session.get("username") if configured else None
    return JSONResponse(
        {
            "enabled": config.enabled,
            "configured": configured,
            "authenticated": _modern_session_authenticated(request, config) if configured else False,
            "username": username if isinstance(username, str) else None,
            "session_timeout_minutes": config.session_timeout_minutes,
        }
    )


async def setup_account(request: Request) -> JSONResponse:
    payload = await _payload(request)
    config = _auth_config()
    if config.enabled and _configured(config):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="管理员账户已经初始化。")
    if str(payload.get("action") or "") == "skip":
        def _skip_setup() -> None:
            values = read_env()
            # Disabling the trusted-LAN gate is an explicit fresh-start choice,
            # so stale owner credentials and
            # managed-account records must not remain in the shared .env.
            values.update(
                {
                    "WEBUI_AUTH_ENABLED": "false",
                    "WEBUI_ADMIN_USERNAME": "",
                    "WEBUI_ADMIN_PASSWORD_HASH": "",
                    "WEBUI_ACCOUNTS": "",
                }
            )
            write_env(values)

        await _blocking_call(_skip_setup)
        request.session.clear()
        return JSONResponse({"ok": True, "enabled": False})
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    confirmation = str(payload.get("password_confirmation") or "")
    if message := validate_username(username):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if message := validate_password(password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if password != confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="两次输入的密码不一致。")
    owner = await _blocking_call(lambda: Account(username, hash_password(password), is_owner=True))

    def _save_setup() -> None:
        values = read_env()
        values.update(
            {
                "WEBUI_AUTH_ENABLED": "true",
                "WEBUI_ADMIN_USERNAME": owner.username,
                "WEBUI_ADMIN_PASSWORD_HASH": owner.password_hash,
                "WEBUI_ACCOUNTS": serialize_accounts((owner,)),
            }
        )
        write_env(values)

    await _blocking_call(_save_setup)
    request.session.clear()
    request.session["username"] = owner.username
    request.session["account_marker"] = account_session_marker(owner)
    request.session["last_activity"] = time.time()
    return JSONResponse({"ok": True, "configured": True})


async def login(request: Request) -> JSONResponse:
    payload = await _payload(request)
    username = str(payload.get("username") or "")[:64].strip()
    password = str(payload.get("password") or "")[:512]
    config = _auth_config()
    if not config.enabled:
        request.session.clear()
        request.session["username"] = "local"
        request.session["last_activity"] = time.time()
        return JSONResponse({"ok": True})
    if not _configured(config):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="请先初始化管理员账户。")
    remaining = _remaining_retry_seconds(username or config.username)
    if remaining:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f"登录尝试过于频繁，请在 {remaining} 秒后重试。")
    account = find_account(config, username)
    password_ok = (
        await _blocking_call(verify_password_hash, account.password_hash, password) if account else False
    )
    if account is None or password_ok is not True:
        _record_failed_attempt(username or config.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误。")
    _clear_attempts(account.username)
    request.session.clear()
    request.session["username"] = account.username
    request.session["account_marker"] = account_session_marker(account)
    request.session["last_activity"] = time.time()
    return JSONResponse({"ok": True, "username": account.username})


async def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def settings_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse(await _blocking_call(backend.public_settings))


async def settings_put(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        result = await _blocking_call(
            backend.save_settings,
            payload.get("config"),
            payload.get("env"),
            payload.get("clear_env"),
        )
    except Exception as exc:
        raise _safe_error(exc) from exc
    return JSONResponse(result)


async def restart_worker(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        await _blocking_call(backend.request_worker_restart)
    except Exception as exc:
        raise _safe_error(exc) from exc
    return JSONResponse({"ok": True})


async def status_get(request: Request) -> JSONResponse:
    _require_session(request)
    kind = request.path_params.get("kind", "daily")
    if kind not in {"daily", "past", "trend", "history"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未知状态页面。")
    try:
        return JSONResponse(await _blocking_call(backend.run_status, kind))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def daily_status(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.run_status, "daily"))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def clear_stale_triggers(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.clear_stale_triggers))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def start_task(request: Request) -> JSONResponse:
    _require_session(request)
    mode = request.path_params.get("mode", "")
    payload = await _payload(request)
    try:
        return JSONResponse(await _blocking_call(backend.enqueue_task, mode, payload.get("args")))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def daily_start(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.enqueue_task, "daily_research"))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def stop_tasks(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _optional_payload(request)
    # ``/api/daily/stop`` predates scoped controls. Keep its intuitive
    # compatibility meaning while the generic endpoint receives the exact
    # status-card scope from the modern client.
    kind = payload.get("kind")
    if kind is None and request.url.path.endswith("/daily/stop"):
        kind = "daily"
    try:
        pids = await _blocking_call(backend.stop_active_tasks, kind)
    except Exception as exc:
        raise _safe_error(exc) from exc
    return JSONResponse({"ok": True, "pids": pids})


async def history_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.history_status))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def history_retry(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(
            await _blocking_call(backend.retry_history_task, request.path_params.get("request_id", ""))
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def papers_get(request: Request) -> JSONResponse:
    _require_session(request)
    filters = {key: value for key, value in request.query_params.items()}
    try:
        return JSONResponse(await _blocking_call(backend.paper_search, filters))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def favorites_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.preferences_summary))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def favorites_collect(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.collect_qualified_favorites))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def preference_put(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(await _blocking_call(backend.set_preference, payload))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def learned_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse(await _blocking_call(backend.learned_preference_terms))


async def extracted_keywords_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse({"items": await _blocking_call(backend.extracted_keywords)})


async def trend_templates_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse({"items": await _blocking_call(backend.list_trend_prompt_templates)})


async def trend_templates_put(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(
            {
                "items": await _blocking_call(
                    backend.save_trend_prompt_template, payload.get("name"), payload.get("text")
                )
            }
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def trend_templates_delete(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(
            {"items": await _blocking_call(backend.delete_trend_prompt_template, payload.get("name"))}
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def diagnostics_get(request: Request) -> JSONResponse:
    _require_session(request)
    raw_days = request.query_params.get("days", "7")
    try:
        days = None if raw_days == "all" else int(raw_days)
        return JSONResponse(await _blocking_call(backend.diagnostics, days))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def analytics_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(
            await _blocking_call(
                backend.analytics,
                request.query_params.get("range", "30d"),
                request.query_params.get("date_from"),
                request.query_params.get("date_to"),
            )
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def reports_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse(
        await _blocking_call(backend.list_reports, request.query_params.get("non_arxiv") == "1")
    )


async def report_file(request: Request) -> FileResponse:
    _require_session(request)
    try:
        path, media_type = await _blocking_call(backend.report_file, request.path_params.get("token", ""))
    except Exception as exc:
        raise _safe_error(exc) from exc
    return FileResponse(path, media_type=media_type, content_disposition_type="inline")


async def report_papers_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(
            {"items": await _blocking_call(backend.report_papers, request.path_params.get("token", ""))}
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def backups_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse({"items": await _blocking_call(backend.local_backups)})
    except Exception as exc:
        raise _safe_error(exc) from exc


async def backup_create(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _optional_payload(request)
    try:
        return JSONResponse(
            await _blocking_call(
                backend.create_local_backup,
                payload.get("config"),
                payload.get("env"),
            )
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def backup_export(request: Request) -> Response:
    _require_session(request)
    try:
        content, filename = await _blocking_call(backend.export_database_backup)
    except Exception as exc:
        raise _safe_error(exc) from exc
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def backup_restore(request: Request) -> JSONResponse:
    _require_session(request)
    filename = Path(request.headers.get("x-file-name", "backup.zip")).name
    body = await request.body()
    try:
        return JSONResponse(await _blocking_call(backend.restore_database_backup, body, filename))
    except Exception as exc:
        raise _safe_error(exc) from exc


async def configuration_export(request: Request) -> Response:
    _require_session(request)
    try:
        content, filename = await _blocking_call(backend.export_configuration)
    except Exception as exc:
        raise _safe_error(exc) from exc
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def webdav_post(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(
            await _blocking_call(
                backend.webdav_operation,
                str(payload.get("operation") or ""),
                payload.get("config"),
                payload.get("env"),
            )
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def connection_test(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(
            await _blocking_call(backend.connection_test, request.path_params.get("kind", ""), payload)
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def notification_test(request: Request) -> JSONResponse:
    _require_session(request)
    payload = await _payload(request)
    try:
        return JSONResponse(
            await _blocking_call(
                backend.test_notification,
                request.path_params.get("channel", ""),
                payload,
            )
        )
    except Exception as exc:
        raise _safe_error(exc) from exc


async def logs_get(request: Request) -> JSONResponse:
    _require_session(request)
    return JSONResponse({"items": await _blocking_call(backend.list_logs)})


async def log_get(request: Request) -> JSONResponse:
    _require_session(request)
    try:
        return JSONResponse(await _blocking_call(backend.read_log, request.path_params.get("token", "")))
    except Exception as exc:
        raise _safe_error(exc) from exc


def _persist_accounts(accounts: tuple[Account, ...]) -> None:
    owner = next(account for account in accounts if account.is_owner)
    values = read_env()
    values.update({"WEBUI_AUTH_ENABLED": "true", "WEBUI_ADMIN_USERNAME": owner.username, "WEBUI_ADMIN_PASSWORD_HASH": owner.password_hash, "WEBUI_ACCOUNTS": serialize_accounts(accounts)})
    write_env(values)


async def accounts_get(request: Request) -> JSONResponse:
    _require_session(request)
    actor = _actor(request)
    config = _auth_config()
    return JSONResponse(
        {
            "enabled": config.enabled,
            "actor": actor,
            "is_owner": account_is_owner(config, actor),
            "items": [
                {
                    "username": account.username,
                    "role": "所有者" if account.is_owner else "管理员",
                    "is_owner": account.is_owner,
                    "current": account.username == actor,
                }
                for account in config.accounts
            ],
        }
    )


async def account_change_password(request: Request) -> JSONResponse:
    _require_session(request)
    actor = _actor(request)
    payload = await _payload(request)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirmation = str(payload.get("password_confirmation") or "")
    config = _auth_config()
    account = find_account(config, actor)
    password_ok = (
        await _blocking_call(verify_password_hash, account.password_hash, current_password)
        if account
        else False
    )
    if account is None or password_ok is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前密码不正确。")
    if message := validate_password(new_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if new_password != confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="两次输入的密码不一致。")
    accounts = await _blocking_call(
        lambda: tuple(
            Account(
                item.username,
                hash_password(new_password) if item.username == actor else item.password_hash,
                item.is_owner,
            )
            for item in config.accounts
        )
    )
    await _blocking_call(_persist_accounts, accounts)
    # Changing a password invalidates the active browser session.  The user
    # explicitly authenticates with the new
    # password instead of keeping a pre-change session alive.
    request.session.clear()
    return JSONResponse({"ok": True})


async def account_add(request: Request) -> JSONResponse:
    _require_session(request)
    _actor_name, config = _require_owner(request)
    payload = await _payload(request)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    confirmation = str(payload.get("password_confirmation") or "")
    if message := validate_username(username):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if message := validate_password(password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if password != confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="两次输入的密码不一致。")
    if find_account(config, username) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该用户名已经存在。")
    if len(config.accounts) >= 20:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账户数量已达到上限。")
    account = await _blocking_call(lambda: Account(username, hash_password(password)))
    await _blocking_call(_persist_accounts, (*config.accounts, account))
    return JSONResponse({"ok": True})


async def account_reset(request: Request) -> JSONResponse:
    _require_session(request)
    _actor_name, config = _require_owner(request)
    payload = await _payload(request)
    target = str(payload.get("username") or "").strip()
    password = str(payload.get("new_password") or "")
    confirmation = str(payload.get("password_confirmation") or "")
    account = find_account(config, target)
    if account is None or account.is_owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="只能重置其他管理员的密码。")
    if message := validate_password(password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)
    if password != confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="两次输入的密码不一致。")
    accounts = await _blocking_call(
        lambda: tuple(
            Account(
                item.username,
                hash_password(password) if item.username == target else item.password_hash,
                item.is_owner,
            )
            for item in config.accounts
        )
    )
    await _blocking_call(_persist_accounts, accounts)
    return JSONResponse({"ok": True})


async def account_delete(request: Request) -> JSONResponse:
    _require_session(request)
    _actor_name, config = _require_owner(request)
    payload = await _payload(request)
    confirmed = str(payload.get("confirmed") or "").strip().lower()
    if confirmed not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请先确认删除该管理员账户。",
        )
    target = str(payload.get("username") or "").strip()
    account = find_account(config, target)
    if account is None or account.is_owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除所有者账户。")
    await _blocking_call(
        _persist_accounts,
        tuple(item for item in config.accounts if item.username != target),
    )
    return JSONResponse({"ok": True})


async def frontend(_request: Request) -> Response:
    """Serve a non-cacheable shell with fingerprinted CSS and JavaScript."""

    try:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - packaging failure safeguard
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="无法读取 WebUI 页面。",
        ) from exc
    body = html.replace(_ASSET_VERSION_TOKEN, _frontend_asset_version())
    return Response(
        body,
        media_type="text/html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code)


app = Starlette(
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/i18n", translations_get, methods=["GET"]),
        Route("/api/auth/status", auth_status, methods=["GET"]),
        Route("/api/auth/setup", setup_account, methods=["POST"]),
        Route("/api/auth/login", login, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/settings", settings_get, methods=["GET"]),
        Route("/api/settings", settings_put, methods=["PUT"]),
        Route("/api/system/restart-worker", restart_worker, methods=["POST"]),
        Route("/api/status/{kind}", status_get, methods=["GET"]),
        Route("/api/daily/status", daily_status, methods=["GET"]),
        Route("/api/triggers/stale", clear_stale_triggers, methods=["POST"]),
        Route("/api/tasks/stop", stop_tasks, methods=["POST"]),
        Route("/api/daily/stop", stop_tasks, methods=["POST"]),
        Route("/api/tasks/{mode}", start_task, methods=["POST"]),
        Route("/api/daily/run", daily_start, methods=["POST"]),
        Route("/api/history", history_get, methods=["GET"]),
        Route("/api/history/{request_id:str}/retry", history_retry, methods=["POST"]),
        Route("/api/papers", papers_get, methods=["GET"]),
        Route("/api/favorites", favorites_get, methods=["GET"]),
        Route("/api/favorites/collect", favorites_collect, methods=["POST"]),
        Route("/api/preferences", preference_put, methods=["PUT"]),
        Route("/api/learned-preferences", learned_get, methods=["GET"]),
        Route("/api/extracted-keywords", extracted_keywords_get, methods=["GET"]),
        Route("/api/trend/templates", trend_templates_get, methods=["GET"]),
        Route("/api/trend/templates", trend_templates_put, methods=["PUT"]),
        Route("/api/trend/templates/delete", trend_templates_delete, methods=["POST"]),
        Route("/api/diagnostics", diagnostics_get, methods=["GET"]),
        Route("/api/analytics", analytics_get, methods=["GET"]),
        Route("/api/reports", reports_get, methods=["GET"]),
        Route("/api/reports/{token:str}/file", report_file, methods=["GET"]),
        Route("/api/reports/{token:str}/papers", report_papers_get, methods=["GET"]),
        Route("/api/backups", backups_get, methods=["GET"]),
        Route("/api/backups/create", backup_create, methods=["POST"]),
        Route("/api/backups/export", backup_export, methods=["GET"]),
        Route("/api/backups/restore", backup_restore, methods=["POST"]),
        Route("/api/configuration/export", configuration_export, methods=["GET"]),
        Route("/api/webdav", webdav_post, methods=["POST"]),
        Route("/api/notifications/{channel:str}/test", notification_test, methods=["POST"]),
        Route("/api/connections/{kind:str}", connection_test, methods=["POST"]),
        Route("/api/logs", logs_get, methods=["GET"]),
        Route("/api/logs/{token:str}", log_get, methods=["GET"]),
        Route("/api/accounts", accounts_get, methods=["GET"]),
        Route("/api/accounts/change-password", account_change_password, methods=["POST"]),
        Route("/api/accounts/add", account_add, methods=["POST"]),
        Route("/api/accounts/reset", account_reset, methods=["POST"]),
        Route("/api/accounts/delete", account_delete, methods=["POST"]),
        Mount("/assets", app=VersionedStaticFiles(directory=STATIC_DIR), name="assets"),
        Route("/{path:path}", frontend, methods=["GET"]),
    ],
    exception_handlers={HTTPException: http_exception_handler},
)
# The management panel is often reached through a reverse proxy or a LAN
# rather than directly on localhost. Its JavaScript, stylesheet and shared
# translation catalogue compress very well; without middleware every full
# load transfers their uncompressed payloads. Starlette skips already
# compressed/binary responses, so report downloads and backup exports retain
# their existing behaviour.
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="adr_modern_session",
    max_age=7 * 24 * 60 * 60,
    same_site="strict",
    https_only=False,
)
