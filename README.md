# Codex Proxy Manager

Codex Proxy Manager is a small macOS menu-bar proxy manager for Xray profiles imported from V2Box or 3x-ui subscriptions.

It is built for a simple desktop workflow:

- import `vmess://` / `vless://` links from the clipboard, a subscription URL, QR images, or the local V2Box database
- start a bundled Xray core on local HTTP and SOCKS ports
- hand over macOS Wi-Fi system proxy with `networksetup`
- test node latency from the menu or the main panel
- show real-time upload/download speed in the macOS menu bar
- stop the Xray process when the app exits

## Current Status

This is a personal utility app, not a polished commercial VPN client. It is useful when you want a lightweight replacement for a GUI proxy client and already have working 3x-ui/V2Box share links.

The app currently targets macOS on Apple Silicon.

## Files

- `outputs/Codex Proxy Manager.app` - packaged macOS app bundle
- `outputs/Codex Proxy Manager.app.zip` - zipped app bundle for transfer
- `outputs/ProxyManagerApp.swift` - native SwiftUI menu-bar UI
- `outputs/proxy_manager.py` - Python command-line manager used by the app
- `outputs/README_PROXY_MANAGER.md` - short local command reference

## Quick Start

Download or clone the repository, then open:

```text
outputs/Codex Proxy Manager.app
```

Because the app is ad-hoc signed, macOS may block the first launch. If that happens, right-click the app and choose `Open`, or allow it in System Settings.

The app starts as a menu-bar item. Open the menu from the top bar to:

- show the main panel
- import nodes
- select a node
- run latency tests
- toggle real-time speed display
- start or stop proxy handover
- switch system proxy back to Clash

## Proxy Ports

When started, the app runs local proxy ports:

```text
HTTP/HTTPS: 127.0.0.1:56542
SOCKS:      127.0.0.1:56543
```

The default macOS network service is `Wi-Fi`.

## Import Methods

The app can import profiles from:

- clipboard text
- subscription URL
- QR image
- V2Box local database

Supported share links include common `vmess://` and `vless://` formats used by 3x-ui. Reality, TLS, WS, TCP, SNI, host, path, public key, short ID, fingerprint, and flow parameters are mapped into Xray config where available.

## Safety Notes

Do not commit private subscriptions, server IPs, UUIDs, Reality private keys, or generated Xray runtime configs.

The app intentionally avoids disabling another proxy manager's system proxy when it stops. It only turns off system proxy if the current macOS proxy points to this app's own ports, `56542` or `56543`. This prevents accidentally breaking an active Clash connection.

## CLI Usage

The app delegates to `outputs/proxy_manager.py`. You can run it directly:

```bash
cd outputs

./proxy_manager.py list-profiles
./proxy_manager.py import-text 'vmess://...'
./proxy_manager.py start --profile <profile-id>
./proxy_manager.py status
./proxy_manager.py test https://api.ipify.org
./proxy_manager.py stop --only-own-system-proxy
```

Switch system proxy back to Clash:

```bash
./proxy_manager.py proxy-clash
```

Disable this app's system proxy:

```bash
./proxy_manager.py proxy-off
```

## Build

Compile the SwiftUI launcher into the app bundle:

```bash
swiftc -parse-as-library outputs/ProxyManagerApp.swift \
  -o "outputs/Codex Proxy Manager.app/Contents/MacOS/launcher"
```

Create a transferable zip:

```bash
ditto -c -k --sequesterRsrc --keepParent \
  "outputs/Codex Proxy Manager.app" \
  "outputs/Codex Proxy Manager.app.zip"
```

## Dependencies

- macOS
- Python 3
- Swift toolchain
- `networksetup`
- Xray core, bundled inside the app at `Contents/Resources/xray`

## Disclaimer

This project is for personal network proxy management and debugging. Use it only with servers and subscriptions you own or are authorized to use.
