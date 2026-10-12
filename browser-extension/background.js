// Extensión opcional: un clic en el icono envía el audio de la pestaña actual a la app local.
// El audio nunca sale del equipo: solo viaja por WebSocket a 127.0.0.1.

const PORTS = [8765, 8766, 8767];
let control = null;
let appPort = null;
let capturingTab = null;

async function findApp() {
  for (const port of PORTS) {
    try {
      const ok = await new Promise((resolve) => {
        const ws = new WebSocket(`ws://127.0.0.1:${port}/ws/extension`);
        const timer = setTimeout(() => { try { ws.close(); } catch (e) {} resolve(false); }, 1500);
        ws.onopen = () => {
          clearTimeout(timer);
          ws.send(JSON.stringify({ type: "hello", version: chrome.runtime.getManifest().version,
                                   browser: navigator.userAgent.includes("Edg/") ? "edge" : "chrome" }));
          control = ws;
          appPort = port;
          ws.onclose = () => { control = null; appPort = null; setBadge(""); };
          resolve(true);
        };
        ws.onerror = () => { clearTimeout(timer); resolve(false); };
      });
      if (ok) return port;
    } catch (e) { /* siguiente puerto */ }
  }
  return null;
}

function setBadge(text, color = "#d93a2b") {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

async function ensureOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (contexts.length) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: ["USER_MEDIA"],
    justification: "Capturar el audio de la pestaña de la clase para transcribirla localmente.",
  });
}

chrome.action.onClicked.addListener(async (tab) => {
  if (capturingTab === tab.id) {
    chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
    capturingTab = null;
    setBadge("");
    return;
  }
  const port = appPort || (await findApp());
  if (!port) {
    setBadge("!", "#8e8e93");
    chrome.action.setTitle({ title: "Abre la app y empieza una grabación con «Una pestaña del navegador»." });
    return;
  }
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  await ensureOffscreen();
  chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId, port, label: tab.title || "pestaña" });
  capturingTab = tab.id;
  setBadge("REC");
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== "background") return;
  if (msg.type === "ended") { capturingTab = null; setBadge(""); }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === capturingTab) {
    chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
    capturingTab = null;
    setBadge("");
  }
});

chrome.alarms.create("buscar-app", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener(() => { if (!control) findApp(); });
chrome.runtime.onStartup.addListener(findApp);
chrome.runtime.onInstalled.addListener(findApp);
