# Memrise Anki Media Exporter

Unpacked Chrome/Edge extension for exporting one open Memrise lesson/scenario at a time.

## Install

1. Open `chrome://extensions` or `edge://extensions`.
2. Turn on **Developer mode**.
3. Click **Load unpacked**.
4. Select this folder: `memrise-anki-media-exporter`.

After editing these files, reload the extension on the extensions page.

## Extension Workflow

1. Open Memrise.
2. Click into one scenario/lesson so the full word list is visible.
3. Open the extension popup.
4. Click **Detect lesson**.
5. If media is missing, click **Open words + capture media**. This tries to open each word detail and press visible audio/video controls.
6. Click **Export lesson**.

The export creates a folder named after the lesson with:

- `lesson_name.csv`
- detected audio/video files

Some Memrise media only appears after playback. If detection misses files, manually play a few items on the page, then run **Detect lesson** or **Open words + capture media** again.

Multiple audio files for the same word are placed in the same CSV cell as multiple Anki `[sound:...]` tags.

## Python Experiment

There is also a standalone experiment script:

```powershell
py -m pip install playwright
py -m playwright install chromium
py .\memrise_lesson_scraper.py --url https://app.memrise.com/
```

Chrome will open. Log in, click into one lesson/scenario, optionally play media manually, then press Enter in the terminal.

Useful options:

```powershell
py .\memrise_lesson_scraper.py --url https://app.memrise.com/ --reveal
py .\memrise_lesson_scraper.py --url https://app.memrise.com/ --reveal --download
```

Outputs go to `scrape-output/`.

## Notes

- This is intentionally focused on one open lesson. Full scenario-list/batch export was removed because it was unreliable.
- Media URLs can only be captured when Memrise exposes them in the page or when the browser loads them after playback/clicks.
- Put downloaded media files into Anki's `collection.media` folder before importing the CSV.
