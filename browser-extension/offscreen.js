// Documento oculto: recibe el audio de la pestaña, lo sigue reproduciendo (para que se oiga)
// y lo envía a la app en bloques de 100 ms a 16 kHz.

let ctx = null;
let stream = null;
let ws = null;

chrome.runtime.onMessage.addListener(async (msg) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "start") await start(msg);
  if (msg.type === "stop") stop(true);
});

async function start({ streamId, port, label }) {
  stop(false);
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });
  ctx = new AudioContext({ sampleRate: 16000 });
  const source = ctx.createMediaStreamSource(stream);
  source.connect(ctx.destination); // tabCapture silencia la pestaña: se vuelve a reproducir
  await ctx.audioWorklet.addModule("worklet.js");
  const node = new AudioWorkletNode(ctx, "pcm-sender");
  source.connect(node);

  ws = new WebSocket(`ws://127.0.0.1:${port}/ws/audio`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify({ type: "start", label }));
  ws.onclose = () => stop(false);
  node.port.onmessage = (ev) => { if (ws && ws.readyState === 1) ws.send(ev.data.buffer); };
  stream.getAudioTracks()[0].onended = () => stop(true);
}

function stop(notify) {
  if (ws && ws.readyState === 1) {
    try { ws.send(JSON.stringify({ type: "stop" })); } catch (e) {}
    ws.close();
  }
  if (stream) stream.getTracks().forEach((t) => t.stop());
  if (ctx) ctx.close();
  ws = stream = ctx = null;
  if (notify) chrome.runtime.sendMessage({ target: "background", type: "ended" });
}
