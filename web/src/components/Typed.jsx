import { useEffect, useState } from "react";

const WORD_DELAY_MS = 20;
const MAX_TYPING_MS = 2500;

/** Reveal text word by word like a streaming chat (total time is capped, same as the Streamlit app). */
export default function Typed({ text, animate, onDone }) {
  const words = text.split(" ");
  const [shown, setShown] = useState(animate ? 0 : words.length);

  useEffect(() => {
    if (!animate) return undefined;
    const delay = Math.min(WORD_DELAY_MS, MAX_TYPING_MS / Math.max(words.length, 1));
    const id = setInterval(() => {
      setShown((n) => {
        if (n + 1 >= words.length) { clearInterval(id); onDone?.(); }
        return n + 1;
      });
    }, delay);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <>{words.slice(0, shown).join(" ")}</>;
}
