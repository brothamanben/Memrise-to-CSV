from pathlib import Path
import csv
import html
import re
import requests
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright


OUTPUT_DIR = Path("memrise_downloads")
PROFILE_DIR = Path("memrise_browser_profile")


def clean_name(value, fallback="item", max_len=60):
    value = str(value or fallback)
    value = re.sub(r'[\\/:*?"<>|]+', "", value)
    value = re.sub(r"\s+", "_", value.strip())
    return value[:max_len] or fallback


def detect_scenario_id(url):
    value = str(url or "").strip()

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


def get_clipboard_text():
    try:
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        text = root.clipboard_get()
        root.destroy()
        return text
    except Exception as e:
        print("\nCould not read the clipboard.")
        print(e)
        return ""


def strip_tags(value):
    value = re.sub(r"<[^>]+>", "\n", value)
    value = html.unescape(value)
    return value.replace("\r", "\n")


def extract_rows_from_visible_text(value):
    text = strip_tags(value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]

    start_markers = [
        "Unmark all as known",
        "Mark all as known",
        "Want to practice again?",
        "Lesson overview",
    ]

    start_index = 0
    for marker in start_markers:
        if marker in lines:
            start_index = lines.index(marker) + 1
            break

    stop_patterns = [
        "https://www.googletagmanager.com/",
        "window.__RUNTIME_CONFIG__",
        "__NEXT_DATA__",
        "data-styled",
        "Error - JavaScript not Loaded",
    ]

    lesson_lines = []
    for line in lines[start_index:]:
        if any(pattern in line for pattern in stop_patterns):
            break

        if re.fullmatch(r"\d+\s*/\s*\d+\s+words and phrases", line, re.I):
            continue
        if line in {"Back to Review", "Practice", "Review", "Start learning"}:
            continue

        lesson_lines.append(line)

    rows = []
    for index in range(0, len(lesson_lines) - 1, 2):
        front = lesson_lines[index]
        back = lesson_lines[index + 1]

        if len(front) > 250 or len(back) > 250:
            continue
        if front.lower() == back.lower():
            continue

        rows.append({
            "front": front,
            "back": back,
            "audio": [],
            "video": [],
        })

    return rows


def ask_for_lesson_id_or_url():
    while True:
        manual = input(
            "\nPaste a Memrise lesson URL or lesson/scenario ID.\n"
            "Leave blank to detect it from the browser: "
        ).strip()

        if not manual:
            return None

        if manual.lower() in {"clipboard", "clip"}:
            rows = extract_rows_from_visible_text(get_clipboard_text())
            if rows:
                return {"rows": rows}

            print(
                "\nI could not find lesson phrase pairs in the clipboard.\n"
                "Copy the visible lesson page text or page HTML, then type clipboard again."
            )
            continue

        scenario_id = detect_scenario_id(manual)
        if scenario_id:
            return scenario_id

        rows = extract_rows_from_visible_text(manual)
        if rows:
            print(
                "\nThat looks like copied page text, but the reliable downloader needs the lesson/scenario ID."
            )
            continue

        print(
            "\nI could not find a lesson/scenario ID in that input.\n"
            "Try a full lesson URL, a URL with scenario_id=12345, or just the number."
        )


def wait_for_lesson_page(context):
    print("\nWaiting for lesson page...")

    while True:
        print("\nTabs the script can see:")

        for p in context.pages:
            try:
                url = p.url
            except Exception:
                continue

            print(" -", url)

            scenario_id = detect_scenario_id(url)
            if scenario_id:
                print(f"\nDetected lesson page: {url}")
                return p, scenario_id

        manual = input(
            "\nCould not auto-detect.\n"
            "Paste the full lesson URL or just the lesson/scenario ID here: "
        ).strip()

        if manual:
            scenario_id = detect_scenario_id(manual)
            if scenario_id:
                return context.pages[-1], scenario_id


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


def write_lesson_outputs(rows, csv_path, media_list_path, audio_dir, video_dir, download_media=True):
    print("\nWriting CSV...")

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Front", "Back", "Audio", "Video"])

        for row in rows:
            audio_field = " ".join(f"[sound:{a['filename']}]" for a in row["audio"])
            video_field = " ".join(f"[sound:{v['filename']}]" for v in row["video"])

            writer.writerow([
                row["front"],
                row["back"],
                audio_field,
                video_field,
            ])

    print(f"CSV saved: {csv_path}")

    print("\nWriting media list...")

    with open(media_list_path, "w", encoding="utf-8") as f:
        for row in rows:
            for audio_item in row["audio"]:
                f.write(f"{audio_item['url']} -> {audio_item['filename']}\n")
                if download_media:
                    download_file(audio_item["url"], audio_dir / audio_item["filename"])

            for video_item in row["video"]:
                f.write(f"{video_item['url']} -> {video_item['filename']}\n")
                if download_media:
                    download_file(video_item["url"], video_dir / video_item["filename"])


def main():
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
            "1. Log into Memrise if needed.\n"
            "2. You can either paste a lesson URL/ID next, or open your lesson page in THIS browser window.\n"
            "3. Press ENTER here when ready.\n\n"
        )

        scenario_id = ask_for_lesson_id_or_url()
        if scenario_id:
            page = context.pages[-1]
        else:
            page, scenario_id = wait_for_lesson_page(context)

        if not scenario_id:
            print("No scenario_id found. Exiting.")
            context.close()
            return

        print(f"\nUsing scenario_id: {scenario_id}")

        lesson_dir = OUTPUT_DIR / f"memrise_{scenario_id}"
        audio_dir = lesson_dir / "audio"
        video_dir = lesson_dir / "video"
        csv_path = lesson_dir / f"memrise_{scenario_id}.csv"
        media_list_path = lesson_dir / "media_links.txt"

        lesson_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(exist_ok=True)
        video_dir.mkdir(exist_ok=True)

        details_url = f"https://app.memrise.com/v1.25/me/scenarios/{scenario_id}/details/"

        print("\nGetting scenario details...")

        try:
            data = fetch_json(page, details_url)
        except Exception as e:
            print("\nCould not get scenario details.")
            print("Possible causes:")
            print("- You are not logged into Memrise in the script browser.")
            print("- The scenario_id is wrong.")
            print("- Memrise changed the API.")
            print("\nError:")
            print(e)
            input("\nPress ENTER to close browser...")
            context.close()
            return

        learnables = data.get("learnables") or []
        learnable_ids = [x.get("learnable_id") for x in learnables if x.get("learnable_id")]

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

            base = f"{index:03d}_{clean_name(front)}"

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

        print("\nDownloading media...")
        write_lesson_outputs(rows, csv_path, media_list_path, audio_dir, video_dir)

        print("\nDone.")
        print(f"Rows: {len(rows)}")
        print(f"Folder: {lesson_dir}")
        print(f"CSV: {csv_path}")
        print(f"Media links: {media_list_path}")

        input("\nPress ENTER to close browser...")
        context.close()


if __name__ == "__main__":
    main()
