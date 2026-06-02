import os
import time
import tempfile
from typing import Optional
from datetime import datetime, timezone
from lxml import etree
import requests
from zeep import Client, Transport
from zeep.plugins import HistoryPlugin
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization
from signxml import XMLSigner
from loguru import logger

class SefazClient:
    def __init__(
        self,
        cert_path: str,
        cert_password: str,
        cnpj_empresa: str,
        ambiente: int = 1
    ):
        self.cert_path = cert_path
        self.cert_password = cert_password
        self.cnpj_empresa = "".join(filter(str.isdigit, cnpj_empresa))
        self.ambiente = ambiente # 1 = Prod, 2 = Homol
        
        self.url = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?WSDL"
        if self.ambiente == 2:
            self.url = "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx?WSDL"

        self.url_evento = "https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx?WSDL"
        if self.ambiente == 2:
            self.url_evento = "https://hom1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/NFeRecepcaoEvento4.asmx?WSDL"
            
        self.nsu_file = ".nsu_state"
        
        self.key_pem = b""
        self.cert_pem = b""
        self.temp_cert_file = None
        self.temp_key_file = None
        
        self._load_certificate()
        
        self.session = requests.Session()
        self.session.cert = (self.temp_cert_file.name, self.temp_key_file.name)
        
        self.transport = Transport(session=self.session, timeout=30)
        self.history = HistoryPlugin()

    def _load_certificate(self):
        try:
            with open(self.cert_path, "rb") as f:
                pfx_data = f.read()
                
            pwd = self.cert_password.encode('utf-8') if self.cert_password else None
            private_key, certificate, _ = pkcs12.load_key_and_certificates(pfx_data, pwd)
            
            self.key_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            self.cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
            
            # Zeep/Requests precisam de arquivos físicos
            self.temp_cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            self.temp_cert_file.write(self.cert_pem)
            self.temp_cert_file.flush()
            
            self.temp_key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
            self.temp_key_file.write(self.key_pem)
            self.temp_key_file.flush()
            
            logger.info("Certificado digital carregado com sucesso.")
        except Exception as e:
            logger.critical(f"Falha ao carregar certificado PFX: {e}")
            raise

    def __del__(self):
        # Limpar os arquivos temporários
        if self.temp_cert_file and os.path.exists(self.temp_cert_file.name):
            os.remove(self.temp_cert_file.name)
        if self.temp_key_file and os.path.exists(self.temp_key_file.name):
            os.remove(self.temp_key_file.name)

    def _assinar_xml(self, xml_str: str, tag_para_assinar: str) -> str:
        root = etree.fromstring(xml_str.encode('utf-8'))
        signer = XMLSigner(
            method=b"http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
            signature_algorithm=b"rsa-sha256",
            digest_algorithm=b"sha256",
            c14n_algorithm=b"http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
        )
        
        # Para assinar eventos da Sefaz
        signed_root = signer.sign(
            root,
            key=self.key_pem,
            cert=self.cert_pem,
            reference_uri=f"#{tag_para_assinar}"
        )
        return etree.tostring(signed_root, encoding='utf-8').decode('utf-8')

    def registrar_ciencia_emissao(self, chave: str) -> bool:
        """
        Envia evento 210210 (Ciência da Emissão).
        """
        logger.info(f"Registrando Ciência da Emissão para a chave {chave}...")
        
        tp_evento = "210210"
        desc_evento = "Ciencia da Operacao"
        
        agora = datetime.now(timezone.utc).astimezone().isoformat()
        
        # O ID da tag evento é ID + tpEvento + chave + nSeqEvento (geralmente 01)
        id_evento = f"ID{tp_evento}{chave}01"
        
        xml_evento = f"""<envEvento xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.00">
            <idLote>1</idLote>
            <evento versao="1.00">
                <infEvento Id="{id_evento}">
                    <cOrgao>91</cOrgao> <!-- 91 = Ambiente Nacional -->
                    <tpAmb>{self.ambiente}</tpAmb>
                    <CNPJ>{self.cnpj_empresa}</CNPJ>
                    <chNFe>{chave}</chNFe>
                    <dhEvento>{agora}</dhEvento>
                    <tpEvento>{tp_evento}</tpEvento>
                    <nSeqEvento>1</nSeqEvento>
                    <versaoEvento>1.00</versaoEvento>
                    <detEvento versao="1.00">
                        <descEvento>{desc_evento}</descEvento>
                    </detEvento>
                </infEvento>
            </evento>
        </envEvento>"""
        
        try:
            xml_assinado = self._assinar_xml(xml_evento, id_evento)
            
            client = Client(self.url_evento, transport=self.transport, plugins=[self.history])
            
            # Usando zeep passando xml crú (AnyNode) se possível, ou injetando no envelope
            node = etree.fromstring(xml_assinado.encode('utf-8'))
            resposta = client.service.nfeRecepcaoEvento(nfeDadosMsg=node)
            
            # Analisa o retorno
            # cStat 128 (Lote processado), no detalhe o cStat do evento deve ser 135 (Evento registrado e vinculado)
            ret_env_evento = resposta
            
            # Aqui simplificaremos pegando os valores de resposta via zeep
            # O zeep converte a resposta para dicionário
            cStat = ret_env_evento['cStat']
            xMotivo = ret_env_evento['xMotivo']
            
            logger.info(f"Sefaz RecepcaoEvento Lote: cStat={cStat} xMotivo={xMotivo}")
            
            ret_evento = ret_env_evento.get('retEvento', [])
            if not isinstance(ret_evento, list):
                ret_evento = [ret_evento]
                
            for ret in ret_evento:
                inf_evento = ret.get('infEvento', {})
                cStat_ev = inf_evento.get('cStat')
                xMotivo_ev = inf_evento.get('xMotivo')
                logger.info(f"Sefaz Evento {chave}: cStat={cStat_ev} xMotivo={xMotivo_ev}")
                
                # 135 - Evento registrado, 573 - Rejeição: Duplicidade de evento
                if cStat_ev in ('135', '573'):
                    logger.success(f"Ciência da Emissão registrada (ou já existia) para {chave}")
                    return True
                    
            logger.error(f"Falha ao registrar Ciência da Emissão para {chave}")
            return False
            
        except Exception as e:
            logger.error(f"Erro ao enviar evento de Ciência da Emissão: {e}")
            return False

    def buscar_xml_por_chave(self, chave: str) -> Optional[str]:
        """
        Consulta NFeDistribuicaoDFe por consChNFe.
        """
        logger.info(f"Buscando XML na Sefaz para a chave {chave}...")
        
        xml_consulta = f"""<distDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01">
            <tpAmb>{self.ambiente}</tpAmb>
            <cUFAutor>SP</cUFAutor> <!-- Aqui idealmente seria a UF da empresa, mas Nacional ignora -->
            <CNPJ>{self.cnpj_empresa}</CNPJ>
            <consChNFe>
                <chNFe>{chave}</chNFe>
            </consChNFe>
        </distDFeInt>"""
        
        # Sefaz Distribuicao aceita a string do node
        node = etree.fromstring(xml_consulta.encode('utf-8'))
        
        try:
            client = Client(self.url, transport=self.transport, plugins=[self.history])
            resposta = client.service.nfeDistDFeInteresse(nfeDadosMsg=node)
            
            cStat = resposta['cStat']
            xMotivo = resposta['xMotivo']
            
            logger.info(f"Retorno DistDFe: cStat={cStat} xMotivo={xMotivo}")
            
            # 138 = Documento localizado
            if cStat != '138':
                logger.warning(f"Sefaz retornou {cStat}: {xMotivo} para a chave {chave}")
                return None
                
            lote = resposta.get('loteDistDFeInt')
            if not lote:
                return None
                
            docs = lote.get('docZip', [])
            if not isinstance(docs, list):
                docs = [docs]
                
            for doc in docs:
                schema = doc.get('schema', '')
                import base64
                import zlib
                
                zip_data = base64.b64decode(doc.get('_value_1'))
                xml_bytes = zlib.decompress(zip_data, 16 + zlib.MAX_WBITS)
                xml_str = xml_bytes.decode('utf-8')
                
                if schema.startswith('procNFe'):
                    # É a NF-e completa!
                    logger.success(f"XML completo da NF-e obtido para {chave}")
                    self._salvar_xml(chave, xml_str)
                    return xml_str
                    
                elif schema.startswith('resNFe'):
                    # Apenas o resumo, precisamos dar Ciência da Emissão
                    logger.info(f"Obtido apenas Resumo (resNFe) para {chave}. Necessário dar Ciência.")
                    sucesso = self.registrar_ciencia_emissao(chave)
                    if sucesso:
                        logger.info("Aguardando 3s antes de consultar novamente...")
                        time.sleep(3)
                        return self.buscar_xml_por_chave(chave) # Tenta novamente
                    
            logger.warning(f"Não foi possível obter o XML procNFe para a chave {chave}")
            return None
            
        except Exception as e:
            logger.error(f"Erro ao buscar XML na Sefaz: {e}")
            return None

    def _salvar_xml(self, chave: str, xml_str: str):
        output_dir = os.getenv("OUTPUT_DIR", "./output")
        xml_dir = os.path.join(output_dir, "xmls")
        os.makedirs(xml_dir, exist_ok=True)
        
        filepath = os.path.join(xml_dir, f"{chave}.xml")
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(xml_str)
        except Exception as e:
            logger.error(f"Falha ao salvar o XML {chave}.xml no disco: {e}")
