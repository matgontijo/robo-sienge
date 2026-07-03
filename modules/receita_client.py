import os
import re
import json
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests
from loguru import logger

# Cache válido por 30 dias — regime tributário muda no máximo na virada do ano
_VALIDADE_DIAS = 30
# ReceitaWS free tier: 3 consultas/minuto
_INTERVALO_MIN_S = 21


class ReceitaClient:
    """
    Regime tributário do fornecedor pelo CNPJ (Simples Nacional ou não).

    Fontes, na ordem:
      1. cache em disco (30 dias) — consultas repetidas custam zero;
      2. ReceitaWS (gratuita, 3 req/min) — por isso a consulta é LAZY:
         só quando uma regra de imposto precisa da resposta.
    Retorna True (optante), False (não optante) ou None (desconhecido).
    """

    _lock = threading.Lock()
    _ultima_chamada = 0.0

    def __init__(self, cache_path: str = None):
        self._cache_path = cache_path
        self._cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Cache de CNPJ ilegível ({cache_path}): {e}")

    def _salvar(self):
        if not self._cache_path:
            return
        try:
            tmp = self._cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False)
            os.replace(tmp, self._cache_path)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Falha ao salvar cache de CNPJ: {e}")

    @staticmethod
    def _digitos(cnpj) -> str:
        return re.sub(r"\D", "", str(cnpj or ""))

    def registrar_hint(self, cnpj, simples: bool, fonte: str = "nfse"):
        """Grava informação vinda de outra fonte (ex.: NFS-e que declara
        'optante pelo Simples Nacional')."""
        c = self._digitos(cnpj)
        if len(c) != 14:
            return
        self._cache[c] = {"simples": bool(simples), "fonte": fonte,
                          "consultado_em": datetime.now().isoformat()}
        self._salvar()

    def regime_conhecido(self, cnpj) -> Optional[bool]:
        """Só olha o cache (sem HTTP). None = desconhecido."""
        c = self._digitos(cnpj)
        item = self._cache.get(c)
        if not item:
            return None
        try:
            idade = datetime.now() - datetime.fromisoformat(item["consultado_em"])
            if idade > timedelta(days=_VALIDADE_DIAS):
                return None
        except (KeyError, ValueError):
            return None
        return item.get("simples")

    def consultar_simples(self, cnpj) -> Optional[bool]:
        """Cache -> ReceitaWS (com ritmo de free tier). Lazy por natureza:
        chame apenas quando a resposta importa para uma regra."""
        c = self._digitos(cnpj)
        if len(c) != 14:
            return None
        conhecido = self.regime_conhecido(c)
        if conhecido is not None:
            return conhecido

        with self._lock:
            # respeita 3 req/min do free tier
            espera = _INTERVALO_MIN_S - (time.time() - ReceitaClient._ultima_chamada)
            if espera > 0:
                time.sleep(espera)
            ReceitaClient._ultima_chamada = time.time()
            try:
                r = requests.get(f"https://receitaws.com.br/v1/cnpj/{c}", timeout=30)
                if r.status_code == 429:
                    logger.warning("ReceitaWS 429 — regime fica desconhecido neste ciclo.")
                    return None
                r.raise_for_status()
                data = r.json()
                optante = (data.get("simples") or {}).get("optante")
                if optante is None:
                    optante = data.get("simei", {}).get("optante")
                if optante is not None:
                    self._cache[c] = {"simples": bool(optante), "fonte": "receitaws",
                                      "consultado_em": datetime.now().isoformat()}
                    self._salvar()
                    logger.info(f"Regime do CNPJ {c}: {'Simples' if optante else 'normal'} (ReceitaWS)")
                return optante
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Falha ao consultar CNPJ {c} na ReceitaWS: {e}")
                return None
