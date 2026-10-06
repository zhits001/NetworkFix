# -*- coding: utf-8 -*-
"""
网络一键修复工具 (Windows)
==========================
针对常见故障一键修复：能连手机热点却连不上路由器 / 连上WiFi显示"无Internet" /
网页打不开(DNS) / 装过VPN加速器后上不了网 / WiFi开关卡死搜不到网络 等。

- 仅使用 Python 标准库，无第三方依赖
- 启动时自动申请管理员权限(UAC)
- 打包: 见 build.bat (PyInstaller --uac-admin)
"""

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "网络一键修复工具"
APP_VERSION = "1.0"
APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
LOG_FILE = os.path.join(APP_DIR, "repair_log.txt")
POWERSHELL = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"]
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# 虚拟/系统伪网卡不参与"禁用再启用"
IFACE_EXCLUDE = re.compile(r"^(Loopback|isatap|Teredo|vEthernet)|^本地连接\*", re.I)


# ---------------------------------------------------------------- 基础工具 --
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated():
    """以管理员身份重新启动自身，成功返回 True。"""
    if getattr(sys, "frozen", False):
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        params = " ".join([f'"{os.path.abspath(sys.argv[0])}"'] + sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1)
        return rc > 32
    except Exception:
        return False


def _decode(b):
    # Windows 中文系统下 netsh/sc 等输出为 GBK(CP936)
    for enc in ("gbk", "utf-8"):
        try:
            return b.decode(enc)
        except UnicodeError:
            pass
    return b.decode("gbk", "ignore")


def run(cmd, timeout=120, cwd=None):
    """执行外部命令，返回 (returncode, 合并后的输出文本)。"""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=CREATE_FLAGS, cwd=cwd)
    except subprocess.TimeoutExpired:
        return -1, "命令超时: " + " ".join(str(c) for c in cmd)
    except FileNotFoundError:
        return -1, "找不到命令: " + str(cmd[0])
    except Exception as e:
        return -1, f"执行出错: {e}"
    out = (p.stdout or b"") + (p.stderr or b"")
    return p.returncode, _decode(out).strip()


# ------------------------------------------------------------ 网络状态读取 --
def get_connected_interfaces():
    """返回状态为"已连接"的网卡接口名列表（中英文系统通用）。"""
    _, out = run(["netsh", "interface", "show", "interface"], timeout=30)
    names, started = [], False
    for line in out.splitlines():
        s = line.strip()
        if re.match(r"^[-—–]{5,}$", s):  # 表头下的分隔线
            started = True
            continue
        if not started or not s:
            continue
        parts = [p for p in re.split(r"\s{2,}", s) if p]
        if len(parts) >= 4:
            state = parts[1]
            if state.startswith("已连接") or state.lower() == "connected":
                names.append(" ".join(parts[3:]))
    return names


def get_wlan_state():
    """返回 (当前SSID或None, 状态描述文本)。"""
    _, out = run(["netsh", "wlan", "show", "interfaces"], timeout=30)
    if not out:
        return None, "无法读取"
    m = re.search(r"^\s*SSID\s*:\s*(.+?)\s*$", out, re.M | re.I)
    ssid = m.group(1) if m else None
    st = re.search(r"^\s*(?:状态|State)\s*:\s*(.+?)\s*$", out, re.M)
    state = st.group(1) if st else "未知"
    if "没有无线接口" in out or "not running" in out or "无线自动配置服务" in out:
        state += "（无线服务未运行或网卡被禁用）"
    return ssid, state


def get_wlan_profiles():
    """已保存的 WiFi 配置文件(SSID)列表。"""
    _, out = run(["netsh", "wlan", "show", "profiles"], timeout=30)
    res = []
    for line in out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if ("配置文件" in k) or ("rofile" in k):
                v = v.strip()
                if v:
                    res.append(v)
    return res


def _ping_ok(host):
    _, out = run(["ping", "-n", "2", "-w", "2000", host], timeout=20)
    return "TTL=" in out.upper()


def net_status():
    """返回 (IP层连通, 域名解析正常)。"""
    ip_ok = _ping_ok("223.5.5.5")
    dns_ok = _ping_ok("www.baidu.com")
    return ip_ok, dns_ok


def proxy_enabled():
    _, out = run(["reg", "query",
                  r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                  "/v", "ProxyEnable"], timeout=20)
    return "0x1" in out


def dns_servers_brief():
    _, out = run(["ipconfig", "/all"], timeout=30)
    vals = []
    for line in out.splitlines():
        if "DNS" in line and ":" in line:
            v = line.split(":", 1)[1].strip()
            if v:
                vals.append(v)
    return " / ".join(vals[:4])


# ------------------------------------------------------------ 修复动作定义 --
def _service_state(name):
    _, out = run(["sc", "query", name], timeout=20)
    m = re.search(r"STATE\s*:\s*\d+\s+([A-Z]+)", out)
    return m.group(1) if m else None


def _wait_service(name, want, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _service_state(name) == want:
            return True
        time.sleep(0.8)
    return _service_state(name) == want


def a_flush_dns(log):
    log("执行: ipconfig /flushdns")
    c, o = run(["ipconfig", "/flushdns"], timeout=60)
    log(o, "dim")
    return c == 0


def a_proxy_reset(log):
    log("执行: netsh winhttp reset proxy + 关闭用户级 IE 代理")
    c1, o1 = run(["netsh", "winhttp", "reset", "proxy"], timeout=60)
    log(o1, "dim")
    key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    c2, _ = run(["reg", "add", key, "/v", "ProxyEnable",
                 "/t", "REG_DWORD", "/d", "0", "/f"], timeout=30)
    run(["reg", "delete", key, "/v", "AutoConfigURL", "/f"], timeout=30)  # 不存在则忽略
    return c1 == 0 and c2 == 0


def a_dns_auto(log):
    names = get_connected_interfaces()
    if not names:
        log("没有处于连接状态的网卡，跳过", "warn")
        return True
    ok = True
    for n in names:
        log(f"接口 [{n}] DNS 恢复自动获取(DHCP)")
        for cmd in (["netsh", "interface", "ip", "set", "dnsservers", f'name="{n}"', "source=dhcp"],
                    ["netsh", "interface", "ipv6", "set", "dnsservers", f'name="{n}"', "source=dhcp"]):
            c, o = run(cmd, timeout=60)
            if c != 0:
                log(o, "dim")
                ok = False
    run(["ipconfig", "/flushdns"], timeout=60)
    return ok


def a_release_renew(log):
    log("执行: ipconfig /release + /renew + /registerdns")
    c1, o1 = run(["ipconfig", "/release"], timeout=90)
    log(o1, "dim")
    c2, o2 = run(["ipconfig", "/renew"], timeout=120)
    log(o2, "dim")
    run(["ipconfig", "/registerdns"], timeout=60)
    return c1 == 0 and c2 == 0


def a_restart_dnscache(log):
    log("重启 DNS 客户端服务 (Dnscache)…")
    run(["sc", "stop", "Dnscache"], timeout=30)
    _wait_service("Dnscache", "STOPPED", 8)
    c, o = run(["sc", "start", "Dnscache"], timeout=30)
    if c != 0:
        log("该服务受系统保护，跳过（不影响其他修复）", "warn")
        return True
    ok = _wait_service("Dnscache", "RUNNING", 15)
    log("Dnscache 状态: " + str(_service_state("Dnscache")))
    return ok


def a_restart_wlansvc(log):
    log("重启 WLAN 自动配置服务 (WlanSvc)…（WiFi 会短暂断开并自动重连）")
    run(["sc", "stop", "WlanSvc"], timeout=30)
    _wait_service("WlanSvc", "STOPPED", 12)
    c, o = run(["sc", "start", "WlanSvc"], timeout=30)
    if c != 0:
        log(o, "err")
        return False
    ok = _wait_service("WlanSvc", "RUNNING", 15)
    log("WlanSvc 状态: " + str(_service_state("WlanSvc")))
    return ok


def get_physical_adapter_names():
    """通过 PowerShell 取物理网卡名；失败返回 None（调用方回退到排除法）。"""
    c, o = run(POWERSHELL + ["Get-NetAdapter -Physical | Select-Object -ExpandProperty Name"],
               timeout=90)
    if c == 0 and o.strip():
        return [l.strip() for l in o.splitlines() if l.strip()]
    return None


def a_adapter_cycle(log):
    """禁用再启用所有已连接的物理网卡（相当于拔插网线/重启无线开关）。"""
    names = [n for n in get_connected_interfaces() if not IFACE_EXCLUDE.search(n)]
    phys = get_physical_adapter_names()
    if phys is not None:
        names = [n for n in names if n in phys]  # 只插拔物理网卡，避开 ZeroTier/虚拟网卡
    if not names:
        log("没有可操作的已连接网卡", "warn")
        return True
    log("将重启网卡: " + ", ".join(names))
    for n in names:
        run(["netsh", "interface", "set", "interface", f'name="{n}"', "admin=disable"], timeout=30)
    time.sleep(3)
    for n in names:
        run(["netsh", "interface", "set", "interface", f'name="{n}"', "admin=enable"], timeout=30)
    # 等网卡重新连上
    for _ in range(10):
        cur = set(get_connected_interfaces())
        if cur & set(names):
            log("网卡已重新启用")
            return True
        time.sleep(2)
    log("网卡已启用，但连接恢复较慢，请稍候", "warn")
    return True


def a_power_save_off(log):
    log("禁用网卡节能（防止系统“允许计算机关闭此设备以节省电源”导致掉线）")
    c, o = run(POWERSHELL + [
        "Get-NetAdapter -Physical | Disable-NetAdapterPowerManagement "
        "-NoRestart -ErrorAction SilentlyContinue; Write-Output done"], timeout=120)
    return c == 0


def a_winsock_reset(log):
    log("执行: netsh winsock reset")
    c, o = run(["netsh", "winsock", "reset"], timeout=60)
    log(o, "dim")
    return c == 0


def a_tcpip_reset(log):
    tmp = os.environ.get("TEMP", APP_DIR)  # reset 日志写到临时目录，避免污染
    cmds = [
        ["netsh", "int", "ip", "reset"],
        ["netsh", "int", "ipv6", "reset"],
        ["arp", "-d", "*"],
        ["nbtstat", "-R"],
    ]
    ok = True
    for cmd in cmds:
        log("执行: " + " ".join(cmd))
        c, o = run(cmd, timeout=90, cwd=tmp)
        if o and len(o) < 300:
            log(o, "dim")
        if c != 0:
            ok = False
    return ok


def a_public_dns(log):
    names = get_connected_interfaces()
    if not names:
        log("没有处于连接状态的网卡，跳过", "warn")
        return True
    ok = True
    for n in names:
        log(f"接口 [{n}] 设置公共DNS: 223.5.5.5 / 119.29.29.29")
        c1, _ = run(["netsh", "interface", "ip", "set", "dnsservers", f'name="{n}"',
                     "source=static", "address=223.5.5.5", "validate=no"], timeout=60)
        run(["netsh", "interface", "ip", "add", "dns", f'name="{n}"',
             "address=119.29.29.29", "index=2", "validate=no"], timeout=60)
        if c1 != 0:
            ok = False
    run(["ipconfig", "/flushdns"], timeout=60)
    return ok


# 动作注册表：按此顺序执行
ACTIONS = [
    dict(id="flush_dns", title="刷新 DNS 缓存", desc="解决“连上了但网页打不开”",
         default=True, reboot=False, fn=a_flush_dns),
    dict(id="proxy_reset", title="重置系统代理", desc="清除 VPN/加速器残留的代理设置",
         default=True, reboot=False, fn=a_proxy_reset),
    dict(id="dns_auto", title="DNS 恢复自动获取", desc="清除手动指定的 DNS",
         default=True, reboot=False, fn=a_dns_auto),
    dict(id="release_renew", title="释放并续租 IP 地址", desc="解决 IP 冲突 / “无Internet”",
         default=True, reboot=False, fn=a_release_renew),
    dict(id="restart_dnscache", title="重启 DNS 客户端服务", desc="DNS 解析卡死时有效",
         default=True, reboot=False, fn=a_restart_dnscache),
    dict(id="restart_wlansvc", title="重启 WLAN 自动配置服务", desc="搜不到WiFi/无线开关卡死",
         default=True, reboot=False, fn=a_restart_wlansvc),
    dict(id="adapter_cycle", title="禁用再启用网卡", desc="相当于拔插网卡，会断网几秒",
         default=True, reboot=False, fn=a_adapter_cycle),
    dict(id="power_save_off", title="禁用网卡节能", desc="防止系统休眠网卡导致掉线",
         default=True, reboot=False, fn=a_power_save_off),
    dict(id="winsock_reset", title="重置 Winsock 目录", desc="LSP损坏类故障的特效药 ⚠️需重启生效",
         default=True, reboot=True, fn=a_winsock_reset),
    dict(id="tcpip_reset", title="重置 TCP/IPv4/IPv6 协议栈", desc="协议栈损坏的终极修复 ⚠️需重启生效",
         default=True, reboot=True, fn=a_tcpip_reset),
    dict(id="public_dns", title="强制使用公共 DNS", desc="223.5.5.5 / 119.29.29.29（运营商DNS异常时勾选）",
         default=False, reboot=False, fn=a_public_dns),
]


# ----------------------------------------------------------------------- GUI --
class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("800x680")
        root.option_add("*Font", ("Microsoft YaHei UI", 9))
        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        self.busy = False
        self.reboot_needed = False
        self.vars = {}

        # ---- 顶部状态栏 ----
        top = ttk.Frame(root, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        top.columnconfigure(4, weight=1)
        self.lbl_admin = ttk.Label(
            top, text="● 管理员权限" if is_admin() else "● 非管理员(功能受限)",
            foreground="#107c10" if is_admin() else "#d13438")
        self.lbl_ssid = ttk.Label(top, text="WiFi: 检测中…")
        self.lbl_net = ttk.Label(top, text="互联网: 检测中…")
        self.btn_check = ttk.Button(top, text="🔍 网络体检", command=self.run_check)
        self.lbl_admin.grid(row=0, column=0, padx=(0, 14))
        self.lbl_ssid.grid(row=0, column=1, padx=(0, 14))
        self.lbl_net.grid(row=0, column=2, padx=(0, 14))
        self.btn_check.grid(row=0, column=3)

        ttk.Label(root, text="提示：“手机能连、电脑连不上”的 WiFi，先在下方删掉那条 WiFi 记录再点一键修复",
                  foreground="#666").pack(fill="x", padx=12)

        # ---- 修复项 ----
        box = ttk.LabelFrame(root, text=" 修复项（勾选后执行，带 ⚠️ 的需要重启电脑才完全生效） ",
                             padding=(8, 4))
        box.pack(fill="x", padx=10, pady=(6, 2))
        for i, a in enumerate(ACTIONS):
            var = tk.BooleanVar(value=a["default"])
            self.vars[a["id"]] = var
            row, col = divmod(i, 2)
            item = ttk.Frame(box)
            item.grid(row=row, column=col, sticky="w", padx=6, pady=2)
            ttk.Checkbutton(item, text=a["title"], variable=var).pack(anchor="w")
            ttk.Label(item, text="  " + a["desc"], foreground="#888",
                      font=("Microsoft YaHei UI", 8)).pack(anchor="w")

        # ---- WiFi 记录管理 ----
        pbox = ttk.LabelFrame(root, text=" 已保存的 WiFi 记录（选中后可删除，删除后重连需重新输密码） ",
                              padding=(8, 4))
        pbox.pack(fill="x", padx=10, pady=4)
        self.tree = ttk.Treeview(pbox, show="tree", selectmode="extended",
                                 height=4, columns=("ssid"))
        self.tree.column("#0", width=520)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(pbox, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")
        pbtns = ttk.Frame(pbox)
        pbtns.pack(side="left", fill="y", padx=(10, 0))
        self.btn_refresh_profiles = ttk.Button(pbtns, text="🔄 刷新", width=14,
                                               command=self.refresh_profiles)
        self.btn_del_profile = ttk.Button(pbtns, text="🗑 删除所选", width=14,
                                          command=self.delete_selected_profiles)
        self.btn_refresh_profiles.pack(pady=2)
        self.btn_del_profile.pack(pady=2)

        # ---- 操作按钮 ----
        ops = ttk.Frame(root, padding=(10, 6))
        ops.pack(fill="x")
        self.btn_fix = tk.Button(ops, text="🚀 一键修复", command=self.do_repair,
                                 bg="#0078D7", fg="white", activebackground="#106EBE",
                                 activeforeground="white", relief="flat", cursor="hand2",
                                 padx=18, pady=4, font=("Microsoft YaHei UI", 10, "bold"))
        self.btn_fix.pack(side="left")
        ttk.Button(ops, text="💾 保存日志", command=self.save_log).pack(side="left", padx=8)
        ttk.Button(ops, text="🔁 重启电脑", command=self.reboot_pc).pack(side="left")
        self.prog = ttk.Progressbar(root, mode="indeterminate")
        self.prog.pack(fill="x", padx=10)

        # ---- 日志 ----
        lbox = ttk.LabelFrame(root, text=" 日志 ", padding=(4, 4))
        lbox.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.txt = tk.Text(lbox, wrap="none", state="disabled",
                           font=("Consolas", 9), bg="#fafafa")
        ysb = ttk.Scrollbar(lbox, orient="vertical", command=self.txt.yview)
        xsb = ttk.Scrollbar(lbox, orient="horizontal", command=self.txt.xview)
        self.txt.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.txt.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        lbox.rowconfigure(0, weight=1)
        lbox.columnconfigure(0, weight=1)
        for tag, color in (("ok", "#107c10"), ("err", "#d13438"),
                           ("warn", "#9a5b13"), ("dim", "#777")):
            self.txt.tag_config(tag, foreground=color)

        self.log(f"欢迎使用 {APP_TITLE} v{APP_VERSION}，日志也会保存到: {LOG_FILE}")
        self.log("建议顺序：先【网络体检】→ 删除连不上的 WiFi 记录 → 【一键修复】→ 按提示重启")
        self.refresh_profiles()
        root.after(500, self.run_check)

    # ---- 线程安全的日志 ----
    def _post(self, fn):
        """把回调投递到主线程执行；窗口已关闭时静默忽略。"""
        try:
            self.root.after(0, fn)
        except RuntimeError:
            pass

    def log(self, msg="", tag=None):
        self._post(lambda: self._append(msg, tag))

    def _append(self, msg, tag):
        self.txt.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        for ln in (msg.splitlines() or [""]):
            prefix = "" if tag == "dim" else f"[{ts}] "
            self.txt.insert("end", prefix + ln + "\n", tag)
        # 防止日志无限增长
        if int(self.txt.index("end-1c").split(".")[0]) > 3000:
            self.txt.delete("1.0", "500.0")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_check, self.btn_refresh_profiles, self.btn_del_profile):
            b.configure(state=state)
        self.btn_fix.configure(state=state)
        if busy:
            self.prog.start(12)
        else:
            self.prog.stop()

    # ---- WiFi 记录 ----
    def refresh_profiles(self):
        self.tree.delete(*self.tree.get_children())
        profiles = get_wlan_profiles()
        if not profiles:
            self.tree.insert("", "end", iid="__none__", text="（无已保存的 WiFi 记录）",
                             tags=("dim",))
            self.tree.tag_configure("dim", foreground="#999")
        else:
            for p in profiles:
                self.tree.insert("", "end", iid=p, text=p)

    def delete_selected_profiles(self):
        sel = [s for s in self.tree.selection() if s != "__none__"]
        if not sel:
            messagebox.showinfo(APP_TITLE, "请先在列表中选中要删除的 WiFi 记录", parent=self.root)
            return
        if not messagebox.askyesno(
                APP_TITLE,
                "确定删除这 %d 条 WiFi 记录吗？\n%s\n删除后连接这些 WiFi 需要重新输入密码。"
                % (len(sel), "\n".join(" · " + s for s in sel)),
                parent=self.root):
            return

        def worker():
            for name in sel:
                self.log(f"删除 WiFi 记录: {name}")
                c, o = run(["netsh", "wlan", "delete", "profile", f'name="{name}"'], timeout=30)
                if c != 0:
                    self.log(o, "err")
            self._post(self.refresh_profiles)
            self.log("删除完成。现在请重新连接该 WiFi 并输入密码", "ok")
        threading.Thread(target=worker, daemon=True).start()

    # ---- 网络体检 ----
    def run_check(self):
        if self.busy:
            return
        self.set_busy(True)

        def worker():
            ssid, state = get_wlan_state()
            ip_ok, dns_ok = net_status()
            proxy = proxy_enabled()
            dns_srv = dns_servers_brief()
            if ip_ok and dns_ok:
                net_txt, color = "互联网: 正常 ✅", "#107c10"
            elif ip_ok:
                net_txt, color = "互联网: DNS解析异常 ⚠️", "#9a5b13"
            else:
                net_txt, color = "互联网: 不通 ❌", "#d13438"
            self._post(lambda: self._apply_check(
                ssid, state, net_txt, color))
            self.log("──── 网络体检 ────")
            self.log(f"无线网卡: {state}")
            self.log(f"当前WiFi: {ssid or '(未连接)' }")
            self.log(f"外网连通(223.5.5.5): {'正常' if ip_ok else '不通'}")
            self.log(f"域名解析(www.baidu.com): {'正常' if dns_ok else '失败'}")
            self.log("系统代理: " + ("已开启！很可能就是它导致上不了网 → 勾选“重置系统代理”" if proxy else "关闭"))
            self.log("当前DNS服务器: " + (dns_srv or "未读取到"))
            if not ip_ok:
                self.log("建议：删除连不上的WiFi记录 → 一键修复（含释放IP/重启网卡）→ 重启电脑", "warn")
            elif not dns_ok:
                self.log("建议：勾选“刷新DNS缓存”/“DNS恢复自动获取”，无效则试“强制使用公共DNS”", "warn")
            self._post(lambda: self.set_busy(False))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_check(self, ssid, state, net_txt, color):
        self.lbl_ssid.configure(text=f"WiFi: {ssid or '(未连接)'}")
        self.lbl_net.configure(text=net_txt, foreground=color)

    # ---- 一键修复 ----
    def do_repair(self):
        if self.busy:
            return
        selected = [a for a in ACTIONS if self.vars[a["id"]].get()]
        if not selected:
            messagebox.showinfo(APP_TITLE, "请至少勾选一个修复项", parent=self.root)
            return
        self.set_busy(True)
        self.reboot_needed = False
        self.log("════════ 开始一键修复（共 %d 项）════════" % len(selected))

        def worker():
            ok_cnt = 0
            for a in selected:
                self.log(f"\n===== {a['title']} =====")
                try:
                    ok = a["fn"](self.log)
                except Exception as e:
                    ok = False
                    self.log(f"执行异常: {e}", "err")
                self.log(("✔ " if ok else "✘ ") + a["title"] + (" 完成" if ok else " 失败"),
                         "ok" if ok else "err")
                if ok:
                    ok_cnt += 1
                    if a["reboot"]:
                        self.reboot_needed = True
            self._post(lambda: self._repair_done(ok_cnt, len(selected)))
        threading.Thread(target=worker, daemon=True).start()

    def _repair_done(self, ok_cnt, total):
        self.set_busy(False)
        self.log(f"\n════════ 修复结束：{ok_cnt}/{total} 项成功 ════════", "ok")
        self.log("正在重新检测网络…")
        self.run_check()
        if self.reboot_needed:
            if messagebox.askyesno(
                    APP_TITLE,
                    "修复已执行完毕。\n\n其中 Winsock / TCP-IP 重置需要【重启电脑】才能生效，现在重启吗？\n"
                    "（也可以稍后手动重启）", parent=self.root):
                self.reboot_pc(confirm=False)

    # ---- 其他 ----
    def save_log(self):
        try:
            content = self.txt.get("1.0", "end-1c")
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo(APP_TITLE, "日志已保存到:\n" + LOG_FILE, parent=self.root)
        except Exception as e:
            messagebox.showerror(APP_TITLE, "保存失败: " + str(e), parent=self.root)

    def reboot_pc(self, confirm=True):
        if confirm and not messagebox.askyesno(
                APP_TITLE, "确定要立即重启电脑吗？\n请先保存其他窗口的工作。", parent=self.root):
            return
        run(["shutdown", "/r", "/t", "10", "/c", "网络修复:即将重启电脑"], timeout=30)
        self.log("电脑将在 10 秒后重启（可运行 shutdown /a 撤销）", "warn")


def main():
    if os.name != "nt":
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, "此工具仅支持 Windows")
        return

    # 高分屏下界面更清晰
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 强制管理员运行
    if not is_admin():
        if relaunch_elevated():
            return  # 已以管理员身份重新启动新进程
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            APP_TITLE,
            "需要管理员权限才能修复网络。\n请允许 UAC 弹窗，或右键选择“以管理员身份运行”。")
        return

    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
