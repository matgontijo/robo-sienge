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
        boletos: List[Boleto],
        boleto_anexo: Optional[Boleto] = None,
        info_pagamento=None,
        impostos_destacados: Optional[dict] = None,
        retencoes: Optional[dict] = None,
        dda_disponivel: bool = True,
        ocr_disponivel: bool = True
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
            
        # REGRA 6 - ANEXO ILEGÍVEL — só quando o OCR está ativo (sem OCR, a leitura
        # do anexo não foi tentada; marcar tudo como "ilegível" seria ruído)
        if ocr_disponivel and (titulo.attachment_bytes or titulo.attachment_url) and not titulo.chave_nfe:
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
                        
        # REGRA 4 - FORMA DE PAGAMENTO (Boleto) — só quando o DDA está configurado;
        # sem Santander, "nenhum boleto DDA encontrado" é o esperado, não divergência
        if dda_disponivel and titulo.forma_pagamento and "BOLETO" in titulo.forma_pagamento.upper():
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

        # CRUZAMENTO DO BOLETO ENCONTRADO NO ANEXO DO TÍTULO
        # (independe do DDA; valida valor, vencimento e CNPJ do beneficiário)
        if boleto_anexo is not None:
            divergencias.extend(self._reconciliar_boleto_anexo(titulo, nfe_data, boleto_anexo))

        # CONFERÊNCIA DOS DADOS DE PAGAMENTO (anti-fraude: destino x fornecedor)
        if info_pagamento is not None:
            divergencias.extend(
                self._reconciliar_pagamento(titulo, nfe_data, info_pagamento, boleto_anexo))

        # CONFERÊNCIA DE IMPOSTOS / RETENÇÕES
        if impostos_destacados or retencoes:
            divergencias.extend(
                self._reconciliar_impostos(titulo, nfe_data, impostos_destacados or {}, retencoes or {}))

        return divergencias

    def _cnpj_fornecedor_ref(self, titulo: Titulo, nfe_data: Optional[NFeData]) -> str:
        """CNPJ de referência do fornecedor (título -> NF)."""
        return self._limpar_cnpj(titulo.fornecedor_cnpj) or (
            self._limpar_cnpj(nfe_data.cnpj_emitente) if nfe_data else "")

    def _reconciliar_pagamento(self, titulo, nfe_data, info, boleto_anexo) -> List[Divergencia]:
        """
        Confere os dados de pagamento cadastrados no título contra o fornecedor real.
        Regra-mãe: o destino do pagamento (beneficiário do boleto / chave PIX-CNPJ /
        titular da conta TED) tem que ser o MESMO CNPJ do fornecedor da nota.
        """
        divs: List[Divergencia] = []
        cnpj_ref = self._cnpj_fornecedor_ref(titulo, nfe_data)
        forma = (info.forma_pagamento or "").upper()

        cnpj_destino = self._limpar_cnpj(info.cnpj_destino() or "")
        # Se o boleto do anexo trouxe beneficiário e a info não, usa o do boleto
        if not cnpj_destino and boleto_anexo:
            cnpj_destino = self._limpar_cnpj(boleto_anexo.cnpj_beneficiario)

        # 1) Destino x fornecedor (CRÍTICA)
        if cnpj_destino and cnpj_ref and cnpj_destino != cnpj_ref:
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="PAGAMENTO_DESTINO_DIVERGENTE",
                campo=f"Destino do Pagamento ({forma or 'N/D'})",
                valor_sienge=cnpj_ref, valor_nfe="-", valor_boleto=cnpj_destino,
                criticidade="CRITICA", danfe_path=titulo.danfe_path,
            ))

        # 2) PIX com chave não-CNPJ: não dá pra confirmar o titular -> conferência manual
        if "PIX" in forma and info.tipo_chave_pix and info.tipo_chave_pix.upper() != "CNPJ":
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="PIX_NAO_VERIFICAVEL",
                campo="Chave PIX",
                valor_sienge=f"{info.tipo_chave_pix}: {info.chave_pix or '-'}",
                valor_nfe="-", valor_boleto="Conferir titular manualmente",
                criticidade="ATENCAO", danfe_path=titulo.danfe_path,
            ))

        # 3) Forma "BOLETO" sem boleto identificado no anexo (ATENÇÃO)
        if "BOLETO" in forma and boleto_anexo is None and not info.linha_digitavel:
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="PAGAMENTO_FORMA_INCOMPATIVEL",
                campo="Forma de Pagamento",
                valor_sienge="BOLETO cadastrado", valor_nfe="-",
                valor_boleto="Nenhum boleto/linha digitável encontrado",
                criticidade="ATENCAO", danfe_path=titulo.danfe_path,
            ))
        return divs

    def _reconciliar_impostos(self, titulo, nfe_data, destacados: dict, retencoes: dict) -> List[Divergencia]:
        """
        Confere impostos destacados (nota) e retenções (título), e líquido x bruto.
        Trabalha de forma tolerante: só aponta quando há dados dos dois lados.
        """
        divs: List[Divergencia] = []

        # Impostos destacados x lançados (mesma chave de tributo)
        for tributo in set(destacados) & set(retencoes):
            v_nota = float(destacados.get(tributo) or 0)
            v_tit = float(retencoes.get(tributo) or 0)
            if abs(v_nota - v_tit) > 0.05:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="IMPOSTO_DIVERGENTE", campo=f"Imposto/Retenção {tributo}",
                    valor_sienge=f"{v_tit:.2f}", valor_nfe=f"{v_nota:.2f}", valor_boleto="-",
                    criticidade="CRITICA", danfe_path=titulo.danfe_path,
                ))

        # Líquido x Bruto: valor a pagar ~ valor da nota - retenções
        # (nfe_data derivado só da chave de acesso não tem valor — pula o check)
        if nfe_data and nfe_data.valor_total and retencoes:
            total_retido = sum(float(v or 0) for v in retencoes.values())
            liquido_esperado = nfe_data.valor_total - total_retido
            if abs(liquido_esperado - titulo.valor_liquido) > 0.05:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="LIQUIDO_BRUTO_DIVERGENTE", campo="Líquido x Bruto",
                    valor_sienge=f"{titulo.valor_liquido:.2f}",
                    valor_nfe=f"{liquido_esperado:.2f} (nota {nfe_data.valor_total:.2f} - ret. {total_retido:.2f})",
                    valor_boleto="-", criticidade="CRITICA", danfe_path=titulo.danfe_path,
                ))
        return divs

    def _reconciliar_boleto_anexo(
        self,
        titulo: Titulo,
        nfe_data: Optional[NFeData],
        boleto: Boleto
    ) -> List[Divergencia]:
        divs: List[Divergencia] = []

        # VALOR: boleto x título (e x NF quando houver)
        if boleto.valor and abs(boleto.valor - titulo.valor_liquido) > 0.05:
            divs.append(Divergencia(
                titulo_id=titulo.id,
                titulo_numero=titulo.numero,
                tipo="BOLETO_VALOR_DIVERGENTE",
                campo="Valor (Boleto x Título)",
                valor_sienge=f"{titulo.valor_liquido:.2f}",
                valor_nfe=f"{nfe_data.valor_liquido:.2f}" if nfe_data else "-",
                valor_boleto=f"{boleto.valor:.2f}",
                criticidade="CRITICA",
                danfe_path=titulo.danfe_path,
            ))

        # VENCIMENTO: boleto x título (tolerância de 1 dia)
        if boleto.data_vencimento and titulo.data_vencimento:
            if abs((boleto.data_vencimento - titulo.data_vencimento).days) > 1:
                divs.append(Divergencia(
                    titulo_id=titulo.id,
                    titulo_numero=titulo.numero,
                    tipo="BOLETO_VENCIMENTO_DIVERGENTE",
                    campo="Vencimento (Boleto x Título)",
                    valor_sienge=str(titulo.data_vencimento),
                    valor_nfe="-",
                    valor_boleto=str(boleto.data_vencimento),
                    criticidade="ATENCAO",
                    danfe_path=titulo.danfe_path,
                ))

        # CNPJ do beneficiário do boleto x CNPJ do fornecedor (título/NF)
        cnpj_boleto = self._limpar_cnpj(boleto.cnpj_beneficiario)
        cnpj_ref = self._limpar_cnpj(titulo.fornecedor_cnpj) or (
            self._limpar_cnpj(nfe_data.cnpj_emitente) if nfe_data else "")
        if cnpj_boleto and cnpj_ref and cnpj_boleto != cnpj_ref:
            divs.append(Divergencia(
                titulo_id=titulo.id,
                titulo_numero=titulo.numero,
                tipo="BOLETO_CNPJ_DIVERGENTE",
                campo="CNPJ Beneficiário (Boleto)",
                valor_sienge=cnpj_ref,
                valor_nfe=self._limpar_cnpj(nfe_data.cnpj_emitente) if nfe_data else "-",
                valor_boleto=cnpj_boleto,
                criticidade="CRITICA",
                danfe_path=titulo.danfe_path,
            ))

        return divs
