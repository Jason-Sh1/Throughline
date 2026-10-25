"""Report which environment variables are set, without printing any values.

`make env-check` - run at the start of a session to see what's configured.
Reads .env too (same as the worker does), so it reflects what the pipeline sees.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# (name, needed from milestone)
VARS = [
    ("DATABASE_URL", "M2"),
    ("CRAWLER_CONTACT", "M2"),
    ("GUARDIAN_API_KEY", "M2"),
    ("NEXT_PUBLIC_SUPABASE_URL", "M2"),
    ("NEXT_PUBLIC_SUPABASE_ANON_KEY", "M2"),
    ("SUPABASE_SERVICE_ROLE_KEY", "M2"),
    ("ANTHROPIC_API_KEY", "M3"),
    ("GEMINI_API_KEY", "M3"),
    ("SENTRY_DSN", "M8"),
    ("LANGFUSE_PUBLIC_KEY", "M8"),
    ("LANGFUSE_SECRET_KEY", "M8"),
]


def main() -> int:
    load_dotenv()
    missing_now = []
    for name, milestone in VARS:
        ok = bool(os.environ.get(name, "").strip())
        print(f"  {'set    ' if ok else 'MISSING'}  {name:32} (needed from {milestone})")
        if not ok and milestone == "M2":
            missing_now.append(name)
    if missing_now:
        print(f"Missing for the current pipeline: {', '.join(missing_now)}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
