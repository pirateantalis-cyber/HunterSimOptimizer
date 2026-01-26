//! Debug the stage 200 boss fight specifically

use rust_sim::config::BuildConfig;
use rust_sim::enemy::Enemy;
use rust_sim::hunter::Hunter;
use std::env;

fn main() {
    let config_path = env::args().nth(1).unwrap_or("../hunter-sim/IRL Builds/my_borge_build.json".to_string());
    let config = BuildConfig::from_file(&config_path).expect("Failed to load config");
    
    let mut hunter = Hunter::from_config(&config);
    hunter.current_stage = 200;
    
    // Create stage 200 boss
    let mut boss = Enemy::new_boss(200, hunter.hunter_type);
    
    println!("=== STAGE 200 BOSS DEBUG ===");
    println!("\nHUNTER:");
    println!("  HP: {:.2} / {:.2}", hunter.hp, hunter.max_hp);
    println!("  Power: {:.2}", hunter.power);
    println!("  DR: {:.2}%", hunter.damage_reduction * 100.0);
    println!("  Speed: {:.2}", hunter.speed);
    println!("  Max Revives: {}", hunter.max_revives);
    println!("  POG level: {}", hunter.presence_of_god);
    
    println!("\nBOSS (before POG):");
    println!("  HP: {:.2} / {:.2}", boss.hp, boss.max_hp);
    println!("  Power: {:.2}", boss.power);
    
    // Apply POG manually like simulation does
    if hunter.presence_of_god > 0 {
        let stage_effect = 0.5; // Boss
        let pog_reduction = (hunter.presence_of_god as f64 * 0.04 * stage_effect).min(0.99);
        let new_hp = boss.max_hp * (1.0 - pog_reduction);
        println!("\nPOG Applied:");
        println!("  POG reduction: {:.2}%", pog_reduction * 100.0);
        println!("  Boss HP after POG: {:.2}", new_hp);
        boss.hp = new_hp;
    }
    
    println!("\nBOSS (after POG):");
    println!("  HP: {:.2} / {:.2}", boss.hp, boss.max_hp);
    
    // Calculate combat math
    let hunter_dmg = hunter.power * (1.0 - boss.damage_reduction);
    let boss_dmg = boss.power * (1.0 - hunter.damage_reduction);
    
    println!("\nCOMBAT MATH:");
    println!("  Hunter dmg/hit: {:.2}", hunter_dmg);
    println!("  Hits to kill boss: {:.1}", boss.hp / hunter_dmg);
    println!("  Boss dmg/hit: {:.2}", boss_dmg);
    println!("  Hits to kill hunter: {:.1}", hunter.hp / boss_dmg);
    
    // Simulate a simplified fight
    println!("\n=== SIMULATING FIGHT ===");
    let mut hunter_time = hunter.speed;
    let mut boss_time = boss.speed;
    let mut tick = 0;
    let mut revives_used = 0;
    
    while !boss.is_dead() && tick < 10000 {
        tick += 1;
        
        if hunter_time <= boss_time {
            // Hunter attacks
            let damage = hunter.power * (1.0 - boss.damage_reduction);
            boss.hp -= damage;
            if tick <= 20 || tick % 100 == 0 {
                println!("[{:>4}] Hunter attacks for {:.0}, boss HP: {:.0}", tick, damage, boss.hp);
            }
            hunter_time += hunter.speed;
        } else {
            // Boss attacks
            let damage = boss.power * (1.0 - hunter.damage_reduction);
            hunter.hp -= damage;
            if tick <= 20 || tick % 100 == 0 {
                println!("[{:>4}] Boss attacks for {:.0}, hunter HP: {:.0}", tick, damage, hunter.hp);
            }
            boss_time += boss.speed;
            
            if hunter.hp <= 0.0 {
                if revives_used < hunter.max_revives {
                    revives_used += 1;
                    hunter.hp = hunter.max_hp * 0.8;
                    println!("[{:>4}] REVIVE #{} - Hunter HP restored to {:.0}", tick, revives_used, hunter.hp);
                } else {
                    println!("[{:>4}] HUNTER DIED (no revives left)", tick);
                    break;
                }
            }
        }
    }
    
    if boss.is_dead() {
        println!("\n=== BOSS KILLED after {} ticks ===", tick);
    } else {
        println!("\n=== HUNTER DIED after {} ticks (used {} revives) ===", tick, revives_used);
    }
}
