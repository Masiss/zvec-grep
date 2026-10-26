use std::{env, fs, path::PathBuf, process::Command};

fn run(command: &mut Command, what: &str) {
    let status = command.status().unwrap_or_else(|e| panic!("{what}: {e}"));
    assert!(status.success(), "{what} failed with {status}");
}

fn main() {
    let manifest = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let m4_root = manifest.parent().expect("M4 root");
    let work = m4_root.join(".work/rust-bridge");
    let build = work.join("cmake");
    let tmp = work.join("tmp");
    fs::create_dir_all(&tmp).expect("create repository-local build tmp");

    let mut configure = Command::new("cmake");
    configure
        .arg("-S")
        .arg(m4_root)
        .arg("-B")
        .arg(&build)
        .arg("-DCMAKE_BUILD_TYPE=RelWithDebInfo")
        .env("TMPDIR", &tmp);
    run(&mut configure, "configure M4 Rust bridge");

    let mut compile = Command::new("cmake");
    compile
        .arg("--build")
        .arg(&build)
        .arg("--target")
        .arg("m4_rust_file_adapter")
        .arg("--parallel")
        .arg("2")
        .env("TMPDIR", &tmp);
    run(&mut compile, "build M4 Rust bridge");

    println!("cargo:rustc-link-search=native={}", build.display());
    println!("cargo:rustc-link-lib=static=m4_rust_file_adapter");
    println!("cargo:rustc-link-lib=static=m4_research");
    println!("cargo:rustc-link-lib=dylib=lz4");
    println!("cargo:rustc-link-lib=dylib=stdc++");
    println!("cargo:rustc-link-lib=dylib=pthread");
    println!(
        "cargo:rerun-if-changed={}",
        m4_root.join("CMakeLists.txt").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        m4_root.join("src/rust_file_adapter.cc").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        m4_root.join("include/m4/rust_file_adapter.h").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        m4_root.join("src/index.cc").display()
    );
}
