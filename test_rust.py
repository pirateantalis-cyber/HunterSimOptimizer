import rust_sim

print("Testing Rust native bindings...")

result = rust_sim.simulate(
    hunter='Borge',
    level=69,
    stats={'hp': 100, 'power': 100, 'regen': 50, 'damage_reduction': 25, 'evade_chance': 15, 'effect_chance': 20, 'special_chance': 50, 'special_damage': 50, 'speed': 30},
    talents={'death_is_my_companion': 2, 'impeccable_impacts': 10, 'presence_of_god': 15, 'fires_of_war': 15, 'life_of_the_hunt': 5, 'unfair_advantage': 5, 'omen_of_defeat': 10, 'call_me_lucky_loot': 7},
    attributes={'soul_of_ares': 50, 'essence_of_ylith': 30, 'spartan_lineage': 6, 'helltouch_barrier': 10, 'book_of_baal': 6, 'explosive_punches': 6, 'superior_sensors': 6, 'lifedrain_inhalers': 10, 'atlas_protocol': 6, 'weakspot_analysis': 6, 'born_for_battle': 1, 'timeless_mastery': 5, 'soul_of_athena': 1, 'soul_of_hermes': 1, 'soul_of_the_minotaur': 1},
    num_sims=100,
    parallel=True
)

print(f"✅ Rust simulation successful!")
print(f"   Average Stage: {result['avg_stage']:.1f}")
print(f"   Max Stage: {result['max_stage']}")
print(f"   Min Stage: {result['min_stage']}")
print(f"   Average Damage: {result['avg_damage']:.0f}")
print(f"   Average Kills: {result['avg_kills']:.0f}")
