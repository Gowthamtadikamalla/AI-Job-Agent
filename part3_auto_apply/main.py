"""
Part 3 — Resume Auto-Apply Agent
==================================
Entry point.

Usage:
    python main.py [--job-url URL] [--data PATH] [--show-browser]

Examples:
    # Use the default Lever URL from the challenge spec
    python main.py

    # Custom job URL
    python main.py --job-url "https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065"

    # Show the Chrome window (useful for demos / debugging)
    python main.py --show-browser

    # Use a different candidate data file
    python main.py --data /path/to/my_data.json
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from part3_auto_apply.controller import ApplicationController

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s — %(message)s",
)

DEFAULT_JOB_URL = (
    "https://jobs.lever.co/ekimetrics/d9d64766-3d42-4ba9-94d4-f74cdaf20065"
)
DEFAULT_DATA_PATH = Path(__file__).parent / "candidate_data.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jobnova Auto-Apply Agent")
    parser.add_argument(
        "--job-url",
        default=DEFAULT_JOB_URL,
        help="Lever job application URL",
    )
    parser.add_argument(
        "--data",
        default=str(DEFAULT_DATA_PATH),
        help="Path to candidate data JSON file",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run Chrome in headed (visible) mode — useful for demos",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"ERROR: Candidate data file not found: {data_path}")
        print("Edit part3_auto_apply/candidate_data.json with your information first.")
        raise SystemExit(1)

    with data_path.open() as f:
        candidate_data = json.load(f)

    print("\n" + "=" * 60)
    print("Jobnova Auto-Apply Agent — Part 3")
    print("=" * 60)
    print(f"Job URL   : {args.job_url}")
    print(f"Candidate : {candidate_data.get('identity', {}).get('name', 'Unknown')}")
    print(f"Browser   : {'headed (visible)' if args.show_browser else 'headless (--headless=new)'}")
    print("=" * 60 + "\n")

    controller = ApplicationController(
        job_url=args.job_url,
        candidate_data=candidate_data,
        headless=not args.show_browser,
    )

    await controller.run()


if __name__ == "__main__":
    asyncio.run(main())
