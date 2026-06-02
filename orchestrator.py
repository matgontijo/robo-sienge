import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from apscheduler.schedulers.blocking import BlockingScheduler
from loguru import logger
from lxml import etree

import config
from models import Titulo, NFeData, Boleto
from modules.sienge_client import SiengeClient
from modules.attachment_reader import AttachmentReader
from modules.sefaz_client import SefazClient
from modules.danfe_generator import DanfeGenerator
from modules.santander_client import SantanderClient
from modules.reconciler import Reconciler
from modules.report_generator import ReportGenerator
from modules.notifier import Notifier
from dashboard import database as db
import datetime

# Dicionário global para controle de aborto
_abort_flags = {}

def abortar_execucao(execucao_id: int):
    _abort_flags[execucao_id] = True

def _parse_xml_to_nfedata(xml_str: str) -> NFeData:
    """Extrai os dados essenciais do XML da NF-e crú para o NFeData"""
    root = etree.fromstring(xml_str.encode('utf-8'))
    ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}
    
    infNFe = root.find(".//nfe:infNFe", namespaces=ns)
    chave = infNFe.get("Id", "").replace("NFe", "")
    
    ide = infNFe.find("nfe:ide", namespaces=ns)
    numero = ide.find("nfe:nNF", namespaces=ns).text
    serie = ide.find("nfe:serie", namespaces=ns).text
    data_emissao = date.fromisoformat(ide.find("nfe:dhEmi", namespaces=ns).text[:10])
    
    emit = infNFe.find("nfe:emit", namespaces=ns)
    cnpj_emitente = emit.find("nfe:CNPJ", namespaces=ns).text
    nome_emitente = emit.find("nfe:xNome", namespaces=ns).text
    
    total = infNFe.find("nfe:total/nfe:ICMSTot", namespaces=ns)
    valor_total = float(total.find("nfe:vNF", namespaces=ns).text)
    valor_liquido = valor_total # Em vrios casos, valor liquido = vNF. O Sienge vai ter descontos retidos j, vamos considerar vNF.
    
    return NFeData(
        chave=chave,
        cnpj_emitente=cnpj_emitente,
        nome_emitente=nome_emitente,
        valor_total=valor_total,
        valor_liquido=valor_liquido,
        data_emissao=data_emissao,
        numero_nfe=numero,
        serie=serie
    )

def processar_titulo(
    titulo: Titulo, 
    sienge_cli: SiengeClient, 
    reader: AttachmentReader, 
    sefaz_cli: SefazClient, 
    danfe_gen: DanfeGenerator
) -> dict:
    """
    Pipeline individual para cada título rodando em thread separada.
    Retorna dicionrio com { 'titulo': Titulo, 'nfe': NFeData ou None, 'erro': msg de erro ou None }
    """
    logger.info(f"Processando Título {titulo.numero} (ID: {titulo.id})")
    try:
        # a. Baixar anexo
        titulo.attachment_bytes = sienge_cli.baixar_anexo(titulo.id)
        if not titulo.attachment_bytes:
            return {"titulo": titulo, "nfe": None, "erro": None} # Sem erro fatal, apenas no tem anexo
            
        # b. Extrair chave NF-e
        titulo.chave_nfe = reader.extrair_chave_nfe(titulo.attachment_bytes)
        if not titulo.chave_nfe:
            return {"titulo": titulo, "nfe": None, "erro": None} # Ilegvel, reconciliador pegar
            
        # c. Buscar XML na SEFAZ
        xml_str = sefaz_cli.buscar_xml_por_chave(titulo.chave_nfe)
        if not xml_str:
            # Reconciliador tambm apontar falta ou SEFAZ falhou (logado)
            return {"titulo": titulo, "nfe": None, "erro": None}
            
        titulo.nfe_xml = xml_str
        nfe_data = _parse_xml_to_nfedata(xml_str)
        
        # d. Gerar DANFE
        import os
        danfe_path = os.path.join(config.OUTPUT_DIR, "danfes", f"{titulo.chave_nfe}.pdf")
        if not os.path.exists(danfe_path):
            danfe_path = danfe_gen.gerar_pdf(xml_str, danfe_path)
            
        titulo.danfe_path = danfe_path
        nfe_data.danfe_path = danfe_path
        
        return {"titulo": titulo, "nfe": nfe_data, "erro": None}
        
    except Exception as e:
        logger.error(f"Erro inesperado no processamento do título {titulo.id}: {e}")
        return {"titulo": titulo, "nfe": None, "erro": str(e)}

def executar_ciclo(data_inicio: date = None, data_fim: date = None, iniciado_por: str = "scheduler") -> int:
    start_time = time.time()
    
    if not data_inicio:
        data_inicio = date.today()
    if not data_fim:
        data_fim = date.today()
        
    # 1. Criar registro de execução no banco
    execucao = db.criar_execucao(data_inicio, data_fim, iniciado_por)
    exec_id = execucao.id
    _abort_flags[exec_id] = False
    
    # 2. Wrapper para log
    def log(level, modulo, msg):
        logger.log(level, msg)
        db.registrar_log(exec_id, level, modulo, msg)

    log("INFO", "orchestrator", "="*50)
    log("INFO", "orchestrator", f"INICIANDO CICLO (ID: {exec_id})")
    log("INFO", "orchestrator", "="*50)
        
    erros_execucao = []
        
    try:
        # Inicializar todos os clientes
        sienge_cli = SiengeClient(config.SIENGE_BASE_URL, config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
        reader = AttachmentReader(config.ANTHROPIC_API_KEY)
        sefaz_cli = SefazClient(config.SEFAZ_CERT_PATH, config.SEFAZ_CERT_PASSWORD, config.SEFAZ_CNPJ, config.SEFAZ_AMBIENTE)
        danfe_gen = DanfeGenerator()
        santander_cli = SantanderClient(config.SANTANDER_CLIENT_ID, config.SANTANDER_CLIENT_SECRET, config.SANTANDER_CERT_PATH, config.SANTANDER_CERT_PASSWORD, config.SANTANDER_ENV)
        reconciler = Reconciler()
        report_gen = ReportGenerator()
        
        notifier = None
        if config.SMTP_HOST:
            notifier = Notifier(
                config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD, 
                config.NOTIF_EMAIL_DESTINO, config.TEAMS_WEBHOOK_URL
            )
            
        # Buscar títulos do Sienge
        titulos = sienge_cli.listar_titulos(data_inicio, data_fim)
        log("INFO", "sienge", f"Total de títulos encontrados: {len(titulos)}")
        
        # Processar títulos em paralelo (Max 5 workers)
        resultados_processamento = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_titulo = {
                executor.submit(processar_titulo, t, sienge_cli, reader, sefaz_cli, danfe_gen): t for t in titulos
            }
            
            for future in as_completed(future_to_titulo):
                if _abort_flags.get(exec_id):
                    log("WARNING", "orchestrator", "Aborto solicitado pelo usuário.")
                    break
                    
                t = future_to_titulo[future]
                try:
                    res = future.result()
                    resultados_processamento.append(res)
                except Exception as exc:
                    log("ERROR", "orchestrator", f"Título {t.id} gerou exceção durante execução da thread: {exc}")
                    resultados_processamento.append({"titulo": t, "nfe": None, "erro": str(exc)})
                    
        if _abort_flags.get(exec_id):
            db.atualizar_execucao(exec_id, status="ABORTADO", concluido_em=datetime.datetime.now())
            return exec_id
            
        # Buscar boletos DDA
        try:
            data_fim_dda = data_fim + timedelta(days=7)
            boletos_dda = santander_cli.consultar_dda(data_inicio, data_fim_dda)
            log("INFO", "santander", f"Total de boletos encontrados DDA: {len(boletos_dda)}")
        except Exception as e:
            msg = f"Falha crítica ao consultar DDA Santander: {e}"
            log("ERROR", "santander", msg)
            erros_execucao.append(msg)
            boletos_dda = [] 

        # Motor de Cruzamento (Reconciliação)
        divergencias_finais = []
        titulos_ok = []
        titulos_erro = []
        total_divergencias = 0
        total_criticos = 0
        
        for r in resultados_processamento:
            t = r["titulo"]
            erro = r["erro"]
            nfe = r["nfe"]
            
            if erro:
                titulos_erro.append((t, erro))
                continue
                
            divs = reconciler.reconciliar(t, nfe, boletos_dda)
            if not divs:
                titulos_ok.append(t)
            else:
                divergencias_finais.append((t, divs))
                total_divergencias += len(divs)
                
                # Gravar as divergencias no DB
                for d in divs:
                    if d.criticidade == "CRITICA":
                        total_criticos += 1
                        
                    db.registrar_divergencia(exec_id, {
                        "titulo_id": d.titulo_id,
                        "titulo_numero": d.titulo_numero,
                        "fornecedor_nome": t.fornecedor_nome,
                        "fornecedor_cnpj": t.fornecedor_cnpj,
                        "valor_sienge": t.valor_liquido,
                        "data_vencimento": t.data_vencimento,
                        "tipo": d.tipo,
                        "campo": d.campo,
                        "valor_sienge_campo": d.valor_sienge,
                        "valor_nfe_campo": d.valor_nfe,
                        "valor_boleto_campo": d.valor_boleto,
                        "criticidade": d.criticidade,
                        "danfe_path": d.danfe_path
                    })

        # Gerar Relatório
        relatorio_path = report_gen.gerar(
            divergencias=divergencias_finais,
            titulos_ok=titulos_ok,
            titulos_erro=titulos_erro,
            data_referencia=data_fim,
            output_dir=config.OUTPUT_DIR
        )
        
        # Notificação
        if notifier:
            notifier.enviar_resumo(
                data_referencia=data_fim,
                total_titulos=len(titulos),
                total_divergencias=total_divergencias,
                total_criticos=total_criticos,
                relatorio_path=relatorio_path,
                erros_execucao=erros_execucao
            )
        else:
            log("WARNING", "notifier", "SMTP não configurado. Notificações não enviadas.")

        # Finalizar Execucao no DB
        db.atualizar_execucao(
            exec_id,
            status="CONCLUIDO",
            concluido_em=datetime.datetime.now(),
            total_titulos=len(titulos),
            total_ok=len(titulos_ok),
            total_divergencias=total_divergencias,
            total_criticos=total_criticos,
            total_nao_processados=len(titulos_erro),
            relatorio_path=relatorio_path
        )

        elapsed = time.time() - start_time
        log("INFO", "orchestrator", "="*50)
        log("INFO", "orchestrator", "CICLO FINALIZADO")
        log("INFO", "orchestrator", f"Tempo total       : {elapsed:.2f}s")
        log("INFO", "orchestrator", f"Títulos Lidos     : {len(titulos)}")
        log("INFO", "orchestrator", f"Títulos OK        : {len(titulos_ok)}")
        log("INFO", "orchestrator", f"Com Divergências  : {len(divergencias_finais)} ({total_divergencias} divs, {total_criticos} crit)")
        log("INFO", "orchestrator", f"Não Processados   : {len(titulos_erro)}")
        log("INFO", "orchestrator", "="*50)
        
        return exec_id
        
    except Exception as e:
        msg = f"Erro fatal no ciclo de execução: {e}"
        logger.critical(msg)
        import traceback
        trace = traceback.format_exc()
        logger.critical(trace)
        db.registrar_log(exec_id, "ERROR", "orchestrator", msg)
        
        db.atualizar_execucao(
            exec_id,
            status="ERRO",
            concluido_em=datetime.datetime.now(),
            erro_mensagem=msg
        )
        return exec_id

def agendar() -> None:
    logger.info(f"Agendando execuo diária s {config.CRON_HORA}:{config.CRON_MINUTO}...")
    scheduler = BlockingScheduler()
    
    # Agendamento dirio usando hour e minute do .env
    scheduler.add_job(
        executar_ciclo, 
        'cron', 
        hour=int(config.CRON_HORA), 
        minute=int(config.CRON_MINUTO)
    )
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
