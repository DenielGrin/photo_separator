#!/usr/bin/env python3
import argparse
import asyncio
import getpass

from photo_separator.cover_judge import run_judge


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--budget-root", action="append", default=[])
    parser.add_argument("--max-cost-usd", type=float, default=5)
    args = parser.parse_args()
    key = getpass.getpass("OpenAI judge API key (hidden): ")
    try:
        asyncio.run(run_judge(args.root, key, args.budget_root, args.max_cost_usd))
    finally:
        key = ""
        print("Judge credential reference cleared; no key file created.")


if __name__ == "__main__":
    main()
