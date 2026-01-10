"""
Remote Speech-to-Text Client
Push-to-talk with Ctrl+Shift+Space, transcribes via remote Whisper server.
"""

import io
import threading
import time
import sys
from dataclasses import dataclass
from typing import Optional

import keyboard
import sounddevice as sd
import numpy as np
from scipy.io import wavfile
import requests
import pystray
from PIL import Image, ImageDraw

# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class Config:
    # Whisper server URL (OpenAI-compatible endpoint)
    server_url: str = "http://localhost:8000/v1/audio/transcriptions"
    hotkey: str = "ctrl+shift+space"
    sample_rate: int = 16000
    channels: int = 1
    language: Optional[str] = "en"  # "en", "ro", or None for auto-detect
    min_duration: float = 0.3
    timeout: int = 30

config = Config()

# =============================================================================
# GLOBAL STATE
# =============================================================================

is_recording = False
audio_data = []
tray_icon: Optional[pystray.Icon] = None

# =============================================================================
# AUDIO RECORDING
# =============================================================================

def audio_callback(indata, frames, time_info, status):
    if status:
        print(f"Audio status: {status}", file=sys.stderr)
    if is_recording:
        audio_data.append(indata.copy())

def start_recording():
    global is_recording, audio_data
    audio_data = []
    is_recording = True
    update_tray_icon(recording=True)
    print("Recording started...")

def stop_recording() -> Optional[bytes]:
    global is_recording
    is_recording = False
    update_tray_icon(recording=False)
    print("Recording stopped.")

    if not audio_data:
        return None

    audio_np = np.concatenate(audio_data, axis=0)
    duration = len(audio_np) / config.sample_rate

    if duration < config.min_duration:
        print(f"Recording too short ({duration:.2f}s), ignoring.")
        return None

    print(f"Recorded {duration:.2f} seconds of audio.")

    wav_buffer = io.BytesIO()
    wavfile.write(wav_buffer, config.sample_rate, audio_np)
    wav_buffer.seek(0)
    return wav_buffer.read()

# =============================================================================
# TRANSCRIPTION
# =============================================================================

def transcribe(audio_bytes: bytes) -> Optional[str]:
    try:
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        data = {
            "model": "Systran/faster-whisper-large-v3",
            "response_format": "json"
        }

        if config.language:
            data["language"] = config.language

        print(f"Sending audio to {config.server_url}...")
        response = requests.post(
            config.server_url,
            files=files,
            data=data,
            timeout=config.timeout,
            verify=False  # Disable SSL verification for direct IP access
        )
        response.raise_for_status()

        result = response.json()
        text = result.get("text", "").strip()
        print(f"Transcription: {text}")
        return text

    except requests.exceptions.Timeout:
        print("Error: Request timed out", file=sys.stderr)
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to server", file=sys.stderr)
    except Exception as e:
        print(f"Error during transcription: {e}", file=sys.stderr)

    return None

def type_text(text: str):
    if text:
        time.sleep(0.1)
        keyboard.write(text)

# =============================================================================
# HOTKEY HANDLING
# =============================================================================

def on_hotkey_press():
    if not is_recording:
        start_recording()

def on_hotkey_release():
    if is_recording:
        audio_bytes = stop_recording()
        if audio_bytes:
            threading.Thread(
                target=lambda: type_text(transcribe(audio_bytes)),
                daemon=True
            ).start()

# =============================================================================
# SYSTEM TRAY
# =============================================================================

def create_icon_image(recording: bool = False) -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (255, 80, 80) if recording else (80, 200, 80)
    margin = 8
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)
    return image

def update_tray_icon(recording: bool):
    global tray_icon
    if tray_icon:
        tray_icon.icon = create_icon_image(recording)

def on_tray_quit(icon, item):
    icon.stop()
    sd.stop()
    keyboard.unhook_all()
    sys.exit(0)

def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(f"Hotkey: {config.hotkey}", lambda: None, enabled=False),
        pystray.MenuItem(f"Server: {config.server_url.split('/')[2]}", lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_tray_quit)
    )
    tray_icon = pystray.Icon("stt-client", create_icon_image(False), "Speech-to-Text Client", menu)
    return tray_icon

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 60)
    print("Remote Speech-to-Text Client")
    print("=" * 60)
    print(f"Server: {config.server_url}")
    print(f"Hotkey: {config.hotkey} (hold to record)")
    print(f"Language: {config.language or 'auto-detect'}")
    print("=" * 60)

    stream = sd.InputStream(
        samplerate=config.sample_rate,
        channels=config.channels,
        dtype=np.float32,
        callback=audio_callback
    )
    stream.start()
    print("Audio stream started.")

    hotkey_parts = config.hotkey.split("+")
    keyboard.add_hotkey(config.hotkey, on_hotkey_press, suppress=False, trigger_on_release=False)

    def on_key_event(e):
        if e.event_type == "up" and is_recording:
            if e.name.lower() in [k.strip().lower() for k in hotkey_parts]:
                on_hotkey_release()

    keyboard.hook(on_key_event)
    print("Hotkey registered. Starting system tray...")

    icon = setup_tray()
    icon.run()

if __name__ == "__main__":
    main()
