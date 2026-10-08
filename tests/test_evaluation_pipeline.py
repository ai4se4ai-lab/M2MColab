"""End-to-end test of the quick/mock evaluation pipeline (evaluation/):
synthetic fixture tasks + MockBackend, no network, no Docker, no real
SWE-bench download. Exercises the three hand-off configurations, the MAST
annotator, the impact injector, aggregate.py's table logic, and
make_figures.py's PNG output, calling their importable core functions
directly rather than shelling out (per evaluation/'s "thin CLI wrapper
around importable functions" convention).
"""
from __future__ import annotations

import sys
import pytest

# Tests the earlier DevBench pilot harness (not part of this tree).
pytest.importorskip("evaluation.harness.free_text_config", reason="legacy DevBench pilot harness not present")

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentm2m.llm.mock_backend import MockBackend

from evaluation.analysis.aggregate import compute_table, write_pilot_csv, write_raw_csv, ROW_ORDER, CONFIG_COLUMNS
from evaluation.analysis.make_figures import make_all_figures
from evaluation.harness.agentm2m_config import CONFIG_NAME as agentm2m, run_agentm2m
from evaluation.harness.common import default_tasks_file, load_tasks
from evaluation.harness.free_text_config import CONFIG_NAME as FREE_TEXT, run_free_text
from evaluation.harness.impact_injector import run_impact_injection
from evaluation.harness.mast_annotator import MAST_MODES, annotate
from evaluation.harness.run_all_configs import run_all
from evaluation.harness.shared_schema_config import CONFIG_NAME as SHARED_SCHEMA, run_shared_schema


def _fixture_tasks() -> list[dict]:
    tasks = load_tasks(default_tasks_file())
    assert len(tasks) >= 2
    return tasks


def test_fixture_tasks_load():
    tasks = _fixture_tasks()
    for t in tasks:
        assert t["instance_id"]
        assert t["problem_statement"]


def test_free_text_config_produces_output():
    task = _fixture_tasks()[0]
    result = run_free_text(task, MockBackend())
    assert result.config == FREE_TEXT
    assert result.instance_id == task["instance_id"]
    assert result.patch_text
    assert len(result.turns) == 3
    assert result.transcript_text()


def test_shared_schema_config_produces_output_and_validates():
    task = _fixture_tasks()[0]
    result = run_shared_schema(task, MockBackend())
    assert result.config == SHARED_SCHEMA
    assert result.patch_text
    assert "shared_state" in result.extra
    assert set(result.extra["shared_state"].keys()) == {"issue", "plan", "patch"}


def test_agentm2m_config_runs_real_engine():
    task = _fixture_tasks()[0]
    result = run_agentm2m(task, MockBackend())
    assert result.config == agentm2m
    assert result.patch_text
    assert result.extra["phi_holds"] is True
    assert result.extra["obligations"] > 0
    assert result.extra["escalations"] == []


def test_all_three_configs_run_on_every_fixture_task():
    tasks = _fixture_tasks()
    for task in tasks:
        for runner in (run_free_text, run_shared_schema, run_agentm2m):
            result = runner(task, MockBackend())
            assert result.patch_text, f"{runner.__name__} produced no output for {task['instance_id']}"


def test_mast_annotator_returns_known_modes_only():
    annotation = annotate("toy-1", "free_text", "Analyst said X. Architect ignored X and said Y.", MockBackend())
    assert set(annotation.modes_present) <= set(MAST_MODES)
    assert isinstance(annotation.p1_count, int)
    assert isinstance(annotation.p2_count, int)


def test_impact_injector_agentm2m_has_perfect_recall():
    result = run_impact_injection(MockBackend(), mode="agentm2m")
    # Proposition 2 predicts recall = 1 (every truly-affected binding is
    # rediscovered); the negative-control second task should not appear.
    assert result.recall == 1.0
    assert result.oracle <= result.predicted
    assert all("impact-B" not in target for _, target in result.predicted)


def test_impact_injector_shared_schema_is_lower_precision_same_recall():
    agentm2m = run_impact_injection(MockBackend(), mode="agentm2m")
    shared = run_impact_injection(MockBackend(), mode="shared_schema")
    assert shared.recall == 1.0
    assert shared.precision <= agentm2m.precision


def test_run_all_produces_one_record_per_task_per_config():
    tasks = _fixture_tasks()
    records = run_all(tasks, llm=MockBackend())
    assert len(records) == len(tasks) * 3
    configs = {r["config"] for r in records}
    assert configs == {FREE_TEXT, SHARED_SCHEMA, agentm2m}
    for r in records:
        assert "mast_modes" in r
        assert "total_tokens" in r
        assert r["total_tokens"] >= 0


def test_aggregate_table_has_right_rows_and_columns():
    tasks = _fixture_tasks()
    records = run_all(tasks, llm=MockBackend())
    impact_metrics = {
        "free_text": (None, None),
        "shared_schema": (0.5, 1.0),
        "agentm2m": (1.0, 1.0),
    }
    table = compute_table(records, impact_metrics=impact_metrics)

    assert list(table.keys()) == ROW_ORDER
    expected_columns = {label for _, label in CONFIG_COLUMNS}
    for row in ROW_ORDER:
        assert set(table[row].keys()) == expected_columns

    assert table["Impact recall / precision (RQ2)"]["Free text"] == "--"
    assert table["Impact recall / precision (RQ2)"]["agentm2m"] == "1.00 / 1.00"


def test_aggregate_writes_csv_files(tmp_path):
    tasks = _fixture_tasks()
    records = run_all(tasks, llm=MockBackend())
    table = compute_table(records, impact_metrics={"free_text": (None, None), "shared_schema": (0.5, 1.0), "agentm2m": (1.0, 1.0)})

    pilot_path = write_pilot_csv(table, tmp_path / "pilot_table.csv")
    raw_path = write_raw_csv(records, tmp_path / "raw_metrics.csv")

    assert pilot_path.exists() and pilot_path.stat().st_size > 0
    assert raw_path.exists() and raw_path.stat().st_size > 0

    pilot_text = pilot_path.read_text()
    assert "Metric,Free text,Shared schema,agentm2m" in pilot_text
    for row in ROW_ORDER:
        assert row in pilot_text


def test_make_figures_produces_expected_pngs(tmp_path):
    tasks = _fixture_tasks()
    records = run_all(tasks, llm=MockBackend())
    paths = make_all_figures(records, out_dir=tmp_path)

    assert len(paths) == 4
    expected_names = {
        "mast_modes_per_trace.png",
        "task_success.png",
        "impact_precision_recall.png",
        "tokens_per_task.png",
    }
    assert {p.name for p in paths} == expected_names
    for p in paths:
        assert p.exists()
        assert p.stat().st_size > 0
