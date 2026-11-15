"""검수기(ai/refine)를 그대로 부르되, TEUM_REFINE=local이면 Claude 호출만 OpenAI 호환 모델 서버로 돌린다.

  python -m teum_factory.run_refine run <입력> --out <출력> ...    (인자는 python -m teum_refine과 같다)

검수기는 Anthropic 클라이언트 하나로만 모델을 부른다(teum_refine/llm.py). 그 클라이언트 자리에 같은 모양의 대역을 끼운다.
판정 기록에는 실제로 답한 모델 이름이 남고 비용은 0으로 적힌다.
"""
import os
import sys
import types

from . import models


class _Reply:
    def __init__(self, r):
        self.content = [types.SimpleNamespace(type="text", text=r["text"])]
        self.stop_reason = "end_turn" if r["finish"] == "stop" else "max_tokens"
        self.stop_details = None
        u = r["usage"]
        self.usage = types.SimpleNamespace(model_dump=lambda: {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)})


class _Messages:
    def create(self, model=None, max_tokens=None, messages=None, output_config=None, system=None, **_):
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system)
        user = "\n".join(m["content"] if isinstance(m["content"], str) else "\n".join(b.get("text", "") for b in m["content"])
                         for m in messages or [])
        schema = ((output_config or {}).get("format") or {}).get("schema") or {"type": "object"}
        return _Reply(models.chat_json(system or "", user, schema, "refine", max_tokens, 300, model or ""))


class LocalClient:
    def __init__(self, **_):
        self.messages = _Messages()
        self.beta = types.SimpleNamespace(messages=self.messages)


def use_local(model):
    import anthropic
    from teum_refine import llm

    key = os.path.join(os.environ.get("TEUM_HOME", "."), "factory", "local-model.key")  # 검수기가 키 파일을 열어 보므로 빈 자리를 채운다. 키가 아니다
    os.makedirs(os.path.dirname(key), exist_ok=True)
    with open(key, "w") as f:
        f.write("local")
    os.environ["REFINE_ANTHROPIC_KEY_FILE"] = key
    llm.anthropic = types.SimpleNamespace(Anthropic=LocalClient, APIConnectionError=anthropic.APIConnectionError,
                                          APIStatusError=anthropic.APIStatusError, BadRequestError=anthropic.BadRequestError)
    original = llm.Client.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.model, self.rates, self.fallback, self.prompt_cache = model, (0, 0), False, False

    llm.Client.__init__ = init


def main():
    s = models.slot("refine")
    if s.provider == "local":
        use_local(models.local_model(s.model))
    from teum_refine.__main__ import main as refine_main
    return refine_main()


if __name__ == "__main__":
    sys.exit(main())
