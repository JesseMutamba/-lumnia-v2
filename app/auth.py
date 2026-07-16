"""Shared-password access gate.

One password (``LUMNIA_PASSWORD``) protects the whole app behind a login page.
When the variable is unset — local dev and the test suite — auth is disabled
and every request passes through, so nothing here changes local behaviour.

The session cookie is a signed, timestamped token (HMAC-SHA256 over the issue
time, keyed by the password). No accounts, no database — right for one
consultant and a handful of clients.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

COOKIE = "lumnia_session"
MAX_AGE = 30 * 24 * 3600          # 30 days
PUBLIC_PATHS = {"/health", "/login"}


def password() -> str | None:
    return os.environ.get("LUMNIA_PASSWORD") or None


def _secret() -> bytes:
    # Key the token with the password itself: changing the password instantly
    # invalidates every outstanding session.
    return hashlib.sha256((password() or "").encode()).digest()


def _sign(msg: str) -> str:
    return hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    issued = str(int(time.time()))
    return f"{issued}.{_sign(issued)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    issued, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(issued)):
        return False
    try:
        return (time.time() - int(issued)) < MAX_AGE
    except ValueError:
        return False


def check_password(candidate: str) -> bool:
    pw = password()
    return bool(pw) and hmac.compare_digest(candidate, pw)


# --- client identities (the hub) --------------------------------------------
# Analyst-issued signed login links instead of hosted magic-link auth: the
# link IS the credential (like a portal token), HMAC-signed over a DB-minted
# secret (storage.app_secret()) so it survives analyst password rotation.
# Exchanging a link sets a separate session cookie; analyst and client
# sessions never share a token space thanks to the kind prefix in the MAC.

CLIENT_COOKIE = "lumnia_client"
LINK_MAX_AGE = 180 * 24 * 3600       # a client link lives half a year
CLIENT_SESSION_MAX_AGE = 30 * 24 * 3600


def _sign_scoped(secret_hex: str, kind: str, msg: str) -> str:
    return hmac.new(bytes.fromhex(secret_hex), f"{kind}.{msg}".encode(),
                    hashlib.sha256).hexdigest()


def make_client_token(kind: str, user_id: str, secret_hex: str) -> str:
    """kind is 'link' or 'session'; tokens of one kind never verify as the
    other."""
    issued = str(int(time.time()))
    msg = f"{user_id}.{issued}"
    return f"{msg}.{_sign_scoped(secret_hex, kind, msg)}"


def read_client_token(token: str | None, kind: str, secret_hex: str,
                      max_age: int) -> str | None:
    """The user_id inside a valid, unexpired token — else None."""
    if not token or token.count(".") != 2:
        return None
    user_id, issued, sig = token.split(".")
    if not hmac.compare_digest(
            sig, _sign_scoped(secret_hex, kind, f"{user_id}.{issued}")):
        return None
    try:
        return user_id if (time.time() - int(issued)) < max_age else None
    except ValueError:
        return None


def make_expiring_token(kind: str, subject: str, secret_hex: str,
                        ttl: int) -> str:
    """A token that carries its own absolute expiry — the shape signed file
    URLs use. The expiry is inside the MAC, so it can't be extended."""
    expires = str(int(time.time()) + ttl)
    msg = f"{subject}.{expires}"
    return f"{msg}.{_sign_scoped(secret_hex, kind, msg)}"


def read_expiring_token(token: str | None, kind: str,
                        secret_hex: str) -> str | None:
    """The subject inside a valid, unexpired token — else None."""
    if not token or token.count(".") != 2:
        return None
    subject, expires, sig = token.split(".")
    if not hmac.compare_digest(
            sig, _sign_scoped(secret_hex, kind, f"{subject}.{expires}")):
        return None
    try:
        return subject if time.time() < int(expires) else None
    except ValueError:
        return None


LOGIN_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumnia</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='7' fill='%23c9992a'/%3E%3C/svg%3E">
<style>
  body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
    background:#f4f0e8;color:#201d17;
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .box{text-align:center;width:300px}
  svg.sun{margin-bottom:16px}
  h1{font-family:"Didot","Bodoni MT",Georgia,serif;font-size:30px;font-weight:500;
    letter-spacing:.14em;margin:0 0 4px;text-indent:.14em}
  .tag{font-family:"Didot",Georgia,serif;font-style:italic;color:#a8821f;
    font-size:14px;margin-bottom:26px}
  input{width:100%;padding:11px 14px;border:1px solid #e4dcc9;border-radius:8px;
    background:#fbf9f3;font-size:14px;box-sizing:border-box;margin-bottom:10px;outline:none}
  input:focus{border-color:#c9992a}
  button{width:100%;padding:11px;border:none;border-radius:8px;background:#a8821f;
    color:#fff;font-family:ui-monospace,Menlo,monospace;font-size:11px;
    letter-spacing:.12em;text-transform:uppercase;cursor:pointer}
  button:hover{background:#c9992a}
  .err{color:#d03b3b;font-size:12.5px;min-height:18px;margin-bottom:8px}
</style></head><body>
  <form class="box" method="post" action="/login">
    <svg class="sun" width="60" height="60" viewBox="-50 -50 100 100" id="s"></svg>
    <h1>LUMNIA</h1><div class="tag">Light where there was none.</div>
    <div class="err">__ERROR__</div>
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Enter</button>
    <p style="margin-top:18px;font-size:12px"><a href="/portal/signin"
      style="color:#a8821f;text-decoration:none">Espace client · Client sign-in →</a></p>
  </form>
  <script>
    let d=`<circle cx="0" cy="0" r="7.5" fill="#c9992a"/>`;
    for(let r=1;r<=7;r++){let R=9+r*4.6,n=10+r*6,rr=Math.max(.5,2.1-r*.18),o=Math.max(.3,1-r*.1);
      for(let k=0;k<n;k++){let a=k/n*6.283+r*.35;
        d+=`<circle cx="${(R*Math.cos(a)).toFixed(1)}" cy="${(R*Math.sin(a)).toFixed(1)}" r="${rr}" fill="#c9992a" opacity="${o.toFixed(2)}"/>`}}
    document.getElementById("s").innerHTML=d;
  </script>
</body></html>"""


# The PUBLIC client sign-in page (/portal/signin): email in, link by email
# out. FR leads — the anchor clientele works in French; EN rides below.
# __MSG__ is the one server-driven slot; the answer is deliberately the
# same whether the address is known, unknown, or throttled.
PORTAL_SIGNIN_HTML = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lumnia — Espace client</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='7' fill='%23c9992a'/%3E%3C/svg%3E">
<style>
  body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
    background:#f4f0e8;color:#201d17;
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
  .box{text-align:center;width:340px}
  svg.sun{margin-bottom:16px}
  h1{font-family:"Didot","Bodoni MT",Georgia,serif;font-size:30px;font-weight:500;
    letter-spacing:.14em;margin:0 0 4px;text-indent:.14em}
  .tag{font-family:"Didot",Georgia,serif;font-style:italic;color:#a8821f;
    font-size:14px;margin-bottom:22px}
  p.how{font-size:13px;color:#5c564a;margin:0 0 6px}
  p.how-en{font-size:11.5px;color:#99917e;margin:0 0 20px}
  input{width:100%;padding:11px 14px;border:1px solid #e4dcc9;border-radius:8px;
    background:#fbf9f3;font-size:14px;box-sizing:border-box;margin-bottom:10px;outline:none}
  input:focus{border-color:#c9992a}
  button{width:100%;padding:11px;border:none;border-radius:8px;background:#a8821f;
    color:#fff;font-family:ui-monospace,Menlo,monospace;font-size:11px;
    letter-spacing:.12em;text-transform:uppercase;cursor:pointer}
  button:hover{background:#c9992a}
  .msg{color:#3f6f3f;font-size:12.5px;min-height:18px;margin-bottom:8px;max-width:38ch;
    margin-left:auto;margin-right:auto}
</style></head><body>
  <form class="box" method="post" action="/portal/request-link">
    <svg class="sun" width="60" height="60" viewBox="-50 -50 100 100" id="s"></svg>
    <h1>LUMNIA</h1><div class="tag">Espace client</div>
    <p class="how">Entrez votre email professionnel — votre lien de connexion
      personnel arrive par email.</p>
    <p class="how-en">Enter your work email — your personal sign-in link
      arrives by email.</p>
    <div class="msg">__MSG__</div>
    <input type="email" name="email" placeholder="vous@entreprise.cd" autofocus required>
    <button type="submit">Recevoir mon lien · Get my link</button>
  </form>
  <script>
    let d=`<circle cx="0" cy="0" r="7.5" fill="#c9992a"/>`;
    for(let r=1;r<=7;r++){let R=9+r*4.6,n=10+r*6,rr=Math.max(.5,2.1-r*.18),o=Math.max(.3,1-r*.1);
      for(let k=0;k<n;k++){let a=k/n*6.283+r*.35;
        d+=`<circle cx="${(R*Math.cos(a)).toFixed(1)}" cy="${(R*Math.sin(a)).toFixed(1)}" r="${rr}" fill="#c9992a" opacity="${o.toFixed(2)}"/>`}}
    document.getElementById("s").innerHTML=d;
  </script>
</body></html>"""
