//! CLI entry point for Hunter Simulator

use clap::{Parser, ValueEnum};
use rust_sim::{
    config::BuildConfig,
    hunter::Hunter,
    enemy::Enemy,
    simulation::run_and_aggregate,
    stats::AggregatedStats,
};
use std::path::PathBuf;
use std::time::Instant;

#[derive(Debug, Clone, ValueEnum)]
enum OutputFormat {
    Text,
    Json,
}

#[derive(Parser, Debug)]
#[command(name = "hunter-sim")]
#[command(version = "1.0")]
#[command(about = "High-performance Hunter Simulator for CIFI idle game", long_about = None)]
struct Args {
    /// Path to the build configuration file (YAML or JSON)
    #[arg(short, long)]
    config: PathBuf,

    /// Number of simulations to run
    #[arg(short, long, default_value = "100")]
    num_sims: usize,

    /// Use parallel processing
    #[arg(short, long, default_value = "false")]
    parallel: bool,

    /// Output format
    #[arg(short, long, value_enum, default_value = "text")]
    output: OutputFormat,

    /// Show timing information
    #[arg(short, long, default_value = "false")]
    timing: bool,
    
    /// Debug: print computed hunter stats before simulation
    #[arg(long, default_value = "false")]
    debug_stats: bool,
    
    /// Debug: print enemy/boss stats for a specific stage
    #[arg(long)]
    debug_enemy_stage: Option<i32>,
    
    /// Debug: enable detailed combat trace
    #[arg(long, default_value = "false")]
    debug_trace: bool,
}

fn main() {
    let args = Args::parse();

    // Load config
    let config = match BuildConfig::from_file(&args.config) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error loading config: {}", e);
            std::process::exit(1);
        }
    };

    // Debug: print computed hunter stats
    if args.debug_stats {
        let hunter = Hunter::from_config(&config);
        println!("============================================================");
        println!("RUST {:?} STATS", hunter.hunter_type);
        println!("============================================================");
        println!("Max HP:        {:.2}", hunter.max_hp);
        println!("Power:         {:.4}", hunter.power);
        println!("Regen:         {:.4}", hunter.regen);
        println!("DR:            {:.4} ({:.2}%)", hunter.damage_reduction, hunter.damage_reduction * 100.0);
        println!("Evade:         {:.4} ({:.2}%)", hunter.evade_chance, hunter.evade_chance * 100.0);
        println!("Effect:        {:.4} ({:.2}%)", hunter.effect_chance, hunter.effect_chance * 100.0);
        println!("Special Chance:{:.4} ({:.2}%)", hunter.special_chance, hunter.special_chance * 100.0);
        println!("Special Damage:{:.4}", hunter.special_damage);
        println!("Speed:         {:.4}", hunter.speed);
        println!("Lifesteal:     {:.4} ({:.2}%)", hunter.lifesteal, hunter.lifesteal * 100.0);
        println!();
        println!("BORGE-SPECIFIC:");
        println!("Minotaur DR:   {:.4} ({:.2}%)", hunter.minotaur_dr, hunter.minotaur_dr * 100.0);
        println!("Soul of Hermes:{}", hunter.soul_of_hermes);
        println!("Atlas Protocol:{}", hunter.atlas_protocol);
        println!("Impeccable Impacts: {}", hunter.impeccable_impacts);
        println!();
        return;
    }

    // Debug: print enemy/boss stats for a stage
    if let Some(stage) = args.debug_enemy_stage {
        let hunter_type = config.get_hunter_type();
        
        println!("============================================================");
        println!("RUST STAGE {} {:?} ENEMY/BOSS STATS", stage, hunter_type);
        println!("============================================================");
        
        // Regular enemy
        let enemy = Enemy::new(0, stage, hunter_type);
        println!("\nREGULAR ENEMY:");
        println!("  HP:      {:.2}", enemy.max_hp);
        println!("  Power:   {:.4}", enemy.power);
        println!("  Regen:   {:.4}", enemy.regen);
        println!("  DR:      {:.4} ({:.2}%)", enemy.damage_reduction, enemy.damage_reduction * 100.0);
        println!("  SpecC:   {:.4} ({:.2}%)", enemy.special_chance, enemy.special_chance * 100.0);
        println!("  SpecD:   {:.4}", enemy.special_damage);
        println!("  Speed:   {:.4}", enemy.speed);
        
        // Boss
        let boss = Enemy::new_boss(stage, hunter_type);
        println!("\nBOSS (Stage {}):", stage);
        println!("  HP:      {:.2}", boss.max_hp);
        println!("  Power:   {:.4}", boss.power);
        println!("  Regen:   {:.4}", boss.regen);
        println!("  DR:      {:.4} ({:.2}%)", boss.damage_reduction, boss.damage_reduction * 100.0);
        println!("  SpecC:   {:.4} ({:.2}%)", boss.special_chance, boss.special_chance * 100.0);
        println!("  SpecD:   {:.4}", boss.special_damage);
        println!("  Speed:   {:.4}", boss.speed);
        println!("  Speed2:  {:.4}", boss.speed2);
        println!("  Secondary: {:?}", boss.secondary_type);
        println!();
        return;
    }

    // Run simulations
    let start = Instant::now();
    let stats = run_and_aggregate(&config, args.num_sims, args.parallel);
    let elapsed = start.elapsed();

    // Output results
    match args.output {
        OutputFormat::Text => {
            println!("=== Hunter Simulation Results ===");
            println!("Simulations: {}", args.num_sims);
            println!();
            println!("Average Final Stage: {:.2} ± {:.2}", stats.avg_stage, stats.std_stage);
            println!("Stage Range: {} - {}", stats.min_stage, stats.max_stage);
            println!();
            println!("Average Elapsed Time: {:.2}s", stats.avg_time);
            println!("Average Total Loot: {:.0}", stats.avg_loot);
            println!();
            println!("--- Combat Stats ---");
            println!("Avg Damage Dealt: {:.0}", stats.avg_damage);
            println!("Avg Damage Taken: {:.0}", stats.avg_damage_taken);
            println!("Avg Damage Mitigated: {:.0}", stats.avg_mitigated);
            println!("Avg Lifesteal: {:.0}", stats.avg_lifesteal);
            println!();
            println!("Avg Attacks: {:.0}", stats.avg_attacks);
            println!("Avg Crits: {:.0}", stats.avg_crits);
            println!("Avg Kills: {:.0}", stats.avg_kills);
            println!("Avg Evades: {:.0}", stats.avg_evades);
            println!("Avg Trickster Evades: {:.0}", stats.avg_trickster_evades);
            println!("Avg Enemy Attacks: {:.0}", stats.avg_enemy_attacks);
            println!("Avg Effect Procs: {:.0}", stats.avg_effect_procs);
            println!("Avg Stun Duration: {:.2}s", stats.avg_stun_duration);
            
            if args.timing {
                println!();
                println!("--- Performance ---");
                println!("Total time: {:.3}s", elapsed.as_secs_f64());
                println!("Per simulation: {:.3}ms", elapsed.as_secs_f64() * 1000.0 / args.num_sims as f64);
                println!("Simulations/sec: {:.0}", args.num_sims as f64 / elapsed.as_secs_f64());
            }
        }
        OutputFormat::Json => {
            let output = serde_json::json!({
                "simulations": args.num_sims,
                "parallel": args.parallel,
                "elapsed_seconds": elapsed.as_secs_f64(),
                "stats": {
                    "avg_stage": stats.avg_stage,
                    "std_stage": stats.std_stage,
                    "min_stage": stats.min_stage,
                    "max_stage": stats.max_stage,
                    "avg_time": stats.avg_time,
                    "avg_loot": stats.avg_loot,
                    "avg_loot_per_hour": stats.avg_loot_per_hour,
                    "avg_loot_common": stats.avg_loot_common,
                    "avg_loot_uncommon": stats.avg_loot_uncommon,
                    "avg_loot_rare": stats.avg_loot_rare,
                    "avg_xp": stats.avg_xp,
                    "avg_damage": stats.avg_damage,
                    "avg_damage_taken": stats.avg_damage_taken,
                    "avg_mitigated": stats.avg_mitigated,
                    "avg_lifesteal": stats.avg_lifesteal,
                    "avg_attacks": stats.avg_attacks,
                    "avg_crits": stats.avg_crits,
                    "avg_kills": stats.avg_kills,
                    "avg_evades": stats.avg_evades,
                    "avg_enemy_attacks": stats.avg_enemy_attacks,
                    "avg_effect_procs": stats.avg_effect_procs,
                    "avg_stun_duration": stats.avg_stun_duration,
                    "avg_regen": stats.avg_regen,
                    "avg_loth_healing": stats.avg_loth_healing,
                    "avg_ua_healing": stats.avg_ua_healing,
                    "avg_trample_kills": stats.avg_trample_kills,
                    "avg_on_kill_calls": stats.avg_on_kill_calls,
                    "survival_rate": stats.survival_rate,
                    "boss1_survival": stats.boss1_survival,
                    "boss2_survival": stats.boss2_survival,
                    "boss3_survival": stats.boss3_survival,
                    "boss4_survival": stats.boss4_survival,
                    "boss5_survival": stats.boss5_survival,
                }
            });
            println!("{}", serde_json::to_string_pretty(&output).unwrap());
        }
    }
}
