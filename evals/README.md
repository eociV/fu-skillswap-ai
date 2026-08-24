# Eval harness

Bộ golden cases chấm chất lượng prompt trước khi deploy — chạy `npm run eval`
(cần `ANTHROPIC_API_KEY` trong env; CI chỉ chạy smoke subset).

- `chat.cases.json` — câu hỏi FAQ → tiêu chí chấm (từ khóa bắt buộc, có/không gọi tool).
- `forum.cases.json` — post mẫu → kỳ vọng `isAnswerableQuestion` đúng.

Quy trình khi sửa prompt:
1. Tạo `src/prompts/<feature>.vN.ts` mới (không sửa đè version cũ).
2. Bổ sung case từ feedback 👎 gần đây (bảng `ai_feedback`).
3. `npm run eval` pass → mở PR.
