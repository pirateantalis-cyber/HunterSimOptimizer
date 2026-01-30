#!/usr/bin/env python3
"""
Loot Formula Verification Tool
Helps verify simulation loot calculations against IRL data
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'hunter-sim'))

from rust_sim import simulate
import json

def calculate_total_loot_multiplier(build_config, global_bonuses):
    """Calculate total loot multiplier from build and global bonuses"""
    multiplier = 1.0

    # Build-specific bonuses (gems, relics, etc.)
    if 'bonuses' in build_config and build_config['bonuses']:
        # Add any build-specific loot multipliers here
        pass

    # Global bonuses - comprehensive calculation matching Rust code
    if global_bonuses:
        # === TIMELESS MASTERY (Attribute) ===
        timeless = build_config.get('attributes', {}).get('timeless_mastery', 0)
        if timeless > 0:
            hunter = build_config.get('hunter', '').lower()
            rate = {'borge': 0.14, 'ozzy': 0.16, 'knox': 0.14}.get(hunter, 0.14)
            multiplier *= 1.0 + (timeless * rate)

        # === SHARD MILESTONE ===
        shard_milestone = global_bonuses.get('shard_milestone', 0)
        if shard_milestone > 0:
            multiplier *= 1.02 ** shard_milestone

        # === RELIC #7 (Manifestation Core: Titan) ===
        relic7 = global_bonuses.get('relic_r7', 0) + global_bonuses.get('manifestation_core_titan', 0)
        if relic7 > 0:
            multiplier *= 1.05 ** relic7

        # === RESEARCH #81 ===
        research81 = global_bonuses.get('research81', 0)
        if research81 > 0:
            hunter = build_config.get('hunter', '').lower()
            research_mult = {
                (1, 'borge'): 1.1, (2, 'borge'): 1.1, (3, 'borge'): 1.1,
                (4, 'borge'): 1.32, (5, 'borge'): 1.32, (6, 'borge'): 1.32,
                (1, 'ozzy'): 1.1, (2, 'ozzy'): 1.1, (3, 'ozzy'): 1.1,
                (4, 'ozzy'): 1.32, (5, 'ozzy'): 1.32, (6, 'ozzy'): 1.32,
                (1, 'knox'): 1.1, (2, 'knox'): 1.1, (3, 'knox'): 1.1,
                (4, 'knox'): 1.32, (5, 'knox'): 1.32, (6, 'knox'): 1.32,
            }.get((research81, hunter), 1.0)
            multiplier *= research_mult

        # === INSCRYPTIONS (hunter-specific) ===
        inscryptions = build_config.get('inscryptions', {})
        hunter = build_config.get('hunter', '').lower()
        if hunter == 'borge':
            # i14: 1.1^level
            i14 = inscryptions.get('i14', 0)
            if i14 > 0:
                multiplier *= 1.1 ** i14
            # i44: 1.08^level
            i44 = inscryptions.get('i44', 0)
            if i44 > 0:
                multiplier *= 1.08 ** i44
            # i60: +3% per level
            i60 = inscryptions.get('i60', 0)
            if i60 > 0:
                multiplier *= 1.0 + (i60 * 0.03)
            # i80: 1.1^level
            i80 = inscryptions.get('i80', 0)
            if i80 > 0:
                multiplier *= 1.1 ** i80
        elif hunter == 'ozzy':
            # i32: 1.5^level
            i32 = inscryptions.get('i32', 0)
            if i32 > 0:
                multiplier *= 1.5 ** i32
            # i81: 1.1^level
            i81 = inscryptions.get('i81', 0)
            if i81 > 0:
                multiplier *= 1.1 ** i81

        # === GADGETS ===
        gadgets = build_config.get('gadgets', {})
        def gadget_loot(level):
            if level <= 0: return 1.0
            base = 1.005 ** level
            tier_mult = 1.02 ** (level // 10)
            return base * tier_mult

        if hunter == 'borge':
            wrench = gadgets.get('wrench_of_gore', gadgets.get('wrench', 0))
            multiplier *= gadget_loot(wrench)
        elif hunter == 'ozzy':
            zaptron = gadgets.get('zaptron_533', gadgets.get('zaptron', 0))
            multiplier *= gadget_loot(zaptron)
        elif hunter == 'knox':
            trident = gadgets.get('trident_of_tides', gadgets.get('trident', gadgets.get('gadget19', 0)))
            multiplier *= gadget_loot(trident)

        # Anchor (all hunters)
        anchor = gadgets.get('titan_anchor', gadgets.get('anchor_of_ages', gadgets.get('anchor', 0)))
        multiplier *= gadget_loot(anchor)

        # === LOOP MODS ===
        if hunter == 'borge':
            scavenger = global_bonuses.get('scavenger', 0)
            if scavenger > 0:
                multiplier *= 1.05 ** min(scavenger, 25)
            lm_ouro1 = global_bonuses.get('lm_ouro1', 0)
            if lm_ouro1 > 0:
                multiplier *= 1.03 ** lm_ouro1
            lm_ouro11 = global_bonuses.get('lm_ouro11', 0)
            if lm_ouro11 > 0:
                multiplier *= 1.05 ** lm_ouro11
        elif hunter == 'ozzy':
            scavenger2 = global_bonuses.get('scavenger2', 0)
            if scavenger2 > 0:
                multiplier *= 1.05 ** min(scavenger2, 25)
            lm_ouro18 = global_bonuses.get('lm_ouro18', 0)
            if lm_ouro18 > 0:
                multiplier *= 1.03 ** lm_ouro18

        # === CONSTRUCTION MILESTONES ===
        if global_bonuses.get('cm46', False): multiplier *= 1.03
        if global_bonuses.get('cm47', False): multiplier *= 1.02
        if global_bonuses.get('cm48', False): multiplier *= 1.07
        if global_bonuses.get('cm51', False): multiplier *= 1.05

        # === DIAMOND CARDS ===
        if hunter == 'borge' and global_bonuses.get('gaiden_card', False):
            multiplier *= 1.05
        if hunter == 'ozzy' and global_bonuses.get('iridian_card', False):
            multiplier *= 1.05

        # === DIAMOND SPECIALS ===
        diamond_loot = global_bonuses.get('diamond_loot', 0)
        if diamond_loot > 0:
            multiplier *= 1.0 + (diamond_loot * 0.025)

        # === IAP ===
        if global_bonuses.get('iap_travpack', False):
            multiplier *= 1.25

        # === ULTIMA ===
        ultima = global_bonuses.get('ultima_multiplier', 0.0)
        if ultima > 0.0:
            multiplier *= ultima

        # === ATTRACTION NODE #3 ===
        gem_node_3 = global_bonuses.get('gem_attraction_node3', global_bonuses.get('attraction_node_#3', global_bonuses.get('attraction_node_3', 0)))
        if gem_node_3 > 0:
            multiplier *= 1.0 + 0.25 * gem_node_3

        # === PRESENCE OF GOD (Talent) ===
        pog_level = build_config.get('talents', {}).get('presence_of_god', 0)
        if pog_level > 0:
            # This needs effect_chance - for now assume 100% for estimation
            effect_chance = 1.0  # TODO: calculate actual effect chance
            multiplier *= 1.0 + pog_level * 0.2 * effect_chance

        # === BLESSINGS OF THE SCARAB (Ozzy attribute) ===
        if hunter == 'ozzy':
            scarab = build_config.get('attributes', {}).get('blessings_of_the_scarab', 0)
            if scarab > 0:
                multiplier *= 1.0 + scarab * 0.05

    return multiplier

def calculate_loot_manually(hunter_type, final_stage, loot_mult):
    multiplier = 1.0

    # Build-specific bonuses (gems, relics, etc.)
    if 'bonuses' in build_config and build_config['bonuses']:
        # Add any build-specific loot multipliers here
        pass

    # Global bonuses
    if global_bonuses:
        # Diamond loot (percentage bonus)
        if 'diamond_loot' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['diamond_loot'] / 100.0)

        # Ultima multiplier
        if 'ultima_multiplier' in global_bonuses:
            multiplier *= global_bonuses['ultima_multiplier']

        # Hunter-specific gem loot bonuses
        hunter = build_config.get('hunter', '').lower()
        if hunter == 'borge' and 'gem_loot_borge' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['gem_loot_borge'] / 100.0)
        elif hunter == 'ozzy' and 'gem_loot_ozzy' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['gem_loot_ozzy'] / 100.0)

        # Gem attraction (might affect loot)
        if 'gem_attraction' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['gem_attraction'] / 100.0)

        # Other potential loot bonuses
        if 'skill6_loot_bonus' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['skill6_loot_bonus'])

        if 'wastarian_relic_loot_bonus' in global_bonuses:
            multiplier *= (1.0 + global_bonuses['wastarian_relic_loot_bonus'] / 100.0)

    return multiplier

def calculate_loot_manually(hunter_type, final_stage, loot_mult):
    """Calculate loot using the geometric series formula manually"""

    # Hunter-specific stage loot multiplier
    stage_mult = {
        'Borge': 1.051,
        'Ozzy': 1.059,
        'Knox': 1.074
    }[hunter_type]

    # Base loot per enemy per stage at stage 1
    base_loot = {
        'Borge': {'common': 30.74, 'uncommon': 26.44, 'rare': 19.92},
        'Ozzy': {'common': 11.1, 'uncommon': 9.56, 'rare': 7.2},
        'Knox': {'common': 0.00348, 'uncommon': 0.00302, 'rare': 0.00228}
    }[hunter_type]

    enemies_per_stage = 10.0

    # Geometric series: sum = (mult^stage - 1) / (mult - 1)
    if stage_mult > 1.0:
        geom_sum = (stage_mult ** final_stage - 1) / (stage_mult - 1)
    else:
        geom_sum = final_stage

    total_enemy_factor = geom_sum * enemies_per_stage

    print(f"\n=== Manual Calculation for {hunter_type} ===")
    print(f"Final Stage: {final_stage}")
    print(f"Stage Multiplier: {stage_mult}")
    print(f"Geometric Sum: {geom_sum:.2f}")
    print(f"Enemies Factor: {total_enemy_factor:.2f}")
    print(f"Loot Multiplier: {loot_mult}")

    for rarity, base in base_loot.items():
        loot = base * total_enemy_factor * loot_mult
        print(f"{rarity.capitalize()}: {loot:,.0f} (base: {base})")

    total = sum(base * total_enemy_factor * loot_mult for base in base_loot.values())
    print(f"Total Loot: {total:,.0f}")

    return total

def test_simulation_vs_manual(hunter_type, level, build_config=None):
    """Compare simulation results vs manual calculation"""

    # Default minimal build if none provided
    if build_config is None:
        build_config = {
            'stats': {'power': level, 'speed': level, 'max_hp': level},
            'talents': {},
            'attributes': {},
            'level': level
        }

    # Run simulation
    result = simulate(
        hunter=hunter_type,
        level=level,
        stats=build_config.get('stats'),
        talents=build_config.get('talents', {}),
        attributes=build_config.get('attributes', {}),
        num_sims=1,  # Single run for testing
        parallel=False
    )

    print(f"\n=== Simulation Results for {hunter_type} Level {level} ===")
    print(f"Final Stage: {result.get('max_stage', 'N/A')}")
    print(f"Sim Loot - Common: {result.get('avg_loot_common', 0):,.0f}")
    print(f"Sim Loot - Uncommon: {result.get('avg_loot_uncommon', 0):,.0f}")
    print(f"Sim Loot - Rare: {result.get('avg_loot_rare', 0):,.0f}")
    print(f"Sim Total Loot: {result.get('avg_loot', 0):,.0f}")

    # Try to estimate loot multiplier from the build
    # This is approximate - you'd need to check the actual multiplier calculation
    loot_mult = 1.0  # Base multiplier
    print(f"\nEstimated Loot Multiplier: {loot_mult}")

    # Manual calculation
    final_stage = result.get('max_stage', 100)
    manual_total = calculate_loot_manually(hunter_type, final_stage, loot_mult)

    sim_total = result.get('avg_loot', 0)
    difference = sim_total - manual_total
    percent_diff = (difference / manual_total * 100) if manual_total > 0 else 0

    print(f"\n=== Comparison ===")
    print(f"Simulation Total: {sim_total:,.0f}")
    print(f"Manual Total: {manual_total:,.0f}")
    print(f"Difference: {difference:,.0f} ({percent_diff:.1f}%)")

    if abs(percent_diff) > 5:
        print("⚠️  LARGE DISCREPANCY - Formula may need adjustment!")
    else:
        print("✅ Results match within acceptable range")

def test_with_real_build(hunter_type, build_name=None):
    """Test with a real build from the GUI"""

    # Try to load a build from the IRL Builds folder
    if build_name:
        build_file = f"hunter-sim/IRL Builds/{build_name}.json"
    else:
        # Try to find any build for this hunter
        import glob
        build_files = glob.glob(f"hunter-sim/IRL Builds/*{hunter_type}*.json")
        if build_files:
            build_file = build_files[0]
            build_name = build_file.split('/')[-1].replace('.json', '')
        else:
            print(f"No build files found for {hunter_type}")
            return

    try:
        with open(build_file, 'r') as f:
            build_data = json.load(f)

        # Load global bonuses
        global_bonuses_file = "hunter-sim/IRL Builds/global_bonuses.json"
        global_bonuses = {}
        try:
            with open(global_bonuses_file, 'r') as f:
                global_bonuses = json.load(f)
        except FileNotFoundError:
            print("Warning: global_bonuses.json not found")

        print(f"\n=== Testing with Real Build: {build_name} ===")

        # Extract build configuration
        config = {
            'hunter': hunter_type,
            'level': build_data.get('level', 100),
            'stats': build_data.get('stats', {}),
            'talents': build_data.get('talents', {}),
            'attributes': build_data.get('attributes', {}),
            'inscryptions': build_data.get('inscryptions', {}),
            'mods': build_data.get('mods', {}),
            'relics': build_data.get('relics', {}),
            'gems': build_data.get('gems', {}),
            'gadgets': build_data.get('gadgets', {}),
            'bonuses': build_data.get('bonuses', {})
        }

        # Calculate total loot multiplier
        total_loot_mult = calculate_total_loot_multiplier(config, global_bonuses)
        print(f"Total Loot Multiplier: {total_loot_mult:.3f}")

        # Run simulation
        result = simulate(
            hunter=hunter_type,
            level=config['level'],
            stats=config.get('stats'),
            talents=config.get('talents'),
            attributes=config.get('attributes'),
            inscryptions=config.get('inscryptions', {}),
            mods=config.get('mods', {}),
            relics=config.get('relics', {}),
            gems=config.get('gems', {}),
            gadgets=config.get('gadgets', {}),
            bonuses=global_bonuses,  # Pass global bonuses here!
            num_sims=10,  # Multiple runs for averages
            parallel=True
        )

        print(f"Level: {config['level']}")
        print(f"Avg Final Stage: {result.get('avg_stage', 0):.1f} (min: {result.get('min_stage', 0)}, max: {result.get('max_stage', 0)})")
        print(f"Avg Loot - Common: [{result.get('min_loot_common', 0):,.0f}]-{result.get('avg_loot_common', 0):,.0f}-[{result.get('max_loot_common', 0):,.0f}]")
        print(f"Avg Loot - Uncommon: [{result.get('min_loot_uncommon', 0):,.0f}]-{result.get('avg_loot_uncommon', 0):,.0f}-[{result.get('max_loot_uncommon', 0):,.0f}]")
        print(f"Avg Loot - Rare: [{result.get('min_loot_rare', 0):,.0f}]-{result.get('avg_loot_rare', 0):,.0f}-[{result.get('max_loot_rare', 0):,.0f}]")
        print(f"Avg Total Loot: {result.get('avg_loot', 0):,.0f}")

        # Manual calculation for comparison
        final_stage = result.get('avg_stage', 0)
        manual_total = calculate_loot_manually(hunter_type, final_stage, total_loot_mult)

        sim_total = result.get('avg_loot', 0)
        difference = sim_total - manual_total
        percent_diff = (difference / manual_total * 100) if manual_total > 0 else 0

        print(f"\nManual calculation (with multipliers): {manual_total:,.0f}")
        print(f"Sim vs Manual: {sim_total:,.0f} vs {manual_total:,.0f} ({percent_diff:+.1f}%)")

        if abs(percent_diff) > 10:
            print("⚠️  Significant difference - check loot multipliers!")
        elif abs(percent_diff) > 5:
            print("⚠️  Moderate difference - verify calculations")
        else:
            print("✅ Results match within acceptable range")

    except Exception as e:
        print(f"Error loading build {build_file}: {e}")

def main():
    print("Loot Formula Verification Tool")
    print("=" * 40)

    # Test basic formulas first
    print("\n--- Basic Formula Verification ---")
    test_cases = [
        ('Borge', 100, None),
        ('Ozzy', 100, None),
        ('Knox', 100, None),
    ]

    for hunter, level, config in test_cases:
        test_simulation_vs_manual(hunter, level, config)

    # Test with real builds
    print("\n--- Real Build Testing ---")
    hunters = ['Borge', 'Ozzy', 'Knox']
    for hunter in hunters:
        test_with_real_build(hunter)

    print("\n" + "=" * 40)
    print("Next steps if results don't match IRL:")
    print("1. Check if your IRL data includes all bonuses (global bonuses, IAP, etc.)")
    print("2. Verify the base loot values match current game values")
    print("3. Check if stage multipliers are correct for current game version")
    print("4. Ensure all loot multipliers are being applied (gems, relics, etc.)")
    print("\nTo debug further:")
    print("- Run the GUI and check the 'Control' tab global bonuses")
    print("- Compare sim results with known IRL values at specific stages")
    print("- Check if the geometric series formula matches game calculations")

if __name__ == "__main__":
    main()