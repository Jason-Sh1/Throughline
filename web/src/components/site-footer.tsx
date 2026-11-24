// The same short footer on every page: what this is, where the ratings come from,
// and the two pages that explain the AI and the data handling.

import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="mt-12 max-w-[36rem] space-y-2 text-[0.875rem] leading-normal text-ink-2">
      <p>
        Throughline groups coverage of the same event across outlets and writes a short briefing
        with AI. Every sentence cites the articles it came from: tap a sentence, or the small
        numbers after it, to see them.
      </p>
      <p>
        Outlet ratings are from{" "}
        <a
          href="https://www.allsides.com/media-bias/ratings"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-ink"
        >
          AllSides Media Bias Ratings
        </a>{" "}
        (CC BY-NC 4.0). A non-commercial student project: no ads, no paid tier.
      </p>
      <p className="label flex flex-wrap gap-x-5 text-[0.8125rem]">
        <Link href="/about" className="inline-flex min-h-11 items-center underline hover:text-ink">
          About our AI
        </Link>
        <Link href="/privacy" className="inline-flex min-h-11 items-center underline hover:text-ink">
          Privacy
        </Link>
      </p>
    </footer>
  );
}
