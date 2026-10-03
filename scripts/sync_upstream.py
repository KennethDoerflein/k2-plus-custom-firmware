#!/usr/bin/env python3
"""Merge upstream without silently dropping either side.

Replaces ad-hoc `git merge -X ours` / `git checkout --ours`, which kept our
version of whole files and hid upstream changes until the printer crashed.

    scripts/sync_upstream.py                 # fetch + merge upstream/main
    scripts/sync_upstream.py --finish        # after resolving conflicts
    scripts/sync_upstream.py --repo ../kalico

What it does:
  1. Refuses to start with uncommitted tracked changes.
  2. Merges with a plain three-way merge (no -X ours/theirs).
  3. Auto-resolves only the generated checksum files (they are regenerated),
     and stops on every other conflict for you to resolve by hand.
  4. Regenerates scripts/release_index.py outputs and runs the compatibility
     tests, when this repo has them.
  5. Lists files that BOTH upstream and we changed, so review is targeted.
  6. Leaves the merge uncommitted so you can inspect it first.
"""

import argparse
import subprocess
import sys
from pathlib import Path

GENERATED = ("extras/manifest.json", "index")


def git(repo, *args, check=True):
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True,
                            text=True)
    if check and result.returncode:
        sys.exit("git %s failed:\n%s%s" % (" ".join(args), result.stdout,
                                           result.stderr))
    return result


def lines(result):
    return [line for line in result.stdout.splitlines() if line]


def unmerged(repo):
    return lines(git(repo, "diff", "--name-only", "--diff-filter=U"))


def merging(repo):
    return git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD",
               check=False).returncode == 0


def report_overlap(repo, remote, branch):
    """Files changed on both sides since the merge base."""
    base = git(repo, "merge-base", "HEAD", "%s/%s" % (remote, branch))
    base = base.stdout.strip()
    ours = set(lines(git(repo, "diff", "--name-only", base, "HEAD")))
    theirs = set(lines(git(
        repo, "diff", "--name-only", base, "%s/%s" % (remote, branch))))
    overlap = sorted((ours & theirs) - set(GENERATED))
    print("\nChanged by BOTH sides (review these):")
    for name in overlap:
        print("  " + name)
    if not overlap:
        print("  (none)")


def finish(repo, commit=False, message="chore(upstream): sync upstream changes"):
    root = Path(repo)
    if unmerged(repo):
        sys.exit("Conflicts remain: " + ", ".join(unmerged(repo)))
    failed = False
    index_script = root / "scripts" / "release_index.py"
    if index_script.exists():
        subprocess.run([sys.executable, str(index_script)], cwd=repo,
                       check=True)
        git(repo, "add", *[g for g in GENERATED if (root / g).exists()])
    if (root / "tests").is_dir():
        print("\nRunning compatibility tests...")
        failed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=repo).returncode != 0
    if failed:
        sys.exit("\nTests failed: fix them before committing this merge.")
    if commit:
        git(repo, "commit", "-m", message)
        print("\nAll checks passed and merge committed successfully.")
    else:
        print("\nAll checks passed. Review with `git diff --cached`, then run "
              "`git commit`.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", default=".", help="repository to sync")
    parser.add_argument("--remote", default="upstream")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--finish", action="store_true",
                        help="regenerate files and test after resolving")
    parser.add_argument("--commit", action="store_true",
                        help="automatically commit the merge if all checks pass")
    parser.add_argument("-m", "--message", default=None,
                        help="commit message to use with --commit")
    args = parser.parse_args()
    repo = args.repo

    commit_msg = args.message or f"chore(upstream): merge {args.remote}/{args.branch}"

    if args.finish:
        if not merging(repo):
            sys.exit("No merge in progress.")
        return finish(repo, commit=args.commit, message=commit_msg)

    if lines(git(repo, "status", "--porcelain", "--untracked-files=no")):
        sys.exit("Commit or stash tracked changes first.")
    git(repo, "fetch", args.remote)
    target = "%s/%s" % (args.remote, args.branch)
    if git(repo, "merge-base", "--is-ancestor", target, "HEAD",
           check=False).returncode == 0:
        print("Already up to date with " + target)
        return 0

    report_overlap(repo, args.remote, args.branch)
    merge = git(repo, "merge", "--no-commit", "--no-ff", target, check=False)
    print(merge.stdout)

    for name in unmerged(repo):
        if name in GENERATED:
            git(repo, "checkout", "--ours", name)
            git(repo, "add", name)
    conflicts = unmerged(repo)
    if conflicts:
        print("Resolve these by hand (keep BOTH sides' intent), then run "
              "`scripts/sync_upstream.py --finish`:")
        for name in conflicts:
            print("  " + name)
        return 2
    return finish(repo, commit=args.commit, message=commit_msg)


if __name__ == "__main__":
    sys.exit(main())
