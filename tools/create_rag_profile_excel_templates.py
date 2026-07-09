#!/usr/bin/env python3
"""Create separate editable Excel templates for RAG project profiles."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font


DEFAULT_OUTPUT_DIR = Path("config/rag_profiles/volcano_demo/source")


WorkbookSpec = dict[str, Any]


WORKBOOKS: list[WorkbookSpec] = [
    {
        "filename": "01_profile.xlsx",
        "headers": ["key", "value", "description"],
        "rows": [
            ["project_id", "volcano_demo", "profile id"],
            ["display_name", "火山防災白書デモ", "人間向け表示名"],
            ["language", "ja", "回答言語"],
            ["default_answer_style", "bullet", "デフォルト回答形式"],
            ["max_answer_bullets", 5, "最大箇条書き数"],
            ["enable_generic_validation", "true", "汎用validationを使う"],
            ["enable_project_validation", "true", "profile validationを使う"],
            ["enable_profile_boost", "true", "profile boostを使う"],
            ["fallback_policy", "extractive", "validation失敗時のfallback"],
        ],
    },
    {
        "filename": "02_question_types.xlsx",
        "headers": [
            "type_id",
            "description",
            "question_contains_any",
            "question_contains_all",
            "priority",
            "enabled",
        ],
        "rows": [
            ["count_fact", "数値を聞く質問", "いくつ|何個|何件|何名|何年|何円|数", "", 100, True],
            ["definition", "定義を聞く質問", "とは|定義|意味|指す", "", 90, True],
            [
                "list_items",
                "必要物や項目を聞く質問",
                "何を|必要なもの|持っていく|持参|携行品|装備|一覧",
                "",
                90,
                True,
            ],
            ["procedure", "手順や方法を聞く質問", "方法|手順|どうする|流れ|進め方", "", 80, True],
            ["summary", "概要や要約を聞く質問", "概要|要約|まとめ|全体像|何について", "", 70, True],
            ["measure", "対策や施策を聞く質問", "対策|整備|取り組み|施策|実施|支援", "", 80, True],
            ["lesson", "教訓や示唆を聞く質問", "教訓|学び|示唆|反省|重要", "", 80, True],
            ["other", "その他", "", "", 0, True],
        ],
    },
    {
        "filename": "03_domain_terms.xlsx",
        "headers": [
            "term_id",
            "category",
            "term",
            "aliases",
            "weight",
            "negative_weight",
            "description",
            "enabled",
        ],
        "rows": [
            ["carry_helmet", "carry_items", "ヘルメット", "", 35, 0, "登山者の個人防災用品", True],
            ["carry_goggles", "carry_items", "ゴーグル", "", 35, 0, "登山者の個人防災用品", True],
            ["carry_mask", "carry_items", "マスク", "", 35, 0, "登山者の個人防災用品", True],
            ["carry_headlight", "carry_items", "ヘッドライト", "懐中電灯|ライト", 30, 0, "登山者の個人防災用品", True],
            [
                "wrong_speaker",
                "wrong_for_carry",
                "防災行政無線",
                "防災行政無線スピーカー",
                0,
                -25,
                "自治体設備であり個人の持ち物ではない",
                True,
            ],
            [
                "wrong_roof",
                "wrong_for_carry",
                "山小屋の屋根",
                "屋根補強|山小屋補強",
                0,
                -25,
                "施設整備であり個人の持ち物ではない",
                True,
            ],
            ["active_count", "active_volcano_count", "111", "111の活火山", 40, 0, "活火山数", True],
            ["jma_50", "jma_monitoring", "50火山", "常時観測火山", 30, 0, "気象庁監視対象", True],
            ["jma_24h", "jma_monitoring", "24時間", "24時間体制", 25, 0, "気象庁監視体制", True],
            ["local_shelter", "local_measures", "退避壕", "避難施設|シェルター", 30, 0, "地方公共団体の火山防災対策", True],
        ],
    },
    {
        "filename": "04_synonyms.xlsx",
        "headers": ["canonical", "synonyms", "description", "enabled"],
        "rows": [
            ["防災用品", "防災グッズ|携行品|装備|持参物|持っていくもの", "同義語", True],
            ["登山届", "登山計画書|届出|オンライン提出", "同義語", True],
            ["活火山", "活動火山|火山", "表記揺れ", True],
            ["噴火警戒レベル", "警戒レベル|噴火警報|噴火予報", "関連語", True],
        ],
    },
    {
        "filename": "05_retrieval_boost_rules.xlsx",
        "headers": [
            "rule_id",
            "applies_to_question_type",
            "when_question_contains_any",
            "positive_categories",
            "positive_terms",
            "positive_weight",
            "negative_categories",
            "negative_terms",
            "negative_weight",
            "enabled",
        ],
        "rows": [
            [
                "carry_items_rule",
                "list_items",
                "防災用品|持っていく|持参|装備",
                "carry_items",
                "噴石|外傷性ショック|頭や体",
                35,
                "wrong_for_carry",
                "避難路|ロープ設置|標識|パトロール員",
                -25,
                True,
            ],
            ["active_count_rule", "count_fact", "活火山|いくつ|数", "active_volcano_count", "", 40, "", "", -10, True],
            [
                "jma_monitoring_rule",
                "measure",
                "気象庁|監視|観測",
                "jma_monitoring",
                "24時間|地震計|監視カメラ|噴火警戒レベル",
                30,
                "",
                "",
                -10,
                True,
            ],
            [
                "local_measure_rule",
                "measure",
                "地方公共団体|自治体|整備|対策",
                "local_measures",
                "退避壕|避難施設|避難促進施設|防災行政無線|ビジターセンター",
                30,
                "",
                "",
                -10,
                True,
            ],
        ],
    },
    {
        "filename": "06_validation_rules.xlsx",
        "headers": [
            "rule_id",
            "applies_to_question_type",
            "when_question_contains_any",
            "answer_must_contain_any",
            "answer_must_contain_all",
            "answer_should_not_contain_any",
            "fallback_if_failed",
            "enabled",
        ],
        "rows": [
            [
                "carry_items_validation",
                "list_items",
                "防災用品|持っていく|持参|装備",
                "ヘルメット|ゴーグル|マスク|ヘッドライト|懐中電灯",
                "",
                "防災行政無線|山小屋の屋根|避難路|ロープ設置",
                True,
                True,
            ],
            ["active_count_validation", "count_fact", "活火山|いくつ|数", "111", "", "", True, True],
            [
                "jma_monitoring_validation",
                "measure",
                "気象庁|監視|観測",
                "50火山|24時間|常時観測火山|噴火警戒レベル",
                "",
                "",
                True,
                True,
            ],
            [
                "local_measure_validation",
                "measure",
                "地方公共団体|自治体|整備|対策",
                "退避壕|避難施設|避難促進施設|防災行政無線|ビジターセンター",
                "",
                "",
                True,
                True,
            ],
        ],
    },
    {
        "filename": "07_answer_templates.xlsx",
        "headers": ["template_id", "applies_to_question_type", "format", "instruction", "max_bullets", "enabled"],
        "rows": [
            [
                "list_items_template",
                "list_items",
                "bullet",
                "必要なものを箇条書きで答える。施策や設備整備ではなく、質問者が準備する物を優先する。",
                5,
                True,
            ],
            [
                "count_fact_template",
                "count_fact",
                "direct_then_reason",
                "最初に数値で直接答え、その後に根拠を1〜2点示す。",
                3,
                True,
            ],
            ["definition_template", "definition", "bullet", "定義を最初に述べ、補足を続ける。", 5, True],
            ["measure_template", "measure", "bullet", "実施主体と取り組み内容が分かるように答える。", 5, True],
            ["summary_template", "summary", "bullet", "重要論点を3〜5点に整理する。", 5, True],
        ],
    },
    {
        "filename": "08_golden_qa.xlsx",
        "headers": [
            "case_id",
            "question",
            "expected_all",
            "expected_any",
            "forbidden_any",
            "expected_citation",
            "expected_question_type",
            "priority",
            "enabled",
        ],
        "rows": [
            [
                "q003_active_volcano_count",
                "日本には令和6年4月時点でいくつの活火山がありますか。",
                "111|活火山",
                "",
                "",
                True,
                "count_fact",
                100,
                True,
            ],
            [
                "q006_items_to_carry",
                "登山者が火山に登るときに持っていくべき防災用品は何ですか。",
                "",
                "ヘルメット|ゴーグル|マスク|ヘッドライト|懐中電灯",
                "防災行政無線|山小屋の屋根|避難路",
                True,
                "list_items",
                100,
                True,
            ],
            [
                "q007_jma_monitoring",
                "気象庁は活火山をどのように監視していますか。",
                "",
                "50火山|24時間|地震計|監視カメラ|噴火警戒レベル",
                "",
                True,
                "measure",
                90,
                True,
            ],
            [
                "q008_local_government_measures",
                "地方公共団体は御嶽山噴火の教訓を踏まえてどのような火山防災対策を行っていますか。",
                "",
                "退避壕|避難施設|防災行政無線|ビジターセンター|避難促進施設",
                "",
                True,
                "measure",
                90,
                True,
            ],
        ],
    },
]


def display_width(value: Any) -> int:
    if value is None:
        return 0
    width = 0
    for char in str(value):
        width += 2 if ord(char) > 127 else 1
    return width


def set_readable_column_widths(ws) -> None:
    for column_cells in ws.columns:
        column_letter = column_cells[0].column_letter
        max_width = max(display_width(cell.value) for cell in column_cells)
        ws.column_dimensions[column_letter].width = min(max(max_width + 2, 12), 60)


def create_workbook(path: Path, headers: list[str], rows: list[list[Any]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "data"

    ws.append(headers)
    for row in rows:
        ws.append(row)

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    set_readable_column_widths(ws)

    wb.save(path)


def create_templates(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    created_paths = []
    for spec in WORKBOOKS:
        path = output_dir / spec["filename"]
        create_workbook(path, spec["headers"], spec["rows"])
        created_paths.append(path)
    return created_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create separate Excel profile templates for the volcano demo RAG profile."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for Excel files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    created_paths = create_templates(args.output_dir)
    for path in created_paths:
        print(path)


if __name__ == "__main__":
    main()
