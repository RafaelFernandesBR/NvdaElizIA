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


def test_conversation_log_windowed_keeps_first_and_last():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: Y2")
    for turn in range(1, 15):
        ch.record_conversation_message("Você", f"pergunta {turn}")
        ch.record_conversation_message(addon_config.ADDON_NAME, f"resposta {turn}")
    text = ch.conversation_log_text()
    lines = text.splitlines()
    # Comparação EXATA (não "in"): "pergunta 1" é substring de "pergunta
    # 12"/"13"/"14", que também estão na janela "recente" — "in" passaria
    # mesmo se o primeiro par real tivesse sido descartado.
    assert lines[1] == "Você: pergunta 1", "primeira pergunta deveria sempre aparecer, intacta"
    assert lines[2] == f"{addon_config.ADDON_NAME}: resposta 1", "primeiro par inteiro, não só a pergunta"
    assert "omitida" in text
    assert "pergunta 14" in text, "pergunta mais recente deveria aparecer"
    # Igual a history_log_text: o meio é descartado pela janela.
    assert "pergunta 7" not in text
    print("test_conversation_log_windowed_keeps_first_and_last OK")


def test_conversation_log_text_pairs_by_sender_not_position():
    # Cenário real: uma ocorrência de tarefa recorrente com
    # notify_only_on_change nunca chama _show_result_in_chat quando
    # changed=False (ver __init__.py:_finalize_scheduled_task), então só
    # "Você" é gravado pra aquela ocorrência — nenhuma resposta. Se a sessão
    # for retomada depois com uma nova pergunta, o log fica com duas
    # entradas "Você" consecutivas. Pareamento por POSIÇÃO (par/ímpar)
    # juntaria as duas perguntas como se fosse pergunta+resposta; por
    # REMETENTE, cada uma fica isolada.
    ch = utils.ConversationHistory()
    ch.record_conversation_message("Você", "comando sem resposta registrada")
    ch.record_conversation_message("Você", "pergunta de continuação depois de retomar")
    ch.record_conversation_message(addon_config.ADDON_NAME, "resposta da continuação")

    text = ch.conversation_log_text()
    lines = text.splitlines()
    # A 1ª pergunta forma um "par" de 1 linha só (sem resposta) — não deve
    # aparecer colada com a 2ª pergunta como se fossem pergunta+resposta.
    assert lines[1] == "Você: comando sem resposta registrada"
    assert lines[2] == "Você: pergunta de continuação depois de retomar"
    assert lines[3] == f"{addon_config.ADDON_NAME}: resposta da continuação"
    print("test_conversation_log_text_pairs_by_sender_not_position OK")


def test_conversation_log_text_truncates_long_messages():
    ch = utils.ConversationHistory()
    long_message = "x" * (addon_config.MAX_CONVERSATION_MESSAGE_CHARS + 50)
    ch.record_conversation_message("Você", long_message)
    text = ch.conversation_log_text()
    assert long_message not in text, "mensagem longa demais não deveria entrar inteira"
    kept = "x" * addon_config.MAX_CONVERSATION_MESSAGE_CHARS
    assert f"Você: {kept}..." in text
    print("test_conversation_log_text_truncates_long_messages OK")


def test_build_messages_for_api_stays_bounded_with_all_fields_populated():
    # Garante que incluir a transcrição da conversa não quebra o invariante
    # O(1)/≤3 Content, mesmo com _task_text + conversation_log + log_entries
    # + notes todos populados ao mesmo tempo.
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: tudo junto")
    ch.record_conversation_message("Você", "pergunta inicial")
    ch.record_conversation_message(addon_config.ADDON_NAME, "resposta inicial")
    ch.record_step(make_batch(1))
    ch.record_note("fato relevante")
    ch.set_last_model_content(fake_model_content(1))

    msgs = ch.build_messages_for_api([gt.Part(text="screenshot")])
    assert len(msgs) <= 3
    assert [m.role for m in msgs] == ["user", "model", "user"]
    joined_text = "\n".join(p.text for p in msgs[0].parts if p.text)
    assert "pergunta inicial" in joined_text
    assert "fato relevante" in joined_text
    print("test_build_messages_for_api_stays_bounded_with_all_fields_populated OK")


def test_from_dict_then_build_messages_for_api_includes_restored_conversation_log():
    live = utils.ConversationHistory()
    live.set_task_text("Tarefa: retomada")
    live.record_conversation_message("Você", "qual foi minha primeira mensagem?")
    live.record_conversation_message(addon_config.ADDON_NAME, "resposta anterior")

    restored = utils.ConversationHistory.from_dict(live.to_dict())
    msgs = restored.build_messages_for_api([gt.Part(text="screenshot nova")])
    joined_text = "\n".join(p.text for p in msgs[0].parts if p.text)
    assert "qual foi minha primeira mensagem?" in joined_text
    assert "resposta anterior" in joined_text
    print("test_from_dict_then_build_messages_for_api_includes_restored_conversation_log OK")


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
    # Garantia central (pós-fix): a transcrição humana AGORA entra no que vai
    # pra API — sem ela, uma pergunta de continuação sobre a própria conversa
    # ("qual foi minha primeira mensagem?") não tem como ter resposta
    # correta, já que _task_text é substituído a cada chamada de
    # _run_ai_loop (ver set_task_text). _log_entries continua vazio (nenhuma
    # ação/record_step foi chamada aqui) — são campos independentes.
    assert ch.history_log_text() == ""
    assert "organize os itens por ordem alfabética" in ch.conversation_log_text()
    msgs = ch.build_messages_for_api([gt.Part(text="screenshot")])
    assert len(msgs) == 1 and msgs[0].role == "user"
    joined_text = "\n".join(p.text for p in msgs[0].parts if p.text)
    assert "organize os itens por ordem alfabética" in joined_text
    assert "Os itens foram classificados por nome." in joined_text
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


def test_record_step_summarizes_successful_long_wait_tool_as_ok_not_ignored():
    # long_wait_tool é kind="skip" (resolvida no nível do BatchPlan, como
    # ask_user_tool), mas com sucesso — sem um ramo explícito pra isso em
    # _summarize_batch_item, cairia no fallback "ignorado: sem detalhes" e a
    # IA acharia, turnos depois, que a espera falhou ou foi pulada.
    ch = utils.ConversationHistory()
    item = BatchItem("long_wait_tool", None, "skip")
    item.response = {"success": True, "message": "Waited 90 seconds."}
    ch.record_step([item], "Aguardando 90 segundos antes de verificar de novo...")
    log_text = ch.history_log_text()
    assert "ok:" in log_text and "Waited 90 seconds" in log_text
    assert "ignorado" not in log_text
    print("test_record_step_summarizes_successful_long_wait_tool_as_ok_not_ignored OK")


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


def test_record_note_never_windowed_unlike_log_entries():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: pesquisa longa")
    ch.record_note("Site X: passagem SP-RJ 15/11 por R$450")
    for turn in range(1, 15):
        ch.record_step(make_batch(turn))
    log_text = ch.history_log_text()
    # Com 14 turnos e janela de 8 itens (1º + omitido + últimos 7), o meio
    # (turnos 2-7) é descartado — turno 6 é um exemplo que certamente caiu
    # fora da janela (ver test_history_log_windowed_keeps_first_and_last).
    # "6." não aparece como sufixo de nenhum turno mantido (1, 8-14), ao
    # contrário de "4."/"3."/"2." (substring de "14."/"13."/"12.").
    assert "6. click" not in log_text
    assert "omitido" in log_text
    # A nota, ao contrário, nunca é janelada — sobrevive independente disso.
    assert "R$450" in ch.notes_text()
    print("test_record_note_never_windowed_unlike_log_entries OK")


def test_notes_included_in_messages_without_exceeding_bounded_payload():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: R")
    ch.record_note("fato 1")
    ch.record_note("fato 2")
    msgs = ch.build_messages_for_api([gt.Part(text="screenshot")])
    assert len(msgs) == 1 and msgs[0].role == "user"
    joined_text = "\n".join(p.text for p in msgs[0].parts if p.text)
    assert "fato 1" in joined_text and "fato 2" in joined_text
    print("test_notes_included_in_messages_without_exceeding_bounded_payload OK")


def test_record_note_rejects_beyond_max_notes_cap():
    ch = utils.ConversationHistory()
    for i in range(addon_config.MAX_NOTES):
        assert ch.record_note(f"fato {i}") is True
    # O teto existe justamente porque record_note_tool é kind="support" e
    # nunca passa pelo circuit breaker de loop (is_loop_detected só vê
    # ações físicas) — sem ele, nada impediria a lista de crescer sem fim.
    assert ch.record_note("fato excedente") is False
    assert len(ch._notes) == addon_config.MAX_NOTES
    print("test_record_note_rejects_beyond_max_notes_cap OK")


def test_record_note_rejects_empty_note():
    ch = utils.ConversationHistory()
    assert ch.record_note("") is False
    assert ch.record_note(None) is False
    assert ch.notes_text() == ""
    print("test_record_note_rejects_empty_note OK")


def test_notes_text_empty_when_no_notes_recorded():
    ch = utils.ConversationHistory()
    assert ch.notes_text() == ""
    print("test_notes_text_empty_when_no_notes_recorded OK")


def test_clear_resets_notes_too():
    ch = utils.ConversationHistory()
    ch.record_note("fato temporário")
    ch.clear()
    assert ch.notes_text() == ""
    print("test_clear_resets_notes_too OK")


def test_to_dict_from_dict_roundtrip_notes():
    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: S")
    ch.record_note("fato A")
    ch.record_note("fato B")

    data = ch.to_dict()
    assert data["notes"] == ["fato A", "fato B"]

    restored = utils.ConversationHistory.from_dict(data)
    assert restored.notes_text() == ch.notes_text()
    print("test_to_dict_from_dict_roundtrip_notes OK")


def test_from_dict_without_notes_key_defaults_to_empty():
    # Simula sessão salva antes do suporte a record_note_tool existir.
    restored = utils.ConversationHistory.from_dict({
        "task_text": "Tarefa: antiga",
        "log_entries": ["1. click -> ok, tela mudou"],
        "last_model_content": None,
    })
    assert restored.notes_text() == ""
    print("test_from_dict_without_notes_key_defaults_to_empty OK")


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
    test_conversation_log_windowed_keeps_first_and_last()
    test_conversation_log_text_pairs_by_sender_not_position()
    test_conversation_log_text_truncates_long_messages()
    test_build_messages_for_api_stays_bounded_with_all_fields_populated()
    test_from_dict_then_build_messages_for_api_includes_restored_conversation_log()
    test_done_tool_or_loop_break_never_leaves_orphan_function_call()
    test_clear_resets_everything()
    test_record_step_handles_unresolved_item_without_crashing()
    test_record_step_summarizes_successful_long_wait_tool_as_ok_not_ignored()
    test_to_dict_from_dict_roundtrip_without_model_content()
    test_to_dict_from_dict_roundtrip_preserves_thought_signature()
    test_from_dict_then_build_messages_for_api_behaves_like_live_history()
    test_record_conversation_message_and_conversation_log()
    test_to_dict_from_dict_roundtrip_conversation_log()
    test_from_dict_without_conversation_log_key_defaults_to_empty()
    test_record_note_never_windowed_unlike_log_entries()
    test_notes_included_in_messages_without_exceeding_bounded_payload()
    test_record_note_rejects_beyond_max_notes_cap()
    test_record_note_rejects_empty_note()
    test_notes_text_empty_when_no_notes_recorded()
    test_clear_resets_notes_too()
    test_to_dict_from_dict_roundtrip_notes()
    test_from_dict_without_notes_key_defaults_to_empty()
    print("\nTODOS OS TESTES PASSARAM")
