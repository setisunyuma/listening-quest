# Listening Quest

個人用の適応型リスニング学習 Web アプリ（PWA）と、問題生成・音声生成・取り込みの仕組みをまとめたモノレポです。
共通テスト／英検準1級／英検1級／C1／C2 の5モードで、今日のクエスト（日課）・ラジオ・復習・実力チェックを使えます。

- Web: Next.js 16 / React 19 / TypeScript（`apps/web`）。業務ロジックは持たず、API の型は OpenAPI から生成。
- API・worker・生成器・CLI: Python 3.13 / FastAPI / SQLAlchemy / Alembic（`services/core`、パッケージ名 `lq`）。
- DB: PostgreSQL 16。ジョブキューも PostgreSQL の表。音声はファイル保存（本番はボリューム）または S3 互換ストレージ（開発用 Compose では SeaweedFS）。
- 契約: `packages/contracts/openapi.json`・`unit.schema.json`・例題 JSON。
- 詳細: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) / [docs/LEARNING_SYSTEM.md](docs/LEARNING_SYSTEM.md) / [docs/CONTENT_PIPELINE.md](docs/CONTENT_PIPELINE.md) / [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) / [docs/DECISIONS.md](docs/DECISIONS.md) / [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)

> **注意** デモ領域の音声はビープ音（トーン）です。画面にも「音声動作確認用」と表示され、実用のリスニング教材ではありません。
> 実用の音声は、OpenAI の実 API で生成するか、既存の音声を取り込み、確認を通った後に使えます。

## 1. 最短の起動（Docker なし・開発 PC）

必要なもの: Python 3.13 と [uv](https://docs.astral.sh/uv/)、Node.js 22、PostgreSQL 16 のサーバーバイナリ（`initdb`）、FFmpeg。

```bash
cp .env.example .env  # 任意。必要な値だけ書く（OPENAI_API_KEY など）
make bootstrap        # 依存関係のインストール、ローカルDB起動(127.0.0.1:55432)、migration、例題の登録
make user             # 本番用アカウント作成（パスワードはその場で入力。固定パスワードは同梱しません）
make dev              # API :8000 + worker + Web :3000 を起動（Ctrl-C で全部止まる）
```

`.env` はリポジトリ直下のものを、API・worker・CLI のどれも、どのディレクトリから起動しても読みます。
実際に読まれた設定は `cd services/core && uv run lq config` で確認できます（秘密値は「set」とだけ表示されます）。

ブラウザーで http://localhost:3000 を開いてログインします。
本番領域（live）は最初は空です。例題は登録済みですが、**実際の音声が付くまで学習画面には出ません**（下の 4・6・iPhone で使う を参照）。

## 2. デモ（有料 API なしで全体を試す）

```bash
make demo             # デモ領域を作成: 例題24件 + 手続き生成の教材 + 実力チェック用セット。数分かかります
```

最後に `Demo login: demo / <パスワード>` が一度だけ表示されます（`cd services/core && uv run lq set-password demo` で変更可）。
デモ領域では、問題作成・検品・音声・音声確認がすべて無料のデモ用プロバイダーで動き、同じ業務ロジックで
「生成 → READY → 教材一覧 → 再生 → 解答 → 採点 → 次の問題の変化」まで確認できます。
デモの教材・成績は本番領域と完全に分かれており、本番の一覧には出ません。

## 3. Docker Compose で起動

```bash
cp .env.example .env  # POSTGRES_PASSWORD などを書き換える
make compose-up       # web / api / worker / PostgreSQL / S3互換ストレージ(SeaweedFS)
docker compose -f infra/compose.yaml --env-file .env run --rm api lq create-user owner
docker compose -f infra/compose.yaml --env-file .env run --rm api lq seed
```

http://localhost:3000 を開きます。停止は `make compose-down`。データは Docker volume に残ります。

## 4. 実 API の設定（OpenAI）

**まず 1 教材だけ試す（費用上限つき）**: 共通テスト第3問の短い教材を 1 件だけ、実 API で
作成→ブラインド検品→音声→独立した音声認識による確認→READY まで進め、続けてアプリの画面で再生・解答・保存まで確かめます。

```bash
OPENAI_API_KEY=... scripts/live-verify.sh 1        # 1 = この検証全体の費用上限（USD、再試行を含む）
scripts/live-verify.sh 1 --rehearsal                # 同じ流れを無料のデモ用プロバイダーで予行（費用 0）
```

この検証は専用の DB（`lq_verify`）を使い、学習データには触れません。全体の有料生成は OFF のままで、
登録済み例題や自動補充は処理されません。報告（費用の内訳、クリップごとの音声認識の結果）とスクリーンショットは `var/verify/` に保存されます。
公開後は、アプリの **教材管理 → 実APIで1教材を検証** から同じことができます。

**日常の利用:**

1. `.env`（または環境変数）に `OPENAI_API_KEY` を設定して API と worker を再起動。キーはサーバー側だけで使い、ブラウザーには渡りません。
2. `cd services/core && uv run lq doctor` で FFmpeg・DB・ストレージ・モデル名・単価表を確認。
3. Web の「教材管理」→「有料生成と予算」で 1日の予算（初期 1 USD）・1ジョブ上限（0.5 USD）・同時実行数（1）を確認し、
   **「有料生成を有効にする」をオン**にします。キーがあるだけでは有料処理は始まりません。
4. 既定モデル: 問題作成と検品 `gpt-6.1-terra`（構造化出力）、音声 `gpt-realtime-2.1-mini`（Realtime API、サーバー側 WebSocket）、
   音声確認 `gpt-transcribe`（独立した音声認識）。`.env.example` で変更できます。単価の出典と確認日は `services/core/lq/generator/prices.yaml`。

## 5. 1件生成する

Web: 教材管理 → 「教材を生成」で形式・技能・難度帯・数量を選び「生成を予約」。進み具合・失敗理由・再試行が同じ画面に出ます。
READY になると教材一覧に数秒以内で自動的に並びます（ページを開き直す必要はありません）。

CLI:

```bash
cd services/core
uv run lq plan common_test 3 --skill numbers_negation         # 見積もりだけ（APIは呼ばない）
uv run lq generate common_test 3 --skill numbers_negation     # 生成→検品→音声→確認→READY
uv run lq generate common_test 3 --realm demo                 # デモ用プロバイダーで同じ流れ
```

その他のコマンド: `doctor validate plan generate review approve render build import demo outbox worker gc openapi schema migrate seed create-user set-password`（`uv run lq --help`）。

## 6. JSON の取り込み

- Web: 教材管理 → 「JSONから取り込み」で manifest JSON と音声ファイルを選ぶ。
- CLI: `uv run lq import <manifest.json> [音声...] --realm live`、別サーバーへは `--remote`（`LQ_SERVER_URL`・`LQ_IMPORT_TOKEN`）。
- サンプルと説明: [fixtures/import_sample/README.md](fixtures/import_sample/README.md)。スキーマ: `packages/contracts/unit.schema.json`。

JSON 内に「検品済み」と書かれていても承認にはなりません。API 検品か、教材管理の「内容を承認」（自分で確認した場合）で記録を作ります。
音声付きの取り込みでは音声合成を飛ばし、音声確認（独立 ASR、または「音声を承認」= 人が聞いて確認）を経て READY になります。

本番領域の例題（`make bootstrap` で登録済み）は、有料生成をオンにすると worker が音声を作って READY にします。

## iPhone で使う（公開）

[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) にあります。推奨は、ルートの Dockerfile（Web・API・worker が 1 コンテナ）を Railway で動かす構成で、
スマホのブラウザーだけで公開できます。最初のアカウントは `LQ_SETUP_CODE`（自分で決める合言葉）を設定してから `https://<URL>/setup` で作ります。

## 7. 日課の使い方

ホーム →「クエストを開始」で今日の計画（推定30分・5モード合計）が固定されます。1枠 = 1 Unit。
誤答すると、まだ始めていない枠が同じ技能を問う別の Unit に入れ替わります（枠数と時間上限は増えません）。
モードを変えて残りも変えたいときは「残りを○○に変更」。ラジオ中心の日は「ラジオ中心で開始」。
移動中に解く分は「未開始分をオフライン用に保存」→ オフラインで解答 → 次にオンラインになったとき採点されます。

## 8. 停止・再起動・バックアップ

```bash
make stop                     # API・worker・Web・ローカルDBを停止（データは残る）
make dev                      # 再開。worker は途中のジョブを回収して続きから処理
make backup                   # DB ダンプ + 音声を backups/<時刻>/ に保存（SHA256SUMS 付き）
make restore DIR=backups/...  # 空のDBへ復元（既存データのあるDBへの上書きは拒否）
```

学習履歴と設定の JSON 書き出し・復元は Web の「設定」→「データ」から。復元は内容を検証し、既存の記録を上書き・二重登録しません。

## 9. テスト

```bash
make test             # ruff・pytest(114件: 受入テスト1〜19、音声検品の表記差、設定読み込みなどを含む)・lint・型検査・契約チェック・build
make e2e              # Playwright(14件): 実サーバー + worker + 本番ビルドに対し、スマホ幅とデスクトップで実行
scripts/caddy-validate.sh <caddy>   # Caddyfile を Caddy 自身で検証（ACME_EMAIL あり／なし）
```

テストは有料 API を呼びません（HTTP/WebSocket を遮断し、デモ用プロバイダーを使います。`.env` も読みません）。
CI（GitHub Actions）では、上記に加えて次を実行します。
- **e2e**: Playwright の E2E
- **compose**: Docker Compose（PostgreSQL・SeaweedFS・API・worker・Web）を実際に起動し、ログイン→生成→S3 からの Range 取得→解答
- **image**: 1 コンテナのイメージを PostgreSQL とボリュームで起動し、`/setup`→生成→再起動後の保持
- **infra**: Caddy と Compose 設定の検証

ブラウザーの自動テストは Chromium のスマホ幅エミュレーションであり、iPhone 実機での確認ではありません。

## 10. iPhone 実機での確認項目（自動テストでは確認していません）

ブラウザーの自動テストは Chromium のスマホ幅エミュレーションです。以下は iPhone 実機で確認してください。

- [ ] Safari で開き、共有 →「ホーム画面に追加」→ アイコンから起動できる
- [ ] 最初の再生がタップで始まり、音が出ていない間に「再生中」と表示されない
- [ ] ラジオ再生中に画面をロックしても再生が続くか、ロック画面の再生・一時停止・シークが効くか
- [ ] 別のアプリへ切り替えた後（バックグラウンド）の再生継続
- [ ] 通話の着信で止まった後、「続きから再生」で同じ位置から再開できる
- [ ] イヤホン・Bluetooth の接続／切断で止まった後の再開
- [ ] 機内モードで、保存した問題に解答でき「解答済み・採点待ち」になる。保存したラジオがオフラインで再生できる
- [ ] 機内モード解除後に自動で同期され、採点結果・解説が見られる。XP や日課が二重に増えない
- [ ] 長時間放置後に開いたとき、ログインし直しを求められ、未同期の回答が残っている

ロック画面での再生継続などは OS・端末設定・ブラウザーの仕様に左右され、コードだけで保証はできません。
