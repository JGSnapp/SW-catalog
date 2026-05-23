from __future__ import annotations

import time
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from agent_server.main import RunManager, compute_runner_max_turns, create_app
    from agent_server.prompts import SITE_AGENT_PROMPT
except ModuleNotFoundError:  # pragma: no cover
    from main import RunManager, compute_runner_max_turns, create_app
    from prompts import SITE_AGENT_PROMPT


async def fake_agent_run(self: RunManager, site_id: str, site_label: str, site_url: str, run_id: str) -> str:
    self.storage.write_notes(site_id, f"Notes for {site_label}")
    self.storage.write_status(site_id, f"Status for {site_label}")
    self.storage.upsert_grant(
        site_id=site_id,
        site_url=site_url,
        run_id=run_id,
        title=f"{site_label} Grant",
        institution="Institute A",
        amount="1 000 000 рублей",
        funding_type="грант",
        category="робототехника",
        conditions="Must fit profile",
        restrictions="No restrictions",
        deadline="2026-12-31",
        application_url=f"{site_url}/apply",
        site=site_label,
        description="Found during test run",
        source=f"{site_url}/grant",
    )
    return "completed"


async def fake_agent_run_max_turns(self: RunManager, site_id: str, site_label: str, site_url: str, run_id: str) -> str:
    raise RuntimeError("Max turns (10) exceeded")


def wait_for_run_completion(client: TestClient, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = client.get("/api/runs").json()
        if runs and runs[0]["status"] in {"completed", "failed"}:
            return
        time.sleep(0.05)
    raise AssertionError("run did not finish in time")


def test_manual_run_creates_run_and_grant(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(RunManager, "_run_agent_for_site", fake_agent_run)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    with client:
        created = client.post("/api/sites", json={"label": "Site A", "url": "https://site-a.example", "enabled": True})
        site_id = created.json()["id"]
        response = client.post(f"/api/sites/{site_id}/run")
        assert response.status_code == 200
        wait_for_run_completion(client)
        runs = client.get("/api/runs").json()
        assert runs[0]["status"] == "completed"
        grants = client.get("/api/grants").json()
        assert grants[0]["source"] == "https://site-a.example/grant"
        assert grants[0]["institution"] == "Institute A"
        assert grants[0]["category"] == "робототехника"
        assert grants[0]["status"] == "new"
        assert grants[0]["telegram_notified_at"] is None
        status_response = client.put(f"/api/grants/{grants[0]['id']}/status", json={"status": "suitable"})
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "suitable"
        status = client.get(f"/api/sites/{site_id}/status").json()
        assert "Status for Site A" in status["content"]
        events = client.get(f"/api/runs/{runs[0]['id']}/events").json()
        assert any(event["event_type"] == "telegram_skipped" for event in events)


def test_put_config_changes_scheduler_inputs(tmp_path: Path):
    app = create_app(tmp_path)
    client = TestClient(app)
    with client:
        payload = {
            "company_profile": "B2B SaaS company",
            "global_prompt": "Focus on export grants",
            "target_institutions": "Institute A",
            "search_directions": "робототехника",
            "min_amount": "1 млн",
            "max_amount": "50 млн",
            "funding_types": "грант, субсидия",
            "regions": "Россия",
            "deadline_window": "6 месяцев",
            "eligibility_requirements": "для технологических компаний",
            "excluded_restrictions": "только для вузов",
            "keywords": "робототехника, НИОКР",
            "interval_hours": 8,
            "iterations_per_site": 7,
            "sites": [],
        }
        response = client.put("/api/config", json=payload)
        assert response.status_code == 200
        config = client.get("/api/config").json()
        assert config["interval_hours"] == 8
        assert config["iterations_per_site"] == 7
        assert config["target_institutions"] == "Institute A"
        assert config["search_directions"] == "робототехника"


def test_max_turns_is_recorded_as_completed(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(RunManager, "_run_agent_for_site", fake_agent_run_max_turns)
    app = create_app(tmp_path)
    client = TestClient(app)
    with client:
        created = client.post("/api/sites", json={"label": "Site A", "url": "https://site-a.example", "enabled": True})
        site_id = created.json()["id"]
        response = client.post(f"/api/sites/{site_id}/run")
        assert response.status_code == 200
        wait_for_run_completion(client)
        runs = client.get("/api/runs").json()
        assert runs[0]["status"] == "completed"
        assert "Max turns" in (runs[0]["error"] or "")


def test_runner_turns_have_finalization_buffer():
    assert compute_runner_max_turns(1) == 5
    assert compute_runner_max_turns(7) == 11


def test_prompt_enforces_russian_outputs():
    assert "Write all outputs in Russian" in SITE_AGENT_PROMPT


def test_prompt_mentions_document_inspection():
    assert "PDF, DOC, DOCX, XLS, XLSX" in SITE_AGENT_PROMPT
