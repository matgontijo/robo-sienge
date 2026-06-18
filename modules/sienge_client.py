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
