//! Core simulation engine - IDENTICAL to Python's sim.py

use crate::config::{BuildConfig, HunterType};
use crate::enemy::{Enemy, SecondaryAttackType};
use crate::hunter::Hunter;
use crate::stats::{AggregatedStats, SimResult};
use rayon::prelude::*;
use std::collections::BinaryHeap;
use std::cmp::Ordering;

/// Fast RNG wrapper for better performance
#[derive(Clone)]
pub struct FastRng {
    inner: fastrand::Rng,
}

impl FastRng {
    #[inline(always)]
    pub fn new(seed: u64) -> Self {
        Self {
            inner: fastrand::Rng::with_seed(seed),
        }
    }

    #[inline(always)]
    pub fn f64(&mut self) -> f64 {
        self.inner.f64()
    }

    #[inline(always)]
    pub fn u32(&mut self) -> u32 {
        self.inner.u32(..)
    }

    #[inline(always)]
    pub fn gen_range(&mut self, low: u32, high: u32) -> u32 {
        self.inner.u32(low..high)
    }
}

/// Event in the simulation queue
/// Python: (time, priority, action) tuple in heapq
#[derive(Debug, Clone)]
struct Event {
    time: f64,
    priority: i32,  // Lower = higher priority (Python uses 0, 1, 2, 3)
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
        // Reverse ordering for min-heap behavior (BinaryHeap is max-heap by default)
        // Python heapq is min-heap, sorts by (time, priority)
        other.time.partial_cmp(&self.time)
            .unwrap_or(Ordering::Equal)
            .then(other.priority.cmp(&self.priority))
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Action {
    Hunter,        // 'hunter' in Python
    Enemy,         // 'enemy' in Python  
    EnemySpecial,  // 'enemy_special' in Python
    Regen,         // 'regen' in Python
    Stun,          // 'stun' in Python
}

/// Run a single simulation - IDENTICAL to Python's Simulation.run()
pub fn run_simulation(config: &BuildConfig) -> SimResult {
    let mut rng = FastRng::new(rand::random::<u64>());
    run_simulation_with_rng(config, &mut rng)
}

/// Run a single simulation with a specific seed
pub fn run_simulation_with_seed(config: &BuildConfig, seed: u64) -> SimResult {
    let mut rng = FastRng::new(seed);
    run_simulation_with_rng(config, &mut rng)
}

/// Helper to round to 3 decimal places like Python's round(x, 3)
fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// Early termination check for obviously bad runs
#[inline(always)]
fn can_terminate(hunter: &Hunter, elapsed_time: f64) -> bool {
    // Terminate if dead
    if hunter.is_dead() {
        return true;
    }
    
    // Terminate if out of revives and current stage is too low for time remaining
    // Rough estimate: need at least 10 stages per minute of remaining time
    let time_remaining_hours = (3600.0 - elapsed_time) / 3600.0; // Convert to hours
    let estimated_max_stages = hunter.current_stage as f64 + time_remaining_hours * 600.0; // 600 stages/hour is very optimistic
    
    // If we can't reach stage 100 even with best case, terminate
    if estimated_max_stages < 100.0 && hunter.max_revives == 0 {
        return true;
    }
    
    false
}

/// Run a simulation with a specific RNG
/// This mirrors Python's Simulation.simulate_combat() EXACTLY
pub fn run_simulation_with_rng(config: &BuildConfig, rng: &mut FastRng) -> SimResult {
    let mut hunter = Hunter::from_config(config);
    
    // Python: self.elapsed_time: int = 0
    let mut elapsed_time: i32 = 0;
    
    // Python: self.queue = []
    let mut queue: BinaryHeap<Event> = BinaryHeap::new();
    
    // Python: self.current_stage = 0
    hunter.current_stage = 0;
    
    // Python: hpush(self.queue, (round(hunter.speed, 3), 1, 'hunter'))
    let initial_speed = hunter.get_speed();  // Consumes fires_of_war like Python
    queue.push(Event { 
        time: round3(initial_speed), 
        priority: 1, 
        action: Action::Hunter 
    });
    
    // Python: hpush(self.queue, (self.elapsed_time, 3, 'regen'))
    queue.push(Event { 
        time: elapsed_time as f64, 
        priority: 3, 
        action: Action::Regen 
    });
    
    // Debug flag
    let debug = std::env::var("DEBUG_SIM").is_ok();
    
    // Python: while not hunter.is_dead():
    'main_loop: while !can_terminate(&hunter, elapsed_time as f64) {
        let stage = hunter.current_stage;
        let is_boss = stage % 100 == 0 && stage > 0;
        
        if debug {
            eprintln!("\n=== STAGE {} ===", stage);
        }
        
        // Python: self.spawn_enemies(hunter)
        // Creates list of enemies: [Boss(...)] for boss stages, [Enemy(...) for i in range(10)] otherwise
        let mut enemies: Vec<Enemy> = if is_boss {
            vec![Enemy::new_boss(stage, hunter.hunter_type)]
        } else {
            (1..=10).map(|i| Enemy::new(i, stage, hunter.hunter_type)).collect()
        };
        
        // Apply on-spawn effects for each enemy (POG, OOD, etc.)
        for enemy in &mut enemies {
            apply_spawn_effects(&mut hunter, enemy, rng);
        }
        
        // Python: while self.enemies:
        let mut enemy_idx = 0;
        while enemy_idx < enemies.len() {
            // Skip if already dead (from trample)
            if enemies[enemy_idx].is_dead() {
                enemy_idx += 1;
                continue;
            }
            
            if debug && is_boss {
                eprintln!("Fighting enemy {} - HP: {:.0}", enemy_idx, enemies[enemy_idx].hp);
            }
            
            // Python: enemy = self.enemies.pop(0)
            // Python: enemy.queue_initial_attack()
            // This is: hpush(self.sim.queue, (round(self.sim.elapsed_time + self.speed, 3), 2, 'enemy'))
            queue.push(Event {
                time: round3(elapsed_time as f64 + enemies[enemy_idx].speed),
                priority: 2,
                action: Action::Enemy,
            });
            
            // If boss has secondary attack:
            // hpush(self.sim.queue, (round(self.sim.elapsed_time + self.speed2, 3), 2, 'enemy_special'))
            if enemies[enemy_idx].has_secondary {
                queue.push(Event {
                    time: round3(elapsed_time as f64 + enemies[enemy_idx].speed2),
                    priority: 2,
                    action: Action::EnemySpecial,
                });
            }
            
            // Python: while not enemy.is_dead() and not hunter.is_dead():
            // Store trample kills to apply after combat loop ends
            let mut pending_trample_kills = 0;
            
            while !enemies[enemy_idx].is_dead() && !hunter.is_dead() {
                // Python: prev_time, _, action = hpop(self.queue)
                let event = match queue.pop() {
                    Some(e) => e,
                    None => break,
                };
                let prev_time = event.time;
                
                if debug && is_boss {
                    eprintln!("  [{:.2}] {:?}", prev_time, event.action);
                }
                
                match event.action {
                    Action::Hunter => {
                        // Python: hunter.attack(enemy)
                        let trample_kills = hunter_attack(&mut hunter, &mut enemies[enemy_idx], rng, elapsed_time as f64);
                        pending_trample_kills = trample_kills;
                        
                        // Python: hpush(self.queue, (round(prev_time + hunter.speed, 3), 1, 'hunter'))
                        // NOTE: hunter.speed is a @property that applies FoW and consumes it!
                        let next_speed = hunter.get_speed();  // This consumes fires_of_war
                        queue.push(Event {
                            time: round3(prev_time + next_speed),
                            priority: 1,
                            action: Action::Hunter,
                        });
                        
                        // If stun was triggered, queue it at priority 0
                        // Python: hpush(self.sim.queue, (0, 0, 'stun'))
                        if hunter.pending_stun_duration > 0.0 {
                            queue.push(Event {
                                time: 0.0,
                                priority: 0,
                                action: Action::Stun,
                            });
                        }
                    }
                    
                    Action::Stun => {
                        // Python: hunter.apply_stun(enemy, isinstance(enemy, Boss))
                        // This finds 'enemy' event in queue and adds duration to its time
                        apply_stun(&mut hunter, &mut queue, is_boss);
                    }
                    
                    Action::Enemy => {
                        // Python: enemy.attack(hunter)
                        enemy_attack(&mut hunter, &mut enemies[enemy_idx], rng);
                        
                        // Python: if not enemy.is_dead():
                        //     hpush(self.queue, (round(prev_time + enemy.speed, 3), 2, 'enemy'))
                        if !enemies[enemy_idx].is_dead() {
                            queue.push(Event {
                                time: round3(prev_time + enemies[enemy_idx].speed),
                                priority: 2,
                                action: Action::Enemy,
                            });
                        }
                    }
                    
                    Action::EnemySpecial => {
                        // Python: enemy.attack_special(hunter)
                        enemy_attack_special(&mut hunter, &mut enemies[enemy_idx], rng);
                        
                        // Python: if not enemy.is_dead():
                        //     hpush(self.queue, (round(prev_time + enemy.speed2, 3), 2, 'enemy_special'))
                        if !enemies[enemy_idx].is_dead() {
                            queue.push(Event {
                                time: round3(prev_time + enemies[enemy_idx].speed2),
                                priority: 2,
                                action: Action::EnemySpecial,
                            });
                        }
                    }
                    
                    Action::Regen => {
                        // Python: hunter.regen_hp()
                        hunter.regen_hp();
                        // Python: enemy.regen_hp()
                        enemies[enemy_idx].regen_hp();
                        // Python: self.elapsed_time += 1
                        elapsed_time += 1;
                        // Python: hpush(self.queue, (self.elapsed_time, 3, 'regen'))
                        queue.push(Event {
                            time: elapsed_time as f64,
                            priority: 3,
                            action: Action::Regen,
                        });
                    }
                }
            }
            
            // Apply pending trample kills (mark additional enemies as dead)
            // Each trampled enemy generates loot via on_kill(), matching Python's behavior
            // Python calls enemy.kill() for each which triggers on_death() -> on_kill()
            for i in 1..=pending_trample_kills {
                if enemy_idx + i < enemies.len() {
                    enemies[enemy_idx + i].hp = 0.0;
                    hunter.result.kills += 1;
                    // Call on_kill for each trampled enemy (generates loot)
                    on_kill(&mut hunter, rng, false);  // Trample only works on non-boss enemies
                }
            }
            
            // Python: if hunter.is_dead(): return
            if hunter.is_dead() {
                break 'main_loop;
            }
            
            // Enemy dead - remove enemy events from queue (Python: on_death removes 'enemy' and 'enemy_special')
            // Python: self.sim.queue = [(p1, p2, u) for p1, p2, u in self.sim.queue if u not in ['enemy', 'enemy_special']]
            let mut temp_events: Vec<Event> = Vec::new();
            while let Some(e) = queue.pop() {
                match e.action {
                    Action::Enemy | Action::EnemySpecial => {
                        // Discard
                    }
                    _ => {
                        temp_events.push(e);
                    }
                }
            }
            for e in temp_events {
                queue.push(e);
            }
            
            // Python: self.sim.hunter.on_kill() - called from enemy.on_death()
            on_kill(&mut hunter, rng, is_boss);
            hunter.result.kills += 1;
            
            // Skip enemies that were killed by trample
            enemy_idx += 1 + pending_trample_kills;
        }
        
        // Python: self.complete_stage()
        // Stage completion effects (Knox Calypso's Advantage, etc.)
        on_stage_complete(&mut hunter, rng, is_boss);
        hunter.current_stage += 1;
        
        if hunter.current_stage >= hunter.max_stage {
            hunter.hp = 0.0;
            hunter.revive_count = hunter.max_revives;  // Prevent revive at max_stage
        }
        
        // Safety limit
        if hunter.current_stage > 1000 {
            break;
        }
    }
    
    // === CALCULATE FINAL LOOT USING GEOMETRIC SERIES FORMULA (after all stages complete) ===
    // Loot: BASE × GeomSum × EnemiesPerStage × LootMultiplier
    let final_stage = hunter.current_stage as f64;
    let enemies_per_stage = 10.0;
    
    // Hunter-specific StageLootMultiplier (from APK: game_dump.cs)
    let stage_loot_mult = match hunter.hunter_type {
        crate::config::HunterType::Borge => 1.051_f64,
        crate::config::HunterType::Ozzy => 1.059_f64,
        crate::config::HunterType::Knox => 1.074_f64,
    };
    
    // Geometric series: sum of (mult^0 + mult^1 + ... + mult^(stage-1))
    // Formula: (mult^stage - 1) / (mult - 1)
    let geom_sum = if stage_loot_mult > 1.0 {
        (stage_loot_mult.powf(final_stage) - 1.0) / (stage_loot_mult - 1.0)
    } else {
        final_stage
    };
    
    // Total enemy factor: geometric sum × enemies per stage
    let total_enemy_factor = geom_sum * enemies_per_stage;
    
    // Per-hunter base loot values (per-enemy per-stage at stage 1, from IRL data)
    let (base_common, base_uncommon, base_rare, base_xp) = match hunter.hunter_type {
        crate::config::HunterType::Borge => (30.74, 26.44, 19.92, 1640000000000.0),
        crate::config::HunterType::Ozzy => (11.1, 9.56, 7.2, 96600000000.0),
        crate::config::HunterType::Knox => (0.00348, 0.00302, 0.00228, 728.0),
    };
    
    // Loot multiplier including all static bonuses
    let loot_mult = hunter.loot_mult;
    
    // Final loot = BASE × GeomSum × EnemiesPerStage × LootMultiplier
    hunter.result.loot_common = base_common * total_enemy_factor * loot_mult;
    hunter.result.loot_uncommon = base_uncommon * total_enemy_factor * loot_mult;
    hunter.result.loot_rare = base_rare * total_enemy_factor * loot_mult;
    hunter.result.total_loot = hunter.result.loot_common + hunter.result.loot_uncommon + hunter.result.loot_rare;
    
    // XP: BASE × Stages × XP_Multiplier (no enemies_per_stage multiplier)
    hunter.result.total_xp = base_xp * final_stage * hunter.xp_mult;
    
    // Finalize
    hunter.result.final_stage = hunter.current_stage;
    hunter.result.elapsed_time = elapsed_time as f64;
    hunter.result.total_loot = hunter.result.loot_common + hunter.result.loot_uncommon + hunter.result.loot_rare;
    
    hunter.result
}

/// Apply stun - IDENTICAL to Python's Hunter.apply_stun()
/// Python:
///   stun_effect = 0.5 if is_boss else 1
///   stun_duration = self.talents['impeccable_impacts'] * 0.1 * stun_effect
///   enemy.stun(stun_duration)
///
/// enemy.stun() does:
///   qe = [(p1, p2, u) for p1, p2, u in self.sim.queue if u == 'enemy'][0]
///   self.sim.queue.remove(qe)
///   hpush(self.sim.queue, (qe[0] + duration, qe[1], qe[2]))
fn apply_stun(hunter: &mut Hunter, queue: &mut BinaryHeap<Event>, _is_boss: bool) {
    if hunter.pending_stun_duration <= 0.0 {
        return;
    }
    
    let stun_duration = hunter.pending_stun_duration;
    hunter.pending_stun_duration = 0.0;
    hunter.result.stun_duration_inflicted += stun_duration;
    
    // Find the 'enemy' event and delay it
    let mut temp_events: Vec<Event> = Vec::new();
    let mut found_enemy: Option<Event> = None;
    
    while let Some(e) = queue.pop() {
        if found_enemy.is_none() && e.action == Action::Enemy {
            found_enemy = Some(e);
        } else {
            temp_events.push(e);
        }
    }
    
    // Put everything back
    for e in temp_events {
        queue.push(e);
    }
    
    // Add enemy event back with delayed time
    if let Some(e) = found_enemy {
        queue.push(Event {
            time: e.time + stun_duration,
            priority: e.priority,
            action: e.action,
        });
    }
}

/// Apply spawn effects - IDENTICAL to Python's hunter.apply_pog(), apply_ood(), etc.
fn apply_spawn_effects(hunter: &mut Hunter, enemy: &mut Enemy, _rng: &mut FastRng) {
    let is_boss = enemy.is_boss;
    let stage_effect = if is_boss { 0.5 } else { 1.0 };
    
    // Presence of God (Borge) - Python: enemy.hp = enemy.max_hp * (1 - pog_effect)
    // NOTE: Python does NOT track POG damage in total_damage!
    if hunter.presence_of_god > 0 {
        let pog_effect = hunter.presence_of_god as f64 * 0.04 * stage_effect;
        let new_hp = enemy.max_hp * (1.0 - pog_effect);
        enemy.hp = new_hp;
        // Python does NOT add this to damage stats
    }
    
    // Omen of Defeat (Borge) - Python: enemy.regen = enemy.regen * (1 - ood_effect)
    if hunter.omen_of_defeat > 0 {
        let ood_effect = hunter.omen_of_defeat as f64 * 0.08 * stage_effect;
        enemy.regen *= 1.0 - ood_effect;
    }
    
    // Soul of Snek (Ozzy) - Python: regen_reduction = 1 - 0.088 * level
    if hunter.soul_of_snek > 0 {
        let regen_reduction = 1.0 - (0.088 * hunter.soul_of_snek as f64);
        enemy.regen *= regen_reduction.max(0.0);
    }
    
    // Gift of Medusa (Ozzy) - Python: enemy.regen -= hunter_regen * medusa_level * 0.06
    if hunter.gift_of_medusa > 0 {
        let anti_regen = hunter.regen * hunter.gift_of_medusa as f64 * 0.06;
        enemy.regen = (enemy.regen - anti_regen).max(0.0);
    }
}

/// Hunter attack - mirrors Python's Borge.attack() / Ozzy.attack() / Knox.attack()
/// Returns number of additional enemies killed by trample (caller handles marking them dead)
#[inline(always)]
fn hunter_attack(
    hunter: &mut Hunter, 
    enemy: &mut Enemy, 
    rng: &mut FastRng, 
    _elapsed_time: f64,
) -> usize {
    let is_boss = enemy.is_boss;
    
    // Get effective stats
    let effective_power = hunter.power;
    let effective_effect_chance = hunter.get_effective_effect_chance(is_boss);
    
    // Calculate damage based on hunter type
    // Borge returns (damage, trample_kills), others return (damage, 0)
    let (damage, trample_kills) = match hunter.hunter_type {
        HunterType::Borge => {
            borge_attack(hunter, enemy, rng, effective_power, effective_effect_chance, is_boss)
        }
        HunterType::Ozzy => {
            (ozzy_attack(hunter, enemy, rng, effective_power, effective_effect_chance, is_boss), 0)
        }
        HunterType::Knox => {
            (knox_attack(hunter, enemy, rng, effective_power, effective_effect_chance, is_boss), 0)
        }
    };
    
    // Common post-attack effects (Borge only - Ozzy/Knox handle their own)
    if hunter.hunter_type == HunterType::Borge {
        // Lifesteal
        if hunter.lifesteal > 0.0 {
            let heal = damage * hunter.lifesteal;
            let effective = heal.min(hunter.max_hp - hunter.hp);
            hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
            hunter.result.lifesteal += effective;
        }
        
        // Life of the Hunt
        if hunter.life_of_the_hunt > 0 && rng.f64() < effective_effect_chance {
            let loth_heal = damage * hunter.life_of_the_hunt as f64 * 0.06;
            hunter.hp = (hunter.hp + loth_heal).min(hunter.max_hp);
            hunter.result.life_of_the_hunt_healing += loth_heal;
            hunter.result.effect_procs += 1;
        }
        
        // Impeccable Impacts (stun)
        if hunter.impeccable_impacts > 0 && rng.f64() < effective_effect_chance {
            let stun_effect = if is_boss { 0.5 } else { 1.0 };
            let stun_duration = hunter.impeccable_impacts as f64 * 0.1 * stun_effect;
            hunter.pending_stun_duration = stun_duration;
            hunter.result.effect_procs += 1;
        }
        
        // Fires of War
        if hunter.fires_of_war > 0 && rng.f64() < effective_effect_chance {
            hunter.fires_of_war_buff = hunter.fires_of_war as f64 * 0.1;
            hunter.result.effect_procs += 1;
        }
    }
    
    trample_kills  // Return trample kills for Borge, 0 for others
}

/// Borge attack - mirrors Python's Borge.attack()
/// Returns (damage, trample_kills) where trample_kills is the number of ADDITIONAL enemies killed
fn borge_attack(
    hunter: &mut Hunter, 
    enemy: &mut Enemy, 
    rng: &mut FastRng, 
    effective_power: f64, 
    _effective_effect_chance: f64,
    is_boss: bool,
) -> (f64, usize) {
    // Python: if random.random() < self.special_chance: damage = self.power * self.special_damage
    let damage = if rng.f64() < hunter.special_chance {
        let crit_dmg = effective_power * hunter.special_damage;
        hunter.result.crits += 1;
        hunter.result.extra_damage_from_crits += crit_dmg - effective_power;
        crit_dmg
    } else {
        effective_power
    };
    
    // Track stats - Python: self.total_damage += damage
    hunter.result.damage += damage;
    hunter.result.attacks += 1;
    
    // Check for trample (Borge mod)
    // Python: trample_power = min(int(damage / enemies[0].max_hp), 10)
    // Returns the number of ADDITIONAL enemies killed (not counting current target)
    let mut trample_kills: usize = 0;
    if hunter.has_trample && !is_boss && damage > enemy.max_hp {
        let trample_power = ((damage / enemy.max_hp) as usize).min(10);
        if trample_power > 1 {
            enemy.hp = 0.0;
            // Python counts current_target + extras, but we return only extras to skip
            // trample_power - 1 because current enemy is already being processed
            trample_kills = trample_power - 1;
            hunter.result.trample_kills += trample_kills as i32;
        } else {
            enemy.take_damage(damage);
        }
    } else {
        enemy.take_damage(damage);
    }
    
    (damage, trample_kills)
}

/// Ozzy attack - mirrors Python's Ozzy.attack()
/// Python's Ozzy uses an attack_queue for multistrikes and echoes, but we simplify
/// by processing them all in one attack call (probabilistically equivalent)
fn ozzy_attack(
    hunter: &mut Hunter, 
    enemy: &mut Enemy, 
    rng: &mut FastRng, 
    effective_power: f64, 
    effective_effect_chance: f64,
    is_boss: bool,
) -> f64 {
    // Main attack
    let base_damage = effective_power;
    hunter.result.attacks += 1;
    
    // Python: Trickster's Boon at half effect_chance gives evade charge
    if hunter.tricksters_boon > 0 && rng.f64() < effective_effect_chance / 2.0 {
        hunter.trickster_charges += 1;
        hunter.result.effect_procs += 1;
    }
    
    // Track which extra attacks were triggered (Python: attack_queue)
    let mut multistrike_triggered = false;
    let mut echo_triggered = false;
    
    // Python: if random.random() < self.special_chance: trigger multistrike
    if rng.f64() < hunter.special_chance {
        multistrike_triggered = true;
    }
    
    // Python: Thousand Needles stun (only on main attack)
    if hunter.thousand_needles > 0 && rng.f64() < effective_effect_chance {
        let stun_effect = if is_boss { 0.5 } else { 1.0 };
        let stun_duration = hunter.thousand_needles as f64 * 0.05 * stun_effect;
        hunter.pending_stun_duration = stun_duration;
        hunter.result.effect_procs += 1;
    }
    
    // Python: Echo Bullets at half effect chance
    if hunter.echo_bullets > 0 && rng.f64() < effective_effect_chance / 2.0 {
        echo_triggered = true;
        hunter.result.effect_procs += 1;
    }
    
    // === CRIPPLING SHOTS DAMAGE ===
    // Python: cripple_damage = target.hp * (self.crippling_on_target * 0.008) * cripple_boss_reduction
    let cripple_boss_reduction = if is_boss { 0.1 } else { 1.0 };
    let cripple_damage = enemy.hp * (hunter.decay_stacks as f64 * 0.008) * cripple_boss_reduction;
    hunter.decay_stacks = 0;  // Reset stacks after attack
    
    // === OMEN OF DECAY MULTIPLIER ===
    // Python: if self.talents["omen_of_decay"] and random.random() < (self.effect_chance / 2):
    let omen_multiplier = if hunter.omen_of_decay > 0 && rng.f64() < effective_effect_chance / 2.0 {
        hunter.result.effect_procs += 1;
        1.0 + (hunter.omen_of_decay as f64 * 0.03)
    } else {
        1.0
    };
    
    // Final main attack damage
    let main_damage = (base_damage + cripple_damage) * omen_multiplier;
    enemy.take_damage(main_damage);
    
    // Track damage
    hunter.result.damage += base_damage;
    hunter.result.extra_damage_from_crits += cripple_damage;
    
    // Lifesteal on main attack base damage (Python: not on cripple/omen extra)
    // WASM: Soul of Snek empowers lifesteal during Vectid buff!
    if hunter.lifesteal > 0.0 {
        let mut heal = base_damage * hunter.lifesteal;
        if hunter.empowered_regen > 0 {
            heal *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
        }
        let effective = heal.min(hunter.max_hp - hunter.hp);
        hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
        hunter.result.lifesteal += effective;
    }
    
    // Crippling Shots proc for NEXT attack (main attack can proc)
    if hunter.crippling_shots > 0 && rng.f64() < effective_effect_chance {
        hunter.decay_stacks += hunter.crippling_shots;
        hunter.result.effect_procs += 1;
    }
    
    // Process extra attacks (multistrikes and echoes)
    let mut total_extra_damage = 0.0;
    
    // Multistrike: deals special_damage multiplier of power
    if multistrike_triggered {
        let ms_dmg = effective_power * hunter.special_damage;
        enemy.take_damage(ms_dmg);
        hunter.result.multistrikes += 1;
        hunter.result.extra_damage_from_ms += ms_dmg;
        total_extra_damage += ms_dmg;
        
        // Lifesteal on multistrike
        if hunter.lifesteal > 0.0 {
            let mut heal = ms_dmg * hunter.lifesteal;
            if hunter.empowered_regen > 0 {
                heal *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
            }
            hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
            hunter.result.lifesteal += heal.min(hunter.max_hp - hunter.hp);
        }
        
        // Crippling Shots proc (multistrike can proc)
        if hunter.crippling_shots > 0 && rng.f64() < effective_effect_chance {
            hunter.decay_stacks += hunter.crippling_shots;
            hunter.result.effect_procs += 1;
        }
    }
    
    // Echo Bullets: deals 5% per level of power (WASM: cannot trigger multistrike)
    if echo_triggered {
        let echo_dmg = effective_power * (hunter.echo_bullets as f64 * 0.05);
        enemy.take_damage(echo_dmg);
        hunter.result.echo_bullets += 1;
        total_extra_damage += echo_dmg;
        
        // Lifesteal on echo
        if hunter.lifesteal > 0.0 {
            let mut heal = echo_dmg * hunter.lifesteal;
            if hunter.empowered_regen > 0 {
                heal *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
            }
            hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
            hunter.result.lifesteal += heal.min(hunter.max_hp - hunter.hp);
        }
        
        // Crippling Shots proc (echo can proc)
        if hunter.crippling_shots > 0 && rng.f64() < effective_effect_chance {
            hunter.decay_stacks += hunter.crippling_shots;
            hunter.result.effect_procs += 1;
        }
    }
    
    main_damage + total_extra_damage
}

/// Knox attack - mirrors Python's Knox.attack() 
/// Knox fires a salvo of projectiles
fn knox_attack(
    hunter: &mut Hunter, 
    enemy: &mut Enemy, 
    rng: &mut FastRng, 
    effective_power: f64, 
    effective_effect_chance: f64,
    _is_boss: bool,
) -> f64 {
    // Python: num_projectiles = self.salvo_projectiles
    let mut num_projectiles = hunter.salvo_projectiles;
    let base_projectiles = num_projectiles;  // Track base for extra damage calc
    
    // Ghost Bullets - chance for extra projectile
    // Python: ghost_chance = self.talents["ghost_bullets"] * 0.0667
    if hunter.ghost_bullets > 0 {
        let ghost_chance = hunter.ghost_bullets as f64 * 0.0667;
        if rng.f64() < ghost_chance {
            num_projectiles += 1;
            hunter.result.ghost_bullets += 1;  // Track ghost bullet procs
        }
    }
    
    let base_salvo = hunter.salvo_projectiles.max(1) as f64;
    let mut total_damage = 0.0;
    
    for i in 0..num_projectiles {
        // Each projectile deals FULL attack power (not split!)
        // This is how Knox can clear stages quickly with enough bullets
        // Python: bullet_damage = self.power (FULL damage per bullet)
        let mut bullet_damage = effective_power;
        
        // Check for charge (Knox's crit equivalent)
        // Python: if random.random() < self.charge_chance: bullet_damage *= (1 + self.charge_gained)
        if rng.f64() < hunter.charge_chance {
            bullet_damage *= 1.0 + hunter.charge_gained;
            hunter.result.crits += 1;  // Track charges as crits
        }
        
        // Finishing Move on last bullet
        // Python: if i == num_projectiles - 1 and self.talents["finishing_move"] > 0:
        //     if random.random() < (self.effect_chance * 2): bullet_damage *= self.special_damage
        if i == num_projectiles - 1 && hunter.finishing_move > 0 {
            if rng.f64() < effective_effect_chance * 2.0 {
                bullet_damage *= hunter.special_damage;
                hunter.result.effect_procs += 1;
            }
        }
        
        total_damage += bullet_damage;
    }
    
    // Apply damage to enemy
    enemy.take_damage(total_damage);
    
    // Track stats - Python: self.total_damage += total_damage
    hunter.result.damage += total_damage;
    hunter.result.attacks += 1;
    
    // Track extra salvo damage (from ghost bullets)
    // Extra damage = damage from projectiles beyond base salvo count
    if num_projectiles > base_projectiles {
        let extra_projectile_count = num_projectiles - base_projectiles;
        let damage_per_projectile = total_damage / num_projectiles as f64;
        hunter.result.extra_salvo_damage += damage_per_projectile * extra_projectile_count as f64;
    }
    
    // Lifesteal (if Knox has any)
    if hunter.lifesteal > 0.0 {
        let heal = total_damage * hunter.lifesteal;
        let effective = heal.min(hunter.max_hp - hunter.hp);
        hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
        hunter.result.lifesteal += effective;
    }
    
    total_damage
}

/// Enemy attack - mirrors Python's Enemy.attack()
#[inline(always)]
fn enemy_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut FastRng) {
    // Python: if random.random() < self.special_chance: damage = self.power * self.special_damage
    let (damage, is_crit) = if rng.f64() < enemy.special_chance {
        (enemy.power * enemy.special_damage, true)
    } else {
        (enemy.power, false)
    };
    
    // Python: hunter.receive_damage(self, damage, is_crit)
    hunter_receive_damage(hunter, enemy, damage, is_crit, rng);
}

/// Enemy special attack - mirrors Python's Boss.attack_special()
fn enemy_attack_special(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut FastRng) {
    match enemy.secondary_type {
        SecondaryAttackType::Gothmorgor => {
            // Gothmorgor: attack + enrage
            enemy_attack(hunter, enemy, rng);
            enemy.add_enrage();
        }
        SecondaryAttackType::Exoscarab => {
            // Exoscarab: harden (95% DR for 5 ticks)
            enemy.start_harden();
        }
        SecondaryAttackType::None => {}
    }
}

/// Hunter receives damage - mirrors Python's Borge/Ozzy/Knox.receive_damage()
fn hunter_receive_damage(hunter: &mut Hunter, attacker: &mut Enemy, damage: f64, is_crit: bool, rng: &mut FastRng) {
    match hunter.hunter_type {
        HunterType::Borge => borge_receive_damage(hunter, attacker, damage, is_crit, rng),
        HunterType::Ozzy => ozzy_receive_damage(hunter, attacker, damage, is_crit, rng),
        HunterType::Knox => knox_receive_damage(hunter, attacker, damage, is_crit, rng),
    }
}

/// Borge receive damage - mirrors Python's Borge.receive_damage()
fn borge_receive_damage(hunter: &mut Hunter, attacker: &mut Enemy, damage: f64, is_crit: bool, rng: &mut FastRng) {
    // Python: if random.random() < self.evade_chance: return
    if rng.f64() < hunter.evade_chance {
        hunter.result.evades += 1;
        return;
    }
    
    let mut final_damage = damage;
    
    // Borge: Minotaur DR first (separate layer)
    if hunter.minotaur_dr > 0.0 {
        final_damage *= 1.0 - hunter.minotaur_dr;
    }
    
    // Borge: Crit reduction from Weakspot Analysis
    if is_crit && hunter.weakspot_analysis > 0 {
        final_damage *= 1.0 - hunter.weakspot_analysis as f64 * 0.11;
    }
    
    // Apply main DR
    let mitigated_damage = final_damage * (1.0 - hunter.damage_reduction);
    hunter.hp -= mitigated_damage;
    
    // Track stats
    hunter.result.damage_taken += mitigated_damage;
    hunter.result.enemy_attacks += 1;
    hunter.result.mitigated_damage += final_damage - mitigated_damage;
    
    // Helltouch Barrier reflection (Borge)
    if hunter.helltouch_barrier_level > 0 && mitigated_damage > 0.0 {
        let helltouch_effect = if attacker.is_boss { 0.1 } else { 1.0 };
        let reflected = mitigated_damage * hunter.helltouch_barrier_level as f64 * 0.08 * helltouch_effect;
        attacker.hp -= reflected;
        hunter.result.helltouch_barrier += reflected;
        if attacker.is_dead() {
            hunter.result.helltouch_kills += 1;
        }
    }
    
    // Check death and revive
    if hunter.is_dead() {
        hunter.try_revive();
    }
}

/// Ozzy receive damage - mirrors Python's Ozzy.receive_damage()
fn ozzy_receive_damage(hunter: &mut Hunter, _attacker: &mut Enemy, damage: f64, is_crit: bool, rng: &mut FastRng) {
    // Python Step 1: Check trickster charges FIRST
    if hunter.trickster_charges > 0 {
        hunter.trickster_charges -= 1;
        hunter.result.trickster_evades += 1;
        return;
    }
    
    // Python Step 2: Check normal evade
    if rng.f64() < hunter.evade_chance {
        hunter.result.evades += 1;
        return;
    }
    
    // Python Step 3: Failed to evade - take damage
    // Apply scarab DR (separate multiplicative layer)
    let scarab_reduced = damage * (1.0 - hunter.scarab_dr);
    let mitigated_damage = scarab_reduced * (1.0 - hunter.damage_reduction);
    hunter.hp -= mitigated_damage;
    
    // Track stats
    hunter.result.damage_taken += mitigated_damage;
    hunter.result.enemy_attacks += 1;
    hunter.result.mitigated_damage += scarab_reduced - mitigated_damage;
    
    // Python Step 4: Dance of Dashes - on crit, chance to gain trickster charge
    if is_crit && hunter.dance_of_dashes > 0 {
        if rng.f64() < hunter.dance_of_dashes as f64 * 0.05 {
            hunter.trickster_charges += 1;
            hunter.result.effect_procs += 1;
        }
    }
    
    // Check death and revive
    if hunter.is_dead() {
        hunter.try_revive();
    }
}

/// Knox receive damage - mirrors Python's Knox.receive_damage()
fn knox_receive_damage(hunter: &mut Hunter, _attacker: &mut Enemy, damage: f64, _is_crit: bool, rng: &mut FastRng) {
    let mut final_damage = damage;
    
    // Check for block first
    // Python: if random.random() < self.block_chance: blocked_amount = damage * 0.5
    if rng.f64() < hunter.block_chance {
        let blocked = damage * 0.5;
        final_damage -= blocked;
        // Track blocked damage (we could add a field for this)
    }
    
    // Apply remaining damage through DR
    if final_damage > 0.0 {
        let mitigated_damage = final_damage * (1.0 - hunter.damage_reduction);
        hunter.hp -= mitigated_damage;
        
        // Track stats
        hunter.result.damage_taken += mitigated_damage;
        hunter.result.enemy_attacks += 1;
        hunter.result.mitigated_damage += final_damage - mitigated_damage;
        
        // Check death and revive
        if hunter.is_dead() {
            hunter.try_revive();
        }
    }
}

/// On kill effects - mirrors Python's Hunter.on_kill()
fn on_kill(hunter: &mut Hunter, rng: &mut FastRng, is_boss: bool) {
    let effective_effect_chance = hunter.get_effective_effect_chance(is_boss);
    
    // DEBUG: Track how many times on_kill is called
    hunter.result.on_kill_calls += 1;
    
    // Call Me Lucky Loot proc (not on bosses) - independent RNG, separate from other effect procs
    // Each talent/ability has its own effect_chance roll, so Lucky Loot gets its own counter
    if !is_boss && hunter.call_me_lucky_loot > 0 {
        if rng.f64() < effective_effect_chance {
            hunter.result.lucky_loot_procs += 1;
        }
    }
    
    // Unfair Advantage - Python: if random.random() < effect_chance and UA:
    //   heal = max_hp * 0.02 * UA_level
    if hunter.unfair_advantage > 0 && rng.f64() < effective_effect_chance {
        let heal = hunter.max_hp * 0.02 * hunter.unfair_advantage as f64;
        hunter.hp = (hunter.hp + heal).min(hunter.max_hp);
        hunter.result.unfair_advantage_healing += heal;
        hunter.result.effect_procs += 1;
        
        // Vectid Elixir (Ozzy) - empowered regen for 5 ticks
        if hunter.vectid_elixir > 0 {
            hunter.empowered_regen += 5;
        }
    }
    
    // Unfair Advantage healing is processed in on_kill()
}

/// On stage complete - mirrors Python's Simulation.complete_stage()
fn on_stage_complete(hunter: &mut Hunter, rng: &mut FastRng, is_boss: bool) {
    let effective_effect_chance = hunter.get_effective_effect_chance(is_boss);
    
    // Calypso's Advantage (Knox) - chance to gain Hundred Souls stack
    if hunter.calypsos_advantage > 0 && rng.f64() < effective_effect_chance * 2.5 {
        let max_stacks = 100 + hunter.soul_amplification * 10;
        if hunter.hundred_souls_stacks < max_stacks {
            hunter.hundred_souls_stacks += 1;
            hunter.result.effect_procs += 1;  // Track effect proc
        }
    }
}

/// Run multiple simulations in parallel
pub fn run_simulations_parallel(config: &BuildConfig, count: usize) -> Vec<SimResult> {
    (0..count)
        .into_par_iter()
        .map(|i| run_simulation_with_seed(config, i as u64))
        .collect()
}

/// Run multiple simulations sequentially
pub fn run_simulations_sequential(config: &BuildConfig, count: usize) -> Vec<SimResult> {
    let mut rng = FastRng::new(rand::random::<u64>());
    (0..count)
        .map(|_| run_simulation_with_rng(config, &mut rng))
        .collect()
}

/// Run simulations and return aggregated stats - MATCHES WHAT main.rs AND python.rs EXPECT
pub fn run_and_aggregate(config: &BuildConfig, count: usize, parallel: bool) -> AggregatedStats {
    let results = if parallel {
        run_simulations_parallel(config, count)
    } else {
        run_simulations_sequential(config, count)
    };
    
    AggregatedStats::from_results(&results)
}
