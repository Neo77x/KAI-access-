# agent_core.py
import os, json, time, shutil
from pathlib import Path
from github import Github
from llm_adapter import call_llm
from planner import propose_candidates
from evaluator import compute_quality, snapshot
from memory import add_memory, query_mem

CONFIG = json.loads(open("agent_config.json","r",encoding="utf-8").read())
IMPROVEMENT_MARGIN = float(CONFIG.get("IMPROVEMENT_MARGIN",5.0))
MAX_ITER = int(CONFIG.get("MAX_ITERATIONS",10))
KILL_FILE = CONFIG.get("KILL_SWITCH_FILE","KILL_KAI")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[AgentCore] {ts} - {msg}"
    print(line, flush=True)
    open("logs/agent_core.log","a",encoding="utf-8").write(line + "\n")

def check_kill():
    if Path(KILL_FILE).exists():
        log("KILL switch present — aborting run.")
        return True
    return False

def read_allowlist():
    p = Path("repos.txt")
    if not p.exists(): return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"): continue
        out.append(s)
    return out

def safe_write_file(repo_dir, relpath, content):
    target = repo_dir / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

def apply_candidate(repo_dir, candidate):
    # candidate: {"file": path, "new_content": full_file_content, "reason": ""}
    try:
        safe_write_file(repo_dir, candidate["file"], candidate["new_content"])
        return True
    except Exception as e:
        log(f"Failed to apply candidate: {e}")
        return False

def compute_score(tests_ok, lint_count):
    score = (100 if tests_ok else 0) - lint_count
    return score

def process_repo(gh, token, full):
    try:
        repo = gh.get_repo(full)
        default_branch = repo.default_branch or "master"
        log(f"Processing {full} (branch {default_branch})")
        work = Path("_kai_work")
        if work.exists(): shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        clone_url = f"https://x-access-token:{token}@github.com/{full}.git"
        # clone
        import subprocess
        try:
            subprocess.run(["git","clone","--depth","1","--branch",default_branch,clone_url], cwd=work, check=True)
        except Exception as e:
            log(f"Clone failed: {e}")
            return
        repo_dir = work / full.split("/",1)[1]
        # baseline
        tests_b, lint_b = compute_quality(repo_dir)
        score_b = compute_score(tests_b, lint_b)
        log(f"Baseline score: {score_b} (tests_ok={tests_b}, lint={lint_b})")
        snapshot(repo_dir,"before")
        # repo summary for planner
        repo_summary = f"name: {full}\\nfiles: {len(list(repo_dir.rglob('*')))}\\nmain language: {repo.language}\\n"
        # propose candidates
        candidates = propose_candidates(repo_summary, n=MAX_ITER)
        log(f"Planner returned {len(candidates)} candidates")
        accepted = False
        for cand in candidates:
            if check_kill(): return
            apply_candidate(repo_dir, cand)
            tests_a, lint_a = compute_quality(repo_dir)
            score_a = compute_score(tests_a, lint_a)
            delta = 0.0
            if score_b != 0:
                delta = (score_a - score_b) / abs(score_b) * 100.0
            elif score_a > 0:
                delta = 100.0
            log(f"Candidate result: score_a={score_a} delta={delta:.2f}%")
            if delta >= IMPROVEMENT_MARGIN:
                log("Candidate is an improvement -> commit & propose")
                # commit and push
                try:
                    subprocess.run(["git","config","user.name","KaiAgent"], cwd=repo_dir)
                    subprocess.run(["git","config","user.email","kai@agent.local"], cwd=repo_dir)
                    subprocess.run(["git","add","-A"], cwd=repo_dir)
                    subprocess.run(["git","commit","-m","KaiAgent: automated safe improvement"], cwd=repo_dir)
                    branch = "kai/auto-" + str(int(time.time()))
                    subprocess.run(["git","checkout","-b",branch], cwd=repo_dir)
                    subprocess.run(["git","push","-u","origin",branch], cwd=repo_dir)
                    pr = repo.create_pull(title="KaiAgent: safe improvement", body=cand.get("reason","Automated improvement by KaiAgent"), head=branch, base=default_branch)
                    log(f"Created PR #{pr.number}")
                    add_memory(f"Accepted change on {full}: {cand.get('reason','')}", {"repo": full})
                    accepted = True
                    break
                except Exception as e:
                    log(f"Push/PR failed: {e}")
            else:
                # reject -> save diff
                try:
                    diff = subprocess.run(["git","diff"], cwd=repo_dir, capture_output=True, text=True)
                    open("artifacts/" + repo_dir.name + "_rejected.diff","w",encoding="utf-8").write(diff.stdout or "")
                except Exception:
                    pass
                # revert files by re-cloning for next candidate
                shutil.rmtree(repo_dir, ignore_errors=True)
                subprocess.run(["git","clone","--depth","1","--branch",default_branch,clone_url], cwd=work)
        snapshot(repo_dir,"after")
        if not accepted:
            log(f"No candidate accepted for {full}")
        shutil.rmtree(work, ignore_errors=True)
    except Exception as e:
        log(f"Error process_repo {full}: {e}")

def main():
    if check_kill():
        return
    cfg = json.load(open("agent_config.json","r",encoding="utf-8"))
    token = os.environ.get("KAI_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        log("Missing KAI_PAT in env.")
        return
    allow = read_allowlist()
    if not allow:
        log("No repos in repos.txt - nothing to do.")
        return
    gh = Github(token, per_page=100)
    for full in allow:
        if check_kill(): break
        process_repo(gh, token, full)
    log("Agent run finished.")
if __name__ == "__main__":
    main()
