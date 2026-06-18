import argparse
import os
import sys
from datetime import date
from loguru import logger
import config


def _validar_relatorio(caminho):
    """Valida o caminho/extensão do relatório de Fluxo de Caixa. Retorna o caminho ou None."""
    if not caminho:
        return None
    if not os.path.isfile(caminho):
        logger.error(f"Relatório não encontrado: {caminho}")
        sys.exit(1)
    ext = os.path.splitext(caminho)[1].lower()
    if ext not in (".xlsx", ".xlsm", ".pdf"):
        logger.error(f"Extensão de relatório inválida: {ext}. Use .xlsx ou .pdf")
        sys.exit(1)
    return caminho


def main():
    parser = argparse.ArgumentParser(description="Robô de Conciliação Contas a Pagar")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # run
    run_parser = subparsers.add_parser("run", help="Executa o robô uma vez imediatamente")
    run_parser.add_argument("--inicio", type=str, help="Data inicio formato YYYY-MM-DD")
    run_parser.add_argument("--fim", type=str, help="Data fim formato YYYY-MM-DD")
    run_parser.add_argument("--relatorio", type=str, help="Caminho do relatorio de Fluxo de Caixa (.xlsx ou .pdf). Quando informado, define a lista de titulos a conferir (ignora --inicio/--fim para selecao; datas ainda usadas na janela DDA).")
    run_parser.add_argument("--dry-run", action="store_true", help="Executa o motor sem gerar relatrio ou email")
    run_parser.add_argument("--debug", action="store_true", help="Ativa logs nvel DEBUG")
    
    # schedule
    sched_parser = subparsers.add_parser("schedule", help="Inicia o agendador em background")
    sched_parser.add_argument("--debug", action="store_true", help="Ativa logs nvel DEBUG")
    
    # dashboard
    dash_parser = subparsers.add_parser("dashboard", help="Inicia apenas o painel web (FastAPI)")
    dash_parser.add_argument("--debug", action="store_true", help="Ativa logs nvel DEBUG")
    
    # full
    full_parser = subparsers.add_parser("full", help="Inicia o scheduler e o painel web juntos (Modo Producao)")
    full_parser.add_argument("--relatorio", type=str, help="Caminho do relatorio de Fluxo de Caixa (.xlsx ou .pdf) usado nas execucoes agendadas.")
    full_parser.add_argument("--debug", action="store_true", help="Ativa logs nvel DEBUG")
    
    # add-user
    add_user_parser = subparsers.add_parser("add-user", help="Adiciona um novo usuario ao banco de dados")
    add_user_parser.add_argument("username", type=str, help="Nome de usuario")
    add_user_parser.add_argument("password", type=str, help="Senha do usuario")
    add_user_parser.add_argument("role", type=str, choices=["ADMIN", "OPERADOR", "LEITURA"], help="Nivel de acesso (ADMIN, OPERADOR, LEITURA)")
    
    args = parser.parse_args()
    
    # Configura DEBUG se necessrio
    if getattr(args, "debug", False):
        logger.remove()
        logger.add(
            sys.stderr,
            level="DEBUG",
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>"
        )
    
    # Valida variveris no .env
    config.validate_config()
    
    import orchestrator
    
    if args.command == "run":
        d_inicio = date.today()
        d_fim = date.today()

        if args.inicio:
            d_inicio = date.fromisoformat(args.inicio)
        if args.fim:
            d_fim = date.fromisoformat(args.fim)

        relatorio_path = _validar_relatorio(getattr(args, "relatorio", None))
        if relatorio_path:
            logger.info(f"Fonte de títulos: relatório de Fluxo de Caixa ({relatorio_path}). "
                        f"Datas usadas apenas para a janela DDA.")

        if args.dry_run:
            logger.info("MODO DRY-RUN ATIVADO. Relatrios e notificaes no sero emitidos.")
            # Fariamos o bypass de Report e Notifier se implementado
            # Neste script para simplificar s logamos
            pass

        orchestrator.executar_ciclo(d_inicio, d_fim, iniciado_por="cli", relatorio_path=relatorio_path)
        
    elif args.command == "schedule":
        orchestrator.agendar()
    elif args.command == "dashboard":
        import uvicorn
        logger.info(f"Iniciando MODO DASHBOARD na porta {config.DASHBOARD_PORT}")
        uvicorn.run("dashboard.app:app", host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, log_level="info" if not args.debug else "debug")
        
    elif args.command == "add-user":
        from dashboard import database as db
        db_session = db.SessionLocal()
        try:
            existing = db_session.query(db.Usuario).filter(db.Usuario.username == args.username).first()
            if existing:
                logger.error(f"Usuário {args.username} já existe.")
                return
            
            hashed_pw = db.pwd_context.hash(args.password)
            novo = db.Usuario(username=args.username, password_hash=hashed_pw, role=args.role)
            db_session.add(novo)
            db_session.commit()
            logger.success(f"Usuário {args.username} criado com sucesso (Role: {args.role}).")
        finally:
            db_session.close()
        
    elif args.command == "full":
        import threading
        import uvicorn
        
        logger.info("Iniciando modo FULL (Scheduler + Dashboard)...")
        relatorio_path = _validar_relatorio(getattr(args, "relatorio", None))
        # Inicia scheduler em thread daemon para morrer se o processo principal (uvicorn) morrer
        t = threading.Thread(target=orchestrator.agendar, kwargs={"relatorio_path": relatorio_path}, daemon=True)
        t.start()
        
        # Inicia Uvicorn no fluxo principal
        uvicorn.run("dashboard.app:app", host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT)

if __name__ == "__main__":
    main()
