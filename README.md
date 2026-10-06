# NetworkFix — One-Click Windows Network Repair Tool

> **This project was fully built by an AI agent (model: GLM-5.3)** — from the idea to the code, GUI, packaging and docs, without a single line written by a human.

A tiny, dependency-free Windows utility that fixes the most common network problems with one click. Written in pure Python (standard library only), with a tkinter GUI and automatic UAC elevation.

## Typical scenarios

- Your phone connects to a Wi-Fi network but the laptop refuses to (yet the laptop happily connects to the phone's hotspot)
- Wi-Fi shows "Connected, no Internet"
- Chat apps work but websites won't load (broken DNS)
- Internet broken after installing a VPN / proxy / game accelerator
- Wi-Fi toggle is stuck, or the laptop can't see any wireless network at all
- Connection drops randomly

## Features

| Section | What it does |
|---|---|
| **Network checkup** | Shows current Wi-Fi, internet connectivity, DNS resolution, leftover proxy settings and current DNS servers, with repair suggestions |
| **One-click repair** | 11 selectable repair actions (see table below), executed in order with a live log |
| **Wi-Fi profile manager** | Lists all saved Wi-Fi networks; delete the stubborn one and reconnect — the #1 fix for "phone connects, PC doesn't" |
| **Log** | Everything is logged on screen and exportable to `repair_log.txt` |

## Getting started

### Option A — run from source

```bash
python wifi_fixer.py
```

The app automatically relaunches itself elevated when it detects it is not running as administrator.

### Option B — build a standalone exe (recommended)

Double-click `build.bat`, or run manually:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --uac-admin --name NetworkFix wifi_fixer.py
```

You get a single portable `dist\NetworkFix.exe` (no Python required on the target machine). The `--uac-admin` flag makes Windows show the UAC prompt automatically on launch.

## What the repair actions actually do

| Action | Command | Notes |
|---|---|---|
| Flush DNS cache | `ipconfig /flushdns` | Safe, instant |
| Reset system proxy | `netsh winhttp reset proxy` + registry `ProxyEnable=0` | Removes VPN/proxy leftovers |
| DNS back to DHCP | `netsh interface ip/ipv6 set dnsservers ... source=dhcp` | Clears manual DNS |
| Release / renew IP lease | `ipconfig /release` `/renew` `/registerdns` | Fixes IP conflicts |
| Restart DNS Client service | `sc stop/start Dnscache` | |
| Restart WLAN AutoConfig service | `sc stop/start WlanSvc` | Fixes stuck Wi-Fi radio |
| Disable → enable adapter | `netsh interface set interface ... disable/enable` (physical adapters only) | Like unplugging the NIC; a few seconds offline |
| Disable adapter power saving | PowerShell `Disable-NetAdapterPowerManagement` | Prevents sleep-related drops |
| Reset Winsock | `netsh winsock reset` | ⚠️ reboot required |
| Reset TCP/IP stack | `netsh int ip reset`, `netsh int ipv6 reset`, `arp -d *` | ⚠️ reboot required |
| Force public DNS | 223.5.5.5 / 119.29.29.29 (unchecked by default) | For hijacked/broken ISP DNS |

## If one click is still not enough

1. **Delete that Wi-Fi profile and reconnect** (in-app) — corrupted profiles are the most common cause of "everyone else connects, this PC doesn't"
2. **Reboot** — Winsock/TCP-IP resets only take effect after restart (the app reminds you and can reboot for you)
3. **Turn off Random Hardware Address**: Settings → Network & Internet → Wi-Fi → Random hardware addresses → Off. Routers with MAC filtering will silently reject randomized MACs
4. **Router-side checks** (the phone connecting proves the router is alive, but compatibility may be off):
   - If the SSID is 5 GHz, pin the channel (36/40/44/48 or 149/153/157/161); many laptop NICs choke on DFS channels
   - Switch security mode from WPA3/WPA2-mixed to WPA2-PSK (AES) — older adapters dislike WPA3
   - Set Wi-Fi 6 (802.11ax) to a compatibility/mixed mode
5. **Update the wireless driver** (Device Manager → Network adapters), or grab it from the laptop vendor's site
6. Last resort: Windows Settings → Network & Internet → Advanced network settings → **Network reset**

## Notes

- Repairing briefly drops the connection (adapter cycling, IP renewal) — pause downloads and remote sessions first
- Adapter cycling only touches physical NICs; virtual adapters (ZeroTier, Hyper-V, VMware, TAP) are skipped automatically
- If you elevated through a *different* administrator account, the per-user proxy fix applies to that account, not your logged-in one
- Logs are saved next to the exe/script as `repair_log.txt`

## License

Provided as-is, free to use and modify.
