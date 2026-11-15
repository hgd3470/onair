"""온에어 관리 대시보드 계정 만들기·비밀번호 바꾸기.

python3 admin_users.py init        → 팀원 계정을 만들고(이미 있으면 건너뜀) 새 비밀번호를 admin_data/passwords.txt(600)에 적는다
python3 admin_users.py reset <아이디> → 그 사람 비밀번호만 새로 만든다
python3 admin_users.py rename      → 예전 아이디를 역할 아이디(ops, script, review, app, viewer)로 바꾼다. 화면 이름이 같은 계정을 찾아 바꾸고, 변경 기록의 아이디도 같이 바꾼다. 사이트를 내린 뒤에 돌린다
python3 admin_users.py shared < 파일   → 팀 공용 비밀번호를 정한다(표준 입력 첫 줄). 정하면 개인 비밀번호는 안 쓰고 이름 + 공용 비밀번호로 들어온다
비밀번호는 화면에 찍지 않는다. passwords.txt는 관리자가 읽고 팀원에게 따로 전한다.
"""
import json
import os
import secrets
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from teum_admin_hash import pw_hash

_HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TEUM_ADMIN_DATA") or Path(os.environ.get("TEUM_HOME", _HERE.parents[1] / "var")) / "admin_data")
# 화면 이름은 역할명이다(실명을 두지 않는다). 로그인은 아이디나 이 이름으로 한다
# 역할: admin=전부, voice=목소리 배정, script=대본, refine=정제 판정, app=앱 적재, rights=원천 등급, rights_check=저작권 확인
TEAM = {
    "ops": {"name": "운영", "roles": ["admin", "voice", "rights"]},
    "script": {"name": "대본", "roles": ["script"]},
    "review": {"name": "검수", "roles": ["refine", "rights_check"]},
    "app": {"name": "앱", "roles": ["app", "voice"]},
    "viewer": {"name": "열람", "roles": []},
}


def save(users, fresh):
    DATA.mkdir(parents=True, exist_ok=True)
    f = DATA / "users.json"
    f.write_text(json.dumps(users, ensure_ascii=False, indent=1))
    os.chmod(f, 0o600)
    p = DATA / "passwords.txt"
    old = p.read_text() if p.exists() else ""
    p.write_text(old + "".join(f"{uid}\t{users[uid]['name']}\t{pw}\n" for uid, pw in fresh.items()))
    os.chmod(p, 0o600)
    print("계정:", ", ".join(f"{u}({users[u]['name']})" for u in users), "| 새 비밀번호", len(fresh), "개 → admin_data/passwords.txt")


def new(users, uid):
    pw = secrets.token_urlsafe(9)
    salt = secrets.token_hex(16)
    users[uid] = {**users.get(uid, TEAM.get(uid, {"name": uid, "roles": []})), "salt": salt, "hash": pw_hash(pw, salt)}
    return pw


def rename(users):
    """화면 이름이 TEAM과 같은데 아이디가 다른 계정을 역할 아이디로 바꾼다. 비밀번호는 그대로다. 바꾼 쌍 {예전: 새}를 돌려준다"""
    by_name = {v["name"]: uid for uid, v in TEAM.items()}
    pairs = {uid: by_name[u["name"]] for uid, u in users.items()
             if u.get("name") in by_name and uid != by_name[u["name"]] and by_name[u["name"]] not in users}
    if not pairs:
        return pairs
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for name in ("users.json", "admin.db", "passwords.txt"):
        if (DATA / name).exists():
            shutil.copy2(DATA / name, DATA / f"{name}.before_rename_{stamp}")
            os.chmod(DATA / f"{name}.before_rename_{stamp}", 0o600)
    f = DATA / "users.json"
    f.write_text(json.dumps({pairs.get(uid, uid): u for uid, u in users.items()}, ensure_ascii=False, indent=1))
    os.chmod(f, 0o600)
    p = DATA / "passwords.txt"
    if p.exists():
        lines = [line.split("\t") for line in p.read_text().splitlines()]
        p.write_text("".join("\t".join([pairs.get(c[0], c[0])] + c[1:]) + "\n" for c in lines))
        os.chmod(p, 0o600)
    if (DATA / "admin.db").exists():
        con = sqlite3.connect(DATA / "admin.db")
        with con:
            for old, new_id in pairs.items():
                for tbl in ("log", "ab", "synth"):
                    con.execute(f"update {tbl} set user=? where user=?", (new_id, old))
                con.execute("update items set updated_by=? where updated_by=?", (new_id, old))
            for tbl, rid, data in con.execute("select tbl, id, data from items").fetchall():
                row = json.loads(data)
                hit = [k for k in ("owner", "drop_by") if row.get(k) in pairs]
                for k in hit:
                    row[k] = pairs[row[k]]
                if hit:
                    con.execute("update items set data=? where tbl=? and id=?", (json.dumps(row, ensure_ascii=False), tbl, rid))
        con.close()
    return pairs


if __name__ == "__main__":
    f = DATA / "users.json"
    users = json.loads(f.read_text()) if f.exists() else {}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        save(users, {uid: new(users, uid) for uid in TEAM if uid not in users})
    elif cmd == "rename":
        done = rename(users)
        print("바꾼 계정", len(done), "개" + (" (바꾸기 전 파일은 admin_data/*.before_rename_* 에 남김)" if done else ". 바꿀 것이 없습니다"))
    elif cmd == "shared":
        pw = sys.stdin.readline().strip()
        if len(pw) < 6:
            sys.exit("비밀번호가 너무 짧습니다")
        salt = secrets.token_hex(16)
        f2 = DATA / "shared.json"
        f2.write_text(json.dumps({"salt": salt, "hash": pw_hash(pw, salt)}))
        os.chmod(f2, 0o600)
        print("공용 비밀번호를 정했습니다 → admin_data/shared.json")
    elif cmd == "reset" and len(sys.argv) > 2 and sys.argv[2] in users:
        save(users, {sys.argv[2]: new(users, sys.argv[2])})
    else:
        print(__doc__)
