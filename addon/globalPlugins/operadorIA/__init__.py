# -*- coding: utf-8 -*-
"""
ElizIA Visual - Addon NVDA
Controla o Windows usando IA Gemini e capturas de tela
"""

from collections import deque
from datetime import datetime
import base64
import hashlib
import threading
import time
import sys
import os
import logging
import wx

import globalPluginHandler
import ui
import tones
import gui
import speech
import speechViewer
import versionInfo
import config
import globalVars
from queueHandler import queueFunction, eventQueue
from eventHandler import FocusLossCancellableSpeechCommand

lib_path = os.path.join(os.path.dirname(__file__), 'lib')
if lib_path not in sys.path:
	sys.path.append(lib_path)

# sqlite3 não vem na build de Python embutida no NVDA (falta o módulo
# inteiro) — por isso vendorizamos sqlite3/ (puro Python), _sqlite3.pyd
# (extensão compilada) e sqlite3.dll (biblioteca nativa que o .pyd carrega
# em runtime) direto em lib/, copiados de um CPython 3.13 win_amd64 comum
# (mesmo ABI já usado pelos outros .pyd bundled aqui, ex: _cffi_backend).
# add_dll_directory é necessário pro Windows achar sqlite3.dll: desde o
# Python 3.8, a pasta do próprio .pyd não é mais buscada automaticamente
# pra dependências de DLL — precisa ser registrada explicitamente.
if hasattr(os, 'add_dll_directory'):
	os.add_dll_directory(lib_path)

from google.genai import types

from . import addon_config
from .screen_capture import capture_fullscreen
from .screen_curtain import is_screen_curtain_active
from .screen_wait import wait_until_stable
from .gemini_client import GeminiClient
from .action_processor import ActionProcessor
from .action_executor import ActionExecutor
from .chat_dialog import OperatorChatDialog
from .sessions_dialog import SessionsDialog
from .session_store import SessionStore, derive_title
from .scheduler_store import SchedulerStore
from .scheduler import TaskScheduler, seconds_since_last_input
from .scheduler_dialog import SchedulerDialog, GraceCountdownDialog
from .utils import (
    clean_markdown,
    get_focused_app_name,
    build_system_instruction,
    build_next_turn_parts,
    ConversationHistory
)

logger = logging.getLogger(__name__)

# Definir confspec para NVDA config system
confspec = {
	"gemini_api_key": "string(default='')",
	"gemini_model": "string(default='gemini-2.5-flash')",
	"api_timeout": "integer(default=30, min=1, max=300)",
	"response_language": "string(default='pt-br')",
	"ai_temperature": "float(default=0.4, min=0.0, max=2.0)",
	"max_turns": "integer(default=15, min=1, max=50)",
	"turn_delay": "float(default=2.0, min=0.1, max=10.0)",
	"max_sessions": "integer(default=50, min=1, max=500)",
	"scheduler_check_interval": "integer(default=30, min=5, max=3600)",
	"scheduler_confirm_grace_seconds": "integer(default=20, min=5, max=120)",
	"ask_user_timeout_seconds": "integer(default=120, min=10, max=600)",
	"shell_command_timeout_seconds": "integer(default=15, min=1, max=120)",
	"enable_shell_tool": "boolean(default=False)",
}
config.conf.spec[addon_config.CONFIG_SECTION] = confspec


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    # Agrupa os comandos deste addon sob um só nome na tela de "Gestos de
    # Entrada" do NVDA (Preferências > Gestos de entrada), onde o usuário
    # pode reatribuir as teclas livremente. Os atalhos padrão ficam só em
    # __gestures (fim da classe) — nunca fixos via @scriptHandler.script(gesture=...).
    scriptCategory = addon_config.ADDON_FULL_NAME

    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.conversation_history = ConversationHistory()
        # Criado sob demanda em _run_ai_loop, não aqui: o SDK google-genai
        # valida a API key no construtor e lança ValueError se estiver vazia
        # (padrão em uma instalação nova) — isso travaria a inicialização do
        # GlobalPlugin inteiro e, com ela, o registro do SettingsPanel mais
        # abaixo, impedindo o usuário de configurar a chave pela própria UI.
        self.gemini_client = None
        # Reserva a automação (atalho, chat, retomada de sessão, scheduler —
        # 4 pontos de entrada) sem corrida: antes era um bool solto
        # (self.processing) lido/escrito de threads diferentes sem proteção
        # — TOCTOU real entre checar "já em execução?" e de fato começar.
        # _try_start_automation()/_finish_automation() são os únicos pontos
        # de contato; .locked() serve só pra um aviso prévio (não consome).
        self._automation_lock = threading.Lock()
        self.chat_dialog = None
        self.last_screen_info = None
        self._current_goal = None
        self._system_instruction = None
        self._speech_history = deque(maxlen=addon_config.SPEECH_HISTORY_SIZE)
        # Escrito pela thread do eventQueue do NVDA (via queueFunction em
        # _capture_speech), lido pela thread de automação em _get_speech_context.
        self._speech_history_lock = threading.Lock()
        self._patch_speech()

        # Sinalizado por event_gainFocus (chamado pelo NVDA na MAIN THREAD
        # sempre que o foco de CONTROLE muda — não só janela, diferente de
        # GetForegroundWindow) — usado por ActionExecutor.execute_type_action
        # pra esperar um campo clicado ganhar foco de verdade antes de
        # colar texto, em vez de dormir um tempo fixo.
        self._focus_gained_event = threading.Event()

        # Banco de sessões: desabilitado em modo seguro (tela de login do
        # Windows) pelo mesmo motivo do SettingsPanel abaixo — nunca
        # persistir comandos digitados nessa tela. Falha ao criar o banco
        # (disco cheio, permissão, arquivo corrompido) não pode travar a
        # inicialização do GlobalPlugin inteiro — mesmo cuidado já aplicado
        # ao gemini_client (criado sob demanda, não aqui).
        self.session_store = None
        self.current_session_id = None
        if not globalVars.appArgs.secure:
            try:
                self.session_store = SessionStore(self._resolve_session_db_path())
            except Exception as e:
                logger.error(f"Falha ao inicializar o banco de sessões: {e}", exc_info=True)

        # Agendamento de tarefas: mesma guarda de modo seguro do session_store
        # acima (nunca persistir/disparar nada na tela de login do Windows).
        # Mesmo arquivo .db das sessões — tabela própria (scheduler_store.py).
        self.scheduler_store = None
        self.task_scheduler = None
        if not globalVars.appArgs.secure:
            try:
                self.scheduler_store = SchedulerStore(self._resolve_session_db_path())
                self.task_scheduler = TaskScheduler(
                    self.scheduler_store,
                    on_due_task=self._on_scheduled_task_due,
                    check_interval_provider=lambda: config.conf[addon_config.CONFIG_SECTION]["scheduler_check_interval"],
                )
                self.task_scheduler.start()
                # Adiado via CallAfter: __init__ do GlobalPlugin roda durante
                # a inicialização do NVDA, antes do wx main loop estar
                # bombeando de verdade — popular um diálogo aqui bloquearia.
                wx.CallAfter(self._check_missed_tasks_on_startup)
            except Exception as e:
                logger.error(f"Falha ao inicializar o agendador de tarefas: {e}", exc_info=True)

        if not globalVars.appArgs.secure:
            from .settings import SettingsPanel
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.append(SettingsPanel)

    def _resolve_session_db_path(self):
        """
        Diretório do perfil NVDA ativo (ao lado de nvda.ini) — sobrevive a
        atualizações do addon, diferente de salvar dentro da própria pasta
        do addon (recriada do zero em updates).
        """
        return os.path.join(globalVars.appArgs.configPath, addon_config.SESSION_DB_FILENAME)

    def _get_speech_module(self):
        """Retorna módulo speech correto baseado na versão NVDA."""
        build_year = getattr(versionInfo, 'version_year', 2021)
        return speech.speech if build_year >= 2021 else speech

    def _patch_speech(self):
        try:
            speech_module = self._get_speech_module()
            self._original_speak = speech_module.speak
            speech_module.speak = self._capture_speech
        except Exception:
            self._original_speak = None

    def _capture_speech(self, sequence, *args, **kwargs):
        if self._original_speak:
            self._original_speak(sequence, *args, **kwargs)
        text = self._get_sequence_text(sequence)
        if text.strip():
            queueFunction(eventQueue, self._append_to_speech_history, text)

    def _append_to_speech_history(self, text):
        with self._speech_history_lock:
            if self._speech_history and self._speech_history[0] == text:
                # NVDA repetiu a mesma frase (comum) — não duplica bullet no prompt.
                return
            self._speech_history.appendleft(text)

    def _get_sequence_text(self, sequence):
        seq = [cmd for cmd in sequence if not isinstance(cmd, FocusLossCancellableSpeechCommand)]
        return speechViewer.SPEECH_ITEM_SEPARATOR.join([x for x in seq if isinstance(x, str)])

    def _get_speech_context(self):
        with self._speech_history_lock:
            if not self._speech_history:
                return ""
            recent_speech = list(reversed(self._speech_history))
        context = "\n".join([f"  • {s}" for s in recent_speech])
        return f"\n🎤 CONTEXTO DE ÁUDIO RECENTE (o que o usuário ouviu):\n{context}\nUse este contexto para entender melhor o estado atual."

    def terminate(self):
        super().terminate()
        if self._original_speak:
            try:
                speech_module = self._get_speech_module()
                speech_module.speak = self._original_speak
            except Exception:
                pass

        # Parar o timer do agendador — sem isso ele sobrevive a um reload
        # do addon (o timer em si não é daemon, é um gui.NonReEntrantTimer
        # vivo na GUI thread do próprio NVDA).
        if self.task_scheduler is not None:
            self.task_scheduler.stop()

        try:
            from .settings import SettingsPanel
            gui.settingsDialogs.NVDASettingsDialog.categoryClasses.remove(SettingsPanel)
        except (ValueError, AttributeError):
            pass

    def event_gainFocus(self, obj, nextHandler):
        """
        Chamado pelo próprio NVDA, na MAIN THREAD, sempre que o foco de
        CONTROLE muda em qualquer app (via EVENT_OBJECT_FOCUS do Windows) —
        mesmo mecanismo público usado por appModules nativos do NVDA
        (ex: thunderbird.py, spotify.py). Nunca bloquear aqui (travaria o
        NVDA inteiro) e sempre repassar pra nextHandler(), senão quebra a
        cadeia pra app modules/tree interceptors que viriam depois.
        """
        self._focus_gained_event.set()
        nextHandler()

    def script_iniciarOperadorIA(self, gesture):
        if self._automation_lock.locked():
            ui.message(f"{addon_config.ADDON_NAME} já está em execução")
            return
        wx.CallAfter(self._start_new_task_flow)
    script_iniciarOperadorIA.__doc__ = f"Inicia o {addon_config.ADDON_FULL_NAME} para controlar o Windows"

    def script_gerenciarSessoes(self, gesture):
        if self._automation_lock.locked():
            ui.message(f"{addon_config.ADDON_NAME} já está em execução")
            return
        if self.session_store is None:
            ui.message("Gerenciamento de sessões não está disponível.")
            return

        def show_dialog():
            gui.mainFrame.prePopup()
            dlg = SessionsDialog(gui.mainFrame, self.session_store, self._resume_session, self._start_new_task_flow)
            dlg.Show()
            gui.mainFrame.postPopup()

        wx.CallAfter(show_dialog)
    script_gerenciarSessoes.__doc__ = f"Abre a lista de sessões salvas do {addon_config.ADDON_FULL_NAME}"

    def script_gerenciarAgendamentos(self, gesture):
        if self.scheduler_store is None:
            ui.message("Agendamento de tarefas não está disponível.")
            return

        def show_dialog():
            gui.mainFrame.prePopup()
            dlg = SchedulerDialog(gui.mainFrame, self.scheduler_store)
            dlg.Show()
            gui.mainFrame.postPopup()

        wx.CallAfter(show_dialog)
    script_gerenciarAgendamentos.__doc__ = f"Abre a lista de tarefas agendadas do {addon_config.ADDON_FULL_NAME}"

    def _start_new_task_flow(self):
        """
        Pede o comando ao usuário e inicia uma tarefa nova — reusado tanto
        pelo atalho NVDA+Shift+R quanto pelo botão "Nova sessão" do
        SessionsDialog. Já deve rodar na GUI thread (via wx.CallAfter).
        """
        gui.mainFrame.prePopup()
        dlg = wx.TextEntryDialog(
            gui.mainFrame,
            "O que você quer que eu faça?",
            addon_config.ADDON_FULL_NAME
        )

        if dlg.ShowModal() == wx.ID_OK:
            command = dlg.GetValue().strip()
            dlg.Destroy()
            gui.mainFrame.postPopup()

            if command:
                if not self._try_start_automation():
                    ui.message(f"{addon_config.ADDON_NAME} já está em execução")
                    return
                ui.message("Processando...")
                time.sleep(addon_config.SHORT_DELAY)
                self._begin_new_automation(command)
            return

        dlg.Destroy()
        gui.mainFrame.postPopup()

    def _try_start_automation(self):
        """Tenta reservar a automação pra esta chamada, sem bloquear. True = conseguiu (chamador passa a ser responsável por _finish_automation(), chamado dentro de _run_ai_loop); False = já há algo em andamento."""
        return self._automation_lock.acquire(blocking=False)

    def _finish_automation(self):
        self._automation_lock.release()

    def _begin_new_automation(self, command, steal_focus=True, scheduled_task_id=None):
        """
        Monta uma sessão nova e inicia _run_ai_loop numa thread separada —
        corpo compartilhado entre o fluxo interativo (_start_new_task_flow,
        depois do wx.TextEntryDialog) e o fluxo agendado
        (_start_scheduled_task_run, sem diálogo — o comando já vem do
        scheduler_store). Chamador precisa já ter chamado
        _try_start_automation() com sucesso antes de chamar isto — a
        liberação acontece dentro de _run_ai_loop (finally), na nova thread.
        """
        logger.info(f"Novo comando recebido: {command!r}")
        self.conversation_history.clear()
        self._speech_history.clear()
        ActionProcessor._last_actions = []

        if self.chat_dialog:
            try:
                self.chat_dialog.Destroy()
            except Exception:
                pass
            self.chat_dialog = None

        if self.session_store is not None:
            max_sessions = config.conf[addon_config.CONFIG_SECTION]["max_sessions"]
            title = derive_title(command, addon_config.SESSION_TITLE_MAX_LENGTH)
            if scheduled_task_id is not None:
                title = addon_config.SCHEDULED_TASK_TITLE_PREFIX + title
            self.current_session_id = self.session_store.create_session(title, max_sessions=max_sessions)
        else:
            self.current_session_id = None

        threading.Thread(
            target=self._run_ai_loop,
            args=(command,),
            kwargs={"steal_focus": steal_focus, "scheduled_task_id": scheduled_task_id},
            daemon=True,
        ).start()

    def _autosave_session(self):
        """
        Persiste o estado atual de conversation_history na sessão corrente.
        Chamado só nos pontos de _run_ai_loop onde o valor final de
        set_last_model_content do turno já foi decidido — nunca entre essa
        decisão e o autosave, pra nunca persistir uma function_call sem
        function_response possível (mesmo invariante documentado em
        utils.ConversationHistory.set_last_model_content). Falha de disco
        nunca interrompe a automação, só é logada.
        """
        if self.session_store is None or self.current_session_id is None:
            return
        try:
            self.session_store.update_session(self.current_session_id, **self.conversation_history.to_dict())
        except Exception as e:
            logger.error(f"Falha ao salvar sessão automaticamente: {e}", exc_info=True)

    def _resume_session(self, session_id):
        """
        Retoma uma sessão salva — chamado numa thread separada pelo
        SessionsDialog (leitura do sqlite + reconstrução de
        ConversationHistory fora da GUI thread).
        """
        if self._automation_lock.locked():
            wx.CallAfter(ui.message, f"{addon_config.ADDON_NAME} já está em execução")
            return
        if self.session_store is None:
            return

        record = self.session_store.load_session(session_id)
        if record is None:
            wx.CallAfter(ui.message, "Sessão não encontrada (pode ter sido excluída).")
            return

        self.current_session_id = session_id
        self.conversation_history = ConversationHistory.from_dict({
            "task_text": record.task_text,
            "log_entries": record.log_entries,
            "last_model_content": record.last_model_content,
            "conversation_log": record.conversation_log,
        })
        self._speech_history.clear()
        ActionProcessor._last_actions = []  # mesmo reset usado em _on_new_question

        wx.CallAfter(self._open_chat_dialog_for_resumed_session, record)

    def _open_chat_dialog_for_resumed_session(self, record):
        """Roda na GUI thread: reabre o chat com o histórico da sessão retomada."""
        if self.chat_dialog:
            try:
                self.chat_dialog.Destroy()
            except Exception:
                pass
            self.chat_dialog = None

        gui.mainFrame.prePopup()
        self.chat_dialog = OperatorChatDialog(
            gui.mainFrame,
            addon_config.ADDON_FULL_NAME + " - Chat",
            None,
            self._on_new_question
        )
        for entry in record.conversation_log:
            self.chat_dialog.append_message(entry["sender"], entry["message"])
        self.chat_dialog.Show()
        gui.mainFrame.postPopup()
        tones.beep(1000, 100)
        ui.message(f"Sessão '{record.title}' retomada. O que você quer fazer agora?")

    # --- Agendamento de tarefas (scheduler.py / scheduler_store.py / scheduler_dialog.py) ---

    def _check_missed_tasks_on_startup(self):
        """
        Chamado 1x via wx.CallAfter em __init__ — tarefas que venceram
        enquanto o NVDA estava fechado (mais do que um ciclo normal de
        verificação, pra não confundir com algo que o próprio polling ao
        vivo pegaria em segundos) nunca rodam nem são descartadas sozinhas:
        marca como 'missed' e abre a revisão pro usuário decidir.
        """
        if self.scheduler_store is None:
            return
        try:
            grace = config.conf[addon_config.CONFIG_SECTION]["scheduler_check_interval"]
            missed = self.scheduler_store.get_missed_tasks(time.time(), grace_seconds=grace)
            for task in missed:
                self.scheduler_store.mark_missed(task.id)
            if not missed:
                return

            gui.mainFrame.prePopup()
            dlg = SchedulerDialog(
                gui.mainFrame, self.scheduler_store, on_run_now=self._run_missed_task_now, missed_mode=True
            )
            dlg.Show()
            gui.mainFrame.postPopup()
            tones.beep(1000, 100)
            ui.message(f"{len(missed)} tarefa(s) agendada(s) venceram enquanto o NVDA estava fechado.")
        except Exception:
            logger.error("Falha ao checar tarefas agendadas perdidas na inicialização", exc_info=True)

    def _run_missed_task_now(self, task_id):
        """Callback do SchedulerDialog (missed_mode) — chamado numa thread separada pelo diálogo."""
        task = self.scheduler_store.load_task(task_id)
        if task is None:
            return
        if not self._try_start_automation():
            wx.CallAfter(ui.message, f"{addon_config.ADDON_NAME} já está em execução")
            return
        self._begin_new_automation(task.command, steal_focus=True, scheduled_task_id=task.id)

    def _on_scheduled_task_due(self, task):
        """
        Callback do TaskScheduler (scheduler.py), chamado na thread de
        fundo do polling — o fire-time gate completo: nenhuma automação
        agendada roda sem passar por aqui. Nunca bloqueia pra sempre em
        nenhum dos passos.
        """
        if globalVars.appArgs.secure:
            return

        if is_screen_curtain_active():
            logger.debug(f"Cortina de tela ativa — tarefa agendada {task.id} adiada pro próximo ciclo.")
            return

        idle = seconds_since_last_input()
        if idle is not None and idle < addon_config.SCHEDULER_RECENT_INPUT_THRESHOLD:
            logger.debug(f"Entrada recente do usuário — tarefa agendada {task.id} adiada pro próximo ciclo.")
            return

        if not self._try_start_automation():
            logger.debug(f"Automação já em andamento — tarefa agendada {task.id} adiada pro próximo ciclo.")
            return

        started = False
        try:
            if self._announce_and_wait_grace(task):
                self.scheduler_store.cancel_task(task.id)
                wx.CallAfter(ui.message, f"Tarefa agendada '{task.title}' cancelada.")
                return
            self._start_scheduled_task_run(task)
            started = True
        except Exception:
            logger.error(f"Falha ao disparar tarefa agendada {task.id}", exc_info=True)
        finally:
            if not started:
                self._finish_automation()

    def _announce_and_wait_grace(self, task):
        """
        Avisa (beep+fala) e espera a janela de graça cancelável — chamado
        na thread de fundo do scheduler, bloqueia até a janela se resolver
        (botão/Esc/expirar). Returns True se o usuário cancelou.
        """
        grace_seconds = config.conf[addon_config.CONFIG_SECTION]["scheduler_confirm_grace_seconds"]
        event = threading.Event()
        result = {"cancelled": False}
        dialog_holder = {}

        def on_resolved(cancelled):
            result["cancelled"] = cancelled
            event.set()

        def show():
            gui.mainFrame.prePopup()
            dlg = GraceCountdownDialog(gui.mainFrame, task.title, grace_seconds, on_resolved)
            dialog_holder["dlg"] = dlg
            dlg.Show()
            gui.mainFrame.postPopup()

        tones.beep(600, 200)
        ui.message(
            f"Tarefa agendada '{task.title}' vai começar em {grace_seconds} segundos. "
            "Pressione Cancelar ou Esc na janela que abriu pra não executar agora."
        )
        wx.CallAfter(show)
        if not event.wait(timeout=grace_seconds + 5):
            # Nunca trava pra sempre — se a janela não se resolveu nem com a
            # folga extra (GUI thread presa?), segue como "não cancelado".
            logger.warning(f"Janela de graça da tarefa agendada {task.id} não se resolveu a tempo — seguindo em frente.")
        return result["cancelled"]

    def _start_scheduled_task_run(self, task):
        self._begin_new_automation(task.command, steal_focus=False, scheduled_task_id=task.id)

    def _finalize_scheduled_task(self, scheduled_task_id, success, summary):
        """Marca a linha em scheduler_store como concluída/falha — chamado em todo desfecho de _run_ai_loop quando veio de uma tarefa agendada."""
        if scheduled_task_id is None or self.scheduler_store is None:
            return
        try:
            self.scheduler_store.mark_done(scheduled_task_id, success, summary, self.current_session_id)
        except Exception:
            logger.error(f"Falha ao marcar tarefa agendada {scheduled_task_id} como concluída", exc_info=True)

    def _build_support_handlers(self):
        """
        Handlers injetados em ActionProcessor.process_function_calls pras
        tools kind="support" (ver action_tools.TOOL_NAME_TO_ACTION_TYPE) —
        mesma injeção de dependência que execute_fn/capture_fn já usam,
        pra manter o planejador puro/testável sem SQLite real.
        """
        if self.scheduler_store is None:
            return {}
        return {
            "schedule_task_tool": self._handle_schedule_task_tool,
            "list_scheduled_tasks_tool": self._handle_list_scheduled_tasks_tool,
            "cancel_scheduled_task_tool": self._handle_cancel_scheduled_task_tool,
        }

    def _handle_schedule_task_tool(self, args):
        # run_at já foi validado (parseável e futuro) em action_tools.
        # validate_args antes de chegar aqui — mas datas são entrada
        # externa, então o parse é refeito com guarda própria mesmo assim.
        try:
            run_at_epoch = datetime.fromisoformat(args.get("run_at")).timestamp()
        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"run_at inválido: {e}"}

        task_id = self.scheduler_store.create_task(
            command=args.get("command", ""),
            title=args.get("title", ""),
            run_at=run_at_epoch,
            created_by="ai",
        )
        return {"success": True, "message": f"Tarefa agendada (id={task_id}).", "task_id": task_id}

    def _handle_list_scheduled_tasks_tool(self, args):
        tasks = self.scheduler_store.list_tasks(status="pending")[:20]
        items = [
            {
                "task_id": t.id,
                "title": t.title,
                "run_at": datetime.fromtimestamp(t.run_at).isoformat(timespec="seconds"),
            }
            for t in tasks
        ]
        return {"success": True, "tasks": items}

    def _handle_cancel_scheduled_task_tool(self, args):
        task_id = args.get("task_id")
        if self.scheduler_store.cancel_task(task_id):
            return {"success": True, "message": f"Tarefa {task_id} cancelada."}
        return {"success": False, "error": f"Tarefa {task_id} não encontrada ou já não está pendente."}

    def _prompt_user_sync(self, question, timeout_seconds):
        """
        ask_user_tool: pausa a thread de automação até o usuário responder
        num wx.TextEntryDialog, ou até timeout_seconds expirar. Nunca
        trava pra sempre — no timeout, fecha o diálogo remotamente.
        Returns: str (resposta) | None (timeout ou usuário cancelou).
        """
        tones.beep(700, 150)
        wx.CallAfter(ui.message, f"A IA está perguntando: {question}")

        event = threading.Event()
        result = {"answer": None}
        dialog_holder = {}

        def ask():
            gui.mainFrame.prePopup()
            dlg = wx.TextEntryDialog(gui.mainFrame, question, addon_config.ADDON_FULL_NAME)
            dialog_holder["dlg"] = dlg
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    result["answer"] = dlg.GetValue().strip()
            finally:
                dlg.Destroy()
                gui.mainFrame.postPopup()
                dialog_holder.pop("dlg", None)
                event.set()

        wx.CallAfter(ask)
        if not event.wait(timeout=timeout_seconds):
            wx.CallAfter(lambda: dialog_holder["dlg"].EndModal(wx.ID_CANCEL) if dialog_holder.get("dlg") else None)
        return result["answer"]

    def _confirm_shell_command(self, command, reason):
        """
        shell_tool: confirmação bloqueante obrigatória, SEMPRE — nunca
        executa sem isso (ver ActionExecutor.execute_action, que recusa
        shell_command se chegar até ele sem passar por aqui primeiro).
        Foco padrão em "Não" (fail-closed): timeout ou recusa nunca executa.
        Returns: bool — True só se o usuário confirmou explicitamente.
        """
        event = threading.Event()
        result = {"confirmed": False}
        dialog_holder = {}

        def ask():
            gui.mainFrame.prePopup()
            dlg = wx.MessageDialog(
                gui.mainFrame,
                f"A IA quer executar o seguinte comando no terminal:\n\n{command}\n\n"
                f"Motivo: {reason}\n\nPermitir?",
                addon_config.ADDON_FULL_NAME,
                wx.YES_NO | wx.ICON_WARNING | wx.NO_DEFAULT,
            )
            dialog_holder["dlg"] = dlg
            try:
                result["confirmed"] = dlg.ShowModal() == wx.ID_YES
            finally:
                dlg.Destroy()
                gui.mainFrame.postPopup()
                dialog_holder.pop("dlg", None)
                event.set()

        tones.beep(500, 150)
        wx.CallAfter(ui.message, f"A IA quer executar um comando no terminal: {reason}. Confirme na janela que abriu.")
        wx.CallAfter(ask)
        if not event.wait(timeout=addon_config.SHELL_CONFIRM_WAIT_SECONDS):
            wx.CallAfter(lambda: dialog_holder["dlg"].EndModal(wx.ID_NO) if dialog_holder.get("dlg") else None)
        return result["confirmed"]

    def _notify_slow_api(self):
        """
        Chamado (via wx.CallAfter, nunca na thread de automação) no máximo
        uma vez por chamada à API, quando ela já passou do api_timeout
        configurado e o SDK certamente está numa tentativa de retry (ver
        GeminiClient.generate_content/on_slow_response) — nunca mais
        silêncio total até o erro final numa rede lenta.
        """
        tones.beep(400, 150)
        ui.message("A IA está demorando a responder, aguarde...")

    def _wait_for_stable_screen(self, max_wait):
        """
        Espera a tela estabilizar (screen_wait.wait_until_stable) em vez de
        dormir um tempo fixo — nunca bloqueia além de `max_wait`; some
        apps/animações nunca "estabilizam" de verdade (vídeo, cursor
        piscando), então ao expirar segue em frente com a última imagem
        capturada, mas NUNCA em silêncio: avisa com um beep distinto do
        resto do addon + log de warning.

        Returns:
            tuple: (img_base64, width, height, mime_type) — width/height/
            mime_type são None se nenhuma captura chegou a funcionar.
        """
        capture_state = {}

        def capture_fn():
            img, w, h, mime = capture_fullscreen()
            if img is not None:
                capture_state["w"] = w
                capture_state["h"] = h
                capture_state["mime"] = mime
            return img

        stability = wait_until_stable(capture_fn, max_wait=max_wait)
        if not stability.stabilized:
            tones.beep(500, 60)
            logger.warning(f"Tela não estabilizou em até {max_wait}s — seguindo com a última captura.")

        return (
            stability.img_base64,
            capture_state.get("w"),
            capture_state.get("h"),
            capture_state.get("mime"),
        )

    def _execute_batch(self, items, pre_batch_hash):
        """
        Wrapper fino em volta de ActionProcessor.run_batch (a lógica de
        sequenciar/abortar/circuit-breaker é lá, como função pura testável
        fora do NVDA — ver tests/test_execute_batch.py). Aqui só conectamos
        os delays/beeps reais e as chamadas de ActionExecutor/capture_fullscreen.
        """
        def execute_fn(action_info):
            if action_info.action_type == "shell_command":
                # Confirmação bloqueante ANTES de qualquer execução — nunca
                # delega pra ActionExecutor.execute_action pra esse tipo
                # (ver o guard explícito lá e GlobalPlugin._confirm_shell_command).
                if not self._confirm_shell_command(action_info.command, action_info.reason):
                    return False, "Usuário recusou ou não respondeu à confirmação do comando."
                timeout = config.conf[addon_config.CONFIG_SECTION]["shell_command_timeout_seconds"]
                result = ActionExecutor.execute_shell_command(action_info.command, timeout)
                tones.beep(1000, 100)
                return result

            time.sleep(addon_config.ACTION_DELAY)
            result = ActionExecutor.execute_action(action_info, focus_event=self._focus_gained_event)
            tones.beep(1000, 100)
            return result

        def capture_fn():
            img, _, _, _ = self._wait_for_stable_screen(addon_config.STABILITY_MAX_WAIT_ACTION)
            return img

        loop_broken = ActionProcessor.run_batch(items, pre_batch_hash, execute_fn, capture_fn)
        if loop_broken:
            logger.warning("Circuit breaker de loop disparou dentro do lote — parando a automação.")
        return loop_broken

    def _run_ai_loop(self, command, steal_focus=True, scheduled_task_id=None):
        """
        Loop unificado de automação — usado na chamada inicial, em
        perguntas de continuação, E em tarefas agendadas (ver
        _begin_new_automation). steal_focus=False e scheduled_task_id
        preenchido só no caminho do scheduler (ver scheduler.py/
        _on_scheduled_task_due): suprime o roubo de foco visual ao concluir
        (_show_result_in_chat) e marca a linha correspondente em
        scheduler_store como concluída/falha ao final.
        """
        max_turns = config.conf[addon_config.CONFIG_SECTION]["max_turns"]
        turn = 0
        # Default "sem sucesso" — só vira True quando a IA chama
        # done_tool(success=true) e de fato vê a tarefa concluída; todo
        # outro desfecho (circuit breaker, max_turns, erro de API/captura,
        # exceção) é, por definição, um encerramento sem sucesso.
        task_succeeded = False
        try:
            if is_screen_curtain_active():
                wx.CallAfter(
                    ui.message,
                    f"A cortina de tela do NVDA está ativa, então não é possível ver a tela. "
                    f"Desative a cortina de tela (categoria Visão, nas configurações do NVDA) "
                    f"antes de usar o {addon_config.ADDON_NAME}."
                )
                self._finalize_scheduled_task(scheduled_task_id, False, "Cortina de tela ativa.")
                return

            api_key = config.conf[addon_config.CONFIG_SECTION]["gemini_api_key"]
            if not api_key:
                wx.CallAfter(
                    ui.message,
                    f"Configure a chave da API Gemini no painel de configurações do NVDA antes de usar o {addon_config.ADDON_NAME}."
                )
                self._finalize_scheduled_task(scheduled_task_id, False, "Chave de API Gemini não configurada.")
                return

            try:
                # Recriado a cada tarefa: pega chave/modelo atuais do painel
                # de configurações sem precisar reiniciar o NVDA.
                self.gemini_client = GeminiClient()
            except Exception as e:
                logger.error(f"Erro ao criar cliente Gemini: {e}", exc_info=True)
                wx.CallAfter(ui.message, f"Erro ao conectar à API Gemini: {e}")
                self._finalize_scheduled_task(scheduled_task_id, False, f"Erro ao conectar à API Gemini: {e}")
                return

            self._current_goal = command
            app_name = get_focused_app_name()
            # Âncora da tarefa + log textual janelado — substitui o
            # Content(role=user) de turno 0; persiste entre perguntas de
            # continuação (_on_new_question não chama clear()).
            self.conversation_history.set_task_text(f"Tarefa: {command}")
            # Transcrição humana (separada do log técnico que vai pra IA) —
            # 1x por chamada de _run_ai_loop, cobre tanto o comando inicial
            # quanto toda pergunta de continuação. Autosave imediato aqui
            # pra não perder esse "Você" se algo falhar já no primeiro turno.
            self.conversation_history.record_conversation_message("Você", command)
            self._autosave_session()
            # Itens do lote do turno anterior, já com function_response
            # resolvido (ver _execute_batch) — vão no próximo turno.
            pending_items = None
            last_display_text = ""

            logger.info(f"Iniciando loop de automação (max_turns={max_turns}) | App em foco: {app_name}")

            self._system_instruction = build_system_instruction(
                app_name,
                config.conf[addon_config.CONFIG_SECTION]["response_language"],
                addon_config.AI_OPERATOR_PROMPT_TEMPLATE,
                now_local=datetime.now().strftime(addon_config.NOW_LOCAL_DISPLAY_FORMAT),
            )

            for turn in range(max_turns):
                logger.debug(f"Turno {turn + 1}/{max_turns}")

                if turn == 0:
                    # Primeira foto da tarefa — não há ação anterior pra
                    # "assentar", só um pequeno delay fixo (dar tempo do
                    # NVDA já ter falado "Processando..." antes do beep).
                    time.sleep(addon_config.SHORT_DELAY)
                    img_base64, screen_w, screen_h, mime_type = capture_fullscreen()
                else:
                    # Espera adaptativa no lugar do antigo sleep(turn_delay)
                    # fixo: repropõe o setting já existente de "durma N
                    # segundos" para "espere até N segundos pela tela
                    # estabilizar" — rápido em telas rápidas, tolerante em
                    # apps que demoram a abrir (até o teto configurado).
                    turn_delay = config.conf[addon_config.CONFIG_SECTION]["turn_delay"]
                    img_base64, screen_w, screen_h, mime_type = self._wait_for_stable_screen(turn_delay)

                if not img_base64:
                    logger.error("Falha ao capturar a tela — abortando loop")
                    wx.CallAfter(ui.message, "Erro ao capturar tela")
                    break

                if pending_items is not None:
                    current_user_parts = build_next_turn_parts(
                        pending_items,
                        self._get_speech_context(),
                        ActionProcessor._last_actions,
                        f"[Objetivo: {command}] Ação(ões) executada(s). Verificando resultado e continuando.",
                    )
                else:
                    current_user_parts = []
                    speech_ctx = self._get_speech_context()
                    if speech_ctx:
                        current_user_parts.append(types.Part(text=speech_ctx))

                current_user_parts.append(types.Part.from_bytes(data=base64.b64decode(img_base64), mime_type=mime_type))

                tones.beep(800, 50)

                # Só a screenshot ATUAL viaja na requisição — turnos
                # anteriores entram apenas como o log textual janelado de
                # ConversationHistory (ver utils.py), nunca como Content
                # crus acumulados: custo O(1) por turno, não O(n).
                messages = self.conversation_history.build_messages_for_api(current_user_parts)
                turn_result = self.gemini_client.generate_content(
                    messages,
                    system_instruction=self._system_instruction,
                    on_slow_response=lambda: wx.CallAfter(self._notify_slow_api),
                )

                if turn_result.error and not turn_result.function_calls:
                    last_display_text = turn_result.error
                    logger.error(f"Erro da API Gemini no turno {turn + 1}: {last_display_text}")
                    wx.CallAfter(ui.message, last_display_text)
                    break

                plan = ActionProcessor.process_function_calls(
                    turn_result.function_calls, screen_w, screen_h,
                    support_handlers=self._build_support_handlers(),
                )
                display_text = turn_result.text or plan.explanation
                last_display_text = display_text
                self.last_screen_info = {"width": screen_w, "height": screen_h}

                logger.debug(
                    f"Turno {turn + 1}: {len(plan.items)} item(ns) no lote, "
                    f"finished={plan.is_finished} explicação={display_text[:150]!r}"
                )

                if plan.awaiting_question:
                    # ask_user_tool: pausa síncrona (nunca passa por
                    # _execute_batch — não é uma ação física) até o usuário
                    # responder ou o timeout expirar. Ver _prompt_user_sync.
                    ask_timeout = config.conf[addon_config.CONFIG_SECTION]["ask_user_timeout_seconds"]
                    answer = self._prompt_user_sync(plan.awaiting_question, ask_timeout)
                    if answer is None:
                        self.conversation_history.set_last_model_content(None)
                        last_display_text = "Parei a automação: sem resposta do usuário à pergunta."
                        wx.CallAfter(ui.message, last_display_text)
                        self._autosave_session()
                        break

                    plan.items[0].response = {"answer": answer}
                    self.conversation_history.record_step(plan.items, f"Pergunta: {plan.awaiting_question}")
                    pending_items = plan.items
                    if turn_result.model_content is not None:
                        self.conversation_history.set_last_model_content(turn_result.model_content)
                    self._autosave_session()
                    continue

                wx.CallAfter(ui.message, clean_markdown(display_text))

                loop_broken = self._execute_batch(plan.items, hashlib.md5(img_base64.encode()).hexdigest())
                self.conversation_history.record_step(plan.items, display_text)

                if loop_broken:
                    # Descarta a function_call deste turno sem resposta —
                    # nunca deixar uma pendente no histórico (ver risco
                    # mapeado no plano: function_call órfã quebraria uma
                    # pergunta de continuação depois do circuit breaker).
                    self.conversation_history.set_last_model_content(None)
                    last_display_text = "Parei a automação: a mesma ação se repetiu sem a tela mudar."
                    wx.CallAfter(ui.message, last_display_text)
                    self._autosave_session()
                    break

                pending_items = plan.items

                if plan.is_finished:
                    # done_tool não executa ação física e não haverá
                    # próximo turno nesta chamada — mesmo motivo do
                    # loop_broken acima: não deixa a function_call pendente.
                    task_succeeded = bool(plan.success)
                    if not task_succeeded:
                        last_display_text = (
                            f"Não concluí: {last_display_text}" if last_display_text
                            else "Não consegui concluir a tarefa."
                        )
                    self.conversation_history.set_last_model_content(None)
                    self._autosave_session()
                    break

                if turn_result.model_content is not None:
                    self.conversation_history.set_last_model_content(turn_result.model_content)

                self._autosave_session()
                # Sem sleep fixo aqui de propósito — dar tempo da tela
                # refletir a última ação é papel da espera adaptativa no
                # topo do próximo turno (self._wait_for_stable_screen).
            else:
                logger.warning(f"Loop encerrado por atingir max_turns ({max_turns}) sem concluir")

            logger.info(f"Loop de automação encerrado após {turn + 1} turno(s)")
            if not task_succeeded:
                # Tom distinto dos demais beeps do addon (1000/100, 800/50,
                # 500/60, 400/150) — sinal sonoro de "não funcionou",
                # essencial pra um usuário cego sem precisar ouvir o texto
                # inteiro pra saber se a automação deu certo.
                tones.beep(300, 250)
            final_text = last_display_text or "Operação concluída."
            self._finalize_scheduled_task(scheduled_task_id, task_succeeded, final_text)
            wx.CallAfter(self._show_result_in_chat, command, final_text, steal_focus)

        except Exception as e:
            logger.error(f"Exceção não tratada no loop de automação: {e}", exc_info=True)
            tones.beep(300, 250)
            wx.CallAfter(ui.message, f"Erro: {str(e)}")
            self._finalize_scheduled_task(scheduled_task_id, False, f"Erro: {str(e)}")
            wx.CallAfter(self._show_result_in_chat, command, f"Erro: {str(e)}", steal_focus)
        finally:
            self._finish_automation()

    def _show_result_in_chat(self, command, response_text, steal_focus=True):
        """
        Exibe resposta final no chat. Cria o dialog se necessário, ou
        reexibe se estava oculto. steal_focus=False (tarefas agendadas,
        ver _run_ai_loop) pula Show()/Raise()/SetFocus() e o beep de "novo
        chat" — o dialog é criado/atualizado oculto, pronto pra ser aberto
        depois (NVDA+Shift+O ou NVDA+Shift+R). A fala/beep de RESULTADO da
        tarefa em si já aconteceu turno a turno dentro de _run_ai_loop —
        isso aqui nunca é a única notificação sonora do desfecho.
        """
        dialog_existia = False

        if self.chat_dialog:
            try:
                dialog_existia = True
                if steal_focus and not self.chat_dialog.IsShown():
                    self.chat_dialog.Show()
            except Exception:
                self.chat_dialog = None
                dialog_existia = False

        if not self.chat_dialog:
            if steal_focus:
                gui.mainFrame.prePopup()
            self.chat_dialog = OperatorChatDialog(
                gui.mainFrame,
                addon_config.ADDON_FULL_NAME + " - Chat",
                None,
                self._on_new_question
            )
            if steal_focus:
                self.chat_dialog.Show()
                gui.mainFrame.postPopup()
                tones.beep(1000, 100)

        # Só adiciona "Você" se o dialog foi criado agora.
        # Se já existia, _on_send já adicionou a mensagem do usuário.
        if not dialog_existia:
            self.chat_dialog.append_message("Você", command)

        self.chat_dialog.append_message(addon_config.ADDON_NAME, response_text)
        # Transcrição humana — não grava "Você" aqui de novo (já foi feito
        # em _run_ai_loop, 1x por chamada); só a resposta final da IA,
        # cobrindo sucesso, erro de API, circuit breaker e exceção.
        self.conversation_history.record_conversation_message(addon_config.ADDON_NAME, response_text)
        self._autosave_session()
        if steal_focus:
            self.chat_dialog.Raise()
            self.chat_dialog.SetFocus()

    def _on_new_question(self, question):
        """Callback do chat dialog — esconde o dialog durante o processamento e reexibe ao terminar."""
        try:
            logger.info(f"Pergunta de continuação recebida: {question!r}")
            if not self._try_start_automation():
                wx.CallAfter(ui.message, f"{addon_config.ADDON_NAME} já está em execução")
                return
            ActionProcessor._last_actions = []

            if self.chat_dialog:
                wx.CallAfter(self.chat_dialog.Hide)

            wx.CallAfter(ui.message, "Processando...")
            time.sleep(addon_config.SHORT_DELAY)
            self._run_ai_loop(question)
        except Exception as e:
            logger.error(f"Erro ao processar pergunta de continuação: {e}", exc_info=True)
            wx.CallAfter(ui.message, f"Erro: {str(e)}")
            wx.CallAfter(self._show_result_in_chat, question, f"Erro: {str(e)}")

    # Atalhos padrão — o usuário pode reatribuí-los livremente em
    # Preferências > Gestos de entrada (categoria acima, em scriptCategory),
    # sem precisar editar código. Chaves aqui são só o valor INICIAL.
    __gestures = {
        "kb:NVDA+shift+r": "iniciarOperadorIA",
        "kb:NVDA+shift+o": "gerenciarSessoes",
        "kb:NVDA+shift+a": "gerenciarAgendamentos",
    }
