import rust_sim
import yaml

with open('builds/sanity-checks/sanity_ut_ozzy.yaml', 'r') as f:
    config = yaml.safe_load(f)

# Print key talents and attributes we just added
print('Key mechanics check:')
print(f"  tricksters_boon: {config.get('talents', {}).get('tricksters_boon', 0)}")
print(f"  unfair_advantage: {config.get('talents', {}).get('unfair_advantage', 0)}")
print(f"  dance_of_dashes: {config.get('attributes', {}).get('dance_of_dashes', 0)}")
print(f"  vectid_elixir: {config.get('attributes', {}).get('vectid_elixir', 0)}")

# Run simulation using simulate_from_file
result = rust_sim.simulate_from_file('builds/sanity-checks/sanity_ut_ozzy.yaml', num_sims=100, parallel=True)
print(f'\nResults after adding Trickster/Unfair Advantage:')
print(f"  avg_stage: {result['avg_stage']:.1f}")
print(f"  min_stage: {result['min_stage']}")
print(f"  max_stage: {result['max_stage']}")
print(f"  std_stage: {result['std_stage']:.2f}")
