import json
import re
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from apscheduler.schedulers.blocking import BlockingScheduler
from loguru import logger
from lxml import etree

import config
from models import Titulo, NFeData, Boleto
from modules.sienge_client import SiengeClient
from modules.report_parser import ReportParser
from modules.attachment_reader import AttachmentReader, decodificar_linha_digitavel
from modules.receita_client import ReceitaClient
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

# Documentos que o pessoal cola junto da nota, mas que NÃO são nota fiscal
_NAO_E_NOTA = ("proposta", "orcamento", "orçamento", "pedido", "medicao", "medição",
               "contrato", "cotacao", "cotação", "planilha", "email", "e-mail",
               "comprovante", "recibo", "ordem de compra")


def _ler_todos_anexos(reader, anexos: dict, titulo: Titulo) -> dict:
    """Lê a camada de texto de TODOS os anexos do título e decide qual é a nota
    fiscal pelo CONTEÚDO — não pelo nome do arquivo nem pela ordem em que vieram.

    O pessoal anexa medição, pedido, e-mail e contrato junto da nota; o primeiro
    anexo raramente é a NF. Escolhe-se a melhor candidata por pontuação:
      +6 tem chave de NF-e válida | +3 texto diz "nota fiscal"/"danfe"
      +2 traz o CNPJ do credor    | +1 nome do arquivo sugere NF
    O resultado também devolve `digitos` com o fluxo de dígitos de TODOS os
    documentos: se o CNPJ do credor aparece em qualquer anexo, não há alarme.
    """
    itens = anexos.get("anexos") or []
    if not itens:
        return None

    cnpj_raiz = re.sub(r"\D", "", str(titulo.fornecedor_cnpj or ""))[:8]
    lidos, digitos_todos, cnpjs_todos = [], [], []

    for a in itens:
        conteudo = a.get("bytes")
        if not conteudo:
            continue
        try:
            info = reader.extrair_info_texto(conteudo)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao ler anexo {a.get('nome')}: {e}")
            continue
        info["_nome"] = a.get("nome")
        info["_path"] = a.get("path")
        info["_nf_bytes"] = conteudo
        # guarda a linha digitável no próprio anexo: _boleto_da_parcela reaproveita
        # em vez de abrir o PDF de novo (pdfplumber é caro)
        a["_linha_digitavel"] = info.get("linha_digitavel")

        digitos_todos.append(info.get("digitos") or "")
        cnpjs_todos.extend(info.get("cnpjs") or [])

        nome_low = (a.get("nome") or "").lower()
        pontos = 0
        if info.get("chave"):
            pontos += 6                      # chave de NF-e válida: é nota, sem dúvida
        if info.get("parece_nf"):
            pontos += 3                      # o texto se identifica como nota fiscal
        if cnpj_raiz and cnpj_raiz in (info.get("digitos") or ""):
            pontos += 2                      # traz o CNPJ do credor
        if a.get("tipo") == "nf":
            pontos += 2                      # nome do arquivo sugere NF
        if any(k in nome_low for k in _NAO_E_NOTA):
            pontos -= 4                      # proposta/pedido/medição não são nota
        info["_pontos"] = pontos
        lidos.append(info)

    if not lidos:
        return None

    melhor = max(lidos, key=lambda i: i["_pontos"])
    # dígitos e CNPJs de TODOS os anexos: o confronto de CNPJ olha o conjunto
    melhor = dict(melhor)
    melhor["digitos"] = "|".join(digitos_todos)
    melhor["cnpjs"] = list(dict.fromkeys(cnpjs_todos))
    melhor["_nf_path"] = melhor.get("_path")
    melhor["_total_anexos"] = len(lidos)
    # confiável se QUALQUER anexo tiver texto legível
    melhor["tem_texto"] = any(i.get("tem_texto") for i in lidos)
    melhor["texto_confiavel"] = any(i.get("texto_confiavel") for i in lidos)
    logger.info(
        f"Título {titulo.numero}: {len(lidos)} anexo(s) lidos; "
        f"NF escolhida por conteúdo: {melhor.get('_nome')} (score {melhor['_pontos']})"
    )
    return melhor


def _boleto_da_parcela(reader, anexos: dict, titulo: Titulo, info_pagamento) -> dict:
    """Entre os anexos do título, escolhe o boleto que pertence À PARCELA do ciclo.

    O anexo no Sienge fica preso ao TÍTULO, não à parcela: um título em 12x traz
    os 12 boletos no mesmo pacote. A parcela então é identificada pelo próprio
    documento, do critério mais forte para o mais fraco:

      1. linha digitável do anexo == a cadastrada naquela parcela  (exato)
      2. vencimento e valor decodificados da linha == os da parcela (FEBRABAN)
      3. só o vencimento bate, e é o único anexo com aquele vencimento
      4. só existe um boleto anexado — não há o que confundir (não confirmado)

    Devolve um dicionário com `situacao`:
      identificado -> achou o boleto desta parcela (path preenchido)
      sem_boleto   -> não há boleto entre os anexos (Pix/TED, por exemplo): não
                      há o que desambiguar, o dossiê leva só a nota
      ambiguo      -> há boletos, mas nenhum pôde ser atribuído à parcela; o
                      dossiê leva TODOS os anexos e marca para conferência
    Devolve None quando nem dá para avaliar (sem anexos ou sem leitor).
    """
    itens = (anexos or {}).get("anexos") or []
    if not itens or reader is None:
        return None

    linha_parcela = re.sub(r"\D", "", str(getattr(info_pagamento, "linha_digitavel", "") or ""))
    venc_parcela = getattr(info_pagamento, "vencimento", None) or titulo.data_vencimento
    valor_parcela = (getattr(info_pagamento, "valor", None)
                     or titulo.valor_liquido or titulo.valor_nominal)

    candidatos = []
    for a in itens:
        linha = a.get("_linha_digitavel")
        if linha is None and a.get("bytes"):
            # anexo não passou por _ler_todos_anexos (fluxo sem relatório)
            try:
                linha = (reader.extrair_info_texto(a["bytes"]) or {}).get("linha_digitavel")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Falha ao ler {a.get('nome')} procurando o boleto: {e}")
                linha = None
        linha = re.sub(r"\D", "", str(linha or ""))
        if not linha:
            continue
        dec = decodificar_linha_digitavel(linha) or {}
        candidatos.append({"path": a.get("path"), "nome": a.get("nome"), "linha": linha,
                           "venc": dec.get("vencimento"), "valor": dec.get("valor")})

    if not candidatos:
        logger.info(f"Título {titulo.numero}/{titulo.parcela}: nenhum boleto entre os anexos.")
        return {"situacao": "sem_boleto", "path": None, "nome": None,
                "criterio": "nenhum boleto anexado", "confiavel": True, "candidatos": 0}

    def _achado(c, criterio, confiavel=True):
        logger.info(f"Título {titulo.numero}/{titulo.parcela}: boleto da parcela = "
                    f"{c['nome']} (por {criterio})")
        return {"situacao": "identificado", "path": c["path"], "nome": c["nome"],
                "criterio": criterio, "confiavel": confiavel, "candidatos": len(candidatos)}

    # 1) linha digitável idêntica à cadastrada na parcela
    if linha_parcela:
        for c in candidatos:
            if c["linha"] == linha_parcela:
                return _achado(c, "linha digitável da parcela")

    # 2) vencimento + valor decodificados da própria linha digitável
    if venc_parcela and valor_parcela:
        for c in candidatos:
            if (c["venc"] == venc_parcela and c["valor"]
                    and abs(c["valor"] - float(valor_parcela)) <= 0.02):
                return _achado(c, "vencimento e valor do boleto")

    # 3) só o vencimento, e desde que não haja empate
    if venc_parcela:
        mesmo_venc = [c for c in candidatos if c["venc"] == venc_parcela]
        if len(mesmo_venc) == 1:
            return _achado(mesmo_venc[0], "vencimento do boleto")

    # 4) um único boleto anexado: não dá para confundir, mas nada confirma a parcela
    if len(candidatos) == 1:
        return _achado(candidatos[0], "único boleto anexado", confiavel=False)

    logger.warning(f"Título {titulo.numero}/{titulo.parcela}: {len(candidatos)} boletos anexados "
                   f"e nenhum pôde ser atribuído à parcela — dossiê levará todos.")
    return {"situacao": "ambiguo", "path": None, "nome": None,
            "criterio": f"{len(candidatos)} boletos, nenhum atribuível à parcela",
            "confiavel": False, "candidatos": len(candidatos)}


def processar_titulo(
    titulo: Titulo,
    sienge_cli: SiengeClient,
    reader: AttachmentReader,
    sefaz_cli: SefazClient,
    danfe_gen: DanfeGenerator,
    from_report: bool = False,
    data_inicio: date = None,
    data_fim: date = None
) -> dict:
    """
    Pipeline individual para cada título rodando em thread separada.
    Retorna dicionrio com { 'titulo': Titulo, 'nfe': NFeData ou None,
    'boleto_anexo': Boleto ou None, 'erro': msg de erro ou None }

    Quando `from_report=True` (título veio do relatório de Fluxo de Caixa):
      - resolve o ID interno do título no Sienge (para baixar anexos);
      - tenta obter a NF pela API do Sienge — se vier a chave de acesso, PULA o OCR;
      - quando há boleto no anexo, extrai seus dados para cruzamento.
    """
    logger.info(f"Processando Título {titulo.numero} (ID: {titulo.id}, from_report={from_report})")
    boleto_anexo = None
    info_pagamento = None
    retencoes = {}
    destacados = {}
    nfe_data = None
    anexos = None
    anexos_info = None
    nf_texto = None
    try:
        # 0. Título do relatório: resolver o ID interno do título no Sienge
        if from_report and sienge_cli is not None and titulo.id is None:
            titulo.id = sienge_cli.resolver_titulo_por_numero(
                titulo.numero, titulo.parcela, data_inicio, data_fim, titulo=titulo
            )

        # a. Baixar anexos (somente se já temos o ID interno e o Sienge disponível)
        if titulo.id is not None and sienge_cli is not None:
            # Baixa TODOS os anexos do título — em qualquer fluxo, com ou sem
            # relatório — salva na pasta do título e ainda destaca NF e boleto.
            import os
            pasta = os.path.join(
                config.OUTPUT_DIR, "anexos",
                f"{str(titulo.numero or titulo.id or 'sem').strip()}_{(titulo.parcela or '0')}"
            )
            anexos = sienge_cli.baixar_anexos_titulo(titulo.id, pasta)
            titulo.attachment_bytes = anexos.get("nf_bytes") or anexos.get("boleto_bytes")
            anexos_info = {
                "pasta": pasta,
                "nf_path": anexos.get("nf_path"),
                "boleto_path": anexos.get("boleto_path"),
                "arquivos": [{"nome": a.get("nome"), "path": a.get("path"), "tipo": a.get("tipo")}
                             for a in anexos.get("anexos", [])],
            }

            if from_report:
                # Camada de texto de TODOS os anexos (sem OCR).
                # O primeiro anexo não é necessariamente a nota: quem manda é o
                # conteúdo. Lemos todos, escolhemos a NF de verdade e juntamos os
                # dígitos de todos os documentos para o confronto de CNPJ.
                if reader is not None:
                    nf_texto = _ler_todos_anexos(reader, anexos, titulo)
                    if nf_texto:
                        if not titulo.chave_nfe and nf_texto.get("chave"):
                            titulo.chave_nfe = nf_texto["chave"]
                        # os bytes da NF escolhida vão para o título; o dicionário
                        # não carrega PDF adiante (o ciclo guarda todos os resultados)
                        nf_bytes = nf_texto.pop("_nf_bytes", None)
                        if nf_bytes:
                            titulo.attachment_bytes = nf_bytes
                        if nf_texto.get("_nf_path"):
                            anexos_info["nf_path"] = nf_texto["_nf_path"]

                # a.1 Boleto: prioriza o anexo classificado como boleto
                if reader is not None:
                    bol_bytes = anexos.get("boleto_bytes") or titulo.attachment_bytes
                    if bol_bytes:
                        boleto_anexo = reader.extrair_boleto(bol_bytes)

        # a.2 Dados de pagamento (anti-fraude) e retenções do título — fluxo do relatório
        if from_report and titulo.id is not None and sienge_cli is not None:
            info_pagamento = sienge_cli.consultar_informacoes_pagamento(titulo.id, titulo.parcela)
            if info_pagamento and info_pagamento.forma_pagamento and not titulo.forma_pagamento:
                titulo.forma_pagamento = info_pagamento.forma_pagamento
            retencoes = sienge_cli.consultar_impostos_titulo(titulo.id) or {}

        # a.2.1 Qual dos boletos anexados é o DESTA parcela. Só dá para decidir
        # aqui: a linha digitável cadastrada vem do info_pagamento, acima.
        if anexos_info is not None and anexos:
            escolha = _boleto_da_parcela(reader, anexos, titulo, info_pagamento)
            anexos_info["boleto_parcela"] = escolha
            if escolha:
                anexos_info["boleto_path"] = escolha["path"]

        # a.3 CNPJ do credor cadastrado no título (/creditors) — referência oficial
        # para o confronto do destino do pagamento (TED/Pix) x credor
        if from_report and sienge_cli is not None and titulo.credor_id and not titulo.fornecedor_cnpj:
            credor = sienge_cli.consultar_credor(credor_id=titulo.credor_id)
            if credor:
                titulo.fornecedor_cnpj = credor.get("cnpj") or credor.get("cpf") or ""
                if not titulo.fornecedor_nome:
                    titulo.fornecedor_nome = credor.get("name") or credor.get("tradeName") or ""

        # b. Nota fiscal via NF-e de Produto (/nfes) — fonte primária (substitui Sefaz)
        if from_report and sienge_cli is not None:
            res_nfe = sienge_cli.resolver_nfe_produto_para_titulo(titulo, data_inicio, data_fim)
            if res_nfe and res_nfe.get("nfe_data"):
                nfe_data = res_nfe["nfe_data"]
                destacados = res_nfe.get("destacados") or {}
                # CNPJ da nota como referência do fornecedor p/ o cruzamento
                if not titulo.fornecedor_cnpj and titulo.nf_cnpj_emitente:
                    titulo.fornecedor_cnpj = titulo.nf_cnpj_emitente

        # c. Fallback: OCR da chave no anexo + Sefaz, se o /nfes não trouxe a nota.
        # Chave de acesso (44 dígitos) só existe em NF-e de PRODUTO — não gasta
        # OCR caçando chave em NFSE/medição/adiantamento.
        if nfe_data is None:
            tipo_doc = (titulo.tipo_documento or "").upper()
            pode_ter_chave = tipo_doc.startswith("NF") and "NFSE" not in tipo_doc
            if not titulo.chave_nfe and titulo.attachment_bytes and reader is not None and pode_ter_chave:
                titulo.chave_nfe = reader.extrair_chave_nfe(titulo.attachment_bytes)
            if titulo.chave_nfe and sefaz_cli is not None:
                try:
                    xml_str = sefaz_cli.buscar_xml_por_chave(titulo.chave_nfe)
                    if xml_str:
                        titulo.nfe_xml = xml_str
                        nfe_data = _parse_xml_to_nfedata(xml_str)
                        import os
                        danfe_path = os.path.join(config.OUTPUT_DIR, "danfes", f"{titulo.chave_nfe}.pdf")
                        if not os.path.exists(danfe_path):
                            danfe_path = danfe_gen.gerar_pdf(xml_str, danfe_path)
                        titulo.danfe_path = danfe_path
                        nfe_data.danfe_path = danfe_path
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Sefaz falhou para a chave {titulo.chave_nfe}: {e}")

        return {"titulo": titulo, "nfe": nfe_data, "boleto_anexo": boleto_anexo,
                "info_pagamento": info_pagamento, "retencoes": retencoes,
                "destacados": destacados, "anexos_info": anexos_info,
                "nf_texto": nf_texto, "erro": None}

    except Exception as e:
        logger.error(f"Erro inesperado no processamento do título {titulo.id}: {e}")
        return {"titulo": titulo, "nfe": None, "boleto_anexo": boleto_anexo,
                "info_pagamento": info_pagamento, "retencoes": retencoes,
                "destacados": destacados, "anexos_info": anexos_info,
                "nf_texto": nf_texto, "erro": str(e)}

def _montar_dossie(exec_id: int, resultados: list) -> list:
    """
    Copia, para UMA pasta única (output/dossie/ciclo_N), os documentos DA PARCELA
    que está sendo paga: a nota fiscal (escolhida pelo conteúdo) e o boleto
    daquela parcela (ver _boleto_da_parcela). Boletos das outras parcelas,
    medições e planilhas ficam de fora — seguem salvos em output/anexos/.

    Exceção: quando há boletos anexados mas nenhum pôde ser atribuído à parcela,
    o título leva TODOS os anexos e é marcado como "CONFERIR" no checklist — é
    preferível ver documento a mais do que ficar sem o certo.
    """
    import os
    import re
    import shutil

    def _slug(s, n=28):
        return re.sub(r"[^A-Za-z0-9]+", "_", str(s or "")).strip("_")[:n] or "X"

    pasta = os.path.join(config.OUTPUT_DIR, "dossie", f"ciclo_{exec_id}")
    os.makedirs(pasta, exist_ok=True)

    rows = []
    for r in resultados:
        t = r["titulo"]
        info = r.get("anexos_info") or {}
        forma = ((r.get("info_pagamento").forma_pagamento if r.get("info_pagamento") else "")
                 or t.forma_pagamento or "").upper()
        tipo_doc = (t.tipo_documento or "").upper()

        nf_esperada = "NF" in tipo_doc          # NFE/NFSE/NFS -> nota esperada
        boleto_esperado = "BOLETO" in forma

        base = f"{t.numero}-{t.parcela or '1'}_{_slug(t.fornecedor_nome)}"
        nf_origem = info.get("nf_path")
        escolha = info.get("boleto_parcela") or {}
        situacao = escolha.get("situacao")
        ambiguo = situacao in (None, "ambiguo")   # None = nem deu para avaliar
        boleto_origem = escolha.get("path") if situacao == "identificado" else (
            None if situacao == "sem_boleto" else info.get("boleto_path"))

        if ambiguo:
            # Não deu para dizer qual boleto é o desta parcela: leva tudo.
            origens = [a.get("path") for a in (info.get("arquivos") or []) if a.get("path")]
            for extra in (nf_origem, boleto_origem):
                if extra and extra not in origens:
                    origens.append(extra)
        else:
            # Só os documentos DESTA parcela.
            origens = [o for o in (nf_origem, boleto_origem) if o]

        arq_nf = arq_boleto = None
        copiados = []
        for i, origem in enumerate(origens, start=1):
            if not os.path.exists(origem):
                logger.warning(f"Dossiê: anexo não encontrado no disco: {origem}")
                continue

            papeis = [p for p, o in (("NF", nf_origem), ("BOLETO", boleto_origem)) if origem == o]
            marca = "-".join(papeis) if papeis else "ANEXO"
            nome_orig = _slug(os.path.splitext(os.path.basename(origem))[0], 30)
            ext = os.path.splitext(origem)[1] or ".pdf"
            destino = os.path.join(pasta, f"{base}_{i:02d}_{marca}_{nome_orig}{ext}")
            try:
                shutil.copy2(origem, destino)
            except OSError as e:
                logger.warning(f"Dossiê: falha ao copiar {origem}: {e}")
                continue

            copiados.append(os.path.basename(destino))
            if origem == nf_origem and arq_nf is None:
                arq_nf = os.path.basename(destino)
            if origem == boleto_origem and arq_boleto is None:
                arq_boleto = os.path.basename(destino)

        status_nf = "✓" if arq_nf else ("FALTA" if nf_esperada else "n/a")
        if ambiguo and (info.get("arquivos") or []):
            status_boleto = "CONFERIR"          # levou tudo; a parcela não foi identificada
        elif boleto_esperado:
            status_boleto = "✓" if arq_boleto else "FALTA"
        else:
            status_boleto = "n/a"
        rows.append({
            "numero": t.numero, "parcela": t.parcela, "fornecedor": t.fornecedor_nome,
            "tipo_doc": t.tipo_documento, "forma": forma.title() if forma else "",
            "nf": status_nf, "boleto": status_boleto,
            "arq_nf": arq_nf, "arq_boleto": arq_boleto,
            "boleto_criterio": escolha.get("criterio") or "",
            "arquivos": copiados,
            "total_copiados": len(copiados),
            "total_anexos": len(info.get("arquivos") or []),
        })
    logger.success(f"Dossiê montado em {pasta}: "
                   f"{sum(x['total_copiados'] for x in rows)} arquivos de "
                   f"{sum(1 for x in rows if x['total_copiados'])} títulos, "
                   f"{sum(1 for x in rows if x['nf'] == 'FALTA')} sem NF, "
                   f"{sum(1 for x in rows if x['boleto'] == 'FALTA')} sem boleto, "
                   f"{sum(1 for x in rows if x['boleto'] == 'CONFERIR')} com parcela não identificada.")
    return rows

def executar_ciclo(data_inicio: date = None, data_fim: date = None, iniciado_por: str = "scheduler", relatorio_path: str = None) -> int:
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
        # Inicializar os clientes — cada um de forma resiliente: se um serviço não
        # estiver configurado (ex.: certificado ausente), o pipeline degrada e segue
        # com os demais, em vez de abortar o ciclo inteiro.
        def _init_cliente(nome, fabrica):
            try:
                return fabrica()
            except Exception as e:  # noqa: BLE001
                log("WARNING", nome, f"{nome} indisponível (seguindo sem ele): {e}")
                return None

        sienge_cli = _init_cliente("sienge", lambda: SiengeClient(
            config.SIENGE_BASE_URL, config.SIENGE_USERNAME, config.SIENGE_PASSWORD))
        import os as _os
        reader = _init_cliente("ocr", lambda: AttachmentReader(
            config.ANTHROPIC_API_KEY, gemini_api_key=config.GEMINI_API_KEY,
            gemini_model=config.GEMINI_MODEL,
            cache_path=_os.path.join(config.OUTPUT_DIR, "ocr_cache.json")))
        sefaz_cli = _init_cliente("sefaz", lambda: SefazClient(
            config.SEFAZ_CERT_PATH, config.SEFAZ_CERT_PASSWORD, config.SEFAZ_CNPJ, config.SEFAZ_AMBIENTE))
        danfe_gen = DanfeGenerator()
        santander_cli = _init_cliente("santander", lambda: SantanderClient(
            config.SANTANDER_CLIENT_ID, config.SANTANDER_CLIENT_SECRET, config.SANTANDER_CERT_PATH,
            config.SANTANDER_CERT_PASSWORD, config.SANTANDER_ENV))
        reconciler = Reconciler()
        report_gen = ReportGenerator()
        receita = _init_cliente("receita", lambda: ReceitaClient(
            cache_path=_os.path.join(config.OUTPUT_DIR, "cnpj_regime.json")))
        
        notifier = None
        if config.SMTP_HOST:
            notifier = Notifier(
                config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD, 
                config.NOTIF_EMAIL_DESTINO, config.TEAMS_WEBHOOK_URL
            )
            
        # Fonte dos títulos: relatório de Fluxo de Caixa (se informado) ou API do Sienge
        from_report = bool(relatorio_path)
        if from_report:
            log("INFO", "report", f"Lendo títulos do relatório de Fluxo de Caixa: {relatorio_path}")
            titulos = ReportParser().parse(relatorio_path)
            log("INFO", "report", f"Títulos conferíveis no relatório: {len(titulos)}")
        else:
            titulos = sienge_cli.listar_titulos(data_inicio, data_fim)
            log("INFO", "sienge", f"Total de títulos encontrados: {len(titulos)}")

        # Processar títulos em paralelo (Max 5 workers)
        resultados_processamento = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_titulo = {
                executor.submit(
                    processar_titulo, t, sienge_cli, reader, sefaz_cli, danfe_gen,
                    from_report, data_inicio, data_fim
                ): t for t in titulos
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
            
        # Buscar boletos DDA (se o Santander estiver configurado)
        boletos_dda = []
        if santander_cli is None:
            log("WARNING", "santander", "Santander não configurado; conciliação DDA será pulada.")
        else:
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
        pagamentos_rows = []
        total_divergencias = 0
        total_criticos = 0
        dda_disponivel = santander_cli is not None

        def _digitos(v):
            import re as _re
            return _re.sub(r"\D", "", str(v or ""))

        for r in resultados_processamento:
            t = r["titulo"]
            erro = r["erro"]
            nfe = r["nfe"]
            boleto_anexo = r.get("boleto_anexo")
            info_pagamento = r.get("info_pagamento")
            retencoes = r.get("retencoes") or {}
            destacados = r.get("destacados") or {}

            # Aba "Pagamentos": destino cadastrado na parcela x CNPJ do credor
            if info_pagamento is not None:
                cnpj_credor = _digitos(t.fornecedor_cnpj)
                cnpj_destino = _digitos(info_pagamento.cnpj_destino())
                if cnpj_destino and cnpj_credor:
                    confronto = "OK" if cnpj_destino == cnpj_credor else "DIVERGENTE"
                elif info_pagamento.linha_digitavel:
                    confronto = "BOLETO (beneficiário via DDA/anexo)"
                else:
                    confronto = "NAO VERIFICAVEL"

                # Banco emissor e valor embutidos na linha digitável (FEBRABAN)
                from modules.attachment_reader import decodificar_linha_digitavel
                from modules.reconciler import _BANCOS
                banco_boleto = valor_boleto = None
                linha_dig = _digitos(info_pagamento.linha_digitavel)
                if len(linha_dig) >= 3:
                    cod = linha_dig[:3]
                    banco_boleto = f"{cod} - {_BANCOS.get(cod, 'outro')}"
                    dec = decodificar_linha_digitavel(linha_dig)
                    if dec and dec.get("valor"):
                        valor_boleto = dec["valor"]
                pagamentos_rows.append({
                    "numero": t.numero, "parcela": info_pagamento.parcela or t.parcela,
                    "fornecedor": t.fornecedor_nome, "cnpj_credor": t.fornecedor_cnpj,
                    "forma": info_pagamento.forma_pagamento,
                    "valor": info_pagamento.valor,
                    "vencimento": info_pagamento.vencimento,
                    "tipo_chave_pix": info_pagamento.tipo_chave_pix,
                    "chave_pix": info_pagamento.chave_pix,
                    "banco": info_pagamento.banco, "agencia": info_pagamento.agencia,
                    "conta": info_pagamento.conta,
                    "titular": info_pagamento.titular_nome or info_pagamento.beneficiario_nome,
                    "cnpj_destino": info_pagamento.beneficiario_cnpj or info_pagamento.titular_cnpj
                                    or (info_pagamento.chave_pix if (info_pagamento.tipo_chave_pix or "").upper() == "CNPJ" else None),
                    "linha_digitavel": info_pagamento.linha_digitavel,
                    "banco_boleto": banco_boleto,
                    "valor_boleto": valor_boleto,
                    "confronto": confronto,
                    "retencoes": json.dumps(retencoes, ensure_ascii=False) if retencoes else None,
                    "liquido_calc": (float(info_pagamento.valor) - sum(float(v or 0) for v in (retencoes or {}).values()))
                                    if info_pagamento.valor and (not info_pagamento.total_parcelas or info_pagamento.total_parcelas == 1)
                                    else None,
                })

            if erro:
                titulos_erro.append((t, erro))
                continue

            # NF que declara "optante pelo Simples" alimenta o cache de regime
            nf_texto_r = r.get("nf_texto")
            if receita is not None and nf_texto_r and nf_texto_r.get("declara_simples") is not None:
                receita.registrar_hint(t.fornecedor_cnpj, nf_texto_r["declara_simples"])

            divs = reconciler.reconciliar(
                t, nfe, boletos_dda, boleto_anexo,
                info_pagamento=info_pagamento,
                impostos_destacados=destacados, retencoes=retencoes,
                dda_disponivel=dda_disponivel,
                ocr_disponivel=reader is not None and reader.provider is not None,
                nf_texto=nf_texto_r,
                consultar_simples=receita.consultar_simples if receita is not None else None,
            )

            # regime conhecido (cache) vai para o cartão da Conferência
            if receita is not None and pagamentos_rows and pagamentos_rows[-1].get("numero") == t.numero:
                simples = receita.regime_conhecido(t.fornecedor_cnpj)
                if simples is not None:
                    pagamentos_rows[-1]["regime"] = "Simples Nacional" if simples else "Regime normal"
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

        # Persiste os dados de pagamento no banco (tela de revisão do painel)
        try:
            db.registrar_pagamentos(exec_id, pagamentos_rows)
        except Exception as e:  # noqa: BLE001
            log("WARNING", "dashboard", f"Falha ao gravar pagamentos no banco: {e}")

        # Dossiê: pasta única só com NF/boleto de cada título + checklist
        dossie_rows = []
        if from_report:
            try:
                dossie_rows = _montar_dossie(exec_id, resultados_processamento)
            except Exception as e:  # noqa: BLE001
                log("WARNING", "dossie", f"Falha ao montar o dossiê: {e}")

        # Gerar Relatório
        relatorio_path = report_gen.gerar(
            divergencias=divergencias_finais,
            titulos_ok=titulos_ok,
            titulos_erro=titulos_erro,
            data_referencia=data_fim,
            output_dir=config.OUTPUT_DIR,
            pagamentos=pagamentos_rows,
            dossie=dossie_rows
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

def agendar(relatorio_path: str = None) -> None:
    logger.info(f"Agendando execuo diária s {config.CRON_HORA}:{config.CRON_MINUTO}...")
    scheduler = BlockingScheduler()

    # Agendamento dirio usando hour e minute do .env
    scheduler.add_job(
        executar_ciclo,
        'cron',
        hour=int(config.CRON_HORA),
        minute=int(config.CRON_MINUTO),
        kwargs={"relatorio_path": relatorio_path}
    )
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
