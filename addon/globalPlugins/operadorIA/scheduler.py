# -*- coding: utf-8 -*-
"""
Job de fundo que verifica tarefas agendadas (scheduler_store.py) e avisa o
GlobalPlugin quando uma vence. Idiom copiado de AutoUpdateChecker do próprio
NVDA core (source/updateCheck.py): gui.NonReEntrantTimer
manipulado SÓ na main thread (via wx.CallAfter), disparando um
threading.Thread(daemon=True) pro trabalho de I/O real — o timer em si nunca
bloqueia nem toca SQLite diretamente.

Este módulo não decide SE uma tarefa deve disparar agora (cortina de tela,
automação já em andamento, janela de graça cancelável, etc.) — isso é
responsabilidade do callback `on_due_task` injetado pelo GlobalPlugin
(__init__.py), que tem acesso ao resto do estado do addon. Aqui dentro só
existe o polling em si, o que o mantém pequeno e fácil de revisar.
"""

import ctypes
import logging
import threading
import time

import wx
import gui

logger = logging.getLogger(__name__)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def seconds_since_last_input():
    """
    Segundos desde a última tecla/clique físico (GetLastInputInfo do
    Windows) — heurística barata (sem dependência nova, só ctypes/user32,
    já usado em action_executor.py) pra evitar disparar uma tarefa agendada
    bem no meio de uma rajada de teclas do usuário. Mitigação, não garantia:
    não substitui o aviso + janela de graça cancelável do fire-time gate.

    Returns:
        float | None — None se a chamada ao Windows falhar (fail-open: o
        chamador deve tratar None como "não sei, siga em frente").
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        idle_ms = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, idle_ms / 1000.0)
    except Exception:
        logger.debug("Falha ao consultar GetLastInputInfo", exc_info=True)
        return None


class TaskScheduler:
    """
    Args:
        store: scheduler_store.SchedulerStore.
        on_due_task: callable(ScheduledTask), chamado na thread de fundo do
            polling (NUNCA na main thread) pra cada tarefa vencida — decide
            e executa o fire-time gate completo. Exceções são logadas e não
            interrompem o restante do polling.
        check_interval_provider: callable() -> int, segundos entre
            verificações — lido do confspec (scheduler_check_interval) a
            cada tick, pra refletir mudança de configuração sem reiniciar.
    """

    def __init__(self, store, on_due_task, check_interval_provider):
        self.store = store
        self._on_due_task = on_due_task
        self._check_interval_provider = check_interval_provider
        self._timer = None

    def start(self):
        """Inicia o timer. Pode ser chamado de qualquer thread — a criação/start de verdade é adiada pra main thread."""
        wx.CallAfter(self._start_timer_on_main_thread)

    def stop(self):
        """Para e descarta o timer. Pode ser chamado de qualquer thread. Chamar em GlobalPlugin.terminate()."""
        wx.CallAfter(self._stop_timer_on_main_thread)

    def _start_timer_on_main_thread(self):
        if self._timer is not None:
            return
        self._timer = gui.NonReEntrantTimer(self._on_tick)
        self._schedule_next_tick()

    def _stop_timer_on_main_thread(self):
        if self._timer is not None:
            self._timer.Stop()
            self._timer = None

    def _schedule_next_tick(self):
        """
        Timer ONE-SHOT que se reagenda a cada tick (mesmo padrão de
        AutoUpdateChecker.setNextCheck em updateCheck.py) — em vez de um
        timer repetindo com intervalo fixo, pra reler scheduler_check_interval
        do confspec a cada vez e refletir uma mudança de configuração sem
        precisar reiniciar o NVDA.
        """
        if self._timer is not None:
            self._timer.Start(self._check_interval_provider() * 1000, True)

    def _on_tick(self):
        # Nunca faz I/O aqui dentro (é a main thread) — só dispara a thread
        # de verdade, mesmo padrão de AutoUpdateChecker.check(), e reagenda
        # o próximo tick.
        threading.Thread(target=self._check_due_tasks, daemon=True).start()
        self._schedule_next_tick()

    def _check_due_tasks(self):
        try:
            due = self.store.get_due_tasks(time.time())
        except Exception:
            logger.error("Falha ao consultar tarefas agendadas vencidas", exc_info=True)
            return

        for task in due:
            try:
                self._on_due_task(task)
            except Exception:
                logger.error(f"Falha ao processar tarefa agendada {task.id}", exc_info=True)
