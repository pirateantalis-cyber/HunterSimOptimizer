//! Core simulation engine

use crate::config::{BuildConfig, HunterType};
use crate::enemy::{Enemy, SecondaryAttackType};
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

#[derive(Debug, Clone, Copy, PartialEq)]
enum Action {
    HunterAttack,
    EnemyAttack,
    EnemySpecial,
    Regen,
    Stun(f64),  // Python queues stun events with priority 0, stores duration
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
    let mut regen_time: f64 = 0.0;  // Python's elapsed_time only updates on regen ticks
    let mut total_loot: f64 = 0.0;
    let mut _total_trample_triggers = 0;
    
    // Debug flag - set to true to trace stage 200 boss
    let debug_boss = std::env::var("DEBUG_BOSS").is_ok();
    
    let mut queue: BinaryHeap<Event> = BinaryHeap::new();
    
    // Queue persistent events ONCE at the start (like Python)
    // Hunter attack: Python queues at hunter.speed from time 0
    // Regen: Python queues at elapsed_time (0) initially
    let initial_attack_speed = hunter.speed;  // Use base speed for first attack
    queue.push(Event { time: initial_attack_speed, priority: 1, action: Action::HunterAttack });
    // Python: hpush(self.queue, (self.elapsed_time, 3, 'regen')) with elapsed_time=0
    queue.push(Event { time: 0.0, priority: 3, action: Action::Regen });
    
    // Main simulation loop - progress through stages
    'stages: loop {
        let stage = hunter.current_stage;
        
        // Debug output for boss stage
        let is_boss_stage = stage % 100 == 0 && stage > 0;
        if debug_boss && is_boss_stage {
            eprintln!("\n=== ENTERING BOSS STAGE {} ===", stage);
            eprintln!("Hunter HP: {:.0}/{:.0}, regen_time: {}", hunter.hp, hunter.max_hp, regen_time);
        }
        
        // Spawn enemies for this stage
        let mut enemies: Vec<Enemy> = if is_boss_stage {
            // Boss stage
            vec![Enemy::new_boss(stage, hunter.hunter_type)]
        } else {
            // Regular stage - 10 enemies
            (1..=10).map(|i| Enemy::new(i, stage, hunter.hunter_type)).collect()
        };
        
        // Track how many enemies to skip due to trample
        let mut enemies_to_skip = 0;
        let total_enemies = enemies.len();
        
        // Fight enemies in the stage
        for enemy_idx in 0..total_enemies {
            // Skip enemies killed by trample
            if enemies_to_skip > 0 {
                enemies_to_skip -= 1;
                on_kill(&mut hunter, rng, false);  // Trample kills are never boss kills
                hunter.result.kills += 1;
                continue;
            }
            
            let enemy = &mut enemies[enemy_idx];
            
            // Remove only enemy events from queue, keep hunter attack and regen (like Python)
            let mut temp_events: Vec<Event> = Vec::new();
            while let Some(event) = queue.pop() {
                match event.action {
                    Action::EnemyAttack | Action::EnemySpecial => {
                        // Discard enemy events
                    }
                    _ => {
                        temp_events.push(event);
                    }
                }
            }
            for event in temp_events {
                queue.push(event);
            }
            
            // Queue enemy-specific events
            // Use regen_time (not elapsed_time) to match Python's behavior
            // Python's elapsed_time only updates on regen ticks, not every event
            queue.push(Event { time: regen_time + enemy.speed, priority: 2, action: Action::EnemyAttack });
            
            if enemy.has_secondary {
                queue.push(Event { time: regen_time + enemy.speed2, priority: 2, action: Action::EnemySpecial });
            }
            
            // Apply on-spawn effects
            apply_spawn_effects(&mut hunter, enemy, rng);
            
            if debug_boss && is_boss_stage {
                eprintln!("Boss spawned: HP={:.0}/{:.0}, Power={:.0}", enemy.hp, enemy.max_hp, enemy.power);
                eprintln!("Boss attacks queued at: {:.2} and {:.2}", regen_time + enemy.speed, regen_time + enemy.speed2);
                let eff_speed = hunter.get_effective_speed(true);
                let eff_effect = hunter.get_effective_effect_chance(true);
                eprintln!("Hunter effective stats on boss: speed={:.4} effect={:.4}", eff_speed, eff_effect);
            }
            
            // Track combat events for debugging
            let mut _event_count = 0;
            
            // Combat loop - continues until enemy dies or hunter dies permanently
            loop {
                // Inner combat loop - runs until someone "dies"
                while !enemy.is_dead() && !hunter.is_dead() {
                    let event = match queue.pop() {
                        Some(e) => e,
                        None => break,
                    };
                    
                    elapsed_time = event.time;
                    _event_count += 1;
                    
                    // Debug first 20 events and every 100th thereafter for boss
                    if debug_boss && is_boss_stage && (_event_count <= 20 || _event_count % 100 == 0) {
                        eprintln!("[{:>4}] t={:.2} {:?} | H:{:.0} B:{:.0}", 
                            _event_count, elapsed_time, event.action, hunter.hp, enemy.hp);
                    }
                    
                    match event.action {
                        Action::HunterAttack => {
                            // Calculate remaining enemies for trample calculation
                            let remaining = total_enemies - enemy_idx - 1;
                            let trample_kills = hunter_attack(&mut hunter, enemy, rng, remaining, elapsed_time);
                            
                            // If trample killed additional enemies, queue them to be skipped
                            if trample_kills > 0 {
                                enemies_to_skip += trample_kills;
                                _total_trample_triggers += 1;
                            }
                            
                            // Python: if Impeccable Impacts procs, queue 'stun' at priority 0 (immediate)
                            // hpush(self.sim.queue, (0, 0, 'stun'))
                            if hunter.pending_stun_duration > 0.0 {
                                let stun_dur = hunter.pending_stun_duration;
                                hunter.pending_stun_duration = 0.0;
                                queue.push(Event { 
                                    time: 0.0,  // Python uses time 0
                                    priority: 0,  // Python uses priority 0 (highest)
                                    action: Action::Stun(stun_dur) 
                                });
                            }
                            
                            // Use effective speed accounting for Atlas Protocol and Fires of War
                            let attack_speed = hunter.get_effective_speed(enemy.is_boss);
                            queue.push(Event { 
                                time: elapsed_time + attack_speed, 
                                priority: 1, 
                                action: Action::HunterAttack 
                            });
                        }
                        Action::Stun(duration) => {
                            // Python: find the 'enemy' event in queue and add duration to its time
                            // qe = [(p1, p2, u) for p1, p2, u in self.sim.queue if u == 'enemy'][0]
                            // self.sim.queue.remove(qe)
                            // hpush(self.sim.queue, (qe[0] + duration, qe[1], qe[2]))
                            let mut temp_events: Vec<Event> = Vec::new();
                            let mut found_enemy_event: Option<Event> = None;
                            
                            while let Some(e) = queue.pop() {
                                if found_enemy_event.is_none() && e.action == Action::EnemyAttack {
                                    found_enemy_event = Some(e);
                                } else {
                                    temp_events.push(e);
                                }
                            }
                            
                            // Put back all events
                            for e in temp_events {
                                queue.push(e);
                            }
                            
                            // Add the enemy event back with delayed time
                            if let Some(e) = found_enemy_event {
                                queue.push(Event {
                                    time: e.time + duration,
                                    priority: e.priority,
                                    action: e.action
                                });
                            }
                        }
                        Action::EnemyAttack => {
                            // Python: stun is already applied via Stun action, no pending check needed
                            let hp_before = hunter.hp;
                            enemy_attack(&mut hunter, enemy, rng);
                            if debug_boss && is_boss_stage && _event_count <= 50 {
                                eprintln!("      BOSS HIT: {:.0} damage (HP: {:.0} -> {:.0})", hp_before - hunter.hp, hp_before, hunter.hp);
                            }
                            if !enemy.is_dead() {
                                queue.push(Event { 
                                    time: elapsed_time + enemy.speed, 
                                    priority: 2, 
                                    action: Action::EnemyAttack 
                                });
                            }
                        }
                        Action::EnemySpecial => {
                            // Boss special attacks are also affected by stun (Ozzy's Thousand Needles)
                            // Using same pending_stun_delay mechanism
                            if enemy.is_boss {
                                // Note: We don't apply pending_stun_delay to boss special attacks
                                // because Python only tracks one 'enemy' event type for stun
                                // and boss specials are 'enemy_special' which isn't modified by stun
                                // Let me verify this in Python... Actually Python's stun only affects 'enemy' events
                                match enemy.secondary_type {
                                    SecondaryAttackType::Gothmorgor => {
                                        // Gothmorgor (Borge boss): deals damage + adds enrage
                                        // Uses the same damage logic as regular enemy attack
                                        enemy_attack(&mut hunter, enemy, rng);
                                        enemy.add_enrage();
                                    }
                                    SecondaryAttackType::Exoscarab => {
                                        // Exoscarab (Ozzy boss): triggers harden (95% DR, 3x regen for 5 ticks)
                                        // Enrage stacks added when harden ends (in regen_hp)
                                        enemy.start_harden();
                                    }
                                    SecondaryAttackType::None => {}
                                }
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
                            // Python: self.elapsed_time += 1 then hpush(self.queue, (self.elapsed_time, 3, 'regen'))
                            // regen_time represents Python's elapsed_time (discrete seconds counter)
                            regen_time += 1.0;
                            queue.push(Event { 
                                time: regen_time, 
                                priority: 3, 
                                action: Action::Regen 
                            });
                        }
                    }
                }
                
                // Check why we exited the combat loop
                if enemy.is_dead() {
                    // Enemy killed - move to next enemy
                    if debug_boss && is_boss_stage {
                        eprintln!("BOSS KILLED! Hunter HP: {:.0}", hunter.hp);
                    }
                    break;
                }
                
                if hunter.is_dead() {
                    if hunter.try_revive() {
                        // Revived - continue fighting the SAME enemy
                        if debug_boss && is_boss_stage {
                            eprintln!("REVIVE! Hunter HP restored to {:.0}, revive #{}", hunter.hp, hunter.revive_count);
                        }
                        continue;
                    } else {
                        // Dead for real, end simulation
                        if debug_boss && is_boss_stage {
                            eprintln!("HUNTER DIED! No revives left. Boss HP: {:.0}", enemy.hp);
                        }
                        break 'stages;
                    }
                }
                
                // No more events - shouldn't happen, but break to be safe
                break;
            }
            
            // Enemy killed - pass is_boss flag for effect chance calculation
            on_kill(&mut hunter, rng, enemy.is_boss);
            hunter.result.kills += 1;
        }
        
        // Check if this was a boss stage (stage % 100 == 0)
        let is_boss_stage = stage % 100 == 0 && stage > 0;
        
        // Stage complete - calculate per-resource loot
        on_stage_complete(&mut hunter, rng, is_boss_stage);
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
    // Presence of God (Borge) - reduce enemy STARTING HP by 4% per level
    // Python: enemy.hp = enemy.max_hp * (1 - talents["presence_of_god"] * 0.04 * stage_effect)
    // where stage_effect = 0.5 on bosses, 1.0 otherwise
    // NOTE: This reduces CURRENT hp, not max_hp! Enemy still shows full max HP.
    if hunter.presence_of_god > 0 {
        let stage_effect = if enemy.is_boss { 0.5 } else { 1.0 };
        let pog_reduction = (hunter.presence_of_god as f64 * 0.04 * stage_effect).min(0.99);
        let new_hp = enemy.max_hp * (1.0 - pog_reduction);
        let damage = enemy.hp - new_hp;
        enemy.hp = new_hp;
        hunter.result.damage += damage;  // Track as damage for stats
    }
    
    // Omen of Defeat - reduce enemy REGEN only (not HP/power!)
    // Python: enemy.regen = enemy.regen * (1 - talents["omen_of_defeat"] * 0.08 * stage_effect)
    // where stage_effect = 0.5 on bosses, 1.0 otherwise
    if hunter.omen_of_defeat > 0 {
        let stage_effect = if enemy.is_boss { 0.5 } else { 1.0 };
        let reduction = 1.0 - (0.08 * hunter.omen_of_defeat as f64 * stage_effect);
        enemy.regen *= reduction.max(0.0);
    }
    
    // Soul of Snek (Ozzy) - reduce enemy regen by 8.8% per level
    if hunter.soul_of_snek > 0 {
        let regen_reduction = 1.0 - (0.088 * hunter.soul_of_snek as f64);
        enemy.regen *= regen_reduction.max(0.0);
    }
    
    // Gift of Medusa (Ozzy) - WASM: enemy.regen -= hunter_regen * medusa_level * 0.06
    // NOTE: Uses hunter REGEN, not max_hp!
    if hunter.gift_of_medusa > 0 {
        let anti_regen = hunter.regen * hunter.gift_of_medusa as f64 * 0.06;
        enemy.regen = (enemy.regen - anti_regen).max(0.0);
    }
}

/// Handle on-kill effects for hunter
fn on_kill(hunter: &mut Hunter, rng: &mut impl Rng, is_boss_kill: bool) {
    // NOTE: Trickster's Boon procs on ATTACK (in ozzy_attack), NOT on kill!
    
    // Get effective effect chance (Atlas Protocol bonus on bosses)
    let effective_effect_chance = hunter.get_effective_effect_chance(is_boss_kill);
    
    // Unfair Advantage (Ozzy/shared) - effect chance to heal 2% max HP per level
    if hunter.unfair_advantage > 0 && rng.gen::<f64>() < effective_effect_chance {
        let heal_amount = hunter.max_hp * 0.02 * hunter.unfair_advantage as f64;
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.unfair_advantage_healing += heal_amount;
        hunter.result.effect_procs += 1;
        
        // Vectid Elixir (Ozzy) - empowered regen for 5 ticks after Unfair Advantage
        if hunter.vectid_elixir > 0 {
            hunter.empowered_regen += 5;
        }
    }
}

/// Handle on-stage-complete effects for hunter
fn on_stage_complete(hunter: &mut Hunter, rng: &mut impl Rng, is_boss_stage: bool) {
    // Get effective effect chance (Atlas Protocol bonus on boss stages)
    let effective_effect_chance = hunter.get_effective_effect_chance(is_boss_stage);
    
    // Calypso's Advantage (Knox) - chance to gain Hundred Souls stack on stage clear
    if hunter.calypsos_advantage > 0 && rng.gen::<f64>() < effective_effect_chance * 2.5 {
        // Max stacks = 100 base + dead_men_tell_no_tales * 10
        let max_stacks = 100 + hunter.soul_amplification * 10;
        if hunter.hundred_souls_stacks < max_stacks {
            hunter.hundred_souls_stacks += 1;
            hunter.result.effect_procs += 1;
        }
    }
}

/// Knox salvo attack - fires multiple projectiles per attack
fn knox_salvo_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng, effective_power: f64, _current_time: f64) {
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
        let heal_amount = actual_damage * hunter.lifesteal;
        let effective_heal = heal_amount.min(hunter.max_hp - hunter.hp);
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.lifesteal += effective_heal;
    }
    
    // Effect proc (stun) - uses pending_stun_delay to match Python mechanic
    if rng.gen::<f64>() < hunter.effect_chance {
        hunter.result.effect_procs += 1;
        let stun_duration = 1.0 + 0.2 * hunter.effect_chance;
        let actual_stun = if enemy.is_boss { stun_duration * 0.5 } else { stun_duration };
        enemy.pending_stun_delay += actual_stun;  // Use pending_stun_delay like Python
        hunter.result.stun_duration_inflicted += actual_stun;
    }
}

/// Hunter attacks enemy - returns number of additional enemies killed by trample
fn hunter_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng, remaining_enemies: usize, current_time: f64) -> usize {
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
        knox_salvo_attack(hunter, enemy, rng, effective_power, current_time);
        return 0;  // Knox doesn't have trample
    }
    
    // OZZY has completely different attack mechanics - no crit, uses multistrike instead
    if hunter.hunter_type == HunterType::Ozzy {
        ozzy_attack(hunter, enemy, rng, effective_power, current_time);
        return 0;  // Ozzy doesn't have trample
    }
    
    // BORGE: Check for crit (Borge uses traditional crit mechanics)
    let base_damage = if rng.gen::<f64>() < effective_crit_chance {
        hunter.result.crits += 1;
        let crit_dmg = effective_power * effective_crit_dmg;
        hunter.result.extra_damage_from_crits += crit_dmg - effective_power;
        crit_dmg
    } else {
        effective_power
    };
    
    let total_damage = base_damage;
    
    // Apply damage to enemy (returns mitigated damage)
    let actual_damage = enemy.take_damage(total_damage);
    hunter.result.damage += actual_damage;
    
    // Lifesteal (Borge) - based on attack damage, NOT mitigated damage
    if hunter.lifesteal > 0.0 {
        let heal_amount = total_damage * hunter.lifesteal;
        let effective_heal = heal_amount.min(hunter.max_hp - hunter.hp);
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.lifesteal += effective_heal;
    }
    
    // Get effective effect_chance for this enemy (Atlas Protocol bonus on bosses)
    let effective_effect_chance = hunter.get_effective_effect_chance(enemy.is_boss);
    
    // Life of the Hunt (Borge) - effect chance to heal 6% of damage dealt per level
    // Python: LotH_healing = damage * LotH * 0.06 (uses attack damage, not mitigated)
    if hunter.life_of_the_hunt > 0 && rng.gen::<f64>() < effective_effect_chance {
        let heal_amount = total_damage * hunter.life_of_the_hunt as f64 * 0.06;
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.life_of_the_hunt_healing += heal_amount;
        hunter.result.effect_procs += 1;
    }
    
    // Impeccable Impacts stun (Borge) - effect chance to stun
    // Python: queues a 'stun' event at priority 0, processed immediately
    // We return should_queue_stun and duration, caller will queue it
    if hunter.impeccable_impacts > 0 && rng.gen::<f64>() < effective_effect_chance {
        hunter.result.effect_procs += 1;
        let stun_effect = if enemy.is_boss { 0.5 } else { 1.0 };
        let stun_duration = hunter.impeccable_impacts as f64 * 0.1 * stun_effect;
        hunter.pending_stun_duration = stun_duration;  // Signal to queue stun event
        hunter.result.stun_duration_inflicted += stun_duration;
    }
    
    // Fires of War (Borge) - effect chance to double attack speed for 0.1s per level
    // Python: self.fires_of_war = self.talents["fires_of_war"] * 0.1
    if hunter.fires_of_war > 0 && rng.gen::<f64>() < effective_effect_chance {
        hunter.fires_of_war_buff = hunter.fires_of_war as f64 * 0.1;
        hunter.result.effect_procs += 1;
    }
    
    // TRAMPLE (Borge mod) - kill multiple enemies if damage > enemy max HP
    // Python: kills current_target first, then up to trample_power MORE from alive list
    // So total kills = 1 + min(trample_power, remaining_alive) = trample_power + 1 when enemies available
    if hunter.has_trample && !enemy.is_boss && total_damage > enemy.max_hp {
        let trample_power = ((total_damage / enemy.max_hp) as usize).min(10);
        if trample_power > 1 {
            // Kill the current target immediately (Python does current_target.kill() first)
            enemy.hp = 0.0;
            
            // Python kills UP TO trample_power additional enemies (not trample_power - 1)
            // Bug in Python: for i in alive_index[:trample_power] kills trample_power MORE
            // So total = 1 (current) + trample_power (additional) = trample_power + 1
            let additional_kills = trample_power.min(remaining_enemies);
            hunter.result.trample_kills += (additional_kills + 1) as i32; // +1 for current target
            return additional_kills;
        }
    }
    
    0  // No trample kills
}

/// Ozzy-specific attack logic (WASM-verified)
/// - NO traditional crits
/// - special_chance triggers Multistrike (extra attack)
/// - Crippling Shots: HP% damage from accumulated stacks
/// - Omen of Decay: damage MULTIPLIER (procs on 50% effect chance)
fn ozzy_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng, effective_power: f64, _current_time: f64) {
    // Base damage is just power (no crit)
    let base_damage = effective_power;
    
    // Crippling Shots - consume stacks for HP% damage
    let cripple_damage = if hunter.decay_stacks > 0 {
        let hp_pct = hunter.decay_stacks as f64 * 0.008;
        let bonus = enemy.hp * hp_pct;
        let boss_reduction = if enemy.is_boss { 0.1 } else { 1.0 };
        hunter.decay_stacks = 0;
        bonus * boss_reduction
    } else {
        0.0
    };
    
    // Omen of Decay - damage MULTIPLIER, procs on 50% effect chance
    let omen_multiplier = if hunter.omen_of_decay > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
        hunter.result.effect_procs += 1;
        1.0 + hunter.omen_of_decay as f64 * 0.03
    } else {
        1.0
    };
    
    // Final damage = (base + cripple) * omen
    let total_damage = (base_damage + cripple_damage) * omen_multiplier;
    let actual_damage = enemy.take_damage(total_damage);
    hunter.result.damage += actual_damage;
    
    // Base attack lifesteal (Ozzy) - based on BASE damage only, not cripple/omen extra
    // Python: lifesteal_amount = damage * self.lifesteal (where damage is base power)
    if hunter.lifesteal > 0.0 {
        let mut heal_amount = base_damage * hunter.lifesteal;
        // Soul of Snek buff: +15% lifesteal per level during empowered_regen
        if hunter.empowered_regen > 0 {
            heal_amount *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
        }
        // Track effective healing only (like Python does)
        let effective_heal = heal_amount.min(hunter.max_hp - hunter.hp);
        hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
        hunter.result.lifesteal += effective_heal;
    }
    
    // Multistrike (triggered by special_chance, NOT a crit)
    // Python: MS is queued as a separate event and only executes if enemy is still alive
    // So we must check enemy.is_dead() BEFORE executing MS
    if !enemy.is_dead() && rng.gen::<f64>() < hunter.special_chance {
        hunter.result.multistrikes += 1;
        // MS damage = power * special_damage (which is the MS multiplier for Ozzy)
        let ms_damage = effective_power * hunter.special_damage;
        
        // MS can also trigger omen
        let ms_omen = if hunter.omen_of_decay > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
            hunter.result.effect_procs += 1;
            1.0 + hunter.omen_of_decay as f64 * 0.03
        } else {
            1.0
        };
        
        let ms_total = ms_damage * ms_omen;
        let ms_actual = enemy.take_damage(ms_total);
        hunter.result.damage += ms_actual;
        hunter.result.extra_damage_from_ms += ms_actual;
        
        // Python: MS is a separate attack() call, which applies lifesteal on MS damage
        if hunter.lifesteal > 0.0 {
            let mut heal_amount = ms_damage * hunter.lifesteal;
            if hunter.empowered_regen > 0 {
                heal_amount *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
            }
            let effective_heal = heal_amount.min(hunter.max_hp - hunter.hp);
            hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
            hunter.result.lifesteal += effective_heal;
        }
    }
    
    // Echo Bullets (50% effect chance to trigger)
    // Python: Echo is queued as a separate event and only executes if enemy is still alive
    if !enemy.is_dead() && hunter.echo_bullets > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
        hunter.result.echo_bullets += 1;
        let echo_dmg = hunter.power * hunter.echo_bullets as f64 * 0.05;
        
        // Echo can trigger omen
        let echo_omen = if hunter.omen_of_decay > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
            hunter.result.effect_procs += 1;
            1.0 + hunter.omen_of_decay as f64 * 0.03
        } else {
            1.0
        };
        
        let echo_total = echo_dmg * echo_omen;
        let echo_actual = enemy.take_damage(echo_total);
        hunter.result.damage += echo_actual;
        // NOTE: Echo CANNOT trigger multistrike (WASM: a=1 skips triggers)
        
        // Python: Echo is a separate attack() call, which applies lifesteal on Echo damage
        if hunter.lifesteal > 0.0 {
            let mut heal_amount = echo_dmg * hunter.lifesteal;
            if hunter.empowered_regen > 0 {
                heal_amount *= 1.0 + hunter.soul_of_snek as f64 * 0.15;
            }
            let effective_heal = heal_amount.min(hunter.max_hp - hunter.hp);
            hunter.hp = (hunter.hp + heal_amount).min(hunter.max_hp);
            hunter.result.lifesteal += effective_heal;
        }
    }
    
    // Thousand Needles stun (effect chance)
    // Python: Stun delays the enemy's next scheduled attack by the stun duration
    // Rather than blocking attacks during a window, we add to pending_stun_delay
    // which gets applied when the enemy attack event fires
    if hunter.thousand_needles > 0 && rng.gen::<f64>() < hunter.effect_chance {
        hunter.result.effect_procs += 1;
        let stun_duration = hunter.thousand_needles as f64 * 0.05;
        let actual_stun = if enemy.is_boss { stun_duration * 0.5 } else { stun_duration };
        enemy.pending_stun_delay += actual_stun;  // Accumulate stun delay
        hunter.result.stun_duration_inflicted += actual_stun;
    }
    
    // Crippling Shots - add stacks for NEXT attack
    if hunter.crippling_shots > 0 && rng.gen::<f64>() < hunter.effect_chance {
        hunter.decay_stacks += hunter.crippling_shots;
        hunter.decay_stacks = hunter.decay_stacks.min(100);
        hunter.result.effect_procs += 1;
    }
    
    // Trickster's Boon (50% effect chance to gain trickster charge)
    if hunter.tricksters_boon > 0 && rng.gen::<f64>() < hunter.effect_chance / 2.0 {
        hunter.trickster_charges += 1;
        hunter.result.effect_procs += 1;
    }
    
    // Python: Ozzy's attack() also calls on_kill() if target dies from THIS attack
    // This is IN ADDITION to the on_kill call from the main sim loop
    // This gives Ozzy double UA procs compared to Borge
    if enemy.is_dead() {
        on_kill(hunter, rng, enemy.is_boss);
    }
}

/// Enemy attacks hunter
fn enemy_attack(hunter: &mut Hunter, enemy: &mut Enemy, rng: &mut impl Rng) {
    // Track total incoming enemy attacks
    hunter.result.enemy_attacks += 1;
    
    // Check if boss is at max enrage (> 200 stacks) - disables evades for Ozzy
    let max_enrage_active = enemy.is_boss && enemy.max_enrage;
    
    // Check for trickster evade (Ozzy) - consume a charge for free evade
    // WASM: Disabled at max enrage
    if !max_enrage_active && hunter.trickster_charges > 0 {
        hunter.trickster_charges -= 1;
        hunter.result.trickster_evades += 1;
        return;
    }
    
    // Check for evade - WASM: Disabled at max enrage
    if !max_enrage_active && rng.gen::<f64>() < hunter.evade_chance {
        hunter.result.evades += 1;
        return;
    }
    
    // Check for block (Knox) - block reduces damage by 50%, doesn't prevent it entirely!
    let mut blocked = false;
    if hunter.block_chance > 0.0 && rng.gen::<f64>() < hunter.block_chance {
        // Blocked - track it (but still apply reduced damage below)
        hunter.result.evades += 1;  // Track blocks via evades counter
        blocked = true;
        
        // Fortification Elixir (Knox) - +10% regen for 5 ticks after block
        if hunter.fortification_elixir > 0 {
            hunter.empowered_block_regen += 5;
        }
    }
    
    // Get enemy damage
    let (mut damage, is_crit) = enemy.get_attack_damage(rng);
    
    // Apply block damage reduction (50%) - Knox
    // Python: blocked_amount = damage * 0.5; damage = damage - blocked_amount
    if blocked {
        damage *= 0.5;
    }
    
    // Dance of Dashes (Ozzy) - WASM: triggers when TAKING a crit, not on evade
    // 15% chance per level to gain trickster charge when hit by crit
    // Still works at max enrage!
    if is_crit && hunter.dance_of_dashes > 0 && rng.gen::<f64>() < hunter.dance_of_dashes as f64 * 0.15 {
        hunter.trickster_charges += 1;
        hunter.result.effect_procs += 1;
    }
    
    // Apply scarab DR FIRST (Ozzy) - this is multiplicative, applied BEFORE normal DR
    // WASM: damage = damage * (1 - scarab_dr)
    if hunter.scarab_dr > 0.0 {
        damage *= 1.0 - hunter.scarab_dr;
    }
    
    // Apply minotaur DR (Borge) - multiplicative, applied BEFORE weakspot and normal DR
    // Python order: minotaur → weakspot → base DR
    if hunter.minotaur_dr > 0.0 {
        damage *= 1.0 - hunter.minotaur_dr;
    }
    
    // Weakspot Analysis (Borge) - reduce crit damage taken by 11% per level (AFTER minotaur)
    if is_crit && hunter.weakspot_analysis > 0 {
        let crit_reduction = hunter.weakspot_analysis as f64 * 0.11;
        damage *= 1.0 - crit_reduction.min(0.99);  // Cap at 99% reduction
    }
    
    // Calculate effective DR (base + deal_with_death per revive used + atlas_protocol on bosses)
    let mut effective_dr = hunter.damage_reduction;
    if hunter.deal_with_death > 0 && hunter.revive_count > 0 {
        effective_dr += hunter.deal_with_death as f64 * 0.016 * hunter.revive_count as f64;
    }
    
    // Atlas Protocol (Borge) - +0.7% DR per level on bosses
    // Python: (self._damage_reduction + self.attributes["atlas_protocol"] * 0.007) on boss stages
    if enemy.is_boss && hunter.atlas_protocol > 0 {
        effective_dr += hunter.atlas_protocol as f64 * 0.007;
    }
    
    // Apply damage reduction (additive DR pool)
    let mitigated = damage * effective_dr.min(0.95);  // Cap DR at 95%
    let actual_damage = damage - mitigated;
    
    hunter.result.mitigated_damage += mitigated;
    hunter.result.damage_taken += actual_damage;
    hunter.hp -= actual_damage;
    
    // Helltouch Barrier (Borge) - REFLECTS damage to enemy
    // Python: reflected_damage = final_damage * helltouch_barrier * 0.08 * helltouch_effect
    // where helltouch_effect = 0.1 on bosses, 1.0 otherwise
    if hunter.helltouch_barrier_level > 0 && actual_damage > 0.0 && !hunter.is_dead() {
        let helltouch_effect = if enemy.is_boss { 0.1 } else { 1.0 };
        let reflected_damage = actual_damage * hunter.helltouch_barrier_level as f64 * 0.08 * helltouch_effect;
        enemy.take_damage(reflected_damage);
        hunter.result.helltouch_barrier += reflected_damage;
    }
    
    // Boss enrage: +1 stack after each primary attack (Python: units.py Boss.attack())
    // This makes bosses attack faster over time
    if enemy.is_boss {
        enemy.add_enrage();
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
