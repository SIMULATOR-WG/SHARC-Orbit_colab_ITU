# Plano de Implementação — Requisitos INPUT/OUTPUT

**Base:** aba `REQUISITOS_REV_2026-07` de `SIMULATIONS_LOG_SHARC-Orbit.xlsx` (revisão de 2026-07-03, auditoria do código na branch `feat/wcga-no-geometry-error`, fundamentada em Rec. ITU-R S.1503-4 (09/2023), SNS Database Format v10.5 e SRS Diagram v10.1).

**Situação atual (revisão 2026-07-03, branch `fix/wcga-epsgso-or-branch`):** 21 requisitos implementados · 7 parciais · 0 não implementados. Marcos **M1–M6 entregues** (com remanescentes em WS1 e WS3): WS4 e WS5 completos (artefatos por run + relatório normativo D7.3), WS2 completo (entrada manual/paramétrica ponta a ponta), WS1 substancialmente implementado (leitura de configs mutuamente exclusivas + badge S/M + seleção por config), WS3 parcial (gerador de máscaras PFD Parte C entregue; resta R7 — apontamento fixo-ao-corpo vs steerable). Status por frente marcado nas seções abaixo (**✓ concluído** / **parcial**).

O plano organiza os 28 requisitos em **5 frentes de trabalho (WS)**, ordenadas por prioridade da planilha (R2 = prioridade 0; R3/R4 = prioridade 1) e por dependência técnica. Cada tarefa cita a âncora normativa e os pontos de código afetados.

---

## Visão geral

| WS | Escopo | Requisitos | Esforço | Depende de | Status |
|----|--------|-----------|---------|------------|--------|
| WS1 | Configurações mutuamente exclusivas | R2 | M | — | Parcial (leitura + badge + seleção por config; falta execução N-por-config) |
| WS2 | Entrada manual/paramétrica | R3, R4 | M | — | **✓ concluído** |
| WS3 | Ferramenta de geração de máscaras PFD (Parte C) | R5, R6, R7, R27, R28, R29 | **G** | — (WS2 ajuda) | Parcial (gerador Opção 1/2 + XML C4.2 + mitigação GSO-arc entregues; R7 apontamento fixo/steerable parcial) |
| WS4 | Exportação de artefatos de resultado | R9–R16 | M | — | **✓ concluído** |
| WS5 | Relatório-resumo e tabelas normativas (D7.3) | R17–R26 | M | WS4 (dados) | **✓ concluído** |

R8 (export de parâmetros) já está implementado (`params.json` por run + SQLite) — sem ação.

Legenda de esforço: P (dias), M (1–2 semanas), G (várias semanas).

---

## WS1 — Configurações mutuamente exclusivas (R2) — prioridade 0

**Norma:** SNS AP4 `non_geo.multi_config_typ` (A.4.b.3.b: `S`/`M`), `non_geo.nbr_config` (A.4.b.3.c), `orbit.orbit_set_id` (A.4.b.3.d), tabela `orbit_set` (PK `ntc_id`+`orbit_set_id`, com limites agregados A.26.a/b); S.1503-4 §D2.1 (um run por conjunto único de elementos orbitais + características operacionais).

**Risco atual:** filings `M` são simulados como uma constelação única fictícia (soma de planos de configs mutuamente exclusivas) → **EPFD superestimado**.

**Fato de dados** (levantamento de 2026-05): 1 SRS `.mdb` = exatamente 1 configuração; os rótulos de configuração aparecem de 3 formas inconsistentes por operador — campo `orbit_set_id`, subpasta `ConfigN/`, token `Config#` no nome do arquivo. Vários notices `M` do dataset local têm só 1 config baixada (completar via BR IFIC antes de estudos agregados).

**Estado (2026-07-03): parcial — leitura + seleção por config entregues.** `src/srs_reader.py` lê `multi_config_type`/`nbr_config`/`orbit_set_id` (expostos em `SRSNonGeoSystem`/`SRSOrbitalPlane`) e `resolve_config()` resolve o rótulo por cascata `orbit_set_id` → subpasta → token no filename (`config_label`); a UI exibe badge S/M + `nbr_config` e um seletor de configuração (`streamlit_app/pages/3_Single_entry.py`, `streamlit_app/lib/srs_inspect.py`) e `streamlit_app/pages/8_Results.py` marca cada resultado com sua config — de modo que configs distintas **nunca** são somadas numa constelação fictícia (§D2.1). **Resta:** disparar automaticamente as N execuções por `orbit_set_id` dentro de um mesmo "run" do usuário (tarefa 4) e agrupar/consolidar o pior caso entre configs (tarefa 5) — hoje simula-se uma config selecionada por vez.

**Tarefas:**
1. **Leitura dos campos SNS** — `src/srs_reader.py`: ler `multi_config_typ`, `nbr_config` (tabela `non_geo`) e `orbit_set_id` (tabela `orbit`); ler tabela `orbit_set` quando existir. Expor em `SRSNonGeoSystem`.
2. **Resolvedor de configuração** — cascata `orbit_set_id` → subpasta → token no filename; validar `count == nbr_config`; atenção: uma config pode ter múltiplas inclinações (geometria NÃO indica config; confiar no rótulo).
3. **UI (Upload/Launcher)** — exibir badge `S`/`M` + `nbr_config` no filing; multiselect das configurações a simular; aviso quando faltarem configs do conjunto.
4. **Execução por configuração** — no mesmo "run" do usuário, gerar N execuções S.1503-4 independentes (uma por `orbit_set_id`), sem soma de EPFD entre elas (§D2.1); relatório por config + pior caso.
5. **Persistência/Resultados** — `params.json`/`sim_data.json` ganham `orbit_set_id`; página Results agrupa por configuração.

**Aceite:** filing `M` com 2+ configs no dataset de teste roda por config; nenhum plano de configs distintas coexiste numa mesma propagação; veredito por config.

---

## WS2 — Entrada manual/paramétrica (R3, R4) — prioridade 1

**Norma:** S.1503-4 §B3.1 (Nsat, H_MIN, DoesRepeat, AdminSuppliedPrecession, Wdelta, ORBIT_PRECESS), §B3.2 (por satélite: A/E/I/O/W/V), §B3.3 (parâmetros operacionais por faixa), §B4.1 (máscara: FreqMin/Max, RefBW, MaskType); §D6.3.6 (3 casos de modelo orbital comandados por flags — Caso 3 = massa pontual SEM J2); §D6.3.7 (mapeamento: `a=Re+(ha+hp)/2`, `e=(ha−hp)/2a`, Ω=`long_asc` — longitude do nó, não ascensão reta —, ω=`perig_arg`, ν₀=`phase_ang`−ω). SNS `orbit` A.4.b.4.* e `phase` A.4.b.4.h.

**Estado (2026-07-03): ✓ concluído.** Entregue ponta a ponta: `load_from_manual()` (`src/main.py`) lê RAAN/ω/ν₀ **por plano** de `_planes` (não mais fixos em 0) e converte apogeu/perigeu → a,e (§D6.3.7); `src/constellation_templates.py` cobre o núcleo do assistente (Walker Delta/Star, anel equatorial, trem, multi-shell, Molniya, Tundra, IGSO) e os auxiliares (sun-síncrona, repeat-ground-track); `config.example.yaml` no repositório; nova página `streamlit_app/pages/E_Manual_System.py` (wizard de template + preview 3D no globo + botão "registrar como filing"), lançada pelo mesmo caminho de motor dos filings MDB (`streamlit_app/lib/launcher.py`, `s1503_worker.py`). Complemento (WS2-3): `src/mdb_writer.py` escreve um par `<base>_SRS.mdb`/`<base>_Mask.mdb` JET4 real via ponte Java/Jackcess (`tools/jackcess/`, requer JRE com `jdk.compiler`) ou fallback YAML+XML pelo fluxo Upload.

**Tarefas:**
1. **Completar o esquema YAML** (`src/main.py`):
   - aceitar `apogee_km`/`perigee_km` com conversão §D6.3.7 (mover/reusar a conversão hoje presa em `srs_reader.SRSOrbitalPlane`);
   - consumir `raan_deg`, `arg_perigee_deg`, `true_anomaly_deg`/`phase_deg` **por plano/satélite** (hoje escritos e nunca lidos; caminho Walker fixa tudo em 0) — expor a via `_planes` como interface pública documentada;
   - flags de modelo orbital: `does_repeat`, `wdelta_deg`, `admin_precession_deg_day` mapeando para os Casos 1/2/3 de §D6.3.6 — **Caso 3 deve desligar J2** (massa pontual `M=M0+n0·t`, ω fixo);
   - parâmetros operacionais §B3.3 manuais (α₀/X por latitude, ε₀[lat][az], MAX_CO_FREQ...) — hoje só via MDB/XML.
2. **`config.example.yaml`** no repositório + seção no README (esquema completo, unidades da Tabela 1 A2.1).
3. **UI Streamlit** — nova página (ou modo na Upload): formulário com os parâmetros mínimos do R4; associação de máscara por upload de XML avulso (`load_pfd_mask_from_xml_content` já suporta); registrar o sistema manual no storage com a mesma interface dos filings MDB (o worker passa a aceitar `source: manual`).
4. **Assistente de constelações (templates)** — gerador automático de formatos conhecidos que **pré-preenche** o formulário manual (usuário revisa/edita plano a plano antes de rodar):

   *Núcleo (padrões de filings reais):*
   - **Walker Delta** `i:T/P/F` — já existe no motor (`create_walker_constellation`, `src/orbit_propagator.py:224`: RAAN espalhado em 360°, fase `F·360/T` entre planos); só expor na UI;
   - **Walker Star (polar)** — RAAN espalhado em ~180° (planos contra-rotativos no *seam*, padrão Iridium/OneWeb), inclinação quase polar; parâmetro `raan_spread_deg` (360 = Delta, 180 = Star) generaliza os dois;
   - **Anel equatorial** (i≈0) — plano único MEO/LEO equatorial (caso real: O3b, ~8 062 km);
   - **Plano único / trem** — 1 plano, N satélites igualmente espaçados (fase configurável);
   - **Multi-shell (composição)** — empilhar N templates num único sistema (Starlink/Kuiper são pilhas de Walkers com a/i distintos); cada shell vira um grupo de planos com `orb_id` próprios — espelha a estrutura de grupos de órbita do SRS;

   *HEO / geossíncronas inclinadas:*
   - **Molniya** — a/e para T=12h, i=63.4° (crítica), ω=270°, apogeu sobre latitude alvo;
   - **Tundra** — T=24h, i=63.4°, ω=270° (tipo Sirius XM);
   - **IGSO** — geossíncrona inclinada, traço em "figura-8" (tipo QZSS);

   *Auxiliares transversais (aplicam-se a qualquer template):*
   - **Sun-síncrona** — deriva `i` de `a` (precessão nodal J2 = 0.9856°/dia);
   - **Repeat ground track** — escolhe `a` para a trilha fechar em j dias / k revoluções; preenche `DoesRepeat`/período de repetição coerentes com §B3.1 e `rpt_prd_*` do SNS (validar contra o dimensionamento repeat-track já usado no motor);
   - **Rosette de Ballard (n, m, k)** (opcional) — generalização formal da Walker, custo marginal;
   - **Dimensionamento por cobertura** (opcional, ferramenta de projeto) — nº de planos/sats a partir de elevação mínima + faixa de latitude (street-of-coverage).

   Fora de escopo (acadêmicos/nicho, adicionar se surgir demanda): Flower/Lattice Flower Constellations, Draim.

   Saída do assistente = a lista `_planes` por plano/satélite (RAAN, ω, ν₀) do item 1 — nada fixo em 0, conforme §B3.2/§D6.3.7 — em formato **editável** (data_editor) e serializável para o `config.yaml`. Preview 3D no globo (reusar `earth_3d_chart` do Constellation Viewer).
5. **Validação** — aplicar limites de §B5.1/§B5.2 na entrada manual (faixas de a, e, i etc.).

**Aceite:** simular uma Walker definida 100% na UI (sem MDB), com máscara XML avulsa, reproduzindo resultado idêntico ao mesmo sistema via MDB de teste; Caso 3 verificado sem deriva J2; assistente Walker Star gera OneWeb-like (18 planos, 87.9°, RAAN 0–180°) conferido visualmente no preview 3D contra o filing MCSAT de teste.

---

## WS3 — Ferramenta de geração de máscaras PFD (R5, R6, R7, R27, R28, R29)

**Norma:** Parte C completa — §C1 (máscara = envelope da potência radiada, independe da estratégia de alocação; latitudes sem transmissão = −1000 dBW), §C2.1 (formatos: lat×α×ΔLong ou lat×az×el), §C2.2 (mitigação, ex. GSO arc avoidance), §C2.3.1 (célula a célula: `pfd_i = P_i + G_i − 10log10(4πd²)`, soma co/cross-pol limitada por N_co/N_cross), §C2.4.1/§C2.4.2 (metodologias Opção 1/Opção 2), §C4.1/§C4.2 Tabela 5 (XML de saída), §B5.3 (validação). É o **1º estágio do exame** (§A1.3), a cargo da administração notificante.

**Estado (2026-07-03): parcial — gerador entregue.** `src/mask_generator.py` implementa o motor de envelope (por célula `pfd_i = P_i + G_i(θ) − 10log10(4πd²)`, §C2.3.1, somado sobre os N_co feixes mais fortes), gera **nativamente** tanto a Opção 2 (az/el) quanto a Opção 1 (α×ΔLong) — sem conversão — e serializa o XML §C4.2 (`write_pfd_mask_xml`) com round-trip coberto por teste (`streamlit_app/tests/test_mask_generator.py`); mitigação de arco GSO por desligamento de feixe de boresight (mantendo o leakage de sidelobe) ou corte direto `|α| < α₀` com valor de fill configurável, mais banda de latitude de operação → −1000 dBW (§C1); `SimpleCircularBeamAntenna` agora em uso. Nova página `streamlit_app/pages/F_Mask_Generator.py` (formulário + preview + export + registro do mask). Infra reutilizada: `pfd_mask.py`, `mask_converter.py`, `geometry.py`. **Resta (R7):** apontamento fixo-ao-corpo vs steerable ainda parcial.

**Fases:**

**F1 — Modelo de dados de entrada (R6, R7)** — novo módulo `src/mask_generator/params.py`:
- sistema: faixa (FreqMin/Max MHz), RefBW kHz (§B4.1: alinhada ao Art. 22; a menor quando houver duas), polarização, perdas;
- por feixe: potência máxima na RefBW (P_i), padrão de ganho do satélite (G_i: tabela az/el ou paramétrico), N_co/N_cross máximos simultâneos por célula;
- apontamento (R7): steerable (células fixas na Terra) vs fixo ao corpo (§C2.4 introdução); limites de steering; área de serviço (polígono/faixa de latitude).

**F2 — Restrições operacionais/mitigação (R27)** — `mask_generator/mitigation.py`:
- ângulo de exclusão do arco GSO (α ou X) — reusar `compute_alpha_angle_*` de `geometry.py`;
- máscara de elevação mínima; latitudes min/máx de operação (→ −1000 dBW, §C1);
- regras de desligamento/redução de feixes dentro da zona (§C2.2).

**F3 — Motor de envelope (R5, R28)** — `mask_generator/envelope.py`:
- grade da máscara: latitude do subsatélite × (α, ΔLong) [Opção 1, §C2.4.1 Steps 1–8, varredura iso-α] ou × (az, el) [Opção 2, §C2.4.2];
- para cada ponto: máximo de `Σ pfd_i` sobre **todas as combinações permitidas** de feixes ativos × potência × polarização, respeitando N_co/N_cross e as regras da F2 (§C2.3.1 + §C2.4);
- vetorizar com NumPy (grade típica ~10⁴–10⁵ células × combinações); validar contra caso analítico de 1 feixe nadir.
- **Sanidade cruzada:** carregar a máscara gerada no motor Parte D (já existente) e comparar EPFD com cenário de referência.

**F4 — Formatos e saída (R29)** — `mask_generator/xml_writer.py`:
- conversão obrigatória para um dos 2 formatos de §C2.1 (reusar `mask_converter.py`);
- serializador XML conforme §C4.1/§C4.2 Tabela 5: raiz `<satellite_system ntc_id sat_name>`, header `<pfd_mask mask_id low_freq_mhz high_freq_mhz refbw_khz type a_name b_name c_name>`, grades `<by_a>/<by_b>/<pfd c=…>`; um mask por arquivo;
- round-trip: XML gerado → `PFDMaskXML` → grade idêntica (teste automatizado);
- validação §B5.3.

**F5 — UI** — página "Mask Generator": formulário F1+F2, preview (reusar heatmap do Constellation Viewer / Mask Viewer), export XML, registro do mask no storage para uso imediato em runs.

**Aceite:** gerar máscara de sistema-exemplo (1 plano, feixes circulares), validar round-trip XML, rodar Parte D com ela e obter CCDF coerente; caso de mitigação GSO-arc mostra "vale" na região α<α₀.

---

## WS4 — Exportação de artefatos de resultado (R9–R16)

**Norma:** o **conteúdo** é normativo — CDF (§D7.3.3, §D5.1.6: dois arrays epfd × %tempo), PDF/histograma (§D7.1.1, bins 0,1 dB §D1.4), geometria WCG (§D3); os **formatos** (zip/csv/png/pdf/parquet) são extra-normativos (boa prática).

**Estado (2026-07-03): ✓ concluído.** `streamlit_app/lib/result_artifacts.py` (`write_run_artifacts`) grava, ao fim de cada run e junto do `sim_data.json`: `ccdf_epfd.csv`, `epfd_histogram.csv` (a partir de `duration_per_bin` do `EPFDStreamAccumulator`), `epfd_timeseries.csv` (`.csv.gz` acima de ~50k pontos), `geometries.csv`, `table17.csv`, `ccdf.png`, `histogram.png` e `map.png` — todos com cabeçalho de unidades (A2.1 Tabela 1) e em modo *best-effort* (erro de plotagem não derruba o run; matplotlib `Agg`, sem kaleido/Chrome). Fiação nos workers `s1503_worker.py`/`s1588_worker.py`. O botão "Download all (.zip)" (R9) está em `streamlit_app/pages/8_Results.py`.

**Tarefas (majoritariamente em `streamlit_app/lib/exports.py` + workers):**
1. **R11 `ccdf_epfd.csv`** — serializar `ccdf_bins_db`/`ccdf_pct` (2 colunas, header com unidades `dBW/m²/<RefBW> kHz`, `% time exceeded`). *P*
2. **R13 `epfd_histogram.csv`** — workers passam a gravar `duration_per_bin` não-vazio do `EPFDStreamAccumulator` (bin_start_db, bin_end_db, duration_s, probability). Hoje o histograma morre em memória. *P*
3. **R10 geometrias** — `geometries.csv` (measure_id=`{run_id}:{per_point.index}`, es_lat, es_lon, gso_lon, max_epfd) + `map.png` estático (matplotlib, pontos sobre costa — sem dependência de browser). *P/M*
4. **R12/R14 imagens** — `ccdf.png` + `histogram.png` nos workers (matplotlib, sem Plotly/kaleido para não depender de Chrome); opcional `.pdf` vetorial pelo mesmo `savefig`. *P*
5. **R15/R16 série temporal** — expor `keep_full_history`/trace decimado nos workers: `epfd_timeseries.csv` (t_s, epfd_db, duration_s) com aviso de decimação no header; opção `.parquet` (pyarrow já é dependência via orbit_tracks) e `.csv.gz` para séries completas. Nota: decimação não fere a norma (estatística é acumulada sobre todos os passos). *M*
6. **R9 pacote `.zip`** — botão "Download all (.zip)" em `8_Results.py` (`shutil.make_archive` do diretório do run, incluindo os novos CSVs/PNGs). *P*

**Aceite:** run novo produz `params.json, sim_data.json, summary.json, ccdf_epfd.csv, epfd_histogram.csv, geometries.csv, ccdf.png, histogram.png, map.png [, epfd_timeseries.*]` e o zip agrupa tudo; CSVs com unidades no header.

---

## WS5 — Relatório-resumo e tabelas normativas (R17–R26)

**Norma:** §D7.3 = *statement* Pass/Fail (§D7.3.1) + **Tabela 17** por ponto de especificação (Ji dB(W/(m²·BWref)), Pi, Pass/fail, Py) (§D7.3.2) + tabela de CDF (§D7.3.3); §D7.2 (background: diâmetro da antena, padrão de referência, tabela de limites); §D2.1 (run = Direction/Service/Frequency/ES_DishSize/Ref_BW); A2.1 Tabela 1 (unidades).

**Estado (2026-07-03): ✓ concluído.** `ComplianceResult.table17` (`src/epfd_calculator.py`) materializa a Tabela 17 real — uma linha por ponto de especificação do Art. 22 `[Ji, Pi, Py, pass]` — e a exporta no `sim_data.json`; `streamlit_app/lib/report.py` (`summary_html`/`write_summary_html`) monta o `summary.html` normativo (statement §D7.3.1 + Tabela 17 §D7.3.2 + CDF §D7.3.3 + background §D7.2, com a imagem CCDF embutida); `streamlit_app/lib/exports.py` (`run_to_xlsx`) gera o XLSX estilo ITU com as abas `run_def`/`result_def`/`results`/`cdf`/`pdf`; identificação e proveniência (`ntc_id`, `sat_name`, `mask_id`/source, `epfd_type: "down"`, `input_source: "mdb"|"manual"`) entram no bloco `sim_data['identification']` (R18/R21/R26). Botões XLSX e Download-all `.zip` em `streamlit_app/pages/8_Results.py`.

**Tarefas:**
1. **R22 núcleo — Tabela 17 real** — estender `ComplianceResult` (`src/epfd_calculator.py`): já compara por ponto; materializar a lista `[(Ji, Pi, Py_simulado, pass/fail)]` por ponto de especificação do Art. 22 (não só percentis fixos + worst_margin) e exportar em `sim_data.json`. Comparação de Jmax com o limite de 100% (§D7.1.3). *M*
2. **R17 relatório `summary.html`** — template Jinja2 (sem dependência de browser; `.pdf` opcional via weasyprint depois) com a estrutura §D7.3: statement (§D7.3.1) → Tabela 17 (§D7.3.2) → CDF (tabela + imagem do WS4) →  background §D7.2. Gerado pelo worker ao fim do run. *M*
3. **R18** — incluir no relatório e em `sim_data.json`: `ntc_id`, `sat_name` (**bug atual: sat_name ausente nos artefatos da UI** — propagar do `SRSNonGeoSystem`), `mask_id`, tipo de máscara (campo explícito, não extensão do arquivo), `frequency_run_mhz`. *P*
4. **R19/R20** — já exportados (bloco `article22` + `dual_time_step`); apenas incluir no relatório. *P*
5. **R21** — campo explícito `epfd_type: "down"` (equivalente a Direction/Service §D2.1) em `sim_data.json`/`summary.json`. *P*
6. **R23 unidades** — passe de revisão: legenda de unidades (A2.1 Tabela 1) no rodapé de cada CSV/relatório; corrigir percentis (dict com valores sem unidade) e aba `params` do xlsx de campanha. *P*
7. **R24 xlsx estilo ITU** — nova função em `exports.py`: `run_to_xlsx` com abas `run_def` (params do run, §D7.2/§D2.1), `result_def` (limites Ji/Pi), `results` (statement + Tabela 17), `cdf` (arrays), `pdf` (histograma do WS4-2). Espelha o formato EPFDResults da BR → comparação direta com `mdb_results.py`. *M*
8. **R25 CSVs** — as mesmas 5 tabelas como CSVs separados (reusa serializadores do WS4). *P*
9. **R26 proveniência** — campo `input_source: "mdb" | "manual"` + paths/hashes em `params.json` e no relatório (princípio de transparência §F2/§E3; vira relevante com WS2). *P*

**Aceite:** run novo gera `summary.html` com os 3 blocos de §D7.3 legíveis; `run_to_xlsx` abre no Excel com as 5 abas; diff campo a campo contra um EPFDResults MDB da ITU do mesmo cenário de teste (docs/test_data) sem divergência estrutural.

---

## Ordem sugerida (marcos)

| Marco | Conteúdo | Racional | Status |
|-------|----------|----------|--------|
| M1 | WS4 (1,2,4,6) + WS5 (3,4,5,6,9) | Quick wins: dados já existem, só serialização — ~10 requisitos saem de Parcial | ✓ concluído |
| M2 | WS5 (1,2,7,8) | Tabela 17 + relatório + xlsx ITU — fecha o bloco D7.3 | ✓ concluído |
| M3 | WS1 | Prioridade 0 da planilha; corrige superestimação de EPFD em filings `M` | Parcial (leitura + badge + seleção por config) |
| M4 | WS2 | Prioridade 1; habilita estudos paramétricos e é pré-requisito prático p/ WS3 | ✓ concluído |
| M5 | WS3 (F1→F5) | Maior esforço; entrega a Parte C completa | Parcial (gerador entregue; R7 parcial) |
| M6 | WS4 (3,5) | Geometrias/série temporal completos (dependem de decisões de volume) | ✓ concluído (geometries.csv/map.png + epfd_timeseries.*) |

## Riscos e observações transversais

- **Tabela 17 exige os pontos de especificação do banco de limites** (pares Ji/Pi do Art. 22) — conferir cobertura em `src/article22_tables.py` para todas as tabelas usadas antes do WS5-1.
- **WS3 é o único item com física nova** (síntese de PFD); todo o resto é plumbing de dados. Validar F3 cedo contra caso analítico.
- **Imagens nos workers**: usar matplotlib `Agg` (headless); evitar kaleido/Chrome.
- **Volume (R15/R16)**: séries completas de runs de 10⁸ passos são inviáveis — manter decimação como padrão documentado e série completa opt-in com estimativa de tamanho na UI.
- **Dataset multiconfig**: para validar WS1 de ponta a ponta, completar o scrape das configs faltantes (9 notices incompletos identificados em 2026-05).
- Requisitos de formato marcados *Extra-normativo* na planilha não bloqueiam conformidade S.1503-4 — priorizar sempre o conteúdo normativo (Tabela 17, CDF, PDF, unidades) sobre o contêiner.
