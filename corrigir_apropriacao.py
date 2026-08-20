"""Corrige a APROPRIACAO (obra + centro de custo/plano financeiro) de titulos de imposto
que foram lancados em "Imposto (RET)" / GARDEN - INCORPORACAO mas, pelo codigo de receita
da guia, sao retencoes da obra (INSS 1162, CSRF 5952, IRRF 1708/8045, ISS 1732).

Endpoints (bill-debt-v1):
  PUT /bills/{billId}/buildings-cost     body: [{buildingId, buildingUnitId, costEstimationSheetId, percentage}]
  PUT /bills/{billId}/budget-categories  body: [{costCenterId, paymentCategoriesId, percentage}]
Precisam estar AUTORIZADOS para o usuario da API (hoje so ha leitura).

Seguranca:
  - so mexe nos titulos do PLANO abaixo (conferindo valor antes);
  - grava snapshot do estado anterior em output/_snapshot_apropriacao.json (para desfazer);
  - --dry-run (padrao) mostra o que faria; --executar aplica; --desfazer restaura o snapshot.

Uso:
    python corrigir_apropriacao.py               # simulacao
    python corrigir_apropriacao.py --executar    # aplica
    python corrigir_apropriacao.py --desfazer    # volta ao que estava (usa o snapshot)
"""
import json
import os
import sys

import requests
from requests.auth import HTTPBasicAuth

import config

BASE = config.SIENGE_BASE_URL
AUTH = HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
H = {"Content-Type": "application/json"}
SNAP = "output/_snapshot_apropriacao.json"

OBRA = dict(buildingId=1, buildingUnitId=2)                       # GARDEN - 1a ETAPA / OBRA GARDEN
CC = 1                                                             # GARDEN - 1a ETAPA
DESTINO = {   # tributo -> (planilha de custo, plano financeiro)  [padrao 2026 do proprio time]
    "INSS": ("00.002.001.007", "1090107"),   # Equipe - Apoio (Ate 12/2026); 1.09.01.07 INSS (retencoes de fornecedores)
    "CSRF": ("00.003.032.003", "2040303"),   # Taxa de Administracao de Obra; 2.04.03.03 PIS/COFINS/CSLL
    "ISS":  ("00.003.032.003", "2040301"),   # Taxa de Administracao de Obra; 2.04.03.01 ISS
    "IRRF": ("00.003.032.004", "2040302"),   # Remuneracao PMG 1a Etapa; 2.04.03.02 IRRF Terceiros PJ (grupo Impostos de Terceiros)
}

# titulo, tributo (pela guia), valor esperado (conferencia)
PLANO = [
    (9834,  "ISS",  15588.94),   # DAR 1406149            -> ISS 05/2026 (cod. 1732)
    (9885,  "INSS", 156688.39),  # DARF INSS RET - 05/2026 (cod. 1162)
    (9886,  "CSRF", 23891.77),   # DARF RET - CRF 05/2026  (cod. 5952)
    (9887,  "IRRF", 7705.27),    # DARF IRRF - 05/2026     (cod. 1708/8045)
    (10444, "ISS",  14254.74),   # DARE ISS 06-2026        (cod. 1732)
    (10447, "CSRF", 25464.89),   # DARF CRF - 06-2026      (cod. 5952)
    (10821, "ISS",  23548.64),   # DARE ISS 07-2026        (cod. 1732)
    (10968, "INSS", 214849.41),  # DARF RET INSS 07-2026   (cod. 1162)
]


def get(path):
    r = requests.get(f"{BASE}{path}", auth=AUTH, timeout=60)
    r.raise_for_status()
    return r.json()


def put(path, body):
    r = requests.put(f"{BASE}{path}", auth=AUTH, headers=H, data=json.dumps(body), timeout=60)
    return r.status_code, r.text[:300]


def estado(tid):
    return {
        "buildings": [{k: a[k] for k in ("buildingId", "buildingUnitId", "costEstimationSheetId", "percentage")}
                      for a in get(f"/bills/{tid}/buildings-cost").get("results", [])],
        "budget": [{k: b[k] for k in ("costCenterId", "paymentCategoriesId", "percentage")}
                   for b in get(f"/bills/{tid}/budget-categories").get("results", [])],
    }


def aplicar(tid, buildings, budget):
    s1, t1 = put(f"/bills/{tid}/buildings-cost", buildings)
    s2, t2 = put(f"/bills/{tid}/budget-categories", budget)
    ok1, ok2 = s1 in (200, 201, 204), s2 in (200, 201, 204)
    print(f"    obra   PUT -> {s1} {'' if ok1 else t1}")
    print(f"    CC     PUT -> {s2} {'' if ok2 else t2}")
    if ok1 != ok2:
        print("    ATENCAO: so metade foi aplicada (obra e CC divergem). Use --desfazer ou corrija na tela.")
    return ok1 and ok2


def gravar_snapshot(snapshot):
    """Grava o estado ANTERIOR sem nunca sobrescrever um titulo ja salvo (preserva o caminho de volta)."""
    os.makedirs("output", exist_ok=True)
    antigo = json.load(open(SNAP, encoding="utf-8")) if os.path.exists(SNAP) else {}
    for k, v in snapshot.items():
        antigo.setdefault(k, v)
    json.dump(antigo, open(SNAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main(modo):
    print(f"=== CORRIGIR APROPRIACAO DE IMPOSTOS — {modo} ===\n")

    if modo == "DESFAZER":
        snap = json.load(open(SNAP, encoding="utf-8"))
        for tid, est in snap.items():
            print(f"{tid}: restaurando apropriacao anterior")
            aplicar(tid, est["buildings"], est["budget"])
        return

    snapshot = {}
    for tid, trib, valor in PLANO:
        bill = get(f"/bills/{tid}")
        if abs(float(bill.get("totalInvoiceAmount", 0)) - valor) > 0.02:
            print(f"{tid}: valor diverge (esperado {valor:,.2f}, achou {bill.get('totalInvoiceAmount')}) — PULANDO")
            continue
        antes = estado(tid)
        sheet, plano = DESTINO[trib]
        novo_b = [dict(OBRA, costEstimationSheetId=sheet, percentage=100.0)]
        novo_c = [dict(costCenterId=CC, paymentCategoriesId=plano, percentage=100.0)]
        ja = (antes["buildings"] == novo_b and antes["budget"] == novo_c)
        print(f"{tid} {(bill.get('documentIdentificationId') or '').strip()} {bill.get('documentNumber')}  "
              f"R$ {valor:,.2f}  [{trib}]")
        print(f"    de : obra {antes['buildings']} | CC {antes['budget']}")
        print(f"    para: obra {novo_b} | CC {novo_c}" + ("   (ja esta assim)" if ja else ""))
        if modo != "EXECUCAO REAL" or ja:
            continue
        gravar_snapshot({str(tid): antes})          # antes de escrever, titulo a titulo
        snapshot[str(tid)] = antes
        if aplicar(tid, novo_b, novo_c):
            depois = estado(tid)
            print("    OK" if (depois["buildings"] == novo_b and depois["budget"] == novo_c)
                  else f"    RESPONDEU MAS NAO MUDOU: {depois}")

    if snapshot:
        print(f"\nsnapshot do estado anterior (para --desfazer): {SNAP}")
    if modo != "EXECUCAO REAL":
        print("\n>>> Nada foi alterado. Para aplicar:  python corrigir_apropriacao.py --executar")
        print(">>> Se a API devolver 403, falta autorizar os PUT de apropriacao (obra e financeira) no usuario da API.")
        print(">>> Credor errado (10968 e 10970) nao tem endpoint de alteracao: corrigir na tela do Sienge.")


if __name__ == "__main__":
    main("DESFAZER" if "--desfazer" in sys.argv else
         "EXECUCAO REAL" if "--executar" in sys.argv else "SIMULACAO (nada sera alterado)")
