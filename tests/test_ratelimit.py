import asyncio


from app.llm.ratelimit import ModelRateLimiter




def test_request_limit_blocks_after_quota():
    limiter = ModelRateLimiter(rpm=3, tpm=1_000_000)

    async def run():
        for _ in range(3):
            assert await limiter.acquire("m", 10)
        # Request thứ 4 phải chờ; timeout ngắn -> trả False thay vì treo.
        return await limiter.acquire("m", 10, timeout=0.6)

    assert asyncio.run(run()) is False


def test_token_limit_blocks_independently_of_request_count():
    limiter = ModelRateLimiter(rpm=50, tpm=1000)

    async def run():
        assert await limiter.acquire("m", 900)
        return await limiter.acquire("m", 500, timeout=0.6)

    assert asyncio.run(run()) is False


def test_limits_are_per_model():
    """Tách tác vụ ra model khác nhau là cách nhân hạn mức — phải đúng như vậy."""
    limiter = ModelRateLimiter(rpm=1, tpm=1_000_000)

    async def run():
        assert await limiter.acquire("gpt-oss-20b", 10)
        return await limiter.acquire("DeepSeek-V4-Flash", 10, timeout=0.5)

    assert asyncio.run(run()) is True
