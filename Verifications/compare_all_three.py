#!/usr/bin/env python3
"""
3-WAY COMPARISON: IRL Data vs Python vs Rust
=============================================
Compares simulation results from:
1. IRL Data (actual game data from player submissions in GitHub)
2. Python implementation simulation
3. Rust implementation simulation

Shows how closely Python and Rust match the real game data.
"""

import sys
import os
import json
import subprocess
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

# Add hunter-sim to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hunter-sim'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from hunters import Borge, Ozzy, Knox
from sim import Simulation

# Add archive to path for IRL_DATA
sys.path.insert(0, str(Path(__file__).parent.parent / "archive"))
from IRL_DATA import ALL_IRL_DATA

# Configuration
NUM_SIMS = 50  # Per implementation
TOLERANCE_PCT = 5.0

# Paths
VALIDATOR_DIR = Path(__file__).parent.parent / "Validator"
RUST_EXE = Path(__file__).parent.parent / "hunter-sim-rs" / "target" / "release" / "hunter-sim.exe"
CACHE_FILE = VALIDATOR_DIR / "cached_issues.json"
IRL_BUILDS_DIR = Path(__file__).parent.parent / "hunter-sim" / "IRL Builds"
GLOBAL_BONUSES_FILE = IRL_BUILDS_DIR / "global_bonuses.json"

# Load global bonuses if available
GLOBAL_BONUSES = {}
if GLOBAL_BONUSES_FILE.exists():
    try:
        with open(GLOBAL_BONUSES_FILE, 'r') as f:
            GLOBAL_BONUSES = json.load(f)
        print(f"  [+] Loaded global bonuses from {GLOBAL_BONUSES_FILE.name}")
    except Exception as e:
        print(f"  [!] Failed to load global bonuses: {e}")


def merge_global_bonuses(config: Dict) -> Dict:
    """Merge global bonuses into a build config."""
    if not GLOBAL_BONUSES:
        return config
    
    merged = config.copy()
    
    # Merge bonuses (global bonuses as defaults, config overrides)
    merged_bonuses = {}
    for key in ['shard_milestone', 'diamond_loot', 'cm46', 'cm47', 'cm48', 'cm51', 
                'iap_travpack', 'ultima_multiplier', 'gaiden_card', 'iridian_card',
                'research81', 'scavenger', 'scavenger2', 'skill6_loot_bonus', 'wastarian_relic_loot_bonus']:
        if key in GLOBAL_BONUSES:
            merged_bonuses[key] = GLOBAL_BONUSES[key]
    # Override with config-specific bonuses
    merged_bonuses.update(config.get('bonuses', {}))
    merged['bonuses'] = merged_bonuses
    
    # Sanitize None values in bonuses
    for key in ['skill6_loot_bonus', 'wastarian_relic_loot_bonus']:
        if merged_bonuses.get(key) is None:
            merged_bonuses[key] = 0.0 if key == 'skill6_loot_bonus' else 0
    
    # Merge relics (add global relics if not present)
    merged_relics = config.get('relics', {}).copy()
    if 'relic7' in GLOBAL_BONUSES or 'r7' in GLOBAL_BONUSES:
        if 'r7' not in merged_relics and 'manifestation_core_titan' not in merged_relics:
            merged_relics['r7'] = GLOBAL_BONUSES.get('relic7', GLOBAL_BONUSES.get('r7', 0))
    if 'relic_r4' in GLOBAL_BONUSES:
        if 'r4' not in merged_relics:
            merged_relics['r4'] = GLOBAL_BONUSES['relic_r4']
    if 'relic_r19' in GLOBAL_BONUSES:
        if 'r19' not in merged_relics:
            merged_relics['r19'] = GLOBAL_BONUSES['relic_r19']
    merged['relics'] = merged_relics
    
    # Merge gems based on hunter type
    merged_gems = config.get('gems', {}).copy()
    hunter = config.get('hunter', '')
    if hunter == 'Borge' and 'gem_loot_borge' in GLOBAL_BONUSES:
        if 'attraction_loot_borge' not in merged_gems:
            merged_gems['attraction_loot_borge'] = GLOBAL_BONUSES['gem_loot_borge']
    if hunter == 'Ozzy' and 'gem_loot_ozzy' in GLOBAL_BONUSES:
        if 'attraction_loot_ozzy' not in merged_gems:
            merged_gems['attraction_loot_ozzy'] = GLOBAL_BONUSES['gem_loot_ozzy']
    if 'gem_attraction_node3' in GLOBAL_BONUSES:
        if 'attraction_node_#3' not in merged_gems:
            merged_gems['attraction_node_#3'] = GLOBAL_BONUSES['gem_attraction_node3']
    merged['gems'] = merged_gems
    
    return merged


def load_irl_builds() -> Dict[str, Dict]:
    """Load simulation configs from IRL Builds folder."""
    builds = {}
    
    # Map hunter names to their build files
    build_files = {
        'Knox': IRL_BUILDS_DIR / 'my_knox_build.json',
        'Ozzy': IRL_BUILDS_DIR / 'my_ozzy_build.json',
        'Borge': IRL_BUILDS_DIR / 'my_borge_build.json',
    }
    
    for hunter_name, build_path in build_files.items():
        if build_path.exists():
            try:
                with open(build_path, 'r') as f:
                    build = json.load(f)
                build = merge_global_bonuses(build)
                builds[hunter_name] = build
            except Exception as e:
                print(f"  [!] Error loading {hunter_name} build: {e}")
        else:
            print(f"  [!] Build file not found: {build_path}")
    
    if builds:
        print(f"  [OK] Loaded {len(builds)} builds from IRL Builds folder")
    
    return builds


def get_hunter_class(hunter_name: str):
    """Get hunter class by name."""
    if hunter_name == 'Knox':
        return Knox
    elif hunter_name == 'Ozzy':
        return Ozzy
    elif hunter_name == 'Borge':
        return Borge
    return None


IRL_BUILDS = load_irl_builds()
BUILDS = [(name, build, get_hunter_class(name)) for name, build in IRL_BUILDS.items()]


def run_python_sim(config: Dict, hunter_class, num_sims: int) -> dict:
    """Run Python simulation and return aggregated stats."""
    results = []
    for _ in range(num_sims):
        hunter = hunter_class(config)
        sim = Simulation(hunter)
        result = sim.run()
        results.append(result)
    
    # Aggregate results
    def avg(key): return statistics.mean([r.get(key, 0) for r in results])
    def total(key): return sum([r.get(key, 0) for r in results])
    
    return {
        'avg_stage': avg('final_stage'),
        'min_stage': min(r['final_stage'] for r in results),
        'max_stage': max(r['final_stage'] for r in results),
        'avg_kills': avg('kills'),
        'avg_damage': avg('damage'),
        'avg_damage_taken': avg('damage_taken'),
        'avg_attacks': avg('attacks'),
        'avg_elapsed_time': avg('elapsed_time'),
        'avg_effect_procs': avg('effect_procs'),
        'avg_evades': avg('evades'),
        'avg_regen': avg('regenerated_hp'),
        'avg_lifesteal': avg('lifesteal'),
        # XP and Loot
        'avg_xp': avg('total_xp'),
        'avg_loot': avg('total_loot'),
        'avg_loot_common': avg('loot_common'),
        'avg_loot_uncommon': avg('loot_uncommon'),
        'avg_loot_rare': avg('loot_rare'),
    }


def run_rust_sim(config: Dict, num_sims: int) -> dict:
    """Run Rust simulation and return aggregated stats."""
    # Write config to temp file
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f)
        temp_config = f.name
    
    try:
        result = subprocess.run(
            [str(RUST_EXE), "--config", temp_config, "--num-sims", str(num_sims), "--parallel", "--output", "json"],
            capture_output=True,
            text=True,
            cwd=str(RUST_EXE.parent)
        )
        
        if result.returncode != 0:
            print(f"Rust error: {result.stderr}")
            raise RuntimeError(f"Rust simulation failed: {result.stderr}")
        
        data = json.loads(result.stdout)
        stats = data['stats']
        
        return {
            'avg_stage': stats['avg_stage'],
            'min_stage': stats['min_stage'],
            'max_stage': stats['max_stage'],
            'avg_kills': stats['avg_kills'],
            'avg_damage': stats['avg_damage'],
            'avg_damage_taken': stats['avg_damage_taken'],
            'avg_attacks': stats['avg_attacks'],
            'avg_elapsed_time': stats['avg_time'],
            'avg_effect_procs': stats['avg_effect_procs'],
            'avg_evades': stats['avg_evades'],
            'avg_regen': stats['avg_regen'],
            'avg_lifesteal': stats['avg_lifesteal'],
            # XP and Loot
            'avg_xp': stats.get('avg_xp', 0),
            'avg_loot': stats.get('avg_loot', 0),
            'avg_loot_common': stats.get('avg_loot_common', 0),
            'avg_loot_uncommon': stats.get('avg_loot_uncommon', 0),
            'avg_loot_rare': stats.get('avg_loot_rare', 0),
        }
    finally:
        os.unlink(temp_config)


def format_number(n, decimals=1):
    """Format number with thousands separator."""
    if isinstance(n, float):
        return f"{n:,.{decimals}f}"
    return f"{n:,}"


def print_comparison(hunter_name: str, irl_stage: int, python: dict, rust: dict):
    """Print detailed comparison table."""
    print(f"\n{'='*80}")
    print(f"  {hunter_name.upper()} COMPARISON")
    print(f"  IRL Benchmark: Stage {irl_stage}")
    print(f"{'='*80}")
    
    # Define metrics to compare
    metrics = [
        ('CORE METRICS', [
            ('avg_stage', 'Avg Stage'),
            ('min_stage', 'Min Stage'),
            ('max_stage', 'Max Stage'),
            ('avg_kills', 'Avg Kills'),
        ]),
        ('COMBAT STATS', [
            ('avg_damage', 'Avg Damage'),
            ('avg_damage_taken', 'Damage Taken'),
            ('avg_attacks', 'Attacks'),
            ('avg_elapsed_time', 'Elapsed Time'),
            ('avg_effect_procs', 'Effect Procs'),
            ('avg_evades', 'Evades'),
        ]),
        ('HEALING', [
            ('avg_regen', 'Regen'),
            ('avg_lifesteal', 'Lifesteal'),
        ]),
        ('REWARDS', [
            ('avg_xp', 'Total XP'),
            ('avg_loot', 'Total Loot'),
            ('avg_loot_common', 'Loot (Common)'),
            ('avg_loot_uncommon', 'Loot (Uncommon)'),
            ('avg_loot_rare', 'Loot (Rare)'),
        ]),
    ]
    
    for section_name, section_metrics in metrics:
        print(f"\n  {section_name}:")
        print(f"  {'-'*76}")
        print(f"  {'Metric':<20} {'Python':>12} {'Rust':>12} {'Py vs Rust':>15}")
        print(f"  {'-'*76}")
        
        for key, label in section_metrics:
            p_val = python.get(key, 0)
            r_val = rust.get(key, 0)
            
            # Calculate difference
            if isinstance(p_val, (int, float)) and isinstance(r_val, (int, float)) and p_val != 0:
                py_rs_diff = abs(p_val - r_val) / p_val * 100
                py_rs_str = f"{py_rs_diff:+.1f}%" if py_rs_diff < 100 else f"{py_rs_diff:.0f}%"
            else:
                py_rs_str = '-'
            
            p_str = format_number(p_val)
            r_str = format_number(r_val)
            
            print(f"  {label:<20} {p_str:>12} {r_str:>12} {py_rs_str:>15}")


def main():
    print("="*80)
    print("  3-WAY COMPARISON: IRL Data vs Python vs Rust")
    print("="*80)
    print(f"\n  Running {NUM_SIMS} simulations per hunter per backend...\n")
    
    irl_builds = load_irl_builds()
    
    if not irl_builds:
        print("  [ERROR] No IRL builds loaded. Make sure IRL Builds folder exists with build files.")
        return
    
    all_results = {}
    
    for hunter_name in ['Knox', 'Ozzy', 'Borge']:
        if hunter_name not in irl_builds:
            print(f"  [SKIP] {hunter_name} not in IRL builds")
            continue
        
        config = irl_builds[hunter_name]
        hunter_class = get_hunter_class(hunter_name)
        
        print(f"  Testing {hunter_name}...")
        
        # Get IRL data from hardcoded constants
        irl_data = ALL_IRL_DATA.get(hunter_name, {})
        
        # Run Python implementation
        print(f"    Running Python...")
        python_results = run_python_sim(config, hunter_class, NUM_SIMS)
        
        # Run Rust implementation
        print(f"    Running Rust...")
        rust_results = run_rust_sim(config, NUM_SIMS)
        
        all_results[hunter_name] = {
            'irl': irl_data,
            'python': python_results,
            'rust': rust_results,
        }
    
    # Print comprehensive comparison
    print_comprehensive_summary(all_results)


def print_comprehensive_summary(all_results: dict):
    """Print detailed comparison of IRL vs Python vs Rust."""
    
    def fmt(val, decimals=1):
        if val == 0:
            return "-"
        if isinstance(val, float):
            return f"{val:,.{decimals}f}"
        return f"{val:,}"
    
    def fmt_large(val):
        """Format large numbers with K/M/B/T suffix."""
        if val == 0:
            return "-"
        if val >= 1e12:
            return f"{val/1e12:.1f}T"
        if val >= 1e9:
            return f"{val/1e9:.1f}B"
        if val >= 1e6:
            return f"{val/1e6:.1f}M"
        if val >= 1e3:
            return f"{val/1e3:.1f}K"
        return f"{val:.0f}"
    
    def pct_diff(a, b):
        """Calculate percentage difference (signed: how much b differs from a)."""
        if a == 0 and b == 0:
            return 0.0
        if a == 0:
            return 100.0
        return (b - a) / a * 100
    
    print(f"\n{'='*100}")
    print("  COMPREHENSIVE COMPARISON: IRL Data vs Python vs Rust Simulations")
    print(f"{'='*100}")
    
    hunters = list(all_results.keys())
    
    # Header
    print(f"\n  {'METRIC':<20}", end="")
    for h in hunters:
        print(f" | {h:^25}", end="")
    print()
    print(f"  {'':<20}", end="")
    for h in hunters:
        print(f" | {'IRL':>7} {'Py':>7} {'Rs':>7}", end="")
    print()
    print(f"  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*25}", end="")
    print()
    
    # Stage metrics
    metrics = [
        ('avg_stage', 'Avg Stage'),
        ('avg_damage', 'Avg Damage'),
        ('avg_damage_taken', 'Dmg Taken'),
    ]
    
    for key, label in metrics:
        print(f"  {label:<20}", end="")
        for h in hunters:
            irl = all_results[h]['irl'].get(key, 0)
            py = all_results[h]['python'].get(key, 0)
            rs = all_results[h]['rust'].get(key, 0)
            print(f" | {fmt(irl, 0):>7} {fmt(py, 0):>7} {fmt(rs, 0):>7}", end="")
        print()
    
    print(f"  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*25}", end="")
    print()
    
    # Rewards (large numbers)
    reward_metrics = [
        ('avg_xp', 'Total XP'),
        ('avg_loot', 'Total Loot'),
        ('avg_loot_common', 'Loot (Common)'),
        ('avg_loot_uncommon', 'Loot (Uncommon)'),
        ('avg_loot_rare', 'Loot (Rare)'),
    ]
    
    for key, label in reward_metrics:
        print(f"  {label:<20}", end="")
        for h in hunters:
            irl = all_results[h]['irl'].get(key, 0)
            py = all_results[h]['python'].get(key, 0)
            rs = all_results[h]['rust'].get(key, 0)
            print(f" | {fmt_large(irl):>7} {fmt_large(py):>7} {fmt_large(rs):>7}", end="")
        print()
    
    # Accuracy summary
    print(f"\n{'='*130}")
    print("  ACCURACY SUMMARY (vs IRL Data)")
    print(f"{'='*130}")
    print(f"\n  {'Hunter':<12} {'Metric':<18} {'IRL':>14} {'Python':>14} {'Rust':>14} {'Py %':>10} {'Rs %':>10}")
    print(f"  {'-'*130}")
    
    for h in hunters:
        irl_stage = all_results[h]['irl'].get('irl_max_stage', 0)
        py_stage = all_results[h]['python'].get('avg_stage', 0)
        rs_stage = all_results[h]['rust'].get('avg_stage', 0)
        
        py_pct = pct_diff(irl_stage, py_stage)
        rs_pct = pct_diff(irl_stage, rs_stage)
        
        print(f"  {h:<12} {'Stage':<18} {irl_stage:>14.1f} {py_stage:>14.1f} {rs_stage:>14.1f} {py_pct:>9.1f}% {rs_pct:>9.1f}%")
        
        # XP accuracy
        irl_xp = all_results[h]['irl'].get('irl_avg_xp', 0)
        py_xp = all_results[h]['python'].get('avg_xp', 0)
        rs_xp = all_results[h]['rust'].get('avg_xp', 0)
        if irl_xp > 0:
            py_xp_pct = pct_diff(irl_xp, py_xp)
            rs_xp_pct = pct_diff(irl_xp, rs_xp)
            print(f"  {'':<12} {'XP':<18} {fmt_large(irl_xp):>14} {fmt_large(py_xp):>14} {fmt_large(rs_xp):>14} {py_xp_pct:>9.1f}% {rs_xp_pct:>9.1f}%")
        
        # Common Loot accuracy
        irl_loot = all_results[h]['irl'].get('irl_avg_common', 0)
        py_loot = all_results[h]['python'].get('avg_loot_common', 0)
        rs_loot = all_results[h]['rust'].get('avg_loot_common', 0)
        if irl_loot > 0:
            py_loot_pct = pct_diff(irl_loot, py_loot)
            rs_loot_pct = pct_diff(irl_loot, rs_loot)
            print(f"  {'':<12} {'Loot (Common)':<18} {fmt_large(irl_loot):>14} {fmt_large(py_loot):>14} {fmt_large(rs_loot):>14} {py_loot_pct:>9.1f}% {rs_loot_pct:>9.1f}%")
        
        # Uncommon Loot accuracy
        irl_loot = all_results[h]['irl'].get('irl_avg_uncommon', 0)
        py_loot = all_results[h]['python'].get('avg_loot_uncommon', 0)
        rs_loot = all_results[h]['rust'].get('avg_loot_uncommon', 0)
        if irl_loot > 0:
            py_loot_pct = pct_diff(irl_loot, py_loot)
            rs_loot_pct = pct_diff(irl_loot, rs_loot)
            print(f"  {'':<12} {'Loot (Uncommon)':<18} {fmt_large(irl_loot):>14} {fmt_large(py_loot):>14} {fmt_large(rs_loot):>14} {py_loot_pct:>9.1f}% {rs_loot_pct:>9.1f}%")
        
        # Rare Loot accuracy
        irl_loot = all_results[h]['irl'].get('irl_avg_rare', 0)
        py_loot = all_results[h]['python'].get('avg_loot_rare', 0)
        rs_loot = all_results[h]['rust'].get('avg_loot_rare', 0)
        if irl_loot > 0:
            py_loot_pct = pct_diff(irl_loot, py_loot)
            rs_loot_pct = pct_diff(irl_loot, rs_loot)
            print(f"  {'':<12} {'Loot (Rare)':<18} {fmt_large(irl_loot):>14} {fmt_large(py_loot):>14} {fmt_large(rs_loot):>14} {py_loot_pct:>9.1f}% {rs_loot_pct:>9.1f}%")
        
        print(f"  {'-'*130}")
    
    print()


if __name__ == "__main__":
    main()
