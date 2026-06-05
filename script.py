# Rec Room Target Room Exporter (Using True export.py Logic)
from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests

ROOMS_API = "https://rooms.rec.net"
ACCOUNTS_API = "https://accounts.rec.net"
CDN_ROOM = "https://cdn.rec.net/room/"

SESSION_COOKIE = "__Secure-next-auth.session-token"
IMPERSONATE = {"chrome": "chrome", "edge": "chrome", "brave": "chrome",
               "vivaldi": "chrome", "opera": "chrome", "firefox": "firefox"}

BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://rec.net/",
    "Origin": "https://rec.net",
    "Sec-GPC": "1",
    "Connection": "keep-alive"
}

def banner(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}", flush=True)

def sanitize(s):
    return re.sub(r"[^\w.\-]+", "_", str(s)).strip("_")[:80] or "x"

def cookies_from_browser(browser="auto"):
    try:
        import browser_cookie3 as bc3
    except ImportError:
        print("  browser_cookie3 not installed.")
        return None, None
    order = ["chrome", "edge", "brave", "firefox", "opera", "vivaldi"]
    if browser != "auto":
        order = [browser]
    for name in order:
        fn = getattr(bc3, name, None)
        if not fn:
            continue
        try:
            jar = fn(domain_name="rec.net")
        except Exception:
            continue
        cookies = {c.name: c.value for c in jar if c.value}
        if SESSION_COOKIE in cookies:
            return cookies, name
    return None, None

def mint_bearer(cookies, impersonate="chrome"):
    try:
        from curl_cffi import requests as creq
        s = creq.Session(impersonate=impersonate)
        for k, v in cookies.items():
            s.cookies.set(k, v)
        data = s.get("https://rec.net/api/auth/session", timeout=30).json()
    except Exception:
        return None
    return data.get("accessToken")

def choose_auth():
    print("How do you want to log in?")
    print("  1) Fetch login automatically from my browser")
    print("  2) Paste my own token (Bearer access token OR session token)")
    choice = input("Choose 1 or 2: ").strip()

    if choice == "1":
        cookies, name = cookies_from_browser("auto")
        if not cookies:
            sys.exit("No rec.net login found in browser.")
        imp = IMPERSONATE.get(name, "chrome")
        tok = mint_bearer(cookies, imp)
        if not tok:
            sys.exit("Could not extract token.")
        return tok, cookies, imp, f"{name} browser"

    if choice == "2":
        raw = input("Paste your token: ").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        if not raw:
            sys.exit("No token entered.")
        r = requests.get(f"{ACCOUNTS_API}/account/me", headers={"Authorization": f"Bearer {raw}"}, timeout=30)
        if r.status_code == 200:
            return raw, None, "chrome", "pasted Bearer token"
        tok = mint_bearer({SESSION_COOKIE: raw}, "chrome")
        if tok:
            return tok, {SESSION_COOKIE: raw}, "chrome", "pasted session token"
        sys.exit("Token was rejected.")
    sys.exit("Invalid choice.")

class Client:
    def __init__(self, token, cookies, impersonate):
        self.token, self.cookies, self.imp = token, cookies, impersonate
        self.s = requests.Session()
        self.s.headers.update(BASE_HEADERS)
        self.s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0"
        self._lock = threading.Lock()

    def _remint(self):
        if self.cookies:
            t = mint_bearer(self.cookies, self.imp)
            if t:
                self.token = t
                return True
        return False

    def get(self, url, **kw):
        kw.setdefault("timeout", 40)
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.token}"}
            r = self.s.get(url, headers=headers, **kw)
            if r.status_code == 401 and attempt == 0:
                with self._lock:
                    if not self._remint():
                        return r
                continue
            return r
        return r

    def json(self, url, **kw):
        r = self.get(url, **kw)
        try:
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

def download(url, dest: Path):
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    try:
        r = requests.get(url, timeout=60, stream=True)
    except Exception:
        return "fail"
    if r.status_code != 200:
        r.close()
        return "fail"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(65536):
            fh.write(chunk)
    tmp.replace(dest)
    return "ok"

def main():
    banner("Rec Room Target Room Exporter")
    
    token, cookies, imp, label = choose_auth()
    print(f"\nAuthenticated via: {label}")
    cli = Client(token, cookies, imp)
    
    room_query = input("\nEnter exact room name to download (e.g., TheBackDoor): ").strip()
    if not room_query:
        sys.exit("No room name entered.")
        
    print(f"\nResolving base identity for '{room_query}'...")
    response = cli.get(f"{ROOMS_API}/rooms?name={requests.utils.quote(room_query)}")
    if response.status_code != 200:
        response = cli.get(f"{ROOMS_API}/rooms/search?query={requests.utils.quote(room_query)}")

    if response.status_code != 200:
        sys.exit(f"Failed to access room indexing registry. Status: {response.status_code}")
        
    raw_results = response.json()
    results = [raw_results] if isinstance(raw_results, dict) else raw_results

    if not results or results[0].get("RoomId") is None:
        sys.exit("Room name could not be resolved.")

    rm = results[0]
    rid = rm.get("RoomId")
    rn = sanitize(rm.get("Name") or rm.get("RoomId"))
    
    print(f"Target Confirmed: ^{rn} (Room ID: {rid})")
    
    # Mirroring export.py save scanning environment logic directly
    print("\n[1/3] Querying deep room manifest layout (include=15)...")
    det = cli.json(f"{ROOMS_API}/rooms/{rid}?include=15") or {}
    
    base_dir = Path(f"Export_{rn}_{rid}")
    rdir = base_dir / "rooms" / f"{rn}_{rid}"
    rdir.mkdir(parents=True, exist_ok=True)
    
    (rdir / "_room_detail.json").write_text(json.dumps(det, indent=2), encoding="utf-8")
    
    tasks = []
    saves_index = {}
    saves_per_subroom = 200  # Matched configuration setting
    
    subrooms_list = det.get("SubRooms") or []
    print(f"[2/3] Processing saves loop across {len(subrooms_list)} subrooms...")
    
    for sr in subrooms_list:
        srid = sr.get("SubRoomId")
        srn = sanitize(sr.get("Name") or sr.get("SubRoomId"))
        
        print(f"  -> Scanning save tree for subroom: '{srn}' ({srid})")
        
        # Pulling version indices identically to your provided format logic
        sv = cli.json(f"{ROOMS_API}/rooms/{rid}/subrooms/{srid}/saves?skip=0&take={saves_per_subroom}") or {}
        res = (sv.get("Results") if isinstance(sv, dict) else None) or []
        saves_index[f"{rid}/{srid}"] = res
        
        for s in res:
            if s.get("DataBlob"):
                blob_name = s["DataBlob"]
                # Forcing extension verification out to look like native system room file
                out_filename = blob_name if "." in blob_name else f"{blob_name}.room"
                
                # Append exact CDN mirror path tuple mapping
                tasks.append((CDN_ROOM + blob_name, rdir / f"{srn}_{srid}" / out_filename))

    (base_dir / "room_saves.json").write_text(json.dumps(saves_index, indent=2), encoding="utf-8")
    
    print(f"\n[3/3] Found {len(tasks)} target version blobs to retrieve. Spawning threads...")
    
    # Execute batch worker queue processing
    done, total = 0, len(tasks)
    c = {"ok": 0, "skip": 0, "fail": 0}
    
    if tasks:
        with ThreadPoolExecutor(max_workers=14) as ex:
            for fut in as_completed(ex.submit(download, u, d) for u, d in tasks):
                c[fut.result()] += 1
                done += 1
                if done % 10 == 0 or done == total:
                    print(f"  Progress: {done}/{total} files (Downloaded: {c['ok']} | Skipped: {c['skip']} | Failed: {c['fail']})", flush=True)
    
    banner("Download Process Concluded")
    print(f"Destination: {base_dir.resolve()}")
    print(f"Successfully processed: {c['ok'] + c['skip']} files total.")

if __name__ == "__main__":
    main()