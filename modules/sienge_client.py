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
