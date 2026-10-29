# -*- coding: utf-8 -*-
"""
Testa action_processor.py isolado: planejamento de lote de function_calls
(BatchPlan/BatchItem) e o circuit breaker de loop. Usa google.genai.types.FunctionCall
de verdade (via SDK bundled), mas nenhuma chamada de rede.

Rodar: python tests/test_action_processor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

addon_config = load_addon_module("addon_config")
action_tools = load_addon_module("action_tools")
action_processor = load_addon_module("action_processor")

from google.genai import types as genai_types

AP = action_processor.ActionProcessor


def fc(name, args=None, id_=None):
    return genai_types.FunctionCall(name=name, args=args or {}, id=id_)


def test_empty_function_calls_is_anomalous_not_success():
    p = AP.process_function_calls([], 1920, 1080)
    assert p.items == [] and p.is_finished is False
    print("test_empty_function_calls_is_anomalous_not_success OK")


def test_done_alone_finishes():
    p = AP.process_function_calls(
        [fc("done_tool", {"summary": "tudo certo", "success": True})], 1920, 1080
    )
    assert p.is_finished is True and p.explanation == "tudo certo"
    assert p.success is True
    assert p.items[0].kind == "skip" and p.items[0].response["success"] is True
    print("test_done_alone_finishes OK")


def test_done_alone_with_success_false():
    p = AP.process_function_calls(
        [fc("done_tool", {"summary": "não encontrei o botão", "success": False})], 1920, 1080
    )
    assert p.is_finished is True and p.explanation == "não encontrei o botão"
    assert p.success is False
    assert p.items[0].kind == "skip" and p.items[0].response["success"] is False
    print("test_done_alone_with_success_false OK")


def test_done_without_success_arg_is_rejected():
    # success agora é obrigatório — sem ele, validate_args deve recusar em
    # vez de assumir um default silencioso.
    p = AP.process_function_calls([fc("done_tool", {"summary": "tudo certo"})], 1920, 1080)
    assert p.is_finished is False
    assert p.items[0].kind == "skip" and p.items[0].response["success"] is False
    assert "success" in p.items[0].response["error"]
    print("test_done_without_success_arg_is_rejected OK")


def test_done_plus_other_tool_rejected():
    p = AP.process_function_calls(
        [fc("done_tool", {"summary": "x"}), fc("click_tool", {"x": 1, "y": 2})], 1920, 1080
    )
    assert p.is_finished is False
    assert all(it.kind == "skip" and it.response["success"] is False for it in p.items)
    print("test_done_plus_other_tool_rejected OK")


def test_batch_plans_multiple_executable_items():
    calls = [fc("click_tool", {"x": 500, "y": 500}), fc("type_tool", {"text": "oi", "press_enter": True})]
    p = AP.process_function_calls(calls, 1920, 1080)
    assert all(it.kind == "execute" for it in p.items)
    assert p.items[0].action_info.action_type == "click"
    assert p.items[1].action_info.press_enter is True
    print("test_batch_plans_multiple_executable_items OK")


def test_batch_truncated_beyond_max_actions_per_turn():
    calls = [fc("keyboard_tool", {"keys": "enter"}) for _ in range(5)]
    p = AP.process_function_calls(calls, 1920, 1080)
    kinds = [it.kind for it in p.items]
    assert kinds == ["execute"] * addon_config.MAX_ACTIONS_PER_TURN + ["skip"] * (5 - addon_config.MAX_ACTIONS_PER_TURN)
    for it in p.items[addon_config.MAX_ACTIONS_PER_TURN:]:
        assert it.response.get("truncated") is True
    print("test_batch_truncated_beyond_max_actions_per_turn OK")


def test_invalid_item_does_not_default_to_click():
    # action ausente/invalida NUNCA deve virar clique silencioso (bug do
    # design antigo em prosa-JSON que este redesenho elimina)
    p = AP.process_function_calls([fc("click_tool", {"x": 1})], 1920, 1080)  # falta y
    assert p.items[0].kind == "skip"
    assert p.items[0].action_info is None
    print("test_invalid_item_does_not_default_to_click OK")


def test_loop_breaker_hard_stop_after_threshold():
    AP._last_actions = []
    sig = "click(100,100)"
    results = [AP.is_loop_detected(sig, screen_changed=False) for _ in range(addon_config.LOOP_DETECTION_THRESHOLD)]
    assert results[:-1] == [False] * (len(results) - 1)
    assert results[-1] is True
    print("test_loop_breaker_hard_stop_after_threshold OK")


def test_loop_breaker_resets_when_screen_changes():
    AP._last_actions = []
    sig = "scroll:down:3"
    for _ in range(addon_config.LOOP_DETECTION_THRESHOLD - 1):
        assert AP.is_loop_detected(sig, screen_changed=False) is False
    assert AP.is_loop_detected(sig, screen_changed=True) is False  # zera a sequencia
    for _ in range(addon_config.LOOP_DETECTION_THRESHOLD - 1):
        assert AP.is_loop_detected(sig, screen_changed=False) is False
    assert AP.is_loop_detected(sig, screen_changed=False) is True  # agora completa o threshold de novo
    print("test_loop_breaker_resets_when_screen_changes OK")


def test_support_tool_resolved_immediately_via_injected_handler():
    calls = [fc("schedule_task_tool", {"command": "x", "title": "X", "run_at": "2999-01-01T00:00:00"})]
    handler_calls = []

    def fake_handler(args):
        handler_calls.append(args)
        return {"success": True, "message": "Tarefa agendada.", "task_id": 7}

    p = AP.process_function_calls(calls, 1920, 1080, support_handlers={"schedule_task_tool": fake_handler})
    assert len(handler_calls) == 1
    assert p.items[0].kind == "support"
    assert p.items[0].response == {"success": True, "message": "Tarefa agendada.", "task_id": 7}
    assert p.is_finished is False
    print("test_support_tool_resolved_immediately_via_injected_handler OK")


def test_support_tool_without_handler_fails_gracefully():
    calls = [fc("list_scheduled_tasks_tool", {})]
    p = AP.process_function_calls(calls, 1920, 1080)  # sem support_handlers
    assert p.items[0].kind == "support"
    assert p.items[0].response["success"] is False
    print("test_support_tool_without_handler_fails_gracefully OK")


def test_support_tool_can_batch_with_physical_action():
    calls = [
        fc("schedule_task_tool", {"command": "x", "title": "X", "run_at": "2999-01-01T00:00:00"}),
        fc("click_tool", {"x": 1, "y": 2}),
    ]
    p = AP.process_function_calls(
        calls, 1920, 1080,
        support_handlers={"schedule_task_tool": lambda args: {"success": True}},
    )
    assert [it.kind for it in p.items] == ["support", "execute"]
    print("test_support_tool_can_batch_with_physical_action OK")


def test_ask_user_tool_alone_sets_awaiting_question():
    p = AP.process_function_calls([fc("ask_user_tool", {"question": "Qual delas?"})], 1920, 1080)
    assert p.awaiting_question == "Qual delas?"
    assert p.is_finished is False
    assert p.items[0].kind == "skip" and p.items[0].response is None
    print("test_ask_user_tool_alone_sets_awaiting_question OK")


def test_ask_user_tool_plus_other_tool_rejected():
    p = AP.process_function_calls(
        [fc("ask_user_tool", {"question": "x"}), fc("click_tool", {"x": 1, "y": 2})], 1920, 1080
    )
    assert p.awaiting_question is None
    assert all(it.kind == "skip" and it.response["success"] is False for it in p.items)
    print("test_ask_user_tool_plus_other_tool_rejected OK")


def test_launch_app_and_shell_command_have_distinct_signatures():
    notepad = AP._build_action_info("launch_app", {"app_name": "notepad"}, 1920, 1080)
    calc = AP._build_action_info("launch_app", {"app_name": "calc"}, 1920, 1080)
    assert notepad.signature() != calc.signature()

    cmd1 = AP._build_action_info("shell_command", {"command": "dir", "reason": "listar"}, 1920, 1080)
    cmd2 = AP._build_action_info("shell_command", {"command": "ipconfig", "reason": "ver rede"}, 1920, 1080)
    assert cmd1.signature() != cmd2.signature()
    print("test_launch_app_and_shell_command_have_distinct_signatures OK")


def test_support_items_never_trip_circuit_breaker_in_run_batch():
    # Mesmo 3 schedule_task_tool "sem efeito" seguidos nunca deveriam
    # disparar o circuit breaker — são kind="support", run_batch nem olha
    # pra eles além de pular (ver run_batch).
    AP._last_actions = []
    items = [
        AP.process_function_calls(
            [fc("schedule_task_tool", {"command": "x", "title": "X", "run_at": "2999-01-01T00:00:00"})],
            1920, 1080, support_handlers={"schedule_task_tool": lambda args: {"success": True}},
        ).items[0]
        for _ in range(5)
    ]
    loop_broken = action_processor.ActionProcessor.run_batch(
        items, "h0", lambda action_info: (True, None), lambda: "h0"
    )
    assert loop_broken is False
    print("test_support_items_never_trip_circuit_breaker_in_run_batch OK")


if __name__ == "__main__":
    test_empty_function_calls_is_anomalous_not_success()
    test_done_alone_finishes()
    test_done_alone_with_success_false()
    test_done_without_success_arg_is_rejected()
    test_done_plus_other_tool_rejected()
    test_batch_plans_multiple_executable_items()
    test_batch_truncated_beyond_max_actions_per_turn()
    test_invalid_item_does_not_default_to_click()
    test_loop_breaker_hard_stop_after_threshold()
    test_loop_breaker_resets_when_screen_changes()
    test_support_tool_resolved_immediately_via_injected_handler()
    test_support_tool_without_handler_fails_gracefully()
    test_support_tool_can_batch_with_physical_action()
    test_ask_user_tool_alone_sets_awaiting_question()
    test_ask_user_tool_plus_other_tool_rejected()
    test_launch_app_and_shell_command_have_distinct_signatures()
    test_support_items_never_trip_circuit_breaker_in_run_batch()
    print("\nTODOS OS TESTES PASSARAM")
