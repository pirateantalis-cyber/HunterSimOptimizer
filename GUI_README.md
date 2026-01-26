# Hunter Sim - Build Optimizer GUI v2.0

> Release highlights: revive timing bug fix, cleaner color palette for all hunters, rebuilt Rust backend, significantly faster multi-threaded sims, and polished Optimizer/Advisor flows.

A graphical interface for the Hunter Sim that **automatically finds optimal talent and attribute builds** by simulating thousands of combinations.

## Features

✨ **Automatic Build Optimization**: Instead of manually entering talents/attributes, the GUI explores ALL valid combinations and finds the best ones!

💾 **Save/Load Builds**: Save your current build configuration and load it later - no more re-typing everything!

🎯 **Upgrade Advisor**: Not sure what stat to upgrade next? The advisor simulates +1 to each stat and tells you which gives the BEST improvement!

⚔️ **Live Battle Arena**: Watch your hunter fight through stages in real-time! The arena syncs with optimization progress and shows:
- Hunter portrait on the left side
- Current enemy being fought on the right
- 10-enemy queue at the bottom (like the real game!)
- Stage progression based on best_avg_stage from optimization
- Hunter-themed backgrounds (Exon-12, Endo Prime, Sirene-6)

🦀 **Rust-Powered Engine**: Optional ultra-fast Rust simulation engine (1000x faster than Python!) - uses 70% of CPU cores for optimal performance without freezing your system.

🎯 **Supports All 3 Hunters**:
- **Borge** - Melee fighter with crits, helltouch barrier, and trample
- **Ozzy** - Ranged attacker with multistrikes, echo bullets, and trickster evades
- **Knox** - Projectile-based salvo attacker with block, charge, and ghost bullets

📊 **Multiple Optimization Goals**:
- 🏔️ **Highest Stage** - Maximize how far you can push
- 💰 **Best Loot/Hour** - Maximize resource farming efficiency  
- ⚡ **Fastest Clear** - Speed-running builds
- 💥 **Most Damage** - Maximum DPS builds
- 🛡️ **Best Survival** - Never die to bosses

🌙 **Dark Theme**: Easy on the eyes with a modern dark interface

🎮 **Easy Input**: Just enter your fixed game data:
- Main stat upgrade levels (HP, Power, Regen, etc.)
- Inscryption levels
- Relic levels
- Gem configurations

## Quick Start

### Option 1: Double-click the batch file
Just double-click `run_gui.bat` in the project folder!

### Option 2: Command line
```bash
cd hunter-sim
python gui.py
```

## How It Works

### Build Configuration Tab
1. **Select Your Hunter**: Choose Ozzy, Borge, or Knox
2. **Enter Your Stats**: Input your stat levels, inscryptions, relics, and gems
3. **Save Your Build**: Click "💾 Save Build" to save your configuration for later
4. **Load a Build**: Click "📂 Load Build" to restore a previously saved configuration

### Run Optimization Tab
1. **Set Simulation Parameters**: Number of sims, max builds, CPU processes
2. **Start Optimization**: Click "🚀 Start Optimization" and let it test builds
3. **View Results**: Results appear in the Results tab when complete

### Upgrade Advisor Tab
1. **Enter Current Talents/Attributes**: Input your current build
2. **Click "Analyze Best Upgrade"**: The advisor simulates adding +1 to each stat
3. **See Recommendations**: View which stat upgrade gives the best improvement!

### Results Tab
View the best builds ranked by different criteria and export them

### ⚔️ Battle Arena
A **live visualization** of your hunter fighting enemies during optimization:
- **Hunter portrait** on the left side, **enemy** on the right
- **10-enemy queue** at the bottom shows upcoming enemies in the current stage (just like the real game!)
- **Stage progression** syncs with optimization progress (based on best_avg_stage)
- Each hunter has a themed planet background:
  - **Borge**: Exon-12 (volcanic planet)
  - **Ozzy**: Endo Prime (tech planet)  
  - **Knox**: Sirene-6 (ocean planet)
- When optimization completes, shows "**DEFEATED 💀**" as the hunter falls in battle

## Understanding the Results

The optimizer tests builds and ranks them by:

| Metric | What It Means |
|--------|---------------|
| Avg Stage | Average stage reached across simulations |
| Loot/Hour | Resources gained per hour of play time |
| Survival % | How often you DON'T die to a boss |
| Avg Damage | Total damage dealt per run |
| Avg Time | How long each run takes (seconds) |

## Tips for Best Results

1. **Start with fewer simulations** (5-10) for quick testing
2. **Increase simulations** (50-100) for final optimization
3. **Use more CPU processes** if you have a powerful computer
4. **Limit max builds** if your level is high (more combinations exist)

## Understanding Optimization Percentage

The optimization percentage shown in results compares each build's simulated performance to your baseline build. Due to the randomness inherent in simulations, you may occasionally see results showing **over 100% optimization** (e.g., "105% of baseline").

**What does this mean?**
- Simulations involve RNG (random number generation) for crits, procs, enemy spawns, etc.
- With fewer simulations, results have higher variance
- A build showing 105% might actually be equal to or slightly worse than baseline

**How to fix it:**
- **Increase "Simulations per Build"** from the default to 50-100 or higher
- More simulations = more accurate averages = more reliable optimization percentages
- If you're seeing wild swings (e.g., builds jumping from 80% to 120%), your simulation count is too low

**Rule of thumb:** If your best builds consistently show >100% optimization compared to your IRL build, increase your simulation count until the baseline stabilizes.

## Technical Details

- Each level grants: **+1 Talent Point** and **+3 Attribute Points**
- The optimizer respects all talent/attribute maximum levels
- Results are sorted and the top 10-20 builds are shown per category
- You can export the best build to a YAML file for use with the original CLI tool

## Requirements

- Python 3.10+
- All dependencies from `requirements.txt`
- tkinter (usually included with Python)
