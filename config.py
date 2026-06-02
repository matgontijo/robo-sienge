import os
import sys
from dotenv import load_dotenv
from loguru import logger

# Carrega variáveis do arquivo .env
load_dotenv()

# Sienge
SIENGE_BASE_URL = os.getenv("SIENGE_BASE_URL")
SIENGE_USERNAME = os.getenv("SIENGE_USERNAME")
SIENGE_PASSWORD = os.getenv("SIENGE_PASSWORD")

# Santander
SANTANDER_CLIENT_ID = os.getenv("SANTANDER_CLIENT_ID")
SANTANDER_CLIENT_SECRET = os.getenv("SANTANDER_CLIENT_SECRET")
SANTANDER_CERT_PATH = os.getenv("SANTANDER_CERT_PATH", "./certs/santander.pfx")
SANTANDER_CERT_PASSWORD = os.getenv("SANTANDER_CERT_PASSWORD")
SANTANDER_ENV = os.getenv("SANTANDER_ENV", "production")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# SEFAZ
SEFAZ_CERT_PATH = os.getenv("SEFAZ_CERT_PATH", "./certs/empresa_a1.pfx")
SEFAZ_CERT_PASSWORD = os.getenv("SEFAZ_CERT_PASSWORD")
SEFAZ_CNPJ = os.getenv("SEFAZ_CNPJ")
SEFAZ_AMBIENTE = int(os.getenv("SEFAZ_AMBIENTE", "1"))
SEFAZ_NSU_ULTIMO = int(os.getenv("SEFAZ_NSU_ULTIMO", "0"))

# Notificações
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
NOTIF_EMAIL_DESTINO = os.getenv("NOTIF_EMAIL_DESTINO")
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")

# Scheduler
CRON_HORA = os.getenv("CRON_HORA", "07")
CRON_MINUTO = os.getenv("CRON_MINUTO", "00")

# Pastas de Saída
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./output")
LOGS_DIR = os.getenv("LOGS_DIR", "./logs")

# Dashboard
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
DASHBOARD_DB_PATH = os.getenv("DASHBOARD_DB_PATH", "./dashboard.db")

# Garante que os diretórios existam
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "danfes"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "xmls"), exist_ok=True)
os.makedirs(os.path.join(OUTPUT_DIR, "relatorios"), exist_ok=True)

# Configuração do Loguru
logger.remove() # Remove o handler padrão
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | <level>{message}</level>"
)
logger.add(
    os.path.join(LOGS_DIR, "robo_{time:YYYY-MM-DD}.log"),
    rotation="1 day",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {module}:{function}:{line} | {message}"
)

# Validação de variáveis obrigatórias
def validate_config():
    missing_vars = []
    required_vars = [
        ("SIENGE_BASE_URL", SIENGE_BASE_URL),
        ("SIENGE_USERNAME", SIENGE_USERNAME),
        ("SIENGE_PASSWORD", SIENGE_PASSWORD),
        ("SANTANDER_CLIENT_ID", SANTANDER_CLIENT_ID),
        ("SANTANDER_CLIENT_SECRET", SANTANDER_CLIENT_SECRET),
        ("SANTANDER_CERT_PASSWORD", SANTANDER_CERT_PASSWORD),
        ("ANTHROPIC_API_KEY", ANTHROPIC_API_KEY),
        ("SEFAZ_CERT_PASSWORD", SEFAZ_CERT_PASSWORD),
        ("SEFAZ_CNPJ", SEFAZ_CNPJ),
        ("SMTP_HOST", SMTP_HOST),
        ("SMTP_USER", SMTP_USER),
        ("SMTP_PASSWORD", SMTP_PASSWORD),
        ("NOTIF_EMAIL_DESTINO", NOTIF_EMAIL_DESTINO),
        ("DASHBOARD_USER", DASHBOARD_USER),
        ("DASHBOARD_PASSWORD", DASHBOARD_PASSWORD),
    ]

    for name, value in required_vars:
        if not value:
            missing_vars.append(name)
            
    if missing_vars:
        msg = f"Variáveis de ambiente ausentes: {', '.join(missing_vars)}"
        logger.critical(msg)
        raise SystemExit(msg)

def reload_config():
    """Recarrega as variveis do .env para a memria."""
    load_dotenv(override=True)
    global SIENGE_BASE_URL, SIENGE_USERNAME, SIENGE_PASSWORD
    global SANTANDER_CLIENT_ID, SANTANDER_CLIENT_SECRET, SANTANDER_CERT_PATH, SANTANDER_CERT_PASSWORD, SANTANDER_ENV
    global ANTHROPIC_API_KEY
    global SEFAZ_CERT_PATH, SEFAZ_CERT_PASSWORD, SEFAZ_CNPJ, SEFAZ_AMBIENTE, SEFAZ_NSU_ULTIMO
    global SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, NOTIF_EMAIL_DESTINO, TEAMS_WEBHOOK_URL
    global CRON_HORA, CRON_MINUTO
    global DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_USER, DASHBOARD_PASSWORD, DASHBOARD_DB_PATH
    
    SIENGE_BASE_URL = os.getenv("SIENGE_BASE_URL")
    SIENGE_USERNAME = os.getenv("SIENGE_USERNAME")
    SIENGE_PASSWORD = os.getenv("SIENGE_PASSWORD")
    
    SANTANDER_CLIENT_ID = os.getenv("SANTANDER_CLIENT_ID")
    SANTANDER_CLIENT_SECRET = os.getenv("SANTANDER_CLIENT_SECRET")
    SANTANDER_CERT_PATH = os.getenv("SANTANDER_CERT_PATH", "./certs/santander.pfx")
    SANTANDER_CERT_PASSWORD = os.getenv("SANTANDER_CERT_PASSWORD")
    SANTANDER_ENV = os.getenv("SANTANDER_ENV", "production")
    
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    
    SEFAZ_CERT_PATH = os.getenv("SEFAZ_CERT_PATH", "./certs/empresa_a1.pfx")
    SEFAZ_CERT_PASSWORD = os.getenv("SEFAZ_CERT_PASSWORD")
    SEFAZ_CNPJ = os.getenv("SEFAZ_CNPJ")
    SEFAZ_AMBIENTE = int(os.getenv("SEFAZ_AMBIENTE", "1"))
    SEFAZ_NSU_ULTIMO = int(os.getenv("SEFAZ_NSU_ULTIMO", "0"))
    
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    NOTIF_EMAIL_DESTINO = os.getenv("NOTIF_EMAIL_DESTINO")
    TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL")
    
    CRON_HORA = os.getenv("CRON_HORA", "07")
    CRON_MINUTO = os.getenv("CRON_MINUTO", "00")
    
    DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
    DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
    DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin")
    DASHBOARD_DB_PATH = os.getenv("DASHBOARD_DB_PATH", "./dashboard.db")
