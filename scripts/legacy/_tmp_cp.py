import sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from _clash_pool import ClashApiPool
t = time.time()
p = ClashApiPool(budget_per_node=8, nodes_file="data/out/clash_api_nodes.json")
tot = sum(x["remaining"] for x in p.nodes)
print(f"初始化耗时 {time.time()-t:.1f}s | 节点 {len(p.nodes)} | 合计 {tot:,}")
print("当前:", p.current()[:40])
for _ in range(20):
    p.record_ok(p.current())
print(p.note())
