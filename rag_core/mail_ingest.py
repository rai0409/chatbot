#!/usr/bin/env python3
"""メールの取り込み・構造化・ノイズ除去・会話の流れ（chatbot の取り込み層の拡張。1 ファイルで完結）。

■ 何をするか
eml・CSV・Excel のメールから、
  1. 日付・From・To・Cc・件名などのメタ情報を、項目ごとに取り出してまとめる（messages.csv）
  2. ヘッダ・署名・宛名・免責・定型文・区切り線など、要らない行を除く（消さずに removed.csv へ退避する）
  3. 引用を、過去メール 1 通ずつに分ける。手元のメールと重複するものは参照に置き換え、
     手元に無いものは「復元したメール」として残す。インライン返信の直前の引用は、文脈として残す
  4. スレッド（会話の木）を作り、誰から誰へ、どう進んだかを、時刻順の表にする（conversation.csv）
msg は、eml に変換してから入れる（このモジュールは msg を読まない）。

■ 行の分類（find_noise を分析して、作り直したもの。find_noise のコードは呼ばない）
find_noise の設計（行頻度・正規表現・位置・署名ゾーン・前後の行・文字 n-gram の LR を LightGBM に重ねる）を
引き継ぎ、次を変えた。効果は `evaluate` で、同じ条件の改良前（--feature-set base）と比べる。
  1. 実名の一覧（名字・地名の一覧）を使わない。名前らしさ・住所らしさは、形（パターン）で取る
  2. 構造の特徴量（plus）: 引用の分割（下記）で分かる「何通目の過去メールか」「その 1 通の末尾からの距離」
     「ヘッダの塊の中か」「引用の帰属行か」・名前らしい行・住所らしい行・パスワード・機械通知の語
  3. 行の並びの平滑化: 署名・免責は連続した行の塊なので、ラベルの遷移確率（学習データから推定）で
     前向き後向きアルゴリズムをかけ、孤立した 1 行の判定を周りに寄せる（重み 0 で無効）
  4. 文単位の判定（任意）: 挨拶と本文が 1 行に同居する行で、挨拶の文だけを落とす
  5. 用途の設定（profile）: training（学習データ用。find_noise と同じ強制削除）／
     evidence（不正検知の証拠用。日時だけの行・お礼や詫びの行を、規則では消さない）
  6. 評価の漏れを除く: 行頻度を「学習側のメールだけ」から作る unseen と、全件から作る batch を、
     分けて測る。分割の単位は、送信者（既定）またはメール
  7. モデルのファイルは、モデルのフォルダの外に保管した SHA-256 の一覧と照合し、照合したバイト列から読む
     （joblib は pickle 形式のため。フォルダの中の一覧では差し替えを防げない）

■ v2 の追加（学習データが少ない前提で、精度を優先。無駄な遅さは入れない）
  8. 日本語の特徴量（--feature-set ja）: 文字の種類の割合・切り替わりの回数・文末の形（です・ます等）・全角半角の揺れ・
     助詞の割合、Sudachi が入っていれば品詞の割合（名詞・固有名詞・助詞・助動詞・動詞・記号）。行ごとの値は
     同じ行の再計算を避けるため、行の文字列ごとにキャッシュする
  9. 埋め込みの重ね（--embed-model）: 小さな日本語の埋め込みモデル（cl-nagoya/ruri-v3-30m。Apache-2.0）で行を
     ベクトルにし（学習しない。接頭辞は分類用の「トピック: 」）、その上の小さな MLP（非線形）の 9 クラスの確率を、
     n-gram の LR と同じく、交差検証の予測（OOF）として LightGBM に重ねる。埋め込みは、正規化した行ごとに 1 回だけ
     計算し、任意でファイルにキャッシュする（キーは SHA-256 で、行の文字列はファイルに書かない）
  10. 行の並びのモデル（--seq）: BiGRU（双方向 GRU。非線形）が、1 通の行の並び全体を見て、LightGBM の確率を補正する
     （LightGBM の対数確率に足す残差の形なので、学習が足りないときは LightGBM の判定に戻る）。前後平滑化の置き換え。
     学習は torch、推論は numpy だけで行う（重みは npz。pickle を使わない）
  11. 運用: 1 通ごとの失敗の隔離（failed.csv・messages.csv の status。分類に失敗したメールは、何も消さずに残す）、
     特徴量を配列で持つ（メモリ）、--jobs で特徴量の計算を並列化、CSV の数式の無害化、
     モデルに部品・ライブラリの版・Sudachi の有無・埋め込みモデルの識別値を記録し、run で食い違えば止める
  採用は、evaluate で、同じ条件の改良前と比べて決める（本文の誤削除が増えず、再現率が上がるときだけ）。

■ 日時
  時差の無い日時（CSV・本文のヘッダ）は、日本時間とみなして比べる（messages.csv の date_utc）。
  日付だけの値は、同じ日の時刻付きの値とは前後を決めない（並べるときは、その日の最後）。

■ 使い方（出力は、すべて UTF-8 BOM 付き CSV。原文を含むので、コミットしない）
  学習:  python rag_core/mail_ingest.py train    --gold-dir <find_noise>/work --out <モデルのフォルダ>
  評価:  python rag_core/mail_ingest.py evaluate --gold-dir <find_noise>/work [--group sender|mail] [--seeds 3]
  実行:  python rag_core/mail_ingest.py run --input <eml のフォルダ | .eml | .csv | .xlsx> --out <出力フォルダ>
              --model <モデルのフォルダ> --model-sums <別に保管した SHA256SUMS>
              [--profile evidence|training] [--col text=本文 --col from=差出人 ...]
         ノイズ除去なし（構造化・引用・スレッドだけ）: --model を付けずに --no-noise
  精度優先の学習（推奨。評価で効果を確かめてから使う）:
         python rag_core/mail_ingest.py train --gold-dir <find_noise>/work --out <モデル> --feature-set ja
              --embed-model <ruri-v3-30m のフォルダ> --seq
         run には、同じ埋め込みモデルのフォルダを --embed-model で渡す（識別値が学習時と違えば止まる）
  比較の評価: python rag_core/mail_ingest.py evaluate --gold-dir <find_noise>/work --feature-sets plus ja
              --embed-model <ruri-v3-30m のフォルダ> --seq --stats unseen
学習・評価・ノイズ除去には numpy・scikit-learn・lightgbm・joblib が要る（評価・学習のゴールドの読み込みには pandas・pyarrow も）。
品詞の特徴量には sudachipy・sudachidict_core、埋め込みには sentence-transformers、--seq の学習には torch が要る
（--seq のモデルの推論は numpy だけ）。埋め込みモデルは、インターネットに出ずに、ローカルのフォルダから読む。
構造化・引用・スレッドだけなら、標準ライブラリだけで動く（xlsx は chatbot の読み込み器か openpyxl）。
"""

from __future__ import annotations

import argparse
import csv
import functools
import hashlib
import importlib
import json
import multiprocessing
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence

VERSION = "mail-ingest-v2"

LABELS = ("body", "sig", "addr", "disc", "qhdr", "sep", "tmpl", "greet", "pw")
REMOVE = LABELS[1:]
LABEL_INDEX = {c: i for i, c in enumerate(LABELS)}


# =============================================================================
# 1. 正規化
# =============================================================================
# 本文は 3 段で持つ:
#   表示用（display_text）  … Excel の制御文字表記・改行コード・見えない文字だけを直す。証拠として見せる本文
#   行の分割用（_line_text） … 表示用 ＋ 特殊空白を半角空白に、倍化した改行を半分に（Outlook 由来のコーパス）
#   照合用（analysis / key） … NFKC・ダッシュ類の統一・数字を 0・空白と引用符の除去。判定と頻度の集計にだけ使う

_XML_ESCAPE = re.compile(r"(?:_x000[Dd]_)(?:\r?\n)?|_x([0-9A-Fa-f]{4})_")
_INVISIBLE = re.compile("[​‌‍⁠﻿­]")
_SPACES = re.compile("[\xa0　\t\v\f]")
_NL_RUN = re.compile(r"\n+")
_DASHES = str.maketrans({ch: "-" for ch in "‐‑‒–—―−"})
RE_QUOTE_MARK = re.compile(r"^\s*(?:[>|＞]+\s*)+")     # 照合用（行頻度のキー・n-gram）
RE_QUOTE_GT = re.compile(r"^\s*(?:[>＞]+\s*)+")        # 引用の深さ・本文の切り出し用


def decode_xml_escapes(text: str) -> str:
    """「_x000D_」などの、Excel が書いた制御文字の表記を戻す（CR・LF は改行、タブは空白、他の制御文字は除く）。"""
    if "_x" not in text:
        return text

    def replace(match: re.Match) -> str:
        code = match.group(1)
        if code is None:
            return "\n"
        value = int(code, 16)
        if value in (0x0A, 0x0D):
            return "\n"
        if value == 0x09:
            return " "
        if value < 0x20:
            return ""
        return match.group(0)

    return _XML_ESCAPE.sub(replace, text)


def display_text(text: str | None) -> str:
    if not text:
        return ""
    text = decode_xml_escapes(str(text))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _INVISIBLE.sub("", text)


def _halve_newlines(text: str) -> str:
    """改行の連が偶数に偏っている文書（改行が 2 倍化された Outlook 由来）だけ、連を半分にする。"""
    runs = [m.end() - m.start() for m in _NL_RUN.finditer(text)]
    multi = [r for r in runs if r >= 2]
    if len(multi) < 3 or len(multi) < 0.8 * len(runs):
        return text
    if sum(1 for r in multi if r % 2 == 0) < 0.9 * len(multi):
        return text
    return _NL_RUN.sub(lambda m: "\n" * max(1, (m.end() - m.start()) // 2), text)


def _line_text(text: str) -> str:
    text = _SPACES.sub(" ", display_text(text))
    text = _halve_newlines(text)
    return "\n".join(ln.rstrip() for ln in text.split("\n"))


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_DASHES)


def line_key(text: str) -> str:
    """同じ行の照合キー（行頻度の集計に使う）。引用符・空白を落とし、NFKC・小文字・数字を 0 にそろえる。"""
    t = nfkc(RE_QUOTE_MARK.sub("", text)).lower()
    t = re.sub(r"\d", "0", t)
    return re.sub(r"\s+", "", t)


def fold_for_match(text: str) -> str:
    """引用の照合用。NFKC・小文字・数字以外の記号と空白を落とす（改行・引用符・句読点の違いを無視する）。"""
    t = nfkc(text).lower()
    return re.sub(r"[\W_]+", "", t)


# =============================================================================
# 2. 行
# =============================================================================
_SEG_SPLIT = re.compile(r"(?<=[。！？!?])(?![）」』】\s])")


@dataclass(slots=True)
class Line:
    text: str            # 前後の空白を落とした行（引用符を含む）
    idx: int             # 空行を除いた通し番号
    blanks_before: int   # 直前の空行の数
    depth: int           # 引用の深さ（行頭の > | ＞ の個数）
    body: str            # 引用符を剥がした行
    nb: str              # body を NFKC にしたもの（判定用）


def split_lines(text: str) -> list[Line]:
    """本文を非空行のリストにする。空行は blanks_before に畳む（find_noise の行番号と同じ数え方）。"""
    out: list[Line] = []
    blanks = 0
    for raw in _line_text(text).split("\n"):
        s = raw.strip()
        if not s:
            blanks += 1
            continue
        # 引用の深さは「>」「＞」だけで数える。行頭の「|」は表の罫線のことが多く、引用とみなすと表が引用として抜き出される
        # （照合用のキー line_key では、find_noise と同じく「|」も落とす）
        m = RE_QUOTE_GT.match(s)
        depth = sum(m.group(0).count(c) for c in ">＞") if m else 0
        body = RE_QUOTE_GT.sub("", s).strip() if m else s
        out.append(Line(text=s, idx=len(out), blanks_before=blanks, depth=depth, body=body, nb=nfkc(body)))
        blanks = 0
    return out


def split_sentences(line: str) -> list[str]:
    parts = [p for p in (s.strip() for s in _SEG_SPLIT.split(line)) if p]
    return parts or [line]


# =============================================================================
# 3. 正規表現（find_noise の分析から。実名の一覧は使わず、形で取る）
# =============================================================================
RE_DECO = re.compile(r"[*_]+")
RE_ORIG_MSG = re.compile(
    r"^-{2,}\s*(original\s+message|original\s+appointment|forwarded\s+message|元のメッセージ|"
    r"返信元のメッセージ|転送されたメッセージ|転送メッセージ)\s*-{2,}", re.I)
RE_UNDERSCORE_LINE = re.compile(r"^_{8,}$")
RE_WROTE = re.compile(r"(wrote:|さんは書きました[:：]?|が書きました[:：]?|のメッセージ[:：])\s*$", re.I)
RE_QUOTE_STAMP = re.compile(
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?\s*(?:[(（][月火水木金土日][)）])?\s*"
    r"\d{1,2}:\d{2}\s*[^@]{0,60}?[\w.+-]+@[\w-]+\.[\w.-]+")
RE_ON_WROTE = re.compile(r"^on\s.{6,200}\swrote:?$", re.I)

# ヘッダ項目。ラベル → 正規化した項目名（長い順に当てる。「送信」が「送信日時」を食わないように）
_FIELD_LABELS = {
    "from": ("from", "sender", "差出人", "送信者", "送信元", "発信者", "発信元"),
    # 「日時」は入れない（会議案内の本文の「日時：…」をヘッダと誤るため）
    "date": ("sent", "date", "送信日時", "送信日", "送信時刻", "送信時間", "送信", "受信日時", "発信日時", "発信日"),
    "to": ("to", "宛先", "宛て先", "受信者"),
    "cc": ("cc",),
    "bcc": ("bcc",),
    "subject": ("subject", "件名", "題名", "表題"),
    "reply_to": ("reply-to",),
    "attach": ("添付ファイル", "attachments", "attachment"),
}
_LABEL_TO_FIELD = {lab: f for f, labs in _FIELD_LABELS.items() for lab in labs}
RE_HDR_FIELD = re.compile(
    r"^\s*(" + "|".join(sorted(map(re.escape, _LABEL_TO_FIELD), key=len, reverse=True)) + r")"
    r"(?:\s*[(（][^)）]{0,12}[)）])?\s*[:：]\s*(.*)$", re.I)
ADDR_FIELDS = {"from", "to", "cc", "bcc", "reply_to"}

RE_ZIP = re.compile(r"〒\s*\d{3}-?\d{4}|\b\d{3}-\d{4}\b")
RE_TEL = re.compile(r"(tel|phone|電話|℡|代表|直通|内線|ext|mobile|携帯|携)\s*[:：.]?\s*[+(]?\d[\d\s()+\-.]{6,}", re.I)
RE_FAX = re.compile(r"(fax|f\s*a\s*x)\s*[:：.]?\s*[+(]?\d", re.I)
RE_PHONE_BARE = re.compile(r"(?<!\d)(0\d{1,4}[-(]\d{1,4}[-)]\d{3,4}|\+\d{2,3}[-\s]\d{1,4}[-\s]\d{3,4}[-\s]\d{3,4})(?!\d)")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
RE_URL = re.compile(r"https?://|www\.", re.I)
RE_CORP = re.compile(
    r"株式会社|有限会社|合同会社|\(株\)|㈱|一般社団法人|一般財団法人|公益財団法人|公益社団法人|独立行政法人|"
    r"信用金庫|信用組合|銀行|証券|保険|生命|損保|商事|物産|工業|製作所|ホールディングス|"
    r"co\.,?\s*ltd|corporation|inc\.|k\.k\.|llc", re.I)
RE_DEPT = re.compile(r"(本部|部門|部|課|室|センター|グループ|チーム|支店|営業所|支社|事務局|担当|係|ユニット)$")
RE_TITLE = re.compile(
    r"(会長|社長|副社長|専務|常務|取締役|執行役員|理事長|理事|監事|本部長|部長|次長|課長|室長|支店長|所長|係長|"
    r"主任|主査|調査役|担当者|代表|マネージャ|リーダー|ディレクター|president|manager|director)", re.I)
RE_HONORIFIC = re.compile(r"(様|殿|御中|各位|さん|先生)\s*$")
RE_SAMA_LINE = re.compile(r"^.{0,24}(様|殿|御中|各位)\s*[:：]?\s*$")
RE_GREET_OPEN = re.compile(r"お世話になって|お世話になります|お世話になり|お疲れ様|お疲れさま|ご無沙汰|初めまして|はじめまして|突然のご連絡")
RE_GREET = re.compile(
    r"お世話になって|お世話になります|お疲れ様|お疲れさま|ご無沙汰|よろしくお願い|宜しくお願い|よろしくおねがい|"
    r"お願い申し上げ|ありがとうございま|御礼申し上げ|失礼いたし|失礼します|ご連絡いただき|ご確認のほど|ご査収|"
    r"取り急ぎ|引き続き|以上、|以上です|お手数|恐れ入り|恐縮")
RE_GREET_ONLY = re.compile(
    r"^(以上|以上です|よろしくお願い(いた)?します|宜しくお願い(いた)?します|よろしくお願い申し上げます|"
    r"引き続き(どうぞ)?よろしくお願い(いた)?します|お疲れ様です|お疲れさまです|お世話になっております|"
    r"いつもお世話になっております|大変お世話になっております|ありがとうございます|ありがとうございました|"
    r"承知いたしました|承知しました|了解(いた)?しました)[。!！]?$")
# 名乗り（「株式会社サンプルの山田です。」）。読点・句点をまたがない（「単価の件、了解です。」を名乗りと誤らない）。
# 名前の部分は、空白を 1 つまで含む 1〜8 字で、「件・こと・方・旨・通り・予定」のような名前でない語で終わらない
RE_INTRO = re.compile(r"^[^、。,]{1,30}?の(?![^、。,]*(?:件|こと|事|方|旨|通り|予定|ため|為))"
                      r"[^、。,\s]{1,8}(?:\s[^、。,\s]{1,6})?(です|でございます|と申します)[。!！]?$")
RE_THANKS = re.compile(r"ありがとうございま|御礼申し上げ|お礼申し上げ|申し訳(ござい|あり)ません|失礼いたしました|お詫び")
RE_DISCLAIM = re.compile(
    r"本メール|このメール|当メール|本電子メール|送信専用|自動配信|自動的に送信|配信停止|配信専用|購読|unsubscribe|"
    r"confidential|privileged|機密情報|守秘|無断|転載|複製|破棄|削除してください|心当たり|誤って受信|受信された場合|"
    r"著作権|copyright|all rights reserved|個人情報保護|プライバシーポリシー|お問い合わせ", re.I)
RE_NOTICE = re.compile(r"自動(送信|配信|通知)|送信専用|返信(は)?(できません|不要)|ご返信いただいても|システム(より|から)|通知メール", re.I)
RE_PW = re.compile(r"パスワード|password|passcode|解凍|暗号化|zip|復号", re.I)
_SEP_CHARS = "-=＝─━*＊#・.。‥…~〜_＿+|＋★☆■□◆◇▲▼※▽△○●◎ー"
RE_SEP_RUN = re.compile("([" + re.escape(_SEP_CHARS) + r"])\1{3,}")
RE_SYMBOL_ONLY = re.compile("^[\\s" + re.escape(_SEP_CHARS + "<>＜＞[]()（）") + "]+$")
RE_NAME_LATIN = re.compile(r"^[A-Z][a-z]+,?\s+[A-Z][a-z]+")
# 名前らしい行（一覧を使わず、形で取る）: 漢字だけの 2〜8 字（間に空白 1 つまで）・カタカナの姓名・ローマ字併記
RE_NAME_LIKE = re.compile(r"^[一-龥々]{1,4}\s?[一-龥々]{1,4}$|^[ァ-ヶー]{2,8}\s[ァ-ヶー]{2,8}$")
RE_NAME_PAREN = re.compile(r"[一-龥々]{1,4}\s?[一-龥々]{1,4}\s*[(（][A-Za-z][A-Za-z .,-]{2,40}[)）]")
# 住所らしさ（形で取る）: 都道府県の語尾 → 市区町村の語尾、丁目・番地・階・ハイフン 3 連の番地
RE_ADDRESS = re.compile(r"[都道府県][^\s、。]{1,12}?[市区町村郡]|\d+丁目|番地|\d+-\d+-\d+|\d+(階|F)\b|ビル\s*\d")
RE_DATETIME = re.compile(
    r"\d{4}[/年.-]\d{1,2}[/月.-]\d{1,2}日?|\d{1,2}[/月]\d{1,2}日?|\d{1,2}[:時]\d{2}分?|"
    r"[(（][月火水木金土日][)）]|\d{8}\s*\d{4}")
RE_DT_WORD = re.compile(r"日時|日付|時間|開始|終了|予定|曜日|午前|午後|[~〜\-～/.:年月日時分()（）\s]")
RE_CONTACT_LABEL = re.compile(r"(tel|fax|e-?mail|mail|url|phone|mobile|電話|携帯|直通|代表|内線|住所|所在地|〒)\s*[:：]?", re.I)


def is_sep_line(nb: str) -> bool:
    return bool(RE_SEP_RUN.search(nb)) or (bool(RE_SYMBOL_ONLY.match(nb)) and len(nb) >= 3)


def hdr_field(nb: str) -> tuple[str, str]:
    """ヘッダ項目の行なら (項目名, 値)。違えば ("", "")。"""
    m = RE_HDR_FIELD.match(RE_DECO.sub("", nb))
    if not m:
        return "", ""
    return _LABEL_TO_FIELD[m.group(1).lower()], m.group(2).strip()


def ends_addr_run(nb: str) -> bool:
    """宛先一覧の折り返しが、ここで終わるか（本文・宛名・区切りが始まった）。"""
    return (nb == "" or is_sep_line(nb) or bool(RE_SYMBOL_ONLY.match(nb)) or "。" in nb
            or bool(RE_SAMA_LINE.match(nb)) or nb.endswith(("さん", "さん、")) or nb.startswith("※"))


def contact_kind(nb: str) -> str:
    """連絡先・日時だけの行の種別（email/url/zip/tel/addr の + 連結、datetime、引用の帰属行なら qhdr）。違えば空。"""
    t = nb.strip()
    if not t or "。" in t or len(t) > 300:
        return ""
    if RE_QUOTE_STAMP.match(t) and re.search(r"(?::|wrote:?)$", t):
        return "qhdr"
    kinds = [k for k, r in (("email", RE_EMAIL), ("url", RE_URL), ("zip", RE_ZIP), ("tel", RE_PHONE_BARE))
             if r.search(t)]
    if RE_TEL.search(t) and "tel" not in kinds:
        kinds.append("tel")
    rest = t
    for r in (RE_EMAIL, RE_PHONE_BARE, RE_TEL, RE_FAX, RE_ZIP, RE_CONTACT_LABEL):
        rest = r.sub(" ", rest)
    rest = re.sub(r"https?://\S+|www\.\S+", " ", rest)
    rest = re.sub(r"[\W_]", "", rest)
    if kinds and len(rest) <= 2 and not re.search(r"[A-Za-z]{2}", rest):
        return "+".join(kinds)
    head = RE_CONTACT_LABEL.sub("", RE_ZIP.sub("", t)).lstrip(" :：")
    if len(t) <= 60 and RE_ADDRESS.search(head) and not re.search(r"[ぁ-ん]{2,}|[、を]", head):
        return "+".join([*kinds, "addr"])
    if len(t) <= 80 and "%" not in t and RE_DATETIME.search(t):
        rest = re.sub(r"[\W_\d]", "", RE_DT_WORD.sub("", RE_DATETIME.sub("", t)))
        if len(rest) <= 1:
            return "datetime"
    return ""


def greet_kind(nb: str) -> str:
    """挨拶の定型句だけの行の種別（greet.phrase / greet.intro / greet.thanks）。違えば空。"""
    t = nb.strip()
    if not t:
        return ""
    if RE_GREET_ONLY.match(t):
        return "greet.phrase"
    if len(t) <= 40 and RE_INTRO.match(t) and not re.search(r"\d", t):
        return "greet.intro"
    if len(t) <= 40 and RE_THANKS.search(t) and not re.search(r"\d|件|について|ため", t):
        return "greet.thanks"
    return ""


def subject_line(nb: str) -> bool:
    return hdr_field(nb)[0] == "subject"


# 英字の語は、前後が英字でないときだけ当てる（「ok」が「Tokyo」「Facebook」に当たらないように。\b は日本語の文字も
# 語の文字とみなすので「OKです」に当たらない。そのため英字だけを見る）
RE_CHAT = re.compile(
    r"(はい|いえ|ええ|うん|そう(です|ですね)|わかりました|分かりました|了解|(?<![A-Za-z])ok(?![A-Za-z])|大丈夫|できます|できません|可能|不可|"
    r"いいです|結構です|至急|\?)", re.I)
# 数字・@・:・/ を含む行は、会話文でなく、署名・連絡先・日時の形（「対応可能時間 9:00-18:00」）
RE_NOT_CHAT = re.compile(r"[\d@:/]")


def protected_chat(nb: str) -> bool:
    """短い会話文（相槌・返事）は、頻度が高くても消さない。返信の連鎖では、それが唯一の内容になる。"""
    return (len(nb) < 20 and bool(RE_CHAT.search(nb)) and not RE_GREET_ONLY.match(nb)
            and not RE_NOT_CHAT.search(nb))


# =============================================================================
# 4. 引用の分割（1 通を「新しく書かれた部分」と「引用された過去メール 1 通ずつ」に分ける）
# =============================================================================
@dataclass
class Address:
    name: str = ""
    addr: str = ""

    def label(self) -> str:
        if self.name and self.addr:
            return f"{self.name} <{self.addr}>"
        return self.name or self.addr

    def key(self) -> str:
        return (self.addr or self.name).strip().lower()


@dataclass
class Meta:
    date_iso: str = ""
    date_raw: str = ""
    date_conf: str = ""          # high（構造）/ mid（日時を解析）/ low（日付だけ・推定）/ 空
    sender: Address = field(default_factory=Address)
    to: list[Address] = field(default_factory=list)
    cc: list[Address] = field(default_factory=list)
    bcc: list[Address] = field(default_factory=list)
    subject: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    origin: str = ""             # eml / columns / body（本文のヘッダから）/ attribution（帰属行から）

    def empty(self) -> bool:
        return not (self.date_raw or self.sender.label() or self.to or self.subject)


@dataclass
class Unit:
    """1 通ぶんの部分。kind = new（新しく書かれた部分）/ quoted（引用された過去メール）。"""
    kind: str
    lines: list[int]                  # 中身の行（ヘッダの行を除く）
    header_lines: list[int] = field(default_factory=list)
    meta: Meta = field(default_factory=Meta)
    context_lines: list[int] = field(default_factory=list)   # new の中の、インライン返信の文脈（引用行）


def _split_address_list(value: str) -> list[str]:
    """「;」「,」で宛先を分ける。引用符と <> の中の区切りは無視する。"""
    parts, buf, quote, angle = [], [], False, 0
    for ch in value.replace("；", ";").replace("，", ","):
        if ch == '"':
            quote = not quote
        elif ch == "<":
            angle += 1
        elif ch == ">":
            angle = max(0, angle - 1)
        if ch in ";," and not quote and angle == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


_RE_MAILTO = re.compile(r"^(.*?)\s*[\[(<]\s*(?:mailto:)?([\w.+-]+@[\w-]+\.[\w.-]+)\s*[\])>]\s*$", re.I)


def parse_addresses(value: str) -> list[Address]:
    out: list[Address] = []
    for part in _split_address_list(display_text(value).replace("\n", " ")):
        m = _RE_MAILTO.match(part)
        if m:
            name, addr = m.group(1), m.group(2)
        else:
            name, addr = parseaddr(part)
            if "@" not in addr:
                bare = RE_EMAIL.search(part)
                name, addr = (part.replace(bare.group(0), ""), bare.group(0)) if bare else (part, "")
        name = name.strip().strip('"').strip("'").strip()
        out.append(Address(name=name, addr=addr.strip().lower()))
    return out


_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")
_MONTHS = {**{m: i for i, m in enumerate(_MONTH_NAMES, 1)}, **{m[:3]: i for i, m in enumerate(_MONTH_NAMES, 1)},
           "sept": 9}
_RE_YMD = re.compile(r"(\d{4})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?")
_RE_WAREKI = re.compile(r"(令和|平成)\s*(\d{1,2}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_RE_EN_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})")     # November 19, 2026 / Jan 2, 2024
_RE_EN_DMY = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})")      # 2 January 2024（イギリス式）
_RE_TIME = re.compile(r"(午前|午後|AM|PM)?\s*(\d{1,2})\s*[:時]\s*(\d{2})?(?:\s*[:分]\s*(\d{2}))?\s*(AM|PM)?", re.I)
_RE_AMPM = re.compile(r"\b(AM|PM)\b|午前|午後", re.I)
_ERA_BASE = {"令和": 2018, "平成": 1988}
_RE_RFC2822 = re.compile(r"^(?:[A-Za-z]{3},\s*)?\d{1,2}\s+[A-Za-z]{3}\s+\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?\s*"
                         r"(?:[+-]\d{4}|[A-Z]{1,5})?(?:\s+[A-Z]{1,5})?(?:\s*\(.*\))?$")
# 時差の無い日時（CSV・本文のヘッダ）は、日本時間とみなす（比較用の UTC を作るときだけ。出力の date は元の表記の ISO のまま）
DEFAULT_TZ = timezone(timedelta(hours=9))


_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?$")
_RE_TZ_AFTER = re.compile(r"^\s*(Z|[+-]\d{2}:?\d{2}|GMT|UTC|JST)\b", re.I)


def _iso_tz_fix(v: str) -> str:
    """fromisoformat が読める形にする（末尾の Z → +00:00、+0900 → +09:00、時刻と時差の間の空白を詰める）。"""
    v = re.sub(r"\s+(?=[+-]\d{2}:?\d{2}$|Z$)", "", v)
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)


def _tz_of(token: str) -> timezone:
    t = token.upper()
    if t in ("Z", "GMT", "UTC"):
        return timezone.utc
    if t == "JST":
        return DEFAULT_TZ
    sign = -1 if t[0] == "-" else 1
    digits = t[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4])))


def _rfc2822(v: str) -> str:
    try:
        dt = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return ""
    return dt.isoformat() if dt is not None else ""


def parse_date(value: str) -> tuple[str, str]:
    """日付の文字列を (ISO 8601, 確度) にする。RFC 2822（eml）・和文・和暦・英文（米式・英式・Gmail の帰属行）に対応。
    読めなければ ("", "")。確度は high（RFC 2822）/ mid（日時を解析）/ low（日付だけ）。"""
    v = nfkc(display_text(value)).strip()
    if not v:
        return "", ""
    # ISO 8601（CSV の書き出しに多い。Z・+0000・+09:00 の時差を捨てずに読む）
    if _RE_ISO.match(v):
        try:
            dt = datetime.fromisoformat(_iso_tz_fix(v.replace(" ", "T", 1)))
            return dt.isoformat(), "high" if dt.tzinfo else "mid"
        except ValueError:
            pass
    # RFC 2822 の形のときは、標準の解析を使う。標準の解析は寛容で、「… 10:30 PM」の午後を落として読むので、
    # 午前・午後の表記があるときは使わない
    if _RE_RFC2822.match(v) and not _RE_AMPM.search(v) and (iso := _rfc2822(v)):
        return iso, "high"
    y = mo = d = None
    rest = v
    m = _RE_WAREKI.search(v)
    if m:
        n = 1 if m.group(2) == "元" else int(m.group(2))
        y, mo, d = _ERA_BASE[m.group(1)] + n, int(m.group(3)), int(m.group(4))
        rest = v[m.end():]
    elif m := _RE_YMD.search(v):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        rest = v[m.end():]
    elif (m := _RE_EN_MDY.search(v)) and m.group(1).lower() in _MONTHS:
        y, mo, d = int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))
        rest = v[m.end():]
    elif (m := _RE_EN_DMY.search(v)) and m.group(2).lower() in _MONTHS:
        y, mo, d = int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))
        rest = v[m.end():]
    if y is None:
        # 形が少し崩れた RFC 2822（末尾にタイムゾーンの名前が 2 つ等）。午前・午後の表記があれば使わない
        if not _RE_AMPM.search(v) and (iso := _rfc2822(v)):
            return iso, "mid"
        return "", ""
    try:
        datetime(y, mo, d)
    except ValueError:
        return "", ""
    t = _RE_TIME.search(rest)
    if not t:
        return f"{y:04d}-{mo:02d}-{d:02d}", "low"
    hour, minute, sec = int(t.group(2)), int(t.group(3) or 0), int(t.group(4) or 0)
    ampm = (t.group(1) or t.group(5) or "").upper()
    if ampm in ("午後", "PM") and hour < 12:
        hour += 12
    if ampm in ("午前", "AM") and hour == 12:
        hour = 0
    # 時刻の後ろの時差（Z・+0000・+09:00・GMT・UTC・JST）。あれば付ける（無ければ時差なし＝比較では日本時間とみなす）
    tz = None
    if tzm := _RE_TZ_AFTER.match(rest[t.end():]):
        tz = _tz_of(tzm.group(1))
    try:
        return datetime(y, mo, d, hour, minute, sec, tzinfo=tz).isoformat(), "mid"
    except ValueError:
        return f"{y:04d}-{mo:02d}-{d:02d}", "low"


def date_utc(iso: str) -> datetime | None:
    """比較用の UTC の時刻。時差の無い値は日本時間（DEFAULT_TZ）とみなす。日付だけの値は、その日の 0 時（日本時間）。"""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=DEFAULT_TZ)
    return dt.astimezone(timezone.utc)


def time_key(meta: Meta) -> tuple:
    """並べ替えの鍵。日時の分からないものは最後。日付だけの値は、その日の最後に置く（同じ日の時刻付きより後）。"""
    dt = date_utc(meta.date_iso)
    if dt is None:
        return (1, 0.0)
    return (0, dt.timestamp() + (86399 if len(meta.date_iso) == 10 else 0))


def is_before(a: Meta, b: Meta) -> bool | None:
    """a が b より前か。分からなければ None（日時が無い・日付だけの値が同じ日の値と比べられる）。"""
    ta, tb = date_utc(a.date_iso), date_utc(b.date_iso)
    if ta is None or tb is None:
        return None
    if (len(a.date_iso) == 10 or len(b.date_iso) == 10) and \
            ta.astimezone(DEFAULT_TZ).date() == tb.astimezone(DEFAULT_TZ).date():
        return None
    return ta < tb


def _apply_field(meta: Meta, fname: str, value: str) -> None:
    if fname == "from":
        addrs = parse_addresses(value)
        if addrs:
            meta.sender = addrs[0]
    elif fname in ("to", "cc", "bcc"):
        getattr(meta, fname).extend(parse_addresses(value))
    elif fname == "date":
        meta.date_raw = value
        meta.date_iso, meta.date_conf = parse_date(value)
    elif fname == "subject":
        meta.subject = value
    elif fname == "attach":
        meta.attachments.extend(p.strip() for p in re.split(r"[;,；、]", value) if p.strip())


@dataclass
class _HeaderBlock:
    end: int
    meta: Meta
    fields: set[str]


def parse_header_block(lines: Sequence[Line], i: int, *, min_fields: int) -> _HeaderBlock | None:
    """行 i から始まるヘッダの塊（From:/差出人: など）を読む。項目が足りなければ None。"""
    meta = Meta(origin="body")
    fields: set[str] = set()
    last = ""
    j = i
    while j < len(lines):
        ln = lines[j]
        fname, value = hdr_field(ln.nb)
        if fname:
            if j > i and ln.blanks_before > 0:
                break
            _apply_field(meta, fname, value)
            fields.add(fname)
            last = fname
            j += 1
            continue
        # 折り返した宛先一覧（To/Cc の後、空行を挟まずに続く行）
        if last in ADDR_FIELDS and ln.blanks_before == 0 and not ends_addr_run(ln.nb) and j > i:
            _apply_field(meta, last, ln.nb)
            j += 1
            continue
        break
    if j == i:
        return None
    core_ok = "from" in fields or ("date" in fields and "subject" in fields)
    if len(fields) < min_fields or (min_fields >= 2 and not core_ok):
        return None
    return _HeaderBlock(end=j, meta=meta, fields=fields)


def is_attribution(nb: str) -> bool:
    t = nb.strip()
    return bool(RE_ON_WROTE.match(t) or RE_WROTE.search(t)
                or (RE_QUOTE_STAMP.match(t) and re.search(r"[:：]$", t)))


def attribution_meta(nb: str) -> Meta:
    meta = Meta(origin="attribution")
    meta.date_raw = nb
    meta.date_iso, meta.date_conf = parse_date(nb)
    em = RE_EMAIL.search(nb)
    name = ""
    t = re.sub(r"\s*(wrote:?|さんは書きました[:：]?|が書きました[:：]?|のメッセージ[:：])\s*$", "", nb, flags=re.I)
    t = re.sub(r"^on\s+", "", t, flags=re.I)
    t = RE_EMAIL.sub("", t)
    t = re.sub(r"[<>\[\]()（）]", " ", t)
    t = re.sub(r"\bat\b", " ", t)
    tm = [x for x in _RE_TIME.finditer(t) if x.group(3) or x.group(1) or x.group(5)]
    if tm:
        name = t[tm[-1].end():]
    else:
        # 時刻の無い帰属行（「山田太郎さんは書きました:」）: 日付を除いた残り全体を名前とみなす
        name = re.sub(r"\d{4}\s*[年/.-]\s*\d{1,2}\s*[月/.-]\s*\d{1,2}\s*日?", " ", t)
    name = name.strip(" ,、:：")
    meta.sender = Address(name=name[:60], addr=em.group(0).lower() if em else "")
    return meta


def segment_mail(lines: Sequence[Line], *, own_header_at_top: bool) -> tuple[list[Unit], Meta | None]:
    """1 通を部分に分ける。戻り値は (部分の一覧, 本文の先頭にあった自分自身のヘッダ)。

    own_header_at_top: CSV・Excel のように、本文の先頭に自分自身のヘッダ（From:/件名: …）が貼られている入力。
    eml では、本文の先頭のヘッダは転送された過去メールなので、False にする。
    """
    n = len(lines)
    # (先頭行, 中身の先頭行, ヘッダ行, メタ, 引用の最小の深さ)。最小の深さがあるとき、それより浅い深さ 0 の行
    # （引用の下に書いた返事）は、引用に入れず、新しく書かれた部分に戻す。
    #   ・「> -----Original Message-----」「> From:」のように、引用記号の付いた区切り → その行の深さ
    #   ・帰属行（「…wrote:」）の直後が、より深い引用 → 帰属行の深さ + 1
    #   ・引用記号の無い区切り（Outlook の転送・返信）→ なし（次の区切りまで、全部が引用）
    starts: list[tuple[int, int, list[int], Meta, int | None]] = []
    own_meta: Meta | None = None
    own_header: list[int] = []

    def boundary_before(i: int) -> bool:
        """行 i の直前が、区切り（本文の先頭・空行・「>」だけの空の引用行・区切り線・引用の深さの変化）か。"""
        return (i == 0 or lines[i].blanks_before > 0 or not lines[i - 1].nb or is_sep_line(lines[i - 1].nb)
                or lines[i - 1].depth != lines[i].depth)

    i = 0
    while i < n:
        nb = RE_DECO.sub("", lines[i].nb).strip()
        min_depth = lines[i].depth or None
        if RE_ORIG_MSG.match(nb) or (RE_UNDERSCORE_LINE.match(nb) and i + 1 < n
                                     and parse_header_block(lines, i + 1, min_fields=1)):
            hb = parse_header_block(lines, i + 1, min_fields=1)
            if hb:
                starts.append((i, hb.end, list(range(i, hb.end)), hb.meta, min_depth))
                i = hb.end
            else:
                starts.append((i, i + 1, [i], Meta(origin="body"), min_depth))
                i += 1
            continue
        hb = parse_header_block(lines, i, min_fields=2)
        if hb:
            at_top = all(is_sep_line(lines[k].nb) for k in range(i))
            if own_header_at_top and at_top and not starts and own_meta is None:
                own_meta, own_header = hb.meta, list(range(0, hb.end))
                i = hb.end
                continue
            # 本文の途中のヘッダの塊は、差出人の項目があり、直前が区切りのときだけ認める
            # （会議案内の「件名：…」「場所：…」のような本文を、引用と誤らないため）
            if "from" in hb.fields and (at_top or boundary_before(i)):
                starts.append((i, hb.end, list(range(i, hb.end)), hb.meta, min_depth))
                i = hb.end
                continue
        deeper = i + 1 < n and lines[i + 1].depth > lines[i].depth
        if is_attribution(nb) and (lines[i].depth > 0 or i + 1 >= n or deeper
                                   or RE_QUOTE_STAMP.match(nb) or RE_ON_WROTE.match(nb)):
            starts.append((i, i + 1, [i], attribution_meta(nb), lines[i].depth + 1 if deeper else None))
            i += 1
            continue
        i += 1

    units: list[Unit] = []
    first = starts[0][0] if starts else n
    new_lines = [k for k in range(len(own_header), first)]
    # 新しく書かれた部分の中の「>」の引用: 後ろに本文が続くならインライン返信の文脈、末尾に固まるなら引用の 1 通
    quoted_tail: list[int] = []
    last_plain = max((k for k in new_lines if lines[k].depth == 0), default=-1)
    body_lines, context = [], []
    for k in new_lines:
        if lines[k].depth > 0 and not lines[k].nb:
            continue                    # 「>」だけの空の引用行は、どの部分にも入れない（中身の無い引用の 1 通を作らない）
        if lines[k].depth == 0:
            body_lines.append(k)
        elif k < last_plain:
            context.append(k)
        else:
            quoted_tail.append(k)
    new_unit = Unit(kind="new", lines=body_lines, header_lines=own_header, meta=own_meta or Meta(),
                    context_lines=context)
    units.append(new_unit)
    if quoted_tail:
        units.append(Unit(kind="quoted", lines=quoted_tail, meta=Meta(origin="body")))
    # 引用の 1 通ごとの「最小の深さ」（無ければ 0 = 次の区切りまで全部）。浅い行を、外側の 1 通に戻すのに使う
    unit_depth: list[int] = [0] + ([min(lines[k].depth for k in quoted_tail)] if quoted_tail else [])
    for s, (start, body_start, hdr, meta, min_depth) in enumerate(starts):
        end = starts[s + 1][0] if s + 1 < len(starts) else n
        rng = list(range(body_start, end))
        unit = Unit(kind="quoted", lines=[], header_lines=hdr, meta=meta)
        implicit: Unit | None = None    # 区切りの無い外側のメール（差出人不明）
        implicit_depth = 0
        if min_depth is not None:
            for k in rng:
                d = lines[k].depth
                if d >= min_depth:
                    unit.lines.append(k)
                elif d == 0:
                    # 引用の下に書いた返事は、新しく書かれた部分に戻す（引用の 1 通が重複と判定されても、消えない）
                    new_unit.lines.append(k)
                elif lines[k].nb:
                    # 深い引用の後に続く、浅い引用の行（外側のメールの続き・署名）は、その深さを含む外側の 1 通に戻す。
                    # 外側の 1 通が無ければ（外側のメールに区切りが無い）、差出人不明の外側の 1 通を作って入れる
                    outer = next((u for u, ud in zip(reversed(units), reversed(unit_depth))
                                  if u.kind == "quoted" and ud <= d), None)
                    if outer is None:
                        if implicit is None:
                            implicit, implicit_depth = Unit(kind="quoted", lines=[], meta=Meta(origin="body")), d
                        outer = implicit
                        implicit_depth = min(implicit_depth, d)
                    outer.lines.append(k)
        else:
            unit.lines = rng
        if implicit is not None:        # 外側（新しい方）を、内側の 1 通より前に置く（引用の連なりは新しい順）
            units.append(implicit)
            unit_depth.append(implicit_depth)
        units.append(unit)
        unit_depth.append(min_depth or 0)
    for u in units:
        u.lines.sort()
    return units, own_meta


def unit_position(units: Sequence[Unit], n: int) -> dict[str, list[float]]:
    """構造の特徴量（plus）: 各行が何通目の部分か・その部分の末尾からの距離・ヘッダの行か・帰属行か・文脈の行か。"""
    msg_index = [0.0] * n
    from_end = [0.0] * n
    rel = [0.0] * n
    in_hdr = [0.0] * n
    ctx = [0.0] * n
    for u_i, u in enumerate(units):
        content = sorted(u.lines)
        for r, k in enumerate(content):
            msg_index[k] = float(u_i)
            from_end[k] = float(len(content) - 1 - r)
            rel[k] = r / max(len(content) - 1, 1)
        for k in u.header_lines:
            msg_index[k] = float(u_i)
            in_hdr[k] = 1.0
            from_end[k] = float(len(content))
        for k in u.context_lines:
            ctx[k] = 1.0
    return {"u_index": msg_index, "u_from_end": from_end, "u_rel_pos": rel, "u_in_hdr": in_hdr, "u_context": ctx}


# =============================================================================
# 5. 行の特徴量（find_noise の分析から作り直し）
# =============================================================================
def _char_ratios(s: str) -> dict[str, float]:
    n = len(s) or 1
    kanji = kana = ascii_ = digit = sym = space = 0
    for ch in s:
        o = ord(ch)
        if ch.isspace():
            space += 1
        elif ch.isdigit():
            digit += 1
        elif ch.isascii():
            if ch.isalpha():
                ascii_ += 1
            else:
                sym += 1
        elif 0x4E00 <= o <= 0x9FFF:
            kanji += 1
        elif 0x3040 <= o <= 0x30FF:
            kana += 1
        else:
            sym += 1
    return {"r_kanji": kanji / n, "r_kana": kana / n, "r_ascii": ascii_ / n,
            "r_digit": digit / n, "r_symbol": sym / n, "r_space": space / n}


def _starts_new_mail(lines: Sequence[Line], i: int) -> bool:
    """マーカー無しで貼られた過去メールの先頭（宛名行の直後に、書き出しの挨拶）。"""
    if i == 0 or not RE_SAMA_LINE.match(lines[i].nb):
        return False
    for j in range(i + 1, min(i + 4, len(lines))):
        nb = lines[j].nb
        if not nb or is_sep_line(nb) or RE_SAMA_LINE.match(nb):
            continue
        return bool(RE_GREET_OPEN.search(nb))
    return False


def find_blocks(lines: Sequence[Line]) -> list[int]:
    """各行の引用ブロック番号（ヘッダの塊の先頭・引用の深さの変化・マーカー無しの過去メールの先頭で切る）。"""
    blk = [0] * len(lines)
    cur, prev_depth, prev_hdr = 0, 0, False
    for i, ln in enumerate(lines):
        nb = RE_DECO.sub("", ln.nb)
        is_orig = bool(RE_ORIG_MSG.match(nb)) or bool(RE_QUOTE_STAMP.match(nb))
        is_hdr = bool(hdr_field(ln.nb)[0])
        if is_orig or (is_hdr and not prev_hdr) or ln.depth != prev_depth or _starts_new_mail(lines, i):
            cur += 1
        blk[i] = cur
        prev_depth, prev_hdr = ln.depth, is_hdr or is_orig
    return blk


def sig_zone(lines: Sequence[Line], blocks: Sequence[int]) -> tuple[list[int], list[int]]:
    """ブロックの末尾から遡る「署名らしい領域」。連絡先の手掛かりが 1 つも無ければ領域にしない。"""
    n = len(lines)
    in_zone, depth = [0] * n, [-1] * n
    per_blk: dict[int, list[int]] = defaultdict(list)
    for i, b in enumerate(blocks):
        per_blk[b].append(i)
    for idx in per_blk.values():
        end = next((i for i in reversed(idx) if lines[i].nb and not is_sep_line(lines[i].nb)), None)
        if end is None:
            continue
        zone: list[int] = []
        anchor, misses = False, 0
        for i in reversed([j for j in idx if j <= end]):
            nb = lines[i].nb
            if is_sep_line(nb):
                zone.append(i)
                continue
            contact = bool(RE_TEL.search(nb) or RE_FAX.search(nb) or RE_ZIP.search(nb) or RE_EMAIL.search(nb))
            org = bool(RE_CORP.search(nb) or RE_DEPT.search(nb) or RE_TITLE.search(nb) or RE_URL.search(nb))
            if contact or org or (len(nb) <= 30 and "。" not in nb):
                zone.append(i)
                anchor = anchor or contact
                misses = 0
                continue
            misses += 1
            if misses > 1:
                break
            zone.append(i)
        if not anchor:
            continue
        zone.sort()
        while zone and is_sep_line(lines[zone[0]].nb):
            zone.pop(0)
        for k, i in enumerate(zone):
            in_zone[i], depth[i] = 1, k
    return in_zone, depth


def _zone_spans(flag: Sequence[int]) -> list[tuple[int, int, int]]:
    n = len(flag)
    out = [(0, -1, -1)] * n
    i = 0
    while i < n:
        if not flag[i]:
            i += 1
            continue
        j = i
        while j < n and flag[j]:
            j += 1
        for k in range(i, j):
            out[k] = (j - i, k - i, j - 1 - k)
        i = j
    return out


@dataclass
class LineStats:
    """行頻度（その行が何通に出るか・同じ送信者の何通に出るか）。単独で最も効く特徴量。"""
    n_docs: int = 0
    doc_df: dict[str, int] = field(default_factory=dict)
    sender_df: dict[str, dict[str, int]] = field(default_factory=dict)
    sender_n: dict[str, int] = field(default_factory=dict)

    @classmethod
    def build(cls, docs: Iterable[tuple[str, str]]) -> LineStats:
        doc_df: Counter[str] = Counter()
        sender_df: dict[str, Counter[str]] = defaultdict(Counter)
        sender_n: Counter[str] = Counter()
        n = 0
        for text, sender in docs:
            n += 1
            keys = {line_key(ln.text) for ln in split_lines(text)}
            doc_df.update(keys)
            sender_n[sender] += 1
            if sender:
                sender_df[sender].update(keys)
        return cls(n_docs=n, doc_df=dict(doc_df), sender_df={s: dict(c) for s, c in sender_df.items()},
                   sender_n=dict(sender_n))


class _CountsPlusSelf:
    """行頻度の表に、1 通ぶん（そのメール自身の行）を足したように見せる（表は複写しない）。"""

    def __init__(self, base: Any, keys: set[str]) -> None:
        self.base, self.keys = base, keys

    def get(self, key: str, default: int = 0) -> int:
        v = (self.base.get(key, 0) if self.base else 0) + (1 if key in self.keys else 0)
        return v if v else default


class _SenderPlusSelf:
    def __init__(self, base: dict, sender: str, keys: set[str]) -> None:
        self.base, self.sender, self.keys = base, sender, keys

    def get(self, sender: str, default: Any = None) -> Any:
        if sender == self.sender:
            return _CountsPlusSelf(self.base.get(sender, {}), self.keys)
        return self.base.get(sender, default)


def stats_with_doc(stats: LineStats, text: str, sender: str) -> LineStats:
    """学習側の行頻度に、評価する 1 通を足したもの（評価の unseen 用）。run は入力全体の統計（自分を含む）を使うので、条件をそろえる。"""
    keys = {line_key(ln.text) for ln in split_lines(text)}
    sender_n = dict(stats.sender_n)
    sender_n[sender] = sender_n.get(sender, 0) + 1
    return LineStats(n_docs=stats.n_docs + 1, doc_df=_CountsPlusSelf(stats.doc_df, keys),  # type: ignore[arg-type]
                     sender_df=_SenderPlusSelf(stats.sender_df, sender, keys) if sender else stats.sender_df,  # type: ignore[arg-type]
                     sender_n=sender_n)


_CTX_KEYS = ("is_sep", "re_tel", "re_zip", "re_email", "re_corp", "re_dept", "re_title", "sdf_ratio", "df_ratio",
             "has_kuten", "len", "re_disclaim", "re_hdr_field", "short_no_punct")
_CTX_KEYS_PLUS = ("name_like", "re_address", "u_in_hdr", "re_greet")
_CTX_KEYS_JA = ("ends_polite", "r_hira", "script_switch_rate", "pos_propn")
FEATURE_SETS = ("base", "plus", "ja")


# ---- 日本語の特徴量（feature_set=ja） ------------------------------------------------
# 文末の丁寧語（本文らしさ）・普通体の文末・助詞（名詞の羅列＝署名・宛名・住所と、文＝本文を分ける）
_RE_POLITE_END = re.compile(r"(です|ます|ました|ません|でした|ましょう|でしょう(か)?|ください|下さい|いたします|致します|"
                            r"願います|ございます|存じます|申し上げます|まして|ますが|ですが|ません(が|でした))"
                            r"[。．.！!？?」』）)\s～〜ー]*$")
_RE_POLITE = re.compile(r"です|ます|ました|ません|ください|下さい|いたし|致し|願い|ござい|存じ|申し上げ")
_RE_PLAIN_END = re.compile(r"(だ|である|した|する|ない|よ|ね|か|な|た)[。．.！!？?」』）)\s]*$")
_RE_PARTICLE = re.compile(r"(?<=[一-龥々ァ-ヶーA-Za-z0-9])(は|が|を|に|で|と|へ|も|から|まで|より)(?=[一-龥々ァ-ヶーぁ-んA-Za-z0-9])")
_RE_BULLET = re.compile(r"^\s*([・■□●○◆◇▼▲※★☆\-*]|\d{1,2}[.)．）]|[①-⑳]|[(（]\d{1,2}[)）])")
_RE_BRACKET_TAG = re.compile(r"^\s*[【\[［〔].{1,20}[】\]］〕]")


def _script_of(ch: str) -> int:
    o = ord(ch)
    if ch.isspace():
        return 0
    if 0x4E00 <= o <= 0x9FFF or ch in "々〆ヵヶ":
        return 1
    if 0x3040 <= o <= 0x309F:
        return 2
    if 0x30A0 <= o <= 0x30FF or 0xFF66 <= o <= 0xFF9F:
        return 3
    if ch.isdigit():
        return 5
    if ch.isalpha():
        return 4
    return 6


_JA_NAMES = ("r_hira", "r_kata", "kanji_run_max", "kata_run_max", "script_switch_rate", "ends_polite", "has_polite",
             "ends_plain", "particle_rate", "width_fold_rate", "ends_nounish", "starts_bullet", "bracket_tag")
_POS_NAMES = ("pos_n_tok", "pos_noun", "pos_propn", "pos_person", "pos_place", "pos_particle", "pos_aux", "pos_verb",
              "pos_symbol", "pos_last_aux_verb")

# Sudachi（品詞）。auto: 入っていれば使う / off: 使わない（品詞の特徴量は -1）。モデルには、学習時に使ったかを記録し、
# run では学習時と同じ状態にそろえる（品詞が -1 のまま判定すると、黙って精度が落ちるため）
_SUDACHI: dict[str, Any] = {"mode": "auto", "tok": None, "tried": False, "version": ""}
_SUDACHI_MAX_CHARS = 2000      # sudachi.rs の入力の上限（バイト数）より十分短く切る


def set_sudachi(mode: str) -> None:
    if mode not in ("auto", "off", "require"):
        raise ValueError(mode)
    _SUDACHI.update(mode=mode, tok=None, tried=False)
    _static_values.cache_clear()
    if mode == "require" and sudachi_tokenizer() is None:
        raise SystemExit("[中止] このモデルは Sudachi の品詞で学習しています。sudachipy と sudachidict_core を入れてください")


def sudachi_tokenizer() -> Any:
    if _SUDACHI["mode"] == "off":
        return None
    if not _SUDACHI["tried"]:
        _SUDACHI["tried"] = True
        try:
            from importlib.metadata import version as _ver

            from sudachipy import Dictionary, SplitMode

            _SUDACHI["tok"] = (Dictionary(dict="core").create(), SplitMode.C)
            _SUDACHI["version"] = f"sudachipy {_ver('sudachipy')}・sudachidict_core {_ver('sudachidict_core')}"
        except Exception:      # 入っていない・辞書が無い → 品詞なし（auto のときだけ。require は set_sudachi で止める）
            _SUDACHI["tok"] = None
    return _SUDACHI["tok"]


def _pos_values(body: str) -> tuple[float, ...]:
    st = sudachi_tokenizer()
    if st is None or not body.strip():
        return (-1.0,) * len(_POS_NAMES)
    tok, mode = st
    try:
        ms = tok.tokenize(body[:_SUDACHI_MAX_CHARS], mode)
    except Exception:
        return (-1.0,) * len(_POS_NAMES)
    pos = [m.part_of_speech() for m in ms if m.part_of_speech()[0] != "空白"]
    if not pos:
        return (0.0,) + (0.0,) * (len(_POS_NAMES) - 1)
    k = len(pos)
    noun = sum(p[0] == "名詞" for p in pos)
    propn = sum(p[0] == "名詞" and p[1] == "固有名詞" for p in pos)
    person = sum(p[1] == "固有名詞" and p[2] == "人名" for p in pos)
    place = sum(p[1] == "固有名詞" and p[2] == "地名" for p in pos)
    particle = sum(p[0] == "助詞" for p in pos)
    aux = sum(p[0] == "助動詞" for p in pos)
    verb = sum(p[0] == "動詞" for p in pos)
    sym = sum(p[0] in ("補助記号", "記号") for p in pos)
    content = [p for p in pos if p[0] not in ("補助記号", "記号")]
    last = float(bool(content) and content[-1][0] in ("助動詞", "動詞", "助詞"))
    return (float(min(k, 60)), noun / k, propn / k, person / k, place / k, particle / k, aux / k, verb / k, sym / k,
            last)


def _ja_values(body: str) -> tuple[float, ...]:
    s = body.strip()
    n = len(s) or 1
    scripts = [_script_of(ch) for ch in s]
    hira = scripts.count(2)
    kata = scripts.count(3)
    runs = {1: 0, 3: 0}
    cur, run = -1, 0
    switches = 0
    prev = -1
    for sc in scripts:
        run = run + 1 if sc == cur else 1
        cur = sc
        if sc in runs and run > runs[sc]:
            runs[sc] = run
        if sc not in (0, 6):
            if prev != -1 and sc != prev:
                switches += 1
            prev = sc
    core = re.sub(r"[\s。．.！!？?」』）)～〜ー、,]+$", "", s)
    last = _script_of(core[-1]) if core else 0
    width = sum(1 for ch in s if ord(ch) >= 0xFF01 and unicodedata.normalize("NFKC", ch) != ch)
    return (hira / n, kata / n, float(min(runs[1], 30)), float(min(runs[3], 30)), switches / n,
            float(bool(_RE_POLITE_END.search(s))), float(bool(_RE_POLITE.search(s))),
            float(bool(_RE_PLAIN_END.search(s))), len(_RE_PARTICLE.findall(s)) / n, width / n,
            float(last in (1, 3, 4, 5)), float(bool(_RE_BULLET.match(s))), float(bool(_RE_BRACKET_TAG.match(s))))


# ---- 行の文字列だけで決まる特徴量（キャッシュする） ----------------------------------------
_STATIC_BASE = ("r_kanji", "r_kana", "r_ascii", "r_digit", "r_symbol", "r_space", "len", "n_words", "is_sep",
                "sep_run_len", "has_kuten", "has_touten", "ends_kuten", "no_punct_long", "re_zip", "re_tel", "re_fax",
                "re_phone_bare", "re_email", "re_url", "re_corp", "re_dept", "re_title", "re_honorific",
                "re_sama_line", "re_greet", "re_greet_only", "re_disclaim", "re_orig_msg", "re_hdr_field",
                "re_quote_stamp", "re_wrote", "re_name_latin", "short_no_punct", "_addr_field", "_looks_cont",
                "_ends_addr_run")
_STATIC_PLUS = ("name_like", "name_paren", "re_address", "re_notice", "re_pw", "re_intro", "re_attribution",
                "contact_only", "greet_only_rule", "n_sentences")
_PRIVATE = {"_addr_field", "_looks_cont", "_ends_addr_run"}     # 計算の途中でだけ使う（特徴量には入れない）


def _static_names(level: int) -> tuple[str, ...]:
    return _STATIC_BASE + (_STATIC_PLUS if level >= 1 else ()) + (_JA_NAMES + _POS_NAMES if level >= 2 else ())


@functools.lru_cache(maxsize=400_000)
def _static_values(nb: str, body: str, level: int) -> tuple[float, ...]:
    """行の文字列だけで決まる値（正規表現・文字の割合・日本語の特徴量）。署名・免責・挨拶は、メールが違っても
    同じ行が何度も出るので、文字列ごとにキャッシュして、再計算を避ける。"""
    cr = _char_ratios(body)
    sep_run = RE_SEP_RUN.search(nb)
    nbd = RE_DECO.sub("", nb)
    fname = hdr_field(nb)[0]
    vals = [cr["r_kanji"], cr["r_kana"], cr["r_ascii"], cr["r_digit"], cr["r_symbol"], cr["r_space"],
            float(len(body)), float(len(body.split())), float(is_sep_line(nb)),
            float(len(sep_run.group(0)) if sep_run else 0), float("。" in nb), float("、" in nb),
            float(nb.endswith(("。", "！", "？", ".", "!", "?"))), float("。" not in nb and len(body) >= 8),
            float(bool(RE_ZIP.search(nb))), float(bool(RE_TEL.search(nb))), float(bool(RE_FAX.search(nb))),
            float(bool(RE_PHONE_BARE.search(nb))), float(bool(RE_EMAIL.search(nb))), float(bool(RE_URL.search(nb))),
            float(bool(RE_CORP.search(nb))), float(bool(RE_DEPT.search(nb))), float(bool(RE_TITLE.search(nb))),
            float(bool(RE_HONORIFIC.search(nb))), float(bool(RE_SAMA_LINE.match(nb))),
            float(bool(RE_GREET.search(nb))), float(bool(RE_GREET_ONLY.match(nb))),
            float(bool(RE_DISCLAIM.search(nb))), float(bool(RE_ORIG_MSG.match(nbd))), float(bool(fname)),
            float(bool(RE_QUOTE_STAMP.match(nbd))), float(bool(RE_WROTE.search(nb))),
            float(bool(RE_NAME_LATIN.match(nb))), float(len(body) <= 30 and "。" not in nb),
            float(fname in ADDR_FIELDS),
            float("。" not in nb and bool(RE_EMAIL.search(nb) or nb.rstrip().endswith((";", ",", "、", ">")))),
            float(ends_addr_run(nb))]
    if level >= 1:
        vals += [float(bool(RE_NAME_LIKE.match(nb))), float(bool(RE_NAME_PAREN.search(nb))),
                 float(bool(RE_ADDRESS.search(nb))), float(bool(RE_NOTICE.search(nb))), float(bool(RE_PW.search(nb))),
                 float(bool(RE_INTRO.match(nb))), float(is_attribution(nbd)), float(bool(contact_kind(nb))),
                 float(bool(greet_kind(nb))), float(len(split_sentences(nb)))]
    if level >= 2:
        vals += list(_ja_values(body)) + list(_pos_values(body))
    return tuple(vals)


@dataclass
class Feats:
    """1 通の行ごとの特徴量（行 × 列の float32 の配列）。辞書を行ごとに持つと、評価でメモリが足りなくなるため。"""
    names: tuple[str, ...]
    x: Any

    def __len__(self) -> int:
        return len(self.x)

    @functools.cached_property
    def pos(self) -> dict[str, int]:
        return {c: j for j, c in enumerate(self.names)}

    def col(self, name: str, default: float = 0.0):
        import numpy as np

        j = self.pos.get(name)
        return self.x[:, j] if j is not None else np.full(len(self.x), default, dtype=np.float32)


def line_features(lines: Sequence[Line], stats: LineStats, sender: str, *, feature_set: str = "plus",
                  units: Sequence[Unit] | None = None) -> Feats:
    """1 通の行ごとの特徴量。feature_set: base（find_noise 相当・実名の一覧なし）/ plus（構造と形の特徴量を追加）/
    ja（plus に、日本語の特徴量を追加）。"""
    import numpy as np

    level = FEATURE_SETS.index(feature_set)
    s_names = _static_names(level)
    keep_static = [j for j, c in enumerate(s_names) if c not in _PRIVATE]
    j_addr, j_cont, j_end = s_names.index("_addr_field"), s_names.index("_looks_cont"), s_names.index("_ends_addr_run")
    j_hdr, j_orig, j_stamp = s_names.index("re_hdr_field"), s_names.index("re_orig_msg"), s_names.index("re_quote_stamp")
    j_sep = s_names.index("is_sep")
    n = len(lines)
    dyn_names = ("line_idx", "from_end", "rel_pos", "blk_from_end", "blk_from_content_end", "sig_zone",
                 "sig_zone_depth", "zone_len", "zone_from_end", "zone_rel_pos", "blk_size", "blk_rel_pos",
                 "blanks_before", "quote_depth", "n_lines", "df", "df_ratio", "sender_n", "sdf", "sdf_ratio",
                 "sender_known", "dist_from_sep", "hdr_cont", "hdr_addr_cont")
    u_names = ("u_index", "u_from_end", "u_rel_pos", "u_in_hdr", "u_context") if level >= 1 else ()
    ctx_keys = _CTX_KEYS + (_CTX_KEYS_PLUS if level >= 1 else ()) + (_CTX_KEYS_JA if level >= 2 else ())
    names = (tuple(s_names[j] for j in keep_static) + dyn_names + u_names
             + tuple(f"{k}@{off:+d}" for off in (-2, -1, 1, 2) for k in ctx_keys))
    if n == 0:
        return Feats(names, np.zeros((0, len(names)), dtype=np.float32))
    blocks = find_blocks(lines)
    blk_size: Counter[int] = Counter(blocks)
    blk_last = {b: i for i, b in enumerate(blocks)}
    blk_content_last: dict[int, int] = {}
    for i, ln in enumerate(lines):
        if ln.nb and not is_sep_line(ln.nb):
            blk_content_last[blocks[i]] = i
    zone_flag, zone_depth = sig_zone(lines, blocks)
    zone_span = _zone_spans(zone_flag)
    upos = unit_position(units, n) if level >= 1 and units is not None else None
    s_df = stats.sender_df.get(sender, {}) if sender else {}
    s_n = stats.sender_n.get(sender, 0) if sender else 0

    n_static = len(keep_static)
    x = np.empty((n, len(names)), dtype=np.float32)
    dist_sep = 99
    prev_blk = -1
    in_hdr = in_addr_run = False
    for i, ln in enumerate(lines):
        b = blocks[i]
        if b != prev_blk:
            dist_sep, prev_blk = 99, b
        sv = _static_values(ln.nb, ln.body, level)
        key = line_key(ln.text)
        sep = bool(sv[j_sep])
        is_hdr_line = bool(sv[j_hdr] or sv[j_orig] or sv[j_stamp])
        hdr_cont = float(in_hdr and bool(sv[j_cont]) and not is_hdr_line)
        in_hdr = is_hdr_line or bool(hdr_cont)
        if is_hdr_line:
            in_addr_run = bool(sv[j_addr])
            hdr_addr_cont = 0.0
        elif in_addr_run and ln.blanks_before == 0 and not sv[j_end]:
            hdr_addr_cont = 1.0
        else:
            in_addr_run = False
            hdr_addr_cont = 0.0
        zl, zp, ze = zone_span[i]
        df = stats.doc_df.get(key, 0)
        sdf = s_df.get(key, 0)
        row = x[i]
        row[:n_static] = [sv[j] for j in keep_static]
        row[n_static:n_static + len(dyn_names)] = (
            i, n - 1 - i, i / max(n - 1, 1), blk_last[b] - i, blk_content_last.get(b, blk_last[b]) - i,
            zone_flag[i], zone_depth[i], zl, ze, zp / max(zl - 1, 1) if zl else -1.0, blk_size[b],
            (i - (blk_last[b] - blk_size[b] + 1)) / max(blk_size[b] - 1, 1), min(ln.blanks_before, 3), ln.depth, n,
            df, df / max(stats.n_docs, 1), s_n, sdf, sdf / s_n if s_n >= 3 else 0.0, 1.0 if s_n >= 3 else 0.0,
            min(dist_sep, 20), hdr_cont, hdr_addr_cont)
        if u_names:
            row[n_static + len(dyn_names):n_static + len(dyn_names) + len(u_names)] = (
                [upos[k][i] for k in u_names] if upos is not None else -1.0)
        dist_sep = 0 if sep else dist_sep + 1

    # 前後 2 行の値（端は -1）
    pos = {c: j for j, c in enumerate(names)}
    src = x[:, [pos[k] for k in ctx_keys]]
    base_col = n_static + len(dyn_names) + len(u_names)
    width = len(ctx_keys)
    for o_i, off in enumerate((-2, -1, 1, 2)):
        block = np.full((n, width), -1.0, dtype=np.float32)
        if off < 0 and n > -off:
            block[-off:] = src[:off]
        elif off > 0 and n > off:
            block[:-off] = src[off:]
        x[:, base_col + o_i * width:base_col + (o_i + 1) * width] = block
    return Feats(names, x)


# =============================================================================
# 6. モデル（文字 n-gram の LR を LightGBM に重ねる。行の並びの平滑化）
# =============================================================================
NG_COLS = ([f"ng_{c}" for c in LABELS] + [f"ng_{c}_prev" for c in LABELS] + [f"ng_{c}_next" for c in LABELS])
EMB_COLS = ([f"emb_{c}" for c in LABELS] + [f"emb_{c}_prev" for c in LABELS] + [f"emb_{c}_next" for c in LABELS])
STACK_COLS = {"ng": NG_COLS, "emb": EMB_COLS}
LGB_PARAMS = {
    "objective": "multiclass", "num_class": len(LABELS), "learning_rate": 0.06, "num_leaves": 15,
    "min_data_in_leaf": 8, "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 1,
    "lambda_l2": 1.0, "verbose": -1,
}
LGB_ROUNDS = 400


def _ngram_norm(text: str) -> str:
    return re.sub(r"\d", "0", nfkc(RE_QUOTE_MARK.sub("", text)).strip())


class NgramLR:
    """行の文字 1〜3 gram の TF-IDF ＋ ロジスティック回帰。挨拶・名乗り・定型文の言い回しの変種に効く。"""

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression

        self.vec = TfidfVectorizer(analyzer="char", ngram_range=(1, 3), min_df=3, sublinear_tf=True,
                                   max_features=200_000)
        self.lr = LogisticRegression(C=4.0, max_iter=2000)

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> NgramLR:
        self.lr.fit(self.vec.fit_transform([_ngram_norm(t) for t in texts]), list(labels))
        return self

    def predict(self, texts: Sequence[str]):
        import numpy as np

        out = np.zeros((len(texts), len(LABELS)))
        if len(texts) == 0:
            return out
        p = self.lr.predict_proba(self.vec.transform([_ngram_norm(t) for t in texts]))
        for j, c in enumerate(self.lr.classes_):
            out[:, LABEL_INDEX[c]] = p[:, j]
        return out


# ---- 埋め込み（学習しない小さな日本語の埋め込みモデル）＋ 小さな MLP -------------------------------
EMB_PREFIX = "トピック: "        # ruri-v3 の分類・クラスタリング用の接頭辞（モデルカードの 1+3 方式）
EMB_MAX_CHARS = 200              # 長い行は先頭だけ（行の種類の判定には十分。速度のため）


def model_dir_digest(folder: Path) -> str:
    """埋め込みモデルのフォルダの識別値（ファイルの相対パスと中身の SHA-256 から作る。.cache は除く）。"""
    h = hashlib.sha256()
    files = sorted(p for p in folder.rglob("*") if p.is_file() and ".cache" not in p.relative_to(folder).parts)
    if not files:
        raise SystemExit(f"[中止] 埋め込みモデルのフォルダが空です: {folder}")
    for p in files:
        h.update(p.relative_to(folder).as_posix().encode("utf-8") + b"\0" + _sha256(p).encode("ascii") + b"\n")
    return h.hexdigest()


def _emb_norm(text: str) -> str:
    return re.sub(r"\s+", " ", _ngram_norm(text))[:EMB_MAX_CHARS]


class LineEmbedder:
    """行の埋め込み。同じ行（正規化後）は 1 回だけ計算する。cache_path を渡すと、ファイルにも保存する
    （キーは SHA-1。行の文字列は保存しない。ただし、ベクトルから元の行を推測される可能性はゼロではないので、
    実データのキャッシュはコミットしない）。モデルは、ローカルのフォルダからだけ読む（ネットワークに出ない）。"""

    def __init__(self, model_dir: Path, *, prefix: str = EMB_PREFIX, batch_size: int = 64,
                 cache_path: Path | None = None, digest: str | None = None) -> None:
        self.model_dir = Path(model_dir).resolve()
        if not (self.model_dir / "config.json").is_file():
            raise SystemExit(f"[中止] 埋め込みモデルのフォルダではありません（config.json が無い）: {self.model_dir}")
        self.digest = digest or model_dir_digest(self.model_dir)
        self.prefix = prefix
        self.batch_size = batch_size
        self.cache_path = cache_path
        self.mem: dict[str, Any] = {}
        self._model: Any = None
        self._dirty = False
        if cache_path and cache_path.is_file():
            self._load_cache(cache_path)

    def _cache_id(self) -> str:
        return hashlib.sha256(f"sha256-v2\0{self.digest}\0{self.prefix}\0{EMB_MAX_CHARS}".encode()).hexdigest()

    def _load_cache(self, path: Path) -> None:
        import numpy as np

        try:
            with np.load(path, allow_pickle=False) as z:
                if str(z["cache_id"]) != self._cache_id():
                    print(f"[注意] 埋め込みのキャッシュは、別のモデル・設定のものなので使いません: {path}")
                    return
                for k, v in zip(z["keys"], z["vecs"]):
                    self.mem[str(k)] = v
        except Exception as e:  # 壊れたキャッシュは使わない（計算し直す）
            print(f"[注意] 埋め込みのキャッシュを読めないので、使いません: {path}（{type(e).__name__}）")

    def save_cache(self) -> None:
        import numpy as np

        if not self.cache_path or not self._dirty or not self.mem:
            return
        keys = list(self.mem)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp.npz")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(tmp, cache_id=np.array(self._cache_id()), keys=np.array(keys),
                 vecs=np.stack([self.mem[k] for k in keys]))
        os.replace(tmp, self.cache_path)      # 書き込みの途中で落ちても、前のキャッシュが残る
        self._dirty = False

    def model(self) -> Any:
        if self._model is None:
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(str(self.model_dir), device="cpu", local_files_only=True)
        return self._model

    def encode(self, texts: Sequence[str]):
        import numpy as np

        keys = []
        todo: dict[str, str] = {}
        for t in texts:
            norm = _emb_norm(t)
            k = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            keys.append(k)
            if k not in self.mem:
                todo[k] = norm
        if todo:
            items = sorted(todo.items(), key=lambda kv: len(kv[1]))    # 長さをそろえて、詰め物の計算を減らす
            vecs = self.model().encode([self.prefix + v for _, v in items], batch_size=self.batch_size,
                                       normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            for (k, _), v in zip(items, vecs):
                self.mem[k] = v.astype(np.float32)
            self._dirty = True
        if not keys:
            return np.zeros((0, 0), dtype=np.float32)
        return np.stack([self.mem[k] for k in keys])


class EmbedMLP:
    """埋め込みの上の小さな MLP（隠れ層 1 つ。非線形）。埋め込みモデル自体は学習しない（少ないデータで過学習させない）。"""

    def __init__(self, embedder: LineEmbedder, seed: int = 0) -> None:
        self.embedder = embedder
        self.seed = seed
        self.clf: Any = None

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> EmbedMLP:
        from sklearn.neural_network import MLPClassifier

        x = self.embedder.encode(texts)
        counts = Counter(labels)
        # 検証の取り分けは層化されるので、1 件しかないクラスがあると取り分けられない → そのときは打ち切りなし
        early = min(counts.values()) >= 5 and len(labels) >= 200
        self.clf = MLPClassifier(hidden_layer_sizes=(128,), alpha=1e-3, learning_rate_init=1e-3, batch_size=256,
                                 max_iter=200 if early else 60, early_stopping=early, validation_fraction=0.1,
                                 n_iter_no_change=8, random_state=self.seed)
        self.clf.fit(x, list(labels))
        return self

    def predict(self, texts: Sequence[str]):
        import numpy as np

        out = np.zeros((len(texts), len(LABELS)))
        if len(texts) == 0:
            return out
        p = self.clf.predict_proba(self.embedder.encode(texts))
        for j, c in enumerate(self.clf.classes_):
            out[:, LABEL_INDEX[c]] = p[:, j]
        return out


def ngram_context(p):
    """1 通ぶんの n-gram の確率 (n, 9) に、前後の行の確率を足して (n, 27) にする（端は -1）。"""
    import numpy as np

    n = len(p)
    prev = np.full_like(p, -1.0)
    nxt = np.full_like(p, -1.0)
    if n > 1:
        prev[1:] = p[:-1]
        nxt[:-1] = p[1:]
    return np.hstack([p, prev, nxt])


def fit_transitions(label_seqs: Iterable[Sequence[str]]):
    """ラベルの並びから、遷移確率 (9, 9) と、先頭の確率 (9,) を推定する（加算 1 の平滑化）。"""
    import numpy as np

    k = len(LABELS)
    trans = np.ones((k, k))
    start = np.ones(k)
    for seq in label_seqs:
        ids = [LABEL_INDEX[c] for c in seq]
        if not ids:
            continue
        start[ids[0]] += 1
        for a, b in zip(ids, ids[1:]):
            trans[a, b] += 1
    return trans / trans.sum(axis=1, keepdims=True), start / start.sum()


def smooth_proba(proba, trans, start, weight: float):
    """前向き後向きアルゴリズムで、行の並びを考慮した確率にする。weight で元の確率と混ぜる（0 なら何もしない）。"""
    import numpy as np

    if weight <= 0 or len(proba) == 0:
        return proba
    e = np.clip(proba, 1e-6, 1.0)
    n, k = e.shape
    alpha = np.zeros((n, k))
    beta = np.ones((n, k))
    alpha[0] = start * e[0]
    alpha[0] /= alpha[0].sum()
    for t in range(1, n):
        alpha[t] = (alpha[t - 1] @ trans) * e[t]
        alpha[t] /= alpha[t].sum()
    for t in range(n - 2, -1, -1):
        beta[t] = trans @ (e[t + 1] * beta[t + 1])
        beta[t] /= beta[t].sum()
    post = alpha * beta
    post /= post.sum(axis=1, keepdims=True)
    return (1 - weight) * proba + weight * post


# ---- 行の並びのモデル（BiGRU。LightGBM の確率への残差） ---------------------------------------
SEQ_FEATS = ("quote_depth", "blanks_before", "sig_zone", "u_in_hdr", "u_context", "re_hdr_field", "is_sep", "len",
             "rel_pos", "has_kuten", "re_greet", "contact_only", "ends_polite", "df_ratio", "sdf_ratio")
SEQ_HIDDEN = 32
_LOGP_FLOOR = 1e-6


def seq_inputs(p, feats: Feats):
    """BiGRU の入力: LightGBM の確率 (n, 9) ＋ 構造の値（尺度をそろえる。無い列は 0）。"""
    import numpy as np

    cols = []
    for c in SEQ_FEATS:
        v = feats.col(c, 0.0).astype(np.float32)
        if c == "quote_depth":
            v = np.minimum(v, 5) / 5
        elif c == "blanks_before":
            v = v / 3
        elif c == "len":
            v = np.log1p(np.maximum(v, 0)) / 6
        cols.append(v)
    return np.hstack([np.asarray(p, dtype=np.float32), np.stack(cols, axis=1)]) if len(p) else \
        np.zeros((0, len(LABELS) + len(SEQ_FEATS)), dtype=np.float32)


def _sigmoid(v):
    import numpy as np

    return 1.0 / (1.0 + np.exp(-v))


def _gru_np(x, w_ih, w_hh, b_ih, b_hh, reverse: bool):
    """torch.nn.GRU の 1 方向ぶんの計算（ゲートの並びは r, z, n。torch と同じ式）。"""
    import numpy as np

    n = len(x)
    hid = w_hh.shape[1]
    gi = x @ w_ih.T + b_ih
    out = np.zeros((n, hid), dtype=np.float32)
    h = np.zeros(hid, dtype=np.float32)
    for t in (range(n - 1, -1, -1) if reverse else range(n)):
        gh = w_hh @ h + b_hh
        r = _sigmoid(gi[t, :hid] + gh[:hid])
        z = _sigmoid(gi[t, hid:2 * hid] + gh[hid:2 * hid])
        c = np.tanh(gi[t, 2 * hid:] + r * gh[2 * hid:])
        h = (1 - z) * c + z * h
        out[t] = h
    return out


def seq_predict(w: dict, p, feats: Feats):
    """学習済みの BiGRU で、LightGBM の確率を補正する（numpy だけ）。"""
    import numpy as np

    if len(p) == 0:
        return p
    x = seq_inputs(p, feats)
    h = np.hstack([_gru_np(x, w["w_ih"], w["w_hh"], w["b_ih"], w["b_hh"], False),
                   _gru_np(x, w["w_ih_r"], w["w_hh_r"], w["b_ih_r"], w["b_hh_r"], True)])
    logits = h @ w["lin_w"].T + w["lin_b"] + np.log(np.clip(p, _LOGP_FLOOR, 1.0))
    logits -= logits.max(axis=1, keepdims=True)
    e = np.exp(logits)
    return e / e.sum(axis=1, keepdims=True)


def train_seq(samples: Sequence[tuple[Any, Feats, Sequence[int], str]], *, seed: int, max_epochs: int = 60,
              patience: int = 8, valid_ratio: float = 0.15, log: Any = None) -> dict:
    """samples: (LightGBM の確率（交差検証の予測）, 特徴量, 正解のラベル番号（採点しない行は -100）, グループ)。
    グループ単位で検証を取り分けて、エポック数を打ち切りで決め、全部で学習し直す。出力層は 0 で始めるので、
    学習の前は LightGBM の確率そのもの（少ないデータで、元より悪くなりにくい）。"""
    import numpy as np
    import torch

    torch.set_num_threads(compute_threads())
    data = [(torch.from_numpy(seq_inputs(p, f)), torch.from_numpy(np.log(np.clip(p, _LOGP_FLOOR, 1.0))).float(),
             torch.tensor(list(y), dtype=torch.long), g) for p, f, y, g in samples if len(p)]
    d_in = len(LABELS) + len(SEQ_FEATS)

    class Net(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.gru = torch.nn.GRU(d_in, SEQ_HIDDEN, batch_first=True, bidirectional=True)
            self.drop = torch.nn.Dropout(0.1)
            self.lin = torch.nn.Linear(2 * SEQ_HIDDEN, len(LABELS))
            torch.nn.init.zeros_(self.lin.weight)
            torch.nn.init.zeros_(self.lin.bias)

        def forward(self, xs, logps, lens):
            packed = torch.nn.utils.rnn.pack_padded_sequence(xs, lens, batch_first=True, enforce_sorted=False)
            h, _ = self.gru(packed)
            h, _ = torch.nn.utils.rnn.pad_packed_sequence(h, batch_first=True, total_length=xs.shape[1])
            return self.lin(self.drop(h)) + logps

    def batches(items, rng):
        order = rng.permutation(len(items)) if rng is not None else np.arange(len(items))
        for s in range(0, len(order), 16):
            chunk = [items[i] for i in order[s:s + 16]]
            lens = torch.tensor([len(c[0]) for c in chunk])
            xs = torch.nn.utils.rnn.pad_sequence([c[0] for c in chunk], batch_first=True)
            lp = torch.nn.utils.rnn.pad_sequence([c[1] for c in chunk], batch_first=True)
            ys = torch.nn.utils.rnn.pad_sequence([c[2] for c in chunk], batch_first=True, padding_value=-100)
            yield xs, lp, ys, lens

    def run(train_items, valid_items, epochs: int):
        torch.manual_seed(seed)
        rng = np.random.default_rng(seed)
        net = Net()
        opt = torch.optim.Adam(net.parameters(), lr=3e-3, weight_decay=1e-4)
        loss_f = torch.nn.CrossEntropyLoss(ignore_index=-100, reduction="sum")
        best, best_ep, bad = float("inf"), epochs, 0
        for ep in range(1, epochs + 1):
            net.train()
            for xs, lp, ys, lens in batches(train_items, rng):
                opt.zero_grad()
                out = net(xs, lp, lens)
                n_valid = max(int((ys != -100).sum()), 1)
                loss = loss_f(out.reshape(-1, len(LABELS)), ys.reshape(-1)) / n_valid
                loss.backward()
                torch.nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()
            if valid_items:
                net.eval()
                tot = cnt = 0.0
                with torch.no_grad():
                    for xs, lp, ys, lens in batches(valid_items, None):
                        out = net(xs, lp, lens)
                        tot += float(loss_f(out.reshape(-1, len(LABELS)), ys.reshape(-1)))
                        cnt += float((ys != -100).sum())
                v = tot / max(cnt, 1)
                if v < best - 1e-5:
                    best, best_ep, bad = v, ep, 0
                else:
                    bad += 1
                    if bad >= patience:
                        break
        return net, best_ep

    groups = sorted({d[3] for d in data})
    epochs = max_epochs // 2
    if len(groups) >= 5:
        rng = np.random.default_rng(seed)
        valid_g = set(rng.choice(groups, size=max(1, int(len(groups) * valid_ratio)), replace=False).tolist())
        _, epochs = run([d for d in data if d[3] not in valid_g], [d for d in data if d[3] in valid_g], max_epochs)
    net, _ = run(data, [], epochs)
    if log:
        log(f"BiGRU のエポック数: {epochs}")
    sd = {k: v.detach().numpy().astype("float32") for k, v in net.state_dict().items()}
    return {"w_ih": sd["gru.weight_ih_l0"], "w_hh": sd["gru.weight_hh_l0"], "b_ih": sd["gru.bias_ih_l0"],
            "b_hh": sd["gru.bias_hh_l0"], "w_ih_r": sd["gru.weight_ih_l0_reverse"],
            "w_hh_r": sd["gru.weight_hh_l0_reverse"], "b_ih_r": sd["gru.bias_ih_l0_reverse"],
            "b_hh_r": sd["gru.bias_hh_l0_reverse"], "lin_w": sd["lin.weight"], "lin_b": sd["lin.bias"],
            "epochs": np.array(epochs)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lib_versions() -> dict[str, str]:
    from importlib.metadata import PackageNotFoundError, version

    out = {"python": sys.version.split()[0]}
    for name in ("numpy", "scikit-learn", "lightgbm", "joblib", "sentence-transformers", "torch", "sudachipy",
                 "sudachidict_core"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            pass
    return out


class NoiseModel:
    """学習済みの行分類器（LightGBM ＋ n-gram LR ＋ 任意で 埋め込みの MLP・BiGRU ＋ 遷移確率）。"""

    FILES = ("model.txt", "ngram.joblib", "meta.json")
    OPTIONAL_FILES = ("emb_head.joblib", "seq.npz")

    def __init__(self, booster: Any, feature_names: list[str], ngram: NgramLR | None, trans, start, *,
                 feature_set: str, kind: str = "lightgbm", info: dict | None = None,
                 emb: EmbedMLP | None = None, seq: dict | None = None) -> None:
        self.booster = booster
        self.feature_names = feature_names
        self.ngram = ngram
        self.trans = trans
        self.start = start
        self.feature_set = feature_set
        self.kind = kind
        self.info = info or {}
        self.emb = emb
        self.seq = seq
        self._plans: dict[tuple[str, ...], list] = {}

    @property
    def files(self) -> tuple[str, ...]:
        return self.FILES + (("emb_head.joblib",) if self.emb else ()) + (("seq.npz",) if self.seq else ())

    def _plan(self, names: tuple[str, ...]) -> list:
        """特徴量の列を、モデルの列の並びに写す手順（特徴量の名前の並びごとに 1 回だけ作る）。"""
        import numpy as np

        plan = self._plans.get(names)
        if plan is None:
            pos = {c: j for j, c in enumerate(names)}
            srcs: dict[str, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
            for dst, c in enumerate(self.feature_names):
                for key, cols in STACK_COLS.items():
                    if c in cols:
                        srcs[key][0].append(dst)
                        srcs[key][1].append(cols.index(c))
                        break
                else:
                    if c in pos:
                        srcs["f"][0].append(dst)
                        srcs["f"][1].append(pos[c])
            plan = [(k, np.array(d), np.array(s)) for k, (d, s) in srcs.items()]
            self._plans[names] = plan
        return plan

    def matrix(self, feats: Feats, stacked: dict[str, Any]):
        """モデルの列の並びの行列。学習時に無かった列（古いモデル・別の特徴量）は -1。"""
        import numpy as np

        out = np.full((len(feats), len(self.feature_names)), -1.0, dtype=np.float32)
        if len(feats) == 0:
            return out
        for key, dst, src in self._plan(feats.names):
            arr = feats.x if key == "f" else stacked[key]
            out[:, dst] = arr[:, src]
        return out

    def stacked(self, texts: Sequence[str]) -> dict[str, Any]:
        out = {"ng": ngram_context(self.ngram.predict(texts))}
        if self.emb is not None:
            out["emb"] = ngram_context(self.emb.predict(texts))
        return out

    def booster_proba(self, feats: Feats, stacked: dict[str, Any]):
        return predict_booster(self.booster, self.kind, self.matrix(feats, stacked))

    def warm(self, texts: Sequence[str]) -> None:
        """埋め込みを、まとめて先に計算する（1 通ずつだと、まとめて計算する速さを活かせない）。"""
        if self.emb is not None and texts:
            self.emb.embedder.encode(texts)

    def predict(self, feats: Feats, texts: Sequence[str], *, smooth: float = 0.0):
        """行ごとの 9 クラスの確率。BiGRU があればそれで補正し（平滑化はしない）、無ければ前後平滑化（smooth）。"""
        import numpy as np

        if len(feats) == 0:
            return np.zeros((0, len(LABELS)))
        p = self.booster_proba(feats, self.stacked(texts))
        if self.seq is not None:
            return seq_predict(self.seq, p, feats)
        return smooth_proba(p, self.trans, self.start, smooth)

    def save(self, out: Path) -> None:
        import joblib
        import numpy as np

        if self.kind != "lightgbm":
            raise ValueError("保存できるのは lightgbm のモデルだけです")
        out.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(out / "model.txt"))
        # 保存するのは scikit-learn の部品（ベクトル化器と LR）だけにする。このモジュールのクラス（NgramLR）ごと保存すると、
        # スクリプトとして実行したとき「__main__.NgramLR」として記録され、chatbot から import して読むと失敗する
        joblib.dump({"vec": self.ngram.vec, "lr": self.ngram.lr}, out / "ngram.joblib")
        if self.emb is not None:
            joblib.dump({"clf": self.emb.clf}, out / "emb_head.joblib")
        if self.seq is not None:
            np.savez(out / "seq.npz", **self.seq)
        meta = {"version": VERSION, "kind": self.kind, "feature_set": self.feature_set, "labels": list(LABELS),
                "feature_names": self.feature_names, "trans": self.trans.tolist(), "start": self.start.tolist(),
                "files": list(self.files), **self.info}
        (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / "SHA256SUMS").write_text("".join(f"{_sha256(out / n)}  {n}\n" for n in self.files), encoding="utf-8")

    @classmethod
    def load(cls, folder: Path, sums: Path, *, embed_model: Path | None = None,
             emb_cache: Path | None = None) -> NoiseModel:
        """信頼できる SHA-256 の一覧（sums）と照合してから読む。一致しなければ、読まずに止まる。

        joblib は pickle 形式で、読むだけでコードが動きうる。一覧がモデルと同じフォルダにあると、
        モデルを書き換えられる人は一覧も書き換えられるので、差し替えを防げない。そのため、一覧はモデルの
        フォルダの外（学習した人が別に保管したもの）を指定させ、フォルダの中の一覧は受け付けない。
        照合した後にファイルを開き直すと、その間に差し替えられうるので、照合したバイト列そのものから読み込む。
        """
        import io

        import joblib
        import lightgbm as lgb
        import numpy as np

        folder, sums = folder.resolve(), sums.resolve()
        if not sums.is_file():
            raise SystemExit(f"[中止] 識別値の一覧がありません: {sums}")
        if folder == sums.parent or folder in sums.parents:
            raise SystemExit(f"[中止] 識別値の一覧が、モデルのフォルダの中にあります: {sums}。"
                             "差し替えを防げないので、学習した人が別に保管した一覧を --model-sums で指定してください")
        expected = {}
        for no, row in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
            if not row.strip():
                continue
            parts = row.split(maxsplit=1)
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                raise SystemExit(f"[中止] 識別値の一覧の {no} 行目の形が不正です: {sums}（読み込みません）")
            expected[parts[1].strip().lstrip("*")] = parts[0].lower()
        blobs: dict[str, bytes] = {}

        def read(name: str) -> bytes:
            path = folder / name
            if name not in expected or not path.is_file():
                raise SystemExit(f"[中止] モデルのファイルか、その識別値がありません: {path}（読み込みません）")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != expected[name]:
                raise SystemExit(f"[中止] 識別値が一致しません: {path}（差し替え・破損の可能性。読み込みません）")
            return data

        # meta.json を先に照合し、その中の部品の一覧（照合済み）に従って、残りを照合する
        blobs["meta.json"] = read("meta.json")
        meta = json.loads(blobs["meta.json"].decode("utf-8"))
        files = meta.get("files", list(cls.FILES))
        allowed = set(cls.FILES) | set(cls.OPTIONAL_FILES)
        if not set(cls.FILES) <= set(files) or not set(files) <= allowed:
            raise SystemExit(f"[中止] モデルの部品の一覧が不正です: {files}")
        for name in files:
            if name not in blobs:
                blobs[name] = read(name)
        if tuple(meta["labels"]) != LABELS:
            raise SystemExit("[中止] モデルのラベルの並びが、このコードと違います")
        if meta.get("feature_set", "plus") not in FEATURE_SETS:
            raise SystemExit(f"[中止] このコードが知らない特徴量の組です: {meta.get('feature_set')}")
        booster = lgb.Booster(model_str=blobs["model.txt"].decode("utf-8"))
        if booster.num_feature() != len(meta["feature_names"]):
            raise SystemExit("[中止] モデルの列の数が、meta.json の特徴量の数と一致しません（部品の取り違え）")
        # 品詞（Sudachi）は、学習時と同じ状態にそろえる（品詞が -1 のまま判定すると、黙って精度が落ちる）
        if meta.get("feature_set") == "ja":
            set_sudachi("require" if meta.get("sudachi") else "off")
        skip = ("feature_names", "trans", "start", "labels", "kind", "feature_set", "files")
        info = {k: v for k, v in meta.items() if k not in skip}
        parts = joblib.load(io.BytesIO(blobs["ngram.joblib"]))
        ngram = NgramLR.__new__(NgramLR)
        ngram.vec, ngram.lr = parts["vec"], parts["lr"]
        emb = None
        if "emb_head.joblib" in files:
            e_info = meta.get("embed") or {}
            if embed_model is None:
                raise SystemExit("[中止] このモデルは埋め込みを使います。学習時と同じ埋め込みモデルのフォルダを "
                                 f"--embed-model で指定してください（学習時: {e_info.get('name', '?')}）")
            embedder = LineEmbedder(embed_model, prefix=e_info.get("prefix", EMB_PREFIX), cache_path=emb_cache)
            if embedder.digest != e_info.get("digest"):
                raise SystemExit(f"[中止] 埋め込みモデルが、学習時のものと違います: {embed_model}"
                                 f"（識別値 {embedder.digest[:12]}… ≠ 学習時 {str(e_info.get('digest'))[:12]}…）")
            emb = EmbedMLP(embedder)
            emb.clf = joblib.load(io.BytesIO(blobs["emb_head.joblib"]))["clf"]
        seq = None
        if "seq.npz" in files:
            with np.load(io.BytesIO(blobs["seq.npz"]), allow_pickle=False) as z:
                seq = {k: z[k] for k in z.files}
        return cls(booster, list(meta["feature_names"]), ngram, np.array(meta["trans"]), np.array(meta["start"]),
                   feature_set=meta["feature_set"], info=info, emb=emb, seq=seq)


def compute_threads() -> int:
    """計算のスレッド数。既定は物理コア数（論理 CPU の半分）。全論理 CPU（ハイパースレッド込み）で OpenMP を回すと、
    WSL では LightGBM が数十倍遅くなることを実測した（14 論理 CPU: 8 スレッド 1.0 秒 / 14 スレッド 91 秒）。
    環境変数 MAIL_INGEST_THREADS で上書きできる。"""
    env = os.environ.get("MAIL_INGEST_THREADS", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, (os.cpu_count() or 2) // 2)


def _lgb_params(seed: int) -> dict:
    return {**LGB_PARAMS, "seed": seed, "bagging_seed": seed, "feature_fraction_seed": seed,
            "num_threads": compute_threads()}


def find_rounds(x, y, row_groups, *, seed: int, max_rounds: int = LGB_ROUNDS, patience: int = 30,
                valid_ratio: float = 0.15) -> int:
    """打ち切りで、LightGBM の木の数を決める。学習側のメールの一部（メール単位）を検証に取り分け、
    検証の多クラス対数損失が patience 回続けて改善しなければ止める。返すのは、最も良かった回数。"""
    import lightgbm as lgb
    import numpy as np

    groups = np.asarray(row_groups)
    uniq = np.unique(groups)
    if len(uniq) < 2:
        return max_rounds           # 検証に取り分けられない（グループが 1 つ）。打ち切りをせずに最大の回数で学習する
    rng = np.random.default_rng(seed)
    valid_g = set(rng.choice(uniq, size=max(1, int(len(uniq) * valid_ratio)), replace=False).tolist())
    is_valid = np.array([g in valid_g for g in groups])
    booster = lgb.train(_lgb_params(seed), lgb.Dataset(x[~is_valid], label=y[~is_valid]),
                        num_boost_round=max_rounds,
                        valid_sets=[lgb.Dataset(x[is_valid], label=y[is_valid])],
                        callbacks=[lgb.early_stopping(patience, verbose=False)])
    return max(1, int(booster.best_iteration or max_rounds))


def train_booster(x, y, *, kind: str, seed: int, rounds: int = LGB_ROUNDS):
    if kind == "lightgbm":
        import lightgbm as lgb

        return lgb.train(_lgb_params(seed), lgb.Dataset(x, label=y), num_boost_round=rounds)
    if kind == "hgb":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(max_iter=LGB_ROUNDS, learning_rate=0.06, max_leaf_nodes=15,
                                              min_samples_leaf=8, l2_regularization=1.0, early_stopping=True,
                                              n_iter_no_change=30, random_state=seed).fit(x, y)
    if kind == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(iterations=rounds, learning_rate=0.1, depth=6, random_seed=seed,
                                  loss_function="MultiClass", verbose=False).fit(x, y)
    raise ValueError(f"未知のモデル: {kind}")


def predict_booster(model: Any, kind: str, x):
    import numpy as np

    if kind == "lightgbm":
        return model.predict(x, num_threads=compute_threads())
    p = model.predict_proba(x)
    out = np.zeros((len(x), len(LABELS)))
    for j, c in enumerate(model.classes_):
        out[:, int(c)] = p[:, j]
    return out


# =============================================================================
# 7. 行ごとの判定（推論と評価で同じ関数を使う）
# =============================================================================
@dataclass(frozen=True)
class Profile:
    name: str
    force_contact: frozenset[str] | None   # 規則で必ず消す連絡先の種別（None = すべて）
    force_greet: frozenset[str]            # 規則で必ず消す挨拶の種別
    protect_contact: frozenset[str]        # モデルが消すと判定しても残す連絡先の種別
    protect_greet: frozenset[str]          # モデルが消すと判定しても残す挨拶の種別
    force_hdr_cont: bool = True


PROFILES = {
    # 学習データ用（find_noise と同じ考え方）: 連絡先・日時だけの行、挨拶の定型句は、規則で必ず消す
    "training": Profile("training", None, frozenset({"greet.phrase", "greet.intro", "greet.thanks"}),
                        frozenset(), frozenset()),
    # 不正検知の証拠用: 日時だけの行、お礼・詫びの行は残す（いつ・誰が・何を謝ったかが証拠になりうる）
    "evidence": Profile("evidence", frozenset({"qhdr"}), frozenset({"greet.phrase", "greet.intro"}),
                        frozenset({"datetime"}), frozenset({"greet.thanks"})),
}


@dataclass
class Decision:
    keep: bool
    label: str
    score: float
    reason: str
    kept_text: str = ""
    removed_text: str = ""

    @property
    def partial(self) -> bool:
        return self.keep and bool(self.removed_text)


def _sentence_split(ln: Line, d: Decision, profile: Profile, forced: str) -> Decision:
    """挨拶と本文が同居する行で、挨拶の文だけを落とす。"""
    sents = split_sentences(ln.body)
    if len(sents) < 2:
        return d
    kinds = [greet_kind(nfkc(s)) for s in sents]
    is_g = [bool(k) and k not in profile.protect_greet for k in kinds]
    subst = [not g and len(nfkc(s)) >= 6 and bool(re.search(r"[ぁ-んァ-ヶ一-龥]", s)) for s, g in zip(sents, is_g)]
    if not any(is_g) or all(is_g):
        return d
    kept = "".join(s for s, g in zip(sents, is_g) if not g)
    dropped = "".join(s for s, g in zip(sents, is_g) if g)
    if not d.keep and (d.label in ("greet", "body") or forced.startswith("rule:greet")) and any(subst):
        return Decision(True, d.label, d.score, "partial:greet", kept, dropped)
    if d.keep:
        return Decision(True, d.label, d.score, "partial:greet", kept, dropped)
    return d


def decide_lines(lines: Sequence[Line], proba, hdr_addr_cont: Sequence[float], *, profile: Profile,
                 threshold: float = 0.9, sentence_split: bool = False,
                 structural: set[int] | None = None, protect_chat: bool = True) -> list[Decision]:
    """行ごとに、残すか消すかを決める。structural は、引用の分割でヘッダと分かった行（必ず除いて、項目は Meta に移す）。"""
    structural = structural or set()
    out: list[Decision] = []
    score = proba[:, 1:].sum(axis=1) if len(proba) else []
    arg = proba.argmax(axis=1) if len(proba) else []
    for i, ln in enumerate(lines):
        nb = ln.nb
        if i in structural:
            out.append(Decision(False, "qhdr", 1.0, "structure:header", "", ln.text))
            continue
        if subject_line(nb):
            out.append(Decision(True, "qhdr", float(score[i]), "keep:subject", ln.body, ""))
            continue
        ck, gk = contact_kind(nb), greet_kind(nb)
        forced = ""
        if ck and (profile.force_contact is None or ck in profile.force_contact):
            forced = f"rule:contact:{ck}"
        elif gk and gk in profile.force_greet:
            forced = f"rule:{gk}"
        elif hdr_addr_cont[i] and profile.force_hdr_cont:
            forced = "rule:hdr_addr_cont"
        protect = ((protect_chat and protected_chat(nb)) or (ck in profile.protect_contact)
                   or (gk in profile.protect_greet))
        lab = LABELS[int(arg[i])]
        s = float(score[i])
        if forced:
            d = Decision(False, lab, s, forced, "", ln.text)
        elif s >= threshold and not protect:
            d = Decision(False, lab, s, f"model:{lab}", "", ln.text)
        else:
            d = Decision(True, lab, s, "protect" if (protect and s >= threshold) else "model:keep", ln.body, "")
        if sentence_split:
            d = _sentence_split(ln, d, profile, forced)
        out.append(d)
    return out


# =============================================================================
# 8. 学習データ（find_noise の目視ゴールド。データとしてだけ読む）と、学習・評価
# =============================================================================
@dataclass
class GoldMail:
    idx: int
    text: str
    sender: str
    split: str
    corpus: str
    labels: list[str]
    limit: int
    lines: list[Line] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)


def _sep_only(ln: Line) -> bool:
    nb = ln.body.strip()
    if not nb or RE_SYMBOL_ONLY.match(nb):
        return True
    return bool(RE_SEP_RUN.search(nb)) and len(re.findall(r"[0-9A-Za-z぀-ヿ一-鿿]", nb)) <= 3


def load_gold(gold_dir: Path) -> list[GoldMail]:
    import pandas as pd

    sample = pd.read_parquet(gold_dir / "gold_sample.parquet")
    raw = json.loads((gold_dir / "gold_labels.json").read_text(encoding="utf-8"))
    mismatched = 0
    out: list[GoldMail] = []
    for k, ranges in raw.items():
        if k.startswith("_"):
            continue
        r = sample.iloc[int(k)]
        text = str(r["original_text"])
        lines = split_lines(text)
        n = len(lines)
        if "n_lines" in r and int(r["n_lines"]) != n:
            mismatched += 1
        lab = ["body"] * n
        for a, b, t in ranges:
            for i in range(a, min(b, n - 1) + 1):
                lab[i] = t
        for i, ln in enumerate(lines):
            if lab[i] == "body" and _sep_only(ln):
                lab[i] = "sep"
        sender = ""
        for col in ("sender_name", "sender_email"):
            v = r.get(col)
            if isinstance(v, str) and v.strip():
                sender = v.strip().lower()
                break
        units, _ = segment_mail(lines, own_header_at_top=True)
        out.append(GoldMail(int(k), text, sender, str(r.get("split", "dev")), str(r.get("corpus", "")), lab,
                            int(r.get("limit", n)), lines, units))
    if mismatched:
        print(f"[注意] 行の数え方が、ゴールドの n_lines と食い違うメール: {mismatched} 通（ラベルの位置がずれる可能性）")
    return out


def _group(m: GoldMail, how: str) -> str:
    return m.sender if (how == "sender" and m.sender) else f"mail{m.idx}"


def _ngram_rows(mails: Sequence[GoldMail]) -> tuple[list[str], list[str]]:
    texts, labs = [], []
    for m in mails:
        for k in range(min(m.limit, len(m.lines))):
            texts.append(m.lines[k].text)
            labs.append(m.labels[k])
    return texts, labs


def stack_features(train: Sequence[GoldMail], target: Sequence[GoldMail], groups: Sequence[str], make: Any,
                   inner_folds: int = 3) -> tuple[dict[int, Any], dict[int, Any], Any]:
    """重ねる確率の列（n-gram の LR・埋め込みの MLP）を作る。学習側は内側の交差検証の予測（自分を見ていないモデル）、
    対象側は学習側全体で学習したモデルの予測。make(): fit(texts, labels) と predict(texts) を持つモデルを返す。"""
    from sklearn.model_selection import GroupKFold

    tr_out: dict[int, Any] = {}
    groups = list(groups)
    if len(set(groups)) < 2:
        groups = [f"mail{m.idx}" for m in train]     # グループが 1 つだけなら、メール単位で分ける（分けられないと例外になる）
    gk = GroupKFold(n_splits=min(inner_folds, len(set(groups))))
    for a, b in gk.split(list(range(len(train))), groups=groups):
        mdl = make().fit(*_ngram_rows([train[i] for i in a]))
        for i in b:
            m = train[i]
            tr_out[m.idx] = ngram_context(mdl.predict([ln.text for ln in m.lines]))
    full = make().fit(*_ngram_rows(train))
    tg_out = {m.idx: ngram_context(full.predict([ln.text for ln in m.lines])) for m in target}
    return tr_out, tg_out, full


def ngram_features(train: Sequence[GoldMail], target: Sequence[GoldMail], groups: Sequence[str],
                   inner_folds: int = 3) -> tuple[dict[int, Any], dict[int, Any], NgramLR]:
    return stack_features(train, target, groups, NgramLR, inner_folds)


# 特徴量の並列計算（fork できる OS だけ。子は純粋な Python と Sudachi だけを使い、LightGBM・torch は使わない）
_POOL: dict[str, Any] = {}


def _feat_worker(i: int) -> tuple[int, Feats]:
    m = _POOL["mails"][i]
    st = stats_with_doc(_POOL["stats"], m.text, m.sender) if _POOL["add_self"] else _POOL["stats"]
    return m.idx, line_features(m.lines, st, m.sender, feature_set=_POOL["fs"], units=m.units)


def default_jobs() -> int:
    return max(1, min(8, (os.cpu_count() or 2) // 2))


def _can_fork() -> bool:
    return "fork" in multiprocessing.get_all_start_methods()


def _feats_for(mails: Sequence[GoldMail], stats: LineStats, feature_set: str, *,
               add_self: bool = False, jobs: int = 1) -> dict[int, Feats]:
    """add_self: 行頻度に、そのメール自身の分を足して特徴量を作る（unseen の評価側。run と同じ「自分を含む」条件にする）。"""
    if jobs > 1 and len(mails) >= 64 and _can_fork():
        sudachi_tokenizer()          # 親で 1 回だけ辞書を読み、子に引き継ぐ
        _POOL.update(mails=list(mails), stats=stats, fs=feature_set, add_self=add_self)
        try:
            with multiprocessing.get_context("fork").Pool(jobs) as pool:
                return dict(pool.map(_feat_worker, range(len(mails)), chunksize=8))
        finally:
            _POOL.clear()
    return {m.idx: line_features(m.lines, stats_with_doc(stats, m.text, m.sender) if add_self else stats, m.sender,
                                 feature_set=feature_set, units=m.units) for m in mails}


def _stack_names(stacked: dict[str, dict[int, Any]]) -> list[str]:
    return [c for key in STACK_COLS if key in stacked for c in STACK_COLS[key]]


def fit_on(train: Sequence[GoldMail], feats: dict[int, Feats], stacked: dict[str, dict[int, Any]], *, kind: str,
           seed: int, feature_set: str, ngram: NgramLR | None = None, rounds: int | None = None,
           early_stop: bool = True, group_of: Any = None) -> NoiseModel:
    """stacked: 重ねる確率の列（"ng" は必須、"emb" は任意）。メールの番号 → (行数, 27)。
    rounds を渡せば、その回数で学習する。渡さず early_stop なら、打ち切りで回数を決めてから、学習側の全部で学習し直す。
    group_of: 打ち切り用の検証を取り分ける単位（評価の分割と同じ単位にする。省略時はメール単位）。"""
    import numpy as np

    names = list(next(feats[m.idx].names for m in train if len(feats[m.idx])))
    keys = [k for k in STACK_COLS if k in stacked]
    xs, ys, gs = [], [], []
    for m in train:
        lim = min(m.limit, len(m.lines))
        if lim == 0:
            continue
        xs.append(np.hstack([feats[m.idx].x[:lim]] + [stacked[k][m.idx][:lim].astype(np.float32) for k in keys]))
        ys.extend(LABEL_INDEX[c] for c in m.labels[:lim])
        gs.extend([group_of(m) if group_of else f"mail{m.idx}"] * lim)
    x_all, y_all = np.vstack(xs), np.array(ys)
    if rounds is None:
        rounds = find_rounds(x_all, y_all, gs, seed=seed) if (early_stop and kind == "lightgbm") else LGB_ROUNDS
    booster = train_booster(x_all, y_all, kind=kind, seed=seed, rounds=rounds)
    trans, start = fit_transitions(m.labels[:min(m.limit, len(m.lines))] for m in train)
    return NoiseModel(booster, names + _stack_names(stacked), ngram, trans, start, feature_set=feature_set, kind=kind,
                      info={"rounds": rounds})


def _stacked_for(stacked: dict[str, dict[int, Any]], idx: int) -> dict[str, Any]:
    return {k: v[idx] for k, v in stacked.items()}


def oof_booster(train: Sequence[GoldMail], feats: dict[int, Feats], stacked: dict[str, dict[int, Any]], *,
                kind: str, seed: int, feature_set: str, rounds: int, group_of: Any,
                inner_folds: int = 3) -> dict[int, Any]:
    """学習側の各メールについて、そのメールを見ていない LightGBM の確率（BiGRU の学習の入力。自分を見た確率で
    学習すると、実際より自信のある入力に慣れて、評価・本番で補正が効かなくなる）。"""
    from sklearn.model_selection import GroupKFold

    groups = [group_of(m) for m in train]
    if len(set(groups)) < 2:
        groups = [f"mail{m.idx}" for m in train]
    out: dict[int, Any] = {}
    for a, b in GroupKFold(n_splits=min(inner_folds, len(set(groups)))).split(list(range(len(train))), groups=groups):
        mdl = fit_on([train[i] for i in a], feats, stacked, kind=kind, seed=seed, feature_set=feature_set,
                     rounds=rounds, early_stop=False)
        for i in b:
            m = train[i]
            out[m.idx] = mdl.booster_proba(feats[m.idx], _stacked_for(stacked, m.idx))
    return out


def fit_seq(train: Sequence[GoldMail], oof: dict[int, Any], feats: dict[int, Feats], *, seed: int,
            group_of: Any, log: Any = None) -> dict:
    samples = []
    for m in train:
        lim = min(m.limit, len(m.lines))
        y = [LABEL_INDEX[c] if k < lim and not subject_line(m.lines[k].nb) else -100 for k, c in enumerate(m.labels)]
        samples.append((oof[m.idx], feats[m.idx], y, group_of(m)))
    return train_seq(samples, seed=seed, log=log)


EVAL_CONDITIONS = (
    "採点の条件: run と同じ判定（training の用途・引用の分割で分かったヘッダの行は必ず除く）。"
    "件名の行は分母から外し、規則で除く連絡先の行は「除いてよい行」に数える（find_noise の evaluate.py と同じ）。"
    "メルマガ・自動送信の前段の除外はしない（全件を採点）。行頻度は正解データ 730 通だけから作る"
    "（find_noise の公表値はコーパス全体の行頻度なので、数字を直接は比べない）。"
    "unseen を送信者単位の分割で使うと、評価側のメールの送信者は必ず未知になる（送信者ごとの行頻度が効かない）ので、"
    "run（同じ送信者の他のメールも入力にある）より悲観的な値になる。")


def score_mails(mails: Sequence[GoldMail], probas: dict[int, Any], feats: dict[int, Feats],
                models: dict[int, NoiseModel], *, threshold: float, smooth: float,
                sentence_split: bool, protect_chat: bool = True) -> dict[str, float]:
    """EVAL_CONDITIONS のとおりに採点する。"""
    nan = float("nan")
    prof = PROFILES["training"]
    tp = n_del = n_y = body_fp = body_n = partial = 0
    sig_hit = sig_n = greet_hit = greet_n = 0
    for m in mails:
        model = models[m.idx]
        p = smooth_proba(probas[m.idx], model.trans, model.start, smooth)
        hc = feats[m.idx].col("hdr_addr_cont")
        structural = {k for u in m.units for k in u.header_lines}
        ds = decide_lines(m.lines, p, hc, profile=prof, threshold=threshold, sentence_split=sentence_split,
                          structural=structural, protect_chat=protect_chat)
        for k in range(min(m.limit, len(m.lines))):
            if subject_line(m.lines[k].nb):
                continue
            gold = m.labels[k]
            d = ds[k]
            dropped = not d.keep
            partial += int(d.partial)
            c = d.reason.startswith("rule:contact")
            y = gold != "body" or c
            body = gold == "body" and not c
            tp += int(dropped and y)
            n_del += int(dropped)
            n_y += int(y)
            body_fp += int(dropped and body)
            body_n += int(body)
            if gold == "sig":
                sig_n += 1
                sig_hit += int(dropped)
            if gold == "greet":
                greet_n += 1
                greet_hit += int(dropped)
    nm = len(mails)
    return {"通": nm, "適合率": tp / n_del if n_del else nan, "再現率": tp / n_y if n_y else nan,
            "本文誤削除": body_fp, "本文誤削除/100通": 100 * body_fp / max(nm, 1), "本文行": body_n,
            "sig再現率": sig_hit / sig_n if sig_n else nan, "greet再現率": greet_hit / greet_n if greet_n else nan,
            "部分削除行": partial}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _make_embedder(args: argparse.Namespace) -> LineEmbedder | None:
    if not getattr(args, "embed_model", ""):
        return None
    cache = Path(args.emb_cache) if getattr(args, "emb_cache", "") else None
    return LineEmbedder(Path(args.embed_model), cache_path=cache)


def cmd_evaluate(args: argparse.Namespace) -> None:
    from sklearn.model_selection import GroupKFold

    if args.out and not args.overwrite:
        o = Path(args.out)
        for p in (o, o.with_name(o.stem + "_seeds.csv")):
            if p.exists():
                raise SystemExit(f"[中止] 出力が既にあります: {p}（上書きするなら --overwrite）")
    if args.seq:
        importlib.import_module("torch")  # 事前の依存確認
    t0 = time.time()
    if args.no_sudachi:
        set_sudachi("off")
    print(EVAL_CONDITIONS)
    gold = load_gold(Path(args.gold_dir))
    dev = [m for m in gold if m.split != "test"]
    test = [m for m in gold if m.split == "test"]
    dev_groups = [_group(m, args.group) for m in dev]
    folds = list(GroupKFold(n_splits=args.folds).split(list(range(len(dev))), groups=dev_groups))
    # 分割の一覧。最後は「dev 全部で学習 → test を予測」
    parts = [("dev", [dev[i] for i in tr], [dev[i] for i in va]) for tr, va in folds]
    if test:
        parts.append(("test", dev, test))
    group_of = lambda m: _group(m, args.group)  # noqa: E731
    _log(f"ゴールド {len(gold)} 通（dev {len(dev)}・test {len(test)}）。分割の単位: {args.group}・{args.folds} 分割・"
         f"並列 {args.jobs}")
    if "ja" in args.feature_sets:
        _log(f"日本語の特徴量: 品詞（Sudachi）{'あり ' + _SUDACHI['version'] if sudachi_tokenizer() else 'なし'}")

    # 重ねる確率の列は、ラベルだけに依存する（行頻度・シードに依存しない）ので、分割ごとに 1 回だけ作る
    ng_cache = []
    for _, tr, tg in parts:
        tr_ng, tg_ng, _ = ngram_features(tr, tg, [group_of(m) for m in tr])
        ng_cache.append((tr_ng, tg_ng))
        _log(f"  n-gram の準備（分割 {len(ng_cache)}/{len(parts)}）: 累計 {time.time() - t0:.0f} 秒")
    embedder = _make_embedder(args)
    emb_cache = []
    if embedder is not None:
        embedder.encode([ln.text for m in gold for ln in m.lines])
        embedder.save_cache()
        _log(f"  埋め込み: {len(embedder.mem)} 種類の行・累計 {time.time() - t0:.0f} 秒")
        for _, tr, tg in parts:
            tr_e, tg_e, _ = stack_features(tr, tg, [group_of(m) for m in tr], lambda: EmbedMLP(embedder))
            emb_cache.append((tr_e, tg_e))
            _log(f"  埋め込みの MLP（分割 {len(emb_cache)}/{len(parts)}）: 累計 {time.time() - t0:.0f} 秒")

    batch_stats = LineStats.build((m.text, m.sender) for m in gold)
    feat_cache: dict[tuple, dict] = {}
    variants = ([("改良前", 0.0, False)] + [(f"平滑化{w}", w, False) for w in args.smooth]
                + [("文単位", 0.0, True)] + [(f"平滑化{w}+文単位", w, True) for w in args.smooth])
    seq_variants = [("系列(BiGRU)", 0.0, False), ("系列(BiGRU)+文単位", 0.0, True)]
    protects = {"on": [True], "off": [False], "both": [True, False]}[args.protect]
    stacks = [("LGB", False)] + ([("LGB+埋め込み", True)] if embedder is not None else [])
    rows = []
    for seed in range(args.seeds):
        for stats_mode in args.stats:
            for fs in args.feature_sets:
                for kind in args.models:
                    for comp, use_emb in stacks:
                        probas: dict[str, dict[int, Any]] = {"dev": {}, "test": {}}
                        probas_seq: dict[str, dict[int, Any]] = {"dev": {}, "test": {}}
                        models: dict[str, dict[int, NoiseModel]] = {"dev": {}, "test": {}}
                        feats_all: dict[str, dict[int, Feats]] = {"dev": {}, "test": {}}
                        do_seq = args.seq and kind == "lightgbm"
                        for p_i, (name, tr, tg) in enumerate(parts):
                            if stats_mode == "batch":
                                st, sid = batch_stats, "batch"
                            else:
                                st, sid = LineStats.build((m.text, m.sender) for m in tr), f"unseen{p_i}"
                            fc = feat_cache.setdefault((sid, fs), {})
                            fc.update(_feats_for([m for m in tr if m.idx not in fc], st, fs, jobs=args.jobs))
                            # unseen の評価側は、学習側の行頻度に、そのメール自身の分だけを足す（run と同じく「自分を含む」）
                            fc.update(_feats_for([m for m in tg if m.idx not in fc], st, fs,
                                                 add_self=stats_mode == "unseen", jobs=args.jobs))
                            tr_ng, tg_ng = ng_cache[p_i]
                            st_tr, st_tg = {"ng": tr_ng}, {"ng": tg_ng}
                            if use_emb:
                                st_tr["emb"], st_tg["emb"] = emb_cache[p_i]
                            t1 = time.time()
                            # 木の数は、分割ごとに、その学習側の中で、評価と同じ単位で取り分けた検証で、打ち切りにより決める
                            model = fit_on(tr, fc, st_tr, kind=kind, seed=seed, feature_set=fs,
                                           rounds=LGB_ROUNDS if args.no_early_stop else None, group_of=group_of)
                            for m in tg:
                                probas[name][m.idx] = model.booster_proba(fc[m.idx], _stacked_for(st_tg, m.idx))
                                models[name][m.idx] = model
                                feats_all[name][m.idx] = fc[m.idx]
                            msg = f"木 {model.info['rounds']} 本"
                            if do_seq:
                                oof = oof_booster(tr, fc, st_tr, kind=kind, seed=seed, feature_set=fs,
                                                  rounds=model.info["rounds"], group_of=group_of)
                                w = fit_seq(tr, oof, fc, seed=seed, group_of=group_of)
                                for m in tg:
                                    probas_seq[name][m.idx] = seq_predict(w, probas[name][m.idx], fc[m.idx])
                                msg += f"・BiGRU {int(w['epochs'])} エポック"
                            _log(f"    {stats_mode}・{fs}・{kind}・{comp}・分割 {p_i + 1}/{len(parts)}: {msg}・"
                                 f"{time.time() - t1:.0f} 秒（累計 {time.time() - t0:.0f} 秒）")
                        sources = [(probas, variants)] + ([(probas_seq, seq_variants)] if do_seq else [])
                        for part, mails in (("dev", dev), ("test", test)):
                            if not mails:
                                continue
                            for src, vs in sources:
                                for vname, w_s, ss in vs:
                                    for pc in protects:
                                        for th in args.threshold:
                                            r = score_mails(mails, src[part], feats_all[part], models[part],
                                                            threshold=th, smooth=w_s, sentence_split=ss,
                                                            protect_chat=pc)
                                            rows.append({"part": part, "行頻度": stats_mode, "特徴量": fs,
                                                         "モデル": kind, "構成": comp, "変更": vname,
                                                         "会話文の保護": "あり" if pc else "なし", "しきい値": th,
                                                         "seed": seed, **r})
                        _log(f"  seed {seed}・{stats_mode}・{fs}・{kind}・{comp}: {time.time() - t0:.0f} 秒")

    import pandas as pd

    df = pd.DataFrame(rows)
    keys = ["part", "行頻度", "特徴量", "モデル", "構成", "変更", "会話文の保護", "しきい値"]
    agg = df.groupby(keys, sort=False).agg(
        適合率=("適合率", "mean"), 再現率=("再現率", "mean"), 再現率sd=("再現率", "std"),
        本文誤削除=("本文誤削除", "mean"), 本文誤削除per100=("本文誤削除/100通", "mean"),
        sig再現率=("sig再現率", "mean"), greet再現率=("greet再現率", "mean"),
        部分削除行=("部分削除行", "mean")).reset_index()
    with pd.option_context("display.width", 240, "display.max_rows", 1000, "display.float_format", "{:.4f}".format):
        print(f"\n{args.seeds} シード平均（構成の比較は、本文誤削除が同じくらいになるしきい値どうしで行う）\n")
        print(agg.to_string(index=False))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        agg.to_csv(out, index=False, encoding="utf-8-sig")
        df.to_csv(out.with_name(out.stem + "_seeds.csv"), index=False, encoding="utf-8-sig")
        print(f"\n書き出し: {out}")
    print(f"所要 {time.time() - t0:.0f} 秒")


def cmd_train(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())) and not args.overwrite:
        raise SystemExit(f"[中止] 出力フォルダが空ではありません: {out}（上書きするなら --overwrite）")
    if args.seq:
        importlib.import_module("torch")
    t0 = time.time()
    if args.no_sudachi:
        set_sudachi("off")
    gold = load_gold(Path(args.gold_dir))
    mails = [m for m in gold if not (args.dev_only and m.split == "test")]
    group_of = lambda m: _group(m, args.group)  # noqa: E731
    groups = [group_of(m) for m in mails]
    tr_ng, _, full_ng = ngram_features(mails, [], groups)
    stacked: dict[str, dict[int, Any]] = {"ng": tr_ng}
    _log(f"n-gram: {time.time() - t0:.0f} 秒")
    embedder = _make_embedder(args)
    emb_head = None
    if embedder is not None:
        tr_e, _, emb_head = stack_features(mails, [], groups, lambda: EmbedMLP(embedder, seed=args.seed))
        stacked["emb"] = tr_e
        embedder.save_cache()
        _log(f"埋め込みの MLP: {time.time() - t0:.0f} 秒")
    # 行頻度も、学習に使うメールだけから作る（--dev-only のとき、test のメールが行頻度に入ると、後で test で測る値に漏れる）
    stats = LineStats.build((m.text, m.sender) for m in mails)
    feats = _feats_for(mails, stats, args.feature_set, jobs=args.jobs)
    _log(f"特徴量: {time.time() - t0:.0f} 秒")
    model = fit_on(mails, feats, stacked, kind="lightgbm", seed=args.seed, feature_set=args.feature_set,
                   ngram=full_ng, early_stop=not args.no_early_stop, group_of=group_of)
    model.emb = emb_head
    _log(f"LightGBM（木 {model.info['rounds']} 本）: {time.time() - t0:.0f} 秒")
    if args.seq:
        oof = oof_booster(mails, feats, stacked, kind="lightgbm", seed=args.seed, feature_set=args.feature_set,
                          rounds=model.info["rounds"], group_of=group_of)
        model.seq = fit_seq(mails, oof, feats, seed=args.seed, group_of=group_of, log=_log)
        _log(f"BiGRU: {time.time() - t0:.0f} 秒")
    use_sudachi = args.feature_set == "ja" and sudachi_tokenizer() is not None
    model.info.update({
        "trained_on": f"{len(mails)} 通（{Path(args.gold_dir).name}）", "threshold": args.threshold,
        "smooth": args.smooth, "created": datetime.now().isoformat(timespec="seconds"),
        "sudachi": use_sudachi, "sudachi_version": _SUDACHI["version"] if use_sudachi else "",
        "embed": ({"name": embedder.model_dir.name, "digest": embedder.digest, "prefix": embedder.prefix,
                   "max_chars": EMB_MAX_CHARS} if embedder is not None else None),
        "seq": bool(args.seq), "libs": _lib_versions(), "train_seconds": round(time.time() - t0)})
    model.save(out)
    print(f"書き出し: {out}（{'・'.join(model.files)}・SHA256SUMS）")
    print("SHA256SUMS は、モデルのフォルダの外の、信頼できる場所に複写して保管し、run の --model-sums で指定してください"
          "（フォルダの中の一覧は、差し替えを防げないので run では受け付けません）")
    if embedder is not None:
        print(f"run では、同じ埋め込みモデルのフォルダを --embed-model で指定してください: {embedder.model_dir}")


# =============================================================================
# 9. 取り込み（eml・CSV・Excel）
# =============================================================================
@dataclass
class RawMail:
    key: str
    source: str
    meta: Meta
    body: str
    origin: str          # eml / columns


_RE_JA_COMMON = re.compile(r"[\t\n\r\x20-\x7eぁ-んァ-ヶー一-龥々　-〿！-～]")


def _ja_score(text: str) -> float:
    """日本語の文書らしさ（よく使う文字の割合）。文字コードを取り違えると、半角カナや記号が増えて下がる。"""
    return len(_RE_JA_COMMON.findall(text)) / max(len(text), 1)


def _try_decode(data: bytes, codecs: Iterable[str]) -> str | None:
    for c in codecs:
        try:
            return data.decode(c)
        except (LookupError, UnicodeDecodeError):
            continue
    return None


def _decode_bytes(data: bytes, charset: str | None) -> str:
    """バイト列を文字列にする。指定の文字コードで読めなければ、次の順で推定する:
    ESC の印がある → ISO-2022-JP（拡張を含む）。ASCII のバイトだけで書かれるので、utf-8 としても「読めてしまう」ため先に試す
    → utf-8（厳密に。他の日本語の文字コードのバイト列では、ほぼ必ず失敗する）
    → cp932 と EUC-JP の両方で読み、日本語らしさの高い方（cp932 を先に試すと、EUC-JP が「読めてしまい」化ける）。"""
    cs = (charset or "").lower()
    if cs in ("shift_jis", "shift-jis", "sjis", "x-sjis"):
        cs = "cp932"
    if cs in ("unknown-8bit", "x-unknown", "us-ascii", "ascii"):
        cs = ""                     # 指定が無いのと同じ（生の 8 ビットのヘッダ・ASCII と偽った本文）
    if cs:
        codecs = [cs, "iso2022_jp_ext"] if cs.startswith("iso-2022-jp") else [cs]
        if (text := _try_decode(data, codecs)) is not None:
            return text
    if b"\x1b" in data and (text := _try_decode(data, ("iso-2022-jp", "iso2022_jp_ext"))) is not None:
        return text
    if (text := _try_decode(data, ("utf-8",))) is not None:
        return text
    cands = [t for t in (_try_decode(data, (c,)) for c in ("cp932", "euc_jp")) if t is not None]
    if cands:
        return max(cands, key=_ja_score)
    return data.decode("utf-8", errors="replace")


def _decode_header_value(value: Any) -> str:
    """ヘッダの値を文字列にする。生の 8 ビット（cp932 等をそのまま書いたヘッダ）は、Header オブジェクトのまま
    decode_header に渡すと、unknown-8bit のバイト列として取り出せる（str にすると、置換文字に化けて戻せない）。"""
    if value is None:
        return ""
    out = []
    for frag, cs in decode_header(value if not isinstance(value, str) else value):
        out.append(_decode_bytes(frag, cs) if isinstance(frag, bytes) else frag)
    return re.sub(r"\s+", " ", "".join(out)).strip()


class _HtmlText(HTMLParser):
    """HTML の本文を行のテキストにする。blockquote の中は、深さぶん「> 」を付ける（引用の深さを保つ）。"""
    BLOCK = {"p", "div", "tr", "li", "table", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.depth = 0
        self.skip = 0

    def newline(self) -> None:
        text = "".join(self.buf).strip()
        self.buf = []
        self.lines.append(("> " * self.depth + text) if text else "")

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style", "head"):
            self.skip += 1
        elif tag == "br":
            self.newline()
        elif tag == "blockquote":
            self.newline()
            self.depth += 1
        elif tag in self.BLOCK:
            self.newline()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag == "blockquote":
            self.newline()
            self.depth = max(0, self.depth - 1)
        elif tag in self.BLOCK:
            self.newline()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.buf.append(re.sub(r"\s+", " ", data))


def html_to_text(markup: str) -> str:
    p = _HtmlText()
    p.feed(markup)
    p.close()
    p.newline()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(p.lines)).strip()


def read_eml(path: Path) -> RawMail:
    msg = BytesParser(policy=policy.compat32).parsebytes(path.read_bytes())

    def hdr(name: str) -> str:
        return " ".join(_decode_header_value(v) for v in msg.get_all(name, []))

    meta = Meta(origin="eml")
    addrs = parse_addresses(hdr("From"))
    meta.sender = addrs[0] if addrs else Address()
    meta.to, meta.cc, meta.bcc = (parse_addresses(hdr(n)) for n in ("To", "Cc", "Bcc"))
    meta.date_raw = hdr("Date")
    meta.date_iso, meta.date_conf = parse_date(meta.date_raw)
    meta.subject = hdr("Subject")
    meta.message_id = hdr("Message-ID").strip("<> ")
    meta.in_reply_to = hdr("In-Reply-To").strip("<> ")
    meta.references = [r.strip("<>") for r in hdr("References").split() if r.strip("<>")]

    plain: list[str] = []
    markup: list[str] = []

    def walk(part: Any) -> None:
        ctype = part.get_content_type()
        if ctype == "message/rfc822":
            inner = part.get_payload()
            subj = _decode_header_value(inner[0].get("Subject")) if isinstance(inner, list) and inner else ""
            meta.attachments.append(f"(添付メール) {subj}".strip())
            return
        if part.is_multipart():
            for sub in part.get_payload():
                walk(sub)
            return
        filename = _decode_header_value(part.get_filename()) if part.get_filename() else ""
        disp = (part.get("Content-Disposition") or "").lower()
        if filename or disp.startswith("attachment"):
            meta.attachments.append(filename or "(名前なし)")
            return
        data = part.get_payload(decode=True) or b""
        if ctype == "text/plain":
            plain.append(_decode_bytes(data, part.get_content_charset()))
        elif ctype == "text/html":
            markup.append(_decode_bytes(data, part.get_content_charset()))

    walk(msg)
    body = "\n".join(plain) if plain else html_to_text("\n".join(markup))
    return RawMail(key=path.stem, source=str(path), meta=meta, body=body, origin="eml")


COLUMN_ALIASES = {
    "id": ("id", "doc_id", "docid", "文書id", "mail_id", "controlnumber", "control number", "begdoc", "番号"),
    "text": ("text", "本文", "body", "original_text", "message", "内容"),
    "from": ("from", "差出人", "送信者", "sender", "sender_email", "from_email", "差出人アドレス"),
    "from_name": ("sender_name", "差出人名", "from_name", "送信者名"),
    "to": ("to", "宛先", "受信者"),
    "cc": ("cc",),
    "bcc": ("bcc",),
    "date": ("date", "送信日時", "日付", "sent", "sentdate", "date sent", "datesent", "送信日", "受信日時"),
    "subject": ("subject", "件名", "title", "題名"),
    "message_id": ("message-id", "message_id", "messageid"),
    "in_reply_to": ("in-reply-to", "in_reply_to", "inreplyto"),
    "references": ("references",),
}


def _chatbot_root() -> None:
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)


def _read_csv(path: Path) -> list[list[str]]:
    try:
        _chatbot_root()
        from rag_core.document_converters.csv_converter import read_csv_rows
    except ImportError:
        read_csv_rows = None
    if read_csv_rows is not None:
        return read_csv_rows(path)[0]
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932", "euc_jp"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit(f"[中止] 文字コードを判定できません: {path}")
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    return list(csv.reader(text.splitlines(), delimiter=delim))


def _read_xlsx(path: Path, sheet: str) -> list[list[str]]:
    try:
        _chatbot_root()
        from rag_core.document_converters import xlsx_converter as X
        from rag_core.document_converters.limits import open_office_zip
    except ImportError:
        X = None
    if X is None:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb[sheet] if sheet else wb.worksheets[0]
        return [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    with open_office_zip(path) as zf:
        shared, formats, d1904 = X._shared_strings(zf), X._cell_formats(zf), X._uses_1904_dates(zf)
        chosen = [t for t in X._sheet_targets(zf) if not sheet or t[0] == sheet]
        if not chosen:
            raise SystemExit(f"[中止] シートがありません: {sheet}")
        rows = X._sheet_rows(zf, chosen[0][1], shared, formats, d1904)
    width = max((max(r) for r in rows.values() if r), default=-1) + 1
    return [[rows[r].get(c, "") for c in range(width)] for r in sorted(rows)]


def read_table(path: Path, overrides: dict[str, str], sheet: str = "") -> list[RawMail]:
    rows = _read_xlsx(path, sheet) if path.suffix.lower() in (".xlsx", ".xlsm") else _read_csv(path)
    if not rows:
        return []
    header = [nfkc(str(h)).strip().lower() for h in rows[0]]
    cols: dict[str, int] = {}
    for fname, aliases in COLUMN_ALIASES.items():
        want = [nfkc(overrides[fname]).strip().lower()] if fname in overrides else list(aliases)
        for a in want:
            if a in header:
                cols[fname] = header.index(a)
                break
        if fname in overrides and fname not in cols:
            raise SystemExit(f"[中止] 列が見つかりません: {overrides[fname]}（{path.name} の列: {rows[0]}）")
    if "text" not in cols:
        raise SystemExit(f"[中止] 本文の列が見つかりません（--col text=<列名> で指定）。列: {rows[0]}")
    out: list[RawMail] = []
    for r_i, row in enumerate(rows[1:], 2):
        def get(name: str, row: list = row) -> str:
            j = cols.get(name)
            return str(row[j]).strip() if j is not None and j < len(row) and row[j] is not None else ""

        meta = Meta(origin="columns")
        if get("from"):
            _apply_field(meta, "from", get("from"))
        if get("from_name") and not meta.sender.name:
            meta.sender.name = get("from_name")
        for fname in ("to", "cc", "bcc", "date", "subject"):
            if get(fname):
                _apply_field(meta, fname, get(fname))
        meta.message_id = get("message_id").strip("<> ")
        meta.in_reply_to = get("in_reply_to").strip("<> ")
        meta.references = [x.strip("<>") for x in get("references").split() if x.strip("<>")]
        key = get("id") or f"{path.stem}#{r_i}"
        out.append(RawMail(key=key, source=f"{path.name}:{r_i}", meta=meta, body=get("text"), origin="columns"))
    return out


MAX_EML_BYTES = 50 * 1024 * 1024     # これより大きい eml は読まない（添付の巨大なメールでメモリを使い切らない）


def _error_row(source: str, stage: str, e: BaseException, key: str = "") -> dict:
    return {"key": key, "source": source, "stage": stage, "error": type(e).__name__, "message": str(e)[:300]}


def read_eml_dir(files: Sequence[Path], failures: list[dict]) -> list[RawMail]:
    """eml を 1 通ずつ読む。壊れた・大きすぎる eml は、failures に記録して飛ばす（全体は止めない）。"""
    out = []
    for p in files:
        try:
            size = p.stat().st_size
            if size > MAX_EML_BYTES:
                raise ValueError(f"{size} バイト（上限 {MAX_EML_BYTES}）")
            out.append(read_eml(p))
        except Exception as e:
            failures.append(_error_row(str(p), "read", e, p.stem))
    return out


def iter_inputs(path: Path, overrides: dict[str, str], sheet: str = "",
                failures: list[dict] | None = None) -> list[RawMail]:
    failures = [] if failures is None else failures
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.suffix.lower() == ".eml")
        if not files:
            raise SystemExit(f"[中止] フォルダに .eml がありません: {path}（msg は eml に変換してから入れてください）")
        return read_eml_dir(files, failures)
    suf = path.suffix.lower()
    if suf == ".eml":
        return read_eml_dir([path], failures)
    if suf in (".csv", ".tsv", ".xlsx", ".xlsm"):
        return read_table(path, overrides, sheet)
    if suf == ".msg":
        raise SystemExit("[中止] msg は読みません。eml に変換してから入れてください")
    raise SystemExit(f"[中止] 読めない形式です: {path}（eml のフォルダ・.eml・.csv・.tsv・.xlsx・.xlsm）")


# =============================================================================
# 10. 処理の本体（構造化 → ノイズ除去 → 引用の照合 → スレッド化）
# =============================================================================
@dataclass
class Message:
    key: str
    source: str
    meta: Meta
    text: str
    context: str = ""
    restored: bool = False
    from_key: str = ""           # 復元したメールなら、どのメールの引用から作ったか
    parent: str = ""
    parent_basis: str = ""       # header（In-Reply-To）/ quote（引用の照合）/ quote_chain（引用の連なり）/ subject
    n_lines: int = 0
    n_removed: int = 0
    quoted_extra: str = ""       # 引用が手元のメールと重複したが、引用側にだけあった記述（duplicate_extra）
    status: str = "ok"           # ok / segment_failed（引用の分割に失敗。全体を 1 通として扱った）/
                                 # noise_failed（分類に失敗。何も消していない）/ noise_skipped（長すぎて分類しない）


_RE_REPLY_PREFIX = re.compile(r"^\s*((re|fw|fwd|返信|転送)\s*(\[\d+\])?\s*[:：]\s*)+", re.I)


def subject_norm(s: str) -> str:
    return re.sub(r"\s+", " ", _RE_REPLY_PREFIX.sub("", nfkc(s or "")).strip().lower())


def _shingles(text: str, k: int = 4) -> set[str]:
    t = fold_for_match(text)
    return {t[i:i + k] for i in range(len(t) - k + 1)} if len(t) >= k else set()


class ShingleIndex:
    """引用された本文が、手元のどのメールの本文と同じかを探す（文字 4-gram の重なり率）。"""

    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = {}
        self.post: dict[str, list[str]] = defaultdict(list)

    def add(self, key: str, sh: set[str]) -> None:
        if len(sh) < 8:
            return
        self.sets[key] = sh
        for s in sh:
            self.post[s].append(key)

    def best(self, sh: set[str], exclude: set[str]) -> tuple[str, float, float]:
        """最も近いメールの (キー, 引用側の包含率, 候補側の包含率)。
        引用側の包含率 = 引用の 4-gram のうち、候補にもある割合（高ければ、引用の内容は候補で全部わかる）。
        候補側の包含率 = 候補の 4-gram のうち、引用にもある割合（高ければ、候補が引用に含まれている）。
        引用側を優先して選ぶ（情報を失わない重複を先に探す）。"""
        if len(sh) < 8:
            return "", 0.0, 0.0
        counts: Counter[str] = Counter()
        for s in sh:
            keys = self.post.get(s, ())
            if len(keys) > 2000:
                continue
            counts.update(keys)
        best: tuple[str, float, float] = ("", 0.0, 0.0)
        for key, c in counts.most_common(20):
            if key in exclude:
                continue
            cand = (key, c / len(sh), c / len(self.sets[key]))
            if (cand[1], cand[2]) > (best[1], best[2]):
                best = cand
        return best


MIN_QUOTE_COVER = 0.3      # 候補側の包含率で一致とみなすときの、引用側の包含率の下限
MIN_REF_SHINGLES = 20      # 候補側の包含率で一致とみなすときの、候補の 4-gram の数の下限（短いメールの偶然の一致を防ぐ）
MIN_EXTRA_CHARS = 6        # 引用にしか無い記述を「追記」として残す文字数の下限（記号・空白を除く）


def _extra_lines(text: str, ref_sh: set[str]) -> str:
    """引用の行のうち、照合先のメールにほとんど無い行（4-gram の半分以上が照合先に無い）だけを返す。"""
    out = []
    for line in text.split("\n"):
        sh = _shingles(line)
        if sh and len(sh - ref_sh) / len(sh) >= 0.5:      # 4 字未満の行（名前だけ等）は、判定できないので入れない
            out.append(line)
    return "\n".join(out)


def _merge_meta(base: Meta, extra: Meta | None) -> Meta:
    if extra is None:
        return base
    if base.empty():
        base.origin = extra.origin          # 項目が本文の先頭のヘッダからしか取れなかった
    if not base.sender.label():
        base.sender = extra.sender
    for name in ("to", "cc", "bcc", "attachments"):
        if not getattr(base, name):
            setattr(base, name, getattr(extra, name))
    if not base.date_raw:
        base.date_raw, base.date_iso, base.date_conf = extra.date_raw, extra.date_iso, extra.date_conf
    if not base.subject:
        base.subject = extra.subject
    return base


@dataclass
class RunResult:
    messages: list[Message]
    segments: list[dict]
    removed: list[dict]
    failures: list[dict] = field(default_factory=list)


def _run_features(todo: Sequence[tuple], stats: LineStats, feature_set: str, jobs: int,
                  failures: list[dict]) -> dict[str, Feats]:
    """run の特徴量（1 通ずつ失敗を隔離する。並列は fork できる OS だけ）。"""
    out: dict[str, Feats] = {}
    items = [(r.key, r.source, lines, units, meta.sender.key()) for r, lines, units, meta, _ in todo]
    if jobs > 1 and len(items) >= 64 and _can_fork():
        sudachi_tokenizer()
        _POOL.update(items=items, stats=stats, fs=feature_set)
        try:
            with multiprocessing.get_context("fork").Pool(jobs) as pool:
                results = pool.map(_run_feat_worker, range(len(items)), chunksize=8)
        finally:
            _POOL.clear()
    else:
        _POOL.update(items=items, stats=stats, fs=feature_set)
        try:
            results = [_run_feat_worker(i) for i in range(len(items))]
        finally:
            _POOL.clear()
    for key, source, feats, err in results:
        if err is not None:
            failures.append({"key": key, "source": source, "stage": "features", "error": err[0], "message": err[1]})
        else:
            out[key] = feats
    return out


def _run_feat_worker(i: int) -> tuple[str, str, Feats | None, tuple[str, str] | None]:
    key, source, lines, units, sender = _POOL["items"][i]
    try:
        return key, source, line_features(lines, _POOL["stats"], sender, feature_set=_POOL["fs"], units=units), None
    except Exception as e:
        return key, source, None, (type(e).__name__, str(e)[:300])


def process(raws: Sequence[RawMail], *, model: NoiseModel | None, profile: Profile, threshold: float,
            smooth: float, sentence_split: bool, match_threshold: float, jobs: int = 1, chunk: int = 500,
            max_lines: int = 5000, failures: list[dict] | None = None) -> RunResult:
    # 同じキーが重なったら、番号を足す（照合と親子の取り違えを防ぐ）。足した名前が、元から有る別のキーや、
    # 既に使った名前とぶつからないよう、空きが見つかるまで番号を進める
    original = {r.key for r in raws}
    used: set[str] = set()
    for r in raws:
        if r.key in used:
            n = 2
            while f"{r.key}~{n}" in used or f"{r.key}~{n}" in original:
                n += 1
            r.key = f"{r.key}~{n}"
        used.add(r.key)
    failures = failures if failures is not None else []
    parsed = []
    seg_status: dict[str, str] = {}
    for r in raws:
        lines = split_lines(r.body)
        try:
            units, own = segment_mail(lines, own_header_at_top=(r.origin != "eml"))
        except Exception as e:
            # 引用の分割に失敗 → 全体を新しく書かれた 1 通として扱う（引用の照合・ヘッダの取り出しはしない）
            failures.append(_error_row(r.source, "segment", e, r.key))
            units, own = [Unit("new", list(range(len(lines))))], None
            seg_status[r.key] = "segment_failed"
        parsed.append((r, lines, units, _merge_meta(r.meta, own)))
    stats = LineStats.build((r.body, meta.sender.key()) for r, _, _, meta in parsed)

    def keep_all(lines: Sequence[Line], structural: set[int]) -> list[Decision]:
        return [Decision(False, "qhdr", 1.0, "structure:header", "", ln.text) if i in structural
                else Decision(True, "body", 0.0, "no-model", ln.body, "") for i, ln in enumerate(lines)]

    # 分類: まとめて処理する（特徴量は並列、埋め込みはまとめて計算）。メモリを使い切らないよう、chunk 通ずつ
    decisions: dict[str, list[Decision]] = {}
    noise_status: dict[str, str] = {}
    for c0 in range(0, len(parsed), chunk):
        part = parsed[c0:c0 + chunk]
        todo = []
        for r, lines, units, meta in part:
            structural = {k for u in units for k in u.header_lines}
            if model is None or not lines:
                decisions[r.key] = keep_all(lines, structural)
            elif len(lines) > max_lines:
                decisions[r.key] = keep_all(lines, structural)
                noise_status[r.key] = "noise_skipped"
            else:
                todo.append((r, lines, units, meta, structural))
        if not todo:
            continue
        feats_by = _run_features(todo, stats, model.feature_set, jobs, failures)
        try:
            model.warm([ln.text for r, lines, *_ in todo if r.key in feats_by for ln in lines])
        except Exception as e:      # 埋め込みをまとめて計算できない → 1 通ずつ計算させる（下の try で個別に失敗を記録）
            failures.append(_error_row("（まとめた埋め込み）", "embed", e))
        for r, lines, units, meta, structural in todo:
            if r.key not in feats_by:          # 特徴量の計算に失敗（failures に記録済み）
                decisions[r.key] = keep_all(lines, structural)
                noise_status[r.key] = "noise_failed"
                continue
            try:
                feats = feats_by[r.key]
                proba = model.predict(feats, [ln.text for ln in lines], smooth=smooth)
                decisions[r.key] = decide_lines(lines, proba, feats.col("hdr_addr_cont"), profile=profile,
                                                threshold=threshold, sentence_split=sentence_split,
                                                structural=structural)
            except Exception as e:
                # 分類に失敗したメールは、何も消さずに残す（黙って本文を失わない）
                failures.append(_error_row(r.source, "noise", e, r.key))
                decisions[r.key] = keep_all(lines, structural)
                noise_status[r.key] = "noise_failed"
    if model is not None and model.emb is not None:
        model.emb.embedder.save_cache()

    messages: dict[str, Message] = {}
    quoted: dict[str, list[tuple[int, Unit, str]]] = defaultdict(list)
    seg_rows: dict[tuple[str, int, str], dict] = {}
    removed: list[dict] = []
    for r, lines, units, meta in parsed:
        ds = decisions[r.key]
        for u_i, u in enumerate(units):
            for k in u.header_lines + u.lines + u.context_lines:
                d = ds[k]
                if d.removed_text:
                    removed.append({"key": r.key, "unit": u_i, "line": k, "label": d.label, "reason": d.reason,
                                    "score": round(d.score, 3), "text": d.removed_text})

        def kept(idx: Sequence[int], ds: list[Decision] = ds) -> str:
            return "\n".join(ds[k].kept_text for k in idx if ds[k].keep and ds[k].kept_text)

        new = units[0]
        ctx = kept(new.context_lines)
        messages[r.key] = Message(key=r.key, source=r.source, meta=meta, text=kept(new.lines),
                                  context="\n".join("> " + t for t in ctx.split("\n") if t),
                                  n_lines=len(lines), n_removed=sum(1 for d in ds if not d.keep),
                                  status="+".join(x for x in (seg_status.get(r.key), noise_status.get(r.key)) if x)
                                  or "ok")
        seg_rows[(r.key, 0, "new")] = {"key": r.key, "unit": 0, "kind": "new", "status": "new", "ref": "",
                                       "score": "", "from": meta.sender.label(), "date": meta.date_iso,
                                       "subject": meta.subject, "text": messages[r.key].text}
        if new.context_lines:
            seg_rows[(r.key, 0, "context")] = {"key": r.key, "unit": 0, "kind": "context", "status": "context",
                                               "ref": "", "score": "", "from": "", "date": "", "subject": "",
                                               "text": messages[r.key].context}
        for u_i, u in enumerate(units[1:], 1):
            quoted[r.key].append((u_i, u, kept(u.lines)))

    # 引用の照合: 手元のメールと重複 → 参照に置き換える。無い → 復元したメールとして残す
    index = ShingleIndex()
    for m in messages.values():
        index.add(m.key, _shingles(m.text))
    order = sorted(messages.values(), key=lambda m: (time_key(m.meta), m.key))
    resolved: dict[str, list[str]] = defaultdict(list)
    for m in order:
        for u_i, u, text in quoted.get(m.key, []):
            sh = _shingles(text)
            ref, cov_q, cov_c = index.best(sh, exclude={m.key})
            row = {"key": m.key, "unit": u_i, "kind": "quoted", "from": u.meta.sender.label(),
                   "date": u.meta.date_iso, "subject": u.meta.subject, "score": round(cov_q, 3),
                   "score_ref": round(cov_c, 3)}
            ref_sh = index.sets.get(ref, set()) if ref else set()
            # 一致の判定:
            #   ・引用側の包含率が高い（引用の内容の大半が、手元のメールにある）
            #   ・または、候補（手元のメール）が引用に丸ごと含まれる。ただし、短い候補は他の引用にも偶然含まれやすいので、
            #     候補が十分長く（4-gram が MIN_REF_SHINGLES 以上）、引用側の包含率も MIN_QUOTE_COVER 以上のときだけ
            matched = bool(ref) and (cov_q >= match_threshold or (
                cov_c >= match_threshold and cov_q >= MIN_QUOTE_COVER and len(ref_sh) >= MIN_REF_SHINGLES))
            # 一致しても、引用にしか無い行（手元のメールに無い記述）は、必ず求めて残す（黙って消さない）
            extra = _extra_lines(text, ref_sh) if matched else ""
            if len(sh) < 8:
                row.update(status="empty", ref="", text=text)
                resolved[m.key].append("")
            elif matched and len(fold_for_match(extra)) < MIN_EXTRA_CHARS:
                # 引用の内容は、手元のメールで全部わかる → 参照に置き換える
                row.update(status="duplicate", ref=ref, text="")
                resolved[m.key].append(ref)
            elif matched:
                # 引用側にしか無い記述がある → 参照に置き換え、その行だけを残す
                row.update(status="duplicate_extra", ref=ref, text=extra)
                resolved[m.key].append(ref)
                m.quoted_extra = "\n".join(x for x in (m.quoted_extra, f"[{ref} の引用にだけある記述]\n{extra}") if x)
            else:
                rkey = f"{m.key}#q{u_i}"
                messages[rkey] = Message(key=rkey, source=f"{m.source}（引用 {u_i}）", meta=u.meta, text=text,
                                         restored=True, from_key=m.key, n_lines=len(u.lines))
                index.add(rkey, sh)
                row.update(status="restored", ref=rkey, text=text)
                resolved[m.key].append(rkey)
            seg_rows[(m.key, u_i, "quoted")] = row
    segments = sorted(seg_rows.values(), key=lambda s: (s["key"], s["unit"], s["kind"] != "new"))

    def set_parent(child: str, parent: str, basis: str) -> None:
        if not parent or child == parent or messages[child].parent:
            return
        p = parent
        while p:                         # 循環を作らない
            if p == child:
                return
            p = messages[p].parent if p in messages else ""
        messages[child].parent, messages[child].parent_basis = parent, basis

    by_msgid = {m.meta.message_id: m.key for m in messages.values() if m.meta.message_id}
    for key in [m.key for m in order]:
        m = messages[key]
        chain = [k for k in resolved.get(key, []) if k]
        if m.meta.in_reply_to and m.meta.in_reply_to in by_msgid:
            set_parent(key, by_msgid[m.meta.in_reply_to], "header")
        elif m.meta.references and m.meta.references[-1] in by_msgid:
            set_parent(key, by_msgid[m.meta.references[-1]], "header")
        if chain:
            set_parent(key, chain[0], "quote")
        for a, b in zip(chain, chain[1:]):
            if messages[a].restored:
                set_parent(a, b, "quote_chain")

    def root_of(key: str) -> str:
        while messages[key].parent:
            key = messages[key].parent
        return key

    # 引用の連なりの奥にあった、復元したメールが、どこにもつながらないとき（連なりの前の 1 通に、既に別の親がある）:
    # 引用した側のスレッドの根より古ければ、その根の親にする（会話の始まりとして、同じスレッドに入れる）
    has_child = {m.parent for m in messages.values() if m.parent}
    for m in list(messages.values()):
        if not m.restored or m.parent or m.key in has_child or not m.from_key:
            continue
        root = root_of(m.from_key)
        r = messages[root]
        if root != m.key and not r.parent and (date_utc(r.meta.date_iso) is None or is_before(m.meta, r.meta)):
            set_parent(root, m.key, "quote_order")
    # 件名の照合（返信・転送の印があるのに、親が分からないメール）: 同じ件名で、参加者が重なる、直前のメール
    by_subject: dict[str, list[Message]] = defaultdict(list)
    for m in messages.values():
        if subject_norm(m.meta.subject):
            by_subject[subject_norm(m.meta.subject)].append(m)
    for m in list(messages.values()):
        if m.parent or not m.meta.date_iso or not _RE_REPLY_PREFIX.match(nfkc(m.meta.subject or "")):
            continue
        people = {a.key() for a in [m.meta.sender, *m.meta.to, *m.meta.cc] if a.key()}
        cands = [c for c in by_subject[subject_norm(m.meta.subject)]
                 if c.key != m.key and is_before(c.meta, m.meta)
                 and people & {a.key() for a in [c.meta.sender, *c.meta.to, *c.meta.cc] if a.key()}]
        if cands:
            set_parent(m.key, max(cands, key=lambda c: time_key(c.meta)).key, "subject")
    return RunResult(list(messages.values()), segments, removed, failures)


def conversation_rows(messages: Sequence[Message]) -> list[dict]:
    """スレッドごとに、親 → 子の順（同じ親の子は時刻順）に並べた会話の流れ。"""
    by_key = {m.key: m for m in messages}
    children: dict[str, list[Message]] = defaultdict(list)
    roots = []
    for m in messages:
        (children[m.parent] if m.parent in by_key else roots).append(m)

    def sort_key(m: Message) -> tuple:
        return (time_key(m.meta), m.key)

    rows: list[dict] = []
    for root in sorted(roots, key=sort_key):
        order = 0
        stack = [(root, 0)]
        while stack:
            m, depth = stack.pop()
            order += 1
            rows.append({"thread_id": root.key, "order": order, "depth": depth, "key": m.key,
                         "date": m.meta.date_iso or m.meta.date_raw, "from": m.meta.sender.label(),
                         "to": "; ".join(a.label() for a in m.meta.to),
                         "cc": "; ".join(a.label() for a in m.meta.cc), "subject": m.meta.subject,
                         "restored": int(m.restored), "parent": m.parent, "parent_basis": m.parent_basis,
                         "text": m.text, "context": m.context, "quoted_extra": m.quoted_extra})
            for c in sorted(children.get(m.key, []), key=sort_key, reverse=True):
                stack.append((c, depth + 1))
    return rows


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(v: Any) -> Any:
    """Excel で開いたとき数式として実行されないよう、= + - @ で始まる文字列の先頭に ' を付ける（CSV インジェクション対策）。
    原文と 1 文字違うので、照合に使うときは --no-csv-escape で書き出す。"""
    return "'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) else v


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str], *, escape: bool = True) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(columns), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: _csv_safe(v) for k, v in row.items()} if escape else row)
    os.replace(tmp, path)        # 書き込みの途中で落ちても、半端なファイルを残さない


def cmd_run(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if out.exists() and not out.is_dir():
        raise SystemExit(f"[中止] --out はフォルダを指定してください（ファイルがあります）: {out}")
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise SystemExit(f"[中止] 出力フォルダが空ではありません: {out}（上書きするなら --overwrite）")
    if not args.model and not args.no_noise:
        raise SystemExit("[中止] --model（学習済みのモデルのフォルダ）か、--no-noise（ノイズ除去なし）を指定してください")
    if args.model and not args.model_sums:
        raise SystemExit("[中止] --model には --model-sums（モデルのフォルダの外に保管した SHA256SUMS）が必要です")
    overrides = dict(kv.split("=", 1) for kv in args.col)
    t0 = time.time()
    model = (NoiseModel.load(Path(args.model), Path(args.model_sums),
                             embed_model=Path(args.embed_model) if args.embed_model else None,
                             emb_cache=Path(args.emb_cache) if args.emb_cache else None) if args.model else None)
    failures: list[dict] = []
    raws = iter_inputs(Path(args.input), overrides, args.sheet, failures)
    # しきい値・平滑化の重みは、指定が無ければ、学習時にモデルへ保存した値を使う
    if args.threshold is None:
        args.threshold = float(model.info.get("threshold", 0.9)) if model else 0.9
    if args.smooth is None:
        args.smooth = float(model.info.get("smooth", 0.0)) if model else 0.0
    res = process(raws, model=model, profile=PROFILES[args.profile], threshold=args.threshold, smooth=args.smooth,
                  sentence_split=args.sentence_split, match_threshold=args.match_threshold, jobs=args.jobs,
                  max_lines=args.max_lines, failures=failures)
    esc = not args.no_csv_escape
    conv = conversation_rows(res.messages)
    thread_of = {r["key"]: r["thread_id"] for r in conv}
    out.mkdir(parents=True, exist_ok=True)
    msg_rows = [{"thread_id": thread_of.get(m.key, ""), "key": m.key, "restored": int(m.restored),
                 "source": m.source, "date": m.meta.date_iso,
                 "date_utc": (lambda d: d.isoformat() if d else "")(date_utc(m.meta.date_iso)),
                 "date_raw": m.meta.date_raw,
                 "date_conf": m.meta.date_conf, "from": m.meta.sender.label(),
                 "to": "; ".join(a.label() for a in m.meta.to), "cc": "; ".join(a.label() for a in m.meta.cc),
                 "bcc": "; ".join(a.label() for a in m.meta.bcc), "subject": m.meta.subject,
                 "message_id": m.meta.message_id, "in_reply_to": m.meta.in_reply_to,
                 "attachments": "; ".join(m.meta.attachments), "meta_origin": m.meta.origin, "parent": m.parent,
                 "parent_basis": m.parent_basis, "n_lines": m.n_lines, "n_removed": m.n_removed,
                 "text_chars": len(m.text), "status": m.status} for m in res.messages]
    _write_csv(out / "messages.csv", msg_rows, list(msg_rows[0].keys()) if msg_rows else ["key"], escape=esc)
    _write_csv(out / "conversation.csv", conv,
               ["thread_id", "order", "depth", "key", "date", "from", "to", "cc", "subject", "restored", "parent",
                "parent_basis", "text", "context", "quoted_extra"], escape=esc)
    _write_csv(out / "segments.csv", res.segments,
               ["key", "unit", "kind", "status", "ref", "score", "score_ref", "from", "date", "subject", "text"],
               escape=esc)
    _write_csv(out / "removed.csv", res.removed, ["key", "unit", "line", "label", "reason", "score", "text"],
               escape=esc)
    _write_csv(out / "failed.csv", res.failures, ["key", "source", "stage", "error", "message"], escape=esc)
    n_orig = sum(1 for m in res.messages if not m.restored)
    run = {"version": VERSION, "input": str(args.input), "n_input": len(raws), "n_messages": n_orig,
           "n_restored": len(res.messages) - n_orig, "n_threads": len({r["thread_id"] for r in conv}),
           "quoted": dict(Counter(s["status"] for s in res.segments if s["kind"] == "quoted")),
           "profile": args.profile, "threshold": args.threshold, "smooth": args.smooth,
           "sentence_split": args.sentence_split, "match_threshold": args.match_threshold,
           "model": str(args.model or "なし"), "model_info": model.info if model else {},
           "parent_basis": dict(Counter(m.parent_basis or "（なし）" for m in res.messages)),
           "removed_reasons": dict(Counter(r["reason"].split(":")[0] + ":" + r["label"] for r in res.removed)),
           "status": dict(Counter(m.status for m in res.messages)),
           "failures": dict(Counter(f["stage"] for f in res.failures)), "csv_escape": esc,
           "seq": bool(model and model.seq is not None), "embed": bool(model and model.emb is not None),
           "libs": _lib_versions(), "seconds": round(time.time() - t0),
           "created": datetime.now().isoformat(timespec="seconds")}
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: run[k] for k in ("n_input", "n_messages", "n_restored", "n_threads", "quoted",
                                          "parent_basis", "status", "failures", "seconds")}, ensure_ascii=False))
    if res.failures:
        print(f"[注意] 失敗 {len(res.failures)} 件（failed.csv）。分類に失敗したメールは、何も消さずに残しています")
    print(f"書き出し: {out}（messages.csv・conversation.csv・segments.csv・removed.csv・failed.csv・run.json。"
          "原文を含むのでコミットしない）")


# =============================================================================
# 11. コマンド
# =============================================================================
def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="メールの取り込み・構造化・ノイズ除去・会話の流れ")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="eml・CSV・Excel を処理して、メタ情報・会話の流れ・除いた部分を書き出す")
    r.add_argument("--input", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--model", default="", help="train で作ったモデルのフォルダ")
    r.add_argument("--no-noise", action="store_true", help="ノイズ除去をしない（構造化・引用・スレッドだけ）")
    r.add_argument("--profile", choices=sorted(PROFILES), default="evidence")
    r.add_argument("--model-sums", default="",
                   help="モデルのフォルダの外に保管した SHA256SUMS（train が出力したものを、信頼できる場所に複写しておく）")
    r.add_argument("--threshold", type=float, default=None, help="省略時は、モデルに保存した値（無ければ 0.9）")
    r.add_argument("--smooth", type=float, default=None,
                   help="行の並びの平滑化の重み（0〜1）。省略時は、モデルに保存した値（無ければ 0）")
    r.add_argument("--sentence-split", action="store_true", help="挨拶と本文が同居する行で、挨拶の文だけを落とす")
    r.add_argument("--match-threshold", type=float, default=0.6, help="引用を、手元のメールと同じとみなす重なり率")
    r.add_argument("--col", action="append", default=[], help="列名の指定（例: text=本文 from=差出人 date=送信日時）")
    r.add_argument("--sheet", default="")
    r.add_argument("--embed-model", default="", help="埋め込みを使うモデルのとき: 学習時と同じ埋め込みモデルのフォルダ")
    r.add_argument("--emb-cache", default="", help="埋め込みのキャッシュ（.npz。リポジトリ内・コミットしない場所）")
    r.add_argument("--jobs", type=int, default=default_jobs(), help="特徴量の計算の並列数")
    r.add_argument("--max-lines", type=int, default=5000, help="これより行の多いメールは、分類しない（何も消さない）")
    r.add_argument("--no-csv-escape", action="store_true", help="= + - @ で始まるセルの先頭に ' を付けない")
    r.add_argument("--overwrite", action="store_true")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("train", help="find_noise の目視ゴールドで学習し、モデルのフォルダを作る")
    t.add_argument("--gold-dir", required=True, help="gold_sample.parquet と gold_labels.json のあるフォルダ")
    t.add_argument("--out", required=True)
    t.add_argument("--feature-set", choices=FEATURE_SETS, default="ja")
    t.add_argument("--embed-model", default="", help="埋め込みモデルのフォルダ（例: ruri-v3-30m）。指定すると、埋め込みの MLP を重ねる")
    t.add_argument("--emb-cache", default="", help="埋め込みのキャッシュ（.npz。リポジトリ内・コミットしない場所）")
    t.add_argument("--seq", action="store_true", help="行の並びのモデル（BiGRU）を学習する（torch が要る）")
    t.add_argument("--no-sudachi", action="store_true", help="Sudachi が入っていても、品詞の特徴量を使わない")
    t.add_argument("--jobs", type=int, default=default_jobs())
    t.add_argument("--group", choices=("sender", "mail"), default="sender")
    t.add_argument("--dev-only", action="store_true", help="test を学習に使わない")
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--threshold", type=float, default=0.9)
    t.add_argument("--smooth", type=float, default=0.0)
    t.add_argument("--no-early-stop", action="store_true", help="打ち切りをせず、木を 400 本で固定する")
    t.add_argument("--overwrite", action="store_true")
    t.set_defaults(func=cmd_train)

    e = sub.add_parser("evaluate", help="改良前と改良後を、同じ分割・同じ行頻度の条件で比べる")
    e.add_argument("--gold-dir", required=True)
    e.add_argument("--group", choices=("sender", "mail"), default="sender")
    e.add_argument("--folds", type=int, default=5)
    e.add_argument("--seeds", type=int, default=3)
    e.add_argument("--threshold", type=float, nargs="+", default=[0.9, 0.93, 0.95, 0.97],
                   help="複数を指定すると、しきい値ごとに採点する（学習は 1 回。精度と再現率の釣り合いを、同じ本文誤削除で比べる）")
    e.add_argument("--stats", nargs="+", choices=("batch", "unseen"), default=["batch", "unseen"])
    e.add_argument("--feature-sets", nargs="+", choices=FEATURE_SETS, default=["plus", "ja"])
    e.add_argument("--embed-model", default="", help="指定すると「LGB」と「LGB+埋め込み」を比べる")
    e.add_argument("--emb-cache", default="")
    e.add_argument("--seq", action="store_true", help="行の並びのモデル（BiGRU）も比べる")
    e.add_argument("--no-sudachi", action="store_true")
    e.add_argument("--jobs", type=int, default=default_jobs())
    e.add_argument("--models", nargs="+", choices=("lightgbm", "hgb", "catboost"), default=["lightgbm"])
    e.add_argument("--smooth", nargs="+", type=float, default=[0.5])
    e.add_argument("--no-early-stop", action="store_true", help="打ち切りをせず、木を 400 本で固定する（以前と同じ）")
    e.add_argument("--protect", choices=("on", "off", "both"), default="both",
                   help="短い会話文の保護（相槌を消さない規則）をかけて採点するか。both は両方を出す")
    e.add_argument("--overwrite", action="store_true")
    e.add_argument("--out", default="", help="結果の CSV（原文は含まない）")
    e.set_defaults(func=cmd_evaluate)

    args = ap.parse_args(argv)
    # BLAS（scikit-learn の LR・MLP）・OpenMP のスレッドも、物理コア数までにする（compute_threads の説明のとおり、
    # 全論理 CPU だと WSL で極端に遅くなる。n-gram の LR の学習で 17.6 秒 → 4.8 秒を実測）
    n = str(compute_threads())
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, n)
    try:
        from threadpoolctl import threadpool_limits
    except ImportError:
        args.func(args)
        return
    with threadpool_limits(int(n)):
        args.func(args)


if __name__ == "__main__":
    main()
