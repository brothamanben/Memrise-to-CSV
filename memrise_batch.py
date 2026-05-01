from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import csv
import re
import threading

import requests
from playwright.sync_api import sync_playwright

from memrise import (
    OUTPUT_DIR,
    PROFILE_DIR,
    clean_name,
    detect_scenario_id,
    download_file,
    get_clipboard_text,
)


MAX_LESSON_WORKERS = 4
MAX_LEARNABLE_WORKERS = 12
MAX_MEDIA_WORKERS = 12

print_lock = threading.Lock()
thread_local = threading.local()


def log(message=""):
    with print_lock:
        print(message)


def extract_scenario_ids(value):
    value = str(value or "")
    ids = []

    candidates = re.findall(
        r"https?://[^\s,;<>\"']+|scenario[_-]?id[=/]\d+|\b\d{4,}\b",
        value,
        flags=re.I,
    )

    for candidate in candidates:
        scenario_id = detect_scenario_id(candidate)
        if scenario_id and scenario_id not in ids:
            ids.append(scenario_id)

    # Some copied URLs can contain punctuation that defeats the first pass.
    for line in value.splitlines():
        scenario_id = detect_scenario_id(line.strip())
        if scenario_id and scenario_id not in ids:
            ids.append(scenario_id)

    return ids


def collect_scenario_ids():
    print(
        "\nPaste Memrise lesson URLs or lesson/scenario IDs.\n"
        "- Paste a batch, then press ENTER on a blank line.\n"
        "- Or paste them one by one; finish with a blank line.\n"
        "- Type clipboard to read links/IDs from your clipboard.\n"
    )

    lines = []
    while True:
        line = input("> ").strip()
        if not line:
            break

        if line.lower() in {"clipboard", "clip"}:
            clipboard_ids = extract_scenario_ids(get_clipboard_text())
            if clipboard_ids:
                return clipboard_ids

            print("No lesson/scenario IDs found in the clipboard.")
            continue

        lines.append(line)

    scenario_ids = extract_scenario_ids("\n".join(lines))
    return scenario_ids


def build_auth_config_from_browser_context(context):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://app.memrise.com/",
    }

    cookies = context.cookies("https://app.memrise.com")
    return {"headers": headers, "cookies": cookies}


def build_session(auth_config):
    session = requests.Session()
    session.headers.update(auth_config["headers"])

    for cookie in auth_config["cookies"]:
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )

    return session


def get_thread_session(auth_config):
    session = getattr(thread_local, "session", None)
    if session is None:
        session = build_session(auth_config)
        thread_local.session = session
    return session


def get_json(auth_config, url):
    session = get_thread_session(auth_config)
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_learnable(auth_config, index, learnable_id):
    details = get_json(
        auth_config,
        f"https://app.memrise.com/v1.25/learnable_details/{learnable_id}/",
    )

    front = details.get("source_value", "") or ""
    back = details.get("target_value", "") or ""
    base = f"{index:03d}_{clean_name(front)}"

    audio_items = []
    for i, url in enumerate(details.get("audio_urls") or [], start=1):
        audio_items.append(
            {
                "url": url,
                "filename": f"{base}_audio_{i}.mp3",
            }
        )

    video_items = []
    for i, url in enumerate(details.get("video_urls") or [], start=1):
        video_items.append(
            {
                "url": url,
                "filename": f"{base}_video_{i}.mp4",
            }
        )

    return {
        "front": front,
        "back": back,
        "audio": audio_items,
        "video": video_items,
    }


def write_csv(rows, csv_path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Front", "Back", "Audio", "Video"])

        for row in rows:
            audio_field = " ".join(f"[sound:{a['filename']}]" for a in row["audio"])
            video_field = " ".join(f"[sound:{v['filename']}]" for v in row["video"])
            writer.writerow([row["front"], row["back"], audio_field, video_field])


def write_media_list(rows, media_list_path):
    with open(media_list_path, "w", encoding="utf-8") as f:
        for row in rows:
            for audio_item in row["audio"]:
                f.write(f"{audio_item['url']} -> {audio_item['filename']}\n")

            for video_item in row["video"]:
                f.write(f"{video_item['url']} -> {video_item['filename']}\n")


def download_lesson_media(rows, audio_dir, video_dir):
    jobs = []

    with ThreadPoolExecutor(max_workers=MAX_MEDIA_WORKERS) as executor:
        for row in rows:
            for audio_item in row["audio"]:
                jobs.append(
                    executor.submit(
                        download_file,
                        audio_item["url"],
                        audio_dir / audio_item["filename"],
                    )
                )

            for video_item in row["video"]:
                jobs.append(
                    executor.submit(
                        download_file,
                        video_item["url"],
                        video_dir / video_item["filename"],
                    )
                )

        for future in as_completed(jobs):
            future.result()


def export_lesson(auth_config, scenario_id):
    log(f"\n[{scenario_id}] Getting scenario details...")

    lesson_dir = OUTPUT_DIR / f"memrise_{scenario_id}"
    audio_dir = lesson_dir / "audio"
    video_dir = lesson_dir / "video"
    csv_path = lesson_dir / f"memrise_{scenario_id}.csv"
    media_list_path = lesson_dir / "media_links.txt"

    audio_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)

    data = get_json(
        auth_config,
        f"https://app.memrise.com/v1.25/me/scenarios/{scenario_id}/details/",
    )
    learnables = data.get("learnables") or []
    learnable_ids = [
        item.get("learnable_id") for item in learnables if item.get("learnable_id")
    ]

    log(f"[{scenario_id}] Found {len(learnable_ids)} learnables.")

    rows_by_index = {}
    with ThreadPoolExecutor(max_workers=MAX_LEARNABLE_WORKERS) as executor:
        futures = {
            executor.submit(fetch_learnable, auth_config, index, learnable_id): index
            for index, learnable_id in enumerate(learnable_ids, start=1)
        }

        for future in as_completed(futures):
            index = futures[future]
            rows_by_index[index] = future.result()
            log(f"[{scenario_id}] Read {len(rows_by_index)}/{len(learnable_ids)}")

    rows = [rows_by_index[index] for index in sorted(rows_by_index)]

    log(f"[{scenario_id}] Writing CSV and media list...")
    write_csv(rows, csv_path)
    write_media_list(rows, media_list_path)

    log(f"[{scenario_id}] Downloading media...")
    download_lesson_media(rows, audio_dir, video_dir)

    return {
        "scenario_id": scenario_id,
        "rows": len(rows),
        "folder": lesson_dir,
        "csv": csv_path,
        "media_links": media_list_path,
    }


def main():
    scenario_ids = collect_scenario_ids()
    if not scenario_ids:
        print("\nNo lesson/scenario IDs found. Exiting.")
        return

    print("\nLessons queued:")
    for scenario_id in scenario_ids:
        print(" -", scenario_id)

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
            "Log into Memrise if needed, then press ENTER here to start the batch.\n\n"
        )

        auth_config = build_auth_config_from_browser_context(context)

        results = []
        errors = []

        with ThreadPoolExecutor(max_workers=MAX_LESSON_WORKERS) as executor:
            futures = {
                executor.submit(export_lesson, auth_config, scenario_id): scenario_id
                for scenario_id in scenario_ids
            }

            for future in as_completed(futures):
                scenario_id = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    errors.append((scenario_id, e))
                    log(f"\n[{scenario_id}] Failed: {e}")

        print("\nBatch complete.")

        if results:
            print("\nFinished lessons:")
            for result in sorted(results, key=lambda item: scenario_ids.index(item["scenario_id"])):
                print(f"- {result['scenario_id']}: {result['rows']} rows")
                print(f"  Folder: {result['folder']}")
                print(f"  CSV: {result['csv']}")
                print(f"  Media links: {result['media_links']}")

        if errors:
            print("\nFailed lessons:")
            for scenario_id, error in errors:
                print(f"- {scenario_id}: {error}")

        input("\nPress ENTER to close browser...")
        context.close()


if __name__ == "__main__":
    main()
