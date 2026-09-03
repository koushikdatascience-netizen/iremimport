# Madhushala Excise Capture Extension

This extension moves the Excise portal browser work to the user's own Chrome browser. The server keeps only the backend API, capture storage, Madhushala item creation, mapping suggestions, and guardrails.

## Operator Flow

1. Open the bridge UI and click `Open Excise Portal`.
2. On first use only, enter the separate BEVCO/WB Excise User ID and password.
3. Enter CAPTCHA and log in.
4. Go to Prepare Indent.
5. Type case quantities in the rows that should be imported.
6. The extension automatically saves the entered rows after input settles.
7. If matching is required, the mapping workspace opens automatically.

The extension captures only rows with a positive case quantity. Checkbox-only rows are ignored. The Madhushala API token is configured on the backend and is not requested from operators.

To replace an incorrect or expired Excise login, open the extension popup and choose `Change Saved Excise Login`; the next portal launch shows the one-time credential prompt again.

## Install for Testing

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this `extension` folder.
5. Keep the Bridge URL as `http://13.232.52.191/excise-import` while testing the public IP route.
6. Open the bridge page and use its single portal button. The one-time credential prompt stores Excise credentials in the local Chrome profile.

For production, set the Bridge URL to the final private subdomain.
