import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import Logo from "./Logo";

/**
 * Left column: new chat, saved conversations (the server keeps them), dashboard link, account.
 * `refreshKey` changes whenever the chat list should be reloaded (after an answer arrives).
 */
export default function Sidebar({ onNewChat, onOpenChat, onNavigate, activeId, refreshKey = 0 }) {
  const { user, logout } = useAuth();
  const [chats, setChats] = useState([]);
  const initials = user.name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api("GET", "/chats")
      .then((list) => { if (alive) { setChats(list); setError(""); } })
      .catch((e) => alive && setError(e.status === 404
        ? "Chat history needs the latest API. Restart it (make api)."
        : `Could not load chat history: ${e.message}`));
    return () => { alive = false; };
  }, [refreshKey]);

  return (
    <aside className="sidebar">
      <div className="brand"><Logo size={30} /> <span>Nexora AI</span></div>
      <button className="new-chat" onClick={onNewChat}>✎ New chat</button>
      <nav className="nav">
        <NavLink to="/dashboard" className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`} onClick={onNavigate}>
          📊 Dashboard
        </NavLink>
      </nav>

      <div className="recents-label">Recents</div>
      <div className="recents">
        {error && <div className="note warn recents-empty">{error}</div>}
        {!error && chats.length === 0 && <div className="muted small recents-empty">Your conversations are saved here.</div>}
        {chats.map((c) => (
          <button key={c.session_id} className={`chat-item ${c.session_id === activeId ? "active" : ""}`}
            title={c.title} onClick={() => onOpenChat(c.session_id)}>
            {c.title}
          </button>
        ))}
      </div>

      <div className="me">
        <span className="avatar">{initials}</span>
        <div><b>{user.name}</b><div className="muted small">{user.job_title} · {user.role}</div></div>
      </div>
      <button className="ghost wide" onClick={logout}>Log out</button>
    </aside>
  );
}
