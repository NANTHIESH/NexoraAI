import { useState } from "react";

/** Expander like Streamlit's st.expander. */
export function Collapsible({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="collapsible">
      <button className="chip" onClick={() => setOpen(!open)} aria-expanded={open}>{title} {open ? "▴" : "▾"}</button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

const label = (key) => key.replaceAll("_", " ");
const cell = (v) => (typeof v === "number" ? (Number.isInteger(v) ? v : v.toLocaleString("en-US", { maximumFractionDigits: 2 })) : v ?? "");

/** Plain data table from an array of objects (columns default to the keys of the first row). */
export function DataTable({ rows, columns }) {
  if (!rows?.length) return <div className="muted small">No rows.</div>;
  const cols = columns || Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{label(c)}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{cell(r[c])}</td>)}</tr>)}
        </tbody>
      </table>
    </div>
  );
}
