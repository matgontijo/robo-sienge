import time
import base64
import requests
from collections import deque
from datetime import date
from typing import Optional, List
from loguru import logger
from requests.exceptions import RequestException, Timeout
from models import Titulo

class SiengeClient:
    # A API do Sienge limita ~200 requisições/min por usuário; ficamos abaixo
    # para não tomar 429 no meio do ciclo.
    _MAX_REQ_POR_MINUTO = 170

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = 30
        self._req_times = deque()
        
        # Cria a string em base64 para o Basic Auth
        auth_str = f"{self.username}:{self.password}"
        b64_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Basic {b64_auth}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        })

    def probe(self, endpoint: str, params: dict = None) -> dict:
        """Faz um GET simples (sem retry/raise) e devolve status/tipo/resumo —
        usado pelo diagnóstico de conexões. Não levanta exceção."""
        url = self._url(endpoint)
        try:
            r = self.session.get(url, params=params or {}, timeout=self.timeout)
            ct = r.headers.get("content-type", "")
            kind = "JSON" if "json" in ct else "HTML/outro"
            return {"endpoint": endpoint, "status": r.status_code, "tipo": kind,
                    "resumo": r.text[:120].replace("\n", " ").strip()}
        except Exception as e:  # noqa: BLE001
            return {"endpoint": endpoint, "status": 0, "tipo": "ERRO", "resumo": str(e)[:120]}

    def _url(self, endpoint: str) -> str:
        # A API bulk vive em /public/api/bulk-data/v1, fora do /public/api/v1 usado
        # pelos demais recursos (ex.: /bills, /nfes, /creditors).
        if endpoint.startswith("/bulk-data/"):
            return self.base_url.replace("/public/api/v1", "/public/api") + endpoint
        return f"{self.base_url}{endpoint}"

    def _aguardar_janela_rate_limit(self):
        """Segura o ritmo para não estourar o limite por minuto da API."""
        agora = time.time()
        while self._req_times and agora - self._req_times[0] > 60:
            self._req_times.popleft()
        if len(self._req_times) >= self._MAX_REQ_POR_MINUTO:
            espera = 60 - (agora - self._req_times[0]) + 0.2
            if espera > 0:
                logger.info(f"Ritmo: {self._MAX_REQ_POR_MINUTO} req/min atingido; aguardando {espera:.1f}s")
                time.sleep(espera)
        self._req_times.append(time.time())

    def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = self._url(endpoint)
        retries = 3
        backoff_factor = 1  # 1s, 2s, 4s
        esperas_429 = 0

        attempt = 0
        while attempt < retries:
            self._aguardar_janela_rate_limit()
            start_time = time.time()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"{method} {endpoint} | Status: {response.status_code} | Time: {elapsed:.2f}s")

                # 429: rate limit — espera (Retry-After se houver) e tenta de novo,
                # sem consumir as tentativas de erro
                if response.status_code == 429 and esperas_429 < 6:
                    esperas_429 += 1
                    ra = str(response.headers.get("Retry-After") or "")
                    espera = int(ra) if ra.isdigit() else min(60, 10 * esperas_429)
                    logger.warning(f"429 (rate limit) em {endpoint}; aguardando {espera}s (tentativa {esperas_429}/6)...")
                    time.sleep(espera)
                    continue

                # Se for 4xx, é erro do cliente/negócio, não faz sentido tentar de novo
                if 400 <= response.status_code < 500:
                    response.raise_for_status()

                # Se for 5xx, levanta a exceção para cair no except e tentar de novo
                if 500 <= response.status_code < 600:
                    response.raise_for_status()
                    
                return response
                
            except (RequestException, Timeout) as e:
                # Se for a última tentativa, ou se for um erro 4xx (que foi pego no RequestException), lança
                is_4xx = getattr(e.response, "status_code", 0) and 400 <= e.response.status_code < 500
                if attempt == retries - 1 or is_4xx:
                    logger.error(f"Falha na requisição {method} {endpoint}: {str(e)}")
                    raise

                sleep_time = backoff_factor * (2 ** attempt)
                logger.warning(f"Erro na requisição {method} {endpoint}: {str(e)}. Retentando em {sleep_time}s...")
                time.sleep(sleep_time)
            attempt += 1

        raise RequestException(f"Falha na requisição {method} {endpoint}: tentativas esgotadas")

    def listar_titulos(
        self,
        data_inicio: date,
        data_fim: date,
        status: List[str] = None
    ) -> List[Titulo]:
        """
        GET /bills — "Títulos do contas a pagar" (bill-debt-v1).
        Atenção: startDate/endDate filtram por data de EMISSÃO (a API não filtra
        por vencimento; vencimento/forma de pagamento ficam nas parcelas —
        GET /bills/{billId}/installments). `status` da API é consistência
        (S/N/I), não situação de pagamento; o parâmetro `status` desta função é
        ignorado (mantido por compatibilidade).
        Pagina automaticamente (offset/limit) até buscar todos.
        """
        logger.info(f"Iniciando busca de títulos no Sienge (emissão de {data_inicio} até {data_fim})")

        endpoint = "/bills"
        limit = 200  # máximo permitido pela API
        offset = 0

        todos_titulos = []

        while True:
            params = {
                "startDate": data_inicio.strftime("%Y-%m-%d"),
                "endDate": data_fim.strftime("%Y-%m-%d"),
                "limit": limit,
                "offset": offset
            }

            response = self._request_with_retry("GET", endpoint, params=params)
            data = response.json()

            resultados = data.get("results", [])
            for item in resultados:
                # Schema Bill: id (nº do título), debtorId, creditorId,
                # documentIdentificationId, documentNumber, issueDate,
                # totalInvoiceAmount, discount, status (S/N/I), originId,
                # accessKeyNumber (chave NF-e), installmentsNumber.
                bruto = float(item.get("totalInvoiceAmount") or 0.0)
                desconto = float(item.get("discount") or 0.0)
                titulo = Titulo(
                    id=item.get("id", 0),
                    numero=str(item.get("id", "")),  # nº do título = id do bill
                    fornecedor_nome="",  # /bills não traz nome/CNPJ; usar /creditors/{creditorId}
                    fornecedor_cnpj="",
                    valor_nominal=bruto,
                    valor_liquido=bruto - desconto,
                    # /bills só traz emissão; o vencimento real está nas parcelas
                    data_vencimento=date.fromisoformat((item.get("issueDate") or "1970-01-01")[:10]),
                    forma_pagamento="",
                    status=item.get("status", "")
                )
                titulo.numero_documento = str(item.get("documentNumber") or "") or None
                titulo.documento = (item.get("documentIdentificationId") or "").strip() or None
                titulo.origem = item.get("originId")
                titulo.nf_chave_api = item.get("accessKeyNumber") or None
                todos_titulos.append(titulo)

            # Paginação via resultSetMetadata {count, offset, limit}
            meta = data.get("resultSetMetadata", {})
            total = meta.get("count")
            offset += limit
            if total is None:
                if len(resultados) < limit:
                    break
            elif offset >= total:
                break

        logger.success(f"Concluída busca de títulos: {len(todos_titulos)} encontrados.")
        return todos_titulos

    def baixar_anexo(self, titulo_id: int) -> Optional[bytes]:
        """
        GET /bills/{billId}/attachments
        Retorna o bytes do primeiro anexo encontrado.
        Se não houver anexo, retorna None e loga warning.
        """
        endpoint = f"/bills/{titulo_id}/attachments"
        logger.info(f"Iniciando download de anexo para título {titulo_id}")
        
        try:
            # Lista os anexos
            response = self._request_with_retry("GET", endpoint)
            data = response.json()
            
            resultados = data.get("results", [])
            if not resultados:
                logger.warning(f"Título {titulo_id} não possui anexos")
                return None
                
            # Pega o primeiro anexo (schema BillAttachment: campo "attachmentid")
            primeiro_anexo = resultados[0]
            anexo_id = primeiro_anexo.get("attachmentid", primeiro_anexo.get("id"))

            if not anexo_id:
                logger.warning(f"Título {titulo_id} com anexo sem ID")
                return None

            # Baixa o conteúdo do anexo
            download_endpoint = f"/bills/{titulo_id}/attachments/{anexo_id}"
            download_response = self._request_with_retry("GET", download_endpoint)
            
            logger.success(f"Concluído download de anexo para título {titulo_id}: {len(download_response.content)} bytes")
            return download_response.content

        except Exception as e:
            logger.error(f"Erro ao baixar anexo do título {titulo_id}: {str(e)}")
            return None

    # Palavras-chave para classificar anexos por nome de arquivo
    _KW_NF = ("danfe", "nfe", "nf-e", "nota", "fiscal", "nfse", "nf_", "_nf", "nf.")
    _KW_BOLETO = ("boleto", "bol_", "_bol", "cobranca", "cobrança", "titulo", "título", "pix", "duplicata")

    @classmethod
    def _classificar_anexo(cls, nome: str) -> Optional[str]:
        """Classifica um anexo como 'nf' ou 'boleto' pelo nome do arquivo."""
        n = (nome or "").lower()
        if any(k in n for k in cls._KW_BOLETO):
            return "boleto"
        if any(k in n for k in cls._KW_NF):
            return "nf"
        return None

    def baixar_anexos_titulo(self, titulo_id: int, pasta_destino: str) -> dict:
        """
        Lista e baixa TODOS os anexos do título via API do Sienge, salva cada um em
        `pasta_destino` e classifica priorizando NF e boleto.

        GET /bills/{billId}/attachments                  -> lista (BillAttachment)
        GET /bills/{billId}/attachments/{attachmentId}   -> download do anexo

        Retorna:
          {
            "nf_bytes", "nf_path",          # melhor candidato a NF/DANFE
            "boleto_bytes", "boleto_path",  # melhor candidato a boleto
            "anexos": [ {id, nome, path, tipo}, ... ]
          }
        """
        import os
        resultado = {"nf_bytes": None, "nf_path": None,
                     "boleto_bytes": None, "boleto_path": None, "anexos": []}
        if titulo_id is None:
            return resultado

        try:
            resp = self._request_with_retry("GET", f"/bills/{titulo_id}/attachments")
            itens = resp.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao listar anexos do título {titulo_id}: {e}")
            return resultado

        if not itens:
            logger.warning(f"Título {titulo_id} não possui anexos na API.")
            return resultado

        os.makedirs(pasta_destino, exist_ok=True)
        nf_fallback = None  # primeiro PDF, caso nada case por nome

        for idx, item in enumerate(itens):
            anexo_id = item.get("attachmentid", item.get("id"))
            nome = (item.get("name") or item.get("fileName")
                    or item.get("description") or f"anexo_{idx+1}")
            if anexo_id is None:
                continue
            try:
                dl = self._request_with_retry(
                    "GET", f"/bills/{titulo_id}/attachments/{anexo_id}")
                conteudo = dl.content
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Falha ao baixar anexo {anexo_id} do título {titulo_id}: {e}")
                continue

            nome_seguro = "".join(c for c in str(nome) if c.isalnum() or c in " ._-").strip() or f"anexo_{idx+1}"
            if not os.path.splitext(nome_seguro)[1]:
                nome_seguro += ".pdf"
            caminho = os.path.join(pasta_destino, nome_seguro)
            with open(caminho, "wb") as f:
                f.write(conteudo)

            tipo = self._classificar_anexo(nome_seguro)
            resultado["anexos"].append({"id": anexo_id, "nome": nome_seguro, "path": caminho, "tipo": tipo})

            if tipo == "nf" and resultado["nf_bytes"] is None:
                resultado["nf_bytes"], resultado["nf_path"] = conteudo, caminho
            elif tipo == "boleto" and resultado["boleto_bytes"] is None:
                resultado["boleto_bytes"], resultado["boleto_path"] = conteudo, caminho
            if nf_fallback is None and nome_seguro.lower().endswith(".pdf"):
                nf_fallback = (conteudo, caminho)

        # Se nada foi classificado como NF, usa o primeiro PDF como candidato
        if resultado["nf_bytes"] is None and nf_fallback:
            resultado["nf_bytes"], resultado["nf_path"] = nf_fallback

        logger.success(
            f"Anexos do título {titulo_id}: {len(resultado['anexos'])} salvos em {pasta_destino} "
            f"(NF={'sim' if resultado['nf_path'] else 'nao'}, boleto={'sim' if resultado['boleto_path'] else 'nao'})"
        )
        return resultado

    # ==========================================================================
    # Resolução de Nota Fiscal de Compra a partir de um título do relatório
    # ==========================================================================
    #
    # Endpoints da doc oficial (https://api.sienge.com.br/docs/) usados aqui:
    #   - GET /purchase-invoices                       -> "Nota Fiscal de Compra"
    #   - GET /purchase-invoices/deliveries-attended   -> vínculo título(bill)↔nota,
    #         filtrável por billId / número da nota / número do título
    #   - GET /bills                                   -> "Títulos do Contas a Pagar"
    #
    # TODO(API): os nomes exatos de alguns campos do JSON de /purchase-invoices
    # (CNPJ do emitente, chave de acesso NF-e, valor total) não puderam ser
    # confirmados na doc renderizada via JS. A extração abaixo testa os nomes mais
    # prováveis de forma defensiva e LOGA qual chave casou — confirme contra a API
    # real (o cliente já possui credenciais) e fixe os nomes definitivos.

    # Conjuntos de candidatos de nomes de campo (defensivo até confirmar na API real)
    _NF_NUMERO_KEYS = ("number", "invoiceNumber", "billNumber", "documentNumber", "nfNumber")
    _NF_CNPJ_KEYS = ("creditorCpfCnpj", "supplierCnpj", "issuerCnpj", "cpfCnpj",
                     "creditorCnpj", "providerCpfCnpj")
    _NF_VALOR_KEYS = ("totalInvoiceAmount", "totalValue", "value", "amount", "invoiceAmount")
    _NF_CHAVE_KEYS = ("accessKey", "nfeAccessKey", "electronicInvoiceAccessKey",
                      "nfeKey", "chaveAcesso", "accessKeyNfe")
    _NF_ID_KEYS = ("id", "sequentialNumber", "purchaseInvoiceId", "billId")

    @staticmethod
    def _primeiro_campo(item: dict, candidatos) -> Optional[object]:
        """Retorna o primeiro valor não-vazio dentre os nomes de campo candidatos."""
        for chave in candidatos:
            if chave in item and item[chave] not in (None, "", 0):
                logger.debug(f"Campo NF resolvido por '{chave}'")
                return item[chave]
        return None

    def _extrair_campos_nf(self, item: dict) -> dict:
        return {
            "nf_numero": self._primeiro_campo(item, self._NF_NUMERO_KEYS),
            "nf_cnpj_emitente": self._primeiro_campo(item, self._NF_CNPJ_KEYS),
            "nf_valor": self._primeiro_campo(item, self._NF_VALOR_KEYS),
            "nf_chave_api": self._primeiro_campo(item, self._NF_CHAVE_KEYS),
        }

    def buscar_nota_fiscal_compra(
        self,
        numero_nota: str = None,
        bill_id: int = None,
    ) -> Optional[dict]:
        """
        GET /purchase-invoices — busca a Nota Fiscal de Compra.

        Tenta primeiro pelo vínculo título↔nota (deliveries-attended) quando há
        `bill_id`; senão filtra diretamente por número da nota. Retorna o item JSON
        bruto da nota (ou None). A extração de campos é feita por `_extrair_campos_nf`.

        TODO(API): confirmar o nome do parâmetro de filtro (`billId`, `number`, ...)
        contra a doc/credenciais reais do cliente.
        """
        # Estratégia A: vínculo título -> nota
        if bill_id is not None:
            try:
                resp = self._request_with_retry(
                    "GET", "/purchase-invoices/deliveries-attended",
                    params={"billId": bill_id},
                )
                results = resp.json().get("results", [])
                if results:
                    logger.info(f"NF vinculada ao título (billId={bill_id}) encontrada.")
                    return results[0]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Falha ao consultar deliveries-attended (billId={bill_id}): {e}")

        # Estratégia B: filtro direto por número da nota
        if numero_nota:
            for param in ("number", "invoiceNumber", "billNumber"):  # TODO(API): fixar
                try:
                    resp = self._request_with_retry(
                        "GET", "/purchase-invoices", params={param: numero_nota, "limit": 1},
                    )
                    results = resp.json().get("results", [])
                    if results:
                        logger.info(f"NF de compra encontrada por {param}={numero_nota}.")
                        return results[0]
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"Filtro {param}={numero_nota} não retornou NF: {e}")

        logger.warning(
            f"Nenhuma NF de compra resolvida (numero_nota={numero_nota}, bill_id={bill_id})."
        )
        return None

    def resolver_nota_fiscal(self, titulo: Titulo) -> bool:
        """
        Preenche `titulo.nf_numero`, `nf_cnpj_emitente`, `nf_valor`, `nf_chave_api`
        (e `attachment_url`, se houver) a partir da NF de compra do Sienge.

        Usa o número do documento do relatório (`numero_documento`, quando o tipo é
        NFE/NFSE) e/ou o ID interno do título (`titulo.id`) como chave de ligação.
        Retorna True se conseguiu resolver a NF, False caso contrário (cai no
        fallback OCR+Sefaz no orquestrador).
        """
        numero_nf = None
        if titulo.tipo_documento and titulo.tipo_documento.upper() in ("NFE", "NFSE", "NF"):
            numero_nf = titulo.numero_documento

        item = self.buscar_nota_fiscal_compra(numero_nota=numero_nf, bill_id=titulo.id)
        if not item:
            return False

        campos = self._extrair_campos_nf(item)
        titulo.nf_numero = str(campos["nf_numero"]) if campos["nf_numero"] is not None else None
        titulo.nf_cnpj_emitente = (
            str(campos["nf_cnpj_emitente"]) if campos["nf_cnpj_emitente"] is not None else None
        )
        try:
            titulo.nf_valor = float(campos["nf_valor"]) if campos["nf_valor"] is not None else None
        except (TypeError, ValueError):
            titulo.nf_valor = None
        titulo.nf_chave_api = (
            str(campos["nf_chave_api"]) if campos["nf_chave_api"] is not None else None
        )

        # Se a NF já trouxe a chave de acesso, reaproveita como chave_nfe (pula OCR)
        if titulo.nf_chave_api:
            titulo.chave_nfe = titulo.nf_chave_api

        # Anexo/boleto, se a NF expuser URL direta
        url_anexo = self._primeiro_campo(item, ("attachmentUrl", "url", "fileUrl"))
        if url_anexo:
            titulo.attachment_url = str(url_anexo)

        logger.success(
            f"NF resolvida via API para título {titulo.numero}: "
            f"num={titulo.nf_numero} cnpj={titulo.nf_cnpj_emitente} "
            f"valor={titulo.nf_valor} chave={'sim' if titulo.nf_chave_api else 'nao'}"
        )
        return bool(titulo.nf_chave_api or titulo.nf_cnpj_emitente or titulo.nf_numero)

    def resolver_titulo_por_numero(
        self,
        numero: str,
        parcela: str = None,
        data_inicio: date = None,
        data_fim: date = None,
        titulo=None,
    ) -> Optional[int]:
        """
        Resolve o ID interno do título no Sienge a partir do número do título
        (coluna Tit/Parc do relatório).

        No Sienge o número do título É o id do bill (schema Bill: "id - Número do
        título"), então a resolução é direta: GET /bills/{billId}. Como fallback
        (número não numérico ou 404), varre GET /bills por janela de emissão e
        casa por documentNumber.
        """
        if not numero:
            return None

        num = str(numero).strip()

        # Caminho direto: nº do título = billId
        if num.isdigit():
            try:
                resp = self._request_with_retry("GET", f"/bills/{int(num)}")
                item = resp.json()
                if item.get("id") is not None:
                    # Aproveita a chave da NF-e que vem no próprio título
                    if titulo is not None and item.get("accessKeyNumber"):
                        titulo.nf_chave_api = titulo.nf_chave_api or str(item["accessKeyNumber"]).strip()
                    logger.info(f"Título {numero}/{parcela} resolvido direto: /bills/{item['id']}")
                    return item["id"]
            except Exception as e:  # noqa: BLE001
                logger.debug(f"GET /bills/{num} falhou ({e}); tentando varredura por documento.")

        # Fallback: varre por janela de EMISSÃO e casa pelo número do documento.
        # A emissão antecede o vencimento do relatório: recua 180 dias da janela.
        try:
            from datetime import timedelta
            ini = (data_inicio - timedelta(days=180)) if data_inicio else None
            fim = data_fim or data_inicio
            if not ini or not fim:
                logger.warning("resolver_titulo_por_numero sem janela de datas; /bills exige startDate/endDate.")
                return None
            offset = 0
            while True:
                params = {"limit": 200, "offset": offset,
                          "startDate": ini.strftime("%Y-%m-%d"),
                          "endDate": fim.strftime("%Y-%m-%d")}
                data = self._request_with_retry("GET", "/bills", params=params).json()
                results = data.get("results", [])
                for item in results:
                    if str(item.get("documentNumber", "")).strip() == num:
                        logger.info(f"Título {numero}/{parcela} resolvido por documento: id={item.get('id')}")
                        return item.get("id")
                total = data.get("resultSetMetadata", {}).get("count")
                offset += 200
                if not results or (total is not None and offset >= total):
                    break
        except Exception as e:  # noqa: BLE001
            logger.error(f"Falha ao resolver título {numero}: {e}")

        logger.warning(f"Não foi possível resolver o ID interno do título {numero}/{parcela}.")
        return None

    # ==========================================================================
    # Informações de pagamento do título (aba "Inf. Pagamento") — anti-fraude
    # ==========================================================================
    #
    # Endpoints oficiais (bill-debt-v1):
    #   GET /bills/{billId}/installments  -> parcelas (forma, valor, vencimento,
    #       situação, sentToBank/lote)
    #   GET /bills/{billId}/installments/{n}/payment-information/{tipo}
    #       tipos: pix | bank-transfer | boleto-bancario | boleto-concessionaria |
    #              dda | boleto-tax | darf-tax | inss-tax | fgts-tax | gare-tax | darj-tax
    # O Sienge separa a informação de pagamento por forma; consultamos os tipos
    # relevantes para a remessa e usamos o primeiro que retornar dados.

    _PG_TIPOS = ("pix", "bank-transfer", "boleto-bancario", "dda", "boleto-concessionaria")

    @staticmethod
    def _tipo_chave_pix(key_type: str, chave: str) -> Optional[str]:
        """Converte keyPixType do Sienge (C/E/T/A) para o rótulo usado no robô."""
        t = (key_type or "").strip().upper()
        if t == "C":  # CPF/CNPJ — decide pelo tamanho
            digitos = "".join(ch for ch in str(chave or "") if ch.isdigit())
            return "CNPJ" if len(digitos) == 14 else "CPF"
        return {"E": "EMAIL", "T": "TELEFONE", "A": "ALEATORIA"}.get(t) or (t or None)

    def consultar_informacoes_pagamento(self, titulo_id: int, parcela: str = None):
        """
        Lê os dados de pagamento cadastrados na parcela do título (forma,
        banco/conta, PIX, beneficiário) para confronto anti-fraude.
        Retorna um InfoPagamento ou None.
        """
        from models import InfoPagamento
        if titulo_id is None:
            return None

        # 1) Parcelas: forma de pagamento, valor e vencimento
        try:
            resp = self._request_with_retry("GET", f"/bills/{titulo_id}/installments")
            parcelas = resp.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Não foi possível listar parcelas do título {titulo_id}: {e}")
            return None

        alvo = None
        for p in parcelas:
            if parcela is not None and str(p.get("installmentNumber", "")) == str(parcela).strip():
                alvo = p
                break
        if alvo is None and parcelas:
            alvo = parcelas[0]
        if alvo is None:
            logger.warning(f"Título {titulo_id} sem parcelas na API.")
            return None

        n_parcela = alvo.get("installmentNumber")
        info = InfoPagamento(
            forma_pagamento=str(alvo.get("paymentType") or ""),
            parcela=str(n_parcela) if n_parcela is not None else parcela,
        )
        if alvo.get("amount") is not None:
            info.valor = float(alvo["amount"])
        if alvo.get("dueDate"):
            info.vencimento = date.fromisoformat(str(alvo["dueDate"])[:10])

        # 2) Informação de pagamento da parcela, separada por tipo no Sienge
        for tipo in self._PG_TIPOS:
            endpoint = f"/bills/{titulo_id}/installments/{n_parcela}/payment-information/{tipo}"
            try:
                data = self._request_with_retry("GET", endpoint).json()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"payment-information/{tipo} indisponível no título {titulo_id}: {e}")
                continue
            item = (data.get("results") or [data])[0] if isinstance(data, dict) else None
            if not item:
                continue

            if tipo == "pix":
                info.chave_pix = self._txt(item.get("keyPix"))
                info.tipo_chave_pix = self._tipo_chave_pix(item.get("keyPixType"), item.get("keyPix"))
                info.beneficiario_nome = self._txt(item.get("beneficiaryName"))
                info.beneficiario_cnpj = self._so_digitos(
                    item.get("beneficiaryCNPJNumber") or item.get("beneficiaryCPFNumber"))
            elif tipo in ("bank-transfer", "dda"):
                info.banco = self._txt(item.get("beneficiaryBankCode"))
                agencia = item.get("beneficiaryBankBranchNumber")
                ag_dig = item.get("beneficiaryBankBranchDigit")
                info.agencia = self._txt(f"{agencia}-{ag_dig}" if agencia and ag_dig else agencia)
                conta = item.get("beneficiaryAccountNumber")
                ct_dig = item.get("beneficiaryAccountDigit")
                info.conta = self._txt(f"{conta}-{ct_dig}" if conta and ct_dig else conta)
                info.titular_nome = self._txt(item.get("beneficiaryName"))
                info.titular_cnpj = self._so_digitos(
                    item.get("beneficiaryCNPJNumber") or item.get("beneficiaryCPFNumber"))
            else:  # boleto-bancario / boleto-concessionaria
                info.linha_digitavel = self._txt(
                    item.get("boletoBancarioManualBarCodeNumber")
                    or item.get("boletoBancarioBarCodeNumber")
                    or item.get("barCodeNumber"))

            if any((info.chave_pix, info.conta, info.linha_digitavel)):
                logger.info(f"Inf. pagamento título {titulo_id}/{n_parcela} via {tipo}.")
                break

        logger.info(
            f"Inf. pagamento título {titulo_id}: forma={info.forma_pagamento!r} "
            f"cnpj_destino={info.cnpj_destino()}"
        )
        return info

    def consultar_credor(self, credor_id: int = None, cnpj: str = None):
        """
        GET /creditors — dados do fornecedor (CNPJ, nome). Referência independente
        para a conferência anti-fraude. Retorna o item JSON bruto ou None.
        TODO(API): confirmar filtro por CNPJ e nome dos campos.
        """
        try:
            if credor_id is not None:
                resp = self._request_with_retry("GET", f"/creditors/{credor_id}")
                return resp.json()
            if cnpj:
                resp = self._request_with_retry("GET", "/creditors",
                                                params={"cnpj": self._so_digitos(cnpj), "limit": 1})
                results = resp.json().get("results", [])
                return results[0] if results else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao consultar credor (id={credor_id}, cnpj={cnpj}): {e}")
        return None

    def consultar_impostos_titulo(self, titulo_id: int) -> dict:
        """
        Lê impostos/retenções lançados no título (aba "Impostos").
        GET /bills/{billId}/taxes — schema BillTax: taxId (código do imposto,
        ex.: "IR"), rate, amount, taxableBaseAmount.
        Retorna dict {tributo: valor}.
        """
        if titulo_id is None:
            return {}
        for endpoint in (f"/bills/{titulo_id}/taxes",):
            try:
                resp = self._request_with_retry("GET", endpoint)
                data = resp.json()
                results = data.get("results", data if isinstance(data, list) else [data])
                impostos = {}
                for r in results:
                    nome = str(r.get("taxId") or r.get("taxType") or r.get("name") or "").upper()
                    valor = r.get("amount") or r.get("value")
                    if nome and valor is not None:
                        try:
                            impostos[nome] = float(valor)
                        except (TypeError, ValueError):
                            pass
                if impostos:
                    return impostos
            except Exception as e:  # noqa: BLE001
                logger.debug(f"impostos via {endpoint} indisponível: {e}")
        return {}

    def consultar_notas_bulk(self, company_id: int = None, data_inicio: date = None,
                             data_fim: date = None) -> list:
        """
        GET /bulk-data/v1/invoice-itens — itens de notas fiscais com impostos
        destacados (ICMS, IPI, PIS, COFINS) para a conferência fiscal.
        TODO(API): confirmar nomes dos parâmetros e campos de imposto.
        """
        params = {}
        if company_id is not None:
            params["companyId"] = company_id
        if data_inicio:
            params["startDate"] = data_inicio.strftime("%Y-%m-%d")
        if data_fim:
            params["endDate"] = data_fim.strftime("%Y-%m-%d")
        try:
            resp = self._request_with_retry("GET", "/bulk-data/v1/invoice-itens", params=params)
            data = resp.json()
            return data.get("data", data.get("results", [])) if isinstance(data, dict) else data
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao consultar bulk-data de notas: {e}")
            return []

    def consultar_nfe_produto(self, chave: str) -> Optional[dict]:
        """
        Consolida a NF-e de Produto pelo módulo "Notas Fiscais Eletrônicas de Produto"
        do Sienge (o Sienge já importa o XML da Sefaz — dispensa certificado/OCR).

        Endpoints (confirmados na doc oficial /docs/#/nfe-api-v1):
          GET /nfes/{chave}
          GET /nfes/{chave}/issuers-recipients   -> emitente/destinatário (CNPJ)
          GET /nfes/{chave}/payments             -> formas de pagamento
          GET /nfes/{chave}/icms                 -> ICMS total
          GET /nfes/{chave}/issqn                -> ISSQN total

        Retorna um dict consolidado {nota, emitente_destinatario, pagamentos, icms, issqn}
        ou None. TODO(API): mapear os nomes finais de campo após autorização.
        """
        if not chave:
            return None
        consolidado = {}
        partes = {
            "nota": f"/nfes/{chave}",
            "emitente_destinatario": f"/nfes/{chave}/issuers-recipients",
            "pagamentos": f"/nfes/{chave}/payments",
            "icms": f"/nfes/{chave}/icms",
            "issqn": f"/nfes/{chave}/issqn",
        }
        achou = False
        for nome, endpoint in partes.items():
            try:
                resp = self._request_with_retry("GET", endpoint)
                consolidado[nome] = resp.json()
                achou = True
            except Exception as e:  # noqa: BLE001
                logger.debug(f"NF-e produto {nome} ({endpoint}) indisponível: {e}")
        if not achou:
            logger.warning(f"NF-e de produto não encontrada para a chave {chave}.")
            return None
        logger.success(f"NF-e de produto consolidada via API para a chave {chave}.")
        return consolidado

    def listar_nfes(self, data_inicio: date = None, data_fim: date = None, numero: str = None) -> list:
        """GET /nfes — lista NF-e de produto por período (e número, se a API aceitar)."""
        params = {"limit": 200, "offset": 0}
        if data_inicio:
            params["startDate"] = data_inicio.strftime("%Y-%m-%d")
        if data_fim:
            params["endDate"] = data_fim.strftime("%Y-%m-%d")
        if numero:
            params["nfeNumber"] = numero  # TODO(API): confirmar nome do filtro
        try:
            resp = self._request_with_retry("GET", "/nfes", params=params)
            return resp.json().get("results", [])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao listar /nfes: {e}")
            return []

    def resolver_nfe_produto_para_titulo(self, titulo, data_inicio: date = None, data_fim: date = None):
        """
        Resolve a NF-e de Produto do título via /nfes (substitui Sefaz/OCR):
        usa a chave (do anexo/título) ou casa por número na listagem, e consolida
        os dados da nota. Preenche os campos nf_* do título e devolve
        {nfe_data: NFeData, destacados: dict} ou None.
        """
        chave = titulo.chave_nfe or titulo.nf_chave_api
        if not chave and titulo.numero_documento and (titulo.tipo_documento or "").upper().startswith("NF"):
            for nf in self.listar_nfes(data_inicio, data_fim, numero=titulo.numero_documento):
                k = self._primeiro_campo(nf, ("nfeKey", "accessKey", "key", "chaveAcesso"))
                if k:
                    chave = str(k)
                    break
        if not chave:
            return None

        consolidado = self.consultar_nfe_produto(chave)
        if not consolidado:
            # Fallback sem o módulo /nfes: o CNPJ do emitente, série e número da
            # nota estão codificados na própria chave de acesso — garante o
            # cruzamento CNPJ emitente × credor mesmo sem a API de NF-e.
            nfe_data = self._nfedata_da_chave(chave)
            if not nfe_data:
                return None
            titulo.chave_nfe = chave
            titulo.nf_chave_api = chave
            titulo.nf_cnpj_emitente = nfe_data.cnpj_emitente or titulo.nf_cnpj_emitente
            titulo.nf_numero = nfe_data.numero_nfe or titulo.nf_numero
            logger.info(
                f"NF-e derivada da chave de acesso (módulo /nfes indisponível): "
                f"CNPJ emitente {nfe_data.cnpj_emitente}, nº {nfe_data.numero_nfe}"
            )
            return {"nfe_data": nfe_data, "destacados": {}}

        nfe_data, destacados = self._extrair_nfedata_de_nfes(consolidado, chave)
        titulo.chave_nfe = chave
        titulo.nf_chave_api = chave
        if nfe_data:
            titulo.nf_cnpj_emitente = nfe_data.cnpj_emitente or titulo.nf_cnpj_emitente
            titulo.nf_valor = nfe_data.valor_total or titulo.nf_valor
            titulo.nf_numero = nfe_data.numero_nfe or titulo.nf_numero
        return {"nfe_data": nfe_data, "destacados": destacados}

    @staticmethod
    def _nfedata_da_chave(chave: str):
        """NFeData mínimo decodificado da estrutura da chave de acesso (44 dígitos):
        cUF(2) + AAMM(4) + CNPJ emitente(14) + modelo(2) + série(3) + número(9) +
        tpEmis(1) + cNF(8) + DV(1). Sem valor da nota (não existe na chave)."""
        from models import NFeData
        from datetime import date as _date
        digitos = "".join(ch for ch in str(chave or "") if ch.isdigit())
        if len(digitos) != 44:
            return None
        try:
            data_em = _date(2000 + int(digitos[2:4]), int(digitos[4:6]), 1)
        except ValueError:
            data_em = _date(1970, 1, 1)
        return NFeData(
            chave=digitos,
            cnpj_emitente=digitos[6:20],
            nome_emitente="",
            valor_total=0.0,
            valor_liquido=0.0,
            data_emissao=data_em,
            numero_nfe=str(int(digitos[25:34])),
            serie=str(int(digitos[22:25])),
        )

    def _extrair_nfedata_de_nfes(self, c: dict, chave: str):
        """Monta um NFeData + dict de impostos destacados a partir do /nfes consolidado.
        TODO(API): confirmar nomes de campo após autorização do recurso."""
        from models import NFeData
        from datetime import date as _date
        nota = c.get("nota") or {}
        emit = c.get("emitente_destinatario") or {}
        icms = c.get("icms") or {}
        issqn = c.get("issqn") or {}
        g = self._primeiro_campo

        def _cnpj(d):
            v = g(d, ("issuerCnpj", "emitterCnpj", "cnpj", "issuerCpfCnpj"))
            if v:
                return self._so_digitos(v)
            for sub in ("issuer", "emitter", "emitente"):
                if isinstance(d.get(sub), dict):
                    vv = self._primeiro_campo(d[sub], ("cnpj", "cpfCnpj", "cnpjCpf"))
                    if vv:
                        return self._so_digitos(vv)
            return None

        cnpj_emit = _cnpj(emit) or _cnpj(nota) or ""
        valor = g(nota, ("totalValue", "value", "invoiceValue", "totalNfeValue", "vNF"))
        numero = g(nota, ("number", "nfeNumber", "nNF", "invoiceNumber"))
        serie = g(nota, ("series", "serie"))
        nome = g(emit, ("issuerName", "emitterName", "issuerCorporateName"))
        try:
            valor_f = float(valor) if valor is not None else 0.0
        except (TypeError, ValueError):
            valor_f = 0.0
        data_em = None
        de = g(nota, ("issueDate", "emissionDate", "dhEmi", "date"))
        if de:
            try:
                data_em = _date.fromisoformat(str(de)[:10])
            except ValueError:
                pass

        nfe = NFeData(
            chave=chave, cnpj_emitente=cnpj_emit, nome_emitente=str(nome or ""),
            valor_total=valor_f, valor_liquido=valor_f, data_emissao=data_em,
            numero_nfe=str(numero or ""), serie=str(serie or ""),
        )

        destacados = {}
        vi = g(icms, ("totalIcms", "icmsValue", "value", "vICMS"))
        if vi is not None:
            try:
                destacados["ICMS"] = float(vi)
            except (TypeError, ValueError):
                pass
        vs = g(issqn, ("totalIssqn", "issqnValue", "value", "vISS"))
        if vs is not None:
            try:
                destacados["ISSQN"] = float(vs)
            except (TypeError, ValueError):
                pass
        return nfe, destacados

    @staticmethod
    def _txt(v):
        return str(v).strip() if v not in (None, "") else None

    @staticmethod
    def _so_digitos(v):
        if v in (None, ""):
            return None
        import re as _re
        d = _re.sub(r"\D", "", str(v))
        return d or None
