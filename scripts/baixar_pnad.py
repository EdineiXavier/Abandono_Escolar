"""
Baixa da API do IBGE (SIDRA) as tabelas da PNAD Contínua - Educação usadas no painel.

Uso:
    python baixar_pnad.py --saida PASTA

Gera três CSVs:
  pnad_escolarizacao_15a17_uf.csv  -> % de jovens de 15 a 17 anos que frequentam escola,
                                      por UF e ano (tabela 7138). 100 - taxa = fora da escola.
  pnad_motivos_saida.csv           -> principal motivo de ter deixado (ou nunca frequentado)
                                      a escola, jovens de 14 a 29 anos sem ensino médio,
                                      por sexo, Brasil e regiões (tabela 7217).
  pnad_idade_saida.csv             -> idade em que deixou a escola pela última vez,
                                      por sexo, Brasil e regiões (tabela 7215).

Observação: o módulo Educação da PNAD não foi a campo em 2020 e 2021 (pandemia).
"""

import argparse
import json
import time
import urllib.request
from pathlib import Path

import pandas as pd

API = "https://apisidra.ibge.gov.br/values"

CONSULTAS = {
    # nome do arquivo: (caminho da consulta, renomear colunas)
    "pnad_escolarizacao_15a17_uf.csv": (
        "/t/7138/n3/all/p/all/v/10276,10277/c58/all/c2/6794",
        {"D1C": "CO_UF", "D1N": "UF_NOME", "D2C": "ANO", "D3N": "VARIAVEL", "D4N": "GRUPO_IDADE"},
    ),
    "pnad_motivos_saida.csv": (
        "/t/7217/n1/all/n2/all/p/all/v/10404,10405/c2/all/c893/all",
        {"D1C": "CO_TERRITORIO", "D1N": "TERRITORIO", "D2C": "ANO", "D3N": "VARIAVEL",
         "D4N": "SEXO", "D5N": "MOTIVO"},
    ),
    "pnad_idade_saida.csv": (
        "/t/7215/n1/all/n2/all/p/all/v/10400,10401/c2/all/c897/all",
        {"D1C": "CO_TERRITORIO", "D1N": "TERRITORIO", "D2C": "ANO", "D3N": "VARIAVEL",
         "D4N": "SEXO", "D5N": "IDADE_SAIDA"},
    ),
}


def baixar(caminho: str, tentativas: int = 5) -> pd.DataFrame:
    url = API + caminho + "/f/a"
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                dados = json.load(r)
            df = pd.DataFrame(dados[1:])  # primeira linha = rótulos
            df["V"] = pd.to_numeric(df["V"], errors="coerce")  # "..", "-" -> nulo
            return df
        except Exception as e:  # a API do IBGE às vezes recusa por excesso de pedidos
            espera = 10 * (i + 1)
            print(f"   falhou ({e}); tentando de novo em {espera}s")
            time.sleep(espera)
    raise RuntimeError(f"Não consegui baixar {url}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", type=Path, default=Path("dados/pnad"))
    args = ap.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)

    for arquivo, (caminho, nomes) in CONSULTAS.items():
        print("Baixando", arquivo)
        df = baixar(caminho)
        # Uma coluna por variável (valor e coeficiente de variação).
        df = df.rename(columns=nomes)
        chaves = [c for c in nomes.values() if c != "VARIAVEL"]
        larga = df.pivot_table(index=chaves, columns="VARIAVEL", values="V", aggfunc="first").reset_index()
        larga.columns.name = None
        larga = larga.rename(columns=lambda c: "CV_%" if str(c).startswith("Coeficiente") else
                             ("VALOR" if c not in chaves else c))
        larga["ANO"] = larga["ANO"].astype(int)
        larga.to_csv(args.saida / arquivo, index=False, encoding="utf-8")
        print(f"   {len(larga):,} linhas, anos {sorted(larga['ANO'].unique())}")
        time.sleep(8)


if __name__ == "__main__":
    main()
