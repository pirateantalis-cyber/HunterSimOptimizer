#!/usr/bin/env python3
"""
Test script for balanced baseline builds integration
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from baseline_builds import create_balanced_baseline_build, get_baseline_levels

def test_baseline_builds():
    """Test that baseline builds are created correctly"""
    print("Testing Balanced Baseline Builds")
    print("=" * 40)

    # Test different levels and hunters
    test_cases = [
        ('Borge', 10),
        ('Knox', 20),
        ('Ozzy', 50),
        ('Borge', 100),
        ('Knox', 200),
        ('Ozzy', 300)
    ]

    for hunter, level in test_cases:
        print(f"\n{hunter} Level {level}:")
        build = create_balanced_baseline_build(hunter, level)

        # Verify stats
        expected_stats = {'power': level, 'speed': level, 'max_hp': level}
        assert build['stats'] == expected_stats, f"Stats mismatch: {build['stats']} != {expected_stats}"

        # Verify talent points total
        total_talents = sum(build['talents'].values())
        assert total_talents == level, f"Talent points mismatch: {total_talents} != {level}"

        # Verify attribute points total (each level costs 5 points)
        total_attr_cost = sum(build['attributes'].values()) * 5
        expected_attr_cost = level * 3
        assert total_attr_cost == expected_attr_cost, f"Attribute cost mismatch: {total_attr_cost} != {expected_attr_cost}"

        print(f"  ✅ Stats: {build['stats']}")
        print(f"  ✅ Talents: {sum(build['talents'].values())} points")
        print(f"  ✅ Attributes: {sum(build['attributes'].values())} levels ({total_attr_cost} points)")

    # Test available levels
    levels = get_baseline_levels()
    expected_levels = list(range(10, 301, 10))
    assert levels == expected_levels, f"Available levels mismatch: {levels} != {expected_levels}"
    print(f"\n✅ Available baseline levels: {len(levels)} levels ({min(levels)}-{max(levels)})")

    print("\n🎉 All baseline build tests passed!")

if __name__ == '__main__':
    test_baseline_builds()