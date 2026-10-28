# プロファイルの版管理

顧客プロファイル（`config/rag_profiles/<id>/exported/`）の内容に版を付け、どの版で回答したかを記録し、
いつでも前の版へ戻せるようにする。

## 仕組み

- **版** = 7 つの JSON（`profile.json` / `question_type_rules.json` / `domain_terms.json` / `synonyms.json` /
  `retrieval_boost_rules.json` / `validation_rules.json` / `answer_templates.json`）と `golden_qa.jsonl` の
  コピー。版 ID は、正規化した内容の SHA-256 の先頭 16 桁。キーの順序・インデント・空行は内容に数えない。
  同じ内容は同じ版（再作成しても何も書かない）、1 つでも値が変われば新しい版になる。
- **不変**: 作成後は書き換えない。保存済みの版が digest と一致しなくなったら `verify` と `activate` が拒否する。
- **保存先**: `data/profile_versions/<id>/versions/<版>/`。`PROFILE_STORE_DIR` で変更できる。
  compose では `./data` が書き込み可能な volume のため、読み取り専用の `config/` には書かない。
  `data/profile_versions/` は `.gitignore` 済み（実行時の状態）。
- **有効な版**: `<id>/active.json`。無い場合・`clear` 後は従来どおり `config/rag_profiles/<id>/exported` を読む
  （挙動は変わらない）。壊れたポインタは別の版へ黙って切り替えず、空プロファイル＋警告として扱う
  （回答の検証は skip される）。
- 読み込み結果の `profile_version`（読んだファイルの digest 先頭 16 桁）と `profile_pinned`、回答トレースの
  `rag_profile_version` で、どの版で答えたかが分かる。キャッシュは「読むディレクトリ＋有効な版」をキーにするため、
  切り替えはキャッシュ再読み込みなしに全ワーカーへ反映される。回答キャッシュのキーにも含まれる。

## 運用の流れ

```
# 1. config/ を編集（または scripts/apply_synonym_candidates.py で同義語を反映）
# 2. 現在の有効な版との差分を見る
python scripts/profile_versions.py diff --profile ID <有効な版> source
# 3. 版を作る → 検証 → 有効化
python scripts/profile_versions.py create   --profile ID --by 担当 --note "変更の理由"
python scripts/profile_versions.py verify   --profile ID --version <版>
python scripts/profile_versions.py activate --profile ID --version <版> --by 担当
# 戻す
python scripts/profile_versions.py rollback --profile ID --by 担当
# 版管理をやめて config/ を直接読む
python scripts/profile_versions.py clear    --profile ID
```

- 版が有効な間、`config/` への編集（同義語の承認反映を含む）は **次の版を作って有効化するまで本番に反映されない**。
  `apply_synonym_candidates.py` と同義語レビューの反映結果には `pinned_version` が出る。
- `rollback` は直前の版へ戻す。`clear` した版も履歴に残るため `rollback` で復帰できる（履歴は 20 件）。
- `golden_qa.jsonl` も版に含める（ファイルが無い場合は、従来どおりロード時の警告になる）。
- 版の作成時には JSON として読めるかだけを確認する。内容の妥当性検証は `tools/validate_rag_profile.py` を併用する。

## 次の段階との関係

承認つきの状態機械（提案→評価→承認→影子→反映）と、評価台帳への版の記録は 2-4 以降で、この版 ID を使う。
