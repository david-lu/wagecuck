"""Bounded workers: never create one browser task per result in a large search."""

import asyncio


async def map_bounded(items, function, workers, on_result=None):
    iterator = iter(enumerate(items))
    results = [None] * len(items)

    async def worker():
        for index, item in iterator:
            result = await function(item)
            results[index] = result
            if on_result:
                on_result(index, result)

    tasks = [asyncio.create_task(worker()) for _ in range(min(workers, len(items)))]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    return results
