# -*- coding: utf-8 -*-
"""
Testa action_tools.py isolado: as function_declarations das tools e a
validação de argumentos. Não depende de rede nem do NVDA.

Rodar: python tests/test_action_tools.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

action_tools = load_addon_module("action_tools")


def test_all_tools_registered():
    # include_shell=True pra conferir TODAS as tools conhecidas de uma vez —
    # por padrão shell_tool não é oferecida ao modelo (ver get_tool()).
    tool = action_tools.get_tool(include_shell=True)
    names = {fd.name for fd in tool.function_declarations}
    assert names == set(action_tools.TOOL_NAME_TO_ACTION_TYPE), names
    print("test_all_tools_registered OK")


def test_shell_tool_excluded_by_default():
    tool = action_tools.get_tool()
    names = {fd.name for fd in tool.function_declarations}
    assert "shell_tool" not in names
    assert "click_tool" in names
    print("test_shell_tool_excluded_by_default OK")


def test_required_args_match_schema():
    tool = action_tools.get_tool(include_shell=True)
    for fd in tool.function_declarations:
        expected = set(action_tools.REQUIRED_ARGS_BY_TOOL[fd.name])
        actual = set(fd.parameters.required or [])
        assert expected == actual, f"{fd.name}: schema={actual} REQUIRED_ARGS_BY_TOOL={expected}"
    print("test_required_args_match_schema OK")


def test_validate_args_missing_required():
    err = action_tools.validate_args("click_tool", {"x": 1})
    assert err and "y" in err
    print("test_validate_args_missing_required OK")


def test_validate_args_valid():
    assert action_tools.validate_args("click_tool", {"x": 1, "y": 2}) is None
    assert action_tools.validate_args("wait_tool", {}) is None  # sem required
    assert action_tools.validate_args("done_tool", {"summary": "ok", "success": True}) is None
    assert action_tools.validate_args("done_tool", {"summary": "ok", "success": False}) is None
    print("test_validate_args_valid OK")


def test_validate_args_scroll_direction_enum():
    assert action_tools.validate_args("scroll_tool", {"direction": "up"}) is None
    assert action_tools.validate_args("scroll_tool", {"direction": "down"}) is None
    err = action_tools.validate_args("scroll_tool", {"direction": "sideways"})
    assert err is not None
    print("test_validate_args_scroll_direction_enum OK")


def test_validate_args_unknown_tool():
    err = action_tools.validate_args("delete_everything_tool", {})
    assert err is not None
    print("test_validate_args_unknown_tool OK")


def test_validate_args_schedule_task_rejects_malformed_run_at():
    err = action_tools.validate_args(
        "schedule_task_tool", {"command": "x", "title": "X", "run_at": "não é uma data"}
    )
    assert err and "run_at" in err
    print("test_validate_args_schedule_task_rejects_malformed_run_at OK")


def test_validate_args_schedule_task_rejects_past_run_at():
    err = action_tools.validate_args(
        "schedule_task_tool", {"command": "x", "title": "X", "run_at": "2000-01-01T00:00:00"}
    )
    assert err and "future" in err
    print("test_validate_args_schedule_task_rejects_past_run_at OK")


def test_validate_args_schedule_task_accepts_future_iso_datetime():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    err = action_tools.validate_args(
        "schedule_task_tool", {"command": "x", "title": "X", "run_at": future}
    )
    assert err is None
    print("test_validate_args_schedule_task_accepts_future_iso_datetime OK")


def test_validate_args_new_support_tools():
    assert action_tools.validate_args("list_scheduled_tasks_tool", {}) is None
    assert action_tools.validate_args("cancel_scheduled_task_tool", {"task_id": 1}) is None
    err = action_tools.validate_args("cancel_scheduled_task_tool", {})
    assert err and "task_id" in err
    print("test_validate_args_new_support_tools OK")


def test_validate_args_app_tool_and_ask_user_tool():
    assert action_tools.validate_args("app_tool", {"app_name": "notepad"}) is None
    assert action_tools.validate_args("app_tool", {}) is not None
    assert action_tools.validate_args("ask_user_tool", {"question": "Qual opção?"}) is None
    assert action_tools.validate_args("ask_user_tool", {}) is not None
    print("test_validate_args_app_tool_and_ask_user_tool OK")


def test_validate_args_shell_tool_requires_command_and_reason():
    assert action_tools.validate_args("shell_tool", {"command": "dir", "reason": "listar arquivos"}) is None
    err = action_tools.validate_args("shell_tool", {"command": "dir"})
    assert err and "reason" in err
    print("test_validate_args_shell_tool_requires_command_and_reason OK")


def test_validate_args_record_note_tool():
    assert action_tools.validate_args("record_note_tool", {"note": "preço R$450"}) is None
    err = action_tools.validate_args("record_note_tool", {})
    assert err and "note" in err
    print("test_validate_args_record_note_tool OK")


def test_validate_args_schedule_task_recurrence_minutes_optional_and_valid():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    base = {"command": "x", "title": "X", "run_at": future}
    # Sem recurrence_minutes (tarefa única) continua válido, como antes.
    assert action_tools.validate_args("schedule_task_tool", base) is None
    assert action_tools.validate_args("schedule_task_tool", {**base, "recurrence_minutes": 30}) is None
    assert action_tools.validate_args("schedule_task_tool", {**base, "recurrence_minutes": 0}) is None
    print("test_validate_args_schedule_task_recurrence_minutes_optional_and_valid OK")


def test_validate_args_schedule_task_recurrence_minutes_rejects_non_positive():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    base = {"command": "x", "title": "X", "run_at": future}
    err = action_tools.validate_args("schedule_task_tool", {**base, "recurrence_minutes": -5})
    assert err and "recurrence_minutes" in err
    print("test_validate_args_schedule_task_recurrence_minutes_rejects_non_positive OK")


def test_validate_args_schedule_task_max_occurrences_requires_recurrence():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    base = {"command": "x", "title": "X", "run_at": future}
    err = action_tools.validate_args("schedule_task_tool", {**base, "max_occurrences": 3})
    assert err and "recurrence_minutes" in err
    assert action_tools.validate_args(
        "schedule_task_tool", {**base, "recurrence_minutes": 10, "max_occurrences": 3}
    ) is None
    print("test_validate_args_schedule_task_max_occurrences_requires_recurrence OK")


def test_validate_args_schedule_task_max_occurrences_rejects_non_positive():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    base = {"command": "x", "title": "X", "run_at": future, "recurrence_minutes": 10}
    err = action_tools.validate_args("schedule_task_tool", {**base, "max_occurrences": 0})
    assert err and "max_occurrences" in err
    print("test_validate_args_schedule_task_max_occurrences_rejects_non_positive OK")


def test_validate_args_schedule_task_recurrence_until_requires_recurrence_and_future():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds")
    far_future = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
    base = {"command": "x", "title": "X", "run_at": future}

    err_no_recurrence = action_tools.validate_args(
        "schedule_task_tool", {**base, "recurrence_until": far_future}
    )
    assert err_no_recurrence and "recurrence_minutes" in err_no_recurrence

    err_past = action_tools.validate_args(
        "schedule_task_tool",
        {**base, "recurrence_minutes": 10, "recurrence_until": "2000-01-01T00:00:00"},
    )
    assert err_past and "future" in err_past

    assert action_tools.validate_args(
        "schedule_task_tool", {**base, "recurrence_minutes": 10, "recurrence_until": far_future}
    ) is None
    print("test_validate_args_schedule_task_recurrence_until_requires_recurrence_and_future OK")


def test_validate_args_done_tool_accepts_recurring_optional_fields():
    base = {"summary": "preço verificado", "success": True}
    assert action_tools.validate_args("done_tool", {**base, "changed": True, "result_data": "price=450"}) is None
    assert action_tools.validate_args("done_tool", {**base, "changed": False, "stop_recurrence": True}) is None
    # Sem nenhum dos campos opcionais, continua válido (tarefa comum).
    assert action_tools.validate_args("done_tool", base) is None
    print("test_validate_args_done_tool_accepts_recurring_optional_fields OK")


def test_validate_args_long_wait_tool_missing_seconds():
    err = action_tools.validate_args("long_wait_tool", {})
    assert err and "seconds" in err
    print("test_validate_args_long_wait_tool_missing_seconds OK")


def test_validate_args_long_wait_tool_rejects_non_positive_seconds():
    err_zero = action_tools.validate_args("long_wait_tool", {"seconds": 0})
    assert err_zero and "seconds" in err_zero
    err_negative = action_tools.validate_args("long_wait_tool", {"seconds": -10})
    assert err_negative and "seconds" in err_negative
    print("test_validate_args_long_wait_tool_rejects_non_positive_seconds OK")


def test_validate_args_long_wait_tool_rejects_non_numeric_seconds():
    err = action_tools.validate_args("long_wait_tool", {"seconds": "logo"})
    assert err and "seconds" in err
    print("test_validate_args_long_wait_tool_rejects_non_numeric_seconds OK")


def test_validate_args_long_wait_tool_accepts_valid_seconds():
    assert action_tools.validate_args("long_wait_tool", {"seconds": 90}) is None
    print("test_validate_args_long_wait_tool_accepts_valid_seconds OK")


if __name__ == "__main__":
    test_all_tools_registered()
    test_shell_tool_excluded_by_default()
    test_required_args_match_schema()
    test_validate_args_missing_required()
    test_validate_args_valid()
    test_validate_args_scroll_direction_enum()
    test_validate_args_unknown_tool()
    test_validate_args_schedule_task_rejects_malformed_run_at()
    test_validate_args_schedule_task_rejects_past_run_at()
    test_validate_args_schedule_task_accepts_future_iso_datetime()
    test_validate_args_new_support_tools()
    test_validate_args_app_tool_and_ask_user_tool()
    test_validate_args_shell_tool_requires_command_and_reason()
    test_validate_args_record_note_tool()
    test_validate_args_schedule_task_recurrence_minutes_optional_and_valid()
    test_validate_args_schedule_task_recurrence_minutes_rejects_non_positive()
    test_validate_args_schedule_task_max_occurrences_requires_recurrence()
    test_validate_args_schedule_task_max_occurrences_rejects_non_positive()
    test_validate_args_schedule_task_recurrence_until_requires_recurrence_and_future()
    test_validate_args_done_tool_accepts_recurring_optional_fields()
    test_validate_args_long_wait_tool_missing_seconds()
    test_validate_args_long_wait_tool_rejects_non_positive_seconds()
    test_validate_args_long_wait_tool_rejects_non_numeric_seconds()
    test_validate_args_long_wait_tool_accepts_valid_seconds()
    print("\nTODOS OS TESTES PASSARAM")
