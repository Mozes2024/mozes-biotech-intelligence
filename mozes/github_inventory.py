"""Complete bounded Actions inventories shared by read-only publication readers."""
import time


def require(condition, category):
    if not condition:
        raise RuntimeError(category)


def inventory(path, key, *, api, collection, bound=None):
    from concurrent.futures import ThreadPoolExecutor
    deadline = time.monotonic() + 90
    first = api(f'{path}&per_page=100&page=1')
    rows = collection(first, key, partial=True)
    require(len(rows) <= 100, 'incomplete_inventory')
    expected = first['total_count']
    pages = max(1, (expected + 99) // 100)
    require(bound is None or pages <= bound, 'producer_inventory_bound')
    require(time.monotonic() < deadline, 'inventory_time_budget')
    def fetch(page):
        require(time.monotonic() < deadline, 'inventory_time_budget')
        result = api(f'{path}&per_page=100&page={page}')
        batch = collection(result, key, partial=True)
        require(result['total_count'] == expected, 'inventory_changed')
        return batch
    with ThreadPoolExecutor(max_workers=4) as pool:
        for batch in pool.map(fetch, range(2, pages + 1)):
            require(time.monotonic() < deadline, 'inventory_time_budget')
            require(len(batch) <= 100, 'incomplete_inventory')
            rows.extend(batch)
    require(time.monotonic() < deadline, 'inventory_time_budget')
    require(len(rows) == expected and all(type(r.get('id')) is int and r['id'] > 0 for r in rows),
            'incomplete_inventory')
    require(len({r['id'] for r in rows}) == len(rows), 'duplicate_inventory')
    return rows

