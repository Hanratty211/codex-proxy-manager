# Codex Proxy Manager

Small local Xray launcher for macOS. It imports the working CDN VMess profile from V2Box's local database, starts local HTTP/SOCKS proxy ports, and configures `networksetup`.

See the root `README.md` for the public project overview, app usage, import methods, and safety notes.

## Commands

```bash
cd /Users/wht/Documents/Codex/2026-06-02/new-chat/outputs

./proxy_manager.py start
./proxy_manager.py status
./proxy_manager.py test https://api.ipify.org
./proxy_manager.py stop
```

## Ports

- HTTP/HTTPS proxy: `127.0.0.1:56542`
- SOCKS proxy: `127.0.0.1:56543`

## Recovery

Switch system proxy back to Clash:

```bash
./proxy_manager.py proxy-clash
```

Disable Wi-Fi system proxy:

```bash
./proxy_manager.py proxy-off
```
