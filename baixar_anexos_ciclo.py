"""Baixa os anexos dos títulos de um ciclo e monta UMA pasta com os documentos
de cada título. A nota fiscal e o boleto são escolhidos pelo CONTEÚDO do arquivo
(não pelo nome nem pela ordem em que vieram do Sienge).

Dois modos:

  padrão    -> pasta output/anexos_corretos/ciclo_N com SÓ a NF e o boleto DA
               PARCELA que está sendo paga. Como o anexo no Sienge fica preso ao
               título, um título em 12x traz os 12 boletos juntos: o boleto certo
               é escolhido cruzando linha digitável / vencimento / valor da
               parcela. Medições, propostas e boletos das outras parcelas ficam
               de fora (seguem salvos em output/anexos/).
               Quando a parcela não pôde ser identificada, o título leva TODOS os
               anexos e sai marcado como CONFERIR no _indice.csv.

  --todos   -> pasta output/anexos_completos/ciclo_N com TODOS os anexos de cada
               título, cada arquivo marcado como NF, BOLETO ou ANEXO no nome.

Uso:
    python baixar_anexos_ciclo.py                # último ciclo, só NF + boleto
    python baixar_anexos_ciclo.py 54             # ciclo 54, só NF + boleto
    python baixar_anexos_ciclo.py --todos        # último ciclo, todos os anexos
    python baixar_anexos_ciclo.py 54 --todos     # ciclo 54, todos os anexos
"""
import csv
import os
import re
import shutil
import sys

from loguru import logger

import config
from models import Titulo
from models.info_pagamento import InfoPagamento
from modules.attachment_reader import AttachmentReader
from modules.sienge_client import SiengeClient
from orchestrator import _ler_todos_anexos, _boleto_da_parcela
from dashboard import database as db


def _slug(s, n=28):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(s or "")).strip("_")[:n] or "X"


def _data(valor):
    """Vencimento vem como texto do banco; devolve date ou None."""
    from datetime import date, datetime
    if isinstance(valor, date):
        return valor
    txt = str(valor or "")[:10]
    try:
        return datetime.strptime(txt, "%Y-%m-%d").date()
    except ValueError:
        return None


def main(exec_id=None, todos=False):
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

    sub = "anexos_completos" if todos else "anexos_corretos"
    destino = os.path.join(config.OUTPUT_DIR, sub, f"ciclo_{exec_id}")
    os.makedirs(destino, exist_ok=True)
    modo = "TODOS os anexos" if todos else "só a NF e o boleto"
    logger.info(f"Ciclo #{exec_id}: {len(pags)} títulos ({modo}). Pasta: {destino}")

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
        venc = _data(p.vencimento)
        t = Titulo(id=titulo_id, numero=num, parcela=str(p.parcela or "1"),
                   fornecedor_nome=p.fornecedor or "", fornecedor_cnpj=cnpj,
                   valor_nominal=p.valor or 0.0, valor_liquido=p.valor or 0.0,
                   data_vencimento=venc, forma_pagamento=p.forma, status=None)

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

        # o boleto DESTA parcela: o anexo fica preso ao título, então cruzamos com
        # a linha digitável / vencimento / valor cadastrados na parcela
        info = InfoPagamento(parcela=str(p.parcela or "") or None, valor=p.valor,
                             vencimento=venc, linha_digitavel=p.linha_digitavel)
        sel = _boleto_da_parcela(rd, anexos, t, info) or {}
        situacao = sel.get("situacao")
        criterio = sel.get("criterio") or "nao avaliado"
        boleto_path = sel.get("path") if situacao == "identificado" else None
        # Sem conseguir atribuir o boleto à parcela: leva todos e sinaliza.
        ambiguo = situacao in (None, "ambiguo")

        base = f"{num}-{p.parcela or '1'}_{_slug(p.fornecedor)}"
        nomes = {"nf": "", "boleto": ""}
        copiados = []

        if todos or ambiguo:
            # Todos os anexos, com o papel identificado no nome do arquivo.
            origens = [a.get("path") for a in itens if a.get("path")]
            for extra in (nf_path, boleto_path):
                if extra and extra not in origens:
                    origens.append(extra)
            for idx, origem in enumerate(origens, start=1):
                if not os.path.exists(origem):
                    continue
                papeis = [r for r, o in (("NF", nf_path), ("BOLETO", boleto_path)) if origem == o]
                marca = "-".join(papeis) if papeis else "ANEXO"
                nome_orig = _slug(os.path.splitext(os.path.basename(origem))[0], 30)
                ext = os.path.splitext(origem)[1] or ".pdf"
                alvo = os.path.join(destino, f"{base}_{idx:02d}_{marca}_{nome_orig}{ext}")
                try:
                    shutil.copy2(origem, alvo)
                except OSError as e:
                    logger.warning(f"Falha ao copiar {origem}: {e}")
                    continue
                copiados.append(os.path.basename(alvo))
                if origem == nf_path and not nomes["nf"]:
                    nomes["nf"] = os.path.basename(alvo)
                if origem == boleto_path and not nomes["boleto"]:
                    nomes["boleto"] = os.path.basename(alvo)
        else:
            for origem, sufixo in ((nf_path, "NF"), (boleto_path, "BOLETO")):
                if not origem or not os.path.exists(origem):
                    continue
                alvo = os.path.join(destino, f"{base}_{sufixo}{os.path.splitext(origem)[1] or '.pdf'}")
                try:
                    shutil.copy2(origem, alvo)
                    nomes[sufixo.lower()] = os.path.basename(alvo)
                    copiados.append(os.path.basename(alvo))
                except OSError as e:
                    logger.warning(f"Falha ao copiar {origem}: {e}")

        if nomes["nf"]:
            n_nf += 1
        if nomes["boleto"]:
            n_bol += 1

        descartados = ([] if (todos or ambiguo) else
                       [a["nome"] for a in itens
                        if a.get("path") not in (nf_path, boleto_path)])
        linhas.append({"titulo": num, "parcela": p.parcela or "1",
                       "fornecedor": p.fornecedor or "", "anexos_no_sienge": len(itens),
                       "nota_fiscal": nomes["nf"] or "NAO IDENTIFICADA",
                       "boleto": nomes["boleto"] or ("CONFERIR" if ambiguo else ""),
                       "como_achou_o_boleto": criterio,
                       "copiados": " | ".join(copiados),
                       "descartados": " | ".join(descartados)})

        if i % 20 == 0:
            logger.info(f"  {i}/{len(pags)} títulos · {n_nf} notas separadas")

    indice = os.path.join(destino, "_indice.csv")
    with open(indice, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["titulo", "parcela", "fornecedor",
                                          "anexos_no_sienge", "nota_fiscal",
                                          "boleto", "como_achou_o_boleto",
                                          "copiados", "descartados"])
        w.writeheader()
        w.writerows(linhas)

    total_copiados = sum(len(l["copiados"].split(" | ")) if l["copiados"] else 0 for l in linhas)
    logger.success(
        f"CONCLUÍDO — {len(pags)} títulos | {total_copiados} arquivos copiados | "
        f"{n_nf} notas fiscais | {n_bol} boletos | {n_sem} sem anexo no Sienge"
    )
    logger.success(f"Pasta: {destino}")
    logger.success(f"Índice: {indice}")


if __name__ == "__main__":
    args = sys.argv[1:]
    todos = "--todos" in args
    ciclo = next((a for a in args if not a.startswith("-")), None)
    main(int(ciclo) if ciclo else None, todos=todos)
