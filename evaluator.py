# evaluator.py
import subprocess, os, time
from pathlib import Path
ART = Path("artifacts")
ART.mkdir(exist_ok=True)
def run_cmd(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)
def compute_quality(repo_dir):
    # run pytest if exists
    tests_ok = True
    try:
        if (repo_dir/"tests").exists() or (repo_dir/"pytest.ini").exists():
            r = run_cmd(["pytest","-q"], cwd=repo_dir, check=False)
            tests_ok = (r.returncode == 0)
            open(ART / f"{repo_dir.name}_pytest.txt","w",encoding="utf-8").write(r.stdout + "\n\n" + r.stderr)
    except Exception as e:
        tests_ok = False
    # lint via flake8
    lint_count = 0
    try:
        r = run_cmd(["flake8","."], cwd=repo_dir, check=False)
        lint_out = r.stdout.strip()
        if lint_out:
            lint_count = len(lint_out.splitlines())
        open(ART / f"{repo_dir.name}_flake8.txt","w",encoding="utf-8").write(lint_out)
    except Exception:
        lint_count = 9999
    return tests_ok, lint_count
def snapshot(repo_dir, tag):
    import shutil
    zipname = ART / f"{repo_dir.name}_{tag}.zip"
    if zipname.exists(): zipname.unlink()
    shutil.make_archive(str(zipname.with_suffix("")), "zip", root_dir=repo_dir)
