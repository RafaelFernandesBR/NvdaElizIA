# -*- coding: utf-8 -*-
"""
Processador de ações da IA.
Converte as function_calls nativas do Gemini (ver action_tools.py) em
ActionInfo para o ActionExecutor, validando argumentos antes de executar.
"""

import hashlib
import logging
from typing import NamedTuple, List, Optional

import addonHandler
from . import addon_config
from . import action_tools

addonHandler.initTranslation()

logger = logging.getLogger(__name__)


class ActionInfo:
    """Representa uma ação física a ser executada"""

    def __init__(self, action_type, x=None, y=None, text="", press_enter=False,
                 keys="", direction="down", amount=3, seconds=0,
                 app_name="", command="", reason=""):
        """
        Args:
            action_type: click, right_click, double_click, type, keyboard, scroll, wait,
                launch_app, shell_command
            x, y: Coordenadas em pixels (None quando não se aplica)
            text: Texto para digitar (type)
            press_enter: Se deve pressionar Enter após digitar (type)
            keys: String de teclas, ex. "win+r" (keyboard)
            direction: "up" ou "down" (scroll)
            amount: Quantidade de scroll, 1-10 (scroll)
            seconds: Segundos a esperar (wait)
            app_name: Nome do programa a abrir (launch_app)
            command: Comando de shell a executar (shell_command)
            reason: Explicação em linguagem simples do comando (shell_command)
        """
        self.action_type = action_type
        self.x = x
        self.y = y
        self.text = text
        self.press_enter = press_enter
        self.keys = keys
        self.direction = direction
        self.amount = amount
        self.seconds = seconds
        self.app_name = app_name
        self.command = command
        self.reason = reason

    def __repr__(self):
        if self.action_type == "keyboard":
            return f"ActionInfo(keyboard, keys={self.keys})"
        elif self.action_type == "scroll":
            return f"ActionInfo(scroll, direction={self.direction}, amount={self.amount})"
        elif self.action_type == "wait":
            return f"ActionInfo(wait, seconds={self.seconds})"
        elif self.action_type == "launch_app":
            return f"ActionInfo(launch_app, app_name={self.app_name})"
        elif self.action_type == "shell_command":
            return f"ActionInfo(shell_command, command={self.command})"
        return f"ActionInfo({self.action_type}, {self.x}, {self.y}, {self.text}, {self.press_enter})"

    def signature(self):
        """
        Assinatura usada para detecção de loop (mesma ação repetida). Cada
        action_type precisa de um ramo explícito aqui — o fallback genérico
        (`self.action_type` cru) faria duas chamadas DIFERENTES (ex:
        app_tool("notepad") e app_tool("calc")) colidirem na mesma
        assinatura e disparar o circuit breaker por engano.
        """
        if self.action_type == "keyboard":
            return f"keyboard:{self.keys}"
        if self.action_type == "scroll":
            return f"scroll:{self.direction}:{self.amount}"
        if self.action_type in ("click", "double_click", "right_click"):
            return f"{self.action_type}({self.x},{self.y})"
        if self.action_type == "launch_app":
            return f"launch_app:{self.app_name}"
        if self.action_type == "shell_command":
            return f"shell_command:{self.command}"
        return self.action_type


class BatchItem:
    """
    Uma function_call recebida da IA dentro do lote do turno, e o
    function_response correspondente.

    kind="skip": já resolvido (erro de validação, done_tool, excedente do
        limite por turno) — response já preenchido, nada a executar.
    kind="execute": ação física pendente de execução — o chamador
        (ActionExecutor, orquestrado em __init__.py) preenche `response`
        depois de rodar `action_info`.
    kind="support": tool que faz algo real (ex: grava uma tarefa agendada)
        mas nunca mexe na tela — já resolvida aqui mesmo, via handler
        injetado (ver process_function_calls), response já preenchido.
        Nunca passa por run_batch/capture_fn/is_loop_detected: diferente
        de "skip", seu sucesso/falha nunca aborta o resto do lote.
    """

    def __init__(self, name, call_id, kind, response=None, action_info=None):
        self.name = name
        self.id = call_id
        self.kind = kind
        self.response = response
        self.action_info = action_info


class BatchPlan(NamedTuple):
    items: List[BatchItem]
    is_finished: bool
    explanation: str
    # None enquanto a tarefa não terminou; só é significativo quando
    # is_finished=True (preenchido a partir do success= que a IA passou
    # pro done_tool) — __init__.py usa isso pra escolher o beep/prefixo.
    success: Optional[bool] = None
    # Preenchido só quando a IA chama ask_user_tool (sozinha, sem outra
    # tool no turno) — sinaliza pro _run_ai_loop pausar e esperar uma
    # resposta síncrona do usuário antes de seguir, em vez de executar
    # qualquer BatchItem. None no caso comum (nenhuma pergunta pendente).
    awaiting_question: Optional[str] = None
    # Os 3 campos abaixo só são preenchidos quando done_tool chega com os
    # parâmetros opcionais relevantes pra tarefas agendadas RECORRENTES (ver
    # action_tools.DONE_TOOL e GlobalPlugin._finalize_scheduled_task) —
    # None/False em qualquer tarefa comum, que nunca os envia.
    changed: Optional[bool] = None
    result_data: Optional[str] = None
    stop_recurrence: bool = False
    # Preenchido só quando a IA chama long_wait_tool (sozinha, sem outra tool
    # no turno) — sinaliza pro _run_ai_loop pausar por N segundos
    # (interrompível por NVDA+Shift+C) antes do próximo turno, sem executar
    # nenhum BatchItem. None no caso comum (nenhuma espera longa pendente).
    awaiting_wait_seconds: Optional[float] = None


class ActionProcessor:
    """Processa as function_calls nativas da IA e extrai ações"""

    _last_actions = []
    # Chamadas consecutivas aceitas de long_wait_tool — guarda independente do
    # circuit breaker normal (is_loop_detected), já que esta tool NUNCA passa
    # por run_batch/_last_actions (ver process_function_calls). Sem isso, uma
    # automação com max_turns=300 e long_wait_max_seconds=600 poderia ficar
    # esperando por dezenas de horas sem nenhum outro limite entrar em ação.
    _consecutive_long_waits = 0

    @staticmethod
    def _scale_coordinates(x, y, screen_w, screen_h):
        """Converte coordenadas normalizadas (0-1000) para pixels reais."""
        real_x = int(x * screen_w / addon_config.COORDINATE_SCALE)
        real_y = int(y * screen_h / addon_config.COORDINATE_SCALE)
        return real_x, real_y

    @staticmethod
    def is_loop_detected(action_signature, screen_changed):
        """
        Rastreia a sequência de ações SEM EFEITO (tela não mudou) e retorna
        True se a mesma ação se repetiu LOOP_DETECTION_THRESHOLD vezes
        seguidas nessa sequência — circuit breaker real (quem chama deve de
        fato interromper o loop, não só logar).

        Toda vez que a tela muda, a sequência de "sem efeito" é zerada: uma
        ação repetida que está de fato produzindo efeito (ex: scroll
        avançando numa lista longa) nunca deve ser confundida com um loop.
        """
        if screen_changed:
            ActionProcessor._last_actions = []
            return False

        ActionProcessor._last_actions.append(action_signature)
        if len(ActionProcessor._last_actions) > addon_config.MAX_ACTION_HISTORY:
            ActionProcessor._last_actions.pop(0)

        if len(ActionProcessor._last_actions) >= addon_config.LOOP_DETECTION_THRESHOLD:
            recent = ActionProcessor._last_actions[-addon_config.LOOP_DETECTION_THRESHOLD:]
            if len(set(recent)) == 1:
                logger.warning(
                    f"Loop detectado: ação {recent[0]!r} repetida {len(recent)}x sem mudança de tela"
                )
                return True
        return False

    @staticmethod
    def run_batch(items, pre_batch_hash, execute_fn, capture_fn, should_cancel=None):
        """
        Executa em sequência os itens "execute" de um BatchPlan, abortando o
        restante da fila se uma ação falhar ou a tela mudar no meio —
        mesmo padrão do multi_act() do browser-use (max_actions_per_step +
        abort em mudança de página), adaptado pra mudança de tela em vez de
        URL/DOM. Também aplica o circuit breaker real de loop
        (is_loop_detected) a cada ação física executada.

        Extraído como função pura (recebe `execute_fn`/`capture_fn` como
        callables em vez de chamar ActionExecutor/capture_fullscreen
        diretamente) justamente pra poder ser testado fora do NVDA — ver
        tests/test_execute_batch.py — sem precisar duplicar a lógica numa
        "réplica" que poderia ficar desatualizada em relação ao código real.

        Args:
            items: lista de BatchItem — mutados in-place, cada um recebe seu
                function_response final em `.response`.
            pre_batch_hash: hash MD5 da tela capturada ANTES deste turno
                (baseline para detectar a mudança causada pelo 1º item).
            execute_fn: callable(action_info) -> (success: bool, error: str|None).
                Em produção é GlobalPlugin._execute_batch que passa um
                wrapper fino em volta de ActionExecutor.execute_action.
            capture_fn: callable() -> img_base64 (str) | None. Em produção,
                um wrapper fino em volta de screen_capture.capture_fullscreen.
            should_cancel: callable() -> bool, checado antes de EXECUTAR cada
                item físico (nunca antes de um "support"/"skip", que não
                tocam a tela). Em produção, GlobalPlugin._execute_batch passa
                self._cancel_event.is_set (NVDA+Shift+C). Injetado em vez de
                lido direto do plugin pelo mesmo motivo de execute_fn/
                capture_fn: manter esta função pura/testável sem o GlobalPlugin
                real. Default (None) nunca cancela — mesmo comportamento de
                antes desta opção existir.

        Returns:
            bool: True se o circuit breaker de loop disparou (a automação
            inteira deve ser interrompida); False caso contrário.
        """
        should_cancel = should_cancel or (lambda: False)
        batch_aborted = False
        last_hash = pre_batch_hash

        for item in items:
            if item.kind == "support":
                # Já resolvida em process_function_calls (não mexe na tela)
                # — nunca passa por capture_fn/is_loop_detected, nem conta
                # pra abortar o resto do lote, mesmo se tiver falhado (um
                # schedule_task_tool com data inválida não deveria
                # interromper um click_tool no mesmo turno).
                continue

            if item.kind == "skip":
                # Marcador explícito, independente do texto traduzido de
                # "error" (ver process_function_calls) — checar substring
                # de uma string que passa por gettext quebraria sempre que
                # o catálogo ativo não for o texto original.
                is_truncation = bool(item.response.get("truncated"))
                if item.response.get("success") is False and not is_truncation:
                    batch_aborted = True
                continue

            if batch_aborted:
                item.response = {
                    "success": False,
                    "error": _("Skipped: batch interrupted (a previous action failed or the screen changed)."),
                }
                continue

            if should_cancel():
                item.response = {
                    "success": False,
                    "error": _("Skipped: automation cancelled by the user."),
                }
                batch_aborted = True
                continue

            success, exec_error = execute_fn(item.action_info)

            new_img = capture_fn()
            screen_changed = False
            if new_img:
                new_hash = hashlib.md5(new_img.encode()).hexdigest()
                screen_changed = new_hash != last_hash
                last_hash = new_hash

            item.response = {"success": success, "error": exec_error, "screen_changed": screen_changed}

            if ActionProcessor.is_loop_detected(item.action_info.signature(), screen_changed):
                return True

            if not success or screen_changed:
                batch_aborted = True

        return False

    @staticmethod
    def process_function_calls(function_calls, screen_w, screen_h, support_handlers=None):
        """
        Planeja a execução do lote de function_calls devolvido pelo Gemini
        para este turno (até addon_config.MAX_ACTIONS_PER_TURN ações físicas).

        Só decide o QUE fazer e valida parâmetros — quem efetivamente executa,
        confere se a tela mudou entre uma ação e outra do lote, e interrompe o
        restante da fila nesse caso (ou se uma ação falhar), é a orquestração
        em __init__.py (ActionExecutor + captura de tela), seguindo o mesmo
        padrão de "abortar o resto do lote se o estado mudar no meio" usado
        pelo browser-use.

        Política:
        - Nenhuma function_call: anômalo (mode=ANY deveria sempre retornar uma);
          não executa nada, não finaliza, pede retry no próximo turno.
        - done_tool/ask_user_tool/long_wait_tool junto de outra ferramenta:
          rejeita todas, pede retry limpo (mesma regra de exclusividade pras três).
        - Só done_tool: tarefa concluída, sem ação física.
        - Só ask_user_tool: BatchPlan.awaiting_question preenchido, sem
          ação física — cabe ao chamador (NVDA/wx-aware) pausar e obter a
          resposta do usuário de forma síncrona, fora desta função pura.
        - Só long_wait_tool: BatchPlan.awaiting_wait_seconds preenchido, sem
          ação física — cabe ao chamador pausar (interrompível) por esse
          tempo antes do próximo turno. Nunca passa por run_batch/
          is_loop_detected (diferente de wait_tool, que é kind="execute"
          normal); um contador de classe separado (_consecutive_long_waits)
          evita espera consecutiva sem fim, já que o circuit breaker normal
          nunca chega a ver esta tool.
        - Tool "support" (schedule_task_tool e afins — ver action_tools.
          TOOL_NAME_TO_ACTION_TYPE): resolvida JÁ AQUI via o handler
          correspondente em `support_handlers` (nunca mexe na tela, nunca
          passa por run_batch/capture_fn).
        - Caso contrário: um item por function_call, na ordem recebida. Itens
          com parâmetro inválido ficam "skip" (não executam) — cabe ao
          chamador decidir se isso também aborta o restante do lote. Itens
          além do limite por turno também ficam "skip", sem abortar nada.

        Args:
            support_handlers: dict[str, Callable[[dict], dict]] opcional —
                nome da tool "support" -> handler(args) -> dict de resposta
                (deve conter ao menos "success": bool). Mesma injeção de
                dependência que run_batch já usa pra execute_fn/capture_fn,
                pelo mesmo motivo: manter esta função pura/testável sem
                SQLite real. Tool "support" sem handler injetado (ex: teste
                que não passou nada) devolve um erro gracioso, nunca lança.

        Returns:
            BatchPlan
        """
        if not function_calls:
            logger.warning("Nenhuma function_call recebida da IA (inesperado com mode=ANY)")
            return BatchPlan([], False, _("The AI did not return any valid action."))

        support_handlers = support_handlers or {}

        done_calls = [fc for fc in function_calls if fc.name == "done_tool"]
        ask_user_calls = [fc for fc in function_calls if fc.name == "ask_user_tool"]
        long_wait_calls = [fc for fc in function_calls if fc.name == "long_wait_tool"]

        if not long_wait_calls:
            # Zera sempre que o turno NÃO é uma chamada isolada de
            # long_wait_tool — mesmo princípio do reset de _last_actions em
            # is_loop_detected quando a tela muda: só conta repetições
            # CONSECUTIVAS, nunca o total acumulado da tarefa inteira.
            ActionProcessor._consecutive_long_waits = 0

        if (done_calls or ask_user_calls or long_wait_calls) and len(function_calls) > 1:
            if done_calls:
                exclusive_name = "done_tool"
            elif ask_user_calls:
                exclusive_name = "ask_user_tool"
            else:
                exclusive_name = "long_wait_tool"
            explanation = _("%s cannot be called together with another tool in the same turn.") % exclusive_name
            items = [
                BatchItem(fc.name, fc.id, "skip", {"success": False, "error": explanation})
                for fc in function_calls
            ]
            return BatchPlan(items, False, explanation)

        if done_calls:
            fc = done_calls[0]
            args = fc.args or {}
            error = action_tools.validate_args(fc.name, args)
            if error:
                item = BatchItem(fc.name, fc.id, "skip", {"success": False, "error": error})
                return BatchPlan([item], False, error)
            summary = args.get("summary", "")
            success = bool(args.get("success"))
            item = BatchItem(fc.name, fc.id, "skip", {"success": success, "acknowledged": True})
            # changed fica None (não True/False) quando a IA não mandou nada —
            # distinção importante pra GlobalPlugin._finalize_scheduled_task
            # tratar "não informou" como "mudou" (nunca suprime por omissão).
            return BatchPlan(
                [item], True, summary, success,
                changed=args.get("changed"),
                result_data=args.get("result_data"),
                stop_recurrence=bool(args.get("stop_recurrence", False)),
            )

        if ask_user_calls:
            fc = ask_user_calls[0]
            args = fc.args or {}
            error = action_tools.validate_args(fc.name, args)
            if error:
                item = BatchItem(fc.name, fc.id, "skip", {"success": False, "error": error})
                return BatchPlan([item], False, error)
            question = args.get("question", "")
            item = BatchItem(fc.name, fc.id, "skip")  # .response preenchido depois, com a resposta do usuário
            return BatchPlan([item], False, question, awaiting_question=question)

        if long_wait_calls:
            fc = long_wait_calls[0]
            args = fc.args or {}
            error = action_tools.validate_args(fc.name, args)
            if error:
                item = BatchItem(fc.name, fc.id, "skip", {"success": False, "error": error})
                return BatchPlan([item], False, error)

            if ActionProcessor._consecutive_long_waits >= addon_config.MAX_CONSECUTIVE_LONG_WAITS:
                # NUNCA zera o contador aqui — fazer isso devolveria outras
                # MAX_CONSECUTIVE_LONG_WAITS chamadas de graça na próxima vez
                # que a IA chamasse long_wait_tool de novo isoladamente,
                # anulando o próprio propósito do teto (ciclo "N aceitas + 1
                # rejeitada" se repetindo pra sempre). Só o reset no topo da
                # função (quando o turno NÃO é um long_wait_tool isolado) tira
                # a IA deste estado.
                error = _(
                    "Wait limit reached (%(limit)d consecutive calls) — act now, finish with "
                    "done_tool, or use schedule_task_tool to check back later instead of "
                    "waiting again."
                ) % {"limit": addon_config.MAX_CONSECUTIVE_LONG_WAITS}
                item = BatchItem(fc.name, fc.id, "skip", {"success": False, "error": error})
                return BatchPlan([item], False, error)

            ActionProcessor._consecutive_long_waits += 1
            # float(), não o valor cru — validate_args só CONFERE que é
            # convertível, não transforma; sem isso, awaiting_wait_seconds
            # poderia carregar uma string ("90.5") e quebrar os int(...) em
            # process_function_calls/_run_ai_loop com um ValueError.
            seconds = float(args.get("seconds"))
            item = BatchItem(fc.name, fc.id, "skip")  # .response preenchido depois, após a espera real
            explanation = _("Waiting %(seconds)d seconds before checking again...") % {"seconds": int(seconds)}
            return BatchPlan([item], False, explanation, awaiting_wait_seconds=seconds)

        items = []
        explanations = []
        for i, call in enumerate(function_calls):
            if i >= addon_config.MAX_ACTIONS_PER_TURN:
                items.append(BatchItem(
                    call.name, call.id, "skip",
                    {
                        "success": False,
                        "error": _("Skipped: turn action limit of %(limit)d exceeded.") % {
                            "limit": addon_config.MAX_ACTIONS_PER_TURN,
                        },
                        "truncated": True,
                    }
                ))
                continue

            action_type = action_tools.TOOL_NAME_TO_ACTION_TYPE.get(call.name)
            error = action_tools.validate_args(call.name, call.args)
            if error:
                items.append(BatchItem(call.name, call.id, "skip", {"success": False, "error": error}))
                explanations.append(error)
                continue

            if action_type == "support":
                handler = support_handlers.get(call.name)
                if handler is None:
                    response = {"success": False, "error": _("Support function not available in this context.")}
                else:
                    response = handler(call.args or {})
                items.append(BatchItem(call.name, call.id, "support", response=response))
                explanations.append(response.get("message") or response.get("error") or call.name)
                continue

            action_info = ActionProcessor._build_action_info(action_type, call.args or {}, screen_w, screen_h)
            items.append(BatchItem(call.name, call.id, "execute", action_info=action_info))
            explanations.append(ActionProcessor._describe_action(action_type, call.args or {}))

        return BatchPlan(items, False, " | ".join(explanations) if explanations else "")

    @staticmethod
    def _build_action_info(action_type, args, screen_w, screen_h):
        if action_type == "keyboard":
            return ActionInfo(action_type="keyboard", keys=args.get("keys", ""))

        if action_type == "wait":
            return ActionInfo(action_type="wait", seconds=args.get("seconds") or 2)

        if action_type == "scroll":
            x, y = args.get("x"), args.get("y")
            if x is not None and y is not None:
                real_x, real_y = ActionProcessor._scale_coordinates(x, y, screen_w, screen_h)
            else:
                # O scroll do mouse é entregue à janela SOB o cursor, não à
                # janela em foco — sem x/y explícito, usamos o centro da tela.
                real_x, real_y = screen_w // 2, screen_h // 2
            return ActionInfo(
                action_type="scroll",
                x=real_x, y=real_y,
                direction=args.get("direction", "down"),
                amount=args.get("amount") or addon_config.DEFAULT_SCROLL_AMOUNT,
            )

        if action_type == "type":
            x, y = args.get("x"), args.get("y")
            real_x, real_y = (None, None)
            if x is not None and y is not None:
                real_x, real_y = ActionProcessor._scale_coordinates(x, y, screen_w, screen_h)
            return ActionInfo(
                action_type="type",
                x=real_x, y=real_y,
                text=args.get("text", ""),
                press_enter=bool(args.get("press_enter", False)),
            )

        if action_type == "launch_app":
            return ActionInfo(action_type="launch_app", app_name=args.get("app_name", ""))

        if action_type == "shell_command":
            return ActionInfo(
                action_type="shell_command",
                command=args.get("command", ""),
                reason=args.get("reason", ""),
            )

        # click / right_click / double_click
        real_x, real_y = ActionProcessor._scale_coordinates(args.get("x"), args.get("y"), screen_w, screen_h)
        return ActionInfo(action_type=action_type, x=real_x, y=real_y)

    @staticmethod
    def _describe_action(action_type, args):
        """Descrição curta de fallback, usada quando a IA não manda texto junto da tool call."""
        if action_type == "keyboard":
            return _("Sending shortcut %s") % args.get('keys', '')
        if action_type == "type":
            return _("Typing: %s") % args.get('text', '')
        if action_type == "scroll":
            return _("Scrolling %s") % args.get('direction', 'down')
        if action_type == "wait":
            return _("Waiting...")
        if action_type == "launch_app":
            return _("Opening %s") % args.get('app_name', '')
        if action_type == "shell_command":
            return _("Terminal command: %s") % args.get('reason', '')
        return _("Running %s") % action_type
