"""설정은 모두 환경변수로 받는다(infra/one-server/env.example). 키 값은 여기서 읽지 않고, 있는지만 본다."""
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from . import models

REPO = Path(__file__).resolve().parents[4]


def _flag(name, default="0"):
    return os.environ.get(name, default) == "1"


@dataclass
class Config:
    repo: Path
    home: Path            # 바뀌는 데이터가 놓이는 곳 (TEUM_HOME)
    work: Path            # home/factory: 상태, 실행 기록
    sa_root: Path         # home/script_agent: 생성기 작업 폴더 (input → processing → output)
    studio_url: str
    token: str
    enabled: bool         # TEUM_FACTORY=1 이어야 loop가 실제로 돈다
    interval: int
    max_generate: int     # 한 번 돌 때 새로 생성할 대본 수 (API 비용 상한)
    max_refine: int       # 한 번 돌 때 검수할 대본 수 (API 비용 상한)
    max_rounds: int       # 검수 → 수정 왕복 상한. 넘으면 사람에게 넘긴다
    refine_scope: str     # all: 검수 대기 전부 / own: 자동 생성한 대본만
    auto_publish: str     # 0: 사람이 듣고 게시 / own: 자동 생성한 대본은 받아쓰기 대조를 통과하면 게시 / all: 음성 완료 전부
    max_publish: int      # 한 번 돌 때 게시할 편수
    publish_hold_topics: set  # 자동 게시하지 않고 사람 승인으로 남기는 분류 코드 (TEUM_AUTO_PUBLISH_HOLD_TOPICS)
    refine_warn_usd: float    # 하루 검수 비용이 이 값을 넘으면 기록에 경고를 남긴다 (TEUM_REFINE_DAILY_WARN_USD)
    rules_only: bool      # 키 없이 규칙 검사만 (시험용). 켜면 사유에 표시된다
    sa_cmd: list
    refine_cmd: list
    collect_cmd: list
    collect_interval: int  # 수집 명령을 부르는 간격(초). 루프 한 바퀴(TEUM_FACTORY_INTERVAL)보다 길게 둔다
    kanana_url: str

    @classmethod
    def load(cls):
        home = Path(os.environ.get("TEUM_HOME", REPO / "var")).resolve()
        env_file = home / "teum.env"  # 손으로 돌릴 때도 teum_up과 같은 설정을 쓴다. 이미 환경에 있는 값이 이긴다
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))
        py = os.environ.get("TEUM_PYTHON", sys.executable)
        return cls(
            repo=REPO, home=home, work=home / "factory", sa_root=home / "script_agent",
            studio_url=os.environ.get("TEUM_STUDIO_URL", "http://127.0.0.1:" + os.environ.get("TEUM_WEB_PORT", "8780")).rstrip("/"),
            token=os.environ.get("TEUM_SERVICE_TOKEN", ""),
            enabled=_flag("TEUM_FACTORY"),
            interval=int(os.environ.get("TEUM_FACTORY_INTERVAL", "600")),
            max_generate=int(os.environ.get("TEUM_FACTORY_MAX_GENERATE", "5")),
            max_refine=int(os.environ.get("TEUM_FACTORY_MAX_REFINE", "20")),
            max_rounds=int(os.environ.get("TEUM_FACTORY_MAX_ROUNDS", "2")),
            refine_scope=os.environ.get("TEUM_FACTORY_REFINE_SCOPE", "all"),
            auto_publish={"1": "own", "": "0"}.get(os.environ.get("TEUM_AUTO_PUBLISH", "0"), os.environ.get("TEUM_AUTO_PUBLISH", "0")),
            max_publish=int(os.environ.get("TEUM_FACTORY_MAX_PUBLISH", "20")),
            publish_hold_topics={c.strip().upper() for c in os.environ.get("TEUM_AUTO_PUBLISH_HOLD_TOPICS", "C16,C08").split(",") if c.strip()},
            refine_warn_usd=float(os.environ.get("TEUM_REFINE_DAILY_WARN_USD", "5")),
            rules_only=_flag("TEUM_REFINE_RULES_ONLY"),
            sa_cmd=shlex.split(os.environ.get("TEUM_SCRIPT_AGENT_CMD", "")) or [py, "-m", "teum_factory.run_script_agent"],
            refine_cmd=shlex.split(os.environ.get("TEUM_REFINE_CMD", "")) or [py, "-m", "teum_factory.run_refine"],
            collect_cmd=shlex.split(os.environ.get("TEUM_COLLECT_CMD", "")),
            collect_interval=int(os.environ.get("TEUM_COLLECT_INTERVAL", "86400")),
            kanana_url=os.environ.get("TEUM_KANANA_URL", ""),
        )

    # ---- 단계별 모델 (models.py). 키는 있는지만 보고 값은 읽지 않는다 ----
    def slots(self):
        return models.slots()

    def _api_keys(self):
        env = self.sa_env()
        names = set()
        if env.exists():
            for line in env.read_text().splitlines():
                name, sep, value = line.partition("=")
                # 값이 빈 줄(키 자리만 만들어 둔 것)은 없는 것으로 본다
                if sep and not name.lstrip().startswith("#") and value.strip().strip("'\""):
                    names.add(name.strip().lower())
        return {n for n in models.KEY_NAME.values() if os.environ.get(n) or os.environ.get(n.upper()) or n in names}

    def generator_blocker(self):
        """② 생성을 못 하는 이유. 할 수 있으면 빈 문자열"""
        if os.environ.get("TEUM_SCRIPT_AGENT_CMD"):
            return ""  # 시험용 대역 생성기
        have, why = self._api_keys(), []
        for s in (models.slot("generator"), models.slot("reviewer")):
            if s.provider == "local":
                reason = models.local_blocker(s.model)
                if reason and reason not in why:
                    why.append(reason)
            elif models.KEY_NAME[s.provider] not in have:
                why.append(f"{s.job}: {models.KEY_NAME[s.provider].upper()} 없음")
        return "; ".join(why)

    def generator_ready(self):
        return not self.generator_blocker()

    def sa_env(self):
        return Path(os.environ.get("TEUM_SCRIPT_AGENT_ENV", self.home / "script_agent.env"))

    def sa_config(self):
        """생성기 config.toml. 저장소 것에 teum.env에서 고른 모델과 실행 방식만 덮어쓴다. 바꿀 것이 없으면 None(저장소 파일을 그대로 쓴다)

        실행 방식은 TEUM_SCRIPT_AGENT_MODE(standard | batch)로 바꿀 수 있고, 비우면 생성기 저장소 설정(지금은 batch)을 따른다.
        local 모델을 고른 단계가 있으면 배치가 없으므로 standard로 부른다"""
        chosen = [s for s in (models.slot("generator"), models.slot("reviewer")) if s.chosen]
        import tomllib
        c = tomllib.loads((self.repo / "ai/script_agent/config.toml").read_text(encoding="utf-8"))
        mode = os.environ.get("TEUM_SCRIPT_AGENT_MODE", "").strip()
        if any(s.provider == "local" for s in chosen):
            mode = "standard"
        repo_mode = c.get("pipeline", {}).get("mode")
        from . import feedback
        team = feedback.active_prompt(self)  # 제작진 규칙을 붙인 프롬프트 판(스튜디오 피드백 → 옵티마이저)
        if not chosen and (not mode or mode == repo_mode) and not team:
            return None
        if team:
            c["pipeline"]["prompt_version"] = team
        if mode and repo_mode is not None:
            c["pipeline"]["mode"] = mode
        for s in chosen:
            m = c["models"][s.name]
            if s.provider == "local":
                m.update(provider="local", model=models.local_model(s.model), effort="none",
                         max_output_tokens=int(os.environ.get("TEUM_LLM_MAX_TOKENS", "4000")))
            else:
                if m["provider"] != s.provider and not s.model:
                    raise ValueError(f"{models.SLOTS[s.name][0]}={s.provider}: 모델 이름을 같이 적어야 합니다 (예: {s.provider}:모델)")
                m.update(provider=s.provider, model=s.model or m["model"])
        if any(s.provider == "local" for s in chosen):
            c["pipeline"]["max_source_chars"] = self.local_source_chars()
        lines = ["# 자동 생성: 저장소 ai/script_agent/config.toml + teum.env의 TEUM_GENERATOR, TEUM_REVIEWER, TEUM_SCRIPT_AGENT_MODE + 제작진 규칙 판(feedback). 손으로 고치지 않는다"]
        for table in ("models.generator", "models.reviewer", "pipeline", "batch", "experiments"):
            d = c
            for part in table.split("."):
                d = d.get(part, {})
            if d:
                lines += ["", f"[{table}]"] + [f"{k} = {str(v).lower() if isinstance(v, bool) else json_str(v)}" for k, v in d.items()]
        return "\n".join(lines) + "\n"

    def local_source_chars(self):
        return int(os.environ.get("TEUM_LLM_MAX_SOURCE_CHARS", "6000"))  # 모델 창(EXAONE 4.5는 16,384토큰)에 프롬프트와 답이 같이 들어가야 한다

    def refine_key_file(self):
        return Path(os.environ.get("REFINE_ANTHROPIC_KEY_FILE", "~/.config/secrets/ClaudePlatform.key")).expanduser()

    def refine_blocker(self):
        if self.rules_only:
            return ""
        s = models.slot("refine")
        if s.provider == "local":
            return models.local_blocker(s.model)
        return "" if self.refine_key_file().exists() else f"검수 키 없음 ({self.refine_key_file()})"

    def refine_ready(self):
        return not self.refine_blocker()

    def refine_args(self):
        args = []
        if self.rules_only:
            args.append("--skip-llm")
        elif models.slot("refine").provider == "claude" and models.slot("refine").model:
            args += ["--model", models.slot("refine").model]
        args += ["--kanana-url", self.kanana_url] if self.kanana_url else ["--skip-safety"]
        try:
            import sentence_transformers  # noqa: F401
            args += ["--dedup-index", str(self.work / "dedup-index.jsonl")]
        except Exception:
            args.append("--skip-dedup")
        return args

    def refine_skipped(self, publish_only=False):
        """이번 설정으로 검수기가 건너뛰는 검사 이름. publish_only면 자동 게시를 막는 것(LLM 검사, 안전 검사)만"""
        checks = (("--skip-llm", "LLM 검사"), ("--skip-safety", "안전 검사"), ("--skip-dedup", "중복 검사"))
        args = self.refine_args()
        return [name for flag, name in checks[:2 if publish_only else 3] if flag in args]

    def refine_label(self):
        """판정 사유 앞에 붙는 표시. 사람 판정과 섞이지 않게 하고, 빠진 검사를 숨기지 않는다"""
        skipped = self.refine_skipped()
        s = models.slot("refine")
        who = ""
        if not self.rules_only and s.provider == "local":
            try:
                who = " · " + models.local_model(s.model).split("/")[-1]
            except Exception:
                who = " · local"
        return "[자동 검수" + who + (" · " + "·".join(skipped) + " 제외" if skipped else "") + "]"


def json_str(v):
    import json
    return json.dumps(v, ensure_ascii=False)
