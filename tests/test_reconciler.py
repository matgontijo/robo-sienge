import pytest
from datetime import date
from models import Titulo, NFeData, Boleto
from modules.reconciler import Reconciler

@pytest.fixture
def reconciler():
    return Reconciler()

@pytest.fixture
def titulo_base():
    return Titulo(
        id=1,
        numero="100",
        fornecedor_nome="Fornecedor A",
        fornecedor_cnpj="11.111.111/0001-11",
        valor_nominal=100.0,
        valor_liquido=100.0,
        data_vencimento=date(2024, 1, 10),
        forma_pagamento="PIX",
        status="ABERTO",
        attachment_bytes=b"pdf",
        chave_nfe="35230911111111111111550010000000011000000008"
    )

@pytest.fixture
def nfe_base():
    return NFeData(
        chave="35230911111111111111550010000000011000000008",
        cnpj_emitente="11111111000111",
        nome_emitente="Fornecedor A",
        valor_total=100.0,
        valor_liquido=100.0,
        data_emissao=date(2024, 1, 1),
        numero_nfe="1",
        serie="1"
    )

def test_titulo_ok_sem_divergencia(reconciler, titulo_base, nfe_base):
    divergencias = reconciler.reconciliar(titulo_base, nfe_base, [])
    assert len(divergencias) == 0

def test_cnpj_divergente(reconciler, titulo_base, nfe_base):
    nfe_base.cnpj_emitente = "22222222000122"
    divergencias = reconciler.reconciliar(titulo_base, nfe_base, [])
    assert len(divergencias) == 1
    assert divergencias[0].tipo == "CNPJ_DIVERGENTE"
    assert divergencias[0].criticidade == "CRITICA"

def test_valor_dentro_tolerancia(reconciler, titulo_base, nfe_base):
    nfe_base.valor_liquido = 100.04
    divergencias = reconciler.reconciliar(titulo_base, nfe_base, [])
    assert len(divergencias) == 0 # Dentro dos 0.05
    
    nfe_base.valor_liquido = 100.06
    divergencias = reconciler.reconciliar(titulo_base, nfe_base, [])
    assert len(divergencias) == 1
    assert divergencias[0].tipo == "VALOR_DIVERGENTE"

def test_boleto_nao_encontrado(reconciler, titulo_base, nfe_base):
    titulo_base.forma_pagamento = "BOLETO"
    
    # Nenhum boleto na lista
    divergencias = reconciler.reconciliar(titulo_base, nfe_base, [])
    assert len(divergencias) == 1
    assert divergencias[0].tipo == "BOLETO_NAO_ENCONTRADO"
    
    # Boleto correspondente na lista
    boleto = Boleto(
        codigo_barras="123",
        cnpj_beneficiario="11111111000111",
        nome_beneficiario="Fornecedor A",
        valor=100.0,
        data_vencimento=date(2024, 1, 10)
    )
    divergencias2 = reconciler.reconciliar(titulo_base, nfe_base, [boleto])
    assert len(divergencias2) == 0
    
    # Boleto com 1 dia de diferena no vencimento (tolerado pelo DDA match, mas gera ATENCAO no VENCIMENTO)
    boleto_dif = Boleto(
        codigo_barras="123",
        cnpj_beneficiario="11111111000111",
        nome_beneficiario="Fornecedor A",
        valor=100.0,
        data_vencimento=date(2024, 1, 11)
    )
    divergencias3 = reconciler.reconciliar(titulo_base, nfe_base, [boleto_dif])
    assert len(divergencias3) == 1
    assert divergencias3[0].tipo == "VENCIMENTO_DIVERGENTE"
    assert divergencias3[0].criticidade == "ATENCAO"

def test_sem_anexo(reconciler, titulo_base, nfe_base):
    titulo_base.attachment_bytes = None
    titulo_base.attachment_url = None
    divergencias = reconciler.reconciliar(titulo_base, None, [])
    assert len(divergencias) == 1
    assert divergencias[0].tipo == "SEM_ANEXO"
