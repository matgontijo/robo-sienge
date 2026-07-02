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

def _bill(i):
    return {"id": i, "documentNumber": str(100 + i), "documentIdentificationId": "NF",
            "issueDate": "2024-01-01", "totalInvoiceAmount": 100.0, "discount": 0.0,
            "status": "S", "originId": "CP", "accessKeyNumber": None}

def test_listar_titulos_paginacao(mocker, sienge_client):
    # GET /bills pagina por resultSetMetadata.count (limit=200 por página)
    mock_request = mocker.patch.object(sienge_client.session, 'request')

    resp_page_1 = MagicMock()
    resp_page_1.status_code = 200
    resp_page_1.json.return_value = {
        "results": [_bill(i) for i in range(1, 201)],
        "resultSetMetadata": {"count": 201, "offset": 0, "limit": 200}
    }

    resp_page_2 = MagicMock()
    resp_page_2.status_code = 200
    resp_page_2.json.return_value = {
        "results": [_bill(201)],
        "resultSetMetadata": {"count": 201, "offset": 200, "limit": 200}
    }

    mock_request.side_effect = [resp_page_1, resp_page_2]

    data_inicio = date(2024, 1, 1)
    data_fim = date(2024, 1, 31)
    titulos = sienge_client.listar_titulos(data_inicio, data_fim)

    # Verifica se iterou todas as páginas
    assert len(titulos) == 201
    assert titulos[0].id == 1
    assert titulos[0].numero == "1"          # nº do título = id do bill
    assert titulos[0].numero_documento == "101"
    assert titulos[-1].id == 201
    assert mock_request.call_count == 2

def test_nfedata_da_chave():
    # chave: cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) numero(9) tpEmis(1) cNF(8) DV(1)
    chave = "35" + "2601" + "11222333000181" + "55" + "001" + "000175118" + "1" + "12345678" + "9"
    nfe = SiengeClient._nfedata_da_chave(chave)
    assert nfe is not None
    assert nfe.cnpj_emitente == "11222333000181"
    assert nfe.numero_nfe == "175118"
    assert nfe.serie == "1"
    assert SiengeClient._nfedata_da_chave("123") is None

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

def test_429_rate_limit_espera_e_repete(mocker, sienge_client):
    mock_request = mocker.patch.object(sienge_client.session, 'request')
    mock_sleep = mocker.patch('time.sleep')

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "2"}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"results": [], "resultSetMetadata": {"count": 0, "offset": 0, "limit": 200}}

    mock_request.side_effect = [resp_429, resp_ok]

    titulos = sienge_client.listar_titulos(date(2024, 1, 1), date(2024, 1, 31))

    assert titulos == []
    assert mock_request.call_count == 2   # 429 não é fatal: espera e repete
    assert mock_sleep.call_count >= 1

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
