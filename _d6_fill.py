import subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
JOBS=[
 ("shardeum/shardeum","shardeum",715),
 ("unionlabs/union","union",2000),
 ("666ghj/BettaFish","bettafish",2000),
 ("morluto/rea","rea",2000),
 ("openclaw/openclaw","openclaw",2000),
]
for repo,tag,n in JOBS:
    t0=time.time()
    print(f"\n=== {repo} D6 (采样 {n}) @ {time.strftime('%H:%M:%S')} ===", flush=True)
    p=subprocess.run([sys.executable,"scripts/collect/collect_full.py",
        "--repo",repo,"--tag",tag,"--sample",str(n),"--workers","12",
        "--dims","D6","--max-minutes","45"],
        capture_output=True)
    out=p.stdout.decode("utf-8",errors="replace")
    for line in out.splitlines()[-2:]:
        print("   "+line, flush=True)
    print(f"   用时 {time.time()-t0:.0f}s", flush=True)
print("\nD6 补齐全部完成")
