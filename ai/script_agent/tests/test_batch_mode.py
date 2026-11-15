"""Unit and integration tests for Gemini Batch API client and batch execution mode."""
import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from script_agent.batch import GeminiBatchClient, parse_item_response, process_batch_queue
from script_agent.io import atomic_json, load_config
from script_agent.models import Draft, Material, Review, SourceMeta
from script_agent.pipeline import Pipeline
from script_agent.providers import Completion, ProviderError

ROOT = Path(__file__).resolve().parents[1]


class DummyOutput(BaseModel):
    title: str
    count: int


def test_batch_client_create_and_extract_inlined(monkeypatch):
    client = GeminiBatchClient(key="test-api-key")

    mock_resp = MagicMock()
    mock_resp.is_error = False
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"name": "batches/mock-123"}

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        batch_name = client.create_batch(
            model="gemini-3.8-flash",
            requests=[{"request": {"contents": []}, "metadata": {"key": "req-1"}}],
            display_name="test-batch"
        )
        assert batch_name == "batches/mock-123"
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["headers"]["x-goog-api-key"] == "test-api-key"
        assert call_kwargs["json"]["batch"]["displayName"] == "test-batch"

    # Test extracting inlined responses
    batch_data = {
        "response": {
            "inlinedResponses": {
                "inlinedResponses": [
                    {"metadata": {"key": "req-1"}, "response": {"candidates": []}}
                ]
            }
        }
    }
    items = client.extract_items(batch_data)
    assert len(items) == 1
    assert items[0]["metadata"]["key"] == "req-1"


def test_batch_client_extract_file_dest(monkeypatch):
    client = GeminiBatchClient(key="test-api-key")

    file_content = json.dumps({"metadata": {"key": "k1"}, "response": {"candidates": []}}) + "\n" + \
                   json.dumps({"metadata": {"key": "k2"}, "response": {"candidates": []}})

    with patch.object(client, "download_file", return_value=file_content) as mock_download:
        batch_data = {"dest": {"fileName": "files/mock-result-file"}}
        items = client.extract_items(batch_data)
        assert len(items) == 2
        assert items[0]["metadata"]["key"] == "k1"
        assert items[1]["metadata"]["key"] == "k2"
        mock_download.assert_called_once_with("files/mock-result-file")


def test_batch_client_poll_batch():
    client = GeminiBatchClient(key="test-api-key")
    responses = [
        {"state": "BATCH_STATE_RUNNING"},
        {"state": "BATCH_STATE_RUNNING"},
        {"state": "BATCH_STATE_SUCCEEDED", "response": {}}
    ]

    states_seen = []

    def on_poll(data):
        states_seen.append(data.get("state"))

    with patch.object(client, "get_batch", side_effect=responses), \
         patch("time.sleep", return_value=None):
        final = client.poll_batch("batches/test-id", interval_sec=0.01, on_poll=on_poll)
        assert final["state"] == "BATCH_STATE_SUCCEEDED"
        assert states_seen == ["BATCH_STATE_RUNNING", "BATCH_STATE_RUNNING", "BATCH_STATE_SUCCEEDED"]


def test_parse_item_response_success():
    item_data = {
        "metadata": {"key": "req-1"},
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": "internal reasoning..."},
                            {"text": json.dumps({"title": "Test Title", "count": 42})}
                        ]
                    },
                    "finishReason": "STOP"
                }
            ],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
            "modelVersion": "gemini-3.8-flash"
        }
    }
    comp = parse_item_response(item_data, DummyOutput, "gemini-3.8-flash")
    assert isinstance(comp.value, DummyOutput)
    assert comp.value.title == "Test Title"
    assert comp.value.count == 42
    assert comp.response_model == "gemini-3.8-flash"
    assert comp.usage["totalTokenCount"] == 15


def test_parse_item_response_errors():
    # Item with error
    with pytest.raises(ProviderError) as exc_info:
        parse_item_response({"error": {"code": 3, "message": "Invalid argument"}}, DummyOutput, "gemini-3.8-flash")
    assert exc_info.value.code == "batch_item_error"

    # Finish reason safety block
    blocked_item = {
        "response": {
            "candidates": [{"finishReason": "SAFETY"}],
            "usageMetadata": {"promptTokenCount": 5}
        }
    }
    with pytest.raises(ProviderError) as exc_info:
        parse_item_response(blocked_item, DummyOutput, "gemini-3.8-flash")
    assert exc_info.value.code == "incomplete_or_blocked"
    assert exc_info.value.diagnostics["finish_reason"] == "SAFETY"

    # Invalid structured output
    bad_json_item = {
        "response": {
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not-valid-json"}]}}]
        }
    }
    with pytest.raises(ProviderError) as exc_info:
        parse_item_response(bad_json_item, DummyOutput, "gemini-3.8-flash")
    assert exc_info.value.code == "invalid_structured_output"


def test_process_batch_queue_end_to_end(tmp_path):
    # Setup test project directory
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    config = load_config(ROOT)
    config["batch"] = {"batch_size": 10, "poll_interval_sec": 0.01, "gpt_workers": 2}

    # Load example fixture data
    fixture_path = ROOT / "examples/reviewer_cases/adaptation.json"
    case_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    material_obj = Material.model_validate(case_data["material"])
    draft_obj = Draft.model_validate(case_data["draft"])
    # Modify draft to pass deterministic length and sentence checks
    draft_obj.script = [
        "돈을 주려다 사이가 틀어질 뻔했어요.", "이 이야기는 가상 사연입니다.",
        "민수는 두 달간 친구에게 신세를 졌어요.", "이제 떠나며 월세를 나눠 내고 싶었죠.",
        "친구는 그 제안을 받아들이지 않았어요.", "민수는 돈부터 보내 놓을까 망설였어요.",
        "그러고는 연락을 막아 둘 생각도 했죠.", "하지만 둘 다 생각에만 그쳤습니다.",
        "친구의 속내는 아직 알 수 없어요."
    ]
    from script_agent.models import Claim
    ids = [[], [], ["f1"], ["f1"], ["f1"], ["f2"], ["f2"], ["f3"], []]
    draft_obj.claims = [
        Claim(sentence=i, kind="source_statement" if facts else "transition", fact_ids=facts)
        for i, facts in enumerate(ids, 1)
    ]

    # Create an input file in tmp_path / 'input'
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    source_file = input_dir / "test-source.md"
    source_meta = SourceMeta.model_validate(case_data["metadata"])
    import yaml
    frontmatter = yaml.dump(source_meta.model_dump(), allow_unicode=True)
    source_file.write_text(f"---\n{frontmatter}\n---\n{case_data['source_text']}", encoding="utf-8")

    class MockReviewer:
        def complete(self, system, payload, schema):
            rev = Review.model_validate({
                "verdict": "pass",
                "issues": [],
                "scores": {"grounding": 4, "listening": 4, "engagement": 4, "type_fit": 4},
                "strengths": ["Clear narrative flow"],
                "remaining": []
            })
            return Completion(rev, {}, 0.05, "gpt-6-luna")

    class MockGenerator:
        keys = {"gemini_api_key": "mock-gemini-key"}

    pipeline = Pipeline(tmp_path, config, {}, providers={"generator": MockGenerator(), "reviewer": MockReviewer()})

    # Mock GeminiBatchClient to simulate Batch Analyze and Batch Generate
    def mock_create_batch(model, requests, display_name):
        return f"batches/mock-{display_name}"

    def mock_poll_batch(batch_name, interval_sec=15.0, timeout_sec=86400, on_poll=None):
        return {"state": "BATCH_STATE_SUCCEEDED", "name": batch_name}

    def mock_extract_items(batch_data):
        bname = batch_data.get("name", "")
        # Find job id from directory
        job_dir = next((tmp_path / "processing").glob("*"))
        key = job_dir.name
        if "analyze" in bname:
            resp_obj = material_obj.model_dump()
        else:
            resp_obj = draft_obj.model_dump()
        return [
            {
                "metadata": {"key": key},
                "response": {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": json.dumps(resp_obj, ensure_ascii=False)}]},
                            "finishReason": "STOP"
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 200, "totalTokenCount": 300},
                    "modelVersion": "gemini-3.8-flash"
                }
            }
        ]

    with patch.object(GeminiBatchClient, "create_batch", side_effect=mock_create_batch), \
         patch.object(GeminiBatchClient, "poll_batch", side_effect=mock_poll_batch), \
         patch.object(GeminiBatchClient, "extract_items", side_effect=mock_extract_items):
        results = process_batch_queue(tmp_path, pipeline, limit=10)

    assert len(results) == 1
    assert results[0]["status"] == "completed"
    assert results[0]["output_status"] == "ready_for_downstream_review"

    # Verify output JSON created
    content_id = results[0]["content_id"]
    output_file = tmp_path / "output" / "_unbatched" / f"{content_id}.json"
    assert output_file.exists()
    out = json.loads(output_file.read_text(encoding="utf-8"))
    assert out["content_id"] == content_id
    assert out["generator"]["feedback_revision"] == 0
    assert out["title"] == draft_obj.title
    assert len(out["script"]) == 9
    assert out["status"] == "ready_for_downstream_review"

    # Verify job archived
    assert (tmp_path / "state" / "completed").exists()
    assert len(list((tmp_path / "state" / "completed").glob("*"))) == 1


def test_batch_queue_rejects_legacy_cache_without_contract(tmp_path):
    """Old caches cannot prove which settings or material produced the review."""
    config = load_config(ROOT)
    shutil.copy2(ROOT / "config.toml", tmp_path / "config.toml")

    # Load fixture
    fixture_path = ROOT / "examples/reviewer_cases/adaptation.json"
    case_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")

    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    source_file = input_dir / "resume-test.md"
    source_meta = SourceMeta.model_validate(case_data["metadata"])
    import yaml
    frontmatter = yaml.dump(source_meta.model_dump(), allow_unicode=True)
    source_file.write_text(f"---\n{frontmatter}\n---\n{case_data['source_text']}", encoding="utf-8")

    class MockGenerator:
        keys = {"gemini_api_key": "mock-key"}
    class MockReviewer:
        pass

    pipeline = Pipeline(tmp_path, config, {}, providers={"generator": MockGenerator(), "reviewer": MockReviewer()})

    # Pre-seed processing directory and logs simulating a resumed run
    from script_agent.io import read_source
    text, meta, digest = read_source(source_file, 60000)
    cid = f"S2-{digest[:16]}"
    log_dir = tmp_path / "logs" / cid
    log_dir.mkdir(parents=True)

    mat_obj = Material.model_validate(case_data["material"])
    draft_obj = Draft.model_validate(case_data["draft"])
    rev_obj = Review.model_validate({
        "verdict": "pass",
        "issues": [],
        "scores": {"grounding": 4, "listening": 4, "engagement": 4, "type_fit": 4},
        "strengths": ["All good"],
        "remaining": []
    })
    dummy_event = {
        "stage": "generate", "model": "gemini-3.8-flash", "response_model": "gemini-3.8-flash",
        "effort": "medium", "prompt_version": "v14", "prompt_sha256": "dummy",
        "usage": {"promptTokenCount": 10, "candidatesTokenCount": 10, "totalTokenCount": 20},
        "elapsed_sec": 0.1, "at": "2026-11-09T00:00:00+09:00"
    }

    atomic_json(log_dir / "01-analyze-result.json", {"event": dummy_event, "result": mat_obj.model_dump()})
    atomic_json(log_dir / "02-generate-result.json", {"event": dummy_event, "result": draft_obj.model_dump()})
    atomic_json(log_dir / "revision-0.json", {"draft": draft_obj.model_dump(), "review": rev_obj.model_dump()})

    # Reject without network calls or modifying the legacy checkpoint files.
    before = {p.name: p.read_bytes() for p in log_dir.iterdir()}
    results = process_batch_queue(tmp_path, pipeline, limit=10)
    assert len(results) == 1
    assert results[0]['status'] == 'failed'
    assert results[0]['code'] == 'checkpoint_config_changed_use_new_destination'
    assert not (tmp_path / 'output' / f'{cid}.json').exists()
    assert {p.name: p.read_bytes() for p in log_dir.iterdir()} == before


def test_cli_mode_dispatch(tmp_path, monkeypatch):
    from script_agent import cli
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy2(ROOT / "config.toml", project / "config.toml")

    dispatched = []

    def mock_process_queue(root, pipeline, limit=100, mode="standard"):
        assert pipeline.config['pipeline']['mode'] == mode
        dispatched.append(mode)
        return []

    monkeypatch.setattr(cli, "process_queue", mock_process_queue)
    monkeypatch.setattr(cli, "credentials", lambda *a, **kw: {})

    # Test explicit --mode batch
    monkeypatch.setattr("sys.argv", ["script-agent", "--root", str(project), "run", "--mode", "batch"])
    ret = cli.main()
    assert ret == 0
    assert dispatched == ["batch"]

    # Test explicit --mode standard
    dispatched.clear()
    monkeypatch.setattr("sys.argv", ["script-agent", "--root", str(project), "run", "--mode", "standard"])
    ret = cli.main()
    assert ret == 0
    assert dispatched == ["standard"]

    dispatched.clear()
    monkeypatch.setattr('sys.stdin.isatty', lambda: False)
    monkeypatch.setattr('sys.argv', ['script-agent', '--root', str(project), 'run'])
    assert cli.main() == 0
    assert dispatched == ['batch']
