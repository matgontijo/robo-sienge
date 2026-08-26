"""AGENTE — o assistente do financeiro no Sienge.

Conversa em linguagem natural; usa as ferramentas de dashboard/agente_tools.py para consultar o
Sienge (títulos, credores, apropriações, anexos), o robô de conferência e as análises; e só altera
o ERP depois que o usuário confirma na tela.

Rotas:
  GET  /api/agente/conversas                      lista
  POST /api/agente/conversas                      cria
  GET  /api/agente/conversas/{id}                 mensagens
  POST /api/agente/conversas/{id}/mensagem        envia (processa em thread)
  GET  /api/agente/conversas/{id}/stream          SSE: text / tool / confirm / done / error
  POST /api/agente/conversas/{id}/confirmar       {token, aprovado}
  DELETE /api/agente/conversas/{id}
"""
import asyncio
import json
import os
import queue
import threading
import time
import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
from dashboard import agente_tools as T

router = APIRouter(prefix="/api/agente", tags=["agente"])
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA = os.path.join(RAIZ, "output", "_agente")
os.makedirs(PASTA, exist_ok=True)
MODELO = os.getenv("AGENTE_MODELO", "claude-opus-5")


def _tem_claude_code():
    try:
        import claude_agent_sdk  # noqa: F401
        return os.path.exists(os.path.expanduser("~/.claude/.credentials.json")) or bool(os.getenv("CLAUDE_CODE_OAUTH_TOKEN"))
    except ImportError:
        return False


def provedor():
    """'claude-code' = roda o Claude Code (login local desta máquina, sem chave de API);
    'api' = Anthropic API com ANTHROPIC_API_KEY. AGENTE_PROVEDOR força um dos dois."""
    forcado = os.getenv("AGENTE_PROVEDOR", "auto").lower()
    if forcado in ("claude-code", "api"):
        return forcado
    return "claude-code" if _tem_claude_code() else "api"

_filas = {}        # conversa_id -> queue.Queue de eventos SSE
_pendentes = {}    # token -> dict(evento=threading.Event, aprovado=bool)
_live = {}         # conversa_id -> resposta em andamento (para religar o navegador ao voltar)
_cancelar = {}     # conversa_id -> threading.Event (botão Parar)
_lock = threading.Lock()

# ---- modo nuvem: o painel roda no Render, mas a IA executa na máquina do usuário.
# O worker local (agente_worker.py) busca trabalhos aqui e devolve os eventos.
MODO = os.getenv("AGENTE_MODO", "local")                      # local | nuvem
WORKER_TOKEN = os.getenv("AGENTE_WORKER_TOKEN", "")
_trabalhos = queue.Queue()                                     # jobs aguardando o worker
_decisoes = {}                                                 # token de confirmação -> True/False (modo nuvem)


def _worker_ok(request: Request):
    if MODO != "nuvem":
        raise HTTPException(404, "modo nuvem desligado")
    if not WORKER_TOKEN or request.headers.get("X-Worker-Token") != WORKER_TOKEN:
        raise HTTPException(401, "token de worker inválido")
    return True

SYSTEM = """Você é o agente financeiro do Grupo Garden dentro do Sienge (ERP). Fala SEMPRE português do Brasil — inclusive nas frases curtas antes de usar uma ferramenta ("vou buscar…", nunca "I'll…") — direto, sem rodeios, como um colega experiente do financeiro.
Seu usuário é Matheus Gontijo (financeiro). Use as ferramentas para responder com DADOS do Sienge, nunca de memória. Quando a pergunta envolve títulos, sempre consulte.

Como trabalhar:
- Datas em DD/MM/AAAA e dinheiro em R$ 1.234,56 nas respostas; para as ferramentas use AAAA-MM-DD.
- Hoje é {hoje}. Se o usuário não disser o período, assuma o mês corrente (ou os últimos 30 dias) e diga qual período usou.
- Para listas, responda em tabela Markdown compacta (título, documento, credor, valor, vencimento/situação). Para um título, os campos que importam.
- Se a resposta depende de olhar um anexo (guia, nota, boleto), leia o anexo com ler_anexo.
- Seja conciso: primeiro a resposta, depois o detalhe. Não repita o que a ferramenta devolveu inteiro; resuma.
- Nunca invente ids, valores ou nomes. Se a ferramenta falhar (403 etc.), diga o que falta liberar no usuário da API.

Regras do negócio que você já sabe (Grupo Garden):
- Obras: 1 GARDEN - 1ª ETAPA (unidade 2 OBRA GARDEN) → centro de custo 1; 3 GARDEN - INCORPORAÇÃO (unidades IMPOSTOS (RET), TERRENO, INCORPORAÇÃO, FF&E) → CC 3; 4 RESIDENCIAL VILA RAIÔ (comissão/vendas) → CC 4.
- Impostos: só o RET (DARF cód. 4095) vai para Imposto (RET) / CC 3 / plano 2.04.05.03 Impostos Federais. INSS retido (1162), CSRF (5952), IRRF (1708/8045) e ISS retido (1732) são custo da obra: 1ª Etapa / CC 1, planos 1.09.01.07 INSS, 2.04.03.03 PIS/COFINS/CSLL, 2.04.03.02 IRRF Terceiros PJ, 2.04.03.01 ISS. Pelos dados, o correto é ratear a guia pelas NFs que geraram a retenção (a ferramenta analise_impostos traz o rateio).
- O tributo de uma guia se define pelo código de receita impresso na guia, não pelo nome do documento ("INSS RET" é INSS retido, não RET).
- O robô de conferência confere remessas de pagamento (NF x título x boleto x destino) e registra divergências; a tela /conferencia mostra tudo.

Escrita no ERP: as ferramentas alterar_* pedem confirmação do usuário automaticamente. Antes de chamar, mostre exatamente o que vai mudar (de → para) e só então chame. Nunca altere nada que o usuário não pediu.

E-mail (Gmail do usuário): você pode ler e-mails livremente e ENVIAR somente quando o usuário pedir — todo envio abre um cartão de confirmação com o e-mail inteiro. Escreva o e-mail em português, direto e profissional; assine "Matheus Gontijo · Financeiro Grupo Garden". Nunca envie nada que o usuário não pediu, e nunca inclua senhas ou chaves em e-mails.
"""


def _path(cid):
    return os.path.join(PASTA, f"{cid}.json")


def _ler(cid):
    p = _path(cid)
    if not os.path.exists(p):
        raise HTTPException(404, "conversa não encontrada")
    return json.load(open(p, encoding="utf-8"))


def _salvar(conv):
    tmp = _path(conv["id"]) + ".tmp"
    json.dump(conv, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, _path(conv["id"]))


def _emitir(cid, evento, dados):
    """Publica o evento no stream E acumula no estado vivo — assim, se o usuário sair da
    conversa e voltar, o navegador recebe um 'snapshot' do que já aconteceu e continua."""
    with _lock:
        st = _live.get(cid)
        if st is not None:
            if evento == "text":
                st["texto"] += dados
            elif evento == "tool":
                st["passos"].append(dict(dados, status="rodando"))
            elif evento == "tool_result":
                for p in reversed(st["passos"]):
                    if p.get("status") == "rodando" and (p.get("id") == dados.get("id") or p.get("nome") == dados.get("nome")):
                        p["status"] = "cancelado" if dados.get("resumo") == "cancelado pelo usuário" else ("erro" if dados.get("erro") else "ok")
                        p["ms"] = dados.get("ms")
                        break
            elif evento == "confirm":
                st["confirms"].append(dados)
            elif evento == "error":
                st["erros"].append(dados)
            elif evento == "done":
                st["ativo"] = False
        _filas.setdefault(cid, queue.Queue()).put((evento, dados))


def _titulo_auto(texto):
    t = (texto or "").strip().replace("\n", " ")
    return (t[:48] + "…") if len(t) > 48 else (t or "Nova conversa")


# ------------------------------------------------------------------ motor Claude Code (login local)
def _rodar_claude_code(cid, texto_usuario, conv):
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, query, tool
    from claude_agent_sdk import AssistantMessage, UserMessage, SystemMessage, ResultMessage, StreamEvent
    from claude_agent_sdk import TextBlock, ToolUseBlock, ToolResultBlock

    # ferramentas do Sienge como servidor MCP em processo (mesma memória -> confirmação funciona)
    def _fazer(f):
        async def handler(args):
            entrada = args or {}
            if f["escreve"]:
                if not await _confirmar(f["name"], entrada):
                    _emitir(cid, "tool_result", dict(id=f["name"], nome=f["name"], resumo="cancelado pelo usuário"))
                    return {"content": [{"type": "text", "text": json.dumps({"cancelado": True, "motivo": "usuário não confirmou a alteração"}, ensure_ascii=False)}]}
            saida, erro = await anyio.to_thread.run_sync(T.executar, f["name"], entrada)
            return {"content": [{"type": "text", "text": saida}], "is_error": bool(erro)}
        return tool(f["name"], f["description"], f["input_schema"])(handler)

    servidor = create_sdk_mcp_server("sienge", "1.0.0", tools=[_fazer(f) for f in T.FERRAMENTAS])

    async def _confirmar(rotulo, entrada):
        """Mostra o cartão de confirmação no painel e espera o clique (True = aprovado)."""
        token = uuid.uuid4().hex[:10]
        ev = threading.Event()
        with _lock:
            _pendentes[token] = dict(evento=ev, aprovado=None, cid=cid)
        _emitir(cid, "confirm", dict(token=token, nome=rotulo, entrada=entrada))
        await anyio.to_thread.run_sync(ev.wait, 900)
        with _lock:
            dec = _pendentes.pop(token, {})
        return bool(dec.get("aprovado"))

    # E-mail (conector Gmail da conta): leitura é livre; qualquer ENVIO passa pelo cartão de
    # confirmação com o e-mail inteiro à vista. O resto continua bloqueado.
    GMAIL_ESCRITA = ("send_message", "reply", "forward", "create_draft", "update_draft",
                     "trash_message", "trash_thread", "mark_message_spam", "mark_thread_spam")

    async def pode_usar(nome, entrada, contexto):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        base = nome.split("__")[-1]
        if nome.startswith("mcp__sienge__") or base in ("Read", "Glob", "Grep", "ToolSearch"):
            return PermissionResultAllow()
        if "gmail" in nome.lower():
            if base not in GMAIL_ESCRITA:
                return PermissionResultAllow()
            if await _confirmar(f"enviar e-mail ({base})", entrada):
                return PermissionResultAllow()
            _emitir(cid, "tool_result", dict(id=nome, nome=f"gmail: {base}", resumo="cancelado pelo usuário"))
            return PermissionResultDeny(message="O usuário não confirmou o envio deste e-mail.")
        return PermissionResultDeny(message="Ferramenta não liberada no painel.")
    system = SYSTEM.format(hoje=date.today().strftime("%d/%m/%Y")) + (
        "\n\nVocê roda como Claude Code dentro do painel do robô. Além das ferramentas do Sienge (mcp__sienge__*), pode LER arquivos "
        "da pasta do projeto (Read/Glob/Grep) — as análises ficam em output/*.json e output/*.xlsx. Não edite arquivos nem rode comandos.")
    opcoes = ClaudeAgentOptions(
        system_prompt=system, mcp_servers={"sienge": servidor},
        # Gmail fica FORA de allowed_tools de propósito: ferramenta listada ali é pré-aprovada
        # e nunca passa pelo can_use_tool — o gate de confirmação seria pulado.
        allowed_tools=["mcp__sienge__*", "Read", "Glob", "Grep"],
        can_use_tool=pode_usar,
        disallowed_tools=["Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "KillShell", "BashOutput", "EnterPlanMode", "ExitPlanMode"],
        permission_mode="default", cwd=RAIZ, setting_sources=[], include_partial_messages=True, max_turns=40,
        resume=conv.get("sessao_claude") or None, model=os.getenv("AGENTE_MODELO_CC") or None,
        effort=os.getenv("AGENTE_ESFORCO") or None)

    texto_final, passos, sessao = [], [], conv.get("sessao_claude")
    nomes_tool = {}
    cancelar = threading.Event()
    with _lock:
        _cancelar[cid] = cancelar
    TIMEOUT = int(os.getenv("AGENTE_TIMEOUT_S", "600"))
    fim = {"motivo": None}   # None = terminou normal | 'cancelado' | 'timeout'

    async def main():
        nonlocal sessao
        with anyio.move_on_after(TIMEOUT) as escopo:
            async with anyio.create_task_group() as tg:
                async def vigia():
                    while True:
                        await anyio.sleep(1)
                        if cancelar.is_set():
                            fim["motivo"] = "cancelado"
                            tg.cancel_scope.cancel()

                tg.start_soon(vigia)
                await consumir()
                tg.cancel_scope.cancel()
        if escopo.cancelled_caught and fim["motivo"] is None:
            fim["motivo"] = "timeout"

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
                    _emitir(cid, "text", e["delta"].get("text", ""))
            elif isinstance(m, AssistantMessage):
                for b in m.content:
                    if isinstance(b, TextBlock):
                        texto_final.append(b.text)
                    elif isinstance(b, ToolUseBlock):
                        nome = b.name.replace("mcp__sienge__", "").replace("mcp__claude_ai_Gmail__", "gmail: ")
                        nomes_tool[b.id] = nome
                        if nome in ("ToolSearch",):      # mecânica interna do Claude Code, não interessa ao usuário
                            continue
                        _emitir(cid, "tool", dict(nome=nome, entrada=b.input, id=b.id))
                        passos.append(dict(ferramenta=nome, entrada=b.input, resultado="", erro=False))
            elif isinstance(m, UserMessage) and isinstance(m.content, list):
                for b in m.content:
                    if isinstance(b, ToolResultBlock):
                        nome = nomes_tool.get(b.tool_use_id, "")

                        if nome in ("ToolSearch",):
                            continue
                        txt = b.content if isinstance(b.content, str) else json.dumps(b.content, ensure_ascii=False, default=str)
                        _emitir(cid, "tool_result", dict(id=b.tool_use_id, nome=nome, erro=bool(b.is_error), resumo=(txt or "")[:300]))
                        for p_ in reversed(passos):
                            if p_["ferramenta"] == nome and not p_["resultado"]:
                                p_["resultado"] = (txt or "")[:2000]
                                p_["erro"] = bool(b.is_error)
                                break
            elif isinstance(m, ResultMessage):
                if m.session_id:
                    sessao = m.session_id
                if m.is_error:
                    _emitir(cid, "error", "Claude Code terminou com erro: " + "; ".join(m.errors or [m.subtype]))
                elif not texto_final and m.result:
                    texto_final.append(m.result)
    try:
        anyio.run(main)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "credentials" in msg.lower() or "login" in msg.lower() or "auth" in msg.lower():
            _emitir(cid, "error", "O Claude Code desta máquina não está logado. Abra o Claude Code uma vez (ou rode claude login) e tente de novo.")
        else:
            _emitir(cid, "error", f"Erro no Claude Code: {msg[:300]}")
    finally:
        with _lock:
            _cancelar.pop(cid, None)
    if fim["motivo"] == "cancelado":
        _emitir(cid, "error", "Interrompido por você. O que já foi apurado está salvo acima.")
    elif fim["motivo"] == "timeout":
        _emitir(cid, "error", f"Passou de {TIMEOUT // 60} minutos sem terminar — interrompi para não te deixar esperando. Tente uma pergunta mais específica.")
    conv = _ler(cid)
    conv["sessao_claude"] = sessao
    conv["mensagens"].append(dict(papel="assistant", texto="\n".join(texto_final).strip(), passos=passos,
                                  quando=datetime.now().isoformat(timespec="seconds")))
    conv["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
    _salvar(conv)
    _emitir(cid, "done", dict(texto="\n".join(texto_final).strip()))


# ------------------------------------------------------------------ o loop do agente
def _rodar(cid, texto_usuario):
    with _lock:
        _live.setdefault(cid, dict(ativo=True, texto="", passos=[], confirms=[], erros=[]))["ativo"] = True
    conv = _ler(cid)
    # histórico para a API (só user/assistant, já com blocos de tool use/result)
    msgs = conv.get("api_messages", [])
    msgs.append({"role": "user", "content": texto_usuario})
    conv["mensagens"].append(dict(papel="user", texto=texto_usuario, quando=datetime.now().isoformat(timespec="seconds")))
    if conv.get("titulo") in (None, "", "Nova conversa"):
        conv["titulo"] = _titulo_auto(texto_usuario)
    conv["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
    _salvar(conv)

    if provedor() == "claude-code":
        msgs.pop()   # o Claude Code guarda a própria sessão (sessao_claude); não usamos api_messages
        conv["api_messages"] = msgs
        _salvar(conv)
        return _rodar_claude_code(cid, texto_usuario, conv)

    try:
        import anthropic
    except ImportError:
        _emitir(cid, "error", "Biblioteca anthropic não instalada (.venv).")
        _emitir(cid, "done", {})
        return
    if not config.ANTHROPIC_API_KEY:
        msgs.pop()   # não deixa a pergunta órfã no histórico da API
        _emitir(cid, "error", "Sem motor disponível: ou logue o Claude Code nesta máquina, ou preencha ANTHROPIC_API_KEY no .env (Configurações ⚙️) e reinicie o painel.")
        _emitir(cid, "done", {})
        return
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3, timeout=600)

    system = SYSTEM.format(hoje=date.today().strftime("%d/%m/%Y"))
    tools = T.schemas_para_api()
    texto_final = []
    passos = []
    tentativas_fallback = True
    for _ in range(24):   # limite de voltas de ferramenta por pergunta
        kwargs = dict(model=MODELO, max_tokens=16000, system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                      tools=tools, messages=msgs, thinking={"type": "adaptive"}, output_config={"effort": "high"})
        if tentativas_fallback:
            kwargs["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
            kwargs["extra_body"] = {"fallbacks": "default"}
        try:
            with client.messages.stream(**kwargs) as stream:
                for ev in stream:
                    if ev.type == "content_block_delta" and getattr(ev.delta, "type", "") == "text_delta":
                        _emitir(cid, "text", ev.delta.text)
                resp = stream.get_final_message()
        except anthropic.BadRequestError as e:
            if tentativas_fallback and "fallback" in str(e).lower():
                tentativas_fallback = False
                continue
            _emitir(cid, "error", f"Erro na API do Claude: {e.message}")
            break
        except anthropic.AuthenticationError:
            _emitir(cid, "error", "Chave ANTHROPIC_API_KEY inválida.")
            break
        except anthropic.RateLimitError:
            _emitir(cid, "error", "Limite de requisições da API atingido. Tente de novo em instantes.")
            break
        except anthropic.APIStatusError as e:
            _emitir(cid, "error", f"API do Claude respondeu {e.status_code}.")
            break
        except anthropic.APIConnectionError:
            _emitir(cid, "error", "Sem conexão com a API do Claude.")
            break
        except TypeError as e:
            if "output_config" in str(e) or "thinking" in str(e):
                _emitir(cid, "error", "A biblioteca anthropic está desatualizada: rode  .venv/Scripts/pip install -U anthropic")
            else:
                _emitir(cid, "error", f"Erro interno: {e}")
            break
        except Exception as e:  # noqa: BLE001
            _emitir(cid, "error", f"Erro inesperado: {str(e)[:300]}")
            break

        conteudo = [b.model_dump(exclude_none=True) for b in resp.content]
        msgs.append({"role": "assistant", "content": conteudo})
        for b in resp.content:
            if b.type == "text":
                texto_final.append(b.text)

        if resp.stop_reason == "refusal":
            _emitir(cid, "error", "O modelo recusou esta solicitação.")
            break
        if resp.stop_reason != "tool_use":
            break

        # executa as ferramentas (com confirmação para as que escrevem)
        resultados = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            entrada = b.input if isinstance(b.input, dict) else json.loads(json.dumps(b.input))
            f = T.POR_NOME.get(b.name)
            _emitir(cid, "tool", dict(nome=b.name, entrada=entrada, id=b.id))
            if not f:
                resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": "ferramenta desconhecida", "is_error": True})
                continue
            if f["escreve"]:
                token = uuid.uuid4().hex[:10]
                ev = threading.Event()
                with _lock:
                    _pendentes[token] = dict(evento=ev, aprovado=None, cid=cid)
                _emitir(cid, "confirm", dict(token=token, nome=b.name, entrada=entrada))
                ev.wait(timeout=900)
                with _lock:
                    dec = _pendentes.pop(token, {})
                if not dec.get("aprovado"):
                    saida, erro = json.dumps({"cancelado": True, "motivo": "usuário não confirmou a alteração"}, ensure_ascii=False), False
                    _emitir(cid, "tool_result", dict(id=b.id, nome=b.name, resumo="cancelado pelo usuário"))
                    resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": saida})
                    passos.append(dict(ferramenta=b.name, entrada=entrada, resultado="cancelado"))
                    continue
            t0 = time.time()
            saida, erro = T.executar(b.name, entrada)
            _emitir(cid, "tool_result", dict(id=b.id, nome=b.name, ms=int((time.time() - t0) * 1000), erro=erro, resumo=saida[:300]))
            passos.append(dict(ferramenta=b.name, entrada=entrada, resultado=saida[:2000], erro=erro))
            resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": saida, "is_error": erro})
        msgs.append({"role": "user", "content": resultados})

    conv = _ler(cid)
    conv["api_messages"] = msgs
    conv["mensagens"].append(dict(papel="assistant", texto="\n".join(texto_final).strip(), passos=passos,
                                  quando=datetime.now().isoformat(timespec="seconds")))
    conv["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
    _salvar(conv)
    _emitir(cid, "done", dict(texto="\n".join(texto_final).strip()))


# ------------------------------------------------------------------ rotas
@router.get("/status")
def status():
    p = provedor()
    return dict(provedor=p,
                descricao=("Claude Code — login local desta máquina (sem chave de API)" if p == "claude-code"
                           else ("Anthropic API — ANTHROPIC_API_KEY" if config.ANTHROPIC_API_KEY else "sem motor: logue o Claude Code ou configure ANTHROPIC_API_KEY")),
                pronto=(p == "claude-code") or bool(config.ANTHROPIC_API_KEY),
                modelo=((os.getenv("AGENTE_MODELO_CC") or "padrão do Claude Code") if p == "claude-code" else MODELO))


@router.get("/conversas")
def listar():
    out = []
    for f in os.listdir(PASTA):
        if f.endswith(".json"):
            try:
                c = json.load(open(os.path.join(PASTA, f), encoding="utf-8"))
                out.append(dict(id=c["id"], titulo=c.get("titulo"), atualizada_em=c.get("atualizada_em"), n=len(c.get("mensagens", [])),
                                processando=bool(_live.get(c["id"], {}).get("ativo"))))
            except Exception:
                pass
    return sorted(out, key=lambda x: x.get("atualizada_em") or "", reverse=True)


@router.post("/conversas")
def criar():
    cid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    conv = dict(id=cid, titulo="Nova conversa", criada_em=datetime.now().isoformat(timespec="seconds"),
                atualizada_em=datetime.now().isoformat(timespec="seconds"), mensagens=[], api_messages=[])
    _salvar(conv)
    return conv


@router.get("/conversas/{cid}")
def obter(cid: str):
    c = _ler(cid)
    with _lock:
        proc = bool(_live.get(cid, {}).get("ativo"))
    return dict(id=c["id"], titulo=c.get("titulo"), mensagens=c.get("mensagens", []), processando=proc)


@router.delete("/conversas/{cid}")
def apagar(cid: str):
    p = _path(cid)
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


class Msg(BaseModel):
    texto: str


@router.post("/conversas/{cid}/mensagem")
def enviar(cid: str, m: Msg):
    _ler(cid)
    if not m.texto.strip():
        raise HTTPException(400, "mensagem vazia")
    with _lock:
        if _live.get(cid, {}).get("ativo"):
            raise HTTPException(409, "esta conversa ainda está processando a pergunta anterior")
        _filas[cid] = queue.Queue()
        _live[cid] = dict(ativo=True, texto="", passos=[], confirms=[], erros=[])   # antes da thread: o stream pode conectar antes dela
    if MODO == "nuvem":
        conv = _ler(cid)
        conv["mensagens"].append(dict(papel="user", texto=m.texto.strip(), quando=datetime.now().isoformat(timespec="seconds")))
        if conv.get("titulo") in (None, "", "Nova conversa"):
            conv["titulo"] = _titulo_auto(m.texto.strip())
        conv["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
        _salvar(conv)
        _trabalhos.put(dict(cid=cid, texto=m.texto.strip(), sessao=conv.get("sessao_claude")))
        return {"ok": True, "modo": "nuvem"}
    threading.Thread(target=_rodar, args=(cid, m.texto.strip()), daemon=True).start()
    return {"ok": True}


class Conf(BaseModel):
    token: str
    aprovado: bool


@router.post("/conversas/{cid}/confirmar")
def confirmar(cid: str, c: Conf):
    with _lock:
        p = _pendentes.get(c.token)
        if p:
            p["aprovado"] = bool(c.aprovado)
            p["evento"].set()
        elif MODO == "nuvem":
            _decisoes[c.token] = bool(c.aprovado)
        else:
            raise HTTPException(404, "confirmação não encontrada ou expirada")
        st = _live.get((p or {}).get("cid") or cid)
        if st:
            st["confirms"] = [x for x in st["confirms"] if x.get("token") != c.token]
    return {"ok": True}


@router.post("/conversas/{cid}/parar")
def parar(cid: str):
    """Interrompe a resposta em andamento desta conversa (o parcial fica salvo)."""
    with _lock:
        ev = _cancelar.get(cid)
        ativo = bool(_live.get(cid, {}).get("ativo"))
        # solta qualquer confirmação pendurada desta conversa
        for tok, p in list(_pendentes.items()):
            if p.get("cid") == cid:
                p["aprovado"] = False
                p["evento"].set()
    if not ativo:
        return {"ok": False, "motivo": "nada processando"}
    if ev:
        ev.set()
        return {"ok": True}
    if MODO == "nuvem":
        with _lock:
            _cancelar.setdefault(cid, threading.Event()).set()
        return {"ok": True, "modo": "nuvem"}
    return {"ok": False, "motivo": "esta resposta não dá para interromper (motor API)"}


@router.get("/conversas/{cid}/stream")
async def stream(cid: str, req: Request):
    with _lock:
        st = _live.get(cid)
        snapshot = json.loads(json.dumps(st, ensure_ascii=False)) if st else None
        q = _filas.setdefault(cid, queue.Queue())
        while not q.empty():          # tudo até aqui já está no snapshot
            try:
                q.get_nowait()
            except queue.Empty:
                break

    async def gen():
        if snapshot is not None:
            yield f"event: snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            if not snapshot.get("ativo"):
                yield "event: done\ndata: {}\n\n"
                return
        elif True:
            yield "event: done\ndata: {}\n\n"
            return
        ocioso = 0
        while True:
            if await req.is_disconnected():
                break
            try:
                ev, dados = q.get_nowait()
                ocioso = 0
                yield f"event: {ev}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"
                if ev == "done":
                    break
            except queue.Empty:
                ocioso += 1
                if ocioso % 15 == 0:
                    yield ": ping\n\n"
                await asyncio.sleep(0.2)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ rotas do WORKER (modo nuvem)
from fastapi import APIRouter as _APIRouter

worker_router = _APIRouter(prefix="/api/agente/worker", tags=["agente-worker"])


@worker_router.get("/trabalho")
def worker_trabalho(request: Request, espera: int = 20):
    """Long-poll: devolve o próximo trabalho (pergunta de usuário) ou {} após 'espera' segundos."""
    _worker_ok(request)
    try:
        return _trabalhos.get(timeout=max(1, min(espera, 50)))
    except queue.Empty:
        return {}


class EventoWorker(BaseModel):
    cid: str
    evento: str          # text | tool | tool_result | confirm | error
    dados: object = None


@worker_router.post("/evento")
def worker_evento(e: EventoWorker, request: Request):
    _worker_ok(request)
    _emitir(e.cid, e.evento, e.dados)
    return {"ok": True}


class FimWorker(BaseModel):
    cid: str
    texto: str = ""
    passos: list = []
    sessao: str = None


@worker_router.post("/fim")
def worker_fim(f: FimWorker, request: Request):
    """Worker terminou a resposta: grava na conversa e emite done."""
    _worker_ok(request)
    conv = _ler(f.cid)
    if f.sessao:
        conv["sessao_claude"] = f.sessao
    conv["mensagens"].append(dict(papel="assistant", texto=f.texto, passos=f.passos,
                                  quando=datetime.now().isoformat(timespec="seconds")))
    conv["atualizada_em"] = datetime.now().isoformat(timespec="seconds")
    _salvar(conv)
    with _lock:
        _cancelar.pop(f.cid, None)
    _emitir(f.cid, "done", dict(texto=f.texto))
    return {"ok": True}


@worker_router.get("/decisao/{token}")
def worker_decisao(token: str, request: Request):
    """Worker consulta se o usuário já clicou no cartão de confirmação."""
    _worker_ok(request)
    with _lock:
        if token in _decisoes:
            return {"decidido": True, "aprovado": _decisoes.pop(token)}
    return {"decidido": False}


@worker_router.get("/cancelado/{cid}")
def worker_cancelado(cid: str, request: Request):
    _worker_ok(request)
    with _lock:
        ev = _cancelar.get(cid)
        return {"cancelado": bool(ev and ev.is_set())}
