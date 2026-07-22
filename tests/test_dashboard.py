import pytest
from fastapi.testclient import TestClient
from dashboard.app import app

client = TestClient(app)

def test_painel_sem_login_entra_direto():
    # O painel não tem autenticação: qualquer rota responde sem credencial.
    response = client.get("/api/stats")
    assert response.status_code == 200

def test_header_authorization_antigo_nao_quebra():
    # Navegadores com token salvo de versões antigas mandavam
    # "Authorization: Basic null" — não pode gerar 401 nem pop-up.
    response = client.get("/api/stats", headers={"Authorization": "Basic null"})
    assert response.status_code == 200
    assert "WWW-Authenticate" not in response.headers

def test_me_retorna_admin_local():
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {"username": "local", "role": "ADMIN"}

def test_stats_retorna_estrutura_correta():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "ultima_execucao" in data
    assert "taxa_divergencia_hoje" in data
    assert "taxa_divergencia_semana" in data
    assert "total_execucoes_mes" in data
    assert "grafico_7dias" in data

def test_iniciar_execucao_retorna_id():
    payload = {
        "data_inicio": "2024-01-01",
        "data_fim": "2024-01-31"
    }
    response = client.post("/api/execucoes/iniciar", json=payload)
    assert response.status_code in [200, 409] # 409 se uma ja estiver rodando no bd local

    if response.status_code == 200:
        data = response.json()
        assert "execucao_id" in data
        assert type(data["execucao_id"]) == int

def test_conflito_execucao_ja_rodando(mocker):
    # Vamos mockar o get_execucoes para forçar um status RODANDO
    mocker.patch("dashboard.database.get_execucoes", return_value=[type("E", (), {"status": "RODANDO"})()])

    payload = {
        "data_inicio": "2024-01-01",
        "data_fim": "2024-01-31"
    }
    response = client.post("/api/execucoes/iniciar", json=payload)
    assert response.status_code == 409

def test_download_relatorio_nao_existe():
    # Passando id=9999 q provavelmente nao existe, app retorna 404
    response = client.get("/api/execucoes/99999/relatorio")
    assert response.status_code == 404

def test_stream_fecha_quando_concluido(mocker):
    # Testa se o generator retorna 'close' para uma execução concluida
    mocker.patch("dashboard.database.get_execucao", return_value=type("E", (), {"status": "CONCLUIDO"})())
    mocker.patch("dashboard.database.get_logs", return_value=[])

    response = client.get("/api/stream/1")

    assert response.status_code == 200

    # O TestClient não roda assíncrono para streaming no event loop, ele vai consumir até o final
    # Como mockamos o DB para retornar CONCLUIDO logo no primeiro loop, a stream vai fechar.
    # Lemos a resposta como str
    text = response.content.decode()
    assert 'event: close' in text
    assert 'data: Fechando stream' in text
