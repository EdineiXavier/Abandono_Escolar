"""
Reduz o zip dos microdados do Censo Escolar para só as colunas usadas no painel.

Uso:
    python reduzir_censo.py CAMINHO/DO/microdados_censo_escolar_AAAA.zip

Gera, na mesma pasta, "microdados_censo_AAAA_reduzido.zip" (poucos MB), que pode
ser enviado no lugar do original. O script de exploração lê esse arquivo do mesmo
jeito que lê o zip completo.

Funciona com os dois formatos do INEP:
  - até 2024: uma tabela só           -> dados/microdados_ed_basica_AAAA.csv
  - 2025+:    tabelas separadas por tema (todas com uma linha por escola)
              Tabela_Escola_AAAA_V2.csv     -> localização, rede, infraestrutura
              Tabela_Matricula_AAAA_V2.csv  -> matrículas (agora também por série)
              Tabela_Docente_AAAA_V2.csv    -> docentes
              Tabela_Turma_AAAA_V2.csv      -> turmas (inclui turmas noturnas)
"""

import io
import re
import sys
import zipfile
from pathlib import Path

import pandas as pd

# Colunas que queremos no final (nomes do formato antigo).
COLUNAS = [
    "NU_ANO_CENSO", "CO_ENTIDADE", "CO_MUNICIPIO", "TP_DEPENDENCIA",
    "TP_LOCALIZACAO", "TP_SITUACAO_FUNCIONAMENTO",
    "QT_MAT_BAS", "QT_MAT_FUND", "QT_MAT_FUND_AI", "QT_MAT_FUND_AF", "QT_MAT_MED",
    "QT_MAT_MED_INT", "QT_DOC_MED", "QT_TUR_MED",
    "IN_INTERNET", "IN_INTERNET_ALUNOS", "IN_BANDA_LARGA", "IN_BIBLIOTECA",
    "IN_BIBLIOTECA_SALA_LEITURA", "IN_LABORATORIO_CIENCIAS",
    "IN_LABORATORIO_INFORMATICA", "IN_QUADRA_ESPORTES", "IN_AGUA_POTAVEL",
    "IN_ESGOTO_REDE_PUBLICA", "IN_ENERGIA_REDE_PUBLICA", "IN_NOTURNO",
]

# Formato novo: o que buscar em cada tabela temática (além de CO_ENTIDADE).
EXTRAS_2025 = {
    "Tabela_Matricula": ["QT_MAT_BAS", "QT_MAT_FUND", "QT_MAT_FUND_AI", "QT_MAT_FUND_AF",
                         *[f"QT_MAT_FUND_AI_{i}" for i in range(1, 6)],
                         *[f"QT_MAT_FUND_AF_{i}" for i in range(6, 10)],
                         "QT_MAT_MED", *[f"QT_MAT_MED_{i}" for i in range(1, 5)],
                         "QT_MAT_MED_NS", "QT_MAT_MED_INT"],
    "Tabela_Docente": ["QT_DOC_MED"],
    "Tabela_Turma": ["QT_TUR_MED", "QT_TUR_BAS_N"],
}

PADRAO_ESCOLA = re.compile(r"(microdados_ed_basica|Tabela_Escola)_(\d{4})(_v\d+)?\.csv$", re.I)


def detectar_formato(zf: zipfile.ZipFile, nome: str) -> tuple[str, str, list[str]]:
    """Codificação, separador e cabeçalho, olhando os primeiros 5 MB do arquivo."""
    with zf.open(nome) as f:
        amostra = f.read(5_000_000)
    try:
        amostra.decode("utf-8")
        enc = "utf-8-sig"
    except UnicodeDecodeError as e:
        # Se o erro for só no último caractere (cortado no meio), é UTF-8 mesmo.
        enc = "utf-8-sig" if e.start > len(amostra) - 4 else "latin-1"
    linha = amostra.split(b"\n", 1)[0].decode(enc, errors="replace").lstrip("﻿").strip()
    sep = ";" if linha.count(";") >= linha.count(",") else ","
    cabecalho = [c.strip().strip('"').upper() for c in linha.split(sep)]
    return enc, sep, cabecalho


def ler_colunas(zf: zipfile.ZipFile, nome: str, desejadas: list[str]) -> tuple[pd.DataFrame, list[str]]:
    enc, sep, cabecalho = detectar_formato(zf, nome)
    usar = [c for c in desejadas if c in cabecalho]
    with zf.open(nome) as f:
        df = pd.read_csv(io.TextIOWrapper(f, encoding=enc, errors="replace"), sep=sep,
                         low_memory=False, usecols=lambda c: c.strip().strip('"').upper() in usar)
    df.columns = [c.strip().strip('"').upper() for c in df.columns]
    print(f"   {Path(nome).name}: {len(df):,} linhas | {enc} | sep '{sep}' | "
          f"{len(usar)}/{len(desejadas)} colunas encontradas")
    return df, sorted(set(desejadas) - set(usar))


origem = Path(sys.argv[1])
zf = zipfile.ZipFile(origem)
nome_escola = next((n for n in zf.namelist() if PADRAO_ESCOLA.search(n)), None)
if nome_escola is None:
    sys.exit("Não achei a tabela de escolas dentro do zip. CSVs encontrados:\n"
             + "\n".join(n for n in zf.namelist() if n.lower().endswith(".csv")))
ano = PADRAO_ESCOLA.search(nome_escola).group(2)
print("Lendo as tabelas do Censo", ano)

df, faltando = ler_colunas(zf, nome_escola, COLUNAS)

# Formato novo: completa com as tabelas temáticas (uma linha por escola em todas).
for prefixo, cols in EXTRAS_2025.items():
    nome = next((n for n in zf.namelist()
                 if re.search(rf"{prefixo}_{ano}(_v\d+)?\.csv$", n, re.I)), None)
    if nome is None:
        continue
    extra, _ = ler_colunas(zf, nome, ["CO_ENTIDADE"] + cols)
    if extra["CO_ENTIDADE"].duplicated().any():
        print(f"   ⚠️ {prefixo} tem mais de uma linha por escola — somando por escola")
        extra = extra.groupby("CO_ENTIDADE", as_index=False).sum(numeric_only=True)
    novas = [c for c in extra.columns if c not in df.columns]
    df = df.merge(extra[["CO_ENTIDADE"] + novas], on="CO_ENTIDADE", how="left")

# Em 2025 não existe mais IN_NOTURNO: derivamos de "tem turma noturna".
if "IN_NOTURNO" not in df and "QT_TUR_BAS_N" in df:
    df["IN_NOTURNO"] = (df["QT_TUR_BAS_N"].fillna(0) > 0).astype(int)
    df = df.drop(columns="QT_TUR_BAS_N")

ainda_faltando = [c for c in COLUNAS if c not in df.columns]
if ainda_faltando:
    print("⚠️ Colunas que não existem neste ano (ficarão de fora):", ainda_faltando)

destino = origem.with_name(f"microdados_censo_{ano}_reduzido.zip")
with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
    # Nome e formato padronizados (o script de exploração espera estes).
    z.writestr(f"microdados_ed_basica_{ano}.csv",
               df.to_csv(sep=";", index=False).encode("latin-1", errors="replace"))

print(f"\n{len(df):,} escolas, {df.shape[1]} colunas -> {destino.name} "
      f"({destino.stat().st_size / 1e6:.1f} MB)")
