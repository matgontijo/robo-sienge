from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from dashboard.database import SessionLocal, Usuario, pwd_context
import config

# auto_error=False: sem credencial NÃO dispara o pop-up nativo do navegador
# (WWW-Authenticate). Quem trata o 401 é a tela de login do próprio painel.
security = HTTPBasic(auto_error=False)


class _UsuarioLocal:
    """Usuário fictício quando a autenticação está desligada (uso local)."""
    username = "local"
    role = "ADMIN"


def get_current_user(credentials: Optional[HTTPBasicCredentials] = Depends(security)):
    # Auth desligada (padrão local): entra direto, sem login.
    if not config.DASHBOARD_AUTH:
        return _UsuarioLocal()

    # Auth ligada (ex.: painel público no Render): exige usuário/senha.
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Login necessário")  # sem WWW-Authenticate → sem pop-up
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username == credentials.username).first()
        if not user or not pwd_context.verify(credentials.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Usuário ou senha incorretos")
        return user
    finally:
        db.close()
