# -*- coding: utf-8 -*-
"""
Funções utilitárias
"""

import re
import api
from . import addon_config

try:
    from google.genai import types
except ImportError as e:
    raise ImportError(f"google-genai nao foi encontrado: {e}")


def _build_loop_warning(last_actions):
    """
    Aviso textual (soft) um passo ANTES do circuit breaker (hard, em
    ActionProcessor.is_loop_detected) disparar — dá à IA uma chance de mudar
    de estratégia antes da automação ser interrompida de verdade.
    """
    warn_at = addon_config.LOOP_DETECTION_THRESHOLD - 1
    if warn_at < 1 or not last_actions or len(last_actions) < warn_at:
        return ""

    recent = last_actions[-warn_at:]
    if len(set(recent)) == 1:
        return (
            f"⚠️ AVISO: você tentou '{recent[0]}' {warn_at}x seguidas e a tela não mudou.\n"
            f"Se repetir mais uma vez sem sucesso, a automação será interrompida. MUDE DE ESTRATÉGIA."
        )
    return ""


def clean_markdown(text):
    """
    Remove formatação markdown do texto.

    Args:
        text: Texto com possível formatação markdown

    Returns:
        str: Texto limpo sem markdown
    """
    if not text:
        return ""

    # Remove negrito/itálico
    text = re.sub(r'\*\*|__|[*_]', '', text)

    # Remove títulos markdown
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    return text.strip()


def get_focused_app_name():
    """
    Obtém o nome do aplicativo em foco.

    Returns:
        str: Nome do aplicativo ou "Desconhecido" se erro
    """
    try:
        return api.getForegroundObject().appModule.appName
    except (AttributeError, TypeError):
        return addon_config.DEFAULT_APP_NAME


def build_system_instruction(app_name, response_lang, prompt_template, now_local=None):
    """
    Constrói a instrução de sistema para a API, sem poluir o histórico de conversa.
    Inclui todo o contexto estático: regras, comandos NVDA, app em foco e idioma.

    Args:
        now_local: texto já formatado com data/hora/dia-da-semana local
            atual (ex: "terça-feira, 28/10/2026 14:32") — é assim que o
            modelo resolve "amanhã às 15h"/"em 2 horas" sozinho ao chamar
            schedule_task_tool, sem o addon precisar de um parser de datas
            relativas. Default None só pra não quebrar chamadores antigos/
            testes que não passam esse argumento.
    """
    return prompt_template.format(
        app_name=app_name,
        response_lang=response_lang,
        max_actions_per_turn=addon_config.MAX_ACTIONS_PER_TURN,
        now_local=now_local or "",
    )


def build_next_turn_parts(call_entries, speech_context="", last_actions=None, info_text_extra=""):
    """
    Monta as partes do turno de usuário que RESPONDE às function_calls do
    turno anterior: um Part.from_function_response por BatchItem (nessa
    ordem), seguido de um texto informativo (contexto de fala + aviso de
    loop). A screenshot do turno atual é adicionada pelo chamador por último.

    Args:
        call_entries: lista de ActionProcessor.BatchItem, cada um já com
            `.response` preenchido (inclusive o "screen_changed" do item
            físico executado, se houver).
        speech_context: texto de contexto de fala do NVDA (pode ser vazio)
        last_actions: histórico de assinaturas de ação para o aviso de loop
        info_text_extra: texto informativo adicional (ex: objetivo da tarefa)

    Returns:
        list[types.Part]
    """
    parts = [
        types.Part.from_function_response(name=entry.name, response=entry.response or {})
        for entry in call_entries
    ]

    info_lines = []
    if info_text_extra:
        info_lines.append(info_text_extra)
    if speech_context:
        info_lines.append(speech_context)
    loop_warning = _build_loop_warning(last_actions)
    if loop_warning:
        info_lines.append(loop_warning)

    if info_lines:
        parts.append(types.Part(text="\n\n".join(info_lines)))

    return parts


def _summarize_action_info(action_info):
    """Representação curta de uma ActionInfo pra uma linha de histórico."""
    at = action_info.action_type
    if at == "keyboard":
        return f"{at}(keys={action_info.keys})"
    if at == "scroll":
        return f"{at}(direction={action_info.direction}, amount={action_info.amount})"
    if at == "wait":
        return f"{at}(seconds={action_info.seconds})"
    if at == "type":
        return f"{at}(text={(action_info.text or '')[:30]!r})"
    if at == "launch_app":
        return f"{at}(app_name={action_info.app_name})"
    if at == "shell_command":
        return f"{at}(command={action_info.command!r})"
    return f"{at}(x={action_info.x}, y={action_info.y})"


def _summarize_batch_item(item):
    """Uma linha resumindo o resultado de um ActionProcessor.BatchItem."""
    resp = item.response or {}
    if item.kind == "support":
        if resp.get("success"):
            return f"{item.name} -> ok: {resp.get('message') or 'concluído'}"
        return f"{item.name} -> erro: {resp.get('error') or 'erro desconhecido'}"

    if item.kind == "skip":
        if "answer" in resp:
            # ask_user_tool: resolvido fora do run_batch normal, de forma
            # síncrona em _run_ai_loop (ver action_processor.
            # process_function_calls/BatchPlan.awaiting_question) — aqui só
            # registra a pergunta feita e a resposta recebida do usuário.
            return f"{item.name} -> perguntou ao usuário, resposta: {resp['answer']!r}"
        return f"{item.name} -> ignorado: {resp.get('error', 'sem detalhes') if resp else 'sem detalhes'}"

    args_desc = _summarize_action_info(item.action_info) if item.action_info else item.name
    if resp.get("success"):
        changed = "tela mudou" if resp.get("screen_changed") else "tela igual"
        return f"{args_desc} -> ok, {changed}"
    return f"{args_desc} -> falhou: {resp.get('error') or 'erro desconhecido'}"


class ConversationHistory:
    """
    Histórico leve de uma tarefa: nunca guarda mais de um types.Content de
    role 'model' (o mais recente, pra preservar thought_signature e parear
    o function_response do próximo turno) nem Content de usuário antigos
    com screenshot — passos anteriores ficam só como texto, numa janela
    limitada (mesmo padrão do agent_history_description do browser-use e do
    "pop da mensagem de estado" do Windows-Use).
    """

    def __init__(self):
        self._task_text = None
        self._log_entries = []
        self._last_model_content = None
        # Transcrição HUMANA (Você / {ADDON_NAME}) — nunca lida por
        # build_messages_for_api/history_log_text, não é contexto pra API.
        # Existe só pra repopular o chat ao retomar uma seção salva. Não
        # confundir com _log_entries: aquele é o resumo técnico de ações
        # pra IA, nunca deveria aparecer na tela.
        self._conversation_log = []
        # Fatos anotados via record_note_tool — ao contrário de
        # _log_entries, NUNCA janelado: sempre enviado por completo em
        # notes_text(), justamente pra sobreviver em tarefas com mais
        # turnos do que MAX_HISTORY_LOG_ITEMS (ver ARCHITECTURE.md).
        self._notes = []

    def set_task_text(self, text):
        """
        Define/substitui a âncora da tarefa atual — sempre enviada, nunca é
        omitida pela janela do log. Chamar 1x no início de toda chamada a
        _run_ai_loop (sessão nova OU pergunta de continuação).
        """
        self._task_text = text

    def record_step(self, items, display_text=""):
        """
        Resume o turno concluído numa linha de texto e acrescenta ao log
        janelado.

        Args:
            items: lista de ActionProcessor.BatchItem do turno (pode ter
                `.response is None` nos itens que o lote cortou antes de
                executar — tratados como "sem detalhes", não é erro).
            display_text: usado como a linha quando `items` está vazia
                (ex: turno sem nenhuma function_call válida).
        """
        turn_number = len(self._log_entries) + 1
        if not items:
            line = display_text.strip() if display_text else "(nenhuma ação)"
        else:
            line = "; ".join(_summarize_batch_item(item) for item in items)
        self._log_entries.append(f"{turn_number}. {line}")

    def record_note(self, note):
        """
        Fato anotado pela IA via record_note_tool — ver docstring de
        __init__: nunca sai do contexto enviado à API, mesmo quando o log
        textual janelado já descartou o turno em que foi anotado.

        Returns:
            bool — True se a nota foi aceita; False se `note` é vazio ou o
            teto addon_config.MAX_NOTES já foi atingido (chamador deve
            devolver um erro explícito à IA, nunca aceitar silenciosamente
            além do teto — ver GlobalPlugin._handle_record_note_tool).
        """
        if not note or len(self._notes) >= addon_config.MAX_NOTES:
            return False
        self._notes.append(note)
        return True

    def notes_text(self):
        """Texto com todas as notas registradas até agora, ou "" se nenhuma."""
        if not self._notes:
            return ""
        lines = [f"- {note}" for note in self._notes]
        return "📌 NOTAS IMPORTANTES (registradas com record_note_tool):\n" + "\n".join(lines)

    def record_conversation_message(self, sender, message):
        """
        Registra uma linha da transcrição HUMANA da conversa (o que o
        usuário digitou/ouviu de volta) — sender costuma ser "Você" ou
        addon_config.ADDON_NAME. Nunca usada pra montar o contexto da API;
        serve só pra repopular o OperatorChatDialog ao retomar uma seção.
        """
        self._conversation_log.append({"sender": sender, "message": message})

    def conversation_log(self):
        """Cópia da transcrição humana registrada até agora."""
        return list(self._conversation_log)

    def set_last_model_content(self, content):
        """
        Args: content — o response.candidates[0].content BRUTO da API, ou
        None.

        None sinaliza "nada pendente de function_response" — usado quando a
        tarefa termina (done_tool) ou o circuit breaker de loop interrompe a
        automação, pra nunca deixar uma function_call sem resposta
        pendurada no próximo `contents=` montado.

        Importante quando não-None: nunca reconstruir esse Content a partir
        de function_calls já extraídas — modelos "thinking" anexam
        thought_signature em partes da resposta, que a API espera receber
        de volta inalterado.
        """
        self._last_model_content = content

    def history_log_text(self):
        """
        Texto do log janelado: todas as entradas se couberem em
        addon_config.MAX_HISTORY_LOG_ITEMS, ou primeiro turno + marcador de
        omissão + as mais recentes, caso contrário. "" se não há nenhuma
        entrada ainda (turno 0).
        """
        if not self._log_entries:
            return ""

        total = len(self._log_entries)
        limit = addon_config.MAX_HISTORY_LOG_ITEMS
        if total <= limit:
            lines = list(self._log_entries)
        else:
            omitted = total - limit
            recent_count = max(0, limit - 1)
            recent = self._log_entries[-recent_count:] if recent_count else []
            lines = [self._log_entries[0], f"[... {omitted} passo(s) anterior(es) omitido(s) ...]"] + recent

        return "📜 HISTÓRICO DE PASSOS ANTERIORES:\n" + "\n".join(lines)

    def build_messages_for_api(self, current_user_parts):
        """
        Monta `contents=` pra API, O(1) em imagens — só a screenshot do
        turno atual (dentro de `current_user_parts`) é enviada; passos
        anteriores entram só como o texto de `history_log_text()`.

        Args:
            current_user_parts: list[types.Part] já montada pelo chamador
                para o turno atual (function_response do turno anterior,
                se houver, + texto informativo + a screenshot capturada agora).

        Returns:
            list[types.Content]: 1 item (turno 0, ou depois de done_tool/
            circuit breaker) ou 3 (task+log, último turno do modelo, turno atual).
        """
        lead_parts = []
        if self._task_text:
            lead_parts.append(types.Part(text=self._task_text))
        log_text = self.history_log_text()
        if log_text:
            lead_parts.append(types.Part(text=log_text))
        notes_text = self.notes_text()
        if notes_text:
            lead_parts.append(types.Part(text=notes_text))

        if self._last_model_content is None:
            return [types.Content(role="user", parts=lead_parts + list(current_user_parts))]

        messages = []
        if lead_parts:
            messages.append(types.Content(role="user", parts=lead_parts))
        messages.append(self._last_model_content)
        messages.append(types.Content(role="user", parts=list(current_user_parts)))
        return messages

    def clear(self):
        """Zera a tarefa, o log, o último turno do modelo, a transcrição e as notas."""
        self._task_text = None
        self._log_entries = []
        self._last_model_content = None
        self._conversation_log = []
        self._notes = []

    def to_dict(self):
        """
        Serializa os 5 campos pra persistência (ver session_store.py).
        `_last_model_content` vira dict puro via model_dump(mode="json") —
        é um types.Content (pydantic) do SDK google-genai, suporta isso
        nativamente, sem encoder customizado.
        """
        return {
            "task_text": self._task_text,
            "log_entries": list(self._log_entries),
            "last_model_content": (
                self._last_model_content.model_dump(mode="json")
                if self._last_model_content is not None else None
            ),
            "conversation_log": list(self._conversation_log),
            "notes": list(self._notes),
        }

    @classmethod
    def from_dict(cls, data):
        """
        Reconstrói a partir do dict de to_dict(). `last_model_content` é
        revalidado via types.Content.model_validate() — nunca montado à
        mão — pelo mesmo motivo documentado em set_last_model_content:
        thought_signature precisa sobreviver ao round-trip inalterado.
        `conversation_log`/`notes` ausentes (sessão antiga, ou dict de teste
        sem essas chaves) caem em lista vazia, sem lançar.
        """
        instance = cls()
        instance._task_text = data.get("task_text") or None
        instance._log_entries = list(data.get("log_entries") or [])
        raw_model_content = data.get("last_model_content")
        instance._last_model_content = (
            types.Content.model_validate(raw_model_content) if raw_model_content else None
        )
        instance._conversation_log = list(data.get("conversation_log") or [])
        instance._notes = list(data.get("notes") or [])
        return instance
