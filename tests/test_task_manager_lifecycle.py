import asyncio

from core.drawing.tasks import DrawingTaskManager


def test_cancel_all_cancels_tasks_replaced_under_the_same_id() -> None:
    async def scenario() -> None:
        manager = DrawingTaskManager()
        cancelled = [asyncio.Event(), asyncio.Event()]

        async def worker(index: int) -> None:
            try:
                await asyncio.Future()
            finally:
                cancelled[index].set()

        first = asyncio.create_task(worker(0))
        second = asyncio.create_task(worker(1))
        manager.start("same-id", first)
        manager.start("same-id", second)
        await asyncio.sleep(0)

        await manager.cancel_all()

        assert first.cancelled()
        assert second.cancelled()
        assert all(event.is_set() for event in cancelled)
        assert manager.running_tasks == {}

    asyncio.run(scenario())
