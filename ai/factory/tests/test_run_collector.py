"""수집기 연결(run_collector): 가짜 수집기가 배치 폴더에 쓴 소재만 입력함으로 가는지 본다. 실제 수집기와 모델은 부르지 않는다."""
import os
import stat
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

FAKE = """#!/usr/bin/env bash
# 인자: run --mode staging --batch-id <배치> ...
out="$FAKE_OUT/$5"; mkdir -p "$out"
echo "# 소재 1" > "$out/$5-C13-01.md"
echo "# 소재 2" > "$out/$5-C13-02.md"
echo "기록" > "$out/collection-log.md"
echo "$@" > "$FAKE_OUT/args.txt"
"""


def run(tmp_path, *args):
    fake = tmp_path / "collector"
    fake.write_text(FAKE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PYTHONPATH": str(SRC), "TEUM_HOME": str(tmp_path / "home"), "FAKE_OUT": str(tmp_path / "out"),
           "TEUM_SOURCE_COLLECTOR": str(fake), "TEUM_SOURCE_COLLECTOR_OUT": str(tmp_path / "out")}
    env.pop("TEUM_INBOX", None)
    return subprocess.run([sys.executable, "-m", "teum_factory.run_collector", *args], env=env, capture_output=True, text=True)


def test_accepted_sources_reach_inbox(tmp_path):
    r = run(tmp_path, "--categories", "C13", "--count", "2")
    assert r.returncode == 0, r.stderr
    inbox = tmp_path / "home" / "script_agent" / "input"
    names = sorted(p.name for p in inbox.iterdir())
    assert len(names) == 2 and all(n.endswith(".md") and "C13" in n for n in names)   # 기록 파일은 오지 않는다
    args = (tmp_path / "out" / "args.txt").read_text()
    assert "--mode staging" in args and "--categories C13 --count 2" in args
    assert "입력함으로 2편" in r.stdout
    assert "입력함으로 0편" in run(tmp_path, "--count", "2").stdout                      # 같은 배치를 다시 불러도 두 번 넣지 않는다


def test_reserved_options_are_refused(tmp_path):
    r = run(tmp_path, "--output-dir", "x")
    assert r.returncode != 0 and "--output-dir" in r.stderr
