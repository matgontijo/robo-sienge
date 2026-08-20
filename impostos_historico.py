"""Levanta o HISTORICO de titulos de imposto (2023..2025 + 2026) com:
   buildings-cost (obra/unidade/planilha) + budget-categories (centro de custo/plano financeiro)
Salva em output/_hist_impostos.json. Reaproveita o cache de credores."""
import json, os, re, sys, time
import requests, config
from requests.auth import HTTPBasicAuth

B = config.SIENGE_BASE_URL
A = HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
S = requests.Session(); S.auth = A

def g(p, **params):
    for tent in range(6):
        r = S.get(B + p, params=params, timeout=90)
        if r.status_code == 429:
            time.sleep(8 * (tent + 1)); continue
        return r.status_code, (r.json() if r.status_code == 200 else None)
    return r.status_code, None

GUIAS = {"DARF", "DARE", "DAR", "IPTU", "GPS", "FGTS", "GARE", "DAM", "DAS", "GRF", "GRU", "DAE", "ISS", "INSS", "IRRF"}
RE_TRIB = re.compile(r"\b(IRRF|IRPJ|IRF|INSS|CSRF|CSLL|CRF|PIS|COFINS|ISS|ISSQN|RET|IPTU|TLP|TFE|TEO|TAXA|DARF|DARE|GPS|FGTS|RPA|IR)\b", re.I)
ORGAOS = ("RECEITA FEDERAL", "DISTRITO FEDERAL", "SECRETARIA DE ESTADO", "FAZENDA", "PREFEITURA", "SEFAZ", "ECONOMIA DO", "UNIAO", "UNIÃO", "TESOURO", "CAIXA ECONOMICA", "MUNICIPIO", "GOVERNO")

credores = {}
cache_cred = "output/_credores_cache.json"
if os.path.exists(cache_cred):
    credores = json.load(open(cache_cred, encoding="utf-8"))

def nome_credor(cid):
    k = str(cid)
    if k not in credores:
        s, j = g(f"/creditors/{cid}")
        credores[k] = (j or {}).get("name", "") if s == 200 else ""
    return credores[k]

def listar(ini, fim):
    out, off = [], 0
    while True:
        s, j = g("/bills", startDate=ini, endDate=fim, limit=200, offset=off)
        if s != 200: print("  /bills", s); break
        res = j.get("results", [])
        out += res
        tot = j.get("resultSetMetadata", {}).get("count", 0)
        off += 200
        if off >= tot or not res: break
    return out

def e_imposto(t):
    tipo = (t.get("documentIdentificationId") or "").strip().upper()
    txt = f"{t.get('documentNumber') or ''} {t.get('notes') or ''}"
    cred = nome_credor(t.get("creditorId")).upper()
    m = []
    if tipo in GUIAS: m.append("tipo")
    if any(o in cred for o in ORGAOS): m.append("credor")
    if RE_TRIB.search(txt): m.append("texto")
    # regra: guia OU (orgao arrecadador) OU (texto tributo com tipo de guia-like)
    if "tipo" in m or "credor" in m: return "+".join(m)
    return None

from datetime import date as _date
_hoje = _date.today()
periodos = [(f"{a}-01-01", f"{a}-12-31") for a in range(2023, _hoje.year)] + [(f"{_hoje.year}-01-01", _hoje.isoformat())]
todos = []
for ini, fim in periodos:
    bs = listar(ini, fim)
    print(f"{ini[:4]}: {len(bs)} titulos")
    todos += bs
json.dump(credores, open(cache_cred, "w", encoding="utf-8"), ensure_ascii=False)

imp = []
for t in todos:
    mot = e_imposto(t)
    if mot:
        t["_credor"] = nome_credor(t.get("creditorId"))
        t["_motivo"] = mot
        imp.append(t)
json.dump(credores, open(cache_cred, "w", encoding="utf-8"), ensure_ascii=False)
print(f"impostos candidatos: {len(imp)}")

for i, t in enumerate(imp):
    s, j = g(f"/bills/{t['id']}/buildings-cost")
    t["_apropriacoes"] = (j or {}).get("results", []) if s == 200 else []
    s, j = g(f"/bills/{t['id']}/budget-categories")
    t["_budget"] = (j or {}).get("results", []) if s == 200 else []
    if i % 25 == 0: print(f"  {i}/{len(imp)}")
json.dump(imp, open("output/_hist_impostos.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
s, j = g("/payment-categories")
if s == 200 and isinstance(j, list):
    json.dump(j, open("output/_planos_financeiros.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"planos financeiros: {len(j)}")
else:
    print(f"planos financeiros: sem acesso ({s}) — painel mostra so o codigo")
print("salvo output/_hist_impostos.json")
