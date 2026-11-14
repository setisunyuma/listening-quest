# 全モード個別最適化・段階移行・実践課題　対応表と版付き仕様

対象: 「統合実装指示書（2026-11-14）」と、その基本となる「学習効果と品質改善の実装指示書」（R01〜R12・L01〜L04・U01）。
この文書は実装の対応表です。学習効果が実証されたことや、公開アプリがすべての要件を満たすことは保証しません。

## 1. 版付きの仕様（どこに何があるか）

| 仕様 | 版 | ファイル | 由来 |
|---|---|---|---|
| 実践課題 8 種の定義 | `practice/2026-11-v1` | `services/core/lq/curriculum.yaml` | 項目ごとの由来:<br>・目的・場面・ルーブリック・所要時間: アプリ独自の練習設計<br>・`ability_refs`: CEFR CV 2020 と英検 Can-do の参照箇所を指すアプリ内 ID（公式 ID ではない） |
| 段階移行・解放・配分のしきい値 | `progression/2026-11-v1` | `services/core/lq/progression.yaml` | すべてアプリ独自の暫定初期値（6課題・3日・80%、21日、移行期 10 学習日、60/25/15 %）。統計的に校正された合否・CEFR 判定ではない |
| 実践課題のプロファイル | `practice_<段階>/2026-11-v1` | `services/core/lq/profiles/practice_*.json` | current にしない。試験形式・模試・通常の日課は変わらない |
| 共通テストの形式契約 | `common_test/2026_main_r2` | `services/core/lq/profiles/common_test__2026_main_r2.json` | 2026 年本試験の公式問題・スクリプトとの差分（指示書 R06） |
| 英検準1級・1級 | `eiken_pre1/2026_2`、`eiken_1/2026_2` | `services/core/lq/profiles/eiken_*.json` | 問題数・再生回数・10秒・Part 3 の先読み10秒、冊子の分数は指示書 S03〜S08 の照合結果。この作業では原本 PDF の逐語照合をやり直していない（sources に明記） |

判定の版は、判定のたびに記録します（`goal_decisions.version`、`readiness_snapshots.version`、`practice_states.rule_version`、各試行の `curriculum_version`）。
しきい値を後から変えても、過去の判定は書き換わりません。

## 2. 対応表（統合実装指示書）

状態の凡例: **実装済み** = コードと自動テストあり。**未検証** = 実装はあるが実 API・実機・実音声で確認していない。**未実装** = コードなし。

| 章 | 状態 | 根拠（コード / テスト / 画面） |
|---|---|---|
| A01 共通設計の分離 | 実装済み | 次の5つを分離して保存している。<br>・試験トラックと実践トラック: `presentation=practice`<br>・目標モード: `learning_goals`<br>・練習種別: `UnitContent.practice.practice_kind`<br>・学習段階: `practice_states`<br>・評価条件: 試行の `result.practice` と `client_meta.clarifications`<br>ReviewSchedule.stage と難度帯は流用していない。<br>テスト: `test_practice.py::test_v08_v09` |
| E01 英検準1級 | 実装済み | 試験の条件（29問・1回再生・10秒・Part 3 の先読み10秒・設問文の非表示・解答時間で打ち切らない）を `test_e01.py` で確認。<br>実践課題 P1A・P1B: `fixtures/units/practice.yaml`、`test_practice.py`。 |
| E02 英検1級 | 実装済み | 試験の条件: 27問、Part 4 は設問1→10秒→設問2→10秒、3話者の Part 1（`test_e01.py`）。<br>実践課題 P2A・P2B。P2B は暫定回答を後から変更できず、確認行為あり。 |
| C01 C1 | 実装済み | P3A（会議の引継ぎ）、P3B（講義メモ）。<br>既存の C1 独自チェックは「アプリ内実力チェック」のまま。 |
| C02 C2 | 実装済み（P4B は一部未検証） | P4A（複数音声の帰属と未確定）、P4B（含意と根拠）。<br>P4B は語調を実音声で確認していないため、解放の証拠に数えない（V15）。 |
| G01 段階解放 | 実装済み | 純粋関数 `practice_unlock`、状態 `practice_states`。課題を開く経路は3つ:<br>・前提の証拠がそろったとき<br>・現在の主目標の段階の入口の課題であるとき<br>・本人が希望したとき（証拠・XP は付与しない）<br>再ロックしない。教材不足は能力不足と別に表示する。<br>テスト: `test_p01.py`、`test_practice.py` |
| P01 期限のない目標の移行と復習継承 | 実装済み | 純粋関数 `transition_readiness`。決定 `goal_decisions` は本人の選択だけで、再送しても冪等。<br>移行期 10 学習日（60/25/15、5分の日は1種類だけ）。他段階の期限復習を継承し、保留理由を付ける。<br>テスト: `test_p01.py`（V14、V17〜V24）。画面: ホームと成長画面の「現在の主目標」。 |
| U02 今日やることへの統合 | 実装済み | 日課に入るもの:<br>・継承した期限復習<br>・移行期の橋渡し<br>・実践課題（原則1日1つ）<br>・L04 短時間チェック<br>・修復<br>当日の時間は増やさない。「次へ」は課題の種類に応じた画面に進む。<br>テスト: `test_practice.py::test_u02_*`、`test_l04.py` |
| M01 評価とフィードバック | 実装済み | 4つの結果を分ける: 聴解内容（ルーブリック項目）、タスク達成（確認後）、英語表現（採点しない）、判定の信頼性（保留）。<br>重大誤解は相殺しない。日本語と英語で同じ内容なら同じ判定にし、否定を足した回答は不一致にする。<br>成長画面で実践課題を別集計し、「〜を取り出せた（n回・日付）」と表示する。 |
| T01 実装箇所とデータ設計 | 実装済み | 新しいテーブル: `learning_goals`、`goal_decisions`、`readiness_snapshots`、`practice_states`（migration 0008）。エクスポートは v2 で、v1 も読める。<br>生成: `GenSpec.practice_kind`、`build_practice_prompt`、`AuthoredPractice`、`_practice_to_unit`、デモ用の生成、`/api/admin/jobs` での実践課題の指定、開いている課題だけの最小在庫補充。 |
| Q01 教材品質と費用 | 一部実装 | 実装済み:<br>・生成教材であることの表示<br>・ラジオ対象外<br>・無効化した教材を日課・後日確認・解放証拠から外す<br>・解放と有料生成を分離（自動補充は既存の `paid_enabled` と日次上限の内側）<br>未実装:<br>・音声を聞かなくても解けるかの自動検査（作者の申告 `audio_dependency_en` を記録するだけ。ブラインド検品は既存のまま）<br>・語調の実音声検品 |

## 3. 対応表（基本指示書の残り）

| ID | 状態 | 根拠 |
|---|---|---|
| R05 | 実装済み | PR #12（模試の版固定・中断の記録） |
| R06 | 実装済み（1B・第2問の絵の描画は未実装） | PR #13。絵を文章で示す教材は形式調整版として扱い、標準条件の成績に混ぜない |
| R08 | 実装済み（有料の意味監査は未実装） | `notes_errors`、`check_notes`、`update_notes`、`test_r08.py` |
| R09 | 実装済み | `pipeline.step_content`、`test_r09.py` |
| R10 | 実装済み（実音声での誤検出率は未測定） | `pitch.pitch_check`、`voice_config_problems`、`test_r10.py` |
| R11 | 実装済み | `radio._withdrawn`、`rebuild_program`、`test_r11.py` |
| R12 | 実装済み | Realtime の incomplete 応答で usage があれば精算する。`test_r12.py` |
| L04 | 実装済み | `_short_check_slots`、`stats.short_checks`、`test_l04.py` |

## 4. 受け入れテスト V01〜V24

| V | 結果 | テスト |
|---|---|---|
| V01 | pass | `test_e01.py::test_v01_*` |
| V02 | pass | `test_e01.py::test_v02_*` |
| V03 | pass | `test_practice.py::test_v03_*` |
| V04 | pass | `test_practice.py::test_v04_*` |
| V05 | pass | `test_practice.py::test_v05_*` |
| V06 | pass（短答の採点関数。画面の自由入力欄は未実装で、例の教材はすべて選択式） | `test_practice.py::test_v06_*` |
| V07 | pass | `test_practice.py::test_v07_*`、E2E `practice: staged update task, ...` |
| V08 | pass | `test_practice.py::test_v08_v09_*`、`test_l04.py` |
| V09 | pass | `test_practice.py::test_v08_v09_*`、`test_p01.py::test_v18_v20_*` |
| V10 | pass | `test_practice.py::test_v10_v11_*` |
| V11 | pass | 同上、`test_p01.py`（予算の上限） |
| V12 | pass（「次へ」は種類ごとの画面に進む。5分の日は1種類だけで、保留を表示） | `test_p01.py::test_v23_*`、`test_practice.py::test_short_day_*` |
| V13 | pass（決定の再送・確認の再送が二重にならない） | `test_p01.py::test_v18_v20_*`、`test_practice.py::test_v07_*`。オフラインで回答を再送したときの解放イベントは、既存の冪等な仕組みで確認 |
| V14 | pass | `test_p01.py::test_v14_*` |
| V15 | pass（語調） / 一部（音声なしで解けるかの自動検査は未実装） | `test_practice.py::test_v15_*` |
| V16 | pass（費用停止・教材不足でも無断で生成しない。在庫不足は能力不足と別表示） | `test_t01.py::test_restock_*`、`test_l04.py::test_short_check_is_skipped_*` |
| V17〜V24 | pass | `test_p01.py` |

すべて自動テストでの確認です。iPhone 実機（ロック画面、イヤホン、画面オフ、通話割込み）、実録音、実 API の教材は**未確認**です。

## 5. 4モードのデモの範囲

- 英検準1級: 主目標にすると、P1A・P1B が日課に入ります。実践課題→結果→次の課題、の流れを `test_practice.py::test_u02_*` で確認しています。
- 英検1級: E2E で、教材一覧から P2B を開き、暫定回答・確認・結果まで進めることを確認しています。
- C1・C2: 手動開始で、その日の任意枠と入れ替わります（`test_v10_v11`）。成長画面には、できたことと不足している証拠を表示します。
- 教材不足と時間短縮:
  - 未見の教材がないと「教材の準備待ち（能力不足ではありません）」と表示します。
  - 5分の日は1種類だけにします。
  - L04 は在庫不足のとき、理由を付けて見送ります。

## 6. 費用・既存データ・切戻し

- **費用**
  - 今回の作業では有料 API を使っていません。
  - 実践課題の生成は、既存の生成と同じ費用管理の中で行います: 予約→確定・推計・不明、日次・月次・ジョブ上限、`paid_enabled` が OFF なら生成しない。
  - 1件の見積もりの上限は、既存の教材と同じ程度（約 0.10〜0.28 USD）です。実測値は未確認です。
  - 自動補充は、本人が開いた課題で、未見の在庫がゼロのものだけです（1日1件まで）。
- **既存データ**
  - 既存の教材・履歴・音声・設定は削除していません。
  - 旧版の教材は、現行の契約で無料の再検査をして、通ったものだけを模試に使います。教材は書き換えません。
  - 学習メモの修正は content hash を変えないので、採点履歴・音声には影響しません。
- **切戻し**
  - 各 PR を revert します。
  - migration 0008 を戻すと、目標と解放のテーブルだけが削除されます。既存のテーブルは変わりません。
  - プロファイル r2 を戻すと、旧版 `2026_main` が current に戻ります。

## 7. まだ行っていないこと（オーナーの確認が必要）

- 実 API での実践課題の生成と品質確認。費用がかかるため、予算の承認が必要です。
- iPhone 実機での確認。
- 語調の実音声検品。
- 1B・第2問の絵の描画。
