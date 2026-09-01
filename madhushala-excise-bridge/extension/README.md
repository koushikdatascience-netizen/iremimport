# Madhushala Excise Capture Extension

This extension moves the Excise portal browser work to the user's own Chrome browser. The server keeps only the backend API, capture storage, Madhushala item creation, mapping suggestions, and guardrails.

## Operator Flow

1. Save Bridge/API URL, API Secret, Excise User ID, and Excise Password once.
2. Click `Open Portal`.
3. Enter CAPTCHA and log in.
4. Go to Prepare Indent.
5. Type case quantity in the rows that should be imported.
6. Click the extension icon.
7. Click `Capture Selected`.
8. Click `Open Mapping` when matching is required.

The extension captures only rows where case quantity is typed. Checkbox-only rows are ignored.

## Install for Testing

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this `extension` folder.
5. Keep the Bridge URL as `http://13.232.52.191/excise-import` while testing the public IP route.
6. Save the API Secret and Excise credentials once in the extension popup.

For production, set the Bridge URL to the final private subdomain.
