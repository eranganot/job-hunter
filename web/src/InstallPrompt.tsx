import { useEffect, useState } from "react";
import { Download, X } from "lucide-react";

// Shows a dismissible "Install app" pill when the browser offers installation
// (Android Chrome fires `beforeinstallprompt`). Hidden once installed/standalone.
export function InstallPrompt() {
  const [deferred, setDeferred] = useState<any>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    const standalone = window.matchMedia("(display-mode: standalone)").matches || (navigator as any).standalone;
    if (standalone) return;
    const onPrompt = (e: Event) => { e.preventDefault(); setDeferred(e); };
    const onInstalled = () => { setDeferred(null); setHidden(true); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!deferred || hidden) return null;

  const install = async () => {
    try { deferred.prompt(); await deferred.userChoice; } catch {}
    setDeferred(null);
  };

  return (
    <div className="fixed left-0 right-0 bottom-0 z-[60] flex justify-center px-4 safe-bottom pointer-events-none">
      <div className="pointer-events-auto mb-4 flex items-center gap-3 bg-gray-800 border border-gray-600 rounded-2xl shadow-2xl px-4 py-3 max-w-sm w-full">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-pink-600 to-purple-600 flex items-center justify-center shrink-0">
          <Download className="w-4 h-4 text-white" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white leading-tight">Install Job Hunter</p>
          <p className="text-xs text-gray-400 leading-tight">Add to your home screen</p>
        </div>
        <button onClick={install} className="px-3 py-2 bg-gradient-to-r from-pink-600 to-purple-600 text-white text-sm font-medium rounded-xl shrink-0">Install</button>
        <button onClick={() => setHidden(true)} className="p-1.5 text-gray-400 active:text-white shrink-0"><X className="w-4 h-4" /></button>
      </div>
    </div>
  );
}
