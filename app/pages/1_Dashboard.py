"""Nexora AI - dashboard: spending, net trend, subscription cost vs usage, anomalies, tasks, savings.

Sections appear only if the logged-in role may see them (the API decides).
Colors: one validated blue for data, orange to mark items needing attention, grays for text/grid.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui_common import ApiError, api, require_login

BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
STATUS_ORDER = ["todo", "in_progress", "blocked", "done"]

st.set_page_config(page_title="Nexora AI - Dashboard", layout="wide")
user = require_login()
st.title("Dashboard")


def style(fig: go.Figure, height: int = 360) -> go.Figure:
    """Quiet chart chrome: thin grid, muted axes, no frame."""
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color=INK, size=13), showlegend=False,
                      hoverlabel=dict(font_size=13))
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, tickfont=dict(color=MUTED), zeroline=False)
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, tickfont=dict(color=MUTED), zeroline=False)
    return fig


try:
    data = api("GET", "/dashboard").json()
except ApiError as exc:
    st.error(exc.detail)
    st.stop()

finance, subs, tasks = data["finance"], data["subscriptions"], data["tasks"]

if finance:
    left, right = st.columns(2)
    with left:
        st.subheader("Spending by category (12 months)")
        spend = pd.Series(finance["spending_by_category"]).sort_values()
        fig = go.Figure(go.Bar(x=spend.values, y=spend.index, orientation="h", marker_color=BLUE,
                               text=[f"{v:,.0f}" for v in spend.values], textposition="outside",
                               cliponaxis=False, hovertemplate="%{y}: %{x:,.0f} EUR<extra></extra>"))
        fig.update_layout(bargap=0.45)
        fig.update_xaxes(title_text="EUR", showgrid=False)
        st.plotly_chart(style(fig, 420), width="stretch")
        with st.expander("Show data table"):
            st.dataframe(spend.sort_values(ascending=False).rename("EUR"))
    with right:
        st.subheader("Monthly net result")
        monthly = pd.DataFrame(finance["monthly"])
        fig = go.Figure(go.Scatter(x=monthly["month"], y=monthly["net"], mode="lines+markers",
                                   line=dict(color=BLUE, width=2), marker=dict(size=8, color=BLUE,
                                   line=dict(color="#fcfcfb", width=2)),
                                   hovertemplate="%{x}: %{y:,.0f} EUR<extra></extra>"))
        fig.add_hline(y=0, line_color=MUTED, line_width=1)
        fig.update_yaxes(title_text="EUR (income minus expenses)")
        st.plotly_chart(style(fig, 420), width="stretch")
        with st.expander("Show data table"):
            st.dataframe(monthly.set_index("month"))

if subs:
    st.subheader("Estimated savings")
    analysis = subs["analysis"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Per month", f"{analysis['monthly_savings']:,.0f} EUR")
    c2.metric("Per year", f"{analysis['yearly_savings']:,.0f} EUR")
    c3.metric("One-off refunds", f"{analysis['one_off_recovery']:,.0f} EUR")
    st.caption("Estimates from detected problems. Fixing them is always your decision.")

    st.subheader("Subscription cost vs. usage")
    unused_tools = {a["tool"] for a in subs["anomalies"] if a["type"] == "unused_subscription"}
    table = pd.DataFrame(subs["table"])
    table["utilisation"] = (table["active_users"] / table["seats"] * 100).clip(upper=100).round(0)
    table["no_users"] = table["tool"].isin(unused_tools)
    fig = go.Figure()
    for no_users, color, name in ((False, BLUE, "In use"), (True, ORANGE, "No active users, still billed")):
        part = table[table["no_users"] == no_users].sort_values("monthly_cost")
        slots = ["top center", "bottom center", "top center", "middle right"]  # stagger labels of close points
        positions = [slots[i % len(slots)] for i in range(len(part))]
        fig.add_trace(go.Scatter(
            x=part["monthly_cost"], y=part["utilisation"], mode="markers+text" if no_users else "markers",
            name=name, text=part["tool"] if no_users else None, textposition=positions,
            textfont=dict(color=INK),
            marker=dict(size=12, color=color, line=dict(color="#fcfcfb", width=2)),
            customdata=part[["tool", "active_users", "seats"]],
            hovertemplate="%{customdata[0]}<br>%{x:,.0f} EUR/month<br>%{customdata[1]} of %{customdata[2]} seats active"
                          "<extra></extra>"))
    fig.update_layout(legend=dict(orientation="h", y=1.1))
    fig.update_xaxes(title_text="Monthly cost (EUR)")
    fig.update_yaxes(title_text="Seats in use (%)", range=[-12, 112])
    st.plotly_chart(style(fig, 440).update_layout(showlegend=True), width="stretch")
    st.caption("Tools with other problems (duplicates, price rises, overlaps, unlisted charges) are in the table below.")
    with st.expander("Show data table"):
        st.dataframe(table.drop(columns=["no_users"]), hide_index=True)

    st.subheader("Flagged anomalies")
    anomalies = pd.DataFrame(subs["anomalies"])[["type", "tool", "month", "detail", "monthly_saving", "one_off_recovery"]]
    st.dataframe(anomalies, hide_index=True, width="stretch")

    st.subheader("Recommendations")
    st.caption("Nothing is cancelled automatically. Confirming records your decision in the audit log only.")
    for n, rec in enumerate(analysis["recommendations"]):
        text_col, button_col = st.columns([5, 1])
        text_col.write(rec["text"])
        if button_col.button("Confirm", key=f"confirm{n}", help=f"Record that you approve: {rec['action']}"):
            try:
                st.success(api("POST", "/actions/confirm",
                               json={"tool": rec["tool"], "action": rec["action"], "detail": rec["text"]}).json()["note"])
            except ApiError as exc:
                st.error(exc.detail)

if tasks:
    scope = "all projects" if tasks["scope"] == "all" else "your tasks"
    st.subheader(f"Task status ({scope}, {tasks['total']} tasks)")
    counts = [tasks["by_status"].get(s, 0) for s in STATUS_ORDER]
    fig = go.Figure(go.Bar(x=[s.replace("_", " ") for s in STATUS_ORDER], y=counts, marker_color=BLUE,
                           text=counts, textposition="outside", cliponaxis=False,
                           hovertemplate="%{x}: %{y} tasks<extra></extra>"))
    fig.update_layout(bargap=0.55)
    fig.update_yaxes(title_text="Tasks")
    st.plotly_chart(style(fig, 320), width="stretch")

if not finance:
    st.info("Finance and subscription charts are available to the Owner and the Operations Manager.")
