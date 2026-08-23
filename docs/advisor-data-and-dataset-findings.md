# Papers that share datasets — train vs not

Campus account is enough to open PDFs and download files. Automation only **finds titles**. Do not scrape IEEE / ACM / ScienceDirect.

Re-run: `python scratch/find_shared_datasets.py`

Links HTTP-checked 2026-08-15 (real records; ACM/IEEE may be gated). Tip: Zenodo record titles often match the paper name — search that title on arXiv / ACM / IEEE to fill the Paper column.

---

## Train the model (IsolationForest / KMeans)

The earlier “only two” were **corpus names**. RCAEval alone is already **three systems** (Online Boutique, Sock Shop, Train Ticket) and 735 labeled inject cases.

Native ChaosGen features still come from **your lab**. Everything else needs a **column map** (metrics/logs/traces → windows) and labeled healthy vs fault. Never mix load-only traffic into the failure class.

**Local disk (extracted, 2026-08-18 scan of** `data/public-datasets/`**): ~1.4 TB total.** Two folders dominate: LO2v2 (725 GB) + TrainTicketTrace (525 GB). Do not point `train-model` at the root — use the labeled slices below.


| Item                                     | Architecture                                                       | Size (local)                                                                                                              | Paper                                                                                                                                                                                                                                                            | Dataset                                                                                                                                                                  | How to train                                                                                                                                                                      |
| ---------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Your lab — healthy vs serious inject** | whatever you deploy (boutique / k8s / …)                           | —                                                                                                                         | —                                                                                                                                                                                                                                                                | (your stack)                                                                                                                                                             | **Required.** Baseline vs network-cut / node-kill / connection wipe.                                                                                                              |
| RCAEval (RE1–RE3)                        | **Microservices ×3:** Boutique, Sock Shop, Train Ticket            | **74.5 GB**                                                                                                               | [ACM 10.1145/3701716.3715290](https://doi.org/10.1145/3701716.3715290)                                                                                                                                                                                           | [Figshare](https://doi.org/10.6084/m9.figshare.31048672) · [Zenodo 14590730](https://doi.org/10.5281/zenodo.14590730) · [GitHub](https://github.com/phamquiluan/RCAEval) | Best published match. CPU/mem/disk/delay/loss/socket + code faults; `inject_time.txt` labels.                                                                                     |
| Nezha `rca_data`                         | Microservices: Boutique + Train Ticket                             | **3.2 GB**                                                                                                                | [ACM 10.1145/3611643.3616249](https://doi.org/10.1145/3611643.3616249) (FSE 2023) · [ResearchGate](https://www.researchgate.net/publication/376091489_Nezha_Interpretable_Fine-Grained_Root_Causes_Analysis_for_Microservices_on_Multi-modal_Observability_Data) | [GitHub](https://github.com/IntelligentDDS/Nezha) · [Zenodo 8276375](https://doi.org/10.5281/zenodo.8276375)                                                             | Fault-free vs fault-suffering metrics/logs/traces + fault_list labels.                                                                                                            |
| Eadro                                    | Microservices: Train Ticket + **DeathStarBench SocialNetwork**     | **1.3 GB** (local folder: `Traces, Metrics, and Logs for Anomaly Detection and Root Cause Localization in Microservices`) | [arXiv 2302.05092](https://arxiv.org/abs/2302.05092) (ICSE 2023) · [ResearchGate](https://www.researchgate.net/publication/368462670_Eadro_An_End-to-End_Troubleshooting_Framework_for_Microservices_on_Multi-source_Data)                                       | [Zenodo 7615393](https://doi.org/10.5281/zenodo.7615393) · [7615394](https://zenodo.org/records/7615394)                                                                 | Injected CPU exhaustion, delay, packet loss; traces+logs+KPIs.                                                                                                                    |
| TrainTicketTrace                         | Microservices: Train Ticket                                        | **525.0 GB**                                                                                                              | [IEEE Xplore 11500150](https://ieeexplore.ieee.org/document/11500150)                                                                                                                                                                                            | [Zenodo 17811971](https://doi.org/10.5281/zenodo.17811971)                                                                                                               | Clean vs **nine seeded-fault versions**; traces+metrics+logs. Sample one clean + one fault version — not the full tree.                                                           |
| LO2v2                                    | Microservices: **LightOAuth2** (OAuth / identity, not a demo shop) | **724.5 GB** (full extract) · train `LO2v2-metrics.zip` **~1.2 GB**                                                       | [arXiv 2504.12067](https://arxiv.org/abs/2504.12067)                                                                                                                                                                                                             | [Zenodo 18937117](https://doi.org/10.5281/zenodo.18937117)                                                                                                               | Prometheus/cAdvisor + logs; tests tagged **correct vs error**. Start with metrics zip, not 2.4M-file raw dump.                                                                    |
| Open5GS                                  | **Cloud-native 5G core** on Kubernetes                             | **2.9 GB**                                                                                                                | [TU Delft thesis PDF](https://repository.tudelft.nl/file/File_d6f4af7f-8ed6-498d-bb7d-55a8f33dfced)                                                                                                                                                              | [4TU](https://doi.org/10.4121/e62eef6e-4958-4713-9cef-ed5b317e40a3)                                                                                                      | Prom+Loki+Jaeger; baseline/fault/recovery. Telco, not boutique.                                                                                                                   |
| Cloud-OpsBench                           | **Kubernetes** control-plane + Boutique / Train Ticket             | **3.9 GB**                                                                                                                | [arXiv 2603.00468](https://arxiv.org/abs/2603.00468)                                                                                                                                                                                                             | [GitHub](https://github.com/LLM4Ops/Cloud-OpsBench)                                                                                                                      | 754 cases, `metrics.csv` + logs + k8s snapshots, 57 fault types (admission/scheduling/runtime/…). Map metrics; ignore agent chat traces for IF.                                   |
| PetShop                                  | Distributed **multi-service** shop                                 | **24.8 MB**                                                                                                               | [arXiv 2311.04806](https://doi.org/10.48550/arxiv.2311.04806)                                                                                                                                                                                                    | [GitHub](https://github.com/amazon-science/petshop-root-cause-analysis)                                                                                                  | 68 injected perf issues; latency/availability. Adapter required (not Prom/Loki).                                                                                                  |
| Loghub HDFS_v1 + HDFS_v3                 | **Distributed FS / client–server** (HDFS)                          | **24.3 GB**                                                                                                               | [arXiv 2008.06448](https://arxiv.org/abs/2008.06448) (ISSRE 2023)                                                                                                                                                                                                | [GitHub](https://github.com/logpai/loghub) · [Zenodo 8196385](https://doi.org/10.5281/zenodo.8196385)                                                                    | v1: labeled normal/anomaly traces. v3 TraceBench: **17 injected faults** vs normal. Log/trace features, not PromQL.                                                               |
| **GAIA**                                 | MicroSS simulation: **10 microservices** + MySQL/Redis             | **10.0 GB**                                                                                                               | [IEEE TSC 2023](https://doi.org/10.1109/tsc.2023.3290018)                                                                                                                                                                                                        | [GitHub](https://github.com/CloudWise-OpenSource/GAIA-DataSet)                                                                                                           | Metrics + logs + traces; five injected faults (stuck, crash, login, file missing, access denied) with injection records. Map to windows; do not mix with lab PromQL blindly.      |
| **AIOps Challenge 2020**                 | Real production **MSS** (Zhejiang Mobile)                          | **22.3 GB**                                                                                                               | [IEEE 9293310](https://ieeexplore.ieee.org/document/9293310) · survey context [ACM 10.1145/3715005](https://doi.org/10.1145/3715005)                                                                                                                             | [GitHub](https://github.com/NetManAIOps/AIOps-Challenge-2020-Data) (community dump)                                                                                      | Labeled anomaly / RCA cases (metrics + call chains). Best published *production* set. Repo is often an index; pull the actual dump from the README links. Schema ≠ boutique Prom. |
| **MultiDimension-Localization**          | Production **21 microservices**                                    | **5.7 GB**                                                                                                                | [Sensors 2025](https://doi.org/10.3390/s25113396)                                                                                                                                                                                                                | [GitHub](https://github.com/NetManAIOps/MultiDimension-Localization)                                                                                                     | Multivariate metrics only (19 per service); 58 labeled cases (contention, latency, app errors, dependency). Easier adapter than full traces.                                      |


**Held — do not train yet:** TrainTicket-DéjàVu — paper [ACM 10.1145/3540250.3549092](https://doi.org/10.1145/3540250.3549092) / [arXiv 2207.09021](https://arxiv.org/abs/2207.09021) · dataset [Zenodo 6955909](https://zenodo.org/records/6955909) — **not downloaded locally** — confirm healthy-vs-fault columns before training. SockShop knowledge-graph benchmark — paper [IEEE TNSM](https://doi.org/10.1109/TNSM.2026.3652304) / [IEEE Xplore](https://ieeexplore.ieee.org/document/11341916/) / [ResearchGate](https://www.researchgate.net/publication/399712228_Towards_Context-Aware_Anomaly_Detection_for_AIOps_in_Microservices_Using_Dynamic_Knowledge_Graphs); open-source framework in the paper (no stable dump URL in the survey row); RCAEval already covers Sock Shop inject.

**Search is enough to stop.** Zhang et al.’s TOSEM survey ([ACM 10.1145/3715005](https://doi.org/10.1145/3715005)) already catalogs most of the same public dumps (GAIA, AIOps Challenge, TrainTicket variants, Nezha, Eadro, …). Overlap with this note is expected — it is confirmation, not a miss. Post-survey additions here (RCAEval, LO2, Open5GS, Cloud-OpsBench, PetShop, MultiDimension) still leave the same **architectural** holes: no public serverless/FaaS *injected-fault* dump, no clean monolith Prom inject dump. More paper search will not invent those. Ask the advisor whether this corpus + **lab inject on the thesis stack** is sufficient; do not keep hunting unless they name a missing architecture.

Local clones (gitignored, do not email): `data/public-datasets/`. Submit **this note**, not the raw dumps.

**Still scarce (do not invent labels):** public **serverless FaaS** dumps with *injected faults* (OpenWhisk/Lambda) are basically missing. Azure Functions 2019 / KSWD are **workload shapes**, not failures — do not train as the anomaly class. Cover “cloud-native / k8s” via Cloud-OpsBench + Open5GS instead.

**Monolith:** no clean public Prom inject corpus showed up. Closest: HDFS (distributed FS) and 5G core (many tightly coupled NFs). Your own lab can still run a modular monolith and inject.

Do **not** treat any published set as a drop-in replacement for lab inject.

---



## Thesis support — do **not** train

Cite, compare, or offline RCA eval. Wrong object or wrong schema for ChaosGen fit.


| Item                       | Paper                                                                                                       | Dataset                                                              | Why not train                         |
| -------------------------- | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------- |
| RCAEval paper              | [ACM 10.1145/3701716.3715290](https://doi.org/10.1145/3701716.3715290)                                      | (see TRAIN row)                                                      | PDF citation only                     |
| Online Boutique telemetry  | [Zenodo record](https://doi.org/10.5281/zenodo.20685204) (dataset deposit; no separate journal paper found) | [Zenodo 20685204](https://doi.org/10.5281/zenodo.20685204)           | Load replay, **not** disaster         |
| OpenTelemetry AIOps        | [Zenodo companion](https://doi.org/10.5281/zenodo.19462084) (IEEE Access submission titled in the deposit)  | [Zenodo 19462084](https://doi.org/10.5281/zenodo.19462084)           | Anomaly benchmark, not chaos inject   |
| TORAI RCA                  | [arXiv 2604.13522](https://arxiv.org/abs/2604.13522)                                                        | [Figshare 31925976](https://doi.org/10.6084/m9.figshare.31925976.v1) | Call-graph RCA, not collector windows |
| LEMMA-RCA (IT slices only) | [arXiv 2406.05375](https://doi.org/10.48550/arxiv.2406.05375)                                               | [lemma-rca.github.io](https://lemma-rca.github.io/)                  | RCA labels; ignore SWaT/WADI water OT |


---



## Not useful — do **not** train


| Item                          | Paper                                                                                                                              | Dataset                                                            | What to do                            |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------- |
| CATS                          | [Zenodo deposit](https://doi.org/10.5281/zenodo.8338435) (CATS; cites [PVLDB AD survey](https://doi.org/10.14778/3538598.3538602)) | [Zenodo 8338435](https://doi.org/10.5281/zenodo.8338435)           | Ignore — generic synthetic series     |
| GWDG GPU telemetry            | [arXiv 2603.28781](https://arxiv.org/abs/2603.28781)                                                                               | [Zenodo 19052366](https://doi.org/10.5281/zenodo.19052366)         | Ignore — wrong layer                  |
| VECROsim                      | [IEEE ISSRE 2022](https://doi.org/10.1109/issre55969.2022.00037)                                                                   | [Figshare 30627446](https://doi.org/10.6084/m9.figshare.30627446)  | Ignore unless README proves otherwise |
| ARISE                         | [Zenodo deposit](https://doi.org/10.5281/zenodo.21096887) (paper under review per deposit)                                         | [Zenodo 21096887](https://doi.org/10.5281/zenodo.21096887)         | Ignore for ML — incident RAG          |
| Cloud Uptime Archive          | [IEEE TPDS](https://doi.org/10.1109/tpds.2026.3673519)                                                                             | [GitHub](https://github.com/atlarge-research/cloud-uptime-archive) | Cite only                             |
| Failure Trace Archive         | [CCGrid 2010](https://doi.org/10.1109/ccgrid.2010.71)                                                                              | —                                                                  | Cite only                             |
| CFDR                          | [USENIX CFDR](https://www.usenix.org/cfdr)                                                                                         | [usenix.org/cfdr-data](https://www.usenix.org/cfdr-data)           | Cite only                             |
| Alibaba cluster               | [arXiv 1808.02919](https://arxiv.org/abs/1808.02919)                                                                               | [GitHub](https://github.com/alibaba/clusterdata)                   | Cite only                             |
| Google Borg traces            | [Borg EuroSys 2015](https://research.google/pubs/pub43438/)                                                                        | [GitHub](https://github.com/google/cluster-data)                   | Cite only                             |
| AIOps KPI benchmarks          | [arXiv 2208.03938](https://doi.org/10.48550/arxiv.2208.03938)                                                                      | —                                                                  | Cite only                             |
| Distributed-trace SMS package | [Zenodo replication package](https://doi.org/10.5281/zenodo.21772871) (SMS title in deposit; no separate DOI found yet)            | [Zenodo 21772871](https://doi.org/10.5281/zenodo.21772871)         | Reading list only                     |


---

## Canonical feature mapping (v1 shipped)

Raw per-export column unions (`merged_public.joblib`, 4624 cols) do not match sparse lab Prom (~288 cols) → Advisor discarded the model and refit on the live window only.

**Fix (v1):** signal-type pooling via [`examples/canonical_features.yaml`](../examples/canonical_features.yaml) and [`chaosgen/ml/canonical_features.py`](../chaosgen/ml/canonical_features.py). Every export maps to **35 fixed columns** (`canonical__cpu__mean`, …) before merge/train/detect.

| Artifact | Shape | Use |
| -------- | ----- | --- |
| `models/merged_public.joblib` | 10,135 × 4624 | Legacy raw-column merge |
| `models/merged_canonical.joblib` | 10,513 × 35 | **Default for Advisor** (`examples/registry-vm-settings.yaml`) |

Enable in settings:

```yaml
features:
  canonical_enabled: true
  canonical_rules_path: examples/canonical_features.yaml
anomaly:
  default_model_path: models/merged_canonical.joblib
```

Retrain (12 validated slices):

```powershell
python -u scripts/auto_train.py -v --config examples/registry-vm-settings.yaml `
  --output models/merged_canonical.joblib `
  --export-path data/public-datasets/RCAEval-v2~/data/re3tt_ts-route-service_f2_1 `
  # ... (see models/per/manifest.json for all 12 paths)
```

**v1.5 (next):** lab-only service columns after Prom/Loki label taxonomy + `chaosgen lab-alias-bootstrap`. **v2:** cross-dataset service aliases (post-thesis).

