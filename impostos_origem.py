"""Monta o LIVRO DAS RETENCOES de 2026: para cada titulo de fornecedor (NF/NFSE/etc.) le os
impostos retidos (GET /bills/{id}/taxes) e a apropriacao de obra (GET /bills/{id}/buildings-cost).
E isso que diz, pelos dados, a que obra/planilha cada guia de INSS/CSRF/IRRF/ISS pertence.
Saida: output/_retencoes_nf_2026.json  (cache incremental: so consulta titulos novos)
Uso: python impostos_origem.py [--de 2026-01-01] [--ate hoje]"""
import json
import os
import sys
import time
from datetime import date

import requests
import config
from requests.auth import HTTPBasicAuth

B = config.SIENGE_BASE_URL
S = requests.Session(); S.auth = HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
OUT = "output/_retencoes_nf_2026.json"
args = sys.argv[1:]
DE = args[args.index('--de') + 1] if '--de' in args else "2026-01-01"
ATE = args[args.index('--ate') + 1] if '--ate' in args else date.today().isoformat()
PULAR_TIPOS = {"DARF", "DARE", "DAR", "IPTU", "GUIA", "DARM", "FAT", "GPS", "FGTS"}


def g(p, **params):
    for tent in range(6):
        r = S.get(B + p, params=params, timeout=90)
        if r.status_code == 429:
            time.sleep(6 * (tent + 1)); continue
        return r.status_code, (r.json() if r.status_code == 200 else None)
    return r.status_code, None


def listar(ini, fim):
    out, off = [], 0
    while True:
        s, j = g("/bills", startDate=ini, endDate=fim, limit=200, offset=off)
        if s != 200:
            print("  /bills", s); break
        res = j.get("results", [])
        out += res
        if off + 200 >= j.get("resultSetMetadata", {}).get("count", 0) or not res:
            break
        off += 200
    return out


cache = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
bills = listar(DE, ATE)
print(f"{len(bills)} títulos emitidos de {DE} a {ATE}")
cred = json.load(open("output/_credores_cache.json", encoding="utf-8")) if os.path.exists("output/_credores_cache.json") else {}
novos = 0
for i, b in enumerate(bills):
    bid = str(b["id"])
    tipo = (b.get("documentIdentificationId") or "").strip().upper()
    if tipo in PULAR_TIPOS:
        continue
    if bid in cache and cache[bid].get("_v") == 1:
        continue
    s, j = g(f"/bills/{bid}/taxes")
    taxes = (j or {}).get("results", []) if s == 200 else []
    ent = {"_v": 1, "id": b["id"], "issueDate": b["issueDate"], "tipo": tipo, "doc": b.get("documentNumber"),
           "valor": b.get("totalInvoiceAmount"), "creditorId": b.get("creditorId"), "registeredBy": b.get("registeredBy"),
           "taxes": [{"tax": t.get("taxId"), "amount": t.get("amount"), "base": t.get("taxableBaseAmount"), "rate": t.get("rate"),
                      "guia": t.get("taxGuideBill")} for t in taxes],
           "aprop": []}
    if taxes:
        s2, j2 = g(f"/bills/{bid}/buildings-cost")
        ent["aprop"] = [{k: a.get(k) for k in ("buildingId", "buildingName", "buildingUnitId", "buildingUnitName",
                                              "costEstimationSheetId", "costEstimationSheetName", "percentage")}
                        for a in ((j2 or {}).get("results", []) if s2 == 200 else [])]
        cid = str(b.get("creditorId"))
        if cid not in cred:
            s3, j3 = g(f"/creditors/{cid}")
            cred[cid] = (j3 or {}).get("name", "") if s3 == 200 else ""
        ent["credor"] = cred.get(cid, "")
    cache[bid] = ent
    novos += 1
    if novos % 100 == 0:
        print(f"  {novos} consultados ({i}/{len(bills)})")
        json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(cache, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
json.dump(cred, open("output/_credores_cache.json", "w", encoding="utf-8"), ensure_ascii=False)
com_tax = [e for e in cache.values() if e.get("taxes")]
print(f"salvo {OUT}: {len(cache)} títulos lidos, {len(com_tax)} com retenção, {novos} novos nesta rodada")
