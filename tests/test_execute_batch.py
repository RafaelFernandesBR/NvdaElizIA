# -*- coding: utf-8 -*-
"""
Testa ActionProcessor.run_batch — a lógica de sequenciar/abortar lote de
ações / circuit breaker de loop usada por GlobalPlugin._execute_batch em
__init__.py. É o CÓDIGO REAL (não uma réplica): run_batch recebe
`execute_fn`/`capture_fn` como parâmetros exatamente pra poder ser testado
aqui com dublês simples, sem precisar do NVDA/mouse/teclado de verdade.

Rodar: python tests/test_execute_batch.py
"""
import os
import sys
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module

action_processor = load_addon_module("action_processor")
AP = action_processor.ActionProcessor
BatchItem = action_processor.BatchItem


def h(raw):
    """Mesma convenção usada em __init__.py: hash do base64 'cru' da tela."""
    return hashlib.md5(raw.encode()).hexdigest()


class FakeActionInfo:
    def __init__(self, sig):
        self._sig = sig

    def signature(self):
        return self._sig


def make_execute_fn(results):
    it = iter(results)
    return lambda action_info: next(it)


def make_capture_fn(screenshots):
    it = iter(screenshots)
    return lambda: next(it)


def test_screen_change_on_first_item_aborts_rest_even_on_success():
    # Mudança de tela == estado ficou obsoleto para o resto do lote (mesma
    # semântica do browser-use: qualquer mudança aborta o restante), mesmo
    # que a ação em si tenha "funcionado" (success=True).
    AP._last_actions = []
    items = [BatchItem("click_tool", None, "execute", action_info=FakeActionInfo(f"click({i},{i})")) for i in range(3)]
    loop_broken = AP.run_batch(
        items, h("h0"), make_execute_fn([(True, None)] * 3), make_capture_fn(["h1", "h2", "h3"])
    )
    assert loop_broken is False
    assert items[0].response["success"] is True and items[0].response["screen_changed"] is True
    assert items[1].response["success"] is False and "Ignorado" in items[1].response["error"]
    assert items[2].response["success"] is False
    print("test_screen_change_on_first_item_aborts_rest_even_on_success OK")


def test_fill_fields_then_submit_no_change_until_last_action():
    # Caso de uso real do lote: clicar um campo, digitar, confirmar — tudo
    # na MESMA tela visível, sem mudança até a última ação (submit).
    AP._last_actions = []
    items = [
        BatchItem("click_tool", None, "execute", action_info=FakeActionInfo("click(300,300)")),
        BatchItem("type_tool", None, "execute", action_info=FakeActionInfo("type")),
        BatchItem("keyboard_tool", None, "execute", action_info=FakeActionInfo("keyboard:enter")),
    ]
    loop_broken = AP.run_batch(
        items, h("h0"), make_execute_fn([(True, None)] * 3), make_capture_fn(["h0", "h0", "h1"])
    )
    assert loop_broken is False
    assert all(it.response["success"] for it in items)
    assert items[0].response["screen_changed"] is False
    assert items[1].response["screen_changed"] is False
    assert items[2].response["screen_changed"] is True
    print("test_fill_fields_then_submit_no_change_until_last_action OK")


def test_failed_action_aborts_rest_of_batch():
    AP._last_actions = []
    items = [
        BatchItem("click_tool", None, "execute", action_info=FakeActionInfo("click(1,1)")),
        BatchItem("type_tool", None, "execute", action_info=FakeActionInfo("type")),
        BatchItem("keyboard_tool", None, "execute", action_info=FakeActionInfo("keyboard:enter")),
    ]
    loop_broken = AP.run_batch(
        items, h("h0"),
        make_execute_fn([(True, None), (False, "campo não encontrado")]),
        make_capture_fn(["h1", "h1"]),
    )
    assert loop_broken is False
    assert items[0].response["success"] is True
    assert items[1].response["success"] is False
    assert items[2].response["success"] is False
    assert "Ignorado" in items[2].response["error"]
    print("test_failed_action_aborts_rest_of_batch OK")


def test_screen_unchanged_and_success_does_not_abort():
    # Sem mudança de tela E sem falha -> o lote CONTINUA normalmente (quem
    # aborta por "sem mudança repetida" é o circuit breaker de loop, não o
    # abort-on-change do lote).
    AP._last_actions = []
    items = [
        BatchItem("scroll_tool", None, "execute", action_info=FakeActionInfo("scroll:down:3")),
        BatchItem("click_tool", None, "execute", action_info=FakeActionInfo("click(5,5)")),
    ]
    loop_broken = AP.run_batch(
        items, h("h0"), make_execute_fn([(True, None), (True, None)]), make_capture_fn(["h0", "h2"])
    )
    assert loop_broken is False
    assert items[0].response["screen_changed"] is False
    assert items[1].response["success"] is True
    print("test_screen_unchanged_and_success_does_not_abort OK")


def test_circuit_breaker_stops_mid_batch():
    AP._last_actions = []
    items = [BatchItem("click_tool", None, "execute", action_info=FakeActionInfo("click(9,9)")) for _ in range(3)]
    loop_broken = AP.run_batch(
        items, h("h0"), make_execute_fn([(True, None)] * 3), make_capture_fn(["h0", "h0", "h0"])
    )
    assert loop_broken is True
    print("test_circuit_breaker_stops_mid_batch OK")


def test_validation_error_item_aborts_remaining_execute_items():
    AP._last_actions = []
    invalid_item = BatchItem("click_tool", None, "skip", {"success": False, "error": "Parâmetro(s) obrigatório(s) faltando"})
    exec_item = BatchItem("keyboard_tool", None, "execute", action_info=FakeActionInfo("keyboard:enter"))
    items = [invalid_item, exec_item]
    loop_broken = AP.run_batch(items, h("h0"), make_execute_fn([]), make_capture_fn([]))
    assert loop_broken is False
    assert exec_item.response["success"] is False
    assert "Ignorado" in exec_item.response["error"]
    print("test_validation_error_item_aborts_remaining_execute_items OK")


def test_truncated_item_does_not_abort_anything():
    AP._last_actions = []
    exec_item = BatchItem("keyboard_tool", None, "execute", action_info=FakeActionInfo("keyboard:enter"))
    truncated = BatchItem("click_tool", None, "skip", {"success": False, "error": "Ignorado: limite de 3 ações por turno excedido."})
    items = [exec_item, truncated]
    AP.run_batch(items, h("h0"), make_execute_fn([(True, None)]), make_capture_fn(["h1"]))
    assert exec_item.response["success"] is True
    print("test_truncated_item_does_not_abort_anything OK")


if __name__ == "__main__":
    test_screen_change_on_first_item_aborts_rest_even_on_success()
    test_fill_fields_then_submit_no_change_until_last_action()
    test_failed_action_aborts_rest_of_batch()
    test_screen_unchanged_and_success_does_not_abort()
    test_circuit_breaker_stops_mid_batch()
    test_validation_error_item_aborts_remaining_execute_items()
    test_truncated_item_does_not_abort_anything()
    print("\nTODOS OS TESTES PASSARAM")
