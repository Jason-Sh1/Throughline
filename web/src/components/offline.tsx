"use client";

// Registers the service worker (public/sw.js) and tells it which story pages belong to
// the edition on screen, so the whole edition can be read offline. Renders nothing.
// Only in production builds: in development a service worker gets in the way of reloads.

import { useEffect } from "react";

export function OfflineEdition({ urls }: { urls: string[] }) {
  const key = urls.join("|");
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    let cancelled = false;
    navigator.serviceWorker
      .register("/sw.js")
      .then(() => navigator.serviceWorker.ready)
      .then((registration) => {
        if (cancelled) return;
        // Scripts, styles and fonts this page already loaded, so the worker can keep
        // them too (on a first visit they were fetched before it took control).
        const assets = performance
          .getEntriesByType("resource")
          .map((entry) => entry.name)
          .filter((name) => name.startsWith(`${location.origin}/_next/static/`));
        registration.active?.postMessage({ type: "cache-edition", urls, assets });
      })
      .catch(() => {
        // No offline copy on this browser; reading online is unaffected.
      });
    return () => {
      cancelled = true;
    };
    // `key` stands in for `urls`, which is a new array on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return null;
}
