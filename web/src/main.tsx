import React from "react";
import ReactDOM from "react-dom/client";
import { SwipeFlow } from "./App";
import { InstallPrompt } from "./InstallPrompt";
import { syncPush } from "./lib/push";
import "./index.css";

// Register the PWA service worker (scope is /app/ — it never touches /api or other routes).
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" })
      .then(() => syncPush())   // self-heal push subscription on load
      .catch(() => {});
  });
  window.addEventListener("focus", () => { setTimeout(() => { syncPush(); }, 500); });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SwipeFlow />
    <InstallPrompt />
  </React.StrictMode>
);
