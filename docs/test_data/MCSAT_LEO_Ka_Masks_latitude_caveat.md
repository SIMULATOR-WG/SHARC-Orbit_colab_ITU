# MCSAT_LEO_Ka — interpretação da máscara PFD e *caveat* de cobertura em latitude

**Arquivos de referência (mesma pasta):**

- `MCSAT_LEO_Ka_Masks.mdb` — máscaras (tabela `masks`)
- `MCSAT_LEO_Ka_SRS.mdb` — dados do filing (tabelas `orbit`, `sat_oper`, `mask_lnk1`)

**Filing:** `ntc_id = 101`, `sat_name = "EPFD3 (OneWeb)"` — constelação LEO Ka, 18 planos × 43 satélites (774 sats), altitude 1200 km, **inclinação i = 87,9°** (quase-polar).

**Norma:** ITU-R S.1503-4 (09/2023), §D5.1.5 (aplicação da máscara) e Anexo C / §C4.1 (geração da máscara).

> **Resumo:** a interpretação da máscara pelo motor está **correta e conforme** a S.1503-4. A única peculiaridade é que a própria máscara do filing **não cobre toda a faixa de latitudes** que a constelação ocupa; o motor trata as latitudes não cobertas por *clamp* de vizinho mais próximo, conforme a norma. Esse caso de borda — filing subespecificado + *clamp* literal — é a raiz da divergência observada com o software de referência do BR (S.1503-2) no cenário OneWeb.

---

## 1. Estrutura verificada da máscara

A tabela `masks` contém três máscaras para o `ntc_id 101`:

| `mask_id` | `f_mask` | `f_mask_type`          | elemento XML   | uso                |
|:---------:|:--------:|------------------------|----------------|--------------------|
| 1         | `S`      | *(vazio)*              | ≠ `<pfd_mask>` | "other" (ignorada no EPFD↓) |
| 2         | `E`      | *(vazio)*              | ≠ `<pfd_mask>` | EIRP (ignorada no EPFD↓) |
| **3**     | **`P`**  | **`alpha_deltaLongitude`** | **`<pfd_mask>`** | **PFD — usada no EPFD↓** |

O blob da coluna `mask` é um **ZIP** (cabeçalho `PK\x03\x04`) contendo o XML interno (`mask ntc_id 101 mask_id N.xml`).

**Raiz do XML da máscara PFD (mask_id 3):**

```xml
<pfd_mask c_name="deltaLongitude" b_name="alpha" a_name="latitude"
          type="alpha_deltaLongitude"
          high_freq_mhz="19300" low_freq_mhz="17800"
          mask_id="3" refbw_khz="40">
```

**Eixos tabulados:**

| Eixo | Nome             | Valores                                                              |
|------|------------------|----------------------------------------------------------------------|
| *a*  | `latitude`       | **{±38, ±40, ±44, ±48,5}** — apenas 8 tabelas                        |
| *b*  | `alpha`          | **−67 … +67** (assinado); fino (0,5°) perto de 0, grosso (10°) nas caudas |
| *c*  | `deltaLongitude` | −30 … +90 (grade esparsa por célula)                                 |

- Largura de referência: **40 kHz**. Banda: **17800–19300 MHz** (Ka *downlink*).
- Estrutura: `<by_a a="lat"> <by_b b="alpha"> <pfd c="dlon">valor</pfd> … </by_b> … </by_a>`.

---

## 2. Pipeline de interpretação (código do motor)

| Etapa | Função / arquivo | Verificação |
|-------|------------------|-------------|
| Blob ZIP → XML interno | `read_pfd_mask_xml_from_mdb` — `src/srs_reader.py` | ✓ extrai, decodifica, exige `<pfd_mask>` |
| Parse do XML (tipo/eixos/refbw) | `load_pfd_mask_from_xml_content` — `src/pfd_mask.py` | ✓ `type`, `a/b/c_name`, `refbw_khz` lidos do **próprio XML** |
| Seleção do `mask_id` (filtro `f_mask="P"`) | `_resolve_mask_lnk1_assignments` — `src/main.py` | ✓ 18 órbitas → **mask 3**; S/E excluídas |
| Tabela de latitude (vizinho mais próximo, sem interp) | `_interp_nearest_lat_bilinear_batch` — `src/pfd_mask.py` | ✓ |
| Bilinear em (α, Δlong) | idem | ✓ |
| *Edge-clamp* (fora da faixa = última válida) | `_prepare_axis` — `src/pfd_mask.py` | ✓ |

> **Defesa contra máscara errada:** se a `mask_id` 1 (`S`) ou 2 (`E`) fosse selecionada por engano, `read_pfd_mask_xml_from_mdb` levanta `ValueError` ("não contém `<pfd_mask>`"). Não há uso silencioso da máscara errada.

### Evidência empírica (engine × XML cru)

```
1) Pontos exatos da grade (7/7):           engine ≡ XML  ✓
2) Nearest-latitude:    sub-lat 46 → tabela 44 ; 47/60/87,9 → 48,5 ; <38 → 38   ✓
3) Edge-clamp:          α>67 → α=67 ; Δlon>90 → 90 ; Δlon<−30 → −30             ✓
4) Alpha assinado:      −67 … +67 consultado com sinal                          ✓
5) Bilinear:            α=0,25 = média linear de α=0 e α=0,5                     ✓
6) Seleção:             read_mask_assignment_all(f_mask="P") → {todas: [3]}      ✓
```

Conclusão da Seção 2: interpretação **conforme §D5.1.5 / §C4.1**.

---

## 3. *Caveat* — a máscara não cobre toda a faixa de latitudes da constelação

### 3.1. O que a máscara contém

O eixo de latitude tem apenas **{±38°, ±40°, ±44°, ±48,5°}**. A faixa explicitamente coberta vai de 38° a 48,5° (espelhada no hemisfério sul), **sem nenhuma tabela acima de 48,5°, abaixo de 38°, nem no equador**.

É essencial notar que esse eixo é a **latitude do ponto subsatélite** (projeção do satélite não-GSO no solo), **não** a latitude da estação terrena GSO vítima. Como a constelação tem **i = 87,9°**, o ponto subsatélite percorre **−87,9° a +87,9°**. Portanto, para boa parte das latitudes que os satélites realmente ocupam (de 48,5° até 87,9°, em cada hemisfério), **a máscara não tem tabela própria**.

### 3.2. O que a norma pede

A S.1503-4 (Anexo C / §C4.1 na geração; §D5.1.5 na aplicação) recomenda que o operador forneça a máscara PFD cobrindo **toda a faixa de latitudes alcançada pela constelação**, idealmente até **±i** (aqui, ±87,9°). Para latitudes onde o sistema **não transmite**, deve-se declarar uma PFD **muito baixa** (um piso), de modo que não contribuam ao EPFD agregado. **O filing OneWeb não cumpre isso** — entrega tabelas só até ±48,5°.

### 3.3. Como o motor trata as latitudes fora da cobertura

A regra normativa (§D5.1.5) é **vizinho mais próximo em latitude, sem interpolação entre tabelas**. O motor aplica isso literalmente, resultando em dois regimes de *clamp* (verificados):

- **Alta latitude (|lat| > 48,5°):** atendida pela tabela de **48,5°** (a de maior latitude disponível). Verificado: subsatélite a 60° e a 87,9° usam a tabela de 48,5° (PFD = −177,1175 dBW em α=0, Δlon=0). A máscara de 48,5° é **estendida** para todas as latitudes mais altas.
- **Gap equatorial (|lat| < 38°):** atendida pela tabela de **±38°** (a de menor latitude). Não há tabela no equador; usa-se a de 38° (mais próxima).

Esse é **exatamente** o comportamento prescrito pela norma para latitude fora da faixa tabelada (repetir a tabela mais próxima, sem extrapolar). **Não há erro de interpretação.**

### 3.4. Por que está correto pela norma, mas diverge do software do BR

A norma é **literal** quanto ao *clamp* (vizinho mais próximo), mas **subespecificada** quanto ao que fazer quando o filing **omite** latitudes altas que a constelação fisicamente ocupa. Dois softwares conformes podem tratar isso de modo diferente:

- **Nosso motor (S.1503-4):** estende a tabela de 48,5° para 60°, 70°, 87,9° etc. (*clamp* literal). Em alta latitude, o satélite passa a "emitir" segundo a tabela de 48,5°, cuja PFD em α pequeno é relativamente alta. Na linha de sub_lat ~44–46° calculamos ≈ −172/−173 dBW.
- **Software do BR (S.1503-2):** pode adotar outra convenção nas latitudes não declaradas (tratar como sem-transmissão, ou interpolar/extrapolar de outra forma), o que desloca a geometria de pior caso. Na mesma linha o BR computa ≈ −171,9 dBW.

Consequência observada no cenário OneWeb: o **WCG do ITU fica em ES ≈ +66°**, o **nosso em ES ≈ −52°**. Como a diferença é **in-band, na magnitude da PFD em alta latitude**, e os intermediários por-satélite do BR não são acessíveis, **não é possível reproduzir bit-a-bit** o resultado do BR sem conhecer a convenção exata que ele aplica às latitudes omitidas pelo filing.

> **Causa-raiz:** não é a interpretação da máscara (conforme), e sim o **filing OneWeb subespecificado** (máscara ±48,5° em vez de ±i) **interagindo com o *clamp* literal** — caso de borda em que ferramentas conformes legitimamente divergem.

### 3.5. Impacto e recomendação

- **Direção do efeito:** estender a tabela de 48,5° é **conservador ou neutro** — o motor repete a última PFD válida, nunca inventa PFD menor. Não há subcontagem silenciosa.
- **Para casar com o BR:** seria preciso (a) um filing com máscara cobrindo ±87,9° (o que o operador deveria ter declarado), ou (b) emular a convenção específica do BR para latitudes não declaradas — **não fixada normativamente** na -4.
- **Diagnóstico prático:** sempre que `mask.lat_range` for mais estreito que ±i da constelação, há *clamp* em alta latitude. Recomenda-se **registrar isso no relatório da run** ("a máscara não cobre as latitudes que os satélites ocupam"), para que o resultado de alta latitude seja lido como *tabela de borda estendida*, não como medição direta.

---

## 4. Como reproduzir as verificações

```python
from src.srs_reader import read_pfd_mask_xml_from_mdb, read_mask_assignment_all
from src.pfd_mask import load_pfd_mask_from_xml_content

F = "docs/test_data/MCSAT_LEO_Ka_Masks.mdb"
xml  = read_pfd_mask_xml_from_mdb(F, mask_id=3, ntc_id="101")
mask = load_pfd_mask_from_xml_content(xml, mask_id=3)

mask.lat_range      # (-48.5, 48.5)  ← mais estreito que ±i = ±87.9
mask.alpha_range    # (-67.0, 67.0)
mask.dlon_range     # (-30.0, 90.0)
mask.refbw_khz      # 40.0

mask.get_pfd(0.0, 87.9, 0.0)   # -177.1175  (clamp → tabela 48.5)
mask.get_pfd(0.0, 60.0, 0.0)   # -177.1175  (clamp → tabela 48.5)

# seleção: só a máscara PFD (f_mask="P")
read_mask_assignment_all("docs/test_data/MCSAT_LEO_Ka_SRS.mdb",
                         ntc_id="101", f_mask_filter="P")
# → {1:[3], 2:[3], …, 18:[3]}
```
