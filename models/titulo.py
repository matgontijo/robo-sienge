from dataclasses import dataclass, field
from datetime import date
from typing import Optional

@dataclass
class Titulo:
    id: int
    numero: str
    fornecedor_nome: str
    fornecedor_cnpj: str
    valor_nominal: float
    valor_liquido: float
    data_vencimento: date
    forma_pagamento: str          # BOLETO, PIX, TED, etc.
    status: str
    attachment_url: Optional[str] = None
    attachment_bytes: Optional[bytes] = None
    chave_nfe: Optional[str] = None
    nfe_xml: Optional[str] = None
    danfe_path: Optional[str] = None

    # --- Campos vindos do relatório de Fluxo de Caixa Analítico (ReportParser) ---
    documento: Optional[str] = None           # coluna "Documento" crua, ex.: "NFE.175118"
    tipo_documento: Optional[str] = None       # antes do primeiro ".", ex.: "NFE"
    numero_documento: Optional[str] = None     # depois do primeiro ".", ex.: "175118"
    parcela: Optional[str] = None              # parte depois da "/" em Tit/Parc, ex.: "3"
    origem: Optional[str] = None               # coluna "Orig.": AC | CP | ME | CO
    data_referencia: Optional[date] = None     # data da linha no relatório (= data_vencimento)

    # --- Cadastro do título no Sienge ---
    credor_id: Optional[int] = None            # creditorId do /bills — referência anti-fraude

    # --- Campos da NF resolvidos pela API do Sienge (SiengeClient) ---
    nf_numero: Optional[str] = None
    nf_cnpj_emitente: Optional[str] = None
    nf_valor: Optional[float] = None
    nf_chave_api: Optional[str] = None
