import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, newSessionId, setActiveChat } from "../api";
import { ColumnChart, HBarChart, LineChart, ScatterChart } from "../components/Charts";
import { Collapsible, DataTable } from "../components/Collapsible";
import Sidebar from "../components/Sidebar";

const eur = (n) => `${n < 0 ? "-" : ""}€${Math.abs(Math.round(n)).toLocaleString("en-US")}`;
const STATUS = ["todo", "in_progress", "blocked", "done"];

function Card({ title, children, wide }) {
  return <section className={`card ${wide ? "wide" : ""}`}><h3>{title}</h3>{children}</section>;
}

function Recommendation({ rec }) {
  const [state, setState] = useState(null); // null | "busy" | confirmation note
  const confirm = async () => {
    setState("busy");
    try {
      const r = await api("POST", "/actions/confirm", { tool: rec.tool, action: rec.action, detail: rec.text });
      setState(r.note);
    } catch (e) {
      setState(null);
      alertError(e);
    }
  };
  return (
    <li>
      <div>{rec.text}{state && state !== "busy" && <div className="muted small">✓ {state}</div>}</div>
      <button className="chip" disabled={state !== null} onClick={confirm}>{state ? "Recorded" : "Confirm"}</button>
    </li>
  );
}

// Surface a failed confirm without a blocking browser dialog.
function alertError(e) {
  window.dispatchEvent(new CustomEvent("nexora:error", { detail: e.message }));
}

export default function DashboardPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [menu, setMenu] = useState(false);
  const navigate = useNavigate();
  const openChat = (id) => { setActiveChat(id); navigate("/"); };

  useEffect(() => {
    api("GET", "/dashboard").then(setData).catch((e) => setError(e.message));
    const onError = (e) => setError(e.detail);
    window.addEventListener("nexora:error", onError);
    return () => window.removeEventListener("nexora:error", onError);
  }, []);

  const { finance, subscriptions: subs, tasks } = data || {};
  const unused = new Set((subs?.anomalies || []).filter((a) => a.type === "unused_subscription").map((a) => a.tool));

  return (
    <div className={`chat-shell ${menu ? "menu-open" : ""}`}>
      <Sidebar onNavigate={() => setMenu(false)} onNewChat={() => openChat(newSessionId())} onOpenChat={openChat} />
      <main className="chat-main">
        <header className="topbar">
          <button className="ghost burger" onClick={() => setMenu(!menu)} aria-label="Menu">☰</button>
          <span>Dashboard</span><span />
        </header>
        <div className="dash">
          {error && <div className="error">{error}</div>}
          {!data && !error && <div className="muted">Loading…</div>}

          {finance && (
            <div className="grid2">
              <Card title="Spending by category (12 months)">
                <HBarChart items={Object.entries(finance.spending_by_category)
                  .map(([label, value]) => ({ label, value })).sort((a, b) => b.value - a.value)} />
                <Collapsible title="Show data table">
                  <DataTable rows={Object.entries(finance.spending_by_category).sort((a, b) => b[1] - a[1])
                    .map(([category, eur]) => ({ category, EUR: eur }))} />
                </Collapsible>
              </Card>
              <Card title="Monthly net result">
                <LineChart points={finance.monthly.map((m) => ({ label: m.month, value: m.net }))} />
                <Collapsible title="Show data table"><DataTable rows={finance.monthly} /></Collapsible>
              </Card>
            </div>
          )}

          {subs && (
            <>
              <h2>Estimated savings</h2>
              <div className="kpis">
                <div><span>Per month</span><b>{eur(subs.analysis.monthly_savings)}</b></div>
                <div><span>Per year</span><b>{eur(subs.analysis.yearly_savings)}</b></div>
                <div><span>One-off refunds</span><b>{eur(subs.analysis.one_off_recovery)}</b></div>
              </div>
              <p className="muted small">Estimates from detected problems. Fixing them is always your decision.</p>

              <div className="grid2">
                <Card title="Subscription cost vs. usage">
                  <ScatterChart points={subs.table.map((r) => ({
                    label: r.tool, x: r.monthly_cost, y: Math.min(100, Math.round((r.active_users / r.seats) * 100)),
                    flagged: unused.has(r.tool), tip: `${r.active_users} of ${r.seats} seats active`,
                  }))} />
                  <div className="legend">
                    <i style={{ background: "#7c6cff" }} /> In use <i style={{ background: "#ff9f5a" }} /> No active users, still billed
                  </div>
                  <p className="muted small">Tools with other problems (duplicates, price rises, overlaps, unlisted charges) are in the table.</p>
                  <Collapsible title="Show data table">
                    <DataTable rows={subs.table.map((r) => ({
                      ...r, utilisation: Math.min(100, Math.round((r.active_users / r.seats) * 100)) }))} />
                  </Collapsible>
                </Card>
                <Card title="Flagged anomalies">
                  <DataTable columns={["type", "tool", "month", "detail", "monthly_saving", "one_off_recovery"]}
                    rows={subs.anomalies.map((x) => ({ ...x, type: x.type.replaceAll("_", " ") }))} />
                </Card>
              </div>

              <Card title="Recommendations" wide>
                <p className="muted small">Nothing is cancelled automatically. Confirming records your decision in the audit log only.</p>
                <ul className="recs">{subs.analysis.recommendations.map((r, i) => <Recommendation key={i} rec={r} />)}</ul>
              </Card>
            </>
          )}

          {tasks && (
            <Card title={`Task status (${tasks.scope === "all" ? "all projects" : "your tasks"}, ${tasks.total} tasks)`} wide>
              <ColumnChart items={STATUS.map((s) => ({ label: s.replace("_", " "), value: tasks.by_status[s] || 0 }))} />
            </Card>
          )}

          {data && !finance && <div className="note">Finance and subscription charts are available to the Owner and the Operations Manager.</div>}
        </div>
      </main>
    </div>
  );
}
