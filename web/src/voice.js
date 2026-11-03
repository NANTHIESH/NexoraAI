import { apiAudio, apiBlob } from "./api";

/** Record from the microphone; returns { stop } where stop() resolves to a 16-bit mono WAV blob. */
export async function startRecording() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw new Error(`The browser only allows the microphone on http://localhost or https pages. This page is ${location.origin}.`);
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    console.error("getUserMedia failed", e);
    throw new Error(await explainMicError(e));
  }
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.start();
  return {
    stop: () =>
      new Promise((resolve, reject) => {
        recorder.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          try {
            resolve(await toWav(new Blob(chunks, { type: recorder.mimeType })));
          } catch (e) {
            reject(e);
          }
        };
        recorder.stop();
      }),
    cancel: () => {
      recorder.onstop = null;
      if (recorder.state !== "inactive") recorder.stop();
      stream.getTracks().forEach((t) => t.stop());
    },
  };
}

// Browsers use different errors for different problems; say which one it is instead of always "blocked".
async function explainMicError(e) {
  let state = "unknown";
  try {
    state = (await navigator.permissions.query({ name: "microphone" })).state; // granted | denied | prompt
  } catch { /* not supported */ }
  switch (e?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return state === "denied"
        ? `Chrome is blocking the microphone for ${location.origin}. Click the icon left of the address bar, set Microphone to Allow, then reload the page.`
        : "The page is allowed to use the microphone, but the system refused. On Windows open Settings > Privacy & security > Microphone and turn on \"Let apps access your microphone\" and \"Let desktop apps access your microphone\", then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found. Plug one in or choose one in Windows Sound settings (Input), then try again.";
    case "NotReadableError":
    case "AbortError":
      return "The microphone could not be started. Another app (Teams, Zoom, a browser tab) may be using it, or Windows is blocking it. Close those and try again.";
    default:
      return `The microphone could not be used (${e?.name || "unknown error"}${e?.message ? `: ${e.message}` : ""}).`;
  }
}

// Browsers record WebM/Opus; the backend wants WAV/MP3/OGG, so decode and re-encode as WAV here.
async function toWav(blob) {
  const ctx = new AudioContext();
  try {
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    const data = audio.getChannelData(0);
    const out = new DataView(new ArrayBuffer(44 + data.length * 2));
    const str = (o, s) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
    str(0, "RIFF"); out.setUint32(4, 36 + data.length * 2, true); str(8, "WAVEfmt ");
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, audio.sampleRate, true); out.setUint32(28, audio.sampleRate * 2, true);
    out.setUint16(32, 2, true); out.setUint16(34, 16, true); str(36, "data"); out.setUint32(40, data.length * 2, true);
    data.forEach((v, i) => out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 0x7fff, true));
    return new Blob([out], { type: "audio/wav" });
  } finally {
    ctx.close();
  }
}

export const transcribe = async (wav) => (await apiAudio("/voice/stt", wav)).text;

let current = null;
/** Read text aloud through the backend voice; stops any audio that is already playing. */
export async function speak(text) {
  stopSpeaking();
  const url = URL.createObjectURL(await apiBlob("/voice/tts", { text: text.slice(0, 5000) }));
  const audio = new Audio(url);
  current = audio;
  audio.onended = () => { URL.revokeObjectURL(url); if (current === audio) current = null; };
  await audio.play();
  return audio;
}
export function stopSpeaking() {
  current?.pause();
  current = null;
}
