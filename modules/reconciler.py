from dataclasses import dataclass
from typing import Optional, List
from datetime import timedelta
import re
from loguru import logger

from models import Titulo, NFeData, Boleto
from modules.attachment_reader import decodificar_linha_digitavel

# Bancos mais comuns (código FEBRABAN -> nome) p/ mensagens amigáveis
_BANCOS = {"001": "Banco do Brasil", "033": "Santander", "104": "Caixa",
           "237": "Bradesco", "341": "Itaú", "756": "Sicoob", "748": "Sicredi",
           "077": "Inter", "260": "Nubank", "336": "C6", "212": "Original",
           "422": "Safra", "745": "Citibank", "399": "HSBC", "041": "Banrisul"}

# Código do banco de onde saem os pagamentos (boleto "próprio banco")
BANCO_CASA = "033"  # Santander

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
        ocr_disponivel: bool = True,
        nf_texto: Optional[dict] = None
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
                
            # REGRA 2 - VALOR (só quando a NF tem valor conhecido; NFeData derivado
            # apenas da chave de acesso não carrega valor)
            diff = abs(titulo.valor_liquido - nfe_data.valor_liquido)
            if nfe_data.valor_liquido and diff > 0.05:
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

        # REGRA 8 - SEM FORMA DE PAGAMENTO (parcela não entra na remessa)
        if info_pagamento is not None and not (info_pagamento.forma_pagamento or "").strip():
            divergencias.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="FORMA_PAGAMENTO_AUSENTE",
                campo="Forma de Pagamento",
                valor_sienge="(vazio)",
                valor_nfe="-",
                valor_boleto="Cadastrar a forma de pagamento da parcela no Sienge",
                criticidade="CRITICA", danfe_path=titulo.danfe_path,
            ))

        # CONFERÊNCIA DOS DADOS DE PAGAMENTO (anti-fraude: destino x fornecedor)
        if info_pagamento is not None:
            divergencias.extend(
                self._reconciliar_pagamento(titulo, nfe_data, info_pagamento, boleto_anexo))
            divergencias.extend(
                self._reconciliar_linha_digitavel(titulo, info_pagamento))
            divergencias.extend(
                self._reconciliar_banco_transferencia(titulo, info_pagamento))
            divergencias.extend(
                self._reconciliar_liquido_parcela(titulo, info_pagamento, retencoes or {}))

        # REGRA 9 - CNPJ DO CREDOR DENTRO DA NF ANEXADA (camada de texto, sem OCR)
        # Se o PDF da NF tem texto e o CNPJ do credor não aparece em lugar nenhum
        # dele (busca no fluxo de dígitos, imune a formatação), a nota anexada
        # pode ser de outro fornecedor — conferência manual.
        # (só para documentos que são NF de fato — ADTF/pedidos não têm nota)
        if (nf_texto and nf_texto.get("tem_texto") and nf_texto.get("texto_confiavel")
                and titulo.fornecedor_cnpj and "NF" in (titulo.tipo_documento or "").upper()):
            # raiz do CNPJ (8 dígitos): identifica a empresa e sobrevive a
            # quebras de formatação no PDF
            cnpj_credor = self._limpar_cnpj(titulo.fornecedor_cnpj)[:8]
            if len(cnpj_credor) == 8 and cnpj_credor not in (nf_texto.get("digitos") or ""):
                divergencias.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="NF_SEM_CNPJ_DO_CREDOR",
                    campo="CNPJ no documento anexado",
                    valor_sienge=f"Credor: {titulo.fornecedor_cnpj}",
                    valor_nfe=f"CNPJs no PDF: {', '.join((nf_texto.get('cnpjs') or [])[:4]) or 'nenhum'}",
                    valor_boleto="Conferir se a NF anexada é do fornecedor certo",
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))

        # CONFERÊNCIA DE IMPOSTOS / RETENÇÕES
        if impostos_destacados or retencoes:
            divergencias.extend(
                self._reconciliar_impostos(titulo, nfe_data, impostos_destacados or {}, retencoes or {}))

        # IMPOSTOS x TEXTO DA NF + SANIDADE DE ALÍQUOTAS
        divergencias.extend(
            self._reconciliar_impostos_nf(titulo, retencoes or {}, nf_texto))
        if info_pagamento is not None:
            divergencias.extend(
                self._reconciliar_aliquotas(titulo, info_pagamento, retencoes or {}))

        return divergencias

    # Nome canônico dos tributos lançados no Sienge (taxId livre, ex. "INSS 2631")
    @staticmethod
    def _tributo_canonico(nome: str) -> Optional[str]:
        n = (nome or "").upper()
        if "INSS" in n: return "INSS"
        if "ISS" in n: return "ISS"
        if "CAUC" in n or "CAUÇ" in n: return "CAUCAO"
        if "COFINS" in n: return "COFINS"
        if "CSLL" in n: return "CSLL"
        if "PIS" in n: return "PIS"
        if n.startswith("IR") or " IR" in n or "IRRF" in n: return "IR"
        return None

    def _reconciliar_impostos_nf(self, titulo: Titulo, retencoes: dict, nf_texto) -> List[Divergencia]:
        """
        Impostos escritos na própria NF (camada de texto) x retenções do título:
          - mesmo tributo com valores diferentes -> IMPOSTO_NF_DIVERGENTE
          - nota destaca retenção que o título NÃO lançou -> IMPOSTO_NAO_RETIDO
        """
        divs: List[Divergencia] = []
        if not nf_texto or not nf_texto.get("texto_confiavel"):
            return divs
        impostos_nf = nf_texto.get("impostos") or {}
        na_nota = {k: v for k, v in impostos_nf.items()
                   if k not in ("VALOR_LIQUIDO", "_com_retencao")}
        com_retencao = set(impostos_nf.get("_com_retencao") or [])
        if not na_nota:
            return divs

        no_titulo = {}
        for nome, valor in (retencoes or {}).items():
            can = self._tributo_canonico(nome)
            if can:
                no_titulo[can] = no_titulo.get(can, 0.0) + float(valor or 0)

        for tributo, v_nota in na_nota.items():
            v_tit = no_titulo.get(tributo)
            if v_tit is None:
                # nota destacou e o título não reteve nada desse tributo.
                # ISS só conta quando a nota diz explicitamente "retido/retenção"
                # (NFS-e sempre exibe o ISS devido, mesmo sem retenção pelo tomador)
                if tributo == "ISS" and "ISS" not in com_retencao:
                    continue
                if v_nota > 1.0 and tributo in ("INSS", "ISS", "IR", "PIS", "COFINS", "CSLL"):
                    divs.append(Divergencia(
                        titulo_id=titulo.id, titulo_numero=titulo.numero,
                        tipo="IMPOSTO_NAO_RETIDO",
                        campo=f"Retenção {tributo}",
                        valor_sienge="não lançado no título",
                        valor_nfe=f"{v_nota:.2f} (destacado na nota)",
                        valor_boleto="Conferir se a retenção deveria ter sido lançada",
                        criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                    ))
            elif abs(v_tit - v_nota) > 0.05:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="IMPOSTO_NF_DIVERGENTE",
                    campo=f"Retenção {tributo}",
                    valor_sienge=f"{v_tit:.2f} (título)",
                    valor_nfe=f"{v_nota:.2f} (nota)",
                    valor_boleto="-",
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))
        return divs

    # Tetos de retenção sobre a PARCELA bruta, com folga de ~1,5x sobre a
    # alíquota legal — a parcela pode ser menor que a nota (medições parciais),
    # o que infla a alíquota implícita. O alvo é erro grosseiro de digitação
    # (um dígito a mais = 10x), não a variação normal.
    _TETO_ALIQUOTA = {"INSS": 0.165, "ISS": 0.08, "IR": 0.075,
                      "PIS": 0.10, "COFINS": 0.12, "CSLL": 0.075, "CAUCAO": 0.16}

    def _reconciliar_aliquotas(self, titulo: Titulo, info, retencoes: dict) -> List[Divergencia]:
        """
        Sanidade das retenções: alíquota implícita (retenção / parcela bruta)
        acima do teto usual do tributo = provável erro de digitação no valor.
        Só para títulos de parcela única (base inequívoca).
        """
        divs: List[Divergencia] = []
        base = float(info.valor or 0)
        if not base or (info.total_parcelas and info.total_parcelas > 1):
            return divs
        for nome, valor in (retencoes or {}).items():
            v = float(valor or 0)
            if v <= 0:
                continue
            can = self._tributo_canonico(nome) or "OUTRO"
            teto = self._TETO_ALIQUOTA.get(can, 0.25)
            aliquota = v / base
            if aliquota > teto:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="RETENCAO_ALIQUOTA_SUSPEITA",
                    campo=f"Retenção {nome}",
                    valor_sienge=f"{v:.2f} = {aliquota * 100:.1f}% da parcela {base:.2f}",
                    valor_nfe=f"teto usual {teto * 100:.1f}%",
                    valor_boleto="Conferir o valor lançado da retenção",
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))
        return divs

    def _reconciliar_linha_digitavel(self, titulo: Titulo, info) -> List[Divergencia]:
        """
        Conferências determinísticas pela linha digitável do boleto cadastrado
        na parcela (sem OCR):
          1. Banco emissor (3 primeiros dígitos): boleto do Santander (033) tem
             que estar cadastrado como boleto do PRÓPRIO banco, e boleto de
             outro banco como OUTROS bancos — senão a remessa é recusada/tarifada.
          2. Valor embutido na linha digitável x valor a pagar do título.
          3. Vencimento embutido x vencimento do título.
        """
        divs: List[Divergencia] = []
        linha = re.sub(r"\D", "", str(info.linha_digitavel or ""))
        if not linha:
            return divs

        forma = (info.forma_pagamento or "").upper()

        # 1) Banco do boleto x forma de pagamento (próprio banco x outros bancos)
        if len(linha) >= 3 and "BOLETO" in forma and "CONCESSION" not in forma:
            banco = linha[:3]
            nome_banco = _BANCOS.get(banco, f"banco {banco}")
            forma_proprio = any(k in forma for k in ("SANTANDER", "MESMO BANCO", "PROPRIO", "PRÓPRIO"))
            if banco == BANCO_CASA and not forma_proprio:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="BOLETO_BANCO_INCOMPATIVEL",
                    campo="Banco do Boleto",
                    valor_sienge=f"Forma: {info.forma_pagamento}",
                    valor_nfe="-",
                    valor_boleto=f"Boleto é do Santander (033) — cadastrar como boleto do PRÓPRIO banco",
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))
            elif banco != BANCO_CASA and forma_proprio:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="BOLETO_BANCO_INCOMPATIVEL",
                    campo="Banco do Boleto",
                    valor_sienge=f"Forma: {info.forma_pagamento}",
                    valor_nfe="-",
                    valor_boleto=f"Boleto é do {nome_banco} — cadastrar como boleto de OUTROS bancos",
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))

        # 2/3) Valor e vencimento embutidos na linha digitável (padrão FEBRABAN)
        dec = decodificar_linha_digitavel(linha)
        if dec:
            valor_boleto = dec.get("valor")
            venc_boleto = dec.get("vencimento")
            if valor_boleto and titulo.valor_liquido and abs(valor_boleto - titulo.valor_liquido) > 0.05:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="BOLETO_VALOR_DIVERGENTE",
                    campo="Valor do Boleto (linha digitável)",
                    valor_sienge=f"{titulo.valor_liquido:.2f}",
                    valor_nfe="-",
                    valor_boleto=f"{valor_boleto:.2f}",
                    criticidade="CRITICA", danfe_path=titulo.danfe_path,
                ))
            if venc_boleto and titulo.data_vencimento and abs((venc_boleto - titulo.data_vencimento).days) > 1:
                divs.append(Divergencia(
                    titulo_id=titulo.id, titulo_numero=titulo.numero,
                    tipo="BOLETO_VENCIMENTO_DIVERGENTE",
                    campo="Vencimento do Boleto (linha digitável)",
                    valor_sienge=str(titulo.data_vencimento),
                    valor_nfe="-",
                    valor_boleto=str(venc_boleto),
                    criticidade="ATENCAO", danfe_path=titulo.danfe_path,
                ))
        return divs

    def _reconciliar_banco_transferencia(self, titulo: Titulo, info) -> List[Divergencia]:
        """
        TED/transferência x banco da conta destino:
          - conta destino no Santander (033) => tem que ser DEPÓSITO/crédito em
            conta do MESMO banco (TED para o próprio banco é recusada/tarifada);
          - conta destino em outro banco => tem que ser TED/transferência para
            outros bancos, não depósito mesmo banco.
        """
        divs: List[Divergencia] = []
        banco = re.sub(r"\D", "", str(info.banco or ""))
        if not banco:
            return divs
        banco = banco.zfill(3)
        nome_banco = _BANCOS.get(banco, f"banco {banco}")
        forma = (info.forma_pagamento or "").upper()

        eh_ted = "TED" in forma or "DOC" in forma or ("TRANSFER" in forma and "MESMO" not in forma)
        eh_deposito = any(k in forma for k in (
            "DEPOSITO", "DEPÓSITO", "MESMO BANCO", "CREDITO EM CONTA", "CRÉDITO EM CONTA"))
        if not (eh_ted or eh_deposito):
            return divs

        if banco == BANCO_CASA and eh_ted and not eh_deposito:
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="TRANSFERENCIA_BANCO_INCOMPATIVEL",
                campo="Forma de Transferência",
                valor_sienge=f"Forma: {info.forma_pagamento}",
                valor_nfe="-",
                valor_boleto=f"Conta destino é Santander (033) — cadastrar como DEPÓSITO/mesmo banco, não TED",
                criticidade="ATENCAO", danfe_path=titulo.danfe_path,
            ))
        elif banco != BANCO_CASA and eh_deposito and not eh_ted:
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="TRANSFERENCIA_BANCO_INCOMPATIVEL",
                campo="Forma de Transferência",
                valor_sienge=f"Forma: {info.forma_pagamento}",
                valor_nfe="-",
                valor_boleto=f"Conta destino é do {nome_banco} — cadastrar como TED/outros bancos, não depósito mesmo banco",
                criticidade="ATENCAO", danfe_path=titulo.danfe_path,
            ))
        return divs

    def _reconciliar_liquido_parcela(self, titulo: Titulo, info, retencoes: dict) -> List[Divergencia]:
        """
        Conta do líquido SEM depender da NF: parcela bruta (API do Sienge)
        menos TODAS as retenções lançadas no título (INSS, ISS, IR, CAUÇÃO...)
        tem que bater com o valor a pagar do fluxo de caixa.
        Só confere títulos de parcela única — com várias parcelas o rateio
        das retenções é ambíguo.
        """
        divs: List[Divergencia] = []
        if not info.valor or not titulo.valor_liquido:
            return divs
        if info.total_parcelas and info.total_parcelas > 1:
            return divs

        total_retido = sum(float(v or 0) for v in (retencoes or {}).values())
        liquido_esperado = float(info.valor) - total_retido
        if abs(liquido_esperado - titulo.valor_liquido) > 0.05:
            detalhe = " + ".join(f"{k} {float(v or 0):.2f}" for k, v in (retencoes or {}).items()) or "sem retenções"
            divs.append(Divergencia(
                titulo_id=titulo.id, titulo_numero=titulo.numero,
                tipo="LIQUIDO_PARCELA_DIVERGENTE",
                campo="Parcela - Retenções x Valor a Pagar",
                valor_sienge=f"a pagar {titulo.valor_liquido:.2f}",
                valor_nfe="-",
                valor_boleto=f"parcela {float(info.valor):.2f} - ({detalhe}) = {liquido_esperado:.2f}",
                criticidade="CRITICA", danfe_path=titulo.danfe_path,
            ))
        return divs

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
