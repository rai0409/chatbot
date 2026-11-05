# テナント評価コア（`eval/tenant_eval/`）

提案された変更（プロファイルの新しい版、ノブの値）を、顧客のゴールデン QA で「採用してよいか」判定する部品。
UI・API・承認の流れは含まない（承認は 2-6、条件の記録とレポートは 2-5）。

## 部品

| 部品 | 役割 |
|---|---|
| `stats.py` | Clopper–Pearson の片側上限/下限（scipy 不要）、三の規則、「上限を示すのに必要な件数」、BH 法。Holm・McNemar・ペアのブートストラップ・PSI・KS は `eval.bench.stats` を使う |
| `metrics.py` | 合格率、回答/棄権/エラー、**誤受理率**（回答したが期待を満たさない）とその上限、遅延、スライス別 |
| `runner.py` | `run_cases(cases, answer_fn)`: 1 ケース 1 行。回答関数が例外でも `error` 行にして続行。`pipeline_answer_fn()` が実パイプライン |
| `gate.py` | `compare(基準の行, 候補の行, Policy)`: `pass` / `fail` / `insufficient` と全チェック |
| `ledger.py` | ハッシュ連鎖つき追記専用 JSONL と、評価セットごとの**評価回数予算**（既定 20） |
| `evaluate.py` | `evaluate_candidate()`: 予算確認 → 基準と候補を実行 → 判定 → 台帳に記録 |

## ゴールデン QA の出どころ（`scripts/tenant_eval.py evaluate`）

評価が使う質問票は、`--golden-source` で選べます。

| 値 | 読むもの |
|---|---|
| `profile`（既定） | プロファイルに入っている `golden_qa`（従来どおり） |
| `console` | 管理コンソールの「ゴールデン QA」で作った質問票（`--golden-tenant` で別のテナントのものも選べる。既定は `--tenant`） |
| `both` | 両方。**同じ `case_id` が両方にあれば、黙って片方を選ばず、問題として止まる** |

無効にした質問は評価されません。結果の JSON に、どれを使ったか（`golden_source`）が入ります。

## ゲートのチェック（`Policy` の初期値は実データで調整）

1. **全体の非劣性**: 合格率が `margin`（0.02）より悪化していない。ペアのブートストラップ（ケースの `group` 単位でクラスタ）の
   下側限界（片側 95 %）で判定。`min_cases`（30）未満なら `insufficient`。
2. **スライス（質問タイプ）別の悪化**: `min_slice`（30）件以上のスライスだけを、厳密 McNemar（悪化側のみ）＋ Holm 補正で判定。
   足りないスライスは「判定せず」と記録する。
3. **保護ケース**: `priority >= 100` で基準が合格しているケースは候補でも合格すること。
4. **誤受理**: 候補の誤受理率の上限（Clopper–Pearson、片側 95 %）が `false_accept_limit`（1 %）以下なら `pass`。
   上限を示せなくても件数が基準より増えていなければ `insufficient`、増えていれば `fail`。
   0 件で 1 % 未満を示すには **299 件**要る（`min_cases_for_zero_failures(0.01)`）。ゴールデン QA が数件のうちは
   このチェックは `insufficient` になる。それが正しい。
5. **新規エラー**: 基準では出ず候補で出た `error` 行がない。

## 使い方の注意

- 評価に使うケースは、調整に使っていない fold（`golden_qa.split_by_fold`）から選び、その変更のために書いたケース
  （`golden_qa.exclude_motivating`）を除く。
- **評価回数予算**: 同じ評価セットで変更を選び続けるほど、そのセットへの過適合が進む。予算を使い切ったら、新しい
  ケースを書く。台帳は `verify_chain()` で改ざん・欠落を検出できるが、ローカルファイルであり監査ログではない。
- 候補のプロファイル版を有効化せずに評価するには `profile_store.override_version(profile_id, version)` を使う
  （そのプロセス内だけ。ポインタ・他プロセスは変わらない）。ノブの値は `knob_registry.check_change()` を通してから
  `config` に設定した状態で、候補の回答関数を作る。

## 条件の記録と比較（`conditions.py`、2-5）

- `collect(profile_id)`: プロファイル版のハッシュ（`profile_version`）、インデックスのコレクションと世代、登録済みノブの現在値。
- `assert_declared_changes(基準, 候補, 宣言)`: 基準と候補の違いが**宣言した変更だけ**であることを確認する。
  評価の間にインデックスが切り替わった・宣言していないノブが違う、宣言した変更が候補で効いていない、
  基準と同じ値を「変更」と宣言した、のいずれも `ConditionsError`（台帳の予算を消費する前に拒否）。
- `candidate_environment(profile_id, version=, knobs=)`: そのプロセス内だけ、保存済みの版を配信し、検証済みのノブ値を
  `config` に設定して、終わると戻す。評価できるのは `config.NAME` を呼び出し時に読む設定（登録済みノブは
  すべてそう）。環境変数・再起動が必要な設定は対象外。
- `evaluate_candidate(candidate_context=...)`: 候補の実行をそのコンテキストの中で行う。

## レポートとコマンド（`report.py`、`scripts/tenant_eval.py`、2-5）

```
python scripts/tenant_eval.py evaluate --profile ID --proposal 名前 \
    [--candidate-version 版] [--knob NAME=値 ...] [--folds K --held-out N --seed S] \
    [--exclude-change 変更ID] [--budget 20] [--report runs/tenant_eval/reports/名前.md]
python scripts/tenant_eval.py ledger      # 台帳の連鎖検証と一覧
```

終了コード: 0 合格 / 1 不合格 / 2 判定不能 / 3 拒否（予算・条件・入力）。
レポート（Markdown）は、判定、基準と候補の結果、比較した条件（プロファイル版を含む）、各チェック、質問タイプ別、
悪化/改善したケースと理由、基準値を載せる。実行には実インデックスと埋め込みモデルが要る（テストはすべて偽の回答関数）。
