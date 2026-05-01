from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
import csv
import re
import sys
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


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def start_command_log():
    log_dir = Path("memrise_logs")
    log_dir.mkdir(exist_ok=True)

    log_path = log_dir / f"memrise_batch_{datetime.now():%Y%m%d_%H%M%S}.log"
    log_file = open(log_path, "w", encoding="utf-8", buffering=1)

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = Tee(original_stderr, log_file)

    print(f"Command log: {log_path.resolve()}")
    return log_file


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


def choose_input_mode():
    capture_count = len(load_scenarios_from_capture_csv())
    capture_label = (
        f"Use saved network capture file ({capture_count} IDs found)"
        if capture_count
        else "Use saved network capture file (none found yet)"
    )

    print(
        "\nHow do you want to choose lessons?\n"
        "1. Auto-discover scenario IDs from Memrise\n"
        "2. Paste lesson URLs or IDs manually\n"
        f"3. {capture_label}\n"
    )

    choice = input("Choose 1, 2, or 3 [3 if capture exists, otherwise 1]: ").strip()

    if choice == "2":
        return "manual"
    if choice == "3" or (not choice and capture_count):
        return "capture"
    return "auto"


def save_discovered_scenarios(scenarios):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUTPUT_DIR / "memrise_discovered_scenarios.csv"
    links_path = OUTPUT_DIR / "memrise_discovered_scenario_links.txt"

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario_id",
                "title",
                "topic_id",
                "topic_name",
                "number_of_learnables",
                "link",
            ],
        )
        writer.writeheader()
        writer.writerows(scenarios)

    links_path.write_text(
        "\n".join(item["link"] for item in scenarios),
        encoding="utf-8",
    )

    print(f"\nDiscovered scenario CSV: {csv_path}")
    print(f"Discovered scenario links: {links_path}")


def load_scenarios_from_capture_csv():
    capture_csv = Path("memrise_network_capture") / "memrise_scenarios.csv"
    if not capture_csv.exists():
        return []

    scenarios = []
    seen = set()

    with open(capture_csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            scenario_id = str(row.get("scenario_id") or "").strip()
            if not scenario_id or not scenario_id.isdigit() or scenario_id in seen:
                continue

            seen.add(scenario_id)
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "title": row.get("title", ""),
                    "topic_id": "",
                    "topic_name": "",
                    "number_of_learnables": "",
                    "link": row.get("link")
                    or (
                        "https://app.memrise.com/aprender/learn?"
                        f"scenario_id={scenario_id}&source=scenarios_tab&back=%2Flearn"
                    ),
                }
            )

    return scenarios


def discover_scenarios_in_browser(page, language_pair_id=None):
    return page.evaluate(
        """
        async ({ languagePairId }) => {
            const getJson = async (url, required = true) => {
                const response = await fetch(url, { credentials: "include" });
                const text = await response.text();

                if (!response.ok) {
                    if (!required) {
                        return null;
                    }

                    throw new Error(`HTTP ${response.status}: ${url}\\n${text.slice(0, 500)}`);
                }

                return JSON.parse(text);
            };

            const walk = function* (value) {
                yield value;

                if (Array.isArray(value)) {
                    for (const item of value) {
                        yield* walk(item);
                    }
                } else if (value && typeof value === "object") {
                    for (const item of Object.values(value)) {
                        yield* walk(item);
                    }
                }
            };

            const detectLanguagePairId = async () => {
                if (languagePairId) {
                    return String(languagePairId);
                }

                const progressMatch = location.href.match(/language_pairs\\/(\\d+)/);
                if (progressMatch) {
                    return progressMatch[1];
                }

                const me = await getJson("https://app.memrise.com/v1.25/me/", false);
                if (me) {
                    for (const value of walk(me)) {
                        if (value && typeof value === "object") {
                            for (const [key, item] of Object.entries(value)) {
                                if (/language.*pair.*id/i.test(key) && /^\\d+$/.test(String(item))) {
                                    return String(item);
                                }
                            }
                        }
                    }
                }

                const pairs = await getJson("https://app.memrise.com/v1.25/me/language_pairs/", false);
                if (pairs) {
                    for (const value of walk(pairs)) {
                        if (value && typeof value === "object") {
                            const id =
                                value.language_pair_id ||
                                value.languagePairId ||
                                value.id ||
                                value.pk;

                            if (id && /^\\d+$/.test(String(id))) {
                                return String(id);
                            }
                        }
                    }
                }

                return null;
            };

            const pairId = await detectLanguagePairId();
            if (!pairId) {
                throw new Error("Could not detect the Memrise language_pair_id.");
            }

            const topicIds = new Set();
            const topicNames = new Map();

            const topicUrls = [
                `https://app.memrise.com/v1.25/me/language_pairs/${pairId}/topics_and_tags/?filters=uncompleted_scenarios`,
                `https://app.memrise.com/v1.25/me/language_pairs/${pairId}/topics_and_tags/`,
                `https://app.memrise.com/v1.25/me/language_pairs/${pairId}/scenario_counts/`,
            ];

            for (const url of topicUrls) {
                const data = await getJson(url, false);
                if (!data) {
                    continue;
                }

                for (const value of walk(data)) {
                    if (value && typeof value === "object") {
                        const topicId = value.topic_id || value.topicId || value.id;
                        const name = value.name || value.title || value.label || "";

                        if (topicId && /^\\d+$/.test(String(topicId))) {
                            topicIds.add(String(topicId));
                            if (name) {
                                topicNames.set(String(topicId), String(name));
                            }
                        }
                    }
                }
            }

            const scenarioMap = new Map();
            const scenarioTypes = ["up_next", "in_progress", "completed"];
            const immerseOptions = ["false", "true"];
            const topicOptions = [null, ...topicIds];
            const limit = 5;

            const addScenario = (scenario) => {
                if (!scenario || !scenario.scenario_id) {
                    return;
                }

                const scenarioId = String(scenario.scenario_id);
                const topic = scenario.topic || {};
                const topicId = topic.topic_id ? String(topic.topic_id) : "";
                const topicName = topic.name || topicNames.get(topicId) || "";

                if (!scenarioMap.has(scenarioId)) {
                    scenarioMap.set(scenarioId, {
                        scenario_id: scenarioId,
                        title: scenario.title || scenario.name || "",
                        topic_id: topicId,
                        topic_name: topicName,
                        number_of_learnables: scenario.number_of_learnables || "",
                        link:
                            `https://app.memrise.com/aprender/learn?scenario_id=${scenarioId}` +
                            "&source=scenarios_tab&back=%2Flearn",
                    });
                }
            };

            for (const scenarioType of scenarioTypes) {
                for (const hasImmerseContent of immerseOptions) {
                    for (const topicId of topicOptions) {
                        for (let offset = 0; offset < 5000; offset += limit) {
                            const params = new URLSearchParams({
                                free_only: "false",
                                has_immerse_content: hasImmerseContent,
                                limit: String(limit),
                                offset: String(offset),
                                scenario_type: scenarioType,
                            });

                            if (topicId) {
                                params.set("topic_id", topicId);
                            }

                            const url =
                                `https://app.memrise.com/v1.25/me/language_pairs/${pairId}/scenarios/?` +
                                params.toString();
                            const data = await getJson(url, false);

                            if (!data) {
                                break;
                            }

                            const scenarios = Array.isArray(data.scenarios) ? data.scenarios : [];
                            scenarios.forEach(addScenario);

                            if (!data.has_more_pages || scenarios.length === 0) {
                                break;
                            }
                        }
                    }
                }
            }

            return {
                language_pair_id: pairId,
                topic_count: topicIds.size,
                scenarios: [...scenarioMap.values()].sort((a, b) => {
                    return Number(a.scenario_id) - Number(b.scenario_id);
                }),
            };
        }
        """,
        {"languagePairId": language_pair_id},
    )


def fetch_lessons_in_browser_chunks(page, scenario_ids, chunk_size=8):
    payloads = []

    for start in range(0, len(scenario_ids), chunk_size):
        chunk = scenario_ids[start:start + chunk_size]
        print(
            f"Reading lesson details {start + 1}-"
            f"{start + len(chunk)} of {len(scenario_ids)}..."
        )
        payloads.extend(fetch_lessons_in_browser(page, chunk))

    return payloads


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
    command_log = start_command_log()

    try:
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

            input_mode = choose_input_mode()

            if input_mode == "capture":
                print("\nLoading scenario IDs from memrise_network_capture\\memrise_scenarios.csv...")
                scenarios = load_scenarios_from_capture_csv()
                print(f"Scenarios loaded from capture CSV: {len(scenarios)}")

                save_discovered_scenarios(scenarios)
                scenario_ids = [item["scenario_id"] for item in scenarios]
            elif input_mode == "auto":
                language_pair_id = input(
                    "\nLanguage pair ID to scan.\n"
                    "Leave blank to auto-detect from your Memrise account: "
                ).strip()

                print("\nDiscovering scenarios through the logged-in browser...")
                discovery = discover_scenarios_in_browser(page, language_pair_id or None)
                scenarios = discovery["scenarios"]

                print(
                    f"\nLanguage pair: {discovery['language_pair_id']}\n"
                    f"Topics scanned: {discovery['topic_count']}\n"
                    f"Scenarios discovered: {len(scenarios)}"
                )

                if not scenarios:
                    print(
                        "\nLive discovery did not return scenarios. "
                        "Trying the saved network-capture CSV..."
                    )
                    scenarios = load_scenarios_from_capture_csv()
                    print(f"Scenarios loaded from capture CSV: {len(scenarios)}")

                save_discovered_scenarios(scenarios)
                scenario_ids = [item["scenario_id"] for item in scenarios]
            else:
                scenario_ids = collect_scenario_ids()

            scenario_ids = [
                str(scenario_id).strip()
                for scenario_id in scenario_ids
                if str(scenario_id).strip().isdigit()
            ]

            if not scenario_ids:
                print("\nNo lesson/scenario IDs found. Exiting.")
                context.close()
                return

            print("\nLessons queued:")
            for scenario_id in scenario_ids:
                print(" -", scenario_id)

            print("\nReading lessons through the logged-in browser...")
            lesson_payloads = fetch_lessons_in_browser_chunks(page, scenario_ids)

            results = []
            errors = []

            for payload in lesson_payloads:
                if payload.get("error"):
                    errors.append((payload["scenario_id"], payload["error"]))
                    log(f"\n[{payload['scenario_id']}] Failed: {payload['error']}")

            successful_payloads = [
                payload for payload in lesson_payloads if not payload.get("error")
            ]

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
    finally:
        command_log.close()


if __name__ == "__main__":
    main()
