# Release Notes - v2.0

## Major Changes

### 🐛 Bug Fixes
- **Revive timing bug** - Fixed critical issue where revive mechanics weren't triggered at the correct health thresholds in both Python and Rust engines
- **Boss fight edge cases** - Improved handling of multi-phase boss fights and special attacks
- **Attribute dependencies** - Fixed validation logic for talent/attribute unlock gates

### 🎨 UI/UX Improvements
- **Refreshed color palette:**
  - Borge: Rich crimson theme
  - Ozzy: Vibrant emerald theme
  - Knox: Clean cobalt blue theme
- **Better progress indicators** - More granular feedback during optimization
- **Smoother battle arena** - Improved animation frame rates and transitions
- **Cleaner result displays** - Better formatting for large numbers and percentages

### ⚡ Performance Enhancements
- **Rust backend rebuild** - Complete rewrite of core simulation loop
  - Now sustains 100+ simulations/sec (up from ~50/sec)
  - Better memory management
  - More efficient parallel iteration with Rayon
- **Multi-threading improvements** - Optimized worker process spawning and communication
- **Reduced memory footprint** - More efficient build representation and result caching

### 🔍 Accuracy & Validation
- **Python ↔ Rust parity** - Both engines now within 0.2% of each other on average
- **WASM validation** - All hunters within ~5% of hunter-sim2.netlify.app
- **Comprehensive test coverage** - Added comparison scripts for all three engines
- **Better RNG handling** - Improved consistency across simulation runs

### 📚 Documentation
- **New README** with accuracy validation section
- **Screenshots** - Auto-generated GUI and accuracy comparison images
- **Better attribution** to hunter-sim2 (community-trusted WASM site)
- **Detailed release notes** (this file!)

## Breaking Changes
None - this release is fully backward compatible with v1.x builds.

## Known Issues
- Windows Defender may flag the .exe as unknown (expected for unsigned executables)
- Very high levels (300+) may show slight variance due to floating-point precision limits
- Battle arena may stutter on low-end hardware with all 3 hunters optimizing simultaneously

## Migration Guide
If upgrading from v1.x:
1. Your IRL builds in `hunter-sim/IRL Builds/` will automatically migrate
2. No config changes needed
3. Optimization runs from v1.x are not compatible - rerun optimizations with v2.0

## What's Next?
- v2.1: Inscryption/mod support in optimizer
- v2.2: Cross-platform builds (Linux, macOS)
- v3.0: Multi-run campaign mode, leaderboard integration

---

Thanks to all contributors and the CIFI community for feedback and testing!
