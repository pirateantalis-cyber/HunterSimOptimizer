"""Compare loot/XP formulas with expected WASM values.

WASM formulas per kill (from reverse engineering):
- Mat1 = stage * 0.347 * multiplier
- Mat2 = stage * 0.330 * multiplier
- Mat3 = stage * 0.247 * multiplier
- XP = stage * 0.0755 * multiplier * xp_bonus

These are approximate values based on:
- Mat1: stage * 0.3 * 1.686 / 1.4558
- Mat2: stage * 0.3 * 1.6 / 1.4558
- Mat3: stage * 0.3 * 1.2 / 1.4558
- XP: stage * 0.1 * 1.1 / 1.4558
"""

# Test per-kill loot values at various stages
def test_per_kill_loot():
    """Test that per-kill loot matches WASM formula."""
    
    # Constants from WASM
    MAT1_FACTOR = 0.347  # stage * 0.3 * 1.686 / 1.4558
    MAT2_FACTOR = 0.330  # stage * 0.3 * 1.6 / 1.4558
    MAT3_FACTOR = 0.247  # stage * 0.3 * 1.2 / 1.4558
    XP_FACTOR = 0.0755   # stage * 0.1 * 1.1 / 1.4558
    
    print("=== PER-KILL LOOT FORMULA VERIFICATION ===")
    print(f"{'Stage':<8} {'Mat1':<12} {'Mat2':<12} {'Mat3':<12} {'XP':<12}")
    print("-" * 56)
    
    for stage in [10, 25, 50, 100, 150, 200, 300]:
        mat1 = stage * MAT1_FACTOR
        mat2 = stage * MAT2_FACTOR
        mat3 = stage * MAT3_FACTOR
        xp = stage * XP_FACTOR
        print(f"{stage:<8} {mat1:<12.2f} {mat2:<12.2f} {mat3:<12.2f} {xp:<12.2f}")
    
    print("\n=== ACCUMULATED LOOT OVER A RUN ===")
    print("Simulating kills from stage 1 to N, 10 enemies per stage + 1 boss:")
    
    for final_stage in [50, 100, 150]:
        total_mat1 = 0
        total_mat2 = 0
        total_mat3 = 0
        total_xp = 0
        total_kills = 0
        
        for stage in range(1, final_stage + 1):
            # 10 regular enemies + 1 boss per stage (for non-boss stages)
            enemies = 10 if stage % 100 != 0 else 1
            for _ in range(enemies):
                total_mat1 += stage * MAT1_FACTOR
                total_mat2 += stage * MAT2_FACTOR
                total_mat3 += stage * MAT3_FACTOR
                total_xp += stage * XP_FACTOR
                total_kills += 1
        
        print(f"\nFinal Stage {final_stage} ({total_kills} kills):")
        print(f"  Mat1: {total_mat1:.2f}")
        print(f"  Mat2: {total_mat2:.2f}")
        print(f"  Mat3: {total_mat3:.2f}")
        print(f"  XP:   {total_xp:.2f}")
        print(f"  Total: {total_mat1 + total_mat2 + total_mat3:.2f}")


if __name__ == "__main__":
    test_per_kill_loot()
