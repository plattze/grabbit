# Chrome extension

The extension lives in [`extension/`](../extension/) and is loaded unpacked
(not on the Web Store yet).

## Install

1. Open `chrome://extensions`, enable **Developer mode**.
2. **Load unpacked** → select the `extension/` directory.
3. Click the Grabbit icon → **Options**:
   - **Server URL** — where Grabbit is reachable, including any sub-path
     (e.g. `https://nas.example.home/grabbit`).
   - **API key** — create a `submit`-scoped key in the web UI → API keys.
     Don't use the admin key here.

## Use

- Right-click any link → **Send link to Grabbit**.
- Right-click a page → **Send page to Grabbit** (for album/thread pages).
- Toolbar popup → **Queue current page**, plus the last few jobs with status.

A notification confirms whether Grabbit accepted the URL; rejections include
the reason (unsupported site, private address, etc.).
