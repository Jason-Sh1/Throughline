// Privacy notice. It describes what this code does today; change it in the same pull
// request as any change to what is collected (accounts arrive in M6).

import type { Metadata } from "next";
import { ProsePage } from "@/components/prose-page";

export const metadata: Metadata = {
  title: "Privacy",
  description: "What Throughline stores about you: almost nothing.",
};

export default function PrivacyPage() {
  return (
    <ProsePage
      title="Privacy"
      lede="Throughline has no accounts, no ads and no analytics. This page lists everything that is stored, and where."
    >
      <h2>On your device</h2>
      <ul>
        <li>
          <strong>Which stories you opened</strong> in each edition, so the progress line and
          &ldquo;You&rsquo;re caught up&rdquo; work. This is kept in your browser&rsquo;s local
          storage. It is never sent to us or anyone else, and clearing your browser&rsquo;s site
          data removes it.
        </li>
        <li>
          <strong>A copy of the latest edition</strong>, if your browser supports it, so you can
          read it without a connection. Clearing site data removes this too.
        </li>
      </ul>

      <h2>On our side</h2>
      <ul>
        <li>
          We do not ask who you are. There is no sign-up, no cookie that identifies you and no
          tracking script.
        </li>
        <li>
          Like any website, the company that hosts Throughline receives your IP address and
          browser type with each request and may keep them in short-lived server logs. We do
          not use them to build a profile.
        </li>
        <li>The typefaces are served from our own site, not from a font service.</li>
      </ul>

      <h2>When you follow a link</h2>
      <p>
        Every source link opens the article on its publisher&rsquo;s own site. From there, that
        publisher&rsquo;s privacy policy applies, not this one.
      </p>

      <h2>News content</h2>
      <p>
        To write the briefings we store headlines, links and the short summaries
        publishers put in their feeds, and we keep those. Where we hold any fuller article
        text, a nightly job deletes it after 14 days. None of it is ever shown in the app.
      </p>

      <h2>If this changes</h2>
      <p>
        If Throughline adds accounts or anything else that stores personal information, this
        page will say what is stored and how to delete it before that feature is switched on.
      </p>
    </ProsePage>
  );
}
