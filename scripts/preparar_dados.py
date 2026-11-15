"""
Pré-processamento final: gera as tabelas do Painel de BI "Abandono × Evasão Escolar".

Uso:
    python preparar_dados.py --dados PASTA_COM_OS_ARQUIVOS --pnad PASTA_DA_PNAD --saida PASTA

Requer, na mesma pasta deste script, o explorar_dados.py (reaproveita as funções de
leitura). A pasta --pnad é a saída do baixar_pnad.py.

Tabelas geradas (CSV com ";" e vírgula decimal, prontas para o Power BI em português):

  DIMENSÕES
    dim_escola.csv          uma linha por escola: localização, rede, INSE, porte, infraestrutura
    dim_municipio.csv       todos os municípios do IBGE (para o mapa)
    dim_uf.csv              UFs e regiões
    dim_etapa.csv           etapas e séries, com ordem de exibição

  FATOS
    fato_rendimento_escola.csv   abandono/aprovação/reprovação por escola, ano e etapa/série
    fato_anomalias_2025.csv      abandono observado × esperado (ensino médio, rede pública)
    fato_municipio.csv           abandono (ponderado) × evasão por município, ano, rede e etapa
    fato_uf.csv                  idem por UF + % de jovens de 15 a 17 anos fora da escola (PNAD)
    pnad_motivos_saida.csv       motivos para deixar a escola (Brasil e regiões, por sexo)
    pnad_idade_saida.csv         idade em que deixou a escola (Brasil e regiões, por sexo)

  DOCUMENTAÇÃO
    dicionario_dados.md          descrição de cada tabela e coluna (base para o relatório)
    log_preprocessamento.md      o que foi limpo, transformado e filtrado, com contagens
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import explorar_dados as ed  # noqa: E402  (funções de leitura e padronização)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

PUBLICAS = ["Federal", "Estadual", "Municipal"]

# Etapas/séries usadas nas tabelas (códigos internos -> rótulo, etapa, ordem).
ETAPAS = {
    "FUN":    ("Ensino Fundamental (total)", "Fundamental", "Total da etapa", 1),
    "FUN_AI": ("Anos Iniciais (1º–5º)",      "Fundamental", "Subetapa", 2),
    "FUN_AF": ("Anos Finais (6º–9º)",        "Fundamental", "Subetapa", 3),
    **{f"FUN_{i:02d}": (f"{i}º ano", "Fundamental", "Série", 3 + i) for i in range(1, 10)},
    "MED":    ("Ensino Médio (total)",       "Médio", "Total da etapa", 13),
    **{f"MED_{i:02d}": (f"{i}ª série EM", "Médio", "Série", 13 + i) for i in range(1, 5)},
}
# Etapas que entram no comparativo abandono × evasão (a transição não tem 4ª série).
ETAPAS_FLUXO = ["FUN", "FUN_AI", "FUN_AF", "FUN_06", "FUN_07", "FUN_08", "FUN_09",
                "MED", "MED_01", "MED_02", "MED_03"]

# Matrícula de cada etapa no Censo (formato padronizado pelo reduzir_censo.py).
MAT_ETAPA = {"FUN": "QT_MAT_FUND", "FUN_AI": "QT_MAT_FUND_AI", "FUN_AF": "QT_MAT_FUND_AF",
             "MED": "QT_MAT_MED"}
MAT_SERIE_2025 = {**{f"FUN_{i:02d}": f"QT_MAT_FUND_AI_{i}" for i in range(1, 6)},
                  **{f"FUN_{i:02d}": f"QT_MAT_FUND_AF_{i}" for i in range(6, 10)},
                  **{f"MED_{i:02d}": f"QT_MAT_MED_{i}" for i in range(1, 5)}}

INFRA = {"IN_INTERNET": "Internet", "IN_INTERNET_ALUNOS": "Internet para alunos",
         "IN_BIBLIOTECA": "Biblioteca", "IN_LABORATORIO_CIENCIAS": "Laboratório de ciências",
         "IN_LABORATORIO_INFORMATICA": "Laboratório de informática",
         "IN_QUADRA_ESPORTES": "Quadra de esportes", "IN_AGUA_POTAVEL": "Água potável",
         "IN_ENERGIA_REDE_PUBLICA": "Energia da rede pública",
         "IN_ESGOTO_REDE_PUBLICA": "Esgoto da rede pública", "IN_NOTURNO": "Turno noturno"}

FAIXAS_PORTE = [0, 50, 150, 300, 600, np.inf]
ROTULOS_PORTE = ["Até 49", "50 a 149", "150 a 299", "300 a 599", "600 ou mais"]
PORTE_MINIMO = 50       # matrículas no ensino médio para entrar no modelo de anomalias
# Cortes de anomalia. São assimétricos de propósito: a taxa não pode ser negativa, então
# uma escola "melhor que o esperado" fica no máximo em 0% (com esperado ~2,4%, o resíduo
# raramente chega a -2 desvios). Com z <= -1 e z >= 2 os dois grupos ficam com ~5% cada.
Z_PIOR = 2.0
Z_MELHOR = -1.0

UFS = {11: ("RO", "Rondônia", "Norte"), 12: ("AC", "Acre", "Norte"), 13: ("AM", "Amazonas", "Norte"),
       14: ("RR", "Roraima", "Norte"), 15: ("PA", "Pará", "Norte"), 16: ("AP", "Amapá", "Norte"),
       17: ("TO", "Tocantins", "Norte"), 21: ("MA", "Maranhão", "Nordeste"),
       22: ("PI", "Piauí", "Nordeste"), 23: ("CE", "Ceará", "Nordeste"),
       24: ("RN", "Rio Grande do Norte", "Nordeste"), 25: ("PB", "Paraíba", "Nordeste"),
       26: ("PE", "Pernambuco", "Nordeste"), 27: ("AL", "Alagoas", "Nordeste"),
       28: ("SE", "Sergipe", "Nordeste"), 29: ("BA", "Bahia", "Nordeste"),
       31: ("MG", "Minas Gerais", "Sudeste"), 32: ("ES", "Espírito Santo", "Sudeste"),
       33: ("RJ", "Rio de Janeiro", "Sudeste"), 35: ("SP", "São Paulo", "Sudeste"),
       41: ("PR", "Paraná", "Sul"), 42: ("SC", "Santa Catarina", "Sul"),
       43: ("RS", "Rio Grande do Sul", "Sul"), 50: ("MS", "Mato Grosso do Sul", "Centro-Oeste"),
       51: ("MT", "Mato Grosso", "Centro-Oeste"), 52: ("GO", "Goiás", "Centro-Oeste"),
       53: ("DF", "Distrito Federal", "Centro-Oeste")}
NOME_PARA_SIGLA = {nome: sigla for sigla, nome, _ in UFS.values()}

LOG: list[str] = []


def log(texto: str = "") -> None:
    print(texto)
    LOG.append(texto)


def salvar(df: pd.DataFrame, pasta: Path, nome: str) -> None:
    """CSV no padrão brasileiro: ';' como separador e ',' como decimal."""
    df.to_csv(pasta / nome, sep=";", decimal=",", index=False, encoding="utf-8-sig",
              float_format="%.2f")
    log(f"- `{nome}`: {len(df):,} linhas × {df.shape[1]} colunas")


def media_ponderada(df: pd.DataFrame, grupo: list[str], valor: str, peso: str) -> pd.Series:
    d = df[df[valor].notna() & df[peso].gt(0)]
    num = (d[valor] * d[peso]).groupby([d[g] for g in grupo]).sum()
    den = d[peso].groupby([d[g] for g in grupo]).sum()
    return num / den


# ---------------------------------------------------------------------------
# Leitura das Taxas de Transição (evasão)
# ---------------------------------------------------------------------------

ETAPAS_TRANS = ["FUN", "FUN_AI", "FUN_AF"] + [f"FUN_{i:02d}" for i in range(1, 10)] \
               + ["MED", "MED_01", "MED_02", "MED_03"]
IND_TRANS = ["PROMOCAO", "REPETENCIA", "EVASAO", "MIGRACAO_EJA"]
MEDIDAS_TRANS = [f"{i}_{e}" for i in IND_TRANS for e in ETAPAS_TRANS]


def ler_transicao(pasta: Path, nivel: str) -> pd.DataFrame:
    """nivel = 'municipios' ou 'brasil_regioes_ufs'. Lê todos os anos encontrados."""
    ids = (["ANO_TRANS", "REGIAO", "UF", "CO_MUNICIPIO", "NO_MUNICIPIO", "LOCALIZACAO", "DEPENDENCIA"]
           if nivel == "municipios" else ["ANO_TRANS", "TERRITORIO", "LOCALIZACAO", "DEPENDENCIA"])
    partes = []
    for z in ed.achar(pasta, f"*tx_transicao_{nivel}_*.zip"):
        zf, nome = ed.membro_do_zip(z, r"\.xlsx$")
        df = pd.read_excel(io.BytesIO(zf.read(nome)), header=None, skiprows=9, engine="calamine")
        df.columns = ids + MEDIDAS_TRANS
        df = df[df["ANO_TRANS"].astype(str).str.match(r"^\d{4}/\d{4}$")].copy()
        # "--" = sem matrícula na etapa; "***" = dado omitido pelo INEP (poucos alunos).
        df[MEDIDAS_TRANS] = df[MEDIDAS_TRANS].replace(["--", "***"], np.nan).apply(pd.to_numeric, errors="coerce")
        df["ANO"] = df["ANO_TRANS"].str[:4].astype(int)  # ano de origem: 2019/2020 -> 2019
        partes.append(df)
        log(f"- Transição {nivel} {df['ANO_TRANS'].iloc[0]}: {len(df):,} linhas")
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def transicao_longa(df: pd.DataFrame, chaves: list[str]) -> pd.DataFrame:
    """Uma linha por chave + etapa, com as 4 taxas em colunas."""
    longo = df.melt(id_vars=chaves, value_vars=MEDIDAS_TRANS, var_name="COD", value_name="V")
    longo["INDICADOR"] = longo["COD"].str.extract(r"^([A-Z_]+?)_(?:FUN|MED)")[0]
    longo["ETAPA"] = longo["COD"].str.extract(r"_((?:FUN|MED)(?:_\w+)?)$")[0]
    return (longo.pivot_table(index=chaves + ["ETAPA"], columns="INDICADOR", values="V", aggfunc="first")
            .reset_index().rename_axis(columns=None))


# ---------------------------------------------------------------------------
# Etapas do processamento
# ---------------------------------------------------------------------------

def carregar(args) -> dict:
    log("## 1. Leitura")
    log()
    b = {
        "rend": ed.ler_rendimento(args.dados),
        "tdi": ed.ler_tdi(args.dados),
        "inse": ed.ler_inse(args.dados),
        "censo": ed.ler_censo(args.dados),
        "malha": ed.ler_malha(args.dados),
        "trans_mun": ler_transicao(args.dados, "municipios"),
        "trans_uf": ler_transicao(args.dados, "brasil_regioes_ufs"),
    }
    for nome in ["pnad_escolarizacao_15a17_uf.csv", "pnad_motivos_saida.csv", "pnad_idade_saida.csv"]:
        caminho = args.pnad / nome
        b[nome] = pd.read_csv(caminho) if caminho.exists() else None
        log(f"- PNAD {nome}: " + ("ok" if b[nome] is not None else "⚠️ não encontrado (rode baixar_pnad.py)"))
    return b


def montar_censo(censo: dict[int, pd.DataFrame]) -> pd.DataFrame:
    """Uma linha por escola com matrículas 2022 e 2025 (sufixo _2022/_2025) e infra mais recente."""
    partes = []
    for ano, df in censo.items():
        d = df.copy()
        if "TP_SITUACAO_FUNCIONAMENTO" in d:
            d = d[d["TP_SITUACAO_FUNCIONAMENTO"] == 1]  # só escolas em atividade
        cols = [c for c in d.columns if c.startswith("QT_")]
        partes.append(d.set_index("CO_ESCOLA")[cols].add_suffix(f"_{ano}"))
    largo = pd.concat(partes, axis=1)
    ult = max(censo)
    ativas = censo[ult]
    if "TP_SITUACAO_FUNCIONAMENTO" in ativas:
        ativas = ativas[ativas["TP_SITUACAO_FUNCIONAMENTO"] == 1]
    infra = ativas.set_index("CO_ESCOLA")[[c for c in INFRA if c in ativas]]
    return largo.join(infra, how="outer")


def dim_escola(b: dict, censo_l: pd.DataFrame) -> pd.DataFrame:
    rend = b["rend"]
    # Atributos da escola no ano mais recente em que ela aparece.
    ult = rend.sort_values("ANO").groupby("CO_ESCOLA").tail(1)
    d = ult[["CO_ESCOLA", "NO_ESCOLA", "CO_MUNICIPIO", "NO_MUNICIPIO", "UF", "REGIAO",
             "LOCALIZACAO", "DEPENDENCIA"]].copy()
    d["REDE"] = np.where(d["DEPENDENCIA"].isin(PUBLICAS), "Pública", "Privada")
    d["ULTIMO_ANO_COM_DADO"] = ult["ANO"].values

    if b["inse"] is not None:
        i = b["inse"][["CO_ESCOLA", "MEDIA_INSE", "INSE_CLASSIFICACAO"]].rename(
            columns={"MEDIA_INSE": "INSE", "INSE_CLASSIFICACAO": "INSE_NIVEL"})
        d = d.merge(i, on="CO_ESCOLA", how="left")
        mapa = {"Nível I": "Baixo (I–III)", "Nível II": "Baixo (I–III)", "Nível III": "Baixo (I–III)",
                "Nível IV": "Médio (IV–V)", "Nível V": "Médio (IV–V)",
                "Nível VI": "Alto (VI–VII)", "Nível VII": "Alto (VI–VII)"}
        d["FAIXA_INSE"] = d["INSE_NIVEL"].map(mapa).fillna("Sem INSE")
        d["INSE_NIVEL"] = d["INSE_NIVEL"].fillna("Sem INSE")
    if b["tdi"] is not None:
        t = b["tdi"][["CO_ESCOLA", "TDI_FUN", "TDI_FUN_AF", "TDI_MED"]].rename(
            columns=lambda c: c if c == "CO_ESCOLA" else f"{c}_2025")
        d = d.merge(t, on="CO_ESCOLA", how="left")

    ult_censo = max(b["censo"])
    c = censo_l[[x for x in censo_l.columns
                 if x in INFRA or x in (f"QT_MAT_BAS_{ult_censo}", f"QT_MAT_MED_{ult_censo}")]]
    c = c.rename(columns={f"QT_MAT_BAS_{ult_censo}": "MATRICULAS_TOTAL",
                          f"QT_MAT_MED_{ult_censo}": "MATRICULAS_MEDIO"})
    d = d.merge(c, left_on="CO_ESCOLA", right_index=True, how="left")
    d["PORTE_MEDIO"] = pd.cut(d["MATRICULAS_MEDIO"].where(d["MATRICULAS_MEDIO"] > 0),
                              FAIXAS_PORTE, labels=ROTULOS_PORTE, right=False).astype(str)
    d.loc[d["PORTE_MEDIO"] == "nan", "PORTE_MEDIO"] = "Sem ensino médio"
    for col, rotulo in INFRA.items():
        if col in d:
            d[rotulo] = d[col].map({1: "Sim", 0: "Não"}).fillna("Sem informação")
            d = d.drop(columns=col)
    return d


def fato_rendimento(rend: pd.DataFrame, censo_l: pd.DataFrame, anos_censo: list[int]) -> pd.DataFrame:
    """Formato longo: escola × ano × etapa/série. Inclui peso (matrículas) para médias ponderadas."""
    cols = [f"{ind}_{e}" for ind in ["APR", "REP", "ABA"] for e in ETAPAS]
    longo = rend.melt(id_vars=["ANO", "CO_ESCOLA"], value_vars=cols, var_name="COD", value_name="V")
    longo = longo.dropna(subset=["V"])
    longo["IND"] = longo["COD"].str[:3]
    longo["ETAPA"] = longo["COD"].str[4:]
    f = (longo.pivot_table(index=["ANO", "CO_ESCOLA", "ETAPA"], columns="IND", values="V", aggfunc="first")
         .reset_index().rename_axis(columns=None)
         .rename(columns={"APR": "APROVACAO", "REP": "REPROVACAO", "ABA": "ABANDONO"}))

    # Peso = matrículas da escola na etapa. Para cada ano usamos o Censo mais próximo
    # disponível (2022 para 2019–2023; 2025 para 2024–2025), com o outro como reserva.
    # Séries: só o Censo 2025 traz matrícula por série; usamos 2025 para todos os anos.
    def censo_mais_proximo(ano):
        return sorted(anos_censo, key=lambda a: (abs(a - ano), -a))

    peso = pd.Series(np.nan, index=f.index)
    for ano in f["ANO"].unique():
        ordem = censo_mais_proximo(ano)
        for etapa in f.loc[f["ANO"] == ano, "ETAPA"].unique():
            m = (f["ANO"] == ano) & (f["ETAPA"] == etapa)
            if etapa in MAT_ETAPA:
                fontes = [f"{MAT_ETAPA[etapa]}_{a}" for a in ordem]
            else:
                fontes = [f"{MAT_SERIE_2025[etapa]}_2025"] if etapa in MAT_SERIE_2025 else []
            for fonte in fontes:
                if fonte in censo_l:
                    falta = m & peso.isna()
                    peso[falta] = f.loc[falta, "CO_ESCOLA"].map(censo_l[fonte])
    f["MATRICULAS_PESO"] = peso
    # Redução: os anos iniciais (1º–5º) têm abandono perto de zero; ficam só como subetapa.
    f = f[~f["ETAPA"].isin([f"FUN_{i:02d}" for i in range(1, 6)])]
    f["MATRICULAS_PESO"] = f["MATRICULAS_PESO"].astype("Int64")
    return f[["ANO", "CO_ESCOLA", "ETAPA", "ABANDONO", "REPROVACAO", "APROVACAO", "MATRICULAS_PESO"]]


def anomalias(rend: pd.DataFrame, dim: pd.DataFrame) -> pd.DataFrame:
    """Abandono esperado no ensino médio (rede pública, último ano) e desvio de cada escola."""
    ult = rend["ANO"].max()
    r = rend[rend["ANO"] == ult][["CO_ESCOLA", "ABA_MED", "REP_MED"]]
    d = dim.merge(r, on="CO_ESCOLA")
    d = d[(d["REDE"] == "Pública") & d["ABA_MED"].notna()].copy()

    d["RURAL"] = (d["LOCALIZACAO"] == "Rural").astype(int)
    d["MUNICIPAL"] = (d["DEPENDENCIA"] == "Municipal").astype(int)
    d["FEDERAL"] = (d["DEPENDENCIA"] == "Federal").astype(int)
    xs = ["INSE", "TDI_MED_2025", "RURAL", "MUNICIPAL", "FEDERAL"]
    d["NO_MODELO"] = d[xs].notna().all(axis=1) & (d["MATRICULAS_MEDIO"] >= PORTE_MINIMO)

    treino = d[d["NO_MODELO"]]
    X = np.column_stack([np.ones(len(treino))] + [treino[x].to_numpy(float) for x in xs])
    coef, *_ = np.linalg.lstsq(X, treino["ABA_MED"].to_numpy(float), rcond=None)
    resid_treino = treino["ABA_MED"].to_numpy(float) - X @ coef
    dp = resid_treino.std()
    r2 = 1 - resid_treino.var() / treino["ABA_MED"].var(ddof=0)

    m = d["NO_MODELO"]
    Xall = np.column_stack([np.ones(m.sum())] + [d.loc[m, x].to_numpy(float) for x in xs])
    d.loc[m, "ABANDONO_ESPERADO"] = np.clip(Xall @ coef, 0, None)
    d["RESIDUO"] = d["ABA_MED"] - d["ABANDONO_ESPERADO"]
    d["Z_SCORE"] = d["RESIDUO"] / dp
    d["CLASSIFICACAO"] = np.select(
        [~m, d["Z_SCORE"] >= Z_PIOR, d["Z_SCORE"] <= Z_MELHOR],
        ["Fora do modelo", "Muito pior que o esperado", "Muito melhor que o esperado"],
        default="Dentro do esperado")

    a22 = rend[rend["ANO"] == 2022].set_index("CO_ESCOLA")["ABA_MED"]
    d["ABANDONO_2022"] = d["CO_ESCOLA"].map(a22)
    d["VARIACAO_2022_ULT"] = d["ABA_MED"] - d["ABANDONO_2022"]

    log()
    log(f"Modelo de abandono esperado (ensino médio, rede pública, {ult}): "
        f"{len(treino):,} escolas com ≥{PORTE_MINIMO} matrículas, INSE e TDI.")
    log(f"R² = {r2:.3f}; desvio-padrão do resíduo = {dp:.2f} p.p.")
    log(f"Cortes: muito pior se z ≥ {Z_PIOR}; muito melhor se z ≤ {Z_MELHOR} (assimétrico por causa do "
        "piso de 0% na taxa).")
    log("Coeficientes: " + ", ".join(f"{n} = {c:.3f}" for n, c in zip(["constante"] + xs, coef)))
    log()
    log(d["CLASSIFICACAO"].value_counts().to_markdown())
    log()

    # Outliers pelo IQR dentro de cada faixa de INSE (para o boxplot). Usa as mesmas escolas
    # do modelo (públicas, >= 50 matrículas no médio, com INSE e TDI).
    base = d[m]
    q = base.groupby("FAIXA_INSE")["ABA_MED"].quantile([.25, .5, .75]).unstack()
    q.columns = ["IQR_Q1", "IQR_MEDIANA", "IQR_Q3"]
    iqr = q["IQR_Q3"] - q["IQR_Q1"]
    q["IQR_LIM_SUPERIOR"] = q["IQR_Q3"] + 1.5 * iqr
    q["IQR_LIM_INFERIOR"] = (q["IQR_Q1"] - 1.5 * iqr).clip(lower=0)  # taxa não fica abaixo de 0
    d = d.merge(q, left_on="FAIXA_INSE", right_index=True, how="left")
    d["OUTLIER_IQR"] = np.select(
        [~m, d["ABA_MED"] > d["IQR_LIM_SUPERIOR"], d["ABA_MED"] < d["IQR_LIM_INFERIOR"]],
        ["Fora do modelo", "Acima do limite (outlier)", "Abaixo do limite (outlier)"],
        default="Dentro dos limites")
    log("Outliers pelo IQR (Q3 + 1,5×IQR) por faixa de INSE:")
    log()
    resumo = q.round(2)
    resumo["outliers acima"] = d[d["OUTLIER_IQR"] == "Acima do limite (outlier)"].groupby("FAIXA_INSE").size()
    resumo["escolas"] = base.groupby("FAIXA_INSE").size()
    log(resumo.to_markdown())
    log()
    log("O limite inferior é sempre 0 (Q1 − 1,5×IQR < 0), então só existem outliers para cima.")
    log()

    return d[["CO_ESCOLA", "FAIXA_INSE", "ABA_MED", "REP_MED", "ABANDONO_ESPERADO", "RESIDUO", "Z_SCORE",
              "CLASSIFICACAO", "OUTLIER_IQR", "IQR_Q1", "IQR_MEDIANA", "IQR_Q3", "IQR_LIM_SUPERIOR",
              "ABANDONO_2022", "VARIACAO_2022_ULT"]].rename(
        columns={"ABA_MED": "ABANDONO_OBSERVADO", "REP_MED": "REPROVACAO",
                 "VARIACAO_2022_ULT": f"VARIACAO_2022_{ult}"})


def agregar_abandono(fato: pd.DataFrame, dim: pd.DataFrame, nivel: str) -> pd.DataFrame:
    """Abandono e reprovação ponderados por matrícula, por território × ano × rede × etapa."""
    f = fato[fato["ETAPA"].isin(ETAPAS_FLUXO)].merge(
        dim[["CO_ESCOLA", "CO_MUNICIPIO", "UF", "REDE"]], on="CO_ESCOLA")
    total = f.assign(REDE="Total")
    f = pd.concat([total, f[f["REDE"] == "Pública"]], ignore_index=True)
    grupo = [nivel, "ANO", "REDE", "ETAPA"]
    out = pd.DataFrame({
        "ABANDONO": media_ponderada(f, grupo, "ABANDONO", "MATRICULAS_PESO"),
        "REPROVACAO": media_ponderada(f, grupo, "REPROVACAO", "MATRICULAS_PESO"),
        "ABANDONO_MEDIA_SIMPLES": f.groupby(grupo)["ABANDONO"].mean(),
        "ESCOLAS": f.groupby(grupo)["CO_ESCOLA"].nunique(),
        "MATRICULAS": f.groupby(grupo)["MATRICULAS_PESO"].sum(min_count=1),
    }).reset_index()
    return out


def fato_territorio(b, fato, dim, nivel: str) -> pd.DataFrame:
    ab = agregar_abandono(fato, dim, nivel)
    if nivel == "CO_MUNICIPIO":
        t = b["trans_mun"]
        t = t[(t["LOCALIZACAO"] == "Total") & t["DEPENDENCIA"].isin(["Total", "Pública"])]
        t = t.assign(CO_MUNICIPIO=t["CO_MUNICIPIO"].astype("int64"), REDE=t["DEPENDENCIA"])
    else:
        t = b["trans_uf"]
        t = t[(t["LOCALIZACAO"] == "Total") & t["DEPENDENCIA"].isin(["Total", "Pública"])
              & t["TERRITORIO"].isin(NOME_PARA_SIGLA)]
        t = t.assign(UF=t["TERRITORIO"].map(NOME_PARA_SIGLA), REDE=t["DEPENDENCIA"])
    tl = transicao_longa(t, [nivel, "ANO", "REDE"])
    tl = tl[tl["ETAPA"].isin(ETAPAS_FLUXO)]
    out = ab.merge(tl, on=[nivel, "ANO", "REDE", "ETAPA"], how="outer")

    if nivel == "UF" and b["pnad_escolarizacao_15a17_uf.csv"] is not None:
        p = b["pnad_escolarizacao_15a17_uf.csv"]
        p = p[p["GRUPO_IDADE"] == "15 a 17 anos"].assign(UF=lambda x: x["CO_UF"].map(lambda c: UFS[c][0]))
        p = p.rename(columns={"VALOR": "PNAD_ESCOLARIZACAO_15A17", "CV_%": "PNAD_CV"})
        p["PNAD_FORA_ESCOLA_15A17"] = 100 - p["PNAD_ESCOLARIZACAO_15A17"]
        out = out.merge(p[["UF", "ANO", "PNAD_ESCOLARIZACAO_15A17", "PNAD_FORA_ESCOLA_15A17", "PNAD_CV"]],
                        on=["UF", "ANO"], how="left")
        # A PNAD é por UF e ano (não por rede/etapa): fica igual em todas as linhas do mesmo UF/ano.
    return out.rename(columns={"EVASAO": "EVASAO", "PROMOCAO": "PROMOCAO",
                               "REPETENCIA": "REPETENCIA", "MIGRACAO_EJA": "MIGRACAO_EJA"})


# ---------------------------------------------------------------------------
# Validação contra números oficiais conhecidos
# ---------------------------------------------------------------------------

def validar(fato_uf: pd.DataFrame, fato: pd.DataFrame, dim: pd.DataFrame) -> None:
    log("## 4. Validação")
    log()
    f = fato.merge(dim[["CO_ESCOLA", "REDE"]], on="CO_ESCOLA")
    f = f[(f["REDE"] == "Pública") & (f["ETAPA"] == "MED")]
    pond = media_ponderada(f, ["ANO"], "ABANDONO", "MATRICULAS_PESO")
    simples = f.groupby("ANO")["ABANDONO"].mean()
    log("Abandono no ensino médio da rede pública — Brasil:")
    log()
    log(pd.DataFrame({"ponderado por matrícula": pond, "média simples": simples}).round(2).to_markdown())
    log()
    if 2022 in pond and 2025 in pond:
        queda = (pond[2025] / pond[2022] - 1) * 100
        log(f"Variação 2022→2025 (ponderado): {queda:.0f}%. Referência do INEP: queda de 61% "
            f"no abandono do ensino médio público entre 2022 e 2025.")
    log()


# ---------------------------------------------------------------------------
# Dicionário de dados
# ---------------------------------------------------------------------------

DICIONARIO = {
    "dim_escola.csv": ("Uma linha por escola que aparece nas Taxas de Rendimento 2019–2025.", {
        "CO_ESCOLA": "Código INEP da escola (chave)",
        "NO_ESCOLA / NO_MUNICIPIO": "Nomes da escola e do município",
        "CO_MUNICIPIO": "Código IBGE do município (7 dígitos) — liga com dim_municipio",
        "UF / REGIAO": "Sigla da UF e região",
        "LOCALIZACAO": "Urbana ou Rural",
        "DEPENDENCIA": "Federal, Estadual, Municipal ou Privada",
        "REDE": "Pública (federal+estadual+municipal) ou Privada",
        "ULTIMO_ANO_COM_DADO": "Último ano em que a escola aparece nas Taxas de Rendimento",
        "INSE": "Média do Indicador de Nível Socioeconômico (INEP/Saeb 2023)",
        "INSE_NIVEL": "Nível do INSE (I a VII) ou 'Sem INSE'",
        "FAIXA_INSE": "Agrupamento: Baixo (I–III), Médio (IV–V), Alto (VI–VII)",
        "TDI_FUN_2025 / TDI_FUN_AF_2025 / TDI_MED_2025": "Taxa de distorção idade-série 2025 (%)",
        "MATRICULAS_TOTAL / MATRICULAS_MEDIO": "Matrículas na educação básica / no ensino médio (Censo mais recente)",
        "PORTE_MEDIO": "Faixa de matrículas no ensino médio",
        "Internet ... Turno noturno": "Itens de infraestrutura (Sim/Não), Censo mais recente",
    }),
    "dim_municipio.csv": ("Todos os municípios da malha do IBGE 2025 (para o mapa).", {
        "CO_MUNICIPIO": "Código IBGE (chave)", "NO_MUNICIPIO": "Nome", "UF / NO_UF / REGIAO": "UF e região",
        "AREA_KM2": "Área em km²"}),
    "dim_uf.csv": ("27 UFs.", {"UF": "Sigla (chave)", "CO_UF": "Código IBGE", "NO_UF": "Nome", "REGIAO": "Região"}),
    "dim_ano.csv": ("Anos 2018–2025.", {
        "ANO": "Ano (chave)", "PERIODO": "Pré-pandemia (até 2019), Pandemia (2020–21), Pós-pandemia (2022+)",
        "TEM_ABANDONO / TEM_EVASAO": "Se o ano tem dado de abandono (2019+) e de evasão (até 2021)"}),
    "dim_etapa.csv": ("Etapas e séries.", {
        "ETAPA": "Código (chave)", "ETAPA_NOME": "Rótulo para os gráficos", "NIVEL": "Fundamental ou Médio",
        "TIPO": "Total da etapa / Subetapa / Série — filtre um tipo por visual para não somar níveis diferentes",
        "ORDEM": "Use em 'Classificar por coluna' para as séries aparecerem em ordem"}),
    "fato_rendimento_escola.csv": ("Taxas de rendimento por escola × ano × etapa/série (INEP).", {
        "ANO": "Ano letivo (2019–2025) — liga com dim_ano",
        "CO_ESCOLA / ETAPA": "Chaves para dim_escola e dim_etapa",
        "ABANDONO": "Taxa de abandono (%): deixou de frequentar durante o ano letivo",
        "REPROVACAO / APROVACAO": "Taxas de reprovação e aprovação (%)",
        "MATRICULAS_PESO": "Matrículas da escola na etapa (Censo mais próximo: 2022 para 2019–2023, "
                           "2025 para 2024–2025; séries sempre 2025). Use para médias ponderadas. "
                           "Séries do 1º ao 5º ano não entram (abandono quase zero); use FUN_AI"}),
    "fato_anomalias_2025.csv": ("Ensino médio, rede pública, último ano: observado × esperado.", {
        "ABANDONO_OBSERVADO": "Taxa de abandono no ensino médio (%)",
        "ABANDONO_ESPERADO": "Previsto pelo modelo (INSE + distorção idade-série + rural + rede)",
        "RESIDUO": "Observado − esperado (p.p.)", "Z_SCORE": "Resíduo dividido pelo desvio-padrão dos resíduos",
        "CLASSIFICACAO": "Muito pior (z ≥ 2) / Dentro / Muito melhor (z ≤ −1) que o esperado, ou Fora do modelo "
                         "(menos de 50 matrículas no médio, sem INSE ou sem TDI)",
        "FAIXA_INSE": "Faixa de INSE da escola (repetida aqui para facilitar o boxplot)",
        "OUTLIER_IQR": "Acima do limite (outlier) se abandono > Q3 + 1,5×IQR da sua faixa de INSE; "
                       "Dentro dos limites; ou Fora do modelo",
        "IQR_Q1 / IQR_MEDIANA / IQR_Q3 / IQR_LIM_SUPERIOR": "Estatísticas do boxplot da faixa de INSE da escola",
        "ABANDONO_2022 / VARIACAO_2022_2025": "Abandono em 2022 e variação em p.p."}),
    "fato_municipio.csv / fato_uf.csv": ("Abandono × evasão por território × ano × rede × etapa.", {
        "ANO": "Ano de origem. Abandono: 2019–2025. Evasão: transição ANO→ANO+1, só 2018–2021",
        "REDE": "Total ou Pública",
        "ABANDONO / REPROVACAO": "Ponderados por matrícula a partir das escolas (%)",
        "ABANDONO_MEDIA_SIMPLES": "Média simples entre escolas (%)",
        "ESCOLAS / MATRICULAS": "Escolas com dado e matrículas usadas como peso",
        "EVASAO": "Taxa de evasão (%): matriculado no ano e sem matrícula no ano seguinte (INEP)",
        "PROMOCAO / REPETENCIA / MIGRACAO_EJA": "Demais taxas de transição (%)",
        "PNAD_FORA_ESCOLA_15A17 (só fato_uf)": "% de jovens de 15 a 17 anos que não frequentam escola "
                                               "(IBGE/PNAD). Igual em todas as etapas/redes da UF no ano",
        "PNAD_CV": "Coeficiente de variação da estimativa da PNAD (%)"}),
    "pnad_motivos_saida.csv": ("Jovens de 14 a 29 anos que não concluíram o ensino médio: principal motivo "
                               "de ter deixado ou nunca frequentado a escola (IBGE/PNAD, tabela 7217).", {
        "TERRITORIO": "Brasil ou região", "SEXO": "Total, Homens, Mulheres", "MOTIVO": "Motivo declarado",
        "VALOR": "% das pessoas nessa condição", "CV_%": "Coeficiente de variação"}),
    "pnad_idade_saida.csv": ("Idade em que deixou a escola pela última vez (IBGE/PNAD, tabela 7215).", {
        "IDADE_SAIDA": "Faixa de idade", "VALOR": "% das pessoas nessa condição"}),
}


def escrever_dicionario(pasta: Path) -> None:
    linhas = ["# Dicionário de dados — Painel Abandono × Evasão Escolar", "",
              "Arquivos CSV com `;` como separador, `,` como decimal e codificação UTF-8.", "",
              "**Abandono ≠ evasão.** Abandono: o aluno deixou de frequentar *durante* o ano letivo "
              "(Taxas de Rendimento). Evasão: o aluno estava matriculado em um ano e *não se matriculou* "
              "no ano seguinte, tendo sido aprovado ou reprovado (Taxas de Transição).", "",
              "## Relacionamentos sugeridos no Power BI", "",
              "- `dim_escola[CO_ESCOLA]` 1 → * `fato_rendimento_escola[CO_ESCOLA]`",
              "- `dim_escola[CO_ESCOLA]` 1 → 1 `fato_anomalias_2025[CO_ESCOLA]`",
              "- `dim_etapa[ETAPA]` 1 → * `fato_rendimento_escola`, `fato_municipio`, `fato_uf`",
              "- `dim_ano[ANO]` 1 → * `fato_rendimento_escola`, `fato_municipio`, `fato_uf`",
              "- `dim_municipio[CO_MUNICIPIO]` 1 → * `dim_escola`, `fato_municipio`",
              "- `dim_uf[UF]` 1 → * `dim_municipio`, `fato_uf`", "",
              "## Mapas (pasta mapas/)", "",
              "- `municipios_br_2025.json`: TopoJSON simplificado (malha IBGE 2025). Chave: `CD_MUN` "
              "= `CO_MUNICIPIO`. Propriedades: CD_MUN, NM_MUN, SIGLA_UF.",
              "- `ufs_br_2025.json`: contorno das UFs. Chave: `SIGLA_UF` = `UF`.", ""]
    for arq, (desc, cols) in DICIONARIO.items():
        linhas += [f"## {arq}", "", desc, "", "| Coluna | Significado |", "|---|---|"]
        linhas += [f"| {c} | {s} |" for c, s in cols.items()] + [""]
    (pasta / "dicionario_dados.md").write_text("\n".join(linhas), encoding="utf-8")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", type=Path, default=Path("dados"))
    ap.add_argument("--pnad", type=Path, default=Path("dados/pnad"))
    ap.add_argument("--saida", type=Path, default=Path("powerbi"))
    args = ap.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    log("# Log de pré-processamento — Painel Abandono × Evasão")
    log()
    b = carregar(args)

    log()
    log("## 2. Limpeza e transformação")
    log()
    rend = b["rend"]
    log(f"- Rendimento: {len(rend):,} linhas (escola × ano), 0 duplicatas; '--' convertido em nulo "
        f"(etapa não ofertada pela escola).")
    log("- Códigos de coluna do INEP padronizados por posição (o INEP mudou os nomes em 2021).")
    log("- Transição: '--' (sem matrícula) e '***' (omitido pelo INEP) convertidos em nulo; "
        "ano '2019/2020' vira ANO = 2019 (ano de origem).")
    censo_l = montar_censo(b["censo"])
    log(f"- Censo: só escolas em atividade (TP_SITUACAO_FUNCIONAMENTO = 1); anos {sorted(b['censo'])}.")
    log("- Censo 2025 vem em tabelas separadas (Escola, Matrícula, Docente, Turma), unidas por CO_ENTIDADE "
        "no reduzir_censo.py; IN_NOTURNO derivado de 'tem turma noturna'.")

    dim = dim_escola(b, censo_l)
    fato = fato_rendimento(rend, censo_l, sorted(b["censo"]))
    sem_peso = fato["MATRICULAS_PESO"].isna().mean() * 100
    log(f"- Peso por matrícula ausente em {sem_peso:.1f}% das linhas (escolas fechadas antes de 2022 "
        "ou série sem matrícula em 2025); essas linhas entram só nas médias simples.")
    log("- Redução: séries do 1º ao 5º ano removidas do fato por escola (abandono ~0,3%); "
        "os anos iniciais seguem como subetapa FUN_AI.")

    log()
    log("## 3. Redução e colunas criadas")
    log()
    anom = anomalias(rend, dim)
    f_mun = fato_territorio(b, fato, dim, "CO_MUNICIPIO")
    f_uf = fato_territorio(b, fato, dim, "UF")
    log("- Fatos territoriais: só localização 'Total' e redes 'Total' e 'Pública'; "
        f"etapas do comparativo: {', '.join(ETAPAS_FLUXO)}.")
    log("- Colunas criadas: PERIODO, REDE, FAIXA_INSE, PORTE_MEDIO, MATRICULAS_PESO, ABANDONO_ESPERADO, "
        "RESIDUO, Z_SCORE, CLASSIFICACAO, OUTLIER_IQR (+ quartis), VARIACAO_2022_2025, PNAD_FORA_ESCOLA_15A17.")
    log()

    validar(f_uf, fato, dim)

    # Dimensões territoriais
    if b["malha"] is not None:
        mun = b["malha"].rename(columns={"CD_MUN": "CO_MUNICIPIO", "NM_MUN": "NO_MUNICIPIO",
                                         "SIGLA_UF": "UF", "NM_UF": "NO_UF", "NM_REGIAO": "REGIAO"})
        mun = mun[["CO_MUNICIPIO", "NO_MUNICIPIO", "UF", "NO_UF", "REGIAO", "AREA_KM2"]]
    else:
        mun = dim[["CO_MUNICIPIO", "NO_MUNICIPIO", "UF", "REGIAO"]].drop_duplicates("CO_MUNICIPIO")
    ufs = pd.DataFrame([{"CO_UF": k, "UF": s, "NO_UF": n, "REGIAO": r} for k, (s, n, r) in UFS.items()])
    etapas = pd.DataFrame([{"ETAPA": k, "ETAPA_NOME": v[0], "NIVEL": v[1], "TIPO": v[2], "ORDEM": v[3]}
                           for k, v in ETAPAS.items()])

    log("## 5. Arquivos gerados")
    log()
    salvar(dim, args.saida, "dim_escola.csv")
    salvar(mun, args.saida, "dim_municipio.csv")
    salvar(ufs, args.saida, "dim_uf.csv")
    salvar(etapas, args.saida, "dim_etapa.csv")
    anos = pd.DataFrame({"ANO": range(2018, 2026)})
    anos["PERIODO"] = np.select([anos["ANO"] <= 2019, anos["ANO"] <= 2021], ["Pré-pandemia", "Pandemia"],
                                "Pós-pandemia")
    anos["TEM_ABANDONO"] = np.where(anos["ANO"] >= 2019, "Sim", "Não")
    anos["TEM_EVASAO"] = np.where(anos["ANO"] <= 2021, "Sim", "Não")
    salvar(anos, args.saida, "dim_ano.csv")
    salvar(fato, args.saida, "fato_rendimento_escola.csv")
    salvar(anom, args.saida, "fato_anomalias_2025.csv")
    salvar(f_mun, args.saida, "fato_municipio.csv")
    salvar(f_uf, args.saida, "fato_uf.csv")
    for nome in ["pnad_motivos_saida.csv", "pnad_idade_saida.csv"]:
        if b[nome] is not None:
            salvar(b[nome], args.saida, nome)
    escrever_dicionario(args.saida)
    (args.saida / "log_preprocessamento.md").write_text("\n".join(LOG), encoding="utf-8")
    print(f"\nPronto: {args.saida.resolve()}")


if __name__ == "__main__":
    main()
