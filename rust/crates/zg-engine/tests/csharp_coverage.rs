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
#[allow(clippy::too_many_lines)]
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
    std::fs::write(
        root.join("Block.cs"),
        concat!(
            "using System.Collections; // qzxasdf\n",
            "namespace BlockNs\n",
            "{\n",
            "    // qzxqwerty\n",
            "    public class BlockThing { public void Go() { } }\n",
            "}\n",
        ),
    )?;
    let engine = ZvecGrep::new();
    let indexed = engine.index(index_options(root)).await?;
    assert_eq!(indexed.files_failed, 0);

    for (query, marker) in [
        ("toplevelalpha", "toplevelalpha"),
        ("jsonbeta", "jsonbeta"),
        ("commentgamma", "commentgamma"),
        ("usingsdelta", "usingsdelta"),
        ("qzxasdf", "qzxasdf"),
        ("qzxqwerty", "qzxqwerty"),
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
        assert_eq!(
            result.items.len(),
            1,
            "{query}: {:?}",
            result
                .items
                .iter()
                .map(|item| (item.relative_path.clone(), item.content.clone()))
                .collect::<Vec<_>>()
        );
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
    let block = engine
        .symbols(SymbolsOptions {
            root: Some(root.to_path_buf()),
            name: "BlockThing".to_owned(),
            auto_update: false,
            allow_remote: true,
            ..SymbolsOptions::default()
        })
        .await?;
    assert_eq!(block.total, 1);
    assert_eq!(block.items[0].scope.as_deref(), Some("BlockNs"));

    engine.close();
    drop(server);
    Ok(())
}
