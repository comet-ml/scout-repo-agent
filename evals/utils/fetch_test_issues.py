#!/usr/bin/env python3
"""Fetch issues from scout-test-repo and save them as a CSV for local testing.

Usage:
    python fetch_test_issues.py [--count N] [--state open|closed|all] [--out FILE]

Defaults: 10 issues, open state, output to test_issues.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

from dotenv import load_dotenv
from github import Github

load_dotenv()


def get_issue_data(issue_obj) -> dict:
    comments = []
    for c in list(issue_obj.get_comments())[:20]:
        comments.append({"author": c.user.login, "body": (c.body or "")[:500]})
    return {
        "number": issue_obj.number,
        "title": issue_obj.title,
        "body": issue_obj.body or "",
        "state": issue_obj.state,
        "author": issue_obj.user.login,
        "labels": [lbl.name for lbl in issue_obj.labels],
        "comments": comments,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10, help="Number of issues to fetch")
    parser.add_argument("--state", default="open", choices=["open", "closed", "all"])
    parser.add_argument("--out", default="test_issues.csv", help="Output CSV path")
    parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo to fetch from (defaults to SCOUT_GITHUB_REPO_OWNER/SCOUT_GITHUB_REPO_NAME)",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        sys.exit("GITHUB_TOKEN is not set — add it to your .env file")

    if args.repo:
        owner, name = args.repo.split("/", 1)
    else:
        owner = os.environ.get("SCOUT_GITHUB_REPO_OWNER", "").strip()
        name = os.environ.get("SCOUT_GITHUB_REPO_NAME", "").strip()
        if not owner or not name:
            sys.exit(
                "Set SCOUT_GITHUB_REPO_OWNER and SCOUT_GITHUB_REPO_NAME in .env, "
                "or pass --repo owner/name"
            )

    gh = Github(token)
    repo = gh.get_repo(f"{owner}/{name}")
    issues = list(repo.get_issues(state=args.state))[: args.count]

    print(f"Fetching {len(issues)} {args.state} issues from {owner}/{name} …")

    rows = []
    for issue in issues:
        data = get_issue_data(issue)
        rows.append(
            {
                "number": data["number"],
                "title": data["title"],
                "state": data["state"],
                "author": data["author"],
                "labels": ", ".join(data["labels"]),
                "body": data["body"],
                # comments stored as JSON so the CSV stays flat
                "comments_json": json.dumps(data["comments"]),
            }
        )
        print(f"  #{data['number']} — {data['title'][:60]}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nSaved {len(rows)} issues to {args.out}")


if __name__ == "__main__":
    main()
