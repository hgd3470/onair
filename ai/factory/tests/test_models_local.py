"""단계별 모델 고르기와 local(OpenAI 호환 서버) 호출. 가짜 모델 서버를 띄워 확인한다. 실제 모델은 부르지 않는다."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from teum_factory import models


class FakeLLM(BaseHTTPRequestHandler):
    answers = []   # 요청마다 하나씩 꺼내 쓴다: 문자열(답), ("status", 코드, 본문)
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self._json(200, {"data": [{"id": "fake-model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append(body)
        ans = type(self).answers.pop(0)
        if isinstance(ans, tuple):
            return self._json(ans[1], ans[2])
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            for i in range(0, len(ans), 20):
                self._event({"model": "fake-model", "choices": [{"delta": {"content": ans[i:i + 20]}, "finish_reason": None}]})
            self._event({"model": "fake-model", "choices": [{"delta": {}, "finish_reason": "stop"}]})
            self._event({"model": "fake-model", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # 받는 쪽이 중간에 끊었다

    def _event(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def llm(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeLLM.answers, FakeLLM.seen = [], []
    monkeypatch.setenv("TEUM_LLM_URL", f"http://127.0.0.1:{server.server_port}/v1")
    for k in ("TEUM_LLM_MODEL", "TEUM_LLM_EXTRA", "TEUM_LLM_KEY", "TEUM_HOME"):
        monkeypatch.delenv(k, raising=False)
    models._MODEL.clear()
    yield FakeLLM
    server.shutdown()


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def test_one_line_per_stage(monkeypatch):
    for k in ("TEUM_GENERATOR", "TEUM_REVIEWER", "TEUM_REFINE"):
        monkeypatch.delenv(k, raising=False)
    s = models.slots()
    assert [(s[n].provider, s[n].chosen) for n in ("generator", "reviewer", "refine")] == [("gemini", False), ("openai", False), ("claude", False)], "비우면 저장소 기본값"
    monkeypatch.setenv("TEUM_GENERATOR", "local")
    monkeypatch.setenv("TEUM_REVIEWER", "gemini:gemini-x")
    monkeypatch.setenv("TEUM_REFINE", "claude:claude-haiku-5-5")
    s = models.slots()
    assert (s["generator"].provider, s["reviewer"].model, s["refine"].model) == ("local", "gemini-x", "claude-haiku-5-5")
    monkeypatch.setenv("TEUM_REFINE", "gemini")
    with pytest.raises(ValueError):
        models.slot("refine")


def test_local_answer_and_request_shape(llm):
    llm.answers = ['{"ok": true}']
    r = models.chat_json("시스템", "질문", SCHEMA, "t")
    assert json.loads(r["text"]) == {"ok": True} and r["finish"] == "stop" and r["model"] == "fake-model"
    body = llm.seen[0]
    assert body["model"] == "fake-model", "모델 이름을 안 적으면 서버의 첫 모델"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_blank_run_is_cut_and_retried(llm):
    """형식 강제 아래에서 빈 줄만 이어지는 답은 끝까지 기다리지 않고 끊어 다시 묻는다"""
    llm.answers = ['{"ok": ' + "\n  " * 2000, '{"ok": true}']
    r = models.chat_json("", "질문", SCHEMA, "t")
    assert json.loads(r["text"]) == {"ok": True} and len(llm.seen) == 2
    llm.answers = ['{"ok": ' + "\n  " * 2000] * 4
    r = models.chat_json("", "질문", SCHEMA, "t")
    assert r["finish"] == "blank_run", "계속 그러면 끝나지 않은 답으로 돌려준다(부르는 쪽이 미완성으로 처리)"


def test_long_input_shrinks_answer_room(llm):
    llm.answers = [("status", 400, {"error": {"message": "This model's maximum context length is 16384 tokens. However, you requested 17000 tokens (13000 in the messages, 4000 in the completion)."}}),
                   '{"ok": true}']
    r = models.chat_json("", "질문", SCHEMA, "t")
    assert r["finish"] == "stop" and llm.seen[1]["max_tokens"] == 16384 - 13000 - 64 and not r["shortened"]


def test_blocker_says_why_local_cannot_run(llm, monkeypatch):
    assert models.local_blocker() == ""
    monkeypatch.setenv("TEUM_LLM_MODEL", "other")
    assert "other 없음" in models.local_blocker()
    monkeypatch.setenv("TEUM_LLM_URL", "http://127.0.0.1:9/v1")
    assert "붙지 못함" in models.local_blocker()


def test_empty_key_slot_counts_as_missing(tmp_path, monkeypatch):
    from teum_factory.config import Config
    for n in ("GEMINI_API_KEY", "OPENAI_API_KEY", "gemini_api_key", "openai_api_key",
              "TEUM_GENERATOR", "TEUM_REVIEWER", "TEUM_SCRIPT_AGENT_CMD"):
        monkeypatch.delenv(n, raising=False)
    env = tmp_path / "script_agent.env"
    env.write_text("# filled by the key owner\nGEMINI_API_KEY=\nOPENAI_API_KEY=  \n")
    monkeypatch.setenv("TEUM_SCRIPT_AGENT_ENV", str(env))
    cfg = Config.load() if hasattr(Config, "load") else Config.from_env()
    assert cfg._api_keys() == set()
    env.write_text("GEMINI_API_KEY=abc\nOPENAI_API_KEY=\n")
    assert cfg._api_keys() == {"gemini_api_key"}


def test_script_agent_mode_override(tmp_path, monkeypatch):
    import dataclasses, tomllib
    from teum_factory.config import Config
    for n in ("TEUM_GENERATOR", "TEUM_REVIEWER", "TEUM_SCRIPT_AGENT_MODE"):
        monkeypatch.delenv(n, raising=False)
    repo = tmp_path / "repo"
    (repo / "ai/script_agent").mkdir(parents=True)
    (repo / "ai/script_agent/config.toml").write_text(
        '[models.generator]\nprovider = "gemini"\nmodel = "g"\n\n[models.reviewer]\nprovider = "openai"\nmodel = "o"\n\n'
        '[pipeline]\nmode = "batch"\ntimeout_sec = 300\n\n[batch]\npoll_interval_sec = 15\n', encoding="utf-8")
    cfg = dataclasses.replace(Config.load(), repo=repo)
    assert cfg.sa_config() is None  # 비우면 저장소 설정(batch)을 그대로 쓴다
    monkeypatch.setenv("TEUM_SCRIPT_AGENT_MODE", "standard")
    c = tomllib.loads(cfg.sa_config())
    assert c["pipeline"]["mode"] == "standard"
    assert c["models"]["generator"]["model"] == "g" and c["batch"]["poll_interval_sec"] == 15


def test_key_env_forwards_filled_keys_only(tmp_path, monkeypatch):
    from teum_factory.config import Config
    from teum_factory.steps import key_env
    env = tmp_path / "script_agent.env"
    env.write_text("# owner fills\ngemini_api_key=g1\nOPENAI_API_KEY=\nX-NCP-APIGW-API-KEY-ID='n1'\n", encoding="utf-8")
    monkeypatch.setenv("TEUM_SCRIPT_AGENT_ENV", str(env))
    got = key_env(Config.load())
    assert got == {"gemini_api_key": "g1", "GEMINI_API_KEY": "g1", "X-NCP-APIGW-API-KEY-ID": "n1"}
