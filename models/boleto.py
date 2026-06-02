from dataclasses import dataclass
from datetime import date
from typing import Optional

@dataclass
class Boleto:
    codigo_barras: str
    cnpj_beneficiario: str
    nome_beneficiario: str
    valor: float
    data_vencimento: date
    nosso_numero: Optional[str] = None
    banco_emissor: Optional[str] = None
