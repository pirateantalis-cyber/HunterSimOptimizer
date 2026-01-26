

"""
Hunter Sim GUI - Multi-Hunter Build Optimizer
==============================================
A GUI application with separate tabs for each hunter (Borge, Knox, Ozzy).
Each hunter has sub-tabs for Build Configuration, Run Optimization, and Results.
Automatically loads/saves builds from IRL Builds folder.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import itertools
import queue
import time
import math
import re
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
import statistics
from collections import Counter
import copy
import sys
import os
import json
import subprocess
import tempfile
import traceback
from tkinter import filedialog
from pathlib import Path

# Try to import PIL for portrait images
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hunters import Borge, Knox, Ozzy, Hunter
from sim import SimulationManager, Simulation, sim_worker
from units import Boss, Enemy

# Try to import Rust simulator
try:
    import rust_sim
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False

# Simple BuildResult - just a container for optimization results
@dataclass
class BuildResult:
    talents: Dict[str, int]
    attributes: Dict[str, int]
    avg_final_stage: float
    highest_stage: int
    lowest_stage: int = 0
    avg_loot_per_hour: float = 0.0
    avg_damage: float = 0.0
    avg_kills: float = 0.0
    avg_elapsed_time: float = 0.0
    avg_damage_taken: float = 0.0
    survival_rate: float = 1.0
    avg_xp: float = 0.0
    config: Dict = field(default_factory=dict)
    
    def __lt__(self, other):
        return self.avg_final_stage < other.avg_final_stage

RUST_SIM_AVAILABLE = RUST_AVAILABLE


class BuildGenerator:
    """Generates all valid talent/attribute combinations for a given hunter and level."""
    
    def __init__(self, hunter_class, level: int, use_smart_sampling: bool = True):
        self.hunter_class = hunter_class
        self.level = level
        self.talent_points = level  # 1 talent point per level
        self.attribute_points = level * 3  # 3 attribute points per level
        self.costs = hunter_class.costs
        self.use_smart_sampling = use_smart_sampling
        
        # Calculate dynamic maxes for infinite attributes based on total points
        self._calculate_dynamic_attr_maxes()
        
    def _calculate_dynamic_attr_maxes(self):
        """
        For attributes with infinite max, calculate a realistic cap based on:
        - The total attribute points available
        - The cost to max out all limited attributes
        
        When there are multiple unlimited attributes, they SHARE the remaining budget.
        Each unlimited attr gets: (remaining_budget / num_unlimited_attrs)
        """
        attrs = self.costs["attributes"]
        
        # Find unlimited attributes and calculate cost to max all limited ones
        unlimited_attrs = [a for a, info in attrs.items() if info["max"] == float("inf")]
        limited_attr_cost = sum(info["cost"] * info["max"] 
                               for a, info in attrs.items() 
                               if info["max"] != float("inf"))
        
        # Calculate remaining budget and share it among unlimited attributes
        if unlimited_attrs:
            remaining_budget = self.attribute_points - limited_attr_cost
            max_per_unlimited = max(1, remaining_budget // len(unlimited_attrs))
            self.dynamic_attr_maxes = {a: max_per_unlimited for a in unlimited_attrs}
        else:
            self.dynamic_attr_maxes = {}
    
    def get_dynamic_attr_max(self, attr_name: str) -> int:
        """Get the effective max for an attribute, using dynamic calc for unlimited attrs."""
        if attr_name in self.dynamic_attr_maxes:
            return self.dynamic_attr_maxes[attr_name]
        
        base_max = self.costs["attributes"][attr_name]["max"]
        if base_max == float("inf"):
            return 250
        return int(base_max)
        
    def get_talent_combinations(self) -> List[Dict[str, int]]:
        """Generate all valid talent point allocations."""
        talents = list(self.costs["talents"].keys())
        max_levels = [min(self.costs["talents"][t]["max"], self.talent_points) 
                      for t in talents]
        
        combinations = []
        self._generate_talent_combos(talents, max_levels, {}, 0, 0, combinations)
        return combinations
    
    def _generate_talent_combos(self, talents, max_levels, current, index, points_spent, results):
        """Recursively generate talent combinations."""
        if index == len(talents):
            if points_spent <= self.talent_points:
                results.append(current.copy())
            return
        
        talent = talents[index]
        max_lvl = min(max_levels[index], self.talent_points - points_spent)
        
        for lvl in range(0, int(max_lvl) + 1):
            current[talent] = lvl
            self._generate_talent_combos(talents, max_levels, current, index + 1, 
                                        points_spent + lvl, results)
    
    def get_attribute_combinations(self, max_per_infinite: int = 30) -> List[Dict[str, int]]:
        """Generate valid attribute point allocations using a smarter approach."""
        attributes = list(self.costs["attributes"].keys())
        attr_costs = {a: self.costs["attributes"][a]["cost"] for a in attributes}
        attr_max = {a: self.costs["attributes"][a]["max"] for a in attributes}
        
        combinations = []
        self._generate_attr_combos(attributes, attr_costs, attr_max, {}, 0, 0, combinations, max_per_infinite)
        return combinations
    
    def _generate_attr_combos(self, attributes, costs, max_levels, current, index, points_spent, results, max_per_infinite):
        """Recursively generate attribute combinations."""
        if index == len(attributes):
            if points_spent <= self.attribute_points:
                results.append(current.copy())
            return
        
        if points_spent > self.attribute_points:
            return
            
        attr = attributes[index]
        cost = costs[attr]
        max_lvl = min(max_levels[attr], (self.attribute_points - points_spent) // cost)
        
        if max_lvl == float('inf'):
            max_lvl = (self.attribute_points - points_spent) // cost
        
        max_lvl = int(min(max_lvl, max_per_infinite))
        
        for lvl in range(0, max_lvl + 1):
            current[attr] = lvl
            self._generate_attr_combos(attributes, costs, max_levels, current, index + 1,
                                       points_spent + (lvl * cost), results, max_per_infinite)
    
    def generate_smart_sample(self, sample_size: int = 100, strategy: str = None) -> List[Tuple[Dict, Dict]]:
        """Generate a smart sample of builds using random walk allocation."""
        import random
        
        builds = []
        talents_list = list(self.costs["talents"].keys())
        attrs_list = list(self.costs["attributes"].keys())
        attr_costs = {a: self.costs["attributes"][a]["cost"] for a in attrs_list}
        attr_max = {a: self.costs["attributes"][a]["max"] for a in attrs_list}
        talent_max = {t: self.costs["talents"][t]["max"] for t in talents_list}
        
        for _ in range(sample_size):
            talents = self._random_walk_talent_allocation(talents_list, talent_max)
            attrs = self._random_walk_attr_allocation(attrs_list, attr_costs, attr_max)
            builds.append((talents, attrs))
        
        return builds
    
    def _random_walk_talent_allocation(self, talents, max_levels) -> Dict[str, int]:
        """True random walk talent allocation - simulate human point-by-point clicking."""
        import random
        result = {t: 0 for t in talents}
        remaining = self.talent_points
        
        # Get talents that require all others to be maxed first
        requires_all_maxed = getattr(self.hunter_class, 'talent_requires_all_maxed', [])
        
        while remaining > 0:
            # Check if all normal talents are maxed
            all_normal_maxed = all(
                result[t] >= int(max_levels[t]) 
                for t in talents 
                if t not in requires_all_maxed and t != 'unknown_talent' and max_levels[t] != float('inf')
            )
            
            valid_talents = []
            for t in talents:
                if t == 'unknown_talent':
                    continue
                if max_levels[t] != float('inf') and result[t] >= int(max_levels[t]):
                    continue  # Already maxed
                # If this talent requires all others maxed, only allow if they are
                if t in requires_all_maxed and not all_normal_maxed:
                    continue
                valid_talents.append(t)
            
            if not valid_talents:
                if 'unknown_talent' in talents:
                    valid_talents = ['unknown_talent']
                else:
                    break
            
            chosen = random.choice(valid_talents)
            result[chosen] += 1
            remaining -= 1
        
        return result
    
    def _can_unlock_attribute(self, attr: str, current_allocation: Dict[str, int], costs: Dict[str, int]) -> bool:
        """Check if an attribute can be unlocked based on point gates."""
        point_gates = getattr(self.hunter_class, 'attribute_point_gates', {})
        
        if attr not in point_gates:
            return True
        
        required_points = point_gates[attr]
        points_spent = sum(
            current_allocation.get(other_attr, 0) * costs[other_attr]
            for other_attr in current_allocation
            if other_attr != attr
        )
        
        return points_spent >= required_points
    
    def _random_walk_attr_allocation(self, attrs, costs, max_levels) -> Dict[str, int]:
        """True random walk attribute allocation - simulate human point-by-point clicking."""
        import random
        result = {a: 0 for a in attrs}
        remaining = self.attribute_points
        
        deps = getattr(self.hunter_class, 'attribute_dependencies', {})
        exclusions = getattr(self.hunter_class, 'attribute_exclusions', [])
        
        max_iterations = 10000
        iteration = 0
        stuck_count = 0
        while remaining > 0 and iteration < max_iterations:
            iteration += 1
            valid_attrs = []
            for attr in attrs:
                cost = costs[attr]
                if cost > remaining:
                    continue
                if max_levels[attr] == float('inf'):
                    pass
                else:
                    max_lvl = int(max_levels[attr])
                    if result[attr] >= max_lvl:
                        continue
                if attr in deps:
                    can_use = all(result.get(req_attr, 0) >= req_level 
                                 for req_attr, req_level in deps[attr].items())
                    if not can_use:
                        continue
                if not self._can_unlock_attribute(attr, result, costs):
                    continue
                excluded = False
                for excl_pair in exclusions:
                    if attr in excl_pair:
                        other = excl_pair[0] if excl_pair[1] == attr else excl_pair[1]
                        if result.get(other, 0) > 0:
                            excluded = True
                            break
                if excluded:
                    continue
                valid_attrs.append(attr)
            
            if not valid_attrs:
                stuck_count += 1
                if stuck_count >= 3:
                    unlimited_attrs = [a for a in attrs if costs[a] <= remaining]
                    while remaining > 0 and unlimited_attrs:
                        chosen = random.choice(unlimited_attrs)
                        result[chosen] += 1
                        remaining -= costs[chosen]
                    break
            else:
                stuck_count = 0
            
            if valid_attrs:
                chosen = random.choice(valid_attrs)
                result[chosen] += 1
                remaining -= costs[chosen]
        
        total_spent = sum(result[attr] * costs[attr] for attr in result)
        if total_spent > self.attribute_points:
            return {a: 0 for a in attrs}
        
        return result


# Import simulation worker for isolated process execution
from sim_worker import SimulationWorker


# Path to IRL Builds folder
IRL_BUILDS_PATH = Path(__file__).parent / "IRL Builds"

# Path to global bonuses config file
GLOBAL_BONUSES_FILE = IRL_BUILDS_PATH / "global_bonuses.json"

# Path to assets folder (in same directory as this file)
ASSETS_PATH = Path(__file__).parent / "assets"

# Hunter color themes and portraits
HUNTER_COLORS = {
    "Borge": {
        "primary": "#DC3545",      # Red
        "light": "#F8D7DA",        # Light red/pink
        "dark": "#721C24",         # Dark red
        "text": "#FFFFFF",         # White text on dark
        "bg": "#FFF5F5",           # Very light red background
        "portrait": "borge.png",
    },
    "Knox": {
        "primary": "#0D6EFD",      # Blue
        "light": "#CFE2FF",        # Light blue
        "dark": "#084298",         # Dark blue
        "text": "#FFFFFF",         # White text on dark
        "bg": "#F0F7FF",           # Very light blue background
        "portrait": "knox.png",
    },
    "Ozzy": {
        "primary": "#198754",      # Green
        "light": "#D1E7DD",        # Light green
        "dark": "#0F5132",         # Dark green
        "text": "#FFFFFF",         # White text on dark
        "bg": "#F0FFF4",           # Very light green background
        "portrait": "ozzy.png",
    },
}


# ============================================================================
# UPGRADE COST CALCULATION FUNCTIONS
# ============================================================================
# Ported from the WASM JavaScript implementation
# Calculates resource costs for upgrading each stat at a given level

def calculate_upgrade_cost(stat: str, level: int, hunter: str) -> int:
    """
    Calculate the resource cost to upgrade a stat from (level-1) to level.
    
    Args:
        stat: The stat key (hp, power, regen, damage_reduction, evade_chance, 
              effect_chance, special_chance, special_damage, speed)
        level: The target level (we're calculating cost to reach this level)
        hunter: Hunter name (Borge, Ozzy, Knox)
    
    Returns:
        The resource cost for this upgrade
    """
    if level <= 0:
        return 0
    
    n = level - 1  # Formula uses 0-based index
    hunter_lower = hunter.lower()
    
    # Map our stat names to WASM stat names
    stat_map = {
        "hp": "hp",
        "power": "atk",
        "regen": "regen",
        "damage_reduction": "dr",
        "evade_chance": "evade",
        "block_chance": "block",  # Knox uses block instead of evade
        "effect_chance": "effect",
        "special_chance": "critchance",  # Maps to crit/multi/charge
        "special_damage": "critpower",   # Maps to critpower/multipower/chargeGain
        "speed": "atkspeed",
        # Knox-specific
        "charge_chance": "charge",
        "charge_gained": "chargeGain",
        "reload_time": "reload",
        "projectiles_per_salvo": "proj",
    }
    
    wasm_stat = stat_map.get(stat, stat)
    
    # HP cost formula
    if wasm_stat == "hp":
        if hunter_lower == "knox":
            t = min(n, 110)
            return math.ceil(1 * pow(1.054 + 0.00027 * t, n))
        elif hunter_lower == "ozzy":
            e = min(n, 130)
            return math.ceil(2 * pow(1.061 + 0.000285 * e, n))
        else:  # Borge
            r = min(n, 130)
            return math.ceil(pow(1.061 + 0.00028 * r, n))
    
    # ATK/Power cost formula
    elif wasm_stat == "atk":
        if hunter_lower == "knox":
            t = min(n, 100)
            return math.ceil(2 * pow(1.068 + 0.00027 * t, n))
        elif hunter_lower == "ozzy":
            e = min(n, 120)
            return math.ceil(3 * pow(1.076 + 0.000285 * e, n))
        else:  # Borge
            r = min(n, 120)
            return math.ceil(3 * pow(1.082 + 0.00028 * r, n))
    
    # Regen cost formula
    elif wasm_stat == "regen":
        if hunter_lower == "knox":
            t = min(n, 70)
            return math.ceil(4 * pow(1.09 + 0.00027 * t, n))
        elif hunter_lower == "ozzy":
            e = min(n, 80)
            return math.ceil(5 * pow(1.11 + 0.000285 * e, n))
        else:  # Borge
            r = min(n, 65)
            return math.ceil(6 * pow(1.143 + 0.000278 * r, n))
    
    # DR cost formula (expensive!)
    elif wasm_stat == "dr":
        if hunter_lower == "knox":
            base = math.ceil(2 * pow(0.008 * n + 1.12, n))
            mult = (pow(1.2, max(n - 9, 0)) * pow(1.5, max(n - 19, 0)) * 
                    pow(2, max(n - 29, 0)) * pow(3, max(n - 34, 0)) * 
                    pow(4, max(n - 39, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(3 * pow(0.024 * n + 1.17, n))
            mult = (pow(3, max(n - 34, 0)) * pow(4, max(n - 35, 0)) * 
                    pow(6, max(n - 36, 0)) * pow(8, max(n - 37, 0)) * 
                    pow(100, max(n - 38, 0)))
            return math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(5 * pow(0.024 * n + 1.17, n))
            mult = (pow(3, max(n - 34, 0)) * pow(4, max(n - 35, 0)) * 
                    pow(6, max(n - 36, 0)) * pow(8, max(n - 37, 0)) * 
                    pow(100, max(n - 38, 0)))
            return math.ceil(base * mult)
    
    # Evade/Block cost formula
    elif wasm_stat in ("evade", "block"):
        if hunter_lower == "knox":
            base = math.ceil(3 * pow(0.028 * n + 1.18, n))
            mult = (pow(1.2, max(n - 9, 0)) * pow(1.5, max(n - 19, 0)) * 
                    pow(2, max(n - 29, 0)) * pow(3, max(n - 34, 0)) * 
                    pow(4, max(n - 39, 0)) * pow(5, max(n - 44, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(5 * pow(0.028 * n + 1.3, n))
            mult = (pow(2, max(n - 34, 0)) * pow(3, max(n - 35, 0)) * 
                    pow(4, max(n - 36, 0)) * pow(5, max(n - 37, 0)) * 
                    pow(10, max(n - 38, 0)))
            return math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(pow(0.015 * n + 1.23, n))
            mult = (pow(1.5, max(n - 39, 0)) * pow(2, max(n - 41, 0)) * 
                    pow(2.5, max(n - 43, 0)) * pow(3, max(n - 45, 0)) * 
                    pow(10, max(n - 47, 0)))
            return 10 * math.ceil(base * mult)
    
    # Effect chance cost formula
    elif wasm_stat == "effect":
        if hunter_lower == "knox":
            base = math.ceil(50 * pow(0.018 * n + 1.2, n))
            mult = (pow(1.2, max(n - 9, 0)) * pow(1.5, max(n - 19, 0)) * 
                    pow(2, max(n - 29, 0)) * pow(3, max(n - 34, 0)) * 
                    pow(4, max(n - 39, 0)) * pow(5, max(n - 44, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(7 * pow(0.018 * n + 1.22, n))
            mult = (pow(1.5, max(n - 39, 0)) * pow(2, max(n - 41, 0)) * 
                    pow(2.5, max(n - 43, 0)) * pow(3, max(n - 45, 0)) * 
                    pow(10, max(n - 47, 0)))
            return math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(3 * pow(0.0095 * n + 1.32, n))
            mult = (pow(1.5, max(n - 39, 0)) * pow(2, max(n - 41, 0)) * 
                    pow(2.5, max(n - 43, 0)) * pow(3, max(n - 45, 0)) * 
                    pow(10, max(n - 47, 0)))
            return 10 * math.ceil(base * mult)
    
    # Crit/Multi/Charge chance cost formula
    elif wasm_stat in ("critchance", "multichance", "charge"):
        if hunter_lower == "knox":
            base = math.ceil(1 * pow(0.016 * n + 1.18, n))
            mult = (pow(1.05, max(n - 9, 0)) * pow(1.05, max(n - 19, 0)) * 
                    pow(1.2, max(n - 29, 0)) * pow(1.3, max(n - 39, 0)) * 
                    pow(1.4, max(n - 49, 0)) * pow(1.5, max(n - 59, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(1 * pow(0.016 * n + 1.18, n))
            mult = (pow(1.05, max(n - 59, 0)) * pow(1.2, max(n - 69, 0)) * 
                    pow(1.3, max(n - 79, 0)) * pow(1.4, max(n - 89, 0)))
            return 10 * math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(5 * pow(0.004 * n + 1.19, n))
            mult = (pow(1.05, max(n - 59, 0)) * pow(1.2, max(n - 69, 0)) * 
                    pow(1.3, max(n - 79, 0)) * pow(1.4, max(n - 89, 0)))
            return math.ceil(base * mult)
    
    # Crit/Multi power or Charge gained cost formula
    elif wasm_stat in ("critpower", "multipower", "chargeGain"):
        if hunter_lower == "knox":
            base = math.ceil(1 * pow(0.025 * n + 1.35, n))
            mult = (pow(1.05, max(n - 9, 0)) * pow(1.05, max(n - 19, 0)) * 
                    pow(1.2, max(n - 29, 0)) * pow(1.3, max(n - 39, 0)) * 
                    pow(1.4, max(n - 49, 0)) * pow(1.5, max(n - 59, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(1.1 * pow(0.025 * n + 1.4, n))
            mult = (pow(1.1, max(n - 59, 0)) * pow(1.2, max(n - 69, 0)) * 
                    pow(1.3, max(n - 79, 0)) * pow(1.4, max(n - 89, 0)))
            return 10 * math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(1 * pow(0.025 * n + 1.35, n))
            mult = (pow(1.05, max(n - 59, 0)) * pow(1.2, max(n - 69, 0)) * 
                    pow(1.3, max(n - 79, 0)) * pow(1.4, max(n - 89, 0)))
            return math.ceil(base * mult)
    
    # ATK Speed / Reload cost formula
    elif wasm_stat in ("atkspeed", "reload"):
        if hunter_lower == "knox":
            base = math.ceil(2 * pow(0.035 * n + 1.24, n))
            mult = (pow(1.02, max(n - 9, 0)) * pow(1.05, max(n - 19, 0)) * 
                    pow(1.2, max(n - 29, 0)) * pow(1.3, max(n - 39, 0)) * 
                    pow(1.4, max(n - 49, 0)) * pow(1.5, max(n - 59, 0)) * 
                    pow(1.6, max(n - 69, 0)) * pow(1.7, max(n - 79, 0)) * 
                    pow(1.8, max(n - 89, 0)))
            return math.ceil(0.9 * base * mult)
        elif hunter_lower == "ozzy":
            base = math.ceil(1.2 * pow(0.035 * n + 1.24, n))
            mult = (pow(1.06, max(n - 39, 0)) * pow(1.07, max(n - 49, 0)) * 
                    pow(1.08, max(n - 59, 0)) * pow(1.1, max(n - 69, 0)))
            return 10 * math.ceil(base * mult)
        else:  # Borge
            base = math.ceil(pow(0.032 * n + 1.21, n))
            mult = (pow(1.05, max(n - 39, 0)) * pow(1.06, max(n - 49, 0)) * 
                    pow(1.07, max(n - 59, 0)) * pow(1.08, max(n - 69, 0)))
            return math.ceil(base * mult * 10)
    
    # Default fallback
    return 0


def get_stat_resource_type(stat: str, hunter: str) -> str:
    """
    Get the resource type (common/uncommon/rare) for a given stat.
    
    Returns: "common", "uncommon", or "rare"
    """
    # Common stats (Obsidian/Farahyte/Glacium)
    common_stats = ["hp", "power", "regen"]
    
    # Uncommon stats (Behlium/Galvarium/Quartz)
    uncommon_stats = ["damage_reduction", "evade_chance", "block_chance", "effect_chance"]
    
    # Rare stats (Hellish-Biomatter/Vectid/Tesseracts)
    rare_stats = ["special_chance", "special_damage", "speed", 
                  "charge_chance", "charge_gained", "reload_time", "projectiles_per_salvo"]
    
    if stat in common_stats:
        return "common"
    elif stat in uncommon_stats:
        return "uncommon"
    else:
        return "rare"


def format_cost(cost: int) -> str:
    """Format a cost number with K/M/B suffixes for readability."""
    if cost < 1000:
        return str(cost)
    elif cost < 1_000_000:
        return f"{cost / 1000:.1f}K"
    elif cost < 1_000_000_000:
        return f"{cost / 1_000_000:.2f}M"
    elif cost < 1_000_000_000_000:
        return f"{cost / 1_000_000_000:.2f}B"
    else:
        return f"{cost / 1_000_000_000_000:.2f}T"


class HunterTab:
    """Manages a single hunter's tab with sub-tabs for Build, Run, and Results."""
    
    # Dark mode colors (matching MultiHunterGUI)
    DARK_BG = "#1a1a2e"
    DARK_BG_SECONDARY = "#16213e"
    DARK_TEXT = "#e0e0e0"
    
    def __init__(self, parent_notebook: ttk.Notebook, hunter_name: str, hunter_class, app: 'MultiHunterGUI'):
        self.hunter_name = hunter_name
        self.hunter_class = hunter_class
        self.app = app
        self.colors = HUNTER_COLORS[hunter_name]
        self.parent_notebook = parent_notebook
        
        # Track if tab content has been initialized (lazy loading)
        self._content_initialized = False
        
        # Create the main frame for this hunter
        self.frame = ttk.Frame(parent_notebook)
        parent_notebook.add(self.frame, text=f"  {hunter_name}  ")
        
        # Bind to visibility event for lazy loading
        self.frame.bind('<Visibility>', self._on_tab_visible)
        
        # Load portrait image
        self.portrait_image = None
        self.portrait_photo = None
        self._load_portrait()
        
        # State
        self.level = tk.IntVar(value=1)
        self.results: List[BuildResult] = []
        self.result_queue = queue.Queue()
        self.is_running = False
        self.stop_event = threading.Event()
        self.optimization_start_time = 0
        
        # Simulation worker - created lazily when optimization starts
        self.sim_worker = None
        self.pending_simulations = {}  # task_id -> metadata
        
        # Best tracking
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_gen = 0
        self.best_avg_gen = 0
        
        # Input references
        self.stat_entries: Dict[str, tk.Entry] = {}
        self.talent_entries: Dict[str, tk.Entry] = {}
        self.attribute_entries: Dict[str, tk.Entry] = {}
        self.inscryption_entries: Dict[str, tk.Entry] = {}
        self.relic_entries: Dict[str, tk.Entry] = {}
        self.gem_entries: Dict[str, tk.Entry] = {}
        self.mod_vars: Dict[str, tk.BooleanVar] = {}
        self.gadget_entries: Dict[str, tk.Entry] = {}
        self.bonus_entries: Dict[str, tk.Entry] = {}
        self.bonus_vars: Dict[str, tk.BooleanVar] = {}
        
        # IRL tracking
        self.irl_max_stage = tk.IntVar(value=0)
        self.irl_baseline_result = None  # Stores sim result for user's current build
        
        # Generation history - tracks top 10 builds per tier during progressive evolution
        # Each entry: {'tier_pct': float, 'tier_idx': int, 'talent_pts': int, 'attr_pts': int, 'builds': list}
        self.generation_history: List[Dict] = []
        self.generation_tabs: Dict[int, scrolledtext.ScrolledText] = {}
        
        # Pre-initialize variables that may be accessed before lazy loading
        self.num_sims = tk.IntVar(value=100)
        self.builds_per_tier = tk.IntVar(value=10)
        self.use_rust = tk.BooleanVar(value=RUST_AVAILABLE)
        self.use_progressive = tk.BooleanVar(value=True)
        
        # Placeholder references for lazy-created widgets
        self.container = None
        self.sub_notebook = None
        self.build_frame = None
        self.run_frame = None
        self.advisor_frame = None
        self.results_frame = None
        self.generations_frame = None
        self.log_text = None  # Will be created in lazy loading
        
        # Show loading placeholder
        self._loading_label = ttk.Label(self.frame, text=f"Loading {hunter_name}...", 
                                        font=('Arial', 14))
        self._loading_label.pack(expand=True)
    
    def _on_tab_visible(self, event=None):
        """Lazy-load tab content when first visible."""
        if self._content_initialized:
            return
        self._content_initialized = True
        
        # Remove loading placeholder
        if hasattr(self, '_loading_label') and self._loading_label:
            self._loading_label.destroy()
            self._loading_label = None
        
        # Now create the actual content
        self._initialize_content()
    
    def _initialize_content(self):
        """Create all the tab content (called lazily on first view)."""
        # Create container for portrait + content
        self.container = ttk.Frame(self.frame)
        self.container.pack(fill=tk.BOTH, expand=True)
        
        # Portrait panel on left (if PIL available)
        if PIL_AVAILABLE and self.portrait_photo:
            portrait_frame = tk.Frame(self.container, bg=self.colors["primary"], width=238)  # 15% thinner (280 * 0.85)
            portrait_frame.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=0)
            portrait_frame.pack_propagate(False)
            
            # Hunter name at top
            name_label = tk.Label(portrait_frame, text=self.hunter_name.upper(), 
                                  font=('Arial', 18, 'bold'), fg=self.colors["text"], 
                                  bg=self.colors["primary"])
            name_label.pack(pady=(15, 10))
            
            # Portrait image - centered and larger
            portrait_label = tk.Label(portrait_frame, image=self.portrait_photo, 
                                      bg=self.colors["primary"])
            portrait_label.pack(pady=10, padx=15, expand=True)
        
        # Create sub-notebook
        self.sub_notebook = ttk.Notebook(self.container)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create sub-tabs
        self.build_frame = ttk.Frame(self.sub_notebook)
        self.run_frame = ttk.Frame(self.sub_notebook)
        self.advisor_frame = ttk.Frame(self.sub_notebook)
        self.results_frame = ttk.Frame(self.sub_notebook)
        self.generations_frame = ttk.Frame(self.sub_notebook)
        
        self.sub_notebook.add(self.build_frame, text="📝 Build")
        self.sub_notebook.add(self.run_frame, text="🚀 Run")
        self.sub_notebook.add(self.advisor_frame, text="🎯 Advisor")
        self.sub_notebook.add(self.results_frame, text="🏆 Best")
        self.sub_notebook.add(self.generations_frame, text="📊 Generations")
        
        self._create_build_tab()
        self._create_run_tab()
        self._create_advisor_tab()
        self._create_results_tab()
        self._create_generations_tab()
        
        # Try to auto-load IRL build
        self._auto_load_build()
    
    def _get_build_file_path(self) -> Path:
        """Get the path to this hunter's IRL build file."""
        return IRL_BUILDS_PATH / f"my_{self.hunter_name.lower()}_build.json"
    
    def _load_portrait(self):
        """Load the hunter's portrait image."""
        if not PIL_AVAILABLE:
            return
        
        portrait_file = ASSETS_PATH / self.colors["portrait"]
        if portrait_file.exists():
            try:
                img = Image.open(portrait_file)
                # Resize to fit smaller sidebar (220px with padding) - 15% smaller
                # Images are horizontal format (717x362), so scale by width
                target_width = 220
                ratio = target_width / img.width
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                self.portrait_image = img
                self.portrait_photo = ImageTk.PhotoImage(img)
            except Exception as e:
                print(f"Failed to load portrait for {self.hunter_name}: {e}")
    
    def _format_attribute_label(self, attr_key: str) -> str:
        """Format attribute key to readable label with smart abbreviations."""
        # Custom abbreviations for long attribute names
        abbreviations = {
            # Ozzy blessings
            "blessings_of_the_cat": "Bless. Cat",
            "blessings_of_the_scarab": "Bless. Scarab",
            "blessings_of_the_sisters": "Bless. Sisters",
            # Borge souls
            "soul_of_athena": "Soul Athena",
            "soul_of_hermes": "Soul Hermes", 
            "soul_of_the_minotaur": "Soul Minotaur",
            "soul_of_ares": "Soul Ares",
            "soul_of_snek": "Soul Snek",
            # Long Ozzy attributes
            "extermination_protocol": "Extermn. Protocol",
            "living_off_the_land": "Living Off Land",
            "shimmering_scorpion": "Shimmer Scorpion",
            # Long Knox attributes
            "a_pirates_life_for_knox": "Pirate Life",
            "dead_men_tell_no_tales": "Dead Men Tales",
            "release_the_kraken": "Release Kraken",
            "space_pirate_armory": "Pirate Armory",
            "serious_efficiency": "Serious Effic.",
            "fortification_elixir": "Fort. Elixir",
            "passive_charge_tank": "Passive Charge",
            "shield_of_poseidon": "Shield Poseidon",
            "soul_amplification": "Soul Amplify",
            # Long Borge attributes
            "helltouch_barrier": "Helltouch Barrier",
            "lifedrain_inhalers": "Lifedrain Inhalers",
            "explosive_punches": "Explo. Punches",
            "superior_sensors": "Superior Sensors",
            "essence_of_ylith": "Ess. Ylith",
            "weakspot_analysis": "Weakspot Analy.",
        }
        
        if attr_key in abbreviations:
            return abbreviations[attr_key]
        
        # Default formatting
        label = attr_key.replace("_", " ").title()
        if len(label) > 18:
            label = label[:17] + "…"
        return label
    
    def _get_hunter_costs(self) -> Dict:
        """Get the costs dictionary for the current hunter."""
        if self.hunter_name == "Borge":
            return Borge.costs
        elif self.hunter_name == "Ozzy":
            return Ozzy.costs
        elif self.hunter_name == "Knox":
            return Knox.costs
        return {}
    
    def _auto_load_build(self):
        """Automatically load the IRL build if it exists."""
        build_file = self._get_build_file_path()
        if build_file.exists():
            try:
                with open(build_file, 'r') as f:
                    config = json.load(f)
                self._load_config(config)
                self.app._log(f"✅ Auto-loaded {self.hunter_name} build from {build_file.name}")
            except Exception as e:
                self.app._log(f"⚠️ Failed to load {self.hunter_name} build: {e}")
    
    def _auto_save_build(self):
        """Automatically save the current build to IRL Builds folder."""
        build_file = self._get_build_file_path()
        try:
            config = self._get_save_config()
            IRL_BUILDS_PATH.mkdir(exist_ok=True)
            with open(build_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Auto-save failed for {self.hunter_name}: {e}")
    
    def _get_save_config(self) -> Dict:
        """Get the current configuration in save format."""
        config = {
            "hunter": self.hunter_name,
            "level": self.level.get(),
            "irl_max_stage": self.irl_max_stage.get(),
            "stats": {},
            "talents": {},
            "attributes": {},
            "inscryptions": {},
            "relics": {},
            "gems": {},
            "mods": {},
            "gadgets": {},
            "bonuses": {}
        }
        
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
        
        for key, entry in self.talent_entries.items():
            try:
                config["talents"][key] = int(entry.get())
            except ValueError:
                config["talents"][key] = 0
        
        for key, entry in self.attribute_entries.items():
            try:
                config["attributes"][key] = int(entry.get())
            except ValueError:
                config["attributes"][key] = 0
                
        for key, entry in self.inscryption_entries.items():
            try:
                config["inscryptions"][key] = int(entry.get())
            except ValueError:
                config["inscryptions"][key] = 0
                
        for key, entry in self.relic_entries.items():
            try:
                config["relics"][key] = int(entry.get())
            except ValueError:
                config["relics"][key] = 0
                
        for key, entry in self.gem_entries.items():
            try:
                config["gems"][key] = int(entry.get())
            except ValueError:
                config["gems"][key] = 0
                
        for key, var in self.mod_vars.items():
            config["mods"][key] = var.get()
        
        # Gadgets
        for key, entry in self.gadget_entries.items():
            try:
                config["gadgets"][key] = int(entry.get())
            except ValueError:
                config["gadgets"][key] = 0
        
        # Bonuses are saved globally, not per-hunter
        # Just save empty bonuses dict for backward compatibility
        config["bonuses"] = {}
        
        return config
    
    def _load_config(self, config: Dict):
        """Load a configuration into the UI."""
        if config.get("level"):
            self.level.set(config["level"])
        
        if config.get("irl_max_stage"):
            self.irl_max_stage.set(config["irl_max_stage"])
        
        for key, value in config.get("stats", {}).items():
            if key in self.stat_entries:
                self.stat_entries[key].delete(0, tk.END)
                self.stat_entries[key].insert(0, str(value))
        
        for key, value in config.get("talents", {}).items():
            if key in self.talent_entries:
                self.talent_entries[key].delete(0, tk.END)
                self.talent_entries[key].insert(0, str(value))
        
        for key, value in config.get("attributes", {}).items():
            if key in self.attribute_entries:
                self.attribute_entries[key].delete(0, tk.END)
                self.attribute_entries[key].insert(0, str(value))
        
        for key, value in config.get("inscryptions", {}).items():
            if key in self.inscryption_entries:
                self.inscryption_entries[key].delete(0, tk.END)
                self.inscryption_entries[key].insert(0, str(value))
        
        for key, value in config.get("relics", {}).items():
            if key in self.relic_entries:
                self.relic_entries[key].delete(0, tk.END)
                self.relic_entries[key].insert(0, str(value))
        
        for key, value in config.get("gems", {}).items():
            if key in self.gem_entries:
                self.gem_entries[key].delete(0, tk.END)
                self.gem_entries[key].insert(0, str(value))
        
        for key, value in config.get("mods", {}).items():
            if key in self.mod_vars:
                self.mod_vars[key].set(bool(value))
        
        # Gadgets
        for key, value in config.get("gadgets", {}).items():
            if key in self.gadget_entries:
                self.gadget_entries[key].delete(0, tk.END)
                self.gadget_entries[key].insert(0, str(value))
        
        # Bonuses
        for key, value in config.get("bonuses", {}).items():
            if key in self.bonus_entries:
                self.bonus_entries[key].delete(0, tk.END)
                self.bonus_entries[key].insert(0, str(value))
            if key in self.bonus_vars:
                self.bonus_vars[key].set(bool(value))
    
    def _create_build_tab(self):
        """Create the build configuration sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.build_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Build Configuration", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Level at top
        top_frame = ttk.Frame(self.build_frame)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(top_frame, text="Level:", font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=5)
        level_spin = ttk.Spinbox(top_frame, textvariable=self.level, from_=1, to=600, width=6)
        level_spin.pack(side=tk.LEFT, padx=5)
        level_spin.bind('<FocusOut>', lambda e: self._auto_save_build())
        level_spin.bind('<<Increment>>', lambda e: self._update_max_points_label())
        level_spin.bind('<<Decrement>>', lambda e: self._update_max_points_label())
        self.level.trace_add('write', lambda *args: self._update_max_points_label())
        
        self.max_points_label = ttk.Label(top_frame, text=f"(Max Talents: {self.level.get()}, Max Attrs: {self.level.get()*3})", 
                  font=('Arial', 9, 'italic'))
        self.max_points_label.pack(side=tk.LEFT, padx=10)
        
        # IRL Max Stage - for tracking real-world performance
        ttk.Separator(top_frame, orient='vertical').pack(side=tk.LEFT, padx=15, fill='y', pady=2)
        ttk.Label(top_frame, text="IRL Max Stage:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)
        irl_stage_spin = ttk.Spinbox(top_frame, textvariable=self.irl_max_stage, from_=0, to=999, width=5)
        irl_stage_spin.pack(side=tk.LEFT, padx=5)
        irl_stage_spin.bind('<FocusOut>', lambda e: self._auto_save_build())
        
        # Manual save button
        ttk.Button(top_frame, text="💾 Save", command=self._manual_save).pack(side=tk.RIGHT, padx=5)
        
        # Content frame (no scrollbar - window is large enough)
        self.scrollable_frame = ttk.Frame(self.build_frame)
        self.scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self._populate_build_fields()
    
    def _update_max_points_label(self):
        """Update the max points label when level changes."""
        level = self.level.get()
        max_talents = level
        max_attrs = level * 3
        self.max_points_label.configure(text=f"(Max Talents: {max_talents}, Max Attrs: {max_attrs})")
    
    def _manual_save(self):
        """Manual save with confirmation."""
        self._auto_save_build()
        messagebox.showinfo("Saved", f"{self.hunter_name} build saved to IRL Builds folder!")
    
    def _create_section_frame(self, parent, title: str, emoji: str, color: str = None) -> ttk.Frame:
        """Create a colorful section with header banner and content frame."""
        # Container for the whole section
        container = ttk.Frame(parent)
        
        # Colorful header banner
        if color is None:
            color = self.colors["primary"]
        
        header = tk.Frame(container, bg=color, height=28)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{emoji} {title}", 
                                font=('Arial', 10, 'bold'), fg="white", bg=color)
        header_label.pack(side=tk.LEFT, padx=10)
        
        # Content frame - use tk.Frame with dark background for proper dark mode
        content = tk.Frame(container, bg=self.DARK_BG)
        content.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
        
        return container, content
    
    def _dark_label(self, parent, text: str, fg: str = None, **kwargs) -> tk.Label:
        """Create a tk.Label with dark mode colors."""
        if fg is None:
            fg = self.DARK_TEXT
        return tk.Label(parent, text=text, fg=fg, bg=self.DARK_BG, **kwargs)
    
    def _get_stat_color(self, stat_key: str) -> str:
        """Get the color for a stat based on its resource type."""
        resource_type = get_stat_resource_type(stat_key, self.hunter_name)
        colors = {
            "common": "#22c55e",    # Green (Obsidian)
            "uncommon": "#3b82f6",  # Blue (Behlium)  
            "rare": "#f97316",      # Orange (Hellish-Biomatter)
        }
        return colors.get(resource_type, "#888888")
    
    def _populate_build_fields(self):
        """Populate the build configuration fields in a 2-column layout."""
        dummy = self.hunter_class.load_dummy()
        
        # Configure 2-column layout for scrollable_frame
        self.scrollable_frame.columnconfigure(0, weight=1)
        self.scrollable_frame.columnconfigure(1, weight=1)
        
        # === LEFT COLUMN (column 0) ===
        left_row = 0
        
        # Stats Section (LEFT) - with colorful header
        stats_container, stats_frame = self._create_section_frame(
            self.scrollable_frame, "Main Stats (Upgrade LEVELS)", "📊"
        )
        stats_container.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        if self.hunter_name == "Knox":
            stat_names = {
                "hp": "HP", "power": "Power", "regen": "Regen",
                "damage_reduction": "DR", "block_chance": "Block",
                "effect_chance": "Effect", "charge_chance": "Charge",
                "charge_gained": "Charge Gain", "reload_time": "Reload",
                "projectiles_per_salvo": "Proj. Upgrades"
            }
        else:
            stat_names = {
                "hp": "HP", "power": "Power", "regen": "Regen",
                "damage_reduction": "DR", "evade_chance": "Evade",
                "effect_chance": "Effect", "special_chance": "Special",
                "special_damage": "Spec Dmg", "speed": "Speed"
            }
        
        for i, (stat_key, stat_label) in enumerate(stat_names.items()):
            r, c = divmod(i, 3)  # 3 columns for stats
            frame = tk.Frame(stats_frame, bg=self.DARK_BG)
            frame.grid(row=r, column=c, padx=4, pady=2, sticky="w")
            # Colored label based on resource type
            stat_color = self._get_stat_color(stat_key)
            label = tk.Label(frame, text=f"{stat_label}:", width=12, anchor="w",
                           fg=stat_color, bg=self.DARK_BG, font=('Arial', 9, 'bold'))
            label.pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=5)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Add max level indicator for projectiles (max 5 upgrades)
            if stat_key == "projectiles_per_salvo":
                tk.Label(frame, text="/5", width=3, fg="#b0b0b0", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            self.stat_entries[stat_key] = entry
        
        # Add resource type legend with actual resource names
        res_common, res_uncommon, res_rare = self._get_resource_names()
        legend_frame = tk.Frame(stats_frame, bg=self.DARK_BG)
        legend_frame.grid(row=10, column=0, columnspan=3, pady=(8, 2), sticky="w")
        tk.Label(legend_frame, text="Resource:", font=('Arial', 8), fg="#888888", bg=self.DARK_BG).pack(side=tk.LEFT, padx=(4, 8))
        tk.Label(legend_frame, text=f"● {res_common}", font=('Arial', 8, 'bold'), fg="#22c55e", bg=self.DARK_BG).pack(side=tk.LEFT, padx=4)
        tk.Label(legend_frame, text=f"● {res_uncommon}", font=('Arial', 8, 'bold'), fg="#3b82f6", bg=self.DARK_BG).pack(side=tk.LEFT, padx=4)
        tk.Label(legend_frame, text=f"● {res_rare}", font=('Arial', 8, 'bold'), fg="#f97316", bg=self.DARK_BG).pack(side=tk.LEFT, padx=4)
        
        # Talents Section (LEFT) - with colorful header
        talents_container, talents_frame = self._create_section_frame(
            self.scrollable_frame, "Talents", "⭐", "#9333ea"  # Purple
        )
        talents_container.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        # Get max levels from hunter's costs
        hunter_costs = self._get_hunter_costs()
        
        talent_items = list(dummy.get("talents", {}).items())
        num_talent_cols = 2  # 2 columns for better readability
        for i, (talent_key, talent_val) in enumerate(talent_items):
            r, c = divmod(i, num_talent_cols)
            frame = tk.Frame(talents_frame, bg=self.DARK_BG)
            frame.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            label = talent_key.replace("_", " ").title()
            if len(label) > 22:
                label = label[:21] + "…"
            tk.Label(frame, text=f"{label}:", width=22, anchor="w",
                    fg="#a855f7", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Show max level
            max_lvl = hunter_costs.get("talents", {}).get(talent_key, {}).get("max", "?")
            max_text = "∞" if max_lvl == float("inf") else str(max_lvl)
            tk.Label(frame, text=f"/{max_text}", width=4, fg="#b0b0b0", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            self.talent_entries[talent_key] = entry
        
        # Inscryptions Section (LEFT) - with colorful header
        inscr_container, inscr_frame = self._create_section_frame(
            self.scrollable_frame, "Inscryptions", "📜", "#0891b2"  # Cyan
        )
        inscr_container.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        inscr_tooltips = self._get_inscryption_tooltips()
        for i, (inscr_key, inscr_val) in enumerate(dummy.get("inscryptions", {}).items()):
            r, c = divmod(i, 2)  # 2 columns
            frame = tk.Frame(inscr_frame, bg=self.DARK_BG)
            frame.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            tooltip = inscr_tooltips.get(inscr_key, inscr_key.upper())
            tk.Label(frame, text=f"{inscr_key} ({tooltip}):", width=18, anchor="w",
                    fg="#06b6d4", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Max level for inscryptions is 10
            tk.Label(frame, text="/10", width=3, fg="#b0b0b0", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            self.inscryption_entries[inscr_key] = entry
        
        # === RIGHT COLUMN (column 1) ===
        right_row = 0
        
        # Attributes Section (RIGHT) - with colorful header
        attrs_container, attrs_frame = self._create_section_frame(
            self.scrollable_frame, "Attributes", "🔮", "#dc2626"  # Red
        )
        attrs_container.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        attr_items = list(dummy.get("attributes", {}).items())
        num_attr_cols = 2  # 2 columns for better readability
        for i, (attr_key, attr_val) in enumerate(attr_items):
            r, c = divmod(i, num_attr_cols)
            frame = tk.Frame(attrs_frame, bg=self.DARK_BG)
            frame.grid(row=r, column=c, padx=2, pady=2, sticky="w")
            label = self._format_attribute_label(attr_key)
            tk.Label(frame, text=f"{label}:", width=22, anchor="w",
                    fg="#ef4444", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Show max level
            max_lvl = hunter_costs.get("attributes", {}).get(attr_key, {}).get("max", "?")
            max_text = "∞" if max_lvl == float("inf") else str(max_lvl)
            tk.Label(frame, text=f"/{max_text}", width=4, fg="#b0b0b0", bg=self.DARK_BG, font=('Arial', 9)).pack(side=tk.LEFT)
            self.attribute_entries[attr_key] = entry
        
        # Mods Section (RIGHT) - with colorful header
        # Always create Mods section for consistent layout across all hunters
        mods_container, mods_frame = self._create_section_frame(
            self.scrollable_frame, "Mods", "⚙️", "#64748b"  # Slate gray
        )
        mods_container.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        # Only populate if hunter has mods
        if dummy.get("mods"):
            for i, (mod_key, mod_val) in enumerate(dummy.get("mods", {}).items()):
                var = tk.BooleanVar(value=False)
                label = mod_key.replace("_", " ").title()
                cb = ttk.Checkbutton(mods_frame, text=label, variable=var,
                                     command=self._auto_save_build)
                cb.grid(row=i // 2, column=i % 2, padx=10, pady=5, sticky="w")
                self.mod_vars[mod_key] = var
        else:
            # Show a message for hunters without mods
            tk.Label(mods_frame, text="No mods available for this hunter",
                    fg="#888888", bg=self.DARK_BG, font=('Arial', 9, 'italic')).pack(padx=10, pady=10)
        
        # Gadgets Section (RIGHT - after Mods/Attributes) - with colorful header
        gadgets_container, gadgets_frame = self._create_section_frame(
            self.scrollable_frame, "Gadget", "🔧", "#f59e0b"  # Amber
        )
        gadgets_container.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        # Each hunter has exactly one gadget
        hunter_gadgets = {
            "Borge": ("wrench_of_gore", "Wrench of Gore"),
            "Ozzy": ("zaptron_533", "Zaptron 533"),
            "Knox": ("anchor_of_ages", "Anchor of Ages"),
        }
        gadget_key, gadget_label = hunter_gadgets[self.hunter_name]
        frame = tk.Frame(gadgets_frame, bg=self.DARK_BG)
        frame.grid(row=0, column=0, padx=4, pady=2, sticky="w")
        tk.Label(frame, text=f"{gadget_label}:", width=16, anchor="w",
                fg="#fbbf24", bg=self.DARK_BG, font=('Arial', 9, 'bold')).pack(side=tk.LEFT)
        entry = ttk.Entry(frame, width=4)
        entry.insert(0, "0")
        entry.bind('<FocusOut>', lambda e: self._auto_save_build())
        entry.pack(side=tk.LEFT)
        self.gadget_entries[gadget_key] = entry
    
    def _get_inscryption_tooltips(self) -> Dict[str, str]:
        """Get tooltip descriptions for inscryptions."""
        if self.hunter_name == "Borge":
            return {
                "i3": "+HP", "i4": "+Crit", "i11": "+Effect",
                "i13": "+Power", "i14": "+Loot", "i23": "-Speed",
                "i24": "+DR", "i27": "+HP", "i44": "+Loot", "i60": "+All",
            }
        elif self.hunter_name == "Knox":
            return {
                "i_knox_hp": "+HP", "i_knox_power": "+Power",
                "i_knox_block": "+Block", "i_knox_charge": "+Charge",
                "i_knox_reload": "-Reload",
            }
        else:  # Ozzy
            return {
                "i31": "+Effect", "i32": "+Loot", "i33": "+XP",
                "i36": "-Speed", "i37": "+DR", "i40": "+Multi",
            }
    
    def _create_run_tab(self):
        """Create the run optimization sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.run_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Optimization", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.run_frame, text="⚙️ Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=3)
        
        ttk.Label(row1, text="Sims per build:").pack(side=tk.LEFT, padx=5)
        # num_sims is pre-initialized in __init__
        ttk.Spinbox(row1, textvariable=self.num_sims, from_=10, to=1000, width=6).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(row1, text="Builds per tier:").pack(side=tk.LEFT, padx=15)
        # builds_per_tier is pre-initialized in __init__
        ttk.Spinbox(row1, textvariable=self.builds_per_tier, from_=100, to=5000, width=6).pack(side=tk.LEFT, padx=5)
        
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=3)
        
        # use_rust is pre-initialized in __init__
        ttk.Checkbutton(row2, text="🦀 Use Rust Engine", variable=self.use_rust,
                        state=tk.NORMAL if RUST_AVAILABLE else tk.DISABLED).pack(side=tk.LEFT, padx=5)
        
        # use_progressive is pre-initialized in __init__
        ttk.Checkbutton(row2, text="📈 Progressive Evolution", variable=self.use_progressive).pack(side=tk.LEFT, padx=15)
        
        # Buttons
        btn_frame = ttk.Frame(self.run_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = ttk.Button(btn_frame, text="🚀 Start", command=self._start_optimization)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹️ Stop", command=self._stop_optimization, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Progress
        progress_frame = ttk.LabelFrame(self.run_frame, text="📊 Progress")
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=3)
        
        self.status_label = ttk.Label(progress_frame, text="Ready")
        self.status_label.pack(padx=10, pady=3)
        
        # Best tracking
        best_frame = ttk.Frame(progress_frame)
        best_frame.pack(fill=tk.X, padx=10, pady=3)
        
        self.best_max_label = ttk.Label(best_frame, text="🏆 Best Max: --")
        self.best_max_label.pack(side=tk.LEFT, padx=15)
        
        self.best_avg_label = ttk.Label(best_frame, text="📊 Best Avg: --")
        self.best_avg_label.pack(side=tk.LEFT, padx=15)
        
        # Log
        log_frame = ttk.LabelFrame(self.run_frame, text="📋 Log", style='Dark.TLabelframe')
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Dark mode log matching global log style
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=10, state=tk.DISABLED, font=('Consolas', 9),
            bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
            selectbackground='#3d3d5c', selectforeground='#ffffff',
            highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
            relief=tk.FLAT, borderwidth=0
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # Style the internal frame
        self.log_text.configure(background='#1e1e2e')
        # Configure text tags for colorful log output
        self._configure_text_tags(self.log_text)
    
    def _create_advisor_tab(self):
        """Create the Upgrade Advisor sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.advisor_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Upgrade Advisor", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Instructions
        info_frame = ttk.LabelFrame(self.advisor_frame, text="🎯 Which Stat Should I Upgrade?")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        info_text = (
            "Simulates adding +1 to each stat and shows which gives the BEST improvement.\n"
            "Stats are grouped by resource type so you know which upgrade to pick!"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=600).pack(padx=10, pady=5)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.advisor_frame, text="⚙️ Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=3)
        
        ttk.Label(row1, text="Simulations per test:").pack(side=tk.LEFT, padx=5)
        self.advisor_sims = tk.IntVar(value=100)
        ttk.Spinbox(row1, textvariable=self.advisor_sims, from_=10, to=500, width=6).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="(100-200 recommended for accuracy)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=3)
        
        self.advisor_use_best = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="🏆 Use best build from optimizer (if available)", 
                        variable=self.advisor_use_best).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="Otherwise uses Build tab talents/attributes", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Analyze button
        btn_frame = ttk.Frame(self.advisor_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.advisor_btn = ttk.Button(btn_frame, text="🔍 Analyze Best Upgrade", command=self._run_upgrade_advisor)
        self.advisor_btn.pack(side=tk.LEFT, padx=5)
        
        self.advisor_status = ttk.Label(btn_frame, text="")
        self.advisor_status.pack(side=tk.LEFT, padx=10)
        
        # Results
        results_frame = ttk.LabelFrame(self.advisor_frame, text="📈 Upgrade Recommendations")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.advisor_results = scrolledtext.ScrolledText(
            results_frame, height=15, font=('Consolas', 10),
            bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
            selectbackground='#3d3d5c', selectforeground='#ffffff',
            highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
            relief=tk.FLAT, borderwidth=0
        )
        self.advisor_results.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Configure text tags for colorful output
        self._configure_text_tags(self.advisor_results)
    
    def _run_upgrade_advisor(self):
        """Run the upgrade advisor analysis."""
        self.advisor_btn.configure(state=tk.DISABLED)
        self.advisor_status.configure(text="Analyzing...")
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        
        # Run in background thread
        thread = threading.Thread(target=self._analyze_upgrades, daemon=True)
        thread.start()
    
    def _analyze_upgrades(self):
        """Analyze which stat upgrade is best (runs in background)."""
        try:
            # Build base config INCLUDING current talents and attributes
            base_config = self._get_current_config()
            
            # If "use best build" is enabled and we have optimizer results, use the best build
            if self.advisor_use_best.get() and self.results:
                best_result = max(self.results, key=lambda r: r.avg_final_stage)
                base_config["talents"] = best_result.talents
                base_config["attributes"] = best_result.attributes
                self.frame.after(0, lambda: self.advisor_status.configure(
                    text=f"Using best build (avg {best_result.avg_final_stage:.1f} stages)..."))
            
            num_sims = self.advisor_sims.get()
            use_rust = self.app.hunter_tabs[self.hunter_name].use_rust.get() and RUST_AVAILABLE
            
            # First, simulate the baseline
            self.frame.after(0, lambda: self.advisor_status.configure(text="Simulating baseline..."))
            if use_rust:
                baseline = self._simulate_build_rust(base_config, num_sims)
            else:
                baseline = self._simulate_build_sequential(base_config, num_sims)
            
            if not baseline:
                self.frame.after(0, lambda: self._show_advisor_error("Could not simulate baseline build"))
                return
            
            # Get stat keys based on hunter type
            stat_keys = list(self.stat_entries.keys())
            results = []
            
            for i, stat in enumerate(stat_keys):
                self.frame.after(0, lambda s=stat, i=i: self.advisor_status.configure(
                    text=f"Testing +1 {s}... ({i+1}/{len(stat_keys)})"))
                
                test_config = copy.deepcopy(base_config)
                current_level = test_config["stats"].get(stat, 0)
                test_config["stats"][stat] = current_level + 1
                
                if use_rust:
                    result = self._simulate_build_rust(test_config, num_sims)
                else:
                    result = self._simulate_build_sequential(test_config, num_sims)
                    
                if result:
                    # Calculate improvements
                    stage_improvement = result.avg_final_stage - baseline.avg_final_stage
                    loot_improvement = result.avg_loot_per_hour - baseline.avg_loot_per_hour
                    # Use damage taken (negative is better = less damage taken)
                    damage_taken_improvement = result.avg_damage_taken - baseline.avg_damage_taken
                    survival_improvement = (result.survival_rate - baseline.survival_rate) * 100
                    
                    # Create a score (weighted combination)
                    # Note: lower damage taken is better, so we subtract it
                    score = (
                        stage_improvement * 10 +  # Stage is important
                        loot_improvement * 5 +    # Loot matters
                        -damage_taken_improvement / 1000 +  # Less damage taken = better
                        survival_improvement * 2   # Survival is good
                    )
                    
                    # Calculate upgrade cost (cost to go from current_level to current_level + 1)
                    upgrade_cost = calculate_upgrade_cost(stat, current_level + 1, self.hunter_name)
                    resource_type = get_stat_resource_type(stat, self.hunter_name)
                    
                    # Calculate efficiency (score per unit cost)
                    # Higher efficiency = more bang for your buck
                    efficiency = score / max(upgrade_cost, 1) * 1000  # Scale for readability
                    
                    results.append({
                        "stat": stat,
                        "current_level": current_level,
                        "stage_improvement": stage_improvement,
                        "loot_improvement": loot_improvement,
                        "damage_taken_change": damage_taken_improvement,
                        "survival_improvement": survival_improvement,
                        "score": score,
                        "cost": upgrade_cost,
                        "resource_type": resource_type,
                        "efficiency": efficiency,
                        "result": result
                    })
            
            # Sort by score
            results.sort(key=lambda x: x["score"], reverse=True)
            
            # Display results
            self.frame.after(0, lambda: self._display_advisor_results(baseline, results))
            
        except Exception as e:
            import traceback
            self.frame.after(0, lambda: self._show_advisor_error(f"Error: {str(e)}\n{traceback.format_exc()}"))
    
    def _show_advisor_error(self, message: str):
        """Show an error in the advisor results."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        self.advisor_results.insert(tk.END, f"❌ {message}")
        self.advisor_results.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="")
    
    def _get_resource_categories(self) -> Dict[str, List[str]]:
        """Get resource categories for stats based on hunter type."""
        # Common stats for all hunters (HP, Power, Regen)
        common = ["hp", "power", "regen"]
        
        # Uncommon stats (DR, Evade/Block, Effect)
        # Rare stats are hunter-specific
        if self.hunter_name == "Knox":
            uncommon = ["damage_reduction", "block_chance", "effect_chance"]
            rare = ["charge_chance", "charge_gained", "reload_time", "projectiles_per_salvo"]
            # Knox resources: Glacium (common), Quartz (uncommon), Tesseracts (rare)
            return {
                "❄️ Glacium": common,
                "💎 Quartz": uncommon,
                "🔮 Tesseracts": rare,
            }
        elif self.hunter_name == "Ozzy":
            uncommon = ["damage_reduction", "evade_chance", "effect_chance"]
            rare = ["special_chance", "special_damage", "speed"]
            # Ozzy resources: Farahyte Ore (common), Galvarium (uncommon), Vectid Crystals (rare)
            return {
                "⛏️ Farahyte Ore": common,
                "🔩 Galvarium": uncommon,
                "💠 Vectid Crystals": rare,
            }
        else:  # Borge
            uncommon = ["damage_reduction", "evade_chance", "effect_chance"]
            rare = ["special_chance", "special_damage", "speed"]
            # Borge resources: Obsidian (common), Behlium (uncommon), Hellish-Biomatter (rare)
            return {
                "⬛ Obsidian": common,
                "⚫ Behlium": uncommon,
                "🔥 Hellish-Biomatter": rare,
            }
    
    def _get_resource_names(self) -> Tuple[str, str, str]:
        """Get the resource names for this hunter (common, uncommon, rare)."""
        if self.hunter_name == "Knox":
            return ("Glacium", "Quartz", "Tesseracts")
        elif self.hunter_name == "Ozzy":
            return ("Farahyte Ore", "Galvarium", "Vectid Crystals")
        else:  # Borge
            return ("Obsidian", "Behlium", "Hellish-Biomatter")
    
    def _configure_text_tags(self, text_widget):
        """Configure colorful text tags for a text widget."""
        # Headers and dividers
        text_widget.tag_configure("header", foreground="#ffd700", font=('Consolas', 11, 'bold'))  # Gold
        text_widget.tag_configure("subheader", foreground="#87ceeb", font=('Consolas', 10, 'bold'))  # Sky blue
        text_widget.tag_configure("divider", foreground="#555577")  # Muted purple
        
        # Medals and rankings
        text_widget.tag_configure("gold", foreground="#ffd700", font=('Consolas', 10, 'bold'))  # Gold
        text_widget.tag_configure("silver", foreground="#c0c0c0", font=('Consolas', 10, 'bold'))  # Silver
        text_widget.tag_configure("bronze", foreground="#cd7f32", font=('Consolas', 10, 'bold'))  # Bronze
        text_widget.tag_configure("rank", foreground="#888899")  # Other ranks
        
        # Stats and numbers
        text_widget.tag_configure("positive", foreground="#00ff88")  # Bright green - good
        text_widget.tag_configure("negative", foreground="#ff6666")  # Red - bad
        text_widget.tag_configure("neutral", foreground="#aaaacc")  # Light purple - neutral
        text_widget.tag_configure("cost", foreground="#ffaa44")  # Orange - costs
        text_widget.tag_configure("stat_name", foreground="#66ccff")  # Cyan - stat names
        text_widget.tag_configure("level", foreground="#cc99ff")  # Light purple - levels
        
        # Resource types
        text_widget.tag_configure("common", foreground="#88cc88")  # Light green
        text_widget.tag_configure("uncommon", foreground="#8888ff")  # Light blue
        text_widget.tag_configure("rare", foreground="#ff8844")  # Orange
        
        # Section titles
        text_widget.tag_configure("section_common", foreground="#88cc88", font=('Consolas', 10, 'bold'))
        text_widget.tag_configure("section_uncommon", foreground="#8888ff", font=('Consolas', 10, 'bold'))
        text_widget.tag_configure("section_rare", foreground="#ff8844", font=('Consolas', 10, 'bold'))
        
        # Tips and info
        text_widget.tag_configure("tip", foreground="#aaddff", font=('Consolas', 9, 'italic'))
        text_widget.tag_configure("efficiency", foreground="#00ddaa")  # Teal - efficiency callout

    def _display_advisor_results(self, baseline, results):
        """Display the upgrade advisor results with costs, efficiency, and colorful formatting."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        
        text = self.advisor_results
        resource_categories = self._get_resource_categories()
        
        # Group results by resource
        grouped_results = {cat: [] for cat in resource_categories}
        for r in results:
            for category, stats in resource_categories.items():
                if r["stat"] in stats:
                    grouped_results[category].append(r)
                    break
        
        # Get resource names
        res_common, res_uncommon, res_rare = self._get_resource_names()
        
        # ===== HEADER =====
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        text.insert(tk.END, f"🎯 {self.hunter_name.upper()} UPGRADE ADVISOR", "header")
        text.insert(tk.END, " (with Costs)\n", "subheader")
        text.insert(tk.END, "═" * 70 + "\n\n", "divider")
        
        # ===== BASELINE =====
        text.insert(tk.END, "📊 BASELINE PERFORMANCE\n", "subheader")
        text.insert(tk.END, "   Avg Stage: ", "neutral")
        text.insert(tk.END, f"{baseline.avg_final_stage:.1f}\n", "positive")
        
        if baseline.avg_elapsed_time > 0:
            runs_per_day = (3600 / baseline.avg_elapsed_time) * 24
            text.insert(tk.END, "   📦 Loot/Run → Loot/Day:\n", "neutral")
            text.insert(tk.END, f"      {res_common}: ", "common")
            text.insert(tk.END, f"{self._format_number(baseline.avg_loot_common)} → {self._format_number(baseline.avg_loot_common * runs_per_day)}\n", "positive")
            text.insert(tk.END, f"      {res_uncommon}: ", "uncommon")
            text.insert(tk.END, f"{self._format_number(baseline.avg_loot_uncommon)} → {self._format_number(baseline.avg_loot_uncommon * runs_per_day)}\n", "positive")
            text.insert(tk.END, f"      {res_rare}: ", "rare")
            text.insert(tk.END, f"{self._format_number(baseline.avg_loot_rare)} → {self._format_number(baseline.avg_loot_rare * runs_per_day)}\n", "positive")
        else:
            text.insert(tk.END, f"   Loot/Hour: {baseline.avg_loot_per_hour:.2f}\n", "neutral")
        
        text.insert(tk.END, f"   Dmg Dealt: ", "neutral")
        text.insert(tk.END, f"{baseline.avg_damage:,.0f}\n", "positive")
        text.insert(tk.END, f"   Dmg Taken: ", "neutral")
        text.insert(tk.END, f"{baseline.avg_damage_taken:,.0f}\n", "negative")
        text.insert(tk.END, f"   Survival: ", "neutral")
        survival_tag = "positive" if baseline.survival_rate >= 0.9 else "negative" if baseline.survival_rate < 0.5 else "neutral"
        text.insert(tk.END, f"{baseline.survival_rate*100:.1f}%\n\n", survival_tag)
        
        # ===== BEST OVERALL =====
        if results:
            best = results[0]
            text.insert(tk.END, "═" * 70 + "\n", "divider")
            text.insert(tk.END, "✨ BEST OVERALL UPGRADE ", "header")
            text.insert(tk.END, "(highest impact)\n", "neutral")
            text.insert(tk.END, "═" * 70 + "\n", "divider")
            
            stat_name = best["stat"].replace("_", " ").title()
            text.insert(tk.END, "🥇 ", "gold")
            text.insert(tk.END, f"+1 {stat_name}", "stat_name")
            text.insert(tk.END, f" (Lv {best['current_level']} → {best['current_level'] + 1})\n", "level")
            
            # Stats line with colors
            text.insert(tk.END, "   Stage: ", "neutral")
            stage_tag = "positive" if best['stage_improvement'] > 0 else "negative" if best['stage_improvement'] < 0 else "neutral"
            text.insert(tk.END, f"{best['stage_improvement']:+.2f}", stage_tag)
            text.insert(tk.END, "  │  Loot: ", "neutral")
            loot_tag = "positive" if best['loot_improvement'] > 0 else "negative" if best['loot_improvement'] < 0 else "neutral"
            text.insert(tk.END, f"{best['loot_improvement']:+.2f}", loot_tag)
            text.insert(tk.END, "  │  Taken: ", "neutral")
            taken_tag = "positive" if best['damage_taken_change'] < 0 else "negative" if best['damage_taken_change'] > 0 else "neutral"
            text.insert(tk.END, f"{best['damage_taken_change']:+,.0f}\n", taken_tag)
            
            res_name = res_common if best['resource_type'] == 'common' else res_uncommon if best['resource_type'] == 'uncommon' else res_rare
            res_tag = best['resource_type']
            text.insert(tk.END, "   💰 Cost: ", "neutral")
            text.insert(tk.END, f"{format_cost(best['cost'])} ", "cost")
            text.insert(tk.END, f"{res_name}\n\n", res_tag)
        
        # ===== BEST VALUE =====
        if results:
            by_efficiency = sorted(results, key=lambda x: x["efficiency"], reverse=True)
            best_eff = by_efficiency[0]
            
            text.insert(tk.END, "═" * 70 + "\n", "divider")
            text.insert(tk.END, "💎 BEST VALUE UPGRADE ", "header")
            text.insert(tk.END, "(most efficient)\n", "neutral")
            text.insert(tk.END, "═" * 70 + "\n", "divider")
            
            stat_name = best_eff["stat"].replace("_", " ").title()
            res_name = res_common if best_eff['resource_type'] == 'common' else res_uncommon if best_eff['resource_type'] == 'uncommon' else res_rare
            
            text.insert(tk.END, "🏆 ", "gold")
            text.insert(tk.END, f"+1 {stat_name}", "stat_name")
            text.insert(tk.END, f" (Lv {best_eff['current_level']} → {best_eff['current_level'] + 1})\n", "level")
            
            text.insert(tk.END, "   Stage: ", "neutral")
            stage_tag = "positive" if best_eff['stage_improvement'] > 0 else "negative" if best_eff['stage_improvement'] < 0 else "neutral"
            text.insert(tk.END, f"{best_eff['stage_improvement']:+.2f}", stage_tag)
            text.insert(tk.END, "  │  Loot: ", "neutral")
            loot_tag = "positive" if best_eff['loot_improvement'] > 0 else "negative" if best_eff['loot_improvement'] < 0 else "neutral"
            text.insert(tk.END, f"{best_eff['loot_improvement']:+.2f}", loot_tag)
            text.insert(tk.END, "  │  Taken: ", "neutral")
            taken_tag = "positive" if best_eff['damage_taken_change'] < 0 else "negative" if best_eff['damage_taken_change'] > 0 else "neutral"
            text.insert(tk.END, f"{best_eff['damage_taken_change']:+,.0f}\n", taken_tag)
            
            res_tag = best_eff['resource_type']
            text.insert(tk.END, "   💰 Cost: ", "neutral")
            text.insert(tk.END, f"{format_cost(best_eff['cost'])} ", "cost")
            text.insert(tk.END, f"{res_name}\n", res_tag)
            
            # Efficiency comparison
            if best != best_eff:
                effectiveness = (best_eff['score'] / best['score'] * 100) if best['score'] > 0 else 0
                cost_savings = ((best['cost'] - best_eff['cost']) / best['cost'] * 100) if best['cost'] > 0 else 0
                text.insert(tk.END, "   📈 ", "neutral")
                text.insert(tk.END, f"{effectiveness:.0f}% as effective at {cost_savings:.0f}% less cost!\n", "efficiency")
            text.insert(tk.END, "\n")
        
        # ===== BY RESOURCE TYPE =====
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        text.insert(tk.END, "📦 UPGRADES BY RESOURCE TYPE ", "header")
        text.insert(tk.END, "(sorted by efficiency)\n", "neutral")
        text.insert(tk.END, "═" * 70 + "\n\n", "divider")
        
        resource_tags = ["section_common", "section_uncommon", "section_rare"]
        resource_names = [res_common, res_uncommon, res_rare]
        
        for idx, category in enumerate(resource_categories.keys()):
            category_results = grouped_results[category]
            if not category_results:
                continue
            
            section_tag = resource_tags[idx] if idx < len(resource_tags) else "subheader"
            text.insert(tk.END, f"{category.upper()}\n", section_tag)
            text.insert(tk.END, "─" * 70 + "\n", "divider")
            
            category_results.sort(key=lambda x: x["efficiency"], reverse=True)
            
            for i, r in enumerate(category_results, 1):
                stat_name = r["stat"].replace("_", " ").title()
                
                # Medal with color
                if i == 1:
                    text.insert(tk.END, "  🥇 ", "gold")
                elif i == 2:
                    text.insert(tk.END, "  🥈 ", "silver")
                elif i == 3:
                    text.insert(tk.END, "  🥉 ", "bronze")
                else:
                    text.insert(tk.END, f"  {i}. ", "rank")
                
                text.insert(tk.END, f"+1 {stat_name}", "stat_name")
                text.insert(tk.END, f" (Lv {r['current_level']} → {r['current_level'] + 1})\n", "level")
                
                text.insert(tk.END, "     Stage: ", "neutral")
                stage_tag = "positive" if r['stage_improvement'] > 0 else "negative" if r['stage_improvement'] < 0 else "neutral"
                text.insert(tk.END, f"{r['stage_improvement']:+.2f}", stage_tag)
                text.insert(tk.END, "  │  Loot: ", "neutral")
                loot_tag = "positive" if r['loot_improvement'] > 0 else "negative" if r['loot_improvement'] < 0 else "neutral"
                text.insert(tk.END, f"{r['loot_improvement']:+.2f}", loot_tag)
                text.insert(tk.END, "  │  Cost: ", "neutral")
                text.insert(tk.END, f"{format_cost(r['cost'])}\n", "cost")
            
            text.insert(tk.END, "\n")
        
        # ===== TOP 5 EFFICIENCY =====
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        text.insert(tk.END, "⚡ TOP 5 MOST EFFICIENT ", "header")
        text.insert(tk.END, "(across all resources)\n", "neutral")
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        
        by_efficiency = sorted(results, key=lambda x: x["efficiency"], reverse=True)[:5]
        for i, r in enumerate(by_efficiency, 1):
            stat_name = r["stat"].replace("_", " ").title()
            res_name = res_common if r['resource_type'] == 'common' else res_uncommon if r['resource_type'] == 'uncommon' else res_rare
            res_tag = r['resource_type']
            
            if i == 1:
                text.insert(tk.END, "  🥇 ", "gold")
            elif i == 2:
                text.insert(tk.END, "  🥈 ", "silver")
            elif i == 3:
                text.insert(tk.END, "  🥉 ", "bronze")
            else:
                text.insert(tk.END, f"  {i}. ", "rank")
            
            text.insert(tk.END, f"+1 {stat_name}", "stat_name")
            text.insert(tk.END, ": Stage ", "neutral")
            stage_tag = "positive" if r['stage_improvement'] > 0 else "negative" if r['stage_improvement'] < 0 else "neutral"
            text.insert(tk.END, f"{r['stage_improvement']:+.2f}", stage_tag)
            text.insert(tk.END, " for ", "neutral")
            text.insert(tk.END, f"{format_cost(r['cost'])} ", "cost")
            text.insert(tk.END, f"{res_name}\n", res_tag)
        
        # ===== TIP =====
        text.insert(tk.END, "\n")
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        text.insert(tk.END, "💡 TIP: ", "header")
        text.insert(tk.END, "'Best Value' may be cheaper than 'Best Overall'!\n", "tip")
        text.insert(tk.END, "   Consider your available resources when choosing.\n", "tip")
        text.insert(tk.END, "═" * 70 + "\n", "divider")
        
        text.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="Analysis complete!")
    
    def _create_results_tab(self):
        """Create the results sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.results_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Best Builds", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Results notebook for different sort criteria
        self.results_notebook = ttk.Notebook(self.results_frame)
        self.results_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.result_tabs: Dict[str, scrolledtext.ScrolledText] = {}
        categories = [
            ("🏔️ Avg Stage", "stage"),
            ("🏔️ Max Stage", "max_stage"),
            ("💰 Loot", "loot"),
            ("📈 XP", "xp"),
            ("💥 Damage", "damage"),
            ("📊 Compare", "compare"),
        ]
        
        for label, key in categories:
            frame = ttk.Frame(self.results_notebook)
            self.results_notebook.add(frame, text=label)
            
            text = scrolledtext.ScrolledText(
                frame, height=20, font=('Consolas', 9),
                bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
                selectbackground='#3d3d5c', selectforeground='#ffffff',
                highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
                relief=tk.FLAT, borderwidth=0
            )
            text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            
            # Configure full color tags (same as advisor)
            self._configure_text_tags(text)
            # Add IRL build tag specific to results
            text.tag_config("irl", foreground="#FF6B6B", font=('Consolas', 9, 'italic'))  # Red for IRL build
            
            # Add initial placeholder message
            text.insert(tk.END, "🏆 ", "gold")
            text.insert(tk.END, "Run optimization to see best builds!\n\n", "subheader")
            text.insert(tk.END, "Go to the ", "neutral")
            text.insert(tk.END, "Run", "positive")
            text.insert(tk.END, " tab and click ", "neutral")
            text.insert(tk.END, "Start Optimization", "positive")
            text.insert(tk.END, " to find the best builds.\n", "neutral")
            text.configure(state=tk.DISABLED)
            
            self.result_tabs[key] = text
    
    def _create_generations_tab(self):
        """Create the generations sub-tab to show evolution progress."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.generations_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Evolution History", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Generations notebook with a tab per tier
        self.generations_notebook = ttk.Notebook(self.generations_frame)
        self.generations_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Store text widgets for each generation tab
        self.generation_tabs: Dict[int, scrolledtext.ScrolledText] = {}
        
        # Create placeholder tab (will be replaced on optimization)
        placeholder_frame = ttk.Frame(self.generations_notebook)
        self.generations_notebook.add(placeholder_frame, text="📊 Overview")
        
        self.generations_overview_text = scrolledtext.ScrolledText(
            placeholder_frame, height=20, font=('Consolas', 9),
            bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
            selectbackground='#3d3d5c', selectforeground='#ffffff',
            highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
            relief=tk.FLAT, borderwidth=0
        )
        self.generations_overview_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._configure_text_tags(self.generations_overview_text)
        
        # Add placeholder message
        self.generations_overview_text.insert(tk.END, "📊 ", "gold")
        self.generations_overview_text.insert(tk.END, "Generation History\n\n", "subheader")
        self.generations_overview_text.insert(tk.END, "Run optimization to see how builds evolve through generations!\n\n", "neutral")
        self.generations_overview_text.insert(tk.END, "This tab shows the ", "neutral")
        self.generations_overview_text.insert(tk.END, "top 10 builds", "positive")
        self.generations_overview_text.insert(tk.END, " from each tier of progressive evolution:\n\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 1 (5%): Early exploration with minimal points\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 2 (10%): Testing promising patterns\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 3 (20%): Refining successful strategies\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 4 (40%): Converging on optimal builds\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 5 (70%): Near-full builds tested\n", "neutral")
        self.generations_overview_text.insert(tk.END, "  • Tier 6 (100%): Final full-point optimization\n", "neutral")
        self.generations_overview_text.configure(state=tk.DISABLED)
    
    def _clear_generation_tabs(self):
        """Clear all generation tabs for a new optimization run."""
        if not hasattr(self, 'generations_notebook') or not self.generations_notebook:
            return
        
        # Remove all tabs except the first (Overview)
        for tab_id in list(self.generations_notebook.tabs())[1:]:
            self.generations_notebook.forget(tab_id)
        
        # Clear stored tab widgets
        self.generation_tabs.clear()
        
        # Reset overview text
        if hasattr(self, 'generations_overview_text'):
            self.generations_overview_text.configure(state=tk.NORMAL)
            self.generations_overview_text.delete(1.0, tk.END)
            self.generations_overview_text.insert(tk.END, "📊 ", "gold")
            self.generations_overview_text.insert(tk.END, "Evolution in Progress...\n\n", "subheader")
            self.generations_overview_text.insert(tk.END, "Tier tabs will appear as each generation completes.\n", "neutral")
            self.generations_overview_text.configure(state=tk.DISABLED)
    
    def _update_generation_display_subprocess(self, gen_data: Dict):
        """Update the generations tab with data from subprocess results."""
        if not hasattr(self, 'generations_notebook') or not self.generations_notebook:
            return
        
        gen_num = gen_data['generation']
        tier_name = gen_data.get('tier_name', f'{gen_num}')
        talent_pts = gen_data.get('talent_points', 0)
        attr_pts = gen_data.get('attribute_points', 0)
        builds_tested = gen_data.get('builds_tested', 0)
        best_avg = gen_data.get('best_avg_stage', 0)
        best_max = gen_data.get('best_max_stage', 0)
        talents = gen_data.get('best_talents', {})
        attributes = gen_data.get('best_attributes', {})
        
        # Create new tab for this tier
        tab_frame = ttk.Frame(self.generations_notebook)
        tab_name = f"Gen {gen_num} ({tier_name})"
        self.generations_notebook.add(tab_frame, text=tab_name)
        
        # Create scrolled text widget
        text = scrolledtext.ScrolledText(
            tab_frame, height=20, font=('Consolas', 9),
            bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
            selectbackground='#3d3d5c', selectforeground='#ffffff',
            highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
            relief=tk.FLAT, borderwidth=0
        )
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._configure_text_tags(text)
        
        # Store reference
        self.generation_tabs[gen_num] = text
        
        # Populate content
        text.insert(tk.END, f"📊 GENERATION {gen_num}: {tier_name} POINTS\n", "header")
        text.insert(tk.END, "═" * 50 + "\n\n", "divider")
        
        # Tier summary
        text.insert(tk.END, "📋 Summary:\n", "subheader")
        text.insert(tk.END, f"   Talent Points: ", "stat_name")
        text.insert(tk.END, f"{talent_pts}\n", "positive")
        text.insert(tk.END, f"   Attribute Points: ", "stat_name")
        text.insert(tk.END, f"{attr_pts}\n", "positive")
        text.insert(tk.END, f"   Builds Tested: ", "stat_name")
        text.insert(tk.END, f"{builds_tested}\n", "neutral")
        text.insert(tk.END, f"   Best Avg Stage: ", "stat_name")
        text.insert(tk.END, f"{best_avg:.1f}\n", "positive")
        text.insert(tk.END, f"   Best Max Stage: ", "stat_name")
        text.insert(tk.END, f"{best_max}\n\n", "gold")
        
        # Best build talents
        text.insert(tk.END, "🏆 BEST BUILD\n", "header")
        text.insert(tk.END, "─" * 50 + "\n\n", "divider")
        
        text.insert(tk.END, "Talents:\n", "subheader")
        for talent, level in talents.items():
            if level > 0:
                text.insert(tk.END, f"   • {talent}: {level}\n", "neutral")
        
        text.insert(tk.END, "\nAttributes:\n", "subheader")
        for attr, level in attributes.items():
            if level > 0:
                text.insert(tk.END, f"   • {attr}: {level}\n", "neutral")
        
        text.configure(state=tk.DISABLED)
        
        # Select this new tab
        self.generations_notebook.select(tab_frame)
    
    def _update_generation_display(self, gen_data: Dict):
        """Update the generations tab with new tier data."""
        if not hasattr(self, 'generations_notebook') or not self.generations_notebook:
            return
        
        tier_idx = gen_data['tier_idx']
        tier_pct = gen_data['tier_pct']
        talent_pts = gen_data['talent_pts']
        attr_pts = gen_data['attr_pts']
        builds_tested = gen_data['builds_tested']
        best_avg = gen_data['best_avg']
        best_max = gen_data['best_max']
        top_10 = gen_data['top_10']
        
        # Create new tab for this tier
        tab_frame = ttk.Frame(self.generations_notebook)
        tab_name = f"T{tier_idx + 1} ({int(tier_pct * 100)}%)"
        self.generations_notebook.add(tab_frame, text=tab_name)
        
        # Create scrolled text widget
        text = scrolledtext.ScrolledText(
            tab_frame, height=20, font=('Consolas', 9),
            bg='#1e1e2e', fg='#e0e0e0', insertbackground='#e0e0e0',
            selectbackground='#3d3d5c', selectforeground='#ffffff',
            highlightbackground='#2d2d3d', highlightcolor='#3d3d5c',
            relief=tk.FLAT, borderwidth=0
        )
        text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self._configure_text_tags(text)
        
        # Store reference
        self.generation_tabs[tier_idx] = text
        
        # Populate content
        text.insert(tk.END, f"📊 TIER {tier_idx + 1}: {int(tier_pct * 100)}% POINTS\n", "header")
        text.insert(tk.END, "═" * 50 + "\n\n", "divider")
        
        # Tier summary
        text.insert(tk.END, "📋 Tier Summary:\n", "subheader")
        text.insert(tk.END, f"   Talent Points: ", "stat_name")
        text.insert(tk.END, f"{talent_pts}\n", "positive")
        text.insert(tk.END, f"   Attribute Points: ", "stat_name")
        text.insert(tk.END, f"{attr_pts}\n", "positive")
        text.insert(tk.END, f"   Builds Tested: ", "stat_name")
        text.insert(tk.END, f"{builds_tested}\n", "neutral")
        text.insert(tk.END, f"   Best Avg Stage: ", "stat_name")
        text.insert(tk.END, f"{best_avg:.1f}\n", "positive")
        text.insert(tk.END, f"   Best Max Stage: ", "stat_name")
        text.insert(tk.END, f"{best_max}\n\n", "gold")
        
        # Top 10 builds
        text.insert(tk.END, "🏆 TOP 10 BUILDS\n", "header")
        text.insert(tk.END, "─" * 50 + "\n\n", "divider")
        
        for i, build in enumerate(top_10, 1):
            # Medal and ranking
            if i == 1:
                medal = "🥇"
                tag = "gold"
            elif i == 2:
                medal = "🥈"
                tag = "silver"
            elif i == 3:
                medal = "🥉"
                tag = "bronze"
            else:
                medal = f"#{i}"
                tag = "neutral"
            
            avg_stage = build.get('avg_stage', 0)
            max_stage = build.get('max_stage', 0)
            talents = build.get('talents', {})
            attrs = build.get('attributes', {})
            
            text.insert(tk.END, f"{medal} ", tag)
            text.insert(tk.END, f"Stage: {avg_stage:.1f}", "positive")
            text.insert(tk.END, f" (max {max_stage})\n", "neutral")
            
            # Format talents compactly
            talent_str = ", ".join(f"{k}:{v}" for k, v in talents.items() if v > 0)
            if talent_str:
                text.insert(tk.END, "   Talents: ", "stat_name")
                text.insert(tk.END, f"{talent_str}\n", "neutral")
            
            # Format attributes compactly
            attr_str = ", ".join(f"{k}:{v}" for k, v in attrs.items() if v > 0)
            if attr_str:
                text.insert(tk.END, "   Attrs: ", "stat_name")
                text.insert(tk.END, f"{attr_str}\n", "neutral")
            
            text.insert(tk.END, "\n")
        
        text.configure(state=tk.DISABLED)
        
        # Update overview with cumulative summary
        self._update_generation_overview()
    
    def _update_generation_overview(self):
        """Update the generations overview tab with all tier summaries."""
        if not hasattr(self, 'generations_overview_text'):
            return
        
        text = self.generations_overview_text
        text.configure(state=tk.NORMAL)
        text.delete(1.0, tk.END)
        
        text.insert(tk.END, "📊 ", "gold")
        text.insert(tk.END, "Evolution Progress Summary\n", "header")
        text.insert(tk.END, "═" * 50 + "\n\n", "divider")
        
        if not self.generation_history:
            text.insert(tk.END, "No generation data yet.\n", "neutral")
            text.configure(state=tk.DISABLED)
            return
        
        # Show progression through tiers
        for gen_data in self.generation_history:
            tier_idx = gen_data['tier_idx']
            tier_pct = gen_data['tier_pct']
            best_avg = gen_data['best_avg']
            best_max = gen_data['best_max']
            builds_tested = gen_data['builds_tested']
            
            pct_label = f"{int(tier_pct * 100)}%"
            text.insert(tk.END, f"📊 Tier {tier_idx + 1} ({pct_label}): ", "subheader")
            text.insert(tk.END, f"Best {best_avg:.1f}", "positive")
            text.insert(tk.END, f" (max {best_max})", "neutral")
            text.insert(tk.END, f" from {builds_tested} builds\n", "neutral")
        
        # Show evolution trend if we have multiple tiers
        if len(self.generation_history) > 1:
            text.insert(tk.END, "\n")
            text.insert(tk.END, "📈 Evolution Trend:\n", "header")
            first_best = self.generation_history[0]['best_avg']
            last_best = self.generation_history[-1]['best_avg']
            improvement = last_best - first_best
            text.insert(tk.END, f"   Stage improved from {first_best:.1f} → {last_best:.1f} ", "neutral")
            if improvement > 0:
                text.insert(tk.END, f"(+{improvement:.1f})\n", "positive")
            else:
                text.insert(tk.END, f"({improvement:.1f})\n", "negative")
        
        text.configure(state=tk.DISABLED)
    
    def _log(self, message: str):
        """Add a message to the log (thread-safe via queue)."""
        print(f"[_log] {message[:80]}...")  # Debug
        # If using subprocess (no background thread), write directly
        if hasattr(self, 'opt_process'):
            self._log_direct(message)
        else:
            # Otherwise use queue for thread safety
            self.result_queue.put(('log', message, None, None))
    
    def _log_direct(self, message: str):
        """Add a message to the log directly (only call from main thread)."""
        if not self.log_text:  # Not initialized yet
            return
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)  # Always scroll to see latest
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.update_idletasks()  # Force GUI refresh
    
    def _get_current_config(self) -> Dict:
        """Build a config dictionary from current input values."""
        config = self.hunter_class.load_dummy()
        config["meta"]["level"] = self.level.get()
        
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
        
        for key, entry in self.talent_entries.items():
            try:
                config["talents"][key] = int(entry.get())
            except ValueError:
                config["talents"][key] = 0
        
        for key, entry in self.attribute_entries.items():
            try:
                config["attributes"][key] = int(entry.get())
            except ValueError:
                config["attributes"][key] = 0
        
        for key, entry in self.inscryption_entries.items():
            try:
                config["inscryptions"][key] = int(entry.get())
            except ValueError:
                config["inscryptions"][key] = 0
        
        # Relics - read from GLOBAL relics in the app's Control tab (shared across hunters)
        try:
            # Common relics for all hunters
            config["relics"]["disk_of_dawn"] = self.app.global_relic_r4.get()
            config["relics"]["r7"] = self.app.global_relic7.get()
            config["relics"]["manifestation_core_titan"] = self.app.global_relic7.get()
            
            # Hunter-specific relics
            if self.hunter_name == "Borge":
                config["relics"]["long_range_artillery_crawler"] = self.app.global_relic_r16.get()
                config["relics"]["book_of_mephisto"] = self.app.global_relic_r19.get()
            elif self.hunter_name == "Ozzy":
                config["relics"]["bee_gone_companion_drone"] = self.app.global_relic_r17.get()
        except (AttributeError, tk.TclError):
            # Fallback if global relics not yet initialized
            config["relics"]["disk_of_dawn"] = 0
            config["relics"]["r7"] = 0
            config["relics"]["manifestation_core_titan"] = 0
            if self.hunter_name == "Borge":
                config["relics"]["long_range_artillery_crawler"] = 0
                config["relics"]["book_of_mephisto"] = 0
            elif self.hunter_name == "Ozzy":
                config["relics"]["bee_gone_companion_drone"] = 0
        
        # Gems - read from GLOBAL gems in the app's Control tab (shared across hunters)
        try:
            # Common gems for all hunters
            config["gems"]["attraction_gem"] = self.app.global_gem_attraction.get()
            config["gems"]["attraction_catch-up"] = self.app.global_gem_catchup.get()
            config["gems"]["attraction_node_#3"] = self.app.global_gem_attraction_node3.get()
            config["gems"]["innovation_node_#3"] = self.app.global_gem_innovation_node3.get()
            
            # Borge-only gems
            if self.hunter_name == "Borge":
                config["gems"]["creation_node_#1"] = self.app.global_gem_creation_node1.get()
                config["gems"]["creation_node_#2"] = self.app.global_gem_creation_node2.get()
                config["gems"]["creation_node_#3"] = self.app.global_gem_creation_node3.get()
        except (AttributeError, tk.TclError):
            # Fallback if global gems not yet initialized
            config["gems"]["attraction_gem"] = 0
            config["gems"]["attraction_catch-up"] = 0
            config["gems"]["attraction_node_#3"] = 0
            config["gems"]["innovation_node_#3"] = 0
            if self.hunter_name == "Borge":
                config["gems"]["creation_node_#1"] = 0
                config["gems"]["creation_node_#2"] = 0
                config["gems"]["creation_node_#3"] = 0
        
        for key, var in self.mod_vars.items():
            config["mods"][key] = var.get()
        
        # Gadgets
        for key, entry in self.gadget_entries.items():
            try:
                config["gadgets"][key] = int(entry.get())
            except ValueError:
                config["gadgets"][key] = 0
        
        # Bonuses - read from GLOBAL bonuses in the app's Control tab
        try:
            # Core multipliers
            config["bonuses"]["shard_milestone"] = self.app.global_shard_milestone.get()
            config["bonuses"]["research81"] = self.app.global_research81.get()
            config["bonuses"]["diamond_loot"] = self.app.global_diamond_loot.get()
            config["bonuses"]["ultima_multiplier"] = self.app.global_ultima_multiplier.get()
            
            # Loop mods
            config["bonuses"]["scavenger"] = self.app.global_scavenger.get()
            config["bonuses"]["scavenger2"] = self.app.global_scavenger2.get()
            
            # Construction Milestones
            config["bonuses"]["cm46"] = self.app.global_cm46.get()
            config["bonuses"]["cm47"] = self.app.global_cm47.get()
            config["bonuses"]["cm48"] = self.app.global_cm48.get()
            config["bonuses"]["cm51"] = self.app.global_cm51.get()
            
            # Diamond cards
            config["bonuses"]["gaiden_card"] = self.app.global_gaiden_card.get()
            config["bonuses"]["iridian_card"] = self.app.global_iridian_card.get()
            
            # IAP
            config["bonuses"]["iap_travpack"] = self.app.global_iap_travpack.get()
            
            # Gem loot nodes - CRITICAL for loot multiplier!
            config["bonuses"]["attraction_loot_borge"] = self.app.global_gem_loot_borge.get()
            config["bonuses"]["attraction_loot_ozzy"] = self.app.global_gem_loot_ozzy.get()
        except (AttributeError, tk.TclError):
            # Fallback if global bonuses not yet initialized
            config["bonuses"]["shard_milestone"] = 0
            config["bonuses"]["research81"] = 0
            config["bonuses"]["diamond_loot"] = 0
            config["bonuses"]["iap_travpack"] = False
            config["bonuses"]["ultima_multiplier"] = 1.0
            config["bonuses"]["scavenger"] = 0
            config["bonuses"]["scavenger2"] = 0
            config["bonuses"]["cm46"] = False
            config["bonuses"]["cm47"] = False
            config["bonuses"]["cm48"] = False
            config["bonuses"]["cm51"] = False
            config["bonuses"]["gaiden_card"] = False
            config["bonuses"]["iridian_card"] = False
            config["bonuses"]["attraction_loot_borge"] = 0
            config["bonuses"]["attraction_loot_ozzy"] = 0
        
        return config
    
    def _start_optimization(self):
        """Start optimization for this hunter."""
        print(f"[DEBUG] _start_optimization called for {self.hunter_name}")
        print(f"[DEBUG] is_running = {self.is_running}")
        
        if self.is_running:
            print(f"[DEBUG] Already running, returning")
            return
        self.is_running = True
        self.stop_event.clear()
        self.results.clear()
        self.generation_history.clear()  # Clear previous generation data
        self.optimization_start_time = time.time()
        self.progress_var.set(0)
        self.poll_count = 0
        self.last_logged_gen = 0  # Track last generation we logged
        self.last_logged_builds = 0  # Track last build count we logged
        
        # Reset best tracking
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_label.configure(text="🏆 Best Max: --")
        self.best_avg_label.configure(text="📊 Best Avg: --")
        
        # Clear generation tabs
        self._clear_generation_tabs()
        
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        
        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)
        
        # Capture ALL tkinter variables BEFORE starting (tkinter is not thread-safe)
        self._thread_level = self.level.get()
        self._thread_num_sims = self.num_sims.get()
        self._thread_builds_per_tier = self.builds_per_tier.get()
        self._thread_config = self._get_current_config()
        
        # Launch optimization as SEPARATE PROCESS (only way to avoid tkinter interference)
        import subprocess
        import tempfile
        
        print(f"[DEBUG] About to create config file for {self.hunter_name}")
        
        config_file = Path(tempfile.gettempdir()) / f"hunter_opt_{self.hunter_name}.json"
        self.result_file = Path(tempfile.gettempdir()) / f"hunter_opt_{self.hunter_name}_results.json"
        
        print(f"[DEBUG] Config file: {config_file}")
        print(f"[DEBUG] Result file: {self.result_file}")
        
        # Delete old result file if exists
        if self.result_file.exists():
            self.result_file.unlink()
        
        self._log(f"💾 Config: {config_file}")
        self._log(f"💾 Results: {self.result_file}")
        
        # Write config
        try:
            with open(config_file, 'w') as f:
                json.dump({
                    'hunter_name': self.hunter_name,
                    'level': self._thread_level,
                    'base_config': self._thread_config,
                    'num_sims': self._thread_num_sims,
                    'builds_per_tier': self._thread_builds_per_tier,
                    'use_progressive': self.use_progressive.get()
                }, f)
            self._log(f"✅ Config written")
        except Exception as e:
            self._log(f"❌ Failed to write config: {e}")
            self._optimization_complete()
            return
        
        # Launch process with error capture
        python_exe = sys.executable
        
        # Try to use venv Python if available (for rust_sim module)
        venv_python = Path(__file__).parent.parent / '.venv' / 'Scripts' / 'python.exe'
        if venv_python.exists():
            python_exe = str(venv_python)
            self._log(f"🐍 Using venv Python: {python_exe}")
        
        script_path = Path(__file__).parent / 'run_optimization.py'
        
        self._log(f"🐍 Python: {python_exe}")
        self._log(f"📜 Script: {script_path}")
        
        if not script_path.exists():
            self._log(f"❌ Script not found: {script_path}")
            self._optimization_complete()
            return
        
        try:
            # Ensure rust_sim module is available in subprocess by adding project to PYTHONPATH
            import os
            env = os.environ.copy()
            project_root = str(script_path.parent.parent)  # Go from hunter-sim/run_optimization.py up to project root
            hunter_sim_dir = str(script_path.parent)  # The hunter-sim directory itself
            if 'PYTHONPATH' in env:
                env['PYTHONPATH'] = hunter_sim_dir + os.pathsep + project_root + os.pathsep + env['PYTHONPATH']
            else:
                env['PYTHONPATH'] = hunter_sim_dir + os.pathsep + project_root
            
            # Create stderr file to capture errors without blocking
            self.stderr_file = Path(tempfile.gettempdir()) / f"opt_stderr_{os.getpid()}.txt"
            self.stderr_handle = open(self.stderr_file, 'w')
            
            self.opt_process = subprocess.Popen(
                [python_exe, str(script_path), str(config_file), str(self.result_file)],
                stdout=subprocess.DEVNULL,
                stderr=self.stderr_handle,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                env=env
            )
            self._log(f"🚀 Subprocess launched (PID: {self.opt_process.pid})")
            self._log(f"⏳ Generating and simulating {self._thread_builds_per_tier} builds...")
        except Exception as e:
            self._log(f"❌ Failed to launch subprocess: {e}\n{traceback.format_exc()}")
            self._optimization_complete()
            return
        
        # Start polling
        self.poll_count = 0
        self.frame.after(500, self._poll_subprocess)
    
    def _stop_optimization(self):
        """Stop optimization."""
        self.stop_event.set()
        self._log("⏹️ Stopping...")
        # Clear the result queue to prevent stale results
        while not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
            except:
                break
    
    def _run_optimization(self):
        """Run the optimization (background thread).
        
        Uses cached values from _thread_* attributes to avoid tkinter access from thread.
        """
        try:
            # Use cached values (captured before thread started)
            level = self._thread_level
            base_config = self._thread_config
            
            # Count actual points
            talent_count = sum(base_config.get("talents", {}).values())
            attr_count = sum(base_config.get("attributes", {}).values())
            
            self._log(f"🚀 Starting {self.hunter_name} optimization at level {level}")
            self._log(f"   Talents: {talent_count} points, Attributes: {attr_count} points")
            
            # Run baseline simulation on user's current IRL build first
            self._run_irl_baseline(base_config)
            
            if self._thread_use_progressive and level >= 30:
                self._run_progressive_evolution(level, base_config)
            else:
                self._run_sampling_optimization(level, base_config)
                
        except Exception as e:
            import traceback
            self._log(f"\n❌ Error: {str(e)}")
            self._log(traceback.format_exc())
            self.result_queue.put(('error', str(e), None, None))
    
    def _run_irl_baseline(self, base_config: Dict):
        """Run baseline simulation on the user's current IRL build."""
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        use_rust = self._thread_use_rust
        irl_max_stage = self._thread_irl_max_stage
        
        # Check if user has entered talents/attributes (from cached config)
        has_talents = any(v > 0 for v in base_config.get("talents", {}).values())
        has_attrs = any(v > 0 for v in base_config.get("attributes", {}).values())
        
        if not (has_talents or has_attrs):
            self._log("⚠️ No talents/attributes entered - skipping IRL baseline")
            self.irl_baseline_result = None
            return
        
        # Count actual talent and attribute points
        talent_count = sum(base_config.get("talents", {}).values())
        attr_count = sum(base_config.get("attributes", {}).values())
        
        self._log("\n📊 Running IRL Baseline Simulation...")
        config_level = base_config.get("meta", {}).get("level") or base_config.get("level", 0)
        self._log(f"   Your current build @ Level {config_level}")
        self._log(f"   Talents: {talent_count} points, Attributes: {attr_count} points")
        if irl_max_stage > 0:
            self._log(f"   IRL Max Stage: {irl_max_stage}")
        
        # Use cached config (already captured before thread started)
        irl_config = base_config
        
        try:
            if use_rust:
                result = self._simulate_build_rust(irl_config, num_sims)
            else:
                result = self._simulate_build_sequential(irl_config, num_sims)
            
            if result:
                self.irl_baseline_result = result
                self._log(f"   ✅ Sim predicts: Stage {result.avg_final_stage:.1f} (max {result.highest_stage})")
                
                # Compare to IRL if provided
                if irl_max_stage > 0:
                    sim_stage = result.avg_final_stage
                    diff = sim_stage - irl_max_stage
                    if abs(diff) < 5:
                        self._log(f"   🎯 Sim accuracy: EXCELLENT (within 5 stages)")
                    elif diff > 0:
                        self._log(f"   📈 Sim predicts {diff:.1f} stages HIGHER than IRL")
                    else:
                        self._log(f"   📉 Sim predicts {-diff:.1f} stages LOWER than IRL")
            else:
                self._log("   ⚠️ Failed to run baseline simulation")
                self.irl_baseline_result = None
        except Exception as e:
            self._log(f"   ⚠️ Baseline error: {e}")
            self.irl_baseline_result = None
    
    def _run_progressive_evolution(self, level: int, base_config: Dict):
        """Run progressive evolution optimization."""
        import random
        
        # Yield immediately to give GUI a chance
        time.sleep(0.01)
        
        self._log("\n📈 Using Progressive Evolution")
        self.result_queue.put(('log', "   [DEBUG] Progressive evolution started", None, None))
        
        tiers = [0.05, 0.10, 0.20, 0.40, 0.70, 1.0]
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        builds_per_tier = self._thread_builds_per_tier
        use_rust = self._thread_use_rust
        
        print(f"[DEBUG] num_sims={num_sims}, builds_per_tier={builds_per_tier}, use_rust={use_rust}")
        
        total_builds_planned = len(tiers) * builds_per_tier
        self._log(f"   Tiers: {[f'{int(t*100)}%' for t in tiers]}")
        self._log(f"   Builds per tier: {builds_per_tier}")
        
        if use_rust:
            self._log("   🦀 Using Rust engine")
        
        total_tested = 0
        elite_patterns = []
        final_tier_idx = len(tiers) - 1  # Index of the 100% tier
        
        for tier_idx, tier_pct in enumerate(tiers):
            if self.stop_event.is_set():
                self._log('\n⏹️ Stopped by user.')
                break
            
            tier_talent_points = max(1, int(level * tier_pct))
            tier_attr_points = max(3, int(level * 3 * tier_pct))
            tier_level = max(1, int(level * tier_pct))
            is_final_tier = (tier_idx == final_tier_idx)
            
            self._log(f"\n{'='*50}")
            self._log(f"📊 TIER {tier_idx + 1}/{len(tiers)}: {int(tier_pct*100)}%{'  [FINAL]' if is_final_tier else ''}")
            self._log(f"   Talents: {tier_talent_points}, Attrs: {tier_attr_points}")
            if elite_patterns:
                self._log(f"   Building on {len(elite_patterns)} elite patterns from previous tier")
            
            tier_generator = BuildGenerator(self.hunter_class, tier_level)
            tier_generator.talent_points = tier_talent_points
            tier_generator.attribute_points = tier_attr_points
            tier_generator._calculate_dynamic_attr_maxes()
            
            # Yield after generator creation
            time.sleep(0.01)
            
            tier_results = []
            tested_hashes = set()
            consecutive_dupes = 0
            max_consecutive_dupes = 100
            
            # BATCH PROCESSING: Collect builds and simulate in batches for speed
            batch_size = 100  # Process 100 at a time for maximum batch efficiency
            pending_configs = []
            pending_metadata = []
            
            builds_generated = 0
            _loop_start = time.perf_counter()
            _gen_time = 0
            _sim_time = 0
            _deepcopy_time = 0
            _validation_time = 0
            _other_time = 0
            for i in range(builds_per_tier):
                _iter_start = time.perf_counter()
                if self.stop_event.is_set():
                    break
                
                if consecutive_dupes >= max_consecutive_dupes:
                    self._log(f"   ⚡ Tier exhausted after {len(tier_results)} builds")
                    break
                
                _gen_start = time.perf_counter()
                
                # Generate build - always try to extend elite first
                talents, attrs = None, None
                extended_from_elite = False
                
                if elite_patterns and random.random() < 0.8:  # 80% from elites
                    elite = random.choice(elite_patterns)
                    talents, attrs = self._extend_elite_pattern(
                        elite, tier_generator, tier_talent_points, tier_attr_points
                    )
                    extended_from_elite = True
                
                # Fallback to random if no elite or extension returned None
                if talents is None or attrs is None:
                    builds = tier_generator.generate_smart_sample(sample_size=1)
                    if builds:
                        talents, attrs = builds[0]
                    else:
                        consecutive_dupes += 1
                        continue
                
                _gen_time += time.perf_counter() - _gen_start
                
                _val_start = time.perf_counter()
                # Check duplicate
                build_hash = (tuple(sorted(talents.items())), tuple(sorted(attrs.items())))
                if build_hash in tested_hashes:
                    consecutive_dupes += 1
                    continue
                tested_hashes.add(build_hash)
                consecutive_dupes = 0
                
                # Validate points spent - must use at least 95% of available points
                # BUT cap at what's actually spendable (some hunters have limited talent pools)
                talent_spent = sum(talents.values())
                attr_costs_local = {a: tier_generator.costs["attributes"][a]["cost"] for a in attrs}
                attr_spent = sum(attrs[a] * attr_costs_local[a] for a in attrs)
                
                # Calculate max possible talent spend (sum of LIMITED talent maxes only)
                # Unlimited talents (inf) don't count toward the cap
                max_possible_talents = sum(
                    int(tier_generator.costs["talents"][t]["max"]) 
                    for t in tier_generator.costs["talents"]
                    if tier_generator.costs["talents"][t]["max"] != float('inf')
                )
                # With unlimited talents available, we can always spend all points
                if any(tier_generator.costs["talents"][t]["max"] == float('inf') 
                       for t in tier_generator.costs["talents"]):
                    talent_target = tier_talent_points
                else:
                    talent_target = min(tier_talent_points, max_possible_talents)
                
                if talent_spent < talent_target * 0.95 or attr_spent < tier_attr_points * 0.95:
                    consecutive_dupes += 1
                    continue
                
                _validation_time += time.perf_counter() - _val_start
                
                _dc_start = time.perf_counter()
                # Add to batch
                config = copy.deepcopy(base_config)
                config["talents"] = talents
                config["attributes"] = attrs
                _deepcopy_time += time.perf_counter() - _dc_start
                
                pending_configs.append(config)
                pending_metadata.append((talents, attrs))
                builds_generated += 1
                
                # Process batch when full or at end of loop
                should_process = (len(pending_configs) >= batch_size or 
                                 i == builds_per_tier - 1 or 
                                 consecutive_dupes >= max_consecutive_dupes)
                
                if should_process and pending_configs:
                    _sim_start = time.perf_counter()
                    try:
                        if use_rust:
                            # ALWAYS use batch processing for Rust (handles single configs too)
                            batch_results = self._simulate_builds_batch(pending_configs, num_sims)
                        else:
                            # Python fallback - individual simulation
                            batch_results = []
                            for cfg in pending_configs:
                                result = self._simulate_build_sequential(cfg, num_sims)
                                batch_results.append(result)
                        _sim_time += time.perf_counter() - _sim_start
                        
                        # Process results
                        for result, (talents, attrs) in zip(batch_results, pending_metadata):
                            if result:
                                # ONLY save to self.results on final tier (100%)
                                if is_final_tier:
                                    self.results.append(result)
                                tier_results.append({
                                    'avg_stage': result.avg_final_stage,
                                    'max_stage': result.highest_stage,
                                    'talents': talents,
                                    'attributes': attrs,
                                    'result': result  # Store full BuildResult for generation history
                                })
                        
                        total_tested += len(batch_results)
                        
                        # Update progress/logs less frequently to reduce queue overhead
                        if total_tested % 50 == 0 or total_tested == len(batch_results):
                            progress = min(100, (total_tested / total_builds_planned) * 100)
                            self.result_queue.put(('progress', progress, total_tested, total_builds_planned))
                            elapsed = time.time() - self.optimization_start_time
                            rate = total_tested / elapsed if elapsed > 0 else 0
                            self.result_queue.put(('log', f"   ...{builds_generated}/{builds_per_tier} generated, {total_tested} tested ({rate:.1f}/sec)", None, None))
                        
                    except Exception as e:
                        self._log(f"   ⚠️ Batch error: {e}")
                    
                    # Clear batch
                    pending_configs = []
                    pending_metadata = []
            
            # Print timing summary
            _total_time = time.perf_counter() - _loop_start
            print(f"[TIMING] Tier complete: tested={total_tested} in {_total_time:.2f}s")
            print(f"[TIMING]   Generation: {_gen_time:.2f}s ({total_tested/_gen_time if _gen_time > 0 else 0:.0f}/s)")
            print(f"[TIMING]   Validation: {_validation_time:.2f}s")
            print(f"[TIMING]   Deepcopy:   {_deepcopy_time:.2f}s")
            print(f"[TIMING]   Simulation: {_sim_time:.2f}s ({total_tested/_sim_time if _sim_time > 0 else 0:.0f}/s)")
            print(f"[TIMING]   Other:      {_total_time - _gen_time - _validation_time - _deepcopy_time - _sim_time:.2f}s")
            print(f"[TIMING]   OVERALL:    {total_tested/_total_time:.1f}/s")
            
            # Analyze tier results
            if tier_results:
                stages = [r['avg_stage'] for r in tier_results]
                max_stage = max(r['max_stage'] for r in tier_results)
                best_avg = max(stages)
                
                self._log(f"   Best avg: {best_avg:.1f}, max: {max_stage}")
                
                self.result_queue.put(('best_update', {
                    'best_max': max_stage,
                    'best_avg': best_avg,
                    'gen': tier_idx + 1
                }, None, None))
                
                # Select elites - sort by avg first, then max_stage as tiebreaker
                tier_results.sort(key=lambda x: (x['avg_stage'], x['max_stage']), reverse=True)
                elite_count = min(100, max(len(tier_results) // 10, 10))
                elite_patterns = [
                    {'talents': r['talents'], 'attributes': r['attributes']}
                    for r in tier_results[:elite_count]
                ]
                self._log(f"   Promoted {len(elite_patterns)} elites")
                
                # Save top 10 for generation history display
                top_10 = tier_results[:10]
                self.result_queue.put(('generation_data', {
                    'tier_idx': tier_idx,
                    'tier_pct': tier_pct,
                    'talent_pts': tier_talent_points,
                    'attr_pts': tier_attr_points,
                    'builds_tested': len(tier_results),
                    'best_avg': best_avg,
                    'best_max': max_stage,
                    'top_10': top_10
                }, None, None))
        
        # Final summary
        total_time = time.time() - self.optimization_start_time
        rate = total_tested / total_time if total_time > 0 else 0
        
        self._log(f"\n{'='*50}")
        self._log(f"✅ Complete! Tested {total_tested} builds in {total_time:.1f}s ({rate:.1f}/sec)")
        
        # Show % optimal comparison if we have baseline
        if self.irl_baseline_result and self.results:
            best = max(self.results, key=lambda r: r.avg_final_stage)
            irl = self.irl_baseline_result
            if best.avg_final_stage > 0:
                pct_optimal = (irl.avg_final_stage / best.avg_final_stage) * 100
                stage_diff = best.avg_final_stage - irl.avg_final_stage
                self._log(f"\n📊 YOUR BUILD VS OPTIMAL:")
                self._log(f"   Your build: Stage {irl.avg_final_stage:.1f}")
                self._log(f"   Best found: Stage {best.avg_final_stage:.1f}")
                self._log(f"   📈 YOUR BUILD IS {pct_optimal:.1f}% OPTIMAL")
                if stage_diff > 0:
                    self._log(f"   Potential gain: +{stage_diff:.1f} stages")
        
        self.result_queue.put(('done', None, None, None))
    
    def _run_sampling_optimization(self, level: int, base_config: Dict):
        """Run simple sampling optimization for low-level hunters."""
        self._log("\n📊 Using Random Sampling")
        
        # Yield before potentially slow build generation
        time.sleep(0.01)
        
        generator = BuildGenerator(self.hunter_class, level)
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        max_builds = self._thread_builds_per_tier
        use_rust = self._thread_use_rust
        
        builds = generator.generate_smart_sample(max_builds)
        self._log(f"   Generated {len(builds)} builds")
        
        # Yield after build generation
        time.sleep(0.01)
        
        # Process builds one at a time
        batch_size = 1
        
        for batch_idx, batch_start in enumerate(range(0, len(builds), batch_size)):
            if self.stop_event.is_set():
                break
            
            # Yield to main thread periodically
            if batch_idx % 5 == 0:
                time.sleep(0.001)
            
            batch_end = min(batch_start + batch_size, len(builds))
            batch_builds = builds[batch_start:batch_end]
            
            # Create configs for batch
            configs = []
            for talents, attrs in batch_builds:
                config = copy.deepcopy(base_config)
                config["talents"] = talents
                config["attributes"] = attrs
                configs.append(config)
            
            try:
                if use_rust and len(configs) > 1:
                    # Use batch processing
                    batch_results = self._simulate_builds_batch(configs, num_sims)
                else:
                    # Fallback to individual simulation
                    batch_results = []
                    for cfg in configs:
                        if use_rust:
                            result = self._simulate_build_rust(cfg, num_sims)
                        else:
                            result = self._simulate_build_sequential(cfg, num_sims)
                        batch_results.append(result)
                
                # Add results
                for result in batch_results:
                    if result:
                        self.results.append(result)
            except Exception:
                pass
            
            progress = (batch_end / len(builds)) * 100
            self.result_queue.put(('progress', progress, batch_end, len(builds)))
        
        self._log(f"\n✅ Complete! Found {len(self.results)} builds")
        self.result_queue.put(('done', None, None, None))
    
    def _extend_elite_pattern(self, elite: Dict, generator: BuildGenerator,
                              target_talents: int, target_attrs: int) -> Tuple[Dict, Dict]:
        """Extend elite pattern with more points. MUST spend all available points."""
        import random
        
        talents_list = list(generator.costs["talents"].keys())
        attrs_list = list(generator.costs["attributes"].keys())
        
        # Copy elite pattern as starting point
        talents = {t: elite.get('talents', {}).get(t, 0) for t in talents_list}
        attrs = {a: elite.get('attributes', {}).get(a, 0) for a in attrs_list}
        
        # Calculate how much elite already spent
        elite_talent_spent = sum(talents.values())
        attr_costs = {a: generator.costs["attributes"][a]["cost"] for a in attrs_list}
        elite_attr_spent = sum(attrs[a] * attr_costs[a] for a in attrs_list)
        
        # Calculate how much MORE we need to add
        talent_to_add = max(0, target_talents - elite_talent_spent)
        attr_to_add = max(0, target_attrs - elite_attr_spent)
        
        attr_max = {a: generator.costs["attributes"][a]["max"] for a in attrs_list}
        talent_max = {t: generator.costs["talents"][t]["max"] for t in talents_list}
        
        # Find unlimited (infinite) attributes for fallback - these can always absorb points
        unlimited_attrs = [a for a in attrs_list if attr_max[a] == float('inf')]
        # Sort by cost (prefer cheaper ones for efficiency)
        unlimited_attrs.sort(key=lambda a: attr_costs[a])
        
        # Find unlimited talents for fallback (but NOT unknown_talent - that's last resort)
        unlimited_talents = [t for t in talents_list if talent_max[t] == float('inf') and t != 'unknown_talent']
        
        # === ADD TALENT POINTS ===
        attempts = 0
        while talent_to_add > 0 and attempts < 1000:
            attempts += 1
            # Find KNOWN talents that can accept more points (exclude unknown_talent)
            valid = [t for t in talents_list 
                     if t != 'unknown_talent' and (talent_max[t] == float('inf') or talents[t] < int(talent_max[t]))]
            
            # Only use unknown_talent as LAST RESORT when all known talents are maxed
            if not valid:
                if 'unknown_talent' in talents_list:
                    valid = ['unknown_talent']
                else:
                    break
            
            chosen = random.choice(valid)
            talents[chosen] += 1
            talent_to_add -= 1
        
        # === ADD ATTRIBUTE POINTS ===
        deps = getattr(generator.hunter_class, 'attribute_dependencies', {})
        exclusions = getattr(generator.hunter_class, 'attribute_exclusions', [])
        
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
                # Randomly choose from valid options (includes unlimited attrs!)
                chosen = random.choice(valid_attrs)
                attrs[chosen] += 1
                remaining -= attr_costs[chosen]
            elif unlimited_attrs:
                # FALLBACK: Force use unlimited attributes if nothing else works
                spent_any = False
                for sink_attr in unlimited_attrs:
                    if attr_costs[sink_attr] <= remaining:
                        attrs[sink_attr] += 1
                        remaining -= attr_costs[sink_attr]
                        spent_any = True
                        break
                if not spent_any:
                    # Can't spend remaining points (all costs > remaining)
                    break
            else:
                # No valid attrs and no unlimited attrs - shouldn't happen
                break
        
        # FINAL GUARANTEE: If we still have remaining points, dump into unlimited attrs
        if remaining > 0 and unlimited_attrs:
            for sink_attr in unlimited_attrs:
                cost = attr_costs[sink_attr]
                while remaining >= cost:
                    attrs[sink_attr] += 1
                    remaining -= cost
        
        return talents, attrs
    
    def _simulate_build_rust(self, config: Dict, num_sims: int) -> Optional[BuildResult]:
        """Run simulations using Rust engine - direct call."""
        if not RUST_AVAILABLE:
            return self._simulate_build_sequential(config, num_sims)
        
        hunter_type = self.hunter_name
        level = config.get("meta", {}).get("level") or config.get("level", 100)
        
        try:
            result = rust_sim.simulate(
                hunter=hunter_type,
                level=level,
                stats=config.get("stats", {}),
                talents=config.get("talents", {}),
                attributes=config.get("attributes", {}),
                inscryptions=config.get("inscryptions", {}),
                mods=config.get("mods", {}),
                relics=config.get("relics", {}),
                gems=config.get("gems", {}),
                gadgets=config.get("gadgets", {}),
                bonuses=config.get("bonuses", {}),
                num_sims=num_sims,
                parallel=True
            )
            
            return BuildResult(
                talents=config.get("talents", {}).copy(),
                attributes=config.get("attributes", {}).copy(),
                avg_final_stage=result.get("avg_stage", 0),
                highest_stage=result.get("max_stage", 0),
                lowest_stage=result.get("min_stage", 0),
                avg_loot_per_hour=result.get("avg_loot_per_hour", 0),
                avg_damage=result.get("avg_damage", 0),
                avg_kills=result.get("avg_kills", 0),
                avg_elapsed_time=result.get("avg_time", 0),
                avg_damage_taken=result.get("avg_damage_taken", 0),
                survival_rate=result.get("survival_rate", 0),
                boss1_survival=result.get("boss1_survival", 0),
                boss2_survival=result.get("boss2_survival", 0),
                boss3_survival=result.get("boss3_survival", 0),
                boss4_survival=result.get("boss4_survival", 0),
                boss5_survival=result.get("boss5_survival", 0),
                avg_loot_common=result.get("avg_loot_common", 0),
                avg_loot_uncommon=result.get("avg_loot_uncommon", 0),
                avg_loot_rare=result.get("avg_loot_rare", 0),
                avg_xp=result.get("avg_xp", 0),
                config=config,
            )
        except Exception as e:
            print(f"[RUST ERROR] {e}")
            return None
    
    def _simulate_builds_batch(self, configs: List[Dict], num_sims: int) -> List[Optional[BuildResult]]:
        """Run simulations for multiple builds using Rust batch API."""
        if not RUST_AVAILABLE:
            return [self._simulate_build_sequential(cfg, num_sims) for cfg in configs]
        
        try:
            # Convert configs to JSON strings for Rust
            config_jsons = []
            for cfg in configs:
                rust_cfg = {
                    'hunter': self.hunter_name,
                    'level': cfg.get("meta", {}).get("level") or cfg.get("level", 100),
                    'stats': cfg.get("stats", {}),
                    'talents': cfg.get("talents", {}),
                    'attributes': cfg.get("attributes", {}),
                    'inscryptions': cfg.get("inscryptions", {}),
                    'mods': cfg.get("mods", {}),
                    'relics': cfg.get("relics", {}),
                    'gems': cfg.get("gems", {}),
                    'gadgets': cfg.get("gadgets", {}),
                    'bonuses': cfg.get("bonuses", {})
                }
                config_jsons.append(json.dumps(rust_cfg))
            
            # Batch simulate
            results = rust_sim.simulate_batch(config_jsons, num_sims, True)
            
            # Convert to BuildResult objects
            build_results = []
            for result, cfg in zip(results, configs):
                # Parse result if it's a JSON string
                if isinstance(result, str):
                    result = json.loads(result)
                
                build_results.append(BuildResult(
                    talents=cfg.get("talents", {}).copy(),
                    attributes=cfg.get("attributes", {}).copy(),
                    avg_final_stage=result.get("avg_stage", 0),
                    highest_stage=result.get("max_stage", 0),
                    lowest_stage=result.get("min_stage", 0),
                    avg_loot_per_hour=result.get("avg_loot_per_hour", 0),
                    avg_damage=result.get("avg_damage", 0),
                    avg_kills=result.get("avg_kills", 0),
                    avg_elapsed_time=result.get("avg_time", 0),
                    avg_damage_taken=result.get("avg_damage_taken", 0),
                    survival_rate=result.get("survival_rate", 0),
                    boss1_survival=result.get("boss1_survival", 0),
                    boss2_survival=result.get("boss2_survival", 0),
                    boss3_survival=result.get("boss3_survival", 0),
                    boss4_survival=result.get("boss4_survival", 0),
                    boss5_survival=result.get("boss5_survival", 0),
                    avg_loot_common=result.get("avg_loot_common", 0),
                    avg_loot_uncommon=result.get("avg_loot_uncommon", 0),
                    avg_loot_rare=result.get("avg_loot_rare", 0),
                    avg_xp=result.get("avg_xp", 0),
                    config=cfg
                ))
            return build_results
        except Exception as e:
            print(f"[BATCH ERROR] {e}")
            return [self._simulate_build_rust(cfg, num_sims) for cfg in configs]
    
    def _simulate_build_sequential(self, config: Dict, num_sims: int) -> Optional[BuildResult]:
        """Run simulations sequentially."""
        results_list = []
        
        for _ in range(num_sims):
            sim = Simulation(self.hunter_class(config))
            results_list.append(sim.run())
        
        if not results_list:
            return None
        
        return self._aggregate_results(config, results_list)
    
    def _aggregate_results(self, config: Dict, results_list: List) -> BuildResult:
        """Aggregate simulation results."""
        final_stages = [r['final_stage'] for r in results_list]
        elapsed_times = [r['elapsed_time'] for r in results_list]
        damages = [r['damage'] for r in results_list]
        kills = [r['kills'] for r in results_list]
        damage_takens = [r['damage_taken'] for r in results_list]
        loots = [r['total_loot'] for r in results_list]
        
        # Per-resource loot
        loots_common = [r.get('loot_common', 0) for r in results_list]
        loots_uncommon = [r.get('loot_uncommon', 0) for r in results_list]
        loots_rare = [r.get('loot_rare', 0) for r in results_list]
        
        # XP tracking - use actual total_xp from simulation (WASM formula)
        xps = [r.get('total_xp', 0) for r in results_list]
        
        loot_per_hours = [(loots[i] / (elapsed_times[i] / 3600)) if elapsed_times[i] > 0 else 0 
                          for i in range(len(loots))]
        
        boss_deaths = sum(1 for s in final_stages if s % 100 == 0 and s > 0)
        survival_rate = 1 - (boss_deaths / len(final_stages))
        
        n = len(final_stages)
        boss1_survival = sum(1 for s in final_stages if s > 100) / n
        boss2_survival = sum(1 for s in final_stages if s > 200) / n
        boss3_survival = sum(1 for s in final_stages if s > 300) / n
        boss4_survival = sum(1 for s in final_stages if s > 400) / n
        boss5_survival = sum(1 for s in final_stages if s > 500) / n
        
        return BuildResult(
            talents=config["talents"].copy(),
            attributes=config["attributes"].copy(),
            avg_final_stage=statistics.mean(final_stages),
            highest_stage=max(final_stages),
            lowest_stage=min(final_stages),
            avg_loot_per_hour=statistics.mean(loot_per_hours),
            avg_damage=statistics.mean(damages),
            avg_kills=statistics.mean(kills),
            avg_elapsed_time=statistics.mean(elapsed_times),
            avg_damage_taken=statistics.mean(damage_takens),
            survival_rate=survival_rate,
            boss1_survival=boss1_survival,
            boss2_survival=boss2_survival,
            boss3_survival=boss3_survival,
            boss4_survival=boss4_survival,
            boss5_survival=boss5_survival,
            avg_loot_common=statistics.mean(loots_common),
            avg_loot_uncommon=statistics.mean(loots_uncommon),
            avg_loot_rare=statistics.mean(loots_rare),
            avg_xp=statistics.mean(xps),
            config=config,
        )
    
    def _poll_subprocess(self):
        """Poll subprocess results file"""
        self.poll_count += 1
        
        # Try to read progress file
        progress_file = Path(str(self.result_file).replace('_results.json', '_progress.json'))
        print(f"[POLL #{self.poll_count}] progress_file={progress_file}, exists={progress_file.exists()}")
        if progress_file.exists():
            try:
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                gen = progress_data.get('generation', 0)
                builds_in_gen = progress_data.get('builds_in_gen', 0)
                print(f"[POLL #{self.poll_count}] gen={gen}, builds={builds_in_gen}, last_gen={self.last_logged_gen}, last_builds={self.last_logged_builds}")
                
                # Update global progress bar
                progress_pct = progress_data.get('progress', 0)
                self.progress_var.set(progress_pct)
                
                # Log generation progress (only when generation changes OR significant progress within gen)
                builds_per_gen = progress_data.get('builds_per_gen', 1)
                
                # Detect generation change (means previous gen completed)
                if gen > self.last_logged_gen:
                    # Log completion of PREVIOUS generation if we had one
                    if self.last_logged_gen > 0:
                        total_gen = progress_data.get('total_generations', 0)
                        speed = progress_data.get('sims_per_sec', 0)
                        elapsed = progress_data.get('elapsed', 0)
                        self._log(f"✅ Gen {self.last_logged_gen}/{total_gen} complete | {speed:.0f} sims/sec ({elapsed:.1f}s)")
                    
                    self.last_logged_gen = gen
                    self.last_logged_builds = 0
                
                # Log in-progress updates (only when build count actually changes)
                if builds_in_gen > self.last_logged_builds:
                    speed = progress_data.get('sims_per_sec', 0)
                    total_gen = progress_data.get('total_generations', 0)
                    tier = progress_data.get('tier_name', '')
                    total_sims = progress_data.get('total_sims', 0)
                    
                    # Calculate ETA
                    total_sims_needed = builds_per_gen * total_gen * self._thread_num_sims
                    sims_remaining = total_sims_needed - total_sims
                    eta_seconds = sims_remaining / speed if speed > 0 else 0
                    eta_str = f"{int(eta_seconds)}s" if eta_seconds < 60 else f"{int(eta_seconds/60)}m {int(eta_seconds%60)}s"
                    
                    self._log(f"📊 Gen {gen}/{total_gen} ({tier}): {builds_in_gen}/{builds_per_gen} builds | {speed:.0f} sims/sec | ETA: {eta_str}")
                    self.last_logged_builds = builds_in_gen
            except Exception as e:
                # File may be mid-write, ignore
                pass
        else:
            # No progress file yet - show basic activity
            if self.poll_count % 2 == 0:  # Every second
                progress = min(10, self.poll_count)  # Start at 10%, wait for real progress
                self.progress_var.set(progress)
        
        # Show activity every 5 seconds (only if no progress file)
        if not progress_file.exists() and self.poll_count % 10 == 0:
            elapsed = self.poll_count * 0.5
            self._log(f"⏳ Still running... ({elapsed:.0f}s elapsed)")
        
        # Check if process crashed
        poll_result = self.opt_process.poll()
        print(f"[POLL #{self.poll_count}] poll()={poll_result}, result_file exists={self.result_file.exists()}")
        if poll_result is not None:
            # Process exited - close stderr handle first
            if hasattr(self, 'stderr_handle') and self.stderr_handle:
                self.stderr_handle.close()
            
            exit_code = self.opt_process.returncode
            print(f"[POLL] Process exited with code {exit_code}")
            
            if exit_code != 0:
                # Process crashed - read error from stderr file
                self._log(f"❌ Optimization process crashed (exit code: {exit_code})")
                if hasattr(self, 'stderr_file') and self.stderr_file.exists():
                    try:
                        stderr_content = self.stderr_file.read_text()
                        if stderr_content.strip():
                            self._log(f"━━━ ERROR OUTPUT ━━━")
                            self._log(stderr_content)
                        self.stderr_file.unlink(missing_ok=True)
                    except Exception as e:
                        self._log(f"Could not read stderr: {e}")
                self._optimization_complete()
                return
            
            # Process completed successfully - check for results
            print(f"[POLL] Checking for result file: {self.result_file}")
            if self.result_file.exists():
                print(f"[POLL] Result file EXISTS, loading...")
                try:
                    with open(self.result_file, 'r') as f:
                        results = json.load(f)
                    print(f"[POLL] Results loaded, keys: {results.keys()}")
                    
                    # Clean up temp files
                    self.result_file.unlink(missing_ok=True)
                    Path(str(self.result_file).replace('_results.json', '.json')).unlink(missing_ok=True)
                    Path(str(self.result_file).replace('_results.json', '_progress.json')).unlink(missing_ok=True)
                    if hasattr(self, 'stderr_file'):
                        self.stderr_file.unlink(missing_ok=True)
                    
                    # Display results FIRST (updates status label with actual count)
                    self._display_results(results)
                    
                    # Get top builds from new structure - each metric has its own top 10
                    top_by_max = results.get('top_10_by_max_stage', [])
                    top_by_avg = results.get('top_10_by_avg_stage', [])
                    top_by_loot = results.get('top_10_by_loot', [])
                    top_by_damage = results.get('top_10_by_damage', [])
                    top_by_xp = results.get('top_10_by_xp', [])
                    
                    # Combine all unique builds for self.results
                    all_builds_dict = {}
                    for build in top_by_max + top_by_avg + top_by_loot + top_by_damage + top_by_xp:
                        key = (tuple(sorted(build['talents'].items())), tuple(sorted(build['attributes'].items())))
                        all_builds_dict[key] = build
                    all_builds = list(all_builds_dict.values())
                    
                    # Helper to convert build dict to BuildResult
                    def to_build_result(build):
                        return BuildResult(
                            talents=build['talents'],
                            attributes=build['attributes'],
                            avg_final_stage=build['avg_stage'],
                            highest_stage=build['max_stage'],
                            lowest_stage=build['max_stage'],
                            avg_loot_per_hour=build.get('avg_loot_per_hour', 0),
                            avg_damage=build.get('avg_damage', 0),
                            avg_kills=build.get('avg_kills', 0),
                            avg_elapsed_time=0,
                            avg_damage_taken=0,
                            survival_rate=1.0,
                            avg_xp=build.get('avg_xp', 0),
                            config={'talents': build['talents'], 'attributes': build['attributes']}
                        )
                    
                    # Store all unique builds
                    self.results.clear()
                    for build in all_builds:
                        self.results.append(to_build_result(build))
                    
                    # Store pre-sorted lists for each tab
                    self._top_by_stage = [to_build_result(b) for b in top_by_avg]
                    self._top_by_max_stage = [to_build_result(b) for b in top_by_max]
                    self._top_by_loot = [to_build_result(b) for b in top_by_loot]
                    self._top_by_damage = [to_build_result(b) for b in top_by_damage]
                    self._top_by_xp = [to_build_result(b) for b in top_by_xp]
                    
                    # Process IRL baseline from subprocess results
                    irl_data = results.get('irl_baseline')
                    if irl_data:
                        self.irl_baseline_result = BuildResult(
                            talents=irl_data.get('talents', {}),
                            attributes=irl_data.get('attributes', {}),
                            avg_final_stage=irl_data.get('avg_stage', 0),
                            highest_stage=irl_data.get('max_stage', 0),
                            lowest_stage=irl_data.get('max_stage', 0),
                            avg_loot_per_hour=irl_data.get('avg_loot_per_hour', 0),
                            avg_damage=irl_data.get('avg_damage', 0),
                            avg_kills=irl_data.get('avg_kills', 0),
                            avg_elapsed_time=irl_data.get('avg_time', 0),
                            avg_damage_taken=irl_data.get('avg_damage_taken', 0),
                            survival_rate=irl_data.get('survival_rate', 1.0),
                            avg_xp=irl_data.get('avg_xp', 0),
                            config={'talents': irl_data.get('talents', {}), 'attributes': irl_data.get('attributes', {})}
                        )
                        self._log(f"📊 IRL Baseline: Stage {self.irl_baseline_result.avg_final_stage:.1f} (max {self.irl_baseline_result.highest_stage})")
                    else:
                        self.irl_baseline_result = None
                        self._log("⚠️ No IRL baseline (no talents/attributes entered)")
                    
                    # Process generation history for Generations tab
                    gen_history = results.get('generation_history', [])
                    print(f"[POLL] gen_history has {len(gen_history)} entries")
                    if gen_history:
                        self.generation_history = []
                        for gen_data in gen_history:
                            self.generation_history.append({
                                'generation': gen_data['generation'],
                                'best_max_stage': gen_data['best_max_stage'],
                                'best_avg_stage': gen_data['best_avg_stage'],
                                'talents': gen_data['best_talents'],
                                'attributes': gen_data['best_attributes']
                            })
                            # Display each generation's results in Generations tab
                            self._update_generation_display_subprocess(gen_data)
                    
                    # Now display in result tabs
                    print(f"[POLL] About to display {len(self.results)} results")
                    if self.results:
                        self._display_results_old()
                    else:
                        print("[POLL] WARNING: self.results is empty!")
                    
                    # Then mark as complete (but don't overwrite status)
                    print("[POLL] Marking optimization complete")
                    self.is_running = False
                    self.start_btn.configure(state=tk.NORMAL)
                    self.stop_btn.configure(state=tk.DISABLED)
                    self.progress_var.set(100)
                    
                except Exception as e:
                    self._log(f"❌ Error loading results: {e}\n{traceback.format_exc()}")
                    self._optimization_complete()
            else:
                self._log(f"❌ Process completed but no result file found at: {self.result_file}")
                self._optimization_complete()
        else:
            # Still running - keep polling
            self.frame.after(500, self._poll_subprocess)
    
    def _display_results(self, results):
        """Display optimization results in GUI"""
        # Check for errors
        if results.get('status') == 'error':
            self._log(f"\n❌ ERROR in optimization:")
            self._log(results.get('error', 'Unknown error'))
            self._log(f"\nTraceback:")
            self._log(results.get('traceback', 'No traceback available'))
            return
        
        # Show timing info
        timing = results.get('timing', {})
        self._log(f"\n=== Optimization Complete ===")
        self._log(f"Total time: {timing.get('total_time', 0):.2f}s")
        self._log(f"Simulation speed: {timing.get('sims_per_sec', 0):.0f}/s\n")
        
        # Show best build
        best = results.get('best_build', {})
        self._log(f"=== Best Build ===")
        self._log(f"Max Stage: {best.get('max_stage', 0)}")
        self._log(f"Avg Stage: {best.get('avg_stage', 0):.1f}")
        # Show additional stats if available
        if best.get('avg_loot_per_hour', 0) > 0:
            self._log(f"Avg Loot/Hour: {best.get('avg_loot_per_hour', 0):,.0f}")
        if best.get('avg_damage', 0) > 0:
            self._log(f"Avg Damage: {best.get('avg_damage', 0):,.0f}")
        if best.get('avg_kills', 0) > 0:
            self._log(f"Avg Kills: {best.get('avg_kills', 0):,.0f}")
        self._log("")  # Blank line
        
        # Show talents
        talents = best.get('talents', {})
        if talents:
            self._log("Talents:")
            for talent, level in talents.items():
                if level > 0:
                    self._log(f"  • {talent}: {level}")
        
        # Show attributes
        attributes = best.get('attributes', {})
        if attributes:
            self._log("\nAttributes:")
            for attr, level in attributes.items():
                if level > 0:
                    self._log(f"  • {attr}: {level}")
        
        # Show full details
        self._log(f"\n{results.get('full_report', '')}")
        
        # Update progress bar with actual build count (extract from report)
        import re
        match = re.search(r'Tested (\d+) builds', results.get('full_report', ''))
        if match:
            tested_count = int(match.group(1))
            self.status_label.configure(text=f"Done! {tested_count} builds ({timing.get('sims_per_sec', 0):.0f}/s)")
        else:
            self.status_label.configure(text=f"Done! Max Stage: {best.get('max_stage', 0)}")
    
    def _poll_results(self):
        """Poll for results from background thread."""
        try:
            while True:
                msg_type, data, tested, total = self.result_queue.get_nowait()
                
                if msg_type == 'progress':
                    self.progress_var.set(data)
                    if tested and total:
                        elapsed = time.time() - self.optimization_start_time
                        rate = tested / elapsed if elapsed > 0 else 0
                        self.status_label.configure(text=f"{tested}/{total} ({rate:.1f}/sec)")
                elif msg_type == 'best_update':
                    if data.get('best_max', 0) > self.best_max_stage:
                        self.best_max_stage = data['best_max']
                        self.best_max_label.configure(text=f"🏆 Best Max: {self.best_max_stage}")
                    if data.get('best_avg', 0) > self.best_avg_stage:
                        self.best_avg_stage = data['best_avg']
                        self.best_avg_label.configure(text=f"📊 Best Avg: {self.best_avg_stage:.1f}")
                elif msg_type == 'log':
                    self._log_direct(data)
                elif msg_type == 'generation_data':
                    # Store generation data for display
                    self.generation_history.append(data)
                    self._update_generation_display(data)
                elif msg_type == 'done':
                    self._optimization_complete()
                    return
                elif msg_type == 'error':
                    self._optimization_complete()
                    return
                    
        except queue.Empty:
            pass
        except Exception as e:
            # Handle any other exceptions to prevent crash
            print(f"Error in _poll_results: {e}")
            pass
        
        if self.is_running:
            self.frame.after(500, self._poll_results)
    
    def _optimization_complete(self):
        """Handle optimization completion (called after results displayed or error occurred)."""
        self.is_running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.progress_var.set(100)
        # Note: results display and status label already updated by _poll_subprocess
        # Just select the results frame to show the user what we found
        if self.results:
            self.sub_notebook.select(self.results_frame)
    
    def _display_results_old(self):
        """Display optimization results using pre-sorted lists from subprocess."""
        print(f"[_display_results_old] Called with {len(self.results)} results")
        if not self.results:
            print("[_display_results_old] No results to display!")
            for text_widget in self.result_tabs.values():
                text_widget.configure(state=tk.NORMAL)
                text_widget.delete(1.0, tk.END)
                text_widget.insert(tk.END, "No results. Run optimization first.")
                text_widget.configure(state=tk.DISABLED)
            return
        
        print(f"[_display_results_old] First result: avg_stage={self.results[0].avg_final_stage}, max={self.results[0].highest_stage}")
        
        # Use pre-sorted lists from subprocess (already top 10 by each metric)
        by_stage = getattr(self, '_top_by_stage', None) or sorted(self.results, 
                          key=lambda r: (r.avg_final_stage, r.highest_stage, r.avg_loot_per_hour), 
                          reverse=True)[:10]
        by_max_stage = getattr(self, '_top_by_max_stage', None) or sorted(self.results,
                          key=lambda r: (r.highest_stage, r.avg_final_stage, r.avg_loot_per_hour),
                          reverse=True)[:10]
        by_loot = getattr(self, '_top_by_loot', None) or sorted(self.results, key=lambda r: r.avg_loot_per_hour, reverse=True)[:10]
        by_xp = getattr(self, '_top_by_xp', None) or sorted(self.results, key=lambda r: r.avg_xp, reverse=True)[:10]
        by_damage = getattr(self, '_top_by_damage', None) or sorted(self.results, key=lambda r: r.avg_damage, reverse=True)[:10]
        
        self._display_category(self.result_tabs["stage"], by_stage, "Avg Stage",
                               lambda r: f"{r.avg_final_stage:.1f} (max {r.highest_stage})")
        self._display_category(self.result_tabs["max_stage"], by_max_stage, "Max Stage",
                               lambda r: f"{r.highest_stage} (avg {r.avg_final_stage:.1f})")
        self._display_category(self.result_tabs["loot"], by_loot, "Loot/Hour",
                               lambda r: f"{r.avg_loot_per_hour:.2f}")
        self._display_category(self.result_tabs["xp"], by_xp, "Avg XP",
                               lambda r: f"{self._format_number(r.avg_xp)}")
        self._display_category(self.result_tabs["damage"], by_damage, "Avg Damage",
                               lambda r: f"{r.avg_damage:,.0f}")
        
        # Compare tab - detailed comparison with IRL build
        self._display_comparison_tab()
    
    def _display_comparison_tab(self):
        """Display the comparison tab with detailed IRL vs top 3 builds analysis."""
        compare_text = self.result_tabs["compare"]
        compare_text.configure(state=tk.NORMAL)
        compare_text.delete(1.0, tk.END)
        
        if not self.irl_baseline_result:
            compare_text.insert(tk.END, "No IRL baseline available.\n")
            compare_text.insert(tk.END, "Run optimization first to see comparison.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        if not self.results:
            compare_text.insert(tk.END, "No optimization results available.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        irl = self.irl_baseline_result
        top3 = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[:3]
        best = top3[0] if top3 else None
        
        if not best:
            compare_text.insert(tk.END, "No builds to compare.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        res_common, res_uncommon, res_rare = self._get_resource_names()
        
        # Header
        compare_text.insert(tk.END, "═" * 70 + "\n", "divider")
        compare_text.insert(tk.END, "📊 BUILD COMPARISON: YOUR BUILD VS OPTIMAL BUILDS\n", "header")
        compare_text.insert(tk.END, "═" * 70 + "\n\n", "divider")
        
        # Calculate optimal percentages based on different metrics
        pct_stage = (irl.avg_final_stage / best.avg_final_stage * 100) if best.avg_final_stage > 0 else 100
        pct_loot = (irl.avg_loot_per_hour / best.avg_loot_per_hour * 100) if best.avg_loot_per_hour > 0 else 100
        pct_xp = (irl.avg_xp / best.avg_xp * 100) if best.avg_xp > 0 else 100
        pct_damage = (irl.avg_damage / best.avg_damage * 100) if best.avg_damage > 0 else 100
        
        # Overall optimal score (weighted average)
        overall_pct = (pct_stage * 0.4 + pct_loot * 0.3 + pct_xp * 0.15 + pct_damage * 0.15)
        
        # Big optimality display
        compare_text.insert(tk.END, "╔════════════════════════════════════════════════════════════════╗\n", "gold")
        compare_text.insert(tk.END, "║         YOUR BUILD IS ", "gold")
        # Color based on score
        if overall_pct >= 95:
            compare_text.insert(tk.END, f"{overall_pct:>6.2f}%", "positive")
        elif overall_pct >= 80:
            compare_text.insert(tk.END, f"{overall_pct:>6.2f}%", "neutral")
        elif overall_pct >= 60:
            compare_text.insert(tk.END, f"{overall_pct:>6.2f}%", "cost")
        else:
            compare_text.insert(tk.END, f"{overall_pct:>6.2f}%", "negative")
        compare_text.insert(tk.END, " OPTIMAL                      ║\n", "gold")
        compare_text.insert(tk.END, "╚════════════════════════════════════════════════════════════════╝\n\n", "gold")
        
        # Rating and recommendation
        if overall_pct >= 98:
            grade = "🌟 PERFECT - No respec needed!"
            advice = "Your build is essentially optimal. Save your resources."
            grade_color = "gold"
        elif overall_pct >= 95:
            grade = "🌟 EXCELLENT - Minor gains possible"
            advice = "Very minor improvements possible. Probably not worth a respec."
            grade_color = "positive"
        elif overall_pct >= 90:
            grade = "✅ GREAT - Small room for improvement"
            advice = "Some gains possible. Consider respec if resources are plentiful."
            grade_color = "positive"
        elif overall_pct >= 80:
            grade = "👍 GOOD - Noticeable gains available"
            advice = "Meaningful improvements available. Respec recommended when convenient."
            grade_color = "neutral"
        elif overall_pct >= 70:
            grade = "📈 DECENT - Significant gains available"
            advice = "Significant improvements possible. Respec is a good investment."
            grade_color = "cost"
        elif overall_pct >= 60:
            grade = "⚠️ SUBOPTIMAL - Large gains available"
            advice = "Major improvements available. Strongly recommend respec."
            grade_color = "cost"
        else:
            grade = "🔧 NEEDS WORK - Substantial gains available"
            advice = "Your build needs significant optimization. Respec ASAP!"
            grade_color = "negative"
        
        compare_text.insert(tk.END, "Rating: ", "stat_name")
        compare_text.insert(tk.END, f"{grade}\n", grade_color)
        compare_text.insert(tk.END, "💡 Advice: ", "stat_name")
        compare_text.insert(tk.END, f"{advice}\n\n", "tip")
        
        # Detailed breakdown
        compare_text.insert(tk.END, "─" * 70 + "\n", "divider")
        compare_text.insert(tk.END, "📊 METRIC BREAKDOWN:\n", "subheader")
        compare_text.insert(tk.END, "─" * 70 + "\n\n", "divider")
        
        # Helper to get color for percentage
        def get_pct_color(pct):
            if pct >= 95: return "positive"
            elif pct >= 80: return "neutral"
            elif pct >= 60: return "cost"
            else: return "negative"
        
        compare_text.insert(tk.END, "  🏔️ Stage:    ", "stat_name")
        compare_text.insert(tk.END, f"{pct_stage:>6.2f}% optimal", get_pct_color(pct_stage))
        compare_text.insert(tk.END, f"  ({irl.avg_final_stage:.1f} vs {best.avg_final_stage:.1f})\n", "neutral")
        
        compare_text.insert(tk.END, "  💰 Loot/Hr:  ", "stat_name")
        compare_text.insert(tk.END, f"{pct_loot:>6.2f}% optimal", get_pct_color(pct_loot))
        compare_text.insert(tk.END, f"  ({irl.avg_loot_per_hour:.2f} vs {best.avg_loot_per_hour:.2f})\n", "neutral")
        
        compare_text.insert(tk.END, "  📈 XP/Run:   ", "stat_name")
        compare_text.insert(tk.END, f"{pct_xp:>6.2f}% optimal", get_pct_color(pct_xp))
        compare_text.insert(tk.END, f"  ({self._format_number(irl.avg_xp)} vs {self._format_number(best.avg_xp)})\n", "neutral")
        
        compare_text.insert(tk.END, "  💥 Damage:   ", "stat_name")
        compare_text.insert(tk.END, f"{pct_damage:>6.2f}% optimal", get_pct_color(pct_damage))
        compare_text.insert(tk.END, f"  ({irl.avg_damage:,.0f} vs {best.avg_damage:,.0f})\n\n", "neutral")
        
        # Potential gains
        compare_text.insert(tk.END, "─" * 70 + "\n", "divider")
        compare_text.insert(tk.END, "✨ POTENTIAL GAINS IF YOU RESPEC:\n", "subheader")
        compare_text.insert(tk.END, "─" * 70 + "\n\n", "divider")
        
        stage_gain = best.avg_final_stage - irl.avg_final_stage
        loot_gain_pct = ((best.avg_loot_per_hour / irl.avg_loot_per_hour) - 1) * 100 if irl.avg_loot_per_hour > 0 else 0
        xp_gain_pct = ((best.avg_xp / irl.avg_xp) - 1) * 100 if irl.avg_xp > 0 else 0
        
        compare_text.insert(tk.END, "  Stage:      ", "stat_name")
        compare_text.insert(tk.END, f"+{stage_gain:.1f} stages\n", "positive")
        compare_text.insert(tk.END, "  Loot:       ", "stat_name")
        compare_text.insert(tk.END, f"+{loot_gain_pct:.1f}% more loot per hour\n", "positive")
        compare_text.insert(tk.END, "  XP:         ", "stat_name")
        compare_text.insert(tk.END, f"+{xp_gain_pct:.1f}% more XP per run\n\n", "positive")
        
        # Compare talents/attributes
        compare_text.insert(tk.END, "─" * 70 + "\n", "divider")
        compare_text.insert(tk.END, "🏆 TOP 3 OPTIMAL BUILDS:\n", "subheader")
        compare_text.insert(tk.END, "─" * 70 + "\n\n", "divider")
        
        medals = ["🥇", "🥈", "🥉"]
        medal_colors = ["gold", "silver", "bronze"]
        for i, build in enumerate(top3, 1):
            pct_of_best = (build.avg_final_stage / best.avg_final_stage * 100) if best.avg_final_stage > 0 else 100
            compare_text.insert(tk.END, f"{medals[i-1]} ", medal_colors[i-1])
            compare_text.insert(tk.END, f"Stage ", "stat_name")
            compare_text.insert(tk.END, f"{build.avg_final_stage:.1f}", "positive")
            compare_text.insert(tk.END, f" ({pct_of_best:.1f}% of best)\n", "neutral")
            compare_text.insert(tk.END, "   Talents: ", "subheader")
            compare_text.insert(tk.END, f"{', '.join(f'{k}:{v}' for k, v in build.talents.items() if v > 0)}\n", "level")
            compare_text.insert(tk.END, "   Attrs: ", "subheader")
            compare_text.insert(tk.END, f"{', '.join(f'{k}:{v}' for k, v in build.attributes.items() if v > 0)}\n\n", "level")
        
        # Talent/attribute diff from your build to best
        compare_text.insert(tk.END, "─" * 70 + "\n", "divider")
        compare_text.insert(tk.END, "🔧 CHANGES NEEDED (Your Build → Best Build):\n", "subheader")
        compare_text.insert(tk.END, "─" * 70 + "\n\n", "divider")
        
        compare_text.insert(tk.END, "  ⭐ TALENTS:\n", "stat_name")
        for talent in set(list(irl.talents.keys()) + list(best.talents.keys())):
            irl_val = irl.talents.get(talent, 0)
            best_val = best.talents.get(talent, 0)
            if irl_val != best_val:
                diff = best_val - irl_val
                compare_text.insert(tk.END, f"    {talent}: ", "level")
                compare_text.insert(tk.END, f"{irl_val}", "neutral")
                compare_text.insert(tk.END, " → ", "stat_name")
                compare_text.insert(tk.END, f"{best_val}", "positive" if diff > 0 else "negative")
                compare_text.insert(tk.END, f" ({'+' if diff > 0 else ''}{diff})\n", "positive" if diff > 0 else "negative")
        
        compare_text.insert(tk.END, "\n  🔮 ATTRIBUTES:\n", "stat_name")
        for attr in set(list(irl.attributes.keys()) + list(best.attributes.keys())):
            irl_val = irl.attributes.get(attr, 0)
            best_val = best.attributes.get(attr, 0)
            if irl_val != best_val:
                diff = best_val - irl_val
                compare_text.insert(tk.END, f"    {attr}: ", "level")
                compare_text.insert(tk.END, f"{irl_val}", "neutral")
                compare_text.insert(tk.END, " → ", "stat_name")
                compare_text.insert(tk.END, f"{best_val}", "positive" if diff > 0 else "negative")
                compare_text.insert(tk.END, f" ({'+' if diff > 0 else ''}{diff})\n", "positive" if diff > 0 else "negative")
        
        compare_text.configure(state=tk.DISABLED)
    
    def _display_category(self, text_widget, results: List[BuildResult], metric_name: str, metric_fn):
        """Display results for a category with color-coded rankings."""
        text_widget.configure(state=tk.NORMAL)
        text_widget.delete(1.0, tk.END)
        
        # Header with color
        text_widget.insert(tk.END, f"🏆 TOP 10 BY {metric_name.upper()}\n", "header")
        text_widget.insert(tk.END, "═" * 50 + "\n\n", "divider")
        
        for i, result in enumerate(results, 1):
            # Determine medal and color tag based on ranking
            if i == 1:
                medal = "🥇"
                tag = "gold"
            elif i == 2:
                medal = "🥈"
                tag = "silver"
            elif i == 3:
                medal = "🥉"
                tag = "bronze"
            else:
                medal = f"#{i}"
                tag = "neutral"
            
            # Insert ranking line with medal and color
            text_widget.insert(tk.END, f"{medal} ", tag)
            text_widget.insert(tk.END, f"{metric_name} = ", "stat_name")
            text_widget.insert(tk.END, f"{metric_fn(result)}\n", "positive")
            
            self._insert_colorful_build_result(text_widget, result)
            text_widget.insert(tk.END, "\n")
        
        text_widget.configure(state=tk.DISABLED)
    
    def _format_build_result(self, result: BuildResult) -> str:
        """Format a build result for display (plain text version)."""
        lines = []
        lines.append(f"  Stage: {result.avg_final_stage:.1f} (max {result.highest_stage})")
        
        # Get resource names for this hunter
        res_common, res_uncommon, res_rare = self._get_resource_names()
        
        # Calculate per run and per day loot
        if result.avg_elapsed_time > 0:
            runs_per_hour = 3600 / result.avg_elapsed_time
            runs_per_day = runs_per_hour * 24
            
            common_per_run = result.avg_loot_common
            uncommon_per_run = result.avg_loot_uncommon
            rare_per_run = result.avg_loot_rare
            common_per_day = common_per_run * runs_per_day
            uncommon_per_day = uncommon_per_run * runs_per_day
            rare_per_day = rare_per_run * runs_per_day
            
            lines.append(f"  📦 LOOT PER RUN / PER DAY:")
            lines.append(f"     {res_common}: {self._format_number(common_per_run)} / {self._format_number(common_per_day)}")
            lines.append(f"     {res_uncommon}: {self._format_number(uncommon_per_run)} / {self._format_number(uncommon_per_day)}")
            lines.append(f"     {res_rare}: {self._format_number(rare_per_run)} / {self._format_number(rare_per_day)}")
            
            xp_per_run = result.avg_xp
            xp_per_day = xp_per_run * runs_per_day
            lines.append(f"  📈 XP PER RUN / PER DAY: {self._format_number(xp_per_run)} / {self._format_number(xp_per_day)}")
        else:
            lines.append(f"  Loot/Hr: {result.avg_loot_per_hour:.2f}")
        
        lines.append(f"  Damage: {result.avg_damage:,.0f}")
        
        lines.append("  Talents: " + ", ".join(f"{k}:{v}" for k, v in result.talents.items() if v > 0))
        lines.append("  Attrs: " + ", ".join(f"{k}:{v}" for k, v in result.attributes.items() if v > 0))
        
        return "\n".join(lines)
    
    def _insert_colorful_build_result(self, text_widget, result):
        """Insert a colorfully formatted build result into a text widget."""
        # Stage line
        text_widget.insert(tk.END, "   Stage: ", "stat_name")
        text_widget.insert(tk.END, f"{result.avg_final_stage:.1f}", "positive")
        text_widget.insert(tk.END, f" (max {result.highest_stage})\n", "neutral")
        
        # Talents line
        talents_str = ", ".join(f"{k}:{v}" for k, v in result.talents.items() if v > 0)
        text_widget.insert(tk.END, "   Talents: ", "subheader")
        text_widget.insert(tk.END, f"{talents_str}\n", "level")
        
        # Attrs line
        attrs_str = ", ".join(f"{k}:{v}" for k, v in result.attributes.items() if v > 0)
        text_widget.insert(tk.END, "   Attrs: ", "subheader")
        text_widget.insert(tk.END, f"{attrs_str}\n", "level")
    
    def _format_number(self, num: float) -> str:
        """Format large numbers with suffixes (k, m, b, t, qa, qi)."""
        if num < 1000:
            return f"{num:.2f}"
        elif num < 1_000_000:
            return f"{num/1000:.2f}k"
        elif num < 1_000_000_000:
            return f"{num/1_000_000:.2f}m"
        elif num < 1_000_000_000_000:
            return f"{num/1_000_000_000:.2f}b"
        elif num < 1_000_000_000_000_000:
            return f"{num/1_000_000_000_000:.2f}t"
        elif num < 1_000_000_000_000_000_000:
            return f"{num/1_000_000_000_000_000:.2f}qa"
        else:
            return f"{num/1_000_000_000_000_000_000:.2f}qi"


class MultiHunterGUI:
    """Main GUI with tabs for each hunter."""
    
    # Dark theme colors
    DARK_BG = "#1a1a2e"
    DARK_BG_SECONDARY = "#16213e"
    DARK_BG_TERTIARY = "#0f0f1a"
    DARK_TEXT = "#e0e0e0"
    DARK_TEXT_DIM = "#888888"
    DARK_ACCENT = "#4a4a6a"
    DARK_BORDER = "#3a3a5a"
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🎮 Hunter Sim - Multi-Hunter Optimizer")
        self.root.geometry("1400x900")
        self.root.minsize(1000, 600)
        
        # Apply dark theme to root window
        self.root.configure(bg=self.DARK_BG)
        
        # Setup color styles FIRST
        self._setup_styles()
        
        # Create global log FIRST (before hunter tabs which may log on load)
        self._create_log_frame()
        
        # Create main notebook
        self.main_notebook = ttk.Notebook(self.root, style="Dark.TNotebook")
        self.main_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Initialize hunter_tabs dict BEFORE creating control tab (which references it)
        self.hunter_tabs: Dict[str, HunterTab] = {}
        
        # Create control tab frame FIRST (so it's first in notebook)
        self.control_frame = ttk.Frame(self.main_notebook, style="Dark.TFrame")
        self.main_notebook.add(self.control_frame, text="  ⚙️ Control  ")
        
        # Create hunter tabs with colors in order: Borge, Ozzy, Knox
        for name, cls in [("Borge", Borge), ("Ozzy", Ozzy), ("Knox", Knox)]:
            self.hunter_tabs[name] = HunterTab(self.main_notebook, name, cls, self)
            # Color the tab text
            colors = HUNTER_COLORS[name]
            idx = self.main_notebook.index("end") - 1
            # Use colored icons in tab names
            icon = '🛡️' if name == 'Borge' else '🔫' if name == 'Knox' else '🐙'
            self.main_notebook.tab(idx, text=f"  {icon} {name}  ")
        
        # Pre-initialize Borge tab since Control tab is shown first and 
        # Borge will be the first hunter tab selected
        self.root.after(100, lambda: self.hunter_tabs["Borge"]._on_tab_visible())
        
        # Now populate the control tab (hunter_tabs exists now)
        self._populate_control_tab()
    
    def _setup_styles(self):
        """Configure ttk styles with hunter colors and dark theme."""
        style = ttk.Style()
        
        # Set theme base
        try:
            style.theme_use('clam')  # 'clam' works well with custom colors
        except:
            pass  # Use default if clam not available
        
        # === APPLY DARK THEME TO ALL DEFAULT TTK WIDGETS ===
        # This ensures all ttk widgets use dark theme by default
        style.configure(".", background=self.DARK_BG, foreground=self.DARK_TEXT,
                       fieldbackground=self.DARK_BG_SECONDARY, 
                       insertcolor=self.DARK_TEXT,
                       selectbackground=self.DARK_ACCENT,
                       selectforeground=self.DARK_TEXT)
        
        # TFrame - dark background
        style.configure("TFrame", background=self.DARK_BG)
        
        # TLabel - dark with light text
        style.configure("TLabel", background=self.DARK_BG, foreground=self.DARK_TEXT)
        
        # TLabelframe - dark with border
        style.configure("TLabelframe", background=self.DARK_BG, foreground=self.DARK_TEXT,
                       bordercolor=self.DARK_BORDER)
        style.configure("TLabelframe.Label", background=self.DARK_BG, foreground=self.DARK_TEXT,
                       font=('Arial', 10, 'bold'))
        
        # TEntry - dark field background
        style.configure("TEntry", fieldbackground=self.DARK_BG_SECONDARY, 
                       foreground=self.DARK_TEXT, insertcolor=self.DARK_TEXT)
        style.map("TEntry",
                 fieldbackground=[("readonly", self.DARK_BG_TERTIARY)],
                 foreground=[("readonly", self.DARK_TEXT_DIM)])
        
        # TSpinbox - dark field background
        style.configure("TSpinbox", fieldbackground=self.DARK_BG_SECONDARY,
                       foreground=self.DARK_TEXT, background=self.DARK_BG,
                       arrowcolor=self.DARK_TEXT, insertcolor=self.DARK_TEXT)
        style.map("TSpinbox",
                 fieldbackground=[("readonly", self.DARK_BG_TERTIARY)])
        
        # TButton - dark with accent
        style.configure("TButton", background=self.DARK_ACCENT, foreground=self.DARK_TEXT,
                       borderwidth=1, focuscolor=self.DARK_ACCENT)
        style.map("TButton",
                 background=[("active", "#5a5a8a"), ("pressed", "#3a3a5a")])
        
        # TCheckbutton - dark with bright checkmark
        style.configure("TCheckbutton", background=self.DARK_BG, foreground=self.DARK_TEXT,
                       indicatorbackground=self.DARK_BG_SECONDARY,
                       indicatorforeground="#00ff88")  # Bright green checkmark
        style.map("TCheckbutton",
                 background=[("active", self.DARK_BG)],
                 indicatorbackground=[("selected", "#2a4a3a")],  # Dark green bg when selected
                 indicatorforeground=[("selected", "#00ff88")])  # Bright green X when checked
        
        # TNotebook - dark tabs
        style.configure("TNotebook", background=self.DARK_BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.DARK_BG_SECONDARY,
                       foreground=self.DARK_TEXT, padding=[12, 4],
                       font=('Arial', 10, 'bold'))
        style.map("TNotebook.Tab",
                 background=[("selected", self.DARK_BG), ("active", self.DARK_ACCENT)],
                 foreground=[("selected", "#ffffff"), ("active", "#ffffff")])
        
        # TProgressbar - dark with accent
        style.configure("Horizontal.TProgressbar",
                       troughcolor=self.DARK_BG_TERTIARY,
                       background="#4a90d9")
        
        # TScrollbar - dark
        style.configure("Vertical.TScrollbar", background=self.DARK_BG_SECONDARY,
                       troughcolor=self.DARK_BG, borderwidth=0, arrowcolor=self.DARK_TEXT)
        style.configure("Horizontal.TScrollbar", background=self.DARK_BG_SECONDARY,
                       troughcolor=self.DARK_BG, borderwidth=0, arrowcolor=self.DARK_TEXT)
        
        # TCombobox - dark
        style.configure("TCombobox", fieldbackground=self.DARK_BG_SECONDARY,
                       foreground=self.DARK_TEXT, background=self.DARK_BG,
                       arrowcolor=self.DARK_TEXT, selectbackground=self.DARK_ACCENT)
        style.map("TCombobox",
                 fieldbackground=[("readonly", self.DARK_BG_SECONDARY)])
        
        # TSeparator - dark border
        style.configure("TSeparator", background=self.DARK_BORDER)
        
        # === EXPLICIT DARK VARIANTS (for manual use) ===
        style.configure("Dark.TFrame", background=self.DARK_BG)
        style.configure("Dark.TLabel", background=self.DARK_BG, foreground=self.DARK_TEXT)
        style.configure("Dark.TLabelframe", background=self.DARK_BG, foreground=self.DARK_TEXT)
        style.configure("Dark.TLabelframe.Label", background=self.DARK_BG, foreground=self.DARK_TEXT, 
                       font=('Arial', 10, 'bold'))
        style.configure("Dark.TNotebook", background=self.DARK_BG, borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=self.DARK_BG_SECONDARY, 
                       foreground=self.DARK_TEXT, padding=[12, 4],
                       font=('Arial', 10, 'bold'))
        style.map("Dark.TNotebook.Tab",
                 background=[("selected", self.DARK_BG), ("active", self.DARK_ACCENT)],
                 foreground=[("selected", "#ffffff"), ("active", "#ffffff")])
        style.configure("Dark.TButton", background=self.DARK_ACCENT, foreground=self.DARK_TEXT,
                       borderwidth=1, focuscolor=self.DARK_ACCENT, font=('Arial', 10))
        style.map("Dark.TButton",
                 background=[("active", "#5a5a8a"), ("pressed", "#3a3a5a")])
        style.configure("Dark.TCheckbutton", background=self.DARK_BG, foreground=self.DARK_TEXT)
        style.configure("Dark.TSpinbox", fieldbackground=self.DARK_BG_SECONDARY, 
                       foreground=self.DARK_TEXT, background=self.DARK_BG)
        style.configure("Dark.TEntry", fieldbackground=self.DARK_BG_SECONDARY, 
                       foreground=self.DARK_TEXT)
        style.configure("Dark.Horizontal.TProgressbar",
                       troughcolor=self.DARK_BG_TERTIARY, background="#4a90d9")
        style.configure("Dark.TSeparator", background=self.DARK_BORDER)
        style.configure("Dark.Vertical.TScrollbar", background=self.DARK_BG_SECONDARY,
                       troughcolor=self.DARK_BG, borderwidth=0)
        
        # Configure styles for each hunter
        for hunter, colors in HUNTER_COLORS.items():
            # Tab style - colored text
            style.configure(f"{hunter}.TNotebook.Tab", 
                          foreground=colors["dark"],
                          font=('Arial', 10, 'bold'))
            
            # Label styles (dark theme compatible)
            style.configure(f"{hunter}.TLabel",
                          background=self.DARK_BG,
                          foreground=colors["primary"],
                          font=('Arial', 10, 'bold'))
            
            style.configure(f"{hunter}Light.TLabel",
                          background=self.DARK_BG,
                          foreground=colors["light"])
            
            # Frame style with colored border effect
            style.configure(f"{hunter}.TLabelframe",
                          background=self.DARK_BG,
                          bordercolor=colors["primary"])
            style.configure(f"{hunter}.TLabelframe.Label",
                          background=self.DARK_BG,
                          foreground=colors["primary"],
                          font=('Arial', 10, 'bold'))
            
            # Progress bar colors
            style.configure(f"{hunter}.Horizontal.TProgressbar",
                          troughcolor=self.DARK_BG_TERTIARY,
                          background=colors["primary"])
            
            # Button style
            style.configure(f"{hunter}.TButton",
                          foreground=colors["primary"])
    
    def _save_global_bonuses(self, *args):
        """Save global bonuses to file."""
        try:
            # Get values with fallbacks for invalid intermediate states during typing
            def safe_get_int(var):
                try:
                    return var.get()
                except (tk.TclError, ValueError):
                    return None
            
            def safe_get_bool(var):
                try:
                    return var.get()
                except (tk.TclError, ValueError):
                    return None
            
            def safe_get_float(var):
                try:
                    return var.get()
                except (tk.TclError, ValueError):
                    return None
            
            # Skip save if any value is invalid (during typing)
            shard = safe_get_int(self.global_shard_milestone)
            relic7 = safe_get_int(self.global_relic7)
            research81 = safe_get_int(self.global_research81)
            scavenger = safe_get_int(self.global_scavenger)
            scavenger2 = safe_get_int(self.global_scavenger2)
            diamond = safe_get_int(self.global_diamond_loot)
            cm46 = safe_get_bool(self.global_cm46)
            cm47 = safe_get_bool(self.global_cm47)
            cm48 = safe_get_bool(self.global_cm48)
            cm51 = safe_get_bool(self.global_cm51)
            gaiden = safe_get_bool(self.global_gaiden_card)
            iridian = safe_get_bool(self.global_iridian_card)
            iap = safe_get_bool(self.global_iap_travpack)
            ultima = safe_get_float(self.global_ultima_multiplier)
            
            # Relics
            relic_r4 = safe_get_int(self.global_relic_r4)
            relic_r16 = safe_get_int(self.global_relic_r16)
            relic_r17 = safe_get_int(self.global_relic_r17)
            relic_r19 = safe_get_int(self.global_relic_r19)
            
            # Gems
            gem_attraction = safe_get_int(self.global_gem_attraction)
            gem_loot_borge = safe_get_int(self.global_gem_loot_borge)
            gem_loot_ozzy = safe_get_int(self.global_gem_loot_ozzy)
            gem_catchup = safe_get_int(self.global_gem_catchup)
            gem_attraction_node3 = safe_get_int(self.global_gem_attraction_node3)
            gem_innovation_node3 = safe_get_int(self.global_gem_innovation_node3)
            gem_creation_node1 = safe_get_int(self.global_gem_creation_node1)
            gem_creation_node2 = safe_get_int(self.global_gem_creation_node2)
            gem_creation_node3 = safe_get_int(self.global_gem_creation_node3)
            
            all_values = [shard, relic7, research81, scavenger, scavenger2, 
                          diamond, cm46, cm47, cm48, cm51, gaiden, iridian, iap, ultima,
                          relic_r4, relic_r16, relic_r17, relic_r19,
                          gem_attraction, gem_loot_borge, gem_loot_ozzy, gem_catchup, 
                          gem_attraction_node3, gem_innovation_node3,
                          gem_creation_node1, gem_creation_node2, gem_creation_node3]
            
            if any(v is None for v in all_values):
                return  # Skip save during invalid typing
            
            config = {
                "shard_milestone": shard,
                "relic7": relic7,
                "research81": research81,
                "scavenger": scavenger,
                "scavenger2": scavenger2,
                "diamond_loot": diamond,
                "cm46": cm46,
                "cm47": cm47,
                "cm48": cm48,
                "cm51": cm51,
                "gaiden_card": gaiden,
                "iridian_card": iridian,
                "iap_travpack": iap,
                "ultima_multiplier": ultima,
                # Global relics
                "relic_r4": relic_r4,
                "relic_r16": relic_r16,
                "relic_r17": relic_r17,
                "relic_r19": relic_r19,
                # Global gems
                "gem_attraction": gem_attraction,
                "gem_loot_borge": gem_loot_borge,
                "gem_loot_ozzy": gem_loot_ozzy,
                "gem_catchup": gem_catchup,
                "gem_attraction_node3": gem_attraction_node3,
                "gem_innovation_node3": gem_innovation_node3,
                "gem_creation_node1": gem_creation_node1,
                "gem_creation_node2": gem_creation_node2,
                "gem_creation_node3": gem_creation_node3
            }
            IRL_BUILDS_PATH.mkdir(exist_ok=True)
            with open(GLOBAL_BONUSES_FILE, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass  # Silently ignore save errors during typing
    
    def _load_global_bonuses(self):
        """Load global bonuses from file."""
        if GLOBAL_BONUSES_FILE.exists():
            try:
                with open(GLOBAL_BONUSES_FILE, 'r') as f:
                    config = json.load(f)
                self.global_shard_milestone.set(config.get("shard_milestone", 0))
                self.global_relic7.set(config.get("relic7", 0))
                self.global_research81.set(config.get("research81", 0))
                self.global_scavenger.set(config.get("scavenger", 0))
                self.global_scavenger2.set(config.get("scavenger2", 0))
                self.global_diamond_loot.set(config.get("diamond_loot", 0))
                self.global_cm46.set(config.get("cm46", False))
                self.global_cm47.set(config.get("cm47", False))
                self.global_cm48.set(config.get("cm48", False))
                self.global_cm51.set(config.get("cm51", False))
                self.global_gaiden_card.set(config.get("gaiden_card", False))
                self.global_iridian_card.set(config.get("iridian_card", False))
                self.global_iap_travpack.set(config.get("iap_travpack", False))
                self.global_ultima_multiplier.set(config.get("ultima_multiplier", 1.0))
                # Global relics
                self.global_relic_r4.set(config.get("relic_r4", 0))
                self.global_relic_r16.set(config.get("relic_r16", 0))
                self.global_relic_r17.set(config.get("relic_r17", 0))
                self.global_relic_r19.set(config.get("relic_r19", 0))
                # Global gems
                self.global_gem_attraction.set(config.get("gem_attraction", 0))
                self.global_gem_loot_borge.set(config.get("gem_loot_borge", 0))
                self.global_gem_loot_ozzy.set(config.get("gem_loot_ozzy", 0))
                self.global_gem_catchup.set(config.get("gem_catchup", 0))
                self.global_gem_attraction_node3.set(config.get("gem_attraction_node3", 0))
                self.global_gem_innovation_node3.set(config.get("gem_innovation_node3", 0))
                self.global_gem_creation_node1.set(config.get("gem_creation_node1", 0))
                self.global_gem_creation_node2.set(config.get("gem_creation_node2", 0))
                self.global_gem_creation_node3.set(config.get("gem_creation_node3", 0))
                self._log("✅ Loaded global bonuses")
            except Exception as e:
                self._log(f"⚠️ Failed to load global bonuses: {e}")
    
    def _populate_control_tab(self):
        """Populate the control tab for running all hunters with dark theme."""
        control_frame = self.control_frame  # Use the pre-created frame
        control_frame.configure(style="Dark.TFrame")
        
        # Split into left (settings) and right (battle arena + run controls)
        left_frame = tk.Frame(control_frame, bg=self.DARK_BG)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        right_frame = tk.Frame(control_frame, bg=self.DARK_BG)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=5, pady=5)
        
        # Battle arena removed for performance
        
        # ============ RUN CONTROLS (below arena) ============
        run_panel = tk.Frame(right_frame, bg=self.DARK_BG_SECONDARY, relief='groove', bd=1)
        run_panel.pack(fill=tk.X, padx=5, pady=5)
        
        run_label = tk.Label(run_panel, text="🚀 Run Optimizations", 
                            bg=self.DARK_BG_SECONDARY, fg=self.DARK_TEXT, font=('Arial', 10, 'bold'))
        run_label.pack(anchor='w', padx=10, pady=(8, 5))
        
        # Run All button row
        btn_frame = tk.Frame(run_panel, bg=self.DARK_BG_SECONDARY)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.run_all_btn = tk.Button(btn_frame, text="🚀 Run ALL Hunters", 
                                     command=self._run_all_hunters,
                                     bg='#2d5a27', fg='white', font=('Arial', 10, 'bold'),
                                     activebackground='#3d7a37', activeforeground='white',
                                     relief='raised', bd=2, padx=10, pady=5)
        self.run_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_all_btn = tk.Button(btn_frame, text="⏹️ Stop All", 
                                      command=self._stop_all_hunters, state=tk.DISABLED,
                                      bg='#8b2500', fg='white', font=('Arial', 10, 'bold'),
                                      activebackground='#ab3500', activeforeground='white',
                                      relief='raised', bd=2, padx=10, pady=5)
        self.stop_all_btn.pack(side=tk.LEFT, padx=5)
        
        # Hunter selection checkboxes
        selection_frame = tk.Frame(run_panel, bg=self.DARK_BG_SECONDARY)
        selection_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(selection_frame, text="Select:", bg=self.DARK_BG_SECONDARY, 
                fg=self.DARK_TEXT_DIM, font=('Arial', 9)).pack(side=tk.LEFT)
        
        self.run_borge_var = tk.BooleanVar(value=True)
        self.run_knox_var = tk.BooleanVar(value=True)
        self.run_ozzy_var = tk.BooleanVar(value=True)
        
        for name, var, color in [("🛡️ Borge", self.run_borge_var, "#DC3545"), 
                                  ("🔫 Knox", self.run_knox_var, "#0D6EFD"),
                                  ("🐙 Ozzy", self.run_ozzy_var, "#198754")]:
            cb = tk.Checkbutton(selection_frame, text=name, variable=var,
                               bg=self.DARK_BG_SECONDARY, fg=color, 
                               selectcolor=self.DARK_BG_TERTIARY, activebackground=self.DARK_BG_SECONDARY,
                               font=('Arial', 9, 'bold'))
            cb.pack(side=tk.LEFT, padx=8)
        
        # Status panel removed - individual hunter tabs show details
        self.leaderboard_labels = {}
        self.hunter_status_frames = {}
        self.all_status = tk.Label(run_panel, text="")  # Dummy for compatibility
        
        # Initialize battle state
        # Battle arena disabled
        # self._init_battle_arena()
        
        # Create scrollable content for left side with dark theme
        canvas = tk.Canvas(left_frame, bg=self.DARK_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas, bg=self.DARK_BG)
        
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Enable mousewheel scrolling
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # ============ GLOBAL SETTINGS ============
        settings_frame = tk.LabelFrame(scrollable, text="⚙️ Global Optimization Settings", 
                                       bg=self.DARK_BG, fg=self.DARK_TEXT, font=('Arial', 10, 'bold'))
        settings_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Simulations per build
        row1 = tk.Frame(settings_frame, bg=self.DARK_BG)
        row1.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(row1, text="Simulations per build:", bg=self.DARK_BG, fg=self.DARK_TEXT).pack(side=tk.LEFT, padx=5)
        self.global_num_sims = tk.IntVar(value=100 if RUST_AVAILABLE else 10)
        tk.Spinbox(row1, textvariable=self.global_num_sims, from_=10, to=1000, increment=10, width=8,
                  bg=self.DARK_BG_SECONDARY, fg=self.DARK_TEXT, insertbackground=self.DARK_TEXT).pack(side=tk.LEFT, padx=5)
        tk.Label(row1, text="(100-500 recommended)", bg=self.DARK_BG, fg=self.DARK_TEXT_DIM,
                font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Builds per tier
        row2 = tk.Frame(settings_frame, bg=self.DARK_BG)
        row2.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row2, text="Builds per tier:").pack(side=tk.LEFT, padx=5)
        self.global_builds_per_tier = tk.IntVar(value=500)
        ttk.Spinbox(row2, textvariable=self.global_builds_per_tier, from_=100, to=10000, increment=100, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="(6 tiers × this = total builds tested)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # CPU processes (Python only)
        row3 = ttk.Frame(settings_frame)
        row3.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row3, text="CPU processes:").pack(side=tk.LEFT, padx=5)
        self.global_num_procs = tk.IntVar(value=16)
        ttk.Spinbox(row3, textvariable=self.global_num_procs, from_=1, to=32, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="(Python only - ignored when using Rust)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Rust engine
        row4 = ttk.Frame(settings_frame)
        row4.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_rust = tk.BooleanVar(value=RUST_AVAILABLE)
        rust_check = ttk.Checkbutton(row4, text="🦀 Use Rust Engine (50-100x faster)", 
                                     variable=self.global_use_rust,
                                     state=tk.NORMAL if RUST_AVAILABLE else tk.DISABLED)
        rust_check.pack(side=tk.LEFT, padx=5)
        
        if RUST_AVAILABLE:
            ttk.Label(row4, text="✅ Rust engine available", 
                     font=('Arial', 9), foreground='green').pack(side=tk.LEFT, padx=10)
        else:
            ttk.Label(row4, text="❌ Rust not found (run 'cargo build --release' in hunter-sim-rs/)", 
                     font=('Arial', 9), foreground='red').pack(side=tk.LEFT, padx=10)
        
        # Progressive evolution
        row5 = ttk.Frame(settings_frame)
        row5.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_progressive = tk.BooleanVar(value=True)
        ttk.Checkbutton(row5, text="📈 Progressive Evolution (5% → 100% points curriculum)", 
                        variable=self.global_use_progressive).pack(side=tk.LEFT, padx=5)
        ttk.Label(row5, text="Finds efficient builds faster by learning at each tier", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Evolutionary optimizer
        row6 = ttk.Frame(settings_frame)
        row6.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_evolutionary = tk.BooleanVar(value=True)
        ttk.Checkbutton(row6, text="🧬 Use Evolutionary Optimizer (learns good/bad patterns)", 
                        variable=self.global_use_evolutionary).pack(side=tk.LEFT, padx=5)
        ttk.Label(row6, text="Recommended for high-level builds", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Apply to all button
        row7 = ttk.Frame(settings_frame)
        row7.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(row7, text="📋 Apply Settings to All Hunters", 
                   command=self._apply_global_settings).pack(side=tk.LEFT, padx=5)
        ttk.Label(row7, text="(Updates each hunter's Run tab)", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # ============ GLOBAL RELICS (shared across all hunters) ============
        relics_frame = ttk.LabelFrame(scrollable, text="🏆 Global Relics (Shared Across All Hunters)")
        relics_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Create two-column grid layout for relics (like hunter builds)
        relics_container = ttk.Frame(relics_frame)
        relics_container.pack(fill=tk.X, padx=10, pady=5)
        relics_container.columnconfigure(0, weight=1)
        relics_container.columnconfigure(1, weight=1)
        
        # Left column relics
        left_relics_frame = ttk.Frame(relics_container)
        left_relics_frame.grid(row=0, column=0, sticky="nsew", padx=5)
        
        # Relic #4 - Disk of Dawn (HP)
        r4_frame = ttk.Frame(left_relics_frame)
        r4_frame.pack(fill=tk.X, pady=2)
        ttk.Label(r4_frame, text="#4 Disk of Dawn (+3% HP):", width=28).pack(side=tk.LEFT)
        self.global_relic_r4 = tk.IntVar(value=0)
        ttk.Spinbox(r4_frame, textvariable=self.global_relic_r4, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(r4_frame, text="/100", width=5).pack(side=tk.LEFT)
        
        # Relic #7 - Manifestation Core (Loot)
        r7_frame = ttk.Frame(left_relics_frame)
        r7_frame.pack(fill=tk.X, pady=2)
        ttk.Label(r7_frame, text="#7 Manifestation Core (1.05x Loot):", width=28).pack(side=tk.LEFT)
        self.global_relic7 = tk.IntVar(value=0)
        ttk.Spinbox(r7_frame, textvariable=self.global_relic7, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(r7_frame, text="/100", width=5).pack(side=tk.LEFT)
        
        # Relic #16 - Long-Range Artillery (ATK for Borge)
        r16_frame = ttk.Frame(left_relics_frame)
        r16_frame.pack(fill=tk.X, pady=2)
        ttk.Label(r16_frame, text="#16 Artillery Crawler (+3% ATK):", width=28).pack(side=tk.LEFT)
        self.global_relic_r16 = tk.IntVar(value=0)
        ttk.Spinbox(r16_frame, textvariable=self.global_relic_r16, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(r16_frame, text="/100", width=5).pack(side=tk.LEFT)
        ttk.Label(r16_frame, text="(Borge)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # Right column relics
        right_relics_frame = ttk.Frame(relics_container)
        right_relics_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        
        # Relic #17 - Bee-gone Drone (ATK for Ozzy)
        r17_frame = ttk.Frame(right_relics_frame)
        r17_frame.pack(fill=tk.X, pady=2)
        ttk.Label(r17_frame, text="#17 Bee-gone Drone (+3% ATK):", width=28).pack(side=tk.LEFT)
        self.global_relic_r17 = tk.IntVar(value=0)
        ttk.Spinbox(r17_frame, textvariable=self.global_relic_r17, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(r17_frame, text="/100", width=5).pack(side=tk.LEFT)
        ttk.Label(r17_frame, text="(Ozzy)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # Relic #19 - Book of Mephisto (XP for Borge)
        r19_frame = ttk.Frame(right_relics_frame)
        r19_frame.pack(fill=tk.X, pady=2)
        ttk.Label(r19_frame, text="#19 Book of Mephisto (2x XP):", width=28).pack(side=tk.LEFT)
        self.global_relic_r19 = tk.IntVar(value=0)
        ttk.Spinbox(r19_frame, textvariable=self.global_relic_r19, from_=0, to=8, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(r19_frame, text="/8", width=5).pack(side=tk.LEFT)
        ttk.Label(r19_frame, text="(Borge)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # ============ GLOBAL BONUSES (shared across all hunters) ============
        bonuses_frame = ttk.LabelFrame(scrollable, text="💎 Global Bonuses (Shared Across All Hunters)")
        bonuses_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Row 1: Core multipliers
        bonuses_row1 = ttk.Frame(bonuses_frame)
        bonuses_row1.pack(fill=tk.X, padx=10, pady=5)
        
        # Shard Milestone (unlimited in game, but we cap at 10000 for UI)
        ttk.Label(bonuses_row1, text="Shard Milestone:").pack(side=tk.LEFT, padx=5)
        self.global_shard_milestone = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row1, textvariable=self.global_shard_milestone, from_=0, to=10000, width=6).pack(side=tk.LEFT, padx=5)
        
        # Research #81
        ttk.Label(bonuses_row1, text="Research #81:").pack(side=tk.LEFT, padx=15)
        self.global_research81 = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row1, textvariable=self.global_research81, from_=0, to=6, width=5).pack(side=tk.LEFT, padx=5)
        
        # Row 2: Loop mods
        bonuses_row2 = ttk.Frame(bonuses_frame)
        bonuses_row2.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(bonuses_row2, text="Loop Mods:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        
        # Scavenger's Advantage (Borge)
        ttk.Label(bonuses_row2, text="Scavenger (Borge):").pack(side=tk.LEFT, padx=10)
        self.global_scavenger = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row2, textvariable=self.global_scavenger, from_=0, to=25, width=5).pack(side=tk.LEFT, padx=5)
        
        # Scavenger's Advantage 2 (Ozzy)
        ttk.Label(bonuses_row2, text="Scavenger 2 (Ozzy):").pack(side=tk.LEFT, padx=10)
        self.global_scavenger2 = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row2, textvariable=self.global_scavenger2, from_=0, to=25, width=5).pack(side=tk.LEFT, padx=5)
        
        # Row 3: Construction Milestones
        bonuses_row3 = ttk.Frame(bonuses_frame)
        bonuses_row3.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(bonuses_row3, text="CMs:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        
        self.global_cm46 = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row3, text="CM46 (1.03x)", variable=self.global_cm46).pack(side=tk.LEFT, padx=10)
        
        self.global_cm47 = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row3, text="CM47 (1.02x)", variable=self.global_cm47).pack(side=tk.LEFT, padx=10)
        
        self.global_cm48 = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row3, text="CM48 (1.07x)", variable=self.global_cm48).pack(side=tk.LEFT, padx=10)
        
        self.global_cm51 = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row3, text="CM51 (1.05x)", variable=self.global_cm51).pack(side=tk.LEFT, padx=10)
        
        # Row 4: Diamond bonuses
        bonuses_row4 = ttk.Frame(bonuses_frame)
        bonuses_row4.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(bonuses_row4, text="💎 Diamond:", font=('Arial', 9, 'bold')).pack(side=tk.LEFT, padx=5)
        
        # Diamond Loot Booster
        ttk.Label(bonuses_row4, text="Loot Booster:").pack(side=tk.LEFT, padx=10)
        self.global_diamond_loot = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row4, textvariable=self.global_diamond_loot, from_=0, to=10, width=5).pack(side=tk.LEFT, padx=5)
        
        # Diamond Cards
        self.global_gaiden_card = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row4, text="Gaiden Card (Borge 1.05x)", variable=self.global_gaiden_card).pack(side=tk.LEFT, padx=10)
        
        self.global_iridian_card = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row4, text="Iridian Card (Ozzy 1.05x)", variable=self.global_iridian_card).pack(side=tk.LEFT, padx=10)
        
        # Row 5: IAP and Ultima
        bonuses_row5 = ttk.Frame(bonuses_frame)
        bonuses_row5.pack(fill=tk.X, padx=10, pady=5)
        
        # IAP Pack
        self.global_iap_travpack = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row5, text="IAP Traversal Pack (1.25x)", variable=self.global_iap_travpack).pack(side=tk.LEFT, padx=5)
        
        # Ultima Multiplier
        ttk.Label(bonuses_row5, text="Ultima Multiplier:").pack(side=tk.LEFT, padx=15)
        self.global_ultima_multiplier = tk.DoubleVar(value=1.0)
        ttk.Entry(bonuses_row5, textvariable=self.global_ultima_multiplier, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(bonuses_row5, text="(Enter displayed value, e.g. 1.5 for 50% boost)", 
                  font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=5)
        
        # ============ GLOBAL GEMS (shared across all hunters) ============
        gems_frame = ttk.LabelFrame(scrollable, text="💎 Global Gems (Shared Across All Hunters)")
        gems_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Create two-column grid layout for gems
        gems_container = ttk.Frame(gems_frame)
        gems_container.pack(fill=tk.X, padx=10, pady=5)
        gems_container.columnconfigure(0, weight=1)
        gems_container.columnconfigure(1, weight=1)
        
        # Left column gems
        left_gems_frame = ttk.Frame(gems_container)
        left_gems_frame.grid(row=0, column=0, sticky="nsew", padx=5)
        
        # Attraction Gem Level
        gem1_frame = ttk.Frame(left_gems_frame)
        gem1_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem1_frame, text="Attraction Gem:", width=22).pack(side=tk.LEFT)
        self.global_gem_attraction = tk.IntVar(value=0)
        ttk.Spinbox(gem1_frame, textvariable=self.global_gem_attraction, from_=0, to=3, width=5).pack(side=tk.LEFT, padx=5)
        
        # Loot (Borge) - CRITICAL MULTIPLIER!
        loot_borge_frame = ttk.Frame(left_gems_frame)
        loot_borge_frame.pack(fill=tk.X, pady=2)
        ttk.Label(loot_borge_frame, text="Loot (Borge):", width=22).pack(side=tk.LEFT)
        self.global_gem_loot_borge = tk.IntVar(value=0)
        ttk.Spinbox(loot_borge_frame, textvariable=self.global_gem_loot_borge, from_=0, to=50, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(loot_borge_frame, text="(1.07^lvl)", font=('Arial', 8), foreground='red').pack(side=tk.LEFT, padx=2)
        
        # Loot (Ozzy) - CRITICAL MULTIPLIER!
        loot_ozzy_frame = ttk.Frame(left_gems_frame)
        loot_ozzy_frame.pack(fill=tk.X, pady=2)
        ttk.Label(loot_ozzy_frame, text="Loot (Ozzy):", width=22).pack(side=tk.LEFT)
        self.global_gem_loot_ozzy = tk.IntVar(value=0)
        ttk.Spinbox(loot_ozzy_frame, textvariable=self.global_gem_loot_ozzy, from_=0, to=50, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(loot_ozzy_frame, text="(1.07^lvl)", font=('Arial', 8), foreground='green').pack(side=tk.LEFT, padx=2)
        
        # Attraction Catch-Up
        gem2_frame = ttk.Frame(left_gems_frame)
        gem2_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem2_frame, text="Catch-Up Power:", width=22).pack(side=tk.LEFT)
        self.global_gem_catchup = tk.IntVar(value=0)
        ttk.Spinbox(gem2_frame, textvariable=self.global_gem_catchup, from_=0, to=5, width=5).pack(side=tk.LEFT, padx=5)
        
        # Attraction Node #3
        gem3_frame = ttk.Frame(left_gems_frame)
        gem3_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem3_frame, text="Attraction Node #3:", width=22).pack(side=tk.LEFT)
        self.global_gem_attraction_node3 = tk.IntVar(value=0)
        ttk.Spinbox(gem3_frame, textvariable=self.global_gem_attraction_node3, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        
        # Innovation Node #3
        gem4_frame = ttk.Frame(left_gems_frame)
        gem4_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem4_frame, text="Innovation Node #3:", width=22).pack(side=tk.LEFT)
        self.global_gem_innovation_node3 = tk.IntVar(value=0)
        ttk.Spinbox(gem4_frame, textvariable=self.global_gem_innovation_node3, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        
        # Right column gems (Borge-only Creation nodes)
        right_gems_frame = ttk.Frame(gems_container)
        right_gems_frame.grid(row=0, column=1, sticky="nsew", padx=5)
        
        # Creation Node #1
        gem5_frame = ttk.Frame(right_gems_frame)
        gem5_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem5_frame, text="Creation Node #1:", width=22).pack(side=tk.LEFT)
        self.global_gem_creation_node1 = tk.IntVar(value=0)
        ttk.Spinbox(gem5_frame, textvariable=self.global_gem_creation_node1, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(gem5_frame, text="(Borge)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # Creation Node #2
        gem6_frame = ttk.Frame(right_gems_frame)
        gem6_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem6_frame, text="Creation Node #2:", width=22).pack(side=tk.LEFT)
        self.global_gem_creation_node2 = tk.IntVar(value=0)
        ttk.Spinbox(gem6_frame, textvariable=self.global_gem_creation_node2, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(gem6_frame, text="(Borge)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # Creation Node #3
        gem7_frame = ttk.Frame(right_gems_frame)
        gem7_frame.pack(fill=tk.X, pady=2)
        ttk.Label(gem7_frame, text="Creation Node #3:", width=22).pack(side=tk.LEFT)
        self.global_gem_creation_node3 = tk.IntVar(value=0)
        ttk.Spinbox(gem7_frame, textvariable=self.global_gem_creation_node3, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(gem7_frame, text="(Borge)", font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=2)
        
        # Load saved global bonuses (after all fields created, before traces)
        self._load_global_bonuses()
        
        # Set up auto-save traces for all bonus, relic, and gem fields
        for var in [self.global_shard_milestone, self.global_relic7, self.global_research81,
                    self.global_scavenger, self.global_scavenger2,
                    self.global_cm46, self.global_cm47, self.global_cm48, self.global_cm51,
                    self.global_diamond_loot, self.global_gaiden_card, self.global_iridian_card,
                    self.global_iap_travpack, self.global_ultima_multiplier,
                    self.global_relic_r4, self.global_relic_r16, self.global_relic_r17, self.global_relic_r19,
                    self.global_gem_attraction, self.global_gem_loot_borge, self.global_gem_loot_ozzy,
                    self.global_gem_catchup, self.global_gem_attraction_node3,
                    self.global_gem_innovation_node3, self.global_gem_creation_node1, 
                    self.global_gem_creation_node2, self.global_gem_creation_node3]:
            var.trace_add("write", self._save_global_bonuses)
        
        # Start battle animation loop
        self.battle_frame = 0
        self._animate_battles()
        
        # Save All button - dark themed
        save_frame = tk.LabelFrame(scrollable, text="💾 Save & Share Builds",
                                   bg=self.DARK_BG, fg=self.DARK_TEXT, font=('Arial', 10, 'bold'))
        save_frame.pack(fill=tk.X, padx=20, pady=10)
        
        btn_row = tk.Frame(save_frame, bg=self.DARK_BG)
        btn_row.pack(pady=10)
        
        save_btn = tk.Button(btn_row, text="💾 Save All Builds", 
                            command=self._save_all_builds,
                            bg=self.DARK_ACCENT, fg=self.DARK_TEXT,
                            font=('Arial', 10), padx=10, pady=5)
        save_btn.pack(side=tk.LEFT, padx=5)
        
        open_btn = tk.Button(btn_row, text="📁 Open IRL Builds Folder", 
                            command=self._open_irl_builds_folder,
                            bg=self.DARK_BG_SECONDARY, fg=self.DARK_TEXT,
                            font=('Arial', 10), padx=10, pady=5)
        open_btn.pack(side=tk.LEFT, padx=5)
    
    def _animate_battles(self):
        """Animate hunter battle scenes when running."""
        import random
        
        # Only animate when on a hunter tab that's running (reduce CPU when not visible)
        try:
            # Check if notebook exists yet (may not during initialization)
            if not hasattr(self, 'main_notebook') or self.main_notebook is None:
                self.root.after(1000, self._animate_battles)
                return
            
            # Cache control tab index to avoid repeated lookups
            if not hasattr(self, '_control_tab_index'):
                self._control_tab_index = self.main_notebook.index(self.control_frame)
            
            current_tab = self.main_notebook.index(self.main_notebook.select())
            
            # Check if any hunter is actually running
            any_running = any(tab.is_running for tab in self.hunter_tabs.values())
            
            if current_tab == self._control_tab_index or not any_running:
                # On Control tab or no simulations running - slow down animation
                self.root.after(1000, self._animate_battles)
                return
        except (tk.TclError, ValueError, AttributeError):
            # Tab lookup failed or notebook not ready, slow poll
            self.root.after(1000, self._animate_battles)
            return
        
        # Update status panel with running info
        for hunter_name, tab in self.hunter_tabs.items():
            if hunter_name in self.hunter_status_frames:
                status_info = self.hunter_status_frames[hunter_name]
                if tab.is_running:
                    # Show progress
                    progress_pct = tab.progress_var.get() if hasattr(tab, 'progress_var') else 0
                    status_info['progress_var'].set(progress_pct)
                    status_info['status_label'].configure(text=f"Running... {progress_pct:.0f}%", fg='#ffcc00')
                elif tab.results:
                    # Show result
                    best = tab.results[0] if tab.results else None
                    if best:
                        status_info['progress_var'].set(100)
                        status_info['status_label'].configure(
                            text=f"Stage {best.avg_final_stage:.0f} | {best.avg_loot_per_hour:.0f} loot/hr", 
                            fg='#00ff00')
                else:
                    # Idle
                    status_info['progress_var'].set(0)
                    status_info['status_label'].configure(text="Waiting...", fg=self.DARK_TEXT_DIM)
        
        # Schedule next frame (1 second)
        self.root.after(1000, self._animate_battles)
    
    def _log_arena(self, msg: str):
        """Log a message to the arena - shows briefly in effects."""
        pass  # Silent for now - can add visual log later
    
    def _init_battle_arena(self):
        """Initialize the battle arena with static hunter PNG and enemy emoji."""
        import random
        
        self.arena_width = 350
        self.arena_height = 300  # Shorter - simpler layout
        
        # Static positions for simplified arena
        self.hunter_pos = {"x": 80, "y": 150}   # Hunter on left
        self.enemy_pos = {"x": 270, "y": 150}   # Enemy on right
        
        # Load hunter PNG images (scaled down for arena)
        self.hunter_images = {}
        self.hunter_photo_images = {}  # Keep reference to prevent garbage collection
        
        # PNG file paths - in assets subfolder
        import os
        # Directory where this file lives
        base = os.path.dirname(__file__)
        # PNGs are in: <repo>/hunter-sim/hunter-sim/assets
        assets_dir = os.path.join(base, "assets")
        png_files = {
            "Borge": os.path.join(assets_dir, "borge.png"),
            "Knox": os.path.join(assets_dir, "knox.png"),
            "Ozzy": os.path.join(assets_dir, "ozzy.png"),
        }

        
        # Try to load PNG images
        if PIL_AVAILABLE:
            for hunter_name, path in png_files.items():
                try:
                    if os.path.exists(path):
                        img = Image.open(path)
                        # Scale to 80x80 for arena display
                        img = img.resize((80, 80), Image.Resampling.LANCZOS)
                        self.hunter_photo_images[hunter_name] = ImageTk.PhotoImage(img)
                        self._log(f"📷 Loaded {hunter_name} portrait")
                except Exception as e:
                    self._log(f"⚠️ Could not load {hunter_name} image: {e}")
        
        # Hunter-specific arena themes
        self.arena_themes = {
            "Borge": {
                "bg_color": "#1a0a0a",
                "accent_color": "#DC3545",
                "field_color": "#2a0505",
                "description": "Exon-12"
            },
            "Knox": {
                "bg_color": "#0a0a1a",
                "accent_color": "#0D6EFD",
                "field_color": "#050520",
                "description": "Sirene-6"
            },
            "Ozzy": {
                "bg_color": "#0a1a0a",
                "accent_color": "#198754",
                "field_color": "#052005",
                "description": "Endo Prime"
            }
        }
        
        # Current enemy state for display
        self.current_enemy = {
            "icon": "👹",
            "hp_percent": 100,
            "is_boss": False,
            "color": "#90EE90"  # Changes with difficulty
        }
        
        # Battle effects for animations
        self.arena_effects = []
        
        # Arena state - 10 enemies per stage like the game
        self.arena_stage = 1
        self.arena_enemy_index = 0  # 0-9 within current stage (10 enemies per stage)
        self.arena_target_stage = 0  # Target stage based on optimization progress
        self.arena_visual_stage = 0.0  # Smooth interpolation to target
        self.victory_mode = False
        self.victory_timer = 0
        
        # Track which hunters were running last frame
        self.prev_running = {"Borge": False, "Knox": False, "Ozzy": False}
        
        # Enemy icons by stage difficulty (each stage cycles through these)
        self.enemy_icons = ['👹', '👺', '👻', '💀', '🐉', '👾', '🤖', '🦇', '🕷️', '🦂']
        self.boss_icons = ['🐲', '👿', '☠️', '🦖', '👑', '🎃']
        
        # Live simulation state (simplified - now syncs with optimization)
        self.live_sim_hunter = None
        self.live_sim_stage = 0
        self.live_sim_hp_percent = 100
        self.live_sim_final_stage = 0
        self.live_sim_running = False
        
        # For compatibility
        self.arena_hunters = {
            "Borge": {"hp": 100, "max_hp": 100, "kills": 0, "color": "#DC3545"},
            "Knox": {"hp": 100, "max_hp": 100, "kills": 0, "color": "#0D6EFD"},
            "Ozzy": {"hp": 100, "max_hp": 100, "kills": 0, "color": "#198754"},
        }
        
        # Start arena animation
        self._animate_arena()
    
    def _start_live_simulation(self, hunter_name: str):
        """Start arena visualization synced with optimization progress.
        
        Instead of running a separate simulation, we sync the visual with
        the optimization's progress and best_avg_stage.
        """
        self.live_sim_hunter = hunter_name
        self.live_sim_stage = 0
        self.live_sim_hp_percent = 100
        self.live_sim_final_stage = 0
        self.live_sim_running = True
        
        # Reset arena state
        self.arena_stage = 1
        self.arena_enemy_index = 0
        self.arena_target_stage = 0
        self.arena_visual_stage = 0.0
        
        self._log_arena(f"🎬 {hunter_name} entering the arena!")
    
    def _update_arena_from_optimization(self, hunter_name: str):
        """Update arena visuals based on optimization progress."""
        tab = self.hunter_tabs.get(hunter_name)
        if not tab or not tab._content_initialized:
            return
        
        # Get current best avg stage from optimization
        target_stage = tab.best_avg_stage if tab.best_avg_stage > 0 else 0
        
        # Get optimization progress (0-100%)
        if hasattr(tab, 'progress_var'):
            progress = tab.progress_var.get()
        else:
            progress = 0
        
        # If optimization is running and we have results, interpolate to target
        if tab.is_running and target_stage > 0:
            # Scale visual stage based on progress
            # Start slow (builds tension), speed up as we get more data
            progress_factor = min(1.0, progress / 80)  # Reach target at 80% progress
            self.arena_target_stage = target_stage * progress_factor
        elif not tab.is_running and self.live_sim_running:
            # Optimization just finished - rush to final stage
            self.arena_target_stage = target_stage
        
        # Smooth interpolation towards target
        stage_diff = self.arena_target_stage - self.arena_visual_stage
        if abs(stage_diff) > 0.1:
            # Move towards target - faster when further away
            speed = max(0.5, abs(stage_diff) * 0.1)
            self.arena_visual_stage += speed if stage_diff > 0 else -speed
        else:
            self.arena_visual_stage = self.arena_target_stage
        
        # Convert visual stage to integer stage + enemy index (10 enemies per stage)
        total_enemies = self.arena_visual_stage * 10  # 10 enemies per stage
        self.live_sim_stage = int(self.arena_visual_stage) + 1  # Stages are 1-indexed
        self.arena_enemy_index = int(total_enemies) % 10  # Which of 10 enemies
        
        # Simulate HP fluctuations based on stage (higher stage = lower HP on average)
        if self.arena_visual_stage > 0:
            import random
            base_hp = max(10, 100 - (self.arena_visual_stage * 0.5))
            self.live_sim_hp_percent = base_hp + random.uniform(-10, 10)
            self.live_sim_hp_percent = max(5, min(100, self.live_sim_hp_percent))
    
    def _add_arena_effect(self, icon: str, x: float, y: float, ttl: int = 15):
        """Add a visual effect to the arena."""
        if hasattr(self, 'arena_effects'):
            self.arena_effects.append({
                "icon": icon, "x": x, "y": y, "ttl": ttl, "alpha": 1.0
            })
    
    def _log_arena(self, message: str):
        """Log arena-related messages."""
        if hasattr(self, 'global_log'):
            try:
                self.global_log.configure(state=tk.NORMAL)
                timestamp = time.strftime("%H:%M:%S")
                self.global_log.insert(tk.END, f"[{timestamp}] 🎮 {message}\n")
                self.global_log.see(tk.END)
                self.global_log.configure(state=tk.DISABLED)
            except:
                pass

    def _animate_arena(self):
        """Arena showing hunter vs enemies with 10 enemies per stage at the bottom."""
        import random
        
        # Only animate when Control tab is visible
        try:
            if not hasattr(self, 'main_notebook') or self.main_notebook is None:
                self.root.after(500, self._animate_arena)
                return
            
            # Cache control tab index to avoid repeated lookups
            if not hasattr(self, '_control_tab_index'):
                self._control_tab_index = self.main_notebook.index(self.control_frame)
            
            current_tab = self.main_notebook.index(self.main_notebook.select())
            if current_tab != self._control_tab_index:
                self.root.after(1000, self._animate_arena)
                return
        except (tk.TclError, ValueError, AttributeError):
            self.root.after(1000, self._animate_arena)
            return
        
        # Check if any hunter is running
        any_running = any(tab.is_running for tab in self.hunter_tabs.values())
        running_hunters = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        # Update arena from optimization progress
        if self.live_sim_running and running_hunters:
            self._update_arena_from_optimization(running_hunters[0])
        
        canvas = self.battle_canvas
        canvas.delete("all")
        
        # Determine which hunter to show (first running, or last one that ran)
        active_hunter = None
        if running_hunters:
            active_hunter = running_hunters[0]
        elif self.live_sim_hunter:
            active_hunter = self.live_sim_hunter
        
        # Get theme
        if active_hunter:
            theme = self.arena_themes.get(active_hunter, self.arena_themes["Borge"])
        else:
            theme = {"bg_color": "#1a1a2e", "accent_color": "#888888", "field_color": "#15152e", "description": "Waiting"}
        
        # Draw background
        canvas.configure(bg=theme["bg_color"])
        
        # Draw battle field area (upper portion)
        field_top = 35
        field_bottom = self.arena_height - 60
        canvas.create_rectangle(10, field_top, self.arena_width - 10, field_bottom,
                               fill=theme["field_color"], outline=theme["accent_color"], width=2)
        
        # Draw header with stage info
        if any_running and active_hunter:
            stage_text = f"⚔️ Stage {self.live_sim_stage} - {theme['description']} ⚔️"
            canvas.create_text(self.arena_width // 2, 18, text=stage_text,
                              fill=theme["accent_color"], font=('Arial', 10, 'bold'))
        else:
            canvas.create_text(self.arena_width // 2, 18, text="🏰 Waiting for Battle 🏰",
                              fill='#888888', font=('Arial', 11, 'bold'))
        
        # === DRAW HUNTER (left side of battle field) ===
        hunter_x, hunter_y = 100, (field_top + field_bottom) // 2
        
        if active_hunter and active_hunter in self.hunter_photo_images:
            try:
                canvas.create_image(hunter_x, hunter_y, image=self.hunter_photo_images[active_hunter], anchor='center')
            except:
                canvas.create_text(hunter_x, hunter_y, text=active_hunter[0], 
                                  font=('Arial', 40, 'bold'), fill=theme["accent_color"])
        elif active_hunter:
            canvas.create_oval(hunter_x - 35, hunter_y - 35, hunter_x + 35, hunter_y + 35,
                              fill=theme["accent_color"], outline='#FFFFFF', width=3)
            canvas.create_text(hunter_x, hunter_y, text=active_hunter[0],
                              font=('Arial', 30, 'bold'), fill='#FFFFFF')
        else:
            # Idle state - show all three hunter portraits stacked
            for i, name in enumerate(["Borge", "Knox", "Ozzy"]):
                y_offset = (i - 1) * 50
                if name in self.hunter_photo_images:
                    try:
                        canvas.create_image(hunter_x, hunter_y + y_offset, 
                                          image=self.hunter_photo_images[name], anchor='center')
                    except:
                        color = self.arena_themes[name]["accent_color"]
                        canvas.create_oval(hunter_x - 25, hunter_y + y_offset - 25, 
                                          hunter_x + 25, hunter_y + y_offset + 25,
                                          fill=color, outline='#666666', width=2)
        
        # Draw HP bar under hunter
        if any_running:
            bar_width = 70
            bar_y = hunter_y + 48
            hp_pct = self.live_sim_hp_percent / 100
            canvas.create_rectangle(hunter_x - bar_width//2, bar_y, hunter_x + bar_width//2, bar_y + 8,
                                   fill='#333333', outline='#555555')
            hp_color = '#00FF00' if hp_pct > 0.5 else '#FFFF00' if hp_pct > 0.25 else '#FF0000'
            canvas.create_rectangle(hunter_x - bar_width//2, bar_y, 
                                   hunter_x - bar_width//2 + bar_width * hp_pct, bar_y + 8,
                                   fill=hp_color, outline='')
        
        # === DRAW CURRENT ENEMY (right side of battle field) ===
        enemy_x, enemy_y = self.arena_width - 100, (field_top + field_bottom) // 2
        
        if any_running:
            stage = self.live_sim_stage
            # Enemy color by stage difficulty
            if stage < 50:
                enemy_color = "#90EE90"
            elif stage < 100:
                enemy_color = "#FFD700"
            elif stage < 150:
                enemy_color = "#FF8C00"
            elif stage < 200:
                enemy_color = "#FF4500"
            else:
                enemy_color = "#FF00FF"
            
            # Pick enemy icon based on current enemy index
            is_boss = stage > 0 and stage % 100 == 0
            enemy_idx = self.arena_enemy_index
            if is_boss:
                enemy_icon = self.boss_icons[(stage // 100) % len(self.boss_icons)]
                font_size = 45
                canvas.create_text(enemy_x, enemy_y - 40, text="👑 BOSS 👑",
                                  font=('Arial', 9, 'bold'), fill='#FFD700')
            else:
                enemy_icon = self.enemy_icons[enemy_idx % len(self.enemy_icons)]
                font_size = 35
            
            canvas.create_text(enemy_x, enemy_y, text=enemy_icon,
                              font=('Segoe UI Emoji', font_size), fill=enemy_color)
            
            # Show which enemy (e.g., "Enemy 3/10")
            canvas.create_text(enemy_x, enemy_y + 40, text=f"Enemy {enemy_idx + 1}/10",
                              font=('Arial', 8), fill='#888888')
        else:
            canvas.create_text(enemy_x, enemy_y, text="💤",
                              font=('Segoe UI Emoji', 30), fill='#666666')
        
        # === DRAW BATTLE EFFECTS (center) ===
        effect_x = self.arena_width // 2
        effect_y = (field_top + field_bottom) // 2
        
        if any_running:
            tick = int(time.time() * 3) % 4
            attack_icons = ['⚔️', '💥', '✨', '🔥']
            canvas.create_text(effect_x, effect_y, text=attack_icons[tick],
                              font=('Segoe UI Emoji', 20))
            canvas.create_text(effect_x - 25, effect_y, text="→",
                              font=('Arial', 16, 'bold'), fill=theme["accent_color"])
            canvas.create_text(effect_x + 25, effect_y, text="←",
                              font=('Arial', 16, 'bold'), fill='#FF6666')
        
        # === DRAW 10 ENEMIES AT BOTTOM (stage queue) ===
        queue_y = self.arena_height - 30
        queue_start_x = 40
        queue_spacing = (self.arena_width - 80) // 10
        
        if any_running:
            stage = self.live_sim_stage
            current_enemy_idx = self.arena_enemy_index
            
            # Draw enemy queue label
            canvas.create_text(self.arena_width // 2, field_bottom + 8, 
                              text=f"Stage {stage} Queue", font=('Arial', 8), fill='#666666')
            
            for i in range(10):
                x = queue_start_x + i * queue_spacing + queue_spacing // 2
                
                if i < current_enemy_idx:
                    # Defeated - draw skull
                    canvas.create_text(x, queue_y, text="💀",
                                      font=('Segoe UI Emoji', 14), fill='#444444')
                elif i == current_enemy_idx:
                    # Current enemy - highlighted
                    enemy_icon = self.enemy_icons[i % len(self.enemy_icons)]
                    canvas.create_rectangle(x - 15, queue_y - 15, x + 15, queue_y + 15,
                                           outline='#FFD700', width=2)
                    canvas.create_text(x, queue_y, text=enemy_icon,
                                      font=('Segoe UI Emoji', 16))
                else:
                    # Waiting enemies
                    enemy_icon = self.enemy_icons[i % len(self.enemy_icons)]
                    canvas.create_text(x, queue_y, text=enemy_icon,
                                      font=('Segoe UI Emoji', 12), fill='#666666')
        else:
            # Idle - show empty slots
            canvas.create_text(self.arena_width // 2, queue_y, 
                              text="👹 👺 👻 💀 🐉 👾 🤖 🦇 🕷️ 🦂",
                              font=('Segoe UI Emoji', 12), fill='#444444')
        
        # === DEFEAT MODE ===
        if self.victory_mode:
            self.victory_timer -= 1
            if self.victory_timer <= 0:
                self.victory_mode = False
            else:
                canvas.create_text(self.arena_width // 2, (field_top + field_bottom) // 2 - 20,
                                  text="💀 DEFEATED 💀", font=('Arial', 16, 'bold'), fill='#FF6B6B')
                if self.live_sim_final_stage > 0:
                    canvas.create_text(self.arena_width // 2, (field_top + field_bottom) // 2 + 5,
                                      text=f"Fell at Stage {self.live_sim_final_stage}",
                                      font=('Arial', 12), fill='#FFFFFF')
        
        # === TRACK HUNTER START/STOP ===
        for name in ["Borge", "Knox", "Ozzy"]:
            tab = self.hunter_tabs[name]
            was_running = self.prev_running.get(name, False)
            is_running = tab.is_running
            
            # Battle arena disabled - just track state
            if is_running and not was_running:
                pass  # Hunter just started
                
            elif not is_running and was_running:
                pass  # Hunter just finished
                
                # Update leaderboard
                if tab._content_initialized and tab.results:
                    best_result = max(tab.results, key=lambda r: r.avg_final_stage)
                    hunter_data = self.arena_hunters.get(name, {})
                    hunter_data["last_avg_stage"] = best_result.avg_final_stage
                    hunter_data["last_max_stage"] = best_result.highest_stage
                    hunter_data["last_gen"] = len(tab.results)
                    self._update_leaderboard(name, hunter_data)
            
            self.prev_running[name] = is_running
        
        # Schedule next frame (faster when running)
        delay = 100 if any_running else 500
        self.root.after(delay, self._animate_arena)
    
    def _update_leaderboard(self, hunter_name: str, hunter: dict):
        """Update the leaderboard with completed run results."""
        if hunter_name not in self.leaderboard_labels:
            return
        
        # Get full results from the hunter tab
        tab = self.hunter_tabs.get(hunter_name)
        if tab and tab.results:
            best = max(tab.results, key=lambda r: r.avg_final_stage)
            
            # Store data for display
            self.leaderboard_data[hunter_name] = {
                "avg_stage": best.avg_final_stage,
                "max_stage": best.highest_stage,
                "gen": len(tab.results),
            }
        
        # Update display (static, no cycling)
        self._refresh_leaderboard_display()
    
    def _refresh_leaderboard_display(self):
        """Refresh the leaderboard display with stage info only."""
        for hunter_name, label in self.leaderboard_labels.items():
            data = self.leaderboard_data.get(hunter_name)
            
            if not data:
                label.configure(text="Waiting...", fg='#888888')
                continue
            
            # Simple static display: avg stage, max stage, generation
            text = f"Stage: {data['avg_stage']:.1f} avg | {data['max_stage']} max | Gen {data['gen']}"
            label.configure(text=text, fg='#FFFFFF')
    
    def _format_number_short(self, num: float) -> str:
        """Format number with K/M/B suffix for compact display."""
        if num < 1000:
            return f"{num:.0f}"
        elif num < 1_000_000:
            return f"{num/1000:.1f}K"
        elif num < 1_000_000_000:
            return f"{num/1_000_000:.1f}M"
        else:
            return f"{num/1_000_000_000:.1f}B"
    
    def _apply_global_settings(self):
        """Apply global settings to all hunter tabs."""
        for name, tab in self.hunter_tabs.items():
            tab.num_sims.set(self.global_num_sims.get())
            tab.builds_per_tier.set(self.global_builds_per_tier.get())
            tab.use_rust.set(self.global_use_rust.get())
            tab.use_progressive.set(self.global_use_progressive.get())
        self._log("📋 Applied global settings to all hunters")
        self.all_status.configure(text="Settings applied to all hunters!")
    
    def _run_single_hunter(self, hunter_name: str):
        """Run optimization for a single hunter."""
        # Apply global settings first
        tab = self.hunter_tabs[hunter_name]
        tab.num_sims.set(self.global_num_sims.get())
        tab.builds_per_tier.set(self.global_builds_per_tier.get())
        tab.use_rust.set(self.global_use_rust.get())
        tab.use_progressive.set(self.global_use_progressive.get())
        
        if not tab.is_running:
            self._log(f"🚀 Starting {hunter_name} optimization...")
            tab._start_optimization()
            self._update_hunter_status()
            self.root.after(1000, self._check_all_complete)
    
    def _update_hunter_status(self):
        """Update individual hunter status labels and progress bars."""
        total_progress = 0
        running_count = 0
        
        for name, tab in self.hunter_tabs.items():
            icon = '🛡️' if name=='Borge' else '🔫' if name=='Knox' else '🐙'
            
            # Use the new hunter_status_frames structure
            if name in self.hunter_status_frames:
                status_info = self.hunter_status_frames[name]
                progress_var = status_info['progress_var']
                status_label = status_info['status_label']
                
                if tab.is_running:
                    # Get progress from the tab's progress var
                    pct = tab.progress_var.get()
                    progress_var.set(pct)
                    total_progress += pct
                    running_count += 1
                    
                    # Calculate ETA
                    eta_str = ""
                    if tab.optimization_start_time > 0:
                        elapsed = time.time() - tab.optimization_start_time
                        if pct > 0:
                            total_time = elapsed / (pct / 100)
                            remaining = total_time - elapsed
                            if remaining < 60:
                                eta_str = f" ({remaining:.0f}s)"
                            elif remaining < 3600:
                                eta_str = f" ({remaining/60:.1f}m)"
                            else:
                                eta_str = f" ({remaining/3600:.1f}h)"
                    
                    status_label.configure(text=f"⏳ Running... {pct:.0f}%{eta_str}", fg='#ffcc00')
                elif tab.results:
                    best = max(tab.results, key=lambda r: r.avg_final_stage).avg_final_stage
                    progress_var.set(100)
                    status_label.configure(text=f"✅ Stage {best:.1f}", fg='#00ff00')
                else:
                    progress_var.set(0)
                    status_label.configure(text="Waiting...", fg=self.DARK_TEXT_DIM)
        
        # Update hunter progress state for color-coded bar
        for name, tab in self.hunter_tabs.items():
            if name in self.hunter_progress_state:
                if tab.is_running:
                    self.hunter_progress_state[name]["progress"] = tab.progress_var.get()
                    self.hunter_progress_state[name]["complete"] = False
                elif tab.results:
                    self.hunter_progress_state[name]["progress"] = 100
                    self.hunter_progress_state[name]["complete"] = True
                else:
                    self.hunter_progress_state[name]["progress"] = 0
                    self.hunter_progress_state[name]["complete"] = False
        
        # Draw color-coded progress bar
        self._draw_global_progress()
        
        # Update global ETA label
        if running_count > 0:
            self.global_eta.configure(text=f"Running {running_count}/3 hunters...")
        elif any(tab.results for tab in self.hunter_tabs.values()):
            self.global_eta.configure(text="✅ All complete!")
        else:
            self.global_eta.configure(text="Ready")
    
    def _create_log_frame(self):
        """Create a global log and progress bar at the bottom with dark theme."""
        # Container for log + progress
        bottom_frame = ttk.Frame(self.root, style='Dark.TFrame')
        bottom_frame.pack(fill=tk.X, padx=10, pady=5, side=tk.BOTTOM)
        
        # Global progress bar
        progress_frame = ttk.LabelFrame(bottom_frame, text="📊 Global Progress", style='Dark.TLabelframe')
        progress_frame.pack(fill=tk.X, pady=2)
        
        progress_inner = ttk.Frame(progress_frame, style='Dark.TFrame')
        progress_inner.pack(fill=tk.X, padx=10, pady=5)
        
        # Hunter-colored progress canvas (3 segments)
        self.global_progress_canvas = tk.Canvas(progress_inner, height=20, bg='#1a1a2e', 
                                                 highlightthickness=1, highlightbackground='#3d3d5c')
        self.global_progress_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        # Track progress for each hunter: {name: {progress: 0-100, complete: bool}}
        self.hunter_progress_state = {
            "Borge": {"progress": 0, "complete": False, "color": "#DC3545"},
            "Ozzy": {"progress": 0, "complete": False, "color": "#198754"},
            "Knox": {"progress": 0, "complete": False, "color": "#0D6EFD"},
        }
        
        self.global_eta = ttk.Label(progress_inner, text="Ready", width=30, style='Dark.TLabel')
        self.global_eta.pack(side=tk.LEFT, padx=5)
        
        # Log
        log_frame = ttk.LabelFrame(bottom_frame, text="📋 Global Log", style='Dark.TLabelframe')
        log_frame.pack(fill=tk.X, pady=2)
        
        self.global_log = scrolledtext.ScrolledText(
            log_frame, height=4, state=tk.DISABLED, font=('Consolas', 9),
            bg=self.DARK_BG_TERTIARY, fg=self.DARK_TEXT, insertbackground=self.DARK_TEXT
        )
        self.global_log.pack(fill=tk.X, padx=5, pady=5)
        
        # Configure color tags for global log
        self._configure_global_log_tags()
    
    def _configure_global_log_tags(self):
        """Configure colorful text tags for the global log."""
        log = self.global_log
        log.tag_configure("timestamp", foreground="#888899")
        log.tag_configure("info", foreground="#aaddff")
        log.tag_configure("success", foreground="#00ff88")
        log.tag_configure("warning", foreground="#ffaa44")
        log.tag_configure("error", foreground="#ff6666")
        log.tag_configure("hunter", foreground="#66ccff", font=('Consolas', 9, 'bold'))
        log.tag_configure("stage", foreground="#ffd700")
        log.tag_configure("loot", foreground="#88cc88")
    
    def _log(self, message: str, tag: str = None):
        """Add message to global log with optional color tag."""
        self.global_log.configure(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.global_log.insert(tk.END, f"[{timestamp}] ", "timestamp")
        if tag:
            self.global_log.insert(tk.END, f"{message}\n", tag)
        else:
            # Auto-detect tag based on content
            if "error" in message.lower() or "failed" in message.lower():
                self.global_log.insert(tk.END, f"{message}\n", "error")
            elif "complete" in message.lower() or "finished" in message.lower() or "done" in message.lower():
                self.global_log.insert(tk.END, f"{message}\n", "success")
            elif "starting" in message.lower() or "running" in message.lower():
                self.global_log.insert(tk.END, f"{message}\n", "info")
            else:
                self.global_log.insert(tk.END, f"{message}\n")
        self.global_log.see(tk.END)
        self.global_log.configure(state=tk.DISABLED)
    
    def _draw_global_progress(self):
        """Draw the color-coded global progress bar with 3 hunter segments."""
        canvas = self.global_progress_canvas
        
        # Check if anything has changed to avoid unnecessary redraws
        current_state = tuple((name, state["progress"], state["complete"]) 
                              for name, state in self.hunter_progress_state.items())
        if hasattr(self, '_last_progress_state') and self._last_progress_state == current_state:
            return  # Nothing changed, skip redraw
        self._last_progress_state = current_state
        
        canvas.delete("all")
        
        # Get canvas dimensions (avoid update_idletasks which causes flicker)
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        
        if width < 10:  # Canvas not ready yet
            return
        
        # Get selected hunters for dynamic sizing
        selected_hunters = []
        if hasattr(self, 'run_borge_var') and self.run_borge_var.get():
            selected_hunters.append("Borge")
        if hasattr(self, 'run_ozzy_var') and self.run_ozzy_var.get():
            selected_hunters.append("Ozzy")
        if hasattr(self, 'run_knox_var') and self.run_knox_var.get():
            selected_hunters.append("Knox")
        
        # If no hunters selected, show all 3 at equal size
        if not selected_hunters:
            selected_hunters = ["Borge", "Ozzy", "Knox"]
        
        # Each selected hunter gets equal portion of bar
        num_hunters = len(selected_hunters)
        segment_width = width / num_hunters
        
        for i, name in enumerate(selected_hunters):
            state = self.hunter_progress_state[name]
            x_start = i * segment_width
            x_end = (i + 1) * segment_width
            progress = state["progress"] / 100
            color = state["color"]
            
            # Draw background (dark)
            canvas.create_rectangle(x_start, 0, x_end, height, 
                                   fill='#2a2a3a', outline='#3d3d5c', width=1)
            
            # Draw filled progress portion
            fill_width = segment_width * progress
            if fill_width > 0:
                # Brighter color for fill
                canvas.create_rectangle(x_start, 2, x_start + fill_width, height - 2,
                                       fill=color, outline='')
            
            # Draw segment label (hunter initial + percentage)
            label = f"{name[0]}:{state['progress']:.0f}%"
            text_x = x_start + segment_width / 2
            text_color = '#FFFFFF' if progress > 0.3 else '#888888'
            canvas.create_text(text_x, height / 2, text=label, 
                              fill=text_color, font=('Arial', 8, 'bold'))
            
            # Draw checkmark if complete
            if state["complete"]:
                canvas.create_text(x_end - 12, height / 2, text="✓",
                                  fill='#00FF00', font=('Arial', 10, 'bold'))

    def _run_all_hunters(self):
        """Start optimization for selected hunters sequentially."""
        # Apply global settings to all hunters first
        self._apply_global_settings()
        
        # Get list of selected hunters (in tab order: Borge, Ozzy, Knox)
        selected = []
        if self.run_borge_var.get():
            selected.append("Borge")
        if self.run_ozzy_var.get():
            selected.append("Ozzy")
        if self.run_knox_var.get():
            selected.append("Knox")
        
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one hunter to optimize!")
            return
        
        self.run_all_btn.configure(state=tk.DISABLED)
        self.stop_all_btn.configure(state=tk.NORMAL)
        self.all_status.configure(text=f"Running {len(selected)} hunter(s) sequentially...")
        self._log(f"🚀 Starting optimization for: {', '.join(selected)}")
        self._log(f"   Running SEQUENTIALLY (one at a time for better responsiveness)")
        
        # Start first hunter
        self.sequential_queue = selected.copy()
        self._run_next_sequential()
        
        self._update_hunter_status()
    
    def _run_next_sequential(self):
        """Run the next hunter in the sequential queue."""
        if not hasattr(self, 'sequential_queue') or not self.sequential_queue:
            # All done
            self.run_all_btn.configure(state=tk.NORMAL)
            self.stop_all_btn.configure(state=tk.DISABLED)
            self.all_status.configure(text="✅ All selected hunters completed!")
            self._log("✅ All optimizations complete!")
            return
        
        # Get next hunter
        next_hunter = self.sequential_queue.pop(0)
        tab = self.hunter_tabs[next_hunter]
        
        # Ensure the tab is initialized (lazy loading might not have triggered yet)
        if not tab._content_initialized:
            tab._on_tab_visible()
            # Force Tkinter to process the initialization before starting optimization
            self.root.update_idletasks()
            # Give a bit more time for any after() calls to complete
            self.root.after(200, lambda: self._start_hunter_after_init(tab, next_hunter))
            return
        
        self._start_hunter_after_init(tab, next_hunter)
    
    def _start_hunter_after_init(self, tab, hunter_name: str):
        """Start a hunter's optimization after ensuring tab is initialized."""
        if not tab.is_running:
            self._log(f"   ▶️ Starting {hunter_name}...")
            self.all_status.configure(text=f"Running: {hunter_name} ({len(self.sequential_queue)} remaining)")
            tab._start_optimization()
        
        # Check for completion
        self.root.after(1000, self._check_sequential_complete)
    
    def _check_sequential_complete(self):
        """Check if current sequential hunter is complete."""
        self._update_hunter_status()
        
        # Check if any hunter is still running
        running = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        # Battle arena disabled - no returning check needed
        # returning = [name for name, hunter in self.arena_hunters.items() 
        #              if hunter.get("returning_to_bench", False)]
        returning = []
        
        if running:
            # Still running, check again
            self.root.after(1000, self._check_sequential_complete)
        elif returning:
            # Hunter finished but still walking back to bench - wait
            self.all_status.configure(text="⏳ Hunter returning to bench...")
            self.root.after(200, self._check_sequential_complete)
        else:
            # Current hunter finished AND back on bench, brief cooldown before next
            if hasattr(self, 'sequential_queue') and self.sequential_queue:
                self._log("   ⏳ Starting next hunter...")
                self.all_status.configure(text="⏳ Starting next hunter...")
                self.root.after(1000, self._run_next_sequential)
            else:
                # All done
                self._run_next_sequential()
    
    def _stop_all_hunters(self):
        """Stop all running optimizations."""
        self._log("⏹️ Stopping all hunters...")
        
        # Clear the sequential queue
        if hasattr(self, 'sequential_queue'):
            self.sequential_queue.clear()
        
        for name, tab in self.hunter_tabs.items():
            if tab.is_running:
                tab._stop_optimization()
        
        self.run_all_btn.configure(state=tk.NORMAL)
        self.stop_all_btn.configure(state=tk.DISABLED)
        self.all_status.configure(text="Stopped")
    
    def _check_all_complete(self):
        """Check if all hunters have completed."""
        running = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        if running:
            self._update_hunter_status()  # Only update status while running
            self.all_status.configure(text=f"Running: {', '.join(running)}")
            self.root.after(1000, self._check_all_complete)
        else:
            self._update_hunter_status()  # Final update
            self.run_all_btn.configure(state=tk.NORMAL)
            self.stop_all_btn.configure(state=tk.DISABLED)
            self.all_status.configure(text="All hunters complete!")
            self._log("✅ All hunter optimizations complete!")
            # Don't schedule another check - we're done
    
    def _save_all_builds(self):
        """Save all hunter builds."""
        for name, tab in self.hunter_tabs.items():
            tab._auto_save_build()
        self._log("💾 All builds saved to IRL Builds folder")
        messagebox.showinfo("Saved", "All builds saved to IRL Builds folder!")
    
    def _open_irl_builds_folder(self):
        """Open the IRL Builds folder in the system file explorer."""
        import subprocess
        import os
        
        builds_path = os.path.join(os.path.dirname(__file__), "IRL Builds")
        if not os.path.exists(builds_path):
            os.makedirs(builds_path)
        
        # Open folder in system file explorer
        try:
            if os.name == 'nt':  # Windows
                os.startfile(builds_path)
            elif os.name == 'posix':  # macOS/Linux
                subprocess.run(['xdg-open' if os.uname().sysname != 'Darwin' else 'open', builds_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder: {e}")
        
        self._log(f"📁 Opened IRL Builds folder: {builds_path}")


def main():
    """Main entry point."""
    root = tk.Tk()
    app = MultiHunterGUI(root)
    
    def on_closing():
        """Cleanup on window close."""
        # Terminate all optimization subprocesses
        for tab in app.hunter_tabs.values():
            if hasattr(tab, 'opt_process') and tab.opt_process and tab.opt_process.poll() is None:
                try:
                    tab.opt_process.terminate()
                    tab.opt_process.wait(timeout=2)
                except:
                    try:
                        tab.opt_process.kill()
                    except:
                        pass
            
            # Shutdown sim workers
            if hasattr(tab, 'sim_worker') and tab.sim_worker:
                tab.sim_worker.shutdown()
        
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
