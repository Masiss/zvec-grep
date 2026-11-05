use super::*;

#[path = "migration_safety.rs"]
mod migration_safety;

fn instance_record(home: &Path) -> Result<serde_json::Value, Box<dyn Error>> {
    Ok(serde_json::from_slice(&std::fs::read(
        home.join("daemon").join("instance.lock"),
    )?)?)
}

fn write_record(home: &Path, record: &serde_json::Value) -> Result<(), Box<dyn Error>> {
    std::fs::write(
        home.join("daemon").join("instance.lock"),
        serde_json::to_vec(record)?,
    )?;
    Ok(())
}

fn current_protocol_version(record: &serde_json::Value) -> Result<u32, Box<dyn Error>> {
    let raw = record
        .get("daemonProtocolVersion")
        .and_then(serde_json::Value::as_u64)
        .ok_or("current daemon record has no protocol version")?;
    let version = u32::try_from(raw)?;
    if version == 0 {
        return Err("current daemon protocol version must be positive".into());
    }
    Ok(version)
}

fn legacy_record(home: &Path) -> Result<(serde_json::Value, u32), Box<dyn Error>> {
    let mut record = instance_record(home)?;
    let current_version = current_protocol_version(&record)?;
    let fields = record.as_object_mut().ok_or("instance record object")?;
    // Keep a real process and shutdown endpoint, but publish the Node.js record
    // shape to exercise fail-closed compatibility without requiring Node.js.
    fields.remove("daemonProtocolVersion");
    fields.remove("listen");
    write_record(home, &record)?;
    Ok((record, current_version))
}

fn explicitly_stop_restart_and_reuse(
    guard: &mut ServerGuard,
    legacy: &serde_json::Value,
    current_version: u32,
    toolset: Option<&str>,
) -> Result<serde_json::Value, Box<dyn Error>> {
    let mut stop = cli(guard, &["--server", "off"]);
    if let Some(token_file) = &guard.token_file {
        stop.arg("--token-file").arg(token_file);
    }
    assert_command_success(&stop.output()?);
    guard.active = false;
    assert!(!guard.home.join("daemon").join("instance.lock").exists());
    assert!(TcpStream::connect(&guard.listen).is_err());

    // A stopped process may leave a stale legacy record. Status must report
    // stopped rather than reject its obsolete protocol before the explicit restart.
    write_record(&guard.home, legacy)?;
    let stale_status = cli(guard, &["--server", "status"]).output()?;
    assert_command_success(&stale_status);
    assert!(
        String::from_utf8_lossy(&stale_status.stdout).contains("Server: stopped"),
        "stale stopped record should report stopped: {stale_status:?}"
    );
    assert_eq!(instance_record(&guard.home)?, *legacy);

    let mut start = cli(guard, &["--server", "on", "--listen", &guard.listen]);
    if let Some(toolset) = toolset {
        start.args(["--mcp-toolset", toolset]);
    }
    if let Some(token_file) = &guard.token_file {
        start.arg("--token-file").arg(token_file);
    }
    assert_command_success(&start.output()?);
    guard.active = true;
    let current = instance_record(&guard.home)?;
    assert_eq!(current["daemonProtocolVersion"], current_version);
    assert_ne!(current["instanceToken"], legacy["instanceToken"]);
    assert_healthy(&guard.listen)?;

    let mut reuse = cli(guard, &["--server", "on", "--listen", &guard.listen]);
    if let Some(toolset) = toolset {
        reuse.args(["--mcp-toolset", toolset]);
    }
    if let Some(token_file) = &guard.token_file {
        reuse.arg("--token-file").arg(token_file);
    }
    assert_command_success(&reuse.output()?);
    assert_eq!(instance_record(&guard.home)?, current);
    Ok(current)
}

fn cli(guard: &ServerGuard, args: &[&str]) -> Command {
    let mut command = Command::new(&guard.binary);
    command
        .args(args)
        .arg("--home")
        .arg(&guard.home)
        .current_dir(&guard.home)
        .env("HOME", &guard.home)
        .env("USERPROFILE", &guard.home)
        .env_remove("ZVEC_GREP_SERVER_TOKEN")
        .env_remove("ZVEC_GREP_SERVER_TOKEN_FILE")
        .env_remove("ZVEC_GREP_MCP_TOOLSET");
    command
}

fn assert_failure(output: &Output, message: &str) {
    assert!(!output.status.success(), "unexpected success: {output:?}");
    assert!(
        String::from_utf8_lossy(&output.stderr).contains(message),
        "expected {message}: {output:?}"
    );
}

fn assert_healthy(listen: &str) -> Result<(), Box<dyn Error>> {
    let mut stream = TcpStream::connect(listen)?;
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    write!(
        stream,
        "GET /healthz HTTP/1.1\r\nHost: {listen}\r\nConnection: close\r\n\r\n"
    )?;
    assert!(read_http_response(&mut stream)?.starts_with("HTTP/1.1 200"));
    Ok(())
}

#[test]
fn legacy_daemon_fails_closed_until_explicit_authenticated_restart() -> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let token_file = home.path().join("token.txt");
    std::fs::write(&token_file, "legacy-transition-token-01234567890123456789")?;
    let (mut guard, _) = start_server(&binary, &home, "agent", Some(&token_file), |command| {
        command
            .env_remove("ZVEC_GREP_SERVER_TOKEN")
            .env_remove("ZVEC_GREP_SERVER_TOKEN_FILE");
    })?;
    let (legacy, current_version) = legacy_record(home.path())?;

    for args in [
        vec!["--server", "status"],
        vec!["--server", "status", "--check-ready"],
        vec!["--status", "--mode", "auto"],
        vec!["--status", "--mode", "server"],
        vec!["--server", "on", "--listen", &guard.listen],
        vec!["--server", "--stdio", "--listen", &guard.listen],
    ] {
        let output = cli(&guard, &args).output()?;
        assert_failure(&output, "legacy zvec-grep daemon");
        assert_failure(&output, "zg --server off");
        assert_eq!(instance_record(home.path())?, legacy);
    }
    let unauthorized_stop = cli(&guard, &["--server", "off"]).output()?;
    assert_failure(&unauthorized_stop, "shutdown request failed with HTTP 401");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;

    let workspace = TempDir::new()?;
    let workspace_path = workspace.path().to_str().ok_or("workspace path")?;
    let auto_index = cli(&guard, &["--index", workspace_path, "--mode", "auto"]).output()?;
    assert_failure(&auto_index, "legacy zvec-grep daemon");
    assert!(
        !workspace.path().join(".zvec-grep").exists(),
        "auto mode must not build directly after a protocol refusal"
    );
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;
    assert_command_success(&cli(&guard, &["--status", "--mode", "direct"]).output()?);

    explicitly_stop_restart_and_reuse(&mut guard, &legacy, current_version, None)?;
    assert_command_success(&guard.stop()?);
    Ok(())
}

#[test]
fn concurrent_starts_fail_closed_until_explicit_stop_then_reuse_current_protocol()
-> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let token_file = home.path().join("token.txt");
    std::fs::write(
        &token_file,
        "migration-token-012345678901234567890123456789",
    )?;
    let (mut guard, _) = start_server(&binary, &home, "full", Some(&token_file), |command| {
        command
            .env_remove("ZVEC_GREP_SERVER_TOKEN")
            .env_remove("ZVEC_GREP_SERVER_TOKEN_FILE");
    })?;
    let (legacy, current_version) = legacy_record(home.path())?;
    let mut children = Vec::new();
    for _ in 0..4 {
        children.push(
            cli(
                &guard,
                &[
                    "--server",
                    "on",
                    "--listen",
                    &guard.listen,
                    "--mcp-toolset",
                    "agent",
                ],
            )
            .arg("--token-file")
            .arg(&token_file)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?,
        );
    }
    let outputs = children
        .into_iter()
        .map(Child::wait_with_output)
        .collect::<Result<Vec<_>, _>>()?;
    assert_eq!(instance_record(home.path())?, legacy);
    for output in outputs {
        assert_failure(&output, "legacy zvec-grep daemon");
    }
    assert_healthy(&guard.listen)?;
    assert_eq!(instance_record(home.path())?, legacy);
    assert!(
        !home.path().join("daemon").join("startup.lock").exists(),
        "rejected concurrent starts must release the startup lock"
    );
    let current = explicitly_stop_restart_and_reuse(&mut guard, &legacy, current_version, None)?;
    assert_eq!(current["mcpToolset"], "agent");

    let direct = cli(
        &guard,
        &["--status", "--mode", "direct", "--color", "never"],
    )
    .output()?;
    let server = cli(
        &guard,
        &["--status", "--mode", "server", "--color", "never"],
    )
    .env("ZVEC_GREP_SERVER_TOKEN_FILE", &token_file)
    .output()?;
    assert_command_success(&direct);
    assert_command_success(&server);
    assert_eq!(direct.stdout, server.stdout);
    assert_command_success(&guard.stop()?);
    Ok(())
}

#[test]
fn stdio_bootstrap_fails_closed_until_explicit_authenticated_restart() -> Result<(), Box<dyn Error>>
{
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let token_file = home.path().join("token.txt");
    std::fs::write(&token_file, "stdio-transition-token-01234567890123456789")?;
    let (mut guard, _) = start_server(&binary, &home, "full", Some(&token_file), |command| {
        command
            .env_remove("ZVEC_GREP_SERVER_TOKEN")
            .env_remove("ZVEC_GREP_SERVER_TOKEN_FILE");
    })?;
    let (legacy, current_version) = legacy_record(home.path())?;
    let bootstrap = cli(&guard, &["--server", "--stdio", "--listen", &guard.listen]).output()?;
    assert_failure(&bootstrap, "legacy zvec-grep daemon");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;

    let current =
        explicitly_stop_restart_and_reuse(&mut guard, &legacy, current_version, Some("full"))?;
    assert_eq!(current["mcpToolset"], "full");

    let mut bridge = StdioBridge::spawn_with_toolset_and_token_file(
        &binary,
        home.path(),
        &guard.listen,
        None,
        Some(&token_file),
    )?;
    let initialized = bridge.request(&json!({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "protocol-upgrade-test", "version": "1"}
        }
    }))?;
    assert_eq!(initialized["result"]["protocolVersion"], "2025-11-25");
    bridge.notify(&json!({
        "jsonrpc": "2.0", "method": "notifications/initialized"
    }))?;
    let tools = bridge.request(&json!({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}
    }))?;
    let mut names = tools["result"]["tools"]
        .as_array()
        .ok_or("tools/list did not return an array")?
        .iter()
        .map(|tool| tool["name"].as_str().ok_or("tool has no name"))
        .collect::<Result<Vec<_>, _>>()?;
    names.sort_unstable();
    assert_eq!(
        names,
        [
            "zvec_grep_index",
            "zvec_grep_index_drop",
            "zvec_grep_index_status",
            "zvec_grep_rg",
            "zvec_grep_search",
            "zvec_grep_server_status",
        ]
    );
    bridge.close()?;
    assert_command_success(&guard.stop()?);
    Ok(())
}

#[test]
fn older_and_newer_protocols_are_rejected_without_replacing_the_daemon()
-> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let (mut guard, _) = start_server(&binary, &home, "agent", None, |_| {})?;
    let current_record = instance_record(home.path())?;
    let current_version = current_protocol_version(&current_record)?;
    let older_version = current_version.saturating_sub(1);
    let newer_version = current_version
        .checked_add(1)
        .ok_or("current protocol version cannot be incremented")?;
    assert_ne!(older_version, current_version);
    assert_ne!(newer_version, current_version);

    let mut mismatches = vec![0, older_version, newer_version];
    mismatches.sort_unstable();
    mismatches.dedup();
    mismatches.retain(|version| *version != current_version);
    assert!(mismatches.contains(&0));
    assert!(!mismatches.contains(&current_version));

    for version in mismatches {
        let mut incompatible = current_record.clone();
        incompatible["daemonProtocolVersion"] = json!(version);
        write_record(home.path(), &incompatible)?;
        for args in [
            vec!["--server", "status", "--check-ready"],
            vec!["--status", "--mode", "auto"],
            vec!["--status", "--mode", "server"],
            vec!["--server", "on"],
            vec!["--server", "--stdio"],
        ] {
            let output = cli(&guard, &args).stdin(Stdio::null()).output()?;
            assert_failure(&output, &format!("uses daemonProtocolVersion {version}"));
            assert_eq!(instance_record(home.path())?, incompatible);
            assert_healthy(&guard.listen)?;
        }
    }
    // Restore the record written by this test binary before explicit cleanup.
    write_record(home.path(), &current_record)?;
    assert_eq!(instance_record(home.path())?, current_record);
    assert_command_success(&guard.stop()?);
    Ok(())
}

#[test]
fn legacy_shutdown_requires_explicit_credentials_before_restart() -> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let token_file = home.path().join("token.txt");
    std::fs::write(
        &token_file,
        "migration-token-012345678901234567890123456789",
    )?;
    let (mut guard, _) = start_server(&binary, &home, "agent", Some(&token_file), |command| {
        command
            .env_remove("ZVEC_GREP_SERVER_TOKEN")
            .env_remove("ZVEC_GREP_SERVER_TOKEN_FILE");
    })?;
    let (legacy, current_version) = legacy_record(home.path())?;
    let refused_start = cli(&guard, &["--server", "on", "--listen", &guard.listen]).output()?;
    assert_failure(&refused_start, "legacy zvec-grep daemon");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;

    let refused_stop = cli(&guard, &["--server", "off"]).output()?;
    assert_failure(&refused_stop, "shutdown request failed with HTTP 401");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;
    assert!(!home.path().join("daemon").join("startup.lock").exists());

    explicitly_stop_restart_and_reuse(&mut guard, &legacy, current_version, None)?;
    assert_command_success(&guard.stop()?);
    Ok(())
}
