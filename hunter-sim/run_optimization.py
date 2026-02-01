"""
Standalone optimization runner - runs WITHOUT GUI for maximum speed.
Communicates results back via JSON file.
"""
import sys
import json
import time
import copy
import os
import argparse
from pathlib import Path
import heapq
import statistics

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Safe logging helper for frozen PyInstaller apps (sys.stderr is None in GUI mode)
def _log(msg):
    """Safely write to stderr if available, otherwise ignore."""
    if sys.stderr is not None:
        try:
            sys.stderr.write(msg)
            sys.stderr.flush()
        except:
            pass

def extend_elite_pattern(elite_talents, elite_attrs, generator, target_talents, target_attrs):
    """
    Extend an elite pattern from a previous tier to use more points.
    
    Takes a build that used fewer points (from an earlier tier) and adds more
    talent/attribute points to reach the new tier's targets while preserving
    the core pattern that made the original build successful.
    """
    import random
    
    hunter_class = generator.hunter_class
    talents_list = list(generator.costs["talents"].keys())
    attrs_list = list(generator.costs["attributes"].keys())
    
    # Copy elite pattern as starting point
    talents = {t: elite_talents.get(t, 0) for t in talents_list}
    attrs = {a: elite_attrs.get(a, 0) for a in attrs_list}
    
    # Calculate how much elite already spent
    elite_talent_spent = sum(talents.values())
    attr_costs = {a: generator.costs["attributes"][a]["cost"] for a in attrs_list}
    elite_attr_spent = sum(attrs[a] * attr_costs[a] for a in attrs_list)
    
    # Calculate how much MORE we need to add
    talent_to_add = max(0, target_talents - elite_talent_spent)
    attr_to_add = max(0, target_attrs - elite_attr_spent)
    
    attr_max = {a: generator.costs["attributes"][a]["max"] for a in attrs_list}
    talent_max = {t: generator.costs["talents"][t]["max"] for t in talents_list}
    
    # Find unlimited (infinite) attributes for fallback
    unlimited_attrs = [a for a in attrs_list if attr_max[a] == float('inf')]
    unlimited_attrs.sort(key=lambda a: attr_costs[a])
    
    # Find unlimited talents for fallback (but NOT unknown_talent)
    unlimited_talents = [t for t in talents_list if talent_max[t] == float('inf') and t != 'unknown_talent']
    
    # === ADD TALENT POINTS ===
    attempts = 0
    while talent_to_add > 0 and attempts < 1000:
        attempts += 1
        # Find KNOWN talents that can accept more points
        valid = [t for t in talents_list 
                 if t != 'unknown_talent' and (talent_max[t] == float('inf') or talents[t] < int(talent_max[t]))]
        
        # Only use unknown_talent as LAST RESORT
        if not valid:
            if 'unknown_talent' in talents_list:
                valid = ['unknown_talent']
            else:
                break
        
        chosen = random.choice(valid)
        talents[chosen] += 1
        talent_to_add -= 1
    
    # === ADD ATTRIBUTE POINTS ===
    deps = getattr(hunter_class, 'attribute_dependencies', {})
    exclusions = getattr(hunter_class, 'attribute_exclusions', [])
    
    attempts = 0
    remaining = attr_to_add
    
    while remaining > 0 and attempts < 5000:
        attempts += 1
        
        # Find valid attributes to add to
        valid_attrs = []
        for attr in attrs_list:
            cost = attr_costs[attr]
            if cost > remaining:
                continue
            # Check max - unlimited attrs (inf) always pass this check
            if attr_max[attr] != float('inf'):
                if attrs[attr] >= int(attr_max[attr]):
                    continue
            # Check dependencies
            if attr in deps:
                if not all(attrs.get(req, 0) >= lvl for req, lvl in deps[attr].items()):
                    continue
            # Check unlock requirements
            if not generator._can_unlock_attribute(attr, attrs, attr_costs):
                continue
            # Check exclusions
            excluded = False
            for excl_pair in exclusions:
                if attr in excl_pair:
                    other = excl_pair[0] if excl_pair[1] == attr else excl_pair[1]
                    if attrs.get(other, 0) > 0:
                        excluded = True
                        break
            if excluded:
                continue
            valid_attrs.append(attr)
        
        if valid_attrs:
            chosen = random.choice(valid_attrs)
            attrs[chosen] += 1
            remaining -= attr_costs[chosen]
        elif unlimited_attrs:
            # FALLBACK: Force use unlimited attributes
            spent_any = False
            for sink_attr in unlimited_attrs:
                if attr_costs[sink_attr] <= remaining:
                    attrs[sink_attr] += 1
                    remaining -= attr_costs[sink_attr]
                    spent_any = True
                    break
            if not spent_any:
                break
        else:
            break
    
    # FINAL GUARANTEE: Dump remaining into unlimited attrs
    if remaining > 0 and unlimited_attrs:
        for sink_attr in unlimited_attrs:
            cost = attr_costs[sink_attr]
            while remaining >= cost:
                attrs[sink_attr] += 1
                remaining -= cost
    
    return talents, attrs

from hunters import Borge, Knox, Ozzy
from sim import Simulation
from gui_multi import BuildGenerator
import rust_sim
from typing import Dict

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
        'avg_loot_per_hour': avg('total_loot') / avg('elapsed_time') * 3600 if avg('elapsed_time') > 0 else 0,
        'survival_rate': len([r for r in results if r.get('final_stage', 0) > 0]) / len(results),
    }

def python_simulate_batch(config_jsons, num_sims):
    """Simulate a batch using Python."""
    results = []
    hunter_classes = {'Borge': Borge, 'Ozzy': Ozzy, 'Knox': Knox}
    for config_json in config_jsons:
        config = json.loads(config_json)
        hunter_class = hunter_classes[config['hunter']]
        result = run_python_sim(config, hunter_class, num_sims)
        results.append(result)
    return results

def evaluate_builds_successive_halving(build_configs, base_sims=64, rounds=3, survival_rate=0.5, progress_file=None, tier_name="", total_sims=0, start_time=None, use_rust=True):
    """
    Evaluate builds using successive halving algorithm with optimizations.
    
    Args:
        build_configs: List of JSON config strings
        base_sims: Starting simulations per build
        rounds: Number of successive halving rounds
        survival_rate: Fraction of builds to keep each round
        use_rust: Whether to use Rust backend (True) or Python backend (False)
    
    Returns:
        List of (config, score) tuples for surviving builds
    """
    _log(f"[DEBUG] evaluate_builds_successive_halving called with {len(build_configs)} builds, progress_file={progress_file}\n")
    if progress_file:
        _log(f"[DEBUG] progress_file exists: {os.path.exists(progress_file)}\n")
    else:
        _log(f"[DEBUG] progress_file is None\n")
    
    if not build_configs:
        _log(f"[DEBUG] No build_configs, returning empty\n")
        return []
    
    # Build similarity cache to avoid re-simulating very similar configurations
    similarity_cache = {}
    
    surviving_configs = build_configs[:]
    current_sims = base_sims
    
    for round_num in range(rounds):
        if len(surviving_configs) <= 1:
            _log(f"[DEBUG] Breaking due to <= 1 surviving configs\n")
            break
            
        _log(f"[SH Round {round_num+1}] Evaluating {len(surviving_configs)} builds with {current_sims} sims each\n")
        
        # Check similarity cache for very similar builds
        filtered_configs = []
        cached_results = []
        cache_hits = 0
        for config_json in surviving_configs:
            config = json.loads(config_json)
            # Create similarity key based on major talent/attribute allocations
            talents = config.get('talents', {})
            attrs = config.get('attributes', {})
            
            # Focus on top 3 talents and attributes for similarity
            top_talents = sorted(talents.items(), key=lambda x: x[1], reverse=True)[:3]
            top_attrs = sorted(attrs.items(), key=lambda x: x[1], reverse=True)[:3]
            similarity_key = (tuple(top_talents), tuple(top_attrs))
            
            if similarity_key in similarity_cache:
                # Use cached result with small random variation
                cached_result = similarity_cache[similarity_key].copy()
                # Add small random noise to prevent exact duplicates
                import random
                noise = random.uniform(-0.05, 0.05)
                cached_result['avg_stage'] *= (1 + noise)
                cached_result['max_stage'] = max(cached_result['max_stage'], cached_result['avg_stage'])
                cached_results.append((config_json, cached_result))
                cache_hits += 1
            else:
                filtered_configs.append(config_json)
        
        surviving_configs = filtered_configs
        if cache_hits > 0:
            _log(f"[CACHE] {cache_hits} builds served from similarity cache\n")
        
        # Evaluate remaining builds
        if surviving_configs:
            # Evaluate builds that weren't in cache
            backend_name = "Rust" if use_rust else "Python"
            
            if use_rust:
                # Rust backend: use larger batches for better parallelization
                # But limit to prevent memory issues
                import multiprocessing
                cpu_count = multiprocessing.cpu_count()
                optimal_batch_size = min(len(surviving_configs), max(100, cpu_count * 50))  # 50 builds per CPU core
                _log(f"[RUST] Using optimized batch size: {optimal_batch_size} for {cpu_count} CPU cores\n")
                
                # Process in optimal chunks
                all_batch_results = []
                for i in range(0, len(surviving_configs), optimal_batch_size):
                    chunk_configs = surviving_configs[i:i + optimal_batch_size]
                    parsed_configs = [json.loads(cfg) for cfg in chunk_configs]
                    chunk_results = rust_sim.simulate_batch(parsed_configs, current_sims, True)
                    all_batch_results.extend(chunk_results)
                batch_results = all_batch_results
            else:
                # Python backend: process all at once (slower but simpler)
                batch_results = python_simulate_batch(surviving_configs, current_sims)
            
            _log(f"[DEBUG] {backend_name} simulate_batch completed, got {len(batch_results)} results\n")
            
            # Update total sims
            total_sims += len(surviving_configs) * current_sims
            
            # Update progress
            if progress_file and start_time is not None:
                try:
                    if os.path.exists(progress_file):
                        with open(progress_file, 'r') as f:
                            progress_data = json.load(f)
                        progress_data['total_sims'] = total_sims
                        progress_data['sims_per_sec'] = total_sims / (time.time() - start_time) if time.time() > start_time else 0
                        temp_progress = progress_file + '.tmp'
                        with open(temp_progress, 'w') as f:
                            json.dump(progress_data, f)
                        os.replace(temp_progress, progress_file)
                        _log(f"[DEBUG] Updated total_sims to {total_sims}, sims_per_sec {progress_data['sims_per_sec']:.0f}\n")
                except Exception as e:
                    _log(f"[DEBUG] Progress update error: {e}\n")
            
            # Process results and combine with cached results
            config_scores = []
            
            # Process cached results
            for config_json, result in cached_results:
                if isinstance(result, str):
                    result = json.loads(result)
                # Use composite score: 70% stage + 30% normalized loot
                avg_stage = result.get('avg_stage', 0)
                avg_loot = result.get('avg_loot_per_hour', 0)
                
                # Early termination: skip builds that are clearly suboptimal
                # If we're in later rounds and this build is performing very poorly, don't waste time
                if round_num >= 2:  # After first couple rounds
                    if 'best_score_so_far' in locals() and avg_stage < best_score_so_far * 0.3:  # Less than 30% of best
                        continue  # Skip this build entirely
                
                # Normalize loot to 0-1 scale (assuming max loot around 1e6 for normalization)
                normalized_loot = min(avg_loot / 1e6, 1.0)  # Cap at 1.0
                
                score = (avg_stage * 0.7) + (normalized_loot * 300 * 0.3)  # 300 stages max for scaling
                config_scores.append((config_json, score))
                
                # Track best score for early termination
                if 'best_score_so_far' not in locals() or score > best_score_so_far:
                    best_score_so_far = score
            
            # Process evaluated results
            for config_json, result in zip(surviving_configs, batch_results):
                if isinstance(result, str):
                    result = json.loads(result)
                # Use composite score: 70% stage + 30% normalized loot
                avg_stage = result.get('avg_stage', 0)
                avg_loot = result.get('avg_loot_per_hour', 0)
                
                # Early termination: skip builds that are clearly suboptimal
                # If we're in later rounds and this build is performing very poorly, don't waste time
                if round_num >= 2:  # After first couple rounds
                    if 'best_score_so_far' in locals() and avg_stage < best_score_so_far * 0.3:  # Less than 30% of best
                        continue  # Skip this build entirely
                
                # Normalize loot to 0-1 scale (assuming max loot around 1e6 for normalization)
                normalized_loot = min(avg_loot / 1e6, 1.0)  # Cap at 1.0
                
                score = (avg_stage * 0.7) + (normalized_loot * 300 * 0.3)  # 300 stages max for scaling
                config_scores.append((config_json, score))
                
                # Store result in similarity cache for future similar builds
                config = json.loads(config_json)
                talents = config.get('talents', {})
                attrs = config.get('attributes', {})
                top_talents = sorted(talents.items(), key=lambda x: x[1], reverse=True)[:3]
                top_attrs = sorted(attrs.items(), key=lambda x: x[1], reverse=True)[:3]
                similarity_key = (tuple(top_talents), tuple(top_attrs))
                if similarity_key not in similarity_cache:
                    similarity_cache[similarity_key] = result.copy()
                
                # Track best score for early termination
                if 'best_score_so_far' not in locals() or score > best_score_so_far:
                    best_score_so_far = score
        
        # Sort by score (descending) and keep top fraction
        config_scores.sort(key=lambda x: x[1], reverse=True)
        keep_count = max(1, int(len(config_scores) * survival_rate))
        surviving_configs = [config for config, _ in config_scores[:keep_count]]
        
        # Log round results
        best_score = config_scores[0][1] if config_scores else 0
        _log(f"🏆 Round {round_num+1} complete: {len(surviving_configs)} builds survive (top {survival_rate:.0%} of {len(config_scores)}) | Best: {best_score:.1f} stages\n")
        
        # Update progress file to show round progress
        if progress_file:
            try:
                _log(f"[DEBUG] Updating progress for round {round_num+1}, surviving: {len(surviving_configs)}, file: {progress_file}\n")
                if os.path.exists(progress_file):
                    # Read current progress
                    with open(progress_file, 'r') as f:
                        progress_data = json.load(f)
                    
                    # Update with round info
                    progress_data['tier'] = f"{tier_name} Round {round_num+1}"
                    progress_data['builds_in_generation'] = len(surviving_configs)
                    progress_data['progress_percent'] = progress_data.get('progress_percent', 0)  # Keep current
                    
                    # Write atomically
                    temp_progress = progress_file + '.tmp'
                    with open(temp_progress, 'w') as f:
                        json.dump(progress_data, f)
                    os.replace(temp_progress, progress_file)
                    
                    _log(f"[DEBUG] Progress updated: {progress_data['tier']}\n")
                else:
                    _log(f"[DEBUG] Progress file not found: {progress_file}\n")
            except Exception as e:
                _log(f"[DEBUG] Progress update error: {e}\n")
        
        # Double sims for next round
        current_sims *= 2
        
        # Periodic memory cleanup
        if round_num % 3 == 0:  # Every 3 rounds
            import gc
            collected = gc.collect()
            if collected > 0:
                _log(f"[GC] Collected {collected} objects to free memory\n")
    
    # Final evaluation with full sim count for survivors
    if surviving_configs:
        final_sims = current_sims  # Use the doubled amount from last round
        _log(f"[SH Final] Evaluating {len(surviving_configs)} survivors with {final_sims} sims each\n")
        
        # Update progress for final round
        if progress_file and os.path.exists(progress_file):
            try:
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                progress_data['tier'] = f"{tier_name} Final"
                progress_data['builds_in_generation'] = len(surviving_configs)
                temp_progress = progress_file + '.tmp'
                with open(temp_progress, 'w') as f:
                    json.dump(progress_data, f)
                os.replace(temp_progress, progress_file)
            except Exception as e:
                _log(f"[DEBUG] Final progress update error: {e}\n")
        
        if use_rust:
            # Parse JSON strings to dicts for Rust
            parsed_configs = [json.loads(cfg) for cfg in surviving_configs]
            batch_results = rust_sim.simulate_batch(parsed_configs, final_sims, True)
        else:
            batch_results = python_simulate_batch(surviving_configs, final_sims)
        
        # Update total sims for final round
        total_sims += len(surviving_configs) * final_sims
        if progress_file and start_time is not None:
            try:
                if os.path.exists(progress_file):
                    with open(progress_file, 'r') as f:
                        progress_data = json.load(f)
                    progress_data['total_sims'] = total_sims
                    progress_data['sims_per_sec'] = total_sims / (time.time() - start_time) if time.time() > start_time else 0
                    temp_progress = progress_file + '.tmp'
                    with open(temp_progress, 'w') as f:
                        json.dump(progress_data, f)
                    os.replace(temp_progress, progress_file)
                    _log(f"[DEBUG] Final update total_sims to {total_sims}, sims_per_sec {progress_data['sims_per_sec']:.0f}\n")
            except Exception as e:
                _log(f"[DEBUG] Final progress update error: {e}\n")
        
        final_results = []
        for config_json, result in zip(surviving_configs, batch_results):
            if isinstance(result, str):
                result = json.loads(result)
            final_results.append((config_json, result))
        _log(f"✅ Successive halving complete: {len(final_results)} builds fully evaluated\n")
        return final_results
    
    _log(f"⚠️ Successive halving found no surviving builds\n")
    return []

def run_irl_baseline(hunter_name, level, base_config, num_sims):
    """Run the IRL baseline simulation on user's current build."""
    # Check if user has talents/attributes entered
    has_talents = any(v > 0 for v in base_config.get("talents", {}).values())
    has_attrs = any(v > 0 for v in base_config.get("attributes", {}).values())
    
    if not (has_talents or has_attrs):
        return None  # No IRL build to simulate
    
    # Use level from config if available, else use passed level
    config_level = base_config.get('level', level)
    
    print(f"[IRL BASELINE DEBUG] hunter={hunter_name}, level={config_level}")
    print(f"[IRL BASELINE DEBUG] relics={base_config.get('relics', {})}")
    print(f"[IRL BASELINE DEBUG] bonuses={base_config.get('bonuses', {})}")
    
    # Build rust config
    rust_cfg = {
        'hunter': hunter_name,
        'level': config_level,
        'stats': base_config.get('stats', {}),
        'talents': base_config.get('talents', {}),
        'attributes': base_config.get('attributes', {}),
        'inscryptions': base_config.get('inscryptions', {}),
        'mods': base_config.get('mods', {}),
        'relics': base_config.get('relics', {}),
        'gems': base_config.get('gems', {}),
        'gadgets': base_config.get('gadgets', {}),
        'bonuses': base_config.get('bonuses', {})
    }
    
    # Run simulation
    results = rust_sim.simulate_batch([rust_cfg], num_sims, True)
    if results:
        result = results[0]
        if isinstance(result, str):
            result = json.loads(result)
        
        return {
            'talents': base_config.get('talents', {}),
            'attributes': base_config.get('attributes', {}),
            'avg_stage': result.get('avg_stage', 0),
            'max_stage': result.get('max_stage', 0),
            'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
            'avg_loot_common': result.get('avg_loot_common', 0),
            'avg_loot_uncommon': result.get('avg_loot_uncommon', 0),
            'avg_loot_rare': result.get('avg_loot_rare', 0),
            'avg_damage': result.get('avg_damage', 0),
            'avg_kills': result.get('avg_kills', 0),
            'avg_xp': result.get('avg_xp', 0),
            'avg_time': result.get('avg_time', 0),
            'avg_damage_taken': result.get('avg_damage_taken', 0),
            'survival_rate': result.get('survival_rate', 0)
        }
    return None

def optimize_builds(hunter_name, level, base_config, irl_config, num_sims, builds_per_tier, use_progressive, use_rust, fast_mode, massive_mode, ultra_mode, turbo_mode, max_batch_size):
    """Main optimization function that returns results dict."""
    start_time = time.time()
    
    # Run IRL baseline FIRST - use IRL config if available, else base_config
    baseline_config = irl_config if irl_config else base_config
    print(f"[IRL BASELINE] Using {'IRL config from JSON' if irl_config else 'GUI config'}", flush=True)
    if irl_config:
        print(f"[IRL BASELINE] IRL Relics: {irl_config.get('relics', {})}", flush=True)
        print(f"[IRL BASELINE] IRL ultima_multiplier: {irl_config.get('bonuses', {}).get('ultima_multiplier', 'N/A')}", flush=True)
    irl_baseline = run_irl_baseline(hunter_name, level, baseline_config, num_sims)
    if irl_baseline:
        _log(f"[INFO] IRL baseline: avg_stage={irl_baseline['avg_stage']:.1f}, max_stage={irl_baseline['max_stage']}\n")
        
    # Get hunter class
    hunter_classes = {'Borge': Borge, 'Knox': Knox, 'Ozzy': Ozzy}
    hunter_class = hunter_classes[hunter_name]
    
    print(f"Hunter class loaded: {hunter_class}, talents: {len(hunter_class.costs['talents'])}, attrs: {len(hunter_class.costs['attributes'])}", flush=True)
    
    results = []
    tested = 0
    batch_configs = []
    batch_metadata = []
    batch_size = max_batch_size  # Allow larger batches for massive scale optimization
    total_sims = 0
    
    # Track top 10 builds by different metrics (only keep these, not all results)
    top_by_max_stage = []    # Min-heap of top 10 by max_stage
    top_by_avg_stage = []    # Min-heap of top 10 by avg_stage
    top_by_loot = []         # Min-heap of top 10 by avg_loot_per_hour
    top_by_damage = []       # Min-heap of top 10 by avg_damage
    top_by_xp = []           # Min-heap of top 10 by avg_xp
    
    def add_to_top_10(heap, build_result, metric_key):
        """Generic function to add build to a top 10 heap by any metric"""
        metric_value = build_result.get(metric_key, 0)
        if len(heap) < 10:
            heapq.heappush(heap, (metric_value, id(build_result), build_result))
        elif metric_value > heap[0][0]:
            heapq.heapreplace(heap, (metric_value, id(build_result), build_result))
    
    def update_top_lists(build_result):
        """Update all top 10 lists - O(log 10) per metric"""
        nonlocal top_by_max_stage, top_by_avg_stage, top_by_loot, top_by_damage, top_by_xp
        add_to_top_10(top_by_max_stage, build_result, 'max_stage')
        add_to_top_10(top_by_avg_stage, build_result, 'avg_stage')
        add_to_top_10(top_by_loot, build_result, 'avg_loot_per_hour')
        add_to_top_10(top_by_damage, build_result, 'avg_damage')
        add_to_top_10(top_by_xp, build_result, 'avg_xp')
    
    def finalize_top_lists():
        """Extract sorted top 10 from heaps for final output"""
        nonlocal top_by_max_stage, top_by_avg_stage, top_by_loot, top_by_damage, top_by_xp
        top_by_max_stage = sorted([item[2] for item in top_by_max_stage], 
                                 key=lambda b: b['max_stage'], reverse=True)
        top_by_avg_stage = sorted([item[2] for item in top_by_avg_stage],
                                 key=lambda b: b['avg_stage'], reverse=True)
        top_by_loot = sorted([item[2] for item in top_by_loot],
                            key=lambda b: b.get('avg_loot_per_hour', 0), reverse=True)
        top_by_damage = sorted([item[2] for item in top_by_damage],
                              key=lambda b: b.get('avg_damage', 0), reverse=True)
        top_by_xp = sorted([item[2] for item in top_by_xp],
                          key=lambda b: b.get('avg_xp', 0), reverse=True)
    
    # Progressive evolution: dynamic curriculum based on level
    if use_progressive:
        if level <= 10:
            tiers = [(1.00, "100%")]
        elif level <= 20:
            tiers = [(0.50, "50%"), (1.00, "100%")]
        elif level <= 40:
            tiers = [(0.25, "25%"), (0.50, "50%"), (1.00, "100%")]
        else:
            tiers = [
                (0.05, "5%"),
                (0.10, "10%"), 
                (0.25, "25%"),
                (0.50, "50%"),
                (0.75, "75%"),
                (1.00, "100%")
            ]
    else:
        tiers = [(1.00, "100%")]
    
    builds_per_gen = builds_per_tier  # Builds PER generation, not divided
    
    generation = 0
    generation_history = []
    tested_builds = set()  # Track unique builds to avoid duplicates
    
    # Import baseline build generator
    try:
        from baseline_builds import create_balanced_baseline_build, get_baseline_levels
        baseline_available = True
        available_baselines = get_baseline_levels()
        _log(f"[INFO] Baseline builds available for levels: {available_baselines}\n")
    except ImportError:
        baseline_available = False
        _log(f"[INFO] Baseline builds not available, using standard generation\n")
    
    elites = []  # For progressive optimization
    
    for tier_idx, (point_multiplier, tier_name) in enumerate(tiers):
        is_final_tier = (tier_idx == len(tiers) - 1)
        talent_points = int(level * point_multiplier)
        attribute_points = int(level * point_multiplier)
        
        _log(f"[TIER {tier_idx+1}/{len(tiers)}] {tier_name} points: {talent_points} talents, {attribute_points} attributes\n")
        
        # Generate all possible builds for this tier
        gen_start = time.time()
        gen_results = []
        gen_best_max_so_far = None
        gen_best_avg_so_far = None
        duplicates_skipped = 0
        
        if use_baseline:
            _log(f"[BASELINE] Using balanced baseline build for level {level}\n")
            # Use the balanced baseline build as starting point
            baseline_build = create_balanced_baseline_build(hunter_name, level)
            
            # Create a single build configuration from the baseline
            build_config = {
                'talents': baseline_build['talents'],
                'attributes': baseline_build['attributes'],
                'mods': {},
                'inscryptions': {},
                'relics': base_config.get('relics', {}),
                'gems': base_config.get('gems', {}),
                'bonuses': base_config.get('bonuses', {})
            }
            
            # Create hash for duplicate detection (baseline builds are deterministic)
            build_hash = hash(json.dumps(build_config, sort_keys=True))
            tested_builds.add(build_hash)
            
            # Add this single baseline build to the batch
            config_json = json.dumps(build_config)
            batch_configs.append(config_json)
            batch_metadata.append((baseline_build['talents'], baseline_build['attributes']))
            
            _log(f"[BASELINE] Added balanced baseline: talents={baseline_build['talents']}, attrs={baseline_build['attributes']}\n")
        else:
            # Generate build combinations for this tier (original logic)
            generator = BuildGenerator(hunter_class, level, use_smart_sampling=True, talent_points=talent_points, attribute_points=attribute_points)
            talent_combos = generator.get_talent_combinations()
            attr_combos = generator.get_attribute_combinations(max_per_infinite=30)
            
            # For progressive tiers, limit combinations to maintain builds_per_tier
            # But don't scale down for partial points - generate full combinations for the tier's budget
            max_combos = builds_per_gen // max(1, len(attr_combos)) if attr_combos else builds_per_gen
            if len(talent_combos) > max_combos:
                talent_combos = talent_combos[:max_combos]
            
            _log(f"[TIER] Talent combinations: {len(talent_combos)}, Attribute combinations: {len(attr_combos)}\n")
            
            for tal_combo in talent_combos:
                for attr_combo in attr_combos:
                    # Create build config
                    build_config = {
                        'talents': tal_combo,
                        'attributes': attr_combo,
                        'mods': {},
                        'inscryptions': {},
                        'relics': base_config.get('relics', {}),
                        'gems': base_config.get('gems', {}),
                        'bonuses': base_config.get('bonuses', {})
                    }
                    
                    # Create hash for duplicate detection
                    build_hash = hash(json.dumps(build_config, sort_keys=True))
                    if build_hash in tested_builds:
                        duplicates_skipped += 1
                        continue
                    tested_builds.add(build_hash)
                    
                    # Add to batch
                    config_json = json.dumps(build_config)
                    batch_configs.append(config_json)
                    batch_metadata.append((tal_combo, attr_combo))
                
                # Process batch when full
                if len(batch_configs) >= batch_size:
                    # Adaptive batch processing: reduce batch size if memory pressure detected
                    import psutil
                    try:
                        memory_percent = psutil.virtual_memory().percent
                        if memory_percent > 85:  # High memory usage
                            effective_batch_size = max(100, batch_size // 4)  # Reduce batch size significantly
                            _log(f"[MEMORY] High memory usage ({memory_percent:.1f}%), reducing batch size to {effective_batch_size}\n")
                        elif memory_percent > 70:  # Moderate memory usage
                            effective_batch_size = max(500, batch_size // 2)  # Reduce batch size moderately
                            _log(f"[MEMORY] Moderate memory usage ({memory_percent:.1f}%), reducing batch size to {effective_batch_size}\n")
                        else:
                            effective_batch_size = batch_size
                    except ImportError:
                        # psutil not available, use default
                        effective_batch_size = batch_size
                        _log(f"[MEMORY] psutil not available, using default batch size {effective_batch_size}\n")
                    
                    # Process in smaller chunks if batch is very large
                    for i in range(0, len(batch_configs), effective_batch_size):
                        chunk_configs = batch_configs[i:i + effective_batch_size]
                        chunk_metadata = batch_metadata[i:i + effective_batch_size]
                        
                        _log(f"[BATCH] Processing chunk {i//effective_batch_size + 1}/{(len(batch_configs) + effective_batch_size - 1)//effective_batch_size} ({len(chunk_configs)} builds)\n")
                        
                        # Adaptive successive halving based on chunk size
                        if len(chunk_configs) > 10000:
                            # Very large chunks: ultra-aggressive parameters
                            base_sims = 2 if ultra_mode else 4
                            rounds = 7 if ultra_mode else 6
                            survival_rate = 0.02 if ultra_mode else 0.05
                            _log(f"[ADAPTIVE] Large chunk ({len(chunk_configs)} builds): ultra-aggressive successive halving\n")
                        elif len(chunk_configs) > 5000:
                            # Large chunks: aggressive parameters
                            base_sims = 4 if ultra_mode or massive_mode else 8
                            rounds = 6 if ultra_mode or massive_mode else 5
                            survival_rate = 0.05 if ultra_mode else 0.1 if massive_mode else 0.15
                            _log(f"[ADAPTIVE] Large chunk ({len(chunk_configs)} builds): aggressive successive halving\n")
                        else:
                            # Use standard mode parameters
                            if ultra_mode:
                                base_sims = 4
                                rounds = 6
                                survival_rate = 0.05
                            elif massive_mode:
                                base_sims = 8
                                rounds = 5
                                survival_rate = 0.1
                            elif fast_mode:
                                base_sims = 16
                                rounds = 4
                                survival_rate = 0.25
                            else:
                                base_sims = 64
                                rounds = 3
                                survival_rate = 0.5
                        
                        _log(f"[SH] {base_sims}→{base_sims*2}→{base_sims*4}→{base_sims*8}→{base_sims*16}→{base_sims*32}→{base_sims*64} sims, {rounds} rounds, {survival_rate:.0%} survival\n")
                        
                        sh_results = evaluate_builds_successive_halving(
                            chunk_configs, 
                            base_sims=base_sims, 
                            rounds=rounds, 
                            survival_rate=survival_rate,
                            progress_file=progress_file,
                            tier_name=tier_name,
                            total_sims=total_sims,
                            start_time=start_time,
                            use_rust=use_rust
                        )
                        
                        # Process results and update tracking
                        for config_json, result in sh_results:
                            config = json.loads(config_json)
                            build_result = {
                                'talents': config['talents'],
                                'attributes': config['attributes'],
                                'avg_stage': result.get('avg_stage', 0),
                                'max_stage': result.get('max_stage', 0),
                                'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
                                'avg_loot_common': result.get('avg_loot_common', 0),
                                'avg_loot_uncommon': result.get('avg_loot_uncommon', 0),
                                'avg_loot_rare': result.get('avg_loot_rare', 0),
                                'avg_damage': result.get('avg_damage', 0),
                                'avg_kills': result.get('avg_kills', 0),
                                'avg_xp': result.get('avg_xp', 0)
                            }
                            
                            gen_results.append(build_result)
                            if is_final_tier:
                                update_top_lists(build_result)
                            
                            if gen_best_max_so_far is None or build_result['max_stage'] > gen_best_max_so_far['max_stage']:
                                gen_best_max_so_far = build_result
                            if gen_best_avg_so_far is None or build_result['avg_stage'] > gen_best_avg_so_far['avg_stage']:
                                gen_best_avg_so_far = build_result
                        
                        # Update sims counter for chunk
                        total_sh_sims = 0
                        surviving = len(chunk_configs)
                        sims_per_build = base_sims
                        for round_num in range(rounds + 1):
                            total_sh_sims += surviving * sims_per_build
                            surviving = max(1, int(surviving * survival_rate))
                            sims_per_build *= 2
                        
                        tested += len(chunk_configs)
                        total_sims += total_sh_sims
                    
                    # Clear processed batches
                    batch_configs = []
                    batch_metadata = []
        
        # Process final batch if any remaining
        if batch_configs:
            # Use same successive halving parameters as above
            if ultra_mode:
                base_sims = 4
                rounds = 6
                survival_rate = 0.05
            elif massive_mode:
                base_sims = 8
                rounds = 5
                survival_rate = 0.1
            elif fast_mode:
                base_sims = 16
                rounds = 4
                survival_rate = 0.25
            else:
                base_sims = 64
                rounds = 3
                survival_rate = 0.5
            
            sh_results = evaluate_builds_successive_halving(
                batch_configs, 
                base_sims=base_sims, 
                rounds=rounds, 
                survival_rate=survival_rate,
                progress_file=progress_file,
                tier_name=tier_name,
                total_sims=total_sims,
                start_time=start_time,
                use_rust=use_rust
            )
            
            # Process results same as above
            for config_json, result in sh_results:
                config = json.loads(config_json)
                tal = config['talents']
                att = config['attributes']
                
                build_result = {
                    'talents': tal,
                    'attributes': att,
                    'avg_stage': result.get('avg_stage', 0),
                    'max_stage': result.get('max_stage', 0),
                    'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
                    'avg_loot_common': result.get('avg_loot_common', 0),
                    'avg_loot_uncommon': result.get('avg_loot_uncommon', 0),
                    'avg_loot_rare': result.get('avg_loot_rare', 0),
                    'avg_damage': result.get('avg_damage', 0),
                    'avg_kills': result.get('avg_kills', 0),
                    'avg_xp': result.get('avg_xp', 0)
                }
                
                gen_results.append(build_result)
                if is_final_tier:
                    update_top_lists(build_result)
                
                if gen_best_max_so_far is None or build_result['max_stage'] > gen_best_max_so_far['max_stage']:
                    gen_best_max_so_far = build_result
                if gen_best_avg_so_far is None or build_result['avg_stage'] > gen_best_avg_so_far['avg_stage']:
                    gen_best_avg_so_far = build_result
            
            # Calculate sims for final batch
            total_sh_sims = 0
            surviving = len(batch_configs)
            sims_per_build = base_sims
            for round_num in range(rounds + 1):
                total_sh_sims += surviving * sims_per_build
                surviving = max(1, int(surviving * survival_rate))
                sims_per_build *= 2
            
            tested += len(batch_configs)
            total_sims += total_sh_sims
            batch_configs = []
            batch_metadata = []
        
        # Write progress after final batch
        if gen_results:
            gen_best_so_far = max(gen_results, key=lambda r: r['max_stage'])
            progress_pct = ((generation - 1 + (len(gen_results) / builds_per_gen)) / len(tiers)) * 100
            elapsed_so_far = time.time() - start_time
            speed = total_sims / elapsed_so_far if elapsed_so_far > 0 else 0
            try:
                with open(progress_file, 'w') as f:
                    json.dump({
                        'generation': generation,
                        'total_generations': len(tiers),
                        'progress': progress_pct,
                        'builds_tested': tested,
                        'builds_in_gen': len(gen_results),
                        'builds_per_gen': builds_per_gen,
                        'total_sims': total_sims,
                        'elapsed': elapsed_so_far,
                        'sims_per_sec': speed,
                        'tier_name': tier_name,
                        'best_stage': gen_best_so_far['max_stage']
                    }, f)
            except Exception as progress_err:
                pass  # Ignore progress write errors
        
        # Add generation results to overall results
        results.extend(gen_results)
        
        # Track generation stats
        if gen_results:
            gen_elapsed = time.time() - gen_start
            # Use pre-computed best builds instead of expensive max() calls
            gen_best_max = gen_best_max_so_far if gen_best_max_so_far else gen_results[0]
            gen_best_avg = gen_best_avg_so_far if gen_best_avg_so_far else gen_results[0]
            
            generation_history.append({
                'generation': generation,
                'tier_name': tier_name,
                'talent_points': talent_points,
                'attribute_points': attribute_points,
                'builds_tested': len(gen_results),
                'best_max_stage': gen_best_max['max_stage'],
                'best_avg_stage': gen_best_avg['avg_stage'],
                'best_talents': gen_best_max['talents'],
                'best_attributes': gen_best_max['attributes'],
                'elapsed': gen_elapsed,
                'duplicates_skipped': duplicates_skipped,
                'unique_builds_total': len(tested_builds)
            })
            
            # Write FINAL progress for this generation with completion flag
            progress_pct = (generation / len(tiers)) * 100
            elapsed_so_far = time.time() - start_time
            speed = total_sims / elapsed_so_far if elapsed_so_far > 0 else 0
            with open(progress_file, 'w') as f:
                json.dump({
                    'generation': generation,
                    'generation_complete': True,  # Flag for GUI to update Generations tab
                    'total_generations': len(tiers),
                    'progress': progress_pct,
                    'builds_tested': tested,
                    'builds_in_gen': len(gen_results),
                    'builds_per_gen': builds_per_gen,
                    'duplicates_skipped': duplicates_skipped,
                    'unique_builds_total': len(tested_builds),
                    'total_sims': total_sims,
                    'elapsed': elapsed_so_far,
                    'sims_per_sec': speed,
                    'tier_name': tier_name,
                    'best_stage': gen_best_max['max_stage'],
                    'best_avg_stage': gen_best_avg['avg_stage'],
                    'best_talents': gen_best_max['talents'],
                    'best_attributes': gen_best_max['attributes']
                }, f)
    
    # All tiers complete - finalize results
    
    # Finalize top 10 lists with a single batch sort (only now, not per-result)
    finalize_top_lists()
    
    elapsed = time.time() - start_time
    sims_per_sec = total_sims / elapsed if elapsed > 0 else 0
    
    # Get best builds from top 10 lists
    best_max = top_by_max_stage[0] if top_by_max_stage else None
    best_avg = top_by_avg_stage[0] if top_by_avg_stage else None
    
    # Build final output with top 10 lists and IRL baseline
    final_data = {
        'status': 'complete',
        'timing': {
            'total_time': elapsed,
            'sims_per_sec': sims_per_sec,
            'tested': tested
        },
        'irl_baseline': irl_baseline,  # Include IRL baseline (or None if no build)
        'best_build': {
            'max_stage': best_max['max_stage'] if best_max else 0,
            'avg_stage': best_avg['avg_stage'] if best_avg else 0,
            'avg_loot_per_hour': best_max.get('avg_loot_per_hour', 0) if best_max else 0,
            'avg_damage': best_max.get('avg_damage', 0) if best_max else 0,
            'avg_kills': best_max.get('avg_kills', 0) if best_max else 0,
            'avg_xp': best_max.get('avg_xp', 0) if best_max else 0,
            'talents': best_max['talents'] if best_max else {},
            'attributes': best_max['attributes'] if best_max else {}
        },
        'top_10_by_max_stage': top_by_max_stage,
        'top_10_by_avg_stage': top_by_avg_stage,
        'top_10_by_loot': top_by_loot,
        'top_10_by_damage': top_by_damage,
        'top_10_by_xp': top_by_xp,
        'generation_history': generation_history,
        'full_report': f"Tested {tested} builds in {elapsed:.1f}s ({sims_per_sec:.0f} sims/sec)\n"
                      f"Best Max Stage: {best_max['max_stage'] if best_max else 0}\n"
                      f"Best Avg Stage: {best_avg['avg_stage'] if best_avg else 0:.1f}"
    }
    
    return final_data
    """Run optimization from config dict instead of file."""
    # Extract parameters from config
    builds_per_tier = config['builds_per_tier']
    max_batch_size = config['max_batch_size']
    use_rust = config['use_rust']
    fast_mode = config['fast_mode']
    massive_mode = config['massive_mode']
    ultra_mode = config['ultra_mode']
    turbo_mode = config['turbo_mode']
    output_file = config['output_file']
    
    # For now, use default values for other parameters that were in the JSON config
    # This is a simplified version - in practice you'd want to pass all needed params
    hunter_name = config.get('hunter_name', 'Unknown')
    level = config.get('level', 1)
    base_config = config.get('base_config', {})
    irl_config = config.get('irl_config')
    num_sims = config.get('num_sims', 1000)
    use_progressive = config.get('use_progressive', True)
    
    # Run the optimization with these parameters
    try:
        results = optimize_builds(
            hunter_name=hunter_name,
            level=level,
            base_config=base_config,
            irl_config=irl_config,
            num_sims=num_sims,
            builds_per_tier=builds_per_tier,
            use_progressive=use_progressive,
            use_rust=use_rust,
            fast_mode=fast_mode,
            massive_mode=massive_mode,
            ultra_mode=ultra_mode,
            max_batch_size=max_batch_size,
            turbo_mode=turbo_mode
        )
        
        # Write results to output file
        with open(output_file, 'w') as f:
            json.dump(results, f)
            
    except Exception as e:
        import traceback
        error_result = {
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        with open(output_file, 'w') as f:
            json.dump(error_result, f)

def run_optimization(config_file, result_file):
    """Legacy function for backward compatibility."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        # Extract parameters
        hunter_name = config['hunter_name']
        level = config['level']
        base_config = config['base_config']
        irl_config = config.get('irl_config')
        num_sims = config['num_sims']
        builds_per_tier = config['builds_per_tier']
        use_progressive = config.get('use_progressive', True)
        use_rust = config.get('use_rust', True)
        fast_mode = config.get('fast_mode', False)
        massive_mode = config.get('massive_mode', False)
        ultra_mode = config.get('ultra_mode', False)
        turbo_mode = config.get('turbo_mode', False)
        max_batch_size = config.get('max_batch_size', 1000)
        
        # Run optimization
        results = optimize_builds(
            hunter_name=hunter_name,
            level=level,
            base_config=base_config,
            irl_config=irl_config,
            num_sims=num_sims,
            builds_per_tier=builds_per_tier,
            use_progressive=use_progressive,
            use_rust=use_rust,
            fast_mode=fast_mode,
            massive_mode=massive_mode,
            ultra_mode=ultra_mode,
            max_batch_size=max_batch_size,
            turbo_mode=turbo_mode
        )
        
        # Write results
        with open(result_file, 'w') as f:
            json.dump(results, f)
            
    except Exception as e:
        import traceback
        error_result = {
            'status': 'error',
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        with open(result_file, 'w') as f:
            json.dump(error_result, f)
    """Run optimization based on config file, write results to result_file."""
    
    print("Starting run_optimization", flush=True)
    
    try:
        start_time = time.time()
        
        # Load config
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        print(f"Config loaded: hunter={config.get('hunter_name')}, level={config.get('level')}", flush=True)
        
        hunter_name = config['hunter_name']
        level = config['level']
        base_config = config['base_config']
        irl_config = config.get('irl_config')  # Load IRL config if provided
        num_sims = config['num_sims']
        builds_per_tier = config['builds_per_tier']
        use_progressive = config.get('use_progressive', True)
        use_rust = config.get('use_rust', True)
        fast_mode = config.get('fast_mode', False)  # New parameter for aggressive optimization
        massive_mode = config.get('massive_mode', False)  # Ultra-aggressive for 100k+ builds
        ultra_mode = config.get('ultra_mode', False)  # Maximum speed for 1M+ builds
        max_batch_size = config.get('max_batch_size', 1000)  # Allow larger batches for massive scale
        
        # Run IRL baseline FIRST - use IRL config if available, else base_config
        baseline_config = irl_config if irl_config else base_config
        print(f"[IRL BASELINE] Using {'IRL config from JSON' if irl_config else 'GUI config'}", flush=True)
        if irl_config:
            print(f"[IRL BASELINE] IRL Relics: {irl_config.get('relics', {})}", flush=True)
            print(f"[IRL BASELINE] IRL ultima_multiplier: {irl_config.get('bonuses', {}).get('ultima_multiplier', 'N/A')}", flush=True)
        irl_baseline = run_irl_baseline(hunter_name, level, baseline_config, num_sims)
        if irl_baseline:
            _log(f"[INFO] IRL baseline: avg_stage={irl_baseline['avg_stage']:.1f}, max_stage={irl_baseline['max_stage']}\n")
            
        
        # Get hunter class
        hunter_classes = {'Borge': Borge, 'Knox': Knox, 'Ozzy': Ozzy}
        hunter_class = hunter_classes[hunter_name]
        
        print(f"Hunter class loaded: {hunter_class}, talents: {len(hunter_class.costs['talents'])}, attrs: {len(hunter_class.costs['attributes'])}", flush=True)
        
        print("Hunter class loaded", flush=True)
        
        results = []
        tested = 0
        batch_configs = []
        batch_metadata = []
        batch_size = max_batch_size  # Allow larger batches for massive scale optimization
        total_sims = 0
        
        # Track top 10 builds by different metrics (only keep these, not all results)
        top_by_max_stage = []    # Min-heap of top 10 by max_stage
        top_by_avg_stage = []    # Min-heap of top 10 by avg_stage
        top_by_loot = []         # Min-heap of top 10 by avg_loot_per_hour
        top_by_damage = []       # Min-heap of top 10 by avg_damage
        top_by_xp = []           # Min-heap of top 10 by avg_xp
        
        def add_to_top_10(heap, build_result, metric_key):
            """Generic function to add build to a top 10 heap by any metric"""
            metric_value = build_result.get(metric_key, 0)
            if len(heap) < 10:
                heapq.heappush(heap, (metric_value, id(build_result), build_result))
            elif metric_value > heap[0][0]:
                heapq.heapreplace(heap, (metric_value, id(build_result), build_result))
        
        def update_top_lists(build_result):
            """Update all top 10 lists - O(log 10) per metric"""
            nonlocal top_by_max_stage, top_by_avg_stage, top_by_loot, top_by_damage, top_by_xp
            add_to_top_10(top_by_max_stage, build_result, 'max_stage')
            add_to_top_10(top_by_avg_stage, build_result, 'avg_stage')
            add_to_top_10(top_by_loot, build_result, 'avg_loot_per_hour')
            add_to_top_10(top_by_damage, build_result, 'avg_damage')
            add_to_top_10(top_by_xp, build_result, 'avg_xp')
        
        def finalize_top_lists():
            """Extract sorted top 10 from heaps for final output"""
            nonlocal top_by_max_stage, top_by_avg_stage, top_by_loot, top_by_damage, top_by_xp
            top_by_max_stage = sorted([item[2] for item in top_by_max_stage], 
                                     key=lambda b: b['max_stage'], reverse=True)
            top_by_avg_stage = sorted([item[2] for item in top_by_avg_stage],
                                     key=lambda b: b['avg_stage'], reverse=True)
            top_by_loot = sorted([item[2] for item in top_by_loot],
                                key=lambda b: b.get('avg_loot_per_hour', 0), reverse=True)
            top_by_damage = sorted([item[2] for item in top_by_damage],
                                  key=lambda b: b.get('avg_damage', 0), reverse=True)
            top_by_xp = sorted([item[2] for item in top_by_xp],
                              key=lambda b: b.get('avg_xp', 0), reverse=True)
        
        # Progressive evolution: dynamic curriculum based on level
        if use_progressive:
            if level <= 10:
                tiers = [(1.00, "100%")]
            elif level <= 20:
                tiers = [(0.50, "50%"), (1.00, "100%")]
            elif level <= 40:
                tiers = [(0.25, "25%"), (0.50, "50%"), (1.00, "100%")]
            else:
                tiers = [
                    (0.05, "5%"),
                    (0.10, "10%"), 
                    (0.25, "25%"),
                    (0.50, "50%"),
                    (0.75, "75%"),
                    (1.00, "100%")
                ]
        else:
            tiers = [(1.00, "100%")]
        
        builds_per_gen = builds_per_tier  # Builds PER generation, not divided
        
        generation = 0
        generation_history = []
        progress_file = result_file.replace('_results.json', '_progress.json')
        
        # Write initial progress immediately so GUI knows we're alive
        with open(progress_file, 'w') as f:
            json.dump({
                'generation': 0,
                'total_generations': len(tiers),
                'progress': 0,
                'builds_tested': 0,
                'builds_in_generation': 0,
                'builds_per_gen': builds_per_gen,
                'total_sims': 0,
                'elapsed': 0,
                'sims_per_sec': 0,
                'tier': 'Starting...',
                'best_stage': 0
            }, f)
        
        for tier_fraction, tier_name in tiers:
            generation += 1
            gen_start = time.time()
            
            print(f"Starting tier {tier_name}, generation {generation}", flush=True)
            
            # Only track top 10 from the FINAL tier (100%) - earlier tiers have partial builds
            is_final_tier = (tier_fraction == 1.00)
            
            # Calculate points for this tier
            talent_points = max(1, int(level * tier_fraction))
            attribute_points = max(3, int(level * 3 * tier_fraction))
            
            print(f"Tier points: talent={talent_points}, attr={attribute_points}", flush=True)
            
            _log(f"[DEBUG] Tier {tier_name}: level={level}, talent_pts={talent_points}, attr_pts={attribute_points}\n")
            
            
            # Setup generator for this tier
            generator = BuildGenerator(hunter_class, level)
            generator.talent_points = talent_points
            generator.attribute_points = attribute_points
            generator._calculate_dynamic_attr_maxes()
            
            talents_list = list(generator.costs["talents"].keys())
            attrs_list = list(generator.costs["attributes"].keys())
            print(f"Generator created: {len(talents_list)} talents ({talents_list[:3]}...), {len(attrs_list)} attrs, talent_pts={talent_points}, attr_pts={attribute_points}", flush=True)
            
            gen_results = []
            duplicates_skipped = 0
            generation_requests = 0
            last_progress_write = 0  # Track when we last wrote progress to avoid excessive file writes
            gen_best_max_so_far = None  # Track best build incrementally to avoid expensive max() later
            gen_best_avg_so_far = None
            tested_builds = set()  # RESET per tier - allow same builds in different tiers
            
            # Determine how many builds to promote from previous generation
            if generation > 1 and results:
                # Get previous generation's results
                prev_gen_results = results[-(builds_per_gen):] if len(results) >= builds_per_gen else results
                
                # Promote at least 100 OR 10%, whichever is GREATER (but cap at what's available)
                num_promote = max(100, int(builds_per_gen * 0.10))
                num_promote = min(num_promote, len(prev_gen_results))  # Can't promote more than exist
                
                num_generate = builds_per_gen - num_promote
                
                print(f"Generation {generation}: promoting {num_promote} from previous, generating {num_generate} new", flush=True)
                
                # Get top performers from previous gen (sorted by max_stage)
                top_builds = sorted(prev_gen_results, key=lambda r: r['max_stage'], reverse=True)[:num_promote]
            else:
                # First generation - generate all fresh
                num_generate = builds_per_gen
                top_builds = []
                
                print(f"First generation: generating {num_generate} builds", flush=True)
            
            # Process promoted builds FIRST (they're known good builds from previous gen)
            for top_build in top_builds:
                # EXTEND the promoted build to use the new tier's point budget
                # This is critical! Previous tier builds have fewer points allocated.
                talents, attrs = extend_elite_pattern(
                    top_build['talents'],
                    top_build['attributes'],
                    generator,
                    talent_points,
                    attribute_points
                )
                
                # Apply small mutations AFTER extending (to explore nearby space)
                import random
                if random.random() < 0.3:  # 30% chance to mutate
                    # Swap some points between talents
                    talent_keys = [t for t in talents if talents[t] > 0]
                    if len(talent_keys) >= 2:
                        src = random.choice(talent_keys)
                        dst = random.choice([t for t in talents if t != src])
                        if talents[src] > 0:
                            talents[src] -= 1
                            talent_max_val = generator.costs["talents"][dst]["max"]
                            if talent_max_val == float('inf') or talents[dst] < int(talent_max_val):
                                talents[dst] += 1
                            else:
                                talents[src] += 1  # Undo if can't add to dst
                
                # Create a hashable key for this build to check for duplicates
                build_key = (
                    tuple(sorted((k, v) for k, v in talents.items() if v > 0)),
                    tuple(sorted((k, v) for k, v in attrs.items() if v > 0))
                )
                
                # Skip if we've already tested this build (even mutated versions)
                if build_key in tested_builds:
                    continue
                
                tested_builds.add(build_key)
                
                # Create config for promoted/mutated build
                # STATS ARE ALWAYS LOCKED TO BASE CONFIG (never mutated)
                cfg = copy.deepcopy(base_config)
                cfg['talents'] = talents
                cfg['attributes'] = attrs
                
                rust_cfg = {
                    'hunter': hunter_name,
                    'level': level,
                    'stats': cfg.get('stats', {}),  # ALWAYS base stats - never mutated
                    'talents': talents,
                    'attributes': attrs,
                    'inscryptions': cfg.get('inscryptions', {}),
                    'mods': cfg.get('mods', {}),
                    'relics': cfg.get('relics', {}),
                    'gems': cfg.get('gems', {}),
                    'gadgets': cfg.get('gadgets', {}),
                    'bonuses': cfg.get('bonuses', {})
                }
                
                batch_configs.append(json.dumps(rust_cfg))
                batch_metadata.append((talents, attrs))
                
                # For successive halving, collect all builds first, don't simulate in batches
                # The simulation will happen at the end of the generation with successive halving
            
            # Generate new random builds - keep trying until we have enough
            builds_generated = 0
            max_attempts = min(num_generate * 10, num_generate + 10000)  # Cap at target + 10k extra attempts
            attempts = 0
            stuck_attempts = 0  # Track consecutive failed generation attempts
            
            while builds_generated < num_generate and attempts < max_attempts:
                attempts += 1
                
                if attempts % 100 == 0:
                    print(f"Attempts {attempts}, builds_generated {builds_generated}, stuck {stuck_attempts}", flush=True)
                
                # Generate build
                builds = generator.generate_smart_sample(sample_size=1)
                if attempts % 100 == 0:
                    print(f"Generate returned {len(builds)} builds", flush=True)
                if not builds:
                    stuck_attempts += 1
                    if stuck_attempts >= 100:  # 100 consecutive None returns = generator is exhausted
                        print(f"Generator exhausted after {attempts} attempts", flush=True)
                        break
                    continue
                
                stuck_attempts = 0  # Reset on successful generation
                talents, attrs = builds[0]
                
                # Create a hashable key for this build (talents + attributes combo)
                build_key = (
                    tuple(sorted((k, v) for k, v in talents.items() if v > 0)),
                    tuple(sorted((k, v) for k, v in attrs.items() if v > 0))
                )
                
                # Skip if we've already tested this exact build
                if build_key in tested_builds:
                    duplicates_skipped += 1
                    continue
                
                tested_builds.add(build_key)
                builds_generated += 1  # Count successful generation
                generation_requests += 1
                
                # Log progress every 100 builds
                if builds_generated % 100 == 0:
                    progress_pct = (builds_generated / num_generate) * 100
                    _log(f"📊 Generated {builds_generated}/{num_generate} builds ({progress_pct:.0f}%) for {tier_name} tier\n")
                    
                    # Update progress bar
                    if progress_file:
                        try:
                            overall_progress = ((generation - 1) / len(tiers)) * 100 + (builds_generated / num_generate) * (100 / len(tiers))
                            if os.path.exists(progress_file):
                                with open(progress_file, 'r') as f:
                                    progress_data = json.load(f)
                                progress_data['progress_percent'] = overall_progress
                                progress_data['builds_in_generation'] = builds_generated
                                temp_progress = progress_file + '.tmp'
                                with open(temp_progress, 'w') as f:
                                    json.dump(progress_data, f)
                                os.replace(temp_progress, progress_file)
                        except Exception as e:
                            _log(f"[DEBUG] Progress update error: {e}\n")
                
                # DEBUG: Log first build of each tier
                if builds_generated == 1:
                    _log(f"[DEBUG] First build: talents_sum={sum(talents.values())}, attrs_sum={sum(attrs.values())}\n")
                    
                
                # Create config
                cfg = copy.deepcopy(base_config)
                cfg['talents'] = talents
                cfg['attributes'] = attrs
                
                # Build Rust config JSON
                rust_cfg = {
                    'hunter': hunter_name,
                    'level': level,
                    'stats': cfg.get('stats', {}),
                    'talents': talents,
                    'attributes': attrs,
                    'inscryptions': cfg.get('inscryptions', {}),
                    'mods': cfg.get('mods', {}),
                    'relics': cfg.get('relics', {}),
                    'gems': cfg.get('gems', {}),
                    'gadgets': cfg.get('gadgets', {}),
                    'bonuses': cfg.get('bonuses', {})
                }
                
                batch_configs.append(json.dumps(rust_cfg))
                batch_metadata.append((talents, attrs))
                
                # Update progress every 100 builds during generation
                if len(batch_configs) % 100 == 0 and len(batch_configs) > 0:
                    try:
                        temp_progress = progress_file + '.tmp'
                        with open(temp_progress, 'w') as f:
                            json.dump({
                                'generation': generation,
                                'total_generations': len(tiers),
                                'progress_percent': ((generation - 1) / len(tiers)) * 100,
                                'builds_tested': 0,
                                'builds_in_generation': len(batch_configs),
                                'builds_per_gen': builds_per_gen,
                                'total_sims': 0,
                                'elapsed': time.time() - start_time,
                                'sims_per_sec': 0,
                                'tier': tier_name,
                                'best_stage': 0
                            }, f)
                        os.replace(temp_progress, progress_file)
                    except:
                        pass
                
                # For successive halving, collect all builds first, don't simulate in batches
                # The simulation will happen at the end of the generation with successive halving
            
            
            # ===== GENERATION LOOP COMPLETE - USE SUCCESSIVE HALVING =====
            if builds_generated < num_generate:
                # Hit max_attempts without generating target number of unique builds
                # This is expected for higher tiers due to build space saturation
                pass  # Silently accept partial tier
            
            # Use successive halving to evaluate all collected builds for this tier
            if batch_configs:
                _log(f"🎯 Starting successive halving for {tier_name}: {len(batch_configs)} builds, 3 rounds, 64→512 sims\n")
                
                # Update progress to show simulation phase has started
                _log(f"[DEBUG] Writing initial progress for successive halving: {len(batch_configs)} builds\n")
                # Write to temp file first to avoid corruption
                temp_progress = progress_file + '.tmp'
                with open(temp_progress, 'w') as f:
                    json.dump({
                        'generation': generation,
                        'total_generations': len(tiers),
                        'progress_percent': generation / len(tiers) * 100,
                        'builds_tested': tested,
                        'builds_in_gen': len(batch_configs),
                        'builds_per_gen': builds_per_gen,
                        'total_sims': total_sims,
                        'elapsed': time.time() - start_time,
                        'sims_per_sec': total_sims / (time.time() - start_time) if time.time() > start_time else 0,
                        'tier': tier_name,
                        'best_stage': max((r.get('max_stage', 0) for r in results), default=0)
                    }, f)
                try:
                    os.replace(temp_progress, progress_file)
                except Exception as e:
                    _log(f"[DEBUG] Failed to update progress file: {e}\n")
                
                # Use successive halving with parameters based on mode
                if ultra_mode:
                    # Maximum speed mode for 1M+ builds: minimal statistical significance
                    base_sims = 4
                    rounds = 6
                    survival_rate = 0.05  # Extremely aggressive: keep only 5%
                    _log(f"[ULTRA MODE] Maximum speed successive halving: {base_sims}→{base_sims*2}→{base_sims*4}→{base_sims*8}→{base_sims*16}→{base_sims*32}→{base_sims*64} sims, {rounds} rounds, {survival_rate:.1%} survival\n")
                    _log(f"[ULTRA MODE] Total sims per build: ~{base_sims * (2**(rounds+1) - 1) // (2-1)} (estimated) - USE WITH CAUTION\n")
                elif massive_mode:
                    # Ultra-aggressive mode for 100k+ builds: minimal sims, aggressive filtering
                    base_sims = 8
                    rounds = 5
                    survival_rate = 0.1  # Very aggressive: keep only 10%
                    _log(f"[MASSIVE MODE] Ultra-aggressive successive halving: {base_sims}→{base_sims*2}→{base_sims*4}→{base_sims*8}→{base_sims*16}→{base_sims*32} sims, {rounds} rounds, {survival_rate:.0%} survival\n")
                    _log(f"[MASSIVE MODE] Total sims per build: ~{base_sims * (2**(rounds+1) - 1) // (2-1)} (estimated)\n")
                elif fast_mode:
                    # Aggressive mode: fewer sims, more rounds for massive scale
                    base_sims = 16
                    rounds = 4
                    survival_rate = 0.25  # More aggressive filtering
                    _log(f"[FAST MODE] Aggressive successive halving: {base_sims}→{base_sims*2}→{base_sims*4}→{base_sims*8}→{base_sims*16} sims, {rounds} rounds, {survival_rate:.0%} survival\n")
                    _log(f"[FAST MODE] Total sims per build: ~{base_sims * (2**(rounds+1) - 1) // (2-1)} (estimated)\n")
                else:
                    # Standard mode: balanced accuracy/speed
                    base_sims = 64
                    rounds = 3
                    survival_rate = 0.5
                    _log(f"[STANDARD MODE] Balanced successive halving: {base_sims}→{base_sims*2}→{base_sims*4}→{base_sims*8} sims, {rounds} rounds, {survival_rate:.0%} survival\n")
                    _log(f"[STANDARD MODE] Total sims per build: {base_sims * (2**(rounds+1) - 1) // (2-1)}\n")
                
                _log(f"[DEBUG] Calling evaluate_builds_successive_halving with progress_file={progress_file}\n")
                sh_results = evaluate_builds_successive_halving(
                    batch_configs, 
                    base_sims=base_sims, 
                    rounds=rounds, 
                    survival_rate=survival_rate,
                    progress_file=progress_file,
                    tier_name=tier_name,
                    total_sims=total_sims,
                    start_time=start_time,
                    use_rust=use_rust
                )
                _log(f"[DEBUG] evaluate_builds_successive_halving returned {len(sh_results)} results\n")
                
                # Process successive halving results
                for config_json, result in sh_results:
                    # Parse metadata from config_json to get talents/attrs
                    config = json.loads(config_json)
                    tal = config['talents']
                    att = config['attributes']
                    
                    build_result = {
                        'talents': tal,
                        'attributes': att,
                        'avg_stage': result.get('avg_stage', 0),
                        'max_stage': result.get('max_stage', 0),
                        'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
                        'avg_loot_common': result.get('avg_loot_common', 0),
                        'avg_loot_uncommon': result.get('avg_loot_uncommon', 0),
                        'avg_loot_rare': result.get('avg_loot_rare', 0),
                        'avg_damage': result.get('avg_damage', 0),
                        'avg_kills': result.get('avg_kills', 0),
                        'avg_xp': result.get('avg_xp', 0)
                    }
                    
                    gen_results.append(build_result)
                    # Only track top 10 from final tier (100%) - earlier tiers have partial builds
                    if is_final_tier:
                        update_top_lists(build_result)
                    
                    if gen_best_max_so_far is None or build_result['max_stage'] > gen_best_max_so_far['max_stage']:
                        gen_best_max_so_far = build_result
                    if gen_best_avg_so_far is None or build_result['avg_stage'] > gen_best_avg_so_far['avg_stage']:
                        gen_best_avg_so_far = build_result
                
                # Calculate total sims used in successive halving (dynamic based on actual parameters)
                total_sh_sims = 0
                surviving = len(batch_configs)
                sims_per_build = base_sims
                for round_num in range(rounds + 1):  # rounds + final round
                    total_sh_sims += surviving * sims_per_build
                    surviving = max(1, int(surviving * survival_rate))
                    sims_per_build *= 2
                
                tested += len(batch_configs)
                total_sims += total_sh_sims
                batch_configs = []
                batch_metadata = []
            
            # Write progress after final batch
            if gen_results:
                gen_best_so_far = max(gen_results, key=lambda r: r['max_stage'])
                progress_pct = ((generation - 1 + (len(gen_results) / builds_per_gen)) / len(tiers)) * 100
                elapsed_so_far = time.time() - start_time
                speed = total_sims / elapsed_so_far if elapsed_so_far > 0 else 0
                try:
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'generation': generation,
                            'total_generations': len(tiers),
                            'progress': progress_pct,
                            'builds_tested': tested,
                            'builds_in_gen': len(gen_results),
                            'builds_per_gen': builds_per_gen,
                            'total_sims': total_sims,
                            'elapsed': elapsed_so_far,
                            'sims_per_sec': speed,
                            'tier_name': tier_name,
                            'best_stage': gen_best_so_far['max_stage']
                        }, f)
                except Exception as progress_err:
                    pass  # Ignore progress write errors
            
            # Add generation results to overall results
            results.extend(gen_results)
            
            # Track generation stats
            if gen_results:
                gen_elapsed = time.time() - gen_start
                # Use pre-computed best builds instead of expensive max() calls
                # Use pre-computed best builds instead of expensive max() calls
                gen_best_max = gen_best_max_so_far if gen_best_max_so_far else gen_results[0]
                gen_best_avg = gen_best_avg_so_far if gen_best_avg_so_far else gen_results[0]
                
                generation_history.append({
                    'generation': generation,
                    'tier_name': tier_name,
                    'talent_points': talent_points,
                    'attribute_points': attribute_points,
                    'builds_tested': len(gen_results),
                    'best_max_stage': gen_best_max['max_stage'],
                    'best_avg_stage': gen_best_avg['avg_stage'],
                    'best_talents': gen_best_max['talents'],
                    'best_attributes': gen_best_max['attributes'],
                    'elapsed': gen_elapsed,
                    'duplicates_skipped': duplicates_skipped,
                    'unique_builds_total': len(tested_builds)
                })
                
                # Write FINAL progress for this generation with completion flag
                progress_pct = (generation / len(tiers)) * 100
                elapsed_so_far = time.time() - start_time
                speed = total_sims / elapsed_so_far if elapsed_so_far > 0 else 0
                with open(progress_file, 'w') as f:
                    json.dump({
                        'generation': generation,
                        'generation_complete': True,  # Flag for GUI to update Generations tab
                        'total_generations': len(tiers),
                        'progress': progress_pct,
                        'builds_tested': tested,
                        'builds_in_gen': len(gen_results),
                        'builds_per_gen': builds_per_gen,
                        'duplicates_skipped': duplicates_skipped,
                        'unique_builds_total': len(tested_builds),
                        'total_sims': total_sims,
                        'elapsed': elapsed_so_far,
                        'sims_per_sec': speed,
                        'tier_name': tier_name,
                        'best_stage': gen_best_max['max_stage'],
                        'best_avg_stage': gen_best_avg['avg_stage'],
                        'best_talents': gen_best_max['talents'],
                        'best_attributes': gen_best_max['attributes']
                    }, f)
        
        # All tiers complete - finalize results
        
        # Finalize top 10 lists with a single batch sort (only now, not per-result)
        finalize_top_lists()
        
        elapsed = time.time() - start_time
        sims_per_sec = total_sims / elapsed if elapsed > 0 else 0
        
        # Get best builds from top 10 lists
        best_max = top_by_max_stage[0] if top_by_max_stage else None
        best_avg = top_by_avg_stage[0] if top_by_avg_stage else None
        
        # Build final output with top 10 lists and IRL baseline
        final_data = {
            'status': 'complete',
            'timing': {
                'total_time': elapsed,
                'sims_per_sec': sims_per_sec,
                'tested': tested
            },
            'irl_baseline': irl_baseline,  # Include IRL baseline (or None if no build)
            'best_build': {
                'max_stage': best_max['max_stage'] if best_max else 0,
                'avg_stage': best_avg['avg_stage'] if best_avg else 0,
                'avg_loot_per_hour': best_max.get('avg_loot_per_hour', 0) if best_max else 0,
                'avg_damage': best_max.get('avg_damage', 0) if best_max else 0,
                'avg_kills': best_max.get('avg_kills', 0) if best_max else 0,
                'avg_xp': best_max.get('avg_xp', 0) if best_max else 0,
                'talents': best_max['talents'] if best_max else {},
                'attributes': best_max['attributes'] if best_max else {}
            },
            'top_10_by_max_stage': top_by_max_stage,
            'top_10_by_avg_stage': top_by_avg_stage,
            'top_10_by_loot': top_by_loot,
            'top_10_by_damage': top_by_damage,
            'top_10_by_xp': top_by_xp,
            'generation_history': generation_history,
            'full_report': f"Tested {tested} builds in {elapsed:.1f}s ({sims_per_sec:.0f} sims/sec)\n"
                          f"Best Max Stage: {best_max['max_stage'] if best_max else 0}\n"
                          f"Best Avg Stage: {best_avg['avg_stage'] if best_avg else 0:.1f}"
        }
        
        try:
            results_json = json.dumps(final_data)
        except Exception as e:
            _log(f"[ERROR] JSON serialization failed: {e}\n")
            
            raise
        
        with open(result_file, 'w') as f:
            f.write(results_json)
    except Exception as e:
        import traceback
        _log(f"[ERROR] Subprocess crashed: {str(e)}\n")
        _log(f"{traceback.format_exc()}\n")
        
        
        # Write error to result file
        with open(result_file, 'w') as f:
            json.dump({
                'status': 'error',
                'error': str(e),
                'traceback': traceback.format_exc()
            }, f)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Hunter Simulator Optimization')
    parser.add_argument('--config-file', type=str, help='Config file path (if not provided, reads from temp directory)')
    parser.add_argument('--builds-per-tier', type=int, help='Number of builds to evaluate per tier')
    parser.add_argument('--max-batch-size', type=int, default=1000, help='Maximum batch size for parallel processing')
    parser.add_argument('--use-rust', type=bool, default=True, help='Use Rust backend for simulation')
    parser.add_argument('--fast-mode', type=bool, default=False, help='Use fast optimization mode')
    parser.add_argument('--massive-mode', type=bool, default=False, help='Use massive optimization mode')
    parser.add_argument('--ultra-mode', type=bool, default=False, help='Use ultra optimization mode')
    parser.add_argument('--turbo-mode', type=bool, default=False, help='Use turbo optimization mode')
    parser.add_argument('--output-file', type=str, required=True, help='Output file for results')
    
    args = parser.parse_args()
    
    if args.config_file:
        # Use config file mode
        run_optimization(args.config_file, args.output_file)
    else:
        # Legacy command line mode - create minimal config and run
        # This is for backward compatibility but requires a config file to exist
        import tempfile
        from pathlib import Path
        
        # Try to find config file in temp directory (GUI creates it there)
        temp_dir = Path(tempfile.gettempdir())
        config_files = list(temp_dir.glob("hunter_opt_*_config.json"))
        if config_files:
            config_file = config_files[0]  # Use the first one found
            run_optimization(str(config_file), args.output_file)
        else:
            # Fallback - this shouldn't happen but provides error handling
            with open(args.output_file, 'w') as f:
                json.dump({
                    'status': 'error',
                    'error': 'No config file found. Please use --config-file argument or ensure GUI created config file.'
                }, f)
