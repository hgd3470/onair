"""생성기(ai/script_agent)를 그대로 부르되, 설정에서 provider가 local인 역할만 OpenAI 호환 모델 서버로 돌린다.

  python -m teum_factory.run_script_agent --root R [--env-file F] run --limit N    (인자는 script-agent와 같다)

Gemini·OpenAI로 설정한 역할은 생성기의 원래 코드가 그대로 처리한다.
"""
import json
import sys
import time

from script_agent import cli, pipeline, preflight, providers

from . import models


class Routed(providers.APIProvider):
    def complete(self, system, payload, schema):
        s = self.settings
        if s["provider"] != "local":
            return super().complete(system, payload, schema)
        start, usage = time.monotonic(), {}
        for attempt in range(3):  # 스키마 밖의 제약(길이, 범위)에 걸린 답은 다시 받는다. 로컬 모델이라 비용이 들지 않는다
            try:
                r = models.chat_json(system, json.dumps(payload, ensure_ascii=False), schema.model_json_schema(), schema.__name__,
                                     s.get("max_output_tokens"), self.timeout, s.get("model", ""))
            except models.LocalLLMError as e:
                raise providers.ProviderError("local", s.get("model", ""), e.code, e.status) from None
            usage = r["usage"]
            if r["finish"] != "stop":
                if attempt < 2:
                    continue
                raise providers.ProviderError("local", r["model"], "incomplete_or_blocked", diagnostics={"finish_reason": "MAX_TOKENS"})
            try:
                parsed = schema.model_validate_json(r["text"])
            except Exception:
                if attempt < 2:
                    continue
                raise providers.ProviderError("local", r["model"], "invalid_structured_output") from None
            return providers.Completion(parsed, usage, round(time.monotonic() - start, 3), r["model"])


def main():
    pipeline.APIProvider = preflight.APIProvider = Routed
    return cli.main()


if __name__ == "__main__":
    sys.exit(main())
