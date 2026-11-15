"""단계마다 쓰는 모델을 한 곳에서 정한다. teum.env의 한 줄이 한 단계이고, 키가 오면 그 줄만 바꾼다.

  TEUM_GENERATOR=local | gemini[:모델] | openai[:모델]    ② 대본 작성 (분석, 초안, 수정)
  TEUM_REVIEWER=local | gemini[:모델] | openai[:모델]     ② 생성기 자체 검토
  TEUM_REFINE=local | claude[:모델]                       ③ 검수
  TEUM_LLM_URL, TEUM_LLM_MODEL                            local이 붙는 OpenAI 호환 서버 (vLLM, Ollama)

줄을 비우면 저장소 기본값(ai/script_agent/config.toml, 검수는 Claude)을 쓴다.
생성기와 검수기 코드는 고치지 않는다. run_script_agent.py와 run_refine.py가 두 프로그램을 감싸서 local일 때만 호출을 돌린다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000/v1"
DEFAULT_EXTRA = '{"chat_template_kwargs": {"enable_thinking": false}}'  # EXAONE 4.5는 생각 모드를 꺼야 답이 바로 JSON으로 온다
SLOTS = {  # 이름: (환경변수, 고를 수 있는 것, 저장소 기본값, 하는 일)
    "generator": ("TEUM_GENERATOR", ("local", "gemini", "openai"), "gemini", "② 대본 작성"),
    "reviewer": ("TEUM_REVIEWER", ("local", "gemini", "openai"), "openai", "② 자체 검토"),
    "refine": ("TEUM_REFINE", ("local", "claude"), "claude", "③ 검수"),
}
KEY_NAME = {"gemini": "gemini_api_key", "openai": "openai_api_key"}
CLAUDE_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5")  # 검수기가 받는 이름


@dataclass
class Slot:
    name: str
    provider: str   # local, gemini, openai, claude
    model: str      # ""이면 그 제공자의 기본 모델
    chosen: bool    # teum.env에서 고른 것이면 True, 저장소 기본값이면 False
    job: str


def slot(name):
    var, allowed, default, job = SLOTS[name]
    raw = os.environ.get(var, "").strip()
    if not raw:
        return Slot(name, default, "", False, job)
    provider, _, model = raw.partition(":")
    if provider not in allowed:
        raise ValueError(f"{var}={raw}: {', '.join(allowed)} 중 하나로 시작해야 합니다")
    if provider == "claude" and model and model not in CLAUDE_MODELS:
        raise ValueError(f"{var}={raw}: 검수기는 {', '.join(CLAUDE_MODELS)}만 받습니다")
    return Slot(name, provider, model, True, job)


def slots():
    return {name: slot(name) for name in SLOTS}


# ---- local: OpenAI 호환 서버 ----
class LocalLLMError(RuntimeError):
    def __init__(self, code, status=None):
        self.code, self.status = code, status
        super().__init__(f"local:{code}" + (f":HTTP_{status}" if status else ""))


def local_url():
    return os.environ.get("TEUM_LLM_URL", DEFAULT_URL).rstrip("/")


def _request(path, body=None, timeout=10):
    headers = {"Content-Type": "application/json"}
    if os.environ.get("TEUM_LLM_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["TEUM_LLM_KEY"]
    req = urllib.request.Request(local_url() + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def local_models(timeout=5):
    return [m["id"] for m in _request("/models", timeout=timeout).get("data", [])]


_MODEL = {}


def local_model(asked=""):
    """쓸 모델 이름. 줄에 적은 것 → TEUM_LLM_MODEL → 서버가 내놓는 첫 모델"""
    name = asked or os.environ.get("TEUM_LLM_MODEL", "").strip()
    if name:
        return name
    if "auto" not in _MODEL:
        found = local_models()
        if not found:
            raise LocalLLMError("no_model_served")
        _MODEL["auto"] = found[0]
    return _MODEL["auto"]


def local_blocker(asked=""):
    """local을 못 쓰는 이유. 쓸 수 있으면 빈 문자열"""
    try:
        found = local_models()
    except Exception:
        return f"모델 서버에 붙지 못함 ({local_url()})"
    want = asked or os.environ.get("TEUM_LLM_MODEL", "").strip()
    if want and want not in found:
        return f"모델 서버에 {want} 없음 (있는 것: {', '.join(found) or '없음'})"
    return "" if found else "모델 서버에 올라온 모델이 없음"


ONE_LINE_JSON = "답은 JSON 하나만 쓴다. 줄바꿈과 들여쓰기 없이 한 줄로 쓴다."
BLANK_RUN = 200  # 답 끝에 공백·줄바꿈만 이만큼 이어지면 끊는다


def _stream_chat(body, timeout):
    """답을 조각으로 받는다. 형식 강제(JSON 스키마) 아래에서 모델이 빈 줄만 끝없이 내는 경우가 있어,
    그때는 최대 길이까지 기다리지 않고 끊어 finish_reason을 blank_run으로 돌려준다."""
    headers = {"Content-Type": "application/json"}
    if os.environ.get("TEUM_LLM_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["TEUM_LLM_KEY"]
    req = urllib.request.Request(local_url() + "/chat/completions", headers=headers, method="POST",
                                 data=json.dumps({**body, "stream": True, "stream_options": {"include_usage": True}}).encode())
    parts, finish, usage, model, size, tail_blank = [], None, {}, body.get("model", ""), 0, 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[5:])
            usage = chunk.get("usage") or usage
            model = chunk.get("model") or model
            for c in chunk.get("choices") or []:
                piece = (c.get("delta") or {}).get("content") or ""
                finish = c.get("finish_reason") or finish
                if piece:
                    parts.append(piece)
                    size += len(piece)
                    tail_blank = tail_blank + len(piece) if not piece.strip() else len(piece) - len(piece.rstrip())
            if tail_blank >= BLANK_RUN:
                finish = "blank_run"
                break  # 연결을 닫으면 서버도 생성을 멈춘다
    if not usage:
        usage = {"completion_tokens": None}
    return {"choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}], "usage": usage, "model": model}


def _keep_trouble(name, body, text, finish):
    """답이 끝까지 안 나온 요청을 TEUM_HOME/factory/llm_trouble/에 남긴다(원인 확인용)"""
    home = os.environ.get("TEUM_HOME")
    if not home:
        return
    try:
        d = Path(home) / "factory" / "llm_trouble"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{time.strftime('%Y%m%d-%H%M%S')}_{re.sub(r'[^0-9A-Za-z_-]', '_', name)[:40]}.json").write_text(
            json.dumps({"finish": finish, "request": body, "answer_head": text[:1500], "answer_tail": text[-1500:]}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def chat_json(system, user, schema, name="result", max_tokens=None, timeout=300, model=""):
    """JSON 스키마에 맞춘 답 한 번. 돌려주는 것: text, usage, model, finish(stop | length), shortened"""
    model = local_model(model)
    cap = int(os.environ.get("TEUM_LLM_MAX_TOKENS", "4000"))
    limit = min(max_tokens or cap, cap)
    shortened, blank_runs = False, 0
    system = (system + "\n\n" if system else "") + ONE_LINE_JSON
    for _ in range(7):
        body = {"model": model, "max_tokens": limit, "temperature": float(os.environ.get("TEUM_LLM_TEMPERATURE", "0.3")),
                "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {"name": re.sub(r"\W", "_", name)[:60] or "result", "schema": schema}}}
        body.update(json.loads(os.environ.get("TEUM_LLM_EXTRA", DEFAULT_EXTRA) or "{}"))
        try:
            data = _stream_chat(body, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            ctx = re.search(r"maximum context length is (\d+)", detail)
            used = re.search(r"\((\d+) in the messages|has (\d+) input tokens|(\d+) prompt tokens", detail)
            if e.code == 400 and ctx and used:  # 입력과 답 길이의 합이 모델 창을 넘었다. 답 길이를 줄이고, 그래도 넘으면 입력 뒤를 줄인다
                ctx, used = int(ctx.group(1)), int(next(g for g in used.groups() if g))
                room = ctx - used - 64
                if room >= 1200:
                    limit = min(limit, room)
                else:
                    user = user[:int(len(user) * (ctx - 1500) * 0.9 / used)] + "\n(길이 제한으로 뒤를 줄임)"
                    limit, shortened = min(limit, 1400), True
                continue
            raise LocalLLMError("api_error", e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LocalLLMError("transport_or_timeout") from None
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") != "stop":
            _keep_trouble(name, body, text, choice.get("finish_reason"))
        if choice.get("finish_reason") == "blank_run" and blank_runs < 3:
            blank_runs += 1  # 같은 요청을 다시 보낸다. 표본이 달라 대부분 다음 번에 끝까지 나온다
            continue
        return {"text": text, "usage": data.get("usage") or {}, "model": data.get("model", model),
                "finish": choice.get("finish_reason"), "shortened": shortened}
    raise LocalLLMError("input_too_long")


def wait_until_ready(seconds=0):
    end = time.time() + seconds
    while True:
        why = local_blocker()
        if not why or time.time() >= end:
            return why
        time.sleep(5)
