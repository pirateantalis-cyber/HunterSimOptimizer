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
    pub call_me_lucky_loot: i32,  // Loot bonus proc on kill
    pub omen_of_defeat: i32,
    pub presence_of_god: i32,
    pub fires_of_war: i32,
    pub impeccable_impacts: i32,  // Borge stun talent
    
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
    
    // Borge runtime state
    pub fires_of_war_buff: f64,  // Remaining attack speed reduction from FoW
    pub pending_stun_duration: f64,  // Stun to queue (Python queues 'stun' event at priority 0)
    
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
    pub minotaur_dr: f64,  // Soul of the Minotaur - multiplicative DR applied separately (like scarab_dr)
    
    // Ozzy attributes (missing combat effects)  
    pub soul_of_snek: i32,
    pub cycle_of_death: i32,
    pub gift_of_medusa: i32,
    pub deal_with_death: i32,
    pub scarab_dr: f64,  // Blessings of the Scarab - multiplicative DR applied separately
    
    // Knox attributes (missing combat effects)
    pub space_pirate_armory: i32,
    pub soul_amplification: i32,
    pub fortification_elixir: i32,
    pub empowered_block_regen: i32,  // Counter for regen buff after block
    
    // Mod flags
    pub has_trample: bool,
    pub has_decay: bool,
    
    // Catch-up gem values (for power/speed bonuses in early stages)
    pub attraction_catchup: i32,
    pub attraction_gem: i32,
    pub catching_up: bool,  // True for stages 0-99, false after stage 100
    
    // Loot and XP multipliers
    pub loot_mult: f64,
    pub xp_mult: f64,
    
    // Combat tracking
    pub result: SimResult,
    pub current_stage: i32,
    pub revive_count: i32,
    pub max_revives: i32,
    pub max_stage: i32,
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
        
        // Gadget multipliers (WASM-verified: ~0.3% per level + 0.2% bonus per 10 levels)
        // WASM formula: (1 + level * 0.003) * (1.002 ** (level // 10))
        fn gadget_mult(level: i32) -> f64 {
            (1.0 + level as f64 * 0.003) * 1.002_f64.powi(level / 10)
        }
        let wrench_level = c.get_gadget("wrench").max(c.get_gadget("wrench_of_gore"));
        let zaptron_level = c.get_gadget("zaptron").max(c.get_gadget("zaptron_533"));
        let anchor_level = c.get_gadget("anchor").max(c.get_gadget("anchor_of_ages"));
        let gadget_hp_mult = gadget_mult(wrench_level) * gadget_mult(zaptron_level) * gadget_mult(anchor_level);
        let gadget_power_mult = gadget_hp_mult;
        let gadget_regen_mult = gadget_hp_mult;
        
        // Legacy of Ultima: +1% HP/Power/Regen per point
        let talent_dump_mult = 1.0 + c.get_talent("legacy_of_ultima") as f64 * 0.01;
        
        // HP calculation - WASM: base * multipliers + flat inscryptions (i27/i3 added AFTER multipliers)
        let hp_stat = c.get_stat("hp") as f64;
        let hp_base = 43.0 + hp_stat * (2.50 + 0.01 * (hp_stat / 5.0).floor());
        let hp_multiplied = hp_base
            * (1.0 + c.get_attr("soul_of_ares") as f64 * 0.01)
            * (1.0 + c.get_relic("disk_of_dawn") as f64 * 0.03)
            * (1.0 + (0.015 * (level - 39) as f64) * c.get_gem("creation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64)
            * (1.0 + 0.2 * c.get_gem("creation_node_#1") as f64)
            * gadget_hp_mult
            * talent_dump_mult;
        // Inscryptions added AFTER multipliers (WASM verified)
        let max_hp = hp_multiplied + c.get_inscr("i3") as f64 * 6.0 + c.get_inscr("i27") as f64 * 59.15;
        
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
            * (1.0 + 0.03 * c.get_gem("innovation_node_#3") as f64)
            * gadget_power_mult
            * talent_dump_mult;
        
        // Regen calculation
        let reg_stat = c.get_stat("regen") as f64;
        let regen = (0.02 
            + reg_stat * (0.03 + 0.01 * (reg_stat / 30.0).floor())
            + c.get_attr("essence_of_ylith") as f64 * 0.04)
            * (1.0 + c.get_attr("essence_of_ylith") as f64 * 0.009)
            * (1.0 + (0.005 * (level - 39) as f64) * c.get_gem("creation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64)
            * gadget_regen_mult
            * talent_dump_mult;
        
        // Damage reduction - includes soul_of_hermes (+0.2% DR per level) per WASM
        let damage_reduction = (c.get_stat("damage_reduction") as f64 * 0.0144
            + c.get_attr("spartan_lineage") as f64 * 0.015
            + c.get_inscr("i24") as f64 * 0.004
            + soul_of_hermes as f64 * 0.002)  // WASM: +0.2% DR per level
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Evade chance
        let evade_chance = 0.01 
            + c.get_stat("evade_chance") as f64 * 0.0034
            + c.get_attr("superior_sensors") as f64 * 0.016;
        
        // Effect chance - includes soul_of_hermes (+0.4% per level)
        let effect_chance = (0.04 
            + c.get_stat("effect_chance") as f64 * 0.005
            + c.get_attr("superior_sensors") as f64 * 0.012
            // NOTE: Python does NOT add soul_of_hermes to effect_chance (though WASM does)
            + c.get_inscr("i11") as f64 * 0.02
            + 0.03 * c.get_gem("innovation_node_#3") as f64)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Special (crit) chance - Python uses +0.4% per level (not 0.5%)
        let special_chance = (0.05 
            + c.get_stat("special_chance") as f64 * 0.0018
            + c.get_attr("explosive_punches") as f64 * 0.044
            + soul_of_hermes as f64 * 0.004  // Match Python: +0.4% crit chance per level
            + c.get_inscr("i4") as f64 * 0.0065)
            * (1.0 + 0.02 * c.get_gem("creation_node_#2") as f64);
        
        // Special (crit) damage - Python does NOT add soul_of_hermes (though WASM does)
        let special_damage = 1.30 
            + c.get_stat("special_damage") as f64 * 0.01
            + c.get_attr("explosive_punches") as f64 * 0.08;
        
        // Speed
        let speed = 5.0 
            - c.get_stat("speed") as f64 * 0.03
            - c.get_inscr("i23") as f64 * 0.04;
        
        // Lifesteal
        let lifesteal = c.get_attr("book_of_baal") as f64 * 0.0111;
        
        // Loot and XP multipliers - use comprehensive calculation from config
        let base_loot_mult = c.calculate_loot_multiplier(HunterType::Borge, effect_chance);
        let loot_mult = base_loot_mult;
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
            call_me_lucky_loot: c.get_talent("call_me_lucky_loot"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: c.get_talent("fires_of_war"),
            impeccable_impacts: c.get_talent("impeccable_impacts"),
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
            fires_of_war_buff: 0.0,
            pending_stun_duration: 0.0,
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
            minotaur_dr: soul_of_the_minotaur as f64 * 0.01,  // +1% multiplicative DR per level
            soul_of_snek: 0,
            cycle_of_death: 0,
            gift_of_medusa: 0,
            deal_with_death: 0,
            scarab_dr: 0.0,  // Borge doesn't have this
            space_pirate_armory: 0,
            soul_amplification: 0,
            fortification_elixir: 0,
            empowered_block_regen: 0,
            has_trample: *c.mods.get("trample").unwrap_or(&false),
            has_decay: false,
            attraction_catchup: c.get_gem("attraction_catch-up").max(c.get_gem("attraction_catch_up")),
            attraction_gem: c.get_gem("attraction_gem"),
            catching_up: true,  // Python starts with catching_up=True
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,  // Python starts at stage 0
            revive_count: 0,
            max_revives,
            max_stage: 300,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    fn create_ozzy(c: &BuildConfig) -> Self {
        let level = c.get_level();
        
        // Get attribute values for calculations
        let blessings_of_the_cat = c.get_attr("blessings_of_the_cat");
        let blessings_of_the_scarab = c.get_attr("blessings_of_the_scarab");
        let soul_of_snek = c.get_attr("soul_of_snek");
        let cycle_of_death = c.get_attr("cycle_of_death");
        let gift_of_medusa = c.get_attr("gift_of_medusa");
        let deal_with_death = c.get_attr("deal_with_death");
        
        // Gadget multipliers (WASM verified: ~0.3% per level + 0.2% bonus per 10 levels)
        // WASM formula: (1 + level * 0.003) * (1.002 ** (level // 10))
        fn gadget_mult(level: f64) -> f64 {
            (1.0 + level * 0.003) * 1.002_f64.powf((level / 10.0).floor())
        }
        let wrench_level = c.get_gadget("wrench").max(c.get_gadget("wrench_of_gore")) as f64;
        let zaptron_level = c.get_gadget("zaptron").max(c.get_gadget("zaptron_533")) as f64;
        let anchor_level = c.get_gadget("anchor").max(c.get_gadget("anchor_of_ages")) as f64;
        let gadget_mult_hp = gadget_mult(wrench_level) * gadget_mult(zaptron_level) * gadget_mult(anchor_level);
        
        // Level multiplier for Power (Python: (1.001 ** level) * (1.02 ** (level // 10)))
        let level_mult = 1.001_f64.powi(level) * 1.02_f64.powi(level / 10);
        
        // Attribute multipliers (WASM-verified)
        let lotl_mult = 1.0 + c.get_attr("living_off_the_land") as f64 * 0.02;  // +2% HP/Regen per level
        let exo_power_mult = 1.0 + c.get_attr("exo_piercers") as f64 * 0.012;   // +1.2% Power per level
        let cat_power_mult = 1.0 + blessings_of_the_cat as f64 * 0.02;          // +2% Power per level
        let cat_speed_mult = 1.0 - blessings_of_the_cat as f64 * 0.004;         // -0.4% speed per level (multiplicative!)
        
        // Legacy of Ultima: +1% HP/Power/Regen per point (WASM verified)
        let talent_dump_mult = 1.0 + c.get_talent("legacy_of_ultima") as f64 * 0.01;
        
        // Iridian Card: +3% HP, +3% Power, +3% Regen (WASM verified)
        let iridian_mult = if c.get_bonus_bool("iridian_card") { 1.03 } else { 1.0 };
        
        // HP calculation (WASM verified: HP * lotl_mult * talent_dump_mult * gadget_mult)
        // Note: HP does NOT use level_mult per Python/WASM
        let hp_stat = c.get_stat("hp") as f64;
        let max_hp = (16.0 + hp_stat * (2.0 + 0.03 * (hp_stat / 5.0).floor()))
            * lotl_mult
            * talent_dump_mult
            * (1.0 + c.get_relic("disk_of_dawn").max(c.get_relic("r4")) as f64 * 0.03)
            * (1.0 + 0.03 * c.get_gem("innovation_node_#3") as f64)  // +3% HP from gem
            * gadget_mult_hp
            * iridian_mult;  // Iridian Card: +3% HP
        
        // Power calculation (WASM verified: Power * level_mult * exo_mult * cat_mult * talent_dump_mult * gadget_mult)
        let pwr_stat = c.get_stat("power") as f64;
        let power = (2.0 + pwr_stat * (0.3 + 0.01 * (pwr_stat / 10.0).floor()))
            * level_mult
            * exo_power_mult
            * cat_power_mult
            * talent_dump_mult
            * (1.0 + c.get_relic("bee_gone_companion_drone").max(c.get_relic("r17")) as f64 * 0.03)
            * (1.0 + 0.03 * c.get_gem("innovation_node_#3") as f64)
            * gadget_mult_hp
            * iridian_mult;  // Iridian Card: +3% Power
        
        // Regen (WASM verified: Regen * lotl_mult * talent_dump_mult * gadget_mult)
        // Note: Regen does NOT use level_mult per Python/WASM
        let reg_stat = c.get_stat("regen") as f64;
        let regen = (0.1 + reg_stat * (0.05 + 0.01 * (reg_stat / 30.0).floor()))
            * lotl_mult
            * talent_dump_mult
            * (1.0 + 0.25 * c.get_gem("innovation_node_#3") as f64)  // +25% Regen from gem
            * gadget_mult_hp
            * iridian_mult;  // Iridian Card: +3% Regen
        
        // Damage reduction - DOES NOT include scarab (scarab is multiplicative, applied in combat)
        // WASM: dr_stat * 0.0035 + wings_of_ibu * 0.026 + i37 * 0.0111 + i86 * 0.002
        let damage_reduction = c.get_stat("damage_reduction") as f64 * 0.0035
            + c.get_attr("wings_of_ibu") as f64 * 0.026
            + c.get_inscr("i37") as f64 * 0.0111
            + c.get_inscr("i86") as f64 * 0.002;  // WASM verified: ab * 0.002
        
        // Evade chance - WASM: 0.05 + evade_stat * 0.0062 + wings_of_ibu * 0.005 (NO cat bonus!)
        let evade_chance = 0.05 
            + c.get_stat("evade_chance") as f64 * 0.0062
            + c.get_attr("wings_of_ibu") as f64 * 0.005;
        
        // Effect chance - WASM: 0.04 + effect_stat * 0.0035 + extermination_protocol * 0.028 + i31 * 0.006 + i92 * 0.002
        let effect_chance = 0.04 
            + c.get_stat("effect_chance") as f64 * 0.0035
            + c.get_attr("extermination_protocol") as f64 * 0.028
            + c.get_inscr("i31") as f64 * 0.006
            + c.get_inscr("i92") as f64 * 0.002;  // WASM verified: bb * 0.002
        
        // Special (multistrike) chance - WASM: 0.05 + special_stat * 0.0038 + i40 * 0.005 + innovation_node_3 * 0.03
        let special_chance = 0.05 
            + c.get_stat("special_chance") as f64 * 0.0038
            + c.get_inscr("i40") as f64 * 0.005
            + c.get_gem("innovation_node_#3") as f64 * 0.03;
        
        // Special (multistrike) damage - WASM: 0.25 + special_damage_stat * 0.01
        let special_damage = 0.25 
            + c.get_stat("special_damage") as f64 * 0.01;
        
        // Speed - WASM: (4 - speed_stat * 0.02 - thousand_needles * 0.06 - i36 * 0.03) * cat_speed_mult
        // Note: cat_speed_mult is MULTIPLICATIVE, not additive!
        // IRL CALIBRATION: Coefficient adjusted from 0.02 to 0.0418 to match 1.74 sec in-game
        let thousand_needles_lvl = c.get_talent("thousand_needles");
        let speed = (4.0 
            - c.get_stat("speed") as f64 * 0.0418
            - c.get_inscr("i36") as f64 * 0.03
            - thousand_needles_lvl as f64 * 0.06)
            * cat_speed_mult;  // WASM: multiplicative, not additive
        
        // Lifesteal - Python: shimmering_scorpion * 0.033
        let lifesteal = c.get_attr("shimmering_scorpion") as f64 * 0.033;
        
        // Loot multiplier - use comprehensive calculation from config
        let base_loot_mult = c.calculate_loot_multiplier(HunterType::Ozzy, effect_chance);
        let loot_mult = base_loot_mult;
        
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
            call_me_lucky_loot: c.get_talent("call_me_lucky_loot"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: 0,
            impeccable_impacts: 0,
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
            fires_of_war_buff: 0.0,
            pending_stun_duration: 0.0,
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
            minotaur_dr: 0.0,  // Ozzy doesn't have this
            soul_of_snek,
            cycle_of_death,
            gift_of_medusa,
            deal_with_death,
            scarab_dr: blessings_of_the_scarab as f64 * 0.01,  // +1% multiplicative DR per level
            space_pirate_armory: 0,
            soul_amplification: 0,
            fortification_elixir: 0,
            empowered_block_regen: 0,
            has_trample: false,
            has_decay: *c.mods.get("decay").unwrap_or(&false),
            attraction_catchup: c.get_gem("attraction_catch-up").max(c.get_gem("attraction_catch_up")),
            attraction_gem: c.get_gem("attraction_gem"),
            catching_up: true,  // Python starts with catching_up=True
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,  // Python starts at stage 0
            revive_count: 0,
            max_revives,
            max_stage: 210,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    fn create_knox(c: &BuildConfig) -> Self {
        let level = c.get_level();
        
        // HP calculation
        // Python: 20 + (hp * (2.0 + hp / 50))
        let hp_stat = c.get_stat("hp") as f64;
        let max_hp = (20.0 + hp_stat * (2.0 + hp_stat / 50.0))
            * (1.0 + c.get_attr("release_the_kraken") as f64 * 0.005)
            * (1.0 + c.get_relic("disk_of_dawn") as f64 * 0.03);
        
        // Power calculation
        // Python: 1.2 + (power * (0.06 + power / 1000))
        let pwr_stat = c.get_stat("power") as f64;
        let power = (1.2 + pwr_stat * (0.06 + pwr_stat / 1000.0))
            * (1.0 + c.get_attr("release_the_kraken") as f64 * 0.005);
        
        // Regen
        // Python: 0.05 + (regen * (0.01 + regen * 0.00075))
        let reg_stat = c.get_stat("regen") as f64;
        let regen = 0.05 + reg_stat * (0.01 + reg_stat * 0.00075);
        
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
        // IRL CALIBRATION: Base adjusted from 4.0 to 8.0, coeff from 0.02 to 0.08
        // to match 6.40 sec in-game with reload_time_stat=20
        let speed = 8.0 - c.get_stat("reload_time") as f64 * 0.08;
        
        // Projectiles per salvo (base 3 + upgrades)
        // Python: self.salvo_projectiles = 3 + self.base_stats.get("projectiles_per_salvo", 0)
        let salvo_projectiles = 3 + c.get_stat("projectiles_per_salvo");
        
        // Special chance/damage (for finishing move)
        let special_chance = 0.10;
        let special_damage = 1.0 + c.get_talent("finishing_move") as f64 * 0.2;
        
        // Loot and XP multipliers - use comprehensive calculation from config
        let base_loot_mult = c.calculate_loot_multiplier(HunterType::Knox, effect_chance);
        let loot_mult = base_loot_mult;
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
            call_me_lucky_loot: c.get_talent("call_me_lucky_loot"),
            omen_of_defeat: c.get_talent("omen_of_defeat"),
            presence_of_god: c.get_talent("presence_of_god"),
            fires_of_war: 0,
            impeccable_impacts: 0,
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
            fires_of_war_buff: 0.0,
            pending_stun_duration: 0.0,
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
            minotaur_dr: 0.0,  // Knox doesn't have this
            soul_of_snek: 0,
            cycle_of_death: 0,
            gift_of_medusa: 0,
            deal_with_death: 0,
            scarab_dr: 0.0,  // Knox doesn't have this
            space_pirate_armory: c.get_attr("space_pirate_armory"),
            soul_amplification: c.get_attr("soul_amplification"),
            fortification_elixir: c.get_attr("fortification_elixir"),
            empowered_block_regen: 0,
            has_trample: false,
            has_decay: false,
            attraction_catchup: c.get_gem("attraction_catch-up").max(c.get_gem("attraction_catch_up")),
            attraction_gem: c.get_gem("attraction_gem"),
            catching_up: true,  // Python starts with catching_up=True
            loot_mult,
            xp_mult,
            result: SimResult::default(),
            current_stage: 0,  // Python starts at stage 0
            revive_count: 0,
            max_revives,
            max_stage: 100,
            hundred_souls_stacks: 0,
            decay_stacks: 0,
        }
    }
    
    /// Reset hunter for a new simulation
    pub fn reset(&mut self) {
        self.hp = self.max_hp;
        self.current_stage = 0;  // Python starts at stage 0
        self.catching_up = true;  // Reset to catching_up
        self.revive_count = 0;
        self.charge = 0.0;
        self.hundred_souls_stacks = 0;
        self.trickster_charges = 0;
        self.empowered_regen = 0;
        self.empowered_block_regen = 0;
        self.fires_of_war_buff = 0.0;
        self.decay_stacks = 0;
        self.result = SimResult::default();
    }
    
    /// Check if hunter is dead
    pub fn is_dead(&self) -> bool {
        self.hp <= 0.0
    }
    
    /// Get effective effect chance, accounting for Atlas Protocol (bosses)
    /// Python: (self._effect_chance + self.attributes["atlas_protocol"] * 0.014) on bosses
    pub fn get_effective_effect_chance(&self, is_boss: bool) -> f64 {
        if is_boss && self.atlas_protocol > 0 {
            self.effect_chance + self.atlas_protocol as f64 * 0.014
        } else {
            self.effect_chance
        }
    }
    
    /// Get effective special chance, accounting for Atlas Protocol (bosses)
    /// Python: (self._special_chance + self.attributes["atlas_protocol"] * 0.025) on bosses
    pub fn get_effective_special_chance(&self, is_boss: bool) -> f64 {
        if is_boss && self.atlas_protocol > 0 {
            self.special_chance + self.atlas_protocol as f64 * 0.025
        } else {
            self.special_chance
        }
    }
    
    /// Calculate catch-up multiplier for power/speed bonus
    /// Python: (1.08 ** attraction_catch-up) ** (1 + (attraction_gem * 0.1) - 0.1)
    fn get_catchup_mult(&self) -> f64 {
        if !self.catching_up || self.attraction_catchup == 0 {
            return 1.0;
        }
        let base = 1.08_f64.powi(self.attraction_catchup);
        let exponent = 1.0 + (self.attraction_gem as f64 * 0.1) - 0.1;
        base.powf(exponent)
    }
    
    /// Get effective power, accounting for Born for Battle and catch-up bonus
    /// Python: self._power * (1 + missing_hp_pct * born_for_battle * 0.001) * catchup_mult
    pub fn get_power(&self) -> f64 {
        let missing_hp_pct = if self.max_hp > 0.0 {
            ((self.max_hp - self.hp) / self.max_hp) * 100.0
        } else {
            0.0
        };
        
        self.power 
            * (1.0 + missing_hp_pct * self.born_for_battle as f64 * 0.001)
            * self.get_catchup_mult()
    }
    
    /// Get speed - IDENTICAL to Python's @property speed getter
    /// Python:
    ///   current_speed = (self._speed * (1 - atlas * 0.04)) if is_boss_stage else self._speed
    ///   current_speed /= (1.08 ** catch_up) if catching_up else 1
    ///   current_speed -= self.fires_of_war
    ///   self.fires_of_war = 0
    ///   return current_speed
    pub fn get_speed(&mut self) -> f64 {
        let is_boss = self.current_stage % 100 == 0 && self.current_stage > 0;
        
        // Atlas Protocol: -4% attack time per level on bosses
        let mut current_speed = if is_boss && self.atlas_protocol > 0 {
            self.speed * (1.0 - self.atlas_protocol as f64 * 0.04)
        } else {
            self.speed
        };
        
        // Catch-up speed bonus: divide by catchup_mult (faster attacks)
        let catchup_mult = self.get_catchup_mult();
        if catchup_mult > 1.0 {
            current_speed /= catchup_mult;
        }
        
        // Fires of War - subtract and CONSUME
        if self.fires_of_war_buff > 0.0 {
            current_speed -= self.fires_of_war_buff;
            self.fires_of_war_buff = 0.0;
        }
        
        current_speed.max(0.1)
    }
    
    /// Get effective attack speed, accounting for Atlas Protocol (bosses) and Fires of War buff
    pub fn get_effective_speed(&mut self, is_boss: bool) -> f64 {
        let mut effective_speed = self.speed;
        
        // Atlas Protocol: -4% attack time per level on bosses
        if is_boss && self.atlas_protocol > 0 {
            effective_speed *= 1.0 - self.atlas_protocol as f64 * 0.04;
        }
        
        // Fires of War: temporary attack speed reduction
        if self.fires_of_war_buff > 0.0 {
            effective_speed -= self.fires_of_war_buff;
            self.fires_of_war_buff = 0.0;  // Consume the buff
        }
        
        effective_speed.max(0.1)  // Minimum attack time
    }
    
    /// Apply regeneration
    pub fn regen_hp(&mut self) {
        if self.hp < self.max_hp {
            // Vectid Elixir + Soul of Snek - empowered regen for 5 ticks after Unfair Advantage
            // WASM: Vectid just activates the buff, Soul of Snek determines the strength!
            let mut regen_value = if self.empowered_regen > 0 {
                self.empowered_regen -= 1;
                self.regen * (1.0 + self.soul_of_snek as f64 * 0.15)  // Soul of Snek, not Vectid!
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
            // Python: self.hp = self.max_hp * 0.8
            // Death is my Companion revives at 80% HP
            let revive_hp = self.max_hp * 0.8;
            self.hp = revive_hp;
            true
        } else {
            false
        }
    }
    
    /// Calculate loot for the current stage using Python formulas
    /// Returns (mat1, mat2, mat3, xp)
    pub fn calculate_loot(&self) -> (f64, f64, f64, f64) {
        let stage = self.current_stage as f64;
        let mult = self.loot_mult;
        
        // Hunter-specific stage loot multiplier and base values
        // MUST MATCH Python exactly!
        let (stage_loot_mult, base_common, base_uncommon, base_rare, base_xp): (f64, f64, f64, f64, f64) = match self.hunter_type {
            HunterType::Borge => (1.051, 395.0, 339.0, 256.0, 1640000000000.0),
            HunterType::Ozzy => (1.059, 0.21, 0.18, 0.14, 96600000000.0),
            HunterType::Knox => (1.074, 0.061, 0.053, 0.04, 728.0),
        };
        
        // Geometric series: sum of (mult^0 + mult^1 + ... + mult^(stage-1))
        // Formula: (mult^stage - 1) / (mult - 1)
        let geom_sum = if stage_loot_mult > 1.0 {
            (stage_loot_mult.powf(stage) - 1.0) / (stage_loot_mult - 1.0)
        } else {
            stage
        };
        
        // Loot: Each stage has 10 enemies
        let enemies_per_stage = 10.0_f64;
        let total_enemy_factor = geom_sum;
        
        // Final loot = BASE × GeomSum × LootMultiplier
        let mat1 = base_common * total_enemy_factor * mult;
        let mat2 = base_uncommon * total_enemy_factor * mult;
        let mat3 = base_rare * total_enemy_factor * mult;
        
        // XP calculation: XP is per-stage accumulation, NOT geometric series
        // XP = BASE × stage × xp_mult
        let xp = base_xp * stage * self.xp_mult;
        
        (mat1, mat2, mat3, xp)
    }
}