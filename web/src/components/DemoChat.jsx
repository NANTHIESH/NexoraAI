import { useEffect, useState } from "react";

const SCRIPT = [
  { from: "user", text: "How many vacation days do full-time employees get?" },
  { from: "bot", text: "Full-time employees get 25 vacation days per year. [HR Handbook, Leave]" },
  { from: "user", text: "Which subscriptions can we cancel?" },
  { from: "bot", text: "I found 6 issues. Estimated savings: €1,240/month. Nothing changes without your confirmation." },
];

// A looping, purely decorative conversation that sells the "chatbot" feel on the login screen.
export default function DemoChat() {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const delay = step >= SCRIPT.length ? 3500 : SCRIPT[step].from === "bot" ? 1600 : 1000;
    const t = setTimeout(() => setStep((s) => (s >= SCRIPT.length ? 0 : s + 1)), delay);
    return () => clearTimeout(t);
  }, [step]);

  const shown = SCRIPT.slice(0, step);
  const typing = step < SCRIPT.length && SCRIPT[step].from === "bot";

  return (
    <div className="demo-chat" aria-hidden="true">
      {shown.map((m, i) => (
        <div key={`${i}-${m.text}`} className={`bubble ${m.from} pop`}>{m.text}</div>
      ))}
      {typing && (
        <div className="bubble bot pop"><span className="dots"><i /><i /><i /></span></div>
      )}
    </div>
  );
}
