# Hunter-Sim Multi-Hunter Optimizer

A high-performance build optimizer for the Interstellar Hunt content from CIFI (Cell Idle Factory Incremental). This fork features a complete GUI rewrite with a Rust simulation backend for blazing-fast optimization.

## ✨ Features

### Multi-Hunter GUI
- **Tabbed interface** for Borge, Knox, and Ozzy optimization
- **Real-time progress** with animated battle arena visualization
- **"Optimize All"** button to run all hunters simultaneously
- **IRL Build comparison** - compare optimized builds against your current in-game build

### High-Performance Rust Backend
- **22-core parallelization** using native Rust via PyO3
- **100+ simulations per second** per build
- **Progressive evolution algorithm** - builds on the best performers from each tier

### Build Management
- **Save/Load builds** - your IRL builds persist between sessions
- **Export to YAML** - share builds with the community
- **One-click apply** - copy optimized builds to your IRL build slots

### Supported Hunters
- 🟩 **Borge**: All talents and attributes, up to stage 200+
- 🟩 **Ozzy**: All talents and attributes, up to stage 200+
- 🟩 **Knox**: All talents and attributes, up to stage 200+

## 🚀 Quick Start

### Option 1: Run the GUI (Recommended)

```powershell
# Clone the repository
git clone https://github.com/pirateantalis-cyber/hunter-sim.git
cd hunter-sim

# Install dependencies
pip install -r requirements.txt

# Run the multi-hunter optimizer
python hunter-sim/gui_multi.py
```

Or double-click `run_gui.bat` on Windows.

### Option 2: Command Line

```powershell
python ./hunter-sim/hunter_sim.py -f ./builds/empty_borge.yaml -i 100
```

## 🔧 Building the Rust Backend (Optional)

The Rust backend provides ~10x speedup over pure Python. Pre-built binaries are included, but you can rebuild:

```powershell
cd hunter-sim-rs
cargo build --release
```

## 📖 Usage Guide

### Using the GUI

1. **Select a Hunter Tab** (Borge, Knox, or Ozzy)
2. **Enter your current level** in the Level field
3. **Input your IRL build** - your current in-game talents/attributes
4. **Click "Start Optimization"** to find optimal builds
5. **Review results** - sorted by average stage reached
6. **Apply the best build** with "Apply to IRL Build" button

### Optimization Settings

| Setting | Description |
|---------|-------------|
| Level | Your hunter's current level |
| Sims/Build | Number of simulations per build (higher = more accurate) |
| Builds/Tier | Builds to test per optimization tier |
| Use Rust | Enable high-performance Rust backend |
| Progressive Evo | Use tiered optimization (recommended) |

### IRL Max Stage

Set this to your actual best stage in-game. The optimizer will show a comparison:
- ✅ **Green** builds beat your current IRL stage
- ⚠️ **Yellow** builds match your IRL stage  
- ❌ **Red** builds are worse than your current build

## 📁 Project Structure

```
hunter-sim/
├── hunter-sim/
│   ├── gui_multi.py    # Multi-hunter GUI optimizer
│   ├── gui.py          # Single hunter GUI (legacy)
│   ├── hunters.py      # Hunter class definitions
│   ├── sim.py          # Simulation engine
│   └── IRL Builds/     # Your saved builds (persisted)
├── hunter-sim-rs/      # Rust simulation backend
│   └── src/
├── builds/             # Build config templates
├── rust_sim.py         # Python-Rust bridge
└── run_gui.bat         # Windows launcher
```

## 🤝 Contributing

Contributions welcome! The main areas for improvement:

- Additional hunter mechanics
- Inscryption/mod support in optimizer
- UI improvements
- Cross-platform testing

## 📝 Credits

- Original simulation: [bhnn/hunter-sim](https://github.com/bhnn/hunter-sim)
- Better Simulation: https://hunter-sim2.netlify.app/home
- Rust backend & GUI: pirateantalis-cyber
- CIFI game: [Play Store](https://play.google.com/store/apps/details?id=com.weihnachtsmann.idlefactoryinc)

## 📄 License

MIT License - See LICENSE file for details.
