# ベンチマークと評価指標の運用方針（benchmark governance）

検索・回答の変更を「数字で採否を決める」ための指標・データ・手順の取り決め。数値の測り方そのもの（eval.bench の
使い方）は `docs/operations/retrieval_benchmarking.md`、変換精度は `docs/operations/conversion_accuracy.md` を参照。

## 1. 何を「主指標」とするか

1 つの変更について報告するのは下の表の指標だけにする（他の数値は参考値）。主指標は 1 つ、ガードレールは「悪化させない」ことだけを見る。

### 検索（eval.bench / 一般ベンチマーク / テナント評価の gold ID 付きケース）

| 役割 | 指標 | 使い方 |
|---|---|---|
| **主指標** | **nDCG@10** | 上位 10 件の並びの良さ。採否はこの差（候補 − 基準）の対応のある cluster bootstrap 95% 区間で決める |
| 候補網羅（再ランクの前提） | Recall@20 | 再ランクに渡す候補に正解が入っている割合。これが低いと再ランクでは取り戻せない。再ランク導入・深さ変更の前提条件（Recall@20 が基準より有意に下がる変更は再ランクの評価に進めない） |
| 最初の 1 件 | MRR@10 / hit@1 | 利用者が最初に見る 1 件。hit@1 は McNemar（対応のある 2 値）で「有意に悪化していない」ことを確認 |
| 運用 | 段階別 p95 レイテンシ（embed / dense / lexical / fusion / rerank / total） | total p95 が予算内。再ランクは段階別 p95 の増分も記録する |

eval.bench の CI 対象（`ci_metrics`）には必ず `ndcg@10, recall@20, mrr@10, hit@1` を入れる（`configs/bench/general_v1.json`）。

### 回答（テナント評価 `eval/tenant_eval`）

| 役割 | 指標 | 定義・条件 |
|---|---|---|
| **主指標** | **回答正解率** | ゴールデンケースの合格率。語の検査（`golden_qa`）または LLM 採点（`llm_judge`）。LLM 採点は人の採点との一致が **50 件以上・Cohen κ ≥ 0.6・誤合格率上限 ≤ 10%** を満たすテナントでだけ使う（`llm_judge.usable`） |
| 出典 | citation precision | 引用のうち gold チャンク（gold ID が文書単位なら gold 文書）を指す割合（`runner.citation_precision`、行に `cited` がある場合） |
| 棄権 | abstention precision / recall | `should_abstain` ラベル × 実際に棄権したか の混同行列（`metrics.abstention_confusion`）。正例 = 棄権。recall = 棄権すべきケースのうち棄権した割合、precision = 棄権のうち棄権すべきだった割合 |
| **ガードレール** | 誤受理率の上限 | 回答したのに不合格の割合の Clopper-Pearson 片側 95% 上限。既定上限 1%（`gate.Policy.false_accept_limit`）。0 件で 1% を示すには 299 件必要 |
| **ガードレール** | 応答時間 p95 | 端から端まで（`latency_p95_ms`）と段階別（`stage_latency_ms`）。予算は `gate.Policy.latency_p95_budget_ms` |

### 主観と数値を 1 つにまとめる指標

**推奨: 顧客採用正解率（customer-accepted correct answer rate）**

> レビュー対象ケースのうち「数値判定で正解」**かつ**「顧客レビュー担当者が採用（accept）」したケースの割合。Clopper-Pearson 両側 95% 区間付きで報告する。

- 理由: 正解率だけでは「語は合っているが顧客には使えない回答」を合格させ、主観評価（1-5）だけでは評価者ごとの甘辛と
  件数不足でぶれる。両方を満たしたケースだけを数えると、どちらか一方の甘さで数字が上がらない（AND なので上振れしない）。
- 1-5 の平均評価は分布（ヒストグラム）と並べて参考値として出す。平均は採否に使わない（順序尺度のため）。
- 「数値は不合格だが顧客は採用」「数値は合格だが顧客は不採用」の件数を必ず併記する。前者が多ければ判定語（expected_all 等）や
  LLM 採点基準が厳しすぎ、後者が多ければ甘すぎる。ゴールデンケースの見直し材料にする。
- ガードレール（誤受理率上限・p95）はこの指標とは別に必ず満たす。
- 実装: `eval/tenant_eval/customer_review.py`（`aggregate_reviews` の `customer_accepted_correct_rate`）、
  レポート節「顧客の主観評価（customer subjective）」（`report.to_markdown(customer_review=..., feedback=...)`）。

## 2. 一般ベンチマーク general_v1（凍結）

| 項目 | 内容 |
|---|---|
| マニフェスト | `eval/bench_data/general_v1/MANIFEST.json`（名前・版・seed・入力の sha256・出力の期待 sha256・ライセンス・取得方法） |
| 構成 | JQaRA dev から 500 問（候補パッセージの和集合をコーパスにする＝開放コーパス化）、JaGovFaqs-22k から 5,000 文書・500 問、合成マルチフォーマット 12 問 |
| ライセンス | JQaRA: CC-BY-SA-4.0、JaGovFaqs-22k: CC-BY-4.0。データ本体はコミットしない（`data/` は git 管理外）。集計値のみ報告に載せる |
| 設定 | `configs/bench/general_v1.json`（seed、ks、4 モード: dense_only / lexical_only(BM25, Sudachi) / hybrid weighted_rrf(k=60) / hybrid + cross-encoder(深さ 30)。モデルは revision 固定） |
| 実行 | `scripts/run_general_benchmark.py`。入力・出力の sha256 が違う、またはモデル revision が未固定なら**実行を拒否**する |
| 出力 | `reports/bench/general_v1/<UTC YYYYmmddTHHMMSSZ>_<commit7>_<設定sha8>_<runid6>.md` と `.json`（O_EXCL で作成し上書きしない。`index.json` に追記され、最新は index から引く）（git commit、dirty か、データ sha256、設定 sha256、モード別の指標と 95% 区間、ソース別 nDCG@10、ハイブリッドとの対応比較、段階別 p50/p95） |
| 動作確認 | `eval/bench_data/general_v1/smoke_v2/`（合成の極小データ）と `configs/bench/general_v1_smoke_v2.json`。テストはスタブのモデルで端から端まで実行する（`tests/test_benchmark_governance_20261122.py`） |

旧 smoke v1 (1.0.0) は固定 SHA256 の入力3件が欠落しており、復元できていないため実行対象外とする。旧マニフェスト・設定は履歴として変更せず保持する。smoke v2 (2.0.0) は新規の架空 FAQ 12件と既存合成ケースの明示的な部分集合（回答可能5件・棄権1件、5チャンク）を使用する。FAQ は10問を抽出し、合計16問・17チャンクとなる。入力と出力の SHA256 を固定する。旧版の復元や精度改善の証拠ではなく、実モデルなしのパイプライン動作確認専用。実測用 general_v1 のデータ・設定・昇格基準は変更しない。

### 測定 PC での手順

```bash
# 0) モデルとデータの revision を決めて設定・マニフェストの REPLACE_WITH_... を置き換える（コミットする）
# 1) データ取得（リポジトリ内 data/external/ に置く。マニフェストの fetch.command 参照）
huggingface-cli download hotchpotch/JQaRA --repo-type dataset --revision <sha> --local-dir data/external/jqara
huggingface-cli download matsuxr/JaGovFaqs-22k --repo-type dataset --revision <sha> --local-dir data/external/jagovfaqs
#    MANIFEST.json の sources.jqara_dev.input を取得した dev の parquet のパスに合わせる
# 2) 初回だけ: 構築して sha256 を固定（表示された構成を確認してから MANIFEST.json をコミット）
.venv/bin/python scripts/run_general_benchmark.py build --pin
# 3) 毎回: 構築・検証・実行・報告
HF_HOME=$PWD/data/external/hf_cache .venv/bin/python scripts/run_general_benchmark.py run
#    再構築を省く場合: run --skip-build（構築済みファイルの sha256 を検証する）
```

`--pin` は初回専用。固定後に入力データが変わったら（データセットの更新など）新しい版 `general_v2` を作る。general_v1 の
数字同士だけを比較し、版をまたいで比較しない。

### CI ゲートとの関係

`scripts/eval_regression_gate.py` は 20 件・スタブベクトルの回帰検出専用で、基準値（`eval/baselines/retrieval_gate_baseline_v2.json`）は
すべて 1.0 に張り付いている。改善の証明には使えない（精度の数字は一般ベンチマークとテナント評価で出す）。

## 3. 再ランク（retrieval 変更）の昇格ゲート

`eval/rerank_promotion_gate.py`（既定 = 統計ゲート）。compare_runs の出力を読み、次をすべて満たすときだけ `promote_candidate`（終了コード 0）:

1. 2 つの実行が同じ評価セット・コーパス・split・照合方式・埋め込みプロファイル（`--require-same-conditions`）
2. 対応のある回答可能クエリが **100 件以上**
3. ΔnDCG@10 の 95% 区間の**下限 > 0**
4. hit@1 の McNemar で候補が有意に悪化していない（α = 0.05）
5. 候補の total p95 が予算内（`--max-p95-ms`、基準からの増分 `--max-p95-increase-ms`）。予算未設定はブロック

```bash
python -m eval.bench.compare_runs --baseline-run runs/bench/general_v1/<A> --baseline-config <hybrid id> \
    --candidate-run runs/bench/general_v1/<A> --candidate-config <hybrid+ce id> --metric ndcg@10 \
    --require-same-conditions --out artifacts/eval/cmp_ndcg.json
python -m eval.bench.compare_runs ... --metric hit@1 --require-same-conditions --out artifacts/eval/cmp_hit1.json
python -m eval.rerank_promotion_gate --compare-ndcg artifacts/eval/cmp_ndcg.json --compare-hit1 artifacts/eval/cmp_hit1.json \
    --max-p95-ms 1500 --output artifacts/eval/rerank_promotion_decision.json
# または run ディレクトリを直接渡す（内部で compare_runs を 2 回呼ぶ）
python -m eval.rerank_promotion_gate --baseline-run <A> --baseline-config <id> --candidate-run <A> --candidate-config <id> --max-p95-ms 1500
```

旧ゲート（feedback_preview の top1/3/5 件数比較、既定 1 件・回帰 1 件まで許容）は統計的な意味がないため
`--legacy-feedback-preview` を付けたときだけ動く。再ランクの昇格判断には使わない。

## 4. テナントごとの評価手順

1. **評価セットを凍結する**: テナントのゴールデンケース（`golden_qa.jsonl`）を fold に分け（`golden_qa.split_by_fold`）、
   調整に使わない fold を判定用にする。変更のきっかけになったケースは除く（`exclude_motivating`）。評価セットの sha256 が台帳に残り、
   同じセットの使用回数に予算がある（`ledger.check_budget`）。ケースには可能なら `gold_chunk_ids` / `gold_doc_ids` と `should_abstain` を付ける。
2. **基準を測る**: 現行設定で実行（`scripts/tenant_eval.py evaluate`）。
3. **変更を宣言する**: プロファイル版・ノブ値など変える項目を先に宣言する。宣言外の条件差があれば実行を拒否する（`conditions.assert_declared_changes`）。
4. **対応比較**: 同じケースで基準と候補を実行し、
   - 正解率: cluster bootstrap 区間で非劣性（許容低下 2 点）、
   - ケース単位の合否: 質問タイプ別 McNemar（Holm 補正）、
   - 検索: gold ID のあるケースの nDCG@10 の対応比較（非劣性、`retrieval_ndcg`）、
   - 誤受理率上限・新規エラー・p95 予算（`latency_p95`）。
5. **保護ケースの非劣性**: 優先度 100 以上で基準が合格していたケースは 1 件も落としてはならない（`protected_cases`）。
6. **顧客レビュー**: 改善・悪化したケース（＋変化なしの無作為抽出、`--sample-unchanged`）を CSV で渡し、各行に評価 1-5 と
   accept / reject を記入してもらう。
   ```bash
   python -m eval.tenant_eval.customer_review export --evaluation <evaluation.json> --out <テナント評価フォルダ>/review.csv --sample-unchanged 20
   python -m eval.tenant_eval.customer_review import --evaluation <evaluation.json> --csv <返送された review.csv> --out <...>/review_aggregate.json
   ```
   CSV は UTF-8（BOM 付き）、数式として実行されうるセル（= + - @ 等）は先頭に `'` を付けて出力する。質問と回答を含むため
   テナントの評価フォルダ内だけで扱い、承認後は `即削除/` に移す。
7. **承認記録（sign-off）**: レポート（Markdown）に判定・チェック・顧客の主観評価（顧客採用正解率と区間、採用率、評価分布、
   チャットのフィードバック集計 `feedback.aggregate`）を載せ、承認者・日付・評価セット sha256・プロファイル版・台帳の run_id を
   記録する（台帳 `eval/tenant_eval/ledger.py` に判定が残る。顧客の承認は proposals のレビュー記録に残す）。

### 件数の目安

- 正解率の非劣性判定: 30 件以上（既定 `min_cases`）。95% 区間の幅 ±3 点を狙うなら約 400 件（`python -m eval.bench samplesize`）。
- 誤受理率 1% 以下の証明: 不合格 0 件なら 299 件以上。
- 再ランク昇格: 100 件以上（それ未満は「判定不能」）。
- 顧客レビュー: 採用率の 95% 区間を ±10 点にするには約 100 件。改善・悪化だけで足りなければ変化なしを抽出して足す。
