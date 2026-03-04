#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone test script for the Dahua VTO RPC2 API.

Tests:
  1. RPC2 login (two-step Dahua challenge-response)
  2. AccessUser.startFind / doFind / stopFind  (list all users)
  3. Basic connectivity (getSystemInfo via CGI)

Usage:
  python test_dahua_rpc2.py
  python test_dahua_rpc2.py 192.168.90.11 admin MyPassword

Requirements:
  pip install aiohttp
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

# Force UTF-8 output on Windows so special chars don't crash
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not installed.  Run:  pip install aiohttp")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration - override via command-line arguments
# ---------------------------------------------------------------------------
HOST     = "192.168.90.11"
PORT     = 80
USERNAME = "admin"
PASSWORD = "admin"     # <- change if needed

if len(sys.argv) >= 2:
    HOST = sys.argv[1]
if len(sys.argv) >= 3:
    USERNAME = sys.argv[2]
if len(sys.argv) >= 4:
    PASSWORD = sys.argv[3]

BASE_URL = f"http://{HOST}:{PORT}"

# ---------------------------------------------------------------------------
# RPC2 helpers
# ---------------------------------------------------------------------------

_rpc2_id = 0

def _next_id() -> int:
    global _rpc2_id
    _rpc2_id += 1
    return _rpc2_id


async def rpc2_login(session: aiohttp.ClientSession) -> str:
    """Two-step Dahua RPC2 login, returns session token."""
    url = f"{BASE_URL}/RPC2_Login"

    # Step 1: challenge request (empty password)
    payload1 = {
        "method": "global.login",
        "params": {
            "userName": USERNAME,
            "password": "",
            "clientType": "Web3.0",
            "ipAddr": "0.0.0.0",
            "loginType": "Direct",
        },
        "id": _next_id(),
        "session": 0,
    }
    print(f"\n[1] POST {url}")
    print(f"    SEND: {json.dumps(payload1, indent=4)}")

    async with session.post(url, json=payload1, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as r:
        challenge = await r.json(content_type=None)

    print(f"    RECV: {json.dumps(challenge, indent=4)}")

    # Some devices accept empty-password login and return result=true directly
    if challenge.get("result") and challenge.get("session"):
        print("    [OK] Device accepted empty-password login directly")
        return str(challenge["session"])

    params   = challenge.get("params") or {}
    random_v = params.get("random", "")
    partial  = challenge.get("session", "")
    encrypt  = params.get("encryption", "Default")

    realm    = params.get("realm", "")
    print(f"\n    challenge: random={random_v!r}  realm={realm!r}  encryption={encrypt!r}")

    # Correct Dahua hash formula (extracted from device web UI JavaScript S.a = MD5+toUpperCase):
    #   r = MD5(username + ":" + realm + ":" + password).toUpperCase()
    #   s = MD5(username + ":" + random + ":" + r).toUpperCase()
    # Browser also echoes back realm, random, passwordType, authorityType in 2nd request.
    #
    # Fallback formula B (no realm, uppercase):
    #   r = MD5(password).upper()
    #   s = MD5(username + ":" + random + ":" + r).upper()

    def _hash_a() -> str:
        ph = hashlib.md5(f"{USERNAME}:{realm}:{PASSWORD}".encode()).hexdigest().upper()
        lp = hashlib.md5(f"{USERNAME}:{random_v}:{ph}".encode()).hexdigest().upper()
        print(f"    Formula A (realm+UPPERCASE): pwd_hash={ph}  login_pass={lp}")
        return lp, realm, random_v, encrypt

    def _hash_b() -> str:
        ph = hashlib.md5(PASSWORD.encode()).hexdigest().upper()
        lp = hashlib.md5(f"{USERNAME}:{random_v}:{ph}".encode()).hexdigest().upper()
        print(f"    Formula B (no realm+UPPER):  pwd_hash={ph}  login_pass={lp}")
        return lp, "", "", encrypt

    for formula_name, (login_pass, r_echo, rnd_echo, enc_echo) in [
        ("A (realm+UPPERCASE)", _hash_a()),
        ("B (no realm+UPPER)", _hash_b()),
    ]:
        payload2 = {
            "method": "global.login",
            "params": {
                "userName": USERNAME,
                "password": login_pass,
                "clientType": "Web3.0",
                "realm": r_echo,
                "random": rnd_echo,
                "passwordType": "Default",
                "authorityType": enc_echo,
            },
            "id": _next_id(),
            "session": partial,
        }
        print(f"\n[{payload2['id']}] POST {url}  (formula {formula_name})")
        print(f"    SEND: {json.dumps(payload2, indent=4)}")

        async with session.post(url, json=payload2, timeout=aiohttp.ClientTimeout(total=10), ssl=False) as r2:
            auth = await r2.json(content_type=None)

        print(f"    RECV: {json.dumps(auth, indent=4)}")

        if auth.get("result"):
            token = str(auth.get("session") or partial)
            print(f"\n    [OK] Logged in with formula {formula_name}!")
            print(f"    session token = {token[:16]}...")
            return token

        print(f"    [FAIL] formula {formula_name} rejected – trying next...")

    raise RuntimeError(
        "RPC2 login failed with all formulas. "
        "Check username/password.\n"
        f"Last response: {auth}\n"
        "Hint: run as:  python3 test_dahua_rpc2.py 192.168.90.11 admin YOUR_PASSWORD"
    )


async def rpc2_call(
    session: aiohttp.ClientSession,
    sess_token: str,
    method: str,
    params: dict,
) -> dict:
    """Single authenticated RPC2 call."""
    url = f"{BASE_URL}/RPC2"
    body = {
        "method": method,
        "params": params,
        "id": _next_id(),
        "session": sess_token,
    }
    print(f"\n[{body['id']}] POST {url}  method={method}")
    print(f"    params={json.dumps(params)}")

    async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=15), ssl=False) as r:
        result = await r.json(content_type=None)

    # Truncate large responses for readability
    result_str = json.dumps(result, ensure_ascii=False, indent=4)
    if len(result_str) > 3000:
        result_str = result_str[:3000] + "\n    ... (truncated)"
    print(f"    RECV: {result_str}")
    return result


# ---------------------------------------------------------------------------
# Test: list all users via AccessUser RPC2 API
# ---------------------------------------------------------------------------

async def test_list_users(session: aiohttp.ClientSession, token: str) -> None:
    print("\n" + "="*60)
    print("TEST: AccessUser.startFind / doFind / stopFind")
    print("="*60)

    # startFind
    start = await rpc2_call(session, token, "AccessUser.startFind", {"Condition": {}})
    if not start.get("result"):
        print(f"\n  [FAIL] startFind failed: {start}")
        return

    rpc_token = start["params"]["Token"]
    total     = start["params"].get("Total", 0)
    caps      = start["params"].get("Caps", 0)
    print(f"\n  [OK] startFind OK - Total={total}  Caps={caps}  Token={rpc_token}")

    if total == 0:
        print("  [INFO] No users found on device.")
    else:
        # doFind (paginated)
        all_users: list[dict] = []
        offset    = 0
        page_size = 20

        while offset < total:
            find = await rpc2_call(
                session, token,
                "AccessUser.doFind",
                {"Token": rpc_token, "Offset": offset, "Count": page_size},
            )
            if not find.get("result"):
                print(f"  [FAIL] doFind failed at offset {offset}: {find}")
                break
            batch: list[dict] = find.get("params", {}).get("Info", [])
            if not batch:
                break
            all_users.extend(batch)
            offset += len(batch)

        print(f"\n  [OK] Fetched {len(all_users)} user(s):\n")
        for u in all_users:
            print(f"    ID={u.get('UserID','?'):<8}  Name={u.get('UserName','?'):<20}"
                  f"  Doors={u.get('Doors','?')}  Type={u.get('UserType','?')}")

    # stopFind
    await rpc2_call(session, token, "AccessUser.stopFind", {"Token": rpc_token})
    print("\n  [OK] stopFind OK")


# ---------------------------------------------------------------------------
# Test: basic CGI connectivity
# ---------------------------------------------------------------------------

async def test_cgi_connectivity(session: aiohttp.ClientSession) -> None:
    print("\n" + "="*60)
    print("TEST: Basic CGI connectivity (magicBox.cgi)")
    print("="*60)
    url = f"{BASE_URL}/cgi-bin/magicBox.cgi?action=getSystemInfo"
    print(f"\n  GET {url}")

    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5), ssl=False) as r:
            if r.status == 401:
                www = r.headers.get("WWW-Authenticate","")
                print(f"  -> 401 (auth required)  WWW-Authenticate: {www[:80]}")
            else:
                text = await r.text()
                print(f"  -> {r.status}  body: {text[:300]}")
    except Exception as exc:
        print(f"  [FAIL] {exc}")


# ---------------------------------------------------------------------------
# Test: live event stream (30 seconds)
# ---------------------------------------------------------------------------

async def test_event_stream(session: aiohttp.ClientSession, listen_secs: int = 30) -> None:
    """Connect to the CGI event stream and print every received event for `listen_secs` seconds.

    Uses HTTP Digest auth (same as the HA integration).
    Scan a fingerprint or press the doorbell during this test to see what events arrive.
    """
    print("\n" + "="*60)
    print(f"TEST: Live Event Stream ({listen_secs}s) – scan fingerprint or ring bell now!")
    print("="*60)

    import hashlib as _hashlib, os as _os, re as _re

    CODES = [
        "BackKeyLight", "RingBell", "VideoTalk",
        "AccessControl", "AlarmLocal", "CallNoAnswered",
        "VideoMotion", "DoorStatus", "DoorOpen", "DoorClose",
        "ProfileAlarmTransmit", "KeepLightOn",
    ]
    path = "/cgi-bin/eventManager.cgi?action=attach&codes=[" + ",".join(CODES) + "]"
    url  = f"{BASE_URL}{path}"

    # --- Digest auth probe ---
    def _digest_header(method, uri, www_auth):
        params = {}
        for m in _re.finditer(r'(\w+)=["\']?([^"\',$]+)["\']?', www_auth):
            params[m.group(1)] = m.group(2).strip()
        realm  = params.get("realm","")
        nonce  = params.get("nonce","")
        qop    = params.get("qop","")
        def md5(s): return _hashlib.md5(s.encode()).hexdigest()
        ha1 = md5(f"{USERNAME}:{realm}:{PASSWORD}")
        ha2 = md5(f"{method}:{uri}")
        if "auth" in qop:
            nc=  "00000001"; cnonce = _os.urandom(8).hex()
            rsp = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
            return (f'Digest username="{USERNAME}", realm="{realm}", nonce="{nonce}", '
                    f'uri="{uri}", algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}", response="{rsp}"')
        rsp = md5(f"{ha1}:{nonce}:{ha2}")
        return f'Digest username="{USERNAME}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{rsp}"'

    to_probe = aiohttp.ClientTimeout(total=10)
    to_stream = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=listen_secs + 5)

    try:
        async with session.get(url, timeout=to_probe, ssl=False) as probe:
            www = probe.headers.get("WWW-Authenticate","")
            await probe.release()
    except Exception as exc:
        print(f"  [FAIL] Could not probe event stream: {exc}")
        return

    auth_hdr = _digest_header("GET", path, www) if www else ""
    headers  = {"Authorization": auth_hdr} if auth_hdr else {}

    event_count = 0
    print(f"  Listening on {url[:80]}...")
    print(f"  (waiting up to {listen_secs}s – press Ctrl+C to stop early)\n")

    try:
        async with session.get(url, headers=headers, timeout=to_stream, ssl=False) as resp:
            if resp.status != 200:
                print(f"  [FAIL] HTTP {resp.status}")
                return

            buf = b""
            deadline = asyncio.get_event_loop().time() + listen_secs
            async for chunk in resp.content.iter_chunked(4096):
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("Code="):
                        continue
                    # Parse
                    parts = {}
                    data_split = line.split(";data=", 1)
                    for seg in data_split[0].split(";"):
                        if "=" in seg:
                            k, _, v = seg.partition("=")
                            parts[k.strip()] = v.strip()
                    raw_data = data_split[1] if len(data_split) > 1 else ""
                    event_count += 1
                    code   = parts.get("Code","?")
                    action = parts.get("action","?")
                    index  = parts.get("index","?")
                    print(f"  [{event_count:03d}] Code={code}  action={action}  index={index}")
                    if raw_data:
                        print(f"        data={raw_data[:300]}")
                if asyncio.get_event_loop().time() > deadline:
                    break

    except asyncio.TimeoutError:
        pass
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"  [FAIL] Stream error: {exc}")

    print(f"\n  Received {event_count} event(s) in {listen_secs}s.")
    if event_count == 0:
        print("  [WARN] No events received – check if the event stream URL is correct.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("="*60)
    print("Dahua VTO RPC2 Test")
    print(f"  Host    : {BASE_URL}")
    print(f"  Username: {USERNAME}")
    print(f"  Password: {'*' * len(PASSWORD)}")
    print("="*60)

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        # 1. Basic CGI reachability
        await test_cgi_connectivity(session)

        # 2. RPC2 login
        print("\n" + "="*60)
        print("TEST: RPC2 Login")
        print("="*60)
        try:
            token = await rpc2_login(session)
        except Exception as exc:
            print(f"\n  [FAIL] Login failed: {exc}")
            return

        # 3. List users
        await test_list_users(session, token)

        # 4. Live event stream – scan fingerprint / ring bell during this test
        await test_event_stream(session, listen_secs=30)

    print("\n" + "="*60)
    print("Done.")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
