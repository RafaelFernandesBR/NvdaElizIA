# -*- coding: utf-8 -*-
"""
Declaração das ações disponíveis para a IA como function calling nativo
do Gemini (google-genai), em vez de instruções em prosa + JSON ad-hoc.
"""

from datetime import datetime

import addonHandler

try:
    from google.genai import types
except ImportError as e:
    raise ImportError(f"google-genai nao foi encontrado: {e}")

addonHandler.initTranslation()


def _prop(type_, description, **kw):
    return types.Schema(type=type_, description=description, **kw)


_COORD_X = _prop(types.Type.INTEGER, "Coordenada X normalizada da tela, 0 (esquerda) a 1000 (direita).")
_COORD_Y = _prop(types.Type.INTEGER, "Coordenada Y normalizada da tela, 0 (topo) a 1000 (base).")


def _click_declaration(name, description):
    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"x": _COORD_X, "y": _COORD_Y},
            required=["x", "y"],
        ),
    )


CLICK_TOOL = _click_declaration(
    "click_tool", "Clica com o botão esquerdo do mouse na posição indicada da tela."
)
RIGHT_CLICK_TOOL = _click_declaration(
    "right_click_tool", "Clica com o botão direito do mouse na posição indicada da tela."
)
DOUBLE_CLICK_TOOL = _click_declaration(
    "double_click_tool", "Dá um clique duplo com o botão esquerdo na posição indicada da tela."
)

TYPE_TOOL = types.FunctionDeclaration(
    name="type_tool",
    description=(
        "Digita texto no campo atualmente focado. Se x/y forem informados, "
        "clica nessa posição antes de digitar."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "text": _prop(types.Type.STRING, "Texto a digitar."),
            "x": _prop(types.Type.INTEGER, "Opcional: coordenada X (0-1000) para clicar antes de digitar."),
            "y": _prop(types.Type.INTEGER, "Opcional: coordenada Y (0-1000) para clicar antes de digitar."),
            "press_enter": _prop(types.Type.BOOLEAN, "Se deve pressionar Enter após digitar."),
        },
        required=["text"],
    ),
)

KEYBOARD_TOOL = types.FunctionDeclaration(
    name="keyboard_tool",
    description='Envia um atalho de teclado. Exemplos: "win+r", "ctrl+c", "alt+f4".',
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"keys": _prop(types.Type.STRING, "Teclas separadas por '+'.")},
        required=["keys"],
    ),
)

SCROLL_TOOL = types.FunctionDeclaration(
    name="scroll_tool",
    description=(
        "Rola a tela com a roda do mouse. Informe x/y apontando para a área "
        "rolável desejada — a rolagem afeta o que estiver SOB O CURSOR, não "
        "a janela em foco."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "direction": _prop(types.Type.STRING, "Direção do scroll.", format="enum", enum=["up", "down"]),
            "amount": _prop(types.Type.INTEGER, "Quantidade de 'cliques' da roda, de 1 a 10."),
            "x": _prop(types.Type.INTEGER, "Coordenada X (0-1000) sobre a qual rolar."),
            "y": _prop(types.Type.INTEGER, "Coordenada Y (0-1000) sobre a qual rolar."),
        },
        required=["direction"],
    ),
)

WAIT_TOOL = types.FunctionDeclaration(
    name="wait_tool",
    description=(
        "Não faz nada além de esperar. Use quando a tela está carregando e "
        "nenhuma ação de mouse/teclado é necessária agora."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"seconds": _prop(types.Type.NUMBER, "Segundos a esperar, de 1 a 10.")},
        required=[],
    ),
)

LONG_WAIT_TOOL = types.FunctionDeclaration(
    name="long_wait_tool",
    description=(
        "Pausa esta automação por até alguns minutos antes de olhar a tela de novo, "
        "mantendo intacto todo o histórico/notas já acumulados — use pra esperar um "
        "processo de fundo terminar (ex: build, upload, exportação, geração de relatório) "
        "quando wait_tool (até 10s) é curto demais. Diferente de schedule_task_tool: NÃO "
        "termina esta automação nem cria uma nova depois — é a MESMA tarefa, só mais "
        "tarde. Use schedule_task_tool em vez desta se a espera for de muitos minutos/"
        "horas, ou se o usuário puder fechar o NVDA nesse meio tempo. Nunca combine com "
        "outra tool no mesmo turno."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"seconds": _prop(types.Type.INTEGER, "Segundos a esperar antes de verificar de novo.")},
        required=["seconds"],
    ),
)

SCHEDULE_TASK_TOOL = types.FunctionDeclaration(
    name="schedule_task_tool",
    description=(
        "Agenda uma tarefa pra ser executada automaticamente mais tarde, sem "
        "precisar que o usuário peça de novo no horário. Use quando o usuário "
        "pedir algo pra um horário futuro (\"me lembra de X às 15h\", \"faça Y "
        "em 10 minutos\") ou quando você mesmo identificar, pelo contexto, que "
        "uma ação deveria acontecer depois em vez de agora. Pra MONITORAMENTO "
        "recorrente (ex: \"veja o preço 3 vezes nas próximas 2 horas\", "
        "\"monitore esse link\"), use também recurrence_minutes/max_occurrences/"
        "recurrence_until — nunca tente controlar a repetição sozinho chamando "
        "esta tool de novo a cada verificação."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command": _prop(
                types.Type.STRING,
                "Instrução completa a executar no horário agendado, como se fosse um novo comando do usuário.",
            ),
            "run_at": _prop(
                types.Type.STRING,
                "Data e hora local no formato ISO 8601 'AAAA-MM-DDTHH:MM:SS' em que a PRIMEIRA execução deve "
                "rodar. Calcule a partir da data/hora atual informada no contexto do sistema.",
            ),
            "title": _prop(types.Type.STRING, "Descrição curta da tarefa, para exibir numa lista."),
            "recurrence_minutes": _prop(
                types.Type.INTEGER,
                "Opcional. Se informado (>0), a tarefa se repete automaticamente a cada N minutos após cada "
                "execução, em vez de rodar só uma vez — use pra monitoramento (preço, disponibilidade, "
                "métricas de uma página). Omita ou mande 0 para uma tarefa única (padrão).",
            ),
            "notify_only_on_change": _prop(
                types.Type.BOOLEAN,
                "Opcional, só com recurrence_minutes. Se true, o usuário só é avisado do RESULTADO quando "
                "done_tool indicar changed=true — checagens sem novidade ficam só registradas, sem "
                "interromper o usuário. Padrão false (avisa o resultado sempre).",
            ),
            "max_occurrences": _prop(
                types.Type.INTEGER,
                "Opcional, só com recurrence_minutes. Número máximo de repetições antes de parar "
                "automaticamente (ex: 3 pra \"verifique 3 vezes\"). Sem isso, repete indefinidamente até "
                "ser cancelada ou até done_tool pedir stop_recurrence=true.",
            ),
            "recurrence_until": _prop(
                types.Type.STRING,
                "Opcional, só com recurrence_minutes. Data/hora ISO 8601 limite após a qual a recorrência "
                "para de se repetir (ex: \"nos próximos 30 minutos\" = agora + 30min). Combine com "
                "max_occurrences quando o usuário der as duas informações (\"3 vezes em 30 minutos\").",
            ),
        },
        required=["command", "run_at", "title"],
    ),
)

LIST_SCHEDULED_TASKS_TOOL = types.FunctionDeclaration(
    name="list_scheduled_tasks_tool",
    description="Lista as tarefas agendadas pendentes (ainda não executadas), com seus IDs, títulos e horários.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}, required=[]),
)

CANCEL_SCHEDULED_TASK_TOOL = types.FunctionDeclaration(
    name="cancel_scheduled_task_tool",
    description="Cancela uma tarefa agendada pendente, pelo ID (obtido via list_scheduled_tasks_tool).",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"task_id": _prop(types.Type.INTEGER, "ID da tarefa agendada a cancelar.")},
        required=["task_id"],
    ),
)

APP_TOOL = types.FunctionDeclaration(
    name="app_tool",
    description=(
        "Abre um programa pelo nome (ex: 'notepad', 'calc', 'chrome', 'explorer'), "
        "sem precisar simular Win+R manualmente. Mais rápido e confiável que "
        "keyboard_tool+type_tool pra esse fim."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"app_name": _prop(types.Type.STRING, "Nome do programa a abrir.")},
        required=["app_name"],
    ),
)

ASK_USER_TOOL = types.FunctionDeclaration(
    name="ask_user_tool",
    description=(
        "Faz uma pergunta ao usuário quando uma decisão só ele pode tomar (ex: qual "
        "das opções escolher, confirmar uma informação ambígua). Use com moderação — "
        "só quando realmente não dá pra decidir sozinho olhando a tela. Pausa a "
        "automação até o usuário responder. Nunca chame junto de outra ferramenta."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"question": _prop(types.Type.STRING, "Pergunta objetiva a fazer ao usuário.")},
        required=["question"],
    ),
)

SHELL_TOOL = types.FunctionDeclaration(
    name="shell_tool",
    description=(
        "Executa um comando no terminal do Windows (cmd.exe). SEMPRE pede confirmação "
        "explícita do usuário antes de rodar — nunca executa silenciosamente. Use "
        "apenas quando não houver um jeito de fazer isso pela interface gráfica."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "command": _prop(types.Type.STRING, "Comando exato a executar no cmd.exe."),
            "reason": _prop(
                types.Type.STRING,
                "Explicação curta, em linguagem simples, do que esse comando faz — será lida ao usuário antes de pedir confirmação.",
            ),
        },
        required=["command", "reason"],
    ),
)

DONE_TOOL = types.FunctionDeclaration(
    name="done_tool",
    description=(
        "Chame APENAS depois de ver, na captura de tela mais recente, que a "
        "tarefa terminou — concluída com sucesso OU definitivamente bloqueada "
        "(ex: elemento não encontrado, ação repetida sem efeito). Nunca chame "
        "junto de outra ferramenta no mesmo turno."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "summary": _prop(
                types.Type.STRING,
                "Descreva exatamente o que você viu na tela confirmando o desfecho.",
            ),
            "success": _prop(
                types.Type.BOOLEAN,
                "true se a tarefa foi concluída com sucesso (confirmado "
                "visualmente); false se você está parando sem conseguir "
                "completá-la.",
            ),
            "changed": _prop(
                types.Type.BOOLEAN,
                "Só relevante se esta execução for uma verificação RECORRENTE (agendada com "
                "recurrence_minutes — o próprio contexto da tarefa avisa quando é o caso, junto do "
                "resultado da checagem anterior, se houver). true se algo mudou de forma relevante desde a "
                "checagem anterior (ex: preço caiu); false se não há novidade. Ignorado em tarefas comuns.",
            ),
            "result_data": _prop(
                types.Type.STRING,
                "Só relevante em verificação RECORRENTE. Snapshot conciso do estado atual, pra comparar na "
                "PRÓXIMA checagem (ex: \"price=450 BRL\", \"likes=1234\"). Ignorado em tarefas comuns.",
            ),
            "stop_recurrence": _prop(
                types.Type.BOOLEAN,
                "Só relevante em verificação RECORRENTE. true quando a condição que justificava monitorar "
                "foi satisfeita (ex: preço já caiu abaixo do limite pedido) — a tarefa recorrente para de "
                "vez, em vez de agendar a próxima checagem. Padrão false. Ignorado em tarefas comuns.",
            ),
        },
        required=["summary", "success"],
    ),
)

RECORD_NOTE_TOOL = types.FunctionDeclaration(
    name="record_note_tool",
    description=(
        "Anota um fato importante que não pode ser esquecido numa tarefa longa, com muitos passos (ex: um "
        "preço encontrado num site já comparado, uma opção descartada e por quê, um dado coletado num site "
        "que você vai precisar comparar com outro mais adiante). Diferente da sua explicação normal de cada "
        "turno, que eventualmente sai do seu histórico visível, esta nota NUNCA é esquecida, mesmo em "
        "tarefas com dezenas de turnos — use quando a informação importar além dos próximos passos "
        "imediatos. Não serve pra narrar passos comuns, só fatos que valem a pena preservar."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={"note": _prop(types.Type.STRING, "Fato curto e objetivo a lembrar.")},
        required=["note"],
    ),
)

ALL_TOOLS = [
    CLICK_TOOL, RIGHT_CLICK_TOOL, DOUBLE_CLICK_TOOL,
    TYPE_TOOL, KEYBOARD_TOOL, SCROLL_TOOL, WAIT_TOOL, LONG_WAIT_TOOL,
    SCHEDULE_TASK_TOOL, LIST_SCHEDULED_TASKS_TOOL, CANCEL_SCHEDULED_TASK_TOOL,
    APP_TOOL, ASK_USER_TOOL, RECORD_NOTE_TOOL, DONE_TOOL,
]

# Tool separada porque só entra na lista enviada ao Gemini se o usuário
# ligar enable_shell_tool no painel (ver get_tool(include_shell=...)) —
# a confirmação bloqueante antes de cada execução (ver __init__.py) é
# obrigatória independentemente disso; isso só evita oferecer a tool pro
# modelo em instalações que nunca vão querer usá-la.
SHELL_TOOLS = [SHELL_TOOL]

# Nome da function declaration -> action_type usado por ActionInfo/ActionExecutor.
# "support": tool que faz algo real (grava/lê dado) mas nunca mexe na tela —
# resolvida em process_function_calls via handler injetado, nunca passa por
# run_batch/capture_fn/is_loop_detected (ver action_processor.py).
# "ask_user": nunca aparece aqui com ActionInfo associada — process_function_calls
# trata ask_user_tool no nível do BatchPlan (como done_tool), não como BatchItem
# "execute"; a entrada existe só pra fechar o invariante "toda tool registrada
# tem um action_type" (ver tests/test_action_tools.py).
# "long_wait": mesma situação de "ask_user" — long_wait_tool é tratada no nível do
# BatchPlan (ver BatchPlan.awaiting_wait_seconds/process_function_calls), nunca
# como BatchItem "execute"/"support"; nunca passa por run_batch/is_loop_detected.
TOOL_NAME_TO_ACTION_TYPE = {
    "click_tool": "click",
    "right_click_tool": "right_click",
    "double_click_tool": "double_click",
    "type_tool": "type",
    "keyboard_tool": "keyboard",
    "scroll_tool": "scroll",
    "wait_tool": "wait",
    "long_wait_tool": "long_wait",
    "schedule_task_tool": "support",
    "list_scheduled_tasks_tool": "support",
    "cancel_scheduled_task_tool": "support",
    "app_tool": "launch_app",
    "ask_user_tool": "ask_user",
    "record_note_tool": "support",
    "shell_tool": "shell_command",
    "done_tool": "done",
}

REQUIRED_ARGS_BY_TOOL = {
    "click_tool": ["x", "y"],
    "right_click_tool": ["x", "y"],
    "double_click_tool": ["x", "y"],
    "type_tool": ["text"],
    "keyboard_tool": ["keys"],
    "scroll_tool": ["direction"],
    "wait_tool": [],
    "long_wait_tool": ["seconds"],
    "schedule_task_tool": ["command", "run_at", "title"],
    "list_scheduled_tasks_tool": [],
    "cancel_scheduled_task_tool": ["task_id"],
    "app_tool": ["app_name"],
    "ask_user_tool": ["question"],
    "record_note_tool": ["note"],
    "shell_tool": ["command", "reason"],
    "done_tool": ["summary", "success"],
}


def get_tool(include_shell=False):
    """
    Retorna o único types.Tool agregando todas as function declarations.

    Args:
        include_shell: inclui shell_tool na lista enviada ao Gemini — só
            True quando o usuário ligou enable_shell_tool no painel (ver
            addon_config/__init__.py). A confirmação bloqueante antes de
            cada execução acontece de qualquer forma; isso só controla se
            a tool é OFERECIDA ao modelo.
    """
    declarations = list(ALL_TOOLS)
    if include_shell:
        declarations.extend(SHELL_TOOLS)
    return types.Tool(function_declarations=declarations)


def validate_args(tool_name, args):
    """
    Valida os argumentos recebidos para uma tool.

    Args:
        tool_name: nome da function declaration (ex: "click_tool")
        args: dict de argumentos vindos de FunctionCall.args

    Returns:
        str: mensagem de erro descrevendo o problema, ou None se válido.
    """
    args = args or {}
    required = REQUIRED_ARGS_BY_TOOL.get(tool_name)
    if required is None:
        return _("Unknown tool: %(tool_name)r") % {"tool_name": tool_name}

    missing = [p for p in required if args.get(p) in (None, "")]
    if missing:
        return _("Missing required parameter(s) for %(tool_name)s: %(missing)s") % {
            "tool_name": tool_name,
            "missing": missing,
        }

    if tool_name == "scroll_tool" and args.get("direction") not in ("up", "down"):
        return _("Invalid direction for scroll_tool: %(direction)r (expected 'up' or 'down')") % {
            "direction": args.get("direction"),
        }

    if tool_name == "long_wait_tool":
        seconds_raw = args.get("seconds")
        try:
            seconds_value = float(seconds_raw)
        except (TypeError, ValueError):
            return _("Invalid seconds for long_wait_tool: %(value)r") % {"value": seconds_raw}
        if seconds_value <= 0:
            return _("seconds must be greater than zero, received %(value)r.") % {"value": seconds_raw}

    if tool_name == "schedule_task_tool":
        run_at_raw = args.get("run_at")
        try:
            # Sempre tratado como horário local NAIVE (sem timezone) — é o
            # que now_local injetado no prompt do sistema usa (ver utils.py),
            # então o modelo nunca deveria mandar um offset de timezone.
            run_at = datetime.fromisoformat(run_at_raw).replace(tzinfo=None)
        except (TypeError, ValueError):
            return _(
                "Invalid run_at for schedule_task_tool: %(run_at)r "
                "(expected ISO 8601 'YYYY-MM-DDTHH:MM:SS')"
            ) % {"run_at": run_at_raw}
        if run_at <= datetime.now():
            return _("run_at must be in the future, received %(run_at)r which has already passed (or is now).") % {
                "run_at": run_at_raw,
            }

        recurrence_minutes = args.get("recurrence_minutes")
        has_recurrence = bool(recurrence_minutes)
        if recurrence_minutes not in (None, 0):
            try:
                recurrence_minutes_value = float(recurrence_minutes)
            except (TypeError, ValueError):
                return _("Invalid recurrence_minutes for schedule_task_tool: %(value)r") % {
                    "value": recurrence_minutes,
                }
            if recurrence_minutes_value <= 0:
                return _("recurrence_minutes must be greater than zero, received %(value)r.") % {
                    "value": recurrence_minutes,
                }

        max_occurrences = args.get("max_occurrences")
        recurrence_until_raw = args.get("recurrence_until")
        if not has_recurrence and (max_occurrences is not None or recurrence_until_raw):
            return _(
                "max_occurrences and recurrence_until are only valid together with recurrence_minutes."
            )

        if max_occurrences is not None:
            try:
                max_occurrences_value = int(max_occurrences)
            except (TypeError, ValueError):
                return _("Invalid max_occurrences for schedule_task_tool: %(value)r") % {
                    "value": max_occurrences,
                }
            if max_occurrences_value <= 0:
                return _("max_occurrences must be greater than zero, received %(value)r.") % {
                    "value": max_occurrences,
                }

        if recurrence_until_raw:
            try:
                recurrence_until = datetime.fromisoformat(recurrence_until_raw).replace(tzinfo=None)
            except (TypeError, ValueError):
                return _(
                    "Invalid recurrence_until for schedule_task_tool: %(value)r "
                    "(expected ISO 8601 'YYYY-MM-DDTHH:MM:SS')"
                ) % {"value": recurrence_until_raw}
            if recurrence_until <= datetime.now():
                return _(
                    "recurrence_until must be in the future, received %(value)r which has already passed "
                    "(or is now)."
                ) % {"value": recurrence_until_raw}

    return None
