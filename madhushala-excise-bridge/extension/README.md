# Madhushala Excise Capture Extension

This extension opens the correct state Excise portal in the user's Chrome browser. State, portal URL, login selectors, and Excise credentials are resolved by the authenticated bridge at launch time.

## Operator Flow

1. Open Excise Import from Madhushala CRM and click `Open Excise Portal`.
2. The bridge loads the current Company Master, including `state`, `exciseUserId`, and `excisePassword`.
3. The bridge resolves that state through `app/excise_portals.json`.
4. The extension opens the returned HTTPS portal and uses the returned login profile to fill credentials.
5. If CAPTCHA/verification is present, the user completes it manually. Otherwise the generic engine can submit the login automatically.
6. WB capture/import continues with the existing Prepare Indent workflow. Other states are login-only until their import adapters are implemented.

Excise credentials are not stored or entered manually in the extension. Update them in Madhushala Company Master and relaunch.

## Adding Another State

Adding another state login does **not** require rebuilding/reinstalling the Chrome extension. Add a new state entry to `app/excise_portals.json` (or point `EXCISE_PORTAL_REGISTRY_PATH` at an external server-managed registry), then redeploy/reload the backend configuration.

A state entry can define:

- `aliases`
- `loginUrl`
- `allowedOrigins`
- `usernameSelectors`
- `passwordSelectors`
- `captchaSelectors`
- `loginSelectors`
- `loginText`
- `autoSubmit`

If custom selectors are omitted, the backend sends generic username/password/CAPTCHA/login selectors.

Example:

```json
{
  "JHARKHAND": {
    "aliases": ["JH", "JHARKHAND"],
    "loginUrl": "https://official-state-excise.example/login",
    "allowedOrigins": ["https://official-state-excise.example"],
    "usernameSelectors": ["#txtUserId"],
    "passwordSelectors": ["#txtPassword"],
    "captchaSelectors": ["#txtCaptcha"],
    "loginSelectors": ["#btnLogin"],
    "autoSubmit": true
  }
}
```

Only verified official portal URLs should be added.

## Install for Testing

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this `extension` folder.
5. Open Excise Import from Madhushala CRM.
6. Use the single `Open Excise Portal` button.

The extension already has generic HTTPS scripting permission, so future state login pages configured by the backend do not require another extension release.
