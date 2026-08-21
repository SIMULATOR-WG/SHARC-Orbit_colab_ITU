# Alpha tables de teste (WP 4A Doc 4A/312)

Tabelas α declaradas (`min` / `max`) para exercitar a estratégia de seleção
`alpha_table` e o envelope de 7 tabelas. Cada arquivo declara só os dois pares
de listas — nada de configuração de constelação — então serve tanto como
`--alpha-table-file` quanto colado direto em `non_gso.alpha_table` de um YAML
de configuração.

## Formato

```yaml
min:
  - [angle_deg, cumulative_probability]   # CDF: P(α ≤ angle)
  - ...
max:
  - [angle_deg, cumulative_probability]
```

É uma **CDF**, não uma CCDF: `prob` é a fração de tempo em que o α topocêntrico
do satélite ativo em relação ao arco GSO é **≤** `angle`. Ângulos estritamente
crescentes em [0, 180); probabilidades não-decrescentes em (0, 1]. A visão
complementar "% do tempo em que α é excedido" (Doc 4A/497) é assunto de
plotagem — `1 − CDF` — e é rejeitada na entrada.

`min` e `max` são as duas tabelas que **limitam o envelope**; a geração produz 7
(`min`, `mid25`, `mid50`, `mid75`, `max` por interpolação linear, mais as
diagonais `MinMax` e `MaxMin`, em que o fator de mistura varia ao longo de α).
Convenção usada aqui: `min` = distribuição deslocada para α pequeno, `max` =
deslocada para α grande.

### Os casos TSS são os intervalos declarados

Doc 4A/312 p. 110: para `n` pares declarados a tabela TSS tem exatamente `n + 1`
**casos**, delimitados pelos próprios ângulos declarados, e o último é **aberto**:

```
[0, α₁)      TSS₁   += Nco[lat]·p₁
[α₁, α₂)     TSS₂   += Nco[lat]·(p₂ − p₁)
…
[αₙ, ∞)      TSSₙ₊₁ += Nco[lat]·(1 − pₙ)
```

Essa granularidade é normativa, não um detalhe de implementação: o Step 20
compara o TSS **por caso**, então um caso largo compete com a sua massa inteira.
Uma tabela `[(1, 0.02), (20, 0.20), …]` dá `TSS = Nco·0.02` ao caso `[0, 1)` e
`Nco·0.18` ao caso `[1, 20)` — o segundo ganha.

`simulation.alpha_bin_deg` (default **0**) subdivide cada caso em sub-bins
uniformes. É **não-normativo** e muda o resultado: subdividir `[1, 20)` em 19
sub-bins deixa cada um com `Nco·0.0095`, e aí o caso estreito `[0, 1)` passa a
ganhar — a decisão **inverte**. Use só para estudo de sensibilidade; o engine
emite `WARNING` quando o valor é positivo.

### Os dois limites não precisam começar (nem terminar) no mesmo ângulo

O exemplo canônico do próprio documento (p. 16) tem `min` em 20°–50° e `max` em
30°–60° — o descasamento **é** o envelope. Na grade-união cada tabela é avaliada
fora do seu span declarado, e as duas extrapolações são descartadas:

- `p = 0` na frente (abaixo do primeiro ângulo declarado da tabela) — o primeiro
  caso já vai de 0° até esse ângulo, então o par não acrescenta nada;
- repetições de `pₙ` no fim (acima do último ângulo declarado, onde a
  interpolação segura o valor) — mantê-las inventaria uma afirmação que o filing
  não fez e deslocaria o caso de cauda aberto.

Com isso os extremos do envelope (fatores 0 e 1) voltam a ser **exatamente** as
duas tabelas declaradas. Ver [08_offset_first_angles.yaml](08_offset_first_angles.yaml)
e [09_doc312_canonical.yaml](09_doc312_canonical.yaml).

## Como rodar

Passando o arquivo (sobrepõe o que estiver no config):

```bash
python -m src.main --config seu_config.yaml \
  --selection-strategy alpha_table \
  --alpha-table-file examples/alpha_tables/09_doc312_canonical.yaml
```

Ou declarando tudo no YAML de configuração e rodando sem flag nenhuma:

```yaml
non_gso:
  alpha_table:
    min: [[1.0, 0.50], [3.0, 0.80], [8.0, 0.95]]
    max: [[1.0, 0.02], [20.0, 0.20], [50.0, 0.60], [85.0, 0.95]]
simulation:
  selection_strategy: alpha_table
  alpha_bin_deg: 0        # 0 = casos declarados (normativo). >0 é não-normativo.
  alpha_envelope_jobs: 7  # processos do envelope; 1 volta ao modo sequencial
```

O `--alpha-table-file` aceita YAML ou JSON (YAML é superconjunto de JSON).

### Inspecionar antes de rodar

O [plot_alpha_tables.py](plot_alpha_tables.py) desta pasta lê o mesmo formato e
plota a família de 7 tabelas sem depender do motor — útil para conferir a forma
da CDF declarada antes de gastar uma simulação:

```bash
python examples/alpha_tables/plot_alpha_tables.py \
  examples/alpha_tables/03_wide_envelope.yaml --save family.png --ccdf
```

## As tabelas

| Arquivo | Regime | O que testa |
|---|---|---|
| [00_reference_low_alpha.yaml](00_reference_low_alpha.yaml) | massa em α pequeno | Controle. Concorda com o Step 20 normativo — pouca ou nenhuma divergência esperada |
| [01_high_alpha_stress.yaml](01_high_alpha_stress.yaml) | massa em α grande | **Teste decisivo.** Se a CCDF não se mexe aqui, a seleção não morde no cenário |
| [02_uniform.yaml](02_uniform.yaml) | α uniforme em [0°, 90°] | Quota vira round-robin entre casos; base limpa para varrer `alpha_bin_deg` |
| [03_wide_envelope.yaml](03_wide_envelope.yaml) | min e max distantes | Espalhamento do envelope e qual tabela fica vinculante (incl. as diagonais) |
| [04_narrow_envelope.yaml](04_narrow_envelope.yaml) | min ≈ max | Envelope deve colapsar; verifica independência das 7 sub-execuções e o desempate |
| [05_operator_realistic.yaml](05_operator_realistic.yaml) | joelho em torno de α₀ | Muitos pares ⇒ muitos casos estreitos; sensível a `alpha_bin_deg` |
| [06_single_pair_tail.yaml](06_single_pair_tail.yaml) | 1 par por tabela | Caminho da massa de cauda `(1 − pₙ)` e a grade-união de ponto único (só 5 tabelas distintas) |
| [07_fractional_angles.yaml](07_fractional_angles.yaml) | ângulos fracionários | Bordas de caso fora de grade inteira; interação com `alpha_bin_deg` |
| [08_offset_first_angles.yaml](08_offset_first_angles.yaml) | spans deslocados | Descarte das extrapolações da grade-união (era `invalid/mismatched_first_angle.yaml`) |
| [09_doc312_canonical.yaml](09_doc312_canonical.yaml) | exemplo do Doc, p. 16 | **Referência de conformidade.** Spans deslocados nas duas pontas, cauda de massa zero |
| [invalid/ccdf_instead_of_cdf.yaml](invalid/ccdf_instead_of_cdf.yaml) | inválida | Deve ser rejeitada: probabilidade decrescente (CCDF passada como CDF) |

## O que olhar no resultado

A linha por sub-execução no log traz o essencial:

```
[PASS] min     margin=+3.21 dB  TSS_max=412.0 (per step 0.14, peak 412.0, WCG-1storbit hits 0)
```

- **As 7 margens idênticas entre si** → o conteúdo da tabela é irrelevante para
  o resultado; a seleção não está mordendo. Duas causas dominam:
  1. o pool elegível por passo é ≤ Nco — compare `num_contributing_sats` com Nco;
  2. **os ângulos declarados não cobrem a faixa de α que a geometria produz**, e
     aí todo mundo cai no caso de cauda único `[αₙ, ∞)`. Confira o painel de
     `min_alpha_deg` antes de concluir qualquer coisa.
- **`TSS_max` crescendo ~linearmente com os passos** (`per step` longe de zero)
  → resíduo de crédito não gasto. Se cresce em todos os casos proporcionalmente
  às massas, o pool está curto; se cresce só nos casos de α alto, a geometria não
  fornece aqueles α e a tabela declarada é infactível para essa constelação.
- **Tabela vinculante = `MinMax` ou `MaxMin`** → a mistura dependente de α é
  pior que os dois extremos. É o caso não-óbvio para o qual as diagonais
  existem; vale reportar.

O WCG de entrada única, o agregado em t=0 e o Δt/nsteps são **independentes da
estratégia por construção** — não adianta comparar esses números entre uma
execução normativa e uma com `alpha_table`. Só a CCDF temporal diverge.

### A CCDF do `alpha_table` NÃO é limitada pela do caso normativo

Versões anteriores deste arquivo afirmavam que o `alpha_table` nunca pode
exceder o `s1503`, porque "qualquer subconjunto de tamanho ≤ Nco soma ≤ o
top-Nco". **Isso só vale quando o Step 21 é inerte.** Com
`MIN_ANGLE_AT_ES > 0` — cujo exemplo na tabela de campos do Doc 4A/312 p. 16 é
**5°** — a poda depende da ordem de escolha: escolher primeiro um satélite de
epfd menor pode podar menos vizinhos e liberar mais slots. Contra-exemplo
verificado (Nco=3, MIN_ANGLE_AT_ES=4°, A a 3° de B/C/D e B/C/D a 5,2° entre si):

```
epfd = [A=10, B=6, C=6, D=6]
s1503      : pega A, poda B/C/D          -> soma 10
alpha_table: pega B, poda A, pega C e D  -> soma 18   (+2.55 dB)
```

O próprio documento admite isso na nota do Step 21 ("Step 21 can remove
satellites from the TSS table…"), sem discutir a implicação. Então uma CCDF
`alpha_table` acima da normativa **não é**, por si só, sinal de bug — confirme
primeiro se `min_angle_at_es_deg > 0`.
