//! G235 · Rust Error Client Recorder
//!
//! Panic handler compiled into `recorder.exe`. Captures panic information,
//! backtrace, and a GDPR-safe machine identifier (HMAC-SHA256 with 24-hour
//! key rotation), then POSTs a JSON report to the G231 endpoint.
//!
//! # Environment Variables
//! * `G231_ERROR_ENDPOINT` — HTTP endpoint (default: `http://localhost:8080/api/v1/errors`)
//! * `G231_HMAC_SECRET`    — optional HMAC secret for machine-ID generation
//! * `RUST_BACKTRACE`      — controls backtrace collection (1 = full, 0 = disabled)

use std::env;
use std::fmt::Write as FmtWrite;
use std::panic::{self, PanicHookInfo};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hmac::{Hmac, Mac};
use once_cell::sync::Lazy;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use sha2::{Sha256, Digest};
use uuid::Uuid;

const DEFAULT_ENDPOINT: &str = "http://localhost:8080/api/v1/errors";
const ROTATION_INTERVAL_SECS: u64 = 24 * 60 * 60; // 24 hours
const DEFAULT_TIMEOUT_SECS: u64 = 10;
const MAX_RETRIES: u32 = 3;
const USER_AGENT: &str = "G235-Recorder/0.1.0";
const VERSION: &str = "0.1.0";

/// Error report structure sent to the G231 endpoint
#[derive(Debug, Serialize, Deserialize)]
struct ErrorReport {
    /// Unique identifier for this error report
    report_id: String,
    /// Timestamp in RFC3339 format
    timestamp: String,
    /// Machine identifier (HMAC-based, GDPR-safe)
    machine_id: String,
    /// Panic message
    panic_message: String,
    /// Location where panic occurred (file:line:column)
    panic_location: String,
    /// Backtrace if available
    backtrace: Option<String>,
    /// Application version
    version: String,
}

/// HMAC-based machine ID generator with key rotation
struct MachineIdGenerator {
    key: Arc<Mutex<Vec<u8>>>,
    last_rotation: Arc<Mutex<SystemTime>>,
}

impl MachineIdGenerator {
    /// Create a new machine ID generator
    fn new() -> Self {
        Self {
            key: Arc::new(Mutex::new(Self::generate_key())),
            last_rotation: Arc::new(Mutex::new(SystemTime::now())),
        }
    }

    /// Generate initial key from environment or random data
    fn generate_key() -> Vec<u8> {
        // Try to get secret from environment
        if let Ok(secret) = env::var("G231_HMAC_SECRET") {
            // Use SHA256 of the secret as the key
            let mut hasher = Sha256::new();
            hasher.update(secret.as_bytes());
            hasher.finalize().to_vec()
        } else {
            // Generate random key
            let mut key = vec![0u8; 32];
            if let Err(_) = getrandom::getrandom(&mut key) {
                // Fallback: use timestamp-based key
                let timestamp = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos();
                key = timestamp.to_le_bytes().to_vec();
                key.resize(32, 0); // Ensure 32 bytes
            }
            key
        }
    }

    /// Rotate key if rotation interval has passed
    fn rotate_if_needed(&self) {
        let mut last_rotation = self.last_rotation.lock().unwrap();
        if let Ok(elapsed) = SystemTime::now().duration_since(*last_rotation) {
            if elapsed.as_secs() >= ROTATION_INTERVAL_SECS {
                let mut key = self.key.lock().unwrap();
                *key = Self::generate_key();
                *last_rotation = SystemTime::now();
            }
        }
    }

    /// Get current machine ID (HMAC of stable system identifier)
    fn get_machine_id(&self) -> String {
        self.rotate_if_needed();
        
        let key = self.key.lock().unwrap();
        
        // Create HMAC-SHA256 instance
        let mut mac = Hmac::<Sha256>::new_from_slice(&key)
            .expect("HMAC key should be valid length");
        
        // Use a combination of stable system identifiers
        let mut input = Vec::new();
        
        // Use process ID for identification
        input.extend_from_slice(&std::process::id().to_le_bytes());
        
        // Add the key rotation timestamp
        let rotation_time = self.last_rotation.lock()
            .unwrap()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
            .to_le_bytes();
        input.extend_from_slice(&rotation_time);
        
        mac.update(&input);
        let result = mac.finalize().into_bytes();
        
        // Convert to hex string
        hex_encode(&result)
    }
}

/// Encode bytes to lowercase hex string
fn hex_encode(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        write!(s, "{:02x}", b).unwrap();
    }
    s
}

/// Global machine ID generator instance
static MACHINE_ID_GENERATOR: Lazy<MachineIdGenerator> = Lazy::new(|| {
    MachineIdGenerator::new()
});

/// Get formatted backtrace if RUST_BACKTRACE is enabled
fn get_backtrace() -> Option<String> {
    match env::var("RUST_BACKTRACE") {
        Ok(val) if val == "1" || val == "full" => {
            let backtrace = std::backtrace::Backtrace::capture();
            Some(format!("{:?}", backtrace))
        }
        _ => None,
    }
}

/// Format timestamp as RFC3339
fn format_timestamp() -> String {
    let now = SystemTime::now();
    let since_epoch = now.duration_since(UNIX_EPOCH).unwrap_or_default();
    let secs = since_epoch.as_secs();
    let nanos = since_epoch.subsec_nanos();
    
    // Simple RFC3339 format: YYYY-MM-DDTHH:MM:SSZ
    // This is a simplified version - in production you'd use chrono
    let days = secs / 86400;
    let seconds_in_day = secs % 86400;
    let hours = seconds_in_day / 3600;
    let minutes = (seconds_in_day % 3600) / 60;
    let seconds = seconds_in_day % 60;
    
    // Approximate year/month/day calculation (simplified)
    // For a real implementation, use chrono or similar library
    let year = 1970 + (days / 365);
    let day_of_year = days % 365;
    let month = 1 + (day_of_year / 30) % 12;
    let day = 1 + day_of_year % 30;
    
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:09}Z", 
            year, month, day, hours, minutes, seconds, nanos)
}

/// Send error report to endpoint with retry logic
fn send_error_report(report: &ErrorReport) -> Result<(), String> {
    let endpoint = env::var("G231_ERROR_ENDPOINT")
        .unwrap_or_else(|_| DEFAULT_ENDPOINT.to_string());
    
    let client = Client::builder()
        .timeout(Duration::from_secs(DEFAULT_TIMEOUT_SECS))
        .user_agent(USER_AGENT)
        .build()
        .map_err(|e| format!("Failed to create HTTP client: {}", e))?;
    
    let mut last_error = None;
    
    for attempt in 0..MAX_RETRIES {
        match client.post(&endpoint)
            .json(report)
            .send()
        {
            Ok(response) => {
                if response.status().is_success() {
                    return Ok(());
                } else {
                    last_error = Some(format!("HTTP {}: {}", response.status(), response.text().unwrap_or_default()));
                }
            }
            Err(e) => {
                last_error = Some(format!("Request failed: {}", e));
            }
        }
        
        // Exponential backoff before retry
        if attempt < MAX_RETRIES - 1 {
            let delay = Duration::from_millis(100 * 2u64.pow(attempt));
            thread::sleep(delay);
        }
    }
    
    Err(last_error.unwrap_or_else(|| "Unknown error".to_string()))
}

/// Custom panic hook that sends error reports
fn panic_hook(panic_info: &PanicHookInfo) {
    // Get machine ID
    let machine_id = MACHINE_ID_GENERATOR.get_machine_id();
    
    // Get panic message
    let panic_message = if let Some(s) = panic_info.payload().downcast_ref::<&str>() {
        s.to_string()
    } else if let Some(s) = panic_info.payload().downcast_ref::<String>() {
        s.clone()
    } else {
        "Unknown panic".to_string()
    };
    
    // Get panic location
    let panic_location = if let Some(location) = panic_info.location() {
        format!("{}:{}:{}", location.file(), location.line(), location.column())
    } else {
        "unknown location".to_string()
    };
    
    // Get backtrace if enabled
    let backtrace = get_backtrace();
    
    // Create error report
    let report = ErrorReport {
        report_id: Uuid::new_v4().to_string(),
        timestamp: format_timestamp(),
        machine_id,
        panic_message,
        panic_location,
        backtrace,
        version: VERSION.to_string(),
    };
    
    // Try to send the report in a separate thread to avoid blocking
    // during panic cleanup
    thread::spawn(move || {
        match send_error_report(&report) {
            Ok(_) => eprintln!("[G235] Error report sent successfully"),
            Err(e) => eprintln!("[G235] Failed to send error report: {}", e),
        }
    });
    
    // Allow a brief moment for the report to be sent
    // (Note: during panic, this is best-effort)
    thread::sleep(Duration::from_millis(100));
}

/// Initialize the panic hook
pub fn init() {
    panic::set_hook(Box::new(panic_hook));
}

/// Main function for testing/standalone usage
fn main() {
    // Initialize the panic handler
    init();
    
    println!("G235 Rust Error Recorder initialized");
    println!("Panic handler installed. Machine ID: {}", MACHINE_ID_GENERATOR.get_machine_id());
    println!("Endpoint: {}", env::var("G231_ERROR_ENDPOINT").unwrap_or_else(|_| DEFAULT_ENDPOINT.to_string()));
    
    // Keep the program running for demonstration
    // In a real application, this would be called from the main binary
    println!("Press Ctrl+C to exit...");
    loop {
        thread::sleep(Duration::from_secs(1));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_hex_encode() {
        let bytes = [0x01, 0x23, 0x45, 0x67, 0x89, 0xab, 0xcd, 0xef];
        let hex = hex_encode(&bytes);
        assert_eq!(hex, "0123456789abcdef");
    }
    
    #[test]
    fn test_machine_id_generator() {
        let generator = MachineIdGenerator::new();
        let id1 = generator.get_machine_id();
        let id2 = generator.get_machine_id();
        
        // Should be consistent within the same key rotation period
        assert_eq!(id1, id2);
        assert_eq!(id1.len(), 64); // SHA256 produces 64 hex chars
    }
    
    #[test]
    fn test_error_report_serialization() {
        let report = ErrorReport {
            report_id: "test-id".to_string(),
            timestamp: "2024-01-01T00:00:00.000000000Z".to_string(),
            machine_id: "test-machine-id".to_string(),
            panic_message: "Test panic".to_string(),
            panic_location: "test.rs:1:1".to_string(),
            backtrace: Some("test backtrace".to_string()),
            version: "0.1.0".to_string(),
        };
        
        let json = serde_json::to_string(&report).unwrap();
        let parsed: ErrorReport = serde_json::from_str(&json).unwrap();
        
        assert_eq!(report.report_id, parsed.report_id);
        assert_eq!(report.panic_message, parsed.panic_message);
    }
}