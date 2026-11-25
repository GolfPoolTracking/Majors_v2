"""Signing and checking the secure "edit your entry" links."""
import hashlib
import hmac


def make_magic_link_hash(email, t_id, ts, secret):
    msg = f"{str(email).strip().lower()}|{t_id}|{int(ts)}".encode()
    return hmac.new(str(secret).encode(), msg, hashlib.sha256).hexdigest()

def secure_equals(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())

def make_follow_token(email, t_id, secret):
    """The token in a personal "follow my team" leaderboard link (…&me=<token>)."""
    msg = f"follow|{str(email).strip().lower()}|{t_id}".encode()
    return hmac.new(str(secret).encode(), msg, hashlib.sha256).hexdigest()[:20]
