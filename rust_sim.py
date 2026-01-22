"""
Python wrapper for the Rust Hunter Simulator.

This module provides a Python interface to call the high-performance Rust
simulation engine using native Python bindings (PyO3).
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
import sys

# Try to import native bindings first
_native_lib = None
_using_native = False

def _init_native_bindings():
    """Initialize native Python bindings if available."""
    global _native_lib, _using_native
    
    if _native_lib is not None:
        return _using_native
    
    # Add the hunter-sim directory to the path for the .pyd file
    script_dir = Path(__file__).parent
    hunter_sim_dir = script_dir / "hunter-sim"
    
    # Try multiple possible locations
    pyd_locations = [
        hunter_sim_dir / "hunter_sim_lib.pyd",
        script_dir / "hunter_sim_lib.pyd",
    ]
    
    pyd_dir = None
    for pyd_path in pyd_locations:
        if pyd_path.exists():
            pyd_dir = str(pyd_path.parent)
            if pyd_dir not in sys.path:
                sys.path.insert(0, pyd_dir)
            break
    
    # On Windows, we need to add the DLL directory explicitly for Python 3.8+
    if sys.platform == 'win32' and hasattr(os, 'add_dll_directory'):
        # Add both the pyd location and the Rust build directory
        dirs_to_add = [
            pyd_dir,
            str(script_dir),  # Add script directory itself
            str(script_dir / "hunter-sim"),  # Add hunter-sim subdirectory
            str(script_dir / "hunter-sim-rs" / "target" / "release"),
        ]
        for dir_path in dirs_to_add:
            if dir_path and Path(dir_path).exists():
                try:
                    os.add_dll_directory(dir_path)
                except (OSError, FileNotFoundError):
                    pass
    
    try:
        import hunter_sim_lib as lib
        _native_lib = lib
        _using_native = True
        print(f"[rust_sim] Using native bindings ({lib.get_available_cores()} cores available)")
    except ImportError as e:
        print(f"[rust_sim] Native bindings not available: {e}")
        print("[rust_sim] Falling back to CLI (slower)")
        _using_native = False
    
    return _using_native

# Initialize on import
_init_native_bindings()


def get_rust_executable() -> Path:
    """Find the Rust hunter-sim executable."""
    # Check for release build
    script_dir = Path(__file__).parent
    rust_dir = script_dir / "hunter-sim-rs"
    
    # Try different possible locations
    candidates = [
        rust_dir / "target" / "release" / "hunter-sim.exe",
        rust_dir / "target" / "release" / "hunter-sim",
        script_dir.parent / "hunter-sim-rs" / "target" / "release" / "hunter-sim.exe",
        script_dir.parent / "hunter-sim-rs" / "target" / "release" / "hunter-sim",
    ]
    
    for path in candidates:
        if path.exists():
            return path
    
    raise FileNotFoundError(
        f"Could not find hunter-sim executable. Tried: {[str(p) for p in candidates]}"
    )


def simulate_from_file(config_path: str, num_sims: int = 100, parallel: bool = True) -> Dict[str, Any]:
    """
    Run simulations using a YAML config file.
    
    Args:
        config_path: Path to the YAML configuration file
        num_sims: Number of simulations to run
        parallel: Whether to use parallel processing
        
    Returns:
        Dictionary with aggregated simulation statistics
    """
    global _native_lib, _using_native
    
    # Use native bindings if available (much faster!)
    if _using_native and _native_lib is not None:
        result_json = _native_lib.simulate_from_file(config_path, num_sims, parallel)
        return json.loads(result_json)
    
    # Fallback to CLI
    exe = get_rust_executable()
    
    cmd = [
        str(exe),
        "--config", str(config_path),
        "--num-sims", str(num_sims),
        "--output", "json"
    ]
    
    if parallel:
        cmd.append("--parallel")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise RuntimeError(f"Simulation failed: {result.stderr}")
    
    return json.loads(result.stdout)


def simulate(
    hunter: str,
    level: int,
    stats: Dict[str, int],
    talents: Dict[str, int],
    attributes: Dict[str, int],
    inscryptions: Optional[Dict[str, int]] = None,
    mods: Optional[Dict[str, bool]] = None,
    relics: Optional[Dict[str, int]] = None,
    gems: Optional[Dict[str, int]] = None,
    bonuses: Optional[Dict[str, Any]] = None,
    num_sims: int = 100,
    parallel: bool = True
) -> Dict[str, Any]:
    """
    Run simulations with a programmatic config.
    
    Args:
        hunter: Hunter type ("Borge", "Ozzy", or "Knox")
        level: Hunter level
        stats: Stats dictionary (hp, power, regen)
        talents: Talents dictionary
        attributes: Attributes dictionary
        inscryptions: Optional inscryptions dictionary
        mods: Optional mods dictionary
        relics: Optional relics dictionary
        gems: Optional gems dictionary
        bonuses: Optional bonuses dictionary (shard_milestone, CMs, diamond_loot, etc.)
        num_sims: Number of simulations to run
        parallel: Whether to use parallel processing
        
    Returns:
        Dictionary with aggregated simulation statistics
    """
    global _native_lib, _using_native
    
    # Build config object
    config = {
        "meta": {
            "hunter": hunter,
            "level": level
        },
        "stats": stats,
        "talents": talents,
        "attributes": attributes,
        "inscryptions": inscryptions or {},
        "mods": mods or {},
        "relics": relics or {},
        "gems": gems or {},
        "bonuses": bonuses or {},
    }
    
    # Use native bindings with direct JSON (much faster, no temp file!)
    if _using_native and _native_lib is not None:
        result_json = _native_lib.simulate(json.dumps(config), num_sims, parallel)
        return json.loads(result_json)
    
    # Fallback: write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        temp_path = f.name
    
    try:
        return simulate_from_file(temp_path, num_sims, parallel)
    finally:
        os.unlink(temp_path)


def simulate_from_dict(config: Dict[str, Any], num_sims: int = 100, parallel: bool = True) -> Dict[str, Any]:
    """
    Run simulations with a config dictionary (matching YAML structure).
    
    Args:
        config: Configuration dictionary matching the YAML structure
        num_sims: Number of simulations to run
        parallel: Whether to use parallel processing
        
    Returns:
        Dictionary with aggregated simulation statistics
    """
    # Write to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        temp_path = f.name
    
    try:
        return simulate_from_file(temp_path, num_sims, parallel)
    finally:
        os.unlink(temp_path)


def simulate_batch(configs: list[Dict[str, Any]], num_sims: int = 100, parallel: bool = True) -> list[Dict[str, Any]]:
    """
    Run simulations with multiple config dictionaries in batch - much faster!
    
    This reduces Python↔Rust call overhead by sending all configs at once.
    
    Args:
        configs: List of configuration dictionaries matching the YAML structure
        num_sims: Number of simulations to run per config
        parallel: Whether to use parallel processing
        
    Returns:
        List of dictionaries with aggregated simulation statistics (one per config)
    """
    global _native_lib, _using_native
    
    # Use native bindings if available (much faster!)
    if _using_native and _native_lib is not None:
        # Convert configs to JSON strings
        config_jsons = [json.dumps(config) for config in configs]
        
        # Call Rust batch function
        result_jsons = _native_lib.simulate_batch(config_jsons, num_sims, parallel)
        
        # Parse results
        return [json.loads(result_json) for result_json in result_jsons]
    
    # Fallback: simulate each config individually
    print("[rust_sim] Warning: Native bindings not available, falling back to sequential batch")
    return [simulate_from_dict(config, num_sims, parallel) for config in configs]


def generate_builds_rust(hunter_class, level: int, count: int) -> list[tuple[Dict[str, int], Dict[str, int]]]:
    """
    Generate builds using Rust - MUCH faster than Python!
    
    Args:
        hunter_class: Hunter class object (Borge, Knox, Ozzy) with costs and dependencies
        level: Hunter level
        count: Number of builds to generate
        
    Returns:
        List of (talents_dict, attributes_dict) tuples
    """
    global _native_lib, _using_native
    
    if not (_using_native and _native_lib is not None):
        # Fallback: would need Python BuildGenerator
        raise RuntimeError("Rust native bindings not available")
    
    # Extract hunter data
    talents = hunter_class.costs["talents"]
    attributes = hunter_class.costs["attributes"]
    
    # Get dependencies and gates (with safe defaults)
    attr_deps = getattr(hunter_class, 'attribute_dependencies', {})
    attr_gates = getattr(hunter_class, 'attribute_point_gates', {})
    attr_exclusions = getattr(hunter_class, 'attribute_exclusions', [])
    
    # Call Rust
    builds = _native_lib.generate_builds(
        level,
        talents,
        attributes,
        attr_deps,
        attr_gates,
        attr_exclusions,
        count
    )
    
    return builds


# Quick test
if __name__ == "__main__":
    # Test with a sample config file
    try:
        exe = get_rust_executable()
        print(f"Found Rust executable: {exe}")
        
        # Run a quick test
        script_dir = Path(__file__).parent
        test_config = script_dir / "builds" / "sanity-checks" / "sanity_chk.yaml"
        
        if test_config.exists():
            print(f"\nRunning test simulation with {test_config}...")
            result = simulate_from_file(str(test_config), num_sims=100, parallel=True)
            
            print("\nResults:")
            print(f"  Average Stage: {result.get('avg_stage', 0):.2f} ± {result.get('std_stage', 0):.2f}")
            print(f"  Stage Range: {result.get('min_stage', 0)} - {result.get('max_stage', 0)}")
            if 'simulations' in result and 'elapsed_seconds' in result:
                print(f"  Simulations/sec: {result['simulations'] / result['elapsed_seconds']:.0f}")
        else:
            print(f"Test config not found: {test_config}")
            
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("\nTo build the Rust simulator:")
        print("  cd hunter-sim-rs")
        print("  cargo build --release")
