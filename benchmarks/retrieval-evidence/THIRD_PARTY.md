# Third-party source attribution

The benchmark does not vendor repository source. `dataset/corpus.json` records
the exact upstream revision, tag, license and tarball hash. Small source excerpts
(`preview`, at most 240 characters) and line ranges are stored only to identify
the annotation anchors.

| Project | Copyright | License | Source |
|---|---|---|---|
| Dapper | DapperLib contributors | Apache-2.0 | https://github.com/DapperLib/Dapper |
| Newtonsoft.Json | James Newton-King and contributors | MIT | https://github.com/JamesNK/Newtonsoft.Json |
| ANTLR4 | Terence Parr and contributors | BSD-3-Clause | https://github.com/antlr/antlr4 |

License texts are available in the upstream repositories at the paths recorded
in `corpus.json` (`license_path`). No source file is relicensed by this
benchmark; the annotations and tooling are independent additions.
