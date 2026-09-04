from __future__ import annotations

import argparse
import json
from pathlib import Path

from chronos_repro.envfile import load_env_file
from chronos_repro.llm import DeepSeekClient, InsufficientBalanceError, LLMError


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    loaded = load_env_file(args.env_file)
    if "DEEPSEEK_API_KEY" not in loaded:
        raise SystemExit("DEEPSEEK_API_KEY is absent from the env file")
    try:
        result = DeepSeekClient().chat(
            [{"role": "user", "content": "Reply with exactly: TLS_OK"}], temperature=0.0
        )
        payload = {
            "status": "ok",
            "model": result.model,
            "text": result.text,
            "usage": result.usage,
            "request_id": result.request_id,
        }
        exit_code = 0
    except InsufficientBalanceError as error:
        payload = {"status": "stopped_insufficient_balance", "error": str(error)}
        exit_code = 3
    except LLMError as error:
        payload = {"status": "stopped_error", "error": str(error)}
        exit_code = 2
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
