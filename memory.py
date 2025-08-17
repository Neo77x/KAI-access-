# memory.py
import os, json, time
from difflib import SequenceMatcher
MEM_FILE = "kai_memory.json"
def _load():
    if os.path.exists(MEM_FILE):
        try:
            return json.load(open(MEM_FILE,"r",encoding="utf-8"))
        except:
            return []
    return []
def _save(data):
    json.dump(data, open(MEM_FILE,"w",encoding="utf-8"), indent=2)
def add_memory(text, meta=None):
    mem = _load()
    mem.append({"text": text, "meta": meta or {}, "ts": time.time()})
    _save(mem)
def query_mem(q, top=5):
    mem = _load()
    scored = []
    for m in mem:
        s = SequenceMatcher(None, q, m.get("text","")).ratio()
        scored.append((s,m))
    scored.sort(key=lambda x:x[0], reverse=True)
    return [m for _,m in scored[:top]]
