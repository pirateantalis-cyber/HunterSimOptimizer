use rand::Rng;
use std::collections::HashMap;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AttributeInfo {
    pub cost: i32,
    pub max: f64,  // Use f64::INFINITY for unlimited
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TalentInfo {
    pub cost: i32,
    pub max: i32,
}

#[derive(Debug, Clone)]
pub struct BuildGenerator {
    pub talent_points: i32,
    pub attribute_points: i32,
    pub talents: HashMap<String, TalentInfo>,
    pub attributes: HashMap<String, AttributeInfo>,
    pub attribute_dependencies: HashMap<String, HashMap<String, i32>>,
    pub attribute_point_gates: HashMap<String, i32>,
    pub attribute_exclusions: Vec<(String, String)>,
    pub dynamic_attr_maxes: HashMap<String, i32>,
}

impl BuildGenerator {
    pub fn new(
        level: i32,
        talents: HashMap<String, TalentInfo>,
        attributes: HashMap<String, AttributeInfo>,
        attribute_dependencies: HashMap<String, HashMap<String, i32>>,
        attribute_point_gates: HashMap<String, i32>,
        attribute_exclusions: Vec<(String, String)>,
    ) -> Self {
        let mut gen = Self {
            talent_points: level,
            attribute_points: level * 3,
            talents,
            attributes,
            attribute_dependencies,
            attribute_point_gates,
            attribute_exclusions,
            dynamic_attr_maxes: HashMap::new(),
        };
        
        gen.calculate_dynamic_attr_maxes();
        gen
    }
    
    fn calculate_dynamic_attr_maxes(&mut self) {
        // Find unlimited attributes
        let unlimited_attrs: Vec<String> = self.attributes.iter()
            .filter(|(_, info)| info.max.is_infinite())
            .map(|(name, _)| name.clone())
            .collect();
        
        // Calculate cost to max all limited attributes
        let limited_attr_cost: i32 = self.attributes.iter()
            .filter(|(_, info)| !info.max.is_infinite())
            .map(|(_, info)| info.cost * info.max as i32)
            .sum();
        
        // Share remaining budget among unlimited attributes
        if !unlimited_attrs.is_empty() {
            let remaining_budget = self.attribute_points - limited_attr_cost;
            let max_per_unlimited = (remaining_budget / unlimited_attrs.len() as i32).max(1);
            
            for attr in unlimited_attrs {
                self.dynamic_attr_maxes.insert(attr, max_per_unlimited);
            }
        }
    }
    
    fn get_attr_max(&self, attr: &str) -> i32 {
        if let Some(&dynamic_max) = self.dynamic_attr_maxes.get(attr) {
            return dynamic_max;
        }
        
        if let Some(info) = self.attributes.get(attr) {
            if info.max.is_infinite() {
                return 250; // Fallback
            }
            return info.max as i32;
        }
        
        0
    }
    
    pub fn generate_random_build(&self) -> (HashMap<String, i32>, HashMap<String, i32>) {
        let talents = self.random_walk_talent_allocation();
        let attrs = self.random_walk_attr_allocation();
        (talents, attrs)
    }
    
    pub fn generate_builds(&self, count: usize) -> Vec<(HashMap<String, i32>, HashMap<String, i32>)> {
        (0..count)
            .map(|_| self.generate_random_build())
            .collect()
    }
    
    fn random_walk_talent_allocation(&self) -> HashMap<String, i32> {
        let mut rng = rand::thread_rng();
        let mut result: HashMap<String, i32> = self.talents.keys()
            .map(|k| (k.clone(), 0))
            .collect();
        
        let mut remaining = self.talent_points;
        let talent_names: Vec<String> = self.talents.keys().cloned().collect();
        
        while remaining > 0 {
            // Find valid talents that can accept +1 point
            let valid_talents: Vec<&String> = talent_names.iter()
                .filter(|&t| {
                    if let Some(info) = self.talents.get(t) {
                        result[t] < info.max
                    } else {
                        false
                    }
                })
                .collect();
            
            if valid_talents.is_empty() {
                break;
            }
            
            // Pick random and add 1 point
            let chosen = valid_talents[rng.gen_range(0..valid_talents.len())];
            *result.get_mut(chosen).unwrap() += 1;
            remaining -= 1;
        }
        
        result
    }
    
    fn can_unlock_attribute(&self, attr: &str, current: &HashMap<String, i32>) -> bool {
        // Check point gate
        if let Some(&required_points) = self.attribute_point_gates.get(attr) {
            // Calculate points spent in OTHER attributes
            let points_spent: i32 = current.iter()
                .filter(|(k, _)| k.as_str() != attr)
                .map(|(k, &v)| {
                    if let Some(info) = self.attributes.get(k) {
                        v * info.cost
                    } else {
                        0
                    }
                })
                .sum();
            
            if points_spent < required_points {
                return false;
            }
        }
        
        true
    }
    
    fn random_walk_attr_allocation(&self) -> HashMap<String, i32> {
        let mut rng = rand::thread_rng();
        let mut result: HashMap<String, i32> = self.attributes.keys()
            .map(|k| (k.clone(), 0))
            .collect();
        
        let mut remaining = self.attribute_points;
        let attr_names: Vec<String> = self.attributes.keys().cloned().collect();
        
        let max_iterations = 10000;
        let mut iteration = 0;
        let mut stuck_count = 0;
        
        while remaining > 0 && iteration < max_iterations {
            iteration += 1;
            
            // Find valid attributes
            let mut valid_attrs = Vec::new();
            
            for attr in &attr_names {
                let info = match self.attributes.get(attr) {
                    Some(i) => i,
                    None => continue,
                };
                
                // Check cost
                if info.cost > remaining {
                    continue;
                }
                
                // Check max level
                let max_lvl = self.get_attr_max(attr);
                if result[attr] >= max_lvl {
                    continue;
                }
                
                // Check dependencies
                if let Some(deps) = self.attribute_dependencies.get(attr) {
                    let can_use = deps.iter().all(|(req_attr, &req_level)| {
                        result.get(req_attr).copied().unwrap_or(0) >= req_level
                    });
                    
                    if !can_use {
                        continue;
                    }
                }
                
                // Check point gates
                if !self.can_unlock_attribute(attr, &result) {
                    continue;
                }
                
                // Check exclusions
                let mut excluded = false;
                for (a, b) in &self.attribute_exclusions {
                    if attr == a && result.get(b).copied().unwrap_or(0) > 0 {
                        excluded = true;
                        break;
                    }
                    if attr == b && result.get(a).copied().unwrap_or(0) > 0 {
                        excluded = true;
                        break;
                    }
                }
                
                if excluded {
                    continue;
                }
                
                valid_attrs.push(attr.clone());
            }
            
            if valid_attrs.is_empty() {
                stuck_count += 1;
                if stuck_count >= 3 {
                    break;
                }
            } else {
                stuck_count = 0;
                
                // Pick random and add 1 point
                let chosen = &valid_attrs[rng.gen_range(0..valid_attrs.len())];
                let cost = self.attributes[chosen].cost;
                *result.get_mut(chosen).unwrap() += 1;
                remaining -= cost;
            }
        }
        
        // Validate total cost
        let total_spent: i32 = result.iter()
            .map(|(k, &v)| {
                if let Some(info) = self.attributes.get(k) {
                    v * info.cost
                } else {
                    0
                }
            })
            .sum();
        
        if total_spent > self.attribute_points {
            // Invalid - return empty
            return self.attributes.keys()
                .map(|k| (k.clone(), 0))
                .collect();
        }
        
        result
    }
}
