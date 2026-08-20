"""Versao HTML (autocontida, mobile) do relatorio ANTES x DEPOIS. Mesmos dados do PDF.
Uso: python relatorio_correcao_html.py [--quem MATHEUS] [--de 2026-01-01] [--ate 2026-08-31]
Saida: output/Relatorio_Correcao_Impostos_<quem>_2026.html"""
import json
from collections import OrderedDict
from datetime import datetime

from relatorio_correcao_dados import *  # noqa: F401,F403

MESN = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]


def item_json(x):
    t, a, l = x['t'], x['a'], x['l']
    cods, vguia, pas = guia_info(t['id'])
    d = DET.get(str(t['id']), {})
    parc = d.get('parcelas', [{}])[0] if d.get('parcelas') else {}
    nome_trib = next((EXPLICA[c][0] for c in cods if c in EXPLICA), l['tributo'])
    expl = next((EXPLICA[c][1] for c in cods if c in EXPLICA), "")
    campos = []
    for nome, f in CAMPOS:
        va, vd = (f(a) if a else '—'), f(t)
        campos.append(dict(nome=nome, antes=va, depois=vd, mudou=va != vd))
    if x['difs']:
        porque = (f"A guia anexada é {(t['documentIdentificationId'] or '').strip()} código {'/'.join(cods) or '—'} = {nome_trib}. {expl} "
                  + ("A equipe lança isso na 1ª Etapa / Obra Garden, centro de custo 1 — nunca na conta de RET."
                     if 'Obra / unidade' in x['difs'] else
                     "A apropriação já estava certa; só o credor estava como a própria Garden em vez do órgão arrecadador."))
    else:
        if l['tributo'] == 'RET':
            porque = "RET é o imposto da incorporadora: a conta certa é mesmo Imposto (RET) / centro de custo 3."
        elif l['tributo'] == 'TAXAS':
            porque = "Taxa da incorporação: vai em Taxas / centro de custo 3, como a equipe sempre fez."
        else:
            porque = f"{nome_trib} é custo da obra e já estava na 1ª Etapa / centro de custo 1, no plano do tributo."
    return dict(
        id=t['id'], tipo=(t['documentIdentificationId'] or '').strip(), doc=t['documentNumber'], valor=t['totalInvoiceAmount'],
        data=t['issueDate'], trib=l['tributo'], nome_trib=nome_trib, cods='/'.join(cods),
        difs=x['difs'], movido='Obra / unidade' in x['difs'], so_credor=(x['difs'] == ['Credor']),
        campos=campos, porque=porque,
        guia=vguia, dif_guia=(round(t['totalInvoiceAmount'] - vguia, 2) if vguia is not None else None),
        competencia=', '.join(pas), venc=parc.get('venc'), sit=parc.get('sit'), alterado=d.get('changedDate'),
    )


itens_js = [item_json(x) for x in itens]
grp = OrderedDict()
for x in mov:
    g = grp.setdefault(x['l']['tributo'], dict(n=0, v=0.0, ids=[], planilhas=set(), planos=set(), plano_antes=set()))
    g['n'] += 1; g['v'] += x['t']['totalInvoiceAmount']; g['ids'].append(x['id'])
    g['planilhas'].add(planilha(x['t'])); g['planos'].add(plano(x['t'])); g['plano_antes'].add(plano(x['a']))
ordem = [k for k in ("INSS", "CSRF", "IRRF", "ISS") if k in grp] + [k for k in grp if k not in ("INSS", "CSRF", "IRRF", "ISS")]
fluxo = [dict(trib=k, n=g['n'], v=round(g['v'], 2), ids=g['ids'], planilhas=sorted(g['planilhas']), planos=sorted(g['planos']),
              plano_antes=sorted(g['plano_antes']), cod={'INSS': '1162', 'CSRF': '5952', 'IRRF': '1708/8045', 'ISS': '1732'}.get(k, ''))
         for k, g in grp.items()]
fluxo.sort(key=lambda f: ordem.index(f['trib']))
cred_ids = [x['id'] for x in alterados if 'Credor' in x['difs']]
padrao = [dict(trib=k, obra=PAD[k]['obra'], planilha=PAD[k]['planilha'], cc=PAD[k]['cc'], plano=pnome(PAD[k]['plano']))
          for k in ["RET", "INSS", "CSRF", "IRRF", "ISS", "IPTU", "TAXAS"]]
explica = [dict(cod=c, nome=n, txt=t) for c, (n, t) in EXPLICA.items() if c != '8045']
quem_nome = next((x['t']['registeredBy'].title() for x in itens), QUEM.title())

D = dict(quem=quem_nome, de=DE, ate=ATE, gerado=datetime.now().strftime('%d/%m/%Y %H:%M'),
         n=len(itens), tot=round(tot, 2), n_mov=len(mov), tot_mov=round(tot_mov, 2), n_cred=len(cred_ids), cred_ids=cred_ids,
         n_alt=len(alterados), itens=itens_js, fluxo=fluxo, padrao=padrao, explica=explica)
DADOS = json.dumps(D, ensure_ascii=False).replace('</', '<\\/')

HTML = r'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Correção das Apropriações</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#F3F5F4; --surf:#FFFFFF; --surf2:#EAEEEC; --ink:#1B1F24; --mute:#5F6B70; --line:#D6DDDA; --line2:#BFC9C5;
  --acc:#0E5C6B; --acc-soft:#DCEBEE;
  --bad:#A5501B; --bad-soft:#F8E7DA; --ok:#2F6B4F; --ok-soft:#DDEDE4;
  --font:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif; --mono:"IBM Plex Mono",Consolas,monospace; --disp:"Fraunces",Georgia,serif;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#0F1416; --surf:#161C1F; --surf2:#1E2629; --ink:#E6EAE8; --mute:#95A2A7; --line:#27323A; --line2:#3A474E;
  --acc:#5FB6C6; --acc-soft:#12333A; --bad:#E8956A; --bad-soft:#3A2417; --ok:#7CC4A1; --ok-soft:#14302A; } }
:root[data-theme="dark"]{
  --bg:#0F1416; --surf:#161C1F; --surf2:#1E2629; --ink:#E6EAE8; --mute:#95A2A7; --line:#27323A; --line2:#3A474E;
  --acc:#5FB6C6; --acc-soft:#12333A; --bad:#E8956A; --bad-soft:#3A2417; --ok:#7CC4A1; --ok-soft:#14302A; }
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--font);font-size:15px;line-height:1.5;font-variant-numeric:tabular-nums}
button,input{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
main{max-width:980px;margin:0 auto;padding:26px 18px 80px}
.eye{font-size:11px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--mute)}
h1{font-family:var(--disp);font-weight:700;font-size:clamp(26px,4.5vw,36px);line-height:1.1;letter-spacing:-.3px;margin:6px 0 10px;text-wrap:balance}
.lead{font-size:16px;color:var(--mute);max-width:64ch;line-height:1.55}
.lead b{color:var(--ink)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0 8px}
.kpi{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:14px 16px}
.kpi b{display:block;font-family:var(--disp);font-weight:600;font-size:30px;line-height:1.05;letter-spacing:-.5px}
.kpi.money b{font-size:22px;padding-top:6px}
.kpi span{display:block;font-size:12px;color:var(--mute);margin-top:6px;line-height:1.3}
.kpi.ok b{color:var(--ok)}

h2{font-family:var(--disp);font-weight:600;font-size:23px;margin:38px 0 4px;letter-spacing:-.2px}
.sub{color:var(--mute);font-size:14px;margin-bottom:14px;max-width:70ch}

/* fluxo de -> para */
.fluxo{display:grid;grid-template-columns:1fr 48px 1fr;gap:10px;align-items:stretch}
.box{border-radius:14px;padding:14px 16px;border:1px solid var(--line)}
.box.de{background:var(--bad-soft);border-color:transparent}
.box.para{background:var(--ok-soft);border-color:transparent}
.box h3{font-size:11px;text-transform:uppercase;letter-spacing:.8px;font-weight:700;margin-bottom:6px}
.box.de h3{color:var(--bad)} .box.para h3{color:var(--ok)}
.box .big{font-weight:700;font-size:16px;line-height:1.3}
.box .m{color:var(--mute);font-size:13px;margin-top:4px}
.arrow{display:flex;align-items:center;justify-content:center;color:var(--acc);font-size:30px}
.fl-row{display:grid;grid-template-columns:1fr 48px 1fr;gap:10px;margin-top:10px;align-items:stretch}
.fl-row .box{padding:12px 14px}
.trib{display:inline-block;font-weight:700;font-size:12px;letter-spacing:.4px;background:var(--acc-soft);color:var(--acc);padding:2px 9px;border-radius:999px;margin-right:6px}
.ids{font-family:var(--mono);font-size:12px;color:var(--mute)}
.val{font-family:var(--mono);font-weight:600}
.cred{margin-top:14px;background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:14px}
.legenda{margin-top:14px;background:var(--surf);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.legenda details{border-bottom:1px solid var(--line)}
.legenda details:last-child{border-bottom:0}
.legenda summary{cursor:pointer;padding:10px 16px;font-weight:600;font-size:14px;list-style:none;display:flex;gap:10px;align-items:baseline}
.legenda summary::-webkit-details-marker{display:none}
.legenda summary code{font-family:var(--mono);font-weight:600;color:var(--acc);min-width:44px}
.legenda p{padding:0 16px 12px 70px;color:var(--mute);font-size:13.5px}

/* filtros + cartões */
.seg{display:inline-flex;border:1px solid var(--line2);border-radius:999px;overflow:hidden;background:var(--surf);margin:0 0 14px}
.seg button{padding:8px 14px;border:0;background:transparent;cursor:pointer;font-weight:600;font-size:13.5px;color:var(--mute)}
.seg button.on{background:var(--acc);color:#fff}
.card{background:var(--surf);border:1px solid var(--line);border-radius:16px;margin-bottom:14px;overflow:hidden;border-left:5px solid var(--acc)}
.card.same{border-left-color:var(--line2)}
.card.cred-only{border-left-color:var(--ok)}
.ch{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:14px 16px 10px;flex-wrap:wrap}
.ch .t{font-weight:700;font-size:17px;line-height:1.25}
.ch .t small{display:block;font-weight:400;color:var(--mute);font-size:13px;margin-top:2px}
.ch .v{font-family:var(--mono);font-weight:600;font-size:18px;white-space:nowrap}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.4px;padding:3px 9px;border-radius:999px;margin-top:6px;margin-right:6px}
.tag.mov{background:var(--bad-soft);color:var(--bad)} .tag.ok{background:var(--ok-soft);color:var(--ok)} .tag.trib{background:var(--acc-soft);color:var(--acc)}
.grid{display:grid;grid-template-columns:120px 1fr 28px 1fr;border-top:1px solid var(--line)}
.grid>div{padding:9px 12px;border-bottom:1px solid var(--line);font-size:14px;display:flex;align-items:center}
.grid .h{font-size:10.5px;text-transform:uppercase;letter-spacing:.7px;color:var(--mute);font-weight:700;background:var(--surf2)}
.grid .k{font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--mute);font-weight:700}
.grid .a{color:var(--mute)} .grid .d{color:var(--mute)}
.grid .ar{justify-content:center;color:var(--acc);font-size:18px}
.grid .chg.a{background:var(--bad-soft);color:var(--bad)}
.grid .chg.d{background:var(--ok-soft);color:var(--ink);font-weight:600}
.why{padding:12px 16px 14px;font-size:14px;line-height:1.5}
.why b{color:var(--ink)}
.facts{color:var(--mute);font-size:12.5px;margin-top:6px;font-family:var(--mono)}
.facts .warn{color:var(--bad);font-weight:600}
table{border-collapse:collapse;width:100%;font-size:13.5px;background:var(--surf)}
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:14px}
th{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);text-align:left;padding:10px 12px;background:var(--surf2);border-bottom:1px solid var(--line)}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
tr.rec td{background:var(--acc-soft)}
.foot{margin-top:30px;color:var(--mute);font-size:12.5px}
@media print{
  @page{size:A4;margin:12mm 11mm 14mm}
  body{background:#fff;color:#1B1F24;font-size:11.5px}
  :root{--bg:#fff;--surf:#fff;--surf2:#F1F4F3;--ink:#1B1F24;--mute:#5F6B70;--line:#D6DDDA;--line2:#BFC9C5;--acc:#0E5C6B;--acc-soft:#DCEBEE;--bad:#A5501B;--bad-soft:#F8E7DA;--ok:#2F6B4F;--ok-soft:#DDEDE4}
  main{max-width:none;padding:0}
  .seg,.so-tela{display:none}
  .card,.box,.kpi,.legenda,.cred,.tw{break-inside:avoid;page-break-inside:avoid}
  .card{border:1px solid var(--line);border-left:5px solid var(--acc);margin-bottom:9px;box-shadow:none}
  .card.same{border-left-color:var(--line2)} .card.cred-only{border-left-color:var(--ok)}
  .card summary{cursor:default}
  .ch{padding:7px 12px 4px} .ch .t{font-size:12.5px} .ch .t small{font-size:10.5px} .ch .v{font-size:13px} .tag{margin-top:3px;font-size:9.5px;padding:2px 7px}
  .grid{grid-template-columns:118px 1fr 22px 1fr} .grid>div{padding:3.5px 8px;font-size:10px;line-height:1.3} .grid .k{font-size:9px}
  .why{padding:5px 12px 6px;font-size:10px;line-height:1.4} .facts{font-size:9px;margin-top:3px}
  .card{margin-bottom:7px}
  .kpis{grid-template-columns:repeat(5,1fr);gap:7px} .kpi{padding:9px 11px} .kpi b{font-size:20px} .kpi.money b{font-size:14.5px} .kpi span{font-size:10px;margin-top:3px}
  h1{font-size:24px} h2{font-size:17px;margin:18px 0 3px;break-after:avoid} .lead{font-size:12px} .sub{font-size:11px;margin-bottom:8px}
  .box .big{font-size:12.5px} .box .m{font-size:11px} .fl-row .box{padding:8px 10px}
  .legenda p{font-size:10.5px} .legenda summary{font-size:11.5px;padding:6px 12px}
  .grupo,h2.pb{break-before:page;page-break-before:always}
  h2.pb{margin-top:0}
  .legenda{margin-top:8px} .legenda summary{padding:4px 10px;font-size:10.5px} .legenda p{padding:0 10px 5px 58px;font-size:9.5px;line-height:1.35} .legenda summary code{min-width:38px}
  .cred{padding:7px 12px;font-size:10.5px;margin-top:8px}
  .fluxo .box{padding:9px 12px} .fl-row{margin-top:6px} .fl-row .box{padding:6px 10px} .box .m{font-size:10px;margin-top:2px} .trib{font-size:10px;padding:1px 7px} .ids{font-size:10px} .arrow{font-size:22px}
  .grupo-tit{font-family:var(--disp);font-size:15px;margin:0 0 6px}
  table{font-size:10.5px} th,td{padding:6px 9px}
  a{color:inherit;text-decoration:none}
  -webkit-print-color-adjust:exact;print-color-adjust:exact
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
}
@media (max-width:700px){
  .fluxo,.fl-row{grid-template-columns:1fr} .arrow{transform:rotate(90deg);height:34px}
  .grid{grid-template-columns:1fr} .grid .h{display:none}
  .grid .k{padding-bottom:2px;border-bottom:0;background:var(--surf2)}
  .grid .ar{display:none}
  .grid .a::before{content:"antes  ";font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);margin-right:8px;font-weight:700}
  .grid .d::before{content:"depois  ";font-size:10px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);margin-right:8px;font-weight:700}
  .grid .a{border-bottom:0}
  body{font-size:14px}
}
</style>
</head>
<body>
<main>
  <div class="eye" id="eye"></div>
  <h1>Correção das apropriações</h1>
  <p class="lead" id="lead"></p>
  <div class="kpis" id="kpis"></div>

  <h2>De onde saiu, para onde foi</h2>
  <p class="sub">A guia anexada (código de receita) diz o que cada título é. O destino é o que a equipe usa para aquele tributo.</p>
  <div class="fluxo">
    <div class="box de"><h3>Estava em</h3><div class="big">GARDEN - INCORPORAÇÃO / IMPOSTOS (RET)</div><div class="m">Planilha <b>Imposto (RET)</b> · centro de custo <b>3</b> — a conta do RET da incorporadora</div></div>
    <div class="arrow">→</div>
    <div class="box para"><h3>Foi para</h3><div class="big">GARDEN - 1ª ETAPA / OBRA GARDEN</div><div class="m">Centro de custo <b>1</b>, no plano financeiro de cada tributo — é custo da obra</div></div>
  </div>
  <div id="fluxo"></div>
  <div class="cred" id="cred"></div>
  <div class="legenda" id="legenda"></div>

  <h2 class="pb">Título a título</h2>
  <p class="sub">Cada cartão mostra o que estava e o que ficou. Só o que mudou aparece colorido. <span class="so-tela">Toque no cartão para ler o porquê.</span></p>
  <div class="seg" id="seg"></div>
  <div id="cards"></div>

  <h2>O padrão da equipe</h2>
  <p class="sub">Como a Ludmylla e a Thalita apropriam cada imposto em 2025–2026. Foi isso que serviu de régua.</p>
  <div class="tw"><table id="pad"><thead><tr><th>Tributo</th><th>Obra / unidade</th><th>Planilha de custo</th><th>CC</th><th>Plano financeiro</th></tr></thead><tbody></tbody></table></div>
  <p class="foot" id="foot"></p>
</main>
<script>
const D = __DADOS__;
const $ = s => document.querySelector(s);
const brl = v => v == null ? '—' : v.toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const dbr = iso => iso ? `${iso.slice(8,10)}/${iso.slice(5,7)}/${iso.slice(0,4)}` : '—';

$('#eye').textContent = `Grupo Garden · impostos lançados por ${D.quem} · ${D.de.slice(5,7)}/${D.de.slice(0,4)} a ${D.ate.slice(5,7)}/${D.ate.slice(0,4)}`;
$('#lead').innerHTML = `Dos <b>${D.n} títulos de imposto</b> no seu nome no período (${brl(D.tot)}), <b>${D.n_mov} tinham sido lançados na conta de RET</b> mas a guia mostra que são retenções da obra. Todos foram movidos para <b>GARDEN - 1ª ETAPA / OBRA GARDEN / centro de custo 1</b>. ${D.n_cred} ${D.n_cred===1?'título teve':'títulos tiveram'} o credor corrigido. Hoje <b>nenhum</b> está fora do padrão da equipe.`;
$('#kpis').innerHTML = [
  [D.n,'títulos de imposto no período',''],[D.n_mov,'movidos da conta de RET para a obra',''],[brl(D.tot_mov),'valor movido','money'],
  [D.n_cred,'credor corrigido (Residencial Garden → Receita Federal)',''],['0','ainda fora do padrão','ok']
].map(([n,l,c]) => `<div class="kpi ${c}"><b>${n}</b><span>${l}</span></div>`).join('');

$('#fluxo').innerHTML = D.fluxo.map(f => `
  <div class="fl-row">
    <div class="box de"><span class="trib">${f.trib}</span><span class="ids">cód. ${f.cod} · ${f.n} título${f.n>1?'s':''}: ${f.ids.join(', ')}</span><div class="m">Imposto (RET) · CC 3 · ${esc(f.plano_antes.join(' / '))}</div></div>
    <div class="arrow">→</div>
    <div class="box para"><span class="val">${brl(f.v)}</span><div class="m"><b>${esc(f.planilhas.join(' · '))}</b><br>CC 1 · ${esc(f.planos.join(' / '))}</div></div>
  </div>`).join('');
$('#cred').innerHTML = D.cred_ids.length ? `<b>Credor:</b> os títulos <b>${D.cred_ids.join(', ')}</b> estavam com credor <b>Residencial Garden Empreendimentos</b> (a própria empresa) e passaram para <b>Secretaria da Receita Federal do Brasil</b>.` : '';
$('#legenda').innerHTML = `<details open><summary><code>?</code> O que é cada código de receita</summary></details>` + D.explica.map(e => `<details><summary><code>${e.cod}</code> ${e.nome}</summary><p>${esc(e.txt)}</p></details>`).join('');

let F = 'alt';
const segs = [['alt',`Alterados (${D.n_alt})`],['same',`Não mudaram (${D.n-D.n_alt})`],['all',`Todos (${D.n})`]];
function renderSeg(){ $('#seg').innerHTML = segs.map(([k,l]) => `<button class="${F===k?'on':''}" data-k="${k}">${l}</button>`).join(''); }
$('#seg').addEventListener('click', e => { const b = e.target.closest('[data-k]'); if(!b) return; F = b.dataset.k; renderSeg(); renderCards(); });

function card(it){
  const mudou = it.difs.length > 0;
  const cls = !mudou ? 'same' : (it.so_credor ? 'cred-only' : '');
  const tag = !mudou ? '<span class="tag ok">já estava certo</span>' : (it.so_credor ? '<span class="tag ok">só o credor</span>' : '<span class="tag mov">movido da conta de RET</span>');
  const rows = it.campos.map(c => `
    <div class="k">${c.nome}</div>
    <div class="a ${c.mudou?'chg':''}">${esc(c.antes)}</div>
    <div class="ar">${c.mudou?'→':''}</div>
    <div class="d ${c.mudou?'chg':''}">${esc(c.depois)}</div>`).join('');
  const facts = [];
  if (it.guia != null) facts.push(it.dif_guia && Math.abs(it.dif_guia) > 0.05 ? `<span class="warn">guia ${brl(it.guia)} ≠ título ${brl(it.valor)} (dif. ${brl(it.dif_guia)})</span>` : `guia ${brl(it.guia)} = título`);
  if (it.competencia) facts.push('competência ' + esc(it.competencia));
  if (it.venc) facts.push(`vencimento ${dbr(it.venc)} · ${(it.sit||'').toLowerCase()}`);
  if (it.alterado && mudou) facts.push(`alterado em ${dbr(it.alterado.slice(0,10))} ${it.alterado.slice(11,16)}`);
  return `<details class="card ${cls}" ${mudou?'':''}>
    <summary style="list-style:none;cursor:pointer">
      <div class="ch">
        <div class="t">Título ${it.id} · ${esc(it.tipo)} ${esc(it.doc)}<small>emitido ${dbr(it.data)} · ${esc(it.nome_trib)}${it.cods?' · guia cód. '+it.cods:''}</small>
          <div><span class="tag trib">${it.trib}</span>${tag}</div></div>
        <div class="v">${brl(it.valor)}</div>
      </div>
      <div class="grid"><div class="h"></div><div class="h">Antes</div><div class="h"></div><div class="h">Depois</div>${rows}</div>
    </summary>
    <div class="why"><b>Por quê:</b> ${esc(it.porque)}<div class="facts">${facts.join(' · ')}</div></div>
  </details>`;
}
const PRINT = location.hash === '#print';
function renderCards(){
  if (PRINT){
    const alt = D.itens.filter(it => it.difs.length), same = D.itens.filter(it => !it.difs.length);
    $('#cards').innerHTML = `<div class="grupo-tit">Alterados (${alt.length})</div>` + alt.map(card).join('')
      + `<div class="grupo"><div class="grupo-tit">Não mudaram (${same.length}) — já estavam no padrão</div>` + same.map(card).join('') + '</div>';
    document.querySelectorAll('details').forEach(d => d.open = true);
    return;
  }
  const L = D.itens.filter(it => F==='all' || (F==='alt' ? it.difs.length : !it.difs.length));
  $('#cards').innerHTML = L.map(card).join('');
  // abre o primeiro cartão para mostrar que expande
  const first = $('#cards details'); if (first) first.open = true;
}
renderSeg(); renderCards();
if (PRINT) document.documentElement.setAttribute('data-theme','light');
$('#pad tbody').innerHTML = D.padrao.map(p => `<tr class="${p.trib==='RET'?'rec':''}"><td><b>${p.trib}</b></td><td>${esc(p.obra)}</td><td>${esc(p.planilha)}</td><td>${p.cc}</td><td>${esc(p.plano)}</td></tr>`).join('');
$('#foot').textContent = `Antes = leitura da API em 19/08/2026 de manhã · depois = leitura atual (${D.gerado}). Tributo definido pelo código de receita impresso na guia anexada. Só o RET (cód. 4095) fica na conta de RET; INSS, CSRF, IRRF e ISS são retenções de fornecedores da obra e vão para a 1ª Etapa.`;
</script>
</body>
</html>
'''
saida = f"output/Relatorio_Correcao_Impostos_{QUEM.title()}_2026.html"
open(saida, 'w', encoding='utf-8').write(HTML.replace('__DADOS__', DADOS))
print(f"gerado {saida} | {len(itens)} títulos, {len(alterados)} alterados")

# ---------- PDF "visual": imprime o proprio HTML com o Chrome headless ----------
import os, subprocess, sys, time
CHROME = next((c for c in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                           r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"] if os.path.exists(c)), None)
if CHROME and '--sem-pdf' not in sys.argv:
    pdf = os.path.abspath(saida[:-5] + ".pdf")
    url = "file:///" + os.path.abspath(saida).replace("\\", "/") + "#print"
    cmd = [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=8000",
           "--run-all-compositor-stages-before-draw", f"--print-to-pdf={pdf}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(("gerado " + pdf) if os.path.exists(pdf) else f"PDF nao gerado: {r.stderr[-300:]}")
else:
    print("Chrome/Edge nao encontrado: PDF visual nao gerado (o HTML fica igual)")
