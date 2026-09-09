# BluePaper research and architecture

Dated 2026-08-31. This document records the competitive landscape, current PDF-malware research, design implications, stack decisions, and evaluation requirements for BluePaper. Product scope, threat model, and HTTP API contract live in [README.md](../README.md).

Headline accuracy numbers from papers are **not** deployment SLOs. They are bounded by datasets, parsers, splits, and attack models.

## 1. Positioning

BluePaper sits between low-level forensic utilities and full detonation sandboxes:

- Deeper and more semantic than keyword counters (PDFiD-style).
- Safer, faster, and easier to self-host than Windows reader sandboxes.
- Explanatory rather than neutralizing (unlike CDR, which rebuilds a “safe” file).

The product is an **analysis server in the operator’s Azure tenant**. Differentiation is a reachable behavior graph, parser-differential evidence, recursive polyglot/attachment analysis, visual phishing detection, and object-level evidence for every finding.

## 2. Alternatives

Tools are grouped by role. They are not interchangeable.

### 2.1 PDF-specific static analyzers

| Tool | Role | License / deploy | Strength | Gap vs BluePaper |
| --- | --- | --- | --- | --- |
| [PDFiD / pdf-parser.py](https://github.com/DidierStevens/DidierStevensSuite) | Keyword triage and object inspection | Public domain, local Python | Fast, trusted, YARA-capable | Analyst-driven; no behavior graph or API |
| [peepdf-3](https://github.com/digitalsleuth/peepdf-3) | Interactive object / JS exploration | GPLv3, local CLI | Revisions, encryption, JS manipulation | Optional JS/shellcode deps; not reader-faithful |
| [Origami](https://github.com/gdelugre/origami) | Programmable PDF forensics | LGPLv3, Ruby | Parse/modify/`pdfcop` heuristics | Framework, not a detection product |
| [Pdfalyzer](https://github.com/michelcrypt4d4mus/pdfalyzer) | Object-tree viz + YARA | GPLv3, Python | Visualization | Signature-driven; depends on other tools |
| [pdfstudio](https://github.com/boredchilada/pdfstudio) | Catalog/trigger graph, SARIF/STIX | Python, emerging | Closest open “product” shape | Maturity: object streams, encryption |
| [qpdf](https://github.com/qpdf/qpdf) | Structural check / transform | Apache-2.0 | Robust parser component | Not a detector |
| [pdfcpu](https://github.com/pdfcpu/pdfcpu) | Go PDF library/CLI | Apache-2.0 | Validate, extract, transform | Not a detector |
| [veraPDF](https://github.com/veraPDF/veraPDF-library) | PDF/A and PDF/UA validation | GPLv3+ / MPLv2+ | Compliance | Not threat analysis |

BluePaper uses **qpdf** (and MuPDF) as isolated workers, not as the product. PDFiD-style keyword counts are a triage signal, not the verdict.

### 2.2 Commercial static / file-analysis platforms

| Product | Role | Notes |
| --- | --- | --- |
| [ReversingLabs Spectra Analyze](https://docs.reversinglabs.com/SpectraAnalyze/) | Recursive static decomposition across many formats | [Auxiliary analyzers](https://docs.reversinglabs.com/SpectraAnalyze/analysis-services/auxiliary-analysis/) include PDFiD and PeePDF. Enterprise appliance, quote licensing. |
| [InQuest / OPSWAT Deep File Inspection](https://inquest.net/solutions/solutions) | Embedded-content inspection in network pipelines | Strong phishing/lure context; less PDF-object reasoning than dedicated forensic tools. |

These are broad platforms. BluePaper is PDF-native and explainable at object granularity.

### 2.3 General malware sandboxes

| Product | Role | Limitation for PDF |
| --- | --- | --- |
| [CAPE](https://capev2.readthedocs.io/en/latest/introduction/what.html) | Self-hosted GPLv3 sandbox | Heavy: Linux host, Windows guests, reader licensing |
| [ANY.RUN](https://any.run/plans/) | Hosted interactive sandbox | Cloud workflow; public submissions leak samples. [Static PDF module](https://any.run/cybersecurity-blog/static-discovery-update/) extracts headers/scripts/URLs. |
| [Falcon Sandbox / Hybrid Analysis](https://hybrid-analysis.com/faq) | Public + private/on-prem | Public corpus is unsuitable for confidential files ([privacy guidance](https://hybrid-analysis.com/knowledge-base/removing-uploaded-sensitive-files)). |
| [Joe Sandbox](https://www.joesecurity.org/) | Commercial static/dynamic/hybrid | Quote-based; powerful but not PDF-native explainability |
| [VMRay](https://www.vmray.com/privacy-first-malware-sandbox/) | Agentless hypervisor analysis | Privacy-strong enterprise platform, not a PDF IR |
| [VirusTotal Private Scanning](https://docs.virustotal.com/docs/private-scanning) | Paid private static/dynamic | Standard VT submission **shares** samples. Private Scanning is a different product. |

Sandbox results depend on reader version, timing, and interaction. Dormant `/AA` paths and environment checks can remain invisible. BluePaper v1 does **not** detonate; it reconstructs reachable behavior statically. Optional detonation is a later integration.

### 2.4 Sanitization / CDR

| Product | Role | Relation to BluePaper |
| --- | --- | --- |
| [Dangerzone](https://github.com/freedomofpress/dangerzone) | Rasterize untrusted docs in network-disabled containers | Safety via destruction of active content |
| [OPSWAT MetaDefender Deep CDR](https://www.opswat.com/docs/mdcore/deep-cdr/advanced-configurations) | Commercial rebuild / policy strip | Neutralizes; does not explain intent |
| [Glasswall CDR](https://docs.glasswall.com/docs/about-glasswall-cdr) | Spec-validate and rebuild | Change reports, not malice determination |
| [Votiro](https://votiro.com/content-disarm-and-reconstruction-cdr/) | Commercial CDR | Feature-preserving claims need independent tests |

CDR is complementary. BluePaper detects and explains. A later phase may preview what a policy would remove.

### 2.5 Gaps BluePaper targets

1. Explainable static behavior graphs (what happens when, not keyword counts).
2. Reader-aware semantics without detonation (Acrobat, Foxit, browser PDF.js differences as annotations on the graph).
3. Adversarial parsing: xref/incremental/object-stream/malformed recovery as evidence.
4. Non-executing JavaScript analysis with capability mapping.
5. Local-first automation: no sample sharing, reproducible JSON/SARIF.
6. Evidence-linked, dimensioned scoring with confidence and uncertainty.
7. Public evaluation with malformed, benign-complex, and malicious PDFs (recall, FPR, parser coverage, performance).

## 3. Latest research (2023–2026)

### 3.1 Parser disagreement is a security signal

**PolyDoc.** Anantharaman, P., Lathrop, R., Shapiro, R., Locasto, M. E. *PolyDoc: Surveying PDF Files from the PolySwarm Network*, IEEE Security and Privacy Workshops (LangSec), 2023. [DOI 10.1109/SPW59333.2023.00017](https://doi.org/10.1109/SPW59333.2023.00017). 58,906 PolySwarm PDFs traced through Mutool, Poppler, and Caradoc; malicious classifications correlated with syntactic malformations; many real-world failures did not fit the existing error ontology. Caveat: engine consensus is operational ground truth, not independently confirmed behavior.

**VALARIN.** Lam, D., Li, L. W., Anderson, C. *PDF Investigation With Parser Differentials and Ontology*, IEEE TIFS 19:7774–7782, 2024. [DOI 10.1109/TIFS.2024.3445733](https://doi.org/10.1109/TIFS.2024.3445733). Preprint 2023-06-08: [10.36227/techrxiv.23290277](https://doi.org/10.36227/techrxiv.23290277). Combines error output from 15 parsers. Parsers have systematic, mutually different recovery around ambiguous syntax. Consensus and disagreement are useful safety features.

**Implication:** a parse failure is never “clean.” Preserve repairs, unreachable objects, duplicate definitions, xref disagreements, and parser differentials as first-class evidence. BluePaper runs at least two independent workers (qpdf, MuPDF) in strict and repair modes.

### 3.2 Structural IR beats shallow counts — with temporal caveats

**VAPD.** Liu, S., Ming, J., Zhou, Y., Fu, J., Peng, G. *VAPD: An Anomaly Detection Model for PDF Malware Forensics with Adversarial Robustness*, USENIX Security 2025 (13 Aug 2025). [Presentation](https://www.usenix.org/conference/usenixsecurity25/presentation/liu-side), [PDF](https://www.usenix.org/system/files/usenixsecurity25-liu-side.pdf). Benign-only variational reconstruction with object-level anomaly localization. Reported 99.54% baseline accuracy; 6.01% evasion under adaptive EvadeML; much lower training cost than robust-training baselines. Valuable part for BluePaper: **localization of suspicious objects**, not only a binary verdict.

**PDFObj IR.** Liu, S., Ming, J., Zhou, G., Liu, X., Fu, J., Peng, G. *Analyzing PDFs like Binaries: Adversarially Robust PDF Malware Analysis via Intermediate Representation and Language Model*, ACM CCS 2025 (13 Oct 2025, Taipei). [DOI 10.1145/3719027.3744829](https://doi.org/10.1145/3719027.3744829), [arXiv:2506.17162](https://arxiv.org/abs/2506.17162). Poir repair-capable parser, PDFObj IR, object-reference graphs, PDFObj2Vec, Graph Isomorphism Network. Reported 99.93% baseline accuracy, **96.62% on a temporal/extended set**, 0.07% FPR on the baseline, and 100% robustness against the paper’s strongest *evaluated* realizable attack.

The drop from 99.93% to 96.62% is the design lesson: random in-dataset scores overestimate deployment. BluePaper v1 adopts the **IR + object graph** idea and evidence localization, not a CCS-style GNN as the production classifier.

### 3.3 Datasets

| Dataset | Date | Size | Caution |
| --- | --- | --- | --- |
| [CIC-Evasive-PDFMal2022](https://www.unb.ca/cic/datasets/pdfmal-2022.html) | 2022, still used 2023–2025 | 10,025 rows (5,557 mal / 4,468 ben), 31–32 static features | “Evasive” via clustering, not independently executed attacker evasions. Contagio + VirusTotal lineage. |
| [RIT-PDFMal-2026](https://doi.org/10.1109/ACCESS.2026.3707213) | IEEE Access, 1 Jul 2026 (ahead of print 25 Jun 2026) | 24,337 (13,235 ben / 11,102 mal), 41–42 predictors | Malicious: VirusTotal 2017–2025. Benign: web crawl. Models can learn source, not malice. Dataset: [github.com/Mo-Alani/RIT-PDFMal-2026](https://github.com/Mo-Alani/RIT-PDFMal-2026). XGBoost F1 > 0.99 is an in-dataset result. |

Related explainability papers on CIC (ensembles + SHAP) report >98% on random splits and weaker external validation. Header/text features dominating SHAP often means **generation artifacts**.

**Tsetlin Machine preprint.** *Leveraging Interpretable Tsetlin Machine for PDF Malware Detection*, arXiv [2607.09290](https://doi.org/10.48550/arXiv.2607.09290) (v1 2026-07-10, v2 2026-08-04). 98.02% on RIT-PDFMal-2026. Promising interpretability; **not** temporally or adversarially validated. Treat as emerging.

### 3.4 Confirmed reader exploitation

| Advisory | Date | CVE | Notes |
| --- | --- | --- | --- |
| [APSB23-01](https://helpx.adobe.com/security/products/acrobat/apsb23-01.html) | 2023-01-10 | CVE-2023-21608 | Use-after-free / code execution. [CISA KEV 2023-10-10](https://www.cisa.gov/news-events/alerts/2023/10/10/cisa-adds-five-known-vulnerabilities-catalog). |
| [APSB23-34](https://helpx.adobe.com/security/products/acrobat/apsb23-34.html) | 2023-09-12 | CVE-2023-26369 | Out-of-bounds write; limited in-the-wild. [CISA KEV 2023-09-14](https://www.cisa.gov/news-events/alerts/2023/09/14/cisa-adds-one-known-vulnerability-catalog). |
| [APSB26-43](https://helpx.adobe.com/security/products/acrobat/apsb26-43.html) | 2026-04-11 | CVE-2026-34621 | JavaScript prototype pollution → arbitrary code execution. Adobe confirmed in-the-wild. [CISA KEV 2026-04-13](https://www.cisa.gov/news-events/alerts/2026/04/13/cisa-adds-seven-known-exploited-vulnerabilities-catalog). |

Reader exploits are less common than phishing PDFs but remain high impact. BluePaper must map privileged JS APIs and prototype-pollution patterns without executing Acrobat’s engine.

### 3.5 Phishing and user-driven execution dominate operations

- Cisco Talos, *PDFs: Portable Documents, or Perfect Deliveries for Phish?*, 2025-07-02. [blog](https://blog.talosintelligence.com/pdfs-portable-documents-or-perfect-deliveries-for-phish/). Brand impersonation, QR phishing, callback/TOAD, Adobe e-signature lures.
- Cisco Talos, *IR Trends Q2 2026*, 2026-07-28. [blog](https://blog.talosintelligence.com/ir-trends-q2-2026/). Phishing in over half of IR engagements; victim-tailored QR PDFs via compromised M365 / SharePoint.
- Netskope, *Fake CAPTCHAs, Malicious PDFs, SEO Traps*, 2025. [blog](https://www.netskope.com/blog/fake-captchas-malicious-pdfs-seo-traps-leveraged-for-user-manual-searches). ~5,000 phishing PDFs, ClickFix, Lumma.
- Trend Micro, *Fake CAPTCHA Attacks Deploy Infostealers and RATs*, 2025-05-19. [blog](https://www.trendmicro.com/en_us/research/25/e/unmasking-fake-captcha-cases.html). PDFs redirect to fake verification; payloads include Lumma, Rhadamanthys, AsyncRAT, XWorm.

**Implication:** a PDF analyzer that only hunts `/JavaScript` and `/Launch` will miss the dominant 2025–2026 operational threat. Visual analysis (render, OCR, QR, phone numbers, visible vs hidden links) is required for the `deep` profile.

### 3.6 Polyglots are active

- JPCERT/CC, *MalDoc in PDF*, 2023-08-01. [blog](https://blogs.jpcert.or.jp/en/2023/08/maldocinpdf.html). PDF/MHTML/Word polyglot; PDF-only tools missed VBA; OLEVBA caught it.
- Proofpoint, *Call It What You Want*, 2025-03-04. [blog](https://www.proofpoint.com/us/blog/threat-insight/call-it-what-you-want-threat-actor-delivers-highly-targeted-multistage-polyglot). PDF+HTA and PDF+ZIP delivering Sosano.
- Check Point, Foxit PDF Reader “Flawed Design”, 2024-05-14. [blog](https://blog.checkpoint.com/research/foxit-pdf-reader-flawed-design-hidden-dangers-lurking-in-common-tools/). Viewer-specific social engineering via default warning choices — not a memory-corruption exploit.

**Implication:** identify all plausible formats, not just leading magic bytes. Recursively classify children. Annotate viewer-specific warning/launch behavior on the graph.

### 3.7 Specification reality

- Core standard: [ISO 32000-2:2020 (PDF 2.0)](https://www.iso.org/standard/75839.html), 2020-12.
- [PDF Association associated-files note](https://pdfa.org/download-area/publications/PDF20_AN002-AF.pdf): any embedded file may contain malicious code; handling is the consumer’s policy.
- Live parser-engineering input: [pdf-issues.pdfa.org](https://pdf-issues.pdfa.org/).

`/JavaScript` or `/EmbeddedFile` alone does not determine malice. Execution path, trigger, target, viewer, and visual lure matter.

### 3.8 Claims to treat carefully

- “100% adversarial robustness” means resistance to the **evaluated** transformations, not universal robustness.
- Vendor prevalence percentages are telemetry-dependent. Do not generalize without denominator and coverage.
- Foxit/Adobe `/Launch`-related NTLM leakage (late 2024) is a design-risk test case unless a confirmed campaign appears.

## 4. Design implications → BluePaper pipeline

1. Identify all plausible formats and trailing data after `%%EOF`.
2. Run multiple independent parsers; score disagreement and recovery.
3. Canonical object IR: incremental revisions, object streams, name trees, annotations, AcroForm/XFA, actions, JS, Launch, URI/SubmitForm/GoToR, embedded files, RichMedia, filter chains.
4. Recursively extract children with depth, size, decompression-ratio, object-count, and CPU/time limits.
5. Visual analysis in a networkless worker: render, OCR, QR, phones, visible/hidden links, lure language.
6. Destinations as *declared* chains only in v1 (no live fetch): shorteners, cloud hosts, query parameters recorded as IOCs.
7. Separate risk dimensions (see README). Localize to object, revision, page, annotation, QR, URL, script, child file.
8. Evaluate with hash dedup, family/source grouping, temporal holdouts, contemporary benign corpora, calibrated abstention, and realizable problem-space attacks.

## 5. Server architecture

v1 is a REST API in the operator’s Azure subscription, not a laptop CLI and not a public SaaS.

```
Client → HTTP API (Axum, Container Apps)
            │
            ├─ put bytes ──► Blob (samples, reports)
            ├─ put row   ──► Table (status, hashes, TTL)
            └─ enqueue   ──► Storage Queue ──► analysis worker
                                                  │
                                                  ├─ isolated parser/render processes
                                                  └─ write report ──► Blob + Table
```

- `POST /v1/scans` writes the blob and table row, enqueues the scan id, returns immediately.
- `GET /v1/scans/{id}` reads Table (status).
- `GET /v1/scans/{id}/report` reads the report blob (JSON or SARIF).
- API keys on every route. Managed identity for Storage. mTLS/OIDC later.
- The HTTP process never loads native PDF parsers.
- Each scan: networkless worker, CPU/memory/descriptor/wall-clock/output-size limits.
- Single-tenant. Hash-addressed blobs. Lifecycle policy for TTL. No sample bytes in logs.

### Why a queue at all, and why not PostgreSQL

Deep analysis (render, OCR, recursive extraction) can run for minutes. The HTTP request must not wait. That is the only reason there is a job system: **accept now, analyze in a worker, poll for the report**.

PostgreSQL was a laptop/single-VM shortcut: `SKIP LOCKED` as a queue plus status rows in one process. On Azure that is extra infrastructure we do not need.

| Concern | Azure v1 |
| --- | --- |
| Work dispatch | Storage Queue (visibility timeout = lease; poison messages after N dequeues) |
| Scan metadata | Table Storage (id, status, sha256, profile, timestamps, error, ttl) |
| Bytes | Blob Storage (samples, extracted children, JSON/SARIF reports) |
| Compute | Container Apps: API + worker, workers scale on queue depth |

A queue message is not a database. `GET /v1/scans/{id}` still needs a durable row. Table Storage is that row. Service Bus and Azure SQL can wait until there is a second region, sessions, or relational query load.

## 6. Stack decision

| Choice | Why |
| --- | --- |
| **Rust** | Memory safety on hostile inputs, predictable concurrency, explicit errors |
| **Axum + Tokio** | Mature async HTTP; `bluepaper serve` / `bluepaper worker` entrypoints |
| **Azure Container Apps** | Linux containers, scale workers on queue length, no AKS tax in v1 |
| **Storage Queue** | Durable competing-consumer jobs without a SQL server |
| **Table Storage** | Scan status and metadata; cheap point lookups by scan id |
| **Blob Storage** | Hash-addressed samples and reports; lifecycle TTL |
| **Managed identity** | API and worker reach Storage without access keys in env |
| **qpdf + MuPDF as subprocesses** | Independent parse/render paths; never in-process |
| **tree-sitter-javascript** | Non-executing JS AST |
| **yara-x** | Signature layer, not the verdict |
| **petgraph** | Evidence / trigger graph |
| **Tesseract + ZXing-C++** | OCR/QR in `deep` only, sandboxed |
| **No Python in the scan path** | Research/eval workspace only |
| **Rules before ML** | Transparent scoring first; GNN/VAPD-style models are a later add-on |
| **No PostgreSQL in v1** | Queue + table + blob already cover jobs, status, and bytes |

Rejected for v1: Azure Database for PostgreSQL as a job queue; in-process Poppler/MuPDF; Python FastAPI core; sync HTTP for `deep`; Redis/NATS; AKS unless scale or networking forces it.

## 7. Evaluation requirements

Golden tests (must remain green): malformed xrefs, object streams, incremental updates, action chains, embedded files, filter bombs, polyglots, QR phishing, benign complex PDFs (forms, signed files, large object streams).

Corpus hygiene:

- Byte-level hashes and near-duplicate clustering.
- Provenance tags (VirusTotal, crawl, internal, synthetic).
- Family/campaign grouping so one kit does not dominate metrics.
- Temporal holdouts and source-separated splits (never train on VT-mal / crawl-benign and call it generalization).
- Contemporary benign documents (invoices, statements, research papers).

Report **per-dimension** precision, recall, and abstention — not only aggregate accuracy.

Fuzz every in-process decoder and IR transformation. Native workers stay networkless with hard limits. Parser crashes are findings or failures, never silent success.

## 8. Rollout

1. HTTP ingest, jobs, isolation.
2. Canonical IR and parser differential.
3. Actions, JavaScript, extraction.
4. Visual phishing.
5. Scoring and reporting.
6. Benchmark hardening and fuzzing.
