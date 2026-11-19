import subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
JOBS=[
 ("Ebony-Vinyl/dsh-our-free-model","dsh"),
 ("farion1231/cc-switch","ccswitch"),
 ("debpalash/VoiceStudio","voice"),
 ("bilawalsidhu/gods-eye-view","gods"),
 ("karpathy/autoresearch","autores"),
]
for repo,tag in JOBS:
    t0=time.time()
    print(f"=== {repo} D2 @ {time.strftime('%H:%M:%S')} ===", flush=True)
    p=subprocess.run([sys.executable,"scripts/collect/collect_full.py",
        "--repo",repo,"--tag",tag,"--sample","2000","--workers","10",
        "--dims","D2","--max-minutes","25"],
        capture_output=True)
    out=p.stdout.decode("utf-8",errors="replace")
    for line in out.splitlines()[-2:]:
        print("   "+line, flush=True)
    print(f"   用时 {time.time()-t0:.0f}s", flush=True)
print("D2 全部完成")
