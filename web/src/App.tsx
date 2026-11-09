import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence, PanInfo } from "motion/react";
import {
  Briefcase, X, MapPin, TrendingUp, Settings, BarChart3, Clock,
  CheckCircle, XCircle, AlertCircle, Sparkles, Building2, ExternalLink,
  LayoutGrid, List, Check, RefreshCw, Bell, Search as SearchIcon,
  Zap, Target, Loader2, Plus, RotateCcw, Ban, Link2, Search, MessageSquare, FileText, Info,
  ShieldCheck, Trash2, Users, Send, Wand2, LogOut, Eye, EyeOff, Lock,
  Download,
} from "lucide-react";
import { api, toUiJob, linkSiteName, openLabel, type UiJob, type Me, type Stats, type Activity, type CvOptimizerResult, type LockedMatches } from "./api/client";
import { enablePush, pushState } from "./lib/push";

type View = "swipe" | "dashboard";
type DashboardTab = "queue" | "applied" | "deferred" | "passed" | "activity" | "analytics" | "admin";

const NAV_KEY = "jh.nav";

// Decide the initial screen on (re)mount, synchronously, before first paint.
// Priority: push deep-link  >  last screen this session (survives refresh)  >
// fall back to the new-jobs rule once jobs load (resolved=false).
function readInitialNav(): { view: View; tab: DashboardTab; resolved: boolean } {
  try {
    const params = new URLSearchParams(window.location.search);
    const v = params.get("view") || window.location.hash.replace("#", "");
    if (v === "applied") return { view: "dashboard", tab: "applied", resolved: true };
    if (v === "dashboard") return { view: "dashboard", tab: "queue", resolved: true };
    const saved = JSON.parse(sessionStorage.getItem(NAV_KEY) || "null");
    if (saved && (saved.view === "swipe" || saved.view === "dashboard")) {
      return { view: saved.view, tab: saved.tab || "queue", resolved: true };
    }
  } catch { /* ignore */ }
  return { view: "swipe", tab: "queue", resolved: false };
}

/* What happened AFTER the application went out. Deliberately separate from
   applyStatus, which records that it was SENT - /api/set-stage used to write
   apply_status and erase that, so a confirmed submission became "interviewing"
   and the evidence it had ever been sent was gone. */
const STAGES: [string, string, string][] = [
  ["screening",    "Screening",    "bg-blue-600/20 border-blue-500/60 text-blue-200"],
  ["interviewing", "Interviewing", "bg-amber-600/20 border-amber-500/60 text-amber-200"],
  ["offer",        "Offer",        "bg-green-600/20 border-green-500/60 text-green-200"],
  ["rejected",     "Rejected",     "bg-red-600/20 border-red-500/60 text-red-200"],
];

const REJECT_REASONS: Record<string, string> = {
  fit: "Not a good fit",
  seniority: "Wrong seniority level",
  salary: "Salary too low",
  company: "Bad company",
  location: "Wrong location",
  applied: "Already applied elsewhere",
  broken_link: "Link is broken",
  gone: "Job no longer exists",
};

export function SwipeFlow() {
  const initialNav = useRef(readInitialNav());
  const [view, setView] = useState<View>(initialNav.current.view);
  const [dashboardTab, setDashboardTab] = useState<DashboardTab>(initialNav.current.tab);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [me, setMe] = useState<Me | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [newJobs, setNewJobs] = useState<UiJob[]>([]);
  const [approvedJobs, setApprovedJobs] = useState<UiJob[]>([]);
  const [appliedJobs, setAppliedJobs] = useState<UiJob[]>([]);
  const [deferredJobs, setDeferredJobs] = useState<UiJob[]>([]);
  // How many matches the plan is holding back (Phase 5). A count, never jobs.
  const [locked, setLocked] = useState<LockedMatches | null>(null);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [direction, setDirection] = useState<"left" | "right" | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  // Which Settings tab to open on: "Upgrade plan" opens Plan (billing is dark
  // until Step 11, so that tab is where an upgrade starts for now).
  const [settingsTab, setSettingsTab] = useState<SettingsTab | undefined>(undefined);
  const openUpgrade = () => { setSettingsTab("plan"); setShowSettings(true); };
  // Set the moment onboarding finishes, so the next render leaves the flow even
  // though /api/me has not come back yet. Without it the wizard reappears for
  // one frame on top of the dashboard.
  const [dismissedOnboarding, setDismissedOnboarding] = useState(false);
  /* Setup runs once and then can never be seen again - which made it the one
     flow nobody could check after shipping it. ?onboarding=1 replays it for
     anyone; Settings links here rather than needing a DB edit. The param is
     stripped on finish so a bookmark or a refresh does not trap the user in
     the wizard forever. */
  const [replayOnboarding, setReplayOnboarding] = useState(() => {
    try { return new URLSearchParams(window.location.search).get("onboarding") === "1"; }
    catch { return false; }
  });
  const endOnboarding = () => {
    setReplayOnboarding(false);
    setDismissedOnboarding(true);
    try {
      const u = new URL(window.location.href);
      if (u.searchParams.has("onboarding")) { u.searchParams.delete("onboarding"); window.history.replaceState({}, "", u.pathname + u.search + u.hash); }
    } catch {}
    loadAll();
  };
  const [selectedJob, setSelectedJob] = useState<UiJob | null>(null);
  const [pendingReject, setPendingReject] = useState<UiJob | null>(null);
  const [undo, setUndo] = useState<{ type: "approve" | "defer"; job: UiJob } | null>(null);
  const [rejectedDelta, setRejectedDelta] = useState(0);
  const undoTimer = useRef<any>(null);
  const [searchMsg, setSearchMsg] = useState("");
  const [actionError, setActionError] = useState("");

  // pull-to-refresh
  const [pull, setPull] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const pullStart = useRef<number | null>(null);

  // If a deep-link or a saved session screen already decided the view, don't
  // re-route on the new-jobs rule. Otherwise the rule applies once jobs load.
  const initialRouteDone = useRef(initialNav.current.resolved);

  // Remember the current screen so a page refresh keeps the user in place.
  // sessionStorage = survives refresh, clears on app relaunch (fresh launch
  // re-applies the new-jobs landing rule).
  useEffect(() => {
    try { sessionStorage.setItem(NAV_KEY, JSON.stringify({ view, tab: dashboardTab })); } catch { /* ignore */ }
  }, [view, dashboardTab]);

  const loadAll = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [meRes, statsRes, nw, ap, ad, df] = await Promise.all([
        api.me(), api.stats(),
        api.jobs("new", "match"), api.jobs("approved", "match"), api.jobs("applied", "date"), api.jobs("deferred", "date"),
      ]);
      setMe(meRes); setStats(statsRes);
      api.lockedMatches().then(setLocked).catch(() => setLocked(null));
      setNewJobs(nw.map(toUiJob)); setApprovedJobs(ap.map(toUiJob)); setAppliedJobs(ad.map(toUiJob)); setDeferredJobs(df.map(toUiJob));
      setCurrentIndex(0); setRejectedDelta(0); setUndo(null);
      // First load only: land on swipe if there are new jobs, else the dashboard.
      if (!initialRouteDone.current) {
        initialRouteDone.current = true;
        if (nw.length > 0) { setView("swipe"); }
        else { setView("dashboard"); setDashboardTab("queue"); }
      }
    } catch (e: any) {
      if (e?.status !== 401) setError("Couldn't load your jobs.");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const reviewJobs = newJobs;
  const currentJob = reviewJobs[currentIndex];
  const remainingCount = reviewJobs.length - currentIndex;

  const approvedCount = approvedJobs.length;
  const deferredCount = deferredJobs.length;
  const rejectedCount = (stats?.rejected ?? 0) + rejectedDelta;

  const finishedSwiping = useRef(false);
  const advance = () => {
    if (currentIndex >= reviewJobs.length - 1) finishedSwiping.current = true; // last card
    setTimeout(() => { setCurrentIndex((i) => i + 1); setDirection(null); }, 240);
  };

  const armUndo = (u: { type: "approve" | "defer"; job: UiJob }) => {
    setUndo(u);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setUndo(null), 5000);
  };

  // Persist a swipe action; if the server write fails, stop swallowing it —
  // surface a clear message so the user knows the change didn't save.
  const persistAction = (p: Promise<any>, label: string) => {
    p.then(() => setActionError("")).catch((e: any) => {
      setActionError(`Couldn't save your ${label} \u2014 ${e?.message || "the change wasn\u2019t saved"}. Pull down to refresh.`);
    });
  };

  const handleApprove = () => {
    const job = currentJob; if (!job || pendingReject) return;
    setDirection("right");
    setApprovedJobs((p) => [{ ...job, status: "approved" }, ...p]);
    persistAction(api.approve(job.id), "approve");
    armUndo({ type: "approve", job });
    advance();
  };

  const handlePass = () => {
    const job = currentJob; if (!job || pendingReject) return;
    setDirection("left");
    setRejectedDelta((c) => c + 1);
    setPendingReject(job);
    advance();
  };

  const finalizeReject = (reasonKey: string | null) => {
    const job = pendingReject; setPendingReject(null);
    if (!job) return;
    const reason = reasonKey ? (REJECT_REASONS[reasonKey] || reasonKey) : "Not a fit";
    persistAction(api.reject(job.id, reason), "pass");
  };

  const undoPass = () => {
    setPendingReject(null);
    setRejectedDelta((c) => Math.max(0, c - 1));
    setCurrentIndex((i) => Math.max(0, i - 1)); // server never told — just go back
  };

  const runSearchNow = async () => {
    setSearchMsg("Starting search\u2026");
    try { await api.runSearch(); setSearchMsg("Search started \u2014 new matches appear in a few minutes. Pull down to refresh."); }
    catch (e: any) { setSearchMsg(e?.message || "Couldn't start a search right now."); }
  };

  // "Decide later" is a plan feature (Free: no). Hidden rather than refused
  // after the card has already moved; the server refuses it either way.
  const canDefer = planLimit(me, "deferred_list") !== false;
  const handleDefer = () => {
    const job = currentJob; if (!job || pendingReject || !canDefer) return;
    setDirection(null);
    setDeferredJobs((p) => [{ ...job, status: "deferred" }, ...p]);
    persistAction(api.later(job.id), "defer");
    armUndo({ type: "defer", job });
    advance();
  };

  const doUndo = () => {
    if (!undo) return;
    const { type, job } = undo;
    if (type === "approve") setApprovedJobs((p) => p.filter((j) => j.id !== job.id));
    if (type === "defer") setDeferredJobs((p) => p.filter((j) => j.id !== job.id));
    api.restore(job.id).catch(() => {});
    setCurrentIndex((i) => Math.max(0, i - 1));
    setUndo(null);
    if (undoTimer.current) clearTimeout(undoTimer.current);
  };

  const unDefer = (job: UiJob) => {
    setDeferredJobs((p) => p.filter((j) => j.id !== job.id));
    setNewJobs((p) => (p.some((j) => j.id === job.id) ? p : [{ ...job, status: "new" }, ...p]));
    api.restore(job.id).catch(() => {});
  };

  const onMarkApplied = (job: UiJob) => {
    setApprovedJobs((p) => p.filter((j) => j.id !== job.id));
    setAppliedJobs((p) => [{ ...job, status: "applied", applyStatus: "submitted" }, ...p]);
    api.markApplied(job.id).catch(() => {});
  };
  const onRemoveQueued = (job: UiJob, reason: string) => {
    setApprovedJobs((p) => p.filter((j) => j.id !== job.id));
    api.reject(job.id, reason).catch(() => {});
  };
  const onRetryApply = (job: UiJob) => {
    setApprovedJobs((p) => p.map((j) => (j.id === job.id ? { ...j, applyStatus: "applying" } : j)));
    setAppliedJobs((p) => p.map((j) => (j.id === job.id ? { ...j, applyStatus: "applying" } : j)));
    api.applyNow(job.id).catch(() => {});
  };

  // pull-to-refresh handlers (swipe screen only)
  const onTouchStart = (e: React.TouchEvent) => { if (window.scrollY <= 0 && !pendingReject) pullStart.current = e.touches[0].clientY; };
  const onTouchMove = (e: React.TouchEvent) => {
    if (pullStart.current == null) return;
    const dy = e.touches[0].clientY - pullStart.current;
    if (dy > 0) setPull(Math.min(dy * 0.4, 80));
  };
  const onTouchEnd = () => {
    if (pull > 55) { setRefreshing(true); loadAll().finally(() => { setRefreshing(false); setPull(0); }); }
    else setPull(0);
    pullStart.current = null;
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (showSettings || view === "dashboard" || !currentJob || pendingReject) return;
      if (e.key === "ArrowRight" || e.key === "Enter") handleApprove();
      else if (e.key === "ArrowLeft" || e.key === "Backspace") handlePass();
      else if (e.key === "ArrowDown" || e.key === "d") handleDefer();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentJob, showSettings, view, pendingReject]);

  // Right after the user swipes the LAST card, jump to the dashboard.
  // Gated by finishedSwiping so opening Swipe later (with an already-empty
  // stack) shows the "All done / Run a new search" screen instead of bouncing.
  useEffect(() => {
    if (view === "swipe" && !loading && !pendingReject && finishedSwiping.current && currentIndex >= reviewJobs.length
        && !(locked && locked.count > 0)) {
      finishedSwiping.current = false;
      setView("dashboard"); setDashboardTab("queue");
    }
  }, [view, loading, pendingReject, currentIndex, reviewJobs.length, locked]);

  if (loading) return <CenterState icon={<Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />} title="Loading your jobs…" />;
  if (error) return <CenterState icon={<AlertCircle className="w-10 h-10 text-red-400" />} title={error} action={{ label: "Retry", onClick: loadAll }} />;

  /* A brand-new account has no titles, no keywords and no analysed CV, so the
     dashboard it would otherwise land on is three empty lists and a search that
     can only fail. Shown until they finish it or skip it; `onboarding_dismissed`
     means never ask again. Note /dashboard (legacy) still self-heals
     onboarding_complete=1 on visit, so a user who detours there is treated as
     onboarded - that goes away with the legacy UI. */
  if (me && (replayOnboarding || (!me.onboarding_complete && !me.onboarding_dismissed && !dismissedOnboarding))) {
    return <OnboardingView me={me} onDone={endOnboarding} />;
  }

  if (view === "dashboard") {
    return (
      <DashboardView
        me={me} activeTab={dashboardTab} setActiveTab={setDashboardTab}
        onBackToSwipe={() => setView("swipe")}
        approvedJobs={approvedJobs} appliedJobs={appliedJobs} deferredJobs={deferredJobs} onUnDefer={unDefer}
        onMarkApplied={onMarkApplied} onRemoveQueued={onRemoveQueued} onRetry={onRetryApply}
        approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount} stats={stats}
        selectedJob={selectedJob} setSelectedJob={setSelectedJob}
        onOpenSettings={() => setShowSettings(true)} showSettings={showSettings} onCloseSettings={() => setShowSettings(false)}
        onReload={loadAll} locked={locked}
      />
    );
  }

  if (currentIndex >= reviewJobs.length && locked && locked.count > 0) {
    return (
      <>
        <LockedSwipeEnd locked={locked} me={me} reviewed={reviewJobs.length}
          onUpgrade={openUpgrade} onDashboard={() => { setView("dashboard"); setDashboardTab("queue"); }} />
        <AnimatePresence>{showSettings && me && <SettingsModal me={me} initialTab={settingsTab} onClose={() => { setShowSettings(false); setSettingsTab(undefined); }} />}</AnimatePresence>
        <AnimatePresence>{pendingReject && <RejectReasonSheet job={pendingReject} onPick={finalizeReject} onUndo={undoPass} />}</AnimatePresence>
      </>
    );
  }

  if (currentIndex >= reviewJobs.length) {
    return (
      <>
        <AllDoneScreen approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount}
          onViewDashboard={() => setView("dashboard")} onStartNew={loadAll} empty={newJobs.length === 0}
          locked={locked} me={me} />
        <AnimatePresence>{showSettings && me && <SettingsModal me={me} initialTab={settingsTab} onClose={() => { setShowSettings(false); setSettingsTab(undefined); }} />}</AnimatePresence>
        <AnimatePresence>{pendingReject && <RejectReasonSheet job={pendingReject} onPick={finalizeReject} onUndo={undoPass} />}</AnimatePresence>
      </>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 relative overflow-hidden"
      onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}>
      {/* pull-to-refresh indicator */}
      <div className="absolute top-0 left-0 right-0 flex justify-center z-20 pointer-events-none" style={{ height: pull, opacity: pull / 80 }}>
        <div className="mt-2"><RefreshCw className={`w-6 h-6 text-indigo-400 ${refreshing ? "animate-spin" : ""}`} style={{ transform: `rotate(${pull * 3}deg)` }} /></div>
      </div>

      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <motion.div className="absolute w-96 h-96 bg-gradient-to-br from-indigo-500/20 to-slate-500/20 rounded-full blur-3xl" animate={{ x: [0, 100, 0], y: [0, 50, 0] }} transition={{ duration: 20, repeat: Infinity, ease: "linear" }} style={{ top: "10%", left: "10%" }} />
        <motion.div className="absolute w-96 h-96 bg-gradient-to-br from-indigo-500/20 to-blue-500/20 rounded-full blur-3xl" animate={{ x: [0, -100, 0], y: [0, -50, 0] }} transition={{ duration: 15, repeat: Infinity, ease: "linear" }} style={{ bottom: "10%", right: "10%" }} />
      </div>

      <header className="relative z-10 bg-gray-800/80 backdrop-blur-lg border-b border-gray-700 safe-top">
        <div className={`${SHELL} px-5 lg:px-8 py-4 flex items-center justify-between`}>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-xl flex items-center justify-center"><Briefcase className="w-5 h-5 text-white" /></div>
            <div><h1 className="text-xl font-bold text-white">Job Hunter</h1><p className="text-xs text-gray-400">Swipe to find your next role</p></div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => loadAll()} className="p-2.5 bg-gray-700 rounded-xl border border-gray-600 active:bg-gray-600" title="Refresh"><RefreshCw className="w-5 h-5 text-gray-300" /></button>
            <button onClick={() => setView("dashboard")} className="p-2.5 bg-gray-700 rounded-xl border border-gray-600 active:bg-gray-600"><LayoutGrid className="w-5 h-5 text-gray-300" /></button>
            <button onClick={() => setShowSettings(true)} className="p-2.5 bg-gray-700 rounded-xl border border-gray-600 active:bg-gray-600"><Settings className="w-5 h-5 text-gray-300" /></button>
          </div>
        </div>
      </header>

      <div className="relative z-10 bg-gray-800/80 backdrop-blur-lg border-b border-gray-700">
        <div className={`${SHELL} px-5 lg:px-8 py-3`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-300">{remainingCount} job{remainingCount !== 1 ? "s" : ""} to review</span>
            <span className="text-sm text-gray-400">{currentIndex + 1} / {reviewJobs.length}
              {locked && locked.count > 0 && <span className="ml-2 inline-flex items-center gap-1 text-indigo-300" title={`${locked.count} more match${locked.count === 1 ? "" : "es"} on a higher plan`}><Lock className="w-3.5 h-3.5" />+{locked.count}</span>}</span>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden"><motion.div className="h-full bg-gradient-to-r from-indigo-500 to-indigo-700" initial={{ width: 0 }} animate={{ width: `${((currentIndex + 1) / reviewJobs.length) * 100}%` }} transition={{ duration: 0.3 }} /></div>
        </div>
      </div>

      <div className={`relative z-10 ${SHELL} px-5 lg:px-8 pt-3`}>
        <button onClick={runSearchNow} className="w-full lg:max-w-md lg:mx-auto py-2.5 bg-indigo-600/15 border border-indigo-600/40 text-indigo-200 rounded-xl text-sm font-medium flex items-center justify-center gap-2"><SearchIcon className="w-4 h-4" />Run a new search</button>
        {searchMsg && <p className="text-xs text-indigo-300 mt-1.5 text-center">{searchMsg}</p>}
        {actionError && <p className="text-xs text-red-300 mt-1.5 text-center">{actionError}</p>}
      </div>

      <NotifyNudge />

      <div className="relative z-10 mx-auto w-full max-w-xl lg:max-w-5xl px-5 lg:px-8 py-6 pb-10">
        <AnimatePresence mode="wait">
          {currentJob && <SwipeCard key={currentJob.id} job={currentJob} direction={direction} onApprove={handleApprove} onPass={handlePass} onDefer={canDefer ? handleDefer : undefined} />}
        </AnimatePresence>
      </div>

      <AnimatePresence>{undo && !pendingReject && <UndoToast undo={undo} onUndo={doUndo} />}</AnimatePresence>
      <AnimatePresence>{showSettings && me && <SettingsModal me={me} initialTab={settingsTab} onClose={() => { setShowSettings(false); setSettingsTab(undefined); }} />}</AnimatePresence>
      <AnimatePresence>{pendingReject && <RejectReasonSheet job={pendingReject} onPick={finalizeReject} onUndo={undoPass} />}</AnimatePresence>
    </div>
  );
}

function UndoToast({ undo, onUndo }: { undo: { type: string; job: UiJob }; onUndo: () => void }) {
  const verb = undo.type === "approve" ? "Approved" : "Deferred";
  return (
    <motion.div initial={{ y: 60, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 60, opacity: 0 }}
      className="fixed left-0 right-0 bottom-0 z-[58] flex justify-center px-4 safe-bottom pointer-events-none">
      <div className="pointer-events-auto mb-4 flex items-center gap-3 bg-gray-800 border border-gray-600 rounded-2xl shadow-2xl px-4 py-3 max-w-sm w-full">
        <span className="text-sm text-gray-200 flex-1 truncate">{verb} <span className="font-semibold">{undo.job.company}</span></span>
        <button onClick={onUndo} className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 active:bg-gray-600 text-indigo-300 text-sm font-medium rounded-xl"><RotateCcw className="w-4 h-4" />Undo</button>
      </div>
    </motion.div>
  );
}

/* "Finish applying" (2026-11-08): a site (Greenhouse) emailed the user a
   security code mid-application; the application is sent only once it is
   pasted. Rendered at the app root (main.tsx) so every screen shows it - it
   first sat in the swipe view only, and the app opens on the Dashboard. The notification's link opens the app, but
   a tap on it when the app is already open only focuses it - so this checks
   whenever the app comes to the front, and every 15 s while it is showing. */
export function FinishApplying() {
  const [runs, setRuns] = useState<any[]>([]);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Record<string, string>>({});
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const check = useCallback(() => {
    if (document.visibilityState !== "visible") return;
    api.applyRunsWaiting().then((r) => setRuns(r.waiting || [])).catch(() => {});
  }, []);
  useEffect(() => {
    check();
    const t = setInterval(check, 15000);
    const onVis = () => check();
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("focus", onVis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVis); window.removeEventListener("focus", onVis); };
  }, [check]);
  const send = async (id: string) => {
    setMsg((m) => ({ ...m, [id]: "" }));
    try {
      await api.applyRunCode(id, (codes[id] || "").trim());
      setHidden((h) => ({ ...h, [id]: true }));
      setMsg((m) => ({ ...m, [id]: "sent" }));
    } catch (e: any) { setMsg((m) => ({ ...m, [id]: e?.message || "The code was not accepted" })); }
  };
  const showing = runs.filter((r) => !hidden[r.run_id]);
  const sent = Object.values(msg).includes("sent");
  if (!showing.length && !sent) return null;
  return (
    <div className="fixed inset-x-0 bottom-0 z-50 p-3 lg:p-6 pointer-events-none">
      <div className="pointer-events-auto mx-auto max-w-md space-y-2">
        {showing.map((r) => (
          <div key={r.run_id} className="bg-gray-900 border border-amber-500/50 rounded-2xl p-4 shadow-2xl">
            <p className="text-sm font-semibold text-white">Finish applying to {r.company || "this job"}</p>
            <p className="text-xs text-gray-300 mt-1">
              {r.company || "The employer"} emailed you a security code{r.title ? ` for ${r.title}` : ""}. Your application is sent only once you paste it here.
              {" "}About {Math.max(1, Math.ceil((r.code_seconds_left || 0) / 60))} min left.
            </p>
            <div className="flex gap-2 mt-3">
              <input value={codes[r.run_id] || ""} onChange={(e) => setCodes((c) => ({ ...c, [r.run_id]: e.target.value }))}
                placeholder="Code from the email" autoComplete="one-time-code"
                className="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded-xl px-3 py-2 text-sm text-gray-100 tracking-widest" />
              <button onClick={() => send(r.run_id)} disabled={(codes[r.run_id] || "").trim().length < 4}
                className="px-4 py-2 rounded-xl bg-indigo-600 active:bg-indigo-700 text-white text-sm font-medium disabled:opacity-50">Send</button>
            </div>
            {msg[r.run_id] && msg[r.run_id] !== "sent" && <p className="text-xs text-red-300 mt-2">{msg[r.run_id]}</p>}
          </div>
        ))}
        {sent && !showing.length && (
          <div className="bg-gray-900 border border-green-500/40 rounded-2xl p-3 shadow-2xl flex items-center justify-between gap-3">
            <p className="text-sm text-green-300">Code sent - finishing your application. Its status updates in your queue.</p>
            <button onClick={() => setMsg({})} className="p-1.5 text-gray-400 active:text-white shrink-0"><X className="w-4 h-4" /></button>
          </div>
        )}
      </div>
    </div>
  );
}

function NotifyNudge() {
  const [show, setShow] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => {
    try { if (pushState() === "default" && !localStorage.getItem("jh_notify_nudge")) setShow(true); } catch {}
  }, []);
  if (!show) return null;
  const dismiss = () => { try { localStorage.setItem("jh_notify_nudge", "1"); } catch {} setShow(false); };
  const enable = async () => { setMsg("…"); const r = await enablePush(); if (r.ok) dismiss(); else setMsg(r.reason || "Couldn't enable"); };
  return (
    <div className={`relative z-10 ${SHELL} px-5 lg:px-8 pt-3`}>
      <div className="flex items-center gap-3 lg:max-w-3xl lg:mx-auto bg-indigo-600/15 border border-indigo-600/40 rounded-2xl px-4 py-3">
        <Bell className="w-5 h-5 text-indigo-300 shrink-0" />
        <div className="flex-1 min-w-0"><p className="text-sm font-medium text-white leading-tight">Get notified about new jobs</p>{msg && <p className="text-xs text-gray-400">{msg}</p>}</div>
        <button onClick={enable} className="px-3 py-1.5 bg-indigo-600 active:bg-indigo-700 text-white text-sm font-medium rounded-xl shrink-0">Enable</button>
        <button onClick={dismiss} className="p-1.5 text-gray-400 active:text-white shrink-0"><X className="w-4 h-4" /></button>
      </div>
    </div>
  );
}

function SwipeCard({ job, direction, onApprove, onPass, onDefer }: any) {
  const onDragEnd = (_: any, info: PanInfo) => { if (info.offset.x > 110) onApprove(); else if (info.offset.x < -110) onPass(); };
  const [showScoreInfo, setShowScoreInfo] = useState(false);
  return (
    <motion.div initial={{ scale: 0.85, opacity: 0 }}
      animate={{ scale: 1, opacity: 1, rotateZ: direction === "left" ? -22 : direction === "right" ? 22 : 0, x: direction === "left" ? -900 : direction === "right" ? 900 : 0 }}
      exit={{ scale: 0.85, opacity: 0 }} transition={{ duration: 0.24 }}
      drag="x" dragConstraints={{ left: 0, right: 0 }} dragElastic={0.7} onDragEnd={onDragEnd} whileDrag={{ cursor: "grabbing" }} className="relative">
      <div className="bg-gray-800 rounded-3xl shadow-2xl overflow-hidden border border-gray-700">
        <div className="h-28 bg-gradient-to-br from-indigo-600 via-indigo-700 to-slate-800 relative">
          <div className="absolute inset-0 bg-black/20" />
          <div className="absolute bottom-4 left-5 right-5 flex items-center gap-3">
            <div className="w-14 h-14 bg-white rounded-2xl shadow-lg flex items-center justify-center shrink-0"><Building2 className="w-7 h-7 text-gray-700" /></div>
            <div className="min-w-0"><h3 className="text-xl font-bold text-white truncate">{job.company}</h3><div className="flex items-center gap-2 text-white/90 text-sm mt-0.5"><MapPin className="w-4 h-4" /><span className="truncate">{(job.location || "").split(",")[0]}</span></div></div>
          </div>
        </div>
        <div className="p-5 lg:p-7 lg:grid lg:grid-cols-2 lg:gap-7 lg:items-start">
          <div className="space-y-5">
          <div>
            <h2 className="text-xl lg:text-2xl font-bold text-white mb-2">{job.title}</h2>
            <div className="flex items-center gap-4 flex-wrap">
              {job.source && <div className="flex items-center gap-1.5 text-sm text-gray-400"><Briefcase className="w-4 h-4" /><span>{job.source.split("/")[0]}</span></div>}
              {job.timeAgo && <div className="flex items-center gap-1.5 text-sm text-gray-400"><Clock className="w-4 h-4" /><span>{job.timeAgo}</span></div>}
            </div>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs font-medium text-gray-400">Scores</span>
              <button type="button" onClick={() => setShowScoreInfo((v: boolean) => !v)} className="text-xs text-indigo-300 active:text-indigo-200 flex items-center gap-1"><Info className="w-3.5 h-3.5" />What’s this?</button>
            </div>
            <div className="flex gap-3">
              <ScoreBox label="Match Score" value={job.matchScore} icon={<TrendingUp className="w-4 h-4 text-blue-400" />} tone="blue" />
              <ScoreBox label="Your Strength" value={job.candidateScore} icon={<Sparkles className="w-4 h-4 text-green-400" />} tone="green" />
            </div>
            {showScoreInfo && (
              <div className="mt-2 text-xs text-gray-400 bg-gray-900/60 border border-gray-700 rounded-xl p-3 space-y-1.5">
                <p><span className="text-blue-300 font-medium">Match Score</span> — how well this job fits your profile (titles, skills, seniority).</p>
                <p><span className="text-green-300 font-medium">Your Strength</span> — your fit as a candidate: the match score adjusted for your CV and experience. They’re equal when no adjustments apply.</p>
                {job.linkPenalty > 0 && <p><span className="text-slate-300 font-medium">No direct link</span> — we couldn’t find this posting on the company’s own site yet, so it ranks {job.linkPenalty} points lower. We’ll keep looking.</p>}
              </div>
            )}
          </div>
          {job.whyFits && (<div className="bg-gradient-to-br from-amber-950/50 to-orange-950/50 rounded-2xl p-4 border-2 border-amber-700"><div className="flex items-center gap-2 mb-2"><div className="w-7 h-7 bg-amber-500 rounded-full flex items-center justify-center"><Sparkles className="w-4 h-4 text-white" /></div><h4 className="font-bold text-amber-400 text-sm">Why this is a strong fit</h4></div><p className="text-gray-300 text-sm leading-relaxed">{job.whyFits}</p></div>)}
          </div>
          <div className="space-y-5 mt-5 lg:mt-0">
          {job.description && (<div><h4 className="font-semibold text-white mb-1.5 text-sm">About the role</h4><p className="text-gray-400 text-sm leading-relaxed line-clamp-6">{job.description}</p></div>)}
          <div className={`flex items-center gap-2 px-4 py-2 rounded-xl ${job.verified ? "bg-green-950/50 text-green-400 border border-green-800" : "bg-orange-950/50 text-orange-400 border border-orange-800"}`}>
            {job.verified ? <><CheckCircle className="w-4 h-4" /><span className="text-sm font-medium">{job.linkPenalty > 0 && linkSiteName(job.url) ? `Posting is live on ${linkSiteName(job.url)}` : "URL verified — ready to apply"}</span></> : <><AlertCircle className="w-4 h-4" /><span className="text-sm font-medium">URL not verified yet</span></>}
          </div>
          {job.linkPenalty > 0 && <div><LinkBadge job={job} /></div>}
          </div>
        </div>
        <div className="p-5 lg:px-7 pt-0 space-y-3 lg:max-w-2xl lg:mx-auto lg:w-full">
          <div className="flex gap-3">
            <motion.button whileTap={{ scale: 0.95 }} onClick={onPass} className="flex-1 py-4 bg-gradient-to-r from-red-500 to-rose-600 text-white rounded-2xl font-semibold shadow-lg flex items-center justify-center gap-2"><X className="w-5 h-5" />Pass</motion.button>
            <motion.button whileTap={{ scale: 0.95 }} onClick={onApprove} className="flex-1 py-4 bg-gradient-to-r from-emerald-500 to-green-600 text-white rounded-2xl font-semibold shadow-lg flex items-center justify-center gap-2"><Check className="w-5 h-5" />Approve</motion.button>
          </div>
          {onDefer && <motion.button whileTap={{ scale: 0.98 }} onClick={onDefer} className="w-full py-3 bg-gray-700 active:bg-gray-600 text-gray-200 rounded-2xl font-medium flex items-center justify-center gap-2"><Clock className="w-4 h-4" />Decide Later</motion.button>}
        </div>
      </div>
    </motion.div>
  );
}

function RejectReasonSheet({ job, onPick, onUndo }: { job: UiJob; onPick: (k: string | null) => void; onUndo: () => void }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-[55] flex items-end justify-center" onClick={() => onPick(null)}>
      <div className="absolute inset-0 bg-black/40" />
      <motion.div initial={{ y: 60 }} animate={{ y: 0 }} exit={{ y: 60 }} onClick={(e) => e.stopPropagation()} className="relative w-full max-w-xl bg-gray-900 border-t border-gray-700 rounded-t-3xl p-5 pb-7 safe-bottom">
        <div className="flex items-center justify-between mb-3">
          <div className="w-10 h-1.5 bg-gray-700 rounded-full mx-auto" />
          <button onClick={onUndo} className="absolute right-5 top-5 flex items-center gap-1 text-indigo-300 text-sm font-medium"><RotateCcw className="w-4 h-4" />Undo</button>
        </div>
        <p className="text-center text-gray-300 font-medium mb-1">Why did you pass on</p>
        <p className="text-center text-white font-semibold mb-4 truncate">{job.company}?</p>
        <div className="space-y-2.5">
          {Object.entries(REJECT_REASONS).map(([k, label]) => (<button key={k} onClick={() => onPick(k)} className="w-full py-3 bg-gray-800 border border-gray-700 active:bg-gray-700 text-gray-200 rounded-xl font-medium">{label}</button>))}
          <button onClick={() => onPick(null)} className="w-full py-3 text-gray-400 active:text-white rounded-xl font-medium">Skip</button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function ScoreBox({ label, value, icon, tone }: { label: string; value: number | null; icon: any; tone: "blue" | "green" }) {
  const pct = value ?? 0;
  const t = tone === "blue" ? { bg: "bg-blue-950/50", border: "border-blue-800", text: "text-blue-400", bar: "from-blue-500 to-indigo-500", track: "bg-blue-900/50" } : { bg: "bg-green-950/50", border: "border-green-800", text: "text-green-400", bar: "from-green-500 to-emerald-500", track: "bg-green-900/50" };
  return (
    <div className={`flex-1 ${t.bg} rounded-2xl p-3.5 border ${t.border}`}>
      <div className="flex items-center justify-between mb-1.5"><span className="text-xs font-medium text-gray-300">{label}</span>{icon}</div>
      <div className={`text-2xl font-bold ${t.text}`}>{value === null ? "—" : `${value}%`}</div>
      <div className={`mt-2 h-2 ${t.track} rounded-full overflow-hidden`}><div className={`h-full bg-gradient-to-r ${t.bar} rounded-full`} style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function CenterState({ icon, title, action }: { icon: any; title: string; action?: { label: string; onClick: () => void } }) {
  return (<div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 flex flex-col items-center justify-center p-6 text-center">{icon}<p className="text-gray-300 mt-4 mb-4">{title}</p>{action && <button onClick={action.onClick} className="px-5 py-2.5 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl font-medium">{action.label}</button>}</div>);
}

// One width for the whole desktop shell. Capped rather than unbounded: at
// 1920px this fills the screen with comfortable gutters, and on an ultrawide it
// stops rows growing so long the eye loses its place returning to the next one.
const SHELL = "mx-auto w-full max-w-[1600px]";

const TABS: [DashboardTab, string, any][] = [
  ["queue", "Queue", CheckCircle], ["applied", "Applied", Rocket], ["deferred", "Deferred", Clock],
  ["passed", "Passed", XCircle], ["activity", "Activity", List], ["analytics", "Analytics", BarChart3],
];
// Admin is a page, not a window floating over Settings. Kept out of TABS so a
// non-admin never renders it at all.
const ADMIN_TAB: [DashboardTab, string, any] = ["admin", "Admin", ShieldCheck];

function DashboardView(p: any) {
  const { me, activeTab, setActiveTab, onBackToSwipe, approvedJobs, appliedJobs, deferredJobs, onUnDefer, onMarkApplied, onRemoveQueued, onRetry, approvedCount, rejectedCount, deferredCount, selectedJob, setSelectedJob, onOpenSettings, showSettings, onCloseSettings, onReload, stats } = p;
  const [removeTarget, setRemoveTarget] = useState<UiJob | null>(null);
  const [sortBy, setSortBy] = useState<"match" | "date" | "company">("match");
  const isAdmin = me?.role === "admin" || (me as any)?.is_admin;
  /* The headline cards told the same untruth the Analytics tab did: "Applied"
     counted a status written by four different things, and "Passed" counted
     the system's own rejections as the user's decisions. Both now show what
     the user did, with what the app did beside it rather than inside it.
     Older deploys do not send the breakdown, so these fall back to the raw
     counts rather than rendering a zero. */
  const appliedReal = typeof stats?.applied_engine === "number"
    ? stats.applied_engine + (stats.applied_manual || 0) : null;
  const appliedNotReal = typeof stats?.applied_bulk === "number"
    ? stats.applied_bulk + (stats.applied_no_url || 0) : 0;
  const passedByYou = typeof stats?.passed_by_user === "number"
    ? stats.passed_by_user + (stats.passed_unknown || 0) + (stats.rejected_archived || 0) : null;
  const filteredOut = typeof stats?.passed_by_system === "number" ? stats.passed_by_system : 0;
  const sortJobs = (arr: UiJob[]) => {
    const a = [...(arr || [])];
    if (sortBy === "company") a.sort((x, y) => (x.company || "").localeCompare(y.company || ""));
    else if (sortBy === "date") a.sort((x, y) => (y.foundDate || "").localeCompare(x.foundDate || ""));
    else {
      // Effective score, not the raw one. /api/jobs already orders by
      // (match_score - feedback_penalty) in SQL; sorting here by matchScore
      // alone silently undid that, so a job the system had demoted 20 points
      // for being somewhere the user keeps passing on sat at the top anyway.
      // Less the "no direct link" penalty too (Step 1d), same as the API.
      const eff = (j: UiJob) => (j.matchScore ?? -1) - (j.feedbackPenalty || 0) - (j.linkPenalty || 0);
      a.sort((x, y) => eff(y) - eff(x));
    }
    return a;
  };
  return (
    <div className="min-h-screen bg-gray-900 text-white lg:flex">
      {/* Desktop navigation rail. Below lg it does not exist and the button grid
          below carries navigation instead - one nav, rendered two ways, rather
          than two navigations to keep in sync. */}
      <aside className="hidden lg:flex lg:flex-col lg:w-56 xl:w-64 lg:shrink-0 lg:sticky lg:top-0 lg:h-screen bg-gray-800 border-r border-gray-700">
        <div className="flex items-center gap-3 px-5 py-5 border-b border-gray-700">
          <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-xl flex items-center justify-center shrink-0"><Briefcase className="w-5 h-5 text-white" /></div>
          <div className="min-w-0"><h1 className="text-base font-bold text-white leading-tight">Job Hunter</h1><p className="text-xs text-gray-400 truncate">{me?.name ? `Hi ${me.name.split(" ")[0]}` : "Manage applications"}</p></div>
        </div>
        <nav className="flex-1 overflow-y-auto no-scrollbar px-3 py-3 space-y-1">
          {(isAdmin ? [...TABS, ADMIN_TAB] : TABS).map(([id, label, Icon]) => { const active = activeTab === id; return (
            <button key={id} onClick={() => setActiveTab(id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl border text-sm font-medium transition-colors ${active ? "bg-indigo-600 border-indigo-500 text-white" : "bg-transparent border-transparent text-gray-400 hover:bg-gray-700/60 hover:text-gray-200"}`}>
              <Icon className="w-[18px] h-[18px] shrink-0" /><span className="truncate">{label}</span>
            </button>); })}
        </nav>
        <div className="px-3 py-3 border-t border-gray-700 space-y-1">
          <button onClick={onBackToSwipe} className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl bg-gradient-to-r from-indigo-600 to-indigo-700 text-white text-sm font-medium"><Briefcase className="w-[18px] h-[18px] shrink-0" />Swipe</button>
          <button onClick={onOpenSettings} className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-gray-400 hover:bg-gray-700/60 hover:text-gray-200 text-sm font-medium"><Settings className="w-[18px] h-[18px] shrink-0" />Settings</button>
          {/* A full-page navigation, not a fetch: /logout clears the session
              cookie in its response, which only a real navigation applies. */}
          <a href="/logout" className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-gray-400 hover:bg-gray-700/60 hover:text-gray-200 text-sm font-medium"><LogOut className="w-[18px] h-[18px] shrink-0" />Sign out</a>
        </div>
      </aside>

      <div className="flex-1 min-w-0">
      <header className="bg-gray-800 border-b border-gray-700 safe-top lg:bg-gray-900">
        <div className={`${SHELL} px-5 lg:px-8 py-4 flex items-center justify-between`}>
          <div className="flex items-center gap-3"><div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-xl flex items-center justify-center lg:hidden"><Briefcase className="w-5 h-5 text-white" /></div><div><h1 className="text-lg font-bold text-white lg:text-2xl">Dashboard</h1><p className="text-xs text-gray-400 lg:hidden">{me?.name ? `Hi ${me.name.split(" ")[0]}` : "Manage applications"}</p></div></div>
          {/* The rail carries these above lg; two copies on screen at once would
              be the thing a user notices before they notice the extra width. */}
          <div className="flex items-center gap-2 lg:hidden"><button onClick={onBackToSwipe} className="px-3.5 py-2 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl text-sm font-medium flex items-center gap-2"><Briefcase className="w-4 h-4" />Swipe</button><button onClick={onOpenSettings} className="p-2.5 bg-gray-800 rounded-xl border border-gray-700 active:bg-gray-700"><Settings className="w-5 h-5 text-gray-300" /></button></div>
        </div>
      </header>
      <div className={`${SHELL} px-5 lg:px-8 py-5`}>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <StatCard label="Queue" value={approvedCount} sub="Ready to apply" icon={<CheckCircle className="w-5 h-5 text-green-500" />} />
          <StatCard label="Applied" value={appliedReal ?? appliedJobs.length}
                    sub={appliedNotReal ? `+${appliedNotReal} marked, not sent` : "Applications sent"}
                    icon={<Rocket className="w-5 h-5 text-blue-500" />} />
          <StatCard label="Deferred" value={deferredCount} sub="Decide later" icon={<Clock className="w-5 h-5 text-amber-500" />} />
          <StatCard label="Passed" value={passedByYou ?? rejectedCount}
                    sub={filteredOut ? `+${filteredOut} filtered out` : "You passed"}
                    icon={<XCircle className="w-5 h-5 text-red-500" />} />
        </div>
        <div className="grid grid-cols-3 gap-2 mb-4 lg:hidden">
          {(isAdmin ? [...TABS, ADMIN_TAB] : TABS).map(([id, label, Icon]) => { const active = activeTab === id; return (<button key={id} onClick={() => setActiveTab(id)} className={`flex flex-col items-center justify-center gap-1 py-3 rounded-xl border transition-colors ${active ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-800 border-gray-700 text-gray-400 active:bg-gray-700"}`}><Icon className="w-5 h-5" /><span className="text-xs font-medium">{label}</span></button>); })}
        </div>
        {["queue", "applied", "deferred"].includes(activeTab) && (
          <div className="flex items-center justify-end gap-2 mb-3">
            <span className="text-xs text-gray-400">Sort by</span>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value as any)} className="px-3 py-1.5 border border-gray-700 rounded-lg bg-gray-800 text-gray-200 text-xs">
              <option value="match">Match</option>
              <option value="date">Date</option>
              <option value="company">Company</option>
            </select>
          </div>
        )}
        {p.locked?.count > 0 && activeTab === "queue" && <div className="mb-4"><LockedMatchesCard locked={p.locked} me={me} compact /></div>}
        <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
          {activeTab === "queue" && <QueueTab me={me} jobs={sortJobs(approvedJobs)} onSelectJob={setSelectedJob} onMarkApplied={onMarkApplied} onRemove={(j: UiJob) => setRemoveTarget(j)} onReload={onReload} />}
          {activeTab === "applied" && <ListTab jobs={sortJobs(appliedJobs)} onSelectJob={setSelectedJob} showStatus stages onReload={onReload} emptyIcon={Rocket} emptyTitle="Nothing applied yet" emptySub="Submitted applications appear here" heading={`${appliedJobs.length} applications submitted`} />}
          {activeTab === "deferred" && <DeferredTab jobs={sortJobs(deferredJobs)} onSelectJob={setSelectedJob} onUnDefer={onUnDefer} onReload={onReload} />}
          {activeTab === "passed" && <PassedTab />}
          {activeTab === "activity" && <ActivityTab />}
          {activeTab === "admin" && isAdmin && <AdminPanel me={me} />}
          {activeTab === "analytics" && <AnalyticsTab approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount} appliedCount={stats?.applied ?? appliedJobs.length} totalSuggested={stats?.total ?? 0} stats={stats} />}
        </div>
      </div>
      </div>
      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} onRetry={onRetry} isAdmin={isAdmin} me={me} />}
      <AnimatePresence>{removeTarget && <RejectReasonSheet job={removeTarget} onPick={(k) => { onRemoveQueued(removeTarget, k ? (REJECT_REASONS[k] || k) : "Removed from queue"); setRemoveTarget(null); }} onUndo={() => setRemoveTarget(null)} />}</AnimatePresence>
      <AnimatePresence>{showSettings && me && <SettingsModal me={me} onClose={onCloseSettings} />}</AnimatePresence>
    </div>
  );
}

function StatCard({ label, value, sub, icon }: any) {
  return (<div className="bg-gray-800 rounded-xl p-4 border border-gray-700"><div className="flex items-center justify-between mb-1.5"><span className="text-sm font-medium text-gray-400">{label}</span>{icon}</div><div className="text-2xl font-bold text-white">{value}</div><p className="text-xs text-gray-400 mt-0.5">{sub}</p></div>);
}

function FeedbackBadge({ job }: { job: UiJob }) {
  if (!job.feedbackPenalty) return null;
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-orange-950/50 border border-orange-800/60 text-orange-300"
          title={`Ranked ${job.feedbackPenalty} points lower because of your past feedback`}>
      <TrendingUp className="w-3 h-3 rotate-180" />{job.feedbackReason || "Adjusted from your feedback"}
    </span>
  );
}

/* Step 1d: we looked for this posting on the company's own site or ATS and did
   not find it, so it opens on a job board and ranks LINK_PENALTY lower. */
function LinkBadge({ job }: { job: UiJob }) {
  if (!job.linkPenalty) return null;
  const site = linkSiteName(job.url);
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-slate-800 border border-slate-600 text-slate-300"
          title={`We couldn't find this posting on the company's own site${site ? `, so it opens on ${site}` : ""}. Ranked ${job.linkPenalty} points lower; we'll keep looking.`}>
      <ExternalLink className="w-3 h-3" />No direct link
    </span>
  );
}


/* Can auto-apply send this one? (Eran, 2026-11-09: "from the queue I can't
   say which one can be relevant to the auto-apply and which one I have to do
   manually".) From apply_engine.apply_route on the server - the URL alone. */
export const AUTO_ROUTES = new Set(["auto", "site"]);
function RouteBadge({ job }: { job: UiJob }) {
  const r = job.applyRoute;
  const pill = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium border";
  if (r === "auto") return <span className={`${pill} bg-emerald-900/40 border-emerald-700 text-emerald-300`}
    title="A form system Job Hunter fills in and submits for you."><Zap className="w-3 h-3" />Auto-apply ready</span>;
  if (r === "site") return <span className={`${pill} bg-sky-900/30 border-sky-800 text-sky-300`}
    title="The company's own site. Auto-apply tries it; some sites need you to finish."><Zap className="w-3 h-3" />Auto-apply: may work</span>;
  if (r === "account") return <span className={`${pill} bg-amber-900/30 border-amber-800 text-amber-300`}
    title="Workday asks you to create an account on the employer's site first, so you apply yourself."><Lock className="w-3 h-3" />Manual · account needed</span>;
  if (r === "board") {
    const site = linkSiteName(job.url) || "job board";
    return <span className={`${pill} bg-amber-900/30 border-amber-800 text-amber-300`}
      title={`Opens on ${site}, which needs your login. Auto-apply only gets through if it finds the same job on the company's own site.`}>
      <ExternalLink className="w-3 h-3" />Manual · {site}</span>;
  }
  return null;
}

/* The questions an application was held on (2026-11-07). Eran: "if you are
   not sure - show me the questions and I'll guide you". Drafts are prefilled;
   answers are saved and reused for the same question on later applications. */
function AnswerQuestions({ job, onDone }: { job: UiJob; onDone: () => void }) {
  const [vals, setVals] = useState<Record<string, string | string[]>>(
    () => Object.fromEntries(job.applyQuestions.map((q) => [q.key, q.draft ?? (q.type === "checkbox" ? [] : "")])));
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  const set = (k: string, v: string | string[]) => setVals((p) => ({ ...p, [k]: v }));
  const save = async () => {
    setBusy(true); setMsg("");
    try {
      const answers = job.applyQuestions.map((q) => ({ key: q.key, label: q.label, answer: vals[q.key] }))
        .filter((a) => (Array.isArray(a.answer) ? a.answer.length : String(a.answer || "").trim()));
      if (answers.length < job.applyQuestions.length) { setMsg("Please answer every question."); return; }
      await api.answerQuestions(job.id, answers);
      setMsg("Saved. The application will go out on the next apply run, and these answers are reused next time.");
      setTimeout(onDone, 1800);
    } catch (e: any) { setMsg(e?.message || "Could not save"); }
    finally { setBusy(false); }
  };
  const field = "w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-sm text-gray-100";
  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm text-gray-300">The application was held because these questions need you. My draft is filled in where I had one.</p>
      {job.applyQuestions.map((q) => (
        <div key={q.key}>
          <label className="block text-sm text-gray-200 mb-1">{q.label.replace(/[*✱]\s*$/, "")}</label>
          {q.options.length > 0 && q.type !== "checkbox" ? (
            <select value={String(vals[q.key] || "")} onChange={(e) => set(q.key, e.target.value)} className={field}>
              <option value="">Choose…</option>
              {q.options.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : q.type === "checkbox" && q.options.length > 0 ? (
            <div className="space-y-1">
              {q.options.map((o) => {
                const cur = Array.isArray(vals[q.key]) ? (vals[q.key] as string[]) : [];
                return (
                  <label key={o} className="flex items-center gap-2 text-sm text-gray-200">
                    <input type="checkbox" checked={cur.includes(o)}
                      onChange={(e) => set(q.key, e.target.checked ? [...cur, o] : cur.filter((x) => x !== o))} />{o}
                  </label>
                );
              })}
            </div>
          ) : (
            <textarea rows={q.type === "textarea" ? 4 : 2} value={String(vals[q.key] || "")}
              onChange={(e) => set(q.key, e.target.value)} className={field} />
          )}
        </div>
      ))}
      <button onClick={save} disabled={busy}
        className="px-4 py-2 bg-indigo-600 active:bg-indigo-700 text-white rounded-xl text-sm font-medium disabled:opacity-60">
        {busy ? "Saving…" : "Save answers"}
      </button>
      {msg && <p className="text-sm text-gray-300">{msg}</p>}
    </div>
  );
}

function ApplyBadge({ job }: { job: UiJob }) {
  const s = job.applyStatus;
  if (!s || s === "queued") return <span className="text-xs text-gray-400">Queued · ready to apply</span>;
  if (s === "applying") return <span className="inline-flex items-center gap-1 text-xs text-blue-300"><Loader2 className="w-3 h-3 animate-spin" />Applying…</span>;
  if (s === "manual_required") return <span className="inline-flex items-center gap-1 text-xs text-amber-300"><AlertCircle className="w-3 h-3" />Manual action needed</span>;
  if (s === "awaiting_code") return <span className="inline-flex items-center gap-1 text-xs text-amber-300"><Bell className="w-3 h-3" />Waiting for your code</span>;
  if (s === "needs_code") return <span className="inline-flex items-center gap-1 text-xs text-amber-300"><AlertCircle className="w-3 h-3" />Needs you: security code</span>;
  if (s === "needs_answers") return <span className="inline-flex items-center gap-1 text-xs text-amber-300"><MessageSquare className="w-3 h-3" />Needs your answers ({job.applyQuestions.length})</span>;
  if (s === "failed") return <span className="inline-flex items-center gap-1 text-xs text-red-300"><XCircle className="w-3 h-3" />Apply failed{job.applyFailureType ? ` (${job.applyFailureType})` : ""}</span>;
  if (s === "confirmed") return <span className="inline-flex items-center gap-1 text-xs text-green-300"><CheckCircle className="w-3 h-3" />Confirmed</span>;
  if (s === "submitted" || s === "applied") return <span className="inline-flex items-center gap-1 text-xs text-blue-300"><CheckCircle className="w-3 h-3" />Submitted</span>;
  return <span className="text-xs text-gray-400">{s}</span>;
}

/* Mirrors entitlements.can_auto_apply() on the server, in ONE place.
   Two copies of this rule is how a screen ends up offering a feature the API
   refuses, or hiding one it would allow. The server is still the authority -
   every gated POST answers 403 - this only decides what to draw. */
export function mayAutoApply(me: any): boolean {
  if (!me) return false;
  if (me.role === "admin" || me.is_admin) return true;
  // Auto-apply is not sold while it is parked (approved matrix, 2026-11-05);
  // the server's can_auto_apply() says the same, and the plan row carries it.
  return !!me.plan_info?.limits?.auto_apply;
}

/** A plan limit from /api/me. undefined = the server did not say (an older
 *  deploy): callers then show the control and let the server decide. */
export function planLimit(me: any, key: string): any {
  const lim = me?.plan_info?.limits;
  return lim && key in lim ? lim[key] : undefined;
}

/** "5 Dec" from the server's UTC 'YYYY-MM-DD HH:MM:SS'. */
export function fmtPlanDate(s: string | null | undefined): string {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short", timeZone: "Asia/Jerusalem" });
}

/* The end of the swipe deck when the plan is holding matches back: one more
   card, blurred, that cannot be swiped - no drag, no approve or pass - and an
   upgrade button in place of the actions. Like LockedMatchesCard, it is a
   skeleton: the held-back jobs never reach the browser. */
function LockedSwipeEnd({ locked, me, reviewed, onUpgrade, onDashboard }:
  { locked: LockedMatches; me: any; reviewed: number; onUpgrade: () => void; onDashboard: () => void }) {
  const n = locked.count;
  const label = me?.plan_info?.label || "current";
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 flex flex-col items-center justify-center px-5 py-8">
      <p className="text-sm text-gray-400 mb-4">{reviewed > 0 ? `You've reviewed all ${reviewed} match${reviewed === 1 ? "" : "es"} your plan shows` : "Your plan shows no more matches from this search"}</p>
      <div className="relative w-full max-w-md">
        <div aria-hidden className="absolute inset-x-6 -bottom-3 h-full rounded-3xl bg-gray-800/60 border border-gray-700" />
        <div className="relative rounded-3xl bg-gray-800 border border-indigo-500/40 shadow-2xl overflow-hidden select-none" style={{ touchAction: "none" }}>
          <div aria-hidden className="p-6 space-y-4 blur-[5px] opacity-60 pointer-events-none">
            <div className="h-6 w-3/4 rounded bg-gray-500/60" />
            <div className="h-4 w-1/2 rounded bg-gray-500/40" />
            <div className="h-4 w-1/3 rounded bg-gray-500/30" />
            <div className="grid grid-cols-2 gap-3 pt-2">
              <div className="h-20 rounded-2xl bg-indigo-500/25" /><div className="h-20 rounded-2xl bg-green-500/20" />
            </div>
            <div className="space-y-2 pt-2">{[1, 0.9, 0.95, 0.7].map((w, i) => <div key={i} className="h-3 rounded bg-gray-500/30" style={{ width: `${w * 100}%` }} />)}</div>
            <div className="grid grid-cols-2 gap-3 pt-4"><div className="h-12 rounded-2xl bg-red-500/20" /><div className="h-12 rounded-2xl bg-green-500/25" /></div>
          </div>
          <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-8 bg-gray-900/30">
            <div className="w-14 h-14 rounded-full bg-indigo-500/20 border border-indigo-400/30 flex items-center justify-center mb-3"><Lock className="w-7 h-7 text-indigo-300" /></div>
            <p className="text-xl font-bold text-white">{n} more match{n === 1 ? "" : "es"} found for you</p>
            <p className="text-sm text-gray-300 mt-2">Your {label} plan shows {locked.matches_per_search ?? "all"} per search. Upgrade to see {n === 1 ? "it" : "them"} and keep swiping.</p>
          </div>
        </div>
      </div>
      <div className="w-full max-w-md mt-8 space-y-3">
        <button onClick={onUpgrade} className="w-full py-4 bg-gradient-to-r from-indigo-500 to-indigo-700 text-white rounded-2xl font-semibold flex items-center justify-center gap-2 shadow-lg shadow-indigo-500/20"><Sparkles className="w-5 h-5" />Upgrade plan</button>
        <button onClick={onDashboard} className="w-full py-3.5 bg-gray-800 border border-gray-700 text-gray-200 rounded-2xl font-medium flex items-center justify-center gap-2"><LayoutGrid className="w-5 h-5" />Go to dashboard</button>
      </div>
    </div>
  );
}

/* Matches the plan is holding back. The browser never receives them - only the
   count - so these are skeletons behind a blur, not blurred job cards: there
   is nothing in the page to un-blur. */
function LockedMatchesCard({ locked, me, compact }: { locked: LockedMatches | null; me: any; compact?: boolean }) {
  if (!locked || !locked.count) return null;
  const per = locked.matches_per_search;
  const label = me?.plan_info?.label || "current";
  const n = locked.count;
  return (
    <div className="relative overflow-hidden rounded-2xl border border-indigo-500/30 bg-gray-800 min-h-[168px]">
      <div aria-hidden className="p-4 space-y-3 blur-[3px] select-none pointer-events-none opacity-60">
        {Array.from({ length: compact ? 2 : Math.max(2, Math.min(n, 3)) }).map((_, i) => (
          <div key={i} className="rounded-xl bg-gray-700/70 p-3 space-y-2">
            <div className="h-3.5 w-2/3 rounded bg-gray-500/60" />
            <div className="h-3 w-1/3 rounded bg-gray-500/40" />
            <div className="flex gap-2"><div className="h-5 w-12 rounded-full bg-indigo-400/30" /><div className="h-5 w-16 rounded-full bg-green-400/20" /></div>
          </div>
        ))}
      </div>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center px-5 bg-gray-900/40">
        <div className="w-10 h-10 rounded-full bg-indigo-500/20 flex items-center justify-center mb-2"><Lock className="w-5 h-5 text-indigo-300" /></div>
        <p className="font-semibold text-white">{n} more match{n === 1 ? "" : "es"} found for you</p>
        <p className="text-xs text-gray-300 mt-1 max-w-xs">
          Your {label} plan shows {per ?? "all"} per search. Upgrade to see {n === 1 ? "it" : "them"} — upgrade options open soon.
        </p>
      </div>
    </div>
  );
}

function QueueTab({ me, jobs, onSelectJob, onMarkApplied, onRemove, onReload }: any) {
  const bulk = useBulk(jobs, onReload);
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState("");
  // api.runApply existed in client.ts and was called from nowhere - a shipped
  // feature to anyone reading the client, and dead weight to everyone else.
  // This is the button. It is shown only to accounts whose plan allows it, and
  // the server refuses it anyway (403 not_entitled): the screen decides what to
  // show, the server decides what happens.
  const canAutoApply = mayAutoApply(me);
  const autoIds = jobs.filter((j: UiJob) => AUTO_ROUTES.has(j.applyRoute)).map((j: UiJob) => j.id);
  const [sending, setSending] = useState(false);
  const [sendMsg, setSendMsg] = useState("");
  const applySelected = async () => {
    const ids = jobs.map((j: UiJob) => j.id).filter((i: number) => bulk.sel.has(i));
    if (!ids.length) return;
    const manual = jobs.filter((j: UiJob) => bulk.sel.has(j.id) && !AUTO_ROUTES.has(j.applyRoute)).length;
    setSending(true); setSendMsg("");
    try {
      const r = await api.applySelected(ids);
      const parts = [`Sending ${r.started} application${r.started === 1 ? "" : "s"}, one after another`];
      if (r.skipped) parts.push(`${r.skipped} skipped (already in progress or sent${ids.length > r.max ? `, or over ${r.max} at once` : ""})`);
      if (manual) parts.push(`${manual} of them look manual and will probably come back as "Manual action needed"`);
      setSendMsg(parts.join(" · ") + ". You'll get a notification if one needs a security code.");
      bulk.clear();
      if (onReload) await onReload();
    } catch (e: any) { setSendMsg(e?.message || "Could not start them"); }
    finally { setSending(false); }
  };
  const [applying, setApplying] = useState(false);
  const [applyMsg, setApplyMsg] = useState("");
  const runApply = async () => {
    setApplying(true); setApplyMsg("Starting a run over your queue…");
    try {
      const r = await api.runApply();
      setApplyMsg(r.status === "already_running"
        ? "A run is already going — this one was not queued twice."
        : "Queued. Applications go out in the background; the queue empties as they land.");
      if (onReload) await onReload();
    } catch (e: any) {
      setApplyMsg(e?.message || "Could not start a run right now.");
    } finally { setApplying(false); }
  };
  const checkLinks = async () => {
    setChecking(true); setCheckMsg("Checking every link…");
    try {
      const r = await api.validateLinks();
      const parts = [`Checked ${r.checked}`];
      if (r.removed) parts.push(`removed ${r.removed} dead`);
      if (r.unknown) parts.push(`${r.unknown} unverified`);
      setCheckMsg(parts.join(" · ") + (r.removed ? "" : " — all good"));
      if (r.removed && onReload) await onReload();
    } catch (e: any) {
      setCheckMsg(e?.message || "Couldn't check links right now.");
    } finally { setChecking(false); }
  };
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-white">{jobs.length} in queue</h3>
        <button onClick={checkLinks} disabled={checking} className="px-3 py-2 bg-gray-700 active:bg-gray-600 text-gray-200 rounded-lg text-xs font-medium flex items-center gap-1.5 disabled:opacity-60 shrink-0">{checking ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5" />}{checking ? "Checking…" : "Check links"}</button>
        {canAutoApply && me?.auto_apply_live && jobs.length > 0 && (
          <button onClick={runApply} disabled={applying || checking}
                  className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-700 text-white rounded-lg text-xs font-medium flex items-center gap-1.5 disabled:opacity-60 shrink-0">
            {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            {applying ? "Starting…" : "Apply to queue"}
          </button>
        )}
      </div>
      {checkMsg && <p className="text-xs text-gray-400 -mt-1">{checkMsg}</p>}
      {applyMsg && <p className="text-xs text-gray-400 -mt-1">{applyMsg}</p>}
      {!jobs.length
        ? <EmptyTab icon={CheckCircle} title="No jobs in queue" sub="Approved jobs appear here" />
        : <>
            {/* Only Pass here: these are already approved, so a bulk "approve"
                would be a button that does nothing. */}
            <p className="text-xs text-gray-400">
              <span className="text-emerald-300 font-medium">{autoIds.length} can auto-apply</span>
              {" · "}{jobs.length - autoIds.length} to apply yourself
              {autoIds.length > 0 && <button onClick={() => bulk.only(autoIds)} className="ml-2 text-indigo-300 hover:text-indigo-200">Select the auto-apply ones</button>}
            </p>
            <BulkBar count={bulk.count} total={bulk.total} onAll={bulk.all} onClear={bulk.clear} busy={bulk.busy || sending} msg={sendMsg || bulk.msg}
                     actions={[
                       ...(canAutoApply && me?.auto_apply_live ? [[sending ? "Sending…" : "Auto-apply selected", applySelected, "bg-indigo-600 hover:bg-indigo-700 text-white"]] : []),
                       ["Remove from queue", bulk.run("reject", "Removed"), "bg-red-600/80 hover:bg-red-600 text-white"]]} />
            <div className="space-y-2">{jobs.map((job: UiJob, i: number) => (
              <QueueJobCard key={job.id} job={job} rank={i + 1} onSelect={onSelectJob}
                            onMarkApplied={onMarkApplied} onRemove={onRemove}
                            selected={bulk.sel.has(job.id)} onToggle={bulk.toggle} />
            ))}</div>
          </>}
    </div>
  );
}

function QueueJobCard({ job, rank, onSelect, onMarkApplied, onRemove, selected, onToggle }: any) {
  return (
    <div className={`bg-gray-800 rounded-xl border overflow-hidden ${selected ? "border-indigo-500" : "border-gray-700"}`}>
      <div className="flex items-start gap-3 p-3.5 hover:bg-gray-700/30 cursor-pointer" onClick={() => onSelect(job)}>
        {onToggle && (
          <input type="checkbox" checked={!!selected} aria-label={`Select ${job.title}`}
                 onClick={(e) => e.stopPropagation()} onChange={() => onToggle(job.id)}
                 className="mt-2 w-4 h-4 shrink-0 accent-indigo-500 cursor-pointer" />
        )}
        {/* The rank is the queue's whole point: these are ordered by effective
            score, and a grid of equal cards hid that they were ordered at all. */}
        <div className="w-8 h-8 rounded-lg bg-gray-900/70 border border-gray-700 flex items-center justify-center shrink-0 mt-0.5">
          <span className="text-xs font-bold text-gray-400">{rank}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h4 className="font-semibold text-white truncate">{job.title}</h4>
            <span className="text-sm text-gray-400 truncate">{job.company}</span>
            {job.location && <span className="text-xs text-gray-400 truncate">{job.location}</span>}
          </div>
          <div className="flex items-center gap-2 mt-1 flex-wrap"><RouteBadge job={job} />{!(["board", "account"].includes(job.applyRoute) && (!job.applyStatus || job.applyStatus === "queued")) && <ApplyBadge job={job} />}<FeedbackBadge job={job} />{job.applyRoute !== "board" && <LinkBadge job={job} />}</div>
          {/* Written by the scorer and shown on the swipe card, then dropped the
              moment a job reached the queue - which is where the decision to
              actually apply gets made. */}
          {job.whyFits && (
            <p className="mt-2 text-sm text-gray-300 leading-relaxed border-l-2 border-amber-700/60 pl-3">
              <span className="text-amber-400 font-medium">Why this fits: </span>{job.whyFits}
            </p>
          )}
        </div>
        <div className="text-right shrink-0 pl-2">
          <div className="text-base font-bold text-indigo-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div>
          <div className="text-[10px] text-gray-400">match</div>
        </div>
      </div>
      <div className="flex border-t border-gray-700 divide-x divide-gray-700">
        <button onClick={() => onMarkApplied(job)} className="flex-1 py-2.5 text-xs font-medium text-green-300 active:bg-gray-700 flex items-center justify-center gap-1"><CheckCircle className="w-4 h-4" />Mark applied</button>
        {job.url && <a href={job.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} className="flex-1 py-2.5 text-xs font-medium text-indigo-300 active:bg-gray-700 flex items-center justify-center gap-1"><ExternalLink className="w-4 h-4" />{openLabel(job)}</a>}
        <button onClick={() => onRemove(job)} className="flex-1 py-2.5 text-xs font-medium text-red-300 active:bg-gray-700 flex items-center justify-center gap-1"><X className="w-4 h-4" />Remove</button>
      </div>
    </div>
  );
}

function StagePicker({ job, onChanged }: { job: UiJob; onChanged: (id: number, stage: string) => void }) {
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const pick = async (stage: string) => {
    if (busy) return;
    const next = job.stage === stage ? "" : stage;   /* clicking the current one clears it */
    if (!next) return;                               /* the API has no "unset", so this is a no-op rather than a lie */
    setBusy(stage); setErr("");
    try {
      const r = await api.setStage(job.id, stage);
      if (r?.error) { setErr(r.error); return; }
      onChanged(job.id, stage);
    } catch (e: any) { setErr(e?.message || "Could not save"); }
    finally { setBusy(""); }
  };
  return (
    <div className="mt-2">
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[11px] text-gray-400 mr-0.5">Stage</span>
        {STAGES.map(([id, label, active]) => (
          <button key={id} onClick={(e) => { e.stopPropagation(); pick(id); }} disabled={!!busy}
            aria-pressed={job.stage === id}
            className={`px-2.5 py-1 rounded-lg text-[11px] font-medium border transition-colors disabled:opacity-60 ${
              job.stage === id ? active : "bg-gray-900/50 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
            {busy === id ? "…" : label}
          </button>
        ))}
      </div>
      {err && <p className="text-[11px] text-red-300 mt-1">{err}</p>}
    </div>
  );
}

/* Selection lives in the tab, not in the row: a row that owned its own checkbox
   would have to be told to clear it after every bulk action. */
function BulkBar({ count, total, onAll, onClear, actions, busy, msg }: any) {
  return (
    <div className="flex items-center gap-2 flex-wrap bg-gray-900/60 border border-gray-700 rounded-xl px-3 py-2 mb-2">
      <span className="text-xs text-gray-300 font-medium">{count} selected</span>
      <button onClick={onAll} className="text-xs text-indigo-300 hover:text-indigo-200">
        {count === total ? "Select none" : `Select all ${total}`}
      </button>
      <div className="flex-1" />
      {actions.map(([label, fn, tone]: any) => (
        <button key={label} onClick={fn} disabled={busy || !count}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium disabled:opacity-40 ${tone}`}>
          {label}
        </button>
      ))}
      <button onClick={onClear} disabled={!count} className="text-xs text-gray-400 hover:text-gray-200 disabled:opacity-40">Clear</button>
      {msg && <span className="text-xs text-gray-400 w-full">{msg}</span>}
    </div>
  );
}

/* Shared selection state for a list that supports bulk actions. */
function useBulk(jobs: UiJob[], onReload?: () => void) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const ids = jobs.map((j) => j.id);
  const toggle = (id: number) => setSel((cur) => {
    const next = new Set(cur);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });
  const all = () => setSel(sel.size === ids.length ? new Set() : new Set(ids));
  const clear = () => setSel(new Set());
  const only = (pick: number[]) => setSel(new Set(pick));
  const run = (action: "approve" | "reject", verb: string) => async () => {
    const picked = ids.filter((i) => sel.has(i));      /* order and membership from the CURRENT list */
    if (!picked.length) return;
    setBusy(true); setMsg("");
    try {
      const r = await api.bulk(action, picked);
      setMsg(`${verb} ${r?.updated ?? picked.length} job${(r?.updated ?? picked.length) === 1 ? "" : "s"}`);
      clear();
      if (onReload) await onReload();
    } catch (e: any) { setMsg(e?.message || "Bulk action failed"); }
    finally { setBusy(false); }
  };
  return { sel, toggle, all, clear, only, busy, msg, run, count: sel.size, total: ids.length };
}

/* One row shape for every list on the dashboard. The queue was already a
   ranked list while Applied and Deferred were grids of equal cards, which read
   as two different apps and hid that these lists are ordered at all. */
function RankedRow({ job, rank, onSelect, right, children, selectable, selected, onToggle }: any) {
  return (
    <div className={`bg-gray-800 rounded-xl border overflow-hidden ${selected ? "border-indigo-500" : "border-gray-700"}`}>
      <div className="flex items-start gap-3 p-3.5 hover:bg-gray-700/30 cursor-pointer" onClick={() => onSelect && onSelect(job)}>
        {selectable && (
          <input type="checkbox" checked={!!selected} aria-label={`Select ${job.title}`}
                 onClick={(e) => e.stopPropagation()} onChange={() => onToggle(job.id)}
                 className="mt-2 w-4 h-4 shrink-0 accent-indigo-500 cursor-pointer" />
        )}
        <div className="w-8 h-8 rounded-lg bg-gray-900/70 border border-gray-700 flex items-center justify-center shrink-0 mt-0.5">
          <span className="text-xs font-bold text-gray-400">{rank}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h4 className="font-semibold text-white truncate">{job.title}</h4>
            <span className="text-sm text-gray-400 truncate">{job.company}</span>
            {job.location && <span className="text-xs text-gray-400 truncate">{job.location.split(",")[0]}</span>}
          </div>
          {children}
        </div>
        <div className="flex items-center gap-3 shrink-0 pl-2">
          <div className="text-right">
            <div className="text-base font-bold text-indigo-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div>
            <div className="text-[10px] text-gray-400">match</div>
          </div>
          {right}
        </div>
      </div>
    </div>
  );
}

function ListTab({ jobs, onSelectJob, showStatus, emptyIcon: EI, emptyTitle, emptySub, heading, stages, onReload }: any) {
  /* The stage lives here rather than being re-fetched: /api/set-stage returns
     only {ok}, and a full reload to move one label would throw away the user's
     scroll position for no new information. */
  const [local, setLocal] = useState<Record<number, string>>({});
  const [filter, setFilter] = useState<string>("");
  /* "What are those 84?" was the question the breakdown could not answer,
     because it gave a number with no way to reach the rows behind it. */
  const [origin, setOrigin] = useState<string>("");
  const [restored, setRestored] = useState<Set<number>>(new Set());
  const [restoreMsg, setRestoreMsg] = useState("");
  const stageOf = (j: UiJob) => local[j.id] ?? j.stage;
  const counts: Record<string, number> = {};
  for (const j of jobs) { const st = stageOf(j) || "none"; counts[st] = (counts[st] || 0) + 1; }
  const origins: Record<string, number> = {};
  for (const j of jobs) { const o = j.appliedVia || "unknown"; origins[o] = (origins[o] || 0) + 1; }
  const shown = jobs
    .filter((j: UiJob) => !filter || stageOf(j) === filter)
    .filter((j: UiJob) => !origin || (j.appliedVia || "unknown") === origin)
    .filter((j: UiJob) => !restored.has(j.id));

  const [bulkBusy, setBulkBusy] = useState(false);
  const restoreAllBulk = async () => {
    const n = origins["bulk"] || 0;
    if (!confirm(`Put all ${n} bulk-marked jobs back in your review queue?\n\nThey were never applied to. They will reappear in your swipe queue to decide on properly.`)) return;
    setBulkBusy(true); setRestoreMsg("");
    try {
      const r = await api.restoreBulkMarked();
      setRestoreMsg(`Moved ${r?.restored ?? n} job${(r?.restored ?? n) === 1 ? "" : "s"} back to review \u2014 open Swipe to go through them.`);
      if (onReload) await onReload();
    } catch (e: any) { setRestoreMsg(e?.message || "Could not move those jobs"); }
    finally { setBulkBusy(false); }
  };

  const sendBackToReview = async (job: UiJob) => {
    if (!confirm(`Put "${job.title}" at ${job.company} back in your review queue?\n\nIt was marked applied by a bulk cleanup, not by an actual application.`)) return;
    setRestoreMsg("");
    try {
      await api.restore(job.id);
      setRestored((c) => new Set(c).add(job.id));
      setRestoreMsg("Moved back to review. It will appear in your swipe queue.");
    } catch (e: any) { setRestoreMsg(e?.message || "Could not move that job"); }
  };

  if (!jobs.length) return <EmptyTab icon={EI} title={emptyTitle} sub={emptySub} />;
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-white mb-1">{heading}</h3>
      {stages && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={() => setFilter("")}
            className={`px-2.5 py-1 rounded-lg text-xs font-medium border ${filter === "" ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-900/50 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
            All {jobs.length}
          </button>
          {STAGES.map(([id, label]) => (
            <button key={id} onClick={() => setFilter(filter === id ? "" : id)}
              className={`px-2.5 py-1 rounded-lg text-xs font-medium border ${filter === id ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-900/50 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
              {label} {counts[id] || 0}
            </button>
          ))}
          {(counts["none"] || 0) > 0 && (
            <span className="text-xs text-gray-400 ml-1">{counts["none"]} not tracked yet</span>
          )}
        </div>
      )}
      {stages && (origins["bulk"] || origins["no_url"] || origins["unknown"]) ? (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] text-gray-400 mr-0.5">Origin</span>
          {([["", "All", jobs.length],
             ["engine", "Sent by Job Hunter", origins["engine"] || 0],
             ["manual", "You marked applied", origins["manual"] || 0],
             ["bulk", "Bulk-marked, never applied", origins["bulk"] || 0],
             ["no_url", "No link", origins["no_url"] || 0],
             ["unknown", "Unknown", origins["unknown"] || 0]] as [string, string, number][])
            .filter(([id, , c]) => id === "" || c > 0)
            .map(([id, label, c]) => (
              <button key={id || "all"} onClick={() => setOrigin(origin === id ? "" : id)}
                className={`px-2.5 py-1 rounded-lg text-xs font-medium border ${
                  origin === id ? "bg-indigo-600 border-indigo-500 text-white"
                                : "bg-gray-900/50 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
                {label} {c}
              </button>
            ))}
        </div>
      ) : null}
      {origin === "bulk" && (
        <div className="bg-amber-950/30 border border-amber-700/50 rounded-xl p-3.5 space-y-2">
          <p className="text-xs text-amber-200/90">
            These {origins["bulk"]} were never reviewed and never applied to — a one-off cleanup marked
            them applied to empty the queue. They are real jobs you never got to decide on.
          </p>
          <button onClick={restoreAllBulk} disabled={!!bulkBusy}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-60">
            {bulkBusy ? "Moving\u2026" : `Send all ${origins["bulk"]} back to review`}
          </button>
        </div>
      )}
      {restoreMsg && <p className="text-xs text-indigo-300">{restoreMsg}</p>}
      {!shown.length && <p className="text-sm text-gray-400 py-6 text-center">Nothing at this stage yet.</p>}
      <div className="space-y-2">
        {shown.map((job: UiJob, i: number) => (
          <RankedRow key={job.id} job={{ ...job, stage: stageOf(job) }} rank={i + 1} onSelect={onSelectJob}
                     right={showStatus && job.applyStatus ? <StatusPill s={job.applyStatus} /> : null}>
            <div className="flex items-center gap-3 mt-1 flex-wrap">
              {job.timeAgo && <span className="text-xs text-gray-400 flex items-center gap-1"><Clock className="w-3 h-3" />{job.timeAgo}</span>}
              <FeedbackBadge job={job} /><LinkBadge job={job} />
            </div>
            {job.whyFits && (
              <p className="mt-2 text-sm text-gray-300 leading-relaxed border-l-2 border-amber-700/60 pl-3">
                <span className="text-amber-400 font-medium">Why this fits: </span>{job.whyFits}
              </p>
            )}
            {stages && (job.appliedVia === "bulk" || job.appliedVia === "no_url") ? (
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <span className="text-[11px] text-amber-300/90">
                  {job.appliedVia === "bulk" ? "Bulk-marked — no application was sent" : "Recorded as sent with no link"}
                </span>
                <button onClick={(e) => { e.stopPropagation(); sendBackToReview(job); }}
                        className="px-2.5 py-1 rounded-lg text-[11px] font-medium border border-indigo-500/50 text-indigo-200 hover:bg-indigo-500/10">
                  Send back to review
                </button>
              </div>
            ) : stages ? (
              <StagePicker job={{ ...job, stage: stageOf(job) }}
                           onChanged={(id: number, st: string) => setLocal((c) => ({ ...c, [id]: st }))} />
            ) : null}
          </RankedRow>
        ))}
      </div>
    </div>
  );
}

function DeferredTab({ jobs, onSelectJob, onUnDefer, onReload }: any) {
  const b = useBulk(jobs, onReload);
  if (!jobs.length) return <EmptyTab icon={Clock} title="No deferred jobs" sub="Jobs you tap 'Decide Later' on appear here" />;
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-white mb-1">{jobs.length} deferred</h3>
      {/* Deferred is where a backlog piles up, which is exactly where deciding
          one job at a time stops being reasonable. */}
      <BulkBar count={b.count} total={b.total} onAll={b.all} onClear={b.clear} busy={b.busy} msg={b.msg}
               actions={[["Approve", b.run("approve", "Approved"), "bg-emerald-600/80 hover:bg-emerald-600 text-white"],
                         ["Pass", b.run("reject", "Passed on"), "bg-red-600/80 hover:bg-red-600 text-white"]]} />
      <div className="space-y-2">
      {jobs.map((job: UiJob, i: number) => (
        <RankedRow key={job.id} job={job} rank={i + 1} onSelect={onSelectJob}
                   selectable selected={b.sel.has(job.id)} onToggle={b.toggle}
                   right={<button onClick={(e: any) => { e.stopPropagation(); onUnDefer(job); }} className="px-3 py-2 bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 rounded-lg text-xs font-medium shrink-0">Move to review</button>}>
          <div className="flex items-center gap-3 mt-1 flex-wrap">
            {job.timeAgo && <span className="text-xs text-gray-400 flex items-center gap-1"><Clock className="w-3 h-3" />{job.timeAgo}</span>}
            <FeedbackBadge job={job} /><LinkBadge job={job} />
          </div>
          {job.whyFits && (
            <p className="mt-2 text-sm text-gray-300 leading-relaxed border-l-2 border-amber-700/60 pl-3">
              <span className="text-amber-400 font-medium">Why this fits: </span>{job.whyFits}
            </p>
          )}
        </RankedRow>
      ))}
      </div>
    </div>
  );
}

function EmptyTab({ icon: Icon, title, sub }: any) {
  return (<div className="text-center py-12"><Icon className="w-14 h-14 text-gray-600 mx-auto mb-3" /><p className="text-gray-400">{title}</p><p className="text-sm text-gray-400 mt-1">{sub}</p></div>);
}

function PassedTab() {
  const [data, setData] = useState<any>(null);
  const [block, setBlock] = useState("");
  const load = () => api.learned().then(setData).catch(() => setData({ pass_reasons: [], blocklist: [], patterns: [] }));
  useEffect(() => { load(); }, []);
  if (data === null) return <div className="py-10 text-center"><Loader2 className="w-6 h-6 text-indigo-400 animate-spin mx-auto" /></div>;
  const forget = async (id: number) => { await api.forgetPattern(id).catch(() => {}); load(); };
  const unblock = async (c: string) => { await api.unblockCompany(c).catch(() => {}); load(); };
  const addBlock = async () => { const c = block.trim(); if (!c) return; setBlock(""); await api.blockCompany(c).catch(() => {}); load(); };
  const totalPass = data.pass_reasons.reduce((a: number, r: any) => a + r.count, 0);

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-white mb-3">Why you pass{totalPass ? ` (${totalPass})` : ""}</h3>
        {data.pass_reasons.length ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {data.pass_reasons.map((r: any) => (
              <div key={r.reason} className="bg-gray-800 rounded-xl p-4 border border-gray-700 flex items-center justify-between">
                <span className="text-sm text-gray-300">{r.reason}</span>
                <span className="text-lg font-bold text-indigo-400">{r.count}</span>
              </div>
            ))}
          </div>
        ) : <p className="text-sm text-gray-400">No pass reasons recorded yet.</p>}
      </div>

      <div>
        <h3 className="text-base font-semibold text-white mb-1 flex items-center gap-2"><Ban className="w-4 h-4 text-red-400" />Blocked companies</h3>
        <p className="text-xs text-gray-400 mb-3">These are skipped automatically in future searches.</p>
        <div className="flex flex-wrap gap-2 mb-2">
          {data.blocklist.length ? data.blocklist.map((c: string) => (
            <span key={c} className="inline-flex items-center gap-1.5 pl-3 pr-2 py-1.5 bg-red-600/15 border border-red-600/40 text-red-200 rounded-full text-sm">
              {c}<button onClick={() => unblock(c)} className="w-4 h-4 flex items-center justify-center rounded-full bg-red-500/40 active:bg-red-500"><X className="w-3 h-3" /></button>
            </span>
          )) : <span className="text-xs text-gray-400">No blocked companies.</span>}
        </div>
        <div className="flex gap-2">
          <input value={block} onChange={(e) => setBlock(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addBlock(); } }} placeholder="Block a company…" className="flex-1 px-4 py-2.5 border border-gray-700 rounded-xl bg-gray-800 text-white text-sm" />
          <button onClick={addBlock} className="px-3.5 bg-red-600/80 active:bg-red-600 text-white rounded-xl text-sm font-medium">Block</button>
        </div>
      </div>

      <div>
        <h3 className="text-base font-semibold text-white mb-3">Learned passes{data.patterns.length ? ` (${data.patterns.length})` : ""}</h3>
        {data.patterns.length ? (
          <div className="space-y-2">
            {data.patterns.map((pat: any) => (
              <div key={pat.id} className="flex items-center gap-3 p-3 bg-gray-800 rounded-xl border border-gray-700">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-white truncate">{pat.title || "(any role)"}</p>
                  <p className="text-xs text-gray-400 truncate">{pat.company}{pat.notes ? ` \u2014 ${pat.notes}` : ""}</p>
                </div>
                <button onClick={() => forget(pat.id)} className="px-3 py-1.5 bg-gray-700 active:bg-gray-600 text-indigo-200 rounded-lg text-xs font-medium shrink-0">Forget</button>
              </div>
            ))}
          </div>
        ) : <p className="text-sm text-gray-400">Nothing learned yet. Jobs you pass will train your future matches.</p>}
      </div>
    </div>
  );
}

function ActivityTab() {
  const [items, setItems] = useState<Activity[] | null>(null);
  useEffect(() => { api.activity().then(setItems).catch(() => setItems([])); }, []);
  if (items === null) return <div className="py-10 text-center"><Loader2 className="w-6 h-6 text-indigo-400 animate-spin mx-auto" /></div>;
  if (!items.length) return <EmptyTab icon={List} title="No activity yet" sub="Searches and applications appear here" />;
  return (
    <div className="space-y-3">
      {items.map((a) => { const ev = (a.event_type || "").toLowerCase(); const isSearch = ev.includes("search"); const isFail = ev.includes("fail") || ev.includes("reject"); return (
        <div key={a.id} className="flex items-start gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700">
          <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 ${isSearch ? "bg-indigo-900/40" : isFail ? "bg-red-900/40" : "bg-green-900/40"}`}>{isSearch ? <SearchIcon className="w-4 h-4 text-indigo-400" /> : isFail ? <XCircle className="w-4 h-4 text-red-400" /> : <CheckCircle className="w-4 h-4 text-green-400" />}</div>
          <div className="flex-1 min-w-0"><h4 className="font-semibold text-white text-sm capitalize">{(a.event_type || "event").replace(/_/g, " ")}</h4><p className="text-sm text-gray-400 break-words">{a.details}</p></div>
        </div>); })}
    </div>
  );
}

/* Every number here used to be computed from two columns that each meant
   several different things at once.

   "Applied" counted status='applied', which was written by the apply engine
   really submitting a form, by the user pressing "Mark applied", by a bulk
   cleanup that marked the whole queue applied without applying to any of it,
   and by a job with no URL being recorded as "submitted". "Passed" counted
   status='rejected', which was the user's own decision AND the system retiring
   dead links, expired postings and already-attempted jobs.

   So "136 applied of 137 approved - 99%" was not a high apply rate. It was a
   denominator built from the same rows as the numerator (approved + applied),
   which climbs toward 100% whenever the queue empties, over a numerator that
   mostly was not an application. Migration 9 records provenance; this screen
   stops averaging the categories together.

   The honest counts are shown even when they are small. A metric that flatters
   is worse than no metric: this one is meant to answer "is the robot working",
   and it can only do that if it is allowed to say no. */
function AnalyticsTab({ approvedCount, rejectedCount, deferredCount, appliedCount, totalSuggested, stats }: any) {
  const n = (k: string) => (typeof stats?.[k] === "number" ? stats[k] : 0);
  const hasBreakdown = stats && typeof stats.applied_engine === "number";

  // A real application is one the engine submitted, or one he told us he sent.
  // Nothing else counts, whatever its status says.
  const reallyApplied = n("applied_engine") + n("applied_manual");
  const notApplied    = n("applied_bulk") + n("applied_no_url");
  const appliedUnknown = n("applied_unknown");

  /* Eran's model, 2026-09-15: a job the system threw away was never a real
     candidate, so it is excluded from EVERY number here - including "found".
     Counting it made the sourcing look more productive than it was and the
     approval rate look worse than it was, in the same breath.

     "Filtered out" is the system's own rejections PLUS the jobs that aged out
     of the queue after three days. Those were counted in `total` and in no
     bucket at all, which is why the dashboard could show 33 jobs that existed
     nowhere on screen. */
  const filtered  = n("passed_by_system") + n("expired");
  const archived  = n("rejected_archived");
  /* Archived passes count as HIS. The rows were deleted and the origin is
     unknowable, but almost every pass is his, and excluding them understated
     the denominator by more than including them overstates it. */
  const yourPasses = n("passed_by_user") + n("passed_unknown") + archived;
  /* "Already applied elsewhere" (Eran, 2026-11-05): he approved the job and then
     applied on his own. It is not a pass, so it counts as a yes in the approval
     rate - but the app did not apply, so it stays OUT of "Actually applied",
     numerator and denominator both. */
  const elsewhere = n("applied_elsewhere");
  /* Decided = you made a call on it, which INCLUDES everything sitting in the
     queue: approving a job is a decision, whether or not it has been applied to
     yet. Only "still in the swipe queue" and "deferred" are undecided, and
     those sit outside the rate rather than dragging it down. */
  const decided   = hasBreakdown
    ? approvedCount + appliedCount + elsewhere + yourPasses
    : approvedCount + rejectedCount;
  const reviewed  = decided;

  const approvedAllTime = approvedCount + appliedCount;
  const pct = (a: number, b: number) => (b > 0 ? Math.round((a / b) * 100) : 0);
  const saidYes = approvedAllTime + elsewhere;
  const approvalRate = pct(saidYes, decided);
  const applyRate    = pct(reallyApplied, approvedAllTime);

  /* Every figure on this screen, and how they meet.
     "Passed 435" and "137 approved of 337 you reviewed" are both true and are
     built from DIFFERENT SETS, which is why subtracting one from the other
     gave nonsense: the archived passes sit inside Passed and outside reviewed,
     and the still-new jobs sit inside Found and inside neither. Nothing said
     so, so the only honest fix is to show the whole sum rather than two
     summaries of it. */
  const stillNew  = n("new");
  /* "Found" means found FOR YOU: everything the system discarded is removed
     from the top line, not just from the rate. */
  const foundRaw  = totalSuggested || 0;
  const found     = foundRaw - filtered;
  const parts     = saidYes + yourPasses + stillNew + deferredCount;
  const reconciles = found === parts;

  const Row = ({ label, value, sub, tone }: any) => (
    <div className="flex items-start justify-between gap-3 py-2.5 border-b border-gray-700/60 last:border-0">
      <div className="min-w-0">
        <p className={`text-sm ${tone || "text-gray-300"}`}>{label}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
      <span className={`text-sm font-semibold shrink-0 ${tone || "text-white"}`}>{value}</span>
    </div>
  );

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-2xl p-5 border border-blue-800/50">
          <h4 className="text-sm font-medium text-gray-400 mb-1">Approval rate</h4>
          <div className="text-3xl font-bold text-blue-400 mb-1">{approvalRate}%</div>
          <p className="text-sm text-gray-400">{saidYes} approved of {decided} you decided on</p>
          {filtered > 0 && <p className="text-xs text-gray-400 mt-1">{filtered} more never reached you and are not counted</p>}
        </div>
        <div className="bg-gradient-to-br from-green-900/20 to-emerald-900/20 rounded-2xl p-5 border border-green-800/50">
          <h4 className="text-sm font-medium text-gray-400 mb-1">Actually applied</h4>
          <div className="text-3xl font-bold text-green-400 mb-1">{applyRate}%</div>
          <p className="text-sm text-gray-400">{reallyApplied} applied of {approvedAllTime} approved</p>
          {notApplied > 0 && <p className="text-xs text-amber-400/90 mt-1">{notApplied} more are marked applied but never were</p>}
          {elsewhere > 0 && <p className="text-xs text-gray-400 mt-1">{elsewhere} you applied to elsewhere are not counted here</p>}
        </div>
      </div>

      {hasBreakdown && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h4 className="font-semibold text-white mb-1">Where every job went</h4>
          <p className="text-xs text-gray-400 mb-3">
            The two percentages above are measured over different sets, which is why they cannot be
            subtracted from each other. This is the whole sum.
          </p>
          <div className="font-mono text-sm space-y-1">
            <div className="flex justify-between gap-3 text-gray-200">
              <span>Jobs that reached you</span><span className="font-semibold">{found}</span>
            </div>
            <div className="flex justify-between gap-3 text-green-300 pl-4 pt-1"><span>approved</span><span>{approvedAllTime}</span></div>
            {elsewhere > 0 && <div className="flex justify-between gap-3 text-green-300 pl-4"><span>approved, then applied elsewhere</span><span>{elsewhere}</span></div>}
            <div className="flex justify-between gap-3 text-red-300 pl-4"><span>you passed</span><span>{yourPasses}</span></div>
            <div className="flex justify-between gap-3 text-amber-300 pl-4"><span>deferred</span><span>{deferredCount}</span></div>
            <div className="flex justify-between gap-3 text-gray-400 pl-4"><span>still in your swipe queue</span><span>{stillNew}</span></div>
            <div className={`flex justify-between gap-3 border-t border-gray-700 pt-1 mt-1 ${reconciles ? "text-white" : "text-amber-300"}`}>
              <span>{reconciles ? "= same number" : "= does NOT match \u2014 a job is in two places or none"}</span>
              <span className="font-semibold">{parts}</span>
            </div>
          </div>
          <p className="text-xs text-gray-400 mt-3">
            Approval rate = {saidYes} approved ÷ ({saidYes} approved + {yourPasses} passed) = <span className="text-blue-300 font-medium">{approvalRate}%</span>.
            {" "}Approved counts everything you said yes to, queued or already applied to — approving is the decision.
            {deferredCount + stillNew > 0 ? ` The ${stillNew} still in your swipe queue and ${deferredCount} deferred are not decisions yet, so they are outside the rate.` : ""}
          </p>
          {filtered > 0 && (
            <p className="text-xs text-gray-400 mt-2">
              A further <span className="text-gray-300">{filtered}</span> were discarded by the app before
              they reached you — dead links and jobs already attempted. They are not counted anywhere above,
              including in the total: they were never real candidates. (Jobs are no longer discarded for
              age — that rule is gone.)
            </p>
          )}
          {archived > 0 && (
            <p className="text-xs text-gray-400 mt-2">
              {archived} of your passes are older than 30 days and their rows have been deleted to save
              space, so only the count survives.
            </p>
          )}
        </div>
      )}

      {hasBreakdown && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h4 className="font-semibold text-white mb-1">What &ldquo;applied&rdquo; is made of</h4>
          <p className="text-xs text-gray-400 mb-2">{appliedCount} jobs carry the applied status. They did not all get an application.</p>
          <Row label="Submitted by Job Hunter" sub="The apply engine filled and sent a form" value={n("applied_engine")} tone="text-green-300" />
          <Row label="You marked it applied" sub="You applied yourself and told the app" value={n("applied_manual")} tone="text-green-300" />
          <Row label="Bulk-marked, not applied" sub="A one-off cleanup emptied the queue by marking it applied — these were never reviewed. Find them under Applied → Origin." value={n("applied_bulk")} tone="text-amber-300" />
          <Row label="No link to apply to" sub="Recorded as submitted with no URL" value={n("applied_no_url")} tone="text-amber-300" />
          {appliedUnknown > 0 && <Row label="Origin unknown" sub="Recorded before the app tracked this" value={appliedUnknown} tone="text-gray-400" />}
        </div>
      )}

      {hasBreakdown && (
        <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
          <h4 className="font-semibold text-white mb-1">What &ldquo;passed&rdquo; is made of</h4>
          <p className="text-xs text-gray-400 mb-2">Only the first line is a decision you made.</p>
          <Row label="You passed" sub="Swiped left, with or without a reason" value={n("passed_by_user")} tone="text-red-300" />
          {elsewhere > 0 && <Row label="Not a pass: applied elsewhere" sub="Removed from the queue because you applied on your own. Counted as approved, never as a pass, and the company is never demoted." value={elsewhere} tone="text-green-300" />}
          <Row label="Filtered out automatically" sub="Dead link, expired, gone, or already attempted" value={filtered} tone="text-gray-400" />
          {n("passed_unknown") > 0 && <Row label="Origin unknown" sub="Recorded before the app tracked this" value={n("passed_unknown")} tone="text-gray-400" />}
          {archived > 0 && <Row label="Archived after 30 days" sub="Rows deleted to save space; origin not recoverable" value={archived} tone="text-gray-400" />}
        </div>
      )}

      <div className="bg-gray-800 rounded-xl p-5 border border-gray-700">
        <h4 className="font-semibold text-white mb-4">Your decisions</h4>
        <div className="space-y-3">
          {[["Approved", saidYes, "bg-green-500"],
            ["Passed", hasBreakdown ? yourPasses : rejectedCount, "bg-red-500"],
            ["Deferred", deferredCount, "bg-amber-500"]].map(([label, v, color]: any) => (
            <div key={label}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm text-gray-400">{label}</span>
                <span className="text-sm font-medium text-white">{v}</span>
              </div>
              <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
                <div className={`h-full ${color}`} style={{ width: `${decided ? (v / decided) * 100 : 0}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-gradient-to-br from-indigo-900/20 to-slate-800/40 rounded-2xl p-5 border border-indigo-800/40">
        <h4 className="font-semibold text-white mb-3 flex items-center gap-2"><Zap className="w-5 h-5 text-indigo-400" />Insights</h4>
        <ul className="space-y-2 text-sm text-gray-300">
          <li className="flex items-start gap-2"><Target className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" /><span>You've decided on {decided} job{decided !== 1 ? "s" : ""} of the {found} that reached you.</span></li>
          <li className="flex items-start gap-2"><Target className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" /><span>{approvalRate}% approval rate — {approvalRate > 50 ? "you're casting a wide net" : "you're being selective"}.</span></li>
          {filtered > 0 && <li className="flex items-start gap-2"><Target className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" /><span>{pct(filtered, foundRaw)}% of what the sources returned never reached you — {filtered} of {foundRaw}.</span></li>}
        </ul>
      </div>
    </div>
  );
}

function JobCard({ job, onSelect, showStatus }: any) {
  return (
    <div className="flex items-center gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700 active:border-indigo-500 cursor-pointer" onClick={() => onSelect(job)}>
      <div className="w-12 h-12 bg-gradient-to-br from-indigo-900/40 to-slate-800 rounded-xl flex items-center justify-center shrink-0"><Building2 className="w-6 h-6 text-indigo-400" /></div>
      <div className="flex-1 min-w-0"><h4 className="font-semibold text-white truncate">{job.title}</h4><p className="text-sm text-gray-400 truncate">{job.company}</p><div className="flex items-center gap-3 mt-0.5">{job.location && <div className="flex items-center gap-1 text-xs text-gray-400"><MapPin className="w-3 h-3" /><span>{job.location.split(",")[0]}</span></div>}{job.timeAgo && <div className="flex items-center gap-1 text-xs text-gray-400"><Clock className="w-3 h-3" /><span>{job.timeAgo}</span></div>}</div></div>
      <div className="flex items-center gap-2 shrink-0"><div className="text-right"><div className="text-base font-bold text-indigo-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div><div className="text-[10px] text-gray-400">match</div></div>{showStatus && job.applyStatus && <StatusPill s={job.applyStatus} />}</div>
    </div>
  );
}

function StatusPill({ s }: { s: string }) {
  if (s === "submitted" || s === "applied") return <span className="px-2.5 py-1 bg-blue-900/40 text-blue-400 text-xs font-medium rounded-full">Submitted</span>;
  if (s === "confirmed") return <span className="px-2.5 py-1 bg-green-900/40 text-green-400 text-xs font-medium rounded-full flex items-center gap-1"><CheckCircle className="w-3 h-3" />Confirmed</span>;
  if (s === "failed") return <span className="px-2.5 py-1 bg-red-900/40 text-red-400 text-xs font-medium rounded-full flex items-center gap-1"><XCircle className="w-3 h-3" />Failed</span>;
  return <span className="px-2.5 py-1 bg-gray-700 text-gray-300 text-xs font-medium rounded-full">{s}</span>;
}

/* Step 14: the user's CV rewritten for this job. The server checks the
   model's version against the original CV and removes any line with a number,
   employer or skill the CV doesn't have; what it removed is shown here. */
/** "8 Nov, 16:52" in the viewer's time, from the server's UTC 'YYYY-MM-DD HH:MM:SS'. */
function fmtWhen(s: string): string {
  const d = new Date((s || "").replace(" ", "T") + "Z");
  return isNaN(d.getTime()) ? s : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jerusalem" });
}

function TailoredCv({ job, me, isAdmin }: { job: UiJob; me?: any; isAdmin?: boolean }) {
  const lim = planLimit(me, "tailored_cv");
  const use = me?.usage?.tailored_cv;
  const allowed = isAdmin || lim === undefined || lim === null || lim > 0;
  const [res, setRes] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(""); const [msg, setMsg] = useState("");
  const [draft, setDraft] = useState<Record<string, any> | null>(null);   // edit mode when set
  useEffect(() => { api.tailoredCv(job.id).then(setRes).catch(() => {}); }, [job.id]);
  const act = async (kind: string, fn: () => Promise<any>, ok = "") => {
    setBusy(kind); setMsg("");
    try { const r = await fn(); setRes(r); if (ok) setMsg(ok); return true; }
    catch (e: any) { setMsg(e?.message || "That did not work"); return false; }
    finally { setBusy(""); }
  };
  const lines = (v: any) => (Array.isArray(v) ? v : []).join("\n");
  const unlines = (t: string) => t.split("\n").map((x) => x.trim()).filter(Boolean);
  const startEdit = () => {
    const c = res!.cv;
    setDraft({ headline: c.headline || "", summary: c.summary || "", skills: (c.skills || []).join(", "),
      experience: (c.experience || []).map((r: any) => ({ ...r, bullets: lines(r.bullets) })),
      education: lines(c.education), other: lines(c.other), changes: c.changes || [] });
  };
  const saveEdit = async () => {
    const d = draft!;
    const cv = { headline: d.headline, summary: d.summary, changes: d.changes,
      skills: d.skills.split(",").map((x: string) => x.trim()).filter(Boolean),
      experience: d.experience.map((r: any) => ({ title: r.title, company: r.company, dates: r.dates, bullets: unlines(r.bullets) })),
      education: unlines(d.education), other: unlines(d.other) };
    if (await act("save", () => api.saveTailoredCv(job.id, cv), "Saved")) setDraft(null);
  };
  const setRole = (i: number, k: string, v: string) =>
    setDraft((d) => ({ ...d!, experience: d!.experience.map((r: any, j: number) => (j === i ? { ...r, [k]: v } : r)) }));
  if (!allowed && !res) return null;
  const cv = res?.cv;
  const inp = "w-full px-2.5 py-1.5 border border-gray-600 rounded-lg bg-gray-900 text-white text-sm";
  return (
    <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
      <h4 className="font-semibold text-white text-sm mb-1">Tailored CV</h4>
      <p className="text-xs text-gray-400 mb-2">Your CV, re-ordered and re-worded for this job. Nothing is added: any line with a fact that isn't in your CV is removed and listed below. Review it, edit anything, then choose which CV goes with this application.
        {use && use.limit !== null && <> {Math.max(0, use.limit - use.used)} of {use.limit} left this month.</>}</p>
      {!draft && (
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => act("gen", () => api.tailorCv(job.id))} disabled={!!busy || !allowed}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-60">
            {busy === "gen" ? "Tailoring… (about 30 s)" : cv ? "Tailor again" : "Tailor my CV for this job"}
          </button>
          {cv && <button onClick={startEdit} disabled={!!busy} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-60">Edit</button>}
          {cv && <a href={`/api/jobs/${job.id}/tailored-cv.docx`} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 inline-flex items-center gap-1"><Download className="w-3.5 h-3.5" />Download (Word)</a>}
        </div>
      )}
      {res?.created_at && !draft && <p className="text-[11px] text-gray-400 mt-2">Tailored {fmtWhen(res.created_at)}{res.edited ? " \u00b7 edited by you" : ""}</p>}
      {msg && <p className="text-xs text-gray-300 mt-2">{msg}</p>}
      {draft && (
        <div className="mt-2 space-y-2">
          <label className="block text-xs text-gray-400">Headline<input className={inp} value={draft.headline} onChange={(e) => setDraft({ ...draft, headline: e.target.value })} /></label>
          <label className="block text-xs text-gray-400">Summary<textarea rows={4} className={inp} value={draft.summary} onChange={(e) => setDraft({ ...draft, summary: e.target.value })} /></label>
          <label className="block text-xs text-gray-400">Skills (comma separated)<input className={inp} value={draft.skills} onChange={(e) => setDraft({ ...draft, skills: e.target.value })} /></label>
          {draft.experience.map((r: any, i: number) => (
            <div key={i} className="p-2.5 rounded-lg border border-gray-700 space-y-1.5">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
                <input className={inp} placeholder="Title" value={r.title} onChange={(e) => setRole(i, "title", e.target.value)} />
                <input className={inp} placeholder="Company" value={r.company} onChange={(e) => setRole(i, "company", e.target.value)} />
              </div>
              <input className={inp} placeholder="Dates" value={r.dates} onChange={(e) => setRole(i, "dates", e.target.value)} />
              <textarea rows={4} className={inp} placeholder="One bullet per line" value={r.bullets} onChange={(e) => setRole(i, "bullets", e.target.value)} />
            </div>
          ))}
          <label className="block text-xs text-gray-400">Education (one per line)<textarea rows={2} className={inp} value={draft.education} onChange={(e) => setDraft({ ...draft, education: e.target.value })} /></label>
          <label className="block text-xs text-gray-400">Additional (one per line)<textarea rows={2} className={inp} value={draft.other} onChange={(e) => setDraft({ ...draft, other: e.target.value })} /></label>
          <div className="flex gap-2">
            <button onClick={saveEdit} disabled={!!busy} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 text-white disabled:opacity-60">{busy === "save" ? "Saving…" : "Save changes"}</button>
            <button onClick={() => setDraft(null)} disabled={!!busy} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 text-gray-200">Cancel</button>
          </div>
        </div>
      )}
      {cv && !draft && (
        <div className="mt-3 space-y-3 text-sm">
          {/* The job's main asks against the CV (2026-11-09). Evidence the CV
              does not back is shown as a gap by the server, never as a strength. */}
          {res?.match?.length > 0 && (
            <div className="rounded-lg border border-gray-700 bg-gray-900/50 p-3">
              <p className="text-xs font-semibold text-gray-300 mb-1.5">How your CV meets this job</p>
              <ul className="space-y-1.5">{res.match.map((m: any, i: number) => (
                <li key={i} className="flex items-start gap-2 text-xs">
                  {m.evidence ? <CheckCircle className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" /> : <AlertCircle className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />}
                  <span><span className="text-white font-medium">{m.need}</span>{" — "}
                    {m.evidence ? <span className="text-gray-300">{m.evidence}</span>
                      : <span className="text-amber-300">not in your CV. If you have it, add it when you edit, or say it in the cover letter.</span>}</span>
                </li>))}</ul>
            </div>
          )}
          {cv.changes?.length > 0 && (
            <div><p className="text-xs font-semibold text-gray-300 mb-1">What changed for this job</p>
              <ul className="list-disc pl-5 text-xs text-gray-300 space-y-0.5">{cv.changes.map((c: string, i: number) => <li key={i}>{c}</li>)}</ul></div>
          )}
          {res?.flags?.length > 0 && !res?.edited && (
            <div className="text-xs text-amber-300 space-y-0.5">{res.flags.map((f: string, i: number) => <p key={i}>{f}</p>)}</div>
          )}
          <div className="bg-gray-900 rounded-xl p-3 border border-gray-700 space-y-2">
            {res?.edited && <p className="text-[11px] text-indigo-300">Edited by you</p>}
            {cv.headline && <p className="text-white font-semibold">{cv.headline}</p>}
            {cv.summary && <p className="text-gray-300">{cv.summary}</p>}
            {cv.skills?.length > 0 && <p className="text-xs text-gray-400">{cv.skills.join(" · ")}</p>}
            {(cv.experience || []).map((r: any, i: number) => (
              <div key={i}>
                <p className="text-gray-100 font-medium">{[r.title, r.company].filter(Boolean).join(" – ")}{r.dates && <span className="text-gray-400 font-normal"> {"·"} {r.dates}</span>}</p>
                <ul className="list-disc pl-5 text-gray-300 space-y-0.5">{(r.bullets || []).map((b: string, k: number) => <li key={k}>{b}</li>)}</ul>
              </div>
            ))}
            {[...(cv.education || []), ...(cv.other || [])].map((l: string, i: number) => <p key={"e" + i} className="text-xs text-gray-400">{l}</p>)}
          </div>
          {res?.removed?.length > 0 && (
            <details><summary className="text-xs text-gray-400 cursor-pointer">Removed by the fact check ({res.removed.length})</summary>
              <ul className="mt-1 text-xs text-gray-400 space-y-1">{res.removed.map((r: any, i: number) => <li key={i}><span className="text-gray-300">{r.text}</span> {"—"} {r.why}</li>)}</ul>
            </details>
          )}
          <div className="p-3 rounded-xl border border-gray-700">
            <p className="text-xs font-semibold text-gray-300 mb-1.5">Send with this job's application</p>
            {(["original", "tailored"] as const).map((k) => (
              <label key={k} className="flex items-center gap-2 text-sm text-gray-200 py-0.5">
                <input type="radio" name={`cvuse-${job.id}`} checked={(res?.use || "original") === k} disabled={!!busy}
                  onChange={() => act("use", () => api.useCv(job.id, k), k === "tailored" ? "This application will send the tailored CV" : "This application will send your original CV")} />
                {k === "original" ? "My original CV" : "This tailored CV (as a Word file)"}
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* "Is this posting still open?" and the cover letter both lived only in the
   legacy dashboard. check-status turned out to have no implementation at all
   (it answered a bare 404), so this is its first working home. */
function JobDetailModal({ job, onClose, onRetry, isAdmin, me }: { job: UiJob; onClose: () => void; onRetry?: (j: UiJob) => void; isAdmin?: boolean; me?: any }) {
  // Cover letters follow the plan (Free none, Premium 10 / month, Expert 30),
  // not the admin role as they did before Phase 5. The server counts and refuses.
  const clLimit = planLimit(me, "cover_letter");
  const canCoverLetter = isAdmin || (clLimit === undefined ? false : clLimit !== 0);
  const clUse = me?.usage?.cover_letter;
  const [checking, setChecking] = useState(false);
  const [checkMsg, setCheckMsg] = useState("");
  const [checkOpen, setCheckOpen] = useState<boolean | null>(
    job.statusCheck === "open" ? true : job.statusCheck === "closed" ? false : null);
  const check = async () => {
    setChecking(true); setCheckMsg("");
    try {
      const r = await api.checkStatus(job.id);
      if (r?.error) { setCheckMsg(r.error); return; }
      setCheckOpen(!!r.open);
      setCheckMsg(r.message || (r.open ? "Still open" : "Could not confirm it is open"));
    } catch (e: any) { setCheckMsg(e?.message || "Could not reach the posting"); }
    finally { setChecking(false); }
  };

  const [letter, setLetter] = useState(job.coverLetter);
  const [clBusy, setClBusy] = useState("");
  const [clMsg, setClMsg] = useState("");
  const clRun = async (action: "generate" | "save") => {
    setClBusy(action); setClMsg("");
    try {
      const r = await api.coverLetter(job.id, action, action === "save" ? letter : undefined);
      if (r?.error) { setClMsg(r.error); return; }
      if (action === "generate" && r.letter) {
        setLetter(r.letter);
        // Sentences the fact check removed (a number not in the CV) are named, not hidden.
        setClMsg("Written from your CV \u2014 read and edit it before you send it." +
                 (Array.isArray(r.notes) && r.notes.length ? " " + r.notes.join(" ") : ""));
      }
      if (action === "save") setClMsg("Saved");
    } catch (e: any) { setClMsg(e?.message || "That did not work"); }
    finally { setClBusy(""); }
  };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center sm:p-6" onClick={onClose}>
      <motion.div initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} onClick={(e) => e.stopPropagation()} className="bg-gray-900 rounded-t-3xl sm:rounded-3xl shadow-2xl max-w-2xl lg:max-w-[92vw] 2xl:max-w-[1600px] w-full max-h-[88vh] sm:max-h-[94vh] overflow-y-auto no-scrollbar border border-gray-700">
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 p-5 flex items-center justify-between z-10"><h2 className="text-lg font-bold text-white">Job Details</h2><button onClick={onClose} className="p-2 active:bg-gray-800 rounded-xl"><X className="w-5 h-5 text-gray-400" /></button></div>
        <div className="p-5 space-y-5">
          <div className="flex items-start gap-4"><div className="w-16 h-16 bg-gradient-to-br from-indigo-900/40 to-slate-800 rounded-2xl flex items-center justify-center shrink-0"><Building2 className="w-8 h-8 text-indigo-400" /></div><div className="flex-1 min-w-0"><h3 className="text-xl font-bold text-white mb-0.5">{job.title}</h3><p className="text-lg text-indigo-400 font-semibold mb-1">{job.company}</p><div className="flex items-center gap-4 text-gray-400 flex-wrap">{job.location && <div className="flex items-center gap-1"><MapPin className="w-4 h-4" /><span className="text-sm">{job.location}</span></div>}{job.timeAgo && <div className="flex items-center gap-1"><Clock className="w-4 h-4" /><span className="text-sm">{job.timeAgo}</span></div>}</div></div></div>
          <div className="grid grid-cols-2 gap-3"><div className="bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-xl p-4 border border-blue-800/50"><div className="text-sm text-gray-400 mb-1">Match Score</div><div className="text-2xl font-bold text-blue-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div></div><div className="bg-gradient-to-br from-green-900/20 to-emerald-900/20 rounded-xl p-4 border border-green-800/50"><div className="text-sm text-gray-400 mb-1">Your Strength</div><div className="text-2xl font-bold text-green-400">{job.candidateScore === null ? "—" : `${job.candidateScore}%`}</div></div></div>
          {job.whyFits && <div className="bg-gradient-to-br from-amber-900/20 to-orange-900/20 rounded-xl p-4 border border-amber-800/50"><h4 className="font-bold text-amber-500 mb-1.5 flex items-center gap-2"><Sparkles className="w-5 h-5" />Why this is a strong fit</h4><p className="text-gray-300 text-sm leading-relaxed">{job.whyFits}</p></div>}
          {job.description && <div><h4 className="font-semibold text-white mb-1.5">About the role</h4><p className="text-gray-400 text-sm leading-relaxed whitespace-pre-line">{job.description}</p></div>}
          {job.applyStatus && job.applyStatus !== "queued" && (
            <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
              <h4 className="font-semibold text-white mb-2">Application status</h4>
              <ApplyBadge job={job} />
              {job.applyConfirmation && <p className="text-sm text-gray-300 mt-2 whitespace-pre-line">{job.applyConfirmation}</p>}
              {!job.applyConfirmation && job.applyError && <p className="text-sm text-red-300 mt-2 break-words">{job.applyError}</p>}
              {job.applyStatus === "needs_answers" && job.applyQuestions.length > 0 && (
                <AnswerQuestions job={job} onDone={onClose} />
              )}
              {job.applyStatus === "awaiting_code" && (
                <p className="text-sm text-amber-300 mt-2">The employer emailed you a security code. Paste it in the "Finish applying" box to send this application.</p>
              )}
              {job.applyStatus === "needs_code" && (
                <p className="text-sm text-gray-300 mt-2">This site emails a security code during the application, and it wasn't entered in time, so nothing was sent. Tap Apply now while you can check your email: a new code will arrive within a minute, and you paste it here.</p>
              )}
              {job.applyStatus === "needs_code" && onRetry && (
                <button onClick={() => { onRetry(job); onClose(); }} className="mt-3 px-4 py-2 bg-indigo-600 active:bg-indigo-700 text-white rounded-xl text-sm font-medium inline-flex items-center gap-1.5"><Send className="w-4 h-4" />Apply now</button>
              )}
              {(job.applyStatus === "failed" || job.applyStatus === "manual_required") && onRetry && (
                <button onClick={() => { onRetry(job); onClose(); }} className="mt-3 px-4 py-2 bg-indigo-600 active:bg-indigo-700 text-white rounded-xl text-sm font-medium inline-flex items-center gap-1.5"><RefreshCw className="w-4 h-4" />Retry application</button>
              )}
            </div>
          )}
          {job.url && (
            <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="min-w-0">
                  <h4 className="font-semibold text-white text-sm">Is this still open?</h4>
                  <p className="text-xs text-gray-400">
                    {job.statusCheckedDate && checkOpen === null
                      ? `Last checked ${job.statusCheckedDate}`
                      : "Fetches the posting and reads it. Never removes the job."}
                  </p>
                </div>
                <button onClick={check} disabled={checking}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-60 shrink-0">
                  {checking ? "Checking\u2026" : "Check now"}
                </button>
              </div>
              {checkMsg && (
                <p className={`text-xs mt-2 ${checkOpen === true ? "text-green-300" : checkOpen === false ? "text-amber-300" : "text-gray-400"}`}>
                  {checkMsg}
                </p>
              )}
            </div>
          )}
          {canCoverLetter && (
            <div className="bg-gray-800 rounded-xl p-4 border border-gray-700">
              <h4 className="font-semibold text-white text-sm mb-1">Cover letter</h4>
              <p className="text-xs text-gray-400 mb-2">Written by the model from your CV and this posting. Read it before it goes anywhere.
                {clUse && clUse.limit !== null && <> {Math.max(0, clUse.limit - clUse.used)} of {clUse.limit} left this month.</>}</p>
              <textarea value={letter} onChange={(e) => setLetter(e.target.value)} rows={8} style={{ minHeight: "12rem" }}
                        placeholder="Nothing yet — generate a draft, or write your own."
                        className="w-full px-3 py-2 border border-gray-500 rounded-xl bg-gray-900 text-white text-sm leading-relaxed" />
              <div className="flex gap-2 mt-2">
                <button onClick={() => clRun("generate")} disabled={!!clBusy}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-60">
                  {clBusy === "generate" ? "Writing\u2026" : letter ? "Rewrite" : "Generate"}
                </button>
                <button onClick={() => clRun("save")} disabled={!!clBusy || !letter.trim()}
                        className="px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 disabled:opacity-40">
                  {clBusy === "save" ? "Saving\u2026" : "Save"}
                </button>
                {clMsg && <span className="text-xs text-gray-400 self-center">{clMsg}</span>}
              </div>
            </div>
          )}
          <TailoredCv job={job} me={me} isAdmin={isAdmin} />
          {job.url && <a href={job.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-indigo-400 text-sm font-medium">{openLabel(job) === "Open" ? "Open job posting" : openLabel(job)} <ExternalLink className="w-3.5 h-3.5" /></a>}
        </div>
      </motion.div>
    </motion.div>
  );
}

function TagInput({ value, onChange, noun = "role", example = "VP Product" }:
    { value: string[]; onChange: (v: string[]) => void; noun?: string; example?: string }) {
  const [draft, setDraft] = useState("");
  const add = () => { const v = draft.trim().replace(/,$/, ""); if (v && !value.includes(v)) onChange([...value, v]); setDraft(""); };
  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-2">
        {value.map((t) => (<span key={t} className="inline-flex items-center gap-1.5 pl-3 pr-2 py-1.5 bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 rounded-full text-sm">{t}<button onClick={() => onChange(value.filter((x) => x !== t))} className="w-4 h-4 flex items-center justify-center rounded-full bg-indigo-500/40 active:bg-indigo-500"><X className="w-3 h-3" /></button></span>))}
        {value.length === 0 && <span className="text-xs text-gray-400">No {noun}s yet — add one below.</span>}
      </div>
      <div className="flex gap-2"><input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} placeholder={`e.g. ${example}`} className="flex-1 px-4 py-2.5 border border-gray-700 rounded-xl bg-gray-800 text-white text-sm" /><button onClick={add} className="px-3.5 bg-indigo-600 active:bg-indigo-700 text-white rounded-xl flex items-center justify-center"><Plus className="w-5 h-5" /></button></div>
      <p className="text-xs text-gray-400 mt-1.5">Press Enter to add each {noun}.</p>
    </div>
  );
}

/* Admin: set a user's plan - permanently, or as a promotion that ends after N
   days and falls back to Free. The server records every change (plan_events)
   and unlocks held-back matches on an upgrade. */
function UserPlanControl({ u, onDone }: { u: any; onDone: () => void }) {
  const [plan, setPlan] = useState<string>(u.plan || "free");
  const [days, setDays] = useState<string>("perm");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const promo = u.plan_until && u.plan_source === "promotion";
  const apply = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await api.adminSetPlan(u.id, plan, days === "perm" ? null : parseInt(days, 10));
      setMsg(`Set to ${r.plan}${r.plan_until ? ` until ${fmtPlanDate(r.plan_until)}` : ""}${r.locked ? ` · ${r.locked} match(es) still held back` : ""}`);
      onDone();
    } catch (e: any) { setMsg(e?.message || "Couldn't change the plan"); }
    finally { setBusy(false); }
  };
  return (
    <div className="mt-2.5 pt-2.5 border-t border-gray-700">
      <p className="text-xs text-gray-300">
        Plan: <b className="text-white capitalize">{u.plan || "free"}</b>
        {promo && <> until {fmtPlanDate(u.plan_until)}, then <span className="capitalize">{u.plan_after || "free"}</span></>}
        {u.stats_locked > 0 && <span className="text-amber-300"> · {u.stats_locked} held back</span>}
      </p>
      <div className="flex flex-wrap items-center gap-2 mt-2">
        <select value={plan} onChange={(e) => setPlan(e.target.value)} className="px-2 py-1.5 border border-gray-600 rounded-lg bg-gray-700 text-white text-xs">
          <option value="free">Free</option><option value="premium">Premium</option><option value="expert">Expert</option>
        </select>
        <select value={days} onChange={(e) => setDays(e.target.value)} className="px-2 py-1.5 border border-gray-600 rounded-lg bg-gray-700 text-white text-xs">
          <option value="perm">Permanent</option>
          {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days, then Free</option>)}
        </select>
        <button onClick={apply} disabled={busy} className="px-2.5 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-60">{busy ? "…" : "Apply"}</button>
      </div>
      {msg && <p className="text-[11px] text-gray-400 mt-1.5">{msg}</p>}
    </div>
  );
}




/* Step 12: are the learning rules earning their place? Read-only. */
// Standing instructions for application forms (2026-11-08): answers to the
// questions that recur on every form, kept on the account - Eran asked for
// them not to be profile questions.
// Proves notifications live (2026-11-08): the security-code flow depends on
// them, and every channel used to fail without a trace.
// Step 8.3: the engine's success rate per application system - the number
// the decision to run auto-apply for everyone rests on.
function ApplyRates() {
  const [rep, setRep] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const load = async () => {
    setBusy(true); setErr("");
    try { setRep(await api.adminApplyRates()); } catch (e: any) { setErr(e?.message || "Could not load"); }
    finally { setBusy(false); }
  };
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
      <h3 className="font-semibold text-white mb-1">Apply results by system</h3>
      <p className="text-xs text-gray-400 mb-2">Every job the engine tried, by where it applies. Success = sent / (sent + failed).</p>
      <button onClick={load} disabled={busy}
        className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50">{busy ? "Loading..." : rep ? "Refresh" : "Show results"}</button>
      {rep && (
        <div className="mt-3 overflow-x-auto">
          <p className="text-xs text-gray-300 mb-2">Auto-apply engine: <span className={rep.engine_on ? "text-green-300" : "text-amber-300"}>{rep.engine_on ? "on" : "off (supervised applies only)"}</span></p>
          {rep.systems?.length ? (
            <table className="w-full text-xs">
              <thead><tr className="text-gray-400 text-left">
                <th className="font-medium py-1 pr-2">System</th><th className="font-medium py-1 px-1 text-right">Tried</th>
                <th className="font-medium py-1 px-1 text-right">Sent</th><th className="font-medium py-1 px-1 text-right">Held</th>
                <th className="font-medium py-1 px-1 text-right">Manual</th><th className="font-medium py-1 px-1 text-right">Failed</th>
                <th className="font-medium py-1 pl-1 text-right">Success</th>
              </tr></thead>
              <tbody>
                {rep.systems.map((r: any) => (
                  <tr key={r.system} className="border-t border-gray-700/60">
                    <td className="py-1 pr-2 text-gray-200">{r.system}</td>
                    <td className="py-1 px-1 text-right text-gray-300">{r.attempted}</td>
                    <td className="py-1 px-1 text-right text-green-300">{r.sent}</td>
                    <td className="py-1 px-1 text-right text-amber-300">{r.held}</td>
                    <td className="py-1 px-1 text-right text-gray-300">{r.manual}</td>
                    <td className="py-1 px-1 text-right text-red-300">{r.failed}</td>
                    <td className="py-1 pl-1 text-right text-gray-100">{r.success_rate == null ? "-" : `${r.success_rate}%`}{r.small_sample ? <span className="text-gray-400"> *</span> : null}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="text-xs text-gray-400">No engine attempts yet.</p>}
          <p className="text-[11px] text-gray-400 mt-2">* fewer than 5 sent or failed: too few to judge. Held = waiting on your answers or a security code; manual = job boards and Workday, by design.</p>
        </div>
      )}
      {err && <p className="text-xs text-red-300 mt-2">{err}</p>}
    </div>
  );
}

function NotificationCheck() {
  const [st, setSt] = useState<Record<string, any> | null>(null);
  const [res, setRes] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const load = () => api.adminNotifyStatus().then(setSt).catch((e: any) => setErr(e?.message || "Could not load"));
  useEffect(() => { load(); }, []);
  const send = async () => {
    setBusy(true); setErr(""); setRes(null);
    try { const r = await api.adminNotifyCheck(); setRes(r.results || {}); setSt(r); }
    catch (e: any) { setErr(e?.message || "The check failed"); }
    finally { setBusy(false); }
  };
  const ok = (v: string) => v.startsWith("sent");
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
      <h3 className="font-semibold text-white mb-1">Notification check</h3>
      <p className="text-xs text-gray-400 mb-2">What can reach you, and a real test on every channel you use.</p>
      {st && (
        <div className="text-xs text-gray-300 space-y-1">
          <p>Push on the server: <span className={st.push?.keys && st.push?.library ? "text-green-300" : "text-amber-300"}>{st.push?.keys && st.push?.library ? "ready" : `not ready (${!st.push?.keys ? "keys missing" : "library missing"})`}</span></p>
          <p>Devices with notifications on: {st.devices?.length ? st.devices.map((d: any) => d.service).join(", ") : <span className="text-amber-300">none - turn them on in Settings on your phone</span>}</p>
          <p>Other channels: {st.channels?.length ? st.channels.join(", ") : "none"}{st.channels?.includes("email") && st.email_sender_is_sandbox ? " (email reaches only your own address until the domain is set up)" : ""}</p>
        </div>
      )}
      <button onClick={send} disabled={busy}
        className="mt-3 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50">{busy ? "Sending..." : "Send me a test on every channel"}</button>
      {res && (
        <ul className="mt-2 text-xs space-y-0.5">
          {Object.entries(res).map(([k, v]) => <li key={k} className={ok(v) ? "text-green-300" : "text-amber-300"}><span className="text-gray-300">{k}:</span> {v}</li>)}
        </ul>
      )}
      {st?.recent?.length > 0 && (
        <details className="mt-2"><summary className="text-xs text-gray-400 cursor-pointer">Last notifications logged</summary>
          <ul className="mt-1 text-[11px] text-gray-400 space-y-0.5">{st.recent.map((r: any, i: number) => <li key={i}>{fmtWhen(r.at)} {r.detail}</li>)}</ul>
        </details>
      )}
      {err && <p className="text-xs text-red-300 mt-2">{err}</p>}
    </div>
  );
}

function ApplicationInstructions() {
  const [text, setText] = useState(""); const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState("");
  useEffect(() => {
    api.applicationAnswers().then((a) => { setText(a.standing_notes || ""); setLoaded(true); })
      .catch(() => setMsg("Could not load the instructions"));
  }, []);
  const save = async () => {
    setBusy(true); setMsg("");
    try { await api.saveApplicationInstructions(text); setMsg("Saved"); }
    catch (e: any) { setMsg(e?.message || "Save failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
      <h3 className="font-semibold text-white mb-1">Application instructions</h3>
      <p className="text-xs text-gray-400 mb-2">How to answer the questions forms keep asking. Followed on every application; anything marked "do not answer" that a form requires is sent to you instead.</p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!loaded} rows={10} maxLength={3000}
        className="w-full bg-gray-900 border border-gray-700 rounded-lg p-2 text-sm text-gray-100" />
      <div className="flex items-center gap-3 mt-2">
        <button onClick={save} disabled={busy || !loaded}
          className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50">{busy ? "Saving..." : "Save"}</button>
        <span className="text-xs text-gray-400">{text.length}/3000 {msg && `· ${msg}`}</span>
      </div>
    </div>
  );
}

function LearningCheck() {
  const [rep, setRep] = useState<Record<string, any> | null>(null);
  const [scope, setScope] = useState<"me" | "all">("me");
  const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const run = async (sc: "me" | "all") => {
    setScope(sc); setBusy(true); setErr("");
    try { setRep(await api.adminLearningReport(sc)); }
    catch (e: any) { setErr(e?.message || "The report failed"); }
    finally { setBusy(false); }
  };
  const Table = ({ title, rows }: { title: string; rows: any[] }) => (
    <div className="mt-3">
      <p className="text-xs font-semibold text-gray-300 mb-1">{title}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead><tr className="text-gray-400 text-left">
            <th className="font-medium py-1 pr-2">Group</th><th className="font-medium py-1 px-1 text-right">Decided</th>
            <th className="font-medium py-1 px-1 text-right">Approved</th><th className="font-medium py-1 pl-1 text-right">Applied</th>
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.group} className="border-t border-gray-700/60">
                <td className="py-1 pr-2 text-gray-200">{r.group}{r.small_sample ? <span className="text-gray-400"> *</span> : null}</td>
                <td className="py-1 px-1 text-right text-gray-300">{r.decided}</td>
                <td className="py-1 px-1 text-right text-gray-100">{r.approval_rate == null ? "-" : `${r.approval_rate}%`}</td>
                <td className="py-1 pl-1 text-right text-gray-300">{r.apply_rate == null ? "-" : `${r.apply_rate}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
  return (
    <div>
      <h3 className="font-semibold text-white mb-1 flex items-center gap-2"><TrendingUp className="w-5 h-5 text-indigo-400" />Learning check</h3>
      <p className="text-xs text-gray-400 mb-3">Are demoted jobs really passed more? Does the AI score predict what you approve? Read-only.</p>
      <div className="grid grid-cols-2 gap-2">
        {(["me", "all"] as const).map((sc) => (
          <button key={sc} onClick={() => run(sc)} disabled={busy}
            className={`p-3 rounded-xl border text-sm font-medium disabled:opacity-60 ${rep && scope === sc ? "bg-indigo-600/20 border-indigo-500 text-white" : "bg-gray-800 border-gray-700 text-gray-200"}`}>
            {busy && scope === sc ? "Checking\u2026" : sc === "me" ? "My decisions" : "All users"}
          </button>
        ))}
      </div>
      {rep && (
        <div className="mt-2 bg-gray-800 rounded-xl border border-gray-700 p-3.5">
          <p className="text-sm text-white font-medium">{rep.verdict}</p>
          <p className="text-xs text-gray-400 mt-1">{rep.decided} decided · {rep.approval_rate ?? "-"}% approved · {rep.apply_rate ?? "-"}% of approved applied</p>
          <Table title="Demoted or not" rows={rep.by_penalty || []} />
          <Table title="By demotion reason" rows={rep.by_reason || []} />
          <Table title="By AI match score" rows={rep.by_score || []} />
          <p className="text-[11px] text-gray-400 mt-3">* fewer than 20 decisions. {rep.caveat}</p>
        </div>
      )}
      {err && <p className="text-sm text-red-300 mt-2">{err}</p>}
    </div>
  );
}

/* Step 1a: the link report, one button instead of a raw /api URL that needs
   the signed-in browser session (2026-11-07: the raw link "is not working").
   Read-only - shows the headline numbers and copies the full JSON to paste. */
function LinkReport() {
  const [rep, setRep] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  const run = async () => {
    setBusy(true); setErr(""); setCopied(false);
    try { setRep(await api.adminLinkReport("all", 50)); }
    catch (e: any) { setErr(e?.message || "The report failed to run"); }
    finally { setBusy(false); }
  };
  const [trial, setTrial] = useState<Record<string, any> | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
  const runTrial = async () => {
    if (!confirm("Ask Gemini + Google Search to find up to 30 job-board jobs on the company's own site?\n\n" +
                 "Estimated cost: about 1 cent (searches are inside Google's free daily allowance). " +
                 "Every link is checked on the page. Nothing is changed in anyone's queue.")) return;
    setTrialBusy(true); setErr("");
    try { setTrial(await api.adminLinkSearchTrial(30)); }
    catch (e: any) { setErr(e?.message || "The trial failed to run"); }
    finally { setTrialBusy(false); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(trial || rep, null, 2)); setCopied(true); }
    catch { setErr("Copy failed - select the text below instead"); }
  };
  const d = rep?.dry_run || {};
  const Row = ({ k, v }: { k: string; v: any }) => (
    <div className="flex justify-between gap-3 text-xs"><span className="text-gray-400">{k}</span><span className="text-gray-200 font-medium text-right">{String(v ?? "-")}</span></div>
  );
  return (
    <div>
      <h3 className="font-semibold text-white mb-1 flex items-center gap-2"><Link2 className="w-5 h-5 text-indigo-400" />Link report</h3>
      <p className="text-xs text-gray-400 mb-3">Where every open job links to, and how many the resolver could point at the company's own posting. Read-only, about a minute.</p>
      <button onClick={run} disabled={busy}
        className="w-full flex items-center gap-3 p-3.5 rounded-xl border bg-gray-800 border-gray-700 text-left disabled:opacity-60">
        {busy ? <Loader2 className="w-5 h-5 animate-spin shrink-0 text-gray-300" /> : <Link2 className="w-5 h-5 text-gray-300 shrink-0" />}
        <span className="block text-sm font-medium text-gray-200">{busy ? "Running (up to a minute)\u2026" : rep ? "Run again" : "Run link report (all users)"}</span>
      </button>
      {rep && (
        <div className="mt-2 bg-gray-800 rounded-xl border border-gray-700 p-3.5 space-y-1.5">
          <Row k="Open jobs" v={rep.open_jobs} />
          <Row k="Already direct (company site / ATS)" v={rep.direct_share != null ? `${rep.direct_share}%` : "-"} />
          <Row k="Need a direct link" v={rep.needing_a_direct_link} />
          <Row k="Sampled / resolved" v={`${d.tried ?? 0} / ${d.resolved ?? 0}${d.hit_rate != null ? ` (${d.hit_rate}%)` : ""}`} />
          {d.incomplete > 0 && <Row k="Lookups that did not answer" v={d.incomplete} />}
          {d.by_ats && Object.keys(d.by_ats).length > 0 && <Row k="Resolved to" v={Object.entries(d.by_ats).map(([a, n]) => `${a} ${n}`).join(", ")} />}
          <Row k="Took" v={d.seconds != null ? `${d.seconds}s` : "-"} />
          {d.reach && <Row k="Reach (known boards)" v={Object.entries(d.reach).map(([a, n]) => `${a} ${n}`).join(", ")} />}
          {d.ats_answers && Object.keys(d.ats_answers).length > 0 && (
            <Row k="Board answers" v={Object.entries(d.ats_answers).slice(0, 8).map(([k, n]) => `${k.replace(/^(boards-api\.|api\.)/, "")}: ${n}`).join(" \u00b7 ")} />
          )}
          <button onClick={copy} className="w-full mt-2 py-2 rounded-lg border border-gray-600 text-sm text-gray-200">
            {copied ? "Copied - paste it to Claude" : trial ? "Copy trial result" : "Copy full report"}
          </button>
        </div>
      )}
      <button onClick={runTrial} disabled={trialBusy || busy}
        className="w-full mt-2 flex items-center gap-3 p-3.5 rounded-xl border bg-gray-800 border-gray-700 text-left disabled:opacity-60">
        {trialBusy ? <Loader2 className="w-5 h-5 animate-spin shrink-0 text-gray-300" /> : <Search className="w-5 h-5 text-gray-300 shrink-0" />}
        <span>
          <span className="block text-sm font-medium text-gray-200">{trialBusy ? "Searching (up to a minute)\u2026" : "Try web search on 30 jobs"}</span>
          <span className="block text-xs text-gray-400">Gemini + Google Search, about 1 cent, read-only</span>
        </span>
      </button>
      {trial && (
        <div className="mt-2 bg-gray-800 rounded-xl border border-gray-700 p-3.5 space-y-1.5">
          <Row k="Jobs tried" v={`${trial.done ?? 0} of ${trial.pairs ?? 0}`} />
          <Row k="Found free (no search)" v={trial.free_hits} />
          <Row k="Searched / verified links" v={`${trial.searched ?? 0} / ${trial.search_hits ?? 0}${trial.search_hit_rate != null ? ` (${trial.search_hit_rate}%)` : ""}`} />
          <Row k="Links found but rejected" v={trial.candidates_rejected} />
          <Row k="Gemini calls / tokens" v={`${trial.gemini_calls ?? 0} / ${trial.gemini_tokens ?? 0}`} />
          <Row k="Took" v={trial.seconds != null ? `${trial.seconds}s` : "-"} />
          {!copied && <button onClick={copy} className="w-full mt-2 py-2 rounded-lg border border-gray-600 text-sm text-gray-200">Copy trial result</button>}
        </div>
      )}
      {err && <p className="text-sm text-red-300 mt-2">{err}</p>}
    </div>
  );
}

/* Step 8.2 of EXECUTION_PLAN_NEXT: one live, supervised submit on one of your
   own approved jobs, without setting APPLY_ENGINE_ENABLED for everyone. The
   dry run comes first and is the only way to reach the real submit, so you
   always see the target URL, the CV and the applicant details before anything
   is sent. Admin only (the server checks), same-origin only for the live run. */
function SupervisedApply() {
  const [jobs, setJobs] = useState<any[] | null>(null);
  const [busy, setBusy] = useState<string>("");
  const [err, setErr] = useState("");
  const [dry, setDry] = useState<any>(null);
  const [live, setLive] = useState<any>(null);
  const [codeRun, setCodeRun] = useState<any>(null);
  const [code, setCode] = useState(""); const [codeMsg, setCodeMsg] = useState("");

  const list = async () => {
    setBusy("list"); setErr(""); setDry(null); setLive(null);
    try {
      const r = await api.adminApplyCandidates();
      const rank = { submittable: 0, account_needed: 1, job_board: 2 } as Record<string, number>;
      const all = (r.jobs || []).slice();
      all.sort((a, b) => Number(a.status !== "approved") - Number(b.status !== "approved")
                         || (rank[a.kind] ?? 3) - (rank[b.kind] ?? 3));
      setJobs(all);
    } catch (e: any) { setErr(e?.message || "Could not load your queue"); }
    finally { setBusy(""); }
  };
  const runDry = async (j: any) => {
    setBusy(`dry-${j.id}`); setErr(""); setLive(null);
    try { setDry(await api.adminApplyTest(j.id)); }
    catch (e: any) { setErr(e?.message || "Dry run failed"); }
    finally { setBusy(""); }
  };
  const runLive = async () => {
    if (!dry?.job) return;
    if (!confirm(`Submit a REAL application to ${dry.job.company} for "${dry.job.title}"?\n\n` +
                 `Target: ${dry.target_url}\n\nThis sends your CV and details to the employer. It cannot be undone.`)) return;
    setBusy("live"); setErr(""); setCodeRun(null);
    try {
      let v = await api.adminApplyTest(dry.job.id, true);
      // The submit runs in the background; a site may email a security code.
      let misses = 0;
      while (v.run_id && v.state !== "done") {
        setCodeRun(v.state === "awaiting_code" ? v : null);
        await new Promise((res) => setTimeout(res, 2000));
        try { v = await api.adminApplyRun(v.run_id); misses = 0; }
        catch (e: any) { if (++misses >= 5) throw e; }
      }
      setCodeRun(null);
      setLive(v);
    }
    catch (e: any) { setErr(e?.message || "The submit failed to run"); }
    finally { setBusy(""); setCodeRun(null); }
  };
  const sendCode = async () => {
    if (!codeRun) return;
    setCodeMsg("");
    try { await api.adminApplyCode(codeRun.run_id, code.trim()); setCode(""); setCodeMsg("Sent - submitting..."); }
    catch (e: any) { setCodeMsg(e?.message || "The code was not accepted"); }
  };
  const ready = dry && dry.is_direct_ats && !dry.account_needed && dry.cv_present && dry.applicant_ready;
  const Field = ({ k, v, ok }: { k: string; v: any; ok?: boolean }) => (
    <div className="flex items-start justify-between gap-3 text-xs">
      <span className="text-gray-400 shrink-0">{k}</span>
      <span className={`min-w-0 break-all text-right font-medium ${ok === false ? "text-amber-300" : ok ? "text-green-300" : "text-gray-200"}`}>{String(v ?? "")}</span>
    </div>
  );
  const r = live?.result;
  return (
    <div>
      <h3 className="font-semibold text-white mb-1 flex items-center gap-2"><Send className="w-5 h-5 text-indigo-400" />Supervised apply</h3>
      <p className="text-xs text-gray-400 mb-3">
        Submit ONE job for real, with auto-apply still off for everyone else.
        A dry run comes first and sends nothing.
      </p>
      <button onClick={list} disabled={!!busy}
        className="w-full flex items-center gap-3 p-3.5 rounded-xl border bg-gray-800 border-gray-700 text-left disabled:opacity-60">
        {busy === "list" ? <Loader2 className="w-5 h-5 animate-spin shrink-0 text-gray-300" /> : <RefreshCw className="w-5 h-5 text-gray-300 shrink-0" />}
        <span className="block text-sm font-medium text-gray-200">{jobs ? "Reload jobs" : "Pick a job"}</span>
      </button>
      {jobs && jobs.length === 0 && <p className="text-xs text-gray-400 mt-2">No approved jobs, and none in your queue on a site the engine can submit to.</p>}
      {jobs && jobs.length > 0 && !dry && (
        <div className="mt-2 space-y-1.5">
          {jobs.map((j, i) => (
            <div key={j.id}>
            {(i === 0 || jobs[i - 1].status !== j.status) && (
              <p className="text-[11px] uppercase tracking-wide text-gray-400 pt-2 pb-1">
                {j.status === "approved" ? "Approved" : "Not approved yet \u2013 the engine can submit these"}
              </p>
            )}
            <button onClick={() => runDry(j)} disabled={!!busy}
              className="w-full flex items-center justify-between gap-3 p-3 rounded-xl border bg-gray-800 border-gray-700 text-left disabled:opacity-60">
              <span className="min-w-0">
                <span className="block text-sm text-gray-200 truncate">{j.title}</span>
                <span className="block text-xs text-gray-400 truncate">{j.company}</span>
              </span>
              <span className={`shrink-0 text-[10px] px-2 py-0.5 rounded-full ${j.kind === "submittable" ? "bg-green-500/20 text-green-300" : j.kind === "account_needed" ? "bg-amber-500/20 text-amber-300" : "bg-gray-700 text-gray-300"}`}>
                {busy === `dry-${j.id}` ? "\u2026" : j.kind === "submittable" ? "Can submit" : j.kind === "account_needed" ? "Account needed" : "Job board"}
              </span>
            </button>
            </div>
          ))}
        </div>
      )}
      {dry && (
        <div className="mt-2 bg-gray-800 rounded-xl border border-gray-700 p-3.5 space-y-1.5">
          <p className="text-sm text-white font-medium">{dry.job?.title} <span className="text-gray-400 font-normal">at {dry.job?.company}</span></p>
          <Field k="Submits to" v={dry.target_url} />
          <Field k="Company site (can submit)" v={dry.is_direct_ats ? "yes" : "no"} ok={!!dry.is_direct_ats} />
          <Field k="CV" v={dry.cv_present ? "found" : "missing"} ok={!!dry.cv_present} />
          <Field k="Applicant" v={dry.applicant_name || (dry.applicant_ready ? "ready" : "no name found")} ok={!!dry.applicant_ready} />
          {!live && (
            <div className="flex gap-2 pt-2">
              <button onClick={() => setDry(null)} disabled={!!busy}
                className="flex-1 py-2 rounded-lg border border-gray-600 text-sm text-gray-300 disabled:opacity-60">Back</button>
              <button onClick={runLive} disabled={!!busy || !ready}
                className="flex-1 py-2 rounded-lg bg-red-600 text-white text-sm font-medium disabled:opacity-40">
                {busy === "live" ? (codeRun ? "Waiting for the code\u2026" : "Submitting\u2026 (up to 2\u00bd min)") : "Submit for real"}
              </button>
            </div>
          )}
          {codeRun && (
            <div className="pt-2 border-t border-gray-700 space-y-2">
              <p className="text-sm text-amber-300 font-medium">The site emailed you a security code</p>
              <p className="text-xs text-gray-300">Check your inbox (from Greenhouse: "Security code for your application to {dry.job?.company}") and paste the code. Nothing is sent until you do. About {Math.ceil((codeRun.code_seconds_left || 0) / 60)} min left.</p>
              <div className="flex gap-2">
                <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="8-character code" autoComplete="one-time-code"
                  className="flex-1 min-w-0 bg-gray-900 border border-gray-700 rounded-lg px-2 py-1.5 text-sm text-gray-100 tracking-widest" />
                <button onClick={sendCode} disabled={code.trim().length < 4}
                  className="px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-50">Enter code</button>
              </div>
              {codeMsg && <p className="text-xs text-gray-300">{codeMsg}</p>}
            </div>
          )}
          {!ready && !live && <p className="text-xs text-amber-300">Not submittable: {!dry.is_direct_ats ? "a job-board listing (apply there yourself)" : dry.account_needed ? "Workday asks for an account on the company's site first (apply there yourself)" : !dry.cv_present ? "no CV found" : "no applicant name in your CV"}.</p>}
          {r && (
            <div className="pt-2 border-t border-gray-700 space-y-1.5">
              <Field k="Result" v={r.status} ok={r.status === "confirmed" || r.status === "submitted"} />
              {r.resolved_url && <Field k="Submitted at" v={r.resolved_url} />}
              {r.confirmation_text && <Field k="Confirmation" v={r.confirmation_text} />}
              {r.error && <Field k="Error" v={r.error} ok={false} />}
              {r.apply_failure_type && <Field k="Failure" v={`${r.apply_failure_type}${r.apply_failure_detail ? ": " + r.apply_failure_detail : ""}`} ok={false} />}
              {r.status === "needs_code" && <p className="text-xs text-amber-300">Not sent: the site's security code was not entered. Submit again and paste the new code when asked.</p>}
              {r.status === "needs_answers" && Array.isArray(r.questions) && (
                <div className="pt-1">
                  <p className="text-xs text-amber-300 mb-1">Held, not sent. Answer these in the job's details (Queue), then submit again:</p>
                  <ul className="list-disc pl-5 text-xs text-gray-300 space-y-0.5">
                    {r.questions.map((q: any) => <li key={q.key}>{q.label}{q.draft ? <span className="text-gray-400"> (draft: {String(q.draft)})</span> : null}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {err && <p className="text-sm text-red-300 mt-2">{err}</p>}
    </div>
  );
}

function AdminPanel({ me }: any) {
  const [stats, setStats] = useState<any>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [diag, setDiag] = useState<Record<string, any> | null>(null);
  const [userMsg, setUserMsg] = useState("");
  const myId = me?.id;
  const load = () => {
    api.adminQueueStats().then(setStats).catch(() => {});
    api.adminUsers().then((u) => setUsers(Array.isArray(u) ? u : [])).catch(() => {});
  };
  useEffect(() => { load(); }, []);
  const run = async (kind: string) => {
    if (kind === "clear-applied" && !confirm("Permanently delete ALL applied jobs? This cannot be undone.")) return;
    if (kind === "clear-attempted" && !confirm("Move already-attempted (manual / failed) jobs out of the queue? They go to Passed and can be restored.")) return;
    setBusy(kind); setMsg("");
    try {
      let m = "Done.";
      if (kind === "clear-attempted") { const r = await api.adminClearAttempted(); m = `Cleared ${r.cleared || 0} attempted job(s) from the queue.`; }
      else if (kind === "clear-applied") { const r = await api.adminClearApplied(); m = `Deleted ${r.deleted || 0} applied job(s).`; }
      else if (kind === "rescore") { const r = await api.adminRescore(); m = r.message || `Re-scored ${r.rescored || 0} job(s).`; }
      else if (kind === "dedup") { const r = await api.adminDedup(); m = `Removed ${r.removed || 0} duplicate(s).`; }
      setMsg(m); load();
    } catch (e: any) { setMsg(e?.message || "Action failed"); }
    finally { setBusy(""); }
  };
  /* The 8-week ops item, which has never been run because running it meant
     hand-crafting a request. It launches a browser against example.com and
     submits nothing, so it is safe behind a plain button. */
  const selftest = async () => {
    setBusy("selftest"); setMsg(""); setDiag(null);
    try { setDiag(await api.adminApplySelftest()); }
    catch (e: any) { setMsg(e?.message || "Diagnostics failed"); }
    finally { setBusy(""); }
  };

  const toggleUser = async (u: any) => {
    setUserMsg("");
    if (u.id === myId) { setUserMsg("You cannot disable your own account."); return; }
    const verb = u.is_active ? "Disable" : "Enable";
    if (!confirm(`${verb} ${u.email}?` + (u.is_active ? "\n\nThey will be signed out and cannot sign back in." : ""))) return;
    setBusy(`user-${u.id}`);
    try {
      const r = await api.adminToggleUser(u.id);
      if (r?.error) { setUserMsg(r.error); return; }
      // Render the state the SERVER reports, not the one we assumed.
      setUsers((cur) => cur.map((x) => (x.id === u.id ? { ...x, is_active: r.is_active } : x)));
    } catch (e: any) { setUserMsg(e?.message || "Could not change that account"); }
    finally { setBusy(""); }
  };

  const Stat = ({ n, label, color }: { n: number; label: string; color: string }) => (
    <div className="bg-gray-800 rounded-xl border border-gray-700 py-3 text-center">
      <div className={`text-xl font-bold ${color}`}>{n ?? 0}</div>
      <div className="text-xs text-gray-400 mt-0.5">{label}</div>
    </div>
  );
  const ActBtn = ({ kind, icon, label, sub, danger }: any) => (
    <button onClick={() => run(kind)} disabled={!!busy}
      className={`w-full flex items-center gap-3 p-3.5 rounded-xl border text-left disabled:opacity-60 ${danger ? "bg-red-500/10 border-red-500/30" : "bg-gray-800 border-gray-700"}`}>
      {busy === kind ? <Loader2 className="w-5 h-5 animate-spin shrink-0 text-gray-300" /> : icon}
      <span className="min-w-0">
        <span className={`block text-sm font-medium ${danger ? "text-red-300" : "text-gray-200"}`}>{label}</span>
        {sub && <span className="block text-xs text-gray-400">{sub}</span>}
      </span>
    </button>
  );
  return (
    <div className="space-y-6">
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><BarChart3 className="w-5 h-5 text-indigo-400" />Queue overview</h3>
            <div className="grid grid-cols-3 gap-2.5">
              <Stat n={stats?.new} label="New" color="text-white" />
              <Stat n={stats?.approved} label="Queue" color="text-green-400" />
              <Stat n={stats?.applied} label="Applied" color="text-indigo-300" />
              <Stat n={stats?.rejected} label="Passed" color="text-gray-300" />
              <Stat n={stats?.manual_required} label="Manual" color="text-amber-400" />
              <Stat n={stats?.dead_links} label="Dead links" color="text-red-400" />
            </div>
          </div>
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Zap className="w-5 h-5 text-indigo-400" />Maintenance</h3>
            <div className="space-y-2">
              <ActBtn kind="clear-attempted" icon={<Ban className="w-5 h-5 text-gray-300 shrink-0" />} label="Clear attempted jobs from queue" sub="Removes already-tried (manual / failed) jobs" />
              <ActBtn kind="rescore" icon={<Target className="w-5 h-5 text-gray-300 shrink-0" />} label="Re-score new jobs" />
              <ActBtn kind="dedup" icon={<RefreshCw className="w-5 h-5 text-gray-300 shrink-0" />} label="Remove duplicate jobs" />
              <ActBtn kind="clear-applied" icon={<Trash2 className="w-5 h-5 text-red-300 shrink-0" />} label="Delete all applied jobs" sub="Permanent — asks for confirmation" danger />
            </div>
            {msg && <p className="text-sm text-indigo-300 mt-3">{msg}</p>}
          </div>
          <div>
            <h3 className="font-semibold text-white mb-1 flex items-center gap-2"><ShieldCheck className="w-5 h-5 text-indigo-400" />Apply diagnostics</h3>
            <p className="text-xs text-gray-400 mb-3">Read-only. Launches a browser against example.com to prove the runtime can drive it. Submits nothing.</p>
            <button onClick={selftest} disabled={!!busy}
              className="w-full flex items-center gap-3 p-3.5 rounded-xl border bg-gray-800 border-gray-700 text-left disabled:opacity-60">
              {busy === "selftest" ? <Loader2 className="w-5 h-5 animate-spin shrink-0 text-gray-300" /> : <Zap className="w-5 h-5 text-gray-300 shrink-0" />}
              <span className="block text-sm font-medium text-gray-200">Run apply self-test</span>
            </button>
            {diag && (
              <div className="mt-2 bg-gray-800 rounded-xl border border-gray-700 p-3.5 space-y-1.5">
                {Object.entries(diag).map(([k, v]) => {
                  const bad = v === false || v === "(unset)";
                  return (
                    <div key={k} className="flex items-start justify-between gap-3 text-xs">
                      <span className="text-gray-400 min-w-0 break-all">{k}</span>
                      <span className={`shrink-0 font-medium ${bad ? "text-amber-300" : typeof v === "boolean" ? "text-green-300" : "text-gray-200"}`}>
                        {typeof v === "object" ? JSON.stringify(v) : String(v)}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
          <SupervisedApply />
          <ApplyRates />
          <NotificationCheck />
          <ApplicationInstructions />
          <LinkReport />
          <LearningCheck />
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Users className="w-5 h-5 text-indigo-400" />Users <span className="text-xs text-gray-400 font-normal">({users.length})</span></h3>
            <div className="space-y-2">
              {users.map((u) => (
                <div key={u.id} className="bg-gray-800 rounded-xl border border-gray-700 p-3.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-white truncate">{u.name || "?"}{u.role === "admin" && <span className="ml-2 text-[10px] bg-amber-500/20 text-amber-300 px-1.5 py-0.5 rounded-full align-middle">admin</span>}</p>
                      <p className="text-xs text-gray-400 truncate">{u.email}</p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className={`text-[10px] px-2 py-0.5 rounded-full ${u.is_active ? "bg-green-500/20 text-green-300" : "bg-red-500/20 text-red-300"}`}>{u.is_active ? "Active" : "Inactive"}</span>
                      {/* No switch on your own row: disabling yourself is an
                          unrecoverable lockout, and the server refuses it too. */}
                      {u.id !== myId && (
                        <button onClick={() => toggleUser(u)} disabled={!!busy}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-medium border disabled:opacity-50 ${
                            u.is_active ? "border-red-500/40 text-red-300 hover:bg-red-500/10"
                                        : "border-green-500/40 text-green-300 hover:bg-green-500/10"}`}>
                          {busy === `user-${u.id}` ? "\u2026" : u.is_active ? "Disable" : "Enable"}
                        </button>
                      )}
                      {u.id === myId && <span className="text-[10px] text-gray-400">you</span>}
                    </div>
                  </div>
                  {u.role !== "admin" && <UserPlanControl u={u} onDone={() => api.adminUsers().then((x) => setUsers(Array.isArray(x) ? x : [])).catch(() => {})} />}
                  <div className="grid grid-cols-4 gap-2 mt-2.5 pt-2.5 border-t border-gray-700 text-center">
                    <div><div className="text-sm font-bold text-white">{u.stats_new || 0}</div><div className="text-[10px] text-gray-400">New</div></div>
                    <div><div className="text-sm font-bold text-green-400">{u.stats_approved || 0}</div><div className="text-[10px] text-gray-400">Queue</div></div>
                    <div><div className="text-sm font-bold text-indigo-300">{u.stats_applied || 0}</div><div className="text-[10px] text-gray-400">Applied</div></div>
                    <div><div className="text-sm font-bold text-gray-300">{u.stats_total || 0}</div><div className="text-[10px] text-gray-400">Total</div></div>
                  </div>
                </div>
              ))}
              {userMsg && <p className="text-xs text-amber-300 pt-1">{userMsg}</p>}
              {users.length === 0 && <p className="text-sm text-gray-400 text-center py-3">No users.</p>}
            </div>
          </div>
    </div>
  );
}

/* ── Onboarding ──────────────────────────────────────────────────────────────
 *
 * Replaces ONBOARDING_HTML (app.py, 804 lines). It is not a port: the steps are
 * the same five because they are the five things the app cannot run without,
 * but every control here is the one /app already uses - TagInput, the channel
 * picker, uploadCv - so there is exactly one implementation of each and the two
 * screens cannot drift.
 *
 * The step that justifies the whole flow is CV -> profile. /app could upload a
 * CV and never read it, so a new user faced three empty tag fields and no idea
 * what to type. Here the upload runs analyze-cv immediately and the next step
 * opens already filled in, for review rather than authorship.
 *
 * Every step is skippable and the flow is dismissible. A setup wizard that
 * cannot be escaped is a support ticket: the data it collects is all reachable
 * from Settings afterwards, and `onboarding_dismissed` means never ask again.
 */
const OB_STEPS = ["Welcome", "Your CV", "Your profile", "Schedule", "Alerts"] as const;
// Monday first, because the index IS the stored value: the scheduler compares
// search_day_of_week against `now.weekday()` (app.py:597), which is Python's
// 0=Monday..6=Sunday. Labelling this Sunday-first would have shifted every
// weekly user's search by a day with nothing on screen to show it.
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

function OnboardingView({ me, onDone }: { me: any; onDone: () => void }) {
  const parse = (v: any): string[] => {
    if (!v) return [];
    if (Array.isArray(v)) return v;
    try { const a = JSON.parse(v); return Array.isArray(a) ? a : String(v).split(",").map((s) => s.trim()).filter(Boolean); }
    catch { return String(v).split(",").map((s) => s.trim()).filter(Boolean); }
  };

  const [step, setStep] = useState(0);
  const [titles, setTitles] = useState<string[]>(parse(me.job_titles));
  const [keywords, setKeywords] = useState<string[]>(parse(me.keywords));
  const [locations, setLocations] = useState<string[]>(parse(me.locations));
  const [cvName, setCvName] = useState<string>(me.cv_filename || "");
  const [cvMsg, setCvMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [freq, setFreq] = useState<string>(me.schedule_frequency || "daily");
  const [searchHour, setSearchHour] = useState(String(me.search_hour ?? 11));
  const [searchDow, setSearchDow] = useState<number>(me.search_day_of_week ?? 1);
  const [perm, setPerm] = useState(pushState());
  const [pushMsg, setPushMsg] = useState("");
  const [finishing, setFinishing] = useState(false);

  const hours = Array.from({ length: 24 }, (_, h) => h);
  const fmtHour = (h: number) => {
    const ampm = h < 12 ? "AM" : "PM";
    const hr = h % 12 === 0 ? 12 : h % 12;
    return `${String(hr).padStart(2, "0")}:00 ${ampm}`;
  };

  /* Upload and read in one go. Two buttons here would be two chances to skip
     the half that matters. */
  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    setBusy(true); setCvMsg("Uploading…");
    try {
      const info = await uploadCv(file);
      setCvName(info.filename || file.name);
      setCvMsg("Reading your CV…");
      const r = await api.analyzeCv();
      if (r?.error) { setCvMsg("Uploaded. Couldn't read it automatically — you can fill the next step in yourself."); return; }
      if (r.job_titles?.length) setTitles(r.job_titles);
      if (r.keywords?.length) setKeywords(r.keywords);
      if (r.locations?.length) setLocations(r.locations);
      setCvMsg("✓ Read your CV — the next step is filled in for you to check.");
    } catch {
      setCvMsg("Upload failed. You can add your details by hand instead.");
    } finally { setBusy(false); }
  };

  const enableNotifs = async () => {
    setPushMsg("Enabling…");
    const r = await enablePush();
    setPerm(pushState());
    setPushMsg(r.ok ? "✓ Notifications on" : (r.reason || "Couldn't enable — you can turn these on later in Settings"));
  };

  /* Saves before it closes. A wizard that discards what was typed because the
     last button was "Skip" is worse than no wizard. */
  const finish = async (dismissed: boolean) => {
    setFinishing(true);
    try {
      if (titles.length || keywords.length || locations.length) {
        await api.saveProfile({ job_titles: titles, keywords, locations });
      }
      await api.saveSchedule({
        schedule_frequency: planLimit(me, "search_cadence") === "weekly" ? "weekly" : freq,
        search_hour: parseInt(searchHour, 10),
        // Only meaningful weekly, but always sent: leaving it unset meant a
        // weekly user silently inherited whatever day the column happened to
        // hold, with nothing on screen saying which.
        ...(freq === "weekly" ? { search_day_of_week: searchDow } : {}),
        onboarding_complete: 1,
      });
      if (dismissed) await api.dismissOnboarding();
    } catch { /* never trap the user behind a failed save */ }
    finally { setFinishing(false); onDone(); }
  };

  const next = () => setStep((s) => Math.min(s + 1, OB_STEPS.length));
  const back = () => setStep((s) => Math.max(s - 1, 0));
  const card = "bg-gray-800 rounded-2xl border border-gray-700 p-5 lg:p-8 lg:max-w-3xl lg:mx-auto";

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 text-white">
      <div className={`${SHELL} px-5 lg:px-12 py-8 safe-top`}>
        <div className="flex items-center gap-3 mb-6">
          <div className="w-11 h-11 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-2xl flex items-center justify-center shrink-0"><Briefcase className="w-6 h-6 text-white" /></div>
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-bold leading-tight">Set up Job Hunter</h1>
            <p className="text-xs text-gray-400">{me?.name ? `Welcome, ${String(me.name).split(" ")[0]}` : "A few quick questions"}</p>
          </div>
          <button onClick={() => finish(true)} className="text-xs text-gray-400 hover:text-gray-200 shrink-0">Skip setup</button>
        </div>

        {/* Progress. Named steps, not a bare bar: "3 of 5" says nothing about
            what is left, and what is left is what decides whether to continue. */}
        <div className="flex items-center gap-1.5 mb-6">
          {OB_STEPS.map((label, i) => (
            <div key={label} className="flex-1">
              <div className={`h-1.5 rounded-full transition-colors ${i <= step ? "bg-indigo-500" : "bg-gray-700"}`} />
              <p className={`mt-1.5 text-[10px] truncate ${i === step ? "text-indigo-300" : "text-gray-400"}`}>{label}</p>
            </div>
          ))}
        </div>

        {step === 0 && (
          <div className={card}>
            <h2 className="text-lg font-semibold mb-2">Your AI job search assistant</h2>
            <p className="text-sm text-gray-300 mb-4">Job Hunter searches for roles that fit you, scores each one against your CV, and lines up the good ones for you to review. Three things to set up and you're done.</p>
            <ul className="space-y-2.5 mb-5">
              {[[FileText, "Your CV", "So matches are scored against your real experience"],
                [SearchIcon, "What you're looking for", "Titles, skills and locations — read from your CV"],
                [Bell, "When to run and how to hear about it", "A daily search, and an alert when there is something good"]].map(([Icon, t, s]: any, i) => (
                <li key={i} className="flex items-start gap-3">
                  <div className="w-8 h-8 rounded-lg bg-indigo-600/20 flex items-center justify-center shrink-0 mt-0.5"><Icon className="w-4 h-4 text-indigo-300" /></div>
                  <div><p className="text-sm font-medium">{t}</p><p className="text-xs text-gray-400">{s}</p></div>
                </li>
              ))}
            </ul>
            <button onClick={next} className="w-full py-3.5 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold">Get started</button>
          </div>
        )}

        {step === 1 && (
          <div className={card}>
            <h2 className="text-lg font-semibold mb-1">Upload your CV</h2>
            <p className="text-sm text-gray-400 mb-4">A PDF. We read it to fill in the next step, and score every job against it.</p>
            {cvName && (
              <div className="flex items-center gap-3 p-3 mb-3 bg-gray-900/60 rounded-xl border border-gray-700">
                <div className="w-9 h-9 rounded-lg bg-indigo-600/20 flex items-center justify-center shrink-0"><FileText className="w-5 h-5 text-indigo-300" /></div>
                <p className="text-sm font-medium truncate">{cvName}</p>
              </div>
            )}
            <label className={`block border-2 border-dashed border-gray-700 rounded-xl p-8 text-center cursor-pointer bg-gray-900/40 hover:border-indigo-500 ${busy ? "opacity-60 pointer-events-none" : ""}`}>
              <input type="file" accept=".pdf" className="hidden" onChange={onPick} />
              {busy ? <Loader2 className="w-6 h-6 text-indigo-400 animate-spin mx-auto mb-2" /> : <FileText className="w-6 h-6 text-gray-400 mx-auto mb-2" />}
              <p className="text-sm text-gray-300">{cvName ? "Choose a different PDF" : "Choose your CV (PDF)"}</p>
              <p className="text-xs text-gray-400 mt-0.5">Max 5MB</p>
            </label>
            {cvMsg && <p className="text-xs text-gray-300 mt-3">{cvMsg}</p>}
            <div className="flex gap-2 mt-5">
              <button onClick={back} className="px-4 py-3 bg-gray-700 rounded-xl text-sm font-medium">Back</button>
              <button onClick={next} disabled={busy} className="flex-1 py-3 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold disabled:opacity-60">{cvName ? "Next" : "Skip for now"}</button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className={card}>
            <h2 className="text-lg font-semibold mb-1">Your job profile</h2>
            <p className="text-sm text-gray-400 mb-4">{cvName ? "Read from your CV. Change anything that looks wrong — this is what the search actually uses." : "What should we search for?"}</p>
            <div className="space-y-5">
              <div><h3 className="font-medium text-sm mb-2 flex items-center gap-2"><Briefcase className="w-4 h-4 text-indigo-400" />Job titles</h3><TagInput value={titles} onChange={setTitles} noun="title" example="VP Product" /></div>
              <div><h3 className="font-medium text-sm mb-2 flex items-center gap-2"><Sparkles className="w-4 h-4 text-indigo-400" />Skills &amp; keywords</h3><TagInput value={keywords} onChange={setKeywords} noun="skill" example="experimentation" /></div>
              <div><h3 className="font-medium text-sm mb-2 flex items-center gap-2"><MapPin className="w-4 h-4 text-indigo-400" />Locations</h3><TagInput value={locations} onChange={setLocations} noun="location" example="Tel Aviv" /></div>
            </div>
            <div className="flex gap-2 mt-5">
              <button onClick={back} className="px-4 py-3 bg-gray-700 rounded-xl text-sm font-medium">Back</button>
              <button onClick={next} className="flex-1 py-3 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold">Next</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className={card}>
            <h2 className="text-lg font-semibold mb-1">When should we look?</h2>
            <p className="text-sm text-gray-400 mb-4">Job Hunter runs on its own and has results waiting for you.</p>
            <div className="grid grid-cols-2 gap-2 mb-4">
              {[["daily", "Every day"], ["weekly", "Once a week"]].map(([id, label]) => (
                <button key={id} onClick={() => setFreq(id)}
                  className={`py-3 rounded-xl border text-sm font-medium ${freq === id ? "bg-indigo-600 border-indigo-500" : "bg-gray-900/40 border-gray-700 text-gray-400 hover:bg-gray-700/50"}`}>{label}</button>
              ))}
            </div>
            {freq === "weekly" && (
              <div className="mb-3">
                <p className="text-sm font-medium mb-2">Which day?</p>
                <div className="grid grid-cols-7 gap-1.5">
                  {DOW.map((d, i) => (
                    <button key={d} onClick={() => setSearchDow(i)}
                      className={`py-2.5 rounded-lg border text-xs font-medium ${searchDow === i ? "bg-indigo-600 border-indigo-500" : "bg-gray-900/40 border-gray-700 text-gray-400 hover:bg-gray-700/50"}`}>{d}</button>
                  ))}
                </div>
              </div>
            )}
            <Field label="Search at" sub="Your local time"><Select value={searchHour} onChange={setSearchHour} options={hours} fmt={fmtHour} /></Field>
            <div className="flex gap-2 mt-5">
              <button onClick={back} className="px-4 py-3 bg-gray-700 rounded-xl text-sm font-medium">Back</button>
              <button onClick={next} className="flex-1 py-3 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold">Next</button>
            </div>
          </div>
        )}

        {step === 4 && (
          <div className={card}>
            <h2 className="text-lg font-semibold mb-1">Stay notified</h2>
            <p className="text-sm text-gray-400 mb-4">Optional. Without it you'll just find the results waiting next time you open the app.</p>
            {perm !== "unsupported" && (
              <button onClick={enableNotifs} disabled={perm === "granted"} className="w-full py-3 bg-gray-900/40 border border-gray-700 rounded-xl font-medium disabled:opacity-60 mb-2">
                {perm === "granted" ? "✓ Push notifications on" : "Enable push notifications"}
              </button>
            )}
            {pushMsg && <p className="text-xs text-gray-400 mb-3">{pushMsg}</p>}
            <NotificationChannels me={me} />
            <div className="flex gap-2 mt-5">
              <button onClick={back} className="px-4 py-3 bg-gray-700 rounded-xl text-sm font-medium">Back</button>
              <button onClick={next} className="flex-1 py-3 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold">Next</button>
            </div>
          </div>
        )}

        {step === 5 && (
          <div className={`${card} text-center`}>
            <div className="w-16 h-16 rounded-2xl bg-green-500/15 border border-green-600/40 flex items-center justify-center mx-auto mb-4"><CheckCircle className="w-8 h-8 text-green-400" /></div>
            <h2 className="text-lg font-semibold mb-1">You're all set</h2>
            <p className="text-sm text-gray-400 mb-5">
              {titles.length
                ? `We'll look for ${titles.slice(0, 2).join(" and ")}${titles.length > 2 ? ` and ${titles.length - 2} more` : ""}${locations.length ? ` in ${locations.slice(0, 2).join(", ")}` : ""}.`
                : "You can add what you're looking for any time in Settings."}
            </p>
            <button onClick={() => finish(false)} disabled={finishing} className="w-full py-3.5 bg-gradient-to-r from-indigo-600 to-indigo-700 rounded-xl font-semibold disabled:opacity-60 flex items-center justify-center gap-2">
              {finishing ? <Loader2 className="w-5 h-5 animate-spin" /> : null}{finishing ? "Saving…" : "Start reviewing jobs"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** Upload a CV. Extracted so onboarding and settings cannot drift apart - the
 *  base64 framing is the API's, not the caller's, and duplicating it is how the
 *  two screens end up disagreeing about what "uploaded" means. */
async function uploadCv(file: File): Promise<{ filename?: string; uploaded_date?: string }> {
  const buf = await file.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const res = await fetch("/api/upload-cv", {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, data: btoa(bin) }),
  });
  if (!res.ok) throw new Error("Upload failed");
  try { return await res.json(); } catch { return {}; }
}

function NotificationChannels({ me }: { me: any }) {
  /* Telegram, WhatsApp and email could not be configured from /app at all -
     web push was the only channel here, while the legacy settings page carried
     all four. These are also the exact three fields crypto.py encrypts at rest
     (telegram_token, twilio_auth_token, email_smtp_pass), so until now the
     encryption had no UI that could reach it. */
  /* Stored as a comma-joined string because that is exactly what the sender
     already reads: deliver_notification() splits notification_channel on ","
     and loops (app.py:288). The single-choice UI here was the only thing
     making it look like one channel at a time. */
  const parseChannels = (v: any): string[] =>
    String(v || "none").split(",").map((c: string) => c.trim()).filter((c: string) => c && c !== "none");
  const [channels, setChannels] = useState<string[]>(parseChannels(me.notification_channel));
  const has = (id: string) => channels.includes(id);
  const toggle = (id: string) =>
    setChannels((cur) => (cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id]));
  const [tgToken, setTgToken] = useState(me.telegram_token || "");
  const [tgChat, setTgChat] = useState(me.telegram_chat_id || "");
  const [waSid, setWaSid] = useState(me.twilio_account_sid || "");
  const [waToken, setWaToken] = useState(me.twilio_auth_token || "");
  const [waNumber, setWaNumber] = useState(me.whatsapp_number || "");
  const [mailTo, setMailTo] = useState(me.email_address || "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const CHANNELS: [string, string][] = [
    ["telegram", "Telegram"], ["whatsapp", "WhatsApp"], ["email", "Email"],
  ];

  const save = async () => {
    setBusy(true); setMsg("");
    try {
      await api.saveNotifications({
        // "none" rather than "" - the sender's default when the column is
        // empty is already "none", and a blank would read as one nameless
        // channel after the split.
        notification_channel: channels.length ? channels.join(",") : "none",
        telegram_token: tgToken, telegram_chat_id: tgChat,
        twilio_account_sid: waSid, twilio_auth_token: waToken,
        whatsapp_number: waNumber, email_address: mailTo,
      });
      setMsg("\u2713 Saved");
    } catch (e: any) { setMsg(e?.message || "Could not save"); }
    finally { setBusy(false); }
  };

  /* One test per selected channel: /api/test-notification takes a single
     channel, and a test that only exercised the first one would have said
     "sent" while the other two were still misconfigured. */
  const test = async () => {
    setBusy(true); setMsg("Sending\u2026");
    const okd: string[] = [], bad: string[] = [];
    for (const ch of channels) {
      try {
        const r = await api.testNotification(ch);
        // Say WHY a channel failed - "Failed: telegram" alone left nothing to act on.
        if (r?.error || r?.success === false) bad.push(`${ch} (${r?.error || "no reason given"})`);
        else okd.push(ch);
      } catch (e: any) { bad.push(`${ch} (${e?.message || "request failed"})`); }
    }
    const parts: string[] = [];
    if (okd.length) parts.push("\u2713 Sent to " + okd.join(", "));
    if (bad.length) parts.push("Failed: " + bad.join(", "));
    setMsg(parts.join(" \u00b7 ") || "Pick a channel first");
    setBusy(false);
  };

  const input = "w-full px-4 py-3 border border-gray-500 rounded-xl bg-gray-800 text-white text-sm";
  return (
    <div>
      <h3 className="font-semibold text-white mb-1 flex items-center gap-2"><Send className="w-5 h-5 text-sky-400" />Notification channels</h3>
      <p className="text-xs text-gray-400 mb-3">Pick any combination — alerts go to all of them.</p>
      <div className="grid grid-cols-3 gap-2 mb-3">
        {CHANNELS.map(([id, label]) => (
          <button key={id} onClick={() => toggle(id)} aria-pressed={has(id)}
            className={`py-2.5 rounded-xl border text-xs font-medium transition-colors flex items-center justify-center gap-1.5 ${has(id) ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-800 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
            {has(id) && <Check className="w-3.5 h-3.5" />}{label}
          </button>
        ))}
      </div>
      {!channels.length && <p className="text-xs text-gray-400 mb-3">No channels selected — only in-app and push alerts.</p>}
      {has("telegram") && (
        <div className="space-y-2">
          <input value={tgToken} onChange={(e) => setTgToken(e.target.value)} placeholder="Bot token" className={input} />
          <input value={tgChat} onChange={(e) => setTgChat(e.target.value)} placeholder="Chat ID" className={input} />
        </div>
      )}
      {has("whatsapp") && (
        <div className="space-y-2">
          <input value={waSid} onChange={(e) => setWaSid(e.target.value)} placeholder="Twilio account SID" className={input} />
          <input value={waToken} onChange={(e) => setWaToken(e.target.value)} placeholder="Twilio auth token" className={input} />
          <input value={waNumber} onChange={(e) => setWaNumber(e.target.value)} placeholder="WhatsApp number" className={input} />
        </div>
      )}
      {has("email") && (
        <input value={mailTo} onChange={(e) => setMailTo(e.target.value)} placeholder="Send alerts to…" className={input} />
      )}
      <div className="flex gap-2 mt-3">
        <button onClick={save} disabled={busy} className="flex-1 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-xl text-sm font-medium disabled:opacity-60">Save channels</button>
        <button onClick={test} disabled={busy || !channels.length} className="flex-1 py-2.5 bg-indigo-600/15 border border-indigo-600/40 text-indigo-200 rounded-xl text-sm font-medium disabled:opacity-60">Send test</button>
      </div>
      {msg && <p className="text-xs text-gray-400 mt-2">{msg}</p>}
    </div>
  );
}

/* Typing a password you cannot see, three times, is how people end up locked
   out of an account they were trying to secure. Each field reveals on its own:
   one shared switch would put the new password on screen while the user is
   still looking at the old one. */
function PasswordInput({ value, onChange, placeholder, autoComplete }: { value: string; onChange: (v: string) => void; placeholder: string; autoComplete: string }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input type={show ? "text" : "password"} autoComplete={autoComplete} value={value}
             onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
             className="w-full px-4 py-3 pr-12 border border-gray-500 rounded-xl bg-gray-800 text-white text-sm" />
      <button type="button" onClick={() => setShow((v) => !v)}
              aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-2 text-gray-400 hover:text-gray-200 rounded-lg">
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);

  const submit = async () => {
    setMsg(""); setOk(false);
    if (next.length < 8) { setMsg("New password must be at least 8 characters."); return; }
    if (next !== confirm) { setMsg("The two new passwords do not match."); return; }
    setBusy(true);
    try {
      const r = await api.changePassword(current, next);
      if (r?.error) { setMsg(r.error); }
      else {
        setOk(true);
        /* Said out loud because it is the point of changing a password: any
           other session - the one you are worried about - is now dead. */
        setMsg("\u2713 Password changed. Every other signed-in device has been signed out.");
        setCurrent(""); setNext(""); setConfirm("");
      }
    } catch (e: any) { setMsg(e?.message || "Could not change password"); }
    finally { setBusy(false); }
  };

  const input = "w-full px-4 py-3 border border-gray-500 rounded-xl bg-gray-800 text-white text-sm";
  return (
    <div>
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><ShieldCheck className="w-5 h-5 text-gray-400" />Change password</h3>
      <div className="space-y-2">
        <PasswordInput value={current} onChange={setCurrent} placeholder="Current password" autoComplete="current-password" />
        <PasswordInput value={next} onChange={setNext} placeholder="New password" autoComplete="new-password" />
        <PasswordInput value={confirm} onChange={setConfirm} placeholder="Repeat new password" autoComplete="new-password" />
      </div>
      <button onClick={submit} disabled={busy || !current || !next} className="w-full mt-3 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-xl text-sm font-medium disabled:opacity-60">
        {busy ? "Changing\u2026" : "Change password"}
      </button>
      {msg && <p className={`text-xs mt-2 ${ok ? "text-green-400" : "text-amber-400"}`}>{msg}</p>}
    </div>
  );
}

type SettingsTab = "profile" | "prefs" | "cv" | "alerts" | "plan";
/* Four tabs, not five. "Profile" and "Account" were the same question asked
   twice - who am I and how do I sign in - and splitting them meant your name
   lived one tab away from your password. */
const SETTINGS_TABS: [SettingsTab, string, any][] = [
  ["profile", "Profile", Users], ["prefs", "Job preferences", Briefcase],
  ["cv", "Resume / CV", FileText], ["alerts", "Alerts & schedule", Bell],
  ["plan", "Plan", Sparkles],
];

const METER_LABELS: Record<string, string> = {
  on_demand_search: "Searches you start",
  cover_letter: "Cover letters",
  cv_review: "CV reviews",
  tailored_cv: "Tailored CVs",
};

/* What the plan includes and how much is used. Read-only: the numbers are the
   server's (plans.py via /api/me), and upgrades open with billing (Step 11). */
function PlanPanel({ me }: { me: any }) {
  const info = me?.plan_info;
  if (!info) return <p className="text-sm text-gray-400">Plan details are not available yet.</p>;
  const promo = info.plan_until && info.plan_source === "promotion";
  const lim = info.limits || {};
  const usage = me?.usage || {};
  const fmt = (v: any) => (v === null ? "Unlimited" : v === 0 || v === false ? "Not included" : String(v));
  return (<>
    <div className="bg-gradient-to-br from-indigo-600/20 to-gray-800 rounded-2xl border border-indigo-500/30 p-5">
      <p className="text-xs uppercase tracking-wide text-indigo-300">Your plan</p>
      <p className="text-2xl font-bold text-white mt-1">{info.label}</p>
      {promo && <p className="text-sm text-gray-300 mt-2">Free Premium until <b>{fmtPlanDate(info.plan_until)}</b>
        {info.days_left != null && <> ({info.days_left} day{info.days_left === 1 ? "" : "s"} left)</>}.
        After that you move to the {String(info.plan_after || "free").replace(/^./, (c: string) => c.toUpperCase())} plan.</p>}
      {info.tier !== "admin" && <p className="text-xs text-gray-400 mt-3">Upgrade options open soon.</p>}
    </div>
    <div>
      <h3 className="font-semibold text-white mb-3">Usage</h3>
      <div className="space-y-2">
        {Object.entries(METER_LABELS).map(([k, label]) => {
          const u = usage[k]; if (!u) return null;
          const pct = u.limit ? Math.min(100, Math.round((u.used / u.limit) * 100)) : 0;
          return (
            <div key={k} className="p-3.5 bg-gray-800 rounded-xl border border-gray-700">
              <div className="flex justify-between text-sm"><span className="text-white">{label}</span>
                <span className="text-gray-300">{u.limit === null ? `${u.used} · unlimited` : u.limit === 0 ? "Not included" : `${u.used} of ${u.limit}`}
                  <span className="text-gray-400"> {u.period === "day" ? "today" : "this month"}</span></span></div>
              {u.limit ? <div className="h-1.5 bg-gray-700 rounded-full mt-2 overflow-hidden"><div className={`h-full rounded-full ${pct >= 100 ? "bg-amber-400" : "bg-indigo-500"}`} style={{ width: `${pct}%` }} /></div> : null}
            </div>);
        })}
      </div>
    </div>
    <div>
      <h3 className="font-semibold text-white mb-3">Included</h3>
      <div className="grid grid-cols-2 gap-2 text-sm">
        {[["Matches per search", fmt(lim.matches_per_search)], ["Automatic search", lim.search_cadence === "weekly" ? "Weekly" : "Daily"],
          ["Save for later", lim.deferred_list ? "Yes" : "Not included"], ["AI calls per day", fmt(lim.ai_calls_per_day)],
          ["History kept", lim.history_days === null ? "All of it" : lim.history_days >= 365 ? "12 months" : `${lim.history_days} days`],
          ["Cover letters", lim.cover_letter === null ? "Unlimited" : lim.cover_letter ? `${lim.cover_letter} / month` : "Not included"],
          ["Tailored CVs", lim.tailored_cv === null ? "Unlimited" : lim.tailored_cv ? `${lim.tailored_cv} / month` : "Not included"]].map(([k, v]) => (
          <div key={k} className="p-3 bg-gray-800 rounded-xl border border-gray-700"><p className="text-xs text-gray-400">{k}</p><p className="text-white font-medium">{v}</p></div>
        ))}
      </div>
    </div>
  </>);
}

/* Step 6: the user's own data. Export is one zip (everything we hold about
   them as JSON, plus their CV). Deletion is scheduled 14 days ahead and signs
   them out everywhere; signing in before then cancels it. Typing the email is
   the confirmation the server also requires. */
function YourData({ me }: { me: any }) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const del = async () => {
    setBusy(true); setMsg("");
    try {
      await api.deleteAccount(typed.trim());
      window.location.href = "/login?deleted=1";
    } catch (e: any) { setMsg(e?.message || "Couldn't schedule the deletion"); setBusy(false); }
  };
  return (
    <div>
      <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><FileText className="w-5 h-5 text-gray-400" />Your data</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        <a href="/api/me/export" className="flex items-center justify-center gap-2 py-2.5 bg-gray-800 border border-gray-700 hover:bg-gray-700 text-gray-200 rounded-xl text-sm font-medium"><ExternalLink className="w-4 h-4" />Export my data</a>
        <button onClick={() => setOpen((v) => !v)} className="flex items-center justify-center gap-2 py-2.5 bg-gray-800 border border-red-500/40 hover:bg-red-500/10 text-red-300 rounded-xl text-sm font-medium"><Trash2 className="w-4 h-4" />Delete my account</button>
      </div>
      <p className="text-xs text-gray-400 mt-2">The export is a zip with your profile, jobs, activity and settings, plus your CV. Passwords and access tokens are never included.</p>
      {open && (
        <div className="mt-3 p-4 rounded-xl border border-red-500/40 bg-red-500/5">
          <p className="text-sm text-white font-medium">Delete your account?</p>
          <p className="text-xs text-gray-300 mt-1">You'll be signed out everywhere now. Your account, CV, jobs and settings are deleted permanently in 14 days. Sign in before then to keep everything.</p>
          <label className="block text-xs text-gray-400 mt-3 mb-1">Type <b className="text-gray-200">{me?.email}</b> to confirm</label>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" className="w-full px-3 py-2 border border-gray-600 rounded-lg bg-gray-900 text-white text-sm" />
          <button onClick={del} disabled={busy || typed.trim().toLowerCase() !== String(me?.email || "").toLowerCase()}
            className="mt-3 w-full py-2.5 rounded-xl text-sm font-semibold bg-red-600 hover:bg-red-500 text-white disabled:opacity-40">
            {busy ? "Scheduling\u2026" : "Delete my account in 14 days"}</button>
          {msg && <p className="text-xs text-red-300 mt-2">{msg}</p>}
        </div>
      )}
    </div>
  );
}

function SettingsModal({ me, onClose, initialTab }: { me: Me & any; onClose: () => void; initialTab?: SettingsTab }) {
  const parseTitles = (v: any): string[] => { if (!v) return []; if (Array.isArray(v)) return v; try { const a = JSON.parse(v); return Array.isArray(a) ? a : String(v).split(",").map((s) => s.trim()).filter(Boolean); } catch { return String(v).split(",").map((s) => s.trim()).filter(Boolean); } };
  const [titles, setTitles] = useState<string[]>(parseTitles(me.job_titles));
  const [keywords, setKeywords] = useState<string[]>(parseTitles(me.keywords));
  const [locations, setLocations] = useState<string[]>(parseTitles(me.locations));
  const [fullName, setFullName] = useState(me.name || "");
  const [linkedin, setLinkedin] = useState(me.linkedin_url || "");
  const [phone, setPhone] = useState(me.phone || "");
  /* Blank = not answered, read as "no" by the apply engine (Eran, 2026-11-07). */
  const [sponsorLocal, setSponsorLocal] = useState<string>((me.sponsorship_local || "no").toLowerCase());
  const [salaryMin, setSalaryMin] = useState<string>("");
  const [salaryCur, setSalaryCur] = useState<string>("ILS");
  const [noticeDays, setNoticeDays] = useState<string>("30");
  useEffect(() => {
    api.applicationAnswers().then((a) => {
      if (a.salary_expectation_min) setSalaryMin(String(a.salary_expectation_min));
      if (a.salary_expectation_currency) setSalaryCur(a.salary_expectation_currency);
      if (a.notice_period_days !== undefined && a.notice_period_days !== null) setNoticeDays(String(a.notice_period_days));
    }).catch(() => {});
  }, []);
  const [sponsorAbroad, setSponsorAbroad] = useState<string>((me.sponsorship_abroad || "no").toLowerCase());
  const [email, setEmail] = useState(me.email_address || me.email || "");
  const [freq, setFreq] = useState<string>(me.schedule_frequency || "daily");
  const [searchHour, setSearchHour] = useState(String(me.search_hour ?? 11));
  const [applyHour, setApplyHour] = useState(String(me.apply_hour ?? 17));
  /* Day-of-week was collected in onboarding and then unreachable: a weekly
     user who wanted to move their search off Tuesday had nowhere to say so.
     Indexes are Python's 0=Monday (see DOW), because the value is compared
     straight against datetime.weekday(). */
  const [searchDow, setSearchDow] = useState<number>(me.search_day_of_week ?? 1);
  const [applyDow, setApplyDow] = useState<number>(me.apply_day_of_week ?? 1);
  const [autoApply, setAutoApply] = useState<boolean>(!!me.auto_apply_enabled);
  const [saving, setSaving] = useState(false); const [saved, setSaved] = useState(false);
  const [cvName, setCvName] = useState<string>(me.cv_filename || (me.cv_path ? String(me.cv_path).split("/").pop() || "" : ""));
  const [cvDate, setCvDate] = useState<string>(me.cv_uploaded_date || "");
  const [cvMsg, setCvMsg] = useState("");
  const [tab, setTab] = useState<SettingsTab>(initialTab || "profile");
  const [analyzing, setAnalyzing] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractMsg, setExtractMsg] = useState("");
  const [cvAnalysis, setCvAnalysis] = useState<CvOptimizerResult | null>(null);
  const [analyzeMsg, setAnalyzeMsg] = useState("");
  const [perm, setPerm] = useState(pushState()); const [pushMsg, setPushMsg] = useState("");
  const isAdmin = me.role === "admin" || (me as any).is_admin;
  /* Entitlement comes from the server, never from a guess here: the same
     can_auto_apply() the POST is checked against. A disabled switch is a
     courtesy to an honest user - /api/save-schedule returns 403 either way. */
  const plan = (me.plan || "free").toLowerCase();
  const canAutoApply = mayAutoApply(me);
  const [gateMsg, setGateMsg] = useState("");

  useEffect(() => { if (!cvName) return; api.cvOptimizerCached().then((r) => { if (r && r.cached && !r.error) setCvAnalysis(r); }).catch(() => {}); }, []);
  const fmtDate = (iso: string) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "Asia/Jerusalem" }); };
  const analyzeCv = async () => { setAnalyzing(true); setAnalyzeMsg(""); try { const r = await api.cvOptimizer(); if (r?.error) setAnalyzeMsg(r.error); else { setCvAnalysis(r); setAnalyzeMsg(r?.cached ? "Showing your most recent analysis" : ""); } } catch (e: any) { setAnalyzeMsg(e?.message || "Analysis failed"); } finally { setAnalyzing(false); } };

  /* The gap that mattered most: /app could upload a CV and never read it, so a
     new user had to type every title, keyword and location by hand. The fields
     are filled in place rather than saved outright - the user still reviews
     them and presses Save, because an AI reading a CV gets things wrong. */
  const fillFromCv = async () => {
    setExtracting(true); setExtractMsg("");
    try {
      const r = await api.analyzeCv();
      if (r?.error) { setExtractMsg(r.error); return; }
      const t = r.job_titles || [], k = r.keywords || [], l = r.locations || [];
      if (t.length) setTitles(t);
      if (k.length) setKeywords(k);
      if (l.length) setLocations(l);
      setExtractMsg(`Read your CV \u2014 ${t.length} title(s), ${k.length} keyword(s), ${l.length} location(s). Review them, then Save.`);
    } catch (e: any) { setExtractMsg(e?.message || "Could not read the CV"); }
    finally { setExtracting(false); }
  };

  const enableNotifs = async () => { setPushMsg("Enabling…"); const r = await enablePush(); setPerm(pushState()); setPushMsg(r.ok ? "✓ Notifications enabled" : (r.reason || "Couldn't enable")); };
  const sendTest = async () => { setPushMsg("Sending…"); try { await api.pushTest(); setPushMsg("✓ Test sent — check your notifications"); } catch (e: any) { setPushMsg("Test failed: " + (e?.message || "unknown error")); } };
  const save = async () => {
    setSaving(true); setSaved(false);
    try {
      await api.saveProfile({ name: fullName, phone, linkedin_url: linkedin, job_titles: titles, keywords, locations,
                              sponsorship_local: sponsorLocal, sponsorship_abroad: sponsorAbroad });
      await api.saveApplicationAnswers({
        salary_expectation_min: salaryMin.trim() ? Number(salaryMin.replace(/,/g, "")) : null,
        salary_expectation_currency: salaryCur,
        notice_period_days: Math.max(0, Math.min(365, parseInt(noticeDays || "30", 10) || 0)),
      });
      await api.saveSchedule({
        // What the plan allows: a downgraded Free user keeps 'daily' in their
        // profile, and re-sending it would make the server refuse the whole
        // save (402) and lose the edits beside it. The scheduler runs weekly
        // for them either way.
        schedule_frequency: planLimit(me, "search_cadence") === "weekly" ? "weekly" : freq,
        // Only sent when it is allowed to be on. Sending 1 from an account
        // without the entitlement makes the whole save 403, which would lose
        // the schedule edits sitting next to it.
        ...(canAutoApply ? { auto_apply_enabled: autoApply ? 1 : 0 } : {}),
        search_hour: parseInt(searchHour, 10),
        apply_hour: parseInt(applyHour, 10),
        // Sent on every save, weekly or not: leaving the columns untouched let
        // a user switch to weekly and inherit whatever day happened to be
        // stored, with nothing on screen saying which.
        search_day_of_week: searchDow,
        apply_day_of_week: applyDow,
      });
      if (email && email !== me.email) await api.saveNotifications({ email_address: email });
      setSaved(true);
    } catch {} finally { setSaving(false); }
  };
  const onCvPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    setCvMsg("Uploading…");
    try {
      const info = await uploadCv(file);
      setCvName(info.filename || file.name);
      setCvDate(info.uploaded_date || new Date().toISOString());
      setCvAnalysis(null); setAnalyzeMsg(""); setCvMsg("✓ Uploaded");
    } catch { setCvMsg("Upload failed"); }
  };
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const fmtHour = (h: number) => { const ampm = h < 12 ? "AM" : "PM"; const hr = h % 12 === 0 ? 12 : h % 12; return `${String(hr).padStart(2, "0")}:00 ${ampm}`; };

  return (
    <>
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center sm:p-6" onClick={onClose}>
      <motion.div initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} onClick={(e) => e.stopPropagation()} className="bg-gray-900 rounded-t-3xl sm:rounded-3xl shadow-2xl max-w-xl lg:max-w-4xl w-full max-h-[88vh] overflow-y-auto no-scrollbar border border-gray-700">
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 p-5 flex items-center justify-between z-10"><h2 className="text-lg font-bold text-white">Settings</h2><button onClick={onClose} className="p-2 active:bg-gray-800 rounded-xl"><X className="w-5 h-5 text-gray-400" /></button></div>
        {/* Five tabs. One column of eleven headings is a scroll, not a settings
            page - and these are the groupings people actually think in: who I
            am, what I am looking for, my CV, when and how you tell me, my
            account. */}
        <div className="px-5 pt-4 border-b border-gray-700 flex gap-1 overflow-x-auto no-scrollbar">
          {SETTINGS_TABS.map(([id, label, Icon]) => (
            <button key={id} onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-3 py-2.5 rounded-t-lg text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${tab === id ? "border-indigo-500 text-white" : "border-transparent text-gray-400 hover:text-gray-200"}`}>
              <Icon className="w-4 h-4" />{label}
            </button>
          ))}
        </div>
        <div className="p-5 space-y-6">
          {tab === "profile" && (<>
          <div>
            <h3 className="font-semibold text-white mb-3">About you</h3>
            <div className="grid gap-3 lg:grid-cols-2">
              <LabelledInput label="Full name" value={fullName} onChange={setFullName} placeholder="Jane Cohen" />
              <LabelledInput label="Email" value={email} onChange={setEmail} placeholder="email@example.com" type="email" />
              <LabelledInput label="Phone" value={phone} onChange={setPhone} placeholder="+972 50 000 0000" type="tel" />
              <LabelledInput label="LinkedIn URL" value={linkedin} onChange={setLinkedin} placeholder="https://linkedin.com/in/you" />
            </div>
          </div>
          <div>
            <h3 className="font-semibold text-white mb-1">Application answers</h3>
            <p className="text-xs text-gray-400 mb-3">Used when a form asks about salary or availability.</p>
            <div className="grid gap-3 grid-cols-[1fr_auto] items-end">
              <LabelledInput label="Minimum salary expectation (gross, per month)" value={salaryMin} onChange={setSalaryMin} placeholder="e.g. 45,000" />
              <select value={salaryCur} onChange={(e) => setSalaryCur(e.target.value)} aria-label="Currency"
                className="h-[42px] px-3 bg-gray-800 border border-gray-700 rounded-xl text-sm text-gray-100">
                {["ILS", "USD", "EUR", "GBP"].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div className="mt-3 max-w-[12rem]">
              <LabelledInput label="Notice period (days)" value={noticeDays} onChange={setNoticeDays} placeholder="30" />
            </div>
          </div>
          <div>
            <h3 className="font-semibold text-white mb-1">Work authorization</h3>
            <p className="text-xs text-gray-400 mb-3">Used to answer visa questions on application forms.</p>
            <div className="space-y-3">
              {([["Do you need visa sponsorship to work in Israel?", sponsorLocal, setSponsorLocal],
                 ["Do you need visa sponsorship to work abroad (e.g. US, EU)?", sponsorAbroad, setSponsorAbroad]] as const).map(([q, v, set]) => (
                <div key={q} className="flex items-center justify-between gap-3">
                  <span className="text-sm text-gray-200">{q}</span>
                  <div className="flex shrink-0 rounded-lg border border-gray-700 overflow-hidden" role="group" aria-label={q}>
                    {["no", "yes"].map((opt) => (
                      <button key={opt} type="button" onClick={() => set(opt)} aria-pressed={v === opt}
                        className={`px-3 py-1.5 text-sm ${v === opt ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-300"}`}>
                        {opt === "yes" ? "Yes" : "No"}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="pt-1 border-t border-gray-700" />
          <ChangePassword />
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><ShieldCheck className="w-5 h-5 text-gray-400" />Account</h3>
            <p className="text-xs text-gray-400 mb-2">Signed in as {me?.email || me?.name}</p>
            <div className="grid gap-2 sm:grid-cols-2">
              <a href="/app?onboarding=1" className="flex items-center justify-center gap-2 py-2.5 bg-gray-800 border border-gray-700 hover:bg-gray-700 text-gray-200 rounded-xl text-sm font-medium"><Wand2 className="w-4 h-4" />Run setup again</a>
              <a href="/logout" className="flex items-center justify-center gap-2 py-2.5 bg-gray-800 border border-gray-700 hover:bg-gray-700 text-gray-200 rounded-xl text-sm font-medium"><LogOut className="w-4 h-4" />Sign out</a>
            </div>
          </div>
          {!isAdmin && <YourData me={me} />}
          </>)}
          {tab === "prefs" && (<>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Briefcase className="w-5 h-5 text-indigo-400" />Job Titles</h3><TagInput value={titles} onChange={setTitles} noun="title" example="VP Product" /></div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Sparkles className="w-5 h-5 text-indigo-400" />Key Skills & Keywords</h3><TagInput value={keywords} onChange={setKeywords} noun="skill" example="experimentation" /></div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><MapPin className="w-5 h-5 text-indigo-400" />Preferred Locations</h3><TagInput value={locations} onChange={setLocations} noun="location" example="Tel Aviv" /></div>
          </>)}
          {tab === "cv" && (<>
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><CheckCircle className="w-5 h-5 text-green-400" />Resume / CV</h3>
            {cvName && (
              <div className="flex items-center gap-3 p-3 mb-3 bg-gray-800 rounded-xl border border-gray-700">
                <div className="w-9 h-9 rounded-lg bg-indigo-600/20 flex items-center justify-center shrink-0"><FileText className="w-5 h-5 text-indigo-300" /></div>
                <div className="min-w-0 flex-1"><p className="text-sm font-medium text-white truncate">{cvName}</p><p className="text-xs text-gray-400">{cvDate ? `Uploaded ${fmtDate(cvDate)}` : "Current CV on file"}</p></div>
                <a href="/api/cv" target="_blank" rel="noopener noreferrer" className="px-3 py-2 bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 rounded-lg text-xs font-medium shrink-0 flex items-center gap-1.5"><ExternalLink className="w-3.5 h-3.5" />View</a>
              </div>
            )}
            <label className="block border-2 border-dashed border-gray-700 rounded-xl p-6 text-center active:border-indigo-500 cursor-pointer bg-gray-800/50"><input type="file" accept=".pdf" className="hidden" onChange={onCvPick} /><p className="text-gray-400 text-sm mb-1">{cvName ? "Tap to replace" : "Tap to upload"}</p><p className="text-xs text-gray-400">PDF, max 5MB</p>{cvMsg && <p className="text-xs text-gray-400 mt-1">{cvMsg}</p>}</label>
            {cvName && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mt-3">
                <button onClick={fillFromCv} disabled={extracting} className="py-3 bg-indigo-600/15 border border-indigo-600/40 text-indigo-200 rounded-xl font-medium flex items-center justify-center gap-2 disabled:opacity-60">{extracting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wand2 className="w-4 h-4" />}{extracting ? "Reading…" : "Fill profile from CV"}</button>
                <button onClick={analyzeCv} disabled={analyzing} className="py-3 bg-gray-800 border border-gray-700 text-gray-200 rounded-xl font-medium flex items-center justify-center gap-2 disabled:opacity-60">{analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}{analyzing ? "Analyzing…" : "Score my CV"}</button>
              </div>
            )}
            {extractMsg && <p className="text-xs text-gray-400 mt-2">{extractMsg}</p>}
            {analyzeMsg && <p className="text-xs text-gray-400 mt-2">{analyzeMsg}</p>}
            {cvAnalysis && <CvAnalysisPanel data={cvAnalysis} fmtDate={fmtDate} />}
          </div>
          </>)}
          {tab === "plan" && <PlanPanel me={me} />}
          {tab === "alerts" && (<>
          <div>
            <h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Clock className="w-5 h-5 text-indigo-400" />Automatic schedule</h3>
            <div className="space-y-3">
              <Field label="How often" sub={freq === "weekly" ? "Runs once a week, on the days you pick" : "Runs every day"}>
                <select value={freq} onChange={(e) => setFreq(e.target.value)} className="px-3 py-2 border border-gray-500 rounded-lg bg-gray-700 text-white text-sm shrink-0">
                  <option value="daily" disabled={planLimit(me, "search_cadence") === "weekly" && freq !== "daily"}>
                    {planLimit(me, "search_cadence") === "weekly" ? "Daily (Premium)" : "Daily"}</option>
                  <option value="weekly">Weekly</option>
                </select>
              </Field>
              <Field label="Job search" sub="Find and score new jobs"><Select value={searchHour} onChange={setSearchHour} options={hours} fmt={fmtHour} /></Field>
              {freq === "weekly" && <DayPicker label="Search day" value={searchDow} onChange={setSearchDow} />}
              <Field label="Auto-apply time" sub="When approved applications go out"><Select value={applyHour} onChange={setApplyHour} options={hours} fmt={fmtHour} /></Field>
              {freq === "weekly" && <DayPicker label="Apply day" value={applyDow} onChange={setApplyDow} />}
              <div className={`p-3.5 rounded-xl border ${canAutoApply ? "bg-gray-800 border-gray-700" : "bg-gray-800/50 border-gray-700/60"}`}>
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-white text-sm flex items-center gap-1.5">
                      {!canAutoApply && <Lock className="w-3.5 h-3.5 text-gray-400" />}Apply automatically
                    </p>
                    <p className="text-xs text-gray-400">
                      {autoApply && canAutoApply
                        ? ((me as any).auto_apply_live === false
                            ? "Auto-apply isn't running yet. Once it starts, approving a job sends the application; until then, approved jobs wait in your queue."
                            : "Approving a job sends the application straight away. If a site needs your answers or emails you a security code, you'll get a notification.")
                        : "Off: approved jobs wait in your queue until you apply."}
                    </p>
                  </div>
                  <Toggle
                    checked={autoApply && canAutoApply}
                    disabled={!canAutoApply}
                    onChange={(v: boolean) => {
                      if (!canAutoApply) { setGateMsg("Auto-apply is part of a paid plan."); return; }
                      setAutoApply(v);
                    }}
                  />
                </div>
                {!canAutoApply && (
                  <p className="text-xs text-indigo-300 mt-2">Auto-apply is part of a paid plan — you are on {plan}.</p>
                )}
                {gateMsg && canAutoApply === false && <p className="text-xs text-gray-400 mt-1">{gateMsg}</p>}
              </div>
            </div>
          </div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Bell className="w-5 h-5 text-amber-400" />Notifications</h3>{perm === "unsupported" ? (<p className="text-sm text-gray-400">This browser doesn't support push notifications.</p>) : (<div className="space-y-2"><button onClick={enableNotifs} disabled={perm === "granted"} className="w-full py-3 bg-gray-800 border border-gray-700 text-gray-200 rounded-xl font-medium disabled:opacity-60">{perm === "granted" ? "✓ Notifications enabled" : "Enable push notifications"}</button>{perm === "granted" && <button onClick={sendTest} className="w-full py-2.5 bg-gray-700 active:bg-gray-600 text-gray-200 rounded-xl text-sm font-medium">Send test notification</button>}{pushMsg && <p className="text-xs text-gray-400">{pushMsg}</p>}</div>)}</div>
          <NotificationChannels me={me} />
          </>)}
          <button onClick={save} disabled={saving} className="w-full py-4 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl font-semibold disabled:opacity-60 flex items-center justify-center gap-2 lg:sticky lg:bottom-0">{saving ? <Loader2 className="w-5 h-5 animate-spin" /> : saved ? <CheckCircle className="w-5 h-5" /> : null}{saving ? "Saving…" : saved ? "Saved" : "Save Settings"}</button>
        </div>
      </motion.div>
    </motion.div>
    </>
  );
}

function CvAnalysisPanel({ data, fmtDate }: { data: CvOptimizerResult; fmtDate: (s: string) => string }) {
  const score = typeof data.score === "number" ? data.score : null;
  const scoreColor = score == null ? "text-gray-300" : score >= 80 ? "text-green-400" : score >= 60 ? "text-amber-400" : "text-red-400";
  return (
    <div className="mt-3 bg-gray-800 rounded-xl border border-gray-700 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2"><Sparkles className="w-4 h-4 text-indigo-300" /><span className="text-sm font-semibold text-white">AI CV Review</span></div>
        {score != null && <div className="text-right"><span className={`text-2xl font-bold ${scoreColor}`}>{score}</span><span className="text-xs text-gray-400">/100</span>{data.score_label && <p className="text-xs text-gray-400">{data.score_label}</p>}</div>}
      </div>
      {data.summary && <p className="text-sm text-gray-300">{data.summary}</p>}
      {data.strengths && data.strengths.length > 0 && (
        <div><p className="text-xs font-semibold text-green-400 mb-1">Strengths</p><ul className="space-y-1">{data.strengths.map((s, i) => <li key={i} className="text-xs text-gray-300 flex gap-2"><Check className="w-3.5 h-3.5 text-green-400 shrink-0 mt-0.5" />{s}</li>)}</ul></div>
      )}
      {data.improvements && data.improvements.length > 0 && (
        <div><p className="text-xs font-semibold text-amber-400 mb-1">Improvements</p><ul className="space-y-1.5">{data.improvements.map((im, i) => <li key={i} className="text-xs text-gray-300"><span className="font-medium text-white">{im.title}</span>{im.detail ? ` — ${im.detail}` : ""}</li>)}</ul></div>
      )}
      {data.ats_notes && data.ats_notes.length > 0 && (
        <div><p className="text-xs font-semibold text-indigo-300 mb-1">ATS notes</p><ul className="space-y-1">{data.ats_notes.map((n, i) => <li key={i} className="text-xs text-gray-400 flex gap-2"><AlertCircle className="w-3.5 h-3.5 text-indigo-300 shrink-0 mt-0.5" />{n}</li>)}</ul></div>
      )}
      {data.analyzed_date && <p className="text-[11px] text-gray-400 pt-1">Last analyzed {fmtDate(data.analyzed_date)}</p>}
    </div>
  );
}

/* A real switch, not a checkbox with a label. Disabled still renders the
   state it is in rather than looking empty, so a locked toggle reads as "off
   and locked" instead of "broken". */
function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-disabled={!!disabled}
            onClick={() => onChange(!checked)}
            className={`relative w-12 h-7 rounded-full shrink-0 transition-colors ${checked ? "bg-indigo-600" : "bg-gray-600"} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}>
      <span className={`absolute top-1 left-1 w-5 h-5 bg-white rounded-full transition-transform ${checked ? "translate-x-5" : ""}`} />
    </button>
  );
}

function LabelledInput({ label, value, onChange, placeholder, type }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <label className="block">
      <span className="block text-xs font-medium text-gray-400 mb-1.5">{label}</span>
      <input type={type || "text"} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
             className="w-full px-4 py-3 border border-gray-500 rounded-xl bg-gray-800 text-white text-sm" />
    </label>
  );
}

function DayPicker({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="p-3.5 bg-gray-800 rounded-xl border border-gray-700">
      <p className="font-medium text-white text-sm mb-2">{label}</p>
      <div className="grid grid-cols-7 gap-1.5">
        {DOW.map((d, i) => (
          <button key={d} onClick={() => onChange(i)} aria-pressed={value === i}
            className={`py-2 rounded-lg text-xs font-medium border transition-colors ${value === i ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-900/60 border-gray-700 text-gray-400 hover:bg-gray-700"}`}>
            {d}
          </button>
        ))}
      </div>
    </div>
  );
}

function Field({ label, sub, children }: any) { return (<div className="flex items-center justify-between gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700"><div className="min-w-0"><p className="font-medium text-white text-sm">{label}</p><p className="text-xs text-gray-400">{sub}</p></div>{children}</div>); }
function Select({ value, onChange, options, fmt }: { value: string; onChange: (v: string) => void; options: number[]; fmt: (n: number) => string }) { return (<select value={value} onChange={(e) => onChange(e.target.value)} className="px-3 py-2 border border-gray-500 rounded-lg bg-gray-700 text-white text-sm shrink-0">{options.map((o) => <option key={o} value={o}>{fmt(o)}</option>)}</select>); }

function AllDoneScreen({ approvedCount, rejectedCount, deferredCount, onViewDashboard, onStartNew, empty, locked, me }: any) {
  const [searchMsg, setSearchMsg] = useState("");
  const runSearch = async () => { setSearchMsg("Starting search…"); try { await api.runSearch(); setSearchMsg("Search started — new jobs will appear in a few minutes. Pull to refresh."); } catch (e: any) { setSearchMsg(e?.message || "Couldn't start search."); } };
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 flex items-center justify-center p-6">
      <motion.div initial={{ scale: 0.85, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="text-center max-w-md w-full">
        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.15, type: "spring" }} className="w-20 h-20 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-2xl mx-auto mb-5 flex items-center justify-center shadow-lg shadow-indigo-500/20"><Briefcase className="w-10 h-10 text-white" /></motion.div>
        <h2 className="text-2xl font-bold text-white mb-2">{empty ? "No new jobs right now" : "All done!"}</h2>
        <p className="text-gray-400 mb-5">{empty ? "Run a search now, or check back after your next scheduled one." : "You've reviewed all new jobs."}</p>
        {locked?.count > 0 && <div className="mb-5 text-left"><LockedMatchesCard locked={locked} me={me} /></div>}
        {!empty && (<div className="bg-gray-800 rounded-2xl p-5 mb-5 border border-gray-700 grid grid-cols-3 gap-4"><div><div className="text-2xl font-bold text-green-400 mb-0.5">{approvedCount}</div><div className="text-xs text-gray-400">Approved</div></div><div><div className="text-2xl font-bold text-red-400 mb-0.5">{rejectedCount}</div><div className="text-xs text-gray-400">Passed</div></div><div><div className="text-2xl font-bold text-amber-400 mb-0.5">{deferredCount}</div><div className="text-xs text-gray-400">Deferred</div></div></div>)}
        <div className="space-y-3">
          <button onClick={runSearch} className="w-full py-4 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl font-semibold flex items-center justify-center gap-2"><SearchIcon className="w-5 h-5" />Run search now</button>
          <button onClick={onViewDashboard} className="w-full py-3.5 bg-gray-800 border border-gray-700 text-gray-200 rounded-xl font-medium flex items-center justify-center gap-2"><LayoutGrid className="w-5 h-5" />View Dashboard</button>
          <button onClick={onStartNew} className="w-full py-3 bg-gray-800 border border-gray-700 text-gray-300 rounded-xl font-medium flex items-center justify-center gap-2"><RefreshCw className="w-4 h-4" />Refresh</button>
        </div>
        {searchMsg && <p className="text-sm text-indigo-300 mt-4">{searchMsg}</p>}
      </motion.div>
    </div>
  );
}

function Rocket({ className }: { className?: string }) {
  return (<svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" /><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z" /><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0" /><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5" /></svg>);
}
