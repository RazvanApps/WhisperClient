# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-01-10

### Added
- Initial public release
- Push-to-talk speech-to-text with Whisper server
- GUI version with full feature set
- CLI version for lightweight usage
- Cross-platform support (Windows, macOS, Linux)
- System tray integration with color-coded status indicator
  - Green: Ready
  - Red: Recording
  - Yellow: Transcribing
- Audio feedback (beeps) on recording start/stop
  - Windows: native winsound
  - macOS/Linux: playsound with bundled WAV files
- JSON configuration file persistence (`~/.whisper-stt/config.json`)
- SQLite transcription history with search
- Custom word corrections/glossary
- Hold-to-talk and toggle recording modes
- GPT text refinement (optional, requires OpenAI API key)
- Cross-platform auto-start on login
  - Windows: Registry
  - macOS: LaunchAgents
  - Linux: XDG autostart
- Always-on-top window option
- Microphone test with volume meter
- Server connection test
