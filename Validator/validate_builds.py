"""
Validate simulation accuracy against real in-game data.

Automatically fetches build submissions from GitHub Issues, validates them,
runs simulations with both Rust and Python backends, and generates accuracy reports.

Usage:
    python Validator/validate_builds.py                    # Fetch from GitHub and validate
    python Validator/validate_builds.py --cached           # Use cached issues (offline mode)
    python Validator/validate_builds.py --hunter Borge     # Only validate specific hunter
"""
import sys
import json
import re
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent.parent / "hunter-sim"))

# Try to import both backends
RUST_AVAILABLE = False
PYTHON_AVAILABLE = False

try:
    import rust_sim
    RUST_AVAILABLE = True
except ImportError:
    pass

try:
    from hunters import Borge, Knox, Ozzy
    from sim import Simulation
    PYTHON_AVAILABLE = True
except ImportError:
    pass

# GitHub API config
GITHUB_API_URL = "https://api.github.com/repos/pirateantalis-cyber/HunterSimOptimizer/issues"
CACHE_FILE = Path(__file__).parent / "cached_issues.json"


@dataclass
class IRLData:
    """Real in-game data from a build submission."""
    hunter: str
    level: int
    issue_number: int
    
    # Combat stats
    enemies_killed_best: int
    enemies_killed_avg: float
    highest_stage: int
    highest_stage_avg: float
    run_avg_time_seconds: float
    
    # Resources (common/uncommon/rare)
    common_best: float
    common_avg: float
    uncommon_best: float
    uncommon_avg: float
    rare_best: float
    rare_avg: float
    
    # XP
    xp_best: float
    xp_avg: float
    
    # Build config
    config: Dict
    
    # Validation status
    valid: bool = True
    validation_errors: List[str] = field(default_factory=list)


@dataclass 
class SimData:
    """Simulated data for comparison."""
    backend: str  # 'rust' or 'python'
    avg_stage: float
    max_stage: int
    min_stage: int
    avg_kills: float
    avg_time: float
    avg_damage: float
    avg_loot_common: float
    avg_loot_uncommon: float
    avg_loot_rare: float
    avg_xp: float


def parse_number(s: str) -> float:
    """Parse numbers with suffixes like 2.98k, 426.11t, 8.56qa."""
    if not s:
        return 0.0
    s = s.strip().lower().replace(',', '')
    
    suffixes = {
        'k': 1e3,
        'm': 1e6,
        'b': 1e9,
        't': 1e12,
        'qa': 1e15,
        'qi': 1e18,
    }
    
    for suffix, multiplier in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            try:
                return float(s[:-len(suffix)]) * multiplier
            except ValueError:
                return 0.0
    
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_time(time_str: str) -> float:
    """Parse time string like '03:16:13' or '2400' to seconds."""
    if not time_str:
        return 0.0
    time_str = time_str.strip()
    
    if ':' in time_str:
        parts = time_str.split(':')
        try:
            if len(parts) == 3:
                h, m, s = map(int, parts)
                return h * 3600 + m * 60 + s
            elif len(parts) == 2:
                m, s = map(int, parts)
                return m * 60 + s
        except ValueError:
            pass
    
    try:
        return float(time_str)
    except ValueError:
        return 0.0


def fetch_github_issues(use_cache: bool = False) -> List[Dict]:
    """Fetch all build submission issues from GitHub."""
    if use_cache and CACHE_FILE.exists():
        print("  📂 Loading cached issues...")
        with open(CACHE_FILE, 'r') as f:
            return json.load(f)
    
    print("  🌐 Fetching issues from GitHub...")
    all_issues = []
    page = 1
    
    while True:
        url = f"{GITHUB_API_URL}?state=all&per_page=100&page={page}"
        try:
            req = Request(url, headers={'User-Agent': 'HunterSimValidator/1.0'})
            with urlopen(req, timeout=30) as response:
                issues = json.loads(response.read().decode())
                if not issues:
                    break
                all_issues.extend(issues)
                page += 1
                if len(issues) < 100:
                    break
        except URLError as e:
            print(f"  ⚠️ Failed to fetch issues: {e}")
            if CACHE_FILE.exists():
                print("  📂 Falling back to cached issues...")
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
            return []
    
    # Cache the results
    with open(CACHE_FILE, 'w') as f:
        json.dump(all_issues, f, indent=2)
    
    print(f"  ✓ Fetched {len(all_issues)} issues, cached to {CACHE_FILE.name}")
    return all_issues


def parse_issue_body(body: str) -> Dict[str, str]:
    """Parse GitHub issue body into field dictionary."""
    fields = {}
    current_field = None
    current_value = []
    
    for line in body.split('\n'):
        # Check for field headers (### Field Name)
        if line.startswith('### '):
            if current_field:
                fields[current_field] = '\n'.join(current_value).strip()
            current_field = line[4:].strip()
            current_value = []
        elif current_field:
            current_value.append(line)
    
    if current_field:
        fields[current_field] = '\n'.join(current_value).strip()
    
    return fields


def extract_json_from_field(field_value: str) -> Optional[Dict]:
    """Extract JSON from a field that may contain markdown code blocks."""
    # Try to find JSON in code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', field_value, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    
    # Try raw JSON
    try:
        return json.loads(field_value)
    except json.JSONDecodeError:
        pass
    
    return None


def parse_build_submission(issue: Dict) -> Optional[IRLData]:
    """Parse a GitHub issue into an IRLData object."""
    title = issue.get('title', '')
    body = issue.get('body', '')
    issue_num = issue.get('number', 0)
    
    # Check if this is a build submission
    if '[BUILD]' not in title:
        return None
    
    fields = parse_issue_body(body)
    
    # Extract basic info
    hunter = fields.get('Hunter', '').strip()
    level_str = fields.get('Hunter Level', '0')
    
    if hunter not in ['Borge', 'Knox', 'Ozzy']:
        return None
    
    try:
        level = int(level_str)
    except ValueError:
        level = 0
    
    # Extract build JSON
    build_json_field = fields.get('Build JSON (from Save/Export)', '')
    config = extract_json_from_field(build_json_field)
    
    if not config:
        return IRLData(
            hunter=hunter, level=level, issue_number=issue_num,
            enemies_killed_best=0, enemies_killed_avg=0,
            highest_stage=0, highest_stage_avg=0, run_avg_time_seconds=0,
            common_best=0, common_avg=0, uncommon_best=0, uncommon_avg=0,
            rare_best=0, rare_avg=0, xp_best=0, xp_avg=0,
            config={}, valid=False, validation_errors=["No valid build JSON found"]
        )
    
    # Validate hunter consistency
    validation_errors = []
    config_hunter = config.get('hunter', hunter)
    if config_hunter != hunter:
        validation_errors.append(f"Hunter mismatch: form says '{hunter}' but JSON says '{config_hunter}'")
    
    # Parse IRL stats
    irl_data = IRLData(
        hunter=hunter,
        level=level,
        issue_number=issue_num,
        enemies_killed_best=int(parse_number(fields.get('Best Run Enemies Killed', '0'))),
        enemies_killed_avg=parse_number(fields.get('Avg Run Enemies Killed', '0')),
        highest_stage=int(parse_number(fields.get('Highest Stage', '0'))),
        highest_stage_avg=parse_number(fields.get('Highest Stage Run Avg', '0')),
        run_avg_time_seconds=parse_time(fields.get('Run Avg Time', '0')),
        common_best=parse_number(fields.get('Common Resource - Best Run', '0')),
        common_avg=parse_number(fields.get('Common Resource - Avg', '0')),
        uncommon_best=parse_number(fields.get('Uncommon Resource - Best Run', '0')),
        uncommon_avg=parse_number(fields.get('Uncommon Resource - Avg', '0')),
        rare_best=parse_number(fields.get('Rare Resource - Best Run', '0')),
        rare_avg=parse_number(fields.get('Rare Resource - Avg', '0')),
        xp_best=parse_number(fields.get('XP Gained - Best Run', '0')),
        xp_avg=parse_number(fields.get('XP Gained - Avg', '0')),
        config=config,
        valid=len(validation_errors) == 0,
        validation_errors=validation_errors
    )
    
    return irl_data


def simulate_rust(config: Dict, num_sims: int = 100) -> Optional[SimData]:
    """Run simulation using Rust backend."""
    if not RUST_AVAILABLE:
        return None
    
    try:
        rust_cfg = {
            'hunter': config.get('hunter', 'Borge'),
            'level': config.get('level', 1),
            'stats': config.get('stats', {}),
            'talents': config.get('talents', {}),
            'attributes': config.get('attributes', {}),
            'inscryptions': config.get('inscryptions', {}),
            'mods': config.get('mods', {}),
            'relics': config.get('relics', {}),
            'gems': config.get('gems', {}),
            'gadgets': config.get('gadgets', {}),
            'bonuses': config.get('bonuses', {})
        }
        
        results = rust_sim.simulate_batch([json.dumps(rust_cfg)], num_sims, True)
        result = results[0]
        if isinstance(result, str):
            result = json.loads(result)
        
        return SimData(
            backend='rust',
            avg_stage=result.get('avg_stage', 0),
            max_stage=result.get('max_stage', 0),
            min_stage=result.get('min_stage', 0),
            avg_kills=result.get('avg_kills', 0),
            avg_time=result.get('avg_time', 0),
            avg_damage=result.get('avg_damage', 0),
            avg_loot_common=result.get('avg_loot_common', 0),
            avg_loot_uncommon=result.get('avg_loot_uncommon', 0),
            avg_loot_rare=result.get('avg_loot_rare', 0),
            avg_xp=result.get('avg_xp', 0)
        )
    except Exception as e:
        print(f"    ⚠️ Rust simulation failed: {e}")
        return None


def simulate_python(config: Dict, num_sims: int = 100) -> Optional[SimData]:
    """Run simulation using Python backend."""
    if not PYTHON_AVAILABLE:
        return None
    
    try:
        hunter_name = config.get('hunter', 'Borge')
        hunter_classes = {'Borge': Borge, 'Knox': Knox, 'Ozzy': Ozzy}
        hunter_class = hunter_classes.get(hunter_name)
        
        if not hunter_class:
            return None
        
        stages, kills, times, damages = [], [], [], []
        loot_c, loot_u, loot_r, xps = [], [], [], []
        
        for _ in range(num_sims):
            sim = Simulation(hunter_class(config))
            result = sim.run()
            stages.append(result['final_stage'])
            kills.append(result['kills'])
            times.append(result['elapsed_time'])
            damages.append(result['damage'])
            loot_c.append(result.get('loot_common', 0))
            loot_u.append(result.get('loot_uncommon', 0))
            loot_r.append(result.get('loot_rare', 0))
            xps.append(result.get('xp', 0))
        
        return SimData(
            backend='python',
            avg_stage=sum(stages) / len(stages),
            max_stage=max(stages),
            min_stage=min(stages),
            avg_kills=sum(kills) / len(kills),
            avg_time=sum(times) / len(times),
            avg_damage=sum(damages) / len(damages),
            avg_loot_common=sum(loot_c) / len(loot_c),
            avg_loot_uncommon=sum(loot_u) / len(loot_u),
            avg_loot_rare=sum(loot_r) / len(loot_r),
            avg_xp=sum(xps) / len(xps)
        )
    except Exception as e:
        print(f"    ⚠️ Python simulation failed: {e}")
        return None


def format_number(n: float) -> str:
    """Format large numbers with suffixes."""
    if n >= 1e15:
        return f"{n/1e15:.2f}qa"
    elif n >= 1e12:
        return f"{n/1e12:.2f}t"
    elif n >= 1e9:
        return f"{n/1e9:.2f}b"
    elif n >= 1e6:
        return f"{n/1e6:.2f}m"
    elif n >= 1e3:
        return f"{n/1e3:.2f}k"
    elif n >= 1:
        return f"{n:.1f}"
    else:
        return f"{n:.4f}"


def pct_diff(irl_val: float, sim_val: float) -> float:
    """Calculate percentage difference."""
    if irl_val == 0:
        return 0 if sim_val == 0 else float('inf')
    return ((sim_val - irl_val) / irl_val) * 100


def compare_irl_vs_sim(irl: IRLData, sim: SimData) -> Dict:
    """Compare IRL data vs simulated data."""
    return {
        'Stage (Avg)': {'irl': irl.highest_stage_avg, 'sim': sim.avg_stage},
        'Stage (Max)': {'irl': irl.highest_stage, 'sim': sim.max_stage},
        'Kills (Avg)': {'irl': irl.enemies_killed_avg, 'sim': sim.avg_kills},
        'Time (Avg)': {'irl': irl.run_avg_time_seconds, 'sim': sim.avg_time},
        'Common Loot': {'irl': irl.common_avg, 'sim': sim.avg_loot_common},
        'Uncommon Loot': {'irl': irl.uncommon_avg, 'sim': sim.avg_loot_uncommon},
        'Rare Loot': {'irl': irl.rare_avg, 'sim': sim.avg_loot_rare},
        'XP (Avg)': {'irl': irl.xp_avg, 'sim': sim.avg_xp},
    }


def print_build_report(irl: IRLData, rust_sim: Optional[SimData], py_sim: Optional[SimData]):
    """Print detailed comparison report for a single build."""
    print(f"\n  {'─'*66}")
    print(f"  📋 Issue #{irl.issue_number}: {irl.hunter} Level {irl.level}")
    print(f"  {'─'*66}")
    
    if not irl.valid:
        print(f"  ❌ INVALID BUILD:")
        for err in irl.validation_errors:
            print(f"     • {err}")
        return {}
    
    results = {}
    
    for sim_data, label in [(rust_sim, 'Rust'), (py_sim, 'Python')]:
        if not sim_data:
            print(f"\n  [{label}] ⚠️ Backend not available")
            continue
        
        comparisons = compare_irl_vs_sim(irl, sim_data)
        
        print(f"\n  [{label}] {'Metric':<20} {'IRL':>12} {'Sim':>12} {'Diff':>10}")
        print(f"  {'':7}{'-'*20} {'-'*12} {'-'*12} {'-'*10}")
        
        diffs = []
        for metric, data in comparisons.items():
            irl_val = data['irl']
            sim_val = data['sim']
            diff = pct_diff(irl_val, sim_val)
            diffs.append(abs(diff) if diff != float('inf') else 0)
            
            irl_str = format_number(irl_val)
            sim_str = format_number(sim_val)
            
            if abs(diff) <= 5:
                marker = "✓"
            elif abs(diff) <= 20:
                marker = "~"
            else:
                marker = "⚠"
            
            diff_str = f"{diff:+.1f}%" if diff != float('inf') else "N/A"
            print(f"  {'':7}{metric:<20} {irl_str:>12} {sim_str:>12} {diff_str:>8} {marker}")
        
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        results[label.lower()] = {
            'comparisons': comparisons,
            'avg_diff': avg_diff,
            'diffs': diffs
        }
        
        print(f"\n  [{label}] Average Discrepancy: {avg_diff:.1f}%")
    
    return results


def print_summary_report(all_results: Dict[str, List[Tuple[IRLData, Dict]]]):
    """Print summary report grouped by hunter and backend."""
    print("\n" + "=" * 70)
    print("  📊 VALIDATION SUMMARY REPORT")
    print("=" * 70)
    
    for hunter in ['Borge', 'Knox', 'Ozzy']:
        hunter_results = all_results.get(hunter, [])
        if not hunter_results:
            continue
        
        print(f"\n  {'─'*66}")
        print(f"  🎯 {hunter.upper()} ({len(hunter_results)} builds)")
        print(f"  {'─'*66}")
        
        for backend in ['rust', 'python']:
            backend_diffs = []
            stage_diffs = []
            loot_diffs = []
            
            for irl, results in hunter_results:
                if backend in results:
                    backend_diffs.append(results[backend]['avg_diff'])
                    comps = results[backend]['comparisons']
                    stage_diffs.append(abs(pct_diff(comps['Stage (Avg)']['irl'], comps['Stage (Avg)']['sim'])))
                    loot_diffs.append(abs(pct_diff(comps['Common Loot']['irl'], comps['Common Loot']['sim'])))
            
            if not backend_diffs:
                continue
            
            avg_overall = sum(backend_diffs) / len(backend_diffs)
            avg_stage = sum(stage_diffs) / len(stage_diffs)
            avg_loot = sum(loot_diffs) / len(loot_diffs)
            
            status = "✓ GOOD" if avg_stage < 5 else "~ OK" if avg_stage < 15 else "⚠ NEEDS WORK"
            
            print(f"\n  [{backend.upper():6}] Builds Tested: {len(backend_diffs)}")
            print(f"  {'':8} Stage Accuracy: {100 - avg_stage:.1f}% ({status})")
            print(f"  {'':8} Loot Accuracy:  {100 - min(avg_loot, 100):.1f}%")
            print(f"  {'':8} Overall Avg Diff: {avg_overall:.1f}%")
    
    # Cross-backend comparison
    print(f"\n  {'─'*66}")
    print(f"  🔄 RUST vs PYTHON COMPARISON")
    print(f"  {'─'*66}")
    
    rust_stage_diffs = []
    python_stage_diffs = []
    
    for hunter_results in all_results.values():
        for irl, results in hunter_results:
            if 'rust' in results:
                comps = results['rust']['comparisons']
                rust_stage_diffs.append(abs(pct_diff(comps['Stage (Avg)']['irl'], comps['Stage (Avg)']['sim'])))
            if 'python' in results:
                comps = results['python']['comparisons']
                python_stage_diffs.append(abs(pct_diff(comps['Stage (Avg)']['irl'], comps['Stage (Avg)']['sim'])))
    
    if rust_stage_diffs:
        print(f"\n  Rust Stage Accuracy:   {100 - sum(rust_stage_diffs)/len(rust_stage_diffs):.1f}% (n={len(rust_stage_diffs)})")
    if python_stage_diffs:
        print(f"  Python Stage Accuracy: {100 - sum(python_stage_diffs)/len(python_stage_diffs):.1f}% (n={len(python_stage_diffs)})")
    
    if rust_stage_diffs and python_stage_diffs:
        rust_avg = sum(rust_stage_diffs) / len(rust_stage_diffs)
        python_avg = sum(python_stage_diffs) / len(python_stage_diffs)
        if abs(rust_avg - python_avg) < 1:
            print(f"\n  ✓ Rust and Python are closely aligned!")
        elif rust_avg < python_avg:
            print(f"\n  ℹ️ Rust is {python_avg - rust_avg:.1f}% more accurate than Python")
        else:
            print(f"\n  ℹ️ Python is {rust_avg - python_avg:.1f}% more accurate than Rust")


def main():
    parser = argparse.ArgumentParser(description="Validate builds against IRL data")
    parser.add_argument('--cached', action='store_true', help='Use cached issues (offline mode)')
    parser.add_argument('--hunter', type=str, choices=['Borge', 'Knox', 'Ozzy'], help='Only validate specific hunter')
    parser.add_argument('--sims', type=int, default=100, help='Number of simulations per build')
    parser.add_argument('--rust-only', action='store_true', help='Only use Rust backend')
    parser.add_argument('--python-only', action='store_true', help='Only use Python backend')
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("  🎮 HUNTER SIM VALIDATION SYSTEM")
    print("  Comparing In-Game Stats vs Simulation")
    print("=" * 70)
    
    print(f"\n  Backends Available:")
    print(f"    Rust:   {'✓ Ready' if RUST_AVAILABLE else '✗ Not installed'}")
    print(f"    Python: {'✓ Ready' if PYTHON_AVAILABLE else '✗ Not installed'}")
    
    if not RUST_AVAILABLE and not PYTHON_AVAILABLE:
        print("\n  ❌ No simulation backends available!")
        return 1
    
    # Fetch issues
    print()
    issues = fetch_github_issues(use_cache=args.cached)
    
    if not issues:
        print("  ❌ No issues found!")
        return 1
    
    # Parse build submissions
    print(f"\n  Parsing {len(issues)} issues...")
    builds: List[IRLData] = []
    rejected = {'no_build_tag': 0, 'parse_error': 0, 'hunter_mismatch': 0}
    
    for issue in issues:
        irl_data = parse_build_submission(issue)
        if irl_data is None:
            rejected['no_build_tag'] += 1
        elif not irl_data.valid:
            rejected['parse_error'] += 1
            print(f"    ⚠️ Issue #{issue.get('number')}: {irl_data.validation_errors}")
        else:
            # Filter by hunter if specified
            if args.hunter and irl_data.hunter != args.hunter:
                continue
            builds.append(irl_data)
    
    print(f"\n  📊 Build Summary:")
    print(f"    Valid builds:    {len(builds)}")
    print(f"    Non-build issues: {rejected['no_build_tag']}")
    print(f"    Parse errors:    {rejected['parse_error']}")
    
    if not builds:
        print("\n  ❌ No valid builds to validate!")
        return 1
    
    # Group by hunter
    by_hunter = {'Borge': [], 'Knox': [], 'Ozzy': []}
    for build in builds:
        by_hunter[build.hunter].append(build)
    
    print(f"\n  Builds by Hunter:")
    for hunter, hunter_builds in by_hunter.items():
        if hunter_builds:
            print(f"    {hunter}: {len(hunter_builds)}")
    
    # Run validations
    print(f"\n  Running simulations ({args.sims} sims per build)...")
    
    all_results: Dict[str, List[Tuple[IRLData, Dict]]] = {
        'Borge': [], 'Knox': [], 'Ozzy': []
    }
    
    for build in builds:
        print(f"\n  ⏳ Simulating {build.hunter} L{build.level} (Issue #{build.issue_number})...", end="", flush=True)
        
        rust_result = None
        python_result = None
        
        if RUST_AVAILABLE and not args.python_only:
            rust_result = simulate_rust(build.config, args.sims)
        
        if PYTHON_AVAILABLE and not args.rust_only:
            python_result = simulate_python(build.config, args.sims)
        
        print(" Done!")
        
        results = print_build_report(build, rust_result, python_result)
        all_results[build.hunter].append((build, results))
    
    # Print summary
    print_summary_report(all_results)
    
    print("\n  💡 To submit more builds:")
    print("     https://github.com/pirateantalis-cyber/HunterSimOptimizer/issues/new?template=build_submission.yml")
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
