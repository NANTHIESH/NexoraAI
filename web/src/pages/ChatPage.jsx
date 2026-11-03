import { useEffect, useRef, useState } from "react";
import { api, getActiveChat, newSessionId, setActiveChat } from "../api";
import { useAuth } from "../auth";
import Logo from "../components/Logo";
import Message from "../components/Message";
import Sidebar from "../components/Sidebar";
import { startRecording, transcribe } from "../voice";

const DOCS = ["How many vacation days do full-time employees get?", "Can we deploy to production on a Friday afternoon?"];
const FINANCE = ["Give me the monthly report", "Which subscriptions can we cancel?", "How much did we spend on GitHub last month?"];
const TASKS = ["How many tasks are blocked?", "Which tasks are overdue?"];
const suggestionsFor = (role) => (["owner", "ops_manager"].includes(role) ? [DOCS[0], ...FINANCE] : [...DOCS, ...TASKS]);

export default function ChatPage() {
  const { user } = useAuth();
  const [messages, setMessages] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState(() => getActiveChat() || newSessionId());
  const sessionRef = useRef(session);
  const [refreshKey, setRefreshKey] = useState(0); // tells the sidebar to reload the saved-chat list
  const [loading, setLoading] = useState(true);
  const [health, setHealth] = useState(null);
  const [menu, setMenu] = useState(false);
  const bottom = useRef(null);
  const recorder = useRef(null);
  const [mic, setMic] = useState("idle"); // idle | recording | transcribing
  const [micError, setMicError] = useState("");

  // Show a saved conversation: on first load (the one open before a reload) and when one is picked in the sidebar.
  async function openChat(id) {
    sessionRef.current = id;
    setActiveChat(id);
    setSession(id);
    setLoading(true);
    setMenu(false);
    try {
      const saved = await api("GET", `/chats/${id}`);
      if (sessionRef.current === id) setMessages(saved);
    } catch {
      if (sessionRef.current === id) setMessages([]);
    } finally {
      if (sessionRef.current === id) setLoading(false);
    }
  }
  useEffect(() => { openChat(session); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { api("GET", "/health").then(setHealth).catch(() => {}); }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function ask(question) {
    const q = question.trim();
    if (!q || busy) return;
    setText("");
    setMessages((m) => [...m, { role: "user", text: q }]);
    setBusy(true);
    const sid = session;
    try {
      const answer = await api("POST", "/chat", { question: q, session_id: sid });
      if (sessionRef.current === sid) setMessages((m) => [...m, { role: "assistant", animate: true, ...answer }]);
    } catch (err) {
      if (sessionRef.current === sid) setMessages((m) => [...m, { role: "assistant", text: err.message, denied: true, error: true }]);
    } finally {
      setBusy(false);
      setRefreshKey((k) => k + 1);
    }
  }

  async function toggleMic() {
    setMicError("");
    if (mic === "recording") {
      setMic("transcribing");
      try {
        const spoken = await transcribe(await recorder.current.stop());
        if (spoken) await ask(spoken);
        else setMicError("I could not hear anything. Please try again.");
      } catch (e) {
        setMicError(`Voice input is not available: ${e.message}`);
      } finally {
        recorder.current = null;
        setMic("idle");
      }
      return;
    }
    if (mic !== "idle") return;
    try {
      recorder.current = await startRecording();
      setMic("recording");
    } catch (e) {
      setMicError(e.message);
    }
  }
  useEffect(() => () => recorder.current?.cancel(), []);

  const reset = () => {
    const id = newSessionId();
    sessionRef.current = id;
    setActiveChat(id);
    setSession(id);
    setMessages([]);
    setLoading(false);
    setMenu(false);
  };
  const onKey = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(text); } };

  return (
    <div className={`chat-shell ${menu ? "menu-open" : ""}`}>
      <Sidebar activeId={session} refreshKey={refreshKey} onNewChat={reset} onOpenChat={openChat} onNavigate={() => setMenu(false)} />

      <main className="chat-main">
        <header className="topbar">
          <button className="ghost burger" onClick={() => setMenu(!menu)} aria-label="Menu">☰</button>
          <span>Nexora AI</span>
          <span className={`status ${health?.llm_ready ? "ok" : ""}`}>{health ? (health.llm_ready ? "● AI online" : "● Extractive mode") : ""}</span>
        </header>

        {health && !health.llm_ready && (
          <div className="banner">
            <b>Extractive mode.</b> {health.notice} Install and start Ollama from ollama.com, then run <code>make setup</code>.
          </div>
        )}

        <div className="stream">
          {!loading && messages.length === 0 && (
            <div className="welcome">
              <Logo size={64} />
              <h2>Hi {user.name.split(" ")[0]}, what can I help with?</h2>
              <p className="muted">Ask about company rules, reports or software subscriptions.</p>
              <div className="suggestions">
                {suggestionsFor(user.role).map((s) => <button key={s} onClick={() => ask(s)}>{s}</button>)}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <Message key={i} msg={m} isLast={i === messages.length - 1 && !busy} canAsk={!busy} onAsk={ask} />
          ))}
          {busy && (
            <div className="row bot"><Logo size={30} /><div className="bubble bot"><span className="dots"><i /><i /><i /></span></div></div>
          )}
          <div ref={bottom} />
        </div>

        <div className="composer">
          <div className="input-pill">
            <textarea rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
              placeholder="Message Nexora AI" maxLength={2000} />
            <button className={`mic ${mic}`} onClick={toggleMic} disabled={busy || mic === "transcribing"}
              aria-label={mic === "recording" ? "Stop recording" : "Speak your question"}
              title={mic === "recording" ? "Stop and send" : "Ask by voice"}>
              {mic === "transcribing" ? "…" : mic === "recording" ? "⏹" : "🎤"}
            </button>
            <button className="send" disabled={!text.trim() || busy} onClick={() => ask(text)} aria-label="Send">➤</button>
          </div>
          {mic === "recording" && <div className="note center rec">● Recording… tap ⏹ to send</div>}
          {micError && <div className="note warn center">{micError}</div>}
          <div className="muted small center">Answers can be wrong. Check the sources. Demo data is synthetic.</div>
        </div>
      </main>
    </div>
  );
}
