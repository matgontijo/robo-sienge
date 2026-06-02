import os
import asyncio
import threading
from datetime import date
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Response, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import config
from dashboard import database as db
from dashboard.auth import get_current_user
import orchestrator

app = FastAPI(title="Robô Conciliação - Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Diretório para servir estáticos
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

class RunRequest(BaseModel):
    data_inicio: str
    data_fim: str

from pydantic import Extra
class ConfigUpdate(BaseModel, extra=Extra.allow):
    pass

# ---------------------------------------------------------
# Static HTML e CSS/JS (NÃO EXIGE AUTH HTTP NA ROTA DIRETA 
# POIS O JAVASCRIPT CUIDA DA SESSÃO, EXCETO /api)
# ---------------------------------------------------------
@app.get("/")
async def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ---------------------------------------------------------
# Endpoints da API (EXIGEM AUTH)
# ---------------------------------------------------------
api_route = app.router

@app.get("/api/stats", dependencies=[Depends(get_current_user)])
def get_stats():
    return db.get_stats_gerais()

@app.get("/api/execucoes", dependencies=[Depends(get_current_user)])
def listar_execucoes(limit: int = 50):
    return db.get_execucoes(limit)

@app.get("/api/execucoes/{execucao_id}", dependencies=[Depends(get_current_user)])
def obter_execucao(execucao_id: int):
    execucao = db.get_execucao(execucao_id)
    if not execucao:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    
    divergencias = db.get_divergencias(execucao_id)
    return {
        "execucao": execucao,
        "divergencias": divergencias
    }

@app.get("/api/execucoes/{execucao_id}/divergencias", dependencies=[Depends(get_current_user)])
def listar_divergencias(execucao_id: int, criticidade: str = None, q: str = None):
    return db.get_divergencias(execucao_id, criticidade, q)

@app.get("/api/execucoes/{execucao_id}/logs", dependencies=[Depends(get_current_user)])
def listar_logs(execucao_id: int):
    return db.get_logs(execucao_id)

@app.get("/api/execucoes/{execucao_id}/relatorio", dependencies=[Depends(get_current_user)])
def download_relatorio(execucao_id: int):
    execucao = db.get_execucao(execucao_id)
    if not execucao or not execucao.relatorio_path:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")
        
    path = execucao.relatorio_path
    
    # Path traversal protection
    abs_path = os.path.abspath(path)
    abs_output = os.path.abspath(config.OUTPUT_DIR)
    if not abs_path.startswith(abs_output):
        raise HTTPException(status_code=403, detail="Acesso negado")
        
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Arquivo físico não encontrado")
        
    return FileResponse(
        path=abs_path, 
        filename=os.path.basename(abs_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.get("/api/execucoes/{execucao_id}/danfe", dependencies=[Depends(get_current_user)])
def download_danfe(execucao_id: int, path: str):
    # Path traversal protection
    abs_path = os.path.abspath(path)
    abs_output = os.path.abspath(config.OUTPUT_DIR)
    if not abs_path.startswith(abs_output):
        raise HTTPException(status_code=403, detail="Acesso negado")
        
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="Arquivo físico não encontrado")
        
    return FileResponse(path=abs_path, filename=os.path.basename(abs_path), media_type="application/pdf")

@app.post("/api/execucoes/iniciar", dependencies=[Depends(get_current_user)])
def iniciar_execucao(req: RunRequest):
    # Verifica se já tem alguma execução rodando
    execs = db.get_execucoes(limit=10)
    for e in execs:
        if e.status == "RODANDO":
            raise HTTPException(status_code=409, detail="Já existe uma execução em andamento")
            
    d_inicio = date.fromisoformat(req.data_inicio)
    d_fim = date.fromisoformat(req.data_fim)
    
    # Inicia a execução do orchestrator em uma thread separada para não bloquear a API
    def rotina_em_background():
        orchestrator.executar_ciclo(d_inicio, d_fim, iniciado_por="dashboard")
        
    thread = threading.Thread(target=rotina_em_background)
    thread.start()
    
    # Precisamos retornar algo para o frontend, mas o ID só é gerado DENTRO do orquestrador.
    # O ideal era criar a execução aqui e passar o ID pro orquestrador, mas para simplificar
    # vamos aguardar uma fração de segundo até que a thread crie o ID.
    import time
    time.sleep(0.5)
    
    # Pega a ultima (que deve ser a nossa)
    ultima = db.get_execucoes(limit=1)
    if ultima and ultima[0].status == "RODANDO":
        return {"execucao_id": ultima[0].id}
    return {"execucao_id": 0}

@app.post("/api/execucoes/{execucao_id}/abortar", dependencies=[Depends(get_current_user)])
def abortar_execucao(execucao_id: int):
    execucao = db.get_execucao(execucao_id)
    if not execucao:
        raise HTTPException(status_code=404, detail="Execução não encontrada")
    if execucao.status != "RODANDO":
        raise HTTPException(status_code=400, detail="Execução não está rodando")
        
    orchestrator.abortar_execucao(execucao_id)
    return {"status": "Abort signal sent"}

# ---------------------------------------------------------
# Configurações do .env
# ---------------------------------------------------------
import dotenv

@app.get("/api/config", dependencies=[Depends(get_current_user)])
def get_config():
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return {}
    return dotenv.dotenv_values(env_path)

@app.post("/api/config", dependencies=[Depends(get_current_user)])
def update_config(payload: dict):
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        open(env_path, 'a').close()
        
    for k, v in payload.items():
        dotenv.set_key(env_path, k, str(v))
        
    config.reload_config()
    return {"status": "success", "message": "Configurações salvas e recarregadas."}

# ---------------------------------------------------------
# SSE - Server Sent Events (TEMPO REAL)
# O sse_starlette.sse lida com isso elegantemente
# ---------------------------------------------------------
@app.get("/api/stream/{execucao_id}")
async def stream_logs(execucao_id: int, req: Request):
    
    # Valida Auth no cabeçalho ou cookie para o SSE (geralmente enviamos o header de auth)
    # A dependência Depends() falha no SSE nativo do navegador as vezes, por isso
    # o frontend tem que enviar no EventSource se possível ou usar cookies.
    auth = req.headers.get("Authorization")
    if not auth:
        auth = req.query_params.get("token")
        if auth:
            auth = f"Basic {auth}"
            
    if not auth:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    import base64
    try:
        scheme, credentials = auth.split()
        if scheme.lower() != 'basic':
            raise Exception()
        decoded = base64.b64decode(credentials).decode("ascii")
        username, _, password = decoded.partition(":")
        import secrets
        if not (secrets.compare_digest(username, config.DASHBOARD_USER) and 
                secrets.compare_digest(password, config.DASHBOARD_PASSWORD)):
            raise Exception()
    except Exception:
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})

    async def event_generator():
        last_id = 0
        while True:
            # Checa conexao
            if await req.is_disconnected():
                break
                
            # Buscar novos logs
            novos_logs = db.get_logs(execucao_id, last_id=last_id)
            for l in novos_logs:
                data_str = f"[{l.timestamp.strftime('%H:%M:%S')}] {l.level.ljust(8)} | {l.modulo} - {l.mensagem}"
                yield f"event: log\ndata: {data_str}\n\n"
                last_id = l.id
                
            # Verifica se terminou
            e = db.get_execucao(execucao_id)
            if not e or e.status != "RODANDO":
                yield f"event: close\ndata: Fechando stream\n\n"
                break
                
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
