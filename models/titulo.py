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
