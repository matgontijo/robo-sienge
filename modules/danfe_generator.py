import os
from lxml import etree
from loguru import logger
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import code128

class DanfeGenerator:
    def __init__(self):
        self.ns = {"nfe": "http://www.portalfiscal.inf.br/nfe"}

    def _get_text(self, node, path):
        elem = node.find(path, namespaces=self.ns)
        return elem.text if elem is not None else ""

    def gerar_pdf(self, xml_str: str, output_path: str) -> str:
        """
        Parseia XML com lxml e gera DANFE simplificado com reportlab.
        """
        try:
            root = etree.fromstring(xml_str.encode('utf-8'))
            
            # Navega at a tag infNFe
            infNFe = root.find(".//nfe:infNFe", namespaces=self.ns)
            if infNFe is None:
                raise ValueError("Tag infNFe no encontrada no XML")

            chave = infNFe.get("Id", "").replace("NFe", "")
            
            ide = infNFe.find("nfe:ide", namespaces=self.ns)
            numero = self._get_text(ide, "nfe:nNF")
            serie = self._get_text(ide, "nfe:serie")
            data_emissao = self._get_text(ide, "nfe:dhEmi")[:10]
            
            emit = infNFe.find("nfe:emit", namespaces=self.ns)
            emit_cnpj = self._get_text(emit, "nfe:CNPJ")
            emit_nome = self._get_text(emit, "nfe:xNome")
            
            dest = infNFe.find("nfe:dest", namespaces=self.ns)
            dest_cnpj = self._get_text(dest, "nfe:CNPJ") or self._get_text(dest, "nfe:CPF")
            dest_nome = self._get_text(dest, "nfe:xNome")
            
            total = infNFe.find("nfe:total/nfe:ICMSTot", namespaces=self.ns)
            v_prod = self._get_text(total, "nfe:vProd")
            v_desc = self._get_text(total, "nfe:vDesc")
            v_frete = self._get_text(total, "nfe:vFrete")
            v_nf = self._get_text(total, "nfe:vNF")
            
            itens = []
            for det in infNFe.findall("nfe:det", namespaces=self.ns):
                prod = det.find("nfe:prod", namespaces=self.ns)
                itens.append({
                    "descricao": self._get_text(prod, "nfe:xProd")[:50], # Limita tamanho
                    "qtd": self._get_text(prod, "nfe:qCom"),
                    "v_unit": self._get_text(prod, "nfe:vUnCom"),
                    "v_total": self._get_text(prod, "nfe:vProd")
                })

            # Gera o PDF
            c = canvas.Canvas(output_path, pagesize=landscape(A4))
            width, height = landscape(A4)
            
            def draw_header(page_num, total_pages):
                c.setFont("Helvetica-Bold", 14)
                c.drawString(30, height - 40, "DANFE SIMPLIFICADO")
                c.setFont("Helvetica", 10)
                c.drawString(30, height - 60, f"Nmero: {numero}  Srie: {serie}  Data Emisso: {data_emissao}")
                c.drawString(30, height - 75, f"Pgina {page_num} de {total_pages}")
                
                c.setFont("Helvetica-Bold", 10)
                c.drawString(30, height - 100, "EMITENTE:")
                c.setFont("Helvetica", 10)
                c.drawString(30, height - 115, f"{emit_nome} - CNPJ: {emit_cnpj}")
                
                c.setFont("Helvetica-Bold", 10)
                c.drawString(width/2 + 30, height - 100, "CHAVE DE ACESSO:")
                c.setFont("Helvetica", 10)
                # Formata a chave em blocos de 4
                chave_fmt = " ".join([chave[i:i+4] for i in range(0, len(chave), 4)])
                c.drawString(width/2 + 30, height - 115, chave_fmt)
                
                # Cdigo de barras
                barcode = code128.Code128(chave, barHeight=30, barWidth=1.2)
                barcode.drawOn(c, width/2 + 30, height - 160)
                
            def draw_footer():
                c.setFont("Helvetica-Bold", 10)
                c.drawString(30, 80, "DESTINATRIO:")
                c.setFont("Helvetica", 10)
                c.drawString(30, 65, f"{dest_nome} - CNPJ/CPF: {dest_cnpj}")
                
                c.setFont("Helvetica-Bold", 10)
                c.drawString(width/2 + 30, 80, "TOTAIS:")
                c.setFont("Helvetica", 10)
                c.drawString(width/2 + 30, 65, f"Produtos: {v_prod} | Frete: {v_frete} | Desc: {v_desc} | TOTAL: {v_nf}")
                
            itens_por_pagina = 10
            total_pages = (len(itens) + itens_por_pagina - 1) // itens_por_pagina
            if total_pages == 0: total_pages = 1
            
            for page in range(total_pages):
                if page > 0:
                    c.showPage()
                    
                draw_header(page + 1, total_pages)
                
                # Tabela de itens
                c.setFont("Helvetica-Bold", 10)
                c.drawString(30, height - 200, "DESCRIO")
                c.drawString(450, height - 200, "QTD")
                c.drawString(550, height - 200, "V. UNIT")
                c.drawString(650, height - 200, "V. TOTAL")
                
                c.line(30, height - 205, width - 30, height - 205)
                
                c.setFont("Helvetica", 9)
                y = height - 225
                
                start_idx = page * itens_por_pagina
                end_idx = min(start_idx + itens_por_pagina, len(itens))
                
                for item in itens[start_idx:end_idx]:
                    c.drawString(30, y, item["descricao"])
                    c.drawString(450, y, item["qtd"])
                    c.drawString(550, y, item["v_unit"])
                    c.drawString(650, y, item["v_total"])
                    y -= 20
                    
                draw_footer()
                
            c.save()
            logger.success(f"DANFE gerado com sucesso em {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"Falha ao gerar DANFE para XML: {e}")
            return None
