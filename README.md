# Robô de Conciliação Contas a Pagar

Robô em Python para automatizar a conciliação de contas a pagar. O sistema integra as seguintes pontas:
1. **Sienge ERP**: Extração de títulos a pagar e anexos em PDF.
2. **Anthropic Claude (Vision)**: OCR inteligente para extrair a chave de acesso da NF-e presente no PDF anexo.
3. **Sefaz Nacional**: Consulta e download do XML da NF-e validada.
4. **Gerador DANFE**: Geração local do DANFE em PDF a partir do XML.
5. **Santander DDA**: Consulta via API dos boletos emitidos contra o CNPJ da empresa.
6. **Motor de Reconciliação**: Cruzamento de todas as informações.

## Pré-requisitos
- Python 3.10 ou superior
- Certificado Digital A1 (.pfx) da empresa, com respectiva senha
- Certificado Digital Santander (.pfx) para Mutual TLS
- Conta na API do Sienge (Basic Auth)
- Conta no Santander Developer (Client ID, Client Secret)
- Chave de API da Anthropic (modelo Claude Haiku)

## Instalação

1. Clone o repositório ou baixe os arquivos.
2. Crie e ative um ambiente virtual:
   ```bash
   python -m venv venv
   # No Windows:
   venv\Scripts\activate
   # No Linux/Mac:
   source venv/bin/activate
   ```
3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```
4. Crie as pastas de certificados se ainda não existirem, e coloque os arquivos `.pfx`:
   ```bash
   mkdir certs
   # Copie os arquivos santander.pfx e empresa_a1.pfx para cá
   ```

## Configuração do .env

Copie o arquivo `.env.example` para `.env` e preencha as variáveis de acordo:
```bash
cp .env.example .env
```
Preencha **todas** as varáveis obrigatórias (veja o arquivo `.env` para detalhes).

## Execução

O robô possui uma interface de linha de comando (CLI) em `main.py`.

### Rodar uma vez imediatamente
Para processar os títulos de hoje:
```bash
python main.py run
```
Para processar um período específico:
```bash
python main.py run --inicio 2024-01-01 --fim 2024-01-31
```
Modo teste (dry-run):
```bash
python main.py run --dry-run
```

### Rodar de forma agendada (Scheduler)
Para deixar o robô rodando em background aguardando a hora configurada no `.env` (CRON_HORA e CRON_MINUTO):
```bash
python main.py schedule
```

## Relatório Gerado

Ao final de cada execução, um arquivo Excel (`.xlsx`) será criado na pasta `output/relatorios/`.
O relatório contém 3 abas:
- **Divergências**: Títulos que falharam em alguma validação (CNPJ, Valor, Vencimento, Boleto, etc.). Linhas vermelhas indicam divergências CRÍTICAS, amarelas indicam ATENÇÃO. Conta também com link direto para o DANFE gerado localmente.
- **Conferidos OK**: Títulos que passaram 100% no cruzamento (valores batem, DDA confere).
- **Não Processados**: Títulos onde ocorreu uma falha técnica fatal no processamento (ex: API fora do ar na hora da consulta daquele título específico).

## Painel Web

### Iniciar

```bash
# Apenas o painel (sem scheduler):
python main.py dashboard

# Modo produção (painel + scheduler juntos):
python main.py full
```

Acesse: http://localhost:8000
Usuário e senha: configurados no `.env` (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`)

### Funcionalidades

- **Cards de status**: última execução, títulos processados hoje, divergências e críticos.
- **Gráfico 7 dias**: volume de títulos vs divergências por dia.
- **Histórico**: todas as execuções com status, clicável para detalhes.
- **Divergências**: filtráveis por criticidade, com link para abrir o DANFE.
- **Logs em tempo real**: interface *terminal-style*, atualiza automaticamente via SSE (Server-Sent Events) durante uma execução ativa.
- **Rodar manualmente**: botão para disparar a execução de imediato em background com um período customizado.
- **Download Excel**: download direto do relatório em Excel através do painel.
