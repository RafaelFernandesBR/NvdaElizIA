# ElizIA Visual

Addon para o [NVDA](https://www.nvaccess.org/) que permite a usuários cegos controlar o
Windows por comandos em linguagem natural. Você descreve o que quer fazer, a IA
([Google Gemini](https://ai.google.dev/)) "vê" a tela através de screenshots e decide
quais ações tomar — cliques, teclado, digitação, scroll, abrir programas — até declarar a
tarefa concluída.

## Requisitos

- NVDA 2026.1 ou mais recente.
- Uma chave de API do Google Gemini, gratuita em [aistudio.google.com](https://aistudio.google.com/app/apikey).

## Configuração inicial

1. Instale o addon (`.nvda-addon`) pelo Gerenciador de Complementos do NVDA.
2. Abra **Preferências > Configurações > ElizIA Visual** e cole sua chave de API do Gemini.
3. Pronto — os atalhos abaixo já funcionam.

## Atalhos principais

| Atalho | Ação |
|---|---|
| `NVDA+Shift+R` | Abre um diálogo para você descrever o que quer fazer e inicia a automação. |
| `NVDA+Shift+O` | Abre a lista de sessões salvas (retomar, renomear ou excluir conversas anteriores). |
| `NVDA+Shift+A` | Abre o gerenciador de tarefas agendadas (cancelar/excluir agendamentos pendentes). |
| `NVDA+Shift+C` | Cancela a automação que estiver em execução no momento. |

Todos os atalhos podem ser remapeados em **Preferências > Gestos de entrada**, categoria
"ElizIA Visual".

## O que a IA pode fazer

- Clicar, clicar com o botão direito, clique duplo, digitar texto, pressionar teclas,
  rolar a tela e esperar a tela carregar.
- Abrir programas pelo nome.
- Agendar uma tarefa para rodar num horário futuro — com aviso sonoro e uma janela de
  tempo cancelável antes de disparar de verdade, nunca silenciosamente.
- Monitorar algo que se repete ao longo do tempo (ex: "veja o preço dessa passagem 3
  vezes nas próximas 2 horas e me avise se mudar") — a cada checagem a IA compara com o
  resultado anterior; se você pedir pra só avisar quando algo mudar, uma checagem sem
  novidade só é registrada, sem interromper você. Cada checagem ainda assume a tela de
  verdade (o aviso sonoro de "vou começar agora" antes de cada disparo continua valendo),
  só a notificação do RESULTADO é que pode ficar silenciosa.
- Em tarefas longas, com muitos passos, anotar fatos importantes (um preço encontrado,
  uma opção já descartada) que não se perdem mesmo depois de muitos turnos.
- Perguntar algo a você no meio de uma automação, quando precisar de uma decisão sua.
- Executar um comando no `cmd.exe` — **desligado por padrão**; se habilitado nas
  configurações, qualquer comando ainda exige sua confirmação explícita antes de rodar.

A automação sempre se recusa a continuar enquanto a Cortina de Tela do NVDA estiver
ativa, e nunca aplica uma ação da IA que não reconheça ou valide — nesse caso ela devolve
um erro para a IA tentar de novo. Uma automação em andamento pode ser interrompida a
qualquer momento com `NVDA+Shift+C`.

## Configurações disponíveis

Em **Preferências > Configurações > ElizIA Visual**:

- Chave de API, modelo do Gemini e idioma das respostas.
- Número máximo de passos por tarefa e o intervalo entre eles.
- Limite de sessões salvas.
- Intervalo de verificação e janela de aviso para tarefas agendadas.
- Habilitar ou não a ferramenta de comando de shell.

## Código-fonte e documentação técnica

O código-fonte, a documentação de arquitetura (`ARCHITECTURE.md`) e as instruções de
desenvolvimento (`CLAUDE.md`) estão neste repositório:
https://github.com/RafaelFernandesBR/NvdaElizIA

## Licença

Distribuído sob a GNU General Public License, versão 2 ou posterior — veja
[`COPYING.txt`](COPYING.txt).
