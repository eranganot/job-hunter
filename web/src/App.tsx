import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence, PanInfo } from "motion/react";
import {
  Briefcase, X, MapPin, TrendingUp, Settings, BarChart3, Clock,
  CheckCircle, XCircle, AlertCircle, Sparkles, Building2, ExternalLink,
  LayoutGrid, List, Check, RefreshCw, Bell, Search as SearchIcon,
  Zap, Target, Loader2, Plus, RotateCcw,
} from "lucide-react";
import { api, toUiJob, type UiJob, type Me, type Stats, type Activity } from "./api/client";
import { enablePush, pushState } from "./lib/push";

type View = "swipe" | "dashboard";
type DashboardTab = "queue" | "applied" | "deferred" | "passed" | "activity" | "analytics";

const REJECT_REASONS: Record<string, string> = {
  seniority: "Wrong seniority level",
  company: "Not interested in this company",
  industry: "Not interested in this industry",
};

export function SwipeFlow() {
  const [view, setView] = useState<View>("swipe");
  const [dashboardTab, setDashboardTab] = useState<DashboardTab>("queue");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [me, setMe] = useState<Me | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [newJobs, setNewJobs] = useState<UiJob[]>([]);
  const [approvedJobs, setApprovedJobs] = useState<UiJob[]>([]);
  const [appliedJobs, setAppliedJobs] = useState<UiJob[]>([]);
  const [deferredJobs, setDeferredJobs] = useState<UiJob[]>([]);

  const [currentIndex, setCurrentIndex] = useState(0);
  const [direction, setDirection] = useState<"left" | "right" | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [selectedJob, setSelectedJob] = useState<UiJob | null>(null);
  const [pendingReject, setPendingReject] = useState<UiJob | null>(null);
  const [undo, setUndo] = useState<{ type: "approve" | "defer"; job: UiJob } | null>(null);
  const [rejectedDelta, setRejectedDelta] = useState(0);
  const undoTimer = useRef<any>(null);

  // pull-to-refresh
  const [pull, setPull] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const pullStart = useRef<number | null>(null);

  // Deep-link: open Applied tab / dashboard when launched from a push.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const v = params.get("view") || window.location.hash.replace("#", "");
    if (v === "applied") { setView("dashboard"); setDashboardTab("applied"); }
    else if (v === "dashboard") setView("dashboard");
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const [meRes, statsRes, nw, ap, ad, df] = await Promise.all([
        api.me(), api.stats(),
        api.jobs("new", "match"), api.jobs("approved", "match"), api.jobs("applied", "date"), api.jobs("deferred", "date"),
      ]);
      setMe(meRes); setStats(statsRes);
      setNewJobs(nw.map(toUiJob)); setApprovedJobs(ap.map(toUiJob)); setAppliedJobs(ad.map(toUiJob)); setDeferredJobs(df.map(toUiJob));
      setCurrentIndex(0); setRejectedDelta(0); setUndo(null);
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

  const advance = () => setTimeout(() => { setCurrentIndex((i) => i + 1); setDirection(null); }, 240);

  const armUndo = (u: { type: "approve" | "defer"; job: UiJob }) => {
    setUndo(u);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setUndo(null), 5000);
  };

  const handleApprove = () => {
    const job = currentJob; if (!job || pendingReject) return;
    setDirection("right");
    setApprovedJobs((p) => [{ ...job, status: "approved" }, ...p]);
    api.approve(job.id).catch(() => {});
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
    api.reject(job.id, reason).catch(() => {});
  };

  const undoPass = () => {
    setPendingReject(null);
    setRejectedDelta((c) => Math.max(0, c - 1));
    setCurrentIndex((i) => Math.max(0, i - 1)); // server never told — just go back
  };

  const handleDefer = () => {
    const job = currentJob; if (!job || pendingReject) return;
    setDirection(null);
    setDeferredJobs((p) => [{ ...job, status: "deferred" }, ...p]);
    api.later(job.id).catch(() => {});
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

  if (loading) return <CenterState icon={<Loader2 className="w-10 h-10 text-indigo-400 animate-spin" />} title="Loading your jobs…" />;
  if (error) return <CenterState icon={<AlertCircle className="w-10 h-10 text-red-400" />} title={error} action={{ label: "Retry", onClick: loadAll }} />;

  if (view === "dashboard") {
    return (
      <DashboardView
        me={me} activeTab={dashboardTab} setActiveTab={setDashboardTab}
        onBackToSwipe={() => setView("swipe")}
        approvedJobs={approvedJobs} appliedJobs={appliedJobs} deferredJobs={deferredJobs} onUnDefer={unDefer}
        approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount}
        selectedJob={selectedJob} setSelectedJob={setSelectedJob}
        onOpenSettings={() => setShowSettings(true)} showSettings={showSettings} onCloseSettings={() => setShowSettings(false)}
      />
    );
  }

  if (currentIndex >= reviewJobs.length) {
    return (
      <>
        <AllDoneScreen approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount}
          onViewDashboard={() => setView("dashboard")} onStartNew={loadAll} empty={newJobs.length === 0} />
        <AnimatePresence>{showSettings && me && <SettingsModal me={me} onClose={() => setShowSettings(false)} />}</AnimatePresence>
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
        <div className="max-w-2xl mx-auto px-5 py-4 flex items-center justify-between">
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
        <div className="max-w-2xl mx-auto px-5 py-3">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium text-gray-300">{remainingCount} job{remainingCount !== 1 ? "s" : ""} to review</span>
            <span className="text-sm text-gray-500">{currentIndex + 1} / {reviewJobs.length}</span>
          </div>
          <div className="h-2 bg-gray-700 rounded-full overflow-hidden"><motion.div className="h-full bg-gradient-to-r from-indigo-500 to-indigo-700" initial={{ width: 0 }} animate={{ width: `${((currentIndex + 1) / reviewJobs.length) * 100}%` }} transition={{ duration: 0.3 }} /></div>
        </div>
      </div>

      <NotifyNudge />

      <div className="relative z-10 max-w-xl mx-auto px-5 py-6 pb-10">
        <AnimatePresence mode="wait">
          {currentJob && <SwipeCard key={currentJob.id} job={currentJob} direction={direction} onApprove={handleApprove} onPass={handlePass} onDefer={handleDefer} />}
        </AnimatePresence>
      </div>

      <AnimatePresence>{undo && !pendingReject && <UndoToast undo={undo} onUndo={doUndo} />}</AnimatePresence>
      <AnimatePresence>{showSettings && me && <SettingsModal me={me} onClose={() => setShowSettings(false)} />}</AnimatePresence>
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
    <div className="relative z-10 max-w-2xl mx-auto px-5 pt-3">
      <div className="flex items-center gap-3 bg-indigo-600/15 border border-indigo-600/40 rounded-2xl px-4 py-3">
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
        <div className="p-5 space-y-5">
          <div>
            <h2 className="text-xl font-bold text-white mb-2">{job.title}</h2>
            <div className="flex items-center gap-4 flex-wrap">
              {job.source && <div className="flex items-center gap-1.5 text-sm text-gray-400"><Briefcase className="w-4 h-4" /><span>{job.source.split("/")[0]}</span></div>}
              {job.timeAgo && <div className="flex items-center gap-1.5 text-sm text-gray-400"><Clock className="w-4 h-4" /><span>{job.timeAgo}</span></div>}
            </div>
          </div>
          <div className="flex gap-3">
            <ScoreBox label="Match Score" value={job.matchScore} icon={<TrendingUp className="w-4 h-4 text-blue-400" />} tone="blue" />
            <ScoreBox label="Your Strength" value={job.candidateScore} icon={<Sparkles className="w-4 h-4 text-green-400" />} tone="green" />
          </div>
          {job.whyFits && (<div className="bg-gradient-to-br from-amber-950/50 to-orange-950/50 rounded-2xl p-4 border-2 border-amber-700"><div className="flex items-center gap-2 mb-2"><div className="w-7 h-7 bg-amber-500 rounded-full flex items-center justify-center"><Sparkles className="w-4 h-4 text-white" /></div><h4 className="font-bold text-amber-400 text-sm">Why this is a strong fit</h4></div><p className="text-gray-300 text-sm leading-relaxed">{job.whyFits}</p></div>)}
          {job.description && (<div><h4 className="font-semibold text-white mb-1.5 text-sm">About the role</h4><p className="text-gray-400 text-sm leading-relaxed line-clamp-6">{job.description}</p></div>)}
          <div className={`flex items-center gap-2 px-4 py-2 rounded-xl ${job.verified ? "bg-green-950/50 text-green-400 border border-green-800" : "bg-orange-950/50 text-orange-400 border border-orange-800"}`}>
            {job.verified ? <><CheckCircle className="w-4 h-4" /><span className="text-sm font-medium">URL verified — ready to apply</span></> : <><AlertCircle className="w-4 h-4" /><span className="text-sm font-medium">URL not verified yet</span></>}
          </div>
        </div>
        <div className="p-5 pt-0 space-y-3">
          <div className="flex gap-3">
            <motion.button whileTap={{ scale: 0.95 }} onClick={onPass} className="flex-1 py-4 bg-gradient-to-r from-red-500 to-rose-600 text-white rounded-2xl font-semibold shadow-lg flex items-center justify-center gap-2"><X className="w-5 h-5" />Pass</motion.button>
            <motion.button whileTap={{ scale: 0.95 }} onClick={onApprove} className="flex-1 py-4 bg-gradient-to-r from-emerald-500 to-green-600 text-white rounded-2xl font-semibold shadow-lg flex items-center justify-center gap-2"><Check className="w-5 h-5" />Approve</motion.button>
          </div>
          <motion.button whileTap={{ scale: 0.98 }} onClick={onDefer} className="w-full py-3 bg-gray-700 active:bg-gray-600 text-gray-200 rounded-2xl font-medium flex items-center justify-center gap-2"><Clock className="w-4 h-4" />Decide Later</motion.button>
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

const TABS: [DashboardTab, string, any][] = [
  ["queue", "Queue", CheckCircle], ["applied", "Applied", Rocket], ["deferred", "Deferred", Clock],
  ["passed", "Passed", XCircle], ["activity", "Activity", List], ["analytics", "Analytics", BarChart3],
];

function DashboardView(p: any) {
  const { me, activeTab, setActiveTab, onBackToSwipe, approvedJobs, appliedJobs, deferredJobs, onUnDefer, approvedCount, rejectedCount, deferredCount, selectedJob, setSelectedJob, onOpenSettings, showSettings, onCloseSettings } = p;
  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <header className="bg-gray-800 border-b border-gray-700 safe-top">
        <div className="max-w-3xl mx-auto px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3"><div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-xl flex items-center justify-center"><Briefcase className="w-5 h-5 text-white" /></div><div><h1 className="text-lg font-bold text-white">Dashboard</h1><p className="text-xs text-gray-400">{me?.name ? `Hi ${me.name.split(" ")[0]}` : "Manage applications"}</p></div></div>
          <div className="flex items-center gap-2"><button onClick={onBackToSwipe} className="px-3.5 py-2 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl text-sm font-medium flex items-center gap-2"><Briefcase className="w-4 h-4" />Swipe</button><button onClick={onOpenSettings} className="p-2.5 bg-gray-800 rounded-xl border border-gray-700 active:bg-gray-700"><Settings className="w-5 h-5 text-gray-300" /></button></div>
        </div>
      </header>
      <div className="max-w-3xl mx-auto px-5 py-5">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
          <StatCard label="Queue" value={approvedCount} sub="Ready to apply" icon={<CheckCircle className="w-5 h-5 text-green-500" />} />
          <StatCard label="Applied" value={appliedJobs.length} sub="Submitted" icon={<Rocket className="w-5 h-5 text-blue-500" />} />
          <StatCard label="Deferred" value={deferredCount} sub="Decide later" icon={<Clock className="w-5 h-5 text-amber-500" />} />
          <StatCard label="Passed" value={rejectedCount} sub="Not a fit" icon={<XCircle className="w-5 h-5 text-red-500" />} />
        </div>
        <div className="grid grid-cols-3 gap-2 mb-4">
          {TABS.map(([id, label, Icon]) => { const active = activeTab === id; return (<button key={id} onClick={() => setActiveTab(id)} className={`flex flex-col items-center justify-center gap-1 py-3 rounded-xl border transition-colors ${active ? "bg-indigo-600 border-indigo-500 text-white" : "bg-gray-800 border-gray-700 text-gray-400 active:bg-gray-700"}`}><Icon className="w-5 h-5" /><span className="text-xs font-medium">{label}</span></button>); })}
        </div>
        <div className="bg-gray-800 rounded-xl border border-gray-700 p-4">
          {activeTab === "queue" && <ListTab jobs={approvedJobs} onSelectJob={setSelectedJob} emptyIcon={CheckCircle} emptyTitle="No jobs in queue" emptySub="Approved jobs appear here" heading={`${approvedJobs.length} ready to apply`} />}
          {activeTab === "applied" && <ListTab jobs={appliedJobs} onSelectJob={setSelectedJob} showStatus emptyIcon={Rocket} emptyTitle="Nothing applied yet" emptySub="Submitted applications appear here" heading={`${appliedJobs.length} applications submitted`} />}
          {activeTab === "deferred" && <DeferredTab jobs={deferredJobs} onSelectJob={setSelectedJob} onUnDefer={onUnDefer} />}
          {activeTab === "passed" && <EmptyTab icon={XCircle} title={`${rejectedCount} passed`} sub="Passed jobs are archived after 30 days" />}
          {activeTab === "activity" && <ActivityTab />}
          {activeTab === "analytics" && <AnalyticsTab approvedCount={approvedCount} rejectedCount={rejectedCount} deferredCount={deferredCount} appliedCount={appliedJobs.length} />}
        </div>
      </div>
      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
      <AnimatePresence>{showSettings && me && <SettingsModal me={me} onClose={onCloseSettings} />}</AnimatePresence>
    </div>
  );
}

function StatCard({ label, value, sub, icon }: any) {
  return (<div className="bg-gray-800 rounded-xl p-4 border border-gray-700"><div className="flex items-center justify-between mb-1.5"><span className="text-sm font-medium text-gray-400">{label}</span>{icon}</div><div className="text-2xl font-bold text-white">{value}</div><p className="text-xs text-gray-500 mt-0.5">{sub}</p></div>);
}

function ListTab({ jobs, onSelectJob, showStatus, emptyIcon: EI, emptyTitle, emptySub, heading }: any) {
  if (!jobs.length) return <EmptyTab icon={EI} title={emptyTitle} sub={emptySub} />;
  return (<div className="space-y-3"><h3 className="text-base font-semibold text-white mb-1">{heading}</h3>{jobs.map((job: UiJob) => <JobCard key={job.id} job={job} onSelect={onSelectJob} showStatus={showStatus} />)}</div>);
}

function DeferredTab({ jobs, onSelectJob, onUnDefer }: any) {
  if (!jobs.length) return <EmptyTab icon={Clock} title="No deferred jobs" sub="Jobs you tap 'Decide Later' on appear here" />;
  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold text-white mb-1">{jobs.length} deferred</h3>
      {jobs.map((job: UiJob) => (
        <div key={job.id} className="flex items-center gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700">
          <div className="w-12 h-12 bg-gradient-to-br from-indigo-900/40 to-slate-800 rounded-xl flex items-center justify-center shrink-0" onClick={() => onSelectJob(job)}><Building2 className="w-6 h-6 text-indigo-400" /></div>
          <div className="flex-1 min-w-0" onClick={() => onSelectJob(job)}><h4 className="font-semibold text-white truncate">{job.title}</h4><p className="text-sm text-gray-400 truncate">{job.company}</p></div>
          <button onClick={() => onUnDefer(job)} className="px-3 py-2 bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 rounded-lg text-xs font-medium shrink-0">Move to review</button>
        </div>
      ))}
    </div>
  );
}

function EmptyTab({ icon: Icon, title, sub }: any) {
  return (<div className="text-center py-12"><Icon className="w-14 h-14 text-gray-600 mx-auto mb-3" /><p className="text-gray-400">{title}</p><p className="text-sm text-gray-500 mt-1">{sub}</p></div>);
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

function AnalyticsTab({ approvedCount, rejectedCount, deferredCount, appliedCount }: any) {
  const totalReviewed = approvedCount + rejectedCount + deferredCount;
  const approvalRate = totalReviewed > 0 ? Math.round((approvedCount / totalReviewed) * 100) : 0;
  const applyRate = approvedCount > 0 ? Math.round((appliedCount / Math.max(approvedCount, appliedCount)) * 100) : 0;
  const bar = (label: string, n: number, color: string) => (<div><div className="flex items-center justify-between mb-1"><span className="text-sm text-gray-400">{label}</span><span className="text-sm font-medium text-white">{n}</span></div><div className="h-2 bg-gray-700 rounded-full overflow-hidden"><div className={`h-full ${color}`} style={{ width: `${totalReviewed ? (n / totalReviewed) * 100 : 0}%` }} /></div></div>);
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-2xl p-5 border border-blue-800/50"><h4 className="text-sm font-medium text-gray-400 mb-1">Approval Rate</h4><div className="text-3xl font-bold text-blue-400 mb-1">{approvalRate}%</div><p className="text-sm text-gray-500">{approvedCount} approved of {totalReviewed} reviewed</p></div>
        <div className="bg-gradient-to-br from-green-900/20 to-emerald-900/20 rounded-2xl p-5 border border-green-800/50"><h4 className="text-sm font-medium text-gray-400 mb-1">Applied</h4><div className="text-3xl font-bold text-green-400 mb-1">{applyRate}%</div><p className="text-sm text-gray-500">{appliedCount} submitted from your queue</p></div>
      </div>
      <div className="bg-gray-800 rounded-xl p-5 border border-gray-700"><h4 className="font-semibold text-white mb-4">Decision Distribution</h4><div className="space-y-3">{bar("Approved", approvedCount, "bg-green-500")}{bar("Passed", rejectedCount, "bg-red-500")}{bar("Deferred", deferredCount, "bg-amber-500")}</div></div>
      <div className="bg-gradient-to-br from-indigo-900/20 to-slate-800/40 rounded-2xl p-5 border border-indigo-800/40"><h4 className="font-semibold text-white mb-3 flex items-center gap-2"><Zap className="w-5 h-5 text-indigo-400" />Insights</h4><ul className="space-y-2 text-sm text-gray-300"><li className="flex items-start gap-2"><Target className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" /><span>You've reviewed {totalReviewed} job{totalReviewed !== 1 ? "s" : ""} so far.</span></li><li className="flex items-start gap-2"><Target className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" /><span>{approvalRate}% approval rate — {approvalRate > 50 ? "you're casting a wide net" : "you're being selective"}.</span></li></ul></div>
    </div>
  );
}

function JobCard({ job, onSelect, showStatus }: any) {
  return (
    <div className="flex items-center gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700 active:border-indigo-500 cursor-pointer" onClick={() => onSelect(job)}>
      <div className="w-12 h-12 bg-gradient-to-br from-indigo-900/40 to-slate-800 rounded-xl flex items-center justify-center shrink-0"><Building2 className="w-6 h-6 text-indigo-400" /></div>
      <div className="flex-1 min-w-0"><h4 className="font-semibold text-white truncate">{job.title}</h4><p className="text-sm text-gray-400 truncate">{job.company}</p><div className="flex items-center gap-3 mt-0.5">{job.location && <div className="flex items-center gap-1 text-xs text-gray-500"><MapPin className="w-3 h-3" /><span>{job.location.split(",")[0]}</span></div>}{job.timeAgo && <div className="flex items-center gap-1 text-xs text-gray-500"><Clock className="w-3 h-3" /><span>{job.timeAgo}</span></div>}</div></div>
      <div className="flex items-center gap-2 shrink-0"><div className="text-right"><div className="text-base font-bold text-indigo-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div><div className="text-[10px] text-gray-500">match</div></div>{showStatus && job.applyStatus && <StatusPill s={job.applyStatus} />}</div>
    </div>
  );
}

function StatusPill({ s }: { s: string }) {
  if (s === "submitted" || s === "applied") return <span className="px-2.5 py-1 bg-blue-900/40 text-blue-400 text-xs font-medium rounded-full">Submitted</span>;
  if (s === "confirmed") return <span className="px-2.5 py-1 bg-green-900/40 text-green-400 text-xs font-medium rounded-full flex items-center gap-1"><CheckCircle className="w-3 h-3" />Confirmed</span>;
  if (s === "failed") return <span className="px-2.5 py-1 bg-red-900/40 text-red-400 text-xs font-medium rounded-full flex items-center gap-1"><XCircle className="w-3 h-3" />Failed</span>;
  return <span className="px-2.5 py-1 bg-gray-700 text-gray-300 text-xs font-medium rounded-full">{s}</span>;
}

function JobDetailModal({ job, onClose }: { job: UiJob; onClose: () => void }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center sm:p-6" onClick={onClose}>
      <motion.div initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} onClick={(e) => e.stopPropagation()} className="bg-gray-900 rounded-t-3xl sm:rounded-3xl shadow-2xl max-w-2xl w-full max-h-[88vh] overflow-y-auto no-scrollbar border border-gray-700">
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 p-5 flex items-center justify-between z-10"><h2 className="text-lg font-bold text-white">Job Details</h2><button onClick={onClose} className="p-2 active:bg-gray-800 rounded-xl"><X className="w-5 h-5 text-gray-400" /></button></div>
        <div className="p-5 space-y-5">
          <div className="flex items-start gap-4"><div className="w-16 h-16 bg-gradient-to-br from-indigo-900/40 to-slate-800 rounded-2xl flex items-center justify-center shrink-0"><Building2 className="w-8 h-8 text-indigo-400" /></div><div className="flex-1 min-w-0"><h3 className="text-xl font-bold text-white mb-0.5">{job.title}</h3><p className="text-lg text-indigo-400 font-semibold mb-1">{job.company}</p><div className="flex items-center gap-4 text-gray-400 flex-wrap">{job.location && <div className="flex items-center gap-1"><MapPin className="w-4 h-4" /><span className="text-sm">{job.location}</span></div>}{job.timeAgo && <div className="flex items-center gap-1"><Clock className="w-4 h-4" /><span className="text-sm">{job.timeAgo}</span></div>}</div></div></div>
          <div className="grid grid-cols-2 gap-3"><div className="bg-gradient-to-br from-blue-900/20 to-indigo-900/20 rounded-xl p-4 border border-blue-800/50"><div className="text-sm text-gray-400 mb-1">Match Score</div><div className="text-2xl font-bold text-blue-400">{job.matchScore === null ? "—" : `${job.matchScore}%`}</div></div><div className="bg-gradient-to-br from-green-900/20 to-emerald-900/20 rounded-xl p-4 border border-green-800/50"><div className="text-sm text-gray-400 mb-1">Your Strength</div><div className="text-2xl font-bold text-green-400">{job.candidateScore === null ? "—" : `${job.candidateScore}%`}</div></div></div>
          {job.whyFits && <div className="bg-gradient-to-br from-amber-900/20 to-orange-900/20 rounded-xl p-4 border border-amber-800/50"><h4 className="font-bold text-amber-500 mb-1.5 flex items-center gap-2"><Sparkles className="w-5 h-5" />Why this is a strong fit</h4><p className="text-gray-300 text-sm leading-relaxed">{job.whyFits}</p></div>}
          {job.description && <div><h4 className="font-semibold text-white mb-1.5">About the role</h4><p className="text-gray-400 text-sm leading-relaxed whitespace-pre-line">{job.description}</p></div>}
          {job.url && <a href={job.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-indigo-400 text-sm font-medium">Open job posting <ExternalLink className="w-3.5 h-3.5" /></a>}
        </div>
      </motion.div>
    </motion.div>
  );
}

function TagInput({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState("");
  const add = () => { const v = draft.trim().replace(/,$/, ""); if (v && !value.includes(v)) onChange([...value, v]); setDraft(""); };
  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-2">
        {value.map((t) => (<span key={t} className="inline-flex items-center gap-1.5 pl-3 pr-2 py-1.5 bg-indigo-600/20 border border-indigo-600/50 text-indigo-200 rounded-full text-sm">{t}<button onClick={() => onChange(value.filter((x) => x !== t))} className="w-4 h-4 flex items-center justify-center rounded-full bg-indigo-500/40 active:bg-indigo-500"><X className="w-3 h-3" /></button></span>))}
        {value.length === 0 && <span className="text-xs text-gray-500">No titles yet — add one below.</span>}
      </div>
      <div className="flex gap-2"><input value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(); } }} placeholder="e.g. VP Product" className="flex-1 px-4 py-2.5 border border-gray-700 rounded-xl bg-gray-800 text-white text-sm" /><button onClick={add} className="px-3.5 bg-indigo-600 active:bg-indigo-700 text-white rounded-xl flex items-center justify-center"><Plus className="w-5 h-5" /></button></div>
      <p className="text-xs text-gray-500 mt-1.5">Press Enter to add each role.</p>
    </div>
  );
}

function SettingsModal({ me, onClose }: { me: Me & any; onClose: () => void }) {
  const parseTitles = (v: any): string[] => { if (!v) return []; if (Array.isArray(v)) return v; try { const a = JSON.parse(v); return Array.isArray(a) ? a : String(v).split(",").map((s) => s.trim()).filter(Boolean); } catch { return String(v).split(",").map((s) => s.trim()).filter(Boolean); } };
  const [titles, setTitles] = useState<string[]>(parseTitles(me.job_titles));
  const [phone, setPhone] = useState(me.phone || "");
  const [email, setEmail] = useState(me.email_address || me.email || "");
  const [searchHour, setSearchHour] = useState(String(me.search_hour ?? 11));
  const [applyHour, setApplyHour] = useState(String(me.apply_hour ?? 17));
  const [saving, setSaving] = useState(false); const [saved, setSaved] = useState(false);
  const [cvName, setCvName] = useState<string>(me.cv_path ? String(me.cv_path).split("/").pop() || "" : "");
  const [cvMsg, setCvMsg] = useState("");
  const [perm, setPerm] = useState(pushState()); const [pushMsg, setPushMsg] = useState("");

  const enableNotifs = async () => { setPushMsg("Enabling…"); const r = await enablePush(); setPerm(pushState()); setPushMsg(r.ok ? "✓ Notifications enabled" : (r.reason || "Couldn't enable")); };
  const sendTest = async () => { setPushMsg("Sending…"); try { await api.pushTest(); setPushMsg("✓ Test sent — check your notifications"); } catch { setPushMsg("Test failed"); } };
  const save = async () => { setSaving(true); setSaved(false); try { await api.saveProfile({ phone, job_titles: titles }); await api.saveSchedule({ search_hour: parseInt(searchHour, 10), apply_hour: parseInt(applyHour, 10) }); if (email && email !== me.email) await api.saveNotifications({ email_address: email }); setSaved(true); } catch {} finally { setSaving(false); } };
  const onCvPick = async (e: React.ChangeEvent<HTMLInputElement>) => { const file = e.target.files?.[0]; if (!file) return; setCvMsg("Uploading…"); try { const buf = await file.arrayBuffer(); let bin = ""; const bytes = new Uint8Array(buf); for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]); const res = await fetch("/api/upload-cv", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: file.name, data: btoa(bin) }) }); if (res.ok) { setCvName(file.name); setCvMsg("✓ Uploaded"); } else setCvMsg("Upload failed"); } catch { setCvMsg("Upload failed"); } };
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const fmtHour = (h: number) => { const ampm = h < 12 ? "AM" : "PM"; const hr = h % 12 === 0 ? 12 : h % 12; return `${String(hr).padStart(2, "0")}:00 ${ampm}`; };

  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center sm:p-6" onClick={onClose}>
      <motion.div initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} onClick={(e) => e.stopPropagation()} className="bg-gray-900 rounded-t-3xl sm:rounded-3xl shadow-2xl max-w-xl w-full max-h-[88vh] overflow-y-auto no-scrollbar border border-gray-700">
        <div className="sticky top-0 bg-gray-900 border-b border-gray-700 p-5 flex items-center justify-between z-10"><h2 className="text-lg font-bold text-white">Settings</h2><button onClick={onClose} className="p-2 active:bg-gray-800 rounded-xl"><X className="w-5 h-5 text-gray-400" /></button></div>
        <div className="p-5 space-y-6">
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Clock className="w-5 h-5 text-indigo-400" />Automatic Schedule</h3><div className="space-y-3"><Field label="Daily Job Search" sub="Run search automatically"><Select value={searchHour} onChange={setSearchHour} options={hours} fmt={fmtHour} /></Field><Field label="Daily Auto-Apply" sub="Submit approved applications"><Select value={applyHour} onChange={setApplyHour} options={hours} fmt={fmtHour} /></Field></div></div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Briefcase className="w-5 h-5 text-indigo-400" />Job Titles</h3><TagInput value={titles} onChange={setTitles} /></div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><CheckCircle className="w-5 h-5 text-green-400" />Resume / CV</h3><label className="block border-2 border-dashed border-gray-700 rounded-xl p-6 text-center active:border-indigo-500 cursor-pointer bg-gray-800/50"><input type="file" accept=".pdf,.doc,.docx" className="hidden" onChange={onCvPick} /><p className="text-gray-400 text-sm mb-1">Tap to upload</p><p className="text-xs text-gray-500">PDF or DOCX, max 5MB</p>{cvName && <p className="text-xs text-green-400 mt-2">✓ {cvName}</p>}{cvMsg && <p className="text-xs text-gray-400 mt-1">{cvMsg}</p>}</label></div>
          <div><h3 className="font-semibold text-white mb-3 flex items-center gap-2"><Bell className="w-5 h-5 text-amber-400" />Notifications</h3>{perm === "unsupported" ? (<p className="text-sm text-gray-400">This browser doesn't support push notifications.</p>) : (<div className="space-y-2"><button onClick={enableNotifs} disabled={perm === "granted"} className="w-full py-3 bg-gray-800 border border-gray-700 text-gray-200 rounded-xl font-medium disabled:opacity-60">{perm === "granted" ? "✓ Notifications enabled" : "Enable push notifications"}</button>{perm === "granted" && <button onClick={sendTest} className="w-full py-2.5 bg-gray-700 active:bg-gray-600 text-gray-200 rounded-xl text-sm font-medium">Send test notification</button>}{pushMsg && <p className="text-xs text-gray-400">{pushMsg}</p>}</div>)}</div>
          <div><h3 className="font-semibold text-white mb-3">Contact Information</h3><div className="space-y-3"><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="email@example.com" className="w-full px-4 py-3 border border-gray-700 rounded-xl bg-gray-800 text-white text-sm" /><input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="Phone number" className="w-full px-4 py-3 border border-gray-700 rounded-xl bg-gray-800 text-white text-sm" /></div></div>
          <button onClick={save} disabled={saving} className="w-full py-4 bg-gradient-to-r from-indigo-600 to-indigo-700 text-white rounded-xl font-semibold disabled:opacity-60 flex items-center justify-center gap-2">{saving ? <Loader2 className="w-5 h-5 animate-spin" /> : saved ? <CheckCircle className="w-5 h-5" /> : null}{saving ? "Saving…" : saved ? "Saved" : "Save Settings"}</button>
        </div>
      </motion.div>
    </motion.div>
  );
}

function Field({ label, sub, children }: any) { return (<div className="flex items-center justify-between gap-3 p-3.5 bg-gray-800 rounded-xl border border-gray-700"><div className="min-w-0"><p className="font-medium text-white text-sm">{label}</p><p className="text-xs text-gray-400">{sub}</p></div>{children}</div>); }
function Select({ value, onChange, options, fmt }: { value: string; onChange: (v: string) => void; options: number[]; fmt: (n: number) => string }) { return (<select value={value} onChange={(e) => onChange(e.target.value)} className="px-3 py-2 border border-gray-700 rounded-lg bg-gray-700 text-white text-sm shrink-0">{options.map((o) => <option key={o} value={o}>{fmt(o)}</option>)}</select>); }

function AllDoneScreen({ approvedCount, rejectedCount, deferredCount, onViewDashboard, onStartNew, empty }: any) {
  const [searchMsg, setSearchMsg] = useState("");
  const runSearch = async () => { setSearchMsg("Starting search…"); try { await api.runSearch(); setSearchMsg("Search started — new jobs will appear in a few minutes. Pull to refresh."); } catch { setSearchMsg("Couldn't start search."); } };
  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-slate-900 to-gray-900 flex items-center justify-center p-6">
      <motion.div initial={{ scale: 0.85, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} className="text-center max-w-md w-full">
        <motion.div initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.15, type: "spring" }} className="w-20 h-20 bg-gradient-to-br from-indigo-500 to-indigo-700 rounded-2xl mx-auto mb-5 flex items-center justify-center shadow-lg shadow-indigo-500/20"><Briefcase className="w-10 h-10 text-white" /></motion.div>
        <h2 className="text-2xl font-bold text-white mb-2">{empty ? "No new jobs right now" : "All done!"}</h2>
        <p className="text-gray-400 mb-5">{empty ? "Run a search now, or check back after your next scheduled one." : "You've reviewed all new jobs."}</p>
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
