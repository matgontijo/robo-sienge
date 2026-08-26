"""Autenticação por sessão (cookie assinado) + permissão por TELA.

- Login em /login grava o cookie "sessao" (HMAC com SECRET_KEY; expira em 12h).
- get_current_user: usuário logado e ativo (401 JSON, nunca WWW-Authenticate → sem pop-up do navegador).
- requer_tela("agente"): dependência que exige a tela liberada (ADMIN enxerga tudo).
- O admin master é o DASHBOARD_USER do .env; ele cadastra os demais e marca as telas de cada um.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException, Request

import config
from dashboard import database as db

TELAS = ["agente", "conferencia", "apropriacao"]          # telas atribuíveis
VALIDADE_S = 12 * 3600

_SECRET = None


def _secret() -> bytes:
    """SECRET_KEY do ambiente (Render) ou gerada uma vez em output/.secret_key (local)."""
    global _SECRET
    if _SECRET is None:
        env = os.getenv("SECRET_KEY")
        if env:
            _SECRET = env.encode()
        else:
            caminho = os.path.join(config.OUTPUT_DIR, ".secret_key")
            os.makedirs(config.OUTPUT_DIR, exist_ok=True)
            if not os.path.exists(caminho):
                with open(caminho, "w", encoding="ascii") as f:
                    f.write(base64.urlsafe_b64encode(os.urandom(32)).decode())
            _SECRET = open(caminho, encoding="ascii").read().strip().encode()
    return _SECRET


def criar_token(username: str) -> str:
    exp = str(int(time.time()) + VALIDADE_S)
    corpo = f"{username}|{exp}"
    assinatura = hmac.new(_secret(), corpo.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{corpo}|{assinatura}".encode()).decode()


def _verificar_token(token: str):
    try:
        corpo_completo = base64.urlsafe_b64decode(token.encode()).decode()
        username, exp, assinatura = corpo_completo.rsplit("|", 2)
        esperado = hmac.new(_secret(), f"{username}|{exp}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(assinatura, esperado) or time.time() > int(exp):
            return None
        return username
    except Exception:  # noqa: BLE001 — token malformado = não logado
        return None


def usuario_da_request(request: Request):
    """Usuario logado (objeto do banco) ou None. Não levanta exceção."""
    token = request.cookies.get("sessao")
    if not token:
        return None
    username = _verificar_token(token)
    if not username:
        return None
    u = db.get_usuario(username)
    if not u or not getattr(u, "ativo", 1):
        return None
    return u


def telas_do(usuario) -> list:
    if usuario.role == "ADMIN":
        return TELAS + ["usuarios", "config"]
    try:
        return json.loads(usuario.telas or "[]")
    except Exception:  # noqa: BLE001
        return []


def get_current_user(request: Request):
    u = usuario_da_request(request)
    if not u:
        raise HTTPException(status_code=401, detail="Faça login para continuar")
    return u


def requer_tela(tela: str):
    def dep(u=Depends(get_current_user)):
        if tela not in telas_do(u):
            raise HTTPException(status_code=403, detail=f"Seu usuário não tem acesso à tela '{tela}'. Peça ao administrador.")
        return u
    return dep


def requer_admin(u=Depends(get_current_user)):
    if u.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Apenas o administrador pode fazer isso")
    return u


def autenticar(username: str, senha: str):
    """Confere usuário+senha; devolve o Usuario ou None."""
    u = db.get_usuario(username)
    if not u or not getattr(u, "ativo", 1):
        return None
    if not db.pwd_context.verify(senha, u.password_hash):
        return None
    return u
