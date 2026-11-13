# 教材パイプライン

## 状態

教材（Unit）: `DRAFT → CONTENT_REVIEW → AUDIO_PENDING → AUDIO_GENERATING → AUDIO_QA → READY`、不適格は `QUARANTINED`、実行失敗は `FAILED`。
ジョブ: `QUEUED / RUNNING / BLOCKED / RETRY_WAIT / FAILED / SUCCEEDED` と、停止理由（`blocked_reason`・`stop_detail`）・最後に完了した工程。
予算待ち（`paid_disabled`・`budget`・`job_cap`）や承認待ち（`awaiting_content_review`・`awaiting_audio_approval`）は BLOCKED で、失敗にも完了にも見せません。
学習画面が候補に使うのは READY だけです。

## 工程（`lq/jobs/pipeline.py`）

1. **内容生成**（生成ジョブのみ）: `templates.yaml` のモード・形式別テンプレートで、Responses API の構造化出力（strict JSON Schema）。
   形式に関わる値（規定回数・設問の媒体とタイミング・採点グループ）は生成モデルではなくプロファイルから入れます。スキーマ違反は3回まで再生成。
   外部 API に送るのはモード・形式・技能・難度帯・話題の条件だけで、学習者の回答や履歴は送りません。
2. **形式チェック**（`authoring.lint_unit`）: 設問数・区間数・話者数・規定回数・問いの媒体・誤答理由・日本語解説・タイトルに答えが出ていないか・図表統合問題が音声と図表の両方を根拠に持つか・採点グループ。違反は隔離。
3. **ブラインド検品**: 検品側には `reviewer_view()`（学習者が見聞きする情報だけ。正答・根拠・解説・タイトル・技能タグなし）を渡して解かせ、作者の正答と照合。
   不一致・「曖昧」・無回答は隔離。結果は `content_hash` 付きで記録し、同じハッシュなら再検品を省略、意味のある変更があれば再検品。
   同じモデルを別呼び出しで使っても `api_reviewed` であり、人間の独立検品とは表示しません。
4. **音声**: 台本（`render.plan_clips`）を話者ごとの固定の声で合成。完成し検査を通ったクリップだけを atomic にキャッシュへ保存し、再開時は再利用。
   キャッシュキー = 台本文・provider・model・voice・speaker・言語・指示・音声設定・実装バージョン。
   PCM（24kHz/16bit/mono）で組み立て、無音は明示的にゼロ値を挿入。2回読みの箇所や解説中の根拠は同じクリップを再利用。根拠は発話単位で再生（推測の時刻で切り出さない）。
   出力トラック: `stimulus:<区間>`（回答前に聞いてよい本文と音声のみの問い）、`exam`（本番提示: 事前確認・規定回数・解答待ち）、`feedback`（正答・根拠・日本語解説）、
   `radio`（事前説明→設問と選択肢→本文（規定回数）→設問と選択肢→無音の解答待ち→全問後に正答・根拠・解説）、`utt:<発話>`。図表は文章化できる場合だけ radio を作り（音声向けアレンジとして記録）、できない Unit はラジオ非対応。
   各ファイルはストレージへ保存した後、再デコード・長さ・無音・クリッピングを確認。
5. **音声確認**: 英語クリップを独立した音声認識で書き起こし、台本と比較。表記の違い（数字の書き方・短縮形・句読点）は許し、数字と否定語の違い・脱落は必ず不合格。根拠の発話は基準を厳しく。
   音声合成 API が返す書き起こしは使いません。ASR が無い場合や旧形式の完成音声は「音声の手動確認待ち」。
6. **READY**: ストレージ上の全オブジェクトの存在・サイズ・ハッシュと必要な音声の充足を確認してから、DB トランザクションで READY。DB とストレージは一つのトランザクションではないため、途中失敗で残ったオブジェクトは `lq gc` で回収します。

## provider（`lq/generator`）

| 工程 | 実 API（OpenAI） | デモ |
|---|---|---|
| 内容生成 | `POST /v1/responses`、`text.format = json_schema (strict)` | 手続き生成（台本中の事実が正答を決める） |
| 検品 | 同上（別呼び出し・学習者視点の入力のみ） | 模擬（デモ生成器の答えを別保存から引く）→ `demo_mock_reviewed` |
| 音声 | Realtime API: `wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini`。`session.update`（音声出力・pcm 24kHz・voice・turn_detection なし）→ `conversation.item.create` → `response.create`、`response.output_audio.delta` を集めて `response.done` を待つ。発話ごとに接続（voice は最初の音声後に変えられないため） | トーン（音声動作確認用） |
| 旧 TTS | `POST /v1/audio/speech`（`gpt-4o-mini-tts` 等。2027-01-06 終了予定。`LQ_TTS_PROVIDER=openai_speech` で選択可） | — |
| 音声認識 | `POST /v1/audio/transcriptions`（`gpt-transcribe`）。台本はヒントとして渡さない | 模擬（台本を返す）→ `audio_demo_mock_checked` |

仕様の確認日: 2026-11-12（developers.openai.com の text-to-speech / realtime-conversations / pricing / deprecations）。

## 予算（`generator/budget.py`）

- 有料利用は初期状態 OFF。キーがあるだけでは始まらない。教材管理で有効化した後は、その上限内の自動補充に毎回の承認は不要。
- すべての有料リクエスト（内容・検品・音声・音声認識・再試行）は開始前に見積額を **予約** し、日次予算（UTC 日）・1ジョブ上限に収まらなければ開始しない。
  返ってきた usage で確定、計算できなければ「不明」、タイムアウト等で課金有無が分からないものは見積額のまま計上（課金されていないと推測しない）。送信前に失敗したものだけ返却。
- 単価が `prices.yaml` に無いモデルは 0 円扱いにせず実行を拒否。単価表に出典と確認日を記録。
- 同時実行数の上限（初期1）。アプリの上限は新規開始を止めるもので、請求額の絶対保証ではありません。

## 自動補充（`services/replenish.py`）

worker が定期的に実行（解答のたびではない）。対象は利用中のモード（または設定したモード）だけ。未接触在庫の推定学習時間が1日分を下回ったら3日分まで、
在庫の少ない形式と弱点・未測定の技能を優先して生成ジョブを作る。生成中のジョブは在庫に数え、1日の作成数に上限。

## 取り込み（`services/importer.py`）

- 入口: 教材管理の画面、`POST /api/admin/import`（ログイン）、`POST /api/import`（Bearer `LQ_IMPORT_TOKEN`、CLI 用）、`lq import`。どれも同じ関数。
- manifest の `files` に名前・SHA-256 を書き、アップロードされたファイルと照合。不足・余分・ハッシュ不一致・不正なファイル名は拒否。サーバー上のパスや URL からは読まない。各ファイル 60MB まで。
- `(source, content_id, revision)` で冪等。同じ版の再送は「登録済み」、内容が違えば競合（新しい revision として送る）。
- schema_version 1（以前のラジオ生成器）は adapter で変換。正答入りの完成音声はラジオ専用 Unit に。根拠行などが無い設問は変換せず理由を報告（勝手に補わない）。
- JSON 内の「passed」等は承認として扱わない。
- 生成器 CLI は `LQ_SERVER_URL` があれば生成成功後に自動送信し、失敗したら `var/outbox/` に残す（`lq outbox list / flush`）。
