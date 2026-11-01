
## Download
| Platform | File |
|---|---|
| Windows 10/11 (64-bit) | `Prismix-Setup.exe` |
| macOS 12+ (Apple Silicon) | `Prismix.dmg`: open it and drag Prismix into Applications |

The first split downloads the separation models (about 1 GB), so it needs a connection and some patience. Your library is kept in `%LOCALAPPDATA%\Prismix` on Windows and `~/Library/Application Support/Prismix` on macOS.

## Windows SmartScreen
The installer is not code-signed. If SmartScreen shows "Windows protected your PC", click **More info, then Run anyway**.

## macOS Gatekeeper
The DMG is ad-hoc signed, not notarized. On first launch macOS asks for **System Settings, Privacy & Security, Open Anyway**, or run `xattr -dr com.apple.quarantine /Applications/Prismix.app` once.

## iPhone and iPad
`Prismix.ipa` is unsigned. Easiest: install [AltStore](https://altstore.io) or [SideStore](https://sidestore.io), then add the source `https://github.com/rsx-xxx/prismix/releases/latest/download/altstore.json` (or scan the QR in Prismix, Settings, Mobile app). Free Apple IDs sign for 7 days and AltStore renews it.
