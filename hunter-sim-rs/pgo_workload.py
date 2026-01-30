import rust_sim 
import json 
import time 
 
# Create representative build configs for profiling 
def create_test_config(hunter_name, level=100): 
    return { 
        'hunter': hunter_name, 
        'level': level, 
        'stats': {'power': 1000, 'speed': 100, 'max_hp': 10000}, 
        'talents': {'omen_of_defeat': 5, 'presence_of_god': 3}, 
        'attributes': {'atlas_protocol': 10, 'born_for_battle': 5}, 
        'inscryptions': {}, 
        'mods': {}, 
        'relics': {}, 
        'gems': {}, 
        'gadgets': {}, 
        'bonuses': {} 
    } 
 
# Run multiple simulations with different hunters 
hunters = ['Borge', 'Knox', 'Ozzy'] 
configs = [json.dumps(create_test_config(h)) for h in hunters] 
 
print("Running PGO profiling workloads...") 
start_time = time.time() 
 
# Run enough simulations to generate good profile data 
for i in range(10): 
    print(f"Batch {i+1}/10") 
    results = rust_sim.simulate_batch(configs, 1000, True) 
    # Process results to ensure they're used 
    for result in results: 
        if isinstance(result, str): 
            result = json.loads(result) 
        _ = result.get('avg_stage', 0) 
 
elapsed = time.time() - start_time 
print(f"PGO profiling completed in {elapsed:.1f} seconds") 
