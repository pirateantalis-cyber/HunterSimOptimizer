"""
Hunter Sim GUI - Multi-Hunter Build Optimizer
==============================================
A GUI application with separate tabs for each hunter (Borge, Knox, Ozzy).
Each hunter has sub-tabs for Build Configuration, Run Optimization, and Results.
Automatically loads/saves builds from IRL Builds folder.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import itertools
import queue
import time
import math
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor
import statistics
from collections import Counter
import copy
import sys
import os
import json
from tkinter import filedialog
from pathlib import Path

# Try to import PIL for portrait images
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hunters import Borge, Knox, Ozzy, Hunter
from sim import SimulationManager, Simulation, sim_worker

# Import classes from the original gui.py
from gui import (
    BuildResult, BuildGenerator, EvolutionaryOptimizer, OptimizationMode,
    RUST_AVAILABLE
)

# Try to import Rust simulator
try:
    import rust_sim
except ImportError:
    pass


# Path to IRL Builds folder
IRL_BUILDS_PATH = Path(__file__).parent / "IRL Builds"

# Path to assets folder (portraits are in parent directory)
ASSETS_PATH = Path(__file__).parent.parent  # Parent of hunter-sim folder

# Hunter color themes and portraits
HUNTER_COLORS = {
    "Borge": {
        "primary": "#DC3545",      # Red
        "light": "#F8D7DA",        # Light red/pink
        "dark": "#721C24",         # Dark red
        "text": "#FFFFFF",         # White text on dark
        "bg": "#FFF5F5",           # Very light red background
        "portrait": "hunter_borge-GMACLV3e.png",
    },
    "Knox": {
        "primary": "#0D6EFD",      # Blue
        "light": "#CFE2FF",        # Light blue
        "dark": "#084298",         # Dark blue
        "text": "#FFFFFF",         # White text on dark
        "bg": "#F0F7FF",           # Very light blue background
        "portrait": "hunter_knox-DfvSfjhv.png",
    },
    "Ozzy": {
        "primary": "#198754",      # Green
        "light": "#D1E7DD",        # Light green
        "dark": "#0F5132",         # Dark green
        "text": "#FFFFFF",         # White text on dark
        "bg": "#F0FFF4",           # Very light green background
        "portrait": "hunter_ozzy-BYN3S8hK.png",
    },
}


class HunterTab:
    """Manages a single hunter's tab with sub-tabs for Build, Run, and Results."""
    
    def __init__(self, parent_notebook: ttk.Notebook, hunter_name: str, hunter_class, app: 'MultiHunterGUI'):
        self.hunter_name = hunter_name
        self.hunter_class = hunter_class
        self.app = app
        self.colors = HUNTER_COLORS[hunter_name]
        
        # Create the main frame for this hunter
        self.frame = ttk.Frame(parent_notebook)
        parent_notebook.add(self.frame, text=f"  {hunter_name}  ")
        
        # Load portrait image
        self.portrait_image = None
        self.portrait_photo = None
        self._load_portrait()
        
        # State
        self.level = tk.IntVar(value=1)
        self.results: List[BuildResult] = []
        self.result_queue = queue.Queue()
        self.is_running = False
        self.stop_event = threading.Event()
        self.optimization_start_time = 0
        
        # Best tracking
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_gen = 0
        self.best_avg_gen = 0
        
        # Input references
        self.stat_entries: Dict[str, tk.Entry] = {}
        self.talent_entries: Dict[str, tk.Entry] = {}
        self.attribute_entries: Dict[str, tk.Entry] = {}
        self.inscryption_entries: Dict[str, tk.Entry] = {}
        self.relic_entries: Dict[str, tk.Entry] = {}
        self.gem_entries: Dict[str, tk.Entry] = {}
        self.mod_vars: Dict[str, tk.BooleanVar] = {}
        self.gadget_entries: Dict[str, tk.Entry] = {}
        self.bonus_entries: Dict[str, tk.Entry] = {}
        self.bonus_vars: Dict[str, tk.BooleanVar] = {}
        
        # IRL tracking
        self.irl_max_stage = tk.IntVar(value=0)
        self.irl_baseline_result = None  # Stores sim result for user's current build
        
        # Create container for portrait + content
        self.container = ttk.Frame(self.frame)
        self.container.pack(fill=tk.BOTH, expand=True)
        
        # Portrait panel on left (if PIL available)
        if PIL_AVAILABLE and self.portrait_photo:
            portrait_frame = tk.Frame(self.container, bg=self.colors["primary"], width=238)  # 15% thinner (280 * 0.85)
            portrait_frame.pack(side=tk.LEFT, fill=tk.Y, padx=0, pady=0)
            portrait_frame.pack_propagate(False)
            
            # Hunter name at top
            name_label = tk.Label(portrait_frame, text=self.hunter_name.upper(), 
                                  font=('Arial', 18, 'bold'), fg=self.colors["text"], 
                                  bg=self.colors["primary"])
            name_label.pack(pady=(15, 10))
            
            # Portrait image - centered and larger
            portrait_label = tk.Label(portrait_frame, image=self.portrait_photo, 
                                      bg=self.colors["primary"])
            portrait_label.pack(pady=10, padx=15, expand=True)
        
        # Create sub-notebook
        self.sub_notebook = ttk.Notebook(self.container)
        self.sub_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create sub-tabs
        self.build_frame = ttk.Frame(self.sub_notebook)
        self.run_frame = ttk.Frame(self.sub_notebook)
        self.advisor_frame = ttk.Frame(self.sub_notebook)
        self.results_frame = ttk.Frame(self.sub_notebook)
        
        self.sub_notebook.add(self.build_frame, text="📝 Build")
        self.sub_notebook.add(self.run_frame, text="🚀 Run")
        self.sub_notebook.add(self.advisor_frame, text="🎯 Advisor")
        self.sub_notebook.add(self.results_frame, text="🏆 Best")
        
        self._create_build_tab()
        self._create_run_tab()
        self._create_advisor_tab()
        self._create_results_tab()
        
        # Try to auto-load IRL build
        self._auto_load_build()
    
    def _get_build_file_path(self) -> Path:
        """Get the path to this hunter's IRL build file."""
        return IRL_BUILDS_PATH / f"my_{self.hunter_name.lower()}_build.json"
    
    def _load_portrait(self):
        """Load the hunter's portrait image."""
        if not PIL_AVAILABLE:
            return
        
        portrait_file = ASSETS_PATH / self.colors["portrait"]
        if portrait_file.exists():
            try:
                img = Image.open(portrait_file)
                # Resize to fit smaller sidebar (220px with padding) - 15% smaller
                # Images are horizontal format (717x362), so scale by width
                target_width = 220
                ratio = target_width / img.width
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                self.portrait_image = img
                self.portrait_photo = ImageTk.PhotoImage(img)
            except Exception as e:
                print(f"Failed to load portrait for {self.hunter_name}: {e}")
    
    def _format_attribute_label(self, attr_key: str) -> str:
        """Format attribute key to readable label with smart abbreviations."""
        # Custom abbreviations for long attribute names
        abbreviations = {
            # Ozzy blessings
            "blessings_of_the_cat": "Bless. Cat",
            "blessings_of_the_scarab": "Bless. Scarab",
            "blessings_of_the_sisters": "Bless. Sisters",
            # Borge souls
            "soul_of_athena": "Soul Athena",
            "soul_of_hermes": "Soul Hermes", 
            "soul_of_the_minotaur": "Soul Minotaur",
            "soul_of_ares": "Soul Ares",
            "soul_of_snek": "Soul Snek",
            # Long Ozzy attributes
            "extermination_protocol": "Extermn. Protocol",
            "living_off_the_land": "Living Off Land",
            "shimmering_scorpion": "Shimmer Scorpion",
            # Long Knox attributes
            "a_pirates_life_for_knox": "Pirate Life",
            "dead_men_tell_no_tales": "Dead Men Tales",
            "release_the_kraken": "Release Kraken",
            "space_pirate_armory": "Pirate Armory",
            "serious_efficiency": "Serious Effic.",
            "fortification_elixir": "Fort. Elixir",
            "passive_charge_tank": "Passive Charge",
            "shield_of_poseidon": "Shield Poseidon",
            "soul_amplification": "Soul Amplify",
            # Long Borge attributes
            "helltouch_barrier": "Helltouch Barrier",
            "lifedrain_inhalers": "Lifedrain Inhalers",
            "explosive_punches": "Explo. Punches",
            "superior_sensors": "Superior Sensors",
            "essence_of_ylith": "Ess. Ylith",
            "weakspot_analysis": "Weakspot Analy.",
        }
        
        if attr_key in abbreviations:
            return abbreviations[attr_key]
        
        # Default formatting
        label = attr_key.replace("_", " ").title()
        if len(label) > 18:
            label = label[:17] + "…"
        return label
    
    def _get_hunter_costs(self) -> Dict:
        """Get the costs dictionary for the current hunter."""
        if self.hunter_name == "Borge":
            return Borge.costs
        elif self.hunter_name == "Ozzy":
            return Ozzy.costs
        elif self.hunter_name == "Knox":
            return Knox.costs
        return {}
    
    def _auto_load_build(self):
        """Automatically load the IRL build if it exists."""
        build_file = self._get_build_file_path()
        if build_file.exists():
            try:
                with open(build_file, 'r') as f:
                    config = json.load(f)
                self._load_config(config)
                self.app._log(f"✅ Auto-loaded {self.hunter_name} build from {build_file.name}")
            except Exception as e:
                self.app._log(f"⚠️ Failed to load {self.hunter_name} build: {e}")
    
    def _auto_save_build(self):
        """Automatically save the current build to IRL Builds folder."""
        build_file = self._get_build_file_path()
        try:
            config = self._get_save_config()
            IRL_BUILDS_PATH.mkdir(exist_ok=True)
            with open(build_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Auto-save failed for {self.hunter_name}: {e}")
    
    def _get_save_config(self) -> Dict:
        """Get the current configuration in save format."""
        config = {
            "hunter": self.hunter_name,
            "level": self.level.get(),
            "irl_max_stage": self.irl_max_stage.get(),
            "stats": {},
            "talents": {},
            "attributes": {},
            "inscryptions": {},
            "relics": {},
            "gems": {},
            "mods": {},
            "gadgets": {},
            "bonuses": {}
        }
        
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
        
        for key, entry in self.talent_entries.items():
            try:
                config["talents"][key] = int(entry.get())
            except ValueError:
                config["talents"][key] = 0
        
        for key, entry in self.attribute_entries.items():
            try:
                config["attributes"][key] = int(entry.get())
            except ValueError:
                config["attributes"][key] = 0
                
        for key, entry in self.inscryption_entries.items():
            try:
                config["inscryptions"][key] = int(entry.get())
            except ValueError:
                config["inscryptions"][key] = 0
                
        for key, entry in self.relic_entries.items():
            try:
                config["relics"][key] = int(entry.get())
            except ValueError:
                config["relics"][key] = 0
                
        for key, entry in self.gem_entries.items():
            try:
                config["gems"][key] = int(entry.get())
            except ValueError:
                config["gems"][key] = 0
                
        for key, var in self.mod_vars.items():
            config["mods"][key] = var.get()
        
        # Gadgets
        for key, entry in self.gadget_entries.items():
            try:
                config["gadgets"][key] = int(entry.get())
            except ValueError:
                config["gadgets"][key] = 0
        
        # Bonuses
        for key, entry in self.bonus_entries.items():
            try:
                if key == "ultima_multiplier":
                    config["bonuses"][key] = float(entry.get())
                else:
                    config["bonuses"][key] = int(entry.get())
            except ValueError:
                config["bonuses"][key] = 0 if key != "ultima_multiplier" else 1.0
        
        for key, var in self.bonus_vars.items():
            config["bonuses"][key] = var.get()
        
        return config
    
    def _load_config(self, config: Dict):
        """Load a configuration into the UI."""
        if config.get("level"):
            self.level.set(config["level"])
        
        if config.get("irl_max_stage"):
            self.irl_max_stage.set(config["irl_max_stage"])
        
        for key, value in config.get("stats", {}).items():
            if key in self.stat_entries:
                self.stat_entries[key].delete(0, tk.END)
                self.stat_entries[key].insert(0, str(value))
        
        for key, value in config.get("talents", {}).items():
            if key in self.talent_entries:
                self.talent_entries[key].delete(0, tk.END)
                self.talent_entries[key].insert(0, str(value))
        
        for key, value in config.get("attributes", {}).items():
            if key in self.attribute_entries:
                self.attribute_entries[key].delete(0, tk.END)
                self.attribute_entries[key].insert(0, str(value))
        
        for key, value in config.get("inscryptions", {}).items():
            if key in self.inscryption_entries:
                self.inscryption_entries[key].delete(0, tk.END)
                self.inscryption_entries[key].insert(0, str(value))
        
        for key, value in config.get("relics", {}).items():
            if key in self.relic_entries:
                self.relic_entries[key].delete(0, tk.END)
                self.relic_entries[key].insert(0, str(value))
        
        for key, value in config.get("gems", {}).items():
            if key in self.gem_entries:
                self.gem_entries[key].delete(0, tk.END)
                self.gem_entries[key].insert(0, str(value))
        
        for key, value in config.get("mods", {}).items():
            if key in self.mod_vars:
                self.mod_vars[key].set(bool(value))
        
        # Gadgets
        for key, value in config.get("gadgets", {}).items():
            if key in self.gadget_entries:
                self.gadget_entries[key].delete(0, tk.END)
                self.gadget_entries[key].insert(0, str(value))
        
        # Bonuses
        for key, value in config.get("bonuses", {}).items():
            if key in self.bonus_entries:
                self.bonus_entries[key].delete(0, tk.END)
                self.bonus_entries[key].insert(0, str(value))
            if key in self.bonus_vars:
                self.bonus_vars[key].set(bool(value))
    
    def _create_build_tab(self):
        """Create the build configuration sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.build_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Build Configuration", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Level at top
        top_frame = ttk.Frame(self.build_frame)
        top_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Label(top_frame, text="Level:", font=('Arial', 12, 'bold')).pack(side=tk.LEFT, padx=5)
        level_spin = ttk.Spinbox(top_frame, textvariable=self.level, from_=1, to=600, width=6)
        level_spin.pack(side=tk.LEFT, padx=5)
        level_spin.bind('<FocusOut>', lambda e: self._auto_save_build())
        level_spin.bind('<<Increment>>', lambda e: self._update_max_points_label())
        level_spin.bind('<<Decrement>>', lambda e: self._update_max_points_label())
        self.level.trace_add('write', lambda *args: self._update_max_points_label())
        
        self.max_points_label = ttk.Label(top_frame, text=f"(Max Talents: {self.level.get()}, Max Attrs: {self.level.get()*3})", 
                  font=('Arial', 9, 'italic'))
        self.max_points_label.pack(side=tk.LEFT, padx=10)
        
        # IRL Max Stage - for tracking real-world performance
        ttk.Separator(top_frame, orient='vertical').pack(side=tk.LEFT, padx=15, fill='y', pady=2)
        ttk.Label(top_frame, text="IRL Max Stage:", font=('Arial', 10)).pack(side=tk.LEFT, padx=5)
        irl_stage_spin = ttk.Spinbox(top_frame, textvariable=self.irl_max_stage, from_=0, to=999, width=5)
        irl_stage_spin.pack(side=tk.LEFT, padx=5)
        irl_stage_spin.bind('<FocusOut>', lambda e: self._auto_save_build())
        
        # Manual save button
        ttk.Button(top_frame, text="💾 Save", command=self._manual_save).pack(side=tk.RIGHT, padx=5)
        
        # Content frame (no scrollbar - window is large enough)
        self.scrollable_frame = ttk.Frame(self.build_frame)
        self.scrollable_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self._populate_build_fields()
    
    def _update_max_points_label(self):
        """Update the max points label when level changes."""
        level = self.level.get()
        max_talents = level
        max_attrs = level * 3
        self.max_points_label.configure(text=f"(Max Talents: {max_talents}, Max Attrs: {max_attrs})")
    
    def _manual_save(self):
        """Manual save with confirmation."""
        self._auto_save_build()
        messagebox.showinfo("Saved", f"{self.hunter_name} build saved to IRL Builds folder!")
    
    def _populate_build_fields(self):
        """Populate the build configuration fields in a 2-column layout."""
        dummy = self.hunter_class.load_dummy()
        
        # Configure 2-column layout for scrollable_frame
        self.scrollable_frame.columnconfigure(0, weight=1)
        self.scrollable_frame.columnconfigure(1, weight=1)
        
        # === LEFT COLUMN (column 0) ===
        left_row = 0
        
        # Stats Section (LEFT)
        stats_frame = ttk.LabelFrame(self.scrollable_frame, text="📊 Main Stats (Upgrade LEVELS)")
        stats_frame.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        if self.hunter_name == "Knox":
            stat_names = {
                "hp": "HP", "power": "Power", "regen": "Regen",
                "damage_reduction": "DR", "block_chance": "Block",
                "effect_chance": "Effect", "charge_chance": "Charge",
                "charge_gained": "Charge Gain", "reload_time": "Reload",
                "projectiles_per_salvo": "Projectiles"
            }
        else:
            stat_names = {
                "hp": "HP", "power": "Power", "regen": "Regen",
                "damage_reduction": "DR", "evade_chance": "Evade",
                "effect_chance": "Effect", "special_chance": "Special",
                "special_damage": "Spec Dmg", "speed": "Speed"
            }
        
        for i, (stat_key, stat_label) in enumerate(stat_names.items()):
            r, c = divmod(i, 3)  # 3 columns for stats
            frame = ttk.Frame(stats_frame)
            frame.grid(row=r, column=c, padx=4, pady=1, sticky="w")
            ttk.Label(frame, text=f"{stat_label}:", width=12).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=5)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            self.stat_entries[stat_key] = entry
        
        # Talents Section (LEFT)
        talents_frame = ttk.LabelFrame(self.scrollable_frame, text="⭐ Talents")
        talents_frame.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        # Get max levels from hunter's costs
        hunter_costs = self._get_hunter_costs()
        
        talent_items = list(dummy.get("talents", {}).items())
        num_talent_cols = 3
        for i, (talent_key, talent_val) in enumerate(talent_items):
            r, c = divmod(i, num_talent_cols)
            frame = ttk.Frame(talents_frame)
            frame.grid(row=r, column=c, padx=2, pady=1, sticky="w")
            label = talent_key.replace("_", " ").title()
            if len(label) > 18:
                label = label[:17] + "…"
            ttk.Label(frame, text=f"{label}:", width=18).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Show max level
            max_lvl = hunter_costs.get("talents", {}).get(talent_key, {}).get("max", "?")
            max_text = "∞" if max_lvl == float("inf") else str(max_lvl)
            ttk.Label(frame, text=f"/{max_text}", width=4).pack(side=tk.LEFT)
            self.talent_entries[talent_key] = entry
        
        # Inscryptions Section (LEFT)
        inscr_frame = ttk.LabelFrame(self.scrollable_frame, text="📜 Inscryptions")
        inscr_frame.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        inscr_tooltips = self._get_inscryption_tooltips()
        for i, (inscr_key, inscr_val) in enumerate(dummy.get("inscryptions", {}).items()):
            r, c = divmod(i, 3)  # 3 columns
            frame = ttk.Frame(inscr_frame)
            frame.grid(row=r, column=c, padx=2, pady=1, sticky="w")
            tooltip = inscr_tooltips.get(inscr_key, inscr_key.upper())
            ttk.Label(frame, text=f"{inscr_key} ({tooltip}):", width=16).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            self.inscryption_entries[inscr_key] = entry
        
        # Relics Section (LEFT)
        relics_frame = ttk.LabelFrame(self.scrollable_frame, text="🏆 Relics")
        relics_frame.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        for i, (relic_key, relic_val) in enumerate(dummy.get("relics", {}).items()):
            frame = ttk.Frame(relics_frame)
            frame.grid(row=i, column=0, padx=4, pady=1, sticky="w")
            label = relic_key.replace("_", " ").title()[:24]
            ttk.Label(frame, text=f"{label}:", width=24).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=4)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            self.relic_entries[relic_key] = entry
        
        # === RIGHT COLUMN (column 1) ===
        right_row = 0
        
        # Attributes Section (RIGHT)
        attrs_frame = ttk.LabelFrame(self.scrollable_frame, text="🔮 Attributes")
        attrs_frame.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        attr_items = list(dummy.get("attributes", {}).items())
        num_attr_cols = 3
        for i, (attr_key, attr_val) in enumerate(attr_items):
            r, c = divmod(i, num_attr_cols)
            frame = ttk.Frame(attrs_frame)
            frame.grid(row=r, column=c, padx=2, pady=1, sticky="w")
            label = self._format_attribute_label(attr_key)
            ttk.Label(frame, text=f"{label}:", width=18).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            # Show max level
            max_lvl = hunter_costs.get("attributes", {}).get(attr_key, {}).get("max", "?")
            max_text = "∞" if max_lvl == float("inf") else str(max_lvl)
            ttk.Label(frame, text=f"/{max_text}", width=4).pack(side=tk.LEFT)
            self.attribute_entries[attr_key] = entry
        
        # Gems Section (RIGHT)
        gems_frame = ttk.LabelFrame(self.scrollable_frame, text="💎 Gems")
        gems_frame.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        for i, (gem_key, gem_val) in enumerate(dummy.get("gems", {}).items()):
            r, c = divmod(i, 3)  # 3 columns
            frame = ttk.Frame(gems_frame)
            frame.grid(row=r, column=c, padx=2, pady=1, sticky="w")
            label = gem_key.replace("_", " ").replace("#", "").title()[:18]
            ttk.Label(frame, text=f"{label}:", width=18).pack(side=tk.LEFT)
            entry = ttk.Entry(frame, width=3)
            entry.insert(0, "0")
            entry.bind('<FocusOut>', lambda e: self._auto_save_build())
            entry.pack(side=tk.LEFT)
            self.gem_entries[gem_key] = entry
        
        # Mods Section (RIGHT)
        if dummy.get("mods"):
            mods_frame = ttk.LabelFrame(self.scrollable_frame, text="⚙️ Mods")
            mods_frame.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
            right_row += 1
            
            for i, (mod_key, mod_val) in enumerate(dummy.get("mods", {}).items()):
                var = tk.BooleanVar(value=False)
                label = mod_key.replace("_", " ").title()
                cb = ttk.Checkbutton(mods_frame, text=label, variable=var,
                                     command=self._auto_save_build)
                cb.grid(row=i // 2, column=i % 2, padx=10, pady=5, sticky="w")
                self.mod_vars[mod_key] = var
        
        # Gadgets Section (LEFT - after Relics) - Each hunter has their own gadget
        gadgets_frame = ttk.LabelFrame(self.scrollable_frame, text="🔧 Gadget")
        gadgets_frame.grid(row=left_row, column=0, sticky="nsew", padx=(10, 5), pady=5)
        left_row += 1
        
        # Each hunter has exactly one gadget
        hunter_gadgets = {
            "Borge": ("wrench_of_gore", "Wrench of Gore"),
            "Ozzy": ("zaptron_533", "Zaptron 533"),
            "Knox": ("anchor_of_ages", "Anchor of Ages"),
        }
        gadget_key, gadget_label = hunter_gadgets[self.hunter_name]
        frame = ttk.Frame(gadgets_frame)
        frame.grid(row=0, column=0, padx=4, pady=1, sticky="w")
        ttk.Label(frame, text=f"{gadget_label}:", width=14).pack(side=tk.LEFT)
        entry = ttk.Entry(frame, width=4)
        entry.insert(0, "0")
        entry.bind('<FocusOut>', lambda e: self._auto_save_build())
        entry.pack(side=tk.LEFT)
        self.gadget_entries[gadget_key] = entry
        
        # Note about Global Bonuses (RIGHT - after Mods)
        bonuses_note_frame = ttk.LabelFrame(self.scrollable_frame, text="💎 Global Bonuses")
        bonuses_note_frame.grid(row=right_row, column=1, sticky="nsew", padx=(5, 10), pady=5)
        right_row += 1
        
        note_label = ttk.Label(bonuses_note_frame, 
                              text="ℹ️ Bonuses are shared across all hunters.\n   Set them in the Control tab.",
                              font=('Arial', 9), foreground='gray')
        note_label.pack(padx=10, pady=10)
    
    def _get_inscryption_tooltips(self) -> Dict[str, str]:
        """Get tooltip descriptions for inscryptions."""
        if self.hunter_name == "Borge":
            return {
                "i3": "+HP", "i4": "+Crit", "i11": "+Effect",
                "i13": "+Power", "i14": "+Loot", "i23": "-Speed",
                "i24": "+DR", "i27": "+HP", "i44": "+Loot", "i60": "+All",
            }
        elif self.hunter_name == "Knox":
            return {
                "i_knox_hp": "+HP", "i_knox_power": "+Power",
                "i_knox_block": "+Block", "i_knox_charge": "+Charge",
                "i_knox_reload": "-Reload",
            }
        else:  # Ozzy
            return {
                "i31": "+Effect", "i32": "+Loot", "i33": "+XP",
                "i36": "-Speed", "i37": "+DR", "i40": "+Multi",
            }
    
    def _create_run_tab(self):
        """Create the run optimization sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.run_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Optimization", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.run_frame, text="⚙️ Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=3)
        
        ttk.Label(row1, text="Sims per build:").pack(side=tk.LEFT, padx=5)
        self.num_sims = tk.IntVar(value=100 if RUST_AVAILABLE else 10)
        ttk.Spinbox(row1, textvariable=self.num_sims, from_=10, to=1000, width=6).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(row1, text="Builds per tier:").pack(side=tk.LEFT, padx=15)
        self.builds_per_tier = tk.IntVar(value=500)
        ttk.Spinbox(row1, textvariable=self.builds_per_tier, from_=100, to=5000, width=6).pack(side=tk.LEFT, padx=5)
        
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=3)
        
        self.use_rust = tk.BooleanVar(value=RUST_AVAILABLE)
        ttk.Checkbutton(row2, text="🦀 Use Rust Engine", variable=self.use_rust,
                        state=tk.NORMAL if RUST_AVAILABLE else tk.DISABLED).pack(side=tk.LEFT, padx=5)
        
        self.use_progressive = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="📈 Progressive Evolution", variable=self.use_progressive).pack(side=tk.LEFT, padx=15)
        
        # Buttons
        btn_frame = ttk.Frame(self.run_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.start_btn = ttk.Button(btn_frame, text="🚀 Start", command=self._start_optimization)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(btn_frame, text="⏹️ Stop", command=self._stop_optimization, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        
        # Progress
        progress_frame = ttk.LabelFrame(self.run_frame, text="📊 Progress")
        progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=10, pady=3)
        
        self.status_label = ttk.Label(progress_frame, text="Ready")
        self.status_label.pack(padx=10, pady=3)
        
        # Best tracking
        best_frame = ttk.Frame(progress_frame)
        best_frame.pack(fill=tk.X, padx=10, pady=3)
        
        self.best_max_label = ttk.Label(best_frame, text="🏆 Best Max: --")
        self.best_max_label.pack(side=tk.LEFT, padx=15)
        
        self.best_avg_label = ttk.Label(best_frame, text="📊 Best Avg: --")
        self.best_avg_label.pack(side=tk.LEFT, padx=15)
        
        # Log
        log_frame = ttk.LabelFrame(self.run_frame, text="📋 Log")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state=tk.DISABLED, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _create_advisor_tab(self):
        """Create the Upgrade Advisor sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.advisor_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Upgrade Advisor", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Instructions
        info_frame = ttk.LabelFrame(self.advisor_frame, text="🎯 Which Stat Should I Upgrade?")
        info_frame.pack(fill=tk.X, padx=10, pady=5)
        
        info_text = (
            "Simulates adding +1 to each stat and shows which gives the BEST improvement.\n"
            "Stats are grouped by resource type so you know which upgrade to pick!"
        )
        ttk.Label(info_frame, text=info_text, justify=tk.LEFT, wraplength=600).pack(padx=10, pady=5)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.advisor_frame, text="⚙️ Settings")
        settings_frame.pack(fill=tk.X, padx=10, pady=5)
        
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=3)
        
        ttk.Label(row1, text="Simulations per test:").pack(side=tk.LEFT, padx=5)
        self.advisor_sims = tk.IntVar(value=100)
        ttk.Spinbox(row1, textvariable=self.advisor_sims, from_=10, to=500, width=6).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="(100-200 recommended for accuracy)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=3)
        
        self.advisor_use_best = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="🏆 Use best build from optimizer (if available)", 
                        variable=self.advisor_use_best).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="Otherwise uses Build tab talents/attributes", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Analyze button
        btn_frame = ttk.Frame(self.advisor_frame)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.advisor_btn = ttk.Button(btn_frame, text="🔍 Analyze Best Upgrade", command=self._run_upgrade_advisor)
        self.advisor_btn.pack(side=tk.LEFT, padx=5)
        
        self.advisor_status = ttk.Label(btn_frame, text="")
        self.advisor_status.pack(side=tk.LEFT, padx=10)
        
        # Results
        results_frame = ttk.LabelFrame(self.advisor_frame, text="📈 Upgrade Recommendations")
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.advisor_results = scrolledtext.ScrolledText(results_frame, height=15, font=('Consolas', 10))
        self.advisor_results.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _run_upgrade_advisor(self):
        """Run the upgrade advisor analysis."""
        self.advisor_btn.configure(state=tk.DISABLED)
        self.advisor_status.configure(text="Analyzing...")
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        
        # Run in background thread
        thread = threading.Thread(target=self._analyze_upgrades, daemon=True)
        thread.start()
    
    def _analyze_upgrades(self):
        """Analyze which stat upgrade is best (runs in background)."""
        try:
            # Build base config INCLUDING current talents and attributes
            base_config = self._get_current_config()
            
            # If "use best build" is enabled and we have optimizer results, use the best build
            if self.advisor_use_best.get() and self.results:
                best_result = max(self.results, key=lambda r: r.avg_final_stage)
                base_config["talents"] = best_result.talents
                base_config["attributes"] = best_result.attributes
                self.frame.after(0, lambda: self.advisor_status.configure(
                    text=f"Using best build (avg {best_result.avg_final_stage:.1f} stages)..."))
            
            num_sims = self.advisor_sims.get()
            use_rust = self.app.hunter_tabs[self.hunter_name].use_rust.get() and RUST_AVAILABLE
            
            # First, simulate the baseline
            self.frame.after(0, lambda: self.advisor_status.configure(text="Simulating baseline..."))
            if use_rust:
                baseline = self._simulate_build_rust(base_config, num_sims)
            else:
                baseline = self._simulate_build_sequential(base_config, num_sims)
            
            if not baseline:
                self.frame.after(0, lambda: self._show_advisor_error("Could not simulate baseline build"))
                return
            
            # Get stat keys based on hunter type
            stat_keys = list(self.stat_entries.keys())
            results = []
            
            for i, stat in enumerate(stat_keys):
                self.frame.after(0, lambda s=stat, i=i: self.advisor_status.configure(
                    text=f"Testing +1 {s}... ({i+1}/{len(stat_keys)})"))
                
                test_config = copy.deepcopy(base_config)
                test_config["stats"][stat] = test_config["stats"].get(stat, 0) + 1
                
                if use_rust:
                    result = self._simulate_build_rust(test_config, num_sims)
                else:
                    result = self._simulate_build_sequential(test_config, num_sims)
                    
                if result:
                    # Calculate improvements
                    stage_improvement = result.avg_final_stage - baseline.avg_final_stage
                    loot_improvement = result.avg_loot_per_hour - baseline.avg_loot_per_hour
                    # Use damage taken (negative is better = less damage taken)
                    damage_taken_improvement = result.avg_damage_taken - baseline.avg_damage_taken
                    survival_improvement = (result.survival_rate - baseline.survival_rate) * 100
                    
                    # Create a score (weighted combination)
                    # Note: lower damage taken is better, so we subtract it
                    score = (
                        stage_improvement * 10 +  # Stage is important
                        loot_improvement * 5 +    # Loot matters
                        -damage_taken_improvement / 1000 +  # Less damage taken = better
                        survival_improvement * 2   # Survival is good
                    )
                    
                    results.append({
                        "stat": stat,
                        "stage_improvement": stage_improvement,
                        "loot_improvement": loot_improvement,
                        "damage_taken_change": damage_taken_improvement,
                        "survival_improvement": survival_improvement,
                        "score": score,
                        "result": result
                    })
            
            # Sort by score
            results.sort(key=lambda x: x["score"], reverse=True)
            
            # Display results
            self.frame.after(0, lambda: self._display_advisor_results(baseline, results))
            
        except Exception as e:
            import traceback
            self.frame.after(0, lambda: self._show_advisor_error(f"Error: {str(e)}\n{traceback.format_exc()}"))
    
    def _show_advisor_error(self, message: str):
        """Show an error in the advisor results."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        self.advisor_results.insert(tk.END, f"❌ {message}")
        self.advisor_results.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="")
    
    def _get_resource_categories(self) -> Dict[str, List[str]]:
        """Get resource categories for stats based on hunter type."""
        # Common stats for all hunters (HP, Power, Regen)
        common = ["hp", "power", "regen"]
        
        # Uncommon stats (DR, Evade/Block, Effect)
        # Rare stats are hunter-specific
        if self.hunter_name == "Knox":
            uncommon = ["damage_reduction", "block_chance", "effect_chance"]
            rare = ["charge_chance", "charge_gained", "reload_time", "projectiles_per_salvo"]
            # Knox resources: Glacium (common), Quartz (uncommon), Tesseracts (rare)
            return {
                "❄️ Glacium": common,
                "💎 Quartz": uncommon,
                "🔮 Tesseracts": rare,
            }
        elif self.hunter_name == "Ozzy":
            uncommon = ["damage_reduction", "evade_chance", "effect_chance"]
            rare = ["special_chance", "special_damage", "speed"]
            # Ozzy resources: Farahyte Ore (common), Galvarium (uncommon), Vectid Crystals (rare)
            return {
                "⛏️ Farahyte Ore": common,
                "🔩 Galvarium": uncommon,
                "💠 Vectid Crystals": rare,
            }
        else:  # Borge
            uncommon = ["damage_reduction", "evade_chance", "effect_chance"]
            rare = ["special_chance", "special_damage", "speed"]
            # Borge resources: Obsidian (common), Behlium (uncommon), Hellish-Biomatter (rare)
            return {
                "⬛ Obsidian": common,
                "⚫ Behlium": uncommon,
                "🔥 Hellish-Biomatter": rare,
            }
    
    def _get_resource_names(self) -> Tuple[str, str, str]:
        """Get the resource names for this hunter (common, uncommon, rare)."""
        if self.hunter_name == "Knox":
            return ("Glacium", "Quartz", "Tesseracts")
        elif self.hunter_name == "Ozzy":
            return ("Farahyte Ore", "Galvarium", "Vectid Crystals")
        else:  # Borge
            return ("Obsidian", "Behlium", "Hellish-Biomatter")
    
    def _display_advisor_results(self, baseline, results):
        """Display the upgrade advisor results grouped by resource type."""
        self.advisor_results.configure(state=tk.NORMAL)
        self.advisor_results.delete(1.0, tk.END)
        
        text = self.advisor_results
        resource_categories = self._get_resource_categories()
        
        # Group results by resource
        grouped_results = {cat: [] for cat in resource_categories}
        for r in results:
            for category, stats in resource_categories.items():
                if r["stat"] in stats:
                    grouped_results[category].append(r)
                    break
        
        text.insert(tk.END, "=" * 60 + "\n")
        text.insert(tk.END, f"🎯 {self.hunter_name.upper()} UPGRADE ADVISOR\n")
        text.insert(tk.END, "=" * 60 + "\n\n")
        
        text.insert(tk.END, "📊 BASELINE PERFORMANCE:\n")
        text.insert(tk.END, f"   Avg Stage: {baseline.avg_final_stage:.1f}\n")
        # Get resource names and show per-resource loot
        res_common, res_uncommon, res_rare = self._get_resource_names()
        if baseline.avg_elapsed_time > 0:
            runs_per_day = (3600 / baseline.avg_elapsed_time) * 24
            text.insert(tk.END, f"   📦 Loot/Run → Loot/Day:\n")
            text.insert(tk.END, f"      {res_common}: {self._format_number(baseline.avg_loot_common)} → {self._format_number(baseline.avg_loot_common * runs_per_day)}\n")
            text.insert(tk.END, f"      {res_uncommon}: {self._format_number(baseline.avg_loot_uncommon)} → {self._format_number(baseline.avg_loot_uncommon * runs_per_day)}\n")
            text.insert(tk.END, f"      {res_rare}: {self._format_number(baseline.avg_loot_rare)} → {self._format_number(baseline.avg_loot_rare * runs_per_day)}\n")
        else:
            text.insert(tk.END, f"   Loot/Hour: {baseline.avg_loot_per_hour:.2f}\n")
        text.insert(tk.END, f"   Dmg Dealt: {baseline.avg_damage:,.0f}\n")
        text.insert(tk.END, f"   Dmg Taken: {baseline.avg_damage_taken:,.0f}\n")
        text.insert(tk.END, f"   Survival: {baseline.survival_rate*100:.1f}%\n\n")
        
        # Show best overall first
        if results:
            best = results[0]
            text.insert(tk.END, "=" * 60 + "\n")
            text.insert(tk.END, "✨ BEST OVERALL UPGRADE\n")
            text.insert(tk.END, "=" * 60 + "\n")
            stat_name = best["stat"].replace("_", " ").title()
            text.insert(tk.END, f"🥇 +1 {stat_name}\n")
            text.insert(tk.END, f"   Stage: {best['stage_improvement']:+.2f}")
            text.insert(tk.END, f"  |  Loot: {best['loot_improvement']:+.2f}")
            text.insert(tk.END, f"  |  Taken: {best['damage_taken_change']:+,.0f}\n\n")
        
        # Show results grouped by resource
        text.insert(tk.END, "=" * 60 + "\n")
        text.insert(tk.END, "📦 BEST UPGRADES BY RESOURCE TYPE\n")
        text.insert(tk.END, "=" * 60 + "\n\n")
        
        for category in resource_categories.keys():
            category_results = grouped_results[category]
            
            if not category_results:
                continue
            
            text.insert(tk.END, f"{category.upper()}\n")
            text.insert(tk.END, "-" * 60 + "\n")
            
            # Sort by score within category
            category_results.sort(key=lambda x: x["score"], reverse=True)
            
            for i, r in enumerate(category_results, 1):
                stat_name = r["stat"].replace("_", " ").title()
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                text.insert(tk.END, f"  {medal} +1 {stat_name}\n")
                text.insert(tk.END, f"     Stage: {r['stage_improvement']:+.2f}")
                text.insert(tk.END, f"  |  Loot: {r['loot_improvement']:+.2f}")
                text.insert(tk.END, f"  |  Taken: {r['damage_taken_change']:+,.0f}\n")
            
            text.insert(tk.END, "\n")
        
        text.insert(tk.END, "=" * 60 + "\n")
        text.insert(tk.END, "💡 TIP: Upgrade within the resource type you have available!\n")
        text.insert(tk.END, "=" * 60 + "\n")
        
        text.configure(state=tk.DISABLED)
        self.advisor_btn.configure(state=tk.NORMAL)
        self.advisor_status.configure(text="Analysis complete!")
    
    def _create_results_tab(self):
        """Create the results sub-tab."""
        # Colored header banner
        icon = '🛡️' if self.hunter_name == 'Borge' else '🔫' if self.hunter_name == 'Knox' else '🐙'
        header = tk.Frame(self.results_frame, bg=self.colors["primary"], height=40)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        header_label = tk.Label(header, text=f"{icon} {self.hunter_name} Best Builds", 
                                font=('Arial', 14, 'bold'), fg=self.colors["text"], bg=self.colors["primary"])
        header_label.pack(expand=True)
        
        # Results notebook for different sort criteria
        self.results_notebook = ttk.Notebook(self.results_frame)
        self.results_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.result_tabs: Dict[str, scrolledtext.ScrolledText] = {}
        categories = [
            ("🏔️ Stage", "stage"),
            ("💰 Loot", "loot"),
            ("� XP", "xp"),
            ("💥 Damage", "damage"),
            ("📊 Compare", "compare"),
            ("⚖️ All", "all"),
        ]
        
        for label, key in categories:
            frame = ttk.Frame(self.results_notebook)
            self.results_notebook.add(frame, text=label)
            
            text = scrolledtext.ScrolledText(frame, height=20, font=('Consolas', 9))
            text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            
            # Configure color tags for rankings
            text.tag_config("gold", foreground="#FFD700", font=('Consolas', 9, 'bold'))  # 1st place
            text.tag_config("silver", foreground="#C0C0C0", font=('Consolas', 9, 'bold'))  # 2nd place
            text.tag_config("bronze", foreground="#CD7F32", font=('Consolas', 9, 'bold'))  # 3rd place
            text.tag_config("header", foreground=self.colors["primary"], font=('Consolas', 10, 'bold'))
            text.tag_config("metric", foreground="#00FF00", font=('Consolas', 9, 'bold'))  # Green for values
            text.tag_config("irl", foreground="#FF6B6B", font=('Consolas', 9, 'italic'))  # Red for IRL build
            
            self.result_tabs[key] = text
    
    def _log(self, message: str):
        """Add a message to the log (thread-safe via queue)."""
        # Always use the queue - it will be processed by the main thread
        self.result_queue.put(('log', message, None, None))
    
    def _log_direct(self, message: str):
        """Add a message to the log directly (only call from main thread)."""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
    
    def _get_current_config(self) -> Dict:
        """Build a config dictionary from current input values."""
        config = self.hunter_class.load_dummy()
        config["meta"]["level"] = self.level.get()
        
        for key, entry in self.stat_entries.items():
            try:
                config["stats"][key] = int(entry.get())
            except ValueError:
                config["stats"][key] = 0
        
        for key, entry in self.talent_entries.items():
            try:
                config["talents"][key] = int(entry.get())
            except ValueError:
                config["talents"][key] = 0
        
        for key, entry in self.attribute_entries.items():
            try:
                config["attributes"][key] = int(entry.get())
            except ValueError:
                config["attributes"][key] = 0
        
        for key, entry in self.inscryption_entries.items():
            try:
                config["inscryptions"][key] = int(entry.get())
            except ValueError:
                config["inscryptions"][key] = 0
        
        for key, entry in self.relic_entries.items():
            try:
                config["relics"][key] = int(entry.get())
            except ValueError:
                config["relics"][key] = 0
        
        for key, entry in self.gem_entries.items():
            try:
                config["gems"][key] = int(entry.get())
            except ValueError:
                config["gems"][key] = 0
        
        for key, var in self.mod_vars.items():
            config["mods"][key] = var.get()
        
        # Gadgets
        for key, entry in self.gadget_entries.items():
            try:
                config["gadgets"][key] = int(entry.get())
            except ValueError:
                config["gadgets"][key] = 0
        
        # Bonuses - read from GLOBAL bonuses in the app's Control tab
        try:
            config["bonuses"]["shard_milestone"] = self.app.global_shard_milestone.get()
            config["bonuses"]["diamond_loot"] = self.app.global_diamond_loot.get()
            config["bonuses"]["iap_travpack"] = self.app.global_iap_travpack.get()
            config["bonuses"]["ultima_multiplier"] = self.app.global_ultima_multiplier.get()
        except (AttributeError, tk.TclError):
            # Fallback if global bonuses not yet initialized
            config["bonuses"]["shard_milestone"] = 0
            config["bonuses"]["diamond_loot"] = 0
            config["bonuses"]["iap_travpack"] = False
            config["bonuses"]["ultima_multiplier"] = 1.0
        
        return config
    
    def _start_optimization(self):
        """Start optimization for this hunter."""
        if self.is_running:
            return
        self.is_running = True
        self.stop_event.clear()
        self.results.clear()
        self.optimization_start_time = time.time()
        self.progress_var.set(0)
        
        # Reset best tracking
        self.best_max_stage = 0
        self.best_avg_stage = 0.0
        self.best_max_label.configure(text="🏆 Best Max: --")
        self.best_avg_label.configure(text="📊 Best Avg: --")
        
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        
        # Clear log
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.configure(state=tk.DISABLED)
        
        # Capture ALL tkinter variables BEFORE starting thread (tkinter is not thread-safe)
        self._thread_level = self.level.get()
        self._thread_num_sims = self.num_sims.get()
        self._thread_builds_per_tier = self.builds_per_tier.get()
        self._thread_use_rust = self.use_rust.get() and RUST_AVAILABLE
        self._thread_use_progressive = self.use_progressive.get()
        self._thread_irl_max_stage = self.irl_max_stage.get()
        self._thread_config = self._get_current_config()
        
        # Start in background
        thread = threading.Thread(target=self._run_optimization, daemon=True)
        thread.start()
        
        # Start polling
        self.frame.after(100, self._poll_results)
    
    def _stop_optimization(self):
        """Stop optimization."""
        self.stop_event.set()
        self._log("⏹️ Stopping...")
        # Clear the result queue to prevent stale results
        while not self.result_queue.empty():
            try:
                self.result_queue.get_nowait()
            except:
                break
    
    def _run_optimization(self):
        """Run the optimization (background thread).
        
        Uses cached values from _thread_* attributes to avoid tkinter access from thread.
        """
        try:
            # Use cached values (captured before thread started)
            level = self._thread_level
            base_config = self._thread_config
            
            # Count actual points
            talent_count = sum(base_config.get("talents", {}).values())
            attr_count = sum(base_config.get("attributes", {}).values())
            
            self._log(f"🚀 Starting {self.hunter_name} optimization at level {level}")
            self._log(f"   Talents: {talent_count} points, Attributes: {attr_count} points")
            
            # Run baseline simulation on user's current IRL build first
            self._run_irl_baseline(base_config)
            
            if self._thread_use_progressive and level >= 30:
                self._run_progressive_evolution(level, base_config)
            else:
                self._run_sampling_optimization(level, base_config)
                
        except Exception as e:
            import traceback
            self._log(f"\n❌ Error: {str(e)}")
            self._log(traceback.format_exc())
            self.result_queue.put(('error', str(e), None, None))
    
    def _run_irl_baseline(self, base_config: Dict):
        """Run baseline simulation on the user's current IRL build."""
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        use_rust = self._thread_use_rust
        irl_max_stage = self._thread_irl_max_stage
        
        # Check if user has entered talents/attributes (from cached config)
        has_talents = any(v > 0 for v in base_config.get("talents", {}).values())
        has_attrs = any(v > 0 for v in base_config.get("attributes", {}).values())
        
        if not (has_talents or has_attrs):
            self._log("⚠️ No talents/attributes entered - skipping IRL baseline")
            self.irl_baseline_result = None
            return
        
        # Count actual talent and attribute points
        talent_count = sum(base_config.get("talents", {}).values())
        attr_count = sum(base_config.get("attributes", {}).values())
        
        self._log("\n📊 Running IRL Baseline Simulation...")
        config_level = base_config.get("meta", {}).get("level") or base_config.get("level", 0)
        self._log(f"   Your current build @ Level {config_level}")
        self._log(f"   Talents: {talent_count} points, Attributes: {attr_count} points")
        if irl_max_stage > 0:
            self._log(f"   IRL Max Stage: {irl_max_stage}")
        
        # Use cached config (already captured before thread started)
        irl_config = base_config
        
        try:
            if use_rust:
                result = self._simulate_build_rust(irl_config, num_sims)
            else:
                result = self._simulate_build_sequential(irl_config, num_sims)
            
            if result:
                self.irl_baseline_result = result
                self._log(f"   ✅ Sim predicts: Stage {result.avg_final_stage:.1f} (max {result.highest_stage})")
                
                # Compare to IRL if provided
                if irl_max_stage > 0:
                    sim_stage = result.avg_final_stage
                    diff = sim_stage - irl_max_stage
                    if abs(diff) < 5:
                        self._log(f"   🎯 Sim accuracy: EXCELLENT (within 5 stages)")
                    elif diff > 0:
                        self._log(f"   📈 Sim predicts {diff:.1f} stages HIGHER than IRL")
                    else:
                        self._log(f"   📉 Sim predicts {-diff:.1f} stages LOWER than IRL")
            else:
                self._log("   ⚠️ Failed to run baseline simulation")
                self.irl_baseline_result = None
        except Exception as e:
            self._log(f"   ⚠️ Baseline error: {e}")
            self.irl_baseline_result = None
    
    def _run_progressive_evolution(self, level: int, base_config: Dict):
        """Run progressive evolution optimization."""
        import random
        
        # Yield immediately to give GUI a chance
        time.sleep(0.01)
        
        self._log("\n📈 Using Progressive Evolution")
        self.result_queue.put(('log', "   [DEBUG] Progressive evolution started", None, None))
        
        tiers = [0.05, 0.10, 0.20, 0.40, 0.70, 1.0]
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        builds_per_tier = self._thread_builds_per_tier
        use_rust = self._thread_use_rust
        
        total_builds_planned = len(tiers) * builds_per_tier
        self._log(f"   Tiers: {[f'{int(t*100)}%' for t in tiers]}")
        self._log(f"   Builds per tier: {builds_per_tier}")
        
        if use_rust:
            self._log("   🦀 Using Rust engine")
        
        total_tested = 0
        elite_patterns = []
        final_tier_idx = len(tiers) - 1  # Index of the 100% tier
        
        for tier_idx, tier_pct in enumerate(tiers):
            if self.stop_event.is_set():
                self._log('\n⏹️ Stopped by user.')
                break
            
            tier_talent_points = max(1, int(level * tier_pct))
            tier_attr_points = max(3, int(level * 3 * tier_pct))
            tier_level = max(1, int(level * tier_pct))
            is_final_tier = (tier_idx == final_tier_idx)
            
            self._log(f"\n{'='*50}")
            self._log(f"📊 TIER {tier_idx + 1}/{len(tiers)}: {int(tier_pct*100)}%{'  [FINAL]' if is_final_tier else ''}")
            self._log(f"   Talents: {tier_talent_points}, Attrs: {tier_attr_points}")
            if elite_patterns:
                self._log(f"   Building on {len(elite_patterns)} elite patterns from previous tier")
            
            tier_generator = BuildGenerator(self.hunter_class, tier_level)
            tier_generator.talent_points = tier_talent_points
            tier_generator.attribute_points = tier_attr_points
            tier_generator._calculate_dynamic_attr_maxes()
            
            # Yield after generator creation
            time.sleep(0.01)
            
            tier_results = []
            tested_hashes = set()
            consecutive_dupes = 0
            max_consecutive_dupes = 100
            
            # SINGLE BUILD PROCESSING: Generate one build, simulate immediately
            # Batching was causing GUI lag due to long blocking calls
            batch_size = 1
            pending_configs = []
            pending_metadata = []
            
            builds_generated = 0
            for i in range(builds_per_tier):
                if self.stop_event.is_set():
                    break
                
                # Yield to main thread - 5ms every iteration for responsiveness
                time.sleep(0.005)
                
                if consecutive_dupes >= max_consecutive_dupes:
                    self._log(f"   ⚡ Tier exhausted after {len(tier_results)} builds")
                    break
                
                # Generate build - always try to extend elite first
                talents, attrs = None, None
                extended_from_elite = False
                
                if elite_patterns and random.random() < 0.8:  # 80% from elites
                    elite = random.choice(elite_patterns)
                    talents, attrs = self._extend_elite_pattern(
                        elite, tier_generator, tier_talent_points, tier_attr_points
                    )
                    extended_from_elite = True
                
                # Fallback to random if no elite or extension returned None
                if talents is None or attrs is None:
                    builds = tier_generator.generate_smart_sample(sample_size=1)
                    if builds:
                        talents, attrs = builds[0]
                    else:
                        consecutive_dupes += 1
                        continue
                
                # Check duplicate
                build_hash = (tuple(sorted(talents.items())), tuple(sorted(attrs.items())))
                if build_hash in tested_hashes:
                    consecutive_dupes += 1
                    continue
                tested_hashes.add(build_hash)
                consecutive_dupes = 0
                
                # Validate points spent - must use at least 95% of available points
                talent_spent = sum(talents.values())
                attr_costs_local = {a: tier_generator.costs["attributes"][a]["cost"] for a in attrs}
                attr_spent = sum(attrs[a] * attr_costs_local[a] for a in attrs)
                
                if talent_spent < tier_talent_points * 0.95 or attr_spent < tier_attr_points * 0.95:
                    consecutive_dupes += 1
                    continue
                
                # Add to batch
                config = copy.deepcopy(base_config)
                config["talents"] = talents
                config["attributes"] = attrs
                pending_configs.append(config)
                pending_metadata.append((talents, attrs))
                builds_generated += 1
                
                # Process batch when full or at end of loop
                should_process = (len(pending_configs) >= batch_size or 
                                 i == builds_per_tier - 1 or 
                                 consecutive_dupes >= max_consecutive_dupes)
                
                if should_process and pending_configs:
                    # Yield before heavy simulation work
                    time.sleep(0.001)
                    
                    try:
                        if use_rust and len(pending_configs) > 1:
                            # Use batch processing
                            batch_results = self._simulate_builds_batch(pending_configs, num_sims)
                        else:
                            # Individual simulation
                            batch_results = []
                            for cfg in pending_configs:
                                if use_rust:
                                    result = self._simulate_build_rust(cfg, num_sims)
                                else:
                                    result = self._simulate_build_sequential(cfg, num_sims)
                                batch_results.append(result)
                        
                        # Process results
                        for result, (talents, attrs) in zip(batch_results, pending_metadata):
                            if result:
                                # ONLY save to self.results on final tier (100%)
                                if is_final_tier:
                                    self.results.append(result)
                                tier_results.append({
                                    'avg_stage': result.avg_final_stage,
                                    'max_stage': result.highest_stage,
                                    'talents': talents,
                                    'attributes': attrs
                                })
                        
                        total_tested += len(batch_results)
                        progress = min(100, (total_tested / total_builds_planned) * 100)
                        self.result_queue.put(('progress', progress, total_tested, total_builds_planned))
                        
                        # Log progress every batch
                        elapsed = time.time() - self.optimization_start_time
                        rate = total_tested / elapsed if elapsed > 0 else 0
                        self.result_queue.put(('log', f"   ...{builds_generated}/{builds_per_tier} generated, {total_tested} tested ({rate:.1f}/sec)", None, None))
                        
                    except Exception as e:
                        self._log(f"   ⚠️ Batch error: {e}")
                    
                    # Clear batch
                    pending_configs = []
                    pending_metadata = []
            
            # Analyze tier results
            if tier_results:
                stages = [r['avg_stage'] for r in tier_results]
                max_stage = max(r['max_stage'] for r in tier_results)
                best_avg = max(stages)
                
                self._log(f"   Best avg: {best_avg:.1f}, max: {max_stage}")
                
                self.result_queue.put(('best_update', {
                    'best_max': max_stage,
                    'best_avg': best_avg,
                    'gen': tier_idx + 1
                }, None, None))
                
                # Select elites
                tier_results.sort(key=lambda x: x['avg_stage'], reverse=True)
                elite_count = min(100, max(len(tier_results) // 10, 10))
                elite_patterns = [
                    {'talents': r['talents'], 'attributes': r['attributes']}
                    for r in tier_results[:elite_count]
                ]
                self._log(f"   Promoted {len(elite_patterns)} elites")
        
        # Final summary
        total_time = time.time() - self.optimization_start_time
        rate = total_tested / total_time if total_time > 0 else 0
        
        self._log(f"\n{'='*50}")
        self._log(f"✅ Complete! Tested {total_tested} builds in {total_time:.1f}s ({rate:.1f}/sec)")
        
        # Show % optimal comparison if we have baseline
        if self.irl_baseline_result and self.results:
            best = max(self.results, key=lambda r: r.avg_final_stage)
            irl = self.irl_baseline_result
            if best.avg_final_stage > 0:
                pct_optimal = (irl.avg_final_stage / best.avg_final_stage) * 100
                stage_diff = best.avg_final_stage - irl.avg_final_stage
                self._log(f"\n📊 YOUR BUILD VS OPTIMAL:")
                self._log(f"   Your build: Stage {irl.avg_final_stage:.1f}")
                self._log(f"   Best found: Stage {best.avg_final_stage:.1f}")
                self._log(f"   📈 YOUR BUILD IS {pct_optimal:.1f}% OPTIMAL")
                if stage_diff > 0:
                    self._log(f"   Potential gain: +{stage_diff:.1f} stages")
        
        self.result_queue.put(('done', None, None, None))
    
    def _run_sampling_optimization(self, level: int, base_config: Dict):
        """Run simple sampling optimization for low-level hunters."""
        self._log("\n📊 Using Random Sampling")
        
        # Yield before potentially slow build generation
        time.sleep(0.01)
        
        generator = BuildGenerator(self.hunter_class, level)
        # Use cached values from main thread
        num_sims = self._thread_num_sims
        max_builds = self._thread_builds_per_tier
        use_rust = self._thread_use_rust
        
        builds = generator.generate_smart_sample(max_builds)
        self._log(f"   Generated {len(builds)} builds")
        
        # Yield after build generation
        time.sleep(0.01)
        
        # BATCH PROCESSING: Process builds in batches
        batch_size = 15 if use_rust else 1
        
        for batch_idx, batch_start in enumerate(range(0, len(builds), batch_size)):
            if self.stop_event.is_set():
                break
            
            # Yield to main thread periodically
            if batch_idx % 5 == 0:
                time.sleep(0.001)
            
            batch_end = min(batch_start + batch_size, len(builds))
            batch_builds = builds[batch_start:batch_end]
            
            # Create configs for batch
            configs = []
            for talents, attrs in batch_builds:
                config = copy.deepcopy(base_config)
                config["talents"] = talents
                config["attributes"] = attrs
                configs.append(config)
            
            try:
                if use_rust and len(configs) > 1:
                    # Use batch processing
                    batch_results = self._simulate_builds_batch(configs, num_sims)
                else:
                    # Fallback to individual simulation
                    batch_results = []
                    for cfg in configs:
                        if use_rust:
                            result = self._simulate_build_rust(cfg, num_sims)
                        else:
                            result = self._simulate_build_sequential(cfg, num_sims)
                        batch_results.append(result)
                
                # Add results
                for result in batch_results:
                    if result:
                        self.results.append(result)
            except Exception:
                pass
            
            progress = (batch_end / len(builds)) * 100
            self.result_queue.put(('progress', progress, batch_end, len(builds)))
        
        self._log(f"\n✅ Complete! Found {len(self.results)} builds")
        self.result_queue.put(('done', None, None, None))
    
    def _extend_elite_pattern(self, elite: Dict, generator: BuildGenerator,
                              target_talents: int, target_attrs: int) -> Tuple[Dict, Dict]:
        """Extend elite pattern with more points. MUST spend all available points."""
        import random
        
        talents_list = list(generator.costs["talents"].keys())
        attrs_list = list(generator.costs["attributes"].keys())
        
        # Copy elite pattern as starting point
        talents = {t: elite.get('talents', {}).get(t, 0) for t in talents_list}
        attrs = {a: elite.get('attributes', {}).get(a, 0) for a in attrs_list}
        
        # Calculate how much elite already spent
        elite_talent_spent = sum(talents.values())
        attr_costs = {a: generator.costs["attributes"][a]["cost"] for a in attrs_list}
        elite_attr_spent = sum(attrs[a] * attr_costs[a] for a in attrs_list)
        
        # Calculate how much MORE we need to add
        talent_to_add = max(0, target_talents - elite_talent_spent)
        attr_to_add = max(0, target_attrs - elite_attr_spent)
        
        attr_max = {a: generator.costs["attributes"][a]["max"] for a in attrs_list}
        talent_max = {t: generator.costs["talents"][t]["max"] for t in talents_list}
        
        # Find unlimited (infinite) attributes for fallback - these can always absorb points
        unlimited_attrs = [a for a in attrs_list if attr_max[a] == float('inf')]
        # Sort by cost (prefer cheaper ones for efficiency)
        unlimited_attrs.sort(key=lambda a: attr_costs[a])
        
        # === ADD TALENT POINTS ===
        attempts = 0
        while talent_to_add > 0 and attempts < 1000:
            attempts += 1
            valid = [t for t in talents_list if talents[t] < int(talent_max[t])]
            if not valid:
                break
            chosen = random.choice(valid)
            talents[chosen] += 1
            talent_to_add -= 1
        
        # === ADD ATTRIBUTE POINTS ===
        deps = getattr(generator.hunter_class, 'attribute_dependencies', {})
        exclusions = getattr(generator.hunter_class, 'attribute_exclusions', [])
        
        attempts = 0
        remaining = attr_to_add
        
        while remaining > 0 and attempts < 5000:
            attempts += 1
            
            # Find valid attributes to add to
            valid_attrs = []
            for attr in attrs_list:
                cost = attr_costs[attr]
                if cost > remaining:
                    continue
                # Check max - unlimited attrs (inf) always pass this check
                if attr_max[attr] != float('inf'):
                    if attrs[attr] >= int(attr_max[attr]):
                        continue
                # Check dependencies
                if attr in deps:
                    if not all(attrs.get(req, 0) >= lvl for req, lvl in deps[attr].items()):
                        continue
                # Check unlock requirements
                if not generator._can_unlock_attribute(attr, attrs, attr_costs):
                    continue
                # Check exclusions
                excluded = False
                for excl_pair in exclusions:
                    if attr in excl_pair:
                        other = excl_pair[0] if excl_pair[1] == attr else excl_pair[1]
                        if attrs.get(other, 0) > 0:
                            excluded = True
                            break
                if excluded:
                    continue
                valid_attrs.append(attr)
            
            if valid_attrs:
                # Randomly choose from valid options (includes unlimited attrs!)
                chosen = random.choice(valid_attrs)
                attrs[chosen] += 1
                remaining -= attr_costs[chosen]
            elif unlimited_attrs:
                # FALLBACK: Force use unlimited attributes if nothing else works
                spent_any = False
                for sink_attr in unlimited_attrs:
                    if attr_costs[sink_attr] <= remaining:
                        attrs[sink_attr] += 1
                        remaining -= attr_costs[sink_attr]
                        spent_any = True
                        break
                if not spent_any:
                    # Can't spend remaining points (all costs > remaining)
                    break
            else:
                # No valid attrs and no unlimited attrs - shouldn't happen
                break
        
        # FINAL GUARANTEE: If we still have remaining points, dump into unlimited attrs
        if remaining > 0 and unlimited_attrs:
            for sink_attr in unlimited_attrs:
                cost = attr_costs[sink_attr]
                while remaining >= cost:
                    attrs[sink_attr] += 1
                    remaining -= cost
        
        return talents, attrs
    
    def _simulate_build_rust(self, config: Dict, num_sims: int) -> Optional[BuildResult]:
        """Run simulations using Rust engine."""
        if not RUST_AVAILABLE:
            return self._simulate_build_sequential(config, num_sims)
        
        hunter_type = self.hunter_name
        # Support both flat format (from JSON saves) and nested format (from load_dummy)
        level = config.get("meta", {}).get("level") or config.get("level", 100)
        
        try:
            result = rust_sim.simulate(
                hunter=hunter_type,
                level=level,
                stats=config.get("stats", {}),
                talents=config.get("talents", {}),
                attributes=config.get("attributes", {}),
                inscryptions=config.get("inscryptions", {}),
                mods=config.get("mods", {}),
                relics=config.get("relics", {}),
                gems=config.get("gems", {}),
                num_sims=num_sims,
                parallel=True
            )
            
            # Rust sim returns stats at top level, not nested
            # Use per-resource loot values directly from Rust (WASM formulas)
            return BuildResult(
                talents=config.get("talents", {}).copy(),
                attributes=config.get("attributes", {}).copy(),
                avg_final_stage=result.get("avg_stage", 0),
                highest_stage=result.get("max_stage", 0),
                lowest_stage=result.get("min_stage", 0),
                avg_loot_per_hour=result.get("avg_loot_per_hour", 0),
                avg_damage=result.get("avg_damage", 0),
                avg_kills=result.get("avg_kills", 0),
                avg_elapsed_time=result.get("avg_time", 0),
                avg_damage_taken=result.get("avg_damage_taken", 0),
                survival_rate=result.get("survival_rate", 0),
                boss1_survival=result.get("boss1_survival", 0),
                boss2_survival=result.get("boss2_survival", 0),
                boss3_survival=result.get("boss3_survival", 0),
                boss4_survival=result.get("boss4_survival", 0),
                boss5_survival=result.get("boss5_survival", 0),
                avg_loot_common=result.get("avg_loot_common", 0),
                avg_loot_uncommon=result.get("avg_loot_uncommon", 0),
                avg_loot_rare=result.get("avg_loot_rare", 0),
                avg_xp=result.get("avg_xp", 0),
                config=config,
            )
        except Exception as e:
            return self._simulate_build_sequential(config, num_sims)
    
    def _simulate_builds_batch(self, configs: List[Dict], num_sims: int) -> List[Optional[BuildResult]]:
        """Run simulations for multiple builds at once using Rust batch function - much faster!"""
        if not RUST_AVAILABLE:
            # Fallback to sequential
            return [self._simulate_build_sequential(cfg, num_sims) for cfg in configs]
        
        try:
            # Call Rust batch function
            results = rust_sim.simulate_batch(configs, num_sims, parallel=True)
            
            # Convert to BuildResult objects
            build_results = []
            for config, result in zip(configs, results):
                # Use per-resource loot values directly from Rust (WASM formulas)
                build_results.append(BuildResult(
                    talents=config.get("talents", {}).copy(),
                    attributes=config.get("attributes", {}).copy(),
                    avg_final_stage=result.get("avg_stage", 0),
                    highest_stage=result.get("max_stage", 0),
                    lowest_stage=result.get("min_stage", 0),
                    avg_loot_per_hour=result.get("avg_loot_per_hour", 0),
                    avg_damage=result.get("avg_damage", 0),
                    avg_kills=result.get("avg_kills", 0),
                    avg_elapsed_time=result.get("avg_time", 0),
                    avg_damage_taken=result.get("avg_damage_taken", 0),
                    survival_rate=result.get("survival_rate", 0),
                    boss1_survival=result.get("boss1_survival", 0),
                    boss2_survival=result.get("boss2_survival", 0),
                    boss3_survival=result.get("boss3_survival", 0),
                    boss4_survival=result.get("boss4_survival", 0),
                    boss5_survival=result.get("boss5_survival", 0),
                    avg_loot_common=result.get("avg_loot_common", 0),
                    avg_loot_uncommon=result.get("avg_loot_uncommon", 0),
                    avg_loot_rare=result.get("avg_loot_rare", 0),
                    avg_xp=result.get("avg_xp", 0),
                    config=config,
                ))
            
            return build_results
        except Exception as e:
            # Fallback to sequential
            return [self._simulate_build_sequential(cfg, num_sims) for cfg in configs]
    
    def _simulate_build_sequential(self, config: Dict, num_sims: int) -> Optional[BuildResult]:
        """Run simulations sequentially."""
        results_list = []
        
        for _ in range(num_sims):
            sim = Simulation(self.hunter_class(config))
            results_list.append(sim.run())
        
        if not results_list:
            return None
        
        return self._aggregate_results(config, results_list)
    
    def _aggregate_results(self, config: Dict, results_list: List) -> BuildResult:
        """Aggregate simulation results."""
        final_stages = [r['final_stage'] for r in results_list]
        elapsed_times = [r['elapsed_time'] for r in results_list]
        damages = [r['damage'] for r in results_list]
        kills = [r['kills'] for r in results_list]
        damage_takens = [r['damage_taken'] for r in results_list]
        loots = [r['total_loot'] for r in results_list]
        
        # Per-resource loot
        loots_common = [r.get('loot_common', 0) for r in results_list]
        loots_uncommon = [r.get('loot_uncommon', 0) for r in results_list]
        loots_rare = [r.get('loot_rare', 0) for r in results_list]
        
        # XP tracking - use actual total_xp from simulation (WASM formula)
        xps = [r.get('total_xp', 0) for r in results_list]
        
        loot_per_hours = [(loots[i] / (elapsed_times[i] / 3600)) if elapsed_times[i] > 0 else 0 
                          for i in range(len(loots))]
        
        boss_deaths = sum(1 for s in final_stages if s % 100 == 0 and s > 0)
        survival_rate = 1 - (boss_deaths / len(final_stages))
        
        n = len(final_stages)
        boss1_survival = sum(1 for s in final_stages if s > 100) / n
        boss2_survival = sum(1 for s in final_stages if s > 200) / n
        boss3_survival = sum(1 for s in final_stages if s > 300) / n
        boss4_survival = sum(1 for s in final_stages if s > 400) / n
        boss5_survival = sum(1 for s in final_stages if s > 500) / n
        
        return BuildResult(
            talents=config["talents"].copy(),
            attributes=config["attributes"].copy(),
            avg_final_stage=statistics.mean(final_stages),
            highest_stage=max(final_stages),
            lowest_stage=min(final_stages),
            avg_loot_per_hour=statistics.mean(loot_per_hours),
            avg_damage=statistics.mean(damages),
            avg_kills=statistics.mean(kills),
            avg_elapsed_time=statistics.mean(elapsed_times),
            avg_damage_taken=statistics.mean(damage_takens),
            survival_rate=survival_rate,
            boss1_survival=boss1_survival,
            boss2_survival=boss2_survival,
            boss3_survival=boss3_survival,
            boss4_survival=boss4_survival,
            boss5_survival=boss5_survival,
            avg_loot_common=statistics.mean(loots_common),
            avg_loot_uncommon=statistics.mean(loots_uncommon),
            avg_loot_rare=statistics.mean(loots_rare),
            avg_xp=statistics.mean(xps),
            config=config,
        )
    
    def _poll_results(self):
        """Poll for results from background thread."""
        try:
            while True:
                msg_type, data, tested, total = self.result_queue.get_nowait()
                
                if msg_type == 'progress':
                    self.progress_var.set(data)
                    if tested and total:
                        elapsed = time.time() - self.optimization_start_time
                        rate = tested / elapsed if elapsed > 0 else 0
                        self.status_label.configure(text=f"{tested}/{total} ({rate:.1f}/sec)")
                elif msg_type == 'best_update':
                    if data.get('best_max', 0) > self.best_max_stage:
                        self.best_max_stage = data['best_max']
                        self.best_max_label.configure(text=f"🏆 Best Max: {self.best_max_stage}")
                    if data.get('best_avg', 0) > self.best_avg_stage:
                        self.best_avg_stage = data['best_avg']
                        self.best_avg_label.configure(text=f"📊 Best Avg: {self.best_avg_stage:.1f}")
                elif msg_type == 'log':
                    self._log_direct(data)
                elif msg_type == 'done':
                    self._optimization_complete()
                    return
                elif msg_type == 'error':
                    self._optimization_complete()
                    return
                    
        except queue.Empty:
            pass
        except Exception as e:
            # Handle any other exceptions to prevent crash
            print(f"Error in _poll_results: {e}")
            pass
        
        if self.is_running:
            # Force process any pending GUI events
            try:
                self.frame.update_idletasks()
            except:
                pass
            self.frame.after(50, self._poll_results)  # Poll more frequently
    
    def _optimization_complete(self):
        """Handle optimization completion."""
        self.is_running = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.progress_var.set(100)
        self.status_label.configure(text=f"Done! {len(self.results)} builds")
        
        self._display_results()
        self.sub_notebook.select(self.results_frame)
    
    def _display_results(self):
        """Display optimization results."""
        if not self.results:
            for text_widget in self.result_tabs.values():
                text_widget.configure(state=tk.NORMAL)
                text_widget.delete(1.0, tk.END)
                text_widget.insert(tk.END, "No results. Run optimization first.")
                text_widget.configure(state=tk.DISABLED)
            return
        
        by_stage = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[:10]
        by_loot = sorted(self.results, key=lambda r: r.avg_loot_per_hour, reverse=True)[:10]
        by_xp = sorted(self.results, key=lambda r: r.avg_xp, reverse=True)[:10]
        by_damage = sorted(self.results, key=lambda r: r.avg_damage, reverse=True)[:10]
        
        self._display_category(self.result_tabs["stage"], by_stage, "Avg Stage",
                               lambda r: f"{r.avg_final_stage:.1f}")
        self._display_category(self.result_tabs["loot"], by_loot, "Loot/Hour",
                               lambda r: f"{r.avg_loot_per_hour:.2f}")
        self._display_category(self.result_tabs["xp"], by_xp, "Avg XP",
                               lambda r: f"{self._format_number(r.avg_xp)}")
        self._display_category(self.result_tabs["damage"], by_damage, "Avg Damage",
                               lambda r: f"{r.avg_damage:,.0f}")
        
        # Compare tab - detailed comparison with IRL build
        self._display_comparison_tab()
        
        # All tab - with IRL comparison
        by_stage = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[:20]
        self._display_all_tab(by_stage)
    
    def _display_comparison_tab(self):
        """Display the comparison tab with detailed IRL vs top 3 builds analysis."""
        compare_text = self.result_tabs["compare"]
        compare_text.configure(state=tk.NORMAL)
        compare_text.delete(1.0, tk.END)
        
        if not self.irl_baseline_result:
            compare_text.insert(tk.END, "No IRL baseline available.\n")
            compare_text.insert(tk.END, "Run optimization first to see comparison.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        if not self.results:
            compare_text.insert(tk.END, "No optimization results available.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        irl = self.irl_baseline_result
        top3 = sorted(self.results, key=lambda r: r.avg_final_stage, reverse=True)[:3]
        best = top3[0] if top3 else None
        
        if not best:
            compare_text.insert(tk.END, "No builds to compare.\n")
            compare_text.configure(state=tk.DISABLED)
            return
        
        res_common, res_uncommon, res_rare = self._get_resource_names()
        
        # Header
        compare_text.insert(tk.END, "=" * 70 + "\n")
        compare_text.insert(tk.END, "📊 BUILD COMPARISON: YOUR BUILD VS OPTIMAL BUILDS\n")
        compare_text.insert(tk.END, "=" * 70 + "\n\n")
        
        # Calculate optimal percentages based on different metrics
        pct_stage = (irl.avg_final_stage / best.avg_final_stage * 100) if best.avg_final_stage > 0 else 100
        pct_loot = (irl.avg_loot_per_hour / best.avg_loot_per_hour * 100) if best.avg_loot_per_hour > 0 else 100
        pct_xp = (irl.avg_xp / best.avg_xp * 100) if best.avg_xp > 0 else 100
        pct_damage = (irl.avg_damage / best.avg_damage * 100) if best.avg_damage > 0 else 100
        
        # Overall optimal score (weighted average)
        overall_pct = (pct_stage * 0.4 + pct_loot * 0.3 + pct_xp * 0.15 + pct_damage * 0.15)
        
        # Big optimality display
        compare_text.insert(tk.END, "╔════════════════════════════════════════════════════════════════╗\n")
        compare_text.insert(tk.END, f"║         YOUR BUILD IS {overall_pct:>6.2f}% OPTIMAL                      ║\n")
        compare_text.insert(tk.END, "╚════════════════════════════════════════════════════════════════╝\n\n")
        
        # Rating and recommendation
        if overall_pct >= 98:
            grade = "🌟 PERFECT - No respec needed!"
            advice = "Your build is essentially optimal. Save your resources."
        elif overall_pct >= 95:
            grade = "🌟 EXCELLENT - Minor gains possible"
            advice = "Very minor improvements possible. Probably not worth a respec."
        elif overall_pct >= 90:
            grade = "✅ GREAT - Small room for improvement"
            advice = "Some gains possible. Consider respec if resources are plentiful."
        elif overall_pct >= 80:
            grade = "👍 GOOD - Noticeable gains available"
            advice = "Meaningful improvements available. Respec recommended when convenient."
        elif overall_pct >= 70:
            grade = "📈 DECENT - Significant gains available"
            advice = "Significant improvements possible. Respec is a good investment."
        elif overall_pct >= 60:
            grade = "⚠️ SUBOPTIMAL - Large gains available"
            advice = "Major improvements available. Strongly recommend respec."
        else:
            grade = "🔧 NEEDS WORK - Substantial gains available"
            advice = "Your build needs significant optimization. Respec ASAP!"
        
        compare_text.insert(tk.END, f"Rating: {grade}\n")
        compare_text.insert(tk.END, f"💡 Advice: {advice}\n\n")
        
        # Detailed breakdown
        compare_text.insert(tk.END, "─" * 70 + "\n")
        compare_text.insert(tk.END, "METRIC BREAKDOWN:\n")
        compare_text.insert(tk.END, "─" * 70 + "\n\n")
        
        compare_text.insert(tk.END, f"  🏔️ Stage:    {pct_stage:>6.2f}% optimal  ({irl.avg_final_stage:.1f} vs {best.avg_final_stage:.1f})\n")
        compare_text.insert(tk.END, f"  💰 Loot/Hr:  {pct_loot:>6.2f}% optimal  ({irl.avg_loot_per_hour:.2f} vs {best.avg_loot_per_hour:.2f})\n")
        compare_text.insert(tk.END, f"  📈 XP/Run:   {pct_xp:>6.2f}% optimal  ({self._format_number(irl.avg_xp)} vs {self._format_number(best.avg_xp)})\n")
        compare_text.insert(tk.END, f"  💥 Damage:   {pct_damage:>6.2f}% optimal  ({irl.avg_damage:,.0f} vs {best.avg_damage:,.0f})\n\n")
        
        # Potential gains
        compare_text.insert(tk.END, "─" * 70 + "\n")
        compare_text.insert(tk.END, "POTENTIAL GAINS IF YOU RESPEC:\n")
        compare_text.insert(tk.END, "─" * 70 + "\n\n")
        
        stage_gain = best.avg_final_stage - irl.avg_final_stage
        loot_gain_pct = ((best.avg_loot_per_hour / irl.avg_loot_per_hour) - 1) * 100 if irl.avg_loot_per_hour > 0 else 0
        xp_gain_pct = ((best.avg_xp / irl.avg_xp) - 1) * 100 if irl.avg_xp > 0 else 0
        
        compare_text.insert(tk.END, f"  Stage:      +{stage_gain:.1f} stages\n")
        compare_text.insert(tk.END, f"  Loot:       +{loot_gain_pct:.1f}% more loot per hour\n")
        compare_text.insert(tk.END, f"  XP:         +{xp_gain_pct:.1f}% more XP per run\n\n")
        
        # Compare talents/attributes
        compare_text.insert(tk.END, "─" * 70 + "\n")
        compare_text.insert(tk.END, "TOP 3 OPTIMAL BUILDS:\n")
        compare_text.insert(tk.END, "─" * 70 + "\n\n")
        
        for i, build in enumerate(top3, 1):
            pct_of_best = (build.avg_final_stage / best.avg_final_stage * 100) if best.avg_final_stage > 0 else 100
            compare_text.insert(tk.END, f"#{i} - Stage {build.avg_final_stage:.1f} ({pct_of_best:.1f}% of best)\n")
            compare_text.insert(tk.END, f"   Talents: {', '.join(f'{k}:{v}' for k, v in build.talents.items() if v > 0)}\n")
            compare_text.insert(tk.END, f"   Attrs: {', '.join(f'{k}:{v}' for k, v in build.attributes.items() if v > 0)}\n\n")
        
        # Talent/attribute diff from your build to best
        compare_text.insert(tk.END, "─" * 70 + "\n")
        compare_text.insert(tk.END, "CHANGES NEEDED (Your Build → Best Build):\n")
        compare_text.insert(tk.END, "─" * 70 + "\n\n")
        
        compare_text.insert(tk.END, "  TALENTS:\n")
        for talent in set(list(irl.talents.keys()) + list(best.talents.keys())):
            irl_val = irl.talents.get(talent, 0)
            best_val = best.talents.get(talent, 0)
            if irl_val != best_val:
                diff = best_val - irl_val
                sign = "+" if diff > 0 else ""
                compare_text.insert(tk.END, f"    {talent}: {irl_val} → {best_val} ({sign}{diff})\n")
        
        compare_text.insert(tk.END, "\n  ATTRIBUTES:\n")
        for attr in set(list(irl.attributes.keys()) + list(best.attributes.keys())):
            irl_val = irl.attributes.get(attr, 0)
            best_val = best.attributes.get(attr, 0)
            if irl_val != best_val:
                diff = best_val - irl_val
                sign = "+" if diff > 0 else ""
                compare_text.insert(tk.END, f"    {attr}: {irl_val} → {best_val} ({sign}{diff})\n")
        
        compare_text.configure(state=tk.DISABLED)
    
    def _display_all_tab(self, by_stage: List[BuildResult]):
        """Display the All tab with summary and top 20 builds."""
        all_text = self.result_tabs["all"]
        all_text.configure(state=tk.NORMAL)
        all_text.delete(1.0, tk.END)
        
        # Show IRL build comparison if available
        best_build = by_stage[0] if by_stage else None
        if self.irl_baseline_result and best_build:
            all_text.insert(tk.END, "=" * 60 + "\n")
            all_text.insert(tk.END, "📊 YOUR BUILD VS OPTIMAL\n")
            all_text.insert(tk.END, "=" * 60 + "\n\n")
            
            irl = self.irl_baseline_result
            opt = best_build
            
            # Calculate % optimal based on stage (primary metric)
            if opt.avg_final_stage > 0:
                pct_optimal = (irl.avg_final_stage / opt.avg_final_stage) * 100
            else:
                pct_optimal = 100.0
            
            all_text.insert(tk.END, f"🎮 YOUR IRL BUILD:\n")
            all_text.insert(tk.END, f"   Sim Stage: {irl.avg_final_stage:.1f} (max {irl.highest_stage})\n")
            if self.irl_max_stage.get() > 0:
                all_text.insert(tk.END, f"   Actual IRL: {self.irl_max_stage.get()}\n")
            # Per-resource loot for IRL build
            res_common, res_uncommon, res_rare = self._get_resource_names()
            if irl.avg_elapsed_time > 0:
                irl_runs_per_day = (3600 / irl.avg_elapsed_time) * 24
                all_text.insert(tk.END, f"   📦 Loot/Run → Loot/Day:\n")
                all_text.insert(tk.END, f"      {res_common}: {self._format_number(irl.avg_loot_common)} → {self._format_number(irl.avg_loot_common * irl_runs_per_day)}\n")
                all_text.insert(tk.END, f"      {res_uncommon}: {self._format_number(irl.avg_loot_uncommon)} → {self._format_number(irl.avg_loot_uncommon * irl_runs_per_day)}\n")
                all_text.insert(tk.END, f"      {res_rare}: {self._format_number(irl.avg_loot_rare)} → {self._format_number(irl.avg_loot_rare * irl_runs_per_day)}\n")
                # XP
                all_text.insert(tk.END, f"   📈 XP/Run → XP/Day: {self._format_number(irl.avg_xp)} → {self._format_number(irl.avg_xp * irl_runs_per_day)}\n\n")
            else:
                all_text.insert(tk.END, f"   Loot/Hr: {irl.avg_loot_per_hour:.2f}\n\n")
            
            all_text.insert(tk.END, f"🏆 OPTIMAL BUILD:\n")
            all_text.insert(tk.END, f"   Stage: {opt.avg_final_stage:.1f} (max {opt.highest_stage})\n")
            # Per-resource loot for optimal build
            if opt.avg_elapsed_time > 0:
                opt_runs_per_day = (3600 / opt.avg_elapsed_time) * 24
                all_text.insert(tk.END, f"   📦 Loot/Run → Loot/Day:\n")
                all_text.insert(tk.END, f"      {res_common}: {self._format_number(opt.avg_loot_common)} → {self._format_number(opt.avg_loot_common * opt_runs_per_day)}\n")
                all_text.insert(tk.END, f"      {res_uncommon}: {self._format_number(opt.avg_loot_uncommon)} → {self._format_number(opt.avg_loot_uncommon * opt_runs_per_day)}\n")
                all_text.insert(tk.END, f"      {res_rare}: {self._format_number(opt.avg_loot_rare)} → {self._format_number(opt.avg_loot_rare * opt_runs_per_day)}\n")
                all_text.insert(tk.END, f"   📈 XP/Run → XP/Day: {self._format_number(opt.avg_xp)} → {self._format_number(opt.avg_xp * opt_runs_per_day)}\n\n")
            else:
                all_text.insert(tk.END, f"   Loot/Hr: {opt.avg_loot_per_hour:.2f}\n\n")
            
            # Show % optimal with color coding
            if pct_optimal >= 95:
                grade = "🌟 EXCELLENT"
            elif pct_optimal >= 85:
                grade = "✅ GREAT"
            elif pct_optimal >= 75:
                grade = "👍 GOOD"
            elif pct_optimal >= 60:
                grade = "📈 ROOM TO IMPROVE"
            else:
                grade = "🔧 NEEDS OPTIMIZATION"
            
            all_text.insert(tk.END, f"📈 YOUR BUILD IS {pct_optimal:.1f}% OPTIMAL\n")
            all_text.insert(tk.END, f"   {grade}\n")
            
            # Show stage difference
            stage_diff = opt.avg_final_stage - irl.avg_final_stage
            if stage_diff > 0:
                all_text.insert(tk.END, f"   Potential gain: +{stage_diff:.1f} stages\n")
            
            all_text.insert(tk.END, "\n" + "=" * 60 + "\n\n")
        
        all_text.insert(tk.END, f"TOP 20 {self.hunter_name.upper()} BUILDS\n", "header")
        all_text.insert(tk.END, "=" * 60 + "\n\n")
        
        # Get best stage for star rating comparison
        best_stage = by_stage[0].avg_final_stage if by_stage else 0
        
        for i, result in enumerate(by_stage[:20], 1):
            # Medals for top 3
            if i == 1:
                all_text.insert(tk.END, "🥇 ", "gold")
            elif i == 2:
                all_text.insert(tk.END, "🥈 ", "silver")
            elif i == 3:
                all_text.insert(tk.END, "🥉 ", "bronze")
            else:
                all_text.insert(tk.END, f"#{i} ")
            
            # Star rating based on % of best
            if best_stage > 0:
                pct = (result.avg_final_stage / best_stage) * 100
                if pct >= 99:
                    stars = "⭐⭐⭐⭐⭐"  # 5 stars - top tier
                elif pct >= 95:
                    stars = "⭐⭐⭐⭐"    # 4 stars - excellent
                elif pct >= 90:
                    stars = "⭐⭐⭐"      # 3 stars - good
                elif pct >= 80:
                    stars = "⭐⭐"        # 2 stars - average
                else:
                    stars = "⭐"          # 1 star - below average
                all_text.insert(tk.END, f"{stars}\n")
            else:
                all_text.insert(tk.END, "\n")
            
            all_text.insert(tk.END, self._format_build_result(result))
            all_text.insert(tk.END, "\n\n")
        
        all_text.configure(state=tk.DISABLED)
    
    def _display_category(self, text_widget, results: List[BuildResult], metric_name: str, metric_fn):
        """Display results for a category with color-coded rankings."""
        text_widget.configure(state=tk.NORMAL)
        text_widget.delete(1.0, tk.END)
        
        # Header with color
        text_widget.insert(tk.END, f"TOP 10 BY {metric_name.upper()}\n", "header")
        text_widget.insert(tk.END, "=" * 50 + "\n\n")
        
        for i, result in enumerate(results, 1):
            # Determine medal and color tag based on ranking
            if i == 1:
                medal = "🥇"
                tag = "gold"
            elif i == 2:
                medal = "🥈"
                tag = "silver"
            elif i == 3:
                medal = "🥉"
                tag = "bronze"
            else:
                medal = f"#{i}"
                tag = None
            
            # Insert ranking line with medal and color
            if tag:
                text_widget.insert(tk.END, f"{medal} {metric_name} = ", tag)
                text_widget.insert(tk.END, f"{metric_fn(result)}\n", "metric")
            else:
                text_widget.insert(tk.END, f"{medal}: {metric_name} = ")
                text_widget.insert(tk.END, f"{metric_fn(result)}\n", "metric")
            
            text_widget.insert(tk.END, self._format_build_result(result))
            text_widget.insert(tk.END, "\n\n")
        
        text_widget.configure(state=tk.DISABLED)
    
    def _format_build_result(self, result: BuildResult) -> str:
        """Format a build result for display."""
        lines = []
        lines.append(f"  Stage: {result.avg_final_stage:.1f} (max {result.highest_stage})")
        
        # Get resource names for this hunter
        res_common, res_uncommon, res_rare = self._get_resource_names()
        
        # Calculate per run and per day loot
        # Loot per run = avg_loot (from avg_elapsed_time)
        # Approximate: if avg_elapsed_time is in seconds, runs per hour = 3600/avg_elapsed_time
        if result.avg_elapsed_time > 0:
            runs_per_hour = 3600 / result.avg_elapsed_time
            runs_per_day = runs_per_hour * 24
            
            # Per-resource loot per run and per day
            common_per_run = result.avg_loot_common
            uncommon_per_run = result.avg_loot_uncommon
            rare_per_run = result.avg_loot_rare
            common_per_day = common_per_run * runs_per_day
            uncommon_per_day = uncommon_per_run * runs_per_day
            rare_per_day = rare_per_run * runs_per_day
            
            lines.append(f"  📦 LOOT PER RUN / PER DAY:")
            lines.append(f"     {res_common}: {self._format_number(common_per_run)} / {self._format_number(common_per_day)}")
            lines.append(f"     {res_uncommon}: {self._format_number(uncommon_per_run)} / {self._format_number(uncommon_per_day)}")
            lines.append(f"     {res_rare}: {self._format_number(rare_per_run)} / {self._format_number(rare_per_day)}")
            
            # XP per run and per day
            xp_per_run = result.avg_xp
            xp_per_day = xp_per_run * runs_per_day
            lines.append(f"  📈 XP PER RUN / PER DAY: {self._format_number(xp_per_run)} / {self._format_number(xp_per_day)}")
        else:
            lines.append(f"  Loot/Hr: {result.avg_loot_per_hour:.2f}")
        
        lines.append(f"  Damage: {result.avg_damage:,.0f}")
        
        lines.append("  Talents: " + ", ".join(f"{k}:{v}" for k, v in result.talents.items() if v > 0))
        lines.append("  Attrs: " + ", ".join(f"{k}:{v}" for k, v in result.attributes.items() if v > 0))
        
        return "\n".join(lines)
    
    def _format_number(self, num: float) -> str:
        """Format large numbers with suffixes (k, m, b, t, qa, qi)."""
        if num < 1000:
            return f"{num:.2f}"
        elif num < 1_000_000:
            return f"{num/1000:.2f}k"
        elif num < 1_000_000_000:
            return f"{num/1_000_000:.2f}m"
        elif num < 1_000_000_000_000:
            return f"{num/1_000_000_000:.2f}b"
        elif num < 1_000_000_000_000_000:
            return f"{num/1_000_000_000_000:.2f}t"
        elif num < 1_000_000_000_000_000_000:
            return f"{num/1_000_000_000_000_000:.2f}qa"
        else:
            return f"{num/1_000_000_000_000_000_000:.2f}qi"


class MultiHunterGUI:
    """Main GUI with tabs for each hunter."""
    
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("🎮 Hunter Sim - Multi-Hunter Optimizer")
        self.root.geometry("1400x900")
        self.root.minsize(1000, 600)
        
        # Setup color styles FIRST
        self._setup_styles()
        
        # Create global log FIRST (before hunter tabs which may log on load)
        self._create_log_frame()
        
        # Create main notebook
        self.main_notebook = ttk.Notebook(self.root)
        self.main_notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Initialize hunter_tabs dict BEFORE creating control tab (which references it)
        self.hunter_tabs: Dict[str, HunterTab] = {}
        
        # Create control tab frame FIRST (so it's first in notebook)
        self.control_frame = ttk.Frame(self.main_notebook)
        self.main_notebook.add(self.control_frame, text="  ⚙️ Control  ")
        
        # Create hunter tabs with colors in order: Borge, Ozzy, Knox
        for name, cls in [("Borge", Borge), ("Ozzy", Ozzy), ("Knox", Knox)]:
            self.hunter_tabs[name] = HunterTab(self.main_notebook, name, cls, self)
            # Color the tab text
            colors = HUNTER_COLORS[name]
            idx = self.main_notebook.index("end") - 1
            # Use colored icons in tab names
            icon = '🛡️' if name == 'Borge' else '🔫' if name == 'Knox' else '🐙'
            self.main_notebook.tab(idx, text=f"  {icon} {name}  ")
        
        # Now populate the control tab (hunter_tabs exists now)
        self._populate_control_tab()
    
    def _setup_styles(self):
        """Configure ttk styles with hunter colors."""
        style = ttk.Style()
        
        # Configure styles for each hunter
        for hunter, colors in HUNTER_COLORS.items():
            # Tab style - colored text
            style.configure(f"{hunter}.TNotebook.Tab", 
                          foreground=colors["dark"],
                          font=('Arial', 10, 'bold'))
            
            # Label styles
            style.configure(f"{hunter}.TLabel",
                          foreground=colors["dark"],
                          font=('Arial', 10, 'bold'))
            
            style.configure(f"{hunter}Light.TLabel",
                          foreground=colors["primary"])
            
            # Frame style with colored border effect
            style.configure(f"{hunter}.TLabelframe",
                          bordercolor=colors["primary"])
            style.configure(f"{hunter}.TLabelframe.Label",
                          foreground=colors["dark"],
                          font=('Arial', 10, 'bold'))
            
            # Progress bar colors
            style.configure(f"{hunter}.Horizontal.TProgressbar",
                          troughcolor=colors["light"],
                          background=colors["primary"])
            
            # Button style
            style.configure(f"{hunter}.TButton",
                          foreground=colors["dark"])
    
    def _populate_control_tab(self):
        """Populate the control tab for running all hunters."""
        control_frame = self.control_frame  # Use the pre-created frame
        
        # Split into left (settings) and right (battle arena)
        left_frame = ttk.Frame(control_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        right_frame = ttk.Frame(control_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=5, pady=5)
        
        # ============ BATTLE ARENA (right side) ============
        arena_frame = ttk.LabelFrame(right_frame, text="⚔️ Battle Arena")
        arena_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create battle canvas
        self.battle_canvas = tk.Canvas(arena_frame, width=350, height=400, bg='#1a1a2e', highlightthickness=2, highlightbackground='#4a4a6a')
        self.battle_canvas.pack(padx=5, pady=5)
        
        # ============ LEADERBOARD (below arena) ============
        leaderboard_frame = ttk.LabelFrame(right_frame, text="🏆 Leaderboard - Completed Runs")
        leaderboard_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Create leaderboard labels for each hunter
        self.leaderboard_labels = {}
        for hunter_name, (icon, color) in [("Borge", ("🛡️", "#DC3545")), 
                                            ("Knox", ("🔫", "#0D6EFD")), 
                                            ("Ozzy", ("🐙", "#198754"))]:
            row_frame = ttk.Frame(leaderboard_frame)
            row_frame.pack(fill=tk.X, padx=5, pady=2)
            
            name_label = tk.Label(row_frame, text=f"{icon} {hunter_name}", 
                                  fg=color, bg='#1a1a2e', font=('Arial', 10, 'bold'), width=10, anchor='w')
            name_label.pack(side=tk.LEFT, padx=2)
            
            stats_label = tk.Label(row_frame, text="Waiting...", 
                                   fg='#888888', bg='#1a1a2e', font=('Arial', 9), anchor='w')
            stats_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            
            self.leaderboard_labels[hunter_name] = stats_label
        
        # Initialize battle state
        self._init_battle_arena()
        
        # Create scrollable content for left side
        canvas = tk.Canvas(left_frame)
        scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=canvas.yview)
        scrollable = ttk.Frame(canvas)
        
        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # ============ GLOBAL SETTINGS ============
        settings_frame = ttk.LabelFrame(scrollable, text="⚙️ Global Optimization Settings (applies to all hunters)")
        settings_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Simulations per build
        row1 = ttk.Frame(settings_frame)
        row1.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row1, text="Simulations per build:").pack(side=tk.LEFT, padx=5)
        self.global_num_sims = tk.IntVar(value=100 if RUST_AVAILABLE else 10)
        ttk.Spinbox(row1, textvariable=self.global_num_sims, from_=10, to=1000, increment=10, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row1, text="(More = more accurate, 100-500 recommended)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Builds per tier
        row2 = ttk.Frame(settings_frame)
        row2.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row2, text="Builds per tier:").pack(side=tk.LEFT, padx=5)
        self.global_builds_per_tier = tk.IntVar(value=500)
        ttk.Spinbox(row2, textvariable=self.global_builds_per_tier, from_=100, to=10000, increment=100, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="(6 tiers × this = total builds tested)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # CPU processes (Python only)
        row3 = ttk.Frame(settings_frame)
        row3.pack(fill=tk.X, padx=10, pady=5)
        
        ttk.Label(row3, text="CPU processes:").pack(side=tk.LEFT, padx=5)
        self.global_num_procs = tk.IntVar(value=16)
        ttk.Spinbox(row3, textvariable=self.global_num_procs, from_=1, to=32, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(row3, text="(Python only - ignored when using Rust)", 
                  font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Rust engine
        row4 = ttk.Frame(settings_frame)
        row4.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_rust = tk.BooleanVar(value=RUST_AVAILABLE)
        rust_check = ttk.Checkbutton(row4, text="🦀 Use Rust Engine (50-100x faster)", 
                                     variable=self.global_use_rust,
                                     state=tk.NORMAL if RUST_AVAILABLE else tk.DISABLED)
        rust_check.pack(side=tk.LEFT, padx=5)
        
        if RUST_AVAILABLE:
            ttk.Label(row4, text="✅ Rust engine available", 
                     font=('Arial', 9), foreground='green').pack(side=tk.LEFT, padx=10)
        else:
            ttk.Label(row4, text="❌ Rust not found (run 'cargo build --release' in hunter-sim-rs/)", 
                     font=('Arial', 9), foreground='red').pack(side=tk.LEFT, padx=10)
        
        # Progressive evolution
        row5 = ttk.Frame(settings_frame)
        row5.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_progressive = tk.BooleanVar(value=True)
        ttk.Checkbutton(row5, text="📈 Progressive Evolution (5% → 100% points curriculum)", 
                        variable=self.global_use_progressive).pack(side=tk.LEFT, padx=5)
        ttk.Label(row5, text="Finds efficient builds faster by learning at each tier", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Evolutionary optimizer
        row6 = ttk.Frame(settings_frame)
        row6.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_use_evolutionary = tk.BooleanVar(value=True)
        ttk.Checkbutton(row6, text="🧬 Use Evolutionary Optimizer (learns good/bad patterns)", 
                        variable=self.global_use_evolutionary).pack(side=tk.LEFT, padx=5)
        ttk.Label(row6, text="Recommended for high-level builds", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # Apply to all button
        row7 = ttk.Frame(settings_frame)
        row7.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(row7, text="📋 Apply Settings to All Hunters", 
                   command=self._apply_global_settings).pack(side=tk.LEFT, padx=5)
        ttk.Label(row7, text="(Updates each hunter's Run tab)", 
                 font=('Arial', 9, 'italic')).pack(side=tk.LEFT, padx=10)
        
        # ============ GLOBAL BONUSES (shared across all hunters) ============
        bonuses_frame = ttk.LabelFrame(scrollable, text="💎 Global Bonuses (Shared Across All Hunters)")
        bonuses_frame.pack(fill=tk.X, padx=20, pady=10)
        
        bonuses_row1 = ttk.Frame(bonuses_frame)
        bonuses_row1.pack(fill=tk.X, padx=10, pady=5)
        
        # Shard Milestone
        ttk.Label(bonuses_row1, text="Shard Milestone:").pack(side=tk.LEFT, padx=5)
        self.global_shard_milestone = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row1, textvariable=self.global_shard_milestone, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        
        # Diamond Loot
        ttk.Label(bonuses_row1, text="💎 Diamond Loot:").pack(side=tk.LEFT, padx=15)
        self.global_diamond_loot = tk.IntVar(value=0)
        ttk.Spinbox(bonuses_row1, textvariable=self.global_diamond_loot, from_=0, to=100, width=5).pack(side=tk.LEFT, padx=5)
        
        # IAP Pack
        self.global_iap_travpack = tk.BooleanVar(value=False)
        ttk.Checkbutton(bonuses_row1, text="IAP Pack", variable=self.global_iap_travpack).pack(side=tk.LEFT, padx=15)
        
        bonuses_row2 = ttk.Frame(bonuses_frame)
        bonuses_row2.pack(fill=tk.X, padx=10, pady=5)
        
        # Ultima Multiplier
        ttk.Label(bonuses_row2, text="Ultima Multiplier:").pack(side=tk.LEFT, padx=5)
        self.global_ultima_multiplier = tk.DoubleVar(value=1.0)
        ttk.Entry(bonuses_row2, textvariable=self.global_ultima_multiplier, width=6).pack(side=tk.LEFT, padx=5)
        ttk.Label(bonuses_row2, text="(Enter displayed bonus, not upgrade level)", 
                  font=('Arial', 8), foreground='gray').pack(side=tk.LEFT, padx=5)
        
        # ============ RUN CONTROLS ============
        run_frame = ttk.LabelFrame(scrollable, text="🚀 Run Optimizations")
        run_frame.pack(fill=tk.X, padx=20, pady=10)
        
        # Run All button
        all_btn_frame = ttk.Frame(run_frame)
        all_btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.run_all_btn = ttk.Button(all_btn_frame, text="🚀 Run ALL Hunters (Borge + Knox + Ozzy)", 
                                       command=self._run_all_hunters)
        self.run_all_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_all_btn = ttk.Button(all_btn_frame, text="⏹️ Stop All", 
                                        command=self._stop_all_hunters, state=tk.DISABLED)
        self.stop_all_btn.pack(side=tk.LEFT, padx=5)
        
        # Hunter selection checkboxes
        ttk.Separator(run_frame, orient='horizontal').pack(fill=tk.X, padx=10, pady=5)
        
        selection_label = ttk.Label(run_frame, text="Select hunters to optimize (runs sequentially):", font=('Arial', 10, 'bold'))
        selection_label.pack(padx=10, pady=5)
        
        selection_frame = ttk.Frame(run_frame)
        selection_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.run_borge_var = tk.BooleanVar(value=True)
        self.run_knox_var = tk.BooleanVar(value=True)
        self.run_ozzy_var = tk.BooleanVar(value=True)
        
        ttk.Checkbutton(selection_frame, text="🛡️ Borge", variable=self.run_borge_var).pack(side=tk.LEFT, padx=20)
        ttk.Checkbutton(selection_frame, text="🔫 Knox", variable=self.run_knox_var).pack(side=tk.LEFT, padx=20)
        ttk.Checkbutton(selection_frame, text="🐙 Ozzy", variable=self.run_ozzy_var).pack(side=tk.LEFT, padx=20)
        
        # Status
        status_frame = ttk.LabelFrame(scrollable, text="📊 Status")
        status_frame.pack(fill=tk.X, padx=20, pady=10)
        
        self.all_status = ttk.Label(status_frame, text="Ready to run optimizations")
        self.all_status.pack(padx=20, pady=10)
        
        # Hunter status indicators with mini progress bars and battle animations
        # Borge (Red theme)
        borge_frame = ttk.Frame(status_frame)
        borge_frame.pack(fill=tk.X, padx=10, pady=3)
        self.borge_status = ttk.Label(borge_frame, text="🛡️ Borge: Idle", width=25, style="Borge.TLabel")
        self.borge_status.pack(side=tk.LEFT, padx=5)
        self.borge_battle = tk.Label(borge_frame, text="", width=15, font=('Segoe UI Emoji', 10))
        self.borge_battle.pack(side=tk.LEFT, padx=2)
        self.borge_progress = tk.DoubleVar(value=0)
        self.borge_progress_bar = ttk.Progressbar(borge_frame, variable=self.borge_progress, maximum=100, length=150, style="Borge.Horizontal.TProgressbar")
        self.borge_progress_bar.pack(side=tk.LEFT, padx=5)
        self.borge_eta = ttk.Label(borge_frame, text="", width=12, style="BorgeLight.TLabel")
        self.borge_eta.pack(side=tk.LEFT, padx=5)
        
        # Knox (Blue theme)
        knox_frame = ttk.Frame(status_frame)
        knox_frame.pack(fill=tk.X, padx=10, pady=3)
        self.knox_status = ttk.Label(knox_frame, text="🔫 Knox: Idle", width=25, style="Knox.TLabel")
        self.knox_status.pack(side=tk.LEFT, padx=5)
        self.knox_battle = tk.Label(knox_frame, text="", width=15, font=('Segoe UI Emoji', 10))
        self.knox_battle.pack(side=tk.LEFT, padx=2)
        self.knox_progress = tk.DoubleVar(value=0)
        self.knox_progress_bar = ttk.Progressbar(knox_frame, variable=self.knox_progress, maximum=100, length=150, style="Knox.Horizontal.TProgressbar")
        self.knox_progress_bar.pack(side=tk.LEFT, padx=5)
        self.knox_eta = ttk.Label(knox_frame, text="", width=12, style="KnoxLight.TLabel")
        self.knox_eta.pack(side=tk.LEFT, padx=5)
        
        # Ozzy (Green theme)
        ozzy_frame = ttk.Frame(status_frame)
        ozzy_frame.pack(fill=tk.X, padx=10, pady=3)
        self.ozzy_status = ttk.Label(ozzy_frame, text="🐙 Ozzy: Idle", width=25, style="Ozzy.TLabel")
        self.ozzy_status.pack(side=tk.LEFT, padx=5)
        self.ozzy_battle = tk.Label(ozzy_frame, text="", width=15, font=('Segoe UI Emoji', 10))
        self.ozzy_battle.pack(side=tk.LEFT, padx=2)
        self.ozzy_progress = tk.DoubleVar(value=0)
        self.ozzy_progress_bar = ttk.Progressbar(ozzy_frame, variable=self.ozzy_progress, maximum=100, length=150, style="Ozzy.Horizontal.TProgressbar")
        self.ozzy_progress_bar.pack(side=tk.LEFT, padx=5)
        self.ozzy_eta = ttk.Label(ozzy_frame, text="", width=12, style="OzzyLight.TLabel")
        self.ozzy_eta.pack(side=tk.LEFT, padx=5)
        
        # Start battle animation loop
        self.battle_frame = 0
        self._animate_battles()
        
        # Save All button
        save_frame = ttk.LabelFrame(scrollable, text="💾 Save All Builds")
        save_frame.pack(fill=tk.X, padx=20, pady=10)
        
        ttk.Button(save_frame, text="💾 Save All Builds to IRL Builds folder", 
                   command=self._save_all_builds).pack(pady=10)
    
    def _animate_battles(self):
        """Animate hunter battle scenes when running."""
        import random
        
        # Enemy emojis for battles
        enemies = ['👹', '👺', '👻', '💀', '🐉', '🐲', '👾', '🤖', '🦇', '🕷️', '🦂', '🐍', '🔥', '⚡']
        attacks = ['⚔️', '💥', '✨', '🌟', '💫', '🔥', '⚡', '💢', '💨']
        
        # Battle scenes for each hunter (hunter attacks enemy)
        battle_labels = {
            "Borge": (self.borge_battle, '🛡️', self.hunter_tabs["Borge"]),
            "Knox": (self.knox_battle, '🔫', self.hunter_tabs["Knox"]),
            "Ozzy": (self.ozzy_battle, '🐙', self.hunter_tabs["Ozzy"]),
        }
        
        self.battle_frame += 1
        
        for name, (label, icon, tab) in battle_labels.items():
            if tab.is_running:
                # Animated battle!
                enemy = random.choice(enemies)
                attack = random.choice(attacks)
                
                # Alternate between attack frames
                if self.battle_frame % 2 == 0:
                    label.configure(text=f"{icon} {attack} {enemy}")
                else:
                    label.configure(text=f"{icon} 💥 {enemy}")
            elif tab.results:
                # Victory pose
                label.configure(text=f"{icon} 🏆 ✨")
            else:
                # Idle - resting
                label.configure(text=f"{icon} 💤")
        
        # Schedule next frame (300ms = ~3fps animation)
        self.root.after(300, self._animate_battles)
    
    def _init_battle_arena(self):
        """Initialize the battle arena with hunters and enemies using CIFI-accurate mechanics."""
        import random
        
        self.arena_width = 350
        self.arena_height = 400
        
        # Bench positions (bottom of arena) - where hunters rest
        self.bench_y = self.arena_height - 50  # Bench is near bottom
        self.bench_positions = {
            "Borge": {"x": 80, "y": self.bench_y},
            "Knox": {"x": 175, "y": self.bench_y},
            "Ozzy": {"x": 270, "y": self.bench_y},
        }
        
        # Battle field area (where fighting happens)
        self.field_top = 50  # Below stage header
        self.field_bottom = self.arena_height - 90  # Above bench
        
        # Hunter-specific arena themes
        self.arena_themes = {
            "Borge": {
                "bg_color": "#1a0a0a",  # Dark red
                "grid_color": "#3a1515",  # Crimson grid
                "accent_color": "#DC3545",
                "field_color": "#2a0505",
                "particles": ["🔥", "💀", "⚔️"],
                "description": "Crimson Battleground"
            },
            "Knox": {
                "bg_color": "#0a0a1a",  # Dark blue
                "grid_color": "#151535",  # Navy grid
                "accent_color": "#0D6EFD",
                "field_color": "#050520",
                "particles": ["⚡", "💥", "🎯"],
                "description": "Tech Arena"
            },
            "Ozzy": {
                "bg_color": "#0a1a0a",  # Dark green
                "grid_color": "#153515",  # Forest grid
                "accent_color": "#198754",
                "field_color": "#052005",
                "particles": ["🌀", "☠️", "🧪"],
                "description": "Toxic Swamp"
            }
        }
        
        # Current active hunter for theming (or mixed if multiple)
        self.active_arena_theme = None
        
        # Track last avg stage for each hunter (for arena stage syncing)
        self.hunter_last_avg_stage = {"Borge": 0, "Knox": 0, "Ozzy": 0}
        
        # Hunter data with CIFI-like stats - start on bench
        self.arena_hunters = {
            "Borge": {"x": 80, "y": self.bench_y, "dx": 2, "dy": 1.5, "icon": "🛡", "color": "#DC3545", 
                      "hp": 150, "max_hp": 150, "kills": 0, "level": 1, "xp": 0, "speed_boost": 0, 
                      "damage": 3, "base_damage": 3, "lifesteal": 0.15,
                      "attack_timer": 0, "attack_speed": 3.5, "attack_cooldown": 0,
                      "crit_chance": 0.15, "crit_damage": 1.8, "dr": 0.10, "regen": 0,
                      "on_field": False, "returning_to_bench": False,
                      "field_position": {"x": 100, "y": 130},
                      "last_avg_stage": 0, "last_max_stage": 0, "last_gen": 0},
            "Knox": {"x": 175, "y": self.bench_y, "dx": 2.5, "dy": -1, "icon": "🔫", "color": "#0D6EFD", 
                     "hp": 80, "max_hp": 80, "kills": 0, "level": 1, "xp": 0, "speed_boost": 0,
                     "damage": 2, "base_damage": 2, "lifesteal": 0, "regen": 0,
                     "attack_timer": 0, "attack_speed": 1.2, "attack_cooldown": 0,
                     "crit_chance": 0.25, "crit_damage": 2.0, "dr": 0.0,
                     "attack_range": 120,
                     "on_field": False, "returning_to_bench": False,
                     "field_position": {"x": 175, "y": 175},
                     "last_avg_stage": 0, "last_max_stage": 0, "last_gen": 0},
            "Ozzy": {"x": 270, "y": self.bench_y, "dx": 1.5, "dy": 2, "icon": "🐙", "color": "#198754", 
                     "hp": 100, "max_hp": 100, "kills": 0, "level": 1, "xp": 0, "speed_boost": 0,
                     "damage": 2, "base_damage": 2, "lifesteal": 0, "regen": 0.5,
                     "attack_timer": 0, "attack_speed": 2.0, "attack_cooldown": 0,
                     "crit_chance": 0.20, "crit_damage": 1.5, "dr": 0.05,
                     "poison_chance": 0.30, "poison_damage": 1,
                     "on_field": False, "returning_to_bench": False,
                     "field_position": {"x": 250, "y": 220},
                     "last_avg_stage": 0, "last_max_stage": 0, "last_gen": 0},
        }
        
        # Enemies: list of {x, y, dx, dy, icon, hp, max_hp, id, is_boss, power, speed, poison_stacks}
        self.arena_enemies = []
        self.enemy_icons = ['👹', '👺', '👻', '💀', '🐉', '👾', '🤖', '🦇', '🕷️', '🦂', '🐍', '🔥']
        self.boss_icons = ['🐲', '👿', '☠️', '🦖', '👑', '🎃']
        self.next_enemy_id = 0
        
        # Projectiles: list of {x, y, target_x, target_y, icon, speed, hunter}
        self.arena_projectiles = []
        
        # Attack effects: list of {x, y, icon, ttl, size}
        self.arena_effects = []
        
        # Game state
        self.arena_stage = 1
        self.arena_total_kills = 0
        self.kills_for_next_stage = 10
        self.boss_active = False
        self.victory_mode = False
        self.victory_timer = 0
        self.fireworks = []
        self.was_running = False
        self.arena_tick = 0  # For timing regen/poison
        
        # Track which hunters were running last frame (for detecting start/stop)
        self.prev_running = {"Borge": False, "Knox": False, "Ozzy": False}
        
        # Spawn initial enemies (fewer - stationary targets)
        for _ in range(2):
            self._spawn_enemy()
        
        # Start arena animation
        self._animate_arena()
    
    def _multi_wasm_arena(self, stage: int) -> float:
        """CIFI-accurate stage scaling for arena enemies."""
        if stage < 15:  # Scaled down from 150 for arena
            return 1.0
        
        result = 1.0
        # Breakpoints scaled for arena (divide real stages by 10)
        if stage > 14:
            result *= 1 + (stage - 14) * 0.06
        if stage > 19:
            result *= 1 + (stage - 19) * 0.06
        if stage > 24:
            result *= 1 + (stage - 24) * 0.06
        if stage > 29:
            result *= 1 + (stage - 29) * 0.06
        # Exponential after stage 35
        if stage > 35:
            result *= 1.02 ** (stage - 35)
        
        return result
    
    def _spawn_enemy(self, is_boss=False):
        """Spawn a new enemy with CIFI-accurate stat scaling."""
        import random
        
        # Spawn enemies from all four edges of the field
        existing_positions = [(e["x"], e["y"]) for e in self.arena_enemies]
        
        # Try to find a good spawn position
        attempts = 0
        while attempts < 20:
            # Choose a random edge: 0=top, 1=right, 2=bottom, 3=left
            edge = random.randint(0, 3)
            
            if edge == 0:  # Top
                x = random.randint(50, self.arena_width - 50)
                y = random.randint(self.field_top, self.field_top + 50)
            elif edge == 1:  # Right
                x = random.randint(self.arena_width - 80, self.arena_width - 30)
                y = random.randint(self.field_top + 30, self.field_bottom - 30)
            elif edge == 2:  # Bottom (but above bench)
                x = random.randint(50, self.arena_width - 50)
                y = random.randint(self.field_bottom - 60, self.field_bottom - 20)
            else:  # Left
                x = random.randint(30, 80)
                y = random.randint(self.field_top + 30, self.field_bottom - 30)
            
            # Check if position is far enough from all other enemies
            min_dist = 70  # Minimum distance between enemies
            is_good = True
            for ex, ey in existing_positions:
                dist = math.sqrt((x - ex)**2 + (y - ey)**2)
                if dist < min_dist:
                    is_good = False
                    break
            
            if is_good:
                break
            attempts += 1
        
        # If we couldn't find a spot, just place it somewhere random in the field
        if attempts >= 20:
            x = random.randint(50, self.arena_width - 50)
            y = random.randint(self.field_top + 20, self.field_bottom - 20)
        
        # CIFI-style scaling: base stats * stage multiplier
        stage = self.arena_stage
        stage_mult = self._multi_wasm_arena(stage)
        post_10_mult = 2.85 if stage > 10 else 1.0  # Like post-100 in real game
        
        # Base enemy stats (scaled down for arena)
        base_hp = (2 + stage * 0.5) * post_10_mult * stage_mult
        base_power = (0.5 + stage * 0.15) * post_10_mult * stage_mult
        base_speed = max(1.5, 4.5 - stage * 0.03)  # Enemies attack faster at higher stages
        crit_chance = min(0.25, 0.03 + stage * 0.004)  # Capped at 25%
        crit_damage = min(2.5, 1.2 + stage * 0.008)  # Capped at 250%
        
        # Color enemies based on stage (low=green, mid=yellow, high=red, very high=purple)
        if stage < 10:
            enemy_color = "#90EE90"  # Light green
        elif stage < 20:
            enemy_color = "#FFD700"  # Gold
        elif stage < 30:
            enemy_color = "#FF8C00"  # Dark orange
        elif stage < 40:
            enemy_color = "#DC3545"  # Red
        else:
            enemy_color = "#8B00FF"  # Purple
        
        if is_boss:
            # Boss spawns in center of the field
            x = self.arena_width // 2
            y = (self.field_top + self.field_bottom) // 2  # Center of field
            # Boss HP = 90x enemy (Borge-style), Power = 3.63x
            boss_hp = base_hp * 90
            boss_power = base_power * 3.63
            boss_speed = base_speed * 2.1  # Bosses attack slower
            
            self.arena_enemies.append({
                "x": x, "y": y, "dx": 0, "dy": 0,
                "icon": random.choice(self.boss_icons),
                "color": "#8B0000",  # Dark red for bosses
                "hp": boss_hp, "max_hp": boss_hp,
                "power": boss_power, "base_power": boss_power,
                "speed": boss_speed, "base_speed": boss_speed,
                "attack_cooldown": int(boss_speed * 10),
                "crit_chance": min(0.25, crit_chance + 0.08),
                "crit_damage": min(2.5, crit_damage + 0.5),
                "id": self.next_enemy_id,
                "is_boss": True,
                "enrage_stacks": 0,
                "poison_stacks": 0,
                "last_attacker": None,
            })
            self.boss_active = True
        else:
            self.arena_enemies.append({
                "x": x, "y": y, "dx": 0, "dy": 0,
                "icon": random.choice(self.enemy_icons),
                "color": enemy_color,
                "hp": base_hp, "max_hp": base_hp,
                "power": base_power, "base_power": base_power,
                "speed": base_speed, "base_speed": base_speed,
                "attack_cooldown": int(base_speed * 10),
                "crit_chance": crit_chance,
                "crit_damage": crit_damage,
                "id": self.next_enemy_id,
                "is_boss": False,
                "enrage_stacks": 0,
                "poison_stacks": 0,
                "last_attacker": None,
            })
        self.next_enemy_id += 1
    
    def _spawn_firework(self):
        """Spawn a firework for victory celebration."""
        import random
        
        colors = ['#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#FF00FF', '#00FFFF', '#FFD700']
        x = random.randint(50, self.arena_width - 50)
        y = random.randint(50, self.arena_height - 100)
        
        self.fireworks.append({
            "x": x, "y": y,
            "particles": [
                {"dx": random.uniform(-3, 3), "dy": random.uniform(-3, 3), 
                 "color": random.choice(colors), "ttl": random.randint(15, 30)}
                for _ in range(12)
            ],
            "ttl": 30
        })
    
    def _animate_arena(self):
        """Animate the battle arena with hunter-themed visuals."""
        import random
        import math
        
        # Check if any hunter is running
        any_running = any(tab.is_running for tab in self.hunter_tabs.values())
        running_hunters = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        canvas = self.battle_canvas
        canvas.delete("all")
        
        # Detect when optimization just completed (was running, now stopped)
        # Victory mode is now triggered per-hunter in the loop below
        self.was_running = any_running
        
        # Determine arena theme based on active hunters
        if len(running_hunters) == 1:
            theme = self.arena_themes[running_hunters[0]]
            theme_name = running_hunters[0]
        elif len(running_hunters) > 1:
            # Mixed theme - blend colors
            theme = {
                "bg_color": "#1a1a1a",
                "grid_color": "#2a2a2a",
                "accent_color": "#FFFFFF",
                "field_color": "#0f0f0f",
                "particles": ["⚔️", "💥", "✨"],
                "description": "Battle Royale"
            }
            theme_name = "Mixed"
        else:
            # No one running - neutral theme
            theme = {
                "bg_color": "#1a1a2e",
                "grid_color": "#2a2a4a",
                "accent_color": "#888888",
                "field_color": "#15152e",
                "particles": ["💤"],
                "description": "Resting"
            }
            theme_name = None
        
        # Check which hunters just started or stopped running
        for name, hunter in self.arena_hunters.items():
            tab = self.hunter_tabs[name]
            was_running = self.prev_running.get(name, False)
            is_running = tab.is_running
            
            if is_running and not was_running:
                # Hunter just started - walk onto field AND RESET ARENA
                hunter["on_field"] = True
                hunter["returning_to_bench"] = False
                # Assign a good field position for this hunter
                if name == "Borge":
                    hunter["field_position"] = {"x": 100, "y": (self.field_top + self.field_bottom) // 2 - 30}
                elif name == "Knox":
                    hunter["field_position"] = {"x": 175, "y": (self.field_top + self.field_bottom) // 2}
                else:  # Ozzy
                    hunter["field_position"] = {"x": 250, "y": (self.field_top + self.field_bottom) // 2 + 30}
                    
                # RESET arena to stage 1 when any new run starts
                self.arena_stage = 1
                self.kills_for_next_stage = 15
                self.arena_total_kills = 0
                
                # Reset all hunters' kills and stats
                for h in self.arena_hunters.values():
                    h["kills"] = 0
                    h["hp"] = h["max_hp"]
                    h["level"] = 1
                    h["xp"] = 0
                    h["damage"] = h["base_damage"]
                
                # Clear enemies and spawn fresh ones
                self.arena_enemies.clear()
                self.arena_effects.clear()
                self.arena_projectiles.clear()
                self.boss_active = False
                for _ in range(4):
                    self._spawn_enemy()
                    
            elif not is_running and was_running:
                # Hunter just finished - update their results and trigger victory
                hunter["returning_to_bench"] = True
                
                # Get last avg stage from tab results
                if tab.results:
                    best_result = max(tab.results, key=lambda r: r.avg_final_stage)
                    hunter["last_avg_stage"] = best_result.avg_final_stage
                    hunter["last_max_stage"] = best_result.highest_stage  # Fixed: was max_final_stage
                    hunter["last_gen"] = len(tab.results)
                    
                    # Update leaderboard
                    self._update_leaderboard(name, hunter)
                
                # Trigger victory mode for this hunter's completion
                if hunter["kills"] > 0:
                    self.victory_mode = True
                    self.victory_timer = 60  # ~3 seconds of celebration
            
            self.prev_running[name] = is_running
        
        # SYNC ARENA STAGE WITH SIMULATION PROGRESS
        # When any hunter is running, sync arena stage to their best_avg_stage (scaled down)
        if running_hunters:
            # Get the highest best_avg_stage from all running hunters
            max_sim_stage = 0
            for name in running_hunters:
                tab = self.hunter_tabs[name]
                # Check if tab has best_avg_stage attribute (set during optimization)
                if hasattr(tab, 'best_avg_stage') and tab.best_avg_stage > 0:
                    max_sim_stage = max(max_sim_stage, tab.best_avg_stage)
            
            # Scale simulation stage to arena stage (1:10 ratio)
            # e.g., sim stage 500 -> arena stage 50
            if max_sim_stage > 0:
                target_arena_stage = max(1, int(max_sim_stage / 10))
                # Gradually increase arena stage toward target (smooth progression)
                if target_arena_stage > self.arena_stage:
                    old_stage = self.arena_stage
                    self.arena_stage = target_arena_stage
                    self.kills_for_next_stage = self.arena_stage * 15
                    
                    # Scale hunter stats to match their power at this stage
                    for h_name, hunter in self.arena_hunters.items():
                        if hunter.get("on_field"):
                            stage_mult = self._multi_wasm_arena(self.arena_stage)
                            hunter["damage"] = hunter["base_damage"] * stage_mult * (1 + hunter["level"] * 0.15)
                            hunter["max_hp"] = int(hunter["max_hp"] * (1 + (self.arena_stage - old_stage) * 0.05))
                            hunter["hp"] = min(hunter["hp"] + 20, hunter["max_hp"])  # Heal a bit on stage up
                    
                    # Spawn effect to show stage increase
                    self.arena_effects.append({
                        "x": self.arena_width // 2, "y": 50, 
                        "icon": f"📈 Stage {self.arena_stage}!", "ttl": 30, "is_text": True,
                        "color": "#FFD700", "float": True
                    })
        
        # Draw themed background
        canvas.configure(bg=theme["bg_color"])
        
        # Draw themed grid
        grid_color = theme["grid_color"]
        for i in range(0, self.arena_width, 30):
            canvas.create_line(i, 0, i, self.arena_height, fill=grid_color, width=1)
        for i in range(0, self.arena_height, 30):
            canvas.create_line(0, i, self.arena_width, i, fill=grid_color, width=1)
        
        # Draw themed field area with gradient effect
        field_color = theme["field_color"]
        canvas.create_rectangle(10, self.field_top - 5, self.arena_width - 10, self.field_bottom + 5,
                               fill=field_color, outline=theme["accent_color"], width=1)
        
        # Add floating theme particles when running
        if any_running and random.random() < 0.15:
            particle = random.choice(theme["particles"])
            px = random.randint(20, self.arena_width - 20)
            py = random.randint(self.field_top, self.field_bottom)
            self.arena_effects.append({
                "x": px, "y": py, "icon": particle, "ttl": 20, "is_text": False,
                "color": theme["accent_color"], "float": True
            })
        
        # Draw the bench at the bottom
        bench_y_top = self.bench_y - 20
        canvas.create_rectangle(20, bench_y_top, self.arena_width - 20, self.bench_y + 20,
                               fill='#3d2b1f', outline='#5c4033', width=3)
        canvas.create_text(self.arena_width // 2, bench_y_top - 8, text="🪑 BENCH 🪑",
                          fill='#8B4513', font=('Arial', 8, 'bold'))
        
        # Draw bench seat spots
        for bx in [80, 175, 270]:
            canvas.create_oval(bx-10, self.bench_y-6, bx+10, self.bench_y+6,
                              fill='#5c4033', outline='#3d2b1f')
        
        # Draw title and stage - show which hunter's arena it is
        if self.victory_mode:
            canvas.create_text(self.arena_width // 2, 12, text="🎉 VICTORY! 🎉", 
                              fill='#FFD700', font=('Arial', 11, 'bold'))
            canvas.create_text(self.arena_width // 2, 28, 
                              text=f"Stage {self.arena_stage} | Kills: {self.arena_total_kills}", 
                              fill='#FFFFFF', font=('Arial', 8))
        elif any_running:
            # Show theme name and stage
            theme_text = f"⚔️ {theme['description']} - Stage {self.arena_stage} ⚔️"
            canvas.create_text(self.arena_width // 2, 12, text=theme_text, 
                              fill=theme["accent_color"], font=('Arial', 9, 'bold'))
            
            # Show simulation progress for running hunters
            sim_status_y = 28
            for hname in running_hunters:
                tab = self.hunter_tabs[hname]
                if hasattr(tab, 'progress_var') and hasattr(tab, 'results'):
                    progress = tab.progress_var.get()
                    builds_tested = len(tab.results)
                    best_stage = max((r.avg_final_stage for r in tab.results), default=0)
                    hunter_icon = '🛡️' if hname == 'Borge' else '🔫' if hname == 'Knox' else '🐙'
                    sim_text = f"{hunter_icon} {progress:.0f}% | {builds_tested} builds | Best: {best_stage:.0f}"
                    canvas.create_text(self.arena_width // 2, sim_status_y, 
                                      text=sim_text, fill='#CCCCCC', font=('Arial', 7))
                    sim_status_y += 10
        else:
            canvas.create_text(self.arena_width // 2, 15, text="🏰 Hunters Resting 🏰", 
                              fill='#888888', font=('Arial', 11, 'bold'))
        
        # Victory mode - spawn fireworks
        if self.victory_mode:
            self.victory_timer -= 1
            if random.random() < 0.3:
                self._spawn_firework()
            if self.victory_timer <= 0:
                self.victory_mode = False
        
        # Draw and update fireworks
        for fw in self.fireworks[:]:
            for p in fw["particles"]:
                px = fw["x"] + p["dx"] * (30 - fw["ttl"])
                py = fw["y"] + p["dy"] * (30 - fw["ttl"])
                size = max(2, p["ttl"] // 5)
                canvas.create_oval(px-size, py-size, px+size, py+size, fill=p["color"], outline='')
                p["ttl"] -= 1
            fw["ttl"] -= 1
            if fw["ttl"] <= 0:
                self.fireworks.remove(fw)
        
        # Update and draw projectiles (Knox's bullets)
        for projectile in self.arena_projectiles[:]:
            # Find target enemy
            target_enemy = None
            for e in self.arena_enemies:
                if e["id"] == projectile["target_enemy_id"]:
                    target_enemy = e
                    break
            
            if target_enemy:
                # Move toward target
                dx = target_enemy["x"] - projectile["x"]
                dy = target_enemy["y"] - projectile["y"]
                dist = math.sqrt(dx*dx + dy*dy)
                
                if dist < projectile["speed"]:
                    # Hit! Apply damage
                    target_enemy["hp"] -= projectile["damage"]
                    target_enemy["last_attacker"] = projectile["hunter"]
                    
                    # Show hit effect
                    self.arena_effects.append({
                        "x": target_enemy["x"], "y": target_enemy["y"],
                        "icon": "💥", "ttl": 5, "is_text": False,
                        "color": projectile.get("color", "#0D6EFD")
                    })
                    
                    # Show crit text
                    if projectile.get("is_crit"):
                        self.arena_effects.append({
                            "x": target_enemy["x"], "y": target_enemy["y"] - 20,
                            "icon": "💥CRIT!", "ttl": 12, "is_text": True,
                            "color": "#FFD700"
                        })
                    
                    # Check if enemy died
                    if target_enemy["hp"] <= 0:
                        self.arena_effects.append({
                            "x": target_enemy["x"], "y": target_enemy["y"],
                            "icon": "💥", "ttl": 10, "is_text": False
                        })
                        self.arena_hunters["Knox"]["kills"] += 1
                        self.arena_hunters["Knox"]["xp"] += 2 if target_enemy.get("is_boss") else 1
                        self.arena_total_kills += 1
                        if target_enemy.get("is_boss"):
                            self.boss_active = False
                        self.arena_enemies.remove(target_enemy)
                    
                    self.arena_projectiles.remove(projectile)
                else:
                    # Move projectile
                    projectile["x"] += (dx / dist) * projectile["speed"]
                    projectile["y"] += (dy / dist) * projectile["speed"]
                    
                    # Draw projectile
                    proj_color = projectile.get("color", "#0D6EFD")
                    canvas.create_text(projectile["x"], projectile["y"], 
                                     text=projectile["icon"],
                                     fill=proj_color,
                                     font=('Segoe UI Emoji', 12))
            else:
                # Target dead, remove projectile
                self.arena_projectiles.remove(projectile)
        
        # Update and draw enemies (enemies attack hunters!)
        for enemy in self.arena_enemies[:]:
            # Remove if somehow off screen
            if enemy["x"] < -20 or enemy["x"] > self.arena_width + 20:
                self.arena_enemies.remove(enemy)
                continue
            
            # Process poison damage (every 10 ticks)
            if enemy.get("poison_stacks", 0) > 0 and self.arena_tick % 10 == 0:
                poison_dmg = enemy["poison_stacks"] * self.arena_hunters["Ozzy"].get("poison_damage", 1)
                enemy["hp"] -= poison_dmg
                self.arena_effects.append({
                    "x": enemy["x"], "y": enemy["y"] + 15,
                    "icon": "🤢", "ttl": 6, "is_text": False
                })
                # Reduce poison stacks over time
                if random.random() < 0.3:
                    enemy["poison_stacks"] = max(0, enemy["poison_stacks"] - 1)
                
                # Check if poison killed the enemy
                if enemy["hp"] <= 0:
                    self.arena_effects.append({
                        "x": enemy["x"], "y": enemy["y"],
                        "icon": "☠️", "ttl": 10, "is_text": False
                    })
                    self.arena_hunters["Ozzy"]["kills"] += 1
                    self.arena_hunters["Ozzy"]["xp"] += 2 if enemy["is_boss"] else 1
                    self.arena_total_kills += 1
                    if enemy["is_boss"]:
                        self.boss_active = False
                    self.arena_enemies.remove(enemy)
                    if len(self.arena_enemies) < 5 + self.arena_stage:
                        self._spawn_enemy()
                    continue
            
            # Enemy attack logic - find nearest hunter and attack
            if any_running and enemy.get("attack_cooldown", 0) <= 0:
                nearest_hunter = None
                nearest_dist = float('inf')
                for hname, h in self.arena_hunters.items():
                    if self.hunter_tabs[hname].is_running:
                        dist = math.sqrt((enemy["x"] - h["x"])**2 + (enemy["y"] - h["y"])**2)
                        if dist < nearest_dist:
                            nearest_dist = dist
                            nearest_hunter = (hname, h)
                
                # Attack if hunter is close enough
                attack_range = 60 if enemy["is_boss"] else 50
                if nearest_hunter and nearest_dist < attack_range:
                    hname, h = nearest_hunter
                    # Calculate damage with crits
                    is_crit = random.random() < enemy.get("crit_chance", 0.1)
                    damage = enemy.get("power", 1)
                    if is_crit:
                        damage *= enemy.get("crit_damage", 1.5)
                    
                    # Apply hunter damage reduction
                    damage *= (1 - h.get("dr", 0))
                    
                    # Deal damage
                    h["hp"] -= damage
                    
                    # Boss enrage - speed up after each attack
                    if enemy["is_boss"]:
                        enemy["enrage_stacks"] = enemy.get("enrage_stacks", 0) + 1
                        stacks = enemy["enrage_stacks"]
                        # CIFI enrage: speed reduction per stack
                        base_speed = enemy.get("base_speed", 3.0)
                        enemy["speed"] = max(0.5, base_speed - (stacks * base_speed / 200))
                        # At 200 stacks: 3x power, always crit
                        if stacks >= 20:  # Scaled down for arena (200/10)
                            enemy["power"] = enemy.get("base_power", 1) * 3
                            enemy["crit_chance"] = 1.0
                            if stacks == 20:
                                self.arena_effects.append({
                                    "x": enemy["x"], "y": enemy["y"] - 30,
                                    "icon": "💀MAX ENRAGE💀", "ttl": 40, "is_text": True
                                })
                    
                    # Show attack effect
                    attack_icon = '👊' if not is_crit else '💥'
                    self.arena_effects.append({
                        "x": (enemy["x"] + h["x"]) / 2,
                        "y": (enemy["y"] + h["y"]) / 2,
                        "icon": attack_icon, "ttl": 5, "is_text": False
                    })
                    
                    if is_crit:
                        self.arena_effects.append({
                            "x": h["x"], "y": h["y"] - 20,
                            "icon": "💢CRIT!", "ttl": 12, "is_text": True
                        })
                    
                    # Set cooldown
                    enemy["attack_cooldown"] = int(enemy.get("speed", 2.0) * 10)
                    
                    # Hunter death? Respawn after delay
                    if h["hp"] <= 0:
                        h["hp"] = 0
                        self.arena_effects.append({
                            "x": h["x"], "y": h["y"],
                            "icon": "💀", "ttl": 20, "is_text": False
                        })
                        # Respawn at field position with reduced HP
                        h["hp"] = h["max_hp"] * 0.5
                        field_pos = h.get("field_position", {"x": 175, "y": 200})
                        h["x"], h["y"] = field_pos["x"], field_pos["y"]
            
            # Decrease attack cooldown
            if enemy.get("attack_cooldown", 0) > 0:
                enemy["attack_cooldown"] -= 1
            
            # Draw enemy with color
            font_size = 22 if enemy["is_boss"] else 16
            enemy_color = enemy.get("color", "#FFFFFF")
            canvas.create_text(enemy["x"], enemy["y"], text=enemy["icon"], 
                              fill=enemy_color, font=('Segoe UI Emoji', font_size))
            
            # Draw poison indicator
            if enemy.get("poison_stacks", 0) > 0:
                canvas.create_text(enemy["x"] + 18, enemy["y"] - 5, 
                                  text=f"☠{enemy['poison_stacks']}", 
                                  fill='#00FF00', font=('Arial', 7, 'bold'))
            
            # Draw enrage indicator for bosses
            if enemy["is_boss"] and enemy.get("enrage_stacks", 0) > 0:
                stacks = enemy["enrage_stacks"]
                rage_color = '#FF0000' if stacks >= 20 else '#FF6600' if stacks >= 10 else '#FFAA00'
                canvas.create_text(enemy["x"], enemy["y"] + 20, 
                                  text=f"🔥{stacks}", 
                                  fill=rage_color, font=('Arial', 8, 'bold'))
            
            # Draw health bar for enemies with HP > 1 or bosses
            if enemy["hp"] > 0 and (enemy["is_boss"] or enemy["max_hp"] > 1):
                bar_width = 40 if enemy["is_boss"] else 25
                hp_pct = enemy["hp"] / enemy["max_hp"]
                bar_y = enemy["y"] - 18 if enemy["is_boss"] else enemy["y"] - 14
                canvas.create_rectangle(enemy["x"]-bar_width//2, bar_y, 
                                        enemy["x"]+bar_width//2, bar_y+4,
                                        outline='#FF0000', fill='#330000')
                canvas.create_rectangle(enemy["x"]-bar_width//2, bar_y, 
                                        enemy["x"]-bar_width//2 + bar_width*hp_pct, bar_y+4,
                                        outline='', fill='#FF0000')
                if enemy["is_boss"]:
                    canvas.create_text(enemy["x"], enemy["y"]-25, text="👑 BOSS", 
                                      fill='#FFD700', font=('Arial', 7, 'bold'))
        
        # Update and draw hunters
        for name, hunter in self.arena_hunters.items():
            tab = self.hunter_tabs[name]
            
            # Sync hunter stats from build when running - use REALISTIC stats
            if tab.is_running:
                try:
                    # Get build config for actual stats
                    config = tab._get_config()
                    stats = config.get("stats", {})
                    
                    # Attack speed from speed stat (lower = faster attacks)
                    speed_stat = stats.get("speed", 0)
                    base_speed = 5.0 - speed_stat * 0.03
                    hunter["attack_speed"] = max(0.3, base_speed / 2)  # Scale for arena
                    
                    # Power from power stat
                    power_stat = stats.get("power", 0)
                    base_power = 3.0 + power_stat * 0.5
                    hunter["damage"] = max(1, base_power / 3)  # Scale for arena
                    
                    # HP from hp stat
                    hp_stat = stats.get("hp", 0)
                    base_hp = 43.0 + hp_stat * 2.5
                    if hunter["max_hp"] < base_hp:
                        hunter["max_hp"] = int(base_hp)
                        hunter["hp"] = min(hunter["hp"], hunter["max_hp"])
                    
                    # Regen from regen stat
                    regen_stat = stats.get("regen", 0)
                    hunter["regen"] = 0.02 + regen_stat * 0.03
                    
                    # DR from damage_reduction stat
                    dr_stat = stats.get("damage_reduction", 0)
                    hunter["dr"] = min(0.90, dr_stat * 0.0144)
                    
                    # Crit from special_chance stat
                    crit_stat = stats.get("special_chance", 0)
                    hunter["crit_chance"] = 0.05 + crit_stat * 0.0018
                    
                    # Crit damage from special_damage stat
                    crit_dmg_stat = stats.get("special_damage", 0)
                    hunter["crit_damage"] = 1.30 + crit_dmg_stat * 0.01
                    
                    # Lifesteal for Borge from attributes
                    if name == "Borge":
                        attrs = config.get("attributes", {})
                        baal = attrs.get("book_of_baal", 0)
                        hunter["lifesteal"] = baal * 0.0111
                    
                    # Poison for Ozzy from effect_chance
                    if name == "Ozzy":
                        effect_stat = stats.get("effect_chance", 0)
                        hunter["poison_chance"] = 0.04 + effect_stat * 0.005
                    
                    # Track optimization progress
                    hunter["opt_progress"] = getattr(tab, 'progress_var', tk.DoubleVar()).get() / 100.0
                except Exception:
                    pass
            
            # Get bench position for this hunter
            bench_pos = self.bench_positions[name]
            field_pos = hunter["field_position"]
            
            # Scale field position based on progress (hunters move deeper into field as progress increases)
            if hunter.get("on_field", False) and not hunter.get("returning_to_bench", False):
                progress = hunter.get("opt_progress", 0)
                # As progress increases, hunters fight closer to the top (enemy territory)
                base_y = field_pos["y"]
                min_y = self.field_top + 25
                # Interpolate: at 0% progress, stay at field_pos; at 100%, push toward top
                field_pos["y"] = int(base_y - (base_y - min_y) * progress * 0.5)
            
            # Handle hunter state: on bench, walking to field, fighting, or returning to bench
            if hunter.get("returning_to_bench", False):
                # Walk back to bench
                dx = bench_pos["x"] - hunter["x"]
                dy = bench_pos["y"] - hunter["y"]
                dist = math.sqrt(dx*dx + dy*dy)
                if dist > 5:
                    move_speed = 4.0  # Walk speed
                    hunter["x"] += (dx / dist) * move_speed
                    hunter["y"] += (dy / dist) * move_speed
                else:
                    # Arrived at bench
                    hunter["x"] = bench_pos["x"]
                    hunter["y"] = bench_pos["y"]
                    hunter["returning_to_bench"] = False
                    hunter["on_field"] = False
                    
            elif tab.is_running and hunter.get("on_field", False):
                # Hunter is on field and fighting
                # Calculate speed with boost
                speed_mult = 1.5 if hunter["speed_boost"] > 0 else 1.0
                hunter["speed_boost"] = max(0, hunter["speed_boost"] - 1)
                
                # DECISION: Find target enemy
                target_x, target_y = None, None
                
                # Find nearest enemy - but prefer enemies that OTHER hunters aren't targeting
                nearest_enemy = None
                nearest_enemy_dist = float('inf')
                
                # Get list of enemies other hunters are targeting
                other_targets = set()
                for other_name, other_hunter in self.arena_hunters.items():
                    if other_name != name and self.hunter_tabs[other_name].is_running:
                        if "target_id" in other_hunter and other_hunter["target_id"] is not None:
                            other_targets.add(other_hunter["target_id"])
                
                for enemy in self.arena_enemies:
                    dist = math.sqrt((hunter["x"] - enemy["x"])**2 + (hunter["y"] - enemy["y"])**2)
                    
                    # Prefer enemies not targeted by others (add penalty)
                    if enemy["id"] in other_targets:
                        dist += 100  # Penalty for contested targets
                    
                    # Prefer enemies in hunter's vertical zone
                    zone_diff = abs(enemy["y"] - hunter["y"])
                    dist += zone_diff * 0.3  # Small preference for same vertical area
                    
                    if dist < nearest_enemy_dist:
                        nearest_enemy_dist = dist
                        nearest_enemy = enemy
                
                # Remember our target
                hunter["target_id"] = nearest_enemy["id"] if nearest_enemy else None
                
                # Go for nearest enemy
                if nearest_enemy:
                    target_x, target_y = nearest_enemy["x"], nearest_enemy["y"]
                
                # Move toward target smoothly
                if target_x is not None:
                    dx = target_x - hunter["x"]
                    dy = target_y - hunter["y"]
                    dist = math.sqrt(dx*dx + dy*dy)
                    if dist > 5:  # Don't jitter when very close
                        # Smooth speed based on hunter type
                        base_speed = 3.0 if name == "Knox" else (2.5 if name == "Borge" else 2.2)
                        move_speed = min(base_speed * speed_mult, dist * 0.15)  # Smooth approach
                        hunter["x"] += (dx / dist) * move_speed
                        hunter["y"] += (dy / dist) * move_speed
                else:
                    # No target - idle at field position
                    home_x = field_pos["x"]
                    home_y = field_pos["y"]
                    dx = home_x - hunter["x"]
                    dy = home_y - hunter["y"]
                    dist = math.sqrt(dx*dx + dy*dy)
                    if dist > 10:
                        hunter["x"] += dx * 0.05
                        hunter["y"] += dy * 0.05
                
                # Keep hunters in field bounds
                hunter["x"] = max(30, min(self.arena_width - 30, hunter["x"]))
                hunter["y"] = max(self.field_top + 20, min(self.field_bottom - 20, hunter["y"]))
                
                # Update attack cooldown (based on attack speed from stats)
                if hunter["attack_cooldown"] > 0:
                    hunter["attack_cooldown"] -= 1
                
                # Ozzy regeneration (every 10 ticks = 0.5 sec)
                if name == "Ozzy" and self.arena_tick % 10 == 0 and hunter.get("regen", 0) > 0:
                    heal_amount = hunter["regen"] * hunter["level"]
                    hunter["hp"] = min(hunter["max_hp"], hunter["hp"] + heal_amount)
                    if heal_amount > 0.5:
                        self.arena_effects.append({
                            "x": hunter["x"] + 15, "y": hunter["y"] - 10,
                            "icon": "💚", "ttl": 8, "is_text": False
                        })
                
                # Check for enemy attacks - determine attack range
                attack_range = hunter.get("attack_range", 45)  # Knox has 120 range
                
                # Check for enemy collisions (attacks) - only attack when cooldown is 0
                for enemy in self.arena_enemies[:]:
                    dist = math.sqrt((hunter["x"] - enemy["x"])**2 + (hunter["y"] - enemy["y"])**2)
                    if dist < attack_range and hunter["attack_cooldown"] <= 0:
                        # Calculate damage with crits
                        is_crit = random.random() < hunter.get("crit_chance", 0.15)
                        damage = hunter["damage"]
                        if is_crit:
                            damage *= hunter.get("crit_damage", 1.5)
                        
                        # Attack icons based on hunter type and crit
                        attack_icons = {
                            "Borge": ['⚔️', '🗡️', '💪'] if not is_crit else ['🔥', '💥', '⚔️'],
                            "Knox": ['�', '⚡', '🎯'] if not is_crit else ['💥', '🎯', '⚡'],
                            "Ozzy": ['🌀', '✨', '🐙'] if not is_crit else ['💫', '🎆', '✨']
                        }
                        attack_icon = random.choice(attack_icons.get(name, ['💥']))
                        
                        # Knox fires projectiles instead of instant hit
                        if name == "Knox":
                            self.arena_projectiles.append({
                                "x": hunter["x"], "y": hunter["y"],
                                "target_enemy_id": enemy["id"],
                                "icon": "💥",
                                "speed": 8,
                                "damage": damage,
                                "is_crit": is_crit,
                                "hunter": name,
                                "color": hunter.get("color", "#0D6EFD")
                            })
                            # Set attack cooldown
                            hunter["attack_cooldown"] = int(hunter["attack_speed"] * 10)
                            continue  # Don't do instant damage, projectile will handle it
                        
                        # Show attack effect - melee for Borge/Ozzy
                        effect_x = (hunter["x"] + enemy["x"]) / 2
                        effect_y = (hunter["y"] + enemy["y"]) / 2
                        attack_color = hunter.get("color", "#FFFFFF")
                        self.arena_effects.append({
                            "x": effect_x, "y": effect_y,
                            "icon": attack_icon, "ttl": 5, "is_text": False,
                            "color": attack_color
                        })
                        
                        # Crit text with color
                        if is_crit:
                            self.arena_effects.append({
                                "x": enemy["x"], "y": enemy["y"] - 20,
                                "icon": "💥CRIT!", "ttl": 12, "is_text": True,
                                "color": "#FFD700"  # Gold
                            })
                        
                        enemy["hp"] -= damage
                        enemy["last_attacker"] = name
                        
                        # Borge lifesteal
                        if name == "Borge" and hunter.get("lifesteal", 0) > 0:
                            heal = damage * hunter["lifesteal"]
                            hunter["hp"] = min(hunter["max_hp"], hunter["hp"] + heal)
                        
                        # Ozzy poison application with visual cloud
                        if name == "Ozzy" and random.random() < hunter.get("poison_chance", 0.3):
                            enemy["poison_stacks"] = enemy.get("poison_stacks", 0) + 1
                            # Green poison cloud effect
                            self.arena_effects.append({
                                "x": enemy["x"], "y": enemy["y"],
                                "icon": "☠️", "ttl": 15, "is_text": False,
                                "color": "#00FF00"
                            })
                            # Text showing poison applied
                            self.arena_effects.append({
                                "x": enemy["x"], "y": enemy["y"] - 25,
                                "icon": "🧪 POISON!", "ttl": 12, "is_text": True,
                                "color": "#00FF00"
                            })
                        
                        # Set attack cooldown based on attack speed
                        hunter["attack_cooldown"] = int(hunter["attack_speed"] * 10)
                        
                        if enemy["hp"] <= 0:
                            # Enemy killed!
                            kill_icons = {
                                "Borge": ['💀', '☠️', '🔥', '⚔️'],
                                "Knox": ['💥', '🎯', '💨', '🔫'],
                                "Ozzy": ['🌀', '✨', '💫', '🎆']
                            }
                            self.arena_effects.append({
                                "x": enemy["x"], "y": enemy["y"],
                                "icon": random.choice(kill_icons.get(name, ['💥'])) if not enemy["is_boss"] else '🎆',
                                "ttl": 8, "is_text": False
                            })
                            
                            # XP and kills - bosses give more XP
                            xp_gain = 20 if enemy["is_boss"] else 1
                            hunter["kills"] += 1
                            hunter["xp"] += xp_gain
                            self.arena_total_kills += 1
                            
                            if enemy["is_boss"]:
                                self.boss_active = False
                                # Victory bonus heal
                                hunter["hp"] = hunter["max_hp"]
                            
                            self.arena_enemies.remove(enemy)
                            
                            # Level up check with CIFI-like stat scaling
                            xp_needed = hunter["level"] * 10
                            if hunter["xp"] >= xp_needed:
                                hunter["xp"] -= xp_needed
                                hunter["level"] += 1
                                # Scale stats on level up
                                hunter["max_hp"] += 10 if name == "Borge" else (5 if name == "Ozzy" else 3)
                                hunter["hp"] = hunter["max_hp"]
                                hunter["damage"] = hunter["base_damage"] * (1 + hunter["level"] * 0.15)
                                if name == "Ozzy":
                                    hunter["regen"] = 0.5 + hunter["level"] * 0.2
                                self.arena_effects.append({
                                    "x": hunter["x"], "y": hunter["y"] - 25,
                                    "icon": f"⬆️ LVL {hunter['level']}!", "ttl": 30, "is_text": True
                                })
                            
                            # Stage progression
                            if self.arena_total_kills >= self.kills_for_next_stage:
                                self.arena_stage += 1
                                self.kills_for_next_stage = self.arena_stage * 15
                                self.arena_effects.append({
                                    "x": self.arena_width // 2, "y": self.arena_height // 2,
                                    "icon": f"🏆 STAGE {self.arena_stage}!", "ttl": 40, "is_text": True
                                })
                                # Spawn boss every 3 stages
                                if self.arena_stage % 3 == 0 and not self.boss_active:
                                    self._spawn_enemy(is_boss=True)
                            
                            # Spawn replacement enemy
                            if len(self.arena_enemies) < 10 + self.arena_stage:
                                self._spawn_enemy()
                        else:
                            # Hit but not killed (boss)
                            self.arena_effects.append({
                                "x": enemy["x"], "y": enemy["y"],
                                "icon": '💢', "ttl": 5, "is_text": False
                            })
            
            # Draw hunter with effects
            is_active = tab.is_running or self.victory_mode or hunter.get("returning_to_bench", False)
            is_on_bench = not hunter.get("on_field", False) and not hunter.get("returning_to_bench", False)
            
            if is_active and not is_on_bench:
                # Active hunter - glow effect
                glow_size = 22 if hunter["speed_boost"] > 0 else 18
                canvas.create_oval(hunter["x"]-glow_size, hunter["y"]-glow_size, 
                                  hunter["x"]+glow_size, hunter["y"]+glow_size,
                                  fill=hunter["color"], outline='', stipple='gray50')
            
            # Draw hunter icon - use text icons to ensure proper centering
            icon_map = {"Borge": "B", "Knox": "K", "Ozzy": "O"}
            icon_char = icon_map.get(name, "?")
            
            # Smaller icon if on bench, larger if active
            icon_size = 10 if is_on_bench else 14
            circle_size = 10 if is_on_bench else 14
            
            # Draw circle background for icon
            canvas.create_oval(hunter["x"]-circle_size, hunter["y"]-circle_size, 
                              hunter["x"]+circle_size, hunter["y"]+circle_size,
                              fill=hunter["color"] if not is_on_bench else '#444444', 
                              outline='#FFFFFF' if not is_on_bench else '#888888', width=2)
            # Draw letter
            canvas.create_text(hunter["x"], hunter["y"], text=icon_char, 
                              font=('Arial', icon_size, 'bold'), 
                              fill='#FFFFFF' if not is_on_bench else '#CCCCCC', anchor='center')
            
            # Draw emoji next to the circle (only if not on bench)
            if not is_on_bench:
                emoji_x = hunter["x"] + 20
                canvas.create_text(emoji_x, hunter["y"] - 10, text=hunter["icon"], 
                                  font=('Segoe UI Emoji', 12), anchor='w')
            
                # Draw level badge
                if hunter["level"] > 1:
                    canvas.create_oval(hunter["x"]+12, hunter["y"]-18, hunter["x"]+24, hunter["y"]-6,
                                      fill='#FFD700', outline='#000000')
                    canvas.create_text(hunter["x"]+18, hunter["y"]-12, text=str(hunter["level"]),
                                      fill='#000000', font=('Arial', 7, 'bold'))
                
                # Draw HP bar
                bar_width = 30
                hp_pct = hunter["hp"] / hunter["max_hp"]
                bar_y = hunter["y"] + 18
                canvas.create_rectangle(hunter["x"]-bar_width//2, bar_y, 
                                        hunter["x"]+bar_width//2, bar_y+4,
                                        outline='#333333', fill='#1a1a1a')
                hp_color = '#00FF00' if hp_pct > 0.5 else '#FFFF00' if hp_pct > 0.25 else '#FF0000'
                canvas.create_rectangle(hunter["x"]-bar_width//2, bar_y, 
                                        hunter["x"]-bar_width//2 + bar_width*hp_pct, bar_y+4,
                                        outline='', fill=hp_color)
                
                # Draw kill count
                canvas.create_text(hunter["x"], hunter["y"] + 30, 
                                  text=f"💀{hunter['kills']}", 
                                  fill=hunter["color"], font=('Arial', 8, 'bold'))
                
                # Draw optimization progress bar if running
                if tab.is_running:
                    opt_progress = hunter.get("opt_progress", 0)
                    prog_bar_width = 40
                    prog_bar_y = hunter["y"] + 42
                    canvas.create_rectangle(hunter["x"]-prog_bar_width//2, prog_bar_y, 
                                            hunter["x"]+prog_bar_width//2, prog_bar_y+3,
                                            outline='#333333', fill='#1a1a1a')
                    canvas.create_rectangle(hunter["x"]-prog_bar_width//2, prog_bar_y, 
                                            hunter["x"]-prog_bar_width//2 + prog_bar_width*opt_progress, prog_bar_y+3,
                                            outline='', fill='#00BFFF')
                    canvas.create_text(hunter["x"], prog_bar_y + 10, 
                                      text=f"{int(opt_progress*100)}%",
                                      fill='#00BFFF', font=('Arial', 7, 'bold'))
            else:
                # On bench - show "💤" sleeping indicator
                canvas.create_text(hunter["x"], hunter["y"] - 18, text="💤",
                                  font=('Segoe UI Emoji', 10))
                # Show name label below
                canvas.create_text(hunter["x"], hunter["y"] + 18, text=name,
                                  fill='#888888', font=('Arial', 7))
        
        # Draw and update effects
        for effect in self.arena_effects[:]:
            effect_color = effect.get("color", "#FFFFFF")
            if effect.get("is_text"):
                canvas.create_text(effect["x"], effect["y"], text=effect["icon"],
                                  fill=effect_color, font=('Arial', 10, 'bold'))
                effect["y"] -= 1  # Float upward
            else:
                canvas.create_text(effect["x"], effect["y"], text=effect["icon"],
                                  fill=effect_color, font=('Segoe UI Emoji', 14))
            effect["ttl"] -= 1
            if effect["ttl"] <= 0:
                self.arena_effects.remove(effect)
        
        # Spawn new enemies LESS frequently when running (stationary enemies)
        if any_running and not self.victory_mode:
            # Very low spawn rate: 0.015 = ~once every 67 frames = ~3.3 seconds
            max_enemies = 3 + (self.arena_stage // 2)  # Fewer enemies on screen
            if random.random() < 0.015 and len(self.arena_enemies) < max_enemies:
                self._spawn_enemy()
        
        # Draw stats at bottom
        y = self.arena_height - 12
        stats_text = f"🛡️Lv{self.arena_hunters['Borge']['level']}:{self.arena_hunters['Borge']['kills']}  " \
                     f"🔫Lv{self.arena_hunters['Knox']['level']}:{self.arena_hunters['Knox']['kills']}  " \
                     f"🐙Lv{self.arena_hunters['Ozzy']['level']}:{self.arena_hunters['Ozzy']['kills']}"
        canvas.create_text(self.arena_width // 2, y, text=stats_text,
                          fill='#FFFFFF', font=('Arial', 9, 'bold'))
        
        # Increment tick counter for timing (regen, poison, etc.)
        self.arena_tick += 1
        
        # Schedule next frame (50ms = 20fps)
        self.root.after(50, self._animate_arena)
    
    def _update_leaderboard(self, hunter_name: str, hunter: dict):
        """Update the leaderboard with completed run results."""
        if hunter_name not in self.leaderboard_labels:
            return
            
        label = self.leaderboard_labels[hunter_name]
        
        avg_stage = hunter.get("last_avg_stage", 0)
        max_stage = hunter.get("last_max_stage", 0)
        gen = hunter.get("last_gen", 0)
        kills = hunter.get("kills", 0)
        
        if avg_stage > 0:
            text = f"Avg: {avg_stage:.1f} | Max: {max_stage} | Gen: {gen} | Arena Kills: {kills}"
            label.configure(text=text, fg='#FFFFFF')
        else:
            label.configure(text="Waiting...", fg='#888888')
    
    def _apply_global_settings(self):
        """Apply global settings to all hunter tabs."""
        for name, tab in self.hunter_tabs.items():
            tab.num_sims.set(self.global_num_sims.get())
            tab.builds_per_tier.set(self.global_builds_per_tier.get())
            tab.use_rust.set(self.global_use_rust.get())
            tab.use_progressive.set(self.global_use_progressive.get())
        self._log("📋 Applied global settings to all hunters")
        self.all_status.configure(text="Settings applied to all hunters!")
    
    def _run_single_hunter(self, hunter_name: str):
        """Run optimization for a single hunter."""
        # Apply global settings first
        tab = self.hunter_tabs[hunter_name]
        tab.num_sims.set(self.global_num_sims.get())
        tab.builds_per_tier.set(self.global_builds_per_tier.get())
        tab.use_rust.set(self.global_use_rust.get())
        tab.use_progressive.set(self.global_use_progressive.get())
        
        if not tab.is_running:
            self._log(f"🚀 Starting {hunter_name} optimization...")
            tab._start_optimization()
            self._update_hunter_status()
            self.root.after(1000, self._check_all_complete)
    
    def _update_hunter_status(self):
        """Update individual hunter status labels and progress bars."""
        statuses = {
            "Borge": (self.borge_status, self.borge_progress, self.borge_eta, self.hunter_tabs["Borge"]),
            "Knox": (self.knox_status, self.knox_progress, self.knox_eta, self.hunter_tabs["Knox"]),
            "Ozzy": (self.ozzy_status, self.ozzy_progress, self.ozzy_eta, self.hunter_tabs["Ozzy"]),
        }
        
        total_progress = 0
        running_count = 0
        
        for name, (label, progress_var, eta_label, tab) in statuses.items():
            icon = '🛡️' if name=='Borge' else '🔫' if name=='Knox' else '🐙'
            
            if tab.is_running:
                # Get progress from the tab's progress var
                pct = tab.progress_var.get()
                progress_var.set(pct)
                total_progress += pct
                running_count += 1
                
                # Calculate ETA
                if tab.optimization_start_time > 0:
                    elapsed = time.time() - tab.optimization_start_time
                    if pct > 0:
                        total_time = elapsed / (pct / 100)
                        remaining = total_time - elapsed
                        if remaining < 60:
                            eta_str = f"{remaining:.0f}s"
                        elif remaining < 3600:
                            eta_str = f"{remaining/60:.1f}m"
                        else:
                            eta_str = f"{remaining/3600:.1f}h"
                        eta_label.configure(text=f"ETA: {eta_str}")
                    else:
                        eta_label.configure(text="Starting...")
                
                label.configure(text=f"{icon} {name}: ⏳ {pct:.0f}%")
            elif tab.results:
                best = max(tab.results, key=lambda r: r.avg_final_stage).avg_final_stage
                progress_var.set(100)
                eta_label.configure(text="✅ Complete")
                label.configure(text=f"{icon} {name}: Best {best:.1f}")
            else:
                progress_var.set(0)
                eta_label.configure(text="")
                label.configure(text=f"{icon} {name}: Idle")
        
        # Update global progress
        if running_count > 0:
            self.global_progress.set(total_progress / 3)  # Average of all 3
            self.global_eta.configure(text=f"Running {running_count}/3 hunters...")
        elif any(tab.results for tab in self.hunter_tabs.values()):
            self.global_progress.set(100)
            self.global_eta.configure(text="✅ All complete!")
        else:
            self.global_progress.set(0)
            self.global_eta.configure(text="Ready")
    
    def _create_log_frame(self):
        """Create a global log and progress bar at the bottom."""
        # Container for log + progress
        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill=tk.X, padx=10, pady=5, side=tk.BOTTOM)
        
        # Global progress bar
        progress_frame = ttk.LabelFrame(bottom_frame, text="📊 Global Progress")
        progress_frame.pack(fill=tk.X, pady=2)
        
        progress_inner = ttk.Frame(progress_frame)
        progress_inner.pack(fill=tk.X, padx=10, pady=5)
        
        self.global_progress = tk.DoubleVar(value=0)
        self.global_progress_bar = ttk.Progressbar(progress_inner, variable=self.global_progress, maximum=100)
        self.global_progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.global_eta = ttk.Label(progress_inner, text="Ready", width=30)
        self.global_eta.pack(side=tk.LEFT, padx=5)
        
        # Log
        log_frame = ttk.LabelFrame(bottom_frame, text="📋 Global Log")
        log_frame.pack(fill=tk.X, pady=2)
        
        self.global_log = scrolledtext.ScrolledText(log_frame, height=4, state=tk.DISABLED, font=('Consolas', 9))
        self.global_log.pack(fill=tk.X, padx=5, pady=5)
    
    def _log(self, message: str):
        """Add message to global log."""
        self.global_log.configure(state=tk.NORMAL)
        timestamp = time.strftime("%H:%M:%S")
        self.global_log.insert(tk.END, f"[{timestamp}] {message}\n")
        self.global_log.see(tk.END)
        self.global_log.configure(state=tk.DISABLED)
    
    def _run_all_hunters(self):
        """Start optimization for selected hunters sequentially."""
        # Apply global settings to all hunters first
        self._apply_global_settings()
        
        # Get list of selected hunters
        selected = []
        if self.run_borge_var.get():
            selected.append("Borge")
        if self.run_knox_var.get():
            selected.append("Knox")
        if self.run_ozzy_var.get():
            selected.append("Ozzy")
        
        if not selected:
            messagebox.showwarning("No Selection", "Please select at least one hunter to optimize!")
            return
        
        self.run_all_btn.configure(state=tk.DISABLED)
        self.stop_all_btn.configure(state=tk.NORMAL)
        self.all_status.configure(text=f"Running {len(selected)} hunter(s) sequentially...")
        self._log(f"🚀 Starting optimization for: {', '.join(selected)}")
        self._log(f"   Running SEQUENTIALLY (one at a time for better responsiveness)")
        
        # Start first hunter
        self.sequential_queue = selected.copy()
        self._run_next_sequential()
        
        self._update_hunter_status()
    
    def _run_next_sequential(self):
        """Run the next hunter in the sequential queue."""
        if not hasattr(self, 'sequential_queue') or not self.sequential_queue:
            # All done
            self.run_all_btn.configure(state=tk.NORMAL)
            self.stop_all_btn.configure(state=tk.DISABLED)
            self.all_status.configure(text="✅ All selected hunters completed!")
            self._log("✅ All optimizations complete!")
            return
        
        # Get next hunter
        next_hunter = self.sequential_queue.pop(0)
        tab = self.hunter_tabs[next_hunter]
        
        if not tab.is_running:
            self._log(f"   ▶️ Starting {next_hunter}...")
            self.all_status.configure(text=f"Running: {next_hunter} ({len(self.sequential_queue)} remaining)")
            tab._start_optimization()
        
        # Check for completion
        self.root.after(1000, self._check_sequential_complete)
    
    def _check_sequential_complete(self):
        """Check if current sequential hunter is complete."""
        self._update_hunter_status()
        
        # Check if any hunter is still running
        running = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        # Also check if any hunter is still returning to bench
        returning = [name for name, hunter in self.arena_hunters.items() 
                     if hunter.get("returning_to_bench", False)]
        
        if running:
            # Still running, check again
            self.root.after(1000, self._check_sequential_complete)
        elif returning:
            # Hunter finished but still walking back to bench - wait
            self.all_status.configure(text="⏳ Hunter returning to bench...")
            self.root.after(200, self._check_sequential_complete)
        elif self.victory_mode and self.victory_timer > 0:
            # Victory celebration still playing - wait
            self.all_status.configure(text="🎉 Celebrating victory...")
            self.root.after(200, self._check_sequential_complete)
        else:
            # Current hunter finished AND back on bench, add 3 second cooldown before next
            if hasattr(self, 'sequential_queue') and self.sequential_queue:
                self._log("   ⏳ 3 second cooldown before next hunter...")
                self.all_status.configure(text="⏳ Cooldown before next hunter...")
                self.root.after(3000, self._run_next_sequential)
            else:
                # All done
                self._run_next_sequential()
    
    def _stop_all_hunters(self):
        """Stop all running optimizations."""
        self._log("⏹️ Stopping all hunters...")
        
        # Clear the sequential queue
        if hasattr(self, 'sequential_queue'):
            self.sequential_queue.clear()
        
        for name, tab in self.hunter_tabs.items():
            if tab.is_running:
                tab._stop_optimization()
        
        self.run_all_btn.configure(state=tk.NORMAL)
        self.stop_all_btn.configure(state=tk.DISABLED)
        self.all_status.configure(text="Stopped")
    
    def _check_all_complete(self):
        """Check if all hunters have completed."""
        self._update_hunter_status()
        running = [name for name, tab in self.hunter_tabs.items() if tab.is_running]
        
        if running:
            self.all_status.configure(text=f"Running: {', '.join(running)}")
            self.root.after(1000, self._check_all_complete)
        else:
            self.run_all_btn.configure(state=tk.NORMAL)
            self.stop_all_btn.configure(state=tk.DISABLED)
            self.all_status.configure(text="All hunters complete!")
            self._log("✅ All hunter optimizations complete!")
    
    def _save_all_builds(self):
        """Save all hunter builds."""
        for name, tab in self.hunter_tabs.items():
            tab._auto_save_build()
        self._log("💾 All builds saved to IRL Builds folder")
        messagebox.showinfo("Saved", "All builds saved to IRL Builds folder!")


def main():
    """Main entry point."""
    root = tk.Tk()
    app = MultiHunterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
