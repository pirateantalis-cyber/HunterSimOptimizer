"""Quick test to verify threading and native bindings work."""
import sys
sys.path.insert(0, r"c:\Users\andrew.antalis\hunter-sim\hunter-sim")

import hunter_sim_lib as lib
import time
import json

print(f"Available cores: {lib.get_available_cores()}")
print(f"Thread pool size: {lib.get_thread_count()}")

# Run benchmark
config_path = r"c:\Users\andrew.antalis\hunter-sim\builds\sanity-checks\sanity_chk.yaml"
num_sims = 10000

print(f"\nRunning {num_sims} simulations (watch Task Manager for CPU usage)...")
start = time.time()
result = lib.simulate_from_file(config_path, num_sims, True)
elapsed = time.time() - start

stats = json.loads(result)
print(f"Done in {elapsed:.2f}s = {num_sims/elapsed:.0f} sims/sec")
# Check the actual structure
if 'stats' in stats:
    print(f"Average stage: {stats['stats']['avg_stage']:.1f}")
else:
    print(f"Structure: {list(stats.keys())[:5]}")
    if 'avg_stage' in stats:
        print(f"Average stage: {stats['avg_stage']:.1f}")

# Run a longer benchmark to see sustained CPU usage
print(f"\nRunning larger benchmark (50,000 sims)...")
num_sims = 50000
start = time.time()
result = lib.simulate_from_file(config_path, num_sims, True)
elapsed = time.time() - start
print(f"Done in {elapsed:.2f}s = {num_sims/elapsed:.0f} sims/sec")
