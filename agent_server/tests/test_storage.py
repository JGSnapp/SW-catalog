from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from agent_server.models import AppConfig, SiteConfig, SiteCreate, utc_now_iso
    from agent_server.storage import JsonStorage
    from agent_server.tools.jina_reader import build_reader_url
    from agent_server.tools.xml_search import extract_results, parse_xml
except ModuleNotFoundError:  # pragma: no cover
    from models import AppConfig, SiteConfig, SiteCreate, utc_now_iso
    from storage import JsonStorage
    from tools.jina_reader import build_reader_url
    from tools.xml_search import extract_results, parse_xml


def test_build_reader_url():
    url = build_reader_url("https://example.com/a path?q=1")
    assert url.startswith("https://r.jina.ai/https://example.com/")
    assert "a%20path" in url


def test_extract_results_limits():
    root = parse_xml(
        b"""
        <response>
          <results>
            <grouping>
              <group>
                <doc>
                  <url>https://a.example/grant</url>
                  <title>Grant A</title>
                  <passages>
                    <passage>One</passage>
                    <passage>Two</passage>
                    <passage>Three</passage>
                  </passages>
                </doc>
              </group>
            </grouping>
          </results>
        </response>
        """
    )
    results = extract_results(root, limit_docs=1, limit_passages=2)
    assert results == [{"title": "Grant A", "url": "https://a.example/grant", "passages": ["One", "Two"]}]


def test_upsert_grant_and_text_files(tmp_path: Path):
    storage = JsonStorage(tmp_path)
    site = storage.add_site(SiteCreate(label="Site A", url="https://site-a.example", enabled=True))
    run = storage.create_run(site.id, site.url)
    first = storage.upsert_grant(
        site_id=site.id,
        site_url=site.url,
        run_id=run.id,
        title="Grant",
        institution="Institute A",
        amount="5 млн рублей",
        funding_type="субсидия",
        category="сертификация",
        conditions="Condition A",
        restrictions="Restriction A",
        deadline="2026-05-01",
        application_url="https://site-a.example/apply",
        site="Site A",
        description="Desc",
        source="https://site-a.example/grant",
    )
    second = storage.upsert_grant(
        site_id=site.id,
        site_url=site.url,
        run_id=run.id,
        title="Grant",
        institution="Institute B",
        amount="7 млн рублей",
        funding_type="грант",
        category="робототехника",
        conditions="Condition B",
        restrictions="Restriction B",
        deadline="2026-05-02",
        application_url="https://site-a.example/apply-2",
        site="Site A",
        description="Desc 2",
        source="https://site-a.example/grant",
    )
    assert first.id == second.id
    assert len(storage.list_grants()) == 1
    assert storage.list_grants()[0].institution == "Institute B"
    third = storage.upsert_grant(
        site_id=site.id,
        site_url=site.url,
        run_id=run.id,
        title="Another Grant",
        institution="Institute C",
        conditions="Condition C",
        deadline="2026-05-03",
        site="Site A",
        description="Desc 3",
        source="https://site-a.example/grant",
    )
    assert third.id != first.id
    assert len(storage.list_grants()) == 2
    assert storage.list_grants()[0].status == "new"
    updated_status = storage.update_grant_status(first.id, "suitable")
    assert updated_status.status == "suitable"
    assert {grant.id for grant in storage.list_unnotified_grants_for_run(run.id)} == {first.id, third.id}
    storage.mark_grant_telegram_notified(first.id)
    assert [grant.id for grant in storage.list_unnotified_grants_for_run(run.id)] == [third.id]
    storage.write_notes(site.id, "notes")
    storage.write_status(site.id, "status")
    assert storage.read_notes(site.id)[0].strip() == "notes"
    assert storage.read_status(site.id)[0].strip() == "status"


def test_due_sites_and_schedule_fields(tmp_path: Path):
    storage = JsonStorage(tmp_path)
    now = utc_now_iso()
    config = AppConfig(
        company_profile="",
        global_prompt="",
        interval_hours=6,
        iterations_per_site=4,
        sites=[
            SiteConfig(
                id="site-1",
                label="Site 1",
                url="https://site-1.example",
                enabled=True,
                created_at=now,
                updated_at=now,
                next_run_at=now,
            ),
            SiteConfig(
                id="site-2",
                label="Site 2",
                url="https://site-2.example",
                enabled=False,
                created_at=now,
                updated_at=now,
                next_run_at=now,
            ),
        ],
    )
    storage.replace_config(config)
    due = storage.due_sites()
    assert [site.id for site in due] == ["site-1"]
    storage.mark_site_run_started("site-1")
    updated = storage.mark_site_run_finished("site-1", 6)
    assert updated.next_run_at is not None
