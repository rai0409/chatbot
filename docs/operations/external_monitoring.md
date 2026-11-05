# サービス停止の外部監視（S7）

サービスは自分の停止を報告できない。止まっているとき、記録も通知も出せないからである。
そのため **別のホスト**から `/ready` を定期的に問い合わせ、答えを記録し、止まったら人に知らせる。
営業時間内の稼働率（平日 8〜21 時、目標 99.0%）は、この記録から計算する。

## 構成

| 部品 | 場所 | 役割 |
| --- | --- | --- |
| プローブ | `scripts/external_probe.py` | `/ready` を問い合わせ、JSON Lines に 1 行記録し、必要なら通知する（標準ライブラリのみ） |
| 集計 | `rag_core/availability.py` | 記録から営業時間内の稼働率・停止区間・監視の空白を計算する |
| 管理 API | `GET /admin/availability` | サービス側が同じ記録を読んで稼働率を返す（全体管理者のみ） |

## 判定の定義

- **稼働**: `/ready` が HTTP 200。
- **停止**: 503、その他の応答、応答なし。
- **縮退**（LLM が使えず抜粋回答になっている状態）は **稼働として数える**（サービスレベルの定義どおり）。別枠で「縮退しながら稼働した分」を表示する。縮退の検出には `--api-key-env` で `/system/status` も読む。
- 1 回の応答は次の応答まで有効。ただし最大 `PROBE_MAX_GAP_SECONDS`（既定 300 秒）まで。これを超えた時間は **「監視できなかった時間」** として稼働にも停止にも数えず、別に表示する。プローブ自体が止まっても数字がよく見えることはない。
- 営業時間は `BUSINESS_HOURS_*`（既定 UTC+9、8〜21 時、平日のみ）。**祝日は未対応**で、平日として数える。

## 設置（別ホストで）

リポジトリの `scripts/external_probe.py` と `rag_core/availability.py` だけがあればよい（Python 3.9 以上）。
通知先の秘密は環境変数で渡し、コマンドラインやログには出さない。

```
# 1 分ごとに 1 回（cron）
* * * * * cd /opt/kuraden && python3 scripts/external_probe.py probe \
    --url https://chat.example.co.jp --log /var/lib/kuraden-probe/probe.jsonl \
    --slack-webhook-env PROBE_SLACK_WEBHOOK --heartbeat-url https://heartbeat.example.co.jp/ping/sample
```

常駐させる場合は `watch --interval 60`（systemd の `Restart=always` 推奨）。

通知の条件（既定）:

- 連続 3 回（`--fail-after`）停止と判定したら **「停止」を 1 回**通知する。一瞬の失敗では通知しない。
- 停止が続く間は 60 分ごと（`--remind-minutes`、0 で無効）に **「停止が継続」**。
- 稼働に戻ったら **「復旧」**（停止時間つき）。
- 状態は `<ログ>.state.json` に保存されるので、cron で毎回起動しても重複通知しない。

通知先（併用可、1 つが失敗しても他は送る）:

| 方法 | 設定 |
| --- | --- |
| Slack 形式の Webhook | `--slack-webhook-env 変数名`（変数に URL） |
| メール | `--smtp` と `PROBE_SMTP_HOST` / `_PORT`(587) / `_USER` / `_PASSWORD` / `_STARTTLS`、`PROBE_MAIL_FROM` / `PROBE_MAIL_TO` |
| コマンド | `--on-event-command "..."`（環境変数 `PROBE_EVENT`、`PROBE_MESSAGE` が渡る。シェルは介さない） |

終了コード（`probe`）: 0 稼働、1 稼働だが縮退、2 停止。

### プローブ自身の死活

`--heartbeat-url` には、プローブの 1 回ごとに GET する URL を指定する（サービスが止まっていても送る）。
Dead man's switch 型の外部サービスで「一定時間 ping が来なければ通知」と設定すれば、**プローブのホストが止まったこと**も検知できる。
これを設定しない場合、プローブが止まっても誰にも通知されない（集計上は「監視できなかった時間」として残る）。

## 稼働率の確認

```
python3 scripts/external_probe.py report --log /var/lib/kuraden-probe/probe.jsonl --days 30 --target 99.0
```

営業時間内の稼働・停止・監視できなかった分数、監視の網羅率、停止区間を表示する。目標未達なら終了コード 1。

サービスの管理画面から見るには、サービス側の `PROBE_LOG_FILE` にそのログのパスを指定する
（別ホストなら共有・同期が必要。`AVAILABILITY_TARGET_PERCENT`、`PROBE_MAX_GAP_SECONDS` も設定可）。
`GET /admin/availability?days=30` が同じ集計を返す。未設定なら `configured: false`。

## ログについて

- 1 行: `{t, ok, status, ms, error, degraded, host}`。**API キー・応答本文・パスは書かない**。
- 10 MB で `.1` へローテーション（1 世代。集計は両方を読む）。古い分の削除は運用で行う。

## 限界（正直な記載）

- 稼働の根拠は `/ready` の応答のみ。画面・ログイン・実際のチャット応答まで通っているかは見ていない。
- 1 か所のプローブからの観測であり、プローブ側のネットワーク障害は「停止」に見える（`--fail-after` で一時的なものは抑える）。
- 祝日・計画停止は除外できない。
- 実環境（実サービス・Slack・SMTP・外部の死活監視サービス）では未検証。テストはローカル HTTP サーバーと模擬の通知で行っている。
