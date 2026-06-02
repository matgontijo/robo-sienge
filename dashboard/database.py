from datetime import datetime, date
from typing import List, Dict, Any, Optional
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Date, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.sql import func
from passlib.context import CryptContext
import config

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

Base = declarative_base()

class Usuario(Base):
    __tablename__ = "usuarios"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String) # ADMIN | OPERADOR | LEITURA
    criado_em = Column(DateTime, default=datetime.now)

class Execucao(Base):
    __tablename__ = "execucoes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    iniciado_em = Column(DateTime, default=datetime.now)
    concluido_em = Column(DateTime, nullable=True)
    status = Column(String) # RODANDO | CONCLUIDO | ERRO | ABORTADO
    periodo_inicio = Column(Date)
    periodo_fim = Column(Date)
    total_titulos = Column(Integer, default=0)
    total_ok = Column(Integer, default=0)
    total_divergencias = Column(Integer, default=0)
    total_criticos = Column(Integer, default=0)
    total_nao_processados = Column(Integer, default=0)
    relatorio_path = Column(String, nullable=True)
    erro_mensagem = Column(String, nullable=True)
    iniciado_por = Column(String, default="scheduler") # "scheduler" | "dashboard" | "cli"

    divergencias = relationship("Divergencia", back_populates="execucao", cascade="all, delete-orphan")
    logs = relationship("LogExecucao", back_populates="execucao", cascade="all, delete-orphan")

class Divergencia(Base):
    __tablename__ = "divergencias"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id"))
    titulo_id = Column(Integer)
    titulo_numero = Column(String)
    fornecedor_nome = Column(String)
    fornecedor_cnpj = Column(String)
    valor_sienge = Column(Float)
    data_vencimento = Column(Date)
    tipo = Column(String) # CNPJ_DIVERGENTE | VALOR_DIVERGENTE | etc.
    campo = Column(String)
    valor_sienge_campo = Column(String)
    valor_nfe_campo = Column(String)
    valor_boleto_campo = Column(String)
    criticidade = Column(String) # CRITICA | ATENCAO | INFO
    danfe_path = Column(String, nullable=True)
    criado_em = Column(DateTime, default=datetime.now)

    execucao = relationship("Execucao", back_populates="divergencias")

class LogExecucao(Base):
    __tablename__ = "logs_execucao"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id"))
    timestamp = Column(DateTime, default=datetime.now)
    level = Column(String) # INFO | WARNING | ERROR | SUCCESS
    modulo = Column(String)
    mensagem = Column(String)

    execucao = relationship("Execucao", back_populates="logs")

# Configuração da Session
engine = create_engine(f"sqlite:///{config.DASHBOARD_DB_PATH}", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def seed_admin_user():
    db = SessionLocal()
    try:
        admin = db.query(Usuario).filter(Usuario.username == "admin").first()
        if not admin:
            hashed_password = pwd_context.hash("trk123")
            novo_admin = Usuario(
                username="admin",
                password_hash=hashed_password,
                role="ADMIN"
            )
            db.add(novo_admin)
            
        rafael = db.query(Usuario).filter(Usuario.username == "Rafael").first()
        if not rafael:
            hashed_password = pwd_context.hash("trk123")
            novo_rafael = Usuario(
                username="Rafael",
                password_hash=hashed_password,
                role="ADMIN"
            )
            db.add(novo_rafael)
            
        db.commit()
    finally:
        db.close()

seed_admin_user()

# ==========================================
# Funções Utilitárias
# ==========================================

def criar_execucao(periodo_inicio: date, periodo_fim: date, iniciado_por: str) -> Execucao:
    db = SessionLocal()
    try:
        execucao = Execucao(
            status="RODANDO",
            periodo_inicio=periodo_inicio,
            periodo_fim=periodo_fim,
            iniciado_por=iniciado_por,
            iniciado_em=datetime.now()
        )
        db.add(execucao)
        db.commit()
        db.refresh(execucao)
        
        # Copiamos os atributos para o objeto retornado antes de fechar a sessão 
        # (Expunging the object makes it usable outside the session).
        db.expunge(execucao)
        return execucao
    finally:
        db.close()

def atualizar_execucao(execucao_id: int, **kwargs) -> None:
    db = SessionLocal()
    try:
        execucao = db.query(Execucao).filter(Execucao.id == execucao_id).first()
        if execucao:
            for key, value in kwargs.items():
                if hasattr(execucao, key):
                    setattr(execucao, key, value)
            db.commit()
    finally:
        db.close()

def registrar_divergencia(execucao_id: int, div_dict: dict) -> None:
    db = SessionLocal()
    try:
        divergencia = Divergencia(
            execucao_id=execucao_id,
            titulo_id=div_dict.get("titulo_id"),
            titulo_numero=div_dict.get("titulo_numero"),
            fornecedor_nome=div_dict.get("fornecedor_nome"),
            fornecedor_cnpj=div_dict.get("fornecedor_cnpj"),
            valor_sienge=div_dict.get("valor_sienge"),
            data_vencimento=div_dict.get("data_vencimento"),
            tipo=div_dict.get("tipo"),
            campo=div_dict.get("campo"),
            valor_sienge_campo=div_dict.get("valor_sienge_campo"),
            valor_nfe_campo=div_dict.get("valor_nfe_campo"),
            valor_boleto_campo=div_dict.get("valor_boleto_campo"),
            criticidade=div_dict.get("criticidade"),
            danfe_path=div_dict.get("danfe_path")
        )
        db.add(divergencia)
        db.commit()
    finally:
        db.close()

def registrar_log(execucao_id: int, level: str, modulo: str, mensagem: str) -> None:
    db = SessionLocal()
    try:
        log_entry = LogExecucao(
            execucao_id=execucao_id,
            level=level,
            modulo=modulo,
            mensagem=mensagem
        )
        db.add(log_entry)
        db.commit()
    finally:
        db.close()

def get_execucoes(limit: int = 50) -> List[Execucao]:
    db = SessionLocal()
    try:
        execucoes = db.query(Execucao).order_by(Execucao.iniciado_em.desc()).limit(limit).all()
        for e in execucoes:
            db.expunge(e)
        return execucoes
    finally:
        db.close()
        
def get_execucao(execucao_id: int) -> Optional[Execucao]:
    db = SessionLocal()
    try:
        execucao = db.query(Execucao).filter(Execucao.id == execucao_id).first()
        if execucao:
            db.expunge(execucao)
        return execucao
    finally:
        db.close()

def get_divergencias(execucao_id: int, criticidade: str = None, q: str = None) -> List[Divergencia]:
    db = SessionLocal()
    try:
        query = db.query(Divergencia).filter(Divergencia.execucao_id == execucao_id)
        if criticidade and criticidade != "Todas":
            query = query.filter(Divergencia.criticidade == criticidade)
        if q:
            q_str = f"%{q}%"
            query = query.filter(
                (Divergencia.titulo_numero.ilike(q_str)) |
                (Divergencia.fornecedor_nome.ilike(q_str)) |
                (Divergencia.tipo.ilike(q_str))
            )
            
        divergencias = query.all()
        for d in divergencias:
            db.expunge(d)
        return divergencias
    finally:
        db.close()

def get_logs(execucao_id: int, last_id: int = 0) -> List[LogExecucao]:
    db = SessionLocal()
    try:
        query = db.query(LogExecucao).filter(LogExecucao.execucao_id == execucao_id)
        if last_id > 0:
            query = query.filter(LogExecucao.id > last_id)
            
        logs = query.order_by(LogExecucao.timestamp.asc()).all()
        for l in logs:
            db.expunge(l)
        return logs
    finally:
        db.close()

def get_stats_gerais() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        stats = {
            "ultima_execucao": None,
            "taxa_divergencia_hoje": 0.0,
            "taxa_divergencia_semana": 0.0,
            "total_execucoes_mes": 0,
            "grafico_7dias": []
        }
        
        ultima = db.query(Execucao).order_by(Execucao.iniciado_em.desc()).first()
        if ultima:
            stats["ultima_execucao"] = {
                "id": ultima.id,
                "status": ultima.status,
                "iniciado_em": ultima.iniciado_em.isoformat() if ultima.iniciado_em else None,
                "total_titulos": ultima.total_titulos,
                "total_divergencias": ultima.total_divergencias,
                "total_criticos": ultima.total_criticos
            }
            
        # Para calculos mais precisos de taxa vamos abstrair com querys
        hoje = date.today()
        # Taxa de hoje
        execs_hoje = db.query(Execucao).filter(func.date(Execucao.iniciado_em) == hoje).all()
        titulos_hoje = sum(e.total_titulos for e in execs_hoje)
        divs_hoje = sum(e.total_divergencias for e in execs_hoje)
        if titulos_hoje > 0:
            stats["taxa_divergencia_hoje"] = (divs_hoje / titulos_hoje) * 100
            
        # Grafico 7 dias (sqlite func date format depends on usage, simple iteration is safer here)
        import datetime
        sete_dias = []
        for i in range(6, -1, -1):
            dia = hoje - datetime.timedelta(days=i)
            # Find in DB
            execs_dia = db.query(Execucao).filter(func.date(Execucao.iniciado_em) == dia).all()
            sete_dias.append({
                "data": dia.isoformat(),
                "total": sum(e.total_titulos for e in execs_dia),
                "divergencias": sum(e.total_divergencias for e in execs_dia),
                "criticos": sum(e.total_criticos for e in execs_dia)
            })
            
        stats["grafico_7dias"] = sete_dias
        
        # Execucoes mes
        primeiro_dia_mes = date(hoje.year, hoje.month, 1)
        stats["total_execucoes_mes"] = db.query(Execucao).filter(func.date(Execucao.iniciado_em) >= primeiro_dia_mes).count()

        return stats
    finally:
        db.close()
