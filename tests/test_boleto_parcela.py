"""Cruzamento boleto <-> parcela: o anexo do Sienge fica preso ao titulo, entao
um titulo em 12x traz os 12 boletos juntos. Estes testes cobrem como o robo
decide qual deles e o da parcela que esta sendo paga."""
from datetime import date

import pytest

from models import Titulo
from models.info_pagamento import InfoPagamento
from orchestrator import _boleto_da_parcela, _montar_dossie


class ReaderFake:
    """Devolve a linha digitavel que o teste associou a cada conteudo."""

    def __init__(self, por_bytes=None):
        self.por_bytes = por_bytes or {}

    def extrair_info_texto(self, conteudo):
        return {"linha_digitavel": self.por_bytes.get(conteudo)}


# Linhas digitaveis reais o bastante para o decodificador FEBRABAN:
# posicoes 33-36 = fator de vencimento, 37-47 = valor em centavos.
def _linha(fator, centavos):
    """Monta uma linha digitavel de 47 digitos com fator e valor dados."""
    base = "2379" + "1" * 29          # 33 primeiros digitos (banco, campos 1-3)
    return base + f"{fator:04d}" + f"{centavos:010d}"


def _fator(venc):
    """Fator de vencimento FEBRABAN. O contador reiniciou em 22/02/2025, entao
    datas do ciclo atual sao gravadas com 9000 dias a menos (o decodificador
    devolve os 9000 ao ler)."""
    f = (venc - date(1997, 10, 7)).days
    return f - 9000 if f > 9999 else f


# Vencimentos no ciclo FEBRABAN atual (pos 22/02/2025), como num boleto de hoje
VENC_A, VALOR_A = date(2026, 8, 20), 1500.00
VENC_B, VALOR_B = date(2026, 9, 20), 1500.00


LINHA_A = _linha(_fator(VENC_A), int(VALOR_A * 100))
LINHA_B = _linha(_fator(VENC_B), int(VALOR_B * 100))


def _titulo(parcela="2", venc=VENC_A, valor=VALOR_A):
    return Titulo(id=1, numero="8674", fornecedor_nome="Fornecedor X",
                  fornecedor_cnpj="12345678000190", valor_nominal=valor,
                  valor_liquido=valor, data_vencimento=venc,
                  forma_pagamento="BOLETO", status=None, parcela=parcela)


def _anexos(*pares):
    """pares = (nome, conteudo, linha_digitavel_ja_lida)"""
    return {"anexos": [{"nome": n, "path": f"/tmp/{n}", "bytes": c, "_linha_digitavel": l}
                       for n, c, l in pares]}


def test_escolhe_pela_linha_digitavel_cadastrada_na_parcela():
    anexos = _anexos(("bol_p1.pdf", b"1", LINHA_A), ("bol_p2.pdf", b"2", LINHA_B))
    info = InfoPagamento(linha_digitavel=LINHA_B, vencimento=None, valor=None)
    r = _boleto_da_parcela(ReaderFake(), anexos, _titulo(), info)
    assert r["situacao"] == "identificado"
    assert r["nome"] == "bol_p2.pdf"
    assert r["criterio"] == "linha digitável da parcela"
    assert r["confiavel"] is True


def test_escolhe_por_vencimento_e_valor_quando_nao_ha_linha_cadastrada():
    anexos = _anexos(("bol_p1.pdf", b"1", LINHA_A), ("bol_p2.pdf", b"2", LINHA_B))
    info = InfoPagamento(vencimento=VENC_B, valor=VALOR_B)
    r = _boleto_da_parcela(ReaderFake(), anexos, _titulo(), info)
    assert r["situacao"] == "identificado"
    assert r["nome"] == "bol_p2.pdf"
    assert r["criterio"] == "vencimento e valor do boleto"


def test_nao_escolhe_boleto_de_outra_parcela():
    """A parcela vence em julho; o unico boleto anexado e o de junho."""
    anexos = _anexos(("bol_junho.pdf", b"1", LINHA_A), ("bol_junho2.pdf", b"2", LINHA_A))
    info = InfoPagamento(vencimento=VENC_B, valor=VALOR_B)
    r = _boleto_da_parcela(ReaderFake(), anexos, _titulo(), info)
    assert r["situacao"] == "ambiguo"
    assert r["path"] is None


def test_sem_boleto_entre_os_anexos():
    """Pagamento em Pix/TED: nao ha boleto, e isso nao e duvida."""
    anexos = _anexos(("nota.pdf", b"1", None), ("medicao.pdf", b"2", None))
    r = _boleto_da_parcela(ReaderFake(), anexos, _titulo(), InfoPagamento())
    assert r["situacao"] == "sem_boleto"
    assert r["path"] is None


def test_boleto_unico_entra_mas_marcado_como_nao_confirmado():
    anexos = _anexos(("nota.pdf", b"1", None), ("boleto.pdf", b"2", LINHA_A))
    info = InfoPagamento(vencimento=VENC_B, valor=999.99)   # nao casa com nada
    r = _boleto_da_parcela(ReaderFake(), anexos, _titulo(), info)
    assert r["situacao"] == "identificado"
    assert r["confiavel"] is False
    assert r["criterio"] == "único boleto anexado"


def test_le_o_pdf_quando_a_linha_ainda_nao_foi_extraida():
    """Fluxo sem relatorio: os anexos nao passaram por _ler_todos_anexos."""
    anexos = {"anexos": [{"nome": "b.pdf", "path": "/tmp/b.pdf", "bytes": b"z"}]}
    reader = ReaderFake({b"z": LINHA_A})
    info = InfoPagamento(vencimento=VENC_A, valor=VALOR_A)
    r = _boleto_da_parcela(reader, anexos, _titulo(), info)
    assert r["situacao"] == "identificado"
    assert r["criterio"] == "vencimento e valor do boleto"


# ----------------------------------------------------------------------------
# Dossie: o que a pasta do ciclo recebe em cada situacao
# ----------------------------------------------------------------------------

def _resultado(tmp_path, escolha, nomes=("nota.pdf", "bol_p1.pdf", "bol_p2.pdf", "medicao.pdf")):
    caminhos = []
    for n in nomes:
        p = tmp_path / n
        p.write_bytes(b"%PDF-1.4 " + n.encode())
        caminhos.append(str(p))
    return {
        "titulo": _titulo(),
        "info_pagamento": None,
        "anexos_info": {
            "pasta": str(tmp_path),
            "nf_path": caminhos[0],
            "boleto_path": caminhos[1],
            "arquivos": [{"nome": n, "path": c, "tipo": None} for n, c in zip(nomes, caminhos)],
            "boleto_parcela": (dict(escolha, path=caminhos[2])
                               if escolha and escolha.get("situacao") == "identificado"
                               else escolha),
        },
    }, caminhos


def test_dossie_leva_so_nf_e_boleto_da_parcela(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    escolha = {"situacao": "identificado", "nome": "bol_p2.pdf",
               "criterio": "linha digitável da parcela", "confiavel": True}
    r, _ = _resultado(tmp_path, escolha)

    rows = _montar_dossie(901, [r])

    assert rows[0]["total_copiados"] == 2, "deveria copiar so a NF e o boleto da parcela"
    copiados = " ".join(rows[0]["arquivos"])
    assert "nota" in copiados and "bol_p2" in copiados
    assert "medicao" not in copiados and "bol_p1" not in copiados
    assert rows[0]["boleto_criterio"] == "linha digitável da parcela"


def test_dossie_leva_tudo_quando_a_parcela_nao_foi_identificada(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    escolha = {"situacao": "ambiguo", "path": None, "nome": None,
               "criterio": "2 boletos, nenhum atribuível à parcela", "confiavel": False}
    r, _ = _resultado(tmp_path, escolha)

    rows = _montar_dossie(902, [r])

    assert rows[0]["total_copiados"] == 4, "na duvida, leva todos os anexos"
    assert rows[0]["boleto"] == "CONFERIR"


def test_dossie_sem_boleto_leva_so_a_nota(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path / "out"))
    escolha = {"situacao": "sem_boleto", "path": None, "nome": None,
               "criterio": "nenhum boleto anexado", "confiavel": True}
    r, _ = _resultado(tmp_path, escolha)

    rows = _montar_dossie(903, [r])

    assert rows[0]["total_copiados"] == 1
    assert "nota" in rows[0]["arquivos"][0]
