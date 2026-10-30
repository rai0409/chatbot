# 外部連携のスイッチ（3 層）

外部へ通信する機能は、**環境の許可・コンソールのスイッチ・テナントの設定のすべてが許したときだけ**使えます。既定はすべてオフです。
外部連携を一つも使わなくても、取り込み（フォルダ）、検索、抽出型の回答、承認済み QA、契約台帳はすべて動きます（`tests/test_no_integrations.py` が、通信が起きたら失敗する形で確認している）。

| 連携 | 名前 | 出口（実際に外へ出る場所） |
|---|---|---|
| SharePoint 同期 | `sharepoint` | `rag_core/connectors/sharepoint.py` の送信関数 |
| Outlook 下書き | `outlook_draft` | 同上（連携ごとに別のチェック） |
| メール通知（SMTP） | `smtp` | `webapi/notifications.py` のメール送信 |
| Slack 通知 | `slack` | `webapi/notifications.py` の Webhook 送信 |
| LLM | `llm` | 従来の 4 層（`rag_core/llm.py`）。ここでは状態を報告するだけ |
| シングルサインオン | `oidc` | 従来の設定（`ENTERPRISE_OIDC_ENABLED`）。ここでは状態を報告するだけ |

## 3 層

1. **環境**: `EXTERNAL_INTEGRATIONS`（`sharepoint,outlook_draft,smtp,slack` のうち許すもの。`all` も可。既定は空）。コンソールから広げられない。
2. **スイッチ**: コンソールの全体スイッチ。既定はオフ。**変更には理由が必要で、記録される**。
3. **テナント**: テナントごとの制限（そのテナントだけ使えなくする）。テナントの管理者は、自分のテナントを制限することだけができる。

出口の送信関数は、テナントを知らないので 1・2 を、サービスの入口（ソースの走査、契約の下書き）は、テナントを知っているので 1〜3 を確認する。
理由は、拒否の理由コード（`env_not_allowed` / `switch_off` / `tenant_off`）で返り、ホスト名・URL・キーは含まない。
オフのとき、リモートのソースの走査は「失敗した走査」として台帳に残り、**「全部削除された」とは扱われない**。

## 管理 API

- `GET /admin/system/status`: 全連携の状態（無効 / 未設定 / 障害 / 正常）、どの層が止めているか、設定の版、LLM のプロファイルとブレーカー、確認が必要な項目。
- `POST /admin/system/switches`: `{name, enabled, reason, tenant_id?, expected_rev?, actor}`。全体のスイッチは全体管理者だけ。`expected_rev` が古ければ 409。
  **オンにする操作は二人承認**（下記）で、すぐには反映されず HTTP 202 で「承認待ちの依頼」を返す。オフにする操作と、テナントの制限は即時に反映される。すでにオンなら `unchanged`。
- `GET /admin/system/changes`: 直近の変更（全体の履歴は監査ログ）。
- `GET /admin/system/approvals`: 承認待ち・処理済みの依頼。`POST /admin/system/approvals/{id}/approve|reject|cancel`: `{actor, reason}`。
- コンソール（`/console/`）の「システム状態」画面から同じ操作ができる。

## 二人承認（オンにするとき）

外部へ出る連携をオンにするには、**依頼した人とは別の人**の承認が要る（`INTEGRATION_APPROVALS_REQUIRED`、既定 2。1 にすると従来どおり即時）。

- 依頼者の承認は 1 人分として数える。残りの人数が揃った時点で、スイッチが入り、設定の版と監査ログに残る。却下・取消もでき、`APPROVAL_EXPIRY_HOURS`（既定 72）を過ぎた依頼は期限切れになる。
- 「同じ人」の判定: 双方が本人の識別子（SSO の `sub` 由来）を持てばそれで判定。持たなければ、名前の空白・大小文字を無視して比較する。
- **限界**: SSO を使わない構成では、名前は申告で、別の名前を打てば二人目になれる。確実にするには SSO を必須にし、`APPROVAL_REQUIRE_SSO=true` にする（本人が確認できない承認を断る）。
  管理者の認証が共有トークンの間は、二人承認は「うっかりの防止」であって「なりすましの防止」ではない。
- 実環境（実 SSO）では未検証。

## 既存の環境から移すとき

以前は、資格情報（`SHAREPOINT_*`、`OUTLOOK_*`、`ALERT_*`）があれば連携が動きました。今は、資格情報があっても**オフのまま**です。
起動時に「設定はあるがオフ」の警告がログに出ます。使い続けるには、`EXTERNAL_INTEGRATIONS` に名前を書き、コンソールで理由を添えてオンにします。

## 出口制御（ネットワーク）との関係

このスイッチはアプリの中の制御です。ネットワークの出口（FW）で、許可する宛先を絞る設定は別に必要です（宛先の一覧は運用手順書で扱う）。
宛先は設定で変えられます: `GRAPH_BASE_URL`、`GRAPH_TOKEN_URL`、`GRAPH_SCOPE`、`OPENAI_BASE_URL`、`EMBEDDING_BASE_URL`、`ALERT_SMTP_HOST`、`ALERT_SLACK_WEBHOOK`、OIDC の発行者。
