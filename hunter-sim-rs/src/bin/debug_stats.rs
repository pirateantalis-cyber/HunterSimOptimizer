//! Debug script to print hunter and enemy stats for comparison with Python

use rust_sim::config::{BuildConfig, HunterType};
use rust_sim::enemy::Enemy;
use rust_sim::hunter::Hunter;
use std::env;

fn main() {
    let args: Vec<String> = env::args().collect();
    
    // If config path provided, print hunter stats
    if args.len() > 1 {
        let config_path = &args[1];
        match BuildConfig::from_file(config_path) {
            Ok(config) => {
                let hunter = Hunter::from_config(&config);
                println!("\n=== RUST {:?} HUNTER STATS ===", hunter.hunter_type);
                println!("  max_hp:      {:.2}", hunter.max_hp);
                println!("  power:       {:.2}", hunter.power);
                println!("  regen:       {:.4}", hunter.regen);
                println!("  DR:          {:.4}", hunter.damage_reduction);
                println!("  minotaur_dr: {:.4}", hunter.minotaur_dr);
                println!("  evade:       {:.4}", hunter.evade_chance);
                println!("  effect:      {:.4}", hunter.effect_chance);
                println!("  crit:        {:.4}", hunter.special_chance);
                println!("  crit_dmg:    {:.4}", hunter.special_damage);
                println!("  speed:       {:.4}", hunter.speed);
                println!("  lifesteal:   {:.4}", hunter.lifesteal);
                return;
            }
            Err(e) => {
                eprintln!("Error loading config: {}", e);
            }
        }
    }
    
    let stage = 200;
    
    for (name, hunter_type) in [("BORGE", HunterType::Borge), ("OZZY", HunterType::Ozzy)] {
        println!("\n=== RUST {} @ STAGE {} ===", name, stage);
        
        // Regular enemy
        let enemy = Enemy::new(0, stage, hunter_type);
        println!("Enemy:");
        println!("  HP: {:.2}", enemy.max_hp);
        println!("  Power: {:.2}", enemy.power);
        println!("  Regen: {:.4}", enemy.regen);
        
        // Boss
        let boss = Enemy::new_boss(stage, hunter_type);
        println!("Boss:");
        println!("  HP: {:.2}", boss.max_hp);
        println!("  Power: {:.2}", boss.power);
        println!("  Regen: {:.4}", boss.regen);
        println!("  Speed: {:.4}", boss.speed);
        println!("  DR: {:.4}", boss.damage_reduction);
        println!("  Special chance: {:.4}", boss.special_chance);
        println!("  Special damage: {:.4}", boss.special_damage);
    }
}
