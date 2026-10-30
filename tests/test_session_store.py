# -*- coding: utf-8 -*-
"""
Testa session_store.SessionStore isolada: criar/carregar, atualizar
(incluindo zerar last_model_content pra None), poda automática, renomear,
excluir. session_store.py só depende de sqlite3/json/time (stdlib) — não
precisa de nenhum stub do NVDA. Dentro do NVDA, sqlite3 é lido da cópia
vendorizada em globalPlugins/operadorIA/lib/ (ver __init__.py); aqui nos
testes standalone, do sqlite3 normal do Python do sistema.

Rodar: python tests/test_session_store.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

session_store = load_addon_module("session_store")

SessionStore = session_store.SessionStore
derive_title = session_store.derive_title
# Mesmo módulo sqlite3 que session_store.py usa (vendorizado em lib/ quando
# dentro do NVDA) — importar só AGORA, depois do load_addon_module acima,
# garante que não fique cacheado em sys.modules a partir do sqlite3 do
# sistema antes do _harness inserir lib/ na frente do sys.path.
sqlite3 = session_store.sqlite3


class _TempDb:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sessions.db")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


def test_create_and_load_session_roundtrip():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Abrir bloco de notas")
        record = store.load_session(session_id)
        assert record.title == "Abrir bloco de notas"
        assert record.task_text == ""
        assert record.log_entries == []
        assert record.last_model_content is None
        assert record.turn_count == 0
        assert record.conversation_log == []
        assert record.notes == []
    print("test_create_and_load_session_roundtrip OK")


def test_update_session_persists_log_entries_and_last_model_content():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Tarefa X")
        fake_content = {"role": "model", "parts": [{"functionCall": {"name": "click_tool", "args": {"x": 1}}}]}
        conversation = [
            {"sender": "Você", "message": "organize os itens da área de trabalho"},
            {"sender": "Operador IA", "message": "Os itens foram classificados por nome."},
        ]
        ok = store.update_session(
            session_id,
            task_text="Tarefa: X",
            log_entries=["1. click -> ok, tela mudou"],
            last_model_content=fake_content,
            conversation_log=conversation,
        )
        assert ok is True
        record = store.load_session(session_id)
        assert record.task_text == "Tarefa: X"
        assert record.log_entries == ["1. click -> ok, tela mudou"]
        assert record.last_model_content == fake_content
        assert record.turn_count == 1
        assert record.conversation_log == conversation
    print("test_update_session_persists_log_entries_and_last_model_content OK")


def test_update_session_without_conversation_log_keyword_defaults_to_empty_list():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Tarefa antiga")
        # Reproduz uma chamada no estilo anterior a esse campo (sem o kwarg).
        ok = store.update_session(session_id, task_text="x", log_entries=[], last_model_content=None)
        assert ok is True
        assert store.load_session(session_id).conversation_log == []
        assert store.load_session(session_id).notes == []
    print("test_update_session_without_conversation_log_keyword_defaults_to_empty_list OK")


def test_update_session_persists_notes():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Tarefa com notas")
        notes = ["Site X: passagem SP-RJ 15/11 por R$450", "Site Y: sem disponibilidade"]
        store.update_session(
            session_id, task_text="Tarefa: Z", log_entries=["1. x"], last_model_content=None, notes=notes,
        )
        assert store.load_session(session_id).notes == notes
    print("test_update_session_persists_notes OK")


def test_update_session_can_clear_last_model_content_to_none():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Tarefa Y")
        store.update_session(session_id, task_text="Tarefa: Y", log_entries=["1. x"], last_model_content={"a": 1})
        store.update_session(session_id, task_text="Tarefa: Y", log_entries=["1. x"], last_model_content=None)
        record = store.load_session(session_id)
        assert record.last_model_content is None, "done_tool/circuit breaker devem persistir None, nunca sobra function_call"
    print("test_update_session_can_clear_last_model_content_to_none OK")


def test_update_session_on_missing_id_returns_false_without_raising():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        ok = store.update_session(9999, task_text="x", log_entries=[], last_model_content=None)
        assert ok is False
    print("test_update_session_on_missing_id_returns_false_without_raising OK")


def test_list_sessions_ordered_by_updated_at_desc():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        first = store.create_session("Primeira")
        second = store.create_session("Segunda")
        store.update_session(first, task_text="t", log_entries=["1"], last_model_content=None)
        sessions = store.list_sessions()
        assert sessions[0].id == first, "a mais recentemente atualizada deve vir primeiro"
        assert sessions[1].id == second
    print("test_list_sessions_ordered_by_updated_at_desc OK")


def test_rename_session():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Nome antigo")
        assert store.rename_session(session_id, "Nome novo") is True
        assert store.load_session(session_id).title == "Nome novo"
        assert store.rename_session(9999, "x") is False
    print("test_rename_session OK")


def test_delete_session():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        session_id = store.create_session("Vai sumir")
        assert store.delete_session(session_id) is True
        assert store.load_session(session_id) is None
        assert store.delete_session(session_id) is False
    print("test_delete_session OK")


def test_prune_keeps_only_max_sessions_newest():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        ids = [store.create_session(f"Sessão {i}") for i in range(5)]
        # Cria mais 3 sessões já aplicando poda pra manter só 4 no total.
        for i in range(5, 8):
            store.create_session(f"Sessão {i}", max_sessions=4)
        remaining = {s.id for s in store.list_sessions()}
        assert len(remaining) == 4
        # As mais antigas (primeiras criadas) devem ter sido removidas.
        assert ids[0] not in remaining
    print("test_prune_keeps_only_max_sessions_newest OK")


def test_load_missing_session_returns_none():
    with _TempDb() as db_path:
        store = SessionStore(db_path)
        assert store.load_session(123456) is None
    print("test_load_missing_session_returns_none OK")


def test_schema_creation_is_idempotent():
    with _TempDb() as db_path:
        store1 = SessionStore(db_path)
        session_id = store1.create_session("Sobrevive")
        store2 = SessionStore(db_path)  # reabre o mesmo arquivo, não deve quebrar nem apagar dados
        assert store2.load_session(session_id).title == "Sobrevive"
    print("test_schema_creation_is_idempotent OK")


def test_migrates_old_schema_db_without_conversation_log_column():
    """
    Reproduz um .db real criado por uma versão anterior do addon (3 colunas
    JSON, sem conversation_log_json) e confirma que abrir com SessionStore
    migra em vez de quebrar, preservando os dados antigos.
    """
    with _TempDb() as db_path:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                task_text TEXT NOT NULL DEFAULT '',
                log_entries_json TEXT NOT NULL DEFAULT '[]',
                last_model_content_json TEXT
            )
        """)
        conn.execute(
            "INSERT INTO sessions (title, created_at, updated_at, turn_count, task_text, log_entries_json, last_model_content_json) "
            "VALUES (?, ?, ?, 1, ?, ?, NULL)",
            ("Sessão antiga", 1.0, 1.0, "Tarefa: X", '["1. click -> ok, tela mudou"]'),
        )
        old_id = conn.execute("SELECT id FROM sessions").fetchone()[0]
        conn.commit()
        conn.close()

        store = SessionStore(db_path)  # dispara _ensure_schema -> migração aditiva
        record = store.load_session(old_id)
        assert record.title == "Sessão antiga"
        assert record.task_text == "Tarefa: X"
        assert record.log_entries == ["1. click -> ok, tela mudou"], "dado antigo não pode se perder na migração"
        assert record.conversation_log == [], "coluna nova, sem dado antigo pra preencher — default seguro"

        # Sessão migrada deve continuar aceitando update normalmente.
        store.update_session(
            old_id, task_text="Tarefa: X", log_entries=["1. click -> ok, tela mudou"],
            last_model_content=None, conversation_log=[{"sender": "Você", "message": "oi"}],
        )
        assert store.load_session(old_id).conversation_log == [{"sender": "Você", "message": "oi"}]
    print("test_migrates_old_schema_db_without_conversation_log_column OK")


def test_migrates_old_schema_db_without_notes_column():
    """
    Mesma ideia do teste acima, mas pra um .db criado DEPOIS de
    conversation_log_json já existir, mas ANTES de notes_json (suporte a
    tarefas longas) — confirma que a migração aditiva cobre essa geração
    intermediária de schema também, sem perder os dados já salvos.
    """
    with _TempDb() as db_path:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE sessions (
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
        """)
        conn.execute(
            "INSERT INTO sessions (title, created_at, updated_at, turn_count, task_text, log_entries_json, "
            "last_model_content_json, conversation_log_json) VALUES (?, ?, ?, 1, ?, ?, NULL, ?)",
            ("Sessão sem notas", 1.0, 1.0, "Tarefa: X", '["1. click -> ok, tela mudou"]', '[{"sender": "Você", "message": "oi"}]'),
        )
        old_id = conn.execute("SELECT id FROM sessions").fetchone()[0]
        conn.commit()
        conn.close()

        store = SessionStore(db_path)
        record = store.load_session(old_id)
        assert record.conversation_log == [{"sender": "Você", "message": "oi"}], "dado antigo não pode se perder"
        assert record.notes == [], "coluna nova, sem dado antigo pra preencher — default seguro"

        store.update_session(
            old_id, task_text="Tarefa: X", log_entries=["1. x"], last_model_content=None, notes=["fato novo"],
        )
        assert store.load_session(old_id).notes == ["fato novo"]
    print("test_migrates_old_schema_db_without_notes_column OK")


def test_derive_title_truncates_long_commands():
    short = derive_title("Abrir bloco de notas", 60)
    assert short == "Abrir bloco de notas"

    long_command = "x" * 100
    truncated = derive_title(long_command, 60)
    assert len(truncated) == 60
    assert truncated.endswith("…")
    print("test_derive_title_truncates_long_commands OK")


if __name__ == "__main__":
    test_create_and_load_session_roundtrip()
    test_update_session_persists_log_entries_and_last_model_content()
    test_update_session_without_conversation_log_keyword_defaults_to_empty_list()
    test_update_session_persists_notes()
    test_update_session_can_clear_last_model_content_to_none()
    test_update_session_on_missing_id_returns_false_without_raising()
    test_list_sessions_ordered_by_updated_at_desc()
    test_rename_session()
    test_delete_session()
    test_prune_keeps_only_max_sessions_newest()
    test_load_missing_session_returns_none()
    test_schema_creation_is_idempotent()
    test_migrates_old_schema_db_without_conversation_log_column()
    test_migrates_old_schema_db_without_notes_column()
    test_derive_title_truncates_long_commands()
    print("\nTODOS OS TESTES PASSARAM")
