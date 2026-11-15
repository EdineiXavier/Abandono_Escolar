"""
Exploração das bases do Painel de BI: Abandono Escolar (INEP + IBGE)
=====================================================================

O que este script faz
---------------------
1. Lê os arquivos baixados direto dos .zip (não precisa descompactar).
2. Padroniza os nomes das colunas (o INEP mudou os códigos em 2021).
3. Converte os marcadores de dado ausente ("--") em nulo.
4. Faz checagens de qualidade (duplicatas, nulos, valores fora de 0-100,
   aprovação + reprovação + abandono = 100).
5. Mede a cobertura das junções (rendimento × INSE × TDI × Censo × malha).
6. Gera estatísticas descritivas e uma prévia do modelo "abandono esperado".
7. Salva um relatório (relatorio_exploracao.md), tabelas-resumo em CSV e a
   base de rendimento já padronizada (parquet) para as próximas etapas.

Como rodar
----------
    pip install pandas python-calamine pyarrow pyshp
    python explorar_dados.py --dados PASTA_COM_OS_ARQUIVOS --saida PASTA_DE_SAIDA

A pasta --dados pode conter os arquivos com qualquer prefixo no nome; o script
procura por padrões:
    *tx_rend_escolas_AAAA*.zip     (Taxas de Rendimento, um por ano)
    *TDI*ESCOLAS*.zip              (Distorção idade-série)
    *INSE*escolas*.xlsx            (INSE 2023)
    *microdados*censo*.zip         (Censo Escolar - 2022, e 2025 quando chegar)
    *Municipios*.zip               (Malha municipal do IBGE)
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

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

ANOS = list(range(2019, 2026))
PERIODO = {2019: "Pré-pandemia", 2020: "Pandemia", 2021: "Pandemia"}  # demais: Pós-pandemia

# As 63 colunas das planilhas de rendimento estão SEMPRE na mesma ordem
# (2019 a 2025). Só os códigos mudaram (ex.: tab_MED -> 3_CAT_MED). Por isso
# renomeamos por posição, com nomes nossos e legíveis.
COLS_ID = ["ANO", "REGIAO", "UF", "CO_MUNICIPIO", "NO_MUNICIPIO",
           "CO_ESCOLA", "NO_ESCOLA", "LOCALIZACAO", "DEPENDENCIA"]
ETAPAS = ["FUN", "FUN_AI", "FUN_AF"] + [f"FUN_{i:02d}" for i in range(1, 10)] \
         + ["MED"] + [f"MED_{i:02d}" for i in range(1, 5)] + ["MED_NS"]
INDICADORES = ["APR", "REP", "ABA"]  # aprovação, reprovação, abandono
COLS_REND = COLS_ID + [f"{ind}_{et}" for ind in INDICADORES for et in ETAPAS]
assert len(COLS_REND) == 63

COLS_TDI = COLS_ID + [f"TDI_{et}" for et in ["FUN", "FUN_AI", "FUN_AF"]
                      + [f"FUN_{i:02d}" for i in range(1, 10)]
                      + ["MED"] + [f"MED_{i:02d}" for i in range(1, 5)]]
assert len(COLS_TDI) == 26

# Colunas do Censo Escolar (tabela de escolas) que interessam ao painel.
COLS_CENSO = [
    "NU_ANO_CENSO", "CO_ENTIDADE", "CO_MUNICIPIO", "TP_DEPENDENCIA",
    "TP_LOCALIZACAO", "TP_SITUACAO_FUNCIONAMENTO",
    # porte
    "QT_MAT_BAS", "QT_MAT_FUND", "QT_MAT_FUND_AI", "QT_MAT_FUND_AF", "QT_MAT_MED",
    "QT_MAT_MED_INT", "QT_DOC_MED", "QT_TUR_MED",
    # infraestrutura
    "IN_INTERNET", "IN_INTERNET_ALUNOS", "IN_BANDA_LARGA", "IN_BIBLIOTECA",
    "IN_BIBLIOTECA_SALA_LEITURA", "IN_LABORATORIO_CIENCIAS",
    "IN_LABORATORIO_INFORMATICA", "IN_QUADRA_ESPORTES", "IN_AGUA_POTAVEL",
    "IN_ESGOTO_REDE_PUBLICA", "IN_ENERGIA_REDE_PUBLICA", "IN_NOTURNO",
]
INFRA = [c for c in COLS_CENSO if c.startswith("IN_")]

FAIXAS_PORTE = [0, 50, 150, 300, 600, np.inf]
ROTULOS_PORTE = ["<50", "50-149", "150-299", "300-599", "600+"]

# ---------------------------------------------------------------------------
# Relatório: tudo que é impresso também vai para o .md
# ---------------------------------------------------------------------------

_LINHAS: list[str] = []


def out(texto: str = "") -> None:
    print(texto)
    _LINHAS.append(texto)


def titulo(texto: str, nivel: int = 2) -> None:
    out()
    out("#" * nivel + " " + texto)
    out()


def tabela(df: pd.DataFrame | pd.Series, decimais: int = 2) -> None:
    if isinstance(df, pd.Series):
        df = df.to_frame()
    out(df.round(decimais).to_markdown())
    out()


# ---------------------------------------------------------------------------
# Leitura dos arquivos
# ---------------------------------------------------------------------------

def achar(pasta: Path, padrao: str) -> list[Path]:
    return sorted(p for p in pasta.rglob(padrao) if p.is_file())


def membro_do_zip(caminho_zip: Path, regex: str) -> tuple[zipfile.ZipFile, str]:
    """Abre o zip e devolve o nome do primeiro arquivo interno que casa com o regex."""
    zf = zipfile.ZipFile(caminho_zip)
    nomes = [n for n in zf.namelist()
             if re.search(regex, n, re.I) and "~$" not in n and not n.endswith("/")]
    if not nomes:
        raise FileNotFoundError(f"Nenhum arquivo '{regex}' dentro de {caminho_zip.name}")
    return zf, nomes[0]


def ler_planilha_inep(fonte, colunas: list[str]) -> pd.DataFrame:
    """Planilhas de indicadores do INEP: 9 linhas de cabeçalho + rodapé com a fonte."""
    df = pd.read_excel(fonte, header=None, skiprows=9, engine="calamine")
    if df.shape[1] != len(colunas):
        raise ValueError(f"Esperava {len(colunas)} colunas, veio {df.shape[1]}")
    df.columns = colunas
    # Rodapé ("Fonte: ...") e linhas vazias: código da escola não numérico.
    df = df[pd.to_numeric(df["CO_ESCOLA"], errors="coerce").notna()].copy()
    # "--" = indicador não se aplica / sem matrícula naquela etapa -> nulo.
    medidas = colunas[len(COLS_ID):]
    df[medidas] = df[medidas].replace("--", np.nan).apply(pd.to_numeric, errors="coerce")
    for c in ["ANO", "CO_MUNICIPIO", "CO_ESCOLA"]:
        df[c] = pd.to_numeric(df[c]).astype("int64")
    for c in ["REGIAO", "UF", "NO_MUNICIPIO", "NO_ESCOLA", "LOCALIZACAO", "DEPENDENCIA"]:
        df[c] = df[c].astype(str).str.strip()
    return df


def ler_rendimento(pasta: Path) -> pd.DataFrame:
    partes = []
    for ano in ANOS:
        zips = achar(pasta, f"*tx_rend_escolas_{ano}*.zip")
        xlsx = achar(pasta, f"*tx_rend_escolas_{ano}*.xlsx")
        if zips:
            zf, nome = membro_do_zip(zips[0], r"\.xlsx$")
            fonte = io.BytesIO(zf.read(nome))
        elif xlsx:
            fonte = xlsx[0]
        else:
            out(f"- ⚠️ Rendimento {ano}: arquivo não encontrado")
            continue
        df = ler_planilha_inep(fonte, COLS_REND)
        out(f"- Rendimento {ano}: {len(df):,} escolas")
        partes.append(df)
    return pd.concat(partes, ignore_index=True)


def ler_tdi(pasta: Path) -> pd.DataFrame | None:
    zips = achar(pasta, "*TDI*ESCOLAS*.zip")
    if not zips:
        out("- ⚠️ TDI: arquivo não encontrado")
        return None
    zf, nome = membro_do_zip(zips[0], r"\.xlsx$")
    df = ler_planilha_inep(io.BytesIO(zf.read(nome)), COLS_TDI)
    out(f"- TDI {df['ANO'].iloc[0]}: {len(df):,} escolas")
    return df


def ler_inse(pasta: Path) -> pd.DataFrame | None:
    arqs = achar(pasta, "*INSE*scola*.xlsx") or achar(pasta, "*inse*.xlsx")
    if not arqs:
        out("- ⚠️ INSE: arquivo não encontrado")
        return None
    df = pd.read_excel(arqs[0], engine="calamine")
    df = df.rename(columns={"ID_ESCOLA": "CO_ESCOLA"})
    out(f"- INSE {df['NU_ANO_SAEB'].iloc[0]}: {len(df):,} escolas")
    return df


def ler_censo(pasta: Path) -> dict[int, pd.DataFrame]:
    """Tabela de escolas do Censo. Lê só as colunas úteis (o CSV é grande)."""
    resultado = {}
    for z in achar(pasta, "*microdados*censo*.zip") + achar(pasta, "*microdados*ed*basica*.zip"):
        # Até 2024: microdados_ed_basica_AAAA.csv | 2025+: Tabela_Escola_AAAA_V2.csv
        try:
            zf, nome = membro_do_zip(z, r"(microdados_ed_basica|Tabela_Escola)_\d{4}(_v\d+)?\.csv$")
        except FileNotFoundError:
            continue
        ano = int(re.search(r"_(\d{4})(_v\d+)?\.csv$", nome, re.I).group(1))
        if ano in resultado:
            continue
        with zf.open(nome) as f:
            amostra = f.read(5_000_000)  # o cabeçalho sozinho não denuncia acentos
        try:
            amostra.decode("utf-8")
            enc = "utf-8-sig"
        except UnicodeDecodeError as e:
            enc = "utf-8-sig" if e.start > len(amostra) - 4 else "latin-1"
        linha = amostra.split(b"\n", 1)[0].decode(enc).lstrip("﻿").strip()
        sep = ";" if linha.count(";") >= linha.count(",") else ","
        cabecalho = [c.strip().strip('"').upper() for c in linha.split(sep)]
        # + matrículas por série (existem a partir de 2025)
        usar = [c for c in cabecalho if c in COLS_CENSO or re.match(r"QT_MAT_(FUND_A[IF]|MED)_\d$", c)]
        faltando = sorted(set(COLS_CENSO) - set(usar))
        with zf.open(nome) as f:
            df = pd.read_csv(io.TextIOWrapper(f, encoding=enc, errors="replace"), sep=sep, low_memory=False,
                             usecols=lambda c: c.strip().strip('"').upper() in usar)
        df.columns = [c.strip().strip('"').upper() for c in df.columns]
        df = df.rename(columns={"CO_ENTIDADE": "CO_ESCOLA", "NU_ANO_CENSO": "ANO"})
        out(f"- Censo Escolar {ano}: {len(df):,} escolas, {len(usar)} colunas"
            + (f" (ausentes neste ano: {', '.join(faltando)})" if faltando else ""))
        resultado[ano] = df
    if not resultado:
        out("- ⚠️ Censo Escolar: nenhum arquivo encontrado")
    return resultado


def ler_malha(pasta: Path) -> pd.DataFrame | None:
    """Lê só a tabela de atributos (.dbf) da malha: basta para checar os códigos."""
    zips = achar(pasta, "*Municipios*.zip")
    if not zips:
        out("- ⚠️ Malha municipal: arquivo não encontrado")
        return None
    try:
        import shapefile  # pyshp
    except ImportError:
        out("- ⚠️ Malha: instale 'pyshp' (pip install pyshp) para ler o .dbf")
        return None
    zf, nome = membro_do_zip(zips[0], r"\.dbf$")
    r = shapefile.Reader(dbf=io.BytesIO(zf.read(nome)), encoding="utf-8")
    campos = [f[0] for f in r.fields[1:]]
    df = pd.DataFrame(r.records(), columns=campos)
    df["CD_MUN"] = df["CD_MUN"].astype("int64")
    out(f"- Malha municipal IBGE: {len(df):,} municípios")
    return df


# ---------------------------------------------------------------------------
# Análises
# ---------------------------------------------------------------------------

def qualidade_rendimento(rend: pd.DataFrame) -> None:
    titulo("1. Qualidade da base de Taxas de Rendimento")

    dup = rend.duplicated(["ANO", "CO_ESCOLA"]).sum()
    out(f"- Duplicatas (ano + escola): **{dup}**")

    medidas = [c for c in rend.columns if c[:4] in ("APR_", "REP_", "ABA_")]
    fora = ((rend[medidas] < 0) | (rend[medidas] > 100)).sum().sum()
    out(f"- Valores fora do intervalo 0–100: **{fora}**")

    # Aprovação + reprovação + abandono deveria dar 100 (com arredondamento).
    for et in ["FUN", "MED"]:
        soma = rend[[f"APR_{et}", f"REP_{et}", f"ABA_{et}"]].sum(axis=1, min_count=3)
        desvio = (soma - 100).abs()
        ruins = (desvio > 0.5).sum()
        out(f"- {et}: escolas em que APR+REP+ABA difere de 100 em mais de 0,5 p.p.: "
            f"**{ruins}** de {soma.notna().sum():,}")

    out()
    out("Escolas por ano e % de nulos ('--') no abandono (nulo = escola não oferta a etapa):")
    out()
    r = rend.groupby("ANO").agg(
        escolas=("CO_ESCOLA", "size"),
        nulo_ABA_FUN=("ABA_FUN", lambda s: s.isna().mean() * 100),
        nulo_ABA_MED=("ABA_MED", lambda s: s.isna().mean() * 100),
        nulo_ABA_MED_01=("ABA_MED_01", lambda s: s.isna().mean() * 100),
    )
    tabela(r, 1)

    out("Categorias de localização e dependência (conferir se batem entre os anos):")
    out()
    tabela(pd.crosstab(rend["ANO"], rend["DEPENDENCIA"]), 0)
    tabela(pd.crosstab(rend["ANO"], rend["LOCALIZACAO"]), 0)


def panorama(rend: pd.DataFrame, saida: Path) -> None:
    titulo("2. Panorama do abandono (médias simples entre escolas)")
    out("> Atenção: sem as matrículas, as médias abaixo são **simples** (cada escola pesa "
        "igual). Depois da junção com o Censo, dá para ponderar por matrícula e chegar "
        "perto dos números oficiais do INEP.")
    out()

    pub = rend[rend["DEPENDENCIA"] != "Privada"]
    evol = pub.groupby("ANO").agg(
        ABA_FUN_media=("ABA_FUN", "mean"), ABA_FUN_mediana=("ABA_FUN", "median"),
        ABA_MED_media=("ABA_MED", "mean"), ABA_MED_mediana=("ABA_MED", "median"),
        pct_escolas_MED_com_zero=("ABA_MED", lambda s: (s == 0).mean() / s.notna().mean() * 100),
        REP_MED_media=("REP_MED", "mean"),
    )
    evol.insert(0, "PERIODO", [PERIODO.get(a, "Pós-pandemia") for a in evol.index])
    out("**Rede pública — evolução 2019–2025**")
    out()
    tabela(evol)
    evol.to_csv(saida / "tabelas" / "evolucao_abandono_rede_publica.csv")

    ult = rend["ANO"].max()
    r = rend[rend["ANO"] == ult]

    out(f"**Abandono médio por série — {ult} (rede pública)**")
    out()
    series = [f"FUN_{i:02d}" for i in range(1, 10)] + [f"MED_{i:02d}" for i in range(1, 5)]
    rp = r[r["DEPENDENCIA"] != "Privada"]
    perfil = pd.DataFrame({
        "abandono": [rp[f"ABA_{s}"].mean() for s in series],
        "reprovacao": [rp[f"REP_{s}"].mean() for s in series],
        "escolas_com_dado": [rp[f"ABA_{s}"].notna().sum() for s in series],
    }, index=series)
    tabela(perfil)
    perfil.to_csv(saida / "tabelas" / f"abandono_por_serie_{ult}.csv")

    out(f"**Ensino médio — {ult}: por dependência e localização**")
    out()
    tabela(r.pivot_table(index="DEPENDENCIA", columns="LOCALIZACAO",
                         values="ABA_MED", aggfunc="mean"))

    out(f"**Ensino médio — {ult}: UFs com maior abandono (rede pública)**")
    out()
    uf = rp.groupby("UF")["ABA_MED"].agg(["mean", "median", "count"]).sort_values("mean", ascending=False)
    tabela(uf.head(10))
    uf.to_csv(saida / "tabelas" / f"abandono_medio_por_uf_{ult}.csv")

    # Variação 2022 -> último ano, só escolas presentes nos dois anos.
    a = rend[rend["ANO"] == 2022].set_index("CO_ESCOLA")["ABA_MED"]
    b = rend[rend["ANO"] == ult].set_index("CO_ESCOLA")["ABA_MED"]
    painel = pd.concat({"y2022": a, f"y{ult}": b}, axis=1).dropna()
    var = painel[f"y{ult}"] - painel["y2022"]
    out(f"**Variação do abandono no ensino médio 2022 → {ult}** "
        f"({len(painel):,} escolas com dado nos dois anos)")
    out()
    out(f"- Melhoraram (caiu > 1 p.p.): {(var < -1).mean() * 100:.1f}%")
    out(f"- Estáveis (±1 p.p.): {(var.abs() <= 1).mean() * 100:.1f}%")
    out(f"- Pioraram (subiu > 1 p.p.): {(var > 1).mean() * 100:.1f}%")
    out()


def juncoes(rend, tdi, inse, censo, malha, saida: Path) -> pd.DataFrame:
    titulo("3. Cobertura das junções (chave: código INEP da escola)")
    ult = rend["ANO"].max()
    base = rend[(rend["ANO"] == ult) & (rend["DEPENDENCIA"] != "Privada")
                & rend["ABA_MED"].notna()].copy()
    out(f"Base de referência: escolas **públicas com ensino médio** em {ult}: **{len(base):,}**")
    out()

    linhas = []
    if inse is not None:
        base = base.merge(inse[["CO_ESCOLA", "MEDIA_INSE", "INSE_CLASSIFICACAO", "QTD_ALUNOS_INSE"]],
                          on="CO_ESCOLA", how="left")
        linhas.append(("INSE 2023", base["MEDIA_INSE"].notna().mean() * 100))
    if tdi is not None:
        base = base.merge(tdi[["CO_ESCOLA", "TDI_MED", "TDI_FUN"]], on="CO_ESCOLA", how="left")
        linhas.append(("TDI (distorção idade-série)", base["TDI_MED"].notna().mean() * 100))
    if censo:
        ano_c = ult if ult in censo else max(censo)
        c = censo[ano_c]
        base = base.merge(c.drop(columns=["ANO", "CO_MUNICIPIO", "TP_DEPENDENCIA", "TP_LOCALIZACAO"],
                                 errors="ignore"), on="CO_ESCOLA", how="left")
        linhas.append((f"Censo Escolar {ano_c} (matrículas/infra)",
                       base["QT_MAT_MED"].notna().mean() * 100))
        if ano_c != ult:
            out(f"> Censo {ult} ainda não carregado: usando Censo {ano_c} provisoriamente. "
                f"Escolas que abriram depois de {ano_c} ficam sem porte/infra.")
            out()
    if malha is not None:
        ok = rend["CO_MUNICIPIO"].isin(malha["CD_MUN"])
        linhas.append(("Malha IBGE (município da escola encontrado)", ok.mean() * 100))
        sem = rend.loc[~ok, "CO_MUNICIPIO"].unique()
        if len(sem):
            out(f"> Municípios do INEP sem correspondência na malha: {list(sem)[:10]}")
            out()

    tabela(pd.DataFrame(linhas, columns=["base", "% de escolas encontradas"]).set_index("base"), 1)

    if inse is not None:
        out("**Quem fica sem INSE?** (% sem INSE por dependência e localização)")
        out()
        tabela(base.assign(sem_inse=base["MEDIA_INSE"].isna() * 100)
               .pivot_table(index="DEPENDENCIA", columns="LOCALIZACAO", values="sem_inse"), 1)
    return base


def contexto(base: pd.DataFrame, saida: Path) -> None:
    titulo("4. Abandono no ensino médio × contexto (escolas públicas, último ano)")

    if "MEDIA_INSE" in base:
        ordem = ["Nível I", "Nível II", "Nível III", "Nível IV", "Nível V", "Nível VI", "Nível VII"]
        t = (base.groupby("INSE_CLASSIFICACAO")["ABA_MED"]
             .agg(media="mean", mediana="median", desvio="std", escolas="count")
             .reindex(ordem).dropna(how="all"))
        out("**Por nível de INSE**")
        out()
        tabela(t)
        t.to_csv(saida / "tabelas" / "abandono_por_inse.csv")
        out(f"- Correlação INSE × abandono: **{base['MEDIA_INSE'].corr(base['ABA_MED']):.2f}**")
    if "TDI_MED" in base:
        out(f"- Correlação distorção idade-série × abandono: "
            f"**{base['TDI_MED'].corr(base['ABA_MED']):.2f}**")
    if "REP_MED" in base:
        out(f"- Correlação reprovação × abandono: "
            f"**{base['REP_MED'].corr(base['ABA_MED']):.2f}**")
    out()

    if "QT_MAT_MED" in base:
        base["PORTE_MED"] = pd.cut(base["QT_MAT_MED"], FAIXAS_PORTE, labels=ROTULOS_PORTE, right=False)
        t = base.groupby("PORTE_MED", observed=True)["ABA_MED"].agg(
            escolas="count", media="mean", desvio="std",
            pct_zero=lambda s: (s == 0).mean() * 100, p95=lambda s: s.quantile(.95))
        out("**Por porte (matrículas no ensino médio)** — repare no desvio: escolas "
            "pequenas oscilam muito, por isso o filtro de porte mínimo")
        out()
        tabela(t)
        t.to_csv(saida / "tabelas" / "abandono_por_porte.csv")

    infra = [c for c in INFRA if c in base]
    if infra:
        linhas = []
        for c in infra:
            g = base.groupby(c)["ABA_MED"].mean()
            linhas.append({"item": c, "% escolas com o item": base[c].mean() * 100,
                           "abandono COM": g.get(1, np.nan), "abandono SEM": g.get(0, np.nan)})
        t = pd.DataFrame(linhas).set_index("item")
        t["diferença (SEM − COM)"] = t["abandono SEM"] - t["abandono COM"]
        out("**Por item de infraestrutura** (associação, não causa)")
        out()
        tabela(t.sort_values("diferença (SEM − COM)", ascending=False))
        t.to_csv(saida / "tabelas" / "abandono_por_infraestrutura.csv")


def previa_modelo(base: pd.DataFrame, saida: Path) -> None:
    titulo("5. Prévia do modelo de 'abandono esperado'")
    out("Regressão linear simples (mínimos quadrados) do abandono no ensino médio. "
        "Serve só para ver **quanto o contexto explica** — o modelo final vem na próxima etapa.")
    out()

    d = base.copy()
    if "QT_MAT_MED" in d:
        d = d[d["QT_MAT_MED"] >= 50]  # tira escolas pequenas e instáveis
    d["RURAL"] = (d["LOCALIZACAO"] == "Rural").astype(int)
    d["MUNICIPAL"] = (d["DEPENDENCIA"] == "Municipal").astype(int)
    d["FEDERAL"] = (d["DEPENDENCIA"] == "Federal").astype(int)

    especificacoes = {
        "só INSE": ["MEDIA_INSE"],
        "INSE + distorção idade-série": ["MEDIA_INSE", "TDI_MED"],
        "INSE + TDI + rural + rede": ["MEDIA_INSE", "TDI_MED", "RURAL", "MUNICIPAL", "FEDERAL"],
    }
    linhas = []
    for nome, xs in especificacoes.items():
        xs = [x for x in xs if x in d]
        m = d.dropna(subset=xs + ["ABA_MED"])
        if len(m) < 100:
            continue
        X = np.column_stack([np.ones(len(m))] + [m[x].to_numpy(float) for x in xs])
        y = m["ABA_MED"].to_numpy(float)
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        r2 = 1 - resid.var() / y.var()
        z = (resid - resid.mean()) / resid.std()
        linhas.append({"modelo": nome, "escolas": len(m), "R²": r2,
                       "anomalias |z|>2 (%)": (np.abs(z) > 2).mean() * 100,
                       "coeficientes": ", ".join(f"{n}={c:.2f}" for n, c in zip(["const"] + xs, coef))})
    t = pd.DataFrame(linhas).set_index("modelo")
    tabela(t, 3)
    t.to_csv(saida / "tabelas" / "previa_modelo.csv")
    out("> Como ler: R² baixo significa que muito do abandono **não** é explicado pelo "
        "contexto socioeconômico — é justamente isso que torna a página de anomalias "
        "interessante (escolas parecidas, resultados diferentes).")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dados", type=Path, default=Path("dados"), help="pasta com os arquivos baixados")
    ap.add_argument("--saida", type=Path, default=Path("saida"), help="pasta para relatório e tabelas")
    args = ap.parse_args()

    (args.saida / "tabelas").mkdir(parents=True, exist_ok=True)

    out("# Relatório de exploração — Painel de BI: Abandono Escolar")
    titulo("0. Arquivos lidos")
    rend = ler_rendimento(args.dados)
    tdi = ler_tdi(args.dados)
    inse = ler_inse(args.dados)
    censo = ler_censo(args.dados)
    malha = ler_malha(args.dados)

    qualidade_rendimento(rend)
    panorama(rend, args.saida)
    base = juncoes(rend, tdi, inse, censo, malha, args.saida)
    contexto(base, args.saida)
    previa_modelo(base, args.saida)

    # Base padronizada para as próximas etapas (pré-processamento e Power BI).
    rend.to_parquet(args.saida / "rendimento_escolas_2019_2025.parquet", index=False)
    (args.saida / "relatorio_exploracao.md").write_text("\n".join(_LINHAS), encoding="utf-8")
    print(f"\nPronto. Relatório e tabelas em: {args.saida.resolve()}")


if __name__ == "__main__":
    sys.exit(main())
