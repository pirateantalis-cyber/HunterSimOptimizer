#!/usr/bin/env python3
"""
Balanced Baseline Build Generator for Hunter Simulator
Creates standardized baseline builds for optimization starting points.

These builds have:
- All main stats set to the level value (power=level, speed=level, max_hp=level)
- Talent points = level
- Attribute points = level * 3
- Balanced talent/attribute distribution based on hunter class
"""

def create_balanced_baseline_build(hunter_name: str, level: int) -> dict:
    """Create a balanced baseline build for optimization starting points.

    All main stats set to level value, talent points = level, attribute points = level * 3.
    This provides consistent baselines that don't depend on player stat allocation choices.

    Args:
        hunter_name: 'Borge', 'Knox', or 'Ozzy'
        level: Hunter level (10, 20, 30, ..., 300)

    Returns:
        Complete build configuration dict
    """
    # Validate inputs
    if hunter_name not in ['Borge', 'Knox', 'Ozzy']:
        raise ValueError(f"Invalid hunter name: {hunter_name}")
    if level < 10 or level > 300 or level % 10 != 0:
        raise ValueError(f"Level must be between 10-300 in multiples of 10, got {level}")

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
    for i, talent in enumerate(talent_priority):
        if remaining_talents > 0:
            # Allocate points with priority weighting
            # Higher priority talents get more points
            priority_weight = len(talent_priority) - i  # 8, 7, 6, 5, 4, 3, 2, 1
            total_weight = sum(range(1, len(talent_priority) + 1))  # Sum of 1+2+...+8 = 36
            
            # Calculate fair share based on remaining points and priority
            fair_share = max(1, (remaining_talents * priority_weight) // total_weight)
            points = min(fair_share, remaining_talents)
            
            talents[talent] = points
            remaining_talents -= points
    
    # If there are still remaining points (due to rounding), add them to the highest priority talent
    if remaining_talents > 0 and talent_priority:
        talents[talent_priority[0]] += remaining_talents

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

    # Distribute attribute points using priority-weighted allocation
    # Each attribute level costs 5 points, so we allocate levels using weights
    remaining_attr_points = attr_points
    attr_weights = list(range(len(attr_priority), 0, -1))  # 8,7,6,5,4,3,2,1 for 8 attributes
    
    # Calculate total weight
    total_weight = sum(attr_weights)
    
    # Allocate levels based on weights, ensuring we don't exceed available points
    for i, attr in enumerate(attr_priority):
        if remaining_attr_points >= 5:
            # Calculate weighted portion of remaining points
            weight = attr_weights[i]
            weighted_levels = int((remaining_attr_points // 5) * (weight / total_weight))
            # Ensure at least 1 level if we have points and this is the last attribute
            if weighted_levels == 0 and remaining_attr_points >= 5 and i == len(attr_priority) - 1:
                weighted_levels = 1
            # Cap at remaining points
            levels = min(weighted_levels, remaining_attr_points // 5)
            if levels > 0:
                attrs[attr] = levels
                remaining_attr_points -= levels * 5
    
    # If we still have points left, distribute to highest priority attributes
    while remaining_attr_points >= 5:
        for attr in attr_priority:
            if remaining_attr_points >= 5:
                if attr not in attrs:
                    attrs[attr] = 0
                attrs[attr] += 1
                remaining_attr_points -= 5
                if remaining_attr_points < 5:
                    break

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

def get_baseline_levels() -> list:
    """Get all available baseline levels (10, 20, 30, ..., 300)"""
    return list(range(10, 301, 10))

def create_all_baseline_builds(hunter_name: str) -> dict:
    """Create baseline builds for all levels for a specific hunter.

    Returns:
        Dict mapping level -> build_config
    """
    builds = {}
    for level in get_baseline_levels():
        builds[level] = create_balanced_baseline_build(hunter_name, level)
    return builds

if __name__ == '__main__':
    # Example usage
    import json

    print("Balanced Baseline Build Generator")
    print("=" * 40)

    # Show example builds
    for hunter in ['Borge', 'Knox', 'Ozzy']:
        print(f"\n{hunter} Level 10 Baseline:")
        build = create_balanced_baseline_build(hunter, 10)
        print(f"  Stats: {build['stats']}")
        print(f"  Talents: {build['talents']}")
        print(f"  Attributes: {build['attributes']}")

    print(f"\nAvailable baseline levels: {get_baseline_levels()}")