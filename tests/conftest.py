import os
import sys

# Adiciona o diretório raiz do projeto ao PYTHONPATH para os testes conseguirem importar os módulos
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
