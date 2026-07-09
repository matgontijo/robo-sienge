# Deploy no Render (painel na nuvem + banco compartilhado)

Arquitetura: **um Postgres no Render é o banco único**. O ciclo de conferência
roda no seu PC (1x/semana, com as credenciais do Sienge) e grava no Postgres;
o painel hospedado no Render lê e escreve as aprovações no **mesmo** banco.
Assim, o que você aprova no celular/escritório e o que o ciclo gera batem.

## 1. Subir no Render (uma vez)

1. Acesse <https://dashboard.render.com> → **New** → **Blueprint**.
2. Conecte o repositório **matgontijo/robo-sienge** e escolha o branch com o código.
3. O Render lê o `render.yaml` e propõe criar: **1 Postgres** + **1 Web Service**.
4. Em **Environment** do web service, preencha os segredos marcados `sync:false`:
   - `DASHBOARD_USER` e `DASHBOARD_PASSWORD` (o login do painel na nuvem).
   - `DATABASE_URL` já é ligado automaticamente ao Postgres pelo blueprint.
5. **Create** — em alguns minutos o painel sobe numa URL tipo
   `https://robo-sienge-painel.onrender.com`.

> Plano grátis do web service hiberna após ~15 min sem uso: o primeiro acesso
> demora ~30s para "acordar". Os dados NÃO se perdem (moram no Postgres).

## 2. Apontar o ciclo LOCAL para o mesmo banco

Para o ciclo do seu PC gravar no Postgres da nuvem (e não mais no SQLite local):

1. No Render, abra o Postgres **robo-sienge-db** → copie a **External Database URL**.
2. No seu `.env` local, cole em `DATABASE_URL=...` (aquela URL `postgresql://...`).
3. Rode o ciclo normalmente: `python main.py run --relatorio "seu_relatorio.xlsx"`.
   Agora ele grava no banco da nuvem — o painel no Render mostra na hora.

> Se `DATABASE_URL` ficar vazio no `.env`, o ciclo volta a usar o SQLite local
> (`dashboard.db`) — útil para testar offline.

## 3. Migrar as revisões que já existem no SQLite (opcional)

Se quiser levar o histórico atual (`dashboard.db`) para o Postgres, me avise —
é um script de cópia SQLite→Postgres de uma vez só.

## Custos

- **Web service**: grátis (hiberna quando ocioso).
- **Postgres**: `basic-256mb` ~US$ 6/mês (persistente e sempre no ar). Dá para
  começar no `free` do Postgres para testar, mas ele expira e some — só para prova.
