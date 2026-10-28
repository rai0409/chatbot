# LLM の使用ポリシー（`rag_core/llm.py`）

LLM を使う箇所はすべて `llm.check(feature, tenant)` に聞く。4 つの層を順に見て、**どの層が止めたか**を返す。

| 層 | 設定 | 内容 |
|---|---|---|
| global | `LLM_ENABLED` | 未設定なら従来の切替（`CHAT_GENERATION_MODE=llm`）に従う。`LLM_ENABLED=false` は無効化スイッチで、従来の切替より優先 |
| tenant | `LLM_TENANT_DENYLIST` / `LLM_TENANT_ALLOWLIST` | 拒否リストは常にオフ。許可リストが空なら全テナント、あれば載っているテナントだけ |
| feature | `LLM_FEATURES`（既定 `generate`） | `generate`（回答の生成。従来どおり）、`rewrite`（4-2）、`verify`（4-3）、`judge`（4-4）。新機能は LLM がオンでも明示しない限り使われない |
| endpoint | `LLM_EXTERNAL_ALLOWED`, `LLM_INTERNAL_HOSTS` | 本番（`APP_ENV=production`）では、外部のエンドポイントに `LLM_EXTERNAL_ALLOWED=true` が要る |

- **外部**とは: `OPENAI_BASE_URL` が無い（ベンダー既定）、またはホストがループバック・プライベート IP・`LLM_INTERNAL_HOSTS`
  のどれでもない場合。本番の起動時ガード（`webapi/production_guard.py`）は、LLM がオンで外部、かつ許可なしなら起動を拒否する。
- **回答経路**: `llm.generation_mode(tenant)` が `config.resolve_chat_generation_mode()` の代わりに使われる。
  許可されないテナントや機能は、LLM を呼ばず抽出式で回答する（応答の `chat_generation_mode` が `extractive` になる）。
- **呼び出し**: 新機能は `llm.complete(messages, feature=, tenant_id=)`。ポリシー確認（拒否は `LLMNotAllowed`、`.decision` に層と理由）、
  429 / 5xx / タイムアウト / 接続エラーだけ再試行、トークン上限（`LLM_FEATURE_MAX_TOKENS`、既定 512）、回数のカウンタ
  （`llm.stats()`）。**プロンプトと生成文はログに出さない**。
- **ローカルのエンドポイント**: `OPENAI_BASE_URL` を指定すれば、API キーが空でも `ensure_openai_client` は動く
  （キーを検査しないローカルサーバー向け。ベンダー既定のエンドポイントでは従来どおりキーが必須）。

## 使い方の例

```
# 社内の LLM を、テナント tenant_a だけ、書き換えと検証に使う
OPENAI_BASE_URL=http://llm.internal:8000/v1   LLM_INTERNAL_HOSTS=llm.internal
LLM_ENABLED=true  LLM_TENANT_ALLOWLIST=tenant_a  LLM_FEATURES=rewrite,verify
CHAT_GENERATION_MODE=extractive      # 回答の骨格は抽出式のまま
```
