"""Pipeline completo do pente-fino dos impostos (roda os 4 passos em ordem):
  1. impostos_historico.py  - lista os titulos de imposto 2023..hoje com apropriacao (obra + CC/plano)
  2. impostos_anexos.py     - baixa as guias anexadas (so as novas)
  3. impostos_codigos.py    - le o codigo de receita / valor de cada guia
  4. impostos_analise.py    - compara com o padrao historico; gera xlsx, JSON do painel e plano de correcao
  5. impostos_html.py       - gera o painel a parte (output/Impostos_Conferencia.html)
Uso:  python analisar_impostos.py               (demora alguns minutos: consulta a API)
      python analisar_impostos.py --so-analise  (nao consulta a API; so refaz a comparacao)
"""
import os
import subprocess
import sys

PY = sys.executable
passos = ["impostos_historico.py", "impostos_anexos.py", "impostos_codigos.py", "impostos_analise.py", "impostos_html.py"]
if "--so-analise" in sys.argv:
    passos = ["impostos_codigos.py", "impostos_analise.py", "impostos_html.py"]
env = dict(os.environ, PYTHONIOENCODING="utf-8")
for p in passos:
    print(f"\n=== {p} ===", flush=True)
    r = subprocess.run([PY, p], env=env)
    if r.returncode != 0:
        print(f"FALHOU em {p} (codigo {r.returncode})")
        sys.exit(r.returncode)
print("\nconcluido.")
