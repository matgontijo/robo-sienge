import pytest
from fastapi.testclient import TestClient

import config
from dashboard.app import app

client = TestClient(app)


def _login(c=None):
    c = c or client
    r = c.post("/api/login", json={"username": config.DASHBOARD_USER or "admin",
                                   "senha": config.DASHBOARD_PASSWORD or "admin"})
    assert r.status_code == 200
    return c


def test_sem_login_api_da_401_sem_popup():
    # Sem sessão: 401 JSON e NUNCA WWW-Authenticate (que abriria o pop-up feio do navegador).
    c = TestClient(app)
    r = c.get("/api/stats")
    assert r.status_code == 401
    assert "WWW-Authenticate" not in r.headers


def test_sem_login_pagina_redireciona_para_login():
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_login_errado_401():
    c = TestClient(app)
    r = c.post("/api/login", json={"username": "admin", "senha": "senha-completamente-errada"})
    assert r.status_code == 401


def test_login_certo_e_me():
    c = _login(TestClient(app))
    r = c.get("/api/me")
    assert r.status_code == 200
    dados = r.json()
    assert dados["role"] == "ADMIN"
    assert "agente" in dados["telas"] and "usuarios" in dados["telas"]


def test_admin_gerencia_usuarios_e_telas():
    c = _login(TestClient(app))
    c.delete("/api/usuarios/pytest@kfinserv.com.br")  # limpeza de execução anterior
    r = c.post("/api/usuarios", json={"username": "pytest@kfinserv.com.br", "senha": "senha123",
                                      "role": "OPERADOR", "telas": ["conferencia"]})
    assert r.status_code == 200

    # o usuário restrito só enxerga a tela liberada
    c2 = TestClient(app)
    assert c2.post("/api/login", json={"username": "pytest@kfinserv.com.br", "senha": "senha123"}).status_code == 200
    assert c2.get("/api/execucoes?limit=1").status_code == 200          # conferencia: liberada
    assert c2.get("/api/agente/conversas").status_code == 403           # agente: negada
    assert c2.get("/api/usuarios").status_code == 403                   # admin only
    r = c2.get("/", follow_redirects=False)                             # página do agente -> manda p/ conferência
    assert r.status_code == 302 and "/conferencia" in r.headers["location"]

    # bloquear derruba o acesso
    assert c.put("/api/usuarios/pytest@kfinserv.com.br", json={"ativo": False}).status_code == 200
    assert c2.get("/api/execucoes?limit=1").status_code == 401
    assert c.delete("/api/usuarios/pytest@kfinserv.com.br").status_code == 200


def test_stats_retorna_estrutura_correta():
    c = _login(TestClient(app))
    data = c.get("/api/stats").json()
    for k in ("ultima_execucao", "taxa_divergencia_hoje", "taxa_divergencia_semana",
              "total_execucoes_mes", "grafico_7dias"):
        assert k in data


def test_conflito_execucao_ja_rodando(mocker):
    mocker.patch("dashboard.database.get_execucoes",
                 return_value=[type("E", (), {"status": "RODANDO"})()])
    c = _login(TestClient(app))
    r = c.post("/api/execucoes/iniciar", json={"data_inicio": "2024-01-01", "data_fim": "2024-01-31"})
    assert r.status_code == 409


def test_download_relatorio_nao_existe():
    c = _login(TestClient(app))
    assert c.get("/api/execucoes/99999/relatorio").status_code == 404


def test_stream_fecha_quando_concluido(mocker):
    mocker.patch("dashboard.database.get_execucao",
                 return_value=type("E", (), {"status": "CONCLUIDO"})())
    mocker.patch("dashboard.database.get_logs", return_value=[])
    c = _login(TestClient(app))
    r = c.get("/api/stream/1")
    assert r.status_code == 200
    text = r.content.decode()
    assert "event: close" in text


def test_worker_exige_token():
    # modo local: rotas do worker respondem 404 (nuvem desligada) mesmo logado
    c = _login(TestClient(app))
    assert c.get("/api/agente/worker/trabalho").status_code in (401, 404)
