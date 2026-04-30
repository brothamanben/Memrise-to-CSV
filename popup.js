let state = {
  tabId: null,
  title: "memrise_lesson",
  rows: [],
  media: []
};

const $ = (id) => document.getElementById(id);

function setStatus(text) {
  $("status").textContent = text;
}

function sanitizeName(value, fallback = "file") {
  const cleaned = String(value || "")
    .normalize("NFKD")
    .replace(/[^\p{L}\p{N}._ -]+/gu, "")
    .trim()
    .replace(/\s+/g, "_")
    .slice(0, 80);
  return cleaned || fallback;
}

function mediaUrl(item) {
  return typeof item === "string" ? item : item?.url;
}

function mediaKind(item) {
  const url = mediaUrl(item) || "";
  const kind = typeof item === "string" ? "" : item?.kind || "";
  if (kind === "audio" || /\.(mp3|m4a|wav|ogg)(?:[?#].*)?$/i.test(url) || /audio/i.test(url)) return "audio";
  if (kind === "video" || /\.(mp4|m4v|webm)(?:[?#].*)?$/i.test(url) || /video/i.test(url)) return "video";
  return "";
}

function uniqueMedia(media) {
  const seen = new Set();
  return (media || []).filter((item) => {
    const url = mediaUrl(item);
    if (!url || seen.has(url)) return false;
    seen.add(url);
    return true;
  });
}

function classifyMedia(media) {
  const audio = [];
  const video = [];

  for (const item of uniqueMedia(media)) {
    const url = mediaUrl(item);
    if (mediaKind(item) === "audio") audio.push(url);
    if (mediaKind(item) === "video") video.push(url);
  }

  return { audio, video };
}

function extensionFromUrl(url, fallback) {
  try {
    const pathname = new URL(url).pathname;
    const match = pathname.match(/\.([a-z0-9]{2,5})$/i);
    return (match?.[1] || fallback).toLowerCase();
  } catch {
    return fallback;
  }
}

function filenameFor(url, rowIndex, kind, row, variant = "") {
  const ext = extensionFromUrl(url, kind === "audio" ? "mp3" : "mp4");
  const prefix = String(rowIndex + 1).padStart(2, "0");
  const target = sanitizeName(row?.target, `${kind}_${prefix}`);
  return `${prefix}_${target}${variant}.${ext}`;
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function fallbackMediaForRow(index) {
  const { audio, video } = classifyMedia(state.media);
  const media = [];
  if (audio[index]) media.push({ url: audio[index], kind: "audio" });
  if (video[index]) media.push({ url: video[index], kind: "video" });
  return media;
}

function rowMedia(row, index) {
  return uniqueMedia(row?.media?.length ? row.media : fallbackMediaForRow(index));
}

function soundTags(urls, rowIndex, kind, row) {
  return urls
    .map((url, mediaIndex) => {
      const variant = mediaIndex ? `_${kind}${mediaIndex + 1}` : "";
      return `[sound:${filenameFor(url, rowIndex, kind, row, variant)}]`;
    })
    .join(" ");
}

function makeCsv() {
  const useVideoSoundTag = $("videoSoundTag").checked;
  const lines = [["Target", "English", "Audio", "Video"].map(csvEscape).join(",")];

  state.rows.forEach((row, index) => {
    const { audio, video } = classifyMedia(rowMedia(row, index));
    lines.push(
      [
        row.target,
        row.english,
        soundTags(audio, index, "audio", row),
        useVideoSoundTag ? soundTags(video, index, "video", row) : video.map((url, videoIndex) => filenameFor(url, index, "video", row, videoIndex ? `_video${videoIndex + 1}` : "")).join(" ")
      ].map(csvEscape).join(",")
    );
  });

  return `${lines.join("\r\n")}\r\n`;
}

async function queueTextDownload(filename, text, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  await chrome.downloads.download({
    url,
    filename,
    conflictAction: "uniquify",
    saveAs: false
  });
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function allExportMedia() {
  const items = [];
  state.rows.forEach((row, rowIndex) => {
    for (const item of rowMedia(row, rowIndex)) {
      items.push({ ...item, row, rowIndex });
    }
  });
  return uniqueMedia(items);
}

function updateUi() {
  const { audio, video } = classifyMedia(allExportMedia().length ? allExportMedia() : state.media);
  $("rowCount").textContent = state.rows.length;
  $("audioCount").textContent = audio.length;
  $("videoCount").textContent = video.length;
  $("preview").value = makeCsv();
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab found.");
  return tab;
}

async function ensureContentScript(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "scanMemrisePage" });
  } catch {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"]
    });
  }
}

async function scanLesson({ reveal = false, reset = false } = {}) {
  const tab = await getActiveTab();
  state.tabId = tab.id;
  await ensureContentScript(tab.id);

  if (reset) {
    await chrome.runtime.sendMessage({ type: "clearMedia", tabId: tab.id });
  }

  setStatus(reveal ? "Opening each word and capturing media..." : "Detecting this lesson...");
  const page = await chrome.tabs.sendMessage(tab.id, {
    type: "detectCurrentTopic",
    activate: reveal,
    resetCapture: reset
  });
  const background = await chrome.runtime.sendMessage({ type: "getMedia", tabId: tab.id });

  state = {
    tabId: tab.id,
    title: page.title || "memrise_lesson",
    rows: page.rows || [],
    media: uniqueMedia([...(page.media || []), ...(background.media || [])])
  };

  updateUi();
  const { audio, video } = classifyMedia(allExportMedia().length ? allExportMedia() : state.media);
  setStatus(
    `${reveal ? `Clicked ${page.clicked || 0} controls. ` : ""}Found ${state.rows.length} words, ${audio.length} audio, ${video.length} video.`
  );
}

async function exportLesson() {
  if (!state.rows.length) await scanLesson();

  const folder = sanitizeName(state.title, "memrise_lesson");
  let queued = 0;

  for (const [rowIndex, row] of state.rows.entries()) {
    const { audio, video } = classifyMedia(rowMedia(row, rowIndex));

    for (const [mediaIndex, url] of audio.entries()) {
      const variant = mediaIndex ? `_audio${mediaIndex + 1}` : "";
      await chrome.runtime.sendMessage({
        type: "downloadUrl",
        url,
        filename: `${folder}/${filenameFor(url, rowIndex, "audio", row, variant)}`
      });
      queued += 1;
    }

    for (const [mediaIndex, url] of video.entries()) {
      const variant = mediaIndex ? `_video${mediaIndex + 1}` : "";
      await chrome.runtime.sendMessage({
        type: "downloadUrl",
        url,
        filename: `${folder}/${filenameFor(url, rowIndex, "video", row, variant)}`
      });
      queued += 1;
    }
  }

  await queueTextDownload(`${folder}/${folder}.csv`, makeCsv());
  setStatus(`Exported ${state.rows.length} words and queued ${queued} media downloads.`);
}

$("detectLesson").addEventListener("click", () => scanLesson({ reset: true }).catch((error) => setStatus(error.message)));
$("revealLesson").addEventListener("click", () => scanLesson({ reveal: true }).catch((error) => setStatus(error.message)));
$("exportLesson").addEventListener("click", () => exportLesson().catch((error) => setStatus(error.message)));

scanLesson().catch(() => updateUi());
