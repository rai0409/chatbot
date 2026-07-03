# Page Regression Deep Dive

## Finding

normal_011 failed only at page-sensitive level: aligned_test kept the expected document in top5, but the expected page chunk `0022009-090.pdf:p3:c2` was pushed out by hybrid/vector-supported neighboring pages. The corrected scoring restores that page chunk to rank 3 without changing the top1 document-level outcome.

## normal_011

- query: インボイス制度はいつ開始されると書かれているか。
- expected_source_doc: `0022009-090.pdf`
- expected_pages: `[3]`

### Production

- doc_rank: 2
- expected_page_rank: 2
- page_match_at_5: True

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | keyword | 18.738459748649106 | None | 0.92 | 0.015873015873015872 | None |  | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p3:c2` | [3] | keyword | 18.258548259391763 | None | 0.92 | 0.015625 | None |  | 1.0 |
| 3 | `pure_scan_test_144dpi.pdf:p1:c0` | [1] | keyword | 24.074524157175237 | None | 0.66 | 0.01639344262295082 | None |  | 0.0 |
| 4 | `0022009-090.pdf:p1:c0` | [1] | keyword | 23.154819522477794 | None | 0.66 | 0.016129032258064516 | None |  | 0.3333333333333333 |
| 5 | `58887_95105_misc.pdf:ja:p1:b0:h841ad8a67e:parent:9:child:1` | [1] | vector |  | None | 0.11 | 0.01639344262295082 | None |  | 0.0 |

### Aligned Before

- doc_rank: 2
- expected_page_rank: 0
- page_match_at_5: False

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | hybrid | 18.738459748649106 | None | 0.92 | 0.03149801587301587 | None |  | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p2:c1` | [2] | hybrid | 12.03460073388751 | None | 0.66 | 0.028404512489927477 | None |  | 0.6666666666666666 |
| 3 | `pure_scan_test_144dpi.pdf:p2:c1` | [2] | hybrid | 11.37135993894892 | None | 0.66 | 0.027799227799227798 | None |  | 0.3333333333333333 |
| 4 | `0022009-090.pdf:p5:c4` | [5] | hybrid | 3.8805120373980646 | None | 0.26 | 0.029906956136464335 | None |  | 1.0 |
| 5 | `20241105-benefits-individual-guide-manual.pdf:p2:c1` | [2] | hybrid | 3.8018489886543145 | None | 0.52 | 0.02803921568627451 | None |  | 0.3333333333333333 |

### Aligned After

- doc_rank: 2
- expected_page_rank: 3
- page_match_at_5: True

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | hybrid | 18.738459748649106 | 0.6080793738365173 | 0.92 | 0.03149801587301587 | 0.050891861847880776 | 0.006 | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p2:c1` | [2] | hybrid | 12.03460073388751 | 0.6861624717712402 | 0.66 | 0.028404512489927477 | 0.03556797256331623 | 0 | 0.6666666666666666 |
| 3 | `0022009-090.pdf:p3:c2` | [3] | keyword | 18.258548259391763 |  | 0.92 | 0.015625 | 0.034970854825939174 | 0.006 | 1.0 |
| 4 | `pure_scan_test_144dpi.pdf:p2:c1` | [2] | hybrid | 11.37135993894892 | 0.6865395307540894 | 0.66 | 0.027799227799227798 | 0.03489636379312269 | 0 | 0.3333333333333333 |
| 5 | `0022009-090.pdf:p5:c4` | [5] | hybrid | 3.8805120373980646 | 0.5132737159729004 | 0.26 | 0.029906956136464335 | 0.03385500734020414 | 0 | 1.0 |

## normal_012

- query: 適格請求書とはどのような手段だと説明されているか。
- expected_source_doc: `0022009-090.pdf`
- expected_pages: `[3]`

### Production

- doc_rank: 2
- expected_page_rank: 2
- page_match_at_5: True

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | keyword | 6.322027037582238 | None | 0.52 | 0.01639344262295082 | None |  | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p3:c2` | [3] | keyword | 6.160113334352174 | None | 0.52 | 0.016129032258064516 | None |  | 1.0 |
| 3 | `0022009-090.pdf:p15:c14` | [15] | keyword | 5.660746021320132 | None | 0.52 | 0.015873015873015872 | None |  | 0.6666666666666666 |
| 4 | `0022009-090.pdf:p24:c23` | [24] | keyword | 3.5756790129886618 | None | 0.26 | 0.015625 | None |  | 0.6666666666666666 |
| 5 | `58887_95105_misc.pdf:ja:p3:b44:hb132274e0a:parent:14:child:1` | [3] | vector |  | None | 0.03 | 0.01639344262295082 | None |  | 0.0 |

### Aligned Before

- doc_rank: 2
- expected_page_rank: 3
- page_match_at_5: True

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | hybrid | 6.322027037582238 | None | 0.52 | 0.032018442622950824 | None |  | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p15:c14` | [15] | hybrid | 5.660746021320132 | None | 0.52 | 0.031024531024531024 | None |  | 0.6666666666666666 |
| 3 | `0022009-090.pdf:p3:c2` | [3] | hybrid | 6.160113334352174 | None | 0.52 | 0.02946236559139785 | None |  | 1.0 |
| 4 | `0022009-090.pdf:p14:c13` | [14] | hybrid | 3.5387522696354594 | None | 0.26 | 0.030536130536130537 | None |  | 0.3333333333333333 |
| 5 | `0022009-090.pdf:p33:c32` | [33] | hybrid | 2.961939843679791 | None | 0.26 | 0.028949545078577336 | None |  | 0.6666666666666666 |

### Aligned After

- doc_rank: 2
- expected_page_rank: 2
- page_match_at_5: True

| rank | chunk_id | pages | source | bm25 | vector_distance | keyword | rrf | hybrid_score | page_boost | coverage |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `pure_scan_test_144dpi.pdf:p3:c2` | [3] | hybrid | 6.322027037582238 | 0.2982107400894165 | 0.52 | 0.032018442622950824 | 0.043770645326709046 | 0.006 | 0.6666666666666666 |
| 2 | `0022009-090.pdf:p3:c2` | [3] | hybrid | 6.160113334352174 | 0.39649784564971924 | 0.52 | 0.02946236559139785 | 0.041198376924833066 | 0.006 | 1.0 |
| 3 | `0022009-090.pdf:p15:c14` | [15] | hybrid | 5.660746021320132 | 0.3327223062515259 | 0.52 | 0.031024531024531024 | 0.036710605626663034 | 0 | 0.6666666666666666 |
| 4 | `0022009-090.pdf:p14:c13` | [14] | hybrid | 3.5387522696354594 | 0.3322269916534424 | 0.26 | 0.030536130536130537 | 0.03445000576309408 | 0 | 0.3333333333333333 |
| 5 | `0022009-090.pdf:p33:c32` | [33] | hybrid | 2.961939843679791 | 0.2791673541069031 | 0.26 | 0.028949545078577336 | 0.032805739062945316 | 0 | 0.6666666666666666 |

