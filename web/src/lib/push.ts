import { api } from "../api/client";

function urlB64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export type PushState = NotificationPermission | "unsupported";

export function pushState(): PushState {
  if (!("Notification" in window) || !("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  return Notification.permission;
}

export async function enablePush(): Promise<{ ok: boolean; reason?: string }> {
  if (pushState() === "unsupported") return { ok: false, reason: "This browser doesn't support notifications." };
  let perm = Notification.permission;
  if (perm === "default") perm = await Notification.requestPermission();
  if (perm !== "granted") return { ok: false, reason: "Notifications permission was denied." };
  const reg = await navigator.serviceWorker.ready;
  const { publicKey } = await api.pushPublicKey();
  if (!publicKey) return { ok: false, reason: "Push isn't configured on the server yet." };
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(publicKey) as unknown as BufferSource,
    });
  }
  await api.pushSubscribe(sub.toJSON());
  try { localStorage.setItem("jh_vapid_key", publicKey); } catch {}
  return { ok: true };
}

// Self-heal: re-register a rotated/wiped push subscription on every app load
// when permission is already granted. Fixes "notifications silently stopped"
// after a redeploy loses the subscription row or Chrome rotates the endpoint.
// Silent — never throws into app startup.
export async function syncPush(): Promise<void> {
  try {
    if (pushState() !== "granted") return;
    const reg = await navigator.serviceWorker.ready;
    const { publicKey } = await api.pushPublicKey();
    if (!publicKey) return;
    let sub = await reg.pushManager.getSubscription();
    let lastKey: string | null = null;
    try { lastKey = localStorage.getItem("jh_vapid_key"); } catch {}
    if (sub && lastKey && lastKey !== publicKey) { try { await sub.unsubscribe(); } catch {} sub = null; }
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(publicKey) as unknown as BufferSource,
      });
    }
    await api.pushSubscribe(sub.toJSON());
    try { localStorage.setItem("jh_vapid_key", publicKey); } catch {}
  } catch { /* silent */ }
}
