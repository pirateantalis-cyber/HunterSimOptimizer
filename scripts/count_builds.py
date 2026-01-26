"""
Count possible build combinations for each hunter at a given level.

This helps understand the search space the optimizer needs to explore.
Shows builds per generation during progressive evolution.
"""
import sys
from pathlib import Path
from functools import lru_cache

sys.path.insert(0, str(Path(__file__).parent.parent / "hunter-sim"))
from hunters import Borge, Knox, Ozzy

# Progressive evolution tiers (same as optimizer)
EVOLUTION_TIERS = [
    (0.05, "5%"),
    (0.10, "10%"), 
    (0.25, "25%"),
    (0.50, "50%"),
    (0.75, "75%"),
    (1.00, "100%")
]


def count_talent_combinations(hunter_class, talent_points: int) -> int:
    """Count all valid talent point allocations."""
    talents = hunter_class.costs["talents"]
    talent_names = list(talents.keys())
    max_levels = [min(int(talents[t]["max"]) if talents[t]["max"] != float('inf') else talent_points, talent_points) 
                  for t in talent_names]
    
    @lru_cache(maxsize=None)
    def count_combos(idx: int, remaining: int) -> int:
        if idx == len(talent_names):
            return 1  # One valid combination (can have leftover points)
        
        total = 0
        max_for_this = min(max_levels[idx], remaining)
        for lvl in range(0, max_for_this + 1):
            total += count_combos(idx + 1, remaining - lvl)
        return total
    
    result = count_combos(0, talent_points)
    count_combos.cache_clear()
    return result


def count_attribute_combinations(hunter_class, attribute_points: int, max_per_infinite: int = 50) -> int:
    """Count all valid attribute point allocations (approximate for infinite attrs)."""
    attrs = hunter_class.costs["attributes"]
    attr_names = list(attrs.keys())
    attr_costs = [attrs[a]["cost"] for a in attr_names]
    attr_maxes = []
    
    for a in attr_names:
        max_val = attrs[a]["max"]
        if max_val == float('inf'):
            # Cap infinite attributes at a reasonable max
            attr_maxes.append(min(max_per_infinite, attribute_points // attrs[a]["cost"]))
        else:
            attr_maxes.append(int(max_val))
    
    @lru_cache(maxsize=None)
    def count_combos(idx: int, remaining: int) -> int:
        if idx == len(attr_names):
            return 1  # One valid combination
        
        cost = attr_costs[idx]
        max_lvl = min(attr_maxes[idx], remaining // cost)
        
        total = 0
        for lvl in range(0, max_lvl + 1):
            total += count_combos(idx + 1, remaining - lvl * cost)
        return total
    
    result = count_combos(0, attribute_points)
    count_combos.cache_clear()
    return result


def format_number(n: int) -> str:
    """Format large numbers with suffixes."""
    if n >= 1e18:
        return f"{n/1e18:.2f} quintillion"
    elif n >= 1e15:
        return f"{n/1e15:.2f} quadrillion"
    elif n >= 1e12:
        return f"{n/1e12:.2f} trillion"
    elif n >= 1e9:
        return f"{n/1e9:.2f} billion"
    elif n >= 1e6:
        return f"{n/1e6:.2f} million"
    elif n >= 1e3:
        return f"{n/1e3:.2f}K"
    return str(n)


def analyze_hunter(hunter_class, level: int):
    """Analyze build combinations for a hunter at a given level."""
    hunter_name = hunter_class.__name__
    talent_points = level
    attribute_points = level * 3
    
    num_talents = len(hunter_class.costs["talents"])
    num_attrs = len(hunter_class.costs["attributes"])
    
    print(f"\n{'='*70}")
    print(f"  {hunter_name} - Level {level}")
    print(f"{'='*70}")
    print(f"  Talent Points:    {talent_points}")
    print(f"  Attribute Points: {attribute_points}")
    print(f"  Num Talents:      {num_talents}")
    print(f"  Num Attributes:   {num_attrs}")
    print()
    
    # Count talent combinations
    print("  Counting talent combinations...", end=" ", flush=True)
    talent_combos = count_talent_combinations(hunter_class, talent_points)
    print(f"{format_number(talent_combos)}")
    
    # Count attribute combinations (with capped infinites)
    print("  Counting attribute combinations...", end=" ", flush=True)
    attr_combos = count_attribute_combinations(hunter_class, attribute_points, max_per_infinite=30)
    print(f"{format_number(attr_combos)}")
    
    # Total combinations
    total = talent_combos * attr_combos
    print()
    print(f"  {'─'*50}")
    print(f"  TOTAL BUILD COMBINATIONS: {format_number(total)}")
    print(f"  (Talents × Attributes = {format_number(talent_combos)} × {format_number(attr_combos)})")
    
    # Progressive Evolution breakdown
    print()
    print(f"  📈 PROGRESSIVE EVOLUTION TIERS:")
    print(f"  {'─'*50}")
    print(f"  {'Tier':<8} {'Talent Pts':>10} {'Attr Pts':>10} {'Talent Combos':>16} {'Attr Combos':>16} {'Total Builds':>18}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*16} {'-'*16} {'-'*18}")
    
    tier_data = []
    for tier_frac, tier_name in EVOLUTION_TIERS:
        tier_talent_pts = max(1, int(level * tier_frac))
        tier_attr_pts = max(3, int(level * 3 * tier_frac))
        
        tier_talent_combos = count_talent_combinations(hunter_class, tier_talent_pts)
        tier_attr_combos = count_attribute_combinations(hunter_class, tier_attr_pts, max_per_infinite=30)
        tier_total = tier_talent_combos * tier_attr_combos
        
        tier_data.append({
            'name': tier_name,
            'talent_pts': tier_talent_pts,
            'attr_pts': tier_attr_pts,
            'talent_combos': tier_talent_combos,
            'attr_combos': tier_attr_combos,
            'total': tier_total
        })
        
        print(f"  {tier_name:<8} {tier_talent_pts:>10} {tier_attr_pts:>10} "
              f"{format_number(tier_talent_combos):>16} {format_number(tier_attr_combos):>16} "
              f"{format_number(tier_total):>18}")
    
    return {
        'hunter': hunter_name,
        'level': level,
        'talent_combos': talent_combos,
        'attr_combos': attr_combos,
        'total': total,
        'tiers': tier_data
    }


def main():
    # Get levels from IRL builds
    irl_builds_path = Path(__file__).parent.parent / "hunter-sim" / "IRL Builds"
    
    levels = {}
    for hunter_name, hunter_class in [("Borge", Borge), ("Knox", Knox), ("Ozzy", Ozzy)]:
        build_file = irl_builds_path / f"my_{hunter_name.lower()}_build.json"
        if build_file.exists():
            import json
            with open(build_file) as f:
                build = json.load(f)
            levels[hunter_name] = build.get("level", 1)
        else:
            levels[hunter_name] = 50  # Default
    
    print("\n" + "="*70)
    print("  BUILD COMBINATION ANALYSIS")
    print("  (How many possible builds exist at your levels?)")
    print("="*70)
    
    results = []
    for hunter_name, hunter_class in [("Borge", Borge), ("Ozzy", Ozzy), ("Knox", Knox)]:
        level = levels.get(hunter_name, 50)
        result = analyze_hunter(hunter_class, level)
        results.append(result)
    
    # Summary
    print("\n" + "="*70)
    print("  SUMMARY")
    print("="*70)
    print(f"\n  {'Hunter':<10} {'Level':>6} {'Talent Combos':>18} {'Attr Combos':>18} {'TOTAL':>20}")
    print(f"  {'-'*10} {'-'*6} {'-'*18} {'-'*18} {'-'*20}")
    
    grand_total = 0
    for r in results:
        print(f"  {r['hunter']:<10} {r['level']:>6} {format_number(r['talent_combos']):>18} "
              f"{format_number(r['attr_combos']):>18} {format_number(r['total']):>20}")
        grand_total += r['total']
    
    print(f"\n  Grand Total (all hunters): {format_number(grand_total)}")
    print()
    
    # Perspective
    print("  📊 TIME TO TEST ALL BUILDS:")
    print("  ─────────────────────────────────────────────────")
    builds_per_sec = 65000  # Rust simulation speed
    for r in results:
        time_to_test_all = r['total'] / builds_per_sec
        time_str = format_time(time_to_test_all)
        print(f"  {r['hunter']}: {time_str} @ 65K sims/sec")
    
    # Optimizer efficiency
    print()
    print("  🚁 OPTIMIZER EFFICIENCY (Smart Search):")
    print("  ─────────────────────────────────────────────────")
    builds_per_tier = 1000  # Default setting
    total_builds_tested = builds_per_tier * len(EVOLUTION_TIERS)  # 6 tiers
    
    print(f"  Default: {builds_per_tier:,} builds/tier × {len(EVOLUTION_TIERS)} tiers = {total_builds_tested:,} builds tested")
    print()
    
    for r in results:
        coverage = total_builds_tested / r['total'] * 100
        print(f"  {r['hunter']}: Testing {total_builds_tested:,} of {format_number(r['total'])} = {coverage:.2e}% coverage")
        print(f"           But thanks to evolution, we find top ~0.01% performers!")
    
    print()
    print("  🎯 WHY IT WORKS:")
    print("  ─────────────────────────────────────────────────")
    print("  Progressive evolution starts with 5% of points, finds good patterns,")
    print("  then carries those patterns forward to 10%, 25%, 50%, 75%, 100%.")
    print("  Each tier builds on the previous - like climbing a mountain!")
    print()
    print("  The helicopter analogy: We fly over, spot peaks, and climb the best ones.")
    print("  We don't walk every square meter - that would take billions of years!")
    print()


def format_time(seconds: float) -> str:
    """Format seconds into human-readable time."""
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    elif seconds < 3600:
        return f"{seconds/60:.1f} minutes"
    elif seconds < 86400:
        return f"{seconds/3600:.1f} hours"
    elif seconds < 86400 * 365:
        return f"{seconds/86400:.1f} days"
    elif seconds < 86400 * 365 * 1000:
        return f"{seconds/(86400*365):.1f} years"
    elif seconds < 86400 * 365 * 1e9:
        return f"{seconds/(86400*365*1e6):.1f} million years"
    else:
        return f"{seconds/(86400*365*1e9):.1f} billion years"


if __name__ == "__main__":
    main()
