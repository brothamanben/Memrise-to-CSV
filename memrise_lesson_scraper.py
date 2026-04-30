"""
Experimental Memrise single-lesson scraper.

Install once:
  py -m pip install playwright
  py -m playwright install chromium

Try it:
  py memrise_lesson_scraper.py --url https://app.memrise.com/

Then log in, click into one scenario/lesson so the word list is visible,
play any stubborn media manually if needed, and press Enter in this terminal.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page, Response, sync_playwright


ROOT = Path(__file__).resolve().parent
MEDIA_RE = re.compile(r"\.(mp3|m4a|wav|ogg|mp4|m4v|webm)(?:[?#].*)?$", re.I)
MEDIA_HINT_RE = re.compile(r"(audio|video|media|mp3|mp4|m4a|m4v|webm|m3u8)", re.I)


def safe_name(value: str, fallback: str = "file") -> str:
    cleaned = re.sub(r"[^\w .-]+", "", value or "", flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)[:80]
    return cleaned or fallback


def media_kind(url: str) -> str:
    if re.search(r"\.(mp3|m4a|wav|ogg)(?:[?#].*)?$", url, re.I) or "audio" in url.lower():
        return "audio"
    if re.search(r"\.(mp4|m4v|webm)(?:[?#].*)?$", url, re.I) or "video" in url.lower():
        return "video"
    return ""


def looks_like_media(url: str) -> bool:
    return bool(url and (MEDIA_RE.search(url) or MEDIA_HINT_RE.search(url)))


def ext_from_url(url: str, fallback: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    return suffix or fallback


def unique_media(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for item in items:
        url = item.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(item)
    return out


def collect_dom(page: Page) -> dict:
    return page.evaluate(
        r"""
() => {
  const mediaRe = /\.(mp3|m4a|wav|ogg|mp4|m4v|webm)(?:[?#].*)?$/i;
  const hintRe = /https?:\/\/[^"'\\\s<>]+(?:mp3|m4a|wav|ogg|mp4|m4v|webm|audio|video|media)[^"'\\\s<>]*/gi;

  function abs(value) {
    try { return new URL(value, location.href).href; } catch { return ""; }
  }

  function kind(url, fallback = "") {
    if (/\.(mp3|m4a|wav|ogg)(?:[?#].*)?$/i.test(url) || /audio/i.test(url)) return "audio";
    if (/\.(mp4|m4v|webm)(?:[?#].*)?$/i.test(url) || /video/i.test(url)) return "video";
    return fallback;
  }

  const rows = [];
  const rowSeen = new Set();
  function addRow(node) {
    const spans = [...node.querySelectorAll("span[dir='auto']")]
      .map((span) => span.textContent.trim())
      .filter(Boolean);
    if (spans.length < 2) return;
    const key = `${spans[0]}|${spans[1]}`;
    if (rowSeen.has(key)) return;
    rowSeen.add(key);
    rows.push({ target: spans[0], english: spans[1], media: [] });
  }

  document.querySelectorAll('[data-testid="learnable_row"], [data-testid*="learnable"], [data-testid*="word"]')
    .forEach(addRow);

  const media = [];
  function addMedia(raw, fallback = "", source = "dom") {
    const url = abs(raw);
    if (!url) return;
    if (mediaRe.test(url) || /audio|video|media|mp3|mp4|m3u8/i.test(url)) {
      media.push({ url, kind: kind(url, fallback), source });
    }
  }

  document.querySelectorAll("audio, video, source, a[href], [src], [data-src]").forEach((node) => {
    const tag = (node.tagName || "").toLowerCase();
    const fallback = tag === "audio" ? "audio" : tag === "video" ? "video" : "";
    addMedia(node.currentSrc, fallback, "player");
    ["src", "href", "data-src", "data-url", "data-media-url", "poster"].forEach((attr) => addMedia(node.getAttribute(attr), fallback));
    [...node.getAttributeNames()].filter((attr) => /^data-/i.test(attr)).forEach((attr) => addMedia(node.getAttribute(attr), fallback, "data"));
  });

  const html = document.documentElement.innerHTML.replace(/\\u002F/g, "/").replace(/\\\//g, "/");
  (html.match(hintRe) || []).forEach((url) => addMedia(url, "", "html"));

  return {
    title: document.querySelector("[data-testid='scenario-header'] h2")?.textContent.trim()
      || document.querySelector("h1")?.textContent.trim()
      || document.querySelector("h2")?.textContent.trim()
      || document.title
      || "memrise_lesson",
    url: location.href,
    rows,
    media
  };
}
"""
    )


def click_media_controls(page: Page) -> int:
    selectors = [
        '[data-testid="audioPlayer"]',
        '[data-testid="playButton"]',
        'button[aria-label*="Audio"]',
        'button[title*="Audio"]',
        'button[aria-label*="Play"]',
        'button[title*="Play"]',
        'button[aria-label*="Video"]',
        'button[title*="Video"]',
    ]
    clicked = 0
    for selector in selectors:
        for button in page.locator(selector).all():
            try:
                if button.is_visible() and button.is_enabled():
                    button.scroll_into_view_if_needed(timeout=1500)
                    button.click(timeout=1500)
                    clicked += 1
                    page.wait_for_timeout(700)
            except Exception:
                pass
    return clicked


def reveal_words(page: Page, network_media: list[dict]) -> list[dict]:
    data = collect_dom(page)
    rows = data["rows"]
    row_locator = page.locator('[data-testid="learnable_row"], [data-testid*="learnable"]')
    count = row_locator.count()

    for index in range(count):
        before = {item["url"] for item in network_media}
        try:
            row = row_locator.nth(index)
            row.scroll_into_view_if_needed(timeout=3000)
            row.click(timeout=3000)
            page.wait_for_timeout(600)
            click_media_controls(page)
            page.wait_for_timeout(900)
        except Exception:
            continue

        added = [item for item in network_media if item["url"] not in before]
        if index < len(rows):
            rows[index]["media"] = unique_media(added)

        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        except Exception:
            pass

    return rows


def write_outputs(data: dict, network_media: list[dict], download: bool, page: Page) -> Path:
    lesson_dir = ROOT / "scrape-output" / safe_name(data.get("title", "memrise_lesson"), "memrise_lesson")
    lesson_dir.mkdir(parents=True, exist_ok=True)

    all_media = unique_media([*data.get("media", []), *network_media])
    rows = data.get("rows", [])

    with (lesson_dir / "raw.json").open("w", encoding="utf-8") as f:
        json.dump({**data, "network_media": network_media, "all_media": all_media}, f, indent=2, ensure_ascii=False)

    with (lesson_dir / "lesson.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Target", "English", "Audio URLs", "Video URLs"])
        for index, row in enumerate(rows):
            row_media = row.get("media") or []
            if not row_media:
                audio = [item for item in all_media if item.get("kind") == "audio"]
                video = [item for item in all_media if item.get("kind") == "video"]
                row_media = ([audio[index]] if index < len(audio) else []) + ([video[index]] if index < len(video) else [])
            writer.writerow([
                row.get("target", ""),
                row.get("english", ""),
                " ".join(item["url"] for item in row_media if item.get("kind") == "audio"),
                " ".join(item["url"] for item in row_media if item.get("kind") == "video"),
            ])

    if download:
        media_dir = lesson_dir / "media"
        media_dir.mkdir(exist_ok=True)
        for index, item in enumerate(all_media, start=1):
            url = item["url"]
            kind = item.get("kind") or media_kind(url) or "media"
            ext = ext_from_url(url, "mp3" if kind == "audio" else "mp4")
            filename = media_dir / f"{index:03d}_{kind}.{ext}"
            try:
                response = page.context.request.get(url, timeout=20000)
                if response.ok:
                    filename.write_bytes(response.body())
            except Exception as error:
                print(f"Could not download {url}: {error}")

    return lesson_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://app.memrise.com/", help="Starting URL.")
    parser.add_argument("--reveal", action="store_true", help="Try clicking every word and visible media control.")
    parser.add_argument("--download", action="store_true", help="Try downloading detected media files.")
    parser.add_argument("--profile", default=str(ROOT / "chrome-scrape-profile"), help="Persistent Chrome profile folder.")
    args = parser.parse_args()

    network_media: list[dict] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            args.profile,
            channel="chrome",
            headless=False,
            viewport={"width": 1400, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Response) -> None:
            url = response.url
            content_type = response.headers.get("content-type", "")
            if looks_like_media(url) or "audio" in content_type or "video" in content_type:
                network_media.append({
                    "url": url,
                    "kind": media_kind(url) or ("audio" if "audio" in content_type else "video" if "video" in content_type else ""),
                    "source": "network",
                    "seen_at": time.time(),
                })

        page.on("response", on_response)
        page.goto(args.url)

        print("Chrome is open. Log in and click into one Memrise lesson/scenario so the word list is visible.")
        print("Play any stubborn audio/video manually if you want the network watcher to catch it.")
        input("Press Enter here to scrape the current lesson...")

        data = collect_dom(page)
        if args.reveal:
            print("Trying to open each word and click visible media controls...")
            data["rows"] = reveal_words(page, network_media)
            data["media"] = unique_media([*data.get("media", []), *collect_dom(page).get("media", [])])

        output_dir = write_outputs(data, unique_media(network_media), args.download, page)
        print(f"Wrote scrape output to: {output_dir}")
        input("Press Enter to close Chrome...")
        context.close()


if __name__ == "__main__":
    main()
