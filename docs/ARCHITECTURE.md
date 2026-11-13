# アーキテクチャ

```
iPhone / PC ブラウザー (PWA)
   │  HTTPS・同一オリジン（Cookie セッション + CSRF トークン）
   ▼
Caddy ──/api/*──► FastAPI (services/core/lq/api)  ──► PostgreSQL（業務データ + ジョブ表）
   └──その他───► Next.js (apps/web)                └─► S3互換ストレージ（音声。公開しない）
                                     worker (lq worker) ──┘   OpenAI（内容生成・検品・音声・音声認識）
```

開発時（`make dev`）は Caddy の代わりに Next.js の `src/app/api/[...path]/route.ts` が `/api/*` をそのまま FastAPI へ中継します（業務ロジックなし）。

## 責務の分け方

| 層 | 場所 | 内容 |
|---|---|---|
| 純粋なルール | `lq/domain` | 教材スキーマと検証・content_hash・Learner DTO（`content.py`）、モードプロファイルと採点（`profiles.py` + `lq/profiles/*.json`）、学習日・初見判定・難度・復習・XP（`rules.py`）、推薦と日課（`recommend.py`）。時刻と seed は引数で受け取る |
| サービス | `lq/services` | DB トランザクション単位の処理。解答（`learning.py`）、日課（`plans.py`）、ラジオ（`radio.py`）、実力チェック（`assessment.py`）、集計（`stats.py`）、取り込み（`importer.py`）、無効化（`admin.py`）、書き出し復元（`backup.py`）、自動補充（`replenish.py`）、認証（`auth.py`） |
| 生成 | `lq/generator` | provider interface（`providers.py`）、OpenAI 実装（`openai_provider.py`）、デモ実装（`demo.py`）、テンプレートとルーブリック（`templates.yaml`）、予算（`budget.py` + `prices.yaml`） |
| 音声 | `lq/audio` | PCM 組み立て（無音は明示的なゼロ値）、トラックのレンダリング（`render.py`）、音声 QA と書き起こし照合（`qa.py`） |
| ジョブ | `lq/jobs` | PostgreSQL ジョブ表（lease・世代番号・heartbeat・回収・有限回 retry）とパイプライン |
| API | `lq/api` | 認証・CSRF・Range 対応の音声配信・各エンドポイント。OpenAPI を出力 |
| Web | `apps/web` | 画面・音声プレイヤー・IndexedDB（未同期回答と問題パック）・Cache Storage（検証済み音声）・Service Worker（アプリの殻だけ） |

採点・日課・推薦・XP・解放条件はすべて FastAPI 側にあり、Web は結果を表示するだけです。

## データモデルの要点（`lq/db/models.py`）

- **Unit** は不変の版（`realm, source, content_id, revision` で一意）。本文（StimulusSegment / Utterance）・設問（Question、型付き正答、根拠参照）・採点グループ（ScoringGroup）・Feedback は `content`（JSON、Pydantic で検証）に入る。`stimulus_fingerprint`（台本のみのハッシュ）で「同じ音声の別設問版」を、`family_id` で類題を追跡。
- **Asset**: 用途別の音声（`stimulus:<segment>`・`exam`・`feedback`・`radio`・`utt:<id>`・番組 `program`）、ハッシュ・長さ・timeline・transcript。
- **ReviewRecord**: `content_hash` に紐づく確認記録（`api_reviewed` / `manual_reviewed` / `author_reviewed` / デモ専用 `demo_mock_reviewed`、音声は `audio_auto_checked` / `audio_human_listened` / `audio_demo_mock_checked`）。
- **GenerationJob / ImportBatch / CostEntry**（予約→確定 / 不明 / 返却）/ **AudioCache**。
- 学習記録: **User**（realm = live / demo）・**ModeState**（モード別難度帯）・**LearningSession**・**Attempt**（client 発行の ID）・**ExposureEvent**・**SkillObservation**・**ReviewSchedule**・**SelfReport**・**DailyPlan / PlanSlot**・**XpEvent**（dedupe_key で一意）・**RadioProgram**・**MockSet**・**ClientEvent**（再送の冪等化）・**AuditLog**。

## 正答を漏らさない仕組み

- 解答画面・先読み・オフライン用パックには `learner_view()` だけを返す（正答・根拠・解説・台本・ヒントなし）。音声のみで出される問いの文面も、明示的な補助を記録してから返す。
- 音声ファイルは推測できない ID の URL（`/api/audio/<id>`）でのみ配信し、用途ごとに権限を確認: 本文は自分の解答中／予約済み枠のみ、`feedback`・根拠は提出後のみ、`exam` は開始済みの実力チェックのみ、Unit 単体の `radio` は直接取得不可（学習者が作った番組ファイル経由のみ。作成時に解答アクセスを記録）。
- ストレージ上のファイル名・教材タイトルに答えの情報を含めない（タイトルは lint で検査）。

## 冪等性と同時実行

- 解答提出は 1 トランザクションで回答・採点・観測・復習・日課を反映。同じ attempt_id + 同じ内容は同じ結果を返し、内容が違えば 409。
- XP は `dedupe_key`（例 `unit_base:<user>:<unit>:<学習日>`）の一意制約で重複しない。解説確認は採点済みでなければ失敗するため、順序が逆でも報酬だけ先に確定しない。
- 同じ Unit の同時開始はユーザー行のロックで直列化し、「初見」権は部分一意インデックス（user × stimulus_fingerprint）で一つだけ。
- 再生・補助・ラジオ進捗・自己申告は client の event_id で冪等。
- worker の書き込みは毎回 lease の所有者と世代番号を確認（古い worker は何も確定できない）。
