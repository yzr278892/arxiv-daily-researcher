"""
WebDAV 同步模块

按需同步配置和数据文件到 WebDAV 服务器。
不常驻运行，只在需要同步时临时调用。
"""

import logging
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

try:  # ``fcntl`` is unavailable on native Windows but WebDAV upload still works there.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms
    fcntl = None

import requests
from requests.auth import HTTPBasicAuth

from utils.safe_url import safe_configured_http_url

logger = logging.getLogger(__name__)


DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS = 30
# Remote WebDAV endpoints are user-configured, but a compromised server or an
# incorrectly pointed endpoint must not turn a restore into unbounded local
# disk consumption.  The values are deliberately generous for normal history
# and report archives while still providing a deterministic safety boundary.
MAX_WEBDAV_PROPFIND_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_WEBDAV_FILE_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_WEBDAV_TOTAL_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_WEBDAV_DIRECTORY_DEPTH = 32
MAX_WEBDAV_DIRECTORY_ENTRIES = 100_000
_WEBDAV_NAMESPACE = "DAV:"
_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


@dataclass(frozen=True)
class _WebDAVDirectoryEntry:
    """One validated, direct child returned by a WebDAV PROPFIND response."""

    remote_path: str
    name: str
    is_collection: bool
    content_length: Optional[int]


def _decode_webdav_path_segment(segment: str) -> str:
    """Decode a user-facing path segment before checking traversal semantics."""
    value = segment
    # A single decode catches normal ``%2e%2e``.  Repeating a small bounded
    # number of times also catches accidental/double-encoded traversal without
    # turning path validation into an unbounded parser.
    for _ in range(4):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def normalize_webdav_remote_path(value: object) -> str:
    """Normalize a relative WebDAV root and reject traversal/URL syntax.

    WebDAV paths are subsequently handed to a third-party client library.  A
    user normally enters a simple directory such as ``/arxiv-researcher/``;
    treating ``..``, encoded separators or query syntax as ordinary text would
    let that library target a surprising remote location.  The empty value is
    retained as a compatibility spelling for the server's configured root.
    """
    if not isinstance(value, str):
        raise ValueError("WebDAV 远程路径必须是字符串")
    raw_path = value.strip()
    if not raw_path or raw_path == "/":
        return ""
    if any(ord(character) < 0x20 for character in raw_path):
        raise ValueError("WebDAV 远程路径不能包含控制字符")
    if "\\" in raw_path or "?" in raw_path or "#" in raw_path:
        raise ValueError("WebDAV 远程路径不能包含反斜杠、查询参数或片段")

    normalized_parts = []
    for raw_part in raw_path.strip("/").split("/"):
        if not raw_part:
            continue
        part = _decode_webdav_path_segment(raw_part)
        if (
            not part
            or part in {".", ".."}
            or "/" in part
            or "\\" in part
            or any(ord(character) < 0x20 for character in part)
        ):
            raise ValueError(f"WebDAV 远程路径包含不安全的目录段: {raw_part!r}")
        normalized_parts.append(part)
    return "/".join(normalized_parts)


def normalize_webdav_base_url(value: object) -> str:
    """Validate and normalize a WebDAV HTTP(S) endpoint without credentials."""
    url = safe_configured_http_url(value, allow_query=False)
    if not url:
        raise ValueError("WebDAV URL 必须是无内嵌凭据的有效 HTTP(S) 地址")
    parsed = urlsplit(url)
    path = normalize_webdav_remote_path(parsed.path)
    # ``webdavclient3`` expects hostname + optional base path and appends its
    # own remote path.  Preserve a configured DAV endpoint path, but never an
    # ambiguous trailing slash, query or fragment.
    normalized_path = (
        "/" + "/".join(quote(part, safe="@:+,;=()[]") for part in path.split("/"))
        if path
        else ""
    )
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def normalize_webdav_username(value: object) -> str:
    """Validate a Basic-auth username without changing its meaningful bytes."""
    if not isinstance(value, str):
        raise ValueError("WebDAV 用户名必须是字符串")
    username = value.strip()
    if (
        not username
        or ":" in username
        or any(ord(character) < 0x20 for character in username)
    ):
        raise ValueError("WebDAV 用户名不能为空且不能包含冒号或控制字符")
    return username


def validate_webdav_password(value: object) -> str:
    """Keep Basic-auth password input out of header-injection territory."""
    if not isinstance(value, str):
        raise ValueError("WebDAV 密码必须是字符串")
    if any(ord(character) < 0x20 for character in value):
        raise ValueError("WebDAV 密码不能包含控制字符")
    return value


# The WebUI currently writes ordinary five-field crontab expressions.  Keep
# the matcher here instead of adding a dependency just to wake a tiny worker
# once per minute.  It deliberately accepts the useful, standard subset of
# Vixie cron (lists, ranges, and steps) and rejects malformed expressions
# rather than silently running at an unexpected time.
_CRON_NICKNAMES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
_CRON_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_CRON_WEEKDAY_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _cron_value(
    raw_value: str,
    minimum: int,
    maximum: int,
    names: Optional[Dict[str, int]],
) -> int:
    """Parse one cron value and keep errors actionable for configuration users."""
    normalized = raw_value.lower()
    if names and normalized in names:
        value = names[normalized]
    elif re.fullmatch(r"\d+", normalized):
        value = int(normalized)
    else:
        raise ValueError(f"无效 cron 字段值: {raw_value!r}")

    if not minimum <= value <= maximum:
        raise ValueError(f"cron 字段值超出范围 {minimum}-{maximum}: {raw_value!r}")
    return value


def _cron_field_matches(
    expression: str,
    value: int,
    minimum: int,
    maximum: int,
    names: Optional[Dict[str, int]] = None,
) -> bool:
    """Return whether one standard cron field matches ``value``.

    A field is small (at most 60 possible values), so expanding ranges is both
    clearer and less error-prone than relying on fragile arithmetic shortcuts.
    """
    if not expression or expression.strip() != expression:
        raise ValueError("cron 字段不能为空或包含首尾空格")

    matched_values: set[int] = set()
    for component in expression.lower().split(","):
        if not component:
            raise ValueError(f"无效 cron 列表: {expression!r}")

        if component.count("/") > 1:
            raise ValueError(f"无效 cron 步长: {component!r}")
        if "/" in component:
            base, raw_step = component.split("/", 1)
            if not re.fullmatch(r"\d+", raw_step) or int(raw_step) <= 0:
                raise ValueError(f"无效 cron 步长: {component!r}")
            step = int(raw_step)
        else:
            base = component
            step = 1

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            if base.count("-") != 1:
                raise ValueError(f"无效 cron 范围: {component!r}")
            raw_start, raw_end = base.split("-", 1)
            start = _cron_value(raw_start, minimum, maximum, names)
            end = _cron_value(raw_end, minimum, maximum, names)
            if start > end:
                raise ValueError(f"cron 范围起点不能大于终点: {component!r}")
        else:
            if step != 1:
                raise ValueError(f"cron 步长必须用于 * 或范围: {component!r}")
            start = end = _cron_value(base, minimum, maximum, names)

        matched_values.update(range(start, end + 1, step))

    # In a crontab Sunday may be written as either 0 or 7.  Preserve both
    # spellings without making ranges such as 0-7 behave strangely.
    return value in matched_values or (
        minimum == 0 and maximum == 7 and value == 0 and 7 in matched_values
    )


def cron_schedule_matches(schedule: str, now: Optional[datetime] = None) -> bool:
    """Return whether a five-field cron schedule matches the local minute.

    The Docker cron daemon and this helper both use the container's local time
    (controlled by ``TZ``).  Day-of-month/day-of-week follows crontab's OR
    semantics when both fields are restricted.
    """
    if not isinstance(schedule, str):
        raise ValueError("cron 表达式必须是字符串")

    minute, hour, day_of_month, month, day_of_week = _parse_cron_schedule(schedule)
    current = now or datetime.now()
    if not _cron_field_matches(minute, current.minute, 0, 59):
        return False
    if not _cron_field_matches(hour, current.hour, 0, 23):
        return False
    if not _cron_field_matches(month, current.month, 1, 12, _CRON_MONTH_NAMES):
        return False

    dom_matches = _cron_field_matches(day_of_month, current.day, 1, 31)
    # datetime.weekday() is Monday=0; cron is Sunday=0 (or 7).
    cron_weekday = (current.weekday() + 1) % 7
    dow_matches = _cron_field_matches(
        day_of_week, cron_weekday, 0, 7, _CRON_WEEKDAY_NAMES
    )
    dom_is_wildcard = day_of_month == "*"
    dow_is_wildcard = day_of_week == "*"
    if dom_is_wildcard and dow_is_wildcard:
        return True
    if dom_is_wildcard:
        return dow_matches
    if dow_is_wildcard:
        return dom_matches
    return dom_matches or dow_matches


def _parse_cron_schedule(schedule: str) -> tuple[str, str, str, str, str]:
    """Normalize and fully validate one five-field cron expression."""
    if not isinstance(schedule, str):
        raise ValueError("cron 表达式必须是字符串")

    normalized = _CRON_NICKNAMES.get(schedule.strip().lower(), schedule.strip())
    fields = normalized.split()
    if len(fields) != 5:
        raise ValueError("cron 表达式必须包含 5 个字段（分 时 日 月 周）")

    minute, hour, day_of_month, month, day_of_week = fields
    # _cron_field_matches expands every list component before returning.  The
    # arbitrary values only trigger validation; they are not used for matching.
    _cron_field_matches(minute, 0, 0, 59)
    _cron_field_matches(hour, 0, 0, 23)
    _cron_field_matches(day_of_month, 1, 1, 31)
    _cron_field_matches(month, 1, 1, 12, _CRON_MONTH_NAMES)
    _cron_field_matches(day_of_week, 0, 0, 7, _CRON_WEEKDAY_NAMES)
    return minute, hour, day_of_month, month, day_of_week


def validate_cron_schedule(schedule: str) -> str:
    """Validate a standard WebDAV schedule and return its normalized spelling."""
    _parse_cron_schedule(schedule)
    return _CRON_NICKNAMES.get(schedule.strip().lower(), schedule.strip())


class WebDAVSync:
    """
    WebDAV 文件同步客户端。

    按需启动，执行完立即退出，不占用持续资源。

    注意：坚果云等 WebDAV 服务器不支持 HEAD 请求，因此使用
    disable_check=True 绕过 webdavclient3 内置的 HEAD 检查，
    改用 PROPFIND 进行所有存在性检查。
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        remote_path: str = "/arxiv-daily-researcher/",
        proxy_url: str = "",
    ):
        try:
            from webdav3.client import Client
        except ImportError:
            raise ImportError(
                "WebDAV 同步需要 webdavclient3 库。请运行: pip install webdavclient3"
            )

        # Validate before constructing the third-party client.  It otherwise
        # accepts traversal-like paths and endpoints with embedded credentials.
        hostname = normalize_webdav_base_url(url)
        username = normalize_webdav_username(username)
        password = validate_webdav_password(password)
        self.remote_root = normalize_webdav_remote_path(remote_path)

        # webdav_hostname 必须去除末尾斜杠，避免 webdavclient3 拼接出 // 导致 403 错误
        options = {
            "webdav_hostname": hostname,
            "webdav_login": username,
            "webdav_password": password,
            "webdav_timeout": DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS,
            # 坚果云等服务器不支持 HEAD 请求，必须禁用 check
            # 后续所有存在性检查改用 _check_remote()（基于 PROPFIND）
            "disable_check": True,
        }
        self.client = Client(options)
        self._project_root = Path(__file__).resolve().parent.parent.parent

        # 用于直接 HTTP 请求的 session（绕过 webdavclient3 的 HEAD check）
        self._http = requests.Session()
        if proxy_url:
            proxy_mapping = {"http": proxy_url, "https": proxy_url}
            self._http.proxies.update(proxy_mapping)
            # webdavclient3 owns a separate requests.Session for PUT/GET/MKCOL.
            # Its advertised ``proxy_hostname`` option is ignored by the
            # supported 3.x client, so set the real session proxy explicitly.
            client_session = getattr(self.client, "session", None)
            if client_session is None or not hasattr(client_session, "proxies"):
                raise RuntimeError("WebDAV 客户端不支持配置网络代理")
            client_session.proxies.update(proxy_mapping)
        self._http.auth = HTTPBasicAuth(username, password)
        self._base_url = hostname

    def _data_dir(self) -> Path:
        """Return the configured data directory when the worker config is available.

        The intentionally thin WebUI image does not ship ``config.py``.  It can
        still synchronize the standard mounted ``/app/data`` directory through
        the fallback, while the worker honors a custom ``paths.data_dir``.
        """
        try:
            from config import settings

            return Path(settings.DATA_DIR)
        except (ImportError, ModuleNotFoundError):
            return self._project_root / "data"

    @staticmethod
    def _daily_research_database_path(data_dir: Path) -> Path:
        """Return the authoritative SQLite history path for WebDAV sync.

        ``data/history`` is v3.2 import input only.  Normal synchronization
        must therefore use the configured SQLite ledger, including installs
        that deliberately keep that database outside the default data tree.
        The thin config-panel image has no worker settings module, so it falls
        back to the conventional mounted location there.
        """
        try:
            from config import settings

            configured_data_dir = Path(settings.DATA_DIR)
            # Helpers/tests may explicitly operate on an alternate data tree;
            # never let that call unexpectedly snapshot or restore the live
            # configured database merely because ``config`` is importable.
            if Path(data_dir).resolve() == configured_data_dir.resolve():
                return Path(settings.DAILY_RESEARCH_DB_PATH)
        except (AttributeError, ImportError, ModuleNotFoundError):
            pass
        return Path(data_dir) / "daily_research" / "daily_research.db"

    def _remote(self, rel_path: str) -> str:
        """将相对路径拼接到 remote_root 下，返回完整远程路径。"""
        rel = normalize_webdav_remote_path(rel_path)
        if self.remote_root:
            return f"{self.remote_root}/{rel}" if rel else self.remote_root
        return rel

    def _url(self, remote_path: str) -> str:
        """构建完整的 WebDAV URL。"""
        path = normalize_webdav_remote_path(remote_path)
        if not path:
            return self._base_url
        # Quote each already-validated segment rather than letting path data
        # reinterpret a query/fragment or a slash at URL construction time.
        encoded_path = "/".join(quote(part, safe="@:+,;=()[]") for part in path.split("/"))
        return f"{self._base_url}/{encoded_path}"

    @staticmethod
    def _safe_download_filename(value: object) -> str:
        """Return one portable local filename, rejecting traversal spellings.

        PROPFIND ``href`` values are remote input.  Even if a WebDAV library
        considers a name valid, it must never become an absolute path, a parent
        traversal, a separator, or a Windows device name when this project is
        restored on another host.
        """
        if not isinstance(value, str):
            raise ValueError("WebDAV 远端文件名必须是字符串")
        name = _decode_webdav_path_segment(value)
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or any(character in '<>:"|?*' for character in name)
            or name != name.rstrip(". ")
            or name.rstrip(". ").upper() in _WINDOWS_RESERVED_FILENAMES
            or any(ord(character) < 0x20 for character in name)
        ):
            raise ValueError(f"WebDAV 返回了不安全的文件名: {value!r}")
        return name

    def _href_to_remote_path(self, href: object, parent_remote: str) -> str:
        """Resolve a DAV href and map it back below the configured endpoint.

        Servers commonly return an absolute-path href containing the endpoint
        prefix (for example ``/dav/root/project/data``), while others return a
        same-origin absolute URI or a relative child name.  Resolve all three
        forms against the directory we asked for, then require the result to
        remain on the exact configured HTTP origin and below its base path.
        """
        if not isinstance(href, str) or not href.strip():
            raise ValueError("WebDAV PROPFIND 返回了空 href")
        raw_href = href.strip()
        raw_parsed = urlsplit(raw_href)
        if raw_parsed.query or raw_parsed.fragment or raw_parsed.username or raw_parsed.password:
            raise ValueError(f"WebDAV PROPFIND 返回了不安全 href: {href!r}")

        request_url = self._url(parent_remote).rstrip("/") + "/"
        resolved_url = urljoin(request_url, raw_href)
        resolved = urlsplit(resolved_url)
        base = urlsplit(self._base_url)
        try:
            same_origin = (
                resolved.scheme.casefold() == base.scheme.casefold()
                and resolved.hostname is not None
                and base.hostname is not None
                and resolved.hostname.casefold() == base.hostname.casefold()
                and resolved.port == base.port
            )
        except ValueError as exc:
            raise ValueError(f"WebDAV PROPFIND 返回了无效 href: {href!r}") from exc
        if (
            not same_origin
            or resolved.query
            or resolved.fragment
            or resolved.username is not None
            or resolved.password is not None
        ):
            raise ValueError(f"WebDAV PROPFIND 返回了端点外 href: {href!r}")

        base_path = normalize_webdav_remote_path(base.path)
        resolved_path = normalize_webdav_remote_path(resolved.path)
        if not base_path:
            return resolved_path
        if resolved_path == base_path:
            return ""
        prefix = base_path + "/"
        if not resolved_path.startswith(prefix):
            raise ValueError(f"WebDAV PROPFIND 返回了端点路径外 href: {href!r}")
        return resolved_path[len(prefix) :]

    def _remote_child_entry(self, parent_remote: str, href: object) -> tuple[str, str]:
        """Ensure an href is a direct child of the directory that was listed."""
        parent = normalize_webdav_remote_path(parent_remote)
        remote_path = self._href_to_remote_path(href, parent)
        if not parent:
            parts = remote_path.split("/") if remote_path else []
        else:
            prefix = parent + "/"
            if not remote_path.startswith(prefix):
                raise ValueError(
                    f"WebDAV PROPFIND 返回了目录外的条目: {href!r}（目录 {parent!r}）"
                )
            parts = remote_path[len(prefix) :].split("/")
        if len(parts) != 1:
            raise ValueError(
                f"WebDAV PROPFIND 返回了非直接子条目: {href!r}（目录 {parent!r}）"
            )
        name = WebDAVSync._safe_download_filename(parts[0])
        return remote_path, name

    @staticmethod
    def _propfind_content_length(response_element: ElementTree.Element) -> Optional[int]:
        """Read a declared DAV content length, rejecting malformed values."""
        raw_length = response_element.findtext(
            f".//{{{_WEBDAV_NAMESPACE}}}getcontentlength"
        )
        if raw_length is None or not raw_length.strip():
            return None
        try:
            value = int(raw_length.strip())
        except ValueError as exc:
            raise ValueError(f"WebDAV 返回了无效文件长度: {raw_length!r}") from exc
        if value < 0:
            raise ValueError(f"WebDAV 返回了负文件长度: {raw_length!r}")
        return value

    def _list_remote_directory(self, remote_dir: str) -> List[_WebDAVDirectoryEntry]:
        """List a directory with a bounded Depth-1 PROPFIND response.

        ``webdavclient3.pull`` recursively joins remote filenames to a local
        root.  This controlled listing keeps the remote-to-local mapping in
        this module and rejects a malicious response before anything is
        written below (or outside) the configured data directory.
        """
        normalized_dir = normalize_webdav_remote_path(remote_dir)
        response = None
        try:
            response = self._http.request(
                "PROPFIND",
                self._url(normalized_dir),
                headers={"Depth": "1"},
                timeout=DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code != 207:
                raise RuntimeError(
                    f"WebDAV PROPFIND 目录枚举失败 {normalized_dir!r}: HTTP {response.status_code}"
                )
            chunks: List[bytes] = []
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > MAX_WEBDAV_PROPFIND_RESPONSE_BYTES:
                    raise RuntimeError(
                        f"WebDAV PROPFIND 响应超过 {MAX_WEBDAV_PROPFIND_RESPONSE_BYTES} 字节限制"
                    )
                chunks.append(chunk)
            try:
                root = ElementTree.fromstring(b"".join(chunks))
            except ElementTree.ParseError as exc:
                raise RuntimeError("WebDAV PROPFIND 返回了无效 XML") from exc

            entries: List[_WebDAVDirectoryEntry] = []
            # A remote server may distinguish names that collide on a Windows
            # or case-insensitive/macOS filesystem.  Reject rather than let a
            # restore overwrite an earlier directory entry unpredictably.
            seen_local_names = set()
            self_entry_seen = False
            for response_element in root.findall(f"{{{_WEBDAV_NAMESPACE}}}response"):
                href = response_element.findtext(f"{{{_WEBDAV_NAMESPACE}}}href")
                remote_path = self._href_to_remote_path(href, normalized_dir)
                collection = response_element.find(
                    f".//{{{_WEBDAV_NAMESPACE}}}collection"
                ) is not None
                if remote_path == normalized_dir:
                    if self_entry_seen:
                        raise ValueError("WebDAV PROPFIND 返回了重复目录自身条目")
                    self_entry_seen = True
                    if not collection:
                        raise ValueError("WebDAV PROPFIND 目标不是目录")
                    continue

                child_path, name = self._remote_child_entry(normalized_dir, href)
                local_name_key = unicodedata.normalize("NFC", name).casefold()
                if local_name_key in seen_local_names:
                    raise ValueError(f"WebDAV PROPFIND 返回了重复目录项: {name!r}")
                seen_local_names.add(local_name_key)
                entries.append(
                    _WebDAVDirectoryEntry(
                        remote_path=child_path,
                        name=name,
                        is_collection=collection,
                        content_length=self._propfind_content_length(response_element),
                    )
                )
                if len(entries) > MAX_WEBDAV_DIRECTORY_ENTRIES:
                    raise RuntimeError(
                        f"WebDAV 目录条目超过 {MAX_WEBDAV_DIRECTORY_ENTRIES} 项限制"
                    )
            if not self_entry_seen:
                raise RuntimeError("WebDAV PROPFIND 响应缺少目标目录自身条目")
            return entries
        finally:
            if response is not None:
                response.close()

    @staticmethod
    def _safe_download_destination(local_dir: Path, child_name: str) -> Path:
        """Return a checked immediate child of ``local_dir`` for a remote item."""
        root = local_dir.resolve()
        candidate = root / WebDAVSync._safe_download_filename(child_name)
        if candidate.is_symlink():
            raise ValueError(f"WebDAV 本地恢复路径不能覆盖符号链接: {child_name!r}")
        destination = candidate.resolve()
        if destination.parent != root:
            raise ValueError(f"WebDAV 本地恢复路径越界: {child_name!r}")
        return destination

    def _download_remote_file_to_path(
        self,
        remote_file: str,
        destination: Path,
        *,
        maximum_bytes: int,
        declared_length: Optional[int] = None,
    ) -> int:
        """Download one known remote file through the bounded HTTP session.

        The WebDAV library's convenience downloader follows its own request
        policy and writes directly to the caller's pathname.  Direct streaming
        keeps redirects disabled and enforces a byte limit before an unfinished
        server response can consume an unbounded amount of local disk.
        """
        if maximum_bytes < 0:
            raise ValueError("WebDAV 下载大小限制不能为负数")
        if declared_length is not None and declared_length > maximum_bytes:
            raise RuntimeError(
                f"WebDAV 文件 {remote_file!r} 超过 {maximum_bytes} 字节下载限制"
            )

        response = None
        try:
            response = self._http.request(
                "GET",
                self._url(remote_file),
                timeout=DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(
                    f"WebDAV 文件下载失败 {remote_file!r}: HTTP {response.status_code}"
                )
            raw_header_length = response.headers.get("Content-Length")
            if raw_header_length:
                try:
                    header_length = int(raw_header_length)
                except ValueError as exc:
                    raise RuntimeError(
                        f"WebDAV 文件 {remote_file!r} 返回了无效 Content-Length"
                    ) from exc
                if header_length < 0 or header_length > maximum_bytes:
                    raise RuntimeError(
                        f"WebDAV 文件 {remote_file!r} 超过 {maximum_bytes} 字节下载限制"
                    )
                if declared_length is not None and header_length != declared_length:
                    raise RuntimeError(
                        f"WebDAV 文件 {remote_file!r} 长度与 PROPFIND 声明不一致"
                    )

            total_bytes = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > maximum_bytes:
                        raise RuntimeError(
                            f"WebDAV 文件 {remote_file!r} 下载后超过 {maximum_bytes} 字节限制"
                        )
                    handle.write(chunk)
            if declared_length is not None and total_bytes != declared_length:
                raise RuntimeError(
                    f"WebDAV 文件 {remote_file!r} 长度与 PROPFIND 声明不一致"
                )
            return total_bytes
        finally:
            if response is not None:
                response.close()

    def _download_remote_file_atomic(
        self,
        remote_file: str,
        local_file: Path,
        declared_length: Optional[int],
        remaining_total_bytes: int,
    ) -> int:
        """Download one listed file via a bounded temporary file and replace it."""
        if declared_length is not None and declared_length > MAX_WEBDAV_FILE_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"WebDAV 文件 {remote_file!r} 超过单文件 {MAX_WEBDAV_FILE_DOWNLOAD_BYTES} 字节限制"
            )
        if declared_length is not None and declared_length > remaining_total_bytes:
            raise RuntimeError("WebDAV 下载内容超过本次总大小限制")

        local_file.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=local_file.parent,
                prefix=f".{local_file.name}.",
                suffix=".download",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
            size = self._download_remote_file_to_path(
                remote_file,
                temporary_path,
                maximum_bytes=min(MAX_WEBDAV_FILE_DOWNLOAD_BYTES, remaining_total_bytes),
                declared_length=declared_length,
            )
            os.replace(temporary_path, local_file)
            temporary_path = None
            return size
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _download_directory_safely(
        self,
        remote_dir: str,
        local_dir: Path,
        *,
        maximum_total_bytes: int = MAX_WEBDAV_TOTAL_DOWNLOAD_BYTES,
    ) -> int:
        """Recursively restore one approved remote directory without ``pull``.

        Every remote name is validated before a local path exists.  A failed
        restore leaves already-existing files intact; files that completed
        before a later failure are individually valid, atomically installed
        snapshots rather than partially streamed output.
        """
        local_dir.mkdir(parents=True, exist_ok=True)
        if local_dir.is_symlink():
            raise ValueError(f"WebDAV 本地恢复根目录不能是符号链接: {local_dir}")
        if maximum_total_bytes < 0:
            raise ValueError("WebDAV 下载总大小限制不能为负数")
        local_root = local_dir.resolve()
        pending = [(normalize_webdav_remote_path(remote_dir), local_root, 0)]
        downloaded_bytes = 0
        seen_remote_dirs = set()
        entry_count = 0

        while pending:
            current_remote, current_local, depth = pending.pop()
            if current_remote in seen_remote_dirs:
                raise RuntimeError(f"WebDAV 目录树包含循环: {current_remote!r}")
            seen_remote_dirs.add(current_remote)
            if depth > MAX_WEBDAV_DIRECTORY_DEPTH:
                raise RuntimeError(
                    f"WebDAV 目录深度超过 {MAX_WEBDAV_DIRECTORY_DEPTH} 层限制"
                )
            for entry in self._list_remote_directory(current_remote):
                entry_count += 1
                if entry_count > MAX_WEBDAV_DIRECTORY_ENTRIES:
                    raise RuntimeError(
                        f"WebDAV 目录树条目超过 {MAX_WEBDAV_DIRECTORY_ENTRIES} 项限制"
                    )
                local_path = self._safe_download_destination(current_local, entry.name)
                if entry.is_collection:
                    local_path.mkdir(parents=False, exist_ok=True)
                    pending.append((entry.remote_path, local_path, depth + 1))
                    continue
                downloaded_bytes += self._download_remote_file_atomic(
                    entry.remote_path,
                    local_path,
                    entry.content_length,
                    maximum_total_bytes - downloaded_bytes,
                )
        return downloaded_bytes

    def _check_remote(self, remote_path: str) -> bool:
        """
        使用 PROPFIND (Depth 0) 检查远程资源是否存在。

        坚果云等服务器不支持 HEAD，但完整支持 PROPFIND。
        """
        url = self._url(remote_path)
        try:
            resp = self._http.request(
                "PROPFIND",
                url,
                headers={"Depth": "0"},
                timeout=DEFAULT_WEBDAV_REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            try:
                return resp.status_code == 207
            finally:
                resp.close()
        except Exception as e:
            logger.debug(f"PROPFIND 检查失败 {url}: {e}")
            return False

    def test_connection(self) -> bool:
        """测试 WebDAV 连接是否正常，并验证对目标目录的访问权限。"""
        try:
            remote = self._remote("")
            # 使用 PROPFIND 检查目标目录是否存在（支持坚果云等不支持 HEAD 的服务器）
            if not self._check_remote(remote):
                # 如果不存在，尝试创建（以此验证凭据和写入权限）
                if not self._ensure_remote_dir(remote + "/"):
                    raise RuntimeError("无法创建或确认 WebDAV 远程目录")
            return True
        except Exception as e:
            logger.error(f"WebDAV 连接测试失败: {e}")
            return False

    def upload_configs(self) -> Dict[str, bool]:
        """
        上传配置文件到 WebDAV。

        上传: runtime/config.json（远端对象名保持 configs/config.json）
        不上传: .env（安全考虑）

        返回:
            Dict[str, bool]: 各文件上传结果
        """
        results = {}

        # The remote object name is intentionally stable so existing WebDAV
        # archives stay compatible. Locally, all writes now use runtime/;
        # this call copies an old v4.1 config once when an installation first
        # upgrades.
        from utils.config_io import ensure_runtime_config_path

        try:
            config_path = ensure_runtime_config_path(self._project_root)
        except RuntimeError as exc:
            logger.error("准备运行配置同步失败: %s", exc)
            config_path = None

        if config_path and config_path.is_file():
            try:
                remote_file = self._remote("configs/config.json")
                if not self._ensure_remote_dir(self._remote("configs") + "/"):
                    raise RuntimeError("无法创建 WebDAV 配置目录")
                self.client.upload_file(remote_file, str(config_path))
                results["configs/config.json"] = True
                logger.info("已上传 configs/config.json")
            except Exception as e:
                results["configs/config.json"] = False
                logger.error(f"上传 configs/config.json 失败: {e}")
        else:
            results["configs/config.json"] = False
            logger.warning("runtime/config.json 不存在，跳过")

        return results

    def upload_data(
        self,
        include_reports: bool = False,
        include_history: bool = True,
        include_keywords: bool = True,
    ) -> Dict[str, bool]:
        """
        上传数据文件到 WebDAV。

        上传: 新 SQLite 历史、data/keywords/
        可选: data/reports/

        参数:
            include_reports: 是否包含报告文件

        返回:
            Dict[str, bool]: 各目录上传结果
        """
        results = {}
        data_dir = self._data_dir()

        # ``data/history`` contains only v3.2 JSON import input.  It is never
        # a runtime history authority after the SQLite migration, so WebDAV
        # must not keep uploading it or restore it onto a new installation.
        # The opt-in retained its old config key for compatibility, but now
        # means the authoritative SQLite history below.

        # 上传 keywords 目录
        keywords_dir = data_dir / "keywords"
        if include_keywords and keywords_dir.exists() and any(keywords_dir.iterdir()):
            results["data/keywords/"] = self._upload_directory(
                keywords_dir, self._remote("data/keywords") + "/"
            )
        elif include_keywords:
            logger.info("data/keywords/ 为空或不存在，跳过")
            results["data/keywords/"] = True

        if include_history:
            results["data/daily_research/daily_research.db"] = (
                self._upload_daily_research_snapshot(data_dir)
            )

        # 可选：上传报告
        if include_reports:
            reports_dir = data_dir / "reports"
            if reports_dir.exists() and any(reports_dir.iterdir()):
                results["data/reports/"] = self._upload_directory(
                    reports_dir, self._remote("data/reports") + "/"
                )
            else:
                results["data/reports/"] = True

        return results

    def download_configs(self) -> Dict[str, bool]:
        """
        从 WebDAV 下载配置文件恢复到本地。

        返回:
            Dict[str, bool]: 各文件下载结果
        """
        results = {}
        from utils.config_io import ensure_runtime_config_path

        try:
            config_path = ensure_runtime_config_path(self._project_root)
        except RuntimeError as exc:
            logger.error("准备运行配置恢复失败: %s", exc)
            return {"configs/config.json": False}
        remote_file = self._remote("configs/config.json")

        try:
            if self._check_remote(remote_file):
                config_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=config_path.parent,
                    prefix=f".{config_path.name}.",
                    suffix=".download",
                    delete=False,
                ) as handle:
                    temporary_path = Path(handle.name)
                try:
                    self._download_remote_file_to_path(
                        remote_file,
                        temporary_path,
                        maximum_bytes=MAX_WEBDAV_FILE_DOWNLOAD_BYTES,
                    )
                    with temporary_path.open("r", encoding="utf-8") as handle:
                        # Reject an interrupted/HTML error response before it
                        # can replace the live configuration.  Parsing alone
                        # is insufficient: a portable config cannot redirect
                        # the local worker's filesystem paths outside /app.
                        import json5
                        from utils.config_io import validate_config_document

                        validate_config_document(json5.load(handle))
                    content = temporary_path.read_text(encoding="utf-8")
                    from utils.config_io import _atomic_write_text

                    _atomic_write_text(
                        config_path,
                        content,
                        mode=0o644,
                        preserve_existing_mode=True,
                    )
                    temporary_path = None
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                results["configs/config.json"] = True
                logger.info("已下载 configs/config.json")
            else:
                results["configs/config.json"] = False
                logger.warning("远程 configs/config.json 不存在")
        except Exception as e:
            results["configs/config.json"] = False
            logger.error(f"下载 configs/config.json 失败: {e}")

        return results

    def download_data(
        self,
        include_reports: bool = False,
        include_history: bool = True,
        include_keywords: bool = True,
    ) -> Dict[str, bool]:
        """
        从 WebDAV 下载数据文件到本地。

        逐项受控下载，不会删除本地已有文件。

        不使用第三方客户端的 ``pull()``：远端 WebDAV 服务返回的目录项
        必须先经过路径、深度、数量和大小校验，才会映射到本地目录。

        返回:
            Dict[str, bool]: 各目录下载结果
        """
        results = {}
        data_dir = self._data_dir()

        dirs_to_download = []
        if include_keywords:
            dirs_to_download.append("keywords")
        if include_reports:
            dirs_to_download.append("reports")

        for subdir in dirs_to_download:
            remote_dir = self._remote(f"data/{subdir}") + "/"
            local_dir = data_dir / subdir
            try:
                if self._check_remote(remote_dir.rstrip("/")):
                    downloaded_bytes = self._download_directory_safely(remote_dir.rstrip("/"), local_dir)
                    results[f"data/{subdir}/"] = True
                    logger.info(f"已下载 data/{subdir}/ ({downloaded_bytes} 字节)")
                else:
                    logger.info(f"远程 data/{subdir}/ 不存在，跳过")
                    results[f"data/{subdir}/"] = True  # 远程不存在不算失败
            except Exception as e:
                results[f"data/{subdir}/"] = False
                logger.error(f"下载 data/{subdir}/ 失败: {e}")

        if include_history:
            results["data/daily_research/daily_research.db"] = (
                self._download_daily_research_snapshot(data_dir)
            )

        return results

    def sync_all(
        self,
        direction: str = "upload",
        include_reports: bool = False,
        include_configs: bool = True,
        include_history: bool = True,
        include_keywords: bool = True,
    ) -> Dict:
        """
        执行完整同步。

        参数:
            direction: "upload" 或 "download"
            include_reports: 是否包含报告
            include_configs: 是否同步 config.json
            include_history: 是否同步新的 SQLite 历史状态（兼容旧配置键名）
            include_keywords: 是否同步关键词数据

        返回:
            dict: 同步结果摘要
        """
        start = time.time()
        results = {}

        if direction == "upload":
            if include_configs:
                results.update(self.upload_configs())
            results.update(
                self.upload_data(
                    include_reports=include_reports,
                    include_history=include_history,
                    include_keywords=include_keywords,
                )
            )
        elif direction == "download":
            if include_configs:
                results.update(self.download_configs())
            results.update(
                self.download_data(
                    include_reports=include_reports,
                    include_history=include_history,
                    include_keywords=include_keywords,
                )
            )
        else:
            raise ValueError(f"不支持的 WebDAV 同步方向: {direction}")

        elapsed = time.time() - start
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)

        summary = {
            "direction": direction,
            "results": results,
            "success": success_count,
            "total": total_count,
            "elapsed_seconds": round(elapsed, 1),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        logger.info(
            f"WebDAV {direction} 完成: {success_count}/{total_count} 成功，"
            f"耗时 {elapsed:.1f}s"
        )
        return summary

    def _upload_daily_research_snapshot(self, data_dir: Path) -> bool:
        """Upload a consistent SQLite backup instead of copying WAL files.

        The daily delivery ledger contains the canonical version de-duplication,
        analysis state and notification outbox.  Copying a live WAL database
        directly can produce an unusable restore, so SQLite's backup API writes
        a point-in-time standalone snapshot first.
        """
        database_path = self._daily_research_database_path(data_dir)
        if not database_path.exists():
            logger.info("SQLite 新历史数据库不存在，跳过 WebDAV 同步: %s", database_path)
            return True

        temporary_path = None
        source_conn = None
        snapshot_conn = None
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=database_path.parent,
                prefix=".daily_research.",
                suffix=".sqlite",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)

            source_conn = sqlite3.connect(database_path)
            snapshot_conn = sqlite3.connect(temporary_path)
            source_conn.backup(snapshot_conn)
            snapshot_conn.close()
            snapshot_conn = None
            source_conn.close()
            source_conn = None

            remote_file = self._remote("data/daily_research/daily_research.db")
            if not self._ensure_remote_dir(self._remote("data/daily_research") + "/"):
                raise RuntimeError("无法创建 WebDAV SQLite 快照目录")
            self.client.upload_file(remote_file, str(temporary_path))
            logger.info("已上传一致性 SQLite 快照: data/daily_research/daily_research.db")
            return True
        except Exception as exc:
            logger.error("上传 daily_research SQLite 快照失败: %s", exc)
            return False
        finally:
            if snapshot_conn is not None:
                snapshot_conn.close()
            if source_conn is not None:
                source_conn.close()
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("无法清理 SQLite 快照临时文件: %s", temporary_path)

    @contextmanager
    def _daily_research_restore_lock(self, data_dir: Path):
        """Use the same restore gate as browser imports and every worker mode."""
        if fcntl is None:
            raise RuntimeError("当前平台无法安全检查任务锁，不能恢复 daily_research 数据库")
        from utils.run_lock import DatabaseRestoreBusyError, database_restore_activity_gate

        try:
            with database_restore_activity_gate(
                exclusive=True, nonblocking=True, data_dir=data_dir
            ):
                yield
        except DatabaseRestoreBusyError as exc:
            raise RuntimeError("有运行中的任务，不能恢复 daily_research 数据库") from exc

    @staticmethod
    def _validate_sqlite_database(database_path: Path) -> None:
        """Raise when a downloaded SQLite snapshot is incomplete or corrupt."""
        uri = f"file:{database_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            detail = result[0] if result else "no quick_check result"
            raise RuntimeError(f"SQLite 完整性校验失败: {detail}")

    @staticmethod
    def _write_sqlite_backup(source_path: Path, target_path: Path) -> None:
        """Write a standalone SQLite snapshot atomically via the backup API."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        source_conn = None
        snapshot_conn = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_path.parent,
                prefix=f".{target_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
            source_conn = sqlite3.connect(source_path)
            snapshot_conn = sqlite3.connect(temporary_path)
            source_conn.backup(snapshot_conn)
            snapshot_conn.close()
            snapshot_conn = None
            source_conn.close()
            source_conn = None
            os.replace(temporary_path, target_path)
            temporary_path = None
        finally:
            if snapshot_conn is not None:
                snapshot_conn.close()
            if source_conn is not None:
                source_conn.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _download_daily_research_snapshot(self, data_dir: Path) -> bool:
        """Safely restore the durable daily ledger from a WebDAV snapshot.

        The snapshot is downloaded to a temporary file, checked by SQLite, and
        atomically installed only while the worker activity gate is free. The prior
        local database is preserved as ``*.before_webdav_restore`` so a manual
        recovery cannot silently discard the newer local state.
        """
        remote_file = self._remote("data/daily_research/daily_research.db")
        database_path = self._daily_research_database_path(data_dir)
        backup_path = database_path.with_name(database_path.name + ".before_webdav_restore")
        temporary_path = None
        try:
            if not self._check_remote(remote_file):
                logger.info("远程 daily_research SQLite 快照不存在，跳过")
                return True

            with self._daily_research_restore_lock(data_dir):
                database_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    dir=database_path.parent,
                    prefix=".daily_research.download.",
                    suffix=".sqlite",
                    delete=False,
                ) as handle:
                    temporary_path = Path(handle.name)

                self._download_remote_file_to_path(
                    remote_file,
                    temporary_path,
                    maximum_bytes=MAX_WEBDAV_FILE_DOWNLOAD_BYTES,
                )
                self._validate_sqlite_database(temporary_path)

                if database_path.exists():
                    self._write_sqlite_backup(database_path, backup_path)
                os.replace(temporary_path, database_path)
                temporary_path = None

                # A WAL belonging to the old database can otherwise be replayed
                # against the restored main database on its next open.
                for sidecar in (
                    database_path.with_name(database_path.name + "-wal"),
                    database_path.with_name(database_path.name + "-shm"),
                ):
                    sidecar.unlink(missing_ok=True)

            logger.info("已安全恢复 daily_research SQLite 快照")
            return True
        except Exception as exc:
            logger.error("恢复 daily_research SQLite 快照失败: %s", exc)
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("无法清理 SQLite 下载临时文件: %s", temporary_path)

    def _ensure_remote_dir(self, remote_dir: str) -> bool:
        """Ensure a validated remote directory exists and confirm every level.

        The previous best-effort implementation swallowed all ``mkdir``
        failures, so ``test_connection`` could report success while no usable
        directory had been created.  A race where another client creates the
        same directory remains fine, provided a subsequent PROPFIND confirms
        it exists.
        """
        normalized = normalize_webdav_remote_path(remote_dir)
        if not normalized:
            return self._check_remote("")
        parts = normalized.split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            if self._check_remote(current):
                continue
            try:
                self.client.mkdir(current + "/")
            except Exception as exc:
                # A concurrent creation is the only non-fatal failure path.
                if not self._check_remote(current):
                    logger.warning("创建 WebDAV 远程目录失败 %s: %s", current, exc)
                    return False
            if not self._check_remote(current):
                logger.warning("WebDAV 远程目录创建后仍不可确认: %s", current)
                return False
            logger.debug(f"已创建远程目录: {current}/")
        return True

    def _upload_directory(self, local_dir: Path, remote_dir: str) -> bool:
        """逐文件上传本地目录到远程（不使用 upload_directory 避免删除远程已有文件）。"""
        try:
            if not self._ensure_remote_dir(remote_dir):
                raise RuntimeError("无法创建 WebDAV 目标目录")
            file_count = 0
            for item in local_dir.rglob("*"):
                if item.is_file():
                    relative = item.relative_to(local_dir)
                    # 统一使用 / 分隔符
                    rel_posix = str(relative).replace("\\", "/")
                    remote_file = f"{remote_dir.rstrip('/')}/{rel_posix}"
                    # 确保父目录存在
                    parent_parts = rel_posix.rsplit("/", 1)
                    if len(parent_parts) > 1:
                        parent_remote = f"{remote_dir.rstrip('/')}/{parent_parts[0]}/"
                        if not self._ensure_remote_dir(parent_remote):
                            raise RuntimeError(f"无法创建 WebDAV 目标目录: {parent_remote}")
                    self.client.upload_file(remote_file, str(item))
                    file_count += 1
            logger.info(f"已上传目录 {local_dir.name}/ ({file_count} 个文件)")
            return True
        except Exception as e:
            logger.error(f"上传目录 {local_dir.name}/ 失败: {e}")
            return False


def create_sync_client(
    url: str = "",
    username: str = "",
    password: str = "",
    remote_path: str = "",
) -> Optional[WebDAVSync]:
    """
    创建 WebDAVSync 实例。

    优先使用传入的参数（用于 WebUI 面板直接传值），
    参数为空时回退到 config.settings。

    返回:
        WebDAVSync 实例，配置不完整时返回 None
    """
    try:
        from config import settings

        # 如果未传入参数，从 settings 读取
        if not url:
            url = getattr(settings, "WEBDAV_URL", "")
        if not username:
            username = getattr(settings, "WEBDAV_USERNAME", "")
        if not password:
            password = getattr(settings, "WEBDAV_PASSWORD", "")
        if not remote_path:
            remote_path = getattr(settings, "WEBDAV_REMOTE_PATH", "/arxiv-daily-researcher/")

        if not url or not username:
            logger.warning("WebDAV URL 或用户名未配置")
            return None

        # WebDAV is a backup service, not a reason to route unexpectedly
        # through a global proxy.  Keep its prior behavior only when the
        # notification/network scope has explicitly opted in; older settings
        # objects without ``get_proxy_dict`` safely retain no proxy.
        proxy_url = ""
        try:
            proxy_config = settings.get_proxy_dict("webdav")
        except (AttributeError, TypeError):
            proxy_config = None
        if proxy_config:
            proxy_url = proxy_config.get("https") or proxy_config.get("http") or ""

        return WebDAVSync(
            url=url,
            username=username,
            password=password,
            remote_path=remote_path,
            proxy_url=proxy_url,
        )
    except Exception as e:
        logger.error(f"创建 WebDAV 客户端失败: {e}")
        return None


def sync_after_report(logger_instance=None) -> Optional[Dict[str, Any]]:
    """
    报告生成后的同步钩子。
    仅在 sync_mode 为 'after_report' 时执行。
    """
    try:
        from config import settings

        if not getattr(settings, "WEBDAV_ENABLED", False):
            return None

        sync_mode = getattr(settings, "WEBDAV_SYNC_MODE", "manual")
        if sync_mode != "after_report":
            return None

        return _sync_with_current_settings(logger_instance, reason="报告后")
    except Exception as e:
        if logger_instance:
            logger_instance.warning(f"[WebDAV] 报告后同步失败: {e}")
        else:
            logger.warning(f"[WebDAV] 报告后同步失败: {e}")
        raise


def _sync_with_current_settings(logger_instance=None, *, reason: str) -> Dict[str, Any]:
    """Upload the selected WebDAV scope and fail on a partial result.

    This common path deliberately does not decide *when* to run.  The daily
    report hook, the scheduled cron hook and manual UI sync all have different
    delivery semantics, while a partial remote copy is an error for each of
    them.
    """
    from config import settings

    log = logger_instance or logger
    client = create_sync_client()
    if not client:
        raise RuntimeError("WebDAV 已启用但 URL、用户名或密码不完整")

    include_reports = getattr(settings, "WEBDAV_SYNC_REPORTS", False)
    include_configs = getattr(settings, "WEBDAV_SYNC_CONFIGS", True)
    include_history = getattr(settings, "WEBDAV_SYNC_HISTORY", True)
    include_keywords = getattr(settings, "WEBDAV_SYNC_KEYWORDS", True)
    log.info("[WebDAV] %s同步开始...", reason)
    result = client.sync_all(
        direction="upload",
        include_reports=include_reports,
        include_configs=include_configs,
        include_history=include_history,
        include_keywords=include_keywords,
    )
    failed = [path for path, succeeded in result["results"].items() if not succeeded]
    if failed:
        raise RuntimeError("WebDAV 同步不完整: " + ", ".join(failed))
    log.info(
        "[WebDAV] %s同步完成: %s/%s 成功，耗时 %ss",
        reason,
        result["success"],
        result["total"],
        result["elapsed_seconds"],
    )
    return result


def sync_scheduled(logger_instance=None) -> Optional[Dict[str, Any]]:
    """Run the configured scheduled upload without invoking paper processing."""
    from config import settings

    if not getattr(settings, "WEBDAV_ENABLED", False):
        return None
    if getattr(settings, "WEBDAV_SYNC_MODE", "manual") != "scheduled":
        return None
    return _sync_with_current_settings(logger_instance, reason="定时")


def after_report_sync_maintenance_entry(run_id: str) -> Optional[Dict[str, Any]]:
    """Build the durable task that must be committed with report delivery."""
    from config import settings

    if not getattr(settings, "WEBDAV_ENABLED", False):
        return None
    if getattr(settings, "WEBDAV_SYNC_MODE", "manual") != "after_report":
        return None
    return {
        "task_key": f"webdav_after_report:{run_id}",
        "payload": {"run_id": run_id, "task_type": "webdav_after_report"},
    }


def enqueue_after_report_sync(store, run_id: str) -> bool:
    """Compatibility helper for callers that are not in report finalization."""
    entry = after_report_sync_maintenance_entry(run_id)
    if entry is None:
        return False
    return store.enqueue_maintenance_task(entry["task_key"], entry["payload"])


def deliver_pending_after_report_syncs(store, logger_instance=None, limit: int = 10) -> Dict[str, int]:
    """Run due WebDAV upload tasks without affecting daily paper delivery state."""
    from config import settings

    log = logger_instance or logger
    # A task was created only when this mode was enabled.  If the user later
    # disables WebDAV or switches modes, leave it pending rather than treating
    # an intentional no-op as a successful upload.
    if not getattr(settings, "WEBDAV_ENABLED", False) or getattr(
        settings, "WEBDAV_SYNC_MODE", "manual"
    ) != "after_report":
        return {"claimed": 0, "completed": 0, "deferred": 0}

    rows = store.claim_due_maintenance_tasks(prefix="webdav_after_report:", limit=limit)
    summary = {"claimed": len(rows), "completed": 0, "deferred": 0}
    max_attempts = max(1, int(getattr(settings, "RETRY_MAX_ATTEMPTS", 3)))
    min_wait = max(1, int(getattr(settings, "RETRY_MIN_WAIT", 2)))
    max_wait = max(min_wait, int(getattr(settings, "RETRY_MAX_WAIT", 30)))

    for row in rows:
        task_key = row["task_key"]
        try:
            sync_after_report(log)
        except Exception as exc:
            # Keep retrying across future daily invocations.  The per-attempt
            # delay is capped, so a transient outage never strands the report
            # indefinitely or blocks the next paper scan.
            retry_exponent = min(max(0, row["attempt_count"] - 1), max_attempts - 1)
            retry_after = min(min_wait * (2**retry_exponent), max_wait)
            store.reschedule_maintenance_task(task_key, str(exc), retry_after)
            log.error(
                "[WebDAV] 同步任务失败，已保留待补发 (%s, %ss 后重试): %s",
                task_key,
                retry_after,
                exc,
            )
            summary["deferred"] += 1
        else:
            store.mark_maintenance_task_completed(task_key)
            summary["completed"] += 1

    return summary
