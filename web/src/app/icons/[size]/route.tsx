// PNG versions of the app icon (public/icon.svg), drawn at build time. Some install
// prompts and iOS want PNG; keeping one drawing in code avoids committing binary files
// that can drift from the SVG.

import { ImageResponse } from "next/og";

const SIZES = new Set([180, 192, 512]);
const INK = "#111e19";
const PAPER = "#f2f7f5";
const MARKER = "#f7ea7e";

export function generateStaticParams() {
  return [...SIZES].map((size) => ({ size: String(size) }));
}

export const dynamicParams = false;

export async function GET(_request: Request, { params }: { params: Promise<{ size: string }> }) {
  const size = Number((await params).size);
  if (!SIZES.has(size)) return new Response("Not found", { status: 404 });
  const u = size / 512; // the SVG is drawn on a 512 grid
  const stitch = (x: number, filled: boolean) => (
    <div
      style={{
        position: "absolute",
        left: (x - 7) * u,
        top: 183 * u,
        width: 56 * u,
        height: 146 * u,
        borderRadius: 12 * u,
        border: `${14 * u}px solid ${filled ? MARKER : PAPER}`,
        background: filled ? MARKER : INK,
      }}
    />
  );
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", position: "relative", background: INK }}>
        <div
          style={{
            position: "absolute",
            left: 88 * u,
            top: 249 * u,
            width: 336 * u,
            height: 14 * u,
            borderRadius: 7 * u,
            background: PAPER,
          }}
        />
        {stitch(139, false)}
        {stitch(235, true)}
        {stitch(331, false)}
      </div>
    ),
    { width: size, height: size },
  );
}
