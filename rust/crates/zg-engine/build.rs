use std::fmt::Write;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

fn hash_file(path: &Path, hasher: &mut Sha256) {
    println!("cargo:rerun-if-changed={}", path.display());
    let bytes = std::fs::read(path).expect("read backend identity input");
    hasher.update((bytes.len() as u64).to_le_bytes());
    hasher.update(bytes);
}

fn hash_tree(path: &Path, hasher: &mut Sha256) {
    let mut entries = std::fs::read_dir(path)
        .expect("read backend identity directory")
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .collect::<Vec<_>>();
    entries.sort();
    for entry in entries {
        if entry.is_dir() {
            hash_tree(&entry, hasher);
        } else if entry.is_file() {
            hash_file(&entry, hasher);
        }
    }
}

/// Hash every source/input that can change an embedding vector without a
/// catalog revision change. The resulting value is part of E2's persisted
/// embedding identity, so a backend upgrade or wrapper edit invalidates
/// cross-pass reuse automatically instead of relying on a hand-maintained
/// revision constant.
fn embedding_backend_build_identity(manifest: &Path) -> String {
    let workspace = manifest
        .ancestors()
        .nth(2)
        .expect("workspace root above crates/zg-engine");
    let mut hasher = Sha256::new();
    for path in [
        workspace.join("Cargo.lock"),
        manifest.join("src/models"),
        workspace.join("crates/zg-llama-native"),
        manifest.join("build.rs"),
    ] {
        if path.is_dir() {
            hash_tree(&path, &mut hasher);
        } else {
            hash_file(&path, &mut hasher);
        }
    }
    hasher
        .finalize()
        .iter()
        .fold(String::new(), |mut hex, byte| {
            write!(hex, "{byte:02x}").expect("write to String");
            hex
        })
}

fn main() {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let backend_identity = embedding_backend_build_identity(&manifest);
    println!("cargo:rustc-env=ZG_EMBEDDING_BACKEND_BUILD={backend_identity}");
    for path in [
        manifest.join("../../Cargo.lock"),
        manifest.join("src/models"),
        manifest.join("../zg-llama-native"),
        manifest.join("build.rs"),
    ] {
        println!("cargo:rerun-if-changed={}", path.display());
    }

    let root = manifest.join("../../scripts");
    let script = root.join("prepare-onnxruntime.py");
    println!("cargo:rerun-if-changed={}", script.display());
    println!(
        "cargo:rerun-if-changed={}",
        root.join("onnxruntime-distributions.json").display()
    );
    for variable in [
        "PYTHON",
        "CARGO_NET_OFFLINE",
        "ORT_DYLIB_PATH",
        "ZG_ORT_CACHE_DIR",
    ] {
        println!("cargo:rerun-if-env-changed={variable}");
    }
    // Explicit external runtimes remain available for custom execution providers
    // (including WebGPU), offline development and embedding the engine.
    if std::env::var_os("ORT_DYLIB_PATH").is_some() {
        return;
    }
    let python = std::env::var_os("PYTHON")
        .unwrap_or_else(|| if cfg!(windows) { "python" } else { "python3" }.into());
    let mut command = std::process::Command::new(python);
    command
        .arg(script)
        .arg("--target")
        .arg(std::env::var("TARGET").expect("Cargo TARGET"))
        .arg("--cargo-out-dir")
        .arg(std::env::var_os("OUT_DIR").expect("Cargo OUT_DIR"));
    if std::env::var_os("CARGO_FEATURE_CUDA").is_some() {
        command.arg("--cuda");
    }
    if let Some(cache) = std::env::var_os("ZG_ORT_CACHE_DIR") {
        command.arg("--cache-dir").arg(cache);
    }
    let status = command
        .status()
        .expect("preparing ONNX Runtime requires Python 3 (override with PYTHON)");
    assert!(status.success(), "preparing ONNX Runtime failed: {status}");
}
