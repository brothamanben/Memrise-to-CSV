const tabMedia = new Map();

const MEDIA_RE = /\.(mp3|m4a|wav|ogg|mp4|m4v|webm)(?:[?#].*)?$/i;
const MEDIA_HINT_RE = /(?:audio|video|media|mp3|mp4|m4a|m4v|webm|\.m3u8)/i;

function contentTypeKind(headers = []) {
  const header = headers.find((item) => item.name?.toLowerCase() === "content-type");
  const value = header?.value || "";
  if (/audio/i.test(value)) return "audio";
  if (/video/i.test(value)) return "video";
  return "";
}

function urlKind(url) {
  if (/\.(mp3|m4a|wav|ogg)(?:[?#].*)?$/i.test(url) || /audio/i.test(url)) return "audio";
  if (/\.(mp4|m4v|webm)(?:[?#].*)?$/i.test(url) || /video/i.test(url)) return "video";
  return "";
}

function addMedia(tabId, media, source = "network", kind = "") {
  const url = typeof media === "string" ? media : media?.url;
  const itemKind = typeof media === "string" ? kind : media?.kind || kind;
  const itemSource = typeof media === "string" ? source : media?.source || source;
  if (!tabId || tabId < 0 || !url || !/^https?:/i.test(url)) return;
  const detectedKind = itemKind || urlKind(url);
  if (!detectedKind && !MEDIA_RE.test(url) && !MEDIA_HINT_RE.test(url)) return;

  const bucket = tabMedia.get(tabId) || [];
  const existing = bucket.find((item) => item.url === url);
  if (existing) {
    if (detectedKind && !existing.kind) existing.kind = detectedKind;
  } else {
    bucket.push({ url, kind: detectedKind, source: itemSource, seenAt: Date.now() });
    tabMedia.set(tabId, bucket);
  }
}

chrome.webRequest.onCompleted.addListener(
  (details) => {
    const kind = contentTypeKind(details.responseHeaders) || (details.type === "media" ? "media" : "");
    addMedia(details.tabId, details.url, "network", kind);
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

chrome.tabs.onRemoved.addListener((tabId) => {
  tabMedia.delete(tabId);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "recordMedia") {
    const tabId = sender.tab?.id ?? message.tabId;
    for (const item of message.urls || message.media || []) addMedia(tabId, item, message.source || "page");
    sendResponse({ ok: true });
    return true;
  }

  if (message?.type === "clearMedia") {
    const tabId = message.tabId ?? sender.tab?.id;
    if (tabId) tabMedia.delete(tabId);
    sendResponse({ ok: true });
    return true;
  }

  if (message?.type === "getMedia") {
    const tabId = message.tabId ?? sender.tab?.id;
    sendResponse({ media: tabMedia.get(tabId) || [] });
    return true;
  }

  if (message?.type === "downloadUrl") {
    chrome.downloads.download(
      {
        url: message.url,
        filename: message.filename,
        conflictAction: "uniquify",
        saveAs: false
      },
      (downloadId) => {
        sendResponse({
          ok: !chrome.runtime.lastError,
          downloadId,
          error: chrome.runtime.lastError?.message
        });
      }
    );
    return true;
  }

  return false;
});
