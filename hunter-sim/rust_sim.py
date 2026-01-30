#!/usr/bin/env python3
"""
Rust Simulator Python Wrapper
=============================
Provides a Python interface to the Rust Hunter Sim binary.
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Dict, Any, List

# Path to Rust executable
RUST_EXE = Path(__file__).parent.parent / "hunter-sim-rs" / "target" / "release" / "hunter-sim.exe"

def simulate(
    hunter: str,
    level: int,
    stats: Dict[str, Any] = None,
    talents: Dict[str, Any] = None,
    attributes: Dict[str, Any] = None,
    inscryptions: Dict[str, Any] = None,
    mods: Dict[str, Any] = None,
    relics: Dict[str, Any] = None,
    gems: Dict[str, Any] = None,
    gadgets: Dict[str, Any] = None,
    bonuses: Dict[str, Any] = None,
    num_sims: int = 50,
    parallel: bool = True
) -> Dict[str, Any]:
    """
    Run Rust simulation with the given configuration.

    Args:
        hunter: Hunter type ('Borge', 'Ozzy', 'Knox')
        level: Hunter level
        stats: Hunter stats dict
        talents: Talents dict
        attributes: Attributes dict
        inscryptions: Inscryptions dict
        mods: Mods dict
        relics: Relics dict
        gems: Gems dict
        gadgets: Gadgets dict
        bonuses: Bonuses dict
        num_sims: Number of simulations to run
        parallel: Whether to run in parallel

    Returns:
        Dict with simulation results
    """
    return simulate_batch([{
        "hunter": hunter,
        "level": level,
        "stats": stats or {},
        "talents": talents or {},
        "attributes": attributes or {},
        "inscryptions": inscryptions or {},
        "mods": mods or {},
        "relics": relics or {},
        "gems": gems or {},
        "gadgets": gadgets or {},
        "bonuses": bonuses or {}
    }], num_sims, parallel)[0]


def simulate_batch(
    configs: List[Dict[str, Any]],
    num_sims: int = 50,
    parallel: bool = True
) -> List[Dict[str, Any]]:
    """
    Run Rust simulations for multiple configs in batch.

    Args:
        configs: List of build configs
        num_sims: Number of simulations per config
        parallel: Whether to run in parallel

    Returns:
        List of dicts with simulation results
    """
    if not RUST_EXE.exists():
        raise FileNotFoundError(f"Rust executable not found at {RUST_EXE}")

    # Write configs to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(configs, f)
        temp_config = f.name

    try:
        # Build command
        cmd = [
            str(RUST_EXE),
            "--configs", temp_config,
            "--num-sims", str(num_sims),
            "--output", "json"
        ]
        if parallel:
            cmd.append("--parallel")

        # Run Rust executable
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(RUST_EXE.parent)
        )

        if result.returncode != 0:
            raise RuntimeError(f"Rust simulation failed: {result.stderr}")

        # Parse JSON output
        output = json.loads(result.stdout)
        return output["stats"]

    finally:
        # Clean up temp file
        try:
            os.unlink(temp_config)
        except:
            pass