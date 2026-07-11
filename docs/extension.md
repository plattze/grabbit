# Chrome extension

The extension lives in [`extension/`](../extension/) and is loaded unpacked
(not on the Web Store yet).

## Install (preconfigured — recommended)

1. In the Grabbit web UI (logged in with an **admin** key), click
   **Install Chrome plugin** on the home page. The server mints a fresh
   `submit`-scoped API key and bakes it, plus the server URL, into the
   downloaded zip.
2. Unzip it, open `chrome://extensions`, enable **Developer mode**,
   **Load unpacked** → select the unzipped directory. Done — no manual setup.

Behind a reverse proxy, set `server.public_url` (or `GRABBIT_PUBLIC_URL`) to
the externally reachable URL so the baked-in server address is right; without
it, the address you're browsing from is used.

## Install (manual)

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
