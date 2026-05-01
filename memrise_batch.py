from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import re
import threading

from playwright.sync_api import sync_playwright

from memrise_single_lesson import (
    OUTPUT_DIR,
    PROFILE_DIR,
    clean_name,
    detect_scenario_id,
    download_file,
    get_clipboard_text,
)


MAX_MEDIA_WORKERS = 12

print_lock = threading.Lock()


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


def fetch_lessons_in_browser(page, scenario_ids):
    return page.evaluate(
        """
        async (scenarioIds) => {
            const getJson = async (url) => {
                const response = await fetch(url, { credentials: "include" });
                const text = await response.text();

                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${text.slice(0, 500)}`);
                }

                return JSON.parse(text);
            };

            return await Promise.all(scenarioIds.map(async (scenarioId) => {
                try {
                    const scenario = await getJson(
                        `https://app.memrise.com/v1.25/me/scenarios/${scenarioId}/details/`
                    );
                    const learnableIds = (scenario.learnables || [])
                        .map((item) => item.learnable_id)
                        .filter(Boolean);

                    const learnables = await Promise.all(learnableIds.map(
                        async (learnableId, index) => {
                            const details = await getJson(
                                `https://app.memrise.com/v1.25/learnable_details/${learnableId}/`
                            );

                            return {
                                index: index + 1,
                                learnable_id: learnableId,
                                details,
                            };
                        }
                    ));

                    return {
                        scenario_id: scenarioId,
                        learnable_count: learnableIds.length,
                        learnables,
                    };
                } catch (error) {
                    return {
                        scenario_id: scenarioId,
                        error: String(error && error.message ? error.message : error),
                    };
                }
            }));
        }
        """,
        scenario_ids,
    )


def rows_from_lesson_payload(payload):
    rows = []

    for item in sorted(payload["learnables"], key=lambda value: value["index"]):
        index = item["index"]
        details = item["details"]
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

        rows.append(
            {
                "front": front,
                "back": back,
                "audio": audio_items,
                "video": video_items,
            }
        )

    return rows


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


def export_lesson_payload(payload):
    scenario_id = payload["scenario_id"]
    log(f"\n[{scenario_id}] Getting scenario details...")

    lesson_dir = OUTPUT_DIR / f"memrise_{scenario_id}"
    audio_dir = lesson_dir / "audio"
    video_dir = lesson_dir / "video"
    csv_path = lesson_dir / f"memrise_{scenario_id}.csv"
    media_list_path = lesson_dir / "media_links.txt"

    audio_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)

    log(f"[{scenario_id}] Found {payload['learnable_count']} learnables.")
    rows = rows_from_lesson_payload(payload)

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
            "Log into Memrise if needed, then press ENTER here.\n\n"
        )

        scenario_ids = collect_scenario_ids()
        if not scenario_ids:
            print("\nNo lesson/scenario IDs found. Exiting.")
            context.close()
            return

        print("\nLessons queued:")
        for scenario_id in scenario_ids:
            print(" -", scenario_id)

        print("\nReading lessons through the logged-in browser...")
        lesson_payloads = fetch_lessons_in_browser(page, scenario_ids)

        results = []
        errors = []

        for payload in lesson_payloads:
            if payload.get("error"):
                errors.append((payload["scenario_id"], payload["error"]))
                log(f"\n[{payload['scenario_id']}] Failed: {payload['error']}")

        successful_payloads = [payload for payload in lesson_payloads if not payload.get("error")]

        with ThreadPoolExecutor(max_workers=min(len(successful_payloads), 4) or 1) as executor:
            futures = {
                executor.submit(export_lesson_payload, payload): payload["scenario_id"]
                for payload in successful_payloads
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
