#!/usr/bin/env python3
"""Proxy for Claude API calls from Node.js server.
Usage: python3 claude_call.py <system_prompt> <user_message>
Returns JSON to stdout.

Auth resolution order (first one with a non-empty value wins):
  1. CLAUDE_CODE_OAUTH_TOKEN   — subscription auth (Pro/Max plan).
                                 Get via `claude setup-token`.
  2. ANTHROPIC_AUTH_TOKEN      — same as above, alternate name.
  3. ANTHROPIC_API_KEY         — standard API key (pay-per-token credits).
"""
import sys
import json
import os

# Load .env from project root, OVERWRITING blank shell vars so empty values don't shadow .env entries
from pathlib import Path
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v:  # only set if value is non-empty (avoid blanks shadowing real env)
                os.environ[k] = v

import anthropic


def _resolve_auth():
    """Return (kwargs_for_Anthropic, mode_string) based on what's available."""
    oauth_token = (
        os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        or os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    )
    if oauth_token:
        # Subscription / OAuth auth — sends Authorization: Bearer <token>
        return {"auth_token": oauth_token, "api_key": None}, "oauth"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        return {"api_key": api_key}, "api_key"
    return None, "missing"


def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: claude_call.py <system> <user>"}))
        sys.exit(1)

    system_prompt = sys.argv[1]
    user_message = sys.argv[2]

    auth_kwargs, mode = _resolve_auth()
    if auth_kwargs is None:
        print(json.dumps({
            "ok": False,
            "error": (
                "No Claude credentials. Set ONE of these in .env:\n"
                "  CLAUDE_CODE_OAUTH_TOKEN=...   (subscription, get via 'claude setup-token')\n"
                "  ANTHROPIC_API_KEY=sk-ant-...  (pay-per-use API credits)"
            ),
        }, ensure_ascii=False))
        sys.exit(1)

    # Optional model override; defaults to a known-good Sonnet build.
    model = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-4-20250514"

    try:
        client = anthropic.Anthropic(**auth_kwargs)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text if response.content else ""
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "auth_mode": mode,
        }
        print(json.dumps({"ok": True, "text": text, "usage": usage}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e), "auth_mode": mode}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
