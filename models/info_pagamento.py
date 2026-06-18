from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class InfoPagamento:
    """
    Dados de pagamento cadastrados no título (aba "Inf. Pagamento" do Sienge),
    geralmente digitados na hora por parcela. É o "destino" que o robô confronta
    com o CNPJ do fornecedor para detectar desvio.
    """
    forma_pagamento: str = ""                      # ex.: "Boleto Bancário Outros Bancos", "PIX", "TED"
    parcela: Optional[str] = None
    valor: Optional[float] = None
    vencimento: Optional[date] = None

    # Destino bancário (TED/depósito)
    banco: Optional[str] = None
    agencia: Optional[str] = None
    conta: Optional[str] = None
    titular_nome: Optional[str] = None
    titular_cnpj: Optional[str] = None

    # PIX
    chave_pix: Optional[str] = None
    tipo_chave_pix: Optional[str] = None           # CNPJ | CPF | EMAIL | TELEFONE | ALEATORIA

    # Boleto
    linha_digitavel: Optional[str] = None
    beneficiario_nome: Optional[str] = None
    beneficiario_cnpj: Optional[str] = None

    def cnpj_destino(self) -> Optional[str]:
        """Retorna o CNPJ do destino do pagamento, quando identificável."""
        if self.beneficiario_cnpj:
            return self.beneficiario_cnpj
        if self.titular_cnpj:
            return self.titular_cnpj
        if self.tipo_chave_pix and self.tipo_chave_pix.upper() == "CNPJ" and self.chave_pix:
            return self.chave_pix
        return None
