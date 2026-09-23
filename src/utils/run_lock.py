"""
任务并发锁 — 防止完全相同的任务同时执行。

使用文件独占锁（fcntl.LOCK_EX | LOCK_NB），进程退出后锁自动释放。
锁文件本身会保留为稳定的锁锚点；不能根据其中的 PID 删除它，也不能
为“超龄”任务发送信号，因为 PID 可能已经被无关进程复用。

锁文件目录：data/run/
  daily_research.lock                — 每日研究（同时只允许一个）
  trend_research_<params_hash>.lock  — 趋势研究（相同参数同时只允许一个）
"""

import fcntl
import hashlib
import os
import re
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# 专用退出码：相同任务已在运行而被跳过（沿用 EX_TEMPFAIL 惯例取值）。
# WebUI 触发链路据此区分“真的跑完”和“被锁跳过”；cron 直接调用时
# 非零返回码只进日志，无副作用。
LOCK_SKIPPED_EXIT_CODE = 75

# 面板触发的后台作业模式。保留该常量给兼容调用方和运行状态展示使用。
AUX_JOB_MODES = (
    "legacy_import",
    "history_data_repair",
    "history_omission_scan",
    "supplement_run",
    "backfill_run",
)

# 旧历史导入会批量写入同一个 SQLite 状态库，不能只依赖“先检查再运行”
# 的锁文件轮询：检查结束到进程真正启动之间仍可能有每日任务插入。这个
# 隐藏 gate 用 flock 的共享/独占语义消除该竞态：普通 worker 作业持共享锁，
# 历史导入持独占锁。它故意不以 ``.lock`` 结尾，因此不会被运行管理面板误
# 当成一个可停止的任务；真实任务仍由各自的 ``run_lock`` 展示和控制。
LEGACY_IMPORT_ACTIVITY_GATE = ".legacy_import_activity.gate"

# 正常日报、补充报告和过去日报都写同一个论文队列/交付账本，因此三者
# 之间也必须原子串行。它与上面的旧历史 gate 分工：前者保护日常流水线
# 家族，后者让历史导入与所有普通 worker 活动隔离。
DAILY_WORKFLOW_GATE = ".daily_workflow.gate"

# 数据库恢复会以原子替换的方式切换 SQLite 文件。它不能与任何正在使用
# SQLite 的 worker 任务重叠，否则任务可能继续向已被替换的旧 inode 写入。
# 所有 ``run_lock`` 持有共享锁；WebUI 恢复持非阻塞独占锁，遇到活动任务时
# 明确拒绝恢复，由用户在任务完成后重新提交。
DATABASE_RESTORE_ACTIVITY_GATE = ".database_restore_activity.gate"


class DatabaseRestoreBusyError(RuntimeError):
    """Raised when an exclusive database restore overlaps a worker activity."""


def _lock_dir() -> Path:
    try:
        from config import settings

        d = Path(settings.DATA_DIR) / "run"
    except Exception:
        d = Path("data/run")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _params_hash(
    keywords: List[str],
    date_from,
    date_to,
    categories: Optional[List[str]],
) -> str:
    key = "|".join(
        [
            ",".join(sorted(str(k) for k in keywords)),
            str(date_from),
            str(date_to),
            ",".join(sorted(str(c) for c in (categories or []))),
        ]
    )
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _parse_lock_info(content: str):
    """Parse diagnostic PID and start time; neither is an authority to kill."""
    pid = None
    started_at = None

    m_pid = re.search(r"PID=(\d+)", content or "")
    if m_pid:
        pid = int(m_pid.group(1))

    started_pattern = r"started=([0-9]{4}-[0-9]{2}-[0-9]{2} " r"[0-9]{2}:[0-9]{2}:[0-9]{2})"
    m_started = re.search(started_pattern, content or "")
    if m_started:
        try:
            started_at = datetime.strptime(m_started.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            started_at = None

    return pid, started_at


class RunLockWaitTimeout(RuntimeError):
    """Raised when a caller-supplied gate wait timeout expires without the lock.

    The default wait is deliberately durable (a queued request runs as soon as
    the holder finishes).  A caller that prefers a visible failure passes an
    explicit ``wait_timeout_seconds``; there is no implicit timeout anywhere.
    """


def _expired_lock_message(lock_file, task_desc: str, max_age_hours: int) -> Optional[str]:
    """Return a safe diagnostic message for an unusually long held lock.

    ``flock`` is the sole source of truth for mutual exclusion.  A timestamp
    and PID are only human-readable diagnostics: a PID can be reused and a
    valid paper/LLM job can legitimately outlive its estimated duration.
    """
    if max_age_hours <= 0:
        return None

    try:
        lock_file.seek(0)
        info = lock_file.read().strip()
    except Exception:
        return None

    pid, started_at = _parse_lock_info(info)
    if not started_at:
        return None

    age_seconds = (datetime.now() - started_at).total_seconds()
    if age_seconds <= max_age_hours * 3600:
        return None

    pid_detail = f" PID={pid}" if pid is not None else ""
    lock_label = str(getattr(lock_file, "name", "") or "")
    try:
        mtime_label = datetime.fromtimestamp(
            os.fstat(lock_file.fileno()).st_mtime
        ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        mtime_label = "未知"
    return (
        f"⚠️  检测到超龄运行锁（已持有约 {age_seconds / 3600.0:.1f}h，"
        f"超过 {max_age_hours}h 阈值），任务仍持有内核锁，本次不启动: {task_desc}。"
        f"锁文件: {lock_label or '未知'}（mtime={mtime_label}，"
        f"记录{pid_detail} started={started_at:%Y-%m-%d %H:%M:%S}）。"
        f"如确认该任务已卡死，请在运行面板 stop 该任务后重新触发，或重启服务；"
        f"为避免 PID 复用误杀，不会自动终止进程。"
    )


def is_lock_held(lock_path: Path) -> bool:
    """Safely check a lock's current kernel state without trusting its PID.

    This is useful for diagnostics/UI.  Do not delete a lock file based on the
    result: retaining one stable inode avoids a race where a new process locks
    a replacement file while another process still owns the old inode.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            return True
        else:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            return False
    finally:
        lock_file.close()


def busy_lock_files(patterns) -> list[Path]:
    """Return currently held lock files matching names/globs (no suffix needed)."""
    lock_dir = _lock_dir()
    busy: list[Path] = []
    for pattern in patterns or ():
        for candidate in sorted(lock_dir.glob(f"{pattern}.lock")):
            if is_lock_held(candidate):
                busy.append(candidate)
    return busy


def wait_for_idle(
    patterns,
    *,
    poll_seconds: float = 30.0,
    timeout_seconds: Optional[float] = None,
    logger=None,
    wait_note: str = "",
) -> bool:
    """Block until none of the given lock patterns is held.

    Used by background jobs (legacy import, supplement runs) that must not
    overlap a live daily run, and by the daily run itself to wait for those
    jobs.  Returns True when idle; False on timeout (the caller decides
    whether proceeding is safe).
    """
    import time as _time

    deadline = None
    if timeout_seconds is not None and timeout_seconds >= 0:
        deadline = _time.monotonic() + timeout_seconds
    notified = False
    while True:
        busy = busy_lock_files(patterns)
        if not busy:
            return True
        if not notified and logger is not None and wait_note:
            logger.info(
                "%s：等待 %s 释放（每 %s 秒复查）", wait_note, ", ".join(p.name for p in busy), int(poll_seconds)
            )
            notified = True
        if deadline is not None and _time.monotonic() >= deadline:
            if logger is not None:
                logger.warning(
                    "等待锁超时（%s 秒），仍被持有: %s", int(timeout_seconds), ", ".join(p.name for p in busy)
                )
            return False
        _time.sleep(max(1.0, float(poll_seconds)))


@contextmanager
def _activity_gate(
    gate_name: str,
    *,
    operation: int,
    logger=None,
    wait_note: str = "",
    default_wait_note: str = "任务等待其他任务完成",
    wait_timeout_seconds: Optional[float] = None,
):
    """Acquire one hidden blocking flock gate and always release it safely.

    ``flock`` remains the sole mutual-exclusion authority: waiting only polls
    the non-blocking acquire.  The wait is infinite unless the caller passes an
    explicit ``wait_timeout_seconds`` (a queued request normally starts as soon
    as the holder finishes; a timeout turns a stuck holder into a visible
    failure instead of an unbounded queue).  ``time.sleep`` stays
    interruptible, so a stop request wakes the waiter and it retries at once.
    """
    gate_path = _lock_dir() / gate_name
    gate_file = gate_path.open("a+")
    acquired = False
    timeout_seconds = 0.0 if wait_timeout_seconds is None else float(wait_timeout_seconds)
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    try:
        waiting_message = wait_note or default_wait_note
        next_wait_log_at = 0.0
        while True:
            try:
                fcntl.flock(gate_file.fileno(), operation | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                now = time.monotonic()
                if logger is not None and now >= next_wait_log_at:
                    logger.info("%s（仍在等待，每 30 秒更新一次）", waiting_message)
                    next_wait_log_at = now + 30.0
                if deadline is not None and now >= deadline:
                    if logger is not None:
                        logger.warning(
                            "排队超时（%d 秒），未获得运行权: %s",
                            int(timeout_seconds),
                            waiting_message,
                        )
                    raise RunLockWaitTimeout(
                        f"排队超时，未获得运行权: {gate_name}"
                    )
                # Do not block indefinitely inside flock: a short retry makes
                # waiting visible in the run log and remains interruptible by
                # the WebUI stop request.
                time.sleep(1.0)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(gate_file.fileno(), fcntl.LOCK_UN)
        finally:
            gate_file.close()


@contextmanager
def legacy_import_activity_gate(
    *,
    exclusive: bool = False,
    logger=None,
    wait_note: str = "",
    wait_timeout_seconds: Optional[float] = None,
):
    """Atomically coordinate legacy import with normal worker activity.

    ``exclusive=True`` is reserved for the v3.2 history importer.  Normal
    worker flows acquire a shared lock, so their established concurrency
    behaviour is preserved while an import waits until they are all idle.
    Once the importer owns the exclusive lock, newly started normal flows wait
    rather than touching the database concurrently.  This is deliberately a
    blocking, durable queue point: a click on "Read Legacy History" should run
    automatically after the active work has finished, not fail because a
    polling timeout happened to expire.
    """
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    default_wait_note = (
        "旧历史导入等待其他任务空闲"
        if exclusive
        else "任务等待旧历史导入完成"
    )
    with _activity_gate(
        LEGACY_IMPORT_ACTIVITY_GATE,
        operation=operation,
        logger=logger,
        wait_note=wait_note,
        default_wait_note=default_wait_note,
        wait_timeout_seconds=wait_timeout_seconds,
    ):
        yield


@contextmanager
def daily_workflow_gate(
    *, logger=None, wait_note: str = "", wait_timeout_seconds: Optional[float] = None
):
    """Serialize daily, supplement and past-date pipeline executions.

    This gate deliberately blocks rather than treating a different daily-style
    mode as a successful no-op.  Its caller has already acquired the visible
    per-mode ``run_lock``; this hidden gate is solely the atomic protection for
    the shared SQLite queue and delivery ledger.
    """
    with _activity_gate(
        DAILY_WORKFLOW_GATE,
        operation=fcntl.LOCK_EX,
        logger=logger,
        wait_note=wait_note,
        default_wait_note="每日研究流水线等待其他每日类任务完成",
        wait_timeout_seconds=wait_timeout_seconds,
    ):
        yield


@contextmanager
def database_restore_activity_gate(
    *,
    exclusive: bool = False,
    nonblocking: bool = False,
    data_dir: Optional[Path] = None,
):
    """Coordinate an SQLite restore with all worker tasks across containers.

    Worker task contexts take a shared lock through :func:`run_lock`.  A
    WebUI restore takes an exclusive lock and deliberately uses
    ``nonblocking=True``: a browser request must report that a task is active
    instead of waiting for an arbitrary LLM run and then replacing its
    database unexpectedly.

    ``data_dir`` lets the thin WebUI use a configured non-default data
    directory without importing the full worker settings module.
    """
    directory = Path(data_dir) / "run" if data_dir is not None else _lock_dir()
    directory.mkdir(parents=True, exist_ok=True)
    gate_path = directory / DATABASE_RESTORE_ACTIVITY_GATE
    gate_file = gate_path.open("a+")
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if nonblocking:
        operation |= fcntl.LOCK_NB
    acquired = False
    try:
        try:
            fcntl.flock(gate_file.fileno(), operation)
        except (IOError, OSError) as exc:
            if exclusive and nonblocking:
                raise DatabaseRestoreBusyError("数据库正被运行任务使用") from exc
            raise
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(gate_file.fileno(), fcntl.LOCK_UN)
        finally:
            gate_file.close()


@contextmanager
def run_lock(
    mode: str,
    keywords: Optional[List[str]] = None,
    date_from=None,
    date_to=None,
    categories: Optional[List[str]] = None,
):
    """
    获取运行锁；若相同任务已在运行则打印提示并以 LOCK_SKIPPED_EXIT_CODE(75) 退出。

    用法:
        with run_lock("daily_research"):
            DailyResearchPipeline().run()

        with run_lock(
            "trend_research", keywords=[...], date_from=..., date_to=..., categories=[...]
        ):
            TrendResearchPipeline(...).run()
    """
    if mode == "trend_research" and keywords:
        h = _params_hash(keywords, date_from, date_to, categories)
        fname = f"trend_research_{h}.lock"
        task_desc = f"trend_research [keywords={keywords}, {date_from}~{date_to}"
        if categories:
            task_desc += f", categories={categories}"
        task_desc += "]"
    else:
        fname = f"{mode}.lock"
        task_desc = mode

    lock_path = _lock_dir() / fname

    try:
        from config import settings

        max_age_hours = int(getattr(settings, "RUN_LOCK_MAX_AGE_HOURS", 12))
    except Exception:
        max_age_hours = 12

    # Keep the path stable across runs.  The operating system releases flock
    # automatically after a SIGKILL, so a leftover diagnostic file is harmless.
    lock_file = open(lock_path, "a+")

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        try:
            lock_file.seek(0)
            info = lock_file.read().strip()
        except Exception:
            info = ""
        expired_message = _expired_lock_message(lock_file, task_desc, max_age_hours)
        lock_file.close()

        print("\n⚠️  相同任务正在运行中，跳过本次执行")
        print(f"   任务: {task_desc}")
        if info:
            print(f"   运行信息: {info}")
        if expired_message:
            print(f"   {expired_message}")
        print(f"   锁文件: {lock_path}\n")
        sys.exit(LOCK_SKIPPED_EXIT_CODE)

    # 写入诊断信息方便排查
    try:
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(
            f"PID={os.getpid()}, started={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        lock_file.flush()
    except Exception:
        pass

    # Turn SIGTERM into KeyboardInterrupt so pipeline-level interruption
    # handling can persist failed state before the context manager releases the
    # flock.  ``main.py`` maps an uncaught interruption to exit code 130 rather
    # than incorrectly reporting a successful scheduled run.
    _old_sigterm = signal.getsignal(signal.SIGTERM)

    def _sigterm_handler(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        # The visible task lock remains the status/stop authority.  This
        # shared gate is only for atomic coordination with a database restore
        # requested by the separate WebUI process.
        with database_restore_activity_gate():
            yield
    finally:
        signal.signal(signal.SIGTERM, _old_sigterm)
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass
