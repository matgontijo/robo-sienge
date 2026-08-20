"""Gera output/Impostos_Conferencia.html — painel a parte, autocontido e mobile, listando TODOS os
titulos de imposto (2025..hoje) com tributo pela guia, quem lancou, apropriacao atual, padrao e status.
Le output/_correcao_impostos.json (saida de impostos_analise.py)."""
import json
from collections import defaultdict

C = json.load(open('output/_correcao_impostos.json', encoding='utf-8'))
L = C['linhas']
P = C['padrao']
HIST = C['historico']

linhas = []
for l in L:
    p = l['padrao']
    linhas.append(dict(
        id=l['id'], data=l['data'], mes=l['mes'], tipo=l['tipo'], doc=l['doc'], valor=round(l['valor'], 2),
        trib=l['tributo'], fonte=l['fonte'], credor=l['credor'], quem=l['quem'].title(),
        obra=l['obra'], plan=l['planilha'], cc=l['cc'], plano=l.get('plano_nome', l['plano']),
        st=l['status'], prob=l['problemas'], obs=l.get('observacoes', []), acoes=l['acoes'],
        guia=(max(l['guia']) if l['guia'] else None),
        para=dict(obra=p['obra'], plan=f"{p['planilha']} ({p['sheetId']})", cc=p['cc'], plano=p.get('plano_nome', p['plano'])) if l['status'] == 'CORRIGIR' else None,
    ))

padrao = [dict(trib=k, obra=v['obra'], plan=v['planilha'], sheet=v['sheetId'], cc=v['cc'], plano=v.get('plano_nome', v['plano']), alt=v.get('alt', ''))
          for k, v in P.items()]
hist = {k: [h for h in v if int(h['ano']) >= 2025] for k, v in HIST.items()}

DADOS = json.dumps(dict(linhas=linhas, padrao=padrao, hist=hist, gerado=C['gerado_em'], lidos=C['n_titulos_lidos']),
                   ensure_ascii=False).replace('</', '<\\/')

HTML = r'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pente-fino dos Impostos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F3F5F4; --surf:#FFFFFF; --surf2:#EAEEEC; --ink:#1B1F24; --mute:#5F6B70; --line:#D6DDDA; --line2:#BFC9C5;
  --acc:#0E5C6B; --acc-soft:#DCEBEE;
  --bad:#A5501B; --bad-soft:#F8E7DA; --warn:#8A6D1F; --warn-soft:#F6EDD2; --ok:#2F6B4F; --ok-soft:#DDEDE4;
  --base:#6B7280; --base-soft:#E8EBEF;
  --font:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif; --mono:"IBM Plex Mono",Consolas,monospace; --disp:"Fraunces",Georgia,serif;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#0F1416; --surf:#161C1F; --surf2:#1E2629; --ink:#E6EAE8; --mute:#95A2A7; --line:#27323A; --line2:#3A474E;
  --acc:#5FB6C6; --acc-soft:#12333A;
  --bad:#E8956A; --bad-soft:#3A2417; --warn:#D9B85A; --warn-soft:#332B12; --ok:#7CC4A1; --ok-soft:#14302A;
  --base:#9AA3AD; --base-soft:#252B31; } }
:root[data-theme="dark"]{
  --bg:#0F1416; --surf:#161C1F; --surf2:#1E2629; --ink:#E6EAE8; --mute:#95A2A7; --line:#27323A; --line2:#3A474E;
  --acc:#5FB6C6; --acc-soft:#12333A;
  --bad:#E8956A; --bad-soft:#3A2417; --warn:#D9B85A; --warn-soft:#332B12; --ok:#7CC4A1; --ok-soft:#14302A;
  --base:#9AA3AD; --base-soft:#252B31; }
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--font);font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{transition:none!important;animation:none!important}}

.top{position:sticky;top:0;z-index:10;background:var(--surf);border-bottom:1px solid var(--line)}
.top-in{max-width:1180px;margin:0 auto;padding:12px 16px;display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
h1{font-family:var(--disp);font-weight:700;font-size:22px;letter-spacing:-.2px;text-wrap:balance}
.top small{color:var(--mute);font-size:12px}
.counts{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.cnt{font-size:12px;font-weight:600;padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--surf2);cursor:pointer;white-space:nowrap}
.cnt b{font-family:var(--mono);font-weight:600}
.cnt.bad{background:var(--bad-soft);color:var(--bad);border-color:transparent}
.cnt.warn{background:var(--warn-soft);color:var(--warn);border-color:transparent}
.cnt.ok{background:var(--ok-soft);color:var(--ok);border-color:transparent}
.cnt.on{outline:2px solid currentColor;outline-offset:1px}

main{max-width:1180px;margin:0 auto;padding:14px 16px 70px}
.filtros{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
.filtros input{flex:1 1 220px;min-width:0;padding:9px 12px;border-radius:10px;border:1px solid var(--line2);background:var(--surf);color:var(--ink)}
.filtros select{padding:8px 10px;border-radius:10px;border:1px solid var(--line2);background:var(--surf);color:var(--ink);font-weight:500}
.filtros .lim{padding:8px 12px;border-radius:10px;border:1px solid var(--line2);background:transparent;cursor:pointer;color:var(--mute)}
.resumo{font-size:12.5px;color:var(--mute);margin:0 2px 10px}
.resumo b{color:var(--ink);font-family:var(--mono);font-weight:600}

.lista{display:flex;flex-direction:column;gap:8px}
.row{background:var(--surf);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.row[data-st="CORRIGIR"]{border-left:4px solid var(--bad)}
.row[data-st="ATENÇÃO"]{border-left:4px solid var(--warn)}
.row[data-st="OK"]{border-left:4px solid transparent}
.row summary{list-style:none;cursor:pointer;display:grid;grid-template-columns:minmax(170px,1.2fr) 110px 90px minmax(180px,1.6fr) 110px;gap:12px;align-items:center;padding:10px 14px}
.row summary::-webkit-details-marker{display:none}
.row summary:hover{background:var(--surf2)}
.t1{font-weight:600}
.t1 .id{font-family:var(--mono);font-size:11.5px;color:var(--mute);font-weight:400;display:block}
.t1 .doc{display:block}
.val{font-family:var(--mono);font-size:13px;text-align:right}
.val small{display:block;color:var(--mute);font-size:10.5px;font-family:var(--font)}
.chip{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.3px;padding:2px 8px;border-radius:999px;background:var(--acc-soft);color:var(--acc)}
.chip.m{background:var(--surf2);color:var(--mute);font-weight:500}
.apr{font-size:12.5px;color:var(--mute);line-height:1.35}
.apr b{color:var(--ink);font-weight:500}
.apr .cc{font-family:var(--mono);font-size:11.5px}
.st{font-size:11px;font-weight:700;text-align:center;padding:4px 8px;border-radius:999px;white-space:nowrap;justify-self:end}
.st.CORRIGIR{background:var(--bad-soft);color:var(--bad)}
.st.ATENÇÃO{background:var(--warn-soft);color:var(--warn)}
.st.OK{background:var(--ok-soft);color:var(--ok)}
.quem{font-size:11.5px;color:var(--mute)}
.det{border-top:1px dashed var(--line);padding:12px 14px 14px;display:grid;grid-template-columns:1fr 1fr;gap:14px;font-size:12.5px}
.det h4{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);margin-bottom:4px;font-weight:700}
.det .para{border-left:3px solid var(--ok);padding-left:10px}
.det .de{border-left:3px solid var(--bad);padding-left:10px}
.det ul{margin-left:16px}
.det li{margin:3px 0}
.det .meta{grid-column:1/-1;color:var(--mute);font-size:12px;font-family:var(--mono)}
.vazio{padding:40px;text-align:center;color:var(--mute)}

h2{font-family:var(--disp);font-size:19px;margin:34px 0 4px;font-weight:600}
.sub{color:var(--mute);font-size:13px;margin-bottom:10px;max-width:70ch}
.tabwrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surf)}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:12.5px}
th{font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);background:var(--surf2);font-weight:700}
td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
td.n{font-family:var(--mono);text-align:right;white-space:nowrap}
td.trib{font-weight:700}
td .alt{display:block;color:var(--mute);font-size:11.5px;margin-top:2px}
.hist-row{color:var(--mute)} .hist-row td.trib{color:var(--ink)}
.rec td{background:var(--acc-soft)}
.foot{margin-top:26px;color:var(--mute);font-size:12px;max-width:80ch}

@media (max-width:820px){
  .row summary{grid-template-columns:1fr auto;grid-template-areas:"t1 val" "trib st" "apr apr";gap:6px 10px}
  .t1{grid-area:t1}.val{grid-area:val}.tribcell{grid-area:trib}.apr{grid-area:apr}.st{grid-area:st}
  .det{grid-template-columns:1fr}
  h1{font-size:19px}
}
</style>
</head>
<body>
<header class="top"><div class="top-in">
  <div><h1>Pente-fino dos Impostos</h1><small id="meta"></small></div>
  <div class="counts" id="counts"></div>
</div></header>

<main>
  <div class="filtros">
    <input id="q" type="search" placeholder="Buscar título, documento, credor, planilha…" autocomplete="off">
    <select id="f-ano"></select>
    <select id="f-trib"><option value="">Todos os tributos</option></select>
    <select id="f-quem"><option value="">Quem lançou: todos</option></select>
    <select id="f-ord"><option value="data">Mais recente</option><option value="valor">Maior valor</option><option value="st">Status</option></select>
    <button class="lim" id="limpar">limpar</button>
  </div>
  <p class="resumo" id="resumo"></p>
  <div class="lista" id="lista"></div>

  <h2>Padrão por tributo</h2>
  <p class="sub">Como a equipe lançou cada imposto em 2025 e 2026 (sem os títulos do Matheus). A linha destacada é o destino recomendado para corrigir.</p>
  <div class="tabwrap"><table id="tab-pad"><thead><tr><th>Tributo</th><th>Ano</th><th>Obra / unidade</th><th>Planilha de custo</th><th>CC</th><th>Plano</th><th style="text-align:right">Títulos</th><th style="text-align:right">Total</th></tr></thead><tbody></tbody></table></div>

  <p class="foot" id="foot"></p>
</main>

<script>
const D = __DADOS__;
const $ = s => document.querySelector(s);
const brl = v => v == null ? '—' : v.toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ORD_ST = {CORRIGIR:0,'ATENÇÃO':1,OK:2};
const F = {q:'', ano:'2026', trib:'', quem:'', st:'', ord:'data'};

$('#meta').textContent = `${D.linhas.length} títulos de imposto · ${D.lidos} lidos na API · gerado ${D.gerado.replace('T',' ')}`;
$('#foot').textContent = 'Tributo definido pelo código de receita lido na guia anexada (4095 RET · 1162 INSS · 5952 CSRF · 1708/8045 IRRF · 1732 ISS); quando não há guia legível, pelo texto do documento. Apropriação lida de GET /bills/{id}/buildings-cost e /budget-categories. Status compara com o padrão dos outros usuários em 2025–2026.';

// filtros
const anos = [...new Set(D.linhas.map(l => l.data.slice(0,4)))].sort().reverse();
$('#f-ano').innerHTML = '<option value="">Todos os anos</option>' + anos.map(a => `<option value="${a}" ${a===F.ano?'selected':''}>${a}</option>`).join('');
const tribs = [...new Set(D.linhas.map(l => l.trib))].sort();
$('#f-trib').innerHTML += tribs.map(t => `<option value="${t}">${t}</option>`).join('');
const quems = [...new Set(D.linhas.map(l => l.quem))].sort();
$('#f-quem').innerHTML += quems.map(q => `<option value="${q}">${q}</option>`).join('');

function filtrar(){
  const q = F.q.toLowerCase();
  let r = D.linhas.filter(l => (!F.ano || l.data.startsWith(F.ano)) && (!F.trib || l.trib===F.trib) && (!F.quem || l.quem===F.quem) && (!F.st || l.st===F.st)
      && (!q || [l.id,l.doc,l.credor,l.plan,l.obra,l.plano,l.trib,l.quem,...l.prob].join(' ').toLowerCase().includes(q)));
  if (F.ord==='valor') r.sort((a,b)=>b.valor-a.valor);
  else if (F.ord==='st') r.sort((a,b)=>ORD_ST[a.st]-ORD_ST[b.st] || b.data.localeCompare(a.data));
  else r.sort((a,b)=>b.data.localeCompare(a.data) || b.id-a.id);
  return r;
}
function counts(base){
  const n = s => base.filter(l=>l.st===s);
  const v = s => n(s).reduce((a,l)=>a+l.valor,0);
  $('#counts').innerHTML = [['CORRIGIR','bad','corrigir'],['ATENÇÃO','warn','atenção'],['OK','ok','ok']].map(([s,c,r]) =>
    `<button class="cnt ${c} ${F.st===s?'on':''}" data-st="${s}" title="${brl(v(s))}"><b>${n(s).length}</b> ${r}</button>`).join('');
}
function render(){
  const base = D.linhas.filter(l => (!F.ano || l.data.startsWith(F.ano)));
  counts(base);
  const r = filtrar();
  const tot = r.reduce((a,l)=>a+l.valor,0);
  $('#resumo').innerHTML = `<b>${r.length}</b> título(s) · <b>${brl(tot)}</b>` + (F.st==='CORRIGIR' ? ' — apropriação a mudar' : '');
  const L = $('#lista');
  if (!r.length){ L.innerHTML = '<div class="vazio">Nada com esses filtros.</div>'; return; }
  L.innerHTML = r.map(l => `
  <details class="row" data-st="${l.st}">
    <summary>
      <div class="t1"><span class="id">título ${l.id} · ${l.data.slice(8,10)}/${l.data.slice(5,7)}/${l.data.slice(0,4)}</span><span class="doc">${esc(l.tipo)} ${esc(l.doc)}</span><span class="quem">${esc(l.quem)}</span></div>
      <div class="val">${brl(l.valor)}${l.guia!=null && Math.abs(l.guia-l.valor)>0.05 ? `<small>guia ${brl(l.guia)}</small>`:''}</div>
      <div class="tribcell"><span class="chip">${l.trib}</span> <span class="chip m" title="${esc(l.fonte)}">${l.fonte.startsWith('código') ? 'cód. '+l.fonte.replace('código de receita ','') : 'texto'}</span></div>
      <div class="apr"><b>${esc(l.obra)}</b><br>${esc(l.plan)} <span class="cc">· CC ${esc(l.cc)} · ${esc(l.plano)}</span></div>
      <span class="st ${l.st}">${l.st==='OK'?'ok':l.st.toLowerCase()}</span>
    </summary>
    <div class="det">
      ${l.para ? `<div class="de"><h4>Está em</h4>${esc(l.obra)}<br>${esc(l.plan)}<br>CC ${esc(l.cc)} · plano ${esc(l.plano)}</div>
                  <div class="para"><h4>Deve ir para</h4>${esc(l.para.obra)}<br>${esc(l.para.plan)}<br>CC ${l.para.cc} · plano ${l.para.plano}</div>`
               : `<div><h4>Apropriação</h4>${esc(l.obra)}<br>${esc(l.plan)}<br>CC ${esc(l.cc)} · plano ${esc(l.plano)}</div>
                  <div><h4>Padrão ${l.trib}</h4>${esc((D.padrao.find(p=>p.trib===l.trib)||{}).obra||'')}<br>${esc((D.padrao.find(p=>p.trib===l.trib)||{}).plan||'')}<br>CC ${(D.padrao.find(p=>p.trib===l.trib)||{}).cc||''} · plano ${(D.padrao.find(p=>p.trib===l.trib)||{}).plano||''}</div>`}
      ${l.prob.length ? `<div><h4>O que está errado / atenção</h4><ul>${l.prob.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></div>`:''}
      ${l.acoes.length ? `<div><h4>Ação</h4><ul>${l.acoes.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></div>`:''}
      ${l.obs.length ? `<div><h4>Observação</h4><ul>${l.obs.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></div>`:''}
      <div class="meta">credor: ${esc(l.credor)} · fonte do tributo: ${esc(l.fonte)}</div>
    </div>
  </details>`).join('');
}
// tabela do padrão
(function(){
  const tb = $('#tab-pad tbody'); let h = '';
  for (const p of D.padrao){
    h += `<tr class="rec"><td class="trib">${p.trib}</td><td>recom.</td><td>${esc(p.obra)}</td><td>${esc(p.plan)} (${p.sheet})${p.alt?`<span class="alt">${esc(p.alt)}</span>`:''}</td><td>${p.cc}</td><td>${p.plano}</td><td class="n"></td><td class="n"></td></tr>`;
    for (const x of (D.hist[p.trib]||[])) h += `<tr class="hist-row"><td class="trib"></td><td>${x.ano}</td><td>${esc(x.obra)}</td><td>${esc(x.planilha)}</td><td>${esc(x.cc)}</td><td>${esc(x.plano_nome||x.plano)}</td><td class="n">${x.n}</td><td class="n">${brl(x.total)}</td></tr>`;
  }
  tb.innerHTML = h;
})();
// eventos
$('#q').addEventListener('input', e => { F.q = e.target.value; render(); });
$('#f-ano').addEventListener('change', e => { F.ano = e.target.value; render(); });
$('#f-trib').addEventListener('change', e => { F.trib = e.target.value; render(); });
$('#f-quem').addEventListener('change', e => { F.quem = e.target.value; render(); });
$('#f-ord').addEventListener('change', e => { F.ord = e.target.value; render(); });
$('#counts').addEventListener('click', e => { const b = e.target.closest('[data-st]'); if(!b) return; F.st = F.st===b.dataset.st ? '' : b.dataset.st; render(); });
$('#limpar').addEventListener('click', () => { Object.assign(F,{q:'',ano:'2026',trib:'',quem:'',st:'',ord:'data'}); $('#q').value=''; $('#f-ano').value='2026'; $('#f-trib').value=''; $('#f-quem').value=''; $('#f-ord').value='data'; render(); });
render();
</script>
</body>
</html>
'''
open('output/Impostos_Conferencia.html', 'w', encoding='utf-8').write(HTML.replace('__DADOS__', DADOS))
print('gerado output/Impostos_Conferencia.html', f"({len(linhas)} linhas)")
