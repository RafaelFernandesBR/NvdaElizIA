# Changelog

## 1.9.0

- Suporte a tradução (i18n): a interface do addon (falas, diálogos de chat/sessões/agendamento,
  painel de configurações) agora usa a infraestrutura oficial de tradução do NVDA
  (`addonHandler.initTranslation()`), com um catálogo `pt-BR` que preserva o comportamento atual
  — pronto para receber outros idiomas no futuro. Strings enviadas só para a IA (prompt de
  sistema, schema das ferramentas) e dados internos (ex.: status de tarefas agendadas no banco)
  permanecem intencionalmente fora da tradução.

## 1.8.0

Primeira versão publicada no GitHub.

- Agendamento de tarefas: a IA (ou o usuário, pedindo no chat) pode marcar um comando para
  rodar num horário futuro (`schedule_task_tool`). Ao vencer, um aviso sonoro e uma janela
  de graça cancelável antecedem a execução de verdade — nunca dispara silenciosamente.
  Tarefas perdidas (NVDA fechado no horário) ficam marcadas para revisão manual, nunca
  rodam nem são descartadas sozinhas. Gerenciamento via `NVDA+Shift+A`.
- Novas ferramentas de apoio para a IA: abrir programas (`app_tool`), perguntar ao usuário
  em meio à automação (`ask_user_tool`) e executar um comando no `cmd.exe` com confirmação
  explícita obrigatória (`shell_tool`, desligada por padrão).
- Seções (sessões de conversa) persistidas em SQLite, com retomada, renomeação e exclusão
  via `NVDA+Shift+O`.
- Captura de tela com downscale automático, conversão de coordenadas por resolução real e
  circuit breaker contra loops de ação repetida sem mudança de tela.
- Recusa explícita a iniciar (ou continuar) automação enquanto a Cortina de Tela do NVDA
  estiver ativa.
