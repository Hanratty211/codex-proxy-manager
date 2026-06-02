#!/usr/bin/env python3
import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


APP_NAME = "Codex Proxy Manager"
DEFAULT_PROFILE = "CDN-jl3opux3"
DEFAULT_SERVICE = "Wi-Fi"
HTTP_PORT = 56542
SOCKS_PORT = 56543

def app_support_root():
    override = os.environ.get("CODEX_PROXY_MANAGER_STATE")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support/Codex Proxy Manager"


ROOT = Path(__file__).resolve().parent
STATE = app_support_root()
BIN_DIR = STATE / "bin"
RUN_DIR = STATE / "run"
PID_FILE = RUN_DIR / "xray.pid"
CONFIG_FILE = RUN_DIR / "config.json"
LOG_FILE = RUN_DIR / "xray.log"
PROFILES_FILE = STATE / "profiles.json"

V2BOX_DB = Path.home() / "Library/Group Containers/group.hossin.asaadi.V2Box/DB.sqlite"


def b64decode_text(value):
    value = value.strip()
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode()).decode("utf-8", "replace")


def profile_id(name, outbound):
    raw = json.dumps({"name": name, "outbound": outbound}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def load_profile_store():
    ensure_dirs()
    if not PROFILES_FILE.exists():
        return []
    try:
        data = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_profile_store(items):
    ensure_dirs()
    PROFILES_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def profile_summary(item):
    out = item["outbound"]
    stream = out.get("streamSettings", {})
    settings = out.get("settings", {})
    vnext = settings.get("vnext", [{}])[0]
    servers = settings.get("servers", [{}])[0]
    return {
        "id": item["id"],
        "name": item["name"],
        "protocol": out.get("protocol", ""),
        "server": vnext.get("address") or servers.get("address") or "",
        "port": vnext.get("port") or servers.get("port") or "",
        "network": stream.get("network", "tcp"),
        "security": stream.get("security", "none"),
        "source": item.get("source", "local"),
    }


def get_v2box_profiles():
    if not V2BOX_DB.exists():
        return []
    con = sqlite3.connect(V2BOX_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select ZREMARK, ZJSON from ZCDV2RAYITEM where ZJSON is not null"
        ).fetchall()
    finally:
        con.close()
    items = []
    for row in rows:
        try:
            cfg = json.loads(row["ZJSON"])
            outbound = cfg["outbounds"][0]
            outbound["tag"] = "proxy"
            name = row["ZREMARK"] or "V2Box Profile"
            items.append(
                {
                    "id": "v2box-" + profile_id(name, outbound),
                    "name": name,
                    "outbound": outbound,
                    "source": "v2box",
                    "updatedAt": int(time.time()),
                }
            )
        except Exception:
            continue
    return items


def all_profiles():
    seen = set()
    items = []
    for item in load_profile_store() + get_v2box_profiles():
        key = item["id"]
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def parse_vmess(link):
    payload = link[len("vmess://") :]
    data = json.loads(b64decode_text(payload))
    name = urllib.parse.unquote(data.get("ps") or data.get("remark") or "VMess")
    network = data.get("net") or "tcp"
    security = "tls" if str(data.get("tls", "")).lower() == "tls" else "none"
    host = data.get("host") or ""
    path = data.get("path") or ""
    sni = data.get("sni") or host
    outbound = {
        "tag": "proxy",
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": data.get("add", ""),
                    "port": int(data.get("port", 443)),
                    "users": [
                        {
                            "id": data.get("id", ""),
                            "alterId": int(data.get("aid", 0) or 0),
                            "security": data.get("scy") or "auto",
                        }
                    ],
                }
            ]
        },
        "streamSettings": {"network": network, "security": security},
    }
    stream = outbound["streamSettings"]
    if security == "tls":
        stream["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": data.get("fp") or "chrome",
            "allowInsecure": bool(data.get("allowInsecure", False)),
        }
    if network == "ws":
        stream["wsSettings"] = {"path": path or "/", "headers": {"Host": host or sni}}
    return name, outbound


def parse_vless(link):
    parsed = urllib.parse.urlparse(link)
    qs = urllib.parse.parse_qs(parsed.query)

    def q(name, default=""):
        return qs.get(name, [default])[0]

    name = urllib.parse.unquote(parsed.fragment or parsed.hostname or "VLESS")
    network = q("type", "tcp")
    security = q("security", "none")
    flow = q("flow", "")
    user = {"id": parsed.username or "", "encryption": q("encryption", "none")}
    if flow:
        user["flow"] = flow
    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": parsed.hostname or "",
                    "port": int(parsed.port or 443),
                    "users": [user],
                }
            ]
        },
        "streamSettings": {"network": network, "security": security},
    }
    stream = outbound["streamSettings"]
    if security == "reality":
        stream["realitySettings"] = {
            "serverName": q("sni"),
            "fingerprint": q("fp", "chrome"),
            "publicKey": q("pbk"),
            "shortId": q("sid"),
            "spiderX": q("spx", "/"),
        }
    elif security == "tls":
        stream["tlsSettings"] = {
            "serverName": q("sni") or q("host") or parsed.hostname or "",
            "fingerprint": q("fp", "chrome"),
            "allowInsecure": q("allowInsecure", "0") in ("1", "true", "True"),
        }
    if network == "ws":
        stream["wsSettings"] = {
            "path": urllib.parse.unquote(q("path", "/")),
            "headers": {"Host": q("host") or q("sni") or parsed.hostname or ""},
        }
    elif network == "tcp":
        stream["tcpSettings"] = {"header": {"type": q("headerType", "none")}}
    return name, outbound


def extract_links(text):
    text = text.strip()
    if text.startswith("http://") or text.startswith("https://"):
        req = urllib.request.Request(text, headers={"User-Agent": APP_NAME})
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", "replace")
    if "://" not in text:
        try:
            decoded = b64decode_text(text)
            if "://" in decoded:
                text = decoded
        except Exception:
            pass
    links = []
    for token in text.replace("\r", "\n").replace(" ", "\n").split("\n"):
        token = token.strip()
        if token.startswith(("vmess://", "vless://")):
            links.append(token)
    return links


def import_text_value(text, source="manual"):
    links = extract_links(text)
    if not links:
        raise SystemExit("No vmess:// or vless:// links found.")
    store = load_profile_store()
    by_id = {item["id"]: item for item in store}
    imported = []
    for link in links:
        try:
            if link.startswith("vmess://"):
                name, outbound = parse_vmess(link)
            elif link.startswith("vless://"):
                name, outbound = parse_vless(link)
            else:
                continue
            item = {
                "id": profile_id(name, outbound),
                "name": name,
                "outbound": outbound,
                "source": source,
                "updatedAt": int(time.time()),
            }
            by_id[item["id"]] = item
            imported.append(profile_summary(item))
        except Exception as exc:
            print(f"Skipped link: {exc}", file=sys.stderr)
    save_profile_store(list(by_id.values()))
    print(json.dumps(imported, ensure_ascii=False, indent=2))


def run(cmd, check=True, capture=False):
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and result.returncode != 0:
        out = (result.stdout or "").strip()
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{out}")
    return result.stdout if capture else ""


def ensure_dirs():
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def xray_path():
    bundled = ROOT / "xray"
    if bundled.exists():
        return bundled
    return BIN_DIR / "xray"


def arch_asset_hint():
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if machine in ("x86_64", "amd64"):
        return "macos-64"
    raise SystemExit(f"Unsupported architecture: {machine}")


def install_xray():
    ensure_dirs()
    if xray_path().exists():
        print(f"Xray already installed: {xray_path()}")
        return

    hint = arch_asset_hint()
    print("Finding latest Xray release...")
    req = urllib.request.Request(
        "https://api.github.com/repos/XTLS/Xray-core/releases/latest",
        headers={"User-Agent": APP_NAME},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        release = json.loads(resp.read().decode("utf-8"))

    assets = release.get("assets", [])
    asset = None
    for item in assets:
        name = item.get("name", "")
        if hint in name and name.endswith(".zip"):
            asset = item
            break
    if not asset:
        names = ", ".join(a.get("name", "") for a in assets)
        raise SystemExit(f"Could not find Xray asset containing {hint}. Assets: {names}")

    url = asset["browser_download_url"]
    zip_path = STATE / asset["name"]
    print(f"Downloading {asset['name']}...")
    urllib.request.urlretrieve(url, zip_path)

    extract_dir = STATE / "xray-extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)

    candidate = extract_dir / "xray"
    if not candidate.exists():
        matches = list(extract_dir.rglob("xray"))
        if not matches:
            raise SystemExit("Downloaded Xray archive did not contain an xray binary.")
        candidate = matches[0]
    shutil.copy2(candidate, xray_path())
    xray_path().chmod(0o755)
    print(f"Installed Xray: {xray_path()}")


def load_profile(profile):
    for item in all_profiles():
        if item["id"] == profile or item["name"] == profile:
            outbound = item["outbound"]
            outbound["tag"] = "proxy"
            return item["name"], outbound

    if not V2BOX_DB.exists():
        raise SystemExit(f"V2Box database not found: {V2BOX_DB}")

    con = sqlite3.connect(V2BOX_DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select ZREMARK, ZJSON from ZCDV2RAYITEM where ZJSON is not null"
        ).fetchall()
    finally:
        con.close()

    selected = None
    for row in rows:
        if row["ZREMARK"] == profile:
            selected = row
            break
    if selected is None:
        for row in rows:
            try:
                cfg = json.loads(row["ZJSON"])
                out = cfg.get("outbounds", [{}])[0]
                if out.get("protocol") == "vmess" and out.get("streamSettings", {}).get("network") == "ws":
                    selected = row
                    break
            except Exception:
                pass
    if selected is None:
        raise SystemExit(f"No usable V2Box profile found. Expected remark: {profile}")

    cfg = json.loads(selected["ZJSON"])
    outbound = cfg["outbounds"][0]
    outbound["tag"] = "proxy"
    return selected["ZREMARK"], outbound


def build_config(profile):
    name, outbound = load_profile(profile)
    config = {
        "log": {
            "loglevel": "warning",
            "access": str(RUN_DIR / "access.log"),
            "error": str(LOG_FILE),
        },
        "inbounds": [
            {
                "tag": "http",
                "listen": "127.0.0.1",
                "port": HTTP_PORT,
                "protocol": "http",
                "settings": {"timeout": 0},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"],
                    "routeOnly": False,
                },
            },
            {
                "tag": "socks",
                "listen": "127.0.0.1",
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"],
                    "routeOnly": False,
                },
            },
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "ip": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
                    "outboundTag": "direct",
                }
            ],
        },
    }
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return name


def pid_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def current_pid():
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return None
    return pid if pid_running(pid) else None


def port_busy(port):
    out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], check=False, capture=True)
    return bool(out.strip())


def cleanup_orphan_xray_ports():
    active = current_pid()
    for port in (HTTP_PORT, SOCKS_PORT):
        out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], check=False, capture=True)
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            command, pid_text = parts[0], parts[1]
            if command.lower() != "xray":
                continue
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if active is not None and pid == active:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    time.sleep(0.3)


def set_system_proxy(service):
    run(["networksetup", "-setwebproxy", service, "127.0.0.1", str(HTTP_PORT)])
    run(["networksetup", "-setsecurewebproxy", service, "127.0.0.1", str(HTTP_PORT)])
    run(["networksetup", "-setsocksfirewallproxy", service, "127.0.0.1", str(SOCKS_PORT)])
    run(["networksetup", "-setwebproxystate", service, "on"])
    run(["networksetup", "-setsecurewebproxystate", service, "on"])
    run(["networksetup", "-setsocksfirewallproxystate", service, "on"])
    run(
        [
            "networksetup",
            "-setproxybypassdomains",
            service,
            "127.0.0.1",
            "localhost",
            "*.local",
            "192.168.0.0/16",
            "10.0.0.0/8",
            "172.16.0.0/12",
        ]
    )


def disable_system_proxy(service):
    run(["networksetup", "-setwebproxystate", service, "off"], check=False)
    run(["networksetup", "-setsecurewebproxystate", service, "off"], check=False)
    run(["networksetup", "-setsocksfirewallproxystate", service, "off"], check=False)


def system_proxy_uses_app(service):
    checks = [
        ("-getwebproxy", HTTP_PORT),
        ("-getsecurewebproxy", HTTP_PORT),
        ("-getsocksfirewallproxy", SOCKS_PORT),
    ]
    for command, port in checks:
        out = run(["networksetup", command, service], check=False, capture=True)
        if "Enabled: Yes" in out and "Server: 127.0.0.1" in out and f"Port: {port}" in out:
            return True
    return False


def start(args):
    ensure_dirs()
    if not xray_path().exists():
        install_xray()
    if current_pid():
        print(f"Already running: pid {current_pid()}")
        if args.system_proxy:
            ok, output = wait_proxy_ready()
            print(f"Verification: {'OK' if ok else 'FAILED'} {output}")
            if not ok:
                raise SystemExit("Proxy verification failed before changing system proxy. System proxy was left unchanged.")
            set_system_proxy(args.service)
            print(f"System proxy enabled on {args.service}.")
        return
    cleanup_orphan_xray_ports()
    if port_busy(HTTP_PORT) or port_busy(SOCKS_PORT):
        raise SystemExit(
            f"Port {HTTP_PORT} or {SOCKS_PORT} is already in use. "
            "Quit V2Box/Clash or run stop/status first."
        )

    profile_name = build_config(args.profile)
    log = LOG_FILE.open("ab")
    proc = subprocess.Popen(
        [str(xray_path()), "run", "-config", str(CONFIG_FILE)],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.5)
    if not pid_running(proc.pid):
        raise SystemExit(f"Xray exited immediately. See log: {LOG_FILE}")

    print(f"Started {profile_name}: pid {proc.pid}")
    print(f"HTTP proxy:  127.0.0.1:{HTTP_PORT}")
    print(f"SOCKS proxy: 127.0.0.1:{SOCKS_PORT}")
    ok, output = wait_proxy_ready()
    print(f"Verification: {'OK' if ok else 'FAILED'} {output}")
    if not ok:
        os.kill(proc.pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        raise SystemExit("Proxy verification failed before changing system proxy. System proxy was left unchanged.")
    if args.system_proxy:
        set_system_proxy(args.service)
        print(f"System proxy enabled on {args.service}.")


def stop(args):
    pid = current_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            if not pid_running(pid):
                break
            time.sleep(0.1)
        if pid_running(pid):
            os.kill(pid, signal.SIGKILL)
        print(f"Stopped Xray pid {pid}")
    else:
        print("Xray is not running.")
    PID_FILE.unlink(missing_ok=True)
    if args.system_proxy:
        if args.only_own_system_proxy and not system_proxy_uses_app(args.service):
            print(f"System proxy left unchanged on {args.service}; it is not using 127.0.0.1:{HTTP_PORT}/{SOCKS_PORT}.")
            return
        disable_system_proxy(args.service)
        print(f"System proxy disabled on {args.service}.")


def proxy_on(args):
    set_system_proxy(args.service)
    print(f"System proxy enabled on {args.service}: 127.0.0.1:{HTTP_PORT} / SOCKS {SOCKS_PORT}")


def proxy_off(args):
    disable_system_proxy(args.service)
    print(f"System proxy disabled on {args.service}.")


def proxy_clash(args):
    run(["networksetup", "-setwebproxy", args.service, "127.0.0.1", "7890"])
    run(["networksetup", "-setsecurewebproxy", args.service, "127.0.0.1", "7890"])
    run(["networksetup", "-setsocksfirewallproxy", args.service, "127.0.0.1", "7890"])
    run(["networksetup", "-setwebproxystate", args.service, "on"])
    run(["networksetup", "-setsecurewebproxystate", args.service, "on"])
    run(["networksetup", "-setsocksfirewallproxystate", args.service, "on"])
    print(f"System proxy switched back to Clash on {args.service}: 127.0.0.1:7890")


def status(args):
    pid = current_pid()
    print(f"Xray: {'running pid ' + str(pid) if pid else 'not running'}")
    for port in (HTTP_PORT, SOCKS_PORT):
        out = run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], check=False, capture=True).strip()
        print(f"Port {port}: {'listening' if out else 'free'}")
        if out:
            print(out)
    print()
    print(run(["networksetup", "-getwebproxy", args.service], check=False, capture=True).strip())
    print(run(["networksetup", "-getsecurewebproxy", args.service], check=False, capture=True).strip())
    print(run(["networksetup", "-getsocksfirewallproxy", args.service], check=False, capture=True).strip())


def test(args):
    url = args.url
    env = os.environ.copy()
    env["http_proxy"] = f"http://127.0.0.1:{HTTP_PORT}"
    env["https_proxy"] = f"http://127.0.0.1:{HTTP_PORT}"
    env["all_proxy"] = f"socks5://127.0.0.1:{SOCKS_PORT}"
    result = subprocess.run(
        ["curl", "-sS", "--connect-timeout", "8", "--max-time", "15", url],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    print(result.stdout.strip())
    raise SystemExit(result.returncode)


def quick_proxy_test(url="https://api.ipify.org"):
    result = subprocess.run(
        ["curl", "-sS", "--proxy", f"http://127.0.0.1:{HTTP_PORT}", "--connect-timeout", "8", "--max-time", "12", url],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return result.returncode == 0, result.stdout.strip()


def wait_proxy_ready(url="https://api.ipify.org", attempts=8, delay=1.5):
    last_output = ""
    for attempt in range(1, attempts + 1):
        ok, output = quick_proxy_test(url)
        if ok:
            return True, output
        last_output = output or f"attempt {attempt} failed"
        time.sleep(delay)
    return False, last_output


def list_profiles(args):
    print(json.dumps([profile_summary(item) for item in all_profiles()], ensure_ascii=False, indent=2))


def import_text(args):
    import_text_value(args.text, source=args.source)


def import_file(args):
    import_text_value(Path(args.path).read_text(encoding="utf-8"), source=args.source)


def import_url(args):
    import_text_value(args.url, source=args.source)


def ping_profile(args):
    ensure_dirs()
    if not xray_path().exists():
        install_xray()
    name, outbound = load_profile(args.profile)
    temp_port = args.port
    cfg = json.loads(json.dumps({
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "http", "listen": "127.0.0.1", "port": temp_port, "protocol": "http", "settings": {"timeout": 0}}
        ],
        "outbounds": [outbound, {"tag": "direct", "protocol": "freedom"}],
    }))
    temp_cfg = RUN_DIR / f"ping-{os.getpid()}.json"
    temp_cfg.write_text(json.dumps(cfg), encoding="utf-8")
    proc = subprocess.Popen([str(xray_path()), "run", "-config", str(temp_cfg)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.8)
        start_time = time.time()
        result = subprocess.run(
            ["curl", "-sS", "--proxy", f"http://127.0.0.1:{temp_port}", "--connect-timeout", "8", "--max-time", "12", args.url],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        ms = int((time.time() - start_time) * 1000)
        payload = {"profile": name, "ok": result.returncode == 0, "ms": ms, "output": result.stdout.strip()[:200]}
        print(json.dumps(payload, ensure_ascii=False))
        raise SystemExit(0 if result.returncode == 0 else result.returncode)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        temp_cfg.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Small Xray/networksetup proxy manager.")
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="networksetup service name")
    sub = parser.add_subparsers(dest="cmd", required=True)

    install_p = sub.add_parser("install")
    install_p.set_defaults(func=lambda args: install_xray())

    start_p = sub.add_parser("start")
    start_p.add_argument("--profile", default=DEFAULT_PROFILE, help="V2Box profile remark to import")
    start_p.add_argument("--no-system-proxy", dest="system_proxy", action="store_false")
    start_p.set_defaults(func=start, system_proxy=True)

    stop_p = sub.add_parser("stop")
    stop_p.add_argument("--keep-system-proxy", dest="system_proxy", action="store_false")
    stop_p.add_argument("--only-own-system-proxy", action="store_true")
    stop_p.set_defaults(func=stop, system_proxy=True)

    status_p = sub.add_parser("status")
    status_p.set_defaults(func=status)

    proxy_on_p = sub.add_parser("proxy-on")
    proxy_on_p.set_defaults(func=proxy_on)

    proxy_off_p = sub.add_parser("proxy-off")
    proxy_off_p.set_defaults(func=proxy_off)

    proxy_clash_p = sub.add_parser("proxy-clash")
    proxy_clash_p.set_defaults(func=proxy_clash)

    test_p = sub.add_parser("test")
    test_p.add_argument("url", nargs="?", default="https://api.ipify.org")
    test_p.set_defaults(func=test)

    list_p = sub.add_parser("list-profiles")
    list_p.set_defaults(func=list_profiles)

    import_text_p = sub.add_parser("import-text")
    import_text_p.add_argument("text")
    import_text_p.add_argument("--source", default="manual")
    import_text_p.set_defaults(func=import_text)

    import_file_p = sub.add_parser("import-file")
    import_file_p.add_argument("path")
    import_file_p.add_argument("--source", default="file")
    import_file_p.set_defaults(func=import_file)

    import_url_p = sub.add_parser("import-url")
    import_url_p.add_argument("url")
    import_url_p.add_argument("--source", default="subscription")
    import_url_p.set_defaults(func=import_url)

    ping_p = sub.add_parser("ping-profile")
    ping_p.add_argument("profile")
    ping_p.add_argument("--url", default="https://api.ipify.org")
    ping_p.add_argument("--port", type=int, default=56552)
    ping_p.set_defaults(func=ping_profile)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
