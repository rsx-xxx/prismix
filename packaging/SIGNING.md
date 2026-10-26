# Code signing

Signing makes every release open without the SmartScreen or Gatekeeper warning. It is tied to the publisher identity, not to a version, so each new release is trusted automatically once the secrets below are set. The release workflow signs by itself when they exist and builds unsigned when they do not.

## Windows: Azure Artifact Signing

About $10 a month, no hardware token, short-lived certificates issued from your verified identity. SmartScreen reputation attaches to that identity and carries over from version to version; a brand-new publisher can still see a warning for the first days until downloads build reputation.

1. In the Azure portal create an **Artifact Signing** account and complete identity validation (an individual or an organisation; the page lists the supported countries).
2. Create a **certificate profile** of type *Public Trust*.
3. Create an app registration with a federated credential for this repository (`repo:rsx-xxx/prismix:ref:refs/tags/*` and `...:ref:refs/heads/master`) and give it the **Artifact Signing Certificate Profile Signer** role on the account.
4. In the GitHub repository settings add:
   - secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`
   - variables `AZURE_SIGNING_ENDPOINT` (for example `https://eus.codesigning.azure.net/`), `AZURE_SIGNING_ACCOUNT`, `AZURE_SIGNING_PROFILE`

`Prismix.exe` and `Prismix-Setup.exe` are then signed and timestamped on every build.

## macOS: Developer ID and notarization

Needs the Apple Developer Program ($99 a year). Signed with the same Team ID and notarized, every release passes Gatekeeper with no "Open Anyway".

1. Create a **Developer ID Application** certificate and export it with its private key as a `.p12`.
2. Create an app-specific password for the Apple ID.
3. Add secrets: `MACOS_CERTIFICATE_BASE64` (`base64 -i cert.p12`), `MACOS_CERTIFICATE_PASSWORD`, `MACOS_KEYCHAIN_PASSWORD` (any string), `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD`. Optionally `MACOS_CODESIGN_IDENTITY`, otherwise the Developer ID certificate is found by itself.

## Android

Set `ANDROID_KEYSTORE_BASE64` once (a keystore with alias `androiddebugkey` and password `android`, or adjust `android/build.sh`) so new APKs install over the old one. Sideloaded APKs still show Play Protect's "unknown developer" prompt on first install; only Google Play removes it.

After the first signed release, edit `packaging/release-footer.md` and the README install note to drop the "unsigned" text.
