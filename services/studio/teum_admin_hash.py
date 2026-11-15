"""teum_admin.py와 admin_users.py가 같이 쓰는 비밀번호 해시 (쿠키 키 없이 불러올 수 있게 따로 둔다)."""
import hashlib


def pw_hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 200_000).hex()
