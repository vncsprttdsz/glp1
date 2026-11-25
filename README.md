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
  baixa os PDFs das pautas mais recentes.
- `.github/workflows/monitor.yml` roda a cada 15 min em dias úteis (7h–21h,
  horário de Brasília) e de hora em hora no resto do tempo. Também dá para rodar
  manualmente em *Actions → Monitor Anvisa → Run workflow*.
- `state.json` guarda o último estado visto; `history.md` registra as mudanças.
- Se o site falhar em 8 execuções seguidas, abre uma issue `erro`.

Para trocar o processo monitorado, edite `PROCESSO` e `PROC_RE` em `monitor.py`.
