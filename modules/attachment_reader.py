import io
import re
import json
import hashlib
import base64
from datetime import date, timedelta
from typing import Optional

import fitz  # PyMuPDF
from loguru import logger
import anthropic

from models import Boleto

# Data-base do fator de vencimento FEBRABAN
_FEBRABAN_BASE = date(1997, 10, 7)


def decodificar_linha_digitavel(linha: str) -> Optional[dict]:
    """
    Decodifica a linha digitável (47 dígitos) de um boleto bancário e extrai
    valor e vencimento de forma determinística (padrão FEBRABAN).

    Retorna {'valor': float, 'vencimento': date|None} ou None se não for um boleto
    bancário de 47 posições (ex.: contas de concessionária com 48 dígitos).
    """
    if not linha:
        return None
    digitos = re.sub(r"\D", "", linha)
    if len(digitos) != 47:
        return None  # concessionárias (48) e formatos inesperados caem no fallback OCR

    ld = digitos
    # Reconstrói o código de barras (44 posições) a partir da linha digitável
    barcode = ld[0:4] + ld[32] + ld[33:47] + ld[4:9] + ld[10:20] + ld[21:31]
    if len(barcode) != 44:
        return None

    fator = int(barcode[5:9])
    valor = int(barcode[9:19]) / 100.0
    vencimento = None
    if fator > 0:
        vencimento = _FEBRABAN_BASE + timedelta(days=fator)
    return {"valor": valor, "vencimento": vencimento}


class AttachmentReader:
    def __init__(self, anthropic_api_key: str):
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.cache = {}
        self.cache_boleto = {}

        self.system_prompt_boleto = """Você é um extrator de dados de boletos bancários brasileiros.
Localize, se houver, os dados do boleto na imagem e retorne APENAS JSON válido, sem markdown:
{"tem_boleto": true, "linha_digitavel": "<somente dígitos>", "cnpj_beneficiario": "<somente dígitos>", "nome_beneficiario": "<texto>", "valor": <numero ou null>, "vencimento": "AAAA-MM-DD ou null"}
Regras:
1. linha_digitavel: a sequência impressa acima do código de barras (geralmente 47/48 dígitos). Remova espaços e pontos.
2. cnpj_beneficiario: CNPJ do BENEFICIÁRIO/cedente (quem recebe), não do pagador. Somente dígitos.
3. Se a imagem NÃO contiver um boleto, retorne {"tem_boleto": false}.
4. Nunca adicione texto fora do JSON."""
        
        # O prompt fornecido nas regras
        self.system_prompt = """Você é um extrator especializado de dados fiscais brasileiros.
Sua única função é localizar e retornar a chave de acesso de
Nota Fiscal Eletrônica (NF-e) presente na imagem.

REGRAS OBRIGATÓRIAS:
1. A chave de acesso tem exatamente 44 dígitos numéricos.
2. Ela aparece como: sequência contínua, em blocos separados
   por espaço, ou abaixo de um código de barras com rótulo
   "Chave de Acesso" ou "Chave de acesso NF-e".
3. Ignore qualquer outro número: CNPJ, valor, número NF,
   código de barras de boleto.
4. Se a imagem estiver ilegível e você não puder extrair com
   certeza, retorne exatamente:
   {"chave": null, "confianca": "baixa", "motivo": "<descrição>"}
5. Se encontrar, retorne APENAS JSON válido, sem markdown:
   {"chave": "35241234...", "confianca": "alta"}
6. Nunca adicione texto fora do JSON.
7. Se houver múltiplas chaves, retorne a primeira encontrada."""

    def _validar_chave_nfe(self, chave: str) -> bool:
        if not chave or len(chave) != 44 or not chave.isdigit():
            return False
            
        pesos = [2, 3, 4, 5, 6, 7, 8, 9] * 6
        pesos = pesos[:43]
        
        # A chave de validação vai do índice 42 ao 0 (de trás pra frente, excluindo o DV que é o 43)
        soma = 0
        for i, peso in enumerate(pesos):
            digito = int(chave[42 - i])
            soma += digito * peso
            
        resto = soma % 11
        digito_esperado = 0 if resto in (0, 1) else 11 - resto
        
        return str(digito_esperado) == chave[43]

    def _pdf_para_imagens(self, pdf_bytes: bytes) -> list[bytes]:
        """Converte as páginas do PDF para imagens PNG usando PyMuPDF."""
        imagens = []
        try:
            # Carrega o PDF a partir dos bytes
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Matriz para 200 DPI (72 DPI nativo * 200/72)
            zoom = 200 / 72
            mat = fitz.Matrix(zoom, zoom)
            
            for i in range(len(doc)):
                page = doc[i]
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                imagens.append(img_bytes)
                
        except Exception as e:
            logger.error(f"Erro ao converter PDF para imagem: {e}")
            
        return imagens

    def _chamar_claude(self, image_bytes: bytes) -> Optional[str]:
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        
        try:
            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022", # Usando a versão real correspondente à intenção
                max_tokens=300,
                system=self.system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_base64,
                                }
                            },
                            {
                                "type": "text",
                                "text": "Extraia a chave de acesso desta imagem."
                            }
                        ],
                    }
                ],
            )
            
            content = response.content[0].text.strip()
            
            # Ocasionalmente o modelo pode retornar markdown ou texto antes/depois, tentar parsear direto
            try:
                # Remove possível bloco de markdown ```json ... ```
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                    
                data = json.loads(content.strip())
                
                chave = data.get("chave")
                if chave:
                    # Remove espaços em branco, caso tenham vindo
                    chave = chave.replace(" ", "")
                
                confianca = data.get("confianca")
                motivo = data.get("motivo", "")
                
                logger.info(f"Retorno Claude | Confiança: {confianca} | Chave: {chave} | Motivo: {motivo}")
                return chave
                
            except json.JSONDecodeError:
                logger.warning(f"Erro ao parsear JSON do Claude. Retorno bruto: {content}")
                return None
                
        except Exception as e:
            logger.error(f"Erro na chamada do Anthropic (Claude Vision): {e}")
            return None

    def extrair_chave_nfe(self, pdf_bytes: bytes) -> Optional[str]:
        if not pdf_bytes:
            return None
            
        # Calcula o hash para usar de cache
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        
        if pdf_hash in self.cache:
            logger.info("Chave obtida do cache para este PDF.")
            return self.cache[pdf_hash]
            
        imagens = self._pdf_para_imagens(pdf_bytes)
        if not imagens:
            logger.warning("Nenhuma imagem gerada a partir do PDF.")
            self.cache[pdf_hash] = None
            return None
            
        # Itera por página
        for i, img in enumerate(imagens):
            logger.info(f"Enviando página {i+1}/{len(imagens)} para análise do Claude...")
            chave = self._chamar_claude(img)
            
            if chave:
                if self._validar_chave_nfe(chave):
                    logger.success(f"Chave válida encontrada na página {i+1}: {chave}")
                    self.cache[pdf_hash] = chave
                    return chave
                else:
                    logger.warning(f"Chave encontrada na página {i+1} porém é INVÁLIDA (Dígito verificador falhou): {chave}")
                    
        logger.warning("Não foi possível extrair chave válida de nenhuma das páginas do PDF.")
        self.cache[pdf_hash] = None
        return None

    def _chamar_claude_boleto(self, image_bytes: bytes) -> Optional[dict]:
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        try:
            response = self.client.messages.create(
                model="claude-3-5-haiku-20241022",
                max_tokens=400,
                system=self.system_prompt_boleto,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/png", "data": image_base64}},
                        {"type": "text", "text": "Extraia os dados do boleto desta imagem."},
                    ],
                }],
            )
            content = response.content[0].text.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            return json.loads(content.strip())
        except json.JSONDecodeError:
            logger.warning("Boleto: retorno do Claude não é JSON válido.")
            return None
        except Exception as e:
            logger.error(f"Erro na chamada do Anthropic (boleto): {e}")
            return None

    def extrair_boleto(self, pdf_bytes: bytes) -> Optional[Boleto]:
        """
        Extrai os dados de um boleto presente no anexo (PDF). Valor e vencimento são
        decodificados da linha digitável (determinístico) quando possível; CNPJ e nome
        do beneficiário vêm do OCR. Retorna None se o anexo não tiver boleto.
        """
        if not pdf_bytes:
            return None

        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        if pdf_hash in self.cache_boleto:
            return self.cache_boleto[pdf_hash]

        for i, img in enumerate(self._pdf_para_imagens(pdf_bytes)):
            dados = self._chamar_claude_boleto(img)
            if not dados or not dados.get("tem_boleto"):
                continue

            linha = re.sub(r"\D", "", str(dados.get("linha_digitavel") or ""))
            decodificado = decodificar_linha_digitavel(linha)

            # Valor/vencimento: prioriza a linha digitável; cai no OCR se necessário
            valor = None
            vencimento = None
            if decodificado:
                valor = decodificado["valor"]
                vencimento = decodificado["vencimento"]
            if valor in (None, 0.0) and dados.get("valor") is not None:
                try:
                    valor = float(dados["valor"])
                except (TypeError, ValueError):
                    valor = None
            if vencimento is None and dados.get("vencimento"):
                try:
                    vencimento = date.fromisoformat(str(dados["vencimento"])[:10])
                except ValueError:
                    vencimento = None

            cnpj = re.sub(r"\D", "", str(dados.get("cnpj_beneficiario") or ""))
            boleto = Boleto(
                codigo_barras=linha,
                cnpj_beneficiario=cnpj,
                nome_beneficiario=str(dados.get("nome_beneficiario") or ""),
                valor=valor if valor is not None else 0.0,
                data_vencimento=vencimento,
            )
            logger.success(
                f"Boleto extraído do anexo (pág {i+1}): valor={boleto.valor} "
                f"venc={boleto.data_vencimento} cnpj_benef={boleto.cnpj_beneficiario}"
            )
            self.cache_boleto[pdf_hash] = boleto
            return boleto

        logger.info("Nenhum boleto detectado no anexo.")
        self.cache_boleto[pdf_hash] = None
        return None
