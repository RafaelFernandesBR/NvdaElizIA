# -*- coding: utf-8 -*-
"""
Teste de INTEGRAÇÃO REAL — único teste desta pasta que faz chamadas de rede
de verdade pra API Gemini. Opt-in: não é chamado por `run_all.py` e não deve
rodar em CI. Serve pra validar hipóteses sobre o comportamento real da API
que nenhum teste com dublê consegue confirmar — por exemplo, se o formato
compactado de ConversationHistory (só o último turno do modelo + a
screenshot atual, histórico anterior só em texto) é aceito pela API sem
erro de pairing function_call/function_response.

Requisito: uma gemini_api_key já configurada no painel do NVDA (lida direto
do nvda.ini — NUNCA é impressa/logada). Se não houver chave configurada,
o teste termina com uma mensagem clara em vez de falhar com traceback.

Rodar manualmente: python tests/test_real_api_integration.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import load_addon_module, stub_nvda_modules, read_gemini_api_key_from_nvda_ini

API_KEY = read_gemini_api_key_from_nvda_ini()
if not API_KEY:
    print("SKIP: nenhuma gemini_api_key configurada no painel do NVDA (nvda.ini) — nada a testar.")
    sys.exit(0)

stub_nvda_modules(include_config=True)

import types as _pytypes

_MODEL = "gemini-2.5-flash"
_config_stub = sys.modules["config"]
_config_stub.conf = {
    "ElizIA": {
        "gemini_api_key": API_KEY,
        "gemini_model": _MODEL,
        "ai_temperature": 0.2,
        "api_timeout": 30,
        "enable_shell_tool": False,
    }
}

addon_config = load_addon_module("addon_config")
action_tools = load_addon_module("action_tools")
action_processor = load_addon_module("action_processor")
utils = load_addon_module("utils")
gemini_client_mod = load_addon_module("gemini_client")

from google.genai import types as gt

BatchItem = action_processor.BatchItem

# imagem mínima (1x1 PNG) só pra satisfazer o formato inline_data — o
# CONTEÚDO da imagem é irrelevante aqui: este teste valida o FORMATO da
# troca de mensagens, não a qualidade da resposta do modelo.
import base64
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScy8qYAAAAASUVORK5CYII="
)


def test_compacted_history_accepted_across_sequential_turns():
    client = gemini_client_mod.GeminiClient()
    system_instruction = utils.build_system_instruction("Notepad", "pt-br", addon_config.AI_OPERATOR_PROMPT_TEMPLATE)

    ch = utils.ConversationHistory()
    ch.set_task_text("Tarefa: abra o Bloco de Notas do Windows")

    turn1_parts = [gt.Part.from_bytes(data=TINY_PNG, mime_type="image/png")]
    messages = ch.build_messages_for_api(turn1_parts)
    assert len(messages) == 1 and messages[0].role == "user"

    turn1 = client.generate_content(messages, system_instruction=system_instruction)
    assert turn1.function_calls, f"esperava function_call no turno 1, veio text={turn1.text!r} error={turn1.error!r}"
    call1 = turn1.function_calls[0]
    print(f"turno 1 OK: function_call={call1.name}({call1.args})")

    item1 = BatchItem(call1.name, call1.id, "execute")
    item1.response = {"success": True, "error": None, "screen_changed": True}
    ch.record_step([item1])
    ch.set_last_model_content(turn1.model_content)

    turn2_parts = [
        gt.Part.from_function_response(name=call1.name, response=item1.response),
        gt.Part(text="Verificando resultado e continuando."),
        gt.Part.from_bytes(data=TINY_PNG, mime_type="image/png"),
    ]
    messages = ch.build_messages_for_api(turn2_parts)
    assert len(messages) == 3
    assert [c.role for c in messages] == ["user", "model", "user"]

    # Se o formato compactado não fosse aceito pela API, o erro apareceria
    # exatamente aqui (ex: "function response missing" ou similar).
    turn2 = client.generate_content(messages, system_instruction=system_instruction)
    assert not (turn2.error and not turn2.function_calls), f"turno 2 falhou: {turn2.error}"
    print(f"turno 2 OK (sem erro de pairing): function_calls={[fc.name for fc in (turn2.function_calls or [])]}")

    print("\nTESTE DE INTEGRAÇÃO REAL PASSOU — API aceita o formato compactado sem erro de pairing.")


def test_list_models_returns_chat_capable_models():
    client = gemini_client_mod.GeminiClient()
    models = client.list_models()
    assert models, "esperava ao menos um modelo retornado pela API"
    assert any(m.startswith("gemini-") for m in models), f"nenhum modelo 'gemini-*' na lista: {models}"
    print(f"list_models OK: {len(models)} modelo(s) encontrado(s): {models}")


if __name__ == "__main__":
    test_compacted_history_accepted_across_sequential_turns()
    test_list_models_returns_chat_capable_models()
