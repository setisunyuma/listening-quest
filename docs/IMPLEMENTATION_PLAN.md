# 実装計画と受入条件（完了状況）

状態: `[x]` 完了 / `[~]` 一部 / `[ ]` 未実施。

## 作業順

| # | 作業 | 受入条件 | 状態 |
|---|------|----------|------|
| 1 | 土台: モノレポ、Python パッケージ `lq`、設定、DB モデル、Alembic | 空 DB で `lq migrate`、モデルと migration の差分なし（テストで確認） | [x] |
| 2 | 教材スキーマ・版管理・content_hash・Learner DTO・モードプロファイル | 根拠参照の検証、ハッシュ循環なし、DTO に正答なし | [x] |
| 3 | 学習ロジック（純粋関数）: 初見判定、難度、復習、推薦、日課、XP、学習日 | 受入テスト 3〜11 | [x] |
| 4 | ストレージ、音声レンダリング（stimulus/exam/feedback/radio）、キャッシュ、音声 QA | 受入テスト 12, 13 | [x] |
| 5 | provider interface（demo / OpenAI）、生成→ブラインド検品→音声→READY、予算 | 受入テスト 14, 17。OpenAI 実 API は未実行 | [~] |
| 6 | ジョブ表と worker、自動補充 | 受入テスト 12, 17 | [x] |
| 7 | サービス層: 取り込み、解答、採点、復習、日課、ラジオ、模試、無効化、書き出し/復元 | 受入テスト 1〜4, 15, 16, 18, 19 | [x] |
| 8 | FastAPI、OpenAPI 出力と TS 型生成、CLI（doctor/validate/plan/generate/review/approve/render/build/import/demo/outbox …） | 契約チェックが CI で失敗を検出 | [x] |
| 9 | 例題 24 件（5 モード）と demo provider、冪等 seed | seed 2 回目で重複なし、demo/live 分離 | [x] |
| 10 | Web（Next.js PWA）: ホーム/プレイヤー/教材/ラジオ/成長/履歴/設定/教材管理/実力チェック、SW、IndexedDB | 受入テスト 16, 20 | [x] |
| 11 | Playwright、CI、Compose、Caddy、ドキュメント、バックアップ | README のコマンドを実行して確認（Docker は下記の理由で未実行） | [~] |

## 受入テスト対応表（仕様 21 章）

| # | 内容 | テスト |
|---|------|--------|
| 1 | 生成/取り込み → READY → 一覧反映 → 再生 → 解答 → 採点 → 出題変更 | `services/core/tests/test_api.py::test_full_flow_and_answer_locks`、`test_pipeline.py::test_generate_job_reaches_ready_with_all_tracks`、`test_import_with_audio_*`、e2e `flows.spec.ts` quest / generation |
| 2 | 提出前に正答・根拠・feedback 不可、補助は先に記録、assessment は全提出まで不可、radio は解答アクセスを記録 | `test_api.py::test_full_flow_and_answer_locks`、`test_aid_is_recorded_before_text_*`、`test_full_mock_is_locked_*`、`test_radio_path_*` |
| 3 | ラジオ・答え入り教材への接触後は初見に数えない | `test_learning.py::test_radio_access_disqualifies_first_attempt`、`test_answer_access_from_another_session_during_attempt` |
| 4 | 再送・二重タップ・複数タブで 1 回だけ更新 | `test_learning.py::test_resubmit_and_ack_apply_once`、`test_two_tabs_*`、`test_play_event_resend_counts_once`、`test_ack_before_submit_cannot_pay_out` |
| 5 | 誤答続き/正解続きで固定 seed の候補・難度が規則どおり変わる | `test_learning.py::test_wrong_answer_changes_next_slot_*`、`test_recommendation_is_deterministic_*`、`test_band_*` |
| 6 | 少数・自己申告・補助付き・ラジオだけで難度が上がらない | `test_learning.py::test_self_report_assist_radio_and_repeats_never_raise_band`、`test_band_rule_pure` |
| 7 | 規定 2 回と 3 回目、速度変更、同じ音声の別設問 | `test_learning.py::test_two_official_plays_ok_third_is_assistance`、`test_same_audio_other_questions_is_not_first` |
| 8 | 5 モードの履歴・難度が混線せず日次予算は共有 | `test_learning.py::test_modes_do_not_mix_and_share_one_plan` |
| 9 | 期限前の反復は段階を進めない、誤答は翌日、遅延後の自力正解で次段階 | `test_learning.py::test_srs_pure_rules`、`test_review_flow_in_service` |
| 10 | 04:00・TZ 変更・端末時計・翌日同期で重複なし | `test_learning.py::test_learning_day_boundary`、`test_daily_reward_once_across_day_change_timezone_and_late_sync` |
| 11 | 新教材や誤答で枠数・時間予算が増えない | `test_learning.py::test_plan_size_is_fixed` |
| 12 | 音声生成・アップロード途中の障害で READY にならず、再起動・再取り込みで重複しない | `test_pipeline.py::test_audio_failure_*`、`test_upload_failure_is_not_ready`、`test_dead_worker_is_reclaimed_*`、`test_retry_is_bounded`、`test_import_*_idempotent` |
| 13 | キャッシュが台本・声・model の変更を検知、未完成音声を使わない、ハッシュ循環なし | `test_pipeline.py::test_cache_key_tracks_every_synthesis_input`、`test_cache_never_serves_*`、`test_content_hash_covers_meaning_only`、`test_review_record_only_counts_for_the_same_hash` |
| 14 | 検品モデルに作者の正答が渡らず、不一致を隔離 | `test_pipeline.py::test_reviewer_never_sees_the_key_and_mismatch_is_quarantined` |
| 15 | 共通テスト 2026 の満点 100、18〜21・28〜29・30〜31 の部分正解は 0 点 | `test_api.py::test_common_test_2026_scoring`、`test_full_mock_is_locked_*` |
| 16 | オフライン回答の保存・再起動・同期、採点前に採点済み表示しない、予約枠が別タブで差し替わらない | `test_api.py::test_offline_package_*`、`test_learning.py::test_reserved_slot_keeps_its_unit_*`、e2e `flows.spec.ts` offline |
| 17 | 予算・同時実行上限が全工程と retry に適用、テストが有料 API を呼ばない | `test_pipeline.py::test_paid_generation_is_off_by_default_*`、`test_daily_budget_spans_all_steps_and_retries`、`test_per_job_cap_*`、`test_max_concurrent_jobs`、`test_openai_adapters_reserve_before_*`（conftest で HTTP/WebSocket を遮断） |
| 18 | 無効化で再計算し履歴を残す | `test_api.py::test_invalidation_recomputes_but_keeps_history` |
| 19 | 書き出し・復元・migration が既存データを保全、復元を繰り返しても重複しない | `test_api.py::test_export_restore_*`、`test_migrations_keep_existing_data`、`scripts/backup.sh`・`restore.sh` の手動往復確認 |
| 20 | Playwright で主要画面・モード切替・解答・再生失敗・空状態・生成失敗・スマホ幅 | `apps/web/e2e/smoke.spec.ts`、`flows.spec.ts`（iPhone 14 幅 + デスクトップ） |

## 実測した検証結果（2026-11-13、この開発コンテナ）

| 項目 | 結果 |
|---|---|
| `ruff check` / `ruff format --check` | 通過 |
| `pytest`（services/core） | **63 passed**（PostgreSQL 16 実 DB、FFmpeg 実エンコード、有料 API なし） |
| `npm run lint` / `typecheck` / `contracts:check` / `build` | 通過 |
| Playwright（`npm run e2e`） | **14 passed**（phone 11 / desktop 3）。実 API サーバー + worker + Next.js 本番ビルド、デモ領域 |
| `lq demo` | 89 Unit が demo provider で READY（例題 24 + 手続き生成 + 実力チェック用） |
| `scripts/backup.sh` → 空 DB へ `restore.sh` | 件数一致、既存データへの上書き拒否、改ざん検出を確認 |
| CLI の自動送信 | 送信失敗で outbox に保存 → `lq outbox flush` で送信（手動 + テスト） |

### 実行できていないもの

- **OpenAI 実 API**（内容生成・検品・Realtime 音声・音声認識）: API キーと予算の指示がないため未実行。アダプターは公式ドキュメント（2026-11-12 確認）に基づいて実装し、テストでは通信を遮断して予算の予約までを確認。実音声の品質・Realtime の実際のイベント形式・usage 形式は未検証。
- **Docker Compose / Caddy**: このコンテナでは Docker デーモンが使えないため未起動（設定ファイルのみ）。ローカルの PostgreSQL + ファイルストレージで同じコードを検証。MinIO（S3）経路は未実行。
- **iPhone 実機**: 未実施（README の確認項目）。Playwright の結果は Chromium のスマホ幅エミュレーションであり、ロック画面・Bluetooth・通話割り込みの確認ではない。
- **公式資料との逐語照合**: 共通テスト 2026・英検 2026 年度第 2 回の冊子・原稿との照合は未実施（形式・配点は仕様書の値）。
- **外部公開**: 未実施（docs/DEPLOYMENT.md）。
