#!/bin/bash
# Profile-Guided Optimization workflow for Hunter Sim

echo "=== Hunter Sim PGO Workflow ==="

# Change to the Rust project directory
cd hunter-sim-rs

# Step 1: Build with instrumentation
echo "Step 1: Building with instrumentation..."
cargo build --profile=release-pgo

# Step 2: Run representative workloads to collect profile data
echo "Step 2: Running representative workloads..."

python ../pgo_workload.py
cargo build --profile=release-pgo

echo "PGO optimization complete! The optimized binary is ready."
echo "You can now use the PGO-optimized version for maximum performance."