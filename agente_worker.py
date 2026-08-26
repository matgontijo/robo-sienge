"""WORKER do Agente Sienge — roda NA SUA MÁQUINA e faz a IA do painel na nuvem funcionar.

O painel no Render (AGENTE_MODO=nuvem) só guarda as conversas e mostra a tela; quem pensa é este
processo: ele busca as perguntas na nuvem, roda o Claude Code com o SEU login local + as ferramentas
do Sienge (credenciais do seu .env), devolve os eventos em tempo real e espera o clique de
confirmação para qualquer escrita (Sienge ou e-mail).

Configure no .env:
    PAINEL_NUVEM_URL=https://robo-sienge-painel.onrender.com
    AGENTE_WORKER_TOKEN=<o mesmo token configurado no Render>

Rode (e deixe rodando; ex.: junto do .bat):
    .venv\\Scripts\\python.exe agente_worker.py
"""
import json
import os
import threading
import time
import uuid

import requests

import config  # carrega o .env

URL = (os.getenv("PAINEL_NUVEM_URL") or "").rstrip("/")
TOKEN = os.getenv("AGENTE_WORKER_TOKEN", "")
H = {"X-Worker-Token": TOKEN, "Content-Type": "application/json"}
TIMEOUT_RESPOSTA = int(os.getenv("AGENTE_TIMEOUT_S", "600"))


def _post(path, dados):
    for tentativa in range(3):
        try:
            r = requests.post(f"{URL}{path}", headers=H, data=json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8"), timeout=30)
            if r.status_code < 500:
                return r
        except requests.RequestException:
            pass
        time.sleep(2 * (tentativa + 1))
    return None


def _get(path, **params):
    r = requests.get(f"{URL}{path}", headers=H, params=params, timeout=70)
    r.raise_for_status()
    return r.json()


def processar(job):
    """Roda uma pergunta com o mesmo motor do modo local, trocando o transporte por HTTP."""
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
    from claude_agent_sdk import AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent
    from claude_agent_sdk import TextBlock, ToolUseBlock, ToolResultBlock
    from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    from dashboard import agente_tools as T
    from dashboard.agente import SYSTEM, RAIZ
    from datetime import date

    cid, texto_usuario, sessao = job["cid"], job["texto"], job.get("sessao")
    print(f"[worker] {cid}: {texto_usuario[:80]}")

    def emitir(evento, dados):
        _post("/api/agente/worker/evento", dict(cid=cid, evento=evento, dados=dados))

    async def _confirmar(rotulo, entrada):
        token = uuid.uuid4().hex[:10]
        emitir("confirm", dict(token=token, nome=rotulo, entrada=entrada))
        prazo = time.time() + 900
        while time.time() < prazo:
            try:
                d = await anyio.to_thread.run_sync(lambda: _get(f"/api/agente/worker/decisao/{token}"))
                if d.get("decidido"):
                    return bool(d.get("aprovado"))
            except Exception:  # noqa: BLE001
                pass
            await anyio.sleep(1.5)
        return False

    def _fazer(f):
        async def handler(args):
            entrada = args or {}
            if f["escreve"]:
                if not await _confirmar(f["name"], entrada):
                    emitir("tool_result", dict(id=f["name"], nome=f["name"], resumo="cancelado pelo usuário"))
                    return {"content": [{"type": "text", "text": json.dumps({"cancelado": True, "motivo": "usuário não confirmou a alteração"}, ensure_ascii=False)}]}
            saida, erro = await anyio.to_thread.run_sync(T.executar, f["name"], entrada)
            return {"content": [{"type": "text", "text": saida}], "is_error": bool(erro)}
        return tool(f["name"], f["description"], f["input_schema"])(handler)

    GMAIL_ESCRITA = ("send_message", "reply", "forward", "create_draft", "update_draft",
                     "trash_message", "trash_thread", "mark_message_spam", "mark_thread_spam")

    async def pode_usar(nome, entrada, contexto):
        base = nome.split("__")[-1]
        if nome.startswith("mcp__sienge__") or base in ("Read", "Glob", "Grep", "ToolSearch"):
            return PermissionResultAllow()
        if "gmail" in nome.lower():
            if base not in GMAIL_ESCRITA:
                return PermissionResultAllow()
            if await _confirmar(f"enviar e-mail ({base})", entrada):
                return PermissionResultAllow()
            emitir("tool_result", dict(id=nome, nome=f"gmail: {base}", resumo="cancelado pelo usuário"))
            return PermissionResultDeny(message="O usuário não confirmou o envio deste e-mail.")
        return PermissionResultDeny(message="Ferramenta não liberada no painel.")

    servidor = create_sdk_mcp_server("sienge", "1.0.0", tools=[_fazer(f) for f in T.FERRAMENTAS])
    system = SYSTEM.format(hoje=date.today().strftime("%d/%m/%Y")) + (
        "\n\nVocê roda como Claude Code dentro do painel do robô. Além das ferramentas do Sienge (mcp__sienge__*), pode LER arquivos "
        "da pasta do projeto (Read/Glob/Grep) — as análises ficam em output/*.json e output/*.xlsx. Não edite arquivos nem rode comandos.")
    opcoes = ClaudeAgentOptions(
        system_prompt=system, mcp_servers={"sienge": servidor},
        allowed_tools=["mcp__sienge__*", "Read", "Glob", "Grep"],
        can_use_tool=pode_usar,
        disallowed_tools=["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "KillShell", "BashOutput", "EnterPlanMode", "ExitPlanMode"],
        permission_mode="default", cwd=RAIZ, setting_sources=[], include_partial_messages=True, max_turns=40,
        resume=sessao or None, model=os.getenv("AGENTE_MODELO_CC") or None,
        effort=os.getenv("AGENTE_ESFORCO") or None)

    texto_final, passos, nomes_tool = [], [], {}
    fim = {"motivo": None}

    async def consumir():
        nonlocal sessao
        async for m in query(prompt=texto_usuario, options=opcoes):
            if isinstance(m, SystemMessage):
                sid = (m.data or {}).get("session_id")
                if sid:
                    sessao = sid
            elif isinstance(m, StreamEvent):
                e = m.event or {}
                if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                    emitir("text", e["delta"].get("text", ""))
            elif isinstance(m, AssistantMessage):
                for b in m.content:
                    if isinstance(b, TextBlock):
                        texto_final.append(b.text)
                    elif isinstance(b, ToolUseBlock):
                        nome = b.name.replace("mcp__sienge__", "").replace("mcp__claude_ai_Gmail__", "gmail: ")
                        nomes_tool[b.id] = nome
                        if nome in ("ToolSearch",):
                            continue
                        emitir("tool", dict(nome=nome, entrada=b.input, id=b.id))
                        passos.append(dict(ferramenta=nome, entrada=b.input, resultado="", erro=False))
            elif isinstance(m, UserMessage) and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolResultBlock):
                        nome = nomes_tool.get(b.tool_use_id, "")
                        if nome in ("ToolSearch",):
                            continue
                        txt = b.content if isinstance(b.content, str) else json.dumps(b.content, ensure_ascii=False, default=str)
                        emitir("tool_result", dict(id=b.tool_use_id, nome=nome, erro=bool(b.is_error), resumo=(txt or "")[:300]))
                        for p_ in reversed(passos):
                            if p_["ferramenta"] == nome and not p_["resultado"]:
                                p_["resultado"] = (txt or "")[:2000]
                                p_["erro"] = bool(b.is_error)
                                break
            elif isinstance(m, ResultMessage):
                if m.session_id:
                    sessao = m.session_id
                if m.is_error:
                    emitir("error", "Claude Code terminou com erro: " + "; ".join(m.errors or [m.subtype]))
                elif not texto_final and m.result:
                    texto_final.append(m.result)

    async def main():
        with anyio.move_on_after(TIMEOUT_RESPOSTA) as escopo:
            async with anyio.create_task_group() as tg:
                async def vigia():
                    while True:
                        await anyio.sleep(2)
                        try:
                            c = await anyio.to_thread.run_sync(lambda: _get(f"/api/agente/worker/cancelado/{cid}"))
                            if c.get("cancelado"):
                                fim["motivo"] = "cancelado"
                                tg.cancel_scope.cancel()
                        except Exception:  # noqa: BLE001
                            pass
                tg.start_soon(vigia)
                await consumir()
                tg.cancel_scope.cancel()
        if escopo.cancelled_caught and fim["motivo"] is None:
            fim["motivo"] = "timeout"

    try:
        import anyio as _anyio
        _anyio.run(main)
    except Exception as e:  # noqa: BLE001
        emitir("error", f"Erro no worker: {str(e)[:300]}")
    if fim["motivo"] == "cancelado":
        emitir("error", "Interrompido por você. O que já foi apurado está salvo acima.")
    elif fim["motivo"] == "timeout":
        emitir("error", f"Passou de {TIMEOUT_RESPOSTA // 60} minutos — interrompi para não te deixar esperando.")
    _post("/api/agente/worker/fim", dict(cid=cid, texto="\n".join(texto_final).strip(), passos=passos, sessao=sessao))
    print(f"[worker] {cid}: concluído ({len(passos)} consultas)")


def main():
    if not URL or not TOKEN:
        raise SystemExit("Configure PAINEL_NUVEM_URL e AGENTE_WORKER_TOKEN no .env")
    print(f"[worker] conectado em {URL} — aguardando trabalhos (Ctrl+C para sair)")
    while True:
        try:
            job = _get("/api/agente/worker/trabalho", espera=25)
        except requests.RequestException as e:
            print(f"[worker] nuvem fora do ar ({e.__class__.__name__}); tentando de novo em 10s")
            time.sleep(10)
            continue
        if not job or not job.get("cid"):
            continue
        # um trabalho por vez, em thread própria (perguntas de conversas diferentes podem enfileirar)
        t = threading.Thread(target=processar, args=(job,), daemon=True)
        t.start()
        t.join()


if __name__ == "__main__":
    main()
