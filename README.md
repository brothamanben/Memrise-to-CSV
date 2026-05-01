# Memrise Anki Media Exporter

Tools for exporting Memrise lesson text, audio, and video into Anki-friendly CSV files.

The scripts open a real Chromium browser through Playwright, so they can use your logged-in Memrise session. Output is saved under `memrise_downloads/`, and the scripts reuse the same browser profile in `memrise_browser_profile/`.

## Which Script To Run

| File | Use it when |
| --- | --- |
| `memrise_single_lesson.py` | You want to download one lesson by pasting a Memrise lesson URL or scenario ID. |
| `memrise_batch.py` | You want to choose one Memrise language and download every lesson found for that language. |
| `memrise_batch_confirm.py` | You want to choose one Memrise language, review the lesson list, and download only selected lesson numbers or ranges. |
| `memrise_batch_multi_language.py` | You want to choose several Memrise languages and download all lessons found for those languages in one run. |
| `network_capture.py` | Backup/debug tool for capturing Memrise network responses and extracting scenario IDs if normal discovery misses something. |

## Setup

Install the Python packages used by the scripts:

```powershell
pip install playwright requests
python -m playwright install chromium
```

Run commands from this folder:

```powershell
cd "C:\Users\ACPS\MY-DOCUMENTS\000 BEN\memrise-anki-media-exporter"
```

## Output

Downloads are saved in `memrise_downloads/`.

Batch lesson folders are named like:

```text
<scenario_id>_<lesson_name>
```

Each lesson folder contains:

| File/folder | Purpose |
| --- | --- |
| `memrise_<scenario_id>.csv` | Anki import file with `Front`, `Back`, `Audio`, and `Video` columns. |
| `audio/` | Downloaded `.mp3` files. |
| `video/` | Downloaded `.mp4` files. |
| `media_links.txt` | Source media URLs matched to saved filenames. |
| `.download_complete` | Marker used by batch scripts to skip lessons already finished. |

Copy or import the media files into Anki's media collection when importing the CSV.

## Resume Behavior

The batch scripts are resumable.

If you stop a script and run it again:

- Finished lesson folders with `.download_complete` are skipped.
- Unfinished lessons are retried.
- Existing media files are skipped.
- In-progress downloads use `.part` files so half-downloaded media will not be mistaken for complete files.

## Single Lesson

Run:

```powershell
python memrise_single_lesson.py
```

Then either:

- paste a Memrise lesson URL,
- paste a scenario ID,
- type `clipboard` if you copied lesson text,
- or open the lesson in the script browser and let it detect the scenario ID.

## One Language, All Lessons

Run:

```powershell
python memrise_batch.py
```

Choose:

```text
1. Auto-discover scenario IDs from Memrise
```

Then select the Memrise language. The script downloads every lesson it finds for that language.

## One Language, Selected Lessons

Run:

```powershell
python memrise_batch_confirm.py
```

Choose:

```text
1. Auto-discover scenario IDs from Memrise
```

Then select the Memrise language. After discovery, the script prints a numbered lesson list and asks which lessons to download.

Examples:

| Input | Meaning |
| --- | --- |
| blank or `all` | Download every listed lesson. |
| `14` | Download lessons 1 through 14. |
| `1-7` | Download lessons 1 through 7. |
| `8-14` | Download lessons 8 through 14. |
| `1-7,15,20-25` | Download combined lesson numbers and ranges. |

## Multiple Languages

Run:

```powershell
python memrise_batch_multi_language.py
```

Choose:

```text
1. Auto-discover scenario IDs from Memrise
```

Then select languages.

Examples:

| Input | Meaning |
| --- | --- |
| blank or `all` | Download every listed language. |
| `1,3,5` | Download languages 1, 3, and 5. |
| `1-3` | Download languages 1 through 3. |
| `12345,67890` | Download specific language pair IDs. |

The script discovers all lessons for the selected languages, combines them into one queue, and downloads them with the same resume behavior.

## Manual URLs Or IDs

The batch scripts can also accept pasted lesson URLs or scenario IDs.

Choose:

```text
2. Paste lesson URLs or IDs manually
```

Paste one or more URLs/IDs, then press ENTER on a blank line. Type `clipboard` to read URLs/IDs from your clipboard.

## Saved Scenario Files

The batch scripts can read previously saved scenario data.

Choose:

```text
3. Use saved scenario file
```

Depending on the script, saved scenarios may come from:

- `memrise_network_capture/memrise_scenarios.csv`
- `memrise_downloads/memrise_discovered_scenarios.csv`

## Network Capture

Run this only if normal discovery is missing lessons:

```powershell
python network_capture.py
```

It opens Memrise, listens to network responses, scrolls Learn pages, clicks likely "show more" buttons, and saves captured data under `memrise_network_capture/`.

Important outputs:

| File | Purpose |
| --- | --- |
| `memrise_network_capture/memrise_scenarios.csv` | Scenario IDs extracted from captured JSON. |
| `memrise_network_capture/memrise_scenario_links.txt` | Lesson links extracted from capture. |
| `memrise_network_capture/scenario_discovery_dump.txt` | Readable debug dump of found scenario records. |
| `memrise_network_capture/capture_summary.json` | Captured response summary. |

After running capture, run `memrise_batch_confirm.py` or `memrise_batch.py` and choose the saved scenario file option.

## Logs

Batch command logs are saved in `memrise_logs/`.

The log files are useful if a run stops or a specific lesson fails.

## Troubleshooting

If discovery finds no lessons:

- Make sure you are logged into Memrise in the browser window opened by the script.
- Try choosing the language manually from the displayed language list.
- Run `network_capture.py`, click around the Memrise Learn pages, then use the saved scenario file option.

If downloads fail:

- Rerun the same script. Existing files are skipped and missing files are retried.
- Check `memrise_logs/` for the latest command log.
- Delete only the affected lesson folder if you want a completely fresh retry for one lesson.

If Anki does not play media:

- Confirm the CSV references filenames like `[sound:001_word_audio_1.mp3]`.
- Confirm those media files were copied into Anki's media collection.
- Keep filenames unchanged after export.
