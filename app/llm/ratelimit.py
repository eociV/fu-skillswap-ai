import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """Cửa sổ trượt đơn giản cho hạn mức mỗi phút."""

    limit: int
    events: list[tuple[float, int]] = field(default_factory=list)

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self.events and self.events[0][0] < cutoff:
            self.events.pop(0)

    def used(self, now: float) -> int:
        self._prune(now)
        return sum(amount for _, amount in self.events)

    def add(self, now: float, amount: int) -> None:
        self.events.append((now, amount))


class ModelRateLimiter:
    """
    Hạn mức FPT AI Factory: mặc định 50 request/phút và 100.000 token/phút,
    tính RIÊNG cho từng model — nên tách tác vụ ra các model khác nhau là tự
    nhiên nhân được hạn mức.

    Lưu ý: trạng thái nằm trong tiến trình. Khi chạy nhiều worker thì phải
    chuyển sang Redis, nếu không mỗi worker sẽ tưởng mình còn nguyên hạn mức.
    """

    def __init__(self, rpm: int, tpm: int) -> None:
        self._rpm = rpm
        self._tpm = tpm
        self._req: dict[str, _Bucket] = {}
        self._tok: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, model: str, estimated_tokens: int, timeout: float = 20.0) -> bool:
        """Chờ tới lượt. Trả False nếu quá `timeout` giây vẫn chưa có chỗ."""
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                now = time.monotonic()
                req = self._req.setdefault(model, _Bucket(self._rpm))
                tok = self._tok.setdefault(model, _Bucket(self._tpm))
                if req.used(now) < self._rpm and tok.used(now) + estimated_tokens <= self._tpm:
                    req.add(now, 1)
                    tok.add(now, estimated_tokens)
                    return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)
