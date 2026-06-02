import os
import smtplib
from email.message import EmailMessage
from datetime import date
from typing import List
from loguru import logger
import requests

class Notifier:
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        email_destino: str,
        teams_webhook_url: str = None
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.email_destino = email_destino
        self.teams_webhook_url = teams_webhook_url

    def enviar_resumo(
        self,
        data_referencia: date,
        total_titulos: int,
        total_divergencias: int,
        total_criticos: int,
        relatorio_path: str,
        erros_execucao: List[str]
    ) -> None:
        
        assunto = f"[ROBÔ CONTAS A PAGAR] Relatório {data_referencia} — {total_divergencias} divergências"
        
        html_erros = ""
        if erros_execucao:
            html_erros = "<h3>Erros de Execução:</h3><ul>"
            for e in erros_execucao:
                html_erros += f"<li>{e}</li>"
            html_erros += "</ul>"
            
        corpo_html = f"""
        <html>
            <body>
                <h2>Resumo da Conciliação de Contas a Pagar</h2>
                <p>Data de Referência: <b>{data_referencia}</b></p>
                <table border="1" cellpadding="5" cellspacing="0">
                    <tr><th>Total de Títulos Analisados</th><td>{total_titulos}</td></tr>
                    <tr><th>Total de Divergências Encontradas</th><td>{total_divergencias}</td></tr>
                    <tr><th>Divergências CRÍTICAS</th><td style="color:red; font-weight:bold;">{total_criticos}</td></tr>
                </table>
                <br/>
                {html_erros}
                <p>O relatório completo em Excel segue em anexo (se tamanho menor que 10MB).</p>
            </body>
        </html>
        """
        
        msg = EmailMessage()
        msg['Subject'] = assunto
        msg['From'] = self.smtp_user
        msg['To'] = self.email_destino
        msg.set_content("Ative a exibio de HTML para ler este e-mail.")
        msg.add_alternative(corpo_html, subtype='html')
        
        # Anexa relatrio
        tamanho_mb = os.path.getsize(relatorio_path) / (1024 * 1024)
        if tamanho_mb <= 10:
            with open(relatorio_path, 'rb') as f:
                relatorio_data = f.read()
            msg.add_attachment(
                relatorio_data,
                maintype='application',
                subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                filename=os.path.basename(relatorio_path)
            )
        else:
            logger.warning(f"Relatório {relatorio_path} muito grande ({tamanho_mb:.2f}MB). Não será anexado.")
            
        # Envia e-mail
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            logger.success("E-mail com resumo enviado com sucesso.")
        except Exception as e:
            logger.error(f"Falha ao enviar e-mail: {e}")
            
        # Envia Teams Webhook se configurado
        if self.teams_webhook_url:
            self._enviar_teams(assunto, total_titulos, total_divergencias, total_criticos)

    def _enviar_teams(self, titulo: str, total: int, divergencias: int, criticos: int):
        try:
            payload = {
                "@type": "MessageCard",
                "@context": "http://schema.org/extensions",
                "themeColor": "FF0000" if criticos > 0 else "0076D7",
                "summary": titulo,
                "sections": [{
                    "activityTitle": titulo,
                    "facts": [
                        {"name": "Total Analisados:", "value": str(total)},
                        {"name": "Divergências:", "value": str(divergencias)},
                        {"name": "Críticas:", "value": str(criticos)}
                    ],
                    "markdown": True
                }]
            }
            resp = requests.post(self.teams_webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.success("Notificação enviada ao Teams com sucesso.")
        except Exception as e:
            logger.error(f"Falha ao enviar notificação ao Teams: {e}")
