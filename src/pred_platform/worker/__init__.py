"""Run orchestration: launch the engine for a queued run, follow it, and record its tasks (G3).

``service.enqueue_run`` queues a run for an accepted ingestion; ``bucle.Worker`` claims it, runs
``pred-engine run`` as a subprocess, applies its ``progreso.jsonl`` events to ``ejecuciones`` and
``tareas`` while it runs, and settles the run from the engine's final files. The platform never
imports the engine: it reads its run folder (sections 18 and 19 of the data contract).
"""
