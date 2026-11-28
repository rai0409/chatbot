# 日本語クエリ正規化（略語・業務同義語・口語・ルーティング）

2026-11-27 追加。検索に使う質問文を、文書側の書き方に寄せるための処理と、その評価手順。
新しいフラグはすべて**既定オフ**。評価で改善を確認してから、テナントごと（または全体）にオンにする。

## パイプライン（`rag_core/query_preprocess.py`）

`rag_core/qa.py` から `preprocess_query()` が呼ばれ、次の順に処理される。各段階は trace の `steps` に残る。

| 順 | 段階 | 内容 | スイッチ |
|---|---|---|---|
| 1 | normalize | `ja_text.normalize_for_index`（NFKC、小文字化。索引と同じ） | 常時 |
| 2 | colloquial | 書き換え（`rewrite`）→ 前置き・語尾の除去（`strip_prefix` / `strip_suffix`） | 常時（`QUERY_PREPROCESS_ENABLED`） |
| 3 | numeric_tokens | キーワード側の数値トークン | `KEYWORD_NUMERIC_TOKENS` |
| 3b | abbreviations | 略語 ⇔ 正式名の展開 | `QUERY_ABBREVIATIONS`（既定オフ） |
| 4 | synonyms | プロファイル同義語（＋汎用業務同義語） | `QUERY_SYNONYM_EXPANSION`（既定オン）、`QUERY_BUSINESS_SYNONYMS`（既定オフ） |
| 5 | typo | 索引語彙による誤字候補 | `QUERY_TYPO_CORRECTION` |

整形後の質問が検索クエリになり、略語・同義語・誤字候補は「展開語」として 1 回だけ後ろに付く（元の語は残るので、
展開語は BM25 でも埋め込みでも元の語より弱く効く）。略語と同義語の展開語は**合計 8 語まで**（共通の上限）。

### 略語（`QUERY_ABBREVIATIONS`）

- 汎用辞書: `configs/query_rules/abbreviations_ja.json`
  `{"version": 1, "entries": [{"abbr": "育休", "expansion": "育児休業", "bidirectional": true, "note": "..."}]}`
- 質問に `abbr` があれば `expansion` を足す。`bidirectional: true` なら、`expansion` があるときに `abbr` も足す。
- 3 文字以下の略語は、質問の語（形態素または英数字トークン）として現れたときだけ一致する（`pc` は `npc` に、
  `マイナ` は `マイナス` に一致しない）。また、長い辞書語が先に文字を占有するので、`在宅勤務` の中の `在宅`、
  `時短勤務` の中の `時短` は別の一致として数えない。
- 1 文字の略語は登録できない（読み込み時に捨てる）。意味が複数ある略語（請求、定期、人事、総務など）は載せていない。
- テナント固有の略語は、そのテナントの質問ルールファイル `configs/query_rules/tenants/<tenant_id>.json` の
  `"abbreviations"` キーに同じ形式で書く（口語ルールと同じファイル、同じテナント ID 検証）。同じ `abbr` の汎用エントリを
  置き換え、`{"abbr": "年調", "enabled": false}` で汎用エントリを無効にできる。
- trace: `{"step": "abbreviations", "added": [...]}`（フラグオンのときだけ出る）。

### 汎用業務同義語（`QUERY_BUSINESS_SYNONYMS`）

- これまで `configs/japanese_business_synonyms.json` は承認済み QA の類似判定でだけ使われ、通常検索の同義語展開は
  RAG プロファイルの `synonyms.json` だけを見ていた（既定テナントには実質的に同義語展開が無かった）。
- フラグオンで、汎用グループをプロファイルのグループに**追加**する。プロファイルのグループと 1 語でも重なる汎用グループは
  使わない（プロファイル＝テナントの定義が優先）。同じグループは 1 回だけ。
- `negative_mismatch_pairs`: 展開語が片側の語を含み、質問が反対側の語だけを含むとき、その展開語は足さない
  （「扶養に入る」の質問に「扶養から外れる」を足さない）。
- 汎用グループには上位語・下位語・方向のある句が混じっている（承認 QA の重なり判定には問題ないが、検索クエリに足すと
  隣の文書に引っ張られる）。そのため、
  - 一致した語を含む複合語（パスワード → パスワード再設定）は足さない
  - `japanese_business_synonyms.json` の `query_expansion.exclude_terms` にある語は、展開のきっかけにも展開語にもならない
    （承認 QA 側はこのキーを読まない）
- 辞書ファイルは mtime とサイズでキャッシュされ、編集は再起動なしで反映される。壊れた JSON は警告を出して無視する。

### 口語の書き換え（`configs/query_rules/colloquial_ja.json` の `rewrite`）

検索クエリにだけ適用される（承認 QA の口語一致は `rewrite` を使わない）。各ルールに正例・負例のテストがある
（`tests/test_query_ja_improvements_20261127.py`）。

| ルール | 例 | 書き換えない例 |
|---|---|---|
| `howto_to_procedure` | 経費精算ってどうやるの → 経費精算の手順 | 〜んだけどどうしたらいい（不具合の相談）、〜どうすればいいですか（丁寧形） |
| `want_to_do` | 経費精算したいんだけど → 経費精算 | 申請したくないんだけど（否定は残る） |
| `where_is` | トナーってどこにあるの → トナーの場所 | 提出先はどこですか（「って」が無い形式的な質問） |
| `can_i_plain` | 在宅勤務できるの？ → 在宅勤務できる | 在宅勤務できない？、在宅勤務できますか |
| `yarikata_to_houhou` | 経費精算のやり方 → 経費精算の方法 | 「申請のやり方」（引用）、申請のやり方一覧（複合語） |

「〜って何／なに」は従来どおり組み込みの定義書き換え（`rewrite_definition`、→「〜とは」）が処理する。
なお `strip_suffix` の中にある `{"type": "rewrite"}` 形式のエントリは `query_preprocess` の読み込みでは使われない
（文字列以外は読み飛ばす）。検索に効く書き換えはトップレベルの `rewrite` に書く。

### 文体ルーティング（`rag_core/query_route.py`、`QUERY_ROUTING_ENABLED`）

`detect_style()` が質問を `colloquial`（口語の語尾・縮約・前置き）、`paraphrase`（ひらがなが多く、カタカナ語・英数字・
引用の無い長めの言い換え）に分類し、`route_for(query_type, question=...)` が dense の重みを上げ BM25 を下げる
（`ROUTES["colloquial"]`、`ROUTES["paraphrase"]`。初期値で、調整対象）。`exact_lookup` と `identifier` は従来の語の
ルートを優先する。ルーティング自体が既定オフで、`question` を渡さない呼び出しは従来と同じ動作。

## フラグ

| フラグ | 既定 | 意味 |
|---|---|---|
| `QUERY_ABBREVIATIONS` | false | 略語辞書による展開 |
| `QUERY_BUSINESS_SYNONYMS` | false | 汎用業務同義語を通常検索の同義語展開に加える |
| `QUERY_ROUTING_ENABLED` | false | 質問の種類・文体でレーンの重みを変える（従来からあるフラグ） |

どれも回答を変えるので、回答キャッシュのキー（`rag_core/answer_cache.py` の `_SETTING_NAMES`）に含める。

## 評価のしかた

### テナント評価（推奨。実際の qa パイプラインを通る）

```
python scripts/tenant_eval.py evaluate --profile <ID> --proposal ja-norm-1 \
    --knob QUERY_BUSINESS_SYNONYMS=true --knob QUERY_ABBREVIATIONS=true \
    --perturb --perturb-kinds casual_ending,keywords_only,abbreviate,synonym_swap \
    --report runs/tenant_eval/reports/ja-norm-1.md
```

- `--knob` に使うには、フラグが `rag_core/knob_registry.py` に登録されている必要がある。
- `abbreviate` / `synonym_swap` の変種は同じ辞書から作るので、フラグオンで改善するのは当然。判断には、元の質問
  （摂動なし）での合否と、`casual_ending` / `keywords_only` での落ち方が悪化していないことを重く見る。
- 1 フラグずつ評価する（2 つ同時だと、どちらが効いたか・どちらが壊したか分からない）。

### ベンチマーク（`python -m eval.bench`）

`from-cases` の自動スライスに `colloquial`（`detect_style` が口語と判定）と `abbreviation`（辞書の略語を含む）を追加した。
`eval.bench` 自体はクエリ前処理を通さないので、フラグの効果はベンチでは出ない。ベンチでは、検索方式や BM25 パラメータを
変えたときにこの 2 つのスライスが悪化していないかを見る。

```
python -m eval.bench from-cases --cases eval/cases/retrieval_v3_cases.jsonl --out runs/eval_set.jsonl
python -m eval.bench run --config <config>.json
python -m eval.bench.compare_runs --baseline-run runs/bench/<A> --baseline-config <id> \
    --candidate-run runs/bench/<B> --candidate-config <id> --require-same-conditions
```

BM25 の `k1` はベンチ設定（`configs/bench/general_v1*.json`）も本番（`BM25_K1=1.5`）にそろえた。`general_v1.json` だけが
1.2 だったが、差を残す理由の記録は無かった。

### オフラインの簡易 A/B（モデル不要、BM25 のみ）

`tools/ab_query_ja_flags_20261127.py` は、合成ケース（`eval/cases/retrieval_v3` / `retrieval_v4` /
`semantic_challenge_v2`）について、前処理後の検索クエリでの BM25 の hit@1 / MRR を、
A（新しい書き換えなし）、B（書き換えあり＝既定）、C1（＋略語）、C2（＋業務同義語）、C（両方）で比べる。
dense・融合・リランクは測らない。ケース数が少ない（カテゴリあたり 3〜12 件）ので、方向の確認にとどめる。

## 汎用とテナント固有の分け方

- **汎用辞書**（`configs/japanese_business_synonyms.json`、`configs/query_rules/abbreviations_ja.json`、
  `configs/query_rules/colloquial_ja.json`）には、どの組織でも同じ意味になる一般的な業務語だけを置く。
  組織名・製品名・人名・部署の固有名は書かない。
- **テナント固有の語彙**は `configs/query_rules/tenants/<tenant_id>.json`（口語ルール・`abbreviations`）または
  テナントの RAG プロファイル（`synonyms.json`）にだけ置く。
- **昇格の条件**: テナントで効いた語を汎用辞書に移すのは、2〜3 テナントで改善が確認でき、かつ公開ベンチマークと
  全テナントの回帰評価（`scripts/eval_regression_gate.py` と各テナントの `tenant_eval`）を通ったときだけ。
  1 テナントでの改善だけで汎用辞書に入れない。


### 辞書語の境界とオフライン評価

略語・同義語の照合には形態素分解を使わず、NFKC・小文字化後の ASCII 識別子境界と、短い日本語語の助詞・句読点などの保守的な境界を使う。長い辞書語が重なりを優先する。これは日本語の単語境界を完全に推定するものではなく、曖昧な複合語は展開を控える。

`python tools/ab_query_ja_flags_20261127.py --tokenizer heuristic --output-dir /tmp/ja-eval` で形態素分解を使わない BM25 比較を実行できる（誤字補正を比較に混ぜない場合は `QUERY_TYPO_CORRECTION=false` を指定）。`--datasets semantic_challenge_v2` のように存在するデータセットだけを指定できる。`case_changes.json` は A を基準に順位が改善・悪化した case ID を示し、元の質問と生成変種は別グループに出る。入力ファイルがない場合は代替せず評価できない。Dense、Reranking、Routing や実テナントでの精度はこの測定からは判断できない。
