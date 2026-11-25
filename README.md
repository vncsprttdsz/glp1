# Monitor Anvisa – GLP-1 manipulados

Acompanha o processo **25351.912958/2026-33** (alteração da regulação de
medicamentos manipulados de GLP-1) no site da Anvisa e abre uma **issue** neste
repositório a cada mudança relevante — o GitHub manda e-mail/notificação.

## O que é verificado

| Página | Alertas |
|---|---|
| [Pautas da Dicol](https://www.gov.br/anvisa/pt-br/composicao/diretoria-colegiada/reunioes-da-diretoria/pautas) | nova pauta publicada (com ou sem o processo); pauta republicada; processo **incluído** ou **retirado** de uma pauta |
| [Minutas prévias](https://www.gov.br/anvisa/pt-br/assuntos/regulamentacao/agenda-regulatoria/minutas-previas) | página atualizada para uma nova reunião; processo aparece / some / é marcado "RETIRADO DE PAUTA"; **link da minuta (IN/RDC) publicado** |

Também avisa (label `possivel`) se termos como GLP-1, semaglutida ou tirzepatida
aparecerem sem o número do processo, caso o tema volte com outro número.

## Como funciona

- `monitor.py` lê o conteúdo pela API REST do portal (`/anvisa/++api++/...`) e
  baixa os PDFs das pautas mais recentes. Cada mudança vira um item em
  `events.json` (ou uma issue direta, se `GITHUB_TOKEN` estiver definido).
- **Onde roda:** uma tarefa agendada do Claude Code (de hora em hora, 7h–22h,
  horário de Brasília) executa `python3 monitor.py`, abre uma issue para cada
  evento e faz commit de `state.json`/`history.md`.
- O GitHub Actions **não** funciona com agendamento: o gov.br derruba as conexões
  dos runners do GitHub (bloqueio de IPs estrangeiros). O workflow
  `.github/workflows/monitor.yml` ficou só com execução manual, útil com um
  runner self-hosted no Brasil.
- `state.json` guarda o último estado visto; `history.md` registra as mudanças.
- Se o site falhar em 8 execuções seguidas, gera um evento `erro`.

Para trocar o processo monitorado, edite `PROCESSO` e `PROC_RE` em `monitor.py`.
