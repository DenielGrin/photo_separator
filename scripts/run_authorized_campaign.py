#!/usr/bin/env python3
"""Run explicitly authorized cloud comparison. Interactive keys never enter files or argv."""

import argparse
import asyncio
import getpass

from photo_separator.paid_campaign import run_authorized_campaign


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--concrete-ids", action="store_true")
    parser.add_argument("--max-cost-usd", type=float, default=5)
    parser.add_argument("--budget-root", action="append", dest="budget_roots", default=[])
    parser.add_argument("--max-free-calls", type=int, default=30)
    parser.add_argument("--max-free-per-model", type=int, default=10)
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=["openai", "deepseek", "openrouter"],
        default=["openai", "deepseek", "openrouter"],
    )
    args = parser.parse_args()
    names = {"openai": "OpenAI", "deepseek": "DeepSeek", "openrouter": "OpenRouter"}
    credentials = {p: getpass.getpass(names[p] + " API key (hidden): ") for p in args.providers}
    try:
        asyncio.run(run_authorized_campaign(vars(args), credentials, max_cost_usd=args.max_cost_usd))
    finally:
        credentials.clear()


if __name__ == "__main__":
    main()
