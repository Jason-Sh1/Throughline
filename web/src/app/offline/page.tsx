// Shown by the service worker when a page is requested offline and no copy is stored.

import type { Metadata } from "next";
import { ProsePage } from "@/components/prose-page";

export const metadata: Metadata = { title: "Offline" };

export default function OfflinePage() {
  return (
    <ProsePage
      title="You're offline"
      lede="This page isn't stored on your device. The latest edition you opened is: go back to it, or try again when you have a connection."
      footer={false}
    >
      <p>
        Throughline keeps a copy of the most recent edition you loaded, so its stories can be
        read without a connection. Source links still need the internet, because they open the
        publishers&rsquo; own sites.
      </p>
    </ProsePage>
  );
}
