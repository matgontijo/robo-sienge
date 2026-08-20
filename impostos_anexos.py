"""Baixa os anexos dos titulos de imposto de 2026 e extrai o CODIGO DE RECEITA da guia
(DARF: 4095 = RET; 1162/1708/5952... = retencoes; DAR/DARE: ISS etc.)"""
import json, os, re, sys, io
import requests, config, fitz
from requests.auth import HTTPBasicAuth
B=config.SIENGE_BASE_URL; A=HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
S=requests.Session(); S.auth=A
H=json.load(open('output/_hist_impostos.json',encoding='utf-8'))
DESDE = os.environ.get('IMPOSTOS_DESDE', '2025-01-01')
ids = [int(x) for x in sys.argv[1:] if x.isdigit()] or [t['id'] for t in H if t['issueDate']>=DESDE and (t['documentIdentificationId'] or '').strip() in ('DARF','DARE','DAR','BOL','IPTU','GUIA')]
pasta='output/_anexos_impostos'; os.makedirs(pasta, exist_ok=True)
RE_COD = re.compile(r"(?:C[oó]digo\s+(?:da\s+)?Receita|Cod(?:igo)?\.?\s*(?:de\s+)?Receita|Receita)\s*[:\-]?\s*(\d{4})(?:[\s\-]*(\d{2}))?", re.I)
out={}
import glob as _glob
ja_baixados = {int(os.path.basename(f).split('_')[0]) for f in _glob.glob(pasta+'/*') if os.path.basename(f).split('_')[0].isdigit()}
for tid in ids:
    if tid in ja_baixados and '--refazer' not in sys.argv:
        continue
    r=S.get(f"{B}/bills/{tid}/attachments", timeout=60)
    itens = r.json().get('results',[]) if r.status_code==200 else []
    info={'anexos':[], 'codigos':set(), 'textos':[]}
    for it in itens:
        aid=it.get('attachmentid', it.get('id')); nome=it.get('name') or it.get('fileName') or str(aid)
        d=S.get(f"{B}/bills/{tid}/attachments/{aid}", timeout=120)
        if d.status_code!=200: continue
        p=os.path.join(pasta, f"{tid}_{aid}_{''.join(c for c in nome if c.isalnum() or c in '._-')}")
        if not p.lower().endswith('.pdf'): p+='.pdf'
        open(p,'wb').write(d.content)
        txt=''
        try:
            doc=fitz.open(stream=d.content, filetype='pdf')
            txt=chr(10).join(pg.get_text() for pg in doc)
        except Exception as e:
            txt=f'<<nao-pdf {e}>>'
        cods = [m.group(1) for m in RE_COD.finditer(txt)]
        info['anexos'].append({'id':aid,'nome':nome,'chars':len(txt),'codigos':cods})
        info['codigos'].update(cods)
        info['textos'].append(txt[:3000])
    info['codigos']=sorted(info['codigos'])
    out[tid]=info
    t=next(x for x in H if x['id']==tid)
    print(tid, (t['documentNumber'] or '')[:22].ljust(22), f"{t['totalInvoiceAmount']:>11,.2f}", '| anexos', len(itens), '| codigos', info['codigos'], '|', [a['nome'][:30] for a in info['anexos']])
json.dump(out, open('output/_anexos_impostos.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
