# Auditoria de cobertura — epfd↓ vs Rec. ITU-R S.1503-4 (09/2023)

**Data:** 2026-07-08 · **Branch:** `feat/requisitos-io` · **Método:** checklist de 67 requisitos normativos extraído da Parte D (texto em `docs/s1503-4/content.md`) cruzado com auditoria do código em `src/`.

**Escopo:** apenas o motor **epfd↓ (downlink)** — §D2 (runs), §D3.1 (WCG-down), §D4 (passo de tempo), §D5.1 (algoritmo), §D6 (geometria/órbita), §D7.1 (estatística/veredito). Fora do escopo desta auditoria: epfd↑ (§D5.2), epfd-IS (§D5.3), geração de máscara (Parte C — ver `PLANO_IMPLEMENTACAO_REQUISITOS.md`, WS3).

**Resultado (atualizado 2026-07-08):** motor ~97% completo. §D5.1.4.2 (janela deslizante) **implementado**. Restam: 1 bloco fora de escopo provável (9.7A/9.7B) e 2 itens de verificação/infra.

---

## 1. Lacunas

### 1.1 §D5.1.4.2 — Algoritmo de janela deslizante (MIN_DURATION ≠ 0) — **IMPLEMENTADO (2026-07-08)**

Quando a tabela `sat_oper` do SRS declara `MIN_DURATION[lat] != 0` em qualquer banda, a norma exige a variante com janelas deslizantes (§D5.1.3: N_SW, MIN_SLIDING_TIME, N_MSL, N_TW; §D5.1.4.2 steps 19–22: satélites elegíveis durante a janela inteira, ordenação pelo pico de epfd↓ na janela, estatística por conjunto de janela).

**Implementação:**
- `src/time_step.py` — `compute_track_duration_windows` + `TrackDurationWindows` (parâmetros §D5.1.3).
- `src/epfd_stream_accumulator.py` — `EPFDWindowStats` (histograma slim por conjunto de janela).
- `src/epfd_calculator.py` — `run_epfd_simulation_windowed` (motor), `_simulate_window_set` (worker picklable por conjunto), `_process_closed_window` (agregação Steps 19–22), `_build_worst_envelope` (envelope pior-caso para veredito go/no-go), modo `per_sat_out` em `_accumulate_epfd_visible_satellites`.
- `src/srs_reader.py` — guard virou dado: `read_sat_oper_min_duration`.
- `src/main.py` — `run_wcg_downlink` detecta MIN_DURATION na latitude da ES e roteia para a variante (dual time step desligado, pois a variante é definida em passos finos).
- **Standalone × cluster**: paralelismo é sobre conjuntos de janela independentes (sem estado cruzado) → standalone, Pool e executor Ray produzem estatística bit-idêntica. Coberto por teste (`streamlit_app/tests/test_track_duration_s1503.py`), incluindo a degeneração N_SW=1 ≡ run padrão §D5.1.4.1.
- **UI**: input MIN_DURATION em `3_Single_entry.py` (expander 7); bloco + overlay por janela em `8_Results.py`.

**Fora de escopo (falha ruidosa preservada):** path agregado/S.1588 (`s1588_worker.py` levanta `NotImplementedError` para filings com MIN_DURATION ≠ 0).

### 1.2 §D2.2/§D2.3 — Runs 9.7A/9.7B — **NÃO IMPLEMENTADO (escopo)**

Runs disparados por coordenação (Apêndice 5): dish size/padrão de ganho vindos do filing da ES, threshold/RefBW do Apêndice 5, `FrequencyRun = max(ES_fmin, Mask_fmin) + RefBW/2`. Zero código. É função de exame da BR — provavelmente fora do escopo do TED, mas registrado como parte da norma.

### 1.3 §D3/Fig. 13 — Ajuste de longitude do WCG — **VERIFICADO E CORRIGIDO (2026-07-08)**

O ajuste estava implementado fora do `wcg_search.py`: `src/s1503_figure13_wcg_lon.py` (`apply_s1503_figure13_wcg_longitude_adjustment`), chamado em `run_wcg_downlink` após a resolução do passo fino, com o satélite dominante do WCGA (`best_overall_idx`). Varredura da 1ª órbita no passo fino, escolhe o instante de latitude mais próxima (desempate: t menor), gated off para WCG manual — tudo conforme §D3.

**Defeito encontrado na verificação: sinal da correção invertido.** O código aplicava `corr = lon_pm − lon_full` ao ES/GSO — movia o par *para longe* do cruzamento real, dobrando o desvio em vez de anulá-lo (pico de EPFD potencialmente subestimado em runs curtos; não-conservador). Confirmado por teste empírico do invariante da norma (`lon_full − es_after == lon_pm − es_before`) e corrigido para `corr = lon_full − lon_pm`. Regressão travada em `streamlit_app/tests/test_figure13_wcg_lon.py`.

### 1.4 Validação automatizada contra referência BR — **PARCIAL**

Dados de referência existem (`docs/test_data/EPFDRESULTS_*.MDB`, fixtures MCSAT) e a UI faz overlay de CCDF (`streamlit_app/lib/mdb_results.py`), mas não há suite de regressão automática (pass/fail em CI) comparando o motor local contra os resultados da ITU/BR.

### 1.5 Fora do motor, afeta epfd↓ de filings `M`

Execução automática de N runs por `orbit_set_id` + consolidação do pior caso entre configs (§D2.1) — WS1 tarefas 4–5 do plano. Hoje simula-se uma config selecionada por vez. **Maior impacto prático pendente.**

---

## 2. Mapa do implementado (18 pontos auditados, com âncoras de código)

| # | Recurso | Norma | Código | Status |
|---|---------|-------|--------|--------|
| 1 | Passo de tempo automático (beamwidth ES / velocidade angular) | §D4.2 | `src/time_step.py:84,196`; `src/main.py:1301` | ✓ (override por config) |
| 2 | Duração do run / nº de passos (repetitivo × não-repetitivo) | §D4.6 | `src/time_step.py:300,346`; `src/main.py:2184` | ✓ |
| 3 | Dual time step (loop coarse/fine real, região crítica) | §D4.7.1, D5.1 sub-steps 6.1–6.3 | `src/time_step.py:512,560`; `src/epfd_calculator.py:~800` | ✓ |
| 4 | Redução N'hit quando Nsteps > 1e8 (não-repetitivo) | §D4.1 | `src/time_step.py:29-50` | ✓ |
| 5 | Propagação J2 secular (Ω̇, ω̇, Ṁ, eqs. 20–22), analítica | §D6.3.2–3 | `src/orbit_propagator.py:41,64,354` | ✓ |
| 6 | Casos orbitais 1/2/3 (Caso 3: ω fixo, M=M0+n0·t, Ω̇ admin) | §D6.3.6 | `src/orbit_propagator.py:280,305,307,332` | ✓ |
| 7 | Station-keeping Wdelta (offset −Wδ→+Wδ ao longo do run) | §D6.3.4 | `src/orbit_propagator.py:390`; `src/main.py:2326` | ✓ (toggle, default OFF) |
| 8 | Precessão artificial (espaçamento de passes, não-repetitivo) | §D6.3.5, D4.6.2 | `src/time_step.py:200`; `src/main.py:1235,2200` | ✓ |
| 9 | Padrão de antena ES — S.1428 (FSS) | §D6.5 | `src/antenna.py:49-80` (D/λ ≥ 20 imposto) | ✓ |
| 10 | Padrão de antena ES — BO.1443 (BSS) | §D6.5 | `src/antenna.py` (grep `1443`) | ✓ |
| 11 | Off-axis: ES rastreia GSO; θ entre ES→GSO e ES→NGSO | §D5.1.4.1 step 15 | `src/epfd_calculator.py:578`; `src/geometry.py:424` | ✓ |
| 12 | Lookup de máscara PFD: bilinear (α,ΔLong)/(az,el), plano de latitude mais próximo | §D5.1.5 | `src/pfd_mask.py:101,448,480` | ✓ |
| 13 | Agregação por passo: Steps 18–23 (zona de exclusão α₀/ε₀ + condição OR, MAX_CO_FREQ, MIN_ANGLE_AT_ES, sidelobes, soma linear) | §D5.1.4.1 | `src/epfd_calculator.py:370,450,468,745,760`; `src/epfd_calculator.py:244` (MIN_ANGLE) | ✓ (toggle `disable_or_condition` p/ emulação S.1503-2) |
| 14 | Correção de RefBW (limite × máscara) | §D2.1/§D5 | `src/main.py:1554-1559` | ✓ |
| 15 | Estatística: bins 0.1 dB, pesos de passo coarse (`duration_s`), CCDF, percentis | §D7.1.1–2 | `src/epfd_stream_accumulator.py:46,66,111,296` | ✓ |
| 16 | Veredito: Tabela 17 por ponto (Ji, Pi, Py) + Jmax vs limite 100% | §D7.1.3–4, §D7.3.2 | `src/epfd_calculator.py` (`ComplianceResult.table17`) | ✓ |
| 17 | Tabelas Art. 22 down (22-1A…22-1E, notas 22.5C.4/.8) + FrequencyRun por banda | §D2.1 | `src/article22_tables.py:1,50,338,404` | ✓ |
| 18 | WCG-down: varredura de latitude multiprocessada, busca binária nas fronteiras α=±α₀, M-search elíptico, tie-break por velocidade angular | §D3.1 | `src/wcg_search.py:978,1476,2351,2744` | ✓ (exceto item 1.3 acima) |

Aceleração: `alpha_method ∈ {sweep, analytical}` (`src/geometry.py:59,720` — Numba paralelo; analítico ~20×), paralelismo hierárquico processos×threads (`src/wcg_search.py:128`).

---

## 3. Checklist normativo usado (resumo por bloco)

67 itens extraídos do texto da Recomendação. Status agregado:

| Bloco | Itens | Status |
|-------|-------|--------|
| §D2 geração de runs (Art. 22, dedup, FrequencyRun; 9.7A/B) | 1–4 | ✓ exceto 9.7A/B (item 1.2) |
| §D3.1 WCG-down (espaço de busca, grades θ/φ, inclusão, tie-break, fronteiras, conversões, ajuste de longitude, tolerâncias) | 5–17 | ✓ exceto ajuste de longitude (item 1.3) |
| §D4 passo de tempo (Δtref, φ, Nhit=16, multi-shell, equatorial/repetitivo/não-repetitivo, redução 1e8, dual step) | 18–27 | ✓ completo |
| §D5.1 algoritmo (parâmetros, seleção de variante, janelas, loop, posições, visibilidade, máscara, θ, GRX, single-entry, Steps 18–24, CDF) | 28–51 | ✓ exceto variante §D5.1.4.2 (item 1.1) |
| §D7.1 veredito (comparação por ponto, Jmax 100%, decisão global, arrays de saída, bins) | 52–56 | ✓ completo |
| §D6.3 modelo orbital (J2, mapeamento SRS §D6.3.7, conversão cartesiana, Casos 1/2/3, Wdelta, precessão forçada) | 57–64 | ✓ completo |
| §D6.4 geometria (α e sinal — incl. hemisfério sul, ΔLong, visibilidade do arco, az/el) | 65–67 | ✓ completo |

Checklist integral (67 itens com referência de seção): reproduzível a partir de `docs/s1503-4/content.md` (Parte D). Auditoria de código: 2026-07-08.

---

## 4. Recomendações (ordem sugerida)

1. **WS1 tarefas 4–5** (N-por-config, §D2.1) — prioridade 0 da planilha; corrige EPFD superestimado em filings `M`.
2. **Regressão automática** contra `docs/test_data/EPFDRESULTS_*.MDB` — barato, protege tudo acima.
3. **Item 1.3** (ajuste de longitude do WCG, Fig. 13) — ✓ verificado e corrigido (sinal invertido; regressão em `test_figure13_wcg_lon.py`).
4. **§D5.1.4.2** — ✓ implementado (2026-07-08). Validar contra filing real com `MIN_DURATION != 0` quando disponível no dataset (hoje coberto por testes sintéticos + degeneração vs run padrão).
5. **9.7A/9.7B** — só se o TED exigir exame completo BR-style.
