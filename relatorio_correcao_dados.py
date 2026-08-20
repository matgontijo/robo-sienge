"""DADOS do relatorio ANTES x DEPOIS (compartilhado pelo PDF e pelo HTML).
Compara output/_hist_impostos_antes_correcao.json (estado antes) com output/_hist_impostos.json (estado
atual, lido da API) e o status da analise (output/_correcao_impostos.json).

Uso: python relatorio_correcao_impostos.py  [--quem MATHEUS] [--de 2026-01-01] [--ate 2026-08-31]
Saida: output/Relatorio_Correcao_Impostos_<quem>_2026.pdf"""
import json
import sys
from collections import OrderedDict
from datetime import datetime


args = sys.argv[1:]
def arg(nome, padrao):
    return args[args.index(nome) + 1] if nome in args else padrao
QUEM = arg('--quem', 'MATHEUS').upper()
DE, ATE = arg('--de', '2026-01-01'), arg('--ate', '2026-08-31')

ANTES = {t['id']: t for t in json.load(open('output/_hist_impostos_antes_correcao.json', encoding='utf-8'))}
DEPOIS = {t['id']: t for t in json.load(open('output/_hist_impostos.json', encoding='utf-8'))}
C = json.load(open('output/_correcao_impostos.json', encoding='utf-8'))
LIN = {l['id']: l for l in C['linhas']}
PAD = C['padrao']
PLANOS = {p['id']: p['name'] for p in json.load(open('output/_planos_financeiros.json', encoding='utf-8'))}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def pnome(c):
    c = str(c)
    m = f"{c[0]}.{c[1:3]}.{c[3:5]}.{c[5:]}" if len(c) == 7 else c
    return f"{m} {PLANOS.get(c, '')}".strip()


def brl(v):
    return ("R$ " + f"{v:,.2f}").replace(",", "@").replace(".", ",").replace("@", ".")


def obra(t):
    ap = t['_apropriacoes']
    return ' + '.join(f"{a['buildingName']} / {a['buildingUnitName']}" for a in ap) or '—'


def planilha(t):
    ap = t['_apropriacoes']
    return ' + '.join(a['costEstimationSheetName'].strip() + (f" ({a['percentage']:.0f}%)" if a['percentage'] != 100 else '') for a in ap) or '—'


def cc(t):
    bd = t['_budget']
    return ' + '.join(f"{b['costCenterId']} · " + {1: 'GARDEN - 1ª ETAPA', 2: 'GARDEN - 2ª ETAPA', 3: 'GARDEN - INCORPORAÇÃO'}.get(b['costCenterId'], '') for b in bd) or '—'


def plano(t):
    bd = t['_budget']
    return ' + '.join(pnome(b['paymentCategoriesId']) for b in bd) or '—'


def credor(t):
    return t['_credor']


CAMPOS = [("Obra / unidade", obra), ("Planilha de custo", planilha), ("Centro de custo", cc), ("Plano financeiro", plano), ("Credor", credor)]

import os as _os
G = json.load(open('output/_codigos_guias.json', encoding='utf-8'))
DET = json.load(open('output/_detalhes_titulos_matheus.json', encoding='utf-8')) if _os.path.exists('output/_detalhes_titulos_matheus.json') else {}
EXPLICA = {   # o que e cada codigo de receita, em portugues simples
    '4095': ("RET", "Regime Especial de Tributação do patrimônio de afetação — imposto da própria incorporadora sobre as vendas. É o único que fica na conta de RET."),
    '1162': ("INSS retido", "INSS de 11% descontado das notas dos fornecedores de serviço da obra (Lei 9.711) e recolhido pela Garden. É custo da obra, não imposto da empresa."),
    '5952': ("CSRF", "PIS, COFINS e CSLL retidos das notas de fornecedores PJ (4,65%). Custo da obra."),
    '1708': ("IRRF", "Imposto de renda retido de serviços prestados por PJ (1,5%). Custo da obra."),
    '8045': ("IRRF", "Imposto de renda retido — demais rendimentos. Custo da obra."),
    '1732': ("ISS retido", "ISS do Distrito Federal retido das notas de serviço de fornecedores. Custo da obra."),
}


def guia_info(tid):
    gs = G.get(str(tid), {}).get('guias', [])
    cods, vals, pas = [], [], []
    for g in gs:
        for c, _ in g['codigos']:
            if c not in cods:
                cods.append(c)
        vals += g['valores']; pas += g['pa']
    return cods, (max(vals) if vals else None), sorted(set(pas))


def data_br(iso):
    return f"{iso[8:10]}/{iso[5:7]}/{iso[:4]}" if iso and len(iso) >= 10 else '—'

itens = []
for i, t in sorted(DEPOIS.items(), key=lambda kv: (kv[1]['issueDate'], kv[0])):
    if not (DE <= t['issueDate'] <= ATE) or QUEM not in t['registeredBy'].upper():
        continue
    if (t['documentIdentificationId'] or '').strip() == 'FAT' or i not in LIN:
        continue
    a = ANTES.get(i)
    difs = [nome for nome, f in CAMPOS if a and f(a) != f(t)]
    itens.append(dict(id=i, t=t, a=a, l=LIN[i], difs=difs))

alterados = [x for x in itens if x['difs']]
iguais = [x for x in itens if not x['difs']]
tot = sum(x['t']['totalInvoiceAmount'] for x in itens)
tot_alt = sum(x['t']['totalInvoiceAmount'] for x in alterados)
mov = [x for x in alterados if 'Obra / unidade' in x['difs']]           # saiu de Imposto (RET)
tot_mov = sum(x['t']['totalInvoiceAmount'] for x in mov)
so_credor = [x for x in alterados if x['difs'] == ['Credor']]

