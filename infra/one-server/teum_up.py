#!/usr/bin/env python3
"""온에어를 서버 한 대에서 명령 하나로 띄운다. 코드는 이 저장소, 데이터는 TEUM_HOME 한 곳.

  python infra/one-server/teum_up.py init         TEUM_HOME 폴더와 teum.env(비밀번호·토큰 자동 생성)를 만든다
  python infra/one-server/teum_up.py up           API · 스튜디오 웹 · 자동 제작 루프를 띄우고, 죽으면 다시 띄운다
  python infra/one-server/teum_up.py status       세 프로세스가 살아 있는지
  python infra/one-server/teum_up.py down         세 프로세스를 내리고 다 내려갈 때까지 기다린다
  python infra/one-server/teum_up.py restart-api  API만 다시 띄운다 (게시 뒤 스튜디오가 부른다)

프로세스 셋
  api      server/teum            차량 앱이 붙는 API (추천·편성·검색·청취 기록)
  web      services/studio        제작 관리 사이트(/admin), 청취 웹앱(/app), /api/v1은 api로 넘긴다
  factory  ai/factory             소재 → 대본 생성 → 검수 → 수정 왕복을 사람 없이 돌린다 (TEUM_FACTORY=1일 때)
"""
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path(os.environ.get("TEUM_HOME", REPO / "var")).resolve()
# conda·venv 환경의 ffmpeg를 찾도록 이 파이썬이 있는 폴더를 PATH 앞에 둔다(절대 경로로 실행해도 같게)
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
RUN, LOGS = HOME / "run", HOME / "logs"
DIRS = ("admin_data", "scripts_live", "work_pipeline", "data_root", "audio_live", "voice_img",
        "script_agent/input", "factory", "run", "logs")
ENV_TEMPLATE = """# 온에어 한 서버 실행 설정. 값은 이 파일에만 둔다(권한 600). 설명은 infra/one-server/env.example
TEUM_PASS={password}
TEUM_COOKIE_KEY={cookie}
TEUM_SERVICE_TOKEN={token}
TEUM_FACTORY=0

# 단계별 모델. 키가 없는 단계는 돌지 않고 preflight에 이유가 나온다. 키는 infra/one-server/README.md "모델과 키"
TEUM_GENERATOR=gemini
TEUM_REVIEWER=openai
TEUM_REFINE=claude
# 시험용 로컬 모델(OpenAI 호환 서버)로 돌릴 때만 위 줄을 local로 바꾼다. EXAONE은 출력물도 비상업 라이선스라 서비스 대본에 쓰지 않는다
TEUM_LLM_URL=http://127.0.0.1:8000/v1
TEUM_LLM_MODEL=

# 음성 합성(Typecast). 자동 합성은 꺼 두고, 켤 때 하루 한도를 같이 본다
TEUM_AUTO_VOICE=0
TEUM_DAILY_CREDITS=5000
"""


def load_env():
    """TEUM_HOME/teum.env를 읽는다. 이미 환경에 있는 값이 이긴다"""
    env = dict(os.environ)
    f = HOME / "teum.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))
    env["TEUM_HOME"] = str(HOME)
    return env


def init():
    for d in DIRS:
        (HOME / d).mkdir(parents=True, exist_ok=True)
    f = HOME / "teum.env"
    if not f.exists():
        f.write_text(ENV_TEMPLATE.format(password=secrets.token_urlsafe(9), cookie=secrets.token_hex(32),
                                         token=secrets.token_hex(32)), encoding="utf-8")
        print(f"만듦: {f} (접속 비밀번호 TEUM_PASS는 이 파일에서 확인)")
    elif "TEUM_SERVICE_TOKEN=" not in f.read_text(encoding="utf-8"):
        with f.open("a", encoding="utf-8") as w:
            w.write(f"\nTEUM_SERVICE_TOKEN={secrets.token_hex(32)}\n")
        print(f"추가: {f} 에 TEUM_SERVICE_TOKEN")
    f.chmod(0o600)
    if not (HOME / "admin_data" / "users.json").exists():  # 팀원 계정. 비밀번호는 admin_data/passwords.txt(600)에만 적힌다
        subprocess.run([sys.executable, str(REPO / "services/studio/admin_users.py"), "init"],
                       env={**os.environ, "TEUM_HOME": str(HOME)}, check=True)
    data_root = Path(load_env().get("TEUM_DATA_ROOT", HOME / "data_root"))
    if not (data_root / "outputs" / "catalog.sqlite").exists():  # 첫 게시 전에도 api가 뜨도록 빈 카탈로그를 만든다(팀 게시 절차 그대로)
        r = subprocess.run([sys.executable, str(REPO / "services/studio/pipeline_ops.py"), "publish"],
                           env={**os.environ, "TEUM_HOME": str(HOME), "TEUM_DATA_ROOT": str(data_root)}, capture_output=True, text=True)
        print("만듦: 빈 카탈로그" if '"ok": true' in r.stdout else f"빈 카탈로그를 만들지 못함: {(r.stdout or r.stderr).strip()[-300:]}")
    print(f"TEUM_HOME = {HOME}")


def children(env):
    py = env.get("TEUM_PYTHON", sys.executable)
    api_port, web_port = env.get("TEUM_API_PORT", "8790"), env.get("TEUM_WEB_PORT", "8780")
    base = {**env, "TEUM_PYTHON": py, "PYTHONUNBUFFERED": "1"}
    base.setdefault("TEUM_DATA_ROOT", str(HOME / "data_root"))
    base.setdefault("TEUM_EVENTS_DB", str(HOME / "data_root" / "events.sqlite"))
    base.setdefault("TEUM_DENSE", "0")
    base.setdefault("TEUM_API_UPSTREAM", f"http://127.0.0.1:{api_port}")
    base.setdefault("TEUM_STUDIO_URL", f"http://127.0.0.1:{web_port}")
    base.setdefault("TEUM_API_RESTART", f"{py} {Path(__file__).resolve()} restart-api")
    path = os.pathsep.join(str(REPO / p) for p in ("ai/factory/src", "ai/script_agent/src", "ai/refine/src"))
    return {
        "api": dict(cmd=[py, "-m", "uvicorn", "teum.main:app", "--host", "127.0.0.1", "--port", api_port],
                    cwd=REPO / "server", env=base, port=api_port),
        "web": dict(cmd=[py, "-m", "uvicorn", "teum_gate:app", "--host", env.get("TEUM_WEB_HOST", "127.0.0.1"),
                         "--port", web_port, "--proxy-headers"],
                    cwd=REPO / "services/studio", env=base, port=web_port),
        "factory": dict(cmd=[py, "-m", "teum_factory", "loop"], cwd=REPO,
                        env={**base, "PYTHONPATH": path + os.pathsep + base.get("PYTHONPATH", "")}, port=None),
        # 롱폼 작업자: 스튜디오 '롱폼' 탭의 다시 만들기 요청($TEUM_HOME/longform/requests)을 처리한다
        "longform": dict(cmd=[py, str(REPO / "ai/longform/pipeline.py"), "watch"], cwd=REPO, env=base, port=None),
    }


def alive(name):
    """pid 파일의 프로세스가 살아 있고 우리가 띄운 것이면 pid"""
    try:
        pid = int((RUN / f"{name}.pid").read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except (OSError, ValueError):
        return None
    marks = {"api": "teum.main:app", "web": "teum_gate:app", "factory": "teum_factory", "longform": "pipeline.py watch", "teum_up": "teum_up.py"}
    return pid if marks[name] in cmdline else None


def up():
    init()
    env = load_env()
    for k in ("TEUM_PASS", "TEUM_COOKIE_KEY"):
        if not env.get(k):
            sys.exit(f"{HOME / 'teum.env'} 에 {k} 가 없습니다")
    if alive("teum_up"):
        sys.exit("이미 떠 있습니다 (status로 확인)")
    (RUN / "teum_up.pid").write_text(str(os.getpid()))
    specs, procs, started, stopping = children(env), {}, {}, []

    def start(name):
        s = specs[name]
        log = open(LOGS / f"{name}.log", "ab")
        procs[name] = subprocess.Popen(s["cmd"], cwd=s["cwd"], env=s["env"], stdout=log, stderr=subprocess.STDOUT)
        started[name] = time.time()
        (RUN / f"{name}.pid").write_text(str(procs[name].pid))
        print(f"[{time.strftime('%H:%M:%S')}] {name} 시작 (pid {procs[name].pid})", flush=True)

    def stop(*_):
        stopping.append(1)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for name in specs:
        start(name)
    retry_at = {}
    while not stopping:
        time.sleep(1)
        for name, p in procs.items():
            if p.poll() is None:
                continue
            if name not in retry_at:  # 오래 돌다 내려간 것은 바로, 뜨자마자 죽는 것은 10초 뒤에 다시 띄운다
                quick = time.time() - started[name] < 10
                retry_at[name] = time.time() + (10 if quick else 0.5)
                print(f"[{time.strftime('%H:%M:%S')}] {name} 종료 (코드 {p.returncode}) → 다시 띄움", flush=True)
            elif time.time() >= retry_at[name]:
                del retry_at[name]
                start(name)
    for p in procs.values():
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + 10
    for p in procs.values():
        try:
            p.wait(max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
    for name in (*procs, "teum_up"):
        (RUN / f"{name}.pid").unlink(missing_ok=True)


def status():
    env = load_env()
    specs = children(env)
    print(f"TEUM_HOME = {HOME}")
    print(f"supervisor: {'pid ' + str(alive('teum_up')) if alive('teum_up') else '꺼짐'}")
    for name, s in specs.items():
        pid = alive(name)
        line = f"{name:8s} {'pid ' + str(pid) if pid else '꺼짐'}"
        if s["port"]:
            with socket.socket() as sock:
                sock.settimeout(1)
                line += f" · 포트 {s['port']} {'열림' if sock.connect_ex(('127.0.0.1', int(s['port']))) == 0 else '닫힘'}"
        if name == "factory":
            line += f" · 자동 실행 {'켜짐' if env.get('TEUM_FACTORY') == '1' else '꺼짐 (TEUM_FACTORY=1로 켠다)'}"
        print(line)


def down():
    pid = alive("teum_up")
    if not pid:
        print("떠 있지 않습니다")
        return
    os.kill(pid, signal.SIGTERM)  # supervisor가 세 프로세스를 내리고 끝난다
    for _ in range(300):
        if not Path(f"/proc/{pid}").exists():
            print(f"내렸습니다 (pid {pid})")
            return
        time.sleep(0.1)
    sys.exit(f"30초가 지나도 내려가지 않았습니다 (pid {pid})")


def restart_api():
    pid = alive("api")
    if not pid:
        sys.exit("api가 떠 있지 않습니다")
    os.kill(pid, signal.SIGTERM)  # supervisor가 바로 다시 띄운다
    print(f"api 다시 띄움 (이전 pid {pid})")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    {"init": init, "up": up, "status": status, "down": down, "restart-api": restart_api}.get(cmd, lambda: sys.exit(__doc__))()
