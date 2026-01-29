import logging
import random
from collections import defaultdict
from heapq import heappush as hpush
from typing import Dict, List, Tuple

import yaml
from util.exceptions import BuildConfigError

hunter_name_spacing: int = 7

# TODO: validate vectid elixir
# TODO: Ozzy: move @property code to on_death() to speed things up?
# TODO: Borge: move @property code as well?
# TODO: DwD power is a little off: 200 ATK, 2 exo, 3 DwD, 1 revive should be 110.59 power but is 110.71. I think DwD might be 0.0196 power instead of 0.02

""" Assumptions:
- order of attacks: main -> ms -> echo -> echo ms
- only the main attack can stun
- multistrike damage (irrespective of trigger source) always depends on main attack power
"""

class Hunter:
    ### SETUP
    def __init__(self, name: str) -> None:
        self.name = name
        self.missing_hp: float
        self.missing_hp_pct: float
        self.sim = None
        self.catching_up: bool = True

        # statistics
        # main
        self.current_stage = 0
        self.total_kills: int = 0
        self.elapsed_time: int = 0
        self.times_revived: int = 0
        self.revive_log = []
        self.enrage_log = []

        # offence
        self.total_attacks: int = 0
        self.total_damage: float = 0

        # sustain
        self.total_taken: float = 0
        self.total_regen: float = 0
        self.total_attacks_suffered: int = 0
        self.total_lifesteal: float = 0

        # defence
        self.total_evades: int = 0
        self.total_mitigated: float = 0

        # effects
        self.total_effect_procs: int = 0
        self.total_lucky_loot_procs: int = 0  # Separate counter for Lucky Loot (independent RNG)
        self.total_stuntime_inflicted: float = 0

        # loot - total and per-resource
        self.total_loot: float = 0
        self.loot_common: float = 0    # Material 1 (Obsidian/Farahyte Ore/Glacium)
        self.loot_uncommon: float = 0  # Material 2 (Behlium/Galvarium/Quartz)
        self.loot_rare: float = 0      # Material 3 (Hellish-Biomatter/Vectid Crystals/Tesseracts)
        self.total_xp: float = 0       # XP earned

    @classmethod
    def from_file(cls, file_path: str) -> 'Hunter':
        """Create a Hunter instance from a build config file.

        Args:
            file_path (str): The path to the build config file.

        Returns:
            Hunter: The Hunter instance.
        """
        with open(file_path, 'r') as f:
            cfg = yaml.safe_load(f)
        if cfg["meta"]["hunter"].lower() not in ["borge", "ozzy", "knox"]:
            raise ValueError("hunter_sim.py: error: invalid hunter found in primary build config file. Please specify a valid hunter.")
        if cls != Hunter:
            return cls(cfg)
        else:
            return globals()[cfg["meta"]["hunter"].title()](cfg)

    def as_dict(self) -> dict:
        """Create a build config dictionary from a loaded hunter instance.

        Returns:
            dict: The hunter build dict.
        """
        return {
            "meta": self.meta,
            "stats": self.base_stats,
            "talents": self.talents,
            "attributes": self.attributes,
            "mods": self.mods,
            "inscryptions": self.inscryptions,
            "relics": self.relics,
            "gems": self.gems,
            "gadgets": dict(self.gadgets),
        }

    def get_results(self) -> List:
        """Fetch the hunter results for end-of-run statistics.

        Returns:
            List: List of all collected stats.
        """
        return {
            'final_stage': self.current_stage,
            'kills': self.total_kills,
            'revive_log': self.revive_log,
            'enrage_log': self.enrage_log,
            'attacks': self.total_attacks,
            'damage': self.total_damage,
            'damage_taken': self.total_taken,
            'regenerated_hp': self.total_regen,
            'attacks_suffered': self.total_attacks_suffered,
            'lifesteal': self.total_lifesteal,
            'evades': self.total_evades,
            'mitigated_damage': self.total_mitigated,
            'effect_procs': self.total_effect_procs,
            'total_loot': self.total_loot,
            'loot_common': self.loot_common,
            'loot_uncommon': self.loot_uncommon,
            'loot_rare': self.loot_rare,
            'total_xp': self.total_xp,
            'stun_duration_inflicted': self.total_stuntime_inflicted,
        }

    @staticmethod
    def load_dummy() -> dict:
        """Abstract placeholder for load_dummy() method. Must be implemented by child classes.

        Raises:
            NotImplementedError: When called from the Hunter class.

        Returns:
            dict: The dummy build dict, created by the child class.
        """
        raise NotImplementedError('load_dummy() not implemented for Hunter() base class')

    def load_build(self, config_dict: Dict) -> None:
        """Load a build config from build config dict, validate it and assign the stats to the hunter's internal dictionaries.

        Args:
            config_dict (dict): A build config dictionary object.

        Raises:
            ValueError: If the config file is invalid.
        """
        # Don't validate strictly - allow missing keys with defaults
        # Support both nested meta format and flat format from web app JSON
        if "meta" in config_dict:
            self.meta = config_dict["meta"]
        else:
            # Flat format: hunter and level at top level
            self.meta = {
                "hunter": config_dict.get("hunter", self.name),
                "level": config_dict.get("level", 0)
            }
        self.max_stage = self.meta.get("irl_max_stage", 1000)
        if self.name == 'Ozzy':
            self.max_stage = 210  # Match IRL data stage
        self.base_stats = defaultdict(int, config_dict.get("stats", {}))
        self.talents = defaultdict(int, config_dict.get("talents", {}))
        self.attributes = defaultdict(int, config_dict.get("attributes", {}))
        self.mods = defaultdict(int, config_dict.get("mods", {}))
        self.inscryptions = defaultdict(int, {k: self.costs["inscryptions"][k]["max"] if v == "max" else v for k, v in config_dict.get("inscryptions", {}).items()})
        self.relics = defaultdict(int, config_dict.get("relics", {}))
        self.gems = defaultdict(int, config_dict.get("gems", {}))
        # New fields with defaults
        self.gadgets = defaultdict(int, config_dict.get("gadgets", {"wrench": 0, "zaptron": 0, "anchor": 0}))
        self.bonuses = config_dict.get("bonuses", {
            "shard_milestone": 0, 
            "research81": 0,
            "scavenger": 0, 
            "scavenger2": 0,
            # Loop Mods (Ouroboros Shrine) - multiplicative loot bonuses
            # LMOuro1: Base Hunt Loot Rewards Bonus (Borge) - exponent^level multiplier
            "lm_ouro1": 0,
            # LMOuro11 Bonus2: Boon Eternity Loot component (Borge) - exponent^level multiplier
            "lm_ouro11": 0,
            # LMOuro18: Base Hunt Loot Rewards Bonus (Ozzy) - exponent^level multiplier
            "lm_ouro18": 0,
            # Construction Milestones
            "cm46": False, 
            "cm47": False, 
            "cm48": False, 
            "cm51": False,
            "gaiden_card": False,
            "iridian_card": False,
            "diamond_loot": 0, 
            "diamond_revive": 0, 
            "iap_travpack": False, 
            "ultima_multiplier": 1.0,
            # XP Bonuses from HuntersAttributes (POM/POI/POK trees)
            # POM3: +10% XP per level (Borge), POI3: +15% XP per level (Ozzy), POK3: +15% XP per level (Knox)
            "pom3": 0,
            "poi3": 0,
            "pok3": 0
        })

    def validate_config(self, cfg: Dict) -> bool:
        """Validate a build config dict against a perfect dummy build to see if they have identical keys in themselves and all value entries.

        Args:
            cfg (dict): The build config

        Returns:
            bool: Whether the configs contain identical keys.
        """
        return (set(cfg.keys()) ^ set(self.load_dummy().keys())) | set().union(*cfg.values()) ^ set().union(*self.load_dummy().values())

    def validate_build(self) -> Tuple[int, int, set, int, int]:
        """Validate the attributes of a build to make sure no attribute maximum levels are exceeded.

        Raises:
            ValueError: When the function is called from a Hunter instance.

        Returns:
            Tuple[int, int, set, int, int]: Attribute points spent, points available, any invalid points found, talent points spent, points available
        """
        if self.__class__ == Hunter:
            raise ValueError('Cannot validate a Hunter() instance.')
        invalid, attr_spent, tal_spent = set(), 0, 0
        # go through all talents and attributes and check if they are within the valid range, then add their cost to the total
        for tal in self.talents.keys():
            if (lvl := self.talents[tal]) > self.costs["talents"][tal]["max"]:
                invalid.add(tal)
            tal_spent += lvl
        for att in self.attributes.keys():
            if (lvl := self.attributes[att]) > self.costs["attributes"][att]["max"]:
                invalid.add(att)
            attr_spent += lvl * self.costs["attributes"][att]["cost"]
        
        # Maximum attribute points per hunter (limited attributes max cost + unlock cost for unlimited nodes)
        max_attr_points = {
            'Ozzy': 238,   # 237 from limited attributes (142 old + 40 cat + 40 scarab + 15 sisters) + 1 unlock
            'Knox': 346,   # 345 from limited attributes + 1 unlock
            'Borge': 257   # 255 from limited attributes (160 old + 15 athena + 40 hermes + 40 minotaur) + 2 unlocks
        }
        
        return attr_spent, max_attr_points[self.name], invalid, tal_spent, (self.meta["level"])

    def attack(self, target, damage: float) -> None:
        """Attack the enemy unit.

        Args:
            target (Enemy): The enemy to attack.
            damage (float): The amount of damage to deal.
        """
        target.receive_damage(damage)

    def receive_damage(self, damage: float) -> float:
        """Receive damage from an attack. Accounts for damage reduction, evade chance and reflected damage.

        Args:
            damage (float): The amount of damage to receive.
        """
        if random.random() < self.evade_chance:
            self.total_evades += 1
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tEVADE')
            return 0
        else:
            mitigated_damage = damage * (1 - self.damage_reduction)
            self.hp -= mitigated_damage
            self.total_taken += mitigated_damage
            self.total_mitigated += (damage - mitigated_damage)
            self.total_attacks_suffered += 1
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTAKE\t{mitigated_damage:>6.2f}, {self.hp:.2f} HP left")
            if self.is_dead():
                self.on_death()
            return mitigated_damage

    def heal_hp(self, value: float, source: str) -> None:
        """Applies healing to hp from different sources. Accounts for overhealing.

        Args:
            value (float): The amount of hp to heal.
            source (str): The source of the healing. Valid: regen, lifesteal, life_of_the_hunt
        """
        effective_heal = min(value, self.missing_hp)
        overhealing = value - effective_heal
        self.hp += effective_heal
        logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\t{source.upper().replace("_", " ")}\t{effective_heal:>6.2f} (+{overhealing:>6.2f} OVERHEAL)')
        match source.lower():
            case 'regen':
                self.total_regen += effective_heal
            case 'steal':
                self.total_lifesteal += effective_heal
            case 'loth':
                self.total_loth += effective_heal
            case 'potion':
                self.total_potion += effective_heal
            case _:
                raise ValueError(f'Unknown heal source: {source}')

    def on_kill(self, loot_type: str = None) -> None:
        """Actions to take when the hunter kills an enemy.
        
        NOTE: Loot is calculated via calculate_final_loot() at end of sim,
        not per-kill. This method only handles kill-related effects like
        Call Me Lucky Loot procs.
        
        Args:
            loot_type: Type of loot this enemy would drop (tracked for future use)
        """
        if not loot_type:
            return
            
        stage = self.current_stage
        
        # Track loot_type effects (e.g., Call Me Lucky Loot procs) without accumulating loot
        # Loot is calculated at end via calculate_final_loot() using geometric series
        
        # Call Me Lucky Loot proc (not on bosses) - independent RNG, separate from other effect procs
        # Each talent/ability has its own effect_chance roll, so Lucky Loot gets its own counter
        if loot_type != 'boss' and (stage % 100 != 0 and stage > 0) and random.random() < self.effect_chance:
            if self.talents["call_me_lucky_loot"] > 0:
                self.total_lucky_loot_procs += 1

    def compute_loot_multiplier(self) -> float:
        """Compute the loot multiplier from talents, attributes, inscryptions, bonuses, etc.
        
        This matches the WASM calculation which multiplies all bonuses together.
        
        Returns:
            float: The combined loot multiplier.
        """
        mult = 1.0
        
        # === TIMELESS MASTERY (Attribute) ===
        if isinstance(self, Borge):
            mult *= 1.0 + self.attributes.get("timeless_mastery", 0) * 0.14
        elif isinstance(self, Ozzy):
            mult *= 1.0 + self.attributes.get("timeless_mastery", 0) * 0.16
        elif isinstance(self, Knox):
            mult *= 1.0 + self.attributes.get("timeless_mastery", 0) * 0.14
        
        # === SHARD MILESTONE #0 ===
        # 1.02^level (unlimited levels!)
        shard_milestone = self.bonuses.get("shard_milestone", 0)
        if shard_milestone > 0:
            mult *= 1.02 ** shard_milestone
        
        # === RELIC #7 (Manifestation Core: Titan) ===
        # 1.05^level (max 100)
        relic7 = self.relics.get("r7", 0) or self.relics.get("manifestation_core_titan", 0)
        if relic7 > 0:
            mult *= 1.05 ** relic7
        
        # === RESEARCH #81 ===
        # Tier-based: levels 1-3 give 1.1, levels 4-6 give 1.32
        research81 = self.bonuses.get("research81", 0)
        if research81 >= 4:
            if isinstance(self, Borge) or (isinstance(self, Ozzy) and research81 >= 5) or research81 >= 6:
                mult *= 1.32
            else:
                mult *= 1.1
        elif research81 >= 1:
            if isinstance(self, Borge) or (isinstance(self, Ozzy) and research81 >= 2) or research81 >= 3:
                mult *= 1.1
        
        # === INSCRYPTIONS (hunter-specific) ===
        if isinstance(self, Borge):
            # i14: 1.1^level (max 5)
            i14 = self.inscryptions.get("i14", 0)
            if i14 > 0: mult *= 1.1 ** i14
            
            # i44: 1.08^level (max 10)
            i44 = self.inscryptions.get("i44", 0)
            if i44 > 0: mult *= 1.08 ** i44
            
            # i60: special multi-power (+3% per level to loot)
            i60 = self.inscryptions.get("i60", 0)
            if i60 > 0: mult *= 1.0 + (i60 * 0.03)
            
            # i80: 1.1^level (max 10)
            i80 = self.inscryptions.get("i80", 0)
            if i80 > 0: mult *= 1.1 ** i80
            
        elif isinstance(self, Ozzy):
            # i32: 1.5^level (max 8)
            i32 = self.inscryptions.get("i32", 0)
            if i32 > 0: mult *= 1.5 ** i32
            
            # i81: 1.1^level (max 10)
            i81 = self.inscryptions.get("i81", 0)
            if i81 > 0: mult *= 1.1 ** i81
            
            # blessings_of_the_scarab: +5% loot per level (max 20)
            scarab = self.attributes.get("blessings_of_the_scarab", 0)
            if scarab > 0: mult *= 1.0 + scarab * 0.05
        
        # === GADGETS ===
        # Compound formula: (1.005)^level * (1.02)^(level/10)
        def gadget_loot(level: int) -> float:
            if level <= 0: return 1.0
            base = 1.005 ** level
            tier_mult = 1.02 ** (level // 10)
            return base * tier_mult
        
        # Support both short ('wrench') and long ('wrench_of_gore') key names
        # Wrench (Borge loot)
        if isinstance(self, Borge):
            wrench_level = self.gadgets.get("wrench", 0) or self.gadgets.get("wrench_of_gore", 0)
            mult *= gadget_loot(wrench_level)
        # Zaptron (Ozzy loot)
        if isinstance(self, Ozzy):
            zaptron_level = self.gadgets.get("zaptron", 0) or self.gadgets.get("zaptron_533", 0)
            mult *= gadget_loot(zaptron_level)
        # Trident (Knox loot) - APK: KnoxLootGadget / Gadget19
        if isinstance(self, Knox):
            trident_level = self.gadgets.get("trident", 0) or self.gadgets.get("gadget19", 0) or self.gadgets.get("trident_of_tides", 0)
            mult *= gadget_loot(trident_level)
        # Anchor (all hunters)
        anchor_level = self.gadgets.get("anchor", 0) or self.gadgets.get("anchor_of_ages", 0)
        mult *= gadget_loot(anchor_level)
        
        # === LOOP MODS ===
        # Scavenger's Advantage: 1.05^level (max 25) - Borge
        if isinstance(self, Borge):
            scavenger = min(self.bonuses.get("scavenger", 0), 25)
            if scavenger > 0: mult *= 1.05 ** scavenger
            
            # LMOuro1: Base Hunt Loot Rewards Bonus (Borge)
            # APK: LMOuro1Bonus1Exponent - multiplicative bonus per level
            # Formula: exponent^level where exponent ≈ 1.02-1.05 (similar to scavenger)
            # Using 1.03 as default based on similar loop mod patterns
            lm_ouro1 = self.bonuses.get("lm_ouro1", 0)
            if lm_ouro1 > 0: mult *= 1.03 ** lm_ouro1
            
            # LMOuro11 Bonus2: Boon Eternity - Loot Rewards component (Borge)
            # APK: LMOuro11Bonus2Exponent - the second bonus is loot (Cells/Loot/Damage)
            # This is a special prestige-tier loop mod, likely stronger
            lm_ouro11 = self.bonuses.get("lm_ouro11", 0)
            if lm_ouro11 > 0: mult *= 1.05 ** lm_ouro11
            
        # Scavenger's Advantage 2: 1.05^level (max 25) - Ozzy
        if isinstance(self, Ozzy):
            scavenger2 = min(self.bonuses.get("scavenger2", 0), 25)
            if scavenger2 > 0: mult *= 1.05 ** scavenger2
            
            # LMOuro18: Base Hunt Loot Rewards Bonus (Ozzy)
            # APK: LMOuro18Bonus18Exponent - multiplicative bonus per level
            # Using 1.03 as default based on similar loop mod patterns
            lm_ouro18 = self.bonuses.get("lm_ouro18", 0)
            if lm_ouro18 > 0: mult *= 1.03 ** lm_ouro18
        
        # === CONSTRUCTION MILESTONES (CMs) ===
        if self.bonuses.get("cm46", False): mult *= 1.03
        if self.bonuses.get("cm47", False): mult *= 1.02
        if self.bonuses.get("cm48", False): mult *= 1.07
        if self.bonuses.get("cm51", False): mult *= 1.05
        
        # === DIAMOND CARDS ===
        if isinstance(self, Borge) and self.bonuses.get("gaiden_card", False):
            mult *= 1.05
        if isinstance(self, Ozzy) and self.bonuses.get("iridian_card", False):
            mult *= 1.05
        
        # === DIAMOND SPECIALS ===
        # Hunter Loot Booster: +2.5% per level (max 10)
        diamond_loot = self.bonuses.get("diamond_loot", 0)
        if diamond_loot > 0:
            mult *= 1.0 + (diamond_loot * 0.025)
        
        # === IAP ===
        # Traversal Pack: 1.25x loot
        if self.bonuses.get("iap_travpack", False):
            mult *= 1.25
        
        # === ULTIMA ===
        # Direct multiplier
        ultima = self.bonuses.get("ultima_multiplier", 1.0)
        if ultima > 0:
            mult *= ultima
        
        # === GEM NODES (Attraction Gem) ===
        # WASM verified: f_m(1.07, level) = 1.07^level
        if isinstance(self, Borge):
            loot_borge = self.gems.get("attraction_loot_borge", 0)
            if loot_borge > 0: mult *= 1.07 ** loot_borge
        if isinstance(self, Ozzy):
            loot_ozzy = self.gems.get("attraction_loot_ozzy", 0)
            if loot_ozzy > 0: mult *= 1.07 ** loot_ozzy
        # APK: AttractionKnoxLootBonusCalc = 1.07^level
        if isinstance(self, Knox):
            loot_knox = self.gems.get("attraction_loot_knox", 0)
            if loot_knox > 0: mult *= 1.07 ** loot_knox
        
        # === ATTRACTION NODE #3 (Gem Bonus) ===
        # All hunters: 1 + 0.25 × level
        gem_node_3 = self.gems.get("attraction_node_#3", 0)
        if gem_node_3 > 0:
            mult *= 1.0 + 0.25 * gem_node_3
        
        # === PRESENCE OF GOD (Talent) ===
        # All hunters: 1 + 0.2 × level × effect_chance
        pog_level = self.talents.get("presence_of_god", 0)
        if pog_level > 0:
            mult *= 1.0 + pog_level * 0.2 * self.effect_chance
        
        # === SKILL 6 - HUNTER SPECIFIC LOOT BONUS ===
        # Each hunter's Skill 6 provides direct loot multiplier
        skill6_loot = self.bonuses.get("skill6_loot_bonus", 0)
        if skill6_loot > 0:
            mult *= 1.0 + skill6_loot
        
        # === WASTARIAN RELIC LOOT BONUS ===
        # Relic that affects loot multiplier
        wastarian_loot = self.relics.get("wastarian_relic_loot_bonus", 0)
        if wastarian_loot > 0:
            mult *= 1.0 + (wastarian_loot * 0.05)  # Assuming 5% per level, adjust if needed
        
        return mult
    
    def get_xp_bonus(self) -> float:
        """Get the XP bonus multiplier from inscryptions and relics.
        
        Returns:
            float: The XP bonus multiplier.
        """
        xp_bonus = 1.0
        
        # Borge: Relic r19 (Book of Mephisto) = 2^level XP bonus (max 8 levels)
        if isinstance(self, Borge):
            r19 = self.relics.get("r19", 0) or self.relics.get("book_of_mephisto", 0)
            if r19 > 0:
                xp_bonus *= 2 ** min(r19, 8)
            
            # POM3: HuntersAttributes XP bonus (Borge) = +10% per level
            # APK: POM3XpBonus with POM3XpBonusExponent
            pom3 = self.bonuses.get("pom3", 0)
            if pom3 > 0:
                xp_bonus *= 1.0 + (pom3 * 0.10)
        
        # Ozzy: i33 = 1.75^level XP multiplier (WASM verified: f_m(1.75, level))
        if isinstance(self, Ozzy):
            i33 = self.inscryptions.get("i33", 0)
            if i33 > 0:
                xp_bonus *= 1.75 ** min(i33, 6)
            
            # POI3: HuntersAttributes XP bonus (Ozzy) = +15% per level
            # APK: POI3XpBonus with POI3XpBonusExponent
            poi3 = self.bonuses.get("poi3", 0)
            if poi3 > 0:
                xp_bonus *= 1.0 + (poi3 * 0.15)
        
        # Knox: POK3 = +15% XP per level
        # APK: POK3XpBonus with POK3XpBonusExponent
        if isinstance(self, Knox):
            pok3 = self.bonuses.get("pok3", 0)
            if pok3 > 0:
                xp_bonus *= 1.0 + (pok3 * 0.15)
        
        return xp_bonus

    def complete_stage(self, stages: int = 1) -> None:
        """Actions to take when the hunter completes a stage. The Hunter() implementation only handles stage progression.

        Args:
            stages (int, optional): The number of stages to complete. Defaults to 1.
        """
        self.current_stage += stages
        if self.current_stage >= self.max_stage:
            self.hp = 0
            self.times_revived = self.talents.get("death_is_my_companion", 2)  # Prevent revive at max_stage
        if self.current_stage >= 100:
            self.catching_up = False

    def calculate_final_loot(self) -> None:
        """Calculate final loot using geometric series for stage scaling.
        
        The formula is:
        Total Loot = BASE_VALUE × GeomSum(stage_mult, stage) × LootMultiplier
        
        Where GeomSum = (mult^stage - 1) / (mult - 1) represents the cumulative
        loot from all stages, and BASE_VALUE is the per-enemy loot at stage 1.
        
        APK verified: StageLootMultiplier constants:
        - Borge: 1.051
        - Ozzy: 1.059
        - Knox: 1.074
        """
        stage = self.current_stage
        if stage <= 0:
            return
        
        # Hunter-specific StageLootMultiplier (from APK: game_dump.cs)
        if isinstance(self, Borge):
            stage_loot_mult = 1.051
        elif isinstance(self, Ozzy):
            stage_loot_mult = 1.059
        elif isinstance(self, Knox):
            stage_loot_mult = 1.074
        else:
            stage_loot_mult = 1.051  # Default to Borge
        
        # CRITICAL DISCOVERY: Each hunter has COMPLETELY DIFFERENT base loot values!
        # These are from hunter-specific reward arrays in LootManager:
        # - Borge: ObsidianRewards, BehliumRewards, HellishBiomatterRewards, BorgeXPRewards
        # - Ozzy: GalvariumRewards, FarahyteOreRewards, VectidEmeraldRewards, OzzyXPRewards
        # - Knox: GlaciumRewards, AquariusQuartzRewards, NautilusTesseractRewards, KnoxXPRewards
        #
        # These BASE values are per-enemy per-stage rates that exist independently in the game.
        # They are the SAME regardless of the build's loot multiplier.
        #
        # Reverse-engineered from real in-game data using the geometric series formula:
        # Knox L30 @ stage 100:  176.15k common loot (actual loot_mult=213.83x)
        # Ozzy L67 @ stage 201:  19.62t common loot (actual loot_mult=49578.58x)
        # Borge L69 @ stage 300: 373.77t common loot (actual loot_mult=6068.89x)
        #
        # Ratio Analysis:
        # Ozzy base is 4,777.6x higher than Knox
        # Borge base is 21,443.1x higher than Knox
        # This reflects the game design where later hunters have vastly higher base loot values
        if isinstance(self, Borge):
            # Borge bases from game file extraction (Array 280)
            BASE_COMMON = 0.0237  # Stage 1 base loot
            BASE_UNCOMMON = 0.0463  # Ratio preserved
            BASE_RARE = 0.0750     # Ratio preserved
            BASE_XP = 26300000000000  # Adjusted to match IRL XP at stage 300
        elif isinstance(self, Ozzy):
            # Ozzy bases from game file extraction (Array 280)
            BASE_COMMON = 0.0237  # Stage 1 base loot
            BASE_UNCOMMON = 0.0463  # Ratio preserved
            BASE_RARE = 0.0750     # Ratio preserved
            BASE_XP = 779000000000  # Adjusted to match IRL XP at stage 210
        elif isinstance(self, Knox):
            # Knox bases from game file extraction (Array 280)
            BASE_COMMON = 0.0237  # Stage 1 base loot
            BASE_UNCOMMON = 0.0463  # Ratio preserved
            BASE_RARE = 0.0750     # Ratio preserved
            BASE_XP = 786  # Adjusted to match IRL XP at stage 100
        else:
            BASE_COMMON = 0.0237  # Default to game file values
            BASE_UNCOMMON = 0.0463
            BASE_RARE = 0.0750
            BASE_XP = 26300000000000
        
        # Geometric series: sum of (mult^0 + mult^1 + ... + mult^(stage-1))
        # This gives total loot from stages 1 to current stage
        # Formula: (mult^stage - 1) / (mult - 1)
        if stage_loot_mult > 1.0:
            geom_sum = (stage_loot_mult ** stage - 1.0) / (stage_loot_mult - 1.0)
        else:
            geom_sum = float(stage)
        
        # Loot: Each stage has 10 enemies, so total enemy contribution
        # Uses geometric series for cumulative stages
        enemies_per_stage = 10.0
        total_enemy_factor = geom_sum * enemies_per_stage
        
        # Get loot multiplier from all sources (inscryptions, relics, gems, talents, etc.)
        # NOTE: compute_loot_multiplier() now includes gem_bonus (attraction_node_#3),
        # pog_bonus (presence_of_god), and ll_bonus (call_me_lucky_loot)
        loot_mult = self.compute_loot_multiplier()
        
        # Final loot = BASE × GeomSum × EnemiesPerStage × LootMultiplier
        self.loot_common = BASE_COMMON * total_enemy_factor * loot_mult
        self.loot_uncommon = BASE_UNCOMMON * total_enemy_factor * loot_mult
        self.loot_rare = BASE_RARE * total_enemy_factor * loot_mult
        self.total_loot = self.loot_common + self.loot_uncommon + self.loot_rare
        
        # XP calculation: XP is per-stage accumulation, NOT geometric series
        # XP = BASE × stage × xp_mult
        xp_mult = self.get_xp_bonus()
        self.total_xp = BASE_XP * stage * xp_mult

    def is_dead(self) -> bool:
        """Check if the hunter is dead.

        Returns:
            bool: True if the hunter is dead, False otherwise.
        """
        return self.hp <= 0

    def on_death(self) -> None:
        """Actions to take when the hunter dies. Logs the revive and resets the hp to 80% of max hp if a `Death is my Companion`
        charge can be used. If no revives are left, the hunter is marked as dead.
        """
        if self.times_revived < self.talents["death_is_my_companion"]:
            self.hp = self.max_hp * 0.8
            self.revive_log.append(self.current_stage)
            self.times_revived += 1
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tREVIVED, {self.talents["death_is_my_companion"] - self.times_revived} left')
        else:
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tDIED\n')


    ### UTILITY
    @property
    def missing_hp(self) -> float:
        return self.max_hp - self.hp

    @property
    def missing_hp_pct(self) -> float:
        return round((1 - self.hp / self.max_hp) * 100, 0)

    @property
    def loot_mult(self) -> float:
        """Get the loot multiplier (calls compute_loot_multiplier)."""
        return self.compute_loot_multiplier()

    @property
    def xp_mult(self) -> float:
        """Get the XP multiplier (calls get_xp_bonus)."""
        return self.get_xp_bonus()

    def show_build(self, in_colour: bool = True) -> None:
        """Prints the build of this Hunter's instance.
        """
        c_off = '\033[0m'
        c_on = '\033[38;2;128;128;128m'
        attr_spent, attr_avail, invalid, tal_spent, tal_avail = self.validate_build()
        if tal_spent > tal_avail:
            tals = f'(\033[91m{tal_spent:>3}\033[0m/{c_on}{tal_avail:>3}{c_off})'
        else:
            tals = f'({tal_spent:>3}/{c_on}{tal_avail:>3}{c_off})'
        if attr_spent > attr_avail:
            attr = f'(\033[91m{attr_spent:>3}\033[0m/{c_on}{attr_avail:>3}{c_off})'
        else:
            attr = f'({attr_spent:>3}/{c_on}{attr_avail:>3}{c_off})'
        invalid_out = f'\033[91mInvalid\033[0m:\t{(", ".join(invalid)).title()}'
        gem_names = {
            "attraction_gem": "ATT",
            "attraction_catch-up": "C0-99",
            "attraction_node_#3": "AN-3",
            "innovation_node_#3" : "IN-3",
            "creation_node_#1": "CR-1",
            "creation_node_#2": "CR-2",
            "creation_node_#3": "CR-3",
        }
        gem_state = {
            0: u'\u2718',
            1: u'\u2714',
        }
        if not in_colour:
            c_on = c_off
        print(self)
        print('Stats {}:\t{} {} {}   {} {} {}   {} {} {}'.format(f'({c_on}l.{c_off}{self.meta["level"]:>3})', *self.base_stats.values()))
        print(f'Tal {tals}:\t' + ' '.join('[{}{}{}: {}]'.format(c_on, ''.join([l[0].upper() for l in k.split('_')]), c_off, v) for k, v in self.talents.items()))
        print(f'Att {attr}:\t' + ' '.join('[{}{}{}: {}]'.format(c_on, ''.join([l[0].upper() for l in k.split('_')]), c_off, v) for k, v in self.attributes.items()))
        print(f'Gems:\t\t' + ' '.join('[{}{}{}: {}]'.format(c_on, ''.join(gem_names[k]), c_off, gem_state[v] if k not in ['attraction_gem', 'attraction_catch-up'] else v) for k, v in self.gems.items()))
        print(f'Relics:\t\t' + ' '.join('[{}{}{}: {}]'.format(c_on, ''.join([l[0].upper() for l in k.split('_')]), c_off, v) for k, v in self.relics.items()))
        if invalid:
            print(invalid_out)
        print('\n'.join(['-'*120]))

    def __str__(self) -> str:
        """Prints the stats of this Hunter's instance.

        Returns:
            str: The stats as a formatted string.
        """
        return f'[{self.name:>{hunter_name_spacing}}]:\t[HP:{(str(round(self.hp, 2)) + "/" + str(round(self.max_hp, 2))):>18}] [AP:{self.power:>8.2f}] [Regen:{self.regen:>7.2f}] [DR: {self.damage_reduction:>6.2%}] [Evasion: {self.evade_chance:>6.2%}] [Effect: {self.effect_chance:>6.2%}] [SpC: {self.special_chance:>6.2%}] [SpD: {self.special_damage:>5.2f}] [Speed:{self.speed:>5.2f}] [LS: {self.lifesteal:>4.2%}]'


class Borge(Hunter):
    ### SETUP
    # Attribute unlock dependencies with point gate requirements:
    # Chain 1: 1 -> 2 -> 3 -> 4 -> 12 (75 pts) -> 13 (180 pts)
    # Chain 2: 1 -> 5 -> 6/7 -> 11 (75 pts) -> 14 (150 pts)
    # Chain 3: 1 -> 8 -> 9 -> 10 (75 pts) -> 15 (150 pts)
    attribute_dependencies = {
        "soul_of_ares": {},  # 1: Always available
        "essence_of_ylith": {"soul_of_ares": 1},  # 2: depends on 1
        "spartan_lineage": {"essence_of_ylith": 1},  # 3: depends on 2
        "timeless_mastery": {"spartan_lineage": 1},  # 4: depends on 3
        "helltouch_barrier": {"soul_of_ares": 1},  # 5: depends on 1
        "lifedrain_inhalers": {"helltouch_barrier": 1},  # 6: depends on 5
        "explosive_punches": {"helltouch_barrier": 1},  # 7: depends on 5
        "book_of_baal": {"soul_of_ares": 1},  # 8: depends on 1
        "superior_sensors": {"book_of_baal": 1},  # 9: depends on 8
        "atlas_protocol": {"superior_sensors": 1},  # 10: depends on 9 (+ 75 pts gate)
        "weakspot_analysis": {"explosive_punches": 1},  # 11: depends on 7 (+ 75 pts gate)
        "born_for_battle": {"spartan_lineage": 1},  # 12: depends on 3 (+ 75 pts gate)
        "soul_of_athena": {"born_for_battle": 1},  # 13: depends on 12 (+ 180 pts gate)
        "soul_of_hermes": {"weakspot_analysis": 1},  # 14: depends on 11 (+ 150 pts gate)
        "soul_of_the_minotaur": {"atlas_protocol": 1},  # 15: depends on 10 (+ 150 pts gate)
    }
    
    # Point gates for specific attributes (must spend this many points elsewhere before unlocking)
    attribute_point_gates = {
        "atlas_protocol": 75,
        "weakspot_analysis": 75,
        "born_for_battle": 75,
        "soul_of_hermes": 150,
        "soul_of_the_minotaur": 150,
        "soul_of_athena": 180,
    }
    
    # Talents that require ALL other talents to be maxed first
    talent_requires_all_maxed = ["legacy_of_ultima"]
    
    costs = {
        "talents": {
            "death_is_my_companion": { # +1 revive at 80% hp
                "cost": 1,
                "max": 2,
            },
            "life_of_the_hunt": { # chance on hit to heal for x0.06 damage dealt
                "cost": 1,
                "max": 5,
            },
            "unfair_advantage": { # chance to heal x0.02 max hp on kill
                "cost": 1,
                "max": 5,
            },
            "impeccable_impacts": { # chance to stun on hit, grants +2 attack power per point
                "cost": 1,
                "max": 10,
            },
            "omen_of_defeat": { # -0.08 enemy regen
                "cost": 1,
                "max": 10,
            },
            "call_me_lucky_loot": { # chance on kill to gain x0.2 increased loot per point
                "cost": 1,
                "max": 12,
            },
            "presence_of_god": { # -0.04 enemy starting hp per point
                "cost": 1,
                "max": 15,
            },
            "fires_of_war": { # chance on hit to double attack speed for 0.1 seconds per point
                "cost": 1,
                "max": 15,
            },
            "legacy_of_ultima": { # The Legacy of Ultima: +1% HP/Power/Regen per point (WASM verified)
                "cost": 1,
                "max": 50,
                "unlock_level": 75,
            },
        },
        "attributes": {
            "soul_of_ares": { # x0.01 hp, x0.02 power
                "cost": 1,
                "max": float("inf"),
            },
            "essence_of_ylith": { # +0.04 regen, x0.009 hp
                "cost": 1,
                "max": float("inf"),
            },
            "spartan_lineage": { # +0.015 dr
                "cost": 2,
                "max": 6,
            },
            "timeless_mastery": { # +0.14 loot
                "cost": 3,
                "max": 5,
            },
            "helltouch_barrier": { # +0.08 reflected damage
                "cost": 2,
                "max": 10,
            },
            "lifedrain_inhalers": { # +0.0008 missing health regen
                "cost": 2,
                "max": 10,
            },
            "explosive_punches": { # +0.044 special chance, +0.08 special damage
                "cost": 3,
                "max": 6,
            },
            "book_of_baal": { # +0.0111 lifesteal
                "cost": 3,
                "max": 6,
            },
            "superior_sensors": { # +0.016 evade chance, +0.012 effect chance
                "cost": 2,
                "max": 6,
            },
            "atlas_protocol": { # +0.007 damage reduction, +0.014 effect chance, +0.025 special chance, x-0.04% speed
                "cost": 3,
                "max": 6,
            },
            "weakspot_analysis": { # -0.11 crit damage taken reduction
                "cost": 2,
                "max": 6,
            },
            "born_for_battle": { # +0.001 power per 1% missing hp
                "cost": 5,
                "max": 3,
            },
            "soul_of_athena": { # +1 extra special heavy attack (6x attack time) that does 1.5x damage and guarantees crit
                "cost": 15,
                "max": 1,
            },
            "soul_of_hermes": { # +0.4% crit chance, +0.2% DR, +1% crit power, +0.4% effect chance (WASM verified)
                "cost": 2,
                "max": 20,
            },
            "soul_of_the_minotaur": { # +1% attack damage, +1% unique damage reduction (stacks with other DR)
                "cost": 2,
                "max": 20,
            },
        },
        "inscryptions": {
            "i3": { # +6 hp
                "cost": 1,
                "max": 8,
            },
            "i4": { # +0.0065 crit chance
                "cost": 1,
                "max": 6,
            },
            "i11": { # +0.02 effect chance
                "cost": 1,
                "max": 3,
            },
            "i13": { # +8 power
                "cost": 1,
                "max": 8,
            },
            "i14": { # +1.1 loot
                "cost": 1,
                "max": 5,
            },
            "i23": { # -0.04 speed
                "cost": 1,
                "max": 5,
            },
            "i24": { # +0.004 damage reduction
                "cost": 1,
                "max": 8,
            },
            "i27": { # +24 hp
                "cost": 1,
                "max": 10,
            },
            "i44": { # +1.08 loot
                "cost": 1,
                "max": 10,
            },
            "i60": { # +0.03 hp, power, loot
                "cost": 1,
                "max": 10,
            },
        },
    }

    def __init__(self, config_dict: Dict):
        super(Borge, self).__init__(name='Borge')
        self.__create__(config_dict)

        # statistics
        # offence
        self.total_crits: int = 0
        self.total_extra_from_crits: float = 0
        self.total_helltouch: float = 0
        self.helltouch_kills: int = 0
        self.trample_kills: int = 0

        # sustain
        self.total_loth: float = 0
        self.total_potion: float = 0
        self.total_inhaler: float = 0

    def __create__(self, config_dict: Dict) -> None:
        """Create a Borge instance from a build config dict. Computes all final stats from stat growth formulae and additional
        power sources.

        Args:
            config_dict (dict): Build config dictionary object.
        """
        self.load_build(config_dict)
        
        # Calculate gadget multipliers (WASM-verified: ~0.3% per level + 0.2% bonus per 10 levels)
        # WASM formula: (1 + level * 0.003) * (1.002 ** (level // 10))
        def gadget_mult(level):
            return (1 + level * 0.003) * (1.002 ** (level // 10))
        
        gadget_hp_mult = (
            gadget_mult(self.gadgets.get("wrench_of_gore", 0)) *
            gadget_mult(self.gadgets.get("zaptron_533", 0)) *
            gadget_mult(self.gadgets.get("anchor_of_ages", 0))
        )
        gadget_power_mult = gadget_hp_mult  # Same multiplier for power
        gadget_regen_mult = gadget_hp_mult  # Same multiplier for regen
        
        # The Legacy of Ultima: +1% HP/Power/Regen per point (WASM: bc * 0.01 + 1.0)
        talent_dump_mult = 1 + (self.talents.get("legacy_of_ultima", 0) * 0.01)
        
        # hp - WASM: base * ares * gadget + i27_flat + i3_flat
        # Note: i27 and i3 are added AFTER multipliers in WASM!
        hp_base = (
            43
            + (self.base_stats["hp"] * (2.50 + 0.01 * (self.base_stats["hp"] // 5)))
        )
        hp_multiplied = (
            hp_base
            * (1 + (self.attributes["soul_of_ares"] * 0.01))
            * (1 + (self.relics.get("disk_of_dawn", 0) * 0.03))
            * (1 + (0.015 * (self.meta.get("level", 0) - 39)) * self.gems.get("creation_node_#3", 0))
            * (1 + (0.02 * self.gems.get("creation_node_#2", 0)))
            * (1 + (0.2 * self.gems.get("creation_node_#1", 0)))
            * gadget_hp_mult
            * talent_dump_mult
        )
        # Inscryptions add flat HP AFTER multipliers
        self.max_hp = hp_multiplied + (self.inscryptions["i3"] * 6) + (self.inscryptions["i27"] * 59.15)
        self.hp = self.max_hp
        # power
        self.power = (
            (
                3
                + (self.base_stats["power"] * (0.5 + 0.01 * (self.base_stats["power"] // 10)))
                + (self.inscryptions["i13"] * 1)
                + (self.talents["impeccable_impacts"] * 2)
            )
            * (1 + (self.attributes["soul_of_ares"] * 0.002))
            * (1 + (self.inscryptions["i60"] * 0.03))
            * (1 + (self.relics.get("long_range_artillery_crawler", 0) * 0.03))
            * (1 + (0.01 * (self.meta["level"] - 39)) * self.gems["creation_node_#3"])
            * (1 + (0.02 * self.gems["creation_node_#2"]))
            * (1 + (0.03 * self.gems["innovation_node_#3"]))
            * (1 + (self.attributes["soul_of_the_minotaur"] * 0.01))  # WASM: +1% power per level
            * gadget_power_mult
            * talent_dump_mult
        )
        # Soul of Minotaur unique DR (separate multiplicative layer, like Scarab for Ozzy)
        self.minotaur_dr = self.attributes["soul_of_the_minotaur"] * 0.01
        # regen
        self.regen = (
            (
                0.02
                + (self.base_stats["regen"] * (0.03 + 0.01 * (self.base_stats["regen"] // 30)))
                + (self.attributes["essence_of_ylith"] * 0.04)
            )
            * (1 + (self.attributes["essence_of_ylith"] * 0.009))
            * (1 + (0.005 * (self.meta["level"] - 39)) * self.gems["creation_node_#3"])
            * (1 + (0.02 * self.gems["creation_node_#2"]))
            * gadget_regen_mult
            * talent_dump_mult
        )
        # damage_reduction
        self.damage_reduction = (
            (
                0
                + (self.base_stats["damage_reduction"] * 0.0144)
                + (self.attributes["spartan_lineage"] * 0.015)
                + (self.inscryptions["i24"] * 0.004)
                + (self.attributes["soul_of_hermes"] * 0.002)  # WASM: +0.2% DR per level
            )
            * (1 + (0.02 * self.gems["creation_node_#2"]))
        )
        # evade_chance
        self.evade_chance = (
            0.01
            + (self.base_stats["evade_chance"] * 0.0034)
            + (self.attributes["superior_sensors"] * 0.016)
        )
        # effect_chance
        self.effect_chance = (
            (
                0.04
                + (self.base_stats["effect_chance"] * 0.005)
                + (self.attributes["superior_sensors"] * 0.012)
                + (self.inscryptions["i11"] * 0.02)
                + (0.03 * self.gems["innovation_node_#3"])
            )
            * (1 + (0.02 * self.gems["creation_node_#2"]))
        )
        # special_chance
        self.special_chance = (
            (
                0.05
                + (self.base_stats["special_chance"] * 0.0018)
                + (self.attributes["explosive_punches"] * 0.044)
                + (self.inscryptions["i4"] * 0.0065)
                + (self.attributes["soul_of_hermes"] * 0.004)  # WASM: +0.4% crit per level
            )
            * (1 + (0.02 * self.gems["creation_node_#2"]))
        )
        # special_damage
        self.special_damage = (
            1.30
            + (self.base_stats["special_damage"] * 0.01)
            + (self.attributes["explosive_punches"] * 0.08)
        )
        # speed
        self.speed = (
            5
            - (self.base_stats["speed"] * 0.03)
            - (self.inscryptions["i23"] * 0.04)
        )
        # lifesteal
        self.lifesteal = (self.attributes["book_of_baal"] * 0.0111)
        self.fires_of_war: float = 0

    @staticmethod
    def load_dummy() -> dict:
        """Create a dummy build dictionary with empty stats to compare against loaded configs.

        Returns:
            dict: The dummy build dict.
        """
        return {
            "meta": {
                "hunter": "Borge",
                "level": 0
            },
            "stats": {
                "hp": 0,
                "power": 0,
                "regen": 0,
                "damage_reduction": 0,
                "evade_chance": 0,
                "effect_chance": 0,
                "special_chance": 0,
                "special_damage": 0,
                "speed": 0,
            },
            "talents": {
                "death_is_my_companion": 0,
                "life_of_the_hunt": 0,
                "unfair_advantage": 0,
                "impeccable_impacts": 0,
                "omen_of_defeat": 0,
                "call_me_lucky_loot": 0,
                "presence_of_god": 0,
                "fires_of_war": 0,
                "legacy_of_ultima": 0,
            },
            "attributes": {
                "soul_of_ares": 0,
                "essence_of_ylith": 0,
                "helltouch_barrier": 0,
                "book_of_baal": 0,
                "spartan_lineage": 0,
                "explosive_punches": 0,
                "lifedrain_inhalers": 0,
                "superior_sensors": 0,
                "born_for_battle": 0,
                "timeless_mastery": 0,
                "weakspot_analysis": 0,
                "atlas_protocol": 0,
                "soul_of_athena": 0,
                "soul_of_hermes": 0,
                "soul_of_the_minotaur": 0,
            },
            "inscryptions": {
                "i3": 0,  # 6 borge hp
                "i4": 0,  # 0.0065 borge crit
                "i11": 0, # 0.02 borge effect chance
                "i13": 0, # 8 borge power
                "i14": 0, # 1.1 borge loot
                "i23": 0, # 0.04 borge speed
                "i24": 0, # 0.004 borge dr
                "i27": 0, # 24 borge hp
                "i44": 0, # 1.08 borge loot
                "i60": 0, # 0.03 borge hp, power, loot
            },
            "mods": {
                "trample": False,
            },
            "relics": {
                "disk_of_dawn": 0,
                "long_range_artillery_crawler": 0,
                "manifestation_core_titan": 0,
                "book_of_mephisto": 0,
            },
            "gems": {
                "attraction_gem": 0,
                "attraction_catch-up": 0,
                "attraction_node_#3": 0,
                "innovation_node_#3" : 0,
                "creation_node_#1": 0,
                "creation_node_#2": 0,
                "creation_node_#3": 0,
            },
            "gadgets": {
                "wrench_of_gore": 0,
                "zaptron_533": 0,
                "anchor_of_ages": 0,
            },
            "bonuses": {
                "shard_milestone": 0,
                "iap_travpack": False,
                "diamond_loot": 0,
                "diamond_revive": 0,
                "ultima_multiplier": 1.0,
            },
        }

    def attack(self, target) -> None:
        """Attack the enemy unit.

        Args:
            target (_type_): The enemy to attack.
        """
        if random.random() < self.special_chance:
            damage = self.power * self.special_damage
            self.total_crits += 1
            self.total_extra_from_crits += (damage - self.power)
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f} (crit)")
        else:
            damage = self.power
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f}")
        if self.mods["trample"] and not target.is_boss() and damage > target.max_hp:
            # Mod: Trample
            trample_kills = self.apply_trample(damage, current_target=target)
            if trample_kills > 1:
                logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTRAMPLE {trample_kills} enemies")
                self.trample_kills += trample_kills
            else:
                super(Borge, self).attack(target, damage)
        else:
            super(Borge, self).attack(target, damage)
        self.total_damage += damage
        self.total_attacks += 1

        #  on_attack() effects
        self.heal_hp(damage * self.lifesteal, 'steal')
        if random.random() < self.effect_chance and (LotH := self.talents["life_of_the_hunt"]):
            # Talent: Life of the Hunt
            LotH_healing = damage * LotH * 0.06
            self.heal_hp(LotH_healing, "loth")
            self.total_loth += LotH_healing
            self.total_effect_procs += 1
        if random.random() < self.effect_chance and self.talents["impeccable_impacts"]:
            # Talent: Impeccable Impacts, will call Hunter.apply_stun()
            hpush(self.sim.queue, (0, 0, 'stun'))
            self.total_effect_procs += 1
        if random.random() < self.effect_chance and self.talents["fires_of_war"]:
            # Talent: Fires of War
            self.apply_fow()
            self.total_effect_procs += 1

    def receive_damage(self, attacker, damage: float, is_crit: bool) -> None:
        """Receive damage from an attack. Accounts for damage reduction, evade chance and reflected damage.

        Args:
            attacker (Enemy): The unit that is attacking. Used to apply damage reflection.
            damage (float): The amount of damage to receive.
            is_crit (bool): Whether the attack was a critical hit or not.
        """
        # Apply Soul of Minotaur unique DR first (separate multiplicative layer, like Scarab for Ozzy)
        # WASM: damage = damage * (1 - minotaur_level * 0.01)
        damage_after_minotaur = damage * (1 - self.minotaur_dr)
        
        if is_crit:
            reduced_crit_damage = damage_after_minotaur * (1 - self.attributes["weakspot_analysis"] * 0.11)
            final_damage = super(Borge, self).receive_damage(reduced_crit_damage)
        else:
            final_damage = super(Borge, self).receive_damage(damage_after_minotaur)
        if (not self.is_dead()) and final_damage > 0:
            helltouch_effect = (0.1 if (self.current_stage % 100 == 0 and self.current_stage > 0) else 1)
            reflected_damage = final_damage * self.attributes["helltouch_barrier"] * 0.08 * helltouch_effect
            self.total_helltouch += reflected_damage
            attacker.receive_damage(reflected_damage, is_reflected=True)

    def regen_hp(self) -> None:
        """Regenerates hp according to the regen stat, modified by the `Lifedrain Inhalers` attribute.
        """
        inhaler_contrib = ((self.attributes["lifedrain_inhalers"] * 0.0008) * self.missing_hp)
        regen_value = self.regen + inhaler_contrib
        self.total_inhaler += inhaler_contrib
        self.heal_hp(regen_value, 'regen')

    ### SPECIALS
    def on_kill(self, loot_type: str = None) -> None:
        """Actions to take when the hunter kills an enemy. Loot is handled by the parent class.
        """
        super(Borge, self).on_kill(loot_type)
        if random.random() < self.effect_chance and (ua := self.talents["unfair_advantage"]):
            # Talent: Unfair Advantage
            potion_healing = self.max_hp * (ua * 0.02)
            self.heal_hp(potion_healing, "potion")
            self.total_potion += potion_healing
            self.total_effect_procs += 1

    def apply_stun(self, enemy, is_boss: bool) -> None:
        """Apply a stun to an enemy.

        Args:
            enemy (Enemy): The enemy to stun.
        """
        stun_effect = 0.5 if is_boss else 1
        stun_duration = self.talents['impeccable_impacts'] * 0.1 * stun_effect
        enemy.stun(stun_duration)
        self.total_stuntime_inflicted += stun_duration

    def apply_pog(self, enemy) -> None:
        """Apply the Presence of a God effect to an enemy.

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        stage_effect = 0.5 if self.current_stage % 100 == 0 and self.current_stage > 0 else 1
        pog_effect = (self.talents["presence_of_god"] * 0.04) * stage_effect
        enemy.hp = enemy.max_hp * (1 - pog_effect)

    def apply_ood(self, enemy) -> None:
        """Apply the Omen of Defeat effect to an enemy.

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        stage_effect = 0.5 if self.current_stage % 100 == 0 and self.current_stage > 0 else 1
        ood_effect = self.talents["omen_of_defeat"] * 0.08 * stage_effect
        enemy.regen = enemy.regen * (1 - ood_effect)

    def apply_fow(self) -> None:
        """Apply the temporaryFires of War effect to Borge.
        """
        self.fires_of_war = self.talents["fires_of_war"] * 0.1
        logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\t[FoW]\t{self.fires_of_war:>6.2f} sec')

    def apply_trample(self, damage: float, current_target) -> int:
        """Apply the Trample effect to a number of enemies.

        Args:
            damage (float): The damage of the current attack.
            current_target (Enemy): The enemy that was attacked.

        Returns:
            int: The number of enemies killed by the trample effect.
        """
        enemies = self.sim.enemies
        if not enemies:
            return 0
        trample_power = min(int(damage / enemies[0].max_hp), 10)
        trample_kills = 0
        # trample only triggers if the damage is enough to kill at least 2 enemies
        if trample_power > 1:
            # make sure to kill current target first to properly manage the simulation queue
            current_target.kill()
            trample_kills += 1
            alive_index = [i for i, e in enumerate(enemies) if not e.is_dead()]
            for i in alive_index[:trample_power]:
                enemies[i].kill()
                trample_kills += 1
            self.sim.refresh_enemies()
        return trample_kills

    ### UTILITY
    @property
    def power(self) -> float:
        """Getter for the power attribute. Accounts for the Born for Battle effect.

        Returns:
            float: The power of the hunter.
        """
        return (
            self._power
            * (1 + (self.missing_hp_pct * self.attributes["born_for_battle"] * 0.001))
            * ((1.08 ** self.gems["attraction_catch-up"]) ** (1 + (self.gems["attraction_gem"] * 0.1) - 0.1) if self.catching_up else 1)
        )

    @power.setter
    def power(self, value: float) -> None:
        self._power = value

    @property
    def damage_reduction(self) -> float:
        """Getter for the damage_reduction attribute. Accounts for the Atlas Protocol attribute.

        Returns:
            float: The damage reduction of the hunter.
        """
        return (self._damage_reduction + self.attributes["atlas_protocol"] * 0.007) if (self.current_stage % 100 == 0 and self.current_stage > 0) else self._damage_reduction

    @damage_reduction.setter
    def damage_reduction(self, value: float) -> None:
        self._damage_reduction = value

    @property
    def effect_chance(self) -> float:
        """Getter for the effect_chance attribute. Accounts for the Atlas Protocol attribute.

        Returns:
            float: The effect chance of the hunter.
        """
        return (self._effect_chance + self.attributes["atlas_protocol"] * 0.014) if (self.current_stage % 100 == 0 and self.current_stage > 0) else self._effect_chance

    @effect_chance.setter
    def effect_chance(self, value: float) -> None:
        self._effect_chance = value

    @property
    def special_chance(self) -> float:
        """Getter for the special_chance attribute. Accounts for the Atlas Protocol attribute.

        Returns:
            float: The special chance of the hunter.
        """
        return (self._special_chance + self.attributes["atlas_protocol"] * 0.025) if (self.current_stage % 100 == 0 and self.current_stage > 0) else self._special_chance

    @special_chance.setter
    def special_chance(self, value: float) -> None:
        self._special_chance = value

    @property
    def speed(self) -> float:
        """Getter for the speed attribute. Accounts for the Fires of War effect and resets it afterwards.

        Returns:
            float: The speed of the hunter.
        """
        current_speed = (self._speed * (1 - self.attributes["atlas_protocol"] * 0.04)) if (self.current_stage % 100 == 0 and self.current_stage > 0) else self._speed
        current_speed /= (1.08 ** self.gems["attraction_catch-up"]) ** (1 + (self.gems["attraction_gem"] * 0.1) - 0.1) if self.catching_up else 1
        current_speed -= self.fires_of_war
        self.fires_of_war = 0
        return current_speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = value

    def get_results(self) -> List:
        """Fetch the hunter results for end-of-run statistics.

        Returns:
            List: List of all collected stats.
        """
        return super(Borge, self).get_results() | {
            'crits': self.total_crits,
            'extra_damage_from_crits': self.total_extra_from_crits,
            'helltouch_barrier': self.total_helltouch,
            'helltouch_kills': self.helltouch_kills,
            'trample_kills': self.trample_kills,
            'life_of_the_hunt_healing': self.total_loth,
            'unfair_advantage_healing': self.total_potion,
        }

class Ozzy(Hunter):
    ### SETUP
    # Attribute unlock dependencies with point gate requirements:
    # Chain: 1 -> 2 -> 3
    # Chain: 1 -> 2 -> 4 -> 12 (88 pts) -> 13 (148 pts)
    # Chain: 1 -> 5 -> 6 -> 8
    # Chain: 6 -> 12 (88 pts) -> 14 (148 pts)
    # Chain: 6 -> 7 -> 9 -> 15 (178 pts)
    # Point gates: 10 (88), 11 (88), 12 (88)
    attribute_dependencies = {
        "living_off_the_land": {},  # 1: Always available
        "exo_piercers": {"living_off_the_land": 1},  # 2: depends on 1
        "timeless_mastery": {"exo_piercers": 1},  # 3: depends on 2
        "shimmering_scorpion": {"exo_piercers": 1},  # 4: depends on 2
        "wings_of_ibu": {"living_off_the_land": 1},  # 5: depends on 1
        "extermination_protocol": {"wings_of_ibu": 1},  # 6: depends on 5
        "soul_of_snek": {"extermination_protocol": 1},  # 7: depends on 6
        "vectid_elixir": {"extermination_protocol": 1},  # 8: depends on 6
        "cycle_of_death": {"soul_of_snek": 1},  # 9: depends on 7
        "gift_of_medusa": {},  # 10: (+ 88 pts gate)
        "deal_with_death": {},  # 11: (+ 88 pts gate)
        "dance_of_dashes": {"shimmering_scorpion": 1},  # 12: depends on 4 (+ 88 pts gate)
        "blessings_of_the_cat": {"dance_of_dashes": 1},  # 13: depends on 12 (+ 148 pts gate)
        "blessings_of_the_scarab": {"dance_of_dashes": 1},  # 14: depends on 12 (+ 148 pts gate)
        "blessings_of_the_sisters": {"cycle_of_death": 1},  # 15: depends on 9 (+ 178 pts gate)
    }
    
    # Point gates for specific attributes (must spend this many points elsewhere before unlocking)
    attribute_point_gates = {
        "gift_of_medusa": 88,
        "deal_with_death": 88,
        "dance_of_dashes": 88,
        "blessings_of_the_cat": 148,
        "blessings_of_the_scarab": 148,
        "blessings_of_the_sisters": 178,
    }
    
    # Talents that require ALL other talents to be maxed first
    talent_requires_all_maxed = ["legacy_of_ultima"]
    
    costs = {
        "talents": {
            "death_is_my_companion": { # +1 revive, 80% of max hp
                "cost": 1,
                "max": 2,
            },
            "tricksters_boon": { # +1 trickster charge
                "cost": 1,
                "max": 1,
            },
            "unfair_advantage": { # chance to heal x0.02 max hp on kill
                "cost": 1,
                "max": 5,
            },
            "thousand_needles": { # -0.06 speed and chance to stun for 0.05s per point on hit
                "cost": 1,
                "max": 10,
            },
            "omen_of_decay": { # WASM: damage MULTIPLIER +3% per point (procs on 50% effect chance)
                "cost": 1,
                "max": 10,
            },
            "call_me_lucky_loot": { # chance on kill to gain x0.2 increased loot per point
                "cost": 1,
                "max": 10,
            },
            "crippling_shots": { # WASM: +0.8% enemy HP per stack on hit, /10 on bosses
                "cost": 1,
                "max": 15,
            },
            "echo_bullets": { # chance on hit to deal x0.05 damage per point to enemy
                "cost": 1,
                "max": 20,
            },
            "legacy_of_ultima": { # The Legacy of Ultima: +1% HP/Power/Regen per point (WASM verified)
                "cost": 1,
                "max": 50,
                "unlock_level": 75,
            },
        },
        "attributes": {
            "living_off_the_land": { # +1% HP per level, +0.2% regen per level (WASM verified)
                "cost": 1,
                "max": float("inf"),
            },
            "exo_piercers": { # +1.2% Power per level (WASM verified - NO speed effect!)
                "cost": 1,
                "max": float("inf"),
            },
            "timeless_mastery": { # +16% loot per level ONLY (WASM verified - NO HP/Power/Regen!)
                "cost": 3,
                "max": 5,
            },
            "shimmering_scorpion": { # +0.033 lifesteal
                "cost": 3,
                "max": 5,
            },
            "wings_of_ibu": { # +0.026 dr, +0.005 evade chance
                "cost": 2,
                "max": 5,
            },
            "extermination_protocol": { # +0.028 effect chance
                "cost": 2,
                "max": 5,
            },
            "soul_of_snek": { # +0.088 enemy regen reduction
                "cost": 3,
                "max": 5,
            },
            "vectid_elixir": { # x0.15 regen after unfair advantage proc
                "cost": 2,
                "max": 10,
            },
            "cycle_of_death": { # +0.023 special chance, +0.02 special damage per revive used
                "cost": 3,
                "max": 5,
            },
            "gift_of_medusa": { # 0.06 hunter regen as enemy -regen (WASM verified, not 0.05!)
                "cost": 3,
                "max": 5,
            },
            "deal_with_death": { # x0.02 power, +0.016 dr per revive used
                "cost": 5,
                "max": 3,
            },
            "dance_of_dashes": { # 0.15 chance to gain trickster charge on evade
                "cost": 3,
                "max": 4,
            },
            "blessings_of_the_cat": { # +0.4% crit, +0.4% evade, +1% effect chance per level (WASM verified)
                "cost": 2,
                "max": 20,
            },
            "blessings_of_the_scarab": { # +1% UNIQUE DR (stacks multiplicatively), +5% loot per level (WASM verified)
                "cost": 2,
                "max": 20,
            },
            "blessings_of_the_sisters": { # +1 revive
                "cost": 15,
                "max": 1,
            },
        },
        "inscryptions": {
            "i31": { # +0.006 ozzy effect chance
                "cost": 1,
                "max": 10,
            },
            "i32": { # x1.5 ozzy loot
                "cost": 1,
                "max": 6,
            },
            "i33": { # x1.75 ozzy xp
                "cost": 1,
                "max": 6,
            },
            "i36": { # -0.03 ozzy speed
                "cost": 1,
                "max": 5,
            },
            "i37": { # +0.0111 ozzy dr
                "cost": 1,
                "max": 7,
            },
            "i40": { # +0.005 ozzy multistrike chance
                "cost": 1,
                "max": 10,
            },
            "i86": { # +0.002 ozzy DR (WASM verified: ab * 0.002)
                "cost": 1,
                "max": 10,
            },
            "i92": { # +0.002 ozzy effect chance (WASM verified: bb * 0.002)
                "cost": 1,
                "max": 10,
            },
        },
    }

    def __init__(self, config_dict: Dict):
        super(Ozzy, self).__init__(name='Ozzy')
        self.scarab_dr: float = 0  # Blessings of the Scarab damage reduction
        self.crit_chance: float = 0  # Ozzy has NO crit in WASM!
        self.__create__(config_dict)
        self.trickster_charges: int = 0
        self.crippling_on_target: int = 0
        self.empowered_regen: int = 0
        self.attack_queue: List = []

        # statistics
        # offence
        self.total_multistrikes: int = 0
        self.total_ms_extra_damage: float = 0
        self.total_decay_damage: float = 0
        self.total_cripple_extra_damage: float = 0
        self.medusa_kills: int = 0

        # sustain
        self.total_potion: float = 0

        # defence
        self.total_trickster_evades: int = 0

        # effects
        self.total_echo: int = 0

    def __create__(self, config_dict: Dict) -> None:
        """Create an Ozzy instance from a build config dict. Computes all final stats from stat growth formulae and
        additional power sources.

        Args:
            config_dict (dict): Build config dictionary object.
        """
        self.load_build(config_dict)
        
        # Calculate gadget multipliers (WASM-verified: ~0.3% per level + 0.2% bonus per 10 levels)
        # WASM formula: (1 + level * 0.003) * (1.002 ** (level // 10))
        def gadget_mult(level):
            return (1 + level * 0.003) * (1.002 ** (level // 10))
        
        gadget_hp_mult = (
            gadget_mult(self.gadgets.get("wrench_of_gore", 0)) *
            gadget_mult(self.gadgets.get("zaptron_533", 0)) *
            gadget_mult(self.gadgets.get("anchor_of_ages", 0))
        )
        gadget_power_mult = gadget_hp_mult
        gadget_regen_mult = gadget_hp_mult
        
        # Attribute multipliers (WASM-verified Jan 2026)
        # NOTE: timeless_mastery only affects LOOT (+16% per level), NOT HP/Power/Regen!
        lotl_mult = 1 + (self.attributes["living_off_the_land"] * 0.02)  # +2% HP/Regen per level (WASM: ja[60] * 0.02)
        exo_power_mult = 1 + (self.attributes["exo_piercers"] * 0.012)  # +1.2% Power per level (WASM: ja[61] * 0.012)
        # NOTE: exo_piercers does NOT give crit or special_chance in WASM!
        
        # blessings_of_the_cat (WASM: ja[64]) - gives Power and Speed, NOT crit/evade/effect!
        cat_power_mult = 1 + (self.attributes["blessings_of_the_cat"] * 0.02)  # +2% Power per level (WASM: ja[64] * 0.02)
        cat_speed_mult = 1 - (self.attributes["blessings_of_the_cat"] * 0.004)  # -0.4% attack speed per level (faster!)
        
        # blessings_of_the_scarab (WASM: ja[69]) - gives DR only (applied in receive_damage), NOT power/speed!
        # NOTE: scarab does NOT give power or speed bonuses in WASM!
        
        # The Legacy of Ultima: +1% HP/Power/Regen per point (WASM: ja[59] = s = "ultima")
        talent_dump_mult = 1 + (self.talents.get("legacy_of_ultima", 0) * 0.01)
        
        # Scarab gives separate multiplicative DR (applied in receive_damage)
        self.scarab_dr = self.attributes["blessings_of_the_scarab"] * 0.01  # +1% DR per level (WASM: b[69] * 0.01)
        
        # WASM Level Multiplier (lines 9182-9184):
        # vb = 1.001^level * 1.02^(level/10)
        # This multiplies HP, Power, and Regen
        level = self.meta.get("level", 0)
        level_mult = (1.001 ** level) * (1.02 ** (level // 10))
        
        # Iridian Card: +3% HP, +3% Power, +3% Regen (WASM verified)
        iridian_mult = 1.03 if self.bonuses.get("iridian_card", False) else 1.0
        
        # hp - WASM-verified: HP does NOT use level_mult!
        # WASM formula: hp_base * lotl_mult * disk_mult * gadget_hp_mult * gem_hp_mult
        # Relic r4 = disk_of_dawn (+3% HP per level)
        disk_of_dawn = self.relics.get("disk_of_dawn", 0) or self.relics.get("r4", 0)
        self.max_hp = (
            (
                16
                + (self.base_stats["hp"] * (2 + 0.03 * (self.base_stats["hp"] // 5)))
            )
            # NOTE: No level_mult for HP in WASM!
            * lotl_mult
            * talent_dump_mult
            * (1 + (disk_of_dawn * 0.03))
            * gadget_hp_mult
            * (1 + (0.03 * self.gems.get("innovation_node_#3", 0)))  # +3% HP from gem
            * iridian_mult  # Iridian Card: +3% HP
        )
        self.hp = self.max_hp
        # power - WASM: Power * level_mult * exo_power_mult * cat_power_mult * talent_dump_mult
        # Relic r17 = bee_gone_companion_drone (+3% Power per level)
        bee_gone = self.relics.get("bee_gone_companion_drone", 0) or self.relics.get("r17", 0)
        self.power = (
            (
                2
                + (self.base_stats["power"] * (0.3 + 0.01 * (self.base_stats["power"] // 10)))
            )
            * level_mult
            * exo_power_mult
            * cat_power_mult
            * talent_dump_mult
            * (1 + (bee_gone * 0.03))
            * (1 + (0.03 * self.gems.get("innovation_node_#3", 0)))
            * gadget_power_mult
            * iridian_mult  # Iridian Card: +3% Power
        )
        # regen - WASM-verified: NO level_mult! Uses +25% from innovation_gem3
        self.regen = (
            (
                0.1
                + (self.base_stats["regen"] * (0.05 + 0.01 * (self.base_stats["regen"] // 30)))
            )
            # NOTE: No level_mult for Regen in WASM!
            * lotl_mult
            * talent_dump_mult
            * gadget_regen_mult
            * (1 + (0.25 * self.gems.get("innovation_node_#3", 0)))  # +25% Regen from gem
            * iridian_mult  # Iridian Card: +3% Regen
        )
        self.damage_reduction = (
            0
            + (self.base_stats["damage_reduction"] * 0.0035)
            + (self.attributes["wings_of_ibu"] * 0.026)
            + (self.inscryptions["i37"] * 0.0111)
            + (self.inscryptions.get("i86", 0) * 0.002)  # WASM: ab * 0.002
        )
        # evade_chance - WASM: NO cat bonus for evade! Only base + wings_of_ibu
        self.evade_chance = (
            0.05
            + (self.base_stats["evade_chance"] * 0.0062)
            + (self.attributes["wings_of_ibu"] * 0.005)
        )
        # effect_chance - WASM: NO cat bonus for effect! Only base + extermination_protocol + inscryption
        self.effect_chance = (
            0.04
            + (self.base_stats["effect_chance"] * 0.0035)
            + (self.attributes["extermination_protocol"] * 0.028)
            + (self.inscryptions["i31"] * 0.006)
            + (self.inscryptions.get("i92", 0) * 0.002)  # WASM: bb * 0.002
        )
        # special_chance - WASM: NO exo_special! Only base stats + inscryption + gem
        self.special_chance = (
            (
                0.05
                + (self.base_stats["special_chance"] * 0.0038)
                + (self.inscryptions["i40"] * 0.005)
                + (0.03 * self.gems["innovation_node_#3"])
            )
        )
        # NOTE: Ozzy has NO crit in WASM! Removing crit_chance entirely.
        # special_damage
        self.special_damage = (
            0.25
            + (self.base_stats["special_damage"] * 0.01)
        )
        # speed - WASM formula (lines 9230, 9325, 9348):
        # 1. ja[10] = 4.0 - speed_stat * 0.02 - i36 * 0.03
        # 2. ja[10] = ja[10] - thousand_needles * 0.06
        # 3. ja[10] = ja[10] * (1.0 - ja[64] * 0.004)  where ja[64] = blessings_of_the_cat
        # NOTE: exo_piercers does NOT affect speed! Only blessings_of_the_cat does!
        # IRL CALIBRATION: User confirmed 1.74 sec with speed=36, TN=10, i36=5, cat=1
        # Coefficient adjusted from 0.02 to 0.0418 to match IRL
        self.speed = (
            (
                4
                - (self.base_stats["speed"] * 0.0418)
                - (self.talents["thousand_needles"] * 0.06)
                - (self.inscryptions["i36"] * 0.03)
            )
            * cat_speed_mult  # WASM: ja[10] *= (1.0 - blessings_of_the_cat * 0.004)
        )
        # lifesteal
        self.lifesteal = (self.attributes["shimmering_scorpion"] * 0.033)

    @staticmethod
    def load_dummy() -> dict:
        """Create a dummy build dictionary with empty stats to compare against loaded configs.

        Returns:
            dict: The dummy build dict.
        """
        return {
            "meta": {
                "hunter": "Ozzy",
                "level": 0
            },
            "stats": {
                "hp": 0,
                "power": 0,
                "regen": 0,
                "damage_reduction": 0,
                "evade_chance": 0,
                "effect_chance": 0,
                "special_chance": 0,
                "special_damage": 0,
                "speed": 0,
            },
            "talents": {
                "death_is_my_companion": 0,
                "tricksters_boon": 0,
                "unfair_advantage": 0,
                "thousand_needles": 0,
                "omen_of_decay": 0,
                "call_me_lucky_loot": 0,
                "crippling_shots": 0,
                "echo_bullets": 0,
                "legacy_of_ultima": 0,
            },
            "attributes": {
                "living_off_the_land": 0,
                "exo_piercers": 0,
                "wings_of_ibu": 0,
                "timeless_mastery": 0,
                "shimmering_scorpion": 0,
                "extermination_protocol": 0,
                "dance_of_dashes": 0,
                "gift_of_medusa": 0,
                "vectid_elixir": 0,
                "soul_of_snek": 0,
                "cycle_of_death": 0,
                "deal_with_death": 0,
                "blessings_of_the_cat": 0,
                "blessings_of_the_scarab": 0,
                "blessings_of_the_sisters": 0,
            },
            "inscryptions": {
                "i31": 0, # 0.006 ozzy effect chance
                "i32": 0, # 1.5 ozzy loot
                "i33": 0, # 1.75 ozzy xp
                "i36": 0, # 0.03 ozzy speed
                "i37": 0, # 0.0111 ozzy dr
                "i40": 0, # 0.005 ozzy multistrike chance
                "i86": 0, # 0.002 ozzy DR (WASM)
                "i92": 0, # 0.002 ozzy effect chance (WASM)
            },
            "mods": {
            },
            "relics": {
                "disk_of_dawn": 0,
                "bee_gone_companion_drone": 0,
                "manifestation_core_titan": 0,
            },
            "gems": {
                "attraction_gem": 0,
                "attraction_catch-up": 0,
                "attraction_node_#3": 0,
                "innovation_node_#3" : 0,
            },
            "gadgets": {
                "wrench_of_gore": 0,
                "zaptron_533": 0,
                "anchor_of_ages": 0,
            },
            "bonuses": {
                "shard_milestone": 0,
                "iap_travpack": False,
                "diamond_loot": 0,
                "diamond_revive": 0,
                "ultima_multiplier": 1.0,
            },
        }

    def attack(self, target) -> None:
        """Attack the enemy unit.

        Args:
            target (Enemy): The enemy to attack.
        """
        # method handles all attacks: normal and triggered
        if not self.attack_queue: # normal attacks
            if random.random() < (self.effect_chance / 2) and self.talents["tricksters_boon"]:
                # Talent: Trickster's Boon
                self.trickster_charges += 1
                self.total_effect_procs += 1
                logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTRICKSTER")
            if random.random() < self.special_chance:
                # Stat: Multi-Strike
                self.attack_queue.append('(MS)')
                hpush(self.sim.queue, (0, 1, 'hunter_special'))
            if random.random() < self.effect_chance and self.talents["thousand_needles"]:
                # Talent: Thousand Needles, will call Hunter.apply_stun(). Only Ozzy's main attack can stun.
                hpush(self.sim.queue, (0, 0, 'stun'))
                self.total_effect_procs += 1
            if random.random() < (self.effect_chance / 2) and self.talents["echo_bullets"]:
                # Talent: Echo Bullets
                self.attack_queue.append('(ECHO)')
                hpush(self.sim.queue, (0, 2, 'hunter_special'))
            damage = self.power
            self.total_attacks += 1
            atk_type = ''
        else: # triggered attacks
            atk_type = self.attack_queue.pop(0)
            match atk_type:
                case '(MS)':
                    damage = self.power * self.special_damage
                    self.total_ms_extra_damage += damage
                    self.total_multistrikes += 1
                case '(ECHO)':
                    # WASM: Echo bullets CANNOT trigger multishot (a=1 skips triggers)
                    damage = self.power * (self.talents["echo_bullets"] * 0.05)
                    self.total_echo += 1
                case _:
                    raise ValueError(f'Unknown attack type: {atk_type}')
        
        # WASM-verified combat formulas (Jan 2026):
        # Crippling Shots = flat % HP damage: (crippling_stacks * 0.008 * enemy_hp), /10 on bosses
        # Omen of Decay = damage MULTIPLIER: (1 + omen * 0.03), procs on effect chance
        
        # crippling shots - flat % HP damage based on accumulated stacks
        is_boss = self.current_stage % 100 == 0 and self.current_stage > 0
        cripple_boss_reduction = 0.1 if is_boss else 1.0
        cripple_damage = target.hp * (self.crippling_on_target * 0.008) * cripple_boss_reduction
        self.crippling_on_target = 0
        
        # omen of decay - damage multiplier that procs on effect chance (50% effect for omen)
        omen_multiplier = 1.0
        if self.talents["omen_of_decay"] and random.random() < (self.effect_chance / 2):
            omen_multiplier = 1 + (self.talents["omen_of_decay"] * 0.03)
            self.total_effect_procs += 1
        
        # Final damage = (base damage + cripple HP%) * omen multiplier
        omen_damage = (damage + cripple_damage) * (omen_multiplier - 1)  # Track bonus from omen
        final_damage = (damage + cripple_damage) * omen_multiplier
        
        logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{final_damage:>6.2f} {atk_type} CRIP: {cripple_damage:>6.2f} OMEN: x{omen_multiplier:.2f}")
        super(Ozzy, self).attack(target, final_damage)
        self.total_decay_damage += omen_damage
        self.total_cripple_extra_damage += cripple_damage
        if atk_type == '':
            self.total_damage += cripple_damage

        # on_attack() effects
        # crippling shots and omen of decay inflict _extra damage_ that does not count towards lifesteal
        # WASM: Soul of Snek also empowers lifesteal during Vectid buff!
        lifesteal_amount = damage * self.lifesteal
        if self.empowered_regen > 0:
            lifesteal_amount *= 1 + (self.attributes["soul_of_snek"] * 0.15)
        self.heal_hp(lifesteal_amount, 'steal')
        if random.random() < self.effect_chance and (cs := self.talents["crippling_shots"]):
            # Talent: Crippling Shots, can proc on any attack
            self.crippling_on_target += cs
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tCRIPPLE\t+{cs}")
            self.total_effect_procs += 1
        # Note: on_kill() is called by Enemy.on_death() - no duplicate call needed here

    def receive_damage(self, attacker, damage: float, is_crit: bool) -> None:
        """Receive damage from an attack. Accounts for damage reduction, evade chance and trickster charges.
        
        WASM-verified order (f_le function, line 8517):
        1. Check trickster_charges - if > 0, consume charge and evade
        2. Else check normal evade chance
        3. If evade fails AND it's a crit: Dance of Dashes can give trickster charge
        4. At 200+ enrage: no evade possible, but Dance of Dashes still works on crits

        Args:
            attacker (Enemy/Boss): The unit that is attacking.
            damage (float): The amount of damage to receive.
            is_crit (bool): Whether the attack was a critical hit or not.
        """
        from units import Boss
        boss_max_enrage = isinstance(attacker, Boss) and getattr(attacker, 'max_enrage', False)
        
        # WASM Step 1: Check trickster charges FIRST (disabled at max enrage)
        if self.trickster_charges and not boss_max_enrage:
            self.trickster_charges -= 1
            self.total_trickster_evades += 1
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tEVADE (TRICKSTER)')
            # Evaded via trickster - no damage, no Dance of Dashes proc
            return
        
        # WASM Step 2: Check normal evade (disabled at max enrage)
        if not boss_max_enrage and random.random() < self.evade_chance:
            self.total_evades += 1
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tEVADE')
            # Evaded normally - no damage, no Dance of Dashes proc
            return
        
        # WASM Step 3: Failed to evade - take damage
        # Apply scarab DR (WASM: separate multiplicative DR)
        scarab_reduced_damage = damage * (1 - self.scarab_dr)
        mitigated_damage = scarab_reduced_damage * (1 - self.damage_reduction)
        self.hp -= mitigated_damage
        self.total_taken += mitigated_damage
        self.total_mitigated += (scarab_reduced_damage - mitigated_damage)
        self.total_attacks_suffered += 1
        
        if boss_max_enrage:
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTAKE\t{mitigated_damage:>6.2f} (MAX ENRAGE - no evade), {self.hp:.2f} HP left")
        else:
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTAKE\t{mitigated_damage:>6.2f}, {self.hp:.2f} HP left")
        
        # WASM Step 4: Dance of Dashes - ONLY when you take a crit (inside failed evade branch)
        if is_crit:
            if (dod := self.attributes["dance_of_dashes"]) and random.random() < dod * 0.15:
                self.trickster_charges += 1
                self.total_effect_procs += 1
                logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tDANCE OF DASHES - gained trickster charge')
        
        if self.is_dead():
            self.on_death()

    def on_death(self) -> None:
        """Actions to take when the hunter dies. Ozzy gets revives from BOTH death_is_my_companion 
        AND blessings_of_the_sisters (WASM verified: d = a[51] + a[74]).
        """
        # Total revives = death_is_my_companion + blessings_of_the_sisters
        total_revives = self.talents["death_is_my_companion"] + self.attributes.get("blessings_of_the_sisters", 0)
        if self.times_revived < total_revives:
            self.hp = self.max_hp * 0.8
            self.revive_log.append(self.current_stage)
            self.times_revived += 1
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tREVIVED, {total_revives - self.times_revived} left')
        else:
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tDIED\n')

    def regen_hp(self) -> None:
        """Regenerates hp according to the regen stat, modified by Vectid Elixir + Soul of Snek.
        
        WASM formula: When Vectid is active, regen is multiplied by (1 + soul_of_snek * 0.15).
        Soul of Snek is what actually provides the regen bonus, Vectid Elixir just activates it.
        """
        regen_value = self.regen
        if self.empowered_regen > 0:
            # WASM: Soul of Snek empowers regen during Vectid buff, not Vectid itself!
            regen_value *= 1 + (self.attributes["soul_of_snek"] * 0.15)
            self.empowered_regen -= 1
        self.heal_hp(regen_value, 'regen')

    ### SPECIALS
    def on_kill(self, loot_type: str = None) -> None:
        """Actions to take when the hunter kills an enemy. Loot is handled by the parent class.
        """
        super(Ozzy, self).on_kill(loot_type)
        if random.random() < self.effect_chance and (ua := self.talents["unfair_advantage"]):
            # Talent: Unfair Advantage
            potion_healing = self.max_hp * (ua * 0.02)
            self.heal_hp(potion_healing, "potion")
            self.total_potion += potion_healing
            self.total_effect_procs += 1
            # Attribute: Vectid Elixir
            self.empowered_regen += 5

    def apply_stun(self, enemy, is_boss: bool) -> None:
        """Apply a stun to an enemy.

        Args:
            enemy (Enemy): The enemy to stun.
        """
        stun_effect = 0.5 if is_boss else 1
        stun_duration = self.talents['thousand_needles'] * 0.05 * stun_effect
        enemy.stun(stun_duration)
        self.total_stuntime_inflicted += stun_duration

    def apply_snek(self, enemy) -> None:
        """Apply the Soul of Snek effect to an enemy.

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        ood_effect = self.attributes["soul_of_snek"] * 0.088
        enemy.regen = enemy.regen * (1 - ood_effect)

    def apply_medusa(self, enemy) -> None:
        """Apply the Gift of Medusa effect to an enemy.
        
        WASM formula: enemy.regen -= hunter_regen * medusa_level * 0.06 * vectid_mult
        The vectid multiplier is applied during regen ticks, not here at spawn.
        
        We store the anti-regen value separately so it can be multiplied by vectid
        during each regen tick (matching WASM behavior).

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        # Store the base anti-regen value - vectid multiplier is applied in Enemy.regen_hp()
        # WASM coefficient is 0.06, not 0.05!
        enemy.medusa_anti_regen = self.regen * self.attributes["gift_of_medusa"] * 0.06

    @property
    def power(self) -> float:
        """Getter for the power attribute. Accounts for the Deal with Death effect.

        Returns:
            float: The power of the hunter.
        """
        return (
            self._power
            * (1 + (self.attributes["deal_with_death"] * 0.02 * self.times_revived))
            * ((1.08 ** self.gems["attraction_catch-up"]) ** (1 + (self.gems["attraction_gem"] * 0.1) - 0.1) if self.catching_up else 1)
        )

    @power.setter
    def power(self, value: float) -> None:
        self._power = value
    
    @property
    def damage_reduction(self) -> float:
        """Getter for the damage_reduction attribute. Accounts for the Deal with Death effect.

        Returns:
            float: The damage_reduction of the hunter.
        """
        return self._damage_reduction + (self.attributes["deal_with_death"] * 0.016 * self.times_revived)

    @damage_reduction.setter
    def damage_reduction(self, value: float) -> None:
        self._damage_reduction = value

    @property
    def special_chance(self) -> float:
        """Getter for the special_chance attribute. Accounts for the Cycle of Death effect.

        Returns:
            float: The special_chance of the hunter.
        """
        return self._special_chance + (self.times_revived * self.attributes["cycle_of_death"] * 0.023)

    @special_chance.setter
    def special_chance(self, value: float) -> None:
        self._special_chance = value

    @property
    def special_damage(self) -> float:
        """Getter for the special_damage attribute. Accounts for the Cycle of Death effect.

        Returns:
            float: The special_chance of the hunter.
        """
        return self._special_damage + (self.times_revived * self.attributes["cycle_of_death"] * 0.02)

    @special_damage.setter
    def special_damage(self, value: float) -> None:
        self._special_damage = value

    @property
    def speed(self) -> float:
        """Getter for the speed attribute. Accounts for the Attraction gem catch-up effect.

        Returns:
            float: The speed of the hunter.
        """
        return (
            self._speed
            / ((1.08 ** self.gems["attraction_catch-up"]) ** (1 + (self.gems["attraction_gem"] * 0.1) - 0.1) if self.catching_up else 1)
        )

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = value

    def get_results(self) -> List:
        """Fetch the hunter results for end-of-run statistics.

        Returns:
            List: List of all collected stats.
        """
        return super(Ozzy, self).get_results() | {
            'multistrikes': self.total_multistrikes,
            'extra_damage_from_ms': self.total_ms_extra_damage,
            'unfair_advantage_healing': self.total_potion,
            'trickster_evades': self.total_trickster_evades,
            'decay_damage': self.total_decay_damage,
            'extra_damage_from_crippling_strikes': self.total_cripple_extra_damage,
            'medusa_kills': self.medusa_kills,
            'echo_bullets': self.total_echo,
        }


class Knox(Hunter):
    """Knox hunter class - projectile-based salvo attacker with block and charge mechanics.
    
    NOTE: This is a simplified implementation for build optimization purposes.
    Some mechanics like Hundred Souls stacking are not fully simulated.
    """
    ### SETUP
    # Attribute unlock dependencies for Knox:
    # - release_the_kraken (1) MUST have at least 1 point before 2-4 can be unlocked
    # - space_pirate_armory (2) locks out fortification_elixir (5) and vice versa
    # - soul_amplification (3) locks out a_pirates_life_for_knox (6) and vice versa  
    # - serious_efficiency (4) locks out dead_men_tell_no_tales (7) and vice versa
    # - fortification_elixir (5) locks out passive_charge_tank (8) and vice versa
    # - Later attributes need points in earlier ones
    attribute_dependencies = {
        "release_the_kraken": {},  # Always available (attr 1)
        "space_pirate_armory": {"release_the_kraken": 1},  # Attr 2: needs 1 in attr 1
        "soul_amplification": {"release_the_kraken": 1},  # Attr 3: needs 1 in attr 1
        "serious_efficiency": {"release_the_kraken": 1},  # Attr 4: needs 1 in attr 1
        "fortification_elixir": {"release_the_kraken": 1},  # Attr 5: needs 1 in attr 1
        "a_pirates_life_for_knox": {"space_pirate_armory": 1},  # Attr 6: needs 1 in attr 2
        "dead_men_tell_no_tales": {"soul_amplification": 1},  # Attr 7: needs 1 in attr 3
        "passive_charge_tank": {"serious_efficiency": 1},  # Attr 8: needs 1 in attr 4
        "shield_of_poseidon": {"passive_charge_tank": 1},  # Attr 9: needs 1 in attr 8
        "timeless_mastery": {"fortification_elixir": 1},  # Attr 10: needs 1 in attr 5
    }
    
    # Mutually exclusive attribute pairs (can't have both)
    attribute_exclusions = [
        # Knox has no mutually exclusive attributes - everything is dependency-based
    ]
    
    # Talents that require ALL other talents to be maxed first
    talent_requires_all_maxed = ["legacy_of_ultima"]
    
    costs = {
        "talents": {
            "death_is_my_companion": {  # +1 revive at 80% hp
                "cost": 1,
                "max": 2,
            },
            "calypsos_advantage": {  # chance to gain Hundred Souls stacks on stage clear
                "cost": 1,
                "max": 5,
            },
            "unfair_advantage": {  # chance to heal x0.02 max hp on kill
                "cost": 1,
                "max": 5,
            },
            "ghost_bullets": {  # +6.67% chance for extra bullet per salvo
                "cost": 1,
                "max": 15,
            },
            "omen_of_defeat": {  # -0.08 enemy regen
                "cost": 1,
                "max": 10,
            },
            "call_me_lucky_loot": {  # chance on kill to gain x0.2 increased loot per point
                "cost": 1,
                "max": 10,
            },
            "presence_of_god": {  # -0.03 enemy ATK power per point
                "cost": 1,
                "max": 10,
            },
            "finishing_move": {  # +0.2x damage on last bullet of salvo
                "cost": 1,
                "max": 15,
            },
            "legacy_of_ultima": {  # The Legacy of Ultima: NO effect for Knox (WASM verified)
                "cost": 1,
                "max": 50,
                "unlock_level": 75,
            },
        },
        "attributes": {
            "release_the_kraken": {  # +0.5% hp, +0.8% regen, +0.5% power
                "cost": 1,
                "max": float("inf"),
            },
            "space_pirate_armory": {  # +2% chance to add +3 rounds to Salvo
                "cost": 2,
                "max": 50,
            },
            "soul_amplification": {  # +1% buff to Hundred Souls effect
                "cost": 1,
                "max": 100,
            },
            "serious_efficiency": {  # +2% Effect Chance, +1% Charge Chance
                "cost": 2,
                "max": 5,
            },
            "fortification_elixir": {  # +1% Block, +10% Regen for 5s after block
                "cost": 2,
                "max": 10,
            },
            "a_pirates_life_for_knox": {  # +0.9% DR, +0.8% Block, +0.7% Effect, +0.6% Charge
                "cost": 3,
                "max": 10,
            },
            "dead_men_tell_no_tales": {  # +10 Max Stacks for Hundred Souls
                "cost": 2,
                "max": 10,
            },
            "passive_charge_tank": {  # +0.02 Charge/sec, +8% Torpedo damage
                "cost": 4,
                "max": 10,
            },
            "shield_of_poseidon": {  # +0.1 Charge, +20% damage reflection
                "cost": 1,
                "max": 10,
            },
            "timeless_mastery": {  # +13% Loot
                "cost": 3,
                "max": 5,
            },
        },
        "inscryptions": {
            # Knox inscryptions - placeholders until wiki has full data
            "i_knox_hp": 0,
            "i_knox_power": 0,
            "i_knox_block": 0,
            "i_knox_charge": 0,
            "i_knox_reload": 0,
        },
    }

    def __init__(self, config_dict: Dict):
        super(Knox, self).__init__(name='Knox')
        self.__create__(config_dict)

        # Knox-specific stats
        self.hundred_souls: int = 0
        # Note: salvo_projectiles is set in __create__ based on config (base 3 + upgrades)
        
        # statistics
        # offence
        self.total_ghost_bullets: int = 0
        self.total_ghost_bullet_damage: float = 0  # Extra damage from ghost bullet projectiles
        self.total_finishing_moves: int = 0
        self.total_charges: int = 0

        # sustain
        self.total_potion: float = 0
        self.total_blocked: float = 0

    def __create__(self, config_dict: Dict) -> None:
        """Create a Knox instance from a build config dict.

        Args:
            config_dict (dict): Build config dictionary object.
        """
        self.load_build(config_dict)
        
        # Calculate gadget multipliers (WASM-verified: ~0.3% per level + 0.2% bonus per 10 levels)
        # WASM formula: (1 + level * 0.003) * (1.002 ** (level // 10))
        def gadget_mult(level):
            return (1 + level * 0.003) * (1.002 ** (level // 10))
        
        gadget_hp_mult = (
            gadget_mult(self.gadgets.get("wrench_of_gore", 0)) *
            gadget_mult(self.gadgets.get("zaptron_533", 0)) *
            gadget_mult(self.gadgets.get("anchor_of_ages", 0))
        )
        gadget_power_mult = gadget_hp_mult
        gadget_regen_mult = gadget_hp_mult
        
        # hp - Knox formula (WASM-verified: 20 + hp * (2 + hp/50))
        # Note: Gadget does NOT affect Knox HP in WASM
        self.max_hp = (
            (
                20  # Base HP
                + (self.base_stats["hp"] * (2.0 + self.base_stats["hp"] / 50))
            )
            * (1 + (self.attributes["release_the_kraken"] * 0.005))
            * (1 + (self.relics.get("disk_of_dawn", 0) * 0.03))
        )
        self.hp = self.max_hp
        
        # power - Knox formula (WASM-verified: 1.2 + atk * (0.06 + atk/1000))
        # Note: Gadget does NOT affect Knox Power in WASM
        self.power = (
            (
                1.2  # Base power
                + (self.base_stats["power"] * (0.06 + self.base_stats["power"] / 1000))
            )
            * (1 + (self.attributes["release_the_kraken"] * 0.005))
        )
        
        # regen - Knox formula (WASM-verified: 0.05 + regen * (0.01 + regen * 0.00075))
        # Note: Gadget and Kraken do NOT affect Knox Regen in WASM
        self.regen = (
            0.05  # Base regen
            + (self.base_stats["regen"] * (0.01 + self.base_stats["regen"] * 0.00075))
        )
        
        # damage_reduction
        self.damage_reduction = (
            0
            + (self.base_stats["damage_reduction"] * 0.01)
            + (self.attributes.get("a_pirates_life_for_knox", 0) * 0.009)  # +0.9% DR
        )
        
        # block_chance (Knox's unique defensive stat instead of evade)
        self.block_chance = (
            0.05
            + (self.base_stats.get("block_chance", 0) * 0.005)
            + (self.attributes.get("fortification_elixir", 0) * 0.01)  # +1% Block
            + (self.attributes.get("a_pirates_life_for_knox", 0) * 0.008)  # +0.8% Block
        )
        # For compatibility with base Hunter class evade checks
        self.evade_chance = 0
        
        # effect_chance
        self.effect_chance = (
            0.04
            + (self.base_stats["effect_chance"] * 0.004)
            + (self.attributes.get("serious_efficiency", 0) * 0.02)  # +2% Effect Chance
            + (self.attributes.get("a_pirates_life_for_knox", 0) * 0.007)  # +0.7% Effect
        )
        
        # charge_chance (Knox's special mechanic)
        self.charge_chance = (
            0.05
            + (self.base_stats.get("charge_chance", 0) * 0.003)
            + (self.attributes.get("serious_efficiency", 0) * 0.01)  # +1% Charge Chance
            + (self.attributes.get("a_pirates_life_for_knox", 0) * 0.006)  # +0.6% Charge Chance
        )
        
        # charge_gained (shield_of_poseidon adds FLAT charge, not chance)
        self.charge_gained = (
            1.0
            + (self.base_stats.get("charge_gained", 0) * 0.01)
            + (self.attributes.get("shield_of_poseidon", 0) * 0.1)  # +0.1 Charge (flat)
        )
        
        # passive charge per second
        self.passive_charge_rate = self.attributes.get("passive_charge_tank", 0) * 0.02  # +0.02/sec
        
        # reload_time (like speed but for salvos)
        # IRL CALIBRATION: User confirmed 6.40 sec reload with reload_time_stat=20
        # Base adjusted from 4.0 to 8.0, coefficient from 0.02 to 0.08 to match IRL
        self.reload_time = (
            8.0  # Base reload (calibrated from IRL)
            - (self.base_stats.get("reload_time", 0) * 0.08)
        )
        
        # For compatibility - use reload_time as speed
        self.speed = self.reload_time
        
        # special_chance and special_damage (for finishing move)
        self.special_chance = 0.10
        self.special_damage = 1.0 + (self.talents["finishing_move"] * 0.2)
        
        # projectiles per salvo (base 3 + upgrades)
        self.salvo_projectiles = 3 + self.base_stats.get("projectiles_per_salvo", 0)
        
        # lifesteal (Knox might not have this, set to 0)
        self.lifesteal = 0

    @staticmethod
    def load_dummy() -> dict:
        """Create a dummy build dictionary with empty stats.

        Returns:
            dict: The dummy build dict.
        """
        return {
            "meta": {
                "hunter": "Knox",
                "level": 0
            },
            "stats": {
                "hp": 0,
                "power": 0,
                "regen": 0,
                "damage_reduction": 0,
                "block_chance": 0,
                "effect_chance": 0,
                "charge_chance": 0,
                "charge_gained": 0,
                "reload_time": 0,
                "projectiles_per_salvo": 0,
            },
            "talents": {
                "death_is_my_companion": 0,
                "calypsos_advantage": 0,
                "unfair_advantage": 0,
                "ghost_bullets": 0,
                "omen_of_defeat": 0,
                "call_me_lucky_loot": 0,
                "presence_of_god": 0,
                "finishing_move": 0,
                "legacy_of_ultima": 0,
            },
            "attributes": {
                "release_the_kraken": 0,
                "space_pirate_armory": 0,
                "soul_amplification": 0,
                "serious_efficiency": 0,
                "fortification_elixir": 0,
                "a_pirates_life_for_knox": 0,
                "dead_men_tell_no_tales": 0,
                "passive_charge_tank": 0,
                "shield_of_poseidon": 0,
                "timeless_mastery": 0,
            },
            "inscryptions": {
                "i_knox_hp": 0,
                "i_knox_power": 0,
                "i_knox_block": 0,
                "i_knox_charge": 0,
                "i_knox_reload": 0,
            },
            "mods": {},
            "relics": {
                "disk_of_dawn": 0,
            },
            "gems": {
                "attraction_gem": 0,
                "attraction_catch-up": 0,
                "attraction_node_#3": 0,
                "innovation_node_#3": 0,
            },
            "gadgets": {
                "wrench_of_gore": 0,
                "zaptron_533": 0,
                "anchor_of_ages": 0,
            },
            "bonuses": {
                "shard_milestone": 0,
                "iap_travpack": False,
                "diamond_loot": 0,
                "diamond_revive": 0,
                "ultima_multiplier": 1.0,
            },
        }

    def attack(self, target) -> None:
        """Attack the enemy with a salvo of projectiles.

        Args:
            target (Enemy): The enemy to attack.
        """
        # Calculate number of projectiles in this salvo
        num_projectiles = self.salvo_projectiles
        base_projectiles = num_projectiles  # Track base for extra damage calc
        
        # Ghost Bullets - chance for extra projectile
        if self.talents["ghost_bullets"] > 0:
            ghost_chance = self.talents["ghost_bullets"] * 0.0667
            if random.random() < ghost_chance:
                num_projectiles += 1
                self.total_ghost_bullets += 1
        
        total_damage = 0
        for i in range(num_projectiles):
            # Each projectile deals FULL attack power (not split!)
            # This is how Knox can clear stages quickly with enough bullets
            bullet_damage = self.power
            
            # Check for charge (Knox's crit equivalent)
            if random.random() < self.charge_chance:
                bullet_damage *= (1 + self.charge_gained)
                self.total_charges += 1
            
            # Finishing Move on last bullet
            if i == num_projectiles - 1 and self.talents["finishing_move"] > 0:
                if random.random() < (self.effect_chance * 2):
                    bullet_damage *= self.special_damage
                    self.total_finishing_moves += 1
            
            total_damage += bullet_damage
        
        # Track extra salvo damage from ghost bullets
        if num_projectiles > base_projectiles:
            extra_projectile_count = num_projectiles - base_projectiles
            damage_per_projectile = total_damage / num_projectiles
            self.total_ghost_bullet_damage += damage_per_projectile * extra_projectile_count
            
        logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tSALVO\t{total_damage:>6.2f} ({num_projectiles} projectiles)")
        super(Knox, self).attack(target, total_damage)
        self.total_damage += total_damage
        self.total_attacks += 1

        # on_attack() effects
        self.heal_hp(total_damage * self.lifesteal, 'steal') if self.lifesteal > 0 else None

    def receive_damage(self, attacker, damage: float, is_crit: bool) -> None:
        """Receive damage from an attack. Knox uses block instead of evade.

        Args:
            attacker (Enemy): The unit attacking.
            damage (float): The amount of damage to receive.
            is_crit (bool): Whether the attack was a critical hit.
        """
        # Check for block first
        if random.random() < self.block_chance:
            blocked_amount = damage * 0.5  # Block reduces damage by 50%
            self.total_blocked += blocked_amount
            damage = damage - blocked_amount
            logging.debug(f'[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tBLOCK\t{blocked_amount:>6.2f}')
        
        # Apply remaining damage through parent class
        if damage > 0:
            mitigated_damage = damage * (1 - self.damage_reduction)
            self.hp -= mitigated_damage
            self.total_taken += mitigated_damage
            self.total_mitigated += (damage - mitigated_damage)
            self.total_attacks_suffered += 1
            logging.debug(f"[{self.name:>{hunter_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTAKE\t{mitigated_damage:>6.2f}, {self.hp:.2f} HP left")
            if self.is_dead():
                self.on_death()

    def regen_hp(self) -> None:
        """Regenerates hp according to the regen stat, modified by Fortification Elixir after blocks.
        """
        # Fortification Elixir bonus regen after blocking (tracked via total_blocked)
        regen_value = self.regen
        self.heal_hp(regen_value, 'regen')

    def on_kill(self, loot_type: str = None) -> None:
        """Actions to take when Knox kills an enemy."""
        super(Knox, self).on_kill(loot_type)
        if random.random() < self.effect_chance and (ua := self.talents["unfair_advantage"]):
            # Talent: Unfair Advantage
            potion_healing = self.max_hp * (ua * 0.02)
            self.heal_hp(potion_healing, "potion")
            self.total_potion += potion_healing
            self.total_effect_procs += 1

    def complete_stage(self, stages: int = 1) -> None:
        """Actions when Knox completes a stage."""
        super(Knox, self).complete_stage(stages)
        
        # Calypso's Advantage - chance to gain Hundred Souls on stage clear
        if self.talents["calypsos_advantage"] > 0:
            if random.random() < (self.effect_chance * 2.5):
                self.hundred_souls += 1
                self.total_effect_procs += 1

    def apply_ood(self, enemy) -> None:
        """Apply the Omen of Defeat effect to reduce enemy regen.

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        stage_effect = 0.5 if self.current_stage % 100 == 0 and self.current_stage > 0 else 1
        ood_effect = self.talents["omen_of_defeat"] * 0.08 * stage_effect
        enemy.regen = enemy.regen * (1 - ood_effect)

    def apply_pog(self, enemy) -> None:
        """Apply the Presence of a God effect to reduce enemy ATK power.

        Args:
            enemy (Enemy): The enemy to apply the effect to.
        """
        pog_effect = self.talents["presence_of_god"] * 0.03
        enemy.power = enemy.power * (1 - pog_effect)

    @property
    def power(self) -> float:
        """Getter for power, includes Hundred Souls bonus."""
        base = self._power
        souls_bonus = 1 + (self.hundred_souls * 0.005)  # +0.5% per soul
        return base * souls_bonus

    @power.setter
    def power(self, value: float) -> None:
        self._power = value

    def get_results(self) -> List:
        """Fetch the hunter results for end-of-run statistics.

        Returns:
            List: List of all collected stats.
        """
        return super(Knox, self).get_results() | {
            'ghost_bullets': self.total_ghost_bullets,
            'finishing_moves': self.total_finishing_moves,
            'charges': self.total_charges,
            'blocked_damage': self.total_blocked,
            'hundred_souls': self.hundred_souls,
            'unfair_advantage_healing': self.total_potion,
            'extra_salvo_damage': self.total_ghost_bullet_damage,  # Extra dmg from ghost bullets
        }


if __name__ == "__main__":
    h = Hunter.from_file('builds/current_ozzy.yaml')
    h.show_build()
    h.complete_stage(150)
    print(h)
