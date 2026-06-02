from dataclasses import dataclass
from typing import Optional, List
from datetime import timedelta
import re
from loguru import logger

from models import Titulo, NFeData, Boleto

@dataclass
class Divergencia:
    titulo_id: int
    titulo_numero: str
    tipo: str            # CNPJ_DIVERGENTE | VALOR_DIVERGENTE | VENCIMENTO_DIVERGENTE | FORMA_PAGAMENTO_INCOMPATIVEL | BOLETO_NAO_ENCONTRADO | CHAVE_NFE_INVALIDA | ANEXO_ILEGIVEL | SEM_ANEXO
    campo: str           # qual campo divergiu
    valor_sienge: str    # o que o Sienge diz
    valor_nfe: str       # o que a NF-e diz
    valor_boleto: str    # o que o boleto diz (se aplicável)
    criticidade: str     # CRITICA | ATENCAO | INFO
    danfe_path: Optional[str] = None

class Reconciler:
    def _limpar_cnpj(self, cnpj: str) -> str:
        if not cnpj:
            return ""
        return re.sub(r'[^0-9]', '', str(cnpj))

    def _validar_chave_nfe(self, chave: str) -> bool:
        if not chave or len(chave) != 44 or not chave.isdigit():
            return False
            
        pesos = [2, 3, 4, 5, 6, 7, 8, 9] * 6
        pesos = pesos[:43]
        soma = 0
        for i, peso in enumerate(pesos):
            digito = int(chave[42 - i])
            soma += digito * peso
            
        resto = soma % 11
        digito_esperado = 0 if resto in (0, 1) else 11 - resto
        return str(digito_esperado) == chave[43]

    def reconciliar(
        self,
        titulo: Titulo,
        nfe_data: Optional[NFeData],
        boletos: List[Boleto]
    ) -> List[Divergencia]:
        divergencias = []
        
        # REGRA 5 - SEM ANEXO
        if titulo.attachment_bytes is None and not titulo.attachment_url:
            divergencias.append(Divergencia(
                titulo_id=titulo.id,
                titulo_numero=titulo.numero,
                tipo="SEM_ANEXO",
                campo="Anexo",
                valor_sienge="Nenhum",
                valor_nfe="-",
                valor_boleto="-",
                criticidade="ATENCAO",
                danfe_path=None
            ))
            # Se não tem anexo, não tem chave nem nfe, só podemos checar boleto se for o caso
            
        # REGRA 6 - ANEXO ILEGÍVEL
        if (titulo.attachment_bytes or titulo.attachment_url) and not titulo.chave_nfe:
            divergencias.append(Divergencia(
                titulo_id=titulo.id,
                titulo_numero=titulo.numero,
                tipo="ANEXO_ILEGIVEL",
                campo="Chave NF-e",
                valor_sienge="-",
                valor_nfe="-",
                valor_boleto="-",
                criticidade="ATENCAO",
                danfe_path=None
            ))
            
        # REGRA 7 - CHAVE INVÁLIDA
        if titulo.chave_nfe and not self._validar_chave_nfe(titulo.chave_nfe):
            divergencias.append(Divergencia(
                titulo_id=titulo.id,
                titulo_numero=titulo.numero,
                tipo="CHAVE_NFE_INVALIDA",
                campo="Chave NF-e",
                valor_sienge=titulo.chave_nfe,
                valor_nfe="-",
                valor_boleto="-",
                criticidade="CRITICA",
                danfe_path=titulo.danfe_path
            ))

        if nfe_data:
            # REGRA 1 - CNPJ
            cnpj_sienge = self._limpar_cnpj(titulo.fornecedor_cnpj)
            cnpj_nfe = self._limpar_cnpj(nfe_data.cnpj_emitente)
            if cnpj_sienge and cnpj_nfe and cnpj_sienge != cnpj_nfe:
                divergencias.append(Divergencia(
                    titulo_id=titulo.id,
                    titulo_numero=titulo.numero,
                    tipo="CNPJ_DIVERGENTE",
                    campo="CNPJ",
                    valor_sienge=cnpj_sienge,
                    valor_nfe=cnpj_nfe,
                    valor_boleto="-",
                    criticidade="CRITICA",
                    danfe_path=titulo.danfe_path or nfe_data.danfe_path
                ))
                
            # REGRA 2 - VALOR
            diff = abs(titulo.valor_liquido - nfe_data.valor_liquido)
            if diff > 0.05:
                divergencias.append(Divergencia(
                    titulo_id=titulo.id,
                    titulo_numero=titulo.numero,
                    tipo="VALOR_DIVERGENTE",
                    campo="Valor Líquido",
                    valor_sienge=f"{titulo.valor_liquido:.2f}",
                    valor_nfe=f"{nfe_data.valor_liquido:.2f}",
                    valor_boleto="-",
                    criticidade="CRITICA",
                    danfe_path=titulo.danfe_path or nfe_data.danfe_path
                ))

        # Encontrar boleto correspondente
        boleto_encontrado = None
        cnpj_s = self._limpar_cnpj(titulo.fornecedor_cnpj)
        
        for b in boletos:
            cnpj_b = self._limpar_cnpj(b.cnpj_beneficiario)
            if cnpj_b == cnpj_s:
                if abs(b.valor - titulo.valor_liquido) <= 0.05:
                    if abs((b.data_vencimento - titulo.data_vencimento).days) <= 1:
                        boleto_encontrado = b
                        break
                        
        # REGRA 4 - FORMA DE PAGAMENTO (Boleto)
        if titulo.forma_pagamento and "BOLETO" in titulo.forma_pagamento.upper():
            if not boleto_encontrado:
                divergencias.append(Divergencia(
                    titulo_id=titulo.id,
                    titulo_numero=titulo.numero,
                    tipo="BOLETO_NAO_ENCONTRADO",
                    campo="Boleto",
                    valor_sienge="Exige Boleto",
                    valor_nfe="-",
                    valor_boleto="Nenhum boleto DDA bate com CNPJ, Valor e Venc.",
                    criticidade="CRITICA",
                    danfe_path=titulo.danfe_path
                ))
            else:
                # REGRA 3 - VENCIMENTO (Sienge x Boleto)
                if titulo.data_vencimento != boleto_encontrado.data_vencimento:
                    divergencias.append(Divergencia(
                        titulo_id=titulo.id,
                        titulo_numero=titulo.numero,
                        tipo="VENCIMENTO_DIVERGENTE",
                        campo="Vencimento",
                        valor_sienge=str(titulo.data_vencimento),
                        valor_nfe="-",
                        valor_boleto=str(boleto_encontrado.data_vencimento),
                        criticidade="ATENCAO",
                        danfe_path=titulo.danfe_path
                    ))
                    
        return divergencias
