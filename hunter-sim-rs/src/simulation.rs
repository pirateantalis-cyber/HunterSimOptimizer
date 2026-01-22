//! Core simulation engine

use crate::config::BuildConfig;
use crate::enemy::Enemy;
use crate::hunter::Hunter;
use crate::stats::{AggregatedStats, SimResult};
use rand::rngs::SmallRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;
use rayon::ThreadPoolBuilder;
use std::collections::BinaryHeap;
use std::cmp::Ordering;

/// Event in the simulation queue
#[derive(Debug, Clone)]
struct Event {
    time: f64,
    priority: i32,  // Lower = higher priority
    action: Action,
}

impl PartialEq for Event {
    fn eq(&self, other: &Self) -> bool {
        self.time == other.time && self.priority == other.priority
    }
}

impl Eq for Event {}

impl PartialOrd for Event {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Event {
    fn cmp(&self, other: &Self) -> Ordering {
        // Reverse ordering for min-heap behavior
        other.time.partial_cmp(&self.time)
            .unwrap_or(Ordering::Equal)
            .then(other.priority.cmp(&self.priority))
    }
}

#[derive(Debug, Clone, Copy)]
enum Action {
    HunterAttack,
    EnemyAttack,
    EnemySpecial,
    Regen,
}

/// Run a single simulation
pub fn run_simulation(config: &BuildConfig) -> SimResult {
    let mut rng = SmallRng::from_entropy();
    run_simulation_with_rng(config, &mut rng)
}

/// Run a simulation with a specific RNG (for deterministic testing)
pub fn run_simulation_with_rng(config: &BuildConfig, rng: &mut impl Rng) -> SimResult {
    let mut hunter = Hunter::from_config(config);
    let mut elapsed_time: f64 = 0.0;
    let mut total_loot: f64 = 0.0;
    
    let mut queue: BinaryHeap<Event> = BinaryHeap::new();
    
    // Main simulation loop - progress through stages
    'stages: loop {
        let stage = hunter.current_stage;
        
        // Spawn enemies for this stage
        let enemies = if stage % 100 == 0 && stage > 0 {
            // Boss stage
            vec![Enemy::new_boss(stage, hunter.hunter_type)]
        } else {
            // Regular stage - 10 enemies
            (1..=10).map(|i| Enemy::new(i, stage, hunter.hunter_type)).collect()
        };
        
        // Fight each enemy in the stage
        for mut enemy in enemies {
            queue.clear();
            
            // Queue initial events
            queue.push(Event { time: elapsed_time + hunter.speed, priority: 1, action: Action::HunterAttack });
            queue.push(Event { time: elapsed_time + enemy.speed, priority: 2, action: Action::EnemyAttack });
            queue.push(Event { time: elapsed_time + 1.0, priority: 3, action: Action::Regen });
            
            if enemy.has_secondary {
                queue.push(Event { time: elapsed_time + enemy.speed2, priority: 2, action: Action::EnemySpecial });
            }
            
            // Apply on-spawn effects
            apply_spawn_effects(&mut hunter, &mut enemy, rng);
            
            // Combat loop
            while !enemy.is_dead() && !hunter.is_dead() {
                let event = match queue.pop() {
                    Some(e) => e,
                    None => break,
                };
                
                elapsed_time = event.time;
                
                match event.action {
                    Action::HunterAttack => {
                        hunter_attack(&mut hunter, &mut enemy, rng);
                        queue.push(Event { 
                            time: elapsed_time + hunter.speed, 
                            priority: 1, 
                            action: Action::HunterAttack 
                        });
                    }
                    Action::EnemyAttack => {
                        if !enemy.is_stunned || elapsed_time >= enemy.stun_end_time {
                            enemy.is_stunned = false;
                            enemy_attack(&mut hunter, &mut enemy, rng);
                            if !enemy.is_dead() {
                                queue.push(Event { 
                                    time: elapsed_time + enemy.speed, 
                                    priority: 2, 
                                    action: Action::EnemyAttack 
                                });
                            }
                        }
                    }
                    Action::EnemySpecial => {
                        if enemy.is_boss && !enemy.is_stunned {
                            enemy.add_enrage();
                            queue.push(Event { 
                                time: elapsed_time + enemy.speed2, 
                                priority: 2, 
                                action: Action::EnemySpecial 
                            });
                        }
                    }
                    Action::Regen => {
                        hunter.regen_hp();
                        enemy.regen_hp();
                        queue.push(Event { 
                            time: elapsed_time + 1.0, 
                            priority: 3, 
                            action: Action::Regen 
                        });
                    }
                }
            }
            
            // Check if hunter died
            if hunter.is_dead() {
                if hunter.try_revive() {
                    // Revived, continue fighting
                    continue;
                } else {
                    // Dead for real, end simulation
                    break 'stages;
                }
            }
            
            // Enemy killed
            on_kill(&mut hunter, rng);
            hunter.result.kills += 1;
        }
        
        // Stage complete - calculate per-resource loot
        on_stage_complete(&mut hunter, rng);
        let (mat1, mat2, mat3, xp) = hunter.calculate_loot();
        hunter.result.loot_common += mat1;
        hunter.result.loot_uncommon += mat2;
        hunter.result.loot_rare += mat3;
        hunter.result.total_xp += xp;
        total_loot += mat1 + mat2 + mat3;
        hunter.current_stage += 1;
        
        // Safety check - don't run forever
        if hunter.current_stage > 1000 {
            break;
        }
    }
    
    // Finalize results
    hunter.result.final_stage = hunter.current_stage;
    hunter.result.elapsed_time = elapsed_time;
    hunter.result.total_loot = total_loot;
    
    hunter.result
}

/// Apply effects when an enemy spawns
fn apply_spawn_effects(hunter: &mut Hunter, enemy: &mut Enemy, _rng: &mut impl Rng) {
    // Presence of God - instant damage on spawn
    if hunter.presence_of_god > 0 {
        let pog_damage = hunter.power * 0.1 * hunter.presence_of_god as f64;
        enemy.take_damage(pog_damage);
        hunter.result.damage += pog_damage;
    }
    
    // Omen of Defeat - reduce enemy stats
    if hunter.omen_of_defeat > 0 {
        let reduction = 1.0 - (0.02 * hunter.omen_of_defeat as f64);
        enemy.power *= reduction;
        enemy.hp *= reduction;
        enemy.max_hp *= reduction;
    }
    
    // Soul of Snek (Ozzy) - reduce enemy regen by 8.8% per level
    if hunter.soul_of_snek > 0 {
        let regen_reduction = 1.0 - (0.088 * hunter.soul_of_snek as f64);
        enemy.regen *= regen_reduction.max(0.0);
    }
    
    // Gift of Medusa (Ozzy) - 5% of hunter max HP as enemy -regen per level
    if hunter.gift_of_medusa > 0 {
        let anti_regen = hunter.max_hp * 0.05 * hunter.gift_of_medusa as f64;
        enemy.regen = (enemy.regen - anti_regen).max(0.0);
    }
}

/// Handle on-kill effects for hunter
fn on_kill(hunter: &mut Hunter, rng: &mut impl Rng) {
    // Trickster's Boon (Ozzy) - 50% of effect chance to gain a trickster charge
    if hunter.tricksters_boon > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
        hunter.trickster_charges += 1;
        hunter.result.effect_procs += 1;
    }
    
    // Unfair Advantage (Ozzy/shared) - effect chance to heal 2% max HP per level
    if hunter.unfair_advantage > 0 && rng.gen::<f64>() < hunter.effect_chance {
        let heal_amount = hunter.max_hp * 0.02 * hunter.unfair_advantage as f64;
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.unfair_advantage_healing += heal_amount;
        hunter.result.effect_procs += 1;
        
        // Vectid Elixir (Ozzy) - empowered regen for 5 ticks after Unfair Advantage
        if hunter.vectid_elixir > 0 {
            hunter.empowered_regen += 5;
        }
    }
    
    // Life of the Hunt (Borge/shared) - effect chance to heal 1% max HP per level
    if hunter.life_of_the_hunt > 0 && rng.gen::<f64>() < hunter.effect_chance {
        let heal_amount = hunter.max_hp * 0.01 * hunter.life_of_the_hunt as f64;
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.life_of_the_hunt_healing += heal_amount;
        hunter.result.effect_procs += 1;
    }
}

/// Handle on-stage-complete effects for hunter
fn on_stage_complete(hunter: &mut Hunter, rng: &mut impl Rng) {
    // Calypso's Advantage (Knox) - chance to gain Hundred Souls stack on stage clear
    if hunter.calypsos_advantage > 0 && rng.gen::<f64>() < hunter.effect_chance * 2.5 {
        // Max stacks = 100 base + dead_men_tell_no_tales * 10
        let max_stacks = 100 + hunter.soul_amplification * 10;
        if hunter.hundred_souls_stacks < max_stacks {
            hunter.hundred_souls_stacks += 1;
            hunter.result.effect_procs += 1;
        }
    }
}

/// Knox salvo attack - fires multiple projectiles per attack
fn knox_salvo_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng, effective_power: f64) {
    // Calculate number of projectiles in this salvo
    let mut num_projectiles = hunter.salvo_projectiles;
    
    // Space Pirate Armory - 2% chance per level to add +3 rounds to salvo
    if hunter.space_pirate_armory > 0 {
        if rng.gen::<f64>() < hunter.space_pirate_armory as f64 * 0.02 {
            num_projectiles += 3;
            hunter.result.effect_procs += 1;
        }
    }
    
    // Ghost Bullets - 6.67% chance per level for extra projectile
    if hunter.ghost_bullets > 0 {
        let ghost_chance = hunter.ghost_bullets as f64 * 0.0667;
        if rng.gen::<f64>() < ghost_chance {
            num_projectiles += 1;
            hunter.result.multistrikes += 1;  // Track ghost bullets via multistrikes
        }
    }
    
    let mut total_damage = 0.0;
    let base_projectiles = hunter.salvo_projectiles as f64;
    
    for i in 0..num_projectiles {
        // Each projectile deals a portion of total power
        let mut bullet_damage = effective_power / base_projectiles;
        
        // Check for charge (Knox's crit equivalent)
        if rng.gen::<f64>() < hunter.charge_chance {
            bullet_damage *= 1.0 + hunter.charge_gained;
            hunter.result.crits += 1;
        }
        
        // Finishing Move on last bullet - chance for bonus damage
        if i == num_projectiles - 1 && hunter.finishing_move > 0 {
            if rng.gen::<f64>() < hunter.effect_chance * 2.0 {
                bullet_damage *= hunter.special_damage;  // special_damage = 1.0 + 0.2 * finishing_move
                hunter.result.effect_procs += 1;
            }
        }
        
        total_damage += bullet_damage;
    }
    
    // Apply damage
    let actual_damage = enemy.take_damage(total_damage);
    hunter.result.damage += actual_damage;
    
    // Lifesteal
    if hunter.lifesteal > 0.0 {
        let healed = actual_damage * hunter.lifesteal;
        hunter.hp = (hunter.hp + healed).min(hunter.max_hp);
        hunter.result.lifesteal += healed;
    }
    
    // Effect proc (stun)
    if rng.gen::<f64>() < hunter.effect_chance {
        hunter.result.effect_procs += 1;
        let stun_duration = 1.0 + 0.2 * hunter.effect_chance;
        let actual_stun = if enemy.is_boss { stun_duration * 0.5 } else { stun_duration };
        enemy.is_stunned = true;
        enemy.stun_end_time = hunter.result.elapsed_time + actual_stun;
        hunter.result.stun_duration_inflicted += actual_stun;
    }
}

/// Hunter attacks enemy
fn hunter_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng) {
    hunter.result.attacks += 1;
    
    // Calculate effective power (base + deal_with_death per revive used)
    let mut effective_power = hunter.power;
    if hunter.deal_with_death > 0 && hunter.revive_count > 0 {
        effective_power *= 1.0 + (hunter.deal_with_death as f64 * 0.02 * hunter.revive_count as f64);
    }
    
    // Born for Battle (Borge) - +0.1% power per 1% missing HP
    if hunter.born_for_battle > 0 {
        let missing_hp_pct = 1.0 - (hunter.hp / hunter.max_hp);
        effective_power *= 1.0 + (missing_hp_pct * hunter.born_for_battle as f64 * 0.001);
    }
    
    // Hundred Souls power bonus (Knox) - +0.5% per stack, boosted by soul_amplification
    if hunter.hundred_souls_stacks > 0 {
        let souls_multiplier = 0.005 * (1.0 + hunter.soul_amplification as f64 * 0.01);
        effective_power *= 1.0 + (hunter.hundred_souls_stacks as f64 * souls_multiplier);
    }
    
    // Calculate effective crit chance (base + cycle_of_death per revive used)
    let mut effective_crit_chance = hunter.special_chance;
    let mut effective_crit_dmg = hunter.special_damage;
    if hunter.cycle_of_death > 0 && hunter.revive_count > 0 {
        effective_crit_chance += hunter.cycle_of_death as f64 * 0.023 * hunter.revive_count as f64;
        effective_crit_dmg += hunter.cycle_of_death as f64 * 0.02 * hunter.revive_count as f64;
    }
    
    // Knox salvo attack mechanics
    if hunter.salvo_projectiles > 0 {
        knox_salvo_attack(hunter, enemy, rng, effective_power);
        return;
    }
    
    // Check for crit
    let base_damage = if rng.gen::<f64>() < effective_crit_chance {
        hunter.result.crits += 1;
        let crit_dmg = effective_power * effective_crit_dmg;
        hunter.result.extra_damage_from_crits += crit_dmg - effective_power;
        crit_dmg
    } else {
        effective_power
    };
    
    // Apply decay stacks bonus (Ozzy Crippling Shots) - consume stacks
    let decay_bonus = if hunter.decay_stacks > 0 {
        let bonus = base_damage * 0.03 * hunter.decay_stacks as f64;
        hunter.decay_stacks = 0;  // Consume stacks
        bonus
    } else {
        0.0
    };
    
    // Omen of Decay (Ozzy) - % of enemy current HP as bonus damage
    let omen_decay_damage = if hunter.omen_of_decay > 0 {
        let decay_pct = hunter.omen_of_decay as f64 * 0.008;  // 0.8% per level
        let bonus_dmg = enemy.hp * decay_pct;
        // 90% reduced on bosses
        if enemy.is_boss {
            bonus_dmg * 0.1
        } else {
            bonus_dmg
        }
    } else {
        0.0
    };
    
    // Check for multistrike (Ozzy)
    let multistrike_bonus = if hunter.multistriker > 0 && rng.gen::<f64>() < 0.1 + 0.05 * hunter.multistriker as f64 {
        hunter.result.multistrikes += 1;
        let ms_base = base_damage * 0.5;
        // Multistrike also benefits from omen of decay
        let ms_omen = if hunter.omen_of_decay > 0 {
            let decay_pct = hunter.omen_of_decay as f64 * 0.008;
            let bonus = enemy.hp * decay_pct;
            if enemy.is_boss { bonus * 0.1 } else { bonus }
        } else {
            0.0
        };
        let ms_total = ms_base + ms_omen;
        hunter.result.extra_damage_from_ms += ms_total;
        ms_total
    } else {
        0.0
    };
    
    let total_damage = base_damage + decay_bonus + omen_decay_damage + multistrike_bonus;
    
    // Apply damage
    let actual_damage = enemy.take_damage(total_damage);
    hunter.result.damage += actual_damage;
    
    // Echo Bullets (Ozzy) - chance for extra shot
    if hunter.echo_bullets > 0 && rng.gen::<f64>() < hunter.effect_chance {
        hunter.result.echo_bullets += 1;
        let echo_dmg = hunter.power * hunter.echo_bullets as f64 * 0.05;  // 0.05x per level
        let echo_decay = if hunter.omen_of_decay > 0 {
            let decay_pct = hunter.omen_of_decay as f64 * 0.008;
            let bonus = enemy.hp * decay_pct;
            if enemy.is_boss { bonus * 0.1 } else { bonus }
        } else {
            0.0
        };
        let echo_total = echo_dmg + echo_decay;
        let echo_actual = enemy.take_damage(echo_total);
        hunter.result.damage += echo_actual;
        
        // Echo can trigger its own multistrike
        if hunter.multistriker > 0 && rng.gen::<f64>() < 0.1 + 0.05 * hunter.multistriker as f64 {
            hunter.result.multistrikes += 1;
            let echo_ms = echo_dmg * 0.5;
            let echo_ms_omen = if hunter.omen_of_decay > 0 {
                let decay_pct = hunter.omen_of_decay as f64 * 0.008;
                let bonus = enemy.hp * decay_pct;
                if enemy.is_boss { bonus * 0.1 } else { bonus }
            } else {
                0.0
            };
            let echo_ms_total = echo_ms + echo_ms_omen;
            let echo_ms_actual = enemy.take_damage(echo_ms_total);
            hunter.result.damage += echo_ms_actual;
            hunter.result.extra_damage_from_ms += echo_ms_actual;
        }
    }
    
    // Crippling Shots (Ozzy) - add decay stacks for NEXT attack
    if hunter.crippling_shots > 0 && rng.gen::<f64>() < hunter.effect_chance {
        hunter.decay_stacks += hunter.crippling_shots;
        hunter.decay_stacks = hunter.decay_stacks.min(100);  // Cap at 100 stacks
    }
    
    // Lifesteal
    if hunter.lifesteal > 0.0 {
        let healed = actual_damage * hunter.lifesteal;
        hunter.hp = (hunter.hp + healed).min(hunter.max_hp);
        hunter.result.lifesteal += healed;
    }
    
    // Effect proc (stun)
    if rng.gen::<f64>() < hunter.effect_chance {
        hunter.result.effect_procs += 1;
        // Thousand Needles (Ozzy) adds stun duration
        let base_stun = if hunter.thousand_needles > 0 {
            hunter.thousand_needles as f64 * 0.05  // 0.05s per level
        } else {
            1.0 + 0.2 * hunter.effect_chance
        };
        let stun_duration = base_stun;
        // 50% reduced on bosses
        let actual_stun = if enemy.is_boss {
            stun_duration * 0.5
        } else {
            stun_duration
        };
        enemy.is_stunned = true;
        enemy.stun_end_time = hunter.result.elapsed_time + actual_stun;
        hunter.result.stun_duration_inflicted += actual_stun;
    }
}

/// Enemy attacks hunter
fn enemy_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng) {
    // Check for trickster evade (Ozzy) - consume a charge for free evade
    if hunter.trickster_charges > 0 {
        hunter.trickster_charges -= 1;
        hunter.result.trickster_evades += 1;
        return;
    }
    
    // Check for evade
    if rng.gen::<f64>() < hunter.evade_chance {
        hunter.result.evades += 1;
        
        // Dance of Dashes (Ozzy) - 15% chance per level to gain trickster charge on evade
        if hunter.dance_of_dashes > 0 && rng.gen::<f64>() < hunter.dance_of_dashes as f64 * 0.15 {
            hunter.trickster_charges += 1;
            hunter.result.effect_procs += 1;
        }
        return;
    }
    
    // Check for block (Knox)
    if hunter.block_chance > 0.0 && rng.gen::<f64>() < hunter.block_chance {
        // Blocked - reduced damage (50% of original)
        hunter.result.evades += 1;  // Track blocks via evades counter
        
        // Fortification Elixir (Knox) - +10% regen for 5 ticks after block
        if hunter.fortification_elixir > 0 {
            hunter.empowered_block_regen += 5;
        }
        return;
    }
    
    // Get enemy damage
    let (mut damage, is_crit) = enemy.get_attack_damage(rng);
    
    // Weakspot Analysis (Borge) - reduce crit damage taken by 11% per level
    if is_crit && hunter.weakspot_analysis > 0 {
        let crit_reduction = hunter.weakspot_analysis as f64 * 0.11;
        damage *= 1.0 - crit_reduction.min(0.99);  // Cap at 99% reduction
    }
    
    // Calculate effective DR (base + deal_with_death per revive used)
    let mut effective_dr = hunter.damage_reduction;
    if hunter.deal_with_death > 0 && hunter.revive_count > 0 {
        effective_dr += hunter.deal_with_death as f64 * 0.016 * hunter.revive_count as f64;
    }
    
    // Apply damage reduction
    let mitigated = damage * effective_dr.min(0.95);  // Cap DR at 95%
    let actual_damage = damage - mitigated;
    
    hunter.result.mitigated_damage += mitigated;
    hunter.result.damage_taken += actual_damage;
    hunter.hp -= actual_damage;
    
    // Helltouch barrier (Borge)
    if hunter.helltouch_barrier_level > 0 && hunter.hp < hunter.max_hp * 0.3 {
        let barrier = hunter.max_hp * 0.01 * hunter.helltouch_barrier_level as f64;
        hunter.hp += barrier;
        hunter.result.helltouch_barrier += barrier;
    }
}

/// Run multiple simulations in parallel with proper thread utilization
pub fn run_simulations_parallel(config: &BuildConfig, count: usize) -> Vec<SimResult> {
    // Use 70% of available cores to keep system responsive
    let num_cores = num_cpus::get();
    let threads_per_hunter = ((num_cores as f64 * 0.70).round() as usize)
        .max(2)
        .min(num_cores.saturating_sub(1).max(1));
    
    let pool = ThreadPoolBuilder::new()
        .num_threads(threads_per_hunter)
        .build()
        .unwrap_or_else(|_| rayon::ThreadPoolBuilder::new().build().unwrap());
    
    pool.install(|| {
        let chunk_size = (count / threads_per_hunter).max(1);
        
        (0..count)
            .into_par_iter()
            .with_min_len(chunk_size.min(100))
            .map(|_| run_simulation(config))
            .collect()
    })
}

/// Run multiple simulations sequentially (lower memory)
pub fn run_simulations_sequential(config: &BuildConfig, count: usize) -> Vec<SimResult> {
    let mut rng = SmallRng::from_entropy();
    (0..count)
        .map(|_| run_simulation_with_rng(config, &mut rng))
        .collect()
}

/// Run simulations and return aggregated stats
pub fn run_and_aggregate(config: &BuildConfig, count: usize, parallel: bool) -> AggregatedStats {
    let results = if parallel {
        run_simulations_parallel(config, count)
    } else {
        run_simulations_sequential(config, count)
    };
    
    AggregatedStats::from_results(&results)
}
