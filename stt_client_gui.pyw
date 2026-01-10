"""
Remote Speech-to-Text Client with GUI
Push-to-talk with configurable hotkey, microphone selection, and log viewer.
Features: Config persistence, audio feedback, system tray, transcription history.

Cross-platform support: Windows, macOS, Linux
"""

import io
import json
import sqlite3
import threading
import time
import sys
import platform
import tkinter as tk
from tkinter import ttk, scrolledtext
import queue
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import numpy as np
from scipy.io import wavfile
import requests
import warnings

# Platform detection
SYSTEM = platform.system()  # "Windows", "Darwin" (macOS), "Linux"

# Cross-platform audio feedback
SOUNDS_DIR = Path(__file__).parent / "sounds"

def _play_sound_windows(sound_name: str, frequencies: dict):
    """Windows: Use winsound for beeps."""
    try:
        import winsound
        freq = frequencies.get(sound_name, 800)
        duration = frequencies.get(f"{sound_name}_duration", 150)
        winsound.Beep(freq, duration)
    except Exception:
        pass

def _play_sound_playsound(sound_name: str):
    """Cross-platform: Use playsound with bundled WAV files."""
    try:
        from playsound import playsound
        sound_file = SOUNDS_DIR / f"{sound_name}.wav"
        if sound_file.exists():
            playsound(str(sound_file), block=False)
    except Exception:
        pass

def play_sound(sound_name: str):
    """Play a sound (cross-platform)."""
    frequencies = {
        "start": 800, "start_duration": 150,
        "stop": 600, "stop_duration": 150,
        "success": 1000, "success_duration": 100,
        "error": 400, "error_duration": 300
    }

    if SYSTEM == "Windows":
        _play_sound_windows(sound_name, frequencies)
    else:
        _play_sound_playsound(sound_name)

# Cross-platform keyboard hooks
if SYSTEM == "Windows":
    import keyboard
    KEYBOARD_AVAILABLE = True
else:
    try:
        from pynput import keyboard as pynput_keyboard
        KEYBOARD_AVAILABLE = True
    except ImportError:
        KEYBOARD_AVAILABLE = False
        print("Warning: pynput not installed. Hotkeys will not work on this platform.")

# Optional imports for system tray
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("Note: Install pystray and Pillow for system tray support")

# Suppress SSL warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

# =============================================================================
# CONFIGURATION
# =============================================================================

# Config file location
CONFIG_DIR = Path.home() / ".whisper-stt"
CONFIG_PATH = CONFIG_DIR / "config.json"

@dataclass
class Config:
    """Application configuration with JSON persistence."""
    server_url: str = "http://localhost:8000/v1/audio/transcriptions"
    hotkey: str = "ctrl+shift+space"
    sample_rate: int = 16000
    channels: int = 1
    language: str = "en"
    min_duration: float = 0.3
    timeout: int = 30
    device_index: Optional[int] = None
    # New settings for Phase 1+
    audio_feedback: bool = True
    recording_mode: str = "hold"  # "hold" or "toggle"
    minimize_to_tray: bool = True
    always_on_top: bool = False
    corrections: Dict[str, str] = field(default_factory=dict)
    # GPT refinement (Phase 5)
    gpt_refinement: bool = False
    openai_api_key: str = ""

    def save(self):
        """Save configuration to JSON file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Convert to dict, handling None values
        data = asdict(self)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        log(f"Config saved to {CONFIG_PATH}")

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from JSON file, or create with defaults."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Filter out unknown keys (forward compatibility)
                valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in data.items() if k in valid_keys}
                return cls(**filtered)
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
                return cls()
        return cls()

# Load config at startup
config = Config.load()

# =============================================================================
# TRANSCRIPTION HISTORY DATABASE
# =============================================================================

HISTORY_DB_PATH = CONFIG_DIR / "history.db"

def init_history_db():
    """Initialize the transcription history database."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            duration REAL,
            text TEXT NOT NULL,
            language TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_transcription_to_history(text: str, duration: float = 0.0, language: str = ""):
    """Save a transcription to the history database."""
    try:
        conn = sqlite3.connect(HISTORY_DB_PATH)
        conn.execute(
            "INSERT INTO transcriptions (timestamp, duration, text, language) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), duration, text, language or config.language or "")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving to history: {e}")

def get_transcription_history(limit: int = 100, search: str = "") -> List[tuple]:
    """Get transcription history from database."""
    try:
        conn = sqlite3.connect(HISTORY_DB_PATH)
        if search:
            cursor = conn.execute(
                "SELECT id, timestamp, duration, text, language FROM transcriptions WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{search}%", limit)
            )
        else:
            cursor = conn.execute(
                "SELECT id, timestamp, duration, text, language FROM transcriptions ORDER BY id DESC LIMIT ?",
                (limit,)
            )
        results = cursor.fetchall()
        conn.close()
        return results
    except Exception as e:
        print(f"Error reading history: {e}")
        return []

def clear_transcription_history():
    """Clear all transcription history."""
    try:
        conn = sqlite3.connect(HISTORY_DB_PATH)
        conn.execute("DELETE FROM transcriptions")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error clearing history: {e}")

# Initialize database on startup
init_history_db()

# =============================================================================
# CROSS-PLATFORM AUTO-START
# =============================================================================

def is_autostart_enabled() -> bool:
    """Check if auto-start is enabled (cross-platform)."""
    if SYSTEM == "Windows":
        return _is_autostart_enabled_windows()
    elif SYSTEM == "Darwin":
        return _is_autostart_enabled_macos()
    elif SYSTEM == "Linux":
        return _is_autostart_enabled_linux()
    return False

def set_autostart(enabled: bool):
    """Enable or disable auto-start on login (cross-platform)."""
    if SYSTEM == "Windows":
        _set_autostart_windows(enabled)
    elif SYSTEM == "Darwin":
        _set_autostart_macos(enabled)
    elif SYSTEM == "Linux":
        _set_autostart_linux(enabled)
    else:
        log(f"Auto-start not supported on {SYSTEM}", "WARN")

# Windows implementation
def _is_autostart_enabled_windows() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ
        )
        try:
            winreg.QueryValueEx(key, "WhisperSTT")
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False

def _set_autostart_windows(enabled: bool):
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )

        if enabled:
            python_exe = sys.executable
            if "python.exe" in python_exe.lower():
                python_exe = python_exe.replace("python.exe", "pythonw.exe")
            script_path = str(Path(__file__).resolve())
            cmd = f'"{python_exe}" "{script_path}"'
            winreg.SetValueEx(key, "WhisperSTT", 0, winreg.REG_SZ, cmd)
            log("Auto-start enabled (Windows)")
        else:
            try:
                winreg.DeleteValue(key, "WhisperSTT")
                log("Auto-start disabled (Windows)")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        log(f"Failed to set Windows auto-start: {e}", "ERROR")

# macOS implementation
def _is_autostart_enabled_macos() -> bool:
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.whisperstt.client.plist"
    return plist_path.exists()

def _set_autostart_macos(enabled: bool):
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = plist_dir / "com.whisperstt.client.plist"

    try:
        if enabled:
            plist_dir.mkdir(parents=True, exist_ok=True)
            script_path = str(Path(__file__).resolve())
            python_path = sys.executable

            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whisperstt.client</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>"""
            plist_path.write_text(plist_content)
            log("Auto-start enabled (macOS)")
        else:
            if plist_path.exists():
                plist_path.unlink()
            log("Auto-start disabled (macOS)")
    except Exception as e:
        log(f"Failed to set macOS auto-start: {e}", "ERROR")

# Linux implementation
def _is_autostart_enabled_linux() -> bool:
    desktop_path = Path.home() / ".config" / "autostart" / "whisperstt.desktop"
    return desktop_path.exists()

def _set_autostart_linux(enabled: bool):
    autostart_dir = Path.home() / ".config" / "autostart"
    desktop_path = autostart_dir / "whisperstt.desktop"

    try:
        if enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            script_path = str(Path(__file__).resolve())
            python_path = sys.executable

            desktop_content = f"""[Desktop Entry]
Type=Application
Name=WhisperSTT
Comment=Speech-to-Text Client
Exec={python_path} {script_path}
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
            desktop_path.write_text(desktop_content)
            log("Auto-start enabled (Linux)")
        else:
            if desktop_path.exists():
                desktop_path.unlink()
            log("Auto-start disabled (Linux)")
    except Exception as e:
        log(f"Failed to set Linux auto-start: {e}", "ERROR")

# =============================================================================
# GPT TEXT REFINEMENT
# =============================================================================

# Optional OpenAI import
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

def refine_text_with_gpt(text: str) -> str:
    """Refine transcribed text using GPT for grammar and punctuation.

    Returns original text if refinement is disabled or fails.
    """
    if not config.gpt_refinement or not config.openai_api_key or not OPENAI_AVAILABLE:
        return text

    if not text.strip():
        return text

    try:
        client = OpenAI(api_key=config.openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "Fix grammar, punctuation, and capitalization in the following transcribed text. Keep the original meaning and tone intact. Only return the corrected text, nothing else."
                },
                {"role": "user", "content": text}
            ],
            max_tokens=len(text) * 2,
            temperature=0.3
        )
        refined = response.choices[0].message.content.strip()
        if refined:
            log(f"GPT refined: {text[:30]}... -> {refined[:30]}...")
            return refined
    except Exception as e:
        log(f"GPT refinement failed: {e}", "WARN")

    return text

# =============================================================================
# AUDIO FEEDBACK
# =============================================================================

def play_start_sound():
    """Play sound when recording starts."""
    if config.audio_feedback:
        play_sound("start")

def play_stop_sound():
    """Play sound when recording stops."""
    if config.audio_feedback:
        play_sound("stop")

def play_success_sound():
    """Play sound on successful transcription."""
    if config.audio_feedback:
        play_sound("success")

def play_error_sound():
    """Play error sound on transcription failure."""
    if config.audio_feedback:
        play_sound("error")

# =============================================================================
# SYSTEM TRAY
# =============================================================================

# Tray icon reference (set by GUI class)
tray_icon = None

def create_tray_icon_image(status: str = "ready") -> "Image":
    """Create a colored circle icon for the system tray.

    Args:
        status: "ready" (green), "recording" (red), or "transcribing" (yellow)
    """
    if not TRAY_AVAILABLE:
        return None

    # Color mapping
    colors = {
        "ready": "#55aa55",      # Green
        "recording": "#ff5555",   # Red
        "transcribing": "#ffaa00" # Yellow/Orange
    }
    color = colors.get(status, colors["ready"])

    # Create 64x64 icon
    size = 64
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Draw filled circle with slight border
    margin = 4
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=color,
        outline="#333333",
        width=2
    )

    return image

# =============================================================================
# LOGGING
# =============================================================================

log_queue = queue.Queue()

def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_queue.put(f"[{timestamp}] [{level}] {message}")

# =============================================================================
# AUDIO
# =============================================================================

is_recording = False
audio_data = []
audio_stream = None

def get_input_devices() -> List[dict]:
    """Get list of available input devices."""
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        if dev['max_input_channels'] > 0:
            devices.append({
                'index': i,
                'name': dev['name'],
                'channels': dev['max_input_channels'],
                'sample_rate': dev['default_samplerate']
            })
    return devices

def audio_callback(indata, frames, time_info, status):
    if status:
        log(f"Audio status: {status}", "WARN")
    if is_recording:
        audio_data.append(indata.copy())

def start_audio_stream(device_index: Optional[int] = None):
    global audio_stream
    if audio_stream is not None:
        audio_stream.stop()
        audio_stream.close()

    try:
        audio_stream = sd.InputStream(
            device=device_index,
            samplerate=config.sample_rate,
            channels=config.channels,
            dtype=np.float32,
            callback=audio_callback
        )
        audio_stream.start()
        dev_name = "Default" if device_index is None else sd.query_devices(device_index)['name']
        log(f"Audio stream started: {dev_name}")
        return True
    except Exception as e:
        log(f"Failed to start audio: {e}", "ERROR")
        return False

def start_recording():
    global is_recording, audio_data
    audio_data = []
    is_recording = True
    play_start_sound()
    log("Recording started...")

def stop_recording() -> Optional[bytes]:
    global is_recording
    is_recording = False
    play_stop_sound()
    log("Recording stopped.")

    if not audio_data:
        return None

    audio_np = np.concatenate(audio_data, axis=0)
    duration = len(audio_np) / config.sample_rate

    if duration < config.min_duration:
        log(f"Recording too short ({duration:.2f}s), ignoring.", "WARN")
        return None

    log(f"Recorded {duration:.2f}s of audio")

    wav_buffer = io.BytesIO()
    wavfile.write(wav_buffer, config.sample_rate, audio_np)
    wav_buffer.seek(0)
    return wav_buffer.read()

# =============================================================================
# TEXT PROCESSING
# =============================================================================

import re

def apply_corrections(text: str) -> str:
    """Apply word corrections from config.corrections dictionary.

    Replacements are case-insensitive with word boundary matching.
    """
    if not config.corrections or not text:
        return text

    for wrong, right in config.corrections.items():
        # Word boundary replacement (case-insensitive)
        pattern = re.compile(r'\b' + re.escape(wrong) + r'\b', re.IGNORECASE)
        text = pattern.sub(right, text)

    return text

# =============================================================================
# TRANSCRIPTION
# =============================================================================

# Store last transcription for copy functionality
last_transcription = ""

def transcribe(audio_bytes: bytes) -> Optional[str]:
    global last_transcription
    try:
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {
            "model": "Systran/faster-whisper-large-v3",
            "response_format": "json"
        }

        if config.language:
            data["language"] = config.language

        log(f"Sending to server...")
        response = requests.post(
            config.server_url,
            files=files,
            data=data,
            timeout=config.timeout,
            verify=False
        )
        response.raise_for_status()

        result = response.json()
        text = result.get("text", "").strip()

        # Apply corrections
        if config.corrections:
            original = text
            text = apply_corrections(text)
            if text != original:
                log(f"Original: {original}")
                log(f"Corrected: {text}")
            else:
                log(f"Transcription: {text}")
        else:
            log(f"Transcription: {text}")

        # Apply GPT refinement (if enabled)
        if config.gpt_refinement and config.openai_api_key:
            text = refine_text_with_gpt(text)

        # Store for copy functionality
        last_transcription = text

        # Save to history
        if text:
            save_transcription_to_history(text, language=config.language)

        play_success_sound()
        return text

    except requests.exceptions.Timeout:
        log("Request timed out", "ERROR")
        play_error_sound()
    except requests.exceptions.ConnectionError:
        log("Could not connect to server", "ERROR")
        play_error_sound()
    except Exception as e:
        log(f"Error: {e}", "ERROR")
        play_error_sound()

    return None

def type_text(text: str):
    if text:
        time.sleep(0.1)
        keyboard.write(text)
        log(f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}")

# =============================================================================
# HOTKEY
# =============================================================================

# Callback to update tray icon (set by GUI)
update_tray_callback = None

def stop_and_transcribe():
    """Stop recording and transcribe in background thread."""
    global tray_icon
    audio_bytes = stop_recording()
    if audio_bytes:
        # Update tray to transcribing state
        if tray_icon and TRAY_AVAILABLE:
            tray_icon.icon = create_tray_icon_image("transcribing")

        def do_transcribe():
            global tray_icon
            text = transcribe(audio_bytes)
            type_text(text)
            # Reset tray to ready state
            if tray_icon and TRAY_AVAILABLE:
                tray_icon.icon = create_tray_icon_image("ready")

        threading.Thread(target=do_transcribe, daemon=True).start()

def on_hotkey_press():
    """Handle hotkey press - behavior depends on recording_mode."""
    global is_recording, tray_icon

    if config.recording_mode == "toggle":
        # Toggle mode: press once to start, press again to stop
        if is_recording:
            stop_and_transcribe()
        else:
            start_recording()
            if tray_icon and TRAY_AVAILABLE:
                tray_icon.icon = create_tray_icon_image("recording")
    else:
        # Hold mode: start on press
        if not is_recording:
            start_recording()
            if tray_icon and TRAY_AVAILABLE:
                tray_icon.icon = create_tray_icon_image("recording")

def on_hotkey_release():
    """Handle hotkey release - only used in hold mode."""
    global tray_icon

    if config.recording_mode == "hold" and is_recording:
        stop_and_transcribe()

def setup_hotkey():
    """Setup hotkey (cross-platform)."""
    if SYSTEM == "Windows":
        _setup_hotkey_windows()
    else:
        _setup_hotkey_pynput()

def _setup_hotkey_windows():
    """Windows: Use keyboard library."""
    keyboard.unhook_all()
    hotkey_parts = config.hotkey.split("+")
    keyboard.add_hotkey(config.hotkey, on_hotkey_press, suppress=False, trigger_on_release=False)

    def on_key_event(e):
        if e.event_type == "up" and is_recording and config.recording_mode == "hold":
            if e.name.lower() in [k.strip().lower() for k in hotkey_parts]:
                on_hotkey_release()

    keyboard.hook(on_key_event)
    mode_str = "toggle" if config.recording_mode == "toggle" else "hold-to-talk"
    log(f"Hotkey registered: {config.hotkey} ({mode_str})")

# Global reference for pynput listener (non-Windows)
_pynput_listener = None

def _setup_hotkey_pynput():
    """Linux/macOS: Use pynput library."""
    global _pynput_listener

    if not KEYBOARD_AVAILABLE:
        log("Keyboard hooks not available on this platform", "ERROR")
        return

    # Stop existing listener if any
    if _pynput_listener:
        _pynput_listener.stop()

    # Parse hotkey string like "ctrl+shift+space"
    parts = config.hotkey.lower().split('+')
    required_modifiers = set()
    trigger_key = None

    for part in parts:
        part = part.strip()
        if part in ('ctrl', 'control'):
            required_modifiers.add(pynput_keyboard.Key.ctrl)
            required_modifiers.add(pynput_keyboard.Key.ctrl_l)
            required_modifiers.add(pynput_keyboard.Key.ctrl_r)
        elif part == 'shift':
            required_modifiers.add(pynput_keyboard.Key.shift)
            required_modifiers.add(pynput_keyboard.Key.shift_l)
            required_modifiers.add(pynput_keyboard.Key.shift_r)
        elif part == 'alt':
            required_modifiers.add(pynput_keyboard.Key.alt)
            required_modifiers.add(pynput_keyboard.Key.alt_l)
            required_modifiers.add(pynput_keyboard.Key.alt_r)
        elif part in ('cmd', 'command', 'meta', 'super'):
            required_modifiers.add(pynput_keyboard.Key.cmd)
            required_modifiers.add(pynput_keyboard.Key.cmd_l)
            required_modifiers.add(pynput_keyboard.Key.cmd_r)
        elif part == 'space':
            trigger_key = pynput_keyboard.Key.space
        else:
            # Assume it's a character key
            trigger_key = part

    current_keys = set()

    def check_modifiers():
        """Check if required modifiers are pressed."""
        for mod_group in [
            {pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.ctrl_r},
            {pynput_keyboard.Key.shift, pynput_keyboard.Key.shift_l, pynput_keyboard.Key.shift_r},
            {pynput_keyboard.Key.alt, pynput_keyboard.Key.alt_l, pynput_keyboard.Key.alt_r},
            {pynput_keyboard.Key.cmd, pynput_keyboard.Key.cmd_l, pynput_keyboard.Key.cmd_r},
        ]:
            if mod_group & required_modifiers:  # This modifier type is required
                if not (mod_group & current_keys):  # But not pressed
                    return False
        return True

    def on_press(key):
        current_keys.add(key)

        # Check for trigger key
        is_trigger = False
        if isinstance(trigger_key, str):
            if hasattr(key, 'char') and key.char == trigger_key:
                is_trigger = True
        else:
            if key == trigger_key:
                is_trigger = True

        if is_trigger and check_modifiers():
            on_hotkey_press()

    def on_release(key):
        current_keys.discard(key)

        # Check for trigger key release (hold mode)
        is_trigger = False
        if isinstance(trigger_key, str):
            if hasattr(key, 'char') and key.char == trigger_key:
                is_trigger = True
        else:
            if key == trigger_key:
                is_trigger = True

        if is_trigger:
            on_hotkey_release()

    _pynput_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    _pynput_listener.start()

    mode_str = "toggle" if config.recording_mode == "toggle" else "hold-to-talk"
    log(f"Hotkey registered: {config.hotkey} ({mode_str}) [pynput]")

# =============================================================================
# GUI
# =============================================================================

class WhisperClientGUI:
    def __init__(self):
        global tray_icon

        self.root = tk.Tk()
        self.root.title("Whisper STT Client")
        self.root.geometry("600x500")
        self.root.minsize(500, 400)

        # Status indicator
        self.status_var = tk.StringVar(value="Ready")
        self.recording_var = tk.BooleanVar(value=False)
        self.mic_testing = False

        # System tray
        self.tray_icon = None

        self.setup_ui()
        self.setup_update_loop()
        self.setup_tray_icon()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Status bar at top
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 10))

        self.status_indicator = tk.Canvas(status_frame, width=20, height=20)
        self.status_indicator.pack(side=tk.LEFT, padx=(0, 10))
        self.draw_status_indicator(False)

        ttk.Label(status_frame, textvariable=self.status_var, font=('Segoe UI', 10, 'bold')).pack(side=tk.LEFT)

        # Notebook for tabs
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Settings tab
        settings_frame = ttk.Frame(notebook, padding="10")
        notebook.add(settings_frame, text="Settings")

        # Microphone selection
        mic_frame = ttk.LabelFrame(settings_frame, text="Microphone", padding="10")
        mic_frame.pack(fill=tk.X, pady=(0, 10))

        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(mic_frame, textvariable=self.mic_var, state='readonly', width=50)
        self.mic_combo.pack(fill=tk.X)
        self.mic_combo.bind('<<ComboboxSelected>>', self.on_mic_change)

        # Mic buttons row
        mic_btn_frame = ttk.Frame(mic_frame)
        mic_btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(mic_btn_frame, text="Refresh", command=self.refresh_mics).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(mic_btn_frame, text="Test Microphone", command=self.test_microphone).pack(side=tk.LEFT, padx=(0, 5))

        # Volume meter
        self.volume_meter = ttk.Progressbar(mic_frame, mode='determinate', maximum=100)
        self.volume_meter.pack(fill=tk.X, pady=(5, 0))
        self.volume_label = ttk.Label(mic_frame, text="Volume: --")
        self.volume_label.pack()

        # Hotkey settings
        hotkey_frame = ttk.LabelFrame(settings_frame, text="Hotkey", padding="10")
        hotkey_frame.pack(fill=tk.X, pady=(0, 10))

        self.hotkey_var = tk.StringVar(value=config.hotkey)
        ttk.Label(hotkey_frame, text="Push-to-talk:").pack(side=tk.LEFT)
        hotkey_entry = ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=20)
        hotkey_entry.pack(side=tk.LEFT, padx=10)
        ttk.Button(hotkey_frame, text="Apply", command=self.apply_hotkey).pack(side=tk.LEFT)

        # Language settings
        lang_frame = ttk.LabelFrame(settings_frame, text="Language", padding="10")
        lang_frame.pack(fill=tk.X, pady=(0, 10))

        self.lang_var = tk.StringVar(value=config.language or "auto")
        languages = [("English", "en"), ("Romanian", "ro"), ("Auto-detect", "")]
        for text, value in languages:
            ttk.Radiobutton(lang_frame, text=text, value=value, variable=self.lang_var,
                          command=self.on_lang_change).pack(side=tk.LEFT, padx=10)

        # Server URL
        server_frame = ttk.LabelFrame(settings_frame, text="Server", padding="10")
        server_frame.pack(fill=tk.X, pady=(0, 10))

        self.server_var = tk.StringVar(value=config.server_url)
        ttk.Entry(server_frame, textvariable=self.server_var, width=60).pack(fill=tk.X)
        ttk.Button(server_frame, text="Test Connection", command=self.test_connection).pack(pady=(5, 0))

        # Options frame
        options_frame = ttk.LabelFrame(settings_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        # Recording mode (hold vs toggle)
        mode_frame = ttk.Frame(options_frame)
        mode_frame.pack(fill=tk.X, pady=(0, 5))
        ttk.Label(mode_frame, text="Recording mode:").pack(side=tk.LEFT)
        self.recording_mode_var = tk.StringVar(value=config.recording_mode)
        ttk.Radiobutton(
            mode_frame, text="Hold-to-talk", value="hold",
            variable=self.recording_mode_var, command=self.on_recording_mode_change
        ).pack(side=tk.LEFT, padx=(10, 5))
        ttk.Radiobutton(
            mode_frame, text="Toggle (press to start/stop)", value="toggle",
            variable=self.recording_mode_var, command=self.on_recording_mode_change
        ).pack(side=tk.LEFT)

        # Audio feedback checkbox
        self.audio_feedback_var = tk.BooleanVar(value=config.audio_feedback)
        ttk.Checkbutton(
            options_frame,
            text="Audio feedback (beeps on record start/stop)",
            variable=self.audio_feedback_var,
            command=self.on_audio_feedback_change
        ).pack(anchor=tk.W)

        # Minimize to tray checkbox
        self.minimize_to_tray_var = tk.BooleanVar(value=config.minimize_to_tray)
        ttk.Checkbutton(
            options_frame,
            text="Minimize to system tray on close",
            variable=self.minimize_to_tray_var,
            command=self.on_minimize_to_tray_change
        ).pack(anchor=tk.W)

        # Always on top checkbox
        self.always_on_top_var = tk.BooleanVar(value=config.always_on_top)
        ttk.Checkbutton(
            options_frame,
            text="Always on top",
            variable=self.always_on_top_var,
            command=self.on_always_on_top_change
        ).pack(anchor=tk.W)

        # Auto-start checkbox
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        ttk.Checkbutton(
            options_frame,
            text="Start with Windows",
            variable=self.autostart_var,
            command=self.on_autostart_change
        ).pack(anchor=tk.W)

        # Buttons row
        btn_frame = ttk.Frame(options_frame)
        btn_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(btn_frame, text="Test Beeps", command=self.test_beeps).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Copy Last Transcription", command=self.copy_last_transcription).pack(side=tk.LEFT)

        # GPT Refinement frame
        gpt_frame = ttk.LabelFrame(settings_frame, text="GPT Text Refinement (Optional)", padding="10")
        gpt_frame.pack(fill=tk.X, pady=(0, 10))

        # GPT enable checkbox
        self.gpt_enabled_var = tk.BooleanVar(value=config.gpt_refinement)
        gpt_check = ttk.Checkbutton(
            gpt_frame,
            text="Enable GPT refinement (improves grammar/punctuation)",
            variable=self.gpt_enabled_var,
            command=self.on_gpt_enabled_change
        )
        gpt_check.pack(anchor=tk.W)

        if not OPENAI_AVAILABLE:
            gpt_check.configure(state='disabled')
            ttk.Label(gpt_frame, text="(Install 'openai' package to enable)", foreground='gray').pack(anchor=tk.W)

        # API Key entry
        api_key_frame = ttk.Frame(gpt_frame)
        api_key_frame.pack(fill=tk.X, pady=(5, 0))
        ttk.Label(api_key_frame, text="OpenAI API Key:").pack(side=tk.LEFT)
        self.api_key_var = tk.StringVar(value=config.openai_api_key)
        api_key_entry = ttk.Entry(api_key_frame, textvariable=self.api_key_var, width=40, show="*")
        api_key_entry.pack(side=tk.LEFT, padx=(5, 10))
        ttk.Button(api_key_frame, text="Show/Hide", command=self.toggle_api_key_visibility).pack(side=tk.LEFT)
        self.api_key_entry = api_key_entry
        self.api_key_visible = False

        # Save settings button
        save_frame = ttk.Frame(settings_frame)
        save_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(save_frame, text="Save Settings", command=self.save_settings).pack(side=tk.RIGHT)

        # Corrections tab
        corrections_frame = ttk.Frame(notebook, padding="10")
        notebook.add(corrections_frame, text="Corrections")

        # Instructions
        ttk.Label(
            corrections_frame,
            text="Add word corrections below. Transcriptions will be auto-corrected.\nFormat: wrong word -> correct word",
            font=('Segoe UI', 9)
        ).pack(anchor=tk.W, pady=(0, 10))

        # Corrections list
        list_frame = ttk.Frame(corrections_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        # Treeview for corrections
        columns = ("wrong", "right")
        self.corrections_tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=10)
        self.corrections_tree.heading("wrong", text="Wrong")
        self.corrections_tree.heading("right", text="Correct")
        self.corrections_tree.column("wrong", width=200)
        self.corrections_tree.column("right", width=200)
        self.corrections_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbar
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.corrections_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.corrections_tree.configure(yscrollcommand=scrollbar.set)

        # Load existing corrections
        self.load_corrections_to_tree()

        # Add/Edit/Delete controls
        edit_frame = ttk.Frame(corrections_frame)
        edit_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(edit_frame, text="Wrong:").pack(side=tk.LEFT)
        self.wrong_entry = ttk.Entry(edit_frame, width=20)
        self.wrong_entry.pack(side=tk.LEFT, padx=(5, 10))

        ttk.Label(edit_frame, text="Correct:").pack(side=tk.LEFT)
        self.right_entry = ttk.Entry(edit_frame, width=20)
        self.right_entry.pack(side=tk.LEFT, padx=(5, 10))

        ttk.Button(edit_frame, text="Add", command=self.add_correction).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(edit_frame, text="Delete Selected", command=self.delete_correction).pack(side=tk.LEFT)

        # History tab
        history_frame = ttk.Frame(notebook, padding="10")
        notebook.add(history_frame, text="History")

        # Search bar
        search_frame = ttk.Frame(history_frame)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT)
        self.history_search_var = tk.StringVar()
        self.history_search_entry = ttk.Entry(search_frame, textvariable=self.history_search_var, width=30)
        self.history_search_entry.pack(side=tk.LEFT, padx=(5, 10))
        ttk.Button(search_frame, text="Search", command=self.search_history).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(search_frame, text="Refresh", command=self.refresh_history).pack(side=tk.LEFT)

        # History list
        history_list_frame = ttk.Frame(history_frame)
        history_list_frame.pack(fill=tk.BOTH, expand=True)

        # Treeview for history
        columns = ("time", "text")
        self.history_tree = ttk.Treeview(history_list_frame, columns=columns, show="headings", height=12)
        self.history_tree.heading("time", text="Time")
        self.history_tree.heading("text", text="Transcription")
        self.history_tree.column("time", width=150, minwidth=100)
        self.history_tree.column("text", width=400, minwidth=200)
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbar
        history_scrollbar = ttk.Scrollbar(history_list_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        history_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_tree.configure(yscrollcommand=history_scrollbar.set)

        # History buttons
        history_btn_frame = ttk.Frame(history_frame)
        history_btn_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(history_btn_frame, text="Copy Selected", command=self.copy_history_item).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(history_btn_frame, text="Clear History", command=self.clear_history).pack(side=tk.LEFT)

        # Log tab
        log_frame = ttk.Frame(notebook, padding="10")
        notebook.add(log_frame, text="Log")

        self.log_text = scrolledtext.ScrolledText(log_frame, height=20, state='disabled',
                                                   font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        ttk.Button(log_frame, text="Clear Log", command=self.clear_log).pack(pady=(5, 0))

        # Initialize
        self.refresh_mics()
        self.refresh_history()

    def draw_status_indicator(self, recording: bool):
        self.status_indicator.delete("all")
        color = "#ff5555" if recording else "#55aa55"
        self.status_indicator.create_oval(2, 2, 18, 18, fill=color, outline=color)

    def refresh_mics(self):
        devices = get_input_devices()
        self.devices = devices
        names = [f"{d['index']}: {d['name']}" for d in devices]
        self.mic_combo['values'] = names

        # Select current device or default
        if config.device_index is not None:
            for i, d in enumerate(devices):
                if d['index'] == config.device_index:
                    self.mic_combo.current(i)
                    break
        elif devices:
            self.mic_combo.current(0)

        log(f"Found {len(devices)} input devices")

    def on_mic_change(self, event=None):
        idx = self.mic_combo.current()
        if idx >= 0 and idx < len(self.devices):
            device_index = self.devices[idx]['index']
            config.device_index = device_index
            start_audio_stream(device_index)

    def apply_hotkey(self):
        config.hotkey = self.hotkey_var.get()
        setup_hotkey()

    def on_lang_change(self):
        config.language = self.lang_var.get() if self.lang_var.get() else None
        log(f"Language set to: {config.language or 'auto-detect'}")

    def test_microphone(self):
        """Test microphone by recording for 3 seconds and showing volume levels."""
        self.mic_testing = True
        self.volume_meter['value'] = 0
        self.volume_label.config(text="Testing... speak now!")
        log("Microphone test started - speak into the microphone")

        def do_test():
            try:
                idx = self.mic_combo.current()
                device_index = self.devices[idx]['index'] if idx >= 0 and idx < len(self.devices) else None

                test_duration = 3.0  # seconds
                chunk_duration = 0.1  # Update every 100ms
                chunks = int(test_duration / chunk_duration)
                max_volume = 0.0

                for i in range(chunks):
                    if not self.mic_testing:
                        break

                    # Record a short chunk
                    chunk = sd.rec(
                        int(config.sample_rate * chunk_duration),
                        samplerate=config.sample_rate,
                        channels=config.channels,
                        dtype=np.float32,
                        device=device_index
                    )
                    sd.wait()

                    # Calculate RMS volume
                    rms = np.sqrt(np.mean(chunk ** 2))
                    volume_pct = min(100, rms * 500)  # Scale for display
                    max_volume = max(max_volume, volume_pct)

                    # Update UI (thread-safe via after)
                    self.root.after(0, lambda v=volume_pct: self.update_volume_display(v))

                # Test complete
                self.root.after(0, lambda: self.finish_mic_test(max_volume))

            except Exception as e:
                log(f"Microphone test failed: {e}", "ERROR")
                self.root.after(0, lambda: self.finish_mic_test(-1))

        threading.Thread(target=do_test, daemon=True).start()

    def update_volume_display(self, volume_pct):
        """Update volume meter display."""
        self.volume_meter['value'] = volume_pct
        self.volume_label.config(text=f"Volume: {volume_pct:.0f}%")

    def finish_mic_test(self, max_volume):
        """Called when microphone test completes."""
        self.mic_testing = False
        if max_volume < 0:
            self.volume_label.config(text="Test failed!")
            log("Microphone test failed", "ERROR")
        elif max_volume < 5:
            self.volume_label.config(text=f"Max: {max_volume:.0f}% - No audio detected!")
            log(f"Microphone test complete - no audio detected (max {max_volume:.0f}%)", "WARN")
        else:
            self.volume_label.config(text=f"Max: {max_volume:.0f}% - OK!")
            log(f"Microphone test complete - max volume {max_volume:.0f}%")

        # Reset meter after a delay
        self.root.after(3000, lambda: self.volume_meter.configure(value=0))

    def test_connection(self):
        config.server_url = self.server_var.get()
        log(f"Testing connection to {config.server_url}...")

        def do_test():
            try:
                # Extract base URL and test health endpoint
                base_url = config.server_url.replace("/v1/audio/transcriptions", "")
                health_url = f"{base_url}/health"
                log(f"Checking {health_url}...")

                resp = requests.get(health_url, timeout=15, verify=False)
                if resp.ok:
                    data = resp.json()
                    model = data.get('model', 'unknown')
                    device = data.get('device', 'unknown')
                    log(f"Connected! Model: {model}, Device: {device}")
                else:
                    log(f"Server returned: {resp.status_code}", "ERROR")
            except requests.exceptions.Timeout:
                log("Connection timed out (15s) - server may be loading model", "ERROR")
            except requests.exceptions.ConnectionError as e:
                log(f"Cannot reach server - check if Whisper server is running", "ERROR")
            except Exception as e:
                log(f"Connection failed: {e}", "ERROR")

        threading.Thread(target=do_test, daemon=True).start()

    def on_audio_feedback_change(self):
        """Handle audio feedback checkbox change."""
        config.audio_feedback = self.audio_feedback_var.get()
        log(f"Audio feedback {'enabled' if config.audio_feedback else 'disabled'}")

    def on_autostart_change(self):
        """Handle auto-start checkbox change."""
        enabled = self.autostart_var.get()
        set_autostart(enabled)

    def on_gpt_enabled_change(self):
        """Handle GPT refinement checkbox change."""
        config.gpt_refinement = self.gpt_enabled_var.get()
        log(f"GPT refinement {'enabled' if config.gpt_refinement else 'disabled'}")

    def toggle_api_key_visibility(self):
        """Toggle visibility of the API key entry."""
        self.api_key_visible = not self.api_key_visible
        self.api_key_entry.configure(show="" if self.api_key_visible else "*")

    def test_beeps(self):
        """Test all audio feedback sounds."""
        log("Testing audio feedback sounds...")

        def do_test():
            log("Playing start sound...")
            play_start_sound()
            time.sleep(0.5)
            log("Playing stop sound...")
            play_stop_sound()
            time.sleep(0.5)
            log("Playing success sound...")
            play_success_sound()
            time.sleep(0.5)
            log("Playing error sound...")
            play_error_sound()
            log("Audio test complete")

        threading.Thread(target=do_test, daemon=True).start()

    def on_recording_mode_change(self):
        """Handle recording mode change."""
        config.recording_mode = self.recording_mode_var.get()
        setup_hotkey()  # Re-register with new mode
        log(f"Recording mode set to: {config.recording_mode}")

    def on_minimize_to_tray_change(self):
        """Handle minimize to tray checkbox change."""
        config.minimize_to_tray = self.minimize_to_tray_var.get()
        log(f"Minimize to tray: {'enabled' if config.minimize_to_tray else 'disabled'}")

    def on_always_on_top_change(self):
        """Handle always on top checkbox change."""
        config.always_on_top = self.always_on_top_var.get()
        self.root.attributes('-topmost', config.always_on_top)
        log(f"Always on top: {'enabled' if config.always_on_top else 'disabled'}")

    def copy_last_transcription(self):
        """Copy the last transcription to clipboard."""
        global last_transcription
        if last_transcription:
            self.root.clipboard_clear()
            self.root.clipboard_append(last_transcription)
            log(f"Copied to clipboard: {last_transcription[:50]}{'...' if len(last_transcription) > 50 else ''}")
        else:
            log("No transcription to copy", "WARN")

    def load_corrections_to_tree(self):
        """Load corrections from config into the treeview."""
        # Clear existing items
        for item in self.corrections_tree.get_children():
            self.corrections_tree.delete(item)

        # Add corrections from config
        for wrong, right in config.corrections.items():
            self.corrections_tree.insert("", tk.END, values=(wrong, right))

    def add_correction(self):
        """Add a new correction to the list."""
        wrong = self.wrong_entry.get().strip()
        right = self.right_entry.get().strip()

        if not wrong or not right:
            log("Both 'Wrong' and 'Correct' fields are required", "WARN")
            return

        # Add to config
        config.corrections[wrong] = right

        # Add to treeview
        self.corrections_tree.insert("", tk.END, values=(wrong, right))

        # Clear entries
        self.wrong_entry.delete(0, tk.END)
        self.right_entry.delete(0, tk.END)

        log(f"Added correction: '{wrong}' -> '{right}'")

    def delete_correction(self):
        """Delete the selected correction."""
        selection = self.corrections_tree.selection()
        if not selection:
            log("No correction selected", "WARN")
            return

        for item in selection:
            values = self.corrections_tree.item(item, "values")
            wrong = values[0]

            # Remove from config
            if wrong in config.corrections:
                del config.corrections[wrong]

            # Remove from treeview
            self.corrections_tree.delete(item)

            log(f"Deleted correction: '{wrong}'")

    def refresh_history(self):
        """Refresh the history list from database."""
        # Clear existing items
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        # Get history
        history = get_transcription_history(limit=100)

        # Add to treeview
        for record in history:
            id_, timestamp, duration, text, language = record
            # Format timestamp nicely
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_str = timestamp

            # Truncate long text for display
            display_text = text[:100] + "..." if len(text) > 100 else text
            self.history_tree.insert("", tk.END, iid=str(id_), values=(time_str, display_text))

    def search_history(self):
        """Search history with the current search term."""
        search_term = self.history_search_var.get().strip()

        # Clear existing items
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)

        # Get filtered history
        history = get_transcription_history(limit=100, search=search_term)

        # Add to treeview
        for record in history:
            id_, timestamp, duration, text, language = record
            try:
                dt = datetime.fromisoformat(timestamp)
                time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                time_str = timestamp

            display_text = text[:100] + "..." if len(text) > 100 else text
            self.history_tree.insert("", tk.END, iid=str(id_), values=(time_str, display_text))

        if search_term:
            log(f"Found {len(history)} results for '{search_term}'")

    def copy_history_item(self):
        """Copy the selected history item to clipboard."""
        selection = self.history_tree.selection()
        if not selection:
            log("No history item selected", "WARN")
            return

        # Get the full text from database (not truncated)
        item_id = int(selection[0])
        history = get_transcription_history(limit=1000)
        for record in history:
            if record[0] == item_id:
                text = record[3]
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                log(f"Copied to clipboard: {text[:50]}{'...' if len(text) > 50 else ''}")
                return

        log("Could not find selected item", "ERROR")

    def clear_history(self):
        """Clear all transcription history after confirmation."""
        from tkinter import messagebox
        if messagebox.askyesno("Clear History", "Are you sure you want to clear all transcription history?"):
            clear_transcription_history()
            self.refresh_history()
            log("Transcription history cleared")

    def save_settings(self):
        """Save all current settings to config file."""
        # Update config from UI values
        config.server_url = self.server_var.get()
        config.hotkey = self.hotkey_var.get()
        config.language = self.lang_var.get() if self.lang_var.get() else ""
        config.audio_feedback = self.audio_feedback_var.get()
        config.recording_mode = self.recording_mode_var.get()
        config.minimize_to_tray = self.minimize_to_tray_var.get()
        config.always_on_top = self.always_on_top_var.get()
        # GPT settings
        config.gpt_refinement = self.gpt_enabled_var.get()
        config.openai_api_key = self.api_key_var.get()
        # corrections are already updated in real-time

        # Save to file
        config.save()

    def setup_tray_icon(self):
        """Setup system tray icon with menu."""
        global tray_icon

        if not TRAY_AVAILABLE:
            log("System tray not available (install pystray and Pillow)", "WARN")
            return

        def show_window(icon, item):
            self.root.after(0, self.show_window)

        def quit_app(icon, item):
            self.root.after(0, self.quit_app)

        # Create tray menu
        menu = pystray.Menu(
            pystray.MenuItem("Show Window", show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app)
        )

        # Create icon
        self.tray_icon = pystray.Icon(
            "whisper-stt",
            create_tray_icon_image("ready"),
            "Whisper STT Client",
            menu
        )

        # Set global reference for hotkey handlers
        tray_icon = self.tray_icon

        # Run in background thread (non-blocking)
        def run_tray():
            self.tray_icon.run_detached()

        threading.Thread(target=run_tray, daemon=True).start()
        log("System tray icon created")

    def show_window(self):
        """Show the main window."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        """Handle window close button."""
        if config.minimize_to_tray and TRAY_AVAILABLE:
            self.root.withdraw()  # Hide window
            log("Minimized to system tray")
        else:
            self.quit_app()

    def quit_app(self):
        """Quit the application completely."""
        global tray_icon

        log("Shutting down...")

        # Stop tray icon
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

        tray_icon = None

        # Destroy window
        self.root.quit()
        self.root.destroy()

    def clear_log(self):
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')

    def update_log(self):
        while not log_queue.empty():
            try:
                msg = log_queue.get_nowait()
                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
            except queue.Empty:
                break

    def update_status(self):
        global is_recording
        if is_recording:
            self.status_var.set("Recording...")
            self.draw_status_indicator(True)
        else:
            mode_hint = "press to toggle" if config.recording_mode == "toggle" else "hold hotkey"
            self.status_var.set(f"Ready - {mode_hint}")
            self.draw_status_indicator(False)

    def setup_update_loop(self):
        self.update_log()
        self.update_status()
        self.root.after(100, self.setup_update_loop)

    def run(self):
        # Initialize
        log("Whisper STT Client starting...")
        log(f"Config file: {CONFIG_PATH}")
        start_audio_stream(config.device_index)
        setup_hotkey()

        # Apply always on top if enabled
        if config.always_on_top:
            self.root.attributes('-topmost', True)

        log(f"Server: {config.server_url}")
        log(f"Language: {config.language or 'auto-detect'}")
        log(f"Recording mode: {config.recording_mode}")
        log(f"Audio feedback: {'enabled' if config.audio_feedback else 'disabled'}")
        if config.corrections:
            log(f"Corrections: {len(config.corrections)} rules loaded")
        if config.gpt_refinement:
            if OPENAI_AVAILABLE and config.openai_api_key:
                log("GPT refinement: enabled")
            else:
                log("GPT refinement: enabled but not configured", "WARN")
        if TRAY_AVAILABLE:
            log(f"System tray: enabled (minimize to tray: {'on' if config.minimize_to_tray else 'off'})")
        else:
            log("System tray: not available")
        if is_autostart_enabled():
            log("Auto-start: enabled")
        log("Ready!")

        self.root.mainloop()

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    app = WhisperClientGUI()
    app.run()
