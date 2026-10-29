# -*- coding: utf-8 -*-
"""
Diálogos de agendamento: lista de gerenciamento normal (SchedulerDialog,
NVDA+Shift+A) e, com missed_mode=True, a revisão de tarefas perdidas
mostrada 1x na inicialização quando o NVDA esteve fechado no horário de
alguma — mesma estrutura (wx.ListCtrl + botões), só troca as ações
disponíveis por linha, pra não duplicar o esqueleto inteiro. Segue os
mesmos padrões de acessibilidade de sessions_dialog.py.

GraceCountdownDialog é a janela de aviso + graça cancelável mostrada pelo
fire-time gate (ver GlobalPlugin._announce_and_wait_grace em __init__.py)
antes de uma tarefa agendada rodar de verdade.
"""

import threading
import time

import wx
import ui
import addonHandler
from . import addon_config
from . import scheduler_store

addonHandler.initTranslation()

# Rótulos de exibição pra coluna "Status" da lista — os valores em si
# (scheduler_store.STATUS_*) ficam em inglês de propósito, são dado
# persistido no SQLite, não texto de UI; este dict é só o mapeamento na
# hora de mostrar, com fallback pro valor cru se um status não estiver aqui.
_STATUS_LABELS = {
    scheduler_store.STATUS_PENDING: _("Pending"),
    scheduler_store.STATUS_DONE: _("Done"),
    scheduler_store.STATUS_CANCELLED: _("Cancelled"),
    scheduler_store.STATUS_MISSED: _("Missed"),
    scheduler_store.STATUS_FAILED: _("Failed"),
}


class SchedulerDialog(wx.Dialog):
    """
    Args:
        parent: janela pai
        scheduler_store: instância de scheduler_store.SchedulerStore
        on_run_now: callable(task_id) -> None, chamado numa thread separada
            (só usado em missed_mode — dispara a automação de verdade)
        missed_mode: False (padrão) = gerenciamento normal, lista tarefas
            pendentes com Cancelar/Excluir. True = revisão de tarefas
            perdidas (vencidas enquanto o NVDA estava fechado), lista só
            status='missed' com Rodar agora/Descartar por linha.
    """

    def __init__(self, parent, scheduler_store, on_run_now=None, missed_mode=False):
        title_suffix = _("Missed scheduled tasks") if missed_mode else _("Scheduled tasks")
        super(SchedulerDialog, self).__init__(
            parent,
            title=f"{addon_config.ADDON_FULL_NAME} - {title_suffix}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )

        self.scheduler_store = scheduler_store
        self.on_run_now = on_run_now
        self.missed_mode = missed_mode

        self._create_ui()
        self._setup_bindings()
        self._reload_list()

        self.SetSize((addon_config.SCHEDULER_DIALOG_WIDTH, addon_config.SCHEDULER_DIALOG_HEIGHT))
        self.CenterOnScreen()

    def _create_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        intro = (
            _(
                "The tasks below were scheduled while NVDA was closed — "
                "choose to run each one now or discard it:"
            )
            if self.missed_mode else
            _("Scheduled tasks:")
        )
        list_label = wx.StaticText(panel, label=intro)
        sizer.Add(list_label, 0, wx.ALL, 5)

        self.tasks_list = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
        )
        self.tasks_list.InsertColumn(0, _("Title"), width=addon_config.SCHEDULER_COLUMN_TITLE_WIDTH)
        # Translators: column header — the date/time the task is scheduled to run.
        self.tasks_list.InsertColumn(1, _("Time"), width=addon_config.SCHEDULER_COLUMN_RUN_AT_WIDTH)
        # Translators: column header — the task's current state (pending, done, etc).
        self.tasks_list.InsertColumn(2, _("Status"), width=addon_config.SCHEDULER_COLUMN_STATUS_WIDTH)
        sizer.Add(self.tasks_list, 1, wx.EXPAND | wx.ALL, 5)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        if self.missed_mode:
            self.run_now_button = wx.Button(panel, label=_("&Run now"))
            self.run_now_button.SetDefault()
            button_sizer.Add(self.run_now_button, 0, wx.ALL, 5)

            self.discard_button = wx.Button(panel, label=_("&Discard"))
            button_sizer.Add(self.discard_button, 0, wx.ALL, 5)
        else:
            self.cancel_task_button = wx.Button(panel, label=_("Cancel tas&k"))
            self.cancel_task_button.SetDefault()
            button_sizer.Add(self.cancel_task_button, 0, wx.ALL, 5)

            self.delete_button = wx.Button(panel, label=_("&Delete"))
            button_sizer.Add(self.delete_button, 0, wx.ALL, 5)

        self.close_button = wx.Button(panel, wx.ID_CLOSE, label=_("&Close"))
        button_sizer.Add(self.close_button, 0, wx.ALL, 5)

        sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(main_sizer)

    def _setup_bindings(self):
        if self.missed_mode:
            self.run_now_button.Bind(wx.EVT_BUTTON, self._on_run_now)
            self.discard_button.Bind(wx.EVT_BUTTON, self._on_discard)
            self.tasks_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_run_now)
        else:
            self.cancel_task_button.Bind(wx.EVT_BUTTON, self._on_cancel_task)
            self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_close)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _reload_list(self):
        self.tasks_list.DeleteAllItems()
        status_filter = "missed" if self.missed_mode else None
        for task in self.scheduler_store.list_tasks(status=status_filter):
            run_at_text = time.strftime(addon_config.SESSION_DATETIME_DISPLAY_FORMAT, time.localtime(task.run_at))
            index = self.tasks_list.InsertItem(self.tasks_list.GetItemCount(), task.title)
            self.tasks_list.SetItem(index, 1, run_at_text)
            self.tasks_list.SetItem(index, 2, _STATUS_LABELS.get(task.status, task.status))
            self.tasks_list.SetItemData(index, task.id)

    def _get_selected_task_id(self):
        selected = self.tasks_list.GetFirstSelected()
        if selected == -1:
            wx.MessageBox(
                _("Select a task in the list first."), _("No task selected"), wx.OK | wx.ICON_WARNING
            )
            return None
        return self.tasks_list.GetItemData(selected)

    def _on_run_now(self, event):
        task_id = self._get_selected_task_id()
        if task_id is None:
            return
        self.Destroy()
        if self.on_run_now:
            # Mesmo padrão de sessions_dialog._on_resume: dispara numa
            # thread separada, nunca na GUI thread (o callback real inicia
            # a automação, que já tem latência de rede/execução própria).
            threading.Thread(target=self.on_run_now, args=(task_id,), daemon=True).start()

    def _on_discard(self, event):
        task_id = self._get_selected_task_id()
        if task_id is None:
            return
        self.scheduler_store.delete_task(task_id)
        self._reload_list()
        ui.message(_("Scheduled task discarded"))
        self.tasks_list.SetFocus()

    def _on_cancel_task(self, event):
        task_id = self._get_selected_task_id()
        if task_id is None:
            return
        self.scheduler_store.cancel_task(task_id)
        self._reload_list()
        ui.message(_("Scheduled task cancelled"))
        self.tasks_list.SetFocus()

    def _on_delete(self, event):
        task_id = self._get_selected_task_id()
        if task_id is None:
            return
        self.scheduler_store.delete_task(task_id)
        self._reload_list()
        ui.message(_("Scheduled task deleted"))
        self.tasks_list.SetFocus()

    def _on_close(self, event):
        self.Destroy()


class GraceCountdownDialog(wx.Dialog):
    """
    Janela não-modal de aviso + contagem regressiva cancelável, mostrada
    pelo fire-time gate antes de rodar uma tarefa agendada de verdade (ver
    GlobalPlugin._announce_and_wait_grace em __init__.py). Fecha sozinha e
    sinaliza "não cancelado" ao chegar a zero; Cancelar/Esc sinaliza
    "cancelado". Nunca bloqueia além do teto configurado.

    Args:
        on_resolved: callable(cancelled: bool) -> None, chamado exatamente
            uma vez, na GUI thread, quando a janela se resolve (botão, Esc,
            ou contagem chegando a zero).
    """

    def __init__(self, parent, title, seconds, on_resolved):
        super(GraceCountdownDialog, self).__init__(
            parent, title=addon_config.ADDON_FULL_NAME, style=wx.DEFAULT_DIALOG_STYLE
        )
        self._on_resolved = on_resolved
        self._remaining = seconds
        self._resolved = False
        self._title = title

        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.label = wx.StaticText(panel, label=self._format_label())
        sizer.Add(self.label, 0, wx.ALL, 10)

        self.cancel_button = wx.Button(panel, label=_("&Cancel"))
        self.cancel_button.SetDefault()
        sizer.Add(self.cancel_button, 0, wx.ALL | wx.ALIGN_CENTER, 10)

        panel.SetSizer(sizer)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(main_sizer)
        self.Fit()
        self.CenterOnScreen()

        self.cancel_button.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.Bind(wx.EVT_CLOSE, self._on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_tick, self._timer)
        self._timer.Start(1000)

    def _format_label(self):
        return _(
            "Scheduled task '%(title)s' will start in %(seconds)s seconds.\n"
            "Press Cancel (or Esc) to not run it now."
        ) % {"title": self._title, "seconds": self._remaining}

    def _on_tick(self, event):
        self._remaining -= 1
        if self._remaining <= 0:
            self._finish(cancelled=False)
            return
        self.label.SetLabel(self._format_label())

    def _on_cancel(self, event):
        self._finish(cancelled=True)

    def _on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._finish(cancelled=True)
            return
        event.Skip()

    def _finish(self, cancelled):
        if self._resolved:
            return
        self._resolved = True
        self._timer.Stop()
        self.Destroy()
        self._on_resolved(cancelled)
