# Code signing

Prismix is free and open source, so releases use only free options. Nothing here costs money.

## Android: done
The release APK is signed with one fixed keystore (GitHub secret `ANDROID_KEYSTORE_BASE64`, the same key as local builds), so every new APK installs over the previous one without uninstalling. Play Protect's "unknown developer" prompt on first install cannot be removed without Google Play.

## Windows: unsigned, optional free signing
Windows has no free trusted certificate for individuals except through **SignPath Foundation**, which signs open-source projects at no cost after a manual review. To enable it:

1. Apply at <https://signpath.org/foundation> with the public repository `rsx-xxx/prismix` (MIT).
2. Once approved, add the SignPath organization id, project and signing policy as repository secrets and variables and a signing step to `release.yml`.

Until then the installer is unsigned and SmartScreen shows "More info, then Run anyway" once. A self-signed certificate does not help: SmartScreen treats it like no signature.

The Azure Artifact Signing steps in `release.yml` stay off unless its secrets exist; they are paid and not used.

## macOS: ad-hoc signed
A Developer ID certificate costs $99 a year, so the DMG is ad-hoc signed. On first launch use **System Settings, Privacy & Security, Open Anyway**, or run `xattr -dr com.apple.quarantine /Applications/Prismix.app` once.
