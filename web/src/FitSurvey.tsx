import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "./api/client";

/* The product-fit question (fitsurvey.py, GTM row 21): on day 14 and day 30, an
   activated user is asked once per round. Reads /api/me itself, like the email
   banner, so it shows over whichever screen is up. "Not now" is an answer too
   (stored as skipped), so the card does not come back that round. */
const ANSWERS: [string, string][] = [
  ["very", "I'd really miss it"], ["somewhat", "I'd miss it a bit"],
  ["not", "I wouldn't miss it"], ["no_longer", "I've stopped using it"],
];

export function FitSurvey() {
  const [round, setRound] = useState<string | null>(null);
  const [picked, setPicked] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    api.me().then((me) => setRound(me.fit_survey_due || null)).catch(() => { /* signed out */ });
  }, []);

  if (!round) return null;

  const send = async (answer: string) => {
    setBusy(true);
    try {
      await api.answerFitSurvey(round, answer, answer === "skipped" ? "" : comment);
    } catch { /* never trap the user behind a survey */ }
    finally {
      setBusy(false);
      if (answer === "skipped") setRound(null);
      else { setDone(true); setTimeout(() => setRound(null), 2500); }
    }
  };

  return (
    <div className="fixed bottom-4 left-4 right-4 sm:left-auto sm:max-w-sm z-[55] rounded-2xl border shadow-xl p-4 text-sm bg-gray-800 border-gray-600 text-gray-200"
      role="dialog" aria-label="One question from Eran">
      {done ? <p className="text-white">Thank you. It comes straight to me. Eran</p> : (
        <>
          <div className="flex items-start justify-between gap-3">
            <p className="font-medium text-white">If Job Hunter disappeared tomorrow, how would you feel?</p>
            <button onClick={() => send("skipped")} disabled={busy} aria-label="Not now" className="text-gray-400 hover:text-gray-200 shrink-0"><X className="w-4 h-4" /></button>
          </div>
          <div className="grid gap-1.5 mt-3">
            {ANSWERS.map(([key, label]) => (
              <button key={key} onClick={() => setPicked(key)} disabled={busy}
                className={`text-left px-3 py-2 rounded-xl border text-sm ${picked === key ? "bg-indigo-600/25 border-indigo-500 text-white" : "bg-gray-900/50 border-gray-700 text-gray-200 hover:border-gray-500"}`}>
                {label}
              </button>
            ))}
          </div>
          {picked && (
            <>
              <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} maxLength={2000}
                placeholder="Anything to add? What Noa does best, or should do better (optional)"
                className="w-full mt-2.5 px-3 py-2 rounded-xl bg-gray-900 border border-gray-700 text-white text-sm placeholder:text-gray-400" />
              <button onClick={() => send(picked)} disabled={busy}
                className="w-full mt-2 py-2.5 bg-brand-yellow hover:bg-brand-yellow-hover active:bg-brand-yellow-press text-ink rounded-xl font-medium disabled:opacity-60">Send</button>
            </>
          )}
          {!picked && <button onClick={() => send("skipped")} disabled={busy} className="mt-2 text-xs text-gray-400 hover:text-gray-200">Not now</button>}
        </>
      )}
    </div>
  );
}
