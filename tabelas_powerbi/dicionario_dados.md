# Dicionário de dados — Painel Abandono × Evasão Escolar

Arquivos CSV com `;` como separador, `,` como decimal e codificação UTF-8.

**Abandono ≠ evasão.** Abandono: o aluno deixou de frequentar *durante* o ano letivo (Taxas de Rendimento). Evasão: o aluno estava matriculado em um ano e *não se matriculou* no ano seguinte, tendo sido aprovado ou reprovado (Taxas de Transição).

## Relacionamentos sugeridos no Power BI

- `dim_escola[CO_ESCOLA]` 1 → * `fato_rendimento_escola[CO_ESCOLA]`
- `dim_escola[CO_ESCOLA]` 1 → 1 `fato_anomalias_2025[CO_ESCOLA]`
- `dim_etapa[ETAPA]` 1 → * `fato_rendimento_escola`, `fato_municipio`, `fato_uf`
- `dim_ano[ANO]` 1 → * `fato_rendimento_escola`, `fato_municipio`, `fato_uf`
- `dim_municipio[CO_MUNICIPIO]` 1 → * `dim_escola`, `fato_municipio`
- `dim_uf[UF]` 1 → * `dim_municipio`, `fato_uf`

## Mapas (pasta mapas/)

- `municipios_br_2025.json`: TopoJSON simplificado (malha IBGE 2025). Chave: `CD_MUN` = `CO_MUNICIPIO`. Propriedades: CD_MUN, NM_MUN, SIGLA_UF.
- `ufs_br_2025.json`: contorno das UFs. Chave: `SIGLA_UF` = `UF`.

## dim_escola.csv

Uma linha por escola que aparece nas Taxas de Rendimento 2019–2025.

| Coluna | Significado |
|---|---|
| CO_ESCOLA | Código INEP da escola (chave) |
| NO_ESCOLA / NO_MUNICIPIO | Nomes da escola e do município |
| CO_MUNICIPIO | Código IBGE do município (7 dígitos) — liga com dim_municipio |
| UF / REGIAO | Sigla da UF e região |
| LOCALIZACAO | Urbana ou Rural |
| DEPENDENCIA | Federal, Estadual, Municipal ou Privada |
| REDE | Pública (federal+estadual+municipal) ou Privada |
| ULTIMO_ANO_COM_DADO | Último ano em que a escola aparece nas Taxas de Rendimento |
| INSE | Média do Indicador de Nível Socioeconômico (INEP/Saeb 2023) |
| INSE_NIVEL | Nível do INSE (I a VII) ou 'Sem INSE' |
| FAIXA_INSE | Agrupamento: Baixo (I–III), Médio (IV–V), Alto (VI–VII) |
| TDI_FUN_2025 / TDI_FUN_AF_2025 / TDI_MED_2025 | Taxa de distorção idade-série 2025 (%) |
| MATRICULAS_TOTAL / MATRICULAS_MEDIO | Matrículas na educação básica / no ensino médio (Censo mais recente) |
| PORTE_MEDIO | Faixa de matrículas no ensino médio |
| Internet ... Turno noturno | Itens de infraestrutura (Sim/Não), Censo mais recente |

## dim_municipio.csv

Todos os municípios da malha do IBGE 2025 (para o mapa).

| Coluna | Significado |
|---|---|
| CO_MUNICIPIO | Código IBGE (chave) |
| NO_MUNICIPIO | Nome |
| UF / NO_UF / REGIAO | UF e região |
| AREA_KM2 | Área em km² |

## dim_uf.csv

27 UFs.

| Coluna | Significado |
|---|---|
| UF | Sigla (chave) |
| CO_UF | Código IBGE |
| NO_UF | Nome |
| REGIAO | Região |

## dim_ano.csv

Anos 2018–2025.

| Coluna | Significado |
|---|---|
| ANO | Ano (chave) |
| PERIODO | Pré-pandemia (até 2019), Pandemia (2020–21), Pós-pandemia (2022+) |
| TEM_ABANDONO / TEM_EVASAO | Se o ano tem dado de abandono (2019+) e de evasão (até 2021) |

## dim_etapa.csv

Etapas e séries.

| Coluna | Significado |
|---|---|
| ETAPA | Código (chave) |
| ETAPA_NOME | Rótulo para os gráficos |
| NIVEL | Fundamental ou Médio |
| TIPO | Total da etapa / Subetapa / Série — filtre um tipo por visual para não somar níveis diferentes |
| ORDEM | Use em 'Classificar por coluna' para as séries aparecerem em ordem |

## fato_rendimento_escola.csv

Taxas de rendimento por escola × ano × etapa/série (INEP).

| Coluna | Significado |
|---|---|
| ANO | Ano letivo (2019–2025) — liga com dim_ano |
| CO_ESCOLA / ETAPA | Chaves para dim_escola e dim_etapa |
| ABANDONO | Taxa de abandono (%): deixou de frequentar durante o ano letivo |
| REPROVACAO / APROVACAO | Taxas de reprovação e aprovação (%) |
| MATRICULAS_PESO | Matrículas da escola na etapa (Censo mais próximo: 2022 para 2019–2023, 2025 para 2024–2025; séries sempre 2025). Use para médias ponderadas. Séries do 1º ao 5º ano não entram (abandono quase zero); use FUN_AI |

## fato_anomalias_2025.csv

Ensino médio, rede pública, último ano: observado × esperado.

| Coluna | Significado |
|---|---|
| ABANDONO_OBSERVADO | Taxa de abandono no ensino médio (%) |
| ABANDONO_ESPERADO | Previsto pelo modelo (INSE + distorção idade-série + rural + rede) |
| RESIDUO | Observado − esperado (p.p.) |
| Z_SCORE | Resíduo dividido pelo desvio-padrão dos resíduos |
| CLASSIFICACAO | Muito pior (z ≥ 2) / Dentro / Muito melhor (z ≤ −1) que o esperado, ou Fora do modelo (menos de 50 matrículas no médio, sem INSE ou sem TDI) ou Suspeita de erro de dado (abandono > 50% em escola com 50+ matrículas no médio) |
| FAIXA_INSE | Faixa de INSE da escola (repetida aqui para facilitar o boxplot) |
| OUTLIER_IQR | Acima do limite (outlier) se abandono > Q3 + 1,5×IQR da sua faixa de INSE; Dentro dos limites; ou Fora do modelo |
| IQR_Q1 / IQR_MEDIANA / IQR_Q3 / IQR_LIM_SUPERIOR | Estatísticas do boxplot da faixa de INSE da escola |
| ABANDONO_2022 / VARIACAO_2022_2025 | Abandono em 2022 e variação em p.p. |

## fato_municipio.csv / fato_uf.csv

Abandono × evasão por território × ano × rede × etapa.

| Coluna | Significado |
|---|---|
| ANO | Ano de origem. Abandono: 2019–2025. Evasão: transição ANO→ANO+1, só 2018–2021 |
| REDE | Total ou Pública |
| ABANDONO / REPROVACAO | Ponderados por matrícula a partir das escolas (%) |
| ABANDONO_MEDIA_SIMPLES | Média simples entre escolas (%) |
| ESCOLAS / MATRICULAS | Escolas com dado e matrículas usadas como peso |
| EVASAO | Taxa de evasão (%): matriculado no ano e sem matrícula no ano seguinte (INEP) |
| PROMOCAO / REPETENCIA / MIGRACAO_EJA | Demais taxas de transição (%) |
| PNAD_FORA_ESCOLA_15A17 (só fato_uf) | % de jovens de 15 a 17 anos que não frequentam escola (IBGE/PNAD). Igual em todas as etapas/redes da UF no ano |
| PNAD_CV | Coeficiente de variação da estimativa da PNAD (%) |

## pnad_motivos_saida.csv

Jovens de 14 a 29 anos que não concluíram o ensino médio: principal motivo de ter deixado ou nunca frequentado a escola (IBGE/PNAD, tabela 7217).

| Coluna | Significado |
|---|---|
| TERRITORIO | Brasil ou região |
| SEXO | Total, Homens, Mulheres |
| MOTIVO | Motivo declarado |
| VALOR | % das pessoas nessa condição |
| CV_% | Coeficiente de variação |

## pnad_idade_saida.csv

Idade em que deixou a escola pela última vez (IBGE/PNAD, tabela 7215).

| Coluna | Significado |
|---|---|
| IDADE_SAIDA | Faixa de idade |
| VALOR | % das pessoas nessa condição |
