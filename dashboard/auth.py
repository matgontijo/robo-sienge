class _UsuarioLocal:
    """Painel sem login: todo acesso entra como administrador local."""
    username = "local"
    role = "ADMIN"


def get_current_user():
    # Autenticação removida — o painel é de uso local e entra direto.
    # Sem HTTPBasic aqui não existe nenhuma resposta 401/WWW-Authenticate,
    # então o navegador nunca mais abre o pop-up nativo de login.
    return _UsuarioLocal()
