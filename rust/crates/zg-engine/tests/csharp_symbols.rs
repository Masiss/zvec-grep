//! End-to-end C# structural indexing and exact-symbol enumeration.
//!
//! Every assertion runs through a real indexed workspace opened by the public
//! engine API. No symbol filter is exercised on synthetic metadata alone.

#[allow(dead_code)]
mod support;

use std::{fs, path::Path};

use support::{EmbeddingServer, configure_remote_model, index_options};
use tempfile::tempdir;
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode, QueryFilter, RefreshPolicy, SymbolType},
        },
        symbols::{SymbolRole, SymbolsOptions},
    },
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn symbols(root: &Path, name: &str) -> SymbolsOptions {
    SymbolsOptions {
        root: Some(root.to_path_buf()),
        name: name.to_owned(),
        auto_update: false,
        allow_remote: true,
        ..SymbolsOptions::default()
    }
}

fn write_fixtures(root: &Path) -> TestResult {
    fs::create_dir_all(root.join("Models"))?;
    fs::create_dir_all(root.join("Other"))?;
    fs::write(
        root.join("Models/Order.cs"),
        concat!(
            "namespace Acme.Core;\n",
            "\n",
            "public partial class Order\n",
            "{\n",
            "    public int Id { get; init; }\n",
            "    public int Add(int a, int b) => a + b;\n",
            "    public int Add(int a, int b, int c) => a + b + c;\n",
            "}\n",
        ),
    )?;
    fs::write(
        root.join("Models/Order.Partial.cs"),
        concat!(
            "namespace Acme.Core;\n",
            "\n",
            "public partial class Order\n",
            "{\n",
            "    public void Save() { Order copy = this; }\n",
            "}\n",
        ),
    )?;
    fs::write(
        root.join("Other/Order.cs"),
        concat!(
            "namespace Other;\n",
            "public class Order\n",
            "{\n",
            "    public string Name { get; set; } = \"\";\n",
            "}\n",
        ),
    )?;
    fs::write(
        root.join("Models/Nested.cs"),
        concat!(
            "namespace Acme.Core;\n",
            "public class Outer\n",
            "{\n",
            "    public class Inner\n",
            "    {\n",
            "        public void Run() { }\n",
            "    }\n",
            "}\n",
        ),
    )?;
    fs::write(
        root.join("Models/Shapes.cs"),
        concat!(
            "namespace Acme.Core;\n",
            "public struct Point { public int X; }\n",
            "public record Line(int Qty);\n",
            "public interface IThing { void Run(); }\n",
            "public enum Kind { A, B }\n",
            "public delegate void Handler();\n",
        ),
    )?;
    Ok(())
}

#[tokio::test]
#[allow(clippy::too_many_lines)]
async fn csharp_exact_symbols_and_filters_survive_real_indexing() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_fixtures(root)?;

    let engine = ZvecGrep::new();
    let indexed = engine.index(index_options(root)).await?;
    assert_eq!(indexed.files_failed, 0);

    // Exact-name enumeration returns every declaration, including partials.
    let orders = engine.symbols(symbols(root, "Order")).await?;
    assert_eq!(orders.total, 3, "two partials plus one namespace collision");
    assert_eq!(
        orders
            .items
            .iter()
            .map(|item| item.relative_path.to_string_lossy().into_owned())
            .collect::<Vec<_>>(),
        vec![
            "Models/Order.Partial.cs".to_owned(),
            "Models/Order.cs".to_owned(),
            "Other/Order.cs".to_owned(),
        ],
        "deterministic path/offset ordering"
    );
    assert!(
        orders
            .items
            .iter()
            .all(|item| item.role == SymbolRole::Definition)
    );
    assert!(
        orders
            .items
            .iter()
            .all(|item| item.symbol_type == Some(SymbolType::Class))
    );
    assert_eq!(orders.items[1].range.start_line(), Some(3));
    assert_eq!(orders.items[1].absolute_path, root.join("Models/Order.cs"));

    // Scope disambiguates same-name types and keeps partials together.
    let mut scoped = symbols(root, "Order");
    scoped.scope = Some("Acme.Core".to_owned());
    let scoped = engine.symbols(scoped).await?;
    assert_eq!(scoped.total, 2);
    assert_eq!(scoped.items[0].scope.as_deref(), Some("Acme.Core"));

    // Path and type filters compose with exact-name lookup.
    let mut path_only = symbols(root, "Order");
    path_only.path = Some("Other/Order.cs".to_owned());
    let path_only = engine.symbols(path_only).await?;
    assert_eq!(path_only.total, 1);
    assert_eq!(path_only.items[0].scope.as_deref(), Some("Other"));
    let mut interfaces = symbols(root, "IThing");
    interfaces.symbol_types = vec![SymbolType::Interface];
    let interfaces = engine.symbols(interfaces).await?;
    assert_eq!(interfaces.total, 1);
    assert_eq!(interfaces.items[0].scope.as_deref(), Some("Acme.Core"));
    let mut mismatched = symbols(root, "Order");
    mismatched.symbol_types = vec![SymbolType::Interface];
    assert_eq!(engine.symbols(mismatched).await?.total, 0);

    // Bounds are explicit and deterministic.
    let mut bounded = symbols(root, "Order");
    bounded.limit = Some(1);
    let bounded = engine.symbols(bounded).await?;
    assert_eq!(bounded.total, 3);
    assert!(bounded.truncated);
    assert_eq!(bounded.items.len(), 1);
    assert_eq!(
        bounded.items[0].relative_path,
        Path::new("Models/Order.Partial.cs")
    );

    // Overloads stay distinct declarations with their own signatures.
    let overloads = engine.symbols(symbols(root, "Add")).await?;
    assert_eq!(overloads.total, 2);
    assert_eq!(
        overloads
            .items
            .iter()
            .map(|item| item.signature.as_deref().expect("overload signature"))
            .collect::<Vec<_>>(),
        vec![
            "public int Add(int a, int b)",
            "public int Add(int a, int b, int c)"
        ]
    );
    assert_eq!(overloads.items[0].range.start_line(), Some(6));
    assert_eq!(overloads.items[1].range.start_line(), Some(7));

    // Nested types and interface members report their exact scopes.
    let run = engine.symbols(symbols(root, "Run")).await?;
    assert_eq!(run.total, 2);
    assert_eq!(
        run.items
            .iter()
            .map(|item| item.scope.as_deref().expect("scope"))
            .collect::<Vec<_>>(),
        vec!["Acme.Core::Outer::Inner", "Acme.Core::IThing"]
    );
    let x = engine.symbols(symbols(root, "X")).await?;
    assert_eq!(x.total, 1);
    assert_eq!(x.items[0].symbol_type, Some(SymbolType::Value));
    assert_eq!(x.items[0].scope.as_deref(), Some("Acme.Core::Point"));

    // Struct/record/delegate map onto the existing type categories; enums and
    // interfaces keep their own categories.
    for (name, kind) in [
        ("Point", SymbolType::Class),
        ("Line", SymbolType::Class),
        ("Handler", SymbolType::Class),
        ("Kind", SymbolType::Enum),
        ("IThing", SymbolType::Interface),
    ] {
        let result = engine.symbols(symbols(root, name)).await?;
        assert_eq!(result.total, 1, "{name}");
        assert_eq!(result.items[0].symbol_type, Some(kind), "{name}");
    }
    let kind = engine.symbols(symbols(root, "A")).await?;
    assert_eq!(kind.items[0].scope.as_deref(), Some("Acme.Core::Kind"));
    let namespace = engine.symbols(symbols(root, "Acme.Core")).await?;
    assert!(namespace.total >= 3, "one declaration per source file");
    assert!(
        namespace
            .items
            .iter()
            .all(|item| item.symbol_type == Some(SymbolType::Module))
    );

    // Exact lookup never promotes a reference occurrence.
    let save = engine.symbols(symbols(root, "Save")).await?;
    assert_eq!(save.total, 1);
    assert_eq!(save.items[0].symbol_type, Some(SymbolType::Function));

    // A missing exact name is empty, not a lexical fallback.
    let missing = engine.symbols(symbols(root, "Ordr")).await?;
    assert_eq!(missing.total, 0);
    assert!(missing.items.is_empty());

    // The root symptom: symbol_type filtering over C# has real data now.
    let filtered = engine
        .context(ContextOptions {
            root: Some(root.to_path_buf()),
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: "Order".to_owned(),
            }],
            filter: QueryFilter {
                symbol_types: vec![SymbolType::Class],
                ..QueryFilter::default()
            },
            auto_update: false,
            allow_remote: true,
            limit: Some(20),
            ..ContextOptions::default()
        })
        .await?;
    assert!(!filtered.items.is_empty());
    assert!(filtered.items.iter().all(|item| {
        matches!(
            &item.metadata,
            Some(zg_engine::api::context::result::EntityMetadata::Code(code))
                if code.symbol_type == Some(SymbolType::Class)
        )
    }));
    assert!(filtered.items.iter().any(|item| {
        matches!(
            &item.metadata,
            Some(zg_engine::api::context::result::EntityMetadata::Code(code))
                if code.symbol_name.as_deref() == Some("Order")
        )
    }));

    engine
        .drop_index(zg_engine::api::info::InfoOptions {
            root: Some(root.to_path_buf()),
            include_status: false,
        })
        .await?;
    engine.close();
    drop(server);
    Ok(())
}

#[tokio::test]
#[allow(clippy::too_many_lines)]
async fn csharp_symbols_track_rename_delete_incremental_refresh_and_restart() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_fixtures(root)?;
    let engine = ZvecGrep::new();
    engine.index(index_options(root)).await?;
    assert_eq!(engine.symbols(symbols(root, "Order")).await?.total, 3);

    // Rename without changing declarations: path and range follow the file.
    fs::rename(
        root.join("Other/Order.cs"),
        root.join("Other/OrderRenamed.cs"),
    )?;
    let mut refreshed = symbols(root, "Order");
    refreshed.refresh = Some(RefreshPolicy::Wait);
    let renamed = engine.symbols(refreshed.clone()).await?;
    assert_eq!(renamed.total, 3);
    assert!(
        renamed
            .items
            .iter()
            .any(|item| item.relative_path == Path::new("Other/OrderRenamed.cs"))
    );
    assert!(
        !renamed
            .items
            .iter()
            .any(|item| item.relative_path == Path::new("Other/Order.cs"))
    );

    // Delete one partial: incremental refresh removes exactly its declaration.
    fs::remove_file(root.join("Models/Order.Partial.cs"))?;
    let deleted = engine.symbols(refreshed).await?;
    assert_eq!(deleted.total, 2);
    assert!(
        !deleted
            .items
            .iter()
            .any(|item| item.relative_path == Path::new("Models/Order.Partial.cs"))
    );

    // Restart: a fresh engine sees the persisted, refreshed metadata.
    engine.close();
    let restarted = ZvecGrep::new();
    let persisted = restarted.symbols(symbols(root, "Order")).await?;
    assert_eq!(persisted.total, 2);

    // Stale metadata is returned but marked; an index-only query cannot invent
    // the new declaration until a refresh reconciles the source.
    let source = root.join("Models/Order.cs");
    let mut text = fs::read_to_string(&source)?;
    text.push_str("public static class AddedLater { }\n");
    fs::write(&source, text)?;
    let stale = restarted.symbols(symbols(root, "AddedLater")).await?;
    assert_eq!(stale.total, 0, "no lexical fallback for an unindexed name");
    let stale = restarted.symbols(symbols(root, "Id")).await?;
    assert_eq!(stale.total, 1);
    assert!(stale.has_stale_items());
    assert_eq!(
        stale.items[0].status,
        zg_engine::api::context::result::ContextItemStatus::PossiblyStale
    );

    let mut refresh = symbols(root, "AddedLater");
    refresh.refresh = Some(RefreshPolicy::Wait);
    let added = restarted.symbols(refresh).await?;
    assert_eq!(added.total, 1);
    assert_eq!(added.items[0].symbol_type, Some(SymbolType::Class));
    assert!(!added.has_stale_items());

    restarted
        .drop_index(zg_engine::api::info::InfoOptions {
            root: Some(root.to_path_buf()),
            include_status: false,
        })
        .await?;
    restarted.close();
    drop(server);
    Ok(())
}

#[tokio::test]
async fn symbols_fail_closed_without_an_index_and_validate_bounds() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let engine = ZvecGrep::new();
    let error = engine
        .symbols(symbols(root, "Order"))
        .await
        .expect_err("unindexed workspace must not pretend to enumerate symbols");
    assert_eq!(error.code(), zg_engine::EngineError::NOT_FOUND);
    let mut empty = symbols(root, "   ");
    empty.name = "   ".to_owned();
    let error = engine
        .symbols(empty)
        .await
        .expect_err("empty exact names are invalid");
    assert_eq!(error.code(), zg_engine::EngineError::INVALID_ARGUMENT);
    let mut zero = symbols(root, "Order");
    zero.limit = Some(0);
    let error = engine
        .symbols(zero)
        .await
        .expect_err("zero limit is invalid");
    assert_eq!(error.code(), zg_engine::EngineError::INVALID_ARGUMENT);
    let mut too_large = symbols(root, "Order");
    too_large.limit = Some(501);
    let error = engine
        .symbols(too_large)
        .await
        .expect_err("unbounded enumeration is invalid");
    assert_eq!(error.code(), zg_engine::EngineError::INVALID_ARGUMENT);
    engine.close();
    Ok(())
}
