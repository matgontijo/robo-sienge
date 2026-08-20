"""Le os PDFs baixados em output/_anexos_impostos e extrai, por titulo:
codigo de receita + denominacao (DARF/DARE), periodo de apuracao, valor total da guia."""
import json, os, re, glob
import fitz
H={t['id']:t for t in json.load(open('output/_hist_impostos.json',encoding='utf-8'))}
pasta='output/_anexos_impostos'
RE_COD_DARF = re.compile(r"\n(\d{4})\n([A-ZÀ-Ú][A-ZÀ-Ú0-9 .,/()-]{8,})\n")
RE_VALOR = re.compile(r"Valor Total do Documento\n([\d.]+,\d{2})")
RE_PA = re.compile(r"\n((?:Janeiro|Fevereiro|Mar[cç]o|Abril|Maio|Junho|Julho|Agosto|Setembro|Outubro|Novembro|Dezembro|janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)/\d{4})\n")
# DARE-DF (ISS): "Código de Receita 1732" / "Cód. Receita"
RE_COD_DARE = re.compile(r"(?:C[oó]d(?:igo)?\.?\s*(?:de\s+)?Receita|Receita)\s*[:\-]?\s*\n?\s*(\d{4})", re.I)
RE_DEN_DARE = re.compile(r"(\d{4})\s*-\s*([A-ZÀ-Úa-zà-ú .,/()-]{6,})")
res={}
for f in sorted(glob.glob(pasta+'/*')):
    base=os.path.basename(f); tid=int(base.split('_')[0])
    try:
        doc=fitz.open(f); txt=chr(10).join(p.get_text() for p in doc)
    except Exception: continue
    if not txt.strip(): continue
    r=res.setdefault(tid, {'guias':[]})
    cods=[(m.group(1), m.group(2).strip()) for m in RE_COD_DARF.finditer(txt)]
    if not cods:
        cods=[(m.group(1), 'DARE') for m in RE_COD_DARE.finditer(txt)]
    vals=[float(v.replace('.','').replace(',','.')) for v in RE_VALOR.findall(txt)]
    pas=RE_PA.findall(txt)
    if cods or vals:
        r['guias'].append({'arquivo':base[len(str(tid))+1:][:60], 'codigos':sorted(set(cods)), 'valores':vals, 'pa':sorted(set(pas))})
for tid in sorted(res):
    t=H.get(tid,{})
    for gq in res[tid]['guias']:
        print(tid, (t.get('documentNumber') or '')[:20].ljust(20), f"{t.get('totalInvoiceAmount',0):>11,.2f}", '| guia', gq['valores'], gq['pa'], '|', gq['codigos'][:3], '|', gq['arquivo'][:45])
json.dump(res, open('output/_codigos_guias.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
