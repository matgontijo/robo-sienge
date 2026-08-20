"""Altera o VENCIMENTO de parcelas especificas do contas a pagar no Sienge.

Seguranca embutida:
  - so mexe nas parcelas listadas em PLANO (titulo + numero da parcela);
  - confere valor e situacao antes de alterar (aborta se nao bater);
  - grava um snapshot do estado anterior em output/_snapshot_vencimentos.json
    para permitir desfazer;
  - --dry-run (padrao) mostra o que faria sem escrever nada.

Uso:
    python alterar_vencimento.py              # simulacao, nao escreve
    python alterar_vencimento.py --executar   # aplica de verdade
"""
import json
import os
import sys
from datetime import date

import requests
from requests.auth import HTTPBasicAuth

import config

NOVA_DATA = "2026-08-14"

# titulo, parcela, valor esperado, forma esperada (conferencia de seguranca)
PLANO = [
    ("10363", 1, 2028.50, "TED"),
    ("10177", 1, 15000.00, "Pix"),
    ("8696", 4, 9216.68, "Pix"),
    ("8628", 4, 12932.38, "Pix"),
    ("8913", 4, 56057.75, "Pix"),
]

BASE = config.SIENGE_BASE_URL
AUTH = HTTPBasicAuth(config.SIENGE_USERNAME, config.SIENGE_PASSWORD)
H = {"Content-Type": "application/json"}


def parcelas(titulo):
    r = requests.get(f"{BASE}/bills/{titulo}/installments", auth=AUTH, timeout=60)
    r.raise_for_status()
    return r.json().get("results", [])


def tentar_atualizar(titulo, parcela, nova_data):
    """Endpoint oficial (bill-debt-v1): PATCH /bills/{billId}/installments/{installmentId}
    'Atualiza parcela do titulo'. installmentId = numero da parcela.
    Corpo aceita dueDate, amount, interestAmount, fineAmount, discountAmount,
    monetaryCorrectionAmount — nenhum obrigatorio. Enviamos so a data."""
    path = f"/bills/{titulo}/installments/{parcela}"
    try:
        resp = requests.patch(f"{BASE}{path}", auth=AUTH, headers=H,
                              data=json.dumps({"dueDate": nova_data}), timeout=60)
    except Exception as e:  # noqa: BLE001
        return False, f"PATCH {path}: {e}"
    if resp.status_code in (200, 201, 204):
        return True, f"PATCH {path} -> {resp.status_code}"
    return False, f"PATCH {path} -> {resp.status_code} {resp.text[:200]}"


def main(executar):
    modo = "EXECUCAO REAL" if executar else "SIMULACAO (nada sera alterado)"
    print(f"=== ALTERAR VENCIMENTO PARA {NOVA_DATA} — {modo} ===\n")

    snapshot = []
    for titulo, parcela, valor, forma in PLANO:
        try:
            itens = parcelas(titulo)
        except Exception as e:  # noqa: BLE001
            print(f"{titulo}/{parcela}: ERRO ao consultar — {e}")
            continue

        alvo = next((p for p in itens if p.get("installmentNumber") == parcela), None)
        if not alvo:
            print(f"{titulo}/{parcela}: parcela NAO ENCONTRADA — pulando")
            continue

        # conferencias de seguranca antes de escrever
        if abs(alvo.get("amount", 0) - valor) > 0.02:
            print(f"{titulo}/{parcela}: valor diverge "
                  f"(esperado {valor:,.2f}, achou {alvo.get('amount'):,.2f}) — PULANDO")
            continue
        situacao = str(alvo.get("situation", "")).lower()
        if "paga" in situacao and "não" not in situacao and "nao" not in situacao:
            print(f"{titulo}/{parcela}: parcela ja esta PAGA ({alvo.get('situation')}) — PULANDO")
            continue

        de = alvo.get("dueDate")
        snapshot.append({"titulo": titulo, "parcela": parcela, "dueDate_anterior": de,
                         "amount": alvo.get("amount"), "paymentType": alvo.get("paymentType")})

        if de == NOVA_DATA:
            print(f"{titulo}/{parcela}: ja esta em {NOVA_DATA} — nada a fazer")
            continue

        print(f"{titulo}/{parcela}  {de} -> {NOVA_DATA}  "
              f"R$ {alvo.get('amount'):,.2f}  {alvo.get('paymentType')}")

        if not executar:
            continue

        ok, desc = tentar_atualizar(titulo, parcela, NOVA_DATA)
        if ok:
            depois = next((p for p in parcelas(titulo)
                           if p.get("installmentNumber") == parcela), {})
            novo = depois.get("dueDate")
            print(f"    {'OK' if novo == NOVA_DATA else 'RESPONDEU MAS NAO MUDOU'}: "
                  f"agora {novo}  ({desc})")
        else:
            print(f"    FALHOU: {desc}")

    if snapshot:
        os.makedirs("output", exist_ok=True)
        caminho = "output/_snapshot_vencimentos.json"
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1)
        print(f"\nsnapshot do estado anterior: {caminho}")

    if not executar:
        print("\n>>> Nada foi alterado. Para aplicar de verdade:")
        print("    python alterar_vencimento.py --executar")


if __name__ == "__main__":
    main("--executar" in sys.argv)
