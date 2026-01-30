#!/usr/bin/env python3
"""
PGO Workload Generator for Hunter Sim
Generates comprehensive profiling data for Profile-Guided Optimization
"""

import rust_sim
import json
import time
import random
import sys
import os

# Add current directory to path to import baseline_builds
sys.path.insert(0, os.path.dirname(__file__))

try:
    from baseline_builds import create_balanced_baseline_build, get_baseline_levels
    baseline_available = True
except ImportError:
    baseline_available = False
    print("Warning: baseline_builds module not found, using random builds")

def create_balanced_baseline_build(hunter_name, level):
    """Create a balanced baseline build for optimization starting points.
    
    All main stats set to level value, talent points = level, attribute points = level * 3.
    This provides consistent baselines that don't depend on player stat allocation choices.
    """
    # Main stats: all set to level value
    stats = {
        'power': level,
        'speed': level, 
        'max_hp': level
    }
    
    # Talent points = level
    talent_points = level
    
    # Attribute points = level * 3 (each attribute level costs 5 points)
    attr_points = level * 3
    
    talents = {}
    attrs = {}
    
    # Hunter-specific talent priorities for balanced builds
    if hunter_name == 'Borge':
        talent_priority = [
            'fires_of_war', 'omen_of_defeat', 'presence_of_god', 'impeccable_impacts',
            'life_of_the_hunt', 'death_is_my_companion', 'unfair_advantage', 'call_me_lucky_loot'
        ]
    elif hunter_name == 'Knox':
        talent_priority = [
            'omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt',
            'fires_of_war', 'impeccable_impacts', 'death_is_my_companion', 'call_me_lucky_loot'
        ]
    else:  # Ozzy
        talent_priority = [
            'omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt',
            'fires_of_war', 'impeccable_impacts', 'death_is_my_companion', 'call_me_lucky_loot'
        ]
    
    # Distribute talent points evenly across priority talents
    remaining_talents = talent_points
    for talent in talent_priority:
        if remaining_talents > 0:
            # Allocate points, preferring higher priority talents
            priority_idx = talent_priority.index(talent)
            max_points = max(1, min(remaining_talents, level // (priority_idx + 1)))
            points = min(max_points, remaining_talents)
            talents[talent] = points
            remaining_talents -= points
    
    # Hunter-specific attribute priorities for balanced builds
    if hunter_name == 'Borge':
        attr_priority = [
            'atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier',
            'lifedrain_inhalers', 'weakspot_analysis', 'soul_of_hermes', 'soul_of_the_minotaur'
        ]
    elif hunter_name == 'Knox':
        attr_priority = [
            'atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier',
            'lifedrain_inhalers', 'weakspot_analysis', 'soul_of_hermes', 'soul_of_the_minotaur'
        ]
    else:  # Ozzy
        attr_priority = [
            'atlas_protocol', 'born_for_battle', 'soul_of_hermes', 'soul_of_the_minotaur',
            'lifedrain_inhalers', 'weakspot_analysis', 'soul_of_ares', 'helltouch_barrier'
        ]
    
    # Distribute attribute points (each level costs 5 points)
    remaining_attr_points = attr_points
    for attr in attr_priority:
        if remaining_attr_points >= 5:
            # Allocate levels, preferring higher priority attributes
            priority_idx = attr_priority.index(attr)
            max_levels = max(1, min(remaining_attr_points // 5, level // (priority_idx + 2)))
            levels = min(max_levels, remaining_attr_points // 5)
            attrs[attr] = levels
            remaining_attr_points -= levels * 5
    
    return {
        'hunter': hunter_name,
        'level': level,
        'stats': stats,
        'talents': talents,
        'attributes': attrs,
        'inscryptions': {},
        'mods': {},
        'relics': {},
        'gems': {},
        'gadgets': {'wrench': 0, 'zaptron': 0, 'anchor': 0},
        'bonuses': {}
    }

def create_random_build(hunter_name, level, talent_points, attr_points):
    """Create a random build for profiling using realistic allocation rules"""
    talents = {}
    attrs = {}

    # Use proper talent allocation - distribute points more realistically
    talent_names = ['omen_of_defeat', 'presence_of_god', 'fires_of_war', 'unfair_advantage',
                   'life_of_the_hunt', 'death_is_my_companion', 'impeccable_impacts', 'call_me_lucky_loot']
    
    # Hunter-specific talent preferences
    if hunter_name == 'Borge':
        primary_talents = ['fires_of_war', 'omen_of_defeat', 'presence_of_god', 'impeccable_impacts']
    elif hunter_name == 'Knox':
        primary_talents = ['omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt']
    else:  # Ozzy
        primary_talents = ['omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt']
    
    remaining_talents = talent_points
    # Prioritize primary talents for the hunter
    for talent in primary_talents:
        if remaining_talents > 0 and talent in talent_names:
            # Allocate 1-3 points to primary talents
            points = min(random.randint(1, 3), remaining_talents)
            talents[talent] = points
            remaining_talents -= points
    
    # Distribute remaining points randomly
    while remaining_talents > 0 and len(talents) < len(talent_names):
        available_talents = [t for t in talent_names if t not in talents]
        if not available_talents:
            break
        talent = random.choice(available_talents)
        points = min(random.randint(1, 2), remaining_talents)
        talents[talent] = points
        remaining_talents -= points

    # Use proper attribute allocation - attributes cost 5 points each
    attr_names = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier',
                 'lifedrain_inhalers', 'weakspot_analysis', 'soul_of_hermes', 'soul_of_the_minotaur']
    
    # Hunter-specific attribute preferences
    if hunter_name == 'Borge':
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier']
    elif hunter_name == 'Knox':
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier']
    else:  # Ozzy
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_hermes', 'soul_of_the_minotaur']
    
    remaining_attr_points = attr_points  # This is in attribute points (5 points = 1 attribute level)
    
    # Prioritize primary attributes for the hunter
    for attr in primary_attrs:
        if remaining_attr_points >= 5 and attr in attr_names:
            # Allocate 1-3 levels to primary attributes
            levels = min(random.randint(1, 3), remaining_attr_points // 5)
            attrs[attr] = levels
            remaining_attr_points -= levels * 5
    
    # Distribute remaining attribute points randomly
    while remaining_attr_points >= 5 and len(attrs) < len(attr_names):
        available_attrs = [a for a in attr_names if a not in attrs]
        if not available_attrs:
            break
        attr = random.choice(available_attrs)
        levels = min(random.randint(1, 2), remaining_attr_points // 5)
        attrs[attr] = levels
        remaining_attr_points -= levels * 5

    return {
        'hunter': hunter_name,
        'level': level,
        'stats': {'power': level*5, 'speed': level//2, 'max_hp': level*50},
        'talents': talents,
        'attributes': attrs,
        'inscryptions': {},
        'mods': {},
        'relics': {},
        'gems': {},
        'gadgets': {},
        'bonuses': {}
    }
    """Create a random build for profiling using realistic allocation rules"""
    talents = {}
    attrs = {}

    # Use proper talent allocation - distribute points more realistically
    talent_names = ['omen_of_defeat', 'presence_of_god', 'fires_of_war', 'unfair_advantage',
                   'life_of_the_hunt', 'death_is_my_companion', 'impeccable_impacts', 'call_me_lucky_loot']
    
    # Hunter-specific talent preferences
    if hunter_name == 'Borge':
        primary_talents = ['fires_of_war', 'omen_of_defeat', 'presence_of_god', 'impeccable_impacts']
    elif hunter_name == 'Knox':
        primary_talents = ['omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt']
    else:  # Ozzy
        primary_talents = ['omen_of_defeat', 'presence_of_god', 'unfair_advantage', 'life_of_the_hunt']
    
    remaining_talents = talent_points
    # Prioritize primary talents for the hunter
    for talent in primary_talents:
        if remaining_talents > 0 and talent in talent_names:
            # Allocate 1-3 points to primary talents
            points = min(random.randint(1, 3), remaining_talents)
            talents[talent] = points
            remaining_talents -= points
    
    # Distribute remaining points randomly
    while remaining_talents > 0 and len(talents) < len(talent_names):
        available_talents = [t for t in talent_names if t not in talents]
        if not available_talents:
            break
        talent = random.choice(available_talents)
        points = min(random.randint(1, 2), remaining_talents)
        talents[talent] = points
        remaining_talents -= points

    # Use proper attribute allocation - attributes cost 5 points each
    attr_names = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier',
                 'lifedrain_inhalers', 'weakspot_analysis', 'soul_of_hermes', 'soul_of_the_minotaur']
    
    # Hunter-specific attribute preferences
    if hunter_name == 'Borge':
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier']
    elif hunter_name == 'Knox':
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_ares', 'helltouch_barrier']
    else:  # Ozzy
        primary_attrs = ['atlas_protocol', 'born_for_battle', 'soul_of_hermes', 'soul_of_the_minotaur']
    
    remaining_attr_points = attr_points  # This is in attribute points (5 points = 1 attribute level)
    
    # Prioritize primary attributes for the hunter
    for attr in primary_attrs:
        if remaining_attr_points >= 5 and attr in attr_names:
            # Allocate 1-3 levels to primary attributes
            levels = min(random.randint(1, 3), remaining_attr_points // 5)
            attrs[attr] = levels
            remaining_attr_points -= levels * 5
    
    # Distribute remaining attribute points randomly
    while remaining_attr_points >= 5 and len(attrs) < len(attr_names):
        available_attrs = [a for a in attr_names if a not in attrs]
        if not available_attrs:
            break
        attr = random.choice(available_attrs)
        levels = min(random.randint(1, 2), remaining_attr_points // 5)
        attrs[attr] = levels
        remaining_attr_points -= levels * 5

    return {
        'hunter': hunter_name,
        'level': level,
        'stats': {'power': level*5, 'speed': level//2, 'max_hp': level*50},
        'talents': talents,
        'attributes': attrs,
        'inscryptions': {},
        'mods': {},
        'relics': {},
        'gems': {},
        'gadgets': {},
        'bonuses': {}
    }

# Generate balanced baseline builds for every 10 levels (10-300)
# Each level has: stats = level, talent_points = level, attr_points = level * 3
baseline_levels = list(range(10, 301, 10))  # 10, 20, 30, ..., 300

hunters = ['Borge', 'Knox', 'Ozzy']

print("Running PGO profiling workloads with balanced baseline builds...")
start_time = time.time()

# Generate and run simulations for each baseline configuration
total_sims = 0
for level in baseline_levels:
    for hunter in hunters:
        print(f"Testing {hunter} balanced baseline at level {level}")
        print(f"  Stats: power={level}, speed={level}, max_hp={level}")
        print(f"  Talent points: {level}")
        print(f"  Attribute points: {level * 3}")

        if baseline_available:
            # Use the balanced baseline build
            config = create_balanced_baseline_build(hunter, level)
            configs = [json.dumps(config)]
        else:
            # Fallback to random build if baseline module not available
            config = create_random_build(hunter, level, level, level * 3)
            configs = [json.dumps(config)]

        # Run simulations - mix of different sim counts to exercise different code paths
        try:
            results = rust_sim.simulate_batch(configs, 100, True)  # Quick evaluation
            total_sims += 100

            # Run with higher sim counts for deeper profiling
            results = rust_sim.simulate_batch(configs, 500, True)
            total_sims += 500
            
            # Run with even higher sim counts for comprehensive profiling
            results = rust_sim.simulate_batch(configs, 1000, True)
            total_sims += 1000
            
        except Exception as e:
            print(f"Error simulating {hunter} level {level}: {e}")

elapsed = time.time() - start_time
print(f"PGO profiling completed in {elapsed:.1f} seconds")
print(f"Total simulations run: {total_sims}")
print(f"Total baseline builds tested: {len(baseline_levels) * len(hunters)}")