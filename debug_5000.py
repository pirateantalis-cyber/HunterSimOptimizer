#!/usr/bin/env python
import sys, json, tempfile, time
sys.path.insert(0, 'hunter-sim')

# Test with 5000 builds per tier
config = {
    'hunter_name': 'Borge',
    'level': 69,
    'base_config': {'stats': {}},
    'num_sims': 50,
    'builds_per_tier': 5000,
    'use_progressive': True
}

config_file = tempfile.NamedTemporaryFile(mode='w', suffix='_config.json', delete=False).name
result_file = config_file.replace('_config.json', '_results.json')

with open(config_file, 'w') as f:
    json.dump(config, f)

print(f'Testing with 5000 builds per tier (30,000 total)...')

from run_optimization import run_optimization

start = time.time()
try:
    run_optimization(config_file, result_file)
    elapsed = time.time() - start
    print(f'✅ Completed in {elapsed:.1f}s')
    
    with open(result_file) as f:
        results = json.load(f)
    
    if results.get('status') == 'complete':
        print(f'Tested: {results["timing"]["tested"]} builds')
        print(f'Tiers: {len(results["generation_history"])}')
        for gen in results['generation_history']:
            print(f"  Gen {gen['generation']} ({gen['tier_name']}): {gen['builds_tested']} builds")
except Exception as e:
    elapsed = time.time() - start
    print(f'❌ FAILED after {elapsed:.1f}s: {e}')
    import traceback
    traceback.print_exc()

import os
os.unlink(config_file)
