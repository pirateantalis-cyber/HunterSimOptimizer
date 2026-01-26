import logging
import random
from heapq import heapify
from heapq import heappush as hpush

from hunters import Borge, Hunter, Ozzy, Knox

unit_name_spacing: int = 7

# TODO: Verify whether Gothmogor's secondary attack contributes to enrage stacks


def multi_wasm(stage: int) -> float:
    """Stage scaling multiplier from CIFI Tools WASM (multiWasm function).
    
    WASM-verified: Uses ADDITIVE scaling with many breakpoints.
    After 350, exponential 1.01^(stage-350) is applied (not 1.02!).
    
    Args:
        stage: The current stage number.
        
    Returns:
        The multiplicative scaling factor for enemy stats.
    """
    # WASM formula from multiWasm function (lines 1304-1320 in release.dcmp)
    result = 1.0
    result += max((stage - 149) * 0.006, 0)
    result += max((stage - 199) * 0.006, 0)
    result += max((stage - 249) * 0.006, 0)
    result += max((stage - 299) * 0.006, 0)
    result += max((stage - 309) * 0.003, 0)
    result += max((stage - 319) * 0.003, 0)
    result += max((stage - 329) * 0.004, 0)
    result += max((stage - 339) * 0.004, 0)
    result += max((stage - 349) * 0.005, 0)
    result += max((stage - 359) * 0.005, 0)
    result += max((stage - 369) * 0.006, 0)
    result += max((stage - 379) * 0.006, 0)
    result += max((stage - 389) * 0.007, 0)
    
    # WASM applies max(result, 1.0) then multiplies by exponential
    result = max(result, 1.0)
    result *= 1.01 ** max(stage - 350, 0)  # WASM uses 1.01, not 1.02
    
    return result


def knox_scaling(stage: int) -> float:
    """Knox-specific stage scaling from CIFI Tools WASM (f_o function).
    
    Args:
        stage: The current stage number.
        
    Returns:
        The multiplicative scaling factor for Knox enemy stats.
    """
    if stage < 150:
        return 1.0
    
    result = 1.0
    
    # First breakpoint at 149
    if stage > 149:
        result *= 1 + (stage - 149) * 0.007
    
    # Additional breakpoints
    if stage > 199:
        result *= 1 + (stage - 199) * 0.007
    if stage > 249:
        result *= 1 + (stage - 249) * 0.007
    if stage > 299:
        result *= 1 + (stage - 299) * 0.007
    if stage > 349:
        result *= 1 + (stage - 349) * 0.007
    
    # Breakpoints every 20 stages after 360
    if stage > 369:
        result *= 1 + (stage - 369) * 0.007
    if stage > 389:
        result *= 1 + (stage - 389) * 0.007
    if stage > 409:
        result *= 1 + (stage - 409) * 0.007
    if stage > 429:
        result *= 1 + (stage - 429) * 0.007
    
    # Exponential scaling after stage 400
    if stage > 400:
        result *= 1.01 ** (stage - 400)
    
    return result

class Enemy:
    ### CREATION
    def __init__(self, name: str, hunter: Hunter, stage: int, sim) -> None:
        """Creates an Enemy instance.

        Args:
            name (str): Name of the enemy. Usually `E{stage}{number}`.
            hunter (Hunter): The hunter that this enemy is fighting.
            stage (int): The stage of the enemy, for stat selection.
            sim (Simulation): The simulation that this enemy is a part of.
        """
        self.__create__(name=name, **self.fetch_stats(hunter, stage))
        self.sim = sim
        self.on_create(hunter)

    def fetch_stats(self, hunter: Hunter, stage: int) -> dict:
        """Fetches the stats of the enemy using CIFI Tools formulas.

        Args:
            hunter (Hunter): The hunter that this enemy will be fighting, for enemy type selection.
            stage (int): The stage of the enemy, for stat selection.

        Raises:
            ValueError: If the hunter is not a valid hunter.

        Returns:
            dict: The stats of the enemy.
        """
        if isinstance(hunter, Borge):
            # CIFI formula: f_ca function from WASM
            # Note: Despite WASM showing f_m(2.85, tier), flat 2.85 matches IRL behavior
            stage_mult = multi_wasm(stage)
            post_100_mult = 2.85 if stage > 100 else 1.0
            # Stage 300 nerf
            stage_300_nerf = 0.9 if stage == 300 else 1.0
            
            return {
                'hp': (9 + stage * 4) * post_100_mult * stage_mult * stage_300_nerf,
                'power': (2.5 + stage * 0.7) * post_100_mult * stage_mult * stage_300_nerf,
                'regen': ((stage - 1) * 0.08 if stage > 1 else 0) * (1.052 if stage > 100 else 1.0) * stage_mult,
                'special_chance': 0.0322 + stage * 0.0004,
                'special_damage': min(1.212 + stage * 0.008, 2.5),
                'damage_reduction': 0,
                'evade_chance': 0.004 if stage > 100 else 0,
                'speed': 4.53 - stage * 0.006,
            }
        elif isinstance(hunter, Ozzy):
            # CIFI formula: Ozzy enemy stats from WASM (f_fa function)
            stage_mult = multi_wasm(stage)
            # WASM: 2.9^tier where tier = floor((stage-1)/100)
            tier = int((stage - 1) // 100)
            hp_tier_mult = 2.9 ** tier   # HP uses 2.9^tier
            power_tier_mult = 2.7 ** tier  # Power uses 2.7^tier
            xp_tier_mult = 1.25 ** tier  # XP uses 1.25^tier (for regen calc)
            # Stage 300 nerf
            stage_300_nerf = 0.94 if stage == 300 else 1.0
            is_boss = stage % 100 == 0 and stage > 0
            
            return {
                'hp': (11 + stage * 6) * hp_tier_mult * stage_mult * stage_300_nerf,
                'power': (1.35 + stage * 0.75) * power_tier_mult * stage_mult * stage_300_nerf,
                'regen': ((stage - 1) * 0.1 if stage > 0 else 0) * xp_tier_mult * stage_mult,
                'special_chance': 0.0994 + stage * 0.0006,
                'special_damage': min(1.03 + stage * 0.008, 2.5),  # WASM caps at 2.5
                'damage_reduction': 0,
                # WASM: evade = (tier-1)*0.01+0.01 for stage >= 100
                'evade_chance': max((tier - 1) * 0.01 + 0.01, 0) if stage >= 100 else 0,
                'speed': 3.20 - stage * 0.004,
            }
        elif isinstance(hunter, Knox):
            # CIFI formula: Knox enemy stats from WASM (uses knox_scaling)
            stage_mult = knox_scaling(stage)
            post_100_mult = 2.8 if stage > 100 else 1.0
            
            return {
                'hp': (10 + stage * 5) * post_100_mult * stage_mult,
                'power': (1.5 + stage * 0.65) * (2.6 if stage > 100 else 1.0) * stage_mult,
                'regen': ((stage - 1) * 0.09 if stage > 0 else 0) * (1.15 if stage > 100 else 1.0) * stage_mult,
                'special_chance': 0.075 + stage * 0.00055,
                'special_damage': 1.15 + stage * 0.0075,
                'damage_reduction': 0,
                'evade_chance': 0.006 if stage > 100 else 0,
                'speed': 3.80 - stage * 0.005,
                'effect_chance': 0.03 + stage * 0.0003,  # Knox enemies can have effects
            }
        else:
            raise ValueError(f'Unknown hunter: {hunter}')

    def __create__(self, name: str, hp: float, power: float, regen: float, damage_reduction: float, evade_chance: float, 
                 special_chance: float, special_damage: float, speed: float, **kwargs) -> None:
        """Creates an Enemy instance.

        Args:
            name (str): Name of the enemy. Usually `E{stage}{number}`.
            hp (float): Max HP value of the enemy.
            power (float): Power value of the enemy.
            regen (float): Regen value of the enemy.
            damage_reduction (float): Damage reduction value of the enemy.
            evade_chance (float): Evade chance value of the enemy.
            special_chance (float): Special chance (for now crit-only) value of the enemy.
            special_damage (float): Special damage value of the enemy.
            speed (float): Speed value of the enemy.
            **kwargs: Optional arguments for special attacks and secondary speeds.
                special (str): Name of the special attack of the enemy.
                speed2 (float): Speed of the secondary attack of the enemy.
        """
        self.name: str = name
        self.hp: float = float(hp)
        self.max_hp: float = float(hp)
        self.power: float = power
        self.regen: float = regen
        self.damage_reduction: float = damage_reduction
        self.evade_chance: float = evade_chance
        # patch 2024-01-24: enemies cant exceed 25% crit chance and 250% crit damage
        self.special_chance: float = min(special_chance, 0.25)
        self.special_damage: float = min(special_damage, 2.5)
        self.speed: float = speed
        self.has_special = False
        # Medusa anti-regen (applied dynamically during regen ticks with vectid multiplier)
        self.medusa_anti_regen: float = 0.0
        if isinstance(self, Boss): # regular boss enrage effect
            self.enrage_effect = kwargs['enrage_effect']
        if isinstance(self, Boss) and 'special' in kwargs: # boss enrage effect for secondary moves
            self.secondary_attack: str = kwargs['special']
            self.speed2: float = kwargs['speed2']
            self.enrage_effect2 = kwargs['enrage_effect2']
            self.has_special: bool = True
        self.stun_duration: float = 0
        self.missing_hp: float

    def on_create(self, hunter: Hunter) -> None:
        """Executes on creation effects such as Presence of God, Omen of Defeat, and Soul of Snek.

        Args:
            hunter (Hunter): The hunter that this enemy is fighting.
        """
        if 'presence_of_god' in hunter.talents:
            hunter.apply_pog(self)
        if 'omen_of_defeat' in hunter.talents:
            hunter.apply_ood(self)
        if 'soul_of_snek' in hunter.attributes:
            hunter.apply_snek(self)
        if 'gift_of_medusa' in hunter.attributes:
            hunter.apply_medusa(self)

    ### CONTENT
    def queue_initial_attack(self) -> None:
        """Queue the initial attacks of the enemy.
        """
        hpush(self.sim.queue, (round(self.sim.elapsed_time + self.speed, 3), 2, 'enemy'))
        if self.has_special:
            hpush(self.sim.queue, (round(self.sim.elapsed_time + self.speed2, 3), 2, 'enemy_special'))

    def attack(self, hunter: Hunter) -> None:
        """Attack the hunter.

        Args:
            hunter (Hunter): The hunter to attack.
        """
        if random.random() < self.special_chance:
            damage = self.power * self.special_damage
            is_crit = True
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f} (crit)")
        else:
            damage = self.power
            is_crit = False
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f}")
        hunter.receive_damage(self, damage, is_crit)

    def receive_damage(self, damage: float, is_reflected: bool = False) -> None:
        """Receive damage from an attack. Accounts for damage reduction and evade chance.

        Args:
            damage (float): Damage to receive.
        """
        if not is_reflected and random.random() < self.evade_chance:
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tEVADE")
        else:
            mitigated_damage = damage * (1 - self.damage_reduction)
            self.hp -= mitigated_damage
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tTAKE\t{mitigated_damage:>6.2f}, {self.hp:.2f} HP left")
            if self.is_dead():
                if is_reflected:
                    self.sim.hunter.helltouch_kills += 1
                self.on_death()

    def heal_hp(self, value: float, source: str) -> None:
        """Applies healing to hp from different sources. Accounts for overhealing.

        Args:
            value (float): The amount of hp to heal.
            source (str): The source of the healing. Valid: regen, lifesteal, life_of_the_hunt
        """
        effective_heal = min(value, self.missing_hp)
        self.hp += effective_heal
        logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\t{source.upper().replace('_', ' ')}\t{effective_heal:>6.2f}")

    def regen_hp(self) -> None:
        """Regenerates hp according to the regen stat.
        
        For Ozzy, applies Gift of Medusa anti-regen dynamically with Soul of Snek multiplier.
        WASM applies snek multiplier to medusa effect during each regen tick, not at spawn.
        
        WASM formula: hp = min(max_hp, hp + regen - medusa * snek_mult)
        """
        regen_value = self.regen
        
        # Apply Medusa anti-regen with Soul of Snek multiplier (Ozzy only)
        # WASM f_je: medusa_effect * snek_mult where snek_mult = (1 + soul_of_snek*0.15) when empowered_regen active
        if self.medusa_anti_regen > 0:
            hunter = self.sim.hunter
            if isinstance(hunter, Ozzy) and hunter.empowered_regen > 0:
                # WASM: slot[70] (soul_of_snek) provides the 0.15 multiplier during vectid buff
                snek_mult = 1 + (hunter.attributes["soul_of_snek"] * 0.15)
            else:
                snek_mult = 1.0
            regen_value -= self.medusa_anti_regen * snek_mult
        
        # Apply regen directly (can be negative if medusa > regen)
        # WASM: hp = min(max_hp, hp + net_regen)
        new_hp = min(self.max_hp, self.hp + regen_value)
        self.hp = new_hp
        
        # handle death from Ozzy's Gift of Medusa
        if self.is_dead():
            self.sim.hunter.medusa_kills += 1
            self.on_death()

    def stun(self, duration: float) -> None:
        """Apply a stun to the unit.

        Args:
            duration (float): The duration of the stun.
        """
        qe = [(p1, p2, u) for p1, p2, u in self.sim.queue if u == 'enemy'][0]
        self.sim.queue.remove(qe)
        hpush(self.sim.queue, (qe[0] + duration, qe[1], qe[2]))
        logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tSTUNNED\t{duration:>6.2f} sec")

    def is_boss(self) -> bool:
        """Check if the unit is a boss.

        Returns:
            bool: True if the unit is a boss, False otherwise.
        """
        return isinstance(self, Boss)

    def is_dead(self) -> bool:
        """Check if the unit is dead.

        Returns:
            bool: True if the unit is dead, False otherwise.
        """
        return self.hp <= 0

    def on_death(self, suppress_logging: bool = False) -> None:
        """Executes on death effects. For enemy units, that is mostly just removing them from the sim queue and incrementing hunter kills.
        """
        if not suppress_logging:
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tDIED")
        self.sim.queue = [(p1, p2, u) for p1, p2, u in self.sim.queue if u not in ['enemy', 'enemy_special']]
        heapify(self.sim.queue)
        self.sim.hunter.total_kills += 1
        self.sim.hunter.on_kill()

    def kill(self) -> None:
        """Kills the unit.

        Currently only used for Trample, which is a guaranteed kill.
        """
        self.hp = 0
        self.on_death(suppress_logging=True)

    ### UTILITY

    @property
    def missing_hp(self) -> float:
        """Calculates the missing hp of the unit.

        Returns:
            float: The missing hp of the unit.
        """
        return self.max_hp - self.hp

    def __str__(self) -> str:
        """Prints the stats of this Enemy's instance.

        Returns:
            str: The stats as a formatted string.
        """
        return f'[{self.name:>{unit_name_spacing}}]:\t[HP:{(str(round(self.hp, 2)) + "/" + str(round(self.max_hp, 2))):>18}] [AP:{self.power:>8.2f}] [Regen:{self.regen:>7.2f}] [DR: {self.damage_reduction:>6.2%}] [Evasion: {self.evade_chance:>6.2%}] [Effect: ------] [CHC: {self.special_chance:>6.2%}] [CHD: {self.special_damage:>5.2f}] [Speed:{self.speed:>5.2f}]{(f" [Speed2:{self.speed2:>6.2f}]") if self.has_special else ""}'


class Boss(Enemy):
    ### CREATION
    def __init__(self, name: str, hunter: Hunter, stage: int, sim) -> None:
        """Creates a Boss instance.

        Args:
            name (str): Name of the boss. Usually `E{stage}{number}`.
            hunter (Hunter): The hunter that this boss is fighting.
            stage (int): The stage of the boss, for stat selection.
            sim (Simulation): The simulation that this enemy is a part of.
        """
        super(Boss, self).__init__(name, hunter, stage, sim)
        self.base_power: float = self.power  # Store base power for enrage calculation
        self.enrage_stacks: int = 0
        self.harden_ticks_left: int = 0 # Exoscarab secondary attack mechanic
        self.max_enrage: bool = False

    def fetch_stats(self, hunter: Hunter, stage: int) -> dict:
        """Fetches the stats of the boss using CIFI Tools formulas.

        Boss stats are derived from enemy stats with specific multipliers:
        - Borge: HP=90x, Power=3.63x
        - Ozzy: HP=48x, Power=3.25x  
        - Knox: HP=120x, Power=4.0x

        Args:
            hunter (Hunter): The hunter that this boss is fighting.
            stage (int): The stage of the boss, for stat selection.

        Returns:
            dict: The stats of the boss.
        """
        # Get base enemy stats first
        enemy_stats = Enemy.fetch_stats(self, hunter, stage)
        
        if isinstance(hunter, Borge):
            # CIFI formula: Borge boss multipliers
            # HP: 90x enemy HP, Power: 3.63x enemy power
            base_speed = 4.53 - stage * 0.006
            base_speed2 = base_speed * 1.8  # Secondary attack is slower
            
            result = {
                'hp': enemy_stats['hp'] * 90,
                'power': enemy_stats['power'] * 3.63,
                'regen': enemy_stats['regen'] * 2.5,  # Boss regen multiplier
                'special_chance': min(enemy_stats['special_chance'] + 0.08, 0.25),  # Capped at 25%
                'special_damage': min(enemy_stats['special_damage'] + 0.5, 2.5),  # Capped at 250%
                'damage_reduction': min(0.05 + stage * 0.0004, 0.25),
                'evade_chance': 0.004 if stage > 100 else 0,
                'speed': base_speed * 2.1,  # Boss attacks slower
                'enrage_effect': base_speed / 200,  # Speed reduction per stack
                'enrage_effect2': 0,
            }
            
            # Add Gothmorgor secondary attack for stage 200+
            if stage >= 200:
                result['speed2'] = base_speed2 * 2.1
                result['special'] = 'gothmorgor'
                result['enrage_effect2'] = base_speed2 / 200
            
            return result
            
        elif isinstance(hunter, Ozzy):
            # CIFI formula: Ozzy boss multipliers from WASM f_fa
            # HP: 48x enemy HP, Power: 3.0x enemy power
            base_speed = 3.20 - stage * 0.004
            
            result = {
                'hp': enemy_stats['hp'] * 48,
                'power': enemy_stats['power'] * 3.0,  # WASM: select_if(3.0, 1.0, is_boss)
                'regen': enemy_stats['regen'] * 6,  # Ozzy boss has high regen
                'special_chance': min(enemy_stats['special_chance'] + 0.1, 0.25),  # WASM: +0.1 for boss
                'special_damage': min(enemy_stats['special_damage'], 2.5),  # Already capped
                'damage_reduction': min(0.05 + stage * 0.0004, 0.25),
                'evade_chance': 0.01 if stage > 100 else 0,
                'speed': base_speed * 2.45,  # WASM: select_if(2.45, 1.0, is_boss)
                'enrage_effect': base_speed / 200,
                'enrage_effect2': 0,
            }
            
            # Add Exoscarab secondary attack for stage 200+
            # WASM verified: 60 second cooldown between hardens, 5 second duration
            # Total cycle = 65 seconds, uptime = 7.7% (not 22.5% from old 22.19s speed2)
            if stage >= 200:
                result['speed2'] = 60.0  # WASM: Fixed 60 second cooldown
                result['special'] = 'exoscarab'
                # Exoscarab doesn't reduce speed on secondary
            
            return result
            
        elif isinstance(hunter, Knox):
            # CIFI formula: Knox boss multipliers
            # HP: 120x enemy HP, Power: 4.0x enemy power
            base_speed = 3.80 - stage * 0.005
            
            result = {
                'hp': enemy_stats['hp'] * 120,
                'power': enemy_stats['power'] * 4.0,
                'regen': enemy_stats['regen'] * 3,
                'special_chance': min(enemy_stats['special_chance'] + 0.06, 0.25),
                'special_damage': min(enemy_stats['special_damage'] + 0.4, 2.5),
                'damage_reduction': min(0.05 + stage * 0.0004, 0.25),
                'evade_chance': 0.006 if stage > 100 else 0,
                'speed': base_speed * 2.0,
                'enrage_effect': base_speed / 200,
                'enrage_effect2': 0,
            }
            
            return result
            
        else:
            raise ValueError(f'Unknown hunter: {hunter}')

    def attack(self, hunter: Hunter) -> None:
        """Attack the hunter. Uses base_power * 3 at 200+ enrage stacks per CIFI.

        Args:
            hunter (Hunter): The hunter to attack.
        """
        super(Boss, self).attack(hunter)
        self.enrage_stacks += 1
        logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tENRAGE\t{self.enrage_stacks:>6.2f} stacks")
        # WASM: Max enrage triggers when stacks > 200 (not >= 200)
        if self.enrage_stacks > 200 and not self.max_enrage:
            self.max_enrage = True
            self.power = self.base_power * 3  # CIFI: 3x base power at max enrage
            self.special_chance = 1  # CIFI: 100% crit at max enrage
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tMAX ENRAGE (x3 base damage, 100% crit chance)")

    def attack_special(self, hunter: Hunter) -> None:
        """Attack the hunter with a special attack.

        Args:
            hunter (Hunter): The hunter to attack.
        """
        if self.secondary_attack == 'gothmorgor':
            if random.random() < self.special_chance:
                damage = self.power * self.special_damage
                is_crit = True
                logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f} SECONDARY (crit)")
            else:
                damage = self.power
                is_crit = False
                logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tATTACK\t{damage:>6.2f} SECONDARY")
            hunter.receive_damage(self, damage, is_crit)
            self.enrage_stacks += 1
        elif self.secondary_attack == 'exoscarab':
            # WASM: Enrage stacks are added when harden ENDS, not when it starts
            self.apply_harden(True)
        else:
            raise ValueError(f'Unknown special attack: {self.secondary_attack}')

    def regen_hp(self) -> None:
        """Regenerates hp according to the regen stat. Also deals with the Harden effect of the Exoscarab boss.
        """
        regen_value = self.regen
        if self.harden_ticks_left > 0:
            # Harden effect: 3x regen for 5 ticks
            for _ in range(3):
                self.heal_hp(regen_value, 'regen')
            self.harden_ticks_left -= 1
            if self.harden_ticks_left == 0:
                self.apply_harden(False)
        else:
            self.heal_hp(regen_value, 'regen')
        # handle death from Ozzy's Gift of Medusa
        if self.is_dead():
            self.on_death()

    def apply_harden(self, enable: bool) -> None:
        """Handles Harden effect application and removal on the boss.

        Args:
            enable (bool): Whether to enable or disable the Harden effect.
        """
        if enable:
            self.harden_ticks_left = 5
            self.previous_dr = self.damage_reduction
            self.damage_reduction = 0.95
        else:
            # WASM: +5 enrage stacks added when harden ends
            self.enrage_stacks += 5
            self.damage_reduction = self.previous_dr
            logging.debug(f"[{self.name:>{unit_name_spacing}}][@{self.sim.elapsed_time:>5}]:\tHARDEN ended, +5 enrage (now {self.enrage_stacks})")

    def on_death(self) -> None:
        """Extends the Enemy::on_death() method to log enrage stacks on death.
        """
        super(Boss, self).on_death()
        self.sim.hunter.enrage_log.append(self.enrage_stacks)

    @property
    def speed(self) -> float:
        """Calculates the speed of the boss, taking enrage stacks into account.
        """
        return max((self._speed - self.enrage_effect * self.enrage_stacks), 0.5)

    @speed.setter
    def speed(self, value: float) -> None:
        """Sets the speed of the boss.

        Args:
            value (float): The speed of the boss.
        """
        self._speed = value

    @property
    def speed2(self) -> float:
        """Calculates the speed2 of the boss, taking enrage stacks into account.
        """
        return max((self._speed2 - self.enrage_effect2 * self.enrage_stacks), 0.5)

    @speed2.setter
    def speed2(self, value: float) -> None:
        """Sets the speed2 of the boss.

        Args:
            value (float): The speed2 of the boss.
        """
        self._speed2 = value


if __name__ == "__main__":
    b = Borge('./builds/current_borge.yaml')
    b.complete_stage(200)
    boss = Boss('E200', b, 200, None) 
    print(boss)
    boss.enrage_stacks = 11
    print(boss)
    e = Enemy('E199', b, 199, None)
    print(e)
