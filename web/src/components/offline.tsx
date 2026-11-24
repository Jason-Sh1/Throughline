"use client";

// Registers the service worker (public/sw.js) and tells it which story pages belong to
// the edition on screen, so the whole edition can be read offline. Renders nothing.
//
// Only in production builds. In development it does the opposite: a worker left over
// from testing a production build on the same port would serve stale scripts and make
// edits look as if they did not apply, so any registered worker is removed.

import { useEffect } from "react";

export function OfflineEdition({ editionId, urls }: { editionId: number; urls: string[] }) {
  const key = `${editionId}|${urls.join("|")}`;
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production") {
      navigator.serviceWorker
        .getRegistrations()
        .then((regs) => Promise.all(regs.map((r) => r.unregister())))
        .then(() => caches.keys())
        .then((keys) =>
          Promise.all(keys.filter((k) => k.startsWith("throughline-")).map((k) => caches.delete(k))),
        )
        .catch(() => {});
      return;
    }
    let cancelled = false;
    const send = () => {
      if (cancelled) return;
      // Scripts, styles and fonts this page already loaded, so the worker can keep
      // them too (on a first visit they were fetched before it took control).
      const assets = performance
        .getEntriesByType("resource")
        .map((entry) => entry.name)
        .filter((name) => name.startsWith(`${location.origin}/_next/static/`));
      navigator.serviceWorker.controller?.postMessage({
        type: "cache-edition",
        editionId,
        urls,
        assets,
      });
    };
    navigator.serviceWorker
      .register("/sw.js")
      .then(() => navigator.serviceWorker.ready)
      .then(send)
      .catch(() => {
        // No offline copy on this browser; reading online is unaffected.
      });
    // When a new worker takes over (first install, or an update), it starts with empty
    // caches: tell it about the edition again.
    navigator.serviceWorker.addEventListener("controllerchange", send);
    return () => {
      cancelled = true;
      navigator.serviceWorker.removeEventListener("controllerchange", send);
    };
    // `key` stands in for `editionId` and `urls` (a new array on every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}
