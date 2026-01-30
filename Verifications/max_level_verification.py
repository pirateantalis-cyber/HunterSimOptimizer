#!/usr/bin/env python3
"""
Max Level Verification Tool
Checks that all talents, attributes, and inscryptions are within their defined max levels
"""

import json
import os
import sys

# Add the hunter-sim directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'hunter-sim'))

from hunters import Borge, Ozzy, Knox

def check_build_against_costs(build_data, costs, hunter_name, build_name):
    """Check if a build exceeds any max levels"""
    issues = []

    # Check talents
    if 'talents' in build_data:
        for talent, level in build_data['talents'].items():
            if talent in costs.get('talents', {}):
                max_level = costs['talents'][talent]['max']
                if level > max_level:
                    issues.append(f"Talent {talent}: {level} > max {max_level}")

    # Check attributes
    if 'attributes' in build_data:
        for attr, level in build_data['attributes'].items():
            if attr in costs.get('attributes', {}):
                max_level = costs['attributes'][attr]['max']
                if max_level != float('inf') and level > max_level:
                    issues.append(f"Attribute {attr}: {level} > max {max_level}")

    # Check inscryptions
    if 'inscryptions' in build_data:
        for inscr, level in build_data['inscryptions'].items():
            if inscr in costs.get('inscryptions', {}):
                inscr_data = costs['inscryptions'][inscr]
                if isinstance(inscr_data, dict):
                    max_level = inscr_data['max']
                else:
                    # Knox has placeholder inscryptions as just integers
                    max_level = inscr_data
                if level > max_level:
                    issues.append(f"Inscription {inscr}: {level} > max {max_level}")

    return issues

def print_costs_summary(hunter_name, costs):
    """Print a summary of all max levels for a hunter"""
    print(f"\n=== {hunter_name.upper()} MAX LEVELS ===")

    print("\nTALENTS:")
    for talent, data in costs.get('talents', {}).items():
        max_level = data['max']
        if max_level == float('inf'):
            print(f"  {talent}: ∞")
        else:
            print(f"  {talent}: {max_level}")

    print("\nATTRIBUTES:")
    for attr, data in costs.get('attributes', {}).items():
        max_level = data['max']
        if max_level == float('inf'):
            print(f"  {attr}: ∞")
        else:
            print(f"  {attr}: {max_level}")

    print("\nINSCRYPTIONS:")
    for inscr, data in costs.get('inscryptions', {}).items():
        if isinstance(data, dict):
            max_level = data['max']
        else:
            # Knox has placeholder inscryptions as just integers
            max_level = data
        if max_level == float('inf'):
            print(f"  {inscr}: ∞")
        else:
            print(f"  {inscr}: {max_level}")

def main():
    print("MAX LEVEL VERIFICATION TOOL")
    print("=" * 50)

    # Check all hunter max levels
    hunters = [
        ("Borge", Borge),
        ("Ozzy", Ozzy),
        ("Knox", Knox)
    ]

    all_issues = []

    for hunter_name, hunter_class in hunters:
        print_costs_summary(hunter_name, hunter_class.costs)

        # Check IRL builds
        build_file = f"hunter-sim/IRL Builds/my_{hunter_name.lower()}_build.json"
        if os.path.exists(build_file):
            print(f"\n--- Checking IRL Build: {build_file} ---")
            try:
                with open(build_file, 'r') as f:
                    build_data = json.load(f)

                issues = check_build_against_costs(build_data, hunter_class.costs, hunter_name, build_file)
                if issues:
                    print("❌ ISSUES FOUND:")
                    for issue in issues:
                        print(f"  {issue}")
                    all_issues.extend(issues)
                else:
                    print("✅ All levels within limits")

            except Exception as e:
                print(f"Error reading build file: {e}")

    print("\n" + "=" * 50)
    if all_issues:
        print(f"❌ TOTAL ISSUES FOUND: {len(all_issues)}")
        print("\nAll issues:")
        for issue in all_issues:
            print(f"  {issue}")
    else:
        print("✅ ALL MAX LEVELS VERIFIED - NO ISSUES FOUND")

if __name__ == "__main__":
    main()