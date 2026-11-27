# 承認済み QA の言い換え（approved_aliases）の候補

質問側の言い換えの仕組みは以前からある。承認済み QA の 1 件に `approved_aliases`（最大 20 個、各 500 文字まで）を持たせると、
利用者の質問が別表現と（正規化後に）一致したとき、承認済みの回答に直接届く
（`approved_qa.lookup_approved_answer`、応答の `answer_mode` は `approved_alias_match`）。
3-5 は、**どの言い換えがまだ届かないか**を調べ、**追加する候補**を出す。

## 網羅の確認（`coverage`）

承認済みの各質問を機械的に揺らし（3-1 の摂動）、変種が同じ承認済み回答に届くかを、実際の照合関数で確かめる。
種類ごとの「届く数 / 変種の数」と、届かなかった変種の一覧が得られる。

```
python scripts/alias_candidates.py coverage --tenant ID [--approved ファイル] [--kinds casual_ending,keywords_only] [--out ファイル]
```

候補にするのは、利用者が実際に繰り返し打ちそうな種類（既定: `casual_ending` `keywords_only` `katakana_hiragana` `spacing`）の
うち届かなかったもの。誤字・字落ちは繰り返されにくいので既定では候補にしない。1 件に追加できる数の残り
（20 − 既存）、既存の質問・別表現との重複、制御文字、500 文字超を除き、1 つの質問につき `max_per_question`（3）まで。

## 質問からの抽出（`mine`）

canonicalまたはformal aliasに一致しない利用者の質問（1 行 1 質問のファイルを**ご自身で用意**する）を、文字 2 グラムの類似度で
最も近い承認済みの質問（別表現を含む）に対応づける。しきい値（既定 0.5）以上の最良の 1 件だけを候補にする。

```
python scripts/alias_candidates.py mine --tenant ID --questions ファイル [--threshold 0.5] [--out ファイル]
```

本番の監査ログは質問をマスクするため、元の質問は平文モードのログ、または利用者の同意のある収集から用意する。

## 出力と運用

- 出力は JSONL（`status: "pending"`、`candidate_id`、`qa_id`、`question`、`alias`、`source`、`kind` または `similarity`）。
  既定の出力先は `RUNS_DIR/alias_candidates/<tenant>_<コマンド>.jsonl`。
- **コードは承認済み QA のファイルを変更しない**（出力先に承認済み QA のファイルは指定できない）。
  候補は提案にすぎない。人が「承認済みの回答が、その言い方の質問にも答えているか」を確認してから、
  既存のレビューの手順で `approved_aliases` に追加する。似ているだけで答えが違う質問を足すと、誤った承認済み回答を返す。
- 質問ファイルと出力には利用者の文章が入りうるので、リポジトリ内の無視されるフォルダ（`runs/`）に置く。

## 口語一致との区別

`coverage`はcanonicalおよび人が承認したformal aliasの一致を測定する。未登録の口語表現がruntimeで一致しても正式alias扱いにはならず、候補の登録にはoperatorの承認が必要。semantic similarityのみで承認回答を自動返却しない。

## 承認状態の反映と追加照合（2026-11）

- **完全一致インデックス（`APPROVED_QA_EXACT_INDEX_PATH`）の結果は、現在の承認済み QA で再確認する。**
  ヒットの `qa_id` が承認済み QA のファイル（`APPROVED_QA_PATH`）で今も `status: approved` であり、回答本文が
  同じときだけ返す。却下・下書きへの戻し（編集）・削除の直後から、インデックスを作り直す前でも返らない。
  承認済み QA が無効（`APPROVED_QA_ENABLED=false`）なら完全一致も使わない（従来どおり）。
- 出典文書の存在確認は `approved_qa_exact_lookup.set_source_doc_exists_check(check)` で差し込む
  （`check(tenant_id, source_doc)` が `False` を返した文書を引用する回答は返さない）。既定では未登録
  （確認しない）。配信中コーパスの文書一覧をテナント別に安く引ける関数ができたら登録する（TODO）。
- **類似候補（approved_similar）** は `status: approved` のペア行だけを検索する
  （`APPROVED_SIMILAR_REQUIRE_STATUS`、既定 `true`）。`scripts/approved_qa_to_pair_chunks.py` は各行に
  `status: "approved"` を書く。**移行**: この欄がない既存のペア行は見つからなくなる（安全側）。
  変換し直して取り込み直すまでの間だけ `APPROVED_SIMILAR_REQUIRE_STATUS=false` にできる。
  承認済み QA が有効なら、候補は現在承認済みの `qa_id` に限る（取り込み後に却下・削除された行は出ない）。
- 承認済み QA の保存・状態変更・削除・取り込みは、そのテナントの回答キャッシュを消す
  （`answer_cache.clear_tenant`）。
- **会話の続き**: 会話文脈で補完された質問（例「申請方法は？」→「有給 申請方法は？」）は、補完後の
  質問で承認済み QA を引く。補完前の短い言い方で別の承認済み回答に当たることはない。
- テナントの `answer_policy.allow_exact_answer=false`（製品プロファイル、またはテナント対応表の
  `chat_policy`）なら、/chat・/chat/stream・/search で承認済み回答（完全一致・別名・口語一致）を使わない。

## 表記ゆれ（長音）

完全一致キー（`exact_match_key`）と回答キャッシュの質問キーは同じ長音の規則を使う。語末の「ー」は除き、
カタカナが続く「ー」は残す。`サーバ`/`サーバー`、`ユーザ`/`ユーザー` は同じキー、`ビール`/`ビル` は別のキー。
複合語の途中（`サーバーメンテ`/`サーバメンテ`）は語中の長音（`パスワード`）と区別できないため別のキーのまま。
必要なら別名（`approved_aliases`）として登録する。回答キャッシュのキーはこの変更で一度だけ変わる（再計算されるだけ）。

## 口語の定義質問

`configs/query_rules/colloquial_ja.json` の `strip_suffix` に書き換え規則
（`{"type": "rewrite", "pattern": ..., "replace": ...}`）を置き、「VPNってなに」を「VPNとは」にしてから
「って」の除去規則が動く。書き換え規則に対応していない読み込み側は、この要素を読み飛ばす。
「〜」と半角の「~」（全角「～」の NFKC）はどちらも伸ばし記号として扱う。
