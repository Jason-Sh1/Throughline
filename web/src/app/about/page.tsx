// "About our AI": what writes the briefings, what checks them, and what can go wrong.
// Every statement here describes code in this repository; update it when the pipeline
// changes (models and efforts live in worker/llm/, the rules in CLAUDE.md).

import type { Metadata } from "next";
import { ProsePage } from "@/components/prose-page";

export const metadata: Metadata = {
  title: "About our AI",
  description: "How Throughline's briefings are written, checked and labeled.",
};

export default function AboutPage() {
  return (
    <ProsePage
      title="About our AI"
      lede="Throughline's briefings are written by an AI model from news articles, then checked by a second pass. Here is exactly what that means, and where it can fail."
    >
      <h2>What is written by AI</h2>
      <p>
        Everything set in this typeface: the headline labeled &ldquo;Throughline headline&rdquo;,
        the brief, why it matters, key facts, background, glossary, timeline, how outlets frame
        it, and what&rsquo;s next. Each story carries the label &ldquo;AI-generated from N
        sources&rdquo;.
      </p>
      <p className="label">
        Text set like this is not ours: outlet names and the outlets&rsquo; own headlines. We
        never put our words under a publisher&rsquo;s name, and we never show an article&rsquo;s
        text. To read it, follow the link to the publisher.
      </p>

      <h2>How a briefing is made</h2>
      <ol>
        <li>
          <strong>Collect.</strong> We read the public feeds of a fixed list of news outlets
          chosen to cover the political spectrum, plus the GDELT news index and the Guardian
          API.
        </li>
        <li>
          <strong>Group.</strong> Articles about the same event are grouped by a small
          text-similarity model that runs inside our own pipeline. No AI writing happens here.
        </li>
        <li>
          <strong>Extract and write.</strong> Claude Haiku 5.5 (an AI model made by Anthropic)
          lists the claims each article makes, then writes the briefing. Every sentence must
          cite the articles it came from. A sentence with no valid citation is deleted.
        </li>
        <li>
          <strong>Check.</strong> A second pass reads each sentence next to the articles it
          cites and marks it supported, unsupported or contradicted. Anything not marked
          supported is deleted, not rewritten. If a headline fails, the whole briefing is
          dropped.
        </li>
        <li>
          <strong>Publish.</strong> Only briefings that passed the check are published.
        </li>
      </ol>

      <h2>Where it can be wrong</h2>
      <ul>
        <li>
          The check is also done by an AI model. It removes many errors but it can miss one, or
          delete a sentence that was fine.
        </li>
        <li>
          A briefing can only be as accurate as the articles behind it. If the sources are
          wrong, or early reports change, the briefing will be wrong too. Stories marked
          &ldquo;Developing&rdquo; are the most likely to change.
        </li>
        <li>
          The model works from headlines and short excerpts, not always the full article, so
          it can miss context that the full text gives.
        </li>
        <li>
          Grouping can put two related events in one story, or split one event in two.
        </li>
        <li>
          We have not yet published accuracy measurements. A test set labeled by hand is
          planned; until those numbers exist, we do not claim any.
        </li>
      </ul>
      <p>
        So check anything that matters to you: tap the sentence, see which articles it came
        from, and read them.
      </p>

      <h2>Outlet ratings</h2>
      <p>
        The Left, Center and Right labels are not ours. They come from{" "}
        <a href="https://www.allsides.com/media-bias/ratings" target="_blank" rel="noopener noreferrer">
          AllSides Media Bias Ratings
        </a>{" "}
        (CC BY-NC 4.0), which rates U.S. outlets. Outlets AllSides has not rated, including
        most outlets outside the U.S., are shown as &ldquo;Not rated&rdquo;. The ratings show who
        covered a story. They are not a judgment of any article, and Throughline does not
        guess your politics or change what you see based on them.
      </p>

      <h2>What we do not do</h2>
      <ul>
        <li>No ads, no paid tier, no sponsored stories.</li>
        <li>No infinite feed: an edition ends, and the app says so.</li>
        <li>No advice. Briefings are information, not financial, legal or medical advice.</li>
      </ul>
    </ProsePage>
  );
}
