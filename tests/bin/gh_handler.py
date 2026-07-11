#!/usr/bin/env python3
import sys

prs_json = """[
    {"number": 300, "title": "Good PR", "headRefName": "feat/S28-cluster-thing",
     "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
     "labels": [{"name": "auto-merge"}]},
    {"number": 301, "title": "WIP PR", "headRefName": "feat/S28-cluster-thing",
     "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
     "labels": [{"name": "WIP"}]},
    {"number": 302, "title": "Conflict PR", "headRefName": "feat/S28-cluster-thing",
     "mergeable": "CONFLICTING", "mergeStateStatus": "CLEAN",
     "labels": [{"name": "auto-merge"}]},
    {"number": 303, "title": "Another good", "headRefName": "feat/S10-cluster-x",
     "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
     "labels": []}
]"""
checks_json = """[]"""
merge_succeeds = True

args = sys.argv[1:]

if args[0] == "pr" and args[1] == "list":
    print(prs_json)
    sys.exit(0)
elif args[0] == "pr" and args[1] == "checks":
    print(checks_json)
    sys.exit(0)
elif args[0] == "pr" and args[1] == "merge":
    if merge_succeeds:
        print("Pull request successfully merged.")
        sys.exit(0)
    else:
        print("Merge failed.", file=sys.stderr)
        sys.exit(1)
elif args[0] == "pr" and args[1] == "view":
    print(prs_json)
    sys.exit(0)
else:
    print("[]")
    sys.exit(0)
