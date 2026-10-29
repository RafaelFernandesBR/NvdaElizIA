# -*- coding: utf-8 -*-
"""
Persistência de tarefas agendadas em SQLite — mesma disciplina de
session_store.py (nunca importa wx/google.genai/config/globalVars; cada
método abre e fecha sua própria conexão; sem conexão compartilhada entre
threads). Vive no MESMO arquivo .db que as sessões
(GlobalPlugin._resolve_session_db_path(), ver __init__.py) — tabela
própria, sem relação com `sessions`; contenção entre as duas camadas fica
coberta pelo mesmo PRAGMA busy_timeout já usado por session_store.

Testável fora do NVDA sem nenhum stub (ver tests/test_scheduler_store.py),
pelo mesmo motivo de session_store.py.
"""

import json
import sqlite3
import time
from collections import namedtuple

ScheduledTask = namedtuple(
    "ScheduledTask",
    [
        "id", "command", "title", "created_at", "run_at", "status",
        "created_by", "result_summary", "session_id", "updated_at",
    ],
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    run_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT NOT NULL DEFAULT 'user',
    result_summary TEXT,
    session_id INTEGER,
    updated_at REAL NOT NULL
)
"""

# Status possíveis — nunca livres, sempre um destes (ver métodos abaixo).
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_MISSED = "missed"
STATUS_FAILED = "failed"


class SchedulerStore:
    """
    Mesma política de conexão de session_store.SessionStore: cada chamada
    pública abre e fecha sua própria conexão sqlite3 (nunca compartilhada
    entre a thread do scheduler, a de automação e a GUI thread).
    """

    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA busy_timeout = 2000")
        return conn

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def create_task(self, command, title, run_at, created_by="user"):
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO scheduled_tasks "
                "(command, title, created_at, run_at, status, created_by, result_summary, session_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                (command, title, now, run_at, STATUS_PENDING, created_by, now),
            )
            return cursor.lastrowid

    def _row_to_task(self, row):
        return ScheduledTask(*row)

    _COLUMNS = (
        "id, command, title, created_at, run_at, status, created_by, "
        "result_summary, session_id, updated_at"
    )

    def load_task(self, task_id):
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {self._COLUMNS} FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, status=None):
        """Lista tarefas, mais recentes (run_at) primeiro. status=None lista todas."""
        with self._connect() as conn:
            if status is None:
                rows = conn.execute(
                    f"SELECT {self._COLUMNS} FROM scheduled_tasks ORDER BY run_at DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {self._COLUMNS} FROM scheduled_tasks WHERE status = ? ORDER BY run_at DESC",
                    (status,),
                ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_due_tasks(self, now):
        """Tarefas pendentes cujo horário já chegou (run_at <= now)."""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._COLUMNS} FROM scheduled_tasks WHERE status = ? AND run_at <= ? ORDER BY run_at ASC",
                (STATUS_PENDING, now),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_missed_tasks(self, now, grace_seconds):
        """
        Tarefas pendentes cujo horário já passou há mais de `grace_seconds`
        — usado só no catch-up de inicialização (NVDA estava fechado),
        nunca durante o polling normal (esse usa get_due_tasks, sem folga).
        """
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._COLUMNS} FROM scheduled_tasks WHERE status = ? AND run_at <= ? ORDER BY run_at ASC",
                (STATUS_PENDING, now - grace_seconds),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def cancel_task(self, task_id):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (STATUS_CANCELLED, time.time(), task_id, STATUS_PENDING),
            )
            return cursor.rowcount > 0

    def delete_task(self, task_id):
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def mark_done(self, task_id, success, summary, session_id=None):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = ?, result_summary = ?, session_id = ?, updated_at = ? WHERE id = ?",
                (STATUS_DONE if success else STATUS_FAILED, summary, session_id, time.time(), task_id),
            )
            return cursor.rowcount > 0

    def mark_missed(self, task_id):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (STATUS_MISSED, time.time(), task_id, STATUS_PENDING),
            )
            return cursor.rowcount > 0
