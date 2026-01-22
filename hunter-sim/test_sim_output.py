"""Test simulation output for loot and XP."""
from hunters import Borge
from sim import Simulation
import yaml

# Use empty borge build file  
with open('../builds/empty_borge.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

# Run at a higher level to get more stages
cfg['meta']['level'] = 100
cfg['stats']['hp'] = 300
cfg['stats']['power'] = 200
cfg['stats']['regen'] = 50

sim = Simulation(Borge(cfg))
result = sim.run()

print('=== SIMULATION RESULTS ===')
print(f'Final Stage: {result["final_stage"]}')
print(f'Kills: {result["kills"]}')
print()
print('Per-resource loot:')
print(f'  Mat1 (Obsidian): {result["loot_common"]:.2f}')
print(f'  Mat2 (Behlium): {result["loot_uncommon"]:.2f}')
print(f'  Mat3 (Hellish-Biomatter): {result["loot_rare"]:.2f}')
print(f'  XP: {result["total_xp"]:.2f}')
print(f'  Total Loot: {result["total_loot"]:.2f}')
print()

# Calculate ratios
total = result['loot_common'] + result['loot_uncommon'] + result['loot_rare']
print('Loot ratios (should be ~37.5% : 35.7% : 26.7%):')
print(f'  Mat1/Total: {result["loot_common"]/total*100:.1f}%')
print(f'  Mat2/Total: {result["loot_uncommon"]/total*100:.1f}%')
print(f'  Mat3/Total: {result["loot_rare"]/total*100:.1f}%')
print(f'  XP/Mat3: {result["total_xp"]/result["loot_rare"]:.2f}x (should be ~0.31)')
