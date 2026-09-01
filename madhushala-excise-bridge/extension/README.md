# Madhushala Excise Capture Extension

This extension moves the Excise portal browser work to the user's own Chrome browser. The server keeps only the backend API, capture storage, Madhushala item creation, mapping suggestions, and guardrails.

## Operator Flow

1. Open the Excise portal in Chrome and log in normally.
2. Go to Prepare Indent.
3. Type case quantity in the rows that should be imported.
4. Click the extension icon.
5. Click `Capture Typed Rows`.
6. Click `Open Mapping` when matching is required.

The extension captures only rows where case quantity is typed. Checkbox-only rows are ignored.

## Install for Testing

1. Open `chrome://extensions`.
2. Enable `Developer mode`.
3. Click `Load unpacked`.
4. Select this `extension` folder.
5. Keep the Bridge URL as `http://13.232.52.191/excise-import` while testing the public IP route.

For production, set the Bridge URL to the final private subdomain.
