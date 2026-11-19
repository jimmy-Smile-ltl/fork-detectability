import subprocess, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPOS=["Ebony-Vinyl/dsh-our-free-model","farion1231/cc-switch",
       "debpalash/VoiceStudio","bilawalsidhu/gods-eye-view","karpathy/autoresearch"]
for r in REPOS:
    t0=time.time()
    print(f"=== {r} @ {time.strftime('%H:%M:%S')} ===", flush=True)
    p=subprocess.run([sys.executable,"scripts/collect/collect_run.py","--repo",r,"--list-only"],
                     capture_output=True)
    out=p.stdout.decode("utf-8",errors="replace")
    for line in out.splitlines()[-2:]:
        print("   "+line, flush=True)
    print(f"   用时 {time.time()-t0:.0f}s", flush=True)
    time.sleep(2)
print("名单全部完成")
