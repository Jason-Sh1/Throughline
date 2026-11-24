// Web app manifest: makes Throughline installable to a phone's home screen.
// Colors match --paper / --ink in globals.css (light theme).

import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Throughline",
    short_name: "Throughline",
    description:
      "Daily news editions: each story clustered across outlets and briefed with cited sources.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#f2f7f5",
    theme_color: "#f2f7f5",
    icons: [
      { src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" },
      { src: "/icon-maskable.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" },
      { src: "/icons/192", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icons/512", sizes: "512x512", type: "image/png", purpose: "any" },
    ],
  };
}
