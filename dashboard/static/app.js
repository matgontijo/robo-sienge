"use strict";
/* ============================================================
   Robô de Conferência — experiência v3 (uma tela, três estados)
   enviar relatório → conferindo (ao vivo) → mesa de decisão
   ============================================================ */

/* ---------- vocabulário ---------- */
const NOMES = {
    "PAGAMENTO_DESTINO_DIVERGENTE": "Dinheiro vai para outro CNPJ (não é o credor)",
    "CNPJ_DIVERGENTE": "Nota emitida por CNPJ diferente do credor",
    "VALOR_DIVERGENTE": "Valor do título ≠ valor da nota",
    "LIQUIDO_BRUTO_DIVERGENTE": "Líquido não bate com bruto − retenções",
    "LIQUIDO_PARCELA_DIVERGENTE": "Parcela − retenções não bate com o valor a pagar",
    "BOLETO_VALOR_DIVERGENTE": "Boleto cobra valor diferente do título",
    "BOLETO_BANCO_INCOMPATIVEL": "Banco do boleto ≠ forma de pagamento",
    "BOLETO_VENCIMENTO_DIVERGENTE": "Boleto vence em data diferente do título",
    "TRANSFERENCIA_BANCO_INCOMPATIVEL": "TED × depósito: forma errada p/ banco destino",
    "IMPOSTO_DIVERGENTE": "Imposto retido ≠ destacado na nota",
    "IMPOSTO_NF_DIVERGENTE": "Retenção do título ≠ destacada na nota",
    "IMPOSTO_NAO_RETIDO": "Nota destaca retenção não lançada no título",
    "RETENCAO_ALIQUOTA_SUSPEITA": "Alíquota de retenção acima do usual",
    "RETENCAO_INDEVIDA_SIMPLES": "Retenção federal de fornecedor do Simples",
    "CHAVE_NFE_INVALIDA": "Chave de NF-e inválida",
    "BOLETO_NAO_ENCONTRADO": "Boleto não encontrado no DDA",
    "SEM_ANEXO": "Título sem nenhum documento anexado",
    "ANEXO_ILEGIVEL": "Documento não lido (OCR pendente)",
    "PIX_NAO_VERIFICAVEL": "Chave Pix não permite confirmar o titular",
    "PAGAMENTO_FORMA_INCOMPATIVEL": "Forma de pagamento incompatível",
    "FORMA_PAGAMENTO_AUSENTE": "Sem forma de pagamento cadastrada",
    "NF_SEM_CNPJ_DO_CREDOR": "CNPJ do credor não aparece na NF anexada",
    "VENCIMENTO_DIVERGENTE": "Vencimento divergente",
};
const DICAS = {
    "PAGAMENTO_DESTINO_DIVERGENTE": "Exigir cessão de crédito ou autorização do credor",
    "BOLETO_VALOR_DIVERGENTE": "Pedir boleto correto ou ajustar o título",
    "BOLETO_VENCIMENTO_DIVERGENTE": "Pagar após o vencimento do boleto gera juros",
    "LIQUIDO_PARCELA_DIVERGENTE": "Revisar as retenções lançadas",
    "TRANSFERENCIA_BANCO_INCOMPATIVEL": "Corrigir a forma no Sienge — a remessa seria recusada",
    "BOLETO_BANCO_INCOMPATIVEL": "Corrigir a forma no Sienge — a remessa seria recusada",
    "FORMA_PAGAMENTO_AUSENTE": "Sem forma cadastrada o título não entra na remessa",
    "SEM_ANEXO": "Cobrar o documento de quem lançou",
    "PIX_NAO_VERIFICAVEL": "Confirmar a chave com o fornecedor",
    "IMPOSTO_NAO_RETIDO": "Risco de pagar a mais e ficar com o passivo fiscal",
    "NF_SEM_CNPJ_DO_CREDOR": "Abrir o anexo e conferir se a nota é do fornecedor",
    "RETENCAO_INDEVIDA_SIMPLES": "Optante do Simples não sofre retenção federal",
};

/* ---------- estado ---------- */
let EXECS = [], EXEC = null, DIVS = [], PAGS = [], TITULOS = [];
let FILTRO = "PENDENTES", BUSCA = "";
const EXPANDIDOS = new Set();
let es = null;            // EventSource do ciclo rodando
let runTotal = 0, runFeitos = 0, runInicio = 0;

/* ---------- utilitários ---------- */
const $ = id => document.getElementById(id);
const H = () => ({ "Content-Type": "application/json" });
const dinheiro = v => v == null ? "–" : Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const dig = v => String(v || "").replace(/\D/g, "");
const numDe = s => { const m = String(s ?? "").match(/-?\d+(?:\.\d+)?/); return m ? parseFloat(m[0]) : null; };
const dataDe = s => { const m = String(s ?? "").match(/\d{4}-\d{2}-\d{2}/); return m ? m[0] : null; };
const dataBR = s => { const m = String(s ?? "").match(/(\d{4})-(\d{2})-(\d{2})/); return m ? `${m[3]}/${m[2]}/${m[1]}` : (s || ""); };
const cnpjDe = s => { const d = dig(s); return d.length === 14 ? d : null; };
const fmtCNPJ = d => d ? `${d.slice(0,2)}.${d.slice(2,5)}.${d.slice(5,8)}/${d.slice(8,12)}-${d.slice(12)}` : "";
function toast(m) { const t = $("toast"); t.textContent = m; t.classList.add("show");
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2200); }

/* ---------- estados do palco ---------- */
function mostrar(stage) {
    ["stage-vazio", "stage-rodando", "stage-revisao"].forEach(s => { $(s).hidden = s !== stage; });
}

/* ============================================================
   BOOT
   ============================================================ */
async function boot() {
    await recarregarExecs();
    const rodando = EXECS.find(e => e.status === "RODANDO");
    const done = EXECS.filter(e => e.status === "CONCLUIDO");
    if (rodando) {
        acompanharCiclo(rodando);
    } else if (done.length) {
        await carregarCiclo(done[0].id);
    } else {
        mostrar("stage-vazio");
    }
}

async function recarregarExecs() {
    const r = await fetch("/api/execucoes?limit=15", { headers: H() });
    EXECS = r.ok ? await r.json() : [];
    const sel = $("sel-exec");
    sel.innerHTML = "";
    EXECS.filter(e => e.status === "CONCLUIDO" || e.status === "RODANDO").forEach(e => {
        const o = document.createElement("option");
        o.value = e.id;
        o.textContent = `Ciclo #${e.id} · ${(e.iniciado_em || "").replace("T", " ").slice(0, 16)}`;
        sel.appendChild(o);
    });
}

$("sel-exec").onchange = e => {
    const ex = EXECS.find(x => String(x.id) === e.target.value);
    if (!ex) return;
    if (ex.status === "RODANDO") acompanharCiclo(ex);
    else carregarCiclo(ex.id);
};

function chipEstado(ex) {
    const c = $("chip-estado");
    if (!ex) { c.textContent = ""; c.className = "chip-estado"; return; }
    c.className = "chip-estado " + ex.status;
    c.textContent = ex.status === "RODANDO" ? "conferindo…"
        : ex.status === "CONCLUIDO" ? `${ex.total_titulos} títulos` : ex.status.toLowerCase();
}

/* ============================================================
   ESTADO: RODANDO — progresso ao vivo
   ============================================================ */
function acompanharCiclo(ex) {
    EXEC = ex;
    $("sel-exec").value = String(ex.id);
    chipEstado(ex);
    mostrar("stage-rodando");
    runTotal = 0; runFeitos = 0; runInicio = Date.now();
    $("run-titulo").textContent = "Conferindo os títulos…";
    $("run-fill").style.width = "4%";
    $("run-pct").textContent = "preparando…";
    $("run-eta").textContent = "";
    $("run-ticker").innerHTML = "";

    if (es) { es.close(); es = null; }
    es = new EventSource(`/api/stream/${ex.id}`);
    es.onmessage = ev => {
        const msg = ev.data || "";
        // total de títulos ("Títulos conferíveis no relatório: 124" / "530 encontrados")
        let m = msg.match(/confer[íi]veis[^:]*:\s*(\d+)/i) || msg.match(/(\d+)\s+t[íi]tulos? para conferir/i);
        if (m) runTotal = parseInt(m[1]);
        if (/Processando/i.test(msg)) {
            runFeitos++;
            const mt = msg.match(/Processando\s+(?:t[íi]tulo\s+)?([\w\/.-]+)/i);
            $("run-titulo").textContent = mt ? `Conferindo o título ${mt[1]}…` : "Conferindo os títulos…";
            atualizarRun();
        }
        const tk = $("run-ticker");
        const d = document.createElement("div");
        d.textContent = msg.replace(/^\[.*?\]\s*/, "").slice(0, 110);
        tk.appendChild(d);
        while (tk.children.length > 7) tk.removeChild(tk.firstChild);
    };
    es.addEventListener("close", async () => {
        es.close(); es = null;
        toast("Ciclo concluído ✓");
        await recarregarExecs();
        const done = EXECS.filter(e => e.status === "CONCLUIDO");
        if (done.length) await carregarCiclo(done[0].id);
        else mostrar("stage-vazio");
    });
    es.onerror = () => { /* reconexão automática do EventSource */ };
}

function atualizarRun() {
    if (!runTotal) { $("run-pct").textContent = runFeitos + " conferidos"; return; }
    const pct = Math.min(99, Math.round(100 * runFeitos / runTotal));
    $("run-fill").style.width = Math.max(4, pct) + "%";
    $("run-pct").textContent = `${runFeitos} de ${runTotal} títulos · ${pct}%`;
    if (runFeitos >= 3) {
        const porTitulo = (Date.now() - runInicio) / runFeitos;
        const resta = Math.round((runTotal - runFeitos) * porTitulo / 60000);
        $("run-eta").textContent = resta > 0 ? `~${resta} min restantes` : "quase lá…";
    }
}

$("bt-abortar").onclick = async () => {
    if (!EXEC || !confirm("Parar o ciclo agora? A conferência é interrompida imediatamente.")) return;
    const r = await fetch(`/api/execucoes/${EXEC.id}/abortar`, { method: "POST", headers: H() });
    if (!r.ok) { toast("Não foi possível parar."); return; }
    if (es) { es.close(); es = null; }
    toast("Ciclo interrompido ✋");
    await recarregarExecs();
    const done = EXECS.filter(e => e.status === "CONCLUIDO");
    if (done.length) await carregarCiclo(done[0].id);
    else mostrar("stage-vazio");
};

/* ============================================================
   ESTADO: MESA DE DECISÃO
   ============================================================ */
async function carregarCiclo(id) {
    const ex = EXECS.find(e => String(e.id) === String(id));
    EXEC = ex || { id };
    $("sel-exec").value = String(id);
    chipEstado(EXEC);
    const [d, p] = await Promise.all([
        fetch(`/api/execucoes/${id}/divergencias`, { headers: H() }).then(r => r.json()),
        fetch(`/api/execucoes/${id}/pagamentos`, { headers: H() }).then(r => r.ok ? r.json() : []),
    ]);
    DIVS = d || []; PAGS = p || [];
    DIVS.forEach(x => { if (!x.status_revisao) x.status_revisao = "PENDENTE"; });
    montar();
    mostrar("stage-revisao");
    render();
}

function montar() {
    const map = {};
    DIVS.forEach(d => {
        const k = String(d.titulo_numero || "?");
        (map[k] = map[k] || { numero: k, divs: [] }).divs.push(d);
    });
    PAGS.forEach(p => {
        const k = String(p.titulo_numero || "?");
        if (!map[k]) map[k] = { numero: k, divs: [] };
    });
    TITULOS = Object.values(map).map(t => {
        t.pag = PAGS.find(p => String(p.titulo_numero) === t.numero) || null;
        const d0 = t.divs[0] || {};
        t.fornecedor = d0.fornecedor_nome || t.pag?.fornecedor || "";
        t.cnpj = d0.fornecedor_cnpj || t.pag?.cnpj_credor || "";
        t.valor = d0.valor_sienge ?? t.pag?.valor;
        t.venc = d0.data_vencimento || t.pag?.vencimento || "";
        recontar(t);
        return t;
    });
    TITULOS.sort((a, b) => (b.critPend - a.critPend) || (b.pendentes - a.pendentes) || ((b.valor || 0) - (a.valor || 0)));
}
function recontar(t) {
    const pend = t.divs.filter(d => d.status_revisao === "PENDENTE");
    t.pendentes = pend.length;
    t.critPend = pend.filter(d => d.criticidade === "CRITICA").length;
    t.aprovados = t.divs.filter(d => d.status_revisao === "APROVADO").length;
    t.rejeitados = t.divs.filter(d => d.status_revisao === "REJEITADO").length;
}
function problemasUnicos(t) {
    t.divs.forEach(d => { d._irmaos = []; });
    const vistos = new Map(), unicos = [];
    t.divs.forEach(d => {
        const k = [d.tipo, d.valor_sienge_campo, d.valor_boleto_campo || d.valor_nfe_campo].join("|");
        if (vistos.has(k)) vistos.get(k)._irmaos.push(d);
        else { vistos.set(k, d); unicos.push(d); }
    });
    unicos.sort((a, b) => (a.criticidade === "CRITICA" ? 0 : 1) - (b.criticidade === "CRITICA" ? 0 : 1));
    return unicos;
}

/* colunas: No Sienge / No documento / Diferença */
function colunas(d) {
    const S = d.valor_sienge_campo;
    const V = d.valor_boleto_campo && d.valor_boleto_campo !== "-" ? d.valor_boleto_campo
        : (d.valor_nfe_campo && d.valor_nfe_campo !== "-" ? d.valor_nfe_campo : null);
    const nS = numDe(S), nV = numDe(V);
    const dS = dataDe(S), dV = dataDe(V);
    const cS = cnpjDe(S), cV = cnpjDe(V);
    if (dS && dV) {
        const dias = Math.round((new Date(dV) - new Date(dS)) / 86400000);
        return { s: dataBR(dS), v: dataBR(dV), diverge: dS !== dV,
                 delta: dias ? Math.abs(dias) + " dia" + (Math.abs(dias) > 1 ? "s" : "") : null };
    }
    if (cS && cV) {
        return { s: `<span class="cnpj">${fmtCNPJ(cS)}</span>`, v: `<span class="cnpj">${fmtCNPJ(cV)}</span>`,
                 diverge: cS !== cV, delta: cS !== cV ? "outro CNPJ" : null };
    }
    if (nS != null && nV != null && /\d\.\d{2}\b/.test(String(S)) && /\d\.\d{2}\b/.test(String(V))) {
        return { s: dinheiro(nS), v: dinheiro(nV), diverge: Math.abs(nS - nV) > 0.05,
                 delta: Math.abs(nS - nV) > 0.05 ? dinheiro(Math.abs(nS - nV)) : null };
    }
    const fmt1 = x => { const n = numDe(x); return (n != null && /^\d+\.\d{2}$/.test(String(x).trim())) ? dinheiro(n) : (x || null); };
    return { s: fmt1(S), v: fmt1(V), diverge: false, delta: null };
}

/* ---------- filtros ---------- */
const FILTROS = [
    ["PENDENTES", "Pendentes"], ["CRITICAS", "Críticas"], ["ATENCAO", "Atenção"],
    ["OK", "Liberados"], ["DECIDIDOS", "Decididos"], ["TODOS", "Todos"],
];
function tituloNoFiltro(t) {
    switch (FILTRO) {
        case "PENDENTES": return t.pendentes > 0;
        case "CRITICAS": return t.critPend > 0;
        case "ATENCAO": return (t.pendentes - t.critPend) > 0;
        case "OK": return t.divs.length === 0;
        case "DECIDIDOS": return t.divs.length > 0 && t.pendentes === 0;
        default: return true;
    }
}
function divsDoTitulo(t) {
    const probs = problemasUnicos(t);
    switch (FILTRO) {
        case "PENDENTES": return probs.filter(d => d.status_revisao === "PENDENTE");
        case "CRITICAS": return probs.filter(d => d.status_revisao === "PENDENTE" && d.criticidade === "CRITICA");
        case "ATENCAO": return probs.filter(d => d.status_revisao === "PENDENTE" && d.criticidade === "ATENCAO");
        default: return probs;
    }
}
function contar(f) {
    const antigo = FILTRO; FILTRO = f;
    const n = TITULOS.filter(tituloNoFiltro).length;
    FILTRO = antigo;
    return n;
}
function renderFiltros() {
    const c = $("filtros"); c.innerHTML = "";
    FILTROS.forEach(([f, label]) => {
        const b = document.createElement("button");
        b.className = (FILTRO === f ? "on " : "") + (f === "CRITICAS" ? "fc" : f === "ATENCAO" ? "fw" : "");
        b.innerHTML = `${label} <b>${contar(f)}</b>`;
        b.onclick = () => { FILTRO = f; render(); };
        c.appendChild(b);
    });
}

/* ---------- render da mesa ---------- */
function render() {
    renderFiltros();
    atualizarVeredito();
    const corpo = $("corpo"); corpo.innerHTML = "";
    const vazio = $("estado-vazio");
    const busca = BUSCA.toLowerCase();
    let visiveis = 0;

    TITULOS.forEach(t => {
        if (busca) {
            const alvo = (t.numero + " " + t.fornecedor + " " + (t.cnpj || "") + " " +
                t.divs.map(d => NOMES[d.tipo] || d.tipo).join(" ")).toLowerCase();
            if (!alvo.includes(busca)) return;
        } else if (!tituloNoFiltro(t)) return;

        const probs = busca ? problemasUnicos(t) : divsDoTitulo(t);
        visiveis++;

        const tr = document.createElement("tr");
        tr.className = "grupo";
        const direita = t.divs.length === 0
            ? `<span class="g-ok">✓ liberado</span>`
            : t.pendentes
            ? `<button class="g-aprovar">✓ Aprovar título (${t.pendentes})</button>`
            : (t.rejeitados ? `<span class="g-ok" style="color:var(--muted)">✗ ${t.rejeitados} rejeitado(s)</span>`
                            : `<span class="g-ok">✓ revisado</span>`);
        tr.innerHTML = `
            <td colspan="2">
                <div class="g-forn">${t.fornecedor || "Fornecedor"} <span style="font-weight:600;color:var(--muted)">· ${t.numero}${t.pag?.parcela ? "/" + t.pag.parcela : ""}</span></div>
                <div class="g-meta">${t.cnpj || ""}${t.pag?.regime ? " · " + t.pag.regime : ""} · <span class="g-pagto">💳 dados do pagamento ${EXPANDIDOS.has(t.numero) ? "▴" : "▾"}</span></div>
            </td>
            <td colspan="3">
                <div class="g-fatos">
                    <div class="g-fato"><div class="k">Valor a pagar</div><div class="v">${dinheiro(t.valor)}</div></div>
                    <div class="g-fato"><div class="k">Vencimento</div><div class="v">${dataBR(t.venc)}</div></div>
                    ${t.pag?.forma ? `<div class="g-fato"><div class="k">Forma</div><div class="v" style="font-size:12.5px">${t.pag.forma}</div></div>` : ""}
                    ${direita}
                </div>
            </td>`;
        const btAp = tr.querySelector(".g-aprovar");
        if (btAp) btAp.onclick = () => decidirTitulo(t, "APROVADO");
        tr.querySelector(".g-pagto").onclick = () => {
            EXPANDIDOS.has(t.numero) ? EXPANDIDOS.delete(t.numero) : EXPANDIDOS.add(t.numero);
            render();
        };
        corpo.appendChild(tr);

        if (EXPANDIDOS.has(t.numero) && t.pag) corpo.appendChild(pagtoRow(t));
        probs.forEach(d => corpo.appendChild(aptoRow(d, t)));
    });

    if (!visiveis) {
        const pend = DIVS.filter(d => d.status_revisao === "PENDENTE").length;
        const filtroPend = ["PENDENTES", "CRITICAS", "ATENCAO"].includes(FILTRO);
        vazio.hidden = false;
        if (!busca && filtroPend && pend === 0 && DIVS.length) {
            const apr = DIVS.filter(d => d.status_revisao === "APROVADO").length;
            const rej = DIVS.filter(d => d.status_revisao === "REJEITADO").length;
            vazio.innerHTML = `<div class="emoji">🎉</div><h2>Tudo revisado!</h2>
                <p>${apr} aprovado(s) · ${rej} rejeitado(s). A remessa está liberada.</p>
                <div class="acoes">${rej ? '<button class="primario" onclick="exportarRejeitados()">⬇ Baixar rejeitados</button>' : ""}
                <button onclick="abrirPasta()">📁 Abrir pasta dos anexos</button></div>`;
        } else if (busca) {
            vazio.innerHTML = `<div class="emoji">🔎</div><h2>Nenhum título encontrado</h2><p>Nada bate com “${BUSCA}”.</p>`;
        } else {
            vazio.innerHTML = `<div class="emoji">🔎</div><h2>Nada aqui</h2><p>Nenhum título com esse filtro.</p>`;
        }
    } else vazio.hidden = true;
}

function aptoRow(d, t) {
    const st = d.status_revisao;
    const tr = document.createElement("tr");
    tr.className = "apto" + (st !== "PENDENTE" ? " decidido" : "");
    const c = colunas(d);
    const nx = d._irmaos?.length ? `<span class="nx" title="confirmado por ${d._irmaos.length + 1} verificações">${d._irmaos.length + 1}×</span>` : "";
    const dica = DICAS[d.tipo] ? `<div class="p-sub">→ ${DICAS[d.tipo]}</div>` : "";
    const acoes = st === "PENDENTE"
        ? `<button class="sim" title="Aprovar">✓</button><button class="nao" title="Rejeitar">✗</button>`
        : `<span class="st ${st}">${st === "APROVADO" ? "✓ aprovado" : "✗ rejeitado"}</span><button class="re" title="Reabrir">↩</button>`;
    tr.innerHTML = `
        <td><div class="p-cell"><span class="dot ${d.criticidade}"></span>
            <div><div class="p-label">${NOMES[d.tipo] || d.tipo}${nx}</div>${dica}</div></div></td>
        <td><span class="vcell">${c.s || '<span class="vazio">—</span>'}</span></td>
        <td><span class="vcell ${c.diverge ? "doc-div" : ""}">${c.v || '<span class="vazio">—</span>'}</span></td>
        <td>${c.delta ? `<span class="delta">${c.delta}</span>` : '<span class="vazio">—</span>'}</td>
        <td><div class="d-cell">${acoes}</div></td>`;
    const sim = tr.querySelector(".sim"), nao = tr.querySelector(".nao"), re = tr.querySelector(".re");
    if (sim) sim.onclick = () => decidir(d, t, "APROVADO");
    if (nao) nao.onclick = () => decidir(d, t, "REJEITADO");
    if (re) re.onclick = () => decidir(d, t, "PENDENTE");
    return tr;
}

function pagtoRow(t) {
    const p = t.pag;
    const tr = document.createElement("tr");
    tr.className = "pagto-row";
    const c1 = dig(p.cnpj_credor), c2 = dig(p.cnpj_destino);
    const itens = [];
    const item = (k, v, mono) => v && itens.push(`<div><div class="k">${k}</div><div class="v ${mono ? "mono" : ""}">${v}</div></div>`);
    item("Forma", p.forma);
    item("Valor da parcela (bruto)", p.valor != null ? dinheiro(p.valor) : null);
    item("Chave Pix" + (p.tipo_chave_pix ? " (" + p.tipo_chave_pix + ")" : ""), p.chave_pix);
    item("Banco · Ag · Conta", [p.banco, p.agencia, p.conta].filter(Boolean).join(" · "));
    item("Titular", p.titular);
    item("CNPJ destino", p.cnpj_destino ? p.cnpj_destino + (c1 && c2 ? (c1 === c2
        ? ' <span class="ok">✓ é o credor</span>' : ' <span class="bad">✗ não é o credor</span>') : "") : null);
    item("Linha digitável", p.linha_digitavel, true);
    item("Banco do boleto", p.banco_boleto);
    item("Valor do boleto", p.valor_boleto != null ? dinheiro(p.valor_boleto) : null);
    if (p.retencoes) { try {
        const r = JSON.parse(p.retencoes);
        item("Retenções", Object.entries(r).map(([k, v]) => `${k} ${dinheiro(v)}`).join(" · "));
    } catch (e) {} }
    if (p.liquido_calc != null) {
        const bate = Math.abs(p.liquido_calc - (t.valor || 0)) <= 0.05;
        item("Líquido calculado", dinheiro(p.liquido_calc) + (bate ? ' <span class="ok">✓ bate com o fluxo</span>' : ` <span class="bad">✗ fluxo ${dinheiro(t.valor)}</span>`));
    }
    item("Regime", p.regime);
    tr.innerHTML = `<td colspan="5"><div class="pagto-grid">${itens.join("")}</div></td>`;
    return tr;
}

/* ---------- veredito / resumo ---------- */
function atualizarVeredito() {
    const total = DIVS.length;
    const feitos = DIVS.filter(d => d.status_revisao !== "PENDENTE").length;
    const crit = DIVS.filter(d => d.status_revisao === "PENDENTE" && d.criticidade === "CRITICA").length;
    const warn = DIVS.filter(d => d.status_revisao === "PENDENTE" && d.criticidade === "ATENCAO").length;
    const liberados = TITULOS.filter(t => t.divs.length === 0).length;
    const rej = DIVS.filter(d => d.status_revisao === "REJEITADO").length;
    const pct = total ? Math.round(100 * feitos / total) : 100;

    $("res-crit").textContent = crit;
    $("res-warn").textContent = warn;
    $("res-ok").textContent = liberados;
    $("pr-pct").textContent = pct + "%";
    $("pr-f").style.strokeDashoffset = String(119.4 * (1 - pct / 100));
    $("bt-rejeitados").hidden = rej === 0;

    const v = $("veredito"), st = $("v-status"), sub = $("v-sub");
    if (crit > 0) {
        v.className = "veredito crit";
        st.textContent = "Remessa bloqueada";
        sub.textContent = `${crit} problema(s) grave(s) esperam sua decisão antes do pagamento.`;
    } else if (warn > 0) {
        v.className = "veredito";
        st.textContent = "Quase lá";
        sub.textContent = `Sem críticas — restam ${warn} ponto(s) de atenção para revisar.`;
    } else {
        v.className = "veredito ok";
        st.textContent = "Remessa liberada ✓";
        sub.textContent = "Todos os apontamentos revisados. Pode montar a remessa no Sienge.";
    }
}

/* ---------- decisões ---------- */
// Erro de rede (painel desligado) é diferente de erro do servidor — a mensagem
// precisa dizer a verdade para você saber o que fazer.
class PainelOffline extends Error {}

async function post(d, status) {
    const alvos = [d, ...(d._irmaos || [])];
    for (const a of alvos) {
        let r;
        try {
            r = await fetch("/api/divergencias/" + a.id + "/revisao", {
                method: "POST", headers: H(),
                body: JSON.stringify({ status, observacao: a.observacao_revisao || null }),
            });
        } catch (e) {
            throw new PainelOffline();   // servidor fora do ar / sem conexão
        }
        if (!r.ok) throw new Error("HTTP " + r.status);
        Object.assign(a, await r.json());
    }
}
function avisarErro(e) {
    if (e instanceof PainelOffline) {
        toast("⚠ Painel desconectado — reabra o atalho 'Painel Robo Sienge'. Nada do que você já decidiu foi perdido.");
        mostrarOffline(true);
    } else {
        toast("Não foi possível salvar (" + (e.message || "erro") + ").");
    }
}
async function decidir(d, t, status) {
    try {
        await post(d, status);
        mostrarOffline(false);
        recontar(t);
        if (status !== "PENDENTE") toast(status === "APROVADO" ? "Aprovado ✓" : "Rejeitado ✗");
        render();
    } catch (e) { avisarErro(e); }
}
async function decidirTitulo(t, status) {
    const agrupados = new Set();
    t.divs.forEach(d => (d._irmaos || []).forEach(x => agrupados.add(x)));
    const pend = t.divs.filter(d => d.status_revisao === "PENDENTE" && !agrupados.has(d));
    try {
        for (const d of pend) await post(d, status);
        mostrarOffline(false);
        recontar(t);
        toast((status === "APROVADO" ? "✔ Título " : "✖ Título ") + t.numero + " concluído");
        render();
    } catch (e) { avisarErro(e); }
}

// Faixa fixa de "painel desligado" — some sozinha quando o servidor volta
function mostrarOffline(on) {
    let el = document.getElementById("faixa-offline");
    if (on) {
        if (!el) {
            el = document.createElement("div");
            el.id = "faixa-offline";
            el.className = "faixa-offline";
            el.innerHTML = `⚠ <b>Painel desligado</b> — suas decisões não estão sendo salvas.
                Reabra o atalho <b>Painel Robo Sienge</b> na Área de Trabalho.
                <span class="fo-x">tentando reconectar…</span>`;
            document.body.appendChild(el);
            clearInterval(mostrarOffline._t);
            mostrarOffline._t = setInterval(async () => {
                try {
                    const r = await fetch("/api/stats", { cache: "no-store" });
                    if (r.ok) { mostrarOffline(false); toast("Painel reconectado ✓"); }
                } catch (e) { /* segue offline */ }
            }, 4000);
        }
    } else if (el) {
        clearInterval(mostrarOffline._t);
        el.remove();
    }
}

/* ---------- ações do veredito ---------- */
$("bt-excel").onclick = () => { if (EXEC) window.open(`/api/execucoes/${EXEC.id}/relatorio`, "_blank"); };
$("bt-rejeitados").onclick = exportarRejeitados;
async function exportarRejeitados() {
    if (!EXEC) return;
    const n = DIVS.filter(d => d.status_revisao === "REJEITADO").length;
    if (!n) return toast("Nada rejeitado neste ciclo ainda.");
    const r = await fetch(`/api/execucoes/${EXEC.id}/revisao/relatorio?status=REJEITADO`, { headers: H() });
    if (!r.ok) return toast("Erro ao gerar o relatório.");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(await r.blob());
    a.download = "rejeitados_ciclo" + EXEC.id + ".xlsx";
    a.click();
    toast(n + " rejeitado(s) exportados ⬇");
}
$("bt-pasta").onclick = abrirPasta;
async function abrirPasta() {
    if (!EXEC) return;
    try {
        const r = await fetch(`/api/execucoes/${EXEC.id}/abrir-pasta`, { headers: H() });
        const d = await r.json();
        if (d.aberto) return;
        if (!d.existe) { toast("A pasta deste ciclo ainda não existe."); return; }
        prompt("Abra esta pasta no computador do robô:", d.pasta);
    } catch { toast("Não foi possível abrir a pasta."); }
}

$("busca").addEventListener("input", e => {
    BUSCA = e.target.value;
    if (!$("stage-revisao").hidden) render();
});

/* ============================================================
   UPLOAD — botão + arrastar e soltar em qualquer lugar
   ============================================================ */
$("bt-enviar").onclick = () => $("file-relatorio").click();
$("dropzone").onclick = () => $("file-relatorio").click();
$("file-relatorio").onchange = e => { const f = e.target.files[0]; if (f) enviarRelatorio(f); e.target.value = ""; };

let dragN = 0;
window.addEventListener("dragenter", e => { e.preventDefault(); if (++dragN === 1) $("drop-veu").classList.add("on"); });
window.addEventListener("dragleave", e => { e.preventDefault(); if (--dragN <= 0) { dragN = 0; $("drop-veu").classList.remove("on"); } });
window.addEventListener("dragover", e => e.preventDefault());
window.addEventListener("drop", e => {
    e.preventDefault(); dragN = 0; $("drop-veu").classList.remove("on");
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) enviarRelatorio(f);
});

async function enviarRelatorio(file) {
    const bt = $("bt-enviar");
    bt.disabled = true; bt.textContent = "Enviando " + file.name.slice(0, 22) + "…";
    try {
        const fd = new FormData();
        fd.append("arquivo", file);
        const r = await fetch("/api/execucoes/iniciar-relatorio", { method: "POST", body: fd });
        if (r.status === 409) { toast("Já existe um ciclo rodando."); return; }
        if (!r.ok) {
            const e = await r.json().catch(() => ({}));
            toast("Erro ao iniciar: " + (e.detail || r.status)); return;
        }
        const data = await r.json();
        toast("Relatório recebido — o robô começou ✓");
        await recarregarExecs();
        const ex = EXECS.find(x => x.id === data.execucao_id) || { id: data.execucao_id, status: "RODANDO" };
        acompanharCiclo(ex);
    } catch (e) {
        toast("Erro de conexão ao enviar.");
    } finally {
        bt.disabled = false; bt.textContent = "⬆ Enviar relatório";
    }
}

/* ============================================================
   GAVETAS: histórico + configurações + apresentação
   ============================================================ */
function abrirGaveta(id) { $("veu").classList.add("on"); $(id).classList.add("on"); }
function fecharGavetas() {
    $("veu").classList.remove("on");
    document.querySelectorAll(".drawer").forEach(d => d.classList.remove("on"));
}
$("veu").onclick = fecharGavetas;
document.querySelectorAll("[data-fecha]").forEach(b => b.onclick = fecharGavetas);
document.addEventListener("keydown", e => { if (e.key === "Escape") fecharGavetas(); });

$("bt-apres").onclick = () => window.open("/static/relatorio_conciliacao.html", "_blank");

$("bt-hist").onclick = async () => {
    abrirGaveta("drawer-hist");
    await recarregarExecs();
    const c = $("hist-lista"); c.innerHTML = "";
    EXECS.forEach(e => {
        const el = document.createElement("div");
        el.className = "h-item" + (EXEC && e.id === EXEC.id ? " sel" : "");
        el.innerHTML = `<span class="h-id">#${e.id}</span>
            <span class="h-data">${(e.iniciado_em || "").replace("T", " ").slice(0, 16)}</span>
            <span class="h-nums"><b>${e.total_titulos || 0}</b> títulos · <span class="c">${e.total_criticos || 0} crít.</span></span>
            <span class="chip-estado ${e.status}">${e.status.toLowerCase()}</span>`;
        el.onclick = () => {
            fecharGavetas();
            e.status === "RODANDO" ? acompanharCiclo(e) : carregarCiclo(e.id);
        };
        c.appendChild(el);
    });
    carregarLogsHist();
};
async function carregarLogsHist() {
    if (!EXEC) return;
    const term = $("hist-term"); term.innerHTML = "";
    const r = await fetch(`/api/execucoes/${EXEC.id}/logs`, { headers: H() });
    if (!r.ok) return;
    (await r.json()).forEach(l => {
        const d = document.createElement("div");
        d.className = "log-" + l.level;
        const ts = (l.timestamp || "").slice(11, 19);
        d.textContent = `[${ts}] ${l.mensagem}`;
        term.appendChild(d);
    });
    term.scrollTop = term.scrollHeight;
}

/* configurações */
const CONFIG_FIELDS = [
    "SIENGE_BASE_URL", "SIENGE_USERNAME", "SIENGE_PASSWORD",
    "SANTANDER_CLIENT_ID", "SANTANDER_CLIENT_SECRET", "SANTANDER_CERT_PATH", "SANTANDER_CERT_PASSWORD", "SANTANDER_ENV",
    "ANTHROPIC_API_KEY",
    "SEFAZ_CNPJ", "SEFAZ_CERT_PATH", "SEFAZ_CERT_PASSWORD",
    "NOTIF_EMAIL_DESTINO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD",
];
$("bt-config").onclick = async () => {
    abrirGaveta("drawer-config");
    const r = await fetch("/api/config", { headers: H() });
    if (!r.ok) return;
    const cfg = await r.json();
    CONFIG_FIELDS.forEach(f => { const el = $("cfg_" + f); if (el && cfg[f]) el.value = cfg[f]; });
};
$("bt-salvar-cfg").onclick = async () => {
    const payload = {};
    CONFIG_FIELDS.forEach(f => { const el = $("cfg_" + f); if (el) payload[f] = el.value; });
    const r = await fetch("/api/config", { method: "POST", headers: H(), body: JSON.stringify(payload) });
    toast(r.ok ? "Configurações salvas ✓" : "Erro ao salvar.");
    if (r.ok) fecharGavetas();
};

/* ============================================================ */
boot();
