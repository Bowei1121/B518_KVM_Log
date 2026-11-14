"""Shared completion callback for Core background tasks."""


def supervise_task(task, fatal):
    """Observe exceptions immediately, including tasks retained by their owner."""
    def completed(done):
        if not done.cancelled():
            error = done.exception()
            if error is not None:
                fatal(error)
    task.add_done_callback(completed)
    return task


def supervise_background(task):
    """Attach the process policy only when running inside headless Core."""
    import asyncio
    fatal = getattr(asyncio.get_running_loop(), "core_fatal", None)
    return supervise_task(task, fatal) if fatal is not None else task
