let token = sessionStorage.getItem("auth_token");
let chartInstance = null;
let currentExecId = null;
let eventSource = null;
let allDivergencias = [];
let userRole = null;

window.onload = () => {
    if (token) {
        document.getElementById('login-container').style.display = 'none';
        document.getElementById('app-container').style.display = 'block';
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
            document.getElementById('app-container').style.display = 'block';
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
                const confTabs = document.querySelectorAll('.tab-btn');
                if(confTabs.length > 1) confTabs[1].style.display = 'none';
            }
            if (userRole === 'LEITURA') {
                const rodarBtn = document.querySelector('.header .btn-primary');
                if(rodarBtn) rodarBtn.style.display = 'none';
            }
        }
    } catch(e) { console.error(e); }

    await fetchStats();
    await fetchHistorico();
    
    // Auto refresh status se a ultima tiver rodando
    setInterval(() => {
        const row = document.querySelector("#tbody-historico tr:first-child .badge.RODANDO");
        if(row || (currentExecId && eventSource)) {
            fetchStats();
            fetchHistorico(false); // atualiza sem recriar
        }
    }, 5000);
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
                    label: 'Títulos Processados',
                    data: dsTitulos,
                    backgroundColor: 'rgba(13, 110, 253, 0.5)',
                    borderColor: 'rgb(13, 110, 253)',
                    borderWidth: 1
                },
                {
                    label: 'Divergências Encontradas',
                    data: dsDiverg,
                    backgroundColor: 'rgba(220, 53, 69, 0.5)',
                    borderColor: 'rgb(220, 53, 69)',
                    borderWidth: 1
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
            <td>${d.tipo}</td>
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

// ==========================================
// CONFIGURAÇÕES
// ==========================================

function switchMainTab(viewName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    event.currentTarget.classList.add('active');
    
    document.getElementById('view-dashboard').style.display = viewName === 'dashboard' ? 'block' : 'none';
    document.getElementById('view-settings').style.display = viewName === 'settings' ? 'block' : 'none';
    document.getElementById('view-presentation').style.display = viewName === 'presentation' ? 'block' : 'none';
    
    if (viewName === 'settings') {
        fetchConfig();
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
