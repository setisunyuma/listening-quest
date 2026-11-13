# 公開と運用（iPhone から使う）

開発 PC の `http://localhost:3000` は、その PC からしか開けません。iPhone で使うには、**iPhone から届く HTTPS の URL** が必要です
（Service Worker・オフライン・Secure Cookie は HTTPS が前提）。この作業は外部公開を伴うため、実施は利用者の判断で行ってください。

## 標準構成: 小さなサーバー 1 台 + Docker Compose + Caddy

必要なもの: Docker が動く Linux サーバー（メモリ 2GB 程度〜）、ドメイン名（例 `lq.example.com`）とその A/AAAA レコード、80/443 番ポートの開放。

```bash
git clone <this repo> && cd listening-quest
cp .env.example .env
#  必須: POSTGRES_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD（長いランダム文字列）
#        LQ_DOMAIN=lq.example.com
#        LQ_PUBLIC_ORIGIN=https://lq.example.com
#        LQ_COOKIE_SECURE=true
#  任意: ACME_EMAIL, OPENAI_API_KEY, LQ_IMPORT_TOKEN（CLI から送る場合）
docker compose -f infra/compose.yaml -f infra/compose.prod.yaml --env-file .env up -d --build
docker compose -f infra/compose.yaml --env-file .env run --rm api lq create-user owner   # パスワードはその場で入力
docker compose -f infra/compose.yaml --env-file .env run --rm api lq seed
```

Caddy が証明書を自動取得し、`https://lq.example.com` の 1 オリジンで `/api/*` を FastAPI、それ以外を Next.js に振り分けます。
MinIO と PostgreSQL は外部に公開しません。音声はログイン済みの API 経由（Range 対応）でのみ配信します。

iPhone: Safari で `https://lq.example.com` → ログイン → 共有 →「ホーム画面に追加」。README の実機確認項目を順に確認します。

### 未設定のまま残っているもの（利用者が決める・入力する）

| 項目 | 内容 |
|---|---|
| 公開先 | サーバー、ドメイン、DNS。**まだ用意・デプロイしていません** |
| `POSTGRES_PASSWORD` / `MINIO_ROOT_*` | ランダムな値を生成して `.env` に |
| 初期アカウント | `lq create-user` で作成（固定パスワードは同梱していません） |
| `OPENAI_API_KEY` | 実 API で生成する場合のみ。サーバーの `.env` にだけ置く |
| 予算 | 教材管理で 1 日の予算・1 ジョブ上限を確認してから「有料生成を有効にする」 |
| `LQ_IMPORT_TOKEN` / `LQ_SERVER_URL` | 別 PC の生成器 CLI から送る場合のみ |

### 代替: 自宅 PC で動かしてトンネルで公開

Cloudflare Tunnel / Tailscale Funnel などで `http://localhost:3000` を HTTPS 公開することもできます。
その場合も `LQ_PUBLIC_ORIGIN`（公開 URL）と `LQ_COOKIE_SECURE=true` を設定し、PC が起動している間だけ使えます。

## セキュリティ

- 1 アカウント運用（live 領域の作成は 1 人まで）。パスワードは argon2。HttpOnly・SameSite=Lax の Cookie セッション、本番は Secure。
- 状態を変える要求は CSRF トークン（ダブルサブミット）必須、`LQ_PUBLIC_ORIGIN` 設定時は Origin も確認。ログイン失敗は 15 分に 5 回で一時拒否。
- API キー・ストレージ鍵はサーバー環境変数のみ（`NEXT_PUBLIC_*` に入れない、ログや書き出し JSON に入らない）。
- Service Worker は `/api/*` を扱わず、個人データを含まないアプリの殻だけをキャッシュ。音声と未同期回答はユーザー別の保存領域に分け、ログアウト時に削除（未同期がある場合は件数と影響を表示してから）。

## バックアップと復旧

- `make backup`（ローカル）/ Compose では次のように:
  ```bash
  docker compose -f infra/compose.yaml exec -T db pg_dump -U lq -Fc lq > backups/db-$(date +%F).dump
  docker run --rm --network listening-quest_default -v $PWD/backups:/b -e MC_HOST_m=http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000 minio/mc mirror m/listening-quest /b/storage
  ```
- 復元は空の DB へ（`scripts/restore.sh` は学習データのある DB への上書きを拒否）。学習者個人の履歴は「設定 → 書き出し／復元」でも可能（検証付き・上書きなし・二重登録なし）。

| 障害 | 復旧 |
|---|---|
| worker が落ちた | 再起動するだけ。実行権の期限切れジョブを回収し、教材の状態から続きを処理。古い worker は結果を確定できない |
| 生成が失敗・隔離 | 教材管理で理由を確認し「再試行」。内容の問題で隔離されたものは修正版を新しい revision として取り込む |
| 予算で止まった | 翌日（UTC）に自動で再確認。上限を変えると即再確認 |
| 音声ファイルの欠損 | READY 前ならジョブが再生成。`lq gc` で参照されないオブジェクトを回収 |
| 誤った教材を公開した | 教材管理で「無効化」。履歴は残り、成績と推薦から除外して再計算 |
| DB 破損 | 新しい DB にバックアップを復元し、`lq migrate` |
