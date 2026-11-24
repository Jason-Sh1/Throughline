import Link from "next/link";
import { Masthead } from "@/components/masthead";

export default function NotFound() {
  return (
    <>
      <Masthead />
      <main id="main" className="page py-12">
        <h1 className="text-[2rem] font-semibold leading-tight">Not in this edition</h1>
        <p className="mt-3 max-w-[34rem] text-ink-2">
          That page isn&apos;t part of the current edition. Stories move out when a new edition
          is published.
        </p>
        <p className="label mt-6">
          <Link href="/" className="font-semibold text-note">
            Go to the current edition
          </Link>
        </p>
      </main>
    </>
  );
}
