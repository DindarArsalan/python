import csv
from datetime import datetime
import math
import os
import random
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pygame

# --- رێکخستنەکانی بنەڕەتی شاشە و ڕادار ---
WIDTH, HEIGHT = 1150, 780
RADAR_CENTER = (400, HEIGHT // 2)
RADAR_RADIUS = 320

# رەنگە تەکتیکییەکان
COLOR_BG = (4, 10, 8)
COLOR_GRID = (0, 60, 35)
COLOR_GRID_BRIGHT = (0, 140, 70)
COLOR_SWEEP = (0, 255, 120)
COLOR_TARGET = (0, 255, 200)
COLOR_ALERT = (255, 50, 50)
COLOR_CAM = (255, 180, 0)
COLOR_TEXT = (140, 255, 190)
COLOR_PANEL_BG = (8, 18, 15)
COLOR_BORDER = (0, 100, 55)

# بنکەدراوەی ناسینەوەی خێرای کۆمپانیاکان بەپێی OUI ی سەرەتای MAC
OUI_DATABASE = {
    "F0:99:BF": "Apple Inc", "AC:BC:32": "Apple Inc", "BC:D1:D3": "Apple Inc",
    "DC:A9:04": "Apple Inc", "38:F9:D3": "Apple Inc", "B8:78:26": "Apple Inc",
    "50:EC:50": "Apple Inc", "70:EE:50": "Apple Inc", "A4:83:E7": "Apple Inc",
    "CC:9E:00": "Samsung", "50:01:D9": "Samsung", "84:25:DB": "Samsung",
    "34:41:5D": "Samsung", "68:DB:F5": "Samsung", "94:65:2D": "OnePlus/Oppo",
    "24:4C:E3": "Xiaomi", "64:CC:2E": "Xiaomi", "78:11:DC": "Xiaomi",
    "80:7D:3A": "Espressif IoT", "24:6F:28": "Espressif IoT", "30:AE:A4": "Espressif IoT",
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi", "E4:5F:01": "Raspberry Pi",
    "00:1A:2B": "Cisco", "00:0C:29": "VMware", "50:C7:BF": "TP-Link",
    "D8:07:B6": "TP-Link", "C8:3A:35": "Tenda Router", "04:95:E6": "Shenzhen/Router",
    "B4:B0:24": "Intel Mobile", "00:15:5D": "Microsoft Hyper-V"
}

# پۆرتە باوەکان بۆ ناسینەوەی ڕۆڵ و هەستیاری ئامێرەکە
TARGET_PORTS = {
    80: "WEB-PANEL",
    443: "HTTPS-SRV",
    554: "RTSP-CAM",
    22: "SSH-SERVER",
    3389: "RDP-DESKTOP",
    8080: "ALT-HTTP",
    5000: "UPNP-IOT"
}

# دروستکردنی دەنگی سۆناری سەربازی بە سینتەسایزەر
pygame.mixer.pre_init(44100, -16, 1, 512)
pygame.init()
pygame.font.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT))
pygame.display.set_caption("TACTICAL SIGINT / EW SUITE // MILITARY RECON")
clock = pygame.time.Clock()

font_xs = pygame.font.SysFont("consolas", 11)
font_sm = pygame.font.SysFont("consolas", 13)
font_md = pygame.font.SysFont("consolas", 15, bold=True)
font_lg = pygame.font.SysFont("consolas", 19, bold=True)

def generate_ping_sound():
    sample_rate = 44100
    duration = 0.08
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    waveform = np.sin(2 * np.pi * 1200 * t) * np.exp(-t * 35)
    sound_array = (waveform * 16383).astype(np.int16)
    return pygame.sndarray.make_sound(sound_array)

try:
    sonar_sound = generate_ping_sound()
except Exception:
    sonar_sound = None

# تۆمارکردنی لۆگی هەواڵگری لە فایلی دەرەکی
LOG_FILE = "ew_tactical_log.csv"
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "IP Address", "MAC Address", "Vendor", "Device Role", "Open Ports"])

def log_detected_entity(ip, mac, vendor, role, ports):
    try:
        with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ip, mac, vendor, role, "-".join(map(str, ports))])
    except Exception:
        pass

def get_base_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        parts = local_ip.split(".")
        return f"{parts[0]}.{parts[1]}.{parts[2]}", local_ip
    except Exception:
        return "192.168.1", "127.0.0.1"

class TacticalNode:
    def __init__(self, ip, mac, vendor="UNKNOWN"):
        self.ip = ip
        self.mac = mac.upper()
        self.vendor = vendor
        self.role = "CLIENT"
        self.open_ports = []
        self.rtt_ms = 15.0
        self.is_camera = False

        # شوێنی جێگیر لەسەر بازنە
        seed_val = sum(int(x) for x in ip.split(".") if x.isdigit())
        random.seed(seed_val)
        self.angle = random.uniform(0, 360)
        self.distance = 120.0
        self.glow = 0.0
        self.sound_triggered = False

    def update_distance(self):
        # ئەژمارکردنی شوێن لەسەر بازنەکە بەپێی RTT (نزیک لە سەنتەر یان دوور)
        clamped_rtt = max(1.0, min(self.rtt_ms, 120.0))
        self.distance = 55 + (clamped_rtt / 120.0) * (RADAR_RADIUS - 75)

    def coords(self):
        rad = math.radians(self.angle)
        x = RADAR_CENTER[0] + self.distance * math.cos(rad)
        y = RADAR_CENTER[1] + self.distance * math.sin(rad)
        return int(x), int(y)

detected_nodes = {}
nearby_wifi_aps = []
status_msg = "EW SYSTEMS ONLINE"

# پشکنینی پۆرتە دیاریکراوەکان بە خێرایی
def inspect_node_services(node):
    open_p = []
    is_cam = False
    for p, desc in TARGET_PORTS.items():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            res = sock.connect_ex((node.ip, p))
            if res == 0:
                open_p.append(p)
                if p == 554:
                    is_cam = True
            sock.close()
        except Exception:
            pass
    node.open_ports = open_p
    node.is_camera = is_cam

    if is_cam:
        node.role = "IP-CAMERA [STREAM]"
    elif 3389 in open_p:
        node.role = "WINDOWS-PC"
    elif 22 in open_p:
        node.role = "LINUX/SSH-HOST"
    elif 80 in open_p or 443 in open_p:
        node.role = "ROUTER/WEB-NODE"
    elif node.ip.endswith(".1"):
        node.role = "GATEWAY-ROUTER"

# پشکنینی کاتی گەیشتن (RTT)
def measure_rtt(ip):
    try:
        cmd = ["ping", "-n", "1", "-w", "300", ip]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if "time=" in res.stdout:
            match = re.search(r"time[=<]([0-9]+)ms", res.stdout)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    return 10.0

def resolve_vendor(mac):
    prefix = mac[:8].replace("-", ":").upper()
    for k, v in OUI_DATABASE.items():
        if prefix.startswith(k):
            return v
    return "GENERIC-NODE"

# سکانکردنی تۆڕە بێتەلەکانی دەوروبەر بە netsh
def scan_wifi_airwaves():
    global nearby_wifi_aps
    try:
        output = subprocess.check_output("netsh wlan show networks mode=bssid", shell=True, text=True, errors="ignore")
        aps = []
        cur_ssid = "HIDDEN"
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("SSID") and ":" in line:
                parts = line.split(":", 1)
                cur_ssid = parts[1].strip() if parts[1].strip() else "[HIDDEN]"
            elif line.startswith("Signal") and ":" in line:
                sig = line.split(":")[1].strip()
                aps.append(f"{cur_ssid[:12]} ({sig})")
        nearby_wifi_aps = aps[:4]
    except Exception:
        nearby_wifi_aps = []

# کارکەری سەرەکی پشکنینی تۆڕ لە پاشبنەما
def background_ew_worker():
    global status_msg
    base_subnet, my_ip = get_base_ip()

    while True:
        status_msg = f"SWEEPING SPECTRUM: {base_subnet}.0/24"
        scan_wifi_airwaves()

        # پینگی فرەتێرد بۆ ناچارکردنی ئامێرەکان تا بەخەبەر بێن
        ips = [f"{base_subnet}.{i}" for i in range(1, 255)]
        with ThreadPoolExecutor(max_workers=75) as ex:
            ex.map(lambda ip: subprocess.run(["ping", "-n", "1", "-w", "180", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), ips)

        try:
            arp_out = subprocess.check_output("arp -a", shell=True, text=True)
            for line in arp_out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and "-" in parts[1]:
                    ip = parts[0]
                    mac = parts[1]
                    typ = parts[2].lower()

                    if typ == "dynamic" and ip.startswith(base_subnet):
                        if ip not in detected_nodes:
                            vendor = resolve_vendor(mac)
                            node = TacticalNode(ip, mac, vendor)
                            node.rtt_ms = measure_rtt(ip)
                            node.update_distance()
                            inspect_node_services(node)
                            detected_nodes[ip] = node
                            log_detected_entity(ip, mac, vendor, node.role, node.open_ports)
                        else:
                            detected_nodes[ip].rtt_ms = measure_rtt(ip)
                            detected_nodes[ip].update_distance()

            # جێگیرکردنی ئامێری سەرەکی خۆت
            if my_ip not in detected_nodes:
                self_node = TacticalNode(my_ip, "NIC-INTERFACE", "HOST TERMINAL")
                self_node.role = "HOST C2"
                self_node.distance = 25
                detected_nodes[my_ip] = self_node

            status_msg = f"SIGINT LOCK: {len(detected_nodes)} TARGETS TRACKED"
        except Exception:
            status_msg = "ARP INTERCEPT RUNNING..."

        time.sleep(5)

# دەستپێکردنی کارکەری پشکنین
worker = threading.Thread(target=background_ew_worker, daemon=True)
worker.start()

def draw_tactical_hud(surface, sweep_ang):
    # خشتەی مەودا و بازنەکان
    for r in range(60, RADAR_RADIUS + 1, 60):
        pygame.draw.circle(surface, COLOR_GRID, RADAR_CENTER, r, 1)
        surface.blit(font_xs.render(f"{r}m", True, COLOR_GRID_BRIGHT), (RADAR_CENTER[0] + 5, RADAR_CENTER[1] - r + 3))

    # هێڵە تەکتیکییەکانی کرۆس
    pygame.draw.line(surface, COLOR_GRID, (RADAR_CENTER[0] - RADAR_RADIUS, RADAR_CENTER[1]), (RADAR_CENTER[0] + RADAR_RADIUS, RADAR_CENTER[1]), 1)
    pygame.draw.line(surface, COLOR_GRID, (RADAR_CENTER[0], RADAR_CENTER[1] - RADAR_RADIUS), (RADAR_CENTER[0], RADAR_CENTER[1] + RADAR_RADIUS), 1)
    pygame.draw.circle(surface, COLOR_GRID_BRIGHT, RADAR_CENTER, RADAR_RADIUS, 2)

    # تیشکی سووڕاوەی ڕادار
    rad = math.radians(sweep_ang)
    tx = RADAR_CENTER[0] + RADAR_RADIUS * math.cos(rad)
    ty = RADAR_CENTER[1] + RADAR_RADIUS * math.sin(rad)
    pygame.draw.line(surface, COLOR_SWEEP, RADAR_CENTER, (tx, ty), 2)

    # شوێنکەوتووی تیشک (Phosphor Persistence Trail)
    for i in range(1, 30):
        tail_rad = math.radians((sweep_ang - i * 1.3) % 360)
        px = RADAR_CENTER[0] + RADAR_RADIUS * math.cos(tail_rad)
        py = RADAR_CENTER[1] + RADAR_RADIUS * math.sin(tail_rad)
        fade_val = max(0, 180 - i * 6)
        pygame.draw.line(surface, (0, fade_val, int(fade_val * 0.45)), RADAR_CENTER, (px, py), 1)

sweep_angle = 0
running = True

while running:
    clock.tick(60)
    sweep_angle = (sweep_angle + 2.5) % 360

    for event in pygame.event.get():
        if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
            running = False

    screen.fill(COLOR_BG)
    draw_tactical_hud(screen, sweep_angle)

    # نەخشاندنی ئامێرەکان لەسەر ڕادار
    for ip, node in list(detected_nodes.items()):
        delta = abs(sweep_angle - node.angle)
        if delta < 3.2 or delta > 356.8:
            if not node.sound_triggered:
                if sonar_sound:
                    sonar_sound.play()
                node.glow = 1.0
                node.sound_triggered = True
        else:
            node.sound_triggered = False

        pos = node.coords()

        if node.glow > 0.05:
            r = int(5 + node.glow * 8)
            # دیاریکردنی ڕەنگ بەپێی ڕۆڵی ئامێرەکە
            if node.is_camera:
                col = COLOR_CAM
            elif "ROUTER" in node.role:
                col = COLOR_ALERT
            else:
                col = COLOR_TARGET

            pygame.draw.circle(screen, col, pos, r, 1)
            pygame.draw.circle(screen, col, pos, 3)

            tag = font_xs.render(f"{node.ip} [{node.vendor[:10]}]", True, COLOR_TEXT)
            screen.blit(tag, (pos[0] + 8, pos[1] - 8))
            node.glow *= 0.95
        else:
            pygame.draw.circle(screen, COLOR_GRID_BRIGHT, pos, 2)

    # --- پنێڵی کۆنتڕۆڵی لای ڕاست (Tactical Telemetry & C2 Feed) ---
    px = 750
    pygame.draw.rect(screen, COLOR_PANEL_BG, (px, 15, 385, HEIGHT - 30))
    pygame.draw.rect(screen, COLOR_BORDER, (px, 15, 385, HEIGHT - 30), 2)

    screen.blit(font_lg.render("SIGINT / ELECTRONIC WARFARE", True, COLOR_SWEEP), (px + 15, 25))
    screen.blit(font_xs.render(f"LATENCY MAPPING: ENABLED (RTT PROXIMITY)", True, (100, 200, 150)), (px + 15, 52))
    screen.blit(font_xs.render(status_msg[:42], True, COLOR_TEXT), (px + 15, 68))
    
    # تۆڕە بێتەلەکانی دەوروبەر
    if nearby_wifi_aps:
        wifi_str = " | ".join(nearby_wifi_aps[:2])
        screen.blit(font_xs.render(f"RF INTERCEPT: {wifi_str[:38]}", True, COLOR_CAM), (px + 15, 84))

    pygame.draw.line(screen, COLOR_BORDER, (px + 10, 105), (px + 375, 105), 1)
    screen.blit(font_md.render("INTERCEPTED NODES & FINGERPRINTS:", True, COLOR_TEXT), (px + 15, 115))

    # لیستی ئامێرە ئاشکراکراوەکان بە زانیاری دەوڵەمەند
    y_pos = 142
    for ip, dev in list(detected_nodes.items())[:15]:
        vendor_tag = dev.vendor[:12]
        ports_str = ",".join(map(str, dev.open_ports[:3])) if dev.open_ports else "NONE"
        col = COLOR_CAM if dev.is_camera else (COLOR_ALERT if "ROUTER" in dev.role else COLOR_SWEEP)

        line_1 = f"• {dev.ip:<15} [{vendor_tag}]"
        line_2 = f"  MAC: {dev.mac[:14]}.. | RTT: {int(dev.rtt_ms)}ms"
        line_3 = f"  ROLE: {dev.role} | PORTS: [{ports_str}]"

        screen.blit(font_sm.render(line_1, True, col), (px + 15, y_pos))
        screen.blit(font_xs.render(line_2, True, (120, 170, 150)), (px + 15, y_pos + 14))
        screen.blit(font_xs.render(line_3, True, (90, 140, 120)), (px + 15, y_pos + 26))
        y_pos += 41

    screen.blit(font_xs.render(f"LOGGED: {LOG_FILE} | ESC TO ABORT", True, COLOR_GRID), (30, HEIGHT - 22))
    pygame.display.flip()

pygame.quit()
