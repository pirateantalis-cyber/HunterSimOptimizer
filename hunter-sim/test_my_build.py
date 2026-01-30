#!/usr/bin/env python3
"""Test actual in-game Borge build"""

from sim import Simulation
from hunters import Borge
import json

# Load your actual build
with open('IRL Builds/my_borge_build.json', 'r') as f:
    config = json.load(f)

hunter = Borge(config)

print('=== TESTING YOUR BORGE BUILD (IRL Stage 300) ===')
print('Level:', config.get('level', 'N/A'))
print('HP:', round(hunter.max_hp, 2))
print('Power:', round(hunter.power, 2))
print('Speed:', round(hunter.speed, 4))
print('DR:', round(hunter.damage_reduction, 4))
print('Crit:', round(hunter.special_chance, 4))
print('Crit Dmg:', round(hunter.special_damage, 4))
print()

# Run simulation
sim = Simulation(hunter)
sim.run()
final_stage = sim.current_stage

print('=== PYTHON SIMULATION RESULTS ===')
print('Final Stage:', final_stage)
print('IRL Stage: 300')
print('Difference:', final_stage - 300, 'stages')
error = ((final_stage - 300) / 300 * 100)
print('Error:', round(error, 1), '%')
