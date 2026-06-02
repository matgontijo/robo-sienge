import os
import time
import tempfile
import base64
from datetime import date
from typing import List
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization
from loguru import logger
import requests
from requests.exceptions import RequestException, Timeout
from models import Boleto

class SantanderClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        cert_path: str,
        cert_password: str,
        ambiente: str = "production"
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.ambiente = ambiente
        self.timeout = 30
        
        self.base_url = "https://trust-open.api.santander.com.br"
        if self.ambiente == "sandbox":
            self.base_url = "https://trust-sandbox.api.santander.com.br"
            
        self.temp_cert_file = None
        self.temp_key_file = None
        
        self._load_certificate()
        
        self.session = requests.Session()
        self.session.cert = (self.temp_cert_file.name, self.temp_key_file.name)
        
        self.access_token = None
        self.token_expires_at = 0

    def _load_certificate(self):
        try:
            with open(self.cert_path, "rb") as f:
                pfx_data = f.read()
                
            pwd = self.cert_password.encode('utf-8') if self.cert_password else None
            private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, pwd)
            
            key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
            
            self.temp_cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            self.temp_cert_file.write(cert_pem)
            self.temp_cert_file.flush()
            
            self.temp_key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            self.temp_key_file.write(key_pem)
            self.temp_key_file.flush()
            
            logger.info("Certificado Santander carregado com sucesso.")
        except Exception as e:
            logger.critical(f"Falha ao carregar certificado Santander PFX: {e}")
            raise

    def __del__(self):
        if self.temp_cert_file and os.path.exists(self.temp_cert_file.name):
            os.remove(self.temp_cert_file.name)
        if self.temp_key_file and os.path.exists(self.temp_key_file.name):
            os.remove(self.temp_key_file.name)

    def _request_with_retry(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        retries = 3
        backoff_factor = 1

        for attempt in range(retries):
            start_time = time.time()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                elapsed = time.time() - start_time
                logger.info(f"{method} {endpoint} | Status: {response.status_code} | Time: {elapsed:.2f}s")
                
                if 400 <= response.status_code < 500:
                    response.raise_for_status()

                if 500 <= response.status_code < 600:
                    response.raise_for_status()
                    
                return response
                
            except (RequestException, Timeout) as e:
                is_4xx = getattr(e.response, "status_code", 0) and 400 <= e.response.status_code < 500
                if attempt == retries - 1 or is_4xx:
                    logger.error(f"Falha na requisio {method} {endpoint}: {str(e)}")
                    raise

                sleep_time = backoff_factor * (2 ** attempt)
                logger.warning(f"Erro {method} {endpoint}: {str(e)}. Retentando em {sleep_time}s...")
                time.sleep(sleep_time)

    def autenticar(self) -> None:
        """
        POST /auth/oauth/v2/token
        grant_type=client_credentials
        """
        logger.info("Autenticando no Santander API...")
        
        endpoint = "/auth/oauth/v2/token"
        
        # A autenticao no Santander com Client Credentials usa Auth Basic com id:secret
        auth_str = f"{self.client_id}:{self.client_secret}"
        b64_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
        
        headers = {
            "Authorization": f"Basic {b64_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "grant_type": "client_credentials"
        }
        
        response = self._request_with_retry("POST", endpoint, headers=headers, data=data)
        result = response.json()
        
        self.access_token = result.get("access_token")
        expires_in = int(result.get("expires_in", 3600))
        
        # Renova 60s antes do vencimento
        self.token_expires_at = time.time() + expires_in - 60
        
        # Atualiza a sesso para enviar o bearer header nas chamadas subsequentes
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}"
        })
        logger.success("Autenticado com sucesso no Santander.")

    def _garantir_autenticacao(self):
        if not self.access_token or time.time() >= self.token_expires_at:
            self.autenticar()

    def consultar_dda(self, data_inicio: date, data_fim: date) -> List[Boleto]:
        """
        GET /collection/v2/boletos/dda
        Pagina automaticamente.
        """
        self._garantir_autenticacao()
        logger.info(f"Consultando DDA Santander de {data_inicio} at {data_fim}")
        
        endpoint = "/collection/v2/boletos/dda"
        boletos = []
        
        # Implementar paginao genrica
        has_next = True
        offset = 0
        limit = 100
        
        while has_next:
            params = {
                "dataVencimentoInicial": data_inicio.strftime("%Y-%m-%d"),
                "dataVencimentoFinal": data_fim.strftime("%Y-%m-%d"),
                "offset": offset,
                "limit": limit
            }
            
            response = self._request_with_retry("GET", endpoint, params=params)
            data = response.json()
            
            resultados = data.get("boletos", [])
            for item in resultados:
                # Converter item retornado para dataclass Boleto
                # Exemplo baseado no Santander Collection API
                b = Boleto(
                    codigo_barras=item.get("codigoBarras", ""),
                    cnpj_beneficiario=item.get("beneficiario", {}).get("documento", {}).get("numero", ""),
                    nome_beneficiario=item.get("beneficiario", {}).get("nome", ""),
                    valor=float(item.get("valorNominal", 0.0)),
                    data_vencimento=date.fromisoformat(item.get("dataVencimento", "1970-01-01")[:10]),
                    nosso_numero=item.get("nossoNumero", ""),
                    banco_emissor=item.get("bancoEmissor", {}).get("nome", "")
                )
                boletos.append(b)
                
            # Verifica paginao
            # O Santander no devolve hasNext diretamente as vezes, pode ter _links.next ou qtd < limit
            if len(resultados) < limit:
                has_next = False
            else:
                offset += limit
                
        logger.success(f"DDA Consultado com sucesso. {len(boletos)} encontrados.")
        return boletos
