"""Relatorio ANTES x DEPOIS das apropriacoes (PDF). Os dados vem de relatorio_correcao_dados.py.
Uso: python relatorio_correcao_impostos.py  [--quem MATHEUS] [--de 2026-01-01] [--ate 2026-08-31]"""
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

from relatorio_correcao_dados import *  # noqa: F401,F403

# ---------- estilos ----------
TINTA = colors.HexColor("#16202A"); MUTED = colors.HexColor("#5E6F7C"); LINHA = colors.HexColor("#D8E0E4"); FUNDO = colors.HexColor("#F2F5F4")
ACC = colors.HexColor("#0E5C6B"); ACC_SOFT = colors.HexColor("#DBEAEE")
BAD = colors.HexColor("#A5501B"); BAD_SOFT = colors.HexColor("#FAEADC")
OK = colors.HexColor("#2F6B4F"); OK_SOFT = colors.HexColor("#DDEDE4")
H1 = ParagraphStyle("H1", fontName="Times-Roman", fontSize=24, leading=28, textColor=TINTA, spaceAfter=4)
EYE = ParagraphStyle("EYE", fontName="Helvetica-Bold", fontSize=7.5, textColor=MUTED)
LEAD = ParagraphStyle("LEAD", fontName="Helvetica", fontSize=10.5, leading=15, textColor=MUTED)
H2 = ParagraphStyle("H2", fontName="Times-Roman", fontSize=15, leading=18, textColor=TINTA, spaceBefore=16, spaceAfter=2)
SEC = ParagraphStyle("SEC", fontName="Helvetica", fontSize=8.8, leading=12.5, textColor=MUTED)
P = ParagraphStyle("P", fontName="Helvetica", fontSize=8.8, leading=12.5, textColor=TINTA)
CEL = ParagraphStyle("CEL", fontName="Helvetica", fontSize=7.8, leading=10, textColor=TINTA)
CELB = ParagraphStyle("CELB", parent=CEL, fontName="Helvetica-Bold")
CELM = ParagraphStyle("CELM", parent=CEL, textColor=MUTED)
CELR = ParagraphStyle("CELR", parent=CEL, alignment=TA_RIGHT)
CELC = ParagraphStyle("CELC", parent=CEL, alignment=TA_CENTER)
ROT = ParagraphStyle("ROT", fontName="Helvetica-Bold", fontSize=6.8, leading=9, textColor=MUTED)
KPI_N = ParagraphStyle("KN", fontName="Times-Roman", fontSize=24, leading=26, textColor=TINTA)
KPI_L = ParagraphStyle("KL", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED)
TIT = ParagraphStyle("TIT", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=TINTA)
TITM = ParagraphStyle("TITM", fontName="Helvetica", fontSize=8, leading=10, textColor=MUTED)
VAL = ParagraphStyle("VAL", fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=TINTA, alignment=TA_RIGHT)

PAGE = A4
doc = BaseDocTemplate(f"output/Relatorio_Correcao_Impostos_{QUEM.title()}_2026_tabelas.pdf", pagesize=PAGE,
                      leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=17 * mm,
                      title=f"Correção dos impostos — {QUEM.title()} 2026", author="Robô de Conferência")


def rodape(canv, d):
    canv.saveState(); canv.setFont("Helvetica", 6.5); canv.setFillColor(MUTED); canv.setStrokeColor(LINHA)
    canv.line(16 * mm, 12 * mm, PAGE[0] - 16 * mm, 12 * mm)
    canv.drawString(16 * mm, 8.5 * mm, "Grupo Garden · correção das apropriações de impostos · antes = leitura da API em 19/08/2026 de manhã · depois = leitura atual")
    canv.drawRightString(PAGE[0] - 16 * mm, 8.5 * mm, f"página {d.page}")
    canv.restoreState()


doc.addPageTemplates([PageTemplate(id="p", onPage=rodape, frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")])])
W = doc.width
el = []
quem_nome = next((x['t']['registeredBy'].title() for x in itens), QUEM.title())

# ================= CAPA / RESUMO =================
el.append(Paragraph(f"GRUPO GARDEN &nbsp;·&nbsp; IMPOSTOS LANÇADOS POR {quem_nome.upper()} &nbsp;·&nbsp; {DE[5:7]}/{DE[:4]} A {ATE[5:7]}/{ATE[:4]}", EYE))
el.append(Paragraph("Correção das apropriações", H1))
el.append(Paragraph(
    f"Dos <b><font color='#16202A'>{len(itens)} títulos de imposto</font></b> no seu nome no período "
    f"({brl(tot)}), <b><font color='#16202A'>{len(mov)} tinham sido lançados na conta de RET</font></b> "
    f"— GARDEN - INCORPORAÇÃO / Imposto (RET) / centro de custo 3 — mas a guia mostra que são retenções da obra. "
    f"Todos foram movidos para <b><font color='#16202A'>GARDEN - 1ª ETAPA / OBRA GARDEN / centro de custo 1</font></b>, "
    f"cada um no plano financeiro do seu tributo. {len(so_credor)} outro{'s' if len(so_credor) != 1 else ''} só trocou o credor. "
    f"Hoje nenhum título está fora do padrão da equipe.", LEAD))
el.append(Spacer(1, 12))

KPI_V = ParagraphStyle("KV", parent=KPI_N, fontSize=17, leading=26)
kp = [[Paragraph(str(len(itens)), KPI_N), Paragraph(str(len(mov)), KPI_N), Paragraph(brl(tot_mov), KPI_V), Paragraph(str(len(so_credor) + sum(1 for x in mov if 'Credor' in x['difs'])), KPI_N), Paragraph("0", ParagraphStyle("k0", parent=KPI_N, textColor=OK))],
      [Paragraph("títulos de imposto<br/>no período", KPI_L), Paragraph("movidos da conta de RET<br/>para a obra", KPI_L), Paragraph("valor movido", KPI_L), Paragraph("credor corrigido<br/>(Residencial Garden → Receita)", KPI_L), Paragraph("ainda fora<br/>do padrão", KPI_L)]]
kt = Table(kp, colWidths=[W * x for x in (.16, .2, .26, .22, .16)])
kt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FUNDO), ("VALIGN", (0, 0), (-1, 0), "BOTTOM"), ("VALIGN", (0, 1), (-1, 1), "TOP"),
                        ("LINEAFTER", (0, 0), (-2, -1), 1, colors.white),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, 0), 9), ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 0), ("TOPPADDING", (0, 1), (-1, 1), 1)]))
el.append(kt)

# --- resumo DE -> PARA por tributo
el.append(Paragraph("De onde saiu, para onde foi", H2))
el.append(Paragraph("Resumo por tributo. A guia (código de receita) diz o que cada título é; o destino é o que a equipe usa para aquele tributo.", SEC))
el.append(Spacer(1, 6))
grp = OrderedDict()
for x in mov:
    g = grp.setdefault(x['l']['tributo'], dict(n=0, v=0.0, ids=[], planilhas=set(), planos=set()))
    g['n'] += 1; g['v'] += x['t']['totalInvoiceAmount']; g['ids'].append(str(x['id']))
    g['planilhas'].add(planilha(x['t'])); g['planos'].add(plano(x['t']))
ordem = [k for k in ("INSS", "CSRF", "IRRF", "ISS") if k in grp] + [k for k in grp if k not in ("INSS", "CSRF", "IRRF", "ISS")]
d = [[Paragraph("<b><font color='white'>Tributo (guia)</font></b>", CEL), Paragraph("<b><font color='white'>Títulos</font></b>", CEL),
      Paragraph("<b><font color='white'>Valor</font></b>", CELR), Paragraph("<b><font color='white'>ESTAVA EM</font></b>", CEL), Paragraph("", CELC),
      Paragraph("<b><font color='white'>FOI PARA</font></b>", CEL)]]
for k in ordem:
    g = grp[k]
    d.append([Paragraph(f"<b>{k}</b><br/><font color='#5E6F7C'>{ {'INSS': 'cód. 1162', 'CSRF': 'cód. 5952', 'IRRF': 'cód. 1708/8045', 'ISS': 'cód. 1732'}.get(k, '') }</font>", CEL),
              Paragraph(f"<b>{g['n']}</b><br/><font color='#5E6F7C'>{', '.join(g['ids'])}</font>", CEL),
              Paragraph(brl(g['v']), CELR),
              Paragraph("GARDEN - INCORPORAÇÃO / IMPOSTOS (RET)<br/>Imposto (RET)<br/>CC 3 · " + ("2.04.05.03 Impostos Federais" if k != 'ISS' else "2.04.03.01 ISS"), CEL),
              Paragraph("→", ParagraphStyle("ar", parent=CELC, fontSize=13, textColor=ACC)),
              Paragraph("GARDEN - 1ª ETAPA / OBRA GARDEN<br/>" + '<br/>'.join(sorted(g['planilhas'])) + "<br/>CC 1 · " + ' / '.join(sorted(g['planos'])), CEL)])
d.append([Paragraph("<b>Total</b>", CEL), Paragraph(f"<b>{len(mov)}</b>", CEL), Paragraph(f"<b>{brl(tot_mov)}</b>", CELR), "", "", ""])
t = Table(d, colWidths=[W * x for x in (.11, .12, .13, .29, .05, .30)])
t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TINTA), ("GRID", (0, 0), (-1, -1), .3, LINHA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                       ("BACKGROUND", (3, 1), (3, -2), BAD_SOFT), ("BACKGROUND", (5, 1), (5, -2), OK_SOFT),
                       ("BACKGROUND", (0, -1), (-1, -1), FUNDO),
                       ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
el.append(t)
el.append(Spacer(1, 8))
el.append(Paragraph("O que é cada código de receita (o que está impresso na guia anexada)", ROT))
el.append(Spacer(1, 3))
dl = [[Paragraph(f"<b>{c}</b>", CEL), Paragraph(f"<b>{n}</b>", CEL), Paragraph(txt, CELM)] for c, (n, txt) in EXPLICA.items() if c != '8045']
tl = Table(dl, colWidths=[W * .08, W * .13, W * .79])
tl.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), .3, LINHA), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
el.append(tl)
if so_credor or any('Credor' in x['difs'] for x in mov):
    ids = [str(x['id']) for x in alterados if 'Credor' in x['difs']]
    el.append(Spacer(1, 6))
    el.append(Paragraph(f"Credor: os títulos <b>{', '.join(ids)}</b> estavam com credor <b>Residencial Garden Empreendimentos</b> (a própria empresa) e passaram para <b>Secretaria da Receita Federal do Brasil</b>.", P))

# ================= TÍTULO A TÍTULO =================
el.append(PageBreak())
el.append(Paragraph("Título a título", H2))
el.append(Paragraph("Cada cartão mostra o que estava e o que ficou. Só as linhas que mudaram estão destacadas; o resto se manteve.", SEC))
el.append(Spacer(1, 6))

for x in alterados:
    t, a, l = x['t'], x['a'], x['l']
    dt = t['issueDate']
    cab = Table([[Paragraph(f"Título {t['id']} &nbsp;·&nbsp; {(t['documentIdentificationId'] or '').strip()} {t['documentNumber']}", TIT),
                  Paragraph(brl(t['totalInvoiceAmount']), VAL)],
                 [Paragraph(f"{l['tributo']} · {l['fonte'].replace('código de receita', 'guia cód.')} · emitido {dt[8:10]}/{MESES[int(dt[5:7]) - 1]}/{dt[:4]}", TITM), ""]],
                colWidths=[W * .72, W * .28])
    cab.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), FUNDO), ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACC),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
                             ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    linhas = [[Paragraph("", ROT), Paragraph("ANTES", ROT), Paragraph("", ROT), Paragraph("DEPOIS", ROT)]]
    est = [("LINEBELOW", (0, 0), (-1, 0), .5, LINHA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
           ("BOX", (0, 0), (-1, -1), .3, LINHA)]
    for k, (nome, f) in enumerate(CAMPOS, start=1):
        va, vd = f(a), f(t)
        mudou = va != vd
        linhas.append([Paragraph(nome, ROT),
                       Paragraph(va, ParagraphStyle("a", parent=CEL, textColor=BAD if mudou else MUTED)),
                       Paragraph("→" if mudou else "", ParagraphStyle("ar2", parent=CELC, textColor=ACC, fontSize=11)),
                       Paragraph(f"<b>{vd}</b>" if mudou else vd, ParagraphStyle("d", parent=CEL, textColor=TINTA if mudou else MUTED))])
        if mudou:
            est += [("BACKGROUND", (1, k), (1, k), BAD_SOFT), ("BACKGROUND", (3, k), (3, k), OK_SOFT)]
        if k < len(CAMPOS):
            est.append(("LINEBELOW", (0, k), (-1, k), .3, LINHA))
    corpo = Table(linhas, colWidths=[W * x for x in (.16, .39, .05, .40)])
    corpo.setStyle(TableStyle(est))
    # contexto em linguagem simples
    cods, vguia, pas = guia_info(t['id'])
    nome_trib = next((EXPLICA[c][0] for c in cods if c in EXPLICA), l['tributo'])
    expl = next((EXPLICA[c][1] for c in cods if c in EXPLICA), "")
    d = DET.get(str(t['id']), {})
    parc = d.get('parcelas', [{}])[0] if d.get('parcelas') else {}
    porque = (f"<b>Por quê:</b> a guia anexada é {(t['documentIdentificationId'] or '').strip()} código <b>{'/'.join(cods) or '—'}</b> = <b>{nome_trib}</b>. {expl} "
              + ("A equipe lança isso na 1ª Etapa / Obra Garden, centro de custo 1 — nunca na conta de RET." if 'Obra / unidade' in x['difs'] else
                 "A apropriação já estava certa; só o credor estava como a própria Garden em vez do órgão arrecadador."))
    fatos = []
    if vguia is not None:
        dif = t['totalInvoiceAmount'] - vguia
        fatos.append(f"guia R$ {brl(vguia)[3:]}" + (f" (título {brl(t['totalInvoiceAmount'])[3:]} — diferença {brl(dif)[3:]})" if abs(dif) > 0.05 else " = título"))
    if pas:
        fatos.append("competência " + ", ".join(pas))
    if parc:
        fatos.append(f"vencimento {data_br(parc.get('venc'))} · {str(parc.get('sit') or '').lower()}")
    if d.get('changedDate'):
        fatos.append(f"alterado em {data_br(d['changedDate'][:10])} {d['changedDate'][11:16]}")
    rod = Table([[Paragraph(porque, ParagraphStyle("pq", parent=CEL, leading=10.5))],
                 [Paragraph(" · ".join(fatos), CELM)]], colWidths=[W])
    rod.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.white), ("BOX", (0, 0), (-1, -1), .3, LINHA),
                             ("LINEABOVE", (0, 0), (-1, 0), 0, colors.white),
                             ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                             ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
                             ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    el.append(KeepTogether([cab, corpo, rod, Spacer(1, 10)]))

# ================= NÃO MUDARAM =================
el.append(PageBreak())
el.append(Paragraph("Os que não mudaram", H2))
el.append(Paragraph("Já estavam no padrão da equipe.", SEC))
el.append(Spacer(1, 6))
d2 = [[Paragraph(f"<b><font color='white'>{c}</font></b>", CELR if c == "Valor" else CEL) for c in ["Título", "Documento", "Guia = tributo", "Valor", "Onde está", "Por que está certo"]]]
for x in iguais:
    t, l = x['t'], x['l']
    cods, _, _ = guia_info(t['id'])
    nome_trib = next((EXPLICA[c][0] for c in cods if c in EXPLICA), l['tributo'])
    if l['tributo'] == 'RET':
        motivo = "RET é o imposto da incorporadora: a conta certa é mesmo Imposto (RET) / CC 3."
    elif l['tributo'] == 'TAXAS':
        motivo = "Taxa da incorporação: vai em Taxas / CC 3, como a equipe sempre fez."
    else:
        motivo = f"{nome_trib} é custo da obra e já estava na 1ª Etapa / CC 1, no plano do tributo."
    d2.append([Paragraph(f"<b>{t['id']}</b>", CEL),
               Paragraph(f"{(t['documentIdentificationId'] or '').strip()} {t['documentNumber']}", CEL),
               Paragraph(f"{'/'.join(cods) + ' = ' if cods else ''}<b>{nome_trib}</b>", CEL), Paragraph(brl(t['totalInvoiceAmount']), CELR),
               Paragraph(f"{obra(t)}<br/>{planilha(t)}<br/>CC {cc(t)} · {plano(t)}", CELM), Paragraph(motivo, CEL)])
t2 = Table(d2, colWidths=[W * x for x in (.07, .16, .12, .14, .29, .22)], repeatRows=1)
t2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TINTA), ("GRID", (0, 0), (-1, -1), .3, LINHA), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
el.append(t2)

# ================= PADRÃO =================
bloco_pad = [Paragraph("O padrão da equipe (referência)", H2),
             Paragraph("Como a Ludmylla e a Thalita apropriam cada imposto em 2025–2026. Foi isso que serviu de régua.", SEC),
             Spacer(1, 6)]
d3 = [[Paragraph(f"<b><font color='white'>{c}</font></b>", CEL) for c in ["Tributo", "Obra / unidade", "Planilha de custo", "CC", "Plano financeiro"]]]
for k in ["RET", "INSS", "CSRF", "IRRF", "ISS", "IPTU", "TAXAS"]:
    p = PAD[k]
    d3.append([Paragraph(f"<b>{k}</b>", CEL), Paragraph(p['obra'], CEL), Paragraph(p['planilha'], CEL),
               Paragraph(str(p['cc']), CEL), Paragraph(pnome(p['plano']), CEL)])
t3 = Table(d3, colWidths=[W * x for x in (.1, .3, .3, .06, .24)], repeatRows=1)
t3.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), TINTA), ("GRID", (0, 0), (-1, -1), .3, LINHA), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BACKGROUND", (0, 1), (-1, 1), ACC_SOFT),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
bloco_pad += [t3, Spacer(1, 4),
              Paragraph("Só o RET (cód. 4095) fica na conta de RET. INSS, CSRF, IRRF e ISS são retenções de fornecedores da obra e vão para a 1ª Etapa.", SEC)]
el.append(KeepTogether(bloco_pad))

doc.build(el)
print(f"gerado output/Relatorio_Correcao_Impostos_{QUEM.title()}_2026_tabelas.pdf | {len(itens)} títulos, {len(alterados)} alterados, {brl(tot_alt)}")
