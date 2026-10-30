# 設定の置き場（4 区分）

配備ごとに変わる値は、コードに書かず、次のどれかに置きます。

| 区分 | 例 | 置き場 | 変更の方法 |
|---|---|---|---|
| 秘密 | LLM のキー、Graph の資格情報、SMTP のパスワード、管理トークン | 環境変数／シークレット管理。**リポジトリには置かない** | 再起動・ローテーション |
| 配備固有 | 接続先の URL、タイムアウト、ブレーカーとアラートの閾値、保持期間、モデルのプロファイル | `.env` と、配備ごとに別管理する設定ファイル（`config/deploy/*.example.*` は雛形） | 再起動 |
| 実行時 | 外部連携のスイッチ（全体・テナント別） | `rag_core/runtime_settings.py` の保存先（`RUNTIME_SETTINGS_DIR`、既定は `runs/settings`） | 管理コンソール |
| コード | 状態の定義、判定のロジック | コード | リリース |

## 実行時の設定（runtime_settings）

- 1 つの JSON ファイルを、ロックのもとでまるごと置き換える（複数ワーカーでも壊れない）。
- **変更には理由と変更者が必要**で、設定の版が 1 つ上がり、ファイル内の履歴（直近 200 件）と監査ログ（ハッシュ連鎖）の両方に残る。
- テナントの値は、全体の値より優先される。値を `None` にすると削除され、既定に戻る。
- 読めない・壊れたファイルは「空」として扱われ、すべてが既定（外部連携はオフ）になる。
- `expected_rev` を付けると、他の人が先に変更していた場合に衝突（409）になる。
- 今はファイルの保存先だけ。複数ノードで共有したくなったら、Postgres の保存先を足す（`state_backend` の流儀）。

## S1 で設定に移した固定値

| 設定 | 既定（従来の固定値と同じ） | 用途 |
|---|---|---|
| `GRAPH_BASE_URL` | `https://graph.microsoft.com/v1.0` | Graph の API |
| `GRAPH_TOKEN_URL` | `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token` | トークンの取得先（`{tenant}` を置き換える） |
| `GRAPH_SCOPE` | `https://graph.microsoft.com/.default` | スコープ |
| `GRAPH_HTTP_TIMEOUT_SECONDS` / `GRAPH_MAX_WAIT_SECONDS` | 60 / 60 | Graph の HTTP と、再試行の最大待ち時間 |
| `NOTIFY_SLACK_TIMEOUT_SECONDS` / `NOTIFY_SMTP_TIMEOUT_SECONDS` | 10 / 15 | 通知 |
| `OIDC_HTTP_TIMEOUT_SECONDS` / `OIDC_RETRY_WAIT_SECONDS` | 15 / 0.5 | OIDC のトークンと JWKS |
| `EMBEDDING_BASE_URL` | 空（`OPENAI_BASE_URL` を使う） | 埋め込みの接続先を LLM と分ける |
| `EXTERNAL_INTEGRATIONS` | 空（すべて無効） | 外部連携の環境の許可 |

**まだコードにあるもの:** 画面の文言（HTML 内。コンソールは `webui/src/i18n/ja.ts` にまとめた）、既存の管理画面のナビのリンク。これらは管理画面の作成（S5）で、設定・データに移す。

## 設定の例に書いてよい値

設定の例・テスト・文書には、実在する組織名・人名・アドレス・ホストを書かない。例が必要なときは、`example.co.jp`、`山田太郎`、`株式会社サンプル` などの固定値だけを使う。
