"""Authoritative SQLite history and resumable state for daily research."""

import json
import re
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.analysis_agent import Stage2Response
    from sources.base_source import PaperMetadata


# Learning-signal strengths for the learned preference scoring mode.  An
# explicit like/dislike dominates; a legacy v1 pass contributes a mild positive
# nudge so the original scoring keeps shaping the learned library too.
PREFERENCE_SIGNALS = {"like": 1.0, "dislike": -1.0, "none": 0.0}
V1_PASS_SIGNAL_STRENGTH = 0.25

# app_state key holding the active daily run's phase heartbeat
# (JSON: {run_id, phase, updated_at}); cleared when the run reaches a
# terminal state and consumed by the WebUI progress panel.
_RUN_PHASE_STATE_KEY = "daily_run_phase"


class DailyResearchStore:
    """Small SQLite store for daily research runs and paper state."""

    # Source APIs only expose a day-granularity query.  Always rescan one
    # extra day around a completed scan boundary: this protects papers near a
    # boundary as well as a short upstream indexing delay.  Exact-version
    # delivery de-duplication makes the overlap safe and cheap in terms of
    # downstream LLM work.
    SCAN_RECOVERY_OVERLAP_DAYS = 1

    # These values come from optional, best-effort enrichment services.  They
    # must not disappear merely because a later retry happens while the
    # enrichment service is rate-limited or temporarily unavailable.  Core
    # bibliographic metadata deliberately remains fresh on every scan.
    _OPTIONAL_ENRICHMENT_FIELDS = (
        "semantic_scholar_tldr",
        "arxiv_id",
        "arxiv_url",
        "pdf_url",
    )

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    @staticmethod
    def _paper_identity_or_none():
        """Migration-time identity helper; None in the thin WebUI image.

        The lightweight WebUI image deliberately ships no paper-source modules.  The
        canonical-id backfill below then runs on the worker's next connect
        instead — the schema columns are still added here, so nothing
        diverges.
        """
        try:
            from sources.base_source import paper_identity

            return paper_identity
        except ImportError:
            return None

    @staticmethod
    def _migration_identity(
        source: object,
        paper_id: object,
        canonical_id: object,
        version: object,
        paper_identity,
    ) -> tuple[str, int]:
        """Return a safe persisted identity while upgrading an older database.

        arXiv versions are recoverable from their paper IDs.  Other sources
        may use a normalized DOI (rather than their URL-shaped paper ID), so
        preserve that stored identity whenever possible.  Legacy HTML imports
        are the important exception: their paper IDs are DOI URLs, which can
        be normalized deterministically before an exact-delivery index is
        restored.
        """
        source_text = str(source or "").strip().lower()
        paper_text = str(paper_id or "").strip()
        canonical_text = str(canonical_id or "").strip()
        try:
            normalized_version = max(0, int(version or 0))
        except (TypeError, ValueError):
            normalized_version = 0

        if source_text == "arxiv":
            canonical, parsed_version = paper_identity(source_text, paper_text)
            return canonical, parsed_version if parsed_version is not None else 0

        def normalize_doi_url(value: str) -> str:
            lowered = value.lower()
            for prefix in (
                "https://doi.org/",
                "http://doi.org/",
                "https://dx.doi.org/",
                "http://dx.doi.org/",
            ):
                if lowered.startswith(prefix):
                    return value[len(prefix) :].strip().rstrip("/.").lower()
            if lowered.startswith("10."):
                return value.rstrip("/.").lower()
            return value

        # A DOI URL is more precise than an old, URL-shaped canonical field.
        # Otherwise retain the existing canonical value from sources whose
        # opaque IDs cannot be reconstructed from a generic paper ID.
        normalized_paper = normalize_doi_url(paper_text)
        normalized_canonical = normalize_doi_url(canonical_text or paper_text)
        return (
            normalized_paper if normalized_paper != paper_text else normalized_canonical,
            normalized_version,
        )

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    scan_started_at TEXT,
                    scan_days INTEGER,
                    scanned_sources_json TEXT,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    total_papers INTEGER DEFAULT 0,
                    error TEXT,
                    report_paths_json TEXT,
                    run_kind TEXT NOT NULL DEFAULT 'daily'
                )
                """
            )
            self._migrate_run_scan_state(conn)
            self._migrate_run_kind(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_papers (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 0,
                    entity_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    run_id TEXT,
                    paper_json TEXT NOT NULL,
                    score_json TEXT,
                    score_audit_json TEXT,
                    abstract_cn TEXT,
                    analysis_json TEXT,
                    scored_at TEXT,
                    translated_at TEXT,
                    analyzed_at TEXT,
                    score_input_fingerprint TEXT,
                    translation_input_fingerprint TEXT,
                    analysis_input_fingerprint TEXT,
                    completed_at TEXT,
                    last_error TEXT,
                    tldr_status TEXT NOT NULL DEFAULT 'pending',
                    report_repair_status TEXT NOT NULL DEFAULT 'not_needed',
                    report_repair_error TEXT,
                    legacy_report_at TEXT,
                    queue_scope TEXT NOT NULL DEFAULT 'daily',
                    backfill_target_date TEXT,
                    PRIMARY KEY (source, paper_id)
                )
                """
            )
            self._migrate_paper_identity(conn)
            self._migrate_paper_queue_scope(conn)
            self._migrate_stage_state(conn)
            self._migrate_tldr_state(conn)
            self._migrate_report_repair_state(conn)
            self._migrate_legacy_report_timestamp(conn)
            self._migrate_stage_fingerprints(conn)
            self._migrate_score_audit_state(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_papers_run ON daily_papers(run_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_completed ON daily_papers(completed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_scope_pending "
                "ON daily_papers(queue_scope, completed_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_identity "
                "ON daily_papers(source, canonical_id, version)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_deliveries (
                    delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    report_path TEXT,
                    report_at TEXT,
                    delivered_at TEXT NOT NULL,
                    UNIQUE(run_id, source, paper_id)
                )
                """
            )
            self._migrate_delivery_identity(conn)
            self._migrate_delivery_report_timestamp(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_deliveries_identity "
                "ON paper_deliveries(source, canonical_id, version)"
            )
            self._migrate_delivery_exact_version_constraint(conn)

            # An outbox makes notification delivery independent from paper completion.
            # A notification can be retried without allowing the paper back into a
            # later daily scan as a supposedly new result.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claimed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(run_id, event_type, channel)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_notification_outbox_pending "
                "ON notification_outbox(status, next_attempt_at)"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_outbox (
                    task_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claimed_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_maintenance_outbox_pending "
                "ON maintenance_outbox(status, next_attempt_at)"
            )

            # A per-source checkpoint is advanced only in the same
            # transaction that completes a successful daily scan/report.  If
            # any fetch, scoring, translation, analysis, report generation,
            # or final delivery commit fails, its old checkpoint remains in
            # place and the next run expands its lookback window instead of
            # letting those papers age out of the fixed daily scan window.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_scan_watermarks (
                    source TEXT PRIMARY KEY,
                    successful_scan_started_at TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # Scan receipts complement the checkpoint; they never replace it.
            # A watermark answers "what interval is safe to recover from?",
            # while this table answers "what did this run actually query?".
            # Keep failed receipts too: they are exactly the evidence needed to
            # distinguish a genuinely quiet day from an incomplete scan.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_scan_receipts (
                    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(run_id, source)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_scan_receipts_run "
                "ON daily_scan_receipts(run_id, receipt_id)"
            )
            # Append-only, cross-workflow source observations.  Daily scan
            # receipts remain the authoritative checkpoint evidence and keep
            # their one-row-per-run/source shape; this table additionally
            # records historical range scans, supplement metadata lookups and
            # optional enrichment requests for the health panel.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_health_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    task_kind TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                    candidate_count INTEGER,
                    error_summary TEXT,
                    occurred_at TEXT NOT NULL,
                    origin_key TEXT UNIQUE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_health_events_source_time "
                "ON source_health_events(source, occurred_at DESC, event_id DESC)"
            )
            self._backfill_source_health_events(conn)
            self._migrate_backfill_target_dates(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_daily_papers_backfill_pending "
                "ON daily_papers(queue_scope, backfill_target_date, completed_at)"
            )
            # Small key/value scratch state for cross-run decisions such as
            # "this remote version was already announced". Values are opaque
            # strings owned by the caller; nothing here is ever deleted.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # LLM token usage per run and model, persisted when a run ends.
            # Trend runs use a synthetic run id and mode='trend_research'.
            # Rows are append-only history; nothing is ever pruned.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_token_usage (
                    run_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'daily_research',
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, model)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_token_usage_recorded "
                "ON run_token_usage(recorded_at)"
            )
            # Lightweight health observations from *real* LLM calls.  They
            # intentionally sit beside run/token history so the WebUI can
            # inspect one authoritative local database without making an
            # extra paid provider request.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_health_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK (role IN ('cheap', 'smart')),
                    model TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed')),
                    error_summary TEXT,
                    occurred_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_health_events_role_time "
                "ON llm_health_events(role, occurred_at DESC, event_id DESC)"
            )
            # Reader-owned paper preferences (like/dislike). Rows are never
            # deleted: clearing a preference writes 'none' so the history of
            # what was marked stays intact. Snapshots of title/authors keep
            # each row self-contained even if the paper is later re-queued.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_preferences (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    canonical_id TEXT,
                    version INTEGER,
                    preference TEXT NOT NULL
                        CHECK (preference IN ('like', 'dislike', 'none')),
                    title TEXT NOT NULL,
                    authors_json TEXT NOT NULL DEFAULT '[]',
                    categories_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, paper_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_paper_preferences_updated "
                "ON paper_preferences(updated_at)"
            )

            # Learned-preference evidence. Rows are upserted in place (never
            # deleted), so a changed opinion updates its own contribution and
            # the aggregate weight evolves with every new signal.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preference_learning_signals (
                    source TEXT NOT NULL,
                    paper_id TEXT NOT NULL,
                    term TEXT NOT NULL,
                    term_type TEXT NOT NULL
                        CHECK (term_type IN ('keyword', 'author')),
                    signal_kind TEXT NOT NULL
                        CHECK (signal_kind IN ('preference', 'v1_pass')),
                    signal REAL NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (source, paper_id, term, term_type, signal_kind)
                )
                """
            )

            # Papers that need a supplement run: legacy entries whose data is
            # incomplete (missing card / translation / analysis) and papers a
            # historical range scan found to be entirely missing.  Rows are
            # resolved by marking them delivered; failed fetch attempts stay
            # re-selectable so a later supplement run can retry them.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS supplement_backlog (
                    backlog_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    paper_id TEXT,
                    reason TEXT NOT NULL
                        CHECK (reason IN (
                            'missing_data', 'missing_analysis',
                            'missing_translation', 'missed_scan'
                        )),
                    detail TEXT,
                    paper_json TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivered', 'failed', 'skipped')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_run_id TEXT,
                    UNIQUE(source, canonical_id, version)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_supplement_backlog_status "
                "ON supplement_backlog(status, reason, created_at)"
            )

            # A requested historical date range is durable work, not merely a
            # transient WebUI click.  Each calendar day gets its own queue row
            # so the worker can process reports sequentially and retain the
            # remaining dates across a restart or a per-day failure.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backfill_queue (
                    backfill_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
                    requested_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    run_id TEXT,
                    error TEXT,
                    UNIQUE(batch_id, target_date)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_backfill_queue_pending "
                "ON backfill_queue(status, requested_at, target_date, backfill_id)"
            )
            self._migrate_paper_entities(conn)

    @staticmethod
    def _migrate_run_scan_state(conn):
        """Add scan audit columns to databases created before recovery watermarks."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(daily_runs)").fetchall()
        }
        additions = {
            "scan_started_at": "TEXT",
            "scan_days": "INTEGER",
            "scanned_sources_json": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_runs ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_run_kind(conn):
        """Tag pre-4.1 run rows as ordinary daily runs."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(daily_runs)").fetchall()
        }
        if "run_kind" not in columns:
            conn.execute(
                "ALTER TABLE daily_runs ADD COLUMN run_kind TEXT NOT NULL DEFAULT 'daily'"
            )

    @staticmethod
    def _migrate_paper_identity(conn):
        """Add identity columns to databases created by the first persistence patch."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "canonical_id" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers ADD COLUMN canonical_id TEXT NOT NULL DEFAULT ''"
            )
        if "version" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )

        paper_identity = DailyResearchStore._paper_identity_or_none()
        if paper_identity is None:
            return

        rows = conn.execute(
            "SELECT source, paper_id, canonical_id, version FROM daily_papers"
        ).fetchall()
        for row in rows:
            canonical_id, desired_version = DailyResearchStore._migration_identity(
                row["source"],
                row["paper_id"],
                row["canonical_id"],
                row["version"],
                paper_identity,
            )
            if row["canonical_id"] != canonical_id or row["version"] != desired_version:
                conn.execute(
                    "UPDATE daily_papers SET canonical_id = ?, version = ? "
                    "WHERE source = ? AND paper_id = ?",
                    (canonical_id, desired_version, row["source"], row["paper_id"]),
                )

    @staticmethod
    def _migrate_paper_queue_scope(conn):
        """Keep deferred past-date candidates out of ordinary daily runs.

        Backfill runs use the same durable paper ledger as the normal daily
        workflow. Before this scope existed, a capped past-date run left its
        unselected papers in the ordinary pending queue, so the next current
        daily report could silently include old papers. Existing uncompleted
        rows whose last owning run was a backfill are safely quarantined too.
        """
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "queue_scope" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers "
                "ADD COLUMN queue_scope TEXT NOT NULL DEFAULT 'daily'"
            )
        conn.execute(
            """
            UPDATE daily_papers
            SET queue_scope = 'backfill'
            WHERE completed_at IS NULL
              AND queue_scope = 'daily'
              AND EXISTS (
                  SELECT 1
                  FROM daily_runs
                  WHERE daily_runs.run_id = daily_papers.run_id
                    AND daily_runs.run_kind = 'backfill'
              )
            """
        )

    @staticmethod
    def _migrate_backfill_target_dates(conn):
        """Add and backfill the historical date that owns queued papers.

        A queue row represents one calendar day.  Before this column existed,
        a capped backfill could leave its remaining papers in the generic
        ``backfill`` scope with no way to know which queue day must resume
        them.  Existing rows can be recovered from their arXiv scan receipt,
        whose immutable payload already records ``backfill_date``.
        """
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "backfill_target_date" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers ADD COLUMN backfill_target_date TEXT"
            )

        rows = conn.execute(
            """
            SELECT papers.source, papers.paper_id, receipts.receipt_json
            FROM daily_papers AS papers
            JOIN daily_scan_receipts AS receipts
              ON receipts.run_id = papers.run_id
             AND receipts.source = 'arxiv'
            WHERE papers.queue_scope = 'backfill'
              AND papers.backfill_target_date IS NULL
            """
        ).fetchall()
        updates = []
        for row in rows:
            try:
                payload = json.loads(row["receipt_json"] or "{}")
                target = date.fromisoformat(str(payload.get("backfill_date") or ""))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            updates.append((target.isoformat(), row["source"], row["paper_id"]))
        if updates:
            conn.executemany(
                """
                UPDATE daily_papers
                SET backfill_target_date = ?
                WHERE source = ? AND paper_id = ?
                  AND backfill_target_date IS NULL
                """,
                updates,
            )

    @staticmethod
    def _migrate_stage_state(conn):
        """Add explicit stage states to databases created before the state model."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        additions = {
            "score_status": "TEXT NOT NULL DEFAULT 'pending'",
            "translation_status": "TEXT NOT NULL DEFAULT 'pending'",
            "analysis_status": "TEXT NOT NULL DEFAULT 'pending'",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_papers ADD COLUMN {name} {definition}")

        conn.execute(
            "UPDATE daily_papers SET score_status = 'succeeded' "
            "WHERE score_json IS NOT NULL AND score_status = 'pending'"
        )
        conn.execute(
            "UPDATE daily_papers SET translation_status = 'succeeded' "
            "WHERE abstract_cn IS NOT NULL AND trim(abstract_cn) <> '' "
            "AND translation_status = 'pending'"
        )
        conn.execute(
            "UPDATE daily_papers SET analysis_status = 'succeeded' "
            "WHERE analysis_json IS NOT NULL AND analysis_status = 'pending'"
        )

    @staticmethod
    def _migrate_tldr_state(conn):
        """Track TL;DR separately from the complete score stage.

        Older reports can contain a valid score table but no TL;DR.  Treating
        that as a failed score forced an expensive re-score and made it
        impossible for history repair to update just the omitted field.
        """
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "tldr_status" not in columns:
            conn.execute(
                "ALTER TABLE daily_papers "
                "ADD COLUMN tldr_status TEXT NOT NULL DEFAULT 'pending'"
            )

        rows = conn.execute(
            "SELECT source, paper_id, score_json, score_status, tldr_status "
            "FROM daily_papers WHERE score_json IS NOT NULL"
        ).fetchall()
        succeeded: list[tuple[str, str]] = []
        for row in rows:
            if row["score_status"] != "succeeded" or row["tldr_status"] != "pending":
                continue
            try:
                payload = json.loads(row["score_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("tldr"), str) and payload["tldr"].strip():
                succeeded.append((row["source"], row["paper_id"]))
        if succeeded:
            conn.executemany(
                "UPDATE daily_papers SET tldr_status = 'succeeded' "
                "WHERE source = ? AND paper_id = ?",
                succeeded,
            )

    @staticmethod
    def _migrate_report_repair_state(conn):
        """Add a durable retry flag for historical report-file patches."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        additions = {
            "report_repair_status": "TEXT NOT NULL DEFAULT 'not_needed'",
            "report_repair_error": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_papers ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_legacy_report_timestamp(conn):
        """Remember which archived HTML card last supplied a legacy row.

        ``completed_at`` is the original delivery time.  Several v3.2 cards
        can share that value through one JSON history entry, so it cannot tell
        whether a newly discovered report is actually newer.  Keeping the
        report artifact timestamp separately preserves the documented
        newest-report-wins rule without changing delivery semantics.
        """
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "legacy_report_at" not in columns:
            conn.execute("ALTER TABLE daily_papers ADD COLUMN legacy_report_at TEXT")

    @staticmethod
    def _migrate_stage_fingerprints(conn):
        """Add stage input keys used to invalidate stale incomplete LLM work."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        additions = {
            "score_input_fingerprint": "TEXT",
            "translation_input_fingerprint": "TEXT",
            "analysis_input_fingerprint": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE daily_papers ADD COLUMN {name} {definition}")

    @staticmethod
    def _migrate_score_audit_state(conn):
        """Add the non-secret score evidence column to existing databases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "score_audit_json" not in columns:
            conn.execute("ALTER TABLE daily_papers ADD COLUMN score_audit_json TEXT")

    # ─── 跨来源论文实体 ─────────────────────────────────────────────────

    @staticmethod
    def _decode_json_object(value: Any) -> Dict[str, Any]:
        """Decode a persisted JSON object without letting malformed history abort a migration."""
        try:
            payload = json.loads(value) if value else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _decode_json_strings(value: Any) -> list[str]:
        """Decode one persisted JSON string list, accepting malformed legacy rows."""
        try:
            payload = json.loads(value) if value else []
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, str)]

    @staticmethod
    def _normalized_doi(value: Any) -> Optional[str]:
        """Return a stable DOI key, or ``None`` for non-DOI source identifiers.

        DOI equality is one of the two deliberately trusted cross-source
        merge signals.  A title, author list, or fuzzy URL is never used for
        automatic matching: those fields vary enough across indexers to risk
        joining two different papers.
        """
        text = str(value or "").strip()
        if not text:
            return None
        text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE
        )
        text = text.split("?", 1)[0].split("#", 1)[0].strip().rstrip("/.")
        if not re.match(r"^10\.\d{4,9}/\S+$", text, flags=re.IGNORECASE):
            return None
        return text.casefold()

    @staticmethod
    def _arxiv_identity_from_value(value: Any) -> tuple[Optional[str], Optional[int]]:
        """Extract a validated arXiv canonical ID and optional explicit version."""
        text = str(value or "").strip()
        if not text:
            return None, None
        url_match = re.search(
            r"arxiv\.org/(?:abs|pdf)/(?P<identifier>[^/?#]+)",
            text,
            flags=re.IGNORECASE,
        )
        if url_match:
            text = url_match.group("identifier")
        text = re.sub(r"^arxiv:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
        match = re.fullmatch(
            r"(?P<canonical>(?:\d{4}\.\d{4,5}|[A-Za-z][A-Za-z0-9.-]*/\d{7}))"
            r"(?:v(?P<version>[1-9]\d*))?",
            text,
        )
        if match is None:
            return None, None
        return (
            match.group("canonical").casefold(),
            int(match.group("version")) if match.group("version") else None,
        )

    @classmethod
    def _paper_entity_identity_data(cls, row: sqlite3.Row) -> Dict[str, Any]:
        """Build trusted entity aliases for one source-level paper record.

        Each source record always receives a source-local alias.  DOI and an
        *exact* arXiv version are additionally shared aliases.  A versionless
        arXiv link is retained only as a lookup hint and is linked later only
        when it identifies exactly one entity; this prevents a mirror from
        being accidentally attached to an unrelated newer revision.
        """
        source = str(row["source"] or "").strip().casefold()
        paper_id = str(row["paper_id"] or "").strip()
        canonical_id = str(row["canonical_id"] or paper_id).strip()
        try:
            version = max(0, int(row["version"] or 0))
        except (TypeError, ValueError):
            version = 0
        metadata = cls._decode_json_object(row["paper_json"])

        doi_candidates = [
            metadata.get("doi"),
            canonical_id if source != "arxiv" else None,
            paper_id if source != "arxiv" else None,
        ]
        doi = next(
            (normalized for value in doi_candidates if (normalized := cls._normalized_doi(value))),
            None,
        )

        arxiv_candidates: list[Any] = []
        if source == "arxiv":
            arxiv_candidates.extend([paper_id, canonical_id])
        arxiv_candidates.extend(
            [
                metadata.get("arxiv_id"),
                metadata.get("arxiv_url"),
                metadata.get("url"),
            ]
        )
        arxiv_canonical: Optional[str] = None
        arxiv_version: Optional[int] = None
        for value in arxiv_candidates:
            candidate_canonical, candidate_version = cls._arxiv_identity_from_value(value)
            if candidate_canonical is None:
                continue
            arxiv_canonical = candidate_canonical
            arxiv_version = candidate_version
            if source == "arxiv" and version:
                arxiv_version = version
            break

        aliases: list[str] = []
        if doi:
            aliases.append(f"doi:{doi}")
        if arxiv_canonical and arxiv_version:
            aliases.append(f"arxiv:{arxiv_canonical}@v{arxiv_version}")
        local_canonical = canonical_id.casefold() or paper_id.casefold()
        aliases.append(f"source:{source}:{local_canonical}@v{version}")
        # ``dict.fromkeys`` preserves the strongest alias first, which makes
        # a new entity's primary key deterministic and readable in SQLite.
        aliases = list(dict.fromkeys(aliases))
        return {
            "source": source,
            "paper_id": paper_id,
            "canonical_id": canonical_id,
            "version": version,
            "metadata": metadata,
            "doi": doi,
            "arxiv_canonical": arxiv_canonical,
            "arxiv_version": arxiv_version,
            "aliases": aliases,
            "versionless_arxiv": (
                arxiv_canonical if arxiv_canonical and arxiv_version is None else None
            ),
        }

    @staticmethod
    def _dedupe_display_strings(values: list[Any]) -> list[str]:
        """Case-insensitively merge display strings while retaining stable spelling."""
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            text = " ".join(value.split())
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _merge_paper_entities(
        self, conn: sqlite3.Connection, entity_ids: set[str]
    ) -> str:
        """Join entities that a newly observed trusted alias proves identical."""
        entity_ids = {str(item) for item in entity_ids if item}
        if not entity_ids:
            raise ValueError("cannot merge an empty entity set")
        if len(entity_ids) == 1:
            return next(iter(entity_ids))
        placeholders = ", ".join("?" for _ in entity_ids)
        rows = conn.execute(
            "SELECT entity_id, created_at FROM paper_entities WHERE entity_id IN ("
            + placeholders
            + ")",
            sorted(entity_ids),
        ).fetchall()
        if not rows:
            raise RuntimeError("论文实体别名指向不存在的实体")
        target = min(
            rows, key=lambda item: (str(item["created_at"] or ""), item["entity_id"])
        )["entity_id"]
        for row in rows:
            source_entity_id = row["entity_id"]
            if source_entity_id == target:
                continue
            conn.execute(
                "UPDATE paper_entity_aliases SET entity_id = ? WHERE entity_id = ?",
                (target, source_entity_id),
            )
            conn.execute(
                "UPDATE daily_papers SET entity_id = ? WHERE entity_id = ?",
                (target, source_entity_id),
            )
            conn.execute(
                "DELETE FROM paper_entities WHERE entity_id = ?", (source_entity_id,)
            )
        return str(target)

    def _refresh_paper_entity(
        self, conn: sqlite3.Connection, entity_id: str
    ) -> None:
        """Rebuild derived, safely mergeable metadata from all source records."""
        rows = conn.execute(
            """
            SELECT source, paper_id, canonical_id, version, first_seen_at,
                   last_seen_at, completed_at, paper_json, score_json
            FROM daily_papers
            WHERE entity_id = ?
            ORDER BY COALESCE(completed_at, '') DESC, last_seen_at DESC, source, paper_id
            """,
            (entity_id,),
        ).fetchall()
        if not rows:
            return
        existing = conn.execute(
            "SELECT * FROM paper_entities WHERE entity_id = ?", (entity_id,)
        ).fetchone()
        if existing is None:
            return

        titles: list[str] = []
        abstracts: list[str] = []
        authors: list[Any] = []
        categories: list[Any] = []
        keywords: list[Any] = []
        doi_values: list[str] = []
        arxiv_values: list[tuple[str, Optional[int]]] = []
        first_seen_values: list[str] = []
        last_seen_values: list[str] = []
        completed_values: list[str] = []
        for row in rows:
            data = self._paper_entity_identity_data(row)
            metadata = data["metadata"]
            title = metadata.get("title")
            abstract = metadata.get("abstract")
            if isinstance(title, str) and title.strip():
                titles.append(title)
            if isinstance(abstract, str) and abstract.strip():
                abstracts.append(abstract)
            authors.extend(metadata.get("authors") or [])
            categories.extend(metadata.get("categories") or [])
            score = self._decode_json_object(row["score_json"])
            keywords.extend(score.get("extracted_keywords") or [])
            if data["doi"]:
                doi_values.append(data["doi"])
            if data["arxiv_canonical"]:
                arxiv_values.append((data["arxiv_canonical"], data["arxiv_version"]))
            if row["first_seen_at"]:
                first_seen_values.append(str(row["first_seen_at"]))
            if row["last_seen_at"]:
                last_seen_values.append(str(row["last_seen_at"]))
            if row["completed_at"]:
                completed_values.append(str(row["completed_at"]))

        merged_authors = self._dedupe_display_strings(authors)
        merged_categories = self._dedupe_display_strings(categories)
        merged_keywords = self._dedupe_display_strings(keywords)
        existing_authors = self._decode_json_strings(existing["authors_json"])
        existing_categories = self._decode_json_strings(existing["categories_json"])
        existing_keywords = self._decode_json_strings(existing["merged_keywords_json"])
        now = datetime.now().isoformat()
        arxiv_canonical = (
            arxiv_values[0][0] if arxiv_values else existing["arxiv_canonical_id"]
        )
        arxiv_version = (
            arxiv_values[0][1] if arxiv_values else existing["arxiv_version"]
        )
        conn.execute(
            """
            UPDATE paper_entities
            SET arxiv_canonical_id = ?, arxiv_version = ?, doi = ?,
                title = ?, authors_json = ?, abstract = ?, categories_json = ?,
                merged_keywords_json = ?, first_seen_at = ?, last_seen_at = ?,
                completed_at = ?, updated_at = ?
            WHERE entity_id = ?
            """,
            (
                arxiv_canonical,
                arxiv_version,
                doi_values[0] if doi_values else existing["doi"],
                titles[0] if titles else existing["title"],
                json.dumps(merged_authors or existing_authors, ensure_ascii=False),
                abstracts[0] if abstracts else existing["abstract"],
                json.dumps(merged_categories or existing_categories, ensure_ascii=False),
                json.dumps(merged_keywords or existing_keywords, ensure_ascii=False),
                min(first_seen_values) if first_seen_values else existing["first_seen_at"],
                max(last_seen_values) if last_seen_values else existing["last_seen_at"],
                max(completed_values) if completed_values else existing["completed_at"],
                now,
                entity_id,
            ),
        )

    def _sync_paper_entity_for_record(
        self,
        conn: sqlite3.Connection,
        source: str,
        paper_id: str,
        *,
        refresh: bool = True,
    ) -> Optional[str]:
        """Attach one source record to its logical paper entity inside a transaction."""
        row = conn.execute(
            "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
            (source, paper_id),
        ).fetchone()
        if row is None:
            return None
        data = self._paper_entity_identity_data(row)
        entity_ids: set[str] = set()
        existing_entity_id = row["entity_id"] if "entity_id" in row.keys() else None
        if existing_entity_id:
            present = conn.execute(
                "SELECT 1 FROM paper_entities WHERE entity_id = ?", (existing_entity_id,)
            ).fetchone()
            if present is not None:
                entity_ids.add(str(existing_entity_id))
        aliases = data["aliases"]
        if aliases:
            placeholders = ", ".join("?" for _ in aliases)
            alias_rows = conn.execute(
                "SELECT DISTINCT entity_id FROM paper_entity_aliases WHERE alias_key IN ("
                + placeholders
                + ")",
                aliases,
            ).fetchall()
            entity_ids.update(str(item["entity_id"]) for item in alias_rows if item["entity_id"])

        # A source may expose only an arXiv work ID without its version. It is
        # safe to join it only while exactly one known entity has that work ID.
        versionless_arxiv = data["versionless_arxiv"]
        if versionless_arxiv:
            candidates = conn.execute(
                "SELECT DISTINCT entity_id FROM paper_entities WHERE arxiv_canonical_id = ?",
                (versionless_arxiv,),
            ).fetchall()
            candidate_ids = {str(item["entity_id"]) for item in candidates}
            if len(candidate_ids) == 1:
                entity_ids.update(candidate_ids)
        elif data["arxiv_canonical"] and data["arxiv_version"]:
            # The reverse order is common in live scans: a curated source can
            # first expose a versionless arXiv work, then the canonical API
            # supplies v1.  Joining exactly one versionless entity is safe;
            # later v2/v3 records see the now-versioned entity and remain
            # distinct unless a DOI explicitly links them.
            candidates = conn.execute(
                """
                SELECT DISTINCT entity_id FROM paper_entities
                WHERE arxiv_canonical_id = ? AND arxiv_version IS NULL
                """,
                (data["arxiv_canonical"],),
            ).fetchall()
            candidate_ids = {str(item["entity_id"]) for item in candidates}
            if len(candidate_ids) == 1:
                entity_ids.update(candidate_ids)

        if entity_ids:
            entity_id = self._merge_paper_entities(conn, entity_ids)
        else:
            entity_id = uuid.uuid4().hex
            now = datetime.now().isoformat()
            conn.execute(
                """
                INSERT INTO paper_entities(
                    entity_id, primary_key, arxiv_canonical_id, arxiv_version,
                    doi, title, authors_json, abstract, categories_json,
                    merged_keywords_json, first_seen_at, last_seen_at,
                    completed_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, '[]', NULL, '[]', '[]', ?, ?, NULL, ?, ?)
                """,
                (
                    entity_id,
                    aliases[0],
                    data["arxiv_canonical"],
                    data["arxiv_version"],
                    data["doi"],
                    row["first_seen_at"] or now,
                    row["last_seen_at"] or now,
                    now,
                    now,
                ),
            )

        for alias in aliases:
            alias_row = conn.execute(
                "SELECT entity_id FROM paper_entity_aliases WHERE alias_key = ?", (alias,)
            ).fetchone()
            if alias_row is not None and alias_row["entity_id"] != entity_id:
                # The exact alias itself is proof of identity, including a
                # race between two workers that registered the same paper.
                entity_id = self._merge_paper_entities(
                    conn, {entity_id, str(alias_row["entity_id"])}
                )
            conn.execute(
                """
                INSERT INTO paper_entity_aliases(alias_key, entity_id, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET entity_id = excluded.entity_id
                """,
                (alias, entity_id, datetime.now().isoformat()),
            )
        conn.execute(
            "UPDATE daily_papers SET entity_id = ? WHERE source = ? AND paper_id = ?",
            (entity_id, source, paper_id),
        )
        if refresh:
            self._refresh_paper_entity(conn, entity_id)
        return entity_id

    def _migrate_paper_entities(self, conn: sqlite3.Connection) -> None:
        """Create and lazily backfill the logical-paper layer for upgraded databases."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(daily_papers)").fetchall()
        }
        if "entity_id" not in columns:
            conn.execute("ALTER TABLE daily_papers ADD COLUMN entity_id TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_entities (
                entity_id TEXT PRIMARY KEY,
                primary_key TEXT NOT NULL UNIQUE,
                arxiv_canonical_id TEXT,
                arxiv_version INTEGER,
                doi TEXT,
                title TEXT,
                authors_json TEXT NOT NULL DEFAULT '[]',
                abstract TEXT,
                categories_json TEXT NOT NULL DEFAULT '[]',
                merged_keywords_json TEXT NOT NULL DEFAULT '[]',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_entity_aliases (
                alias_key TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_daily_papers_entity ON daily_papers(entity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_entities_arxiv ON paper_entities(arxiv_canonical_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_entities_doi ON paper_entities(doi)"
        )
        # The archive's normal first view is the most recently completed
        # logical papers.  Keep its filter and ordering covered together so
        # the WebUI does not sort the entire historical entity table before
        # showing its first page.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_entities_completed "
            "ON paper_entities(completed_at DESC, last_seen_at DESC, entity_id DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_entity_aliases_entity "
            "ON paper_entity_aliases(entity_id)"
        )

        missing_rows = conn.execute(
            """
            SELECT source, paper_id
            FROM daily_papers
            WHERE entity_id IS NULL
               OR NOT EXISTS (
                   SELECT 1 FROM paper_entities entities
                   WHERE entities.entity_id = daily_papers.entity_id
               )
            ORDER BY first_seen_at, source, paper_id
            """
        ).fetchall()
        entity_ids: set[str] = set()
        for row in missing_rows:
            entity_id = self._sync_paper_entity_for_record(
                conn, row["source"], row["paper_id"], refresh=False
            )
            if entity_id:
                entity_ids.add(entity_id)
        for entity_id in entity_ids:
            self._refresh_paper_entity(conn, entity_id)

    def _ensure_paper_entity_coverage(self) -> None:
        """Backfill rows inserted by older tools or direct compatibility callers."""
        with self._lock, self._connect() as conn:
            missing = conn.execute(
                """
                SELECT 1 FROM daily_papers
                WHERE entity_id IS NULL
                   OR NOT EXISTS (
                       SELECT 1 FROM paper_entities entities
                       WHERE entities.entity_id = daily_papers.entity_id
                   )
                LIMIT 1
                """
            ).fetchone()
            if missing is not None:
                self._migrate_paper_entities(conn)

    @staticmethod
    def _migrate_delivery_identity(conn):
        """Backfill identity fields for delivery ledgers created by older releases."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_deliveries)").fetchall()
        }
        additions = {
            "canonical_id": "TEXT NOT NULL DEFAULT ''",
            "version": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE paper_deliveries ADD COLUMN {name} {definition}")

        paper_identity = DailyResearchStore._paper_identity_or_none()
        if paper_identity is None:
            return

        rows = conn.execute(
            "SELECT delivery_id, source, paper_id, canonical_id, version FROM paper_deliveries"
        ).fetchall()
        updates = []
        for row in rows:
            canonical_id, desired_version = DailyResearchStore._migration_identity(
                row["source"],
                row["paper_id"],
                row["canonical_id"],
                row["version"],
                paper_identity,
            )
            if row["canonical_id"] != canonical_id or row["version"] != desired_version:
                updates.append((canonical_id, desired_version, row["delivery_id"]))

        if updates:
            # Existing releases may already have the exact-version unique
            # index.  Identity backfill can intentionally merge old aliases
            # (for example DOI URL vs bare DOI), so update under no exact
            # constraint, then let the next migration step keep one ledger
            # row and recreate the index atomically in this transaction.
            conn.execute("DROP INDEX IF EXISTS idx_paper_deliveries_exact_version")
            conn.executemany(
                "UPDATE paper_deliveries SET canonical_id = ?, version = ? WHERE delivery_id = ?",
                updates,
            )

    @staticmethod
    def _migrate_delivery_report_timestamp(conn):
        """Add the source-report timestamp used by legacy path replacement."""
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(paper_deliveries)").fetchall()
        }
        if "report_at" not in columns:
            conn.execute("ALTER TABLE paper_deliveries ADD COLUMN report_at TEXT")

    @staticmethod
    def _migrate_delivery_exact_version_constraint(conn):
        """Enforce one completed delivery for each exact source/version.

        Old databases used a run-scoped unique constraint, which permitted an
        accidental second report for the same exact paper if a future caller
        bypassed the normal pre-filter.  A unique index is compatible with the
        existing table and is safer than a table rebuild.  In the unlikely
        event an old DB already contains duplicates, retain its earliest
        delivery as the authoritative one before creating the index.
        """
        duplicates = conn.execute(
            """
            SELECT source, canonical_id, version, MIN(delivery_id) AS keep_delivery_id
            FROM paper_deliveries
            GROUP BY source, canonical_id, version
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicates:
            conn.execute(
                "DELETE FROM paper_deliveries "
                "WHERE source = ? AND canonical_id = ? AND version = ? AND delivery_id != ?",
                (
                    row["source"],
                    row["canonical_id"],
                    row["version"],
                    row["keep_delivery_id"],
                ),
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_deliveries_exact_version "
            "ON paper_deliveries(source, canonical_id, version)"
        )

    def start_run(self, total_papers: int, run_kind: str = "daily") -> str:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        now = datetime.now().isoformat()
        normalized_kind = str(run_kind or "daily").strip() or "daily"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_runs(run_id, started_at, status, total_papers, run_kind)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, now, "running", total_papers, normalized_kind),
            )
        return run_id

    @staticmethod
    def _parse_checkpoint_timestamp(value: Optional[str]) -> Optional[datetime]:
        """Parse a stored local/UTC ISO timestamp into a UTC-aware value.

        Existing databases use ``datetime.now().isoformat()`` (without an
        offset), while future callers may persist offset-aware timestamps.
        Treat legacy naive values as local time, matching their original
        meaning, then compare everything in UTC.
        """
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed.astimezone(timezone.utc)

    def prepare_scan(
        self,
        run_id: str,
        configured_days: int,
        sources: list[str],
        now: Optional[datetime] = None,
    ) -> int:
        """Record a scan plan and return a recovery-safe lookback length.

        The fixed daily scan window remains the normal overlap window.  When an enabled
        source has not completed a successful scan recently (because a run
        failed or the scheduler was offline), extend the window back to that
        source's last successful scan start plus a one-day overlap.  The
        SQLite delivery ledger filters already delivered exact versions, so
        this never turns an expanded recovery scan into duplicate reports.

        A new source has no checkpoint by definition; its configured window is
        used rather than silently attempting an unbounded historical import.
        Call this immediately before fetching, rather than at process start,
        so the checkpoint represents the actual source-query boundary.
        """
        base_days = max(1, int(configured_days))
        normalized_sources = sorted(
            {str(source).strip().lower() for source in sources if str(source).strip()}
        )
        now_dt = now or datetime.now().astimezone()
        if now_dt.tzinfo is None:
            now_dt = now_dt.astimezone()
        now_utc = now_dt.astimezone(timezone.utc)
        scan_started_at = now_dt.isoformat()

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id, status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能为 running 运行准备扫描计划: {run_id} ({run['status']})"
                )

            recovery_days = base_days
            if normalized_sources:
                placeholders = ", ".join("?" for _ in normalized_sources)
                rows = conn.execute(
                    "SELECT source, successful_scan_started_at "
                    "FROM daily_scan_watermarks WHERE source IN ("
                    + placeholders
                    + ")",
                    normalized_sources,
                ).fetchall()
                checkpoints = {}
                for row in rows:
                    raw_checkpoint = row["successful_scan_started_at"]
                    checkpoint = self._parse_checkpoint_timestamp(raw_checkpoint)
                    if checkpoint is None:
                        raise RuntimeError(
                            "扫描水位线损坏，已停止本次运行以避免漏抓: "
                            f"{row['source']}: {raw_checkpoint!r}"
                        )
                    checkpoints[row["source"]] = checkpoint
                for source in normalized_sources:
                    checkpoint = checkpoints.get(source)
                    if checkpoint is None:
                        continue
                    elapsed_seconds = (now_utc - checkpoint).total_seconds()
                    elapsed_days = max(0, int(elapsed_seconds / 86400))
                    recovery_days = max(
                        recovery_days,
                        elapsed_days + self.SCAN_RECOVERY_OVERLAP_DAYS,
                    )

            conn.execute(
                """
                UPDATE daily_runs
                SET scan_started_at = ?, scan_days = ?, scanned_sources_json = ?
                WHERE run_id = ?
                """,
                (
                    scan_started_at,
                    recovery_days,
                    json.dumps(normalized_sources, ensure_ascii=False),
                    run_id,
                ),
            )
        return recovery_days

    @staticmethod
    def _scan_sources_from_run(conn, run_id: str) -> list[str]:
        row = conn.execute(
            "SELECT scanned_sources_json, scan_days FROM daily_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return []
        if row["scanned_sources_json"] is None:
            if row["scan_days"] is not None:
                raise RuntimeError(f"日报运行扫描计划缺失: {run_id}")
            return []
        raw_sources = row["scanned_sources_json"]
        try:
            sources = json.loads(raw_sources)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"日报运行扫描计划损坏: {run_id}") from exc
        if not isinstance(sources, list) or any(
            not isinstance(source, str) or not source.strip() for source in sources
        ):
            raise RuntimeError(f"日报运行扫描计划格式无效: {run_id}")
        normalized = [source.strip().lower() for source in sources]
        if len(set(normalized)) != len(normalized):
            raise RuntimeError(f"日报运行扫描计划包含重复数据源: {run_id}")
        return sorted(normalized)

    @staticmethod
    def _scan_started_at_from_run(conn, run_id: str, fallback: str) -> str:
        row = conn.execute(
            "SELECT scan_started_at, started_at FROM daily_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return fallback
        value = row["scan_started_at"] or row["started_at"] or fallback
        if DailyResearchStore._parse_checkpoint_timestamp(value) is None:
            raise RuntimeError(f"日报运行扫描开始时间损坏: {run_id}")
        return value

    @staticmethod
    def _require_successful_scan_receipts(conn, run_id: str, sources: list[str]) -> None:
        """Refuse a checkpoint when any planned source lacks terminal success proof.

        A source fetch can be fail-closed and still leave an observability
        hole if a later refactor accidentally omits its receipt callback.
        Checkpoint advancement is the last irreversible-looking transition in
        a run, so require one ``succeeded`` receipt for every source that was
        recorded in ``prepare_scan``.  The caller's transaction rolls back all
        delivery/completion writes if this invariant is not satisfied.
        """
        if not sources:
            return
        placeholders = ", ".join("?" for _ in sources)
        rows = conn.execute(
            "SELECT source, status FROM daily_scan_receipts WHERE run_id = ? "
            f"AND source IN ({placeholders})",
            [run_id, *sources],
        ).fetchall()
        statuses = {row["source"]: row["status"] for row in rows}
        missing = [source for source in sources if source not in statuses]
        unsuccessful = [
            source for source in sources if source in statuses and statuses[source] != "succeeded"
        ]
        if missing or unsuccessful:
            details = []
            if missing:
                details.append(f"缺少收据: {', '.join(missing)}")
            if unsuccessful:
                details.append(f"未成功: {', '.join(unsuccessful)}")
            raise RuntimeError(
                "扫描收据不完整，拒绝推进来源水位线: " + "; ".join(details)
            )

    def _advance_scan_watermarks(self, conn, run_id: str, now: str) -> None:
        """Advance all planned source checkpoints inside a successful commit."""
        sources = self._scan_sources_from_run(conn, run_id)
        if not sources:
            return
        self._require_successful_scan_receipts(conn, run_id, sources)
        scan_started_at = self._scan_started_at_from_run(conn, run_id, now)
        for source in sources:
            conn.execute(
                """
                INSERT INTO daily_scan_watermarks(
                    source, successful_scan_started_at, run_id, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    successful_scan_started_at = excluded.successful_scan_started_at,
                    run_id = excluded.run_id,
                    updated_at = excluded.updated_at
                """,
                (source, scan_started_at, run_id, now),
            )

    def get_scan_watermark(self, source: str) -> Optional[sqlite3.Row]:
        """Return one source scan checkpoint for diagnostics and tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM daily_scan_watermarks WHERE source = ?", (source,)
            ).fetchone()

    @staticmethod
    def _receipt_candidate_count(receipt: Dict[str, Any]) -> Optional[int]:
        """Read a non-negative candidate count from a terminal source receipt."""
        value = receipt.get("total_new_candidates")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        domain_receipts = receipt.get("domain_receipts")
        if not isinstance(domain_receipts, list):
            return None
        total = 0
        found = False
        for item in domain_receipts:
            if not isinstance(item, dict):
                continue
            count = item.get("new_candidates")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                total += count
                found = True
        return total if found else None

    @staticmethod
    def _sanitize_source_health_error(value: object) -> Optional[str]:
        """Keep persisted source failures useful without turning them into logs."""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            from utils.webui_trigger import sanitize_task_error_summary

            cleaned = sanitize_task_error_summary(text, max_chars=360)
        except Exception:
            cleaned = re.sub(r"\s+", " ", text)[:360]
        return cleaned or None

    @staticmethod
    def _normalized_source_health_value(source: object) -> str:
        """Return a bounded source key without rejecting future source plugins."""
        value = str(source or "").strip().lower()
        return value[:80] if value else "unknown"

    @staticmethod
    def _normalized_task_kind_value(task_kind: object) -> str:
        value = str(task_kind or "").strip().lower()
        return value[:80] if value else "unknown"

    @staticmethod
    def _normalized_source_health_count(value: object) -> Optional[int]:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    def _upsert_source_health_event(
        self,
        conn: sqlite3.Connection,
        *,
        source: object,
        success: bool,
        run_id: Optional[str] = None,
        task_kind: object = None,
        candidate_count: object = None,
        error_summary: object = None,
        occurred_at: Optional[str] = None,
        origin_key: Optional[str] = None,
    ) -> None:
        """Write one logical source request using an optional stable origin key."""
        source_key = self._normalized_source_health_value(source)
        task_key = self._normalized_task_kind_value(task_kind)
        status = "succeeded" if success else "failed"
        count = self._normalized_source_health_count(candidate_count)
        error = self._sanitize_source_health_error(error_summary)
        timestamp = str(occurred_at or "").strip() or datetime.now().isoformat()
        normalized_run_id = str(run_id).strip() if run_id else None
        normalized_origin = str(origin_key).strip()[:240] if origin_key else None
        conn.execute(
            """
            INSERT INTO source_health_events(
                run_id, task_kind, source, status, candidate_count,
                error_summary, occurred_at, origin_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(origin_key) DO UPDATE SET
                run_id = excluded.run_id,
                task_kind = excluded.task_kind,
                source = excluded.source,
                status = excluded.status,
                candidate_count = excluded.candidate_count,
                error_summary = excluded.error_summary,
                occurred_at = excluded.occurred_at
            """,
            (
                normalized_run_id,
                task_key,
                source_key,
                status,
                count,
                error,
                timestamp,
                normalized_origin,
            ),
        )

    def _backfill_source_health_events(self, conn: sqlite3.Connection) -> None:
        """Seed cross-workflow health history from pre-existing scan receipts."""
        try:
            rows = conn.execute(
                """
                SELECT receipts.run_id, receipts.source, receipts.status,
                       receipts.receipt_json, receipts.recorded_at,
                       COALESCE(runs.run_kind, 'daily') AS task_kind
                FROM daily_scan_receipts AS receipts
                LEFT JOIN daily_runs AS runs ON runs.run_id = receipts.run_id
                LEFT JOIN source_health_events AS health
                  ON health.origin_key =
                     ('scan-receipt:' || receipts.run_id || ':' || receipts.source)
                WHERE health.event_id IS NULL
                ORDER BY receipts.receipt_id ASC
                """
            ).fetchall()
        except sqlite3.Error:
            # A database midway through an old upgrade can still use all
            # existing workflows; a later open will backfill the receipts.
            return

        for row in rows:
            status = row["status"]
            if status not in {"succeeded", "failed"}:
                continue
            try:
                receipt = json.loads(row["receipt_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                receipt = {}
            if not isinstance(receipt, dict):
                receipt = {}
            self._upsert_source_health_event(
                conn,
                source=row["source"],
                success=status == "succeeded",
                run_id=row["run_id"],
                task_kind=row["task_kind"],
                candidate_count=self._receipt_candidate_count(receipt),
                error_summary=(
                    self._extract_receipt_error(receipt)
                    if status == "failed"
                    else None
                ),
                occurred_at=row["recorded_at"],
                origin_key=f"scan-receipt:{row['run_id']}:{row['source']}",
            )

    def record_source_health_event(
        self,
        source: str,
        success: bool,
        *,
        run_id: Optional[str] = None,
        task_kind: str = "unknown",
        candidate_count: Optional[int] = None,
        error_summary: object = None,
        origin_key: Optional[str] = None,
    ) -> None:
        """Persist one terminal logical data-source request from any workflow.

        This observability write is deliberately independent of delivery and
        checkpoint transactions: a health-panel failure must never cause a
        report or retry queue to fail.
        """
        with self._lock, self._connect() as conn:
            self._upsert_source_health_event(
                conn,
                source=source,
                success=bool(success),
                run_id=run_id,
                task_kind=task_kind,
                candidate_count=candidate_count,
                error_summary=error_summary,
                origin_key=origin_key,
            )

    @staticmethod
    def _validate_scan_receipt(run_id: str, source: str, receipt: Dict[str, Any]) -> Dict[str, Any]:
        """Validate the small public receipt schema before persisting it.

        The source owns detailed fields, but the store must reject a receipt
        that is accidentally attached to the wrong run/source or lacks a
        terminal status.  This keeps the audit trail useful even after future
        source implementations are added.
        """
        if not isinstance(receipt, dict):
            raise ValueError("扫描收据必须是 JSON 对象")
        normalized_source = str(source or "").strip().lower()
        if not normalized_source:
            raise ValueError("扫描收据缺少数据源")
        receipt_source = str(receipt.get("source") or "").strip().lower()
        if receipt_source != normalized_source:
            raise ValueError(
                f"扫描收据来源不匹配: expected {normalized_source}, got {receipt_source or '<empty>'}"
            )
        status = receipt.get("status")
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"扫描收据状态无效: {status!r}")
        if not isinstance(receipt.get("scanned_at"), str) or not receipt["scanned_at"].strip():
            raise ValueError("扫描收据缺少 scanned_at")
        if not isinstance(receipt.get("domain_receipts", []), list):
            raise ValueError("扫描收据 domain_receipts 必须是列表")
        # A JSON round trip both ensures serialisability and detaches callers'
        # mutable dicts from the durable record.
        try:
            encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
            decoded = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"扫描收据不可 JSON 序列化: {exc}") from exc
        return decoded

    def record_scan_receipt(self, run_id: str, source: str, receipt: Dict[str, Any]) -> None:
        """Persist one source receipt for a daily run, replacing retry details.

        A source may retry a domain internally.  Its callback is invoked only
        after the scan reaches a terminal source-level result, so one
        ``(run_id, source)`` row represents the final evidence for that run.
        Failed receipts intentionally remain durable; ``fail_run`` does not
        erase them.
        """
        normalized_source = str(source or "").strip().lower()
        payload = self._validate_scan_receipt(run_id, normalized_source, receipt)
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id, run_kind FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            conn.execute(
                """
                INSERT INTO daily_scan_receipts(
                    run_id, source, status, receipt_json, recorded_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source) DO UPDATE SET
                    status = excluded.status,
                    receipt_json = excluded.receipt_json,
                    recorded_at = excluded.recorded_at
                """,
                (
                    run_id,
                    normalized_source,
                    payload["status"],
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            # Preserve one health observation for every final daily/backfill
            # source request without changing the strict receipt/checkpoint
            # semantics above. Repeated callbacks for the same logical scan
            # update their stable event instead of inflating success rates.
            self._upsert_source_health_event(
                conn,
                source=normalized_source,
                success=payload["status"] == "succeeded",
                run_id=run_id,
                task_kind=run["run_kind"],
                candidate_count=self._receipt_candidate_count(payload),
                error_summary=(
                    self._extract_receipt_error(payload)
                    if payload["status"] == "failed"
                    else None
                ),
                occurred_at=now,
                origin_key=f"scan-receipt:{run_id}:{normalized_source}",
            )

    def get_scan_receipts(self, run_id: str) -> list[Dict[str, Any]]:
        """Return parsed source receipts in stable source order for diagnostics/UI."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, status, receipt_json, recorded_at
                FROM daily_scan_receipts
                WHERE run_id = ?
                ORDER BY source ASC
                """,
                (run_id,),
            ).fetchall()
        receipts = []
        for row in rows:
            try:
                receipt = json.loads(row["receipt_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                # A corrupt legacy row must remain visible rather than silently
                # disappearing from a diagnostic screen.
                receipt = {"source": row["source"], "status": "corrupt"}
            if not isinstance(receipt, dict):
                receipt = {"source": row["source"], "status": "corrupt"}
            receipt["source"] = row["source"]
            receipt["status"] = row["status"]
            receipt["recorded_at"] = row["recorded_at"]
            receipts.append(receipt)
        return receipts

    def get_app_state(self, key: str) -> Optional[str]:
        """Return a persisted scratch value, or None when the key is unset."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_app_state(self, key: str, value: str) -> None:
        """Persist a scratch value; existing values are overwritten, never dropped."""
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def record_run_phase(
        self,
        run_id: str,
        phase: str,
        *,
        detail: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
    ) -> None:
        """写入当前活跃 run 的阶段心跳，供 WebUI 的长任务进度反馈读取。

        只在 run 仍处于 running 状态时写入：交付提交后的收尾步骤
        （通知派发等）不再产生新心跳，避免终态 run 留下陈旧阶段。
        心跳在 run 完成/失败时清理（见 ``_clear_run_phase``）；一个陈旧的
        心跳（如进程被 SIGKILL）只会让进度视图回退到状态推断，不会误报。

        ``detail/current/total`` 是可选的、面向操作者的长任务进度。它们
        保存在既有 ``app_state`` 心跳中，不改变任何论文或交付账本语义；
        因而旧数据库无需迁移也能显示旧历史导入、时间段扫描等非论文阶段。
        """
        payload_data: Dict[str, Any] = {
            "run_id": run_id,
            "phase": str(phase or ""),
            "updated_at": datetime.now().isoformat(),
        }
        if isinstance(detail, str) and detail.strip():
            # Keep the small shared state bounded even if an upstream service
            # returned a verbose error or an unusually long file name.
            payload_data["detail"] = detail.strip()[:500]
        for key, value in (("current", current), ("total", total)):
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                payload_data[key] = value
        payload = json.dumps(payload_data, ensure_ascii=False)
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "running":
                return
            conn.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (_RUN_PHASE_STATE_KEY, payload, now),
            )

    def _run_phase_payload(self) -> Optional[Dict[str, Any]]:
        raw = self.get_app_state(_RUN_PHASE_STATE_KEY)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _clear_run_phase(conn: sqlite3.Connection, run_id: str) -> None:
        """run 终态时删除阶段心跳；只清理属于该 run 的心跳，避免误删新 run。"""
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?", (_RUN_PHASE_STATE_KEY,)
        ).fetchone()
        if row is None:
            return
        try:
            payload = json.loads(row["value"])
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("run_id") != run_id:
            return
        conn.execute("DELETE FROM app_state WHERE key = ?", (_RUN_PHASE_STATE_KEY,))

    def active_run_progress(self) -> Optional[Dict[str, Any]]:
        """汇总当前活跃 run 的阶段与论文处理进度（无活跃 run 时返回 None）。

        阶段优先取 ``record_run_phase`` 写入的心跳；心跳缺失或属于别的
        run 时（例如升级期间的旧进程），按论文阶段状态推断兜底。
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            run = conn.execute(
                """
                SELECT run_id, started_at, scan_days, run_kind
                FROM daily_runs
                WHERE completed_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            if run is None:
                return None
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS registered,
                    SUM(CASE WHEN score_status = 'succeeded' THEN 1 ELSE 0 END) AS scored,
                    SUM(CASE WHEN analysis_status = 'succeeded' THEN 1 ELSE 0 END) AS analyzed,
                    SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                    SUM(CASE WHEN score_status = 'failed'
                              OR tldr_status = 'failed'
                              OR translation_status = 'failed'
                              OR analysis_status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN score_status IN ('pending', 'running')
                              OR tldr_status IN ('pending', 'running') THEN 1 ELSE 0 END) AS awaiting_score,
                    SUM(CASE WHEN analysis_status IN ('pending', 'running')
                              AND score_status = 'succeeded' THEN 1 ELSE 0 END) AS awaiting_analysis
                FROM daily_papers
                WHERE run_id = ?
                """,
                (run["run_id"],),
            ).fetchone()

        registered = int(counts["registered"] or 0)
        heartbeat = self._run_phase_payload()
        phase = None
        detail = None
        current = None
        total = None
        if heartbeat and heartbeat.get("run_id") == run["run_id"]:
            phase = heartbeat.get("phase")
            raw_detail = heartbeat.get("detail")
            if isinstance(raw_detail, str) and raw_detail.strip():
                detail = raw_detail.strip()
            raw_current = heartbeat.get("current")
            raw_total = heartbeat.get("total")
            if isinstance(raw_current, int) and not isinstance(raw_current, bool):
                current = raw_current
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                total = raw_total
        if not isinstance(phase, str) or not phase:
            # 兜底推断：与主流程的阶段顺序一致。
            if registered == 0 or int(counts["awaiting_score"] or 0) > 0:
                phase = "score" if registered > 0 else "scan"
            elif int(counts["awaiting_analysis"] or 0) > 0:
                phase = "analyze"
            else:
                phase = "report"

        return {
            "run_id": run["run_id"],
            "run_kind": run["run_kind"],
            "started_at": run["started_at"],
            "scan_days": run["scan_days"],
            "phase": phase,
            "detail": detail,
            "current": current,
            "total": total,
            "registered": registered,
            "scored": int(counts["scored"] or 0),
            "analyzed": int(counts["analyzed"] or 0),
            "completed": int(counts["completed"] or 0),
            "failed": int(counts["failed"] or 0),
            "awaiting_score": int(counts["awaiting_score"] or 0),
            "awaiting_analysis": int(counts["awaiting_analysis"] or 0),
        }

    def record_token_usage(
        self,
        run_id: str,
        by_model: Dict[str, Dict[str, int]],
        *,
        mode: str = "daily_research",
        recorded_at: datetime | str | None = None,
    ) -> None:
        """Persist one run's per-model token usage.

        ``by_model`` follows TokenCounter.get_summary(): ``{model: {"prompt":
        int, "completion": int, "total": int}}``.  Recording again for the
        same run replaces its rows, keeping interrupted-then-retried runs
        from double counting.  ``recorded_at`` is normally omitted for a live
        run; historical report imports pass the report generation timestamp so
        charts remain on the original reporting day.
        """
        timestamp = self._token_usage_timestamp(recorded_at)
        rows = self._token_usage_rows(by_model, mode, timestamp)
        with self._lock, self._connect() as conn:
            self._replace_token_usage_rows(conn, run_id, rows)

    @staticmethod
    def _token_usage_timestamp(recorded_at: datetime | str | None) -> str:
        """Normalise a local report/run timestamp for SQLite range queries."""
        if recorded_at is None:
            return datetime.now().isoformat()
        if isinstance(recorded_at, datetime):
            return recorded_at.replace(tzinfo=None).isoformat()
        if isinstance(recorded_at, str):
            text = recorded_at.strip()
            if text:
                try:
                    return datetime.fromisoformat(text).replace(tzinfo=None).isoformat()
                except ValueError as exc:
                    raise ValueError("invalid token usage timestamp") from exc
        raise ValueError("invalid token usage timestamp")

    @staticmethod
    def _token_usage_rows(
        by_model: Dict[str, Dict[str, int]], mode: str, recorded_at: str
    ) -> list[tuple[str, int, int, int, str, str]]:
        """Convert one token summary into stable, comparable database rows."""
        rows: list[tuple[str, int, int, int, str, str]] = []
        for model, usage in (by_model or {}).items():
            if not isinstance(usage, dict):
                continue
            prompt = max(0, int(usage.get("prompt", 0) or 0))
            completion = max(0, int(usage.get("completion", 0) or 0))
            rows.append(
                (
                    str(model or "unknown").strip() or "unknown",
                    prompt,
                    completion,
                    prompt + completion,
                    str(mode or "daily_research"),
                    recorded_at,
                )
            )
        return sorted(rows)

    @staticmethod
    def _replace_token_usage_rows(
        conn: sqlite3.Connection,
        run_id: str,
        rows: list[tuple[str, int, int, int, str, str]],
    ) -> None:
        conn.execute("DELETE FROM run_token_usage WHERE run_id = ?", (run_id,))
        conn.executemany(
            """
            INSERT INTO run_token_usage (
                run_id, mode, model, prompt_tokens,
                completion_tokens, total_tokens, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (run_id, mode, model, prompt, completion, total, recorded_at)
                for model, prompt, completion, total, mode, recorded_at in rows
            ],
        )

    def upsert_historical_token_usage(
        self,
        run_id: str,
        by_model: Dict[str, Dict[str, int]],
        *,
        mode: str,
        recorded_at: datetime | str,
    ) -> str:
        """Insert or update one stable historical-report token record.

        The caller supplies a deterministic synthetic ``run_id`` for a report
        batch.  Repeating an import therefore never adds another run; an
        edited archive report simply replaces the previous imported values.
        Returns ``imported``, ``updated`` or ``unchanged``.
        """
        timestamp = self._token_usage_timestamp(recorded_at)
        rows = self._token_usage_rows(by_model, mode, timestamp)
        if not rows:
            raise ValueError("historical token usage requires at least one model row")
        with self._lock, self._connect() as conn:
            current = conn.execute(
                """
                SELECT model, prompt_tokens, completion_tokens, total_tokens, mode, recorded_at
                FROM run_token_usage WHERE run_id = ?
                ORDER BY model, prompt_tokens, completion_tokens, total_tokens, mode, recorded_at
                """,
                (run_id,),
            ).fetchall()
            current_rows = [
                (
                    str(row["model"]),
                    int(row["prompt_tokens"] or 0),
                    int(row["completion_tokens"] or 0),
                    int(row["total_tokens"] or 0),
                    str(row["mode"]),
                    str(row["recorded_at"]),
                )
                for row in current
            ]
            if current_rows == rows:
                return "unchanged"
            self._replace_token_usage_rows(conn, run_id, rows)
        return "updated" if current_rows else "imported"

    def has_token_usage_for_run(self, run_id: str) -> bool:
        """Return whether a normal runtime already recorded this run's usage."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM run_token_usage WHERE run_id = ? LIMIT 1", (run_id,)
            ).fetchone()
        return row is not None

    # ─── 旧版本（v3.2）历史导入与补充运行积压 ─────────────────────────────

    def import_legacy_paper(self, payload: Dict[str, Any], *, delivered: bool) -> str:
        """Upsert one paper reconstructed from a legacy HTML report card.

        ``payload`` keys: source/paper_id/canonical_id/version/paper_json(dict)/
        score_json(str)/abstract_cn/analysis_json(str)/score_status/tldr_status/
        translation_status/analysis_status/completed_at/report_path/report_at/
        delivered_at.

        Newest-wins: an existing row is overwritten only when the incoming
        card timestamp is newer.  Rows produced by v4 runs carry stage
        fingerprints and are never downgraded by legacy data; the delivery
        ledger is still backfilled because it is identity-only.  Returns one
        of ``imported`` / ``skipped_existing_newer`` / ``skipped_v4_rows``.
        """
        now = datetime.now().isoformat()
        source = str(payload["source"]).strip().lower()
        paper_id = str(payload["paper_id"])
        canonical_id = str(payload.get("canonical_id") or paper_id)
        version = int(payload.get("version") or 0)
        completed_at = payload.get("completed_at") or now
        report_at = str(payload.get("report_at") or completed_at).strip() or completed_at
        paper_json = payload.get("paper_json")
        if not isinstance(paper_json, str):
            paper_json = json.dumps(paper_json or {}, ensure_ascii=False)

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                SELECT completed_at, legacy_report_at, score_input_fingerprint,
                       translation_input_fingerprint, analysis_input_fingerprint
                FROM daily_papers WHERE source = ? AND paper_id = ?
                """,
                (source, paper_id),
            ).fetchone()
            outcome = "imported"
            v4_managed = existing is not None and any(
                existing[column] for column in (
                    "score_input_fingerprint",
                    "translation_input_fingerprint",
                    "analysis_input_fingerprint",
                )
            )
            if existing is not None and v4_managed:
                outcome = "skipped_v4_rows"
            elif (
                existing is not None
                and existing["legacy_report_at"]
                and str(existing["legacy_report_at"]) >= report_at
            ):
                outcome = "skipped_existing_newer"
            else:
                conn.execute(
                    """
                    INSERT INTO daily_papers (
                        source, paper_id, canonical_id, version,
                        first_seen_at, last_seen_at, run_id, paper_json,
                        score_json, abstract_cn, analysis_json,
                        scored_at, translated_at, analyzed_at,
                        score_status, tldr_status, translation_status, analysis_status,
                        retry_count, completed_at, legacy_report_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    ON CONFLICT(source, paper_id) DO UPDATE SET
                        canonical_id = excluded.canonical_id,
                        version = excluded.version,
                        last_seen_at = excluded.last_seen_at,
                        paper_json = excluded.paper_json,
                        score_json = excluded.score_json,
                        abstract_cn = excluded.abstract_cn,
                        analysis_json = excluded.analysis_json,
                        scored_at = excluded.scored_at,
                        translated_at = excluded.translated_at,
                        analyzed_at = excluded.analyzed_at,
                        score_status = excluded.score_status,
                        tldr_status = excluded.tldr_status,
                        translation_status = excluded.translation_status,
                        analysis_status = excluded.analysis_status,
                        last_error = NULL,
                        completed_at = COALESCE(excluded.completed_at, daily_papers.completed_at),
                        legacy_report_at = excluded.legacy_report_at
                    """,
                    (
                        source,
                        paper_id,
                        canonical_id,
                        version,
                        report_at,
                        report_at,
                        paper_json,
                        payload.get("score_json"),
                        payload.get("abstract_cn"),
                        payload.get("analysis_json"),
                        completed_at if payload.get("score_json") else None,
                        completed_at if payload.get("abstract_cn") else None,
                        completed_at if payload.get("analysis_json") else None,
                        payload.get("score_status") or "pending",
                        payload.get("tldr_status") or "pending",
                        payload.get("translation_status") or "pending",
                        payload.get("analysis_status") or "pending",
                        completed_at,
                        report_at,
                    ),
                )

            # Preserve the source-specific legacy report while refreshing the
            # logical entity's shared metadata/keyword projection.
            self._sync_paper_entity_for_record(conn, source, paper_id)

            if delivered:
                conn.execute(
                    """
                    INSERT INTO paper_deliveries(
                        run_id, source, paper_id, canonical_id, version,
                        report_path, report_at, delivered_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, canonical_id, version) DO UPDATE SET
                        run_id = excluded.run_id,
                        paper_id = excluded.paper_id,
                        report_path = excluded.report_path,
                        report_at = excluded.report_at,
                        delivered_at = excluded.delivered_at
                    WHERE ? = 'imported'
                      AND (
                          paper_deliveries.report_at IS NULL
                          OR paper_deliveries.report_at = ''
                          OR excluded.report_at > paper_deliveries.report_at
                      )
                    """,
                    (
                        str(payload.get("delivery_run_id") or "legacy_import"),
                        source,
                        paper_id,
                        canonical_id,
                        version,
                        payload.get("report_path"),
                        report_at,
                        payload.get("delivered_at") or completed_at,
                        outcome,
                    ),
                )
        return outcome

    # ─── SQLite 历史修复与遗漏扫描 ────────────────────────────────────────

    @staticmethod
    def _paper_payload_from_row(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        """Decode one persisted paper payload without making report files input.

        Historical repair deliberately trusts SQLite instead of re-parsing
        HTML.  A malformed old payload is returned as an empty object so the
        caller can expose it as a retryable, actionable problem.
        """
        try:
            raw = row["paper_json"]
        except (KeyError, IndexError, TypeError):
            raw = None
        try:
            payload = json.loads(raw) if raw else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _published_date_from_payload(payload: Dict[str, Any]) -> Optional[date]:
        """Read the source day, falling back to legacy publication metadata."""
        if not isinstance(payload, dict):
            return None
        for field in ("source_date", "published_date"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            text = value.strip()
            try:
                return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
            except ValueError:
                try:
                    return date.fromisoformat(text[:10])
                except ValueError:
                    continue
        return None

    @staticmethod
    def _report_date_from_value(value: Any) -> Optional[date]:
        """Parse a persisted report-batch timestamp without using paper metadata."""
        if not isinstance(value, str) or not value.strip():
            return None
        text = value.strip()
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None

    def historical_delivery_date_range(self, source: str = "arxiv") -> Optional[Tuple[date, date]]:
        """Return the imported report-batch coverage for one historical source.

        Historical omission scans repair imported archive coverage, so their
        range must follow the archived report batches rather than a paper's
        original publication day.  In particular, a newly revised old paper
        must never extend a 2026 archive scan back to its original year.

        The imported-card timestamp is durable SQLite state; report directories
        and legacy JSON files are deliberately not read here.  Ordinary daily
        deliveries are excluded because their normal incremental scan and
        watermarks already cover them.  The range is therefore stable when
        new daily reports are delivered after an archive import.
        """
        normalized_source = str(source or "").strip().lower()
        if not normalized_source:
            raise ValueError("source must be non-empty")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(
                    NULLIF(TRIM(deliveries.report_at), ''),
                    NULLIF(TRIM(papers.legacy_report_at), '')
                ) AS report_at
                FROM paper_deliveries AS deliveries
                JOIN daily_papers AS papers
                  ON papers.source = deliveries.source
                 AND papers.paper_id = deliveries.paper_id
                WHERE deliveries.source = ?
                  AND NULLIF(TRIM(papers.legacy_report_at), '') IS NOT NULL
                """,
                (normalized_source,),
            ).fetchall()
        days = [
            report_day
            for row in rows
            for report_day in [self._report_date_from_value(row["report_at"])]
            if report_day is not None
        ]
        return (min(days), max(days)) if days else None

    def history_repair_candidates(
        self,
        *,
        include_deep_analysis: bool = True,
        limit: int = 0,
    ) -> list[Dict[str, Any]]:
        """Find delivered papers whose SQLite fields or report patch need repair.

        The query reads only the authoritative relational history.  It does
        not inspect report contents: HTML is an output to patch *after* a
        missing field is repaired, never evidence used to decide whether a
        field exists.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("history repair limit must be a non-negative integer")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT papers.*, deliveries.run_id AS delivery_run_id,
                       deliveries.report_path AS delivery_report_path,
                       deliveries.delivered_at,
                       runs.report_paths_json AS delivery_report_paths_json
                FROM paper_deliveries AS deliveries
                JOIN daily_papers AS papers
                  ON papers.source = deliveries.source
                 AND papers.paper_id = deliveries.paper_id
                LEFT JOIN daily_runs AS runs ON runs.run_id = deliveries.run_id
                ORDER BY deliveries.delivered_at ASC, deliveries.delivery_id ASC
                """
            ).fetchall()

        candidates: list[Dict[str, Any]] = []
        for row in rows:
            payload = self._paper_payload_from_row(row)
            needs: list[str] = []
            score_payload: Dict[str, Any] = {}
            try:
                decoded_score = json.loads(row["score_json"]) if row["score_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                decoded_score = {}
            if isinstance(decoded_score, dict):
                score_payload = decoded_score

            score_ready = row["score_status"] == "succeeded" and bool(score_payload)
            if not score_ready:
                needs.append("score")
            elif (
                row["tldr_status"] != "succeeded"
                or not isinstance(score_payload.get("tldr"), str)
                or not score_payload["tldr"].strip()
            ):
                needs.append("tldr")

            abstract = str(payload.get("abstract") or "").strip()
            if abstract and (
                row["translation_status"] != "succeeded"
                or not str(row["abstract_cn"] or "").strip()
            ):
                needs.append("translation")

            qualified = bool(score_payload.get("is_qualified", False))
            pdf_url = str(payload.get("pdf_url") or "").strip()
            arxiv_canonical, _ = self._arxiv_identity_from_value(
                payload.get("arxiv_id") or payload.get("arxiv_url")
            )
            has_pdf_access = bool(pdf_url or arxiv_canonical)
            if (
                include_deep_analysis
                and score_ready
                and qualified
                and has_pdf_access
                and (
                    row["analysis_status"] != "succeeded"
                    or not str(row["analysis_json"] or "").strip()
                )
            ):
                needs.append("analysis")

            patch_status = str(row["report_repair_status"] or "not_needed")
            if patch_status in {"pending", "failed"}:
                needs.append("report_patch")

            if not needs:
                continue
            candidates.append(
                {
                    "source": row["source"],
                    "paper_id": row["paper_id"],
                    "canonical_id": row["canonical_id"],
                    "version": int(row["version"] or 0),
                    "paper_json": payload,
                    "score_json": score_payload,
                    "abstract_cn": str(row["abstract_cn"] or ""),
                    "analysis_json": row["analysis_json"],
                    "needs": needs,
                    "delivery_run_id": row["delivery_run_id"],
                    "delivery_report_path": row["delivery_report_path"],
                    "delivery_report_paths_json": row["delivery_report_paths_json"],
                    "report_repair_status": patch_status,
                }
            )
            if limit and len(candidates) >= limit:
                break
        return candidates

    def history_repair_summary(self, *, include_deep_analysis: bool = True) -> Dict[str, Any]:
        """Count outstanding SQLite-driven history repairs by field."""
        candidates = self.history_repair_candidates(
            include_deep_analysis=include_deep_analysis
        )
        by_need: Dict[str, int] = {}
        for candidate in candidates:
            for need in candidate["needs"]:
                by_need[need] = by_need.get(need, 0) + 1
        return {"pending": len(candidates), "by_need": by_need}

    def report_paths_for_paper(self, source: str, paper_id: str) -> list[str]:
        """Return saved report artifacts for one delivered paper, HTML first."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT deliveries.report_path, deliveries.run_id,
                       runs.report_paths_json
                FROM paper_deliveries AS deliveries
                LEFT JOIN daily_runs AS runs ON runs.run_id = deliveries.run_id
                WHERE deliveries.source = ? AND deliveries.paper_id = ?
                ORDER BY deliveries.delivery_id DESC
                LIMIT 1
                """,
                (source, paper_id),
            ).fetchone()
        if row is None:
            return []

        paths: list[str] = []
        try:
            report_paths = json.loads(row["report_paths_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            report_paths = {}
        if isinstance(report_paths, dict):
            for key in (f"{source}_html", source):
                value = report_paths.get(key)
                if isinstance(value, str) and value.strip():
                    paths.append(value.strip())
            for key, value in report_paths.items():
                if (
                    isinstance(key, str)
                    and key.startswith(f"{source}_")
                    and isinstance(value, str)
                    and value.strip()
                ):
                    paths.append(value.strip())
        direct = row["report_path"]
        if isinstance(direct, str) and direct.strip():
            paths.append(direct.strip())

        unique: list[str] = []
        for value in paths:
            if value not in unique:
                unique.append(value)
        unique.sort(key=lambda value: (0 if value.lower().endswith(".html") else 1, value))
        return unique

    @staticmethod
    def normalize_report_reference(value: object) -> str:
        """Return a portable path below ``reports`` for archive relocation.

        Worker and WebUI containers persist paths such as
        ``/app/data/reports/...`` while a host-side process can use a project
        path or a relative ``data/reports/...`` path.  Migration needs a
        stable comparison key without assuming either deployment layout.
        """
        raw = str(value or "").strip().replace("\\", "/")
        if not raw:
            return ""
        lowered = raw.casefold()
        marker = "/reports/"
        marker_index = lowered.rfind(marker)
        if marker_index >= 0:
            return raw[marker_index + len(marker) :].lstrip("/")
        if lowered.startswith("reports/"):
            return raw[len("reports/") :].lstrip("/")
        return raw.lstrip("./")

    def token_usage_report_references(self, references: list[str]) -> set[str]:
        """Return report references already represented by live token rows.

        A v4 daily/supplement run stores its generated report paths in
        ``daily_runs`` and its token summary under the same ``run_id``.  The
        historical-report importer uses this link to avoid adding a synthetic
        row for reports that are already covered by the normal runtime
        accounting.
        """
        wanted = {
            self.normalize_report_reference(value).casefold()
            for value in references
            if self.normalize_report_reference(value)
        }
        if not wanted:
            return set()

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT runs.run_id, runs.report_paths_json
                FROM daily_runs AS runs
                WHERE runs.report_paths_json IS NOT NULL
                  AND trim(runs.report_paths_json) != ''
                  AND EXISTS (
                      SELECT 1 FROM run_token_usage AS usage
                      WHERE usage.run_id = runs.run_id
                  )
                """
            ).fetchall()

        matched: set[str] = set()

        def collect(value: object) -> None:
            if isinstance(value, str):
                reference = self.normalize_report_reference(value).casefold()
                if reference in wanted:
                    matched.add(reference)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, (list, tuple)):
                for nested in value:
                    collect(nested)

        for row in rows:
            try:
                collect(json.loads(row["report_paths_json"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return matched

    @classmethod
    def _relocate_report_reference(
        cls, value: object, replacements: Dict[str, str]
    ) -> tuple[object, bool]:
        """Replace one saved report path while preserving its deployment prefix."""
        if not isinstance(value, str):
            return value, False
        raw = value.strip()
        key = cls.normalize_report_reference(raw)
        replacement = replacements.get(key.casefold())
        if not replacement:
            return value, False

        normalized = raw.replace("\\", "/")
        lowered = normalized.casefold()
        report_marker = "/reports/"
        report_index = lowered.rfind(report_marker)
        if report_index >= 0:
            return normalized[: report_index + len(report_marker)] + replacement, True
        if lowered.startswith("reports/"):
            return "reports/" + replacement, True
        if lowered == key.casefold():
            return replacement, True
        suffix = "/" + key.casefold()
        if lowered.endswith(suffix):
            return normalized[: -len(key)] + replacement, True
        return value, False

    @classmethod
    def _relocate_report_value(
        cls, value: object, replacements: Dict[str, str]
    ) -> tuple[object, int]:
        """Recursively update report-path JSON while retaining unknown values."""
        if isinstance(value, str):
            replaced, changed = cls._relocate_report_reference(value, replacements)
            return replaced, int(changed)
        if isinstance(value, list):
            changed = 0
            updated = []
            for item in value:
                replacement, item_changed = cls._relocate_report_value(item, replacements)
                updated.append(replacement)
                changed += item_changed
            return updated, changed
        if isinstance(value, dict):
            changed = 0
            updated: Dict[str, Any] = {}
            for key, item in value.items():
                replacement, item_changed = cls._relocate_report_value(item, replacements)
                updated[key] = replacement
                changed += item_changed
            return updated, changed
        return value, 0

    def supplement_report_paths(self) -> list[str]:
        """Return artifacts recorded by durable supplement runs.

        The result includes both HTML and Markdown paths and intentionally
        preserves their stored spelling.  Callers can normalise each value for
        the current host/container layout with :meth:`normalize_report_reference`.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT report_paths_json FROM daily_runs WHERE run_kind = 'supplement'"
            ).fetchall()
        paths: list[str] = []
        for row in rows:
            try:
                report_paths = json.loads(row["report_paths_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

            def collect(item: object) -> None:
                if isinstance(item, str) and item.strip():
                    paths.append(item.strip())
                elif isinstance(item, dict):
                    for nested in item.values():
                        collect(nested)
                elif isinstance(item, list):
                    for nested in item:
                        collect(nested)

            collect(report_paths)
        return list(dict.fromkeys(paths))

    def relocate_report_paths(self, path_map: Dict[str, str]) -> Dict[str, int]:
        """Synchronise report references after a safe archive-file relocation.

        ``path_map`` uses paths relative to ``data/reports``.  The method
        updates both run artifacts and paper deliveries in one SQLite
        transaction, retaining each stored path's host/container prefix.
        """
        replacements: Dict[str, str] = {}
        for old, new in path_map.items():
            old_key = self.normalize_report_reference(old)
            new_key = self.normalize_report_reference(new)
            if not old_key or not new_key:
                raise ValueError("报告迁移路径不能为空")
            replacements[old_key.casefold()] = new_key
        if not replacements:
            return {"runs": 0, "deliveries": 0}

        updated_runs = 0
        updated_deliveries = 0
        with self._lock, self._connect() as conn:
            run_rows = conn.execute(
                "SELECT run_id, report_paths_json FROM daily_runs WHERE report_paths_json IS NOT NULL"
            ).fetchall()
            for row in run_rows:
                try:
                    parsed = json.loads(row["report_paths_json"] or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                relocated, changed = self._relocate_report_value(parsed, replacements)
                if not changed:
                    continue
                conn.execute(
                    "UPDATE daily_runs SET report_paths_json = ? WHERE run_id = ?",
                    (json.dumps(relocated, ensure_ascii=False), row["run_id"]),
                )
                updated_runs += 1

            delivery_rows = conn.execute(
                "SELECT delivery_id, report_path FROM paper_deliveries WHERE report_path IS NOT NULL"
            ).fetchall()
            for row in delivery_rows:
                relocated, changed = self._relocate_report_reference(
                    row["report_path"], replacements
                )
                if not changed:
                    continue
                conn.execute(
                    "UPDATE paper_deliveries SET report_path = ? WHERE delivery_id = ?",
                    (relocated, row["delivery_id"]),
                )
                updated_deliveries += 1
        return {"runs": updated_runs, "deliveries": updated_deliveries}

    def set_history_report_repair_status(
        self,
        source: str,
        paper_id: str,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        """Persist report-patching state so file errors remain retryable."""
        if status not in {"not_needed", "pending", "succeeded", "failed"}:
            raise ValueError(f"invalid history report repair status: {status}")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET report_repair_status = ?, report_repair_error = ?
                WHERE source = ? AND paper_id = ?
                """,
                (status, str(error)[:4000] if error else None, source, paper_id),
            )

    def record_supplement_backlog(self, entries: list[Dict[str, Any]]) -> int:
        """Queue papers that need a supplement run; returns newly queued count.

        Idempotent per ``(source, canonical_id, version)``; already delivered
        rows keep their terminal status, and an existing reason is never
        downgraded to a vaguer one by a re-import.
        """
        now = datetime.now().isoformat()
        inserted = 0
        with self._lock, self._connect() as conn:
            for entry in entries:
                source = str(entry["source"]).strip().lower()
                canonical_id = str(entry["canonical_id"]).strip()
                if not source or not canonical_id:
                    continue
                existing = conn.execute(
                    "SELECT status FROM supplement_backlog "
                    "WHERE source = ? AND canonical_id = ? AND version = ?",
                    (source, canonical_id, int(entry.get("version") or 0)),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO supplement_backlog (
                            source, canonical_id, version, paper_id, reason,
                            detail, paper_json, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                        """,
                        (
                            source,
                            canonical_id,
                            int(entry.get("version") or 0),
                            entry.get("paper_id"),
                            entry["reason"],
                            entry.get("detail"),
                            (
                                json.dumps(entry["paper_json"], ensure_ascii=False)
                                if entry.get("paper_json") is not None
                                else None
                            ),
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                elif existing["status"] != "delivered":
                    conn.execute(
                        """
                        UPDATE supplement_backlog
                        SET paper_id = COALESCE(?, paper_id),
                            detail = ?, updated_at = ?,
                            paper_json = COALESCE(?, paper_json)
                        WHERE source = ? AND canonical_id = ? AND version = ?
                        """,
                        (
                            entry.get("paper_id"),
                            entry.get("detail"),
                            now,
                            (
                                json.dumps(entry["paper_json"], ensure_ascii=False)
                                if entry.get("paper_json") is not None
                                else None
                            ),
                            source,
                            canonical_id,
                            int(entry.get("version") or 0),
                        ),
                    )
        return inserted

    @staticmethod
    def _normalize_supplement_reasons(reasons: Optional[set[str] | list[str] | tuple[str, ...]]) -> Optional[list[str]]:
        if reasons is None:
            return None
        normalized = sorted(
            {
                str(reason).strip()
                for reason in reasons
                if isinstance(reason, str) and str(reason).strip()
            }
        )
        if not normalized:
            return []
        allowed = {"missing_data", "missing_analysis", "missing_translation", "missed_scan"}
        invalid = set(normalized).difference(allowed)
        if invalid:
            raise ValueError("invalid supplement backlog reasons: " + ", ".join(sorted(invalid)))
        return normalized

    @staticmethod
    def _normalize_history_date_filter(value: Optional[date | str], field: str) -> Optional[date]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError as exc:
                raise ValueError(f"{field} must be YYYY-MM-DD") from exc
        raise ValueError(f"{field} must be YYYY-MM-DD")

    @classmethod
    def _supplement_row_matches_date_filter(
        cls,
        row: Dict[str, Any],
        published_from: Optional[date],
        published_to: Optional[date],
    ) -> bool:
        if published_from is None and published_to is None:
            return True
        payload = row.get("paper_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
        paper_day = cls._published_date_from_payload(
            payload if isinstance(payload, dict) else {}
        )
        if paper_day is None:
            return False
        return (
            (published_from is None or paper_day >= published_from)
            and (published_to is None or paper_day <= published_to)
        )

    def supplement_backlog_summary(
        self,
        *,
        reasons: Optional[set[str] | list[str] | tuple[str, ...]] = None,
        published_from: Optional[date | str] = None,
        published_to: Optional[date | str] = None,
    ) -> Dict[str, Any]:
        """Return per-status/per-reason counts plus the oldest pending row.

        Optional reason/date filters let a natural-week omission workflow
        count only its own queued papers without inspecting any report file.
        """
        normalized_reasons = self._normalize_supplement_reasons(reasons)
        start_day = self._normalize_history_date_filter(published_from, "published_from")
        end_day = self._normalize_history_date_filter(published_to, "published_to")
        if start_day and end_day and start_day > end_day:
            raise ValueError("published_from must not be after published_to")
        with self._connect() as conn:
            query = "SELECT reason, status, created_at, paper_json FROM supplement_backlog"
            params: list[Any] = []
            if normalized_reasons is not None:
                if not normalized_reasons:
                    rows = []
                else:
                    placeholders = ", ".join("?" for _ in normalized_reasons)
                    query += f" WHERE reason IN ({placeholders})"
                    rows = conn.execute(query, normalized_reasons).fetchall()
            else:
                rows = conn.execute(query, params).fetchall()
        filtered = [
            dict(row)
            for row in rows
            if self._supplement_row_matches_date_filter(dict(row), start_day, end_day)
        ]
        breakdown: Dict[str, Dict[str, int]] = {}
        pending_created: list[str] = []
        for row in filtered:
            bucket = breakdown.setdefault(row["reason"], {})
            bucket[row["status"]] = bucket.get(row["status"], 0) + 1
            if row["status"] in {"pending", "failed"} and row.get("created_at"):
                pending_created.append(str(row["created_at"]))
        return {
            "breakdown": breakdown,
            "pending": sum(
                1 for row in filtered if row["status"] in {"pending", "failed"}
            ),
            "oldest_pending_at": min(pending_created) if pending_created else None,
        }

    def claim_supplement_backlog(
        self,
        limit: int = 0,
        *,
        reasons: Optional[set[str] | list[str] | tuple[str, ...]] = None,
        published_from: Optional[date | str] = None,
        published_to: Optional[date | str] = None,
    ) -> list[Dict[str, Any]]:
        """Select the next backlog papers for one supplement run.

        Pending data-repair entries (from the legacy import) are selected
        before pending missed-scan discoveries, oldest first.  Failed rows
        retry after fresh work so one currently-unfetchable legacy paper
        cannot consume every capped batch and starve the rest of the import.
        The loader also continues past a failed *pending* repair to fill the
        cap with later deliverable rows in the same supplement report.
        Returned rows carry the persisted paper metadata when the import
        already reconstructed it.  ``limit=0`` means all rows, matching
        ``daily_research.max_papers_per_run`` semantics.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("supplement backlog limit must be a non-negative integer")
        normalized_reasons = self._normalize_supplement_reasons(reasons)
        start_day = self._normalize_history_date_filter(published_from, "published_from")
        end_day = self._normalize_history_date_filter(published_to, "published_to")
        if start_day and end_day and start_day > end_day:
            raise ValueError("published_from must not be after published_to")
        if normalized_reasons == []:
            return []
        bounded = int(limit)
        query = """
                SELECT source, canonical_id, version, paper_id, reason,
                       detail, paper_json
                FROM supplement_backlog
                WHERE status IN ('pending', 'failed')
                """
        params: list[Any] = []
        if normalized_reasons is not None:
            placeholders = ", ".join("?" for _ in normalized_reasons)
            query += f" AND reason IN ({placeholders})"
            params.extend(normalized_reasons)
        query += """
                ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                         CASE reason WHEN 'missed_scan' THEN 1 ELSE 0 END,
                         created_at ASC,
                         backlog_id ASC
                """
        if bounded:
            # Date filtering happens after parsing stored metadata. Do not
            # apply SQL LIMIT first, otherwise a few malformed old rows could
            # starve later valid papers from a capped natural-week report.
            pass
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item.get("paper_json"):
                try:
                    item["paper_json"] = json.loads(item["paper_json"])
                except (TypeError, ValueError):
                    item["paper_json"] = None
            if not self._supplement_row_matches_date_filter(item, start_day, end_day):
                continue
            result.append(item)
            if bounded and len(result) >= bounded:
                break
        return result

    def resolve_supplement_backlog(
        self,
        run_id: str,
        identities: list[Tuple[str, str, int]],
        *,
        status: str,
        detail: Optional[str] = None,
    ) -> int:
        """Mark selected backlog rows delivered/failed/skipped after a run."""
        if status not in ("delivered", "failed", "skipped"):
            raise ValueError(f"invalid supplement backlog status: {status}")
        now = datetime.now().isoformat()
        resolved = 0
        with self._lock, self._connect() as conn:
            for source, canonical_id, version in identities:
                cursor = conn.execute(
                    """
                    UPDATE supplement_backlog
                    SET status = ?, detail = COALESCE(?, detail),
                        resolved_run_id = ?, updated_at = ?
                    WHERE source = ? AND canonical_id = ? AND version = ?
                      AND status != 'delivered'
                    """,
                    (status, detail, run_id, now, source, canonical_id, int(version or 0)),
                )
                resolved += max(0, cursor.rowcount or 0)
        return resolved

    def missed_scan_week_groups(self) -> Dict[date, int]:
        """Return pending omission rows grouped by ISO calendar week (Mon-Sun).

        The grouping is derived from the stored paper publication date, not a
        report filename or a directory scan. Rows with malformed metadata are
        deliberately omitted here; the caller can surface them as a retryable
        issue instead of assigning them to an arbitrary week.
        """
        rows = self.claim_supplement_backlog(0, reasons={"missed_scan"})
        groups: Dict[date, int] = {}
        for row in rows:
            payload = row.get("paper_json")
            published = self._published_date_from_payload(
                payload if isinstance(payload, dict) else {}
            )
            if published is None:
                continue
            week_start = published - timedelta(days=published.weekday())
            groups[week_start] = groups.get(week_start, 0) + 1
        return dict(sorted(groups.items()))

    # ─── 过去日报日期队列 ────────────────────────────────────────────────

    @staticmethod
    def _normalize_backfill_date(value: date | str) -> date:
        """Validate one historical report date without accepting datetimes."""
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("过去日报日期必须是 YYYY-MM-DD") from exc
        else:
            raise ValueError("过去日报日期必须是 YYYY-MM-DD")
        if parsed >= date.today():
            raise ValueError("过去日报日期必须早于今天")
        if parsed < date(1991, 1, 1):
            raise ValueError("过去日报日期早于 arXiv 可用范围")
        return parsed

    def enqueue_backfill_range(
        self, date_from: date | str, date_to: date | str
    ) -> Dict[str, Any]:
        """Persist every day in a user-requested historical-report range.

        A new ``batch_id`` intentionally allows the same day to be requested
        again later.  That is useful when the per-report paper cap left
        undelivered candidates on a prior run; SQLite's delivery ledger still
        prevents duplicate paper deliveries.
        """
        start = self._normalize_backfill_date(date_from)
        end = self._normalize_backfill_date(date_to)
        if start > end:
            raise ValueError("过去日报起始日期不能晚于结束日期")

        now = datetime.now().isoformat()
        batch_id = f"{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:8]}"
        dates: list[tuple[str, str, str]] = []
        cursor = start
        while cursor <= end:
            dates.append((batch_id, cursor.isoformat(), now))
            cursor += timedelta(days=1)

        with self._lock, self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO backfill_queue(
                    batch_id, target_date, status, requested_at
                ) VALUES (?, ?, 'pending', ?)
                """,
                dates,
            )
        return {
            "batch_id": batch_id,
            "queued": len(dates),
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }

    def recover_interrupted_backfill_jobs(self) -> int:
        """Return a stale in-progress day to the durable pending queue.

        This method is called only by the single ``backfill_run`` worker after
        it owns the mode lock.  It therefore never steals a genuinely running
        day from another worker.
        """
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE backfill_queue
                SET status = 'pending', started_at = NULL,
                    error = COALESCE(error, '工作进程中断，已恢复到待处理队列'),
                    completed_at = NULL
                WHERE status = 'running'
                """
            )
        return max(0, cursor.rowcount or 0)

    def claim_next_backfill_job(self) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest pending historical-report day."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT backfill_id, batch_id, target_date, requested_at
                FROM backfill_queue
                WHERE status = 'pending'
                ORDER BY requested_at ASC, target_date ASC, backfill_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE backfill_queue
                SET status = 'running', started_at = ?, completed_at = NULL,
                    error = NULL
                WHERE backfill_id = ? AND status = 'pending'
                """,
                (now, row["backfill_id"]),
            )
            # The enclosing context commits this state change atomically before
            # another process can see the row as pending again.
            return dict(row)

    def complete_backfill_job(
        self, backfill_id: int, *, run_id: Optional[str] = None
    ) -> bool:
        """Mark one claimed calendar day complete after its report run succeeds."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE backfill_queue
                SET status = 'completed', completed_at = ?, run_id = ?, error = NULL
                WHERE backfill_id = ? AND status = 'running'
                """,
                (now, run_id, int(backfill_id)),
            )
        return bool(cursor.rowcount)

    def fail_backfill_job(self, backfill_id: int, error: str) -> bool:
        """Keep a failed date visible for a later user-requested retry."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE backfill_queue
                SET status = 'failed', completed_at = ?, error = ?
                WHERE backfill_id = ? AND status = 'running'
                """,
                (now, str(error or "过去日报运行失败")[:4000], int(backfill_id)),
            )
        return bool(cursor.rowcount)

    def requeue_backfill_job(self, backfill_id: int, detail: str = "") -> bool:
        """Return an interrupted day to pending so a later worker resumes it."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE backfill_queue
                SET status = 'pending', started_at = NULL, completed_at = NULL,
                    error = NULLIF(?, '')
                WHERE backfill_id = ? AND status = 'running'
                """,
                (str(detail)[:4000], int(backfill_id)),
            )
        return bool(cursor.rowcount)

    def backfill_queue_summary(self) -> Dict[str, Any]:
        """Return compact queue state for the WebUI without exposing errors."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM backfill_queue GROUP BY status"
            ).fetchall()
            active = conn.execute(
                """
                SELECT target_date, batch_id, started_at
                FROM backfill_queue WHERE status = 'running'
                ORDER BY started_at ASC, backfill_id ASC LIMIT 1
                """
            ).fetchone()
            next_row = conn.execute(
                """
                SELECT target_date, batch_id
                FROM backfill_queue WHERE status = 'pending'
                ORDER BY requested_at ASC, target_date ASC, backfill_id ASC LIMIT 1
                """
            ).fetchone()
        counts = {str(row["status"]): int(row["n"] or 0) for row in rows}
        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "active_date": active["target_date"] if active else None,
            "next_date": next_row["target_date"] if next_row else None,
        }

    def backfill_batch_summary(self, batch_id: str) -> Dict[str, Any]:
        """Return one requested range's completion counts and first safe error.

        The WebUI needs only the global queue state, while a result
        notification must describe the exact range the user asked to run.
        Error text is deliberately bounded and stays in a notification only
        when that request itself failed.
        """
        batch = str(batch_id or "").strip()
        if not batch:
            raise ValueError("backfill batch_id 不能为空")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS n
                FROM backfill_queue
                WHERE batch_id = ?
                GROUP BY status
                """,
                (batch,),
            ).fetchall()
            first_failure = conn.execute(
                """
                SELECT target_date, error
                FROM backfill_queue
                WHERE batch_id = ? AND status = 'failed'
                ORDER BY target_date ASC, backfill_id ASC
                LIMIT 1
                """,
                (batch,),
            ).fetchone()
        counts = {str(row["status"]): int(row["n"] or 0) for row in rows}
        return {
            "batch_id": batch,
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
            "first_failed_date": first_failure["target_date"] if first_failure else None,
            "first_error": first_failure["error"] if first_failure else None,
        }

    # ─── 小型键值状态（跨运行决策） ─────────────────────────────────────

    def get_app_state(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (str(key),)
            ).fetchone()
        return row["value"] if row else None

    def set_app_state(self, key: str, value: str) -> None:
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (str(key), str(value), now),
            )

    def get_daily_token_totals(self, days: Optional[int] = None) -> list[Dict[str, Any]]:
        """Aggregate persisted token usage by calendar day (oldest first).

        ``days`` limits the window to the most recent N days; None returns
        the full history.  Each row: ``{"date", "prompt", "completion",
        "total", "runs"}``.
        """
        query = (
            "SELECT substr(recorded_at, 1, 10) AS day, "
            "SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total, "
            "COUNT(DISTINCT run_id) AS runs "
            "FROM run_token_usage"
        )
        params: list[Any] = []
        if days is not None and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
            query += " WHERE substr(recorded_at, 1, 10) >= ?"
            params.append(cutoff)
        query += " GROUP BY day ORDER BY day"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "date": row["day"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
                "runs": row["runs"] or 0,
            }
            for row in rows
        ]

    def get_token_usage_by_model(self, days: Optional[int] = None) -> list[Dict[str, Any]]:
        """Aggregate persisted token usage by model over the window."""
        query = (
            "SELECT model, SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total "
            "FROM run_token_usage"
        )
        params: list[Any] = []
        if days is not None and days > 0:
            cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
            query += " WHERE substr(recorded_at, 1, 10) >= ?"
            params.append(cutoff)
        query += " GROUP BY model ORDER BY total DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "model": row["model"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
            }
            for row in rows
        ]

    @staticmethod
    def _token_usage_time_filter(
        start_at: Optional[datetime], end_at: Optional[datetime]
    ) -> tuple[str, list[str]]:
        """Build a lexicographically-safe ISO timestamp range for token rows.

        ``recorded_at`` is persisted with ``datetime.isoformat()``, so SQLite
        can compare the local, zero-padded ISO strings directly.  Keeping the
        query range here avoids each WebUI endpoint inventing subtly different
        definitions for a day or a rolling 24-hour window.
        """

        clauses: list[str] = []
        params: list[str] = []
        if start_at is not None:
            clauses.append("recorded_at >= ?")
            params.append(start_at.isoformat())
        if end_at is not None:
            clauses.append("recorded_at < ?")
            params.append(end_at.isoformat())
        return (f" WHERE {' AND '.join(clauses)}" if clauses else "", params)

    def get_token_usage_series(
        self,
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
        bucket: str = "day",
    ) -> list[Dict[str, Any]]:
        """Aggregate token use for a precise local-time range.

        ``bucket`` is either ``day`` or ``hour``.  The caller supplies the
        complete display window, which lets the modern WebUI accurately show
        both "today" and a rolling 24-hour chart without approximating either
        from calendar-day totals.
        """

        if bucket not in {"day", "hour"}:
            raise ValueError("token usage bucket must be 'day' or 'hour'")
        label = "substr(recorded_at, 1, 10)" if bucket == "day" else "substr(recorded_at, 1, 13)"
        where, params = self._token_usage_time_filter(start_at, end_at)
        query = (
            f"SELECT {label} AS bucket, "
            "SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total, "
            "COUNT(DISTINCT run_id) AS runs "
            "FROM run_token_usage"
            f"{where} GROUP BY bucket ORDER BY bucket"
        )
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "bucket": row["bucket"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
                "runs": row["runs"] or 0,
            }
            for row in rows
        ]

    def get_token_usage_summary(
        self,
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """Return compact totals for the same exact token-use window."""

        where, params = self._token_usage_time_filter(start_at, end_at)
        query = (
            "SELECT SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total, "
            "COUNT(DISTINCT run_id) AS runs "
            "FROM run_token_usage"
            f"{where}"
        )
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return {
            "prompt": int(row["prompt"] or 0),
            "completion": int(row["completion"] or 0),
            "total": int(row["total"] or 0),
            "runs": int(row["runs"] or 0),
        }

    def get_token_usage_by_model_range(
        self,
        *,
        start_at: Optional[datetime] = None,
        end_at: Optional[datetime] = None,
    ) -> list[Dict[str, Any]]:
        """Aggregate token use by model for a precise local-time range."""

        where, params = self._token_usage_time_filter(start_at, end_at)
        query = (
            "SELECT model, SUM(prompt_tokens) AS prompt, "
            "SUM(completion_tokens) AS completion, "
            "SUM(total_tokens) AS total "
            "FROM run_token_usage"
            f"{where} GROUP BY model ORDER BY total DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "model": row["model"],
                "prompt": row["prompt"] or 0,
                "completion": row["completion"] or 0,
                "total": row["total"] or 0,
            }
            for row in rows
        ]

    # ==================== LLM health ====================

    def record_llm_health_event(
        self,
        role: str,
        model: str,
        success: bool,
        error_summary: Optional[str] = None,
    ) -> None:
        """Persist the final outcome of one real LLM operation.

        The caller owns retrying.  A failed event therefore means the full
        retry budget was exhausted (or a fatal provider error occurred), not
        merely that one transient attempt was retried successfully.
        """
        normalized_role = str(role or "").strip().lower()
        if normalized_role not in {"cheap", "smart"}:
            raise ValueError(f"invalid LLM role: {role!r}")
        normalized_model = str(model or "").strip() or "unknown"
        status = "succeeded" if success else "failed"
        # Health events contain only a compact, pre-sanitized user-facing
        # explanation.  Bound it again at this storage boundary so direct
        # callers cannot accidentally turn the table into a log sink.
        detail = str(error_summary or "").strip()[:360] or None
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_health_events(
                    role, model, status, error_summary, occurred_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (normalized_role, normalized_model, status, detail, now),
            )

    def get_llm_health(self, window: int = 20) -> Dict[str, Dict[str, Any]]:
        """Summarize recent real-call outcomes for the cheap and smart roles."""
        bounded_window = max(1, min(int(window), 100))
        summaries: Dict[str, Dict[str, Any]] = {}
        with self._connect() as conn:
            for role in ("cheap", "smart"):
                rows = conn.execute(
                    """
                    SELECT model, status, error_summary, occurred_at
                    FROM llm_health_events
                    WHERE role = ?
                    ORDER BY occurred_at DESC, event_id DESC
                    LIMIT ?
                    """,
                    (role, bounded_window),
                ).fetchall()
                if not rows:
                    continue

                newest = rows[0]
                last_success = next(
                    (row for row in rows if row["status"] == "succeeded"), None
                )
                last_failure = next(
                    (row for row in rows if row["status"] == "failed"), None
                )
                consecutive_failures = 0
                for row in rows:
                    if row["status"] != "failed":
                        break
                    consecutive_failures += 1
                succeeded = sum(1 for row in rows if row["status"] == "succeeded")
                summaries[role] = {
                    "last_status": newest["status"],
                    "last_event_at": newest["occurred_at"],
                    "last_model": newest["model"],
                    "last_success_at": last_success["occurred_at"] if last_success else None,
                    "last_error": last_failure["error_summary"] if last_failure else None,
                    "consecutive_failures": consecutive_failures,
                    "events_in_window": len(rows),
                    "success_rate": succeeded / len(rows),
                }
        return summaries

    def get_llm_health_by_model(self, days: Optional[int]) -> list[Dict[str, Any]]:
        """Summarize all recorded LLM calls by concrete model and time range.

        ``None`` selects the complete local history.  The rows remain
        read-only observations from completed real calls; no provider probe is
        sent when an operator opens the diagnostics page.
        """
        if days is not None and (
            isinstance(days, bool) or not isinstance(days, int) or days < 1
        ):
            raise ValueError("LLM 健康查看天数必须是正整数或 None")
        query = (
            "SELECT role, model, status, error_summary, occurred_at "
            "FROM llm_health_events"
        )
        params: list[Any] = []
        if days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " WHERE occurred_at >= ?"
            params.append(cutoff)
        query += " ORDER BY occurred_at DESC, event_id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        per_model: Dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            model = str(row["model"] or "").strip()[:200] or "unknown"
            per_model.setdefault(model, []).append(row)

        summaries: list[Dict[str, Any]] = []
        for model in sorted(per_model, key=lambda key: per_model[key][0]["occurred_at"], reverse=True):
            entries = per_model[model]
            succeeded = sum(1 for row in entries if row["status"] == "succeeded")
            newest = entries[0]
            newest_success = next(
                (row for row in entries if row["status"] == "succeeded"), None
            )
            newest_failure = next(
                (row for row in entries if row["status"] == "failed"), None
            )
            roles = []
            for row in entries:
                role = str(row["role"] or "").strip().lower()
                if role and role not in roles:
                    roles.append(role)
            summaries.append(
                {
                    "model": model,
                    "roles": roles,
                    "last_status": newest["status"],
                    "last_event_at": newest["occurred_at"],
                    "last_success_at": (
                        newest_success["occurred_at"] if newest_success else None
                    ),
                    "events_in_window": len(entries),
                    "succeeded_in_window": succeeded,
                    "success_rate": succeeded / len(entries),
                    "last_error": (
                        self._sanitize_source_health_error(newest_failure["error_summary"])
                        if newest_failure
                        else None
                    ),
                    "last_error_at": (
                        newest_failure["occurred_at"] if newest_failure else None
                    ),
                }
            )
        return summaries

    # ==================== Paper preferences ====================

    def add_auto_favorite_if_unmarked(
        self,
        source: str,
        paper_id: str,
        *,
        title: str,
        canonical_id: Optional[str] = None,
        version: Optional[int] = None,
        authors: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
    ) -> bool:
        """Add one automatic 收藏 without replacing a reader decision.

        Automatic qualification is a convenience for the reading list, not a
        reader-provided learning signal. ``DO NOTHING`` makes the insert safe
        across retries and concurrent workers, while preserving ``like``,
        ``dislike`` and explicit ``none`` rows exactly as the reader left them.
        """
        now = datetime.now().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO paper_preferences (
                    source, paper_id, canonical_id, version, preference,
                    title, authors_json, categories_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'like', ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO NOTHING
                """,
                (
                    source,
                    paper_id,
                    canonical_id,
                    version,
                    title,
                    json.dumps(list(authors or []), ensure_ascii=False),
                    json.dumps(list(categories or []), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return cursor.rowcount > 0

    def collect_qualified_favorites(self) -> Dict[str, int]:
        """Add every already-qualified paper to 收藏 without replacing a reader mark.

        This is the historical counterpart to :meth:`add_auto_favorite_if_unmarked`.
        It reads only persisted scoring results and keeps explicit ``dislike``
        and ``none`` decisions untouched through the same conflict-safe insert.
        """
        now = datetime.now().isoformat()
        scanned = 0
        qualified = 0
        added = 0
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, paper_id, canonical_id, version, paper_json, score_json
                FROM daily_papers
                WHERE score_json IS NOT NULL AND trim(score_json) <> ''
                """
            ).fetchall()
            scanned = len(rows)
            for row in rows:
                score = self._decode_json_object(row["score_json"])
                if score.get("is_qualified") is not True:
                    continue
                qualified += 1
                metadata = self._decode_json_object(row["paper_json"])
                title = str(metadata.get("title") or row["paper_id"] or "").strip()[:4_000]
                raw_authors = metadata.get("authors")
                authors = [
                    item.strip()[:500]
                    for item in raw_authors
                    if isinstance(item, str) and item.strip()
                ][:100] if isinstance(raw_authors, list) else []
                raw_categories = metadata.get("categories")
                categories = [
                    item.strip()[:100]
                    for item in raw_categories
                    if isinstance(item, str) and item.strip()
                ][:100] if isinstance(raw_categories, list) else []
                canonical_id = str(row["canonical_id"] or metadata.get("canonical_id") or "").strip()[:500] or None
                raw_version = row["version"] if row["version"] not in (None, 0) else metadata.get("version")
                try:
                    version = int(raw_version) if raw_version not in (None, "") else None
                except (TypeError, ValueError):
                    version = None
                cursor = conn.execute(
                    """
                    INSERT INTO paper_preferences (
                        source, paper_id, canonical_id, version, preference,
                        title, authors_json, categories_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'like', ?, ?, ?, ?, ?)
                    ON CONFLICT(source, paper_id) DO NOTHING
                    """,
                    (
                        str(row["source"]),
                        str(row["paper_id"]),
                        canonical_id,
                        version,
                        title or str(row["paper_id"]),
                        json.dumps(authors, ensure_ascii=False),
                        json.dumps(categories, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                added += max(0, int(cursor.rowcount))
        return {
            "scanned": scanned,
            "qualified": qualified,
            "added": added,
            "preserved": max(0, qualified - added),
        }

    def set_paper_preference(
        self,
        source: str,
        paper_id: str,
        *,
        preference: str,
        title: str,
        canonical_id: Optional[str] = None,
        version: Optional[int] = None,
        authors: Optional[list[str]] = None,
        categories: Optional[list[str]] = None,
    ) -> None:
        """Upsert one reader preference; clearing writes 'none', never a delete."""
        if preference not in ("like", "dislike", "none"):
            raise ValueError(f"invalid preference: {preference!r}")
        now = datetime.now().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO paper_preferences (
                    source, paper_id, canonical_id, version, preference,
                    title, authors_json, categories_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    preference = excluded.preference,
                    title = excluded.title,
                    authors_json = excluded.authors_json,
                    categories_json = excluded.categories_json,
                    updated_at = excluded.updated_at
                """,
                (
                    source,
                    paper_id,
                    canonical_id,
                    version,
                    preference,
                    title,
                    json.dumps(list(authors or []), ensure_ascii=False),
                    json.dumps(list(categories or []), ensure_ascii=False),
                    now,
                    now,
                ),
            )

            # Explicit preferences are the strongest learning signal. The
            # paper's own stored metadata supplies the terms, and clearing a
            # preference rewrites its signal to zero instead of deleting it.
            keyword_terms: list[str] = []
            author_terms = [name for name in (authors or []) if isinstance(name, str) and name.strip()]
            row = conn.execute(
                "SELECT paper_json, score_json FROM daily_papers "
                "WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()
            if row is not None:
                try:
                    metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                if isinstance(metadata, dict):
                    author_terms = author_terms or [
                        name
                        for name in (metadata.get("authors") or [])
                        if isinstance(name, str) and name.strip()
                    ]
                try:
                    score = json.loads(row["score_json"]) if row["score_json"] else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    score = {}
                if isinstance(score, dict):
                    keyword_terms = [
                        keyword
                        for keyword in (score.get("extracted_keywords") or [])
                        if isinstance(keyword, str) and keyword.strip()
                    ]
            signal = PREFERENCE_SIGNALS.get(preference)
            if signal is not None:
                self._upsert_learning_signals(
                    conn,
                    source,
                    paper_id,
                    keyword_terms,
                    "keyword",
                    "preference",
                    signal,
                    now,
                )
                self._upsert_learning_signals(
                    conn,
                    source,
                    paper_id,
                    author_terms,
                    "author",
                    "preference",
                    signal,
                    now,
                )

    @staticmethod
    def _preference_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        def parse(raw: object) -> list[str]:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else []
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
            return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []

        return {
            "source": row["source"],
            "paper_id": row["paper_id"],
            "canonical_id": row["canonical_id"],
            "version": row["version"],
            "preference": row["preference"],
            "title": row["title"],
            "authors": parse(row["authors_json"]),
            "categories": parse(row["categories_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_paper_preference(self, source: str, paper_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_preferences WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()
        return self._preference_row_to_dict(row) if row else None

    def get_preference_map(self, papers: list[Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
        """Batch lookup of preference strings for (source, paper_id) pairs."""
        result: Dict[Tuple[str, str], str] = {}
        with self._connect() as conn:
            for paper in papers:
                source = paper.get("source")
                paper_id = paper.get("paper_id")
                if not isinstance(source, str) or not isinstance(paper_id, str):
                    continue
                row = conn.execute(
                    "SELECT preference FROM paper_preferences "
                    "WHERE source = ? AND paper_id = ?",
                    (source, paper_id),
                ).fetchone()
                if row and row["preference"] != "none":
                    result[(source, paper_id)] = row["preference"]
        return result

    def list_preferences(
        self, *, preference: Optional[str] = None, limit: int = 100
    ) -> list[Dict[str, Any]]:
        """List marked papers newest-first; 'none' rows are skipped by default."""
        query = "SELECT * FROM paper_preferences"
        params: list[Any] = []
        if preference in ("like", "dislike"):
            query += " WHERE preference = ?"
            params.append(preference)
        elif preference != "all":
            query += " WHERE preference != 'none'"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._preference_row_to_dict(row) for row in rows]

    def get_preference_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT preference, COUNT(*) AS n FROM paper_preferences "
                "GROUP BY preference"
            ).fetchall()
        counts = {"like": 0, "dislike": 0, "none": 0}
        for row in rows:
            if row["preference"] in counts:
                counts[row["preference"]] = row["n"]
        return counts

    @staticmethod
    def _upsert_learning_signals(
        conn,
        source: str,
        paper_id: str,
        terms: list[str],
        term_type: str,
        signal_kind: str,
        signal: float,
        now: str,
    ) -> None:
        for term in terms:
            conn.execute(
                """
                INSERT INTO preference_learning_signals(
                    source, paper_id, term, term_type, signal_kind,
                    signal, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id, term, term_type, signal_kind)
                DO UPDATE SET
                    signal = excluded.signal,
                    recorded_at = excluded.recorded_at
                """,
                (source, paper_id, term, term_type, signal_kind, signal, now),
            )

    def record_learning_signals(
        self,
        source: str,
        paper_id: str,
        terms: list[str],
        term_type: str,
        signal_kind: str,
        signal: float,
    ) -> None:
        """Upsert one paper's learning signal for each given term."""
        if term_type not in ("keyword", "author"):
            raise ValueError(f"invalid term_type: {term_type!r}")
        if signal_kind not in ("preference", "v1_pass"):
            raise ValueError(f"invalid signal_kind: {signal_kind!r}")
        if isinstance(signal, bool) or not isinstance(signal, (int, float)):
            raise ValueError("signal 必须是数字")
        cleaned = [
            term.strip() for term in terms if isinstance(term, str) and term.strip()
        ]
        if not cleaned:
            return
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            self._upsert_learning_signals(
                conn, source, paper_id, cleaned, term_type, signal_kind,
                float(signal), now,
            )

    def get_learned_preference_terms(
        self, term_type: Optional[str] = None, limit: int = 200
    ) -> list[Dict[str, Any]]:
        """Aggregate the learned keyword/author library with net weights.

        Terms whose signals cancel out to (near) zero are skipped: they carry
        no usable preference either way.
        """
        bounded_limit = max(1, min(int(limit), 1000))
        query = (
            "SELECT term, term_type, SUM(signal) AS weight, COUNT(*) AS signals "
            "FROM preference_learning_signals "
        )
        params: list[Any] = []
        if term_type in ("keyword", "author"):
            query += "WHERE term_type = ? "
            params.append(term_type)
        query += (
            "GROUP BY term, term_type "
            "HAVING ABS(SUM(signal)) > 1e-9 "
            "ORDER BY ABS(SUM(signal)) DESC, term ASC LIMIT ?"
        )
        with self._connect() as conn:
            rows = conn.execute(query, [*params, bounded_limit]).fetchall()
        return [
            {
                "term": row["term"],
                "term_type": row["term_type"],
                "weight": float(row["weight"]),
                "signals": int(row["signals"]),
            }
            for row in rows
        ]

    def aggregate_liked_preferences(self) -> Dict[str, list[Dict[str, Any]]]:
        """Deterministic top authors/categories among liked papers.

        Pure SQLite + Python counting; no LLM involved by design — the goal is
        a faithful mirror of what the reader marked, not a model's guess.
        """
        rows = self.list_preferences(preference="like", limit=100000)
        author_counts: Dict[str, int] = {}
        category_counts: Dict[str, int] = {}
        for row in rows:
            for author in row["authors"]:
                key = author.strip()
                if key:
                    author_counts[key] = author_counts.get(key, 0) + 1
            for category in row["categories"]:
                key = category.strip()
                if key:
                    category_counts[key] = category_counts.get(key, 0) + 1

        def ranked(counts: Dict[str, int]) -> list[Dict[str, Any]]:
            return [
                {"name": name, "count": count}
                for name, count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )
            ]

        return {"authors": ranked(author_counts), "categories": ranked(category_counts)}

    def liked_paper_urls(self) -> Dict[Tuple[str, str], str]:
        """URL lookup for liked papers, taken from their stored metadata."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.source, p.paper_id, d.paper_json FROM paper_preferences p "
                "LEFT JOIN daily_papers d "
                "ON d.source = p.source AND d.paper_id = p.paper_id "
                "WHERE p.preference = 'like'"
            ).fetchall()
        urls: Dict[Tuple[str, str], str] = {}
        for row in rows:
            try:
                metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            url = metadata.get("url") if isinstance(metadata, dict) else None
            if isinstance(url, str) and url.strip():
                urls[(row["source"], row["paper_id"])] = url.strip()
        return urls

    def aggregate_liked_keywords(self, limit: int = 200) -> list[Dict[str, Any]]:
        """Count extracted keywords across currently liked papers.

        Mirrors aggregate_liked_preferences: pure SQL + Python counting over
        the reader's own marks — no model inference involved.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT d.score_json FROM paper_preferences p "
                "JOIN daily_papers d "
                "ON d.source = p.source AND d.paper_id = p.paper_id "
                "WHERE p.preference = 'like'"
            ).fetchall()
        counts: Dict[str, int] = {}
        for row in rows:
            try:
                score = json.loads(row["score_json"]) if row["score_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                score = {}
            if not isinstance(score, dict):
                continue
            for keyword in score.get("extracted_keywords") or []:
                if isinstance(keyword, str) and keyword.strip():
                    key = keyword.strip()
                    counts[key] = counts.get(key, 0) + 1
        ranked = [
            {"keyword": keyword, "count": count}
            for keyword, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        return ranked[: max(1, int(limit))]

    def count_pending_papers(self) -> Dict[str, int]:
        """Ordinary daily-queue depth, split by retry need.

        Pure SQL with no paper-source imports so the thin WebUI image can
        surface the normal daily backlog any time. Deferred past-date papers
        have their own backfill scope and must not make the current-day queue
        look actionable.
        """
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM daily_papers "
                "WHERE completed_at IS NULL AND queue_scope = 'daily'"
            ).fetchone()["n"] or 0
            failed = conn.execute(
                "SELECT COUNT(*) AS n FROM daily_papers "
                "WHERE completed_at IS NULL AND queue_scope = 'daily' "
                "AND (score_status = 'failed' OR translation_status = 'failed' "
                "OR analysis_status = 'failed')"
            ).fetchone()["n"] or 0
        return {"total": total, "failed_retry": failed, "fresh": total - failed}

    def list_delivered_papers(self, limit: int = 50) -> list[Dict[str, Any]]:
        """Recently completed papers (newest first) for preference marking."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source, paper_id, canonical_id, version, paper_json, "
                "completed_at FROM daily_papers "
                "WHERE completed_at IS NOT NULL "
                "ORDER BY completed_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        papers = []
        for row in rows:
            try:
                metadata = json.loads(row["paper_json"]) if row["paper_json"] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            papers.append(
                {
                    "source": row["source"],
                    "paper_id": row["paper_id"],
                    "canonical_id": row["canonical_id"],
                    "version": row["version"],
                    "title": str(metadata.get("title") or row["paper_id"]),
                    "authors": [
                        a for a in (metadata.get("authors") or []) if isinstance(a, str)
                    ],
                    "categories": [
                        c
                        for c in (metadata.get("categories") or [])
                        if isinstance(c, str)
                    ],
                    "completed_at": row["completed_at"],
                }
            )
        return papers

    @staticmethod
    def _like_pattern(query: str) -> str:
        """Escape LIKE wildcards so user input matches literally."""
        escaped = (
            query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        return f"%{escaped}%"

    def _entity_variants_with_conn(
        self, conn: sqlite3.Connection, entity_id: str
    ) -> list[Dict[str, Any]]:
        """Return every source-level view of one entity, including non-mergeable output."""
        rows = conn.execute(
            """
            SELECT dp.source, dp.paper_id, dp.canonical_id, dp.version,
                   dp.completed_at, dp.paper_json, dp.score_json, dp.abstract_cn,
                   dp.analysis_json, dp.score_status, dp.translation_status,
                   dp.analysis_status, dp.last_error,
                   deliveries.report_path, deliveries.report_at, deliveries.delivered_at,
                   (SELECT pp.preference FROM paper_preferences pp
                    WHERE pp.source = dp.source AND pp.paper_id = dp.paper_id) AS preference
            FROM daily_papers dp
            LEFT JOIN paper_deliveries deliveries
              ON deliveries.source = dp.source AND deliveries.paper_id = dp.paper_id
            WHERE dp.entity_id = ?
            ORDER BY COALESCE(dp.completed_at, '') DESC, dp.last_seen_at DESC,
                     dp.source, dp.paper_id
            """,
            (entity_id,),
        ).fetchall()
        variants: list[Dict[str, Any]] = []
        for row in rows:
            metadata = self._decode_json_object(row["paper_json"])
            score = self._decode_json_object(row["score_json"])
            analysis = self._decode_json_object(row["analysis_json"])
            variants.append(
                {
                    "source": row["source"],
                    "paper_id": row["paper_id"],
                    "canonical_id": row["canonical_id"],
                    "version": int(row["version"] or 0),
                    "completed_at": row["completed_at"],
                    "title": str(metadata.get("title") or row["paper_id"]),
                    "authors": self._dedupe_display_strings(metadata.get("authors") or []),
                    "abstract": str(metadata.get("abstract") or ""),
                    "abstract_cn": str(row["abstract_cn"] or ""),
                    "url": metadata.get("url"),
                    "pdf_url": metadata.get("pdf_url"),
                    "doi": metadata.get("doi"),
                    "arxiv_id": metadata.get("arxiv_id"),
                    "arxiv_url": metadata.get("arxiv_url"),
                    "categories": self._dedupe_display_strings(
                        metadata.get("categories") or []
                    ),
                    "published_date": metadata.get("published_date"),
                    "total_score": score.get("total_score"),
                    "is_qualified": score.get("is_qualified"),
                    "strategy_id": score.get("strategy_id"),
                    "tldr": score.get("tldr"),
                    "extracted_keywords": self._dedupe_display_strings(
                        score.get("extracted_keywords") or []
                    ),
                    "analysis": analysis,
                    "score_status": row["score_status"],
                    "translation_status": row["translation_status"],
                    "analysis_status": row["analysis_status"],
                    "last_error": row["last_error"],
                    "report_path": row["report_path"],
                    "report_at": row["report_at"],
                    "delivered_at": row["delivered_at"],
                    "preference": row["preference"],
                }
            )
        return variants

    def get_entity_variants(self, entity_id: str) -> list[Dict[str, Any]]:
        """Public read API for source-specific TL;DRs, translations and analyses."""
        self._ensure_paper_entity_coverage()
        with self._connect() as conn:
            return self._entity_variants_with_conn(conn, str(entity_id))

    def get_paper_entity(
        self, source: str, paper_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return the logical paper that owns a source-level record, if present."""
        self._ensure_paper_entity_coverage()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT entities.* FROM daily_papers papers
                JOIN paper_entities entities ON entities.entity_id = papers.entity_id
                WHERE papers.source = ? AND papers.paper_id = ?
                """,
                (str(source).strip().casefold(), paper_id),
            ).fetchone()
            if row is None:
                return None
            variants = self._entity_variants_with_conn(conn, row["entity_id"])
        return self._entity_search_item(row, variants)

    def _entity_search_item(
        self, row: sqlite3.Row, variants: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Shape one entity and all of its source variants for archive consumers."""
        representative = variants[0] if variants else {}
        sources = list(dict.fromkeys(item["source"] for item in variants))
        preferences = {item.get("preference") for item in variants}
        preference = "like" if "like" in preferences else (
            "dislike" if "dislike" in preferences else None
        )
        return {
            "entity_id": row["entity_id"],
            # Compatibility fields point to the newest source variant. New
            # callers should use ``sources``/``variants`` instead of treating
            # this representative as a destructive merge.
            "source": representative.get("source"),
            "sources": sources,
            "source_count": len(sources),
            "paper_id": representative.get("paper_id"),
            "canonical_id": representative.get("canonical_id"),
            "version": representative.get("version"),
            "completed_at": row["completed_at"] or representative.get("completed_at"),
            "title": str(row["title"] or representative.get("title") or "—"),
            "authors": self._decode_json_strings(row["authors_json"]),
            "abstract": str(row["abstract"] or representative.get("abstract") or ""),
            "doi": row["doi"],
            "arxiv_canonical_id": row["arxiv_canonical_id"],
            "arxiv_version": row["arxiv_version"],
            "categories": self._decode_json_strings(row["categories_json"]),
            "merged_keywords": self._decode_json_strings(row["merged_keywords_json"]),
            "url": representative.get("url"),
            "pdf_url": representative.get("pdf_url"),
            "published_date": representative.get("published_date"),
            "total_score": representative.get("total_score"),
            "is_qualified": representative.get("is_qualified"),
            "strategy_id": representative.get("strategy_id"),
            "tldr": representative.get("tldr"),
            "extracted_keywords": self._decode_json_strings(
                row["merged_keywords_json"]
            ),
            "preference": preference,
            "variants": variants,
        }

    def search_papers(
        self,
        *,
        query: str = "",
        source: Optional[str] = None,
        liked_only: bool = False,
        min_score: Optional[float] = None,
        completed_from: Optional[str] = None,
        completed_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Search logical papers while keeping each source's output inspectable.

        The outer query paginates one ``paper_entities`` row per logical
        paper.  Source, date and score filters are evaluated against at least
        one completed source record; text search spans all variants.  This
        prevents a DOI/arXiv mirror pair from occupying two archive rows while
        retaining both independent TL;DRs, translations and analyses.
        """
        self._ensure_paper_entity_coverage()
        conditions: list[str] = []
        variant_conditions = ["dp.completed_at IS NOT NULL"]
        variant_params: list[Any] = []
        normalized_source = (source or "").strip().casefold()
        if normalized_source:
            variant_conditions.append("dp.source = ?")
            variant_params.append(normalized_source)
        if min_score is not None:
            variant_conditions.append("json_extract(dp.score_json, '$.total_score') >= ?")
            variant_params.append(float(min_score))
        if completed_from:
            variant_conditions.append("substr(dp.completed_at, 1, 10) >= ?")
            variant_params.append(str(completed_from))
        if completed_to:
            variant_conditions.append("substr(dp.completed_at, 1, 10) <= ?")
            variant_params.append(str(completed_to))
        conditions.append(
            "EXISTS (SELECT 1 FROM daily_papers dp WHERE dp.entity_id = pe.entity_id AND "
            + " AND ".join(variant_conditions)
            + ")"
        )
        params: list[Any] = [*variant_params]

        stripped = (query or "").strip()
        if stripped:
            pattern = self._like_pattern(stripped)
            conditions.append(
                """EXISTS (
                    SELECT 1 FROM daily_papers dp
                    WHERE dp.entity_id = pe.entity_id
                      AND (dp.paper_json LIKE ? ESCAPE '\\'
                           OR dp.score_json LIKE ? ESCAPE '\\')
                )"""
            )
            params.extend([pattern, pattern])
        if liked_only:
            conditions.append(
                """EXISTS (
                    SELECT 1 FROM paper_preferences pp
                    JOIN daily_papers dp
                      ON dp.source = pp.source AND dp.paper_id = pp.paper_id
                    WHERE dp.entity_id = pe.entity_id AND pp.preference = 'like'
                )"""
            )

        where_clause = " AND ".join(conditions)
        bounded_limit = max(1, min(int(limit), 200))
        bounded_offset = max(0, int(offset))
        # The common initial archive view has no source/date/text/preference
        # filter.  ``paper_entities.completed_at`` is the derived maximum of
        # its completed variants and is rebuilt whenever a paper record is
        # registered, merged or migrated.  Querying that canonical logical
        # layer directly is therefore equivalent to the general EXISTS
        # predicate, while the completed-time index avoids thousands of
        # correlated SQLite probes on a populated history database.
        unfiltered_completed_view = not any(
            (
                normalized_source,
                min_score is not None,
                completed_from,
                completed_to,
                stripped,
                liked_only,
            )
        )
        with self._connect() as conn:
            if unfiltered_completed_view:
                total_row = conn.execute(
                    "SELECT COUNT(*) FROM paper_entities WHERE completed_at IS NOT NULL"
                ).fetchone()
                rows = conn.execute(
                    """
                    SELECT pe.* FROM paper_entities pe
                    WHERE pe.completed_at IS NOT NULL
                    ORDER BY pe.completed_at DESC, pe.last_seen_at DESC, pe.entity_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (bounded_limit, bounded_offset),
                ).fetchall()
            else:
                total_row = conn.execute(
                    f"SELECT COUNT(*) FROM paper_entities pe WHERE {where_clause}", params
                ).fetchone()
                rows = conn.execute(
                    f"""
                    SELECT pe.* FROM paper_entities pe
                    WHERE {where_clause}
                    ORDER BY COALESCE(pe.completed_at, pe.last_seen_at) DESC, pe.entity_id DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, bounded_limit, bounded_offset],
                ).fetchall()
            items = [
                self._entity_search_item(
                    row, self._entity_variants_with_conn(conn, row["entity_id"])
                )
                for row in rows
            ]
        return {"total": int(total_row[0]), "items": items}

    def _source_health_entries(
        self, *, days: Optional[int] = None
    ) -> list[Dict[str, Any]]:
        """Read durable logical source requests for an optional calendar window."""
        if days is not None and (
            isinstance(days, bool) or not isinstance(days, int) or days < 1
        ):
            raise ValueError("数据源健康查看天数必须是正整数或 None")
        query = (
            "SELECT source, status, candidate_count, error_summary, occurred_at, task_kind "
            "FROM source_health_events"
        )
        params: list[Any] = []
        if days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            query += " WHERE occurred_at >= ?"
            params.append(cutoff)
        query += " ORDER BY occurred_at DESC, event_id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                "source": self._normalized_source_health_value(row["source"]),
                "status": row["status"],
                "candidate_count": self._normalized_source_health_count(
                    row["candidate_count"]
                ),
                "error_summary": self._sanitize_source_health_error(
                    row["error_summary"]
                ),
                "occurred_at": row["occurred_at"],
                "task_kind": self._normalized_task_kind_value(row["task_kind"]),
            }
            for row in rows
            if row["status"] in {"succeeded", "failed"}
        ]

    @staticmethod
    def _summarize_source_health_entries(
        entries: list[Dict[str, Any]], *, per_source_limit: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate chronological event rows into one health row per source."""
        per_source: Dict[str, list[Dict[str, Any]]] = {}
        for entry in entries:
            per_source.setdefault(str(entry["source"]), []).append(entry)

        summaries: Dict[str, Dict[str, Any]] = {}
        for source, source_entries in per_source.items():
            window_entries = (
                source_entries[:per_source_limit]
                if per_source_limit is not None
                else source_entries
            )
            if not window_entries:
                continue
            newest = window_entries[0]
            succeeded = sum(
                1 for entry in window_entries if entry["status"] == "succeeded"
            )
            newest_success = next(
                (entry for entry in window_entries if entry["status"] == "succeeded"),
                None,
            )
            newest_failure = next(
                (entry for entry in window_entries if entry["status"] == "failed"),
                None,
            )
            summaries[source] = {
                "last_status": newest["status"],
                "last_scan_at": newest["occurred_at"],
                "last_task_kind": newest["task_kind"],
                "scans_in_window": len(window_entries),
                "succeeded_in_window": succeeded,
                "success_rate": succeeded / len(window_entries),
                "last_new_candidates": (
                    newest_success["candidate_count"] if newest_success else None
                ),
                "last_error": (
                    newest_failure["error_summary"] if newest_failure else None
                ),
                "last_error_at": (
                    newest_failure["occurred_at"] if newest_failure else None
                ),
                "last_error_task_kind": (
                    newest_failure["task_kind"] if newest_failure else None
                ),
            }
        return summaries

    def get_source_health(self, window: int = 20) -> Dict[str, Dict[str, Any]]:
        """Compatibility view using the newest ``window`` events per source."""
        bounded_window = max(1, min(int(window), 100))
        return self._summarize_source_health_entries(
            self._source_health_entries(), per_source_limit=bounded_window
        )

    def get_source_health_for_days(
        self, days: Optional[int]
    ) -> Dict[str, Dict[str, Any]]:
        """Aggregate every logical source request in the selected day window.

        ``None`` intentionally means the complete local event history.  This
        is separate from ``get_source_health(window=...)`` so old integrations
        retain their request-count semantics.
        """
        return self._summarize_source_health_entries(
            self._source_health_entries(days=days)
        )

    @staticmethod
    def _extract_receipt_error(receipt: Dict[str, Any]) -> Optional[str]:
        error = receipt.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        for item in receipt.get("domain_receipts") or []:
            if not isinstance(item, dict):
                continue
            domain_error = item.get("error")
            if isinstance(domain_error, str) and domain_error.strip():
                label = item.get("domain") or item.get("label") or ""
                return f"{label}: {domain_error.strip()}".lstrip(": ")
        return None

    def get_recent_operational_runs(
        self, limit: Optional[int] = 5, *, days: Optional[int] = None
    ) -> list[Dict[str, Any]]:
        """Return the latest daily or past-date reports for operator diagnosis.

        Historical import, repair, omission and supplement workflows own their
        progress in History Maintenance.  Keeping them out here makes the
        System diagnostics view answer one concise question: whether the
        normal daily schedule and explicitly queued past-date reports worked.
        ``days=None`` covers all local history; ``limit=None`` intentionally
        leaves result paging to the WebUI rather than discarding older rows.
        """
        if days is not None and (
            isinstance(days, bool) or not isinstance(days, int) or days < 1
        ):
            raise ValueError("运行诊断查看天数必须是正整数或 None")
        bounded_limit = (
            max(1, min(int(limit), 1_000)) if limit is not None else None
        )
        visible_kinds = ("daily", "daily_research", "backfill", "backfill_run")
        placeholders = ", ".join("?" for _ in visible_kinds)
        conditions = [f"run_kind IN ({placeholders})"]
        params: list[Any] = list(visible_kinds)
        if days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            conditions.append("started_at >= ?")
            params.append(cutoff)
        limit_clause = ""
        if bounded_limit is not None:
            limit_clause = " LIMIT ?"
            params.append(bounded_limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT run_id, run_kind, started_at, completed_at, status,
                       total_papers, error
                FROM daily_runs
                WHERE {' AND '.join(conditions)}
                ORDER BY started_at DESC, run_id DESC
                {limit_clause}
                """,
                params,
            ).fetchall()
        return [
            {
                "run_id": str(row["run_id"]),
                "run_kind": str(row["run_kind"] or "daily"),
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "status": str(row["status"] or "unknown"),
                "total_papers": self._normalized_source_health_count(
                    row["total_papers"]
                )
                or 0,
                "error_summary": self._sanitize_source_health_error(row["error"]),
            }
            for row in rows
        ]

    def get_recent_runs(self, limit: int = 20) -> list[Dict[str, Any]]:
        """Return recent run summaries plus receipts for local observability."""
        max_rows = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, started_at, scan_started_at, scan_days,
                       scanned_sources_json, completed_at, status, total_papers,
                       error, report_paths_json
                FROM daily_runs
                ORDER BY started_at DESC, run_id DESC
                LIMIT ?
                """,
                (max_rows,),
            ).fetchall()
        runs = []
        for row in rows:
            try:
                sources = json.loads(row["scanned_sources_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                sources = []
            runs.append(
                {
                    "run_id": row["run_id"],
                    "started_at": row["started_at"],
                    "scan_started_at": row["scan_started_at"],
                    "scan_days": row["scan_days"],
                    "scanned_sources": sources if isinstance(sources, list) else [],
                    "completed_at": row["completed_at"],
                    "status": row["status"],
                    "total_papers": int(row["total_papers"] or 0),
                    "error": row["error"],
                    "receipts": self.get_scan_receipts(row["run_id"]),
                }
            )
        return runs

    def set_run_total(self, run_id: str, total_papers: int):
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE daily_runs SET total_papers = ? WHERE run_id = ?",
                (total_papers, run_id),
            )

    def complete_run(self, run_id: str, report_paths: Optional[Dict[str, Any]] = None):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] == "completed":
                return
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能完成 running 运行: {run_id} ({run['status']})"
                )
            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = ?, report_paths_json = ?
                WHERE run_id = ?
                """,
                (
                    now,
                    "completed",
                    json.dumps(report_paths or {}, ensure_ascii=False),
                    run_id,
                ),
            )
            self._advance_scan_watermarks(conn, run_id, now)
            self._clear_run_phase(conn, run_id)

    def finalize_report_delivery(
        self,
        run_id: str,
        report_paths: Dict[str, Any],
        delivered_papers_by_source: Dict[str, list[Dict[str, Any]]],
        notification_entries: Optional[list[Dict[str, Any]]] = None,
        maintenance_entries: Optional[list[Dict[str, Any]]] = None,
        report_at: Optional[datetime] = None,
    ) -> None:
        """Atomically record report delivery and all follow-up outbox rows.

        A report is considered delivered only after every included paper has
        completed its required analysis.  The same transaction records paper
        delivery, completes the run, queues one notification per channel, and
        queues maintenance work such as the post-report WebDAV upload.  This
        removes crash windows where a paper was hidden from future scans but a
        required follow-up task had not yet been persisted.
        """
        now = datetime.now().isoformat()
        report_timestamp = (report_at or datetime.now()).isoformat()
        entries = notification_entries or []
        maintenance = maintenance_entries or []
        normalized_paths = {key: str(value) for key, value in report_paths.items()}

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT run_id, status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能交付 running 运行: {run_id} ({run['status']})"
                )

            for source, papers in delivered_papers_by_source.items():
                report_path = normalized_paths.get(source) or normalized_paths.get(
                    f"{source}_html"
                )
                for paper_info in papers:
                    paper = paper_info.get("paper_metadata")
                    paper_id = paper_info.get("paper_id")
                    if paper is None or not paper_id:
                        raise ValueError(f"无法记录缺少元数据的日报论文: {source}:{paper_id}")

                    record = conn.execute(
                        """
                        SELECT score_status, tldr_status, translation_status, analysis_status,
                               score_json, abstract_cn, paper_json
                        FROM daily_papers
                        WHERE source = ? AND paper_id = ?
                        """,
                        (source, paper_id),
                    ).fetchone()
                    if record is None:
                        raise RuntimeError(f"日报论文尚未持久化: {source}:{paper_id}")

                    if record["score_status"] != "succeeded" or not record["score_json"]:
                        raise RuntimeError(f"评分尚未完成，不能交付日报: {source}:{paper_id}")
                    if record["tldr_status"] != "succeeded":
                        raise RuntimeError(f"TL;DR 尚未完成，不能交付日报: {source}:{paper_id}")
                    if paper.abstract and paper.abstract.strip() and (
                        record["translation_status"] != "succeeded"
                        or not (record["abstract_cn"] or "").strip()
                    ):
                        raise RuntimeError(f"摘要翻译尚未完成，不能交付日报: {source}:{paper_id}")

                    requires_analysis = bool(
                        paper_info.get("requires_analysis", False)
                    )
                    if requires_analysis and record["analysis_status"] != "succeeded":
                        raise RuntimeError(
                            f"深度分析尚未完成，不能交付日报: {source}:{paper_id}"
                        )

                    if not requires_analysis:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET analysis_status = 'not_required'
                            WHERE source = ? AND paper_id = ?
                              AND analysis_status != 'succeeded'
                            """,
                            (source, paper_id),
                        )

                    existing_delivery = conn.execute(
                        """
                        SELECT run_id FROM paper_deliveries
                        WHERE source = ? AND canonical_id = ? AND version = ?
                        """,
                        (
                            source,
                            paper.canonical_id or paper.paper_id,
                            paper.version or 0,
                        ),
                    ).fetchone()
                    if existing_delivery is not None and existing_delivery["run_id"] != run_id:
                        raise RuntimeError(
                            "该论文版本已由另一日报交付，拒绝重复提交: "
                            f"{source}:{paper.canonical_id or paper.paper_id}v{paper.version or 0}"
                        )

                    conn.execute(
                        """
                        INSERT INTO paper_deliveries(
                            run_id, source, paper_id, canonical_id, version,
                            report_path, report_at, delivered_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, canonical_id, version) DO NOTHING
                        """,
                        (
                            run_id,
                            source,
                            paper_id,
                            paper.canonical_id or paper.paper_id,
                            paper.version or 0,
                            report_path,
                            report_timestamp,
                            now,
                        ),
                    )
                    conn.execute(
                        """
                        UPDATE daily_papers
                        SET run_id = ?, completed_at = COALESCE(completed_at, ?), last_error = NULL
                        WHERE source = ? AND paper_id = ?
                        """,
                        (run_id, now, source, paper_id),
                    )
                    self._sync_paper_entity_for_record(conn, source, paper_id)

            for entry in entries:
                try:
                    event_type = entry["event_type"]
                    channel = entry["channel"]
                    payload = entry["payload"]
                except KeyError as exc:
                    raise ValueError(f"无效通知 outbox 条目: {entry!r}") from exc
                conn.execute(
                    """
                    INSERT INTO notification_outbox(
                        run_id, event_type, channel, payload_json, status,
                        attempt_count, next_attempt_at, created_at
                    )
                    VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(run_id, event_type, channel) DO NOTHING
                    """,
                    (
                        run_id,
                        event_type,
                        channel,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

            for entry in maintenance:
                try:
                    task_key = entry["task_key"]
                    payload = entry["payload"]
                except KeyError as exc:
                    raise ValueError(f"无效维护 outbox 条目: {entry!r}") from exc
                if not isinstance(task_key, str) or not task_key.strip():
                    raise ValueError(f"维护 outbox task_key 无效: {task_key!r}")
                conn.execute(
                    """
                    INSERT INTO maintenance_outbox(
                        task_key, payload_json, status, attempt_count,
                        next_attempt_at, created_at
                    )
                    VALUES (?, ?, 'pending', 0, ?, ?)
                    ON CONFLICT(task_key) DO NOTHING
                    """,
                    (task_key, json.dumps(payload, ensure_ascii=False), now, now),
                )

            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = 'completed', error = NULL, report_paths_json = ?
                WHERE run_id = ?
                """,
                (now, json.dumps(normalized_paths, ensure_ascii=False), run_id),
            )
            self._advance_scan_watermarks(conn, run_id, now)
            self._clear_run_phase(conn, run_id)

    def fail_run(self, run_id: str, error: str):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            # A completed report is authoritative.  A late provider or cleanup
            # exception must not reopen it as a failed/retryable run.  Repeated
            # failure calls are also idempotent and preserve the first error.
            if run["status"] in {"completed", "failed"}:
                return
            if run["status"] != "running":
                raise RuntimeError(
                    f"只能失败 running 运行: {run_id} ({run['status']})"
                )
            conn.execute(
                """
                UPDATE daily_runs
                SET completed_at = ?, status = ?, error = ?
                WHERE run_id = ?
                """,
                (now, "failed", error[:4000], run_id),
            )
            self._clear_run_phase(conn, run_id)

    # ------------------------------------------------------------------
    # Notification outbox
    # ------------------------------------------------------------------

    def enqueue_notification(
        self,
        run_id: str,
        event_type: str,
        channel: str,
        payload: Dict[str, Any],
    ) -> bool:
        """Persist one channel delivery request without overwriting an existing one.

        The unique key provides idempotence when the process restarts between
        report completion and notification dispatch.
        """
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO notification_outbox(
                    run_id, event_type, channel, payload_json, status,
                    attempt_count, next_attempt_at, created_at
                )
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(run_id, event_type, channel) DO NOTHING
                """,
                (
                    run_id,
                    event_type,
                    channel,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def claim_due_notifications(
        self,
        event_type: Optional[str] = None,
        limit: int = 100,
        stale_claim_seconds: int = 900,
    ) -> list[sqlite3.Row]:
        """Claim due outbox rows for one sender process.

        Claims protect against duplicate concurrent delivery.  A process crash can
        leave a row in ``sending``; the next run safely recovers an old claim.
        External notification protocols cannot guarantee exactly-once delivery
        across a crash after the remote side accepted a request, so this gives
        durable at-least-once delivery with a visible attempt history.
        """
        now_dt = datetime.now()
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, stale_claim_seconds))).isoformat()
        max_rows = max(1, int(limit))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?
                WHERE status = 'sending' AND claimed_at IS NOT NULL AND claimed_at <= ?
                """,
                (now, stale_before),
            )

            clauses = ["status = 'pending'", "next_attempt_at <= ?"]
            params: list[Any] = [now]
            if event_type is not None:
                clauses.append("event_type = ?")
                params.append(event_type)
            query = (
                "SELECT outbox_id FROM notification_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, outbox_id ASC LIMIT ?"
            )
            params.append(max_rows)
            outbox_ids = [row["outbox_id"] for row in conn.execute(query, params).fetchall()]
            claimed = []
            for outbox_id in outbox_ids:
                cursor = conn.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'sending', claimed_at = ?, attempt_count = attempt_count + 1
                    WHERE outbox_id = ? AND status = 'pending'
                    """,
                    (now, outbox_id),
                )
                if cursor.rowcount:
                    claimed.append(
                        conn.execute(
                            "SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
                        ).fetchone()
                    )
            return claimed

    def increment_notification_attempt(self, outbox_id: int) -> int:
        """Record an additional immediate delivery attempt for a claimed row."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = attempt_count + 1, claimed_at = ?
                WHERE outbox_id = ? AND status = 'sending'
                """,
                (now, outbox_id),
            )
            row = conn.execute(
                "SELECT attempt_count FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"notification outbox row does not exist: {outbox_id}")
            return row["attempt_count"]

    def mark_notification_sent(self, outbox_id: int) -> None:
        """Finalize a successful external notification delivery."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', sent_at = ?, claimed_at = NULL,
                    last_error = NULL
                WHERE outbox_id = ?
                """,
                (now, outbox_id),
            )

    def reschedule_notification(
        self, outbox_id: int, error: str, retry_after_seconds: int
    ) -> None:
        """Release a failed claim for a later retry while retaining its payload."""
        now_dt = datetime.now()
        next_attempt = (now_dt + timedelta(seconds=max(1, retry_after_seconds))).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?, last_error = ?
                WHERE outbox_id = ?
                """,
                (next_attempt, error[:4000], outbox_id),
            )

    def get_notification_outbox(self, outbox_id: int) -> Optional[sqlite3.Row]:
        """Return an outbox row for diagnostics and tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()

    def get_pending_notification_count(self, event_type: Optional[str] = None) -> int:
        """Return the number of notification rows that still need delivery."""
        with self._connect() as conn:
            if event_type is None:
                row = conn.execute(
                    "SELECT count(*) AS count FROM notification_outbox WHERE status != 'sent'"
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT count(*) AS count FROM notification_outbox
                    WHERE status != 'sent' AND event_type = ?
                    """,
                    (event_type,),
                ).fetchone()
            return int(row["count"])

    # ------------------------------------------------------------------
    # Durable post-report maintenance tasks (currently WebDAV upload)
    # ------------------------------------------------------------------

    def enqueue_maintenance_task(self, task_key: str, payload: Dict[str, Any]) -> bool:
        """Persist an idempotent post-report task for restart-safe execution."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO maintenance_outbox(
                    task_key, payload_json, status, attempt_count,
                    next_attempt_at, created_at
                )
                VALUES (?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(task_key) DO NOTHING
                """,
                (task_key, json.dumps(payload, ensure_ascii=False), now, now),
            )
            return cursor.rowcount == 1

    def claim_due_maintenance_tasks(
        self, prefix: Optional[str] = None, limit: int = 20, stale_claim_seconds: int = 900
    ) -> list[sqlite3.Row]:
        """Claim due maintenance tasks; stale in-progress claims are recovered."""
        now_dt = datetime.now()
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=max(1, stale_claim_seconds))).isoformat()
        max_rows = max(1, int(limit))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?
                WHERE status = 'running' AND claimed_at IS NOT NULL AND claimed_at <= ?
                """,
                (now, stale_before),
            )
            clauses = ["status = 'pending'", "next_attempt_at <= ?"]
            params: list[Any] = [now]
            if prefix is not None:
                clauses.append("task_key LIKE ?")
                params.append(f"{prefix}%")
            params.append(max_rows)
            query = (
                "SELECT task_key FROM maintenance_outbox WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at ASC, task_key ASC LIMIT ?"
            )
            task_keys = [row["task_key"] for row in conn.execute(query, params).fetchall()]
            claimed = []
            for task_key in task_keys:
                cursor = conn.execute(
                    """
                    UPDATE maintenance_outbox
                    SET status = 'running', claimed_at = ?, attempt_count = attempt_count + 1
                    WHERE task_key = ? AND status = 'pending'
                    """,
                    (now, task_key),
                )
                if cursor.rowcount:
                    claimed.append(
                        conn.execute(
                            "SELECT * FROM maintenance_outbox WHERE task_key = ?", (task_key,)
                        ).fetchone()
                    )
            return claimed

    def mark_maintenance_task_completed(self, task_key: str) -> None:
        """Mark a claimed maintenance task complete."""
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'completed', completed_at = ?, claimed_at = NULL, last_error = NULL
                WHERE task_key = ?
                """,
                (now, task_key),
            )

    def reschedule_maintenance_task(
        self, task_key: str, error: str, retry_after_seconds: int
    ) -> None:
        """Preserve a failed maintenance task for a later attempt."""
        next_attempt = (
            datetime.now() + timedelta(seconds=max(1, retry_after_seconds))
        ).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE maintenance_outbox
                SET status = 'pending', claimed_at = NULL, next_attempt_at = ?, last_error = ?
                WHERE task_key = ?
                """,
                (next_attempt, error[:4000], task_key),
            )

    def get_maintenance_task(self, task_key: str) -> Optional[sqlite3.Row]:
        """Return maintenance task state for diagnostics/tests."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM maintenance_outbox WHERE task_key = ?", (task_key,)
            ).fetchone()

    def get_paper_record(self, source: str, paper_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()

    def is_paper_delivered(self, source: str, paper_id: str) -> bool:
        """Return whether this exact paper version has entered a completed daily report."""
        from sources.base_source import paper_identity

        canonical_id, version = paper_identity(source, paper_id)
        normalized_version = version if version is not None else 0
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = ? AND canonical_id = ? AND version = ?
                LIMIT 1
                """,
                (source, canonical_id, normalized_version),
            ).fetchone()
            if row is not None:
                return True
            # Backward-compatible fallback for pre-delivery-table databases.
            row = conn.execute(
                """
                SELECT 1 FROM daily_papers
                WHERE source = ? AND paper_id = ? AND completed_at IS NOT NULL
                LIMIT 1
                """,
                (source, paper_id),
            ).fetchone()
            return row is not None

    def is_paper_delivered_strict(self, source: str, paper_id: str) -> bool:
        """Ledger-only delivery check without the ``completed_at`` fallback.

        Supplement candidates may carry a historical ``completed_at`` from the
        legacy import while still needing one real delivery; only an exact
        ledger row counts as delivered for them.
        """
        from sources.base_source import paper_identity

        canonical_id, version = paper_identity(source, paper_id)
        normalized_version = version if version is not None else 0
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = ? AND canonical_id = ? AND version = ?
                LIMIT 1
                """,
                (source, canonical_id, normalized_version),
            ).fetchone()
            return row is not None

    def has_delivered_arxiv_canonical(self, canonical_id: str) -> bool:
        """Return whether any delivered arXiv version exists for a canonical ID.

        This intentionally answers a broader question than
        :meth:`is_paper_delivered`: a late-arriving supplemental mirror should
        not create a second report merely because its upstream feed omitted an
        arXiv ``vN`` suffix.  It is used only to suppress the mirror, never to
        suppress an arXiv revision itself.
        """
        value = str(canonical_id or "").strip()
        if not value:
            return False
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM paper_deliveries
                WHERE source = 'arxiv' AND canonical_id = ?
                LIMIT 1
                """,
                (value,),
            ).fetchone()
            if row is not None:
                return True
            # Backward-compatible fallback for databases before the delivery
            # ledger.  Exact canonical identity still prevents false matches.
            row = conn.execute(
                """
                SELECT 1 FROM daily_papers
                WHERE source = 'arxiv' AND canonical_id = ?
                  AND completed_at IS NOT NULL
                LIMIT 1
                """,
                (value,),
            ).fetchone()
            return row is not None

    @staticmethod
    def _analysis_json(analysis: Any) -> str:
        if hasattr(analysis, "model_dump"):
            payload = analysis.model_dump(mode="json")
        elif isinstance(analysis, dict):
            payload = analysis
        else:
            payload = dict(analysis)
        return json.dumps(payload, ensure_ascii=False)

    def get_previous_version_record(
        self, source: str, paper: "PaperMetadata"
    ) -> Optional[sqlite3.Row]:
        """Return the latest completed earlier version of an arXiv paper."""
        if getattr(paper, "version", None) is None:
            return None
        with self._connect() as conn:
            delivered = conn.execute(
                """
                SELECT daily_papers.*, paper_deliveries.delivered_at
                FROM paper_deliveries
                JOIN daily_papers
                  ON daily_papers.source = paper_deliveries.source
                 AND daily_papers.paper_id = paper_deliveries.paper_id
                WHERE paper_deliveries.source = ?
                  AND paper_deliveries.canonical_id = ?
                  AND paper_deliveries.version < ?
                ORDER BY paper_deliveries.version DESC, paper_deliveries.delivered_at DESC
                LIMIT 1
                """,
                (source, paper.canonical_id, paper.version),
            ).fetchone()
            if delivered is not None:
                return delivered
            # Fallback for databases produced before paper_deliveries existed.
            return conn.execute(
                """
                SELECT * FROM daily_papers
                WHERE source = ? AND canonical_id = ? AND version < ?
                  AND completed_at IS NOT NULL
                ORDER BY version DESC, completed_at DESC
                LIMIT 1
                """,
                (source, paper.canonical_id, paper.version),
            ).fetchone()

    def get_version_records(self, source: str, canonical_id: str) -> list[sqlite3.Row]:
        """Return all persisted versions for one canonical paper."""
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM daily_papers
                WHERE source = ? AND canonical_id = ?
                ORDER BY version ASC, first_seen_at ASC
                """,
                (source, canonical_id),
            ).fetchall()

    @classmethod
    def _restore_optional_enrichment(
        cls, paper: "PaperMetadata", persisted_paper_json: Optional[str]
    ) -> None:
        """Fill absent best-effort enrichment fields from a prior attempt.

        Semantic Scholar is intentionally non-blocking for a daily run.  A
        transient 429/network failure on a retry must therefore not erase a
        TLDR or arXiv PDF URL that was already obtained and persisted for the
        exact same paper identity.  Invalid legacy JSON is ignored so it
        cannot turn a recovery attempt into a failed report.
        """
        if not persisted_paper_json:
            return
        try:
            persisted = json.loads(persisted_paper_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return
        if not isinstance(persisted, dict):
            return

        for field in cls._OPTIONAL_ENRICHMENT_FIELDS:
            current_value = getattr(paper, field, None)
            persisted_value = persisted.get(field)
            if not current_value and persisted_value:
                setattr(paper, field, persisted_value)

    @classmethod
    def restore_optional_enrichment_from_record(
        cls, paper: "PaperMetadata", record: Optional[sqlite3.Row]
    ) -> None:
        """Hydrate best-effort fields from an already loaded paper record.

        The daily worker needs these fields before it computes the stage input
        fingerprints.  Keeping this as a read-only helper avoids a preliminary
        ``upsert_paper_seen`` transaction solely to get the same hydration,
        while ``upsert_paper_seen`` still performs the restoration itself for
        all other callers.
        """
        if record is None:
            return
        try:
            persisted_paper_json = record["paper_json"]
        except (IndexError, KeyError, TypeError):
            return
        cls._restore_optional_enrichment(paper, persisted_paper_json)

    def register_paper_candidates(
        self,
        run_id: str,
        papers_by_source: Dict[str, list["PaperMetadata"]],
        *,
        queue_scope: str = "daily",
        backfill_target_date: Optional[date | str] = None,
    ) -> int:
        """Durably register every newly discovered candidate before limiting work.

        A per-run processing limit must never truncate an upstream scan.  All
        exact source/version candidates enter ``daily_papers`` first; papers
        not selected in this run remain pending and are eligible next time even
        after the successful scan watermark advances. ``backfill`` rows are
        deliberately excluded from the ordinary daily selector: a current
        report must never consume deferred papers from a past-date report.
        """
        normalized_scope = str(queue_scope or "").strip().lower()
        if normalized_scope not in {"daily", "backfill"}:
            raise ValueError("candidate queue scope must be 'daily' or 'backfill'")
        target_date_text = None
        if normalized_scope == "backfill":
            if backfill_target_date is None:
                raise ValueError("backfill candidates require a target date")
            target_date_text = self._normalize_backfill_date(
                backfill_target_date
            ).isoformat()
        elif backfill_target_date is not None:
            raise ValueError("daily candidates cannot have a backfill target date")
        registered = 0
        seen_identities = set()
        candidates = []
        for source, papers in papers_by_source.items():
            normalized_source = str(source or "").strip().lower()
            if not normalized_source:
                raise ValueError("candidate source must be a non-empty string")
            for paper in papers:
                if paper.source != normalized_source:
                    raise ValueError(
                        "candidate source mismatch: "
                        f"group={normalized_source}, metadata={paper.source}"
                    )
                identity = (
                    normalized_source,
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                )
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                candidates.append((normalized_source, paper))

        with self._lock, self._connect() as conn:
            run = conn.execute(
                "SELECT status FROM daily_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(f"daily run does not exist: {run_id}")
            if run["status"] != "running":
                raise RuntimeError(f"只能向 running 运行登记候选论文: {run_id}")

            for normalized_source, paper in candidates:
                candidate_seen_at = datetime.now().isoformat()
                existing = conn.execute(
                    "SELECT paper_json FROM daily_papers WHERE source = ? AND paper_id = ?",
                    (normalized_source, paper.paper_id),
                ).fetchone()
                if existing is not None:
                    self._restore_optional_enrichment(paper, existing["paper_json"])
                conn.execute(
                    """
                    INSERT INTO daily_papers(
                        source, paper_id, canonical_id, version,
                        first_seen_at, last_seen_at, run_id, queue_scope,
                        backfill_target_date, paper_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, paper_id) DO UPDATE SET
                        canonical_id = excluded.canonical_id,
                        version = excluded.version,
                        last_seen_at = excluded.last_seen_at,
                        queue_scope = CASE
                            WHEN excluded.queue_scope = 'backfill' THEN 'backfill'
                            ELSE daily_papers.queue_scope
                        END,
                        backfill_target_date = CASE
                            WHEN excluded.queue_scope = 'backfill'
                                THEN COALESCE(
                                    daily_papers.backfill_target_date,
                                    excluded.backfill_target_date
                                )
                            ELSE daily_papers.backfill_target_date
                        END,
                        paper_json = excluded.paper_json
                    """,
                    (
                        normalized_source,
                        paper.paper_id,
                        paper.canonical_id or paper.paper_id,
                        paper.version or 0,
                        candidate_seen_at,
                        candidate_seen_at,
                        run_id,
                        normalized_scope,
                        target_date_text,
                        json.dumps(paper.to_dict(), ensure_ascii=False),
                    ),
                )
                self._sync_paper_entity_for_record(
                    conn, normalized_source, paper.paper_id
                )
                registered += 1
        return registered

    @staticmethod
    def _pending_row_sort_key(row: sqlite3.Row, paper: "PaperMetadata") -> tuple:
        failed_or_retried = bool(row["retry_count"]) or any(
            row[field] == "failed"
            for field in ("score_status", "translation_status", "analysis_status")
        )
        first_seen = DailyResearchStore._parse_checkpoint_timestamp(row["first_seen_at"])
        first_seen_key = first_seen.timestamp() if first_seen is not None else float("inf")
        published = paper.published_date
        if published.tzinfo is None:
            published = published.astimezone()
        published_key = published.timestamp()
        return (
            0 if failed_or_retried else 1,
            first_seen_key,
            published_key,
            row["source"],
            row["canonical_id"],
            int(row["version"] or 0),
            row["paper_id"],
        )

    def select_pending_papers(
        self,
        enabled_sources: list[str],
        limit: int = 0,
        *,
        queue_scope: str = "daily",
        backfill_target_date: Optional[date | str] = None,
    ) -> tuple[Dict[str, list["PaperMetadata"]], int]:
        """Return the deterministic pending queue and its total size.

        ``limit == 0`` means all pending papers.  Failed/retried records are
        attempted first, followed by older queued records and publication time.
        Only currently enabled report sources are selected; disabling a source
        preserves its backlog without processing it unexpectedly. The default
        is the ordinary daily queue; historical backfill candidates are only
        processed by an explicit past-date run.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("daily paper limit must be a non-negative integer")
        normalized_scope = str(queue_scope or "").strip().lower()
        if normalized_scope not in {"daily", "backfill"}:
            raise ValueError("pending queue scope must be 'daily' or 'backfill'")
        target_date_text = None
        if backfill_target_date is not None:
            if normalized_scope != "backfill":
                raise ValueError("only backfill queues can filter by target date")
            target_date_text = self._normalize_backfill_date(
                backfill_target_date
            ).isoformat()
        sources = sorted(
            {str(source).strip().lower() for source in enabled_sources if str(source).strip()}
        )
        if not sources:
            return {}, 0
        placeholders = ", ".join("?" for _ in sources)
        query = (
            """
                SELECT daily_papers.*
                FROM daily_papers
                WHERE daily_papers.source IN ("""
                + placeholders
                + """)
                  AND daily_papers.completed_at IS NULL
                  AND daily_papers.queue_scope = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM paper_deliveries
                      WHERE paper_deliveries.source = daily_papers.source
                        AND paper_deliveries.canonical_id = daily_papers.canonical_id
                        AND paper_deliveries.version = daily_papers.version
                  )
                """
        )
        params: list[Any] = [*sources, normalized_scope]
        if target_date_text is not None:
            query += " AND daily_papers.backfill_target_date = ?"
            params.append(target_date_text)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()

        pending = []
        identities = set()
        for row in rows:
            try:
                payload = json.loads(row["paper_json"])
                from sources.base_source import PaperMetadata

                paper = PaperMetadata.from_dict(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    "SQLite 待处理论文元数据损坏，已停止运行以避免漏报: "
                    f"{row['source']}:{row['paper_id']}"
                ) from exc
            expected_identity = (
                row["source"],
                row["canonical_id"],
                int(row["version"] or 0),
            )
            actual_identity = (
                paper.source,
                paper.canonical_id or paper.paper_id,
                paper.version or 0,
            )
            if paper.paper_id != row["paper_id"] or actual_identity != expected_identity:
                raise RuntimeError(
                    "SQLite 待处理论文身份不一致，已停止运行以避免错误交付: "
                    f"{row['source']}:{row['paper_id']}"
                )
            if expected_identity in identities:
                raise RuntimeError(
                    "SQLite 待处理队列包含重复论文版本: "
                    f"{row['source']}:{row['canonical_id']}v{row['version']}"
                )
            identities.add(expected_identity)
            pending.append((row, paper))

        pending.sort(key=lambda item: self._pending_row_sort_key(*item))
        total_pending = len(pending)
        if limit:
            pending = pending[:limit]

        selected: Dict[str, list["PaperMetadata"]] = {}
        for row, paper in pending:
            selected.setdefault(row["source"], []).append(paper)
        return selected, total_pending

    def upsert_paper_seen(
        self,
        run_id: str,
        source: str,
        paper: "PaperMetadata",
        stage_fingerprints: Optional[Dict[str, str]] = None,
    ):
        """Persist fresh metadata and invalidate stale incomplete stage output.

        Delivered rows are immutable ledger entries and are filtered before
        this method in normal runs.  For incomplete rows, a changed score
        input invalidates score/translation/analysis; a changed translation
        input invalidates only translation; and a changed analysis input
        invalidates only deep analysis.  This lets restarts reuse work only
        when it was produced for the same paper/configuration/model inputs.
        """
        now = datetime.now().isoformat()
        fingerprints = stage_fingerprints or {}
        score_fingerprint = fingerprints.get("score")
        translation_fingerprint = fingerprints.get("translation")
        analysis_fingerprint = fingerprints.get("analysis")
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM daily_papers WHERE source = ? AND paper_id = ?",
                (source, paper.paper_id),
            ).fetchone()
            if existing is not None:
                self._restore_optional_enrichment(paper, existing["paper_json"])

            paper_json = json.dumps(paper.to_dict(), ensure_ascii=False)
            if existing is not None and existing["completed_at"] is None:
                score_changed = (
                    score_fingerprint is not None
                    and existing["score_input_fingerprint"] != score_fingerprint
                )
                translation_changed = (
                    translation_fingerprint is not None
                    and existing["translation_input_fingerprint"] != translation_fingerprint
                )
                analysis_changed = (
                    analysis_fingerprint is not None
                    and existing["analysis_input_fingerprint"] != analysis_fingerprint
                )
                if score_changed:
                    conn.execute(
                        """
                        UPDATE daily_papers
                        SET score_json = NULL, score_audit_json = NULL,
                            abstract_cn = NULL, analysis_json = NULL,
                            scored_at = NULL, translated_at = NULL, analyzed_at = NULL,
                            score_status = 'pending', tldr_status = 'pending',
                            translation_status = 'pending',
                            analysis_status = 'pending', last_error = NULL
                        WHERE source = ? AND paper_id = ?
                        """,
                        (source, paper.paper_id),
                    )
                else:
                    if translation_changed:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET abstract_cn = NULL, translated_at = NULL,
                                translation_status = 'pending', last_error = NULL
                            WHERE source = ? AND paper_id = ?
                            """,
                            (source, paper.paper_id),
                        )
                    # Deep analysis consumes the translated/abstract content
                    # indirectly through the reporting configuration.  A
                    # changed translation stage must therefore not leave a
                    # stale deep-analysis cache attached to a new report.
                    if translation_changed or analysis_changed:
                        conn.execute(
                            """
                            UPDATE daily_papers
                            SET analysis_json = NULL, analyzed_at = NULL,
                                analysis_status = 'pending', last_error = NULL
                            WHERE source = ? AND paper_id = ?
                            """,
                            (source, paper.paper_id),
                        )

            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json,
                    score_input_fingerprint, translation_input_fingerprint,
                    analysis_input_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_input_fingerprint = COALESCE(
                        excluded.score_input_fingerprint, daily_papers.score_input_fingerprint
                    ),
                    translation_input_fingerprint = COALESCE(
                        excluded.translation_input_fingerprint, daily_papers.translation_input_fingerprint
                    ),
                    analysis_input_fingerprint = COALESCE(
                        excluded.analysis_input_fingerprint, daily_papers.analysis_input_fingerprint
                    )
                """,
                (
                    source,
                    paper.paper_id,
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                    now,
                    now,
                    run_id,
                    paper_json,
                    score_fingerprint,
                    translation_fingerprint,
                    analysis_fingerprint,
                ),
            )
            self._sync_paper_entity_for_record(conn, source, paper.paper_id)

    def update_scored_paper(
        self,
        run_id: str,
        source: str,
        scored: Dict[str, Any],
        stage_fingerprints: Optional[Dict[str, str]] = None,
        score_audit_metadata: Optional[Dict[str, Any]] = None,
    ):
        """Persist a complete score result for backward-compatible callers."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        fingerprints = stage_fingerprints or {}
        translation_done = bool(str(scored.get("abstract_cn", "")).strip())
        translation_status = "succeeded" if translation_done else "pending"
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_papers(
                    source, paper_id, canonical_id, version,
                    first_seen_at, last_seen_at, run_id, paper_json,
                    score_json, abstract_cn, scored_at, translated_at,
                    score_audit_json,
                    score_status, tldr_status, translation_status,
                    score_input_fingerprint, translation_input_fingerprint,
                    analysis_input_fingerprint, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(source, paper_id) DO UPDATE SET
                    canonical_id = excluded.canonical_id,
                    version = excluded.version,
                    last_seen_at = excluded.last_seen_at,
                    run_id = excluded.run_id,
                    paper_json = excluded.paper_json,
                    score_json = excluded.score_json,
                    score_audit_json = COALESCE(
                        excluded.score_audit_json, daily_papers.score_audit_json
                    ),
                    abstract_cn = excluded.abstract_cn,
                    scored_at = excluded.scored_at,
                    translated_at = excluded.translated_at,
                    score_status = excluded.score_status,
                    tldr_status = excluded.tldr_status,
                    translation_status = excluded.translation_status,
                    score_input_fingerprint = COALESCE(
                        excluded.score_input_fingerprint, daily_papers.score_input_fingerprint
                    ),
                    translation_input_fingerprint = COALESCE(
                        excluded.translation_input_fingerprint, daily_papers.translation_input_fingerprint
                    ),
                    analysis_input_fingerprint = COALESCE(
                        excluded.analysis_input_fingerprint, daily_papers.analysis_input_fingerprint
                    ),
                    last_error = NULL
                """,
                (
                    source,
                    scored["paper_id"],
                    paper.canonical_id or paper.paper_id,
                    paper.version or 0,
                    now,
                    now,
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    scored.get("abstract_cn", ""),
                    now,
                    now if scored.get("abstract_cn") else None,
                    json.dumps(score_audit_metadata, ensure_ascii=False)
                    if score_audit_metadata is not None
                    else None,
                    "succeeded",
                    "succeeded",
                    translation_status,
                    fingerprints.get("score"),
                    fingerprints.get("translation"),
                    fingerprints.get("analysis"),
                ),
            )
            self._sync_paper_entity_for_record(conn, source, scored["paper_id"])

    def update_score(
        self,
        run_id: str,
        source: str,
        scored: Dict[str, Any],
        score_input_fingerprint: Optional[str] = None,
        score_audit_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist score/TLDR before attempting translation."""
        now = datetime.now().isoformat()
        paper = scored["paper_metadata"]
        score_response = scored["score_response"]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, paper_json = ?, score_json = ?, score_audit_json = ?, scored_at = ?,
                    score_status = 'succeeded', tldr_status = 'succeeded',
                    score_input_fingerprint = COALESCE(?, score_input_fingerprint),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    json.dumps(paper.to_dict(), ensure_ascii=False),
                    score_response.model_dump_json(),
                    json.dumps(score_audit_metadata, ensure_ascii=False)
                    if score_audit_metadata is not None
                    else None,
                    now,
                    score_input_fingerprint,
                    source,
                    scored["paper_id"],
                ),
            )
            self._sync_paper_entity_for_record(conn, source, scored["paper_id"])

    def update_score_tldr(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        tldr: str,
    ) -> None:
        """Fill only the TL;DR of an otherwise valid persisted score.

        History repair intentionally avoids re-scoring a paper whose original
        relevance decision is already present.  The score JSON remains the
        single source of truth; this narrowly replaces its missing ``tldr``
        field and records a separate durable stage state for retry.
        """
        text = str(tldr or "").strip()
        if not text:
            raise ValueError("tldr must be non-empty")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT score_json, score_status FROM daily_papers "
                "WHERE source = ? AND paper_id = ?",
                (source, paper_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"论文不存在，无法补全 TL;DR: {source}:{paper_id}")
            if row["score_status"] != "succeeded" or not row["score_json"]:
                raise RuntimeError(f"评分未完成，无法仅补全 TL;DR: {source}:{paper_id}")
            try:
                payload = json.loads(row["score_json"])
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"评分 JSON 损坏，无法仅补全 TL;DR: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("评分 JSON 必须是对象")
            payload["tldr"] = text
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, score_json = ?, tldr_status = 'succeeded',
                    last_error = NULL, last_seen_at = ?
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    source,
                    paper_id,
                ),
            )
            self._sync_paper_entity_for_record(conn, source, paper_id)

    def update_translation(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        translation: str,
        translation_input_fingerprint: Optional[str] = None,
    ):
        """Persist a successful non-empty abstract translation."""
        if not translation or not translation.strip():
            raise ValueError("translation must be non-empty")
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, abstract_cn = ?, translated_at = ?,
                    translation_status = 'succeeded',
                    translation_input_fingerprint = COALESCE(
                        ?, translation_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    translation.strip(),
                    now,
                    translation_input_fingerprint,
                    source,
                    paper_id,
                ),
            )

    def mark_translation_not_required(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        translation_input_fingerprint: Optional[str] = None,
    ):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, translation_status = 'not_required',
                    translation_input_fingerprint = COALESCE(
                        ?, translation_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, translation_input_fingerprint, source, paper_id),
            )

    def update_analysis(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        analysis: "Stage2Response",
        analysis_input_fingerprint: Optional[str] = None,
    ):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_json = ?, analyzed_at = ?,
                    analysis_status = 'succeeded',
                    analysis_input_fingerprint = COALESCE(?, analysis_input_fingerprint),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (
                    run_id,
                    self._analysis_json(analysis),
                    now,
                    analysis_input_fingerprint,
                    source,
                    paper_id,
                ),
            )

    def mark_analysis_not_required(
        self,
        run_id: str,
        source: str,
        paper_id: str,
        analysis_input_fingerprint: Optional[str] = None,
    ):
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, analysis_status = 'not_required',
                    analysis_input_fingerprint = COALESCE(
                        ?, analysis_input_fingerprint
                    ),
                    last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, analysis_input_fingerprint, source, paper_id),
            )

    def update_error(
        self, run_id: str, source: str, paper_id: str, error: str, stage: str = "general"
    ):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, last_seen_at = ?, last_error = ?, retry_count = retry_count + 1,
                    score_status = CASE WHEN ? = 'score' THEN 'failed' ELSE score_status END,
                    tldr_status = CASE WHEN ? = 'tldr' THEN 'failed' ELSE tldr_status END,
                    translation_status = CASE WHEN ? = 'translation' THEN 'failed' ELSE translation_status END,
                    analysis_status = CASE WHEN ? = 'analysis' THEN 'failed' ELSE analysis_status END
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, now, error[:4000], stage, stage, stage, stage, source, paper_id),
            )
            self._sync_paper_entity_for_record(conn, source, paper_id)

    def mark_completed(self, run_id: str, source: str, paper_id: str):
        now = datetime.now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE daily_papers
                SET run_id = ?, completed_at = ?, last_error = NULL
                WHERE source = ? AND paper_id = ?
                """,
                (run_id, now, source, paper_id),
            )
            self._sync_paper_entity_for_record(conn, source, paper_id)

    def hydrate_scored_paper(
        self, paper: "PaperMetadata", record: sqlite3.Row, require_translation: bool = True
    ) -> Optional[Dict[str, Any]]:
        if not record or not record["score_json"]:
            return None
        if record["score_status"] != "succeeded":
            return None
        if record["tldr_status"] != "succeeded":
            return None
        if require_translation and record["translation_status"] not in (
            "succeeded",
            "not_required",
        ):
            return None

        # Callers can hydrate a score directly (without having first called
        # upsert_paper_seen in this process), so restore the optional fields
        # here as well.  This keeps report rendering and deep-analysis retry
        # decisions stable across process restarts.
        self._restore_optional_enrichment(paper, record["paper_json"])

        from agents.analysis_agent import WeightedScoreResponse

        score_response = WeightedScoreResponse.model_validate_json(record["score_json"])
        return {
            "paper_metadata": paper,
            "paper_id": paper.paper_id,
            "title": paper.title,
            "authors": paper.get_authors_string(),
            "abstract": paper.abstract,
            "abstract_cn": record["abstract_cn"] or "",
            "url": paper.url,
            "pdf_url": paper.pdf_url,
            "published": paper.published_date.strftime("%Y-%m-%d")
            if paper.published_date
            else "N/A",
            "canonical_id": paper.canonical_id,
            "version": paper.version,
            "score_response": score_response,
        }

    def hydrate_analysis(self, record: sqlite3.Row) -> Optional["Stage2Response"]:
        if not record or not record["analysis_json"]:
            return None
        if record["analysis_status"] != "succeeded":
            return None
        raw_analysis = record["analysis_json"]
        try:
            payload = json.loads(raw_analysis)
            if not isinstance(payload, dict) or not payload:
                raise ValueError("深度分析缓存必须是非空 JSON 对象")

            # Validate known fields while preserving unknown template fields.
            # Future/custom report modules may add keys that Stage2Response does
            # not know yet, so returning model_dump() here would silently lose
            # them during retry hydration.  The shared validator additionally
            # rejects a nonempty metadata/error object that contains no
            # renderable enabled module; treating that as success used to hide
            # a failed deep-analysis call behind an empty report section.
            from agents.analysis_agent import validate_deep_analysis_payload
            from config import settings

            validate_deep_analysis_payload(
                payload,
                settings.load_report_template("deep_analysis_template.json"),
            )
        except Exception as exc:
            # A successful status with unreadable data is a recoverable cache
            # corruption, not a valid result.  Clear it and mark the stage
            # failed so the next run retries it with the same input fingerprint.
            try:
                source = record["source"]
                paper_id = record["paper_id"]
            except (IndexError, KeyError, TypeError):
                return None
            error = f"持久化深度分析缓存无效: {exc}"[:4000]
            now = datetime.now().isoformat()
            with self._lock, self._connect() as conn:
                conn.execute(
                    """
                    UPDATE daily_papers
                    SET analysis_json = NULL, analyzed_at = NULL,
                        analysis_status = 'failed', last_error = ?,
                        retry_count = retry_count + 1
                    WHERE source = ? AND paper_id = ?
                      AND analysis_status = 'succeeded'
                    """,
                    (error, source, paper_id),
                )
            return None
        return payload
