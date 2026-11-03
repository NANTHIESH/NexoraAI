// Thin client for the Nexora FastAPI backend (proxied at /api in dev).
const BASE = import.meta.env.VITE_API_URL || "/api";
const KEY = "nexora.session";

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export const loadSession = () => {
  try {
    return JSON.parse(sessionStorage.getItem(KEY)) || null;
  } catch {
    return null;
  }
};
export const saveSession = (s) => sessionStorage.setItem(KEY, JSON.stringify(s));
export const clearSession = () => sessionStorage.removeItem(KEY);

async function request(method, path, { json, body, contentType } = {}) {
  const headers = {};
  if (json !== undefined) headers["Content-Type"] = "application/json";
  if (contentType) headers["Content-Type"] = contentType;
  const session = loadSession();
  if (session?.token) headers.Authorization = `Bearer ${session.token}`;
  let res;
  try {
    res = await fetch(BASE + path, { method, headers, body: json !== undefined ? JSON.stringify(json) : body });
  } catch {
    throw new ApiError(0, "Cannot reach the Nexora API. Is it running on port 8000?");
  }
  if (res.status === 401 && session && path !== "/login") {
    clearSession();
    window.dispatchEvent(new Event("nexora:logout"));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : data.detail?.[0]?.msg || detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res;
}

export const api = async (method, path, json) => (await request(method, path, { json })).json();
/** POST JSON, get binary back (text-to-speech WAV). */
export const apiBlob = async (path, json) => (await request("POST", path, { json })).blob();
/** POST raw audio bytes, get JSON back (speech-to-text). */
export const apiAudio = async (path, blob) =>
  (await request("POST", path, { body: blob, contentType: "audio/wav" })).json();
