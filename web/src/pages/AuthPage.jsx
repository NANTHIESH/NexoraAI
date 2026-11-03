import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import DemoChat from "../components/DemoChat";
import Logo from "../components/Logo";
import { useAuth } from "../auth";

export default function AuthPage({ mode }) {
  const isLogin = mode === "login";
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", identifier: "", password: "", confirm: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (isLogin) {
      if (!form.identifier.trim() || !form.password) return setError("Enter your email (or employee ID) and password.");
    } else {
      if (!form.name.trim() || !form.email.trim() || !form.password) return setError("Please fill in all fields.");
      if (form.password.length < 8) return setError("The password must have at least 8 characters.");
      if (form.password !== form.confirm) return setError("The passwords do not match.");
    }
    setBusy(true);
    try {
      if (isLogin) await login(form.identifier.trim(), form.password);
      else await register(form.name, form.email, form.password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <section className="auth-hero">
        <div className="orb orb-a" /><div className="orb orb-b" />
        <div className="brand"><Logo /> <span>Nexora AI</span></div>
        <h1>Your company's<br />assistant, <em>on call.</em></h1>
        <p>Ask about company rules, get finance reports, and spot wasted software spend, in plain language, with sources.</p>
        <DemoChat />
      </section>

      <section className="auth-panel">
        <form className="auth-card" onSubmit={submit} noValidate>
          <div className="auth-icon"><Logo size={48} /></div>
          <h2>{isLogin ? "Welcome back" : "Create your account"}</h2>
          <p className="muted">{isLogin ? "Log in to start chatting with Nexora." : "Join to chat with Nexora AI."}</p>

          {!isLogin && (
            <label>Full name
              <input value={form.name} onChange={set("name")} autoComplete="name" placeholder="Ada Lovelace" autoFocus />
            </label>
          )}
          {isLogin ? (
            <label>Email or employee ID
              <input value={form.identifier} onChange={set("identifier")} autoComplete="username" placeholder="you@company.com or E001" autoFocus />
            </label>
          ) : (
            <label>Email
              <input type="email" value={form.email} onChange={set("email")} autoComplete="email" placeholder="you@company.com" />
            </label>
          )}
          <label>Password
            <span className="pw">
              <input type={showPw ? "text" : "password"} value={form.password} onChange={set("password")}
                autoComplete={isLogin ? "current-password" : "new-password"} placeholder={isLogin ? "Your password" : "At least 8 characters"} />
              <button type="button" className="ghost" onClick={() => setShowPw((s) => !s)}>{showPw ? "Hide" : "Show"}</button>
            </span>
          </label>
          {!isLogin && (
            <label>Repeat password
              <input type={showPw ? "text" : "password"} value={form.confirm} onChange={set("confirm")} autoComplete="new-password" />
            </label>
          )}

          {error && <div className="error" role="alert">{error}</div>}
          <button className="primary" disabled={busy}>
            {busy ? <span className="dots"><i /><i /><i /></span> : isLogin ? "Log in" : "Create account"}
          </button>

          <p className="switch">
            {isLogin ? <>New here? <Link to="/register">Create an account</Link></> : <>Already have an account? <Link to="/login">Log in</Link></>}
          </p>
          {!isLogin && <p className="hint">New accounts get the Employee role: company documents and your own tasks.</p>}
        </form>
      </section>
    </div>
  );
}
