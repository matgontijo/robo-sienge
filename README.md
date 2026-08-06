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

## Anexos

Cada ciclo baixa **todos** os anexos de cada título para `output/anexos/<titulo>_<parcela>/`.
Já o dossiê do ciclo (`output/dossie/ciclo_N/`) leva só os documentos **da parcela
que está sendo paga**: a nota fiscal e o boleto daquela parcela.

### Por que isso não é trivial

No Sienge o anexo fica preso ao **título**, não à parcela — um título em 12x traz
os 12 boletos no mesmo pacote. O robô identifica a parcela pelo próprio documento,
do critério mais forte para o mais fraco:

1. **linha digitável do anexo == a cadastrada na parcela** (exato)
2. **vencimento e valor** decodificados da linha digitável batem com os da parcela
   (padrão FEBRABAN, determinístico, sem OCR)
3. só o **vencimento** bate, e nenhum outro anexo tem o mesmo vencimento
4. só existe **um boleto** anexado — entra, mas marcado como não confirmado

Quando há boletos e nenhum pôde ser atribuído à parcela, o título leva **todos**
os anexos e sai como **CONFERIR** na aba Dossiê: é preferível ver documento a mais
do que ficar sem o certo. Título sem boleto nenhum (Pix/TED) leva só a nota.

A nota fiscal é escolhida pelo **conteúdo** do arquivo, não pelo nome nem pela
ordem em que veio do Sienge. A aba **Dossiê** do Excel mostra, por título, como o
boleto da parcela foi encontrado e quais arquivos foram copiados.

### Remontar a pasta de um ciclo já concluído

```bash
python baixar_anexos_ciclo.py            # último ciclo, só NF + boleto da parcela
python baixar_anexos_ciclo.py 54         # ciclo 54, só NF + boleto da parcela
python baixar_anexos_ciclo.py --todos    # último ciclo, TODOS os anexos
python baixar_anexos_ciclo.py 54 --todos # ciclo 54, TODOS os anexos
```

O modo padrão grava em `output/anexos_corretos/ciclo_N/`; o `--todos`, em
`output/anexos_completos/ciclo_N/`. Os dois geram um `_indice.csv` com o que foi
copiado, por qual critério o boleto foi escolhido e o que ficou de fora.

## Painel Web

### Iniciar

```bash
# Apenas o painel (sem scheduler):
python main.py dashboard

# Modo produção (painel + scheduler juntos):
python main.py full
```

Acesse: http://localhost:8000 — **sem login** (ferramenta de uso local, entra direto).

> Por isso, **não publique este painel na internet sem proteção**: os dados de
> contas a pagar ficariam abertos a qualquer pessoa com a URL.

### Funcionalidades

- **Cards de status**: última execução, títulos processados hoje, divergências e críticos.
- **Gráfico 7 dias**: volume de títulos vs divergências por dia.
- **Histórico**: todas as execuções com status, clicável para detalhes.
- **Divergências**: filtráveis por criticidade, com link para abrir o DANFE.
- **Logs em tempo real**: interface *terminal-style*, atualiza automaticamente via SSE (Server-Sent Events) durante uma execução ativa.
- **Rodar manualmente**: botão para disparar a execução de imediato em background com um período customizado.
- **Download Excel**: download direto do relatório em Excel através do painel.
