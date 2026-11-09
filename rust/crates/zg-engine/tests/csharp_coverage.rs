//! C# files with declarations must not lose searchable non-declaration content.

#[allow(dead_code)]
mod support;

use support::{EmbeddingServer, configure_remote_model, index_options};
use tempfile::tempdir;
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode},
        },
        symbols::SymbolsOptions,
    },
};

#[tokio::test]
async fn csharp_gap_entities_preserve_usings_comments_and_top_level_code()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    std::fs::write(
        root.join("Program.cs"),
        concat!(
            "using System.Text.Json; // jsonbeta\n",
            "// commentgamma\n",
            "Console.WriteLine(\"toplevelalpha\");\n",
            "public class Marker { public void Inside() { } }\n",
        ),
    )?;
    std::fs::write(
        root.join("OnlyUsings.cs"),
        "using System.Text;\n// usingsdelta\n",
    )?;
    let engine = ZvecGrep::new();
    let indexed = engine.index(index_options(root)).await?;
    assert_eq!(indexed.files_failed, 0);

    for (query, marker) in [
        ("toplevelalpha", "toplevelalpha"),
        ("jsonbeta", "jsonbeta"),
        ("commentgamma", "commentgamma"),
        ("usingsdelta", "usingsdelta"),
    ] {
        let result = engine
            .context(ContextOptions {
                root: Some(root.to_path_buf()),
                routes: vec![ContextRoute {
                    mode: ContextRouteMode::Fts,
                    query: query.to_owned(),
                }],
                auto_update: false,
                allow_remote: true,
                limit: Some(10),
                ..ContextOptions::default()
            })
            .await?;
        assert_eq!(result.items.len(), 1, "{query}");
        assert!(
            result.items[0].content.contains(marker),
            "{query}: {:?}",
            result.items[0].content
        );
    }

    // Structured declarations remain available and are not replaced by gaps.
    let symbols = engine
        .symbols(SymbolsOptions {
            root: Some(root.to_path_buf()),
            name: "Marker".to_owned(),
            auto_update: false,
            allow_remote: true,
            ..SymbolsOptions::default()
        })
        .await?;
    assert_eq!(symbols.total, 1);
    let inside = engine
        .symbols(SymbolsOptions {
            root: Some(root.to_path_buf()),
            name: "Inside".to_owned(),
            auto_update: false,
            allow_remote: true,
            ..SymbolsOptions::default()
        })
        .await?;
    assert_eq!(inside.total, 1);
    assert_eq!(inside.items[0].scope.as_deref(), Some("Marker"));

    engine.close();
    drop(server);
    Ok(())
}
