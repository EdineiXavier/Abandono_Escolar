# Painel de BI: Abandono × Evasão Escolar — pipeline de dados

Código Python que baixa, limpa, cruza e organiza dados públicos do INEP e do IBGE para
um painel interativo no Power BI.

- **Abandono:** o aluno deixou de frequentar a escola *durante* o ano letivo
  (INEP, Taxas de Rendimento).
- **Evasão:** o aluno estava matriculado em um ano e *não se matriculou* no ano
  seguinte (INEP, Taxas de Transição; IBGE, PNAD Contínua).

## Estrutura de pastas

```
Abandono_Escolar/
├── README.md
├── requirements.txt
├── scripts/
│   ├── reduzir_censo.py     1. reduz os microdados do Censo Escolar
│   ├── baixar_pnad.py       2. baixa as tabelas da PNAD (API do IBGE)
│   ├── gerar_mapas.py       3. converte a malha do IBGE em TopoJSON
│   ├── explorar_dados.py    4. análise exploratória (opcional, mas é importado pelo 5)
│   └── preparar_dados.py    5. gera as tabelas finais para o Power BI
├── dados/                   arquivos baixados (não precisa descompactar)
├── powerbi/                 saída: tabelas CSV + dicionário + log   (gerada)
├── mapas/                   saída: TopoJSON para o Mapa de formas   (gerada)
└── exploracao/              saída: relatório exploratório           (gerada)
```

## 1. Bases de dados — o que baixar e onde colocar

Tudo vai na pasta `dados/`. Os nomes podem ter prefixos; os scripts procuram por partes do nome.

| Arquivo | Onde baixar | Para que serve |
|---|---|---|
| `tx_rend_escolas_2019.zip` … `2025.zip` (7) | [INEP – Taxas de Rendimento](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/indicadores-educacionais/taxas-de-rendimento-escolar), arquivo "Escolas" de cada ano | Abandono, aprovação e reprovação por escola |
| `TDI_2025_ESCOLAS.zip` | [INEP – Distorção idade-série](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/indicadores-educacionais/taxas-de-distorcao-idade-serie), 2025, "Escolas" | Atraso escolar (variável do modelo) |
| `INSE_2023_escolas.xlsx` | [INEP – Nível socioeconômico](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/indicadores-educacionais/nivel-socioeconomico), 2023, "Escolas" | Nível socioeconômico da escola |
| `microdados_censo_escolar_2022.zip` | [INEP – Microdados do Censo Escolar](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/censo-escolar) | Matrículas (peso e porte) |
| `microdados_censo_2025_reduzido.zip` | Gerado pelo `reduzir_censo.py` a partir do zip de 2025 | Matrículas por série e infraestrutura |
| `tx_transicao_municipios_AAAA_AAAA.zip` (4) e `tx_transicao_brasil_regioes_ufs_AAAA_AAAA.zip` (4), de 2018/2019 a 2021/2022 | [INEP – Taxas de Transição](https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/indicadores-educacionais/taxas-de-transicao) | Evasão (dado oficial, até 2021/2022) |
| `BR_Municipios_2025.zip` | [IBGE – Malhas Territoriais](https://www.ibge.gov.br/geociencias/organizacao-do-territorio/malhas-territoriais.html), Brasil, municípios | Lista de municípios e mapa |

> ⚠️ Deixe o zip **original** do Censo 2025 (≈ 537 MB) **fora** da pasta `dados/`: a partir de
> 2025 o INEP divide a base em várias tabelas, e só o arquivo reduzido já vem com elas unidas.

A PNAD não precisa ser baixada à mão: o `baixar_pnad.py` busca direto na API do IBGE.

## 2. Como executar

Pré-requisitos: Python 3.10+ e, só para os mapas, Node.js.

No terminal (PowerShell), dentro da pasta `Abandono_Escolar`:

```
pip install -r requirements.txt

# 1. Censo 2025 (só uma vez; o original pode estar em qualquer pasta)
python scripts\reduzir_censo.py CAMINHO\microdados_censo_escolar_2025.zip
#    -> mova o "microdados_censo_2025_reduzido.zip" gerado para dados\

# 2. PNAD (precisa de internet)
python scripts\baixar_pnad.py --saida dados\pnad

# 3. Mapas (precisa de Node.js)
python scripts\gerar_mapas.py --malha dados\BR_Municipios_2025.zip --saida mapas

# 4. Exploração (opcional)
python scripts\explorar_dados.py --dados dados --saida exploracao

# 5. Tabelas finais (3 a 5 minutos)
python scripts\preparar_dados.py --dados dados --pnad dados\pnad --saida powerbi
```

## 3. O que cada script faz

**`reduzir_censo.py`** — Lê só as 26 colunas usadas e, no formato de 2025, une as tabelas Escola,
Matrícula, Docente e Turma pelo código da escola. Detecta sozinho a codificação e o separador.
Recria `IN_NOTURNO` a partir das turmas noturnas, porque essa coluna deixou de existir em 2025.

**`baixar_pnad.py`** — Consulta a API do SIDRA (IBGE):
tabela 7138 (escolarização de 15 a 17 anos, por UF), 7217 (motivo de ter deixado a escola) e
7215 (idade em que deixou a escola). Tenta de novo quando a API recusa por excesso de pedidos.

**`gerar_mapas.py`** — Converte a malha do IBGE (~320 MB) em TopoJSON (~3 MB): reprojeta para
latitude/longitude, simplifica as bordas para 3% dos vértices e mantém só código, nome e UF.

**`explorar_dados.py`** — Lê os arquivos direto dos zips e padroniza as colunas pela posição
(o INEP mudou os códigos em 2021, mas não a ordem). Converte `--` em nulo, confere a qualidade
(duplicatas, valores fora de 0–100, soma das taxas = 100) e a cobertura das junções. Gera
estatísticas descritivas e uma prévia do modelo. Também guarda as funções de leitura usadas pelo
`preparar_dados.py`.

**`preparar_dados.py`** — Faz o pré-processamento final:

1. *Limpeza:* `--` e `***` viram nulo; no Censo, só escolas em funcionamento; na transição,
   "2019/2020" vira ANO = 2019 (ano de origem).
2. *Transformação:* passa as tabelas para o formato longo (escola × ano × etapa); cria peso por
   matrícula, faixas de INSE e de porte, e o período (pré, pandemia, pós).
3. *Redução:* tira as séries do 1º ao 5º ano (abandono ≈ 0,3%); nos fatos territoriais, mantém
   só localização "Total" e redes "Total" e "Pública".
4. *Modelo de anomalias:* regressão linear do abandono no ensino médio (rede pública, 2025) em
   função de INSE, distorção idade-série, rural e rede. Gera esperado, resíduo, z-score,
   classificação e outliers pelo IQR por faixa de INSE.
5. *Agregação:* abandono ponderado por município e UF, unido à evasão e à PNAD.
6. *Validação:* confere a queda do abandono no ensino médio público de 2022 a 2025 (−61%,
   igual ao divulgado pelo INEP).
7. *Documentação:* grava `dicionario_dados.md` e `log_preprocessamento.md`.

## 4. Saídas

| Pasta | Arquivo | Conteúdo |
|---|---|---|
| `powerbi/` | `dim_escola`, `dim_municipio`, `dim_uf`, `dim_etapa`, `dim_ano` | Dimensões |
| `powerbi/` | `fato_rendimento_escola` | Taxas por escola × ano × etapa, com peso |
| `powerbi/` | `fato_anomalias_2025` | Observado × esperado, classificação, IQR |
| `powerbi/` | `fato_municipio`, `fato_uf` | Abandono × evasão (+ PNAD na UF) |
| `powerbi/` | `pnad_motivos_saida`, `pnad_idade_saida` | Motivos e idade de saída da escola |
| `powerbi/` | `dicionario_dados.md`, `log_preprocessamento.md` | Documentação |
| `mapas/` | `municipios_br_2025.json`, `ufs_br_2025.json` | Mapas para o Shape Map |

Os CSVs usam `;` como separador e `,` como decimal (padrão do Power BI em português).
Os relacionamentos entre as tabelas estão no `dicionario_dados.md`.

## 5. Limitações conhecidas

- **Evasão por escola não é publicada.** A evasão oficial só existe por município e UF, e só até
  2021/2022. Para o período recente, usamos a PNAD (jovens de 15 a 17 anos fora da escola, por UF).
- **O peso de 2019 a 2021 é aproximado.** Usa as matrículas do Censo 2022, porque não temos o
  Censo desses anos.
- **O INSE cobre só escolas avaliadas pelo Saeb:** cerca de 92% das escolas públicas com ensino
  médio.
- **A PNAD não teve o módulo de Educação em 2020 e 2021.**
- **O modelo de anomalias explica pouco (R² ≈ 0,12).** Isso indica que o contexto socioeconômico
  sozinho não explica o abandono, e é esse o argumento da página de anomalias.