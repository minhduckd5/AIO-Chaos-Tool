# Mẫu đăng ký đề tài LVTN — bản nháp tiếng Việt (ChaosGen)

> Bản sao nội dung của [`thesis-registration-form.md`](thesis-registration-form.md) (tiếng Anh — dùng khi nộp form IU).  
> File này để đối chiếu / thảo luận với thầy bằng tiếng Việt. Trường `[...]` điền tay.

---

## Sinh viên / giảng viên hướng dẫn

| Trường | Nội dung |
|--------|----------|
| **Họ tên sinh viên** | `[Họ và tên]` |
| **MSSV** | `[MSSV]` |
| **Email** | `[email@student.hcmiu.edu.vn]` |
| **SĐT** | `[Số điện thoại]` |
| **Ngành** | `[Ngành đúng trên hệ thống Khoa, vd. Khoa học máy tính / Công nghệ thông tin]` |
| **GVHD 1** | Trần Thanh Tùng |
| **GVHD 2** | *(tùy chọn — để trống nếu không có)* |

---

## Tên đề tài

**ChaosGen: Chaos Engineering dựa trên AI cho hệ thống đa kiến trúc**

*(Bản tiếng Anh nộp form: ChaosGen: AI-Driven Chaos Engineering for Multi-Architecture Systems)*

---

## Mục tiêu và đối tượng của luận văn

*(mô tả ngắn sản phẩm đầu ra)*

Xây dựng **ChaosGen** — **nền tảng control plane** Chaos Engineering dựa trên AI (CLI + GUI), bao phủ vòng đời: thu thập telemetry → phát hiện bất thường → lọc sự cố thật → sinh và xếp hạng kịch bản chaos **theo kiến trúc** → phê duyệt human-in-the-loop (HITL) → thực thi an toàn trên Kubernetes/Docker/bare metal → đánh giá KPI.

**Mục tiêu cụ thể:**

1. **Nghiên cứu:** Mô hình hóa khung *Unknown → Describe → Known* kết hợp gatekeeper (frequency × severity), chuyển từ xử lý sự cố phản ứng sang hướng *predictive maintenance* với rủi ro dư thừa có kiểm soát. Khung áp dụng cho **hệ thống đa kiến trúc** (microservices, monolith, event-driven, serverless/client–server, …).
2. **Hiện thực:** Pipeline Python (`chaosgen`) gồm ingestion Prometheus/Loki, ML anomaly (IsolationForest/KMeans), advisor LLM/catalog theo architecture profile, orchestrator + HITL, lớp an toàn (blast radius, dead man’s switch), UCAL bọc ≥6 công cụ chaos, và **discovery/profile kiến trúc** để chọn catalog & prompt phù hợp.
3. **Đánh giá theo pha:** Ổn định và đánh giá sâu trên **microservices** (lab/cluster K8s) — ≥1 case study cụ thể *make sense* + tổng hợp KPI nhiều case; sau đó **mở rộng** ≥1 kiến trúc bổ sung (vd. monolith hoặc event-driven/serverless) để chứng minh tính đa kiến trúc.
4. **Sản phẩm bàn giao:** Prototype đa-profile, tài liệu kiến trúc/pipeline, báo cáo luận văn, demo E2E (analyze → generate → approve → inject → evaluate) trên ≥2 kiểu kiến trúc (microservices là đường chính; kiến trúc thứ hai theo lab/dataset sẵn có).

---

## Yêu cầu chính

*(liệt kê các yêu cầu lớn của đề tài)*

1. **Phạm vi đa kiến trúc:** Thiết kế hệ thống cho **nhiều kiểu kiến trúc** (microservices, monolith, event-driven, serverless, …). **Pha 1:** ổn định vòng Unknown→Known + inject/eval trên microservices (K8s/Docker). **Pha 2:** bật/hoàn thiện profile, catalog, prompt cho các kiểu khác — không khóa đề tài chỉ ở microservices.
2. **Discovery / profile:** Chọn profile **form-first** (`hints.architecture` + khối `connect` trong settings/GUI; auto-discovery heuristic hoãn lại). Người vận hành khai báo ý định; observability được probe khi cấu hình. Microservices là đường đánh giá chính; **modular monolith** là case inject live thứ hai (Docker Compose / Pumba). Các profile còn lại (event-driven, monolith, client–server, serverless) dùng catalog + dry-run cho phạm vi luận văn.
3. **Dữ liệu & ML:** Ingest metrics/logs; train/đánh giá anomaly trên **từng phần** dataset công khai (RCAEval, Nezha, Eadro, …) + dữ liệu inject lab — không train toàn bộ dump TB; ưu tiên corpus khớp từng kiến trúc khi mở rộng.
4. **Advisor pipeline:** Gatekeeper REAL/CHRONIC; mô tả có cấu trúc cho sự cố unknown; sinh/xếp hạng scenario theo kiến trúc; promote vào catalog known sau HITL.
5. **Thực thi chaos an toàn:** State machine + HITL bắt buộc; giới hạn blast radius; blocked namespaces (vd. `kube-system`); hỗ trợ dry-run; inject thật chỉ trên staging/lab (không production).
6. **Tích hợp công cụ:** Abstraction UCAL cho Chaos Toolkit, Pumba, Toxiproxy, Kube-Monkey, … (và/hoặc Chaos Mesh/Litmus trên cluster trường khi có).
7. **Đánh giá:** ≥1 case study microservices chi tiết *make sense* + tổng hợp KPI nhiều case (latency, error rate, recovery/rollback); thêm ≥1 case/demo kiến trúc không-microservices (theo lab/dataset khả dụng).
8. **Giao diện & vận hành:** CLI tự động hóa + GUI PySide6 cho review HITL; secrets qua `.env` (không hard-code); chọn/override architecture profile từ CLI/GUI.
9. **Bàn giao học thuật:** SRS/architecture đa-profile, pipeline framework khớp khung thầy hướng dẫn, báo cáo LVTN, demo trên cụm K8s lab (master + nodes) nếu được cấp access.

---

## Kế hoạch gợi ý *(nếu form/plan tuần yêu cầu)*

| Giai đoạn | Nội dung |
|-----------|----------|
| Tuần 1–3 | Chốt đề tài đa kiến trúc, SRS, schemas, architecture profiles |
| Tuần 4–6 | Discovery/bootstrap observability; ingest lab (microservices trước) |
| Tuần 7–9 | Advisor + ML + catalog Unknown→Known (profile microservices) |
| Tuần 10–11 | Orchestrator, HITL, UCAL, safety |
| Tuần 12–13 | GUI, KPI/A-B; hoàn thiện case study microservices |
| Tuần 14–16 | Profile đa kiến trúc form-first (P0: MS + modular monolith live; P1: ma trận dry-run); luận văn + bảo vệ |

> **Trạng thái repo (9/2026):** Vòng flagship microservices hoàn tất. **Pha 2:** preset form-first, validate connect, lab modular monolith, inject Pumba live, verdict Prometheus (FAIL thật + PASS sau HITL). Ma trận: `python scripts/demo_multi_arch_matrix.py`. ADR: `docs/adr-multi-arch-form-profiles.md`.

---

## Chữ ký (trên form Word)

| Bên | Điền |
|-----|------|
| GVHD 1 | Trần Thanh Tùng — Ngày ký: `[ngày]` |
| Sinh viên 1 | `[Họ tên]` — Ngày ký: `[ngày]` |

---

## Trước khi nộp

- Điền đủ họ tên / MSSV / email / SĐT.
- Xác nhận cách ghi **Ngành** với Khoa.
- Form nộp chính thức: dùng bản tiếng Anh [`thesis-registration-form.md`](thesis-registration-form.md).
