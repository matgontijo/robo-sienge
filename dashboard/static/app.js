let token = sessionStorage.getItem("auth_token");

const NOMES_TIPO = {
    "PAGAMENTO_DESTINO_DIVERGENTE": "Destino do pagamento ≠ CNPJ do credor",
    "CNPJ_DIVERGENTE": "CNPJ da nota ≠ credor do título",
    "VALOR_DIVERGENTE": "Valor do título ≠ valor da nota",
    "LIQUIDO_BRUTO_DIVERGENTE": "Líquido ≠ bruto − retenções (via NF)",
    "LIQUIDO_PARCELA_DIVERGENTE": "Parcela − retenções ≠ valor a pagar",
    "BOLETO_VALOR_DIVERGENTE": "Valor do boleto ≠ valor a pagar",
    "BOLETO_BANCO_INCOMPATIVEL": "Banco do boleto × forma (próprio/outros)",
    "BOLETO_VENCIMENTO_DIVERGENTE": "Vencimento do boleto ≠ título",
    "TRANSFERENCIA_BANCO_INCOMPATIVEL": "TED × depósito mesmo banco",
    "IMPOSTO_DIVERGENTE": "Imposto retido ≠ destacado na nota",
    "CHAVE_NFE_INVALIDA": "Chave de NF-e inválida",
    "BOLETO_NAO_ENCONTRADO": "Boleto não encontrado no DDA",
    "SEM_ANEXO": "Título sem anexo no Sienge",
    "ANEXO_ILEGIVEL": "Anexo não lido (OCR pendente)",
    "PIX_NAO_VERIFICAVEL": "Chave Pix não verificável",
    "PAGAMENTO_FORMA_INCOMPATIVEL": "Forma de pagamento incompatível",
    "FORMA_PAGAMENTO_AUSENTE": "Sem forma de pagamento cadastrada",
    "NF_SEM_CNPJ_DO_CREDOR": "CNPJ do credor não aparece na NF anexada",
    "IMPOSTO_NF_DIVERGENTE": "Retenção do título ≠ destacada na nota",
    "IMPOSTO_NAO_RETIDO": "Nota destaca retenção que o título não lançou",
    "RETENCAO_ALIQUOTA_SUSPEITA": "Alíquota de retenção acima do usual",
    "RETENCAO_INDEVIDA_SIMPLES": "Retenção federal de fornecedor do Simples",
    "VENCIMENTO_DIVERGENTE": "Vencimento divergente",
};
let chartInstance = null;
let currentExecId = null;
let eventSource = null;
let allDivergencias = [];
let userRole = null;

window.onload = () => {
    if (token) {
        document.getElementById('login-container').style.display = 'none';
        document.getElementById('app-container').style.display = 'flex';
        initDashboard();
    }
};

function login() {
    const u = document.getElementById("username").value;
    const p = document.getElementById("password").value;
    
    if(!u || !p) return;
    
    token = btoa(u + ":" + p);
    
    const btn = document.getElementById("btn-login");
    const err = document.getElementById("login-error");
    
    btn.innerText = "Acessando...";
    btn.disabled = true;
    err.style.display = "none";
    
    fetch("/api/stats", { headers: getHeaders() })
    .then(r => {
        if (r.ok) {
            sessionStorage.setItem("auth_token", token);
            document.getElementById('login-container').style.display = 'none';
            document.getElementById('app-container').style.display = 'flex';
            initDashboard();
        } else {
            err.style.display = "block";
            token = null;
            btn.innerText = "Acessar Painel";
            btn.disabled = false;
        }
    })
    .catch(() => {
        err.innerText = "Erro de conexão com o servidor.";
        err.style.display = "block";
        token = null;
        btn.innerText = "Acessar Painel";
        btn.disabled = false;
    });
}

function getHeaders() {
    return {
        "Authorization": "Basic " + token,
        "Content-Type": "application/json"
    };
}

async function initDashboard() {
    try {
        const meRes = await fetch("/api/me", { headers: getHeaders() });
        if (meRes.ok) {
            const meData = await meRes.json();
            userRole = meData.role;
            
            if (userRole !== 'ADMIN') {
                const nav = document.getElementById('nav-settings');
                if (nav) nav.style.display = 'none';
            }
            if (userRole === 'LEITURA') {
                document.querySelectorAll('.header-actions button').forEach(b => b.style.display = 'none');
            }
        }
    } catch(e) { console.error(e); }

    await fetchStats();
    await fetchHistorico();
    atualizarBadgeConferencia();

    // O trabalho do dia a dia é aprovar: abre direto na Conferência
    switchMainTab('conferencia');

    // Auto refresh status se a ultima tiver rodando
    setInterval(() => {
        const row = document.querySelector("#tbody-historico tr:first-child .badge.RODANDO");
        if(row || (currentExecId && eventSource)) {
            fetchStats();
            fetchHistorico(false); // atualiza sem recriar
        }
    }, 5000);
    setInterval(atualizarBadgeConferencia, 30000);
}

// Badge no menu + cockpit da remessa: estado da revisão do último ciclo concluído
const moedaBR = v => (v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });

async function atualizarBadgeConferencia() {
    try {
        const r = await fetch("/api/execucoes?limit=10", { headers: getHeaders() });
        if (!r.ok) return;
        const done = (await r.json()).filter(e => e.status === "CONCLUIDO");
        if (!done.length) return;
        const exec = done[0];
        const divs = await (await fetch(`/api/execucoes/${exec.id}/divergencias`, { headers: getHeaders() })).json();

        const pendentes = divs.filter(d => (d.status_revisao || "PENDENTE") === "PENDENTE");
        const pendCrit = pendentes.filter(d => d.criticidade === "CRITICA");
        const revisadas = divs.length - pendentes.length;

        // badge do menu
        const badge = document.getElementById("nav-conf-badge");
        if (badge) {
            badge.style.display = pendCrit.length > 0 ? "" : "none";
            badge.textContent = pendCrit.length;
        }

        // cockpit da remessa
        const hero = document.getElementById("hero-remessa");
        if (!hero) return;
        hero.style.display = "";
        document.getElementById("hr-ciclo").innerText =
            "ciclo #" + exec.id + " · " + new Date(exec.iniciado_em).toLocaleDateString("pt-BR");

        // títulos distintos com crítica pendente e valor sob crítica
        const titulosCrit = new Map();
        pendCrit.forEach(d => { if (!titulosCrit.has(d.titulo_numero)) titulosCrit.set(d.titulo_numero, d.valor_sienge || 0); });
        const valorRisco = [...titulosCrit.values()].reduce((a, b) => a + b, 0);
        const titulosComPend = new Set(pendentes.map(d => d.titulo_numero)).size;
        const liberados = (exec.total_titulos || 0) - titulosComPend;

        const st = document.getElementById("hr-status");
        const sub = document.getElementById("hr-sub");
        if (pendCrit.length > 0) {
            st.className = "hr-status crit";
            st.innerText = "Remessa bloqueada";
            sub.innerText = pendCrit.length + " apontamento(s) crítico(s) aguardam sua decisão antes do pagamento.";
        } else if (pendentes.length > 0) {
            st.className = "hr-status warn";
            st.innerText = "Quase lá";
            sub.innerText = "Sem críticas pendentes — restam " + pendentes.length + " ponto(s) de atenção para revisar.";
        } else {
            st.className = "hr-status ok";
            st.innerText = "Remessa liberada ✓";
            sub.innerText = "Todos os apontamentos do ciclo foram revisados. Pode montar a remessa.";
        }

        const pct = divs.length ? Math.round(100 * revisadas / divs.length) : 100;
        document.getElementById("hr-bar-fill").style.width = pct + "%";
        document.getElementById("hr-meta").innerText =
            revisadas + " de " + divs.length + " apontamentos revisados (" + pct + "%)";
        document.getElementById("hr-liberados").innerText = liberados + "/" + (exec.total_titulos || 0);
        document.getElementById("hr-criticas").innerText = pendCrit.length;
        document.getElementById("hr-valor-risco").innerText = moedaBR(valorRisco);
    } catch (e) { /* silencioso */ }
}

async function fetchStats() {
    const r = await fetch("/api/stats", { headers: getHeaders() });
    if (!r.ok) return;
    const stats = await r.json();
    
    if (stats.ultima_execucao) {
        document.getElementById("val-ultima-status").innerText = stats.ultima_execucao.status;
        document.getElementById("val-ultima-data").innerText = new Date(stats.ultima_execucao.iniciado_em).toLocaleString();
        
        const card = document.getElementById("card-ultima");
        card.className = "card status-" + stats.ultima_execucao.status;
    }
    
    // Calcula totais do grafico do dia de hoje (ultima barra)
    if (stats.grafico_7dias && stats.grafico_7dias.length > 0) {
        const hoje = stats.grafico_7dias[stats.grafico_7dias.length - 1];
        document.getElementById("val-titulos-hoje").innerText = hoje.total;
        document.getElementById("val-diverg-hoje").innerText = hoje.divergencias;
        document.getElementById("val-criticos-hoje").innerText = hoje.criticos;
    }
    
    document.getElementById("val-taxa-diverg").innerText = stats.taxa_divergencia_hoje.toFixed(1) + "% taxa";
    
    renderChart(stats.grafico_7dias);
}

function renderChart(dados) {
    const ctx = document.getElementById('grafico-7dias').getContext('2d');
    
    const labels = dados.map(d => d.data.substring(5)); // mostra MM-DD
    const dsTitulos = dados.map(d => d.total);
    const dsDiverg = dados.map(d => d.divergencias);
    
    if (chartInstance) {
        chartInstance.data.labels = labels;
        chartInstance.data.datasets[0].data = dsTitulos;
        chartInstance.data.datasets[1].data = dsDiverg;
        chartInstance.update();
        return;
    }
    
    chartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Títulos processados',
                    data: dsTitulos,
                    backgroundColor: 'rgba(15, 118, 110, 0.45)',
                    borderColor: 'rgb(15, 118, 110)',
                    borderWidth: 1.5,
                    borderRadius: 5
                },
                {
                    label: 'Apontamentos',
                    data: dsDiverg,
                    backgroundColor: 'rgba(198, 57, 47, 0.4)',
                    borderColor: 'rgb(198, 57, 47)',
                    borderWidth: 1.5,
                    borderRadius: 5
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { beginAtZero: true }
            }
        }
    });
}

async function fetchHistorico(rebuild = true) {
    const r = await fetch("/api/execucoes?limit=15", { headers: getHeaders() });
    if(!r.ok) return;
    const execs = await r.json();
    
    const tbody = document.getElementById("tbody-historico");
    if(rebuild) tbody.innerHTML = "";
    
    let html = "";
    execs.forEach(e => {
        let fim = e.concluido_em ? new Date(e.concluido_em).toLocaleTimeString() : "-";
        let cssSel = (e.id === currentExecId) ? "selected" : "";
        html += `<tr class="${cssSel}" onclick="selectExecucao(${e.id})">
            <td>#${e.id}</td>
            <td>${new Date(e.iniciado_em).toLocaleString()}</td>
            <td>${fim}</td>
            <td>${e.total_titulos}</td>
            <td>${e.total_divergencias}</td>
            <td class="text-danger">${e.total_criticos}</td>
            <td>${e.iniciado_por}</td>
            <td><span class="badge ${e.status}">${e.status}</span></td>
        </tr>`;
    });
    
    if(rebuild || tbody.innerHTML !== html) {
        tbody.innerHTML = html;
    }
}

async function selectExecucao(id) {
    currentExecId = id;
    document.getElementById("detalhes-container").style.display = "block";
    document.getElementById("detalhes-id").innerText = "#" + id;
    
    // Highlight table
    document.querySelectorAll("#tbody-historico tr").forEach(tr => tr.classList.remove("selected"));
    const rows = document.querySelectorAll("#tbody-historico tr");
    for(let r of rows) {
        if(r.cells[0].innerText === "#"+id) r.classList.add("selected");
    }
    
    const r = await fetch(`/api/execucoes/${id}`, { headers: getHeaders() });
    const data = await r.json();
    
    allDivergencias = data.divergencias;
    renderDivergencias();
    
    // Tabs visibility
    document.getElementById("tab-relatorio").style.display = data.execucao.status === "CONCLUIDO" ? "block" : "none";
    document.getElementById("tab-abortar").style.display = (data.execucao.status === "RODANDO" && userRole !== "LEITURA") ? "block" : "none";
    
    // Load logs
    const rL = await fetch(`/api/execucoes/${id}/logs`, { headers: getHeaders() });
    const logs = await rL.json();
    
    const term = document.getElementById("terminal-logs");
    term.innerHTML = "";
    logs.forEach(l => appendLog(l));
    term.scrollTop = term.scrollHeight;
    
    // Handle SSE if running
    if(eventSource) {
        eventSource.close();
        eventSource = null;
    }
    
    if(data.execucao.status === "RODANDO") {
        startSSE(id);
        switchTab("logs");
    } else {
        switchTab("divergencias");
    }
}

function startSSE(id) {
    // Para SSE nativo que nao tem Header, passamos token via QS
    eventSource = new EventSource(`/api/stream/${id}?token=${token}`);
    
    eventSource.onmessage = (e) => {
        const term = document.getElementById("terminal-logs");
        
        let colorClass = "log-INFO";
        if(e.data.includes("WARNING")) colorClass = "log-WARNING";
        if(e.data.includes("ERROR")) colorClass = "log-ERROR";
        if(e.data.includes("SUCCESS")) colorClass = "log-SUCCESS";
        
        const div = document.createElement("div");
        div.className = colorClass;
        div.innerText = e.data;
        term.appendChild(div);
        term.scrollTop = term.scrollHeight;
    };
    
    eventSource.addEventListener("close", () => {
        eventSource.close();
        eventSource = null;
        fetchStats();
        fetchHistorico();
        document.getElementById("tab-abortar").style.display = "none";
        document.getElementById("tab-relatorio").style.display = "block";
    });
}

function appendLog(l) {
    const term = document.getElementById("terminal-logs");
    let colorClass = "log-" + l.level;
    let d = new Date(l.timestamp);
    let timeStr = `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}:${d.getSeconds().toString().padStart(2,'0')}`;
    
    const div = document.createElement("div");
    div.className = colorClass;
    div.innerText = `[${timeStr}] ${l.level.padEnd(8, ' ')} | ${l.modulo} - ${l.mensagem}`;
    term.appendChild(div);
}

function switchTab(tabId) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(t => t.classList.remove("active"));
    
    event.currentTarget.classList.add("active");
    document.getElementById("tab-" + tabId).classList.add("active");
}

function renderDivergencias() {
    const crit = document.getElementById("filtro-crit").value;
    const busca = document.getElementById("filtro-busca").value.toLowerCase();
    
    const tbody = document.getElementById("tbody-divergencias");
    tbody.innerHTML = "";
    
    const filtradas = allDivergencias.filter(d => {
        if(crit !== "Todas" && d.criticidade !== crit) return false;
        if(busca) {
            const num = (d.titulo_numero||"").toLowerCase();
            const forne = (d.fornecedor_nome||"").toLowerCase();
            if(!num.includes(busca) && !forne.includes(busca)) return false;
        }
        return true;
    });
    
    filtradas.forEach(d => {
        let badge = d.criticidade === "CRITICA" ? "badge-crit" : (d.criticidade==="ATENCAO"?"badge-aten":"badge-info");
        
        let link = d.danfe_path ? `<a href="#" onclick="abrirDanfe('${d.danfe_path}')">Ver PDF</a>` : "-";
        
        tbody.innerHTML += `<tr>
            <td>${d.titulo_numero}</td>
            <td>${d.fornecedor_nome}</td>
            <td>${NOMES_TIPO[d.tipo] || d.tipo}</td>
            <td>${d.campo}</td>
            <td>${d.valor_sienge_campo || "-"}</td>
            <td>${d.valor_nfe_campo || d.valor_boleto_campo || "-"}</td>
            <td><span class="${badge}">${d.criticidade}</span></td>
            <td>${link}</td>
        </tr>`;
    });
}

document.getElementById("filtro-crit").addEventListener("change", renderDivergencias);
document.getElementById("filtro-busca").addEventListener("input", renderDivergencias);

function abrirDanfe(path) {
    window.open(`/api/execucoes/${currentExecId}/danfe?path=${encodeURIComponent(path)}&token=${token}`, '_blank');
}

function baixarRelatorio() {
    window.open(`/api/execucoes/${currentExecId}/relatorio?token=${token}`, '_blank');
}

async function abortarExecucao() {
    if(!confirm("Tem certeza que deseja abortar a execução atual? O orquestrador vai parar após finalizar o título atual.")) return;
    
    await fetch(`/api/execucoes/${currentExecId}/abortar`, {
        method: "POST",
        headers: getHeaders()
    });
    alert("Sinal de aborto enviado.");
}

async function rodarAgora() {
    let di = prompt("Data Início (YYYY-MM-DD)", new Date().toISOString().substring(0,10));
    if(!di) return;
    let df = prompt("Data Fim (YYYY-MM-DD)", di);
    if(!df) return;
    
    const r = await fetch("/api/execucoes/iniciar", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({ data_inicio: di, data_fim: df })
    });
    
    if(r.status === 409) {
        alert("Já existe uma execução rodando.");
        return;
    }
    
    if(r.ok) {
        const data = await r.json();
        if(data.execucao_id > 0) {
            await fetchStats();
            await fetchHistorico();
            selectExecucao(data.execucao_id);
        } else {
            setTimeout(async () => {
                await fetchStats();
                await fetchHistorico();
            }, 1000);
        }
    }
}

async function rodarComRelatorio(input) {
    const file = input.files && input.files[0];
    if (!file) return;

    // Janela DDA automática (hoje → +7 dias, calculada no servidor);
    // os títulos vêm do relatório enviado — sem perguntas.
    const fd = new FormData();
    fd.append("arquivo", file);

    const btn = document.getElementById("btn-relatorio");
    const txtOriginal = btn.innerText;
    btn.innerText = "Enviando " + file.name + "...";
    btn.disabled = true;

    try {
        const r = await fetch("/api/execucoes/iniciar-relatorio", {
            method: "POST",
            headers: { "Authorization": "Basic " + token }, // sem Content-Type: o browser põe o boundary
            body: fd
        });

        if (r.status === 409) { alert("Já existe uma execução rodando."); return; }
        if (!r.ok) {
            const e = await r.json().catch(() => ({}));
            alert("Erro ao iniciar: " + (e.detail || r.status));
            return;
        }
        const data = await r.json();
        await fetchStats();
        await fetchHistorico();
        if (data.execucao_id > 0) selectExecucao(data.execucao_id);
    } catch (e) {
        alert("Erro de conexão: " + e);
    } finally {
        btn.innerText = txtOriginal;
        btn.disabled = false;
        input.value = ""; // permite reenviar o mesmo arquivo
    }
}

// ==========================================
// CONFIGURAÇÕES
// ==========================================

const VIEW_TITULOS = {
    dashboard:    ["Visão geral", "Acompanhe os ciclos de conferência antes da remessa de pagamento."],
    conferencia:  ["Conferência", "Aprove ou rejeite cada apontamento — ↑↓ navegam, A aprova o título."],
    presentation: ["Apresentação", "Status do robô, pendências, custos e resultados — pronta para reunião."],
    settings:     ["Configurações", "Credenciais das integrações. Alterações valem a partir do próximo ciclo."],
};

function switchMainTab(viewName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    const nav = document.getElementById('nav-' + viewName);
    if (nav) nav.classList.add('active');
    else if (typeof event !== "undefined" && event.currentTarget) event.currentTarget.classList.add('active');

    ['dashboard', 'conferencia', 'presentation', 'settings'].forEach(v => {
        const el = document.getElementById('view-' + v);
        if (el) el.style.display = viewName === v ? 'block' : 'none';
    });

    const [t, s] = VIEW_TITULOS[viewName] || ["", ""];
    const vt = document.getElementById('view-title');
    const vs = document.getElementById('view-sub');
    if (vt) vt.innerText = t;
    if (vs) vs.innerText = s;

    // Conferência/Apresentação ocupam a tela toda (sem padding nem rolagem dupla)
    const content = document.querySelector('.content');
    if (content) content.classList.toggle('full-bleed',
        viewName === 'conferencia' || viewName === 'presentation');

    if (viewName === 'settings') fetchConfig();
    if (viewName === 'conferencia') {
        // carrega a tela de conferência na primeira abertura (e recarrega os dados nas demais)
        const frame = document.getElementById('frame-conferencia');
        if (frame && !frame.src) frame.src = '/static/revisao.html?v=12';
        atualizarBadgeConferencia();
    }
}

const CONFIG_FIELDS = [
    "SIENGE_BASE_URL", "SIENGE_USERNAME", "SIENGE_PASSWORD",
    "SANTANDER_CLIENT_ID", "SANTANDER_CLIENT_SECRET", "SANTANDER_CERT_PATH", "SANTANDER_CERT_PASSWORD", "SANTANDER_ENV",
    "ANTHROPIC_API_KEY",
    "SEFAZ_CNPJ", "SEFAZ_CERT_PATH", "SEFAZ_CERT_PASSWORD",
    "NOTIF_EMAIL_DESTINO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"
];

async function fetchConfig() {
    const r = await fetch("/api/config", { headers: getHeaders() });
    if (!r.ok) return;
    const config = await r.json();
    
    CONFIG_FIELDS.forEach(f => {
        const el = document.getElementById("cfg_" + f);
        if (el && config[f]) {
            el.value = config[f];
        }
    });
}

async function saveConfig() {
    const payload = {};
    CONFIG_FIELDS.forEach(f => {
        const el = document.getElementById("cfg_" + f);
        if (el) {
            payload[f] = el.value;
        }
    });
    
    const r = await fetch("/api/config", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify(payload)
    });
    
    if (r.ok) {
        alert("Configurações salvas e recarregadas com sucesso no servidor!");
    } else {
        alert("Erro ao salvar configurações.");
    }
}
