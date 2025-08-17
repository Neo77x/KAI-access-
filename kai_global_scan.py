"""
kai_global_scan.py

- Lists all repos the authenticated user owns or is a member of via orgs.
- Optionally clones each repo (depth=1) into a working folder, performs a safe scan,
  creates a small marker file (KAI_SCAN.md) describing findings.
- If DIRECT_PUSH env var is "true" the script will push changes directly to default branch.
  Otherwise it will create a branch and open a PR for review.

REQUIREMENTS:
  pip install PyGithub
ENV:
  GITHUB_PAT (or passed via Actions secret: KAI_PAT)
  DIRECT_PUSH ("true" or "false")  - default "false"
"""
import os, sys, time, shutil, subprocess, json
from pathlib import Path
from github import Github

TOKEN = os.environ.get("KAI_PAT") or os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
DIRECT_PUSH = os.environ.get("DIRECT_PUSH","false").lower() == "true"
WORKDIR = Path.cwd() / "_kai_global_work"
LOG = Path.cwd() / "logs" / "global_scan.log"
LOG.parent.mkdir(parents=True, exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[KAI-SCAN] {ts} - {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def safe_run(cmd, cwd=None, check=True):
    log(f"RUN: {' '.join(cmd)} (cwd={cwd or Path.cwd()})")
    return subprocess.run(cmd, cwd=cwd, check=check)

def ensure_clone(repo_full_name, clone_url, branch):
    if WORKDIR.exists():
        shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    try:
        safe_run(["git","clone","--depth","1","--branch",branch,clone_url], cwd=WORKDIR)
        return WORKDIR / repo_full_name.split("/",1)[1]
    except Exception as e:
        log(f"Clone failed for {repo_full_name}: {e}")
        return None

def make_marker(repo_dir, repo):
    marker = repo_dir / "KAI_SCAN.md"
    info = {
        "full_name": repo.full_name,
        "private": repo.private,
        "pushed_at": str(repo.pushed_at),
        "language": repo.language,
        "stargazers": repo.stargazers_count
    }
    marker.write_text("# KAI Scan\n\n" + json.dumps(info, indent=2), encoding="utf-8")
    log(f"Marker written for {repo.full_name}")

def commit_and_push(repo_dir, repo_full, default_branch):
    # commit changes, create branch & PR if not DIRECT_PUSH
    safe_run(["git","add","-A"], cwd=repo_dir, check=False)
    try:
        safe_run(["git","commit","-m","KAI automated scan update"], cwd=repo_dir, check=False)
    except Exception:
        log("Nothing to commit")
    if DIRECT_PUSH:
        try:
            safe_run(["git","push","origin", default_branch], cwd=repo_dir, check=False)
            log(f"Pushed changes directly to {repo_full}:{default_branch}")
        except Exception as e:
            log(f"Direct push failed for {repo_full}: {e}")
    else:
        branch = f"kai/scan-{int(time.time())}"
        try:
            safe_run(["git","checkout","-b",branch], cwd=repo_dir)
            safe_run(["git","push","-u","origin",branch], cwd=repo_dir, check=False)
            # create PR via API in main loop
            return branch
        except Exception as e:
            log(f"Branch/Push failed for {repo_full}: {e}")
    return None

def main():
    if not TOKEN:
        log("ERROR: No KAI_PAT/GITHUB_PAT found in environment. Exiting.")
        sys.exit(1)
    gh = Github(TOKEN, per_page=100)
    me = gh.get_user()
    log(f"Authenticated as {me.login}")

    # gather repos: personal repos
    repos = []
    for r in me.get_repos():
        repos.append(r)
    # gather org repos where user is member (if any)
    try:
        for org in me.get_orgs():
            for r in org.get_repos():
                repos.append(r)
    except Exception as e:
        log(f"Org fetch issue: {e}")

    log(f"Discovered {len(repos)} repos (raw). Filtering duplicates...")
    seen = set()
    unique = []
    for r in repos:
        if r.full_name in seen: continue
        seen.add(r.full_name)
        unique.append(r)
    log(f"{len(unique)} unique repos to scan.")

    for repo in unique:
        try:
            full = repo.full_name
            default_branch = repo.default_branch or "master"
            log(f"--> Scanning {full} (default {default_branch})")
            clone_url = f"https://x-access-token:{TOKEN}@github.com/{full}.git"
            repo_dir = ensure_clone(full, clone_url, default_branch)
            if not repo_dir:
                log(f"Skipping {full} due to clone failure")
                continue
            # safe scan: create marker file with metadata
            make_marker(repo_dir, repo)
            # attempt commit & push or branch & PR
            branch = commit_and_push(repo_dir, full, default_branch)
            if branch and not DIRECT_PUSH:
                # open PR via API
                try:
                    pr = repo.create_pull(title="KAI: automated scan/update", body="Automated scan marker from Kai", head=branch, base=default_branch)
                    log(f"Opened PR #{pr.number} on {full}")
                except Exception as e:
                    log(f"Failed to open PR on {full}: {e}")
            # cleanup
            shutil.rmtree(WORKDIR, ignore_errors=True)
        except Exception as e:
            log(f"Error processing {repo.full_name}: {e}")

    log("Scan complete.")
if __name__ == '__main__':
    main()
