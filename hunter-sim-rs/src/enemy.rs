//! Enemy and Boss implementations - Updated to match CIFI Tools formulas

use crate::config::HunterType;
use crate::simulation::FastRng;

/// A regular enemy in combat
/// Secondary attack type for bosses
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SecondaryAttackType {
    None,
    Gothmorgor,  // Borge boss: deals damage + adds enrage
    Exoscarab,   // Ozzy boss: triggers harden (95% DR, 3x regen for 5 ticks, +5 enrage at end)
}

#[derive(Debug, Clone)]
pub struct Enemy {
    pub name: String,
    pub hp: f64,
    pub max_hp: f64,
    pub power: f64,
    pub base_power: f64,  // Store base power for enrage calculations
    pub regen: f64,
    pub damage_reduction: f64,
    pub base_dr: f64,  // Store base DR for harden mechanic
    pub evade_chance: f64,
    pub effect_chance: f64,  // Added: enemy effect chance (starts at stage 300)
    pub special_chance: f64,
    pub special_damage: f64,
    pub speed: f64,
    pub base_speed: f64,  // Store base speed for enrage calculations
    pub is_boss: bool,
    pub is_stunned: bool,
    pub stun_end_time: f64,
    pub stun_duration: f64,  // Store the stun duration for proper rescheduling
    pub pending_stun_delay: f64,  // Accumulated stun time to add to next enemy attack
    // Boss-specific
    pub enrage_stacks: i32,
    pub max_enrage: bool,  // True when stacks > 200 (3x power, 100% crit)
    pub has_secondary: bool,
    pub secondary_type: SecondaryAttackType,
    pub speed2: f64,
    pub base_speed2: f64,
    // Exoscarab harden mechanic
    pub harden_ticks_left: i32,
}

impl Enemy {
    /// Python's multi_wasm scaling function - WASM-verified additive breakpoints
    /// This MUST match Python's units.py multi_wasm exactly!
    fn multi_wasm(stage: i32) -> f64 {
        let s = stage as f64;
        
        // WASM formula from multiWasm function (lines 1304-1320 in release.dcmp)
        // Uses ADDITIVE scaling with many breakpoints
        let mut result = 1.0;
        result += 0.0_f64.max((s - 149.0) * 0.006);
        result += 0.0_f64.max((s - 199.0) * 0.006);
        result += 0.0_f64.max((s - 249.0) * 0.006);
        result += 0.0_f64.max((s - 299.0) * 0.006);
        result += 0.0_f64.max((s - 309.0) * 0.003);
        result += 0.0_f64.max((s - 319.0) * 0.003);
        result += 0.0_f64.max((s - 329.0) * 0.004);
        result += 0.0_f64.max((s - 339.0) * 0.004);
        result += 0.0_f64.max((s - 349.0) * 0.005);
        result += 0.0_f64.max((s - 359.0) * 0.005);
        result += 0.0_f64.max((s - 369.0) * 0.006);
        result += 0.0_f64.max((s - 379.0) * 0.006);
        result += 0.0_f64.max((s - 389.0) * 0.007);
        
        // WASM applies max(result, 1.0) then multiplies by exponential
        result = result.max(1.0);
        result *= 1.01_f64.powi((stage - 350).max(0));
        
        result
    }
    
    /// CIFI stage scaling function for Knox (f_o)
    /// Knox uses MULTIPLICATIVE scaling unlike Borge/Ozzy's additive multi_wasm.
    /// Below stage 150, returns 1.0 (no scaling).
    fn knox_scaling(stage: i32) -> f64 {
        if stage < 150 {
            return 1.0;
        }
        
        let s = stage as f64;
        let mut result = 1.0;
        
        // First breakpoint at 149
        if stage > 149 {
            result *= 1.0 + (s - 149.0) * 0.007;
        }
        
        // Additional breakpoints
        if stage > 199 {
            result *= 1.0 + (s - 199.0) * 0.007;
        }
        if stage > 249 {
            result *= 1.0 + (s - 249.0) * 0.007;
        }
        if stage > 299 {
            result *= 1.0 + (s - 299.0) * 0.007;
        }
        if stage > 349 {
            result *= 1.0 + (s - 349.0) * 0.007;
        }
        
        // Breakpoints every 20 stages after 360
        if stage > 369 {
            result *= 1.0 + (s - 369.0) * 0.007;
        }
        if stage > 389 {
            result *= 1.0 + (s - 389.0) * 0.007;
        }
        if stage > 409 {
            result *= 1.0 + (s - 409.0) * 0.007;
        }
        if stage > 429 {
            result *= 1.0 + (s - 429.0) * 0.007;
        }
        
        // Exponential scaling after stage 400
        if stage > 400 {
            result *= 1.01_f64.powi(stage - 400);
        }
        
        result
    }

    /// Create a regular enemy for a given stage - using CIFI formulas
    pub fn new(index: i32, stage: i32, hunter_type: HunterType) -> Self {
        let (hp, power, regen, special_chance, special_damage, dr, evade_chance, effect_chance, speed) = 
            Self::calculate_stats_cifi(stage, hunter_type, false);
        
        Self {
            name: format!("E{:>3}{:>3}", stage, index),
            hp,
            max_hp: hp,
            power,
            base_power: power,
            regen,
            damage_reduction: dr,
            base_dr: dr,
            evade_chance,
            effect_chance,
            special_chance: special_chance.min(0.25),  // Cap at 25%
            special_damage: special_damage.min(2.5),   // Cap at 250%
            speed,
            base_speed: speed,
            is_boss: false,
            is_stunned: false,
            stun_end_time: 0.0,
            stun_duration: 0.0,
            pending_stun_delay: 0.0,
            enrage_stacks: 0,
            max_enrage: false,
            has_secondary: false,
            secondary_type: SecondaryAttackType::None,
            speed2: 0.0,
            base_speed2: 0.0,
            harden_ticks_left: 0,
        }
    }
    
    /// Create a boss for a given stage - using CIFI formulas
    pub fn new_boss(stage: i32, hunter_type: HunterType) -> Self {
        let (hp, power, regen, special_chance, special_damage, dr, evade_chance, effect_chance, speed) = 
            Self::calculate_stats_cifi(stage, hunter_type, true);
        
        // Calculate speed2 and secondary type based on hunter type
        // Ozzy Exoscarab: 60 second cooldown (fixed), no speed reduction from enrage
        // Borge Gothmorgor: speed2 = base_speed * 2.1 * 1.8, reduced by enrage
        let (speed2, secondary_type) = if stage >= 200 {
            match hunter_type {
                HunterType::Ozzy => (60.0, SecondaryAttackType::Exoscarab),  // WASM: Fixed 60 second cooldown
                HunterType::Borge => (speed * 1.8, SecondaryAttackType::Gothmorgor),
                HunterType::Knox => (0.0, SecondaryAttackType::None),  // Knox doesn't have secondary
            }
        } else {
            (0.0, SecondaryAttackType::None)
        };
        
        Self {
            name: format!("B{:>3}", stage),
            hp,
            max_hp: hp,
            power,
            base_power: power,
            regen,
            damage_reduction: dr,
            base_dr: dr,
            evade_chance,
            effect_chance,
            special_chance: special_chance.min(0.30),
            special_damage: special_damage.min(5.0),
            speed,
            base_speed: speed,
            is_boss: true,
            is_stunned: false,
            stun_end_time: 0.0,
            stun_duration: 0.0,
            pending_stun_delay: 0.0,
            enrage_stacks: 0,
            max_enrage: false,
            has_secondary: stage >= 200 && hunter_type != HunterType::Knox,
            secondary_type,
            speed2,
            base_speed2: speed2,
            harden_ticks_left: 0,
        }
    }
    
    /// Calculate enemy stats using CIFI formulas extracted from WASM
    fn calculate_stats_cifi(stage: i32, hunter_type: HunterType, is_boss: bool) -> (f64, f64, f64, f64, f64, f64, f64, f64, f64) {
        // Returns: (hp, power, regen, special_chance, special_damage, dr, evade_chance, effect_chance, speed)
        let s = stage as f64;
        let d = ((stage - 1).max(0) as f64 / 100.0).floor() as i32;  // Boss cycles completed
        let d_f = d as f64;
        let is_stage_300 = stage == 300;
        
        match hunter_type {
            HunterType::Borge => {
                let f = Self::multi_wasm(stage);
                
                // Match Python Borge formulas (validated against WASM)
                // Borge uses FLAT 2.85 multiplier for stages > 100
                let tier_mult = if stage > 100 { 2.85 } else { 1.0 };
                
                // HP: (9 + stage * 4) * tier_mult * multi_wasm * boss(90x) * stage300(0.9)
                let hp = (s * 4.0 + 9.0) * f * tier_mult
                    * if is_boss { 90.0 } else { 1.0 }
                    * if is_stage_300 { 0.9 } else { 1.0 };
                
                // Power: (2.5 + stage * 0.7) * tier_mult * multi_wasm * boss(3.63x) * stage300(0.9)
                let power = (s * 0.7 + 2.5) * f * tier_mult
                    * if is_boss { 3.63 } else { 1.0 }
                    * if is_stage_300 { 0.9 } else { 1.0 };
                
                // Crit chance: 0.0322 + stage * 0.0004 + boss(0.04), capped at 0.25 (APK verified)
                let special_chance = (s * 0.0004 + 0.0322 + if is_boss { 0.04 } else { 0.0 }).min(0.25);
                
                // Crit damage: 1.212 + stage * 0.008 + boss(0.25), capped at 2.5 (APK verified)
                let special_damage = (s * 0.008 + 1.212 + if is_boss { 0.25 } else { 0.0 }).min(2.5);
                
                // Damage reduction (boss only): min(0.05 + stage * 0.0004, 0.25)
                let actual_dr = if is_boss { (0.05 + s * 0.0004).min(0.25) } else { 0.0 };
                
                // Evade: 0.004 if stage > 100
                let evade = if stage > 100 { 0.004 } else { 0.0 };
                
                // Effect chance (not used for Borge enemies in Python)
                let effect = 0.0;
                
                // Regen: (stage-1) * 0.08 * 1.052 (if stage > 100) * multi_wasm * boss(1.92x) (APK verified)
                let regen_tier = if stage > 100 { 1.052 } else { 1.0 };
                let regen = if stage > 1 { (s - 1.0) * 0.08 } else { 0.0 } * regen_tier * f
                    * if is_boss { 1.92 } else { 1.0 };
                
                // Speed: (4.53 - stage * 0.006) * boss(2.42x) (APK verified)
                let speed = (4.53 - s * 0.006) * if is_boss { 2.42 } else { 1.0 };
                
                (hp, power, regen, special_chance, special_damage, actual_dr, evade, effect, speed)
            }
            HunterType::Ozzy => {
                let f = Self::multi_wasm(stage);
                
                // Match Python Ozzy formulas (validated against WASM)
                // HP: (11 + stage * 6) * 2.9^tier * multi_wasm * boss(48x) * stage300(0.94)
                let hp = (s * 6.0 + 11.0) * f * 2.9_f64.powf(d_f)
                    * if is_boss { 48.0 } else { 1.0 }
                    * if is_stage_300 { 0.94 } else { 1.0 };
                
                // Power: (1.35 + stage * 0.75) * 2.7^tier * multi_wasm * boss(3x) * stage300(0.94)
                let power = (s * 0.75 + 1.35) * f * 2.7_f64.powf(d_f)
                    * if is_boss { 3.0 } else { 1.0 }
                    * if is_stage_300 { 0.94 } else { 1.0 };
                
                // Crit chance: 0.0994 + stage * 0.0006 + boss(0.13) (APK verified)
                let special_chance = (s * 0.0006 + 0.0994 + if is_boss { 0.13 } else { 0.0 }).min(0.25);
                
                // Crit damage: min(1.03 + stage * 0.008, 2.5)
                let special_damage = (s * 0.008 + 1.03).min(2.5);
                
                // Damage reduction (boss only from Python)
                let actual_dr = if is_boss { (0.05 + s * 0.0004).min(0.25) } else { 0.0 };
                
                // Evade: max((tier-1)*0.01+0.01, 0) if stage >= 100
                let evade = if stage >= 100 {
                    ((d_f - 1.0) * 0.01 + 0.01).max(0.0)
                } else { 0.0 };
                
                // Effect chance (not used in Python Ozzy enemies)
                let effect = 0.0;
                
                // Regen: (stage-1) * 0.1 * 1.25^tier * multi_wasm * boss(6x)
                let regen = if stage > 0 { (s - 1.0) * 0.1 } else { 0.0 }
                    * 1.25_f64.powf(d_f) * f
                    * if is_boss { 6.0 } else { 1.0 };
                
                // Speed: (3.20 - stage * 0.004) * boss(2.45)
                let speed = (3.2 - s * 0.004) * if is_boss { 2.45 } else { 1.0 };
                
                (hp, power, regen, special_chance, special_damage, actual_dr, evade, effect, speed)
            }
            HunterType::Knox => {
                let f = Self::knox_scaling(stage);
                
                // Match Python Knox formulas (validated against WASM)
                // HP: (10 + stage * 5) * 2.8 (if stage > 100) * knox_scaling * boss(120x)
                let post_100_mult = if stage > 100 { 2.8 } else { 1.0 };
                let hp = (10.0 + s * 5.0) * post_100_mult * f
                    * if is_boss { 120.0 } else { 1.0 };
                
                // Power: (1.5 + stage * 0.65) * 2.6 (if stage > 100) * knox_scaling * boss(4x)
                let power_100_mult = if stage > 100 { 2.6 } else { 1.0 };
                let power = (1.5 + s * 0.65) * power_100_mult * f
                    * if is_boss { 4.0 } else { 1.0 };
                
                // Crit chance: 0.075 + stage * 0.00055 + boss_bonus (APK verified: +13%)
                let special_chance = (s * 0.00055 + 0.075 + if is_boss { 0.13 } else { 0.0 }).min(0.25);
                
                // Crit damage: 1.15 + stage * 0.0075 + boss_bonus (APK verified: +0%)
                let special_damage = (s * 0.0075 + 1.15 + if is_boss { 0.0 } else { 0.0 }).min(2.5);
                
                // Damage reduction (boss only)
                let dr = if is_boss { 0.05 } else { 0.0 };
                let actual_dr = dr;
                
                // Evade: 0.006 if stage > 100, else 0
                let evade = if stage > 100 { 0.006 } else { 0.0 };
                
                // Effect chance: 0.03 + stage * 0.0003
                let effect = s * 0.0003 + 0.03;
                
                // Regen: (stage - 1) * 0.09 * 1.15 (if stage > 100) * knox_scaling * boss(2.0x) (APK verified)
                let regen_100_mult = if stage > 100 { 1.15 } else { 1.0 };
                let regen = if stage > 0 { (s - 1.0) * 0.09 } else { 0.0 } * regen_100_mult * f
                    * if is_boss { 2.0 } else { 1.0 };
                
                // Speed: (3.80 - stage * 0.005) * boss(2.85x) (APK verified)
                let speed = (3.80 - s * 0.005) * if is_boss { 2.85 } else { 1.0 };
                
                (hp, power, regen, special_chance, special_damage, actual_dr, evade, effect, speed)
            }
        }
    }
    
    /// Check if enemy is dead
    pub fn is_dead(&self) -> bool {
        self.hp <= 0.0
    }
    
    /// Apply damage to the enemy
    pub fn take_damage(&mut self, damage: f64) -> f64 {
        let actual = damage * (1.0 - self.damage_reduction);
        self.hp -= actual;
        actual
    }
    
    /// Apply regeneration - also handles harden mechanic for Exoscarab
    pub fn regen_hp(&mut self) {
        if self.hp < self.max_hp && self.hp > 0.0 {
            if self.harden_ticks_left > 0 {
                // Harden effect: 3x regen for 5 ticks
                self.hp = (self.hp + self.regen * 3.0).min(self.max_hp);
                self.harden_ticks_left -= 1;
                if self.harden_ticks_left == 0 {
                    // Harden ends: +5 enrage stacks and restore DR
                    self.end_harden();
                }
            } else {
                self.hp = (self.hp + self.regen).min(self.max_hp);
            }
        }
    }
    
    /// Start harden effect (Exoscarab boss)
    pub fn start_harden(&mut self) {
        self.harden_ticks_left = 5;
        self.damage_reduction = 0.95;  // 95% DR during harden
    }
    
    /// End harden effect (Exoscarab boss)
    pub fn end_harden(&mut self) {
        self.damage_reduction = self.base_dr;  // Restore original DR
        // WASM: +5 enrage stacks added when harden ends
        for _ in 0..5 {
            self.add_enrage();
        }
    }
    
    /// Get attack damage with possible crit - CIFI enrage mechanics
    pub fn get_attack_damage(&self, rng: &mut FastRng) -> (f64, bool) {
        // At 200+ enrage stacks, damage is tripled and always crits
        let power = if self.enrage_stacks > 200 {
            self.base_power * 3.0
        } else {
            self.base_power
        };
        
        let crit_chance = if self.enrage_stacks > 200 {
            1.0  // Always crit at max enrage
        } else {
            self.special_chance
        };
        
        if rng.f64() < crit_chance {
            (power * self.special_damage, true)
        } else {
            (power, false)
        }
    }
    
    /// Add enrage stack (boss only) - CIFI mechanics
    /// Enrage reduces attack speed until 200 stacks, then 3x power + 100% crit
    pub fn add_enrage(&mut self) {
        if self.is_boss {
            self.enrage_stacks += 1;
            
            // Speed reduction: speed = base_speed - (stacks * base_speed / 200), min 0.5
            self.speed = (self.base_speed - self.enrage_stacks as f64 * self.base_speed / 200.0).max(0.5);
            
            // Also reduce secondary attack speed
            if self.has_secondary && self.base_speed2 > 0.0 {
                self.speed2 = (self.base_speed2 - self.enrage_stacks as f64 * self.base_speed2 / 200.0).max(0.5);
            }
            
            // WASM: Max enrage triggers when stacks > 200 (not >= 200)
            // At max enrage: 3x base power, 100% crit chance
            if self.enrage_stacks > 200 && !self.max_enrage {
                self.max_enrage = true;
                self.power = self.base_power * 3.0;  // CIFI: 3x base power at max enrage
                self.special_chance = 1.0;  // CIFI: 100% crit at max enrage
            }
        }
    }
    
    /// Get current attack speed (accounting for enrage)
    pub fn get_speed(&self) -> f64 {
        self.speed
    }
    
    /// Get current secondary attack speed (accounting for enrage)
    pub fn get_speed2(&self) -> f64 {
        self.speed2
    }
}
