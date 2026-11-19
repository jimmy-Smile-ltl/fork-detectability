import subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
JOBS=[
 ("Ebony-Vinyl/dsh-our-free-model","dsh",2296),
 ("farion1231/cc-switch","ccswitch",2000),
 ("debpalash/VoiceStudio","voice",2000),
 ("bilawalsidhu/gods-eye-view","gods",2000),
 ("karpathy/autoresearch","autores",2000),
]
for repo,tag,n in JOBS:
    t0=time.time()
    print(f"\n=== {repo} D15 (采样 {n}) @ {time.strftime('%H:%M:%S')} ===", flush=True)
    p=subprocess.run([sys.executable,"scripts/collect/collect_full.py",
        "--repo",repo,"--tag",tag,"--sample",str(n),"--workers","12",
        "--dims","D15","--max-minutes","70"],
        capture_output=True)
    out=p.stdout.decode("utf-8",errors="replace")
    for line in out.splitlines()[-3:]:
        print("   "+line, flush=True)
    print(f"   用时 {time.time()-t0:.0f}s", flush=True)
print("\nD15 全部完成")
