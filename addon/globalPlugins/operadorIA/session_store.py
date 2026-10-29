# -*- coding: utf-8 -*-
"""
Persistência de seções (sessions) em SQLite — permite listar, retomar,
renomear e excluir conversas anteriores, estilo "voltar a uma seção
anterior" do Claude Code.

Importante: a build de Python embutida no NVDA NÃO inclui o módulo
`sqlite3` (falta o pacote inteiro — nem a parte pura Python, nem a
extensão compilada `_sqlite3`/`sqlite3.dll`). Por isso o addon vendoriza
`sqlite3/`, `_sqlite3.pyd` e `sqlite3.dll` direto em `globalPlugins/
operadorIA/lib/` (ver o comentário em `__init__.py` sobre `add_dll_directory`)
— o `import sqlite3` abaixo resolve pra essa cópia vendorizada quando
rodando dentro do NVDA, e pro sqlite3 normal do sistema quando rodando os
testes standalone (`tests/`).

Este módulo nunca importa `wx`, `google.genai`, `config` (NVDA) nem
`globalVars` — só `sqlite3`/`json`/`time`/`os`. Quem resolve o caminho do
arquivo .db é o chamador (GlobalPlugin); aqui dentro tudo é
string/lista/dict/None já prontos. Isso deixa a classe 100% testável fora
do NVDA, sem nenhum stub (ver tests/test_session_store.py).
"""

import json
import sqlite3
import time
from collections import namedtuple

SessionSummary = namedtuple("SessionSummary", ["id", "title", "updated_at", "turn_count"])
SessionRecord = namedtuple(
    "SessionRecord",
    [
        "id", "title", "created_at", "updated_at", "turn_count", "task_text", "log_entries",
        "last_model_content", "conversation_log",
    ],
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    task_text TEXT NOT NULL DEFAULT '',
    log_entries_json TEXT NOT NULL DEFAULT '[]',
    last_model_content_json TEXT,
    conversation_log_json TEXT NOT NULL DEFAULT '[]'
)
"""


def derive_title(command, max_length):
    """Título de exibição a partir do comando do usuário, truncado com '…'."""
    text = (command or "").strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


class SessionStore:
    """
    Cada método público abre e fecha sua própria conexão sqlite3 — em vez
    de uma conexão compartilhada entre a thread de automação e a GUI
    thread (o que exigiria check_same_thread=False e abriria espaço pra
    corrupção sutil em escrita concorrente). O custo de abrir/fechar um
    arquivo sqlite pequeno a cada chamada é irrelevante comparado à
    latência de rede do Gemini ou de uma ação física, que já dominam o
    tempo de cada turno.
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
        `_SCHEMA` (CREATE TABLE IF NOT EXISTS) só cobre banco novo — numa
        tabela que já existe (usuário com sessões salvas por uma versão
        anterior do addon, sem a coluna `conversation_log_json`), isso é um
        no-op. Por isso a migração aditiva abaixo: confere as colunas
        existentes e só adiciona a que falta, preservando os dados já
        salvos (title/task_text/log_entries/last_model_content).
        """
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "conversation_log_json" not in existing_cols:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN conversation_log_json TEXT NOT NULL DEFAULT '[]'"
                )

    def create_session(self, title, max_sessions=None):
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (title, created_at, updated_at, turn_count, task_text, log_entries_json, last_model_content_json, conversation_log_json) "
                "VALUES (?, ?, ?, 0, '', '[]', NULL, '[]')",
                (title, now, now),
            )
            session_id = cursor.lastrowid
        if max_sessions is not None:
            self._prune(max_sessions)
        return session_id

    def update_session(self, session_id, task_text, log_entries, last_model_content, conversation_log=None):
        log_entries_json = json.dumps(list(log_entries or []))
        last_model_content_json = json.dumps(last_model_content) if last_model_content is not None else None
        conversation_log_json = json.dumps(list(conversation_log or []))
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sessions SET task_text = ?, log_entries_json = ?, last_model_content_json = ?, "
                "conversation_log_json = ?, turn_count = ?, updated_at = ? WHERE id = ?",
                (
                    task_text or "", log_entries_json, last_model_content_json, conversation_log_json,
                    len(log_entries or []), time.time(), session_id,
                ),
            )
            return cursor.rowcount > 0

    def list_sessions(self):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, updated_at, turn_count FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [SessionSummary(*row) for row in rows]

    def load_session(self, session_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at, turn_count, task_text, log_entries_json, "
                "last_model_content_json, conversation_log_json FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        (
            id_, title, created_at, updated_at, turn_count, task_text, log_entries_json,
            last_model_content_json, conversation_log_json,
        ) = row
        return SessionRecord(
            id=id_,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            turn_count=turn_count,
            task_text=task_text,
            log_entries=json.loads(log_entries_json),
            last_model_content=json.loads(last_model_content_json) if last_model_content_json is not None else None,
            conversation_log=json.loads(conversation_log_json),
        )

    def rename_session(self, session_id, new_title):
        with self._connect() as conn:
            cursor = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (new_title, session_id))
            return cursor.rowcount > 0

    def delete_session(self, session_id):
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def _prune(self, max_sessions):
        """Mantém só as `max_sessions` sessões mais recentes por updated_at."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE id NOT IN ("
                "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT ?"
                ")",
                (max_sessions,),
            )
