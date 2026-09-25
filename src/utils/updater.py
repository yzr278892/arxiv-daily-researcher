"""Release update availability checks.

This module deliberately never pulls code, rebuilds images, or restarts a
process.  A running application (especially a Docker container) cannot safely
replace itself.  It compares the packaged ``VERSION`` with the latest GitHub
Release and notifies the configured channels when an operator action is needed.
"""

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

REPO_URL = "https://github.com/yzr278892/arxiv-daily-researcher"
GITHUB_API_LATEST = "https://api.github.com/repos/yzr278892/arxiv-daily-researcher/releases/latest"
GITHUB_LATEST_RELEASE_PAGE = f"{REPO_URL}/releases/latest"
VERSION_FILE = Path(__file__).resolve().parent.parent.parent / "VERSION"
_NOTIFIED_STATE_KEY = "update_notified_version"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "configs" / "templates"
_CHECK_GATE_FILENAME = ".update_check.gate"


def _log(logger, message: str, level: str = "info") -> None:
    if logger:
        getattr(logger, level)(message)
    else:
        print(message)


_LOCAL_VERSION_CACHE: tuple[tuple[int, int], str] | None = None


def _get_local_version() -> str:
    """读取本地 VERSION 文件，不存在返回 'unknown'。"""
    global _LOCAL_VERSION_CACHE
    try:
        stat = VERSION_FILE.stat()
    except OSError:
        return "unknown"
    signature = (stat.st_mtime_ns, stat.st_size)
    if _LOCAL_VERSION_CACHE is not None and _LOCAL_VERSION_CACHE[0] == signature:
        return _LOCAL_VERSION_CACHE[1]
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "unknown"
    _LOCAL_VERSION_CACHE = (signature, version)
    return version


def _parse_version(version: str):
    """把点分数字版本号解析成可比较的整数元组；非纯数字段返回 None。"""
    parts = version.strip().lstrip("vV").split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def _is_remote_newer(remote_version: str, local_version: str):
    """远端更新返回 True，不更新返回 False，无法比较返回 None。"""
    remote = _parse_version(remote_version)
    local = _parse_version(local_version)
    if remote is None or local is None:
        return None
    width = max(len(remote), len(local))
    remote += (0,) * (width - len(remote))
    local += (0,) * (width - len(local))
    return remote > local


def _load_template(name: str, subdir: str = "notifications") -> str | None:
    """从 configs/templates 加载通知模板文件。"""
    path = TEMPLATES_DIR / subdir / name
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8")
            # 跳过模板文件的注释头（以 # 或 <!-- 开头的行）
            lines = content.splitlines()
            body_lines = []
            in_header = True
            for line in lines:
                stripped = line.strip()
                if in_header and (stripped.startswith("#") and not stripped.startswith("##")):
                    continue
                if in_header and stripped.startswith("<!--"):
                    # Skip HTML comment blocks
                    continue
                if in_header and stripped.startswith("-->"):
                    continue
                in_header = False
                body_lines.append(line)
            return "\n".join(body_lines).strip()
        except Exception:
            return None
    return None


def _inject_proxy_env(logger=None):
    """
    从 config.py 的代理配置注入 http_proxy/https_proxy 环境变量，
    供 GitHub Release 请求使用。
    """
    try:
        from config import settings
        if not getattr(settings, "PROXY_UPDATE_CHECK", False):
            return
        proxy_url = getattr(settings, "PROXY_URL", "")
        if proxy_url:
            os.environ["http_proxy"] = proxy_url
            os.environ["https_proxy"] = proxy_url
            if logger:
                logger.info(f"[更新检查] 已注入代理: {proxy_url}")
    except Exception:
        pass


def check_for_updates(logger=None) -> bool:
    """Check the latest published release and notify; never change this install.

    The same code path is intentionally used for Docker, source, and CI
    deployments.  ``VERSION`` belongs to the running build, so a notification
    accurately tells the operator that a manual pull/rebuild/restart is
    required.  Returns ``True`` when the check completed (including "already
    current") and ``False`` only when it could not be completed.
    """
    _inject_proxy_env(logger)

    # The Docker startup check, the independent cron check, and a manually
    # started research job can happen at nearly the same time.  Serialize the
    # whole check so they cannot all observe the same unmarked release and
    # send duplicate alerts.  A filesystem that cannot provide this optional
    # gate still performs the check; update availability must never block work.
    lock_file = None
    fcntl_mod = None
    try:
        import fcntl as fcntl_mod

        try:
            from config import settings

            lock_dir = Path(settings.DATA_DIR) / "run"
        except Exception:
            lock_dir = Path("data/run")
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = (lock_dir / _CHECK_GATE_FILENAME).open("a+")
        try:
            fcntl_mod.flock(lock_file.fileno(), fcntl_mod.LOCK_EX | fcntl_mod.LOCK_NB)
        except (IOError, OSError):
            _log(logger, "版本更新检查已在运行，跳过本次重复检查")
            lock_file.close()
            return True
    except Exception as exc:
        if lock_file is not None:
            lock_file.close()
            lock_file = None
        _log(logger, f"版本更新检查互斥锁不可用，继续检查: {exc}", "warning")

    try:
        return _check_version_via_api(logger)
    finally:
        if lock_file is not None and fcntl_mod is not None:
            try:
                fcntl_mod.flock(lock_file.fileno(), fcntl_mod.LOCK_UN)
            finally:
                lock_file.close()


def check_and_update(logger=None) -> bool:
    """Backward-compatible name for the former auto-update entry point.

    Kept for integrations that imported it before v4.  It now only checks and
    notifies; automatic ``git pull`` was unsafe and has been removed.
    """
    return check_for_updates(logger)


def _update_already_notified(remote_version: str) -> bool:
    """该远端版本是否已经通过通知渠道提醒过。

    状态不可用（库无法打开等）时返回 False，退回每次提醒的旧行为，
    保证用户不会因为状态故障而永远收不到更新提醒。
    """
    try:
        from config import settings
        from utils.daily_research_store import DailyResearchStore

        store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
        return store.get_app_state(_NOTIFIED_STATE_KEY) == remote_version
    except Exception:
        return False


def _mark_update_notified(remote_version: str) -> None:
    """记录已提醒的远端版本；失败只影响去重，不影响主流程。"""
    try:
        from config import settings
        from utils.daily_research_store import DailyResearchStore

        store = DailyResearchStore(settings.DAILY_RESEARCH_DB_PATH)
        store.set_app_state(_NOTIFIED_STATE_KEY, remote_version)
    except Exception:
        pass


def _release_from_redirect(location: str | None) -> tuple[str, str] | None:
    """Extract a tagged GitHub Release from the public ``/releases/latest`` redirect."""
    if not location:
        return None
    release_url = urljoin(f"{REPO_URL}/", str(location))
    parsed = urlparse(release_url)
    expected_prefix = "/yzr278892/arxiv-daily-researcher/releases/tag/"
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    if not parsed.path.startswith(expected_prefix):
        return None
    tag = unquote(parsed.path[len(expected_prefix) :]).strip()
    if not tag or "/" in tag:
        return None
    return tag.lstrip("vV"), release_url


def _fetch_latest_release(requests_module, log):
    """Fetch the latest release without normally consuming GitHub API quota.

    GitHub's public HTML endpoint redirects to the latest release tag and is
    not subject to the small unauthenticated REST API quota.  Keep the API as
    a fallback in case GitHub changes that redirect behaviour.
    """
    try:
        page_response = requests_module.get(
            GITHUB_LATEST_RELEASE_PAGE,
            timeout=15,
            allow_redirects=False,
            headers={"User-Agent": "arxiv-daily-researcher-update-check"},
        )
        if page_response.status_code == 404:
            return None
        if 300 <= page_response.status_code < 400:
            release = _release_from_redirect(page_response.headers.get("Location"))
            if release is not None:
                remote_version, release_url = release
                return remote_version, release_url, "请查看发布页面中的完整更新日志。"
        else:
            page_response.raise_for_status()
        raise ValueError("GitHub latest-release redirect did not contain a release tag")
    except Exception as exc:
        log(f"GitHub Release 页面检查不可用，尝试 API 回退: {exc}", "info")

    api_response = requests_module.get(
        GITHUB_API_LATEST,
        timeout=15,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "arxiv-daily-researcher-update-check",
        },
    )
    if api_response.status_code == 404:
        return None
    api_response.raise_for_status()
    data = api_response.json()
    remote_version = str(data.get("tag_name") or "").strip().lstrip("vV")
    release_url = str(data.get("html_url") or REPO_URL)
    release_body = str(data.get("body") or "")[:500]
    return remote_version, release_url, release_body


def _check_version_via_api(logger=None) -> bool:
    """Check the newest GitHub Release and notify when it is newer.

    This is deliberately deployment-agnostic: Docker and source installs both
    compare their packaged ``VERSION`` and require a manual update afterwards.
    """
    import requests as req

    def log(msg, level="info"):
        if logger:
            getattr(logger, level)(msg)
        else:
            print(msg)

    local_version = _get_local_version()
    if local_version == "unknown":
        log("未找到 VERSION 文件，跳过版本检查", "info")
        return True

    try:
        release = _fetch_latest_release(req, log)
        if release is None:
            log("未找到发布版本，跳过", "info")
            return True
        remote_version, release_url, release_body = release

        if not remote_version:
            log("无法获取远程版本号", "warning")
            return True

        if remote_version == local_version:
            log(f"当前版本 {local_version} 已是最新")
            return True

        # 只有远端语义化版本更高时才提醒；本地领先（如未发布的开发版）
        # 或版本号无法比较时保持安静，避免每次运行都误报"新版本"。
        newer = _is_remote_newer(remote_version, local_version)
        if newer is None:
            log(f"远程版本号 {remote_version} 无法与 {local_version} 比较，跳过提醒")
            return True
        if not newer:
            log(f"当前版本 {local_version} 不低于远程 {remote_version}，已是最新")
            return True

        # 同一个远端版本只提醒一次；重复运行不反复打扰所有通知渠道。
        if _update_already_notified(remote_version):
            log(f"新版本 {remote_version} 此前已提醒过，跳过重复通知")
            return True

        log(f"发现新版本: {remote_version}（当前: {local_version}）")
        delivered = _send_update_notification(
            local_version, remote_version, release_url, release_body, logger
        )
        # Do not suppress a future alert merely because every current channel
        # was disabled or failed.  The operator should get the release notice
        # after fixing notifications, even if the version was discovered first.
        if delivered:
            _mark_update_notified(remote_version)
        else:
            log(
                f"新版本 {remote_version} 尚未通过任何通知渠道送达，将在下次检查时重试",
                "warning",
            )
        return True

    except Exception as e:
        log(f"GitHub API 版本检查失败: {e}", "warning")
        return False


def _send_update_notification(
    local_version: str,
    remote_version: str,
    release_url: str,
    release_notes: str,
    logger=None,
) -> bool:
    """Send an update notice and report whether at least one channel delivered it."""
    try:
        from config import settings

        if not getattr(settings, "ENABLE_NOTIFICATIONS", False):
            if logger:
                logger.info("更新提醒未发送：通知总开关未启用")
            return False

        from notifications.notifier import NotifierAgent

        agent = NotifierAgent()
        if not agent.notifiers:
            if logger:
                logger.warning("更新提醒未发送：没有可用的通知渠道")
            return False

        subject = f"ArXiv Daily Researcher - 新版本 {remote_version} 可用"
        template_vars = {
            "local_version": local_version,
            "remote_version": remote_version,
            "release_url": release_url,
            "release_notes": release_notes if release_notes else "无更新日志",
        }

        delivered = False
        for notifier in agent.notifiers:
            try:
                platform = agent._platform_for_notifier(notifier)
                body = _format_update_body(platform, template_vars)

                from notifications.notifier import EmailNotifier

                if isinstance(notifier, EmailNotifier):
                    html_body = _format_update_html(template_vars)
                    sent = notifier.send(subject, body, html_body=html_body)
                else:
                    sent = notifier.send(subject, body)
                if sent:
                    delivered = True
                elif logger:
                    logger.warning(f"更新通知未被 {platform or 'unknown'} 渠道确认送达")
            except Exception as e:
                if logger:
                    logger.warning(f"更新通知发送失败: {e}")
        return delivered

    except Exception as e:
        if logger:
            logger.warning(f"更新通知初始化失败: {e}")
        return False


def _format_update_body(platform: str, vars: dict) -> str:
    """根据平台加载对应的更新通知模板。"""
    # 按平台选择模板文件
    template_map = {
        "telegram": "update_available_telegram.md",
        "wechat_work": "update_available_wechat.md",
    }
    template_name = template_map.get(platform, "update_available.md")
    template = _load_template(template_name)

    if template:
        try:
            return template.format(**vars)
        except KeyError:
            pass

    # 回退到硬编码格式
    return (
        f"## ArXiv Daily Researcher\n\n"
        f"**🔄 新版本可用**\n\n"
        f"> 当前版本: `{vars['local_version']}`\n"
        f"> 最新版本: `{vars['remote_version']}`\n\n"
        f"[查看发布页面]({vars['release_url']})"
    )


def _format_update_html(vars: dict) -> str:
    """加载 Email HTML 更新通知模板。"""
    import html as html_mod

    template = _load_template("update_available.html", subdir="email")
    if template:
        try:
            safe_vars = {k: html_mod.escape(str(v)) for k, v in vars.items()}
            # release_url 不需要 HTML escape（用于 href 属性）
            safe_vars["release_url"] = vars["release_url"]
            return template.format(**safe_vars)
        except KeyError:
            pass

    # 回退到基础 HTML
    return (
        f"<h2>ArXiv Daily Researcher</h2>"
        f"<p><b>🔄 新版本可用</b></p>"
        f"<p>当前版本: <code>{html_mod.escape(vars['local_version'])}</code></p>"
        f"<p>最新版本: <code>{html_mod.escape(vars['remote_version'])}</code></p>"
        f'<p><a href="{html_mod.escape(vars["release_url"])}">查看发布页面</a></p>'
    )


def _main() -> int:
    """CLI entry point used by Docker's independent update-check schedule."""
    try:
        from config import settings

        if not getattr(settings, "AUTO_UPDATE_ENABLED", True):
            print("版本更新检查未启用，跳过")
            return 0
    except Exception as exc:
        print(f"加载更新检查配置失败: {exc}", file=sys.stderr)
        return 1
    return 0 if check_for_updates() else 1


if __name__ == "__main__":
    sys.exit(_main())
