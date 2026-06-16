#!/usr/bin/env python3
"""Fetch issues from a GitHub repository and save them as JSON.

Usage:
    python fetch_github_issues.py [--repo owner/name] [--count N] [--state open|closed|all] [--out FILE]

Defaults: 10 issues, open state, output to github_issues.json

The output JSON has the shape:
    {
      "repo": {"owner": "...", "name": "..."},
      "issues": [
        {
          "number": 42,
          "title": "...",
          "body": "...",
          "state": "open",
          "author": "alice",
          "labels": ["bug"],
          "comments": [{"author": "bob", "body": "..."}]
        },
        ...
      ]
    }
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from github import Github

load_dotenv(override=True)


def fetch_issue(issue_obj) -> dict:
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
    parser.add_argument("--out", default="github_issues.json", help="Output JSON path")
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

    state_qualifier = "" if args.state == "all" else f" state:{args.state}"
    query = f"repo:{owner}/{name} is:issue{state_qualifier}"

    print(f"Fetching up to {args.count} {args.state} issues from {owner}/{name} ...")

    issues = []
    for issue_obj in gh.search_issues(query):
        if len(issues) >= args.count:
            break
        issue = fetch_issue(issue_obj)
        issues.append(issue)
        print(f"  #{issue['number']} — {issue['title'][:60]}")

    output = {
        "repo": {"owner": owner, "name": name},
        "issues": issues,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(issues)} issues to {args.out}")


if __name__ == "__main__":
    main()
