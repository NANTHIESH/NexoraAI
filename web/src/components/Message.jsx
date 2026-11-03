import { useState } from "react";
import { api } from "../api";
import { speak, stopSpeaking } from "../voice";
import { Collapsible, DataTable } from "./Collapsible";
import Logo from "./Logo";
import Typed from "./Typed";

const eur = (n) => `${n < 0 ? "-" : ""}€${Math.abs(Math.round(n)).toLocaleString("en-US")}`;

function Sources({ sources }) {
  const [open, setOpen] = useState(false);
  if (!sources?.length) return null;
  return (
    <div className="sources">
      <button className="chip" onClick={() => setOpen(!open)}>📚 Sources ({sources.length}) {open ? "▴" : "▾"}</button>
      {open && sources.map((s, i) => (
        <div className="source" key={i}>
          <strong>[{s.title}, {s.section}]</strong>
          {s.status !== "current" && <span className="tag warn">superseded</span>}
          <div className="muted small">version {s.version} · match {s.score.toFixed(2)} · {s.source}</div>
          <p>{s.text}</p>
        </div>
      ))}
    </div>
  );
}

function Feedback({ chatId, text }) {
  const [rated, setRated] = useState(null);
  const [voice, setVoice] = useState({ state: "idle", error: "" }); // idle | loading | playing
  if (chatId == null || chatId < 0) return null;
  const toggleSpeak = async () => {
    if (voice.state !== "idle") {
      stopSpeaking();
      return setVoice({ state: "idle", error: "" });
    }
    setVoice({ state: "loading", error: "" });
    try {
      const audio = await speak(text);
      setVoice({ state: "playing", error: "" });
      audio.addEventListener("ended", () => setVoice({ state: "idle", error: "" }), { once: true });
    } catch (e) {
      setVoice({ state: "idle", error: `Read aloud is not available: ${e.message}` });
    }
  };
  const send = async (rating) => {
    setRated(rating);
    try { await api("POST", "/feedback", { chat_id: chatId, rating }); } catch { setRated(null); }
  };
  return (
    <div className="feedback">
      <button disabled={rated !== null} className={rated === 1 ? "on" : ""} onClick={() => send(1)} title="Helpful">👍</button>
      <button disabled={rated !== null} className={rated === -1 ? "on" : ""} onClick={() => send(-1)} title="Not helpful">👎</button>
      <button className={voice.state === "playing" ? "on" : ""} onClick={toggleSpeak} title={voice.state === "idle" ? "Read aloud" : "Stop"}>
        {voice.state === "loading" ? "…" : voice.state === "playing" ? "⏹" : "🔊"}
      </button>
      {voice.error && <span className="note warn">{voice.error}</span>}
    </div>
  );
}

export default function Message({ msg, isLast = false, canAsk = false, onAsk }) {
  const [typing, setTyping] = useState(Boolean(msg.animate));
  if (msg.role === "user") return <div className="row user"><div className="bubble user">{msg.text}</div></div>;
  if (msg.denied) {
    return <div className="row bot"><Logo size={30} /><div className="bot-body"><div className="bubble bot denied">{msg.text}</div></div></div>;
  }
  const r = msg.report;
  const recs = msg.analysis?.recommendations;
  return (
    <div className="row bot">
      <Logo size={30} />
      <div className="bot-body">
        <div className={`bubble bot pre ${msg.refused ? "refused" : ""}`}>
          <Typed text={msg.text} animate={msg.animate} onDone={() => setTyping(false)} />
        </div>
        {!typing && (
          <>
            {msg.notice && <div className="note">{msg.notice}</div>}
            {msg.warning && <div className="note warn">{msg.warning}</div>}
            {msg.injection_flagged?.length > 0 && (
              <div className="note warn">Instruction-like text was found and removed from: {msg.injection_flagged.join(", ")}</div>
            )}
            {r && "income" in r && (
              <div className="metrics">
                <div><span>Income</span><b>{eur(r.income)}</b></div>
                <div><span>Expenses</span><b>{eur(r.expenses)}</b></div>
                <div><span>Net</span><b>{eur(r.net)}</b></div>
              </div>
            )}
            {r?.blocked_tasks?.length > 0 && (
              <Collapsible title={`Blocked tasks (${r.blocked_tasks.length} shown)`}><DataTable rows={r.blocked_tasks} /></Collapsible>
            )}
            {recs?.length > 0 && (
              <Collapsible title={`All ${recs.length} recommendations (confirm actions on the Dashboard)`}>
                <DataTable rows={recs.map((x) => ({ Recommendation: x.text }))} />
              </Collapsible>
            )}
            <Sources sources={msg.sources} />
            {msg.rewritten_question && <div className="muted small">Understood as: {msg.rewritten_question}</div>}
            {!msg.error && <Feedback chatId={msg.chat_id} text={msg.text} />}
            {!msg.error && msg.intent && <div className="muted small">{msg.intent}{msg.method ? ` (${msg.method})` : ""}</div>}
            {isLast && msg.suggestions?.length > 0 && (
              <div className="chips" aria-label="Suggested questions">
                {msg.suggestions.map((q) => (
                  <button key={q} className="suggest" disabled={!canAsk} onClick={() => onAsk(q)}>{q}</button>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
