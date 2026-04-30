(() => {
  const MEDIA_RE = /\.(mp3|m4a|wav|ogg|mp4|m4v|webm)(?:[?#].*)?$/i;
  const MEDIA_HINT_RE = /https?:\/\/[^"'\\\s<>]+(?:mp3|m4a|wav|ogg|mp4|m4v|webm|audio|video|media)[^"'\\\s<>]*/gi;
  let captureStartedAt = 0;

  function absoluteUrl(value, baseUrl = location.href) {
    try {
      return new URL(value, baseUrl).href;
    } catch {
      return "";
    }
  }

  function unique(values) {
    return [...new Set(values.filter(Boolean))];
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function visible(node) {
    if (!node) return false;
    const rect = node.getBoundingClientRect?.();
    return Boolean(
      node.offsetParent !== null &&
        rect &&
        rect.width > 0 &&
        rect.height > 0 &&
        rect.bottom >= 0 &&
        rect.right >= 0 &&
        rect.top <= window.innerHeight &&
        rect.left <= window.innerWidth
    );
  }

  async function waitFor(predicate, timeout = 6000, interval = 150) {
    const started = Date.now();
    while (Date.now() - started < timeout) {
      const value = predicate();
      if (value) return value;
      await sleep(interval);
    }
    return predicate();
  }

  function mediaKindFromUrl(url, fallback = "") {
    if (/\.(mp3|m4a|wav|ogg)(?:[?#].*)?$/i.test(url) || /audio/i.test(url)) return "audio";
    if (/\.(mp4|m4v|webm)(?:[?#].*)?$/i.test(url) || /video/i.test(url)) return "video";
    return fallback;
  }

  function collectMediaUrls(root = document, baseUrl = location.href, record = root === document) {
    const items = [];

    function add(raw, kind = "", source = "dom") {
      const url = raw ? absoluteUrl(raw, baseUrl) : "";
      if (!url || !/^https?:/i.test(url)) return;
      if (MEDIA_RE.test(url) || /(?:audio|video|media|mp3|mp4|m3u8)/i.test(url)) {
        items.push({ url, kind: mediaKindFromUrl(url, kind), source });
      }
    }

    root.querySelectorAll("audio, video, source, a[href], [src], [data-src]").forEach((node) => {
      const tag = node.tagName?.toLowerCase();
      const kind = tag === "audio" ? "audio" : tag === "video" ? "video" : "";

      add(node.currentSrc, kind, "player");
      for (const attr of ["src", "href", "data-src", "data-url", "data-media-url", "poster"]) {
        add(node.getAttribute?.(attr), kind, "dom");
      }

      for (const attr of node.getAttributeNames?.() || []) {
        if (/^data-/i.test(attr)) add(node.getAttribute(attr), kind, "data");
      }
    });

    if (root === document) {
      for (const entry of performance.getEntriesByType("resource")) {
        if (entry.startTime < captureStartedAt) continue;
        if (MEDIA_RE.test(entry.name) || /(?:audio|video|media|mp3|mp4|m3u8)/i.test(entry.name)) {
          items.push({ url: entry.name, kind: mediaKindFromUrl(entry.name), source: "performance" });
        }
      }
    }

    const htmlRoot = root.documentElement || root;
    const normalizedHtml = (htmlRoot.innerHTML || "")
      .replace(/\\u002F/g, "/")
      .replace(/\\\//g, "/");
    const htmlMatches = normalizedHtml.match(MEDIA_HINT_RE) || [];
    htmlMatches.forEach((url) => add(url, "", "html"));

    const seen = new Set();
    const clean = items.filter((item) => {
      if (!item.url || seen.has(item.url)) return false;
      seen.add(item.url);
      return true;
    });
    if (record && clean.length) {
      chrome.runtime.sendMessage({ type: "recordMedia", urls: clean, source: "page" }, () => {});
    }
    return clean;
  }

  async function collectKnownMediaUrls() {
    const pageUrls = collectMediaUrls();
    try {
      const response = await chrome.runtime.sendMessage({ type: "getMedia" });
      const items = [...pageUrls, ...(response.media || [])];
      const seen = new Set();
      return items.filter((item) => {
        const url = typeof item === "string" ? item : item.url;
        if (!url || seen.has(url)) return false;
        seen.add(url);
        return true;
      });
    } catch {
      return pageUrls;
    }
  }

  async function clickPlayButtons() {
    const buttons = [...document.querySelectorAll('[data-testid="playButton"], button[aria-label*="Play"], button[title*="Play"]')]
      .filter((button) => !button.disabled && button.offsetParent !== null);

    const before = collectMediaUrls().length;
    let clicked = 0;

    for (const button of buttons) {
      button.scrollIntoView({ block: "center", inline: "center" });
      await sleep(150);
      button.click();
      clicked += 1;
      await sleep(1200);
      collectMediaUrls();
    }

    await sleep(800);
    const after = collectMediaUrls().length;
    return { clicked, before, after, added: Math.max(0, after - before), scan: scan() };
  }

  function mediaUrl(item) {
    return typeof item === "string" ? item : item?.url;
  }

  function mediaAddedAfter(before, after) {
    const known = new Set(before.map(mediaUrl).filter(Boolean));
    return after.filter((item) => {
      const url = mediaUrl(item);
      return url && !known.has(url);
    });
  }

  async function clickMediaControls(root = document) {
    const selectors = [
      '[data-testid="audioPlayer"]',
      '[data-testid="playButton"]',
      'button[aria-label*="Audio"]',
      'button[title*="Audio"]',
      'button[aria-label*="Play"]',
      'button[title*="Play"]',
      'button[aria-label*="Video"]',
      'button[title*="Video"]'
    ].join(",");
    const buttons = [...root.querySelectorAll(selectors)]
      .filter((button) => visible(button) && !button.disabled);
    let clicked = 0;

    for (const button of buttons) {
      button.scrollIntoView({ block: "center", inline: "center" });
      await sleep(100);
      button.click();
      clicked += 1;
      await sleep(900);
      collectMediaUrls();
    }

    return clicked;
  }

  function collectRows(root = document, options = {}) {
    const rows = [];
    const seen = new Set();

    function addFromNode(node) {
      if (options.visibleOnly && !visible(node)) return;
      const spans = [...node.querySelectorAll("span[dir='auto']")]
        .map((span) => span.textContent.trim())
        .filter(Boolean);

      if (spans.length >= 2) {
        const key = `${spans[0]}|${spans[1]}`;
        if (!seen.has(key)) {
          seen.add(key);
          rows.push({
            target: spans[0],
            english: spans[1]
          });
        }
      }
    }

    root.querySelectorAll('[data-testid="learnable_row"], [data-testid*="learnable"], [data-testid*="word"]').forEach((row) => {
      addFromNode(row);
    });

    root.querySelectorAll('[data-testid="playButton"], button[aria-label*="Play"], button[title*="Play"]').forEach((button) => {
      const row =
        button.closest('[data-testid="learnable_row"], [data-testid*="learnable"], [data-testid*="word"]') ||
        button.closest("li, tr, article, section, div");
      if (row) addFromNode(row);
    });

    return rows;
  }

  function collectScenarioRows() {
    const rows = [...document.querySelectorAll('[data-testid="learnable_row"]')];
    return rows.length ? rows : [...document.querySelectorAll('[data-testid*="learnable"]')];
  }

  async function openWordDetailFromRow(row) {
    const opener =
      row.querySelector('button[aria-label*="See More"], button[title*="See More"], [data-testid="more"]')?.closest("button") ||
      row;
    opener.scrollIntoView({ block: "center", inline: "center" });
    await sleep(100);
    opener.click();

    await waitFor(
      () =>
        document.querySelector('[data-testid="audio_section"], [data-testid="audioPlayer"], [data-testid="learnable_source"]'),
      5000
    );
  }

  async function closeTopLayer() {
    const closeButton = [...document.querySelectorAll('button[aria-label*="Close"], button[title*="Close"], [data-testid*="close"]')]
      .find((button) => visible(button));
    if (closeButton) {
      closeButton.click();
    } else {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true }));
    }
    await sleep(600);
  }

  function collectCurrentRows() {
    const allRows = collectRows(document);
    if (allRows.length) return allRows;
    const visibleRows = collectRows(document, { visibleOnly: true });
    return visibleRows;
  }

  async function detectCurrentTopic({ activate = false, resetCapture = false } = {}) {
    if (resetCapture) captureStartedAt = performance.now();

    const title =
      document.querySelector("[data-testid='scenario-header'] h2")?.textContent.trim() ||
      document.querySelector("h1")?.textContent.trim() ||
      document.querySelector("h2")?.textContent.trim() ||
      document.title ||
      "memrise_topic";

    collectMediaUrls();
    const rowsBeforeDetails = collectCurrentRows();
    let clicked = 0;

    if (activate) {
      clicked += await clickMediaControls();
      const rowCount = collectScenarioRows().length;

      for (let index = 0; index < rowCount; index += 1) {
        const rows = collectScenarioRows();
        const row = rows[index];
        if (!row) continue;
        const before = await collectKnownMediaUrls();
        await openWordDetailFromRow(row);
        clicked += await clickMediaControls();
        await sleep(400);
        const after = await collectKnownMediaUrls();
        if (rowsBeforeDetails[index]) rowsBeforeDetails[index].media = mediaAddedAfter(before, after);
        await closeTopLayer();
        await sleep(250);
      }

      clicked += await clickMediaControls();
    }

    await sleep(800);
    collectMediaUrls();
    const rows = collectCurrentRows();
    const outputRows = rows.length
      ? rows.map((row, index) => ({ ...row, media: rowsBeforeDetails[index]?.media || row.media || [] }))
      : rowsBeforeDetails;
    const media = await collectKnownMediaUrls();

    return {
      title,
      url: location.href,
      rows: outputRows,
      media,
      clicked
    };
  }

  function parseDocument(root, baseUrl = location.href) {
    const title =
      root.querySelector("[data-testid='scenario-header'] h2")?.textContent.trim() ||
      root.querySelector("h1")?.textContent.trim() ||
      root.querySelector("h2")?.textContent.trim() ||
      root.title ||
      "memrise";

    return {
      title,
      url: baseUrl,
      rows: collectRows(root),
      media: collectMediaUrls(root, baseUrl, root === document),
      scenarios: []
    };
  }

  function scan() {
    return parseDocument(document, location.href);
  }

  collectMediaUrls();
  window.addEventListener("play", () => collectMediaUrls(), true);
  window.addEventListener("loadedmetadata", () => collectMediaUrls(), true);
  window.addEventListener("click", () => setTimeout(collectMediaUrls, 500), true);

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "scanMemrisePage") {
      sendResponse(scan());
      return true;
    }
    if (message?.type === "clickPlayButtons") {
      clickPlayButtons().then(sendResponse);
      return true;
    }
    if (message?.type === "detectCurrentTopic") {
      detectCurrentTopic({
        activate: Boolean(message.activate),
        resetCapture: Boolean(message.resetCapture)
      }).then(sendResponse);
      return true;
    }
    return false;
  });
})();
