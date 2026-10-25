
## Download
| Platform | File |
|---|---|
| Windows 10/11 (64-bit) | `Prismix-Setup.exe` |
| macOS 12+ (Apple Silicon) | `Prismix.dmg`: open it and drag Prismix into Applications |

The first split downloads the separation models (about 1 GB), so it needs a connection and some patience. Your library is kept in `%LOCALAPPDATA%\Prismix` on Windows and `~/Library/Application Support/Prismix` on macOS.

## Windows SmartScreen
The installer is not code-signed. If SmartScreen shows "Windows protected your PC", click **More info, then Run anyway**.

## macOS Gatekeeper
The DMG is signed and notarized when the repository has Apple signing secrets, otherwise it is ad-hoc signed and macOS asks for **System Settings, Privacy & Security, Open Anyway**.
