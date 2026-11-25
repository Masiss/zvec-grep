#[path = "llama_runtime_build.rs"]
mod llama_runtime;

fn command_output(command: &str, args: &[&str]) -> Option<String> {
    let output = std::process::Command::new(command)
        .args(args)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    use std::fmt::Write as _;
    let mut output = String::with_capacity(64);
    for byte in Sha256::digest(bytes) {
        write!(&mut output, "{byte:02x}").expect("write SHA-256 hex");
    }
    output
}

fn sha256(path: &std::path::Path) -> Option<String> {
    Some(sha256_hex(&std::fs::read(path).ok()?))
}

fn native_library(target: &str) -> Option<std::path::PathBuf> {
    let name = if target.contains("windows") {
        "zvec_c_api.dll"
    } else if target.contains("darwin") {
        "libzvec_c_api.dylib"
    } else {
        "libzvec_c_api.so"
    };
    let mut candidates = Vec::new();
    if let Some(directory) = std::env::var_os("DEP_ZVEC_RUST_LIB_DIR") {
        candidates.push(std::path::PathBuf::from(directory).join(name));
    }
    if let Some(directory) = std::env::var_os("ZVEC_LIB_DIR") {
        candidates.push(std::path::PathBuf::from(directory).join(name));
    }
    if let Some(directory) = std::env::var_os("ZVEC_NATIVE_LIBRARY") {
        candidates.push(std::path::PathBuf::from(directory));
    }
    if let (Some(target_dir), Some(profile)) = (
        std::env::var_os("CARGO_TARGET_DIR"),
        std::env::var_os("PROFILE"),
    ) {
        let build = std::path::PathBuf::from(target_dir)
            .join(profile)
            .join("build");
        if let Ok(entries) = std::fs::read_dir(build) {
            for entry in entries.flatten() {
                let path = entry.path().join("out/zvec-prebuilt").join(name);
                if path.is_file() {
                    candidates.push(path);
                }
            }
        }
    }
    candidates.into_iter().find(|path| path.is_file())
}

fn emit_build_info() {
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR"));
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let git = |args: &[&str]| command_output("git", args);
    let commit = git(&["-C", root.to_str().expect("utf8 root"), "rev-parse", "HEAD"])
        .unwrap_or_else(|| "unknown".to_owned());
    let dirty = git(&[
        "-C",
        root.to_str().expect("utf8 root"),
        "status",
        "--porcelain",
    ])
    .is_none_or(|status| !status.is_empty());
    let lock = std::fs::read(root.join("Cargo.lock")).ok();
    let lock_hash = lock.as_deref().map(sha256_hex);
    let zvec_version = std::fs::read_to_string(root.join("Cargo.lock"))
        .ok()
        .and_then(|lock| {
            lock.lines()
                .skip_while(|line| !line.starts_with("name = \"zvec-rust\""))
                .nth(1)
                .and_then(|line| line.strip_prefix("version = \""))
                .and_then(|line| line.strip_suffix('"'))
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "unknown".to_owned());
    let target = std::env::var("TARGET").unwrap_or_else(|_| "unknown".to_owned());
    let native = native_library(&target);
    let native_hash = native.as_deref().and_then(sha256);
    let native_name = native
        .as_ref()
        .and_then(|path| path.file_name())
        .map(|name| name.to_string_lossy().into_owned());
    let ort = std::env::var_os("ORT_DYLIB_PATH").map(std::path::PathBuf::from);
    let ort_hash = ort.as_deref().and_then(sha256);
    let ort_name = ort
        .as_ref()
        .and_then(|path| path.file_name())
        .map(|name| name.to_string_lossy().into_owned());
    let llama_manifest =
        std::env::var_os("DEP_LLAMA_RUNTIME_MANIFEST").map(std::path::PathBuf::from);
    let llama_manifest_hash = llama_manifest.as_deref().and_then(sha256);
    let features = std::env::vars()
        .filter(|(key, _)| key.starts_with("CARGO_FEATURE_"))
        .map(|(key, _)| {
            key.trim_start_matches("CARGO_FEATURE_")
                .to_ascii_lowercase()
        })
        .collect::<Vec<_>>()
        .join(",");
    let info = serde_json::json!({
        "schemaVersion": 1,
        "version": env!("CARGO_PKG_VERSION"),
        "git": { "commit": commit, "dirty": dirty },
        "lockfileSha256": lock_hash,
        "target": target,
        "profile": std::env::var("PROFILE").unwrap_or_else(|_| "unknown".to_owned()),
        "features": features,
        "rustc": command_output("rustc", &["--version"]),
        "zvecVersion": zvec_version,
        "indexFormatVersion": 3,
        "jieba": "embedded-in-zvec-0.8",
        "native": {
            "zvec": { "library": native_name, "sha256": native_hash },
            "onnxruntime": { "library": ort_name, "sha256": ort_hash },
            "llamaRuntimeManifestSha256": llama_manifest_hash,
        },
        "buildTimestamp": std::env::var("SOURCE_DATE_EPOCH").unwrap_or_else(|_| command_output("date", &["-u", "+%Y-%m-%dT%H:%M:%SZ"]).unwrap_or_else(|| "unknown".to_owned())),
    });
    let rendered = serde_json::to_string_pretty(&info).expect("build provenance JSON");
    std::fs::write(
        out.join("build_info.rs"),
        format!("pub const BUILD_INFO: &str = r#\"{rendered}\"#;\n"),
    )
    .expect("write build provenance");
    println!("cargo:rerun-if-env-changed=ZVEC_LIB_DIR");
    println!("cargo:rerun-if-env-changed=ZVEC_NATIVE_LIBRARY");
    println!("cargo:rerun-if-env-changed=SOURCE_DATE_EPOCH");
    println!("cargo:rerun-if-env-changed=ORT_DYLIB_PATH");
    println!("cargo:rerun-if-env-changed=DEP_ZVEC_RUST_LIB_DIR");
    if let Some(path) = ort.as_deref() {
        println!("cargo:rerun-if-changed={}", path.display());
    }
    for relative in [".git/HEAD", ".git/refs", ".git/index"] {
        println!("cargo:rerun-if-changed={}", root.join(relative).display());
    }
    println!(
        "cargo:rerun-if-changed={}",
        root.join("Cargo.lock").display()
    );
    for relative in [
        "Cargo.toml",
        "crates/zg/src",
        "crates/zg-cli/src",
        "crates/zg-engine/src",
        "crates/zg-daemon/src",
        "crates/zg-transport-mcp/src",
    ] {
        println!("cargo:rerun-if-changed={}", root.join(relative).display());
    }
}

fn main() {
    emit_build_info();
    zvec_rust_build::configure();
    if std::env::var_os("CARGO_FEATURE_CUDA").is_some() {
        // zg owns the executable layout for both SDK and source builds. The
        // source wrapper's *.so staging omits versioned SONAME targets.
        let target = std::env::var("TARGET").expect("Cargo TARGET");
        let out = std::env::var_os("OUT_DIR").expect("Cargo OUT_DIR");
        let paths = match (
            std::env::var_os("DEP_LLAMA_RUNTIME_DIR"),
            std::env::var_os("DEP_LLAMA_RUNTIME_MANIFEST"),
        ) {
            (Some(root), Some(manifest)) => llama_runtime::stage(
                std::path::Path::new(&root),
                std::path::Path::new(&manifest),
                &target,
                std::path::Path::new(&out),
            ),
            (None, None) => {
                let backends = std::env::var_os("DEP_LLAMA_BACKENDS_DIR")
                    .expect("source CUDA build must expose DEP_LLAMA_BACKENDS_DIR");
                llama_runtime::stage_source(
                    std::path::Path::new(&backends),
                    &target,
                    std::path::Path::new(&out),
                )
            }
            _ => panic!("incomplete llama.cpp SDK runtime metadata"),
        }
        .expect("stage zg's llama.cpp runtime libraries");
        for path in paths {
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }
    if std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc") {
        // Delay direct imports so the executable can locate its sibling lib/
        // before Windows resolves any native model/storage dependencies.
        cc::Build::new()
            .file("windows_runtime.c")
            .compile("zg_windows_runtime");
        println!("cargo:rerun-if-changed=windows_runtime.c");
        println!("cargo:rustc-link-lib=delayimp");
        println!("cargo:rustc-link-arg=/INCLUDE:__pfnDliNotifyHook2");
        println!("cargo:rustc-link-arg=/INCLUDE:zg_runtime_initializer");
        println!("cargo:rustc-link-arg=/DELAYLOAD:zvec_c_api.dll");
        let libraries: &[&str] = if std::env::var_os("CARGO_FEATURE_CUDA").is_some() {
            &["llama.dll", "ggml.dll", "ggml-base.dll"]
        } else {
            &[]
        };
        for library in libraries {
            println!("cargo:rustc-link-arg=/DELAYLOAD:{library}");
        }
    }
}
