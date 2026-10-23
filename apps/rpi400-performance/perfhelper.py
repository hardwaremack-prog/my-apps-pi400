#!/usr/bin/env python3
"""
RPi400 Performance - the part that changes system settings (runs as root).

Called by rpi400perf.py through sudo (or the desktop password box):
    perfhelper.py <action> [value]
Every change is written down in /etc/rpi400-performance/state.json so
"Put everything back" can undo it. Prints one line of JSON: {"ok":..,"msg":..}
"""
import json, os, re, shutil, subprocess, sys, time

STATE_DIR = "/etc/rpi400-performance"
STATE = os.path.join(STATE_DIR, "state.json")
GOV_UNIT = "/etc/systemd/system/rpi400-performance.service"
SYSCTL = "/etc/sysctl.d/90-rpi400-performance.conf"
JOURNAL = "/etc/systemd/journald.conf.d/90-rpi400-performance.conf"
TMP_UNIT = "/etc/systemd/system/tmp.mount"
FSTAB = "/etc/fstab"
BLOCK_START = "# >>> RPi400 Performance overclock (delete these lines to undo)"
BLOCK_END = "# <<< RPi400 Performance overclock"
SERVICES_OK = {   # things nobody misses on most Pis; Bluetooth is separate (keyboards, headsets)
    "cups": "Printing", "cups-browsed": "Printer finder", "ModemManager": "Cell modems",
    "triggerhappy": "Hotkey daemon", "colord": "Color profiles",
    "bluetooth": "Bluetooth", "hciuart": "Bluetooth chip",
}
OVERCLOCK = {
    "stock": None,
    "2000": {"arm_freq": 2000, "over_voltage": 6, "gpu_freq": 600},
    "2147": {"arm_freq": 2147, "over_voltage": 8, "gpu_freq": 750},
}


def out(ok, msg, **kw):
    print(json.dumps(dict(ok=ok, msg=msg, **kw)))
    sys.exit(0)


def sh(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa
        return 1, str(e)


def load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=1)
    os.replace(tmp, STATE)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def remove(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def config_txt():
    for p in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.isfile(p):
            return p
    return None


def model():
    try:
        return open("/proc/device-tree/model").read().strip("\x00 \n")
    except OSError:
        return ""


def throttled():
    rc, o = sh(["vcgencmd", "get_throttled"], 5)
    m = re.search(r"0x([0-9a-fA-F]+)", o)
    return int(m.group(1), 16) if m else 0


def zram_on():
    try:
        return "zram" in open("/proc/swaps").read()
    except OSError:
        return False


# ------------------------------------------------------------------ actions
def governor(value):
    if value not in ("performance", "ondemand", "schedutil"):
        out(False, "Unknown setting.")
    for f in sorted(os.listdir("/sys/devices/system/cpu/cpufreq")):
        p = f"/sys/devices/system/cpu/cpufreq/{f}/scaling_governor"
        if os.path.exists(p):
            try:
                open(p, "w").write(value)
            except OSError as e:
                out(False, f"Couldn't set the CPU mode: {e}")
    if value == "performance":
        write(GOV_UNIT, """[Unit]
Description=RPi400 Performance: keep the CPU at full speed
After=multi-user.target raspi-config.service

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do echo performance > "$g"; done'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
""")
        sh(["systemctl", "daemon-reload"])
        sh(["systemctl", "enable", "rpi400-performance.service"])
        # Raspberry Pi OS sets "ondemand" at boot with raspi-config's service; ours runs after it
        out(True, "CPU set to full speed all the time. Stays on after a restart.")
    sh(["systemctl", "disable", "rpi400-performance.service"])
    remove(GOV_UNIT)
    sh(["systemctl", "daemon-reload"])
    out(True, f"CPU back to normal ({value}): it speeds up only when busy.")


def overclock(value):
    if value not in OVERCLOCK:
        out(False, "Unknown overclock setting.")
    cfg = config_txt()
    if not cfg:
        out(False, "Couldn't find config.txt in /boot.")
    m = model()
    if value != "stock" and not re.search(r"Raspberry Pi (4|400)", m):
        out(False, f"Overclock presets are for the Pi 400 and Pi 4. This is: {m or 'unknown'}.")
    t = throttled()
    if value != "stock" and t & 0x10001:
        out(False, "Not safe to overclock: the power supply has been too weak (under-voltage). "
                   "Use the official 5.1 V 3 A Pi supply first, restart, and try again.")
    text = open(cfg).read()
    backup = cfg + ".before-rpi400-performance"
    if not os.path.exists(backup):
        shutil.copy2(cfg, backup)
    text = re.sub(r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", "\n", text, flags=re.S)
    text = text.rstrip("\n") + "\n"
    if OVERCLOCK[value]:
        lines = [BLOCK_START, "[all]"] + [f"{k}={v}" for k, v in OVERCLOCK[value].items()] + [BLOCK_END]
        text += "\n" + "\n".join(lines) + "\n"
    write(cfg, text)
    os.sync()
    st = load()
    st["overclock"] = value
    save(st)
    if value == "stock":
        out(True, "Overclock removed. Restart the Pi to go back to the normal 1.8 GHz.", reboot=True)
    out(True, f"Overclock set to {int(value) / 1000:.2f} GHz. Restart the Pi to use it.", reboot=True)


def zram(value):
    st = load()
    if value == "on":
        if zram_on():
            out(True, "Compressed RAM (zram) is already on.")
        if not shutil.which("zramswap"):
            rc, o = sh(["apt-get", "install", "-y", "zram-tools"], 600)
            if rc != 0 or not shutil.which("zramswap"):
                out(False, "Couldn't install zram-tools (needs the internet once).")
            st["zram_installed"] = True
        write("/etc/default/zramswap", "# set by RPi400 Performance\nALGO=zstd\nPERCENT=50\nPRIORITY=100\n")
        sh(["systemctl", "enable", "zramswap.service"])
        sh(["systemctl", "restart", "zramswap.service"])
        st["zram"] = True
        save(st)
        apply_sysctl()
        out(True, "Compressed RAM (zram) is on: the Pi can hold about 1.5x more before it slows down.")
    if st.get("zram"):
        sh(["systemctl", "disable", "--now", "zramswap.service"])
        st["zram"] = False
        save(st)
        apply_sysctl()
        out(True, "Compressed RAM (zram) turned off.")
    out(True, "Compressed RAM was set up by the system, so I left it alone.")


def apply_sysctl():
    if not os.path.exists(SYSCTL):
        return
    swappiness = 100 if zram_on() else 10
    write(SYSCTL, f"""# RPi400 Performance - memory and SD card tuning
# Use compressed RAM before the slow SD card, keep folder info in memory longer,
# and write to the SD card in small, steady batches instead of big stalls.
vm.swappiness={swappiness}
vm.vfs_cache_pressure=50
vm.dirty_background_ratio=5
vm.dirty_ratio=10
""")
    sh(["sysctl", "-p", SYSCTL])


def sysctl(value):
    if value == "on":
        write(SYSCTL, "")
        apply_sysctl()
        out(True, "Memory and SD card tuning is on.")
    remove(SYSCTL)
    for k, v in (("vm.swappiness", "60"), ("vm.vfs_cache_pressure", "100"),
                 ("vm.dirty_background_ratio", "10"), ("vm.dirty_ratio", "20")):
        sh(["sysctl", "-w", f"{k}={v}"])
    out(True, "Memory and SD card tuning back to normal.")


def tmpram(value):
    if value == "on":
        rc, fs = sh(["findmnt", "-no", "FSTYPE", "/tmp"])
        if fs.strip() == "tmpfs":
            out(True, "Temporary files already live in RAM.")
        src = next((p for p in ("/usr/share/systemd/tmp.mount", "/lib/systemd/system/tmp.mount",
                                "/usr/lib/systemd/system/tmp.mount") if os.path.exists(p)), None)
        write(TMP_UNIT, """[Unit]
Description=Temporary files in RAM (RPi400 Performance)
ConditionPathIsSymbolicLink=!/tmp
DefaultDependencies=no
Conflicts=umount.target
Before=local-fs.target umount.target
After=swap.target

[Mount]
What=tmpfs
Where=/tmp
Type=tmpfs
Options=mode=1777,strictatime,nosuid,nodev,size=512M

[Install]
WantedBy=local-fs.target
""")
        sh(["systemctl", "daemon-reload"])
        sh(["systemctl", "enable", "tmp.mount"])
        st = load(); st["tmpram"] = True; save(st)
        # not mounted now: the desktop keeps live files in /tmp
        out(True, "Temporary files will live in RAM (up to 512 MB) after a restart: fewer SD card writes.",
            reboot=True)
    st = load()
    if st.get("tmpram"):
        sh(["systemctl", "disable", "tmp.mount"])
        remove(TMP_UNIT)
        sh(["systemctl", "daemon-reload"])
        st["tmpram"] = False; save(st)
        out(True, "Temporary files go back to the SD card after a restart.", reboot=True)
    out(True, "Temporary files in RAM were set up by the system, so I left them alone.")


def journal(value):
    if value == "on":
        write(JOURNAL, "[Journal]\n# RPi400 Performance: keep logs small on the SD card\nSystemMaxUse=64M\nRuntimeMaxUse=32M\n")
        sh(["systemctl", "restart", "systemd-journald"])
        sh(["journalctl", "--vacuum-size=64M"])
        out(True, "System logs capped at 64 MB: less SD card wear, more free space.")
    remove(JOURNAL)
    sh(["systemctl", "restart", "systemd-journald"])
    out(True, "System log size back to normal.")


def trim(value):
    if value == "on":
        sh(["systemctl", "enable", "--now", "fstrim.timer"])
        rc, o = sh(["fstrim", "-v", "/"], 300)
        extra = (" Just now: " + o.split(":", 1)[-1].strip() + ".") if rc == 0 and "trimmed" in o else ""
        out(True, "Weekly SD card clean-up (TRIM) is on, keeping writes fast." + extra)
    sh(["systemctl", "disable", "--now", "fstrim.timer"])
    out(True, "Weekly SD card clean-up turned off.")


def noatime(value):
    rc, opts = sh(["findmnt", "-no", "OPTIONS", "/"])
    if value == "on" and "noatime" in opts:
        out(True, "The SD card already skips 'last opened' writes (noatime).")
    fstab = open(FSTAB).read()
    lines = fstab.splitlines()
    changed = False
    for i, line in enumerate(lines):
        f = line.split()
        if len(f) >= 4 and not line.lstrip().startswith("#") and f[1] == "/":
            o = [x for x in f[3].split(",") if x not in ("relatime", "noatime", "atime", "strictatime")]
            if value == "on":
                o.append("noatime")
            f[3] = ",".join(x for x in o if x) or "defaults"
            lines[i] = "\t".join(f)
            changed = True
    if not changed:
        out(False, "Couldn't find the main drive in /etc/fstab, so nothing was changed.")
    def errors():
        rc, o = sh(["findmnt", "--verify", "--tab-file", FSTAB])
        m = re.search(r"(\d+) errors?", o)
        return int(m.group(1)) if m else 0
    before = errors()
    if not os.path.exists(FSTAB + ".before-rpi400-performance"):
        shutil.copy2(FSTAB, FSTAB + ".before-rpi400-performance")
    keep = fstab
    write(FSTAB, "\n".join(lines) + "\n")
    if errors() > before:   # our edit made it worse: put it back exactly
        write(FSTAB, keep)
        out(False, "That fstab change didn't check out, so I put the old one back.")
    sh(["mount", "-o", "remount," + ("noatime" if value == "on" else "relatime"), "/"])
    st = load(); st["noatime"] = value == "on"; save(st)
    out(True, "The SD card no longer writes a 'last opened' time on every file read." if value == "on"
        else "Back to the normal 'last opened' setting.")


def service(value):
    name, _, how = value.partition(":")
    if name not in SERVICES_OK or how not in ("off", "on"):
        out(False, "That service isn't on the list.")
    st = load()
    off = set(st.get("services_off", []))
    if how == "off":
        sh(["systemctl", "disable", "--now", name + ".service"])
        off.add(name)
        msg = f"{SERVICES_OK[name]} ({name}) turned off. It won't start with the Pi any more."
    else:
        sh(["systemctl", "enable", "--now", name + ".service"])
        off.discard(name)
        msg = f"{SERVICES_OK[name]} ({name}) turned back on."
    st["services_off"] = sorted(off)
    save(st)
    out(True, msg)


def reboot(_):
    subprocess.Popen(["systemctl", "reboot"])
    out(True, "Restarting the Pi ...")


def undo_all(_):
    st = load()
    msgs = []
    for name in st.get("services_off", []):
        sh(["systemctl", "enable", "--now", name + ".service"])
    if os.path.exists(GOV_UNIT):
        sh(["systemctl", "disable", "rpi400-performance.service"]); remove(GOV_UNIT)
    for f in os.listdir("/sys/devices/system/cpu/cpufreq"):
        p = f"/sys/devices/system/cpu/cpufreq/{f}/scaling_governor"
        if os.path.exists(p):
            try:
                open(p, "w").write("ondemand")
            except OSError:
                pass
    if remove(SYSCTL):
        for k, v in (("vm.swappiness", "60"), ("vm.vfs_cache_pressure", "100"),
                     ("vm.dirty_background_ratio", "10"), ("vm.dirty_ratio", "20")):
            sh(["sysctl", "-w", f"{k}={v}"])
    if remove(JOURNAL):
        sh(["systemctl", "restart", "systemd-journald"])
    if st.get("zram"):
        sh(["systemctl", "disable", "--now", "zramswap.service"])
    if st.get("tmpram"):
        sh(["systemctl", "disable", "tmp.mount"]); remove(TMP_UNIT)
    if st.get("noatime") is not None and os.path.exists(FSTAB + ".before-rpi400-performance"):
        shutil.copy2(FSTAB + ".before-rpi400-performance", FSTAB)
    cfg = config_txt()
    reboot_needed = False
    if cfg and BLOCK_START in open(cfg).read():
        text = re.sub(r"\n?" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", "\n",
                      open(cfg).read(), flags=re.S)
        write(cfg, text.rstrip("\n") + "\n")
        reboot_needed = True
    sh(["systemctl", "daemon-reload"])
    save({})
    out(True, "Everything is back the way it was." + (" Restart to finish." if reboot_needed or st.get("tmpram") else ""),
        reboot=reboot_needed or bool(st.get("tmpram")))


ACTIONS = dict(governor=governor, overclock=overclock, zram=zram, sysctl=sysctl, tmpram=tmpram,
               journal=journal, trim=trim, noatime=noatime, service=service, reboot=reboot, undo_all=undo_all)

if __name__ == "__main__":
    if os.geteuid() != 0:
        out(False, "This part needs to run as root.")
    if len(sys.argv) < 2 or sys.argv[1] not in ACTIONS:
        out(False, "Unknown action.")
    try:
        ACTIONS[sys.argv[1]](sys.argv[2] if len(sys.argv) > 2 else "")
    except SystemExit:
        raise
    except Exception as e:  # noqa
        out(False, f"Something went wrong: {e}")
