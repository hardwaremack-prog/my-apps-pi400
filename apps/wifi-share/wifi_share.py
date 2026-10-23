#!/usr/bin/env python3
"""
WiFi Share for Raspberry Pi
Shares the Pi's Wi-Fi internet to its Ethernet port (NetworkManager "shared"
mode) and shows the devices connected on the Ethernet side.

Works on Raspberry Pi OS Bookworm or newer (uses NetworkManager / nmcli).
Run as your normal desktop user - no sudo needed.
"""

import ipaddress
import os
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox, ttk

CON_NAME = "WiFi-Share"       # the NetworkManager connection this app manages
REFRESH_MS = 15000


# ------------------------------------------------------------------ helpers --
def run(args, timeout=20):
    """Run a command, return (ok, stdout, stderr)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout, p.stderr.strip()
    except FileNotFoundError:
        return False, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return False, "", f"{' '.join(args)} timed out"


def nm_split(line):
    """Split an nmcli -t line on ':' while honoring '\\:' escapes."""
    parts = re.split(r"(?<!\\):", line)
    return [p.replace("\\:", ":") for p in parts]


def list_devices():
    """Return list of (device, type, state) from NetworkManager."""
    ok, out, _ = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"])
    devs = []
    if ok:
        for line in out.splitlines():
            f = nm_split(line)
            if len(f) >= 3 and f[1] in ("wifi", "ethernet"):
                devs.append((f[0], f[1], f[2]))
    return devs


def wifi_info(dev):
    """Return a human description of what a Wi-Fi device is connected to."""
    ok, out, _ = run(["nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
                      "device", "show", dev])
    if not ok:
        return "unknown"
    info = dict(nm_split(l)[:2] for l in out.splitlines() if ":" in l)
    conn = info.get("GENERAL.CONNECTION", "")
    state = info.get("GENERAL.STATE", "")
    if conn and conn != "--" and state.startswith("100"):
        return f"connected to “{conn}”"
    return "not connected"


def sharing_active():
    """Return the device WiFi-Share is active on, or None."""
    ok, out, _ = run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
    if ok:
        for line in out.splitlines():
            f = nm_split(line)
            if len(f) >= 2 and f[0] == CON_NAME:
                return f[1]
    return None


def connection_exists():
    ok, out, _ = run(["nmcli", "-t", "-f", "NAME", "connection", "show"])
    return ok and CON_NAME in out.splitlines()


def get_autoconnect():
    ok, out, _ = run(["nmcli", "-g", "connection.autoconnect", "connection", "show", CON_NAME])
    return ok and out.strip() == "yes"


def start_sharing(eth_dev, at_boot):
    auto = "yes" if at_boot else "no"
    if connection_exists():
        ok, _, err = run(["nmcli", "connection", "modify", CON_NAME,
                          "connection.interface-name", eth_dev,
                          "ipv4.method", "shared", "ipv6.method", "ignore",
                          "connection.autoconnect", auto,
                          "connection.autoconnect-priority", "100"])
    else:
        ok, _, err = run(["nmcli", "connection", "add", "type", "ethernet",
                          "ifname", eth_dev, "con-name", CON_NAME,
                          "ipv4.method", "shared", "ipv6.method", "ignore",
                          "connection.autoconnect", auto,
                          "connection.autoconnect-priority", "100"])
    if not ok:
        raise RuntimeError(err or "Couldn't create the sharing connection.")
    ok, _, err = run(["nmcli", "connection", "up", CON_NAME], timeout=45)
    if not ok:
        raise RuntimeError(err or "Couldn't start sharing.")


def stop_sharing(eth_dev):
    ok, _, err = run(["nmcli", "connection", "down", CON_NAME], timeout=30)
    # stop it coming back by itself, then let the normal wired profile reconnect
    run(["nmcli", "connection", "modify", CON_NAME, "connection.autoconnect", "no"])
    if eth_dev:
        run(["nmcli", "device", "connect", eth_dev], timeout=10)
    if not ok and "not an active" not in err:
        raise RuntimeError(err or "Couldn't stop sharing.")


def set_autoconnect(at_boot):
    if connection_exists():
        run(["nmcli", "connection", "modify", CON_NAME,
             "connection.autoconnect", "yes" if at_boot else "no"])


def ipv4_of(dev):
    """Return (ip, prefix) of a device, or None."""
    ok, out, _ = run(["ip", "-4", "-o", "addr", "show", "dev", dev])
    if ok:
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out)
        if m:
            return m.group(1), int(m.group(2))
    return None


def ping(ip):
    ok, _, _ = run(["ping", "-c", "1", "-W", "1", "-n", "-q", ip], timeout=3)
    return ip if ok else None


def ping_sweep(own_ip, prefix):
    """Ping every address in the subnet (max /24) so the neighbor table fills."""
    net = ipaddress.ip_network(f"{own_ip}/{max(prefix, 24)}", strict=False)
    hosts = [str(h) for h in net.hosts() if str(h) != own_ip]
    with ThreadPoolExecutor(max_workers=64) as pool:
        return {ip for ip in pool.map(ping, hosts) if ip}


def neighbors(dev):
    """Return {mac: (ip, state)} from the kernel neighbor table."""
    ok, out, _ = run(["ip", "-4", "neigh", "show", "dev", dev])
    result = {}
    if ok:
        for line in out.splitlines():
            m = re.match(r"(\S+) lladdr (\S+) (\S+)", line)
            if m and m.group(3) != "FAILED":
                result[m.group(2).lower()] = (m.group(1), m.group(3))
    return result


def lease_names(dev):
    """Hostnames devices gave NetworkManager's DHCP server, {mac: (ip, name)}."""
    names = {}
    for path in (f"/var/lib/NetworkManager/dnsmasq-{dev}.leases",
                 "/var/lib/misc/dnsmasq.leases"):
        try:
            with open(path) as f:
                for line in f:
                    p = line.split()
                    if len(p) >= 4:
                        names[p[1].lower()] = (p[2], "" if p[3] == "*" else p[3])
        except OSError:
            pass
    return names


def ip_sort_key(ip):
    try:
        return tuple(int(x) for x in ip.split("."))
    except ValueError:
        return (999,)


# ----------------------------------------------------------------------- UI --
class App:
    GREEN = "#16803c"
    GRAY = "#787878"

    def __init__(self, root):
        self.root = root
        self.devices = {}      # mac -> dict
        self.scanning = False
        self.busy = False

        root.title("WiFi Share")
        root.geometry("820x540")
        root.minsize(680, 400)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Big.TButton", font=("TkDefaultFont", 11, "bold"), padding=10)
        style.configure("Treeview", rowheight=24)

        top = ttk.Frame(root, padding=(14, 12))
        top.pack(fill="x")

        ttk.Label(top, text="Share from (Wi-Fi):").grid(row=0, column=0, sticky="w", pady=3)
        self.from_var = tk.StringVar()
        self.cb_from = ttk.Combobox(top, textvariable=self.from_var, state="readonly", width=14)
        self.cb_from.grid(row=0, column=1, sticky="w", padx=6)
        self.wifi_lbl = ttk.Label(top, text="", foreground=self.GRAY)
        self.wifi_lbl.grid(row=0, column=2, sticky="w")

        ttk.Label(top, text="To (Ethernet):").grid(row=1, column=0, sticky="w", pady=3)
        self.to_var = tk.StringVar()
        self.cb_to = ttk.Combobox(top, textvariable=self.to_var, state="readonly", width=14)
        self.cb_to.grid(row=1, column=1, sticky="w", padx=6)

        self.boot_var = tk.BooleanVar(value=False)
        self.chk_boot = ttk.Checkbutton(top, text="Start sharing automatically at boot",
                                        variable=self.boot_var, command=self.on_boot_toggle)
        self.chk_boot.grid(row=1, column=2, sticky="w")

        self.btn = ttk.Button(top, text="Start sharing", style="Big.TButton",
                              command=self.on_toggle, width=15)
        self.btn.grid(row=0, column=3, rowspan=2, padx=(18, 10), sticky="ns")

        self.status = tk.Label(top, text="Checking…", font=("TkDefaultFont", 11, "bold"),
                               fg=self.GRAY, justify="left", anchor="w")
        self.status.grid(row=0, column=4, rowspan=2, sticky="w")
        top.columnconfigure(4, weight=1)

        self.dev_title = ttk.Label(root, text="Connected devices",
                                   font=("TkDefaultFont", 11, "bold"), padding=(14, 4))
        self.dev_title.pack(anchor="w")

        mid = ttk.Frame(root, padding=(14, 0))
        mid.pack(fill="both", expand=True)
        cols = ("status", "name", "ip", "mac", "seen")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, title, w in (("status", "Status", 80), ("name", "Name", 220),
                            ("ip", "IP address", 120), ("mac", "MAC address", 160),
                            ("seen", "Last seen", 160)):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("on", foreground=self.GREEN)
        self.tree.tag_configure("off", foreground=self.GRAY)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_copy)

        bottom = ttk.Frame(root, padding=(14, 10))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Refresh now", command=self.refresh).pack(side="left")
        self.auto_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="Auto-refresh every 15s",
                        variable=self.auto_var).pack(side="left", padx=12)
        self.scan_lbl = ttk.Label(bottom, text="Double-click a device to copy its IP",
                                  foreground=self.GRAY)
        self.scan_lbl.pack(side="left", padx=8)

        self.cb_to.bind("<<ComboboxSelected>>", lambda e: self.on_eth_changed())
        self.cb_from.bind("<<ComboboxSelected>>", lambda e: self.update_wifi_label())

        if not shutil.which("nmcli"):
            messagebox.showerror(
                "WiFi Share",
                "NetworkManager (nmcli) wasn't found.\n\n"
                "On older Raspberry Pi OS, turn it on with:\n"
                "sudo raspi-config → Advanced Options → Network Config → NetworkManager\n"
                "then reboot and open WiFi Share again.")
            root.destroy()
            return

        self.load_adapters()
        self.update_sharing_ui()
        self.refresh()
        self.root.after(REFRESH_MS, self.tick)

    # ---------------------------------------------------------------- setup --
    def load_adapters(self):
        devs = list_devices()
        wifis = [d for d, t, _ in devs if t == "wifi"]
        eths = [d for d, t, _ in devs if t == "ethernet"]
        self.cb_from["values"] = wifis
        self.cb_to["values"] = eths
        active = sharing_active()
        if wifis:
            self.from_var.set("wlan0" if "wlan0" in wifis else wifis[0])
        if active:
            self.to_var.set(active)
        elif eths:
            self.to_var.set("eth0" if "eth0" in eths else eths[0])
        self.boot_var.set(connection_exists() and get_autoconnect())
        self.update_wifi_label()

    def update_wifi_label(self):
        dev = self.from_var.get()
        self.wifi_lbl.config(text=wifi_info(dev) if dev else "no Wi-Fi adapter found")

    def update_sharing_ui(self):
        active = sharing_active()
        if active:
            self.btn.config(text="Stop sharing")
            self.status.config(text=f"Sharing ON\n{self.from_var.get()} → {active}", fg=self.GREEN)
            self.cb_from.config(state="disabled")
            self.cb_to.config(state="disabled")
        else:
            self.btn.config(text="Start sharing")
            self.status.config(text="Sharing OFF", fg=self.GRAY)
            self.cb_from.config(state="readonly")
            self.cb_to.config(state="readonly")
        self.update_wifi_label()

    # --------------------------------------------------------------- events --
    def on_toggle(self):
        if self.busy:
            return
        eth = self.to_var.get()
        if not eth:
            messagebox.showwarning("WiFi Share", "No Ethernet port found.")
            return
        active = sharing_active()
        self.busy = True
        self.btn.config(state="disabled")
        self.status.config(text="Stopping…" if active else "Starting…", fg=self.GRAY)

        def work():
            err = None
            try:
                if active:
                    stop_sharing(active)
                else:
                    start_sharing(eth, self.boot_var.get())
                    time.sleep(1.5)
            except Exception as e:  # noqa: BLE001
                err = str(e)
            self.root.after(0, lambda: self.after_toggle(err))

        threading.Thread(target=work, daemon=True).start()

    def after_toggle(self, err):
        self.busy = False
        self.btn.config(state="normal")
        if err:
            messagebox.showwarning("WiFi Share", f"Couldn't change sharing:\n\n{err}")
        self.update_sharing_ui()
        self.refresh()

    def on_boot_toggle(self):
        set_autoconnect(self.boot_var.get())

    def on_eth_changed(self):
        self.devices = {}
        self.refresh()

    def on_copy(self, _event):
        sel = self.tree.selection()
        if sel:
            ip = self.tree.item(sel[0], "values")[2]
            self.root.clipboard_clear()
            self.root.clipboard_append(ip)
            self.scan_lbl.config(text=f"Copied {ip} to the clipboard.")

    def tick(self):
        if self.auto_var.get():
            self.update_sharing_ui()
            self.refresh()
        self.root.after(REFRESH_MS, self.tick)

    # ------------------------------------------------------------- scanning --
    def refresh(self):
        if self.scanning:
            return
        eth = self.to_var.get()
        if not eth:
            return
        addr = ipv4_of(eth)
        if not addr:
            for d in self.devices.values():
                d["online"] = False
            self.show_devices()
            self.scan_lbl.config(text=f"{eth} has no IP yet – plug in a cable and start sharing.")
            return
        self.scanning = True
        self.scan_lbl.config(text=f"Scanning {addr[0]}/{max(addr[1], 24)} …")
        self.root.config(cursor="watch")

        def work():
            alive = ping_sweep(*addr)
            time.sleep(0.3)
            neigh = neighbors(eth)
            names = lease_names(eth)
            self.root.after(0, lambda: self.apply_scan(eth, addr, alive, neigh, names))

        threading.Thread(target=work, daemon=True).start()

    def apply_scan(self, eth, addr, alive, neigh, names):
        now = time.time()
        net = ipaddress.ip_network(f"{addr[0]}/{max(addr[1], 24)}", strict=False)
        seen = set()
        for mac, (ip, state) in neigh.items():
            try:
                if ipaddress.ip_address(ip) not in net or ip == addr[0]:
                    continue
            except ValueError:
                continue
            online = ip in alive or state in ("REACHABLE", "DELAY", "PROBE")
            d = self.devices.setdefault(mac, {"mac": mac, "ip": ip, "name": "",
                                              "online": online, "seen": now})
            d["ip"] = ip
            d["online"] = online
            if online:
                d["seen"] = now
            if mac in names and names[mac][1]:
                d["name"] = names[mac][1]
            seen.add(mac)
        for mac, d in self.devices.items():
            if mac not in seen:
                d["online"] = False
        self.show_devices()
        self.scanning = False
        self.root.config(cursor="")
        self.scan_lbl.config(
            text=f"Last scan {time.strftime('%-I:%M:%S %p')} on {eth} ({addr[0]})  ·  "
                 "double-click a device to copy its IP")

    def show_devices(self):
        self.tree.delete(*self.tree.get_children())
        ordered = sorted(self.devices.values(),
                         key=lambda d: (not d["online"], ip_sort_key(d["ip"])))
        online = 0
        for d in ordered:
            online += d["online"]
            self.tree.insert("", "end", tags=("on" if d["online"] else "off",), values=(
                "Online" if d["online"] else "Offline",
                d["name"] or "(unknown device)",
                d["ip"],
                d["mac"].upper(),
                time.strftime("%a %-I:%M:%S %p", time.localtime(d["seen"])),
            ))
        self.dev_title.config(text=f"Connected devices  ({online} online)")


def main():
    if os.geteuid() == 0:
        print("Tip: you don't need sudo - run WiFi Share as your normal user.")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
