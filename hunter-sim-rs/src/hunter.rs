//! Hunter implementation with stat calculations for all three hunters

use crate::config::{BuildConfig, HunterType};
use crate::stats::SimResult;

/// Computed hunter stats ready for combat simulation
#[derive(Debug, Clone)]
pub struct Hunter {
    pub hunter_type: HunterType,
    pub level: i32,
    
    // Core stats
    pub max_hp: f64,
    pub hp: f64,
    pub power: f64,
    pub regen: f64,
    pub damage_reduction: f64,
    pub evade_chance: f64,
    pub effect_chance: f64,
    pub special_chance: f64,
    pub special_damage: f64,
    pub speed: f64,
    pub lifesteal: f64,
    
    // Knox-specific
    pub block_chance: f64,
    pub charge: f64,
    pub charge_chance: f64,
    pub charge_gained: f64,
    pub salvo_projectiles: i32,
    
    // Talent values (for combat mechanics)
    pub death_is_my_companion: i32,
    pub life_of_the_hunt: i32,
    pub unfair_advantage: i32,
    pub omen_of_defeat: i32,
    pub presence_of_god: i32,
    pub fires_of_war: i32,
    
    // Ozzy talents
    pub multistriker: i32,
    pub echo_location: i32,
    pub tricksters_boon: i32,
    pub crippling_shots: i32,
    pub omen_of_decay: i32,
    pub echo_bullets: i32,
    pub thousand_needles: i32,
    
    // Ozzy attributes
    pub dance_of_dashes: i32,
    pub vectid_elixir: i32,
    
    // Ozzy runtime state
    pub trickster_charges: i32,
    pub empowered_regen: i32,
    
    // Knox talents
    pub calypsos_advantage: i32,
    pub ghost_bullets: i32,
    pub finishing_move: i32,
    
    // Attribute values
    pub helltouch_barrier_level: i32,
    pub atlas_protocol: i32,
    pub born_for_battle: i32,
    
    // Borge attributes (missing combat effects)
    pub lifedrain_inhalers: i32,
    pub weakspot_analysis: i32,
    pub soul_of_athena: i32,
    pub soul_of_hermes: i32,
    pub soul_of_the_minotaur: i32,
    
    // Ozzy attributes (missing combat effects)  
    pub soul_of_snek: i32,
    pub cycle_of_death: i32,
    pub gift_of_medusa: i32,
    pub deal_with_death: i32,
    
    // Knox attributes (missing combat effects)
    pub space_pirate_armory: i32,
    pub soul_amplification: i32,
    pub fortification_elixir: i32,
    pub empowered_block_regen: i32,  // Counter for regen buff after block
    
    // Mod flags
    pub has_trample: bool,
    pub has_decay: bool,
    
    // Loot and XP multipliers
    pub loot_mult: f64,
    pub xp_mult: f64,
    
    // Combat tracking
    pub result: SimResult,
    pub current_stage: i32,
    pub revive_count: i32,
    pub max_revives: i32,
    pub hundred_souls_stacks: i32,  // Knox
    pub decay_stacks: i32,  // Ozzy crippling shots
}

impl Hunter {
    /// Create a hunter from a build configuration
    pub fn from_config(config: &BuildConfig) -> Self {
        match config.get_hunter_type() {
            HunterType::Borge => Self::create_borge(config),
            HunterType::Ozzy => Self::create_ozzy(config),
            HunterType::Knox => Self::create_knox(config),
        }
    }
    
    fn create_borge(c: &BuildConfig) -> Self {
        let level = c.get_level();
        
        // Get attribute values for calculations
        let soul_of_hermes = c.get_attr("soul_of_hermes");
        let soul_of_the_minotaur = c.get_attr("soul_of_the_minotaur");
        
        // HP calculation
        let hp_stat = c.get_stat("hp") as f64;
        let max_hp = (43.0 
            + hp_stat * (2.50 + 0.01 * (hp_stat / 5.0).floor())
            + c.get_inscr("i3") as f64 * 6.0
            + c.get_inscr("i27") as f64 * 24.0)
            * (1.0 + c.get_attr("soul_of_ares") as f64 * 0.01)
            * (1.0 + c.get_inscr("i60") as f64 * 0.03)
            * (1.0 + c.get_relic("disk_of_dawn") as f64 * 0.03)
            * (1.0 + (0.015 * (level - 39) as f64) * c.get_gem("creation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64)
            * (1.0 + 0.2 * c.get_gem("creation_node_#1") as f64);
        
        // Power calculation - includes soul_of_the_minotaur (+1% power per level)
        let pwr_stat = c.get_stat("power") as f64;
        let power = (3.0 
            + pwr_stat * (0.5 + 0.01 * (pwr_stat / 10.0).floor())
            + c.get_inscr("i13") as f64 * 1.0
            + c.get_talent("impeccable_impacts") as f64 * 2.0)
            * (1.0 + c.get_attr("soul_of_ares") as f64 * 0.002)
            * (1.0 + soul_of_the_minotaur as f64 * 0.01)  // +1% power per level
            * (1.0 + c.get_inscr("i60") as f64 * 0.03)
            * (1.0 + c.get_relic("long_range_artillery_crawler") as f64 * 0.03)
            * (1.0 + (0.01 * (level - 39) as f64) * c.get_gem("creation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64)
            * (1.0 + 0.03 * c.get_gem("innovation_node_#3") as f64);
        
        // Regen calculation
        let reg_stat = c.get_stat("regen") as f64;
        let regen = (0.02 
            + reg_stat * (0.03 + 0.01 * (reg_stat / 30.0).floor())
            + c.get_attr("essence_of_ylith") as f64 * 0.04)
            * (1.0 + c.get_attr("essence_of_ylith") as f64 * 0.009)
            * (1.0 + (0.005 * (level - 39) as f64) * c.get_gem("creation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Damage reduction - includes soul_of_the_minotaur (+1% unique DR per level)
        let damage_reduction = (c.get_stat("damage_reduction") as f64 * 0.0144
            + c.get_attr("spartan_lineage") as f64 * 0.015
            + soul_of_the_minotaur as f64 * 0.01  // +1% unique DR per level
            + c.get_inscr("i24") as f64 * 0.004)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Evade chance
        let evade_chance = 0.01 
            + c.get_stat("evade_chance") as f64 * 0.0034
            + c.get_attr("superior_sensors") as f64 * 0.016;
        
        // Effect chance - includes soul_of_hermes (+0.4% per level)
        let effect_chance = (0.04 
            + c.get_stat("effect_chance") as f64 * 0.005
            + c.get_attr("superior_sensors") as f64 * 0.012
            + soul_of_hermes as f64 * 0.004  // +0.4% effect chance per level
            + c.get_inscr("i11") as f64 * 0.02
            + 0.03 * c.get_gem("innovation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Special (crit) chance - includes soul_of_hermes (+0.5% per level)
        let special_chance = (0.05 
            + c.get_stat("special_chance") as f64 * 0.0018
            + c.get_attr("explosive_punches") as f64 * 0.044
            + soul_of_hermes as f64 * 0.005  // +0.5% crit chance per level
            + c.get_inscr("i4") as f64 * 0.0065)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Special (crit) damage - includes soul_of_hermes (+1% per level)
        let special_damage = 1.30 
            + c.get_stat("special_damage") as f64 * 0.01
            + c.get_attr("explosive_punches") as f64 * 0.08
            + soul_of_hermes as f64 * 0.01;  // +1% crit power per level
        
        // Speed
        let speed = 5.0 
            - c.get_stat("speed") as f64 * 0.03
            - c.get_inscr("i23") as f64 * 0.04;
        
        // Lifesteal
        let lifesteal = c.get_attr("book_of_baal") as f64 * 0.0111;
        
        // Loot and XP multipliers - use comprehensive calculation from config
        let loot_mult = c.calculate_loot_multiplier(HunterType::Borge);
        let xp_mult = c.calculate_xp_multiplier(HunterType::Borge);
        
        // Death is my companion revives
        let dimc = c.get_talent("death_is_my_companion");
        let max_revives = if dimc > 0 { dimc } else { 0 };
        
        Self {
            hunter_type: HunterType::Borge,
            level,
            max_hp,
            hp: max_hp,
            power,
            regen,
            damage_reduction,
            evade_chance,
            effect_chance,
            special_chance,
            special_damage,
            speed: speed.max(0.1),
            lifesteal,
            block_chance: 0.0,
            charge: 0.0,
            charge_chance: 0.0,
            charge_gained: 0.0,
            salvo_projectiles: 0,
            death_is_my_companion: dimc,
            life_of_the_hunt: c.get_talent("life_of_the_hunt"),
            unfair_advantage: c.get_talent("unfair_advantage"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: c.get_talent("fires_of_war"),
            multistriker: 0,
            echo_location: 0,
            tricksters_boon: 0,
            crippling_shots: 0,
            omen_of_decay: 0,
            echo_bullets: 0,
            thousand_needles: 0,
            dance_of_dashes: 0,
            vectid_elixir: 0,
            trickster_charges: 0,
            empowered_regen: 0,
            calypsos_advantage: 0,
            ghost_bullets: 0,
            finishing_move: 0,
            helltouch_barrier_level: c.get_attr("helltouch_barrier"),
            atlas_protocol: c.get_attr("atlas_protocol"),
            born_for_battle: c.get_attr("born_for_battle"),
            lifedrain_inhalers: c.get_attr("lifedrain_inhalers"),
            weakspot_analysis: c.get_attr("weakspot_analysis"),
            soul_of_athena: c.get_attr("soul_of_athena"),
            soul_of_hermes,
            soul_of_the_minotaur,
            soul_of_snek: 0,
            cycle_of_death: 0,
            gift_of_medusa: 0,
            deal_with_death: 0,
            space_pirate_armory: 0,
            soul_amplification: 0,
            fortification_elixir: 0,
            empowered_block_regen: 0,
            has_trample: *c.mods.get("trample").unwrap_or(&false),
            has_decay: false,
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,
            revive_count: 0,
            max_revives,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    fn create_ozzy(c: &BuildConfig) -> Self {
        let level = c.get_level();
        
        // Get attribute values for calculations
        let blessings_of_the_cat = c.get_attr("blessings_of_the_cat");
        let soul_of_snek = c.get_attr("soul_of_snek");
        let cycle_of_death = c.get_attr("cycle_of_death");
        let gift_of_medusa = c.get_attr("gift_of_medusa");
        let deal_with_death = c.get_attr("deal_with_death");
        
        // HP calculation
        let hp_stat = c.get_stat("hp") as f64;
        let max_hp = (16.0 + hp_stat * (2.0 + 0.03 * (hp_stat / 5.0).floor()))
            * (1.0 + c.get_attr("living_off_the_land") as f64 * 0.02)
            * (1.0 + c.get_relic("disk_of_dawn") as f64 * 0.03);
        
        // Power calculation - includes blessings_of_the_cat (+2% power per level)
        let pwr_stat = c.get_stat("power") as f64;
        let power = (2.0 + pwr_stat * (0.3 + 0.01 * (pwr_stat / 10.0).floor()))
            * (1.0 + c.get_attr("exo_piercers") as f64 * 0.012)
            * (1.0 + blessings_of_the_cat as f64 * 0.02)
            * (1.0 + c.get_relic("bee_gone_companion_drone") as f64 * 0.03)
            * (1.0 + 0.03 * c.get_gem("innovation_node_#3") as f64);
        
        // Regen - Python: (base) * (1 + living_off_the_land * 0.02)
        let reg_stat = c.get_stat("regen") as f64;
        let regen = (0.1 + reg_stat * (0.05 + 0.01 * (reg_stat / 30.0).floor()))
            * (1.0 + c.get_attr("living_off_the_land") as f64 * 0.02);
        
        // Damage reduction - Python: dr_stat * 0.0035 + wings_of_ibu * 0.026 + i37 * 0.0111
        // blessings_of_the_scarab adds +1% unique DR per level
        let damage_reduction = c.get_stat("damage_reduction") as f64 * 0.0035
            + c.get_attr("wings_of_ibu") as f64 * 0.026
            + c.get_attr("blessings_of_the_scarab") as f64 * 0.01
            + c.get_inscr("i37") as f64 * 0.0111;
        
        // Evade chance - Python: 0.05 + evade_stat * 0.0062 + wings_of_ibu * 0.005
        let evade_chance = 0.05 
            + c.get_stat("evade_chance") as f64 * 0.0062
            + c.get_attr("wings_of_ibu") as f64 * 0.005;
        
        // Effect chance - Python: 0.04 + effect_stat * 0.0035 + extermination_protocol * 0.028 + i31 * 0.006
        let effect_chance = 0.04 
            + c.get_stat("effect_chance") as f64 * 0.0035
            + c.get_attr("extermination_protocol") as f64 * 0.028
            + c.get_inscr("i31") as f64 * 0.006;
        
        // Special (multistrike) chance - Python: 0.05 + special_stat * 0.0038 + i40 * 0.005 + innovation_node_3 * 0.03
        let special_chance = 0.05 
            + c.get_stat("special_chance") as f64 * 0.0038
            + c.get_inscr("i40") as f64 * 0.005
            + c.get_gem("innovation_node_#3") as f64 * 0.03;
        
        // Special (multistrike) damage - Python: 0.25 + special_damage_stat * 0.01
        let special_damage = 0.25 
            + c.get_stat("special_damage") as f64 * 0.01;
        
        // Speed - Python: 4 - speed_stat * 0.02 - thousand_needles * 0.06 - i36 * 0.03
        // blessings_of_the_cat adds -0.4% speed per level (makes attacks faster)
        let thousand_needles_lvl = c.get_talent("thousand_needles");
        let speed = 4.0 
            - c.get_stat("speed") as f64 * 0.02
            - c.get_inscr("i36") as f64 * 0.03
            - thousand_needles_lvl as f64 * 0.06
            - blessings_of_the_cat as f64 * 0.004;  // -0.4% speed per level
        
        // Lifesteal - Python: shimmering_scorpion * 0.033
        let lifesteal = c.get_attr("shimmering_scorpion") as f64 * 0.033;
        
        // Loot multiplier - use comprehensive calculation from config
        // blessings_of_the_scarab adds +5% loot per level (additive to base)
        let base_loot_mult = c.calculate_loot_multiplier(HunterType::Ozzy);
        let loot_mult = base_loot_mult * (1.0 + c.get_attr("blessings_of_the_scarab") as f64 * 0.05);
        
        // XP multiplier
        let xp_mult = c.calculate_xp_multiplier(HunterType::Ozzy);
        
        // Revives - death_is_my_companion + blessings_of_the_sisters
        let dimc = c.get_talent("death_is_my_companion");
        let sisters = c.get_attr("blessings_of_the_sisters");
        let max_revives = dimc + sisters;
        
        Self {
            hunter_type: HunterType::Ozzy,
            level,
            max_hp,
            hp: max_hp,
            power,
            regen,
            damage_reduction,
            evade_chance,
            effect_chance,
            special_chance,
            special_damage,
            speed: speed.max(0.1),
            lifesteal,
            block_chance: 0.0,
            charge: 0.0,
            charge_chance: 0.0,
            charge_gained: 0.0,
            salvo_projectiles: 0,
            death_is_my_companion: dimc,
            life_of_the_hunt: c.get_talent("life_of_the_hunt"),
            unfair_advantage: c.get_talent("unfair_advantage"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: 0,
            multistriker: c.get_talent("multistriker"),
            echo_location: c.get_talent("echo_location"),
            tricksters_boon: c.get_talent("tricksters_boon"),
            crippling_shots: c.get_talent("crippling_shots"),
            omen_of_decay: c.get_talent("omen_of_decay"),
            echo_bullets: c.get_talent("echo_bullets"),
            thousand_needles: c.get_talent("thousand_needles"),
            dance_of_dashes: c.get_attr("dance_of_dashes"),
            vectid_elixir: c.get_attr("vectid_elixir"),
            trickster_charges: 0,
            empowered_regen: 0,
            calypsos_advantage: 0,
            ghost_bullets: 0,
            finishing_move: 0,
            helltouch_barrier_level: 0,
            atlas_protocol: 0,
            born_for_battle: 0,
            lifedrain_inhalers: 0,
            weakspot_analysis: 0,
            soul_of_athena: 0,
            soul_of_hermes: 0,
            soul_of_the_minotaur: 0,
            soul_of_snek,
            cycle_of_death,
            gift_of_medusa,
            deal_with_death,
            space_pirate_armory: 0,
            soul_amplification: 0,
            fortification_elixir: 0,
            empowered_block_regen: 0,
            has_trample: false,
            has_decay: *c.mods.get("decay").unwrap_or(&false),
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,
            revive_count: 0,
            max_revives,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    fn create_knox(c: &BuildConfig) -> Self {
        let level = c.get_level();
        
        // HP calculation
        let hp_stat = c.get_stat("hp") as f64;
        let max_hp = (20.0 + hp_stat * (2.0 + 0.02 * (hp_stat / 5.0).floor()))
            * (1.0 + c.get_attr("release_the_kraken") as f64 * 0.005)
            * (1.0 + c.get_relic("disk_of_dawn") as f64 * 0.03);
        
        // Power calculation
        let pwr_stat = c.get_stat("power") as f64;
        let power = (2.5 + pwr_stat * (0.4 + 0.01 * (pwr_stat / 10.0).floor()))
            * (1.0 + c.get_attr("release_the_kraken") as f64 * 0.005);
        
        // Regen
        let reg_stat = c.get_stat("regen") as f64;
        let regen = (0.15 + reg_stat * (0.04 + 0.01 * (reg_stat / 30.0).floor()))
            * (1.0 + c.get_attr("release_the_kraken") as f64 * 0.008);
        
        // Damage reduction
        let damage_reduction = c.get_stat("damage_reduction") as f64 * 0.01
            + c.get_attr("a_pirates_life_for_knox") as f64 * 0.009;
        
        // Block chance (Knox's unique defense)
        let block_chance = 0.05 
            + c.get_stat("block_chance") as f64 * 0.005
            + c.get_attr("fortification_elixir") as f64 * 0.01
            + c.get_attr("a_pirates_life_for_knox") as f64 * 0.008;
        
        // Effect chance
        let effect_chance = 0.04 
            + c.get_stat("effect_chance") as f64 * 0.004
            + c.get_attr("serious_efficiency") as f64 * 0.02
            + c.get_attr("a_pirates_life_for_knox") as f64 * 0.007;
        
        // Charge chance
        let charge_chance = 0.05 
            + c.get_stat("charge_chance") as f64 * 0.003
            + c.get_attr("serious_efficiency") as f64 * 0.01
            + c.get_attr("a_pirates_life_for_knox") as f64 * 0.006;
        
        // Charge gained (shield of poseidon is FLAT charge)
        let charge_gained = 1.0 
            + c.get_stat("charge_gained") as f64 * 0.01
            + c.get_attr("shield_of_poseidon") as f64 * 0.1;
        
        // Speed (reload time)
        let speed = 4.0 - c.get_stat("reload_time") as f64 * 0.02;
        
        // Projectiles per salvo
        let salvo_projectiles = 5 + c.get_stat("projectiles_per_salvo");
        
        // Special chance/damage (for finishing move)
        let special_chance = 0.10;
        let special_damage = 1.0 + c.get_talent("finishing_move") as f64 * 0.2;
        
        // Loot and XP multipliers - use comprehensive calculation from config
        let loot_mult = c.calculate_loot_multiplier(HunterType::Knox);
        let xp_mult = c.calculate_xp_multiplier(HunterType::Knox);
        
        // Revives
        let dimc = c.get_talent("death_is_my_companion");
        let max_revives = if dimc > 0 { dimc } else { 0 };
        
        Self {
            hunter_type: HunterType::Knox,
            level,
            max_hp,
            hp: max_hp,
            power,
            regen,
            damage_reduction,
            evade_chance: 0.0,  // Knox uses block instead
            effect_chance,
            special_chance,
            special_damage,
            speed: speed.max(0.1),
            lifesteal: 0.0,
            block_chance,
            charge: 0.0,
            charge_chance,
            charge_gained,
            salvo_projectiles,
            death_is_my_companion: dimc,
            life_of_the_hunt: 0,
            unfair_advantage: c.get_talent("unfair_advantage"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: 0,
            multistriker: 0,
            echo_location: 0,
            tricksters_boon: 0,
            crippling_shots: 0,
            omen_of_decay: 0,
            echo_bullets: 0,
            thousand_needles: 0,
            dance_of_dashes: 0,
            vectid_elixir: 0,
            trickster_charges: 0,
            empowered_regen: 0,
            calypsos_advantage: c.get_talent("calypsos_advantage"),
            ghost_bullets: c.get_talent("ghost_bullets"),
            finishing_move: c.get_talent("finishing_move"),
            helltouch_barrier_level: 0,
            atlas_protocol: 0,
            born_for_battle: 0,
            lifedrain_inhalers: 0,
            weakspot_analysis: 0,
            soul_of_athena: 0,
            soul_of_hermes: 0,
            soul_of_the_minotaur: 0,
            soul_of_snek: 0,
            cycle_of_death: 0,
            gift_of_medusa: 0,
            deal_with_death: 0,
            space_pirate_armory: c.get_attr("space_pirate_armory"),
            soul_amplification: c.get_attr("soul_amplification"),
            fortification_elixir: c.get_attr("fortification_elixir"),
            empowered_block_regen: 0,
            has_trample: false,
            has_decay: false,
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,
            revive_count: 0,
            max_revives,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    /// Reset hunter for a new simulation
    pub fn reset(&mut self) {
        self.hp = self.max_hp;
        self.current_stage = 0;
        self.revive_count = 0;
        self.charge = 0.0;
        self.hundred_souls_stacks = 0;
        self.trickster_charges = 0;
        self.empowered_regen = 0;
        self.empowered_block_regen = 0;
        self.decay_stacks = 0;
        self.result = SimResult::default();
    }
    
    /// Check if hunter is dead
    pub fn is_dead(&self) -> bool {
        self.hp <= 0.0
    }
    
    /// Apply regeneration
    pub fn regen_hp(&mut self) {
        if self.hp < self.max_hp {
            // Vectid Elixir - empowered regen for 5 ticks after Unfair Advantage
            let mut regen_value = if self.empowered_regen > 0 {
                self.empowered_regen -= 1;
                self.regen * (1.0 + self.vectid_elixir as f64 * 0.15)
            } else {
                self.regen
            };
            
            // Fortification Elixir (Knox) - +10% regen for 5 ticks after block
            if self.empowered_block_regen > 0 {
                self.empowered_block_regen -= 1;
                regen_value *= 1.0 + self.fortification_elixir as f64 * 0.10;
            }
            
            // Lifedrain Inhalers (Borge) - +0.08% missing HP regen per level
            let missing_hp = self.max_hp - self.hp;
            let lifedrain_bonus = if self.lifedrain_inhalers > 0 {
                missing_hp * 0.0008 * self.lifedrain_inhalers as f64
            } else {
                0.0
            };
            
            let total_regen = regen_value + lifedrain_bonus;
            let healed = total_regen.min(self.max_hp - self.hp);
            self.hp += healed;
            self.result.regenerated_hp += healed;
        }
    }
    
    /// Try to revive if possible
    pub fn try_revive(&mut self) -> bool {
        if self.revive_count < self.max_revives {
            self.revive_count += 1;
            // Revive formula: 10% + 5% per level of talent
            let revive_hp = self.max_hp * (0.10 + 0.05 * self.death_is_my_companion as f64);
            self.hp = revive_hp;
            true
        } else {
            false
        }
    }
    
    /// Calculate loot for the current stage using WASM formulas
    /// Returns (mat1, mat2, mat3, xp)
    pub fn calculate_loot(&self) -> (f64, f64, f64, f64) {
        let stage = self.current_stage as f64;
        let mult = self.loot_mult;
        
        // WASM loot formula uses EXPONENTIAL scaling with stage!
        // Base loot per stage = pow(1.07, stage) * coefficient
        // The 1.07 base was derived from matching website expected values:
        // - 345.67t total loot for 300 stages with ~6000x multiplier
        // - Base loot ~57.6 billion = sum of pow(1.07, i) for i=1..300
        // 
        // Material ratios (from WASM analysis):
        // - Mat1 (Obsidian): 37.8% of total
        // - Mat2 (Behlium): 35.7% of total  
        // - Mat3 (Hellish-Biomatter): 26.6% of total
        let base_loot = 1.07_f64.powf(stage);
        let mat1 = base_loot * 0.378 * mult;
        let mat2 = base_loot * 0.357 * mult;
        let mat3 = base_loot * 0.266 * mult;
        
        // XP also uses exponential scaling
        // XP multiplier includes Book of Mephisto (r19) and inscryptions
        let xp = base_loot * 0.1 * mult * self.xp_mult;
        
        (mat1, mat2, mat3, xp)    }
}