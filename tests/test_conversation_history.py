# -*- coding: utf-8 -*-
"""
Testa utils.ConversationHistory isolada: confirma que `build_messages_for_api`
nunca devolve mais que 3 Content (O(1) por turno, não O(n)), que a janela
do log textual corta corretamente, e que done_tool/circuit breaker nunca
deixam uma function_call órfã. Não depende de rede nem do NVDA.

Rodar: python tests/test_conversation_history.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module, stub_nvda_modules

stub_nvda_modules()  # utils.py importa `api` no nível do módulo

addon_config = load_addon_module("addon_config")
action_tools = load_addon_module("action_tools")
action_processor = load_addon_module("action_processor")
utils = load_addon_module("utils")

from google.genai import types as gt

BatchItem = action_processor.BatchItem


class FakeActionInfo:
    def __init__(self, action_type, **kw):
        self.action_type = action_type
        self.x = kw.get("x")
        self.y = kw.get("y")
        self.text = kw.get("text", "")
        self.keys = kw.get("keys", "")
        self.direction = kw.get("direction", "down")
        self.amount = kw.get("amount", 3)
        self.seconds = kw.get("seconds", 0)


def make_batch(n):
    item = BatchItem("click_tool", None, "execute", action_info=FakeActionInfo("click", x=10 * n, y=20 * n))
    item.response = {"success": True, "error": None, "screen_changed": bool(n % 2)}
    return [item]


def fake_model_content(n):
    return gt.Content(role="model", parts=[gt.Part.from_function_call(name="click_tool", args={"x": n, "y": n})])


def test_turn0_no_model_content_single_content():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: abrir bloco de notas")
    msgs = ch.build_messages_for_api([gt.Part(text="screenshot aqui")])
    assert len(msgs) == 1 and msgs[0].role == "user"
    print("test_turn0_no_model_content_single_content OK")


def test_payload_stays_bounded_across_turns():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: X")
    for turn in range(1, 6):
        current_parts = [gt.Part(text=f"info turno {turn}"), gt.Part(text=f"screenshot#{turn}")]
        msgs = ch.build_messages_for_api(current_parts)
        assert len(msgs) <= 3, f"turno {turn}: {len(msgs)} Content (esperado <=3 — o payload não pode crescer)"
        if turn > 1:
            assert [m.role for m in msgs] == ["user", "model", "user"]
        ch.record_step(make_batch(turn))
        ch.set_last_model_content(fake_model_content(turn))
    print("test_payload_stays_bounded_across_turns OK")


def test_history_log_windowed_keeps_first_and_last():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: Y")
    for turn in range(1, 15):
        ch.record_step(make_batch(turn))
    text = ch.history_log_text()
    assert "1. click" in text, "primeiro turno deveria sempre aparecer"
    assert "omitido" in text
    assert "14. click" in text, "turno mais recente deveria aparecer"
    print("test_history_log_windowed_keeps_first_and_last OK")


def test_done_tool_or_loop_break_never_leaves_orphan_function_call():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: Z")
    ch.set_last_model_content(fake_model_content(1))
    ch.record_step(make_batch(1))
    ch.set_last_model_content(None)  # done_tool ou circuit breaker zeram isso
    msgs = ch.build_messages_for_api([gt.Part(text="pergunta de continuação")])
    assert len(msgs) == 1 and msgs[0].role == "user", "não deveria sobrar function_call sem resposta"
    print("test_done_tool_or_loop_break_never_leaves_orphan_function_call OK")


def test_clear_resets_everything():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: W")
    ch.record_step(make_batch(1))
    ch.set_last_model_content(fake_model_content(1))
    ch.record_conversation_message("Você", "oi")
    ch.clear()
    assert ch.history_log_text() == ""
    assert len(ch.build_messages_for_api([gt.Part(text="novo")])) == 1
    assert ch.conversation_log() == []
    print("test_clear_resets_everything OK")


def test_record_conversation_message_and_conversation_log():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: organizar área de trabalho")
    ch.record_conversation_message("Você", "organize os itens por ordem alfabética")
    ch.record_conversation_message(addon_config.ADDON_NAME, "Os itens foram classificados por nome.")

    assert ch.conversation_log() == [
        {"sender": "Você", "message": "organize os itens por ordem alfabética"},
        {"sender": addon_config.ADDON_NAME, "message": "Os itens foram classificados por nome."},
    ]
    # Garantia central: a transcrição humana nunca toca o que vai pra API.
    assert ch.history_log_text() == ""
    msgs = ch.build_messages_for_api([gt.Part(text="screenshot")])
    assert len(msgs) == 1 and msgs[0].role == "user"
    print("test_record_conversation_message_and_conversation_log OK")


def test_to_dict_from_dict_roundtrip_conversation_log():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: Q")
    ch.record_conversation_message("Você", "comando")
    ch.record_conversation_message(addon_config.ADDON_NAME, "resposta")

    data = ch.to_dict()
    assert data["conversation_log"] == [
        {"sender": "Você", "message": "comando"},
        {"sender": addon_config.ADDON_NAME, "message": "resposta"},
    ]

    restored = utils.ConversationHistory.from_dict(data)
    assert restored.conversation_log() == ch.conversation_log()
    print("test_to_dict_from_dict_roundtrip_conversation_log OK")


def test_from_dict_without_conversation_log_key_defaults_to_empty():
    # Simula sessão antiga no disco (ou chamada de teste anterior a esse campo).
    restored = utils.ConversationHistory.from_dict({
        "task_text": "Tarefa: antiga",
        "log_entries": ["1. click -> ok, tela mudou"],
        "last_model_content": None,
    })
    assert restored.conversation_log() == []
    print("test_from_dict_without_conversation_log_key_defaults_to_empty OK")


def test_record_step_handles_unresolved_item_without_crashing():
    # Item que o circuit breaker cortou antes de executar (.response is None)
    ch = utils.ConversationHistory()
    unresolved = BatchItem("keyboard_tool", None, "execute", action_info=FakeActionInfo("keyboard", keys="enter"))
    unresolved.response = None
    ch.record_step([unresolved])  # não deve lançar exceção
    assert "keyboard" in ch.history_log_text()
    print("test_record_step_handles_unresolved_item_without_crashing OK")


def test_to_dict_from_dict_roundtrip_without_model_content():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: V")
    ch.record_step(make_batch(1))
    data = ch.to_dict()
    assert data["task_text"] == "Tarefa: V"
    assert len(data["log_entries"]) == 1 and data["log_entries"][0].startswith("1. click")
    assert data["last_model_content"] is None

    restored = utils.ConversationHistory.from_dict(data)
    assert restored.history_log_text() == ch.history_log_text()
    msgs = restored.build_messages_for_api([gt.Part(text="novo turno")])
    assert len(msgs) == 1 and msgs[0].role == "user"
    print("test_to_dict_from_dict_roundtrip_without_model_content OK")


def test_to_dict_from_dict_roundtrip_preserves_thought_signature():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: U")
    original_content = gt.Content(
        role="model",
        parts=[gt.Part(
            function_call=gt.FunctionCall(name="click_tool", args={"x": 1, "y": 2}),
            thought_signature=b"\x01\x02\x03",
        )],
    )
    ch.set_last_model_content(original_content)

    restored = utils.ConversationHistory.from_dict(ch.to_dict())

    original_dump = original_content.model_dump(mode="json")
    restored_dump = restored._last_model_content.model_dump(mode="json")
    assert restored_dump == original_dump, "thought_signature deve sobreviver byte-a-byte ao round-trip"
    print("test_to_dict_from_dict_roundtrip_preserves_thought_signature OK")


def test_from_dict_then_build_messages_for_api_behaves_like_live_history():
    live = utils.ConversationHistory()
    live.set_task_text("Tarefa: T")
    live.record_step(make_batch(1))
    live.set_last_model_content(fake_model_content(1))

    restored = utils.ConversationHistory.from_dict(live.to_dict())

    current_parts = [gt.Part(text="screenshot nova")]
    live_msgs = live.build_messages_for_api(current_parts)
    restored_msgs = restored.build_messages_for_api(current_parts)

    assert len(restored_msgs) == len(live_msgs) <= 3
    assert [m.role for m in restored_msgs] == [m.role for m in live_msgs]
    print("test_from_dict_then_build_messages_for_api_behaves_like_live_history OK")


if __name__ == "__main__":
    test_turn0_no_model_content_single_content()
    test_payload_stays_bounded_across_turns()
    test_history_log_windowed_keeps_first_and_last()
    test_done_tool_or_loop_break_never_leaves_orphan_function_call()
    test_clear_resets_everything()
    test_record_step_handles_unresolved_item_without_crashing()
    test_to_dict_from_dict_roundtrip_without_model_content()
    test_to_dict_from_dict_roundtrip_preserves_thought_signature()
    test_from_dict_then_build_messages_for_api_behaves_like_live_history()
    test_record_conversation_message_and_conversation_log()
    test_to_dict_from_dict_roundtrip_conversation_log()
    test_from_dict_without_conversation_log_key_defaults_to_empty()
    print("\nTODOS OS TESTES PASSARAM")
