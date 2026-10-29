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


if __name__ == "__main__":
    test_create_and_load_task_roundtrip()
    test_load_missing_task_returns_none()
    test_get_due_tasks_only_returns_pending_past_run_at()
    test_get_due_tasks_excludes_cancelled_and_done()
    test_get_missed_tasks_requires_grace_period_elapsed()
    test_cancel_task_only_affects_pending()
    test_mark_done_success_and_failure()
    test_mark_missed_only_affects_pending()
    test_delete_task()
    test_list_tasks_filters_by_status_and_orders_by_run_at_desc()
    test_schema_creation_is_idempotent()
    print("\nTODOS OS TESTES PASSARAM")
