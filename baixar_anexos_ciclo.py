"""Baixa os anexos dos títulos de um ciclo e monta UMA pasta só com os
documentos certos de cada título — a nota fiscal e o boleto, escolhidos pelo
CONTEÚDO do arquivo (não pelo nome nem pela ordem em que vieram do Sienge).

Medições, propostas, cotações e planilhas que o pessoal cola junto ficam de fora
da pasta final (mas continuam salvas em output/anexos/, caso precise conferir).

Uso:
    python baixar_anexos_ciclo.py            # último ciclo concluído
    python baixar_anexos_ciclo.py 54         # um ciclo específico
"""
import csv
import os
import re
import shutil
import sys

from loguru import logger

import config
from models import Titulo
from modules.attachment_reader import AttachmentReader
from modules.sienge_client import SiengeClient
from orchestrator import _ler_todos_anexos
from dashboard import database as db


def _slug(s, n=28):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s or "")).strip("_")[:n] or "X"


def main(exec_id=None):
    if exec_id is None:
        concl = [e for e in db.get_execucoes(limit=10) if e.status == "CONCLUIDO"]
        if not concl:
            logger.error("Nenhum ciclo concluído encontrado.")
            return
        exec_id = concl[0].id

    pags = db.get_pagamentos(exec_id)
    if not pags:
        logger.error(f"Ciclo #{exec_id} não tem títulos registrados.")
        return

    destino = os.path.join(config.OUTPUT_DIR, "anexos_corretos", f"ciclo_{exec_id}")
    os.makedirs(destino, exist_ok=True)
    logger.info(f"Ciclo #{exec_id}: {len(pags)} títulos. Pasta: {destino}")

    cli = SiengeClient(config.SIENGE_BASE_URL, config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
    rd = AttachmentReader()

    linhas = []
    n_nf = n_bol = n_sem = 0

    for i, p in enumerate(pags, 1):
        num = str(p.titulo_numero)
        try:
            titulo_id = int(re.sub(r"\D", "", num) or 0)
        except ValueError:
            continue
        if not titulo_id:
            continue

        cnpj = re.sub(r"\D", "", str(p.cnpj_credor or ""))
        t = Titulo(id=titulo_id, numero=num, parcela=str(p.parcela or "1"),
                   fornecedor_nome=p.fornecedor or "", fornecedor_cnpj=cnpj,
                   valor_nominal=0.0, valor_liquido=0.0, data_vencimento=None,
                   forma_pagamento=p.forma, status=None)

        pasta_bruta = os.path.join(config.OUTPUT_DIR, "anexos", f"{num}_{p.parcela or '1'}")
        anexos = cli.baixar_anexos_titulo(titulo_id, pasta_bruta)
        itens = anexos.get("anexos") or []
        if not itens:
            n_sem += 1
            linhas.append({"titulo": num, "parcela": p.parcela or "1",
                           "fornecedor": p.fornecedor or "", "anexos_no_sienge": 0,
                           "nota_fiscal": "SEM ANEXO", "boleto": "", "descartados": ""})
            logger.warning(f"[{i}/{len(pags)}] título {num}: sem anexos no Sienge")
            continue

        # a nota certa, escolhida pelo conteúdo
        escolhido = _ler_todos_anexos(rd, anexos, t)
        nf_path = (escolhido or {}).get("_nf_path") or anexos.get("nf_path")
        boleto_path = anexos.get("boleto_path")

        base = f"{num}-{p.parcela or '1'}_{_slug(p.fornecedor)}"
        nomes = {"nf": "", "boleto": ""}
        for origem, sufixo in ((nf_path, "NF"), (boleto_path, "BOLETO")):
            if not origem or not os.path.exists(origem):
                continue
            alvo = os.path.join(destino, f"{base}_{sufixo}{os.path.splitext(origem)[1] or '.pdf'}")
            try:
                shutil.copy2(origem, alvo)
                nomes[sufixo.lower()] = os.path.basename(alvo)
            except OSError as e:
                logger.warning(f"Falha ao copiar {origem}: {e}")

        if nomes["nf"]:
            n_nf += 1
        if nomes["boleto"]:
            n_bol += 1

        descartados = [a["nome"] for a in itens
                       if a.get("path") not in (nf_path, boleto_path)]
        linhas.append({"titulo": num, "parcela": p.parcela or "1",
                       "fornecedor": p.fornecedor or "", "anexos_no_sienge": len(itens),
                       "nota_fiscal": nomes["nf"] or "NAO IDENTIFICADA",
                       "boleto": nomes["boleto"],
                       "descartados": " | ".join(descartados)})

        if i % 20 == 0:
            logger.info(f"  {i}/{len(pags)} títulos · {n_nf} notas separadas")

    indice = os.path.join(destino, "_indice.csv")
    with open(indice, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["titulo", "parcela", "fornecedor",
                                          "anexos_no_sienge", "nota_fiscal",
                                          "boleto", "descartados"])
        w.writeheader()
        w.writerows(linhas)

    logger.success(
        f"CONCLUÍDO — {len(pags)} títulos | {n_nf} notas fiscais | {n_bol} boletos | "
        f"{n_sem} sem anexo no Sienge"
    )
    logger.success(f"Pasta: {destino}")
    logger.success(f"Índice: {indice}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(int(arg) if arg else None)
