# 管理 API（管理コンソールが使う API）

すべて管理者の認証が必要です（セッション、または管理トークン）。変更系は CSRF トークン（`X-CSRF-Token`）が必要です。
**範囲:** 全体管理者はすべて、**テナント管理者は自分のテナントの分だけ**（他のテナントのものは 403、または「存在しない」404）。
「全体」と書いたものは、全体管理者だけです。変更は監査ログに残ります。

| 領域 | エンドポイント | 内容 | 範囲 |
|---|---|---|---|
| 状態・連携 | `GET /admin/system/status` | 連携の状態、どの層が止めているか、LLM のプロファイルとブレーカー | 読み取りは全員 |
| | `POST /admin/system/switches` | 連携のオン・オフ（理由必須、`expected_rev` で衝突検出） | 全体は全体管理者。テナントの制限は自分のテナント |
| | `GET /admin/system/changes` | スイッチの変更履歴 | 自分のテナントの分 |
| | `GET /admin/system/approvals`、`POST /admin/system/approvals/{id}/approve｜reject｜cancel` | 外部連携のオンの二人承認（オンの依頼は 202 で承認待ちになる） | 全体は全体管理者 |
| | `GET /admin/availability` | 外部プローブの記録から、営業時間内の稼働率・停止区間・監視の空白（`PROBE_LOG_FILE` 未設定なら `configured: false`） | 全体管理者 |
| | `GET /system/status`（利用者向け） | 通常／縮退だけ（理由コードのみ） | API 認証 |
| 記録 | `GET/POST /admin/records/level` | 記録レベルの確認・変更（理由必須、環境の上限まで） | 全体は全体管理者 |
| | `GET /admin/records`、`/stats`、`/{id}` | 記録の一覧（フィルタ・ページ送り）、集計、1 件。**読むこと自体を監査に残す** | 自分のテナントの分 |
| | `POST /admin/records/{id}/review` | 判定（正しい／部分的／誤り、理由、期待する回答、判定者の名前） | 自分のテナントの分 |
| | `POST /admin/records/purge` | 保持期限の処理（既定は予行）。記録と使用量のファイル | 全体 |
| ゴールデン QA | `GET/POST /admin/golden/{tenant}` | 一覧・追加 | そのテナント |
| | `GET/PUT/DELETE /admin/golden/{tenant}/{case_id}` | 取得・変更（`expected_updated_at` で衝突検出）・削除（削除したものは別ファイルへ） | そのテナント |
| | `POST /admin/golden/{tenant}/from-record` | 判定した記録から作る | そのテナント |
| | `GET /admin/golden/{tenant}/export` | 評価が読む形（`golden_qa` の行）で書き出す | そのテナント |
| 使用量 | `GET /admin/usage`、`/daily` | LLM の呼び出し・トークン・費用の概算（テナント・用途・プロファイル・モデル・呼び出し元・場所別、日別） | 自分のテナントの分 |
| | `GET /admin/usage/downtime` | ブレーカーが開いていた時間と、そのうち営業時間内の時間 | 全体 |
| 上限・割り当て | `GET/POST /admin/limits` | 1 日の上限（利用者・テナント・全体）と、今日の使用量。上限の設定は理由必須（`docs/operations/llm_limits.md`） | 読み取りは自分のテナントの分。全体の上限は全体管理者 |
| | `GET/POST /admin/llm-assignments` | モデルのティアの割り当て（全員・テナント・役割・利用者）。変更は理由必須 | 読み取りは全員。変更は全体管理者 |
| 監査 | `GET /admin/audit` | 監査ログ（`kind`＝admin / chat / feedback、フィルタ、新しい順） | 自分のテナントの分（テナントのないイベントは見えない） |
| | `GET /admin/audit/verify` | ハッシュ連鎖の検証 | 全体 |
| 変更管理 | `GET /admin/proposals`、`/{id}` | 提案の一覧・詳細（履歴つき） | 読み取りは全員 |
| | `POST /admin/proposals`、`/{id}/{move}` | 提案の作成と、classify・propose・attach-evaluation・approve・reject・advance・retire | 全体（実行する人の名前が必須。提案した人は承認できない） |
| | `GET /admin/profiles`、`/{id}/versions`、`/{id}/diff` | プロファイルの版、差分 | 読み取りは全員 |
| | `POST /admin/profiles/{id}/activate`、`/rollback` | 版の有効化・巻き戻し（理由と名前が必須） | 全体 |
| | `GET /admin/evaluations`、`/admin/knobs`、`/admin/monitoring` | 評価台帳（連鎖の検証つき）、評価が変えてよい設定、フィードバックと記録の推移 | 読み取りは全員（監視は自分のテナントの分） |
| 権限 | `GET /admin/access` | 認証設定の確認用の一覧（キー・トークン・グループ名は出さない） | 読み取りは全員 |

## 「名前」について

提案・承認・判定・版の有効化のように、**人が責任を持つ操作**では、管理画面が操作する人の名前を求めます。
管理者の認証は「どのテナントを触れるか」を決めるもので、画面の前にいる人が誰かまでは分かりません。名前は**申告**で、記録と監査ログに残ります。
「提案した人は承認できない」という規則は、この申告の名前で判定されます。厳密な本人確認が必要になったら、SSO の本人の識別子に置き換えます（未実装）。

## 評価の実行について

評価（ゴールデン QA での測定、条件の比較）は、従来どおりコマンドラインで実行し、結果が評価台帳に記録されます。API は、その結果の閲覧と、提案への紐付けを受け持ちます。
コンソールで作ったゴールデン QA は、`scripts/tenant_eval.py evaluate --golden-source console`（または `both`）で評価に使えます（`tenant_evaluation.md`）。
