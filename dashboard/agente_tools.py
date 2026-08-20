"""Ferramentas que o AGENTE usa para falar com o Sienge e com o robô.

Cada ferramenta tem: schema (o que o modelo vê), um executor Python e a flag
`escreve` (True = altera o ERP → exige confirmação do usuário antes de rodar).
Tudo devolve texto/JSON compacto, em pt-BR, pronto para o modelo ler.
"""
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta

import requests
from requests.auth import HTTPBasicAuth

import config
from dashboard import database as db

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(RAIZ, "output")


def _auth():
    return HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)


def _get(path, **params):
    r = requests.get(f"{config.SIENGE_BASE_URL}{path}", auth=_auth(), params=params, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Sienge GET {path} → {r.status_code}: {(r.text or '')[:200]}")
    return r.json()


def _put(path, body):
    r = requests.put(f"{config.SIENGE_BASE_URL}{path}", auth=_auth(), json=body, timeout=60)
    return r.status_code, (r.text or "")[:300]


def _patch(path, body):
    r = requests.patch(f"{config.SIENGE_BASE_URL}{path}", auth=_auth(), json=body, timeout=60)
    return r.status_code, (r.text or "")[:300]


def _pag(path, **params):
    """Lê todas as páginas de um endpoint com resultSetMetadata."""
    out, off = [], 0
    while True:
        j = _get(path, limit=200, offset=off, **params)
        res = j.get("results", [])
        out += res
        if off + 200 >= j.get("resultSetMetadata", {}).get("count", 0) or not res:
            return out
        off += 200


def brl(v):
    try:
        return ("R$ " + f"{float(v):,.2f}").replace(",", "@").replace(".", ",").replace("@", ".")
    except Exception:
        return str(v)


def _json_cache(nome):
    p = os.path.join(OUT, nome)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


_CRED = {}
def _credor_nome(cid):
    if cid is None:
        return ""
    cid = str(cid)
    if cid not in _CRED:
        cache = _json_cache("_credores_cache.json") or {}
        if cid in cache:
            _CRED[cid] = cache[cid]
        else:
            try:
                _CRED[cid] = _get(f"/creditors/{cid}").get("name", "")
            except Exception:
                _CRED[cid] = f"credor {cid}"
    return _CRED[cid]


_PLANOS = None
def _plano_nome(c):
    global _PLANOS
    if _PLANOS is None:
        _PLANOS = {p["id"]: p["name"] for p in (_json_cache("_planos_financeiros.json") or [])}
    c = str(c or "")
    m = f"{c[0]}.{c[1:3]}.{c[3:5]}.{c[5:]}" if len(c) == 7 else c
    return f"{m} {_PLANOS.get(c, '')}".strip()


# ============================================================ LEITURA: títulos
def buscar_titulos(data_inicio, data_fim, credor_id=None, numero_documento=None, texto=None, tipo_documento=None, limite=60):
    """Lista títulos do contas a pagar emitidos no período (data de emissão)."""
    params = {}
    if credor_id: params["creditorId"] = credor_id
    if numero_documento: params["documentNumber"] = numero_documento
    if tipo_documento: params["documentsIdentificationId"] = tipo_documento
    bills = _pag("/bills", startDate=data_inicio, endDate=data_fim, **params)
    txt = (texto or "").lower()
    out = []
    for b in bills:
        nome = _credor_nome(b.get("creditorId")) if (txt or len(bills) <= 400) else ""
        alvo = f"{b.get('documentNumber','')} {nome} {b.get('notes','') or ''}".lower()
        if txt and txt not in alvo:
            continue
        out.append(dict(id=b["id"], emissao=b.get("issueDate"), tipo=(b.get("documentIdentificationId") or "").strip(),
                        documento=b.get("documentNumber"), credor=nome or b.get("creditorId"), valor=b.get("totalInvoiceAmount"),
                        lancado_por=b.get("registeredBy"), obs=(b.get("notes") or "")[:80]))
    tot = sum(x["valor"] or 0 for x in out)
    return dict(total_encontrado=len(out), soma=round(tot, 2), mostrando=min(len(out), limite), titulos=out[:limite])


def detalhar_titulo(titulo_id):
    """Tudo sobre um título: cadastro, parcelas, apropriações, impostos retidos, anexos."""
    b = _get(f"/bills/{titulo_id}")
    parc = _get(f"/bills/{titulo_id}/installments").get("results", [])
    aprop = _get(f"/bills/{titulo_id}/buildings-cost").get("results", [])
    cc = _get(f"/bills/{titulo_id}/budget-categories").get("results", [])
    try:
        taxes = _get(f"/bills/{titulo_id}/taxes").get("results", [])
    except Exception:
        taxes = []
    try:
        anexos = _get(f"/bills/{titulo_id}/attachments").get("results", [])
    except Exception:
        anexos = []
    return dict(
        id=b["id"], credor=dict(id=b.get("creditorId"), nome=_credor_nome(b.get("creditorId"))),
        devedor=b.get("debtorId"), emissao=b.get("issueDate"), tipo=(b.get("documentIdentificationId") or "").strip(),
        documento=b.get("documentNumber"), valor=b.get("totalInvoiceAmount"), observacao=b.get("notes"),
        lancado_por=b.get("registeredBy"), lancado_em=b.get("registeredDate"), alterado_por=b.get("changedBy"), alterado_em=b.get("changedDate"),
        parcelas=[dict(n=p.get("installmentNumber"), vencimento=p.get("dueDate"), valor=p.get("amount"), situacao=p.get("situation"),
                       forma=p.get("paymentType"), pago_em=p.get("paymentDate"), valor_pago=p.get("paidAmount")) for p in parc],
        apropriacao_obra=[dict(obra=f"{a.get('buildingId')} - {a.get('buildingName')}", unidade=f"{a.get('buildingUnitId')} - {a.get('buildingUnitName')}",
                               planilha=f"{a.get('costEstimationSheetId')} {(a.get('costEstimationSheetName') or '').strip()}", pct=a.get("percentage")) for a in aprop],
        apropriacao_financeira=[dict(centro_custo=c.get("costCenterId"), plano=_plano_nome(c.get("paymentCategoriesId")), pct=c.get("percentage")) for c in cc],
        impostos_retidos=[dict(imposto=t.get("taxId"), valor=t.get("amount"), base=t.get("taxableBaseAmount"), aliquota=t.get("rate")) for t in taxes],
        anexos=[dict(id=a.get("attachmentid") or a.get("id"), nome=a.get("name") or a.get("fileName"), tamanho=a.get("size"), tipo=a.get("contentType")) for a in anexos],
    )


def ler_anexo(titulo_id, anexo_id=None, max_caracteres=6000):
    """Baixa um anexo (PDF) do título e devolve o texto. Sem anexo_id, lê o primeiro."""
    import fitz
    anexos = _get(f"/bills/{titulo_id}/attachments").get("results", [])
    if not anexos:
        return "Título sem anexos."
    def _aid(a): return a.get("attachmentid") or a.get("id")
    alvo = next((a for a in anexos if anexo_id and _aid(a) == anexo_id), anexos[0])
    r = requests.get(f"{config.SIENGE_BASE_URL}/bills/{titulo_id}/attachments/{_aid(alvo)}", auth=_auth(), timeout=120)
    if r.status_code != 200:
        return f"Não consegui baixar o anexo ({r.status_code})."
    try:
        doc = fitz.open(stream=r.content, filetype="pdf")
        texto = "\n".join(p.get_text() for p in doc)
    except Exception:
        return f"Anexo '{alvo.get('name')}' não é PDF legível ({len(r.content)} bytes)."
    texto = re.sub(r"\n{3,}", "\n\n", texto).strip()
    return dict(anexo=alvo.get("name"), paginas=doc.page_count, texto=texto[:max_caracteres], truncado=len(texto) > max_caracteres)


def buscar_credor(nome=None, cnpj=None):
    """Procura credor por parte do nome ou CNPJ."""
    if cnpj:
        res = _get("/creditors", cnpj=re.sub(r"\D", "", cnpj), limit=10).get("results", [])
    else:
        res = _pag("/creditors", name=nome) if nome else []
        if not res and nome:
            # fallback: varre o cache local
            cache = _json_cache("_credores_cache.json") or {}
            res = [dict(id=int(k), name=v) for k, v in cache.items() if nome.lower() in (v or "").lower()]
    return [dict(id=c.get("id"), nome=c.get("name"), cnpj=c.get("cnpj") or c.get("cpf"), ativo=c.get("active")) for c in res[:20]]


def consultar_tabelas(tabela, obra_id=None):
    """Tabelas de apoio: planos_financeiros | centros_de_custo | obras | planilhas_de_custo (precisa obra_id)."""
    if tabela == "planos_financeiros":
        P = _json_cache("_planos_financeiros.json") or _get("/payment-categories")
        return [dict(codigo=_plano_nome(p["id"]), tipo=p.get("tpConta"), redutora=p.get("flRedutora")) for p in P if p.get("flAtiva", "S") == "S" and p.get("tpConta") == "R"]
    if tabela == "centros_de_custo":
        return [dict(id=c.get("id"), nome=c.get("name"), ativo=c.get("active")) for c in _pag("/cost-centers")]
    if tabela == "obras":
        try:
            return [dict(id=b.get("id"), nome=b.get("name"), empresa=b.get("companyId")) for b in _pag("/building-projects")]
        except Exception as e:
            return f"Sem acesso à lista de obras ({e}). Conhecidas: 1 GARDEN - 1ª ETAPA (unidade 2 OBRA GARDEN), 3 GARDEN - INCORPORAÇÃO, 4 RESIDENCIAL VILA RAIÔ."
    if tabela == "planilhas_de_custo":
        if not obra_id:
            return "Informe obra_id."
        try:
            res = _pag(f"/building-projects/{obra_id}/sheets")
            return [dict(codigo=s.get("id"), nome=s.get("description") or s.get("name")) for s in res]
        except Exception as e:
            # fallback: planilhas já vistas no livro das retenções
            led = _json_cache("_retencoes_nf_2026.json") or {}
            vistas = {}
            for e2 in led.values():
                for a in e2.get("aprop", []):
                    if a.get("buildingId") == obra_id:
                        vistas[a["costEstimationSheetId"]] = a["costEstimationSheetName"]
            return dict(aviso=f"endpoint de planilhas indisponível ({str(e)[:80]}); estas são as planilhas vistas em títulos de 2026",
                        planilhas=[dict(codigo=k, nome=v) for k, v in sorted(vistas.items())])
    return "tabela desconhecida"


# ============================================================ LEITURA: robô e análises
def status_conferencia():
    """Última execução do robô de conferência e resumo das divergências."""
    execs = db.get_execucoes(limit=5)
    if not execs:
        return "Nenhum ciclo de conferência registrado."
    out = []
    for e in execs:
        divs = db.get_divergencias(e.id)
        crit = sum(1 for d in divs if (d.criticidade or "").upper() == "CRITICA")
        out.append(dict(execucao_id=e.id, status=e.status, iniciado_em=str(e.iniciado_em)[:16], concluido_em=str(e.concluido_em or "")[:16],
                        periodo=f"{e.periodo_inicio} a {e.periodo_fim}", titulos=getattr(e, "total_titulos", None),
                        divergencias=len(divs), criticas=crit, relatorio=bool(e.relatorio_path)))
    return out


def divergencias_conferencia(execucao_id=None, texto=None, criticidade=None, limite=40):
    """Divergências de um ciclo (padrão: o último). Filtra por texto (título, fornecedor, CNPJ) e criticidade."""
    if not execucao_id:
        execs = db.get_execucoes(limit=1)
        if not execs:
            return "Nenhum ciclo registrado."
        execucao_id = execs[0].id
    divs = db.get_divergencias(execucao_id, criticidade, texto)
    out = [dict(id=d.id, titulo=d.titulo_numero, fornecedor=d.fornecedor_nome, cnpj=d.fornecedor_cnpj, valor=d.valor_sienge,
                vencimento=str(d.data_vencimento or ""), problema=d.tipo, campo=d.campo, sienge=d.valor_sienge_campo,
                verificado=d.valor_boleto_campo if d.valor_boleto_campo not in (None, "-") else d.valor_nfe_campo,
                criticidade=d.criticidade, revisao=d.status_revisao) for d in divs]
    return dict(execucao_id=execucao_id, total=len(out), divergencias=out[:limite])


def iniciar_conferencia(data_inicio, data_fim):
    """Inicia um ciclo do robô (sem arquivo de relatório: confere os títulos emitidos no período)."""
    import orchestrator, threading
    for e in db.get_execucoes(limit=10):
        if e.status == "RODANDO":
            return f"Já existe um ciclo rodando (execução {e.id}). Acompanhe na tela de conferência."
    d1, d2 = date.fromisoformat(data_inicio), date.fromisoformat(data_fim)
    threading.Thread(target=lambda: orchestrator.executar_ciclo(d1, d2, iniciado_por="agente"), daemon=True).start()
    import time; time.sleep(1.0)
    ult = db.get_execucoes(limit=1)
    return dict(iniciado=True, execucao_id=(ult[0].id if ult else None), acompanhar_em="/conferencia")


def analise_impostos(pergunta=None, titulo_id=None):
    """Resultado do pente-fino dos impostos (apropriação, padrão da equipe, rateio pelas NFs)."""
    C = _json_cache("_correcao_impostos.json")
    R = _json_cache("_obra_correta.json") or []
    if not C:
        return "Análise de impostos ainda não gerada. Rode a ferramenta rodar_analise('impostos')."
    if titulo_id:
        l = next((x for x in C["linhas"] if x["id"] == titulo_id), None)
        r = next((x for x in R if x["id"] == titulo_id), None)
        if not l:
            return f"Título {titulo_id} não está entre os impostos analisados."
        return dict(titulo=l, rateio_pelas_nfs=(dict(competencia=r["competencia"], nfs=r["n_nfs"], soma_nfs=r["soma_nfs"], diferenca=r["dif"],
                                                    rateio=r["rateio"][:15]) if r else None))
    linhas = [l for l in C["linhas"] if l["data"] >= "2026"]
    resumo = dict(gerado_em=C.get("gerado_em"), titulos_2026=len(linhas),
                  por_status={s: sum(1 for l in linhas if l["status"] == s) for s in ("CORRIGIR", "ATENÇÃO", "OK")},
                  padrao_equipe={k: dict(obra=v["obra"], planilha=v["planilha"], cc=v["cc"], plano=v.get("plano_nome", v["plano"])) for k, v in C["padrao"].items()},
                  regra_pelos_dados="a retenção segue a NF que a gerou: apropriar a guia pelo rateio das notas da competência (ver rateio_pelas_nfs por título)",
                  titulos=[dict(id=l["id"], data=l["data"], doc=l["doc"], valor=l["valor"], tributo=l["tributo"], quem=l["quem"], status=l["status"],
                                obra=l["obra"], planilha=l["planilha"], cc=l["cc"], plano=l.get("plano_nome", l["plano"]), problemas=l["problemas"]) for l in linhas])
    return resumo


def rodar_analise(qual):
    """Roda uma análise pesada em segundo plano: 'impostos' (pente-fino completo) ou 'origem_retencoes' (livro das NFs)."""
    scripts = {"impostos": ["analisar_impostos.py"], "origem_retencoes": ["impostos_origem.py", "impostos_obra_correta.py"]}
    if qual not in scripts:
        return "opções: impostos | origem_retencoes"
    os.makedirs(os.path.join(RAIZ, "logs"), exist_ok=True)
    log = open(os.path.join(RAIZ, "logs", f"analise_{qual}.log"), "ab")
    cmd = " && ".join(f'"{sys.executable}" {s}' for s in scripts[qual])
    subprocess.Popen(cmd, cwd=RAIZ, shell=True, stdout=log, stderr=subprocess.STDOUT,
                     env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return dict(iniciado=True, demora="alguns minutos", log=f"logs/analise_{qual}.log", depois="chame analise_impostos de novo")


# ============================================================ ESCRITA (exigem confirmação)
def alterar_vencimento(titulo_id, parcela, nova_data):
    """PATCH da data de vencimento de uma parcela."""
    s, t = _patch(f"/bills/{titulo_id}/installments/{parcela}", {"dueDate": nova_data})
    if s in (200, 201, 204):
        p = next((x for x in _get(f"/bills/{titulo_id}/installments").get("results", []) if x.get("installmentNumber") == parcela), {})
        return dict(ok=p.get("dueDate") == nova_data, vencimento_agora=p.get("dueDate"), http=s)
    return dict(ok=False, http=s, resposta=t, dica="403 = falta autorizar 'Atualiza parcela do título' no usuário da API")


def alterar_apropriacao(titulo_id, obra=None, financeira=None):
    """PUT das apropriações. obra: lista de {buildingId, buildingUnitId, costEstimationSheetId, percentage};
    financeira: lista de {costCenterId, paymentCategoriesId, percentage}. Guarda snapshot para desfazer."""
    snap_path = os.path.join(OUT, "_snapshot_apropriacao.json")
    snap = json.load(open(snap_path, encoding="utf-8")) if os.path.exists(snap_path) else {}
    antes = dict(buildings=[{k: a.get(k) for k in ("buildingId", "buildingUnitId", "costEstimationSheetId", "percentage")}
                            for a in _get(f"/bills/{titulo_id}/buildings-cost").get("results", [])],
                 budget=[{k: c.get(k) for k in ("costCenterId", "paymentCategoriesId", "percentage")}
                         for c in _get(f"/bills/{titulo_id}/budget-categories").get("results", [])])
    snap.setdefault(str(titulo_id), antes)
    json.dump(snap, open(snap_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    res = {}
    if obra:
        if abs(sum(x.get("percentage", 0) for x in obra) - 100) > 0.01:
            return dict(ok=False, erro="percentuais da obra não somam 100")
        res["obra"] = _put(f"/bills/{titulo_id}/buildings-cost", obra)
    if financeira:
        if abs(sum(x.get("percentage", 0) for x in financeira) - 100) > 0.01:
            return dict(ok=False, erro="percentuais financeiros não somam 100")
        res["financeira"] = _put(f"/bills/{titulo_id}/budget-categories", financeira)
    ok = all(s in (200, 201, 204) for s, _ in res.values()) and bool(res)
    return dict(ok=ok, respostas={k: dict(http=s, msg=t) for k, (s, t) in res.items()},
                dica=None if ok else "403 = falta autorizar 'Atualiza as apropriações' no usuário da API; snapshot salvo para desfazer")


# ============================================================ registro
FERRAMENTAS = [
    dict(name="buscar_titulos", escreve=False, fn=buscar_titulos,
         description="Lista títulos do contas a pagar emitidos no período (datas YYYY-MM-DD). Filtros opcionais: credor_id, numero_documento, tipo_documento (NF, NFSE, DARF, BOL…), texto livre (procura no documento/credor/observação).",
         input_schema={"type": "object", "properties": {
             "data_inicio": {"type": "string"}, "data_fim": {"type": "string"},
             "credor_id": {"type": "integer"}, "numero_documento": {"type": "string"}, "tipo_documento": {"type": "string"},
             "texto": {"type": "string"}, "limite": {"type": "integer"}}, "required": ["data_inicio", "data_fim"]}),
    dict(name="detalhar_titulo", escreve=False, fn=detalhar_titulo,
         description="Detalha um título pelo id: credor, parcelas (vencimento, situação, forma), apropriação de obra (planilha) e financeira (centro de custo/plano), impostos retidos, anexos, quem lançou/alterou.",
         input_schema={"type": "object", "properties": {"titulo_id": {"type": "integer"}}, "required": ["titulo_id"]}),
    dict(name="ler_anexo", escreve=False, fn=ler_anexo,
         description="Lê o texto de um anexo PDF do título (guia, nota fiscal, boleto). Sem anexo_id lê o primeiro.",
         input_schema={"type": "object", "properties": {"titulo_id": {"type": "integer"}, "anexo_id": {"type": "integer"}, "max_caracteres": {"type": "integer"}}, "required": ["titulo_id"]}),
    dict(name="buscar_credor", escreve=False, fn=buscar_credor,
         description="Procura credor/fornecedor por parte do nome ou por CNPJ.",
         input_schema={"type": "object", "properties": {"nome": {"type": "string"}, "cnpj": {"type": "string"}}}),
    dict(name="consultar_tabelas", escreve=False, fn=consultar_tabelas,
         description="Tabelas de apoio do Sienge: planos_financeiros, centros_de_custo, obras, planilhas_de_custo (exige obra_id).",
         input_schema={"type": "object", "properties": {"tabela": {"type": "string", "enum": ["planos_financeiros", "centros_de_custo", "obras", "planilhas_de_custo"]}, "obra_id": {"type": "integer"}}, "required": ["tabela"]}),
    dict(name="status_conferencia", escreve=False, fn=status_conferencia,
         description="Últimos ciclos do robô de conferência de contas a pagar (status, divergências, críticas).",
         input_schema={"type": "object", "properties": {}}),
    dict(name="divergencias_conferencia", escreve=False, fn=divergencias_conferencia,
         description="Divergências encontradas pelo robô num ciclo (padrão: o último). Filtros: texto, criticidade (CRITICA/ALTA/MEDIA/BAIXA).",
         input_schema={"type": "object", "properties": {"execucao_id": {"type": "integer"}, "texto": {"type": "string"}, "criticidade": {"type": "string"}, "limite": {"type": "integer"}}}),
    dict(name="iniciar_conferencia", escreve=False, fn=iniciar_conferencia,
         description="Inicia um ciclo do robô de conferência para os títulos emitidos no período (YYYY-MM-DD). Para conferir uma remessa/fluxo de caixa em arquivo, oriente o usuário a usar o botão Enviar relatório na tela de conferência.",
         input_schema={"type": "object", "properties": {"data_inicio": {"type": "string"}, "data_fim": {"type": "string"}}, "required": ["data_inicio", "data_fim"]}),
    dict(name="analise_impostos", escreve=False, fn=analise_impostos,
         description="Pente-fino dos impostos (RET, INSS, CSRF, IRRF, ISS, IPTU, taxas): apropriação atual x padrão da equipe, status por título, e o rateio correto pelas NFs de origem (passe titulo_id para um título específico).",
         input_schema={"type": "object", "properties": {"pergunta": {"type": "string"}, "titulo_id": {"type": "integer"}}}),
    dict(name="rodar_analise", escreve=False, fn=rodar_analise,
         description="Reexecuta em segundo plano uma análise pesada: 'impostos' (pente-fino completo pela API) ou 'origem_retencoes' (livro das NFs + rateio).",
         input_schema={"type": "object", "properties": {"qual": {"type": "string", "enum": ["impostos", "origem_retencoes"]}}, "required": ["qual"]}),
    dict(name="alterar_vencimento", escreve=True, fn=alterar_vencimento,
         description="ALTERA no Sienge a data de vencimento de uma parcela (exige confirmação do usuário).",
         input_schema={"type": "object", "properties": {"titulo_id": {"type": "integer"}, "parcela": {"type": "integer"}, "nova_data": {"type": "string"}}, "required": ["titulo_id", "parcela", "nova_data"]}),
    dict(name="alterar_apropriacao", escreve=True, fn=alterar_apropriacao,
         description="ALTERA no Sienge a apropriação de um título (exige confirmação). obra = lista de {buildingId, buildingUnitId, costEstimationSheetId, percentage}; financeira = lista de {costCenterId, paymentCategoriesId (7 dígitos, ex. 1090107), percentage}. Percentuais somam 100. Snapshot salvo para desfazer.",
         input_schema={"type": "object", "properties": {
             "titulo_id": {"type": "integer"},
             "obra": {"type": "array", "items": {"type": "object", "properties": {"buildingId": {"type": "integer"}, "buildingUnitId": {"type": "integer"}, "costEstimationSheetId": {"type": "string"}, "percentage": {"type": "number"}}, "required": ["buildingId", "buildingUnitId", "costEstimationSheetId", "percentage"]}},
             "financeira": {"type": "array", "items": {"type": "object", "properties": {"costCenterId": {"type": "integer"}, "paymentCategoriesId": {"type": "string"}, "percentage": {"type": "number"}}, "required": ["costCenterId", "paymentCategoriesId", "percentage"]}}},
             "required": ["titulo_id"]}),
]
POR_NOME = {f["name"]: f for f in FERRAMENTAS}


def schemas_para_api():
    return [dict(name=f["name"], description=f["description"], input_schema=f["input_schema"]) for f in FERRAMENTAS]


def executar(nome, entrada):
    f = POR_NOME[nome]
    try:
        res = f["fn"](**(entrada or {}))
    except TypeError as e:
        return json.dumps({"erro": f"parâmetros inválidos: {e}"}, ensure_ascii=False), True
    except Exception as e:  # noqa: BLE001
        return json.dumps({"erro": str(e)[:400]}, ensure_ascii=False), True
    s = json.dumps(res, ensure_ascii=False, default=str)
    if len(s) > 60000:
        s = s[:60000] + " …(cortado)"
    return s, False
