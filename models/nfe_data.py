from dataclasses import dataclass
from datetime import date
from typing import Optional

@dataclass
class NFeData:
    chave: str
    cnpj_emitente: str
    nome_emitente: str
    valor_total: float
    valor_liquido: float
    data_emissao: date
    numero_nfe: str
    serie: str
    xml_path: Optional[str] = None
    danfe_path: Optional[str] = None
