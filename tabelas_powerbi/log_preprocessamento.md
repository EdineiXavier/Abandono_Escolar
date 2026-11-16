# Log de pré-processamento — Painel Abandono × Evasão

## 1. Leitura

- Transição municipios 2021/2022: 23,722 linhas
- Transição municipios 2020/2021: 23,758 linhas
- Transição municipios 2019/2020: 23,776 linhas
- Transição municipios 2018/2019: 23,793 linhas
- Transição brasil_regioes_ufs 2019/2020: 183 linhas
- Transição brasil_regioes_ufs 2018/2019: 183 linhas
- Transição brasil_regioes_ufs 2021/2022: 183 linhas
- Transição brasil_regioes_ufs 2020/2021: 183 linhas
- PNAD pnad_escolarizacao_15a17_uf.csv: ok
- PNAD pnad_motivos_saida.csv: ok
- PNAD pnad_idade_saida.csv: ok

## 2. Limpeza e transformação

- Rendimento: 906,273 linhas (escola × ano), 0 duplicatas; '--' convertido em nulo (etapa não ofertada pela escola).
- Códigos de coluna do INEP padronizados por posição (o INEP mudou os nomes em 2021).
- Transição: '--' (sem matrícula) e '***' (omitido pelo INEP) convertidos em nulo; ano '2019/2020' vira ANO = 2019 (ano de origem).
- Censo: só escolas em atividade (TP_SITUACAO_FUNCIONAMENTO = 1); anos [2022, 2025].
- Censo 2025 vem em tabelas separadas (Escola, Matrícula, Docente, Turma), unidas por CO_ENTIDADE no reduzir_censo.py; IN_NOTURNO derivado de 'tem turma noturna'.
- Peso por matrícula ausente em 1.8% das linhas (escolas fechadas antes de 2022 ou série sem matrícula em 2025); essas linhas entram só nas médias simples.
- Redução: séries do 1º ao 5º ano removidas do fato por escola (abandono ~0,3%); os anos iniciais seguem como subetapa FUN_AI.

## 3. Redução e colunas criadas

Escolas com abandono > 50.0% marcadas como suspeita de erro de dado: 2 (IFF - CAMPUS CAMPOS CENTRO, CE CASTELNUOVO)

Modelo de abandono esperado (ensino médio, rede pública, 2025): 18,365 escolas com ≥50 matrículas, INSE e TDI.
R² = 0.123; desvio-padrão do resíduo = 3.10 p.p.
Cortes: muito pior se z ≥ 2.0; muito melhor se z ≤ -1.0 (assimétrico por causa do piso de 0% na taxa).
Coeficientes: constante = -3.286, INSE = 0.793, TDI_MED_2025 = 0.107, RURAL = -0.376, MUNICIPAL = -0.091, FEDERAL = -1.101

| CLASSIFICACAO               |   count |
|:----------------------------|--------:|
| Dentro do esperado          |   16303 |
| Fora do modelo              |    3044 |
| Muito melhor que o esperado |    1182 |
| Muito pior que o esperado   |     880 |
| Suspeita de erro de dado    |       2 |

Outliers pelo IQR (Q3 + 1,5×IQR) por faixa de INSE:

| FAIXA_INSE    |   IQR_Q1 |   IQR_MEDIANA |   IQR_Q3 |   IQR_LIM_SUPERIOR |   IQR_LIM_INFERIOR |   outliers acima |   escolas |
|:--------------|---------:|--------------:|---------:|-------------------:|-------------------:|-----------------:|----------:|
| Alto (VI–VII) |        0 |           0.7 |      2.4 |               6    |                  0 |               79 |      2032 |
| Baixo (I–III) |        0 |           1.1 |      3.4 |               8.5  |                  0 |              293 |      4835 |
| Médio (IV–V)  |        0 |           1.2 |      3.9 |               9.75 |                  0 |              516 |     11498 |

O limite inferior é sempre 0 (Q1 − 1,5×IQR < 0), então só existem outliers para cima.

- Fatos territoriais: só localização 'Total' e redes 'Total' e 'Pública'; etapas do comparativo: FUN, FUN_AI, FUN_AF, FUN_06, FUN_07, FUN_08, FUN_09, MED, MED_01, MED_02, MED_03.
- Colunas criadas: PERIODO, REDE, FAIXA_INSE, PORTE_MEDIO, MATRICULAS_PESO, ABANDONO_ESPERADO, RESIDUO, Z_SCORE, CLASSIFICACAO, OUTLIER_IQR (+ quartis), VARIACAO_2022_2025, PNAD_FORA_ESCOLA_15A17.

## 4. Validação

Abandono no ensino médio da rede pública — Brasil:

|   ANO |   ponderado por matrícula |   média simples |
|------:|--------------------------:|----------------:|
|  2019 |                      5.2  |            5.08 |
|  2020 |                      2.6  |            2.98 |
|  2021 |                      5.42 |            5.12 |
|  2022 |                      6.37 |            6.19 |
|  2023 |                      3.7  |            3.72 |
|  2024 |                      3.5  |            3.5  |
|  2025 |                      2.49 |            2.55 |

Variação 2022→2025 (ponderado): -61%. Referência do INEP: queda de 61% no abandono do ensino médio público entre 2022 e 2025.

## 5. Arquivos gerados

- `dim_escola.csv`: 143,736 linhas × 29 colunas
- `dim_municipio.csv`: 5,573 linhas × 6 colunas
- `dim_uf.csv`: 27 linhas × 4 colunas
- `dim_etapa.csv`: 17 linhas × 5 colunas
- `dim_ano.csv`: 8 linhas × 4 colunas
- `fato_rendimento_escola.csv`: 4,394,336 linhas × 7 colunas
- `fato_anomalias_2025.csv`: 21,411 linhas × 15 colunas
- `fato_municipio.csv`: 979,767 linhas × 13 colunas
- `fato_uf.csv`: 4,752 linhas × 16 colunas
- `pnad_motivos_saida.csv`: 690 linhas × 7 colunas
- `pnad_idade_saida.csv`: 720 linhas × 7 colunas