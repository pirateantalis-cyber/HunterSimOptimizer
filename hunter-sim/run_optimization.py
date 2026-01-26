"""
Standalone optimization runner - runs WITHOUT GUI for maximum speed.
Communicates results back via JSON file.
"""
import sys
import json
import time
import copy
from pathlib import Path
import heapq

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from hunters import Borge, Knox, Ozzy
from gui_multi import BuildGenerator
import rust_sim

def run_irl_baseline(hunter_name, level, base_config, num_sims):
    """Run the IRL baseline simulation on user's current build."""
    # Check if user has talents/attributes entered
    has_talents = any(v > 0 for v in base_config.get("talents", {}).values())
    has_attrs = any(v > 0 for v in base_config.get("attributes", {}).values())
    
    if not (has_talents or has_attrs):
        return None  # No IRL build to simulate
    
    # Build rust config
    rust_cfg = {
        'hunter': hunter_name,
        'level': level,
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
    results = rust_sim.simulate_batch([json.dumps(rust_cfg)], num_sims, True)
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
            'avg_damage': result.get('avg_damage', 0),
            'avg_kills': result.get('avg_kills', 0),
            'avg_xp': result.get('avg_xp', 0),
            'avg_time': result.get('avg_time', 0),
            'avg_damage_taken': result.get('avg_damage_taken', 0),
            'survival_rate': result.get('survival_rate', 0)
        }
    return None

def run_optimization(config_file, result_file):
    """Run optimization based on config file, write results to result_file."""
    
    try:
        start_time = time.time()
        
        # Load config
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        hunter_name = config['hunter_name']
        level = config['level']
        base_config = config['base_config']
        num_sims = config['num_sims']
        builds_per_tier = config['builds_per_tier']
        use_progressive = config.get('use_progressive', True)
        
        # Run IRL baseline FIRST
        irl_baseline = run_irl_baseline(hunter_name, level, base_config, num_sims)
        if irl_baseline:
            sys.stderr.write(f"[INFO] IRL baseline: avg_stage={irl_baseline['avg_stage']:.1f}, max_stage={irl_baseline['max_stage']}\n")
            sys.stderr.flush()
        
        # Get hunter class
        hunter_classes = {'Borge': Borge, 'Knox': Knox, 'Ozzy': Ozzy}
        hunter_class = hunter_classes[hunter_name]
        
        results = []
        tested = 0
        batch_configs = []
        batch_metadata = []
        batch_size = 100  # User can control via config if needed
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
        
        # Progressive evolution: 5% -> 100% curriculum
        if use_progressive:
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
                'builds_in_gen': 0,
                'builds_per_gen': builds_per_gen,
                'total_sims': 0,
                'elapsed': 0,
                'sims_per_sec': 0,
                'tier_name': 'Starting...',
                'best_stage': 0
            }, f)
        
        for tier_fraction, tier_name in tiers:
            generation += 1
            gen_start = time.time()
            
            # Only track top 10 from the FINAL tier (100%) - earlier tiers have partial builds
            is_final_tier = (tier_fraction == 1.00)
            
            # Calculate points for this tier
            talent_points = max(1, int(level * tier_fraction))
            attribute_points = max(3, int(level * 3 * tier_fraction))
            
            # Setup generator for this tier
            generator = BuildGenerator(hunter_class, level)
            generator.talent_points = talent_points
            generator.attribute_points = attribute_points
            generator._calculate_dynamic_attr_maxes()
            
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
                
                # Get top performers from previous gen (sorted by max_stage)
                top_builds = sorted(prev_gen_results, key=lambda r: r['max_stage'], reverse=True)[:num_promote]
            else:
                # First generation - generate all fresh
                num_generate = builds_per_gen
                top_builds = []
            
            # Process promoted builds FIRST (they're known good builds from previous gen)
            for top_build in top_builds:
                # Mutate the promoted build
                talents = dict(top_build['talents'])
                attrs = dict(top_build['attributes'])
                
                # Apply mutations: randomly adjust 1-2 talents/attributes
                import random
                if random.random() < 0.5 and talents:
                    # Mutate a talent
                    talent_key = random.choice(list(talents.keys()))
                    talents[talent_key] = max(0, min(talent_points, talents[talent_key] + random.randint(-1, 1)))
                
                if random.random() < 0.5 and attrs:
                    # Mutate an attribute
                    attr_key = random.choice(list(attrs.keys()))
                    attrs[attr_key] = max(0, attrs[attr_key] + random.randint(-2, 2))
                
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
                
                # Simulate batch when full
                if len(batch_configs) >= batch_size:
                    batch_results = rust_sim.simulate_batch(batch_configs, num_sims, True)
                    
                    for result, (tal, att) in zip(batch_results, batch_metadata):
                        if isinstance(result, str):
                            result = json.loads(result)
                        
                        gen_results.append({
                            'talents': tal,
                            'attributes': att,
                            'avg_stage': result.get('avg_stage', 0),
                            'max_stage': result.get('max_stage', 0),
                            'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
                            'avg_damage': result.get('avg_damage', 0),
                            'avg_kills': result.get('avg_kills', 0),
                            'avg_xp': result.get('avg_xp', 0)
                        })
                    
                    tested += len(batch_configs)
                    total_sims += len(batch_configs) * num_sims
                    batch_configs = []
                    batch_metadata = []
                    
                    # Write progress update DURING generation (every batch)
                    if gen_results:
                        gen_best_so_far = max(gen_results, key=lambda r: r['max_stage'])
                        progress_pct = ((generation - 1 + (len(gen_results) / builds_per_gen)) / len(tiers)) * 100
                        elapsed_so_far = time.time() - start_time
                        speed = total_sims / elapsed_so_far if elapsed_so_far > 0 else 0
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
            
            # Generate new random builds - keep trying until we have enough
            builds_generated = 0
            max_attempts = num_generate * 10  # Allow many attempts for deduplication
            attempts = 0
            stuck_attempts = 0  # Track consecutive failed generation attempts
            
            while builds_generated < num_generate and attempts < max_attempts:
                attempts += 1
                
                # Generate build
                builds = generator.generate_smart_sample(sample_size=1)
                if not builds:
                    stuck_attempts += 1
                    if stuck_attempts >= 100:  # 100 consecutive None returns = generator is exhausted
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
                
                # Simulate batch when full
                if len(batch_configs) >= batch_size:
                    # Batch is full - process it
                    try:
                        batch_results = rust_sim.simulate_batch(batch_configs, num_sims, True)
                    except Exception as e:
                        raise
                    
                    for result, (tal, att) in zip(batch_results, batch_metadata):
                        if isinstance(result, str):
                            result = json.loads(result)
                        
                        build_result = {
                            'talents': tal,
                            'attributes': att,
                            'avg_stage': result.get('avg_stage', 0),
                            'max_stage': result.get('max_stage', 0),
                            'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
                            'avg_damage': result.get('avg_damage', 0),
                            'avg_kills': result.get('avg_kills', 0),
                            'avg_xp': result.get('avg_xp', 0)
                        }
                        
                        # Keep in gen_results for generation history
                        gen_results.append(build_result)
                        # Only track top 10 from final tier (100%) - earlier tiers have partial builds
                        if is_final_tier:
                            update_top_lists(build_result)
                        
                        # Track best builds incrementally to avoid expensive max() later
                        if gen_best_max_so_far is None or build_result['max_stage'] > gen_best_max_so_far['max_stage']:
                            gen_best_max_so_far = build_result
                        if gen_best_avg_so_far is None or build_result['avg_stage'] > gen_best_avg_so_far['avg_stage']:
                            gen_best_avg_so_far = build_result
                    
                    tested += len(batch_configs)
                    total_sims += len(batch_configs) * num_sims
                    batch_configs = []
                    batch_metadata = []
                    
                    # Write progress update DURING generation (only every 100+ builds to avoid excessive file writes)
                    if gen_results and (len(gen_results) - last_progress_write >= 100):
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
                            last_progress_write = len(gen_results)
                        except Exception as progress_err:
                            sys.stderr.write(f"[WARN] Progress write failed: {progress_err}\n")
            
            
            # ===== GENERATION LOOP COMPLETE - HANDLE INCOMPLETE TIER =====
            if builds_generated < num_generate:
                # Hit max_attempts without generating target number of unique builds
                # This is expected for higher tiers due to build space saturation
                pass  # Silently accept partial tier
            
            # Process remaining builds in batch
            if batch_configs:
                batch_results = rust_sim.simulate_batch(batch_configs, num_sims, True)
                
                # Process results
                for result, (tal, att) in zip(batch_results, batch_metadata):
                    if isinstance(result, str):
                        result = json.loads(result)
                    
                    build_result = {
                        'talents': tal,
                        'attributes': att,
                        'avg_stage': result.get('avg_stage', 0),
                        'max_stage': result.get('max_stage', 0),
                        'avg_loot_per_hour': result.get('avg_loot_per_hour', 0),
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
                
                tested += len(batch_configs)
                total_sims += len(batch_configs) * num_sims
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
            sys.stderr.write(f"[ERROR] JSON serialization failed: {e}\n")
            sys.stderr.flush()
            raise
        
        with open(result_file, 'w') as f:
            f.write(results_json)
    except Exception as e:
        import traceback
        sys.stderr.write(f"[ERROR] Subprocess crashed: {str(e)}\n")
        sys.stderr.write(f"{traceback.format_exc()}\n")
        sys.stderr.flush()
        
        # Write error to result file
        with open(result_file, 'w') as f:
            json.dump({
                'status': 'error',
                'error': str(e),
                'traceback': traceback.format_exc()
            }, f)

if __name__ == '__main__':
    config_file = sys.argv[1]
    result_file = sys.argv[2]
    run_optimization(config_file, result_file)
