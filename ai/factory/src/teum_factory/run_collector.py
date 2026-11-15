"""① 소재 수집기(ai/source_collector)를 부르고, 통과한 소재 md를 입력함으로 옮긴다.

수집기는 결과를 자기 폴더(ai/source_collector/output/<배치>/)에만 쓴다. 자동 제작 루프는 TEUM_HOME/script_agent/input/을
읽으므로 이 파일이 둘을 잇는다. 수집기 코드는 고치지 않는다. teum.env에는 이렇게 적는다.

    TEUM_COLLECT_CMD=python -m teum_factory.run_collector --categories C13 --count 3

뒤에 붙인 인자는 수집기의 run 명령으로 그대로 간다(--categories, --count, --model, --min-score, --mock 등).
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
LOG_NAME = "collection-log.md"  # 수집기가 배치마다 남기는 기록. 소재가 아니다


def copy_batch(batch_dir, inbox):
    """배치 폴더의 소재 md를 입력함에 복사한다. 같은 이름이 이미 있으면 건너뛴다. 복사한 이름을 돌려준다"""
    inbox.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in sorted(batch_dir.glob("*.md")) if batch_dir.is_dir() else []:
        if f.name == LOG_NAME or (inbox / f.name).exists():
            continue
        part = inbox / (f.name + ".part")  # 루프가 쓰다 만 파일을 집지 않게 다 쓴 뒤 이름을 바꾼다
        shutil.copyfile(f, part)
        part.replace(inbox / f.name)
        copied.append(f.name)
    return copied


def main(argv):
    collector = Path(os.environ.get("TEUM_SOURCE_COLLECTOR", REPO / "ai/source_collector/collector"))
    out_root = Path(os.environ.get("TEUM_SOURCE_COLLECTOR_OUT", REPO / "ai/source_collector/output"))
    home = Path(os.environ.get("TEUM_HOME", REPO / "var"))
    inbox = Path(os.environ.get("TEUM_INBOX", home / "script_agent" / "input"))
    if "--batch-id" in argv or "--mode" in argv or "--output-dir" in argv:
        sys.exit("--batch-id, --mode, --output-dir는 이 명령이 정합니다. 빼고 다시 부르세요")
    if not collector.exists():
        sys.exit(f"수집기가 없습니다: {collector}")
    batch = datetime.now(timezone(timedelta(hours=9))).strftime("%y%m%d-%H%M")  # 수집기의 기본 배치 이름과 같은 모양
    rc = subprocess.run([str(collector), "run", "--mode", "staging", "--batch-id", batch, *argv], cwd=collector.parent).returncode
    copied = copy_batch(out_root / batch, inbox)
    print(f"수집 배치 {batch}: 입력함으로 {len(copied)}편")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
