import os
from datetime import date
from typing import List, Tuple
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from loguru import logger
from models import Titulo
from modules.reconciler import Divergencia

class ReportGenerator:
    def __init__(self):
        self.fill_header = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")
        self.fill_critica = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
        self.fill_atencao = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
        self.font_bold = Font(bold=True)
        self.font_link = Font(color="0563C1", underline="single")

    def gerar(
        self,
        divergencias: List[Tuple[Titulo, List[Divergencia]]],
        titulos_ok: List[Titulo],
        titulos_erro: List[Tuple[Titulo, str]], # Titulo e motivo do erro
        data_referencia: date,
        output_dir: str
    ) -> str:
        
        wb = Workbook()
        
        # Aba Divergncias
        ws_div = wb.active
        ws_div.title = "Divergências"
        self._preencher_aba_divergencias(ws_div, divergencias)
        
        # Aba Conferidos OK
        ws_ok = wb.create_sheet(title="Conferidos OK")
        self._preencher_aba_ok(ws_ok, titulos_ok)
        
        # Aba No Processados
        ws_err = wb.create_sheet(title="Não Processados")
        self._preencher_aba_erro(ws_err, titulos_erro)
        
        # Salvar
        os.makedirs(os.path.join(output_dir, "relatorios"), exist_ok=True)
        timestamp = data_referencia.strftime("%Y-%m-%d")
        
        import datetime
        agora = datetime.datetime.now().strftime("%H%M")
        
        filename = f"conciliacao_{timestamp}_{agora}.xlsx"
        filepath = os.path.join(output_dir, "relatorios", filename)
        
        wb.save(filepath)
        logger.success(f"Relatrio gerado: {filepath}")
        return filepath

    def _preencher_aba_divergencias(self, ws, divergencias_list):
        headers = [
            "Nº Título Sienge", "Fornecedor", "CNPJ Fornecedor", "Valor Sienge", 
            "Vencimento Sienge", "Forma Pagamento", "Tipo Divergência", 
            "Campo", "Valor Sienge (D)", "Valor NF-e", "Valor Boleto", 
            "Criticidade", "Link DANFE"
        ]
        ws.append(headers)
        
        # Formatar cabealho
        for cell in ws[1]:
            cell.fill = self.fill_header
            cell.font = self.font_bold
            
        row_num = 2
        qtd_criticas = 0
        qtd_atencao = 0
        
        for titulo, divs in divergencias_list:
            for div in divs:
                ws.append([
                    titulo.numero,
                    titulo.fornecedor_nome,
                    titulo.fornecedor_cnpj,
                    titulo.valor_liquido,
                    str(titulo.data_vencimento),
                    titulo.forma_pagamento,
                    div.tipo,
                    div.campo,
                    div.valor_sienge,
                    div.valor_nfe,
                    div.valor_boleto,
                    div.criticidade,
                    div.danfe_path or "Nenhum"
                ])
                
                # Pintar linha baseada na criticidade
                fill = None
                if div.criticidade == "CRITICA":
                    fill = self.fill_critica
                    qtd_criticas += 1
                elif div.criticidade == "ATENCAO":
                    fill = self.fill_atencao
                    qtd_atencao += 1
                    
                if fill:
                    for col in range(1, len(headers) + 1):
                        ws.cell(row=row_num, column=col).fill = fill
                
                # Transformar Link DANFE em link se existir
                if div.danfe_path:
                    cell_link = ws.cell(row=row_num, column=13)
                    # Caminho absoluto para o link funcionar localmente
                    abs_path = os.path.abspath(div.danfe_path)
                    cell_link.hyperlink = f"file:///{abs_path.replace(chr(92), '/')}"
                    cell_link.font = self.font_link
                
                row_num += 1
                
        # Totais
        ws.append([])
        ws.append(["TOTAIS", f"Críticas: {qtd_criticas}", f"Atenção: {qtd_atencao}", f"Total Divergências: {qtd_criticas + qtd_atencao}"])
        for cell in ws[row_num+1]:
            cell.font = self.font_bold
            
        # Filtro
        ws.auto_filter.ref = f"A1:M{row_num - 1}"
        
        # Auto ajuste
        self._auto_fit_columns(ws)

    def _preencher_aba_ok(self, ws, titulos):
        headers = [
            "Nº Título", "Fornecedor", "CNPJ", "Valor", 
            "Vencimento", "Pagamento"
        ]
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = self.fill_header
            cell.font = self.font_bold
            
        for t in titulos:
            ws.append([
                t.numero, t.fornecedor_nome, t.fornecedor_cnpj,
                t.valor_liquido, str(t.data_vencimento), t.forma_pagamento
            ])
            
        ws.auto_filter.ref = f"A1:F{len(titulos)+1}"
        self._auto_fit_columns(ws)

    def _preencher_aba_erro(self, ws, titulos_erro):
        headers = ["Nº Título", "Fornecedor", "Valor", "Erro/Motivo"]
        ws.append(headers)
        for cell in ws[1]:
            cell.fill = self.fill_header
            cell.font = self.font_bold
            
        for t, erro in titulos_erro:
            ws.append([
                t.numero, t.fornecedor_nome, t.valor_liquido, erro
            ])
            
        ws.auto_filter.ref = f"A1:D{len(titulos_erro)+1}"
        self._auto_fit_columns(ws)

    def _auto_fit_columns(self, ws):
        for col in ws.columns:
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(cell.value)
                except:
                    pass
            adjusted_width = (max_length + 2)
            ws.column_dimensions[column].width = min(adjusted_width, 50) # limite 50
