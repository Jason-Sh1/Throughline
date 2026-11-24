"""Labeling helper for the gold set: `make label`, then open http://localhost:8765.

For each story you answer two things:
1. Which articles are about the same event as the anchor headline (clustering).
2. The key facts a good briefing must state, one per line (key-fact coverage).

Labeling is blind: articles are shuffled and shown under opaque ids, so you
can't tell which ones the clusterer put in the story or which ones the model
saw. Nothing here calls an LLM; the labels are yours alone (CLAUDE.md).

Standard library only (http.server): it's a local tool, not part of the app.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evals.stories import GOLD_DIR, STORIES_DIR, load_gold, load_stories, save_gold

PAGE = Path(__file__).with_name("label.html")


def anchor(story: dict) -> dict:
    """The reference article: S1, the first source the model sees."""
    return next(a for a in story["articles"] if a["ref"] == "S1")


def blind(story: dict) -> tuple[list[dict], dict[str, str]]:
    """Every article except the anchor, shuffled (stable per story), with opaque
    ids. Returns (articles for the page, opaque id -> ref)."""
    rest = [a for a in story["articles"] if a["ref"] != "S1"]
    random.Random(story["id"]).shuffle(rest)
    view, mapping = [], {}
    for i, a in enumerate(rest, 1):
        oid = f"a{i}"
        mapping[oid] = a["ref"]
        view.append({"id": oid, "outlet": a["outlet"], "title": a["title"],
                     "summary": a["summary"], "published_at": a["published_at"],
                     "url": a["url"]})  # fmt: skip
    return view, mapping


def story_payload(story: dict, gold: dict | None) -> dict:
    view, mapping = blind(story)
    to_opaque = {ref: oid for oid, ref in mapping.items()}
    membership = {to_opaque[r]: v for r, v in (gold or {}).get("membership", {}).items()
                  if r in to_opaque}  # fmt: skip
    a = anchor(story)
    return {
        "id": story["id"],
        "category": story["category"],
        "anchor": {
            "outlet": a["outlet"],
            "title": a["title"],
            "summary": a["summary"],
            "url": a["url"],
        },  # fmt: skip
        "articles": view,
        "membership": membership,
        "key_facts": (gold or {}).get("key_facts", []),
        "notes": (gold or {}).get("notes", ""),
    }


def to_gold(story: dict, body: dict, labeler: str) -> dict:
    _, mapping = blind(story)
    membership = {"S1": "same"}
    for oid, v in body.get("membership", {}).items():
        if oid in mapping and v in ("same", "different"):
            membership[mapping[oid]] = v
    facts = [f.strip() for f in body.get("key_facts", []) if f.strip()]
    return {
        "story_id": story["id"],
        "labeled_by": labeler,
        "labeled_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "membership": membership,
        "key_facts": facts,
        "notes": body.get("notes", "").strip(),
    }


def is_complete(story: dict, gold: dict | None) -> bool:
    if not gold or not gold.get("key_facts"):
        return False
    return all(a["ref"] in gold.get("membership", {}) for a in story["articles"])


def make_handler(stories_dir: Path, gold_dir: Path, labeler: str):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, data, status=200):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def do_GET(self):
            stories = {s["id"]: s for s in load_stories(stories_dir)}
            gold = load_gold(gold_dir)
            if self.path == "/":
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/stories":
                self._json([{"id": sid, "title": anchor(s)["title"], "category": s["category"],
                             "done": is_complete(s, gold.get(sid))}
                            for sid, s in stories.items()])  # fmt: skip
            elif self.path.startswith("/api/story/"):
                sid = self.path.removeprefix("/api/story/")
                if sid not in stories:
                    return self._json({"error": "not found"}, 404)
                self._json(story_payload(stories[sid], gold.get(sid)))
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path != "/api/gold":
                return self._json({"error": "not found"}, 404)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            stories = {s["id"]: s for s in load_stories(stories_dir)}
            story = stories.get(body.get("id"))
            if story is None:
                return self._json({"error": "unknown story"}, 400)
            g = to_gold(story, body, labeler)
            save_gold(g, gold_dir)
            self._json({"saved": True, "done": is_complete(story, g)})

    return Handler


def _labeler() -> str:
    try:
        return subprocess.check_output(["git", "config", "user.name"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "human"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(STORIES_DIR, GOLD_DIR, _labeler()))  # fmt: skip
    print(f"Labeling at http://localhost:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
