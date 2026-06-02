import pytest
import requests
from datetime import date
from unittest.mock import MagicMock
from requests.exceptions import HTTPError

from modules.sienge_client import SiengeClient

@pytest.fixture
def sienge_client():
    return SiengeClient(
        base_url="https://api.sienge.com.br/teste/public/api/v1",
        username="user",
        password="password"
    )

def test_listar_titulos_paginacao(mocker, sienge_client):
    # Prepara os mocks das respostas (2 páginas)
    mock_request = mocker.patch.object(sienge_client.session, 'request')
    
    # Resposta da Página 1
    resp_page_1 = MagicMock()
    resp_page_1.status_code = 200
    resp_page_1.json.return_value = {
        "results": [
            {"id": 1, "documentNumber": "100", "providerCpfCnpj": "111", "value": 100.0, "balance": 100.0, "dueDate": "2024-01-01", "paymentMethod": "BOLETO", "situation": "ABERTO"},
            {"id": 2, "documentNumber": "101", "providerCpfCnpj": "222", "value": 200.0, "balance": 200.0, "dueDate": "2024-01-02", "paymentMethod": "PIX", "situation": "ABERTO"}
        ],
        "resultSetMetadata": {"hasNext": True}
    }
    
    # Resposta da Página 2
    resp_page_2 = MagicMock()
    resp_page_2.status_code = 200
    resp_page_2.json.return_value = {
        "results": [
            {"id": 3, "documentNumber": "102", "providerCpfCnpj": "333", "value": 300.0, "balance": 300.0, "dueDate": "2024-01-03", "paymentMethod": "TED", "situation": "VENCIDO"}
        ],
        "resultSetMetadata": {"hasNext": False}
    }
    
    # Configura o mock para retornar a pág 1 na 1ª chamada e pág 2 na 2ª
    mock_request.side_effect = [resp_page_1, resp_page_2]
    
    data_inicio = date(2024, 1, 1)
    data_fim = date(2024, 1, 31)
    titulos = sienge_client.listar_titulos(data_inicio, data_fim)
    
    # Verifica se iterou todas as páginas
    assert len(titulos) == 3
    assert titulos[0].id == 1
    assert titulos[2].id == 3
    assert mock_request.call_count == 2

def test_baixar_anexo_sem_anexo(mocker, sienge_client):
    mock_request = mocker.patch.object(sienge_client.session, 'request')
    
    resp_sem_anexo = MagicMock()
    resp_sem_anexo.status_code = 200
    resp_sem_anexo.json.return_value = {"results": []}
    
    mock_request.return_value = resp_sem_anexo
    
    # Deve retornar None e não lançar exceção
    result = sienge_client.baixar_anexo(123)
    
    assert result is None
    assert mock_request.call_count == 1

def test_retry_em_500(mocker, sienge_client):
    mock_request = mocker.patch.object(sienge_client.session, 'request')
    mock_sleep = mocker.patch('time.sleep') # Evita esperar o backoff nos testes
    
    # Cria uma resposta 500
    resp_500 = MagicMock()
    resp_500.status_code = 500
    
    # A função raise_for_status() normalmente lança um HTTPError
    def raise_for_status_mock():
        raise HTTPError(response=resp_500)
    
    resp_500.raise_for_status.side_effect = raise_for_status_mock
    
    mock_request.return_value = resp_500
    
    # Tenta listar_titulos, o retry deve falhar após 3 tentativas
    with pytest.raises(HTTPError):
        sienge_client.listar_titulos(date(2024, 1, 1), date(2024, 1, 31))
        
    assert mock_request.call_count == 3
    assert mock_sleep.call_count == 2 # Dorme depois da 1ª e 2ª tentativa
