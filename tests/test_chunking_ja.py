from __future__ import annotations

from rag_core.chunking_ja import build_ja_chunk_records


def _by_role(rows, role: str):
    return [r for r in rows if r.get("chunk_role") == role]


def test_faq_glossary_chunking_creates_short_entry_children_with_parent_links():
    text = (
        "Q: 請求書IDはどこで確認できますか？\n"
        "A: 請求書IDは帳票ヘッダーの右上に記載されています。"
        "管理画面の請求明細からも同じIDを確認できます。\n\n"
        "返金条件: 返金条件は契約プランごとに異なります。"
        "返金申請フォーム送信後、審査結果はメールで通知されます。"
    )
    rows = build_ja_chunk_records(
        doc_id="faq-doc",
        source_doc="faq.pdf",
        text=text,
        doc_type="faq",
        title="FAQ",
        source_pages=[1],
    )

    parents = _by_role(rows, "parent")
    children = _by_role(rows, "child")
    assert len(parents) == 2
    assert len(children) == 2
    assert all(20 <= len(ch["display_text"]) <= 320 for ch in children)
    assert all(ch.get("parent_chunk_id") for ch in children)
    assert all("FAQ" in ch.get("searchable_text", "") for ch in children)
    for parent in parents:
        assert parent.get("child_chunk_ids")


def test_procedure_chunking_keeps_prerequisite_steps_and_notes_together():
    text = (
        "第1章 アカウント再設定\n"
        "前提: 管理者権限でログインし、対象ユーザーのメールアドレスを確認します。\n"
        "1. 管理画面の設定メニューを開きます。\n"
        "2. ユーザー管理から対象ユーザーを選択し、再設定を実行します。\n"
        "3. 完了画面で通知設定を確認します。\n"
        "注意: 再設定後は一時パスワードの有効期限が24時間です。"
    )
    rows = build_ja_chunk_records(
        doc_id="proc-doc",
        source_doc="ops.pdf",
        text=text,
        doc_type="procedure",
        title="運用手順",
        source_pages=[2],
    )

    children = _by_role(rows, "child")
    assert children
    child_blob = "\n".join(ch["display_text"] for ch in children)
    assert "前提" in child_blob
    assert "1." in child_blob
    assert "注意" in child_blob


def test_policy_chunking_uses_heading_based_sections():
    text = (
        "第1章 総則\n"
        "本規程は社内データ利用の基本方針を定めます。\n\n"
        "第2章 取り扱い\n"
        "個人情報を含むデータは利用目的を明示し、保管期間を定めて管理します。"
    )
    rows = build_ja_chunk_records(
        doc_id="policy-doc",
        source_doc="policy.pdf",
        text=text,
        doc_type="policy",
        title="データ利用規程",
        source_pages=[3],
    )

    parents = _by_role(rows, "parent")
    assert len(parents) == 2
    assert parents[0]["section_path"][0].startswith("第1章")
    assert parents[1]["section_path"][0].startswith("第2章")


def test_table_like_chunking_flattens_rows_with_title_and_headers():
    text = (
        "返金ルール一覧\n"
        "| 区分 | 条件 | 対応 |\n"
        "| A | 契約7日以内 | 全額返金 |\n"
        "| B | 契約30日以内 | 50%返金 |"
    )
    rows = build_ja_chunk_records(
        doc_id="table-doc",
        source_doc="table.pdf",
        text=text,
        doc_type="table",
        title="返金表",
        source_pages=[4],
    )

    children = _by_role(rows, "child")
    assert len(children) == 2
    assert "返金表" in children[0]["display_text"] or "返金ルール一覧" in children[0]["display_text"]
    assert "区分=A" in children[0]["display_text"]
    assert "対応=50%返金" in children[1]["display_text"]


def test_chunk_ids_are_unique_across_repeated_calls_for_same_doc_id_and_linkage_is_valid():
    # Simulate per-page/repeated-call ingestion where local section numbering restarts.
    text = (
        "Q: 申請期限はいつですか？\n"
        "A: 申請期限は月末です。\n\n"
        "Q: 問い合わせ先はどこですか？\n"
        "A: 管理部に連絡してください。"
    )
    page1 = build_ja_chunk_records(
        doc_id="same-doc",
        source_doc="same.pdf",
        text=text,
        doc_type="faq",
        title="FAQ",
        source_pages=[1],
        base_chunk_index=0,
    )
    page2 = build_ja_chunk_records(
        doc_id="same-doc",
        source_doc="same.pdf",
        text=text,
        doc_type="faq",
        title="FAQ",
        source_pages=[2],
        base_chunk_index=0,
    )
    rows = page1 + page2

    parents = _by_role(rows, "parent")
    children = _by_role(rows, "child")
    parent_ids = [r["id"] for r in parents]
    child_ids = [r["id"] for r in children]

    assert len(parent_ids) == len(set(parent_ids))
    assert len(child_ids) == len(set(child_ids))

    parent_set = set(parent_ids)
    child_set = set(child_ids)
    for child in children:
        assert child.get("parent_chunk_id") in parent_set

    linked_children = set()
    for parent in parents:
        cids = list(parent.get("child_chunk_ids") or [])
        assert cids
        for cid in cids:
            assert cid in child_set
            linked_children.add(cid)
    assert linked_children == child_set
