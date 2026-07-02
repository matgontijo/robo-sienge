from datetime import date, timedelta

import pytest
from openpyxl import Workbook

from modules.report_parser import ReportParser
from modules.attachment_reader import decodificar_linha_digitavel, _FEBRABAN_BASE


@pytest.fixture
def parser():
    return ReportParser()


# --------------------------------------------------------------- helpers puros

@pytest.mark.parametrize("entrada,esperado", [
    ("1.234,56", 1234.56),
    ("500,00", 500.0),
    ("R$ 1.000,00", 1000.0),
    ("(320,10)", -320.10),
    ("", 0.0),
    ("-", 0.0),
    (None, 0.0),
    (2000, 2000.0),
    (10.5, 10.5),
])
def test_parse_valor_br(parser, entrada, esperado):
    assert parser._parse_valor_br(entrada) == pytest.approx(esperado)


@pytest.mark.parametrize("doc,tipo,numero", [
    ("NFE.175118", "NFE", "175118"),
    ("NFSE.14727", "NFSE", "14727"),
    ("IPTU.IPTU 2026", "IPTU", "IPTU 2026"),
    ("ADTF.PEDIDO2710", "ADTF", "PEDIDO2710"),
    ("CT.CT R205", "CT", "CT R205"),
    ("SEMPONTO", None, "SEMPONTO"),
])
def test_split_documento(parser, doc, tipo, numero):
    assert parser._split_documento(doc) == (tipo, numero)


@pytest.mark.parametrize("tit_parc,titulo,parcela", [
    ("8674/3", "8674", "3"),
    ("5555/12", "5555", "12"),
    ("9001", "9001", None),
])
def test_split_tit_parc(parser, tit_parc, titulo, parcela):
    assert parser._split_tit_parc(tit_parc) == (titulo, parcela)


# --------------------------------------------------------------------- XLSX

def _gerar_xlsx_amostra(path):
    """Gera um .xlsx mínimo com o layout do Fluxo de Caixa (colunas mescladas/None)."""
    wb = Workbook()
    ws = wb.active
    ws.append(["Fluxo de Caixa Analítico"])
    ws.append(["Grupo de Empresa: TRK"])
    ws.append(["Período: 01/06/2026 a 30/06/2026"])
    ws.append(["Disponível em 01/06/2026", None, None, None, None, None, None, 10000.00])
    ws.append([])
    ws.append(["Data", None, "Documento", None, "Tit/Parc", "Orig.",
               "Cliente/Fornecedor", None, "Entradas", "Saídas", "Saldo"])
    # 3 saídas conferíveis + 1 entrada (crédito) + 2 totais
    ws.append(["01/06/2026", None, "NFE.175118", None, "8674/3", "CP",
               "FORNECEDOR ALPHA LTDA", None, None, "1.234,56", "8.765,44"])
    ws.append(["02/06/2026", None, "NFSE.14727", None, "9001/1", "AC",
               "BETA SERVICOS ME", None, None, "500,00", "8.265,44"])
    ws.append(["03/06/2026", None, "CT.CT R205", None, "7777/2", "CO",
               "CLIENTE GAMA", None, "2.000,00", None, "10.265,44"])
    ws.append(["03/06/2026", None, "IPTU.IPTU 2026", None, "5555/12", "CP",
               "PREFEITURA", None, None, "320,10", "9.945,34"])
    ws.append(["Total do dia 03/06/2026", None, None, None, None, None, None, None,
               "2.000,00", "320,10", None])
    ws.append(["Total do período", None, None, None, None, None, None, None,
               "2.000,00", "2.054,66", None])
    wb.save(path)


def test_parse_xlsx_conferiveis(parser, tmp_path):
    arq = tmp_path / "fluxo.xlsx"
    _gerar_xlsx_amostra(str(arq))

    titulos = parser.parse(str(arq))

    # 3 saídas conferíveis; entrada (CT) e totais ignorados
    assert len(titulos) == 3
    numeros = {t.numero for t in titulos}
    assert numeros == {"8674", "9001", "5555"}
    assert "7777" not in numeros  # entrada (crédito) ignorada


def test_parse_xlsx_campos(parser, tmp_path):
    arq = tmp_path / "fluxo.xlsx"
    _gerar_xlsx_amostra(str(arq))
    titulos = parser.parse(str(arq))
    por_num = {t.numero: t for t in titulos}

    alpha = por_num["8674"]
    assert alpha.tipo_documento == "NFE"
    assert alpha.numero_documento == "175118"
    assert alpha.parcela == "3"
    assert alpha.origem == "CP"
    assert alpha.fornecedor_nome == "FORNECEDOR ALPHA LTDA"
    assert alpha.valor_liquido == pytest.approx(1234.56)
    assert alpha.data_vencimento == date(2026, 6, 1)

    iptu = por_num["5555"]
    assert iptu.tipo_documento == "IPTU"
    assert iptu.numero_documento == "IPTU 2026"
    assert iptu.parcela == "12"


# ---------------------------------------------------------------- PDF (texto)

def test_parse_pdf_text_fallback(parser):
    texto = "\n".join([
        "Fluxo de Caixa Analítico",
        "Disponível em 01/06/2026 10.000,00",
        "Data Documento Tit/Parc Orig. Cliente/Fornecedor Entradas Saídas Saldo",
        "01/06/2026 NFE.175118 8674/3 CP FORNECEDOR ALPHA LTDA 1.234,56 8.765,44",
        "02/06/2026 NFSE.14727 9001/1 AC BETA SERVICOS ME 500,00 8.265,44",
        "03/06/2026 CT.CT 7777/2 CO CLIENTE GAMA 2.000,00 10.265,44",
        "Total do período 2.000,00 1.734,56",
    ])

    titulos = parser._parse_pdf_text(texto)

    # As 2 saídas entram; a entrada (saldo sobe) é ignorada; total ignorado
    numeros = {t.numero for t in titulos}
    assert numeros == {"8674", "9001"}
    alpha = next(t for t in titulos if t.numero == "8674")
    assert alpha.tipo_documento == "NFE"
    assert alpha.valor_liquido == pytest.approx(1234.56)


def test_parse_extensao_invalida(parser):
    with pytest.raises(ValueError):
        parser.parse("relatorio.txt")


# ----------------------------------------------- boleto: linha digitável FEBRABAN

def test_decodificar_linha_digitavel_valor_e_vencimento():
    # fator em ld[33:37]="1000", valor em ld[37:47]="0000002000" -> R$ 20,00
    ld = "0" * 33 + "1000" + "0000002000"
    assert len(ld) == 47
    out = decodificar_linha_digitavel(ld)
    assert out["valor"] == pytest.approx(20.0)
    # Reset FEBRABAN: fator 1000 = 22/02/2025 (novo ciclo, +9000 dias sobre a base antiga)
    assert out["vencimento"] == date(2025, 2, 22)


def test_decodificar_linha_digitavel_concessionaria_retorna_none():
    # 48 dígitos (conta de concessionária) -> não é boleto bancário
    assert decodificar_linha_digitavel("8" * 48) is None
    assert decodificar_linha_digitavel("123") is None
    assert decodificar_linha_digitavel("") is None
