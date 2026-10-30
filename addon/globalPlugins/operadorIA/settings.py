# -*- coding: utf-8 -*-
"""
Painel de Configurações do ElizIA Visual
"""

import logging
import threading
import wx
import gui
import ui
import config
import addonHandler
from . import addon_config
from . import gemini_client

addonHandler.initTranslation()

logger = logging.getLogger(__name__)

# Lista de fallback, usada até o usuário clicar em "Atualizar lista de
# modelos" (busca a lista real da API — ver onRefreshModels).
GEMINI_MODELS = [
	"gemini-2.5-flash",
	"gemini-2.5-flash-lite",
	"gemini-2.5-pro",
	"gemini-3-pro-preview",
	"gemini-3.5-flash",
	"gemini-3.1-flash-lite",
]

TEMPERATURE_PRESETS = [
	(_("0.0 (Deterministic)"), 0.0),
	(_("0.4 (Default)"), 0.4),
	(_("0.7 (Creative)"), 0.7),
	(_("1.0 (Very creative)"), 1.0),
]

# Nomes de idioma propositalmente NÃO traduzidos (_()) — convenção comum é
# mostrar cada idioma em seu próprio nome nativo, independente do idioma da
# UI do NVDA. Não confundir com o catálogo gettext desta UI: isto alimenta
# response_language (idioma em que a IA responde), um sistema separado.
LANGUAGES = [
	("Português (Brasil)", "pt-br"),
	("English (US)", "en"),
	("Español", "es"),
	("Français", "fr"),
	("Deutsch", "de"),
	("Italiano", "it"),
]


class SettingsPanel(gui.settingsDialogs.SettingsPanel):
	title = addon_config.ADDON_NAME

	def _load_config_value(self, key, default):
		"""Carrega valor da config com fallback."""
		try:
			return config.conf[addon_config.CONFIG_SECTION][key]
		except KeyError:
			return default

	def _find_preset_index(self, current_value, presets):
		"""Encontra índice do preset mais próximo ao valor atual."""
		for i, (label, value) in enumerate(presets):
			if abs(value - current_value) < addon_config.TEMPERATURE_TOLERANCE:
				return i
		return -1

	def makeSettings(self, settingsSizer):
		helper = gui.guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

		# --- Conexão com Gemini ---
		groupLabel = _("Connection and API")
		box = wx.StaticBox(self, label=groupLabel)
		sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

		apiKeyLabel = wx.StaticText(box, label=_("Gemini API key:"))
		sizer.Add(apiKeyLabel, 0, wx.ALL, 5)
		self.apiKeyCtrl = wx.TextCtrl(box, style=wx.TE_PASSWORD)
		self.apiKeyCtrl.SetValue(self._load_config_value("gemini_api_key", ""))
		sizer.Add(self.apiKeyCtrl, 0, wx.EXPAND | wx.ALL, 5)

		self.showApiCheck = wx.CheckBox(box, label=_("Show API key"))
		self.showApiCheck.Bind(wx.EVT_CHECKBOX, self.onToggleApiVisibility)
		sizer.Add(self.showApiCheck, 0, wx.ALL, 5)

		modelLabel = wx.StaticText(box, label=_("Gemini model:"))
		sizer.Add(modelLabel, 0, wx.ALL, 5)
		current_model = self._load_config_value("gemini_model", GEMINI_MODELS[addon_config.DEFAULT_MODEL_INDEX])
		# Garante que o modelo salvo apareça na lista mesmo que não esteja
		# em GEMINI_MODELS (ex: foi escolhido numa busca dinâmica anterior)
		# — senão, reabrir o painel sem buscar de novo perderia a seleção.
		initial_models = list(GEMINI_MODELS)
		if current_model not in initial_models:
			initial_models.insert(0, current_model)
		self.modelChoice = wx.Choice(box, choices=initial_models)
		self.modelChoice.SetSelection(initial_models.index(current_model))
		sizer.Add(self.modelChoice, 0, wx.EXPAND | wx.ALL, 5)

		self.refreshModelsBtn = wx.Button(box, label=_("Refresh model list"))
		self.refreshModelsBtn.Bind(wx.EVT_BUTTON, self.onRefreshModels)
		sizer.Add(self.refreshModelsBtn, 0, wx.ALL, 5)

		helper.addItem(sizer)

		# --- Comportamento da IA ---
		groupLabel = _("AI behavior")
		box = wx.StaticBox(self, label=groupLabel)
		sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

		tempLabel = wx.StaticText(box, label=_("Temperature (Creativity):"))
		sizer.Add(tempLabel, 0, wx.ALL, 5)
		self.tempChoice = wx.Choice(box, choices=[x[0] for x in TEMPERATURE_PRESETS])
		current_temp = self._load_config_value("ai_temperature", 0.4)
		selected_index = self._find_preset_index(current_temp, TEMPERATURE_PRESETS)
		self.tempChoice.SetSelection(selected_index if selected_index >= 0 else addon_config.DEFAULT_TEMP_INDEX)
		sizer.Add(self.tempChoice, 0, wx.EXPAND | wx.ALL, 5)

		turnsLabel = wx.StaticText(box, label=_("Maximum number of iterations (1-300):"))
		sizer.Add(turnsLabel, 0, wx.ALL, 5)
		self.turnsSpinCtrl = wx.SpinCtrl(box, min=1, max=300)
		self.turnsSpinCtrl.SetValue(self._load_config_value("max_turns", addon_config.DEFAULT_MAX_TURNS))
		sizer.Add(self.turnsSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		delayLabel = wx.StaticText(box, label=_("Maximum wait time between iterations (sec):"))
		sizer.Add(delayLabel, 0, wx.ALL, 5)
		self.delaySpinCtrl = wx.SpinCtrl(box, min=1, max=10)
		self.delaySpinCtrl.SetValue(int(self._load_config_value("turn_delay", addon_config.DEFAULT_TURN_DELAY)))
		sizer.Add(self.delaySpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		timeoutLabel = wx.StaticText(box, label=_("API timeout (sec):"))
		sizer.Add(timeoutLabel, 0, wx.ALL, 5)
		self.timeoutSpinCtrl = wx.SpinCtrl(box, min=5, max=120)
		self.timeoutSpinCtrl.SetValue(self._load_config_value("api_timeout", addon_config.DEFAULT_API_TIMEOUT))
		sizer.Add(self.timeoutSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		sessionsLabel = wx.StaticText(box, label=_("Maximum saved sessions (1-500):"))
		sizer.Add(sessionsLabel, 0, wx.ALL, 5)
		self.sessionsSpinCtrl = wx.SpinCtrl(box, min=1, max=500)
		self.sessionsSpinCtrl.SetValue(self._load_config_value("max_sessions", addon_config.DEFAULT_MAX_SESSIONS))
		sizer.Add(self.sessionsSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		helper.addItem(sizer)

		# --- Idioma e Localização ---
		groupLabel = _("Language and localization")
		box = wx.StaticBox(self, label=groupLabel)
		sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

		langLabel = wx.StaticText(box, label=_("Response language:"))
		sizer.Add(langLabel, 0, wx.ALL, 5)
		self.langChoice = wx.Choice(box, choices=[x[0] for x in LANGUAGES])
		current_lang = self._load_config_value("response_language", "pt-br")
		for i, (label, lang_code) in enumerate(LANGUAGES):
			if lang_code == current_lang:
				self.langChoice.SetSelection(i)
				break
		else:
			self.langChoice.SetSelection(0)
		sizer.Add(self.langChoice, 0, wx.EXPAND | wx.ALL, 5)

		helper.addItem(sizer)

		# --- Agendamento e Automação ---
		groupLabel = _("Scheduling and automation")
		box = wx.StaticBox(self, label=groupLabel)
		sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

		checkIntervalLabel = wx.StaticText(box, label=_("Scheduled task check interval (5-3600 sec):"))
		sizer.Add(checkIntervalLabel, 0, wx.ALL, 5)
		self.schedulerCheckIntervalSpinCtrl = wx.SpinCtrl(box, min=5, max=3600)
		self.schedulerCheckIntervalSpinCtrl.SetValue(
			self._load_config_value("scheduler_check_interval", addon_config.DEFAULT_SCHEDULER_CHECK_INTERVAL)
		)
		sizer.Add(self.schedulerCheckIntervalSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		graceLabel = wx.StaticText(box, label=_("Warning time before running a scheduled task (5-120 sec):"))
		sizer.Add(graceLabel, 0, wx.ALL, 5)
		self.schedulerGraceSpinCtrl = wx.SpinCtrl(box, min=5, max=120)
		self.schedulerGraceSpinCtrl.SetValue(
			self._load_config_value("scheduler_confirm_grace_seconds", addon_config.DEFAULT_SCHEDULER_CONFIRM_GRACE_SECONDS)
		)
		sizer.Add(self.schedulerGraceSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		askUserTimeoutLabel = wx.StaticText(
			box, label=_("Maximum wait time for a user response (ask_user_tool, 10-600 sec):")
		)
		sizer.Add(askUserTimeoutLabel, 0, wx.ALL, 5)
		self.askUserTimeoutSpinCtrl = wx.SpinCtrl(box, min=10, max=600)
		self.askUserTimeoutSpinCtrl.SetValue(
			self._load_config_value("ask_user_timeout_seconds", addon_config.DEFAULT_ASK_USER_TIMEOUT_SECONDS)
		)
		sizer.Add(self.askUserTimeoutSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		self.enableShellToolCheck = wx.CheckBox(box, label=_("Allow the AI to run terminal commands (shell_tool)"))
		self.enableShellToolCheck.SetValue(bool(self._load_config_value("enable_shell_tool", False)))
		sizer.Add(self.enableShellToolCheck, 0, wx.ALL, 5)

		shellTimeoutLabel = wx.StaticText(box, label=_("Maximum terminal command execution time (1-120 sec):"))
		sizer.Add(shellTimeoutLabel, 0, wx.ALL, 5)
		self.shellTimeoutSpinCtrl = wx.SpinCtrl(box, min=1, max=120)
		self.shellTimeoutSpinCtrl.SetValue(
			self._load_config_value("shell_command_timeout_seconds", addon_config.DEFAULT_SHELL_COMMAND_TIMEOUT_SECONDS)
		)
		sizer.Add(self.shellTimeoutSpinCtrl, 0, wx.EXPAND | wx.ALL, 5)

		helper.addItem(sizer)

	def onRefreshModels(self, event):
		api_key = self.apiKeyCtrl.GetValue().strip()
		if not api_key:
			gui.messageBox(
				_("Enter the API key before refreshing the model list."),
				_("Error"), wx.OK | wx.ICON_ERROR, self
			)
			return

		self.refreshModelsBtn.Enable(False)
		self.refreshModelsBtn.SetLabel(_("Fetching models..."))
		threading.Thread(
			target=self._refresh_models_worker,
			args=(api_key,),
			daemon=True
		).start()

	def _refresh_models_worker(self, api_key):
		try:
			client = gemini_client.GeminiClient(api_key=api_key)
			models = client.list_models()
			if not models:
				raise RuntimeError(_("No compatible model was returned by the API."))
			wx.CallAfter(self._apply_fetched_models, models)
		except Exception as e:
			wx.CallAfter(self._on_refresh_error, str(e))

	def _apply_fetched_models(self, models):
		current = self.modelChoice.GetStringSelection()
		self.modelChoice.Set(models)
		if current in models:
			self.modelChoice.SetSelection(models.index(current))
		else:
			self.modelChoice.SetSelection(0)
		self.refreshModelsBtn.SetLabel(_("Refresh model list"))
		self.refreshModelsBtn.Enable(True)
		ui.message(ngettext("%d model loaded from the API", "%d models loaded from the API", len(models)) % len(models))

	def _on_refresh_error(self, msg):
		self.refreshModelsBtn.SetLabel(_("Refresh model list"))
		self.refreshModelsBtn.Enable(True)
		ui.message(_("Could not refresh the model list: %s") % msg)

	def onToggleApiVisibility(self, event):
		if self.showApiCheck.Value:
			self.apiKeyCtrl.SetWindowStyle(0)
		else:
			self.apiKeyCtrl.SetWindowStyle(wx.TE_PASSWORD)
		self.apiKeyCtrl.Refresh()

	def onSave(self):
		api_key = self.apiKeyCtrl.GetValue().strip()
		if not api_key:
			gui.messageBox(_("API key cannot be empty"), _("Error"), wx.OK | wx.ICON_ERROR, self)
			return

		model_idx = self.modelChoice.GetSelection()
		if model_idx < 0:
			model_idx = addon_config.DEFAULT_MODEL_INDEX
		model_name = self.modelChoice.GetString(model_idx)

		config.conf[addon_config.CONFIG_SECTION]["gemini_api_key"] = api_key
		config.conf[addon_config.CONFIG_SECTION]["gemini_model"] = model_name
		config.conf[addon_config.CONFIG_SECTION]["ai_temperature"] = TEMPERATURE_PRESETS[self.tempChoice.GetSelection()][1]
		config.conf[addon_config.CONFIG_SECTION]["max_turns"] = self.turnsSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["turn_delay"] = float(self.delaySpinCtrl.GetValue())
		config.conf[addon_config.CONFIG_SECTION]["api_timeout"] = self.timeoutSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["response_language"] = LANGUAGES[self.langChoice.GetSelection()][1]
		config.conf[addon_config.CONFIG_SECTION]["max_sessions"] = self.sessionsSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["scheduler_check_interval"] = self.schedulerCheckIntervalSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["scheduler_confirm_grace_seconds"] = self.schedulerGraceSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["ask_user_timeout_seconds"] = self.askUserTimeoutSpinCtrl.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["enable_shell_tool"] = self.enableShellToolCheck.GetValue()
		config.conf[addon_config.CONFIG_SECTION]["shell_command_timeout_seconds"] = self.shellTimeoutSpinCtrl.GetValue()

		logger.info(
			f"Configurações salvas: model={model_name} "
			f"temperature={TEMPERATURE_PRESETS[self.tempChoice.GetSelection()][1]} "
			f"max_turns={self.turnsSpinCtrl.GetValue()} turn_delay={self.delaySpinCtrl.GetValue()} "
			f"api_timeout={self.timeoutSpinCtrl.GetValue()} "
			f"language={LANGUAGES[self.langChoice.GetSelection()][1]} "
			f"max_sessions={self.sessionsSpinCtrl.GetValue()} "
			f"scheduler_check_interval={self.schedulerCheckIntervalSpinCtrl.GetValue()} "
			f"scheduler_confirm_grace_seconds={self.schedulerGraceSpinCtrl.GetValue()} "
			f"ask_user_timeout_seconds={self.askUserTimeoutSpinCtrl.GetValue()} "
			f"enable_shell_tool={self.enableShellToolCheck.GetValue()} "
			f"shell_command_timeout_seconds={self.shellTimeoutSpinCtrl.GetValue()}"
		)
