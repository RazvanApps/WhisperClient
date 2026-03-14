# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Update release workflow (`.github/workflows/release.yml`): route Linux build to WAN self-hosted runner (`[self-hosted, Linux, X64, WAN]`), add `timeout-minutes: 30` to `build-linux` job. macOS and Windows builds remain on GitHub-hosted runners. Release job moved to WAN runner.

## [0.0.1] - 2026-01-10

### Added

- Restructured as proper Python package with `pyproject.toml`
- Entry points: `whisper-client` and `whisper-client-cli` commands
- Support for `python -m whisperclient` execution
- PyInstaller build scripts for standalone executables
- GitHub Actions workflow for automated multi-platform releases
- Modular code structure with separate modules:
  - `config.py` - Configuration management with lazy loading
  - `audio.py` - Audio recording and playback
  - `transcription.py` - Whisper API integration
  - `hotkeys.py` - Cross-platform hotkey handling
  - `typing.py` - Cross-platform text typing
  - `tray.py` - System tray functionality
  - `history.py` - SQLite transcription history
  - `autostart.py` - Cross-platform auto-start

### Changed

- Moved from monolithic single-file to `src/whisperclient/` package layout
- Sound files bundled inside package at `src/whisperclient/sounds/`
- Auto-start now detects installed entry point vs script path
- Text typing now works cross-platform (pynput fallback for Linux/macOS)

### Removed

- Legacy `stt_client_gui.pyw` and `stt_client.pyw` files
- Root-level `sounds/` directory (moved to package)
- `run_gui.bat` and `run_gui.sh` launcher scripts
