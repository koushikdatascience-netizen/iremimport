# Madhushala Excise Capture Extension

This extension moves the Excise portal browser work to the user's own Chrome browser. The server keeps only the backend API, capture storage, Madhushala item creation, mapping suggestions, and guardrails.

## Operator Flow

1. Open Excise Import from Madhushala CRM and click `Open Excise Portal`.
2. The bridge loads the current Company Master, including state and Excise credentials.
3. WB opens the West Bengal Excise login; MP opens eAabgari.
4. The extension fills the Excise User ID and password automatically. If CAPTCHA is present, complete it manually.
5. WB capture/import continues with the existing Prepare Indent workflow. MP is login-only in this release.
6. If WB matching is required, the mapping workspace opens automatically.

The extension captures only rows with a positive case quantity. Checkbox-only rows are ignored. The Madhushala API token is configured on the backend and is not requested from operators.

Excise credentials are not entered manually in the extension. Update `exciseUserId` / `excisePassword` in Madhushala Company Master and relaunch the portal.

## Install for Testing

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this `extension` folder.
5. Keep the Bridge URL as `http://13.232.52.191/excise-import` while testing the public IP route.
6. Open the bridge page and use its single portal button. The one-time credential prompt stores Excise credentials in the local Chrome profile.

For production, set the Bridge URL to the final private subdomain.
