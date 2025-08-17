import os, sys, subprocess, time, shutil, json, re
from pathlib import Path
from typing import List, Dict
from github import Github
import networkx as nx

OWNER = os.getenv("OWNER","").strip()
TOKEN = os.getenv("KAI_PAT","").strip()
DIRECT_PUSH = os.getenv("DIRECT_PUSH","false").lower() == "true"
TRENDING_LANG = os.getenv("TRENDING_LANG","python")

BASE = Path.cwd()
WORK = BASE / "_kai_work"
LOGS = BASE / "logs"
ART  = BASE / "artifacts"
LOGS.mkdir(exist_ok=True)
ART.mkdir(exist_ok=True)
LOG = LOGS / "evolve.log"

def log(msg: str):
    line = f"[KAI] {time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def run(cmd, cwd=None, check=True):
    log(f"RUN: {' '.join(cmd)} (cwd={cwd or os.getcwd()})")
    return subprocess.run(cmd, cwd=cwd, check=check)

def read_repos() -> List[str]:
    rows = []
    for line in (BASE/"repos.txt").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"): continue
        if "/" in s: rows.append(s)
    return rows

def load_objectives() -> List[Dict]:
    p = BASE/"kai_objectives.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

# --- Mutations (safe) ---
def ensure_editorconfig(repo_dir: Path):
    ec = repo_dir / ".editorconfig"
    if not ec.exists():
        ec.write_text(
            "root = true\n\n[*]\nend_of_line = lf\ninsert_final_newline = true\ncharset = utf-8\ntrim_trailing_whitespace = true\n",
            encoding="utf-8"
        )

def ensure_badge(repo_dir: Path):
    badge = "[![Maintained by Kai](https://img.shields.io/badge/maintained%20by-Kai-blue)](#)"
    readme = repo_dir / "README.md"
    if readme.exists():
        txt = readme.read_text(encoding="utf-8")
        if "Maintained by Kai" not in txt:
            readme.write_text(badge + "\n\n" + txt, encoding="utf-8")
    else:
        readme.write_text("# Repo\n\n" + badge + "\n", encoding="utf-8")

def auto_format(repo_dir: Path):
    # run black + flake8 (ignore failures)
    subprocess.run([sys.executable, "-m", "black", "."], cwd=repo_dir)
    subprocess.run(["flake8", "."], cwd=repo_dir)

def run_tests(repo_dir: Path) -> bool:
    # if tests folder or pytest.ini exists, attempt pytest
    has_tests = (repo_dir/"tests").exists() or (repo_dir/"pytest.ini").exists()
    if not has_tests: 
        log("No tests detected; skipping pytest")
        return True
    r = subprocess.run(["pytest", "-q"], cwd=repo_dir)
    return r.returncode == 0

def run_benchmarks(repo_dir: Path) -> Dict:
    # Discover simple *.py under "benchmarks" folder & time them
    results = {}
    bench_dir = repo_dir/"benchmarks"
    if bench_dir.exists():
        for p in bench_dir.rglob("*.py"):
            t0 = time.time()
            subprocess.run([sys.executable, str(p)], cwd=repo_dir)
            results[str(p.relative_to(repo_dir))] = round(time.time()-t0, 3)
    return results

# --- Knowledge Graph ---
def update_knowledge_graph(repo_full: str, repo_dir: Path, bench: Dict):
    gfile = ART / "knowledge_graph.json"
    if gfile.exists():
        data = json.loads(gfile.read_text(encoding="utf-8"))
        G = nx.node_link_graph(data)
    else:
        G = nx.DiGraph()

    # nodes: repo, files touched
    G.add_node(repo_full, type="repo")
    for p in repo_dir.rglob("*.py"):
        rel = str(p.relative_to(repo_dir))
        G.add_node(f"{repo_full}:{rel}", type="file")
        G.add_edge(repo_full, f"{repo_full}:{rel}", relation="contains")

    # benchmark edges
    for k,v in bench.items():
        node = f"{repo_full}:{k}"
        if not G.has_node(node):
            G.add_node(node, type="file")
        G.nodes[node]["benchmark_sec"] = v
        G.add_edge(repo_full, node, relation="benchmarked")

    # write back
    data = nx.node_link_data(G)
    gfile.write_text(json.dumps(data, indent=2), encoding="utf-8")

# --- Inspiration (GitHub Trending via search as proxy) ---
def fetch_inspiration(gh: Github, language: str="python", limit: int=5) -> List[str]:
    # Approximate “trending” via most-starred recent updates
    query = f"language:{language} sort:stars"
    repos = gh.search_repositories(query=query)
    out = []
    for i, r in enumerate(repos[:limit]):
        out.append(r.full_name)
    log(f"Inspiration sample: {out}")
    return out

def mutate_repo(repo_dir: Path, objectives: List[Dict]):
    # Apply enabled objectives in order (you can expand logic per task name)
    for obj in objectives:
        if not obj.get("enabled", True): 
            continue
        t = obj.get("task","").lower()
        if "editorconfig" in t:
            ensure_editorconfig(repo_dir)
        elif "badge" in t:
            ensure_badge(repo_dir)
        elif "flake8" in t or "black" in t or "format" in t:
            auto_format(repo_dir)
        elif "tests" in t:
            pass
        elif "knowledge graph" in t:
            pass
        elif "benchmark" in t:
            pass

def process_repo(gh: Github, full: str, objectives: List[Dict]):
    try:
        repo = gh.get_repo(full)
        default_branch = repo.default_branch or "master"
        log(f"=== Processing {full} (default={default_branch}) ===")

        # fresh workspace
        if WORK.exists(): shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True, exist_ok=True)

        url = f"https://x-access-token:{TOKEN}@github.com/{full}.git"
        run(["git", "clone", "--depth", "1", "--branch", default_branch, url], cwd=WORK)
        repo_dir = WORK / full.split("/",1)[1]
        if not repo_dir.exists():
            log(f"ERROR: clone failed for {full}")
            return

        # mutate + tests + benchmarks
        mutate_repo(repo_dir, objectives)
        tests_ok = run_tests(repo_dir)
        bench = run_benchmarks(repo_dir)

        # decision gate
        if not tests_ok:
            log("Tests failed -> not pushing changes.")
            # Save diff to artifact
            diff = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
            (ART/"failed_diff.patch").write_text(diff.stdout or "", encoding="utf-8")
            return

        # commit & push or PR
        run(["git", "config", "user.name", "Kai 2.0"], cwd=repo_dir)
        run(["git", "config", "user.email", "kai@auto.evolve"], cwd=repo_dir)
        subprocess.run(["git", "add", "-A"], cwd=repo_dir)
        subprocess.run(["git", "commit", "-m", "Kai 2.0: maintenance & evolution update"], cwd=repo_dir)

        if DIRECT_PUSH:
            subprocess.run(["git", "push", "origin", default_branch], cwd=repo_dir)
            log(f"Pushed directly to {full}:{default_branch}")
        else:
            branch = "kai/evolve-" + str(int(time.time()))
            subprocess.run(["git", "checkout", "-b", branch], cwd=repo_dir)
            subprocess.run(["git", "push", "-u", "origin", branch], cwd=repo_dir)
            pr = repo.create_pull(
                title="Kai 2.0: evolution update",
                body="Automated evolution by Kai 2.0 (safe & auditable).",
                head=branch,
                base=default_branch
            )
            log(f"Opened PR #{pr.number} on {full}")

        # knowledge graph
        update_knowledge_graph(full, repo_dir, bench)
        log(f"DONE: {full}")

    except Exception as e:
        log(f"ERROR {full}: {e}")

def main():
    if not OWNER or not TOKEN:
        log("ERROR: OWNER or KAI_PAT missing.")
        sys.exit(1)

    gh = Github(TOKEN, per_page=100)
    objectives = load_objectives()

    # inspiration (optional usage; currently just logs)
    fetch_inspiration(gh, language=TRENDING_LANG, limit=5)

    # iterate allowlisted repos
    targets = read_repos()
    if not targets:
        log("No repositories in repos.txt.")
        return

    for full in targets:
        process_repo(gh, full, objectives)

    log("All done.")

if __name__ == "__main__":
    main()
