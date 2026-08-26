"""Gestão de usuários (somente ADMIN): cadastrar, liberar telas, trocar senha, bloquear."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dashboard import database as db
from dashboard.auth import TELAS, requer_admin, telas_do

router = APIRouter(prefix="/api/usuarios", tags=["usuarios"], dependencies=[Depends(requer_admin)])


def _fmt(u):
    return dict(username=u.username, role=u.role, telas=telas_do(u), ativo=bool(getattr(u, "ativo", 1)),
                criado_em=str(u.criado_em or "")[:16])


@router.get("")
def listar():
    return dict(telas_disponiveis=TELAS, usuarios=[_fmt(u) for u in db.listar_usuarios()])


class NovoUsuario(BaseModel):
    username: str
    senha: str
    role: str = "OPERADOR"        # OPERADOR | ADMIN
    telas: list = []


@router.post("")
def criar(n: NovoUsuario):
    nome = n.username.strip()
    if not nome or len(n.senha) < 6:
        raise HTTPException(400, "Informe usuário e uma senha com pelo menos 6 caracteres")
    if db.get_usuario(nome):
        raise HTTPException(409, "Já existe um usuário com esse nome")
    if n.role not in ("OPERADOR", "ADMIN"):
        raise HTTPException(400, "role deve ser OPERADOR ou ADMIN")
    telas = [t for t in n.telas if t in TELAS]
    u = db.criar_usuario(nome, n.senha, role=n.role, telas=json.dumps(telas))
    return _fmt(u)


class EditaUsuario(BaseModel):
    senha: str = None
    role: str = None
    telas: list = None
    ativo: bool = None


@router.put("/{username}")
def editar(username: str, e: EditaUsuario, admin=Depends(requer_admin)):
    if username == admin.username and (e.ativo is False or (e.role and e.role != "ADMIN")):
        raise HTTPException(400, "Você não pode bloquear nem rebaixar o próprio usuário")
    if e.senha is not None and len(e.senha) < 6:
        raise HTTPException(400, "Senha com pelo menos 6 caracteres")
    if e.role is not None and e.role not in ("OPERADOR", "ADMIN"):
        raise HTTPException(400, "role deve ser OPERADOR ou ADMIN")
    telas = json.dumps([t for t in e.telas if t in TELAS]) if e.telas is not None else None
    u = db.atualizar_usuario(username, senha=e.senha, role=e.role, telas=telas,
                             ativo=(None if e.ativo is None else (1 if e.ativo else 0)))
    if not u:
        raise HTTPException(404, "Usuário não encontrado")
    return _fmt(u)


@router.delete("/{username}")
def apagar(username: str, admin=Depends(requer_admin)):
    if username == admin.username:
        raise HTTPException(400, "Você não pode apagar o próprio usuário")
    if not db.apagar_usuario(username):
        raise HTTPException(404, "Usuário não encontrado")
    return {"ok": True}
