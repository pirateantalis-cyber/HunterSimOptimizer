"""
Hunter Sim GUI - Build Optimizer
================================
A GUI application that helps find optimal talent/attribute builds by:
1. Taking user input for fixed game data (stats, inscriptions, relics, gems)
2. Automatically generating all valid talent/attribute combinations
3. Running simulations for each build
4. Displaying the best builds for different optimization goals
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import itertools
import queue
import time
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor
import statistics
from collections import Counter
import copy
import sys
import os
import json
from tkinter import filedialog

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hunters import Borge, Knox, Ozzy, Hunter
from sim import SimulationManager, Simulation, sim_worker

# Try to import Rust simulator
try:
    import rust_sim
    RUST_AVAILABLE = True
except ImportError:
    RUST_AVAILABLE = False


@dataclass
class BuildResult:
    """Stores the results of a simulated build."""
    talents: Dict[str, int]
    attributes: Dict[str, int]
    avg_final_stage: float
    highest_stage: int
    lowest_stage: int
    avg_loot_per_hour: float
    avg_damage: float
    avg_kills: float
    avg_elapsed_time: float
    avg_damage_taken: float
    survival_rate: float  # Legacy: % of runs without dying exactly at a boss stage
    # Boss milestone survival rates
    boss1_survival: float = 0.0  # % that passed boss 1 (stage > 100)
    boss2_survival: float = 0.0  # % that passed boss 2 (stage > 200)
    boss3_survival: float = 0.0  # % that passed boss 3 (stage > 300)
    boss4_survival: float = 0.0  # % that passed boss 4 (stage > 400)
    boss5_survival: float = 0.0  # % that passed boss 5 (stage > 500)
    # Per-resource loot (Material 1/2/3 for each hunter)
    avg_loot_common: float = 0.0      # Material 1 (Obsidian/Farahyte Ore/Glacium)
    avg_loot_uncommon: float = 0.0    # Material 2 (Behlium/Galvarium/Quartz)
    avg_loot_rare: float = 0.0        # Material 3 (Hellish-Biomatter/Vectid Crystals/Tesseracts)
    # XP tracking
    avg_xp: float = 0.0               # Average XP gained per run
    config: Dict = field(default_factory=dict)
    
    def __lt__(self, other):
        return self.avg_final_stage < other.avg_final_stage


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
        
        This ensures we never exceed total available points.
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
            # Each unlimited attr gets an equal share of the remaining budget
            # But must be at least 1 (to satisfy dependency requirements)
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
            # Fallback (shouldn't happen if _calculate_dynamic_attr_maxes worked)
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
        
        # Limit infinite max attributes to reasonable values based on remaining points
        if max_lvl == float('inf'):
            max_lvl = (self.attribute_points - points_spent) // cost
        
        # Cap infinite attributes for performance
        max_lvl = int(min(max_lvl, max_per_infinite))
        
        for lvl in range(0, max_lvl + 1):
            current[attr] = lvl
            self._generate_attr_combos(attributes, costs, max_levels, current, index + 1,
                                       points_spent + (lvl * cost), results, max_per_infinite)
    
    def generate_smart_sample(self, sample_size: int = 100, strategy: str = None) -> List[Tuple[Dict, Dict]]:
        """Generate a smart sample of builds using random walk allocation.
        
        This simulates human point-by-point clicking to explore the full build space.
        Each build is generated independently using random walk, which naturally creates
        diverse builds without needing artificial "strategies".
        
        If strategy is specified, it's used for logging only - all builds use random walk.
        """
        import random
        
        builds = []
        talents_list = list(self.costs["talents"].keys())
        attrs_list = list(self.costs["attributes"].keys())
        attr_costs = {a: self.costs["attributes"][a]["cost"] for a in attrs_list}
        attr_max = {a: self.costs["attributes"][a]["max"] for a in attrs_list}
        talent_max = {t: self.costs["talents"][t]["max"] for t in talents_list}
        
        # ALL builds use random walk - simulates human clicking point-by-point
        for _ in range(sample_size):
            talents = self._random_walk_talent_allocation(talents_list, talent_max)
            attrs = self._random_walk_attr_allocation(attrs_list, attr_costs, attr_max)
            builds.append((talents, attrs))
        
        return builds
    
    def _generate_single_build(self, strategy: str, talents_list, attrs_list, 
                               attr_costs, attr_max, talent_max) -> Tuple[Dict, Dict]:
        """Generate a single build using true random walk for both talents and attributes.
        
        Simulates human point-by-point allocation without algorithmic bias.
        All strategies now use random walk to explore the full human-accessible build space.
        """
        # Use true random walk for both - simulate human clicking
        talents = self._random_walk_talent_allocation(talents_list, talent_max)
        attrs = self._random_walk_attr_allocation(attrs_list, attr_costs, attr_max)
        
        return (talents, attrs)
    
    def _random_walk_talent_allocation(self, talents, max_levels) -> Dict[str, int]:
        """True random walk talent allocation - simulate human point-by-point clicking.
        
        Talents have no dependencies or gates, so this is simple:
        1. Start with 0 points in everything
        2. Repeat until out of points:
           - Find all talents that can accept +1 point
           - Pick one randomly
           - Add 1 point to it
        
        This explores the full space a human would without algorithmic bias.
        """
        import random
        result = {t: 0 for t in talents}
        remaining = self.talent_points
        
        # Point-by-point random allocation
        while remaining > 0:
            # Find all KNOWN talents that can still accept points (exclude unknown_talent)
            valid_talents = [
                t for t in talents 
                if t != 'unknown_talent' and (max_levels[t] == float('inf') or result[t] < int(max_levels[t]))
            ]
            
            # Only use unknown_talent as LAST RESORT when all known talents are maxed
            if not valid_talents:
                if 'unknown_talent' in talents:
                    valid_talents = ['unknown_talent']
                else:
                    break  # All talents maxed
            
            # Pick random talent and add 1 point
            chosen = random.choice(valid_talents)
            result[chosen] += 1
            remaining -= 1
        
        return result
    
    def _can_unlock_attribute(self, attr: str, current_allocation: Dict[str, int], costs: Dict[str, int]) -> bool:
        """Check if an attribute can be unlocked based on point gates.
        
        Point gates require a certain number of points to be spent in OTHER attributes
        before this attribute can be unlocked.
        """
        point_gates = getattr(self.hunter_class, 'attribute_point_gates', {})
        
        if attr not in point_gates:
            return True  # No gate requirement
        
        required_points = point_gates[attr]
        
        # Calculate total points spent in OTHER attributes (excluding this one)
        points_spent = sum(
            current_allocation.get(other_attr, 0) * costs[other_attr]
            for other_attr in current_allocation
            if other_attr != attr
        )
        
        return points_spent >= required_points
    
    def _random_walk_attr_allocation(self, attrs, costs, max_levels) -> Dict[str, int]:
        """True random walk attribute allocation - simulate human point-by-point clicking.
        
        Point-by-point random allocation without algorithmic bias:
        1. Start with 0 points in everything
        2. Repeat until out of points:
           - Find all attributes that can accept +1 point (considering costs, gates, exclusions, dependencies)
           - Pick one randomly
           - Add 1 point to it
        
        This explores the full space a human would access through random clicking.
        """
        import random
        result = {a: 0 for a in attrs}
        remaining = self.attribute_points
        
        # Get dependencies if they exist
        deps = getattr(self.hunter_class, 'attribute_dependencies', {})
        exclusions = getattr(self.hunter_class, 'attribute_exclusions', [])
        
        # Pure point-by-point random allocation:
        max_iterations = 10000  # Safety limit
        iteration = 0
        stuck_count = 0  # Track how many times we found no valid moves
        while remaining > 0 and iteration < max_iterations:
            iteration += 1
            # Find all currently valid attributes (can add at least 1 more point)
            valid_attrs = []
            for attr in attrs:
                # Check cost
                cost = costs[attr]
                if cost > remaining:
                    continue
                # Check if at max level
                if max_levels[attr] == float('inf'):
                    # Unlimited attributes can ALWAYS accept more points (if we can afford it)
                    pass  # No max check needed
                else:
                    max_lvl = int(max_levels[attr])
                    if result[attr] >= max_lvl:
                        continue
                # Check dependencies
                if attr in deps:
                    can_use = all(result.get(req_attr, 0) >= req_level 
                                 for req_attr, req_level in deps[attr].items())
                    if not can_use:
                        continue
                # Check point gates
                if not self._can_unlock_attribute(attr, result, costs):
                    continue
                # Check exclusions
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
                if stuck_count >= 3:  # Give up after 3 consecutive failures
                    break  # No more valid moves
            else:
                stuck_count = 0  # Reset counter when we find valid moves
            
            # Pick random valid attribute and add 1 point
            if valid_attrs:  # Safety check
                chosen = random.choice(valid_attrs)
                result[chosen] += 1
                remaining -= costs[chosen]
        
        # CRITICAL: Validate total points spent doesn't exceed budget
        total_spent = sum(result[attr] * costs[attr] for attr in result)
        if total_spent > self.attribute_points:
            # Build is invalid - exceeded budget somehow
            return {a: 0 for a in attrs}
        
        return result


class EvolutionaryOptimizer:
    """
    Genetic/evolutionary optimizer that learns from simulation results.
    
    Key features:
    1. Maintains a population of builds
    2. Evaluates fitness based on simulation results
    3. Selects best performers for breeding
    4. Applies crossover and mutation to create new generations
    5. Tracks "worthless" patterns to avoid (e.g., no survival = bad)
    """
    
    def __init__(self, build_generator: BuildGenerator, 
                 population_size: int = 100,
                 elite_ratio: float = 0.1,
                 mutation_rate: float = 0.15,
                 crossover_rate: float = 0.7):
        self.generator = build_generator
        self.population_size = population_size
        self.elite_count = max(2, int(population_size * elite_ratio))
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        
        # Population tracking
        self.population: List[Dict] = []
        self.fitness_scores: List[float] = []
        self.generation = 0
        self.tested_builds: set = set()  # Track all builds we've already tested
        
        # Learning patterns
        self.bad_patterns: Dict[str, int] = {}  # Pattern -> failure count
        self.good_patterns: Dict[str, float] = {}  # Pattern -> avg fitness
        self.worthless_threshold = 10  # Failures before pattern is "worthless" (be less aggressive)
    
    def _build_hash(self, build: Dict) -> tuple:
        """Create a hashable representation of a build for deduplication."""
        attrs = tuple(sorted(build.get('attributes', {}).items()))
        talents = tuple(sorted(build.get('talents', {}).items()))
        return (attrs, talents)
    
    def _is_duplicate(self, build: Dict) -> bool:
        """Check if we've already tested this exact build."""
        return self._build_hash(build) in self.tested_builds
    
    def _mark_tested(self, build: Dict):
        """Mark a build as tested."""
        self.tested_builds.add(self._build_hash(build))
        
    def initialize_population(self) -> List[Dict]:
        """Create initial diverse population with unique builds only."""
        import random
        self.population = []
        self.tested_builds = set()  # Reset for new run
        
        strategies = ['random', 'defensive', 'offensive', 'balanced', 'focused']
        
        # Generate AGGRESSIVELY more candidates - high level chars need LOTS more attempts
        # to find unique builds due to point gate constraints
        candidates_per_strategy = self.population_size * 5  # 5x per strategy = 25x total
        
        print(f"   Generating candidates to find {self.population_size} unique builds...")
        print(f"   (Will generate up to {candidates_per_strategy * len(strategies)} candidates across {len(strategies)} strategies)")
        
        # Generate in batches to avoid memory issues
        batch_size = 200
        total_generated = 0
        duplicates_found = 0
        
        for strategy in strategies:
            batches_needed = (candidates_per_strategy + batch_size - 1) // batch_size
            for batch_num in range(batches_needed):
                current_batch_size = min(batch_size, candidates_per_strategy - batch_num * batch_size)
                builds = self.generator.generate_smart_sample(sample_size=current_batch_size, strategy=strategy)
                
                for talents, attrs in builds:
                    candidate = {'talents': talents, 'attributes': attrs}
                    if not self._is_duplicate(candidate):
                        self.population.append(candidate)
                        self._mark_tested(candidate)
                        if len(self.population) >= self.population_size:
                            print(f"   ✓ Found {len(self.population)} unique builds (generated {total_generated + len(builds)} total, {duplicates_found} duplicates)")
                            self.generation = 0
                            return self.population
                    else:
                        duplicates_found += 1
                
                total_generated += current_batch_size
                
                # Progress update every 500 candidates
                if total_generated % 500 == 0:
                    print(f"   Generated {total_generated} candidates, found {len(self.population)} unique ({duplicates_found} dupes)...")
        
        # If STILL not enough, keep generating pure random until we hit target
        print(f"   Still need {self.population_size - len(self.population)} more builds, continuing with random generation...")
        attempts = 0
        max_attempts = self.population_size * 100  # Much higher limit
        batch_size = 100
        
        while len(self.population) < self.population_size and attempts < max_attempts:
            builds = self.generator.generate_smart_sample(sample_size=batch_size, strategy='random')
            for talents, attrs in builds:
                candidate = {'talents': talents, 'attributes': attrs}
                if not self._is_duplicate(candidate):
                    self.population.append(candidate)
                    self._mark_tested(candidate)
                    if len(self.population) >= self.population_size:
                        break
            
            attempts += batch_size
            if attempts % 1000 == 0:
                print(f"   Random attempts: {attempts}, unique builds: {len(self.population)}")
        
        print(f"   ✓ Created {len(self.population)} unique builds after {total_generated + attempts} total candidates")
        self.generation = 0
        return self.population
    
    def evaluate_fitness(self, build: Dict, sim_result: Dict, mode: str = "Balanced") -> float:
        """
        Calculate fitness score for a build based on simulation results.
        Higher is better.
        """
        # Default bad fitness for failed builds
        if not sim_result or sim_result.get('died_early', True):
            return 0.001
        
        stage = sim_result.get('avg_stage', 0)
        loot = sim_result.get('loot_per_hour', 0)
        survival = sim_result.get('survival_rate', 0)
        damage = sim_result.get('total_damage', 0)
        clear_time = sim_result.get('clear_time', float('inf'))
        
        # Even with 0 survival, stage progression matters!
        # Use stage as base fitness, with survival as a multiplier bonus
        base_fitness = stage  # Stage reached is always valuable
        
        # Calculate fitness based on mode
        if mode == "Highest Average Stage":
            fitness = stage * (1 + survival)  # survival gives up to 2x bonus
        elif mode == "Best Loot Per Hour":
            fitness = loot + (stage * 0.1)  # stage matters for loot farming too
        elif mode == "Fastest Clear Time":
            fitness = (1000 / max(clear_time, 1)) + stage
        elif mode == "Most Damage Dealt":
            fitness = damage + (stage * 10)
        elif mode == "Best Survival Rate":
            fitness = (survival * 1000) + stage  # survival primary, stage secondary
        else:  # Balanced
            # Stage is primary, with bonuses for loot and survival
            fitness = stage + (loot * 0.01) + (survival * 50)
        
        return max(0.001, fitness)
    
    def update_population_fitness(self, results: List[Dict], mode: str = "Balanced"):
        """Update fitness scores for current population."""
        self.fitness_scores = []
        
        for build, result in zip(self.population, results):
            fitness = self.evaluate_fitness(build, result, mode)
            self.fitness_scores.append(fitness)
            
            # Learn patterns using RELATIVE thresholds based on current population
            # This is done after we have all scores
        
        # Now learn patterns using relative thresholds
        if self.fitness_scores:
            sorted_scores = sorted(self.fitness_scores)
            # Bottom 25% = bad, Top 25% = good
            bad_threshold = sorted_scores[len(sorted_scores) // 4] if len(sorted_scores) > 4 else 0
            good_threshold = sorted_scores[3 * len(sorted_scores) // 4] if len(sorted_scores) > 4 else float('inf')
            
            for build, result, fitness in zip(self.population, results, self.fitness_scores):
                self._update_patterns(build, fitness, result, bad_threshold, good_threshold)
    
    def _extract_patterns(self, build: Dict) -> List[str]:
        """Extract key patterns from a build for learning.
        
        Only extract very specific patterns to avoid over-filtering.
        """
        patterns = []
        
        # Only track very high investment attributes (>= 10 levels)
        attrs = build.get('attributes', {})
        very_high_attrs = [k for k, v in attrs.items() if v >= 10]
        for attr in very_high_attrs:
            patterns.append(f"attr:{attr}:maxed")
        
        # Only track maxed talents
        talents = build.get('talents', {})
        maxed_talents = [k for k, v in talents.items() if v >= 5]
        for talent in maxed_talents:
            patterns.append(f"talent:{talent}:maxed")
        
        return patterns
    
    def _update_patterns(self, build: Dict, fitness: float, result: Dict, 
                         bad_threshold: float = 0.05, good_threshold: float = 1.0):
        """Update pattern learning based on build performance.
        
        Uses relative thresholds from the current population.
        """
        patterns = self._extract_patterns(build)
        
        for pattern in patterns:
            if fitness <= bad_threshold:  # Bottom 25% of population
                self.bad_patterns[pattern] = self.bad_patterns.get(pattern, 0) + 1
            elif fitness >= good_threshold:  # Top 25% of population
                # Running average of fitness for good patterns
                if pattern in self.good_patterns:
                    self.good_patterns[pattern] = (self.good_patterns[pattern] + fitness) / 2
                else:
                    self.good_patterns[pattern] = fitness
    
    def is_worthless_pattern(self, build: Dict) -> bool:
        """Check if a build contains worthless patterns."""
        patterns = self._extract_patterns(build)
        
        for pattern in patterns:
            if self.bad_patterns.get(pattern, 0) >= self.worthless_threshold:
                return True
        return False
    
    def select_parents(self) -> List[Dict]:
        """Select parents for next generation using tournament selection."""
        import random
        
        if not self.population or not self.fitness_scores:
            return []
        
        # Ensure fitness_scores and population are in sync
        pop_size = min(len(self.population), len(self.fitness_scores))
        if pop_size == 0:
            return []
        
        parents = []
        tournament_size = 3
        
        # Always include elites (but only up to what we have)
        elite_indices = sorted(range(pop_size), 
                               key=lambda i: self.fitness_scores[i], 
                               reverse=True)[:min(self.elite_count, pop_size)]
        for i in elite_indices:
            parents.append(self.population[i])
        
        # Tournament selection for remaining
        target_parents = min(self.population_size // 2, pop_size)
        while len(parents) < target_parents:
            tournament = random.sample(range(pop_size), 
                                       min(tournament_size, pop_size))
            winner = max(tournament, key=lambda i: self.fitness_scores[i])
            parents.append(self.population[winner])
        
        return parents
    
    def evolve_population(self) -> List[Dict]:
        """Create next generation using pure random walk generation.
        
        All builds are generated fresh using random walk allocation which properly
        respects budget constraints, dependencies, and point gates. No crossover/mutation
        needed since random walk naturally explores the full human-accessible build space.
        
        Returns a smaller population if we've exhausted the build space.
        """
        if not self.fitness_scores:
            return self.initialize_population()
        
        new_population = []
        consecutive_failures = 0
        max_consecutive_failures = 1000
        batch_size = 100
        
        print(f"   Generation {self.generation + 1}: Generating {self.population_size} new unique builds...")
        
        while len(new_population) < self.population_size and consecutive_failures < max_consecutive_failures:
            # Generate a batch of fresh builds using random walk
            builds = self.generator.generate_smart_sample(sample_size=batch_size, strategy='random')
            
            batch_added = 0
            for talents, attrs in builds:
                candidate = {'talents': talents, 'attributes': attrs}
                
                # Only add if unique and not a known bad pattern
                if not self._is_duplicate(candidate) and not self.is_worthless_pattern(candidate):
                    new_population.append(candidate)
                    self._mark_tested(candidate)
                    batch_added += 1
                    
                    if len(new_population) >= self.population_size:
                        break
            
            if batch_added == 0:
                consecutive_failures += batch_size
            else:
                consecutive_failures = 0
        
        # If we got no new builds, we've exhausted the search space
        if not new_population:
            print(f"   Build space exhausted after {len(self.tested_builds)} unique builds tested")
            return []  # Signal that we're done
        
        self.population = new_population
        self.generation += 1
        
        print(f"   ✓ Created {len(new_population)} unique builds for generation {self.generation}")
        return self.population
    
    def get_best_builds(self, n: int = 10) -> List[Tuple[Dict, float]]:
        """Return top N builds with their fitness scores."""
        if not self.fitness_scores:
            return []
        
        indexed = list(enumerate(self.fitness_scores))
        indexed.sort(key=lambda x: x[1], reverse=True)
        
        return [(self.population[i], score) for i, score in indexed[:n]]
    
    def get_stats(self) -> Dict:
        """Return current optimizer statistics."""
        if not self.fitness_scores:
            return {'generation': 0, 'best': 0, 'avg': 0, 'patterns_learned': 0}
        
        return {
            'generation': self.generation,
            'best_fitness': max(self.fitness_scores),
            'avg_fitness': sum(self.fitness_scores) / len(self.fitness_scores),
            'bad_patterns': len(self.bad_patterns),
            'good_patterns': len(self.good_patterns),
            'worthless_patterns': sum(1 for v in self.bad_patterns.values() if v >= self.worthless_threshold)
        }


class OptimizationMode:
    """Defines different optimization targets."""
    HIGHEST_STAGE = "Highest Average Stage"
    BEST_LOOT = "Best Loot Per Hour"
    FASTEST_CLEAR = "Fastest Clear Time"
    MOST_DAMAGE = "Most Damage Dealt"
    BEST_SURVIVAL = "Best Survival Rate"
    BALANCED = "Balanced (Stage + Loot)"


class HunterSimGUI:
    """Main GUI application for the Hunter Sim Build Optimizer."""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hunter Sim - Build Optimizer")
        self.root.geometry("1200x900")
        self.root.minsize(1000, 700)
        
        # State
        self.current_hunter = tk.StringVar(value="Ozzy")
        self.hunter_level = tk.IntVar(value=100)
        # Higher defaults when Rust is available (much faster)
        self.num_sims_per_build = tk.IntVar(value=100 if RUST_AVAILABLE else 10)
        self.max_builds_to_test = tk.IntVar(value=5000 if RUST_AVAILABLE else 1000)
        self.num_processes = tk.IntVar(value=16)  # Good for high-core CPUs
        
        # Input field references
        self.stat_entries: Dict[str, tk.Entry] = {}
        self.inscryption_entries: Dict[str, tk.Entry] = {}
        self.relic_entries: Dict[str, tk.Entry] = {}
        self.gem_entries: Dict[str, tk.Entry] = {}
        self.mod_vars: Dict[str, tk.BooleanVar] = {}
        
        # Results storage
        self.results: List[BuildResult] = []
        self.result_queue = queue.Queue()
        self.is_running = False
        self.stop_event = threading.Event()  # Thread-safe stop flag
        
        # Best tracking during optimization
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_gen = 0
        self.best_avg_gen = 0
        
        self._create_ui()
        
    def _create_ui(self):
        """Create the main UI layout."""
        # Main notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Tab 1: Input Configuration
        self.input_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.input_frame, text="Build Configuration")
        
        # Tab 2: Simulation Control
        self.sim_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.sim_frame, text="Run Optimization")
        
        # Tab 3: Upgrade Advisor
        self.advisor_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.advisor_frame, text="Upgrade Advisor")
        
        # Tab 4: Results
        self.results_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.results_frame, text="Results")
        
        self._create_input_tab()
        self._create_sim_tab()
        self._create_advisor_tab()
        self._create_results_tab()
        
    def _create_input_tab(self):
        """Create the input configuration tab."""
        # Hunter selection at top
        top_frame = ttk.Frame(self.input_frame)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(top_frame, text="Hunter:", font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=5)
        hunter_combo = ttk.Combobox(top_frame, textvariable=self.current_hunter, 
                                     values=["Ozzy", "Borge", "Knox"], state="readonly", width=10)
        hunter_combo.pack(side=tk.LEFT, padx=5)
        hunter_combo.bind('<<ComboboxSelected>>', self._on_hunter_change)
        
        ttk.Label(top_frame, text="Level:", font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=(20, 5))
        level_spin = ttk.Spinbox(top_frame, textvariable=self.hunter_level, from_=1, to=600, width=6)
        level_spin.pack(side=tk.LEFT, padx=5)
        
        ttk.Label(top_frame, text="(Each level = +1 Talent Point, +3 Attribute Points)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=20)
        
        # Save/Load buttons
        ttk.Separator(top_frame, orient='vertical').pack(side=tk.LEFT, fill='y', padx=10)
        ttk.Button(top_frame, text="💾 Save Build", command=self._save_build).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="📂 Load Build", command=self._load_build).pack(side=tk.LEFT, padx=5)
        
        # Scrollable content area
        canvas = tk.Canvas(self.input_frame)
        scrollbar = ttk.Scrollbar(self.input_frame, orient="vertical", command=canvas.yview)
        self.scrollable_frame = ttk.Frame(canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bind mouse wheel
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        
        self._populate_input_fields()
        
    def _populate_input_fields(self):
        """Populate input fields based on selected hunter."""
        # Clear existing
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.stat_entries.clear()
        self.inscryption_entries.clear()
        self.relic_entries.clear()
        self.gem_entries.clear()
        self.mod_vars.clear()
        
        hunter_name = self.current_hunter.get()
        if hunter_name == "Ozzy":
            hunter_class = Ozzy
        elif hunter_name == "Knox":
            hunter_class = Knox
        else:
            hunter_class = Borge
        dummy = hunter_class.load_dummy()
        
        # Create sections
        row = 0
        
        # Stats Section
        stats_frame = ttk.LabelFrame(self.scrollable_frame, text="📊 Main Stats (Enter your upgrade LEVELS, not final values)")
        stats_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        row += 1
        
        # Get stat names based on hunter (Knox has different stats)
        if hunter_name == "Knox":
            stat_names = {
                "hp": "HP",
                "power": "Power/Attack",
                "regen": "Regeneration",
                "damage_reduction": "Damage Reduction",
                "block_chance": "Block Chance",
                "effect_chance": "Effect Chance",
                "charge_chance": "Charge Chance",
                "charge_gained": "Charge Gained",
                "reload_time": "Reload Time",
                "projectiles_per_salvo": "Projectiles/Salvo"
            }
        else:
            stat_names = {
                "hp": "HP",
                "power": "Power/Attack",
                "regen": "Regeneration",
                "damage_reduction": "Damage Reduction",
                "evade_chance": "Evade Chance",
                "effect_chance": "Effect Chance",
                "special_chance": "Special/Crit Chance",
                "special_damage": "Special/Crit Damage",
                "speed": "Speed"
            }
        
        col = 0
        for i, (stat_key, stat_label) in enumerate(stat_names.items()):
            r, c = divmod(i, 3)
            frame = ttk.Frame(stats_frame)
            frame.grid(row=r, column=c, padx=10, pady=5, sticky="w")
            ttk.Label(frame, text=f"{stat_label}:", width=18).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=8)
            entry.insert(0, "0")
            entry.pack(side=tk.LEFT)
            self.stat_entries[stat_key] = entry
        
        # Inscryptions Section
        inscr_frame = ttk.LabelFrame(self.scrollable_frame, text="📜 Inscryptions (Enter your levels)")
        inscr_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        row += 1
        
        inscr_tooltips = self._get_inscryption_tooltips(hunter_name)
        
        for i, (inscr_key, inscr_val) in enumerate(dummy.get("inscryptions", {}).items()):
            r, c = divmod(i, 4)
            frame = ttk.Frame(inscr_frame)
            frame.grid(row=r, column=c, padx=10, pady=5, sticky="w")
            tooltip = inscr_tooltips.get(inscr_key, inscr_key.upper())
            ttk.Label(frame, text=f"{inscr_key.upper()} ({tooltip}):", width=25).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=6)
            entry.insert(0, "0")
            entry.pack(side=tk.LEFT)
            self.inscryption_entries[inscr_key] = entry
        
        # Relics Section
        relics_frame = ttk.LabelFrame(self.scrollable_frame, text="🏆 Relics (Enter your levels)")
        relics_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        row += 1
        
        for i, (relic_key, relic_val) in enumerate(dummy.get("relics", {}).items()):
            frame = ttk.Frame(relics_frame)
            frame.grid(row=0, column=i, padx=10, pady=5, sticky="w")
            label = relic_key.replace("_", " ").title()
            ttk.Label(frame, text=f"{label}:", width=30).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=6)
            entry.insert(0, "0")
            entry.pack(side=tk.LEFT)
            self.relic_entries[relic_key] = entry
        
        # Gems Section
        gems_frame = ttk.LabelFrame(self.scrollable_frame, text="💎 Gems (Enter 0 or 1, or level for attraction gems)")
        gems_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        row += 1
        
        for i, (gem_key, gem_val) in enumerate(dummy.get("gems", {}).items()):
            r, c = divmod(i, 3)
            frame = ttk.Frame(gems_frame)
            frame.grid(row=r, column=c, padx=10, pady=5, sticky="w")
            label = gem_key.replace("_", " ").replace("#", "").title()
            ttk.Label(frame, text=f"{label}:", width=22).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=6)
            entry.insert(0, "0")
            entry.pack(side=tk.LEFT)
            self.gem_entries[gem_key] = entry
        
        # Mods Section (if applicable)
        if dummy.get("mods"):
            mods_frame = ttk.LabelFrame(self.scrollable_frame, text="⚙️ Mods")
            mods_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
            row += 1
            
            for i, (mod_key, mod_val) in enumerate(dummy.get("mods", {}).items()):
                var = tk.BooleanVar(value=False)
                label = mod_key.replace("_", " ").title()
                cb = ttk.Checkbutton(mods_frame, text=label, variable=var)
                cb.grid(row=0, column=i, padx=10, pady=5)
                self.mod_vars[mod_key] = var
        
        # Info section about talents/attributes
        info_frame = ttk.LabelFrame(self.scrollable_frame, text="ℹ️ Talents & Attributes (AUTOMATICALLY OPTIMIZED)")
        info_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        row += 1
        
        info_text = """
The optimizer will automatically test different combinations of talents and attributes to find the best builds.

Based on your level, you have:
• Talent Points: {talent_pts} (1 per level)
• Attribute Points: {attr_pts} (3 per level)

The system will explore combinations and rank them by different criteria:
• Highest Stage Reached
• Best Loot Per Hour  
• Fastest Clear Time
• Most Damage Dealt
• Best Survival Rate
        """.format(talent_pts=self.hunter_level.get(), attr_pts=self.hunter_level.get() * 3)
        
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=800).pack(padx=10, pady=10)
        
    def _get_inscryption_tooltips(self, hunter: str) -> Dict[str, str]:
        """Get tooltip descriptions for inscryptions."""
        if hunter == "Borge":
            return {
                "i3": "+6 HP",
                "i4": "+0.65% Crit",
                "i11": "+2% Effect",
                "i13": "+8 Power",
                "i14": "+1.1 Loot",
                "i23": "-0.04s Speed",
                "i24": "+0.4% DR",
                "i27": "+24 HP",
                "i44": "+1.08 Loot",
                "i60": "+3% HP/Pwr/Loot",
            }
        elif hunter == "Knox":
            # Knox inscryptions - placeholders until wiki has full data
            return {
                "i_knox_hp": "+HP",
                "i_knox_power": "+Power",
                "i_knox_block": "+Block",
                "i_knox_charge": "+Charge",
                "i_knox_reload": "-Reload",
            }
        else:  # Ozzy
            return {
                "i31": "+0.6% Effect",
                "i32": "+50% Loot",
                "i33": "+75% XP",
                "i36": "-0.03s Speed",
                "i37": "+1.11% DR",
                "i40": "+0.5% Multistrike",
            }
    
    def _on_hunter_change(self, event=None):
        """Handle hunter selection change."""
        self._populate_input_fields()
    
    def _save_build(self):
        """Save the current build configuration to a JSON file."""
        config = {
            "hunter": self.current_hunter.get(),
            "level": self.hunter_level.get(),
            "stats": {},
            "inscryptions": {},
            "relics": {},
            "gems": {},
            "mods": {}
        }
        
        # Gather all input values
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
                
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
        
        # Ask for filename
        filename = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile=f"my_{self.current_hunter.get().lower()}_build.json",
            title="Save Build Configuration"
        )
        
        if filename:
            with open(filename, 'w') as f:
                json.dump(config, f, indent=2)
            messagebox.showinfo("Saved", f"Build saved to:\n{filename}")
    
    def _load_build(self):
        """Load a build configuration from a JSON file."""
        filename = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Load Build Configuration"
        )
        
        if not filename:
            return
            
        try:
            with open(filename, 'r') as f:
                config = json.load(f)
            
            # Set hunter and level
            if config.get("hunter"):
                self.current_hunter.set(config["hunter"])
                self._populate_input_fields()  # Rebuild the form for this hunter
            
            if config.get("level"):
                self.hunter_level.set(config["level"])
            
            # Fill in stats
            for key, value in config.get("stats", {}).items():
                if key in self.stat_entries:
                    self.stat_entries[key].delete(0, tk.END)
                    self.stat_entries[key].insert(0, str(value))
            
            # Fill in inscryptions
            for key, value in config.get("inscryptions", {}).items():
                if key in self.inscryption_entries:
                    self.inscryption_entries[key].delete(0, tk.END)
                    self.inscryption_entries[key].insert(0, str(value))
            
            # Fill in relics
            for key, value in config.get("relics", {}).items():
                if key in self.relic_entries:
                    self.relic_entries[key].delete(0, tk.END)
                    self.relic_entries[key].insert(0, str(value))
            
            # Fill in gems
            for key, value in config.get("gems", {}).items():
                if key in self.gem_entries:
                    self.gem_entries[key].delete(0, tk.END)
                    self.gem_entries[key].insert(0, str(value))
            
            # Fill in mods
            for key, value in config.get("mods", {}).items():
                if key in self.mod_vars:
                    self.mod_vars[key].set(bool(value))
            
            messagebox.showinfo("Loaded", f"Build loaded from:\n{filename}")
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load build:\n{str(e)}")
    
    def _create_advisor_tab(self):
        """Create the Upgrade Advisor tab."""
        # Instructions
        info_frame = ttk.LabelFrame(self.advisor_frame, text="🎯 Upgrade Advisor - Which Stat Should I Upgrade?")
        info_frame.pack(fill=tk.X, padx=10, pady=10)
        
        info_text = """
This tool helps you decide which STAT to upgrade next when you level up (HP, Power, Regen, etc.)

How it works:
1. Set your current build in "Build Configuration" (stats, talents, attributes)
2. Optionally, run the Build Optimizer first and use the best talents/attributes from there
3. Click "Analyze Best Upgrade" below
4. The advisor simulates adding +1 to each stat and shows which gives the BEST improvement

Example output:  "🥇 +1 Power → Stage: +1.24, Loot: +0.35, Damage: +15,420"
This tells you upgrading Power will help you progress 1.24 more stages on average!
        """
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=800).pack(padx=10, pady=10)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.advisor_frame, text="⚙️ Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=10)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row1, text="Simulations per test:").pack(side=tk.LEFT, padx=5)
        self.advisor_sims = tk.IntVar(value=100)
        ttk.Spinbox(row1, textvariable=self.advisor_sims, from_=10, to=500, width=6).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="(Rust engine: 100-200 recommended for accuracy)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Use best build option
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=5)
        
        self.advisor_use_best = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="🏆 Use best build from optimizer (if available)", 
                        variable=self.advisor_use_best).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="Otherwise uses Build Configuration talents/attributes", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Analyze button
        btn_frame = ttk.Frame(self.advisor_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.advisor_btn = ttk.Button(btn_frame, text="🔍 Analyze Best Upgrade", command=self._run_upgrade_advisor)
        self.advisor_btn.pack(side=tk.LEFT, padx=5)
        
        self.advisor_status = ttk.Label(btn_frame, text="")
        self.advisor_status.pack(side=tk.LEFT, padx=10)
        
        # Results
        results_frame = ttk.LabelFrame(self.advisor_frame, text="📈 Upgrade Recommendations")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.advisor_results = scrolledtext.ScrolledText(results_frame, height=15, font=('Consolas', 10))
        self.advisor_results.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
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
            hunter_name = self.current_hunter.get()
            if hunter_name == "Ozzy":
                hunter_class = Ozzy
            elif hunter_name == "Knox":
                hunter_class = Knox
            else:
                hunter_class = Borge
            
            # Build base config INCLUDING current talents and attributes
            base_config = self._get_current_config()
            
            # If "use best build" is enabled and we have optimizer results, use the best build
            if self.advisor_use_best.get() and self.results:
                # Find best build by avg stage
                best_result = max(self.results, key=lambda r: r.avg_final_stage)
                base_config["talents"] = best_result.talents
                base_config["attributes"] = best_result.attributes
                self.root.after(0, lambda: self.advisor_status.configure(
                    text=f"Using best build (avg {best_result.avg_final_stage:.1f} stages)..."))
            
            num_sims = self.advisor_sims.get()
            use_rust = self.use_rust.get() and RUST_AVAILABLE
            
            # First, simulate the baseline
            self.root.after(0, lambda: self.advisor_status.configure(text="Simulating baseline..."))
            if use_rust:
                baseline = self._simulate_build_rust(hunter_class, base_config, num_sims)
            else:
                baseline = self._simulate_build_sequential(hunter_class, base_config, num_sims)
            
            if not baseline:
                self.root.after(0, lambda: self._show_advisor_error("Could not simulate baseline build"))
                return
            
            # Test each stat upgrade
            stat_keys = list(self.stat_entries.keys())
            results = []
            
            for i, stat in enumerate(stat_keys):
                self.root.after(0, lambda s=stat, i=i: self.advisor_status.configure(
                    text=f"Testing +1 {s}... ({i+1}/{len(stat_keys)})"))
                
                test_config = copy.deepcopy(base_config)
                test_config["stats"][stat] = test_config["stats"].get(stat, 0) + 1
                
                if use_rust:
                    result = self._simulate_build_rust(hunter_class, test_config, num_sims)
                else:
                    result = self._simulate_build_sequential(hunter_class, test_config, num_sims)
                    
                if result:
                    # Calculate improvements
                    stage_improvement = result.avg_final_stage - baseline.avg_final_stage
                    loot_improvement = result.avg_loot_per_hour - baseline.avg_loot_per_hour
                    damage_improvement = result.avg_damage - baseline.avg_damage
                    survival_improvement = (result.survival_rate - baseline.survival_rate) * 100
                    
                    # Create a score (weighted combination)
                    score = (
                        stage_improvement * 10 +  # Stage is important
                        loot_improvement * 5 +    # Loot matters
                        damage_improvement / 1000 +  # Normalize damage
                        survival_improvement * 2   # Survival is good
                    )
                    
                    results.append({
                        "stat": stat,
                        "stage_improvement": stage_improvement,
                        "loot_improvement": loot_improvement,
                        "damage_improvement": damage_improvement,
                        "survival_improvement": survival_improvement,
                        "score": score,
                        "result": result
                    })
            
            # Sort by score
            results.sort(key=lambda x: x["score"], reverse=True)
            
            # Display results
            self.root.after(0, lambda: self._display_advisor_results(baseline, results))
            
        except Exception as e:
            import traceback
            self.root.after(0, lambda: self._show_advisor_error(f"Error: {str(e)}\n{traceback.format_exc()}"))
    
    def _show_advisor_error(self, message: str):
        """Show an error in the advisor results."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        self.advisor_results.insert(tk.END, f"❌ {message}")
        self.advisor_results.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="")
    
    def _display_advisor_results(self, baseline, results):
        """Display the upgrade advisor results grouped by resource type."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        
        text = self.advisor_results
        
        # Define resource categories (same for all hunters)
        resource_categories = {
            "Common Resource": ["hp", "power", "regeneration"],
            "Rare Resource": ["dr", "evade", "effect"],
            "Very Rare Resource": ["special_chance", "crit_chance", "special_damage", "crit_damage", "speed"]
        }
        
        # Group results by resource
        grouped_results = {cat: [] for cat in resource_categories}
        for r in results:
            for category, stats in resource_categories.items():
                if r["stat"] in stats:
                    grouped_results[category].append(r)
                    break
        
        text.insert(tk.END, "=" * 70 + "\n")
        text.insert(tk.END, "🎯 UPGRADE ADVISOR RESULTS\n")
        text.insert(tk.END, "=" * 70 + "\n\n")
        
        text.insert(tk.END, "📊 BASELINE PERFORMANCE:\n")
        text.insert(tk.END, f"   Avg Stage: {baseline.avg_final_stage:.1f}\n")
        text.insert(tk.END, f"   Loot/Hour: {baseline.avg_loot_per_hour:.2f}\n")
        text.insert(tk.END, f"   Avg Damage: {baseline.avg_damage:,.0f}\n")
        text.insert(tk.END, f"   Survival: {baseline.survival_rate*100:.1f}%\n\n")
        
        # Show best overall first
        if results:
            best = results[0]
            text.insert(tk.END, "=" * 70 + "\n")
            text.insert(tk.END, "✨ BEST OVERALL UPGRADE\n")
            text.insert(tk.END, "=" * 70 + "\n")
            stat_name = best["stat"].replace("_", " ").title()
            text.insert(tk.END, f"🥇 +1 {stat_name}\n")
            text.insert(tk.END, f"   Stage: {best['stage_improvement']:+.2f}")
            text.insert(tk.END, f"  |  Loot/Hr: {best['loot_improvement']:+.2f}")
            text.insert(tk.END, f"  |  Damage: {best['damage_improvement']:+,.0f}")
            text.insert(tk.END, f"  |  Survival: {best['survival_improvement']:+.1f}%\n\n")
        
        # Show results grouped by resource
        text.insert(tk.END, "=" * 70 + "\n")
        text.insert(tk.END, "📦 BEST UPGRADES BY RESOURCE TYPE\n")
        text.insert(tk.END, "=" * 70 + "\n\n")
        
        resource_icons = {
            "Common Resource": "⚪",
            "Rare Resource": "🔵", 
            "Very Rare Resource": "🟣"
        }
        
        for category in ["Common Resource", "Rare Resource", "Very Rare Resource"]:
            icon = resource_icons[category]
            category_results = grouped_results[category]
            
            if not category_results:
                continue
            
            text.insert(tk.END, f"{icon} {category.upper()}\n")
            text.insert(tk.END, "-" * 70 + "\n")
            
            # Sort by score within category
            category_results.sort(key=lambda x: x["score"], reverse=True)
            
            for i, r in enumerate(category_results[:3], 1):  # Top 3 per category
                stat_name = r["stat"].replace("_", " ").title()
                text.insert(tk.END, f"  {i}. +1 {stat_name}\n")
                text.insert(tk.END, f"     Stage: {r['stage_improvement']:+.2f}")
                text.insert(tk.END, f"  |  Loot: {r['loot_improvement']:+.2f}")
                text.insert(tk.END, f"  |  Dmg: {r['damage_improvement']:+,.0f}")
                text.insert(tk.END, f"  |  Surv: {r['survival_improvement']:+.1f}%\n")
            
            text.insert(tk.END, "\n")
        
        text.insert(tk.END, "=" * 70 + "\n")
        text.insert(tk.END, "💡 TIP: Upgrade within the resource type you have available!\n")
        text.insert(tk.END, "=" * 70 + "\n")
        
        text.insert(tk.END, "\n\n📋 ALL STATS COMPARISON:\n")
        text.insert(tk.END, "-" * 70 + "\n")
        text.insert(tk.END, f"{'Stat':<20} {'Stage':>10} {'Loot/Hr':>10} {'Damage':>12} {'Survival':>10}\n")
        text.insert(tk.END, "-" * 70 + "\n")
        
        for r in results:
            stat_name = r["stat"].replace("_", " ").title()[:18]
            text.insert(tk.END, f"{stat_name:<20} {r['stage_improvement']:>+10.2f} {r['loot_improvement']:>+10.2f} {r['damage_improvement']:>+12,.0f} {r['survival_improvement']:>+9.1f}%\n")
        
        text.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="Analysis complete!")
        
    def _create_sim_tab(self):
        """Create the simulation control tab."""
        # Settings Frame
        settings_frame = ttk.LabelFrame(self.sim_frame, text="⚙️ Optimization Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=10)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row1, text="Simulations per build:").pack(side=tk.LEFT, padx=5)
        ttk.Spinbox(row1, textvariable=self.num_sims_per_build, from_=10, to=1000, increment=10, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="(More = more accurate. Rust: 100-500 recommended)", font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row2, text="Max builds to test:").pack(side=tk.LEFT, padx=5)
        ttk.Spinbox(row2, textvariable=self.max_builds_to_test, from_=100, to=100000, increment=500, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="(Rust can handle 10k+ builds easily)", font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        row3 = ttk.Frame(settings_frame)
        row3.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row3, text="CPU processes:").pack(side=tk.LEFT, padx=5)
        ttk.Spinbox(row3, textvariable=self.num_processes, from_=1, to=32, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="(Python only - ignored when using Rust)", font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Rust mode checkbox
        row4 = ttk.Frame(settings_frame)
        row4.pack(fill=tk.X, padx=10, pady=5)
        
        self.use_rust = tk.BooleanVar(value=RUST_AVAILABLE)
        rust_check = ttk.Checkbutton(row4, text="🦀 Use Rust Engine (50-100x faster)", 
                                     variable=self.use_rust, 
                                     state=tk.NORMAL if RUST_AVAILABLE else tk.DISABLED)
        rust_check.pack(side=tk.LEFT, padx=5)
        
        if RUST_AVAILABLE:
            ttk.Label(row4, text="✅ Rust engine available", 
                     font=('Arial', 9), foreground='green').pack(side=tk.LEFT, padx=10)
        else:
            ttk.Label(row4, text="❌ Rust engine not found (run 'cargo build --release' in hunter-sim-rs/)", 
                     font=('Arial', 9), foreground='red').pack(side=tk.LEFT, padx=10)
        
        # Evolutionary mode checkbox
        row5 = ttk.Frame(settings_frame)
        row5.pack(fill=tk.X, padx=10, pady=5)
        
        self.use_evolutionary = tk.BooleanVar(value=True)
        evo_check = ttk.Checkbutton(row5, text="🧬 Use Evolutionary Optimizer (learns good/bad patterns)", 
                                    variable=self.use_evolutionary)
        evo_check.pack(side=tk.LEFT, padx=5)
        ttk.Label(row5, text="Recommended for high-level builds (faster, smarter)", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Evolutionary settings
        row6 = ttk.Frame(settings_frame)
        row6.pack(fill=tk.X, padx=10, pady=5)
        
        self.evo_population = tk.IntVar(value=500)
        
        ttk.Label(row6, text="Builds per Tier:").pack(side=tk.LEFT, padx=5)
        ttk.Spinbox(row6, textvariable=self.evo_population, from_=100, to=5000, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row6, text="(6 tiers total = 6× this many builds)", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Progressive evolution (curriculum learning)
        row7 = ttk.Frame(settings_frame)
        row7.pack(fill=tk.X, padx=10, pady=5)
        
        self.use_progressive = tk.BooleanVar(value=True)
        prog_check = ttk.Checkbutton(row7, text="📈 Progressive Evolution (level up from 5% → 100% points)", 
                                      variable=self.use_progressive)
        prog_check.pack(side=tk.LEFT, padx=5)
        ttk.Label(row7, text="Finds efficient builds faster by learning at each tier", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Buttons
        btn_frame = ttk.Frame(self.sim_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.start_btn = ttk.Button(btn_frame, text="🚀 Start Optimization", command=self._start_optimization)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹️ Stop", command=self._stop_optimization, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Progress
        progress_frame = ttk.LabelFrame(self.sim_frame, text="📊 Progress")
        progress_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=5)
        
        self.status_label = ttk.Label(progress_frame, text="Ready to start optimization")
        self.status_label.pack(padx=10, pady=5)
        
        # Best tracking display
        best_frame = ttk.Frame(progress_frame)
        best_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.best_max_label = ttk.Label(best_frame, text="🏆 Best Max: -- (Gen --)")
        self.best_max_label.pack(side=tk.LEFT, padx=20)
        
        self.best_avg_label = ttk.Label(best_frame, text="📊 Best Avg: -- (Gen --)")
        self.best_avg_label.pack(side=tk.LEFT, padx=20)
        
        # Log
        log_frame = ttk.LabelFrame(self.sim_frame, text="📋 Log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
    def _create_results_tab(self):
        """Create the results display tab."""
        # Optimization targets
        targets_frame = ttk.LabelFrame(self.results_frame, text="🏆 Best Builds by Category")
        targets_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create a notebook for different optimization results
        self.results_notebook = ttk.Notebook(targets_frame)
        self.results_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create tabs for each optimization mode
        self.result_tabs: Dict[str, scrolledtext.ScrolledText] = {}
        categories = [
            ("🏔️ Highest Stage", "stage"),
            ("💰 Best Loot/Hour", "loot"),
            ("⚡ Fastest Clear", "speed"),
            ("💥 Most Damage", "damage"),
            ("🛡️ Best Survival", "survival"),
            ("⚖️ Top 20 Overall", "all"),
        ]
        
        for label, key in categories:
            frame = ttk.Frame(self.results_notebook)
            self.results_notebook.add(frame, text=label)
            
            text = scrolledtext.ScrolledText(frame, height=25, font=('Consolas', 10))
            text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            self.result_tabs[key] = text
        
        # Export button
        export_frame = ttk.Frame(self.results_frame)
        export_frame.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Button(export_frame, text="📁 Export Best Build to YAML", 
                   command=self._export_best_build).pack(side=tk.LEFT, padx=5)
        ttk.Button(export_frame, text="📋 Copy to Clipboard", 
                   command=self._copy_results).pack(side=tk.LEFT, padx=5)
    
    def _log(self, message: str):
        """Add a message to the log."""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        
    def _get_current_config(self) -> Dict:
        """Build a config dictionary from current input values."""
        hunter_name = self.current_hunter.get()
        if hunter_name == "Ozzy":
            hunter_class = Ozzy
        elif hunter_name == "Knox":
            hunter_class = Knox
        else:
            hunter_class = Borge
        config = hunter_class.load_dummy()
        
        # Update meta
        config["meta"]["level"] = self.hunter_level.get()
        
        # Update stats
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
        
        # Update inscryptions
        for key, entry in self.inscryption_entries.items():
            try:
                config["inscryptions"][key] = int(entry.get())
            except ValueError:
                config["inscryptions"][key] = 0
        
        # Update relics
        for key, entry in self.relic_entries.items():
            try:
                config["relics"][key] = int(entry.get())
            except ValueError:
                config["relics"][key] = 0
        
        # Update gems
        for key, entry in self.gem_entries.items():
            try:
                config["gems"][key] = int(entry.get())
            except ValueError:
                config["gems"][key] = 0
        
        # Update mods
        for key, var in self.mod_vars.items():
            config["mods"][key] = var.get()
        
        return config
    
    def _start_optimization(self):
        """Start the optimization process."""
        if self.is_running:
            return
            
        self.is_running = True
        self.stop_event.clear()  # Reset stop flag
        self.results.clear()
        self.optimization_start_time = time.time()  # Track start time
        
        # Reset best tracking
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_gen = 0
        self.best_avg_gen = 0
        self.best_max_label.configure(text="🏆 Best Max: -- (Gen --)")
        self.best_avg_label.configure(text="📊 Best Avg: -- (Gen --)")
        
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        
        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)
        
        # Start optimization in background thread
        thread = threading.Thread(target=self._run_optimization, daemon=True)
        thread.start()
        
        # Start polling for results
        self.root.after(100, self._poll_results)
        
    def _stop_optimization(self):
        """Stop the optimization process."""
        self.stop_event.set()  # Thread-safe signal
        self._log("⏹️ Stopping optimization...")
        
    def _run_optimization(self):
        """Run the optimization (in background thread)."""
        try:
            hunter_name = self.current_hunter.get()
            if hunter_name == "Ozzy":
                hunter_class = Ozzy
            elif hunter_name == "Knox":
                hunter_class = Knox
            else:
                hunter_class = Borge
            level = self.hunter_level.get()
            
            self._log(f"🚀 Starting optimization for {hunter_name} at level {level}")
            self._log(f"   Talent points available: {level}")
            self._log(f"   Attribute points available: {level * 3}")
            
            # Get base config
            base_config = self._get_current_config()
            
            # Check if using evolutionary mode
            use_evolutionary = self.use_evolutionary.get()
            use_progressive = self.use_progressive.get()
            
            if use_evolutionary and level >= 30:
                if use_progressive:
                    # Use progressive evolution (curriculum learning)
                    self._run_progressive_evolution(hunter_class, level, base_config)
                else:
                    # Use standard evolutionary optimizer
                    self._run_evolutionary_optimization(hunter_class, level, base_config)
            else:
                # Use traditional sampling optimization
                self._run_sampling_optimization(hunter_class, level, base_config)
                
        except Exception as e:
            self._log(f"\n❌ Error during optimization: {str(e)}")
            import traceback
            self._log(traceback.format_exc())
            self.result_queue.put(('error', str(e), None, None))
    
    def _run_evolutionary_optimization(self, hunter_class, level: int, base_config: Dict):
        """Run evolutionary/genetic optimization that learns from results."""
        import copy
        import random
        
        self._log("\n🧬 Using Evolutionary Optimizer")
        self._log("   This mode learns from simulation results to find better builds faster")
        
        generator = BuildGenerator(hunter_class, level)
        optimizer = EvolutionaryOptimizer(
            generator, 
            population_size=self.evo_population.get(),
            elite_ratio=0.15,
            mutation_rate=0.2,
            crossover_rate=0.7
        )
        
        # Use 6 generations to match the 6 tiers in progressive mode
        num_generations = 6
        num_sims = self.num_sims_per_build.get()
        num_procs = self.num_processes.get()
        use_rust = self.use_rust.get() and RUST_AVAILABLE
        
        # Max builds to test = population × generations
        # This allows testing many unique builds instead of evolving the same population
        max_builds_to_test = optimizer.population_size * num_generations
        
        self._log(f"   Max builds to test: {max_builds_to_test} (builds_per_tier × 6)")
        self._log(f"   Sims per build: {num_sims}")
        
        if use_rust:
            self._log(f"   🦀 Using Rust engine")
        else:
            self._log(f"   🐍 Using Python engine with {num_procs} processes")
        
        # Initialize first population
        self._log("\n📊 Initializing population...")
        population = optimizer.initialize_population()
        self._log(f"   Created {len(population)} initial builds")
        
        total_tested = 0
        all_tested_builds = []  # Track all builds we've tested
        
        for gen in range(num_generations):
            if self.stop_event.is_set():
                self._log('\n⏹️ Optimization stopped by user.')
                break
            
            # Check if we've hit our target
            if total_tested >= max_builds_to_test:
                self._log(f'\n✅ Reached max builds limit ({max_builds_to_test})')
                break
            
            self._log(f"\n🔄 Generation {gen + 1}/{num_generations} (tested {total_tested}/{max_builds_to_test} builds so far)")
            
            # Evaluate current population
            gen_results = []
            for i, build in enumerate(population):
                if self.stop_event.is_set():
                    break
                
                if total_tested >= max_builds_to_test:
                    break
                
                # Create config for this build
                config = copy.deepcopy(base_config)
                config["talents"] = build.get('talents', {})
                config["attributes"] = build.get('attributes', {})
                
                try:
                    # Run simulations
                    if use_rust:
                        result = self._simulate_build_rust(hunter_class, config, num_sims)
                    else:
                        result = self._simulate_build(hunter_class, config, num_sims, num_procs)
                    
                    if result:
                        self.results.append(result)
                        gen_results.append({
                            'avg_stage': result.avg_final_stage,
                            'loot_per_hour': result.avg_loot_per_hour,
                            'survival_rate': result.survival_rate if hasattr(result, 'survival_rate') else (1.0 if result.avg_final_stage > 0 else 0.0),
                            'boss1_survival': result.boss1_survival if hasattr(result, 'boss1_survival') else 0,
                            'boss2_survival': result.boss2_survival if hasattr(result, 'boss2_survival') else 0,
                            'boss3_survival': result.boss3_survival if hasattr(result, 'boss3_survival') else 0,
                            'total_damage': result.avg_damage if hasattr(result, 'avg_damage') else 0,
                            'clear_time': result.avg_elapsed_time if hasattr(result, 'avg_elapsed_time') else 100,
                            'died_early': False  # Explicitly mark as successful
                        })
                    else:
                        gen_results.append({'died_early': True})
                except Exception:
                    gen_results.append({'died_early': True})
                
                total_tested += 1
                progress = min(100, (total_tested / max_builds_to_test) * 100)
                self.result_queue.put(('progress', progress, total_tested, max_builds_to_test))
                
                # Log progress within generation every 10% or 100 builds
                log_interval = max(100, len(population) // 10)
                if (i + 1) % log_interval == 0:
                    elapsed = time.time() - self.optimization_start_time
                    rate = total_tested / elapsed if elapsed > 0 else 0
                    self.result_queue.put(('log', f"   ...{i+1}/{len(population)} builds in gen {gen+1} ({rate:.1f} builds/sec)", None, None))
            
            # Only process results for builds we actually tested
            gen_results = gen_results[:len(population)]
            
            # Update fitness scores
            optimizer.update_population_fitness(gen_results, "Balanced")
            stats = optimizer.get_stats()
            
            # Collect stats from this generation for debugging
            stages = [r.get('avg_stage', 0) for r in gen_results if not r.get('died_early', False)]
            boss1_rates = [r.get('boss1_survival', 0) for r in gen_results if not r.get('died_early', False)]
            
            if stages:
                avg_stage = sum(stages) / len(stages)
                max_stage = max(stages)
                avg_boss1 = sum(boss1_rates) / len(boss1_rates) if boss1_rates else 0
                self._log(f"   Stages: avg={avg_stage:.1f}, max={max_stage:.1f}, boss1={avg_boss1:.1%}")
                
                # Send best update to UI
                self.result_queue.put(('best_update', {
                    'best_max': int(max_stage),
                    'best_avg': avg_stage,
                    'gen': gen + 1
                }, None, None))
            
            self._log(f"   Best fitness: {stats['best_fitness']:.4f}, Avg: {stats['avg_fitness']:.4f}")
            self._log(f"   Patterns: {stats['bad_patterns']} bad, {stats['good_patterns']} good, {stats['worthless_patterns']} worthless")
            
            # Get best build from this generation
            best_builds = optimizer.get_best_builds(1)
            if best_builds:
                best_build, best_fitness = best_builds[0]
                self._log(f"   Current best: fitness={best_fitness:.2f}")
            
            # Generate NEW unique builds for next generation (unless last gen or at limit)
            if gen < num_generations - 1 and total_tested < max_builds_to_test:
                self._log(f"   Generating new unique builds for next generation...")
                
                # Try to generate a full population of new builds
                new_population = []
                max_attempts = optimizer.population_size * 20  # Try harder to find new builds
                attempts = 0
                
                while len(new_population) < optimizer.population_size and attempts < max_attempts:
                    # Use learned patterns to guide generation
                    strategy = random.choice(['random', 'defensive', 'offensive', 'balanced', 'focused'])
                    builds = optimizer.generator.generate_smart_sample(sample_size=50, strategy=strategy)
                    
                    for talents, attrs in builds:
                        candidate = {'talents': talents, 'attributes': attrs}
                        if not optimizer._is_duplicate(candidate):
                            new_population.append(candidate)
                            optimizer._mark_tested(candidate)
                            if len(new_population) >= optimizer.population_size:
                                break
                    
                    attempts += 50
                
                if new_population:
                    population = new_population
                    self._log(f"   Generated {len(population)} new unique builds for next generation")
                else:
                    self._log(f"   ⚠️ Build space exhausted - no more unique builds to test")
                    break  # Exit early, we've tested everything possible
        
        # Final summary - use actual generations completed
        actual_generations = gen + 1  # gen is 0-indexed
        total_time = time.time() - self.optimization_start_time
        rate = total_tested / total_time if total_time > 0 else 0
        
        self._log(f"\n✅ Evolutionary optimization complete!")
        self._log(f"   Tested {total_tested:,} unique builds across {actual_generations} generations")
        self._log(f"   Time: {total_time:.1f}s ({rate:.1f} builds/sec)")
        self._log(f"   Found {len(self.results):,} valid builds")
        
        final_stats = optimizer.get_stats()
        self._log(f"\n📈 Optimizer learned:")
        self._log(f"   • {final_stats['bad_patterns']} bad patterns to avoid")
        self._log(f"   • {final_stats['good_patterns']} promising patterns")
        self._log(f"   • {final_stats['worthless_patterns']} completely worthless patterns filtered")
        
        self.result_queue.put(('done', None, None, None))
    
    def _run_progressive_evolution(self, hunter_class, level: int, base_config: Dict):
        """Run progressive evolution - start with few points, find what works, scale up.
        
        This mimics how a player would level up and allocate points:
        1. Start with 5% of available points - find what works
        2. Scale to 10%, 20%, 40%, 70%, 100% - each tier inherits from previous
        3. Top builds from each tier become the "seed" for next tier
        
        Much more efficient than random walk over full space.
        """
        import copy
        import random
        
        self._log("\n📈 Using Progressive Evolution (Curriculum Learning)")
        self._log("   Starting with limited points, finding what works, then scaling up")
        
        # Progressive tiers - what % of points to use at each tier
        tiers = [0.05, 0.10, 0.20, 0.40, 0.70, 1.0]  # 5%, 10%, 20%, 40%, 70%, 100%
        
        num_sims = self.num_sims_per_build.get()
        num_procs = self.num_processes.get()
        use_rust = self.use_rust.get() and RUST_AVAILABLE
        pop_size = self.evo_population.get()
        builds_per_tier = pop_size  # How many builds to test per tier
        
        total_builds_planned = len(tiers) * builds_per_tier
        self._log(f"   Tiers: {[f'{int(t*100)}%' for t in tiers]}")
        self._log(f"   Builds per tier: {builds_per_tier}")
        self._log(f"   Total planned: {total_builds_planned}")
        
        if use_rust:
            self._log(f"   🦀 Using Rust engine")
        else:
            self._log(f"   🐍 Using Python engine with {num_procs} processes")
        
        total_tested = 0
        elite_patterns = []  # Patterns that worked well from previous tier
        
        for tier_idx, tier_pct in enumerate(tiers):
            if self.stop_event.is_set():
                self._log('\n⏹️ Optimization stopped by user.')
                break
            
            # Calculate points for this tier
            tier_talent_points = max(1, int(level * tier_pct))
            tier_attr_points = max(3, int(level * 3 * tier_pct))
            tier_level = max(1, int(level * tier_pct))  # Effective level for this tier
            
            self._log(f"\n{'='*60}")
            self._log(f"📊 TIER {tier_idx + 1}/{len(tiers)}: {int(tier_pct*100)}% points (Level ~{tier_level})")
            self._log(f"   Talent points: {tier_talent_points}/{level}")
            self._log(f"   Attribute points: {tier_attr_points}/{level*3}")
            if elite_patterns:
                self._log(f"   Building on {len(elite_patterns)} elite patterns from previous tier")
            
            # Create a generator with limited points for this tier
            tier_generator = BuildGenerator(hunter_class, tier_level)
            # Override the points to use our tier-specific amounts
            tier_generator.talent_points = tier_talent_points
            tier_generator.attribute_points = tier_attr_points
            
            tier_results = []
            tier_builds = []
            tested_hashes = set()  # Track unique builds in this tier
            consecutive_dupes = 0  # Track consecutive duplicate attempts
            max_consecutive_dupes = 100  # Stop tier if we can't find new builds
            
            for i in range(builds_per_tier):
                if self.stop_event.is_set():
                    break
                
                # Check if we've exhausted unique builds for this tier
                if consecutive_dupes >= max_consecutive_dupes:
                    self._log(f"   ⚡ Tier exhausted after {len(tier_results)} unique builds (no new builds found)")
                    break
                
                # Generate a build - if we have elite patterns, bias towards them
                talents, attrs = None, None
                if elite_patterns and random.random() < 0.7:  # 70% chance to build from elite
                    # Pick an elite pattern and extend it (always succeeds)
                    elite = random.choice(elite_patterns)
                    talents, attrs = self._extend_elite_pattern(
                        elite, tier_generator, tier_talent_points, tier_attr_points
                    )
                else:
                    # Generate fresh random walk build (30% chance, or no elites yet)
                    builds = tier_generator.generate_smart_sample(sample_size=1)
                    if builds:
                        talents, attrs = builds[0]
                    else:
                        consecutive_dupes += 1
                        continue
                
                # Check if this build is a duplicate
                build_hash = (tuple(sorted(talents.items())), tuple(sorted(attrs.items())))
                if build_hash in tested_hashes:
                    consecutive_dupes += 1
                    continue
                tested_hashes.add(build_hash)
                consecutive_dupes = 0  # Reset counter on successful unique build
                
                # VALIDATION: Ensure build uses correct number of points
                talent_spent = sum(talents.values())
                attr_costs_local = {a: tier_generator.costs["attributes"][a]["cost"] for a in attrs}
                attr_spent = sum(attrs[a] * attr_costs_local[a] for a in attrs)
                
                if talent_spent < tier_talent_points * 0.95 or attr_spent < tier_attr_points * 0.95:
                    # Build is significantly under-spent - skip it and try again
                    consecutive_dupes += 1
                    continue
                
                # Create config for this build
                config = copy.deepcopy(base_config)
                config["talents"] = talents
                config["attributes"] = attrs
                
                try:
                    # Run simulations
                    if use_rust:
                        result = self._simulate_build_rust(hunter_class, config, num_sims)
                    else:
                        result = self._simulate_build(hunter_class, config, num_sims, num_procs)
                    
                    if result:
                        self.results.append(result)
                        tier_results.append({
                            'avg_stage': result.avg_final_stage,
                            'max_stage': result.highest_stage,
                            'talents': talents,
                            'attributes': attrs
                        })
                        tier_builds.append({'talents': talents, 'attributes': attrs})
                except Exception:
                    pass
                
                total_tested += 1
                progress = min(100, (total_tested / total_builds_planned) * 100)
                self.result_queue.put(('progress', progress, total_tested, total_builds_planned))
                
                # Log progress every 20% of tier
                if (i + 1) % max(1, builds_per_tier // 5) == 0:
                    elapsed = time.time() - self.optimization_start_time
                    rate = total_tested / elapsed if elapsed > 0 else 0
                    self.result_queue.put(('log', f"   ...{i+1}/{builds_per_tier} ({rate:.1f} builds/sec)", None, None))
            
            # Analyze this tier's results
            if tier_results:
                stages = [r['avg_stage'] for r in tier_results]
                avg_stage = sum(stages) / len(stages)
                max_stage = max(r['max_stage'] for r in tier_results)
                best_avg = max(stages)
                
                self._log(f"   Results: avg={avg_stage:.1f}, best_avg={best_avg:.1f}, max={max_stage}")
                
                # Send best update
                self.result_queue.put(('best_update', {
                    'best_max': max_stage,
                    'best_avg': best_avg,
                    'gen': tier_idx + 1
                }, None, None))
                
                # Select elite patterns for next tier
                # Promote at least 100 patterns (or 10% of tested, whichever is greater)
                # If fewer than 100 builds exist, promote ALL of them
                tier_results.sort(key=lambda x: x['avg_stage'], reverse=True)
                min_elites = 100
                pct_elites = len(tier_results) // 10  # 10%
                if len(tier_results) <= min_elites:
                    # Small tier - promote ALL builds to avoid cutting off promising paths
                    elite_count = len(tier_results)
                else:
                    # Larger tier - promote at least 100 or 10%, whichever is greater
                    elite_count = max(min_elites, pct_elites)
                elite_patterns = [
                    {'talents': r['talents'], 'attributes': r['attributes']}
                    for r in tier_results[:elite_count]
                ]
                self._log(f"   Promoted {len(elite_patterns)} elite patterns to next tier")
        
        # Final summary
        total_time = time.time() - self.optimization_start_time
        rate = total_tested / total_time if total_time > 0 else 0
        
        self._log(f"\n{'='*60}")
        self._log(f"✅ Progressive evolution complete!")
        self._log(f"   Tested {total_tested:,} builds across {len(tiers)} tiers")
        self._log(f"   Time: {total_time:.1f}s ({rate:.1f} builds/sec)")
        self._log(f"   Found {len(self.results):,} valid builds")
        
        self.result_queue.put(('done', None, None, None))
    
    def _extend_elite_pattern(self, elite: Dict, generator: BuildGenerator,
                              target_talents: int, target_attrs: int) -> Tuple[Dict, Dict]:
        """Extend an elite pattern with more points using random walk.
        
        Takes a successful build from a previous tier and adds more points
        using random walk to explore extensions of what worked.
        
        ALWAYS succeeds - uses unlimited attributes to sink any remaining points.
        """
        import random
        
        # Start with the elite's allocation - ensure all talents/attrs are initialized
        talents_list = list(generator.costs["talents"].keys())
        attrs_list = list(generator.costs["attributes"].keys())
        
        talents = {t: elite.get('talents', {}).get(t, 0) for t in talents_list}
        attrs = {a: elite.get('attributes', {}).get(a, 0) for a in attrs_list}
        
        # Calculate how many points elite used
        elite_talent_spent = sum(talents.values())
        elite_attr_spent = sum(
            attrs[a] * generator.costs["attributes"][a]["cost"]
            for a in attrs_list
        )
        
        # Points we need to add
        talent_to_add = target_talents - elite_talent_spent
        attr_to_add = target_attrs - elite_attr_spent
        
        # Find unlimited attributes (our fallback sinks)
        attr_costs = {a: generator.costs["attributes"][a]["cost"] for a in attrs_list}
        attr_max = {a: generator.costs["attributes"][a]["max"] for a in attrs_list}
        unlimited_attrs = [a for a in attrs_list if attr_max[a] == float('inf')]
        
        # Extend talents using random walk
        talent_max = {t: generator.costs["talents"][t]["max"] for t in talents_list}
        
        while talent_to_add > 0:
            # Find KNOWN talents that can accept more points (exclude unknown_talent)
            valid = [t for t in talents_list 
                     if t != 'unknown_talent' and (talent_max[t] == float('inf') or talents[t] < int(talent_max[t]))]
            
            # Only use unknown_talent as LAST RESORT when all known talents are maxed
            if not valid:
                if 'unknown_talent' in talents_list:
                    valid = ['unknown_talent']
                else:
                    break  # All talents maxed
            
            chosen = random.choice(valid)
            talents[chosen] += 1
            talent_to_add -= 1
        
        # Extend attributes using random walk
        deps = getattr(generator.hunter_class, 'attribute_dependencies', {})
        exclusions = getattr(generator.hunter_class, 'attribute_exclusions', [])
        
        remaining = attr_to_add
        
        while remaining > 0:
            valid_attrs = []
            for attr in attrs_list:
                cost = attr_costs[attr]
                if cost > remaining:
                    continue
                # Unlimited attributes can always accept more
                if attr_max[attr] != float('inf'):
                    max_lvl = int(attr_max[attr])
                    if attrs[attr] >= max_lvl:
                        continue
                # Check dependencies
                if attr in deps:
                    if not all(attrs.get(req, 0) >= lvl for req, lvl in deps[attr].items()):
                        continue
                # Check point gates
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
                # FALLBACK: Dump into first unlimited attribute that we can afford
                for sink_attr in unlimited_attrs:
                    if attr_costs[sink_attr] <= remaining:
                        attrs[sink_attr] += 1
                        remaining -= attr_costs[sink_attr]
                        break
                else:
                    # Can't afford any unlimited attr (remaining < min cost)
                    break
            else:
                # No valid attrs and no unlimited - shouldn't happen, but break to avoid infinite loop
                break
        
        return talents, attrs
    
    def _run_sampling_optimization(self, hunter_class, level: int, base_config: Dict):
        """Run traditional random sampling optimization."""
        import copy
        
        # Generate combinations
        self._log("\n📊 Generating build combinations...")
        generator = BuildGenerator(hunter_class, level)
        max_builds = self.max_builds_to_test.get()
        
        # First, try to estimate the total number of combinations
        talent_combos = generator.get_talent_combinations()
        self._log(f"   Found {len(talent_combos)} talent combinations")
        
        # For attributes, we need to be careful with high-level characters
        # since they can have millions of combinations
        estimated_attr_combos = level * 3 * 2  # Rough estimate
        if level > 50:
            # Use smart sampling for high-level characters
            self._log(f"   High level detected - using smart sampling strategy")
            build_combos = generator.generate_smart_sample(max_builds)
            total_combos = f"~{max_builds:,}+ (smart sampled)"
        else:
            # For lower levels, we can enumerate all combinations
            attr_combos = generator.get_attribute_combinations(max_per_infinite=20)
            self._log(f"   Found {len(attr_combos)} attribute combinations")
            
            total_combos_num = len(talent_combos) * len(attr_combos)
            total_combos = f"{total_combos_num:,}"
            self._log(f"\n📈 Total possible builds: {total_combos}")
            
            # Sample if too many
            if total_combos_num > max_builds:
                self._log(f"⚠️ Sampling {max_builds:,} random builds from {total_combos_num:,} possibilities")
                import random
                all_combos = list(itertools.product(talent_combos, attr_combos))
                random.shuffle(all_combos)
                build_combos = all_combos[:max_builds]
            else:
                build_combos = list(itertools.product(talent_combos, attr_combos))
        
        num_sims = self.num_sims_per_build.get()
        num_procs = self.num_processes.get()
        use_rust = self.use_rust.get() and RUST_AVAILABLE
        
        self._log(f"\n🔄 Testing {len(build_combos):,} builds with {num_sims} simulations each...")
        if use_rust:
            self._log(f"   🦀 Using Rust engine (high performance)")
        else:
            self._log(f"   🐍 Using Python engine with {num_procs} CPU processes")
        self._log("")
        
        tested = 0
        for i, (talents, attributes) in enumerate(build_combos):
            if self.stop_event.is_set():
                self.result_queue.put(('log', '\n⏹️ Optimization stopped by user.', None, None))
                break
            
            # Create config for this build
            config = copy.deepcopy(base_config)
            config["talents"] = talents
            config["attributes"] = attributes
            
            try:
                # Run simulations for this build
                if use_rust:
                    result = self._simulate_build_rust(hunter_class, config, num_sims)
                else:
                    result = self._simulate_build(hunter_class, config, num_sims, num_procs)
                if result:
                    self.results.append(result)
            except Exception as e:
                # Skip invalid builds
                pass
            
            tested += 1
            progress = (tested / len(build_combos)) * 100
            self.result_queue.put(('progress', progress, tested, len(build_combos)))
            
            # Log every 1% or every 100 builds, whichever is less frequent
            log_interval = max(100, len(build_combos) // 100)
            if tested % log_interval == 0:
                elapsed = time.time() - self.optimization_start_time
                rate = tested / elapsed if elapsed > 0 else 0
                self.result_queue.put(('log', f"   {tested:,}/{len(build_combos):,} builds ({progress:.1f}%) - {rate:.1f} builds/sec", None, None))
        
        # Final summary
        total_time = time.time() - self.optimization_start_time
        rate = tested / total_time if total_time > 0 else 0
        self._log(f"\n✅ Optimization complete!")
        self._log(f"   Tested {tested:,} builds in {total_time:.1f}s ({rate:.1f} builds/sec)")
        self._log(f"   Found {len(self.results):,} valid builds")
        self.result_queue.put(('done', None, None, None))
    
    def _simulate_build_sequential(self, hunter_class, config: Dict, num_sims: int) -> BuildResult:
        """Run simulations sequentially (low memory usage, for advisor)."""
        results_list = []
        
        for _ in range(num_sims):
            sim = Simulation(hunter_class(config))
            results_list.append(sim.run())
        
        if not results_list:
            return None
        
        return self._aggregate_results(config, results_list)
    
    def _simulate_build(self, hunter_class, config: Dict, num_sims: int, num_procs: int) -> BuildResult:
        """Run simulations for a single build and return results."""
        results_list = []
        
        if num_procs > 1:
            with ProcessPoolExecutor(max_workers=num_procs) as executor:
                results_list = list(executor.map(
                    sim_worker, 
                    [hunter_class] * num_sims, 
                    [config] * num_sims
                ))
        else:
            for _ in range(num_sims):
                sim = Simulation(hunter_class(config))
                results_list.append(sim.run())
        
        if not results_list:
            return None
        
        return self._aggregate_results(config, results_list)
    
    def _simulate_build_rust(self, hunter_class, config: Dict, num_sims: int) -> BuildResult:
        """Run simulations using the high-performance Rust engine."""
        if not RUST_AVAILABLE:
            # Fall back to Python
            return self._simulate_build_sequential(hunter_class, config, num_sims)
        
        # Determine hunter type
        if hunter_class == Borge:
            hunter_type = "Borge"
        elif hunter_class == Ozzy:
            hunter_type = "Ozzy"
        elif hunter_class == Knox:
            hunter_type = "Knox"
        else:
            hunter_type = "Borge"
        
        try:
            # Get level from the correct location in config
            level = config.get("meta", {}).get("level", 100)
            
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
                num_sims=num_sims,
                parallel=True
            )
            
            # Convert Rust result to BuildResult
            stats = result.get("stats", {})
            return BuildResult(
                talents=config.get("talents", {}).copy(),
                attributes=config.get("attributes", {}).copy(),
                avg_final_stage=stats.get("avg_stage", 0),
                highest_stage=stats.get("max_stage", 0),
                lowest_stage=stats.get("min_stage", 0),
                avg_loot_per_hour=stats.get("avg_loot_per_hour", 0),
                avg_damage=stats.get("avg_damage", 0),
                avg_kills=stats.get("avg_kills", 0),
                avg_elapsed_time=stats.get("avg_time", 0),
                avg_damage_taken=stats.get("avg_damage_taken", 0),
                survival_rate=stats.get("survival_rate", 0),
                boss1_survival=stats.get("boss1_survival", 0),
                boss2_survival=stats.get("boss2_survival", 0),
                boss3_survival=stats.get("boss3_survival", 0),
                boss4_survival=stats.get("boss4_survival", 0),
                boss5_survival=stats.get("boss5_survival", 0),
                config=config,
            )
        except Exception as e:
            # Fall back to Python on error
            print(f"Rust simulation failed: {e}, falling back to Python")
            return self._simulate_build_sequential(hunter_class, config, num_sims)
    
    def _aggregate_results(self, config: Dict, results_list: List) -> BuildResult:
        """Aggregate simulation results into a BuildResult."""
        final_stages = [r['final_stage'] for r in results_list]
        elapsed_times = [r['elapsed_time'] for r in results_list]
        damages = [r['damage'] for r in results_list]
        kills = [r['kills'] for r in results_list]
        damage_takens = [r['damage_taken'] for r in results_list]
        loots = [r['total_loot'] for r in results_list]
        
        # Calculate loot per hour
        loot_per_hours = [(loots[i] / (elapsed_times[i] / 3600)) if elapsed_times[i] > 0 else 0 
                          for i in range(len(loots))]
        
        # Calculate legacy survival rate (didn't die at a boss stage ending in 00)
        boss_deaths = sum(1 for s in final_stages if s % 100 == 0 and s > 0)
        survival_rate = 1 - (boss_deaths / len(final_stages))
        
        # Calculate boss milestone survival rates
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
            config=config,
        )
    
    def _poll_results(self):
        """Poll for results from the background thread."""
        try:
            while True:
                msg_type, data, tested, total = self.result_queue.get_nowait()
                
                if msg_type == 'progress':
                    self.progress_var.set(data)
                    # Calculate ETA
                    elapsed = time.time() - self.optimization_start_time
                    if tested > 0:
                        builds_per_sec = tested / elapsed
                        remaining = total - tested
                        eta_seconds = remaining / builds_per_sec if builds_per_sec > 0 else 0
                        if eta_seconds < 60:
                            eta_str = f"{eta_seconds:.0f}s"
                        elif eta_seconds < 3600:
                            eta_str = f"{eta_seconds/60:.1f}m"
                        else:
                            eta_str = f"{eta_seconds/3600:.1f}h"
                        self.status_label.configure(
                            text=f"Testing build {tested:,}/{total:,} | {builds_per_sec:.1f} builds/sec | ETA: {eta_str}"
                        )
                    else:
                        self.status_label.configure(text=f"Testing build {tested}/{total}...")
                elif msg_type == 'best_update':
                    # data is dict with best_max, best_avg, gen
                    if data.get('best_max', 0) > self.best_max_stage:
                        self.best_max_stage = data['best_max']
                        self.best_max_gen = data.get('gen', 0)
                        self.best_max_label.configure(text=f"🏆 Best Max: {self.best_max_stage} (Gen {self.best_max_gen})")
                    if data.get('best_avg', 0) > self.best_avg_stage:
                        self.best_avg_stage = data['best_avg']
                        self.best_avg_gen = data.get('gen', 0)
                        self.best_avg_label.configure(text=f"📊 Best Avg: {self.best_avg_stage:.1f} (Gen {self.best_avg_gen})")
                elif msg_type == 'log':
                    self._log(data)
                elif msg_type == 'done':
                    self._optimization_complete()
                    return
                elif msg_type == 'error':
                    self._optimization_complete()
                    return
                    
        except queue.Empty:
            pass
        
        if self.is_running:
            self.root.after(100, self._poll_results)
    
    def _optimization_complete(self):
        """Handle optimization completion."""
        self.is_running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.progress_var.set(100)
        self.status_label.configure(text=f"Complete! Found {len(self.results)} valid builds.")
        
        # Update results display
        self._display_results()
        
        # Switch to results tab
        self.notebook.select(self.results_frame)
    
    def _display_results(self):
        """Display the optimization results."""
        if not self.results:
            for text_widget in self.result_tabs.values():
                text_widget.configure(state=tk.NORMAL)
                text_widget.delete(1.0, tk.END)
                text_widget.insert(tk.END, "No valid builds found. Try adjusting your settings.")
                text_widget.configure(state=tk.DISABLED)
            return
        
        # Sort by different criteria
        by_stage = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[:10]
        by_loot = sorted(self.results, key=lambda r: r.avg_loot_per_hour, reverse=True)[:10]
        by_speed = sorted(self.results, key=lambda r: r.avg_elapsed_time)[:10]
        by_damage = sorted(self.results, key=lambda r: r.avg_damage, reverse=True)[:10]
        by_survival = sorted(self.results, key=lambda r: r.survival_rate, reverse=True)[:10]
        
        # Display each category
        self._display_category(self.result_tabs["stage"], by_stage, "Avg Stage", 
                               lambda r: f"{r.avg_final_stage:.1f}")
        self._display_category(self.result_tabs["loot"], by_loot, "Loot/Hour",
                               lambda r: f"{r.avg_loot_per_hour:.2f}")
        self._display_category(self.result_tabs["speed"], by_speed, "Avg Time (s)",
                               lambda r: f"{r.avg_elapsed_time:.1f}")
        self._display_category(self.result_tabs["damage"], by_damage, "Avg Damage",
                               lambda r: f"{r.avg_damage:,.0f}")
        self._display_category(self.result_tabs["survival"], by_survival, "Survival %",
                               lambda r: f"{r.survival_rate*100:.1f}%")
        
        # All results
        all_text = self.result_tabs["all"]
        all_text.configure(state=tk.NORMAL)
        all_text.delete(1.0, tk.END)
        
        all_text.insert(tk.END, "=" * 80 + "\n")
        all_text.insert(tk.END, "TOP 20 BUILDS (sorted by average stage reached)\n")
        all_text.insert(tk.END, "=" * 80 + "\n\n")
        
        for i, result in enumerate(by_stage[:20], 1):
            all_text.insert(tk.END, f"{'='*60}\n")
            all_text.insert(tk.END, f"RANK #{i}\n")
            all_text.insert(tk.END, f"{'='*60}\n")
            all_text.insert(tk.END, self._format_build_result(result))
            all_text.insert(tk.END, "\n\n")
        
        all_text.configure(state=tk.DISABLED)
    
    def _display_category(self, text_widget, results: List[BuildResult], metric_name: str, metric_fn):
        """Display results for a specific category."""
        text_widget.configure(state=tk.NORMAL)
        text_widget.delete(1.0, tk.END)
        
        text_widget.insert(tk.END, f"{'='*80}\n")
        text_widget.insert(tk.END, f"TOP 10 BUILDS BY {metric_name.upper()}\n")
        text_widget.insert(tk.END, f"{'='*80}\n\n")
        
        for i, result in enumerate(results, 1):
            text_widget.insert(tk.END, f"#{i}: {metric_name} = {metric_fn(result)}\n")
            text_widget.insert(tk.END, "-" * 60 + "\n")
            text_widget.insert(tk.END, self._format_build_result(result))
            text_widget.insert(tk.END, "\n\n")
        
        text_widget.configure(state=tk.DISABLED)
    
    def _format_build_result(self, result: BuildResult) -> str:
        """Format a build result for display."""
        lines = []
        
        lines.append("PERFORMANCE:")
        lines.append(f"  Avg Stage: {result.avg_final_stage:.1f} (High: {result.highest_stage}, Low: {result.lowest_stage})")
        lines.append(f"  Avg Time: {result.avg_elapsed_time:.1f}s ({result.avg_elapsed_time/60:.1f} min)")
        lines.append(f"  Loot/Hour: {result.avg_loot_per_hour:.2f}")
        lines.append(f"  Avg Damage: {result.avg_damage:,.0f}")
        
        # Boss milestone survival rates
        lines.append("  Boss Survival:")
        if result.boss1_survival > 0 or result.avg_final_stage >= 100:
            lines.append(f"    Boss 1 (100): {result.boss1_survival*100:.1f}%")
        if result.boss2_survival > 0 or result.avg_final_stage >= 200:
            lines.append(f"    Boss 2 (200): {result.boss2_survival*100:.1f}%")
        if result.boss3_survival > 0 or result.avg_final_stage >= 300:
            lines.append(f"    Boss 3 (300): {result.boss3_survival*100:.1f}%")
        if result.boss4_survival > 0 or result.avg_final_stage >= 400:
            lines.append(f"    Boss 4 (400): {result.boss4_survival*100:.1f}%")
        if result.boss5_survival > 0 or result.avg_final_stage >= 500:
            lines.append(f"    Boss 5 (500): {result.boss5_survival*100:.1f}%")
        lines.append("")
        
        lines.append("TALENTS:")
        active_talents = {k: v for k, v in result.talents.items() if v > 0}
        if active_talents:
            for talent, level in active_talents.items():
                lines.append(f"  {talent.replace('_', ' ').title()}: {level}")
        else:
            lines.append("  (none)")
        lines.append("")
        
        lines.append("ATTRIBUTES:")
        active_attrs = {k: v for k, v in result.attributes.items() if v > 0}
        if active_attrs:
            for attr, level in active_attrs.items():
                lines.append(f"  {attr.replace('_', ' ').title()}: {level}")
        else:
            lines.append("  (none)")
        
        return "\n".join(lines)
    
    def _export_best_build(self):
        """Export the best build to a YAML file."""
        if not self.results:
            messagebox.showwarning("No Results", "No results to export. Run optimization first.")
            return
        
        from tkinter import filedialog
        import yaml
        
        best = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[0]
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".yaml",
            filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")],
            initialfile=f"optimized_{self.current_hunter.get().lower()}.yaml"
        )
        
        if filename:
            with open(filename, 'w') as f:
                yaml.dump(best.config, f, default_flow_style=False, sort_keys=False)
            messagebox.showinfo("Export Complete", f"Build exported to:\n{filename}")
    
    def _copy_results(self):
        """Copy current results tab content to clipboard."""
        current_tab = self.results_notebook.index(self.results_notebook.select())
        tab_keys = ["stage", "loot", "speed", "damage", "survival", "all"]
        
        if current_tab < len(tab_keys):
            text_widget = self.result_tabs[tab_keys[current_tab]]
            content = text_widget.get(1.0, tk.END)
            self.root.clipboard_clear()
            self.root.clipboard_append(content)
            messagebox.showinfo("Copied", "Results copied to clipboard!")


def main():
    """Main entry point for the GUI application."""
    root = tk.Tk()
    app = HunterSimGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
