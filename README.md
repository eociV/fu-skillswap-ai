# fu-skillswap-ai

Service AI của SkillSwap — FastAPI + Postgres/pgvector, gọi model qua **FPT AI Factory**.
Chạy cạnh BE (cùng docker network), **tự quản database và bucket riêng**, không đụng tới hạ tầng của BE.

## Trạng thái

| Phần | Trạng thái |
|---|---|
| Nền tảng: config, DB, migration, JWT, sổ cái, ngân sách, rate limit | ✅ xong, 65 test xanh |
| Kho tri thức RAG: upload / list / xóa / index lại / tìm kiếm | ✅ xong |
| Kiểm duyệt nội dung forum (`/v1/moderate`) | ✅ xong |
| Bot trả lời forum (RAG + gợi ý mentor + đo phễu) | ✅ xong |
| Chatbot `/v1/chat` (SSE + non-streaming) | ✅ xong |
| Re-rank gợi ý mentor | ⬜ chưa làm |
| Bộ eval so sánh chất lượng model | ⬜ chưa làm |

## Chạy local

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # điền FPT_API_KEY và JWT_SECRET_KEY (lấy từ BE)
docker compose up -d ai-postgres
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Swagger ở `http://localhost:8000/docs` (tự tắt khi `ENVIRONMENT=production`).
Chạy test: `.venv/bin/python -m pytest tests -q`

## API

| Method | Path | Quyền | Việc |
|---|---|---|---|
| `POST` | `/v1/documents` | admin | Nạp tài liệu (PDF/DOCX/MD/TXT, ≤20MB) vào kho tri thức |
| `POST` | `/v1/documents/text` | admin | Nạp bằng văn bản dán thẳng — tiện thử nhanh |
| `GET` | `/v1/documents` | admin | Danh sách, lọc theo `status` / `school_code` / `topic` |
| `GET` | `/v1/documents/{id}` | admin | Chi tiết + trạng thái index |
| `GET` | `/v1/documents/{id}/download` | admin | Link tải file gốc (presigned) |
| `POST` | `/v1/documents/{id}/reindex` | admin | Index lại (khi đổi model embedding / cách cắt đoạn) |
| `DELETE` | `/v1/documents/{id}` | admin | Xóa tài liệu + toàn bộ đoạn |
| `GET` | `/v1/knowledge/search?q=` | user | **Thử truy xuất RAG** — xem đoạn nào được lấy, điểm bao nhiêu |
| `POST` | `/v1/chat` | user | Chatbot. Mặc định SSE; `"stream": false` để nhận JSON một lần |
| `POST` | `/v1/feedback` | user | Nút 👍/👎 |
| `POST` | `/v1/moderate` | BE (token nội bộ) | Kiểm duyệt bài/bình luận |
| `POST` | `/v1/forum/events` | BE (token nội bộ) | Xử lý một bài forum mới (đường chính khi có event) |
| `POST` | `/v1/forum/scan` | token nội bộ | Quét thủ công (MVP khi BE chưa có event / để test tay) |
| `GET` | `/v1/ops/budget` | admin | Chi tiêu hôm nay theo tính năng |

Upload trả về ngay với `status: "pending"`; việc cắt đoạn và nhúng vector chạy nền.
Gọi lại `GET /v1/documents/{id}` để xem `indexed` / `failed` kèm `error`.

Thử nhanh sau khi có API key:

```bash
curl -X POST http://localhost:8000/v1/documents/text \
  -H "Authorization: Bearer $ADMIN_JWT" -H "Content-Type: application/json" \
  -d '{"title":"Quy chế thi FPTU","content":"...ít nhất 50 ký tự...","school_code":"FPTU"}'

curl -G http://localhost:8000/v1/knowledge/search \
  -H "Authorization: Bearer $JWT" --data-urlencode "q=thi lại tính điểm thế nào"
```

## Bot forum hoạt động thế nào

Đây là phễu chuyển đổi của bản pitch, không phải trợ lý lịch sự:

1. **Lọc rẻ trước** — bỏ qua bài đã có bình luận (để người thật trả lời trước), bài chưa
   quá `FORUM_GRACE_MINUTES`, bài không ACTIVE, bài của chính bot. Chưa tốn lượt gọi model nào.
2. **Phân loại một lượt** (`gpt-oss-20b`) trả về đồng thời: có phải câu hỏi không,
   **có cần mentor kèm 1-1 không**, từ khóa tìm mentor, chủ đề. Gộp vào một lượt vì
   hạn mức 50 request/phút quá chật để tách đôi.
3. **Truy xuất RAG** từ kho tri thức đã kiểm duyệt.
4. **Tìm mentor** qua API BE khi câu hỏi thật sự cần người kèm.
5. **Trả lời sơ bộ** (`DeepSeek-V4-Flash`) — cố ý chừa khoảng trống cho mentor, tối đa ~150 từ,
   dẫn nguồn nếu có tài liệu, kết bằng dòng nhận diện là câu trả lời tự động.
6. **Ghi nhận mentor đã gợi ý** vào bảng `mentor_suggestions` — đây là số liệu để nói
   "X% lượt đặt lịch đến từ diễn đàn do AI dẫn dắt". Ghi tại chỗ nên không mất dữ liệu
   trong lúc chờ BE thêm `MentorFunnelSource.AI_*`; bật `FUNNEL_EVENTS_ENABLED` để đẩy sang BE.

Chống trả lời trùng bằng bảng `forum_bot_posts` — mỗi bài chỉ xử lý đúng một lần, kể cả
khi bị bỏ qua (lưu luôn lý do). Trần `FORUM_MAX_REPLIES_PER_HOUR` cho toàn hệ thống.
Kill-switch: `FORUM_BOT_ENABLED=false`.

## Chatbot hoạt động thế nào

Trước khi trả lời, một lượt gọi JSON rẻ tiền quyết định cần lấy dữ liệu gì (tìm mentor?
xem lịch của người dùng? tra kho tri thức?), rồi mới sinh câu trả lời với dữ liệu đã có.

**Cố ý không dùng `tools` / function calling** — khả năng hỗ trợ của model trên FPT chưa
xác minh được, mà cách này chạy với mọi model. `LLMClient.chat` đã nhận tham số `tools`
sẵn, đổi sang tool calling sau khi test là sửa một hàm, không đụng router hay FE.

Gọi BE bằng **JWT của chính người dùng**, nên chatbot chỉ thấy đúng dữ liệu họ được phép thấy.

## Hợp đồng với BE (gửi TamVQ)

**Kiểm duyệt — bắt buộc fail-open.** BE gọi khi tạo bài/bình luận:

```
POST /v1/moderate
X-Internal-Token: <BOT_ACCESS_TOKEN>
{ "target_type": "post", "target_id": "...", "author_user_id": "...",
  "title": "...", "content": "..." }

-> { "decision": "allow|review|block", "max_severity": 0-3,
     "categories": {"sexual":0,"toxic":0,"hate":0,"political":0,"spam":0},
     "degraded": false }
```

Quy ước bắt buộc: **timeout 2 giây, và mọi trường hợp lỗi — timeout, 5xx, hay
`degraded: true` — đều phải CHO ĐĂNG**. FPT giới hạn 50 request/phút, một buổi
thảo luận sôi nổi có thể sinh hơn 100 bài và bình luận mỗi phút; nếu chặn cứng
thì sinh viên không đăng bài được. `block` chặn, `review` cho đăng nhưng đẩy vào
hàng đợi admin.

**Bot forum — BE bắn event:**

```
POST /v1/forum/events
X-Internal-Token: <BOT_ACCESS_TOKEN>
{ "postId": "...", "post": { ...ForumPostResponse... } }   // gửi kèm `post` thì bot khỏi gọi ngược lại BE
```

**Việc còn lại phía BE:** cấp cách verify JWT (chia `JWT_SECRET_KEY` nếu giữ
HS256, hoặc đổi RS256 và chia public key — xem phần Bảo mật), thêm route cho
service, và về sau: role `AI_BOT` + tài khoản bot, event `ForumPostCreated` qua
outbox, ba giá trị mới cho `MentorFunnelSource` (`AI_FORUM_ANSWER`,
`AI_CHATBOT`, `AI_RECOMMENDATION`) để đo phễu chuyển đổi.

## Những chỗ cố ý phòng thủ

**Vỏ response của FPT.** Tài liệu FPT hiển thị `{"code":200,"data":{...}}` nhưng
gateway có thể trả thẳng shape OpenAI. `LLMClient._unwrap` nhận cả hai nên không
phải chờ xác minh mới chạy được (có test cho cả hai dạng, và cho `/embeddings`
vốn có `data` là list — không được bóc nhầm).

**`response_format: json_schema`.** Không có trong tài liệu FPT. Client thử dùng
trước; model nào từ chối thì tự nhớ và chuyển sang ép JSON bằng prompt rồi parse.

**Hạn mức.** 50 request/phút và 100.000 token/phút, tính riêng theo từng model —
nên tách tác vụ ra các model khác nhau là tự nhiên nhân được hạn mức. Bộ đếm nằm
trong tiến trình; **chạy nhiều worker thì phải chuyển sang Redis**, nếu không mỗi
worker tưởng mình còn nguyên hạn mức.

**Ngân sách.** Trần theo ngày (`DAILY_BUDGET_VND`, mặc định 25.000đ) + sổ cái
từng lượt gọi. Đây là thứ bảo vệ 5 triệu ngân sách: rate limit chỉ giới hạn tốc
độ, một vòng lặp lỗi chạy hết 50 req/phút vẫn có thể đốt sạch trong vài ngày.

**Nhúng theo lô.** 64 đoạn mỗi request. Gọi từng đoạn một sẽ chạm trần chỉ sau
vài chục đoạn.

## Bảo mật

`JWT_SECRET_KEY` hiện là HS256 dùng chung với BE — nghĩa là service này **ký được
token mới**, không chỉ đọc. Mà đây lại là nơi xử lý dữ liệu người dùng không đáng
tin. Trước khi lên production thật nên đổi BE sang **RS256** rồi đặt
`JWT_ALGORITHM=RS256` + `JWT_PUBLIC_KEY` — verify được, không ký được.

Audience và issuer được kiểm **tường minh** trong `app/security.py`: `python-jose`
bỏ qua hai claim này khi token thiếu hẳn chúng, kể cả lúc đã bật `verify_aud`.

## Chưa kiểm chứng được

Chưa chạy migration và API trên Postgres thật (máy dev không có Docker). CI đã có
job `migrations` chạy `alembic upgrade head` rồi `downgrade base` trên
`pgvector/pgvector:pg17` — merge được nghĩa là migration chạy thật.

`EMBEDDING_DIM` đang đặt 1024 theo BGE-M3 (gốc của `Vietnamese_Embedding`).
Xác minh bằng lệnh dưới; nếu khác thì sửa `.env` và tạo migration mới.

```bash
curl -s -X POST https://mkp-api.fptcloud.com/v1/embeddings \
  -H "Authorization: Bearer $FPT_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"Vietnamese_Embedding","input":["sinh vien FPT hoc PRJ301"]}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); d=d.get('data',d); print('dim =', len((d[0] if isinstance(d,list) else d['data'][0])['embedding']))"
```

Endpoint `/rerank` cũng chưa xác minh — nếu FPT không có, `LLMClient.rerank` trả
`None` và hệ thống tự giữ thứ tự theo vector (giảm chất lượng, không gãy).

## Việc cần làm tiếp (bàn giao)

### Bước 1 — verify trên máy có Docker (làm trước mọi thứ khác)

```bash
docker compose up -d ai-postgres
cp .env.example .env      # điền FPT_API_KEY, JWT_SECRET_KEY, BOT_ACCESS_TOKEN
.venv/bin/alembic upgrade head    # chưa từng chạy trên Postgres thật
.venv/bin/python -m pytest tests -q
.venv/bin/uvicorn app.main:app --reload
```

Rồi chốt 4 ẩn số bằng API key thật (lệnh curl ở mục "Chưa kiểm chứng được"):

| Ẩn số | Nếu sai thì sửa ở đâu |
|---|---|
| Response có bọc `{"code":..,"data":..}` không | Đã xử lý cả hai — chỉ cần xác nhận |
| `response_format: json_schema` có chạy không | Đã có fallback ép JSON bằng prompt — chỉ cần xác nhận |
| Số chiều vector `Vietnamese_Embedding` | `EMBEDDING_DIM` trong `.env` + tạo migration mới nếu khác 1024 |
| Có endpoint `/rerank` không | Không có thì tự giữ thứ tự vector, chất lượng giảm nhẹ |

### Bước 2 — thử luồng thật

1. Nạp vài tài liệu thật (quy chế, hướng dẫn môn học) qua `POST /v1/documents`.
2. `GET /v1/knowledge/search?q=...` xem truy xuất có đúng đoạn không —
   **kiểm tra bước này trước khi đánh giá chất lượng câu trả lời**, vì RAG sai đoạn
   thì model giỏi mấy cũng trả lời sai.
3. `POST /v1/chat` với `"stream": false` cho dễ đọc.
4. Bật `FORUM_BOT_ENABLED=true`, gọi `POST /v1/forum/scan` trên môi trường dev
   (**không chạy trên production khi chưa xem kỹ output** — bot sẽ đăng bình luận thật).

### Bước 3 — còn thiếu

- **Re-rank gợi ý mentor** (`/v1/recommendations`): BE lọc rule-based, AI xếp lại top 20
  kèm lý do cá nhân hóa, cache 24h, AI lỗi thì trả nguyên thứ tự BE.
- **Bộ eval**: 20 câu hỏi thật của sinh viên, chạy qua `DeepSeek-V4-Flash`,
  `gpt-oss-120b`, `GLM-5.2`, chấm mù để chọn model. Chi phí dưới 10.000đ.
  Đây là cách quyết định model bằng số liệu thay vì cảm giác.
- **Trang Forum phía FE** — BE có API đủ rồi nhưng FE chưa có trang, nên bot
  chưa có chỗ để người dùng nhìn thấy.
- **Widget chat FE**: `fu-skillswap-fe/src/components/ChatAssistant.tsx` đã viết sẵn và
  khớp định dạng SSE của service; chỉ cần đặt `VITE_AI_BASE_URL`.
- **Redis cho rate limit** khi chạy nhiều worker (hiện bộ đếm nằm trong tiến trình).
- **Đổi BE sang RS256** rồi bỏ việc chia secret HS256.

### Lưu ý khi chuyển máy

`_archive_worker_ts/` (bản Cloudflare Worker cũ) **nằm trong .gitignore nên không đi theo git**.
Bản đó đã bị thay thế hoàn toàn; nếu muốn giữ thì copy tay.
