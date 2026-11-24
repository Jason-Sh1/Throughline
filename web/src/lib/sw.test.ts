// The service worker (public/sw.js) is plain JavaScript served as-is, so it cannot be
// imported. This test pulls its one pure function out of the file text and checks it:
// a stored page is useless offline if the assets its HTML refers to are missed.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  fileURLToPath(new URL("../../public/sw.js", import.meta.url)),
  "utf8",
);

function loadAssetUrls(): (html: string) => string[] {
  const start = source.indexOf("function assetUrls(html) {");
  const end = source.indexOf("\n}\n", start) + 3;
  expect(start).toBeGreaterThan(-1);
  return new Function(`return (${source.slice(start, end)})`)();
}

describe("service worker assetUrls", () => {
  const assetUrls = loadAssetUrls();

  it("finds scripts, styles and fonts a page refers to, once each", () => {
    const html =
      '<link rel="stylesheet" href="/_next/static/css/a1b2.css" data-precedence="next"/>' +
      '<script src="/_next/static/chunks/webpack-abc.js" async=""></script>' +
      '<script src="/_next/static/chunks/webpack-abc.js" async=""></script>' +
      '<link rel="preload" href="/_next/static/media/font-x.woff2" as="font"/>' +
      "url(/_next/static/media/other.woff2)";
    expect(assetUrls(html).sort()).toEqual(
      [
        "/_next/static/css/a1b2.css",
        "/_next/static/chunks/webpack-abc.js",
        "/_next/static/media/font-x.woff2",
        "/_next/static/media/other.woff2",
      ].sort(),
    );
  });

  it("finds lazy chunks named in Next's inline page data without the /_next/ prefix", () => {
    const html = 'self.__next_f.push([1,"3:I[123,[\\"255\\",\\"static/chunks/255-b1.js\\"],\\"x\\"]"])';
    expect(assetUrls(html)).toEqual(["/_next/static/chunks/255-b1.js"]);
  });

  it("returns nothing for a page with no build assets, and ignores other paths", () => {
    expect(assetUrls("<p>plain</p>")).toEqual([]);
    expect(assetUrls('<a href="/story/12">x</a><img src="/icon.svg">')).toEqual([]);
  });
});

describe("service worker scope of what it stores", () => {
  it("only caches same-origin story pages, the edition and build assets", () => {
    // Guards against a future edit widening the cache to arbitrary URLs.
    expect(source).toContain('url.pathname.startsWith("/story/")');
    expect(source).toContain('url.pathname.startsWith("/_next/static/")');
    expect(source).toContain("url.origin !== self.location.origin");
  });
});
