import pytest
import time
from unittest.mock import MagicMock
from modules.santander_client import SantanderClient

def mock_load_certificate(self):
    self.temp_cert_file = MagicMock()
    self.temp_cert_file.name = "dummy_cert.pem"
    self.temp_key_file = MagicMock()
    self.temp_key_file.name = "dummy_key.pem"

@pytest.fixture
def mock_santander_client(mocker):
    mocker.patch.object(SantanderClient, '_load_certificate', new=mock_load_certificate)
    client = SantanderClient("client_id", "client_secret", "dummy.pfx", "dummy")
    return client

def test_renovacao_token(mocker, mock_santander_client):
    mock_request = mocker.patch.object(mock_santander_client.session, 'request')
    
    # Simula o _request_with_retry em 'autenticar'
    mock_response_auth = MagicMock()
    mock_response_auth.status_code = 200
    mock_response_auth.json.return_value = {
        "access_token": "token_123",
        "expires_in": 3600
    }
    
    # Simula o _request_with_retry em 'consultar_dda'
    mock_response_dda = MagicMock()
    mock_response_dda.status_code = 200
    mock_response_dda.json.return_value = {
        "boletos": [] # Menor que limit, acaba a paginao
    }
    
    # A ordem das chamadas de request será: Auth -> DDA
    mock_request.side_effect = [mock_response_auth, mock_response_dda]
    
    # Fora a data de expirao do token para o passado, forando a autenticao
    mock_santander_client.access_token = "token_velho"
    mock_santander_client.token_expires_at = time.time() - 1000
    
    from datetime import date
    boletos = mock_santander_client.consultar_dda(date(2024, 1, 1), date(2024, 1, 31))
    
    assert mock_santander_client.access_token == "token_123"
    assert mock_request.call_count == 2 # 1 auth, 1 DDA

def test_consultar_dda_paginado(mocker, mock_santander_client):
    mock_request = mocker.patch.object(mock_santander_client.session, 'request')
    
    # J está autenticado e no precisa renovar
    mock_santander_client.access_token = "token_ok"
    mock_santander_client.token_expires_at = time.time() + 3000
    
    # Paginao: Pagin 1 (tem 100 boletos), Pgina 2 (tem 10 boletos)
    boletos_pg1 = [{"codigoBarras": f"123_{i}", "valorNominal": 10.0} for i in range(100)]
    boletos_pg2 = [{"codigoBarras": f"123_2_{i}", "valorNominal": 20.0} for i in range(10)]
    
    mock_resp_1 = MagicMock()
    mock_resp_1.status_code = 200
    mock_resp_1.json.return_value = {"boletos": boletos_pg1}
    
    mock_resp_2 = MagicMock()
    mock_resp_2.status_code = 200
    mock_resp_2.json.return_value = {"boletos": boletos_pg2}
    
    mock_request.side_effect = [mock_resp_1, mock_resp_2]
    
    from datetime import date
    boletos = mock_santander_client.consultar_dda(date(2024, 1, 1), date(2024, 1, 31))
    
    assert len(boletos) == 110
    assert mock_request.call_count == 2
