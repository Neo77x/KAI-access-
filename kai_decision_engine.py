"""
kai_decision_engine.py

Decision-making loop (safe):
- Read repos.txt allowlist
- For each repo: clone default branch (depth=1) into workdir
- Compute baseline quality: tests pass? flake8 issues count
- Apply safe edits: format (black), fix simple lints (optional), run local "mutations"
- Recompute quality; compute score delta
- If delta >= threshold (IMPROVEMENT_MARGIN), then:
    - create branch, push to remote, create PR (or push directly if DIRECT_PUSH=true)
  Else:
    - discard changes, record artifact
- Store logs, diffs, and snapshot for audit
- Uses token from env: KAI_PAT (or GITHUB_TOKEN in Actions)
"""
import os, sys, time, json, shutil, subprocess
from pathlib import Path
from typing import Tuple
from github import Github

# CONFIG
IMPROVEMENT_MARGIN = 5.0  # percent improvement required to accept change
WORK_DIR = Path.cwd() / "_kai_work"
LOG_DIR  = Path.cwd() / "logs"
ART_DIR  = Path.cwd() / "artifacts"
LOG_DIR.mkdir(exist_ok=True)
ART_DIR.mkdir(exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[KaiDecision] {ts} - {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "decision.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, cwd=None, check=True):
    log("CMD: " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check)

def read_allowlist() -> list:
    p = Path("repos.txt")
    if not p.exists(): return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"): continue
        out.append(s)
    return out

def compute_quality(repo_dir: Path) -> Tuple[bool,int]:
    """
    Returns (tests_ok, lint_issues_count)
    tests_ok: True if pytest passes or no tests found
    lint_issues_count: number of flake8 issues (0 = perfect)
    """
    # run pytest if tests exist
    tests_ok = True
    try:
        if (repo_dir / "tests").exists() or (repo_dir / "pytest.ini").exists():
            r = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=repo_dir, capture_output=True, text=True)
            tests_ok = (r.returncode == 0)
            with open(ART_DIR / f"{repo_dir.name}_pytest_output.txt","w",encoding="utf-8") as f:
                f.write(r.stdout + "\n\n" + r.stderr)
    except Exception as e:
        log(f"pytest error: {e}")
        tests_ok = False

    # run flake8 (count issues)
    lint_count = 0
    try:
        r = subprocess.run(["flake8", "."], cwd=repo_dir, capture_output=True, text=True)
        lint_out = r.stdout.strip()
        if lint_out:
            lint_count = len(lint_out.splitlines())
        with open(ART_DIR / f"{repo_dir.name}_flake8.txt","w",encoding="utf-8") as f:
            f.write(lint_out)
    except Exception as e:
        log(f"flake8 error: {e}")
        lint_count = 9999

    return tests_ok, lint_count

def apply_mutations(repo_dir: Path):
    """
    SAFE MUTATIONS: formatting and simple improvements.
    Do NOT perform risky refactors here. Keep actions reversible.
    """
    try:
        run([sys.executable, "-m", "black", "."], cwd=repo_dir)
    except Exception as e:
        log(f"black formatting error: {e}")
    # Could add targeted fixes here (e.g., simple import sorting). Keep minimal.

def snapshot_repo(repo_dir: Path, tag: str):
    """
    Save a small zip snapshot (diff) for auditing
    """
    try:
        zip_path = ART_DIR / f"{repo_dir.name}_{tag}.zip"
        if zip_path.exists():
            zip_path.unlink()
        shutil.make_archive(str(zip_path.with_suffix("")), 'zip', root_dir=repo_dir)
        log(f"Snapshot saved: {zip_path}")
    except Exception as e:
        log(f"Snapshot error: {e}")

def commit_and_branch_push(repo_dir: Path, gh_repo, branch_name: str, direct_push: bool):
    try:
        run(["git","add","-A"], cwd=repo_dir, check=False)
        run(["git","commit","-m", "KaiDecision: safe maintenance & formatting"], cwd=repo_dir, check=False)
    except Exception as e:
        log(f"Commit likely had no changes or error: {e}")
    if direct_push:
        try:
            run(["git","push","origin", branch_name if branch_name else gh_repo.default_branch], cwd=repo_dir, check=False)
            log(f"Direct pushed changes to {gh_repo.full_name}:{branch_name or gh_repo.default_branch}")
            return None
        except Exception as e:
            log(f"Direct push failed: {e}")
            return None
    else:
        try:
            # push branch
            run(["git","checkout","-b", branch_name], cwd=repo_dir)
            run(["git","push","-u","origin", branch_name], cwd=repo_dir, check=False)
            pr = gh_repo.create_pull(title="KaiDecision: automated safe update", body="Automated safe maintenance by KaiDecision", head=branch_name, base=gh_repo.default_branch or "master")
            log(f"Created PR #{pr.number} for {gh_repo.full_name}")
            return pr.number
        except Exception as e:
            log(f"Branch/PR creation failed: {e}")
            return None

def quality_score(tests_ok_before, lint_before, tests_ok_after, lint_after) -> float:
    """
    Compute a simple score: base 100 for passing tests, minus lint_count.
    Score = (tests_score*100) - lint_count
    tests_score: 1 if tests_ok else 0.5 if no tests, 0 if failed
    """
    def tests_score(ok):
        return 1.0 if ok else 0.0
    before = tests_score(tests_ok_before)*100 - lint_before
    after  = tests_score(tests_ok_after)*100 - lint_after
    # Normalize to percent improvement
    if before == 0:
        # if before is 0 (very bad), treat any positive after as big improvement
        if after > 0:
            return 100.0
        else:
            return 0.0
    delta = (after - before) / abs(before) * 100.0
    return delta

def process_repo(gh: Github, repo_full: str, token: str, direct_push_env: str):
    try:
        repo = gh.get_repo(repo_full)
        default_branch = repo.default_branch or "master"
        log(f"Processing {repo_full} (default {default_branch})")
        # prepare workdir
        if WORK_DIR.exists():
            shutil.rmtree(WORK_DIR, ignore_errors=True)
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        clone_url = f"https://x-access-token:{token}@github.com/{repo_full}.git"
        # clone
        try:
            run(["git","clone","--depth","1","--branch", default_branch, clone_url], cwd=WORK_DIR)
        except Exception as e:
            log(f"Clone failed for {repo_full}: {e}")
            return
        repo_dir = WORK_DIR / repo_full.split("/",1)[1]

        # baseline
        tests_b, lint_b = compute_quality(repo_dir)
        log(f"Baseline: tests_ok={tests_b}, lint_issues={lint_b}")
        snapshot_repo(repo_dir, "before")

        # apply safe mutations
        apply_mutations(repo_dir)

        # after
        tests_a, lint_a = compute_quality(repo_dir)
        log(f"After: tests_ok={tests_a}, lint_issues={lint_a}")
        snapshot_repo(repo_dir, "after")

        # compute delta
        delta_percent = quality_score(tests_b, lint_b, tests_a, lint_a)
        log(f"Quality delta percent = {delta_percent:.2f}%")

        direct_push = str(direct_push_env).lower() == "true"
        # decide
        if delta_percent >= IMPROVEMENT_MARGIN:
            log("ACCEPT: improvement threshold met -> propose/commit changes")
            # prepare branch name
            branch = "kai/decision-" + str(int(time.time()))
            pr_num = commit_and_branch_push(repo_dir, repo, branch, direct_push)
            # record artifact
            with open(ART_DIR / f"{repo_dir.name}_decision.json","w",encoding="utf-8") as f:
                json.dump({"repo":repo_full,"delta":delta_percent,"pr":pr_num, "pushed":direct_push}, f, indent=2)
        else:
            log("REJECT: improvement below threshold -> discarding changes")
            # save diff for audit
            try:
                diff = subprocess.run(["git","diff"], cwd=repo_dir, capture_output=True, text=True)
                (ART_DIR / f"{repo_dir.name}_rejected.diff").write_text(diff.stdout or "", encoding="utf-8")
            except Exception as e:
                log(f"Diff failed: {e}")
        # cleanup
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    except Exception as e:
        log(f"Error processing {repo_full}: {e}")

def main():
    token = os.environ.get("KAI_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_PAT")
    if not token:
        log("ERROR: KAI_PAT/GITHUB_TOKEN not found in env.")
        sys.exit(1)
    direct_env = os.environ.get("DIRECT_PUSH","false")
    gh = Github(token, per_page=100)
    allow = read_allowlist()
    if not allow:
        log("No repos in repos.txt allowlist. Exiting.")
        return
    for repo_full in allow:
        process_repo(gh, repo_full, token, direct_env)
    log("Decision run complete.")
if __name__ == "__main__":
    main()
