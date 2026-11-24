"use client";

// Shown when a page fails to load (for example the edition store cannot be reached).
// It is deliberately not the "not found" page: nothing has moved, something is down.
// The marker on <main> tells the service worker never to keep this as an offline copy.

import Link from "next/link";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <main id="main" data-edition-state="error" className="page py-12">
      <h1 className="text-[2rem] font-semibold leading-tight">This didn&rsquo;t load</h1>
      <p className="mt-3 max-w-[34rem] text-ink-2">
        Throughline could not reach its edition store. Nothing is wrong on your side. Try
        again in a minute.
      </p>
      <p className="label mt-6 flex flex-wrap gap-x-6">
        <button
          type="button"
          onClick={reset}
          className="inline-flex min-h-11 items-center font-semibold text-note underline"
        >
          Try again
        </button>
        <Link href="/" className="inline-flex min-h-11 items-center font-semibold text-note underline">
          The current edition
        </Link>
      </p>
    </main>
  );
}
