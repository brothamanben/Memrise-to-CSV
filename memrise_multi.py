from pathlib import Path
import csv
import re
import requests
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright


OUTPUT_DIR = Path("memrise_downloads")
PROFILE_DIR = Path("memrise_browser_profile")


def clean_name(value, fallback="item", max_len=80):
    value = str(value or fallback)
    value = re.sub(r'[\\/:*?"<>|]+', "", value)
    value = re.sub(r"\s+", "_", value.strip())
    return value[:max_len] or fallback


def detect_scenario_id(value):
    value = str(value or "").strip()

    if value.isdigit():
        return value

    parsed = urlparse(value)
    query = parse_qs(parsed.query)

    if "scenario_id" in query:
        return query["scenario_id"][0]

    match = re.search(r"scenario[_-]?id[=/](\d+)", value)
    if match:
        return match.group(1)

    match = re.search(r"\b(\d{4,})\b", value)
    if match:
        return match.group(1)

    return None


def ask_for_scenario_ids():
    print("\nPaste Memrise lesson links or lesson/scenario IDs.")
    print("You can paste one per line, or paste a whole block at once.")
    print("Press ENTER on a blank line when finished.\n")

    items = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        items.extend(line.split())

    scenario_ids = []
    seen = set()

    for item in items:
        scenario_id = detect_scenario_id(item)
        if scenario_id and scenario_id not in seen:
            scenario_ids.append(scenario_id)
            seen.add(scenario_id)

    return scenario_ids


def download_file(url, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        print(f"Skipping existing: {output_path.name}")
        return

    print(f"Downloading: {output_path.name}")

    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)


def fetch_json(page, url):
    return page.evaluate(
        """
        async (url) => {
            const r = await fetch(url, { credentials: "include" });
            const text = await r.text();

            if (!r.ok) {
                throw new Error(`HTTP ${r.status}: ${text.slice(0, 500)}`);
            }

            return JSON.parse(text);
        }
        """,
        url,
    )


def find_lesson_name(data, scenario_id):
    candidates = [
        data.get("title"),
        data.get("name"),
        data.get("scenario_name"),
        data.get("display_name"),
    ]

    scenario = data.get("scenario")
    if isinstance(scenario, dict):
        candidates.extend([
            scenario.get("title"),
            scenario.get("name"),
            scenario.get("scenario_name"),
            scenario.get("display_name"),
        ])

    for candidate in candidates:
        if candidate:
            return clean_name(candidate, fallback=f"memrise_{scenario_id}")

    return f"memrise_{scenario_id}"


def write_lesson_outputs(rows, csv_path, media_list_path, audio_dir, video_dir):
    print("Writing CSV...")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Front", "Back", "Audio", "Video"])

        for row in rows:
            audio_field = " ".join(f"[sound:{a['filename']}]" for a in row["audio"])
            video_field = " ".join(f"[sound:{v['filename']}]" for v in row["video"])
            writer.writerow([row["front"], row["back"], audio_field, video_field])

    print(f"CSV saved: {csv_path}")

    with open(media_list_path, "w", encoding="utf-8") as f:
        for row in rows:
            for audio_item in row["audio"]:
                f.write(f"{audio_item['url']} -> audio/{audio_item['filename']}\n")
                download_file(audio_item["url"], audio_dir / audio_item["filename"])

            for video_item in row["video"]:
                f.write(f"{video_item['url']} -> video/{video_item['filename']}\n")
                download_file(video_item["url"], video_dir / video_item["filename"])


def write_import_notes(lesson_dir):
    notes_path = lesson_dir / "anki_import_notes.txt"
    notes_path.write_text(
        "Import the CSV into Anki with four fields: Front, Back, Audio, Video.\n"
        "The CSV uses [sound:filename] references for audio and video.\n"
        "Copy the files from the audio and video folders into Anki's collection.media folder before reviewing cards.\n",
        encoding="utf-8",
    )


def download_lesson(page, scenario_id):
    print(f"\n=== Lesson {scenario_id} ===")
    details_url = f"https://app.memrise.com/v1.25/me/scenarios/{scenario_id}/details/"

    print("Getting scenario details...")
    data = fetch_json(page, details_url)

    lesson_name = find_lesson_name(data, scenario_id)
    lesson_dir = OUTPUT_DIR / lesson_name
    audio_dir = lesson_dir / "audio"
    video_dir = lesson_dir / "video"
    csv_path = lesson_dir / f"{lesson_dir.name}.csv"
    media_list_path = lesson_dir / "media_links.txt"

    lesson_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(exist_ok=True)
    video_dir.mkdir(exist_ok=True)

    learnables = data.get("learnables") or []
    learnable_ids = [x.get("learnable_id") for x in learnables if x.get("learnable_id")]

    print(f"Lesson folder: {lesson_dir}")
    print(f"Found {len(learnable_ids)} learnables.")

    rows = []

    for index, learnable_id in enumerate(learnable_ids, start=1):
        print(f"Reading {index}/{len(learnable_ids)}: {learnable_id}")

        details = fetch_json(
            page,
            f"https://app.memrise.com/v1.25/learnable_details/{learnable_id}/",
        )

        front = details.get("source_value", "") or ""
        back = details.get("target_value", "") or ""
        base = f"{index:03d}_{clean_name(front, max_len=60)}"

        audio_items = []
        for i, url in enumerate(details.get("audio_urls") or [], start=1):
            audio_items.append({
                "url": url,
                "filename": f"{base}_audio_{i}.mp3",
            })

        video_items = []
        for i, url in enumerate(details.get("video_urls") or [], start=1):
            video_items.append({
                "url": url,
                "filename": f"{base}_video_{i}.mp4",
            })

        rows.append({
            "front": front,
            "back": back,
            "audio": audio_items,
            "video": video_items,
        })

    write_lesson_outputs(rows, csv_path, media_list_path, audio_dir, video_dir)
    write_import_notes(lesson_dir)

    print(f"Done: {lesson_dir}")
    return {
        "scenario_id": scenario_id,
        "rows": len(rows),
        "folder": lesson_dir,
        "csv": csv_path,
    }


def main():
    scenario_ids = ask_for_scenario_ids()

    if not scenario_ids:
        print("No lesson/scenario IDs found. Exiting.")
        return

    print(f"\nFound {len(scenario_ids)} lesson(s): {', '.join(scenario_ids)}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
        )

        page = context.new_page()
        page.goto("https://app.memrise.com/", wait_until="domcontentloaded")

        input(
            "\nBrowser opened.\n"
            "Log into Memrise if needed, then press ENTER here to start downloading.\n\n"
        )

        successes = []
        failures = []

        for scenario_id in scenario_ids:
            try:
                successes.append(download_lesson(page, scenario_id))
            except Exception as e:
                print(f"\nCould not download lesson {scenario_id}.")
                print(e)
                failures.append((scenario_id, str(e)))

        print("\nAll requested lessons processed.")
        print(f"Successful: {len(successes)}")
        print(f"Failed: {len(failures)}")

        if successes:
            print("\nDownloaded folders:")
            for item in successes:
                print(f"- {item['folder']}")

        if failures:
            print("\nFailures:")
            for scenario_id, error in failures:
                print(f"- {scenario_id}: {error}")

        input("\nPress ENTER to close browser...")
        context.close()


if __name__ == "__main__":
    main()
