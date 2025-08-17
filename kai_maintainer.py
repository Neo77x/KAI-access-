import os, subprocess, time, shutil, sys
from pathlib import Path
from github import Github

OWNER = os.getenv("OWNER","").strip()
TOKEN = os.getenv("KAI_PAT","").strip()
DIRECT_PUSH = os.getenv("DIRECT_PUSH","false").lower() == "true"

LOG = Path("maintainer.log")
def log(msg):
    line = f"[KAI] {time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    LOG.write_text((LOG.read_text(encoding="utf-8") if LOG.exists() else "") + line + "\n", encoding="utf-8")

def run(cmd, cwd=None, check=True):
    log(f"RUN: {' '.join(cmd)}  (cwd={cwd or os.getcwd()})")
    return subprocess.run(cmd, cwd=cwd, check=check)

def read_repos_list():
    rows = []
    for line in Path("repos.txt").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"): continue
        if "/" not in s: continue
        rows.append(s)
    return rows

def apply_safe_mutations(repo_dir: Path):
    """Add/normalize .editorconfig, ensure README badge, etc. Expand as you like."""
    # Example: ensure .editorconfig
    ec = repo_dir / ".editorconfig"
    if not ec.exists():
        ec.write_text(
            "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\ntrim_trailing_whitespace = true\n",
            encoding="utf-8"
        )
    # Example: add a “Maintained by Kai” badge if README.md exists
    readme = repo_dir / "README.md"
    badge = "[![Maintained by Kai](https://img.shields.io/badge/maintained%20by-Kai-blue)](#)"
    if readme.exists():
        txt = readme.read_text(encoding="utf-8")
        if "Maintained by Kai" not in txt:
            readme.write_text(badge + "\n\n" + txt, encoding="utf-8")
    else:
        readme.write_text("# Repo\n\n" + badge + "\n", encoding="utf-8")

def main():
    if not OWNER or not TOKEN:
        log("ERROR: OWNER or KAI_PAT missing.")
        sys.exit(1)

    repos = read_repos_list()
    if not repos:
        log("No repositories listed in repos.txt. Nothing to do.")
        return

    work = Path("_kai_work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    gh = Github(TOKEN, per_page=100)

    for full in repos:
        try:
            repo = gh.get_repo(full)
            default_branch = repo.default_branch or "master"
            log(f"=== Processing {full} (default={default_branch}) ===")

            # clone
            url = f"https://x-access-token:{TOKEN}@github.com/{full}.git"
            run(["git", "clone", "--depth", "1", "--branch", default_branch, url], cwd=work)
            repo_dir = work / full.split("/",1)[1]
            if not repo_dir.exists():
                log(f"ERROR: clone failed for {full}")
                continue

            # mutate
            apply_safe_mutations(repo_dir)

            # commit
            run(["git", "config", "user.name", "Kai Maintainer"], cwd=repo_dir)
            run(["git", "config", "user.email", "kai@maintainer.bot"], cwd=repo_dir)
            run(["git", "add", "-A"], cwd=repo_dir, check=False)
            subprocess.run(["git", "commit", "-m", "Kai: maintenance update"], cwd=repo_dir)

            if DIRECT_PUSH:
                # push to default branch (explicit choice)
                subprocess.run(["git", "push", "origin", default_branch], cwd=repo_dir)
                log(f"Pushed directly to {full}:{default_branch}")
            else:
                # create PR
                branch = "kai/maintenance-" + str(int(time.time()))
                run(["git", "checkout", "-b", branch], cwd=repo_dir)
                run(["git", "push", "-u", "origin", branch], cwd=repo_dir, check=False)
                pr = repo.create_pull(
                    title="Kai: maintenance update",
                    body="Automated maintenance by Kai (explicitly approved).",
                    head=branch,
                    base=default_branch
                )
                log(f"Opened PR #{pr.number} on {full}")

            log(f"DONE: {full}")
        except Exception as e:
            log(f"ERROR {full}: {e}")

    log("All done.")

if __name__ == "__main__":
    main()
