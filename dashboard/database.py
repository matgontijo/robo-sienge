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
    pid = Column(Integer, nullable=True)  # processo do ciclo — permite parar na hora

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

    # Revisão (workflow de aprovação no painel)
    status_revisao = Column(String, default="PENDENTE")  # PENDENTE | APROVADO | REJEITADO
    observacao_revisao = Column(String, nullable=True)
    revisado_por = Column(String, nullable=True)
    revisado_em = Column(DateTime, nullable=True)

    execucao = relationship("Execucao", back_populates="divergencias")

class Pagamento(Base):
    """Dados de pagamento da parcela (aba Inf. Pagamento do Sienge) por execução —
    contexto completo para a tela de revisão."""
    __tablename__ = "pagamentos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id"), index=True)
    titulo_numero = Column(String, index=True)
    parcela = Column(String, nullable=True)
    fornecedor = Column(String, nullable=True)
    cnpj_credor = Column(String, nullable=True)
    forma = Column(String, nullable=True)
    valor = Column(Float, nullable=True)
    vencimento = Column(String, nullable=True)
    tipo_chave_pix = Column(String, nullable=True)
    chave_pix = Column(String, nullable=True)
    banco = Column(String, nullable=True)
    agencia = Column(String, nullable=True)
    conta = Column(String, nullable=True)
    titular = Column(String, nullable=True)
    cnpj_destino = Column(String, nullable=True)
    linha_digitavel = Column(String, nullable=True)
    banco_boleto = Column(String, nullable=True)
    valor_boleto = Column(Float, nullable=True)
    confronto = Column(String, nullable=True)
    retencoes = Column(String, nullable=True)       # JSON {tributo: valor}
    liquido_calc = Column(Float, nullable=True)     # parcela bruta - retenções
    regime = Column(String, nullable=True)          # Simples Nacional | Regime normal

class LogExecucao(Base):
    __tablename__ = "logs_execucao"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execucao_id = Column(Integer, ForeignKey("execucoes.id"))
    timestamp = Column(DateTime, default=datetime.now)
    level = Column(String) # INFO | WARNING | ERROR | SUCCESS
    modulo = Column(String)
    mensagem = Column(String)

    execucao = relationship("Execucao", back_populates="logs")

# Configuração da Session — Postgres compartilhado (Render) ou SQLite local.
# Uma única DATABASE_URL faz o ciclo local e o painel na nuvem verem os mesmos dados.
_db_url = config.DATABASE_URL
if _db_url:
    # Render às vezes entrega "postgres://"; o SQLAlchemy 2.x exige "postgresql://".
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    engine = create_engine(_db_url, pool_pre_ping=True)
    IS_SQLITE = False
else:
    engine = create_engine(f"sqlite:///{config.DASHBOARD_DB_PATH}",
                           connect_args={"check_same_thread": False})
    IS_SQLITE = True

Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def _migrar_colunas():
    """Migração leve para bancos SQLite ANTIGOS (adiciona colunas que faltam).
    No Postgres o create_all já cria as tabelas com todas as colunas do modelo,
    então esta migração roda só no SQLite."""
    if not IS_SQLITE:
        return
    from sqlalchemy import text
    novas = {
        "status_revisao": "TEXT DEFAULT 'PENDENTE'",
        "observacao_revisao": "TEXT",
        "revisado_por": "TEXT",
        "revisado_em": "DATETIME",
    }
    novas_pag = {"retencoes": "TEXT", "liquido_calc": "REAL", "regime": "TEXT"}
    novas_exec = {"pid": "INTEGER"}
    with engine.connect() as conn:
        existentes = {r[1] for r in conn.execute(text("PRAGMA table_info(divergencias)"))}
        for col, ddl in novas.items():
            if col not in existentes:
                conn.execute(text(f"ALTER TABLE divergencias ADD COLUMN {col} {ddl}"))
        existentes_exec = {r[1] for r in conn.execute(text("PRAGMA table_info(execucoes)"))}
        for col, ddl in novas_exec.items():
            if existentes_exec and col not in existentes_exec:
                conn.execute(text(f"ALTER TABLE execucoes ADD COLUMN {col} {ddl}"))
        existentes_pag = {r[1] for r in conn.execute(text("PRAGMA table_info(pagamentos)"))}
        for col, ddl in novas_pag.items():
            if existentes_pag and col not in existentes_pag:
                conn.execute(text(f"ALTER TABLE pagamentos ADD COLUMN {col} {ddl}"))
        conn.commit()

_migrar_colunas()

def seed_admin_user():
    """Garante um usuário ADMIN inicial usando as credenciais do config/.env
    (DASHBOARD_USER / DASHBOARD_PASSWORD). Assim o login do painel é configurável
    e fica consistente entre app e testes."""
    db = SessionLocal()
    try:
        admin_user = config.DASHBOARD_USER or "admin"
        admin_pass = config.DASHBOARD_PASSWORD or "admin"

        admin = db.query(Usuario).filter(Usuario.username == admin_user).first()
        if not admin:
            db.add(Usuario(
                username=admin_user,
                password_hash=pwd_context.hash(admin_pass),
                role="ADMIN"
            ))

        # Usuário secundário histórico (mantido para compatibilidade)
        rafael = db.query(Usuario).filter(Usuario.username == "Rafael").first()
        if not rafael:
            db.add(Usuario(
                username="Rafael",
                password_hash=pwd_context.hash(admin_pass),
                role="ADMIN"
            ))

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

def registrar_pagamentos(execucao_id: int, rows: list) -> None:
    """Grava em lote os dados de pagamento da execução (para a tela de revisão)."""
    db = SessionLocal()
    try:
        for p in rows or []:
            db.add(Pagamento(
                execucao_id=execucao_id,
                titulo_numero=str(p.get("numero") or ""),
                parcela=str(p.get("parcela") or "") or None,
                fornecedor=p.get("fornecedor"),
                cnpj_credor=p.get("cnpj_credor"),
                forma=p.get("forma"),
                valor=p.get("valor"),
                vencimento=str(p.get("vencimento") or "") or None,
                tipo_chave_pix=p.get("tipo_chave_pix"),
                chave_pix=p.get("chave_pix"),
                banco=p.get("banco"),
                agencia=p.get("agencia"),
                conta=p.get("conta"),
                titular=p.get("titular"),
                cnpj_destino=p.get("cnpj_destino"),
                linha_digitavel=p.get("linha_digitavel"),
                banco_boleto=p.get("banco_boleto"),
                valor_boleto=p.get("valor_boleto"),
                confronto=p.get("confronto"),
                retencoes=p.get("retencoes"),
                liquido_calc=p.get("liquido_calc"),
                regime=p.get("regime"),
            ))
        db.commit()
    finally:
        db.close()

def get_pagamentos(execucao_id: int) -> List["Pagamento"]:
    db = SessionLocal()
    try:
        rows = db.query(Pagamento).filter(Pagamento.execucao_id == execucao_id).all()
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()

def atualizar_revisao(divergencia_id: int, status: str, observacao: str = None,
                      usuario: str = None) -> Optional[Divergencia]:
    """Marca uma divergência como APROVADO/REJEITADO/PENDENTE no fluxo de revisão."""
    db = SessionLocal()
    try:
        d = db.query(Divergencia).filter(Divergencia.id == divergencia_id).first()
        if not d:
            return None
        d.status_revisao = status
        d.observacao_revisao = observacao
        d.revisado_por = usuario
        d.revisado_em = datetime.now() if status != "PENDENTE" else None
        db.commit()
        db.refresh(d)
        db.expunge(d)
        return d
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
