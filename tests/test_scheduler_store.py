# -*- coding: utf-8 -*-
"""
Testa scheduler_store.SchedulerStore isolada: criar/carregar, listar por
status, due/missed por horário, cancelar, concluir. Mesma disciplina de
test_session_store.py — só depende de sqlite3/json/time (stdlib).

Rodar: python tests/test_scheduler_store.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

scheduler_store = load_addon_module("scheduler_store")

SchedulerStore = scheduler_store.SchedulerStore


class _TempDb:
    def __enter__(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "scheduler.db")
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)


def test_create_and_load_task_roundtrip():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("abra o bloco de notas", "Abrir notas", run_at=1000.0, created_by="ai")
        task = store.load_task(task_id)
        assert task.command == "abra o bloco de notas"
        assert task.title == "Abrir notas"
        assert task.run_at == 1000.0
        assert task.status == scheduler_store.STATUS_PENDING
        assert task.created_by == "ai"
        assert task.result_summary is None
        assert task.session_id is None
        assert task.recurrence_seconds is None, "tarefa única, sem recorrência — comportamento padrão inalterado"
        assert task.max_occurrences is None
        assert task.occurrence_count == 0
        assert task.recurrence_until is None
    print("test_create_and_load_task_roundtrip OK")


def test_load_missing_task_returns_none():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        assert store.load_task(9999) is None
    print("test_load_missing_task_returns_none OK")


def test_get_due_tasks_only_returns_pending_past_run_at():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        past = store.create_task("tarefa vencida", "Vencida", run_at=100.0)
        future = store.create_task("tarefa futura", "Futura", run_at=100000.0)
        due = store.get_due_tasks(now=500.0)
        due_ids = {t.id for t in due}
        assert past in due_ids
        assert future not in due_ids
    print("test_get_due_tasks_only_returns_pending_past_run_at OK")


def test_get_due_tasks_excludes_cancelled_and_done():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        cancelled = store.create_task("x", "X", run_at=100.0)
        done = store.create_task("y", "Y", run_at=100.0)
        store.cancel_task(cancelled)
        store.mark_done(done, success=True, summary="ok")
        assert store.get_due_tasks(now=500.0) == []
    print("test_get_due_tasks_excludes_cancelled_and_done OK")


def test_get_missed_tasks_requires_grace_period_elapsed():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0)
        # Vencida há só 10s — dentro da folga de 60s, não é "perdida" ainda.
        assert store.get_missed_tasks(now=1010.0, grace_seconds=60) == []
        # Vencida há 100s — além da folga de 60s, agora conta como perdida.
        missed = store.get_missed_tasks(now=1100.0, grace_seconds=60)
        assert [t.id for t in missed] == [task_id]
    print("test_get_missed_tasks_requires_grace_period_elapsed OK")


def test_cancel_task_only_affects_pending():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0)
        assert store.cancel_task(task_id) is True
        assert store.load_task(task_id).status == scheduler_store.STATUS_CANCELLED
        # Já cancelada — cancelar de novo não encontra uma linha 'pending' pra afetar.
        assert store.cancel_task(task_id) is False
    print("test_cancel_task_only_affects_pending OK")


def test_mark_done_success_and_failure():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        ok_id = store.create_task("x", "X", run_at=1000.0)
        fail_id = store.create_task("y", "Y", run_at=1000.0)
        store.mark_done(ok_id, success=True, summary="concluído", session_id=42)
        store.mark_done(fail_id, success=False, summary="não encontrei o botão")
        assert store.load_task(ok_id).status == scheduler_store.STATUS_DONE
        assert store.load_task(ok_id).session_id == 42
        assert store.load_task(fail_id).status == scheduler_store.STATUS_FAILED
    print("test_mark_done_success_and_failure OK")


def test_mark_done_never_overwrites_a_cancelled_task():
    # Regressão do bug corrigido junto da recorrência: o SchedulerDialog
    # pode cancelar uma linha ENQUANTO a automação daquela ocorrência ainda
    # está rodando (nada marca 'running' no meio) — mark_done não pode
    # sobrescrever esse cancelamento de volta pra done/failed.
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0)
        store.cancel_task(task_id)
        assert store.mark_done(task_id, success=True, summary="concluído") is False
        assert store.load_task(task_id).status == scheduler_store.STATUS_CANCELLED
    print("test_mark_done_never_overwrites_a_cancelled_task OK")


def test_mark_missed_only_affects_pending():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0)
        assert store.mark_missed(task_id) is True
        assert store.load_task(task_id).status == scheduler_store.STATUS_MISSED
        assert store.mark_missed(task_id) is False
    print("test_mark_missed_only_affects_pending OK")


def test_delete_task():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0)
        assert store.delete_task(task_id) is True
        assert store.load_task(task_id) is None
        assert store.delete_task(task_id) is False
    print("test_delete_task OK")


def test_list_tasks_filters_by_status_and_orders_by_run_at_desc():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        earlier = store.create_task("x", "Cedo", run_at=100.0)
        later = store.create_task("y", "Tarde", run_at=200.0)
        cancelled = store.create_task("z", "Cancelada", run_at=300.0)
        store.cancel_task(cancelled)

        pending = store.list_tasks(status=scheduler_store.STATUS_PENDING)
        assert [t.id for t in pending] == [later, earlier]

        all_tasks = store.list_tasks()
        assert len(all_tasks) == 3
    print("test_list_tasks_filters_by_status_and_orders_by_run_at_desc OK")


def test_schema_creation_is_idempotent():
    with _TempDb() as db_path:
        store1 = SchedulerStore(db_path)
        task_id = store1.create_task("x", "X", run_at=1000.0)
        store2 = SchedulerStore(db_path)  # reabre o mesmo arquivo
        assert store2.load_task(task_id).title == "X"
    print("test_schema_creation_is_idempotent OK")


def test_migrates_old_schema_db_without_recurrence_columns():
    """
    Reproduz um .db real criado antes do suporte a tarefas longas (sem as
    6 colunas de recorrência) e confirma que abrir com SchedulerStore
    migra em vez de quebrar, preservando a tarefa já salva.
    """
    import sqlite3
    with _TempDb() as db_path:
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE scheduled_tasks (
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
        """)
        conn.execute(
            "INSERT INTO scheduled_tasks (command, title, created_at, run_at, status, created_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("abrir notas", "Tarefa antiga", 1.0, 1000.0, scheduler_store.STATUS_PENDING, "user", 1.0),
        )
        old_id = conn.execute("SELECT id FROM scheduled_tasks").fetchone()[0]
        conn.commit()
        conn.close()

        store = SchedulerStore(db_path)  # dispara _ensure_schema -> migração aditiva
        task = store.load_task(old_id)
        assert task.command == "abrir notas", "dado antigo não pode se perder na migração"
        assert task.recurrence_seconds is None
        assert task.occurrence_count == 0
        assert task.max_occurrences is None

        # Tarefa migrada deve aceitar reschedule_occurrence normalmente.
        assert store.reschedule_occurrence(old_id, next_run_at=2000.0, summary="ok") is True
        assert store.load_task(old_id).run_at == 2000.0
    print("test_migrates_old_schema_db_without_recurrence_columns OK")


def test_create_task_with_recurrence_params():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task(
            "veja o preço", "Monitorar preço", run_at=1000.0, created_by="ai",
            recurrence_seconds=600, notify_on_change_only=True, max_occurrences=3, recurrence_until=5000.0,
        )
        task = store.load_task(task_id)
        assert task.recurrence_seconds == 600
        assert task.notify_on_change_only == 1
        assert task.max_occurrences == 3
        assert task.recurrence_until == 5000.0
        assert task.occurrence_count == 0
        assert task.last_result_json is None
    print("test_create_task_with_recurrence_params OK")


def test_reschedule_occurrence_reschedules_and_increments_count():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("veja o preço", "Monitorar", run_at=1000.0, recurrence_seconds=600)
        ok = store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="sem mudança", last_result_json='{"price": 450}')
        assert ok is True
        task = store.load_task(task_id)
        assert task.status == scheduler_store.STATUS_PENDING
        assert task.run_at == 1600.0
        assert task.occurrence_count == 1
        assert task.last_result_json == '{"price": 450}'
    print("test_reschedule_occurrence_reschedules_and_increments_count OK")


def test_reschedule_occurrence_terminates_at_max_occurrences():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("veja 3x", "Monitorar 3x", run_at=1000.0, recurrence_seconds=600, max_occurrences=2)
        assert store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="1a checagem") is True
        assert store.load_task(task_id).status == scheduler_store.STATUS_PENDING
        assert store.reschedule_occurrence(task_id, next_run_at=2200.0, summary="2a checagem, parando") is True
        task = store.load_task(task_id)
        assert task.status == scheduler_store.STATUS_DONE, "bateu max_occurrences=2 na 2a ocorrência — deve terminar"
        assert task.occurrence_count == 2
        assert task.result_summary == "2a checagem, parando"
    print("test_reschedule_occurrence_terminates_at_max_occurrences OK")


def test_reschedule_occurrence_terminates_past_recurrence_until():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task(
            "veja até o prazo", "Monitorar com prazo", run_at=1000.0, recurrence_seconds=600, recurrence_until=1500.0,
        )
        # next_run_at (1600.0) já passaria do prazo (1500.0) — deve terminar em vez de reagendar.
        ok = store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="prazo esgotado")
        assert ok is True
        assert store.load_task(task_id).status == scheduler_store.STATUS_DONE
    print("test_reschedule_occurrence_terminates_past_recurrence_until OK")


def test_reschedule_occurrence_never_erases_last_result_json_when_omitted():
    # Regressão: uma ocorrência sem result_data (falha sem done_tool, ou a
    # IA simplesmente omitiu o campo opcional) não pode apagar o snapshot
    # válido salvo pela ocorrência anterior — senão a próxima checagem
    # perde a base de comparação.
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("veja o preço", "Monitorar", run_at=1000.0, recurrence_seconds=600)
        store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="1a checagem", last_result_json='{"price": 450}')
        assert store.load_task(task_id).last_result_json == '{"price": 450}'

        # 2a ocorrência sem result_data (ex: falhou antes de chamar done_tool).
        store.reschedule_occurrence(task_id, next_run_at=2200.0, summary="falhou", success=False)
        task = store.load_task(task_id)
        assert task.last_result_json == '{"price": 450}', "não deveria ter sido apagado por um valor omitido"
        assert task.occurrence_count == 2
    print("test_reschedule_occurrence_never_erases_last_result_json_when_omitted OK")


def test_reschedule_occurrence_termination_respects_success_flag():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("veja 1x", "Monitorar 1x", run_at=1000.0, recurrence_seconds=600, max_occurrences=1)
        store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="falhou na última checagem", success=False)
        task = store.load_task(task_id)
        assert task.status == scheduler_store.STATUS_FAILED, "última ocorrência falhou — não pode terminar como 'done'"
        assert task.occurrence_count == 1
    print("test_reschedule_occurrence_termination_respects_success_flag OK")


def test_reschedule_occurrence_noop_on_non_pending_task():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        task_id = store.create_task("x", "X", run_at=1000.0, recurrence_seconds=600)
        store.cancel_task(task_id)
        ok = store.reschedule_occurrence(task_id, next_run_at=1600.0, summary="não deveria rodar")
        assert ok is False
        assert store.load_task(task_id).status == scheduler_store.STATUS_CANCELLED
    print("test_reschedule_occurrence_noop_on_non_pending_task OK")


def test_get_missed_tasks_excludes_recurring_tasks():
    with _TempDb() as db_path:
        store = SchedulerStore(db_path)
        one_shot = store.create_task("x", "Única", run_at=100.0)
        recurring = store.create_task("y", "Recorrente", run_at=100.0, recurrence_seconds=600)
        missed = store.get_missed_tasks(now=1000.0, grace_seconds=60)
        missed_ids = {t.id for t in missed}
        assert one_shot in missed_ids
        assert recurring not in missed_ids, "tarefa recorrente nunca deve entrar na revisão de perdidas"
    print("test_get_missed_tasks_excludes_recurring_tasks OK")


if __name__ == "__main__":
    test_create_and_load_task_roundtrip()
    test_load_missing_task_returns_none()
    test_get_due_tasks_only_returns_pending_past_run_at()
    test_get_due_tasks_excludes_cancelled_and_done()
    test_get_missed_tasks_requires_grace_period_elapsed()
    test_cancel_task_only_affects_pending()
    test_mark_done_success_and_failure()
    test_mark_done_never_overwrites_a_cancelled_task()
    test_mark_missed_only_affects_pending()
    test_delete_task()
    test_list_tasks_filters_by_status_and_orders_by_run_at_desc()
    test_schema_creation_is_idempotent()
    test_migrates_old_schema_db_without_recurrence_columns()
    test_create_task_with_recurrence_params()
    test_reschedule_occurrence_reschedules_and_increments_count()
    test_reschedule_occurrence_terminates_at_max_occurrences()
    test_reschedule_occurrence_terminates_past_recurrence_until()
    test_reschedule_occurrence_never_erases_last_result_json_when_omitted()
    test_reschedule_occurrence_termination_respects_success_flag()
    test_reschedule_occurrence_noop_on_non_pending_task()
    test_get_missed_tasks_excludes_recurring_tasks()
    print("\nTODOS OS TESTES PASSARAM")
