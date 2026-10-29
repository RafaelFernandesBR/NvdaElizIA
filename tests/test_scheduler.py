# -*- coding: utf-8 -*-
"""
Testa a parte de scheduler.py que não depende de timer/dialog de verdade:
TaskScheduler._check_due_tasks (polling puro, recebe o store e o callback
injetados) e seconds_since_last_input (ctypes/user32 real — funciona fora
do NVDA, é só uma API do Windows). start()/stop()/_on_tick (timer de
verdade, gui.NonReEntrantTimer) não têm teste automatizado, mesmo motivo de
sessions_dialog.py/chat_dialog.py — só manual, dentro do NVDA.

`wx`/`gui` são stubados aqui (minimamente) só pra poder importar o módulo
fora do NVDA — nenhum teste abaixo chama start()/stop(), então os stubs
nunca precisam fazer nada além de existir.

Rodar: python tests/test_scheduler.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

if "wx" not in sys.modules:
    wx_stub = types.ModuleType("wx")
    wx_stub.CallAfter = lambda fn, *a, **kw: fn(*a, **kw)
    sys.modules["wx"] = wx_stub

if "gui" not in sys.modules:
    sys.modules["gui"] = types.ModuleType("gui")

scheduler = load_addon_module("scheduler")

TaskScheduler = scheduler.TaskScheduler
seconds_since_last_input = scheduler.seconds_since_last_input


class _FakeStore:
    def __init__(self, due_tasks):
        self._due_tasks = due_tasks

    def get_due_tasks(self, now):
        return self._due_tasks


class _FakeTask:
    def __init__(self, id_):
        self.id = id_


def test_check_due_tasks_calls_callback_once_per_task():
    tasks = [_FakeTask(1), _FakeTask(2), _FakeTask(3)]
    seen = []
    ts = TaskScheduler(_FakeStore(tasks), on_due_task=lambda t: seen.append(t.id), check_interval_provider=lambda: 30)
    ts._check_due_tasks()
    assert seen == [1, 2, 3]
    print("test_check_due_tasks_calls_callback_once_per_task OK")


def test_check_due_tasks_with_no_due_tasks_calls_nothing():
    seen = []
    ts = TaskScheduler(_FakeStore([]), on_due_task=lambda t: seen.append(t.id), check_interval_provider=lambda: 30)
    ts._check_due_tasks()
    assert seen == []
    print("test_check_due_tasks_with_no_due_tasks_calls_nothing OK")


def test_check_due_tasks_one_callback_exception_does_not_stop_the_rest():
    tasks = [_FakeTask(1), _FakeTask(2), _FakeTask(3)]
    seen = []

    def on_due(task):
        if task.id == 2:
            raise RuntimeError("falha simulada")
        seen.append(task.id)

    ts = TaskScheduler(_FakeStore(tasks), on_due_task=on_due, check_interval_provider=lambda: 30)
    ts._check_due_tasks()  # não deve lançar
    assert seen == [1, 3]
    print("test_check_due_tasks_one_callback_exception_does_not_stop_the_rest OK")


def test_check_due_tasks_store_exception_is_swallowed():
    class _BrokenStore:
        def get_due_tasks(self, now):
            raise RuntimeError("banco indisponível")

    ts = TaskScheduler(_BrokenStore(), on_due_task=lambda t: None, check_interval_provider=lambda: 30)
    ts._check_due_tasks()  # não deve lançar
    print("test_check_due_tasks_store_exception_is_swallowed OK")


def test_seconds_since_last_input_returns_non_negative_float_or_none():
    result = seconds_since_last_input()
    assert result is None or (isinstance(result, float) and result >= 0.0)
    print("test_seconds_since_last_input_returns_non_negative_float_or_none OK")


if __name__ == "__main__":
    test_check_due_tasks_calls_callback_once_per_task()
    test_check_due_tasks_with_no_due_tasks_calls_nothing()
    test_check_due_tasks_one_callback_exception_does_not_stop_the_rest()
    test_check_due_tasks_store_exception_is_swallowed()
    test_seconds_since_last_input_returns_non_negative_float_or_none()
    print("\nTODOS OS TESTES PASSARAM")
