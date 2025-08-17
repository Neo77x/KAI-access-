import os, sys, subprocess, shutil, time
from pathlib import Path
from github import Github

OWNER = os.getenv("OWNER", "").strip()
TOKEN = os.getenv("GITHUB_PAT", "").strip()
LOG   = Path("multi_repo.log")

def log(msg):
    line = f"[KAI] {time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def safe_run(cmd, cwd=None, check=True):
    log(f"RUN: {' '.join(cmd)}  (cwd={cwd or os.getcwd()})")
    return subprocess.run(cmd, cwd=cwd, check=check)

def should_skip(repo):
    # Skip archived/disabled/mirror/fork to reduce noise; tweak as you like
    try:
        if getattr(repo, "archived", False): return True
        if getattr(repo, "disabled", False): return True
        if getattr(repo, "mirror_url", None): return True
        # Process only repos owned by OWNER (not starred, etc.)
        if repo.owner.login.lower() != OWNER.lower(): return True
    except Exception:
        return True
    return False

def mutate_repo(repo_path: Path):
    """
    <- PLACE YOUR MUTATIONS HERE ->
    Example: touch a marker file, bump a version, fix formatting, etc.
    """
    # Example mutation: ensure a KAI touch marker exists/updates each run
    marker = repo_path / "KAI_TOUCH.md"
    content = f"# Touched by Kai Overlord\n\nTimestamp: {time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
    marker.write_text(content, encoding="utf-8")

def main():
    if not OWNER or not TOKEN:
        log("ERROR: OWNER or GITHUB_PAT missing.")
        sys.exit(1)

    gh = Github(TOKEN, per_page=100)
    log(f"Authenticating as OWNER={OWNER} ...")
    # Pull both personal and org repos via search to avoid pagination headaches
    # But simplest path: list user repos (includes private if token permits)
    user = gh.get_user(OWNER)
    repos = list(user.get_repos())  # may be many; GitHub-hosted runner can handle iteration

    base = Path.cwd()
    work = base / "_kai_overlord_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    for repo in repos:
        try:
            full = repo.full_name
            if should_skip(repo):
                log(f"SKIP: {full}")
                continue

            default_branch = (repo.default_branch or "master")
            log(f"=== Processing {full} (default: {default_branch}) ===")

            # Clone with PAT over HTTPS
            clone_url = f"https://x-access-token:{TOKEN}@github.com/{full}.git"
            safe_run(["git", "clone", "--depth", "1", "--branch", default_branch, clone_url], cwd=work)

            repo_dir = work / repo.name
            if not repo_dir.exists():
                log(f"ERROR: clone failed for {full}")
                continue

            # Mutate
            mutate_repo(repo_dir)

            # Commit + push
            safe_run(["git", "config", "user.name", "Kai Overlord"], cwd=repo_dir)
            safe_run(["git", "config", "user.email", "kai@overlord.bot"], cwd=repo_dir)
            safe_run(["git", "add", "-A"], cwd=repo_dir, check=False)
            # If nothing to commit, commit will fail -> ignore
            subprocess.run(["git", "commit", "-m", "Kai Overlord: automated update"], cwd=repo_dir)
            # Force push? You asked for dominance; but safer is normal push:
            # change to ["--force"] if you insist on rewriting history.
            safe_run(["git", "push", "origin", default_branch], cwd=repo_dir, check=False)

            log(f"DONE: {full}")
        except Exception as e:
            log(f"ERROR processing {getattr(repo, 'full_name', 'unknown')}: {e}")

    log("All done.")

if __name__ == "__main__":
    main()
