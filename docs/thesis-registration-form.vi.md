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

**An AI-assistant for Chaos Engineering**

*(Trợ lý AI cho Chaos Engineering — thầy đã chốt bản tiếng Anh này; bỏ “-driven” vì khó hiểu.)*

---

## Mục tiêu và đối tượng của luận văn

*(mô tả ngắn sản phẩm đầu ra)*

Xây dựng **ChaosGen** — trợ lý AI hỗ trợ vận hành Chaos Engineering an toàn xuyên suốt vòng đời: thu thập telemetry, phát hiện bất thường, lọc sự cố thật, gợi ý kịch bản chaos theo kiến trúc, phê duyệt HITL, thực thi trên Kubernetes/Docker/bare metal, và đánh giá KPI.

**Mục tiêu cụ thể:**

Xây dựng vòng tư vấn *Unknown → Describe → Known* cho hệ thống đa kiến trúc; hiện thực pipeline Python (`chaosgen`) gồm ML, advisor LLM/catalog, HITL và inject đa công cụ (UCAL); đánh giá sâu trên microservices rồi mở rộng ≥1 kiến trúc khác; bàn giao prototype, tài liệu, báo cáo luận văn và demo E2E (analyze → generate → approve → inject → evaluate).

---

## Yêu cầu chính

*(liệt kê các yêu cầu lớn của đề tài)*

Hỗ trợ nhiều architecture profile (microservices là đường chính; modular monolith là case inject live thứ hai; các kiểu còn lại qua catalog/dry-run); chọn profile form-first; ingest metrics/logs và ML trên dataset công khai/lab đã chọn; gatekeeper REAL/CHRONIC và HITL trước khi inject; an toàn blast-radius + dry-run (chỉ lab/staging); UCAL bọc các công cụ chaos phổ biến; CLI + GUI; bàn giao học thuật (tài liệu, luận văn, demo lab) với KPI đo được trên ≥2 kiểu kiến trúc.

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
