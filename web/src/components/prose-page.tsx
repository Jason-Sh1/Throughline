// Layout for the plain reading pages (About our AI, Privacy, Offline).

import Link from "next/link";
import { ArrowLeftIcon } from "./icons";
import { Masthead } from "./masthead";
import { SiteFooter } from "./site-footer";

export function ProsePage({
  title,
  lede,
  children,
  footer = true,
}: {
  title: string;
  lede?: string;
  children: React.ReactNode;
  footer?: boolean;
}) {
  return (
    <>
      <Masthead />
      <main id="main" className="page pb-24">
        <p className="label pt-5 text-[0.8125rem]">
          <Link
            href="/"
            className="inline-flex min-h-11 items-center gap-1.5 text-ink-2 no-underline hover:text-ink"
          >
            <ArrowLeftIcon className="size-3.5" />
            The current edition
          </Link>
        </p>
        <h1 className="text-balance text-[2rem] font-semibold leading-[1.1] tracking-[-0.015em]">
          {title}
        </h1>
        {lede && <p className="mt-3 max-w-[36rem] text-[1.125rem] leading-[1.5]">{lede}</p>}
        <div className="prose mt-8 max-w-[38rem]">{children}</div>
        {footer && <SiteFooter />}
      </main>
    </>
  );
}
