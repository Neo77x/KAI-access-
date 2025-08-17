"""
kai_pro_trainer.py  — Kai Pro Programmer orchestrator

Behaviors:
- Read allowlist repos (repos.txt)
- For each repo: clone (depth=1), run analyzers (black, flake8), run pytest if tests exist
- Run self-challenges from pro_kai/challenges (pytest style)
- Update simple knowledge graph (networkx -> JSON)
- Create PRs for suggested fixes (or push directly if DIRECT_PUSH=true)

Env:
  KAI_PAT (secret)
  DIRECT_PUSH ("true" or "false")
"""
import os, sys, time, json, shutil, subprocess
from pathlib import Path
from github import Github
import networkx as nx

BASE = Path.cwd()
WORK = BASE / "_kai_work"
LOGS = BASE / "logs"
ART = BASE / "artifacts"
KG = ART / "knowledge_graph.json"
REPOS_FILE = BASE / "repos.txt"
CHALLENGES_DIR = BASE / "pro_kai" / "challenges"

LOGS.mkdir(exist_ok=True)
ART.mkdir(exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[KaiPro] {ts} - {msg}"
    print(line, flush=True)
    with open(LOGS/"trainer.log","a",encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, cwd=None, check=True):
    log("CMD: " + " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check)

def read_allowlist():
    if not REPOS_FILE.exists():
        return []
    rows = []
    for ln in REPOS_FILE.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"): continue
        rows.append(s)
    return rows

def ensure_clone(full_name, token):
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    default_branch = "master"
    # Get default branch via API if possible
    try:
        gh = Github(token)
        repo_obj = gh.get_repo(full_name)
        default_branch = repo_obj.default_branch or "master"
    except Exception:
        pass
    clone_url = f"https://x-access-token:{token}@github.com/{full_name}.git"
    try:
        run(["git","clone","--depth","1","--branch",default_branch,clone_url], cwd=WORK)
        repo_dir = WORK / full_name.split("/",1)[1]
        return repo_dir, default_branch
    except Exception as e:
        log(f"Clone failed {full_name}: {e}")
        return None, default_branch

def run_analyzers(repo_dir):
    # run black (format) and flake8 (lint) - ignore non-zero lints to continue
    try:
        run([sys.executable,"-m","black","."], cwd=repo_dir)
    except Exception as e:
        log(f"Black failed: {e}")
    try:
        run(["flake8","."], cwd=repo_dir, check=False)
    except Exception as e:
        log(f"Flake8 issue: {e}")

def run_tests(repo_dir):
    if (repo_dir/"tests").exists() or (repo_dir/"pytest.ini").exists():
        try:
            res = run(["pytest","-q"], cwd=repo_dir, check=False)
            return res.returncode == 0
        except Exception:
            return False
    return True

def run_challenges():
    # Run local challenge suites (pytest) in pro_kai/challenges
    if not CHALLENGES_DIR.exists(): return {}
    results = {}
    try:
        run([sys.executable,"-m","pytest","-q", str(CHALLENGES_DIR)], cwd=BASE, check=False)
        results["challenges_ran"] = True
    except Exception as e:
        results["challenges_error"] = str(e)
    return results

def update_knowledge_graph(repo_full, repo_dir, bench):
    G = nx.DiGraph()
    if KG.exists():
        try:
            data = json.loads(KG.read_text(encoding="utf-8"))
            G = nx.node_link_graph(data)
        except Exception:
            G = nx.DiGraph()
    # add repo node
    G.add_node(repo_full, type="repo", touched=time.time())
    for p in repo_dir.rglob("*.py"):
        node = f"{repo_full}:{p.relative_to(repo_dir)}"
        G.add_node(node, type="file")
        G.add_edge(repo_full,node,relation="contains")
    # add bench info
    for k,v in bench.items():
        n = f"{repo_full}:{k}"
        if not G.has_node(n): G.add_node(n, type="bench")
        G.nodes[n]["time"] = v
        G.add_edge(repo_full,n,relation="benchmarked")
    data = nx.node_link_data(G)
    KG.write_text(json.dumps(data,indent=2),encoding="utf-8")

def commit_and_pr(repo_dir, repo_full, default_branch, gh_repo, token, direct_push=False):
    try:
        run(["git","add","-A"], cwd=repo_dir, check=False)
        # commit (if nothing changed, commit will do nothing)
        try:
            run(["git","commit","-m","KaiPro: maintenance & format changes"], cwd=repo_dir, check=False)
        except Exception:
            pass
        if direct_push:
            try:
                run(["git","push","origin",default_branch], cwd=repo_dir, check=False)
                log(f"Pushed directly to {repo_full}:{default_branch}")
                return
            except Exception as e:
                log(f"Direct push failed: {e}")
        # otherwise create branch & PR
        branch = "kaipro/auto-" + str(int(time.time()))
        run(["git","checkout","-b",branch], cwd=repo_dir)
        run(["git","push","-u","origin",branch], cwd=repo_dir, check=False)
        pr = gh_repo.create_pull(title="KaiPro: auto maintenance", body="Automated maintenance by KaiPro", head=branch, base=default_branch)
        log(f"Opened PR #{pr.number} on {repo_full}")
    except Exception as e:
        log(f"Failed commit/pr for {repo_full}: {e}")

def benchmark_simple(repo_dir):
    results = {}
    bench_dir = repo_dir/"benchmarks"
    if bench_dir.exists():
        for p in bench_dir.rglob("*.py"):
            t0 = time.time()
            try:
                run([sys.executable,str(p)], cwd=repo_dir, check=False)
                results[str(p.relative_to(repo_dir))] = round(time.time()-t0,3)
            except Exception as e:
                results[str(p.relative_to(repo_dir))] = "err"
    return results

def main():
    token = os.environ.get("KAI_PAT") or os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN")
    if not token:
        log("ERROR: KAI_PAT not set in env.")
        sys.exit(1)
    direct = os.environ.get("DIRECT_PUSH","false").lower() == "true"
    gh = Github(token, per_page=100)
    allow = read_allowlist()
    objectives = []
    try:
        if (BASE/"kai_objectives.json").exists():
            objectives = json.loads((BASE/"kai_objectives.json").read_text(encoding="utf-8"))
    except Exception:
        objectives = []
    # run local challenges
    run_challenges()
    for full in allow:
        try:
            log(f"Processing {full}")
            repo_dir, default_branch = ensure_clone(full, token)
            if not repo_dir:
                log(f"Skip {full}")
                continue
            # analyzers and tests
            run_analyzers(repo_dir)
            ok = run_tests(repo_dir)
            bench = benchmark_simple(repo_dir)
            if not ok:
                log(f"Tests failed for {full}; not creating PR/push.")
                continue
            # prepare GH repo object
            gh_repo = gh.get_repo(full)
            commit_and_pr(repo_dir, full, default_branch, gh_repo, token, direct_push=direct)
            update_knowledge_graph(full, repo_dir, bench)
            log(f"Done {full}")
        except Exception as e:
            log(f"Error {full}: {e}")
    log("All repos processed.")
if __name__ == "__main__":
    main()
