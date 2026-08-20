"""Painel de APROPRIAÇÃO — corrige, pela API, títulos de imposto lançados na obra/centro de
custo errados. Lê o plano em output/_plano_apropriacao.json (gerado pela análise histórica),
mostra o estado ATUAL de cada título direto do Sienge e aplica a correção com um clique.

PUT /bills/{id}/buildings-cost      -> obra / unidade / planilha de custo
PUT /bills/{id}/budget-categories   -> centro de custo / plano financeiro
Snapshot do estado anterior em output/_snapshot_apropriacao.json (nunca sobrescreve) -> desfazer.
"""
import json
import os
import threading
import time

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from requests.auth import HTTPBasicAuth

import config

router = APIRouter(prefix="/api/apropriacao", tags=["apropriacao"])

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLANO_PATH = os.path.join(RAIZ, "output", "_plano_apropriacao.json")
SNAP_PATH = os.path.join(RAIZ, "output", "_snapshot_apropriacao.json")
_lock = threading.Lock()
_cache = {"ts": 0.0, "estado": {}}


def _auth():
    return HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)


def _get(path):
    r = requests.get(f"{config.SIENGE_BASE_URL}{path}", auth=_auth(), timeout=60)
    if r.status_code != 200:
        raise HTTPException(502, f"Sienge GET {path} -> {r.status_code}")
    return r.json()


def _put(path, body):
    r = requests.put(f"{config.SIENGE_BASE_URL}{path}", auth=_auth(),
                     headers={"Content-Type": "application/json"}, data=json.dumps(body), timeout=60)
    return r.status_code, (r.text or "")[:300]


def _ler_plano():
    if not os.path.exists(PLANO_PATH):
        raise HTTPException(404, "Plano de apropriação não encontrado (output/_plano_apropriacao.json).")
    return json.load(open(PLANO_PATH, encoding="utf-8"))


def _salvar_plano(plano):
    tmp = PLANO_PATH + ".tmp"
    json.dump(plano, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, PLANO_PATH)


def _ler_snap():
    return json.load(open(SNAP_PATH, encoding="utf-8")) if os.path.exists(SNAP_PATH) else {}


def _gravar_snap(tid, estado):
    """Guarda o estado ANTERIOR; nunca sobrescreve (preserva o caminho de volta)."""
    with _lock:
        snap = _ler_snap()
        snap.setdefault(str(tid), estado)
        tmp = SNAP_PATH + ".tmp"
        json.dump(snap, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, SNAP_PATH)


def _estado(tid):
    b = _get(f"/bills/{tid}/buildings-cost").get("results", [])
    c = _get(f"/bills/{tid}/budget-categories").get("results", [])
    return {
        "buildings": [{k: a.get(k) for k in ("buildingId", "buildingName", "buildingUnitId", "buildingUnitName",
                                             "costEstimationSheetId", "costEstimationSheetName", "percentage")} for a in b],
        "budget": [{k: x.get(k) for k in ("costCenterId", "paymentCategoriesId", "percentage")} for x in c],
    }


def _opcoes_item(plano, item):
    """Opcoes de destino do titulo: o rateio pelas NFs de origem (se calculado) vem primeiro,
    depois as variantes de planilha unica que a equipe ja usou para o tributo."""
    ops = []
    if item.get("rateio_nf"):
        r = item["rateio_nf"]
        ops.append(dict(tipo="rateio", sheet=None, planilha=f"rateio pelas NFs de origem ({len(r['obra'])} linhas)",
                        plano=r["cc"][0]["plano"] if r["cc"] else "", plano_nome=r.get("plano_nome", ""),
                        rotulo=f"a retenção segue a nota: {r.get('resumo', '')}", rateio=r))
    ops += [dict(o, tipo="unica") for o in plano["opcoes"].get(item["tributo"], [])]
    return ops


def _alvo(plano, item):
    ops = _opcoes_item(plano, item)
    op = ops[min(item.get("escolha", 0), len(ops) - 1)]
    if op["tipo"] == "rateio":
        r = op["rateio"]
        novo_b = [dict(buildingId=x["buildingId"], buildingUnitId=x["buildingUnitId"],
                       costEstimationSheetId=x["sheet"], percentage=x["pct"]) for x in r["obra"]]
        novo_c = [dict(costCenterId=x["costCenterId"], paymentCategoriesId=x["plano"], percentage=x["pct"]) for x in r["cc"]]
        return op, novo_b, novo_c
    novo_b = [dict(buildingId=plano["obra"]["buildingId"], buildingUnitId=plano["obra"]["buildingUnitId"],
                   costEstimationSheetId=op["sheet"], percentage=100.0)]
    novo_c = [dict(costCenterId=plano["cc"]["id"], paymentCategoriesId=op["plano"], percentage=100.0)]
    return op, novo_b, novo_c


def _bate(atual, novo_b, novo_c):
    ab = sorted((a["buildingId"], a["buildingUnitId"], a["costEstimationSheetId"], round(a["percentage"] or 0, 2)) for a in atual["buildings"])
    ac = sorted((c["costCenterId"], c["paymentCategoriesId"], round(c["percentage"] or 0, 2)) for c in atual["budget"])
    nb = sorted((x["buildingId"], x["buildingUnitId"], x["costEstimationSheetId"], round(x["percentage"], 2)) for x in novo_b)
    nc = sorted((x["costCenterId"], x["paymentCategoriesId"], round(x["percentage"], 2)) for x in novo_c)
    return ab == nb, ac == nc


def _item(plano, tid):
    for it in plano["itens"]:
        if it["id"] == tid:
            return it
    raise HTTPException(404, f"Título {tid} não está no plano.")


# ------------------------------------------------------------------ rotas
@router.get("")
def listar(atualizar: bool = False):
    """Plano + estado atual (vivo, do Sienge) de cada título. Cache de 60 s."""
    plano = _ler_plano()
    agora = time.time()
    if atualizar or agora - _cache["ts"] > 60:
        est = {}
        for it in plano["itens"]:
            try:
                est[it["id"]] = _estado(it["id"])
            except HTTPException as e:
                est[it["id"]] = {"erro": e.detail}
        _cache["estado"], _cache["ts"] = est, agora
    snap = _ler_snap()
    saida = []
    for it in plano["itens"]:
        atual = _cache["estado"].get(it["id"], {})
        op, novo_b, novo_c = _alvo(plano, it)
        if "erro" in atual or not atual:
            status, ob_ok, cc_ok = "erro", None, None
        else:
            ob_ok, cc_ok = _bate(atual, novo_b, novo_c)
            status = "corrigido" if (ob_ok and cc_ok) else ("parcial" if (ob_ok or cc_ok) else "pendente")
        saida.append({**it, "para": op, "opcoes_item": _opcoes_item(plano, it), "atual": atual, "status": status,
                      "obra_ok": ob_ok, "cc_ok": cc_ok, "tem_snapshot": str(it["id"]) in snap})
    return {"obra": plano["obra"], "cc": plano["cc"], "opcoes": plano["opcoes"], "manual": plano.get("manual", []),
            "planos": plano.get("planos", {}),
            "base": plano.get("base"), "itens": saida, "atualizado_em": _cache["ts"]}


class Escolha(BaseModel):
    escolha: int


@router.post("/{tid}/escolha")
def escolher(tid: int, payload: Escolha):
    """Troca o destino (entre as variantes que o time já usou) sem aplicar."""
    plano = _ler_plano()
    it = _item(plano, tid)
    n = len(_opcoes_item(plano, it))
    if not 0 <= payload.escolha < n:
        raise HTTPException(400, "opção inválida")
    it["escolha"] = payload.escolha
    _salvar_plano(plano)
    return {"ok": True}


@router.post("/{tid}/corrigir")
def corrigir(tid: int):
    plano = _ler_plano()
    it = _item(plano, tid)
    bill = _get(f"/bills/{tid}")
    if abs(float(bill.get("totalInvoiceAmount") or 0) - float(it["valor"])) > 0.02:
        raise HTTPException(409, f"Valor do título mudou ({bill.get('totalInvoiceAmount')}); não vou mexer.")
    antes = _estado(tid)
    op, novo_b, novo_c = _alvo(plano, it)
    ob_ok, cc_ok = _bate(antes, novo_b, novo_c)
    if ob_ok and cc_ok:
        return {"status": "corrigido", "msg": "Já estava certo.", "atual": antes}
    _gravar_snap(tid, antes)
    res = {}
    if not ob_ok:
        res["obra"] = _put(f"/bills/{tid}/buildings-cost", novo_b)
    if not cc_ok:
        res["cc"] = _put(f"/bills/{tid}/budget-categories", novo_c)
    depois = _estado(tid)
    _cache["estado"][tid] = depois
    ob2, cc2 = _bate(depois, novo_b, novo_c)
    status = "corrigido" if (ob2 and cc2) else ("parcial" if (ob2 or cc2) else "pendente")
    sem_perm = any(s == 403 for s, _ in res.values())
    msg = ("Sem permissão de escrita na API: libere 'Atualiza as apropriações obra / financeiras do título' "
           "no usuário da API do Sienge." if sem_perm else
           "Corrigido." if status == "corrigido" else
           "Aplicado só em parte — veja os códigos." if status == "parcial" else
           "A API respondeu mas nada mudou.")
    return {"status": status, "msg": msg, "respostas": res, "atual": depois}


@router.post("/{tid}/desfazer")
def desfazer(tid: int):
    snap = _ler_snap()
    est = snap.get(str(tid))
    if not est:
        raise HTTPException(404, "Sem snapshot para esse título.")
    b = [{k: a[k] for k in ("buildingId", "buildingUnitId", "costEstimationSheetId", "percentage")} for a in est["buildings"]]
    c = [{k: x[k] for k in ("costCenterId", "paymentCategoriesId", "percentage")} for x in est["budget"]]
    res = {"obra": _put(f"/bills/{tid}/buildings-cost", b), "cc": _put(f"/bills/{tid}/budget-categories", c)}
    depois = _estado(tid)
    _cache["estado"][tid] = depois
    ok = all(s in (200, 201, 204) for s, _ in res.values())
    return {"ok": ok, "respostas": res, "atual": depois}


@router.post("/corrigir-todos")
def corrigir_todos():
    plano = _ler_plano()
    out = []
    for it in plano["itens"]:
        try:
            r = corrigir(it["id"])
            out.append({"id": it["id"], **r})
        except HTTPException as e:
            out.append({"id": it["id"], "status": "erro", "msg": e.detail})
    return {"resultados": out}
