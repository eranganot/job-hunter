import { useEffect, useRef, useState } from "react";
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

  /* 2026-11-24: the banner is fixed to the bottom of the screen. On a phone it
     sat on top of the swipe card's Pass / Approve buttons - the activation
     action - for every new, unconfirmed user (E2E, 390x844 / 375x667 / 430x932).
     While it shows, a spacer the banner's height sits after the screen's content
     (this component is mounted right after the app in #root), so the last button
     of any screen can scroll clear of it. Body padding does not work here:
     html/body/#root are height:100%, so padding shrank the box instead of adding
     scroll room (measured: 196px applied, document height unchanged). */
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = boxRef.current;
    if (state === "hidden" || !el) { setRoom(0); return; }
    const apply = () => setRoom(Math.ceil(el.getBoundingClientRect().height) + 32);
    apply();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(apply) : null;
    ro?.observe(el);
    return () => ro?.disconnect();
  }, [state]);

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
  return (<>
    <div aria-hidden="true" style={{ height: room }} />
    <div ref={boxRef} className="fixed bottom-4 left-4 right-4 sm:left-auto sm:max-w-sm z-40 rounded-2xl border shadow-xl p-4 text-sm bg-gray-800 border-gray-600 text-gray-200"
      role="status">
      <div className="flex items-start gap-3">
        <MailCheck className={`w-5 h-5 shrink-0 mt-0.5 ${done ? "text-emerald-400" : "text-amber-400"}`} />
        <div className="flex-1 min-w-0">
          {done ? (
            <p className="font-medium text-white">Email confirmed. Thanks!</p>
          ) : (<>
            <p className="font-medium text-white">Confirm your email</p>
            <p className="text-gray-400 mt-0.5">We sent a link to <span className="text-gray-200 break-all">{email}</span>. Tap the link in it: it shows the address is really yours, so Noa's picks reach your inbox and only you can reset your password.</p>
            <button onClick={resend} disabled={busy} className="mt-2 text-indigo-300 hover:text-indigo-200 font-medium disabled:opacity-50">{busy ? "Sending…" : "Send it again"}</button>
            {msg && <p className="text-gray-400 mt-1">{msg}</p>}
          </>)}
        </div>
        <button onClick={() => setState("hidden")} aria-label="Close" className="text-gray-400 hover:text-gray-200"><X className="w-4 h-4" /></button>
      </div>
    </div>
  </>);
}
