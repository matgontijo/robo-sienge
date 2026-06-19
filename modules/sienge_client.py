import time
import base64
import requests
from datetime import date
from typing import Optional, List
from loguru import logger
from requests.exceptions import RequestException, Timeout
from models import Titulo

class SiengeClient:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = 30
        
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
        url = f"{self.base_url}{endpoint}"
        try:
            r = self.session.get(url, params=params or {}, timeout=self.timeout)
            ct = r.headers.get("content-type", "")
            kind = "JSON" if "json" in ct else "HTML/outro"
            return {"endpoint": endpoint, "status": r.status_code, "tipo": kind,
                    "resumo": r.text[:120].replace("\n", " ").strip()}
        except Exception as e:  # noqa: BLE001
            return {"endpoint": endpoint, "status": 0, "tipo": "ERRO", "resumo": str(e)[:120]}

    def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        retries = 3
        backoff_factor = 1  # 1s, 2s, 4s

        for attempt in range(retries):
            start_time = time.time()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"{method} {endpoint} | Status: {response.status_code} | Time: {elapsed:.2f}s")
                
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

    def listar_titulos(
        self,
        data_inicio: date,
        data_fim: date,
        status: List[str] = None
    ) -> List[Titulo]:
        """
        GET /bill-debts
        Parâmetros: startDueDate, endDueDate, situation
        Pagina automaticamente (offset/limit) até buscar todos.
        """
        if status is None:
            status = ["ABERTO", "VENCIDO"]

        logger.info(f"Iniciando busca de títulos no Sienge de {data_inicio} até {data_fim}")
        
        endpoint = "/bill-debts"
        limit = 50
        offset = 0
        
        todos_titulos = []
        has_next = True
        
        # A API pode aceitar status como múltiplos parâmetros listados, ou separados por vírgula.
        # Considerando padrão listado se for passar via dict.
        # Muitas APIs Sienge esperam listagem via params separados, mas o requests cuida disso para listas.
        
        while has_next:
            params = {
                "startDueDate": data_inicio.strftime("%Y-%m-%d"),
                "endDueDate": data_fim.strftime("%Y-%m-%d"),
                "situation": status,
                "limit": limit,
                "offset": offset
            }
            
            response = self._request_with_retry("GET", endpoint, params=params)
            data = response.json()
            
            resultados = data.get("results", [])
            for item in resultados:
                # Extrai dados mapeando para a dataclass
                # A API Sienge retorna algo como:
                # { "id": 123, "documentNumber": "1234", "providerId": 1, "providerName": "Fornecedor X", "providerCnpj": "...", ... }
                # Usaremos nomes genéricos baseados na documentação típica da API Sienge
                
                titulo = Titulo(
                    id=item.get("id", 0),
                    numero=str(item.get("documentNumber", "")),
                    fornecedor_nome=item.get("providerName", ""),
                    fornecedor_cnpj=item.get("providerCpfCnpj", ""),
                    valor_nominal=float(item.get("value", 0.0)),
                    valor_liquido=float(item.get("balance", item.get("value", 0.0))),
                    data_vencimento=date.fromisoformat(item.get("dueDate", "1970-01-01")[:10]),
                    forma_pagamento=item.get("paymentMethod", ""),
                    status=item.get("situation", "")
                )
                todos_titulos.append(titulo)
            
            # Paginação
            meta = data.get("resultSetMetadata", {})
            has_next = meta.get("hasNext", False)
            if has_next:
                offset += limit
                
        logger.success(f"Concluída busca de títulos: {len(todos_titulos)} encontrados.")
        return todos_titulos

    def baixar_anexo(self, titulo_id: int) -> Optional[bytes]:
        """
        GET /bill-debts/{id}/attachments
        Retorna o bytes do primeiro anexo encontrado.
        Se não houver anexo, retorna None e loga warning.
        """
        endpoint = f"/bill-debts/{titulo_id}/attachments"
        logger.info(f"Iniciando download de anexo para título {titulo_id}")
        
        try:
            # Lista os anexos
            response = self._request_with_retry("GET", endpoint)
            data = response.json()
            
            resultados = data.get("results", [])
            if not resultados:
                logger.warning(f"Título {titulo_id} não possui anexos")
                return None
                
            # Pega o primeiro anexo
            primeiro_anexo = resultados[0]
            anexo_id = primeiro_anexo.get("id")
            anexo_url = primeiro_anexo.get("url") # As vezes a API envia URL, mas o endpoint pra baixar é específico
            
            if not anexo_id:
                logger.warning(f"Título {titulo_id} com anexo sem ID")
                return None
                
            # Baixa o conteúdo do anexo
            download_endpoint = f"/bill-debts/{titulo_id}/attachments/{anexo_id}/download"
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

        GET /bill-debts/{id}/attachments           -> lista de anexos
        GET /bill-debts/{id}/attachments/{aid}/download -> conteúdo do anexo

        Retorna:
          {
            "nf_bytes", "nf_path",          # melhor candidato a NF/DANFE
            "boleto_bytes", "boleto_path",  # melhor candidato a boleto
            "anexos": [ {id, nome, path, tipo}, ... ]
          }
        TODO(API): confirmar o nome do campo do arquivo na listagem (name/fileName/
        description) e a URL de download contra a API real.
        """
        import os
        resultado = {"nf_bytes": None, "nf_path": None,
                     "boleto_bytes": None, "boleto_path": None, "anexos": []}
        if titulo_id is None:
            return resultado

        try:
            resp = self._request_with_retry("GET", f"/bill-debts/{titulo_id}/attachments")
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
            anexo_id = item.get("id")
            nome = (item.get("name") or item.get("fileName")
                    or item.get("description") or f"anexo_{idx+1}")
            if anexo_id is None:
                continue
            try:
                dl = self._request_with_retry(
                    "GET", f"/bill-debts/{titulo_id}/attachments/{anexo_id}/download")
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
    #   - GET /bill-debts                              -> "Títulos do Contas a Pagar"
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
    ) -> Optional[int]:
        """
        Resolve o ID interno do título no Sienge a partir do número do título
        (coluna Tit/Parc do relatório), necessário para baixar anexos via
        GET /bill-debts/{id}/attachments.

        GET /bill-debts exige uma janela de datas (startDueDate/endDueDate); por isso
        recebe `data_inicio`/`data_fim`. Faz o match local por documentNumber e, se
        informado, por parcela.

        TODO(API): se a API expuser filtro direto por número do documento, trocar a
        varredura por janela por uma consulta pontual.
        """
        if not numero:
            return None
        if not data_inicio or not data_fim:
            logger.warning(
                "resolver_titulo_por_numero sem janela de datas; /bill-debts pode exigir período."
            )

        try:
            params = {"limit": 200, "offset": 0}
            if data_inicio:
                params["startDueDate"] = data_inicio.strftime("%Y-%m-%d")
            if data_fim:
                params["endDueDate"] = data_fim.strftime("%Y-%m-%d")

            resp = self._request_with_retry("GET", "/bill-debts", params=params)
            for item in resp.json().get("results", []):
                doc = str(item.get("documentNumber", "")).strip()
                if doc == str(numero).strip():
                    if parcela is None or str(item.get("installmentNumber", "")) == str(parcela):
                        logger.info(f"Título {numero}/{parcela} resolvido para id={item.get('id')}")
                        return item.get("id")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Falha ao resolver título {numero}: {e}")

        logger.warning(f"Não foi possível resolver o ID interno do título {numero}/{parcela}.")
        return None

    # ==========================================================================
    # Informações de pagamento do título (aba "Inf. Pagamento") — anti-fraude
    # ==========================================================================
    #
    # Endpoints prováveis (a confirmar ao vivo quando o recurso for autorizado):
    #   GET /bill-debts/{id}/payment-information   (detalhe de pagamento por parcela)
    #   ou os campos vêm embutidos em GET /bill-debts/{id}
    # TODO(API): confirmar endpoint e nomes de campo (banco/conta/beneficiário/PIX).

    _PG_FORMA_KEYS = ("paymentMethod", "paymentType", "formaPagamento", "paymentTypeName")
    _PG_BANCO_KEYS = ("bank", "bankNumber", "bankCode", "banco")
    _PG_AGENCIA_KEYS = ("agency", "agencyNumber", "agencia")
    _PG_CONTA_KEYS = ("account", "accountNumber", "conta")
    _PG_TITULAR_CNPJ_KEYS = ("holderCpfCnpj", "accountHolderCnpj", "beneficiaryCpfCnpj", "titularCnpj")
    _PG_TITULAR_NOME_KEYS = ("holderName", "accountHolderName", "titularNome")
    _PG_PIX_KEY_KEYS = ("pixKey", "pixKeyValue", "chavePix")
    _PG_PIX_TIPO_KEYS = ("pixKeyType", "pixType", "tipoChavePix")
    _PG_BENEF_CNPJ_KEYS = ("beneficiaryCpfCnpj", "payeeCpfCnpj", "beneficiarioCnpj")
    _PG_BENEF_NOME_KEYS = ("beneficiaryName", "payeeName", "beneficiarioNome")
    _PG_LINHA_KEYS = ("digitableLine", "barCode", "linhaDigitavel", "typeableLine")

    def consultar_informacoes_pagamento(self, titulo_id: int, parcela: str = None):
        """
        Lê os dados de pagamento cadastrados no título (forma, banco/conta, PIX,
        beneficiário) para confronto anti-fraude. Retorna um InfoPagamento ou None.
        """
        from models import InfoPagamento
        if titulo_id is None:
            return None

        item = None
        # Tentativa A: sub-recurso de pagamento
        for endpoint in (f"/bill-debts/{titulo_id}/payment-information",
                         f"/bill-debts/{titulo_id}/payments"):
            try:
                resp = self._request_with_retry("GET", endpoint)
                data = resp.json()
                results = data.get("results", data if isinstance(data, list) else [data])
                if results:
                    item = results[0]
                    break
            except Exception as e:  # noqa: BLE001
                logger.debug(f"payment-information via {endpoint} indisponível: {e}")

        # Tentativa B: campos embutidos no próprio título
        if item is None:
            try:
                resp = self._request_with_retry("GET", f"/bill-debts/{titulo_id}")
                item = resp.json()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Não foi possível ler info de pagamento do título {titulo_id}: {e}")
                return None

        g = self._primeiro_campo
        info = InfoPagamento(
            forma_pagamento=str(g(item, self._PG_FORMA_KEYS) or ""),
            parcela=parcela,
            banco=self._txt(g(item, self._PG_BANCO_KEYS)),
            agencia=self._txt(g(item, self._PG_AGENCIA_KEYS)),
            conta=self._txt(g(item, self._PG_CONTA_KEYS)),
            titular_nome=self._txt(g(item, self._PG_TITULAR_NOME_KEYS)),
            titular_cnpj=self._so_digitos(g(item, self._PG_TITULAR_CNPJ_KEYS)),
            chave_pix=self._txt(g(item, self._PG_PIX_KEY_KEYS)),
            tipo_chave_pix=self._txt(g(item, self._PG_PIX_TIPO_KEYS)),
            beneficiario_nome=self._txt(g(item, self._PG_BENEF_NOME_KEYS)),
            beneficiario_cnpj=self._so_digitos(g(item, self._PG_BENEF_CNPJ_KEYS)),
            linha_digitavel=self._txt(g(item, self._PG_LINHA_KEYS)),
        )
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
        Retorna dict {tributo: valor}. TODO(API): confirmar endpoint/campos.
        """
        if titulo_id is None:
            return {}
        for endpoint in (f"/bill-debts/{titulo_id}/taxes",
                         f"/bill-debts/{titulo_id}/tax-information"):
            try:
                resp = self._request_with_retry("GET", endpoint)
                data = resp.json()
                results = data.get("results", data if isinstance(data, list) else [data])
                impostos = {}
                for r in results:
                    nome = (r.get("taxType") or r.get("type") or r.get("name") or "").upper()
                    valor = r.get("value") or r.get("amount") or r.get("withheldValue")
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
            return None

        nfe_data, destacados = self._extrair_nfedata_de_nfes(consolidado, chave)
        titulo.chave_nfe = chave
        titulo.nf_chave_api = chave
        if nfe_data:
            titulo.nf_cnpj_emitente = nfe_data.cnpj_emitente or titulo.nf_cnpj_emitente
            titulo.nf_valor = nfe_data.valor_total or titulo.nf_valor
            titulo.nf_numero = nfe_data.numero_nfe or titulo.nf_numero
        return {"nfe_data": nfe_data, "destacados": destacados}

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
