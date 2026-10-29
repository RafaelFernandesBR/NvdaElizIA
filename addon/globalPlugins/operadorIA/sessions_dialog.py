# -*- coding: utf-8 -*-
"""
Diálogo de gerenciamento de seções salvas (listar/abrir/renomear/excluir).
Segue os mesmos padrões de acessibilidade de chat_dialog.py: rótulo antes
de cada controle, mnemonics "&" nos botões, botão padrão via SetDefault(),
botão de fechar com wx.ID_CLOSE, e foco explícito de volta ao controle
principal depois de qualquer operação.
"""

import threading
import time

import wx
import ui
import addonHandler
from . import addon_config

addonHandler.initTranslation()


class SessionsDialog(wx.Dialog):
    """
    Args:
        parent: janela pai
        session_store: instância de session_store.SessionStore
        on_resume: callable(session_id) -> None, chamado numa thread
            separada (I/O de leitura + reconstrução de ConversationHistory)
        on_new_session: callable() -> None, chamado na GUI thread
    """

    def __init__(self, parent, session_store, on_resume, on_new_session):
        super(SessionsDialog, self).__init__(
            parent,
            title=_("%s - Saved sessions") % addon_config.ADDON_FULL_NAME,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX
        )

        self.session_store = session_store
        self.on_resume = on_resume
        self.on_new_session = on_new_session

        self._create_ui()
        self._setup_bindings()
        self._reload_list()

        self.SetSize((addon_config.SESSIONS_DIALOG_WIDTH, addon_config.SESSIONS_DIALOG_HEIGHT))
        self.CenterOnScreen()

    def _create_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        list_label = wx.StaticText(panel, label=_("Saved sessions:"))
        sizer.Add(list_label, 0, wx.ALL, 5)

        self.sessions_list = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES
        )
        self.sessions_list.InsertColumn(0, _("Title"), width=addon_config.SESSIONS_COLUMN_TITLE_WIDTH)
        self.sessions_list.InsertColumn(1, _("Last activity"), width=addon_config.SESSIONS_COLUMN_ACTIVITY_WIDTH)
        # Translators: column header — how many back-and-forth turns this session has.
        self.sessions_list.InsertColumn(2, _("Turns"), width=addon_config.SESSIONS_COLUMN_TURNS_WIDTH)
        sizer.Add(self.sessions_list, 1, wx.EXPAND | wx.ALL, 5)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.resume_button = wx.Button(panel, label=_("&Open/Resume"))
        self.resume_button.SetDefault()
        button_sizer.Add(self.resume_button, 0, wx.ALL, 5)

        self.new_button = wx.Button(panel, label=_("&New session"))
        button_sizer.Add(self.new_button, 0, wx.ALL, 5)

        self.rename_button = wx.Button(panel, label=_("&Rename"))
        button_sizer.Add(self.rename_button, 0, wx.ALL, 5)

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
        self.resume_button.Bind(wx.EVT_BUTTON, self._on_resume)
        self.new_button.Bind(wx.EVT_BUTTON, self._on_new_session)
        self.rename_button.Bind(wx.EVT_BUTTON, self._on_rename)
        self.delete_button.Bind(wx.EVT_BUTTON, self._on_delete)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_close)
        self.sessions_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_resume)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _reload_list(self):
        """
        Lista/renomeia/exclui são consultas locais de poucas linhas
        (sub-milissegundo) — rodam síncronas na GUI thread de propósito,
        diferente de _on_resume (que envolve reconstruir ConversationHistory
        e por isso vai pra thread separada).
        """
        self.sessions_list.DeleteAllItems()
        for session in self.session_store.list_sessions():
            activity = time.strftime(
                addon_config.SESSION_DATETIME_DISPLAY_FORMAT, time.localtime(session.updated_at)
            )
            index = self.sessions_list.InsertItem(self.sessions_list.GetItemCount(), session.title)
            self.sessions_list.SetItem(index, 1, activity)
            self.sessions_list.SetItem(index, 2, str(session.turn_count))
            self.sessions_list.SetItemData(index, session.id)

    def _get_selected_session_id(self):
        selected = self.sessions_list.GetFirstSelected()
        if selected == -1:
            wx.MessageBox(
                _("Select a session in the list first."),
                _("No session selected"),
                wx.OK | wx.ICON_WARNING
            )
            return None
        return self.sessions_list.GetItemData(selected)

    def _get_selected_session_title(self):
        selected = self.sessions_list.GetFirstSelected()
        if selected == -1:
            return ""
        return self.sessions_list.GetItemText(selected, 0)

    def _on_resume(self, event):
        session_id = self._get_selected_session_id()
        if session_id is None:
            return

        self.Destroy()
        threading.Thread(target=self.on_resume, args=(session_id,), daemon=True).start()

    def _on_new_session(self, event):
        self.Destroy()
        wx.CallAfter(self.on_new_session)

    def _on_rename(self, event):
        session_id = self._get_selected_session_id()
        if session_id is None:
            return

        current_title = self._get_selected_session_title()
        dlg = wx.TextEntryDialog(self, _("New title:"), _("Rename session"), value=current_title)
        if dlg.ShowModal() == wx.ID_OK:
            new_title = dlg.GetValue().strip()
            if new_title:
                self.session_store.rename_session(session_id, new_title)
                self._reload_list()
        dlg.Destroy()
        self.sessions_list.SetFocus()

    def _on_delete(self, event):
        session_id = self._get_selected_session_id()
        if session_id is None:
            return

        title = self._get_selected_session_title()
        confirm = wx.MessageBox(
            _("Delete session '%s'? This action cannot be undone.") % title,
            _("Confirm deletion"),
            wx.YES_NO | wx.ICON_WARNING
        )
        if confirm == wx.YES:
            self.session_store.delete_session(session_id)
            self._reload_list()
            ui.message(_("Session deleted"))
        self.sessions_list.SetFocus()

    def _on_close(self, event):
        self.Destroy()
