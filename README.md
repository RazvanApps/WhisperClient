# WhisperClient

A cross-platform push-to-talk speech-to-text client for Whisper servers. Press a hotkey, speak, and the transcribed text is automatically typed where your cursor is.

## Features

- **Push-to-Talk**: Hold or toggle hotkey to record, release to transcribe
- **Cross-Platform**: Windows, macOS, and Linux support
- **System Tray**: Minimize to tray with color-coded status indicator
- **Audio Feedback**: Beeps on recording start/stop and transcription complete
- **Transcription History**: SQLite database with search and export
- **Word Corrections**: Auto-correct common transcription errors
- **GPT Refinement** (Optional): Improve grammar/punctuation with OpenAI
- **Auto-Start**: Launch on system login
- **Configurable**: JSON config file with all settings

## Screenshots

*Coming soon*

## Requirements

- Python 3.8+
- A running Whisper server (OpenAI-compatible API)

### Platform-Specific Notes

| Platform | Keyboard | Audio Feedback | Auto-Start |
|----------|----------|----------------|------------|
| Windows | `keyboard` library | `winsound` (built-in) | Registry |
| macOS | `pynput` library | `playsound` + WAV files | LaunchAgents |
| Linux | `pynput` library | `playsound` + WAV files | XDG autostart |

**Linux users**: The `keyboard` library requires root privileges. We use `pynput` instead, which works in user space but may require accessibility permissions.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/RazvanApps/WhisperClient.git
cd WhisperClient
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure the client

Copy the example environment file and edit as needed:

```bash
cp .env.example .env
```

Or edit the config file directly after first run:
- Windows: `%USERPROFILE%\.whisper-stt\config.json`
- macOS/Linux: `~/.whisper-stt/config.json`

### 5. Run the client

```bash
# GUI version (recommended)
python stt_client_gui.pyw

# CLI version (lightweight)
python stt_client.pyw
```

## Configuration

Settings are stored in `~/.whisper-stt/config.json`:

```json
{
  "server_url": "http://localhost:8000/v1/audio/transcriptions",
  "hotkey": "ctrl+shift+space",
  "language": "en",
  "audio_feedback": true,
  "recording_mode": "hold",
  "minimize_to_tray": true,
  "corrections": {
    "gpt": "GPT",
    "api": "API"
  }
}
```

### Key Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `server_url` | `http://localhost:8000/v1/audio/transcriptions` | Whisper server endpoint |
| `hotkey` | `ctrl+shift+space` | Push-to-talk hotkey |
| `language` | `en` | Language code (or empty for auto-detect) |
| `audio_feedback` | `true` | Play beeps on record/transcribe |
| `recording_mode` | `hold` | `hold` (release to stop) or `toggle` (press to start/stop) |
| `minimize_to_tray` | `true` | Minimize to tray on close |
| `corrections` | `{}` | Word replacement dictionary |
| `gpt_refinement` | `false` | Enable GPT text refinement |
| `openai_api_key` | `""` | OpenAI API key (for GPT refinement) |

## Whisper Server Setup

This client requires a Whisper server with an OpenAI-compatible API. Options:

### Option 1: faster-whisper-server (Recommended)

```bash
pip install faster-whisper uvicorn fastapi

# Run server
python server.py
```

See the included `server.py` example or use [faster-whisper-server](https://github.com/fedirz/faster-whisper-server).

### Option 2: OpenAI Whisper API

Set your server URL to: `https://api.openai.com/v1/audio/transcriptions`

Note: This requires an OpenAI API key and incurs costs.

### Option 3: LocalAI

Use [LocalAI](https://localai.io/) with whisper backend.

## Usage

1. **Start the client**: Run `python stt_client_gui.pyw`
2. **Check system tray**: Green circle = ready
3. **Record**: Press and hold `Ctrl+Shift+Space` (or your configured hotkey)
4. **Speak**: Talk into your microphone
5. **Release**: Let go of the hotkey
6. **Done**: Text is typed at your cursor position

### Status Indicators

| Tray Color | Status |
|------------|--------|
| 🟢 Green | Ready |
| 🔴 Red | Recording |
| 🟡 Yellow | Transcribing |

## Development

### Project Structure

```
WhisperClient/
├── stt_client_gui.pyw   # Main GUI application
├── stt_client.pyw       # Lightweight CLI version
├── requirements.txt     # Python dependencies
├── sounds/              # Audio feedback files (cross-platform)
│   ├── start.wav
│   ├── stop.wav
│   ├── success.wav
│   └── error.wav
├── .env.example         # Environment template
├── README.md
├── LICENSE
└── CHANGELOG.md
```

### User Data Location

Configuration and history are stored in:
- Windows: `%USERPROFILE%\.whisper-stt\`
- macOS/Linux: `~/.whisper-stt/`

Files:
- `config.json` - Settings
- `history.db` - Transcription history (SQLite)

## Troubleshooting

### No audio detected
- Check microphone permissions in system settings
- Use "Test Microphone" button in Settings tab
- Select the correct input device

### Hotkey not working
- **Windows**: Run as administrator if needed
- **Linux**: May need to add user to `input` group or grant accessibility permissions
- **macOS**: Grant accessibility permissions in System Preferences

### Connection refused
- Ensure Whisper server is running
- Check firewall settings
- Verify server URL in settings

### SSL certificate errors
- The client disables SSL verification by default for local servers
- For production, enable `verify=True` in the code

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [faster-whisper](https://github.com/guillaumekln/faster-whisper) - Fast Whisper implementation
- [pystray](https://github.com/moses-palmer/pystray) - System tray support
- [pynput](https://github.com/moses-palmer/pynput) - Cross-platform input handling
