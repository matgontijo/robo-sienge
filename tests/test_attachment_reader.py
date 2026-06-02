import pytest
import json
from unittest.mock import MagicMock

from modules.attachment_reader import AttachmentReader

@pytest.fixture
def attachment_reader():
    return AttachmentReader("dummy_api_key")

def test_validar_chave_nfe(attachment_reader):
    # Chave válida
    chave_ok = "35230911111111111111550010000000011000000008"
    assert attachment_reader._validar_chave_nfe(chave_ok) is True
    
    # Chave com dígito verificador errado (final 7 ao invés de 8)
    chave_errada = "35230911111111111111550010000000011000000007"
    assert attachment_reader._validar_chave_nfe(chave_errada) is False
    
    # Tamanho errado
    assert attachment_reader._validar_chave_nfe("123") is False

def test_extrair_chave_valida(mocker, attachment_reader):
    # Mock PyMuPDF para não precisar de PDF real
    mocker.patch.object(attachment_reader, '_pdf_para_imagens', return_value=[b"fake_image_bytes"])
    
    # Mock da API do Anthropic
    mock_create = mocker.patch.object(attachment_reader.client.messages, 'create')
    
    # Simula resposta válida do Claude com JSON (usando a chave válida)
    chave_ok = "35230911111111111111550010000000011000000008"
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({"chave": chave_ok, "confianca": "alta"}))]
    mock_create.return_value = mock_response
    
    result = attachment_reader.extrair_chave_nfe(b"pdf_content")
    
    assert result == chave_ok
    assert mock_create.call_count == 1
    
    # Testa se o cache funciona na segunda chamada com os mesmos bytes
    mock_create.reset_mock()
    result_cache = attachment_reader.extrair_chave_nfe(b"pdf_content")
    assert result_cache == chave_ok
    assert mock_create.call_count == 0 # Não deve ter chamado o Claude novamente

def test_chave_invalida_digito(mocker, attachment_reader):
    mocker.patch.object(attachment_reader, '_pdf_para_imagens', return_value=[b"fake_image_bytes"])
    
    mock_create = mocker.patch.object(attachment_reader.client.messages, 'create')
    
    # Chave inválida no dígito
    chave_errada = "35230911111111111111550010000000011000000007"
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({"chave": chave_errada, "confianca": "alta"}))]
    mock_create.return_value = mock_response
    
    result = attachment_reader.extrair_chave_nfe(b"pdf_content_2")
    
    # Deve retornar None porque a validação do dígito falhou
    assert result is None

def test_pdf_ilegivel(mocker, attachment_reader):
    mocker.patch.object(attachment_reader, '_pdf_para_imagens', return_value=[b"fake_image_bytes"])
    
    mock_create = mocker.patch.object(attachment_reader.client.messages, 'create')
    
    # Simula resposta do Claude quando imagem é ilegível
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=json.dumps({"chave": None, "confianca": "baixa", "motivo": "Ilegível"}))]
    mock_create.return_value = mock_response
    
    result = attachment_reader.extrair_chave_nfe(b"pdf_content_3")
    
    assert result is None
