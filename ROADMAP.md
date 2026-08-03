# ROADMAP — Community Cam Guard (CCG)

Documento vivo. Backlog, prioridades e marcos. Detalhe técnico e justificativas ficam em
`docs/` (ADRs); aqui fica **o quê** e **em que ordem**, não o **como**.

Legenda de prioridade: **P0** crítico · **P1** alto · **P2** médio · **P3** oportunista.
Status: `todo` · `wip` · `done` · `blocked`.

---

## Marco M1 — Qualidade de vídeo ao vivo (Feature 1)  ⟶ EM ANDAMENTO

Objetivo: aproximar a imagem do app oficial, com um **seletor de qualidade** cujo default é o
máximo que a câmera + host sustentam. Referência de diagnóstico: `docs/DECISIONS.md §34`.

| Prioridade | Item | Status |
|---|---|---|
| P0 | **Bitrate alvo + GOP** no transcode (`-b:v/-maxrate/-bufsize/-g`) — antes só defaults do go2rtc | done |
| P0 | Modelo de **níveis de qualidade** (`low`/`medium`/`high`/`max`) mapeando fonte→bitrate (`media/quality.py`) | done |
| P0 | Config `live_quality` (default `max`) + testes unitários (`test_quality.py`, wiring do `build_config`) | done |
| P1 | **Seletor de qualidade por câmera na UI** — dropdown Auto/HD/SD (client-side/instantâneo) + endpoint expõe qualidade | done |
| P0 | **Travamento do HD RESOLVIDO** — era MSE-sobre-internet (viewer remoto). Forçar `mode=webrtc,mse` no player resolveu; usuário confirmou HD liso nas 2 câmeras. Diagnóstico via `scripts/diagnose_streams.sh` | done |
| P1 | **Polimento dos controles** (feedback): dropdown de qualidade, D-pad de PTZ em cruz, borda em todos os botões, barra mais alta | done |
| P2 | Resiliência a engasgo da câmera (EOF→reconnect do transcode) — tuning de timeout/reconnect no go2rtc (fator secundário) | todo |
| P2 | **Aceleração por hardware** (`live_hwaccel`: vaapi/cuda/v4l2m2m/...) — plumbing + testes prontos | done¹ |
| P1 | **Validação visual** dos níveis de bitrate + hwaccel contra o go2rtc real → `FEEDBACK.md` | blocked² |
| P2 | Perfil `high`/preset no encoder — go2rtc vem UPX-packed, template não inspecionável; validar antes | blocked² |
| P2 | Auto-degradação sob pressão de CPU (respeita `grid_hd_max_cameras` como guarda de host) | todo |
| P2 | UI: controle global de `live_quality` (bitrate) — precisa endpoint de settings + restart go2rtc | todo |

¹ Gated (default `""` = software, comportamento atual). Precisa do GPU do usuário pra valer — ver `FEEDBACK.md §3`.
² Depende do hardware/olho do usuário. Passos e o que reportar em `FEEDBACK.md`.
| P3 | Medir e documentar qualidade vs. app oficial (bitrate/resolução/latência lado a lado) | todo |

## Marco M2 — Arquitetura & qualidade de código (Feature 2)

| Prioridade | Item | Status |
|---|---|---|
| P1 | Tooling: `ruff` (lint, ruleset focado) + `mypy` (limpo, 31 arquivos) + CI rodando lint+type+testes | done |
| P1 | `black` configurado no pyproject — **aplicar** formatação em massa (37 arquivos) = passo deliberado à parte | todo |
| P1 | Cobertura de testes ≥ 90% — **65% → 72%** (221 testes). Feito: `routes.py` 88%, `main.py` 76%, `ptz.py`/`device.py` ~75-82%. Falta: discovery (`ws_discovery` 24%, `active_scan` 37%), `recorder.py` 66%, `drivers/base.py` 62% | wip |
| P1 | Formalizar camada de drivers (Strategy + Factory) — **já pronto**: `CameraDriver` + registry ordenado + `detect`/`for_camera`/`get` + fallback genérico | done |
| P1 | Injeção de dependência p/ serviços (go2rtc, recorder, registry) — remover singletons/acoplamento | todo |
| P2 | Remover condicionais por fabricante espalhadas; isolar em drivers/adapters | todo |
| P2 | Quebrar arquivos grandes por responsabilidade única (auditar `recorder.py`, `routes.py`) | todo |

## Marco M3 — Documentação & multiplataforma (Feature 3)

| Prioridade | Item | Status |
|---|---|---|
| P1 | Estrutura `docs/public/` + `docs/internal/` + índice. **ADRs iniciados** (6 fundacionais: drivers, identidade-MAC, discovery, gravação, live-view, OOM); migração das demais decisões do `DECISIONS.md` segue em lotes | wip |
| P1 | **Doc de API/endpoints** — `docs/public/api.md` (referência) + Swagger/ReDoc em `/api/docs`,`/api/redoc`, schema em `/api/openapi.json` | done |
| P2 | README com índice/documentação apontando pra `docs/`; roadmap desduplicado; fatos atualizados | done |
| P2 | `CONTRIBUTING.md`: padrões (ruff/mypy/pytest), fluxo de PR, regra de segredos, plug-in de driver | done |
| P2 | Infra: Dockerfile/compose/.dockerignore revisados (comentário "SKELETON" desatualizado corrigido, contexto de build enxuto, framing cross-platform) | done |
| P2 | Suporte Windows/Linux/macOS documentado (Docker roda em todos; WSL reenquadrado como opção do Windows, não requisito). Falta: testar de fato em macOS/Windows nativo | wip |

## Fora de escopo / paralelo (RE do protocolo P2P)

A engenharia reversa do controle P2P (reboot/PTZ/two-way talk direto) segue como trilha
separada — ver `re/notes/` e `re/notes/penetrate-commands.md` (dicionário de comandos já
recuperado). Não bloqueia os marcos acima.

---

_Convenção: ao concluir um item, marque `done` e mova o detalhe técnico/justificativa para um ADR
em `docs/internal/`._
