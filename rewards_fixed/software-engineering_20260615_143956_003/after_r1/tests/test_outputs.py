import subprocess
import sys
import weakref
import gc


def _import_ringbuffer():
    """Import ringbuffer, rebuilding if needed."""
    try:
        import ringbuffer
        return ringbuffer
    except ImportError:
        # Build it first
        result = subprocess.run(
            [sys.executable, "setup.py", "build_ext", "--inplace"],
            cwd="/app",
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Build failed: {result.stderr}")
        import ringbuffer
        return ringbuffer


def test_module_is_importable():
    """Verify the ringbuffer module can be imported."""
    rb = _import_ringbuffer()
    assert hasattr(rb, "RingBuffer"), "RingBuffer class not found in module"


def test_basic_operations():
    """Verify basic push/pop/peek/size operations work."""
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(5)

    assert rb.is_empty() is True
    assert rb.is_full() is False
    assert rb.size() == 0

    rb.push("a")
    rb.push("b")
    rb.push("c")

    assert rb.is_empty() is False
    assert rb.size() == 3
    assert rb.peek() == "a"

    assert rb.pop() == "a"
    assert rb.pop() == "b"
    assert rb.pop() == "c"
    assert rb.is_empty() is True


def test_overwrite_on_full_buffer():
    """When the buffer is full, new pushes should overwrite the oldest items."""
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(3)

    for i in range(5):
        rb.push(i)

    # After 5 pushes, oldest items (0, 1) should be overwritten
    assert rb.size() == 3
    assert rb.pop() == 2
    assert rb.pop() == 3
    assert rb.pop() == 4


def test_clear():
    """Verify clear() empties the buffer and frees items."""
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def do_clear():
        rb = rb_mod.RingBuffer(5)
        refs = []
        for i in range(5):
            obj = Trackable()
            refs.append(weakref.ref(obj))
            rb.push(obj)
        rb.clear()
        return refs, rb

    refs, rb = do_clear()

    assert rb.is_empty() is True
    assert rb.size() == 0

    gc.collect()

    # All items should be freed after clear
    leaked = sum(1 for r in refs if r() is not None)
    assert leaked == 0, (
        f"clear() did not free all items: {leaked} out of 5 still alive"
    )


def test_to_list():
    """Verify to_list() returns correct items in order."""
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(5)

    for i in range(3):
        rb.push(i)

    result = rb.to_list()
    assert result == [0, 1, 2], f"to_list() returned {result}, expected [0, 1, 2]"


def test_resize_grow():
    """Verify resize() can grow the buffer without losing items."""
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(3)

    for i in range(3):
        rb.push(i)

    rb.resize(6)

    assert rb.capacity() == 6
    assert rb.size() == 3
    assert rb.to_list() == [0, 1, 2], (
        f"After growing, items should be preserved. Got {rb.to_list()}"
    )


def test_resize_shrink_keeps_newest():
    """
    Verify resize() shrinks correctly by keeping the newest items
    and dropping the oldest.
    """
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(10)

    for i in range(10):
        rb.push(i)

    rb.resize(3)

    assert rb.capacity() == 3
    assert rb.size() == 3
    # Should keep the newest 3 items: 7, 8, 9
    assert rb.to_list() == [7, 8, 9], (
        f"After shrinking to 3, should keep newest items [7, 8, 9]. "
        f"Got {rb.to_list()}"
    )


def test_resize_shrink_to_same_size():
    """Verify resize() to the same size preserves all items."""
    rb_mod = _import_ringbuffer()
    rb = rb_mod.RingBuffer(5)

    for i in range(5):
        rb.push(i)

    rb.resize(5)

    assert rb.capacity() == 5
    assert rb.size() == 5
    assert rb.to_list() == [0, 1, 2, 3, 4]


def test_no_memory_leak_on_overwrite():
    """
    When the buffer overwrites old items, those items must be properly
    released (reference count decremented). If the bug is present,
    overwritten objects are never freed.
    """
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def push_many():
        rb = rb_mod.RingBuffer(4)
        refs = []
        for i in range(50):
            obj = Trackable()
            refs.append(weakref.ref(obj))
            rb.push(obj)
        return refs, rb

    refs, rb = push_many()

    gc.collect()

    # Check: the first 46 objects should have been freed
    leaked = 0
    for i in range(46):
        if refs[i]() is not None:
            leaked += 1

    assert leaked == 0, (
        f"Memory leak detected: {leaked} out of 46 overwritten objects "
        f"were not freed. The C extension is leaking references."
    )

    # The last 4 objects should still be alive in the buffer
    for i in range(46, 50):
        assert refs[i]() is not None, (
            f"Object {i} was incorrectly freed; it should still be in the buffer."
        )


def test_no_memory_leak_on_extend():
    """
    extend() should also properly release overwritten objects.
    """
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def do_extend():
        rb = rb_mod.RingBuffer(4)
        refs = []
        objects = []
        for i in range(50):
            obj = Trackable()
            refs.append(weakref.ref(obj))
            objects.append(obj)
        rb.extend(objects)
        return refs, rb

    refs, rb = do_extend()

    gc.collect()

    # First 46 objects should have been freed
    leaked = 0
    for i in range(46):
        if refs[i]() is not None:
            leaked += 1

    assert leaked == 0, (
        f"Memory leak in extend(): {leaked} out of 46 overwritten objects "
        f"were not freed."
    )


def test_no_memory_leak_on_resize_shrink():
    """
    When resize() shrinks the buffer, items that no longer fit must
    be properly released.
    """
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def do_resize():
        rb = rb_mod.RingBuffer(10)
        refs = []
        for i in range(10):
            obj = Trackable()
            refs.append(weakref.ref(obj))
            rb.push(obj)
        # Shrink to 3: oldest 7 items should be dropped
        rb.resize(3)
        return refs, rb

    refs, rb = do_resize()

    gc.collect()

    # First 7 items were dropped by resize, should be freed
    leaked = 0
    for i in range(7):
        if refs[i]() is not None:
            leaked += 1

    assert leaked == 0, (
        f"Memory leak in resize(): {leaked} out of 7 dropped objects "
        f"were not freed."
    )

    # Last 3 items should still be in the buffer
    for i in range(7, 10):
        assert refs[i]() is not None, (
            f"Object {i} was incorrectly freed by resize(); it should be kept."
        )


def test_overwritten_objects_freed_after_buffer_destroyed():
    """
    Even after the ring buffer is destroyed, all objects that were
    in it at the time of destruction must be properly freed.
    """
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def create_and_push():
        rb = rb_mod.RingBuffer(3)
        refs = []
        for i in range(10):
            obj = Trackable()
            refs.append(weakref.ref(obj))
            rb.push(obj)
        return refs, rb

    refs, rb = create_and_push()

    # Delete the buffer
    del rb
    gc.collect()

    # Objects 0-6 were overwritten during the loop.
    # Objects 7-9 were in the buffer when it was destroyed.
    # ALL should now be freed.
    leaked = 0
    for i in range(10):
        if refs[i]() is not None:
            leaked += 1

    assert leaked == 0, (
        f"Memory leak detected after buffer destruction: {leaked} out of 10 "
        f"objects were not freed."
    )


def test_many_push_pop_cycles():
    """
    Stress test: many push/pop cycles with a small buffer should not
    leak memory over time.
    """
    rb_mod = _import_ringbuffer()

    class Trackable:
        pass

    def run_cycles():
        rb = rb_mod.RingBuffer(8)
        refs = []
        for cycle in range(20):
            for i in range(12):
                obj = Trackable()
                refs.append(weakref.ref(obj))
                rb.push(obj)
            for i in range(6):
                rb.pop()
        return refs, rb

    refs, rb = run_cycles()

    gc.collect()

    # Count how many tracked objects are still alive
    alive_count = sum(1 for r in refs if r() is not None)

    # With the bug, nearly all objects are leaked.
    # With fix, at most ~8 (buffer capacity) + some pending should be alive.
    assert alive_count <= 30, (
        f"Too many objects still alive after stress test: {alive_count}. "
        f"Expected at most ~30, got {alive_count}. Memory leak likely present."
    )
