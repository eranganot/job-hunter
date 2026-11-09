import { useEffect, useState } from "react";
import { MailCheck, X } from "lucide-react";
import { api } from "./api/client";

/* Step 5: tell an unconfirmed account to open the link we mailed, and confirm
   it once they have (/verify-email lands here with ?verified=1). Reads /api/me
   itself so it stays out of the views - one banner, whichever screen is up. */
export function VerifyEmailBanner() {
  const [state, setState] = useState<"hidden" | "pending" | "done">("hidden");
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("verified") === "1") {
      setState("done");
      url.searchParams.delete("verified");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
      const t = setTimeout(() => setState("hidden"), 6000);
      return () => clearTimeout(t);
    }
    const check = () => api.me().then((me) => {
      setEmail(me.email || "");
      setState(me.email_verified === false ? "pending" : "hidden");
    }).catch(() => { /* signed out: nothing to say */ });
    check();
    const onVis = () => { if (document.visibilityState === "visible") check(); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  if (state === "hidden") return null;

  const resend = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await api.resendVerification();
      setMsg(`Sent again to ${r.sent_to}. Check spam too.`);
    } catch (e: any) {
      setMsg(e?.status === 429 ? "Sent several times already - try again in an hour." : "Couldn't send it right now. Try again in a minute.");
    } finally { setBusy(false); }
  };

  const done = state === "done";
  return (
    <div className="fixed bottom-4 left-4 right-4 sm:left-auto sm:max-w-sm z-50 rounded-2xl border shadow-xl p-4 text-sm bg-gray-800 border-gray-600 text-gray-200"
      role="status">
      <div className="flex items-start gap-3">
        <MailCheck className={`w-5 h-5 shrink-0 mt-0.5 ${done ? "text-emerald-400" : "text-amber-400"}`} />
        <div className="flex-1 min-w-0">
          {done ? (
            <p className="font-medium text-white">Email confirmed. Thanks!</p>
          ) : (<>
            <p className="font-medium text-white">Confirm your email</p>
            <p className="text-gray-400 mt-0.5">We sent a link to <span className="text-gray-200 break-all">{email}</span>. Applications go out under this address, so auto-apply starts once it's confirmed.</p>
            <button onClick={resend} disabled={busy} className="mt-2 text-indigo-300 hover:text-indigo-200 font-medium disabled:opacity-50">{busy ? "Sending…" : "Send it again"}</button>
            {msg && <p className="text-gray-400 mt-1">{msg}</p>}
          </>)}
        </div>
        <button onClick={() => setState("hidden")} aria-label="Close" className="text-gray-400 hover:text-gray-200"><X className="w-4 h-4" /></button>
      </div>
    </div>
  );
}
