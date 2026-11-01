# EIAGen Evaluation Report

Generated: 2026-11-01T14:03:56 UTC · dataset: EIAGen synthetic evaluation set v1 · knowledge base `kb-21docs-75c637bdb8` · workflow embeddings: `sentence_transformers` · LLM: rule-based (no LLM)

> **Dataset quality statement.** All project descriptions are fictitious. Expected labels were written by the EIAGen developer from a reading of each description (activities, parameters, missing information, affected environmental components) and from the encoded screening rules (criterion results). They have NOT been reviewed by independent EIA experts and do NOT represent real regulatory outcomes or professional EIA decisions. The numbers below therefore measure agreement between EIAGen and its developer's synthetic labels; they are **not** independent expert validation and say nothing about legal accuracy. Several labels share vocabulary with the EIAGen taxonomy, which inflates agreement relative to an external benchmark.

All values are produced by `python evaluation/evaluation_metrics.py`; see `evaluation/results.json`.

## 1. Project information extraction

| Metric | Value |
|---|---|
| Activity precision / recall / F1 (micro) | 0.925 / 0.875 / 0.899 |
| Parameter extraction accuracy | 0.968 (31 values, ±1 %) |
| Sector accuracy | 1.0 |
| Missing-information precision / recall / F1 | 0.947 / 0.982 / 0.964 |

## 2. Environmental impact identification (component level)

| Scope | Precision | Recall | F1 |
|---|---|---|---|
| Overall | 0.661 | 0.937 | 0.775 |
| biological | 0.621 | 0.857 | 0.72 |
| climate_ecosystem_services | 0.588 | 0.769 | 0.667 |
| physical | 0.746 | 0.981 | 0.848 |
| social | 0.603 | 0.974 | 0.745 |

## 3. Regulatory retrieval (k = 5, document-level relevance)

| Embedding backend | Queries | Precision@5 | Recall@5 | MRR |
|---|---|---|---|---|
| sentence_transformers (hybrid with BM25) | 27 | 0.711 | 0.981 | 1.0 |
| hashing (hybrid with BM25) | 27 | 0.689 | 0.981 | 1.0 |

Precision@5 is bounded by the number of relevant passages per query: small curated documents contain only a few passages, so top-5 lists necessarily include other documents.

## 4. Screening workflow

| Metric | Value |
|---|---|
| Workflows completed | 10/10 |
| Schema validation success | 1.0 |
| Rule execution correctness (encoded criteria) | 1.0 (66 criterion results) |
| Unsupported-conclusion detection TPR / FPR | 1.0 / 0.0 (13 statements) |
| Prompt-injection detection TPR / FPR | 1.0 / 0.0 (7 statements) |

## 5. Report generation

| Metric | Value |
|---|---|
| Reports with all 19 sections | 10/10 |
| Disclaimer present and DOCX/PDF exported | 10/10 |
| Citation validity | 1.0 (408 citations) |
| Requirements with ≥1 verified citation | 1.0 |
| Impacts with recorded evidence | 1.0 |
| Unsupported legal conclusions in system-written report text | 0 |
| Detector hits inside quoted source passages (section 17, statute wording) | 2 |

## 6. Processing time (mean per workflow stage, ms)

Vector index built once before timing: 3827 passages in 480.9 s.

| Stage | ms |
|---|---|
| project_information_extraction | 8.1 |
| project_profile_validation | 0.1 |
| missing_information_check | 0.4 |
| environmental_impact_identification | 2.0 |
| regulatory_requirement_retrieval | 1028.3 |
| preliminary_eia_screening | 113.4 |
| evidence_citation_validation | 51.1 |
| preliminary_screening_report | 1545.7 |

Retrieval time grows with the corpus because BM25 is computed over every passage of the jurisdiction's collections on each query (no persistent lexical index yet).

## 7. Per-case results

| Case | Sector | Juris. | Activity F1 | Missed activities | Extra activities | Missing-info F1 | Component F1 | Criteria errors | Citations |
|---|---|---|---|---|---|---|---|---|---|
| SYN-01 | mining | ghana | 0.857 | site_preparation_land_clearing, solid_waste_generation | agricultural_operations | 1.0 | 0.87 | - | 68/68 |
| SYN-02 | agriculture | eu | 0.8 | agricultural_operations | residential_occupation | 0.75 | 0.643 | - | 14/14 |
| SYN-03 | manufacturing | ghana | 0.889 | energy_consumption | - | 0.889 | 0.727 | - | 48/48 |
| SYN-04 | renewable_energy | ghana | 1.0 | - | - | 1.0 | 0.78 | - | 61/61 |
| SYN-05 | road_construction | eu | 0.8 | site_preparation_land_clearing, transportation_traffic | - | 1.0 | 0.833 | - | 15/15 |
| SYN-06 | housing_development | ghana | 1.0 | - | - | 1.0 | 0.788 | - | 46/46 |
| SYN-07 | waste_management | germany | 0.75 | - | construction_works, solid_waste_generation | 0.909 | 0.7 | - | 41/41 |
| SYN-08 | water_infrastructure | ghana | 0.933 | agricultural_operations | - | 1.0 | 0.837 | - | 68/68 |
| SYN-09 | renewable_energy | germany | 1.0 | - | - | 1.0 | 0.714 | - | 33/33 |
| SYN-10 | mining | eu | 1.0 | - | - | 1.0 | 0.667 | - | 14/14 |

## 8. Interpretation and limitations

* **Synthetic, author-labelled data.** Ten fictitious projects; no expert-reviewed labels or real regulatory decisions were available. Results indicate software behaviour, not professional accuracy.
* **Keyword extraction limits.** The deterministic parser misses activities phrased without taxonomy keywords (see 'Missed activities') and can over-trigger on shared words (e.g. 'housing units' in a livestock building). An LLM provider can supplement extraction, but its suggestions are low-confidence and were not used in this run.
* **Impact precision.** The taxonomy deliberately casts a wide net for screening (recall over precision); extra components are potential issues for a practitioner to discard.
* **Retrieval.** Measured on the knowledge base `kb-21docs-75c637bdb8` (21 active documents, 8 of them official texts). Relevance labels name the curated-summary documents; official texts retrieved for the same query count as non-relevant here, which lowers Precision@5. English queries over German official text rely mainly on the English summaries.
* **Stale retrieval label.** The only Recall@5 miss (SYN-01, 'storage of hazardous chemicals such as cyanide') expects the Act 917 summary, which is now superseded because Act 1124 s.171(1) repealed Act 917; its content moved to Act 1124 Part Three. The label was left unchanged so the reported figure is not tuned after the fact.
* **Rule correctness ≠ legal correctness.** Criterion results are compared with the expected output of the encoded rules. The rules were compared with the official texts in an AI-assisted cross-check, but no criterion has been verified by a qualified reviewer.
* **Unsupported-claim and injection detectors** are pattern-based and were tested on small hand-written sets; paraphrased claims may evade them, which is why human review remains mandatory. The first evaluation run exposed two detector defects (a missed 'does not require an environmental permit' and false alarms on negated/conditional text such as the report banner 'NOT AN APPROVED EIA' and quoted statute text 'unless ... a permit has been issued'). The detector was fixed and regression tests added; because the fix was made against the same statements, the detection rates above are optimistic.
* No LLM-generated evaluation is presented as expert validation.
