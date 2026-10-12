"""«Procesar clases pendientes» por lotes con la Batch API de Claude (cliente simulado)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.integration.test_services import _record
from tests.integration.test_services import services as services

from transcriptor.config.models import AIConfig
from transcriptor.llm.anthropic_provider import AnthropicProvider
from transcriptor.llm.composite import BudgetedProvider, FallbackProvider
from transcriptor.services import AppServices

SUMMARY = {"titulo": "Integración por partes", "resumen": "Resumen por lotes.", "puntos_clave": [],
           "temas": ["Integración por partes"],
           "tareas": [{"descripcion": "Hoja 4", "fecha_texto": "el jueves", "tipo": "tarea", "minuto": ""}]}


class FakeBatches:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.status = "in_progress"

    def create(self, requests: list[dict[str, Any]]) -> Any:
        self.created = requests
        return SimpleNamespace(id="msgbatch_1")

    def retrieve(self, batch_id: str) -> Any:
        return SimpleNamespace(processing_status=self.status)

    def results(self, batch_id: str) -> list[Any]:
        msg = SimpleNamespace(
            stop_reason="end_turn", stop_details=None, model="claude-haiku-5-5",
            content=[SimpleNamespace(type="text", text=json.dumps(SUMMARY))],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0),
        )
        return [SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(type="succeeded", message=msg))
                for r in self.created]


def test_pending_classes_processed_by_batch(services: AppServices, vault: Path) -> None:
    s1, s2 = _record(services), _record(services)
    batches = FakeBatches()
    client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
    provider = AnthropicProvider(None, AIConfig().models["anthropic"], client=client)
    services._provider = BudgetedProvider(FallbackProvider([provider]), services.ledger, None)

    assert services.process_pending() == 2
    assert {r["custom_id"] for r in batches.created} == {s1, s2}
    assert batches.created[0]["params"]["output_config"]["format"]["type"] == "json_schema"
    assert services.process_pending() == 0  # no se reenvían mientras el lote está en curso
    assert services.check_batches() == 0

    batches.status = "ended"
    assert services.check_batches() == 2
    for sid in (s1, s2):
        session = services.store.get(sid)
        assert session and session.llm_processed and session.summary
        assert session.summary.title == "Integración por partes"
        assert "Resumen por lotes." in (vault / session.note_path).read_text(encoding="utf-8")
    assert services.batched_sessions() == set()
    assert services.tokens_this_month() >= 2400
