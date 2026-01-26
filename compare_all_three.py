#!/usr/bin/env python3
"""
COMPREHENSIVE 3-WAY COMPARISON: WASM vs Python vs Rust
======================================================
Compares simulation results from:
1. WASM (official game code - source of truth)
2. Python implementation
3. Rust implementation

Includes: Stages, Kills, XP, Loot, Damage, Time
"""

import sys
import os
import json
import subprocess
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

# Add hunter-sim to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'hunter-sim'))

from hunters import Borge, Ozzy, Knox
from sim import Simulation

# Configuration
NUM_SIMS = 10  # Per implementation
TOLERANCE_PCT = 5.0  # WASM vs others (more lenient due to different RNG)

# Paths
BUILDS_DIR = Path(__file__).parent / "hunter-sim" / "IRL Builds"
RUST_EXE = Path(__file__).parent / "hunter-sim-rs" / "target" / "release" / "hunter-sim.exe"
WASM_SCRIPT = Path(__file__).parent / "archive" / "wasm-analysis" / "run_wasm_sim.js"

BUILDS = [
    ("Borge", BUILDS_DIR / "my_borge_build.json", Borge),
    ("Ozzy", BUILDS_DIR / "my_ozzy_build.json", Ozzy),
    ("Knox", BUILDS_DIR / "my_knox_build.json", Knox),
]


def run_python_sim(config_path: Path, hunter_class, num_sims: int) -> dict:
    """Run Python simulation and return aggregated stats."""
    with open(config_path, 'r') as f:
        config = json.load(f)
    
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


def run_rust_sim(config_path: Path, num_sims: int) -> dict:
    """Run Rust simulation and return aggregated stats."""
    result = subprocess.run(
        [str(RUST_EXE), "--config", str(config_path), "--num-sims", str(num_sims), "--parallel", "--output", "json"],
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


def run_wasm_sim(hunter_name: str, config_path: Path, num_sims: int) -> dict:
    """Run WASM simulation via Node.js and return stats."""
    wasm_dir = Path(__file__).parent / "archive" / "wasm-analysis"
    wasm_script = wasm_dir / "run_wasm_sim.js"
    
    try:
        result = subprocess.run(
            ["node", str(wasm_script), str(config_path), hunter_name.lower(), str(num_sims)],
            capture_output=True,
            text=True,
            cwd=str(wasm_dir)
        )
        
        if result.returncode != 0:
            print(f"WASM error: {result.stderr}")
            return {'error': result.stderr, 'avg_stage': 0, 'min_stage': 0, 'max_stage': 0}
        
        data = json.loads(result.stdout.strip())
        if 'error' in data:
            return {'error': data['error'], 'avg_stage': 0, 'min_stage': 0, 'max_stage': 0}
        
        return data
    except FileNotFoundError:
        return {'error': 'Node.js not found - install Node.js to run WASM', 'avg_stage': 0, 'min_stage': 0, 'max_stage': 0}
    except Exception as e:
        return {'error': str(e), 'avg_stage': 0, 'min_stage': 0, 'max_stage': 0}


def format_number(n, decimals=1):
    """Format number with thousands separator."""
    if isinstance(n, float):
        return f"{n:,.{decimals}f}"
    return f"{n:,}"


def print_comparison(hunter_name: str, irl_stage: int, wasm: dict, python: dict, rust: dict):
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
    
    wasm_err = wasm.get('error')
    
    for section_name, section_metrics in metrics:
        print(f"\n  {section_name}:")
        print(f"  {'-'*76}")
        print(f"  {'Metric':<20} {'WASM':>12} {'Python':>12} {'Rust':>12} {'Py vs Rs':>10} {'vs WASM':>10}")
        print(f"  {'-'*76}")
        
        for key, label in section_metrics:
            w_val = wasm.get(key, 0) if not wasm_err else '-'
            p_val = python.get(key, 0)
            r_val = rust.get(key, 0)
            
            # Calculate differences
            if isinstance(p_val, (int, float)) and isinstance(r_val, (int, float)) and p_val != 0:
                py_rs_diff = abs(p_val - r_val) / p_val * 100
                py_rs_str = f"{py_rs_diff:+.1f}%" if py_rs_diff < 100 else f"{py_rs_diff:.0f}%"
            else:
                py_rs_str = '-'
            
            if isinstance(w_val, (int, float)) and isinstance(p_val, (int, float)) and w_val != 0:
                vs_wasm = abs(p_val - w_val) / w_val * 100
                vs_wasm_str = f"{vs_wasm:+.1f}%" if vs_wasm < 100 else f"{vs_wasm:.0f}%"
            else:
                vs_wasm_str = '-'
            
            w_str = format_number(w_val) if isinstance(w_val, (int, float)) else str(w_val)
            p_str = format_number(p_val)
            r_str = format_number(r_val)
            
            print(f"  {label:<20} {w_str:>12} {p_str:>12} {r_str:>12} {py_rs_str:>10} {vs_wasm_str:>10}")


def main():
    print("="*80)
    print("  COMPREHENSIVE 3-WAY SIMULATION COMPARISON")
    print("  WASM (Game Code) vs Python vs Rust")
    print("="*80)
    print(f"\n  Running {NUM_SIMS} simulations per hunter per implementation...")
    print()
    
    all_results = {}
    
    for hunter_name, config_path, hunter_class in BUILDS:
        print(f"  Testing {hunter_name}...")
        
        # Load IRL benchmark
        with open(config_path) as f:
            config = json.load(f)
        irl_stage = config.get('irl_max_stage', 0)
        
        # Run all three implementations
        print(f"    Running WASM...")
        wasm_results = run_wasm_sim(hunter_name, config_path, NUM_SIMS)
        
        print(f"    Running Python...")
        python_results = run_python_sim(config_path, hunter_class, NUM_SIMS)
        
        print(f"    Running Rust...")
        rust_results = run_rust_sim(config_path, NUM_SIMS)
        
        all_results[hunter_name] = {
            'irl': irl_stage,
            'wasm': wasm_results,
            'python': python_results,
            'rust': rust_results,
        }
    
    # Print comprehensive summary
    print_comprehensive_summary(all_results)


def print_comprehensive_summary(all_results: dict):
    """Print a comprehensive side-by-side comparison of all hunters."""
    
    def pct_diff(a, b):
        """Calculate percentage difference."""
        if a == 0 and b == 0:
            return 0.0
        if a == 0:
            return 100.0
        return abs(a - b) / a * 100
    
    def fmt(val, decimals=1):
        """Format number with commas."""
        if val == 0:
            return "-"
        if isinstance(val, float):
            return f"{val:,.{decimals}f}"
        return f"{val:,}"
    
    def diff_str(py, rs):
        """Format difference percentage."""
        if py == 0:
            return "-"
        diff = pct_diff(py, rs)
        return f"{diff:.1f}%"
    
    print(f"\n{'='*120}")
    print("  COMPREHENSIVE 3-WAY COMPARISON: All Hunters")
    print(f"{'='*120}")
    
    hunters = list(all_results.keys())
    
    # Header
    print(f"\n  {'METRIC':<20}", end="")
    for h in hunters:
        print(f" | {h:^30}", end="")
    print()
    print(f"  {'':<20}", end="")
    for h in hunters:
        print(f" | {'WASM':>8} {'Python':>10} {'Rust':>10}", end="")
    print()
    print(f"  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*30}", end="")
    print()
    
    # IRL Benchmarks
    print(f"  {'IRL Benchmark':<20}", end="")
    for h in hunters:
        irl = all_results[h]['irl']
        print(f" | {irl:>8} {irl:>10} {irl:>10}", end="")
    print()
    
    # Stage metrics
    metrics = [
        ('avg_stage', 'Avg Stage'),
        ('min_stage', 'Min Stage'),
        ('max_stage', 'Max Stage'),
    ]
    
    for key, label in metrics:
        print(f"  {label:<20}", end="")
        for h in hunters:
            wasm = all_results[h]['wasm'].get(key, 0)
            py = all_results[h]['python'].get(key, 0)
            rs = all_results[h]['rust'].get(key, 0)
            print(f" | {fmt(wasm, 0):>8} {fmt(py, 0):>10} {fmt(rs, 0):>10}", end="")
        print()
    
    print(f"  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*30}", end="")
    print()
    
    # Combat metrics (Python vs Rust only - WASM doesn't expose these)
    combat_metrics = [
        ('avg_kills', 'Avg Kills'),
        ('avg_damage', 'Avg Damage'),
        ('avg_damage_taken', 'Damage Taken'),
        ('avg_attacks', 'Attacks'),
    ]
    
    for key, label in combat_metrics:
        print(f"  {label:<20}", end="")
        for h in hunters:
            py = all_results[h]['python'].get(key, 0)
            rs = all_results[h]['rust'].get(key, 0)
            diff = diff_str(py, rs)
            print(f" | {'-':>8} {fmt(py, 0):>10} {fmt(rs, 0):>10}", end="")
        print()
    
    print(f"  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*30}", end="")
    print()
    
    # Rewards - now with WASM values (may be very large due to game multipliers)
    reward_metrics = [
        ('avg_xp', 'Total XP'),
        ('avg_loot', 'Total Loot'),
        ('avg_loot_common', 'Loot (Common)'),
        ('avg_loot_uncommon', 'Loot (Uncommon)'),
        ('avg_loot_rare', 'Loot (Rare)'),
    ]
    
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
    
    for key, label in reward_metrics:
        print(f"  {label:<20}", end="")
        for h in hunters:
            wasm = all_results[h]['wasm'].get(key, 0)
            py = all_results[h]['python'].get(key, 0)
            rs = all_results[h]['rust'].get(key, 0)
            print(f" | {fmt_large(wasm):>8} {fmt_large(py):>10} {fmt_large(rs):>10}", end="")
        print()
    
    # XP/Loot accuracy section (Python vs Rust only - WASM uses different formulas)
    print(f"\n  {'-'*20}", end="")
    for h in hunters:
        print(f"-+-{'-'*30}", end="")
    print()
    print(f"  {'Py-Rs XP Diff %':<20}", end="")
    for h in hunters:
        py = all_results[h]['python'].get('avg_xp', 0)
        rs = all_results[h]['rust'].get('avg_xp', 0)
        diff = diff_str(py, rs)
        print(f" | {'':>8} {diff:>10} {'':>10}", end="")
    print()
    print(f"  {'Py-Rs Loot Diff %':<20}", end="")
    for h in hunters:
        py = all_results[h]['python'].get('avg_loot', 0)
        rs = all_results[h]['rust'].get('avg_loot', 0)
        diff = diff_str(py, rs)
        print(f" | {'':>8} {diff:>10} {'':>10}", end="")
    print()
    
    # Summary box
    print(f"\n{'='*120}")
    print("  ACCURACY SUMMARY (Python vs Rust)")
    print(f"{'='*120}")
    print(f"\n  {'Hunter':<12} {'IRL':>6} {'WASM':>8} {'Python':>8} {'Rust':>8} {'Py-Rs %':>10} {'Py-WASM %':>10} {'Rs-WASM %':>10} {'Status':>10}")
    print(f"  {'-'*90}")
    
    all_good = True
    for h in hunters:
        irl = all_results[h]['irl']
        wasm = all_results[h]['wasm'].get('avg_stage', 0)
        py = all_results[h]['python']['avg_stage']
        rs = all_results[h]['rust']['avg_stage']
        
        py_rs = pct_diff(py, rs)
        py_wasm = pct_diff(py, wasm) if wasm > 0 else 0
        rs_wasm = pct_diff(rs, wasm) if wasm > 0 else 0
        
        # Status based on Py-Rs difference
        if py_rs < 1:
            status = "EXCELLENT"
        elif py_rs < 5:
            status = "GOOD"
        else:
            status = "CHECK"
            all_good = False
        
        wasm_str = f"{wasm:.0f}" if wasm > 0 else "N/A"
        print(f"  {h:<12} {irl:>6} {wasm_str:>8} {py:>8.1f} {rs:>8.1f} {py_rs:>9.2f}% {py_wasm:>9.1f}% {rs_wasm:>9.1f}% {status:>10}")
    
    print(f"\n  {'='*90}")
    if all_good:
        print("  [OK] All hunters within 5% Python vs Rust - Ready for production!")
    else:
        print("  [!!] Some hunters exceed 5% difference - Review needed")
    print(f"  {'='*90}\n")


if __name__ == "__main__":
    main()
