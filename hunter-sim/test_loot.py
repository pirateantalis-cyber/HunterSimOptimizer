"""Test per-resource loot tracking."""
from hunters import Borge
from sim import Simulation
import yaml

# Use empty borge build file
with open('../builds/empty_borge.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

# Set a small level to get a short sim
cfg['meta']['level'] = 10
cfg['stats']['hp'] = 50
cfg['stats']['power'] = 50
cfg['stats']['regen'] = 10

sim = Simulation(Borge(cfg))
result = sim.run()

print('=== PER-RESOURCE LOOT ===')
print(f'Final Stage: {result["final_stage"]}')
print(f'Total Loot: {result["total_loot"]:.2f}')
print(f'Obsidian (Common): {result["loot_common"]:.2f}')
print(f'Behlium (Uncommon): {result["loot_uncommon"]:.2f}')
print(f'Hellish-Biomatter (Rare): {result["loot_rare"]:.2f}')
print(f'Sum of parts: {result["loot_common"] + result["loot_uncommon"] + result["loot_rare"]:.2f}')
print(f'Check (should equal total): {abs(result["total_loot"] - (result["loot_common"] + result["loot_uncommon"] + result["loot_rare"])) < 0.01}')
