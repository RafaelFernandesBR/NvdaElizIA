# -*- coding: utf-8 -*-
"""
Executor de ações do sistema
Executa ações de mouse e teclado
"""

import time
import ctypes
import os
import subprocess
import winUser
import mouseHandler
import keyboardHandler
import api
from . import addon_config
import logging

logger = logging.getLogger(__name__)

# Estruturas para injetar o evento de roda do mouse via SendInput.
# mouse_event() é a API legada e, em navegadores/apps modernos baseados em
# Chromium, seus eventos de roda às vezes não chegam ao elemento correto
# (o cursor pode estar sobre o HWND certo mas o hit-test interno do app não
# reage). SendInput é a API recomendada pela Microsoft desde o Windows 2000
# e é o que ferramentas de automação confiáveis usam.
ULONG_PTR = ctypes.c_size_t
INPUT_MOUSE = 0


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    )


class _INPUTUnion(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT),)


class INPUT(ctypes.Structure):
    _anonymous_ = ("_input",)
    _fields_ = (
        ("type", ctypes.c_ulong),
        ("_input", _INPUTUnion),
    )


class ActionExecutor:
    """Executa ações de mouse e teclado no sistema"""

    # Sinônimos entre o vocabulário coloquial que a IA usa no prompt
    # (addon_config.AI_OPERATOR_PROMPT_TEMPLATE, ex: "win+r", "ctrl+c",
    # "esc") e os nomes que keyboardHandler.KeyboardInputGesture.fromName
    # espera (vkCodes.byName) — ver execute_keyboard_shortcut. "nvda" não
    # entra aqui: fromName já trata esse nome como palavra reservada
    # (resolve pra tecla modificadora do NVDA configurada pelo usuário).
    _KEY_NAME_SYNONYMS = {
        "win": "windows",
        "ctrl": "control",
        "esc": "escape",
        "del": "delete",
        "up": "uparrow",
        "down": "downarrow",
        "left": "leftarrow",
        "right": "rightarrow",
    }

    @staticmethod
    def execute_mouse_action(x, y, action_type):
        """
        Executa uma ação de mouse.
        
        Args:
            x: Coordenada X em pixels
            y: Coordenada Y em pixels
            action_type: Tipo de ação (click, right_click, double_click)
        """
        winUser.setCursorPos(x, y)
        time.sleep(addon_config.CLICK_DELAY)

        if action_type == "right_click":
            mouseHandler.doSecondaryClick()
        elif action_type == "double_click":
            mouseHandler.doPrimaryClick()
            time.sleep(addon_config.DOUBLE_CLICK_DELAY)
            mouseHandler.doPrimaryClick()
        else:
            mouseHandler.doPrimaryClick()

        time.sleep(addon_config.MOUSE_CLICK_POST_DELAY)

    @staticmethod
    def execute_scroll_action(x, y, direction, amount=3):
        """
        Executa scroll (rolar tela) com a roda do mouse.
        
        Args:
            x: Coordenada X onde posicionar o cursor antes de rolar
            y: Coordenada Y onde posicionar o cursor antes de rolar
            direction: Direção do scroll ("up" ou "down")
            amount: Quantidade de "cliques" da roda (padrão: 3)
        """
        if x > 0 and y > 0:
            winUser.setCursorPos(x, y)
            time.sleep(addon_config.KEYBOARD_KEY_DELAY)

        WHEEL_DELTA = 120
        delta = WHEEL_DELTA * amount

        # Scroll para cima = positivo, para baixo = negativo
        if direction.lower() == "down":
            delta = -delta

        logger.debug(
            f"scroll: pos=({x},{y}) direction={direction} amount={amount} delta={delta}"
        )

        sent = ActionExecutor._send_wheel_event(delta)
        if not sent:
            logger.debug("scroll: SendInput/mouse_event falharam, usando fallback de teclado (PageUp/PageDown)")
            key_name = "pagedown" if direction.lower() == "down" else "pageup"
            for _ in range(amount):
                keyboardHandler.KeyboardInputGesture.fromName(key_name).send()
                time.sleep(addon_config.DOUBLE_CLICK_DELAY)

    @staticmethod
    def _send_wheel_event(delta):
        """
        Injeta um evento de roda do mouse (scroll) na posição atual do cursor.
        Tenta SendInput (API moderna) primeiro; cai para mouse_event (legado)
        se SendInput não estiver disponível/falhar.

        Returns:
            bool: True se algum dos dois métodos foi executado sem lançar exceção
        """
        try:
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp.mi = MOUSEINPUT(0, 0, delta, addon_config.MOUSEEVENTF_WHEEL, 0, ULONG_PTR(0))
            sent_count = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            time.sleep(addon_config.PASTE_DELAY)
            if sent_count == 1:
                return True
            logger.debug(f"scroll: SendInput retornou {sent_count} (esperado 1)")
        except Exception as e:
            logger.debug(f"scroll: SendInput lançou exceção: {e}")

        try:
            winUser.mouse_event(addon_config.MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
            time.sleep(addon_config.PASTE_DELAY)
            return True
        except Exception as e:
            logger.debug(f"scroll: mouse_event (legado) também falhou: {e}")
            return False
    
    @staticmethod
    def execute_type_action(x, y, text, press_enter=False, focus_event=None):
        """
        Digita texto em um campo.

        Args:
            x: Coordenada X do campo
            y: Coordenada Y do campo
            text: Texto a digitar
            press_enter: Se deve pressionar Enter ao final
            focus_event: threading.Event opcional, sinalizado pelo
                GlobalPlugin.event_gainFocus (ver __init__.py) sempre que o
                NVDA detecta troca de foco de CONTROLE — usado aqui pra
                esperar o campo clicado realmente ganhar foco antes de
                colar, em vez de dormir um tempo fixo. Importante:
                GetForegroundWindow() NÃO serve pra esse caso — só reage a
                troca de JANELA, não de campo dentro da mesma janela (é
                exatamente o que event_gainFocus cobre, via EVENT_OBJECT_FOCUS
                do Windows). Sem esse evento (chamada direta/teste), cai no
                fallback de sleep fixo.
        """
        if x is not None and y is not None:
            if focus_event is not None:
                # Limpa ANTES do clique — se limpasse depois, um gainFocus
                # muito rápido poderia ser perdido na janela entre o clique
                # e a chamada de wait() abaixo.
                focus_event.clear()
            else:
                time.sleep(addon_config.TYPE_CLICK_SETTLE_FALLBACK_DELAY)

            winUser.setCursorPos(x, y)
            mouseHandler.doPrimaryClick()

            if focus_event is not None:
                # Se o clique não mudar o foco de controle (ex: campo já
                # focado, ou área não focável), expira no teto sem travar —
                # mesmo comportamento "fail-open" da espera adaptativa de tela.
                focus_event.wait(addon_config.TYPE_FOCUS_WAIT_TIMEOUT)
            else:
                time.sleep(addon_config.TYPE_CLICK_SETTLE_FALLBACK_DELAY)

        old_clip = ActionExecutor._save_clipboard()

        # Cola texto via clipboard (mais confiável que envio de teclas)
        clean_text = text.replace('\n', '').strip()
        ActionExecutor._paste_text(clean_text)

        ActionExecutor._restore_clipboard(old_clip)
        time.sleep(addon_config.CLIPBOARD_RESTORE_SETTLE_DELAY)

        if press_enter:
            time.sleep(addon_config.TYPE_PRE_ENTER_DELAY)
            ActionExecutor._press_enter()

    @staticmethod
    def _save_clipboard():
        """
        Salva o conteúdo atual da área de transferência.
        
        Returns:
            str: Conteúdo atual ou None se erro
        """
        try:
            return api.getClipData()
        except (RuntimeError, OSError):
            return None
    
    @staticmethod
    def _restore_clipboard(content):
        """
        Restaura conteúdo da área de transferência.
        
        Args:
            content: Conteúdo a restaurar
        """
        if content:
            try:
                api.copyToClip(content)
            except (RuntimeError, OSError):
                pass
    
    @staticmethod
    def _paste_text(text):
        """
        Cola texto usando Ctrl+V.
        Salva e restaura o foco para evitar que operações de clipboard roubem o foco.
        """
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()

            api.copyToClip(text)
            time.sleep(addon_config.CLIPBOARD_WRITE_SETTLE_DELAY)

            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(addon_config.KEYBOARD_KEY_DELAY)

            keyboardHandler.KeyboardInputGesture.fromName("control+v").send()
            time.sleep(addon_config.PASTE_KEY_RELEASE_DELAY)
        except Exception as e:
            logger.error(f"Error in _paste_text: {e}")

    @staticmethod
    def _press_enter():
        """Pressiona a tecla Enter"""
        keyboardHandler.KeyboardInputGesture.fromName("enter").send()
    
    @staticmethod
    def execute_keyboard_shortcut(keys_string):
        """
        Executa uma combinação de teclas (atalho de teclado) via
        keyboardHandler.KeyboardInputGesture.fromName — o mesmo mecanismo
        que sustenta os bindings "kb:..." do próprio addon em __gestures
        (ver scriptHandler._makeKbEmulateScript no código-fonte do NVDA).

        Args:
            keys_string: String com as teclas, separadas por '+'
                        Exemplos: "win+r", "ctrl+c", "alt+f4", "ctrl+shift+esc"

        Returns:
            tuple (success: bool, error: str|None)
        """
        keys = [k.strip().lower() for k in keys_string.split('+')]
        normalized = "+".join(ActionExecutor._KEY_NAME_SYNONYMS.get(k, k) for k in keys)

        try:
            keyboardHandler.KeyboardInputGesture.fromName(normalized).send()
            time.sleep(addon_config.PASTE_DELAY)
            return True, None
        except ValueError as e:
            logger.warning(f"Tecla não reconhecida no atalho {keys_string!r}: {e}")
            return False, str(e)
        except Exception as e:
            logger.error(f"Erro ao executar atalho de teclado {keys_string!r}: {e}", exc_info=True)
            return False, str(e)

    @staticmethod
    def execute_app_launch(app_name):
        """
        Abre um programa pelo nome (app_tool). Sucesso aqui é "comando de
        abertura enviado", não "app confirmado aberto" — a IA valida
        visualmente no próximo turno, mesmo contrato de confiança que
        click_tool já tem hoje.

        Returns:
            tuple (success: bool, error: str|None)
        """
        try:
            os.startfile(app_name)
            return True, None
        except OSError:
            pass  # cai pro fallback abaixo (mesma resolução de PATH/App Paths do diálogo Executar)

        try:
            subprocess.Popen(["cmd", "/c", "start", "", app_name])
            return True, None
        except OSError as e:
            logger.warning(f"Falha ao abrir app {app_name!r}: {e}")
            return False, str(e)

    @staticmethod
    def execute_shell_command(command, timeout_seconds):
        """
        Executa um comando no cmd.exe (shell_tool). A confirmação bloqueante
        do usuário já aconteceu ANTES desta chamada (ver GlobalPlugin.
        _execute_batch/_confirm_shell_command em __init__.py) — esta função
        só executa e nunca pede confirmação por conta própria.

        Returns:
            tuple (success: bool, error: str|None) — error carrega
            stdout+stderr truncados, pra IA ver o que aconteceu mesmo
            quando o comando falha (returncode != 0).
        """
        try:
            result = subprocess.run(
                ["cmd", "/c", command],
                capture_output=True, text=True, timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return False, f"Comando excedeu o tempo limite de {timeout_seconds}s."
        except OSError as e:
            return False, str(e)

        limit = addon_config.SHELL_OUTPUT_TRUNCATE_CHARS
        output = (result.stdout or "")[:limit]
        error_output = (result.stderr or "")[:limit]
        if result.returncode == 0:
            # Reaproveita o 2º elemento da tupla (sempre vira o campo
            # "error" do function_response, ver run_batch) pra carregar o
            # stdout mesmo em sucesso — é o único canal de texto de volta
            # pra IA, e sem ver a saída o shell_tool não serviria pra nada.
            return True, output or None
        return False, error_output or output or f"Comando terminou com código {result.returncode}."

    @staticmethod
    def execute_action(action_info, focus_event=None):
        """
        Executa uma ação baseada no ActionInfo.

        Args:
            action_info: Objeto ActionInfo com detalhes da ação
            focus_event: threading.Event opcional (ver execute_type_action) —
                só tem efeito pra action_type == "type".

        Returns:
            tuple (success: bool, error: str|None) — permite informar o
            resultado real da execução de volta à IA via function_response,
            em vez de assumir sucesso silenciosamente.
        """
        logger.debug(f"Executando ação: {action_info!r}")

        if action_info.action_type == "keyboard":
            if not action_info.keys:
                return False, "keyboard_tool chamado sem 'keys'"
            return ActionExecutor.execute_keyboard_shortcut(action_info.keys)

        elif action_info.action_type == "scroll":
            ActionExecutor.execute_scroll_action(
                action_info.x, action_info.y, action_info.direction, action_info.amount
            )
            return True, None

        elif action_info.action_type == "type":
            if not action_info.text:
                return False, "type_tool chamado sem 'text'"
            ActionExecutor.execute_type_action(
                action_info.x, action_info.y, action_info.text, action_info.press_enter,
                focus_event=focus_event,
            )
            return True, None

        elif action_info.action_type == "wait":
            time.sleep(min(max(action_info.seconds or 2, 0), 10))
            return True, None

        elif action_info.action_type in ("click", "right_click", "double_click"):
            ActionExecutor.execute_mouse_action(action_info.x, action_info.y, action_info.action_type)
            return True, None

        elif action_info.action_type == "launch_app":
            if not action_info.app_name:
                return False, "app_tool chamado sem 'app_name'"
            return ActionExecutor.execute_app_launch(action_info.app_name)

        elif action_info.action_type == "shell_command":
            # Nunca executado a partir daqui: a confirmação bloqueante do
            # usuário (obrigatória, ver GlobalPlugin._confirm_shell_command
            # em __init__.py) precisa acontecer ANTES, na camada NVDA/wx-aware
            # — por isso o execute_fn de _execute_batch intercepta
            # "shell_command" e chama ActionExecutor.execute_shell_command
            # diretamente, sem nunca passar por aqui. Se isso chegar até
            # execute_action mesmo assim (bug de wiring), recusa — nunca
            # executa um comando de shell sem confirmação, por padrão nenhum.
            logger.error("shell_command chegou em execute_action sem passar pela confirmação — recusando.")
            return False, "shell_tool precisa de confirmação do usuário antes de executar."

        # action_type desconhecido: nunca cair em clique por padrão — para um
        # usuário cego, nenhuma ação é mais seguro que uma ação errada.
        # Só deveria acontecer por bug interno, já que action_tools.py fecha
        # o conjunto de action_type possíveis antes de chegar aqui.
        logger.error(f"action_type desconhecido, nada será executado: {action_info.action_type!r}")
        return False, f"Tipo de ação desconhecido: {action_info.action_type!r}"
