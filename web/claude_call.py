#!/usr/bin/env python3
"""Proxy for Claude API calls from Node.js server.
Usage: python3 claude_call.py <system_prompt> <user_message>
Returns JSON to stdout.
"""
import sys
import json
import os

# Load .env from project root
from pathlib import Path
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import anthropic

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: claude_call.py <system> <user>"}))
        sys.exit(1)

    system_prompt = sys.argv[1]
    user_message = sys.argv[2]

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text if response.content else ""
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        print(json.dumps({"ok": True, "text": text, "usage": usage}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
