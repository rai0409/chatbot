# LLM ゲートウェイ（モデルプロファイル・ブレーカー・使用量）

LLM の呼び出しは、回答生成も書き換え・検証・採点も、すべて `rag_core/llm_gateway.py` を通ります。
「使ってよいか」は従来どおり `rag_core/llm.py` の 4 層（全体・テナント・機能・宛先）が決め、ゲートウェイは「どのモデルを、いま呼べるか」を決めます。

## モデルプロファイル

配備ごとの JSON ファイル（`LLM_PROFILES_FILE`）に、モデルの場所・名前・単価と、用途ごとの試す順番を書きます。
雛形は `config/deploy/llm_profiles.example.json` です。**ファイルはリポジトリの外に置きます。** キーはファイルに書かず、`api_key_env` で環境変数の名前を指します。

- **Claude（Anthropic）**は、OpenAI 互換層ではなく**公式 `anthropic` SDK のネイティブ API**で呼ぶ。プロファイルに `"provider": "anthropic"` と `api_key_env` を書く（`effort`、`thinking` も指定可。雛形の `claude_low_cost`）。
  `pip install -r requirements-anthropic.txt` が必要。`temperature` は送らない（新しいモデルは非既定値を 400 で拒否する）。`batch` は使えない。モデルが拒否した場合は空の回答として扱う。
  実 API では未検証（テストは模擬クライアント）。
- `profiles`: 名前ごとに `base_url`（OpenAI 互換 API）、`model`、`timeout_seconds`、`max_tokens`、`cost`（100 万トークンあたりの単価。ローカルは 0）。
- `routes`: `generate` / `rewrite` / `verify` / `judge` ごとの、試すプロファイルの順番。最初のものが失敗するか、ブレーカーが開いているときに次を使う。
- `tenants`: テナントごとの上書き。
- ファイルがなければ、従来の `OPENAI_BASE_URL` / `CHAT_MODEL` / `OPENAI_API_KEY` から作る 1 つの暗黙のプロファイルだけが使われる（従来どおりに動く）。
- 場所の判定: ループバック・プライベート IP・`LLM_INTERNAL_HOSTS` のホストは「ローカル」、それ以外は「外部」。本番（`APP_ENV=production`）では、
  `LLM_EXTERNAL_ALLOWED=true` でない限り、外部のプロファイルは使われない（全部が外部なら、その用途は無効）。
- 回答キャッシュのキーに、プロファイルファイルの内容の要約が入るので、モデルを替えると古い回答は使われない。
- 設定の誤りは、起動時の本番ガードと `/admin/system/status` の `profiles_file_problems` に出る（キーは出さない）。

## ブレーカー（サーキットブレーカー）

プロファイルごとに、連続して `LLM_BREAKER_FAILURE_THRESHOLD`（既定 3）回失敗したら、`LLM_BREAKER_OPEN_SECONDS`（既定 60 秒）の間は呼ばない。
待たずに次のプロファイルへ進み、全部が止まっていれば抽出型の回答になる。期間が過ぎると `LLM_BREAKER_PROBES`（既定 1）回だけ試し、成功すれば元に戻る。

- 数えるのは、リトライをすべて使った後の結果。形式の誤り（HTTP 400・422）は、接続先の障害とは数えない。
- 状態は既定ではプロセス内。`BREAKER_BACKEND=redis`（`REDIS_URL` が必要）にすると、**複数ワーカー・複数台で共有**される（`webapi/breaker_store.py`。WATCH/MULTI による原子的な更新、有効期限 1 日）。
  Redis に届かないときは、その間だけプロセス内の状態に退避し（LLM の呼び出しは止めない）、`/ready` の確認対象にも入る。実 Redis では未検証（テストは模擬）。
- 状態が変わるたびに使用量のファイルに 1 行残るので、各状態にいた時間（稼働率の算定の材料）を足し合わせられる（`llm_usage.state_seconds`）。
- `LLM_BREAKER_ENABLED=false` で止められる。

## 使用量の記録

呼び出しごとに 1 行（JSON）を、日ごとのファイル `llm_usage-YYYY-MM-DD.jsonl`（`LLM_USAGE_DIR`、既定は `runs/usage`）に追記する。
**質問・回答・文書の本文は入らない。** 入るのは、テナント、API キーの指紋とロール（名前ではない）、用途、プロファイル、モデル、ホスト、場所、
入出力トークン、応答時間、結果（ok / error / refused / breaker_open）、費用の概算。トークン数はプロバイダが返せばその値、返さなければ文字数からの推定（`estimated: true`）。
`LLM_USAGE_ENABLED=false` で止められる。記録に失敗しても回答は止まらない。集計は `llm_usage.summarize`（テナント別・用途別・モデル別など）。

## 利用者向けの状態

`GET /system/status` は、サービスが「通常」か「縮退（LLM が使えず抽出型で回答）」かだけを、理由コードで返す（ホスト名・モデル名・件数は返さない）。
管理者向けの `GET /admin/system/status` は、プロファイルごとのブレーカーの状態を返す。

## 設定項目

`LLM_PROFILES_FILE`、`BREAKER_BACKEND`、`LLM_BREAKER_ENABLED`、`LLM_BREAKER_FAILURE_THRESHOLD`、`LLM_BREAKER_OPEN_SECONDS`、`LLM_BREAKER_PROBES`、`LLM_USAGE_ENABLED`、`LLM_USAGE_DIR`
（`.env.example` に説明あり）。モデルの選定は `docs/operations/llm_model_selection.md`。

## まだないもの

- 実 LLM・実 Redis・実際の Claude API での確認（どれも模擬でのテストのみ）。

1 日の上限、モデルの割り当て（ティア）、バッチ呼び出し、LLM のアラートは、`docs/operations/llm_limits.md` にあります。
