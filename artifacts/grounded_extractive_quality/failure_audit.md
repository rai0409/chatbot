# Grounded Extractive Failure Audit

Audit source: pre-fix artifacts/grounded_extractive_quality/grounded_extractive_quality_results.jsonl.

| case_id | expected_answer_mode | actual_answer_mode | used_fallback | guard_reason | has_citations | source_doc_match | page_match | missing_required_terms | 主因分類 | answer_summary |
|---|---|---|---|---|---|---|---|---|---|---|
| geq_001 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | used_fallback_contract_bug | 文書内では、以下の記載が確認できます。 - e-BISC センターホームページ画面左の「電子入札準備手順書一覧」メニューをクリックし、「事前確認」の「手順2」にある「旧バージョンJRE アンインストール手順書(176KB)」をクリックして頂くと、マニュアルが表示出来ます -... |
| geq_002 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | required_terms_case_mismatch | 文書内では、以下の記載が確認できます。 - e-BISC センター FAQ (http://www.e-bisc.go.jp/)1質問内容区分質問回答社内で認証局が異なるIC カードを持っていますが、それぞれに利用者登録を行なった場合、企業ID はどのように採番されますか ... |
| geq_003 | grounded_extractive | fallback | true | soft_distance | false | true | true | Java Plug-in, 警告, 2 バイト文字 | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: soft_distance 不明: 根拠不足 不足: 関連記載なし |
| geq_004 | grounded_extractive | fallback | true | too_general | false | true | true | PIN, デバイスが使用できません, IC カードリーダー | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: too_general 不明: 根拠不足 不足: 関連記載なし |
| geq_005 | grounded_extractive | fallback | true | too_general | false | true | true | 適格請求書, 正確な適用税率, 消費税額 | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: too_general 不明: 根拠不足 不足: 関連記載なし |
| geq_006 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | used_fallback_contract_bug | 文書内では、以下の記載が確認できます。 - 8(7) 新たに設立された法人等の登録時期の特例新たに設立された法人が、事業を開始した日の属する課税期間の初日から登録を受けようとする旨を記載した登録申請書を、事業を開始した日の属する課税期間の末日までに提出した場合において、税務... |
| geq_007 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | used_fallback_contract_bug | 文書内では、以下の記載が確認できます。 - したがって、相続により事業を承継した相続人が、適格請求書発行事業者の登録を受けるためには、登録申請書を提出する必要があります(相続人が既に登録申請書を提出していた場合を除きます - また、相続により事業を承継した相続人が、適格請求... |
| geq_008 | grounded_extractive | fallback | true | too_general | false | true | true | 偽りの記載, 禁止, 罰則 | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: too_general 不明: 根拠不足 不足: 関連記載なし |
| geq_009 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | required_terms_case_mismatch | 文書内では、以下の記載が確認できます。 - 個々の商品ごとに消費税額を計算し、その計算した消費税額を税率ごとに合計し、適格請求書の記載事項とすることはできません - ※ 例えば、一の適格請求書に記載されている個々の商品ごとに消費税額等を計算し、端数処理を行い、その合計額を「... |
| geq_010 | grounded_extractive | grounded_extractive | true |  | true | true | true | 111, 火山国 | required_terms_case_mismatch | 文書内では、以下の記載が確認できます。 - 第1章御嶽山噴火から10年、教訓を踏まえた火山防災対策について長野県と岐阜県にまたがる標高3,067mの御嶽山は、活火山としては富士山に次ぎ日本で二番目の高さを誇る - 第3節登山者等の備え平成26年(2014年)の御嶽山噴火災害... |
| geq_011 | grounded_extractive | fallback | true | too_general | false | true | true | 登山届, 救助・救出活動, 迅速化 | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: too_general 不明: 根拠不足 不足: 関連記載なし |
| geq_012 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | used_fallback_contract_bug | 文書内では、以下の記載が確認できます。 - 13手順1申請者情報の確認画面に表示された氏名、住所、生年月日、性別を確認2内容に誤りがなければ「申請者本人の情報であることを確認しました」左のチェックボックスをタップ3[次へ]をタップマイナンバーカードの読み取り画面に移動します... |
| geq_013 | grounded_extractive | grounded_extractive | true |  | true | true | true | none | used_fallback_contract_bug | 文書内では、以下の記載が確認できます。 - 14手順1[読み取りをはじめる]をタップ2マイナンバーカードの利用者証明用電子証明書のパスワード数字4桁を入力21「パスワードの入力」画面が表示されるまで画面操作はしないでください - 25手順1マイナンバーカードの利用者証明用電... |
| geq_014 | grounded_extractive | fallback | true | too_general | false | true | true | 申請できません, 給付対象者, 条件 | guard_too_strict_for_in_corpus_question | - 関連情報が見つかりませんでした。理由: too_general 不明: 根拠不足 不足: 関連記載なし |

## Questions

- geq_001: 旧バージョンJREアンインストール手順書の場所を確認したい。
- geq_002: 企業IDの番号は認証局や地域ごとに変わるのか。
- geq_003: Java Plug-in アプレットインストールの警告が出る原因を調べたい。
- geq_004: 接続確認でPIN入力後にデバイスが使用できないと出る場合の関連箇所はどこか。
- geq_005: 適格請求書とはどのような手段だと説明されているか。
- geq_006: 新しく設立された法人が登録時期の特例を受ける条件は何か。
- geq_007: 相続で事業を承継した人がインボイス登録を受けるには何を提出する必要があるか。
- geq_008: 適格請求書に偽りの記載をした場合の注意事項はどこにあるか。
- geq_009: 個々の商品ごとに消費税額を計算して合計する例は認められるのか。
- geq_010: 日本には活火山がいくつあると書かれているか。
- geq_011: 登山届を出すことは救助活動にどう関係すると説明されているか。
- geq_012: 申請者情報の確認画面でチェックして次へ進む操作はどのページにあるか。
- geq_013: 本人確認のために利用者証明用電子証明書のパスワードを入力する手順を探す。
- geq_014: 申請できません画面が出る条件に関する記載を探したい。
