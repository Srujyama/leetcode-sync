#!/usr/bin/env python3
"""Fetch public LeetCode stats and regenerate README.md + data/history.json.

Uses only the Python standard library so it runs anywhere (locally or in
GitHub Actions) with no pip install. Reads the username from config.json at
the repo root.
"""

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPHQL_URL = "https://leetcode.com/graphql"

PROFILE_QUERY = """
query profile($username: String!) {
  allQuestionsCount { difficulty count }
  matchedUser(username: $username) {
    username
    profile { realName ranking }
    submitStatsGlobal { acSubmissionNum { difficulty count } }
    userCalendar { streak totalActiveDays }
  }
}
"""

RECENT_QUERY = """
query recent($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    title titleSlug timestamp
  }
}
"""

CONTEST_QUERY = """
query contest($username: String!) {
  userContestRanking(username: $username) {
    attendedContestsCount rating globalRanking topPercentage
  }
}
"""


def graphql(query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Referer": "https://leetcode.com",
            "User-Agent": "leetcode-sync (github.com/Srujyama/leetcode-sync)",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_stats(username: str, recent_limit: int) -> dict:
    profile = graphql(PROFILE_QUERY, {"username": username})["data"]
    user = profile["matchedUser"]
    if user is None:
        sys.exit(f"LeetCode user {username!r} not found")

    solved = {row["difficulty"]: row["count"]
              for row in user["submitStatsGlobal"]["acSubmissionNum"]}
    totals = {row["difficulty"]: row["count"]
              for row in profile["allQuestionsCount"]}

    recent = graphql(RECENT_QUERY, {"username": username, "limit": recent_limit})
    recent = recent["data"].get("recentAcSubmissionList") or []

    # userContestRanking is null (and may come with an error entry) for
    # accounts that never attended a contest — treat that as "no contests".
    try:
        contest = graphql(CONTEST_QUERY, {"username": username})["data"].get(
            "userContestRanking")
    except Exception:
        contest = None

    calendar = user.get("userCalendar") or {}
    return {
        "username": user["username"],
        "real_name": (user.get("profile") or {}).get("realName") or "",
        "ranking": (user.get("profile") or {}).get("ranking"),
        "solved": solved,
        "totals": totals,
        "streak": calendar.get("streak", 0),
        "active_days": calendar.get("totalActiveDays", 0),
        "recent": recent,
        "contest": contest,
    }


def load_config() -> dict:
    return json.loads((REPO_ROOT / "config.json").read_text())


def update_history(stats: dict) -> list:
    path = REPO_ROOT / "data" / "history.json"
    history = json.loads(path.read_text()) if path.exists() else []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = {
        "date": today,
        "total": stats["solved"].get("All", 0),
        "easy": stats["solved"].get("Easy", 0),
        "medium": stats["solved"].get("Medium", 0),
        "hard": stats["solved"].get("Hard", 0),
        "ranking": stats["ranking"],
        "streak": stats["streak"],
    }
    history = [row for row in history if row["date"] != today] + [entry]
    history.sort(key=lambda row: row["date"])

    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(history, indent=2) + "\n")
    return history


def bar(count: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "░" * width + " 0.0%"
    pct = count / total
    filled = round(pct * width)
    return "█" * filled + "░" * (width - filled) + f" {pct * 100:.1f}%"


def build_readme(stats: dict, history: list, config: dict) -> str:
    username = stats["username"]
    profile_url = f"https://leetcode.com/u/{username}/"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    solved, totals = stats["solved"], stats["totals"]

    lines = [
        f"# 📊 LeetCode Progress — [{username}]({profile_url})",
        "",
        f"> Auto-updated by GitHub Actions · Last updated: **{now}**",
        "",
        f"**{solved.get('All', 0)} / {totals.get('All', 0)} solved**"
        f" · 🔥 Streak: **{stats['streak']}** day(s)"
        f" · 📅 Active days: **{stats['active_days']}**"
        + (f" · 🏅 Ranking: **#{stats['ranking']:,}**" if stats["ranking"] else ""),
        "",
        "## Progress",
        "",
        "| Difficulty | Solved | Progress |",
        "|---|---:|---|",
    ]
    for diff, emoji in (("Easy", "🟢"), ("Medium", "🟡"), ("Hard", "🔴")):
        s, t = solved.get(diff, 0), totals.get(diff, 0)
        lines.append(f"| {emoji} {diff} | {s} / {t} | `{bar(s, t)}` |")
    s, t = solved.get("All", 0), totals.get("All", 0)
    lines.append(f"| **All** | **{s} / {t}** | `{bar(s, t)}` |")

    contest = stats["contest"]
    if contest and contest.get("attendedContestsCount"):
        lines += [
            "",
            "## Contests",
            "",
            f"Rating: **{contest['rating']:.0f}**"
            f" · Attended: **{contest['attendedContestsCount']}**"
            f" · Global rank: **#{contest['globalRanking']:,}**"
            f" (top {contest['topPercentage']}%)",
        ]

    if stats["recent"]:
        lines += ["", "## Recent accepted submissions", "",
                  "| Problem | Solved on |", "|---|---|"]
        for sub in stats["recent"]:
            when = datetime.fromtimestamp(int(sub["timestamp"]),
                                          timezone.utc).strftime("%Y-%m-%d")
            url = f"https://leetcode.com/problems/{sub['titleSlug']}/"
            lines.append(f"| [{sub['title']}]({url}) | {when} |")

    rows = config.get("history_rows_in_readme", 30)
    lines += ["", "## History", "",
              "| Date | Total | Easy | Medium | Hard | Streak |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in reversed(history[-rows:]):
        lines.append(f"| {row['date']} | {row['total']} | {row['easy']}"
                     f" | {row['medium']} | {row['hard']} | {row['streak']} |")

    lines += [
        "",
        "---",
        "",
        "## How this works",
        "",
        "A [GitHub Actions workflow](.github/workflows/update-stats.yml) runs",
        "[`scripts/update_stats.py`](scripts/update_stats.py) every 6 hours.",
        "It pulls public profile stats from LeetCode's GraphQL API (no login",
        "or cookies needed), appends a daily snapshot to",
        "[`data/history.json`](data/history.json), and regenerates this README.",
        "",
        "To track a different account, edit `leetcode_username` in",
        "[`config.json`](config.json). Full solution-code syncing (which needs",
        "your LeetCode session cookies) is available as an optional workflow —",
        "see [`.github/workflows/sync-solutions.yml`](.github/workflows/sync-solutions.yml).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    config = load_config()
    stats = fetch_stats(config["leetcode_username"],
                        config.get("recent_submissions_limit", 10))
    history = update_history(stats)
    (REPO_ROOT / "README.md").write_text(build_readme(stats, history, config))
    print(f"Updated stats for {stats['username']}: "
          f"{stats['solved'].get('All', 0)} solved "
          f"(E {stats['solved'].get('Easy', 0)} / "
          f"M {stats['solved'].get('Medium', 0)} / "
          f"H {stats['solved'].get('Hard', 0)})")


if __name__ == "__main__":
    main()
