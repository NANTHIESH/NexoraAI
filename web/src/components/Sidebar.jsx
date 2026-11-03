import { NavLink } from "react-router-dom";
import { useAuth } from "../auth";
import Logo from "./Logo";

export default function Sidebar({ onNewChat, onNavigate }) {
  const { user, logout } = useAuth();
  const initials = user.name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const link = ({ isActive }) => `nav-link ${isActive ? "active" : ""}`;
  return (
    <aside className="sidebar">
      <div className="brand"><Logo /> <span>Nexora AI</span></div>
      <nav className="nav">
        <NavLink to="/" end className={link} onClick={onNavigate}>💬 Chat</NavLink>
        <NavLink to="/dashboard" className={link} onClick={onNavigate}>📊 Dashboard</NavLink>
      </nav>
      {onNewChat && <button className="new-chat" onClick={onNewChat}>＋ New chat</button>}
      <div className="grow" />
      <div className="me">
        <span className="avatar">{initials}</span>
        <div><b>{user.name}</b><div className="muted small">{user.job_title} · {user.role}</div></div>
      </div>
      <button className="ghost wide" onClick={logout}>Log out</button>
    </aside>
  );
}
