# -*- coding: utf-8 -*-
"""
Diálogo de chat do ElizIA
Permite interação contínua com o usuário
"""

import wx
import re
import threading
import ui
from . import addon_config


class OperatorChatDialog(wx.Dialog):
    """Diálogo interativo para chat com o ElizIA"""
    
    def __init__(self, parent, title, initial_message, callback):
        """
        Args:
            parent: Janela pai
            title: Título do diálogo
            initial_message: Mensagem inicial a exibir
            callback: Função a chamar quando usuário faz nova pergunta
                      callback(new_question) -> None
        """
        super(OperatorChatDialog, self).__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX
        )
        
        self.callback = callback
        self._create_ui()
        self._setup_bindings()
        
        if initial_message:
            self.append_message(addon_config.ADDON_NAME, initial_message)

        self.SetSize((addon_config.DIALOG_WIDTH, addon_config.DIALOG_HEIGHT))
        self.CenterOnScreen()
    
    def _create_ui(self):
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)

        history_label = wx.StaticText(panel, label="Histórico da Conversa:")
        sizer.Add(history_label, 0, wx.ALL, 5)

        self.history_list = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_HRULES | wx.LC_VRULES,
            size=(-1, addon_config.HISTORY_LIST_HEIGHT)
        )
        self.history_list.InsertColumn(0, "Quem", width=addon_config.COLUMN_SENDER_WIDTH)
        self.history_list.InsertColumn(1, "Mensagem", width=addon_config.COLUMN_MESSAGE_WIDTH)
        self.history_list.SetFont(wx.Font(addon_config.HISTORY_LIST_FONT_SIZE, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.history_list, 1, wx.EXPAND | wx.ALL, 5)

        input_label = wx.StaticText(panel, label="Sua pergunta ou comando:")
        sizer.Add(input_label, 0, wx.ALL, 5)

        self.input_field = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        sizer.Add(self.input_field, 0, wx.EXPAND | wx.ALL, 5)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.send_button = wx.Button(panel, label="&Enviar")
        self.send_button.SetDefault()
        button_sizer.Add(self.send_button, 0, wx.ALL, 5)

        self.copy_button = wx.Button(panel, label="&Copiar histórico")
        button_sizer.Add(self.copy_button, 0, wx.ALL, 5)

        self.close_button = wx.Button(panel, wx.ID_CLOSE, label="&Fechar")
        button_sizer.Add(self.close_button, 0, wx.ALL, 5)

        sizer.Add(button_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)

        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(main_sizer)
    
    def _setup_bindings(self):
        self.send_button.Bind(wx.EVT_BUTTON, self._on_send)
        self.input_field.Bind(wx.EVT_TEXT_ENTER, self._on_send)
        self.copy_button.Bind(wx.EVT_BUTTON, self._on_copy)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_close)
        self.Bind(wx.EVT_CLOSE, self._on_close)
    
    def _on_send(self, event):
        """Chamado quando usuário envia uma pergunta"""
        question = self.input_field.GetValue().strip()
        
        if not question:
            wx.MessageBox(
                "Por favor, digite uma pergunta ou comando.",
                "Campo vazio",
                wx.OK | wx.ICON_WARNING
            )
            return
        
        self.append_message("Você", question)
        self.input_field.Clear()

        self.send_button.Enable(False)
        self.input_field.Enable(False)

        if self.callback:
            threading.Thread(
                target=self._call_callback,
                args=(question,),
                daemon=True
            ).start()
    
    def _call_callback(self, question):
        try:
            self.callback(question)
        except ConnectionError as e:
            wx.CallAfter(self.append_message, "Erro", f"Erro de conexão: {str(e)}")
        except TimeoutError as e:
            wx.CallAfter(self.append_message, "Erro", f"Timeout: {str(e)}")
        except Exception as e:
            wx.CallAfter(self.append_message, "Erro", f"Erro inesperado: {type(e).__name__}")
        finally:
            wx.CallAfter(self._enable_input)
    
    def _on_copy(self, event):
        """Copia o histórico para a área de transferência"""
        text_lines = []

        for i in range(self.history_list.GetItemCount()):
            sender = self.history_list.GetItemText(i, 0)
            message = self.history_list.GetItemText(i, 1)
            text_lines.append(f"{sender}: {message}")

        if text_lines:
            full_text = "\n".join(text_lines)
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(full_text))
                wx.TheClipboard.Close()
                ui.message("Histórico copiado")
        else:
            ui.message("Não há histórico para copiar")
    
    def _on_close(self, event):
        self.Destroy()
    
    def append_message(self, sender, message):
        """
        Adiciona mensagem ao histórico.

        Args:
            sender: Nome do remetente (Você, {addon_config.ADDON_NAME}, Sistema)
            message: Texto da mensagem
        """
        def _append():
            clean_msg = self._clean_markdown(message)

            index = self.history_list.InsertItem(self.history_list.GetItemCount(), sender)
            self.history_list.SetItem(index, 1, clean_msg)

            self.history_list.EnsureVisible(index)

        wx.CallAfter(_append)
    
    def _clean_markdown(self, text):
        """Remove formatação markdown básica"""
        if not text:
            return ""
        
        text = re.sub(r'\*\*|__|[*_]', '', text)
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        return text.strip()
    
    def _enable_input(self):
        self.send_button.Enable(True)
        self.input_field.Enable(True)
        self.input_field.SetFocus()

