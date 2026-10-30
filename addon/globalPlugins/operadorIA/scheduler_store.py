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
        "recurrence_seconds", "last_result_json", "notify_on_change_only",
        "max_occurrences", "occurrence_count", "recurrence_until",
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
    updated_at REAL NOT NULL,
    recurrence_seconds INTEGER,
    last_result_json TEXT,
    notify_on_change_only INTEGER NOT NULL DEFAULT 0,
    max_occurrences INTEGER,
    occurrence_count INTEGER NOT NULL DEFAULT 0,
    recurrence_until REAL
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
        """
        `_SCHEMA` (CREATE TABLE IF NOT EXISTS) só cobre banco novo — num
        arquivo .db que já existia antes das colunas de recorrência, isso é
        um no-op. Por isso a migração aditiva abaixo, no mesmo padrão de
        session_store.py: confere as colunas existentes e só adiciona as
        que faltam, preservando as tarefas já salvas.
        """
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(scheduled_tasks)")}
            migrations = {
                "recurrence_seconds": "ALTER TABLE scheduled_tasks ADD COLUMN recurrence_seconds INTEGER",
                "last_result_json": "ALTER TABLE scheduled_tasks ADD COLUMN last_result_json TEXT",
                "notify_on_change_only": (
                    "ALTER TABLE scheduled_tasks ADD COLUMN notify_on_change_only INTEGER NOT NULL DEFAULT 0"
                ),
                "max_occurrences": "ALTER TABLE scheduled_tasks ADD COLUMN max_occurrences INTEGER",
                "occurrence_count": (
                    "ALTER TABLE scheduled_tasks ADD COLUMN occurrence_count INTEGER NOT NULL DEFAULT 0"
                ),
                "recurrence_until": "ALTER TABLE scheduled_tasks ADD COLUMN recurrence_until REAL",
            }
            for col, ddl in migrations.items():
                if col not in existing_cols:
                    conn.execute(ddl)

    def create_task(self, command, title, run_at, created_by="user",
                     recurrence_seconds=None, notify_on_change_only=False,
                     max_occurrences=None, recurrence_until=None):
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO scheduled_tasks "
                "(command, title, created_at, run_at, status, created_by, result_summary, session_id, updated_at, "
                "recurrence_seconds, last_result_json, notify_on_change_only, max_occurrences, occurrence_count, "
                "recurrence_until) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, 0, ?)",
                (
                    command, title, now, run_at, STATUS_PENDING, created_by, now,
                    recurrence_seconds, int(bool(notify_on_change_only)), max_occurrences, recurrence_until,
                ),
            )
            return cursor.lastrowid

    def _row_to_task(self, row):
        return ScheduledTask(*row)

    _COLUMNS = (
        "id, command, title, created_at, run_at, status, created_by, "
        "result_summary, session_id, updated_at, recurrence_seconds, "
        "last_result_json, notify_on_change_only, max_occurrences, "
        "occurrence_count, recurrence_until"
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

        Tarefas RECORRENTES (recurrence_seconds not null) nunca entram
        aqui: uma checagem recorrente é sobre o estado ATUAL, não sobre o
        instante exato em que deveria ter rodado — "perdeu 5 checagens de
        preço enquanto o NVDA estava fechado" produziria 5 respostas
        idênticas, sem valor, só inflando a revisão de tarefas perdidas.
        Elas simplesmente disparam no próximo poll normal (get_due_tasks),
        recalculando a partir de agora.
        """
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {self._COLUMNS} FROM scheduled_tasks "
                "WHERE status = ? AND run_at <= ? AND recurrence_seconds IS NULL ORDER BY run_at ASC",
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
        # Guarda por status=pending (mesmo padrão de cancel_task/mark_missed)
        # — antes não existia, e uma linha cancelada pelo SchedulerDialog
        # ENQUANTO a automação daquela ocorrência ainda rodava podia ser
        # sobrescrita de volta pra done/failed quando a automação terminasse.
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = ?, result_summary = ?, session_id = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (STATUS_DONE if success else STATUS_FAILED, summary, session_id, time.time(), task_id, STATUS_PENDING),
            )
            return cursor.rowcount > 0

    def mark_missed(self, task_id):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE scheduled_tasks SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (STATUS_MISSED, time.time(), task_id, STATUS_PENDING),
            )
            return cursor.rowcount > 0

    def reschedule_occurrence(self, task_id, next_run_at, summary, success=True, last_result_json=None, session_id=None):
        """
        Única forma de uma linha RECORRENTE seguir pendente sem terminar:
        incrementa occurrence_count e decide ela mesma — nunca a IA (ver
        action_tools.SCHEDULE_TASK_TOOL) — se max_occurrences/
        recurrence_until já foi atingido; nesse caso termina (status
        depende de `success`, igual mark_done) em vez de agendar a próxima
        ocorrência, na MESMA linha (sem explosão de uma linha por
        ocorrência).

        `last_result_json=None` NUNCA apaga um snapshot válido já salvo —
        acontece sempre que a ocorrência atual falhou sem chegar a chamar
        done_tool (erro de API, max_turns, circuit breaker) ou teve sucesso
        mas a IA omitiu o parâmetro opcional result_data; nesses casos o
        valor anterior precisa continuar disponível pra próxima comparação.

        Guardado por status=pending, mesmo motivo do guard em mark_done:
        nunca reagenda/termina uma linha que o usuário já cancelou.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT occurrence_count, max_occurrences, recurrence_until FROM scheduled_tasks "
                "WHERE id = ? AND status = ?",
                (task_id, STATUS_PENDING),
            ).fetchone()
            if row is None:
                return False
            occurrence_count, max_occurrences, recurrence_until = row
            new_count = occurrence_count + 1
            reached_max = max_occurrences is not None and new_count >= max_occurrences
            past_deadline = recurrence_until is not None and next_run_at > recurrence_until

            if reached_max or past_deadline:
                cursor = conn.execute(
                    "UPDATE scheduled_tasks SET status = ?, result_summary = ?, session_id = ?, "
                    "occurrence_count = ?, last_result_json = COALESCE(?, last_result_json), updated_at = ? "
                    "WHERE id = ? AND status = ?",
                    (
                        STATUS_DONE if success else STATUS_FAILED, summary, session_id,
                        new_count, last_result_json, now, task_id, STATUS_PENDING,
                    ),
                )
                return cursor.rowcount > 0

            cursor = conn.execute(
                "UPDATE scheduled_tasks SET run_at = ?, occurrence_count = ?, "
                "last_result_json = COALESCE(?, last_result_json), updated_at = ? WHERE id = ? AND status = ?",
                (next_run_at, new_count, last_result_json, now, task_id, STATUS_PENDING),
            )
            return cursor.rowcount > 0
