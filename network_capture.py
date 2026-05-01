from pathlib import Path
from datetime import datetime
import csv
import json
import re
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, Error as PlaywrightError


OUTPUT_DIR = Path("memrise_network_capture")
PROFILE_DIR = Path("memrise_browser_profile")

START_PAGES = [
    "https://app.memrise.com/learn",
    "https://app.memrise.com/aprender/learn",
]

KEYWORDS = [
    "scenario",
    "scenarios",
    "learnable",
    "learnables",
    "course",
    "courses",
    "level",
    "levels",
    "language_pairs",
    "topics_and_tags",
]

IGNORE_URL_PARTS = [
    "google-analytics.com",
    "googletagmanager.com",
    "facebook.com",
    "google.com/ccm",
    "sentry.io",
    "scenario-icons",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".svg",
    ".gif",
    ".ico",
    ".css",
    ".woff",
    ".woff2",
    ".mp3",
    ".mp4",
]


def clean_filename(text, max_len=160):
    text = re.sub(r"[\\/:*?\"<>|=&%]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text.strip("_")[:max_len] or "response"


def clean_cell(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def should_save(url, content_type=""):
    lower = url.lower()
    lower_content_type = content_type.lower()

    if any(x in lower for x in IGNORE_URL_PARTS):
        return False

    if "app.memrise.com/v" in lower or "app.memrise.com/api" in lower:
        return True

    if "json" in lower_content_type:
        return "memrise" in lower or any(k in lower for k in KEYWORDS)

    return any(k in lower for k in KEYWORDS)


def scenario_link(scenario_id):
    return (
        "https://app.memrise.com/aprender/learn?"
        f"scenario_id={scenario_id}&source=scenarios_tab&back=%2Flearn"
    )


def detect_scenario_id_from_text(value):
    text = str(value or "")

    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    if "scenario_id" in query:
        return query["scenario_id"][0]

    patterns = [
        r"scenario[_-]?id[=/](\d+)",
        r'"scenario_id"\s*:\s*"?(\d+)"?',
        r"'scenario_id'\s*:\s*'?(\d+)'?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)

    return None


def queue_capture_url(response, pending_urls, seen_urls):
    url = response.url

    if url in seen_urls:
        return

    try:
        status = response.status
        content_type = response.headers.get("content-type", "")

        if not should_save(url, content_type):
            return

        seen_urls.add(url)

        if status >= 400:
            print(f"SKIP STATUS {status}: {url}")
            return

        pending_urls.append(
            {
                "url": url,
                "status": status,
                "content_type": content_type,
            }
        )

        print()
        print(f"QUEUED FOR LOGGED-IN FETCH: {url}")
        print("-" * 80)

    except Exception as e:
        print()
        print(f"FAILED TO QUEUE: {url}")
        print(f"Reason: {e}")
        print("-" * 80)


def fetch_text_in_browser(page, url):
    return page.evaluate(
        """
        async (url) => {
            const response = await fetch(url, {
                credentials: "include",
                method: "GET",
            });
            const text = await response.text();

            return {
                ok: response.ok,
                status: response.status,
                contentType: response.headers.get("content-type") || "",
                text,
            };
        }
        """,
        url,
    )


def save_body(url, status, content_type, body, saved):
    if not body or not body.strip():
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    parsed = urlparse(url)

    filename_base = parsed.path.replace("/", "_")
    if parsed.query:
        filename_base += "_" + parsed.query

    filename_base = clean_filename(filename_base)

    is_json = (
        "json" in content_type.lower()
        or body.strip().startswith("{")
        or body.strip().startswith("[")
    )

    if is_json:
        out_path = OUTPUT_DIR / f"{timestamp}_{filename_base}.json"
        try:
            data = json.loads(body)
            out_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            out_path.write_text(body, encoding="utf-8")
    else:
        out_path = OUTPUT_DIR / f"{timestamp}_{filename_base}.txt"
        out_path.write_text(body, encoding="utf-8")

    saved.append(
        {
            "url": url,
            "status": status,
            "content_type": content_type,
            "file": str(out_path),
        }
    )

    print()
    print(f"SAVED: {out_path.name}")
    print(f"URL: {url}")
    print("-" * 80)
    return True


def process_pending_urls(page, pending_urls, saved):
    while pending_urls:
        item = pending_urls.pop(0)
        url = item["url"]

        try:
            result = fetch_text_in_browser(page, url)

            if not result["ok"]:
                print(f"SKIP REFETCH STATUS {result['status']}: {url}")
                continue

            save_body(
                url=url,
                status=result["status"],
                content_type=result["contentType"] or item.get("content_type", ""),
                body=result["text"],
                saved=saved,
            )

        except PlaywrightError as e:
            print()
            print(f"FAILED TO REFETCH: {url}")
            print(f"Reason: {e}")
            print("-" * 80)
        except Exception as e:
            print()
            print(f"FAILED TO REFETCH: {url}")
            print(f"Reason: {e}")
            print("-" * 80)


def save_summary(saved):
    summary_path = OUTPUT_DIR / "capture_summary.json"
    summary_path.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    urls_path = OUTPUT_DIR / "captured_network_urls.txt"
    urls_path.write_text(
        "\n".join(item["url"] for item in saved),
        encoding="utf-8",
    )

    print()
    print(f"Summary saved: {summary_path.resolve()}")
    print(f"Captured URL list saved: {urls_path.resolve()}")


def compact_preview(value, max_len=700):
    text = json.dumps(value, ensure_ascii=False)
    text = re.sub(r"\s+", " ", text)
    return text[:max_len]


def walk_json(value, path="$"):
    yield path, value

    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_json(child, f"{path}[{index}]")


def title_from_dict(value):
    for key in (
        "title",
        "name",
        "display_name",
        "scenario_name",
        "short_name",
        "label",
        "description",
    ):
        if value.get(key):
            return clean_cell(value.get(key))

    return ""


def find_nearby_title(value):
    if not isinstance(value, dict):
        return ""

    title = title_from_dict(value)
    if title:
        return title

    for child in value.values():
        if isinstance(child, dict):
            title = title_from_dict(child)
            if title:
                return title

    return ""


def extract_scenarios_from_data(data, source_file, source_url):
    rows = []

    for path, value in walk_json(data):
        if isinstance(value, dict):
            scenario_id = value.get("scenario_id")

            if scenario_id is None and "scenario" in str(value.get("type", "")).lower():
                scenario_id = value.get("id")

            if scenario_id is not None:
                rows.append(
                    {
                        "scenario_id": str(scenario_id),
                        "title": find_nearby_title(value),
                        "path": path,
                        "source_file": source_file,
                        "source_url": source_url,
                        "preview": compact_preview(value),
                    }
                )

        if isinstance(value, str):
            scenario_id = detect_scenario_id_from_text(value)
            if scenario_id:
                rows.append(
                    {
                        "scenario_id": str(scenario_id),
                        "title": "",
                        "path": path,
                        "source_file": source_file,
                        "source_url": source_url,
                        "preview": value[:700],
                    }
                )

    return rows


def load_summary_by_file():
    summary_path = OUTPUT_DIR / "capture_summary.json"
    if not summary_path.exists():
        return {}

    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    return {Path(item["file"]).name: item["url"] for item in summary if item.get("file")}


def extract_scenarios():
    rows = []
    seen = set()
    url_by_file = load_summary_by_file()

    for path in OUTPUT_DIR.glob("*.json"):
        if path.name == "capture_summary.json":
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        source_url = url_by_file.get(path.name, "")

        for row in extract_scenarios_from_data(data, path.name, source_url):
            scenario_id = clean_cell(row["scenario_id"])
            if not scenario_id or not scenario_id.isdigit():
                continue

            key = (scenario_id, row["source_file"], row["path"])
            if key in seen:
                continue

            seen.add(key)
            row["scenario_id"] = scenario_id
            row["link"] = scenario_link(scenario_id)
            rows.append(row)

    rows.sort(
        key=lambda r: (
            int(r["scenario_id"]) if r["scenario_id"].isdigit() else r["scenario_id"],
            r["source_file"],
            r["path"],
        )
    )

    unique_by_id = {}
    for row in rows:
        unique_by_id.setdefault(row["scenario_id"], row)

    unique_rows = list(unique_by_id.values())

    links_path = OUTPUT_DIR / "memrise_scenario_links.txt"
    csv_path = OUTPUT_DIR / "memrise_scenarios.csv"
    dump_path = OUTPUT_DIR / "scenario_discovery_dump.txt"

    links_path.write_text(
        "\n".join(r["link"] for r in unique_rows),
        encoding="utf-8",
    )

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario_id",
                "title",
                "link",
                "path",
                "source_file",
                "source_url",
                "preview",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    dump_lines = [
        "Memrise Scenario Discovery Dump",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"Unique scenario IDs found: {len(unique_rows)}",
        f"Total scenario-looking records found: {len(rows)}",
        "",
        "Unique scenario links:",
        *[r["link"] for r in unique_rows],
        "",
        "Detailed records:",
    ]

    for row in rows:
        dump_lines.extend(
            [
                "",
                f"scenario_id: {row['scenario_id']}",
                f"title: {row['title']}",
                f"link: {row['link']}",
                f"path: {row['path']}",
                f"source_file: {row['source_file']}",
                f"source_url: {row['source_url']}",
                f"preview: {row['preview']}",
            ]
        )

    dump_path.write_text("\n".join(dump_lines), encoding="utf-8")

    print()
    print(f"Unique scenario IDs extracted: {len(unique_rows)}")
    print(f"Links saved to: {links_path.resolve()}")
    print(f"CSV saved to: {csv_path.resolve()}")
    print(f"Readable dump saved to: {dump_path.resolve()}")


def click_show_more_buttons(page):
    labels = [
        "show more",
        "see more",
        "load more",
        "view more",
        "more",
    ]

    clicked = 0
    for _ in range(8):
        found_one = False

        for label in labels:
            locator = page.get_by_text(re.compile(label, re.I)).first

            try:
                if locator.count() and locator.is_visible(timeout=1000):
                    locator.click(timeout=3000)
                    page.wait_for_timeout(1500)
                    clicked += 1
                    found_one = True
                    print(f"Clicked possible '{label}' button.")
                    break
            except Exception:
                continue

        if not found_one:
            break

    return clicked


def scroll_page(page):
    for _ in range(8):
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(700)


def run_discovery(page):
    for url in START_PAGES:
        print()
        print(f"Opening discovery page: {url}")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            scroll_page(page)
            click_show_more_buttons(page)
            scroll_page(page)
        except Exception as e:
            print(f"Could not fully scan {url}: {e}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    PROFILE_DIR.mkdir(exist_ok=True)

    saved = []
    pending_urls = []
    seen_urls = set()

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 950},
        )

        page = browser.pages[0] if browser.pages else browser.new_page()
        browser.on(
            "response",
            lambda response: queue_capture_url(response, pending_urls, seen_urls),
        )

        print()
        print("Opening Memrise...")
        page.goto("https://app.memrise.com/", wait_until="domcontentloaded")

        input(
            "\nLog into Memrise in the browser window if needed.\n"
            "When you are fully logged in, press ENTER here.\n\n"
        )

        print()
        print("Starting network discovery.")
        print("The script will open the Learn pages, scroll, and click likely Show More buttons.")
        print("You can also click around manually while it is running.")
        print()

        run_discovery(page)
        process_pending_urls(page, pending_urls, saved)

        for _ in range(3):
            process_pending_urls(page, pending_urls, saved)
            save_summary(saved)
            extract_scenarios()
            print()
            print("Still listening. Click around manually now if anything is missing.")
            print("Press CTRL + C to stop, or wait for the next refresh.")
            time.sleep(10)

        print()
        print("Automatic capture window finished.")
        print("You can keep clicking manually, or press CTRL + C.")

        try:
            while True:
                process_pending_urls(page, pending_urls, saved)
                save_summary(saved)
                extract_scenarios()
                print()
                print("Still capturing... press CTRL + C when done.")
                time.sleep(15)

        except KeyboardInterrupt:
            print()
            print("Stopping capture...")
            process_pending_urls(page, pending_urls, saved)
            save_summary(saved)
            extract_scenarios()
            print()
            print("Done.")
            print(f"Send me this file next: {(OUTPUT_DIR / 'scenario_discovery_dump.txt').resolve()}")
            input("Press ENTER to close the browser and exit...")
            try:
                browser.close()
            except Exception as e:
                print()
                print("Browser was already closed or disconnected.")
                print(f"Close warning: {e}")


if __name__ == "__main__":
    main()
