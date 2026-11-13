# 取り込みサンプル

- `v2/manifest.json` + `v2/*.wav` — 現行形式（schema_version 2）。Unit 1件と、必要な全クリップの音声。
  音声はトーン（**音声動作確認用**）で、実用のリスニング教材ではありません。
- `v1/episode.json` + `v1/ep-sample.wav` — 以前のラジオ生成器の形式（schema_version 1）。
  - 正答入りの完成音声は **ラジオ専用** Unit として取り込まれます（解答型には出ません）。
  - `q1` は本文・正答・根拠行がそろっているので Unit に変換されます（本文の音声は別途必要）。
  - `q2` は根拠行（`evidence_line`）が無いため変換されず、理由が報告されます。正答や根拠を勝手に補いません。
- スキーマ: `packages/contracts/unit.schema.json`、全例題をまとめたサンプル: `packages/contracts/examples/manifest.sample.json`

```bash
cd services/core
uv run lq validate ../../fixtures/import_sample/v2/manifest.json ../../fixtures/import_sample/v1/episode.json
uv run lq import ../../fixtures/import_sample/v2/manifest.json --realm demo     # ローカルDBへ
LQ_SERVER_URL=https://lq.example.com LQ_IMPORT_TOKEN=... uv run lq import ../../fixtures/import_sample/v2/manifest.json --remote   # サーバーへ送信
curl -H "Authorization: Bearer $LQ_IMPORT_TOKEN" -F realm=live -F manifest=@v2/manifest.json $(for f in v2/*.wav; do echo -F files=@$f; done) https://lq.example.com/api/import
```
