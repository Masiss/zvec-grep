//! Multi-term FTS must not return arbitrary documents when terms do not match.

#[allow(dead_code)]
mod support;

use support::{EmbeddingServer, configure_remote_model, index_options};
use tempfile::tempdir;
use zg_engine::{
    ZvecGrep,
    api::context::{
        ContextOptions,
        options::{ContextRoute, ContextRouteMode},
    },
};

async fn fts_paths(
    engine: &ZvecGrep,
    root: &std::path::Path,
    query: &str,
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
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
    Ok(result
        .items
        .iter()
        .map(|item| item.relative_path.to_string_lossy().into_owned())
        .collect())
}

#[tokio::test]
async fn multi_term_fts_is_or_and_never_falls_back_to_all_documents()
-> Result<(), Box<dyn std::error::Error>> {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    std::fs::write(
        root.join("alpha.cs"),
        "public class Alpha { public void First() { } } // alpha_marker\n",
    )?;
    std::fs::write(
        root.join("beta.cs"),
        "public class Beta { public void Second() { } } // beta_marker\n",
    )?;
    std::fs::write(
        root.join("notes.md"),
        "# 文档\n\n发票对账流程说明。财务系统每日核对发票与总账。\n",
    )?;
    std::fs::write(
        root.join("widget.cs"),
        "namespace Demo;\npublic class Widget { public void Run() { } }\n",
    )?;
    let engine = ZvecGrep::new();
    engine.index(index_options(root)).await?;

    assert!(fts_paths(&engine, root, "zzzz qqqq").await?.is_empty());
    assert!(fts_paths(&engine, root, "zzzz").await?.is_empty());
    assert!(fts_paths(&engine, root, "---").await?.is_empty());

    let alpha = fts_paths(&engine, root, "alpha_marker zzzz").await?;
    assert_eq!(alpha, vec!["alpha.cs"]);
    let alpha_boolean = fts_paths(&engine, root, "alpha_marker or zzzz").await?;
    assert_eq!(alpha_boolean, vec!["alpha.cs"]);
    let mut both = fts_paths(&engine, root, "alpha_marker beta_marker").await?;
    both.sort();
    assert_eq!(both, vec!["alpha.cs", "beta.cs"]);
    let cjk = fts_paths(&engine, root, "发票对账").await?;
    assert_eq!(cjk, vec!["notes.md"]);
    let cjk_terms = fts_paths(&engine, root, "发票 对账").await?;
    assert_eq!(cjk_terms, vec!["notes.md"]);
    assert!(fts_paths(&engine, root, "不存在的词语").await?.is_empty());

    // C# members no longer inherit their class scope as searchable text; the
    // class/namespace declarations still carry it.
    let widget = fts_paths(&engine, root, "Widget").await?;
    assert_eq!(widget, vec!["widget.cs"]);
    let demo = fts_paths(&engine, root, "Demo").await?;
    assert_eq!(demo, vec!["widget.cs"]);
    let run = fts_paths(&engine, root, "Run").await?;
    assert!(!run.is_empty() && run.iter().all(|path| path == "widget.cs"));

    engine.close();
    drop(server);
    Ok(())
}
