//! Simulation result statistics

use serde::{Deserialize, Serialize};

/// Results from a single simulation run
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SimResult {
    pub final_stage: i32,
    pub elapsed_time: f64,
    pub kills: i32,
    pub damage: f64,
    pub damage_taken: f64,
    pub total_loot: f64,
    // Per-resource loot (WASM formulas)
    pub loot_common: f64,    // Mat1 - Obsidian
    pub loot_uncommon: f64,  // Mat2 - Behlium
    pub loot_rare: f64,      // Mat3 - Hellish-Biomatter
    pub total_xp: f64,       // XP earned
    pub attacks: i32,
    pub crits: i32,
    pub extra_damage_from_crits: f64,
    pub multistrikes: i32,
    pub extra_damage_from_ms: f64,
    pub evades: i32,
    pub enemy_attacks: i32,  // Total incoming enemy attacks
    pub regenerated_hp: f64,
    pub lifesteal: f64,
    pub mitigated_damage: f64,
    pub effect_procs: i32,
    pub stun_duration_inflicted: f64,
    // Hunter-specific stats
    pub helltouch_barrier: f64,
    pub helltouch_kills: i32,
    pub trample_kills: i32,
    pub medusa_kills: i32,
    pub trickster_evades: i32,
    pub echo_bullets: i32,
    pub unfair_advantage_healing: f64,
    pub life_of_the_hunt_healing: f64,
    // Debug stats
    pub on_kill_calls: i32,
}

/// Aggregated statistics from multiple simulation runs
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AggregatedStats {
    pub runs: i32,
    pub avg_stage: f64,
    pub std_stage: f64,
    pub min_stage: i32,
    pub max_stage: i32,
    pub avg_time: f64,
    pub avg_loot: f64,
    pub avg_loot_per_hour: f64,
    pub avg_damage: f64,
    pub avg_damage_taken: f64,
    pub avg_mitigated: f64,
    pub avg_lifesteal: f64,
    pub avg_attacks: f64,
    pub avg_crits: f64,
    pub avg_kills: f64,
    pub avg_evades: f64,
    pub avg_trickster_evades: f64,  // Trickster evades (Ozzy)
    pub avg_enemy_attacks: f64,  // Total incoming enemy attacks
    pub avg_effect_procs: f64,
    pub avg_stun_duration: f64,
    pub avg_trample_kills: f64,
    pub avg_loth_healing: f64,
    pub avg_ua_healing: f64,
    pub avg_regen: f64,
    pub survival_rate: f64,  // Legacy: % of runs that didn't die exactly at a boss stage
    // Boss milestone survival rates - % of runs that PASSED each boss
    pub boss1_survival: f64,  // % that reached stage > 100
    pub boss2_survival: f64,  // % that reached stage > 200
    pub boss3_survival: f64,  // % that reached stage > 300
    pub boss4_survival: f64,  // % that reached stage > 400
    pub boss5_survival: f64,  // % that reached stage > 500
    // Per-resource loot averages (WASM formulas)
    pub avg_loot_common: f64,
    pub avg_loot_uncommon: f64,
    pub avg_loot_rare: f64,
    pub avg_xp: f64,
    pub avg_on_kill_calls: f64,  // DEBUG: on_kill calls per run
}

impl AggregatedStats {
    /// Create aggregated stats from a list of simulation results
    pub fn from_results(results: &[SimResult]) -> Self {
        if results.is_empty() {
            return Self::default();
        }
        
        let n = results.len() as f64;
        let stages: Vec<i32> = results.iter().map(|r| r.final_stage).collect();
        let times: Vec<f64> = results.iter().map(|r| r.elapsed_time).collect();
        let loots: Vec<f64> = results.iter().map(|r| r.total_loot).collect();
        
        // Calculate average stage
        let avg_stage = stages.iter().sum::<i32>() as f64 / n;
        
        // Calculate standard deviation of stages
        let variance = stages.iter()
            .map(|&s| (s as f64 - avg_stage).powi(2))
            .sum::<f64>() / n;
        let std_stage = variance.sqrt();
        
        let loot_per_hours: Vec<f64> = results
            .iter()
            .map(|r| {
                if r.elapsed_time > 0.0 {
                    r.total_loot / (r.elapsed_time / 3600.0)
                } else {
                    0.0
                }
            })
            .collect();
        
        // Count boss deaths (died at stage ending in 00) - legacy metric
        let boss_deaths = stages.iter().filter(|&&s| s % 100 == 0 && s > 0).count();
        
        // Boss milestone survival - % of runs that PASSED each boss
        let boss1_passed = stages.iter().filter(|&&s| s > 100).count();
        let boss2_passed = stages.iter().filter(|&&s| s > 200).count();
        let boss3_passed = stages.iter().filter(|&&s| s > 300).count();
        let boss4_passed = stages.iter().filter(|&&s| s > 400).count();
        let boss5_passed = stages.iter().filter(|&&s| s > 500).count();
        
        Self {
            runs: results.len() as i32,
            avg_stage,
            std_stage,
            min_stage: *stages.iter().min().unwrap_or(&0),
            max_stage: *stages.iter().max().unwrap_or(&0),
            avg_time: times.iter().sum::<f64>() / n,
            avg_loot: loots.iter().sum::<f64>() / n,
            avg_loot_per_hour: loot_per_hours.iter().sum::<f64>() / n,
            avg_damage: results.iter().map(|r| r.damage).sum::<f64>() / n,
            avg_damage_taken: results.iter().map(|r| r.damage_taken).sum::<f64>() / n,
            avg_mitigated: results.iter().map(|r| r.mitigated_damage).sum::<f64>() / n,
            avg_lifesteal: results.iter().map(|r| r.lifesteal).sum::<f64>() / n,
            avg_attacks: results.iter().map(|r| r.attacks as f64).sum::<f64>() / n,
            avg_crits: results.iter().map(|r| r.crits as f64).sum::<f64>() / n,
            avg_kills: results.iter().map(|r| r.kills as f64).sum::<f64>() / n,
            avg_evades: results.iter().map(|r| r.evades as f64).sum::<f64>() / n,
            avg_trickster_evades: results.iter().map(|r| r.trickster_evades as f64).sum::<f64>() / n,
            avg_enemy_attacks: results.iter().map(|r| r.enemy_attacks as f64).sum::<f64>() / n,
            avg_effect_procs: results.iter().map(|r| r.effect_procs as f64).sum::<f64>() / n,
            avg_stun_duration: results.iter().map(|r| r.stun_duration_inflicted).sum::<f64>() / n,
            avg_trample_kills: results.iter().map(|r| r.trample_kills as f64).sum::<f64>() / n,
            avg_loth_healing: results.iter().map(|r| r.life_of_the_hunt_healing).sum::<f64>() / n,
            avg_ua_healing: results.iter().map(|r| r.unfair_advantage_healing).sum::<f64>() / n,
            avg_regen: results.iter().map(|r| r.regenerated_hp).sum::<f64>() / n,
            survival_rate: 1.0 - (boss_deaths as f64 / n),
            boss1_survival: boss1_passed as f64 / n,
            boss2_survival: boss2_passed as f64 / n,
            boss3_survival: boss3_passed as f64 / n,
            boss4_survival: boss4_passed as f64 / n,
            boss5_survival: boss5_passed as f64 / n,
            avg_loot_common: results.iter().map(|r| r.loot_common).sum::<f64>() / n,
            avg_loot_uncommon: results.iter().map(|r| r.loot_uncommon).sum::<f64>() / n,
            avg_loot_rare: results.iter().map(|r| r.loot_rare).sum::<f64>() / n,
            avg_xp: results.iter().map(|r| r.total_xp).sum::<f64>() / n,
            avg_on_kill_calls: results.iter().map(|r| r.on_kill_calls as f64).sum::<f64>() / n,
        }
    }
}
