"""Conversion-accuracy measurement for rag_core.document_converters.

Scores what a converter produced against a hand-written truth file
(<document>.truth.json, format: eval/conversion/truth.py) per document:

- CER / normalized Levenshtein similarity of the linear text
- table cell precision / recall / F1 over (column header, value) pairs,
  merged cells expanded the way the converters repeat them
- heading precision / recall
- reading order (Kendall tau over the expected text blocks)
- header / footer leakage into body chunks, leakage of excluded text
  (e.g. furigana)
- exact match of typed cells (numbers, dates, era dates)
- expected converter warnings (e.g. formula without cached value)

Run `python -m eval.conversion --help`. Output schema: conversion-run-v1
(eval/conversion/harness.py). Guide: docs/operations/conversion_accuracy.md.
"""
