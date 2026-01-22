//! Configuration structures for loading build YAML files

use serde::{Deserialize, Deserializer, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

/// The type of hunter
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum HunterType {
    Borge,
    Ozzy,
    Knox,
}

// Custom deserializer for case-insensitive matching
impl<'de> Deserialize<'de> for HunterType {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let s = String::deserialize(deserializer)?;
        match s.to_lowercase().as_str() {
            "borge" => Ok(HunterType::Borge),
            "ozzy" => Ok(HunterType::Ozzy),
            "knox" => Ok(HunterType::Knox),
            _ => Err(serde::de::Error::unknown_variant(
                &s,
                &["borge", "ozzy", "knox", "Borge", "Ozzy", "Knox"],
            )),
        }
    }
}

/// Metadata about the build
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Meta {
    pub hunter: HunterType,
    pub level: i32,
}

/// Full build configuration loaded from YAML/JSON
/// Supports both formats:
/// 1. { "meta": { "hunter": "Borge", "level": 69 }, ... }  (original YAML format)
/// 2. { "hunter": "Borge", "level": 69, ... }             (GUI JSON format)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BuildConfig {
    // Support both nested meta and flat format
    #[serde(default)]
    pub meta: Option<Meta>,
    // Flat format fields (alternative to meta)
    #[serde(default)]
    pub hunter: Option<HunterType>,
    #[serde(default)]
    pub level: Option<i32>,
    
    pub stats: HashMap<String, i32>,
    pub talents: HashMap<String, i32>,
    pub attributes: HashMap<String, i32>,
    #[serde(default)]
    pub inscryptions: HashMap<String, i32>,
    #[serde(default)]
    pub mods: HashMap<String, bool>,
    #[serde(default)]
    pub relics: HashMap<String, i32>,
    #[serde(default)]
    pub gems: HashMap<String, i32>,
    #[serde(default)]
    pub gadgets: HashMap<String, i32>,
    #[serde(default)]
    pub bonuses: HashMap<String, serde_json::Value>,
}

impl BuildConfig {
    /// Get the hunter type (from meta or flat format)
    pub fn get_hunter_type(&self) -> HunterType {
        if let Some(ref meta) = self.meta {
            meta.hunter
        } else {
            self.hunter.unwrap_or(HunterType::Borge)
        }
    }
    
    /// Get the level (from meta or flat format)
    pub fn get_level(&self) -> i32 {
        if let Some(ref meta) = self.meta {
            meta.level
        } else {
            self.level.unwrap_or(1)
        }
    }
    
    /// Load a build configuration from a YAML file
    pub fn from_file<P: AsRef<Path>>(path: P) -> Result<Self, Box<dyn std::error::Error>> {
        let content = fs::read_to_string(&path)?;
        let path_str = path.as_ref().to_string_lossy().to_lowercase();
        
        // Check if it's JSON or YAML
        if path_str.ends_with(".json") {
            let config: BuildConfig = serde_json::from_str(&content)?;
            Ok(config)
        } else {
            let config: BuildConfig = serde_yaml::from_str(&content)?;
            Ok(config)
        }
    }
    
    /// Load from JSON string (for Python interop)
    pub fn from_json(json: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let config: BuildConfig = serde_json::from_str(json)?;
        Ok(config)
    }
    
    /// Get a stat value with default
    pub fn get_stat(&self, key: &str) -> i32 {
        *self.stats.get(key).unwrap_or(&0)
    }
    
    /// Get a talent value with default
    pub fn get_talent(&self, key: &str) -> i32 {
        *self.talents.get(key).unwrap_or(&0)
    }
    
    /// Get an attribute value with default
    pub fn get_attr(&self, key: &str) -> i32 {
        *self.attributes.get(key).unwrap_or(&0)
    }
    
    /// Get an inscryption value with default
    pub fn get_inscr(&self, key: &str) -> i32 {
        *self.inscryptions.get(key).unwrap_or(&0)
    }
    
    /// Get a relic value with default
    pub fn get_relic(&self, key: &str) -> i32 {
        *self.relics.get(key).unwrap_or(&0)
    }
    
    /// Get a gem value with default
    pub fn get_gem(&self, key: &str) -> i32 {
        *self.gems.get(key).unwrap_or(&0)
    }
    
    /// Get a gadget value with default
    pub fn get_gadget(&self, key: &str) -> i32 {
        *self.gadgets.get(key).unwrap_or(&0)
    }
    
    /// Get a bonus integer value with default
    pub fn get_bonus_int(&self, key: &str) -> i32 {
        self.bonuses.get(key)
            .and_then(|v| v.as_i64())
            .map(|v| v as i32)
            .unwrap_or(0)
    }
    
    /// Get a bonus float value with default
    pub fn get_bonus_float(&self, key: &str) -> f64 {
        self.bonuses.get(key)
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0)
    }
    
    /// Get a bonus boolean value with default
    pub fn get_bonus_bool(&self, key: &str) -> bool {
        self.bonuses.get(key)
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
    }
    
    /// Calculate the complete loot multiplier from all sources.
    /// This matches the WASM calculation which multiplies all bonuses together.
    pub fn calculate_loot_multiplier(&self, hunter_type: HunterType) -> f64 {
        let mut mult = 1.0;
        
        // === TIMELESS MASTERY (Attribute) ===
        // Different bonus per hunter: Borge +14%, Ozzy +16%, Knox +14% per level
        let timeless = self.get_attr("timeless_mastery");
        if timeless > 0 {
            let rate = match hunter_type {
                HunterType::Borge => 0.14,
                HunterType::Ozzy => 0.16,
                HunterType::Knox => 0.14,
            };
            mult *= 1.0 + (timeless as f64 * rate);
        }
        
        // === SHARD MILESTONE #0 ===
        // 1.02^level (unlimited levels!)
        let shard_milestone = self.get_bonus_int("shard_milestone");
        if shard_milestone > 0 {
            mult *= 1.02_f64.powi(shard_milestone);
        }
        
        // === RELIC #7 (Manifestation Core: Titan) ===
        // 1.05^level (max 100)
        let relic7 = self.get_relic("r7").max(self.get_relic("manifestation_core_titan"));
        if relic7 > 0 {
            mult *= 1.05_f64.powi(relic7);
        }
        
        // === RESEARCH #81 ===
        // Tier-based: 0=1.0, 1-3=1.1, 4-6=1.32 per hunter
        let research81 = self.get_bonus_int("research81");
        let research_mult = match (research81, hunter_type) {
            (0, _) => 1.0,
            (1, HunterType::Borge) => 1.1,
            (2, HunterType::Borge) | (2, HunterType::Ozzy) => 1.1,
            (3, _) => 1.1,
            (4, HunterType::Borge) => 1.32,
            (5, HunterType::Borge) | (5, HunterType::Ozzy) => 1.32,
            (6, _) => 1.32,
            _ => 1.0,
        };
        mult *= research_mult;
        
        // === INSCRYPTIONS (hunter-specific) ===
        match hunter_type {
            HunterType::Borge => {
                // i14: 1.1^level (max 5)
                let i14 = self.get_inscr("i14");
                if i14 > 0 { mult *= 1.1_f64.powi(i14); }
                
                // i44: 1.08^level (max 10)
                let i44 = self.get_inscr("i44");
                if i44 > 0 { mult *= 1.08_f64.powi(i44); }
                
                // i60: special multi-power (+3% per level to loot)
                let i60 = self.get_inscr("i60");
                if i60 > 0 { mult *= 1.0 + (i60 as f64 * 0.03); }
                
                // i80: 1.1^level (max 10)
                let i80 = self.get_inscr("i80");
                if i80 > 0 { mult *= 1.1_f64.powi(i80); }
            }
            HunterType::Ozzy => {
                // i32: 1.5^level (max 8)
                let i32_val = self.get_inscr("i32");
                if i32_val > 0 { mult *= 1.5_f64.powi(i32_val); }
                
                // i81: 1.1^level (max 10)
                let i81 = self.get_inscr("i81");
                if i81 > 0 { mult *= 1.1_f64.powi(i81); }
            }
            HunterType::Knox => {
                // Knox doesn't have hunter-specific loot inscryptions yet
            }
        }
        
        // === GADGETS ===
        // Compound formula: (1 + baseValue)^level * tierMultiplier^(level/tierStep)
        // wrench/zaptron/anchor: baseValue=0.005, tierStep=10, tierMultiplier=1.02
        let gadget_loot = |level: i32| -> f64 {
            if level <= 0 { return 1.0; }
            let base = 1.005_f64.powi(level);
            let tier_mult = 1.02_f64.powi(level / 10);
            base * tier_mult
        };
        
        // Wrench (Borge loot) - supports both 'wrench' and 'wrench_of_gore' keys
        if hunter_type == HunterType::Borge {
            let wrench_level = self.get_gadget("wrench").max(self.get_gadget("wrench_of_gore"));
            mult *= gadget_loot(wrench_level);
        }
        // Zaptron (Ozzy loot) - supports both 'zaptron' and 'zaptron_533' keys
        if hunter_type == HunterType::Ozzy {
            let zaptron_level = self.get_gadget("zaptron").max(self.get_gadget("zaptron_533"));
            mult *= gadget_loot(zaptron_level);
        }
        // Anchor (all hunters) - supports both 'anchor' and 'titan_anchor' keys
        let anchor_level = self.get_gadget("anchor").max(self.get_gadget("titan_anchor"));
        mult *= gadget_loot(anchor_level);
        
        // === LOOP MODS ===
        // Scavenger's Advantage: 1.05^level (max 25) - Borge
        if hunter_type == HunterType::Borge {
            let scavenger = self.get_bonus_int("scavenger");
            if scavenger > 0 { mult *= 1.05_f64.powi(scavenger.min(25)); }
        }
        // Scavenger's Advantage 2: 1.05^level (max 25) - Ozzy
        if hunter_type == HunterType::Ozzy {
            let scavenger2 = self.get_bonus_int("scavenger2");
            if scavenger2 > 0 { mult *= 1.05_f64.powi(scavenger2.min(25)); }
        }
        
        // === CONSTRUCTION MILESTONES (CMs) ===
        // These are boolean - either unlocked or not
        if self.get_bonus_bool("cm46") { mult *= 1.03; }
        if self.get_bonus_bool("cm47") { mult *= 1.02; }
        if self.get_bonus_bool("cm48") { mult *= 1.07; }
        if self.get_bonus_bool("cm51") { mult *= 1.05; }
        
        // === DIAMOND CARDS ===
        // Gaiden Card: 1.05 loot (Borge)
        if hunter_type == HunterType::Borge && self.get_bonus_bool("gaiden_card") {
            mult *= 1.05;
        }
        // Iridian Card: 1.05 loot (Ozzy)
        if hunter_type == HunterType::Ozzy && self.get_bonus_bool("iridian_card") {
            mult *= 1.05;
        }
        
        // === DIAMOND SPECIALS ===
        // Hunter Loot Booster: +2.5% per level (max 10)
        let diamond_loot = self.get_bonus_int("diamond_loot");
        if diamond_loot > 0 {
            mult *= 1.0 + (diamond_loot as f64 * 0.025);
        }
        
        // === IAP ===
        // Traversal Pack: 1.25x loot
        if self.get_bonus_bool("iap_travpack") {
            mult *= 1.25;
        }
        
        // === ULTIMA ===
        // Direct multiplier (user enters the displayed bonus value)
        let ultima = self.get_bonus_float("ultima_multiplier");
        if ultima > 0.0 {
            mult *= ultima;
        }
        
        // === GEM NODES (Attraction Gem) ===
        // lootBorge/lootOzzy: 1.07^level per level (this is HUGE at high levels!)
        // Formula from WASM: pow(1.07, lootLevel) - max level 50
        // At level 50: 1.07^50 = 29.46x multiplier
        if hunter_type == HunterType::Borge {
            // Try multiple key variations for attraction_loot_borge
            let loot_borge = self.get_gem("attraction_loot_borge")
                .max(self.get_gem("attraction_lootBorge"))
                .max(self.get_gem("lootBorge"))
                .max(self.get_bonus_int("attraction_loot_borge"))
                .max(self.get_bonus_int("attraction_lootBorge"));
            if loot_borge > 0 { 
                mult *= 1.07_f64.powi(loot_borge.min(50)); 
            }
        }
        if hunter_type == HunterType::Ozzy {
            // Try multiple key variations for attraction_loot_ozzy
            let loot_ozzy = self.get_gem("attraction_loot_ozzy")
                .max(self.get_gem("attraction_lootOzzy"))
                .max(self.get_gem("lootOzzy"))
                .max(self.get_bonus_int("attraction_loot_ozzy"))
                .max(self.get_bonus_int("attraction_lootOzzy"));
            if loot_ozzy > 0 { 
                mult *= 1.07_f64.powi(loot_ozzy.min(50)); 
            }
        }
        
        mult
    }
    
    /// Calculate comprehensive XP multiplier from all sources
    pub fn calculate_xp_multiplier(&self, hunter_type: HunterType) -> f64 {
        let mut mult = 1.0;
        
        // === RELIC #19 (Book of Mephisto) - Borge only ===
        // 2^level (max 8 levels) = up to 256x XP
        if hunter_type == HunterType::Borge {
            let r19 = self.get_relic("r19").max(self.get_relic("book_of_mephisto"));
            if r19 > 0 {
                mult *= 2.0_f64.powi(r19.min(8));
            }
        }
        
        // === INSCRYPTION i33 (Ozzy) ===
        // +75% XP per level
        if hunter_type == HunterType::Ozzy {
            let i33 = self.get_inscr("i33");
            if i33 > 0 {
                mult *= 1.75_f64.powi(i33);
            }
        }
        
        mult
    }}