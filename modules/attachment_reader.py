import io
import os
import re
import time
import json
import hashlib
import base64
import threading
from collections import deque
from datetime import date, timedelta
from typing import Optional

import fitz  # PyMuPDF
import requests
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
        # FEBRABAN reiniciou o fator em 22/02/2025 (fator 1000). Datas calculadas
        # na base antiga que caem antes disso pertencem ao novo ciclo: +9000 dias.
        if vencimento < date(2025, 2, 22):
            vencimento += timedelta(days=9000)
    return {"valor": valor, "vencimento": vencimento}


class AttachmentReader:
    # Free tier do Gemini: ~10 req/min — ritmo global (compartilhado entre threads)
    _GEMINI_MAX_POR_MINUTO = 8
    _gemini_lock = threading.Lock()
    _gemini_req_times = deque()

    # Custo/cota: analisa no máximo N páginas por PDF (chave/boleto ficam no início)
    _MAX_PAGINAS_OCR = 4

    def __init__(self, anthropic_api_key: str = None, gemini_api_key: str = None,
                 gemini_model: str = "gemini-2.5-flash", cache_path: str = None):
        # Provedor de OCR: Gemini (gratuito) tem prioridade quando configurado;
        # senão usa Anthropic (Claude Haiku). Sem nenhum, o robô segue sem OCR.
        self.gemini_api_key = gemini_api_key or None
        self.gemini_model = gemini_model
        self.client = anthropic.Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None
        if self.gemini_api_key:
            self.provider = "gemini"
        elif self.client is not None:
            self.provider = "anthropic"
        else:
            # Sem OCR de IA: ainda funciona lendo a CAMADA DE TEXTO dos PDFs
            # (93% dos anexos são PDFs digitais com texto embutido)
            self.provider = None
        logger.info("Leitor de anexos: camada de texto sempre ativa; OCR de IA: "
                    + (f"{self.provider} ({self.gemini_model})" if self.provider == "gemini"
                       else self.provider or "desativado"))
        self.cache = {}
        self.cache_boleto = {}

        # Cache persistente em disco: cada anexo é lido uma única vez;
        # execuções seguintes reutilizam o resultado (hash do PDF -> dados).
        self._cache_path = cache_path
        self._cache_disk_lock = threading.Lock()
        self._tl = threading.local()  # marca falha de chamada p/ não cachear falso negativo
        self._carregar_cache_disco()

    # ------------------------------------------------------------------
    # Cache persistente
    # ------------------------------------------------------------------
    def _carregar_cache_disco(self):
        if not self._cache_path or not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path, encoding="utf-8") as f:
                data = json.load(f)
            self.cache.update(data.get("chaves", {}))
            for h, b in (data.get("boletos") or {}).items():
                if b is None:
                    self.cache_boleto[h] = None
                else:
                    venc = b.get("data_vencimento")
                    self.cache_boleto[h] = Boleto(
                        codigo_barras=b.get("codigo_barras", ""),
                        cnpj_beneficiario=b.get("cnpj_beneficiario", ""),
                        nome_beneficiario=b.get("nome_beneficiario", ""),
                        valor=float(b.get("valor") or 0.0),
                        data_vencimento=date.fromisoformat(venc) if venc else None,
                    )
            logger.info(f"Cache de OCR carregado: {len(self.cache)} chaves, "
                        f"{len(self.cache_boleto)} boletos ({self._cache_path})")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao carregar cache de OCR ({self._cache_path}): {e}")

    def _salvar_cache_disco(self):
        if not self._cache_path:
            return
        try:
            with self._cache_disk_lock:
                boletos = {}
                for h, b in self.cache_boleto.items():
                    boletos[h] = None if b is None else {
                        "codigo_barras": b.codigo_barras,
                        "cnpj_beneficiario": b.cnpj_beneficiario,
                        "nome_beneficiario": b.nome_beneficiario,
                        "valor": b.valor,
                        "data_vencimento": b.data_vencimento.isoformat() if b.data_vencimento else None,
                    }
                tmp = self._cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"chaves": self.cache, "boletos": boletos}, f, ensure_ascii=False)
                os.replace(tmp, self._cache_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao salvar cache de OCR: {e}")

    def _aguardar_janela_gemini(self):
        """Segura o ritmo global (todas as threads) abaixo do limite do free tier."""
        while True:
            with self._gemini_lock:
                agora = time.time()
                fila = self._gemini_req_times
                while fila and agora - fila[0] > 60:
                    fila.popleft()
                if len(fila) < self._GEMINI_MAX_POR_MINUTO:
                    fila.append(agora)
                    return
                espera = 60 - (agora - fila[0]) + 0.5
            logger.debug(f"Ritmo Gemini: aguardando {espera:.1f}s")
            time.sleep(max(espera, 0.5))

    system_prompt_boleto = """Você é um extrator de dados de boletos bancários brasileiros.
Localize, se houver, os dados do boleto na imagem e retorne APENAS JSON válido, sem markdown:
{"tem_boleto": true, "linha_digitavel": "<somente dígitos>", "cnpj_beneficiario": "<somente dígitos>", "nome_beneficiario": "<texto>", "valor": <numero ou null>, "vencimento": "AAAA-MM-DD ou null"}
Regras:
1. linha_digitavel: a sequência impressa acima do código de barras (geralmente 47/48 dígitos). Remova espaços e pontos.
2. cnpj_beneficiario: CNPJ do BENEFICIÁRIO/cedente (quem recebe), não do pagador. Somente dígitos.
3. Se a imagem NÃO contiver um boleto, retorne {"tem_boleto": false}.
4. Nunca adicione texto fora do JSON."""

    # O prompt fornecido nas regras
    system_prompt = """Você é um extrator especializado de dados fiscais brasileiros.
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

    # ------------------------------------------------------------------
    # Camada de texto do PDF (sem OCR, sem custo)
    # ------------------------------------------------------------------
    _RE_CNPJ = re.compile(r"\d{2}[\s.]?\d{3}[\s.]?\d{3}[\s./]?\d{4}[\s-]?\d{2}")
    _RE_LINHA = re.compile(r"\d[\d.\s]{40,70}\d")
    _RE_DINHEIRO = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")

    # Rótulos de tributos em NFS-e/DANFE -> nome canônico (ordem importa: INSS antes de ISS)
    _TRIBUTOS_LABELS = [
        ("INSS", re.compile(r"\bINSS\b", re.I)),
        ("ISS", re.compile(r"\bISS(?:QN)?\b", re.I)),
        ("IR", re.compile(r"\bIR(?:RF|PJ)?\b", re.I)),
        ("PIS", re.compile(r"\bPIS\b", re.I)),
        ("COFINS", re.compile(r"\bCOFINS\b", re.I)),
        ("CSLL", re.compile(r"\bCSLL\b", re.I)),
        ("CAUCAO", re.compile(r"CAU[ÇC][AÃ]O", re.I)),
    ]
    _RE_LIQUIDO = re.compile(r"VALOR\s+L[IÍ]QUIDO", re.I)

    @classmethod
    def _impostos_do_texto(cls, txt: str) -> dict:
        """
        Varre o texto da NF linha a linha e captura o valor monetário que
        acompanha cada rótulo de tributo (mesma linha). Retorna
        {tributo: valor, ..., 'VALOR_LIQUIDO': x} — só o que estiver legível.
        Conservador: em PDFs com layout em colunas o rótulo e o número podem
        se separar; nesses casos o tributo simplesmente não é capturado.
        """
        achados = {}
        com_retencao = set()  # tributos cuja linha menciona retenção explícita
        rx_ret = re.compile(r"RET(?:ID|EN[ÇC])", re.I)
        for linha in txt.splitlines():
            valores = cls._RE_DINHEIRO.findall(linha)
            if not valores:
                continue
            valor = float(valores[-1].replace(".", "").replace(",", "."))
            if cls._RE_LIQUIDO.search(linha) and "VALOR_LIQUIDO" not in achados:
                achados["VALOR_LIQUIDO"] = valor
                continue
            for nome, rx in cls._TRIBUTOS_LABELS:
                if rx.search(linha):
                    # guarda o primeiro valor visto para o tributo (linha de destaque)
                    if nome not in achados and valor > 0:
                        achados[nome] = valor
                        if rx_ret.search(linha):
                            com_retencao.add(nome)
                    break
        achados["_com_retencao"] = sorted(com_retencao)
        return achados

    # UFs válidas do IBGE (posições 1-2 da chave) e modelos de documento fiscal
    _UFS_VALIDAS = {11,12,13,14,15,16,17,21,22,23,24,25,26,27,28,29,31,32,33,35,
                    41,42,43,50,51,52,53}

    @classmethod
    def _chave_plausivel(cls, chave: str) -> bool:
        """Estrutura da chave além do DV: UF do IBGE, ano/mês plausíveis e
        modelo fiscal conhecido — evita 'chaves' fatiadas de outros números
        (linhas digitáveis, códigos) que passam no DV por sorte (1 em 11)."""
        try:
            uf = int(chave[0:2])
            ano = int(chave[2:4])
            mes = int(chave[4:6])
            modelo = chave[20:22]
        except (ValueError, IndexError):
            return False
        return (uf in cls._UFS_VALIDAS and 15 <= ano <= 45 and 1 <= mes <= 12
                and modelo in ("55", "57", "58", "65", "67"))

    def extrair_texto_pdf(self, pdf_bytes: bytes, max_paginas: int = 4) -> str:
        """Extrai o texto embutido do PDF (PDFs digitais; scans retornam vazio)."""
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            txt = "\n".join(doc[i].get_text() for i in range(min(len(doc), max_paginas)))
            doc.close()
            return txt
        except Exception as e:  # noqa: BLE001
            logger.debug(f"PDF sem texto extraível: {e}")
            return ""

    def extrair_info_texto(self, pdf_bytes: bytes) -> dict:
        """
        Lê a camada de texto do PDF e retorna, sem OCR:
          tem_texto (False = scan/foto), chave (NF-e 44 díg. com DV válido),
          cnpjs (todos os CNPJs presentes) e linha_digitavel (boleto 47/48).
        """
        info = {"tem_texto": False, "chave": None, "cnpjs": [], "linha_digitavel": None,
                "digitos": "", "impostos": {}}
        if not pdf_bytes:
            return info
        txt = self.extrair_texto_pdf(pdf_bytes)
        if len((txt or "").strip()) < 200:
            return info
        info["tem_texto"] = True
        # Camada de texto pode ser lixo (scan com OCR ruim embutido) — marca a
        # confiabilidade para as regras não tirarem conclusão de texto corrompido
        info["texto_confiavel"] = txt.count("�") <= 3
        # fluxo só de dígitos: robusto a CNPJ quebrado por espaços/linhas
        info["digitos"] = re.sub(r"\D", "", txt)
        info["cnpjs"] = sorted({re.sub(r"\D", "", m) for m in self._RE_CNPJ.findall(txt)})
        compacto = re.sub(r"[\s.\-/]", "", txt)
        for m in re.finditer(r"\d{44}", compacto):
            if self._validar_chave_nfe(m.group()) and self._chave_plausivel(m.group()):
                info["chave"] = m.group()
                break
        for m in self._RE_LINHA.finditer(txt):
            dig = re.sub(r"\D", "", m.group())
            if len(dig) in (47, 48):
                info["linha_digitavel"] = dig
                break
        if info["texto_confiavel"]:
            info["impostos"] = self._impostos_do_texto(txt)
        # Declaração impressa na própria nota: "OPTANTE PELO SIMPLES NACIONAL"
        m = re.search(r"(N[ÃA]O\s+)?OPTANTE\s+PELO\s+SIMPLES", txt, re.I)
        info["declara_simples"] = (m.group(1) is None) if m else None
        # tipo do documento pelo próprio conteúdo — permite escolher a NF entre
        # vários anexos (pedido, medição, e-mail) sem depender do nome do arquivo
        low = txt.lower()
        info["parece_nf"] = any(k in low for k in
                                ("nota fiscal", "danfe", "nfs-e", "nfse", "nf-e"))
        return info

    @staticmethod
    def _limpar_markdown_json(content: str) -> str:
        """Remove possível bloco de markdown ```json ... ``` em volta do JSON."""
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()

    def _chamar_vision(self, image_bytes: bytes, system_prompt: str, user_text: str,
                       max_tokens: int = 400) -> Optional[str]:
        """Envia a imagem para o provedor de OCR configurado e retorna o texto bruto."""
        if self.provider is None:
            self._tl.falha = True  # sem OCR: não conclui nada sobre a imagem
            return None
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        if self.provider == "gemini":
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{self.gemini_model}:generateContent?key={self.gemini_api_key}")
            body = {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": image_base64}},
                    {"text": user_text},
                ]}],
                "generationConfig": {
                    "maxOutputTokens": max(max_tokens, 1024),
                    "temperature": 0,
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            }
            try:
                for tentativa in range(5):
                    self._aguardar_janela_gemini()
                    resp = requests.post(url, json=body, timeout=90)
                    if resp.status_code != 429:
                        break
                    # 429: respeita o retryDelay sugerido pelo Google, se vier
                    espera = 30
                    try:
                        for det in resp.json().get("error", {}).get("details", []):
                            rd = str(det.get("retryDelay", ""))
                            if rd.endswith("s") and rd[:-1].replace(".", "").isdigit():
                                espera = min(120, float(rd[:-1]) + 1)
                    except Exception:  # noqa: BLE001
                        pass
                    logger.warning(f"Gemini 429 (free tier); aguardando {espera:.0f}s "
                                   f"(tentativa {tentativa + 1}/5)...")
                    time.sleep(espera)
                resp.raise_for_status()
                data = resp.json()
                parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
                texto = "".join(p.get("text", "") for p in parts).strip()
                return texto or None
            except Exception as e:  # noqa: BLE001
                logger.error(f"Erro na chamada do Gemini (OCR): {e}")
                self._tl.falha = True
                return None

        # Anthropic (Claude Haiku)
        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/png", "data": image_base64}},
                        {"type": "text", "text": user_text},
                    ],
                }],
            )
            return response.content[0].text.strip()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Erro na chamada do Anthropic (Claude Vision): {e}")
            self._tl.falha = True
            return None

    def _chamar_claude(self, image_bytes: bytes) -> Optional[str]:
        content = self._chamar_vision(
            image_bytes, self.system_prompt, "Extraia a chave de acesso desta imagem.",
            max_tokens=300)
        if not content:
            return None

        try:
            data = json.loads(self._limpar_markdown_json(content))

            chave = data.get("chave")
            if chave:
                # Remove espaços em branco, caso tenham vindo
                chave = chave.replace(" ", "")

            confianca = data.get("confianca")
            motivo = data.get("motivo", "")

            logger.info(f"Retorno OCR | Confiança: {confianca} | Chave: {chave} | Motivo: {motivo}")
            return chave

        except json.JSONDecodeError:
            logger.warning(f"Erro ao parsear JSON do OCR. Retorno bruto: {content}")
            return None

    def extrair_chave_nfe(self, pdf_bytes: bytes) -> Optional[str]:
        if not pdf_bytes:
            return None
            
        # Calcula o hash para usar de cache
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
        
        if pdf_hash in self.cache:
            logger.info("Chave obtida do cache para este PDF.")
            return self.cache[pdf_hash]

        # 1) Camada de texto (grátis, instantânea) — resolve os PDFs digitais
        info_txt = self.extrair_info_texto(pdf_bytes)
        if info_txt.get("chave"):
            logger.success(f"Chave obtida da camada de texto do PDF: {info_txt['chave']}")
            self.cache[pdf_hash] = info_txt["chave"]
            self._salvar_cache_disco()
            return info_txt["chave"]
        if self.provider is None:
            # Sem OCR: se o PDF é texto e não tem chave, a conclusão é confiável;
            # se é scan, deixa sem cache para o OCR tentar quando for ativado.
            if info_txt.get("tem_texto"):
                self.cache[pdf_hash] = None
                self._salvar_cache_disco()
            return None

        # 2) OCR de IA (fallback para scans/fotos)
        imagens = self._pdf_para_imagens(pdf_bytes)
        if not imagens:
            logger.warning("Nenhuma imagem gerada a partir do PDF.")
            self.cache[pdf_hash] = None
            return None
            
        # Itera por página (limitado para poupar cota do OCR)
        self._tl.falha = False
        for i, img in enumerate(imagens[:self._MAX_PAGINAS_OCR]):
            logger.info(f"Enviando página {i+1}/{len(imagens)} para análise do OCR...")
            chave = self._chamar_claude(img)

            if chave:
                if self._validar_chave_nfe(chave):
                    logger.success(f"Chave válida encontrada na página {i+1}: {chave}")
                    self.cache[pdf_hash] = chave
                    self._salvar_cache_disco()
                    return chave
                else:
                    logger.warning(f"Chave encontrada na página {i+1} porém é INVÁLIDA (Dígito verificador falhou): {chave}")

        if getattr(self._tl, "falha", False):
            # Falha de chamada (429/erro): não conclui "sem chave" — tenta no próximo ciclo
            logger.warning("OCR falhou em alguma página; resultado não será cacheado.")
            return None
        logger.warning("Não foi possível extrair chave válida de nenhuma das páginas do PDF.")
        self.cache[pdf_hash] = None
        self._salvar_cache_disco()
        return None

    def _chamar_claude_boleto(self, image_bytes: bytes) -> Optional[dict]:
        content = self._chamar_vision(
            image_bytes, self.system_prompt_boleto,
            "Extraia os dados do boleto desta imagem.", max_tokens=400)
        if not content:
            return None
        try:
            return json.loads(self._limpar_markdown_json(content))
        except json.JSONDecodeError:
            logger.warning("Boleto: retorno do OCR não é JSON válido.")
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

        # 1) Camada de texto (grátis): linha digitável impressa no PDF digital
        info_txt = self.extrair_info_texto(pdf_bytes)
        linha_txt = info_txt.get("linha_digitavel")
        if linha_txt:
            dec = decodificar_linha_digitavel(linha_txt) or {}
            boleto = Boleto(
                codigo_barras=linha_txt, cnpj_beneficiario="", nome_beneficiario="",
                valor=dec.get("valor") or 0.0, data_vencimento=dec.get("vencimento"),
            )
            logger.success(f"Boleto obtido da camada de texto: valor={boleto.valor} venc={boleto.data_vencimento}")
            self.cache_boleto[pdf_hash] = boleto
            self._salvar_cache_disco()
            return boleto
        if self.provider is None:
            if info_txt.get("tem_texto"):
                self.cache_boleto[pdf_hash] = None
                self._salvar_cache_disco()
            return None

        # 2) OCR de IA (fallback para scans/fotos)
        self._tl.falha = False
        for i, img in enumerate(self._pdf_para_imagens(pdf_bytes)[:self._MAX_PAGINAS_OCR]):
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
            self._salvar_cache_disco()
            return boleto

        if getattr(self._tl, "falha", False):
            logger.warning("OCR de boleto falhou em alguma página; resultado não será cacheado.")
            return None
        logger.info("Nenhum boleto detectado no anexo.")
        self.cache_boleto[pdf_hash] = None
        self._salvar_cache_disco()
        return None
