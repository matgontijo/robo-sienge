# robo-sienge — technical portfolio notes

Source reviewed: `30d806b4a60bba0805a7f046e63d33a98b41b859` · 2026-11-04. These notes distinguish implemented behavior from next steps; they do not certify a production deployment.

## Purpose

Document extraction and accounts-payable reconciliation combining LLM-assisted reading with deterministic checks.

**Stack:** Python, FastAPI, Anthropic/Gemini integration, PDF processing, SQLAlchemy.

**AI scope:** Applied LLM extraction; no trained ML model.

## Evidence in this repository

- [modules/attachment_reader.py](../modules/attachment_reader.py): PDF processing, LLM calls, content caching, shared request pacing and deterministic payment-slip decoding.
- [orchestrator.py](../orchestrator.py): Coordinates document processing and installment-level reconciliation.
- [modules/reconciler.py](../modules/reconciler.py): Business-rule validation.
- [tests/test_attachment_reader.py](../tests/test_attachment_reader.py): Mocked extraction behavior.
- [tests/test_boleto_parcela.py](../tests/test_boleto_parcela.py): Installment selection and dossier regression cases.

## Validation

```text
python -m unittest discover -s tests -p test_cli_dry_run.py -q
```

Use an isolated environment and synthetic data. Never run operational ingestion, scheduled jobs or live provider calls merely to demonstrate the project. Test presence is not the same as a successful test run.

## Known limits and next steps

- No labeled extraction benchmark, precision/recall, error taxonomy or measured cost/latency baseline was found.
- dashboard/auth.py grants local administrator access; config.py binds to all interfaces by default. Restrict network access before using real data.
- The unsupported --dry-run flag previously executed the real cycle. The audit branch now rejects it before validation and orchestration.
- Tests in tests/test_dashboard.py include a processing trigger; do not treat the full suite as offline without isolating its side effects.

## Portfolio positioning

Strongest public AI project after security review and an anonymized, measurable extraction evaluation.

A reproducible AI evaluation should document a synthetic or anonymized dataset, baseline, expected outputs, failure cases, quality metrics, latency and cost. Training and evaluating a predictive model would be separate work; it is not claimed here.
