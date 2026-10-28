import asyncio
import threading

from prismix import jobs


def test_urgent_job_pauses_running_one_and_resumes_it_after():
    async def run():
        order = []
        gate = threading.Event()

        def fake(job):
            order.append(("start", job.video_id))
            if job.video_id == "a" and not gate.is_set():
                gate.set()
                while True:  # a long separation: reports until paused
                    job.update("separate", 0.5, "x")
                    threading.Event().wait(0.01)
            job.update("separate", 0.9, "x")
            job.update("done", 1.0, "Ready")
            order.append(("end", job.video_id))

        m = jobs.JobManager()
        m._execute = fake
        m.start()
        a, b, c = (m.submit(v, "m") for v in "abc")
        while not gate.is_set():
            await asyncio.sleep(0.01)
        m.submit("c", "m", urgent=True)
        while not all(j.finished for j in (a, b, c)):
            await asyncio.sleep(0.01)
        await m.stop()
        return [o for o in order if o[0] == "end"], a.error

    ends, err = asyncio.run(run())
    assert err is None
    assert ends == [("end", "c"), ("end", "a"), ("end", "b")]



def test_cancel_stops_a_running_job_and_drops_a_waiting_one():
    async def run():
        started = threading.Event()

        def fake(job):
            started.set()
            while True:
                job.update("separate", 0.5, "x")
                threading.Event().wait(0.01)

        m = jobs.JobManager()
        m._execute = fake
        m.start()
        a, b = m.submit("a", "m"), m.submit("b", "m")
        while not started.is_set():
            await asyncio.sleep(0.01)
        assert m.cancel(b.id) and b.error == "Cancelled" and b not in m._pending
        assert m.cancel(a.id)
        while not a.finished:
            await asyncio.sleep(0.01)
        await m.stop()
        return a.error, m.cancel(a.id), m._running

    err, again, running = asyncio.run(run())
    assert err == "Cancelled" and again is False and running is None
