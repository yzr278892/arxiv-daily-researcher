"""后台作业的空闲等待：等待持锁释放、超时返回 False。"""

import fcntl
import multiprocessing
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.run_lock import (  # noqa: E402
    busy_lock_files,
    daily_workflow_gate,
    legacy_import_activity_gate,
    wait_for_idle,
)


def _hold_shared_gate(lock_path: str, ready, release) -> None:
    """Hold the normal-worker side of the import gate in another process."""
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
        ready.set()
        release.wait(10)


def _hold_exclusive_gate(lock_path: str, ready, release) -> None:
    """Hold a daily-workflow gate in another process."""
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(10)


class WaitForIdleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lock_dir = Path(self.tmp.name) / "run"
        self.lock_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    @patch("utils.run_lock._lock_dir")
    def test_gate_waits_forever_by_default(self, mock_dir):
        """Without a configured timeout the queued request still runs."""
        from utils import run_lock as run_lock_module

        mock_dir.return_value = self.lock_dir
        holder = (self.lock_dir / run_lock_module.DAILY_WORKFLOW_GATE).open("a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        entered = threading.Event()
        errors = []

        def run_gate():
            try:
                with daily_workflow_gate():
                    entered.set()
            except BaseException as exc:  # pragma: no cover - defensive
                errors.append(exc)

        with patch.object(run_lock_module.time, "sleep", return_value=None):
            thread = threading.Thread(target=run_gate)
            thread.start()
            self.assertFalse(entered.wait(0.2))
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            thread.join(timeout=5)

        holder.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(entered.is_set())

    @patch("utils.run_lock._lock_dir")
    def test_configured_gate_timeout_raises_and_warns(self, mock_dir):
        """A configured timeout fails visibly instead of queueing forever."""
        from utils import run_lock as run_lock_module

        mock_dir.return_value = self.lock_dir
        holder = (self.lock_dir / run_lock_module.DAILY_WORKFLOW_GATE).open("a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger = Mock()
        clock = [0.0]

        def fake_sleep(seconds):
            clock[0] += seconds

        with patch.object(
            run_lock_module.time, "monotonic", side_effect=lambda: clock[0]
        ), patch.object(run_lock_module.time, "sleep", side_effect=fake_sleep):
            with self.assertRaises(run_lock_module.RunLockWaitTimeout):
                with daily_workflow_gate(logger=logger, wait_timeout_seconds=2.0):
                    self.fail("a held gate must not be acquired")

        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
        self.assertTrue(logger.warning.called)
        self.assertIn(
            "排队超时", " ".join(str(argument) for argument in logger.warning.call_args.args)
        )

    @patch("utils.run_lock._lock_dir")
    def test_returns_true_when_no_locks_held(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        self.assertEqual(busy_lock_files(["daily_research"]), [])
        self.assertTrue(wait_for_idle(["daily_research"], poll_seconds=0.01))

    @patch("utils.run_lock._lock_dir")
    def test_waits_until_lock_released(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        lock_file = (self.lock_dir / "daily_research.lock").open("a+")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            # 超时路径：锁始终被持有。
            self.assertFalse(
                wait_for_idle(
                    ["daily_research"], poll_seconds=0.01, timeout_seconds=0.05
                )
            )
            self.assertEqual(
                [p.name for p in busy_lock_files(["daily_research", "trend_research_*"])],
                ["daily_research.lock"],
            )
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        self.assertTrue(wait_for_idle(["daily_research"], poll_seconds=0.01))

    @patch("utils.run_lock._lock_dir")
    def test_exclusive_import_gate_waits_for_existing_worker_activity(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        holder = multiprocessing.Process(
            target=_hold_shared_gate,
            args=(str(self.lock_dir / ".legacy_import_activity.gate"), ready, release),
        )
        holder.start()
        self.addCleanup(lambda: release.set())
        self.addCleanup(lambda: holder.join(timeout=5))
        self.assertTrue(ready.wait(5))

        acquired = threading.Event()

        def acquire_import_gate():
            with legacy_import_activity_gate(exclusive=True):
                acquired.set()

        waiter = threading.Thread(target=acquire_import_gate)
        waiter.start()
        self.assertFalse(acquired.wait(0.1))

        release.set()
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive())
        self.assertTrue(acquired.is_set())

    @patch("utils.run_lock._lock_dir")
    def test_daily_workflow_gate_serializes_daily_style_runs(self, mock_dir):
        mock_dir.return_value = self.lock_dir
        ready = multiprocessing.Event()
        release = multiprocessing.Event()
        holder = multiprocessing.Process(
            target=_hold_exclusive_gate,
            args=(str(self.lock_dir / ".daily_workflow.gate"), ready, release),
        )
        holder.start()
        self.addCleanup(lambda: release.set())
        self.addCleanup(lambda: holder.join(timeout=5))
        self.assertTrue(ready.wait(5))

        acquired = threading.Event()
        waiter = threading.Thread(
            target=lambda: _enter_daily_gate(acquired)
        )
        waiter.start()
        self.assertFalse(acquired.wait(0.1))

        release.set()
        waiter.join(timeout=5)
        self.assertFalse(waiter.is_alive())
        self.assertTrue(acquired.is_set())


def _enter_daily_gate(acquired) -> None:
    with daily_workflow_gate():
        acquired.set()


if __name__ == "__main__":
    unittest.main()
