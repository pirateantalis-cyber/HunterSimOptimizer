@echo off
REM Profile-Guided Optimization workflow for Hunter Sim

echo === Hunter Sim PGO Workflow ===

REM Change to the Rust project directory
cd hunter-sim-rs

REM Step 1: Build with instrumentation
echo Step 1: Building with instrumentation...
cargo build --profile=release-pgo
if %errorlevel% neq 0 (
    echo Build failed!
    exit /b 1
)

REM Step 2: Run representative workloads to collect profile data
echo Step 2: Running representative workloads...

python ..\pgo_workload.py
if %errorlevel% neq 0 (
    echo PGO workload failed!
    exit /b 1
)

REM Step 3: Rebuild with profile data
echo Step 3: Rebuilding with profile-guided optimizations...
cargo build --profile=release-pgo
if %errorlevel% neq 0 (
    echo PGO rebuild failed!
    exit /b 1
)

echo PGO optimization complete! The optimized binary is ready.
echo You can now use the PGO-optimized version for maximum performance.
pause