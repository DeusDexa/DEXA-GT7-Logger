# V32 - experimentelle Streckenerkennung auf Basis der stabilen V31
# V31 bleibt unverändert und ist der stabile Referenzstand.
# Experiment:
# - lädt data/gt7trackdetect.csv
# - lädt data/course.csv
# - nutzt POSITION X/Z aus dem GT7-A-Paket
# - erkennt nach einer vollständigen Runde über Kontrolllinie + Richtung + Bounding-Box-IoU
# - übernimmt nur eindeutige Treffer >= 96 %
# - unklare Ergebnisse werden nur protokolliert
import csv
import math
import socket
import sys
import struct
import os
from pathlib import Path
#import datetime
import subprocess
import traceback
import webbrowser
import time
import threading
import queue
import ctypes
import tkinter as tk
from tkinter import messagebox
import tkinter.font as tkfont
from tkinter import scrolledtext

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

from salsa20 import Salsa20_xor
from collections import deque
from typing import List
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from matplotlib.patches import Rectangle 
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.font_manager import FontProperties
from random import randint, uniform
from datetime import datetime

# Konfiguration
MATPLOTLIB_STYLE = 'ggplot'
dejavu_font = FontProperties(family="DejaVu Sans")

GGPLOT_THEME = {
    "lap_color": "#FFA500",
    "fuel_color": "#2ca02c",
    "bestlap_color": "#FFD700",
    "max_speed_color": "#1f77b4",
    "min_speed_color": "#d62728",
    "text_color": "black"
}



def app_resource_path(*parts):
    """Pfad für normale Python-Ausführung und spätere PyInstaller-Bundles."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def register_private_fonts():
    """Registriert die mitgelieferten TTFs nur für den laufenden Windows-Prozess.

    Fehlt eine Datei oder schlägt die Registrierung fehl, arbeitet die GUI
    mit den eingebauten Windows-Fallback-Schriften weiter.
    """
    if os.name != "nt":
        return

    FR_PRIVATE = 0x10
    font_dir = app_resource_path("assets", "Fonts")
    font_files = (
        "Inter-VariableFont_opsz,wght.ttf",
        "Inter-Italic-VariableFont_opsz,wght.ttf",
        "Barlow-Regular.ttf",
        "Barlow-Medium.ttf",
        "Barlow-SemiBold.ttf",
        "Barlow-Bold.ttf",
        "RobotoMono-VariableFont_wght.ttf",
    )

    try:
        add_font = ctypes.windll.gdi32.AddFontResourceExW
        for filename in font_files:
            path = font_dir / filename
            if path.exists():
                add_font(str(path), FR_PRIVATE, 0)
    except Exception:
        # Die GUI besitzt bewusst sichere Fallback-Fonts.
        pass


def global_exception_handler(exc_type, exc_value, exc_traceback):
    print("🚨 Unbehandelter Fehler:")
    traceback.print_exception(exc_type, exc_value, exc_traceback)

sys.excepthook = global_exception_handler

current_lap_max_speed = 0
current_lap_min_speed = 333 
lap_history = []
fuel_start_of_lap = None 
fuel_avg = 0
fuel_used = 0.00
fuel_used = round(fuel_used, 2)
fuel_used_cur = 0 
tanken = False
tanken_first_pkt = False
fuel_prev = None
fuel_before_box = 0
in_race = False
paket1_lap1_done = False
lastLap = None
fout = None
lapTime = 0
startTime = None
current_laptime = 0 
race_start_menu = False
main_menu = False
start_pos:int = 0
race_id = f"Race_ID_{datetime.now():%Y%m%d%H%M%S}"
current_lap_speeds: List[int] = []

SendDelaySeconds = 10
ReceivePort = 33740
SendPort = 33739
port = ReceivePort
pknt = 0
#DUMP_PACKET_NR = 112 # zu testzwecken paket 112 dumpen 

# wenn eine ip-adresse übergeben wurde dann diese nehmen
# falls keine übergeben wurde prüfen ob meine PS5 da ist
# Sonst abbruch     
# Standardwerte
ip = "192.168.178.66"
enable_graphics = True

# Parameter auswerten
if len(sys.argv) >= 2:
    ip = sys.argv[1]

if len(sys.argv) >= 3 and sys.argv[2].lower() == "nogfx":
    enable_graphics = False

# Initialisierung eines Ringpuffers
log_lines = deque(maxlen=500)

# V20: Kommunikation zwischen Logger-Thread und GUI.
ui_queue = queue.Queue()
current_vehicle_name = "— noch nicht erkannt —"
current_vehicle_id = None
current_track_name = "— noch nicht erkannt —"
stop_event = threading.Event()
logger_socket = None

def reset_session_identity():
    """Fahrzeug und Strecke außerhalb einer aktiven Rennsession zurücksetzen."""
    globals()["current_vehicle_name"] = "— noch nicht erkannt —"
    globals()["current_vehicle_id"] = None
    globals()["current_track_name"] = "— noch nicht erkannt —"
    ui_queue.put(("vehicle", "— noch nicht erkannt —"))
    ui_queue.put(("track", "— noch nicht erkannt —"))


def add_log(message: str):
    """Ereignismeldung im Speicher ablegen und an die GUI senden."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"{timestamp}  {message}"
    log_lines.append(entry)
    ui_queue.put(("log", entry))

def publish_status(**values):
    """Aktuelle Live-Werte thread-sicher an die GUI übergeben."""
    ui_queue.put(("status", values))

def publish_connection(text: str, state: str = "waiting"):
    ui_queue.put(("connection", {"text": text, "state": state}))

class DexaLoggerGUI:
    """V22: Standard-Tkinter-GUI mit modernem Kartenlayout.

    Die fachliche Logger-Logik bleibt außerhalb dieser Klasse unverändert.
    Grafiken werden optional aus ./assets geladen.
    """

    BG = "#0b0f12"
    HEADER_BG = "#0a0d10"
    PANEL = "#10161b"
    PANEL2 = "#0e1418"
    CARD = "#131b21"
    LOG_BG = "#090d10"

    FG = "#f4f7f9"
    MUTED = "#8e9aa4"
    ACCENT = "#18c7ad"
    ACCENT2 = "#23d7ff"
    BORDER = "#1d6170"
    CARD_BORDER = "#263842"
    GREEN = "#30e57a"
    RED = "#ff4d57"
    YELLOW = "#f4c542"
    BLUE = "#42a5ff"

    def __init__(self, root):
        self.root = root
        self.root.title("Dexa GT7 Logger V32")
        self.root.geometry("1280x930")
        self.root.minsize(1100, 820)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        # Die mitgelieferten Fonts werden vor dem Erzeugen des Tk-Fensters
        # registriert. Falls Windows/Tk sie trotzdem nicht kennt, greifen
        # die bewährten System-Fallbacks.
        available_fonts = set(tkfont.families(self.root))
        self.UI_FONT = (
            "Inter" if "Inter" in available_fonts
            else "Barlow" if "Barlow" in available_fonts
            else "Segoe UI"
        )
        self.MONO_FONT = (
            "Roboto Mono" if "Roboto Mono" in available_fonts else "Consolas"
        )

        self.values = {}
        self.status_dots = {}
        self.progress = {}
        self._images = []

        self._build_header()
        self._build_connection_bar()
        self._build_live_panel()
        self._build_log_panel()
        self._build_footer()

        self.root.bind("<Control-c>", lambda _e: self.close())
        self.root.after(100, self.process_queue)

    # ------------------------------------------------------------------
    # Asset handling
    # ------------------------------------------------------------------
    @staticmethod
    def resource_path(*parts):
        """Funktioniert lokal und später auch mit einem PyInstaller-Bundle."""
        return app_resource_path(*parts)

    def _load_asset(self, filename, max_size):
        """Lädt ein PNG proportional skaliert. Fehlt es, wird None geliefert."""
        path = self.resource_path("assets", filename)
        if not path.exists():
            return None
        try:
            if Image is not None and ImageTk is not None:
                img = Image.open(path).convert("RGBA")
                img.thumbnail(max_size, Image.Resampling.LANCZOS)
                tk_img = ImageTk.PhotoImage(img)
            else:
                # Standard-Tkinter-Fallback ohne Skalierung.
                tk_img = tk.PhotoImage(file=str(path))
            self._images.append(tk_img)
            return tk_img
        except Exception as exc:
            add_log(f"Asset konnte nicht geladen werden ({filename}): {exc}")
            return None


    def _add_icon(self, parent, filename, fallback="•", size=(22, 22),
                  bg=None, fg=None):
        """Fügt ein PNG-Icon ein; bei fehlender Datei erscheint ein Fallback."""
        bg = bg or self.CARD
        fg = fg or self.ACCENT
        img = self._load_asset(filename, size)
        if img:
            lbl = tk.Label(parent, image=img, bg=bg, bd=0)
        else:
            lbl = tk.Label(
                parent, text=fallback, bg=bg, fg=fg,
                font=("Segoe UI Symbol", 11, "bold")
            )
        lbl.pack(side="left")
        return lbl

    # ------------------------------------------------------------------
    # Header + menu
    # ------------------------------------------------------------------
    def _build_header(self):
        header = tk.Frame(self.root, bg=self.HEADER_BG, height=92)
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=self.HEADER_BG)
        left.pack(side="left", fill="y", padx=(22, 0))

        logo = (
            self._load_asset("DEXA-LOGO-Logger.png", (420, 64))
            or self._load_asset("DEXA-LOGO.png", (330, 64))
        )
        if logo:
            tk.Label(left, image=logo, bg=self.HEADER_BG, bd=0).pack(
                side="left", pady=12
            )
        else:
            tk.Label(
                left, text="DEXA GT7 LOGGER", bg=self.HEADER_BG, fg=self.FG,
                font=(self.UI_FONT, 24, "bold")
            ).pack(side="left", pady=20)

        tk.Label(
            left, text="V31", bg=self.HEADER_BG, fg=self.ACCENT,
            font=(self.UI_FONT, 10, "bold")
        ).pack(side="left", padx=(12, 0), pady=(30, 0))

        # Burger-Menü
        menu_button = tk.Menubutton(
            header, text="☰", bg=self.HEADER_BG, fg=self.FG,
            activebackground=self.CARD, activeforeground=self.FG,
            relief="flat", bd=0, font=("Segoe UI Symbol", 17),
            padx=10, pady=6, cursor="hand2"
        )
        menu = tk.Menu(menu_button, tearoff=False, bg=self.CARD, fg=self.FG,
                       activebackground=self.ACCENT, activeforeground="#071011")
        menu.add_command(label="Über DEXA Logger", command=self._show_about)
        menu.add_command(
            label="Rennstatistik / Dashboard",
            command=lambda: webbrowser.open(
                "https://racetracks-dashboard-4zcsrreq2ufpw9wvb5mm8q.streamlit.app/"
            )
        )
        menu.add_command(
            label="GitHub-Projekt",
            command=lambda: webbrowser.open(
                "https://github.com/DeusDexa/DEXA-GT7-Logger"
            )
        )
        menu.add_command(
            label="YouTube",
            command=lambda: webbrowser.open(
                "https://www.youtube.com/@DeusDexa"
            )
        )
        menu.add_separator()
        menu.add_command(label="Logger beenden", command=self.close)
        menu_button.configure(menu=menu)
        menu_button.pack(side="right", padx=(6, 18), pady=18)

        car = (
            self._load_asset("Ferrari296GT3-nachts.png", (520, 86))
            or self._load_asset("Ferrari296GT3-frei.png", (430, 86))
        )
        if car:
            tk.Label(header, image=car, bg=self.HEADER_BG, bd=0).pack(
                side="right", padx=(8, 4), pady=2
            )

    def _show_about(self):
        messagebox.showinfo(
            "Über DEXA Logger",
            "DEXA GT7 Logger\n\n"
            "Der Logger ist in erster Linie als persönliches Projekt entstanden. "
            "Mein ursprüngliches Ziel waren zuverlässige Logdateien meiner GT7-Rennen, "
            "um daraus Statistiken und Auswertungen erstellen zu können.\n\n"
            "Live-Telemetrie-Dashboards gibt es bereits viele. Der Schwerpunkt des "
            "DEXA GT7 Loggers liegt deshalb bewusst auf sauberen Rennlogs und deren "
            "weiterer Auswertung.\n\n"
            "Die Rennstatistik wird separat über das Streamlit-Dashboard ausgewertet.\n\n"
            "Aktuelle Version: V31 (Experiment auf Basis V30)"
        )

    # ------------------------------------------------------------------
    # Connection bar
    # ------------------------------------------------------------------
    def _build_connection_bar(self):
        outer = tk.Frame(self.root, bg=self.BG)
        outer.pack(fill="x", padx=20, pady=(10, 8))

        bar = tk.Frame(
            outer, bg=self.PANEL2,
            highlightbackground=self.BORDER, highlightthickness=1
        )
        bar.pack(fill="x")

        self.connection_dot = tk.Label(
            bar, text="●", bg=self.PANEL2, fg=self.YELLOW,
            font=("Segoe UI", 20, "bold")
        )
        self.connection_dot.pack(side="left", padx=(14, 7), pady=6)

        controller_icon = self._load_asset("Controller.png", (24, 24))
        if controller_icon:
            tk.Label(
                bar, image=controller_icon, bg=self.PANEL2, bd=0
            ).pack(side="left", padx=(0, 9))
        else:
            tk.Label(
                bar, text="🎮", bg=self.PANEL2, fg=self.FG,
                font=("Segoe UI Emoji", 13)
            ).pack(side="left", padx=(0, 9))

        info_block = tk.Frame(bar, bg=self.PANEL2)
        info_block.pack(side="left", fill="x", expand=True, pady=(7, 7))

        self.connection = tk.Label(
            info_block, text=f"PS5 {ip} – Verbindung wird geprüft …",
            anchor="w", bg=self.PANEL2, fg=self.YELLOW,
            font=(self.UI_FONT, 11, "bold")
        )
        self.connection.pack(fill="x")

        self.vehicle_info = tk.Label(
            info_block, text="Fahrzeug: — noch nicht erkannt —",
            anchor="w", bg=self.PANEL2, fg=self.MUTED,
            font=(self.UI_FONT, 11, "bold")
        )
        self.vehicle_info.pack(fill="x", pady=(2, 0))

        self.track_info = tk.Label(
            info_block, text="Strecke: — noch nicht erkannt —",
            anchor="w", bg=self.PANEL2, fg=self.MUTED,
            font=(self.UI_FONT, 11, "bold")
        )
        self.track_info.pack(fill="x", pady=(1, 0))

        # dekorative "Signal"-Balken, Farbe folgt dem aktuellen Verbindungsstatus
        signal = tk.Frame(bar, bg=self.PANEL2)
        signal.pack(side="right", padx=(8, 15), pady=9)
        self.signal_bars = []
        for h in (5, 9, 13, 17):
            c = tk.Canvas(signal, width=5, height=20, bg=self.PANEL2,
                          highlightthickness=0)
            c.pack(side="left", padx=1)
            bar_id = c.create_rectangle(1, 20-h, 4, 19, fill=self.YELLOW, outline="")
            self.signal_bars.append((c, bar_id))

    # ------------------------------------------------------------------
    # Live panel / cards
    # ------------------------------------------------------------------
    def _build_live_panel(self):
        outer = tk.Frame(
            self.root, bg=self.PANEL,
            highlightbackground=self.BORDER, highlightthickness=1
        )
        outer.pack(fill="x", padx=20, pady=(0, 9))

        title = tk.Frame(outer, bg=self.PANEL)
        title.pack(fill="x", padx=14, pady=(10, 6))
        heartbeat_icon = self._load_asset("Heartbeat.png", (28, 28))
        if heartbeat_icon:
            tk.Label(title, image=heartbeat_icon, bg=self.PANEL, bd=0).pack(side="left")
        else:
            tk.Label(
                title, text="⌁", bg=self.PANEL, fg=self.ACCENT,
                font=("Segoe UI Symbol", 20, "bold")
            ).pack(side="left")
        tk.Label(
            title, text="Live-Daten", bg=self.PANEL, fg=self.FG,
            font=(self.UI_FONT, 17, "bold")
        ).pack(side="left", padx=(8, 0))

        grid = tk.Frame(outer, bg=self.PANEL)
        grid.pack(fill="x", padx=12, pady=(0, 10))

        # Feste gleichbreite Grundspalten verhindern horizontales Springen,
        # wenn sich dynamische Textwerte ändern.
        for c in range(12):
            grid.grid_columnconfigure(
                c, weight=1, uniform="livecols", minsize=82
            )

        # Reihe 1
        self._metric_card(grid, 0, 0, 2, "Checkerflag.png", "Runde", "lap",
                          emphasis=True, fallback="⚑")
        self._metric_card(grid, 0, 2, 2, "LastLap.png", "Letzte Runde", "last_lap_no",
                          fallback="◷")
        self._metric_card(grid, 0, 4, 2, "Position.png", "Position", "position",
                          emphasis=True, fallback="◇")
        self._metric_card(grid, 0, 6, 3, "LastLap.png", "Last Lap", "last_laptime",
                          emphasis=True, fallback="◷")
        self._metric_card(grid, 0, 9, 3, "bestlapcrown.png", "Best Lap", "best_laptime",
                          emphasis=True, value_color=self.ACCENT2, fallback="★")

        # Reihe 2
        self._metric_card(grid, 1, 0, 2, "Speed.png", "Speed", "speed",
                          emphasis=True, fallback="◉")
        self._metric_card(grid, 1, 2, 1, "Gear.png", "", "gear",
                          emphasis=True, fallback="H")
        self._bar_card(grid, 1, 3, 3, "RPM", "rpm", "rpm_pct", self.ACCENT,
                       "RPM.png", fallback="◉")
        self._dual_bar_card(grid, 1, 6, 3)
        self._paired_metric_card(
            grid, 1, 9, 3,
            ("maxkmh.png", "Max", "max_speed", "↑"),
            ("minkmh.png", "Min", "min_speed", "↓")
        )

        # Reihe 3
        self._tyre_card(grid, 2, 0, 5)
        self._bar_card(
            grid, 2, 5, 3, "Fuel", "fuel", "fuel_pct", self.BLUE,
            "tank.png", fallback="▣", show_bar=False
        )
        self._paired_metric_card(
            grid, 2, 8, 4,
            ("tank.png", "Verbr.", "fuel_used", "◫"),
            ("tank.png", "Ø / Lap", "fuel_avg", "Ø")
        )

        self._status_row(outer)

    def _card_shell(self, parent, row, col, span):
        f = tk.Frame(
            parent, bg=self.CARD,
            highlightbackground=self.CARD_BORDER, highlightthickness=1
        )
        f.grid(row=row, column=col, columnspan=span, sticky="nsew",
               padx=5, pady=5, ipadx=2, ipady=2)
        return f

    def _metric_card(self, parent, row, col, span, icon_file, caption, key,
                     emphasis=False, value_color=None, fallback="•"):
        card = self._card_shell(parent, row, col, span)

        top = tk.Frame(card, bg=self.CARD)
        top.pack(fill="x", padx=12, pady=(9, 0))
        self._add_icon(top, icon_file, fallback=fallback, size=(22, 22))
        if caption:
            tk.Label(
                top, text=caption, bg=self.CARD, fg=self.MUTED,
                font=(self.UI_FONT, 12, "bold"), anchor="w"
            ).pack(side="left", padx=(7, 0))

        # Monospace + feste Zeichenbreite: wechselnde Zahlen verändern
        # die Geometrie der Karte nicht mehr.
        label = tk.Label(
            card, text="---", bg=self.CARD, fg=value_color or self.FG,
            anchor="w", width=13,
            font=(self.MONO_FONT, 22 if emphasis else 18, "bold")
        )
        label.pack(anchor="w", padx=12, pady=(5, 10))
        self.values[key] = label

    def _paired_metric_card(self, parent, row, col, span, left, right):
        """Zwei exakt gleich breite Metrikfelder innerhalb eines festen Bereichs."""
        outer = self._card_shell(parent, row, col, span)
        outer.configure(bg=self.PANEL)

        pair = tk.Frame(outer, bg=self.PANEL)
        pair.pack(fill="both", expand=True)
        pair.grid_columnconfigure(0, weight=1, uniform="pair")
        pair.grid_columnconfigure(1, weight=1, uniform="pair")
        pair.grid_rowconfigure(0, weight=1)

        for idx, spec in enumerate((left, right)):
            icon_file, caption, key, fallback = spec
            card = tk.Frame(
                pair, bg=self.CARD,
                highlightbackground=self.CARD_BORDER, highlightthickness=1
            )
            card.grid(row=0, column=idx, sticky="nsew",
                      padx=(0, 3) if idx == 0 else (3, 0))

            top = tk.Frame(card, bg=self.CARD)
            top.pack(fill="x", padx=12, pady=(9, 0))
            self._add_icon(top, icon_file, fallback=fallback, size=(22, 22))
            tk.Label(
                top, text=caption, bg=self.CARD, fg=self.MUTED,
                font=(self.UI_FONT, 12, "bold"), anchor="w"
            ).pack(side="left", padx=(7, 0))

            label = tk.Label(
                card, text="---", bg=self.CARD, fg=self.FG,
                anchor="w", width=10,
                font=(self.MONO_FONT, 18, "bold")
            )
            label.pack(anchor="w", padx=12, pady=(5, 10))
            self.values[key] = label

    def _bar_card(self, parent, row, col, span, caption, value_key,
                  pct_key, bar_color, icon_file, fallback="•", show_bar=True):
        card = self._card_shell(parent, row, col, span)
        top = tk.Frame(card, bg=self.CARD)
        top.pack(fill="x", padx=12, pady=(8, 1))
        self._add_icon(top, icon_file, fallback=fallback, size=(22, 22))
        tk.Label(
            top, text=caption, bg=self.CARD, fg=self.MUTED,
            font=(self.UI_FONT, 12, "bold")
        ).pack(side="left", padx=(7, 0))

        label = tk.Label(
            card, text="---", bg=self.CARD, fg=self.FG,
            font=(self.MONO_FONT, 18, "bold"), anchor="w", width=16
        )
        if show_bar:
            label.pack(anchor="w", padx=12)
            bar = tk.Canvas(card, height=8, bg="#202a31", highlightthickness=0)
            bar.pack(fill="x", padx=10, pady=(5, 9))
            self.progress[pct_key] = (bar, bar_color)
        else:
            # Ohne Balken sitzt der Zahlenwert auf derselben optischen Höhe
            # wie die übrigen Werte der unteren Datenzeile.
            label.pack(anchor="w", padx=12, pady=(5, 10))

        self.values[value_key] = label


    def _dual_bar_card(self, parent, row, col, span):
        card = self._card_shell(parent, row, col, span)
        for caption, value_key, pct_key, color, icon_file in (
            ("Gas", "gas", "gas_pct", self.GREEN, "Gas.png"),
            ("Bremse", "brake", "brake_pct", self.RED, "Bremse.png"),
        ):
            line = tk.Frame(card, bg=self.CARD)
            line.pack(fill="x", padx=12, pady=(8 if caption == "Gas" else 5, 3))

            self._add_icon(line, icon_file, fallback="•", size=(22, 22))
            tk.Label(line, text=caption, bg=self.CARD, fg=self.MUTED,
                     width=7, anchor="w", font=(self.UI_FONT, 12, "bold")).pack(side="left", padx=(6, 0))

            bar = tk.Canvas(line, height=10, bg="#202a31", highlightthickness=0)
            bar.pack(side="left", fill="x", expand=True, padx=(3, 8))
            self.progress[pct_key] = (bar, color)

            value = tk.Label(
                line, text="---", bg=self.CARD, fg=self.FG,
                width=5, anchor="e", font=(self.MONO_FONT, 15, "bold")
            )
            value.pack(side="right")
            self.values[value_key] = value

    def _tyre_card(self, parent, row, col, span):
        """Kompakte Reifenkarte ohne zusätzlichen 'Reifen'-Header."""
        card = self._card_shell(parent, row, col, span)

        vals = tk.Frame(card, bg=self.CARD)
        vals.pack(fill="both", expand=True, padx=8, pady=(10, 10))
        for c in range(4):
            vals.grid_columnconfigure(c, weight=1, uniform="tyres", minsize=72)
        vals.grid_rowconfigure(0, weight=1)

        for col_idx, (caption, key) in enumerate((
            ("FL", "tyre_fl"), ("FR", "tyre_fr"),
            ("RL", "tyre_rl"), ("RR", "tyre_rr")
        )):
            box = tk.Frame(vals, bg=self.CARD)
            box.grid(row=0, column=col_idx, sticky="nsew")

            tk.Label(
                box, text=caption, bg=self.CARD, fg=self.MUTED,
                font=(self.UI_FONT, 10, "bold"), width=4
            ).pack(pady=(0, 5))

            lab = tk.Label(
                box, text="---", bg=self.CARD, fg=self.FG,
                font=(self.MONO_FONT, 18, "bold"),
                width=6, anchor="center"
            )
            lab.pack()
            self.values[key] = lab


    def _status_row(self, parent):
        status = tk.Frame(
            parent, bg=self.PANEL2,
            highlightbackground=self.CARD_BORDER, highlightthickness=1
        )
        status.pack(fill="x", padx=12, pady=(0, 11))

        for key, caption in (
            ("in_race", "In Race"),
            ("race_started", "Rennen gestartet"),
            ("race_menu", "Race Menu"),
            ("main_menu", "Hauptmenü"),
            ("tanken", "Tanken"),
        ):
            box = tk.Frame(status, bg=self.PANEL2)
            box.pack(side="left", padx=(12, 20), pady=7)
            dot = tk.Label(box, text="●", bg=self.PANEL2, fg=self.RED,
                           font=(self.UI_FONT, 15, "bold"))
            dot.pack(side="left")
            tk.Label(box, text=caption, bg=self.PANEL2, fg=self.FG,
                     font=(self.UI_FONT, 11, "bold")).pack(side="left", padx=(7, 0))
            self.status_dots[key] = dot

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------
    def _build_log_panel(self):
        outer = tk.Frame(
            self.root, bg=self.PANEL,
            highlightbackground=self.BORDER, highlightthickness=1
        )
        outer.pack(fill="both", expand=True, padx=20, pady=(0, 9))

        title = tk.Frame(outer, bg=self.PANEL)
        title.pack(fill="x", padx=12, pady=(8, 6))
        tk.Label(title, text="▤", bg=self.PANEL, fg=self.ACCENT,
                 font=("Segoe UI Symbol", 15, "bold")).pack(side="left")
        tk.Label(title, text="Ereignisprotokoll", bg=self.PANEL, fg=self.FG,
                 font=(self.UI_FONT, 15, "bold")).pack(side="left", padx=(7, 0))

        tk.Button(
            title, text="Log leeren", command=self.clear_log,
            bg=self.CARD, fg=self.FG, activebackground="#1c2a32",
            activeforeground=self.FG, relief="flat", bd=0,
            padx=11, pady=4, cursor="hand2", font=(self.UI_FONT, 10)
        ).pack(side="right")

        self.log = scrolledtext.ScrolledText(
            outer, wrap="word", bg=self.LOG_BG, fg=self.FG,
            insertbackground=self.FG, relief="flat", bd=0,
            font=(self.MONO_FONT, 11), padx=10, pady=7,
            height=10, state="disabled"
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log.tag_configure("time", foreground="#81909b")
        self.log.tag_configure("normal", foreground=self.FG)
        self.log.tag_configure("ok", foreground=self.GREEN)
        self.log.tag_configure("info", foreground=self.ACCENT2)
        self.log.tag_configure("warn", foreground=self.YELLOW)
        self.log.tag_configure("error", foreground=self.RED)

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=self.BG)
        footer.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(
            footer,
            text="Strg+C oder Fenster schließen beendet den Logger.",
            bg=self.BG, fg=self.MUTED, font=(self.UI_FONT, 10)
        ).pack(side="left")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append_log(self, line):
        if "  " in line:
            stamp, message = line.split("  ", 1)
        else:
            stamp, message = "", line

        low = message.lower()
        if "fehler" in low or "❌" in message:
            tag = "error"
        elif "⚠" in message or "nicht erreichbar" in low:
            tag = "warn"
        elif ("erreicht" in low or "beginnt" in low or "aktiv" in low
              or "gespeichert" in low):
            tag = "ok"
        elif "runde" in low or "logverzeichnis" in low:
            tag = "info"
        else:
            tag = "normal"

        self.log.configure(state="normal")
        if stamp:
            self.log.insert("end", stamp + "  ", "time")
        self.log.insert("end", message + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def _set_progress(self, key, percent):
        if key not in self.progress:
            return
        canvas, color = self.progress[key]
        try:
            pct = max(0.0, min(100.0, float(percent)))
        except (TypeError, ValueError):
            pct = 0.0

        canvas.update_idletasks()
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#202a31", outline="")
        canvas.create_rectangle(
            0, 0, width * pct / 100.0, height,
            fill=color, outline=""
        )

    def update_status(self, data):
        for key, value in data.items():
            if key in self.values:
                self.values[key].configure(text=str(value))

        for key in self.status_dots:
            if key in data:
                self.status_dots[key].configure(
                    fg=self.GREEN if bool(data[key]) else self.RED
                )

        for key in ("gas_pct", "brake_pct", "rpm_pct", "fuel_pct"):
            if key in data:
                self._set_progress(key, data[key])

    def update_connection(self, payload):
        state = payload.get("state", "waiting")
        color = (
            self.GREEN if state == "ok"
            else self.RED if state == "error"
            else self.YELLOW
        )
        self.connection.configure(text=payload.get("text", ""), fg=color)
        self.connection_dot.configure(fg=color)
        for canvas, bar_id in getattr(self, "signal_bars", []):
            canvas.itemconfig(bar_id, fill=color)

    def process_queue(self):
        try:
            while True:
                kind, payload = ui_queue.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "status":
                    self.update_status(payload)
                elif kind == "connection":
                    self.update_connection(payload)
                elif kind == "vehicle":
                    self.vehicle_info.configure(text=f"Fahrzeug: {payload}")
                elif kind == "track":
                    self.track_info.configure(text=f"Strecke: {payload}")
        except queue.Empty:
            pass

        if not stop_event.is_set():
            self.root.after(100, self.process_queue)

    def close(self):
        stop_event.set()
        global logger_socket
        if logger_socket is not None:
            try:
                logger_socket.close()
            except Exception:
                pass
        self.root.after(100, self.root.destroy)

def save_race_summary(logpath: str, lap_history: list, start_pos:int, race_id):
    """
    Speichert am Ende des Rennens alle geloggten Runden (lap_history) 
    in einer Datei summary.txt in logpath. 
    Jede Zeile entspricht einer Runde, mit Lap-Nr, Laptime, FuelUsed, Max-Speed, Min-Speed.
    Falls finish_pos angegeben, wird am Ende die finale Position angehängt.
    """
    if not lap_history:
        # nichts zu speichern
        return

    summary_file = os.path.join(logpath, "summary.txt")
    with open(summary_file, "w", encoding="utf-8") as f:
        # Header
        #f.write("Pos     Lap     Laptime         Fuel    Max     Min     Avg\n")
        f.write("Pos\tLap\tLaptime\t\tFuel\tMax\tMin\tAvg\n")
        # Jede Runde
        for entry in lap_history:
            f.write(
                f"{entry['pos']:02d}\t"
                f"{entry['lap']:03d}\t"
                f"{entry['laptime']}\t"
                f"{entry['fuel_used']:.2f}\t"
                f"{entry['max_speed']}\t"
                f"{entry['min_speed']}\t"
                f"{entry['avg_speed']:.0f}\n"
            )
        # Die lap_history enthält dicts mit keys "laptime", "max_speed", "min_speed" etc.
        if lap_history:
            # 1) max aller max_speed
            overall_max = max(e["max_speed"] for e in lap_history)
            # 2) min aller min_speed
            overall_min = min(e["min_speed"] for e in lap_history)
            # 3) Summe aller Laptimes
            total_ms = sum(timestr_to_ms(e["laptime"]) for e in lap_history if isinstance(e["laptime"], str))
            # formatiere Gesamt-Dauer zurück in hh:mm:ss,mmm
            total_time_str = ms_to_timestr(total_ms)
            # Bestlap 
            best_lap = min(e["laptime"] for e in lap_history)
            #finish 
            if len(lap_history) >= 2:
                pos_finish = lap_history[-2]["pos"]
            else:
                pos_finish = lap_history[-1]["pos"]  # fallback

            # Alle fuel_used-Werte sammeln (nur Zahlen)
            fuel_values = [
                entry["fuel_used"]
                for entry in lap_history
                if isinstance(entry["fuel_used"], (int, float))
            ]
            # Durchschnitt berechnen (falls Liste nicht leer)
            if fuel_values:
                avg_fuel = sum(fuel_values) / len(fuel_values)
            else:
                avg_fuel = 0.0  # oder None, je nachdem wie du's handhaben willst
           
            # avg_speed berechnen 
            # Alle avg_speed-Werte aus lap_history sammeln (nur Zahlen)
            avg_speeds: List[float] = [
                entry["avg_speed"]
                for entry in lap_history
                if isinstance(entry.get("avg_speed"), (int, float))
            ]

            # Durchschnitt berechnen (falls die Liste nicht leer ist)
            if avg_speeds:
                overall_avg_speed: float = sum(avg_speeds) / len(avg_speeds)
            else:
                overall_avg_speed: float = 0.0

            # Best Lap ermitteln - alle gültigen Laptimes in ms
            lap_times_ms = [
                timestr_to_ms(e["laptime"])
                for e in lap_history
                if e["laptime"] not in ("---", None)
            ]
            if lap_times_ms:
                fastest_ms = min(lap_times_ms)
                # zurück in das Anzeige-Format:
                best_lap = ms_to_timestr(fastest_ms)
                
            f.write(" \n")
            summary_car_id = str(current_vehicle_id) if current_vehicle_id is not None else "—"

            # Feste Spaltenbreiten: Überschrift und Werte stehen sauber untereinander.
            summary_header = (
                f"{'Race_ID':<24}"
                f"{'GT7_Car_ID':<14}"
                f"{'Dauer':<16}"
                f"{'BestLap':<16}"
                f"{'min':>6}"
                f"{'max':>8}"
                f"{'avg':>8}"
                f"{'PS':>8}"
                f"{'PF':>8}"
                f"{'fuelavg':>11}"
            )
            summary_row = (
                f"{race_id:<24}"
                f"{summary_car_id:<14}"
                f"{'00:' + total_time_str:<16}"
                f"{'00:' + best_lap:<16}"
                f"{overall_min:>6}"
                f"{overall_max:>8}"
                f"{overall_avg_speed:>8.0f}"
                f"{format(start_pos, '02d'):>8}"
                f"{format(pos_finish, '02d'):>8}"
                f"{avg_fuel:>11.2f}"
            )
            f.write(summary_header + "\n")
            f.write(summary_row + "\n")

            # Session-Kontext am Ende der Summary.
            f.write("\n")
            f.write(f"Fahrzeug: {current_vehicle_name}\n")
            f.write(f"Strecke:  {current_track_name}\n")
    if enable_graphics:
        generate_graphics(logpath, lap_history, GGPLOT_THEME, race_id)


# Chart (s) anlegen 
def generate_graphics(logpath: str, lap_history: list, theme: dict, race_id, chunk_size: int = 12):
    if not lap_history:
        return
    os.makedirs(logpath, exist_ok=True)

    timestamp_str = race_id.split("_")[-1] # Zeitstempel extrahieren (nach dem letzten Unterstrich) 
    dt = datetime.strptime(timestamp_str, "%Y%m%d%H%M%S") # In datetime-Objekt umwandeln 
    race_date_str = dt.strftime("%d.%m.%Y %H:%M")  # Formatieren in "tt.mm.jjjj hh.mm"

    for i in range(0, len(lap_history), chunk_size):
        chunk = lap_history[i:i + chunk_size]
        suffix = f"{chunk[0]['lap']:02d}-{chunk[-1]['lap']:02d}"

        # V32: nur noch die neue DEXA-Analysegrafik erzeugen.
        dexa_logpath = os.path.join(logpath, f"Summary_lap_analysis_DEXA_{suffix}.png")
        add_log(f"DEXA-Grafik gespeichert: {dexa_logpath}")
        _render_dexa_graphic(
            dexa_logpath,
            chunk,
            race_date_str,
            current_vehicle_name,
            current_track_name
        )


def _render_dexa_graphic(
    filename: str,
    lap_history: list,
    race_date_str: str,
    vehicle_name: str,
    track_name: str
):
    """Neue DEXA-Vergleichsgrafik.

    Die Rundenzeitachse beginnt bewusst nicht bei 0.
    Sie wird um die tatsächlich gefahrenen Runden gezoomt, damit kleine
    Zeitunterschiede deutlich sichtbar werden. Eine Mindestspanne von
    3 Sekunden verhindert eine übertriebene optische Vergrößerung.
    """
    if not lap_history:
        return

    lap_nums = [entry["lap"] for entry in lap_history]
    lap_times_ms = [timestr_to_ms(entry["laptime"]) for entry in lap_history]
    fuel_values = [float(entry.get("fuel_used", 0) or 0) for entry in lap_history]

    best_ms = min(lap_times_ms)
    worst_ms = max(lap_times_ms)
    raw_span = max(0, worst_ms - best_ms)

    # Dynamischer Puffer:
    # unten mindestens 1,0 s bzw. 25 % der realen Spannweite,
    # oben mindestens 0,5 s bzw. 15 % der realen Spannweite.
    lower_pad = max(1000, raw_span * 0.25)
    upper_pad = max(500, raw_span * 0.15)

    y_min = max(0, best_ms - lower_pad)
    y_max = worst_ms + upper_pad

    # Mindestens 3 Sekunden sichtbarer Bereich.
    min_visible_span = 3000
    if y_max - y_min < min_visible_span:
        center = (best_ms + worst_ms) / 2
        y_min = max(0, center - min_visible_span / 2)
        y_max = y_min + min_visible_span

    # Auf volle 0,5 Sekunden runden -> ruhige, nachvollziehbare Achsgrenzen.
    y_min = math.floor(y_min / 500) * 500
    y_max = math.ceil(y_max / 500) * 500
    if y_max <= y_min:
        y_max = y_min + min_visible_span

    # DEXA-Farben passend zur GUI.
    bg = "#090D10"
    panel = "#10181E"
    grid = "#26343D"
    text_primary = "#F2F4F5"
    text_muted = "#8E9AA4"
    cyan = "#18C7C8"
    cyan_soft = "#4DD8D9"
    gold = "#F4C542"
    max_color = "#D7F1FF"
    min_color = "#FFD6D6"
    fuel_color = "#BDF7C9"

    fig, ax = plt.subplots(figsize=(12, 6.4))
    fig.patch.set_facecolor(bg)
    ax.set_facecolor(panel)

    # Balken beginnen optisch am dynamischen Achsenminimum.
    bar_heights = [t - y_min for t in lap_times_ms]
    bars = ax.bar(
        lap_nums,
        bar_heights,
        bottom=y_min,
        width=0.58,
        color=cyan,
        edgecolor=cyan_soft,
        linewidth=0.8,
        zorder=3
    )

    ax.set_ylim(y_min, y_max)
    ax.set_xticks(lap_nums)
    ax.set_xticklabels([f"Runde {n}" for n in lap_nums], color=text_muted, fontsize=9)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: ms_to_timestr(int(y))))
    ax.tick_params(axis="y", colors=text_muted, labelsize=9, length=0)
    ax.tick_params(axis="x", colors=text_muted, length=0, pad=10)

    # Dezentes horizontales Raster.
    ax.grid(axis="y", color=grid, linewidth=0.7, alpha=0.75, zorder=0)
    ax.grid(axis="x", visible=False)

    for spine in ax.spines.values():
        spine.set_visible(False)

    # Titel / Kontext
    fig.text(
        0.055, 0.945, "RUNDENANALYSE",
        color=text_primary, fontsize=18, fontweight="bold",
        ha="left", va="top"
    )
    fig.text(
        0.945, 0.945, race_date_str.replace(" ", " · "),
        color=text_muted, fontsize=10,
        ha="right", va="top"
    )

    session_parts = []
    if vehicle_name and vehicle_name != "— noch nicht erkannt —":
        session_parts.append(vehicle_name)
    if track_name and track_name != "— noch nicht erkannt —":
        session_parts.append(track_name)
    session_line = "  ·  ".join(session_parts) if session_parts else "Session"
    fig.text(
        0.055, 0.895, session_line,
        color=text_muted, fontsize=10,
        ha="left", va="top"
    )

    # Achsenhinweis unten links.
    fig.text(
        0.055, 0.055,
        "Rundenzeitachse dynamisch gezoomt",
        color=text_muted, fontsize=8,
        ha="left", va="bottom"
    )

    visible_span = y_max - y_min
    time_label_offset = visible_span * 0.018
    info_gap = visible_span * 0.045

    # Tank-Icon für die Fuel-Zeile laden. Falls das Asset fehlt,
    # wird automatisch nur der Text ohne Symbol verwendet.
    tank_icon_img = None
    try:
        tank_icon_path = app_resource_path("assets", "tank.png")
        if tank_icon_path.exists():
            tank_icon_img = plt.imread(str(tank_icon_path))
    except Exception:
        tank_icon_img = None

    # Labels pro Runde
    for bar, entry, lap_time, fuel in zip(bars, lap_history, lap_times_ms, fuel_values):
        x = bar.get_x() + bar.get_width() / 2
        is_best = lap_time == best_ms

        # Zeit groß direkt über dem Balken.
        time_label = entry["laptime"]
        if is_best:
            time_label = f"★ {time_label}"

        ax.text(
            x,
            lap_time + time_label_offset,
            time_label,
            ha="center", va="bottom",
            fontsize=10.5,
            fontweight="bold",
            color=gold if is_best else text_primary,
            zorder=5
        )

        # Max/Min/Fuel als kompakter Block nahe unterem sichtbaren Bereich.
        info_y = y_min + info_gap
        info_box = dict(
            boxstyle="round,pad=0.22,rounding_size=0.08",
            facecolor="#0B1116",
            edgecolor="#1F3038",
            linewidth=0.8,
            alpha=0.96
        )

        ax.text(
            x, info_y + visible_span * 0.075,
            f"▲ {entry['max_speed']} km/h",
            ha="center", va="center",
            fontsize=9.5, fontweight="bold",
            color=max_color, zorder=6,
            bbox=info_box
        )
        ax.text(
            x, info_y + visible_span * 0.035,
            f"▼ {entry['min_speed']} km/h",
            ha="center", va="center",
            fontsize=9.5, fontweight="bold",
            color=min_color, zorder=6,
            bbox=info_box
        )
        # Fuel: echtes GUI-Tank-Icon statt Unicode-Zeichen.
        # Dadurch gibt es keine DejaVu-Sans-Glyph-Warnung mehr.
        if tank_icon_img is not None:
            icon = OffsetImage(tank_icon_img, zoom=0.055)
            icon_box = AnnotationBbox(
                icon,
                (x - 0.10, info_y),
                frameon=False,
                box_alignment=(0.5, 0.5),
                zorder=7
            )
            ax.add_artist(icon_box)
            fuel_text_x = x + 0.035
        else:
            fuel_text_x = x

        ax.text(
            fuel_text_x, info_y,
            f"{fuel:.2f} l",
            ha="center", va="center",
            fontsize=9.5, fontweight="bold",
            color=fuel_color, zorder=6,
            bbox=info_box
        )

    # Falls tatsächlich Kraftstoff verbraucht wurde, kleine Info rechts unten.
    total_fuel = sum(v for v in fuel_values if v > 0)
    if total_fuel > 0:
        avg_fuel = total_fuel / max(1, sum(1 for v in fuel_values if v > 0))
        fig.text(
            0.945, 0.055,
            f"Ø Verbrauch/Runde: {avg_fuel:.2f} l",
            color=fuel_color, fontsize=8,
            ha="right", va="bottom"
        )

    fig.subplots_adjust(left=0.09, right=0.96, top=0.80, bottom=0.16)
    plt.savefig(filename, dpi=220, facecolor=fig.get_facecolor())
    plt.close(fig)


def clear():
    # Bildschirm säubern vor der Ausgabe 
    os.system('cls' if os.name == 'nt' else 'clear')

def ms_to_timestr(ms: int) -> str:
    # Wandelt Millisekunden in ein Format hh:mm:ss,mmm oder mm:ss,mmm um. 
    ms = int(ms)
    if ms is None or ms < 0:
        return "---"

    seconds_total = ms // 1000
    milliseconds = ms % 1000
    minutes = (seconds_total // 60) % 60
    hours = seconds_total // 3600
    seconds = seconds_total % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
    else:
        return f"{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def timestr_to_ms(s: str) -> int:
# Hilfsfunktion, um einen aus ms_to_timestr stammenden String wieder in Millisekunden zu parsen
    # s = "hh:mm:ss,mmm" oder "mm:ss,mmm"
    parts = s.split(':')
    if len(parts) == 3:
        h, m, sm = parts
    else:
        h = 0
        m, sm = parts
    ssec, ms = sm.split(',')
    total_ms = (int(h) * 3600 + int(m) * 60 + int(ssec)) * 1000 + int(ms)
    return total_ms

def load_car_database():
    """Lokale GT7 Car-ID-Tabelle laden; kein Internetzugriff nötig."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, "data", "car_ids.csv")
    mapping = {}
    try:
        with open(csv_file, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    car_id = int(row.get("ID", ""))
                except (TypeError, ValueError):
                    continue
                name = (row.get("ShortName") or "").strip()
                if name:
                    mapping[car_id] = name
        add_log(f"🚗 Fahrzeugdatenbank geladen: {len(mapping)} Einträge")
    except Exception as exc:
        add_log(f"⚠️ Fahrzeugdatenbank konnte nicht geladen werden: {exc}")
    return mapping


def load_track_detection_database():
    """Bornhall-Streckengeometrie aus data/gt7trackdetect.csv laden."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, "data", "gt7trackdetect.csv")
    rows = []
    try:
        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    rows.append({
                        "track_id": int(row["TRACK"]),
                        "p1x": float(row["P1X"]),
                        "p1z": float(row["P1Y"]),
                        "p2x": float(row["P2X"]),
                        "p2z": float(row["P2Y"]),
                        "direction": str(row["DIRECTION"]).strip(),
                        "minx": float(row["MINX"]),
                        "minz": float(row["MINY"]),
                        "maxx": float(row["MAXX"]),
                        "maxz": float(row["MAXY"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
        add_log(f"🗺️ Streckenerkennungsdatenbank geladen: {len(rows)} Einträge")
    except Exception as exc:
        add_log(f"⚠️ Streckenerkennungsdatenbank konnte nicht geladen werden: {exc}")
    return rows


def load_course_database():
    """Track-ID -> Streckenname aus data/course.csv laden."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(script_dir, "data", "course.csv")
    mapping = {}
    try:
        with open(csv_file, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                try:
                    track_id = int(row.get("ID", ""))
                except (TypeError, ValueError):
                    continue
                name = (row.get("Name") or "").strip()
                if name:
                    mapping[track_id] = name
        add_log(f"🗺️ Streckennamen geladen: {len(mapping)} Einträge")
    except Exception as exc:
        add_log(f"⚠️ Streckennamen konnten nicht geladen werden: {exc}")
    return mapping


def _track_line_intersects(p0x, p0z, p1x, p1z, p2x, p2z, p3x, p3z):
    s1x = p1x - p0x
    s1z = p1z - p0z
    s2x = p3x - p2x
    s2z = p3z - p2z
    denom = (-s2x * s1z + s1x * s2z)
    if abs(denom) < 1e-12:
        return False, "??"

    s = (-s1z * (p0x - p2x) + s1x * (p0z - p2z)) / denom
    t = ( s2x * (p0z - p2z) - s2z * (p0x - p2x)) / denom

    if s2x > 0:
        direction = "PX"
    elif s2x < 0:
        direction = "NX"
    elif s2z > 0:
        direction = "PY"
    elif s2z < 0:
        direction = "NY"
    else:
        direction = "??"

    return (0 <= s <= 1 and 0 <= t <= 1), direction


def _track_bbox_area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _track_iou(box1, box2):
    left = max(box1[0], box2[0])
    top = max(box1[1], box2[1])
    right = min(box1[2], box2[2])
    bottom = min(box1[3], box2[3])

    if left > right or top > bottom:
        return 0.0

    intersection = (right - left) * (bottom - top)
    union = _track_bbox_area(box1) + _track_bbox_area(box2) - intersection
    return intersection / union if union > 0 else 0.0



def _canonical_track_name(track_id, course_database):
    """
    Liefert für bekannte geometrisch nicht sauber unterscheidbare Varianten
    einen gemeinsamen Streckennamen.

    Wichtig: Nur konservativ zusammenfassen. Exakt unterscheidbare Layouts
    bleiben unverändert.
    """
    name = course_database.get(track_id, f"Track ID {track_id}")

    # Spa: normales Layout und 24h-Layout nutzen praktisch dieselbe Geometrie
    # für unsere X/Z-Erkennung. In diesem Fall nur die Grundstrecke melden.
    if track_id in (462, 1269):
        return "Circuit de Spa-Francorchamps"

    return name


def _matches_share_canonical_track(matches, course_database):
    """True, wenn alle Kandidaten auf denselben kanonischen Streckennamen fallen."""
    if not matches:
        return False, None

    names = {
        _canonical_track_name(track_id, course_database)
        for _, track_id in matches
    }
    if len(names) == 1:
        return True, next(iter(names))
    return False, None


def find_track_matches(prev_point, new_point, lap_bounds, track_database,
                       max_matches=3, near_best_ratio=0.02):
    if not prev_point or not new_point or not lap_bounds:
        return []

    driven_box = tuple(lap_bounds)
    matches = []

    for track in track_database:
        intersects, direction = _track_line_intersects(
            track["p1x"], track["p1z"],
            track["p2x"], track["p2z"],
            prev_point[0], prev_point[1],
            new_point[0], new_point[1],
        )
        if not intersects or direction != track["direction"]:
            continue

        known_box = (
            track["minx"], track["minz"],
            track["maxx"], track["maxz"],
        )
        matches.append((_track_iou(driven_box, known_box), track["track_id"]))

    matches.sort(key=lambda item: item[0], reverse=True)
    if not matches:
        return []

    best_iou = matches[0][0]
    near_best = [
        match for match in matches
        if match[0] >= best_iou * (1.0 - near_best_ratio)
    ]
    return near_best[:max_matches]


def create_log_dir(base_path=r"log\gt7"):
    # Erstellt ein neues Log-Verzeichnis mit Zeitstempel und gibt den Pfad zurück.
    timestamp = datetime.now().replace(microsecond=0).isoformat().replace('-', '').replace(':', '')
    logpath = os.path.join(base_path, timestamp)
    os.makedirs(logpath, exist_ok=True)
    add_log(f"📂 Neues Logverzeichnis erstellt: {logpath}")
    return logpath

def logger_worker():
    global current_lap_max_speed, current_lap_min_speed, lap_history, fuel_start_of_lap
    global fuel_avg, fuel_used, fuel_used_cur, tanken, tanken_first_pkt, fuel_prev
    global fuel_before_box, in_race, paket1_lap1_done, lastLap, fout, lapTime, startTime
    global current_laptime, race_start_menu, main_menu, start_pos, race_id, current_lap_speeds
    global pknt, logger_socket

    add_log(f"Dexa GT7 Logger V32 gestartet – PS5-IP: {ip}")
    car_database = load_car_database()
    last_detected_car_id = None

    track_database = load_track_detection_database()
    course_database = load_course_database()
    track_prev_point = None
    track_prev_lap = None
    track_min_x = float("inf")
    track_min_z = float("inf")
    track_max_x = float("-inf")
    track_max_z = float("-inf")
    detected_track_id = None

    publish_connection(f"PS5 {ip} – Verbindung wird geprüft …", "waiting")

    # V19-Verhalten bleibt erhalten: solange warten, bis die PS5 erreichbar ist.
    while not stop_event.is_set():
        try:
            result = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", ip],
                capture_output=True, text=True, encoding="utf-8", errors="ignore"
            )
            if result.returncode == 0:
                publish_connection(f"PS5 {ip} erreicht – warte auf GT7-Telemetriedaten", "ok")
                add_log("PS5 erreicht. Warte auf GT7-Telemetriedaten …")
                break
            publish_connection(f"PS5 {ip} nicht erreichbar – neuer Versuch in 30 Sekunden", "error")
            add_log("PS5 nicht erreichbar. Neuer Versuch in 30 Sekunden.")
            if stop_event.wait(30):
                return
        except Exception as exc:
            publish_connection("Fehler bei der Prüfung der PS5-Verbindung", "error")
            add_log(f"Fehler bei der Prüfung der PS5-Verbindung: {exc}")
            if stop_event.wait(30):
                return

    logpath = create_log_dir() # Erstes Verzeichnisanlegen
    logdir_initialized = True

    data_type_spec = {
        'FLOAT':{'struct_decrypt':'f', 'bytes':4},
        'BYTE':{'struct_decrypt':'B', 'bytes':1},
        'INT':{'struct_decrypt':'i','bytes':4},
        'INT32':{'struct_decrypt':'i','bytes':4},
        'SHORT':{'struct_decrypt':'H','bytes':2}
    }

    packet_data_struct = [
        (0x04,3,"FLOAT","POSITION"),
        (0x10,3,"FLOAT","VELOCITY"),
        (0x3C,1,"FLOAT","RPM"),
        (0x44,1,"FLOAT","FUEL_LEVEL"),  #0-?100	Fuel Level	amount of of fuel left, starts at Fuel Capacity at start of race, TDB for EVs
        (0x48,1,"FLOAT","FUEL_CAPA"),   # 5,100?	Fuel Capacity	amount of fuel that fits into the tank, usually 100 for fossil fuel cars. 
        (0x4C,1,"FLOAT","SPEED"),
        (0x60,4,"FLOAT","TYRES_TEMP"),
        (0x74,2,"SHORT","LAPS"),
        (0x76,2,"SHORT","TOTALLAPS"), # How many laps the race has 
        (0x7C,1,"INT","LAST_LAPTIME"),
        (0x78,1,"INT","BEST_LAPTIME"),
        (0x84,2,"SHORT","RACE_POSITION"), # 01 von 16 2/16
        (0x90,1,"BYTE", "GEAR"),
        (0x91,1,"BYTE", "THROTTLE"),
        (0x92,1,"BYTE", "BRAKE"),
        # V31: GT7 Vehicle/Car Code im Standard-A-Paket
        (0x124,1,"INT32","CAR_CODE"),
        # Erweiterung, nur fürs Logging
        #(0x0020, 1, "FLOAT", "SpeedMPS"),
        #(0x0108, 1, "FLOAT", "Throttle2"),
        #(0x010C, 1, "FLOAT", "Brake2"),
        #(0x0114, 1, "BYTE",  "Gear2"),
        #(0x0124, 3, "FLOAT", "Velocity2"),
        (0x0148, 1, "FLOAT", "Fuel"),
        (0x014C, 1, "FLOAT", "FuelCapacity"),
        (0x0150, 1, "FLOAT", "FuelPerLap"),
        (0x01B0, 4, "FLOAT", "TireWear"),
        (0x0204, 1, "INT32", "LapCount"),
        (0x0208, 1, "INT32", "CurrentLap"),
        # (0x020C, 1, "FLOAT", "LapTimeCurrent"),
        (0x0210, 1, "FLOAT", "LapTimePrevious"),
        #(0x0214, 1, "FLOAT", "LapTimeBest"),
        (0x0218, 1, "INT32", "RacePosition2"),
        # V31: alte Versuchsoffsets 0x0220/0x0224 bewusst deaktiviert.
        #(0x0228, 1, "FLOAT", "TotalTime"),
        #(0x022C, 1, "INT32", "BestLapCarIndex"),
        #(0x0244, 1, "INT32", "CarDamage"),
        #(0x0264, 1, "FLOAT", "BrakeBalance"),
        #(0x026C, 1, "FLOAT", "FuelMixture"),
        #(0x0270, 1, "FLOAT", "EngineMap"),
        #(0x0274, 1, "FLOAT", "TractionControlLevel")
    ]
    # Initialisiert das UDP-Socket und bindet es an Port 33740; fängt Fehler ab, wenn der Port bereits in Benutzung ist 
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logger_socket = s
        server_address = ('0.0.0.0', port)
        s.bind(server_address)
    except OSError as e:
        if e.errno == 10048:
            add_log(f"❌ Port {port} ist bereits belegt. Bitte sicherstellen, dass kein anderes Programm läuft.")
        else:
            add_log(f"❌ Socket-Fehler: {e}")
        return

    s.settimeout(30)  # V19: Nach 30 Sekunden ohne UDP-Daten erneut prüfen und weiter warten 

    # Entschlüsselt ein GT7-UDP-Datenpaket mit Salsa20 und prüft, ob das Paket gültig ist (Magic-Header "G7S0").
    def salsa20_dec(dat):
        KEY = b'Simulator Interface Packet GT7 ver 0.0'
        oiv = dat[0x40:0x44]
        iv1 = int.from_bytes(oiv, byteorder='little')
        iv2 = iv1 ^ 0xDEADBEAF
        IV = bytearray()
        IV.extend(iv2.to_bytes(4, 'little'))
        IV.extend(iv1.to_bytes(4, 'little'))
        ddata = Salsa20_xor(dat, bytes(IV), KEY[0:32])
        magic = int.from_bytes(ddata[0:4], byteorder='little')
        if magic != 0x47375330:
            return bytearray(b'')
        return ddata

    # Sendet einen regelmäßigen Heartbeat ("A") an die PS5, um die UDP-Übertragung der Telemetriedaten aktiv zu halten.
    def send_hb(s):
        send_data = 'A'
        s.sendto(send_data.encode('utf-8'), (ip, SendPort))

    try:
        send_hb(s)
    except Exception as exc:
        add_log(f"⚠️ Initialer Heartbeat konnte nicht gesendet werden: {exc}")
    # Main Loop 
    try:
        while not stop_event.is_set():
            try:
                data, address = s.recvfrom(4096)
                publish_connection(f"PS5 {ip} – GT7-Telemetrie aktiv", "ok")
                pknt += 1
                ddata = salsa20_dec(data)
                # Alle 100 Pakete einen heartbeat senden 
                if pknt > 100:
                    send_hb(s)
                    pknt = 0
                
                # Wenn Daten im paket waren gehts hier weiter 
                if len(ddata) > 0:
                    packet_data = {}
                    for start, size, type, name in packet_data_struct:
                        end = start + size * (data_type_spec[type]['bytes'])
                        if len(ddata) >= end:
                            unpacker = data_type_spec[type]['struct_decrypt'] * size
                            data_decrypted = struct.unpack(unpacker, ddata[start:end])
                            packet_data[name] = data_decrypted
                    # 
                    # Ab hier Variablen parsen 
                    car_id = packet_data.get("CAR_CODE", [None])[0]
                    if car_id not in (None, 0, -1) and car_id != last_detected_car_id:
                        globals()["current_vehicle_id"] = car_id
                        car_name = car_database.get(car_id)
                        if car_name:
                            add_log(f"🚗 Fahrzeug erkannt: {car_name} (Car ID {car_id})")
                            ui_queue.put(("vehicle", car_name))
                            globals()["current_vehicle_name"] = car_name
                        else:
                            add_log(f"⚠️ Unbekannte Fahrzeug-ID erkannt: {car_id}")
                            unknown_name = f"Unbekannt (ID {car_id})"
                            ui_queue.put(("vehicle", unknown_name))
                            globals()["current_vehicle_name"] = unknown_name
                        last_detected_car_id = car_id
                    speed = int(round(float(packet_data["SPEED"][0]) * 3.6))
                    current_lap_speeds.append(speed) #wird für den Durchschnitt gebraucht 
                    rpm = packet_data["RPM"][0]
                    lap = packet_data["LAPS"][0]
                    ll = packet_data.get("LAST_LAPTIME", [None])[0] 
                    lastLaptime = ms_to_timestr(ll)
                    bl = packet_data.get("BEST_LAPTIME", [None])[0]
                    bestLaptime = ms_to_timestr(bl)
                    cl = packet_data.get("LapTimeCurrent", [None])[0] 
                    # current_laptime = ms_to_timestr(cl) 
                    my_pos          = packet_data.get("RACE_POSITION", [None, None])[0]
                    num_cars        = packet_data.get("RACE_POSITION", [None, None])[1]  
                    gas             = packet_data["THROTTLE"][0]
                    brake           = packet_data["BRAKE"][0]
                    gear            = packet_data["GEAR"][0] & 0x0f
                    temps           = packet_data["TYRES_TEMP"]
                    fuel            = packet_data.get("FUEL_LEVEL", [None])[0]
                    fuel            = round(fuel, 2) if fuel is not None else None
                    fuel_capacity   = packet_data.get("FUEL_CAPA", [None])[0]

                    position = packet_data.get("POSITION", (None, None, None))
                    pos_x = position[0] if len(position) >= 3 else None
                    pos_z = position[2] if len(position) >= 3 else None

                                            
                    #Daten für das Runden Array sammeln 
                    if speed > current_lap_max_speed:
                        current_lap_max_speed = speed
                    if speed < current_lap_min_speed:
                        current_lap_min_speed = speed
                    # DATEN PARSING ENDE #####################################
                
                    # Im hauptmenü sind alle drei 65535 
                    main_menu = (lap == 65535 and my_pos == 65535 and num_cars == 65535)
                
                    if lap == 65535 or my_pos == 65535 or num_cars == 65535 or lap == 0: 
                    # an 65535 kann man erkennen das kein Rennen läuft 
                        if in_race: # aber es lief schon eines 
                            add_log("🛑 Rennen wurde beendet")
                            in_race = False
                        
                            # Letzte Logdatei schließen
                            if fout:
                                #Summary der vorherigen Runde in den aktuellen Pfad schreiben 
                                save_race_summary(logpath, lap_history, start_pos, race_id)
                                fout.flush()
                                fout.close()
                                fout = None 

                            # Hier ggf. Datei der Auslaufrunde löschen 
                            if lap_history:                      # erst prüfen, ob die Liste nicht leer ist
                                last_real_lap = lap_history[-1]["lap"]
                                # add_log(f"Letzte Runde in lap_history : {last_real_lap}")
                                auslauf_lap = last_real_lap + 1 
                                logdatei = f"lap-{auslauf_lap}.txt"
                                datei = os.path.join(logpath, logdatei)
                                if os.path.exists(datei):
                                    os.remove(datei)
                            # V32: Erst nach der Summary alles leeren.
                            reset_session_identity()
                            last_detected_car_id = None
                            detected_track_id = None
                            track_prev_point = None
                            track_prev_lap = None
                            track_min_x = float("inf")
                            track_min_z = float("inf")
                            track_max_x = float("-inf")
                            track_max_z = float("-inf")

                               
                    else:
                        if not in_race and lap != 0:  
                            add_log("🚦 Rennen beginnt oder Wiederholung läuft. ")
                            in_race = True
                
                    # im startmenü eines rennens läuft im Hintegrund eine wiederholung und lap zählt hoch 
                    # Prüfen ob das Spiel in einem Menü ist 
                    race_start_menu = (
                        (lap != 65535 and my_pos == 65535 and num_cars == 65535)
                        or
                        (lap == 0     and my_pos == "---" and num_cars == "---")
                    )

                
                    # 
                    # Prüfen ob das Auto an der Box ist und nachtankt
                    #
                    if speed > 0 and tanken: # tanken endet erstes Paket bei wiederanfahrt 
                            fuel_start_of_lap = fuel # neuer fuellstand 
                            add_log(f"⛽🛑 Tanken beendet fuel ist gestiegen auf {fuel} | used: {fuel_used:.2f}")
                            tanken = False
                    if (fuel_prev is not None and fuel > fuel_prev and speed == 0 and in_race) or tanken:
                        # ==> Fuel ist gestiegen: gerade wird wohl getankt
                        # Hier tanken regeln  
                        tanken = True 
                        if tanken_first_pkt == True: 
                            add_log(f"⛽🟢 Tanken begonnen! fuel gestiegen von {fuel_prev} auf {fuel} | used: {fuel_used:.2f}")
                            fuel_before_box = fuel # Am Anfang in der Box den Füllstand merken 
                            tanken_first_pkt = False
                                               
                    else: 
                    # normales paket ohne tanken
                        #if fuel is not None and fuel_start_of_lap is not None:
                        tanken_first_pkt = True # Flag zurücksetzen 

            
                    # --- Zum Schluss: speichere aktuellen Fuel in `fuel_prev` 
                    # ab um zu nächstes Paket zu vergleichen ob der Tankinhalt steigt---
                    if fuel_prev is not None and fuel < fuel_prev:
                        fuel_used += round(fuel_prev - fuel, 2)
                    fuel_prev = fuel
                
                
                    #
                    # Prüfen ob ein Rennen beendet wurde  
                    #
                    if (lastLap is not None and lastLap > lap) or not in_race:
                        paket1_lap1_done = False # bedeutet der nächste Satz mit lap == 1 ist der erste 
                    # 
                    # Wenn eine Wiederholung oder der Rennstart beginnt
                    # NEUES RENNEN 
                    #
                    if lap == 1 and not paket1_lap1_done and not race_start_menu:
                         # nur beim ersten Datensatz lap == 1 ausführen zu Beginn eines Rennens
                        race_id = f"Race_ID_{datetime.now():%Y%m%d%H%M%S}"
                        start_pos = my_pos # Startposition merken 
                        #fuel_start_of_lap = None  ###????????? müsste =fuel sein  
                        fuel_start_of_lap = fuel 
                        current_lap_max_speed = 0
                        current_lap_min_speed = 333
                        lap_history.clear()  # altes rennen löschen 
                        lastLap = None  # wichtig, sonst wird keine neue Runde erkannt
                        # continue  # springe zurück zum Anfang der Schleife
                        logpath = create_log_dir()
                        # logdir_initialized = False
                        add_log(f"🏁🟢 Neues Rennen beginnt")

                        track_prev_point = None
                        track_prev_lap = None
                        track_min_x = float("inf")
                        track_min_z = float("inf")
                        track_max_x = float("-inf")
                        track_max_z = float("-inf")
                        detected_track_id = None
                        reset_session_identity()
                        last_detected_car_id = None
                        paket1_lap1_done = True  

                
                    #
                    # TRACK-EXP-v01: Streckenerkennung
                    #
                    if (
                        in_race
                        and not race_start_menu
                        and lap not in (0, 65535)
                        and pos_x is not None
                        and pos_z is not None
                        and math.isfinite(float(pos_x))
                        and math.isfinite(float(pos_z))
                    ):
                        new_track_point = (float(pos_x), float(pos_z))

                        track_min_x = min(track_min_x, new_track_point[0])
                        track_min_z = min(track_min_z, new_track_point[1])
                        track_max_x = max(track_max_x, new_track_point[0])
                        track_max_z = max(track_max_z, new_track_point[1])

                        if track_prev_lap is None:
                            track_prev_lap = lap

                        if (
                            detected_track_id is None
                            and track_prev_point is not None
                            and lap > track_prev_lap
                            and track_database
                        ):
                            matches = find_track_matches(
                                track_prev_point,
                                new_track_point,
                                (track_min_x, track_min_z, track_max_x, track_max_z),
                                track_database,
                            )

                            if matches:
                                best_iou, best_track_id = matches[0]
                                best_pct = best_iou * 100.0
                                best_name = course_database.get(
                                    best_track_id, f"Track ID {best_track_id}"
                                )

                                if len(matches) == 1 and best_pct >= 96.0:
                                    detected_track_id = best_track_id
                                    globals()["current_track_name"] = best_name
                                    ui_queue.put(("track", best_name))
                                    add_log(
                                        f"🗺️ Strecke erkannt: {best_name} "
                                        f"(Track ID {best_track_id}, {best_pct:.1f} %)"
                                    )
                                else:
                                    same_track, canonical_name = _matches_share_canonical_track(
                                        matches, course_database
                                    )

                                    # EXP-v04:
                                    # Wenn mehrere nahezu identische Kandidaten nur
                                    # verschiedene, geometrisch nicht unterscheidbare
                                    # Varianten derselben Grundstrecke sind, übernehmen
                                    # wir den gemeinsamen Streckennamen.
                                    if same_track and best_pct >= 96.0:
                                        detected_track_id = best_track_id
                                        globals()["current_track_name"] = canonical_name
                                        ui_queue.put(("track", canonical_name))

                                        ids = ", ".join(
                                            str(track_id) for _, track_id in matches
                                        )
                                        add_log(
                                            f"🗺️ Strecke erkannt: {canonical_name} "
                                            f"(mehrere nicht unterscheidbare Layouts: "
                                            f"{ids}; bester Treffer {best_pct:.1f} %)"
                                        )
                                    else:
                                        diag = []
                                        for iou, track_id in matches:
                                            name = course_database.get(
                                                track_id, f"Track ID {track_id}"
                                            )
                                            diag.append(
                                                f"{name}: {iou * 100.0:.1f} %"
                                            )
                                        add_log(
                                            "🗺️ Streckenerkennung noch nicht eindeutig: "
                                            + " | ".join(diag)
                                        )
                            else:
                                add_log(
                                    "🗺️ Nach vollständiger Runde keine passende "
                                    "Strecke gefunden – nächster Versuch folgt."
                                )

                            if detected_track_id is None:
                                track_min_x = new_track_point[0]
                                track_min_z = new_track_point[1]
                                track_max_x = new_track_point[0]
                                track_max_z = new_track_point[1]

                        track_prev_point = new_track_point
                        track_prev_lap = lap

                    # 
                    # NEUE RUNDE 
                    #
                    if lastLap != lap and in_race:
                        add_log(f"🚦 Neue Runde erkannt {lap}")
                        if lastLap is not None:
                            # Durchschnitts-Speed ermitteln
                            if current_lap_speeds:
                                avg_speed = sum(current_lap_speeds) / len(current_lap_speeds)
                            else:
                                avg_speed = 0
                        
                            lap_record = {
                            "pos": my_pos, 
                            "lap": lastLap,
                            "laptime": lastLaptime,
                            "max_speed": current_lap_max_speed,
                            "min_speed": current_lap_min_speed,
                            "fuel_used": round(fuel_used,2),
                            "avg_speed": round(avg_speed, 2)
                            }
                            lap_history.append(lap_record) # Statistikarray schreiben 
                        
                            # Durschnittsverbrauch pro Runde ermitteln 
                            valid_fuel_values = [entry["fuel_used"] for entry in lap_history if isinstance(entry["fuel_used"], float)]
                            if valid_fuel_values:
                                fuel_avg = round(sum(valid_fuel_values) / len(valid_fuel_values), 2)
                            else:
                                fuel_avg = "---"
                
                        # Wenn eine neue Runde beginnt: alte Log-Datei schließen, neue Datei mit passendem Namen erstellen und Header schreiben
                        if fout:
                            fout.flush() # Aus Buffer auf Platte schreiben 
                            fout.close()
                        #if in_race:
                        fout = open(os.path.join(logpath, f'lap-{lap}.txt'), "w")
                        #header = ['speed', 'gas', 'brake', 'gear', 'tyre_FL', 'tyre_FR', 'tyre_RL', 'tyre_RR'] + [k for k in packet_data if k not in ['SPEED', 'THROTTLE', 'BRAKE', 'GEAR', 'TYRES_TEMP']]
                        header = ["LAP", "POS", "SPD", "GEAR", "RPM", "GAS", "BRK","MAX", "MIN", "TYRES           ", "FUEL", "USED", "LLAP     ", "BESTLAP"]
                        fout.write("\t".join(header) + "\n")
                        #Lap Variablen resetten
                        fuel_start_of_lap = fuel  # Startwert merken
                        current_lap_max_speed = 0
                        current_lap_min_speed = 333 
                        current_lap_speeds = []
                        fuel_prev = None
                        fuel_used = 0.00 
                        lastLap = lap  # NEUE RUNDE GESETZT ! 

                    # Nächste zeile für das Log, schreibt einfach die nächste zeile in die offene Datei      
                    if fout and in_race:
                        tyre_str = "|".join(f"{t:.1f}" for t in temps)
                        line = [
                            f"{lap:>2}",
                            f"{my_pos:02}/{num_cars:02}",
                            f"{speed:>3}",
                            str(gear),
                            f"{rpm:.0f}",
                            str(gas),
                            str(brake),
                            f"{current_lap_max_speed:.0f}",
                            f"{current_lap_min_speed:.0f}",
                            tyre_str,
                            f"{fuel:.2f}" if fuel is not None else "---",
                            f"{fuel_used:.2f}" if fuel_used is not None else "---",
                            lastLaptime,
                            bestLaptime
                        ]
                        #if in_race: 
                        fout.write("\t".join(line) + "\n")  # Nächste Zeile ins Log schreiben 
                        fout.flush()  # Aus Buffer auf Platte schreiben  

                # V20: Live-Daten nur an die GUI senden; kein Löschen/Neuzeichnen der Konsole.
                if pknt % 40 == 0:
                    tyre_vals = [round(t, 1) for t in temps]
                    publish_status(
                        lap=lap,
                        last_lap_no=lastLap if lastLap is not None else "---",
                        position=f"{my_pos:02}/{num_cars:02}",
                        last_laptime=lastLaptime,
                        best_laptime=bestLaptime,
                        speed=f"{speed} km/h",
                        gear=gear,
                        rpm=f"{rpm:.0f}",
                        gas=f"{(gas / 255) * 100:.0f}%",
                        brake=f"{(brake / 255) * 100:.0f}%",
                        gas_pct=(gas / 255) * 100,
                        brake_pct=(brake / 255) * 100,
                        rpm_pct=min(100, max(0, (rpm / 10000) * 100)),
                        max_speed=f"{current_lap_max_speed:.0f} km/h",
                        min_speed=f"{current_lap_min_speed:.0f} km/h",
                        tyre_fl=f"{tyre_vals[0]:.1f}°",
                        tyre_fr=f"{tyre_vals[1]:.1f}°",
                        tyre_rl=f"{tyre_vals[2]:.1f}°",
                        tyre_rr=f"{tyre_vals[3]:.1f}°",
                        fuel=f"{fuel:.2f} / {fuel_capacity:.1f} l" if fuel is not None else "---",
                        fuel_pct=((fuel / fuel_capacity) * 100 if fuel is not None and fuel_capacity else 0),
                        fuel_used=f"{fuel_used:.2f} l",
                        fuel_avg=f"{fuel_avg} l",
                        in_race=in_race,
                        race_started=paket1_lap1_done,
                        race_menu=race_start_menu,
                        main_menu=main_menu,
                        tanken=tanken,
                    )
                        
  
            # Error Handling
            except socket.timeout:
                # Nach 30 Sekunden ohne UDP-Daten unterscheiden:
                # PS5 nicht mehr erreichbar oder PS5 erreichbar, aber GT7 sendet nichts.
                try:
                    result = subprocess.run(
                        ["ping", "-n", "1", "-w", "1000", ip],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="ignore"
                    )
                    if result.returncode == 0:
                        publish_connection(f"PS5 {ip} erreicht – keine GT7-Telemetriedaten", "waiting")
                        add_log("PS5 erreicht, aber keine GT7-Telemetriedaten empfangen. Neuer Versuch in 30 Sekunden.")
                    else:
                        publish_connection(f"PS5 {ip} nicht erreichbar", "error")
                        add_log("PS5 nicht erreichbar. Neuer Versuch in 30 Sekunden.")
                except Exception:
                    publish_connection("PS5-Verbindungsstatus konnte nicht geprüft werden", "error")
                    add_log("PS5-Verbindungsstatus konnte nicht geprüft werden.")

                try:
                    send_hb(s)
                except Exception:
                    pass
                pknt = 0
                continue

            except Exception:
                if stop_event.is_set():
                    break
                add_log("❌ Fehler im Hauptloop: " + traceback.format_exc().strip().splitlines()[-1])
                try:
                    send_hb(s)
                except:
                    add_log("⚠️ Heartbeat konnte nicht gesendet werden.")
                pknt = 0
                try:
                    if fout:
                        fout.close()
                except:
                    add_log("⚠️ Fehler beim Schließen der Log-Datei.")
                return  # Unerwartete Fehler beenden nur den Logger-Thread; GUI bleibt sichtbar
    except KeyboardInterrupt:
        add_log("Logger mit Strg+C beendet.")

    finally:
        try:
            if fout:
                fout.flush()
                fout.close()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass
        logger_socket = None
        if lap_history:
            add_log(f"Letzte Rundenübersicht enthält {len(lap_history)} Runde(n).")

def main():
    register_private_fonts()
    root = tk.Tk()
    app = DexaLoggerGUI(root)
    worker = threading.Thread(target=logger_worker, name="GT7-Logger", daemon=True)
    worker.start()
    root.mainloop()

if __name__ == "__main__":
    main()
