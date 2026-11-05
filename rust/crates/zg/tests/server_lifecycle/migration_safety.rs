use super::*;

#[test]
fn legacy_record_does_not_signal_a_reused_pid() -> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let unrelated_home = TempDir::new()?;
    // A real process owned by another home stands in for an unrelated process
    // that inherited the PID from a crashed legacy daemon.
    let (mut unrelated, _) = start_server(&binary, &unrelated_home, "agent", None, |_| {})?;
    for args in [vec!["--server", "on"], vec!["--server", "--stdio"]] {
        let home = TempDir::new()?;
        let mut guard = ServerGuard {
            binary: binary.clone(),
            home: home.path().to_owned(),
            listen: format!("127.0.0.1:{}", available_port()?),
            token_file: None,
            active: true,
        };
        let mut stale = instance_record(unrelated_home.path())?;
        let fields = stale.as_object_mut().ok_or("instance record object")?;
        fields.remove("daemonProtocolVersion");
        fields.remove("listen");
        stale["startedAt"] = json!(1);
        stale["updatedAt"] = json!(1);
        stale["serverUrl"] = json!(format!("http://{}/mcp", guard.listen));
        std::fs::create_dir(home.path().join("daemon"))?;
        write_record(home.path(), &stale)?;
        let output = cli(&guard, &args)
            .args(["--listen", &guard.listen])
            .stdin(Stdio::null())
            .output()?;
        let survived = assert_healthy(&unrelated.listen).is_ok();
        // Do not let cleanup signal the PID under test through the stale record.
        if instance_record(home.path())?["pid"] == stale["pid"] {
            guard.active = false;
        }
        assert!(
            survived,
            "fail-closed legacy refusal signaled an unrelated process: {output:?}"
        );
        assert_failure(&output, "legacy zvec-grep daemon");
        assert_eq!(instance_record(home.path())?, stale);
    }
    assert_command_success(&unrelated.stop()?);
    Ok(())
}

#[test]
fn occupied_configured_target_does_not_override_legacy_fail_closed_policy()
-> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let (mut guard, _) = start_server(&binary, &home, "agent", None, |_| {})?;
    let (legacy, current_version) = legacy_record(home.path())?;
    let occupied = TcpListener::bind("127.0.0.1:0")?;
    let config_home = home.path().join(".zvec-grep");
    std::fs::create_dir(&config_home)?;
    std::fs::write(
        config_home.join("config.json"),
        serde_json::to_vec(&json!({
            "version": 1, "server": { "host": "127.0.0.1", "port": occupied.local_addr()?.port() }
        }))?,
    )?;
    let refused = cli(&guard, &["--server", "on"]).output()?;
    assert_failure(&refused, "legacy zvec-grep daemon");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;

    let stop = cli(&guard, &["--server", "off"]).output()?;
    assert_command_success(&stop);
    guard.active = false;
    assert!(!home.path().join("daemon").join("instance.lock").exists());
    assert!(TcpStream::connect(&guard.listen).is_err());

    // Once the operator explicitly stops the incompatible daemon, normal bind
    // validation still rejects the occupied configured address without a record.
    let collision = cli(&guard, &["--server", "on"]).output()?;
    assert_failure(&collision, "is already in use");
    assert!(!home.path().join("daemon").join("instance.lock").exists());
    assert!(TcpStream::connect(occupied.local_addr()?).is_ok());

    let restarted = cli(&guard, &["--server", "on", "--listen", &guard.listen]).output()?;
    assert_command_success(&restarted);
    guard.active = true;
    assert_eq!(
        instance_record(home.path())?["daemonProtocolVersion"],
        current_version
    );
    assert_healthy(&guard.listen)?;
    assert_command_success(&guard.stop()?);
    Ok(())
}

#[test]
fn legacy_mismatch_is_rejected_before_contacting_an_unreachable_endpoint()
-> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let (mut guard, _) = start_server(&binary, &home, "agent", None, |_| {})?;
    let original = instance_record(home.path())?;
    let (mut legacy, _current_version) = legacy_record(home.path())?;
    let unavailable = format!("127.0.0.1:{}", available_port()?);
    legacy["serverUrl"] = json!(format!("http://{unavailable}/mcp"));
    write_record(home.path(), &legacy)?;
    let output = cli(&guard, &["--server", "on", "--listen", &unavailable]).output()?;
    assert_failure(&output, "legacy zvec-grep daemon");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_healthy(&guard.listen)?;
    assert!(TcpStream::connect(&unavailable).is_err());
    // Restore the valid endpoint for explicit test cleanup; no request was sent
    // to the unavailable endpoint because compatibility validation failed first.
    write_record(home.path(), &original)?;
    assert_eq!(instance_record(home.path())?, original);
    assert_command_success(&guard.stop()?);
    Ok(())
}

struct DecoyServer {
    stop: Arc<AtomicBool>,
    shutdown_requests: Arc<AtomicUsize>,
    thread: Option<JoinHandle<()>>,
}

impl DecoyServer {
    fn start(listener: TcpListener, status: u16) -> std::io::Result<Self> {
        listener.set_nonblocking(true)?;
        let stop = Arc::new(AtomicBool::new(false));
        let shutdown_requests = Arc::new(AtomicUsize::new(0));
        let worker_stop = Arc::clone(&stop);
        let worker_requests = Arc::clone(&shutdown_requests);
        let thread = std::thread::spawn(move || {
            while !worker_stop.load(Ordering::SeqCst) {
                let Ok((mut stream, _)) = listener.accept() else {
                    std::thread::sleep(Duration::from_millis(5));
                    continue;
                };
                let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                let mut reader = BufReader::new(&mut stream);
                let mut line = String::new();
                if reader.read_line(&mut line).is_err() {
                    continue;
                }
                if line.starts_with("POST /control/shutdown ") {
                    worker_requests.fetch_add(1, Ordering::SeqCst);
                }
                loop {
                    line.clear();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                        break;
                    }
                }
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status} Fixture\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                );
            }
        });
        Ok(Self {
            stop,
            shutdown_requests,
            thread: Some(thread),
        })
    }
}

impl Drop for DecoyServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[test]
fn legacy_localhost_record_does_not_contact_an_ipv4_decoy() -> Result<(), Box<dyn Error>> {
    check_legacy_localhost_refusal(418)
}

#[test]
fn ambiguous_legacy_localhost_preserves_ipv4_and_ipv6_services() -> Result<(), Box<dyn Error>> {
    check_legacy_localhost_refusal(200)
}

fn check_legacy_localhost_refusal(decoy_status: u16) -> Result<(), Box<dyn Error>> {
    let _permit = server_test_permit();
    let reservation = match TcpListener::bind("[::1]:0") {
        Ok(listener) => listener,
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::AddrNotAvailable | std::io::ErrorKind::Unsupported
            ) =>
        {
            eprintln!("IPv6 loopback is unavailable: {error}");
            return Ok(());
        }
        Err(error) => return Err(error.into()),
    };
    let listen = reservation.local_addr()?.to_string();
    let port = reservation.local_addr()?.port();
    let decoy = DecoyServer::start(
        TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port))?,
        decoy_status,
    )?;
    let binary = PathBuf::from(env!("CARGO_BIN_EXE_zg"));
    let home = TempDir::new()?;
    let mut guard = ServerGuard {
        binary,
        home: home.path().to_owned(),
        listen: listen.clone(),
        token_file: None,
        active: true,
    };
    drop(reservation);
    assert_command_success(&cli(&guard, &["--server", "on", "--listen", &listen]).output()?);
    let original = instance_record(home.path())?;
    let (mut legacy, _current_version) = legacy_record(home.path())?;
    legacy["serverUrl"] = json!(format!("http://localhost:{port}/mcp"));
    write_record(home.path(), &legacy)?;
    let target = format!("127.0.0.1:{}", available_port()?);
    let output = cli(&guard, &["--server", "on", "--listen", &target]).output()?;
    assert_failure(&output, "legacy zvec-grep daemon");
    assert_eq!(instance_record(home.path())?, legacy);
    assert_eq!(decoy.shutdown_requests.load(Ordering::SeqCst), 0);
    assert_healthy(&listen)?;

    // Restore the actual IPv6 address for explicit cleanup. Protocol validation
    // must refuse the legacy record before resolving its ambiguous localhost URL.
    write_record(home.path(), &original)?;
    assert_eq!(instance_record(home.path())?, original);
    assert_command_success(&guard.stop()?);
    Ok(())
}
