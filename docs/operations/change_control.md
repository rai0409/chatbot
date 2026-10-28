# 変更管理（提案の状態機械と承認）

改善の提案を、観測から廃止までの決まった順序で進め、各段階の条件をコードで強制する
（`eval/tenant_eval/proposals.py`）。記録と規則だけで、画面・通知・影子/カナリアのトラフィック分割は含まない。

```
observed → classified → proposed → evaluated → approved → shadow → canary → live → monitoring → retired
                                     ↑           ↓
                                     └─ 改訂 ─────┘     rejected: approved より前のどこからでも
```

| 移動 | 条件（呼び出し側の申告ではなく、コードが確認する） |
|---|---|
| `classify` | カテゴリ: `synonym` `boost_rule` `validation_rule`（プロファイル版）/ `guard_threshold` `retrieval_setting`（ノブ）/ `ingest_gap` `stale_content` `other`（手作業） |
| `propose` | 変更 = 保存済みのプロファイル版（実在・整合性確認）と/または登録済みノブ値（`knob_registry`）。カテゴリと合っていること。手作業カテゴリは `{"manual": "内容"}` のみで、ここでは評価・承認できず、完了したら `retire` で閉じる |
| `attach_evaluation` | 台帳（連鎖検証つき）の、**この提案の**エントリで、評価した変更（`declared`）が提案の変更と**完全に一致**すること。別の変更の評価では通らない |
| `approve` | 承認者は提案者本人・既承認者ではない。評価が `fail` なら承認できない。`insufficient` は理由の記述が必須。**距離ガード（高リスクノブ）を含む変更は 2 人の承認**が要る。必要数に達すると `approved` |
| `advance` | `approved → shadow → canary → live → monitoring` を 1 段ずつ、実行者と根拠（文章）つき |
| `reject` / `retire` | 却下は承認前、廃止は承認後（中止・巻き戻しを含む） |

- すべての移動は履歴（誰が・いつ・どこから・どこへ・メモ・根拠）に追記される。終了状態（`retired` / `rejected`）からは動かせない。
- 1 提案 = `runs/tenant_eval/proposals/<id>.json`（`TENANT_EVAL_PROPOSALS_DIR` で変更）。原子的に書き、
  別の更新と衝突したら（`rev` 不一致）拒否する。
- 評価の流れ: `scripts/tenant_eval.py evaluate --proposal <id> ...` → 台帳の `seq` → `attach_evaluation(ledger_seq=)`。
- 影子・カナリアの「根拠」には、2-7 の影子比較の結果（`shadow.compare_runs`）や監視の確認結果を書く。
