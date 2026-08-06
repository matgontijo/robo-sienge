"""Extrai TODOS os anexos dos títulos do Sienge de um período, organizados por
título, num diretório único, com um índice CSV. Uso:
    python extrair_arquivos_sienge.py 2026-06-01 2026-06-30
"""
import os
import sys
import csv
from datetime import date

import config
from modules.sienge_client import SiengeClient
from loguru import logger


def main(ini: date, fim: date):
    cli = SiengeClient(config.SIENGE_BASE_URL, config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
    pasta_raiz = os.path.join(config.OUTPUT_DIR, "arquivos_sienge", f"{ini:%Y-%m}")
    os.makedirs(pasta_raiz, exist_ok=True)

    titulos = cli.listar_titulos(ini, fim)
    logger.info(f"{len(titulos)} títulos emitidos de {ini} a {fim}. Baixando anexos...")

    indice = []
    tot_arquivos = 0
    tot_bytes = 0
    com_anexo = 0

    for i, t in enumerate(titulos, 1):
        pasta = os.path.join(pasta_raiz, str(t.id))
        res = cli.baixar_anexos_titulo(t.id, pasta)
        arquivos = res.get("anexos", [])
        if arquivos:
            com_anexo += 1
        for a in arquivos:
            try:
                tam = os.path.getsize(a["path"])
            except OSError:
                tam = 0
            tot_bytes += tam
            tot_arquivos += 1
            indice.append({
                "titulo": t.id,
                "documento": t.numero_documento or "",
                "arquivo": a["nome"],
                "tipo": a.get("tipo") or "",
                "tamanho_kb": round(tam / 1024, 1),
                "caminho": os.path.relpath(a["path"], config.OUTPUT_DIR),
            })
        if i % 25 == 0:
            logger.info(f"  {i}/{len(titulos)} títulos · {tot_arquivos} arquivos · "
                        f"{tot_bytes/1_048_576:.1f} MB")

    # índice CSV
    caminho_indice = os.path.join(pasta_raiz, "_indice.csv")
    with open(caminho_indice, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["titulo", "documento", "arquivo", "tipo", "tamanho_kb", "caminho"])
        w.writeheader()
        w.writerows(indice)

    logger.success(
        f"CONCLUÍDO: {tot_arquivos} arquivos de {com_anexo} títulos "
        f"({tot_bytes/1_048_576:.1f} MB) em {pasta_raiz}"
    )
    logger.success(f"Índice: {caminho_indice}")


if __name__ == "__main__":
    ini = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 6, 1)
    fim = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 6, 30)
    main(ini, fim)
