import sys, json, time, threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,"scripts/collect")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import collect_core as core, fetchers as F
cfg=json.load(open("data/proxy_tunnel.json",encoding="utf-8"))
tun={"http":f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/",
     "https":f"http://{cfg['username']}:{cfg['password']}@{cfg['host']}/"}
ctx=F.Ctx(proxies=tun, proxies_html=tun, year=2026, timeout=12)
col=core.Collector(tag="bettafish", out_dir="data/raw",
                   fetch=lambda dim,lg: F.fetch(dim,lg,ctx))
logins=[(json.loads(l).get("login") or "").lower() for l in open("data/raw/forks_all_666ghj__BettaFish.jsonl",encoding="utf-8") if l.strip()]
dims=F.ORDER_PER_ACCOUNT
lock=threading.Lock(); done={"n":0}
def one(lg):
    marks=[]
    for dim in dims:
        w=col.writers[dim]
        if w.done(lg):
            marks.append(dim+":skip"); continue
        t0=time.time()
        rec=col._unit(dim,lg)
        w.write(rec)
        marks.append(f"{dim}:{rec.outcome[:4]}({time.time()-t0:.1f})")
    with lock:
        done["n"]+=1
        if done["n"]<=3: print(f"  {lg[:3]}*** " + " ".join(marks), flush=True)
t0=time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(one, logins[:40]))
print(f"40 个账号 / 4 并发 = {time.time()-t0:.1f}s")
